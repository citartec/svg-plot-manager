#!/usr/bin/env python3
"""SVG Plot Manager: non-destructive batch planning for long pen-plotter jobs.

Requires: lxml (Ubuntu: sudo apt install python3-lxml)

Keeps original page, viewBox, transforms, styles, and path order. It never
implicitly scales, centres, joins, shortens, or reorders drawing paths.
"""
from __future__ import annotations

import argparse
import copy
import csv
import colorsys
import json
import math
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

try:
    from lxml import etree
except ImportError:
    sys.exit("Missing lxml. On Ubuntu: sudo apt install python3-lxml")

SVG = "http://www.w3.org/2000/svg"
SVG_TAG = "{%s}" % SVG
NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
TOKENS = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|" + NUM)
TRANSFORMS = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
NUMBERS = re.compile(NUM)
NONART = {"defs", "symbol", "clipPath", "mask", "marker", "pattern", "filter", "linearGradient", "radialGradient"}
OTHER_ART = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "image", "use", "foreignObject"}


def name(tag):
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def matmul(a, b):
    """Affine matrices (a,b,c,d,e,f), with result applying b then a."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0*b0+a2*b1, a1*b0+a3*b1,
            a0*b2+a2*b3, a1*b2+a3*b3,
            a0*b4+a2*b5+a4, a1*b4+a3*b5+a5)


IDENTITY = (1., 0., 0., 1., 0., 0.)


def point(m, p):
    x, y = p
    return (m[0]*x+m[2]*y+m[4], m[1]*x+m[3]*y+m[5])


def dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


def transform(value):
    if not value:
        return IDENTITY
    out = IDENTITY
    matches = list(TRANSFORMS.finditer(value))
    if not matches or TRANSFORMS.sub("", value).strip(" ,\t\n"):
        raise ValueError(f"Unsupported transform: {value}")
    for match in matches:
        op = match.group(1)
        nums = [float(s) for s in NUMBERS.findall(match.group(2))]
        if op == "matrix" and len(nums) == 6:
            m = tuple(nums)
        elif op == "translate" and len(nums) in (1, 2):
            m = (1., 0., 0., 1., nums[0], nums[1] if len(nums) == 2 else 0.)
        elif op == "scale" and len(nums) in (1, 2):
            m = (nums[0], 0., 0., nums[1] if len(nums) == 2 else nums[0], 0., 0.)
        elif op == "rotate" and len(nums) in (1, 3):
            t = math.radians(nums[0])
            r = (math.cos(t), math.sin(t), -math.sin(t), math.cos(t), 0., 0.)
            if len(nums) == 3:
                x, y = nums[1:]
                r = matmul((1,0,0,1,x,y), matmul(r, (1,0,0,1,-x,-y)))
            m = r
        elif op == "skewX" and len(nums) == 1:
            m = (1, 0, math.tan(math.radians(nums[0])), 1, 0, 0)
        elif op == "skewY" and len(nums) == 1:
            m = (1, math.tan(math.radians(nums[0])), 0, 1, 0, 0)
        else:
            raise ValueError(f"Invalid or unsupported transform: {match.group(0)}")
        out = matmul(out, m)
    return out


def to_mm(value):
    if value is None or "%" in value:
        raise ValueError(f"SVG page size must use absolute units: {value!r}")
    match = re.fullmatch(r"\s*("+NUM+r")\s*(mm|cm|in|pt|pc|px|Q)?\s*", value)
    if not match:
        raise ValueError(f"Cannot read SVG page measurement: {value}")
    v = float(match.group(1))
    unit = match.group(2) or "px"
    return v * {"mm": 1, "cm": 10, "in": 25.4, "pt": 25.4/72,
                "pc": 25.4/6, "px": 25.4/96, "Q": 0.25}[unit]


def page_info(root):
    w, h = to_mm(root.get("width")), to_mm(root.get("height"))
    if w <= 0 or h <= 0:
        raise ValueError("SVG page dimensions must be positive")
    vb = root.get("viewBox")
    if vb:
        nums = [float(n) for n in NUMBERS.findall(vb)]
        if len(nums) != 4 or nums[2] <= 0 or nums[3] <= 0:
            raise ValueError("Invalid viewBox")
        x0, y0, vw, vh = nums
        preserve = root.get("preserveAspectRatio", "xMidYMid meet").strip().split()
        if "none" in preserve:
            sx, sy = w/vw, h/vh
            tx, ty = -x0*sx, -y0*sy
        else:
            scale = max(w/vw, h/vh) if "slice" in preserve else min(w/vw, h/vh)
            align = next((x for x in preserve if x.startswith("x") and "Y" in x), "xMidYMid")
            extra_x, extra_y = w-vw*scale, h-vh*scale
            ox = 0 if "xMin" in align else (extra_x if "xMax" in align else extra_x/2)
            oy = 0 if "YMin" in align else (extra_y if "YMax" in align else extra_y/2)
            sx = sy = scale
            tx, ty = ox-x0*scale, oy-y0*scale
        base = (sx, 0., 0., sy, tx, ty)
    else:
        base = (25.4/96, 0., 0., 25.4/96, 0., 0.)
    return w, h, base


def arc_points(start, end, rx, ry, angle_deg, large_arc, sweep, steps=32):
    """SVG elliptical arc, sampled for measurement only; original d is unchanged."""
    if rx == 0 or ry == 0 or start == end:
        return [end]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(angle_deg % 360)
    co, si = math.cos(phi), math.sin(phi)
    dx, dy = (start[0]-end[0])/2, (start[1]-end[1])/2
    xp, yp = co*dx+si*dy, -si*dx+co*dy
    lam = xp*xp/(rx*rx) + yp*yp/(ry*ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    numerator = max(0., rx*rx*ry*ry-rx*rx*yp*yp-ry*ry*xp*xp)
    denom = rx*rx*yp*yp+ry*ry*xp*xp
    k = math.sqrt(numerator/denom) if denom > 0 else 0.
    if bool(large_arc) == bool(sweep):
        k = -k
    cxp, cyp = k*rx*yp/ry, -k*ry*xp/rx
    cx = co*cxp-si*cyp+(start[0]+end[0])/2
    cy = si*cxp+co*cyp+(start[1]+end[1])/2
    def angle(u, v):
        return math.atan2(u[0]*v[1]-u[1]*v[0], u[0]*v[0]+u[1]*v[1])
    u = ((xp-cxp)/rx, (yp-cyp)/ry)
    v = ((-xp-cxp)/rx, (-yp-cyp)/ry)
    theta = math.atan2(u[1], u[0])
    delta = angle(u, v)
    if not sweep and delta > 0:
        delta -= 2*math.pi
    if sweep and delta < 0:
        delta += 2*math.pi
    steps = max(8, math.ceil(abs(delta)/(2*math.pi)*steps))
    return [(cx+rx*co*math.cos(theta+delta*i/steps)-ry*si*math.sin(theta+delta*i/steps),
             cy+rx*si*math.cos(theta+delta*i/steps)+ry*co*math.sin(theta+delta*i/steps))
            for i in range(1, steps+1)]


@dataclass
class Geometry:
    draw_mm: float = 0.
    internal_travel_mm: float = 0.
    vertex_count: int = 0
    pen_contacts: int = 0
    first: tuple[float, float] | None = None
    last: tuple[float, float] | None = None
    min_x: float = math.inf
    min_y: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf

    def touch(self, p):
        self.min_x = min(self.min_x, p[0]); self.min_y = min(self.min_y, p[1])
        self.max_x = max(self.max_x, p[0]); self.max_y = max(self.max_y, p[1])


def measure_path(d, matrix):
    """Measure arbitrary standard SVG paths in millimetres without modifying them."""
    words = TOKENS.findall(d or "")
    if not words:
        raise ValueError("Empty SVG path 'd' attribute")
    metrics = Geometry()
    i = 0
    cmd = None
    cursor = (0., 0.)
    substart = cursor
    previous_control = None
    previous_command = None
    has_subpath = False
    def take(n):
        nonlocal i
        if i+n > len(words) or any(re.fullmatch(r"[A-Za-z]", w) for w in words[i:i+n]):
            raise ValueError("Malformed path: missing parameters near token " + str(i))
        result = [float(w) for w in words[i:i+n]]
        i += n
        return result
    def draw_points(pts):
        nonlocal cursor
        last_point = point(matrix, cursor)
        for p in pts:
            physical = point(matrix, p)
            metrics.draw_mm += dist(last_point, physical)
            metrics.vertex_count += 1
            metrics.touch(physical)
            last_point = physical
        if pts:
            cursor = pts[-1]
            metrics.last = last_point
    def curve(samples):
        draw_points(samples)
    while i < len(words):
        if re.fullmatch(r"[A-Za-z]", words[i]):
            cmd = words[i]
            i += 1
        if cmd is None:
            raise ValueError(f"Malformed path at token {i}")
        typ = cmd.upper()
        relative = cmd.islower()
        old = cursor
        if typ == "Z":
            if has_subpath:
                draw_points([substart])
            cursor = substart
            previous_control = None
            previous_command = "Z"
            cmd = None
            continue
        if typ == "M":
            x, y = take(2)
            target = (x+old[0], y+old[1]) if relative else (x, y)
            pp = point(matrix, target)
            if has_subpath and metrics.last is not None:
                metrics.internal_travel_mm += dist(metrics.last, pp)
            else:
                metrics.first = pp
            cursor = substart = target
            metrics.last = pp
            metrics.touch(pp)
            metrics.pen_contacts += 1
            has_subpath = True
            previous_control = None
            previous_command = "M"
            cmd = "l" if relative else "L"
            continue
        if not has_subpath:
            raise ValueError("Path must begin with an M command")
        if typ == "L":
            x, y = take(2)
            target = (x+old[0], y+old[1]) if relative else (x, y)
            draw_points([target]); previous_control = None
        elif typ == "H":
            x, = take(1)
            draw_points([(x+old[0] if relative else x, old[1])]); previous_control = None
        elif typ == "V":
            y, = take(1)
            draw_points([(old[0], y+old[1] if relative else y)]); previous_control = None
        elif typ in ("C", "S", "Q", "T"):
            k = {"C": 6, "S": 4, "Q": 4, "T": 2}[typ]
            nums = take(k)
            pts = [(nums[j], nums[j+1]) for j in range(0, k, 2)]
            if relative:
                pts = [(p[0]+old[0], p[1]+old[1]) for p in pts]
            target = pts[-1]
            if typ == "C":
                p1, p2 = pts[0], pts[1]
                cubic = True
            elif typ == "S":
                p1 = ((2*old[0]-previous_control[0], 2*old[1]-previous_control[1])
                      if previous_command in ("C", "S") and previous_control else old)
                p2 = pts[0]
                cubic = True
            elif typ == "Q":
                p1 = pts[0]
                cubic = False
            else:
                p1 = ((2*old[0]-previous_control[0], 2*old[1]-previous_control[1])
                      if previous_command in ("Q", "T") and previous_control else old)
                cubic = False
            samples = []
            for n in range(1, 25):
                t = n/24
                u = 1-t
                if cubic:
                    samples.append((u**3*old[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*target[0],
                                    u**3*old[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*target[1]))
                else:
                    samples.append((u*u*old[0]+2*u*t*p1[0]+t*t*target[0],
                                    u*u*old[1]+2*u*t*p1[1]+t*t*target[1]))
            curve(samples)
            previous_control = p2 if cubic else p1
        elif typ == "A":
            rx, ry, a, large, sweep, x, y = take(7)
            if int(large) not in (0, 1) or int(sweep) not in (0, 1):
                raise ValueError("Invalid arc flags")
            target = (x+old[0], y+old[1]) if relative else (x, y)
            draw_points(arc_points(old, target, rx, ry, a, int(large), int(sweep)))
            previous_control = None
        else:
            raise ValueError(f"Unsupported SVG path command {cmd}")
        previous_command = typ
    return metrics


@dataclass
class Stroke:
    element: object = field(repr=False)
    number: int = 0
    geometry: Geometry = field(default_factory=Geometry)
    label: str = ""


@dataclass
class Batch:
    strokes: list[Stroke] = field(default_factory=list)
    draw_mm: float = 0.
    ink_mm: float = 0.
    travel_mm: float = 0.
    lifts: int = 0
    vertices: int = 0
    estimated_seconds: float = 0.
    first_path: int = 0
    last_path: int = 0
    start: tuple | None = None
    finish: tuple | None = None


def find_strokes(root, base):
    strokes = []
    unsupported = []
    def walk(el, matrix, excluded=False):
        if not isinstance(el.tag, str):
            return
        tag = name(el.tag)
        if tag == "svg" and el is not root:
            raise ValueError("Nested <svg> viewport detected; flatten it in Inkscape first")
        if excluded or tag in NONART:
            return
        if el.get("style", "").find("transform:") >= 0:
            raise ValueError("CSS style transform detected; convert it to SVG transform first")
        matrix = matmul(matrix, transform(el.get("transform")))
        if tag == "path":
            n = len(strokes)+1
            try:
                geom = measure_path(el.get("d"), matrix)
            except ValueError as err:
                raise ValueError(f"Path #{n} ({el.get('id','no id')}): {err}") from err
            strokes.append(Stroke(el, n, geom, el.get("id", "")))
        elif tag in OTHER_ART:
            unsupported.append(tag)
        for child in el:
            walk(child, matrix)
    walk(root, base)
    if unsupported:
        raise ValueError("Non-path drawing elements present ("+", ".join(sorted(set(unsupported)))+"). "
                         "Convert objects to paths in Inkscape; original SVG was not changed.")
    if not strokes:
        raise ValueError("No drawable SVG paths found")
    return strokes


def estimated_cost(g, opts, travel_mm=0.):
    return (g.draw_mm/opts.draw_speed +
            (g.internal_travel_mm+travel_mm)/opts.travel_speed +
            g.pen_contacts*opts.lift_seconds +
            g.vertex_count*opts.vertex_seconds)


def ink_usage(g, opts):
    return g.draw_mm + g.pen_contacts*opts.ink_mm_per_contact + g.vertex_count*opts.ink_mm_per_vertex


def add(batch, stroke, opts, time_scale=1.):
    g = stroke.geometry
    distance_to_first = dist(batch.finish, g.first) if batch.finish and g.first else 0.
    if not batch.strokes:
        batch.first_path = stroke.number
        batch.start = g.first
    batch.strokes.append(stroke)
    batch.last_path = stroke.number
    batch.draw_mm += g.draw_mm
    batch.ink_mm += ink_usage(g, opts)
    batch.travel_mm += distance_to_first + g.internal_travel_mm
    batch.lifts += g.pen_contacts
    batch.vertices += g.vertex_count
    batch.estimated_seconds += estimated_cost(g, opts, distance_to_first)*time_scale
    batch.finish = g.last


def limits_exceeded(b, opts):
    return [title for title, is_over in (
        ("time", opts.max_minutes > 0 and b.estimated_seconds > opts.max_minutes*60+1e-7),
        ("ink", opts.max_ink_m > 0 and b.ink_mm > opts.max_ink_m*1000+1e-7),
        ("drawing distance", opts.max_draw_m > 0 and b.draw_mm > opts.max_draw_m*1000+1e-7),
        ("pen contacts", opts.max_contacts > 0 and b.lifts > opts.max_contacts),
        ("vertices", opts.max_vertices > 0 and b.vertices > opts.max_vertices),
        ("paths", opts.max_paths > 0 and len(b.strokes) > opts.max_paths)
    ) if is_over]


def partition(selected, opts, time_scale):
    batches = []
    current = Batch()
    oversized = []
    for stroke in selected:
        candidate = copy.copy(current)
        candidate.strokes = current.strokes.copy()
        add(candidate, stroke, opts, time_scale)
        if current.strokes and limits_exceeded(candidate, opts):
            batches.append(current)
            current = Batch()
        add(current, stroke, opts, time_scale)
        if len(current.strokes) == 1 and limits_exceeded(current, opts):
            oversized.append(stroke.number)
    if current.strokes:
        batches.append(current)
    return batches, sorted(set(oversized))


def partition_equal_parts(selected, opts, time_scale, count):
    """Find exactly count contiguous, approximately equal-time pieces.

    Cut only between source paths. Optional pen-down caps may subdivide these
    base pieces, so the total can exceed count. Never silently drop a path.
    """
    from bisect import bisect_left
    if len(selected) < count:
        raise ValueError(f"Cannot make {count} nonempty parts from {len(selected)} SVG paths")
    accumulated = [0.]
    previous = None
    for stroke in selected:
        travel = (dist(previous.geometry.last, stroke.geometry.first)
                  if previous and previous.geometry.last is not None and stroke.geometry.first is not None else 0.)
        accumulated.append(accumulated[-1] + estimated_cost(stroke.geometry, opts, travel)*time_scale)
        previous = stroke
    total = accumulated[-1]
    start = 0
    divisions = []
    for part in range(1, count):
        goal = total*part/count
        lo = start + 1
        hi = len(selected) - (count - part)
        k = max(lo, min(hi, bisect_left(accumulated, goal, lo, hi+1)))
        if k > lo and abs(accumulated[k-1]-goal) < abs(accumulated[k]-goal):
            k -= 1
        divisions.append(selected[start:k])
        start = k
    divisions.append(selected[start:])
    batches = []
    oversized = []
    for group in divisions:
        sub_batches, sub_oversized = partition(group, opts, time_scale)
        batches.extend(sub_batches)
        oversized.extend(sub_oversized)
    return batches, sorted(set(oversized))


def preserve_subset(root, selected_elements, all_artwork_elements):
    clone = copy.deepcopy(root)
    original_paths = list(root.iter(SVG_TAG+"path"))
    cloned_paths = list(clone.iter(SVG_TAG+"path"))
    if len(original_paths) != len(cloned_paths):
        raise AssertionError("Unexpected path count while copying SVG")
    for original, copied in zip(original_paths, cloned_paths):
        if original in all_artwork_elements and original not in selected_elements:
            parent = copied.getparent()
            parent.remove(copied)
    return clone


def create_overview(file_path, root, batches, artwork_elements, doctype):
    """Colour-coded screen preview ONLY, not a plotting job."""
    by_element = {}
    for i, batch in enumerate(batches):
        # Session shading is deliberately neutral, matching the web UI.
        shades = ('#252525', '#484848', '#666666', '#888888',
                  '#a1a1a1', '#777777', '#575757', '#383838')
        colour = shades[i % len(shades)]
        for stroke in batch.strokes:
            by_element[stroke.element] = colour
    clone = copy.deepcopy(root)
    for original, copied in zip(root.iter(SVG_TAG+"path"), clone.iter(SVG_TAG+"path")):
        if original in artwork_elements:
            colour = by_element.get(original)
            if colour is None:
                copied.getparent().remove(copied)
            else:
                style = copied.get("style", "").rstrip("; ")
                copied.set("style", style+f";stroke:{colour};fill:none")
                copied.set("stroke", colour)
                copied.set("fill", "none")
    write_svg(file_path, clone, doctype)


def write_svg(path, root, doctype):
    tree = etree.ElementTree(root)
    tree.write(str(path), encoding="UTF-8", xml_declaration=True,
               pretty_print=False, doctype=doctype or None)


def pen_test_svg(file_path):
    """Small scrap-paper test swatch. Never plot on your main artwork."""
    root = etree.Element(SVG_TAG+"svg", nsmap={None: SVG})
    root.set("width", "20mm"); root.set("height", "20mm")
    root.set("viewBox", "0 0 20 20")
    el = etree.SubElement(root, SVG_TAG+"path")
    el.set("d", "M2 5 L18 5 M2 8 L18 8 M2 11 L18 11 M2 14 L18 14 M2 17 L18 17")
    el.set("stroke", "black"); el.set("stroke-width", "0.2"); el.set("fill", "none")
    write_svg(file_path, root, "")


def registration_svg(file_path, w, h, margin, size):
    if margin <= size or 2*margin >= min(w, h):
        raise ValueError("Registration margin must be larger than cross size and fit on the page")
    root = etree.Element(SVG_TAG+"svg", nsmap={None: SVG})
    root.set("width", f"{w:.6f}mm"); root.set("height", f"{h:.6f}mm")
    root.set("viewBox", f"0 0 {w:.6f} {h:.6f}")
    group = etree.SubElement(root, SVG_TAG+"g")
    group.set("stroke", "black"); group.set("fill", "none"); group.set("stroke-width", "0.10")
    for x, y in ((margin, margin), (w-margin, margin), (margin, h-margin)):
        e = etree.SubElement(group, SVG_TAG+"path")
        e.set("d", f"M{x-size:.4f} {y:.4f} L{x+size:.4f} {y:.4f} M{x:.4f} {y-size:.4f} L{x:.4f} {y+size:.4f}")
    write_svg(file_path, root, "")


def make_report(info, destination, opts, source):
    rows = info["batches"]
    with (destination/"manifest.csv").open("w", newline="", encoding="utf-8") as f:
        cols = ["job", "filename", "first_path", "last_path", "paths", "drawing_m", "pen_up_m",
                "ink_equivalent_m", "pen_contacts", "vertices", "estimated_minutes", "start_x_mm",
                "start_y_mm", "end_x_mm", "end_y_mm"]
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows({k: row[k] for k in cols} for row in rows)
    (destination/"manifest.json").write_text(json.dumps(info, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    body = [f"# Plot job sheet: {source.name}", "",
            f"Original: {info['source_paths']} paths; selected: {info['selected_paths']} paths",
            f"Artwork page: {info['page_mm']['width']} × {info['page_mm']['height']} mm",
            f"Jobs: {len(rows)}; total estimated time: {info['totals']['estimated_hours']} h",
            f"Time model: {'calibrated to supplied historical hours' if info['time_calibrated'] else 'uncalibrated nominal speeds'}",
            f"Drawn length: {info['totals']['drawing_m']} m; ink allowance used: {info['totals']['ink_equivalent_m']} equivalent m",
            "", "## Before each job", "",
            "- Keep the paper firmly fixed. Use the **same document origin, scale, orientation, and machine coordinates** for every file.",
            "- Do NOT use an automatic 'fit', 'centre', or 'optimise order' option if it changes placement or drawing sequence.",
            "- Check that the pen has ink; refill or change pens at each scheduled boundary.",
            "- Use **pen_test.svg on separate scrap paper** after changing pens. Never plot that file on the unfinished artwork.",
            "- Open **PREVIEW_DO_NOT_PLOT.svg** on screen to see sections in different colours; do NOT send it to the plotter.",
            "- Physically RE-HOME against a repeatable reference before continuing if drift has accumulated. Commanding (0,0) alone does NOT repair missed steps.",
            "- Test pen-up travel to a known reference point before lowering the pen, if your controller supports that.",
            "- Tick each completed job. For recovery, use --from-path with the next unfinished path number.",
            "", "## Batch checklist", ""]
    for r in rows:
        body.append(f"- [ ] **{r['filename']}**: paths {r['first_path']}–{r['last_path']} ({r['paths']}), "
                    f"draw {r['drawing_m']} m, ink-equivalent {r['ink_equivalent_m']} m, "
                    f"~{r['estimated_minutes']} min; {r['pen_contacts']} contacts")
    body += ["", "## Important limits", "",
             "- Running-time estimates are approximations: acceleration, tool-specific delays, and servo motion can dominate stippling.",
             "- 'Ink-equivalent metres' are a **user-set planning allowance**, not a measured pen capacity. Test your own pens and paper.",
             "- Paths are never cut mid-stroke. Any path longer than an imposed limit is placed in its own job and flagged.",
             "- Jobs preserve original path data and transformations, but your plotter software must honour SVG group/path order.",
             "- Jobs do not automatically move, home, or pause your physical plotter.",
             "- If enabled, **registration_marks.svg** is a SEPARATE file. Plot marks once in a clear paper margin; use them to check alignment before later jobs.",
             "- If your home position is unreliable or paper shifts, splitting files by itself does not restore alignment.",
             "", "## Recovery example", "",
             "```bash", f"python3 svg_plot_manager.py '{source.name}' --from-path 701 --outdir recovery_jobs", "```", ""]
    (destination/"RUN_SHEET.md").write_text("\n".join(body), encoding="utf-8")


def args_parser():
    p = argparse.ArgumentParser(description="Split SVGs into safe plotting sessions with ink, time and contact limits.",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", type=Path, help="Original SVG (never modified)")
    p.add_argument("--outdir", type=Path, help="Destination folder (created if absent)")
    p.add_argument("--file-prefix", default="job_", help="Safe prefix for session SVG filenames")
    p.add_argument("--max-minutes", type=float, default=60, help="Max estimated minutes per job; 0 disables")
    p.add_argument("--equal-parts", type=int, choices=(0, 2, 4, 8), default=0, help="Split into equal-workload parts (2, 4, or 8); additional caps may create more jobs")
    p.add_argument("--max-ink-m", type=float, default=8, help="Max estimated ink-equivalent metres per pen/job; 0 disables")
    p.add_argument("--max-draw-m", type=float, default=0, help="Max actual stroke metres per job; 0 disables")
    p.add_argument("--max-contacts", type=int, default=300, help="Max pen-down contacts per job; 0 disables")
    p.add_argument("--max-vertices", type=int, default=0, help="Max line/curve sampled steps per job; 0 disables")
    p.add_argument("--max-paths", type=int, default=0, help="Max paths per job; 0 disables")
    p.add_argument("--draw-speed", type=float, default=20, help="Estimated pen-down speed, mm/second")
    p.add_argument("--travel-speed", type=float, default=80, help="Estimated pen-up speed, mm/second")
    p.add_argument("--lift-seconds", type=float, default=0.6, help="Seconds per pen contact, inclusive of pen movement")
    p.add_argument("--vertex-seconds", type=float, default=0, help="Extra seconds per sampled corner/segment for acceleration")
    p.add_argument("--observed-hours", type=float, default=0,
                   help="Calibrate estimated total to this many hours for the *entire source* SVG")
    p.add_argument("--ink-mm-per-contact", type=float, default=0.5,
                   help="Equivalent ink length added per pen contact, mm")
    p.add_argument("--ink-mm-per-vertex", type=float, default=0,
                   help="Optional extra equivalent ink length per segment/corner, mm")
    p.add_argument("--from-path", type=int, default=1, help="Global 1-based source path number at which to resume")
    p.add_argument("--to-path", type=int, default=0, help="Global inclusive last source path number; 0 for all")
    p.add_argument("--registration-margin", type=float, default=0,
                   help="Generate separate three-cross registration SVG this many mm from page edges; 0 disables")
    p.add_argument("--registration-size", type=float, default=1, help="Registration cross half-length, mm")
    p.add_argument("--force", action="store_true", help="Allow writing in an existing nonempty output folder (overwrites named outputs)")
    p.add_argument("--dry-run", action="store_true", help="Analyse and plan only, without writing files")
    return p


def main(argv=None):
    opts = args_parser().parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", opts.file_prefix):
        raise ValueError("--file-prefix must contain 1-100 ASCII letters, digits, - or _")
    if not opts.input.is_file():
        raise ValueError(f"SVG not found: {opts.input}")
    for key in ("max_minutes", "max_ink_m", "max_draw_m", "max_contacts", "max_vertices", "max_paths",
                "vertex_seconds", "lift_seconds", "ink_mm_per_contact", "ink_mm_per_vertex", "observed_hours"):
        if getattr(opts, key) < 0:
            raise ValueError(f"--{key.replace('_','-')} cannot be negative")
    if opts.draw_speed <= 0 or opts.travel_speed <= 0:
        raise ValueError("Speeds must be positive")
    if opts.from_path < 1:
        raise ValueError("--from-path is one-based and must be at least 1")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    tree = etree.parse(str(opts.input), parser)
    root = tree.getroot()
    if name(root.tag) != "svg":
        raise ValueError("File is not an SVG")
    w, h, base = page_info(root)
    strokes = find_strokes(root, base)
    upper = opts.to_path or len(strokes)
    selected = [s for s in strokes if opts.from_path <= s.number <= upper]
    if not selected:
        raise ValueError("Selected path number range is empty")
    raw_seconds = sum(estimated_cost(s.geometry, opts) for s in strokes)
    raw_seconds += sum(dist(a.geometry.last, b.geometry.first)/opts.travel_speed
                       for a, b in zip(strokes, strokes[1:])
                       if a.geometry.last is not None and b.geometry.first is not None)
    # Estimated time can be calibrated to an actual completed run of the same file.
    time_scale = opts.observed_hours*3600/raw_seconds if opts.observed_hours > 0 else 1.
    if opts.equal_parts and opts.max_minutes > 0:
        raise ValueError("Choose --equal-parts OR --max-minutes (set other to 0)")
    if opts.equal_parts:
        batches, oversized = partition_equal_parts(selected, opts, time_scale, opts.equal_parts)
    else:
        batches, oversized = partition(selected, opts, time_scale)
    outdir = opts.outdir or opts.input.parent/(opts.input.stem+"_jobs")
    if not opts.dry_run:
        if outdir.exists() and any(outdir.iterdir()) and not opts.force:
            raise ValueError(f"Output directory not empty: {outdir}. Choose another or pass --force")
        if opts.registration_margin > 0 and opts.registration_margin <= opts.registration_size:
            raise ValueError("Registration margin must be larger than cross size")
        outdir.mkdir(parents=True, exist_ok=True)
        if opts.force:
            # Clean only files with names generated by this tool; never touch other files.
            for old in outdir.glob(f"{opts.file_prefix}*_paths_*.svg"):
                old.unlink()
            for item in ("manifest.csv", "manifest.json", "RUN_SHEET.md", "registration_marks.svg", "pen_test.svg", "PREVIEW_DO_NOT_PLOT.svg"):
                (outdir/item).unlink(missing_ok=True)
    rows = []
    for n, batch in enumerate(batches, 1):
        filename = f"{opts.file_prefix}{n:03d}_paths_{batch.first_path:04d}-{batch.last_path:04d}.svg"
        if not opts.dry_run:
            subset = preserve_subset(root, {s.element for s in batch.strokes}, {s.element for s in strokes})
            write_svg(outdir/filename, subset, tree.docinfo.doctype)
        rows.append(dict(job=n, filename=filename, first_path=batch.first_path, last_path=batch.last_path,
                         paths=len(batch.strokes), drawing_m=round(batch.draw_mm/1000, 3),
                         pen_up_m=round(batch.travel_mm/1000, 3), ink_equivalent_m=round(batch.ink_mm/1000, 3),
                         pen_contacts=batch.lifts, vertices=batch.vertices,
                         estimated_minutes=round(batch.estimated_seconds/60, 2),
                         start_x_mm=round(batch.start[0], 3) if batch.start else None,
                         start_y_mm=round(batch.start[1], 3) if batch.start else None,
                         end_x_mm=round(batch.finish[0], 3) if batch.finish else None,
                         end_y_mm=round(batch.finish[1], 3) if batch.finish else None))
    info = dict(input=str(opts.input.resolve()), source_paths=len(strokes), selected_paths=len(selected),
                selected_range=[selected[0].number, selected[-1].number],
                page_mm=dict(width=round(w, 5), height=round(h, 5)),
                limits=dict(equal_parts=opts.equal_parts, minutes=opts.max_minutes, ink_equivalent_m=opts.max_ink_m,
                            drawing_m=opts.max_draw_m, contacts=opts.max_contacts,
                            vertices=opts.max_vertices, paths=opts.max_paths),
                time_calibrated=bool(opts.observed_hours),
                oversized_source_paths=oversized,
                totals=dict(drawing_m=round(sum(s.geometry.draw_mm for s in selected)/1000, 3),
                            ink_equivalent_m=round(sum(ink_usage(s.geometry, opts) for s in selected)/1000, 3),
                            estimated_hours=round(sum(b.estimated_seconds for b in batches)/3600, 3)),
                batches=rows)
    if not opts.dry_run:
        if opts.registration_margin > 0:
            registration_svg(outdir/"registration_marks.svg", w, h, opts.registration_margin, opts.registration_size)
        pen_test_svg(outdir/"pen_test.svg")
        create_overview(outdir/"PREVIEW_DO_NOT_PLOT.svg", root, batches,
                        {s.element for s in strokes}, tree.docinfo.doctype)
        make_report(info, outdir, opts, opts.input)
    print(f"SVG: {len(strokes)} source paths, {len(selected)} selected, "
          f"{info['totals']['drawing_m']} m drawn, {len(batches)} jobs")
    print("JOB  PATHS         DRAW(m)  INK-EQ(m)  CONTACTS  EST(min)")
    for row in rows:
        print(f"{row['job']:>3}  {row['first_path']:>4}-{row['last_path']:<4} "
              f"{row['drawing_m']:>9.3f} {row['ink_equivalent_m']:>10.3f} "
              f"{row['pen_contacts']:>9} {row['estimated_minutes']:>9.2f}")
    if oversized:
        print(f"WARNING: {len(oversized)} individual paths exceed a limit and were kept intact. "
              f"Example: {oversized[:10]}")
    print("Dry run: no files written." if opts.dry_run else f"Written to: {outdir.resolve()}")
    return info


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, etree.XMLSyntaxError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
