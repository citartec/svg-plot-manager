# SVG Plot Manager Web v1.5

A local browser app for managing long CoreXY / pen-plotter drawings, including one set of sessions **per pen layer**. Works on Ubuntu with Python 3 and `lxml`. All processing happens on your own computer.

## Start

```bash
sudo apt install python3-lxml
cd SVG_Plot_Manager_Web_v1.5
./start.sh
```

Open **http://127.0.0.1:8765** in Firefox if it doesn't open automatically. If you're running an older edition already, stop it with **Ctrl+C** before starting v1.4, or run `./start.sh --port 8766` and open http://127.0.0.1:8766.

## Pen layers

1. Load a DrawingBotV3 or Inkscape SVG with **Drop SVG or browse**. The included `examples/multi_pen_demo.svg` has three different pens for testing. `examples/1_plotted_1a4.svg` is the original, real DrawingBotV3 black-pen example.
2. Select a pen-layer tab immediately **to the right of the preview**. Inkscape layer labels are detected automatically. If the document has no Inkscape layers, its top-level drawing groups are treated as separate layers where possible, otherwise it becomes one main layer.
3. On the selected layer, choose either **Equal parts** (2, 4 or 8 jobs with approximately equal estimated plotting workload) or **Max time** (target maximum minutes for each session). These options apply **only to the selected pen layer**.
4. Optionally select **Limit pen-down events** to cap the number of pen placements per job. Especially helpful for stippling, though individual unsplittable source paths may exceed the cap and trigger a warning.
5. Click **Generate layer sessions**. Inspect the grayscale-shaded layer preview, highlight individual jobs, download each SVG or **Export layer ZIP**, and tick jobs off as they are plotted.
6. Check **Include reversed sessions in ZIP** at export if you want one backward-order alternative for every generated job. The normal forward jobs are always included. Reversed sessions keep the original path shapes/directions and only reverse the order in which paths are drawn. Do not plot both copies unless you want to overdraw.
7. Switch to the next pen-layer tab and choose a completely different split. **Existing generated jobs and their completion marks are retained when you switch layers**. **Clear layer** only clears the selected layer's jobs.

The monochrome dark/light theme button at the top right remembers your preference in the browser.

Generated job SVG filenames begin with a filesystem-safe version of the pen layer name and layer ID, for example `Copic_Original_100_Black_layer_1_session_001_paths_0001-0162.svg`. ZIP exports use the layer name too. If two layers have the same name, their numbered IDs distinguish their filenames.

When checked, **Include reversed sessions in ZIP** adds one `_reversed.svg` alternative for every normal session SVG. It does not reverse the order of the numbered sessions, nor reverse the direction *within* a path. The checkbox applies only to the ZIP export and does not regenerate the sessions.

## Time calibration and recovery

The optional **Known time for this layer (hours)** calibrates rough runtime estimates using a prior complete run of that **same layer**, not the entire multicolour source. Leave at 0 if unknown. These are estimates, not guarantees.

**Resume from layer path** starts jobs after previously completed paths. The path numbers are counted *within the selected pen layer*, starting at 1. Use the job checklist and pen-layer tab to identify the path number for recovery.

## Important precautions

- Each generated job is an independent SVG with the original **page dimensions, coordinates, nested group transforms, stroke geometry and path order**. No changes are made to the uploaded original. Only paths belonging to the selected layer are included in its jobs. Other empty Inkscape pen layers are removed from exported jobs.
- The source must use SVG `<path>` drawing elements; convert rectangles, shapes, and text to paths first where required. There is no conversion from raster images.
- Paths are not cut mid-stroke. A source path can have several internal `M` subpaths and therefore multiple pen-down contacts; an indivisible path can exceed a chosen limit. That will be flagged in the status.
- These are *logical* plotting sessions. Merely returning to the plotter's software home cannot fix missed steps or paper movement. Use repeatable physical homing or registration, with paper fixed securely throughout.
- dDraw or other drivers may re-sort, re-centre or re-scale geometry; use the **same physical origin, scale, positioning and sorting settings** for every job. Disable automatic fit-to-page and sorting if they alter the sequence or alignment.
- The cap for pen-down events measures pen contacts, not remaining ink volume. Stop between sessions to check ink flow, replace/refill a pen and re-establish registration.
- Completed checkmarks are stored in this browser's localStorage and associated with the SVG's content and the layer settings.
- The app creates temporary session files while the server is running. **Export and save each layer's ZIP before stopping the server**, which removes its temporary output.
- The server binds only to 127.0.0.1, not your LAN. SVG files are processed locally. SVGs with scripts or externally referenced resources are rejected for safety.

## Included files

- `web_app.py`: browser/server endpoints, per-layer extraction, secure previews and per-layer downloads
- `svg_plot_manager.py`: session splitter and original CLI utility
- `assets/index.html`, `assets/style.css`, `assets/app.js`: responsive browser UI
- `examples/multi_pen_demo.svg`: three-layer demonstration file
- `examples/1_plotted_1a4.svg`: provided DrawingBotV3 sample
- `start.sh`: Ubuntu startup script

To verify the included SVG's layer detection, run:

```bash
python3 - <<'PY'
from pathlib import Path
from web_app import analyze
for layer in analyze(Path('examples/multi_pen_demo.svg'))['layers']:
    print(layer['name'], layer['paths'], 'paths')
PY
```

## Command-line alternative

The unchanged `svg_plot_manager.py` can process any path-only SVG. For pen-layer-specific operation, use the browser to select the layer first.

```bash
python3 svg_plot_manager.py drawing.svg --equal-parts 4 --outdir drawing_jobs
```
