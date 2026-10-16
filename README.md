# FlatCAM Evo

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Qt](https://img.shields.io/badge/GUI-PyQt6-green)
![Platforms](https://img.shields.io/badge/Platforms-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

**FlatCAM Evo** is a free, open-source 2D CAM program for PCB manufacturing. It reads the files that a PCB design
program makes (Gerber, Excellon, board files of Altium, KiCad, Eagle, OrCAD/Allegro and Sprint Layout) and makes the
G-code that a CNC router, a CNC mill or a laser needs to make the board: isolation routing, copper clearing, drilling,
slot milling, board cutout, solder paste dispensing and more.

FlatCAM Evo continues [FlatCAM](http://flatcam.org) by Juan Pablo Caram. It has a modern PyQt6 interface, a fast
OpenGL canvas, many new plugins and a Tcl scripting shell for automation.

- Stable code: branch [`main`](https://github.com/MariusAdrian88/FlatCAM_Evo/tree/main)
- Development code: branch [`1.0`](https://github.com/MariusAdrian88/FlatCAM_Evo/tree/1.0)
- Video tutorials: [YouTube playlist](https://www.youtube.com/playlist?list=PLVvP2SYRpx-AQgNlfoxw93tXUXon7G94_)

---

## Contents

- [What FlatCAM Evo does](#what-flatcam-evo-does)
- [Main features](#main-features)
- [Screenshots](#screenshots)
- [Installation](#installation)
- [Quick start: isolate and drill a board](#quick-start-isolate-and-drill-a-board)
- [Object types](#object-types)
- [Plugins](#plugins)
- [Editors](#editors)
- [File formats](#file-formats)
- [G-code preprocessors](#g-code-preprocessors)
- [Path optimization](#path-optimization)
- [Tcl scripting and automation](#tcl-scripting-and-automation)
- [Preferences, Tools Database, themes and languages](#preferences-tools-database-themes-and-languages)
- [For developers](#for-developers)
- [Reporting bugs and contributing](#reporting-bugs-and-contributing)
- [License and credits](#license-and-credits)

---

## What FlatCAM Evo does

The workflow has 4 steps:

1. **Load** the manufacturing files of the board: Gerber layers (copper, solder mask, silkscreen, outline) and
   Excellon drill files. You can also import a complete board file (Altium, KiCad, Eagle, OrCAD/Allegro, Sprint
   Layout 6), or SVG, DXF, PDF, HPGL2 and images.
2. **Prepare** the objects: move, mirror (for 2-sided boards), rotate, scale, panelize, join, or edit them in the
   built-in editors.
3. **Generate** the toolpaths with a plugin: isolation routing around the tracks, removal of the copper that is not
   used, drill and slot paths, the board cutout with tabs, solder paste paths and more. A toolpath is a Geometry
   object.
4. **Create a CNC job** from the Geometry (or directly from an Excellon file for drilling), check it on the canvas
   (travel distance, estimated time), and save the G-code for your machine with the correct preprocessor.

The program keeps all objects of a job in 1 **project** file (`.FlatPrj`).

---

## Main features

- Native parsers for **Gerber** (RS-274X, with aperture macros, regions, polarity changes and step and repeat) and
  **Excellon** (drills, routed and drilled slots, all number formats).
- **Direct import of PCB board files**: Altium `.PcbDoc`, KiCad `.kicad_pcb`, Eagle `.brd`, OrCAD/Allegro `.brd`
  and Sprint Layout 6 `.lay6`. Each copper or mask layer becomes a Gerber object, and each drill group becomes an
  Excellon object.
- **Isolation routing** with many passes, rest machining, V-shape tools and area selection.
- **Non-copper clearing (NCC)** and **Paint** with the Standard, Seed, Lines and Combo methods.
- **Drilling** and **milling** of holes and slots, with tool change, many depths per cut, exclusion areas and laser
  modes.
- **Cutout** of the board with bridges, thin gaps or mouse bites.
- **Double-sided boards**: mirror about an axis and make alignment drills.
- **Panelization** of Gerber, Excellon and Geometry objects.
- **Autolevelling** (Levelling plugin): probe the board surface and correct the Z of a CNC job.
- **Board preparation tools**: copper thieving, robber bar, fiducials, markers, QR codes, punched pads, inverted
  Gerbers, etch compensation, film output for photo processes, design rule check.
- **3 built-in editors** for Gerber, Excellon and Geometry objects, and a G-code editor.
- **Path optimization** of the drill and mill paths (Rtree, Google OR-Tools, travelling salesman).
- **27 G-code preprocessors**: GRBL, Marlin, Repetier, MACH3-compatible controllers, Roland, ISEL, Berta, NCCAD,
  HPGL, lasers and solder paste dispensers. You can add your own.
- **Tcl shell** with 75 commands, script files and command-line options for automatic jobs.
- **Tools Database** for your tools and their cutting parameters.
- **Light and dark themes**, a fast **OpenGL (VisPy)** canvas, and
  translations in 9 languages.

---

## Screenshots

### Main Application

<img src="assets/images/app.png" width="850" alt="FlatCAM Evo Main Interface">

### PCB Preview

<img src="assets/images/pcb_preview.png" width="850" alt="PCB Gerber Viewer">

### PCB Isolation / Engraving

<img src="assets/images/isolation.png" width="850" alt="PCB Isolation / Engraving">

### PCB Non Copper Clearing

<img src="assets/images/ncc.png" width="850" alt="PCB Non Copper Clearing">
<img src="assets/images/ncc_gcode.png" width="850" alt="PCB Non Copper Clearing - Result">

### PCB Paint a Gerber

<img src="assets/images/paint_cncjob.png" width="850" alt="PCB Paint a Gerber object">

---

## Installation

FlatCAM Evo runs from the source code on **Windows**, **Linux** and **macOS**.

### Requirements

- **Python 3.10 or newer**. Use a 64-bit Python: the OR-Tools path optimization works only on a 64-bit Python.
  - **Windows** has no Python by default. Install it from [python.org](https://www.python.org/downloads/windows/)
    (select "Add python.exe to PATH"), or use conda / mamba as shown below.
  - **Linux**: most current distributions have Python 3.10 or newer (for example Ubuntu 22.04 and newer, Debian 12
    and newer, Fedora). Check with `python3 --version`.
  - **macOS**: install Python with Homebrew (`brew install python`) or from python.org.
- A working **OpenGL** context and graphics driver for the **VisPy** canvas. The Legacy (Matplotlib) backend has
  been removed. Ordinary 2D PCB editing is unchanged, and the separate 3D view is retained.
- The Python packages in [`requirements.txt`](requirements.txt). The main packages are PyQt6, Shapely 2, NumPy,
  Rtree, VisPy, ezdxf, svglib, reportlab and lxml.

| Dependency | Current requirement |
|---|---|
| `ezdxf` | `>=1.4.5`; DXF text uses the fontTools-based path, without Matplotlib. |
| Matplotlib | No longer required; the Legacy canvas and vendored `descartes` have been removed. |

Some packages are optional. FlatCAM Evo runs without them, but the related feature is not available:

| Package | Feature |
|---|---|
| `ortools` | The MetaHeuristic and Basic path optimization of drill and mill paths. Works only on a 64-bit Python. |
| `rasterio` (with GDAL) | The Image import plugin. |
| `quickjs-ng` | The tracing mode of the Image import plugin (it runs `imagetracer.js` in an embedded QuickJS, no browser needed). Intel Macs use the older `quickjs` package instead, because `quickjs-ng` has no wheel for them. |
| `pikepdf` | An alternative PDF parser (the built-in parser is the default). |
| `pyclipr` | Faster fusion of the Gerber geometry when a file is opened. See [`requirements-optional.txt`](requirements-optional.txt). |

### 1. Get the source code

```bash
git clone https://github.com/MariusAdrian88/FlatCAM_Evo.git
cd FlatCAM_Evo
```

This gives the stable branch `main`. For the newest development code, run `git checkout 1.0`.

### 2a. Install with conda / mamba (recommended, all systems)

Conda installs the packages that need system libraries (GDAL, GEOS, libspatialindex, FreeType) for you.

1. Install [Miniforge](https://conda-forge.org/download/) (it includes `mamba`).
2. Create the environment from [`environment.yml`](environment.yml):

   ```bash
   mamba env create -f environment.yml
   ```

3. Activate the environment:

   ```bash
   mamba activate flatcam
   ```

### 2b. Install with pip and a virtual environment

1. Create and activate a virtual environment in the project folder:

   ```bash
   python -m venv .venv
   ```

   - Windows (PowerShell): `.venv\Scripts\Activate.ps1`
   - Linux / macOS: `source .venv/bin/activate`

2. Install the packages:

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   python -m pip install -r requirements-optional.txt   # optional
   ```

3. If `pip` cannot install `gdal`:
   - **Windows**: install a GDAL wheel for your Python version from
     [cgohlke/geospatial-wheels](https://github.com/cgohlke/geospatial-wheels), then run the `requirements.txt`
     command again.
   - **Linux / macOS**: install the GDAL system library first (see below), or use the conda installation.

#### Linux (Debian / Ubuntu): the setup script

[`setup_ubuntu.sh`](setup_ubuntu.sh) does all the steps of the pip installation for you:

```bash
./setup_ubuntu.sh
.venv/bin/python flatcam.py
```

The script installs the system libraries with `apt` (it asks for the sudo password), makes the `.venv` virtual
environment in the project folder, and installs the Python packages into it. It also installs the GDAL Python package
with the same version as the GDAL system library. Set `PYTHON=python3.12` (for example) before the command to use
another Python.

To do the same steps by hand, install these system packages before the `pip` step:

```bash
sudo apt install python3-venv python3-dev build-essential \
     libgeos-dev libspatialindex-dev libfreetype6-dev gdal-bin libgdal-dev \
     libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0
python -m pip install "gdal==$(gdal-config --version)"   # in the virtual environment
```

Use a virtual environment as shown above. Newer Debian and Ubuntu releases refuse a system-wide `pip install`.

After the installation, `make install` adds FlatCAM Evo to the applications menu of your user (`sudo make install`
installs it for all users in `/usr/share/flatcam-beta`). `make remove` removes it.

#### System packages for macOS

Install [Homebrew](https://brew.sh), then:

```bash
brew install python geos spatialindex freetype gdal
```

### 3. Start FlatCAM Evo

From the project folder, with the environment active:

```bash
python flatcam.py
```

You can give files on the command line, for example `python flatcam.py board.FlatPrj` or
`python flatcam.py top.gbr drills.drl`. See [Command-line options](#command-line-options).

#### OpenGL startup troubleshooting

If startup reports **OpenGL initialization failed**, open the error details and check `log.txt` in the
[data folder](#where-flatcam-evo-keeps-its-data) for the traceback.

- Install or update the graphics driver from your GPU vendor or operating system, then restart FlatCAM Evo.
- For remote desktop sessions or virtual machines, check that the session exposes OpenGL. Enable the VM's graphics
  acceleration and install its guest graphics drivers where supported. Try a local desktop session to isolate a
  remote graphics problem.
- If it still fails, report the error details and log with your OS, GPU, driver version and remote/VM setup in the
  [issue tracker](https://github.com/MariusAdrian88/FlatCAM_Evo/issues).

There is no Legacy renderer selection, Ctrl-startup fallback or renderer title badge. If your environment cannot
provide working OpenGL, use a previous release that includes the Legacy backend from the repository's
[Releases/downloads](https://github.com/MariusAdrian88/FlatCAM_Evo/releases).

### Update

```bash
git pull
python -m pip install -r requirements.txt   # or: mamba env update -f environment.yml
```

### Where FlatCAM Evo keeps its data

The program keeps the preferences (`current_defaults.FlatConfig`), the Tools Database, the recent files lists, the
log file (`log.txt`) and your own preprocessors (`preprocessors/` folder) in a data folder:

| System | Data folder |
|---|---|
| Windows | `%APPDATA%\FlatCAM` |
| Linux, macOS | `~/.FlatCAM` |

**Portable mode (Windows)**: set `portable=True` in `config/configuration.txt`. The program then keeps its data in a
`config` folder next to the program folder, for example on a USB drive.

The window layout and the style are kept in the Qt settings (`Open Source / FlatCAM_EVO`).

---

## Quick start: isolate and drill a board

1. **File > Open > Open Gerber** (`Ctrl+G`): open the top copper layer.
2. **File > Open > Open Excellon** (`Ctrl+E`): open the drill file.
3. Select the Gerber object and open **Plugins > Isolation**. Add a tool (for example a 0.1 mm V-shape tool), set the
   number of passes and the overlap, then click **Generate Geometry**.
4. Select the new isolation Geometry and open **Plugins > Milling**. Set the cut depth, the travel height, the feed
   rates and the preprocessor of your machine, then click **Generate CNCJob object**.
5. Select the Excellon object and open **Plugins > Drilling** (`Alt+D`). Select the tools, set the drill parameters, and
   click **Generate CNCJob object**. Turn on **Tool change** if your machine must stop to change the drill bit
   between tools. If it is off, all holes are drilled with the first tool.
6. Select each CNCJob object and click **Save** in its Properties to write the G-code file.
7. **File > Save Project** (`Ctrl+S`) keeps all objects and settings in 1 `.FlatPrj` file.

Press **F3** in the program to see the full list of keyboard shortcuts.

---

## Object types

All the data of a project are **objects**. The **Project** tab lists them. The **Properties** tab shows the settings
of the selected object. Every object has a Plot toggle, scale and offset fields, and a button for the Transformation
plugin.

| Object | Content | What you can do in its Properties |
|---|---|---|
| **Gerber** | Copper, mask, silkscreen or outline layer, with its apertures | Show it solid or as outlines, in 1 color or in many colors; mark apertures on the canvas; open the Gerber Editor; buffer the geometry; make the non-copper regions or a bounding box as a Geometry; start Isolation, Cutout, NCC, Film or Follow. |
| **Excellon** | Drills and slots, grouped by tool diameter | See the tools, the drill and slot counts; show or hide each tool; open the Excellon Editor; start Drilling or Milling; make a Geometry that mills the drills or the slots with a smaller tool. |
| **Geometry** | Toolpaths and shapes (lines and polygons), with a tool table | Single-geometry or multi-geometry (1 set of paths for each tool); open the Geometry Editor; simplify the paths; start Milling, Paint or NCC. |
| **CNCJob** | Machine code made from a Geometry or an Excellon | See the paths of each tool, the travel distance and the estimated time; show annotations; edit the G-code; add code snippets; start Levelling; save the G-code. |
| **Script** | A Tcl script | Edit the script with auto-complete and run it. |
| **Document** | A text document | Edit the text with font, color and alignment settings. The Check Rules plugin writes its report into a Document. |

The **Edit** menu converts and combines objects: convert single-geometry to multi-geometry and back, convert any
object to a Geometry, Gerber or Excellon object, join Gerber, Excellon or Geometry objects, copy, delete, set the
origin, move to the origin and jump to a location.

---

## Plugins

Plugins open in the **Plugin** tab of the side panel. Most of them are in the **Plugins** menu. Some are in the
**Edit**, **Options** or **File > Import** menu, as shown below. Many plugins also have a button in the Properties
of the object that they use.

### Toolpath generation

- **Isolation** — Makes isolation routing paths around the copper features of a Gerber object, so that the tracks
  and pads are electrically separated from the copper around them. The tool table holds 1 or more tools (from the
  Tools Database or typed in), with the tool shape (also V-shape with tip diameter and angle), the cut depth, the
  number of passes, extra passes around the pads, the overlap, the milling direction (climb or conventional) and the
  isolation type (full, exterior only or interior only). Options: rest machining (each next tool cuts only where the
  previous tool could not go), combine the passes into 1 Geometry, check the validity of the result, simplify the
  paths, exclude the area of another object, and isolate only a part of the board (all, an area, selected polygons,
  or the area of a reference object). Output: a Geometry object.
- **NCC (Non-Copper Clearing)** — Removes all the copper that is not part of a track or a pad, for example to
  reduce the etch time or to make a board with large insulated areas. Input: a Gerber or Geometry object. It uses a
  tool table with many tools and rest machining, and the methods Standard, Seed and Lines. Options: connect the
  paths, cut a contour around each area, offset from the copper, check the validity, and clear the whole object, an
  area or the area of a reference object. Output: a Geometry object.
- **Paint** — Fills polygons of a Geometry object with toolpaths, for example to remove copper from a large area or
  to engrave a filled shape. Methods: Standard, Seed, Lines and Combo. Options: connect, contour and rest machining.
  You can paint all polygons, selected polygons, an area or the area of a reference object. Output: a Geometry
  object.
- **Follow** — Makes a Geometry that follows the center lines of the Gerber tracks, for example for a pen plotter or
  for traces cut with a 1-pass tool. Options: simplify, union, and all or an area. Output: a Geometry object.
- **Cutout** — Cuts the board out of the stock along its outline. Input: a Gerber or Geometry object, as 1 board or
  a panel. Parameters: tool diameter, cut depth, many depths per cut, margin, and a convex or rectangular shape. The
  gaps (tabs) that hold the board can be bridges, thin gaps with their own depth, or mouse bites (a row of drills).
  You can place the gaps automatically or by mouse clicks, and draw a manual cutout. "Cut by drilling" makes a row
  of drills along a line. Outputs: a Geometry object, and an Excellon object for mouse bites.
- **Milling** — Makes a CNCJob from a Geometry object, or mills the holes and slots of an Excellon object that are
  too large for a drill bit. Tool shapes: C1 to C4, ball, V-shape and L. Offset types: on the path, inside, outside
  or custom. Jobs: roughing, finishing, isolation and polishing. Parameters: cut depth, many depths per cut, travel
  height, X-Y and Z feed rates, rapid moves, an extra cut at the path end, spindle speed or laser power, dwell, tool
  change, start and end heights, preprocessor, and exclusion areas. Output: a CNCJob object.
- **Drilling** — Makes a CNCJob that drills the holes of an Excellon object. Each tool has its own cut depth, many
  depths per cut, travel height, feed rates, spindle speed or laser power, dwell and Z offset. Slots can be drilled
  as a row of holes with an overlap. Common parameters: tool change on or off with its Z and X-Y position, start
  and end heights, probe depth, preprocessor, path optimization and exclusion areas (move over them or around them).
  Output: a CNCJob object.
- **SolderPaste** — Makes the paths and the G-code for a solder paste dispenser from the paste mask Gerber. It uses
  a table of nozzles, with the dispense heights, the feed rates, the forward and reverse speeds of the dispenser and
  the dwells. Outputs: a Geometry object and a CNCJob object (preprocessors `Paste_*`).
- **Levelling** (Options > Experimental, or the Levelling button of a CNCJob) — Corrects the Z of a CNCJob for a
  board that is not flat. You define the probe points manually or as a grid (with an option to avoid the drill
  holes), set the probe parameters, and select the interpolation (Voronoi or bilinear). It can talk to a GRBL
  controller on a serial port (jog, send commands, read the height map), save a probing G-code for MACH3, MACH4,
  LinuxCNC or GRBL, or import a height map. Output: a levelled CNCJob object.

### Double-sided boards and panels

- **2-Sided** — Mirrors an object for the bottom side of a double-sided board. The mirror axis goes through a point,
  the center of a box object, or a hole that you pick on the canvas. It shows the bounds and the center of the
  selection, and it makes an Excellon object with alignment drills for the 2 sides of the board.
- **Align Objects** — Moves an object so that 1 or 2 points on it fall on 1 or 2 points of another object, for
  example to align a drill file to its copper layer.
- **Panelize PCB** — Makes a panel: a grid of copies of a Gerber, Excellon or Geometry object, with a number of
  columns and rows and the spacing between them. The copy size comes from the object or from the bounding box of
  another object. The panel can be limited to a maximum width and height (it then has fewer copies). A Gerber or
  Geometry source can make a Gerber panel or a Geometry panel. Path optimization fuses the overlapped cutout paths.
  Output: an object of the selected type.

### Board preparation

- **Copper Thieving** — Fills the empty areas of a copper layer with a pattern (solid, dots, squares or lines), to
  make the plating more uniform. It can also add a robber bar around the board and make a pattern plating mask.
  Outputs: Gerber objects.
- **Fiducials** — Adds fiducial marks (circle, cross or chess pattern) for pick-and-place machines and for
  alignment, automatically at the board corners or at the positions that you click or type. It also makes the
  solder mask openings for them. Outputs: Gerber objects.
- **Markers** — Adds corner markers (L shapes) at the board corners, as Gerber or Geometry, and optionally as drills.
  Useful to align a board on the machine.
- **Extract** — Makes new objects from the pads of a Gerber object: an Excellon with a drill in each pad (fixed
  diameter, proportional to the pad, or a fixed annular ring), a solder mask Gerber with a clearance, or a cutout
  Gerber. You can select the pad types (circular, oblong, square, rectangular, other).
- **Punch Gerber** — Makes a hole in the center of the pads of a Gerber object, as a guide for a manual drill. The
  hole comes from an Excellon object, or has a fixed diameter, a size proportional to the pad, or a fixed annular
  ring. Output: a Gerber object.
- **Invert Gerber** — Inverts a Gerber object inside its bounding box with a margin: copper becomes empty and empty
  becomes copper. Output: a Gerber object.
- **Etch Compensation** — Makes the tracks of a Gerber object wider, so that they have the correct width after a
  chemical etch. The offset comes from the copper thickness and an etch factor, from a list of etchants, or is set
  manually. It includes oz-to-micron and mil-to-micron converters. Output: a Gerber object.
- **QRCode** — Adds a QR code with your text or link to a Gerber object, with the version, the error correction,
  the box size, the border and the polarity. It can also export the QR code as SVG or PNG.
- **Film** — Makes a film for a photo process (UV exposure) from a Gerber or Geometry object: positive or negative,
  with a border, scale, skew and mirror corrections and punched holes for the pads. It exports SVG, PNG or PDF.
- **Subtract** — Subtracts a Gerber object from another Gerber object, or a Geometry object from another Geometry
  object. Output: a new object.

### Measurement and analysis

- **Distance** (Edit menu, `Ctrl+M`) — Measures between 2 or more points that you click, with snap to the pad and
  drill centers. It shows dX, dY, the angle and the distance.
- **Object Distance** (Edit menu, `Shift+M`) — Measures the distance between 2 objects, between their nearest points
  or their centers, and can jump to the point halfway between them.
- **Find Optimal** — Finds the minimum distance between the copper features of a Gerber object, and the other
  distances with how often they occur. Use it to select the largest isolation tool that can cut between all tracks.
- **Check Rules** — A design rule check of the Gerber and Excellon layers of a board: trace width, copper to copper
  clearance, copper to outline clearance, minimum annular ring, silkscreen to silkscreen, silkscreen to solder mask,
  silkscreen to outline, minimum solder mask sliver, hole to hole clearance and hole size. The report is a Document
  object.
- **Calculators** — A unit converter (mm/inch and more), a V-shape tool calculator (the cut width from the tip
  diameter, the angle and the cut depth), an electroplating calculator (current and time) and a tinning solution
  calculator.
- **Object Report** (Options menu, `P`) — Shows all the properties of an object in a tree: type, dimensions, units,
  options, apertures, tools, drills and slots.

### Transform and move

- **Transformation** (Options menu, `Alt+T`) — Rotates, skews, scales, mirrors, offsets and buffers the selected
  objects, about the origin, the selection, a point or another object. A Gerber keeps its aperture sizes in
  agreement with the new geometry where this is possible.
- **Move** (Edit menu, `M`) — Moves the selected objects with 2 clicks: a start point and an end point.

### Import plugins (File > Import)

- **PDF Import** — Imports the drawings of a PDF file (for example a PCB printout) as Gerber objects, and the drill
  marks as an Excellon object.
- **Image Import** — Imports a raster image (BMP, PNG, JPG) as a Gerber or Geometry object, with the DPI and a black
  and white or color mask, or traces it as vectors with presets. Needs `rasterio`.
- **PcbWizard Import** — Imports an Excellon file of PCB Wizard together with its `.INF` file, which holds the
  number format and the units.

### Command line

- **Command Line** (Plugins menu, `S`) — Opens the Tcl shell. See [Tcl scripting and automation](#tcl-scripting-and-automation).

---

## Editors

Select an object and click the editor button in its Properties, or use **Edit > Edit Object** (`E`). Click the
button again to save the changes into the object. An editor menu appears in the menu bar while an editor is open.

- **Gerber Editor** — Pad, pad array, track and region tools; polygonize, semi-disc and disc shapes; buffer,
  simplify, scale, mark an area, eraser and transformation; select, move and copy. New pads and tracks use the
  aperture that you select in the aperture table. An object with clear (negative) features is loaded as its final
  geometry.
- **Excellon Editor** — Drill, drill array, slot and slot array tools; resize drills; copy, move and delete.
  Each tool works with the diameter that you select in the tool table.
- **Geometry Editor** — Circle, arc, rectangle, polygon, path and text tools; union, intersection, subtraction and
  cut path operations; buffer, simplify, paint and transformation; copy, move and delete; snap to the grid and to
  corners.
- **G-code Editor** — Opens the G-code of a CNCJob object as text, so that you can change it before you save it.

---

## File formats

### Open and import

| Format | Menu | Object |
|---|---|---|
| Gerber (`.gbr`, `.gtl`, `.gbl`, `.gts`, `.gto`, `.gko`, `.cmp`, `.sol`, `.art`, `.pho` and many more) | File > Open > Open Gerber | Gerber |
| Excellon (`.drl`, `.txt`, `.xln`, `.drd`, `.tap`, `.exc`, `.ncd`) | File > Open > Open Excellon | Excellon |
| G-code (`.nc`, `.gcode`, `.cnc`, `.tap` and more) | File > Open > Open G-Code | CNCJob |
| FlatCAM project (`.FlatPrj`) | File > Open > Open Project | all objects |
| Tcl script (`.FlatScript`, `.tcl`, `.txt`) | File > Scripting | Script |
| SVG | File > Import | Geometry or Gerber |
| DXF | File > Import | Geometry or Gerber |
| HPGL2 (`.plt`) | File > Import | Geometry |
| PDF | File > Import | Gerber and Excellon |
| Image (BMP, PNG, JPG) | File > Import | Gerber or Geometry |
| PCB Wizard Excellon + `.INF` | File > Import | Excellon |
| Altium PCB (`.PcbDoc`) | File > Import | Gerber and Excellon objects |
| KiCad PCB (`.kicad_pcb`) | File > Import | Gerber and Excellon objects |
| Eagle board (`.brd`, XML) | File > Import | Gerber and Excellon objects |
| OrCAD/Allegro board (`.brd`) | File > Import | Gerber and Excellon objects |
| Sprint Layout 6 (`.lay6`) | File > Import | Gerber and Excellon objects |

The board file imports show a dialog where you select the layers and drill groups. OrCAD/Allegro boards saved by
Allegro 17.2 and 24.1 are fully checked. Boards from 17.4 to 19.0 are read with a warning. Boards from 16.x are
refused: save them again in Allegro 17.2 or newer.

You can also drag and drop files on the window. The file extensions that the program opens as Gerber, Excellon or
G-code are set in **Preferences > Utilities** (file associations).

### Save and export

| Format | Menu |
|---|---|
| G-code | Save button of a CNCJob object; Tcl `write_gcode` |
| Gerber | File > Export > Export Gerber (format and units from Preferences > Gerber > Export) |
| Excellon | File > Export > Export Excellon (format, zeros, units and slot type from Preferences > Excellon > Export) |
| SVG, DXF | File > Export |
| PNG (image of the canvas) | File > Export > Export PNG |
| PDF | File > Print (PDF) (`Ctrl+P`) |
| Film (SVG, PNG, PDF) | Film plugin |
| Project (`.FlatPrj`) | File > Save Project |
| Preferences (`.FlatConfig`) | File > Backup |

The Gerber export writes the apertures of the object when it can. If the object has aperture macros, polygon
apertures, clear (negative) features, or a transform that the apertures cannot follow, the export writes the final
geometry as regions, with all holes and islands kept.

---

## G-code preprocessors

A preprocessor converts the toolpaths into the G-code dialect of your machine. Select it in the Drilling, Milling or
SolderPaste plugin.

| Preprocessor | Machine / controller |
|---|---|
| `default` | MACH3-compatible controllers, with `M6` tool change |
| `Default_no_M6` | MACH3-compatible, without `M6` |
| `GRBL_11` | GRBL 1.1 (and most other GRBL versions) |
| `GRBL_11_no_M6` | GRBL, without `M6` |
| `Marlin` | Marlin firmware |
| `Repetier` | Repetier firmware |
| `Toolchange_Manual` | Manual tool change: the machine stops (`M0`) for the change |
| `Toolchange_Probe_MACH3` | MACH3, with a Z probe after each tool change |
| `Line_xyz` | X, Y and Z on the same line |
| `Check_points` | Moves to the points and stops, to check the position of the board (no cutting) |
| `Berta_CNC` | Berta CNC router |
| `ISEL_CNC`, `ISEL_ICP_CNC` | ISEL CNC routers |
| `NCCAD9` | Max Computer nccad9 (KOSY), `.knc` files |
| `Roland_MDX_20`, `Roland_MDX_540` | Roland MDX mills (RML-1) |
| `hpgl` | HPGL plotters and cutters |
| `default_laser` | Lasers on MACH3-compatible controllers |
| `GRBL_laser`, `GRBL_laser_z` | Lasers on GRBL (`_z` moves Z on tool change, to change the spot size) |
| `grbl_laser_eleks_drd` | GRBL lasers without Z: drills become circles |
| `Marlin_laser_FAN_pin`, `Marlin_laser_Spindle_pin`, `Marlin_laser_z` | Lasers on Marlin |
| `Paste_1`, `Paste_GRBL`, `Paste_Marlin` | Solder paste dispensers (SolderPaste plugin only) |

To add your own preprocessor, copy a file from the [`preprocessors`](preprocessors) folder into the `preprocessors`
folder of the [data folder](#where-flatcam-evo-keeps-its-data), change the class name and the G-code, and start the
program again. The Tcl command `list_pp` lists all preprocessors.

---

## Path optimization

The order of the drill holes and of the mill paths changes the machining time a lot. Select the method in
**Preferences > Excellon** (drilling) and **Preferences > Geometry** (milling):

- **Rtree** — fast, nearest neighbor with a spatial index.
- **MetaHeuristic** — Google OR-Tools with Guided Local Search. It searches for the "Duration" time.
- **Basic** — Google OR-Tools, first solution and local search, also limited by the "Duration" time. Default for
  drilling.
- **TSA** — a travelling salesman algorithm.
- **None** — the paths stay in their order (milling only).

The "Duration" preference (seconds, 0 means 3 s) limits MetaHeuristic and Basic. A longer time can give a shorter
path. MetaHeuristic and Basic need the `ortools` package and a 64-bit Python.

---

## Tcl scripting and automation

FlatCAM Evo has a **Tcl shell** that runs the same operations as the user interface. You can type commands in the
shell, run script files, and start the program with a script for automatic jobs.

### The Tcl shell

Open it with **Plugins > Command Line**, the `S` key, or the Command Line button of the toolbar. Type `help` for the
list of commands and `help <command>` for the arguments of 1 command:

```tcl
help isolate
```

Each command takes the object name and options in the form `-option value`. The result goes to the shell. Tcl
variables, loops and procedures work as in any Tcl interpreter.

### Script files

- **File > Scripting > New Script** makes a Script object that you edit in the program (with auto-complete).
- **File > Scripting > Open Script** opens a `.FlatScript`, `.tcl` or `.txt` file as a Script object.
- **File > Scripting > Run Script** (`Shift+S`) runs a script file.
- **File > Scripting > Open Example** opens the examples in [`assets/examples`](assets/examples).

Example: isolate a Gerber and drill an Excellon file, then save the G-code.

```tcl
set_path "C:/pcb/job1"
open_gerber top.gbr -outname top
open_excellon drills.drl -outname drills

# isolation: 0.1 mm tool, 2 passes, 50 % overlap, 1 Geometry for all passes
isolate top -dia 0.1 -passes 2 -overlap 50 -combine True -outname top_iso

# mill the isolation paths
cncjob top_iso -dia 0.1 -z_cut -0.05 -z_move 2 -feedrate 120 -spindlespeed 12000 -pp GRBL_11 -outname top_iso_cnc
write_gcode top_iso_cnc "C:/pcb/job1/top_iso.nc"

# drill all holes
drillcncjob drills -drilled_dias all -drillz -1.8 -travelz 2 -feedrate_z 60 -toolchangez 15 -pp GRBL_11 -outname drills_cnc
write_gcode drills_cnc "C:/pcb/job1/drills.nc"

plot_all
```

Use `help <command>` in the shell for the exact option names and their defaults.

### Command-line options

```bash
python flatcam.py [files...] [--shellfile=<script>] [--shellvar=<value1,value2,...>]
```

| Option | Meaning |
|---|---|
| `files` | Opens each file by its extension: `.FlatPrj` (project), `.FlatScript` / `.tcl` (runs the script), `.FlatConfig`, and the Gerber, Excellon and G-code extensions of the file associations (Preferences > Utilities). Put a path with spaces between quotes. |
| `--shellfile=<path>` | Runs the Tcl script file after the program starts. |
| `--shellvar=<a,b,c>` | Defines the Tcl variables `shellvar_0`, `shellvar_1`, ... with these values, for use in the script. |

On Windows, a second start of the program sends its file arguments to the program that already runs.

End a script with `quit_app` to close the program when the script is done.

### Tcl command reference

| Area | Commands |
|---|---|
| Projects and files | `new`, `open_project`, `save_project`, `open_gerber`, `open_excellon`, `open_gcode`, `open_svg`, `open_dxf`, `import_svg`, `open_folder`, `get_path`, `set_path` |
| Export | `export_grb`, `export_exc`, `export_svg`, `export_dxf`, `export_gcode`, `write_gcode` |
| Objects | `new_gerber`, `new_excellon`, `new_geometry`, `delete`, `get_names`, `get_active`, `set_active`, `options`, `get_bounds`, `plot_all`, `plot_objects` |
| Create geometry | `add_aperture`, `add_drill`, `add_slot`, `add_circle`, `add_rect`, `add_polygon`, `add_polyline`, `subtract_poly`, `subtract_rectangle`, `bounding_box`, `exteriors`, `interiors`, `non_copper_regions` |
| Transform | `offset`, `scale`, `mirror`, `skew`, `buffer`, `set_origin` |
| Combine and split | `join_gerber`, `join_excellon`, `join_geometries`, `geo_union`, `split_geometries`, `panelize` |
| Toolpaths | `isolate`, `ncc_clear`, `paint`, `follow`, `cutout`, `geocutout`, `milldrills`, `millslots`, `aligndrill`, `aligndrillgrid` |
| CNC jobs | `cncjob`, `drillcncjob`, `list_pp` |
| Settings and program | `get_sys`, `set_sys`, `list_sys`, `save_sys`, `help`, `version`, `clear`, `quit_app` |

There are 75 commands. Some have short aliases, for example `bbox`, `ncc`, `pan`, `ee`, `egr` and `geoc`.

---

## Preferences, Tools Database, themes and languages

- **Preferences** (`Shift+P`): the settings of the program and the default parameters of each object type and
  plugin, in the tabs General, Gerber, Excellon, Geometry, CNC Job, Engraving, Processing, Extra Plugins and
  Utilities. **File > Backup** exports, imports and saves the preferences.
- **Tools Database** (Options > Tools Database, `Ctrl+D`): your tools with their diameter, tolerance, shape and
  cutting parameters, and the settings for Isolation, NCC, Paint, Drilling, Milling and Cutout. The plugins can pick a
  tool from the database, or find the tool that fits a diameter. The database is exported and imported as a text
  file.
- **Rendering**: VisPy is the only PCB canvas backend. The retired `global_graphic_engine` preference is discarded
  automatically when loading or importing settings and when accepting project settings; subsequent saves omit it.
  No manual preference reset is needed.
- **Disable parallel shape processing** (Preferences > General): processes shapes synchronously instead of using
  parallel workers. The compatibility key `global_graphic_engine_3d_no_mp` is preserved; this checkbox does not
  select another renderer and does not remove the OpenGL requirement.
- **Themes**: default, automatic (follows the system), light and dark, with an optional dark canvas.
- **Languages**: English, Brazilian Portuguese, Chinese (Simplified), French, German, Italian, Romanian, Russian,
  Spanish and Turkish.
- **Units**: millimeters or inches for the whole program (Preferences > General, or the `Q` key). A file in other
  units is converted when it is opened.

---

## For developers

- Entry point: [`flatcam.py`](flatcam.py). The main class `App` is in [`appMain.py`](appMain.py).
- Core geometry and CNC code: [`camlib.py`](camlib.py) (classes `Geometry`, `CNCjob`).
- File parsers: [`appParsers`](appParsers) (Gerber, Excellon, DXF, SVG, PDF, HPGL2, fonts, PCB board formats).
- Project objects: [`appObjects`](appObjects). Plugins: [`appPlugins`](appPlugins). Editors: [`appEditors`](appEditors).
- Tcl commands: [`tclCommands`](tclCommands). Preprocessors: [`preprocessors`](preprocessors).
- User interface: [`appGUI`](appGUI). File input and output: [`appHandlers/appIO.py`](appHandlers/appIO.py).

### Run the tests

The tests use `pytest` and run without a display:

```bash
python -m pip install pytest
python -m pytest tests -q
```

[`tests/README_gates.md`](tests/README_gates.md) describes the test areas and the faster subsets.

---

## Reporting bugs and contributing

- Report bugs and ask for features in the [GitHub issues](https://github.com/MariusAdrian88/FlatCAM_Evo/issues).
  Attach the files (Gerber, Excellon, project) and the log (`log.txt` in the [data folder](#where-flatcam-evo-keeps-its-data))
  when you can.
- Pull requests are welcome. Make them against the development branch `1.0`, and add tests for the changes.
- Translations are in the [`locale`](locale) folder (`strings.po` files).

---

## License and credits

FlatCAM Evo is released under the [MIT License](LICENSE).

- Copyright (c) 2018-2026 Marius Stanciu — FlatCAM Evo
- Copyright (c) 2014-2018 Juan Pablo Caram — FlatCAM

Many contributors and translators helped FlatCAM and FlatCAM Evo. The list is in the program, in
**Help > About**. If you are not in the list and you have contributed, please let me know.
The icons come from Freepik, Pixel perfect, Anggara and Kharisma (flaticon.com), Icons8
and oNline Web Fonts.
