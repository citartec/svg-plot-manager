#!/usr/bin/env python3
"""Local-only browser UI for svg_plot_manager.py (Python standard library + lxml).

Run: python3 web_app.py; open http://127.0.0.1:8765
No Internet, account, uploads to third-party services, or external JS/CSS required.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import html
import io
import unicodedata
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import zipfile

from lxml import etree
import svg_plot_manager as plot

HERE = Path(__file__).resolve().parent
MAX_UPLOAD = 25 * 1024 * 1024
MAX_SESSIONS = 12
EXPIRE_SECONDS = 48 * 60 * 60
WORKDIR = Path(tempfile.mkdtemp(prefix="svg_plot_web_"))
SESSIONS = {}
LOCK = threading.RLock()
PATH_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,180}$")
BASIC_SVG = {"svg", "g", "path", "a", "switch"}
SETTINGS = {
    "max_minutes": (float, 0, 0, 100000),
    "equal_parts": (int, 0, 0, 8),
    "max_ink_m": (float, 0, 0, 100000),
    "max_draw_m": (float, 0, 0, 100000),
    "max_contacts": (int, 0, 0, 100000000),
    "max_vertices": (int, 0, 0, 100000000),
    "max_paths": (int, 0, 0, 100000000),
    "draw_speed": (float, 20, 0.0001, 100000),
    "travel_speed": (float, 80, 0.0001, 100000),
    "lift_seconds": (float, 0.6, 0, 100000),
    "vertex_seconds": (float, 0, 0, 100000),
    "observed_hours": (float, 0, 0, 100000),
    "ink_mm_per_contact": (float, 0.5, 0, 100000),
    "ink_mm_per_vertex": (float, 0, 0, 100000),
    "from_path": (int, 1, 1, 100000000),
    "to_path": (int, 0, 0, 100000000),
    "registration_margin": (float, 0, 0, 10000),
    "registration_size": (float, 1, 0.0001, 10000),
}


def cleanup():
    with LOCK:
        now = time.time()
        keys = sorted(SESSIONS, key=lambda x: SESSIONS[x]["created"])
        expired = [key for key in keys if now - SESSIONS[key]["created"] > EXPIRE_SECONDS]
        remaining = [key for key in keys if key not in expired]
        expired.extend(remaining[:max(0, len(remaining) - MAX_SESSIONS)])
        for key in expired:
            sess = SESSIONS.pop(key, None)
            if sess:
                shutil.rmtree(sess["dir"], ignore_errors=True)


def safe_parse(path):
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    if plot.name(root.tag) != "svg":
        raise ValueError("This file does not contain an SVG drawing")
    return tree, root


def has_external_css(text):
    if '@import' in text.lower():
        return True
    for match in re.finditer(r'url\s*\(([^)]*)\)', text, re.I | re.S):
        ref = match.group(1).strip().strip('\"\'').strip()
        if not ref.startswith('#'):
            return True
    return False


def reject_active_svg(root):
    """Output files retain the source SVG; refuse potentially active elements/links."""
    denied = {'script', 'foreignObject', 'iframe', 'object', 'embed', 'audio', 'video',
              'animate', 'animateTransform', 'animateMotion', 'set', 'handler'}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname
        if tag in denied:
            raise ValueError(f'SVG contains unsupported active element <{tag}>. Remove it before uploading.')
        if tag == 'style':
            css = (el.text or '').lower()
            if has_external_css(css):
                raise ValueError('SVG contains externally referenced CSS; please remove it first.')
        for key, value in el.attrib.items():
            keyname = etree.QName(key).localname.lower()
            if keyname.startswith('on') and len(keyname) > 2:
                raise ValueError('SVG contains script event-handler attributes.')
            if keyname in ('href', 'src') and value.strip() and not value.strip().startswith('#'):
                raise ValueError('SVG references an external resource; please embed or remove it.')
            if keyname == 'style' and has_external_css(value):
                raise ValueError('SVG contains externally referenced CSS resources.')
    return root


def find_layers(root, strokes):
    """Determine pen layers in source order; paths belong to exactly one layer.

    Prefer explicit Inkscape layers (DrawingBotV3 exports these). For simple SVGs
    without Inkscape metadata, use top-level drawable groups; otherwise group all
    paths under a single layer. Nested Inkscape layers own their own paths only.
    """
    ns = '{http://www.inkscape.org/namespaces/inkscape}'
    explicit = [node for node in root.iter() if isinstance(node.tag, str)
                and plot.name(node.tag) == 'g' and node.get(ns+'groupmode') == 'layer']
    use_groups = bool(explicit)
    def owner_for(element):
        if use_groups:
            for ancestor in [element, *element.iterancestors()]:
                if ancestor in explicit:
                    return ancestor
            return None
        # Fallback: each immediate root group can represent one pen in a plain SVG.
        parents = list(element.iterancestors())
        for parent in reversed(parents):
            if parent.getparent() is root and plot.name(parent.tag) == 'g':
                return parent
        return None

    result, lookup = [], {}
    for stroke in strokes:
        owner = owner_for(stroke.element)
        key = id(owner) if owner is not None else 'ungrouped'
        if key not in lookup:
            label = (owner.get(ns+'label') or owner.get('data-name') or owner.get('id')
                     or f'Pen layer {len(result)+1}') if owner is not None else 'Ungrouped paths'
            # The default ungrouped case is shown as a single main pen layer.
            if owner is None and not result:
                label = 'Main pen layer'
            result.append({'id': f'layer_{len(result)+1}', 'name': label,
                           'strokes': [], 'group': owner})
            lookup[key] = result[-1]
        lookup[key]['strokes'].append(stroke)
    return result


def layers_summary(layers):
    summary = []
    for layer in layers:
        strokes = layer['strokes']
        summary.append({'id': layer['id'], 'name': layer['name'],
                        'paths': len(strokes),
                        'draw_m': round(sum(s.geometry.draw_mm for s in strokes)/1000, 3),
                        'pen_contacts': sum(s.geometry.pen_contacts for s in strokes),
                        'vertices': sum(s.geometry.vertex_count for s in strokes)})
    return summary


def analyze(path):
    _tree, root = safe_parse(path)
    reject_active_svg(root)
    w, h, matrix = plot.page_info(root)
    strokes = plot.find_strokes(root, matrix)
    layers = find_layers(root, strokes)
    drawing = sum(s.geometry.draw_mm for s in strokes) / 1000
    travel = sum(s.geometry.internal_travel_mm for s in strokes) / 1000
    travel += sum(plot.dist(a.geometry.last, b.geometry.first)
                  for a, b in zip(strokes, strokes[1:])
                  if a.geometry.last is not None and b.geometry.first is not None) / 1000
    return {
        'page_mm': {'width': round(w, 3), 'height': round(h, 3)},
        'paths': len(strokes),
        'draw_m': round(drawing, 3),
        'pen_up_m': round(travel, 3),
        'pen_contacts': sum(s.geometry.pen_contacts for s in strokes),
        'vertices': sum(s.geometry.vertex_count for s in strokes),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'layers': layers_summary(layers),
    }


def layer_source(path, layer_id, output):
    tree, root = safe_parse(path)
    w, h, base = plot.page_info(root)
    strokes = plot.find_strokes(root, base)
    layers = find_layers(root, strokes)
    layer = next((x for x in layers if x['id'] == layer_id), None)
    if layer is None:
        raise ValueError('Unknown pen layer')
    subset = plot.preserve_subset(root, {s.element for s in layer['strokes']},
                                  {s.element for s in strokes})
    # Remove other, now-empty Inkscape pen layers. Plot drivers should see only
    # the chosen pen, not a list of empty layers from the original document.
    ink_mode = '{http://www.inkscape.org/namespaces/inkscape}groupmode'
    for group in reversed(list(subset.iter())):
        if not isinstance(group.tag, str) or plot.name(group.tag) != 'g':
            continue
        if group.get(ink_mode) == 'layer' and not any(
                isinstance(n.tag, str) and plot.name(n.tag) == 'path'
                for n in group.iterdescendants()):
            parent = group.getparent()
            if parent is not None:
                parent.remove(group)
    plot.write_svg(output, subset, tree.docinfo.doctype)
    return layer

def settings_from_json(value):
    if not isinstance(value, dict):
        raise ValueError("Settings must be an object")
    result = {}
    for key, (kind, default, minimum, maximum) in SETTINGS.items():
        raw = value.get(key, default)
        if isinstance(raw, bool):
            raise ValueError(f"Invalid {key}")
        try:
            num = kind(raw)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"Invalid number for {key}") from exc
        if not (minimum <= num <= maximum):
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        if kind is int and float(raw) != num:
            raise ValueError(f"{key} must be an integer")
        result[key] = num
    if result["to_path"] and result["to_path"] < result["from_path"]:
        raise ValueError("End path must be at least the start path")
    if result["equal_parts"] not in (0, 2, 4, 8):
        raise ValueError("Equal parts must be 2, 4, or 8")
    if result["equal_parts"] and result["max_minutes"]:
        raise ValueError("Choose equal parts or maximum session time, not both")
    if not any(result[key] > 0 for key in
               ("equal_parts", "max_minutes", "max_ink_m", "max_draw_m", "max_contacts", "max_vertices", "max_paths")):
        raise ValueError("Enable at least one job-splitting limit")
    if result["registration_margin"] and result["registration_margin"] <= result["registration_size"]:
        raise ValueError("Registration margin must exceed cross half-size")
    return result


def palette(job):
    shades = ('#252525', '#484848', '#666666', '#888888',
              '#a1a1a1', '#777777', '#575757', '#383838')
    return shades[(job - 1) % len(shades)]


def preview_svg(path, layer_id, rows=None, active=0):
    """Path-only safe preview of one pen layer with original group transforms."""
    _, src = safe_parse(path)
    w, h, base = plot.page_info(src)
    strokes = plot.find_strokes(src, base)
    layers = find_layers(src, strokes)
    layer = next((x for x in layers if x['id'] == layer_id), None)
    if layer is None:
        raise ValueError('Unknown pen layer')
    dst = etree.Element(plot.SVG_TAG + 'svg', nsmap={None: plot.SVG})
    for key in ('width', 'height', 'viewBox', 'preserveAspectRatio'):
        if key in src.attrib:
            dst.set(key, src.get(key))
    selected = {stroke.element: i for i, stroke in enumerate(layer['strokes'], 1)}
    ranges = [(row['job'], row['first_path'], row['last_path']) for row in (rows or [])]
    def visit(node, parent):
        if not isinstance(node.tag, str):
            return
        tag = plot.name(node.tag)
        if tag == 'path':
            index = selected.get(node)
            if index is None:
                return
            el = etree.SubElement(parent, plot.SVG_TAG + 'path')
            el.set('d', node.get('d', ''))
            if node.get('transform'):
                el.set('transform', node.get('transform'))
            job = next((n for n, lo, hi in ranges if lo <= index <= hi), 0)
            col = ('#c5c5c5' if (active and job != active) or not job else palette(job)) if rows else '#555555'
            el.set('stroke', col)
            el.set('stroke-width', '0.9')
            el.set('fill', 'none')
            el.set('stroke-linecap', 'round')
            el.set('stroke-linejoin', 'round')
            return
        if tag in ('g', 'a', 'switch'):
            group = etree.SubElement(parent, plot.SVG_TAG + 'g')
            if node.get('transform'):
                group.set('transform', node.get('transform'))
            for ch in node:
                visit(ch, group)
        elif tag == 'svg':
            for ch in node:
                visit(ch, parent)
    visit(src, dst)
    return etree.tostring(dst, encoding='utf-8', xml_declaration=True)

def layer_prefix(layer_id, layer_name):
    # ASCII filenames survive copying through plotter utilities and old filesystems.
    plain = unicodedata.normalize('NFKD', layer_name).encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^A-Za-z0-9]+', '_', plain).strip('_')[:65].rstrip('_') or 'Pen'
    return f"{slug}_{layer_id}_"


def reverse_svg_bytes(contents):
    """Reverse SVG *stroke ordering*, preserving path data, styles and transforms.

    Only SVG drawing path-bearing groups are reordered. <defs> and other
    metadata are left untouched. Reversing child order at every drawing group
    reverses the document-wide depth-first path sequence, including mixed
    nested groups. The direction inside each d attribute is unchanged.
    """
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    tree = etree.parse(io.BytesIO(contents), parser)
    root = tree.getroot()
    containers = {'svg', 'g', 'a', 'switch'}

    def has_paths(element):
        if not isinstance(element.tag, str):
            return False
        kind = plot.name(element.tag)
        if kind == 'path':
            return True
        return kind in containers and any(has_paths(child) for child in element)

    def reverse(element):
        if plot.name(element.tag) not in containers:
            return
        children = list(element)
        indices = [i for i, child in enumerate(children) if has_paths(child)]
        matched = [children[i] for i in indices][::-1]
        for i, node in zip(indices, matched):
            children[i] = node
        element[:] = children
        for child in children:
            if isinstance(child.tag, str) and plot.name(child.tag) in containers:
                reverse(child)

    reverse(root)
    return etree.tostring(tree, encoding='UTF-8', xml_declaration=True,
                          doctype=tree.docinfo.doctype or None)


def export_zip_bytes(folder, info, include_reversed=False):
    """Export a layer with optional reversed alternatives; never mutate jobs."""
    buffer = io.BytesIO()
    file_names = {r['filename'] for r in info['batches']}
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix != '.zip':
                archive.write(file, arcname=f"plot_jobs/{file.name}")
        if include_reversed:
            for filename in sorted(file_names):
                original = (folder/filename).read_bytes()
                name = filename[:-4] + '_reversed.svg'
                archive.writestr(f'plot_jobs/{name}', reverse_svg_bytes(original))
            archive.writestr('plot_jobs/REVERSED_VERSIONS_README.txt',
                             'Reversed SVGs are ALTERNATIVES to their matching normal sessions.\n'
                             'Do not plot both versions of the same session unless intentionally overdrawing.\n'
                             'Only the ORDER of paths has been reversed; the direction of each path\n'
                             'and its geometric coordinates remain unchanged.\n'
                             'Most plotting software must have automatic path sorting DISABLED.\n'
                             'The session numbers are not renumbered or rearranged.\n')
    return buffer.getvalue()


def build_zip(folder, destination):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(folder.iterdir()):
            if file.is_file() and file != destination:
                archive.write(file, arcname=f"plot_jobs/{file.name}")


class Handler(BaseHTTPRequestHandler):
    server_version = 'SVGPlotManager/1.5'
    def log_message(self, fmt, *args):
        print('[local web]', fmt % args)

    def respond(self, code, data, content_type='application/json; charset=utf-8', filename=None):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, code, message):
        self.respond(code, {'error': str(message)})

    def request_json(self, max_size=16384):
        if self.headers.get('X-Plot-Manager') != '1':
            raise ValueError('Invalid request header')
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError as exc:
            raise ValueError('Invalid request size') from exc
        if not 0 < size <= max_size:
            raise ValueError('Invalid settings size')
        return json.loads(self.rfile.read(size))

    def do_GET(self):
        parts = urlsplit(self.path)
        route = parts.path
        if route in ('/', '/index.html'):
            return self.respond(200, (HERE / 'assets/index.html').read_bytes(), 'text/html; charset=utf-8')
        if route == '/assets/app.js':
            return self.respond(200, (HERE / 'assets/app.js').read_bytes(), 'text/javascript; charset=utf-8')
        if route == '/assets/style.css':
            return self.respond(200, (HERE / 'assets/style.css').read_bytes(), 'text/css; charset=utf-8')
        if route == '/api/example':
            f = HERE / 'examples/1_plotted_1a4.svg'
            if f.exists():
                return self.respond(200, f.read_bytes(), 'image/svg+xml', 'example.svg')
            return self.error(404, 'Example file not installed')
        query = parse_qs(parts.query)
        token = query.get('session', [''])[0]
        with LOCK:
            session = SESSIONS.get(token)
        if route == '/api/preview':
            if not session:
                return self.error(404, 'Session expired. Upload the SVG again.')
            try:
                number = int(query.get('job', ['0'])[0])
                layer = query.get('layer', ['layer_1'])[0]
                plan = session['plans'].get(layer)
                data = preview_svg(session['source'], layer, plan.get('rows') if plan else None, number)
                return self.respond(200, data, 'image/svg+xml; charset=utf-8')
            except (ValueError, etree.XMLSyntaxError) as exc:
                return self.error(400, str(exc))
        if route == '/api/export':
            if not session:
                return self.error(404, 'Session expired. Upload the SVG again.')
            layer = query.get('layer', [''])[0]
            plan = session['plans'].get(layer)
            if not plan:
                return self.error(404, 'No jobs generated for this layer')
            value = query.get('reverse', ['0'])[0]
            if value not in ('0', '1'):
                return self.error(400, 'Invalid reverse option')
            prefix = plan['prefix'].removesuffix('_')
            title = f'{prefix}_sessions' + ('_with_reversed' if value == '1' else '') + '.zip'
            contents = export_zip_bytes(plan['output'], plan['info'], value == '1')
            return self.respond(200, contents, 'application/zip', title)
        if route == '/api/download':
            if not session:
                return self.error(404, 'Session expired. Upload the SVG again.')
            filename = query.get('file', [''])[0]
            if not PATH_NAME.fullmatch(filename) or filename in ('.', '..'):
                return self.error(400, 'Invalid download filename')
            layer = query.get('layer', ['layer_1'])[0]
            plan = session['plans'].get(layer)
            output = plan.get('output') if plan else None
            if not output:
                return self.error(404, 'No jobs generated yet')
            target = output / filename
            if not target.is_file() or target.is_symlink():
                return self.error(404, 'File does not exist')
            content = 'application/zip' if filename.endswith('.zip') else 'image/svg+xml' if filename.endswith('.svg') else 'application/octet-stream'
            return self.respond(200, target.read_bytes(), content, filename)
        return self.error(404, 'Not found')

    def do_POST(self):
        parts = urlsplit(self.path)
        route = parts.path
        try:
            if route == '/api/upload':
                if self.headers.get('X-Plot-Manager') != '1' or self.headers.get('Content-Type') != 'image/svg+xml':
                    return self.error(400, 'Expected SVG upload from this app')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    return self.error(400, 'Invalid upload length')
                if not 0 < size <= MAX_UPLOAD:
                    return self.error(413, 'SVG must be less than 25 MB')
                blob = self.rfile.read(size)
                if len(blob) != size:
                    return self.error(400, 'Incomplete upload')
                raw_name = unquote(self.headers.get('X-Filename', 'drawing.svg'))
                filename = Path(raw_name.replace('\\', '/')).name
                if not filename.lower().endswith('.svg'):
                    return self.error(400, 'Please select an .svg file')
                cleanup()
                token = secrets.token_urlsafe(20)
                folder = WORKDIR / token
                folder.mkdir(mode=0o700)
                source = folder / 'source.svg'
                source.write_bytes(blob)
                try:
                    stats = analyze(source)
                except Exception:
                    shutil.rmtree(folder, ignore_errors=True)
                    raise
                sess = dict(dir=folder, source=source, name=filename[:160],
                            stats=stats, sha256=hashlib.sha256(blob).hexdigest(),
                            plans={}, created=time.time())
                with LOCK:
                    SESSIONS[token] = sess
                return self.respond(200, dict(session=token, filename=filename, sha256=sess['sha256'], stats=stats))
            if route == '/api/clear':
                payload = self.request_json()
                token = payload.get('session', '')
                layer_id = payload.get('layer', '')
                with LOCK:
                    session = SESSIONS.get(token)
                    if not session:
                        return self.error(404, 'Session expired. Upload the SVG again.')
                    if layer_id not in {x['id'] for x in session['stats']['layers']}:
                        return self.error(400, 'Unknown pen layer')
                    old = session['plans'].pop(layer_id, None)
                if old:
                    shutil.rmtree(old['output'], ignore_errors=True)
                return self.respond(200, {'cleared': True, 'layer': layer_id})
            if route == '/api/plan':
                payload = self.request_json()
                token = payload.get('session', '')
                layer_id = payload.get('layer', '')
                with LOCK:
                    session = SESSIONS.get(token)
                if not session:
                    return self.error(404, 'Session expired. Upload the SVG again.')
                if layer_id not in {x['id'] for x in session['stats']['layers']}:
                    return self.error(400, 'Unknown pen layer')
                opts = settings_from_json(payload.get('options', {}))
                folder = session['dir']
                new_output = folder / ('jobs_' + layer_id + '_' + secrets.token_hex(6))
                new_output.mkdir()
                extracted = folder / ('source_' + layer_id + '_' + secrets.token_hex(6) + '.svg')
                log = io.StringIO()
                try:
                    layer = layer_source(session['source'], layer_id, extracted)
                    prefix = layer_prefix(layer_id, layer['name'])
                    argv = [str(extracted), '--outdir', str(new_output),
                            '--file-prefix', prefix + 'session_']
                    for key, value in opts.items():
                        argv.extend(['--' + key.replace('_', '-'), str(value)])
                    with redirect_stdout(log):
                        info = plot.main(argv)
                    # The source was only an intermediary; its extra copy is not
                    # a plottable job and should not be bundled or offered as such.
                    extracted.unlink()
                    info['layer_id'] = layer_id
                    info['file_prefix'] = prefix
                    info['layer_name'] = next(x['name'] for x in session['stats']['layers'] if x['id'] == layer_id)
                    (new_output/'manifest.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
                    # Normal/reversed archives are generated on export, on demand.
                except Exception:
                    shutil.rmtree(new_output, ignore_errors=True)
                    raise
                finally:
                    extracted.unlink(missing_ok=True)
                with LOCK:
                    previous = session['plans'].get(layer_id)
                    session['plans'][layer_id] = {'rows': info['batches'], 'output': new_output, 'info': info, 'prefix': prefix}
                if previous:
                    shutil.rmtree(previous['output'], ignore_errors=True)
                return self.respond(200, dict(info=info, files=[f.name for f in sorted(new_output.iterdir()) if f.is_file()],
                                              zip=f'{prefix}sessions.zip', layer=layer_id))
            return self.error(404, 'Not found')
        except (ValueError, TypeError, json.JSONDecodeError, etree.XMLSyntaxError, OSError) as exc:
            return self.error(400, str(exc))
        except Exception as exc:
            print('Unexpected server error:', repr(exc))
            return self.error(500, 'Processing failed; check terminal output')


def main():
    parser = argparse.ArgumentParser(description='Local browser interface for SVG Plot Manager')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'\nSVG Plot Manager running locally: http://127.0.0.1:{args.port}')
    print('Files stay on this computer. Ctrl+C to stop.\n', flush=True)
    import webbrowser
    try:
        webbrowser.open(f'http://127.0.0.1:{args.port}')
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping SVG Plot Manager')
    finally:
        httpd.server_close()
        shutil.rmtree(WORKDIR, ignore_errors=True)

if __name__ == '__main__':
    main()
