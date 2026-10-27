# svg-plot-manager
<img width="2542" height="1342" alt="image" src="https://github.com/user-attachments/assets/d3771bb2-854a-4500-a458-eeb2407c55da" />

# SVG Plot Manager v1

**Split large pen-plotter drawings into smaller, easier-to-manage jobs, using a simple web page on your own computer.**

SVG Plot Manager is a free-to-run, local tool for people who use a pen plotter and want to avoid leaving a huge drawing running for hours. It can separate a drawing into sessions, so you can pause between jobs, check ink, change pens, and resume from where you stopped.

**You don't need to know Python or write code to use the interface.** You only need to copy a few commands to get it started.

## What can it do?

- Open SVG drawings exported from programs such as Inkscape or DrawingBotV3.
- Separate drawings by **pen layer**, so each pen colour can have its own set of jobs.
- Split each layer into **2, 4, or 8 roughly equal jobs**, or set a **maximum estimated time** per job.
- Optionally limit the number of **pen-down contacts**, useful for stippling.
- Preview each session before exporting it.
- Download a single session SVG or a ZIP containing a whole layer's sessions.
- Optionally include **reversed-path-order alternatives** in the exported ZIP.
- Keep track of completed sessions and resume at a particular path.
- Switch between light and dark themes.

The app works **offline**, runs in your browser, and keeps your drawings on your computer. It doesn't send them to a website.

## What you need

- A computer running **Ubuntu Linux** (the instructions below are for Ubuntu).
- **Python 3** (usually already installed on Ubuntu).
- An SVG drawing to split.
- Firefox or another modern browser.

The instructions below install one Python library called `lxml`. You do **not** need to install Docker, Node.js, or Flask.

## Installation (Ubuntu, step by step)

### 1. Download the project

On GitHub, click the green **Code** button, choose **Download ZIP**, and extract it to a folder. If you downloaded a release ZIP called `SVG_Plot_Manager_Web_v1.5.zip`, extract that instead.

You should see these files together in the extracted folder: `start.sh`, `web_app.py`, `svg_plot_manager.py`, an `assets` folder, and an `examples` folder.

### 2. Install the required library

Open **Terminal** (press `Ctrl` + `Alt` + `T` on Ubuntu) and paste:

```bash
sudo apt update
sudo apt install python3 python3-lxml
```

Your computer may ask for your Linux password. While typing it, you won't see any characters appear. That's normal.

### 3. Open a terminal in the project folder

The easiest way is to open the extracted folder in your file manager, right-click the empty space, and choose **Open in Terminal**.

Alternatively, use `cd` to change into the folder. For example, if it is inside Downloads:

```bash
cd ~/Downloads/SVG_Plot_Manager_Web_v1.5
```

Your folder name might differ depending on how you downloaded or extracted it.

### 4. Start SVG Plot Manager

```bash
./start.sh
```

If Linux reports **Permission denied**, run:

```bash
chmod +x start.sh
./start.sh
```

The app should open in your browser. If it doesn't, open Firefox and visit:

**http://127.0.0.1:8765**

That address means **your own computer**. The app is not being hosted on the public internet.

**Keep the Terminal window open while you're using the app.** Closing it stops the local server.

## Your first drawing: a simple walkthrough

1. Click **Drop SVG or browse** and choose your `.svg` drawing. If you want to try it before using a real drawing, open the included `examples/multi_pen_demo.svg`.
2. Select the **pen layer** you want to work on using the tabs beside the preview. The demo file contains three layers.
3. Choose **Equal parts** to split the selected layer into 2, 4, or 8 sessions, or **Max time** to set an estimated minutes-per-session limit.
4. If your drawing contains lots of dots or separate little strokes, consider enabling **Limit pen-down events**.
5. Click **Generate layer sessions**.
6. Click through the individual sessions to preview what each one will draw.
7. Download individual SVGs or click **Export layer ZIP** to save them together.
8. Repeat for any other pen layers. You can give different layers different splitting settings.

**Before closing the app, export the files you want to keep.** Generated session files are temporary and are removed when the server stops.

## What are reversed sessions?

The **Include reversed sessions in ZIP** checkbox puts an extra `_reversed.svg` alternative next to each normal session in the exported ZIP.

These alternative files draw the original paths in **reverse order**. They do **not** draw every line backwards or reverse the numbered session order. Choose the normal **or** reversed version of each session: plotting both would draw over the same artwork twice.

## How to resume after a stopped drawing

The app tracks completed sessions with checkboxes in the browser. If your plot stops partway through, you can use **Resume from layer path** to generate jobs starting at a later path.

Path numbering is **per pen layer**, starting at 1. A partially completed path may need to be redrawn in full. Completion tracking is stored in that browser, so it isn't a replacement for saving your exported ZIP files and taking notes.

## Important: keep your paper aligned

**Splitting a drawing into jobs does not automatically fix positioning or drift.** Every exported SVG keeps the original page size and coordinates so the jobs can line up, but your plotter must start them from exactly the same physical reference.

- Keep the paper fixed in place between sessions.
- Use the **same paper size, origin, orientation and scale** for every session.
- Disable automatic **fit-to-page, re-centering and path reordering** in your plotting software if they would change alignment or plotting order.
- If your machine has lost steps, telling it to move to `(0, 0)` may not physically restore its position. Use reliable homing or registration.
- Inspect pen flow and change or refill pens between jobs as needed.

**Time estimates are only estimates.** Different machines, speeds, servo delays and drawing styles can change actual plotting time significantly.

## SVG compatibility

The tool is designed for drawings made from SVG **paths** (`<path>` elements), including paths inside groups and Inkscape layers.

If your SVG contains live text, rectangles, circles or other shapes, convert those items to paths in Inkscape before using the planner if necessary. The app cannot turn a PNG or JPG photograph into vector paths.

## How to stop or restart

To stop the app, go back to its Terminal window and press:

```text
Ctrl+C
```

To start it again, open a terminal in the same folder and run `./start.sh`.

If port 8765 is already being used, stop the other running copy or choose another port:

```bash
./start.sh --port 8766
```

Then visit **http://127.0.0.1:8766**.

## Common problems

| Problem | What to try |
| --- | --- |
| `./start.sh: Permission denied` | Run `chmod +x start.sh`, then `./start.sh` again. |
| `No module named lxml` | Run `sudo apt install python3-lxml`. |
| Browser page won't open | Check that the Terminal is still running and open `http://127.0.0.1:8765`. |
| Port already in use | Stop the other copy with `Ctrl+C`, or use `./start.sh --port 8766`. |
| Some artwork is missing or rejected | Convert text and shapes to paths in Inkscape, and try again. |
| Jobs do not line up on paper | Verify identical scale, origin and orientation for every session; turn off automatic fit/re-centering. |
| Exported jobs have disappeared | Reopen the source SVG and regenerate them. Always save your ZIPs before stopping the app. |

## What's inside the download?

| File or folder | What it does |
| --- | --- |
| `start.sh` | Starts the local web app. |
| `web_app.py` | Runs the browser interface and handles SVG uploads/exports locally. |
| `svg_plot_manager.py` | Splits SVG paths into plotting sessions. |
| `assets/` | The web page, appearance and JavaScript controls. |
| `examples/multi_pen_demo.svg` | Small sample with three pen layers. |
| `examples/1_plotted_1a4.svg` | Larger example drawing for testing. |

## Privacy and safety

The app listens on **127.0.0.1**, which means only your local computer can normally access it. Drawing files aren't uploaded to a third-party server. Only open SVG files from sources you trust; SVG files can contain more than just harmless lines and shapes.

## Who is this for?

SVG Plot Manager is particularly useful for long-running pen-plotter projects, layered drawings, stippling, and jobs that would otherwise run for many hours without convenient pen-change or inspection points.

**Version:** 1.5
