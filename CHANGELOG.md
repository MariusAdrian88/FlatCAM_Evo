FlatCAM Evo (c) 2016-2026 (former FlatCAM beta) by Marius Stanciu

Based on FlatCAM: 
2D Computer-Aided PCB Manufacturing by (c) 2014-2016 Juan Pablo Caram
=================================================

CHANGELOG for FlatCAM Evo

26.10.2026

Proteus PCB import:

- new: File -> Import -> Proteus PCB (.pdsprj); native Proteus 8 and 9 projects are read without Proteus or a prior Gerber export
- each embedded board has its own layer and drill selection and is imported into a separate, named project; the next board waits for object creation, insertion and plotting to finish
- native pads, vias, routes, board cutouts, saved copper pours, holes, islands, component graphics and supported reference text keep their board coordinates and layer identities
- identified manufacturing layers are selected by default and use their Gerber extensions in object names, source saves and exports; unknown artwork is kept separate and is not selected by default
- malformed projects fail without publishing partially decoded boards; unsupported records and uncertain layer data produce warnings; Cancel, Stop and export failures stop the remaining boards while keeping projects already imported

PDF import:

- the pure-Python parser reads compressed object streams (PDF 1.5+); both PDF backends expand nested Form XObjects with their transforms and resources and use line widths from ExtGState
- new: embedded TrueType and supported Type0/CID text is converted to glyph outlines; unsupported fonts are reported instead of silently losing text
- repeated colors return to their original layer instead of creating duplicate layers; white clears and later islands keep their paint order across the color layers
- repeated equal solids are removed unless an intervening clear requires the later copy to restore material; empty Gerber layers and empty drill groups no longer create objects
- fixed: stray delimiters could leave the tokenizer in an endless loop; text-heavy fill processing reads ring coordinates once and skips rings that cannot affect the winding number

Gerber editor:

- new: Copper Pour in the editor menu and toolbar, with a polygon outline and clearance from existing copper; supports vertex editing, cancellation, Undo / Redo and native Gerber export without changing the source features
- Track: new "Snap to pad" option snaps to the center of the pad under the mouse, also with grid snap off or an off-grid pad center; the preview and the committed endpoint use the same snap, and the option survives successive routes
- Track: hold Space for a grid ten times finer while routing; releasing Space restores the normal grid and refreshes the live preview
- rectangular and obround apertures now have separate Width and Height fields; both dimensions must be finite and positive
- fixed: aperture, feature and Select All table selections now respect the Zoom checkbox

Canvas and Jump:

- fixed: clicking after Jump could use the nearest screen pixel's coordinates instead of the requested point; a subpixel view adjustment now aligns the actual canvas position with the mouse pointer, including subsequent wheel zoom
- Jump keeps the current zoom level and centers the requested point instead of resetting the view to a 40 x 40 area; Locate also aligns its target with the cursor pixel
- opening, closing or resizing the left notebook keeps the grid and geometry at the same screen positions and scale; only the visible viewport changes, including the nested Plot Area tab layout

Projects:

- new: Import... in the project context menu (right-click on the project combobox) opens an Import dialog; the left side lists all the imports in groups (vector files, raster images, PCB boards) with a search box, the right side shows the file type, the created objects and a description; Import... runs the selected import with its own file dialog
- new: Close All Projects in the project context menu, after a separator below Close Project; each project with unsaved changes asks first, a Cancel or a failed save stops the remaining closes, and 1 empty project stays at the end
- new: Preferences -> General -> "Project Caching" (off by default); a switch hides the plot shapes of the inactive project instead of releasing them and keeps the plugin widgets, so a switch back does not plot again; a change of the object or the plot options plots the objects again, and closing a project or turning the option off releases the kept shapes

Proteus PCB import:

- new: the firmware source files of a project (embedded, or listed in FIRMWARE.XML and found beside the project file) open as Document objects with the first imported board; files outside the project folder and files larger than 4 MB are not read
- monochrome bitmap artwork is drawn from its set pixels; other bitmaps produce a warning
- unidentified native layers are imported as Mechanical N (GMN) and are not selected by default; only the PTH and NPTH drill groups are selected by default
- a board without a valid name gets the name of its file member; the system fonts are read once per session instead of once per board

Circle resolution:

- the native board imports (Altium, KiCad, Eagle, OrCAD/Allegro, Sprint Layout, Proteus) use the Gerber "Circle Steps" preference for the radius of each circle instead of a fixed 128 steps
- the SVG, DXF and Image (trace) imports (also the DXF POINT disks and filled circles) give each circle, arc and stroke the steps of its own radius instead of the steps of a typical 5 mm feature; the DXF import uses the preferences of the target object (Gerber or Geometry)

CNC Job:

- fixed: Edit -> Flip on X / Y axis, Rotate and Skew (also the X and Y keys) on a CNCJob changed the plot but not the saved G-code: Save CNC Code wrote the job as it was before; the saved code now follows the transform, also after Undo / Redo
- fixed: a CNCJob moved with the Move tool (M key) exported its G-code with the old coordinates; the exported code now has the moved ones, also after Undo / Redo
- fixed: an object name with parentheses (a Windows file name like "board (1)-F_Cu.gbr") ended the "(Name: ...)" comment of the G-code header early: GRBL, FluidNC and LinuxCNC read the rest of the line as G-code and stopped the job at about line 2; in a "( ... )" comment the name now has "[" and "]" (also in the probing G-code of the Levelling tool and in the Solder Paste G-code)

Tests:

- added Proteus 8 / 9 projects and CAM reference fixtures, PDF samples and regression checks for imports, copper pours, track snapping, aperture dimensions, Jump precision, zoom preservation and notebook resizing
- regression tests for the saved G-code of a CNCJob after Edit -> Flip, Rotate and Skew (tests/test_geometry_transforms.py)
- regression test for the exported G-code of a CNCJob moved with the Move tool (tests/test_move_tool.py)
- regression tests for an object name with parentheses in the header comments of the CNC job, probing and solder paste G-code (tests/test_cncjob_export_gcode.py, tests/test_levelling_controller_regressions.py, tests/test_solderpaste.py)

25.10.2026
RELEASE FlatCAM Evo 1.0

25.10.2026

Isolation:

- fixed: a run changed the tool table of the plugin: it removed the tools that were not selected and kept the paths of the last run in the table; a run now works on a copy of the selected tools
- fixed: a new object got the paths of tools from an earlier run (also of another Gerber object), and a CNC job from it cut these old paths
- fixed: the created objects shared the tool data with the plugin and with each other; a later change in the plugin changed the objects that were already made
- fixed: an empty selection (for example a Reference Object that covers all the copper) isolated the whole board; it now shows "No geometry to isolate."
- fixed: the extra pad passes went around all the pads of the board, also when only some polygons were isolated; a clear (LPC) flash crashed the combined run with pad passes
- fixed: in separate passes, the first pad ring was on the path of its own pass (a double cut) and each pass object had the rings again; the rings now start after the last pass and are only in the last object
- fixed: rest machining crashed after "Generate Buffer" and with a Reference Object; it now uses the selected tools (largest first) and continues when one tool fails
- fixed: rest machining did not give a pad with 1 hole to the next, smaller tool when the hole was too small for the tool
- fixed: the tool check and the isolation ran at the same time; the check now finishes first, in the same job
- fixed: the circle steps were used as segments for each quarter, so the arcs had 4 times the points
- fixed: after a tool was added, the table selected an other row, because the sort gave new numbers to the tools; the tools now keep their numbers
- fixed: the Tools Database insert selected the wrong row and did not merge the default values; the edit of a diameter compared only with the last tool
- fixed: Rest Machining stayed disabled after Area and then Polygon selection; the V-shape fields stayed hidden for a V tool; the "Selection" preference was not used
- fixed: the Beginner / Advanced switch overwrote the isolation type of each tool; the signals were connected again at each Reset
- fixed: in Polygon selection, a rectangle drag selected nothing; Select All added the polygons a second time; the first corner of a Square area was wrong without grid snap
- fixed: Esc and the right click did not reset the selection, so old polygons went into the next run; the grid snap was not always restored
- the Area and Polygon selections run the isolation in the worker, so the GUI does not freeze; the selection uses a spatial index and draws all the polygons in one batch (200 clicks on 20000 pads: 0.02 s)
- the widgets are read when Generate is clicked; the worker gets plain values
- the paths of a run are not kept in the tool table, so the table is faster to rebuild
- a running isolation can be stopped
- new: "Selected objects" isolates all the Gerber objects selected in the Project tab with the same tools
- the preference key tools_iso_tool_cutz is removed (it was not used)
- Tcl isolate: new options -milling_type, -pad_passes, -tool_shape, -vtipdia, -vtipangle, -cutz, -simplify, -except_obj and -rest (with a list of diameters); several Gerber objects as a comma separated list; the values are checked; the tool data of the result has the real diameter and the isolation job type
- Tcl isolate: the default name of an exterior or interior isolation is now <name>_ext_iso or <name>_int_iso; each separate pass has its own name
- fixed: a diameter that was rejected (it is the same as the diameter of another tool) still changed the data of the tool; the data are now applied only when the diameter is accepted
- fixed: a change of the V-tip diameter, the V-tip angle or the Cut Z updated the table before the new tool diameter was checked; it now goes through the check of the tool diameter
- fixed: a run used the tools of the table at the time the job started in the worker; the tools are now copied when Generate is clicked, so an edit of the table during the run does not change the run
- fixed: the traced outline of the isolation boundary went through the excluded area (Tcl -except_obj); the trace now leaves out the excluded area

Non-Copper Clearing:

- fixed: the tool table kept the geometry of an earlier run and a run wrote into the table; an empty boundary did not fail
- fixed: the V-tip values of the tools did not reach the output tools; the circle steps were used as segments for each quarter
- the tool check runs in the worker; the Tcl command uses the same code as the GUI
- new: Preview button, an Exclusion areas section, "All selected objects", a simplification preference
- Tcl ncc: new options -iso_tooldia, -milling_type, -final_cleanup, -simplification, -area, -exclude and -plot; the output names _ncc and _ncc_rm are fixed
- fixed: with "All selected objects", the preview showed only the first object; it now shows the area of all the selected objects
- fixed: the tool path near an exclusion area was clipped too close, so the tool cut into the exclusion area; the exclusion area is now grown by the tool radius, corrected for the number of circle segments

Paint:

- fixed: a job wrote paths into the tool table and removed tools from it; each tool now uses its own method unless the caller (Tcl) sets one
- the tool check tests that the tool fits inside the selected polygons
- new: Preview button, "Selected objects" (one job for each selected Geometry or Gerber object), a rest offset and a check preference
- Tcl paint: new options -rest, -rest_offset, -simplification, -final_cleanup, -ref and -area; a failure gives a Tcl error
- fixed: the preview stayed on the screen after a change of the offset, the rest offset, the tool selection, the rest machining, the reference object or the reference type; it is now cleared
- fixed: the check that the tools fit used the polygons before they were merged, but Generate paints the merged polygons; the check now uses the merged geometry
- fixed: a Reference Object made of closed lines (not polygons) gave an empty area; the closed outlines are now used as polygons (open paths are not)
- fixed: with "Selected objects", the object in the drop-down was checked first, and a missing object gave an error; the drop-down object is now used only without "Selected objects"

Follow:

- the plugin was made again: the work runs in the worker, one Undo removes the result, the names are unique, more than one object can be used, with a preview, an aperture list and Cancel
- new: Follow preferences (merge, selection, area shape, diameter mode, name suffix); the shortcut Alt+Y opens the plugin
- Tcl follow uses the same code as the GUI and has all the options

Extract:

- the drills, the soldermask and the cutout are made by one Qt-free core that the GUI and Tcl use; the work runs in the worker
- the cutout also uses "All selected Gerbers"
- new: Tcl command extract, and new preferences

Invert Gerber:

- fixed: the source object was changed; an empty or infinite bound crashed the plugin; the circle steps were used as segments for each quarter
- the inversion is one union and one difference; the work runs in the worker with Stop and one Undo step
- new: the source can be the object or a selection; the reference can be the object itself or an other object (Box or Exact); the margin is converted to the app units
- new: Tcl command invert_gerber, and new preferences
- fixed: the source, reference and area radio buttons had no checked button when the plugin opened; "Object", "Itself" and "Box" are now selected

Etch Compensation:

- fixed: a record whose clear area made it empty was exported as copper; the result did not keep the follow geometry, the aperture macros and the region export of the source
- fixed: only the C and P apertures grow by the offset; an empty result shows an error; Reset during a job is refused
- Tcl etch_compensate: new option -mode etch_list

Drilling:

- fixed: an Excellon object lost the values of its tools when its UI was made again
- fixed: a wrong number in the toolchange X,Y gave a crash; it now gives an error
- fixed: the drill travels went around the exclusion areas also when the tool did not use them
- the tool table keeps the selection when it is made again for the same object
- new: "Probe plate thickness" in the plugin (shown for the preprocessors with probing, next to the probe depth) and in Preferences > Tools > Drilling (advanced, default 0.0); it is the thickness of the touch plate: after the probing the tool tip is set to this height, so Z = 0 stays the top of the material
- new: Tcl drillcncjob option -pplate (the probe plate thickness; the default is the value of each tool)

Milling:

- fixed: the form values were applied in the wrong order of level and preprocessor; the tool label showed the wrong column; the Excellon totals were not reset
- fixed: multi-depth was used for a laser preprocessor; a laser cuts at one height
- a change in the form is one light Undo step
- new: "Probe plate thickness" in the plugin and in Preferences > Tools > Milling (advanced, default 0.0), for Toolchange_Probe_MACH3, as in Drilling

QR Code:

- fixed: a result of the geometry job that came after Reset, Close or a new Insert was used
- fixed: a QR code into a new object needed a target object; a PCB document object keeps its own units
- the soldermask Gerber can not be the target object; the automatic version is now on by default; Esc only cancels an active insert

Punch Gerber:

- the last column of the apertures table fits its contents

G-code Sender:

- the plugin is now named G-code Sender (ToolGcodeSender); it does not open until it is finished

Gerber Parser:

- fixed: a track drawn with a rectangular (R) aperture became the bounding box of its first segment; a polyline or a diagonal track now has the true swept shape of the rectangle on each segment
- fixed: a track drawn with an obround (O) or a polygon (P) aperture used the diagonal of the aperture as its width (O0.5x0.5 gave 0.707); the aperture shape is now swept along the track
- fixed: a track drawn with a macro (AM) aperture made the whole file fail at a D02, at a polarity change (%LPC%) or at the end of the file; the macro shape is now swept along the track
- fixed: a region with more contours separated by D02 kept only one contour when a rectangular aperture was selected before G36; a bare D02* in a region did not close the contour
- fixed: a region that crosses itself lost one of its parts when it was repaired; the repair now keeps all the polygonal parts, and the aperture data keeps all the parts of a repaired region
- fixed: incremental coordinates (%FSLI...% and G91) were read as absolute coordinates
- fixed: with trailing zeros (%FST...), a "+" sign was counted as a digit (X+1 gave 1 in place of 10)
- fixed: an arc in a file without G74/G75 was not drawn; the single quadrant mode is now the default
- fixed: in G01 mode, a line with I/J values was read as an arc and was dropped
- fixed: a single quadrant (G74) arc larger than 90 degrees was not drawn correctly
- fixed: a D01 before any D02/D03 made the parse of the file fail
- fixed: an OrCAD format line set the units of the file to "G74" or "G75"
- fixed: a line with an attribute (%TO, %TA, %TD, %TF) was deleted whole, together with the flash or the draw on the same line
- fixed: two extended commands on one line (%ADD10C,1*%%ADD11C,2*%), an aperture definition on more lines or a "*%" on the next line made the file fail or the command was ignored; the file is now split into commands, not into lines
- fixed: lowercase commands were not understood
- fixed: the tracks were 0.05% too wide (the width was divided by 1.999 in place of 2)
- fixed: a flash of an aperture that can not be made is skipped with a warning in the log; before, the whole file failed
- new: step and repeat (%SR...%), also with a polarity change inside the block
- new: aperture blocks (%AB...%), also nested; a block is flashed like an aperture
- new: load mirror, load rotation and load scale (%LM, %LR, %LS); they change the flashes and the tracks that come after them, not the regions (as the Gerber specification says); the order is mirror, then rotation, then scale
- new: holes in the standard apertures (C, R, O and P, for example C,1X0.5)
- new: the deprecated image commands OF (offset), SF (scale factor), MI (mirror image), IR (image rotation) and IPNEG (negative image); SF scales only the coordinates, not the aperture sizes; AS (axis select) is not supported and a warning is written in the log
- new: IPNEG inverts the image inside its bounding box, and the user gets a warning that says this
- new: the X2 attributes (TF, TA, TO and TD) are kept with the Gerber object and are saved in the project
- new: coordinates with a decimal point (not allowed by the format) are read as absolute values, with a warning in the log
- the file is parsed about 2 times faster: each flash shape is made once for each aperture and then moved, the tracks of an aperture are swept in one step, the clear (LPC) parts cut only the dark parts that they touch, and the joining of the polygons uses a faster closing step (a test file: 7.0 s -> 3.7 s)
- the distance buffer of a Gerber object (Transform plugin) buffers the solid geometry and all the aperture records in one step; a distance that is not a number is refused, and a failure leaves the object unchanged

Aperture Macros:

- fixed: a macro that used "x" for a multiplication but had no modifiers, and a Vector Line, Center Line or Polygon primitive with fewer modifiers than declared (for example a Center Line without the rotation), made the whole file fail
- fixed: a macro with variables used a wrong value for a negative number, a division, spaces, the variables $10 and up ($1 was replaced inside $10) or a variable that was not defined; every $n is now replaced by its value in brackets, and an undefined variable is 0
- fixed: the Circle primitive ignored its rotation angle; the Moiré and the Thermal primitives ignored the rotation, and the crosshair length of a Moiré is now the total length of each line
- fixed: a Polygon primitive with the number of vertices from a variable made the parse fail
- fixed: a primitive with no size (a width or a height of 0) could make a geometry collection, and the code then moved the geometry by 0.0001; such a primitive is now skipped and no primitive is moved
- fixed: an unknown primitive in a macro is skipped with a warning; a Moiré with a ring thicker than the circle made a crash, it is now a disk
- the circles of the macro primitives use the circle steps of the Gerber object

Excellon Parser:

- fixed: after the first route (G00 + M15 ... M16), the drills of the next tools were not read; the parser now has a real drill mode and route mode (G05, M15, M16, M17)
- fixed: a route with more segments or with a second G01 kept only one slot, and the arcs of a route (G02/G03 with A or I/J) made no geometry; now each segment of a route is a slot and an arc is made of short slots
- fixed: a tool that was used but not defined made the import fail; it now gets a default diameter and a warning
- fixed: a file without a header (no M48 and no %) ignored the diameter in "T1C0.8"; a G93 line without a tool was read as a drill
- fixed: the units of the preferences overwrote the METRIC/INCH units of the header
- fixed: after M71/M72 or the Allegro ";Holesize ... MILS" comments, the units were guessed again from the diameters (a PADS file with M72 was read in mm)
- fixed: a bare INCH or METRIC line (without LZ/TZ) set the zeros to "trailing", and the coordinates were 10000 times too small
- fixed: a change of the units (M71/M72) in the body did not convert the values that came after it
- fixed: a comment in the header with the word INCH or METRIC converted the tools
- fixed: a comment in the body cleared the modal coordinates, so the next drill was lost
- fixed: the R repeat with decimal coordinates put all the copies in the same place
- fixed: a tool with its parameters in a different order (T1F200S65, T01Z-0.1F200C0.8) made the parse fail or lost the tool
- fixed: a coordinate with a decimal point next to one without (X10.5Y020000), and "X2.0G85X4.0", were not read
- fixed: "M97,REV X1" made a drill; M02X..Y.. was read as a drill
- fixed: lowercase files had no tools; tabs at the end of a line were kept
- fixed: a tool defined again in the body (T1C1.2 after T1C0.8 in the header) kept the old diameter
- fixed: a G85 slot did not set the modal X/Y of the next drill, and a slot could not start from the last drill
- fixed: a ";HEADER" text inside any comment switched the parser to the Allegro mode
- fixed: a file with a UTF-8 BOM lost its first header line; an error in the parse now writes the reason in the log
- fixed: the parser wrote the state of the file into Excellon.defaults, a dictionary that all the objects share
- new: incremental coordinates (G91 and ICI,ON) and the G93 zero set
- new: pattern repeat (M25, M01, M02X..Y..) with M70 (swap X/Y), M80 (mirror X) and M90 (mirror Y); the mirror and the swap change only the copy of their own M02 line
- new: the plated / non-plated flag of each tool, from the KiCad attributes (TF.FileFunction, TA.AperFunction), the Altium ";TYPE=PLATED" comment and the Allegro "PLATED / NON_PLATED" comments
- new: the KiCad "; FORMAT={3:3/ ...}" comment gives the number format; FMAT and VER are read; the M97/M98 canned text and the M47 messages are ignored
- new: Excellon.merge_equal_diameter_tools() can join the tools with the same diameter (it is not used by default, so the saved files keep their tools)
- new: the user gets a warning when lines of the file were not understood or holes were dropped
- a file with 100000 drills opens about 20 times faster (about 15 s -> 0.7 s): the source text is not copied for each line, a plain drill line has a fast path, the numbers use a cached divisor and the holes of a tool are made from one template circle
- the conversion of the units after the import makes the geometry only once (before, 3 times)

Circle Steps (Gerber, Excellon, Geometry):

- new: Preferences > Gerber, Excellon and Geometry > "Circles": Adaptive (default) or Fixed; the label has a tooltip that explains the two modes
- new: Adaptive gives each circle the number of segments that keeps it inside the "Max Deviation" (in µm) from the true circle; a small pad gets few segments and a large arc gets many (at least 16, at most 1024, a multiple of 4); the default deviation is 2 µm for Gerber and 5 µm for Excellon and Geometry
- new: Fixed uses the "Circle Steps" value for every circle, as before; the field is shown only in the Fixed mode
- all the code uses the same setting for its circles: the Gerber and Excellon parsers, the Gerber, Geometry and Excellon editors, Isolation, Non-Copper Clearing, Paint, Milling, Drilling, Etch Compensation, Invert Gerber, Fiducials, Markers, Punch Gerber, QR Code, Image, the PDF, HPGL2 and SVG import and the Tcl commands
- the default circle steps are now 64 for Gerber, 32 for Excellon and Geometry and 16 for Copper Thieving
- the PDF import uses the Gerber circle setting for the strokes and the round ends
- Tcl subtract_objects: when -steps is not given, the steps come from the circle setting of the target kind and the largest radius (margin or drill)

Open G-Code:

- new: File > Open G-Code makes a CNC job like the jobs of Drilling and Milling, from any G-code: the tools, the toolpaths, the drill hits, the bounds, the travel distance and the routing time come from the text of the file (new parser appParsers/ParseGCode.py)
- new: the job type (Geometry or Excellon) is read from the "TYPE: G-CODE FROM ..." header of a FlatCAM file; for other files, a file that has only drill hits (no cutting moves) is an Excellon job, and all the others are Geometry jobs
- new: the preprocessor name is read from the FlatCAM header; when the name is not known, the "default" preprocessor is used
- new: the file is split into tool blocks at each T word; the tool diameter is read from comments like "(Tool: 1 -> Dia: 0.8)", "(T1 D0.8)" or "(D0.8)", and the cut Z from "Tool: 1 -> Z_Cut: -1.7"
- new: the text of the file stays as it is: the line endings, the comments and the spacing do not change; the file is read as UTF-8 (latin-1 if it is not UTF-8) and written back with the same encoding and the same line endings
- new: the Source File of the job is the file; the job has no FlatCAM header, no start code and no footer, and the Prepend / Append snippets are added only when the snippets checkbox is checked
- new: a file without a tool diameter gets the default diameter of the application (the first "Drill tooldia" of Excellon for drills, the first "tooldia" of Milling for the other jobs; 0.8 mm or 2.4 mm if the preference is empty)
- new: Cut Z, Travel Z, Feedrate X-Y, Feedrate Z and Spindle speed that the file does not give come from the Drilling or Milling preferences; one warning tells the user which values and which tool diameters are the defaults
- new: the warning also says when the file has no G20 / G21 (then the units of the application are used) and lists the unsupported parts of the file (the first 3, then the number of the others)
- new: the units of the G-code (G20 / G21) are read and the job is converted to the units of the application; a change of the units inside the file is ignored with a warning (the first units are kept)
- new: the parser reads G0, G1, G2 and G3, G90 / G91, G90.1 / G91.1 (the mode of the arc centre), G92 / G92.1 (offset) and G98 / G99, and skips the G28, G30 and G53 lines
- new: arcs are read with I J K or with a radius R (a negative R is an arc over 180 degrees), also a full circle; the arcs are made with the circle steps setting
- new: arcs in the G18 and G19 planes (and G17) are read, with a linear third axis (helix); when the start position of a G18 / G19 arc is not known, the arc is drawn as a straight line with a warning
- new: the canned drill cycles G73, G81, G82, G83, G85, G86 and G89 give one drill hit for each XY position (with the R plane, the depth, the G98 / G99 retract and the incremental mode)
- new: a plunge that never moves in XY is a drill hit, and one straight XY cut after a plunge is a slot; the hits and slots of an Excellon job are counted for each tool; the drill rings are drawn with the tool diameter
- new: Fanuc subprograms (M98 / M99 with O numbers, G65 macro calls with the argument letters) and LinuxCNC subroutines (o<name> sub / call / endsub / return) are followed; a call of a missing subprogram is skipped with a warning; calls nested more than 16 levels deep are skipped
- new: macro variables (#1, #<name>, global and local), expressions in [ ] (+ - * / MOD ** AND OR XOR, EQ NE GT GE LT LE, SIN COS TAN ASIN ACOS ATAN SQRT ABS ROUND FIX FUP LN EXP POW), GOTO, IF / THEN, WHILE / DO / END and the LinuxCNC o-word IF / ELSEIF / ELSE, WHILE, DO, REPEAT, BREAK and CONTINUE are run offline; the job shows the lines that the machine would run, each line in the tool block of its call
- new: the expansion stops with a warning after 200000 lines, and one loop stops after 10000 turns; a value that is not known offline (a machine variable) skips the moves that use it, and a write to a machine variable is ignored, both with a warning
- new: Move, Rotate, Scale, Mirror and Skew of an imported job change the X, Y, I and J words in the G-code text (the comments, the line order, the line endings and all the other words stay), with the decimals of the "CNC job coordinates decimals" setting, and the job is parsed again; the Source File and the saved file have the new coordinates
- new: a transform that is not a rotation by 90 degrees, a scale or a mirror (for example a skew) replaces the arcs with short straight segments; a scale or a mirror keeps the arcs (I and J scale with them, and a mirror changes G2 to G3)
- new: a transform replaces the arcs in the G18 / G19 planes with straight segments; the lines with G53, G28, G30 or G10 and X / Y are not transformed; relative (G91) moves are transformed as vectors; all with a warning in the log
- new: a job that has macro variables or subprograms can not be moved, transformed or levelled; the plot and the text stay as they are, and the user gets the message "The G-code has macro variables or subprograms: it can not be moved or transformed" (or "... it can not be levelled") with the name of the object
- new: a change of the units of an imported job scales the parsed geometry and the drill positions; the text of the file is never changed
- new: the Project tab has an Open entry for the CNC Job category, which opens a G-code file
- new: in the Tools tab of the job and in the tool table of the G-code Editor, a tool without a diameter or with older data does not crash; the Cut Z column of the Excellon table works for jobs that have no "offset" in the tool
- fixed: a G-code file with comments in "( )" or ";", lowercase letters or more than one G word on a line (for example "G90 G21 G1") was read wrong; the parser now ignores the comments, reads the lowercase words and all the G words of the line, and does not take an exponent (1e3) for a word
- fixed: Open G-Code gave the error "Probable not a GCode file" for any file that FlatCAM did not make; a file that holds no motion now gives "This is not GCODE", and the other errors are shown once (no second error message)
- fixed: the old reader looked only for the text "Tool: n -> Dia: d" of a FlatCAM file; the job of an imported file now plots and has tools in the tool table and a real travel distance, routing time and bounds

CNC Job:

- new: an imported job has the attributes "gcode_imported" and "gcode_encoding", which are saved in the project; Save Source File writes the text as it is
- new: after a Move, a Transform or a change of the origin, the Source File of a job is made again from the current G-code (before, the saved G-code kept the old coordinates, which is not safe); this reads no widget, so it can run in the worker
- new: a transform of a job of the application changes the G-code text of each tool with the matrix; for the Roland and HPGL preprocessors only the plot follows, and the message "The machine code of the Roland/HPGL preprocessors is not transformed, only the plot is. Generate the job again." is shown
- fixed: "Update Source File" of an imported job added a FlatCAM header, a start code and a footer to the original file; it now gives the original text, with the snippets only if the checkbox is checked
- fixed: the default "tooldia" of a new CNC job was 0.03937 in all the units (0.4 mm in inches); it is now 0.4 for MM and 0.01575 for inches
- fixed: an old project with a CNC job tool that has no diameter, "offset" or "gcode_parsed" gave an error when the tool table or the plot was made; a missing diameter is now the diameter of the job
- fixed: the Cut Z of an Excellon CNC job tool was wrong or made a crash when the tool data had "offset_z" or no "tools_drill_cutz"
- fixed: the plot parser of the jobs of the application read G91 (incremental) moves as absolute; X, Y and Z are now relative to the last position in G91
- fixed: in the plot of a job, G2 / G3 arcs with R (radius) or with G90.1 (absolute centre) were drawn wrong or made a crash; a missing I or J is 0
- fixed: in the plot of a job, a line with G53, G28 or G30 was drawn as a move, and a line with more than one G word (for example "G21 G90") lost its motion mode; only G0 to G3 change the motion mode now
- fixed: the diameter of each hole of an Excellon job was found with a loop over all the drills for each Z move; it is now one table (faster for a job with many holes)

Preprocessors:

- fixed: the G-code was made with str.format(**p) on a text that already had the coordinates, so a "{ }" in a value could make a preprocessor error; all the 28 preprocessors now make the move lines without format(**p)
- fixed: a preprocessor error gave a missing line in the G-code without a message; the user now gets the warning "Preprocessor error, G-code is missing: <method> - <error>" once for each preprocessor method of the job
- fixed: a preprocessor method that has nothing to write (a dwell of 0, the tool change of a laser) gave an empty line in the G-code; it now gives no line
- fixed: the Z of a deep pass (multi-depth) was given to the preprocessors as a Decimal, which made Roland and ISEL ICP fail; it is now a number
- fixed: the extra-cut moves of a ring (Extra cut) did not give the cut Z to the preprocessor, so a preprocessor that writes the Z on the move line (Line_xyz) used a wrong Z
- the move line (linear_code) is about 3 times faster: the preprocessors use one shared position code in the base class, and the line is joined, not formatted
- fixed: the bed offset and the bed skew of the machine were not applied the same way in all the preprocessors (ISEL ICP used the X for the Y skew and offset; Roland and HPGL applied them after the conversion to machine units); all the preprocessors now add the offset and apply the skew to the job point, in the job units
- fixed: the end moves (End Z and End X,Y) were missing or wrong in some preprocessors: Marlin_laser_FAN_pin, Marlin_laser_Spindle_pin, GRBL_laser, GRBL_laser_z, default_laser, Line_xyz, grbl_laser_eleks_drd, Paste_1, Paste_GRBL and Paste_Marlin now write the end coordinates with the "Coordinates decimals" of the job; NCCAD9 now always writes the End Z, also without an End X,Y; the X-Y end move is not made below the Travel Z
- fixed: the Z of the end moves and of the Paste end moves was written with the feedrate decimals; it now uses the coordinate decimals
- fixed: the dwell code of default, Default_no_M6, GRBL_11, GRBL_11_no_M6, Berta_CNC, Line_xyz, Toolchange_Manual and Toolchange_Probe_MACH3 wrote "None" when the dwell is 0; it now writes no line
- fixed: the header of Berta_CNC, GRBL_11_no_M6, ISEL_CNC, Line_xyz, grbl_laser_eleks_drd and ISEL_ICP_CNC had the line "Steps per circle" 2 times; Berta_CNC also had the tool diameter and the feedrate lines 2 times
- fixed: the base preprocessor class now has startz_code, z_feedrate_code, dwell_code, position_code and coord(), so a preprocessor file of the user that does not define them still works
- default_laser, GRBL_laser, GRBL_laser_z: the laser min power can be empty (it made a crash); default_laser writes "S0" with the laser ON code when the min power is 0 (it wrote no S)
- GRBL_laser_z, Marlin_laser_z: the tool header (feedrate, Z focus, laser power) works for an Excellon job: it takes the Milling keys, then the Drilling keys, and writes "-" when a tool has none (it was a KeyError); Marlin_laser_z also writes the rapid feedrate and the laser min power for both job types, and the line "X,Y Toolchange: None" had the wrong text "X,Y End"
- Marlin_laser_z: the "Feedrate rapids" lines were written with "(" and not with ";" as a comment
- Marlin: the first line of the header ends with a new line; the "Spindle Speed" comment has no extra ")"; the file now sets G90 (absolute) after G20 / G21
- Marlin: the tool change has no M6 and no T (in Marlin, T selects an extruder): it is M5 (spindle stop), Z toolchange, X,Y toolchange, "M0 Change to tool T<n> dia <d>" (with "drills <n>" for Excellon), Z toolchange
- Marlin and Repetier: the dwell is in seconds; they write "G4 S<seconds>" for whole seconds and "G4 P<milliseconds>" for the other values (before, G4 P got the seconds, so the dwell was 1000 times too short)
- Marlin_laser_FAN_pin: the M106 power is an integer from 0 to 255 (before, a spindle speed like 1000 was written)
- Repetier: the M106 (fan) power is limited to 0 - 255; the tool change now has M400 (wait for the moves) and M107 (fan off) and no M84 (it switched off the motors)
- Repetier, NCCAD9: the Z move to zero uses the Z feedrate (it used the X-Y feedrate)
- Default_no_M6: the tool change message has the tool number: "Change to Tool T<n> Dia = <d>"
- Check_points, Toolchange_Manual, Line_xyz: the tool change line "G00 X.. Y.." has a space between X and Y; Check_points and Toolchange_Manual do not write spaces at the end of the lines
- Toolchange_Manual: the Excellon message after the tool change says "Drilling with Tool Dia" (it said "Milling")
- Line_xyz: the tool change moved to a Y equal to the X value; it now moves to the Y; the tool change of an Excellon job for the first tool (no old position) uses 0 and does not crash; the start Z, lift, down and up-to-zero moves have the bed offset and skew
- NCCAD9: the "Tools Offset" header used the key exc_cnc_tools, which does not exist; it now uses the tools of the job; the file sets G90; a tool change X or Y of 0 is now used (before, it switched the X,Y move off)
- ISEL_CNC: G70 for inches (it was always G71 for mm); the tool change goes to the Z toolchange (the Start Z for the first tool), to the X,Y toolchange, then T, M06 and M00 (a mandatory stop; M01 does not stop when the optional stop is off), then to the Z toolchange again
- ISEL_ICP_CNC: the units are micrometres for MM and for inches (before, an inch job was 25.4 times too small); the VEL feedrate is in micrometres per second; the tool change goes to the Z toolchange and the X,Y toolchange, stops the spindle and then sends GETTOOL; End Z and End X,Y are written (before, the end always moved to the machine zero); the "Spindle Speed" comment has no extra ")"
- grbl_laser_eleks_drd: the drill circles (0.1 mm and 0.2 mm radius) are in the job units (an inch job had a radius 25.4 times too large) and at the hole with the bed offset and skew; the end code uses the End X,Y (it used the toolchange X,Y); the spindle direction line does not crash when the value is not a text
- Roland_MDX_20: X, Y and Z now use 40 units per mm (the RML-1 unit is 0.025 mm); before, X and Y used 100 units per mm, so the job was 2.5 times too large in X and Y; the coordinates are integers; an inch job is converted to mm first
- Roland_MDX_540: the coordinates are integers in units of 0.01 mm (100 per mm) as before; the feedrate range is 0.1 to 125 mm/s (before, it had the 15 mm/s range of the MDX-20)
- Roland_MDX_20, Roland_MDX_540: the feedrate V is limited to the range of the machine (a feedrate under 6 mm/min was set to 6 mm/s; now it is the lowest valid value, 0.1 mm/s), and the feedrate of an inch job is converted to mm/min first
- Roland_MDX_20, Roland_MDX_540: the "up to zero" move made a crash (a text plus the number 0); the spindle ON line works without a spindle speed (it writes only "!MC1;"); the spindle speed can be a decimal
- hpgl: this is an HP-GL plotter preprocessor, not a Roland one (the comment is changed); the coordinates are integers in plotter units of 0.025 mm (40 per mm), with the bed offset and skew in the job units and a limit of -32768 to 32767
- Toolchange_Probe_MACH3: after the probing, the Z is set with "G92 Z<probe plate thickness>" (it was "G92 Z0"), and the height between the moves includes the plate; the message shows the plate thickness when it is not 0
- Toolchange_Probe_MACH3: the tool change goes to the Z toolchange and the X,Y toolchange first, then T and M6 (before, T and M6 came before the move); the Start Z (G00 Z) is written before the probe warning when it is set
- Paste_Marlin: the tool change message is "M0 Change to tool with nozzle dia <d>" (Marlin shows the text of M0; ";MSG" is a comment)

Levelling:

- new: an imported G-code job can be levelled (before, it needed a segmented G-code made by the application); the long G1 moves of the file are split into shorter pieces (the segment length of the plugin), so the height map also levels the cut between the ends; the message "Long moves split into shorter line moves: <n>" shows how many
- new: the levelled job of an imported file is again an imported job; its tools, text and plot follow the levelled text
- fixed: the height map (in the units of the application) was applied to a file in the other units with the wrong scale; the probe X, Y and Z are now converted to the units of the file text before they are written in it
- a job with macro variables or subprograms can not be levelled (see Open G-Code)

G-code Editor:

- fixed: a click on a tool in the tool table selected the wrong text (the first column is the row number, not the tool); the row is now found by the hidden tool key, and the tool of an imported job selects its exact block in the editor, also when 2 tools have the same diameter
- fixed: a click on the Header or the Start code row made a crash when the job has no such code
- fixed: the Cut Z column of the Excellon table made a crash for tools without "offset" or "tools_drill_cutz"

Transform:

- new: the buttons "Skew X/Y", "Scale X/Y" and "Offset X/Y" do the action on both axes in one step (one Undo step); with Link checked, the X value is used for both axes
- new: the Transform plugin works only on Gerber, Excellon and Geometry objects; a CNC job or other object in the selection is skipped with a warning that names it
- fixed: the Rotate, Skew, Scale, Offset, Flip and Buffer buttons used the selection at the time the worker ran; the selection is now taken at the click
- fixed: a second click while a transformation was still running started a second one at the same time; it now shows "Another transformation is still running. Please wait for it to finish."
- fixed: an empty or wrong number in an entry, or a point that is not finite, gave a crash or a wrong result; it now shows "Invalid numeric value" or the message of the Point format, and nothing changes
- fixed: Rotate by 360 degrees (or a multiple), Skew by 0 degrees, Scale by 1 and Buffer by 0 did a step that changed nothing; they now show "... would not change anything."
- fixed: Skew by 90 degrees (and 90 + n x 180) collapsed the geometry; it is refused; Skew by 180 degrees is now allowed because it collapses nothing
- fixed: a transformation that can not be undone (a matrix that collapses the geometry) changed the object; it is now refused before any object changes
- fixed: when the transformation of one object failed, its Gerber or Excellon source text was left changed; the exact old text is now put back
- fixed: after Rotate, Skew, Scale, Flip and Offset, the Gerber and Excellon source (in the project file and "Save source") did not follow the geometry; it is made again after each step
- fixed: Buffer of an Excellon or Geometry object did not report a failure; a failed buffer is now an error and the object does not change
- fixed: the Offset field of the Properties tab was filled from the worker thread; it is now filled in the GUI thread with the bounds that are already known
- fixed: the plugin closed the wrong tab when the notebook tabs were in a different order, and a new tab could close at once; the plugin now closes only its own tab
- fixed: the Point and Object fields of the reference stayed hidden or shown after the plugin was opened again; they follow the selected reference
- fixed: a deleted or empty Reference object made a crash; it now shows "Reference object not found" or "Reference object has no geometry"
- the entries of Scale, Offset X and Offset Y have a range of -1e6 to 1e6
- the buttons "Flip on X" and "Flip on Y" are now "Flip X" and "Flip Y"; the tooltips say which coordinate is mirrored (Flip X mirrors X over the Y axis, Flip Y mirrors Y over the X axis), that Rotate, Skew, Scale and Flip use the reference point and that Offset does not use it

Move:

- fixed: a move offset that is not a number or is infinite made a crash; it now shows "Invalid move offset."
- fixed: a second move that started while the first one was in the worker overlapped it; it now shows "A move is already in progress. Wait for it to finish, then retry."
- fixed: a failure inside the move job left the plugin blocked; the plugin is always released when the job ends
- fixed: the selection shape of a very thin object disappeared (it was shrunk by 0.1 mm / 0.00393 in); it is now made from the original shape

Distance:

- new: the fields "Segment" (the length of the preview segment, or of the last segment when the measurement is done) and "Points" (the number of the points that are set)
- new: in the Multipoint mode, Backspace removes the last point (when the focus is not in a text entry), and a right click finishes the measurement and shows the final start, end, deltas, angle and total distance
- fixed: with the big cursor, the cursor type was changed or not restored when the plugin started again or closed; the cursor type and color are saved at the start and restored at the end
- fixed: the cursor did not go back to "small" when the big cursor option was cleared; the option now changes the cursor at once while the plugin runs
- fixed: a drag of the mouse (pan) was counted as a click, and the right click after a drag finished the measurement; a drag is not a click now
- fixed: when another tool took the mouse events, the Distance plugin stayed on and changed the call source and the command of the other tool; it now ends without a change to them
- fixed: when the plugin tab was closed or replaced, the measurement stayed active; it now ends
- fixed: the start, end, deltas and total distance did not show the value 0 (zero values were skipped)
- fixed: a change of the Multipoint mode in the middle of a measurement kept the old points; the measurement starts again
- fixed: "Snap center" switched off the grid snap also when the plugin was not active; it does this only while it measures
- fixed: the cursor jumped to the snap point only on a click; the preview now shows the snap to the center of the pad or drill under the cursor, also at the border of the pad
- the snap to the center of a pad or drill uses a spatial index of the visible Gerber and Excellon objects and of the pads or drills of the open editor; the index is made once and again when an object or the editor changes; for a pad inside a pad, the closest center is used
- the snap to a Gerber pad does not use a clear (LPC) flash and needs copper under the point

Film:

- fixed: the PNG export scaled the geometry with the DPI of the screen; the PNG now uses only the "PNG DPI" value, and a value of 0 or less shows "Failed. Invalid PNG DPI value."
- fixed: the position of the artwork on a page of a fixed size ignored the border; the artwork is now at the border distance from its original position
- fixed: for the page size "Bounds", the artwork had to be in the first quadrant; the page is now the box plus a border on each side, and the artwork must only intersect it
- fixed: the negative film checked the page with the bounds of the object; it now uses the bounds of the artwork and of the background together

Geometry Editor:

- new: Text tool: "Bitmap smoothing (px)" (0 to 2, default 0.5), for bitmap fonts only
- fixed: Text tool: Bold together with Italic used the Regular face
- fixed: Text tool: Apply with a font size of 0, a negative size or a size that is not a number showed no message or made a crash; it now shows "Enter a positive font size."
- fixed: Text tool: a failed Apply kept the geometry of the earlier Apply, which could then be placed; the earlier result is now dropped, and a font error (for example a missing glyph) shows a message in the status bar
- fixed: Copy as a circular array did not rotate the copies; each copy now rotates around the reference point by the array angle, in the selected direction (CW or CCW)
- fixed: the copies of the Copy tool lost the data (for example the bounds) of their source shape; they now keep a copy of it
- fixed: a transformation with a value that is not finite, a skew of 90 degrees, a buffer with a value that is not finite, a scale or matrix that collapses the geometry, or an empty or invalid result changed the shapes; it now shows an error and changes nothing
- fixed: the transformation of the selected shapes shared the data dictionary with the source shape; it is a copy now
- fixed: a failed Rotate, Mirror, Skew, Scale, Offset or Buffer in the Transformation plugin of the editor still showed a success message; a failure now stops the action
- fixed: "Offset on Y axis" and "Skew on Y axis" of the Transformation plugin did the X action; they now do the Y action
- fixed: the success message was shown also when the input box was cancelled; a cancel is now a warning
- fixed: the Point field of the Transformation plugin stayed visible or hidden after the plugin was opened again; it follows the selected reference
- fixed: the reference "Origin" was not used when it was given as the number 0
- new: the Transformation plugin has the references "Lower Right", "Upper Right" and "Upper Left"; "Minimum" is the lower left corner of the selection
- the buttons "Flip on X" and "Flip on Y" of the Transformation plugin are now "Reflect X (across Y)" and "Reflect Y (across X)"; the rotation label says "Angle (CW)", and the rotate dialog says "positive CW"
- Buffer (Interior, Exterior, Full): the result is made from the contours of the polygons and lines, also for the parts of a multi-geometry; a distance that is not finite shows "Invalid distance." and the selection is kept; a failed add of the shapes stops the action; the distance is not shifted by 1e-10 any more
- a click on a shape selects its row in the shapes tree at once and scrolls the row into view if it is outside the view
- the vertex grips (also in the Gerber Editor) are at least 12 pixels apart: when zoomed out, the dense grips do not cover the shape (zoom in to reach the others); the fill of a grip is half transparent; the grips are drawn again after a zoom, also without a mouse move

Gerber Editor:

- the Rectangle Region and the Text Regions controls are now in the Properties tab of the editor (they were in a dialog window); the "Cancel" button is now "Close"; Esc closes the panel and J jumps to a location; the panel ends when another tool is selected
- new: Rectangle Region and Text Regions show a live preview on the canvas that follows the mouse; a click sets X and Y; Apply keeps the panel open, so more regions can be placed
- new: Text Regions: "Bitmap smoothing (px)", as in the Geometry Editor; the text input shows the font, the size, Bold and Italic as they are set, and takes plain text only (one style for the whole text)
- the text preview is cached, so it does not parse the font again at each mouse move; a font error of the preview is not shown at each move
- new: the Transformation plugin has the references "Lower right", "Upper right" and "Upper left"; "Minimum" is now "Minimum (lower left)"
- new: the Buffer plugin accepts a negative distance (range -10000 to 10000); only a negative distance can remove a shape
- fixed: Scale, Buffer and the other Transformation actions run in the worker as one Undo step and show the success message ("Done.") only when the geometry really changed; the tool closes after the step is stored
- fixed: Rotate, Skew, Scale and Offset with a value that is not finite, a skew of 90 degrees, a factor of 0 or a matrix that collapses the geometry showed no error; they now show "Transform values and reference must be finite and nonsingular." or "Transform matrix must be finite and nonsingular."
- fixed: a transformation that made a coordinate that is not finite, an invalid geometry or an empty geometry changed some shapes before it failed; the whole result is now checked first and then assigned
- fixed: Scale of a pad with an empty solid used the wrong center; the center now comes from the first geometry that is not empty
- fixed: the status line said "Rotate done", "Offset on the X axis. Done." and so on after the dialog was cancelled or the action failed; the messages now come from the action itself
- fixed: a shape with an explicit empty solid was made again as copper from its old follow geometry; only the clear geometry is kept
- fixed: a Gerber object loaded as regions was not found when the solid was empty but the follow was not
- fixed: the "Point" reference accepted a text like "inf,1" and gave a wrong result, and an empty selection made a crash with "Selection" or "Minimum"
- fixed: the Resize circles plugin, the track bend and the history checks found a stored circle only with the steps of the current preference; they also try the other mode (Fixed / Adaptive) and the steps of the loaded object
- the history combo of the editor takes the full width
- new: Preferences -> Gerber -> Editor -> "Load" sets how the editor opens a Gerber object with clear (negative) features: "Regions" (default, as before) loads it as one region aperture (0) with one polygon for each solid area; "Apertures" keeps the apertures and cuts the clear areas from the copper and the follow lines of the features (a feature cut in more parts becomes one feature for each part); the value is read each time the editor opens; such an object is saved as regions in both modes
- fixed: the edit of the dimensions of an R or O aperture made new full flashes, also from a flash that was cut or edited; it now shows "Dimension editing supports flashes with the aperture shape only." and changes nothing
- fixed: after a click on a shape, the row of its aperture was shown as selected only after the mouse went over the table; the row is now shown at once and comes into view if it is outside the panel; the features table scrolls to the selected feature
- fixed: a feature in more parts (for example a region that the Eraser cut in 2, or a region operation or buffer that split a shape) was not drawn on the canvas, so it looked deleted; each part is now drawn, and the parts are selected and recolored together

Excellon Editor:

- fixed: the tool table found the tool by the diameter text of the table (rounded to the display decimals); the real diameter is now kept in the table item, so a diameter like 0.3000001 is not mixed with 0.3 when you add a drill, a slot or an array, or move, copy, resize or delete
- fixed: an edit of a tool in the table used the selected row, not the edited cell; it now uses the edited cell and only the diameter column
- fixed: the selection of the tool table after a click on a shape or after Select All had a toggle effect and used the tool number as the row; the rows are now found by the diameter
- fixed: Copy as an array in the Linear/2D mode checked the limit only for the selected drills; it now counts the copies (rows x columns, or the array size) against the limit
- fixed: Transform features (scale, rotate, mirror, skew, offset) now put only the selected features into the Undo step, which is faster for a large board
- fixed: Mirror or Scale of the drills changed the order of the drill and slot lists, and the point counters could differ; the order of the features is kept
- fixed: the drills that a plugin made (2-Sided) lost the mark of the plugin after a Move, Copy, Resize or Transform in the editor or after a save; the mark is kept
- new: Transform features has the references "Minimum / lower left", "Lower right", "Upper right" and "Upper left"; the tooltip and the menu entry of Mirror X and Mirror Y say which coordinate is reversed
- the editor counts the drills and the slots of a tool again only when the tool changed
- fixed: after a click on a shape, the row of its tool was shown as selected only after the mouse went over the table; the row is now shown at once and comes into view if it is outside the panel; the features table scrolls to the selected feature

Undo / Redo:

- fixed: Undo of a Rotate, Scale, Skew or Mirror of an Excellon object left the holes about 2 µm away from their first place; only a translation moves the hole shapes now, every other matrix makes the holes again from the restored centers
- fixed: after Undo or Redo of a matrix step, the Gerber or Excellon source text was not made again; it now follows the geometry

Text and Fonts:

- new: font collections (.ttc, .otc) are read; each face of a collection is listed as its own font, and the right face is used
- new: bitmap fonts (.bdf, .pcf, .fon, .fnt) are listed and can make text; a .fon file with more faces uses the size nearest to the font size; Windows bitmap fonts with a legacy code page (not Unicode) find their characters
- new: bitmap fonts become smooth outlines: the pixel image is traced and simplified; strokes are not merged, a counter (hole) is not closed and a dot is not lost; with a smoothing of 0 the exact pixel edges are kept
- new: text with ligatures, kerning, contextual forms, clusters, right-to-left text and mark positioning (GPOS) is set with the Qt text layout; simple text keeps the FreeType outlines
- new: the font parser does not replace a font: if Qt would use another font, or the requested face is not available, the text fails with a message ("Font substitution is not allowed", "Font face is not available") and no wrong geometry is made
- new: the glyph fill rule of the font (non-zero or even-odd) is used, so overlapping contours, holes and islands in a glyph are filled as the font draws them
- new: text with more lines: a new line character starts a new line of text (before, it gave the "missing glyph" shape); a tab moves to the next column that is a multiple of 4
- fixed: a text with a character that the font does not have gave a partial result; it now fails and lists the missing characters ("Font has no glyph for: ...")
- fixed: Bold and Italic could not be used together (the style was always Bold or Italic); the Bold Italic face is now used
- fixed: a family that has only a heavier or slanted face now serves the Regular style
- the curves of the glyphs are flattened with a tolerance that follows the font size, so they stay smooth at all sizes (before, a fixed number of steps)
- the font list finds the fonts in the Windows font folder of the user, in the Linux XDG data folder and the fonts that the user installed without admin rights; the family names that the Windows font menu shows (for example "Arial Narrow", "Segoe UI Light") are listed next to the legacy family name
- SVG import: the width of a text run is measured with the same font engine that draws it (with the ligatures and the kerning); a font error is written to the log
- Altium PcbDoc import: a text from a font collection used the first face of the file, it now uses the right face; the kerning used glyph indexes where the font binding needs characters, so the kerning could be wrong
- Image tracing: an image that is already in memory can be traced (no file is needed); the trace of a file uses the same code

Tcl Scripts:

- fixed: a script ran line by line, and when a line failed, the next lines were added to it and it was tried again; a script now runs as one unit, and nothing is tried again
- fixed: a script with an incomplete command (for example a missing brace) ran its first part; it now runs nothing and shows "Incomplete Tcl script; no commands were executed."
- fixed: an error in a script did not tell where it happened; the error message now has the file name and the line number (for a Script object, the cursor goes to the line with the error)
- fixed: the Tcl command puts wrote to the real standard output; puts (also puts stdout and puts stderr) now writes to the Tcl Shell, and to the console in the headless mode; puts to a file channel does not change
- fixed: a script file with a BOM (UTF-8 signature) was read with the BOM characters in the first line; script files are read as UTF-8 with or without a BOM
- fixed: a script file given on the command line (shellfile) stopped the application with exit code 2 when it could not be read or a command failed; the error is now shown and the application keeps running
- fixed: a script given on the command line ran with the exec_command of the Tcl Shell; it now uses the same code as Run Script, so the errors have the file name and the line
- new: a running script can be stopped: Abort stops the Tcl command that runs, also a loop in pure Tcl (Tcl 8.6 only; if the Tcl library can not be found, a warning is written to the log, and a pure Tcl loop can not be stopped)
- a script that is stopped or that fails does not roll back the changes that it made; the message says this
- a new script, a Tcl Shell command or a Reset is refused while a script runs ("A script is already running.", "Cannot reset Tcl while a script is running."); the same while work that timed out still runs ("Timed-out script work is still running; changes are not rolled back.")
- Close, Restart, Open Project and the delete of objects are refused while a script runs ("Stop the running script before closing / restarting / deleting objects."); in the headless mode the close is not refused
- headless mode: the Tcl errors of a shellfile or a command show the file name; when the input ends in the middle of a command, it reports "incomplete Tcl command at end of input" and the exit code is not 0

Script Object:

- fixed: Save of a Script object always asked for a file name; it now saves to the file that it was opened from or saved to, and the Save button has a menu with "Save As"
- fixed: a Script object was saved with a text that was not the text of the editor; the save now uses the text of the editor and writes it in one step, so a failed save does not damage the old file (the error is shown)
- fixed: the file name of a Script object was lost when the project was saved and opened again; it is now kept in the project
- fixed: an edit of a Script object did not mark the project as changed, so the project closed without the question to save it
- fixed: the delete of a Script object left its editor tab open; the tab is now closed with the object (also with Delete All)
- fixed: Run of a Script object used the text from before the last edit; it now runs the text of the editor, and a Script object of another project does not run ("Activate the script's project before running it.")

Text Editor:

- fixed: the Find entry searched the whole text at each key press, and the editor was slow with a long text; the search now starts 150 ms after the last key press
- fixed: the highlight of the Find text moved the text cursor and changed the view; it now leaves the cursor in place
- fixed: Save of a text wrote the text line by line; it now writes it in one step

Main Window:

- fixed: the splash image and the main window opened on the primary screen of a computer with more than one screen; both now open on the screen of the mouse cursor, with the scale of that screen; a saved window position that is outside this screen is moved to the center of the screen (and the size is made to fit)
- fixed: at quit, a redraw that was still waiting could run after the canvas was closed and printed the error "Gloo has no canvas"; the waiting redraws are now stopped before the canvas closes
- the splash image is changed (light and dark theme)

Cutout:

- fixed: when the Freeform or Rectangular cutout failed or was stopped, the name of the new object stayed reserved, and the next run got a number added to the name; the names are now released
- fixed: Manual Cutout with mouse bites: the row of drills of an inner contour (a hole in the board) was on the wrong side, outside the cutout line; the row is now always on the side of the board material

2-Sided:

- fixed: a second run with another output Excellon object left the alignment drills also in the object of the earlier run; the drills are now in one object only: they are removed from the earlier object, and an earlier output object that has only these drills is deleted (one Undo step restores it)

Geometry:

- the circle steps count the whole circle in all the geometry code: the circles, the buffers of the geometry, the drills and slots of an Excellon object and the CNC job buffers have the number of segments that the setting gives (before, 4 times more)
- fixed: the buffer of a Geometry object changed the object also when it failed; the whole result is now made first, and an open line with a negative distance, a closed contour or a geometry that the buffer removes, a distance that is not a number, a factor of 0 or less, a stop by the user or any other error gives "Failed." and the object does not change
- the buffer of a closed contour with a negative distance gives the exterior of every piece (a split contour is not dropped)
- the buffer of a multi-geometry object buffers the aggregate and every tool in one call, and "Buffer by factor" scales each geometry around its own center

Build:

- the frozen build finds the preprocessors in lib/ next to the executable
- the release check of a frozen build needs the bundled default preprocessor and the compiled translations, reads every bundled translation file to find a damaged one, and checks in the running application that the default preprocessor is loaded and the translations are found
- the macOS package is named "FlatCAM Evo"; its version and build number come from App.version and App.version_date (for example 1.0 of 2026/10/25 gives 1.0.0, build 2026.10.25)
- Linux install (make install / make remove): the program, the launcher, the desktop entry and the MIME file are named flatcam-evo (they were flatcam-beta); the launcher uses the .venv of the project if there is one; a local install puts the launcher in ~/.local/bin; the manual page is installed and removed too; the paths with spaces work; the Python version check of the Makefile is removed
- the manual page (flatcam-evo.1) has the name flatcam-evo and describes --command, --headless, the exit status in headless mode and FLATCAM_DATA_PATH
- the update archives have the version in their names: the Windows asset of a GitHub release is flatcam-evo-<version>-win64-update.zip (make_freeze.py --zip takes the version from App.version); the updater takes the asset whose version is the version of the release tag (v1.1 and v1.1.0 both take flatcam-evo-1.1-win64-update.zip) and does not use the archive of another version
- Digi (legacy): Prepare Update Files writes flatcam-evo-<version>-win64-update.zip and flatcam-evo-<version>-source-update.zip; the manifest.json of each channel names its archive, so the installed versions find the new names

Tests:

- the tests that read source code or that only pinned the design are removed; the tests of the new work are in the topical files
- the lifecycle tests of the G-code Sender are skipped while the plugin is disabled
- the tests of the parser, G-code import, preprocessor, transform and editor work are in the topical files; the tests that only drove the design are removed, and near duplicates are merged into scenario tests (5360 -> 4919 tests)
- the 4 flaky tests (the view cube drag, the view keys, the project load that never ends and the late Tcl completion) are removed
- the flaky 3D canvas test of the grid toggle (it compared the pixels of a real OpenGL render) is removed
- the Excellon parser tests fix the class defaults, so a headless application in the same test worker does not change their result

Translations:

- the new texts of Isolation, Non-Copper Clearing, Paint, Follow, Extract and the other plugins are translated in 9 languages
- the new texts of the circle preferences, the parsers, Open G-Code, Transform, Move, Distance, the editors, the font parser and the Tcl scripts (140 texts) are translated in 9 languages, as short as possible, with common abbreviations
- the texts of the Gerber Editor "Load" preference and of the dimension edit check are translated in 9 languages

24.10.2026

Release:

- version 1.0 of 2026/10/24 is no longer marked as beta

Etch Compensation:

- fixed: the lateral offset was thickness x etch factor; it is now thickness / etch factor (etch factor = depth etch / lateral etch); the Etchants list had the same error
- fixed: the offset ignored the app units; in an IN project it was about 25 times too large
- fixed: the aperture size, width and height grew by the offset; they now grow by 2 x offset, the same as the copper; a negative offset does not make a size less than 0
- fixed: the clear (dark/clear polarity) areas were not compensated; they now shrink by the offset
- fixed: the buffer used the circle steps as segments for each quarter, so round corners had 4 times the points
- fixed: the source Gerber object was changed by the plugin
- fixed: an etch factor of 0 crashed the plugin; a factor of 0 or less now shows an error; an offset of 0 shows a warning and makes nothing; a done run shows the output and the offset in um and in app units
- fixed: "Fe3Cl" is now FeCl3; each etchant has its own etch factor (CuCl2 3.0, FeCl3 2.5, alkaline baths 4.0), shown in the list
- the compensated copper is a flat list of polygons
- the work runs in the worker pool; the copper is buffered once instead of polygon by polygon; fewer copies of the geometry
- new: Preview button that draws the outline of the compensated copper
- new: a "Lateral offset" label shows the offset of the current values
- new: "Use all selected Gerber objects" makes one output for each selected Gerber; one Undo removes them all
- new: Etch Compensation preferences (thickness, ratio, etch factor, etchant, manual offset, name suffix); the last values used are kept for the session
- new: Tcl command etch_compensate
- the panel has titled sections; the tooltips match the options

Start:

- the splash screen is not shown when the app starts with a file to open, for example a double-click on an associated file; this is true also when the file goes to an app that is already open
- the splash image is scaled to the physical pixels of the screen; on a screen with display scaling above 100% it is no longer blurred; new splash images
- a second launch with files now hands them to the open window on Windows, Linux and macOS; the old pipe thread (Windows only) is replaced by a local socket for each user and installation, with JSON messages instead of pickle
- the second launch hands off its files before the app is built, so it ends fast; it sends absolute paths and no options; a launch with --shellfile, --command or --shellvar, or with no files, starts a new window
- the open window comes to the front when it gets the files of a second launch
- a bad message to the socket no longer stops the listener for the rest of the session; a second launch with no files no longer takes or deletes the socket of the open instance
- all the file arguments open; before, only the first Gerber, Excellon or G-code file of a launch opened
- macOS: a file opened from the Finder (double-click, Open With) opens in the app, also while the app starts
- quit and restart close the socket directly; the restarted app is again the instance that gets the files of a second launch
- the listener starts before the app is built, so the files that a second launch sends while the first window starts are kept and open when it is ready; they open after the files of its own command line
- fixed: files opened together in the Explorer start more than 1 process at the same time, and 2 of them could both become the main window (on Windows 2 pipes can have the same name); a lock file now decides which process listens, and the others try to connect for a short time
- fixed: when the open window did not answer in 10 s, the second launch opened the files itself and the open window opened them later too; each request now has a deadline and a late request is dropped; a sender that closes before the answer is not served
- fixed: the wait for the answer had no total limit and could miss an answer that had already arrived
- if the open window does not answer, the new window shows a warning that the files open in it
- the second launch makes relative file names absolute against its own folder; file:// URLs (some Linux launchers) become local paths
- Windows: the socket name includes the session, so 2 remote desktop sessions of the same user do not share it
- files dropped on the main window open the same way as the files of the command line: projects in order, the other files by their type; before, the drop on the main window was off
- the file type of an argument comes from the last extension only; before, a Gerber named top.tcl.gtl, or a file in a folder board.tcl, ran as a Tcl script
- SVG, DXF and PDF files dropped on the main window are imported

Open Project:

- an open from the GUI, a double-click or a second launch adds a new project tab; Tcl and the command line replace the active project
- a failed open removes the tab that it added and shows the previous project again; the reason is shown in a dialog, or in the Tcl error
- one project loads at a time; a second open during a load is refused with a message
- an object that cannot be loaded no longer stops the load: the other objects load, and a dialog lists the skipped objects; the file name is not kept, so a Save does not overwrite the file with the missing objects
- the progress dialog shows the count of the loaded objects and has a Cancel button
- the units of the project become the units of the app (status bar too); a switch of the project tab also changes them; the length options of a new object are converted to the units of its project
- a project from a newer app version gives a warning; a project with invalid units ignores them
- a project whose imported source files are missing tells which files; "Locate folder..." relinks them to the files of the same name in a folder (only for objects imported with this version)
- a saved job that names a preprocessor that is not installed uses the default preprocessor, with a warning
- an older Script or Document object with missing attributes loads
- an object loaded from a file that has the history id of another object gets a new id
- faster: a project file writes each distinct geometry 1 time in a geometry table, and all the geometries are decoded in 1 call; the stored bounds and buffered Gerber geometry are used; the canvas redraws 1 time at the end of the load; the Properties tab is built 1 time
- WARNING: a project file saved by this version cannot be opened by an older version; this version still opens the older files
- a project file larger than 4 GiB after the decompression is refused
- Tcl open_project waits for the end of the load and returns the names of the objects that could not be loaded

File Associations:

- the associations work on Linux (MIME types and a desktop file in ~/.local/share) and on macOS (an app bundle in ~/Applications, registered with lsregister); before, they were only on Windows
- new "FlatCAM Files" group in Preferences -> File Associations: register or unregister the .FlatPrj and .FlatConfig files
- Windows: the files use their own program ids (FlatCAM.Project, FlatCAM.Config) with an icon; an unregister removes only the extensions that still point to FlatCAM
- make install also installs the MIME types; the desktop file opens files (%F)

Project Tab:

- each object with plot colors has a color swatch at the right side of its row; a click on it opens a color dialog with alpha; if the object is selected, the color goes to all the selected objects, else only to this object; the click does not change the selection
- a color change from the context menu also updates the swatch
- a right click on a category row (Gerber, Excellon, Geometry, CNC Job, Script, Document) shows a menu with: Show All, Hide All, a separator and Open; Show All and Hide All work on the objects of that category; Open shows the icon of the category
- Open of a category: Gerber, Excellon and Script open their files; Geometry opens SVG or DXF files by the file extension; Document opens FlatDoc files (new); CNC Job has no Open
- the objects can be moved by drag and drop inside their category; a drop on the category row moves them to the end; a drop in another category is refused; the move marks the project as changed
- with "Store colors" on, a Gerber object that moves to a new row takes the colors of that row from the Gerber color list
- a click on the color swatch of a row that is not selected makes that row the only selected row; on a selected row it keeps the selection
- the category rows have no expand arrow; a double click expands or collapses a category
- files dragged from the OS onto the Project Tab are opened, the same as a drop on the main window

Toolbar:

- the Snap to grid button shows a status icon when it is on; the icon of a name that ends in "_status" is not recolored by the theme
- the File Toolbar is off by default in all the layouts (Standard, Compact, Minimal) of the GUI Preferences; the user can show it again from the toolbar context menu, and a saved toolbar arrangement keeps it

Canvas:

- the shapes, the selection overlay, the drag rectangle and the HUD rectangle set their own alpha blend; before, they could use the additive blend of the grid and show colors that were too light
- new "Axis Width" option in Preferences -> APP Settings (1 to 10 px); the axis color can have an alpha (#RRGGBBAA); an old 6 digit color keeps the old transparency
- when the axis is on, the grid lines at X=0 and Y=0 are not drawn, so they do not show beside the axis at some zoom levels
- new HUD section in Preferences -> APP Settings: Position (TL, TR, BL, BR), Text Color (else a color that contrasts with the canvas), Background and Offset from the canvas edges; the HUD moves to its corner after a resize of the canvas; the changes are applied immediately
- the cursor width is applied to the big cursor too; a change of the cursor color in the Preferences is applied at once to the small and big cursor; the "Cursor Color" checkbox off gives the theme color
- new defaults: axis color #AA0000FF, cursor width 1, cursor color #0055FFFF
- fixed: after an edit and a save in an editor, the canvas cursor could stay hidden while the snap still worked; the cursor hid on the Leave of the canvas and showed only on an Enter that did not come; now a mouse move over the canvas shows it

Align Objects:

- Beginner/Advanced level button; in Advanced, "Limit pairs" sets the number of source points and fit pairs of the Auto mode (0 = no limit); all features still check the result; the result line shows the number of fit pairs
- the table and the errors of an Auto result show the pairs that made the fit

Title Bar:

- new "Hamburger Menu" option in Preferences -> GUI Preferences (on by default); when it is off, the custom title bar shows the menu bar all the time instead of the hamburger button; the change is applied immediately

Preferences:

- General -> Updates: the Digi share URL and the Prepare Update Files button show only when the update source is Digi (legacy)
- GUI Preferences: the check boxes are in 2 columns (Custom Title Bar | Hamburger Menu, Hover Shape | Joined Redraws, Selection Shape | Selection Outline, Object AutoHide | Object Autoswitch); Hamburger Menu is enabled only with the custom title bar
- APP Settings: "Undo memory (MB)" is now "Undo RAM"; the tooltip gives the unit

Performance:

- the worker threads take the tasks from 1 central queue: a task goes only to an idle worker and no longer waits behind a busy one; the G-code stream of the GRBL Sender runs in its own thread, so it does not hold an app worker
- the process pool sends 1 color for each shape, not for each vertex; the result of 2000 shapes is 53% smaller (6.6 MB to 3.1 MB)
- a pool process starts in 0.35 s with 62 MB instead of 1.29 s with 168 MB: flatcam.py loads Qt and the app only in the main process, and a pool process uses 1 BLAS thread
- the process pool is made 1 time for the life of the app, not again on New Project; appPool.py and the dill dependency are removed
- fixed: an exception in a worker task shows in the GUI again
- the main shape collection of the 2D and 3D canvas joins the redraws: the update runs 30 ms after the last request, at most 150 ms after the first; plot_all of 50 objects: 25.2 s and 50 rebuilds to 9.2 s and 8 rebuilds
- new "Joined Redraws" option in Preferences -> GUI Preferences (on by default); the change is applied immediately
- the OR-Tools path optimization of a CNC job runs in the process pool, so the GUI no longer freezes during the search; an abort stops the wait
- the progress dialog of a file open is painted before the parse starts; before, the busy workers made its first paint late

Project:

- the geometry is saved with 20 decimals; small coordinates (below 1 mm) no longer lose precision after a save and a load

Editors:

- the checked radio buttons of the editor plugins are shown again in the Dark theme with the native styling; the editor plugins no longer set their own stylesheet in that case
- the X, Y and length text near the cursor goes on the side away from the last clicked point, so it does not cover the shape that is drawn; if shapes of the editor are under that side, the text goes to the next free side
- one function makes the cursor text for the Geometry, Gerber and Excellon Editors (it replaces 16 copies)

Markers Plugin:

- fixed: the cursor color is restored after a manual placement
- fixed: the plugin connects to the project selection signal only once; before, each run added one more connection
- fixed: the Reset button no longer rebuilds the panel inside its own click; closing the plugin ends a running placement and clears the preview
- the circles use the app circle steps for the whole circle (before: 4x more segments)
- the manual cross is centered on the clicked point; the cut depth of the check Excellon follows the units
- the panel shows the units next to the values; the thickness and length accept small values

Fiducials Plugin:

- fixed: Cross and Chess fiducials are exported (and saved in the source) with the same shape as on the canvas; before, a Cross was exported as a dot and a Chess as 2 overlapped squares
- fixed: Add Soldermask Opening puts the openings on the last added fiducials; with no fiducials added it shows a warning and creates nothing; before, it could create an empty copy
- fixed: Add Fiducial works on a Gerber object made from a DXF file
- fixed: the Line thickness field is enabled for the Cross type (before: for the Chess type)
- fixed: no preview shape stays on the canvas after the last manual click; the end of a placement (done, failed or cancelled) always restores the cursor and enables the notebook again
- fixed: the Reset button no longer rebuilds the panel inside its own click; closing the plugin no longer shows the exit message or redraws the source objects; a drag release does not add a point
- each Add Fiducial and Add Soldermask Opening is 1 Undo / Redo step (the new object)
- the circles use the app circle steps for the whole circle (before: 4x more segments)
- the Size accepts small values (it was limited to 1-3); the panel shows the units next to Size, Margin and Line thickness; the coordinate cells are read-only
- the Big cursor option is also in Preferences; the Line thickness tooltip in Preferences is fixed

Punch Gerber Plugin:

- fixed: an aperture macro pad is punched inside its own bounds; a macro whose first record is a clear no longer crashes
- fixed: clear flashes and the geometry outside the apertures are kept in the punched object
- fixed: a nested or MultiPolygon solid geometry is punched correctly
- fixed: with an Excellon source, the clear features are kept, only the selected holes are cut, and the slots are cleared
- fixed: a source with no apertures, or with nothing to punch, shows a warning and creates no object
- fixed: in manual mode, a second click on a pad removes its highlight; Select All has each pad only once; Escape ends the mode, and the next manual punch does not use the old pads
- fixed: a right click with no pads ends manual mode and creates nothing; an error during the punch enables the notebook again
- fixed: closing the plugin ends manual mode and clears the marks; the Reset button no longer rebuilds the panel inside its own click
- the circle steps option sets the smoothness of the holes (steps of the whole circle)
- the panel has titled sections for the Fixed Diameter, Fixed Annular Ring and Proportional methods
- fixed: with an Excellon source, a hole that only touched a pad was cleared in the export and erased copper outside the pad; now only a hole that is fully inside the pads is cleared

Copper Thieving Plugin:

- fixed: Escape during the Area Selection crashed; now it ends the selection
- fixed: every end of the Area Selection (done, Escape, right click, plugin close, Reset) gives back the canvas mouse handlers, removes the zone rectangles and enables Undo again; before, a failed or empty selection could keep the canvas in the plugin mode, and old zones were added to the next fill
- fixed: a second Add during the Area Selection no longer connects the mouse handlers twice; a pan or drag release is not a zone corner
- fixed: the Reset button no longer rebuilds the panel inside its own click
- fixed: Reference Object mode with no reference object shows an error and creates nothing; before, it used the Gerber object itself
- fixed: an empty Gerber, an empty fill, a robber bar thickness of 0 or an empty plating mask shows a warning and creates no object
- fixed: the robber bar works on a Gerber with MultiPolygon geometry, with no follow geometry, or with only region apertures; its new aperture number is 10 or more
- fixed: the plating mask job no longer reads or writes the panel from the worker thread; the busy indicator stays on while a job runs; the plating mask is no longer added to the recent files
- the thieving object and the plating mask have the follow geometry of the new copper
- the dots, the robber bar and the plating mask clearance use the app circle steps for the whole circle (before: 256 segments for each dot); the Circle Steps preference starts at 4
- the panel and the Preferences show the units next to each length; the sizes and the robber bar thickness accept only values above 0; the robber bar margin is not negative; the Clearance and Margin in Preferences accept values above 99.99

Check Rules Plugin:

- fixed: the clearance and annular ring rules crashed with Shapely 2 on any layer with more than one polygon, and the user got no report and no message
- fixed: an error in one rule no longer stops the other rules; the busy indicator always goes back to idle
- fixed: each check makes one result document; before, each opening of the plugin or Reset added one more
- fixed: the Solder Mask Sliver rule used the Silk to Silk checkbox; the Silk to Outline rule used the Copper to Outline value
- fixed: the Annular Ring, Silk to Solder Mask, Copper to Outline and Silk to Outline rules check both the top and the bottom layers and both Excellon objects; before, one of them was skipped
- fixed: Gerber clear (LPC) features are applied; before, most of the copper was not checked and touching copper was reported as a violation
- fixed: a layer with one polygon shows PASSED, not FAILED; a rule with nothing to check says so
- fixed: Trace Size uses the short side of rectangle and obround apertures and skips flashed pads
- fixed: the check no longer reads the panel from the worker thread; the layers are prepared in the worker, so a big board does not freeze the GUI
- the checks use a spatial index: a few seconds on a board with thousands of pads and holes (before: minutes)
- new rules: Minimum Slot Width, Hole to Outline, Hole to Copper and Copper Gap vs Isolation Tool (off by default), also in Preferences
- a results table lists each violation with layer, location, value and limit; a click on a row zooms to it; markers show the violations on the canvas
- the report shows the limit, the measured values and the units; the results can be exported as CSV
- rule presets (home etching, CNC milling, typical fab values) fill the rule values; a Cancel button stops a running check
- the panel shows the units next to each value, each rule has its own tooltip, the Select All checkboxes follow their rules, and the Run button tooltip is fixed

QRCode Plugin:

- fixed: in an inch project the clearance mask and the aperture had mm values; the mask was about 10 times too large and a negative QR made a large copper area
- fixed: the clearance mask cut only the copper polygons that contained it completely; now it cuts each polygon that it touches
- fixed: an export to PNG or SVG kept the app in the plugin mode and Undo / Redo stayed blocked; export errors are reported and a success message gives the file name
- fixed: the SVG export ignored the fill and back colors; the colors are checked before an export
- fixed: the placement started before the QR was made; data that did not fit, or a failed geometry, left the canvas in the plugin mode or inserted the QR of the previous run; now the placement starts only after the QR is ready, and errors are reported
- fixed: Reset, plugin close, a change to another plugin, Escape and a right click end the placement; the canvas mouse handlers and the previous mode come back; a drag release does not insert a QR
- fixed: a deleted target, a target that is not a Gerber or a Gerber in an editor is refused
- fixed: a fixed Version is used as it is; before, it grew without a message; the new Auto option selects the smallest version
- fixed: the new aperture keys have the same type as the keys of the object
- fixed: the Insert QRCode button is shown in Beginner and in Advanced mode
- after an insert, the bounds, the follow geometry and the apertures table of the object are updated
- the insert into a big Gerber is fast (0.3 s for 10000 polygons, before: about 8 s); a big QR is made in about 1 s (before: about 4 s); the preview redraws one time for each mouse move
- new options: Mask Pattern, Border 0, Rotation (0/90/180/270), Mirror, Clearance margin; the panel shows the version, the modules and the size in the app units, and warns when the border is below 4 or the module is below the minimum module size (Preferences)
- new Placement section: click, or X / Y coordinates; anchor (corners or center); Keep placing with a serial number that increments
- new Variables: {date}, {time}, {project}, {object} and {serial} in the QR data, with the serial start, step and zero padding
- new Output section: add the QR to the source object or make a new Gerber object; optional opening in a soldermask Gerber, in the same Undo step
- a preview image of the QR in the panel
- the new options are also in Preferences

Subtract Plugin:

- fixed: a subtractor that cut a pad or a trace into 2 parts stopped the Gerber subtraction with an error and made no object
- fixed: all the copper of the result went to aperture 0; now the pads that the subtractor does not touch keep their aperture, and the pads that are fully removed and the empty apertures are gone
- fixed: the result Gerber had no follow geometry; now it is kept, or trimmed where the cut goes through it
- fixed: the clear (hole) areas of the subtractor Gerber were ignored and the copper under them was removed
- fixed: a Geometry target made of one MultiPolygon gave an empty result when Close paths was off; nested lists, collections and points are now used
- fixed: a single-geo Geometry result kept the old geometry in its tools, so a CNC job made from a tool used the geometry before the cut
- fixed: each opening of the plugin added the signal connections again, so one click made more than one result object
- fixed: an error in the subtraction left the plugin waiting for ever and blocked the next runs; a second click during a run is refused; Cancel stops a run
- fixed: the deletion of the source objects was not in the Undo history; now the result, the deletions and the rename are one Undo step, and Redo works
- fixed: the objects were deleted and the widgets were read from a worker thread; the result was added to the recent files list
- fixed: a Geometry target could also be its subtractor; a missing object showed a wrong name or crashed
- faster: the shapes are found with a spatial index (2000 pads against 500 subtractor shapes in less than 2 s); the heavy geometry runs in the worker, not in the GUI
- one panel for all the cases: the target can be a Gerber, a Geometry or an Excellon object, with one or more subtractors of any of these types (Excellon drills and slots as holes)
- new: the operation can be Subtract, Intersect or XOR (not for an Excellon target)
- new: a margin grows the subtractor area; the circle steps of the target type are used
- new: in Advanced mode the apertures / tools of the target that change, and the apertures / tools of each subtractor that cut, can be selected
- new: output name; Keep target (off: the result replaces the target and takes its name); Delete subtractors
- new: Preview shows the subtractor area on the canvas; the result message gives the kept, changed, removed and added shapes; a subtractor without area (lines with no margin) gives a warning and no object
- new: the selection in the Project Tab sets the target and the subtractors; objects of a different unit are scaled to the target units
- the options are in Preferences; the old "Delete source" choice becomes Keep target off and Delete subtractors on

Milling Plugin:

- fixed: Copy made more than 1 copy of a selected row; Copy All gave the copies the same id; Delete and Delete All removed the geometry of the object
- fixed: a new tool from the Add button, the default add or the Tools Database cut with the diameter of another tool; the Tools Database values now go over the default values
- fixed: an Excellon tool table rebuild duplicated the tools with the same diameter and added the totals again; the Order combo changed the tools of the object
- fixed: an edit of the tool diameter left the table signals off; a diameter of 0 or less is refused
- fixed: a change of the level (Beginner / Advanced) changed the tool data; now it only shows or hides the fields
- fixed: a row click could change the Cut Z of another tool; a change of the tool shape reset the job type; the Cut Z of a V-shape tool and the V-tip edit are now in the same Undo step
- fixed: the close of the plugin removed the cleanup slots of the other plugins (also in Drilling); the signals could stay off or be connected 2 times
- fixed: the drag and drop of the Geometry rows did nothing; the Toolchange XY and End XY edits were not stored
- fixed: a negative Travel Z made the travel moves inside the material; a Depth per Pass of 0 hung the job; a Dwell of 0 crashed the G-code; 2 paths with the same start point lost 1 path
- fixed: the first-tool Start Z used the tool number, not the first tool of the job (12 preprocessors)
- fixed: each tool of a job now starts at the end point of the previous tool; the spindle direction comes from the tool
- fixed: a failed job made an empty CNC Job object; a tool with no usable geometry now gives a message
- fixed: a second Generate click during a running job could empty the tools of that job
- fixed: a milling bit too small for a slot-only tool cancelled the drill milling; all the tools with too small holes are told in 1 message
- the "Exclusion" checkbox now controls the travel around the exclusion areas (off by default; before, the areas always changed the travel); the area ids stay correct after a delete; a cut through an area gives a warning
- faster: the travel around the exclusion areas is about 20 times faster (390 s to 20 s for a mixed layout)
- faster: the Excellon milling paths are made in the worker thread, with abort and progress; copy and delete no longer copy all the tools for each selected cell
- new: Path Optimization type and OR-Tools search time fields (Advanced)
- new: warnings for wrong tool values; a diameter of 0 or less, or a Depth per Pass of 0 or less with Multi-Depth, blocks the job
- new: each parameter edit is 1 Undo step

Drilling Plugin:

- fixed: the G-code of an earlier job went into the next job
- fixed: the tools were keyed by the diameter instead of the tool id; sorting made duplicate tools
- fixed: the start and the end code were lost when the first or the last tool had no holes
- fixed: a failed generation crashed or left the app busy; now it shows an error
- fixed: the "No optimization" path did not work
- fixed: a positive Cut Z is now made negative, with a warning
- fixed: the travel after a toolchange now starts from the toolchange XY
- fixed: the Tcl travel Z after an Over-Z crossing did not go back to the user value
- fixed: the toolchange check read the mill keys from the drill data
- fixed: a bad value left the UI disconnected; the plugin removed the cleanup handlers of the other plugins; the rows of the exclusion table changed the wrong area; opening the plugin reset the per-tool values; the Overlap and Last drill fields did not show with "Drill slots"; selection, preprocessor and Reset bugs
- fixed: the exclusion areas of Milling were used in a Drilling job; each area now has an owner
- faster: the exclusion area shapes are cached; the TSP optimizer uses numpy; the drill buffers are made in one call; the tool data is not deep-copied for each tool; above 2000 points the OR-Tools route uses a greedy order
- new: peck drilling with a full retract after each peck; dwell at the bottom of the hole
- new: plugin fields for the spindle direction, fast plunge and fast retract
- new: a spot drill pass (needs toolchange)
- new: XY offset, mirror and array of the job
- new: more Excellon objects in one job ("Add objects"); with added objects the tool parameters are read-only
- new: drill only the holes inside drawn areas
- new: warnings for slots that are not drilled and for tools with the same diameter and different parameters
- Tcl drillcncjob: uses the per-tool values, converts slots to drills, has the GUI defaults and all the new GUI options (peck, dwell, spot, offset, mirror, array, objects, area and more)

Tcl Commands:

- cncjob: only the arguments that are given change the tool data; -dia only for an object with 1 tool; new arguments -offset_type, -offset_value, -job_type, -polish_margin, -polish_overlap, -polish_method, -optimization, -search_time, -area_exclusion and, for Excellon, -mill_dia, -milling_type, -overlap, -connect, -tools; it no longer empties the exclusion areas
- new command check_rules runs the Check Rules checks from scripts and in headless mode, with an optional report document and CSV file
- new command qrcode adds a QR code to a Gerber object or makes a new Gerber object, with the options of the QRCode plugin; it does not plot
- new command subtract_objects (alias subtract) subtracts, intersects or XORs the area of one or more objects from a Gerber, Geometry or Excellon object, with the options of the Subtract plugin; it does not plot

Resources:

- the Copper Clearing (NCC) icon is updated, light and dark themes

Translations:

- the new texts of Milling, Drilling, Open Project, File Associations and the start are translated in all 9 languages and the .mo files are compiled; the QRCode version message uses named placeholders

Tests:

- the tests use their own single-instance socket (FLATCAM_INSTANCE_NAME), so they do not send files to an open FlatCAM window
- a pytest run that ends with a live App no longer crashes at the exit (0xC0000005); the pool is stopped first
- the test window of the title bar tests closes on Alt+F4 or from the taskbar; before, a window left by a stopped run stayed on the screen and could not be closed

23.10.2026

Plugins:

- the single-file plugins are split in MVC, a controller, a view (UI) and a model (Gen) in their own folder in appPlugins/: Copper Thieving, Cutout, 2-Sided, Distance, Drilling, Etch Compensation, Extract, Fiducials, Film, Follow, Image, Invert Gerber, Isolation, Levelling, Markers, Milling, Move, Object Distance, Optimal, Panelize, PCB Wizard, Punch Gerber, QRCode, Report, Rules Check, Shell, Solder Paste, Subtract, Transform
- NCC, Paint and PDF Import get a separate model module (NccModel, PaintModel, PdfGen)
- 2-Sided: new Basic mode; alignment of all source objects, with a warning when 2 fits are almost equal; Mirror is 1 Undo step and restores the object if it fails; the alignment holes use the selected box object; invalid hole coordinates or diameter create nothing; the grid snap is restored after a pick
- Copper Thieving: the line fill stays inside the fill area; the dots/squares grid starts from the reference area; an Excellon reference is accepted; the plating mask is 1 region aperture that covers the pads, the thieving and the robber bar with the clearance, and the plated area is the area of that union; the thieving and robber Gerbers are exported as regions; a grid size of 0 gives a message that names the grid; the first corner of a zone is no longer moved twice by the grid snap; an object name with no extension gets "<name>_plating_mask"
- Cutout: the manual bridges can be edited: select, drag, Delete; Escape cancels the session; manual geometry cutout uses the inner contours when they are on; the inner gaps option is enabled only for the cutout type that uses it; Beginner/Advanced level change keeps the values of the current tool
- Paint: a polygon that cannot be painted is skipped with a warning and the job goes on; the options are read once at the start of the job; the Tcl -box selection works
- Align Objects: automatic alignment of an Excellon with a Gerber matches the holes with the pads: the score counts only the holes, so the SMD pads do not hide a bad fit; it works in both directions (Excellon or Gerber moves) and takes less than 1 s on a board of 1000 holes and 10000 pads
- NCC: the tool order of the UI is used; a tool diameter that is not in the tool table no longer makes the margin of the next tool too large; a tool that clears nothing gives a warning; in Tcl, an empty area gives a message; the rest machining no longer writes into the tool table

Tcl:

- a Tcl command that creates objects returns their names: one name as text, several names as a Tcl list; a script can chain the commands: set g [paint ...]; cncjob $g ... A command that creates nothing returns nothing, a command with its own result keeps it
- a new object named "fail" or "failed" gets a warning, because that name looks like a failure result
- select_project and close_project return nothing: the objects of the other project are not reported as new

22.10.2026

Tools Database:

- the tool library is now an SQLite file (tools.sqlite3) in the data folder; it stays the same across application upgrades. At the first start, the tools of the old tools_db_<version>.FlatDB are copied into it; the old file is not changed
- a save is 1 transaction: a failed save keeps the library as it was; a save from an editor that is older than the file on disk is refused, so a change from another instance is not lost
- new library view: search by name, diameter or shape, filter by operation, numeric sort of the diameter column; the properties show only the sections of the operation of the tool, with "Show advanced settings" for the rest
- when a plugin (Milling, Drilling, Isolation, Paint, NCC, Cutout) opens the library, it shows only the compatible tools; "Add selected tools" adds the selection; "General" tools are available in all plugins
- the lookup of a tool by diameter takes the exact diameter first, then a tool whose tolerance contains the diameter
- Add, Duplicate, Delete, Import, Export, Save buttons; Import adds the tools of a file, "Replace library from file…" replaces the library; the JSON export keeps the units, and an import in the wrong units is refused
- a tool with no name or with a diameter of 0 or NaN is refused at save
- a bad tool record is skipped, not fatal: the library shows it in red with the reason, and the editor, Save, Export and the plugins keep working with the other tools
- a failed first save does not leave an empty tools.sqlite3 that blocks the migration; the migration also takes the newest older tools_db_*.FlatDB when the file of the current version is missing
- an Import of the same file again does not add duplicate tools
- a plugin tells the user when the library is in other units than the application and was not used

Calculators:

- the Calculators plugin is split in Calculator (controller), CalculatorUI (view) and CalculatorGen (model) in appPlugins/ToolCalculators/

GUI:

- Windows: the title bar keeps the native resize frame and caption actions behind the custom title bar: drag, edge snap, double-click and the Windows 11 Snap Layouts on the maximize button work; the title bar is 30 px high; the restore after a maximized start goes back to the saved size
- Windows: the main window starts again at the size and position of the last stop (before, it grew by the native frame at each start); a quit from the updater or a Tcl command also saves the window geometry
- Shift+G, Shift+A, Shift+H and Shift+W (grid lines, axis, HUD, workspace) work in the editors and plugins too, but not in a text input; Shift+G and View -> Grid lines toggle the grid of the active plot (2D or 3D)
- the plot cursor shows only when the mouse is over the canvas
- the plot shapes stay visible when the plot tab is detached into a window and attached again
- grid lines off now hide the grid (before, the grid was painted white); in the dark theme the grid is light gray, not almost the color of the background; the grid is drawn below the axes
- the dark theme has no borders around the items of the status bar; the status bar has no size grip
- the spin boxes accept a value typed with its prefix or suffix (units, %); a text that is not a number keeps the old value instead of 0

Performance:

- the coordinate readouts and the HUD update at most 30 times a second
- the selection rectangle, the hover shape and the mouse previews of the tools and editors change only the vertices; they do not rebuild the shape collections at each mouse move
- the hover test uses the bounds of the objects, not a Shapely polygon

Measurement:

- in multi-point mode, the mouse move replaces only the preview segment; the committed segments stay

Editors (Geometry, Gerber, Excellon) - left panel:

- removed the Undo / Redo buttons from the panel; the Undo / Redo of the title bar, the Edit menu and the keys work in the editors. The history of the editor is a combo box in its own "History" section (in the Excellon Editor it was a small list)
- each group of controls has its own section with a title and a frame: Geometry Editor - Snapping, Coordinates, Paths, Inspect, Select by Size, Resize Circles; Gerber Editor - Parameters, Select by Size, Snapping, Tracks, Regions, Resize Circles, Inspect; Excellon Editor - diagnostics, filters, transform, native slot endpoints, snapping. The Parameters frame keeps only the parameters of the selection
- in each section: the label (with ':') is on the left, the value in the middle and the units on the right; the inputs come first, then the button that uses them, then the result. The label columns align across the sections
- Gerber Editor: the 5 material operations of Regions are in a grid of 2 columns; "Delete by Size" is below "Select"
- Excellon Editor: the Refresh button of the filters is a tool button with an icon; the Undo / Redo of the menu and toolbar are on only when there is a step
- Geometry Editor: the result of "Inspect selected geometry" is hidden until the first inspection (it left an empty row)
- Beginner mode hides the advanced sections, as before

Editors (Geometry, Gerber) - Selection Tool:

- drag the body of a selected object to move it (1 Undo step); away from the vertex grips, Ctrl+Shift+click adds a straight segment, Ctrl+Alt+click removes one, Ctrl+Shift+drag stretches the segments and Ctrl+Alt+drag pulls them with endpoint cords. Escape or a right click cancels the drag
- Gerber Editor: a drag of tracks rebuilds each trace before the storage changes; 1 drag is 1 Undo step

Geometry Editor:

- new Join in the toolbar: joins the selected lines into polylines with the Join tolerance of the panel
- new Polygonize in the toolbar and the menu: makes polygons from the selected closed paths or the segments that enclose an area; the unused open segments stay, and the endpoints must meet exactly

Gerber Editor:

- new Join in the toolbar: joins the selected connected track sections into 1 track
- Join accepts tracks with mixed continuous corners and end caps, skips duplicate records and keeps the selected shapes that are not tracks
- shorter toolbar and menu texts for Rectangle Region, Text Regions, Arc and Difference (Keep Cutters); the full name is in the tooltip

GUI:

- the checked tool of an editor toolbar has a blue background; the Exit Editor button of the toolbar shows "Exit Edit" under the icon; new power icon
- the editors use the displayed click position for the coordinate deltas and update both readouts
- a quit from an editor keeps the toolbar layout of the application, not the layout of the editor
- fixed a stray ')' in the "Diff. Keep" menu item of the Gerber Editor; "Exit Edit" is translated

Tests:

- test_app_refactor_integration patched App.info before PyQt built the meta-object of App, so the later tests on the same worker could not connect App.inform to info(); the meta-object is now built first
- the test suite is cut from 8383 tests in 358 files to 4393 tests in 314 files (full run 58-60 s instead of about 108 s with 8 workers): the tests that pinned the implementation (source text and AST, method manifests, private attributes, call order), the duplicates, the stage / ticket / audit scaffolding and the finished-refactor characterization are gone; parametrized matrices keep the cases that take different branches; ticket-named files are merged into topical files. E-Cut, Levelling and the G-code Sender keep their distinct behavior tests (features in development). test_ncc_followups and test_paint_final_cleanup (110 s together) are deleted
- the "unify" test files are gone: their shared helpers are in tests/helpers_objects.py, their tests in the Gerber / Excellon / object / project topical files
- new core gate tests/gates/run_core.py (all files except tests/gates/slow_files.txt) for each commit; the full gate run_gate.py before a push; new test budget tests/gates/test_budget.json (4850 tests, 350 files): above it the gates print a warning, they do not fail. The gate lists and the outcome baseline are rebuilt
- test policy in CLAUDE.md: fold the TDD tests into the topical file when a task is done, keep only tests of user-visible behavior
- conftest: after each test, a Ctrl / Shift / Alt state left by QTest key events is released (a plain click in a later test extended the selection); a hidden message box makes no Windows sound
- the Tcl tests try tkinter.Tcl() again when Windows fails to read init.tcl under load

21.10.2026

Security:

- fixed: a file given to FlatCAM on the command line (a double-click on an associated file, or a file sent by a second launch) got its type from a text found anywhere in its path, so 'top_copper.tcl.gtl' (a Gerber for Windows) or a file in a folder named 'board.tcl' was run as a Tcl script without a question; the type now comes from the last extension only, and quit, exit and save are commands only as the whole argument (a path with 'exit' in it closed the application, one with 'save' in it saved the preferences and opened nothing)
- a Tcl script (.tcl, .FlatScript) given as a file to open is opened in the Code Editor and runs only after a Yes to "Run this script?" (the default answer is No); --shellfile and the headless runs still run their script without a question

Undo / Redo:

- new Undo / Redo history for each project, outside the editors (the editors keep their own): Edit menu (the items show the next step), 2 buttons in the custom title bar before the main menu button, Ctrl+Z = Undo, Ctrl+Shift+Z and Ctrl+Y = Redo; the keys do nothing when the focus is in a text or number field
- recorded: Align Objects, the Move tool (1 step for all the moved objects), the Transform plugin (1 step for each action on all the selected objects; Buffer as a snapshot), the Edit menu Flip X / Flip Y, Rotate, Skew X / Skew Y, Set Origin, Move to Origin, Custom Origin, the Offset and Scale buttons of the object Properties, object delete, object create (a tool result or a file import) and rename
- the affine changes keep only the matrix (Undo applies the inverse); the other changes keep the old state with the Shapely geometries shared, not copied; Undo of a delete makes the object again with the same history id, so the older steps of that object still work
- limits for each project in Preferences > General > App Settings: "Undo steps" (default 100) and "Undo memory (MB)" (default 256); the oldest steps go first
- not recorded: Tcl commands, the headless mode and the project open; an editor save drops the steps of the edited object; Undo / Redo wait while a task runs and are off while an editor is open
- the history is not saved in the project file
- Exit Editor (Geometry, Gerber, Excellon): "Save" updates the edited object in place (same name, place in the project and options; the Excellon tools keep their data) and is 1 Edit step in the history; "Save as New" leaves the object as it was and makes a new "_edit" object

Align Objects Plugin:

- moved to the MVC pattern: appPlugins/ToolAlignObjects/ (Align.py, AlignUI.py, AlignGen.py)
- a click snaps through an STRtree index of the features (it was a Python loop over all features for each click); 1 click gives at most 1 point: the smallest feature under the click. Gerber flashes snap to the flash point, drills to the drill point, slots to the slot middle; if no feature is under the click, the point snaps to the nearest vertex within 10 pixels
- Geometry objects can be aligned and can be the aligner (snap to vertices)
- new modes: 2 points + scale (rotate, move and uniform scale) and Best fit (3 or more pairs, least squares) with the Rigid, Similarity or Affine model; a table shows the pairs and the error of each pair, and the RMS and maximum error show after the alignment
- the alignment is 1 affine matrix applied 1 time (it was an offset and then a rotate); only the aligned object is plotted again (it was plot_all())
- markers for the clicked points and a line for each pair; Backspace removes the last point, Enter applies the Best fit, Esc or a right click cancels
- the alignments are steps of the app Undo / Redo history (the plugin has no Undo / Redo of its own)
- new Auto mode: "Find" searches the rotation and the translation that put the most holes (or pads) of the aligned object inside the pads (or holes) of the aligner object; no clicks are needed. A histogram of the nearest neighbor angles gives the rotation, a vote of the offsets gives the translation, then an ICP refine with the selected fit model. "Allow mirror" also tests the mirrored object (bottom layer). The search runs in a worker thread; the result shows "matched / total", RMS and the pairs as markers before "Apply". If another solution has almost the same score (a symmetric pattern), a warning shows and "Next candidate" shows the other solutions. A board with about 2700 pads: about 1 s
- fixed: the rotation angle used atan and was wrong by 180 degrees for some point pairs; the rotation was skipped if only the X or only the Y coordinates were equal; a vertical pair raised a division by zero that was hidden
- fixed: overlapping pads or drills added 2 or more points for 1 click

Geometry Editor - vertex grips:

- in the Select tool, 1 selected shape shows grips on its vertices (filled squares) and on the middle of its segments (small hollow squares)
- drag a vertex grip to move the vertex; drag a midpoint grip to insert a vertex; Ctrl+click a vertex grip to remove the vertex; Esc or the right mouse button cancels the drag
- works on all rings (the exterior and the holes) and on multipart shapes; grid snap and corner snap apply during the drag
- the preview is red if the result is not valid; the release then leaves the shape as it was and tells the reason; if red is near an editor color, the opposite color is used (the same rule for the orange grips)
- each drag is 1 Undo step; shapes with more than 5000 vertices get no grips (use Edit vertices)
- Edit vertices dialog: new Part and Ring fields, so the dialog also edits holes and multipart shapes
- rounded corners and other arcs (found from the vertices: at least 3 equal segments with the same turn) show 3 grips: begin, middle (a diamond) and end, and no grips on their inner vertices; drag the middle grip to bend the arc; drag the begin or end grip to move that end (the arc stays through its middle point); Ctrl+click the middle grip to make the arc a straight segment; a full circle shows 4 radius grips; a new arc uses the "Circle steps" of the Geometry preferences; Explode and Edit vertices still use all the vertices
- with the grid on, a press takes the nearest grip within 1 grid step, so a vertex that is not on the grid can be picked with the snapped cursor

Transformation Plugin:

- rotate, mirror, skew, scale and offset of Gerber, Excellon, Geometry and CNCJob objects transform all the geometry (and all the Gerber apertures) in 1 vectorized call; they called shapely.affinity once for each shape and updated the progress once for each shape. eagle.GTL rotate 0.36 s to 0.12 s, scale 0.54 s to 0.07 s; 20000 Geometry polygons rotate 0.39 s to 0.03 s; Excellon with 20000 drills and 2000 slots rotate 1.22 s to 0.27 s, skew 2.52 s to 0.59 s
- the Excellon drill and slot shapes are made with 1 vectorized buffer for each tool
- the plot after rotate, mirror, skew, scale and offset maps the vertices that are already on the canvas (no new triangulation); 20000 polygons: 2.47 s to 0.12 s. Excellon scale and skew plot again, because the holes keep their diameter
- bounds() of Gerber, Excellon and Geometry objects is 1 vectorized call (about 5x faster); the plugin computes the bounds 1 time for each object (it was 2 or 3 times)
- 1 message for each action (not 1 for each object); a failure on 1 object does not stop the other objects, and the final message names the objects that failed; the CNCJob objects are skipped
- fixed: the 'Object' reference with a missing object raised an exception
- the shutdown no longer prints "QItemSelectionModel: Selecting when no model has been set" for the views that Qt destroys after their models

Canvas:

- the X axis keeps only the space for the ticks, the labels and a small gap (the height follows the font size and does not change on resize); the outer margin of 10 pixels is removed and the right gap is 8 pixels, so the plot area is larger

Milling:

- fixed: a tool with a Cut Z of 0 and a non-laser preprocessor showed the "Cut Z is zero" warning and then an internal error ('too many values to unpack'); the job now fails cleanly and no CNC job is made; same fix for the Geometry object CNC job (issue #40 reported by ErkanGench)
- fixed: the selection of a Milling target loaded the form with the form signals connected, so the values of the last tool were written into all the other selected tools (offset type, tool shape, job type) and the preprocessor could be reset from the Preferences; with the issue #41 tests (reported by ErkanGench) the form and the job now always use the values saved with the selected object and its tools

Drilling:

- fixed: with a laser preprocessor (GRBL_laser, GRBL_laser_z, default_laser, Marlin_laser_*) the G-code used full drilling power before the travel to the first hole of each tool; on a controller without the GRBL laser mode this burns a line across the board; the laser now uses the preprocessor's lift code for travel (the configured minimum power, or off when minimum power is zero) and drilling power only at the holes, like the Tcl drillcncjob command and the Milling tool; the G-code of the other preprocessors does not change

Gerber Editor:

- the Transform tool (rotate, flip, skew, scale, offset, buffer) changes all the selected shapes in 1 vectorized call for each geometry kind and plots only the changed shapes; the affine actions map the vertices already on the canvas (no new triangulation); 2000 pads rotate: 125 ms to 52 ms without the plot
- the reference point of the selection is 1 vectorized bounds call
- fixed: the buffer by factor scaled the 'follow' and 'clear' geometry about the center of the already scaled 'solid'; the Y offset and Y skew keys did the X offset and X skew; the Skew Y button did nothing; a transform did not mark the edited object as changed
- removed the second copy of the Transform tool from the editor module; the editor uses the plugin in grb_plugins (same layout as the Geometry Editor Transform tool)
- the corner modes of Track and Region use the real distances (not the rounded grid steps), so a point off the grid gets an exact 45 or 90 degrees corner; the modes also work with the grid snap off
- fixed: in the corner modes 1 and 2 an exact diagonal move kept the corner of a previous mouse position, so Region added a wrong vertex; Region added the same vertex 2 times on a horizontal or vertical segment; Track added a segment of zero length in the modes 3 and 4
- fixed: Backspace in Region always removed 2 points (now it removes the points of the last click); Backspace in Track did not remove the last segment; Region and Track always showed "Corner Mode 1" at the start
- new history of the editor ("Undo" and "Redo" buttons in the side panel; the labels show the step name; also from the menu, the toolbar and the keys): a step keeps only the changed records and the aperture data, not a copy of the board; 1 user action is 1 step (delete, add and edit aperture, draw, move, copy, erase, import, scale, buffer, simplify, polygonize and track edits); a failed action leaves no partial edit; the memory of the history has a limit
- new snaps on the FOLLOW lines (not on the solid outline): endpoint / bend, flash center, grid, and the new "FOLLOW midpoint snap" and "Nearest FOLLOW segment snap"; "Snap radius (px)" sets the pick distance; the "Snap:" label shows the active snap
- new "Features" list: it selects single records and follows the canvas selection
- new "Select by Size" (Width, Height, Solid area, FOLLOW length, Tolerance) searches the whole board; "Delete by Size" acts on the selection if there is one, else on the whole board; 1 Undo step (Undo also gives back the apertures); an invalid value keeps the selection
- Simplify of the selected shapes runs in the worker and writes all the shapes in 1 step on the GUI thread; the components, the aperture data and the selection are kept; the whole batch is refused if the tolerance is invalid, the geometry fails or the board changed during the work
- new FOLLOW operations that keep the circular sweep: "Split path" (click on a path; the width and the order are kept) and "Join paths" (paths with the same endpoints and the same width); a branch, an overlap or a gap is refused
- new "Resize" for the selected circular flashes: a matching aperture is used again, else a new aperture is made; a shared aperture does not change; 1 Undo step
- new "Move Track Bend": drag a bend of a track with a circular aperture; the neighbor bends stay; the width and the aperture are kept; the destination snaps; Esc or a tool change cancels the preview; a bend that makes the track invalid is refused
- new "Rectangle Region" and "Text Regions" (typed values): they make region material, not aperture sweeps; the rectangle has an anchor (center or 1 of 4 corners), square, round or bevel corners, a corner radius and a rotation; the text uses the font outlines with holes, a size in points and a rotation; 1 Undo step
- new arc modes for Track and Region, with preview and Undo; unsupported apertures and repeated or collinear points are refused
- the region operations (union, intersection, difference with "Difference (Keep Cutters)", explode) and Import plot only the changed records and keep holes, islands and the other records; Import takes polygons, multi-geometry, closed outlines and open paths and refuses the whole batch if 1 shape is invalid
- vertex grips and arc grips on the canvas for 1 selected shape (like the Geometry Editor), and the "Edit vertices" dialog (Part, Ring, Vertex / segment index; Move, Insert, Remove): a region edits its solid (ring 0 is the exterior, the next rings are holes); a track edits its FOLLOW line and the solid is made again with the same width; a flashed pad has no vertices; 1 Undo step
- new "Inspect selected geometry" (read-only) for the Solid, Follow or Clear component with an "Endpoint tolerance": it counts missing, empty, zero-length, duplicate, invalid and open records and near endpoint pairs; open FOLLOW paths are not errors
- fixed: decorated Qt slots (the edit tool, Scale) failed with a TypeError because Qt sends extra signal arguments; Scale from the menu did not start; Scale removed more canvas click connections than its own
- fixed: a move of a round FOLLOW pad marked the regions for export; the aperture table signal was connected again after an error; a failed import left the object hidden; the lists of marked shapes stayed after a delete
- fixed: translations and reshapes were not in the history; Scale and Buffer with no selected shape made an empty step; a buffer factor of 0 or less was accepted; a non-numeric aperture key broke the aperture codes; the intersection of separate shapes removed the source shapes; update_ui with zoom off moved the camera
- fixed: the editor, the save and the selection refuse changes while a worker runs; a result of an old revision or a closed session is ignored; the exit of a busy editor is refused before the dialog; save and load results are applied on the GUI thread only

Geometry Editor:

- faster selection: a selection change only hides or shows the changed shapes and copies their canvas buffers (no new triangulation); the tree rows are selected in 1 pass with the signals blocked; the area selection tests all the shapes in 1 vectorized call; area selection of 289 of 1000 shapes: 418 s to 0.02 s
- a click picks the nearest shape in 5 screen pixels (STRtree, like the Gerber Editor); a click on no shape clears the selection; inside nested polygons the smaller one is picked
- faster delete: the tree is built in 1 batch and only the deleted shapes leave the canvas; delete of 529 of 2000 shapes: 4.5 s to 0.12 s
- Simplify: the click selects, deselects (with the key) and clears like the Select tool; it no longer selects the same shape N times; the start shows the coordinates 1 time (it filled the text box once for each shape); the simplification is 1 vectorized call, 1 storage update and a plot of the changed shapes only; 529 shapes: start 1.4 s to 0.03 s, simplify 3.3 s to 0.3 s
- the editor loads the shapes in 1 batch: 5000 shapes 4.0 s to 0.4 s
- fixed: the save of the edits ran in a thread while the caller already read the bounds and plotted the object, so the plot could show the old geometry; the save now ends before the caller goes on
- fixed: a click selection was cleared at once by the info update; the vertex count of the selection failed on polygons
- the Transform tool (rotate, flip, skew, scale, offset, buffer), Union, Intersection, Subtract, Cut Path, Change Orientation, the Buffer and Paint sub tools and the Eraser change the selected shapes in 1 storage update, 1 tree build and 1 plot of the changed shapes (they plotted everything again after each shape)
- the Eraser finds the touched shapes with the pick index instead of a test of each shape; an eraser path that makes more than 1 polygon erases with all of them
- Copy makes the copies at the destination with 1 translation of all the selected shapes (the last preview could be stale or only a bounding box); the preview of an array shows only the bounding box when the number of copies is large, not only when the number of shapes is large
- fixed: Subtract with an open path as a tool used the previous tool geometry; the Buffer sub tool failed on a MultiLineString and used the old Shapely 'resolution' argument; Change Orientation skipped the LinearRing shapes and lost the shape data
- fixed: Move and Copy dropped the z coordinate of 3D shapes (translate_geometry)
- the Circle, Rectangle and Move tools select the shape under the click with 1 index query (they made 1 query for each shape)
- new corner modes for the Path and Polygon tools (from the Gerber Editor Track and Region): 1 = 45 degrees, 2 = reverse 45 degrees, 3 = 90 degrees, 4 = reverse 90 degrees, 5 = free angle (the default); T = next mode, R = previous mode; Backspace removes all the points of the last click. The corner math is shared with the Gerber Editor (appEditors/editor_corner.py)
- new "Select by Size" in the side panel: a Min / Max area for the polygons and a Min / Max length for the lines; "Select" makes the matches the selection, "Delete" deletes them (1 Undo step); it searches the selected shapes, or all the shapes if nothing is selected
- new Import tool (toolbar and menu): a click picks the shapes of other visible Geometry, Gerber and Excellon objects, a drag picks by area, a right click or Enter copies them into the edited object (1 Undo step), Esc cancels
- new "Resize Circles" in the side panel: the selected circles get the new diameter about their own center (type and vertex count are kept); the other shapes are skipped; 1 Undo step
- the editor Undo / Redo records each finished operation as 1 step with only the added, removed and changed shapes (a boolean of many shapes is 1 step; a preview makes no step; a new edit clears the Redo steps); the side panel has Undo and Redo buttons and a History list that goes to any state; Ctrl+Z = Undo, Ctrl+Shift+Z and Ctrl+Y = Redo (not when the focus is in a text field); the history starts again with each edit session
- the Path, Polygon, Circle, Rectangle, Move and Copy tools check the typed and clicked values: Enter before the first point, a zero length or a value that is not a finite number shows a message that tells what to correct and adds no shape; Esc removes the preview and leaves the shapes as they were; Backspace removes only construction points
- new "Midpoint snap" and "Nearest segment snap" in the side panel, with an "Acquisition (px)" distance on the screen (it needs the grid snap on); the closest candidate wins (equal distances: corner, midpoint, segment, grid) and a label shows the active snap; curves are straight segments for the snap (no exact centers or tangents)
- new "Edit vertices" button: move, insert or remove 1 vertex of 1 selected contour with typed X and Y values; the ring stays closed and the holes are kept; a removal below the minimum number of points is refused; 1 Undo step
- new "Split path": select 1 open path and click inside it; the path becomes 2 paths at the projected point; a click near an endpoint or outside the "Split tolerance" is refused; 1 Undo step
- new "Join paths": the selected open paths with endpoints within the "Join tolerance" become 1 path; a gap gets a straight bridge (no vertex moves); branching or ambiguous endpoints are refused; separate groups stay separate; 1 Undo step
- new "Inspect selected geometry": a read-only report of the selected shapes: zero-length paths, duplicate paths (also reversed), invalid or self-intersecting shapes, open paths and endpoints nearer than the "Endpoint tolerance"; nothing is repaired
- a save of the editor also makes a DXF source of the saved geometry (the object source); a failed DXF export keeps the previous source and tells that it is old; sampled arcs stay polylines in the DXF

CNCJob:

- R-tree path order of Geometry and Excellon jobs (appCommon/PathOrder.py): the walk goes on from the end where the tool leaves a path (it went on from the original last point after entering a path at its end), a grid with lazy delete replaces the R-tree in the walk, and a bounded 2-opt / Or-opt pass shortens the order; the same cuts and lifts; rapid travel on the sample board 8% to 29% shorter, 82% shorter on 20000 random polylines; the order step of 20000 paths: 5.1 s to 2.1 s
- the nearest path on a tie is the first inserted one, so the G-code does not depend on how an index was built
- the G-code of the paths, drills and points is collected in a list and joined 1 time; fixed: a job with 20000 polylines failed with MemoryError
- the R-tree storages are bulk loaded from NumPy arrays (insert_many(), new_rtree_index(points=..., boxes=...)): clearing, paint, connect, cleanup order, solder paste, panelize and the Excellon editor; about 3 times faster than an insert for each object
- fixed: exclusion areas: a travel move that started or ended inside an area grown by the tool radius and 0.1 mm went straight through the area at the travel Z, so the tool could hit the clamp the area was drawn for; a travel that crossed an area failed with an internal error ('MultiPoint' object is not iterable, Shapely 2); now the tool rises before the move ('Over') or goes around the area ('Around'; a travel that starts or ends inside an 'Around' area fails with a message); the areas are avoided in the order the travel enters them
- fixed: the drill point checks of the Drilling tool and of the Tcl drillcncjob used only the tool radius; they use the same grown areas as the travel moves, so a drill in the 0.1 mm margin is refused and no travel starts or ends inside an area
- fixed: every leg of an 'Around' detour avoids all the grown 'Around' areas, including overlapping ones; 'Over' clearance is checked on the detour legs too, and overlapping travel intervals keep the highest Over Z until all their areas are cleared; an enclosed travel with no way out fails with a message

Preferences:

- fixed: a restart (Apply Language, Apply font size, Preferences -> Save with a new appearance, style, dark canvas or title bar, the icon color) killed the project save that the user had just confirmed: the save ran in a worker thread and os.execl() replaced the process at once, so the project file was not written; the projects are now saved in the GUI thread before the restart, the same way as on exit (each project with changes is asked about: Yes / No / Cancel) (merged PR #43 from ErkanGench)
- fixed: a cancelled Save As dialog, a failed save or a save that is still running (File -> Save, Save As) no longer restart the app; the setting is applied at the next start and the app goes on as it was (merged PR #43 from ErkanGench)
- a restart also waits for a Geometry Editor save that still runs; with an open editor or a running task, a project with changes is not asked about and the app does not restart (the message tells the reason)

Languages:

- fixed: the language names are written in their own language: Deutsch (was "Deutsche"), Português do Brasil (was "Portugues do Brasil"), Русский (began with a Latin P), Türkçe (was "Türk") (merged PR #26 from ErkanGench)
- fixed: an unknown or unavailable language in the settings (e.g. a name written by an older version) falls back to English; before, the last language of the list was used (merged PR #26 from ErkanGench)
- the fall back to English also writes English into the settings; before, Apply of English in Preferences compared with the old name and asked for a restart
- reviewed translations for all 9 languages: fuzzy entries (never shown) confirmed or corrected, missing texts translated, format errors and wrong translations corrected (de 1486, es 1042, fr 1305, it 894, pt_BR 574, ro 924, ru 1353, tr 1097, zh 1009 entries); each entry carries a "# auto-review 2026-10-09" comment and is listed for the translators in docs/translation_review/ (merged PR #39 from ErkanGench)
- the string template and the catalogs are updated from the current code: 3943 strings, 192 new strings untranslated, 2 old translations used again
- fixed: the fuzzy translation of "The laser power when the laser is travelling." had the text of the probe feedrate (de, fr, it, ru, zh); it is now translated
- the 635 untranslated texts of each language are translated (machine translation, reviewed by a second model, checked with scripts/i18n_tool.py) and the fuzzy "Calibration" entry is corrected (de, fr, ru) or confirmed (pt_BR); all 9 languages are now 100 % translated; each entry carries a "# auto-review 2026-10-21" comment and needs a native translator's approval

Excellon Editor:

- fixed: the save of the editor gave a new slot diameter the tool number after the last matching tool, so it could overwrite an existing drill tool (drills 0.8 / 1.0 / 3.2 mm and slots 1.0 / 1.5 mm: the 3.2 mm tool became a 1.5 mm tool with the 3.2 mm holes in it); a new slot diameter now gets a new tool number after the highest one
- fixed: the save of the editor made the tools with only slots without the 'drills' key and the tools with only drills without the 'slots' key, so the Excellon export (and the object source) of an object with slots failed with KeyError: 'slots' and the Tcl drillcncjob failed on a tool with only slots; every saved tool now has both lists, like a tool of a parsed Excellon file
- new history of the editor: the editor keeps its own Undo and Redo list (Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y; the text fields keep their own text undo); each operation is 1 step and keeps only the changed drills and slots, not a copy of the whole board; a failed operation goes back to the old tools, counters, geometry and order; an operation that changes nothing adds no step and does not mark the editor as changed; a click on a row of the history list goes to that step
- the editor draws only what changed: select, deselect and local edits keep the canvas buffers of the unchanged drills and slots; the inspector keeps its measurements and table rows until the storage or the filter changes
- the editor snaps to a local cached index of its own drills, slots and endpoints (not the Geometry Editor storage); the index follows add, delete, Undo and Redo; the grid snap keeps its priority
- new "Feature selection / filters": filter the drills and slots by type, diameter range, slot length range and bounds, also for the selected features only; "Select matches" selects the result; "Delete matches" deletes it in 1 Undo step; a bad filter value changes nothing
- new "Feature diagnostics / inspection": a read-only list of the features with a "Tolerance" value; a slot shows its length, orientation and endpoints; empty, invalid and zero-length slots are marked
- new "Transform selected features": "Rotate", "Mirror X", "Mirror Y" and "Uniform scale (positions and diameters)" about the origin, the selection center or a point; the slot endpoints are kept (no polygon estimates); a scale that gives a new diameter moves those features to a new tool; the menu, the toolbar and the panel use the same operation; 1 Undo step
- new "Offset selected features" ("Offset X", "Offset Y"): the selected drills and slots move by the typed values; the diameters stay; 1 Undo step; a zero or invalid offset adds no step
- new click-select cycling: a second click at the same place selects the next stacked drill (A, B, A, ...); a drill on a slot comes before the slot; a click at another place starts again at the first one; a click on an empty canvas clears the selection; the tool table selects the row of the selected feature
- new slots keep their start and stop points: "Add Slot", the linear and circular slot arrays, Copy and Move keep the slot endpoints and rotate them with the geometry; save and load keep the endpoints in the document units and in the export units
- new "Edit slot endpoints" (Start X / Start Y / Stop X / Stop Y, "Apply endpoints") and "Drag endpoint" on the canvas: move 1 end of a selected slot; the width and the other end stay; the drag shows a preview, snaps to the grid and Esc cancels it; 1 Undo step
- the save and the other long jobs of the editor have a reservation: a late result of an old job does not change a new editor session; an edit or a second Save waits while a save runs; a failed job frees the editor; a save refuses a feature that is not a circle or a slot before it changes the object
- fixed: the editor applied the units factor (x25.4) again for an object in inches when the app used mm; load and then save now give the same file
- fixed: after New Project the editor had each menu action 2 times
- fixed: the tool table was not sorted after Scale and the next added drill did not select its row; the diameter of an added or edited tool is rounded to the decimals of the app; a tool add with a bad diameter changes nothing
- fixed: the history label of a Move was the generic one; an Undo or Redo that changed only the selection did not update the canvas; the feature table shows the translated type names; the diagnostics tolerance follows the current units; the corner snap button is hidden when the editor starts

Tests:

- new unit tests for the exclusion area travel moves and drill point checks (tests/test_exclusion_area_travel.py)
- new unit tests for the project save before a restart (tests/test_restart_project_save.py) (merged PR #43 from ErkanGench)
- new unit tests for the language names and the fall back to English (tests/test_language.py) (merged PR #26 from ErkanGench)
- new unit tests for the type of a file given on the command line and for the question before a script given this way runs (tests/test_startup_args.py)
- new unit tests for the tool numbers and the tool keys of the Excellon Editor save, with the Excellon export and the drillcncjob of the saved object (tests/test_exc_editor_slot_tools.py)
- new unit tests for the laser state during the travels of a Drilling job with a laser preprocessor (tests/test_laser_drill_travel.py)
- new unit tests for the app Undo / Redo history (tests/test_app_history*.py, tests/test_history_recovery.py, tests/test_history_gerber_export.py, tests/test_editor_save_modes.py)
- new unit tests for Align Objects (tests/test_align_objects.py, tests/test_align_apply.py, tests/test_align_auto.py)
- new unit tests for the vectorized transforms, bounds and plot (tests/test_affine_geometry.py, tests/test_geometry_transforms.py, tests/test_gerber_transforms.py, tests/test_excellon_transforms.py, tests/test_bounds_vectorized.py, tests/test_shape_transform.py, tests/test_tool_transform.py, tests/test_grb_editor_transform.py)
- new unit tests for the Geometry Editor: history, input checks, snaps, vertex edit, split, join, diagnostics, DXF source, corner modes, Select by Size, Import, Resize Circles and vertex grips (tests/test_geo_editor_*.py, tests/test_editor_corner.py, tests/test_vertex_ops.py, tests/test_vertex_grips.py)
- new unit tests for the Gerber Editor: history, snaps, feature selection, size select and delete, simplify, FOLLOW split / join / resize, track bend, typed drawing, region rendering, action parity, vertex edit and the defect fixes (tests/test_gerber_editor_*.py, tests/test_gerber_follow_ops.py, tests/test_gerber_track_bend.py, tests/test_gerber_drawing.py, tests/test_gerber_region_delta.py, tests/test_gerber_action_parity.py)
- new unit tests for the Excellon Editor: defect fixes, history, click select and Offset, render cache, snaps, workers, feature filters, selected transforms, slot creation and slot endpoints (tests/test_exc_editor_*.py, tests/test_exc_feature_filters.py, tests/test_exc_selected_transforms.py, tests/test_exc_slot_creation.py, tests/test_exc_slot_endpoints.py)
- new unit tests for the canvas axis layout (tests/test_canvas_layout.py)

20.10.2026

Security:

- fixed: opening a Gerber file, a project or the preferences, pasting from the clipboard or typing in an entry no longer runs Python code; the aperture macro expressions, the tool diameters, the toolchange and end X,Y values, the clipboard coordinates, the entry fields, --shellvar and the portable flag are read with safe_eval() (literals and arithmetic only) in place of eval() (merged PR #15 from ErkanGench)

CNCJob:

- fixed: the G-code file of a multi-tool drill job with a preprocessor without a header (Roland_MDX_20, Roland_MDX_540, ISEL_ICP_CNC) had no drill moves (merged PR #21 from gargamel778)
- faster CNC job from a Geometry, with the same G-code: the paths are simplified in 1 vectorized call on more than 1 thread (simplify_paths), the G-code of the paths is joined 1 time at the end, the preprocessor attributes are copied 1 time for each path, codes_split() compiles its patterns 1 time and the copies of the tool dicts share the geometries (deepcopy_keep_geometry); Tcl cncjob of a 6-pass laser isolation (2.7 MB of G-code): 9.94 s to 4.35 s; 40-pass laser isolation (6.0 MB): 18.47 s to 8.14 s; a 3-depth GRBL_11 job: 1.3 times faster (merged PR #30 from ErkanGench)
- Abort stops the CNC job also while the paths are oriented and simplified

Milling and Geometry Editor:

- fixed: Apply parameters to all tools read the Excellon tools table also for a Geometry target; it raised an error or copied the data of a wrong tool (merged PR #19 from ErkanGench)
- fixed: Copy in the Geometry Editor works without a cursor move after the reference click (merged PR #19 from ErkanGench)
- fixed: a CNC job made from a single-geometry object with 2 or more tools had the paths of the earlier tools again in the G-code of each next tool, so a laser burned them 2 times (27 cut moves instead of 18 with 2 tools); each tool keeps only its own G-code and the exported file has each path 1 time (merged PR #38 from ErkanGench)

Export:

- fixed: File > Export > Export Gerber lists each Gerber file type and All Files again: the ';;' separator and the '*' of the patterns were missing (merged PR #14 from ErkanGench)
- fixed: the viewBox of an exported SVG contains the drawing; a viewer showed an empty page (merged PR #23 from gargamel778)

Translations:

- fixed: the labels and tooltips of the General preferences are marked with _() where they are defined, so the extractor finds them; the Tools Database accepts an English label, its translation or its index
- i18n_tool: a bad PO string or line gives an error with the line number; bad brace formatting in a translation is an error of that entry

Machine Sender:

- new (experimental, not ready for production; bench tests only): E-Cut USB motion controller support (appPlugins/Common/ecut): connect, jog (also continuous: hold the arrow), streaming, spindle, inputs and outputs, soft limits and the motion settings of the board
- new: a manual tool change dialog; the spindle stays off until a spindle command
- new: the connected controllers stop (motion, spindle, outputs) when the application closes or crashes (appCommon/MachineSafety)
- fixed: GRBL reset waits for the controller banner in a worker; after a stop the controller asks for Home; the status report reads the accessory state (spindle, coolant)
- fixed: the 3D view of the sender with an empty path raised an error in VisPy
- hardware tests run only with FLATCAM_TEST_HARDWARE=1; the other tests cannot open a real HID device or serial port
- the GRBL modules are in the appPlugins/Common/grbl package: grbl_protocol (the parsers of the GRBL replies) and grbl_client (the serial client of the GRBL Sender, before ToolGrblSender/SenderGrbl.py)

Isolation Plugin:

- fixed: All and Area Selection generate isolation Geometry with Combine unchecked; separate passes use the Gerber object's geometry when no specific geometry is supplied (merged PR #16 from ErkanGench)
- fixed: the Optimal tool diameter fits in the smallest gap: the proposed diameter was the rounded gap, up to half a unit of the last decimal above the real gap, so Check validity rejected it with "Incomplete isolation." (merged PR #17 from ErkanGench)
- fixed: Isolation, NCC and Optimal give the same safe tool diameter: they measure the same geometry (no positive buffer) and round the gap to the app decimals; a gap of 0 or less shows "Could not find a safe tool diameter." instead of a 0 diameter; NCC keeps its recommendation below the real gap; Optimal clears the last results when it starts
- faster rest machining: the check buffers of a pass are found with 1 STRtree query instead of a test against every other polygon; on a board with 2432 copper polygons: 0.2 mm, 1 pass 41.4 s to 1.9 s; 0.1 mm, 6 passes 148.6 s to 5.0 s; the paths and the polygons left for the next tool are the same (merged PR #31 from ErkanGench)
- rest machining adapts to the density of the layout: for each pass a sample of up to 16 polygons selects bulk STRtree queries in limited batches (65536 possible pairs at most for 1 query) for a sparse layout, or a stop at the first collision for a dense layout; on a dense layout the PR #31 queries collected every pair
- measured with tests/benchmark_isolation_rest.py (synthetic square pads, median of 3 rounds, Python 3.14.3, Shapely 2.2.0, GEOS 3.14.1): sparse layouts (1000 pads, 3 mm pitch, 0.2 mm tool, 1 and 3 passes) keep the time of PR #31, about 5 times faster than before PR #31; dense layouts (3000 pads, 0.3 mm pitch, 1, 3 and 10 mm tool with 1 pass, 1 mm tool with 3 passes) are 1.9 to 48 times faster than PR #31 and faster than the code before PR #31 in every case; in the dense 10 mm case the largest query result is 397 KiB instead of 73 MiB
- the paths, their order and the polygons left for the next tool are the same in the 3 versions (compared by WKB, also on 96 random layouts); new tests check that a dense layout does not collect every pair
- faster isolation with many passes: the passes are buffered ahead in parallel threads, 1 pass per thread and 1 vectorized buffer for each pass (camlib.PassBuffers); Tcl isolate and the combined and separate passes of the Isolation Plugin use it, rest machining does not; the activity text shows the pass number; a board with 182 copper polygons, 0.1 mm tool, 12 threads: Tcl isolate 30 passes 26.5 s to 7.7 s, 100 passes 55.0 s to 13.4 s, the Isolation Plugin 30 combined passes 33.9 s to 5.8 s; the geometry and the G-code are the same (merged PR #34 from ErkanGench)
- the Passes of the Isolation Plugin, of the isolation preferences and of the Tools Database accept up to 9999 passes (999 before); the Tcl isolate command has no limit (merged PR #34 from ErkanGench)
- new: Boundary limits the isolation to the area enclosed by the outline of a Gerber or Geometry object, e.g. the Edge Cuts Gerber (Isolation Plugin, Tcl isolate -boundary and -trace_boundary); each pass is clipped to the area and the passes stop at the first pass that is completely outside it; the status bar reports the number of passes made, or warns that the area may not be cleared yet; if no pass reaches into the area no object is made and Tcl isolate fails; Trace boundary adds 1 path along the outline; on a 190 x 106 mm board 1000 requested passes stop after 126, with no toolpath outside the board (merged PR #35 from ErkanGench)
- new: Clear leftovers (off by default, tools_iso_clear_leftovers) adds 1 path along each strip of copper left between the passes where the passes around 2 features meet, for the full isolation with combined passes (Isolation Plugin, Preferences -> Isolation, Tcl isolate -clear_leftovers); on a board with 2432 copper areas and 6 passes the copper left goes from 109.2 mm2 to 1.99 mm2 (merged PR #36 from ErkanGench)
- fixed: Tcl isolate milled in the reverse direction: the climb milling type gave conventional milling and the conventional type gave climb milling; with a spindle that turns clockwise (M3), climb milling goes clockwise around the copper features, in the Isolation Plugin and in Tcl isolate
- fixed: conventional milling reversed only the outlines; the paths along the holes of the copper stayed climb milling (Isolation Plugin, rest machining and Tcl isolate)
- fixed: the paths that clear the leftovers between the passes follow the milling type
- fixed: with a boundary of more than 1 area, the first pass outside the boundary stopped the passes before they reached the other areas; the passes stop now when a pass encloses the whole boundary, and the report counts the passes up to the last one with paths inside the boundary
- fixed: with a boundary, the simplified paths stay inside the boundary, and the traced boundary respects the area selection and the area exclusion

NCC and Paint:

- faster seed clearing (NCC, Paint and the Milling of holes): the rings are cut 16 at a time, and a large margin (2000 coordinates or more) is cut without the holes that the rings of the band cannot reach; each ring is the same, bit for bit; NCC seed path generation on a board with 2432 copper polygons: 42.3 s to 10.5 s (merged PR #31 from ErkanGench)
- faster joining of the paths (paint_connect) with NumPy coordinate arrays: NCC standard 5.8 s to 1.6 s on the same board; the G-code of NCC and Paint is byte-identical (merged PR #31 from ErkanGench)

G-Code preprocessors:

- fixed: the final X-Y move is made at least at Travel Z when End Z is lower; the tool lowers to End Z only after reaching End X-Y, avoiding travel across the board below the configured clearance height (merged PR #12 from ErkanGench)
- fixed: Line_xyz, ISEL_ICP_CNC and Berta_CNC raised inside the preprocessor, so the start block or the tool change lines were missing from the G-code without an error (merged PR #22 from gargamel778)

Application updater:

- new: updates can be downloaded from GitHub Releases; Digi remains available as the legacy update provider
- fixed: update offers keep the provider they were checked with; existing custom Digi share URLs are migrated to the legacy provider setting

Laser G-Code:

- fixed: GRBL_laser, GRBL_laser_z and grbl_laser_eleks_drd emit explicit S0 for zero or unset cutting power, preventing laser activation from reusing the controller's previous power; intentional minimum travel power is preserved
- fixed: the Tcl commands cncjob and drillcncjob accept -spindlespeed as laser power when -las_power is omitted; an explicit -las_power takes priority (merged PR #13 from ErkanGench)
- new: Drilling and Milling G-Code generation (including the Tcl commands) warns when the 'laser OFF' commands retain a nonzero Min Power; the tooltips explain this behavior; the generated G-Code and intentional minimum-power behavior are unchanged (merged PR #11 from ErkanGench)
- fixed: the laser Min Power warning names only the tool whose G-code has the 'laser OFF' commands; a single-geometry job with more tools reported the Min Power of an earlier tool for the next one (merged PR #25 from ErkanGench)

Headless mode:

- new: flatcam.py --headless runs a FlatScript (--shellfile), Tcl commands (--command) or a Tcl prompt on stdin with no window and no OpenGL (Qt offscreen platform), for build servers and containers with no display; the output goes to stdout, the errors to stderr; exit code 0 done, 1 a Tcl command failed, 2 bad command line, N for 'exit N'
- new: --command="cmd1; cmd2" runs Tcl commands after the --shellfile, also in GUI mode
- in headless mode quit_app ends the run and the preferences of the user are not saved; a headless run never hands its arguments to an open FlatCAM window
- the frozen Windows build has FlatCAM-cli.exe, a console executable for headless runs
- fixed: the Tcl command interiors plotted its result; a Tcl command makes no plot (only plot_all and plot_objects draw), a test checks every command

Packaging:

- fixed: the application frozen with make_freeze.py (cx_Freeze) did not start: PyOpenGL loads its platform module (OpenGL.platform.win32) by name, so cx_Freeze left it out; the OpenGL package is now included
- fixed: the frozen application did not find its data: assets, locale, preprocessors and config are now copied into lib/, next to the modules, where the application looks for them (as the old freeze script did); config/configuration.txt was not copied at all
- fixed: in the frozen application cncjob failed with "No module named 'ortools.constraint_solver.routing_parameters_pb2'": OR-Tools loads its protobuf modules by name, so cx_Freeze left them out; the ortools package is now included when it is installed
- fixed: the frozen application logged "No translation file found for domain: 'strings'" at each start: it looked for the translations in library.zip first; the existing locale folder is now used at the first try

Tcl and command line:

- fixed: a packaged application started in its install folder (a Start Menu shortcut) wrote relative file names into the install folder, because with cx_Freeze the modules are in its lib/ subfolder; the folder of the executable (the .app bundle on macOS) now also counts as the application, and the home folder is used
- fixed: quit_app in a --shellfile hid the window but did not end the application: the script runs before the Qt event loop starts, and QApplication.quit() does nothing then; the quit is now also queued for the event loop
- fixed: join_geometry makes the same object as Edit -> Join: MultiGeo objects stay MultiGeo and the tools with the same diameter and type are fused (preference geometry_merge_fuse_tools); a mix of MultiGeo and SingleGeo objects is refused; before, the joined isolation of a real board gave 3 times the G-code (124214 G1 moves instead of 40732) (merged PR #29 from ErkanGench)
- fixed: Edit -> Join failed with "'Polygon' object is not iterable" or "'MultiLineString' object is not iterable" when a tool geometry was not a list (bbox, cutout) (merged PR #29 from ErkanGench)
- Join fuses only the tools whose machining parameters (tools_*) are equal; a SingleGeo tool with an empty geometry of its own is kept

Milling Tool:

- fixed: the CNC job of a Geometry from an older project failed with a KeyError ('tools_mill_laser_on' for a FlatCAM Evo project saved before 12.2023, 'tools_mill_tooldia', 'tools_mill_offset_type' or 'tools_mill_job_type' for a FlatCAM 8.994 project); the Milling tool and the Tcl cncjob command give a tools_mill_* option that is missing from the tool data the value the tool keeps under its older name (FlatCAM 8.994: cutz, travelz, feedrate, spindlespeed (also the laser power), spindledir (also the laser ON code: CW is M03, CCW is M04), ppname_g ... and the tool's tooldia, offset, offset_value, type, tool_type; FlatCAM Evo before 12.2023: tools_mill_z_pdepth), and the application value only when the tool has neither; a saved 8.994 preprocessor that was renamed (grbl_11, line_xyz, Z_laser) gets its current name, and a saved preprocessor that the application does not have counts as absent (a job that uses the preprocessor of the tool gets the application one, with a warning; a multi-geometry job of the Milling tool uses the one of its form, as before); the options already in the tool data and the source object are not changed (merged PR #18 from ErkanGench)
- fixed: the form of the Milling tool shows the values that a Geometry from an older project keeps under older names (the values that its CNC job uses); opening the tool does not change the tool data of the object

19.10.2026

DXF and SVG Import:

- new DXF importer (appParsers/DXF package, dxf_to_shapes) replaces ParseDXF.py and ParseDXF_Spline.py; it is built on ezdxf
- the DXF importer reads HATCH (with holes and islands in the holes), 3DFACE, MTEXT and MINSERT arrays, in addition to POINT, LINE, CIRCLE, ARC, ELLIPSE, SPLINE, LWPOLYLINE (bulges, closed), 2D and 3D POLYLINE, SOLID, TRACE, TEXT and ATTRIB
- DXF text (TEXT, ATTRIB and MTEXT) has the stroke, outline (the holes of the letters stay) and none modes; the text uses ezdxf text2path and does not need matplotlib; an unknown mode logs a warning and uses stroke
- DXF blocks: nested INSERTs with the full transform; an entity on layer 0 in a block takes the layer of the INSERT; a circle in a block with a non-uniform scale stays filled; ATTDEF is skipped
- fixed: the DXF extrusion (mirrored arcs) is applied; invisible entities, invisible ATTRIBs and layers that are off or frozen are skipped; 1 bad entity does not stop the import; very large radii stay finite
- the DXF curves are flattened with a tolerance of 0.01 mm in the $INSUNITS of the drawing, instead of a fixed number of points (100 for a circle)
- new SVG importer (appParsers/SVG package, svg_to_shapes) replaces ParseSVG.py; it reads a path, text, bytes, a stream or an lxml element; a file that cannot be read gives SvgImportError; the XML is parsed without entity resolution and without network access
- SVG paths: all the commands, absolute and relative, smooth curves, arc flags and radius scaling, implicit lineto and compact numbers; a syntax error stops only that path and keeps the part that was read
- SVG units: px, pt, pc, mm, cm, in, Q, em and %, the viewBox with preserveAspectRatio, nested svg viewports and an optional user units mode (the old parser refused em, ex and %)
- SVG fill rules nonzero and evenodd, with holes and islands; the strokes are buffered with the linecap, the linejoin and the miter limit, scaled by the transform and the units; vector-effect non-scaling-stroke is supported
- SVG CSS: style sheets with class, id, tag, comma list and descendant selectors; the cascade and the inheritance with inherit, initial, unset, currentColor and calc(); paint none or transparent, display:none, visibility:hidden and zero opacity are skipped
- SVG transforms: the transform attribute, the CSS transform property and transform-origin, with skew; defs, use (nested, with a recursion limit), symbol with viewBox, switch and a; unknown and Inkscape elements are ignored; 1 bad element does not stop the import
- SVG text uses ParseFont: tspan, text-anchor, collapsed whitespace and generic font families; the Gerber import gets only polygons
- the SVG import of simple fills is done in vectorized batches (a test with 20000 elements); the circle resolution follows the circle steps preference
- the Geometry, Gerber and QR Code imports use the new importers; the unit, viewBox, flip and text code in each caller is removed

Image Import:

- new Load Image button with a status dot (gray: no image, green: loaded, red: failed); the image is loaded and checked with PIL; Import Image is enabled only after a good load
- the DPI value of the image file is put in the DPI field; if the file has no DPI, the field keeps its value; the minimum DPI is 1
- fixed: a bad image file clears the last selection and shows an error; Cancel keeps the current selection; the tool resets the loaded file and the status dot when it opens again
- the trace and the import use the loaded file with the current settings, without a second file dialog; the traced SVG is imported with the new SVG importer

PDF Import:

- new parser of the content stream (ParsePDF.py): it reads the operators token by token and keeps the graphics state with a q/Q stack and the full CTM; rotated, skewed and scaled artwork is imported correctly
- the fills use the nonzero winding rule (f, F, B, b) and the even-odd rule (f*, F*, B*, b*); holes and islands are correct
- new CMYK (k, K) and gray (g, G) color operators; the fill color and the stroke color are kept separately
- a color layer is made only when dark paint is drawn; white paint clears all the layers below it in the painter order; dark paint that comes after can put an island back on a cleared area
- each geometry element has an order number; the Gerber export replays the solid and clear operations in this order and writes region apertures
- fixed: a clear area that crosses an edge was ignored; the fallback that restored the original solid is removed
- the Excellon export uses only circular openings (width, height and area within 5%); duplicates are merged by the distance of the centers (0.001 mm or 0.0001 in); an opening that holds a smaller opening is clearance artwork and is not drilled; the 0.974 diameter factor is removed
- the Python parser reads the /Contents streams of the first page in their order, instead of all the streams of the file (fonts and images were mixed in); new ASCIIHexDecode and ASCII85Decode filters; clear errors for encrypted files, object streams, /DecodeParms and filters that are not supported
- clipping paths (W, W*), XObjects (Do) and inline images show a "[WARNING] Unsupported PDF content" message; the pikepdf path also finds them

Paint Tool:

- new Final paint option for each tool (Paint form, Preferences and Tools Database), Off by default: after the Paint, the smallest selected tool with Final paint paints the gaps that it can enter; the paths can cross the old passes, but the full cutter stays inside the selection and its margins; the final paths run after the normal paths of all the tools; old Tools Databases get Off
- the residual cleanup code is shared by the NCC Final cleanup and the Final paint (camlib.residual_cleanup); the cleanup trims the path ends that cut no new stock, opens covered gaps of a ring without more lifts and orders the paths to make the rapids shorter
- new Simplification preference (in mm, 0.01 by default; 0 disables it) for the tolerance of the Paint tool paths; inch projects convert the value 1 time
- the clearing insets are made from the original margin at each distance, not from the last inset; the chord error is limited by the circle steps and by the Geo Tolerance; the number of segments is about 10 times smaller than with unbounded insets
- faster Paint: the seed contour uses the margin buffer again; the last tool does not compute the residual area; a polygon narrower than the tool is skipped; the bounds of the output do not need a union of the paths

Milling and Drilling Tools:

- fixed: the tool connects and disconnects only its own slots of proj_selection_changed and of the exclusion area signal; before, a disconnect removed the slots of the other tools; a second connect does not add a second slot; the exclusion area signal is also disconnected when the tool closes
- the CNC job of the Milling tool reads all the GUI values on the GUI thread before the worker starts; the single and multi geometry jobs share the same code
- fixed: a Tcl command without an explicit toolchange does not read the widgets; a failure is returned to the caller; missing geometry options and an empty multi geometry fail
- fixed: the offset types (Path, In, Out, Custom) and the disabled offset are the same in the single and multi geometry jobs
- fixed: the buffer of the drills and slots is computed for each tool; a changed widget value is applied 1 time to all the selected tools
- the Excellon paint keeps only the paths and not the RTree index of each feature; abort is checked before each clearing step; the polishing keeps the captured steps and stops on abort

Cutout Tool:

- new gap engine: the automatic and rectangular gaps are cut by distance along each contour, at fixed anchors for each gap mode (LR and TB in the middle, 2LR and 2TB at the quarters, 4, 8); if a contour cannot hold the gap count and the gap width, the operation fails with a message
- fixed: a manual gap cuts the exact length along the clicked path, also on bends and on the seam of a closed path; the preview, the click result and the cut are the same
- fixed: the mouse bite holes follow the margin rows with the correct spacing; rectangular mouse bites do not change the milling path; panels with more than 1 row keep all the paths; each manual panel has its own tool data
- fixed: Drill Cut works with multipart, open and closed paths; bad pitch or diameter values stop the operation
- the values are checked before the job starts: the tool diameter must be finite and not 0; the mouse bite diameter plus spacing must give a positive step; Cut Z must be negative; Thin Depth must be 0 or between Cut Z and 0; a Thin Depth of 0 keeps a full bridge and makes no thin gap tool
- fixed: a job that cannot succeed shows "Cutout gaps failed: ..." or "Mouse bite gaps failed: ..." and makes no object
- fixed: a queued job uses the values from the time of the click; Cancel of a manual session restores the selection shapes; the queued mouse bite drills do not go to the next session
- fixed: a large rectangular margin keeps all 4 gaps; the flatten keeps the holes in nested collections; Reset does not add a second selection slot
- new tooltips for the gap size, Thin Depth, the mouse bite spacing (edge to edge) and the gap modes

Film Tool:

- fixed: the object keeps its position in the box frame; the box and the object are transformed together, with the origin from the box bounds
- fixed: a length scale on a box with a zero width or height does not divide by zero; a length skew uses the selected reference
- fixed: the box type used the object combobox; the PDF page size uses the application units instead of mm
- fixed: a missing object shows a message; the punched film uses the real object name; nested geometry collections get the film color; lines stay without fill; the viewBox and the margin are correct for negative coordinates
- faster export: the SVG document is made with 1 regex pass instead of an lxml and minidom parse; the punch uses intersects(); the geometry is not deep copied
- the positive and negative films share the same export code; the GUI values are read before the worker starts; a failure of the worker shows "Failed."

Solder Paste Tool:

- new calibrated dispensing: the Dose calculation setting has Thickness (wet paste thickness x pad area), Fixed volume (uL for each pad) and Legacy (the old cycle); the volumes are in uL and mm, also in inch projects
- new settings tabs, the same in the tool and in Preferences: Hardware (controller, syringe drive Motor with M3/M4 and S or Stepper with an A, B, C or E axis, Rotary or Linear, the Mach3 dwell units, the GRBL $32 mode, the Marlin cold extrusion) and Quantity (thickness, volume, measured flow, dose correction, syringe bore, pitch, gear ratio, plunger feed, prime, retract, slow Z approach)
- the controllers are Mach3, Mach4, LinuxCNC, GRBL (spindle mode only) and Marlin (linear E axis; the cold extrusion must be confirmed); the Marlin output uses ";" comments
- new pad placement: Auto, Line, Raster (with overlap %) and Dot (with dot diameter), with a pad margin; the raster strokes share the pad dose by length; the paths do not cross pad holes; rotated pads follow their long axis, also in Legacy
- the pads include the region apertures, are clipped to the final polarity (clear areas) and the overlapping apertures are merged
- the settings are checked before the G-code is made: a bad value gives an error and no output; the errors of the preprocessor are shown
- the nozzle diameters and the XY coordinates are read with ast.literal_eval instead of eval(); an empty toolchange XY means the current position
- fixed in Paste_1, Paste_GRBL and Paste_Marlin: M03 and M04 always have S; a dwell of 0 writes no G4; the Mach3 dwell can be in milliseconds; the paste jobs always use absolute coordinates (G90)
- new defaults: Travel Z 2.0 (was 0.1), Toolchange Z 5.0 (was 1.0), preprocessors Paste_1, Paste_GRBL and Paste_Marlin; the new settings default to Mach3 and Thickness 0.1 mm
- a selected paste geometry restores its nozzle settings; the controller and calibration changes apply to the next CNC job without a new geometry; the integer fields are saved; the project keeps the paste role and the preprocessor, and older projects get them from the tool data
- fixed: unused or empty nozzles do not stop the CNC job and do not change the source object; the CNC object combobox used the geometry widget; the G-code header units come from the job; the job can be aborted

CNCJob Plot:

- fixed: the project plot toggle and the redraw on selection use the plot filter of the CNC job (All, Cut, Travel) instead of the cncjob_plot_kind default; before, they plotted again or showed the wrong filter
- fixed: on_plot_kind_change does not take the combobox value as the tool diameter
- fixed: a plot with a tool diameter (Update Plot) is marked as the full plot, so a later visibility toggle uses it again

Preprocessors:

- fixed: the preprocessors that come with the application were searched in 'preprocessors' relative to the current working directory, so none was loaded from another folder (a frozen build changes to lib/, a script or a test that does not change folder); they are searched next to appPreProcessor.py now, next to the executable in a frozen build; the folder names are escaped for glob ('[' or ']' in a path) (merged PR #20 from ErkanGench)
- the preprocessor files are loaded with importlib.util.spec_from_file_location() and exec_module() instead of the deprecated SourceFileLoader.load_module() (1 DeprecationWarning for each file on Python 3.12, hidden by the default warning filters) (merged PR #20 from ErkanGench)
- fixed: all the preprocessor files were executed in the same module, so a module level name (a helper function, a constant) of a user preprocessor was replaced by the same name of the next file; every file is a module of its own now and appPreProcessor imports math again, for the older preprocessors that get it with 'from appPreProcessor import *' (merged PR #20 from ErkanGench)

Data Folder:

- new: the FLATCAM_DATA_PATH environment variable sets the data folder (preferences, Tools Database, recent files, preprocessors, log and the updater files) instead of %APPDATA%/FlatCAM, ~/.FlatCAM or the portable 'config' folder; a second installation keeps its own settings, also on Linux and macOS, and a test or a script can start with a throw-away folder; a relative path is taken from the working folder at the start (merged PR #28 from ErkanGench)
- FLATCAM_DATA_PATH is read before the portable configuration: when the variable is set, config/configuration.txt is not read

Translations:

- new scripts/i18n_tool.py (Python standard library only): extract writes locale_template/strings.pot from the code and lists the _() calls that can not be translated; merge updates the .po of a language (the translations are kept with their previous English text (#|), the new strings are added, the removed strings stay as obsolete entries); compile writes the .mo files; init starts a new language (merged PR #27 from ErkanGench)
- check reports the untranslated and fuzzy entries; a translation that does not format with the values of the English text (a real % formatting test) or whose {name} placeholders differ is an error and makes check exit with 1; placeholders written differently, a "%" that should be "%%", HTML tags, <argument> names and first and last line breaks that differ from the English text are printed as warnings (merged PR #27 from ErkanGench)
- stats prints the status table of the languages (translated, untranslated, fuzzy, placeholder errors, warnings, .mo up to date); glossary shows how each language translates the key terms of locale_template/glossary_terms.txt (merged PR #27 from ErkanGench)
- new guide for the translators (docs/TRANSLATING.md) (merged PR #27 from ErkanGench)
- a GitHub workflow runs check, stats and compile --check when a push or a pull request changes the translations (.github/workflows/translations.yml); new Translation problem issue form (merged PR #27 from ErkanGench)
- fixed: the "Undefined aperture at line" message of the Gerber parser was formatted with % inside _(), so it was never translated (merged PR #27 from ErkanGench)
- fixed: the Tcl help hint read "Type help  for usage.": the shell shows it as HTML and took <command_name> for a tag; the hint texts are escaped (merged PR #27 from ErkanGench)

Project file:

- the project save makes the WKT of all the geometries with vectorized Shapely calls, in pieces of about 10000 coordinates, instead of 1 call for each geometry while the JSON is written; a large project saves about 5x faster (compressed) and the GUI thread is blocked for much shorter times during the save (merged PR #32 from ErkanGench)
- the coordinates are saved with the shortest text that reads back to the same number instead of 16 fixed decimals, which lost the last bits of a coordinate below 1; the files are smaller and older versions open them (merged PR #32 from ErkanGench)
- a compressed project is written as compact JSON (no indentation, no sorted keys), which is faster for a project with many small items (a CNC job); a plain project keeps the indented JSON (merged PR #32 from ErkanGench)
- fixed: closing the app while the project is saved did nothing (only a message); now the app closes when the save ends, the normal way (the settings are saved, a change made during the save is asked about); after a failed save the app stays open (merged PR #32 from ErkanGench)
- fixed: a close during the save did not close the app when the check of the file size ended the save before the GUI received the save result

Code Editor:

- the code editor (Source Editor, G-code Editor, Script Editor) highlights only the lines that are shown, the other lines when they are scrolled into view; Qt highlighted every line when the text was set: the View Source of a G-code of 94000 lines took 11 s, now 0.9 s; Print and Print Preview highlight every line first (merged PR #33 from ErkanGench)
- fixed: highlighting during scrolling, resizing and printing no longer reports unchanged text as edited; the GRBL Sender keeps its program, progress and error selections

Project Tab:

- fixed: View Source opened an empty Source Editor and left the app busy for an object without a source, e.g. a Geometry made by a tool; it shows a warning now (merged PR #33 from ErkanGench)
- the context menu shows only the entries that apply to the selected objects: View Source for 1 object with a source, Edit and Save for 1 object (Batch Save for several CNCJob objects), Set Color for Gerber and Excellon objects, Enable Plot or Disable Plot by the plot state (merged PR #33 from ErkanGench)
- fixed: the context menu entries hidden for a Script or a Document object stayed hidden for the next objects (merged PR #33 from ErkanGench)

Tests:

- new unit tests for the DXF importer (tests/test_dxf_import.py) and the SVG importer (tests/test_svg_import.py); the image tests use the new SVG importer
- new unit tests for the Image tool loading (tests/test_image_tool.py)
- new unit tests for the PDF import: winding rules, nested q/Q transforms, the Gerber round trip with holes and islands, the drill filter and the duplicates, the stream order of the first page, filters that are not supported and encrypted files
- new unit tests for the Final paint, the Paint simplification, the Paint performance and the bounded insets (tests/test_shrink_offsets.py); the Tools Database tests check the Final paint
- new unit tests for the signal lifecycle of the Milling and Drilling tools (tests/test_milling_lifecycle.py)
- new unit tests for the Cutout regressions (tests/test_cutout_regressions.py) and the panel checks
- new unit tests for the Film tool (tests/test_film.py)
- new unit tests for the calibrated solder paste (tests/test_solderpaste_calibrated.py)
- new unit tests for the CNC job plot filter and the visibility toggle
- new unit tests for the preprocessor loading (tests/test_preprocessor_loading.py) (merged PR #20 from ErkanGench)
- new unit test for the translation of the Gerber parser messages (tests/test_gerber_translated_messages.py) (merged PR #27 from ErkanGench)
- new unit test for the usage hint of the Tcl help (tests/test_tcl_help.py) (merged PR #27 from ErkanGench)
- new unit tests for scripts/i18n_tool.py (tests/test_i18n_tool.py) (merged PR #27 from ErkanGench)
- new unit tests for the WKT of the project save (tests/test_project_save_wkt.py) (merged PR #32 from ErkanGench)
- new unit tests for a close of the app during a project save (tests/test_shutdown_routing.py) (merged PR #32 from ErkanGench)
- new unit tests for the highlighting of the code editor (tests/test_lazy_highlight.py) (merged PR #33 from ErkanGench)
- new unit tests for the Project tab context menu and View Source (tests/test_project_context_menu.py) (merged PR #33 from ErkanGench)
- new unit tests for the parallel isolation passes (tests/test_isolation_parallel_passes.py) (merged PR #34 from ErkanGench)
- new unit tests for the isolation boundary (tests/test_isolation_boundary.py) (merged PR #35 from ErkanGench)
- new unit tests for Clear leftovers (tests/test_isolation_leftovers.py) (merged PR #36 from ErkanGench)
- new unit tests for the CNC jobs of the tool data of older projects (tests/test_milling_old_tool_data.py) (merged PR #18 from ErkanGench)
- new unit tests for the milling direction of the isolation paths and for a boundary of more than 1 area (tests/test_isolation_leftovers.py, tests/test_isolation_boundary.py)
- new unit tests for the form of the Milling tool with the tool data of an older project (tests/test_milling_old_tool_data.py)
- the tests hide the DeprecationWarnings of the SWIG wrappers of OR-Tools ("builtin type SwigPyPacked has no __module__ attribute")

18.10.2026

GUI:

- new Appearance setting (Light or Dark) and Styling setting (Default or Custom): Default uses the Qt widget style without the application stylesheet; Custom applies the stylesheet; changing either needs a restart; the old theme values are migrated
- new Icons Color preferences: the light and dark icon colors recolor the single-color icon files; mixed-color icons, Project Tab icons and status indicators are not changed; the new colors show after a restart
- new Object AutoHide and Object Autoswitch preferences: AutoHide hides the source object after a new object is made; Autoswitch shows Properties after a new object is made
- new Dim text preference, on by default, next to Dark Canvas: in the Dark theme the GUI text without its own color is shown in a darker gray (#8c8c8c) instead of bright white; the change applies on save without a restart
- new Project Tab icons for Gerber, Excellon, CNCJob, Geometry, Script and Document objects
- the toolbars have no border
- a double click on a Notebook tab shows or hides the Notebook, the same as a double click on a Plot Area tab
- fixed: the toolbar lock and menu text states are read as booleans from the settings; the menu checks follow the state
- fixed: the layout selector stored an index instead of a layout name
- fixed: the compact and minimal layouts restore the saved custom arrangement; the preset is used only when that arrangement is not valid
- fixed: a rebuilt toolbar follows the menu lock state; the minimal layout stays locked
- fixed: the Toggle Grid Lines menu action uses grid32.png instead of grid_lines32.png
- the Drilling tool uses the drill32.png icon
- the paper size label is shown in tomato color
- the Project tab can hold more than 1 project: a combobox above the tree selects the active project; a right-click on the combobox has New Project, Open Project, Save Project, Rename Project and Close Project, with icons; an inactive project keeps its geometry and its Properties UI but releases its plots, the CNCJob annotations and the Gerber marks; a switch is refused while an editor is open or a task runs; File New and File Open add a project; after a rename, Save writes '<new name>.FlatPrj' in the same folder and the title bar shows the project name; Save and Save As change the file name and the dirty flag only after a good save; exit asks about each project with unsaved changes; new Tcl commands list_projects, select_project and close_project
- the File toolbar has no Open and Save buttons; the project commands are in the File menu and in the menu of the project combobox
- removed the project autosave and its Preferences options; an old preferences file with the autosave keys still loads
- fixed: Jump To and Locate put the mouse cursor at the wrong place on a scaled display: VisPyCanvas.translate_coords_2 maps to the logical canvas pixels (the inverse of translate_coords), and the canvas point is mapped to the screen with mapToGlobal; the application mouse position is updated to the new location, so the next relative Jump To starts from it, and a relative jump uses the position from the start of the command

Objects:

- a tool that makes a new object from a source object passes the AutoHide and Autoswitch options to the new object
- the options are shown in the tool form under Common Parameters

Plotting:

- the polygon fill is triangulated with GEOS (shapely.constrained_delaunay_triangles) when it is available and the polygon is valid; GLU is still used for older GEOS versions and invalid contours
- the shape buffers are made with NumPy: line segments, colors (np.tile, the color conversion is cached) and interiors; no Python loop for each vertex
- the CNC job plot buffers the paths of a tool in batches of 1024 (shapely.buffer and shapely.simplify on arrays) instead of 1 call for each path; the path annotations use a set for the position check
- measured on 2000 shapes (tests/benchmark_object_plot.py, median of 3 runs, buffer preparation): Gerber surfaces 3.39 s to 1.14 s (3.0x), Gerber contours 0.437 s to 0.144 s (3.0x), Geometry contours 0.427 s to 0.208 s (2.1x), Gerber follow 0.057 s to 0.033 s (1.7x), Geometry lines do not change; the fingerprints of the rendered buffers are the same
- the Gerber aperture marking sends the shapes in 1 batch for each aperture, so the existing multiprocessing is used; Mark All redraws 1 time instead of after each aperture and gets the marking color 1 time
- measured on 2000 shapes with 4 worker processes (tests/benchmark_gerber_marks.py, median of 3 runs): 1 aperture 0.71 s to 0.28 s (2.5x), Mark All with 40 apertures 1.13 s to 0.33 s (3.4x); the fingerprints of the rendered buffers are the same, including the colors, the visibility and the layer

Move Tool:

- moves only the selected objects that are plotted and visible; the selection box uses the same objects; a hidden selection cancels the move without an error
- a move of 0 does nothing: no replot and no export
- the Gerber aperture marks are turned off on the GUI thread before the move starts
- the worker moves the objects, plots them and then regenerates the Gerber and Excellon source; the bounds, the offset fields and the selection box update on the GUI thread after the worker ends
- a failure is shown in the status bar; the objects that moved are plotted again, so the canvas and the bounds stay in sync
- a queued move keeps its own object list; an empty object list does not move the current selection
- the move marks the project as changed
- the outline of the objects during the drag is drawn 1 time for each mouse move
- more than 1 selected object is moved in parallel threads (up to 1 thread for each CPU core); the plot and the source regeneration stay sequential because the canvas is not thread-safe; a failed object does not stop the move of the other objects and the first error is shown
- the debug log shows the time of the offset, the plot and the source regeneration of each move

Gerber and Excellon:

- a Gerber move translates the geometry of each aperture in 1 batch instead of 1 element at a time
- an Excellon move translates the existing hole and slot outlines and does not buffer every drill again
- the Gerber and Excellon source regeneration (after a move, a transform or an edit) formats a full path or all the holes of a tool in 1 operation instead of 1 coordinate at a time; the output is byte-identical; Gerber export 3.6x faster (0.542 s to 0.151 s for 4000 traces of 50 points), Excellon export 3.9x faster (0.112 s to 0.029 s for 40000 drills)
- the Allegro, KiCad and Altium (PcbDoc) board imports make the stroke text with 1 buffer of all the strokes instead of a union of 1 buffer per stroke (the same shape, 2.2x faster for the text); Allegro 17.2 sample board 0.233 s to 0.177 s, KiCad sample board 1.406 s to 1.262 s

Editors:

- the Move in the Gerber, Excellon and Geometry editors translates the selected shapes in 1 vectorized batch, changes each storage in 1 pass and plots 1 time; measured with 2000 of 4000 shapes moved: Gerber editor 12x faster (0.166 s to 0.014 s; 1 time instead of 4000 canvas clears), Excellon editor 21x faster (23.26 s to 1.13 s; 1 replot instead of 2000), Geometry editor 13x faster (3.15 s to 0.23 s; 1 plot instead of 2 and 1 UI build instead of 2000); the canvas time of the removed plots, clears and UI builds is not in these numbers, so the gain in the application is larger
- the drag outline of the Gerber editor Move is 9x faster (23.3 ms to 2.5 ms for each mouse move with 500 shapes); the Excellon and Geometry editors also translate the drag outline in 1 batch
- the Gerber editor Copy uses the same batch as the Move
- the editor storage (AppRTreeStorage) removes and inserts many shapes in 1 call: when the changes cost more than a new index, the R-tree is bulk loaded again (the bulk load is about 12x faster than the inserts and about 85x faster than the deletes)
- the Geometry editor adds many shapes in 1 batch and builds the UI 1 time instead of 1 time for each shape; a delete of many shapes does 1 pass instead of a search for each shape
- fixed: with the climb milling type, the Geometry editor reversed the direction of the shapes again on each Move, Copy and Explode; now only a new drawn shape is reversed
- the editor storage uses the quadratic rtree index (see NCC Tool): small edits in a large design are faster (4000 shapes, 132000 points): delete 1 shape 0.0110 s to 0.0023 s (4.8x), move 1 shape 0.0174 s to 0.0028 s (6.2x), delete 10 shapes 8.2x, delete 50 shapes 1.208 s to 0.086 s (14x), move 50 shapes 8.7x, move 200 shapes 3.0x; a Move of half of the shapes does not change (the index is bulk loaded again in both cases)

Gerber Editor:

- selecting elements updates only the selection colors and reuses the plotted geometry instead of rebuilding the whole board; geometry edits invalidate the cache
- the shape-to-aperture lookup is cached with the spatial pick index instead of searching the aperture geometry lists after each selection
- rectangle selection uses the spatial index for enclosing and touching selections; the selection order and modifier-key toggling are preserved
- bulk selection avoids repeated searches in the selected elements list; rectangle and aperture-table selections also reuse the plotted geometry
- fixed: repeated aperture-table selections no longer add duplicate elements; rectangle selection synchronizes the aperture rows without firing the table selection handlers
- added regression tests for cached selection, geometry edits, rectangle selection, selection order, modifier-key toggling and duplicate prevention
- fixed: importing shapes by click or rectangle from a Gerber with LPC (clear / negative polarity) features uses the composed copper geometry; clear slots stay empty and later dark islands are preserved when saved, regardless of import order
- fixed: imported shape records are copied; transforming an imported shape no longer changes the source object's aperture records
- fixed: regions split into multiple polygons by the eraser can be saved and exported; saving also handles split regions without follow geometry
- added regression tests for LPC imports, source object isolation after transforms and saving split regions, including Gerber export / reimport checks
- Copy plots the board 1 time at the end instead of after each copied shape
- the Eraser replots the board also when no fragment is left
- Delete removes the selected shapes in 1 pass over the apertures and marks the edit as changed
- Scale and Buffer change the selected shapes in place: the selection stays valid after a repeated Scale or Buffer, and the shapes that are not selected do not change; Scale finds the aperture also when its code is a number; a failed Scale shows an error and stops
- Import merges a rectangular (R) or oblong (O) aperture only with an aperture of the same type
- Zoom Fit uses the bounds of the shapes without a union of all the geometry; an empty board does not change the view
- fixed: Zoom Fit made the view rectangle from the bounds in the wrong format
- added regression tests for Copy, Delete, Buffer, Scale, Eraser, Import and Zoom Fit

GCode sender:

- the menu and labels use GCode sender and GCode Console; the object selector is labelled Source Object
- new Sim button: runs the prepared program with a virtual controller in virtual time; no serial port is used
- new start line dialog: the program starts at a chosen 1-based line
- the console highlights the progress line; browsing the console does not replace the stream highlight
- new Fit View option: fits the GCode bounds in isometric view after the source object is loaded
- the Show console option is next to the source object selector; the sections collapse when their label is clicked
- new Plot height map option: shows or hides the height-map surface in Plot 3D; levelling is not changed
- the probe points avoid the holes of an Excellon object that is selected in the Autolevelling section
- the job preparation (line numbering and levelling of the lines) is in the SenderGen module
- fixed: resume is blocked while a recovery is needed, while a reset is pending and for an abandoned job
- fixed: a new probe point with the placeholder height 0.0 no longer makes a full height map valid
- fixed: the cached 3D mesh of the height map is cleared when the map data is gone or invalidated
- the GCode Console has a Program tab and a Console tab; the Program tab shows the acknowledged line count and the lines with errors; the Follow option scrolls with the progress
- a program line that is selected in the console flashes red in the 3D view
- the 3D view shows the probe points and the holes of the selected Excellon object
- when a height map is available, a dialog asks to generate a new map or to use the existing map
- the 3D preview (with the levelled toolpath) is prepared in a worker thread
- fixed: a Grbl banner during a stream (unexpected controller reset) stops the stream and asks for a recovery
- fixed: a connection to the controller is blocked while the simulation is open

Plot 3D:

- new context menu on right click: Toggle Grid and Fit View
- the grid does not hide the toolpath
- the camera zooms in closer; after a fit to the toolpath, the camera can zoom out to the full workspace
- fixed: an empty marker or line visual does not raise an error when the view bounds are calculated
- the 3D plot area has no margins

Image Import:

- the Tracing mode uses the vtracer package instead of svgtrace; Chromium, playwright, pyppeteer, scikit-image and SciPy are no longer needed
- the default tracing uses straight segments (polygon mode); the old engine filled white areas enclosed by traces, which made shorts on PCB artwork
- new Threshold option for B/W tracing: a pixel darker than the value is traced; transparent areas are traced as white
- new presets: Default, Detailed, Smooth and Photo; a preset fills the Options
- new Options: Curve (Polygon, Spline, Pixel), Filter Speckle, Precision, Gradient Step, Corner, Segment Length and Splice; the options that do not apply are disabled
- the Chromium download dialog is removed; if vtracer is not installed, the plugin loads and Tracing shows how to install it
- fixed: the Custom options of the old engine had no effect

Preferences:

- the application settings are kept only in the application preferences; the project options are kept in the project
- a project file saves only the project options (Tools parameters, units and some Gerber, Excellon and Geometry parameters); an old project file still opens and its application settings are ignored
- removed the "Import Settings" question on project open; the project options of the file are always loaded
- a new project gets its options from the preferences and not from the Preferences form
- the Preferences window shows the project options of the active project; a save writes a changed project option to the project and to the preferences
- fixed: a save of the Preferences copied all the preferences into the project options
- Import Preferences writes the application settings of the file into the preferences
- the Compression preference sets the Zstandard level (1 to 22, default 3) instead of the LZMA level (0 to 9)

Project file:

- a compressed project is saved with Zstandard (level 3 by default) instead of LZMA; a compressed save of a large project takes about half the time
- a project compressed with LZMA (older app versions) still opens; the file type is detected from the first bytes of the file
- the project JSON is written and read as a stream, so the save does not hold the full JSON text and the compressed data in RAM
- a save writes to a temporary file that replaces the project file only after a complete write; a failed save keeps the old file
- uses compression.zstd on Python 3.14 and newer, else the zstandard package (new requirement for Python older than 3.14)
- an uncompressed project file has LF line endings on all platforms
- replaced simplejson with the standard json module (equal or faster); removed the unused simplejson imports in the plugins; simplejson is no longer a requirement

Panelize:

- the snap of almost-touching lines (snap overlaps) uses a dynamic R-tree to skip the line pairs that are far apart, instead of a snap of every pair; the snap order is the same and the bounds are updated after a line moves; when all the remaining lines are near each other, the old loop of every pair is used (dense cases stay quadratic); empty lines are skipped and the abort check runs in the loops
- measured on synthetic lines (median of 3 runs, old to new, the same output): 200 sparse lines 0.086 s to 0.008 s (10x), 800 sparse lines 1.343 s to 0.031 s (43x), 200 dense lines 0.149 s to 0.165 s (no change, the old loop)
- the source geometry is flattened 1 time, not again for each copy
- the panel grid refuses infinite or empty reference bounds and spacing, and the row and column limits do not go below 0
- fixed: the Panelize tool closed its tab when the tab was at index 0, removed tab 2 instead of its own tab and named tab 2 instead of its own tab; a second start does not connect the selection signal again
- fixed: the tool opens with the selected Gerber, Excellon or Geometry object, and the panel type is set after the object; the optimization is disabled for an Excellon source
- fixed: with the Bounding Box reference, the box object combobox is not read (a missing box object gave a wrong warning); a constrain area that is too small for 1 copy shows an error; invalid bounds or spacing show an error instead of an exception
- fixed: a failed panel removes the promised object name and shows "Panelize failed"; each run uses its own output name, so a second run started before the first ends does not rename the first panel

Find Optimal and Check validity:

- the minimum distance between the copper polygons (Isolation and NCC "Check validity" and the optimal tool diameter, the Find Optimal tool) is found with an STRtree instead of a check of every pair (O(n log n), not O(n^2)): the candidate pairs come from the expanded bounding boxes and a prepared 'dwithin' test, and each distance is computed on the parts of the 2 polygons that are clipped to the intersection of their expanded boxes, so a large copper pour does not make each pair slow; the new function is camlib.polygon_gap_pairs()
- the minimum distance, its frequency and its locations are exact; when more than one point pair is at the minimum distance (parallel edges), a location can be another point on the same edges
- Find Optimal lists the distances between neighbor polygons only: all the pairs that are closer than the largest distance from a polygon to its nearest polygon, so each polygon has its nearest pair (before: every pair of polygons, also the pairs that are far apart)
- measured on 4 boards (old check of every pair to new): KiCad F_Cu (106 polygons, 1 pour of 54951 vertices) 22.8 s to 0.76 s (30x), Find Optimal 1.6 s (14x); Pcb39 (216 polygons) 38.0 s to 0.10 s, Find Optimal 0.71 s; eagle (276 polygons) 88.9 s to 1.84 s (48x), Find Optimal 2.97 s (30x); KiCad B_Cu (66 polygons) 4.4 s to 0.25 s; the same minimum distance and number of pairs on all 4 boards

NCC Tool:

- measured on a real board (Easyduino RP2040 front copper, KiCad: 20 x 50.2 mm, 106 copper polygons, 20688 vertices; tools 2, 1 and 0.5 mm; median of 3 runs, geometry generation only): normal clearing 1.6x faster (1.094 s to 0.672 s) and the cutter sweep over copper 89x smaller (1.240 mm2 to 0.014 mm2); rest machining 1.8x slower (0.655 s to 1.206 s) because the residual stock is now exact, but the sweep over copper is 61x smaller (0.668 mm2 to 0.011 mm2) and 74 paths clear the stock instead of 65; the small overlap that remains is the polygonal approximation of the round cutter
- measured on the small test board (40 x 30 mm, 54 pads, 2 traces): normal clearing 0.131 s to 0.152 s, rest machining 0.042 s to 0.068 s; the sweep over copper 1.98 mm2 to 0.36 mm2 (normal) and 1.73 mm2 to 0.35 mm2 (rest)
- the progressive plotting redraws at most every 50 ms: on the real board 53 to 4 redraws for the line clearing and 79 to 5 for the seed clearing; the events and the abort check still run on each step
- the copper and offset preparation is computed 1 time for each different offset in a generation (it was computed again for each tool)
- the shrink clearing does not union a single buffer component again; the union of split components stays, because its removal changes the geometry
- the simplification uses the Geo Tolerance preference (converted for inch units) instead of a fixed 0.01; a simplified path is kept only when its full cutter sweep stays inside the area, else the original path is used; the stock boundaries are not simplified, so small copper features are not lost
- rest machining subtracts the union of the cutter sweeps of each tool in 1 operation; a polygon that a tool cannot clear stays in the area for the next smaller tool and is not added a second time; the rest margin uses its own Margin field
- fixed: the Tcl rest isolation passed the cutter diameter instead of the radius (the GUI passed the radius); multipart isolation geometry is handled
- fixed: the rest machining tooltip said "next bigger tool" instead of "next smaller tool"
- the Milling tool makes the CNC jobs of the selected tools in the visible row order of the tool table, so the NCC order and a manual reorder are kept
- two optimizations were tested and rejected: batched scan-line intersections were slower, and the removal of all the shrinking buffer unions changed the geometry
- new "Final cleanup" option for each tool (default Off), in the NCC tool, the NCC preferences and the Tools Database: after the clearing, the smallest selected tool with the option on clears the reachable residual copper; its paths can cross areas that are already cleared, but the full cutter sweep stays in the original offset and margin area, so the copper clearance and the boundary are kept; the cutter is computed as a circumscribed polygon; with Gerber sources the copper is always removed from the cleanup area, also with a negative Tcl offset
- the cleanup paths are added after the standard paths of that tool, the tool is moved to the end of the tool order, and the CNC job keeps this path order (no path optimization for this tool); a warning tells when residual copper stays
- Final cleanup works in the GUI and in Tcl, for normal clearing and rest machining; old Tools Databases load with the option Off, so the current preference does not enable a new cut
- measured on the real board (same setup as above, Final cleanup on for all tools, median of 3 runs): rest machining 1.52 s to 18.46 s (12x slower), 798 cleanup paths, the residual stock 176.42 mm2 to 165.07 mm2 (11.35 mm2 more copper removed); normal clearing 0.90 s to 8.43 s (9x slower), 22 cleanup paths, the residual stock does not change (163.59 mm2); the most of the residual is in gaps that are narrower than the smallest tool; the sweep over copper and outside the boundary does not change in all 4 cases (0.014 mm2 normal, 0.011 mm2 rest, 0 mm2 outside)
- Final cleanup is faster and makes no empty paths: it works only on the residual that the cutter can reach (most of the residual is in gaps narrower than the tool); a cleanup path must remove at least a strip of Geo Tolerance width across the cutter (it was 1e-18 mm2, so paths that removed nothing were kept); the copper gain is checked before the 2 cutter safety checks, and only on the residual parts near the path (STRtree), not on the full residual; the events and the abort check also run after the large overlays
- measured on the real board (median of 3 runs): rest machining with Final cleanup 17.63 s to 3.62 s (4.9x faster), 462 paths instead of 872, the residual stock 165.07 mm2 to 164.90 mm2 (more copper removed with fewer paths); normal clearing with Final cleanup: 0 cleanup paths instead of 22 (they removed no copper), about 1.1 s more than without Final cleanup (was 7.5 s more)
- connect (reduce tool lifts): the walk check uses the prepared boundary (contains and dwithin) and no polygonal cutter buffer; a walk inside the boundary at the real tool radius or more from its edge keeps the full cutter inside; the old check rejected some safe walks because of rounding errors in the polygonal buffer, so the seed method makes fewer tool lifts (1044 paths instead of 1189 on the real board with a 0.2 mm tool) with the same minimum distance to copper (0.0989 mm) and no sweep outside the boundary
- the line method cuts each scan line from a band of 8 lines clipped from the margin polygon (shapely.clip_by_rect), not from the full polygon: 12x faster for the scan lines of the largest board polygon (14647 vertices), the same lines (count and length equal, max difference 1.4e-14 mm)
- the path storage (AppRTreeStorage) uses an rtree index with the quadratic split instead of R*: an R* delete reinserts entries and costs 41x to 59x the bulk load of a point, a quadratic delete costs 2.5x to 3x; on 20000 points the nearest + delete walk of connect is 7.8x faster (13.60 s to 1.73 s), the inserts 2.6x, the bulk load, nearest and window queries are also faster and the walk order is the same; the replace() costs are measured again (delete and insert 3x, was 85x and 12x)
- measured NCC on the real board (median of 3 runs, geometry generation only), old to new: lines 0.2 mm tool 1.185 s to 0.535 s (2.2x), lines with connect 2.115 s to 0.847 s (2.5x), seed with connect 3.181 s to 2.324 s (1.4x, 1044 paths instead of 1189), standard with connect 0.701 s to 0.628 s, lines with connect and tools 2, 1 and 0.5 mm 0.789 s to 0.678 s; standard, seed and rest machining without connect do not change (within the noise, 7 runs)

Tcl:

- fixed: set_sys changes the preference through app.defaults, so the new value is also used by the active project
- a command that fails raises a Tcl error: a 'fail' or False result, or a TclCommandFailure text, stops the script; the commands that return a value (get_active, get_names, get_path, get_sys, list_projects) still return it
- the object factories and the tools return 'fail' to the Tcl command on a failure: new Gerber, Excellon and Geometry objects, Gerber isolation and follow, Geometry and Excellon CNC jobs, NCC, Paint, Milling, the G-Code export and the preferences save
- no eval() on script input: the values are read with ast.literal_eval, TclCommand.parse_bool and TclCommand.parse_coords; NaN and infinite numbers are refused; an option without a value is refused (except the flags that need none); a required argument needs a value
- the signaled commands (-timeout) keep their state for each call and wait in a QEventLoop with a single-shot timer; a late result of a command that timed out is not taken by the next command; the timeout message says that the queued work is not cancelled
- open_gerber, open_excellon and open_gcode look for a missing file also in the Tcl path folder
- subtract_poly and geo_union work only on Geometry objects; they also change the multi-geometry tools and update the bounds
- cncjob from an Excellon object reads the tool list without eval() and fails when no tool matches
- the Geometry CNC job uses a value of 0 for the tool diameter, the toolchange Z, the spindle speed, the laser minimum power and the dwell time (a 0 was replaced by the default before)
- add_aperture: integer aperture codes from 10, a positive and finite size
- align_drill: -dia is required; the grid holes are computed directly instead of in loops
- copper_clear: -ref is an alias of -box; exactly one of -all and -box is needed
- set_origin: updates the bounds and the plot; fixed the Y value in the message
- set_sys: the new value is converted to the type of the current value
- the shell reports a Tcl error with tcl.call('error') and FCShell.raise_tcl_error raises TclErrorException
- fixed: the Gerber follow command used self.options instead of self.obj_options for the name

Tests:

- new unit tests for the GCode sender workflow, console, restart, simulation, audit fixes, icon colors, layout changes and plugin object creation
- extended unit tests for the GRBL sender, Plot 3D, levelling audit and theme color scheme; the gate list is updated
- new unit tests for the image tracing; the gate list records the renamed Plot 3D zoom test
- new unit tests for the Move Tool, the multiple projects (ProjectContext, ProjectManager, project combobox, File New, Open, Save and Save As, exit, Tcl commands) and the split of application settings and project options; the gate list records the removed autosave tests
- new unit tests for the project file: plain and Zstandard round trip with both Zstandard backends, legacy LZMA load, inf/nan cleanup, level clamp and a failed save that keeps the old file
- new unit tests for the Panelize snap: the same output as the old loop, moved bounds, the tolerance limit, empty lines and abort; extended tests for the Panelize plugin fixes; new unit tests for Jump To and Locate (cursor mapping, relative jumps)
- the full test suite takes 284 s instead of 401 s (the same results): the Python 3.10 syntax check skips every virtual environment (a .venv_3.13 folder gave 8627 of its 9228 files) and checks a backslash in the lines before it gets the source of an f-string expression (44.6 s to 4.3 s); the tests that change the application style or stylesheet run in a new process when the test process holds many widgets (tests/qt_isolation.py: each style change restyled every widget left by the earlier tests, 26 s to 2 s for test_qt_style.py and 27 s and 28 s to 2.2 s each for 2 title bar tests); the title bar glyph check compares the pixels with NumPy; the Clipper Eagle tests check the boundary distance with a 'dwithin' query (5.8 s to 3.5 s); the Allegro 18.1 tests parse each film 1 time and fuse each layer 1 time (setup 8.4 s to 4.5 s)
- the GUI tests do not show their windows on the desktop: conftest.py sets WA_DontShowOnScreen on each top-level window before its first show (the windows are still shown for Qt); set FLATCAM_TEST_SHOW_WINDOWS=1 to see them
- more test time removed: the KiCad, Eagle and Sprint Layout e2e tests parse the board 1 time for both test classes, and the Allegro tests parse each sample board 1 time for the tests that only read it; the axis label and HUD tests check each unit or theme and each font size in 3 canvases instead of 6 (the first render of a canvas costs about 0.75 s); the NCC benchmark tests run each case 1 time (FLATCAM_BENCHMARK=1 gives the median of 3 or 5 runs); the levelling simulation check makes the segment data 1 time for each move; the Qt connection check parses each file 1 time; full suite 284 s to 279 s with a busy machine
- new unit tests for camlib.polygon_gap_pairs (against a check of every pair: minimum, neighbors, ties, a pad in a hole) and for the Check validity of the Isolation and NCC tools
- new unit tests for the plot preparation of objects and CNC jobs and for the Gerber marking batch; new benchmark scripts tests/benchmark_object_plot.py and tests/benchmark_gerber_marks.py (with --baseline-ref to measure an old revision)
- new unit tests for the NCC Final cleanup: re-crossing of cleared stock, default Off, negative offset on Gerber copper, the offset and boundary limits in mm and inch, a gap that no tool can enter, abort, Tcl, unsafe connectors, holes and multipart areas; the Tools Database round trip, old databases and the tool form and preferences
- new unit tests for the Tcl commands (tests/test_tcl_audit_*.py, 100 tests): the result contract, cutting, CNC generation, geometry, transforms and regressions
- new unit tests for the NCC Tool (tests/test_ncc_followups.py and tests/test_ncc_rest_machining.py): preparation cache, simplification safety, rest machining, isolation parity, redraw throttle and the board benchmarks (pytest -k benchmark -s)

17.10.2026

GUI:

- fixed: the Default and Light themes were unreadable when the operating system was in dark mode (dark blue headings, black project items and black ruler ticks on a dark window; the selected preferences tab had green text on the system highlight): Qt's color scheme now follows the theme (Default and Light are light, Dark is dark, Auto follows the operating system); needs Qt 6.8 or newer, with an older Qt the ruler ticks of the plot canvas still switch to gray on a dark window (merged PR #9 from gargamel778)

GRBL Sender:

- new GRBL Sender plugin: a GRBL console dock, the GRBL Sender button in the CNCJob UI and the GRBL preferences; the selected baud rate is kept between sessions
- the G-code is streamed with the character-counting protocol; the GRBL status report is parsed; run control, overrides and check mode are available during a job
- a job that is not segmented is segmented before it is streamed
- a levelled job is made from the G-code with the height map: long G1 moves are split so the autolevelling follows the surface; the height map is bound to the GRBL work frame

Levelling Tool:

- GRBL probing and streaming moved from the Levelling Tool to the GRBL Sender; the Levelling Tool keeps the height map and the autolevelling
- added the bicubic height-map method
- the probe grid is probed in snake order, so the head does not return to the start of each row

Plot 3D:

- the Plot 3D tab shows the toolpath, the tool and the height map of the GRBL job
- the 3D toolpath shows the line index of each move

Tcl and command line:

- fixed: a relative file name in a Tcl command (write_gcode, save_project, open_gerber, export_svg ...) was resolved against the application folder, because the application changes into it at startup; for a packaged application that wrote into the application bundle and broke its code signature. It is now resolved against the folder FlatCAM was started from (the home folder when that is the root folder, a folder inside the Windows folder, or the application itself); after set_path or a Tcl cd the new working folder is used; a leading ~ is expanded (merged PR #10 from gargamel778)
- fixed: a relative --shellfile or a relative file to open given on the command line was not found for the same reason; an unreadable --shellfile now ends the program with an error before the GUI starts (it started the GUI and then crashed on exit) (merged PR #10 from gargamel778)

16.10.2026

Rendering and compatibility:

- removed the Legacy Matplotlib canvas and vendored descartes; VisPy is now the only PCB canvas backend and requires working OpenGL. Ordinary 2D PCB editing is unchanged, and the separate 3D view is retained.
- removed renderer selection, the Ctrl-startup fallback and renderer title badges. OpenGL initialization failures now direct users to graphics drivers, remote desktop/VM graphics settings, application logs and error details. See README startup troubleshooting; previous releases remain available through the repository's Releases/downloads page.
- retired global_graphic_engine is automatically discarded on settings load/import and acceptance of project settings; subsequent saves omit it. The compatibility key global_graphic_engine_3d_no_mp is preserved. Its checkbox is now labelled 'Disable parallel shape processing': it selects synchronous processing, not another renderer.
- raised ezdxf to >=1.4.5 and removed the Matplotlib requirement. DXF TEXT and ATTRIB use the modern fontTools-based text path without Matplotlib (checked with Arial); upstream MTEXT and ATTDEF still produce empty geometry, unchanged.

16.10.2026

GUI:

- optional custom title bar with a hamburger menu and a panel toggle button; the project name is shown in the title bar instead of the window title; the operating system title bar is used when the preference is off (the preference is on by default and requires a restart)
- the toolbars use smaller icons and tighter spacing; the status bar is compacted without shrinking its fonts or clipping the coordinate entries
- the dark-theme blue text is lighter (#66B3FF); inline blue HTML colors in labels (status bar messages, plugin parameter headings, help text) are mapped through the theme color so they stay readable on a dark background
- fixed: leaving Fullscreen restored the normal-window geometry before the native state finished changing; the window now leaves fullscreen first, then restores the saved rectangle, and a maximized window goes back to maximized
- fixed: Return and Enter in the project tree open the selected object's Properties through the native tree activation; object insertion keeps both existing Properties UI builds and restores the selected object's UI
- File / Exit and the system tray Exit now use the guarded window-close path; updater and save guards run before settings are saved, and cancelling the unsaved-project prompt keeps the window open; the native Quit shortcut and Exit Editor remain separate
- worker progress messages are coalesced on the GUI thread instead of updating widgets from worker threads
- fixed: on macOS, Cmd+Q and the Quit item of the application menu work with the native menu bar (merged PR #5 from gargamel778)
- the custom title bar is on by default only on Windows (merged PR #7 from gargamel778)
- the Objects menu no longer rebuilds a per-object entry list on every collection change; Select All and Deselect All remain connected once at startup

File loading:

- opening 3 or more Gerber files and restoring projects insert objects on a GUI timer, with nonmodal progress; completion waits for parsing, object insertion and plotting
- batch loading refreshes object menus, Tcl keywords and recent files once at completion, fits the view once after plotting, and preserves a selection made while loading
- project parsing failures and cancelled legacy-project approval restore the prior autosave state; legacy approval runs on the GUI thread before the current project is cleared; CLI/no-plot restoration waits for object insertion without showing progress

Object operations:

- fixed: Move2Origin and Custom Origin menu actions passed Qt's unchecked state as use_thread=False; menu actions now keep worker execution enabled and snapshot the selected objects before dispatch
- origin moves perform geometry, plotting and source regeneration in the worker; bounds, offset fields and selection overlays refresh on the GUI thread after completion; a busy 3D plot lock retries the camera fit without blocking the interface
- Replot actions and the Geometry and Gerber editor Transform actions discard Qt's checked argument so command defaults are preserved; added a source guard and real Qt regression checks for these signal connections
- deleting multiple selected objects removes tree rows in contiguous backward ranges before cleanup, then refreshes autocomplete, canvas and tabs once instead of per-object; single-object deletion keeps its original path

Canvas:

- the HUD text uses CPU glyph rendering instead of GPU; object culling state could corrupt the GPU font atlas and erase the coordinates display after drawing large shapes
- the selected object is drawn in a foreground overlay above every normal plotting layer; the overlay works for Gerber, Geometry, CNCJob and Excellon objects; the shapes keep their original layer and cached geometry, and the previous selection is restored when a new one is made
- a partial plot (bring_to_top returns False) is rebuilt and then brought to the foreground

Toolbar:

- the disabled Levelling and Follow buttons are removed from the Plugins toolbar

Preferences:

- the 'Custom Title Bar' checkbox in Preferences (General / GUI) turns the custom title bar on or off; changing it prompts for a restart

Tcl:

- fixed: open_gerber without a filename raised an unhandled error; it now reports the missing argument
- fixed: export_gerber and export_excellon pass the object name as obj_name (merged PR #4 from gargamel778)

Isolation:

- fixed: when the passes are combined into 1 Geometry, every pass is kept (merged PR #3 from gargamel778)

Compatibility:

- fixed: a backslash in an f-string expression of the NCC Tool stopped FlatCAM from starting on Python 3.10 and 3.11 (merged PR #2 from gargamel778)

README:

- added a Screenshots section with images of the main interface, the PCB preview, isolation, NCC clearing, NCC G-code and Paint

Version:

- the version is 1.0

15.10.2026

Levelling Tool:

- the Manual/Grid mode and Voronoi/Bilinear method are saved per CNCJob; reopening the tool restores the job's choices instead of the global method, without clearing a valid height map
- added optional GRBL precision probing (two touches): coarse touch, retract, slow touch, then lift to the travel height; only the second measurement is used
- the fine probe feedrate and precision retract are saved per job, with unit conversion; defaults are 10 mm/min and 0.5 mm, and precision probing is off by default
- precision probing checks the contact, retract limits and coordinate frame between stages; a failed touch or an uncertain frame stops the operation and requires recovery
- fixed: malformed, incomplete, duplicate or nonfinite GRBL work-offset replies could escape error handling; they now fail through the normal recovery path
- applying autolevelling warns once when compensation samples fall outside the measured map: grid bounds for bilinear maps, the convex hull of measured points for Voronoi maps; the warning includes the sample count and asks for the map to be extended and reprobed
- coverage warnings do not change the compensation and are published only for the current job; rapid moves and motions skipped by the coordinate-frame checks do not cause coverage warnings

Gerber / Excellon objects (the 2 object kinds now share their transforms, copy, merge, export and plot code):

- the Gerber export writes the composed geometry as regions when the native writer cannot represent the object: aperture macros (AM), polygon (P) apertures, clear (LPC) features, tracks drawn with a rectangle aperture, a transform that the apertures cannot follow (for example a skew or a rotation that is not a quarter turn), and the PCB board imports. Holes and islands in holes are kept. The object keeps this state through copy, join, editor save and project save/load
- after scale, rotate, mirror and buffer the aperture sizes of a Gerber follow the geometry where the aperture type can show the result
- the multiplicative buffer works for every aperture type; an object with clear features or tracks of a non-circular aperture is refused with a message and is not changed
- fixed: the Gerber export wrote negative coordinates with the sign after the zero padding (T zeros); the Excellon export wrote wrong LZ/TZ coordinates and slots ('drilling' slot type)
- fixed: a polygon (P) aperture was rotated about the wrong point
- fixed: the Gerber editor lost every clear (LPC) feature on save; an object with clear features now loads in the editor as its composed geometry, with a message
- fixed: the Excellon rotate without a point turns about the center of the object; the buffer modes are correct; the tool parameters are kept when the geometry is rebuilt
- fixed: every object ran the init of its geometry class 2 times: the Excellon circle steps preference was ignored and the CNCJob state was reset
- Excellon scale moves the holes and keeps the tool diameters: a drill is a physical tool (use it to correct a file read with a wrong number format; Eagle writes inch files as 2:5)
- Excellon join fuses 2 tools with the same diameter only if their tool parameters are the same
- copy keeps the aperture macros, the follow geometry and the source of the object; join (merge) of Gerbers deep-copies the apertures
- fixed: the slots of an Excellon come back from a project as lists; they are tuples again
- fixed: marking an aperture that was added after the object was built raised an error
- fixed: the plot checkbox of an Excellon tool row could read the checkbox of another row
- fixed: Script and Document objects could not be created
- fixed: the Distance tool did not snap to pad and drill centers (Shapely 2)
- fixed: Solder Paste and Isolation failed on clear flashes; Film failed on punch holes
- the export text of Gerber and Excellon is built in 1 step: the Gerber export of a large panel is about 3 times faster, the output is the same

Panelize:

- the Panelize plugin and the Tcl panelize command use 1 shared core: both make the same panel
- a Gerber panel made from a Geometry, and a Geometry panel made from a Gerber, keep their tools and get a source file (before, the export of such a panel failed)
- fixed: an endless loop for a single-geometry source
- fixed: Tcl panelize no longer runs eval() on the -use_thread value, reports a missing -box object, and uses 1 for a missing -rows or -columns
- the messages show the object name, and 'done' is shown after the panel exists

Gerber Editor:

- a click selects 1 shape: the nearest one within 5 screen pixels, so a thin trace is easy to hit. Inside more than 1 shape, the smallest shape is selected first
- a repeated click at the same place selects the next shape under the cursor; the status bar shows 'Shape N / M'. Every shape is reachable, also equal shapes on top of each other. With the multi-select key, the next shape replaces only the last pick
- the click selection uses a spatial index on the main thread: about 0.1 ms per click on a board of 20000 shapes (it sent all the geometry to the worker processes on each click, about 0.9 s)
- the select click is not snapped to the grid; a mouse move of less than 5 pixels between press and release is a click, not a selection box
- the Simplify tool click selects 1 shape (it selected every shape under the cursor); the Erase tool picks its shape with the same rule
- the Apertures Table follows the selection without firing its handlers
- fixed: the Erase tool turned the center line of an erased trace into a polygon, added an empty shape for every shape it did not touch and added its cut shapes more than 1 time

Geometry Editor:

- fixed: deleting more than 1 selected shape deleted only some of them, and some wrong ones stayed in the storage
- fixed: the last deleted or deselected shape stayed on the canvas; utility shapes were drawn only when unselected shapes were present

Cutout:

- new option for the 'Any' (freeform) cutout: 'Source is board outline' and 'Cut inside contours'. The closed contours and the holes inside a board outline (slots, windows, cutouts) are cut from the inside; an island in a cutout is cut again from the outside. Contours drawn with lines and filled regions (also clear regions) are both supported
- the inner contours are cut first and the board outline last, so the board stays held while its inside is cut. Gaps and mouse bites on the inner contours are off by default ('Gaps on inner contours')
- a Gerber with the X2 attribute '%TF.FileFunction,Profile' marks itself as a board outline; the file function is kept in the object options and in the project
- a slot that is narrower than the cutter is skipped with a warning; an outline drawn with an aperture that has no center line (a rectangle aperture) is reported and never cut as an inner contour
- with the option off the cutout output is the same as before
- fixed: the mouse bites in Single mode with a negative margin raised an error

Isolation:

- fixed: a change of the V-shape tool parameters could leave the plugin signals disconnected, and an unchanged diameter was reported as a duplicate of the tool itself
- a V-shape tool added with a diameter of 0 gets its diameter from the tip diameter, the tip angle and the Cut Z; when the tool comes from the Tools Database, its own parameters are used

CNCJob / Drilling:

- the OR-Tools 'Basic' path optimization has a time limit: the 'Duration' preference (Excellon and Geometry) is now used by 'MetaHeuristic' and 'Basic' and is enabled for both; 0 means 3 seconds. A panel of 1152 holes took 134 s, now about 3 s
- the OR-Tools distances are given as a matrix and with a resolution of 1/1000 unit: holes less than 1 mm apart were in a random order
- fixed: in a CNCJob made from an Excellon, the 'Plot' checkbox did not check or uncheck the plot checkboxes of the tools, and a click on a tool checkbox did not update the 'Plot' checkbox
- fixed: the milling G-code generation checked the Cut Z and Travel Z of the previous tool, then used the raw values of the current tool; a positive Cut Z or a negative Travel Z went into the G-code without the sign correction, and a laser job without a Cut Z raised an error
- fixed: the G-code export suggested the file name of the active object instead of the exported CNCJob; renaming the job after export works when its Properties panel is closed

Tcl:

- fixed: scale with only -x or -y, mirror and skew of more than 1 object, mirror without -box or -origin (now about the center), buffer -join 2 (square)
- new command: join_gerber
- a buffer that the object refuses is reported as a failure

Other:

- fixed: the saved application style is restored by name instead of its position in Qt's style list; older numeric settings are converted and invalid settings use Qt's default style
- fixed: the Style combobox in Preferences was empty when an application theme stylesheet was active
- startup keeps Qt's native default style instead of forcing windowsvista, allowing Windows 11 styling and native dark title bars on supported Qt versions
- fixed: the restored objects of a project did not react to option changes
- fixed: the interiors of a geometry (isolation with the 'Interiors' type, the Tcl command interiors) were returned as ring sequences instead of rings
- fixed: in the Geometry editor, Cut Path with a closed cutter (polygon or closed path) removes the part of the target inside it; results with more than 1 part are added as separate shapes and empty results are dropped
- fixed: the Delete key raised an error on a Geometry object in the Properties tab that has no tool delete handler
- fixed: a Geometry object was drawn below the filled copper of a Gerber object; it is now drawn on the top layer of the canvas, and a plot with visible=False stays hidden
- fixed: after a rename, the old name could stay in the Tcl autocomplete and the new name was missing when the old name was not registered
- the project tree refreshes only the renamed row instead of the whole layout; the selection is kept
- fixed: a Gerber file with a macro flash in 2 parts failed to load when Clipper2 was not used
- a shape added to a canvas layer that does not exist raises an error with the layer number
- the plugins Isolation, NCC, Paint, Follow, Punch Gerber, Drilling and Milling no longer inherit the Gerber or Excellon class
- the project moved to GitHub: https://github.com/MariusAdrian88/FlatCAM_Evo (branch 'main' is the stable code, branch '1.0' the development code). The About, How To and Report a bug links point there
- Python 3.10 is the minimum version; an older Python stops with a message
- the headless mode is removed (the --headless option and the 'headless' key of config/configuration.txt)
- OR-Tools is used only on a 64-bit Python and only if it can be imported (on a 32-bit Python the app said it was available)
- shortcuts: Alt+D opens the Drilling plugin; 2-Sided moved to Alt+U; the menus show Alt+E for Extract and Alt+R for the Transformation of the editors
- fixed: the F3 shortcut list raised an error after the Alt+U row was added, and it showed 'T' for Alt+S
- the Tcl editor autocomplete has the correct preprocessor names (GRBL_11_no_M6 was GRL_11_no_M6) and the missing ones
- Linux: setup_ubuntu.sh installs the Python packages in a .venv in the project folder (no system-wide pip), with the GDAL package that matches the system library; the launcher starts flatcam.py with that .venv; the man page and the desktop file are updated
- the README is rewritten: features, every plugin, editors, file formats, preprocessors, path optimization, Tcl scripting and the installation on Windows, Linux and macOS
- the '3D Area' plot tab is renamed to 'Plot 3D'
- the minimum VisPy version is raised to 0.17.0

Plot 3D:

- the 3D view is rewritten: a clickable ViewCube overlay gives Top, Bottom, Left, Right, Front, Back and Iso views (right-click for the full menu); the camera zooms toward the cursor, rotates about the clicked point and pans with the configured mouse button
- dragging the ViewCube rotates both the cube and the workspace around the grid center; face clicks and the context menu remain available, while dragging on the canvas still rotates around the clicked point
- the grid is rendered with a stable ray-plane intersection shader that no longer breaks up at maximum zoom or edge-on angles; the same renderer is used by the accelerated 2D canvas
- the X and Y axes show their labels with units (e.g. 'X (mm)'); the title spacing is measured from the tick-label font metrics and is recalculated when the grid size changes
- the workspace is a fixed-size rectangle in millimetres (or inches); the Fit View button and the ViewCube both frame it with room for the axes

Canvas:

- the coordinates HUD on the accelerated canvas uses a measured fixed-width font with right-aligned values, tight background padding and a foreground color that contrasts with the canvas background; the legacy HUD loses its historical 2.5× size multiplier and uses aligned monospaced columns
- grid labels, HUD text and CNCJob annotations use the Qt logical DPI instead of the VisPy physical-DPI estimate, fixing oversized text under high-DPI scaling; the annotation and axis font-size preferences are in points
- CNCJob annotations scale down with zoom-out and are capped at the configured annotation font size

Status bar:

- the right-click menu of the status bar uses the native toolbar toggle actions, so the check marks track the actual visibility of the Coordinates, Delta Coordinates, Grid and Status toolbars
- the Coordinates and Delta Coordinates toolbars are hidden when the active plot tab is not the Plot tab and restored when returning to it; their toggle actions respect the active tab

14.10.2026

Gerber parser:

- the polarity changes (LPC/LPD) are composed 1 time at the end of the file, about 4 times faster on files with many polarity changes; the geometry is the same
- a path that is open at a polarity change is stored with its own polarity (before, it got the new polarity)
- the fusion of the Gerber geometry at the end of the parse uses Clipper2 (pyclipr) if it is installed: about 2-4 times faster on large files; the result is the same within 1e-7 mm, with fewer vertices. Install it with: pip install -r requirements-optional.txt
- if pyclipr is missing or the geometry is not safe for it (invalid polygons, coordinates out of range, rings smaller than 1e-9 units), the old GEOS fusion runs
- fixed: the old fusion could make a false hole where 2 parts overlap (seen on an Eagle top layer)

Preferences:

- all the Preferences pages use the columnar layout; the 'GUI Layout' option (Normal / Columnar) is removed
- columnar layout: the number of columns uses the median width of the groups, so 1 wide group does not remove a column for all the other groups
- columnar layout: a column is not wider than the widest group; the free space stays on the right side. If the groups fit in fewer columns, the used columns get the free width
- columnar layout: the groups are inside the layout margins; a hidden group takes no room
- fixed: in the columnar layout the groups could overlap, because some groups report a too small height before they are shown
- fixed: the columnar layout raised an error each time Qt asked for its expanding directions (PyQt6 has no Qt.Orientations)

Other:

- fixed a bug when changing colors for objects
- new feature: Altium .pcbdoc import in File -> Import -> Altium PCB directly to Gerbers and Excellons
- new feature: KiCad .kicad_pcb import in File -> Import -> KiCad PCB directly to Gerbers and Excellons
- new feature: Sprint Layout 6 .lay6 import in File -> Import -> Sprint Layout directly to Gerbers and Excellons
- new feature: Eagle .brd import in File -> Import -> Eagle Board directly to Gerbers and Excellons
- new feature: OrCAD/Allegro .brd import in File -> Import -> OrCAD/Allegro Board directly to Gerbers and Excellons
- OrCAD/Allegro import: boards saved by Allegro 17.2 and 24.1 are checked against Gerbers made by Allegro; 17.4 to 19.0 boards are read with a warning and only if the strict checks pass; 16.x boards are refused (save them again in 17.2 or newer)
- OrCAD/Allegro import: Eagle and Allegro boards both use .brd; the content of the file selects the format (an Eagle board is XML)
- tests: pytest options (for example -q or -x) do not stop the tests that import appMain any more (new tests/conftest.py)
- fixed a crash when the Space key toggles the plot of the selected objects: the default Properties tab deleted the UI of an object; now the UI is kept, and if a UI is deleted it is rebuilt
- the plot toggle (Plot checkbox, Space key, Enable/Disable plots) does not plot the object again if its shapes are still on the canvas; it only shows or hides them. The UI does not block any more on big objects (50000 shapes: 0.5 s instead of a full tessellation)
- fixed: the show/hide of a shape group did not always update the canvas (the shape collection was not marked dirty); the per-tool show/hide of Excellon was slow on big objects
- the selection of a Gerber object puts it over the other objects without a new plot: only the draw order of the shapes on the canvas changes (52000 shapes: 0.5 s instead of a full tessellation of the selected object). If the object has no valid full plot, it is plotted again as before

13.10.2026

Paint / NCC / camlib:

- the joining of paths in Paint and NCC (paint_connect) is about 4 times faster on large jobs; the output is the same
- the clearing methods (Standard, Seed, Lines, trace filling) use the circle steps setting as the number of segments of a full circle
- a connecting move between 2 paths is checked against the real tool radius, with a small tolerance for paths at exactly 1 tool radius from the edge
- Lines mode and the trace filling keep the last scan line next to the edge (before, it was always removed)
- the check of drill points against the exclusion areas uses the exact distance

Isolation Tool:

- the Simplification option is on by default, with a tolerance of 0.001 mm (before: off, 0.01 mm); the isolation ring of a round pad has 64 G-code lines in place of 128, and the path moves less than 1 um toward the copper

12.10.2026

Levelling Tool:

- GRBL commands run as serial operations with 1 owner; Stop cancels the running operation before its next write
- after Stop, a timeout, an alarm or a failed probe, GRBL accepts no motion until a reset and a successful home cycle
- probing starts only when GRBL is Idle and the units, G90 and G94 are confirmed; each probe is checked before the next move; Z lifts only when it is safe
- probing uses G20 in inch projects; GRBL positions are changed to the app units ($13)
- probe and import results are published as a whole in the GUI thread, only for the same points, target object and units
- real connect check (removed a development hack); Connect toggles; the port closes on exit; no GUI freeze on connect, port list or homing
- fixed jog, Zero (G10 L20 on the active WCS), the phantom manual probe point, old probing G-code, and handler and signal leaks
- a cancelled or failed operation can never be reported as a success; motion keeps its owner until GRBL reports Idle, also through a feed hold or an open door
- each connect has its own handshake; only the GRBL startup banner confirms a reset; the serial port has a write time limit
- alarms that arrive between commands are kept and handled; a partly received line is read to its end; a late reply of an earlier command is never used as a new reply
- Home works after Stop and Reset (the lines that GRBL sends before its banner are ignored)
- a newer probe or import replaces an older one; Apply uses a frozen copy of the height map and its units
- a refused Probe click no longer discards a running probe or a valid height map
- a mode change clears all probe data; a target change releases the manual point handlers
- removed the unused send_grbl_command() and send_grbl_block()
- the handshake keeps a partly received line, so an alarm split across 2 reads still blocks Home
- Reset clears the old received data before it sends the reset byte; if the receive buffer cannot be cleared, Home stays blocked
- an imported height map (Import Height Map and the import of points) now makes the _levelled CNCJob
- the LinuxCNC probing G-code has its notes as comments, not as plain text
- the Avoid Step must be greater than zero; the hole avoidance loop has a limit and can no longer freeze the GUI
- a controller change makes the probing G-code again for the new controller
- added controller regression tests and end-to-end workflow tests for GRBL, MACH3, MACH4 and LinuxCNC

levelling_interp:

- every G1 move is compensated; G90/G91, canned cycles, modal settings with axis words and trailing-dot numbers are handled
- a G53 line keeps its G0/G1 motion mode and makes the moved work axes unknown, so a following G1 is not compensated until they are known again
- G2/G3 arcs in the G17 plane (I/J or R, full circles, helical arcs, G90.1/G91.1) are split into height-compensated G1 segments; the segment length is the CNCJob seg_x/seg_y, else 1 mm or 0.04 in
- an arc that can not be split (G18/G19, G93, a bad radius, other words) is kept and gets an explicit G2/G3 word (after a block delete and a line number)
- G91 G1 moves are compensated when X/Y/Z are known; the Z delta comes from the offset in effect, so rounding errors do not add up
- G28, G30, G92, G92.1/.2/.3, G10 L2 and G10 L20 make the affected axes unknown
- added arc and G91 tests and a G-code path simulator that compares the levelled path with the original path plus the height map
- an arc is a full circle only when its sweep is below 5e-7 rad (as in GRBL); ends 1 output unit apart are a short arc, not 360 degrees
- an optional block delete "/" line can be skipped by the controller: after it, the modal settings and axes that it changes and the Z offset in effect are unknown, so arcs and G91 moves are not compensated until absolute words set them again
- a line with expressions, parameters or other unknown text (X[1], F[100], X#1) is kept unchanged, makes the position unknown and is reported in a warning
- a cutting move that is not compensated because its position, distance mode or motion mode is unknown is reported in a warning
- an optional "/" line that changes only the Z offset in effect, or makes an axis known with the same value, makes them unknown; it never clears an unknown motion mode
- the height map is valid only in its frame (the job units and the first work coordinate system that the program selects before its first move); a G1 move in other units or in another G54..G59.3 system is not compensated and is reported in a warning
- a change of units or of the work coordinate system makes the position unknown
- the position is unknown at the start of the program (no 0.0 default): a cut is compensated only after X, Y and Z are set
- a units or work coordinate system word after the first possible move never declares the frame of the height map, also when G28, G53 or an unknown line made the position unknown; nonmoving setup headers do not count as moves
- a line that moves the X/Y work origin (G92 or G10 L20 with X/Y, G10 L2 with X/Y/R, G92.1/G92.2/G92.3) puts the rest of the program out of the frame of the height map, also with unresolved expressions or parameters; for a Z-only shift see the Z zero line below
- G10 invalidates the map only when its P target is the map work coordinate system or cannot be established safely: P0 is the active system and P1..9 are G54..G59.3; a write to the map system also invalidates it while another system is active
- G52 with X/Y (local offset), G51 (scale) and G68 (rotation) also put the rest of the program out of the frame of the height map
- a line with a G word that is not read (lower case "g20", "g92.1", "G[92]", "G#1") can be any G code, so the rest of the program is out of the frame of the height map
- a subprogram call or flow control (an O-word with call, sub, while, if, repeat, ...; M98; G65) puts the rest of the program out of the frame of the height map; a plain program number (O1234) does not
- G1 moves under cutter radius compensation (G41/G42 until G40) are not compensated, because the tool is off the programmed X/Y; arcs are not split; the moves are reported in a warning
- a comment at the start of a line ("(c) G41 D1") no longer hides the commands after it; a new Z word goes after the last word of the line, and the G word of an unsplit arc goes after a leading comment
- a Z zero change (G92 Z, G10 L20 Z) right after a contact probe (G38.2, G31) at a known X/Y inside the map, with the probe and the zero in the units and the WCS of the map, re-references the height map to the probed point, so the Toolchange_Probe_MACH3 tool change keeps a correct compensation (before, the error was the map height at the tool change point); any other Z zero change of the map coordinate system (also G10 L2 Z, G52 Z) puts the rest of the program out of the frame of the height map
- Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>" are separate work coordinate systems; G70/G71 (ISEL, Siemens) are units words
- G51.1 (mirror), G16 (polar coordinates) and G7 (diameter mode) also put the rest of the program out of the frame of the height map
- canned cycle moves (G73, G76, G81..G89, also the holes under a modal cycle) are not compensated and are reported in a warning
- a G code that the tool does not know (NCCAD9 G77, Mach3 G12/G13, G5 splines, ...) makes the position and the motion mode unknown; G76 (NCCAD9 reference travel) makes the position unknown
- an axis word of a line that is not a G0..G3 move (a probe stop, G43.1 Z) is not taken as a position, and G43/G49 make Z unknown: before, "G43.1 Z1" then "G1 X1 Y1" moved the tool up to the compensated Z1
- the warning for moves outside the frame of the height map also names a change of the work origin or of the Z zero, a subprogram call and a G word that can not be read

19.09.2026

appMain.py:

- added complete updater system: version checking, manifest validation, download with progress/cancel, external transactional apply with automatic rollback
- integrated Digi public share transport with dynamic short URL resolution and optional explicit UUID pin
- added automatic update check on startup (configurable via preferences) and manual Help → Check for Updates
- added release preparation workflow for maintainers
- added rollback/revert support with restore point management
- fixed download worker error handling: transport setup failures now properly emit completion and reset active state
- added stable version-independent preferences and factory defaults filenames (no longer include version number)
- added legacy defaults file discovery and migration for versioned files

defaults.py:

- refactored AppDefaults to use version-independent persistence: stable filenames, obsolete 'version' field stripped on load/save
- added factory defaults snapshot with transactional write: serialize to temp file then os.replace, preserving existing bytes on failure
- added safe .get() fallback pattern: AppDefaults.get() and AppOptions.get() return factory baseline when key missing
- added AppOptions baseline parameter for sparse overlay with falsy value preservation
- added find_legacy_defaults_file() for discovering versioned legacy files (exact version match, then mtime with filename tiebreak)
- fixed propagate_defaults() to use fallback-aware accessor and skip keys absent from both runtime and factory defaults
- fixed mutable aliasing: all factory default injections now use deepcopy
- removed obsolete version-based migration logic; load() now merges saved data over factory defaults

Bookmark.py:

- fixed mutable defaults aliasing: build_bm_ui() now deepcopies fallback and preserves existing runtime dictionary identity
- fixed global_bookmarks_limit reads to use safe .get() fallback pattern

appDatabase.py:

- added _database_option() helper with 3-level fallback (options → defaults → factory deepcopy) for safe preference reads
- added _database_option_items() for iterating defaults regardless of underlying type

appGUI/UpdateDialog.py:

- new module: update dialog with release notes display, version comparison, and user choice (install/later/exit)

services/updater/:

- new updater subsystem: transport.py (Digi public share), manifest.py (validation/security), checker.py (version checking), launcher.py (external apply), recovery.py (rollback), release.py (preparation)
- added manifest security validation: checksum verification, path traversal protection, symlink rejection
- added protected paths exclusion (config/, updater/) from payload extraction
- added transactional archive staging with cleanup on failure

updater_app.py:

- standalone updater application for applying updates outside the main FlatCAM process

make_freeze.py:

- cx_Freeze packaging configuration with updater integration and protected path exclusion

appGUI/preferences/:

- added automatic update check preference (global_version_check) with UI checkbox in General Preferences
- added release preparation UI for maintainers
- converted all preference reads to safe .get() fallback pattern across General, Gerber, Excellon, Geometry, CNCJob, Tools, Utilities groups

appHandlers/:

- converted all preference reads to safe .get() fallback pattern in appIO, appLifecycle, appEdit, appPlotManager, appObjectOps, appSignalConnector, appCanvasEvents

appPlugins/:

- converted all preference reads to safe .get() fallback pattern across all tools: ToolPaint, ToolIsolation, ToolMilling, ToolDrilling, ToolCutOut, ToolNCC, ToolFilm, ToolFollow, ToolPanelize, ToolDblSided, ToolExtract, ToolPunchGerber, ToolCopperThieving, ToolSolderPaste, ToolTransform, ToolFiducials, ToolLevelling, ToolMarkers, ToolQRCode, ToolRulesCheck, ToolShell, ToolSub, ToolAlignObjects, ToolCalculators, ToolDistance, ToolEtchCompensation, ToolImage, ToolInvertGerber, ToolMove, ToolOptimal, ToolPdf, ToolReport
- fixed ToolPdf runtime preference precedence for gerber_circle_steps

appEditors/:

- converted all preference reads to safe .get() fallback pattern in Gerber, Excellon, Geometry, GCode, Text editors and their plugins

appObjects/:

- converted all preference reads to safe .get() fallback pattern in AppObject, GerberObject, ExcellonObject, GeometryObject, CNCJobObject, DocumentObject, ScriptObject, ObjectCollection
- fixed gerber_color_list initialization: missing runtime storage now populated with deepcopy before mutation

appParsers/:

- converted all preference reads to safe .get() fallback pattern in ParseGerber, ParseHPGL2

tclCommands/:

- converted all preference reads to safe .get() fallback pattern across all Tcl commands

camlib.py:

- added _app_option() helper for safe preference reads with factory fallback
- converted geometry_* legacy fallback chains to use _app_option()

appCommon/:

- converted preference reads to safe .get() fallback pattern in Common.py, RegisterFileKeywords.py

tests/:

- added test_database_option_fallback.py: mutable factory fallback independence and precedence
- added test_defaults_audit_regressions.py: bookmark aliasing, propagation fallback, factory snapshot equality
- added test_factory_snapshot_atomic.py: transactional write guarantees (dump/replace failure preservation, temp cleanup, mode restoration)
- added tests/updater/: comprehensive updater tests covering transport, manifest validation, preferences integration, legacy discovery, falsy preservation, frozen packaging

16.09.2026

appMain.py:

- massive refactoring: extracted signal connections, menu handlers, layout, lifecycle, canvas events, object operations and UI actions into 5 new handler modules (appSignalConnector, appLifecycle, appCanvasEvents, appObjectOps, appUIActions)
- removed unused imports (urllib, shapely ops, subprocess, webbrowser, platform, numpy inf, etc.)
- fixed tools_db.FlatDB creation race condition with open('x') + FileExistsError guard
- added graceful ToolImage plugin fallback when rasterio is not installed
- delegated on_options_value_changed and on_app_restart to AppLifecycle

appDatabase.py:

- added load_tools_database() with validation and normalization of tools DB records
- fixed tool ID management: sort/add/copy/delete now preserve actual IDs instead of re-indexing
- added deepcopy for tool data to prevent shared-state mutations
- changed mill shape/job/offset combos to FCComboBox2
- added close confirmation support (confirm_close callback on tabs)
- improved error handling on DB load/save with proper exception types

appPlugins/ToolCutOut.py, ToolDrilling.py, ToolIsolation.py, ToolMilling.py, ToolNCC/Ncc.py, ToolPaint/Paint.py:

- replaced duplicated file-read + json-parse blocks with load_tools_database()
- fixed tool_target comparisons to use integer constants instead of translated strings
- added deepcopy for tool data copied from DB
- removed redundant Tools Database tab-closing loops

flatcam.py:

- added Qt message handler to suppress noisy QThreadStorage warnings during shutdown

appWorkerStack.py:

- improved worker thread shutdown with shared timeout, requestInterruption() and forced terminate fallback

appGUI/GUIElements.py:

- added confirm_close callback support for detachable tabs
- fixed missing event.accept() in drop handling
- fixed systray Run Script to call the renamed on_file_run_script method

appGUI/MainGUI.py:

- removed redundant tools_db_changed_flag
- fixed method name typo: on_file_run_cript -> on_file_run_script

appHandlers/appIO.py:

- renamed on_file_run_cript to on_file_run_script
- added handling for tabs that refuse to close during New Project (retained tabs warning)

appCommon/Common.py:

- fixed property setter: _log -> _log_level

appPlugins/__init__.py:

- added logging warning when ToolImage plugin fails to load instead of silent pass

appParsers/ParseGerber.py:

- fixed aperture parameter parsing to handle None or empty apParameters without raising AttributeError

camlib.py:

- reformatted clear_polygon_shrink() signature for readability
- fixed ApertureMacro union logic: now detects GeometryCollection results instead of rejecting non-Polygon geometry
- refactored flatten_shapely_geometry() to recursively flatten nested multipart and iterable geometries

.gitignore:

- updated entries for __pycache__, .venv, *.pyc.*

appPlugins/ToolLevelling.py:

- implemented the G-code height compensation (autolevelling): the height map is applied to each tool G-code of the selected CNCJob object and the result is a new '<name>_levelled' CNCJob object; the source object is not changed
- the bilinear method falls back to nearest-point levelling (with a warning) when the probe points do not form a regular grid
- only G1 cut moves at Z <= 0 are levelled; G0 moves, arcs, probing moves (G31, G38.x) and offset/home moves (G10, G28, G30, G53, G92) are not changed
- autolevelling is refused for the Roland, HPGL, laser and solder paste preprocessors
- GRBL: the work offset is read with '$#' before probing and the probe results are converted from machine to work coordinates and matched to the nearest probe point
- MACH3, MACH4, LinuxCNC: the height map import now accepts comma or space separated rows with extra axis columns, skips blank and invalid rows, matches the rows to the nearest probe point and applies the autolevelling automatically
- fixed send_grbl_command() raising TypeError on the controller answer (bytes vs str); it now returns the decoded answer as text
- the autolevelling is refused when there is no probed or imported height map, or when the units of the object and of the application do not match
- the autolevelling runs in a worker thread and a second run is blocked while one is in progress
- added Excellon object support as autolevelling target alongside Geometry (CNCJob) objects
- added bilinear heatmap visualization: probe point cells are colored by interpolated height bands once the height map is complete
- GRBL: added dedicated probe command handler (_send_grbl_probe_command) with 10s timeout and line-buffered reading for reliable [PRB:] detection
- GRBL: now reads the active work coordinate system (G54-G59) via $G before querying work offsets with $#, so probing works in any WCS
- height map (probed or imported) is now preserved when re-opening the tool; the mode radio UI is updated without resetting the stored geometry
- export of probe data now writes CSV (X,Y,Z per probe point) from the voronoi storage instead of the raw GRBL output string
- fixed baudrate combo duplicate check to use findText() instead of model().stringList()
- added show_probing_geo_sig signal and plot_probing_pts checkbox to control probing point visibility after probing/import

appPlugins/levelling_interp.py:

- new module with pure (no Qt) helpers for autolevelling: bilinear grid and interpolation, nearest-point offset, match tolerance, height map row parser, GRBL probe and work offset parsers and a modal G-code line leveller
- added parse_grbl_active_wcs() to extract the active G54-G59 work coordinate system from $G output
- parse_grbl_work_offset() now accepts an active_wcs parameter (default G54) and reads the matching G5x offset line
- offset regex extended to match G54 through G59
- parse_height_map_line() and parse_grbl_probe_output() now reject non-finite coordinate values

tests:

- added test_levelling.py, test_levelling_tool.py and test_levelling_journey.py; the journey tests generate G-code from the Gerber test files, apply height maps from MACH3/MACH4/LinuxCNC files and GRBL probe output and check every levelled Z value
- added tests for Excellon autolevelling support, bilinear heatmap generation, GRBL active WCS parsing, probe command timeout, height map persistence, non-finite value rejection and CSV export

22.03.2026

appMain.py:

- refactored the __init__ method into smaller methods and a number of small fixes

21.03.2026

appMain.py:

- updated the splash screen and rescaled it to 622x344
- updated the version strings to reflect the current year

20.03.2026

flatcam.py:
- Changed application exit to use os._exit() instead of sys.exit() to prevent Windows 0xC0000005 crashes caused by Qt/VisPy C++ object destruction during Python GC
  appMain.py:
- Fixed systray icon crash by checking self.trayIcon is not None before showing
- Fixed multiprocessing pool cleanup: changed terminate() to close() + join() for graceful shutdown
- Improved ArgsThread shutdown: added pipe close mechanism to unblock listener.accept()
- Added explicit VisPy canvas resource release before QApplication quit
- Enhanced thread termination with 3-second wait and forced terminate if still running
- Removed redundant close_command() call
- Fixed double-click selection: re-select object if toggled off, removed unnecessary selection shape deletion
- Added version check validation to prevent crashes when version is not numeric
- Code formatting improvements

camlib.py:
- Added type hints throughout (Geometry, CNCjob classes)
- Fixed typos: "Punch gerber" → "Punch Gerber", "extracut_length" → "extracut_length", "probel" → "probe", "soldepaste" → "repasted"
- Added GeometrySequence import for type annotations
- Fixed clear_polygon_seed() and clear_polygon_lines() method signatures with proper type hints
- Improved geo_optimized_rtree() with better exception handling
- Fixed coordinate extraction for MultiLineString/MultiPolygon in path optimization
- Added proper handling for None geometries in geo_storage processing
- Fixed regex patterns for GCode parsing (removed unnecessary escape characters)
- Added noqa comments for legitimate type comparisons
- Code reformatting and cleanup

ToolPaint/PaintGen.py:
- Massive refactoring: extracted worker logic into separate methods (_create_params, _parse_tool_diameters, _find_tool_uid, _get_params, _buffer_geometry, _process_polygon, _simplify_geometry, _update_progress)
- Improved code organization and readability
- Better separation of concerns
  ToolNCC/NccGen.py:
- Fixed typo: "sutracted" → "subtracted"

18.03.2026 

- ToolPaint: more refactoring
- camlib: some type hints and reformatting

17.03.2026

- ToolPaint and ToolNCC: refactoring the UI elements to use the same name as preparation to extraction of the common code into a 3rd common class
- ToolNCC: massive refactoring and fixes
- ToolNCC and ToolIsolation: fixed the determination if the selected tool diameter is suitable for operating on the selected object
- ToolNCC: fixed bugs

16.03.2026

- remade the workers system; now when assigning a worker we can set the priority of the worker: LOW, NORMAL (default), HIGH
- improved the DXF parsing performance and fixed some bugs
- ToolImage: fixed an import error
- ParseSVG: improved the SVG parser by adding support for pt, px units
- ParseSVG: added support for "Symbol" SVG element
- ParseSVG: fixed the usage of offsets, rounded corners and some wrong application of factors
- VisPyVisuals: big improvements in the performance of the plotting; in some cases the performance was improved by a factor of 3 to 5 times
- implemented batch plotting for FlatCAM objects
- VisPyVisuals: huge improvements in plotting speed on the 3d engine VisPy canvas
- VisPyVisuals: another whooping ~30% performance boost in plotting / panning / zooming on top of the previous improvements by moving fully to numpy data structures
- ToolMove: increased the performance of the move tool (and any offsets) by 10...20 % by using the new Shapely 2.0 vectorized operations
- ParseFont: refactoring and optimization
- ToolPaint: start the refactoring of the tool
- ToolPaint: refactored into its own folder with MVC architecture
- ToolPaint: fixed a bug where the signals could be connected multiple times at the same method
- ToolNCC: refactored into its own folder with MVC architecture

15.02.2026

- ParseDXF: refactoring and optimization
- ParseDXF: fixed an issue with the parsing of blocks
- ParseDXF: fixed an issue with the parsing of the splines
- ParseDXF: added support for text entities
- updated TclCommand OpenDXF

14.03.2026 

- ParseGerber: refactoring and optimization
- appMain: some refactoring and fixed the annoying issue with the selection shape being left on the screen when moving fast
- ParseGerber: fixed an issue with setting an already existing list into another list
- ObjectCollection: fixed an issue with the index of the objects not being valid for a specific case
- dealt with linter warnings in appMain.py
- ToolPDF: added an alternative pure python parser and made the pikepdf mode optional

13.03.2026

- ParsePDF upgrade and refactor

12.03.2026

- PDF Tool: refactoring the tool into its own folder
- ParsePDF.py: refactoring the parser to use numpy for the bezier_to_circle method
- ParseGerber: optimization of the geometry creation process
- ParseGerber: optimization of the geometry creation process - part 2

8.10.2025

- added some changes suggested by Matthew Kaprocki on bitbucket regarding new Gerber extensions 
- fixed saving a project if the project has math.inf or math.nan values; this does not address the issue of why those values are there but it cleans the project before saving it

9.09.2025

- in MainGui fixed the auto-repeat when using the key shortcuts which led to issues with repeating the same command multiple times
- actually fixed the above (the issue was created by updates in VisPy v0.15)
- fixed a number of issues with loading/creating objects by removing as much as possible creation of the app instance by injecting the attribute in the object classes

5.03.2025

- upgrade to use the new notations in the Shapely's buffer method for the cap_style and join_style parameters (used to be integers, now they are strings or defines)

02.02.2025

- modified the makefile file (contribution from @Paul Munsch on bitbucket)
- fixed an error from using the latest numpy version regarding missing Inf attribute (now it is inf, lower case)

19.06.2024

- fixed Issues #49. Path mismatch for SVG icons -> missing checkboxes fixed as suggested by Stefan Bruens, by adapting the paths in the stylesheets files (dark and light)

31.03.2024 

- added the ability to use no path optimization
- camblib.generate_from_geometry_2(): added the usage for different kinds of path optimizations set in the Preferences -> Geometry
- some minor objects name refactoring

29.03.2024

- continue refactoring methods from the appMain
- appIO: fixed an encoding error when trying to open a DXF file

28.03.2024

- major refactoring: started to move the methods connected to the Edit menu to their own class to clean up the App mega class

20.02.2024

- Punch Gerber Plugin: fixed issues with iterating ovr a MultiPolygon
- GerberObject: fixed Gerber merging functionality
- Film Plugin: fixed error on generation of positive film with punched holes due of the aperture of type "REG" (copper plane / region)

11.01.2024

- Paint Plugin: fixed an issue where a Gerber object cannot be painted using the Single Polygon selection correctly because it painted the whole geometry
- Geo Editor: improving the selection and deletion - work in progress

7.01.2024

- Milling Plugin: when an object has a "tools_mill_tooldia" property set as an object option it is used to set the Tool Dia field

6.01.2024

- NCC Plugin: make sure to use the CutZ value set in Preferences NCC Tool section

5.01.2024

- Follow Plugin: added more parameters: simplification and union
- Follow Plugin: the resulting LineString geometries are now merged together to minimize the number of lines

3.01.2024

- Geo Editor -> Simplification Sub-tool: fixed an issue when calculating vertexes number

16.12.2023

- when tabs are set as not detachable, double_clicking the Plot_Area tab will toggle the notebook area visibility
- fixed the Full Screen issue with not remembering the toolbar's state before full screen event

6.12.2023

- Paint Tool: fixed the calculation of object bounds
- camlib: updated the method used to calculate the extrapolated lines for the laser_lines method: camlib.fill_with_lines()
- Drilling and Milling Tools: added support to choose the GCode for turning On the laser

25.11.2023

- some names are updated
- made sure that when a new project is created that the levelling grid shapes are deleted
- some code optimization and code refactoring
- Levelling Tool: added parameter (in Preferences too) to control the probe tip diameter which is reflected in the probing location mark diameter
- Levelling Tool: when adding a Grid probing and the avoidance of Excellon is used, now the probing locations will be offset enough so the probing is not done in the Excellon holes

14.11.2023

- trying to solve an idiosyncrasy of MacOS which do not allow modifying the main menu in a non-main thread which is done after editing an object

29.10.2023

- when failing to save a project make sure that we can still close the app correctly
- Milling Tool: when generating CNCJob's from a single_geometry type fixed an issue getting bad bounds values (Inf) which did not allow saving the project that included such a CNCJob object due of failing to serialize such an object
- fixed some redundancy in some RegEx expressions
- minor fixes

19.10.2023

- fixed preprocessors issue where the start GCode was not added due of trying to access an object that did not exist (because its name was not changed to reflect the changes in other parts on the app)
- updated the requirements.txt file
- added qdarktheme package into the code
- upgraded code so the app will work with Python 3.12

27.09.2023

- Gerber parser: added support for obsoleted G55 command before a D03 (flash)

23.09.2023

- fixed aperture macros parse for Gerber files generated from KiCAD 7.0.7

6.07.2023

- Isolation Plugin: reversed the milling direction between the conventional and climbing milling types
- Isolation Plugin: code refactoring

5.07.2023

- Excellon Editor: fixed crash when editing hole diameters in the Tool Table
- Isolation Plugin: fixed some possible issues when using rest machining
- Drag and Drop files works now only in the Notebook and in the Plot Tab
- Gerber Parser: fixed a bug where a region is a MultiPolygon and failed because of changes in Shapely starting with version 2.0

4.07.2023

- fixed a number of bugs reported in the issue #32 here: https://bitbucket.org/marius_stanciu/flatcam_beta/issues/32/crashes-on-close-random-crashes-that
- Isolation Plugin: fixed some errors due of trying to get the is_empty property from a list (geometry that was flattened)
- Gerber Editor: fixed issue with editing the rectangular aperture dimensions in the Tool Table (I used wrong parameter names to the Shapely box() method)

1.07.2023

- NCC Plugin: fixed the determination of optimal tool
- NCC Plugin: fixed a bug where when using the Rest Machining an error is issued due of trying to create a MultiPolygon having in the list another MultiPolygon; fixed by flattening the list

30.06.2023

- NCC Plugin: fixed the Isolation operation mode
- a number of methods were updated
- modified some strings in regard to rest machining

29.06.2023

- NCC Plugin: updates to fix a bug when doing copper clearing with Rest option

19.06.2023

- added a new signal allowing printing messages in statusbar without polluting the Tcl Shell; to be used in the future for low-ranking messages

17.06.2023

- NCC Plugin: modified the previous change and now the simplification action is much bigger reducing the number of coordinates by a factor of 20 (20 times less)
- Paint Plugin: same as above
- Ncc Plugin: added some more tooltips
- Isolation Plugin: fixed some issues when using the Rest Machining option

16.06.2023

- NCC Plugin: made a small optimization by adding a simplification in the clearing geometry

15.06.2023

- a bit of optimization in camblib.clear_polygon() method
- added the update of bounds for the TclCommands: Mirror, Scale, Offset, Skew, Buffer

12.06.2023

- Geometry Editor: fixed the selected shapes width issue; now the selected shapes are thicker
- Geometry Editor: no longer matter the order (top-bottom or bottom-top) of selection, now the last selected shape has the data updated in the UI
- Geometry Editor: fixed the simplification tool to work on a selection of shapes

10.06.2023

- Isolation Plugin and Isolate Tcl Command: optimized the isolation method

8.06.2023

- Gerber Parser: minor changes, cleaning up the KiCAD junk lines if the Gerber file is made by KiCAD
- Allowed the opening of certain types of damaged Gerber files (apertures not defines but used) but WARNINGS
- NCC Plugin - fixed a minor bug that crashed the NCC Tcl Command; reported by @Aldin Halilovic on bitbucket

7.06.2023

- updated in GUI Elements for FCButton, FCFrame and FCLabel custom widgets adding an stylesheet optimization method

5.06.2023

- updated the FCButton and FCLabel custom widgets
- Paint Plugin: fixed issues caused by the latest changes in the Shapely module
- NCC Plugin: some changes in the method used by the Tcl Command
- in Preferences, added a control in the General -> GUI Settings to control the font size for the entire application; require an app restart
- Sub Tool: fixed an issue due of an incorrect parameter for a QTimer start

31.05.2023

- Pdf Import plugin: fixed an issue with assigning a wrong property to a Qtimer
- Extract Plugin and Punch gerber Plugin: fixed a wrong build of the UI triggered even those tools where not launched yet
- Pdf import Plugin: made sure that the keys in tools are integers
- Pdf Import Plugin: added the source code to the imported Gerber file

25.05.2023

- Geometry Editor: subtool Path: fixed issue of not registering the new click when using the Shift modifier
- Geometry Editor: subtool Path: the coordinates displayed when using the Shift key modifier are the coordinates for the end of the line and not the mouse cursor
- Geometry Editor: added some constraints in regards of the required selection before using some of the subTools

24.05.2023

- for all Editors now there is a shortcut key (key "C") that will toggle the cursor data in case the cursor data is in the way
- Geometry Editor: the Path tool now has a new behavior: when key modifier SHIFT is pressed and the Grid is OFF, when the line is drawn and it is near the 0, 45 and 90 degrees, in each quadrant, then the drawn line will follow the nearest target angle (0 or 45 or 90 degrees) allowing the drawing of straight lines.

19.05.2023

- CutOut Plugin - fixed the manual adding of gaps
- the selection shapes are now moved from Move Plugin to AppMain
- Fiducials Plugin: fixed errors due of changes in the Shapely module
- Fiducials Plugin: fixed an error where in Basic mode the soldermask opening is added incorrectly
- Film Tool: fixed the export to PNG to be done at correct DPI

17.05.2023

- Thieving Plugin: fixed errors due of changes in the Shapely module

16.05.2023

- NCC Plugin: fixed the Standard method of copper clearing (both normal and with Rest machining)

14.05.2023

- Isolation Plugin, NCC Plugin and Find Optimal Plugin: fixed not finding the optimal tool diameter (the one that will isolate completely a Gerber object)
- NCC Plugin - minor code refactoring

05.05.2023

- fixed an erroneous opening for a Gerber file generated by KiCAD 6.0.11, due of a rectangular flash being eliminated by the global unary_union operation
- replaced some type hints with using the Union

15.04.2023

- changed the shapely imports a bit according to the specifications of Shapely 2.0
- changed the requirements.txt file to reflect the need for at least Shapely in version 2.0
- some code leftovers are commented
- when parsing a new Gerber object, I am "preparing" each geometry added therefore making the Gerber object geometry possible faster to process

08.04.2023

- fixed some really long strings in the Geometry Editor toolbar actions
- modified the paint and buffer icons
- optimized the editor menu/toolbar action names
- major change: from now on the only dimensional units available are those from ISO METRIC system
- minor name changes

22.03.2023

- in the toolbars now there is text under the icons (which can be turned off from the toolbar context menu)
- the GUI state is now saved and restored through QSettings

13.03.2023

- in Tcl Commands: export_dxf and export_gerber, fixed a mistake in declaring a wrong name of a required option
- in Tcl Command set_path added an optional parameter which allows to create a directory where path is to be set, if the directory does not exist

6.03.2023

- fixed some possible issues due of changes in version 2.0 of Shapely
- removed the import * statement from most of the app

01.03.2023

- in Tool Milling made sure that deleting the only tool will not crash the application
- added a new parameter in Preferences to control the number of processes created by the Pool() - more processes better performance but also a lot of memory consumed
- made sure that the display of messages in the Status Bar is done asap

19.01.2023

- added a new method for bilinear interpolation

18.01.2023

- in Autolevelling Plugin made sure that when adding manual probe points mouse dragging with the right button is not counted as end of adding operation
- in Autolevelling Plugin, when adding manual probe points, added an option that prevent adding a probe point within a drill hole of an Excellon object that is plotted on canvas
- in Autolevelling Plugin, preventing the add of a probe point on Excellon hole checkbox is not disabled except for the Manual mode
- in Autolevelling Plugin, the status of some checkboxes (avoid Excellon holes and plot points) is stored between app restarts
- in Isolation Plugin, added an convenience shortcut button to launch the Milling Plugin.

16.01.2023

- fixed a decoding error in the Excellon parser
- some PEP8 fixes
- fixed the Voronoi generation in the Autolevelling Tool (removed the Foronoi package due of issues and now using the embedded functionality from Shapely)
- fixed the enable of Levelling Tool only for CNCJob objects that were made from geometries that were segmented

15.01.2023

- PEP8 changes

19.11.2022

- added a 'return' button when a fatal error is encountered allowing the user to continue the work
- fixed a crash in Milling Plugin when trying to mill slots that do not have drills in the same file

9.11.2022

- when changing the style for the decorations from Preferences, now change is applied immediately

7.11.2022

- in Drilling Plugin fixed a situation when having tools with the same diameter will get them multiplied by the number of those tools

6.11.2022

- if PikePDF module is not available then show an error but run the app (currently, for Python 3.11 is the only module not available)

19.10.2022

- added a hack so the Gerber files from Allegro 17.2 (which do not follow the Gerber specifications) can be loaded
- fixed an issue when exporting huge SVG files from the Film Tool by adding a fallback and using a custom XML parser with the 'huge_tree' option set

2.10.2022

- really small UI change in 2-Sided Plugin

27.09.2022

- in Milling Plugin, for Excellon target, re-enabled the control over the Feedrate X-Y parameter

25.09.2022

- added a new setting in Preferences ("3D compatibility") controlled by a checkbox. If the checkbox is checked then multithreading is disabled for the 3D mode (lower performance but perhaps more compatibility, especially for Linux) - this was done from the research done by Matti Eiden on bitbucket
- small fix in ToolCutout Plugin when trying to set a checkbox state with a float value which still works in Windows but creates issues in other OS's

10.09.2022

- hided the main UI on application quit to create a user experience of a shutdown without lag
- added a way to terminate QThreads safely by waiting; should be much safer
- made sure that the ArgsThread class receive the signal to stop
- made sure that on application shutdown, all workers will quit before the actual exit
- added insurance that the QThread where the ArgThread class is running, really receive the interruption request and it is finished

1.09.2022

- added a new feature for Geometry export-as-SVG, the ability to export only the paths (outlines); the new feature is controlled from a new parameter in Preferences -> Geometry -> Export

1.08.2022

- fixed some bugs in Geometry Editor in regards of Buffer Tool
- fixed some issues in the Cutout Plugin by adding more checks
- fixed issues when loading files by dragging in the UI (caused by recent code refactoring)

26.05.2022

- upgraded the UI of Region sub-tool in the Gerber Editor

22.05.2022

- in Gerber Editor upgraded the PadAdd GUI
- in Gerber Editor upgraded the PadArray GUI
- in Gerber Editor upgraded the Track sub-tool GUI
- in Gerber Editor upgraded the Simplification sub-tool GUI

21.05.2022

- more code refactored in the appMain.py
- added properties for the mouse position and mouse clicked position (and setters) and cleaned up the code regarding this data

20.05.2022

- small fix for a bug that interfere with running the 2D graphic mode
- refactored the code for the file registration and for the keywords update into its own class to make the main class smaller
- when creating new files the file names are added to the beginning of the keywords list for the usage of Qt auto-completer

19.05.2022

- added a new setting that control the selection shape creation, in the Preferences. If the checkbox Selection Outline is checked then the selection shape is only an outline.
- fixed the Move Plugin to use the new setting of outline for the selection shape

18.05.2022

- added some safeguards in Cutout Plugin
- fixed the `Toggle Grid Lines` functionality for dark mode

17.05.2020

- in Cutout Plugin added ability to use the margin (full range of values: negative, zero, positive) when applying the cutout over a Geometry source object
- updated the Turkish language strings (by Mehmet Kaya)
- in Cutout Plugin added an extra attempt to solve the issue with creating invalid geometries
- the ArgThread class is no longer launched in case of Linux systems due of crashes

16.05.2022

- finished upgrading the UI in Geometry Editor sub-tools
- fixed an issue that left some parts of the Geometry Editor UI linked to the `Move` context menu action
- started to lay ground for upgrading the UI in the Gerber Editor
- fixed and upgraded the Buffer sub-tool in the Gerber Editor
- fixed adding same aperture multiple times

15.05.2022

- fixed the Tcl command `drillcncjob` so it can use positive or zero depth of cuts
- fixed the Drilling Plugin such that when the Z_CUt (depth of cut) is positive value then the up-to-zero feature will not be used

13.05.2022

- fixed the join-as-gerber functionality

12.05.2022

- changed the syntax highlighting for the AppTextEditor such that the strings are not color marked
- changed the syntax highlighting such that for a dark theme the colors are inverted
- made sure that in the AppTextEditor, when a line is highlighted then the text foreground color is black such that the writing can be read over the line highlight

11.05.2022

- fixed a crash when creating a Document object due of changes in Qt6 (missing QtGui.Qt)
- in Document object fixed the issue with not setting selection color when in a dark theme (essentially got rid of using QPalette)
- in dark theme stylesheet changed the indent of the QCheckBox (and in Radio buttons too)
- updated the FClabel widget with some more properties
- updated the hack to make sure that the Editor sub-tools do not lose the stylesheet of the background
- updated the disabled project item color default value for the dark theme
- in Document Object UI - upgraded the UI and fixed the colors setting
- some refactoring and updated the application strings
- updated Romanian translation

10.05.2022

- upgraded the Geometry Editor main UI
- upgraded the FCButton widget (and made it used everywhere instead of the QPushButton) so it can have the color and font weight properties settable
- styled the border for the QToolButton (for dark mode)

9.05.2022

- fixed an issue in the Paint Plugin where some polygons are discarded in a Geometry object made out of an imported SVG
- fixed an issue in the SVG parser where when parsing a path and getting multiple polygons will generate an invalid compound polygon (the polygons other than the first are seen as interiors even if they are not inside the first)
- made sure that clicking the text in the InfoBar will also toggle the Tcl Shell display as is the click on the Infobar itself

6.05.2022

- minor changes to one of the custom widgets
- added an example of custom titlebar - still needs a lot of work

5.05.2022

- changed the activity GIF's for the dark theme
- changed some of the dark theme icons to the red color
- made sure that the draw color in Editors is inverted for the dark mode
- added a hack so the dark stylesheet for QWidget is reapplied if that Qwidget is removed (I don't know why it is lost in the first time)
- when exiting an Editor with a sub-tool active the cursor data display feature is removed
- updated the GCode header
- fixed the 'light' theme paint icon name
- removed the shortcut for the PDF Import Plugin (Ctrl+Q) since it interfered with the MacOS usage

4.05.2020

- some changes to the dark mode stylesheet to make it look better
- darkened the text color for the dark theme in order to make it easier on the eyes
- in Distance Plugin, when using the big cursor, the cursor colors are fixed to work for the dark mode too
- for dark mode disabled hover highlight on the Status Bar
- more changes for dark theme

2.05.2022

- in Excellon Editor, Copy sub-tool, added UI and ability to copy as array
- fixed an issue in Geometry Editor, Copy sub-tool where when the geometry copied numbers over the set limit then the copy as array is incorrect
- in Geometry Editor fixed an angle issue in the Circle sub-tool
- in Geometry Editor made sure that when using the Move sub-tool if there is no shape selected, when clicking to get selection that point is also used as reference for the Move command

1.05.2022

- fixed persistence of view status for the coordinates toolbars
- fixed broken selection in Excellon Editor
- in Geometry Editor updated the Rectangle Plugin such that when selecting different corners now the drawn rectangle will have those
- in Geometry Editor updated the Circle Plugin such that when the Radius parameters are not linked then an ellipse is drawn

30.04.2022

- in Excellon Editor, Slot Plugin, made sure that once the Editor is active any change for this plugin parameters will be kept until editor is exit or that parameter is changed again
- in Excellon Editor, Drill Array Plugin, upgraded the UI and work in progress for the features
- prettified some GUI elements (some of those that are used in the popup messages)
- in Excellon Editor, finished the upgrade work on the Drill Array Plugin
- in Excellon Editor, finished the upgrade work on the Slot Array Plugin
- in Excellon Editor, finished the upgrade work on the Resize Plugin
- minor changes in the Excellon Editor -> Resize Plugin
- updated the language strings

29.04.2022

- In Excellon Editor, finished updating the Drill Plugin and Slot Plugin
- fixed a bug in the excellon export method, regarding the slots processing

28.04.2022

- in Isolation Plugin made sure that the last displayed message is the warning, in the case of using tool validation and the tool is not validated
- some more work in the Excellon Editor - Drill adding
- some fixes in the image loader when clicking the an image that is in the list of previous loaded files
- a small fix in the SVG parser
- added a new Tcl command that is returning the name of the active object ('get_active') to accompany the 'set_active' command
- a small fix for the 2d graphic mode by replacing the q5agg matplotlib backend with the qtagg backend which should work with the Qt6
- in Calculators Plugin fixed the Electroplating Calculator to work as expected and removed the redundant button 'Calculate'

26.04.2022

- activated the 'View Source' option in the project menu context menu for the Geometry objects
- in Image Import plugin now the created objects have the 'source_file' attribute with content
- working in the Excellon Editor - wip

25.04.2022

- deleted the Calibration Plugin (made redundant by changes in other Plugins)
- some strings were updated
- in SolderPaste Plugin fixed an issue when creating a new project with this plugin open
- updated the application strings
- changed again the FClabel GUI element to allow font size change from a class parameter and modified the way the weight is set
- small UI change in the Image Import Plugin

24.04.2022

- some fixes in the Import Image Plugin
- made sure that the CNCJob objects made out of non-multi-toolGeometries are plotted correctly
- in SolderPaste Plugin fixed an issue where the new parameter 'margin' was taken from the current UI and applied to all tools (nozzles); now each nozzle use his 'margin' parameter
- in SolderPaste Plugin make sure that the preprocessor is set the same for all tools (nozzles) whenever is modified
- added 2 new preprocessor files for the SolderPaste Plugin (GRBL and MARLIN)
- in SolderPaste Plugin made sure that the start GCode is not added for each nozzle but only once at start
- in SolderPaste Plugin added a new parameter: 'Feedrate Rapids' which will set the feedrate for the fast moves as opposed with the previously used G0 command. This allows to fine tune the fast moves to reduce the vibrations

23.04.2022

- fixed some leftovers due of recent changes in the theme management
- added a new feature (new modules are required) in the Image Plugin (Menu -> File -> Import -> Import Image) that allow tracing images. This may allow engraving pictures.
- started to add UI's for the Excellon Editor Tools (really early work)

20.04.2022

- in Solderpaste Plugin fixed the GCode generation; make sure that if no object is selected then the first Gerber object is autoselected
- in Solderpaste Plugin fixed the CNCJob plotting
- in Solderpaste Plugin added a new parameter 'Margin' which allows reducing how much solderpaste is added and therefore adding a space between the solderpaste and the pad boundary
- all CNCJob objects generated by the Solderpaste plugin now have the GCode saved as source_code which can be saved also from the CNCJob object context menu, and edited
- updated some custom widgets in the GUI elements such that the scrolling in the Preferences can be done without blocking on some of the widgets
- remade how the Preferences Tab is constructed such that now is made on demand for each section (tab) therefore making it faster to load (once a section is loaded - by clicking its tab - it will not be reloaded in the current session)
- a fix for the latest change in the Preferences Tab
- some changes in the Geometry Editor UI's and in some cases, fixes for the right-click close action

19.04.2022

- fixed and prettified the 'Light' theme
- some more fixes for the 'Light' theme
- made sure that the 'default' theme gets the 'stronger' colors

18.04.2022

- in Geometry Editor, in Copy Tool added the 2D copy-as-array feature therefore finishing this editor plugin upgrade
- updated the FCLabel widget
- replaced all the FCLabel widgets that have color HTML with the new FCLabel widget that uses parameters for 'color' and weight
- minor changes
- added a way to allow patching FCLabel widget colors for certain cases without having to pass them each instance 
- some changes in the theme selection, added that the default situation is where no theme is applied
- some string changes

17.04.2022

- in Geometry Editor, in Copy Tool - work in progress (adding utility geometry for the array mode)
- in Geometry Editor, in Copy Tool - linear array utility geometry is working
- in Geometry Editor, Copy Tool, finished the copy-as-array feature except the 2D array type which was not implemented yet

16.04.2022

- in Geometry Editor, in the Simplification Tool made sure that the selected shape is highlighted
- in Geometry Editor, in Rectangle Tool added a modification mode where a selected shape (using the SHIFT + click combo) while the tool is active, can be modified
- in Geometry Editor, in Move Tool and Copy Tool added UI's, move projection and cursor data. Also now require acquiring reference point before the action
- in Geometry Editor, in Rectangle Tool added cursor data
- in Geometry Editor, in Circle Tool added UI, cursor data, radius projection and ability to add ellipses
- in Geometry Editor, in Copy Tool remade the UI and started to add the support for copy-as-array (work in progress)

15.04.2022

- in Geometry Editor moved the simplification feature in its own Editor Tool (plugin)
- in Geometry Editor the default draw color is now black
- in Geometry Editor, the Rectangle Editor Tool allows creation of rectangles with the mouse but projecting the length and width dimensions by typing a number (the choice of setting the length or width is based on the direction of the mouse move after setting the first point)
- in Geometry Editor, the Rectangle Editor Tool has now Ui which allows adding a rectangle by parameters

14.04.2022

- in Geometry Editor added a new feature. While drawing a 'Path' now the user can project a direction by moving the mouse cursor in a certain direction and after that by typing a number or an arithmetic simple expression, a line segment will be drawn in that direction with the specified length from the last point
- in Geometry Editor for the Path tool but only when using the 3D engine graphic mode, the mouse cursor is followed by position data
- in Geometry Editor for the Path tool fixed an issue with path projection when changing the grid size while the Path tool is active
- in Geometry Editor, for Path tool, added UI that close on end of the Path tool action; it displays the projected length which now is kept for as long as it is wanted, allowing for path automation in case of repetitive lengths
- solved a ZeroDivisionError exception in the Geometry Editor -> Path Tool
- in Geometry Editor, for Path tool, added the ability to differentiate between creating a multidigit number for the projection and starting a new number (for a new segment)
- added ability to control the color of the project items for the dark theme too
- in Geometry Editor, for the Polygon Tool added the UI, the length projection and the cursor data, just like for the Path Tool
- made sure that some menu entries in the canvas popup menu are hidden when entering into an Editor and restored on exit

13.04.2022

- fixed the display of lines in Distance Plugin when using 'snap to' together with 'multipoint'
- in Geometry Editor - update (some reformatting and adding shape data)
- in Geometry Editor - fixed the Explode tool to work on the result of adding Text geometry
- all the Geometry Editor plugins are moved inside another folder and the UI's are moved into their own class
- in Geometry Editor made sure that the MultiPolygon shapes (like those made by the Text Tool) will update the coordinates in the UI

7.04.2022

- refactoring code in Distance Plugin and making sure that the measurements are done correctly - work in progress

6.04.2022

- some fixes to the Tcl commands contribution from Ali Khalil

5.04.2022

- fixed Distance plugin grid snap restore

3.04.2022

- in Cutout Plugin exposed the any_cutout_handler() method and made sure that the `gaps` parameter case does not matter
- some reformatting and class rename
- when clicking the coordinates toolbars in the status bar now the Distance Plugin is shown and if already displayed, it is closed (toggle action)

2.04.2022

- in Markers Plugin, added a numeric mode
- in Fiducials Plugin, for manual mode: added utility geometry and the ability to use a big cursor
- minor fix in Fiducials Plugin
- make sure that replotting objects is done only for enabled objects

1.04.2022

- in Markers Plugin, for the manual mode added the choice to use a big cursor when manual placing markers

31.03.2022

- changed the app main icons, hopefully they are more visible on black backgrounds than the ones that FlatCAM has
- make sure that the state of the workspace is saved correctly on change
- refactored the imports in the Plugins, moved almost all imports in the AppTool file
- fixed a number of issues, mostly leftovers from moving the UI of a Plugin in its own class
- fixed some bugs in the Punch Gerber plugin
- fixed some bugs where the 'pool' parameter was not passed when creating shapes collections (in 3D graphic mode); I wonder how it worked until now
- added a new feature in the Isolation Plugin: now for all the isolation Geometry objects this plugin can do a supplementary simplification of the geometry using the tolerance parameter defined in the General Parameters. This should lead to a reduced number of tool lifts when doing corners
- fixed aperture marking in Extract Plugin
- in Extract Plugin if no Gerber object is selected then the first Gerber object in the project list is selected (if any)
- in Punch Gerber Plugin if no Gerber object is selected then the first Gerber object in the project list is selected (if any); fixed aperture marking in Punch Gerber Plugin
- in Punch Gerber and Extract plugins, clean up the aperture markings on Plugin exit

30.03.2022

- a minor fix in the Plotcanvas() class
- fixed the OR-tools path optimization (it required now a cast to int when creating the distances' matrix)
- in Distance Plugin made sure that the grid snapping restore is working properly (previously it always restored it to the ON state)
- the HUD display is no longer default active on new installations
- in Distance Plugin: added a new way to display the angle (angles between 181° and 359° are subtracted from 360°)
- in Distance Plugin: "snap to center" feature now works outside the Editors too (for Gerber object pads and Excellon object drill holes)
- some minor changes in Distance Plugin
- when changing workspace orientation fixed leaving behind the old frame on the canvas
- improved how the PDF printing works: now the Excellon files are always on top and from the rest of objects the first selected is on top, below the Excellons
- when doing PDF printing and no object is selected now the default action is to print everything that fits within the workspace
- when doing PDF printing, the cncjob objects are prioritized and placed on top of Gerber objects

29.03.2022

- added ability to change the mouse cursor color on the fly
- in Distance Plugin made sure that the 'big cursor' (when is used) is black (visible in most situations)
- in Distance Plugin, for the 2D graphic mode activated the utility line
- in Distance Plugin, fixed the `Snap to center` feature to work as intended

28.03.2022

- in Distance Plugin added the ability to use a big cursor; not finished: when the grid is off it is not displayed
- in Distance Plugin finished the 'big cursor' feature
- in the Film Plugin fixed an error which triggered an unwanted 'length' scaling

27.03.2022

- trying to make loading a project an easier task for the application
- fixed toggling the visibility for the CNCJob objects (was in reverse to the expected state)

24.03.2022

- refactoring names for some classes

22.03.2022

- fixed the GCode generation such that (for milling Geometries) the choice of using (or not) and end position is respected
- for `hpgl` code generation made sure that the first travel line is not marked as a cut line when using a toolchange event

21.03.2022

- optimized the UI for the CNCJob object
- when loading a Gcode file in the app now the application makes an attempt in adding information's such that the CncJob UI works correctly; if so information's are not found then assumptions are made. Only for GCode that is detected as made from an Excellon object
- updated Turkish language strings by Mehmet Kaya

20.03.2022

- added a last resort option to load old projects; the result is not guaranteed if the differences are too great
- updated the code in FlatCAMCNCJob object such that more attributes will be serialized in order for loading a project correctly when it has CNCJob objects
- another fix to make the app save correctly the CNCJob objects when saving a project
- in all text editors, when writing in the Find entry, all instances of typed chars will be selected
- in all text editors, pressing Enter on Find Entry will search for next instance of the searched text (no longer for focus out)

19.03.2022

- fixed some errors for when loading a saved project
- updated the app translation strings to match the ones in the sources, for all languages
- updated the Romanian language (native)
- updated the Spanish language (Google-translated)
- added a way to load older projects (made before recent changes)

17.03.2022

- fixed issues in the Scale feature of the Film Plugin
- in the Scale feature of the Film plugin added ability to scale by a length amount

16.03.2022

- fixed an exception encountered on saving projects due of getting a "circular error" on serializing the project objects

15.03.2022

- fixed an issue in Calculators Plugin that could crash the app, in the Electroplating section
- changed estimated time for new release

13.03.2022

- fixed issues when saving the project due of changed serializable attributes

12.03.2022

- added to the application an older contribution by another user: Columnar Layout for the Preferences Tab. It can be set in Preferences -> General -> GUI section
- installed an eventFilter for the Sliders in the Preferences tab such that only if a slider has focus the mouse wheel will change its value
- in Preferences made sure that the changes of the scrollbar no longer interfere with signalling changes
- minor UI changes (added some parenting so some pop-up messages will be centered in the UI as opposed to the screen)

11.03.2022

- added a new feature: now in the context menu (and main menu -> Edit) there is a new command that allow to move a selection of objects at specified numeric coordinates (either absolute or relative to current position) 
- made sure that on canvas context menu pop-up, some actions are disabled if there is no object selected
- made sure that on canvas context menu pop-up, if 'Properties' action is clicked and there is no object selected then the Properties tab is selected
- in the Grid Toolbar, the Grid Y entry is now by default hidden and shown only when there is a need (grid gap link is disabled) in order to maximize the status bar space availability
- in Distance Plugin made sure that the 0.0 value for the angle is displayed
- some UI changes (startup splash image changed)
- changed the look for the Location/Jump To widgets; it is now like a cross being more relevant visually
- the Numeric Move now can take only a parameter instead of a tuple but only if the comma is added, the not added parameter will be assumed 0.0. Therefore ",10" will move to (0, 10).
- added a new splash image for the case of using a dark theme

10.03.2022

- fixed an issue where using the 'G' shortcut key in Editors will not toggle the grid snap
- fixed an issue in the Excellon Editor where selecting the drills did not highlight them but instead made them invisible (although the selection still worked)
- fixed an issue in the Gerber Editor where selecting one shape will auto-select all the shapes made with the same aperture
- fixed multiple issues in the App objects related to wrong usage of self.obj_options attribute instead of self.app.options attribute
- remade the Film Plugin such that the `skew` feature is now done in length units as opposed with the previous usage of angles
- refactored some big methods from the Film Plugin
- `skew` feature in Film Plugin now have multiple options. `skew` can be done by length, by angle and by ratio (factor)
- fixed an issue when importing GCode which made the loaded GCode object un-plotable
- fixed Film Plugin not using the set value in Preferences for the `skew type`
- more work in Film Plugin, in the `ratio` skew feature
- fixed a typo in the Distance Plugin which made that the Delta Y value was always the same as Delta X value
- made sure that the app starts with toolbar's locked
- fixed an issue when creating a new project (due of recent massive changes)

9.03.2022

- added a few new GUI custom widgets
- removed the status bar Tcl Shell toggleable QLabel. Now the Tcl Shell is toggled by clicking the Message in the Status bar

24.02.2022

- some changes to the Preferences

23.02.2022

- in Gerber Editor: in Eraser tool now the intersected shapes are stored properly such that when exporting the edited Gerber, it is done properly

22.02.2022

- in Gerber Editor: if the aperture size is 0 then an aperture code 0 is added (if it does not exist yet)
- in Gerber Editor: when the Eraser tool is used, after it is applied if some geometries elements are empty, they are now removed
- some fixes in remembering the last save location and last open location
- changes in the App options are now saved to the App defaults on app exit
- in Gerber Editor fixed adding a new aperture with code 0
- in Gerber Editor when the aperture size is 0.0 then automatically add a new aperture with code 0
- in Gerber Editor -> Eraser tool: when deleting all geometry in an aperture then the aperture is deleted too

20.02.2022

- fixed a minor error in the Gerber Editor which created a graphic glitch on shape selection
- making the above fix accept few possibilities
- updated the Turkish translation (by (Mehmet Kaya)
- fixed an issue where the selected application translation language is not remembered as was selected and defaulting to English language
- refactored code in Preferences section
- added the wheels for a working pair of GDAL-Rasterio modules (python 3.10)
- fixed an issue when trying to print an exception message
- fixed an issue due of recent changes that made the application think that is run always for the first time; fixed not applying the selected language translation
- some more code is refactored in the Preferences
- the axis will now be drawn on the canvas as they were left in the previous run (just like the HUD and the Workspace)
- for processors with less than 4 cores now the default number of workers is 1 (changed from 2)
- some graphical settings go now directly to the defaults dictionary and will not pass through the `options` app dictionary

19.02.2022

- updated the language strings files to the latest changes

18.02.2022

- completed the refactoring of object `options` property to `obj_options` in preprocessors too
- made sure that the CNCJob UI building no longer have issues with table items added from an already existing table
- fixed CNCJob UI showing all tools in the Tools Table even when only some tools were selected for processing
- fixed the `drillcncjob` Tcl Command to work in the case of drilling a selection of tools instead of all, with toolchange inactive
- in Excellon Object UI, Advanced Mode, added a Tools Table context menu allowing to copy the tool diameters in the selected rows to clipboard
- the application now uses only the default values from the app.options dict, the app.defaults dict holds the definitive default values
- fixed some outstanding issues from the PyQt6 port
- PEP8 fixes
- minor fixes
- updated the saving of Preferences to update the self.options too: the `Apply` action will update the self.options but the `Save` action will save the updated preferences to the file on disk

16.02.2022

- refactoring all the references to object `options` property to `obj_options` to make a difference with the application `options` property

15.02.2022

- fixed an issue with the moving shape when creating an exclusion area
- added ability to copy the coordinates of the exclusion area with the context menu actions in the Exclusion areas table found in the Milling/Drilling plugins
- in Tcl command `add_polygon` added ability to use `-p_coords` optional parameter where the value of this argument should be a list of tuple coordinates
- minor fixes in the Milling Plugin
- if `Start Z` parameter is set in Edit -> Preferences now there is no extra lift GCode because it is replaced by the startz GCode (except for `laser` preprocessors)
- if `Start Z` parameter is set in Edit -> Preferences now there is no extra lift GCode because it is replaced by the startz GCode (except for `laser` preprocessors) - for Drilling Plugin too
- minor debug log message change 

13.02.2022

- fixed issue when doing a custom origin set

11.02.2022

- a new feature that when selecting an object in the Project Tab, that object is redrawn over the other objects, in top position. Feature controlled from Preferences -> Gerber -> General.
- made sure that the new feature of redrawing Gerber on top is not activated when that Gerber object is disabled
- added ability to turn off the outline plotting for Gerber objects (will speed up the plotting but the plotting visual quality will degrade); controlled from Preferences -> Gerber -> General
- plotting visuals in 3d graphic mode will no longer create some unnecessary lists
- made sure that the `redraw_on_top_on_project_click` works properly and only on mouse click release in the Project list
- made sure that the `redraw_on_top_on_project_click` works only on single object selections
- `redraw_on_top_on_project_click` works only for left mouse clicks on the Project objects list

10.02.2022

- added a new preprocessor: `default_laser`
- in the app added a new tool type: "L" (laser) which when selected will autoselect the `default_laser` preprocessor in Milling Plugin

7.02.2022

- fixed some issues when exporting Excellon objects

2.02.2022

- fixed some issue regarding Milling Plugin, importing DXF file as Geometry

1.02.2022

- fixed the `cncjob` Tcl command: forgot to pop the arguments that are not recognized by the non-multigeo handler
- fixed the multi-color feature in the Gerber object UI
- fixed the marking of apertures in Gerber object UI and in Extract plugin
- minor changes in `cncjob` Tcl command
- updated the `offset` and `scale` Tcl commands to work on a selection of objects
- minor changed in camlib.py regarding the self.check_zcut() method setting the self.z_cut to 'fail'
- upgraded the `mirror` Tcl command to work on a selection of objects and also fixed a plot issue
- upgraded the `skew` Tcl command to work on a selection of objects and added the ability to skew not only in degrees but also by distances
- fixed some issues in `panelize` and `cncjob` Tcl commands
- reversed the mirroring axis in the `mirror` Tcl command to be consistent with the action in GUI
- solved some more exceptions in the `cncjob` Tcl command

31.02.2022

- fixed all the `laser` preprocessors to work correctly and the resulting GCode to be plotted
- made sure that when drilling and milling with a `laser` preprocessor the first move is not done with the laser on
- fixed a minor UI glitch in the CNCJob UI
- made sure that the `drillcncjob` and `cncjob` Tcl commands will use the `-las_power` parameter to set the laser power when using a preprocessor with `laser` in its name
- now the `verbose log` parameter from the Preferences can take 3 values (0, 1, 2). Value = 0 means that the logging is disabled (mostly), value = 1 means that the logging is only in console and value = 3 means that the logging is now displayed in the Tcl box
- most of the logging (except in the Editors) is now done by the AppLogging class
- made sure that for laser preprocessors, the laser is always turned off completely at the end of a job
- added a new script example
- improved the quit application process; now the `quit_app` Tcl command works properly
- another fix for Shapely 2.0 deprecation warning
- yet another fix for Shapely 2.0 deprecation warnings
- updated the `join_geometry` and `join_excellon` Tcl commands format to follow the format of other commands
- updated the `isolate`, `cutout` and `geocutout` Tcl commands and now they yield multigeo Geometry objects
- fixed an issue in `panelize` Tcl command where some parameters (`spacing_columns` and `spacing_rows` where forced into integers where correct was to keep them as floats)
- fixed a performance issue in `panelize` Tcl command: when panelizing a Geometry object the total solid_geometry was calculated for each tool, but it was enough to be calculated once
- yet another fix for a Shapely 2.0 deprecation warning
- minor change in the way the app quits

30.01.2022

- refactored the Isolation Plugin class
- some more work on the Isolation plugin
- fixed more issues in the `ncc` and `paint` Tcl commands (missing `seg_x` and `seg_y` keys)
- fixed not adding the feedrate code in `drillcncjob` Tcl command
- fixed crash when trying to do a `select all` and there are app Scripts present
- updated the `drillcncjob` Tcl command to make a script exit in case of an error
- in `drillcncjob` Tcl command added the usage of `RTree` path optimization and made it the default choice
- finished the UI for adding a new parameter that is used in conjunction with the `laser` preprocessors: `laser minimum power` parameter which should help in doing a full power up/down cycle (should prolong the lifetime for the laser diode)
- fixed some UI issues for the Milling and Drilling plugins when using a `laser` preprocessor
- finished implementing the new feature that add a new parameter `laser minimum power` for the Drillin, Milling Plugins and for the `cncjob` and `drillcncjob` Tcl commands: 
- modified all the preprocessors that are for `laser` to use the new parameter: `laser minimum power`
- fixed the `drillcncjob` Tcl command to use the parameters: `endz` and `endxy`

29.01.2022

- upgraded/fixed the Panelize Tcl command
- added a new preprocessor for Marlin that has movements on the Z axis named `Marlin_laser_Z`
- cleaned up Marlin preprocessors
- in the `panelize`, `cutout` and `geocutout` Tcl commands updated the error strings and made sure that if an error occur then a potential script execution in chain is aborted
- updated the `new` Tcl command: now it has an argument `-keep_scripts` that when True, will keep the loaded scripts in the new project
- fixed some issues in the `ncc` Tcl command
- fixed some issues in the `paint` Tcl command
- minor description update for `export_gcode` Tcl command

28.01.2022

- in Tcl command `geocutout` fixed an error due of a no longer used parameter
- in camlib.CNCjob.gcode_parse() fixed an assumption that a certain object is present: tool_data["tools_drill_toolchange"]
- fixed an error in Drilling Plugin when selecting only a few tools and not all for drilling
- fixed an error when building the UI for a CNCJob object created from drilling an Excellon object with a limited selection of tools
- fixed an KeyError exception in the GCode Editor regarding the key: 'offset' not existing
- updated the Tcl command `drillcncjob` to store the tool gcode and the parsed tool gcode in each tool data structure
- updated the Tcl command `drillcncjob` description and examples

27.01.2022

- made sure that for the Tcl command `cncjob` when using the `dpp` parameter (multi depth) the value is always positive, as expected
- the `cutout` Tcl command creates a non-multigeo Geometry object

26.01.2022

- fixed the Cutout plugin not working with Geometry objects that are made out of a LineString or LinearRing geometric elements
- fixed the Cutout plugin for the rectangular cutout: the name of the resulting object is now correct if there is no extension in the source file
- modified the bounding box utility feature in the Gerber Object UI to work with margin value of 0.0 and rounded corners
- in Cutout plugin added the ability to create Manual Geometries with negative tool diameters
- fix the Transform plugin, buffer functionality for Geometry objects such that the buffer with negative values will work by crating a buffer toward the interior of that geometry
- minor changes
- modified the bounding box utility feature in the Gerber Object UI to work with negative margin values
- fixed some errors in the `buffer` and `isolate` Tcl commands
- updated the camlib.Geometry.buffer() method

25.01.2022

- minor changes
- fixed the Follow plugin not marking correctly the resulting Geometry objet as a `multigeo`
- fixed the Cutout plugin not working with Geometry objects that are made out of a MultiLineString
- fixed the Milling Plugin who changed the tools information "tools_mill_multidepth" when changing from Basic to Advanced mode to the application default value but that defeated the change made by the Cutout plugin
- fixed a Shapely 2.0 deprecation warning in the Report plugin

24.01.2022

- fixed a syntax error in a call of the log.error()
- trying to solve a 'circular reference' error when saving a project in Arch Linux

22.01.2022

- improved the running of scripts when loading from GUI a script as a FlatCAM script
- fixed the `GRBL_laser` preprocessor such that it does not have a Z move at the end of the job due of the fact that this preprocessor has moves only in the X-Y plane
- in Milling Plugin made sure the preprocessors that have the `laser_z` in the name (case insensitive) have the ability to change the Z height at the end of the job (through End_Z parameter)
- in Milling Plugin there was issue that did not showed the Z focus parameter for the `GRBL_laser_Z` preprocessor
- updated the `GRBL_laser_Z` preprocessor to show more complete parameter information's in the header

21.01.2022

- added a new Tcl command: `buffer` which will buffer the geometry of an object or will scale individually each geometry sub element
- fixed the buffer() method for the Excellon objects (the resulting tool diameters were calculated less than the what was expected)
- fixed the description for the new Tcl command `buffer`
- fixed more issues with the `cncjob` Tcl command
- made sure that some autocomplete keywords are correctly processed by the application; needs the delete of the preferences files

19.01.2022

- updated the header of the preprocessors with 'laser' to show essential information's like some of them do not move on the Z axis
- make sure that the laser postprocessor that do move on the Z axis (like 'GRBL_laser_Z') accept negative values for the Z focus
- fixed issue in highlighter such that the keywords that have an underscore included are highlighted
- rearranged the default keywords
- fixed the generatecncjob() method default parameters to reflect new data structure names
- in Geometry object the default self.options dictionary is updated with keys that reflect new data structure
- added comments in the `default` preprocessors which will help other people create their own preprocessors
- remade the Image Import plugin in an attempt to fix issues with the latest packages of Gdal and Rasterio but it looks that it is a conflict between the Rasterio 1.2.10, Gdal 3.4.1 and Shapely 1.8.0
- made a hack so if the Image Import plugin can't be loaded due of dependencies errors the app will not crash but this plugin will no longer be available
- fixed some issues due of the above hack

18.01.2022

- in the 'cutout' Tcl command made sure that when an error pop-up then it returns with a "fail" string
- made sure when running scripts from the interface that if a command will generate an error then the script is aborted
- added a new Tcl command named 'list_pp' which lists the available preprocessor names

17.01.2022

- fixed issues in the 'millslots' and 'milldrills' Tcl commands for the case when some parameters are not used
- made sure that in Tcl commands deleting argument keys will not raise an error
- fixed the 'isolate' Tcl command: updated to the new tools data structure elements
- fixing an issue with not building the UI for the generated objects when running a script and then trying to enable/disable the plots
- attempting to add a feature in the text editors to autoselect all words in a document that are the same as the one that was clicked
- all the colors set in Preferences now have to be in the 8digits format RGBA (including the alpha channel as a suffix) - it is required to delete the current preferences files
- fixed an issue with path conversion when using Windows-style of paths in the 'set_path' Tcl command
- modified the 'new' Tcl command to be silent and to show no popup message

15.01.2022

- All FCColorEntry GUI element instances have now a set icon for the title (and taskbar)
- some changes to the FCMessageBox so the dark theme works ok on it
- fixed the Tcl Command 'set_origin' to work for a specified location
- updated the Tcl Command 'set_origin' to give the option to set the origin for a specified object
- fixed the 'cncjob' Tcl Command to work in the new configuration of the data structures
- fixed the 'save_project' Tcl Command - missing 'f_handlers' in the path

14.01.2022

- made sure that new message boxes are always centered on the app UI by setting correctly the parent

13.01.2022

- modified the FCMessageBox to have a border and a single color inside the box
- made sure that the FCMessageBox taskbar icons and message are the correct ones
- applied the FCMessageBox GUI elements everywhere; there is still a hack in appTranslation file where I needed to re-implement the class due of some circular imports errors

12.01.2022

- subclassed the QMessageBox and created a new type of MessageBox that is frameless; started to use it throughout the app

11.01.2022

- added an initial implementation of a dark theme using the pyqtqdarktheme (performance is not great and there are some artifacts); the theme is activated once the gray icons are checked in the Preferences
- started to mod the qdarktheme to fit the application

10.01.2022

- added a new feature in Preferences -> Gerber: a Layer Color Manager where the user can set and edit predefined colors for the Gerber objects: loading order matter
- fixed and error in the Isolation Plugin when using the pad multi-isolations and area isolation simultaneously

9.01.2022

- some UI changes in the Preferences: the clicked tab now have a distinctive color (green) and some strings are optimized
- added in CNCJob preferences new preferences that allow compensation for the CNC bed skew/tilt (axes not being square one to another)
- all pre-processors are altered such they can adjust the gcode to use the compensation values set in Preferences -> CNC-Job section
- fixed a Gerber parser issue shown by Gerber files generated by Autotrax DEX ECAD software: if the last operation before a region was a flash D03 then at the beginning of the region a flash was added with the previous aperture

8.01.2021

- in Isolation Plugin fixed the issue with clicking the header not toggling all tools rows
- in Isolation Plugin fixed an issue in the reset_fields() method

6.01.2022

- modified the 'laser-lines' method in the Paint Plugin such that the rounded part of a painted trace is painted too in the case that the trace width is much higher than the clearing tool diameter
- some changes in the Excellon object suggessted by an user: Karl-Heinz
- added a donation link (some people wanted it there so...)

5.01.2022

- in Paint Plugin fixed the method 'laser-lines' for the case when the painted Gerber file has square shape apertures

24.12.2021

- in Milling Plugin made sure that the UI is updated correctly when changing the type of target object depending on the Basic/Advanced mode

23.12.2021

- in Milling Plugin working on milling Excellon holes by painting them from the center
- removed display of some logs in Tcl Shell (when in Verbose mode) when parsing the Gerber files
- fixed a double application of milling offset when having geometries with multiple tools
- finished the mill-excellon feature in the Milling Tool; milling will be done progressively from the hole center toward the edge

16.12.2021

- in Milling Plugin fixed the toolchange_xy and end_xy parameters not being used

9.12.2021

- small optimization when deleting all objects (creating a new project)

8.12.2021

- fixed some more Shapely 2.0 deprecation warnings
- fixed a message bug in the AppMain

29.11.2021

- some issues fixed that were left behind from porting to PyQt6
- trying to solve differently RuntimeError exceptions that randomly popup due of wrapped C++ objects being deleted by attempting to create a new UI for the objects

27.11.2021

- fixed a bug in the Gerber Editor in the Move command

26.11.2021
- in Follow Plugin made sure that the notebook is disabled until the user finish (or cancel) the operation when using the area type of 'follow'

23.11.2021

- added a few protections against RuntimeError exceptions that randomly popup due of wrapped C++ objects being deleted

22.11.2021

- in AppTextEditor made some changes (added some placeholders and a message popup when reaching the end of document)
- when viewing GCode the Find field can now keep the focus
- fixed the Travelling Salesman algorithm such that it does not add duplicated points that are next to each other
- when building the UI for the CNCJob first clear the CNCJob Tools Table

21.11.2021

- updated the language strings
- updated the Italian (Massimiliano Golfetto) and Turkish (Mehmet Kaya) translations
- fixed a bug in Panelize Plugin where the Geometry objects could not be panelized
- fixed some more Shapely 2.0 deprecation warnings in the Panelize Plugin

20.11.2021

- fixed another issue when using Python 3.10, in GUI elements
- fixed the Rtree optimization when drilling with no toolchange such that all drill points are used
- fixed the CNCJob object Tools Table when drilling with no toolchange: now only one tool is displayed
- made sure that if the OR-TOOLS package is not installed then the options in the Preferences for the related optimizations are disabled

19.11.2021

- solved some Shapely 2.0 deprecation warnings in the Panelize Plugin

17.11.2021

- fixed an issue when using Python 3.10, in GUI elements
- added fix such that the application will work without having the ortools package installed: TSA and RTree algorithms will be automatically used in that case
- added Rtree optimization for Drilling Plugin

10.11.2021

- fixed the issue with toggling visibility for Excellon objects
- fixed some issue when using Python 3.10

7.11.2021

- in Cutout Plugin made sure that mouse bytes feature will not autoselect the Excellon object that is created

6.11.2021

- in Isolation Plugin there is no longer auto-switch to Properties Tab
- when editing Geometry objects with multiple geometry fixed the issue with the edited geometry always being the one in the first tool
- in Geometry Property Tab, clicking the tools table header will toggle the selection for all tools in the table

1.11.2021

- Cutout Plugin - improved the functionality in Manual mode: depending on the state of the Shape parameter different types of cutout manual geometry is generated
- fixed a bug in the Editors when trying to launch the Editor without an object selected
- Markers Plugin - fixed issue when adding existing markers to an external Geometry object

31.10.2021

- in Markers Plugin made sure that whenever new markers are added, the previous ones are first cleared so there is no accumulative action
- in Markers Plugin added utility geometry for the manual mode
- refactored the is_legacy object to use_3d_engine
- made sure that there is no longer a double action when toggling the object visibility ('plot' attribute)
- fixed an exception that was triggered when toggling units inside the Editors
- working on Cutout Plugin - refactoring code
- fixed issue in Cutout Plugin: there was an offset from the object center when using automatic gaps, both on X and Y axis
- fixed issue in Cutout Plugin: when the margin parameter was bigger than the gaps size (automatic gaps) the gaps are not created

30.10.2021

- small changes in the VisPyVisuals that should improve a bit the performance
- in Isolation Plugin: if using a V-shape tool, restored the ability to change the tool diameter as a function of tip diameter, tip angle and the depth of cut

28.10.2021

- cleaned the geometry generated by flattening by removing the empty geometry; fixed issue #539 on jpcgt's bitbucket account
- fixed error that did not allowed printing from the TextEditor
- added an error message when the File -> Print to PDF functionality is not working due of missing a root element in the SVG step
- fixed printing a CNCJob object with File -> Print(PDF) functionality by adding a root element if both travels and cuts are present
- fixed some more Shapely 2.0 deprecation warnings
- added more of the contributors in the About page

27.10.2021

- fixed the offsetting feature in the Isolation Plugin such that the interiors of the isolated polygons are offset correctly
- applied changes for offsetting to other methods

25.10.2021

- updated the PlotCanvas3d

24.10.2021

- added an example showing performance degradation in VisPy 0.9.1

18.10.2021

- fixing a possible error in Subtract Plugin: trying to remove the aperture with id: 0 will create an exception if this aperture does not exist

15.10.2021

- added an ugly form of extra pad passes functionality in Isolation Plugin - does not take into consideration the milling direction
- fixed a Shapely deprecation warning in export_dxf() in GeometryObject class
- in Geo Editor fixed another Shapely deprecation warning in flatten() method

14.10.2021

- updated the translation strings

13.10.2021

- in Distance Plugin - modified the UI
- in Distance Plugin - implemented a new feature: multi segment distance measurement
- in Distance Plugin - the Dx, Dy, Angle and Distance values are updated real time (for non multi segment measurement)
- in Distance Plugin - Distance value is now updated real time regardless of the Multi Point setting
- in Distance Minimum Plugin - upgraded the UI
- added a method to do cleanup after a Plugin close
- in Distance Plugin added the cleanup after the Plugin is closed from the Tab close button
- renamed the Distance Minimum Plugin to Object Distance; started to add new functionality on this Plugin: to calculate the distance between the bounding box centers too
- pywin32 module is no longer needed when running in Windows
- updated the Romanian translation
- Google-translated the Spanish translation
- updated the strings in all translation files
- if the user is not admin then the application will not restart from within but the changes will be applied at the next app run
- updated the strings in all translation files
- finished the new feature in the Object Distance Plugin (calculate the distance between the bounding box centers) and code refactoring

12.10.2021

- fixed some issues in NCC Plugin: the Plugin could not clear a simple geometry and also some failures were not treated correctly

11.10.2021

- in Distance Plugin, after setting the start point of the measurement, moving the mouse will update the end point position
- in Markers Plugin: fixed the offset from center functionality
- in Markers Plugin: changed the name of the resulting object to reflect this plugin name
- fixed an issue with toggling the visibility of objects

10.10.2021

- in Markers Plugin replaced the Margin parameters with the Offset parameters that now can be referenced either to the edge of the bounding box (effectively replacing the old Margin parameter) or to the center of the bounding box
- Distance Plugin Ui was upgraded
- updated the language strings
- in 2Sided Plugin: adding cumulative alignment points using the Ctrl+Shift+LMB now detects if it is a new start in adding alignment drill points
- in 2Sided Plugin: duplicated set of coordinates in Alignment Drills are removed
- in 2Sided Plugin: Shift+LMB will add automatically the clicked coordinates in the Alignment Drills location
- update language strings
- updated the Romanian translation files
- updated the Turkish translation files by Mehmet Kaya

9.10.2021

- fixed some recently introduced issues for the Milling and Drilling Plugins; those plugins did not had the pluginName defined in the Plugin UI
- in the canvas context menu added the menu for color changing
- when doing color changing now only Gerber and Excellon objects can have the colors changed
- fixed an issue within the version checking (inherited from FlatCAM)
- renamed the Corner Markers Plugin in Markers Plugin
- added a new feature in Markers Plugin: now the Margin parameter can be set for both axis: X and Y or both

8.10.2021

- the Plugins object combo boxes are no longer updated on object selection on Project tab generally but only for the Plugin that is current, if any
- in Corner Markers Plugin I added a new feature, ability to insert existing markers into external objects. Finished the work for external Gerbers and work in progress for the external Geometry
- the Editors require an Object UI build before launching; I've added the required code so the shortcut keys work even if the Properties tab is not current
- in Corner Markers Plugin - finished the feature of adding markers geometry to an external Geometry object
- changing the Excellon Object color from Project Tab context menu is now persistent

7.10.2021

- updated the Turkish translation files (by Mehmet Kaya)
- added a new color in the Object Colors ("indigo")
- now the object color transparency is preserved if a new color is chosen
- the Properties tab UI is now populated with the object UI only when the Properties tab is selected and not on object selection like it was done previously
- make sure that if the Properties Tab is selected then the Object UI is updated (built) on object selection on canvas
- code cleanup
- in "Punch Gerber" and "Extract" Plugins - make sure that the aperture markings are deleted on Reset Tool click and upon finishing the Plugin main function
- in Punch Gerber Plugin the object is no longer populated automatically with the generated object
- in Corner Markers Plugin fixed the position of "drills in locations" and for Checking Excellon; the source object is no longer auto-updated to the latest one since we have to always work on the original object

5.10.2021

- Corners Plugin made sure that Drill in Locations and Check in Locations functionality works for manual mode
- Check Rules Plugin - upgraded the UI to the new look and grouped better the rules
- updated the language strings
- upgraded the Preferences UI section for Check Rules Plugin

4.10.2021

- fixed a typo in the Object UI
- in 2Sided Plugin advanced mode fixed the bounds calculation: if no object is selected on canvas then the object selected in Source Object is used
- in 2Sided Plugin added a new typ of alignment drills: manual. This mode will no longer add pairs of drill holes mirrored against reference but only add in place drill holes
- in 2Sided Plugin clicking LMB and also pressing CTRL+Shift will add the click coordinates to the Drill Alignment Coordinates
- added support for all Plugins to handle the LMB click release event without connect/reconnect of the mouse events
- code refactoring in appMain file
- in 2Sided Plugin, deleting the last drill alignment coordinates will update the clipboard values too
- added the circle approximation parameter for Excellon geometry in Preferences
- updated the language strings

3.10.2021

- updated the language strings to the latest version
- some changes in the Objects UI
- removed some VisPy patches that now are incorporated in the v 0.9 of VisPy; bumped the VisPy requirement to 0.9.0
- in Cutout Plugin solved a Shapely 2.0 deprecation warning

2.10.2021

- in Preferences, more Plugins preferences UI are upgraded to the new look
- In Paint Plugin fixed the Area select mode to work with Geometry object created by the Geometry Editor
- in Paint Plugin some changes in the way the source object is autoloaded
- in Paint, NCC and Cutout Plugins when using a mode that require to be terminated (by mouse RMB or ESC key) the notebook UI element is disabled until this is done
- for Transform and SolderPaste Plugins upgraded the UI
- in SolderPaste Plugin now the paste is dispensed only on the pads/Gerber flashes
- fixed Find Optimal Plugin: there was an error triggered after finding the minimum distance
- upgraded the Find Optimal Plugin UI
- upgraded Optimal and Calibration Plugins Preferences UI

1.10.2021

- clicking the splash screen will close it; also if an error is triggered, the error message will pop over the splash screen
- the Aperture Table in the Gerber Editor is no longer extended to show all apertures at once
- in Preferences: Excellon, Geometry and CNCJob tabs, updated the UI to the new design

29.09.2021

- more UI changes in the Preferences to align them to the new app look
- finished the UI update in Preferences -> Processing

28.09.2021

- changed the circle resolution back to the default of 16 since this value is good for fast rendering of Gerber files
- added a shortcut to select all apertures in the Gerber Editor (Ctrl+A)
- other minor fixes
- added a shortcut to select all tools in the Excellon Editor (Ctrl+A)
- in Cutout Plugin simplified the UI
- Gerber Extra Buffering is now OFf by default. For those cases where the Gerber file is not rendered correctly it will need to be activated. It has a performance penalty so it will no longer be enabled by default for everybody.
- Gerber UI in Preferences is now updated

27.09.2021

- in Gerber Object trying to solve the issue with too little details on plotting geometry with inch units
- working to differentiate between temporary units change and permanent units change
- changed the message when encountering a critical error - it now looks better

26.09.2021

- besides reporting the error into the log file now any application crash is reported also in a MessageBox after which the application will close
- solved a Shapely 2.0 deprecation warning
- more Qt6 port bugs fixed
- in Autolevelling Plugin modified the UI; work in progress - the frame disable is not working
- in Excellon Editor and Geometry Editor solved some Shapely 2.0 deprecation warnings
- in Excellon Editor made sure that the tool diameter is always updated before adding a new drill (or drill array)
- changing units is not possible while in Editors
- changing units outside the Preferences will make no permanent changes
- deleting all shapes from the shapes collection (OpenGl graphics) will reset also the index in the collection data dict

25.09.2021

- solved more Shapely 2.0 deprecation warnings 
- in Isolation Plugin and NCC Plugin changed the UI and the Preferences for those Plugins
- in Gerber Editor, the Region Tool now will draw only for the '0' aperture therefore the utility geometry will be a very thin line
- multiple UI changes in Preferences
- UI changes in the Paint Plugin

24.09.2021

- in Extract Plugin some minor UI changes
- in Film Plugin added new parameters and improvements: now the negative film can have a box that is convex and it is no longer limited to square shapes. Also, if the box object has only one geometric element (an outline) then that one will be the final shape of the negative
- in Gerber Editor fixed not being able to delete an aperture
- in Gerber Editor fixed the edge case where the user selects apertures in the Tools Table and then uses the Poligonize Tool and not by selecting shapes on canvas
- in Cutout Plugin remade the UI and fixed different issues
- in Gerber Editor fixed the issue where some shapes that belong to a deleted aperture were still stored inside the selection storage after this deletion

21.09.2021

- fixed an error due of missing attribute of PlotCanvasLegacy when using Legacy2D graphic engine
- solving deprecation warnings issued by Shapely
- made sure that the Gerber Object geometry is always flattened
- in Legacy2D graphic engine fixed setting the Opacity functionality
- fixed a recent issue where Gerber files made out of single polygons were not processed
- more Shapely deprecations fixes
- fixed some leftovers from Qt6 port
- some code optimizations in the Gerber Editor
- Corners Plugin now generate Gerber objects with the solid geometry flattened
- fixed issue where Gerber objects created from importing SVG geometries could not be edited

19.09.2021

- fixed Extract Plugin not extracting soldermask for aperture macros
- fixed INFO in Gerber Object UI to show correctly the type of geometric element in Follow geometry
- in INFO functionality for Gerber Object made the difference if the Follow geometry is not a Point or a LineString
- In Plugins: Panelize, Cutout, Extract, Copper Thieving disabled the autoload of the last created APP object
- In Plugins: Calculators, Copper Thieving, Corners, Extract, Fiducials and Film activated the harmonizing first column width in the Plugin UI
- DblSided Plugin - remade the UI
- fixed some issues in AppTextEditor due of Qt6 conversion
- In Extract Plugin fixed issue with not extracting soldermask for pads made with "P" type Gerber aperture
- in the method that exports the Gerber fixed issue with not exporting the geometry associated with the apertures of type "P"
- pattern highlighting: added the Dx and %LPC%, %LPD% expressions

18.09.2021

- some code reformatting in Extract Plugin
- fixed the Geometry object merge functionality
- fixed a message when renaming an object

15.09.2021

- updated the Chinese translation by 俊霄 余

12.09.2021

- some UI changes in the Milling and Drilling Plugins
- working on the Excellon side from Milling Plugin
- modified the UI in Excellon Object Properties UI in the milling section

10.09.2021

- minor changes in Film Plugin
- fixed the GLay (in GUI elements) method that synchronize the column width for multiple grid layouts when the item on column is spanning multiple columns
- updated the Turkish translation by Mehmet Kaya

9.09.2021

- in Fiducials Plugin updated the GUI to the new style
- Fiducials Plugin: replaced a Radio button with a Combobox2 and optimized the UI
- The Combobox2 GUI element no longer issue an exception if it is tried to set a string value, it will set automatically the index 0
- some changes in the Preferences UI for Film and Fiducial Plugins
- in Milling Plugin added a property that allows to segment the resulting GCode therefore allowing autolevelling
- in Levelling Plugin added a check that allow levelling only for CNC Job objects resulted from Geometry
- some minor changes
- updated the language strings
- on Coppper Thieving Plugin some UI updates
- updated the GCGridLayout GUi element to automatically stretch the first column but offered also configuration; updated the use throughout the app
- in Copper Thieving Plugin more UI changes
- in GUI Elements the GLay has now a class method that allow adjusting column size in multiple grid layouts to the highest on that column; still work to do to take care of the situation when widgets are spanning multiple cells
- in Fiducials Plugin added the support for ESCAPE key from manual mode and also exit by right clicking
- in Fiducials Plugin addressed the situation when no object is selected but there are available
- in Fiducials Plugin when adding manual fiducials now panning is allowed without cancelling the process of adding
- in Corners Plugin implemented the manual adding of markers
- in Corners Plugin added the support for ESCAPE key from manual mode and also exit by right clicking
- added updated Italian translation by Massimiliano Golfetto
- added updated Romanian translation
- updated the language strings

8.09.2021

- in Calculator Plugin, in Units Calculator added more units conversions
- changes to the strings in the Calculator Plugin
- in Film Plugin updated the GUI
- in Film Plugin some changes in the data storage; require to delete de current preferences files
- Film Plugin: now the Skew and Scale transformation have their own reference
- updated the language strings to the current strings in the app
- in Minimum Distance Plugin updated the GUI
- in Distance Plugin updated the GUI

6.09.2021

- remade the Excellon Object Properties UI
- remade the Geometry Object Properties UI
- remade the CNC Job Object Properties UI
- in Etch Compensation Plugin updated the GUI
- small UI updates in other Plugins
- in Align Plugin treated a possible exception DivideByZero
- in Isolation Plugin updated the GUI

5.09.2021

- in Punch Gerber Plugin remade the GUI
- some minor fixes in some Plugins
- in QRCode Plugin remade the GUI
- added some new properties to the FCCheckBox GUI element
- in Corners Plugin remade the GUI
- in Corners Markers Plugin added the beginning of the manual marker placement
- in Alignment Plugin updated the GUI
- remade the Gerber Object Properties UI
- fixed a small typo when creating a new Gerber empty App Object
- in Copper Thieving Plugin updated the GUI
- in Extract Drills Plugin updated the GUI
- in Paint Plugin updated the GUI
- in NCC Plugin updated the GUI
- in Subtraction Plugin updated the GUI
- in Follow Plugin updated the GUI
- in Invert Plugin updated the GUI

4.09.2021

- in Calculators Plugin added a new calculator to calculate the required chemical weights for tinning solutions
- in Calculators Plugin remade the GUI
- some minor GUI modifications for a series of App Plugins
- in Calculators Plugin, added sodium hypophosphite optional parameter calculation
- in Cutout Plugin remade the GUI
- in 2-sided Plugin remade the GUI
- in Drilling Plugin remade the GUI
- in Milling Plugin remade the GUI
- in Panelize Plugin remade the GUI

3.09.2021

- trying to get the right size of the HUD rectangle

2.09.2021

- in Geometry Editor, deleting a shape from the Table context menu will update now the plot
- when a Plugin is no longer used disconnect a certain signal that control the selection in the App Object Target combobox since it is not needed and it takes cpu cycles

1.09.2021

- fixed the issue in Milling Plugin that did not automatically set the object combo to the previously selected object
- some GUI changes in Milling Plugin
- fixed the multiple selection in the Project Tab collection list; now only a single selection will trigger the signal 'proj_selection_changed'
- when Milling Plugin is opened, selecting an Excelon object will set the GUI to Excellon Target

31.08.2021

- made sure that the location labels in the InfoBar have only the decimals required when the Grid snapping is ON
- fixed the bug where the HUD have not used the set font size from Preferences

30.08.2021

- fixed the reconnection of the mouse events when canceling setting the origin
- disabled the selection of a Graphic Engine until matplotlib will have support for PyQt6 framework
- removed patch to fix scaling on HIDPI displays since now the fix is implemented in VisPy v0.8.1
- modified a patch for VisPy Infinite Line Visual that set the line width, in order to make it compliant with newer versions of OpenGL
- add HUD rectangle auto size for 3D Graphic Engine
- add HUD rectangle auto size for 2D Graphic Engine
- the Y axis labels are now rotated at 45 degrees allowing better space management
- changed the HUD color, with Light theme, to gray
- in Gerber Editor fixed bug with the row selection that no longer selected the visuals on canvas

29.08.2021

- fixed a reported issue with the manual cutout in Cutout Plugin where adding more gaps will crash the app due of the manual geometry being a MultiPolygon and not a Polygon as it was assumed
- fixed a reported bug in convert 2 geometry where a MultiLineString' object does not support item assignment“ error was issued
- updated the language strings and the Simplified Chinese language translation

28.08.2021

- removed the setting for HDPI from Preferences and QSettings: QT6 does it automatically now

26.08.2021

- updated the Italian translation by Massimiliano Golfetto
- Turkish and Chinese small updates
- in Preferences the language names are in the native language
- fixed a Qt6 derived issue where dragging tabs outside the app crashed the app
- updated the Chinese translation, by 俊霄 余
- fixed bug in Cutout Plugin that when no object is loaded and the Generate Rectangular Geometry is clicked, the app will hard crash
- improved rendering performance for lines (with help from David Hoese from the VisPy team)
- fixed the convert 2 excellon and to geometry functionality
- minor changes

25.08.2021

- fixed some issues in the convert_units
- added a new GUI element, a radio button that can change a setting in the self.defaults preferences dict
- made sure that the update of the default properties tab (in the Notebook) is done only for certain keys in self.defaults not for all
- fixed the "headless" feature; running headless still start the GUI just it does not show it
- when running headless the sys tray icon will always be shown so the user can close the app correctly
- in the systray icon context menu I've added a menu entry to toggle the GUI
- fixed a possible issue with having older versions of QSettings (leftovers from previous installations of this app)

24.08.2021

- using PyQt6 with multi monitors, when the pixel ratio (scaling in WIndows) is different than 1.0 there are visual issues in the 3D canvas - trying to solve by updating the dpi - partial solve
- failing to load the 3d graphic engine is now reported on the log.txt file found in the Preferences folder (in appData/Roaming/app_name for Windows)
- added an example to illustrate the issue with scaling in Qt6 and VisPy
- the scaling issue is from VisPy, removing the previous changes on this subject and adding a patch until the new release of VisPy

23.08.2021

- added a partial translation for Chinese Simplified language, by 俊霄 余
- trying to separate the Preferences settings from App init and to make them dependent on the self.defaults dictionary
- updated the language strings from the source
- translation to Simplified Chinese language is done by 俊霄 余
- updated the Turkish translation files, by Mehmet Kaya
- in 2sided Plugin I've added ability to use shortcut keys (like Escape) when waiting for picking a hole when the Hole Snap feature is used
- in 2sided Plugin entering in the Hole Snap feature will disable the notebook to signal that you need somehow to escape this mode

22.08.2021

- added a new menu entry in the View menu to show the application log file where even the fatal crashes are logged
- fixed an issue due of porting to Qt6

19.08.2021

- some cleaning up in the Preferences
- cleaned up the Geometry and Gerber object classes
- more cleanup in Isolation Plugin
- started the work in adding to Isolation Plugin a new feature to extra isolate the pads
- some changes due of porting to PyQt6

18.08.2021

- fixed the "Set Color" functionality broken by recent changes
- updated the "Set Color" functionality to store the used color in a correct way
- when creating a nw project the user is asked if he wants to save the preferences
- changed the ColorRole in the on_about() method to 'window' os the programmers and translator tab is set to white color
- changed the translators tab in the on_about() method
- added the usage of the FCLabel widget instead of QtWidgets.QLabel
- cleaning up Isolation Plugin
- made sure that the KeyboardInterrupt exception is not logged to the log.txt file

17.08.2021

- made sure that the optimal tool detected works for isolation - the offset amount changed to reflect the number of decimals used by the app

15.08.2021

- fixed some issues with using the exec_() which now should be exec()
- added a context menu action in the canvas context menu for moving selected objects to origin
- made sure that the optimal tool detected works for isolation

14.08.2021

- small change due of porting to PyQt6 in self.on_portable_checked() method

11.08.2021

- fixed splash screen position on current screen when using multiple screens
- some changes due of porting to PyQt6

9.08.2021

- wip in porting to PyQt6: fixed OptionalInput and OptionalHideInput GUI elements

6.08.2021

- more changes due of porting to PyQt6
- fixed the Autocompleter highlighting to work in PyQt6
- created a new custom layout based on the Grid Layout where I can set the horizontal and vertical spacings between grid items and used it throughout the app
- changed the spacings in the new custom Grid layout

5.08.2021

- continuing to fix the PyQt6 port
- added an Exception when App.on_canvas_setup() fails
- fixed fullscreen functionality for PyQt6 and a few other fixes regarding the port to PyQt6
- more fixes for porting to PyQt6
- changes the exit method from exit(0) to quit()
- more work in PyQt6 porting
- Isolation Plugin - if there is no object selected try to select the first one if there is any
- Fixed setting a new style

4.08.2021

- toggling the plot is now deleting the selection shape, if any
- porting to PyQt6

23.07.2021

- fixed errors in the Roland preprocessors
- fixed CNCJob plot scaling when using Roland preprocessors
- in Roland preprocessors added the use of spindlespeed
- made sure that the preprocessors that don't use the gcode_header still use the start gcode


21.07.2021

- when using the NCCAD9 preprocessor (for Kosy machines) at GCode save the .knc extension will be automatically preselected
- added a preprocessor for Roland MDX-540
- fixed the preprocessor for Roland MDX-20 X,Y moves

20.07.2021

- in ParseDXF file fixed import of module due of changes in the latest version of ezdxf module
- changes in Milling Plugin in the selection in the Tools Table
- some updates of milling data structure
- changes in handling the tool shape due of moving the parameter from the tool table in the tool parameters section
a
18.07.2021

- modified the Geometry UI tools table. Replaced the comboboxes with labels.
- Fixed the SolderPaste Plugin regarding the special designation 'SP' job type and 'DN' tool shape
- some fixes in the GCode Editor regarding the new changes in the data structure
- some updates to the language strings
- finished preprocessor file for Kosy CNC machine (NCCAD9)
- fixed the gcode header export to work with the new data structure

15.07.2021

- updated the Turkish language with the translated strings by Mehmet Kaya
- modified the setup_ubuntu.sh file removing a deprecated package
- added a Linux man page, courtesy of Romain Porte

14.07.2021

- preferences UI is now started on preferences open
- more work/fixes in working on data structures (offset, offset_value, job_type, shape)

7.07.2021

- some changes in data tools structure ('tool_type' to ['data']['tools_mill_job_type])
- some strings changes
- working on data structures (offset, offset_value, job_type, shape) and on the Tools Database

4.07.2021

- optimized the Milling Plugin preferences
- added more preferences for v-shape tools in the Milling Plugin Preferences
- deactivated the automatic disable of the Cut Z parameter when selecting a v-shape tool in Milling Plugin
- in Milling Plugin added some protections against receiving signals from widgets without objectName set
- in Milling Plugin made sure that the Custom offset field is displayed only when it is needed (selected from Offset Type combobox)
- added some requirements asked by vispy v0.7.0
- if the generated Gcode is empty then the app will know and will not create a CNCJob object
- modified the Tool Table in Milling Plugin: now the tool type parameter is part of the Tool parameters and no longer in the Tool Table
- working on a Kosy CNC machine preprocessor file

21.06.2021

- optimized the data storage in the on_about() method
- randomized the order in the programmers list at each launch of the on_about() method
- fixed typo in Gerber parser

19.06.2021

- fixed Film Plugin (Tool) negative generation to have the black background in sync with the actual geometry when transformations are applied
- fixed Film Plugin (Tool) to have mirroring done only with reference 'center' regardless of what is selected in the UI
- in Film Plugin now multiple adjustments can be applied at the same time and correctly
- updated the files to use the new name for the voronoi package: foronoi
- using Python 3.9.4

17.06.2021

- fixed the setting color functionality on right click on the objects in the Project Tab such that the set color (or opacity) it is stored in a persistent setting correctly and the colors for Gerber objects are restored in order
- trying to fix a Runtime exception that may happen when the Plugin Tab is deleted completely

10.06.2021

- fixed some issues with error reporting for the Tcl commands
- fixed drillcncjob Tcl command crashing the app when the used name is wrong and therefore no Excellon objects are located

16.05.2021

- fixed SVG import to show an error for SVG files that have SVG units not mm or cm or inch
- for SVG files with cm units the scaling factor is multiplied by 10
- the app will signal failure when encountering the 'g' SVG element which is not supported and cancel the SVG import

7.05.2021

 - fixed the Gerber parser to work for the case of having coordinates with negative values and the trailing zeros are removed and leading zeros are kept 

3.05.2021

- changed the save_project() method such that when the project is saved as compressed now the compression is first done in memory and only after that the app will attempt to write it to a file on disk

29.04.2021

- in CNCJob object, for CNCJob objects created when importing a foreign Gcode file, fixed the updating of the tool diameter
- in CNCJob object, when there are no tools in the Tools Table (it happens when importing foreign GCode files) the Tool Table is now displayed in a correct way

20.04.2021

- fixed a recent but in Gerber Object that crashed the application when the aperture marking was used

12.04.2021

- small string change

5.04.2021

- now every version of FlatCAM beta creates its own set of Preferences files

4.04.2021

- fixed drill file import from FreePCB

1.04.2021

- fixed a bug in the Drilling Tool when there are tools without drill points

29.03.2021

- Milling Plugin - finished the polish functionality

28.03.2021

- upgraded all the plugins such that at each launch the Plugin UI is reinitialized reducing the chances to get errors like "wrapped up C++ object deleted" with the side effect that the performance might be slightly reduced
- minor logging changes
- created a new category in Preferences where only the Engraving Related preferences are located
- added a new set of options in the Preferences for the Milling Plugin by taking them out of the Geometry section
- fixed an error in the Subtract Plugin when working on Geometry objects
- fixed ParseFont such that the output of the Text Plugin in the Geometry Editor can now be painted using the Paint Plugin
- fixed errors when using the new ... canvas context menu entries which crated a hard crash
- minor layout change in Preferences -> Geometry

22.03.2021

- Milling Plugin - will update the UI form on object change with the data from its last tool
- Drilling Plugin - will update the UI form on object selection on canvas

21.03.2021

- fixed a small issue in Film Plugin (the object selection the plugin UI is not updated with the selected object in Project Tab)

20.03.2021

- removed another parameter in the serialized parameters for CNCJob object (no longer used) which made the project saving to fail (when CNCjob objects are in the project)

19.03.2021

- fixed mouse bytes generation if Cutout Plugin to add an extra drill hole at the end
- fixed an issue in Film Plugin made by copy/paste done recently to make sure that objects selected in Project Menu are populated in the Plugin UI on Film Plugin launch
- removed a parameter in the serialized parameters for CNCJob object (no longer used)

18.03.2021

- changed some strings; updated the translation strings with the new strings (they need to be translated)
- modified all the plugins (formerly named Tools) to auto select in the plugin the selected object from Project Menu
- small changes in Tools Database

15.03.2021

- the GCode generation takes now into consideration the Toolchange X-Y parameter as a starting point
- Milling Plugin - work on it; upgraded the form-to-data_storage methods
- fixed bug in Copper Thieving, Corners and Fiducial Plugins which crashed the app when using Disable Plot menu action on the Project Menu objects
- Etch Compensation Plugin - fixed a number of issue; fixed issue #500

14.03.2021

- Geometry Editor can now modify the edited tool diameter
- upgraded the Drilling Plugin and all the related parts in the CNCJob Object and in all preprocessors. Now, the parent 'tools' attribute is inherited and also the GCode is stored here
- small fix in the Toolchange_Manual preprocessor
- Drilling Plugin - added in the UI the toolchange X,Y parameter and made it to work as expected
- Milling Plugin - added in the UI the toolchange X,Y parameter and made it to work as expected
- allow the old projects to be loaded without crashing the app, although the load is incomplete
- updated the Milling Plugin and all the related parts in the CNCJob Object and in all preprocessors. Now, the parent 'tools' attribute is inherited and also the GCode is stored here
- made sure that old projects load but without the CNCjob objects which would have crashed the app due different data structures
- the FlatCAm Evo projects load now in succession, no longer on threads

13.03.2021

- fixed a bug in Gerber merge
- bypassed a bug in FlatCAM Gerber UI - it's not really fixed (it's a crash in build UI when disconnecting signals due of the wrapped C++ object already destroyed)

11.03.2021

- fixed issue when exporting DXF and not settings the units
- added the possibility to export the Geometry in different DXF formats (previously it was hard coded to the AutoCAD 2010): R12, R2000, R2004, R2007, R2010, R2013, R2018
- fixed the camlib.Geometry.bounds() method such that it is calculated correctly for Geometry objects created in the Geometry Editor

7.03.2021

- Levelling Plugin - mare sure that there are no duplicate seed points when creating Voronoi polygons
- Levelling Plugin - fix of missing variable declaration (forgot to take everything when I moved the functionality from the CNCJob class to its own class)
- made sure that possible temporary drawn shapes are deleted when the UI of the Plugin that created them is closed (by closing it in the Plugin Tab)
- modified the CNCJob UI to add a shortcut button for the Levelling Plugin

6.03.2021

- some minor updates to NCC Tool

5.03.2021

- added detection of Gerber files in Gerber X2 format
- added a new feature: if a Gerber file in format Gerber X2 having Drill information's is detected then after loading, it will be converted to an Excellon object
- fixed the Extract Tool to work with apertures (tools) keys as integers
- fixed the Copper Thieving Tool to work with apertures (tools) keys as integers
- fixed the Corners Tool to work with apertures (tools) keys as integers
- fixed the Fiducials Tool to work with apertures (tools) keys as integers

4.03.2021

- fixed Punch Gerber Tool to work in new conditions
- fixed creating positive films in Film Tool, because of recent changes
- working on the Levelling Tool - trying a new Voronoi module (https://github.com/Yatoom/voronoi) without success
- fixed some display issues in the Levelling Tool

3.03.2021

- fixed errors on project load, in ParseGerber and Extract and Punch Gerber Plugins due of refactoring the 'apertures' attribute to 'tools'
- aperture keys in the gerber 'tools' dictionary are now stored as integers instead of strings as before; need to check the application especially the Gerber Editor
- fixed issues in Gerber Editor (using the '0' instead of 'REG' type for apertures)

2.03.2021

- removed the spacing between icons in toolbars
- fixed a problem in saving done at the app closure

01.03.2021

- major rafactoring: replaced the name of the Gerber object data structure from 'apertures' to 'tools' to make the object data structure uniform across the app

28.02.2021

- fixed Panelize Tool to work with objects made by merging other objects together

27.02.2021

- added possibility of changing the Axis color and created a new parameter for that in Preferences
- some refactoring
- in a FCColorEntry GUI element, setting a color in the Color dialog will trigger the editingFinished signal therefore propagating the changes
- Milling Tool fixes: milling tool dia after edit fix and milling slots fix
- update in Code Highlighter to highlight the X and Y chars
- updated the Document and Script objects Beginner/Advanced selection labels to the same as the rest of the app objects
- strings update
- make sure that Autoload Tools from DB functionality from Excellon Object UI is working only with tools from DB market for Drilling Tool

26.02.2021

- fixes in Drilling & Milling Plugins for the error: "RuntimeError: wrapped C/C++ object of type ... has been deleted"
- Milling Tool (Plugin) - fix to use the tool diameter set in the Milling plugin tool; selection in the Tools Table is now by row
- Film Tool - made sure that the reference point is used for all transformations in case of positive film
- Film Tool - made sure that the reference point is used for all transformations in case of negative film; negative film now use the color setting from Preferences too
- changed some confusing strings regarding the 'Properties'
- Properties Tool is now ObjectReport Tool
- fixed some attributes in Milling Slots Excellon Object utilities functionality
- fixed some issues in the translated strings .po files

13.02.2021

- fixed a bug in Transform Tool when calculating the point reference
- working in Film Tool to add the reference for all adjustments

10.02.2021

- fix of Tcl command AlignDrill by Bogusz Jagoda
- small changes in FlatCAMExcellon.py file to fix milling the drill holes (due of the new changes in Geometry data structure)

29.01.2021

- some refactoring
- working on Milling Tool and Drilling Tool: made sure that the plugin UI is initialized only when the plugin (Tool) is run in order to avoid errors like (wrapped C++ objects was deleted)

28.01.2021

- added a new preprocessor by Georg Ziegler, used with a laser (cleaned it for PEP8)

27.01.2021

- some fixes in Drilling Tool and in camlib.py file
- working on Milling Tool
- working in Drilling Tool to make it more robust
- Drilling Tool - fixed an issue with dropping the changes done in the UI
- Drilling Tool - fixed an issue when Drilling Tool is open and the last Excellon object is deleted
- fixed an issue in Tool Cutout - when using the thin gaps the 9999 tool number is not accepted by Mach3
  
21.01.2021

- added ability to see the verbose log when importing SVG's (if set in preferences)
- added some more logs to the SVG import
- fixed minor issue with not having the Cancel option on exiting the GCode Editor through the Toolbar button
- in Gerber Editor fixed the canvas context menu not showing
- made sure that the Offset fields in the Properties Tab are updated on object: move, origin change, transformations
- when doing 'Set Origin' the right mouse click will cancel the action

19.01.2021

- fixed the Gerber export to convert all the aperture macros to regions and therefore export also the data from aperture macros

17.01.2021

- some changes in the GUI elements and some updates in the Cutout Tool
- fixes in Panelize and Cutout Tool (Panelize has issues when exporting Gerber with aperture macros)
- changed the Drilling Tool such that when an Excellon Object was generated to use the 'Check_points' preprocessor then only that preprocessor will be available and usable

13.01.2021

- working on the Follow Tool and Milling Tool
- working on the Isolation Tool and Milling Tool
- fixed GCode Editor such that selection of the only tool present when the GCode ie generated without Toolchange event will select all the actual working GCode

12.01.2021

- in Gerber parser added a fix for Gerber region geometry that is self-intersecting

11.01.2021

- working on Tool Follow, Milling Tool, camlib to adapt them to the new changes in the Geometry Object

10.01.2021

- renamed the Tool menu category to Plugins
- for all the plugins optimized the plugin name
- renamed the app to FlatCAM Evo in order to make a difference from FlatCAM app which follows its own path
- modified the splash image to reflect the new naming of the app
- modified some of the documents (License, Readme) in the app to reflect the actual reality in 2021
- made more changes in the names; Tools Toolbar is now Plugin Toolbar
- the detachable tabs are now not detachable by default after first install

9.1.2021

- attempting to fix issue #492 - when checking for tools present in the database there were some errors in the NCC, ISO, MILL, PAINT and CUTOUT Tools
- minor change in Geometry Object
- some changes in Tools Database and in Cutout Tool; working to update to new milling parameters

8.01.2021

- added the toggle button for Beginner/Advanced mode for the CNCjob, Geometry, Gerber, Excellon Objects UI (Properties Tab)
- some changes in PreferencesManager due of Milling Tool changing names of the geometry defaults to tools_mill_ defaults
- for Excellon, Geometry and Gerber objects now in the Object UI (Properties Tab) by default the Offset field is populated with the coordinates of the center of the current object
- for NCC, Cutout, Paint, Milling Tools made sure that the tool data dict is populated correctly

6.01.2021

- allowed some more parameters to have negative values (tool diameters especially)
- some more tools now have a Beginner/Advanced mode
- for QRCode and Subtraction Tools added also a Beginner/Advanced mode
- in NCC Tool fixed issue #486 where creating Area Selection shapes of type Square with Grid snap OFF will not use the first click position
- fixed a bug in Paint Tool when doing Painting Area Selection and nothing is no polygon is in the selections to be painted
- fixed Isolation, Paint and NCC Tools to use the Tool Type parameter from Preferences on adding new tools and also setting the 'type' key in tools dict correctly
- fixed the export_dxf and export_svg Tcl commands
- fixed issue #489 - missing `pywin` module requirement in the `requirements.txt` file

5.01.2021

- changing some parameters from 'geometry_' to 'tools_mill_' format

4.01.2021

- Levelling Tool - disable the UI if the Gcode is not segmented for autolevelling (perhaps recreate the GCode as segmented?)
- in camlib.CNCjob class fixed the linear2gcode_extra() method to be able to do GCode segmentation.
- Levelling Tool - minor changes

3.01.2021

- fixed the Tcl Commands: new_geometry, new_gerber and new_excellon to create correct objects. New Geometry object created with the new_geometry Tcl command accept now the usage of add_circle/polygon/polyline/rectangle Tcl commands
- updated the autocompleter list for the Tcl commands with missing Tcl commands names
- added three new Tcl Commands: add_aperture (adds an aperture to a Gerber object), add_drill and add_slot (add a drill/slot to an Excellon object)
- added a new parameter to the New Tcl command named -reset. If set False (or 0) the TCL instance will not be recreated, allowing to keep the TCL variable content on the new project.
- included the modifications from PR #336 from Andre Spahlinger

2.01.2021

- removed the 'machinist setting' and allow all over the app the usages of both negative and positive values (where it is the case)
- applied the changes from Andre Spahlinger from PR's #332 and #334
- moved all the Levelling stuff out of the CNCjob Properties Tab to its own tool, Levelling Tool. The functionality itself is not finished yet.
- Levelling Tool - changed the Beginner mode
- Levelling Tool - moved all the Levelling Preferences into its own category

31.12.2020

- fixed the issue with not saving the project on program close if selecting the 'Yes' option.
- all the logs done on Exception are now errors (with the exception of few places where the Exception is expected)
- added the modifications made by Andre Spahlinger in PR #333
- replaced all usages of QFormLayout with QGridLayout
- fixed parseDXF() class imports to work with newer versions of ezdxf module (starting with 0.15) - contribution of Maurizio D'Addona and Hans Boot
- fixed a string ("Penelization Reference") by Hans Boot contribution.
- fixed a string ("Penelization Reference") translations by Hans Boot contribution.
- fixed issue with not being able to mill holes in an Excellon object part of a loaded project (JSON serialization makes all keys in the dictionary into strings so I had to make sure that the obj.tools dict keys are made integers on project reconstruction)
- added needed 'testresources' module in the setup_ubuntu.sh script - Hans Boot contribution

30.12.2020

- fixed error in Gerber Editor
- made some more log.debug messages visible in Tcl Shell

28.12.2020

- Gerber Editor - Import Shape sub-tool finished (by adding selection by dragging a selection box)
- fixed issue #478 - Tcl Command 'New' updated

27.12.2020

- Gerber Editor - finished the Import Shape sub-tool single shape select feature

26.12.2020

- made sure that the Tool menu is disabled when entering into an Editor and re-enabled on Editor exit
- Gerber Editor - working on a new sub-tool: Import Shape which should allow importing polygons from other Gerber objects

25.12.2020

- merged PR by Dmitriy Klabukov and expanded it a bit
- Gerber Editor - made sure that when simplification button is clicked the coordinates and vertex points field are cleared
- Gerber Editor - added a Beginner/Advanced toggle button
- Gerber Editor - added context menu to Apertures Table and also on row selection in Aperture Table, the area and vertex number fields are updated
- Excellon Editor - added a Beginner/Advanced toggle button; added context menu to Tools Table
- Excellon Editor - fixed the Tools Table row selection handler
- GCode Editor - added a Beginner/Advanced toggle button
- Geometry Editor - added a Beginner/Advanced toggle button
- Geometry Editor - made sure that on right clicking in the Table the geometric element associated with the row under mouse cursor is selected
- Geometry Editor - added the label Beginner/Advanced for uniformity within the app
- minor string changes

23.12.2020

- Dwell and Dwell Time parameters are visible in Beginner Mode

21.12.2020

- made sure that the GCode Editor does not have a misleading Cancel option when closing the tab
- made sure that some other part of the program outputs the log messages to the Tcl Shell if the Verbose Log options is set in Preferences
- minor changes to the syntax highlighting in GCode Editor

20.12.2020

- modified the way the status bar icon is set 
- Drilling Tool - fixed missing feedrate code when the toolchange is Off
- AppTextEditor - working on syntax highlighting
- App - trying to speed up the new project creation
- Tcl Shell - Browser Edit - added Undo/Redo, Cut and Delete selection
- replace all the exec_() calls with exec() (except one situation in Tcl where I'm not sure of the effect)
- in verbose log made the messages in Tcl Shell to be displayed in gray color and to include the date

18.12.2020

- fixed the Tcl command export_gcode to return the actual gcode
- Gerber Editor - when doing a selection of polygons, the displayed area is now the cummulated value

17.12.2020

- Gerber parser - when simplification is used, the tolerance value will be adjusted by dividing it with 25.4 if the file units are imperial

15.12.2020

- modified the way that the PlotCanvas widget is added
- in ParseGerber class fixed simplification controlled by the Preferences parameter 
- in ParseGerber class made logs use the self.app.log handler which in turn can output messages in the Tcl Shell

14.12.2020

- added new functionality: centering in origin for a selection of objects
- for all centering functionality, now if the moved object is of 'geometry' kind it will have the source_file attribute updated with DXF code
- modified the centering in origin functionality to include a selection of choices in a dialog
- fixed Locate in Object functionality to work in the situations where there are negative bounds values
- Gerber Editor - the vertex number points are now calculated also for a selection of shapes
- Geometry Editor - the vertex number points are now calculated also for a selection of shapes
- Geometry Editor - made sure that the shapes cannot be selected while the simplification is in process because it will mess things

13.12.2020

- GCode Editor - fixed the issue with the editor toolbar buttons not being updated like for the other editors
- GCode Editor - the plot_area tab GCode editor that is added will close the Editor when the tab itself is closed
- SolderPaste Tool - the GCode Viewer will open a AppText Editor Tab with Plain Text
- GCode Editor - added an Exit action to the context menu in the GCode Editor Tab

12.12.2020

- added an experimental 3D area (archball camera)
- Drilling Tool - fix a bug introduced in the latter changes; when the Tool is launched and there is no Excellon object loaded then the default_data can't be filled in: solved by using the self.app.options in that case
- Drilling Tool - made sure that the preprocessors that are not allowed in this Tool are not loaded in the combobox
- SolderPaste Tool - fixed not recognizing the default tools
- SolderPaste Tool - fixed the UI
- SolderPaste Tool - fixed various issues
- Cutout Tool - made the gap size parameter to take values between 0 and 10000

11.12.2020

- updated the 'Default_no_M6' preprocessor by removing the Tx command in the Toolchange section to make it compatible with GRBL controllers
- added a new preprocessor named 'Check_points' which is to be used to check if the PCB is well aligned in position (used by at least the Corners Tool)
- Tool Corners - added a new feature that creates a verification GCode by moving in the locations positions; modfied the UI a bit
- Drilling Tool - removed the mandatory Toolchange event added even if the Toolchange was not selected
- Excellon and Geometry objects no longer have the self.options dict overwritten with the application defaults
- Drilling Tool - first tool that benefit from using the object options instead of application defaults; this is useful if an object is recreated on project load
- Drilling Tool - fixed it to update the UI form also for common parameters on object change
- new Application Objects will hold all the options that start with 'tools_'
- Tool Corners - fixed the positions of the drills and for the verification locations in the Drill in Locations and Check in Locations Excellon objects, to take in the consideration the tool diameter
- modified the default drawing tolerance from 0.01 to 0.001

10.12.2020

- NCC Tool - remade the 'Beginner/Advanced' Mode
- Paint Tool - remade the 'Beginner/Advanced' Mode
- Cutout Tool - remade the 'Beginner/Advanced' Mode
- minor changes to the UI to all Tools with 'Beginner/Advanced' Mode
- Film Tool - remade the 'Beginner/Advanced' Mode
- 2Sided Tool - remade the 'Beginner/Advanced' Mode
- Drilling Tool - fixed a bug where this Tool will work on a previous version of the selected Excellon object (in case that the object was altered between launch of the Drilling Tool and the GCode generation)
- added some color coded titles in some Application Tools that hold multiple functions
- in ObjectCollection class made sure to use the log handler defined in the App class
- minor changes in App.__init__()

9.11.2020

- simplifying the UI for the Milling Tool when in 'Beginner' mode
- Milling UI - clicking the 'Beginner/Advanced' top button will switch the application mode for the current tool(this change need to be propagated everywhere a 'Beginner' mode is needed)
- fixed the on_delete() method in the App class; sometime it will delete all files that have similar names
- made sure that on creation of new objects the adding of the names to the auto-complete list is done properly
- Drilling Tool - remade the 'Beginner/Advanced' Mode
- Milling Tool - fixed the ever-growing list of preprocessor's
- in all Object UI's fixed the Properties section columns resize to content on expansion/collapse of the items
- in Tcl Shell Dock widget make sure that the context menu is not shown on the title bar and middle clicking on the title bar will close the widget
- Drilling Tool - minor UI change
- Isolation Tool - remade the 'Beginner/Advanced' Mode
- Panelize Tool - some UI fixes

8.12.2020

- few changes in the Milling UI
- made some changes in the application to prepare for usage of PyQt6

7.12.2020

- Geometry object - work in upgrade of the data structure

6.12.2020

- Milling Tool - working on the data structure

4.12.2020

- in Cutout, 2Sided, Film, NCC, Paint, Panelize and Subtract Tool made sure that the object selection in Project Tab reflects in the selected object in the Tools
- set the shortcut key for Milling Tool to ALt+M and for Corner Markers Tool to Alt+B
- fixed a bug introduced by first mod today, when the selected object is of 'cncjob' kind.
- Milling Tool - working in making the exclusion areas work in this Tool; added the Exclusion Table editing ability
- Milling Tool - fixed a crash when clicking the Generate CNCJob object button with no object selected
- Milling, Isolation, NCC, Paint Tools UI update

3.12.2020

- in Gerber Editor - modified the UI
- in Gerber Editor - added new parameters for selected polygons: is_valid and area
- in Gerber Editor - for Disc and SemiDisc tools now it is allowed to start without a defined aperture; it will be assumed that we work with an aperture with size zero
- Subtraction Tool - fixed issue when from a given polygon it is subtracted multiple ones, the result being that the subtraction is not done

2.12.2020

- Subtraction Tool - minor changes
- in Gerber Editor - added some parameters to the UI: selected polygon coordinates and vertexes number and also added polygon simplification
- in Gerber Editor - fixed update of Aperture Table rows selection on multiple shapes selection
- in Geometry Editor - modified the FCCircle class to work as is the rest of the Geometry Editor add shapes classes
- fixed the display of status bar messages when they contain brackets
- in Panelize Tool - fixed the export when panelizing Excllon objects
- in Panelize Tool - remade the methods such that panelizing a Gerber object as Geometry panel will now hold all the Gerber apertures as Geometry tools and added a supplementary tool that holds the solid_geometry. 
- in Panelize Tool - remade the methods such that panelizing a Geometry object as a Gerber panel will attempt to create polygons from Geometry
- in Panelize Tool - made sure that when the path optimization will yield an empty geometry it will not be added to the panel geometry

1.12.2020

- in Milling Tool fixed the UI change as a result of preprocessor change
- in Isolation Tool added possibility to have tools with the same diameter; remade the sorting of the tools storage
- some work in Milling Tool
- Milling Tool - working in tool data structures
- Milling Tool - more or less made the CNCJob generation for multi-geo Geometries to work; still the parameters may not be used
- removed Generate CNCJob context menu action in the Project Menu as this will not work anymore
- Milling Tool - selecting an object on canvas will update the selection of the object combobox, if the selected object type is the same sa the one selected in the Target radio
- Milling Tool - changing parameters for the Geo Tool table now updates correctly the storage and are used in the CNCJob object that is created
- Milling Tool - after creating and CNCJob object it is now auto-selected and the Property tab is automatically switched to
- Milling Tool - various fixes
- Line_xyx preprocessor - fixed when using multi-depth cut to use the right depth
- Milling Tool - trying to fix an error on dragging and dropping tools in the Geo Tools Table

29.11.2020

- in Geometry Editor - improvements and some issues are solved
- in Geometry Editor - improvement in the update method of the whole Geometry Editor UI on shape selection on canvas
- in Geometry Editor - fixed multiple plotting for the same selection
- in Geometry Editor - made sure that selecting shapes on canvas will update the UI - final fix
- in Geometry Editor - the Length and Vertex Points GUI elements are now read only since it does not make sense to edit them
- in Properties Tab UI (Object UI) made sure that the UI stays compact even on large screens

28.11.2020

- updated the Italian translation (by Massimiliano Golfetto)
- added the mechanism to create an error log in the data path where to write debug data for the crashes of FlatCAM
- Turkish language strings updated (by Mehmet Kaya)
- in Geometry Editor - reverted to using non-threaded move
- in Geometry Editor - made sure that polygons are displayed as such in order to not create confusion (the plotted shapes now have 'content')
- in Geometry Editor - in Polygon tool solved issues with adding a double point at the last position
- in Geometry Editor - the Rectangle and Polygon tools are now the result stored as LinearRing instead of Polygon
- in Geometry Editor - upgraded the Intersection and Subtraction tools to work correctly using as inputs LinearRing's
- in Geometry Editor - fixed the display of coordinates for MultiLineString's
- in Geometry Editor - fixed the Explode tool to work in the new conditions
- in Geometry Editor - fixed the Buffer tool to work in the new conditions

27.11.2020

- in Geometry Editor fixed the selection on the Geometry Table
- in Geometry Editor - made sure that the edited Geometry do not contain Shapely Points and that when adding shapes, that the geometry of that shapes is valid and non empty and it is not a Shapely Point
- in Geometry Editor - added new information's (length, coordinates and vertex points number) for the geometric element selected in the Geometry Table
- in Geometry Editor - added more parameters displayed for the geometric elements selected in the Geometry Table: is_valid, is_empty, is_ccw, is_simple, is_ring
- in Geometry Editor - added a new feature: Simplification. It will greatly reduce the number of vertex points in a geometry element selected in the Geometry Table thus potentially greatly reducing the resulting GCode number of lines
- in Geometry Editor - simplification method is now threaded and optimized
- in Geometry Editor - fixed a crash on Geometry Editor exit
- in Geometry Editor - added a new feature: ability to change the orientation (from CW to CCW and the revers) for Polygon and LinearRing geometry elements.
- in Geometry Editor - added a context menu in the Geometry Table
- fixed crash when launching with 2D Graphic Engine (due of recent changes)
- in Geometry Editor - added new feature: Zoom on selected (by selecting a geometry element in the Geometry Table)
- in Geometry Object Properties UI - added the UI for Utilities and within Utilities added the Simplification UI
- in Geometry Object Properties UI - finished the new feature Simplification and Vertex Points calculation which should greatly reduce the resulting GCode size
- in Tool Isolation, for Polygon Selection method, added ability to select all/clear all polygons allowing thus negative selection (select all followed by deselection of the polygons you don't want isolated, e.g a ground plane)
- some refactoring between appMain class and MainGUI class
- on tab close in Notebook the tool_shapes are deleted (shape markings used by some of the App Tools) therefore part fo the clean-up
- added some protections in case the Editors could not start such that the app is not crashed
- fixed wrong display of editor actions in the Editor toolbar at the first start of the app
- fixed missing import in the preprocessors
- deleted the Toolchange_custom preprocessor because it is no longer used
- updated Language strings
- translated the strings to Romanian language
- Google-translated the strings to German language

26.11.2020

- fixed error in Tools Database when deleting first tool; remade the tool deletion method
- in Tools Database added tooltips for each column item in the left TreeWidget
- in Tools Database made sure that editing the Target and Tool diameter in the right side section will update the values in the left TreeWidget
- in Tools Database added a Sort by Target context menu entry and functionality; fixed the Sort by Dia functionality
- in Tools Database - clicking the header sections of the TreeWidget for columns Target and Diameter will sort the tools by that criteria
- in Tool Follow made sure that the resulting geometry elements are all valid and not empty and also do not contain Points as those are irrelevant in the Follow Tool

25.11.2020

- had a first shot in creating a way to show the log messages in the Tcl Shell if a setting in Preferences is set
- fixed Isolation Tool tool table context menu pointing at the same method for the menu entries: Add Tool and Pick from DB
- made sure that the GUI element FCInputDialogSpinnerButton has the spinner element auto-selected on launch
- fixed an error in Geo Editor when trying to save an empty Geometry
- in Preferences made sure that clicking the Restore Defaults will update the values in the Preferences Tab
- some changes in the Tools visibility in the Tools ToolBar
- added confirmation status messages for clearing the recent files/projects lists
- moved around portions of code in the App.__init__ for optimization; added a control in Preferences for usage of Log in Tcl Shell
- added a text placeholder in the Tcl Shell
- added Find function in Tcl Shell (will search for either the selected text in the command line or for the one stored on the clipboard)
- using 'Escape' key shortcut while the focus is in the Tcl Shell command line will set focus on the main canvas
- in Tools Database added a new column showing the intended target and also added in the context menu an entry that will sort the tools by tool diameter
- fixed in Tools Database the message pop-up that tools are modified but not saved on Toos Database tab closing
- in Tools Database added a 3rd column showing the Tool Diameter

24.11.2020

- fixed an issue for importing DXF as Geometry when the DXF geometry is a single line
- updated the convert_any2geo() method such that resulting geometry objects have the tools attribute required in the newer versions of the app
- added a way to keep compatibility with old projects
- some small fixes in the Geo Editor such that the Tool Tab is no longer shown on Editor Exit if it was used by an Editor sub-tool
- removed the Notebook hide action when using the Esc key in the Geometry Editor
- changed how the Editor toolbar button works; added a background for the 'Exit Editor' toolbar button when in Editor

21.11.2020

- Tool Copper Thieving - made sure that the clearance for pattern plating mask is applied also for positive values
- Tool Copper Thieving - when creating pattern plating masks now the user can select to use only the pads and that's useful when the soldermsk Gerber is actually a copper Gerber
- Tool Copper Thieving - changed the units for plated area from mm2 in cm2 when the app units are Metric
- Calculator Tool - Electroplating Calculator - changing the area will update the current value
- GUI Elements FCDoubleSpinner and FCSpinner: modified the context menu to not allow cut/paste/delete/step_up/step_down when the GUI element is set as Read Only
- GUI Element FCLineEdit (and the inheritors) has now disabled cut/paste/delete context menu entries too
- Cleaned the GUI in Excellon Preferences of parameters no longer used (transferred to Drilling Tool)
- Removed warning message boxes from export DXF/SVG handlers of the menu entries in the File menu and upgraded the status bar message to error
- reactivated the close_app Qt signal mechanism
- updated the Readme file - MacOS installation instructions to ensure that the gdal package is installed correctly
- Punch Gerber Tool - made sure that Select All/Deselect All in Manual mode is actually selecting pads not only visually
- Punch Gerber Tool - finished manual mode for "Excellon" method
- Punch Gerber Tool - finished manual mode for all methods
- Cutout Tool - fixed generation of rectangular geometry if the selected gaps is "None"

20.11.2020

- fixed the Distance adn Minimal Distance Tools not showing
- in Geometry Editor and in the Gerber Editor made sure that for the sub-tools that open in the Tool Tab, if the tab is closed then the Select Tool is automatically selected. And also trying to open that sub-tool again will open a new tab.

19.11.2020

- cleaned up the Geometry Properties UI
- in Geometry Properties UI restored the plot functionality
- in Tool Cutout fixed issues when using a negative value for the Margin parameter when the cutout object is a Gerber object
- fixed the Gerber parser such that when it encounter an aperture with size 0.0 it will replace it with the smallest number that is still displayed which is (10 ** -self.decimals); previously it replaced with a too small number (1e-12)

17.11.2020

- the Follow Geometry outputted by the Follow Tools is now of type multigeo which means that it can be fused with other multigeo object without much pain 
- Punch Gerber Tool - working on the new manual pads add feature
- fixed PDF Tool such that now it can import more types of files including PDF files made with FlatCAM

16.11.2020

- modified the behavior of the Tool Tab in the Notebook widget. Now the app start with the Tool Tab off. If an App Tool is launched then the Tool Tab is added and populated. If the same App Tool is launched again and the focus is in its Tool Tab then the Tool Tab is removed but if the focus is elsewhere then the focus is set again on the Tool Tab.
- fixed the App Tools buttons in the Tools section of the Properties Tab for Excellon, Gerber and Geometry objects
- made the Tool Tab closable
- fixed a small typo in the Gerber Properties Tab that made the icon for the Cutout Tool not to show up
- fixed the menu entries for the Properties Tool, Transform Tool
- fixed the Tools menu actions
- added new feature in the Calculator Tool: the Electroplating sub-tool is now able to do reverse calculations when time is adjusted; fixes issue #14 (on the work repo)
- Turkish language strings updated (by Mehmet Kaya)

15.11.2020

- working on adding new feature to Punch Gerber Tool - manual selection of pads to be punched
- Punch Gerber Tool - added methods to draw the pads selected and some UI buttons to select all and clear all to help in the selection
- Milling Tool - default state is for all the tools in the Tools Table to be selected
- removed the enforced ActivityView width
- various string changes; updated the strings
- brought up-to-date the Romanian translation strings
- updated the source file when creating Excellon objects in various places in the app
- working in Milling Tool
- updated the Calculators Tool such that now the Cut Z parameter for V-tip tool calculator can be calculated from the other parameters too
- fixed a small issue in the Calculators Tool Cut Z default value; added a confirmation message for Electroplating calculator
- fixed bug in opening PDF file from recent files list
- replaced some of the icons in the app

14.11.2020

- upgraded the Extract Tool to allow aperture selection therefore narrowing down what apertures are the source for drills and/or soldermask openings
- some work in PDF parser to make it work more reliable (not sure if I succeeded)

13.11.2020

- updated the Geometry Editor such that loading objects into Editor and updating the edited object is done now multithreaded
- most of the functions in the Geometry Editor are now multithreaded
- fixed the Subtract sub-tool in the Geometry Editor
- added an alternative Subtract sub-tool in the Geometry Editor, one that do not delete the subtracting shapes
- added some not-so-good icons for the Subtract Alt sub-tool in Geometry Editor
- minimized the number of tools present in the Tools Toolbar to the bare minimum
- fixed an error in the Follow Tool
- started to work in Milling Tool - designing the Tool UI
- Milling Tool - finished the UI and also the button handlers in the Geometry and Excellon objects Properties UI
- Milling Tool - fixed the situation when launching the Tool but there is no object loaded
- Milling Tool - made sure that on target object change the UI is build again
- Geo Editor - Text sub-tool - fixed font size control not shown; fixes issue #470
- Geo Editor - fixed an issue with having a translatable text empty; updated the translation strings from sources
- added a message warning the user that the file is no longer available when it tries to open a file from the Menu -> Recent files, that was moved or deleted
- fixed a tooltip in the Extract Tool for the Extract Cutout button
- Milling Tool - added plot awareness and connection to the Plot button in the tool; updated translation strings
- Milling Tool - finished adding the add tool, copy tool, delete tool and tool plot when the target object is Geometry
- in camlib.py fixed the bounds() method for the Geometry objects such that tools in an Geometry object with no geometry will be now ignored for calculating the bounds
- in Milling Tool - synchronized the Tool Table for Geometry Object with the Geometry UI Table (in Properties)
- in Milling Tool - the Tools Table selection works now as expected (still have to store the changed parameters/update the parameters UI)
- fixed an error in the app that allowed editing of Geometry objects with multiple tools selected and of type MultiGeo; also reverted some changes in case of error in this section
- in Milling Tool for Geometry object added the V-tip dia and V-tip angle parameters and linked their visibility to the TT column in Tool Table 
- in Milling Tool the Edit tool method now works and also updates the Cut Z if the shape of the edited tool is V-shape

12.11.2020

- some fixes in the appMain class
- removed the "follow" functionality from the Isolation Tool
- created a new application Tool named Follow Tool
- added the "follow" functionality in the Follow Tool and added the new feature of allowing to perform "follow" on an area selection
- fixed bug that inversed mouse cursor movement versus the real movement on Y axis when Grid lines are Off
- updated the language strings
- PEP8 changes and PyCharm suggestions
- in Geometry UI and in Drilling Tool added ability to edit existing Exclusion Areas and change the Strategy and OverZ parameters for each of them
- in Geometry UI and in Drilling Tool added a Delete menu entry in the Exclusion Area Table context menu

11.11.2020

- removed the forcing of multigeo geometry usage when creating CNCJobs
- in Film Tool added messages to warn the user that it can't save a film to file due of existing locked file, if the file needs to overwrite another file who is opened in another application
- in Film Tool warn the user if it tries to save a film to file but the artwork is outside the page size therefore invisible

10.11.2020

- renamed the Extract Drills Tool to Extract Tool and enhanced the tool to extract a Soldermask Gerber from a given Gerber object
- updated the language strings files
- changed the icon for Extract Tool
- in Cutout Tool added the UI for a new feature: Cut by Drilling
- fixed a bug in Extract Tool, when extracting drills some of the drills were lost; added a new UI control to select/deselect all apertures
- updated the Extract Tool - Extract Soldermask functionality, such that the selection of apertures will control the final SolderMask Gerber content
- updated the Extract Tool - new functionality added: Extract Cutout Gerber from a given Gerber object; added parameters in Preferences
- updated the UI for Cutout Tool
- fixed Paint Tcl command; fixes issue #437
- updated the setup_ubuntu.sh script
- finished adding new feature in Cutout Tool: cut a geometry by drilling along its path
- Drilling cut in Cutout Tool now works for geometry with multiple segments

9.11.2020

- some refactoring in signal connections in App.__init__()
- fixed issue with connecting twice the signals for the File Toolbar on the first execution of the program

8.11.2020

- preprocessors are no longer ordered in the comboboxes so I had to sort them manually

7.11.2020

- fixed some issues with not finding the methods when treating the startup arguments
- set the app to "Unstable" status
- fixed an if/else selection in the AppObject.on_object_created() that made the plotting of CNCJob objects to be done twice and second time will disregard the Plot Kind Preference value
- updated the GCode generation section. Now the GCode is generated only once, when a CNCJob object was created. If the user chose to use the GCode Editor the GCode is only updated. If the user chose to include or remove the CNC Code Snippets, only then the GCode is regenerated. This has a higher impact on CNCJobs with very complex and long GCode.
- in GCode Editor added handlers for the Insert Code buttons
- in Project tab, when selecting multiple CNCJob objects, in the context menu the name of the Save entry change to Batch Save and those objects are all saved each in its own file
- added support for shortcut key F2 when in the Project Tab and that will allow the object renaming much faster
- fixed issue with first click on the header of the Tool Table in the Geometry Object Properties and also made sure that clicking the Plot Items in the last column will not select rows
- fixed issue with the Geometry object name, object made from NCC Tool (rest machining), being changed on row clicking in the Geometry Tool Table

7.11.2020

- fixed a small issue in Excellon Editor that reset the delta coordinates on right mouse button click too, which was incorrect. Only left mouse button click should reset the delta coordinates.
- In Gerber Editor upgraded the UI
- in Gerber Editor made sure that trying to add a Circular Pad array with null radius will fail
- in Gerber Editor when the radius is zero the utility geometry is deleted
- in Excellon Editor made sure that trying to add a Circular Drill/Slot array with null radius will fail
- in Excellon Editor when the radius is zero the utility geometry is deleted
- in Gerber Editor fixed an error in the Eraser tool trying to disconnect the Jump signal
- small UI change in the Isolation Tool for the Reference Object selection
- small UI changes in NCC Tool and in Paint Tool for the Reference Object selection
- language strings recompiled to make sure that the .MO files are well optimized
RELEASE 8.994

6.11.2020

- in Gerber Editor made the selection multithreaded in a bid to get more performance but until Shapely will start working on vectorized geometry this don't yield too much improvement
- in Gerber Editor, for selection now the intersection of the click point and the geometry is determined for chunks of the original geometry, each chunk gets done in a separate process
- updated the French translation (by Olivier Cornet)
- fixed the new InputDialog widget to set its passed values in the constructor
- in Gerber Editor fixed the Add circular array capability
- in Gerber Editor remade the utility geometry generation for Circular Pad Array to show the array updated in real time and also fixed the adding of array in negative quadrants
- in Excellon Editor remade the utility geometry generation for Circular Drill/Slot Array to show the array updated in real time and also fixed the adding of array in negative quadrants
- Turkish language strings updated (by Mehmet Kaya)
- both for Excellon and Gerber editor fixed the direction of slots/pads when adding a circular array
- in Gerber editor added the G key shortcut to toggle the grid snapping
- made some changes in the Region Tool from the Gerber Editor

5.11.2020

- fixed the annotation plotting in the CNCJob object
- created a new InputDialog widget that has the buttons and the context menu translated and replaced the old widget throughout the app
- updated the translation strings
- Turkish language strings updated
- set some policy rules back the way they were for the combo boxes in Geometry Object properties
- updated the Italian translation (by Massimiliano Golfetto)
- finished the Google-translation of the German language strings

4.11.2020

- updated all the translation files
- fixed issue with arrays of items could not be added in the Gerber/Excellon Editor when a translation is used
- fixed issue in the Excellon Editor where the Space key did not toggle the direction of the array of drills
- combed the application strings all over the app and trimmed them up until those starting with letter 'O'
- updated the translation strings
- fixed the UI layout in Excellon Editor and made sure that after changing a value in the Notebook side after the mouse is inside the canvas, the canvas takes the focus allowing the key shortcuts to work
- Turkish language strings updated (by Mehmet Kaya)
- in Gerber Editor added the shortcut key 'Space' to change the direction of the array of pads
- updated all the translation languages. Translated by Google the Spanish, Russian. Romanian translation updated.
- refactored the name of the classes from the Gerber Editor
- added more icons in the Gerber and Excellon Editors for the buttons

3.11.2020

- fixed an issue in Tool Isolation used with tools from the Tools Database: the set offset value was not used
- updated the Tools Database to include all the Geometry keys in the every tool from database
- made sure that the Operation Type values ('Iso', 'Rough' and 'Finish') are not translated as this may create issues all over the application
- fix an older issue that made that only the Custom choice created an effect when changing the Offset in the Geometry Object Tool Table
- trying to optimize Gerber Editor selection with the mouse
- optimized some of the strings
- fixed the project context save functionality to work in the new program configuration
- updated Turkish translation (by Mehmet Kaya)
- in NCC and Isolation Tools, the Validity Checking of the tools is now multithreaded when the Check Validity UI control is checked
- translation strings updated
- fixed an error in Gerber parser, when it encounter a pen-up followed by pen-down move while in a region
- trimmed the application strings
- updated the Italian translation (by Massimiliano Golfetto)
- fixed a series of issues in Gerber Editor tools when the user is trying to use the tools by preselecting a aperture without size (aperture macro)
- moved all the UI stuff out of the Gerber Editor class in its own class
- in the Excellon Editor, added shortcut keys Space and Ctrl+Space for toggling the direction of the Slots, respectively for the Array of Slots
- updated the translation strings to the latest changes in the app strings

2.11.2020

- fixed the Tcl Command AlignDrill
- fixed the Tcl Command AlignDrillGrid
- fixed the Tcl COmmand Panelize, Excellon panelization section
- Fixed an issue in Tool Calibration export_excellon method call
- PEP8 corrections all over the app
- made sure that the object selection will not work while in Editors or in the App Tools
- some minor changes to strings and icons
- in Corner Markers Tool - the new Gerber object will have also follow_geometry
- upgraded the Fiducials Tool to create new objects instead of updating in place the source objects
- upgraded the Copper Thieving Tool to create new objects instead of updating in place the source objects
- in Copper Thieving Tool added a new parameter to filter areas too small to be desired in the copper thieving; added it to Preferences too
- Copper Thieving Tool added a new parameter to select what extra geometry to include in the Pattern Plating Mask; added it to the Preferences
- made a wide change on the spinners GUI ranges: from 9999.9999 all values to 10000.0000
- fixed some late issues in Corner Markers Tool new feature (messages)
- upgraded Calculator Tool and added the new parameter is the Preferences
- updated translation strings
- fixed borderline bug in Gerber editor when the edited Gerber object last aperture is a aperture without size (Aperture Macro)
- improved the loading of a Gerber object in the Gerber Editor
- updated translation strings

1.11.2020

- updated the French Translation (by Olivier Cornet)
- fixed issue in Corner Markers Tool that crashed the app if only one corner was checked
- fixed issue in Isolation Tool where Area Isolation selection was not working 
- added to the translatable strings the category labels in the Project Tab and also updated the translations
- fixed a small issue (messages) in Corner Markers Tool
- in Corners Markers Tool added a new feature: possibility to use cross shape markers
- in Corner Marker Tool add new feature: ability to create an Excellon object with drill holes in the corner markes
- in Corner Marker Tool, will no longer update the current object with the marker geometry but create a new Gerber object
- in Join Excellon functionality made sure that the new Combo Exellon object will have copied the data from source objects and not just references, therefore will survive the delete of its parents
- updated Turkish translation (by Mehmet Kaya)
- updated all the languages except Turkish
- in the Tool PDF fixed the creation of Excellon objects to the current Excellon object data structure

31.10.2020

- adapted HPGL importer to work within the new app
- in Gerber Editor fixed an error when using the Distance Tool with "Snap to center" option active: if clicking not on a pad Distance Tool was not working
- updated the Turkish translation strings (by Mehmet Kaya)
- typo fixed in Copper Thieving Tool (due of recent changes)
- fixed issue #457; wrong reference when saving a project
- fixed issue in Excellon Editor that crashed the app if using the Resize Drill feature by clicking in menu/toolbar
- fixed issue in Excellon Editor when using the menu links to Move or Copy Drills/Slots
- updated the strings 
- updated the Turkish translation strings (by Mehmet Kaya)
- added a parent to some of the FCInputDialog widgets used in the app such that those pop-up windows will b displayed in the center of the app main window as opposed to the center of the screen
- finished the Google-translation of not translated strings in Russian language

30.10.2020

- fixed the Punch Gerber Tool bug that did not allowed the projects to be loaded or to create a new project. Fixed issue #456
- in Tool Subtract added an option to delete the source objects after a successful operation. Fixed issue #455
- when entering into an Editor now the Project tab is disabled and the Properties tab where the Editor is installed change the text to 'Editor' and the color is set in Red. After exiting the Tab text is reverted to previous state.
- fixed and issue where the Tab color that was changed in various states of the app was reverted back to a default color 'black'. Now it reverts to whatever color had before therefore being compatible with an usage of black theme
- fixed bug that did not allow joining of any object to a Geometry object
- working on solving the lost triggered signals for the Editor Toolbars buttons after changing the layout
- fixed issue #454; trigger signals for Editor Toolbars lost after changing the layout
- updated the translation strings
- more bugs that were introduced by recent changes done to solve other bugs and so on: fixed issues with the Editors and Delete shortcut
- fixed an error in the Gerber Editor

29.10.2020

- added icons in most application Tools
- updated Punch Gerber Tool such that the aperture table is updated upon clicking of the checboxes in Processed Pads Type
- updated Punch Gerber Tool: the Excellon method now takes into consideration the pads choice 
- minor change for the FCComboBox UI element by setting its size policy as ignored so it will not expand the notebook when the name of one of its items is very long
- added a protection on opening the tools database UI if the tools DB file is not loaded
- fixed NCC Tool not working with the new changes; the check for not having complete isolation is just a Warning
- fixed the sizePolicy for the FCComboBox widgets in the Preferences that holds the preprocessors
- fixed issue with how the preamble / postamble GCode were inserted into the final GCode
- fixed a small issue in GCode Editor where the signals for the buttons were attached again at each launch of the GCode Editor
- fixed issues in the Tools Database due of recent changes in how the data structure is created
- made sure that the right tools go only to the intended use, in Tools Database otherwise an error status message is created and Tools DB is closed on adding a wrong tool
- fixed the usage for Tools Database in Unix-like OS's; fixed issue #453
- done some modest refactoring
- fixed the Search and Add feature in Geometry Object UI
- fixed issue with preamble not being inserted when used alone
- modified the way that the start GCode is stored such that now the bug in GCode Editor that did not allowed selection of the first tool is now solved
- in Punch Gerber Tool added a column in the apertures table that allow marking of the selected aperture so the user can see what apertures are selected
- improvements in the Punch Gerber Tool aperture markings
- improved the Geometry Object functionality in regards of Tools DB, deleting a tool and adding a tool
- when using the 'T' shortcut key with Properties Tab in focus and populated with the properties of a Geometry Object made the popped up spinner to have the value autoselected
- optimized the UI in Extract Drills Tool
- added some more icons for buttons

28.10.2020

- a series of PEP8 corrections in the FlatCAMGeometry.py
- in Geometry UI finished a very basic way for the Polish feature (this will be upgraded in the future, for now is very rough)
- added some new GUI elements by subclassing some widgets for the dialog pop-ups
- in NCC Tool and Isolation Tool, pressing the shortcut key 'T' will bring the add new tool pop up in which now it is included the button to get the optimal diameter
- in Geometry UI and for Solderpaste Tool replaced the pop up window that is launched when using shortcut key with one that has the context menu translated
- some UI cleanup in the Geometry UI
- updated the translation strings except Russian which could be in the works
- fixed an error that did not allowed for the older preferences to be deleted when installing a different version of the software
- in Legacy Mode fixed a small issue: the status bar icon for the Grid axis was not colored on app start
- added a new string to the translatable strings
- fixed an error that sometime showed in Legacy Mode when moving the mouse outside canvas
- reactivated the shortcut key 'S' in TCL Shell, to close the shell dock when it was open (of course the focus has to be not on the command line)
- brought up-to-date and fixed the Tcl Command Drillcncjob and Cncjob
- fixed Tcl command Isolate to not print messages on message bar in case it is run headless
- fixed Tcl command Copper Clear (NCC)
- fixed Tcl command Paint
- temporary fix for comboboxes not finding the the value in the items when setting themselves with a value by defaulting to the first item in the list
- fix in Tool Subtract where there was a typo
- upgraded the punch Gerber Tool
- updated the Turkish translation strings (by Mehmet Kaya)
- fixed an issue in Isolation Tool when running the app in Basic mode;
- fixed Paint, Isolation and NCC Tools such the translated comboboxes values are now stored as indexes instead of translated words as before
- in Geometry Object made sure that the widgets in the Tool Table gets populated regardless of encountering non-recognizable translated values
- in Paint Tool found a small bug and fixed it
- fixed the Tool Subtractor algorithms

27.10.2020

- created custom classes derived from TextEdit and from LineEdit where I overloaded the context menu and I made all the other classes that were inheriting from them to inherit from those new classes
- minor fix in ToolsDB2UI
- updated the Turkish translation strings (by Mehmet Kaya)
- fixed a bug in conversion of any to Gerber in the section of Excellon conversion
- some PEP8 fixes
- fixed a bug due of recent chagnes in FileMenuHandlers class
- fixed an issue in Tools Database (ToolsDB2 class) that did not made the Tab name in Red color when adding/deleting a tool by using the context menu
- optimized the Tools Database
- small string change

26.10.2020

- added a new menu entry and functionality in the View category: enable all non-selected (shortcut key ALT+3)
- fixed shortcut keys for a number of functionality and in some cases added some new
- fixed the enable/disable all plots functionality
- fixed issue with the app window restored in a shifted position after doing Fullscreen
- fixed issue with coords, delta_coords and status toolbars being disabled when entering fullscreen mode and remaining disabled after restore to normal mode
- changed some of the strings (added a few in the How To section)
- more strings updated
- modified the shortcut strings and the way the shortcuts were listed in the Shortcut keys list such that it will allow a future Shortcuts Manager
- updated all the language strings according to the modifications done above
- fixed a small issue with using the shortcut key for toggling the display of Properties tab
- fixed issue with not updating the model view on the model used in the Project tab when using the shortcut keys 1, 2, 3 which made the color of the tree element not reflect the change in status
- minor string fix; removed the 'Close' menu entry on the context menu for the TCL Shell
- overloaded the context menu in the Tcl Shell and added key shortcuts; the menu entries are now translatable
- overloaded the context menu in several classes from GUI Elements such that the menus are now translated
- fixed a formatting issue in the MainGUI.py file
- updated the translations for the new strings that were added
- another GUI element for which I've overloaded the context menu to make it translatable: _ExpandableTextEdit
- overloaded the context menu for FCSpinner and for FCDoubleSpinner
- added new strings and therefore updated the translation strings
- fixed some minor issues when doing a project save

25.10.2020

- updated the Italian translation (by Massimiliano Golfetto)
- finished the update of the Spanish translation (Google translate)

24.10.2020

- added a new GUI element, an InputDialog made out of FCSliderWithSpinner named FCInputDialogSlider
- replaced the InputDialog in the Opacity pop menu for the objects in the Project Tab with a FCInputDialogSlider
- minor changes
- UI changes in the AppTextEditor and in CNCJob properties tab and in GCoe Editor
- some changes in strings; updated all the translation strings to the latest changes
- finished the Romanian translation
- created two new preprocessors (from 'default' and from 'grbl_11') that will have no toolchange commands regardless of the settings in the software
- updated the Turkish translation (by Mehmet Kaya)
- the methods of the APP class that were the handlers for the File menu are now moved to their own class
- fixed some of the Tcl Commands that depended on the methods refactored above
- reverted the preprocessors with no toolchange commands to the original but removed the M6 toolchange command
- fixed newly introduced issue when doing File -> Print(PDF)
- fixed newly introduced issues with SysTray and Splash
- added ability for the app to detect the current DPI used on the screen; applied this information in the Film Tool when exporting PNG files
- found that Pillow v >= 7.2 breaks Reportlab 3.5.53 (latest version) and creates an error in Film Tool when exporting PNG files. Pillow 7.2 still works.

23.10.2020

- updated Copper Thieving Tool to work with the updated program
- updated Rules Check Tool - Hole Size rule to work with the new data structure for the Excellon objects
- updated Rules Check Tool - added an activity message
- updated some strings, updated the translation strings
- commented the ToolsDB class since it is not used currently
- some minor changes in the AppTextEditor.py file
- removed Hungarian language since it's looking like is no longer being translated
- added a default properties tab which will hold a set of information's about the application
- minor changes in the Properties Tool
- Excellon UI: fixed a small issue with toggling all rows in Tools Table not toggling off and also the milling section in Utilities was not updated
- some refactoring in the keys of the defaults dictionary
- fixed an ambiguity in the Tools Database GUI elements

22.10.2020

- added  a message to show if voronoi_diagram method can be used (require Shapely >= 1.8)
- modified behind the scene the UI for Tool Subtract
- modified some strings and updated the translation strings
- in NCC Tool added a check for the validity of the used tools; its only informative
- in NCC Tool done some refactoring
- in NCC Tool fixed a bug when using Rest Machining; optimizations
- in NCC Tool fixed a UI issue
- updated the Turkish translation (by Mehmet Kaya)
- small change in the CNCJob UI by replacing the AL checkbox with a checkable QButton
- disabled the Autolevelling feature in CNCJob due of being not finished and missing also a not-yet-released package: Shapely v 1.8
- added some new strings for translation and updated the translation strings
- in ToolsDB2UI class made the vertical layouts have a preferred minimum dimension as opposed to the previous fixed one
- in Geometry Object made sure that the Tools Table second column is set to Resize to contents
- fixed a bug in Tool PunchGerber when using an Excellon to punch holes in the Gerber apertures

21.10.2020

- in Geometry Object fixed the issue with not using the End X-Y value and also made some other updates here
- in NCC and Paint Tool fixed some issues with missing keys in the tool data dictionary
- In Excellon Object UI fixed the enable/disable for the Milling section according to the Tools Table row that is selected
- In Excellon Object UI fixed the milling geometry generation
- updated the translations strings to the changes in the source code
- some strings changed
- made the Properties checkbox in the Object UI into a checkable button and added to it an icon
- fixed crash on using shortcut for creating a new Document Object
- fixed Cutout Tool to work with the endxy parameter
- added the exclusion parameters for Drilling Tool to the Preferences area
- cascaded_union() method will be deprecated in Shapely 1.8 in favor of unary_union; replaced the usage of cascaded_union with unary_union in all the app
- added some strings to the translatable strings and updated the translation strings
- merged in the Autolevelling branch and made some PEP8 changes to the bilinearInterpolator.py file
- added a button in Gerber UI that will hide/show the bounding box generation and the non-copper region section
- compacted the UI for the 2Sided Tool
- added a button in Excellon UI that will hide/show the milling section
- optimized a bit the UI for Gerber/Excellon/Geometry objects
- optimized FlatCAMObj.add_properties_items() method
- updated the Turkish translation (by Mehmet Kaya)
- added ability to run a callback function with callback_parameters after a new FlatCAM object is created

20.10.2020

- finished to add the Properties data to the Object Properties (former Selected Tab)

19.10.2020

- added a check (and added to Preferences too) for the verification of tools validity in the Isolation Tool
- fixed QrCode Tool
- updated the Turkish translation (by Mehmet Kaya)

18.10.2020

- fixed issue with calling the inform signal in the AppDefaults.load method
- fixed macro parsing in Gerber files generated by KiCAD 4.99 (KiCAD 5.0)

17.10.2020

- updated Turkish translation (by Mehmet Kaya)

8.10.2020

- small change in the NCC Tool UI
- some strings are changed and therefore the translation strings source are updated
- Isolation Tool - added a check for having a complete isolation

7.10.2020

- working on adding DPI setting for PNG export in Film Tool - update
- finished updating DPI setting feature for PNG export in Film Tool

5.10.2020

- working on adding DPI setting for PNG export in the Film Tool
- finished working in adding DPI settings for PNG export in Film Tool although there are some limitations due of Reportlab
- small change in TclCommandExportSVG

26.09.2020

- the Selected Tab is now Properties Tab for FlatCAM objects
- modified the Properties Tab for various FlatCAM objects preparing the move of Properties Tool data into the Properties Tab
- if the Properties tab is in focus (selected) when a new object is created then it is automatically selected therefore it's properties will be populated

25.09.2020

- minor GUI change in Isolation Tool

24.09.2020

- fixed a bug where end_xy parameter in Drilling Tool was not used
- fixed an issue in Delete All method in the appMain.py

23.09.2020

- added support for virtual units in SVG parser; warning: it may require the support for units which is not implemented yet
- fixed canvas selection such that when selecting shape fails to be displayed with rounded corners a square selection shape is used
- fixed canvas selection for the case when the selected object is a single line or a line made from multiple segments

22.09.2020

- fixed an error in importing SVG that has a single line
- updated the POT file and the PO/MO files for Turkish language
- working to add virtual units to SVG parser

20.09.2020

- in CNCJob UI Autolevelling: on manual add of probe points, only voronoi diagram is calculated
- in SVG parser: made sure that the minimum number of steps to approximate an arc/circle/bezier is 10

19.09.2020

- removed some brackets in the GRBL laser preprocessor due of GRBL firmware interpreting the first closing bracket as the comment end

3.09.2020

- in CNCJob UI Autolevelling: changed the UI a bit
- added a bilinear interpolation calculation class from: https://github.com/pmav99/interpolation
- in CNCJob UI Autolevelling: made sure that the grid can't have less than 2 rows and 2 columns when using the bilinear interpolation or 1 row and 1 column when using the Voronoi polygons
- in CNCJob UI Autolevelling: prepared the app for bilinear interpolation
- in CNCJob UI Autolevelling: fixes in the UI

2.09.2020

- in CNCJob UI Autolevelling: solved some small errors: when manual adding probe points dragging the mouse with left button pressed created selection rectangles; detection of click inside the solid geometry was failing
- in CNCJob UI Autolevelling: in manual adding of probe points make sure you always add a first probe point in origin
- in CNCJob UI Autolevelling: first added point when manual adding of probe points is auto added in origin before adding first point
- in CNCJob UI Autolevelling: temp geo for adding points in manual mode is now painted in solid black color and with a smaller diameter
- in CNCJob UI Autolevelling - GRBL controller - added a way to save a GRBL height map
- in CNCJob UI Autolevelling: added the UI for choosing the method used for the interpolation used in autolevelling

31.08.2020

- updated the Italian translation files by Massimiliano Golfetto
- in CNCJob UI Autolevelling: made sure that plotting a Voronoi polygon is done only for non-None polygons
- in CNCJob UI Autolevelling: in manual mode, Points can be chosen only when clicking inside the object to be probed
- in CNCJob UI Autolevelling: made sure that plotting a Voronoi polygon is done only for non-None polygons
- in CNCJob UI Autolevelling: remade the probing points generation so they could allow bilinear interpolation

29.08.2020

- 2Sided Tool - fixed newly introduced issues in the Alignment section
- 2Sided Tool - modified the UI such that some of the fields will allow only numbers and some special characters ([,],(,),/,*,,,+,-,%)
- Cutout Tool - working on adding mouse bites for the Freeform cutout
- updated the translation files to the current state of the app
- Cutout Tool - rectangular and freeform cutouts are done in a threaded way
- Cutout Tool - added the Mouse Bites feature for the Rectangular and Freeform cutouts and right now it fails in case of using a Geometry object and Freeform cutout (weird result)
- some changes in camlib due of warnings for future changes in Shapely 2.0
- Cutout Tool - fixed mouse bites feature in case of using a Geometry object and Freeform cutout
- Cutout Tool - can do cutouts on multigeo Geometry objects: it will automatically select the geometry of first tool
- Geometry Editor - fixed exception raised when trying to move and there is no shape to move
- Cutout Tool - finished adding the Mouse Bites feature by adding mouse bites for manual cutouts

28.08.2020

- Paint Tool - upgraded the UI and added the functionality that now adding a new tool is done by first searching the Tools DB for a suitable tool and if fails then it adds an default tool
- Paint Tool - on start will attempt to search in the Tools DB for the default tools and if found will load them from the DB
- NCC Tool - upgraded the UI and added the functionality that now adding a new tool is done by first searching the Tools DB for a suitable tool and if fails then it adds an default tool
- NCC Tool - on start will attempt to search in the Tools DB for the default tools and if found will load them from the DB
- fixes in NCC, Paint and Isolation Tool due of recent changes
- modified the Tools Database and Preferences with the new parameters from CutOut Tool
- changes in Tool Cutout: now on Cutout Tool start the app will look into Tools Database and search for a tool with same diameter (or within the set tolerance) as the one from Preferences and load it if found or load a default tool if not
- Tool Cutout - this Tool can now load tools from Tools Database through buttons in the Cutout Tool

27.08.2020

- fixed the Tcl commands AddCircle, AddPolygon, AddPolyline and AddRectangle to have stored bounds therefore making them movable/selectable on canvas
- in Tool Cutout, when using the Thin Gaps feature, the resulting geometry loose the extra color by toggling tool plot in Geometry UI Tools Table- fixed
- in Tool Cutout fixed manual adding of gaps with thin gaps and plotting
- in Tool Cutout, when using fix gaps made sure that this feature is not activated if the value is zero
- in Tool Cutout: modified the UI in preparation for adding the Mouse Bites feature
- Turkish translation strings were updated by the translator, Mehmet Kaya
- Film Tool - moved the Tool UI in its own class
- in Tools: Image, InvertGerber, Optimal, PcbWizard - moved the Tool UI in its own class
- Tool Isolation - made sure that the app can load from Tools Database only tools marked for Isolation tool
- Tool Isolation - on Tool start it will attempt to load the Preferences set tools by diameter from Tools Database. If it can't find one there it will add a default tool.
- in Tools: Transform, SUb, RulesCheck, ObjectDistance, Distance - moved the Tool UI in its own class
- some small fixes
- fixed a borderline issue in CNCJob UI Autolevelling - Voronoi polygons calculations

26.08.2020

- fix for issue nr 2 in case of Drilling Tool. Need to check Isolation Tool, Paint Tool, NCC Tool
- Drilling Tool - UI changes
- Geometry object - now plotting color for an individual tool can be specified
- in CutOut Tool - when using  'thin gaps' option then the cut parts are colored differently than the rest of the geometry in the Geometry object
- solved some deprecation warnings (Shapely module)
- Drilling Tool - when replacing Tools if more than one tool for one diameter is found, the application exit the process and display an error in status bar; some minor fixes
- Isolation Tool - remade the UI
- Isolation Tool - modified the add new tool method to search first in Tools Database  for a suitable tool
- Isolation Tool - added ability to find the tool diameter that will guarantee total isolation of the currently selected Gerber object
- NCC Tool - UI change: if the operation is Isolation then some of the tool parameters are disabled
- fixed issue when plotting a CNCJob object with multiple tools and annotations on by plotting annotations after all the tools geometries are plotted
- fixed crash in Properties Tool, when applied on a CNCJob object made out of an Excellon object (fixed issue #444)
- in Properties Tool, for CNCJob objects made out of Excellon objects, added the information's from Tool Data
- in Properties Tool made sure that the set color for the Tree parents depends on the fact if the gray icons set are used (dark theme) or not
- Properties Tool - properties for a Gerber objects has the Tool Data now at the end of the information's
- in Gerber UI done some optimizations

25.08.2020

- in CNCJob UI Autolevelling - made the Voronoi calculations work even in the scenarios that previously did not work; it need a newer version of Shapely, currently I installed the GIT version
- in CNCJob UI Autolevelling - Voronoi polygons are now plotted
- in CNCJob UI Autolevelling - adding manual probe points now show some geometry (circles) for the added points until the adding is finished
- 2Sided Tool - finished the feature that allows picking an Excellon drill hole center as a Point mirror reference
- Tool Align Objects - moved the Tool Ui into its own class
- for Tools: Calculators, Calibration, Copper Thieving, Corners, Fiducials - moved the Tool UI in its own class

24.08.2020

- fixed issues in units conversion
- in CNCJob UI Autolevelling - changed how the probing code is generated and when
- changed some strings in CNCJob UI Autolevelling
- made sure that when doing units conversion keep only the decimals specified in the application decimals setting (should differentiate between values and display?)
- in CNCJob UI Autolevelling - some UI changes
- in CNCJob UI Autolevelling - GRBL controller - added the probing method
- in CNCJob UI Autolevelling - GRBL controller - fixed the send_grbl_command() method

23.08.2020

- in CNCJob UI Autolevelling - autolevelling is made to be not available for cnc code generated with Roland or HPGL preprocessors
- in CNCJob UI Autolevelling - added a save dialog for the probing GCode
- added a new GUI element, a DoubleSlider
- in CNCJob UI Autolevelling - GRBL controller - Control: trying to add DoubleSlider + DoubleSpinner combo controls
- in GUI element FCDoubleSpinner fixed an range issue

21.08.2020

- in CNCJob UI Autolevelling - GRBL controller - Control: added a Origin button; changed the UI to have rounded rectangles 
- in CNCJob UI Autolevelling - GRBL controller - Control: added feedrate and step size controls and added them in Preferences
- in CNCJob UI Autolevelling - GRBL controller - added handlers for the Zeroing and for Homing and for Pause/Resume; some UI optimizations

19.08.2020

- in CNCJob UI Autolevelling - sending GCode/GRBL commands is now threaded
- in CNCJob UI Autolevelling - Grbl Connect tab colors will change with the connection status
- in CNCJob UI Autolevelling - GRBL Control and Sender tabs are disabled when the serial port is disconnected
- in CNCJob UI Autolevelling - GRBL Sender - now only a single command can be sent 
- in CNCJob UI Autolevelling - GRBL controller - changed the UI
- in CNCJob UI Autolevelling - added some VOronoi poly calculations

18.08.2020

- in Doublesided Tool added some UI for Excellon hole snapping
- in Doublesided Tool cleaned up the UI
- in CNCJob UI Autolevelling - in COntrol section added  buttons for Jog an individual axes zeroing
- in CNCJob UI Autolevelling - added handlers for: jogging, reset, sending commands
- in CNCJob UI Autolevelling - added handlers for GRBL report and for getting GRBL parameters

17.08.2020

- in CNCJob UI Autolevelling - GRBL GUI controls are now organized in a tab widget

16.08.2020

- in CNCJob UI Autolevelling - updated the UI with controls for probing GCode parameters and added signals and slots for the UI
- in CNCJob UI Autolevelling - added a mini gcode sender for the GRBL to be able to send the probing GCode and get the height map (I may make a small and light app for that so it does not need to have FlatCAM on the GCode sender PC)
- in CNCJob UI Autolevelling finished the probing GCode generation for MACH/LinuxCNC controllers; this GCode can also be viewed
- in CNCJob UI Autolevelling - Probing GCode has now a header
- in CNCJob UI Autolevelling - Added entries in Preferences
- in CNCJob UI Autolevelling - finished the Import Height Map method
- in CNCJob UI Autolevelling - made autolevelling checkbox state persistent between app restarts

14.08.2020

- in CNCJob UI worked on the UI for the Autolevelling
- in CNCJob UI finished working on adding test points in Grid mode
- in CNCJob UI finished working on adding test points in Manual mode

13.08.2020

- in CNCJob UI added GUI for an eventual Autolevelling feature 
- in CNCJob UI updated the GUI for Autolevelling
- Cutout Tool - finished handler for gaps thickness control for the manual gaps
- CNCJob object - working in generating Voronoi diagram for autolevelling

11.08.2020

- CutOut Tool - finished handler for gaps thickness control for the free form cutout

9.08.2020

- small fix so the cx_freeze 6.2 module will work in building a frozen version of FlatCAM

7.08.2020

- all Geometry objects resulted from Isolation Tool are now of type multi-geo
- fixed minor glitch in the Isolation Tool UI
- added an extra check when doing selection on canvas
- fixed an UI problem in Gerber Editor

5.08.2020

- Tool Cutout - more work in gaps thickness control feature
- Tool Cutout - added some icons to buttons
- Tool Cutout - done handling the gaps thickness control for the rectangular cutout; TODO: check all app for the usage of geometry_spindledir and geometry_optimization_type defaults in tools and in options
- Tool Cutout - some work in gaps thickness control for the free form cutout

4.08.2020

- removed the Toolchange Macro feature (in the future it will be replaced by full preprocessor customization)
- modified GUI in Preferences
- Tool Cutout - working in adding gaps thickness control feature; added the UI in the Tool

3.08.2020

- GCode Editor - GCode tool selection when clicking on tool in Tools table is working. The only issue is that the first tool gcode includes the start gcode which confuse the algorithm
- GCode Editor - can not delete objects while in the Editor; can not close the Code Editor Tab except on Editor exit; activated the shortcut keys (for now only CTRL+S is working)
- added a way to remember the old state of Tools toolbar before and after entering an Editor
- GCode Editor - modified the UI

2.08.2020

- GCode Editor - closing the Editor will close also the Code Editor Tab
- cleanup of the CNCJob UI; added a checkbox to signal if any append/prepend gcode was set in Preferences (unchecking it will override and disable the usage of the append/prepend GCode)
- the start Gcode is now stored in the CNCJob object attribute gc_start
- GCode Editor - finished adding the ability to select a row in the Tools table and select the related GCode
- GCode Editor - working on GCode tool selection - not OK

1.08.2020

- Tools Database: added a Cutout Tool Parameters section
- GCode Editor - work in the UI

31.07.2020

- minor work in GCode Editor

29.07.2020

- fixed an exception that was raised in Geometry object when using an Offset

27.07.2020

- Gerber parser - a single move with pen up D2 followed by a pen down D1 at the same location is now treated as a Flash; fixed issue #441

25.07.2020

- Tools Tab is hidden when entering into a Editor and showed on exit (this needs to be remade such that the toolbars state should be restored to whatever it was before entering in the Editor)

22.07.2020

- working on a proper GCode Editor
- wip in the GCode Editor
- added a Laser preprocessor named 'Z_laser' which will change the Z to the Travel Z on each ToolChange event allowing therefore control of the dot size
- by default now a new blank Geometry object created by FlatCAM is of type multigeo
- made sure that optimizations of lines when importing SVG or DXF as lines will not encounter polygons but only LinesStrings or LinearRings, otherwise having crashes
- fixed the import SVG and import DXF, when importing as Geometry to be imported as multigeo tool
- fixed the import SVG and import DXF, the source files will be saved as loaded into the source_file attribute of the resulting object (be it Geometry or Gerber)
- in import SVG and import DXF methods made sure that any polygons that are imported as polygons will survive and only the lines are optimized (changed the behavior of the above made modification)

21.07.2020

- updated the FCRadio class with a method that allow disabling certain options
- the Path optimization options for Excellon and Geometry objects are now available depending on the OS platform used (32bit vs 64bit)
- fixed MultiColor checkbox in Excellon Object to work in Legacy Mode (2D)
- modified the visibility change in Excellon UI to no longer do plot() when doing visibility toggle for one of the tools but only a visibility change in the shapes properties
- Excellon UI in Legacy Mode (2D): fixed the Solid checkbox functionality
- Excellon UI: fixed plot checkbox performing an extra plot function which was not required
- Excellon UI: added a column which will color each row/tool of that column in the color used when checking Multicolor checkbox
- Excellon UI: made sure that when the Multicolor checkbox is unchecked, the color is updated in the Color column of the tools table
- made sure that the Preferences files are deleted on new version install, while the application is in Beta status
- fixed issues with detecting older Preferences files
- fixed some issues in Excellon Editor due of recent changes
- moved the Gerber colors fill in the AppObject.on_object_created() slot and fixed some minor issues here
- made sure there are no issues when plotting the Excellon object in one thread and trying to build the UI in another by using a signal

20.07.2020

- fixed a bug in the FlatCAMGerber.on_mark_cb_click_table() method when moving a Gerber object
- added a way to remember the colors set for the Gerber objects; it will remember the order that they were loaded and set a color previously given
- added a control in Preferences -> Gerber Tab for Gerber colors storage usage
- made sure that the defaults on first install will set the number of workers to half the number of CPU's on the system but no less than 2

18.07.2020

- added some icons in the Code Editor
- replaced some icons in the app
- in Code Editor, when changing text, the Save Code button will change color (text and icon) to red and after save it will revert the color to the default one
- in Code Editor some methods rework

16.07.2020

- added a new method for GCode generation for Geometry objects
- added multiple algorithms for path optimization when generating GCode from an Geometry object beside the original Rtree algorithm: TSA, OR-Tools Basic, OR-Tools metaheuristics
- added controls for Geometry object path optimization in Preferences

15.07.2020

- added icons to some of the push buttons
- Tool Drilling - automatically switch to the Selected Tab after job finished
- added Editor Push buttons in Geometry and CNCJob UI's
- Tool Drilling - brushing through code and solved the report on estimation of execution time
- Tool Drilling - more optimizations regarding of using Toolchange as opposed to not using it
- modified the preprocessors to work with the new properties for Excellon objects
- added to preprocessors information regarding the X,Y position at the end of the job
- Tool Drilling made sure that on Toolchange event after toolchange event the tool feedrate is set
- added some icons to more push buttons inside the app
- a change of layout in Tools Database
- a new icon for Search in DB

14.07.2020

- Drilling Tool - now there is an Excellon preference that control the autoload of tools from the Tools Database
- Tools Database - remade the UI
- made sure that the serializable attributes are added correctly and only once (self.ser_attrs)
- Tools Database - some fixes in the UI (some of the widgets had duplicated names)
- Tools Database - made sure the on save the tools are saved only with the properties that relate to their targeted area of the app
- Tools Database - changes can be done only for one tool at a time
- Tool Database - more changes to the UI

13.07.2020

- fixed a bug in Tools Database: due of not disconnecting the signals it created a race that was concluded into a RuntimeError exception (an dict changed size during iteration)
- Drilling Tool - working in adding tools auto-load from Tools DB
- some updates to the Excellon Object options
- Drilling Tool - manual add from Tools DB is working
- Drilling Tool - now slots are converted to drills if the checkbox is ON for the tool investigated
- Drilling Tool - fixes due of changes in properties (preferences)
- fixed the Drillcncjob TCL command
- Multiple Tools fix - fixed issue with converting slots to drills selection being cleared when toggling all rows by clicking on the header
- Multiple Tools fix - fixes for when having multiple tools selected which created issues in tool tables for many tools

12.07.2020

- when creating a new FlatCAM object, the options will be updated with FlatCAM tools properties that relate to them
- updated the Tools DB class by separating the Tools DB UI into it's own class
- Tools DB - added the parameters for Drilling Tool

11.07.2020

- moved all Excellon Advanced Preferences to Drilling Tool Preferences
- updated Drilling Tool to use the new settings
- updated the Excellon Editor: the default_data dict is populated now on Editor entry
- Excellon Editor: added a new functionality: conversion of slots to drills
- Excellon UI: added a new feature that is grouped in Advanced Settings: a toggle tools table visibility checkbox 
- Drilling Tool - minor fixes
- Drilling Tool - changes in UI
- Isolation Tool - modified the UI; preparing to add new feature of polishing at the end of the milling job
- Tool Paint - fixed an issue when launching the tool and an object other than Geometry or Excellon is selected
- Geometry UI - moved the UI for polishing from Isolation Tool to Geometry UI (actually in the future Milling Tool) where it belongs
- Gerber UI - optimized the mark shapes to use only one ShapeCollection

10.07.2020

- Tool Drilling - moved some of the Excellon Preferences related to drilling operation to it's own group Drilling Tool Options
- optimized the CNCJob UI to look like other parts of the app 
- in Gerber and Excellon UI added buttons to start the Editor
- in all Editors Selected Tab added a button to Exit the Editor
- Tool Drilling - fixed incorrect annotations in CNCJob objects generated; one drawback is that now each tool (when Toolchange is ON) has it's own annotation order which lead to overlapping in the start point of one tool and the end of previous tool
- Tool Drilling - refactoring methods and optimizations

9.07.2020

- Tool Drilling - remade the methods used to generate GCode from Excellon, to parse the GCode. Now the GCode and GCode_parsed are stored individually for each tool and also they are plotted individually
- Tool Drilling now works - I still need to add the method for converting slots to drill holes
- CNCJob object - now it is possible for CNCJob objects originated from Excellon objects, to toggle the plot for a selection of tools
- working in cleaning up the Excellon UI (Selected Tab)
- finished the clean-up in Excellon UI
- Tool Drilling - added new feature to drill the slots

8.07.2020

- Tool Drilling - working on the UI
- Tool Drilling - added more tool parameters; laying the ground for adding "Drilling Slots" feature
- added as ToolTip for the the Preprocessor combobox items, the actual name of the items
- working on Tool Drilling - remaking the way that the GCode is stored, each tool will store it's own GCode
- working on Tool Drilling

7.07.2020

- updated the Panelize Tool to save the source code for the panelized Excellon objects so it can be saved from the Save project tab context menu entry
- updated the Panelize Tool to save the source code for the panelized Geometry objects as DXF file
- fixed the Panelize Tool so the box object stay as selected on new objects are loaded; any selection shape on canvas is deleted when clicking Panelize

6.07.2020

- Convert Any to Excellon. Finished Gerber object conversion to Excellon. Flash's are converted to drills. Traces in the form of a linear LineString (no changes in direction) are converted to slots.
- Turkish translation updated by Mehmet Kaya for the 8.993 version of strings

2.07.2020

- trying to optimize the resulting geometry in DXF import (and in SVG import) by merging contiguous lines; reduced the lines to about one third of the original
- fixed importing DXF file as Gerber method such that now the resulting Gerber object is correctly created having the geometry attributes like self.apertures and self.follow_geometry
- added Turkish translation - courtesy of Mehmet Kaya
- modified the Gerber export method to take care of the situation where the exported Gerber file is a SVG/DXF file imported as Gerber
- working in making a new functionality: Convert Any to Excellon. Finished Geometry object conversion to Excellon.

30.06.2020

- fixed the SVG parser so the SVG files with no information regarding the 'height' can be opened in FlatCAM; fixed issue #433

29.06.2020

- fixed the DXF parser to work with the latest version of ezdxf module (issues for the ellipse entity and modified attribute name for the knots_values to knots)
- fixed the DXF parser to parse correctly the b-splines by not adding automatically a knot value 0f (0, 0) when the spline is not closed

27.06.2020

- Drilling Tool - UI is working as expected; I will have to propagate the changes to other tools too, to increase likeness between different parts of the app

25.06.2020

- made sure that when trying to view the source but no object is selected, the messages are correct
- wip for Tool Drilling

23.06.2020

- working on Tool Drilling

21.06.2020

- wip

18.06.2020

- fixed bug in the Cutout Tool that did not allowed the manual cutous to be added on a Geometry created in the Tool
- fixed bug in Cutout Tool that made the selection box show in the stage of adding manual gaps
- updated Cutout Tool UI
- Cutout Tool - in manual gap adding there is now an option to automatically turn on the big cursor which could help
- Cutout Tool - fixed errors when trying to add a manual gap without having a geometry object selected in the combobox
- Cutout Tool - made sure that all the paths generated by this tool are contiguous which means that two lines that meet at one end will become only one line therefore reducing unnecessary Z moves
- Panelize Tool - added a new option for the panels of type Geometry named Path Optimization. If the checkbox is checked then all the LineStrings that are overlapped in the resulting multigeo Geometry panel object will keep only one of the paths thus minimizing the tool cuts.
- Panelize Tool - fixed to work for panelizing Excellon objects with the new data structure storing drills and tools in the obj.tools dictionary
- put the bases for a new Tool: Milling Holes Tool

17.06.2020

- added the multi-save capability if multiple CNCJob objects are selected in Project tab but only if all are of type CNCJob
- added fuse tools control in Preferences UI for the Excellon objects: if checked the app will try to see if there are tools with same diameter and merge the drills for those tools; if not the tools will just be added to the new combined Excellon
- modified generate_from_excellon_by_tool() method in camlib.CNCJob() such that when Toolchange option is False, since the drills will be drilled with one tool only, all tools will be optimized together

16.06.2020

- changed the data structure for the Excellon object; modified the Excellon parser and the Excellon object class
- fixed partially the Excellon Editor to work with the new data structure
- fixed Excellon export to work with the new data structure
- fixed all transformations in the Excellon object attributes; still need to fix the App Tools that creates or use Excellon objects
- fixed some problems (typos, missing data) generated by latest changes
- more typos fixed in Excellon parser, slots processing
- fixed Extract Drills Tool to work with the new Excellon data format
- minor fix in App Tools that were updated to have UI in a separate class
- Tool Punch Gerber - updated the UI
- Tool Panelize - updated the UI
- Tool Extract Drills - updated the UI
- Tool QRcode - updated the UI
- Tool SolderPaste - updated the UI
- Tool DblSided - updated the UI

15.06.2020

- in Paint Tool and NCC Tool updated the way the selected tools were processed and made sure that the Tools Table rows are counted only once in the processing
- modified the UI in Paint Tool such that in case of using rest machining the offset will apply for all tools
- Paint Tool - made the rest machining function for the paint single polygon method
- Paint Tool - refurbished the 'rest machining' for the entire tool
- Isolation Tool - fixed to work with selection of tools in the Tool Table (previously it always used all the tools in the Tool Table)
- Tools Database - added a context menu action to Save the changes to the database even if it's not in the Administration mode
- Tool Isolation - fixed a UI minor issue: 'forced rest' checkbox state at startup was always enabled
- started working in moving the Excellon drilling in its own Application Tool
- created a new App Tool named Drilling Tool where I will move the drilling out of the Excellon UI
- working on the Drilling Tool - started to create a new data structure that will hold the Excellon object data

14.06.2020

- made sure that clicking the icons in the status bar works only for the left mouse click
- if clicking the activity icon in the status bar and there is no object selected then the effect will be a plot_all with fit_view
- modified the FCLabel GUI element
- NCC Tool - remade and optimized the copper clearing with rest machining: now it works as expected with a reasonable performance
- fixed issue #428 - Cutout Tool -> Freeform geometry was not generated due of trying to get the bounds of the solid_geometry before it was available
- NCC Tool - now the tools can be reordered (if the order UI radio is set to 'no')
- remade the UI in Paint Tool and the tools in tools table ca now be reordered (if the order UI radio is set to 'no')
- some updates in NCC Tool using code from Paint Tool
- in Paint and NCC Tools made sure that using the key ESCAPE to cancel the tool will not create mouse events issues
- some updates in Tcl commands Paint and CopperClear data dicts
- modified the Isolation Tool UI: now the tools can be reordered (if the order UI radio is set to 'no')
- modified the Paint, NCC and Isolation Tools that when no tools is selected in the Tools Table, a message will show that no Tool is selected and the Geometry generation button is disabled

13.06.2020

- modified the Tools Database such that there is now a way to mark a tool as meant to be used in a certain part of the application; it will disable or enable parts of the parameters of the tool
- updated the FCTable GUI element to work correctly when doing drag&drop for the rows
- updated the Geometry UI to work with the new FCTable
- made the coordinates / delta coordinates / grid toolbar / actions toolbar visibility an option, controlled from the infobar (Status bar) context menu. How it's at app shutdown it's restored at the next application start
- moved the init of activity view in the MainGUI file from the APP.__init__()
- added a new string in the tooltip for the button that adds tool from database specifying the tools database administration is done in the menu
- when opening a new tab in the PlotTabArea the coordinates toolbars will be hidden and shown after the tab is closed

12.06.2020

- NCC Tool optimization - moved the UI in its own class
- NCC Tool optimization - optimized the Tool edit method
- NCC Tool - allow no tool at NCC Tool start (the Preferences have no tool)
- NCC Tool - optimized tool reset code
- NCC Tool - fixed the non-rest copper clearing to work as expected: each tool in the tool table will make it's own copper clearing without interference from the rest of the tools 
- Geometry UI - made again the header clickable and first click selects all rows, second click will deselect all rows.
- Geometry UI - minor updates in the layout; moved the warning text to the tooltip of the generate_cncjob button
- Geometry UI - working in making the modification of tool parameters such that if there is a selection of tools the modification in the Tool parameters will be applied to all selected

11.06.2020

- finished tool reordering in Geometry UI

10.06.2020

- fixed bug in the Isolation Tool that in certain cases an empty geometry was present in the solid_geometry which mae the CNCJob object generation to fail. It happen for Gerber objects created in the Gerber Editor
- working on the tool reordering in the Geometry UI
- continue - work in tool reordering in Geometry UI

9.06.2020

- fixed a possible problem in generating bounds value for a solid_geometry that have empty geo elements
- added ability to merge tools when merging Geometry objects if they share the same attributes like: diameter, tool_type or type
- added a control in Edit -> Preferences -> Geometry to control if to merge/fuse tools during Geometry merging

8.06.2020

- minor changes in the way that the tools are installed and connected
- renamed the GeoEditor class/file to AppGeoEditor from FlatCAMGeoEditor making it easier to see in the IDE tree structure
- some refactoring that lead to a working solution when using the Python 3.8 + PyQt 5.15
- more refactoring in the app Editors
- added a protection when trying to edit a Geometry object that have multiple tools but no tool is selected

7.06.2020

- refactoring in camlib.py. Made sure that some conditions are met, if some of the parameters are None then return failure. Modifications in generate_from_geometry_2 and generate_from_multitool_geometry methods
- fixed issue with trying to access GUI from different threads by adding a new signal for printing to shell messages
- fixed a small issue in Gerber file opener filter that did not see the *.TOP extension or *.outline extension
- in Excellon parser added a way to "guestimate" the units if no units are detected in the header. I may need to make it optional in Preferences
- changed the Excellon defaults for zeros suppression to TZ (assumed that most Excellon without units in header will come out of older Eagle) and the Excellon export default is now with coordinates in decimal
- made sure that the message that exclusion areas are deleted is displayed only if there are shapes in the exclusion areas storage
- fixed bug: on first ever usage of FlatCAM beta the last loaded language (alphabetically) is used instead of English (in current state is Russian)
- made sure the the GUI settings are cleared on each new install
- added a new signal that is triggered by change in visibility for the Shell Dock and will change the status of the shell label in the status bar. In this way the label will really be changed each time the shell is toggled
- optimized the GUI in Film Tool
- optimized GUI in Alignment Tool

6.06.2020

- NCC Tool - added a message to warn the user that he needs at least one tool with clearing operation
- added a GUI element in the Preferences to control the possibility to edit with mouse cursor objects in the Project Tab. It is named: "Allow Edit"

5.06.2020

- fixed a small issue in the Panelization Tool that blocked the usage of a Geometry object as panelization reference
- in Tool Calculators fixed an application crash if the user typed letters instead of numbers in the boxes. Now the boxes accept only numbers, dots, comma, spaces and arithmetic operators
- NumericalEvalEntry allow the input of commas now
- Tool Calculators: allowed comma to be used as decimal separator
- changed how the import of svg.path module is done in the ParseSVG.py file
- Tool Isolation - new feature that allow to isolate interiors of polygons (holes in polygons). It is possible that the isolation to be reported as successful (internal limitations) but some interiors to not be isolated. This way the user get to fix the isolation by doing an extra isolation.
- added mouse events disconnect in the quit_application() method
- remade the ReadMe tab
- Tool Isolation - added a GUI element to control if the isolation of a polygon, when done with rest, should be done with the current tool even if its interiors (holes in it) could not be isolated or to be left for the next tool
- updated all the translation strings to the latest changes
- small fix
- fixed the color set for the application objects
- made some reverts regarding the mods in the quit_application() method - problems when freezed
RELEASE 8.993

4.06.2020

- improved the Isolation Tool - rest machining: test if the isolated polygon has interiors (holes) and if those can't be isolated too then mark the polygon as a rest geometry to be isolated with the next tool and so on
- updated the French translation strings - from @micmac (Michel Maciejewski)

3.06.2020

- updated Transform Tool to have a selection of possible references for the transformations that are now selectable in the GUI
- Transform Tool - compacted the UI
- minor issue in Paint Tool
- added a new feature for Gerber parsing: if the NO buffering is chosen in the Gerber Advanced Preferences there is now a checkbox to activate delayed buffering which will do the buffering in background allowing the user to work in between. I hope that this can be useful in case of large Gerber files.
- made the delayed Gerber buffering to use multiprocessing but I see not much performance increase
- made sure that the status bar label for preferences is updated also when the Preferences Tab is opened from the Edit -> Preferences
- remade file names in the app
- fixed the issue with factory_defaults being saved every time the app start
- fixed the preferences not being saved to a file when the Save button is pressed in Edit -> Preferences
- fixed and updated the Transform Tools in the Editors
- updated the language translation strings (and Google_Translated some of them)
- made sure that if the user closes the app with an editor open, before the exit the editor is closed and signals disconnected
- updated the Italian translation - contribution by Golfetto Massimiliano
- made the timing for the object creation to be displayed in the shell

2.06.2020

- Tcl Shell - added a button to delete the content of the active line
- Tcl Command Isolate - fixed to work in the new configuration
- Tcl Command Follow - fixed to work in the new configuration
- Etch Compensation Tool - added a new etchant: alkaline baths
- fixed spacing in the status toolbar icons
- updated the translation files to the latest changes
- modified behavior of object comboboxes in Paint, NCC and CutOut Tools: now if an object is selected in Project Tab and is of the supported kind in the Tool, it will be auto-selected
- fixed some more strings
- updated the Google-translations for the German, Spanish, French
- updated the Romanian translation
- replaced the icon for the Editor in Toolbar (both for the normal icons and for icons in dark theme)

1.06.2020

- made the Distance Tool display the angle in values between 0 and 359.9999 degrees
- changed some strings
- fixed the warning that old preferences found even for new installation
- in Paint Tool fixed the message to select a polygon when using the Selection: Single Polygon being overwritten by the "Grid disabled" message
- more changes in strings throughout the app
- made some minor changes in the GUI of the FlatCAM Tools
- in Tools Database made sure that each new tool added has a unique name
- in AppTool made some methods to be class methods
- reverted the class methods in AppTool
- added a button for Transformations Tool in the lower side (common) of the Object UI
- some other UI changes
- after using Isolation Tool it will switch automatically to the Geometry UI
- in Preferences replaced some widgets with a new one that combine a Slider with a Spinner (from David Robertson)
- in Preferences replaced the widgets that sets colors with a compound one (from David Robertson)
- made Progressive plotting work in Isolation Tool
- fix an issue with progressive plotted shapes not being deleted on the end of the job
- some fixed due of recent changes and some strings changed
- added a validator for the FCColorEntry GUI element such that only the valid chars are accepted
- changed the status bar label to have an icon instead of text
- added a label in status bar that will toggle the Preferences tab
- made some changes such that that the label in status bar for toggling the Preferences Tab will be updated in various cases of closing the tab
- changed colors for the status bar labels and added some of the new icons in the gray version
- remade visibility as threaded - it seems that I can't really squeeze more performance from this

31.05.2020

- structural changes in Preferences from David Robertson
- made last filter selected for open file to be used next time when opening files (for Excellon, GCode and Gerber files, for now)

30.05.2020

- made confirmation messages for the values that are modified not to be printed in the Shell
- Isolation Tool: working on the Rest machining: almost there, perhaps I will use multiprocessing
- Isolation Tool: removed the tools that have empty geometry in case of rest machining
- Isolation Tool: solved some naming issues
- Isolation Tool: updated the tools dict with the common parameters value on isolating
- Fixed a recent change that made the edited Geometry objects in the Geometry Editor not to be plotted after saving changes
- modified the Tool Database such that when a tool shape is selected as 'V' any change in the Vdia or Vangle or CutZ parameters will update the tool diameter value
- In Tool Isolation made sure that the use of ESC key while some processes are active will disconnect the mouse events that may be connected, correctly
- optimized the Gerber UI
- added a Multi-color checkbox for the Geometry UI (will color differently tool geometry when the geometry is multitool)
- added a Multi-color checkbox for the Excellon UI (this way colors for each tool are easier to differentiate especially when the diameter is close)
- made the Shell Dock always show docked
- fixed NCC Tool behavior when selecting tools for Isolation operation

29.05.2020

- fixed the Tool Isolation when using the 'follow' parameter
- in Isolation Tool when the Rest machining is checked the combine parameter is set True automatically because the rest machining concept make sense only when all tools are used together
- some changes in the UI; added in the status bar an icon to control the Shell Dock
- clicking on the activity icon will replot all objects
- optimized UI in Tool Isolation
- overloaded the App inform signal to allow not printing to shell if a second bool parameter is given; modified some GUI messages to use this feature
- fixed the shell status label status on shell dock close from close button
- refactored some methods from App class and moved them to plotcanvas (plotcanvaslegacy) class
- added an label with icon in the status bar, clicking it will toggle (show status) of the X-Y axis on cavnas
- optimized the UI, added to status bar an icon to toggle the axis 
- updated the Etch Compensation Tool by adding a new possibility to compensate the lateral etch (manual value)
- updated the Etch Compensation Tool such that the resulting Gerber object will have the apertures attributes ('size', 'width', 'height') updated to the changes

28.05.2020

- made the visibility change (when using the Spacebar key in Project Tab) to be not threaded and to use the enabled property of the ShapesCollection which should be faster
- updated the Tool Database class to have the Isolation Tool data
- Isolation Tool - made to work the adding of tools from database
- fixed some issues related to using the new Numerical... GUI elements
- fixed issues in the Tool Subtract
- remade Tool Subtract to use multiprocessing when processing geometry
- the resulting Gerber file from Tool Subtract has now the attribute source_file populated

27.05.2020

- working on Isolation Tool: made to work the Isolation with multiple tools without rest machining

26.05.2020

- working on Isolation Tool: made to work the tool parameters data to GUI and GUI to data
- Isolation Tool: reworked the GUI
- if there is a Gerber object selected then in Isolation Tool the Gerber object combobox will show that object name as current
- made the Project Tree items not editable by clicking on selected Tree items (the object rename can still be done in the Selected tab)
- working on Isolation Tool: added a Preferences section in Edit -> Preferences and updated their usage within the Isolation tool
- fixed milling drills not plotting the resulting Geometry object
- all tuple entries in the Preferences UI are now protected against letter entry
- all entries in the Preferences UI that have numerical entry are protected now against letters
- cleaned the Preferences UI in the Gerber area
- minor UI changes

25.05.2020

- updated the GUI fields for the Scale and Offset in the Object UI to allow only numeric values and operators in the list [/,*,+,-], spaces, dots and comma
- modified the Etch Compensation Tool and added conversion utilities from Oz thickenss and mils to microns
- added a Toggle All checkbox to Corner Markers Tool
- added an Icon to the MessageBox that asks for saving if the user try to close the app and there is some unsaved work 
- changed and added some icons
- fixed the Shortcuts Tab to reflect the actual current shortcut keys
- started to work on moving the Isolation Routing from the Gerber Object UI to it's own tool
- created a new tool: Isolation Routing Tool: work in progress
- some fixes in NCC Tool
- added a dialog in Menu -> Help -> ReadMe?

24.05.2020

- changes some icons
- added a new GUI element which is a evaluated LineEdit that accepts only float numbers and /,*,+,-,% chars
- finished the Etch Compensation Tool
- fixed unreliable work of Gerber Editor and optimized the App.on_editing_finished() method
- updated the Gerber parser such that it will parse correctly Gerber files that have only one solid polygon inside with multiple clear polygons (like those generated by the Invert Tool)
- fixed a small bug in the Geometry UI that made updating the storage from GUI not to work
- some small changes in Gerber Editor

23.05.2020

- fixed a issue when testing for Exclusion areas overlap over the Geometry object solid_geometry

22.05.2020

- fixed the algorithm for calculating closest points in the Exclusion areas
- added the Exclusion zones processing to Geometry GCode generation

21.05.2020

- added the Exclusion zones processing to Excellon GCode generation
- fixed a non frequent plotting problem for CNCJob objects made out of Excellon objects

19.05.2020

- updated the Italian language (translation incomplete)
- updated all the language strings to the latest changes; updated the POT file
- fixed a possible malfunction in Tool Punch Gerber

18.05.2020

- fixed the PDF Tool when importing as Gerber objects
- moved all the parsing out of the PDF Tool to a new file ParsePDF in the flatcamParsers folder
- trying to fix the pixmap load crash when running a FlatCAMScript
- made the workspace label in the status bar clickable and also added a status bar message on status toggle for workspace
- modified the GUI for Film and Panelize Tools
- moved some of the GUI related methods from FlatCAMApp.App to the flatcamGUI.MainGUI class
- moved Shortcuts Tab creation in it's own class
- renamed classes to have shorter names and grouped
- removed reference to postprocessors and replaced it with preprocessors
- more refactoring class names
- moved some of the methods from the App class to the ObjectCollection class
- moved all the new_object related methods in their own class AppObjects.AppObject
- more refactoring; solved some issues introduced by the refactoring
- solved a circular import
- updated the language translation files to the latest changes (no translation)
- working on a new Tool: Etch Compensation Tool -> installed the tool and created the GUI and class template
- moved more methods out of appMain class
- added confirmation messages for toggle of HUD, Grid, Grid Snap, Axis
- added icon in status bar for HUD; clicking on it will toggle the HUD (heads up display)
- fixes due of recent changes
- fixed issue #417

17.05.2020

- added new FlatCAM Tool: Corner Markers Tool which will add line markers in the selected corners of the bounding box of the targeted Gerber object
- added a menu entry in Menu -> View for Toggle HUD
- solved the issue with the GUI in the Notebook being expanded too much in width due of the FCDoubleSpinner and FCSpinner sizeHint by setting the sizePolicy to Ignored value
- fixed the workspace being always A4
- added a label in the status bar to show if the workplace is active and what size it is
- now the Edit command (either from Menu Edit ->Edit Object) or through the shortcut key (E key) or project tab context menu works also for the CNCJob objects (will open a text Editor with the GCode)
- fixed the object collection methods that return a list of objects or names of objects such that they have a parameter now to allow adding to those lists (or not) for the objects of type Script or Document. Thus fixing some of the Tcl commands such Set Origin
- reverted the previous changes to object collection; it is better to create empty methods in FlatCAMScript and FlatCAMDocument objects

16.05.2020

- worked on the NCC Tool; added a new clear method named 'Combo' which will go through all methods until the clear is done
- added a Preferences parameter for font size used in HUD

13.05.2020

- updated the French translation strings, made by @micmac (Michel)

12.05.2020

- fixed recent issues introduced in Tcl command Drillcncjob
- updated the Cncjob to use the 'endxy' parameter which dictates the x,y position at the end of the job
- now the Tcl commands Drillcncjob and Cncjob can use the toolchangexy and endxy parameters with or without parenthesis (but no spaces allowed)
- modified the Tcl command Paint "single" parameter. Now it's value is a tuple with the x,y coordinates of the single polygon to be painted.
- the HUD display state is now persistent between app restarts
- updated the Distance Tool such that the right click of the mouse will cancel the tool unless it was a panning move
- modified the PlotCanvasLegacy to decide if there is a mouse drag based on the distance between the press event position and the release event position. If the distance is smaller than a delta distance then it is not a drag move.

11.05.2020

- removed the labels in status bar that display X,Y positions and replaced it with a HUD display on canvas (combo key SHIFT+H) will toggle the display of the HUD
- made the HUD work in Legacy2D mode
- fixed situation when the mouse cursor is outside of the canvas and no therefore returning None values
- remade the Snap Toolbar presence; now it is always active and situated in the Status Bar
- Snap Toolbar is now visible in Fullscreen
- in Fullscreen now the Notebook is available but it will be hidden on Fullscreen launch
- fixed some minor issues (in the HUD added a separating line, missing an icon in toolbars on first launch)
- made sure that the corner snap buttons are shown only in Editors
- changed the HUD color when using Dark theme 
- fix issue in Legacy2D graphic mode where the snap function was not accessible when the PlotCanvasLegacy class was created
- modified the HUD in Legacy2D when using Dark Theme to use different colors
- modified how the graphic engine change act in Preferences: now only by clicking Apply(or Save) the change will happen. And there is also a message asking for confirmation
- re-added the position labels in the status bar; they will be useful if HUD is Off (Altium does the same :) so learn from the best)
- fixed the Tcl command Cncjob: there was a problem reported as issue #416. The command did not work due of the dpp parameter
- modified the Tcl command Cncjob such that if some of the parameters are not used then the default values will be used (set with set_sys)
- modified the Tcl command Drillcncjob to use the defaults when some of the parameters are not used

10.05.2020

- fixed the problem with using comma as decimal separator in Grid Snap fields

9.05.2020

- modified the GUI for Exclusion areas; now the shapes are displayed in a Table where they can be selected and deleted. Modification applied for Geometry Objects only (for now).
- fixed an error when converting units, error that acted when in those fields that accept lists of tools only one tool was added
- finished the GUI for exclusion areas both in the Excellon and Geometry Objects. Need to think if to make it visible only in Advanced Mode

8.05.2020

- added a parameter to the AppDefaults class, whenever a value in the self.defaults dict change it will call a callback function and send to it the modified key
- optimized and fixed some issues in the self.on_toggle_units() method
- the Exclusion areas will have all the orange color but the color of the outline will differ according to the type of the object from where it was added (cosmetic use only as the Exclusion areas will be applied globally)
- removed the Apply theme button in the Preferences; it is now replaced by the more general buttons (either Save or Apply)
- added a confirmation/warning message when applying a new theme

7.05.2020

- added a fix so the app close is now clean, with exit code 0 as set
- added the ability to add exclusion areas from the Excellon object too. Now there is a difference in color to differentiate from which type of object the exclusion areas were added but they all serve the same purpose

6.05.2020

- wip in adding Exclusion areas in Geometry object; each Geometry object has now a storage for shapes (exclusion shapes, should I make them more general?)
- changed the above: too many shapes collections and the performance will go down. Created a class ExclusionAreas that holds all the require properties and the Object UI elements will connect to it's methods. This way I can apply this feature to Excellon object too (who is a special type of Geometry Object)
- handled the New project event and the object deletion (when all objects are deleted then the exclusion areas will be deleted too)
- solved issue with new parameter end_xy when it is None
- solved issue with applying theme and not making the change in the Preferences UI. In Preferences UI the theme radio is always Light (white)
- now the annotations will invert the selected color in the Preferences, when selecting Dark theme 

5.05.2020

- fixed an issue that made the preprocessors combo boxes in Preferences not to load and display the saved value fro the file
- some PEP8 corrections

4.05.2020

- in detachable tabs, Linux loose the reference of the detached tab and on close of the detachable tabs will gave a 'segmentation fault' error. Solved it by not deleting the reference in case of Unix-like systems
- some strings added to translation strings

3.05.2020

- small changes to allow making the x86 installer that is made from a Python 3.5 run FlatCAM beta 
- fixed multiple parameter 'outname' in the Tcl commands OpenGerber and OpenGcode 
- added more examples in the scripts Examples: isolate and cutout examples
- updated the Italian translation
- updated the translation files
- changed the line endings for Makefile and setup_ubuntu.sh files
- protected a dict in VispyVisuals from issuing errors of keys changed while iterating through it

2.05.2020

- working on a new feature: adding interdiction area for Gcode generation. They will be added in the Geometry Object

2.05.2020

- changed the icons for the grid snap in the status bar
- moved some of the methods from FlatCAMApp.App to flatcamGUI.MainGUI class
- fixed bug in Gerber Editor in which the units conversion wasn't calculated correct
- fixed bug in Gerber Editor in which the QThread that is started on object edit was not stopped at clean up stage
- fixed bug in Gerber Editor that kept all the apertures (including the geometry) of a previously edited object that was not saved after edit
- modified the Cutout Tool to generate multi-geo objects therefore the set geometry parameters will populate the Geometry Object UI
- modified the Panelize Tool to optimize the output from Cutout Tool such that there are no longer overlapping cuts
- some string corrections
- updated the Italian translation done by user @pcb-hobbyst (Golfetto Massimiliano)
- RELEASE 8.992

01.05.2020

- added some ToolTips (strings needed to be translated too) for the Cut Z entry in Geometry Object UI that explain why is sometime disabled and reason for it's value (sometime is zero)
- solve parenting issues when trying to load a FlatScript from Menu -> File -> Scripting
- added a first new example script and added some files to work with
- added a new parameter that will store the home folder of the FlatCAM installation so we can access the example folder
- added in Gerber editor a method for zoom fit that takes into consideration the current geometry of the edited object

30.04.2020 

- made some corrections - due of recent refactoring PyCharm reported errors all over (not correct but it made programming difficult)
- modified the requirements.txt file to force svg.path module to be at least version 4.0
- fixed bug in Tools DB that crashed when a tool is copied
- in Tools Database added a Save Button whose color is changed in Red if the DB was modified and back to default when the DB is saved.
- fixed bug in Tool DB that crashed the app when the Tool Name was modified but there was no tree item (a tool in the list) selected in the Tree widget (list of tools)
- now on tool add and tool copy, the last item (tool, which is the one added) is autoselected; o tool delete always the first item (tool) is selected
- fixed issue #409; problem was due of an assert I used in the handler of the Menu ->Options -> Flip X(Y) menu entry
- activated and updated the editing in the Aperture Table in the Gerber Editor; not all parameters can be edited for every type of aperture
- some strings updated
- fixed a small issue in loading the Projects

29.04.2020

- added a try-except clause in the FlatCAMTranslation.restart_program() when closing the Listener and the thread that runs it to adjust to MacOS usage
- more PEP8 changes
- in PreferencesUI.PreferencesUIManager class I removed the need to pass reference to the App class since it this was available through the 'ui' parameter
- some fixes due to recent refactoring
- minor bugs fixed (not so visible)
- promoted some methods to be static
- set the default layout on first run to the 'minimal' value
- modified the method that detects which tab was closed in the Plot Area so it will no longer depend on it's translated text but on it's objectName set on the QTab creation
- fixed the merge methods for all FlatCAM objects
- fixed a SyntaxError Exception when checking for types of found old preferences
- updated the French, German and Spanish Google translations
- updated the Romanian translation
- fixed units conversion issue
- updated the units conversion method to convert all the convertible parameters in the Preferences
- solved the problem with not closing all the tabs in Plot Area when creating a New Project; the issue was that once a tab was removed the indexes are remade (when tab 0 is removed then tab 1 becomes tab 0 and so on)
- some more strings changed -> updated the translations
- replaced some FormLayouts with Gridlayouts in Tool Cutout.

28.04.2020

- handled a possible situation in App.load_defaults() method
- fixed some issues in FlatCAMDB that may appear in certain scenarios
- some minor changes in the Python version detection
- added a new Tcl Command named SetPath which will set a path to be used by the Tcl commands. Once set will serve as a fallback path in case that the files fail to be opened first time. It will be persistent, saved in preferences.
- added the GUI for the new Open Example in the FIle -> Scripting menu.
- I am modifying all the open ... handlers to add a parameter that will flag if the method was launched from Tcl Shell. This way if the method will fail to open the filename (which include the path) it will try to open from a set fallback path.
- fixed issue #406, bug introduced recently (leftover changes).
- modified the ImportSVG Tcl command name to OpenSVG (open_svg alias)
- added a new Tcl command named OpenDXF (open_dxf alias)
- fixed some errors in Scripting features
- added a new Tcl command named GetPath as a convenient way to get the current default path stored in App.defaults['global_tcl_path']
- added a new package to be installed in Linux to make available the black theme for FlatCAM beta
- moved all the 'share' resources (icons) to the 'assets/resources' folder
- some more fixes to problems generated by latest changes in the open handlers
- modified the make_freezed.py script for the new location of the icons
- added a fix for the ConnectionRefusedError in Linux that is issued when first running after a FlatCAM crash
- in SVG parser modified some imports to be one on each line
- fixed the Tcl Command BBox (leftovers from recent global changes)
- fixed some typos in strings reported by @pcb-hobbyst on FlatCAM forum
- disabled a skip_quotes method in ToolShell.FCShell class so I can now use quotes to enclose file paths with spaces inside

27.04.2020

- finished the moving of all Tcl Shell stuff out of the FlatCAAMApp class to flatcamTools.ToolShell class
- updated the requirements.txt file to request that the Shapely package needs to be at least version 1.7.0 as it is needed in the latest versions of FlatCAM beta
- some TOOD cleanups
- minor changes
- replaced the testing if instance of FlatCAMObj with testing the obj.kind attribute
- removed the import of the whole FlatCAMApp file only for the usage of GracefulException
- remove the import of FlatCAMApp and used alternate ways
- optimized the imports in some files
- moved the Bookmarksmanager and ToolDB classes into their own files
- solved some bugs that were not so visible in the Editors and HPGL parser
- split the FlatCAMObj file into multiple files located in the flatcamObjects folder and renamed the contained classes with names more suggestive
- updated the Google Translation for the German language
- added support for Hungarian language - no translation for now
- minor changes
- moved the ObjectCollection class to the flatcamObjects folder where it belongs
- Linux Makefile 

25.04.2020

- ensured that on Graceful Exit (CTRL+ALT+X key combo) if using Progressive Plotting, the eventual residual plotted lines are deleted. This apply for Tool NCC and Tool Paint
- fixed links in Attributions tab in Help -> About FlatCAM to be able to open external links.
- updated Google Translations for French and Spanish languages
- added some '\n' chars in the Help Tcl command to make the help more readable

24.04.2020

- some PEP changes, some method descriptions updated
- added a placeholder text to 2Sided Tool
- added a new menu entry in the context menu of the Tcl Shell: 'Save Log' which will save the content of the Tcl Shell browser window to a file
- the status bar messages that are echoed in the Tcl Shell will no longer have all text colored but only the identifier
- some message strings cleanup
- added possibility to save as text file the content in Tcl Shell browser window when clicking the Save log context menu entry
- fixed an issue regarding the statusbar pixmap selection
- update the language template strings.pot and updated the Romanian translation
- updated the Readme file with the steps for installation for MacOS
- updated the requirements.txt file
- updated some of the icons in the dark_resources folder (some added, some modified)
- updated Paint Tool for the new Tool DB
- updated the Tcl commands CopperClear and Paint

23.04.2020 

- fixed the Tcl Command Help to work as expected; made the text of the commands to be colored in Red color and bold
- added a 'Close' menu entry in the Tcl Shell context menu that will close (hide) the Tcl Shell Dock widget
- on launching the Tcl Shell the Edit line will take focus immediately 
- in App.on_mouse_move_over_plot() method no longer will be done a setFocus() on every move, only when it is needed
- added an extra check if old preferences files are detected, a check if the type of the values is the same with the type in the current preferences file. If the type is not the same then the current type is preferred.
- aligned the Tcl commands display when the Help Tcl command is run without parameters
- fixed the Tcl command Plot_All that malfunctioned if there were any FlatCAM scripts (or FlatCAM documents) open
- updated the shortcuts list

22.04.2020 

- added a new feature, project auto-saving controlled from Edit -> Preferences -> General -> APP. Preferences -> Enable Auto Save checkbox
- fixed some bugs in the Tcl Commands
- modified the Tcl Commands to be able to use as boolean values keywords with lower case like 'false' instead of expected 'False'
- refactored some of the code in the App class and created a new Tcl Command named Help

20.04.2020

- made the Grid icon in the status bar clickable and it will toggle the snap to grid function
- some mods in the Distance Tool
- added ability to use line width when adding shapes for both Legacy and OpenGL graphic engines
- added the linewidth=2 parameter for the Tool Distance utility geometry
- fixed a selection issue in Legacy graphic mode for single click
- added a CHANGELOG file and changed the README file to contain the installation instructions
- updated the README file
- in Project Tab added tooltips for the loaded objects
- fixed a bug in loading objects by drag&drop into the Project Tab where only one object in the selection was loaded

19.04.2020 

- fixed a bug that did not allow to edit GUI elements of type FCDoubleSpinner if it contained the percent symbol
- some small optimizations in the GUI of Cutout Tool
- fixed more issues (new) in NCC Tool
- added a new layout named 'minimal'
- some PEP8 changes in Geometry Editor

15.04.2020 

- made sure that the Tcl commands descriptions listed on help command are aligned

14.04.2020 

- lightened the hue of the color for 'success' messages printed in the Tcl Shell browser
- modified the extensions all over such the names include also the extension name. For Linux who does not display the extensions in the native FileDialog.
- added descriptions for some of the methods in the app.
- added lightened icons for the dark theme from Leandro Heck 

13.04.2020 

- added the outname parameter for the geocutout Tcl command
- multiple fixes in the Tcl commands (especially regarding the interchange between True/false and 1/0 values)
- updated the help for all Tcl Commands
- in Tcl Shell, the 'help' command will add also a brief description for each command in the list
- updated the App.plot_all() method giving it the possibility to be run as threaded or not
- updated the Tcl command PlotAll to be able to run threaded or not
- updated the Tcl commands PlotAll and PlotObjects to have a parameter that control if the objects are to be plotted or not on canvas; it serve as a disable/enable
- minor update to the autocomplete dictionary
- the Show Shell in Edit -> Preferences will now toggle the Tcl shell based on the current status of the Tcl Shell
- updated the Tcl command Isolate help for follow parameter 
- updated DrillCncJob Tcl Command with new parameters and fixed it to work in the new format of the Excellon methods
- fixed issue #399
- changed CncJob Tcl Command parameter 'depthperpass' to a shorter 'dpp'

11.04.2020 

- fixed issue #394 - the saveDialog in Linux did not added the selected extension
- when the Save button is clicked in the Edit -> Preferences the Preferences tab is closed.

10.04.2020 

- made sure that the timeout parameter used by some Tcl Commands is seen as an integer in all cases - fixed issue #389
- minor changes in Paint Tool
- minor changes in GUI (Save locations in Menu -> File) and the key shortcuts - fixed issue #391


9.04.2020 

- if FlatCAM is not run with Python version >= 3.5 it will exit.
- modified all CTRL+ with Ctrl+ and all ALT+ with Alt+ and all SHIFT+ with Shift+. Fixed issue #387.
- removed some packages from setup_ubuntu.sh as they are not needed in FlatCAM beta

8.4.2020 

- fixed the Tcl Command Delete to have an argument -f that will force deletion evading the popup (if the popup is enabled). The sme command without a name now will delete all objects
- fixed the Tcl Command JoinExcellons
- fixed the Tcl Command JoinGeometry
- fixed the Tcl Command Mirror
- updated the Tcl Command Mirror to use a (X,Y) origin parameter. Works if the -box parameter is not used.
- updated the Tcl Command Offset. Now it can use only -x or -y parameter no longer is mandatory to have both. The one that is not present will be assumed 0.0
- updated the Tcl Command Panelize. The -rows and -columns parameters are no longer both required. If one is not present then it is assumed to be zero.
- updated the Tcl Command Scale. THe -origin parameter can now be a tuple of (x,y) coordinates.
- updated the Tcl Command Skew. Now it can use only -x or -y parameter no longer is mandatory to have both. The one that is not present will be assumed 0.0
- updated the help for all the Tcl Commands

6.04.2020 

- added key shortcuts (arrow up/down) that will select the objects in the Project tab if the focus is in that tab
- added a minor change to the ListSys Tcl command
- fixed an crash generated when running the Tool Database from the Menu -> Options menu entry
- fixed a bug in handling the UP/DOWN key shortcuts that caused a crash when no object was selected in the Project Tab; also made sure that the said keys are handled only for the Project Tab
- some PEP8 changes and other minor changes
- updated the requirements file
- updated the 2Sided Tool by not allowing the Gerber file to be mirrored without a valid reference and added some placeholder texts

5.04.2020 

- made sure that the HDPI scaling attribute is set before the QApplication is started
- made sure that when saving a project, the app will try to update the active object from UI form only if there is an active object
- fix for contextual menus on canvas when using PyQt versions > 5.12.1
- decision on which mouse button to use for panning is done now once when setting the plotcanvas
- fix to work with Python 3.8 (closing the application)
- fixed bug in Gerber parser that allowed loading as Gerber of a file that is not a Gerber
- fixed a bug in extension detection for Gerber files that allowed in the filtered list files that extension *.gb*
- added a processEvents method in the Gerber parser parse_lines() method
- fixed issue #386 - multiple Cut operation on a edited object created a crash due of the bounds() method
- some changes in the Geometry UI

4.04.2020 

- fixed the Repeated code parsing in Excellon Parse

1.04.2020 

- updated the SVG parser to take into consideration the 'Close' svg element and paths that are made from a single line (we may need to switch to svgpathtools module)
- minor changes to increase compatibility with Python 3.8
- PEP8 changes

30.03.2020

- working to update the Paint Tool
- fixed some issues in Paint Tool

29.03.2020

- modified the new database to accept data from NCC and Paint Tools
- fixed issues in the new database when adding the tool in a Geometry object
- fixed a bug in Geometry object that generated a change of dictionary while iterating over it
- started to add the new database links in the NCC and Paint Tools
- in the new Tools DB added ability to double click on the ID in the tree widget to execute adding a tool from DB
- working in updating NCC Tool

28.03.2020

- finished the new database based on a QTreeWidget

21.03.2020

- fixed Cutout Tool to work with negative values for Margin parameter

20.03.2020

- updated the "re-cut" feature in Geometry object; now if the re-cut parameter is non zero it will cut half of the entered distance before the isolation end and half of it after the isolation end
- added to Paint and NCC Tool a feature that allow polygon area selection when the reference is selected as Area Selection
- in Paint Tool and NCC Tool added ability to use Escape Tool to cancel Area Selection and for Paint Tool to cancel Polygon Selection
- fixed issue in "re-cut" feature when combined with multi-depth feature
- fixed bugs in cncjob TclCommand

13.03.2020

- fixed a bug in CNCJob generation out of a Excellon object; the plot failed in case some of the geometry of the CNCJob was invalid
- fixed Properties Tool due of recent changes to the FCTree widget

12.03.2020

- working on the new database
- fix a bug in the TextInputTool in FlatCAM Geometry Editor that crashed the sw when some fonts are not loaded correctly

4.03.2020

- updated all the FlatCAM Tools and the Gerber UI FCComboBoxes to update the box value with the latest object loaded in the App
- some fixes in the NCC Tool
- modified some strings

02.03.2020

- added property that allow the FCComboBox to update the view with the last item loaded; updated the app to use this property

01.03.2020

- updated the CutOut Tool such that while adding manual gaps, the cutting geometry is updated on-the-fly if the gap size or tool diameter parameters are adjusted
- updated the UI in Geometry Editor

29.02.2020

- compacted the NCC Tool UI by replacing some Radio buttons with Combo boxes due of too many elements
- fixed error in CutOut Tool when trying to create a FreeFrom Cutout out of a Gerber object with the Convex Shape checked
- working on a new type of database

28.02.2020

- some small changes in preprocessors
- solved issue #381 where there was an error when trying to generate CNCJob out of an Excellon file that have a tool with only slots and no drills
- solved some issues in the preprocessors regarding the newly introduced feature that allow control of the final move X,Y positions

25.02.2020

- fixed bug in Gerber parser: it tried to calculate a len() for a single element and not a list - a Gerber generated by Eagle exhibited this
- added a new parameter named 'End Move X,Y' for the Geometry and Excellon objects. Adding a tuple of coordinates in this field will control the X,Y position of the final move; not entering a value there will cause not to make an end move

20.02.2020

- in Paint Tool replaced the Selection radio with a combobox GUI element that is more compact
- in NCC Tool modified the UI

19.02.2020

- fixed some issues in the Geometry Editor; the jump signal disconnect was failing for repeated Editor tool operation
- fixed an issue in Gerber Editor where the multiprocessing pool was reported as closed and an ValueError exception was raised in a certain scneraio
- on Set Origin, Move to Origin and Move actions for Gerber and Excellon objects the source file will be also updated (the export functions will export an updated object)
- in FlatCAMObj.export_gerber() method took into account the possibility of polygons of type 'clear' (the ones found in the Gerber files under the LPC command)

17.02.2020

- updated the Excellon UI to hold data for each tool
- in Excellon UI removed the tools table column for Offset Z and used the UI form parameter
- updated the Excellon Editor to add for each tool a 'data' dictionary
- updated all FlatCAM tools to use the new confirmation message that show if the entered value is within range or outside
- updated all FlatCAM tools to use the new confirmation message for QSpinBoxes, too
- in Excellon UI protected the values that are common parameters from change on tool selection change
- fixed some issues related to the usage of the new confirmation message in FlatCAM Tools
- made sure that the FlatCAM Tools UI initialization is done only in set_tool_ui() method and not in the constructor
- adapted the GCode generation from Excellon to work with multiple tools data and modified the preprocessors header
- when multiple tools are selected in Excellon UI and parameters are modified it will applied to all selected
- in Excellon UI, Paint Tool and NCC Tool finished the "Apply parameters to all tools" functionality
- updated Paint Tool and NCC Tool in the UI functionality
- fixed the Offset spinbox not being controller by offset checkbox in NCC Tool

16.02.2020

- small update to NCC Tool UI

15.02.2020

- in Paint Tool added a new method of painting named Combo who will pass through all the methods until the polygon is cleared
- in Paint Tool attempting to add a new mode suitable for Laser usage
- more work in the new Laser Mode in the Paint Tool
- modified the Paint Tool UI

14.02.2020

- adjusted the UI for Excellon and Geometry objects
- added a new FlatCAM Tool: Gerber Invert Tool. It will invert the copper features in a Gerber file: where is copper there will be empty and where is empty it will be copper
- added the Preferences entries for the Gerber Invert Tool

13.02.2020

- finished Punch Gerber Tool
- minor changes in the Tool Transform and Tool Calculators UI to bring them up2date with the other tools

12.02.2020

- working on fixing a bug in GeometryObject.merge() - FIXED issue #380
- fixed bug: when deleting a FlatCAMCNCJob with annotations enabled, the annotations are not deleted from canvas; fixed issue #379
- fixed bug: creating a new project while a project is open and it contain CNCJob annotations and/or Gerber mark shapes, did not delete them from canvas

11.02.2020

- working on Tool Punch; finished the geometry update with the clear geometry for the case of Excellon method
- working on Tool Punch; finished the geometry update with the clear geometry for the case of Fixed Diameter method

10.02.2020

- optimized the Paint and NCC Tools. When the Lines type of painting/clearing is used, the lines will try to arrange themselves on the direction that the lines length clearing the polygon are bigger
- solved bug that made drilling with Marlin preprocessor very slow
- applied the fix for above bug to the TclCommand Drillcncjob too
- started a new way to clear the Gerber polygons based on the 'follow' lines
- some cleanup and bug fixes for the Paint Tool


8.02.2020

- added a new preprocessor for using laser on a Marlin 3D printer named 'Marlin_laser_use_Spindle_pin'
- modified the Geometry UI when using laser preprocessors
- added a new preprocessor file for using laser on a Marlin motion controller but with the laser connected to one of the FAN pins, named 'Marlin_laser_use_FAN_pin'
- modified the Excellon GCode generation so now it can use multi depth drilling; modified the preprocessors to show the number of passes

5.02.2020

- Modified the Distance Tool such that the Measure button can't be clicked while measuring is in progress
- optimized selection of drills in the Excellon Editor
- fixed bugs in multiple selection in Excellon Editor
- fixed selection problems in Gerber Editor
- in Distance Tool, when run in the Excellon or Gerber Editor, added a new option to snap to center of the geometry (drill for Excellon, pad for Gerber)

3.02.2020

- modified Spinbox and DoubleSpinbox Custom UI elements such that they issue a warning status message when the typed value is out of range
- fixed the preprocessors with 'laser' in the name to use the spindle direction set in the Preferences
- increased the upper limit for feedrates by an order of magnitude

2.02.2020

- fixed issue #376 where the V-Shape parameters from Gerber UI are not transferred to the resulting Geometry object if the 'combine' checkbox is not checked in the Gerber UI
- in Excellon UI, if Basic application mode is selected in Preferences, the Plot column 'P' is hidden now because some inexperienced users mistake this column checkboxes for tool selection
- fixed an error in Gerber Parser; the initial values for current_x, current_y were None but should have been 0.0
- limited the lower limit of angle of V-tip to a value of 1 because 0 makes no sense 
- small changes in Gerber UI
- in Geometry Editor make sure that after an edit is finished (correctly or forced) the QTree in the Editor UI is cleared of items

31.01.2020

- added a new functionality, a variation of Set Origin named Move to Origin. It will move a selection of objects to origin such as the bottom left corner of the bounding box that fit them all is in origin.
- fixed some bugs
- fixed a division by zero error: fixed #377

30.01.2020

- remade GUI in Tool Cutout, Tool Align Objects, Tool Panelize
- some changed in the Excellon UI
- some UI changes in the common object UI

29.01.2020

- changes in how the Editor exit is handled
- small fix in some pywin32 imports
- remade the GUI + small fixes in 2Sided Tool
- updated 2Sided Tool

28.01.2020

- some changes in Excellon Editor

27.01.2020

- in Geometry Editor made sure that on final save, for MultiLineString geometry all the connected lines are merged into one LineString to minimize the number of vertical movements in GCode
- more work in Punch Gerber Tool
- the Jump To popup window will now autoselect the LineEdit therefore no more need for an extra click after launching the function
- made some structural changes in Properties Tool
- started to make some changes in Geometry Editor
- finished adding in Geometry Editor a TreeWidget with the geometry shapes found in the edited object

24.02.2020

- small changes to the Toolchange manual preprocessor
- fix for plotting Excellon objects if the color is changed and then the object is moved
- laying the GUI for a new Tool: Punch Gerber Tool which will add holes in the Gerber apertures
- fixed bugs in Minimum Distance Tool
- update in the GUI for the Punch Gerber Tool

22.01.2020

- fixed a bug in the bounding box generation

19.01.2020

- fixed some bugs that are visible in Linux regarding the ArgsThread class: on app close we need to quit the QThread running the ArgsThread class and also close the opened Socket
- make sure that the fixes above apply when rebooting app for theme change or for language change
- fixed and issue that made setting colors for the Gerber file not possible if using a translation
- made possible to set the colors for Excellon objects too
- added to the possible colors the fundamentals: black and white
- in the project context menu for setting colors added the option to set the transparency and also a default option which revert the color to the default value set in the Preferences

17.01.2020

- more changes to Excellon UI
- changes to Geometry UI
- more work in NCC Tool upgrade; each tool now work with it's own set of parameters
- some updates in NCC Tool
- optimized the object envelope generation in the redesigned NCC Tool

16.01.2020

- updated/optimized the GUI in Preferences for Paint Tool and for NCC Tool
- work in Paint Tool to bring it up to date with NCC Tool
- updated the GUI in preferences for Calculator Tool
- a small change in the Excellon UI
- updated the Excellon and Geometry UI to be similar
- put bases for future changes to Excellon Object UI such that each tool will hold it's own parameters
- in ParseExcellon.Excellon the self.tools dict has now a key 'data' which holds a dict with all the default values for Excellon and Geometry
- Excellon and Geometry objects, when started with multiple tools selected, the parameters tool name reflect this situation
- moved default_data data update from Excellon parser to the Excellon object constructor

15.01.2020

- added key shortcuts and toolbar icons for the new tools: Align Object Tool (Alt+A) and Extract Drills (Alt+I)
- added new functionality (key shortcut Shift+J) to locate the corners of the bounding box (and center) in a selected object
- modified the NCC Tool GUI to prepare for accepting a tool from a tool database
- started to modify the Paint Tool to be similar to NCC Tool and to accept a tool from a database
- work in Paint Tool GUI functionality

14.01.2020

- in Extract Drill Tool added a new method of drills extraction. The methods are: fixed diameter, fixed annular ring and proportional
- in Align Objects Tool finished the Single Point method of alignment
- working on the Dual Point option in Align Objects Tool - angle has to be recalculated
- finished Dual Point option in Align Objects Tool

13.01.2020

- fixed a small GUI issue in Excellon UI when Basic mode is active
- started the add of a new Tool: Align Objects Tool which will align (sync) objects of Gerber or Excellon type
- fixed an issue in Gerber parser introduced recently due of changes made to make Gerber files produced by Sprint Layout
- working on the Align Objects Tool

12.01.2020

- improved the circle approximation resolution
- fixed an issue in Gerber parser with detecting old kind of units
- if CTRL key is pressed during app startup the app will start in the Legacy(2D) graphic engine compatibility mode

11.01.2020

- fixed an issue in the Distance Tool
- expanded the Extract Drills Tool to use a particular annular ring for each type of aperture flash (pad)
- Extract Drills Tool: fixed issue with oblong pads and with pads made from aperture macros
- Extract Drills Tool: added controls in Edit -> Preferences

10.02.2020

- working on a new tool: Extract Drills Tool who will create a Excellon object out of the apertures of a Gerber object
- finished the GUI in the Extract Drills Tool
- fixed issue in Film Tool where some parameters names in calls of method export_positive_handler() were not matching the actual parameters name
- finished the Extract Drills Tool
- fixed a small issue in the DoubleSided Tool

8.01.2020

- working in NCC Tool
- selected rows in the Tools Tables will stay colored in blue after loosing focus instead of the default gray
- in NCC Tool the Tool name in the Parameters section will be the Tool ID in the Tool Table
- added an exception catch in case the plotcanvas init failed for the OpenGL graphic engine and warn user about what happened

7.01.2020

- solved issue #368 - when using the Enable/Disable prj context menu entries the plotted status is not updated in the object properties
- updates in NCC Tool

6.01.2020

- working on new NCC Tool

2.01.2020

- started to rework the NCC Tool GUI in preparation for adding a Tool DB feature
- for auto-completer, now clicking an entry in the completer popup will select that entry and insert it
- made available only for Linux and Windows (not OSX) the starting of the thread that checks if another instance of FlatCAM is already running at the launch of FLatCAM
- modified Toggle Workspace function to work in the new Preferences UI configuration
- cleaned the app from progress signal usage since it is not used anymore

1.01.2020

- fixed bug in NCC Tool: after trying to add a tool already in the Tool Table when trying to change the Tool Type the GUI does not change
- final fix for app not quiting when running a script as argument, script that has the quit_flatcam Tcl command; fixed issue #360
- fixed issue #363. The Tcl command drillcncjob does not create tool cut, does not allow creation of gcode, it forces the usage of dwell and dwelltime parameters
- in NCC Tool I've added a warning so the user is warned that the NCC margin has to have a value of at least the tool diameter that is doing an iso_op job in the Tool Table
- modified the Drillcncjob and Cncjob Tcl commands to be allowed to work without the 'dwell' and 'toolchange' arguments. If 'dwelltime' argument is present it will be assumed that the 'dwell' is True and the same for 'toolchangez' parameter, if present then 'toolchange' will be assumed to be True, else False
- modified the extracut and multidepth parameters in Cncjob Tcl command like for dwell and toolchange
- added ability for Tcl commands to have optional arguments with None value (meaning missing value). This case should be treated for each Tcl command in execute() method
- fixed the Drillcncjob Tcl command by adding an custom self.options key "Tools_in_use" and build it's value, in case it does not exist, to make the toolchange command work
- middle mouse click on closable tabs will close them

30.12.2019

- Buffer sub-tool in Transform Tool: added the possibility to apply a factor effectively scaling the aperture size thus the copper features sizes
- in Transform Tool adjusted the GUI
- fixed some decimals issues in NCC Tool, Paint Tool and Excellon Editor (they were still using the hardcoded values)
- some small updates in the NCC Tool
- changes in the Preferences UI for NCC and Paint Tool in Tool Dia entry field
- fixed Tcl commands that use the overlap parameter to switch from fraction to percentage
- in Transform Tool made sure that the buffer sub-tool parameters are better explained in tooltips
- attempt to make TclCommand quit_flatcam work under Linux
- some fixes in the NCC Tcl command (using the bool() method on some params)
- another attempt to make TclCommand quit_flatcam work under Linux
- another attempt to make TclCommand quit_flatcam work under Linux - use signal to call a hard exit when in Linux
- TclCommand quit_flatcam work under Linux

29.12.2019

- the Apply button text in Preferences is now made red when changes were made and require to be applied
- the Gerber UI is built only once now so the process is lighter on CPU
- the Gerber apertures marking shapes storage is now built only once because the more are built the more sluggish is the interface
- added a new function called by shortcut key combo Ctrl+G when the current widget in Plot Area is an Code Editor. It will jump to the specified line in the text.
- fixed a small bug where the app tried to hide a label that I've removed previously
- in Paint Tool Preferences is allowed to add a list of initial tools separated by comma
- in Geometry Paint Tool fixed the Overlap rate to work between 0 and 99.9999%

28.12.2019

- more updates to the Preferences window and in some other parts of the GUI
- updated the translations (less Russian)
- fixed a minor issue that when saving a project with CNCJob objects, the variable that holds the origin of the CNCJob object was not saved in the project. Added to the serializable objects also the exc_cnc_tools dictionary 
- some changes in the File menu

28.12.2019

- updated all the translations files
- fixed the big mouse cursor in OpenGL(3D) graphic mode to get the set color
- fixed the cursor to have the set color and set cursor width in the Legacy(2D) graphic engine
- in Legacy(2D) graphic mode fixed the cursor toggle when the big cursor is activated
- in Legacy(2D) fixed big mouse cursor to snap to the grid
- RELEASE 8.991

27.12.2019

- updated the POT file and the translation files for German, Spanish and French languages
- fixed some typos

26.12.2019

- modified the ToolDB class and changed some strings
- Preferences classes now have access to the App attributes through app.setup_obj_classes() method
- moved app.setup_obj_classes() upper in the App.__init__()
- added a new Preferences setting allowing to modify the mouse cursor color
- remade the GUI in Preferences -> General grouping the settings in a more clear way
- made available the Jump To function in Excellon Editor
- added a clean_up() method in all the Editor Tools that need it, to be run when aborting using the ESC key
- fixed an error in the Gerber parser; it did not took into consideration the aperture size declared before the beginning of a Gerber region. Detected for Gerber files generated by KiCAD 5.x
- in Panelize Tool made sure that for Gerber objects if one of the apertures is without geometry then it is ignored
- further modifications in Preferences -> General GUI
- further modifications in Preferences -> General GUI - extended the changes
- in Legacy(2D) graphic engine made to work the mouse color change
- theme changing is no longer auto-reboot upon change; it require now to press a button
- cleaned the Preferences classes and added the signals and signal slots in those classes, removing them from the main app class
- each FlatCAM object found in Preferences has it's own set of controls for changing the colors
- added a set of gray icons to be used when the theme is complete dark (for now it is useful only for MacOS with dark theme because at the moment the app is not styled to dark UI except the plot area)

25.12.2019

- fixed an issue in old default file detection and in saving the factory defaults file
- in Preferences window removed the Import/Export Preferences buttons because they are redundant with the entries in the File -> Menu -> Backup. and added a button to Restore Defaults
- when in Basic mode the Tool type of the tool in the Geometry UI Tool Table after isolating a Gerber object is automatically selected as 'C1'
- let the multiprocessing Pool have as many processes as needed
- added a new Preferences setting allowing a custom mouse line width (to make it thicker or thinner)
- changed the extension of the Tool Database file to FlatDB for easy recognition (in the future double clicking such a file might import the new tools in the FC database)

24.12.2019

- edited some icons so they don't contain white background
- fixed an incorrect usage of object in the app.select_objects() method
- fixed a typo in ToolDB.on_tool_add()

23.12.2019

- some fixes in the Legacy(2D) graphic mode regarding the possibility of changing the color of the Gerber objects
- added a method to darken the outline color for Gerber objects when they have the color set
- when Printing as PDF Gerber objects now the rendered color is the print color
- speed up the plotting in OpenGL(3D) graphic mode
- speed up the color setting for Gerber object when using the OpenGL(3D) graphic mode
- setting color for Gerber objects work on a selection of Gerber objects
- ~~when the selection is changed in the Project Tree the selection shape on canvas is deleted~~
- if an object is selected on Project Tree and it does not have the selection shape drawn, first click on canvas over it will draw the selection shape 
- in Tool Transform added a new feature named 'Buffer'. For Geometry and Gerber objects will create (and replace) a geometry at a distance from the original geometry and for Excellon will adjust the Tool diameters
- solved issue #355 - when the tool diameter field in the Edit → Preferences → Geometry → Geometry General → Tools → Tool dia is only one the app failed to read it
- solved issue #356 - in Tools DB can not be added more than one tool if a translation is active 
- some changes related to the fact that the geometry default tool diameter value can be comma separated string of tool diameters

22.12.2019

- added a new option for the Gerber objects: on the project context menu now can be chosen a color for the selected Gerber object
- fixed issue in Gerber UI where a label was not hidden when in Basic mode
- added the color parameters of the objects to the serializable attributes
- fixed Gerber object color set for Legacy(2D) graphic engine; glitch on the OpenGL(3D) graphic engine
- fixed the above mentioned glitch in the OpenGL(3D) graphic engine when an Gerber object has been set with a color

21.12.2019

- fixed a typo in Distance Tool

20.12.2019

- fixed a rare issue in the generation of non-copper-region geometry started from the Gerber Object UI (selected tab)
- Print function is now printing a PDF file for a selection of objects in the colors from canvas 
- added an icon in the infobar that will show more clearly the status of the grid snapping
- in Geometry Object UI (selected tab) when a tool type is changed from no matter what to V-shape, the cut_z value is saved and when the tool type is changed back to something different than V-shape, this saved cut-z value is restored
- fixed re-cut length entry not staying disabled when the re-cut cb is not checked

19.12.2019

- in 2-Sided Tool added a way to calculate the bounding box values for a selection of objects, and also the centroid
- in 2-Sided Tool fixed the Reset Tool button handler to reset the bounds value too; changed a string
- added Preferences values for PDF margins when saving text in Code Editor as PDF
- when clicking Cancel in Preferences now the values are reverted to what they used to be before opening Preferences tab and start changing values
- starting to work to a general Print function; for now it will generate PDF files; currently it works only for one object not for a selection
- added shortcut key Ctrl+P for printing to PDF method

18.12.2019

- added new parameters to improve Gerber parsing
- small optimizations in the Preferences UI
- the Jump To function reference is now saving it's last used value
- added the ability to use the Jump To method in the Gerber Editor
- improved the loading of Config File by using the advanced code editor
- fixed a bug in the new feature 'extra buffering'
- fixed the creation of CNCJob objects out of multigeo Geometry objects (objects with multiple tools)
- optimized the NCC Tool

17.12.2019

- more optimizations in NCC Tool
- optimizations in Paint Tool
- maximum range for Cut Z is now zero to deal with the situation when using V-shape with tip-dia same value with cut width
- modified QValidator in FCDoubleSpinner() GUI element to allow entering the minus sign when the range maximum is set as 0.0; also for positive numbers allowed entering the symbol plus
- made sure that if in Gerber UI the isolation is made with a V-Shape tool then the tool type is automatically updated on the generated Geometry Object
- added ability to save the Source File as PDF (still have to adjust the page size)
- fixed the generate_from_geometry_2() method to use the default values in case the parameters are None
- added ability to save the Source File as PDF - fixed page size and added line breaks
- more mods to generate_from_geometry_2() method
- fixed bug saving the FlatCAM project saying the file is used by another application
- fixed issue #347 - a Gerber generated by Sprint Layout with copper pour ON will not have rendered the copper pour

16.12.2019

- in Geometry Editor added support for Jump To function such as that it works within the Editor Tools themselves. For now it works only in absolute jumps
- modified the Jump To method such that now allows relative jump from the current mouse location
- fixed the Defaults upgrade overwriting the new version number with the old one
- fixed issue with clear_polygon3() - the one who makes 'lines' and fixed the NCC Tool
- some small changes in the GeometryObject.on_tool_add() method
- made sure that in Geometry Editor the self.app.mouse attribute is updated with the current mouse position (x, y)
- updated the preprocessor files
- fixed the HPGL preprocessor
- fixed the CNCJob geometry created with HPGL preprocessor
- fixed GCode generated with HPGL preprocessor to output only integer coordinates
- fixed the HPGL2 import parsing for absolute linear movements
- fixed the line endings for setup_ubuntu.sh

15.12.2019

- fixed a bug that created a crash in special conditions; it's related to the QSettings in FlatCAMGui.py
- added a script to remove the bad profiles from resource pictures. From here: https://stackoverflow.com/questions/22745076/libpng-warning-iccp-known-incorrect-srgb-profile/43415650, link mentioned by @camellan (Andrey Kultyapov)
- prepared the application for usage of dark icons in case of using the dark theme
- updated the languages
- fixed a typo
- fixed layout on first launch of the app
- fixed some issues with the recent preparation for dark icons resource usage
- added a new preprocessor file contributed by Daniel Friderich and added fixes for it
- modified the export_gcode() method and the preprocessors such that the preprocessors now have the information if to include the gcode header
- updated all the translation PO files and the POT file
- RELEASE 8.99

14.12.2019

- finished the strings update in the Google-translated Spanish
- finished the strings update in the Google-translated French

13.12.2019

- HPGL2 import: added support for circles, arcs and 3-point arcs. Everything works only for absolute coordinates.
- removed the .plt extension from Gcode extensions
- some strings updated; update on the Romanian translate
- more strings updated; finished the Romanian translation update
- some work in updating the Spanish Google-translation
- small updates (Google Translate) in Russian and Brazilian-PT languages

12.12.2019

- finished the Calibration Tool
- changed the Scale Entry in Object UI to FCEntry() GUI element in order to allow expressions to be entered. E.g: 1/25.4
- some small changes in the Scale button handler in FlatCAMObj() class
- added option to save objects as PDF files in File -> Save menu
- optimized the GerberObject.clear_plot_apertures() method
- some changes in the ObjectUI and for the Geometry UI
- finished a very rough and limited HPGL2 file import 

11.12.2019

- started work in HPGL2 parser
- some more work in Calibration Tool

10.12.2019

- small changes in the Geometry UI
- now extracut option in the Geometry Object will recut as many points as many they are within the specified re-cut length
- if extracut_length is zero then the extracut will cut up until the first point in path no matter what the distance is
- in Gerber isolation, when selection mode is checked, now area selection works too
- in CNCJob UI, now the CNCJob objects made out of Excellon objects will display their CNC tools (drill bits)
- fixed a cumulative error when using the Tool Offset for Excellon objects
- added the display of the real depth of cut (cut z + offset_z) for CNC tools made out of an Excellon object
- for OpenGL graphic mode added a fit_view() execution on canvas initialization
- fixed Excellon scaling the UI values
- replaced the SpindleSpeed entry with a FCSpinner() GUI element; if speed is set to 0 it will amount to None

9.12.2019 

- updated the border for fit view on OpenGL graphic mode
- Calibration Tool - added preferences values
- Calibration Tool - more work on it
- reverted this change: "selected object in Project used to ask twice for UI build" because it will not build the UI when a tab is closed for Document object and the object is selected
- fixed issue after Geometry object edit; the GCode made from an edited object did not reflect the changes in the object
- in Object UI, the Scale FCDoubleSpinner will no longer work for Return key press due of issues of unwanted scaling on focusOut event
- in GeometryObject fixed the scale and offset methods to always process the self.solid_geometry
- Calibration Tool - finished the calibrated object creation method
- updated the POT file
- fixed an error in the German PO file
- updated the languages PO files
- some fixes on the app.jump_to() method
- made sure that the ToolFilm will not start saving a file if there are no objects loaded
- some fixes on the app.jump_to() method for the Legacy(2D) graphic mode

8.12.2019

- Calibrate Tool - rearranged the GUI
- in Geometry UI made sure that the Label that points to the Tool parameters show clearly that those parameters apply only for the selected tool
- fixed an small issue in Object UI
- small fixes: selected object in Project used to ask twice for UI build; if scale factor in Object UI is 1 do nothing as there is no point in scaling with a factor of 1
- in Geometry UI added a button that allow updating all the tools in the Tool Table with the current values in the UI form
- updated Tcl commands to make use of either 0 or False for False value or 1 or True for True in case of a parameter with type Bool

7.12.2019 

- renamed Calibrate Excellon Tool to a simpler Calibrate Tool
- Calibrate Tool - when generating verification GCode it will always load into an Editor from which it can be edited and/or saved. On save the editor will close.
- updated the CNCJob and Drillcncjob Tcl Commands to use 0 and 1 as values for the parameters that are stated as of bool type, beside the normal keywords of False and True
- Calibrate Tool - working on it

6.12.2019

- fixed the toggle_units() method so now the grid values are accurate to the decimal
- cleaned up the Excellon parser and fixed some bugs (old and new); Excellon parser has it's own convert_units() method no longer inheriting from Geometry
- in Excellon UI fixed bug that did not allow editing of the Offset Z parameter from the Tool table
- in Properties Tool added new information's for the tools in the CNCjob objects
- few bugs solved regarding the newly created empty objects
- changed everywhere the name "preprocessor" with "preprocessor"
- updated the preprocessor files in the toolchange section in order to avoid a graphical representation of travel lines glitch
- fixed a GUI glitch in the Excellon tool table
- added units to some of the parameters in the Properties Tool

5.12.2019 

- in NCC Tool, the new Geometry object that is created on copper clear now has the solid_geometry attribute where the geometry is stored not only in the obj.tools attribute
- Copper Thieving Tool - added units label for the pattern plated area
- Properties Tool - added a new parameter, the copper area which show the area of the copper features for the Gerber objects
- Copper Thieving Tool - added a default value for the mask clearance when generating pattern plating mask
- application wide change: introduced the precision parameters in Edit -> Preferences who will control how many decimals to use in the app parameters
- changed the FCDoubleSpinner, FCSpinner and FCEntry GUI elements to allow passing an alignment value: left, right or center (not yet available in the app)
- fixed the GUI of the Geometry Editor Tool Transform and adapted it to use the precision setting
- updated Gerber Editor to use the precision setting and the Gerber Editor Transform Tool to use the FCDoubleSpinner GUI element
- in Properties Tool added more information's regarding the Excellon tools, about travelled distance and job time; fixed issues when doing Properties on the CNCjob objects
- TODO: I need to solve the mess in units conversion: it's too convoluted 

4.12.2019 

- made sure that if an older preferences file is detected then there are no errors and only the parameters that are currently active are loaded; the factory defaults file is deleted and recreated in the new format
- in Preferences added a new button: 'Close' to close the Preferences window without saving
- fixed bug in FCSpinner and FCDoubleSpinner GUI elements, that are now the main GUI element in FlatCAM, that made partial selection difficult
- updated the Paint Tool in Geometry Editor to use the FCDoubleSpinner
- added the possibility for suffix presence on the FCSpinner and FCDoubleSpinner GUI Elements
- added the '%' symbol for overlap fields; I still need to divide the content by 100 to get the original (0 ... 1) value
- fixed the overlap parameter all over the app to reflect the change to percentage
- in Copper Thieving Tool added the display of the patterned plated area (approximate area) 
- Copper Thieving Tool - updated the way plated area is calculated making it a bit more precise but still it is a bit bigger than the actual area
- fixed the Copy Object function to copy also the source_file content
- Copper Thieving Tool - when the clearance value for the pattern plating mask is negative it will be applied to the origin soldermask too
- modified the GUI in all tools making the text of the buttons bold and adding a new button named Reset Tool which have to reset the tool GUI and variables (need to check all tools to see if happen)
- when the Tool tab is in focus, clicking on canvas will no longer change the focus to Project tab
- Copper Thieving Tool - when creating the pattern platting mask will make a new Gerber object with it
- small fix in the GUI layout in Gerber Editor

3.12.2019

- in Preferences added an Apply button which apply the modified preferences but does not save to a file, minimizing the file IO operations; Ctrl+S key combo does the Apply now.
- updated some of the default values to metric, values that were missed previously
- remade the Gerber Editor way to import an Gerber object into the editor in such a way to use the multiprocessing
- various small fixes
- fix for toggle grid lines updating canvas only after moving the mouse (hack, actually)
- some changes in the UI layout in Cutout Tool
- added some geometry parameters in Cutout Tool as a convenience, to be passed to the generated Geometry objects

2.12.2019

- fixed issue #343; updated the Image Tool
- improvements in Importing SVG as Gerber - added an automatic source generation (it is not infallible)
- a hack to import correctly the QRCode exported as SVG from FlatCAM
- added 3 new tcl commands: export dxf, export excellon and export gerber
- added a Cancel button in Tools DB when requesting to add a tool in the Geometry Tool Table
- modified the default values for the METRIC system; the app now starts in the METRIC units since the majority of the world use the METRIC units system
- small changes, updated the estimated release date
- Tool Copper Thieving - added pattern plating mask generation feature

28.11.2019

- small fixes in NCC Tool and in the GeometryObject class

27.11.2019

- in Tool Film added the page size and page orientation in case of saving the film as PDF file
- the application workspace has now a lot more options selectable in the Edit -> Preferences -> General -> GUI Preferences
- updated the drawing of the workspace such that the application overall start time is improved and after first turn on of the workspace, toggling it will have no performance penalty
- updated the workspace functions to work in Legacy(2D) graphic mode
- adjusted the selection color transparency for the Legacy(2D) graphic mode because it was too transparent for the fill

26.11.2019

- updated the Film Tool to allow exporting PDF and PNG file (besides the SVG file)

25.11.2019

- In Gerber isolation changed the UI
- in Gerber isolation added the option to selectively isolate only certain polygons
- made some optimizations in GerberObject.isolate() method
- updated the 'single' isolation of Gerber polygons to remove the polygon if clicked on it and it is already in the list of single polygons to be isolated
- clicking to add a polygon when doing Single type isolation will add a blue shape marking the selected polygon, second click will remove that shape
- fixed bugs in Paint Tool when painting single polygon
- in Gerber isolation added the option to selectively isolate only certain polygons - made it to work for Legacy(2D) graphic mode
- remade the Paint Tool - single polygon painting; now it can single paint a list of polygons that are clicked onto (right click will start the actual painting)

23.11.2019

- in Tool Fiducials added a new fiducial type: chess pattern
- work in Calibrate Excellon Tool
- fixed the line numbers in the TextPlainEdit to fit all digits of the line number; activated the line numbers for ScriptObject objects too
- line numbers in the TextPlainEdit for the selected line are bold
- made sure that the self.defaults dictionary is deepcopy-ed in the self.options dictionary
- made sure that the units are read from the self.defaults and not from the GUI
- added Robber Bar option to Copper Thieving Tool

22.11.2019

- Tool Fiducials - added GUI in Preferences and entries in self.defaults dict
- Tool Fiducials - updated the source_file object for the modified Gerber files
- working on adding line numbers to the TextPlainEdit
- GCode view now has line numbers
- solved a bug that made selection of objects on canvas impossible if there is an object of type ScriptObject or DocumentObject opened

21.11.2019

- Tool Fiducials - finished the part with adding copper fiducials: manual and auto
- Tool Fiducials - added choice of shapes: circular or non-standard cross
- Tool Fiducials - finished the work on adding soldermask openings
- Tool Fiducials - finished the tool
- updated requirements.txt and setup_ubuntu.sh files

20.11.2019

- Tool Fiducials - added the GUI and the shortcut key
- Tool Fiducials - updated the icon

19.11.2019

- removed the f-strings replacing them with the traditional string formatting due of not being supported by older versions of Python 3
- fixed some TclCommands: MillDrills and OpenGerber
- fixed bug in Tool Subtract that did not allow subtracting Gerber objects
- starting to work on Tool Fiducials - created the file

18.11.2019

- finished the Dots and Squares options in the Copper Thieving Tool
- working on the Lines option in Copper Thieving Tool
- finished the Lines option in the Copper Thieving Tool; still have to add threading to maximize performance
- finished Copper Thieving Tool improvements
- working on the Calibrate Excellon Tool - remade the UI

17.11.2019

- optimized the storage of the Gerber mark shapes by making them one layer only
- optimized the Distance Tool such that the distance utility geometry will be shown even when the mark shapes are plotted.
- updated the make_freezed.py file to make sure that all the required files are included
- updated the setup_ubuntu.sh to include the sudo command (courtesy of Krishna Torque on bitbucket)

16.11.2019

- fixed issue #341 that affected both preprocessors that have inlined feedrate: marlin and repetier. The used feedrate was the Feedrate X-Y and instead had to be Feedrate Z.

15.11.2019

- added all the recognized extensions to the save dialog filters; latest extension used will be preselected next time a save operation occur
- fixed issue #335. The FCDoubleSPinBox (and FCSpinBox) value was not used when the user entered data but just hovered away the mouse expecting the data to be already confirmed
- converted setup_ubuntu.sh to Linux line endings

14.11.2019

- made sure that the 'default' preprocessor file is always loaded first such that this name is always first in the GUI comboboxes
- added a class in GUIElements for a TextEdit box with line numbers and highlight

13.11.2019

- trying to improve the performance of View CNC Code command by using QPlainTextEdit; made the mods for it
- when using the Find function in the AppTextEditor and the result reach the bottom of the document, the next find will be the first in the document (before it defaulted to the beginning of the document)
- finished improving the show of text files in FlatCAM (CNC Code, Source files)
- fixed an issue in the FlatCAMObj.GerberObject.convert_units() which needed to be updated after changes elsewhere

12.11.2019

- added two new preprocessor files for ISEL CNC and for BERTA CNC
- clicking on a FCTable GUI element empty space will also clear the focus now

11.11.2019

- in Tools Database added a contextual menu to add/copy/delete tool; Ctrl+C, DEL keys work too; key T for adding a tool is now only partially working
- in Tools Database made the status bar messages show when adding/copying/deleting tools in DB
- changed all Except statements that were single to except Exception as recommended in some PEP
- renamed the Copper Fill Tool to Copper Thieving Tool as this is a more appropriate name; started to add ability for more types of copper thieving besides solid
- fixed some issues recently introduced in ParseSVG
- updated POT file
- fixed GUI in 2Sided Tool
- extending the Copper Thieving Tool - wip

9.11.2019

- fixed a new bug that did not allow to open the FlatCAM Preferences files by doubleclick in Windows
- added a new feature: Tools Database for Geometry objects; resolved issue #308
- added tooltips for the Tools Database table headers and buttons

8.11.2019

- updated the make file for frozen executable

7.11.2019

- added the '.ngc' file extension to the GCode Save file dialog filter
- made the 'M2' Gcode command footer optional, default is False (can be set using the TclCommand: set_sys cncjob_footer True)
- added a setting in Preferences to force the GCode output to have the Windows line-endings even for non-Windows OS's

6.11.2019

- the "CRTL+S" key combo when the Preferences Tab is in focus will save the Preferences instead of saving the Project
- fixed bug in the Paint Tool that did not allow choosing a Paint Method that was not Standard
- made sure that in the GeometryObject.merge() all the source data is deepcopy-ed in the final object
- the font color of the Preferences tab will change to red if settings are not saved and it will revert to default when saved
- fixed issue #333. The Geometry Editor Paint tool was not working and using it resulted in an error

5.11.2019

- added a new setting named 'Allow Machinist Unsafe Settings' that will allow the Travel Z and Cut Z to take both positive and negative values
- fixed some issues when editing a multigeo geometry

4.11.2019

- wip
- getting rid of all the Options GUI and related functions as it is no longer supported
- updated the UI in Geometry UI
- optimized the order of the defaults storage declaration and the update of the Preferences GUI from the defaults
- started to add a Tool Database

3.11.2019

- fixed the V-shape tool diameter calculation in NCC Tool
- in NCC Tool made the new tool dia (circular type) a parameter in Preferences
- fixed a small issue with clicking in a disabled FCDoubleSpinner or FCSpinner still doing a selection

30.10.2019

- converted SolderPaste Tool to usage of SpinBoxes; changed the SolderPaste Tool UI in Preferences too
- fixed a bug in SolderPaste Tool that did not allow to view the GCode

29.10.2019

- a bug fix in Geometry Object
- fixed some missing properties in Tool Calculators

28.10.2019

- in Tools: Paint, NCC and Copper Fill, when using the Area Selection, now the selected areas will stay drawn as markers until the user click RMB
- in legacy2D graphic engine, adding an utility geometry no longer draw the older ones, overwriting them
- fixed some issues in the Gerber Editor (Aperture add was double adding an aperture)
- converted Gerber Editor to usage of SpinBoxes
- working on the Calibrate Excellon Tool
- converted Excellon Editor to usage of SpinBoxes
- Calibrate Excellon Tool: working on self.calculate_factors() method

27.10.2019

- Copper Fill Tool: some PEP8 corrections

26.10.2019

- fixed an error in the FCDoubleSpinner class when FlatCAM is run on system with locale that use the comma as decimal separator

25.10.2019

- QRCode Tool: added ability to add negative QRCodes (perhaps they can be isolated on copper?); added a clear area surrounding the QRCode in case it is dropped on a copper pour (region); fixed the Gerber export
- QRCode Tool: all parameters are hard-coded for now
- small update
- fixed imports in all TclCommands
- fixed the requirements.txt and setup_ubuntu.sh files
- QRCode Tool: change the plot method parameter
- QRCode Tool: added ability to save the generated QRCode as SVG file or PNG file
- QRCode Tool: added the feature to save the PNG file with transparent background
- QRCode Tool: added GUI category in Preferences window
- QRCode Tool: shortcut key for this tool is now Alt+Q while PDF import Tool was relegated to Ctrl+Q combo key shortcut
- added a new FlatCAM Tool: Copper Fill Tool. It will pour copper into a Gerber filling all empty space with copper, at a clearance distance of the Gerber features
- Copper Fill Tool: added possibility to select between a bounding box rectangular or convex hull when the reference is the geometry of the source Gerber object
- Copper Fill Tool: cleanup on not regular tool exit
- Copper Fill Tool: added GUI category in Edit -> Preferences window
- QRCode Tool: added a selection limit parameter to control the selection shape vs utility geo

24.10.2019

- added some placeholder texts in the TextBoxes.
- working on QRCode Tool; added the utility geometry and initial functional layout
- working on QRCode Tool; finished adding the QRCode geometry to the selected Gerber object and also finished adding the 'follow' geometry needed when exporting the Gerber object as a Gerber file in addition to the 'solid' geometry in the obj.apertures
- working on QRCode Tool; finished offsetting the geometry both in apertures and in solid_geometry; updated the source_file of the source object

23.10.2019

- QRCode Tool - a SVG object is generated and plotted on screen having the QRCode data
- fixed an import error in Distance Tool
- fixed the Toggle Grid Lines functionality

22.10.2019

- working on the Calibrate Excellon Tool
- finished the GUI layout for the Calibrate Excellon Tool
- start working on QRCode Tool - not working yet
- start working on QRCode Tool - searching for alternatives

21.10.2019

- the context menu for the Tabs in notebook and PlotTabArea is launched now on right mouse click on tabs themselves
- fixed an error when trying to view the source file and there is no object selected
- updated the Objects menu signals so whenever an object is (de)selected in the Project Tab, it's state will reflect the (un)checked state of the actions in the Object menu
- fixed issue in Gerber Object UI of not updating the value of CutZ entry on TipDia or TipAngle entries change. Fixed issue #324

18.10.2019

- fixed a small bug in BETA status change
- updated the About FlatCAM window
- reverted change in tool dia being able to take only positive values in Gerber Object UI
- started to work to a new tool: Calibrate Excellon Tool
- solved the issue #329

18.10.2019

- finished the update on the Google translated Spanish translation.
- updated the new objects icons for Gerber, Geometry and Excellon
- small import problem fixed
- RELEASE 8.98

17.10.2019

- fixed a bug in milling holes due of a message wrongly formatted
- added an translator email address
- finished the update on German Google translation. Part of it was corrected by Jens Karstedt
- finished the update of the Romanian translation.
- finished the Objects menu by adding the ability of actions to be checked so they will show the selected status of the objects and by adding to actions to (de)select all objects
- fixed and optimized the click selection on canvas
- fixed Gerber parsing for very simple Gerber files that have only one Polygon but many LPC zones
- fixed SVG export; fix bug #327
- finished the update on French Google translation.

16.10.2019

- small update to Romanian translation files

15.10.2019

- adjusted the layout in NCC Tool
- fixed bug in Panelization Tool for which in case of Excellon objects, the panel kept a reference to the source object which created issues when moving or disabling/enabling the plots
- cleaned up the module imports throughout the app (the TclCommands are not yet verified)
- removed the styling on the comboboxes cellWidget's in the Tool Tables
- replaced some of the icons that did not looked Ok on the dark theme
- added a new toolbar button for the Copy object functionality
- changed the Panelize tool icon
- corrected some strings

14.10.2019

- modified the result highlight color in Check Rules Tool
- added the Check Rules Tool parameters to the unit conversion list
- converted more of the Preferences entries to FCDoubleSpinner and FCSpinner
- converted all ObjectUI entries to FCDoubleSpinner and FCSpinner
- updated the translation files (~ 89% translation level)
- changed the splash screen as it seems that FlatCAM beta will never be more than beta
- changed some of the signals from returnPressed to editingFinished due of now using the SpinBoxes
- fixed an issue that caused the impossibility to load a GCode file that contained the % symbol even when was loaded in a regular way from the File menu
- re-added the CNC tool diameter entry for the CNCjob object in Selected tab.FCSpinner
- since the CNCjob geometry creation is only useful for graphical purposes and have no impact on the GCode creation I have removed the cascaded union on the GCode geometry therefore speeding up the Gcode display by many factors (perhaps hundreds of times faster)
- added a secondary link in the bookmark manager
- fixed the bookmark manager order of bookmark links; first two links are always protected from deletion or drag-and-drop to other positions
- fixed a whole load of PyQT signal problems generated by recent changes to the usage of SpinBoxes; added a signal returnPressed for the FCSpinner and for FCDoubleSpinner
- fixed issue in Paint Tool where the first added tool was expected to have a float diameter but it was a string
- updated the translation files to the latest state in the app

13.10.2019

- fixed a bug in the Merge functions
- fixed the Export PNG function when using the 2D legacy graphic engine
- added a new capability to toggle the grid lines for both graphic engines: menu link in View and key shortcut combo Alt+G
- changed the grid colors for 3D graphic engine when in Dark mode
- enhanced the Tool Film adding the Film adjustments and added the GUI in Preferences
- set the GUI layout in Preferences for a new category named Tools 2
- added the Preferences for Check Rules Tool and for Optimal Tool and also updated the Film Tool to use the default settings in Preferences

12.10.2019

- fixed the Gerber Parser convert units unnecessary usage. The only units conversion should be done when creating the new object, after the parsing
- more fixes in Rules Check Tool
- optimized the Move Tool
- added support for key-based panning in 3D graphic engine. Moving the mouse wheel while pressing the CTRL key will pan up-down and while pressing SHIFT key will pan left-right
- fixed a bug in NCC Tool and start trying to make the App responsive while the NCC tool is run in a non-threaded way
- fixed a GUI bug with the QMenuBar recently introduced

11.10.2019

- added a Bookmark Manager and a Bookmark menu in the Help Menu
- added an initial support for rows drag and drop in FCTable in GUIElements; it crashes for CellWidgets for now, if CellWidgetsare in the table rows
- fixed some issues in the Bookmark Manager
- modified the Bookmark manager to be installed as a widget tab in Plot Area; fixed the drag & drop function for the table rows that have CellWidgets inside
- marked in gray color the rows in the Bookmark Manager table that will populate the BookMark menu
- made sure that only one instance of the BookmarkManager class is active at one time

10.10.2019

- fixed Tool Move to work only for objects that are selected but also plotted, therefore disabled objects will not be moved even if selected

9.10.2019

- updated the Rules Check Tool - solved some issues
- made FCDoubleSpinner to use either comma or dot as a decimal separator
- fixed the FCDoubleSpinner to only allow the amount of decimals already set with set_precision()
- fixed ToolPanelize to use FCDoubleSpinner in some places

8.10.2019

- modified the FCSpinner and FCDoubleSpinner GUI elements such that the wheel event will not change the values inside unless there is a focus in the lineedit of the SpinBox
- in Preferences General, Gerber, Geometry, Excellon, CNCJob sections made all the input fields of type SpinBox (where possible)
- updated the Distance Tool utility geometry color to adapt to the dark theme canvas
- Toggle Code Editor now works as expected even when the user is closing the Editor tab and not using the command Toggle Code Editor
- more changes in Preferences GUI, replacing the FCEntries with Spinners
- some small fixes in toggle units conversion
- small GUI changes

7.10.2019

- fixed an conflict in a signal usage that was triggered by Tool SolderPaste when a new project was created
- updated Optimal Tool to display both points coordinates that made a distance (and the minimum) not only the middle point (which is still the place where the jump happen)
- added a dark theme to FlatCAM (only for canvas). The selection is done in Edit -> Preferences -> General -> GUI Settings
- updated the .POT file and worked a bit in the romanian translation
- small changes: reduced the thickness of the axis in 3D mode from 3 pixels to 1 pixel
- made sure that is the text in the source file of a DocumentObject is HTML is loaded as such
- added inverted icons

6.10.2019

- remade the Mark area Tool in Gerber Editor to be able to clear the markings and also to delete the marked polygons (Gerber apertures)
- working in adding to the Optimal Tool the rest of the distances found in the Gerber and the locations associated; added GUI
- added display of the results for the Rules Check Tool in a formatted way
- made the Rules Check Tool document window Read Only
- made Excellon and Gerber classes from camlib into their own files in the flatcamParser folder
- moved the ApertureMacro class from camlib to ParseGerber file
- moved back the ApertureMacro class to camlib for now and made some import changes in the new ParseGerber and ParseExcellon classes
- some changes to the tests - perhaps I will try adding a few tests in the future
- changed the Jump To icon and reverted some changes to the parseGerber and ParseExcellon classes
- updated Tool Optimal with display of all distances (and locations of the middle point between where they happen) found in the Gerber Object

5.10.2019

- remade the Tool Calculators to use the QSpinBox in order to simplify the user interaction and remove possible errors
- remade: Tool Cutout, Tool 2Sided, Tool Image, Panelize Tool, NCC Tool, Paint Tool  to use the QSpinBox GUI elements
- optimized the Transformation Tool both in GUI and in functionality and replaced the entries with QSpinBox
- fixed an issue with the tool table context menu in Paint Tool
- made some changes in the GUI in Paint Tool, NCC Tool and SolderPaste Tool
- changed some of the icons; added attributions for icons source in the About FlatCAM window
- added a new tool in the Geometry Editor named Explode which is the opposite of Union Tool: it will explode the polygons into lines

4.10.2019

- updated the Film Tool and added the ability to generate Punched Positive films (holes in the pads) when a Gerber file is the film's source. The punch holes source can be either an Excellon file or the pads center
- optimized Rules Check Tool so it runs faster when doing Copper 2 Copper rule
- small GUI changes in Optimal Tool and in Film Tool
- some PEP8 corrections
- some code annotations to make it easier to navigate in the MainGUI.py
- fixed exit FullScreen with Escape key
- added a new menu category in the MenuBar named 'Objects'. It will hold the objects found in the Project tab. Useful when working in FullScreen
- disabled a log.debug in ObjectColection.get_by_name()
- added a Toggle Notebook button named 'NB' in the QMenBar which toggle the notebook
- in Gerber isolation section, the tool dia value is updated when changing from Circular to V-shape and reverse
- in Tool Film, when punching holes in a positive film, if the resulting object geometry is the same as the source object geometry, the film will not ge generated
- fixed a bug that when a Gerber object is edited and it has as solid_geometry a single Polygon, saving the result was failing due of len() function not working on a single Polygon
- added the Distance Tool, Distance Min Tool, Jump To and Set Origin functions to the Edit Toolbar

3.10.2019

- previously I've added the initial layout for the DocumentObject object
- added more editing features in the Selected Tab for the DocumentObject object

2.10.2019

- fixed bug in Geometry Editor that did not allow the copy of geometric elements
- created a new class that holds all the Code Editor functionality and integrated as a Editor in FlatCAM, the location is in flatcamEditors folder
- remade all the functions for view_source, scripts and view_code to use the new AppTextEditor class; now all the Code Editor tabs are being kept alive, before only one could be in an open state
- changed the name of the new object FlatCAMNotes to a more general one DocumentObject
- changed the way a new ScriptObject object is made, the method that is processing the Tcl commands when the Run button is clicked is moved to the FlatCAMObj.ScriptObject() class
- reused the Multiprocessing Pool declared in the App for the ToolRulesCheck() class
- adapted the Project context menu for the new types of FLatCAM objects
- modified the setup_recent_files to accommodate the new FlatCAM objects
- made sure that when an ScriptObject object is deleted, it's associated Tab is closed
- fixed the FlatCMAScript object saving when project is saved (loading a project with this script object is not working yet)
- fixed the FlatCMAScript object when loading it from a project

1.10.2019

- fixed the FCSpinner and FCDoubleSpinner GUI elements to select all on first click and deselect on second click in the Spinbox LineEdit
- for Gerber object in Selected Tab added ability to chose a V-Shape tool and therefore control the isolation better by adjusting the cut width of the isolation in function of the cut depth, tip width of the tool and the tip angle of the tool
- when in Gerber UI is selected the V-Shape tool, all those parameters (tip dia, tip angle, tool_type = 'V' and cut Z) are transferred to the generated Geometry and prefilled in the Geoemtry UI
- added a fix in the Gerber parser to work even when there is no information about zero suppression in the Gerber file
- added new settings in Edit -> Preferences -> Gerber for Gerber Units and Gerber Zeros to be used as defaults in case that those informations are missing from the Gerber file
- added new settings for the Gerber newly introduced feature to isolate with the V-Shape tools (tip dia, tip angle, tool_type and cut Z) in Edit -> Preferences -> Gerber Advanced
- made those settings just added for Gerber, to be updated on object creation
- added the Geo Tolerance parameter to those that are converted from MM to INCH
- added two new FlatCAM objects: ScriptObject and FlatCAMNotes

30.09.2019

- modified the Distance Tool such that the number of decimals all over the tool is set in one place by the self.decimals
- added a new tool named Minimum Distance Tool who will calculate the minimum distance between two objects; key shortcut: SHIFT + M
- finished the Minimum Distance Tool in case of using it at the object level (not in Editors)
- completed the Minimum Distance Tool by adding the usage in Editors
- made the Minimum Distance Tool more precise for the Excellon Editor since in the Excellon Editor the holes shape are represented as a cross line but in reality they should be evaluated as circles
- small change in the UI layout for Check Rules Tool by adding a new rule (Check trace size)
- changed a tooltip in Optimal Tool
- in Optimal Tool added display of how frequent that minimum distance is found
- in Tool Distance and Tool Minimal Distance made the entry fields read-only
- in Optimal Tool added the display of the locations where the minimum distance was detected
- added support to use Multi Processing (multi core usage, not simple threading) in Rules Check Tool
- in Rules Check Tool added the functionality for the following rules: Hole Size, Trace Size, Hole to Hole Clearance
- in Rules Check Tool added the functionality for Copper to Copper Clearance
- in Rules Check Tool added the functionality for Copper to Outline Clearance, Silk to Silk Clearance, Silk to Solder Mask Clearance, Silk to Outline Clearance, Minimum Solder Mask Sliver, Minimum Annular Ring
- fixes to cover all possible situations for the Minimum Annular Ring Rule in Rules Check Tool
- some fixes in Rules Check Tool and added a QSignal that is fired at the end of the job

29.09.2019

- work done for the GUI layout of the Rule Check Tool
- setup signals in the Rules Check Tool GUI
- changed the name of the Measurement Tool to Distance Tool. Moved it's location to the Edit Menu
- added Angle parameter which is continuously updated to the Distance Tool

28.09.2019

- changed the icon for Open Script and reused it for the Check Rules Tool
- added a new tool named "Optimal Tool" which will determine the minimum distance between the copper features for a Gerber object, in fact determining the maximum diameter for a isolation tool that can be used for a complete isolation
- fixed the ToolMeasurement geometry not being displayed
- fixed a bug in Excellon Editor that crashed the app when editing the first tool added automatically into a new black Excellon file
- made sure that if the big mouse cursor is selected, the utility geometry in Excellon Editor has a thicker line width (2 pixels now) so it is visible over the geometry of the mouse cursor
- fixed issue #319 where generating a CNCJob from a geometry made with NCC Tool made the app crash; also #328 which is the same
- replaced in FlatCAM Tools and in FLatCAMObj.py  and in Editors all references to hardcoded decimals in string formats for tools with a variable declared in the __init__()
- fixed a small bug that made app crash when the splash screen is disabled: it was trying to close it without being open

27.09.2019

- optimized the toggle axis command
- added possibility of using a big mouse cursor or a small mouse cursor. The big mouse cursor is made from 2 infinite lines. This was implemented for both graphic engines
- added ability to change the cursor size when the small mouse cursor is selected in Preferences -> General
- removed the line that remove the spaces from the path parameter in the Tcl commands that open something (Gerber, Gcode, Excellon)
- fixed issue with the old SysTray icon not hidden when the application is restarted programmatically
- if an object is edited but the result is not saved, the app will reload the edited object UI and set the Selected tab as active
- made the mouse cursor (big, small) change in real time for both graphic engines
- started to work on a new FlatCAM tool: Rules Check
- created the GUI for the Rule Check Tool
- if there are (x, y) coordinates in the clipboard, when launching the "Jump to" function, those coordinates will be preloaded in the Dialog box.
- when the combo SHIFT + LMB is executed there is no longer a deselection of objects
- when the "Jump to" function is called, the mouse cursor (if active) will be moved to the new position and the screen position labels will be updated accordingly


27.09.2019

- RELEASE FlatCAM 8.97

26.09.2019

- added a Copy All button in the Code Editor, clicking this button will copy all text in the editor to the clipboard
- added a 'Milling Type' radio button in Geometry Editor Preferences to contorl the type of geometry will be generated in the Geo Editor (for conventional milling or for the climb milling)
- added the functionality to allow climb/conventional milling selection for the geometry created in the Geometry Editor
- now any Geometry that is edited in Geometry editor will have coordinates ordered such that the resulting Gcode will allow the selected milling type in the 'Milling Type' radio button in Geometry Editor Preferences (which depends also of the spindle direction)
- some strings update
- French Google-translation at 100%
- German Google-translation update to 100%
- updated the other languages and the .POT file
- changed some strings (that should not have been included for translation) and updated language files and the .POT file
- fixed issue when rebooting from within in cx_freezed state (it issued a startup arg with the path to FlatCAM.exe but that triggered the last sys.exit(2) that I had in the App.args_at_startup())
- modified the make_win script for the presence of MatPlotLib

25.09.2019

- French translation at 33%
- fixed the 'Jump To' function to work in legacy graphic engine
- in legacy graphic engine fixed the mouse cursor shape when grid snapping is ON, such that it fits with the shape from the OpenGL graphic engine
- in legacy graphic engine fixed the axis toggle
- French Google-translation at 48%

24.09.2019

- fixed the fullscreen method to show the application window in fullscreen wherever the mouse pointer it is therefore on the screen we are working on; before it was showing always on the primary screen
- fixed setup_ubuntu.sh to include the matplotlib package required by the Legacy (2D) graphic engine
- in legacy graphic engine, fixed issue where immediately after changing the mouse cursor snapping the mouse cursor shape was not updated
- in legacy graphic engine, fixed issue where while zooming the mouse cursor shape was not updated
- in legacy graphic engine, fixed issue where immediately after panning finished the mouse cursor shape was not updated
- unfortunately the fix for issue where while zooming the mouse cursor shape was not updated braked something in way that Matplotlib work with PyQt5, therefore I removed it
- fixed a bug in legacy graphic engine: when doing the self.app.collection.delete_all() in new_project an app crash occurred
- implemented the Annotation change in CNCJob Selected Tab for the legacy graphic engine

23.09.2019

- in legacy graphic engine, fixed bug that made the old object disappear when a new object was loaded
- in legacy graphic engine, fixed bug that crashed the app when creating a new project
- in legacy graphic engine, fixed a bug that when deleting an object all objects where deleted
- added a new TclCommand named "set_origin" which will set the origin for all loaded objects to zero if the -auto True argument is used and to a certain x,y location if the format is: set_origin 5,7
- added a new TclCommand named "bounds" which will return a list of bounds values from a supplied list of objects names. For use in Tcl Scripts
- updated strings in the translations and the .POT file
- added the new keywords to the default keywords list
- fixed the FullScreen option not working for the 3D graphic engine (due bug of Qt5 when OpenGL window is fullscreen) by creating a sort of fullscreen
- added a final fix that allow full coverage of the screen in FullScreen in Windows and still the menus are working
- optimized the Gerber mark shapes display
- fixed a color format bug in Tool Move for 3D engine
- made sure that when the Tool Move is used on a Gerber file with mark shapes active, those mark shapes are deleted before the actual move
- in legacy graphic engine, fixed issue with Delete shortcut key trying to delete twice
- 26% in Google-translated French translation and updated some strings too

22.09.2019

- fixed zoom directions legacy graphic engine (previous commit)
- fixed display of MultiGeo geometries in legacy graphic engine
- fixed Paint tool to work in legacy graphic engine
- fixed CutOut Tool to work in legacy graphic engine
- fixed display of distance labels and code optimizations in ToolPaint and NCC Tool
- adjusted axis at startup for legacy graphic engine plotcanvas
- when the graphic engine is changed in Edit -> Preferences -> General -> App Preferences, the application will restart
- made hover shapes work in legacy graphic engine
- fixed bug in display of the apertures marked in the Aperture table found in the Gerber Selected tab and through this made it to also work with the legacy graphic engine
- fixed annotation in Mark Area Tool in Gerber Editor to work in legacy graphic engine
- fixed the MultiColor plot option Gerber selected tab to work in legacy graphic engine
- documented some methods in the ShapeCollectionLegacy class
- updated the files: setup_ubuntu.sh and requirements.txt
- some strings changed to be easier for translation
- updated the .POT file and the translation files
- updated and corrected the Romanian and Spanish translations
- updated the .PO files for the rest of the translations, they need to be filled in.
- fixed crash when trying to set a workspace in FlatCAM in the Legacy engine 2D mode by disabling this function for the case of 2D mode
- fixed exception when trying to Fit View (shortcut key 'V') with no object loaded, in legacy graphic engine

21.09.2019

- fixed Measuring Tool in legacy graphic engine
- fixed Gerber plotting in legacy graphic engine
- fixed Geometry plotting in legacy graphic engine
- fixed CNCJob and Excellon plotting in legacy graphic engine
- in legacy graphic engine fixed the travel vs cut lines in CNCJob objects
- final fix for key shortcuts with modifier in legacy graphic engine
- refactored some of the code in the legacy graphic engine
- fixed drawing of selection box when dragging mouse on screen and the selection shape drawing on the selected objects
- fixed the moving drawing shape in Tool Move in legacy graphic engine
- fixed moving geometry in Tool Measurement in legacy graphic engine
- fixed Geometry Editor to work in legacy graphic engine
- fixed Excellon Editor to work in legacy graphic engine
- fixed Gerber Editor to work in legacy graphic engine
- fixed NCC tool to work in legacy graphic engine

20.09.2019

- final fix for the --shellvar having spaces within the assigned value; now they are retained
- legacy graphic engine - made the mouse events work (click, release, doubleclick, dragging)
- legacy graphic engine - made the key events work (simple or with modifiers)
- legacy graphic engine - made the mouse cursor work (enabled/disabled, position report); snapping is not moving the cursor yet
- made the mouse cursor snap to the grid when grid snapping is active
- changed the axis color to the one used in the OpenGL graphic engine
- work on ShapeCollectionLegacy
- fixed mouse cursor to work for all objects
- fixed event signals to work in both graphic engines: 2D and 3D

19.09.2019

- made sure that if FlatCAM is registered with a file extension that it does not recognize it will exit
- added some fixes in the the file extension detection
- added some status messages for the Tcl script related methods
- made sure that optionally, when a script is run then it is also loaded into the code editor
- added control over the display of Sys Tray Icon in Edit -> Preferences -> General -> GUI Settings -> Sys Tray Icon checkbox
- updated some of the default values to more reasonable ones
- FlatCAM can be run in HEADLESS mode now. This mode can be selected by using the --headless=1 command line argument or by changing the line headless=False to True in config/configuration.txt file. In this mod the Sys Tray Icon menu will hold only the Run Scrip menu entry and Exit entry.
- added a new TclCommand named quit_flatcam which will ... quit FlatCAM from Tcl Shell or from a script
- fixed the command line argument --shellvar to work when there are spaces in the argument value
- fixed bug in Gerber editor that did not allow to display all shapes after it encountered one shape without 'solid' geometry
- fixed bug in Gerber Editor -> selection area handler where if some of the selected shapes did not had the 'solid' geometry will silently abort selection of further shapes
- added new control in Edit -> Preferences -> General -> Gui Preferences -> Activity Icon. Will select a GIF from a selection, the one used to show that FlatCAM is working.
- changed the script icon to a smaller one in the sys tray menu
- fixed bug with losing the visibility of toolbars if at first startup the user tries to change something in the Preferences before doing a first save of Preferences
- changed a bit the splash PNG file
- moved all the GUI Preferences classes into it's own file flatcamGUI.PreferencesUI.py
- changed the default method for Paint Tool to 'all'

18.09.2019

- added more functionality to the Extension registration with FLatCAM and added to the GUI in Edit -> Preferences -> Utilities
- fixed the parsing of the Manufacturing files when double clicking them and they are registered with FlatCAM
- fixed showing the GUI when some settings (maximized_GUI) are missing from QSettings
- added sys tray menu
- added possibility to edit the custom keywords used by the autocompleter (in Tcl Shell and in the Code Editor). It is done in the Edit -> Preferences -> Utilities
- added a new setting in Edit -> Preferences -> General -> GUI Settings -> Textbox Font which control the font on the Textbox GUI elements
- fixed issue with the sys tray icon not hiding after application close
- added option to run a script from the context menu of the sys tray icon. Changed the color of the sys tray icon to a green one so it will be visible on light and dark themes

17.09.2019

- added more programmers that contributed to FlatCAM over the years, in the "About FlatCAM" -> Programmers window
- fixed issue #315 where a script run with the --shellfile argument crashed the program if it contained a TclCommand New
- added messages in the Splash Screen when running FlatCAM with arguments at startup
- fixed issue #313 where TclCommand drillcncjob is spitting errors in Tcl Shell which should be ignored
- fixed an bug where the pywrapcp name from Google OR-Tools is not defined; fix issue #316
- if FlatCAM is started with the 'quit' or 'exit' as argument it will close immediately and it will close also another instance of FlatCAM that may be running
- added a new command line parameter for FlatCAM named '--shellvars' which can load a text file with variables for Tcl Shell in the format: one variable assignment per line and looking like: 'a=3' without quotes
- made --shellvars into --shellvar and make it only one list of commands passed to the Tcl. The list is separated by comma but without spaces. The variables are accessed in Tcl with the names shellvar_x where x is the index in the list of command comma separated values
- fixed an issue in the TclShell that generated an exception IndexError which crashed the software
- fixed the --shellvar and --shellfile FlatCAM arguments to work together but the --shellvar has precedence over --shellfile as it is most likely that whatever variable set by --shellvar will be used in the script file run by --shellfile

16.09.2019

- modified the TclCommand New so it will no longer close all tabs when called (it closed the Code Editor tab which may have been holding the code that run)
- fixed the App.on_view_source() method for CNCJob objects: the Gcode will now contain the Prepend and Append code from the Edit -> Preferences -> CNCJob -> CNCJob Options
- added a new parameter named 'muted' for the TclCommands: cncjob, drillcncjob and write_gcode. Setting it as -muted 1 will disable the error reporting in TCL Shell
- some GUI optimizations
- more GUI optimizations related to being part of the Advanced category or not
- added possibility to change the positive SVG exported file color in Tool Film
- fixed some issues recently introduced in the TclCommands CNCJob, DrillCNCJob and write_gcode; changed some parameters names
- fixed issue in the Laser preprocessor where the laser was turned on as soon as the GCode started creating an unwanted cut up until the job start
- added new links in Menu -> Help (Excellon, Gerber specifications and a Report Bug)
- made the splashscreen to be showed on the current monitor on systems with multiple monitors
- added a new entry in Menu -> View -> Redraw All which is doing what the name says: redraw all loaded objects
- fixed issue where in TCl Shell the Windows paths were not understood due of backslash symbol understood as escape symbol instead of path separator
- made sure that in for the TclCommand cncjob and for the drillcncjob if one of the args is stated but no value then the value used will be the default one
- made available the TSA algorithm for drill path optimization when the used OS is 64bit. When used OS is 32bit the only available algorithm is TSA

15.09.2019

- refactored GeometryObject.mtool_gen_cncjob() method
- fixed the TclCommandCncjob to work for multigeometry Geometry objects; still I had to fix the list of tools parameter, right now I am setting it to an empty list
- update the Tcl Command isolate to be able to isolate exteriors, interiors besides the full isolation, using the iso_type parameter
- fixed issue in ToolPaint that could not allow area painting of a geometry that was a list and not a Geometric element (polygon or MultiPolygon)
- fixed UI showing before the initialization of FlatCAM is finished when the last state of GUI was maximized
- finished updating the TclCommand cncjob to work for multi-geo Geometry objects with the parameters from the args
- fixed the TclCommand cncjob to use the -outname parameter
- added some more keywords in the data_model for auto-completer
- fixed isolate TclCommand to use correctly the -outname parameter
- added possibility to see the GCode when right clicking on the Project tab on a CNCJob object and then clicking View Source
- added a new TclCommand named PlotObjects which will plot a list of FlatCAM objects
- made that after opening an object in FlatCAM it is not automatically plotted. If the user wants to plot it can use the TclCommands PlotAll or PlotObjects
- modified the TclCommands so that open files do not plot the opened files automatically
- made all TclCommands not to be plotted automatically
- made sure that all TclCommands are not threaded
- added new TclCommands: NewExcellon, NewGerber
- fixed the TclCommand open_project
- added the outname parameter (and established an default name when outname not used) for the AlignDrillGrid and AlignDrill TclCommands
- fixed Scripts repeating multiple time when the Code Editor is used. This repetition was correlated with multiple openings of the Code Editor window (especially after an error)
- added the autocomplete keywords that can be changed to the defaults dictionary

14.09.2019

- more string changes
- updated translation files
- fixed a small bug
- minor changes in the Code Editor GUI
- minor changes in the 'FlatCAM About' GUI
- added a new shortcut key F5 for doing the 'Plot All'
- updated the google-translated Spanish translation strings
- fixed the layouts to include toolbars breaks where it was needed
- whenever the user changes the Excellon format values for loading files, the Export Excellon Format values will be updated
- made optional the behavior of Excellon Export values following the values in the Excellon Loading section
- updated the translations (except RU) and the POT file
- added to the NonCopperClear.ncc_handler() a parameter to be able to run it non-threaded

13.09.2019

- added control for simplification when loading a Gerber file in Preferences -> Gerber -> Gerber General -> Simplify
- added some messages for the Edit -> Conversions -> Join methods() to make sure that there are at least 2 objects selected for join
- added a grid layout in on_about()
- upgraded the Script Editor to be able to run Tcl commands in batches
- added some ToolTips for the buttons in the Code Editor
- converted the big strings that hold the shortcut keys descriptions to smaller string to make translations easier
- fixed some of the strings that were left in the old way
- updated the POT file
- updated Romanian language partially
- added a new way to handle scripts with repeating Tcl commands
- added new buttons in the Tools toolbar for running, opening and adding new scripts
- finished the Romanian translation update and updated the POT file

12.09.2019

- small changes in the TclCommands: MillDrills, MillSlots, DrillCNCJob: the new parameter for tolerance is now named: diatol
- cleaned up the 'About FlatCAM' window, started to give credits for the translation team
- started to add an application splash screen
- now, Excellon and Gerber edited objects will have the source_code updated and ready to be saved
- the edited Gerber (or Excellon) object now is kept in the app after editing and the edited object is a new object
- added a message to the splash screen
- remade the splash screen to show multiple messages on app initialization
- added a new splash image
- added a control in Preferences -> General -> GUI Settings -> Splash Screen that control if the splash screen is shown at startup

11.09.2019

- added the Gerber code as source for the panelized object in Panelize Tool
- whenever a Gerber file is deleted, the mark_shapes objects are deleted also
- made faster the Gerber parser for the case of having a not valid geometry when loading a Gerber file without buffering
- updated code in self.on_view_source() to make it more responsive
- fixed the TclCommand MillHoles
- changed the name of TclCommand MillHoles to MillDrills and added a new TclCommand named MillSlots
- modified the MillDrills and MillSlots TclCommands to accept as parameter a list of tool diameters to be milled instead of tool indexes
- fixed issue #302 where a copied object lost all the tools
- modified the TclCommand DrillCncJob to have as parameter a list of tool diameters to be drilled instead of tool indexes
- updated the Spanish translation (Google-translation)
- added a new parameter in the TclCommands: DrillCNCJob, MillDrills, MillSlots named tol (from tolerance). If the diameters of the milled (drilled) dias are within the tolerance specified of the diameters in the Excellon object than those diameters will be processed. This is to help account for rounding errors when having units conversion

10.09.2019

- made isolation threaded
- fixed a small typo in TclCommandCopperCLear
- made changing the Plot kind in CNCJob selected tab, threaded
- fixed an object used before declaring it in NCC Tool - Area option
- added progress for the generation of Isolation geometry
- added progress and possibility of graceful exit in Panel Tool
- added graceful exit possibility when creating Isolation
- changed the workers thread priority back to Normal
- when disabling plots, if the selection shape is visible, it will be deleted
- small changes in Tool Panel (eliminating some deepcopy() calls)
- made sure that all the progress counters count to 100%

9.09.2019

- changed the triangulation type in VisPyVisuals for ShapeCollectionVisual class
- added a setting in Preferences -> Gerber -> Gerber General named Buffering. If set to 'no' the Gerber objects load a lot more faster (perhaps 10 times faster than when set to 'full') but the visual look is not so great as all the aperture polygons can be seen
- added for NCC Tool and Paint Tool a setting in the Preferences -> Tools --> (NCC Tool/ Paint Tool) that can set a progressive plotting (plot shapes as they are processed)
- some fixes in Paint Tool when done over the Gerber objects in case that the progressive plotting is selected
- some fixes in Gerber isolation in case that the progressive plotting is selected; added a 'Buffer solid geometry' button shown only when progressive plotting for Gerber object is selected. It will buffer the entire geometry of the object and plot it, in a threaded way.
- modified FlatCAMObj.py file to the new string format that will allow easier translations
- modified camlib.py, FlatCAMAPp.py and ObjectCollection.py files to the new string format that will allow easier translations
- updated the POT file and the German language
- fixed issue when loading unbuffered a Gerber file that has negative regions
- fixed Panelize Tool to save the aperture geometries into the panel apertures. Also made the tool faster by removing the buffering at the end of the job
- modified FlatCAMEditor's files to the new string format that will allow easier translations
- updated POT file and the Romanian translation

8.09.2019

- added some documentation strings for methods in FlatCAMApp.App class
- removed some @pyqtSlot() decorators as they interfere with the current way the program works

7.09.2019

- added a method to gracefully exit from threaded tasks and implemented it for the NCC Tool and for the Paint Tool
- modified the on_about() function to reflect the reality in 2019 - FlatCAM it is an Open Source contributed software
- remade the handlers for the Enable/Disable Project Tree context menu so they are threaded and activity is shown in the lower right corner of the main window
- added to GUI new options for the Gerber object related to area subtraction
- added new feature in the Gerber object isolation allowing for the isolation to avoid an area defined by another object (Gerber or Geometry)
- all transformation functions show now the progress (rotate, mirror, scale, offset, skew)
- made threaded the Offset and Scale operations found in the Selected tab of the object
- corrected some issues and made Move Tool to show correctly when it is plotting and when it is offsetting the objects position
- made Set Origin feature, threaded
- updated German language translation files
- separated the Plotting thread from the transformations threads

6.09.2019

- remade visibility threaded
- reimplemented the thread listening for new FlatCAM process starting with args so it is no longer subclassed but using the moveToThread function
- added percentage display for work done in NCC Tool
- added percentage display for work done in Paint Tool
- some fixes and prepared the activity monitor area to receive updated texts
- added progress display in status bar for generating CNCJob from Excellon objects
- added progress display in status bar for generating CNCJob from Geometry objects
- modified all the FlatCAM tools strings to the new format in which the status is no longer included in the translated strings to make it easier for the future translations
- more customization for the progress display in case of NCC Tool, Paint Tool and for the Gcode generation
- updated POT file with the new strings
- made the objects offset (therefore the Move Tool) show progress display

5.09.2019

- fixed issue with loading files at start-up
- fixed issue with generating bounding box geometry for CNCJob objects
- added some more infobar messages and log.debug
- increased the priority for the worker tasks
- hidden the configuration for G91 coordinates due of deciding to leave this development for another time; it require too much refactoring
- added some messages for the G-code generation so the user know in which stage the process is

4.09.2019

- started to work on support for G91 in Gcode (relative coordinates)
- added support for G91 coordinates
- working in plotting the CNCjob generated with G91 coordinates

3.09.2019

- in NCC tool there is now a depth of cut parameter named 'Cut Z' which will dictate how deep the tool will enter into the PCB material
- in NCC tool added possibility to choose between the type of tools to be used and when V-shape is used then the tool diameter is calculated from the desired depth of cut and from the V-tip parameters
- small changes in NCC tool regarding the usage of the V-shape tool
- fixed the isolation distance in NCC Tool for the tools with iso_op type
- in NCC Tool now the Area adding is continuous until RMB is clicked (no key modifier is needed anymore)
- fixed German language translation
- in NCC Tool added a warning in case there are isolation tools and if those isolation's are interrupted by an area or a box
- in Paint Tool made that the area selection is repeated until RMB click
- in Paint Tool and NCC Tool fixed the RMB click detection when Area selection is used
- finished the work on file extensions registration with FlatCAM. If the file extensions are deleted in the Preferences -> File Associations then those extensions are unregistered with FlatCAM
- fixed bug in NCC Tools and in SolderPaste Tool if in Edit -> Preferences only one tool is entered
- fixed bug in camblib.clear_polygon3() which caused that some copper clearing / paintings were not complete (some polygons were not processed) when the Straight Lines method was used
- some changes in NCC Tools regarding of the clearing itself

2.09.2019

- fixed issue in NCC Tool when using area option
- added formatting for some strings in the app strings, making the future translations easier
- made changes in the Excellon Tools Table to make it more clear that the tools are selected in the # column and not in the Plot column
- in Excellon and Gerber Selected tab made the Plot (mark) columns not selectable
- some ToolTips were modified
- in Properties Tool made threaded the calculation of convex_hull area and also made it to work for multi-geo objects
- in NCC tool the type of tool that is used is transferred to the Geometry object
- in NCC tool the type of isolation done with the tools selected as isolation tools can now be selected and it has also an Edit -> Preferences entry
- in Properties Tool fixed the dimensions calculations (length, width, area) to work for multi-geo objects

1.09.2019

- fixed open handlers
- fixed issue in NCC Tool where the tool table context menu could be installed multiple times
- added new ability to create simple isolation's in the NCC Tool
- fixed an issue when multi depth step is larger than the depth of cut

27.08.2019

- made FlatCAM so that whenever an associated file is double clicked, if there is an opened instance of FlatCAM, the file will be opened in the first instance without launching a new instance of FlatCAM. If FlatCAM is launched again it will spawn a new process (hopefully it will work when freezed).

26.08.2019

- added support for file associations with FlatCAM, for Windows

25.08.2019

- initial add of a new Tcl Command named CopperClear
- remade the NCC Tool in preparation for the newly added TclCommand CopperClear
- finished adding the TclCommandCopperClear that can be called with alias: 'ncc'
- added new capability in NCC Tool when the reference object is of Gerber type and fixed some newly introduced errors
- fixed issue #298. The changes in preprocessors done in Preferences dis not update the object UI layout as it was supposed to. The selection of Marlin postproc. did not unhidden the Feedrate Rapids entry.
- fixed minor issues
- fixed Tcl Command AddPolygon, AddPolyline
- fixed Tcl Command CncJob
- fixed crash due of Properties Tool trying to have a convex hull area on FlatCAMCNCJob objects which is not possible due of their nature
- modified Tcl Command SubtractRectangle
- fixed and modernized the Tcl Command Scale to be able to scale on X axis or on Y axis or on both and having as scale reference either the (0, 0) point or the minimum point of the bounding box or the center of the bounding box.
- fixed and modernized the Tcl Command Skew

24.08.2019

- modified CutOut Tool so now the manual gaps adding will continue until the user is clicking the RMB
- added ability to turn on/off the grid snapping and to jump to a location while in CutOut Tool manual gap adding action
- made PlotCanvas class inherit from VisPy Canvas instead of creating an instance of it (work of JP)
- fixed selection by dragging a selection shape in Geometry Editor
- modified the Paint Tool. Now the Single Polygon and Area/Reference Object painting works with multiple tools too. The tools have to be selected in the Tool Table.
- remade the TclCommand Paint to work in the new configuration of the the app (the painting functions are now in their own tool, Paint Tool)
- fixed a bug in the Properties Tool
- added a new TcL Command named Nregions who generate non-copper regions
- added a new TclCommand named Bbox who generate a bounding box.

23.08.2019

- in Tool Cutout for the manual gaps, right mouse button click will exit from the action of adding gaps
- in Tool Cutout tool I've added the possibility to create a cutout without bridge gaps; added the 'None' option in the Gaps combobox
- in NCC Tool added ability to add multiple zones to clear when Area option is checked and the modifier key is pressed (either CTRL or SHIFT as set in Preferences). Right click of the mouse is an additional way to finish the job.
- fixed a bug in Excellon Editor that made that the selection of drills is always cumulative
- in Paint Tool added ability to add multiple zones to paint when Area option is checked and the modifier key is pressed (either CTRL or SHIFT as set in Preferences). Right click of the mouse is an additional way to finish the job.
- in Paint Tool and NCC Tool, for the Area option, now mouse panning is allowed while adding areas to process
- for all the FlatCAM tools launched from toolbar the behavior is modified: first click it will launch the tool; second click: if the Tool tab has focus it will close the tool but if another tab is selected, the tool will have focus
- modified the NCC Tool and Paint Tool to work multiple times after first launch
- fixed the issue with GUI entries content being deselected on right click in the box in order to copy the value
- some changes in GUI tooltips
- modified the way key modifiers are detected in Gerber Editor Selection class and in Excellon Editor Selection class
- updated the translations
- fixed aperture move in Gerber Editor
- fixed drills/slots move in Excellon Editor
- RELEASE 8.96

22.08.2019

- added ability to turn ON/OFF the detachable capability of the tabs in Notebook through a context menu activated by right mouse button click on the Notebook header
- added ability to turn ON/OFF the detachable capability of the tabs in Plot Tab Area through a context menu activated by right mouse button click on the Notebook header
- added possibility to turn application portable from the Edit -> Preferences -> General -> App. Preferences -> Portable checkbox
- moved the canvas setup into it's own function and called it in the init() function
- fixed the Buffer Tool in Geometry Editor; made the Buffer entry field a QDoubleSpinner and set the lower limit to zero.
- fixed Tool Cutout so when the target Gerber is a single Polygon then the created manual geometry will follow the shape if shape is freeform
- fixed TclCommandFollow command; an older function name was used who yielded wrong results
- in Tool Cutout for the manual gaps, now the moving geometry that cuts gaps will orient itself to fit the angle of the cutout geometry

21.08.2019

- added feature in Paint Tool allowing the painting to be done on Gerber objects
- added feature in Paint Tool to set how (and if) the tools are sorted
- added Edit -> Preferences GUI entries for the above just added features
- added new entry in Properties Tool which is the calculated Convex Hull Area (should give a more precise area for the irregular shapes than the box area)
- added some more strings in Properties Tool for the translation
- in NCC Tool added area selection feature
- fixed bug in Excellon parser for the Excellon files that do not put the type of zero suppression they use in the file (like DipTrace eCAD)
- fixed some issues introduced in NCC Tool

20.08.2019

- added ability to do copper clearing through NCC Tool on Geometry objects
- replaced the layout from Grid to Form for the Reference objects comboboxes in Paint Tool and in NCC Tool

19.08.2019

- updated the Edit -> Preferences to include also the Gerber Editor complete Preferences
- started to update the app strings to make it easier for future translations
- fixed the POT file and the German translation
- some mods in the Tool Sub
- fixed bug in Tool Sub that created issues when toggling visibility of the plots
- fixed the Spanish, Brazilian Portuguese and Romanian translations

18.08.2019

- made the exported preferences formatted therefore more easily read
- projects at startup don't work in another thread so there is no multithreading if I want to double click an project and to load it
- added messages in the application window title which show the progress in loading a project (which is not thread-safe therefore keeping the app from fully initialize until finished)
- in NCC Tool added a new parameter (radio button) that offer the choice on the order of the tools both in tools table and in execution of engraving; added as a parameter also in Edit -> Preferences -> Tools -> NCC Tool
- added possibility to drag & drop FlatCAM config files (*.FlatConfig) into the canvas to be opened into the application
- added GUI in Paint tool in beginning to add Paint by external reference object 
- finished adding in Paint Tool the usage of an external object to set the extent of th area painted. For simple shapes (single Polygon) the shape can be anything, for the rest will be a convex hull of the reference object
- modified NCC tool so for simple objects (single Polygon) the external object used as reference can have any shape, for the other types of objects the copper cleared area will be the convex hull of the reference object
- modified the strings of the app wherever they contained the char seq <b> </b> so it is not included in the translated string
- updated the translation files for the modified strings (and for the newly added strings)
- added ability to lock toolbars within the context menu that is popped up on any toolbars right mouse click. The value is saved in QSettings and it is persistent between application startup's.

17.08.2019

- added estimated time of routing for the CNCJob and added travelled distance parameter for geometry, too
- fixed error when creating CNCJob due of having the annotations disabled from preferences but the plot2() function from camlib.CNCJob class still performed operations who yielded TypeError exceptions
- coded a more accurate way to estimate the job time in CNCJob, taking into consideration if there is a usage of multi depth which generate more passes
- another fix (final one) for the Exception generated by the annotations set not to show in Preferences
- updated translations and changed version
- fixed installer issue for the x64 version due of the used CX_FREEZE python package which was in unofficial version (obviously not ready to be used)
- fixed bug in Geometry Editor, in disconnect_canvas_event_handlers() where I left some part of code without adding a try - except block which was required
- moved the initialization of the FlatCAM editors after a read of the default values. If I don't do this then only at the first start of the application the Editors are not functional as the Editor objects are most likely destroyed
- fixed bug in FlatCAM editors that caused the shapes to be drawn without resolution when the app units where INCH
- modified the transformation functions in all classes in camlib.py and FlatCAMObj.py to work with empty geometries
- RELEASE 8.95

17.08.2019

- updated the translations for the new strings
- RELEASE 8.94

16.08.2019

- working in Excellon Editor to Tool Resize to consider the slots, too
- fixed a weird error that created a crash in the following scenario: create a new excellon, edit it, add some drills/slots, delete it without saving, create a new excellon, try to edit and a crash is issued due of a wrapped C++ error
- fixed bug selection in Excellon editor that caused not to select the corresponding row (tool dia) in the tool table when a selection rectangle selected an even number of geometric elements
- updated the default values to more convenient ones
- remade the enable/disable plots functions to work only where it needs to (no sense in disabling a plot already disabled)
- made sure that if multi depth is choosed when creating GCode then if the multidepth is more than the depth of cut only one cut is made (to the depth of cut)
- each CNCJob object has now it's own text_collection for the annotations which allow for the individual enabling and disabling of the annotations
- added new menu category in File -> Backup with two menu entries that duplicate the functions of the export/import preferences buttons from the bottom of the Preferences window
- in Excellon Editor fixed the display of the number of slots in the Tool Table after the resize done with the Resize tool
- in Excellon Editor -> Resize tool, made sure that when the slot is resized, it's length remain the same, because the tool should influence only the 'thickness' of the slot. Since I don't know anything but the geometry and tool diameters (old and new), this is only an approximation and computationally intensive
- in Excellon Editor -> remade the Tool edit made by editing the diameter values in the Tools Table to work for slots too
- In Excellon Editor -> fixed bug that caused incorrect display of the relative coordinates in the status bar

15.08.2019

- added Edit -> Preferences GUI and storage for the Excellon Editor Add Slots
- added a confirmation message for objects delete and a setting to activate it in Edit -> Preferences -> Global
- merged pull request from Mike Smith which fix an application crash when attempting to open a not-a-FlatCAM-project file as project
- merged pull request from Mike Smith that add support for a new SVG element: <use>
- stored inside FlatCAM app the VisPy data files and at the first start the application will try to copy those files to the APPDATA (roaming) folder in case of running under Windows OS
- created a configuration file in the root/config/configuration.txt with a configuration line for portability. Set portable to True to run the app as portable
- working on the Slots Array in Excellon Editor - building the GUI
- added a failsafe path to the source folder from which to copy the VisPy data
- fixed the GUI for Slot Arrays in Excellon Editor
- finished the Slot Array tool in Excellon Editor
- added the key shortcut handlers for Add Slot and Add Slot Array tools in Excellon Editor
- started to work on the Resize tool for the case of Excellon slots in Excellon Editor
- final fix for the VisPy data files; the defaults files are saved to the Config folder when the app is set to be portable
- added the Slot Type parameter for exporting Excellon in Edit -> Preferences -> Excellon -> Export Excellon. Now the Excellon object can be exported also with drilled slot command G85
- fixed bug in Excellon export when there are no zero suppression (coordinates with decimals)

14.08.2019

- fixed the loading of Excellon with slots and the saving of edited Excellon object in regard of slots, in Excellon Editor
- fixed the Delete tool, Select tool in Excellon Editor to work for Slots too
- changes in the way the edited Excellon with added slots is saved
- added more icons and cursor in Excellon Editor for Slots related functions
- in Excellon Editor fixed the selection issue which in a certain step created a failure in the Copy and Move tools.
- in Excellon Editor fixed the selection with key modifier pressed
- edited the mouse cursors and saved them without included thumbnail in a bid to remove some CRC warnings made by libpng

13.08.2019

- added new option in ToolSub: the ability to close (or not) the resulting paths when using tool on Geometry objects. Added also a new category in the Edit -> Preferences -> Tools, the Substractor Tool Options
- some PEP8 changes in FlatCAMApp.py
- added new settings in Edit -> Preferences -> General for Notebook Font size (set font size for the items in Project Tree and for text in Selected Tab) and for canvas Axis font size. The values are stored in QSettings.
- updated translations
- fixed a bug in FCDoubleSpinner GUI element
- added a new parameter in NCC tool named offset. If the offset is used then the copper clearing will finish to a set distance of the copper features
- fixed bugs in Geometry Editor
- added protection's against the 'bowtie' geometries for Subtract Tool in Geometry Editor
- added all the tools from Geometry Editor to the the contextual menu
- fixed bug in Add Text Tool in Geometry Editor that gave error when clicking to place text without having text in the box
- added all the tools from Gerber Editor to the the contextual menu
- added the menu entry "Edit" in the Project contextual menu for Gerber objects
- started to work in adding slots and slots array in Excellon Editor
- in SlotAdd finished the utility geometry and the GUI for it

12.08.2019

- done regression to solve the bug with multiple passes cutting from the copper features (I should remember not to make mods here)
- if 'combine' is checked in Gerber isolation but there is only one pass, the resulting geometry will still be single geo
- the 'passes' entry was changed to a IntSpinner so it will allow passes to be entered only in range (1, 999) - it will not allow entry of 0 which may create some issues
- improved the GerberObject.isolate() function to work for geometry in the form of list and also in case that the elements of the list are LinearRings (like when doing the Exterior Isolation)
- in NCC Tool made sure that at each run the old objects are deleted
- fixed bug in camlib.Gerber.parse_lines() Gerber parser where for Allegro Gerber files the Gerber units were incorrectly detected
- improved Mark Area Tool in Gerber Editor such that at each launch the previous markings are deleted

11.08.2019

- small changes regarding the Project Title
- trying to fix reported bugs
- made sure that the annotations are deleted when the object that contain them is deleted
- fixed issue where the annotations for all the CNCJob objects are toggled together whenever the ones for an single object are toggled
- optimizations in GeoEditor
- updated translations

10.08.2019

- added new feature in NCC Tool: now another object can be used as reference for the area extent to be cleared of copper
- fixed issue in the latest feature in NCC Tool: now it works also with reference objects made out of LineStrings (tool 'Path' in Geometry Editor)
- translation files updated for the new strings (Google Translate)
- RELEASE 8.93

9.08.2019

- added Exception handing for the case when the user is trying to save & overwrite a file already opened in another file
- finished added 'Area' type of Paint in Paint Tool
- fixed bug that created a choppy geometry for CNCJob when working in INCH
- fixed bug that did not asked the user to save the preferences after importing a new set of preferences, after the user is trying to close the Preferences tab window

7.08.2019

- replaced setFixedWidth calls with setMinimumWidth
- recoded the camlib.Geometry.isolation_geometry() function
- started to work on Paint Area in Paint Tool

6.08.2019

- fixed bug that crashed the app after creating a new geometry, if a new object is loaded and the new geometry is deleted and then trying to select the just loaded new object
- made some GUI elements in Edit -> Preferences to have a minimum width as opposed to the previous fixed one
- fixed issue in the isolation function, if the isolation can't be done there will be generated no Geometry object 
- some minor UI changes
- strings added and translations updated

5.08.2019

- made sure that if using an negative Gerber isolation diameter, the resulting Geometry object will use a tool with positive diameter
- fixed bug that when isolating a Gerber file made out of a single polygon, an RecursionException was issued together with inability to create tbe isolation
- when applying a new language if there are any changes in the current project, the app will offer to save the project before the reboot

3.08.2019

- added project name to the window title
- fulfilled request: When saving a CNC file, if the file name is changed in the OS window, the new name does appear in the “Selected” (in name) and “Project” tabs (in cnc_job)
- solved bug such that the app is not crashing when some apertures in the Gerber file have no geometry. More than that, now the apertures that have geometry elements are bolded as opposed to the ones without geometry for which the text is unbolded
- merged a pull request with language changes for Russian translate
- updated the other translations

31.07.2019

- changed the order of the menu entries in the FIle -> Open ...
- organized the list of recent files so the Project entries are to the top and separated from the other types of file
- work on identification of changes in Preferences tab
- added categories names for the recent files
- added a detection if any values are changed in the Edit -> Preferences window and on close it will ask the user if he wants to save the changes or not
- created a new menu entry in the File menu named Recent projects that will hold the recent projects and the previous "Recent files" will hold only the previous loaded files
- updated all translations for the new strings
- fixed bug recently introduced that when changing the units in the Edit -> Preferences it did not converted the values
- fixed another bug that when selecting an Excellon object after disabling it it crashed the app
- RELEASE 8.92

30.07.2019

- fixed bug that crashed the software when trying to edit a GUI value in Geometry selected tab without having a tool in the Tools Table
- fixed bug that crashed the app when trying to add a tool without a tool diameter value
- Spanish Google translation at 77%
- changed the Disable plots menu entry in the context menu, into a Toggle Visibility menu entry
- Spanish Google translation 100% but two strings (big ones) - needs review
- added two more strings to translation strings (due of German language)
- completed the Russian translation using the Google and Yandex translation engines (minus two big strings) - needs review

28.07.2019

- fixed issue with not using the current units in the tool tables after unit conversion
- after unit conversion from Preferences, the default values are automatically saved by the app
- in Basic mode, the tool type column is no longer hidden as it may create issues when using an painted geometry
- some PEP8 clean-up in FlatCAMGui.py
- fixed Panelize Tool to do panelization for multiple passes type of geometry that comes out of the isolation done with multiple passes

20.07.2019

- updated the CutOut tool so it will work on single PCB Gerbers or on PCB panel Gerbers
- updated languages
- 70% progress in Spanish Google translation

19.07.2019

- fixed bug in FlatCAMObj.GeometryObject.ui_disconnect(); the widgets signals were not disconnected from handlers when required therefore the signals were connected in an exponential way
- some changes in the widgets used in the Selected tab for Geometry object
- some PEP8 cleanup in FlatCAMObj.py
- updated languages
- 60% progress in Spanish Google translation

17.07.2019

- added some more strings to the translatable ones, especially the radio button labels
- updated the .POT file and the available translations
- 51% progress in Spanish Google translation
- version date change

16.07.2019

- PEP8 correction in flatcamTools
- merged the Brazilian-portuguese language from a pull request made by Carlos Stein
- more PEP8 corrections

15.07.2019

- some PEP8 corrections

13.07.2019

- fixed a possible issue in Gerber Object class
- added a new tool in Gerber Editor: Mark Area Tool. It will mark the polygons in a edited Gerber object with areas within a defined range, allowing to delete some of the not necessary  copper features
- added new menu links in the Gerber Editor menu for Eraser Tool and Mark Area Tool
- added key shortcuts for Eraser Tool (Ctrl+E) and Mark Area Tool (Alt+A) and updated the shortcuts list

9.07.2019

- some changes in the app.on_togle_units() to make sure we don't try to convert empty parameters which may cause crashes on FlatCAM units change
- updated setup_ubuntu.sh file
- made sure to import certain libraries in some of the FlatCAM files and not to rely on chained imports

8.07.2019

- fixed bug that allowed empty tool in the tools generated in Geometry object
- fixed bug in Tool Cutout that did not allow the transfer of used cutout tool diameter to the cutout geometry object

5.07.2019

- fixed bug in CutOut Tool
- some other bug in CutOut tool fixed

1.07.2019

- Spanish translation at 36%

28.06.2019

- Spanish translation (Google Translate) at 21%

27.06.2019

- added new translation: Spanish. Finished 10%

23.06.2019

- fixes issues with units conversion when the tool diameters are a list of comma separated values (NCC Tool, SolderPaste Tool and Geometry Object)
- fixed a "typo" kind of bug in SolderPaste Tool
- RELEASE 8.919

22.06.2019

- some GUI layout optimizations in Edit -> Preferences
- added the possibility for multiple tool diameters in the Edit -> Preferences -> Geometry -> Geometry General -> Tool dia separated by comma
- fixed scaling for the multiple tool diameters in Edit -> Preferences -> Geometry -> Geometry General -> Tool dia, for NCC tools more than 2 and for Solderpaste nozzles more than 2
- fixed bug in CNCJob where the CNC Tools table will show always only 2 decimals for Tool diameters regardless of the current measuring units
- made the tools diameters decimals in case of INCH FlatCAM units to be 4 instead of 3
- fixed bug in updating Grid values whenever toggling the FlatCAM units and the X, Y Grid values are linked, bugs which caused the Y value to be scaled incorrectly
- set the decimals for Grid values to be set to 6 if the units of FlatCAM is INCH and to set to 4 if FlatCAM units are METRIC
- updated translations
- updated the Russian translation from 51% complete to 69% complete using the Yandex translation engine
- fixed recently introduced bug in milling drills/slots functions
- moved Substract Tool from Menu -> Edit -> Conversions to Menu -> Tool
- fixed bug in Gerber isolation (Geometry expects now a value in string format and not float)
- fixed bug in Paint tool: now it is possible to paint geometry generated by External Isolation (or Internal isolation)
- fixed bug in editing a multigeo Geometry object if previously a tool was deleted
- optimized the toggle of annotations; now there is no need to replot the entire CNCJob object too on toggling of the annotations
- on toggling off the plot visibility the annotations are turned off too
- updated translations; Russian translation at 76% (using Yandex translator engine - needs verification by a native speaker of Russian)

20.06.2019

- fixed Scale and Buffer Tool in Gerber Editor
- fixed Editor Transform Tool in Gerber Editor
- added a message in the status bar when copying coordinates to clipboard with SHIFT + LMB click combo
- languages update

19.06.2019

- milling an Excellon file (holes and/or slots) will now transfer the chosen milling bit diameter to the resulting Geometry object

17.06.2019

- fixed bug where for Geometry objects after a successful object rename done in the Object collection view (Project tab), deselect the object and reselect it and then in the Selected tab the name is not the new one but the old one
- for Geometry objects, adding a new tool to the Tools table after a successful rename will now store the new name in the tool data

15.06.2019

- fixed bug in Gerber parser that made the Gerber files generated by Altium Designer 18 not to be loaded
- fixed bug in Gerber editor - on multiple edits on the same object, the aperture size and dims were continuously multiplied due of the file units not being updated
- restored the FlatCAMObj.visible() to a non-threaded default

11.06.2019

- fixed the Edit -> Conversion -> Join ... functions (merge() functions)
- updated translations
- Russian translate by @camellan is not finished yet
- some PEP8 cleanup in camlib.py
- RELEASE 8.918

9.06.2019

- updated translations
- fixed the the labels for shortcut keys for zoom in and zoom out both in the Menu links and in the Shortcut list
- made sure the zoom functions use the global_zoom_ratio parameter from App.self.defaults dictionary.
- some PEP8 cleanup

8.06.2019

- make sure that the annotation shapes are deleted on creation of a new project
- added folder for the Russian translation
- made sure that visibility for TextGroup is set only if index is not None in VisPyVisuals.TextGroup.visible() setter

7.06.2019

- fixed bug in ToolCutout where creating a cutout object geometry from another external isolation geometry failed
- fixed bug in cncjob TclCommand where the gcode could not be correctly generated due of missing bounds params in obj.options dict
- fixed a hardcoded tolerance in GeometryObject.generatecncjob() and in GeometryObject.mtool_gen_cncjob() to use the parameter from Preferences
- updated translations

5.06.2019

- updated translations
- some layout changes in Edit -> Preferences such that the German translation (longer words than English) to fit correctly
- after editing an parameter the focus is lost so the user knows that something happened

4.06.2019

- PEP8 updates in AppExcEditor.py
- added the Excellon Editor parameters to the Edit -> Preferences -> Excellon GUI
- fixed a small bug in Excellon Editor
- PEP8 cleanup in FlatCAMGui
- finished adding the Excellon Editor parameters into the app logic and added a selection limit within Excellon Editor just like in the other editors

3.06.2019

- TclCommand Geocutout is now creating a new geometry object when working on a geometry, preserving also the origin object
- added a new parameter in Edit -> Preferences -> CNCJob named Annotation Color; it controls the color of the font used for annotations
- added a new parameter in Edit -> Preferences -> CNCJob named Annotation Size; it controls the size of the font used for annotations
- made visibility change threaded in FlatCAMObj()

2.06.2019

- fixed issue with geometry name not being updated immediately after change while doing geocutout TclCommand
- some changes to enable/disable project context menu entry handlers

1.06.2019

- fixed text annotation for CNC job so there are no overlapping numbers when 2 lines meet on the same point
- fixed issue in CNC job plotting where some of the isolation polygons are painted incorrectly
- fixed issue in CNCJob where the set circle steps is not used 

31.05.2019

- added the possibility to display text annotation for the CNC travel lines. The setting is both in Preferences and in the CNC object properties

30.05.2019

- editing a multi geometry will no longer pop-up a Tcl window
- solved issue #292 where a new geometry renamed with many underscores failed to store the name in a saved project
- the name for the saved projects are updated to the current time and not to the time of the app startup
- some PEP8 changes related to comments starting with only one '#' symbol
- more PEP8 cleanup
- solved issue where after the opening of an object the file path is not saved for further open operations

24.05.2019

- added a toggle Grid button to the canvas context menu in the Grids submenu
- added a toggle left panel button to the canvas context menu

23.05.2019

- fixed bug in Gerber editor FCDisk and DiscSemiEditorGrb that the resulting geometry was not stored into the 0 aperture where all the solids are stored
- fixed minor issue in Gerber Editor where apertures were included in the saved object even if there was no geometric data for that aperture
- some PEP8 cleanup in FlatCAMApp.py

22.05.2019

- Geo Editor - added a new editor tool, Eraser
- some PEP8 cleanup of the Geo Editor
- fixed some selection issues in the new tool Eraser in Geometry Editor
- updated the translation files
- RELEASE 8.917

21.05.2019

- added the file extension .ncd to the Excellon file extension list
- solved parsing issue for Excellon files generated by older Eagle versions (v6.x)
- Gerber Editor: finished a new tool: Eraser. It will erase certain parts of Gerber geometries having the shape of a selected shape.

20.05.2019

- more PEP8 changes in Gerber editor
- Gerber Editor - started to work on a new editor tool: Eraser

19.05.2019

- fixed the Circle Steps parameter for both Gerber and Geometry objects not being applied and instead the app internal defaults were used.
- fixed the Tcl command Geocutout issue that gave an error when using the 4 or 8 value for gaps parameter
- made wider the '#' column for Apertures Table for Gerber Object and for Gerber Editor; in this way numbers with 3 digits can be seen
- PEP8 corrections in AppGerberEditor.py
- added a selection limit parameter for Geometry Editor
- added entries in Edit -> Preferences for the new parameter Selection limit for both the Gerber and Geometry Editors.
- set the buttons in the lower part of the Preferences Window to have a preferred minimum width instead of fixed width
- updated the translation files

18.05.2019

- added a new toggle option in Edit -> Preferences -> General Tab -> App Preferences -> "Open" Behavior. It controls which path is used when opening a new file. If checked the last saved path is used when saving files and the last opened path is used when opening files. If unchecked then the path for the last action (either open or save) is used.
- fixed App.convert_any2gerber to work with the new Gerber apertures data structure
- fixed Tool Sub to work with the new Gerber apertures data structure
- fixed Tool PDF to work with the new Gerber apertures data structure

17.05.2019

- remade the Tool Cutout to work on panels
- remade the Tool Cutout such that on multiple applications on the same object it will yield the same result
- fixed an issue in the remade Cutout Tool where when applied on a single Gerber object, the Freeform Cutout produced no cutout Geometry object
- remade the Properties Tool such that it works with the new Gerber data structure in the obj.apertures. Also changed the view for the Gerber object in Properties
- fixed issue with false warning that the Gerber object has no geometry after an empty Gerber was edited and added geometry elements

16.05.2019

- Gerber Export: made sure that if some of the coordinates in a Gerber object geometry are repeating then the resulting Gerber code include only one copy
- added a new parameter/feature: now the spindle can work in clockwise mode (CW) or counter clockwise mode (CCW)

15.05.2019

- rewrited the Gerber Parser in camlib - success
- moved the self.apertures[aperture]['geometry'] processing for clear_geometry (geometry made with Gerber LPC command) in Gerber Editor
- Gerber Editor: fixed the Poligonize Tool to work with new geometric structure and took care of a special case
- Gerber Export is fixed to work with the new Gerber object data structure and it now works also for Gerber objects edited in Gerber Editor
- Gerber Editor: fixed units conversion for obj.apertures keys that require it
- camlib Gerber parser - made sure that we don't loose goemetry in regions
- Gerber Editor - made sure that for some tools the added geometry is clean (the coordinates are non repeating)
- covered some possible issues in Gerber Export

12.05.2019

- some modifications to ToolCutout

11.05.2019

- fixed issue in camlib.CNCjob.generate_from_excellon_by_tool() in the drill path optimization algorithm selection when selecting the MH algorithm. The new API's for Google OR-tools required some changes and also the time parameter can be now just an integer therefore I modified the GUI
- made the Feedrate Rapids parameter to depend on the type of preprocessor choosed. It will be showed only for a preprocessor which the name contain 'marlin' and for any preprocessor's that have 'custom' in the name
- fixed the camlib.Gerber functions of mirror, scale, offset, skew and rotate to work with the new data structure for apertures geometry
- fixed Gerber Editor selection to work with the new Gerber data structure in self.apertures
- fixed Gerber Editor PadEditorGrb class to work with the new Gerber data structure in self.apertures
- fixed camlib.Gerber issues related to what happen after parsing rectangular apertures 
- wip in camblib.Gerber
- completely converted the Gerber editor to the new data structure
- Gerber Editor: added a threshold limit for how many elements a move selection can have. If above the threshold only a bounding box Poly will be painted on canvas as utility geometry.

10.05.2019

- Gerber Editor - working in conversion to the new data format
- made sure that only units toggle done in Edit -> Preferences will toggle the data in Preferences. The menu entry Edit -> Toggle Units and the shortcut key 'Q' will change only the display units in the app
- optimized Transform tool
- RELEASE 8.916

9.05.2019

- reworked the Gerber parser

8.05.2019

- added zoom fit for Set Origin command
- added move action for solid_geometry stored in the gerber_obj.apertures
- fixed camlib.Gerber skew, rotate, offset, mirror functions to work for geometry stored in the Gerber apertures
- fixed Gerber Editor follow_geometry reconstruction
- Geometry Editor: made the tool to be able to continuously move until the tool is exited either by ESC key or by right mouse button click
- Geometry Editor Move Tool: if no shape is selected when triggering this tool, now it is possible to make the selection inside the tool
- Gerber editor Move Tool: fixed a bug that repeated the plotting function unnecessarily 
- Gerber editor Move Tool: if no shape is selected the tool will exit

7.05.2019

- remade the Tool Panelize GUI
- work in Gerber Export: finished the header export
- fixed the Gerber Object and Gerber Editor Apertures Table to not show extra rows when there are aperture macros in the object
- work in Gerber Export: finished the body export but have some errors with clear geometry (LPC)
- Gerber Export - finished

6.05.2019

- made units change from shortcut key 'Q' not to affect the preferences
- made units change from Edit -> Toggle Units not to affect the preferences
- remade the way the aperture marks are plotted in Gerber Object
- fixed some bugs related to moving an Gerber object with the aperture table in view
- added a new parameter in the Edit -> Preferences -> App Preferences named Geo Tolerance. This parameter control the level of geometric detail throughout FlatCAM. It directly influence the effect of Circle Steps parameter.
- solved a bug in Excellon Editor that caused app crash when trying to edit a tool in Tool Table due of missing a tool offset
- updated the ToolPanelize tool so the Gerber panel of type GerberObject can be isolated like any other GerberObject object
- updated the ToolPanelize tool so it can be edited
- modified the default values for toolchangez and endz parameters so they are now safe in all cases

5.05.2019

- another fix for bug in clear geometry processing for Gerber apertures
- added a protection for the case that the aperture table is part of a deleted object
- in Script Editor added support for auto-add closing parenthesis, brace and bracket
- in Script Editor added support for "CTRL + / " key combo to comment/uncomment line

4.05.2019

- fixed bug in camlib.parse_lines() in the clear_geometry processing section for self.apertures
- fixed bug in parsing Gerber regions (a point was added unnecessary)
- renamed the menu entry Edit -> Copy as Geo to Convert Any to Geo and moved it in the Edit -> Conversion
- created a new function named Convert Any to Gerber and installed it in Edit -> Conversion. It's doing what the name say: it will convert an Geometry or Excellon FlatCAM object to a Gerber object.

01.05.2019

- the project items color is now controlled from Foreground Role in ObjectCollection.data()
- made again plot functions threaded but moved the dataChanged signal (update_view() ) to the main thread by using an already existing signal (plots_updated signal) to avoid the errors with register QVector
- Enable/Disable Object toggle key ("Space" key) will trigger also the datChanged signal for the Project MVC
- added a new setting for the color of the Project items, the color when they are disabled.
- fixed a crash when triggering 'Jump To' menu action (shortcut key 'J' worked ok)
- made some mods to what can be translated as some of the translations interfered with the correct functioning of FlatCAM
- updated the translations
- fixed bugs in Excellon Editor
- Excellon Editor:  made Add Pad tool to work until right click
- Excellon Editor: fixed mouse right click was always doing popup context menu
- GUIElements.FCEntry2(): added a try-except clause
- made sure that the Tools Tab is cleared on Editors exit
- Geometry Editor: restored the old behavior: a tool is active until it is voluntarily exited: either by using the 'ESC' key, or selecting the Select tool or new: right click on canvas
- RELEASE 8.915

30.04.2019

- in ObjectCollection class, made sure that renaming an object in Project View does not result in an empty name. If new name is blank the rename is cancelled.
- made ObjectCollection.TreeItem() inherit KeySensitiveListVIew and implicitly QTreeView (in the hope that the theme applied on app will be applied on the tree items, too (for MacOs new DarkUI theme)
- renamed SilkScreen Tool to Substract Tool and move it's menu location in Edit -> Conversion
- started to modify the Substract Tool to work on Geometry objects too
- progress in the new Substract Tool for Geometry Objects
- finished the new Substract Tool
- added new setting for the color of the Project Tree items; it helps in providing contrast when using dark theme like the one in MacOS

29.04.2019

- solved bug in Gerber Editor: the 0 aperture (the region aperture) had no size which created errors. Made the size to be zero.
- solved bug in editors: the canvas selection shape was not deleted on mouse release if the grid snap was OFF
- solved bug in Excellon Editor: when selecting a drill hole on canvas the selected row in the Tools Table was not the correct one but the next highest row
- finished the Silkscreen Tool but there are some limitations (some wires fragments from silkscreen are lost)
- solved the issue in Silkscreen Tool with losing some fragments of wires from silkscreen

26.04.2019

- small changes in GUI; optimized contextual menu display
- made sure that the Project Tab is disabled while one of the Editors is active and it is restored after returning to app
- fixed some bugs recently introduced in Editors due of the changes done to the way mouse panning is detected 
- cleaned up the context menu's when in Editors; made some structural changes
- updated the code in camlib.CNCJob.generate_from_excellon_by_tools() to work with the new API from Google OR-Tools
- all Gerber regions (G36 G37) are stored in the 0 aperture
- fixed a bug that added geometry with clear polarity in the apertures where was not supposed to be

25.04.2019

- Geometry Editor: modified the intersection (if the selected shapes don't intersects preserve them) and substract functions (delete all shapes that were used in the process)
- work in the ToolSub
- for all objects, if in Selected the object name is changed to the same name, the rename is not done (because there is nothing changed)
- fixed Edit -> Copy as Geom function handler to work for Excellon objects, too
- made sure that the mouse pointer is restored to default on Editor exit
- added a toggle button in Preferences to toggle on/off the display of the selection box on canvas when the user is clicking an object or selecting it by mouse dragging.

24.04.2019

- PDF import tool: working in making the PDF layer rendering multithreaded in itself (one layer rendered on each worker)
- PDF import tool: solved a bug in parsing the rectangle subpath (an extra point was added to the subpath creating nonexisting geometry)
- PDF import tool: finished layer rendering multithreading
- New tool: Silkscreen Tool: I am trying to remove the overlapped geo with the soldermask layer from overlay layer; layed out the class and functions - not working yet

23.04.2019

- Gerber Editor: added two new tools: Add Disc and Add SemiDisc (porting of Circle and Arc from Geometry Editor)
- Gerber Editor: made Add Pad repeat until user exits the Add Pad through either mouse right click, or ESC key or deselecting the Add Pad menu item
- Gerber and Geometry Editors: fixed some issues with the Add Arc/Add Semidisc; in mode 132, the norm() function was not the one from numpy but from a FlatCAM Class. Also fixed some of the texts and made sure that when changing the mode, the current points are reset to prepare for the newly selected mode.
- Fixed Measurement Tool to show the mouse coordinates on the status bar (it was broken at some point)
- updated the translation files
- added more custom mouse cursors in Geometry and Gerber Editors
- RELEASE 8.914

22.04.2019

- added PDF file as type in the Recent File list and capability to load it from there
- PDF's can be drag & dropped on the GUI to be loaded
- PDF import tool: added support for save/restore Graphics stack. Only for scale and offset transformations and for the linewidth. This is the final fix for Microsoft PDF printer who saves in PDF format 1.7
- PDF Import tool: added support for PDF files that embed multiple Gerber layers (top, bottom, outline, silkscreen etc). Each will be opened in it's own Gerber file. The requirement is that each one is drawn in a different color
- PDF Import tool: fixed bugs when drag & dropping PDF files on canvas the files geometry previously opened was added to the new one. Also scaling issues. Solved.
- PDF Import tool: added support for detection of circular geometry drawn with white color which means actually invisible color. When detected, FlatCAM will build an Excellon file out of those geoms.
- PDF Import tool: fixed storing geometries in apertures with the right size (before they were all stored in aperture D10)

21.04.2019

- fixed the PDF import tool to work with files generated by the Microsoft PDF printer (chained subpaths)
- in PDF import tool added support for paths filled and at the same time stroked ('B' and 'B*'commands)
- added a shortcut key for PDF Import Tool (Alt+Q) and updated the Shortcut list (also with the 'T' and 'R' keys for Gerber Editor where they control the bend in Track and Region tool and the 'M' and 'D' keys for Add Arc tool in Geometry Editor)

20.04.2019

- finished adding the PDF import tool although it does not support all kinds of outputs from PDF printers. Microsoft PDF printer is not supported.

19.04.2019

- started to work on PDF import tool


18.04.2019

- Gerber Editor: added custom mouse cursors for each mode in Add Track Tool
- Gerber Editor: Poligonize Tool will first fuse polygons that touch each other and at a second try will create a polygon. The polygon will be automatically moved to Aperture 0 (regions).
- Gerber Editor: Region Tool will add regions only in 0 aperture
- Gerber Editor: the bending mode will now survive until the tool is exited
- Gerber Editor: solved some bugs related with deleting an aperture and updating the last_selected_aperture

17.04.2019

- Gerber Editor: added some messages to warn user if no selection exists when trying to do aperture deletion or aperture geometry deletion
- fixed version check
- added custom mouse cursors for some tools in Gerber Editor
- Gerber Editor: added multiple modes to lay a Region: 45-degrees, reverse 45-degrees, 90-degrees, reverse 90-degrees and free-angle. Added also key shortcuts 'T' and 'R' to cycle forward, respectively in reverse through the modes.
- Excellon Editor: fixed issue not remembering last tool after adding a new tool
- added custom mouse cursors for Excellon and Geometry Editors in some of their tools

16.04.2019

- added ability to use ENTER key to finish tool adding in Editors, NCC Tool, Paint Tool and SolderPaste Tool.
- Gerber Editor: started to add modes of laying a track
- Gerber Editor: Add Track Tool: added 5 modes for laying a track: 45-degrees, reverse-45 degrees, 90-degrees, reverse 90-degrees and free angle. Key 'T' will cycle forward through the modes and key 'R' will cycle in reverse through the track laying modes.
- Gerber Editor: Add Track Tool: first right click will finish the track. Second right click will exit the Track Tool and return to Select Tool.
- Gerber Editor: added protections for the Pad Array and Pad Tool for the case when the aperture size is zero (the aperture where to store the regions)

15.04.2019

- working on a new tool to process automatically PcbWizard Excellon files which are generated in 2 files
- finished ToolPcbWizard; it will autodetect the Excellon format, units from the INF file
- Gerber Editor: reduced the delay to show UI when editing an empty Gerber object
- update the order of event handlers connection in Editors to first connect new handlers then disconnect old handlers. It seems that if nothing is connected some VispY functions like canvas panning no longer works if there is at least once nothing connected to the 'mouse_move' event
- Excellon Editor: update so always there is a tool selected even after the Excellon object was just edited; before it always required a click inside of the tool table, not you do it only if needed.
- fixed the menu File -> Edit -> Edit/Close Editor entry to reflect the status of the app (Editor active or not)
- added support in Excellon parser for autodetection of Excellon file format for the Excellon files generated by the following ECAD sw: DipTrace, Eagle, Altium, Sprint Layout
- Gerber Editor: finished a new tool: Poligonize Tool (Alt+N in Editor). It will fuse a selection of tracks into a polygon. It will fill a selection of polygons if they are apart and it will make a single polygon if the selection is overlapped. All the newly created filled polygons will be stored in aperture 0 (if it does not exist it will be automatically created)
- fixed a bug in Move command in context menu who crashed the app when triggered
- Gerber Editor: when adding a new aperture it will be store as the last selected and it will be used for any tools that are triggered until a new aperture is selected.

14.04.2019

- Gerber Editor: Remade the processing of 'clear_geometry' (geometry generated by polygons made with Gerber LPC command) to work if more than one such polygon exists
- Gerber Editor: a disabled/enabled sequence for the VisPy cursor on Gerber edit make the graphics better
- Editors: activated an old function that was no longer active: each tool can have it's own set of shortcut keys, the Editor general shortcut keys that are letters are overridden
- Gerber and Geometry editors, when using the Backspace keys for certain tools, they will backtrack one point but now the utility geometry is immediately updated
- In Geometry Editor I fixed bug in Arc modes. Arc mode shortcut key is now key 'M' and arc direction change shortcut key is 'D'
- moved the key handler out of the Measurement tool to flatcamGUI.FlatCAMGui.keyPressEvent()
- Gerber Editor: started to add new function of poligonize which should make a filled polygon out of a shape
- cleaned up Measuring Tool
- solved bug in Gerber apertures size and dimensions values conversion when file units are different than app units

13.04.2019

- updating the German translation
- Gerber Editor: added ability to change on the fly the aperture after one of the tools: Add Pad or Add Pad Array is activated
- Gerber Editor: if a tool is cancelled via key shortcut ESCAPE, the selection is now deleted and any other action require a new selection
- finished German translation (Google translated with some adjustments)
- final fix for issue #277. Previous fix was applied only for one case out of three.
- RELEASE 8.913

12.04.2019

- Gerber Editor: added support for Oblong type of aperture
- fixed an issue with automatically filled in aperture code when the edited Gerber file has no apertures; established an default with value 10 (according to Gerber specifications)
- fixed a bug in editing a blank Gerber object
- added handlers for the Gerber Editor context menu
- updated the translation template POT file and the EN PO/MO files
- Gerber Editor: added toggle effect to the Transform Tool
- Gerber Editor: added shortcut for Transform Tool and also toggle effect here, too
- updated the shortcut list with the Gerber Editor shortcut keys
- Gerber Editor: fixed error when adding an aperture with code value lower than the ones that already exists
- when adding an aperture with code 0 (zero) it will automatically be set with size zero and type: 'REG' (from region); here we store all the regions from a Gerber file, the ones without a declared aperture
- Gerber Editor: added support for Gerber polarity change commands (LPD, LPC)
- moved the polarity change processing from AppGerberEditor() class to camlib.Gerber().parse_lines()
- made optional the saving of an edited object. Now the user can cancel the changes to the object.
- replaced the standard buttons in the QMessageBox's used in the app with custom ones that can have text translated
- updated the POT translation file and the MO/PO files for English and Romanian language

11.04.2019

- changed the color of the marked apertures to the global_selection_color
- Gerber Editor: added Transformation Tool and Rotation key shortcut
- in all Editors, manually deactivating a button in the editor toolbar will automatically select the 'Select' button
- fixed Excellon Editor selection: when a tool is selected in Tools Table, all the drills belonging to that tool are selected. When a drill is selected on canvas, the associated tool will be selected without automatically selecting all other drills with same tool
- Gerber Editor: added Add Pad Array tool
- Gerber Editor: in Add Pad Array tool, if the pad is not circular type, for circular array the pad will be rotated to match the array angle
- Gerber Editor: fixed multiple selection with key modifier such that first click selects, second deselects

10.04.2019

- Gerber Editor: added Add Track and Add Region functions
- Gerber Editor: fixed key shortcuts
- fixed setting the Layout combobox in Preferences according to the current layout
- created menu links and shortcut keys for adding a new empty Gerber objects; on update of the edited Gerber, if the source object was an empty one (new blank one) this source obj will be deleted
- removed the old apertures editing from Gerber Obj selected tab
- Gerber Editor: added Add Pad (circular or rectangular type only)
- Gerber Editor: autoincrement aperture code when adding new apertures
- Gerber Editor: automatically calculate the size of the rectangular aperture

9.04.2019

- Gerber Editor: added buffer and scale tools
- Gerber Editor: working on aperture selection to show on Aperture Table
- Gerber Editor: finished the selection on canvas; should be used as an template for the other Editors
- Gerber Editor: finished the Copy, Aperture Add, Buffer, Scale, Move including the Utility geometry
- Trying to fix bug in Measurement Tool: the mouse events don't disconnect
- fixed above bug in Measurement Tool (but there is a TODO there)

7.04.2019

- default values for Jump To function is jumping to origin (0, 0)

6.04.2019

- fixed bug in Geometry Editor in buffer_int() function that created an Circular Reference Error when applying buffer interior on a geometry.
- fixed issue with not possible to close the app after a project save.
- preliminary Gerber Editor.on_aperture_delete() 
- fixed 'circular reference' error when creating the new Gerber file in Gerber Editor
- preliminary Gerber Editor.on_aperture_add()

5.04.2019

- Gerber Editor: made geometry transfer (which is slow) to Editor to be multithreaded
- Gerber Editor: plotting process is showed in the status bar
- increased the number of workers in FlatCAM and made the number of workers customizable from Preferences
- WIP in Gerber Editor: geometry is no longer stored in a Rtree storage as it is not needed
- changed the way delayed plot is working in Gerber Editor to use a Qtimer instead of python threading module
- WIP in Gerber Editor
- fixed bug in saving the maximized state
- fixed bug in applying default language on first start
~~- on activating 'V' key shortcut (zoom fit) the mouse cursor is now jumping to origin (0, 0)~~
- fixed bug in saving toolbars state; the file was saved before setting the self.defaults['global_toolbar_view]

4.04.2019

- added support for Gerber format specification D (no zero suppression) - PCBWizard Gerber files support
- added support for Excellon file with no info about tool diameters - PCB Wizard Excellon file support
- modified the bogus diameters series for Excellon objects that do not have tool diameter info
- made Excellon Editor aware of the fact that the Excellon object that is edited has fake (bogus) tool diameters and therefore it will not sort the tools based on diameter but based on tool number
- fixed bug on Excellon Editor: when diameter is edited in Tools Table and the target diameter is already in the tool table, the drills from current tool are moved to the new tool (with new dia) - before it crashed
- fixed offset after editing drill diameters in Excellon Editor.

3.04.2019

- fixed plotting in Gerber Editor
- working on GUI in Gerber Editor
- added a Gcode end_command: default is M02
- modified the calling of the on_editing_finished() slot function to fix an issue with updating geometry imported from SVG file, after edit
- working on Gerber Editor - added the key shortcuts: wip
- made saving of the project file non-blocking and also while saving the project file, if the user tries again to close the app while project file is being saved, the app will close only after saving is complete (the project file size is non zero)
- fixed the camlib.Geometry.import_svg() and camlib.Gerber.bounds() to work when importing SVG files as Gerber

31.03.2019

- fixed issue #281 by making generation of a convex shape for the freeform cutout in Tool Cutout a choice rather than the default
- fixed bug in Tool Cutout, now in manual cutout mode the gap size reflect the value set
- changed Measuring Tool to use the mouse click release instead of mouse click press; also fixed a bug when using the ESC key.
- fixed errors when the File -> New Project is initiated while an Editor is still active.
- the File->Exit action handler is now self.final_save() 
- wip in Gerber editor

29.03.2019

- update the TCL keyword list
- fix on the Gerber parser that makes searching for '%%' char optional when doing regex search for mode, units or image polarity. This allow loading Gerber files generated by the ECAD software TCl4.4
- fix error in plotting Excellon when toggling units
- FlatCAM editors now are separated each in it's own file
- fixed TextTool in Geometry Editor so it will open the notebook on activation and close it after finishing text adding
- started to work on a Gerber Editor
- added a fix in the Excellon parser by allowing a comma in the tool definitions between the diameter and the rest

28.03.2019

- About 45% progress in German translation
- new feature: added ability to edit MultiGeo geometry (geometry from Paint Tool)
- changed all the info messages that are of type warning, error or success so they have a space added after the keyword
- changed the Romanian translation by adding more diacritics  
- modified Gerber parser to copy the follow_geometry in the self.apertures
- modified the Properties Tool to show the number of elements in the follow_geometry for each aperture
- modified the copy functions to copy the follow_geometry and also the apertures if it's possible (only for Gerber objects)

27.03.2019

- added new feature: user can delete apertures in Advanced mode and then create a new FlatCAM Gerber object
- progress in German translation. About 27% done.
- fixed issue #278. Crash on name change in the Name field in the Selected Tab.

26.03.2019

- fixed an issue where the Geometry plot function protested that it does not have an parameter that is used by the CNCJob plot function. But both inherit from FaltCAMObj plot function which does not have that parameter so something may need to be changed. Until then I provided a phony keyboard parameter to make that function 'shut up'
- fixed bug: after using Paint Tool shortcut keys are disabled
- added CNCJob geometry for the holes created by the drills from Excellon objects

25.03.2019

- in the TCL completer if the word is already complete don't add it again but add a space
- added all the TCL keywords in the completer keyword list
- work in progress in German translation ~7%
- after any autocomplete in TCL completer, a space is added
- fixed an module import issue in NCC Tool
- minor change (optimization) of the CNCJob UI
- work in progress in German translation ~20%

22.03.2019

- fixed an error that created a situation that when saving a project with some of the CNCJob objects disabled, on project reload the CNCJob objects are no longer loaded
- fixed the Gerber.merge() function. When some of the Gerber files have apertures with same id, create a new aperture id for the object that is fused because each aperture id may hold different geometries.
- changed the autoname for saving Preferences, Project and PNG file

20.03.2019

- added autocomplete finish with ENTER key for the TCL Shell
- made sure that the autocomplete function works only for FlatCAM Scripts
- ESC key will trigger normal view if in full screen and the ESC key is pressed
- added an icon and title text for the Toggle Units QMessageBox

19.03.2019

- added autocomplete for Code editor;
- autocomplete in Code Editor is finished by hitting either TAB key or ENTER key
- fixed the Gerber.merge() to work for the case when one of the merged Gerber objects solid_geometry type is Polygon and not a list

18.03.2019

- added ability to create new scripts and open scripts in FlatCAM Script Editor
- the Code Editor tab name is changed according to the task; 'save' and 'open' buttons will have filters installed for the QOpenDialog fit to the task
- added ability to run a FlatCAM Tcl script by double-clicking on the file
- in Code Editor added shortcut combo key Ctrl+Shift+V to function as a Special Paste that will replace the '\' char with '/' so the Windows paths will be pasted correctly for TCL Shell. Also doing SHIFT + LMB on the Paste in contextual menu is doing the same.

17.03.2019

- remade the layout in 2Sided Tool
- work in progress for translation in Romanian - 91%
- changed some of the app strings formatting to work better with Poedit translation software
- fixed bug in Drillcncjob TclCommand
- finished translation in Romanian
- made the translations work when the app is frozen with CX_freeze
- some formatting changes for the application strings
- some changes on how the first layout is applied
- minor bug fixes (typos from copy/paste from another part of the program)

16.03.2019

- fixed bug in Paint Tool - Single Poly: no geometry was generated
- work in progress for translation in Romanian - 70%

13.03.2019

- made the layout combobox current item from Preferences -> General window to reflect the current layout
- remade the POT translate file
- work in progress in translation for Romanian language 44%
- fix for showing tools by activating them from the Menu - final fix.

11.03.2019

- changed some icons here and there
- fixed the Properties Project menu entry to work on the new way
- in Properties tool now the Gerber apertures show the number of polygons in 'solid_geometry' instead of listing the objects
- added a visual cue in Menu -> Edit about the entries to enter the Editor and to Save & Exit Editor. When one is enabled the other is disabled.
- grouped all the UI files in flatcamGUI folder
- grouped all parser files in flatcamParsers folder
- another changes to the final_save() function
- some strings were left outside the translation formatting - fixed
- finished the replacement of '_' symbols throughout the app which conflicted with the _() function used by the i18n
- reverted changes in Tools regarding the toggle effect - now they work as expected

10.03.2019

- added a fix in the Gerber parser when adding the geometry in the self.apertures dict for the case that the current aperture is None (Allegro does that)
- finished support for internationalization by adding a set of .po/.mo files for the English language. Unfortunately the final action can be done only when Beta will be out of Beta (no more changes) or when I will decide to stop working on this app.
- changed the tooltip for 'feedrate_rapids' parameter to point out that this parameter is useful only for the Marlin preprocessor
- fix app crash for the case that there are no translation files
- fixed some forgotten strings to be prepared for internationalization in ToolCalculators
- fixed Tools menu no longer working due of changes
- added some test translation for the ToolCalculators (in Romanian)
- fixed bug in ToolCutOut where for each tool invocation the signals were reconnected
- fixed some issues with ToolMeasurement due of above changes
- updated the App.final_save() function
- fixed an issue created by the fact that I used the '_' char inside the app to designate unused info and that conflicted with the _() function used by gettext
- made impossible to try to reapply current language that it's already applied (un-necessary)

8.03.2019

- fixed issue when doing th CTRL (or SHIFT) + LMB, the focus is automatically moved to Project Tab
- further work in internationalization, added a fallback to English language in case there is no translation for a string
- fix for issue #262: when doing Edit-> Save & Close Editor on a Geometry that is not generated through first entering into an Editor, the geometry disappear
- finished preparing for internationalization for the files: camlib and objectCollection
- fixed tools shortcuts not working anymore due of the new toggle parameter for the .run().
- finished preparing for internationalization for the files: FlatCAMEditor, MainGUI
- finished preparing for internationalization for the files: FlatCAMObj, ObjectUI
- sorted the languages in the Preferences combobox

7.03.2019

- made showing a shape when hovering over objects, optional, by adding a Preferences -> General parameter
- starting to work in internationalization using gettext()
- Finished adding _() in FlatCAM Tools
- fixed Measuring Tool - after doing a measurement the Notebook was switching to Project Tab without letting the user see the results
- more work on the translation engine; the app now restarts after a language is applied
- added protection against using Travel Z parameter with negative or zero value (in Geometry).
- made sure that when the Measuring Tools is active after last click the Status bar is no longer deleted

6.03.2019

- modified the way the FlatCAM Tools are run from toolbar as opposed of running them from other sources
- some Gerber UI changes

5.03.2019

- modified the grbl-laser preprocessor lift_code()
- treated an error created by Z_Cut parameter being None
- changed the hover and selection box transparency

4.03.2019

- finished work on object hovering
- fixed Excellon object move and all the other transformations
- starting to work on Manual Cutout Tool
- remade the CutOut Tool
- finished Manual Cutout Tool by adding utility geometry to the cutting geometry
- added CTRL + click behavior for adding manual bridge gaps in Cutout Tool
- in Tool Cutout added shortcut key 'Escape' to cancel the current adding of bridge gaps

3.03.2019

- minor UI changes for Gerber UI
- ~~after an object move, the apertures plotted shapes are deleted from canvas and the 'mark all' button is deselected~~
- after move tool action or any other transform (rotate, skew, scale, mirror, offset), the plotted apertures are kept plotted.
- changing units now will convert all the default values from one unit type to another
- prettified the selection shape and the moving shape
- initial work in object hovering shape

02.03.2019

- fixed offset, rotate, scale, skew for follow_geometry. Fixed the move tool also.
- fixed offset, rotate, scale, skew for 'solid_geometry' inside the self.apertures.

28.02.2019

- added a change that when a double click is performed in a object on canvas resulting in a selection, if the notebook is hidden then it will be displayed
- progress in ToolChange Custom commands replacement and rename

27.02.2019

- made the Custom ToolChange Text area in CNCJob Selected Tab depend on the status of the ToolChange Enable Checkbox even in the init stage.
- added some parameters throughout camlib gcode generation functions; handled some possible errors (e.g like when attempting to use an empty Custom GCode Toolchange)
- added toggle effect for the tools in the toolbar.
- enhanced the toggle effect for the tools in the Tools Toolbar and also for Notebook Tab selection: if the current tool is activated it will toggle the notebook side but only if the installed widget is itself. If coming from another tool, the notebook will stay visible
- upgraded the Tool Cutout when done from Gerber file to create a convex_hull around the Gerber file rather than trying to isolate it
- added some protections for the FlatCAM Tools run after an object was loaded

26.02.2019

- added a function to read the parameters from ToolChange macro Text Box (I need to move it from CNCJob to Excellon and Geometry)
- fixed the geometry adding to the self.apertures in the case when regions are done without declaring any aperture first (Allegro does that). Now, that geometry will be stored in the 0 aperture with type REG
- work in progress to Toolchange_Custom code replacement -> finished the parse and replace function
- fixed mouse selection on canvas, mouse drag, mouse click and mouse double click
- fixed Gerber Aperture Table dimensions
- added a Mark All button in the Gerber aperture table.
- because adding shapes to the shapes collection (when doing Mark or Mark All) is time consuming I made the plot_aperture() threaded.
- made the polygon fusing in modified Gerber creation, a list comprehension in an attempt for optimization
- when right clicking the files in Project tab, the Save option for Excellon no longer export it but really save the original. 
- in ToolChange Custom Code replacement, the Text Box in the CNCJob Selected tab will be active only if there is a 'toolchange_custom' in the name of the preprocessor file. This assume that it is, or was created having as template the Toolchange Custom preprocessor file.


25.02.2019

- fixed the Gerber object UI layout
- added ability to mark individual apertures in Gerber file using the Gerber Aperture Table
- more modifications for the Gerber UI layout; made 'follow' an advanced Gerber option
- added in Preferences a new Category: Gerber Advanced Options. For now it controls the display of Gerber Aperture Table and the "follow" attribute4
- fixed GerberObject.merge() to merge the self.apertures[ap]['solid_geometry'] too
- started to work on a new feature that allow adding a ToolChange GCode macro - GUI added both in CNCJob Selected tab and in CNCJob Preferences
- added a limited 'sort-of' Gerber Editor: it allows buffering and scaling of apertures

24.02.2019

- fixed a small bug in the Tool Solder Paste: the App don't take into consideration pads already filled with solder paste.
- prettified the defaults files and the recent file. Now they are ordered and human readable
- added a Toggle Code Editor Menu and key shortcut
- added the ability to open FlatConfig configuration files in Code Editor, Modify them and then save them.
- added ability to double click the FlatConfig files and open them in the FlatCAM Code Editor (to be verified)
- when saving a file from Code Editor and there is no object active then the OpenFileDialog filters are reset to FlatConfig files.
- reverted a change in GCode that might affect Gerber polarity change in Gerber parser
- ability to double click the FlatConfig files and open them in the FlatCAM Code Editor - fixed and verified
- fixed the Set To Origin function when Escape was clicked
- added all the Tools in a new ToolBar
- fixed bug that after changing the layout all the toolbar actions are no longer working
- fixed bug in Set Origin function
- fixed a typo in Toolchange_Probe_MACH3 preprocessor

23.02.2019

- remade the SolderPaste geometry generation function in ToolSoderPaste to work in certain scenarios where the Gerber pads in the SolderPaste mask Gerber may be just pads outlines
- updated the Properties Tool to include more information's, also details if a Geometry is of type MultiGeo or SingleGeo
- remade the Preferences GUI to include the Advanced Options in a separate way so it is obvious which are displayed when App Level is Advanced.
- added protection, not allowing the user to make a Paint job on a MultiGeo geometry (one that is converted in the Edit -> Conversion menu)) because it is not supported

22.02.2019

- added Repetier preprocessor file
- removed "added ability to regenerate objects (it's actually deletion followed by recreation)" because of the way Python pass parameters to functions by reference instead of copy
- added ability to toggle globally the display of ToolTips. Edit -> Preferences -> General -> Enable ToolTips checkbox.
- added true fullscreen support (for Windows OS)
- added the ability of context menu inside the GuiElements.FCCombobox() object.
- remade the UI for ToolSolderPaste. The object comboboxes now have context menu's that allow object deletion. Also the last object created is set as current item in comboboxes.
- some GUI elements changes

21.02.2019

- added protection against creating CNCJob from an empty Geometry object (with no geometry inside)
- changed the shortcut key for YouTube channel from F2 to key F4
- changed the way APP LEVEL is showed both in Edit -> Preferences -> General tab and in each Selected Tab. Changed the ToolTips content for this.
- added the functions for GCode View and GCode Save in Tool SolderPaste
- some work in the Gcode generation function in Tool SolderPaste
- added protection against trying to create a CNCJob from a solder_paste dispenser geometry. This one is different than the default Geometry and can be handled only by SolderPaste Tool.
- ToolSolderPaste tools (nozzles) now have each it's own settings
- creating the camlib functions for the ToolSolderPaste gcode generation functions
- finished work in ToolSolderPaste
- fixed issue with not updating correctly the plot kind (all, cut, travel) when clicking in the CNC Tools Table plot buttons
- made the GCode Editor for ToolSolderPaste clear the text before updating the Code Editor tab
- all the Tabs in Plot Area are closed (except Plot Area itself) on New Project creation
- added ability to regenerate objects (it's actually deletion followed by recreation)

20.02.2019

- finished added a Tool Table for Tool SolderPaste
- working on multi tool solder paste dispensing
- finished the Edit -> Preferences defaults section
- finished the UI, created the preprocessor file template
- finished the multi-tool solder paste dispensing: it will start using the biggest nozzle, fill the pads it can, and then go to the next smaller nozzle until there are no pads without solder.

19.02.2019

- added the ability to compress the FlatCAM project on save with LZMA compression. There is a setting in Edit -> Preferences -> Compression Level between 0 and 9. 9 level yields best compression at the price of RAM usage and time spent.
- made FlatCAM able to load old type (uncompressed) FlatCAM projects
- fixed issue with not loading old projects that do not have certain information's required by the new versions of FlatCAM
- compacted a bit more the GUI for Gerber Object
- removed the Open Gerber with 'follow' menu entry and also the open_gerber Tcl Command attribute 'follow'. This is no longer required because now the follow_geometry is stored by default in a Gerber object attribute gerber_obj.follow_geometry
- added a new parameter for the Tcl CommandIsolate, named: 'follow'. When follow = 1 (True) the resulting geometry will follow the Gerber paths.
- added a new setting in Edit -> Preferences -> General that allow to select the type of saving for the FlatCAM project: either compressed or uncompressed. Compression introduce an time overhead to the saving/restoring of a FlatCAM project.
- started to work on Solder Paste Dispensing Tool
- fixed a bug in rotate from shortcut function
- finished generating the solder paste dispense geometry

18.02.2019

- added protections again wrong values for the Buffer and Paint Tool in Geometry Editor
- the Paint Tool in Geometry Editor will load the default values from Tool Paint in Preferences
- when the Tools in Geometry Editor are activated, the notebook with the Tool Tab will be unhidden. After execution the notebook will hide again for the Buffer Tool.
- changed the font in Tool names
- added in Geometry Editor a new Tool: Transformation Tool.
- in Geometry Editor by selecting a shape with a selection shape, that object was added multiple times (one per each selection) to the selected list, which is not intended. Bug fixed.
- finished adding Transform Tool in Geometry Editor - everything is working as intended
- fixed a bug in Tool Transform that made the user to not be able to capture the click coordinates with SHIFT + LMB click combo
- added the ability to choose an App QStyle out of the offered choices (different for each OS) to be applied at the next app start (Preferences -> General -> Gui Pref -> Style Combobox)
- added support for FlatCAM usage with High DPI monitors (4k). It is applied on the next app startup after change in Preferences -> General -> Gui Settings -> HDPI Support Checkbox
- made the app not remember the window size if the app is maximized and remember in QSettings if it was maximized. This way we can restore the maximized state but restore the windows size unmaximized
- added a button to clear the GUI preferences in Preferences -> General -> Gui Settings -> Clear GUI Settings
- added key shortcuts for the shape transformations within Geometry Editor: X, Y keys for Flip(mirror), Shift+X, Shift+Y combo keys for Skew and Alt+X, Alt+Y combo keys for Offset
- adjusted the plotcanvas.zomm_fit() function so the objects are better fit into view (with a border around) 
- modified the GUI in Objects Selected Tab to accommodate 2 different modes: basic and Advanced. In Basic mode, some of the functionality's are hidden from the user.
- added Tool Transform preferences in Edit -> Preferences and used them through out the app
- made the output of Panelization Tool a choice out of Gerber and Geometry type of objects. Useful for those who want to engrave multiple copies of the same design.

17.02.2019

- changed some status bar messages
- New feature: added the capability to view the source code of the Gerber/Excellon file that was loaded into the app. The file is also stored as an object attribute for later use. The view option is in the project context menu and in Menu -> Options -> View Source
- Serialized the source_file of the Objects so it is saved in the FlatCAM project and restored.
- if there is a single tool in the tool list (Geometry , Excellon) and the user click the Generate GCode, use that tool even if it is not selected
- fixed issue where after loading a project, if the default kind of CNCjob view is only 'cuts' the plot will revert to the 'all' type
- in Editors, if the modifier key set in Preferences (CTRL or SHIFT key) is pressed at the end of one tool operation it will automatically continue to that action until the modifier is no longer pressed when Select tool will be automatically selected.
- in Geometry Editor, on entry the notebook is automatically hidden and restored on Geometry Editor exit.
- when pressing Escape in Geometry Editor it will automatically deselect any shape not only the currently selected tool.
- when deselecting an object in Project menu the status bar selection message is deleted
- added ability to save the Gerber file content that is stored in FlatCAM on Gerber file loading. It's useful to recover from saved FlatCAM projects when the source files are no longer available.
- fixed an issue where the function handler that changed the layout had a parameter changed accidentally by an index value passed by the 'activate' signal to which was connected
- fixed bug in paint function in Geometry Editor that didn't allow painting due of overlap value

16.02.2019

- added the 'Save' menu entry to the Project context menu, for CNCJob: it will export the GCode.
- added messages in info bar when selecting objects in the Project View list
- fixed DblSided Tool so it correctly creates the Alignment Drills Excellon file using the new structure
- fixed DblSided Tool so it will not crash the app if the user tries to make a mirror using no coordinates
- added some relevant status bar messages in DblSided Tool
- fixed DblSided Tool to correctly use the Box object (until now it used as reference only Gerber object in spite of Excellon or Geometry objects being available)
- fixed DblSided Tool crash when trying to create Alignment Drills object without a Tool diameter specified
- fixed DblSided Tool issue when entering Tool diameter values with comma decimal separator instead of decimal dot separator
- fixed Cutout Tool Freeform to generate cutouts with options: LR, TB. 2LR, 2TB which didn't worked previously
- fixed Excellon parser to detect correctly the units and zeros for Excellon's generated by Eagle 9.3.0
- modified the initial size of the canvas on startup
- modified the build file (make_win.py) to solve the issue with suddenly not accepting the version as Beta
- changed the initial layout to 'compact'
- updated the install scripts to uninstall a previously installed FlatCAM Beta (that has the same GUID)

15.02.2019

- rearranged the File and Edit menu's and added some explanatory tooltips on certain menu items that could be seen as cryptic
- added Excellon Export Options in Edit -> Preferences
- started to work in using the Excellon Export parameters
- remade the Excellon export function to work with parameters entered in Edit -> Preferences -> Excellon Export
- added a new entry in the Project Context Menu named 'Save'. It will actually work for Geometry and it will do Export DXF and for Excellon and it will do Export Excellon
- reworked the offer to save a project so it is done only if there are objects in the project but those objects are new and/or are modified since last project load (if an old project was loaded.)
- updated the Excellon plot function so it can plot the Excellon's from old projects
- removed the message boxes that popup on Excellon Export errors and replaced them with status bar messages
- small change in tab width so the tabs looks good in Linux, too.

14.02.2019

- added total travel distance for CNCJob object created from Excellon Object in the CNCJob Selected tab
- added 'FlatCAM ' prefix to any detached tab, for easy identification
- remade the Grids context menu (right mouse button click on canvas). Now it has values linked to the units type (inch or mm). Added ability to add or delete grid values and they are persistent.
- updated the function for the project context menu 'Generate CNC' menu entry (Action) to use the modernized function FlatCAMObj.GeometryObject.on_generatecnc_button_click()
- when linked, the grid snap on Y will copy the value in grid snap on X in real time
- in Gerber aperture table now the values are displayed in the current units set in FlatCAM
- added shortcut key 'J' (jump to location) in Editors and added an icon to the dialog popup window
- the notebook is automatically collapsed when there are no objects in the collection and it is showed when adding an object
- added new options in Edit -> Preferences -> General -> App Preferences to control if the Notebook is showed at startup and if the notebook is closed when there are no objects in the collection and showed when the collection has objects.

13.02.2019

- added new parameter for Excellon Object in Preferences: Fast Retract. If the checkbox is checked then after reaching the drill depth, the drill bit will be raised out of the hole asap.
- started to work on GUI forms simplification
- changed the Preferences GUI for Geometry and Excellon Objects to make a difference between parameters that are changed often and those that are not.
- changed the layout in the Selected Tab UI
- started to add apertures table support
- finished Gerber aperture table display
- made the Gerber aperture table not visible as default and added a checkbox that can toggle the visibility
- fixed issue with plotting in CNCJob; with Plot kind set to something else than 'all' when toggling Plot, it was defaulting to kind = 'all'
- added (and commented) an experimental FlatCAMObj.GerberObject.plot_aperture()

12.02.2019

- whenever a FlatCAM tool is activated, if the notebook side is hidden it will be unhidden
- reactivated the Voronoi classes
- added a new parameter named Offset in the Excellon tool table - work in progress
- finished work on Offset parameter in Excellon Object (Excellon Editor, camlib, FlatCAMObj updated to take this param in consideration)
- fixed a bug where in Excellon editor when editing a file, a tool was automatically added. That is supposed to happen only for empty newly created Excellon Objects.
- starting to work on storing the solid_geometry for each tool in part in Excellon Object
- stored solid_geometry of Excellon object in the self.tools dictionary
- finished the solid_geometry restore after edit in Excellon Editor
- finished plotting selection for each tool in the Excellon Tool Table
- fixed the camlib.Excellon.bounds() function for the new type of Excellon geometry therefore fixed the canvas selection, too


10.02.2019

- the SELECTED type of messages are no longer printed to shell from 2 reasons: first, too much spam and second, issue with displaying html
- on set_zero function and creation of new geometry or new excellon there is no longer a zoom fit 
- repurposed shortcut key 'Delete' to delete tools in tooltable when the mouse is over the Seleted tab (with Geometry inside) or in Tools tab (when NCC Tool or Paint Tool is inside). Or in Excellon Editor when mouse is hovering the Selected tab selecting a tool, 'Delete' key will delete that tool, if on canvas 'Delete' key will delete a selected shape (drill). In rest, will delete selected objects.
- adjusted the preprocessor files so the Spindle Off command (M5) is done before the move to Toolchange Z
- adjusted the Toolchange Manual preprocessor file to have more descriptive messages on the toolchange event
- added a strong focus to the object_name entry in the Selected tab
- the keypad keyPressed are now detected correctly
- added a pause and message/warning to do a rough zero for the Z axis, in case of Toolchange_Probe_MACH3 preprocessor file
- changes in Toolchange_Probe_MACH3 preprocessor file

9.02.2019

- added a protection for when saving a file first time, it require a saved path and if none then it use the current working directory
- added into Preferences the Calculator Tools
- made the Preferences window scrollable on the horizontal side (it was only vertically scrollable before)
- fixed an error in Excellon Editor -> add drill array that could appear by starting the function to add a drill array by shortcut before any mouse move is registered while in Editor
- changed the messages from status bar on new object creation/selection
- in Geometry Editor fixed the handler for the Rotate shortcut key ('R')

8.02.2019

- when shortcut keys 1, 2, 3 (tab selection) are activated, if the splitter left side (the notebook) is hidden it will be made visible
- changed the menu entry Toggle Grid name to Toggle Grid Snap
- fixed errors in Toggle Axis
- fixed error with shortcut key triggering twice the keyPressEvent when in the Project List View
- moved all shortcut keys handlers from Editors to the keyPressEvent() handler from FLatCAMGUI
- in Excellon Editor added a protection for Tool_dia field in case numbers using comma as decimal separator are used. Also added a QDoubleValidator forcing a number with max 4 decimals and from 0.0000 to 9.9999
- in Excellon Editor added a shortcut key 'T' that popup a window allowing to enter a new Tool with the set diameter
- in App added a shortcut key 'T' that popup a windows allowing to enter a new Tool with set diameter only when the Selected tab is on focus and only if a Geometry object is selected
- changed the shortcut key for Transform Tool from 'T' to 'Alt+T'
- fixed bug in Geometry Selected tab that generated error when used tool offset was less than half of either total length or half of total width. Now the app signal the issue with a status bar message
- added Double Validator for the Offset value so only float numbers can be entered.
- in App added a shortcut key 'T' that popup a windows allowing to enter a new Tool with set diameter only when the Tool tab is on focus and only if a NCC Tool or Paint Area Tool object is installed in the Tool Tab
- if trying to add a tool using shortcut key 'T' with value zero the app will react with a message telling to use a non-zero value.

7.02.2019

- in Paint Tool, when painting single polygon, when clicking on canvas for the polygon there is no longer a selection of the entire object
- commented some debug messages
- imported speedups for shapely
- added a disable menu entry in the canvas contextual menu
- small changes in Tools layout
- added some new icons in the help menu and reorganized this menu
- added a new function and the shortcut 'leftquote' (left of Key 1) for toggle of the notebook section
- changed the Shortcut list shortcut key to F3
- moved some graphical classes out of Tool Shell to GUIElements.py where they belong
- when selecting an object on canvas by single click, it's name is displayed in status bar. When nothing is selected a blank message (nothing) it's displayed
- in Move Tool I've added the type of object that was moved in the status bar message
- color coded the status bar bullet to blue for selection
- the name of the selected objects are displayed in the status bar color coded: green for Gerber objects, Brown for Excellon, Red for Geometry and Blue for CNCJobs.

6.02.2019

- fixed the units calculators crash FlatCAM when using comma as decimal separator
- done a regression on Tool Tab default text. It somehow delete Tools in certain scenarios so I got rid of it
- fixed bug in multigeometry geometry not having the bounds in self.options and crashing the GCode generation
- fixed bug that crashed whole application in case that the GCode editor is activated on a Tool gcode that is defective. 
- fixed bug in Excellon Slots milling: a value of a dict key was a string instead to be an int. A cast to integer solved it.
- fixed the name self-insert in save dialog file for GCode; added protection in case the save path is None
- fixed FlatCAM crash when trying to make drills GCode out of a file that have only slots.
- changed the messages for Units Conversion
- all key shortcuts work across the entire application; moved all the shortcuts definitions in MainGUI.keyPressEvent()
- renamed the theme to layout because it is really a layout change
- added plot kind for CNC Job in the App Preferences
- combined the geocutout and cutout_any TCL commands - work in progress
- added a new function (and shortcut key Escape) that when triggered it deselects all selected objects and delete the selection box(es) 
- fixed bug in Excellon Gcode generation that made the toolchange X,Y always none regardless of the value in Preferences
- fixed the Tcl Command Geocutout to work with Gerber objects too (besides Geometry objects)

5.02.3019

- added a text in the Selected Tab which is showed whenever the Selected Tab is selected but without having an object selected to display it's properties
- added an initial text in the Tools tab
- added possibility to use the shortcut key for shortcut list in the Notebook tabs
- added a way to set the Probe depth if Toolchange_Probe preprocessors are selected
- finished the preprocessor file for MACH3 tool probing on toolchange event
- added a new parameter to set the feedrate of the probing in case the used preprocessor does probing (has toolchange_probe in it's name)
- fixed bug in Marlin preprocessor for the Excellon files; the header and toolchange event always used the parenthesis witch is not compatible with GCode for Marlin
- fixed a issue with a move to Z_move before any toolchange

4.02.2019

- modified the Toolchange_Probe_general preprocessor file to remove any Z moves before the actual toolchange event
- created a prototype preprocessor file for usage with tool probing in MACH3
- added the default values for Tool Film and Tool Panelize to the Edit -> Preferences
- added a new parameter in the Tool Film which control the thickness of the stroke width in the resulting SVG. It's a scale parameter.
- whatever was the visibility of the corresponding toolbar when we enter in the Editor, it will be set after exit from the Editor (either Geometry Editor or Excellon Editor).
- added ability to be detached for the tabs in the Notebook section (Project, Selected and Tool)
- added ability for all detachable tabs to be restored to the same position from where they were detached.
- changed the shortcut keys for Zoom In, Zoom Out and Zoom Fit from 1, 2, 3 to '-', '=' respectively 'V'. Added new shortcut keys '1', '2', '3' for Select Project Tab, Select Selected Tab and Select Tool Tab.
- formatted the Shortcut List Tab into a HTML table

3.3.2019

- updated the new shortcut list with the shortcuts added lately
- now the special messages in the Shell are color coded according to the level. Before they all were RED. Now the WARNINGS are yellow, ERRORS are red and SUCCESS is a dark green. Also the level is in CAPS LOCK to make them more obvious
- some more changes to GUI interface (solved issues)
- added some status bar messages in the Geometry Editor to guide the user when using the Geometry Tools
- now the '`' shortcut key that shows the 'shortcut key list' in Editors points to the same window which is created in a tab no longer as a pop-up window. This tab can be detached if needed.
- added a remove_tools() function before install_tools() in the init_tools() that is called when creating a new project. Should solve the issue with having double menu entry's in the TOOLS menu
- fixed remove_tools() so the Tcl Shell action is readded to the Tools menu and reconnected to it's slot function
- added an automatic name on each save operation based on the object name and/or the current date
- added more information's for the statistics

2.2.2019

- code cleanup in Tools
- some GUI structure optimization's
- added protection against entering float numbers with comma separator instead of decimal dot separator in key points of FlatCAM (not everywhere)
- added a choice of plotting the kind of geometry for the CNC plot (all, travel and cut kind of geometries) in CNCJob Selected Tab
- added a new preprocessor file named: 'probe_from_zmove' which allow probing to be done from z_move position on toolchange event 
- fixed the snap magnet button in Geometry Editor, restored the checkable property to True
- some more changes in the Editors GUI in deactivate() function
- a fix for saving as empty an edited new and empty Excellon Object

1.02.2019

- fixed preprocessor files so now the bounds values are right aligned (assuming max string length of 9 chars which means 4 digits and 4 decimals)
- corrected small type in list_sys Tcl command; added a protection of the Plot Area Tab after a successful edit.
- remade the way FlatCAM saves the GUI position data from a file (previously) to use PyQt QSettings
- added a 'theme' combo selection in Edit -> Preferences. Two themes are available: standard and compact.
- some code cleanup
- fixed a source of possible errors in DetachableTab Widget.
- fixed gcode conversion/scale (on units change) when multiple values are found on each line
- replaced the pop-up window for the shortcut list with a new detachable tab
- removed the pop-up messages from the rotate, skew, flip commands

31.01.2019

- added a parameter ('Fast plunge' in Edit -> Preferences -> Geometry Options and Excellon Options) to control if the fast move to Z_move is done or not
- added new function to toggle fullscreen status in Menu -> View -> Toggle Full Screen. Shortcut key: Alt+F10
- added key shortcuts for Enable Plots, Disable Plots and Disable other plots functions (Alt+1, Alt+2, Alt+3)
- hidden the snap magnet entry and snap magnet toggle from the main view; they are now active only in Editor Mode
- updated the camlib.CNCJob.scale() function so now the GCode is scaled also (quite a HACK :( it will need to be replaced at some point)). Units change work now on the GCODE also.
- added the bounds coordinates to the GCODE header
- FlatCAM saves now to a file in self.data_path the toolbar positions and the position of TCL Shell
- Plot Area Tab view can now be toggled, added entry in View Menu and shortcut key Ctrl+F10
- All the tabs in the GUI right side are (Plot Are, Preferences etc) are now detachable to a separate windows which when closed it returns in the previous location in the toolbar. Those detached tabs can be also reattached by drag and drop.

30.01.2019

- added a space before Y coordinate in end_code() function in some of the preprocessor files
- added in Calculators Tool an Electroplating Calculator.
- remade the App Menu for Editors: now they will be showed only when the respective Editor is active and hidden when the Editor is closed.
- added a traceback report in the TCL Shell for the errors that don't allow creation of an object; useful to trace exceptions/errors
- in case that the Toolchange X,Y parameter in Selected (or in Preferences) are deleted then the app will still do the job using the current coordinates for toolchange
- fixed an issue in camlib.CNCJob where tha variable self.toolchange_xy was used for 2 different purposes which created loss of information.
- fixed unit conversion functions in case the toolchange_xy parameter is None
- more fixes in camlib.CNCJob regarding usage of toolchange (in case it is None)
- fixed preprocessor files to work with toolchange_xy parameter value = None (no values in Edit - Preferences fields)
- fixed Tcl commands CncJob and DrillCncJob to work with toolchange
- added to the preprocessor files the command after toolchange to go with G00 (fastest) to "Z Move" value of Z pozition.

29.01.2019

- fixed issue in Tool Calculators when a float value was entered starting only with the dot.
- added protection for entering incorrect values in Offset and Scale fields for Gerber and Geometry objects (in Selected Tab)
- added more shortcut keys in the Geometry Editor and in Excellon Editor; activated also the zoom (fit, in, out) shortcut keys ('1' , '2', '3') for the editors
- disabled the context menu in tools table on Paint Tool in case that the painting method is single.
- added protection when trying to do Intersection in Geometry Editor without having selected Geometry items.
- fixed the scale, mirror, rotate, skew functions to work with Geometry Objects of multi-geometry type.
- added a GUI for Excellon Search time for OR-TOOLS path optimization in Edit -> Preferences -> Excellon General -> Optimization Time
- more changes in Edit -> Preferences -> Geometry, Gerber and in CNCJob
- added new option for Cutout Tool Freeform Gaps in Edit -> Preferences -> Tools
- fixed Freeform Cutout gaps issue (it was double than the value set)
- added protection so the Cutout (either Freeform or Rectangular) cannot be done on a multigeo Geometry
- added 2Sided Tool default values in Edit -> Preferences -> Tools
- optimized the FlatCAMCNCJob.on_plot_cb_click_table() plot function and solved a bug regarding having tools numbers not in sync with the cnc tool table

28.01.2018

- fixed the GerberObject.merge() function
- added a new menu entry for the Gerber Join function: Edit -> Conversions -> "Join Gerber(s) to Gerber" allowing joining Gerber objects into a final Gerber object
- moved Paint Tool defaults from Geometry section to the Tools section in Edit -> Preferences
- added key shortcuts for Open Manual = F1 and for Open Online VideoHelp = F2

27.01.2018

- added more key shortcuts into the application; they are now displayed in the GUI menu's
- reorganized the Edit -> Preferences -> Global
- redesigned the messagebox that is showed when quiting ot creating a New Project: now it has an option ('Cancel') to abort the process returning to the app
- added options for trace segmentation that can be useful for auto-levelling (code snippet from Lei Zheng from a rejected pull request on FlatCAM https://bitbucket.org/realthunder/ )
- added shortcut key 'L' for creating 'New Excellon' 
- added shortcut key combo 'Shift+S' for Running a Script.
- modified GRBL_laser preprocessor file so it includes a Sxxxx command on the line with M03 (laser active) whenever a value is enter in the Spindlespeed entry field
- remade the EDIT -> PREFERENCES window, the Excellon and Gerber sections. Created a new section named TOOLS

26.01.2019

- fixed grbl_11 preprocessor in linear_code() function
- added icons to the Project Tab context menu
- added new entries to the Canvas context menu (Copy, Delete, Edit/Save, Move, New Excellon, New Geometry, New Project)
- fixed GRBL_laser preprocessor file
- updated function for copy of an Excellon object for the case when the object has slots
- updated ExcellonObject.merge() function to work in case some (or all) of the merged objects have slots  

25.01.2019

- deleted junk folders
- remade the Panelize Tool: now it is much faster, it is multi-threaded, it works with multitool geometries and it works with multigeo geometries too.
- made sure to copy the options attribute to the final object in the case of: GeometryObject.merge(), GerberObject.merge() and for the Panelize Tool
- modified the panelize TclCommand to take advantage of the new panelize() function; added a 'threaded' parameter (default value is 1) which controls the execution of the panelize TclCommand: threaded or non-threaded
- fixed TclCommand Cutout
- added a new TclCommand named CutoutAny. Keyword: cutout_any

24.01.2019

- trying to fix painting single when the actual painted object it's a MultiPolygon
- fixed the Copy Object function when the object is Gerber
- added the Copy entry to the Project context menu
- made the functions behind Disable and Enable project context menu entries, non-threaded to fix a possible issue
- added multiple object selection on Open ... and Import ... (idea and code snippet came from Travers Carter, BitBucket user https://bitbucket.org/travc/)
- fixed 'GRBL_laser' preprocessor bugs (missing functions)
- fixed display geometry for 'GRBL_laser' preprocessor
- Excellon Editor - added possibility to create an linear drill array rotated at an custom angle
- added the Edit and Properties entries to the Project context menu

23.01.2019

- added a new preprocessor file named 'line_xyz' which have x, y, z values on the same GCode line
- fixed calculation of total path for Excellon Gcode file
- modified the way FlatCAM preferences are saved. Now they can be saved as new files with .FlatConfig extension by the user and shared.
- added possibility to open the folder where FlatCAM is saving the preferences files

21.01.2019

- changed some tooltips
- added tooltips in Excellon tool table headers
- in Excellon Tool Table the columns are now only selectable by clicking on the header (sorting is done automatically)
- if CNCJob from Excellon then hide the CNC tools table in CNCJob Object

 
20.01.2019

- fixed the HPGL code geometry rendering when travel
- fixed the message box layout when asking to save the current work
- made sure that whenever the HPGL preprocessor is selected the Toolchange is always ON and the MultiDepth is OFF
- the HPGL preprocessor entry is not allowed in Excellon Object preprocessor selection combobox as it is only applicable for Geometry
- when saving HPGL code it will be saved as a file with extension .plt
- the units mentioned in HPGL format are only METRIC therefore if FlatCAM units are in INCH they will be transform to METRIC
- the minimum unit in HPGL is 0.025mm therefore the coordinates are rounded to a multiple of 0.025mm
- removed the raise statement in do_worker_task() function as this is fatal in conjunction with PyQt5
- added a try - except clause for the situations when for a font can't be determined the family and name
- moved font parsing to the Geometry Editor: it is done everytime the Text tool is invoked
- made sure that the HPGL preprocessor is not populated in the Excellon preprocessors in Preferences as it make no sense (HPGL is useful only for Geometries)

19.01.2019

- added initial implementation of HPGL preprocessor
- fixed display HPGL code geometry on canvas

11.01.2019

- added a status message for font parsing

9.01.2019

- added a fix to allow creating of Excellon geometry even when there are points with no tools by skipping those points and warning the user about this in a Tcl message
- added a message box asking users if they want to save the project in case that either New Project menu entry is clicked or if Exit menu entry is clicked or if the app is closed from the close button. The message box will be showed only if there are objects in the collection.
- modified the first line in the Gcode header to show the FlatCAM version and version_date

8.01.2019

- added checkboxes in Preferences -> General -> Global Preferences to switch on/off version check at application startup and also to control if the app will send anonymous statistics about FlatCAM usage to help improve FlatCAM

7.01.2019

- added tooltips in Edit->Convert menu
- fixed cutting from copper features when doing Gerber isolation with multiple passes

6.01.2019

- fixed the Marlin preprocessor detection in GCode header
- the version date in GCode header is now the one set in FlatCAMApp.App.version_date
- fixed bug in preprocessor files: number of drills is now calculated only for the Excellon objects in toolchange function (only Excellon objects have drills) 

5.01.2019

- fixed cncjob TclCommand - it used the default values for parameters
- fixed the layout in ToolTransform
- fixed the initial text in the ToolShell
- reactivated the version check in case the release is not BETA; FlatCAMApp.App has now a beta object that when set True the application will show in the Title and help-> About that is Beta (and it disable version checking)
- added a new name (mine: for good and/or bad) to the contributors list
- fixed the Join function to work on Gerber and Excellon, Gerber and Gerber, Excellon and Excelon combination of objects. The merged property is the solid_geometry and the result is a GeometryObject object.

3.01.2019

- initial merge into FlatCAM regular

28.12.2018

- changed the workspace drawing from 'gl' to 'agg'. 'gl' has better performance but it messes with the overlapping graphics
- removed the initial obj.build_ui() in App.on_editing_finished()

25.12.2018

- fixed bugs in Excellon Editor due of PyQt5 port
- fixed bug when loading Gerber with follow
- fixed bug that when a Gerber was loaded with -follow parameter it could not be isolated external and full
- changed multiple status bar messages
- changed some assertions to (status error message + return) combo
- fixed issues in 32bit installers
- added protection against using Excellon joining on different kind of objects
- fixed bug in ToolCutout where the Rectangular Cutout used the Type of Gaps from Freeform Cutout
- fixed bug that didn't allowed saving SVG file from a Gerber file
- modified setup_ubuntu.sh file for PyQt5 packages

23.12.2018

- added move (as in Tool Move) capability for CNCJob object and the GCode is updated on each move --> finished both for Gcode loaded and for CNCJob generated in the app
- fixed some errors related to DialogOpen widget that I've missed in PyQt5 porting
- added a bounds() method for CNCJob class in camlib (perhaps overdone as it worked well with the one inherited)
- small changes in Paint Tool - the rest machining is working only partially
- added more columns in CNCjob Tool Table showing more info about the present tools
- make the columns in CNCJob Tool Table not editable as it has no sense

22.12.2018

- fixed issues in Transform Tool regarding the message boxes
- fixed more error in Double Sided Tool and added some more information's in ToolTips
- added more information's in CutOut Tool ToolTips
- updated the tooltips in amost all FlatCAM tools; in Tool Tables added column header ToolTips
- fixed NCC rest machining in NCC Tool; added status message and stop object creation if there is no geometry on any tool
- fixed version number: now it will made of a number in format main_version.secondary_version/working_version
- modified the makefile for windows builds to accommodate both 32bit and 64bit executable generation

21.12.2018

- added shortcut "SHIFT + W" for workspace toggle
- updated the list of shortcuts
- forbid editing for the MultiGeo type of Geometry because the Geometry Editor is not prepared for this
- finished a "sort" of rest-machining for Non Copper Clearing Tool but it's time consuming operation
- reworked the NCC Tool as it was fundamental wrong - still has issues on the rest machining
- added a parameter reset for each run of Paint Tool and NCC Tool

20.12.2018

- porting application to PyQt5
- adjusted the level of many status bar messages
- created new bounds() methods for Excellon and Gerber objects as the one inherited from Geometry failed in conjunction with PyQt5
- fixed some small bugs where a string was divided by a float finally casting the result to an integer
- removed the 'raise' conditions everywhere I could and make protections against loading files in the wrong place
- fixed a "PyCharm stupid paste on the previous tab level even after one free line " in Excellon.bounds()
- in Geometry object fixed error in tool_delete regarding deletion while iterating a dict
- started to rework the NCC Tool to generate one file only
- in Geometry Tool Table added checkboxes for individual plot of tools in case of MultiGeo Geometry
- rework of NCC Tool UI
- added a automatic selector: if the system is 32bit the OR-tools imports are not done and the OR-tools drill path optimizations are replaced by a default Travelling Salesman drill path optimization
- created a Win32 make file to generate a Win32 executable
- disabled the Plot column in Geometry Tool Table when the geometry is SingleGeo as it is not needed
- solved a issue when doing isolation, if the solid_geometry is not a list will make it a list
- added tooltips in the Geometry Tool Table headers explaining each column
- added a new Tcl Command: clear. It clears the Tcl Shell of all text and restore it to the original state
- fixed Properties Tool area calculation; added status bar messages if there is no object selected show an error and successful showing properties is confirmed in status bar
- when Preferences are saved, now the default values are instantly propagated within the application
- when a geometry is MultiGeo and all the tools are deleted, it will have no geometry at all therefore all that it's plotted on canvas that used to belong to it has to be deleted and because now it is an empty object we demote it to SingleGeo so it can be edited

19.12.2018

- fixed SVG_export for MultiGeo Geometries
- fixed DXF_export for MultiGeo Geometries
- fixed SingleGeo to MultiGeo conversion plotting bug

18.12.2018

- small changes in GeometryObject.plot()
- updated the GeometryObject.merge() function and the Join Geometry feature to accommodate the different types of geometries: singlegeo and multigeo type
- added Conversion submenu in Edit where I moved the Join features and added the Convert from MultiGeo to SingleGeo type and the reverse
- added Copy Tool (on a selection of tools) feature in Geometry Object UI 
- fixed the bounds() method for the MultiGeo geometry object so the canvas selection is working and also the Properties Tool
- fixed Move Tool to support MultiGeo geometry objects moving
- added tool edit in Geometry Object Tool Table
- added Tool Table context menu in Geometry Object and in Paint Tool
- modified some Status Bar messages in Geometry Object

17.12.2018

- added support for multiple solid_geometry in a geometry object; each tool can now have it's own geometry. Plot, project save/load are OK.
- added support for single GCode file generation from multi-tool PaintTool job
- added protection for results of Paint Tool job that do not have geometry at all. An Error will be issued. It can happen if the combination of Paint parameters is not good enough
- solved a small bug that didn't allow the Paint Job to be done with lines when the results were geometries not iterable 
- added protection for the case when trying to run the cncjob Tcl Command on a Geometry object that do not have solid geometry or one that is multi-tool
- Paint Tool Table: now it is possible to edit a tool to a new diameter and then edit another tool to the former diameter of the first edited tool
- added a new type of warning, [WARNING_NOTCL]
- fixed conflict with "space" keyboard shortcut for CNC job

16.12.2018

- redone the Options menu; removed the Transfer Options as they were not used
- deleted some folders in the project structure that were never used
- Paint polygon Single works only for left mouse click allowing mouse panning
- added ability to print errors in status bar without raising Tcl Shell
- fixed small bug: when doing interiors isolation on a Gerber that don't allow it, no object is created now and an error in the status bar is issued
- fixed bug in Paint All for Geometry made from exteriors Gerber isolation
- fixed the join geometry: when the geometries has different tools the join will fail with a status bar message (as it should). Allow joining of geometries that have no tool. // Reverted on 18.12.2018
- changed the error messages that are simple to the kind that do not open the TCl shell
- fixed some issues in Geometry Object
- Paint Tool - reworked the UI and made it compatible with the Geometry Object UI
- Paint Tool - tool edit functional
- added Clear action in the Context menu of the TCl Shell

14.12.2018

- fixed typo in setup_ubuntu.sh
- minor changes in Excellon Object UI
- added Tool Table in Paint Tool
- now in Paint Tool and Non Copper Clearing Tool a selection of tools can be deleted (not only one by one)
- minor GUI changes (added/corrected tooltips)
- optimized vispy startup time from about >6 sec to ~3 seconds
- removed vispy text collection starting in plotcanvas as it did nothing // RESTORED 18.12.2018 as it messed the graphical presentation
- fixed cncjob TclCommand for the new type of Geometry
- make sure that when using the TclCommands, the object names are case insensitive
- updated the TCL Shell auto-complete function; now it will index also the names of objects created or loaded in the application
- on object removal the name is removed from the Shell auto-complete model

13.12.2018

NEW Geometry Object and CNC Object architecture (3rd attempt) which allow multiple tools for one geometry

- fixed issue with cumulative G-code after successive delete/create of a CNCJob on the same geometry (some references were kept after deletion of CNCJob object which kept the deleted tools data and added it to a new one)
- fixed plot and export G-code in new format
- fixed project save/load in the new format for geometry
- added new feature in CNCJob Object UI: since we may have multiple tools per CNCJob object due of having multiple tool in Geometry Object,
now there is a Tool Table in CNC Object UI and each tool GCode can be enabled or disabled

12.12.2018

- Geometry Tool Table: when the Offset type is 'custom' each tool it's storing the value and it is updated on UI when that tool is selected in UI table
- Geometry Tool Table: fixed tool offset conversion when the Offset in Tool Table UI is set to Custom

11.12.2018

- cleaned up the generatecncjob() function in FlatCAMObj
- created a new function for generating cncjob out of multitool geometry, mtool_generate_cncjob()
- cleaned up the generate_from_geometry_2() method in camlib
- Geometry Tool Table: new tool added copy all the form fields (data) from the last tool
- finished work on generation of a single CNC Job file (therefore a single GCODE file) even for multiple tools in Geo Tool Table
- GCode header is added only on saving the file therefore the time generation will be reflected in the file
- modified preprocessors to accommodate the new CNC Job file with multiple tools
- modified preprocessors so the last X,Y move will be to the toolchange X,Y pos (set in Preferences)
- save_project and load_project now work with the new type of multitool geometry and cncjob objects

10.12.2018

- added new feature in Geometry Tool Table: if the Offset type in tool table is 'Offset' then a new entry is unhidden and the user can use custom offset
- Geometry Tool Table: fixed add new tool with diameter with many decimals
- Geometry Tool Table: when editing the tip dia or tip angle for the V Shape tool, the CutZ is automatically calculated

9.12.2018

- new Geometry Tool Table has functional unit conversion
- when entering a float number in Spindle Speed now there is no error and only the integer part is used, the decimals are discarded
- finished the Geometry Tool Table in the form that generates only multiple files
- if tool type is V-Shape ('V') then the Cut Z entry is disabled and new 'Tip Dia' and 'Tip Angle' fields are showed. The values entered will calculate the Cut Z parameter

5.12.2018

- remade the Geometry Tool Table, before this change each tool could not store it's own set of data in case of multiple tools with same diameter
- added a new column in Geo Tool Table where to specify which type of tool to use: C for circular, B for Ball and V for V-shape

4.12.2018

- new geometry/excellon object name is now only "new_g"/"new_e" as the type is clear from the category is into (and the associated icon)
- always autoselect the first tool in the Geometry Tool table
- issue error message if the user is trying to generate CNCJob without a tool selected in Geometry Tool Table
- add the whole data from Geometry Object GUI as dict in the geometry tool dict so each tool (file) will have it's own set of data

3.12.2018

- Geometry Tool table: delete multiple tools with same diameter = DONE
- Geometry Tool table: possibility to cut a path inside or outside or on path = DONE
- Geometry Tool table: fixed situation when user tries to add a tool but there is no tool diameter entered
- if a geometry is a closed shape then create a Polygon out of it
- some fixes in Non Copper Clearing Tool
- Geometry Tool table: added option to delete_tool function for delete_all
- Geometry Tool table: added ability to delete even the last tool in plugin_table and added an warning if the user try to generate a CNC Job without a tool in tool table
- if a geometry is painted inside the Geometry Editor then it will store the tool diameter used for this painting. Only one tool cn be stored (the last one) so if multiple paintings are done with different tools in the same geometry it will store only the last used tool.
- if multiple geometries have different tool diameters associated (contain a paint geometry) they aren't allowed to be joined and a message is displayed letting the user know

2.12.2018

- started to work on a geometry Tool Table
- renamed FlatCAMShell as ToolShell and moved it (and termwidget) to flatcamTools folder
- cleaned up the ToolShell by removing the termwidget separate file and added those classes to ToolShell
- added autocomplete for TCL Shell - the autocomplete key is 'TAB'
- covered some possible exceptions in rotate/skew/mirror functions
- Geometry Tool table: add/delete tools = DONE
- Geometry Tool table: add multiple tools with same diameter = DONE

1.12.2018

- fixed Gerber parser so now the Gerber regions that have D02 operation code just before the end of the region will be processed correctly. Autotrax Dex Gerbers are now loaded
- fixed an issue with temporary geo storage "geo" being referenced before assignment
- moved all FlatCAM Tools into a single directory

30.11.2018

- remade the CutOut Tool. I've put together the former Freeform Cutout tool and the Cutout Object fount in Gerber Object GUI and left only a link in the Gerber Object GUI. This tidy the GUI a bit.
- created a Paint Tool and replaced the Paint Area section in Geometry Object GUI with a link to this tool.
- fixed bug in former Paint Area and in the new Paint Tool that made the paint method not to be saved in App preferences
- solved a bug in Gerber parser: in case that an operation code D? was encountered alone it was not remembered - fixed
- fixed bug related to the newly entered toolchange feature for Geometry: it was trying to evaluate toolchange_z as a comma separated value like for toolchange x,y
- fixed bug in scaling units in CNC Job which made the unit change between INCH and MM not possible if a CNC Job was present in the project objects

29.11.2018

- added checks for using a Z Cut with positive value. The Z Cut parameter has to be negative so if the app will detect a positive value it will automatically convert it to negative
- started to implement rest-machining for Non Copper clearing Tool - for now the results are not great
- added Toolchange X,Y position parameters and modified the default and manual_toolchange preprocessor file to use them
For now they are used only for Excellon objects who do have toolchange events
- added Toolchange event selection for Geometry objects; for now it is as before, single tool on each file
- remade the GUI for objects and in Preferences to have uniformity
- fixed bug: after editing a newly created excellon/geometry object the object UI used to not keep the original settings
- fixed some bugs in Tool Add feature of the new Non Copper Clear Tool
- added some messages in the Non Copper Clear Tool
- added parameters for coordinates no of decimals and for feedrate no of decimals used in the resulting GCODE. They are in EDIT -> Preferences -> CNC Job Options
- modified the preprocessors to use the "decimals" parameters

28.11.2018

- added different methods of copper clearing (standard, seed, line_based) and "connect", "contour" options found in Paint function
- remake of the non-copper clearing tool as a separate tool
- modified the "About" menu entry to mention the main contributors to FlatCAM 3000 
- modified Marlin preprocessor according to modifications made by @redbull0174 user from FlatCAM.org forum
- modified Move Tool so it will detect if there is no object to move and issue a message

27.11.2018

- fixed bug in isolation with multiple passes
- cosmetic changes in Buffer and Paint tool from Geometry Editor
- changed the way selection box is working in Geometry Editor; now cumulative selection is done with modifier key (SHIFT or CONTROL) - before it was done by default
- changed the default value for CNCJob tooldia to 1mm

25.11.2018

- each Tool change the name of the Tools tab to it's name
- all open objects are no longer autoselected upon creation. Only on new Geometry/Excellon object creation it will be autoselected

24.11.2018

- restored the selection method in Geometry Editor to the original one found in FlatCAM 8.5
- minor changes in Clear Copper function
- minor changes in some preprocessors
- change Join Geometry menu entry to Join Geo/Gerber
- added menu entry for Toggle Axis in Menu -> View
- added menu entry for Toggle Workspace in Menu -> View
- added Bounding box area to the Properties (when metric units, in cm2)
- non-copper clearing function optimization
- fixed Z_toolchange value in the GCODE header

21.11.2018

- not very precise jump to location function
- added shortcut key for jump to coordinates (J) and for Tool Transform (T)
- some work in shortcut key

19.11.2018

- fixed issue with nested comment in preprocessors
- fixed issue in Paint All; reverted changes

18.11.2018

- renamed FlatCAM 2018 to FlatCAM 3000
- added new entries in the Help menu; one will show shortcut list and the other will start a YouTube webpage with a playlist where I will publish future training videos for this version of FlatCAM
- if a Gerber region has issues the file will be loaded bypassing the error but there will be a TCL message letting the user know that there are parser errors. 

17.11.2018

- added Excellon parser support for units defined outside header


12.11.2018

- fixed bug in Paint Single Polygon
- added spindle speed in laser preprocessor
- added Z start move parameter. It controls the height at which the tool travel on the fist move in the job. Leave it blank if you don't need it.

9.11.2018

- fixed a reported bug generated by a typo for feedrate_z object in camlib.py. Because of that, the project could not be saved.
- fixed a G01 usage (should be G1) in Marlin preprocessor.
- changed the position of the Tool Dia entry in the Object UI and in MainGUI
- fixed issues in the installer

30.10.2018

- fixed a bug in Freeform Cutout Tool - it was missing a change in the name of an object

29.10.2018

- added Excellon export menu entry and functionality that can export in fixed format 2:4 LZ INCH (format that Altium can load and it is a more generic format).
It will be usefull for those who need FlatCAM to only convert the Excellon to a more useful format and visualize Gerbers.
The other Excellon Export menu entry is exporting in units either Metric or INCH depending on the current units in FlatCAM, but it will always use the decimal format which may not be loaded in all cases.
- disabled the Selected Tab while in Geometry Editor; the user is not supposed to have access to those functions while in Geometry Editor
- added an menu entry in Menu -> File -> Recent Files named Clear Recent files which does exactly that
- fixed issue: when a New Project is created but there is a Geometry still in Geometry Editor (or Excellon Editor) not saved, now that geometry is deleted
- fixed problem when doing Clear Copper with Cut over 1st point option active. When the shape is not closed then it may cut over copper features. Originally the feature was meant to be used only with isolation geometry which is closed. Fixed

28.10.2018

- fixed Excellon Editor shortcut messages; also fixed differences in messages between usage by shortcuts and usage by menu toolbar actions
- fixed Excellon Editor bug: it was triggering exceptions when the user selected a tool in tooltable and then tried to add a drill (or array) by clicking on canvas
Clicking on canvas by default clear all the used tools, therefore the action could not be done. Fixed.
- fixed bug Excellon Editor: when all the drills from a tool are resized, after resize they can't be selected.
- Excellon Editor: added ability to delete multiple tools at once by doing multiple selection on the tooltable
- Excellon Editor: if there are no more drills to a tool after doing drills resize then delete that tool from the tooltable
- Excellon Editor: always select the last tool added to the tooltable
- Excellon Editor: added a small canvas context menu for Excellon Editor

27.10.2018

- added a Paint tool toolbar icon and added shortcut key 'I' for Paint Tool
- fixed unreliable multiple selection in Geometry Editor; some clicks were not registered
- added utility geometry for Add Drill Array in Excellon Editor
- fixed bug Excellon Editor: drills in drill array start now from the array start point (x, y); previously array start point was used only for calculating the radius
- fixed bug Excellon Editor: Measurement Tool was not acting correctly in Exc Editor regarding connect/disconnect of events
- in Excellon Editor every time a tool is clicked (except Select which is the default) the focus will return to Selected tab
- added protection in Excellon Editor: if there is no tool/drill selected no operation over drills can be performed and a status bar message will be displayed
- Excellon Editor: added relevant messages for all actions
- fixed bug Excellon Editor: multiple selection with key modifier pressed (CTRL/SHIFT) either by simple click or through selection box is now working
- fixed dwell parameter for Excellon in Preferences to be default Off

26.10.2018

- when objects are disabled they can't be selected
- added Feedrate_z (Plunge) parameter for Geometry Object
- fixed bug in units convert for Geometry Tab; added some missing parameters to the conversion list
- fixed bug in isolation Geometry when the isolated Gerber was a single Polygon
- updated the Paint function in Geometry Editor

25.10.2018

- added a verification on project saving to make sure that the project was saved successfully. If not, a message will be displayed in the status bar saying so.

20.10.2018

- fixed the SVG import as Gerber. But unfortunately, when there is a ground pour in a imported PCB SVG, the ground pour will be isolated inside
instead to be isolated outside like every other feature. That's no way around this. The end result will be thinner features
for the ground pour and if one is relying on those thin connections as GND links then it will not work as intended ,they may be broken.
Of course one can edit the isolation geometry and delete the isolation for the ground pour.
- delete selection shapes on double clicking on object as we may not want to have selection shape while Selected tab is active

19.10.2018

- solved some value update bugs in plugin_table in Excellon Editor when editing tools followed by deleting another tool,
and then re-adding the just-deleted tool.
- added support for chaining blocks in DXF Import
- fixed the DXF arc import
- added support for a type of Gerber files generated by OrCAD where the Number format is combined with G74 on the same line
- in Geometry Editor added the possibility for buffer to use different kinds of corners
- added protection against loading an GCODE file as Excellon through drag & drop on canvas or file open dialog
- added shortcut key 'B' for buffer operation inside Geometry Editor
- added shell message in case the Font used in Text Tool in Geometry editor is not supported. Only Regular, Bold, Italic adn BoldItalic are supported as of yet.
- added shortcut key 'T' for Text Tool inside Geometry Editor
- added possibility for Drag & Drop on FlatCAM GUI with multiple files at once 

18.10.2018

- fixed DXF arc import in case of extrusion enabled
- added on Geo Editor Toolbar the button for Buffer Geometry; added the possibility to create exterior and interior buffer
- fixed a numpy import error

17.10.2018

- added Spline support and Ellipse (chord) support in DXF Import: chord might have issues
(borrowed from the work of Vasilis Vlachoudis, https://github.com/vlachoudis/bCNC)
- added Block support in DXF Import - no support yet for chained blocks (INSERT in block)
- support for repasted block insertions

16.10.2018

- added persistent toolbar view: the enabled toolbars will be active at the next app startup while those that are not enabled will not be
enabled at the next app startup. To enable/disable toolbars right click on the toolbar.

15.10.2018

- DXF Export works now also for Exteriors only and Interiors only geometry generated from Gerber Object
- when a Geometry is edited, now the interiors and exterior of a Polygon that is part of the Geometry can be selected individually. In practice, if
doing full isolation geometry, now both external and internal trace can be selected individually.

13.10.2018

- solved issue in CNC Code Editor: it appended text to the previous one even if the CNC Code Editor was closed
- added .GBD Gerber extension to the lists
- added support for closed polylines/lwpolylines in Import DXF; now PCB patterns found in PDF format can be imported in INKSCAPE
and saved as DXF. FlatCAM can import DXF as Gerber and the user now can do isolation on it.

12.10.2018

- added zoom in, zoom out and zoom fit buttons on the View toolbar
- fixed bug that on Double Sided Tool when a Excellon Alignment is created does not reset the list of Alignment drills
- added a message warning the user to add Point coordinates in case the reference used in Double Sided Tool is Point
- added new feature: DXF Export for Geometry

10.10.2018

- fixed a small bug in Setup Recent Files
- small fix in Freeform Cutout Tool regarding objects populating the combo boxes
- Excellon object name will reflect the number of edits performed on it

9.10.2018

- In Geometry Editor, now Path and Polygon draw mode can be finished not only with shortcut key Enter but also with right click on canvas
- fixes regarding of circle linear approximation - final touch
- fix for interference between Geo Editor and Excellon Editor
- fixed Cut action in Geometry Editor so it can now be done multiple times on the target geometry without need for saving in between.
- initial work on DXF import; made the GUI interface and functional structure
- added import functions for DXF import
- finished DXF Import (no blocks support, no SPLINE support for now)

8.10.2018

- completed toggle canvas selection when there is only one object under click position for the case when clicking the object is done
while other object is already selected.
- added static utility geometry just upon activating an Editor function
- changed the way the canvas is showed on FlatCAM startup

7.10.2018

- solved mouse click not setting relative measurement origin to zero
- solved bug that always added one drill when copying a selection of drills in the EXCELLON EDITOR
- solved bug that the number of copied drills in Excellon Editor was not updated in the tool table
- work in the Excellon Editor: found useful to change the diameter of one tool to another already in the list;
could help for all those tools that are a fraction difference that comes from imperial to mm (or reverse) conversion,
to reduce the tool changes - Done
- in Excellon Editor, always auto-select the last tool added
- in Excellon Editor fixed shortcuts for drill add and drill_array add: they were reversed. Now key 'A' is for array add
and key 'D' is for drill add
- solved a small bug in Excellon export: even when there were no slots in the file, it always added the tools list that
acted as unnecessary toolchanges
- after Move action, all objects are deselected


6.10.2018

- Added basic support for SVG text in SVG import. Will not work if some letters in a word have different style (italic bold or both)
- added toggle selection to the canvas selection if there is only one object under the click position
- added support for "repeat" command in Excellon file
- added support for Allegro Gerber and Excellon files
- Python 3.7 is used again; solved bug where the activity icon was not playing when FlatCAM active

5.10.2018

- fixed undesired setting focus to Project Tab when doing the SHIFT + LMB combo (to capture the click coordinates)

4.10.2018

- Excellon Editor: finished Add Drill Array - Linear type action
- Excellon Editor: finished Add Drill Array - Circular type action
- detected bug in shortcuts: Fixed
- Excellon Editor: added constrain for adding circular array, if the number of drills multiplied by angle is more than 360
the app will return with an message
- solved sorting bug in the Excellon Editor tool table
- solved bug in Menu -> Edit -> Sort Origin ; the selection box was not updated after offset
- added Excellon Export in Menu -> File -> Export -> Export Excellon
- added support to save the slots in the Excellon file in case there were some in the original file
- fixed Double Sided Tool for the case of using the box as mirroring reference.

2.10.2018

- made slots persistent after edit
- bug detected: in Excellon Editor if new tool added diameter is bigger than 10 it mess things up: SOLVED
- Excellon Editor: finished Drill Resize action
- after an object is deleted from the Project list, if the current tab in notebook is not Project,
always focus in the Project Tab (deletion can be done by shortcut key also)
- changed the initial view to include the possible enabled workspace guides

1.10.2018

- added GUI for Excellon Editor in the Tool Tab
- Excellon Editor: created and populated the tool list
- Excellon Editor: added possibility to add new tools in the list
- Excellon Editor: added possibility to delete a tool (and the drills that it contain) by selecting a row in the tool table and 
clicking the Delete Tool button
- Excellon Editor: added possibility to change the tool diameter in the tool list for existing tool diameters.
- Excellon Editor: when selecting a drill, it will highlight the tool in the Tool table
- Excellon Editor: optimized single click selection
- Excellon Editor: added selection for all drills with same diameter upon tool selection in tool table; fix in tool_edit
- Excellon Editor: added constrain to selection by single click, it will select if within a certain area around the drill
- Excellon Editor: finished Add Drill action
- Excellon Editor: finished Move Drill action
- Excellon Editor: finished Copy Drill action

- fixed issue: when an object is selected before entering the Editor mode, now the selecting shape is deleted before entry 
in the Editor (be it Geometry or Excellon).
- fixed a few glitches regarding the units change
- when an object is deselected on the Plot Area, the notebook will switch to Project Tab
- changed the selection behavior for the dragging rectangle selection box in Editor (Geometry, Excellon): by dragging a
selection box and selecting is cumulative: it just adds. To remove from selection press key Ctrl (or Shift depending of 
the setting in the Preferences) and drag the rectangle across the objects you want to deselect.

29.09.2018

- optimized the combobox item population in Panelization Tool and in Film Tool
- FlatCAM now remember the last path for saving files not only for opening
- small fix in GUI
- work on Excellon Editor. Excellon editor working functions are: loading an Excellon object into Editor, 
saving an Excellon object from editor to FlatCAM, selecting drills by left click, selection of drills by dragging rectangle, deletion of drills.
- fixed Excellon merge
- added more Gcode details (depthperpass parameter in Gcode header) in preprocessors
- deleted the Tool informations from header in preprocessors due to Mach3 not liking the lot of square brackets
- more corrections in preprocessors


28.09.2018

- added a save_defaults() call on App exit from action on Menu -> File -> Exit
- solved a small bug in Measurement Tool
- disabled right mouse click functions when Measurement Tools is active so the user can do panning and find the destination point easily
- added a new button named "Measure" in Measurement Tool that allow easy access to Measurement Tool from within the tool
- fixed a bug in Gerber parser that when there was a rectangular aperture used within a region, some artifacts were generated.
- some more work on Excellon Editor

27.09.2018

- fixed bug when creating a new project, if a previous object was selected on screen, the selection shape survived the creation of a new project
- added compatibility with old type of FlatCAM projects
- reverted modifications to the way that Excellon geometry was stored to the old way.
- added exceptions for Paint functions so the user can know if something failed.
- modified confirmation messages to use the color coded messages (error = red, success = green, warning = yellow)
- restored activity icon

26.09.2018

- disabled selection of objects in Project Tab when in Editor
- the Editor Toolbar is hidden in normal mode and it is showed when Editor is activated. I may change this behaviour back.
- changed names in classes, functions to prepare for the Excellon editor

- fixed bugs in Paint All function
- fixed a bug in ParseSVG module in parse_svg_transform(), related to 'scale'

- moved all the Editor menu/toolbar creation to FlatCAMUI where they belong
- fixed a Gerber parse number issue when Gerber zeros are TZ (keep trailing zeros)

- changed the way of how the solid_geometry for Excellon files is stored and plotted. Before everything was put in the same "container". Now, the geometries of drills and slots are organized into dictionaries having as keys the tool diameters and as values list of Shapely objects (polygons)
- fix for Excellon plotting for newly created empty Excellon Object
- fixed geometry.bounds() in camlib to work with the new format of the Excellon geometry (list of dicts)

24.09.2018

- added packages in the Requirements and setup_ubuntu.sh. Tested in Ubuntu and it's OK
- added Replace (All) feature in the CNC Code Editor
- made CNC Code generation for Excellon to show progress
- added information about transforms in the object properties (like skew and how much, if it was mirrored and so on)
- made all the transforms threaded and make them show progress in the progress bar
- made FlatCAM project saving, threaded.
 
23.09.2018

- added support for "header-less" Excellon files. It seems that Mentor PADS does generate such non-standard Excellon files. The user will have to guess: units (IN/MM), type of zero suppression LZ/TZ  (leading zeros or trailing zeros are kept) and Excellon number format(digits and decimals).  All of those can be adjusted in Menu -> Edit -> Preferences -> Excellon Object -> Excellon format
- fixed svgparse for Path. Now PCB rasted images can traced in Inkscape or PDF's can be converted and then saved as SVG files which can be imported into FlatCAM. This is a convolute way to convert a PDF to Gerber file.

22.09.2018

- added Drag & Drop capability. Now the user can drag and drop to FlatCAM GUI interface a file (with the right extension) that can be a FlatCAM project file (.FlatPrj) a Gerber file, an Excellon file, a G-Code file or a SVG file.
- made the Move Tool command threaded
- added Image import into FlatCAM

21.09.2018

- added new information's in the object properties: all used Tool-Table items are included in a new entry in self.options dictionary
- modified the preprocessor files so they now include information's about how many drills (or slots) are for each tool. The Gcode will have this information displayed on the message from ToolChange.
- removed some log.debug and add new log.debug especially for moments when some process is finished
- fixed the utility geometry for Font geometry in Geometry Editor
- work on selection in Geometry Editor
- added multiple selection key as a Preference in Menu -> Edit -> Preferences It can be either Shift or Ctrl.
- fixed bug in Gerber Object -> Copper Clearing.
- added more comprehensive tooltips in Non-copper Clearing as advice on how to proceed.
- adjusted make_win32.py file so it will work with Python 3.7 (cx_freeze can't copy OpenGL files, so it has to be done manually)

19.09.2018

- optimized loading FlatCAM project by double clicking on project file; there is no need to clean up everything by using the function not Thread Safe: on_file_new_project() because there is nothing to clean since FlatCAM just started.

- added a workspace delimitation with sizes A3, A4 and landscape or portrait format
- The Workspace checkbox in Preferences GUI is doing toggle on the workspace
- made the workspace app default state = False
- made the workspace to resize when units are changed
- disabled automatic defaults save (might create SSD wear)
- added an automatic defaults save on FlatCAM application close
- made the draw method for the Workspace lines 'agg' so the quality of the FC objects will not be affected

- added Area constrain to the Panelization Tool: if the resulting area is too big to fit within constrains, the number of columns and/or rows will be reduced to the maximum that still fits is.
- removed the Flip command from Panelization Tools because Flipping (Mirroring) should be done properly with the Transform Tool or using the provided shortcut keys.

- made Font parsing threaded so the application will not wait for the font parsing to complete therefore the app start is faster


17.09.2018

- fixed Measuring Tool not working when grid is turned OFF
- fixed Roland MDX20 preprocessor
- added a .GBR extension in the open_gerber filter
- added ability to Scale and Offset (for all types of objects) to just press Enter after entering a value in the Entry just like in Tool Transform
- added capability in Tool Transform to mirror(flip) around a certain Point. The point coordinates can either be entered by hand or they can be captured by left clicking while pressing key "SHIFT" and then clicking the Add button
- added the .ROL extension when saving Machine Code
- replaced strings that reference to G-Code from G-Code to CNC Code
- added capability to open a project by serving the path/project_name.FlatPrj as a parameter to FlatCAM.py

15.09.2018

- removed dwell line generator and included dwell generation in the preprocessor files
- added a proposed RML1 Roland_MDX20 preprocessor file.
- added a limit of 15mm/sec (900mm/min) to the feedrate and to the feedrate_rapid. Anything faster than this will be capped to 900mm/min regardless what is entered in the program GUI. This is because Roland MDX-20 has a mechanical limit of the speed to 15mm/sec (900mm/min in GUI)

14.09.2018
- remade the Double Sided Tool so it now include mirroring of Excellon and Geometry Objects along Gerber. Made adding points easier by adding buttons to GUI that allow adding the coordinates captured by left mouse click + SHIFT key
- added a few fixes in code to the other FlatCAM tools regarding reset_fields() function. The issue was present when clicking New Project entry in Menu -> File.
- FIXED: fix adding/updating bounding box coords for the mirrored objects in Double side Tool.
- FIXED: fix the bounding box values from within FlatCAM objects, upon units change.
- fixed issue with running again the constructor of the drawing tools after the tool action was complete, in Geometry Editor
- fixed issue with Tool tab not closed after Text Input tool is finished.
- fixed issue with TEXT to GEOMETRY tool, the resulting geometry was not scaled depending of current units
- fixed case when user is clicking on the canvas to place a Font Geometry without clicking apply button first or the Font Geometry is empty, in Geometry Editor - > Text Input tool
- reworked Measuring Tool by adding more information's (START, STOP point coordinates) and remade the strings
- added to Double Sided Tool the ability to use as reference box Excellon and Geometry Objects

12.09.2018

- fixed Excellon Object class such that Excellon files that have both drills and slots are supported
- remade the GUI interface for the Excellon Object in a more compact way; added a column with slots numbers (if any) along the drills numbers so now there is only one tool table for drills and slots.
- remade the GUI in Preferences and removed unwanted stretch that was broken the layout.
- if for a certain tool, the slots number is zero it will not be displayed
- reworked Text to Geometry feature to work in Linux and MacOS
- remade the Text to Geometry so font collection process is done once at app start-up improving the performance


09.09.2018

- added TEXT ENTRY SUPPORT in Geometry Editor. It will convert strings of True Type Fonts to geometry. The actual dimensions are approximations because font size is in points and not in metric or inch units. For now full support is limited to Windows. In Linux/MacOS only the fonts for which the font name is the same as the font filename are supported. Italic and Bold functions may not work in Linux/MacOS.
- solved bug: some Drawing menu entries not having connected functions

28.08.2018

- fixed Gerber parser so now G01 "moving" rectangular aperture is supported.
- fixed import_svg function; it can import SVG as geometry (solved bug)
- fixed import_svg function; it can import SVG as Gerber (it did not work previously)
- added menu entry's for SVG import as Gerber and separated import as Geometry

27.08.2018

- fixed Gerber parser so now FlatCAM can load Gerber files generated by Mentor Graphics EDA programs.

26.08.2018

- added awareness for missing coordinates in Gerber parsing. It will try to use the previous coordinates but if there are not any those lines will be ignored and an Warning will be printed in Tcl Shell.
- fixed TCL commands AlignDrillGrid and DrilCncJob
- added TCL script file load_and_run support in GUI
- made the plugin_table in Excellon to automatically adjust the table height depending on the number of rows such that all the rows will be displayed.
- structural changes in the Excellon build_ui()
- icon changes and menu compress

23.08.2018

- added Excellon routing support
- solved a small bug that crippled Excellon slot G85 support when the coordinates are with period.
- changed the way selection is done in Geometry Editor; now it should work in all cases (although the method used may be computationally intensive, because sometimes you have to click twice to make selection if you do it too fast)

21.08.2018

- added Excellon slots support when using G85 command for generation of the slots file. Inspired from the work of @mgix. Thanks. Routing format support for slots will follow. 
- minor bug solved: option "Cut over 1st pt" now has same name both in Preferences -> Geometry Options and in Selected tab -> Geomety Object. Solves #3
- added option to select Climb or Conventional Milling in Gerber Object options Solves #4
- made "Combine passes" option to be saved as an app preference
- added Generate Exteriors Geo and Generate Interiors Geo buttons in the Gerber Object properties
- added configuration for the number of steps used for Gerber circular aperture linear approximation. The option is in Preferences -> Gerber Options
- added configuration for the number of steps used for Gcode circular aperture linear approximation. The option is in Preferences -> CNCjob Options
- added configuration for the number of steps used for Geometry circular aperture linear approximation. The option is in Preferences -> Geometry Options. It is used on circles/arcs made in Geometry Editor and for other types of geometries generated in the app.

17.07.2018

- added the required packages in Requirements.txt file
- added required packages in setup_ubuntu.sh file
- added color control over almost all the colors in the application; those settings are in Menu -> Edit -> Preferences -> General Tab
- added configuration of which mouse button to be used when panning (MMB or RMB)
- fixed bug with missing 'drillz' parameter in function generate_from_excellon_by_tool() (credits for finding it goes to Stefan Smith https://bitbucket.org/stefan064/)
- load Factory defaults in Preferences will load the defaults that are used just after first install. Load Defaults option in Preferences will load the User saved Defaults.

03.07.2018

- fixed bug in rotate function that didn't update the bounding box of the modified object (rotated) due of not emitting the right signal parameter.
- removed the Options tab from the Notebook (the left area where is located also the Project tab). Replaced it with the Preferences Tab launched with Menu -> Edit -> Preferences
- when FlatCAM is used under MacOS, multiple selection of shapes in Editor mode is done using SHIFT key instead of CTRL key due of MacOS interpreting Ctrl+LMB_click as a RMB click
- when in Editor, clicking not on a shape, reset the index of selected shapes to zero
- added a new Tab in the Plot Area named Gcode Editor. It allow the user to edit the Gcode and then Save it or Print it.
- added a fix so the 'preamble' Gcode is correctly inserted between the comments header and the actual GCODE
- added Find function in G-Code Editor

27.06.2018

- the Plot Area tab is changing name to "Editor Area" when the Editor is activated and returns to the "Plot Area" name upon exiting the Editor
- made the labels shorter in Transform Tool in anticipation of Options Tab removal from Notebook and replacing it with Preferences
- the Excellon Editor is not finished (not even started yet) so the Plot Area title should stay "Plot Area" not change to "Editor Area" when attempting to edit an Excellon file. Solved.
- added a header comment block in the generated Gcode with useful information's
- fixed issue that did not allow the Nightly's to be run in Windows 7 x64. The reason was an outdated DLL file (freetype.dll) used by Vispy python module.

25.06.2018

- "New" menu entry in Menu -> File is renamed to "New Project"
- on "New Project" action, all the Tools are reinitialized so the Tools tab will work as expected
- fixed issue in Film Tool when generating black film
- fixed Measurement Tool acquiring and releasing the mouse/key events
- fixed cursor shape is updated on grid_toggle
- added some infobar messages to show the user when the Editor was activated and when it was closed (control returned to App).
- added thread usage for Film tool; now the App is no longer blocked on film generation and there is a visual clue that the App is working

22.06.2018

- added export PNG image functionality and menu entry in Menu -> File -> Export PNG ...
- added a command to set focus on canvas inside the mouve move event handler; once the mouse is moved the focus is moved to canvas so the shortcuts work immediatly.
- solved a small bug when using the 'C' key to copy name of the selected object to clipboard
- fixed millholes() function and isolate() so now it works even when the tool diameter is the same as the hole diameter.

Actually if the passed value to  the buffer() function is zero, I
artificially add a value of 0.0000001 (FlatCAM has a precision of
6 decimals so I use a tenth of that value as a pseudo "zero")
because the value has to be positive. This may have solved for some use
cases the user complaints that on clearing the areas of copper there is
still copper leftovers.

- added shortcut "Shift+G" to toggle the axis presence. Useful when one wants to save a PNG file.
- changed color of the grid from 'gray' to 'dimgray'
- the selection shape is deleted when the object is deleted
- the plot area is now in a TAB.
- solved bug that allowed middle button click to create selection
- fixed issue with main window geometry restore (hopefully).
- made view toolbar to be hidden by default as it is not really needed (we have the functions in menu, zoom is done with mouse wheel, and there is also the canvas context menu that holds the functionality)
- remade the GUIElements.FCInput() and made a GUIElements.FCTab()
- on visibility plot toogle the selection shape is deleted
- made sure that on panning in Geometry editor, the context menu is not displayed
- disabled App shortcut keys on entry in Geometry Editor so only the local shortcut keys are working
- deleted metric units in canvas context menu
- added protection so object deletion can't be done until Geometry Editor session is finished. Solved bug when the shapes on Geometry Editor were not transferred to the New_geometry object yet and the New_Geometry object is deleted. In this case the drawn shapes are left in a intermediary state on canvas.
- added selection shape drawing in Geometry Editor preserving the current behavior: click to select, click on canvas clear selection, Ctrl+click add to selection new shape but remove from selection if already selected. Drag LMB from left to right select enclosed shapes, drag LMB from right to left select touching shapes. Now the selection is made based on
- added info message to be displayed in infobar, when a object is renamed

20.06.2018

- there are two types of mouse drag selection (rectangle selection). If there is a rectangle selection from left to right, the color of the selection rectangle is blue and the selection is "enclosing" - this means that the object to be selected has to be enclosed by the selecting blue rectangle shape. If there is a rectangle selection fro right to left, the color of the selection rectangle is green and the selection is "touching" - this means that it's enough to touch with the selecting green rectangle the object(s) to be selected so they become selected
- changed the modifier key required to be pressed when LMB is ckicked over canvas in order to copy to clipboard the coordinates of the click, from CTRL to SHIFT. CTRL will be used for multiple selection.
- change the entry names in the canvas context menu
- disconnected the app mouse event functions while in geometry editor since the geometry editor has it's own mouse event functions and there was interference between object and geometry items. Exception for the mouse release event so the canvas context menu still work.
- solved a bug that did not update the obj.options after a geometry object was edited in geometry editor
- solved a bug in the signal that saved the position and dimensions of the application window.
- solved a bug in app.on_preferences() that created an error when run in Linux

18.06.2018 Update 1

- reverted the 'units' parameter change to 'global_units' due of a bug that did not allow saving of the project
- modified the camlib transform (rotate, mirror, scale etc) functions so now they work with Gerber file loaded with 'follow' parameter

18.06.2018

- reworked the Properties context menu option to a Tool that displays more informations on the selected object(s)
- remade the FlatCAM project extension as .FlatPrj
- rearranged the toolbar menu entries to a more properly order
- objects can now be selected on canvas, a blue polygon is drawn around when selected
- reworked the Tool Move so it will work with the new canvas selection
- reworked the Measurement Tool so it will work with the new canvas selection
- canvas selection can now be done by dragging left mouse boutton and creating a selection box over the objects
- when the objects are overlapped on canvas, the mouse click selection works in a circular way, selecting the first, then the second, then ..., then the last and then again the first and so on.
- double click on a object on canvas will open the Selected Tab
- each object store the bounding box coordinates in the options dict
- the bbox coordinates are updated on the obj options when the object is modified by a transform function (rotate, scale etc)


15.06.2018

- the selection marker when moving is now a semitransparent Polygon with a blue border
- rectified a small typo in the ToolTip for Excellon Format for Diptrace excellon format; from 4:2 to 5:2
- corrected an error that cause no Gcode could be saved

14.06.2018

- more work on the contextual menu
- added Draw context menu
- added a new tool that bring together all the transformations, named Transformation Tool (Rotate, Skew, Scale, Offset, Flip)
- added shorcut key 'Q' which toggle the units between IN and MM
- remade the Move tool, there is now a selection box to show where the move is done
- remade the Measurement tool, there is now a line between the start point of measurement and the end point of the measurement.
- renamed most of the system variables that have a global app effect to global_name where name is the parameter (variable)

9.06.2018

- reverted to PyQt4. PyQt5 require too much software rewrite
- added calculators: units_calculator and V-shape Tool calculator
- solved bug in Join Excellon
- added right click menu over canvas

6.06.2018 Update

- fixed bug: G-Code could not be saved
- fixed bug: double clicking a category in Project Tab made the app to crash
- remade the bounds() function to work with nested lists of objects as per advice from JP which made the operation less performance taxing.
- added shortcut Shift+R that is complement to 'R'
- shorcuts 'R' and 'Shift+R' are working now in steps of 90 degrees instead of previous 45 degrees.
- added filters in the open ... FlatCAM projects are saved automatically as *.flat, the Gerber files have few categories. So the Excellons and G-Code and SVG.

6.06.2018

- remade the transform functions (rotate, flip, skew) so they are now working for joined objects, too
- modified the Skew and Rotate comamands: if they are applied over a selection of objects than the origin point will be the center of the biggest bounding box. That allow for perfect sync between the selected objects
- started to modify the program so the exceptions are handled correctly
- solved bug where a crash occur when ObjCollection.setData didn't return a bool value
- work in progress for handling situations when a different file is loaded as another (like loading a Gerber file using Open Excellon commands.
- added filters on open_gerber and open_excellon Dialogs. There is still the ability to select All Files but this should reduce the cases when the user is trying to oprn a file from a wrong place.

4.06.2018

- finished PyQt4 to PyQt4 port on the Vispy variant (there were some changes compared with the Matplotlib version for which the port was finished some time ago)
- added Ctrl+S shortcut for the Geometry Editor. When is activated it will save de geometry ("update") and return to the main App.
- modified the Mirror command for the case when multiple objects are selected and we want to mirror all together. In this case they should mirror around a bounding box to fill all.

3.06.2018

- removed the current drill path optimizations as they are inefficient
- implemented Google OR-tools drill path optimization in 2 flavors; Basic OR-tools TSP algorithm and OR-Tools Metaheuristics Guided Local Path
- Move tool is moved to Menu -> Edit under the name Move Object

- solved some internal bugs (info command was creating an non-fatal error in PyQt, regarding using QPixMaps outside GUI thread
- reworked camlib number parsing (still had some bugs)
- working in porting the application from usage of PyQt4 to PyQt4
- added TclCommands save_sys and list_sys. save_sys is saving all the system default parameters and list_sys is listing them by the first letters. listsys with no arguments will list all the system parameters.

29.05.2018

- modified the labels for the X,Y and Dx,Dy coordinates
- modified the menu entries, added more icons
- added initial work on a Excellon Editor
- modified the behavior of when clicking on canvas the coordinates were copied to cliboard: now it is required to press CTRL key for this to happen, and it will only happen just for left mouse button click
- removed the autocopy of the object name on new object creation
- remade the Tcl commands drillcncjob and cncjob
- added fix so the canvas is focused on the start of the program, therefore the shortcuts work without the need for doing first a click on canvas.

28.05.2018

- added total drill count column in Excellon Tool Table which displays the total number of drills
- added aliases in panelize Tool (pan and panel should work)
- modified generate_milling method which had issues from the Python3 port (it could not sort the tools due of dict to dict comparison no longer possible).
- modified the 'default' preprocessor in order to include a space between the value of Xcoord and the following Y
- made optional the using of threads for the milling command; by default it is OFF (False) because in the current configuration it creates issues when it is using threads
- modified the Panelize function and Tcl command Panelize. It was having issues due to multithreading (kept trying to modify a dictionary in redraw() method)and automatically selecting the last created object (feature introduced by me). I've added a parameter to the app_obj.new_object method, named autoselected (by default it is True) and in the panelize method I initialized it with False.
By initializing the plot parameter with False for the temporary objects, I have increased dramatically the  generation speed of the panel because now the temporary object are no longer ploted which consumed time.
- replaced log.warn() with log.warning() in camlib.py. Reason: deprecated
- fixed the issue that the "Defaults" button was having no effect when clicked and Options Combo was in Project Options
- fixed issue with Tcl Shell loosing focus after each command, therefore needing to click in the edit line before we type a new command (borrowed from @brainstorm
- added a header in the preprocessor files mentioning that the GCODE files were generated by FlatCAM.
- modified the number of decimals in some of the line entries to 4.
- added an alias for the millholes Tcl Command: 'mill'

27.04.2018

- modified the Gerber.scale() function from camlib.py in order to allow loading Gerber files with 'follow' parameter in other units than the current ones
- snap_max_entry is disabled when the DRAW toolbar is disabled (previous fix didn't work)
- added drill count column in Excellon Tool Table which displays the total number of drills for each tool
- added a new menu entry in Menu -> EDIT named "Join Excellon". It will merge a selection of Excellon files into a new Excellon file
- added menu stubs for other Excellon based actions
- solved bug that was not possible to generate film from joined geometry
- improved toggle active/inactive of the object through SPACE key. Now the command works not only for one object but also for a selection

26.05.2018

- made conversion to Python3
- added Rtree Indexing drill path optimization
- added a checkbox in Options Tab -> App Defaults -> Excellon Group named Excellon Optim. Type from which it can be selected the default optimization type: TS stands for Travelling Salesman algorithm and Rtree stands for Rtree Indexing
- added a checkbox on the Grid Toolbar that when checked (default status is checked) whatever value entered in the GridX entry will be used instead of the now disabled GridY entry
- modified the default behavior on when a line_entry is clicked. Now, on each click on a line_entry, the content is automatically selected.
- snap_max_entry is disabled when the DRAW toolbar is disabled

24.05.2015

- in Geometry Editor added a initial form of Rotate Geometry command in toolbar
- changed the way the geometry is finished if it requires a key: before it was using key 'Space' now it uses 'Enter'
- added Shortcut for Rotate Geometry to key 'Space'
- after using a tool in Geometry Editor it automatically defaults to 'Select Tool'

23.05.2018

Added key shortcut's in FlatCAMApp and in Geometry Editor.

FlatCAMApp shortcut list:
1      Zoom Fit
2      Zoom Out
3      Zoom In
C      Copy Obj_Name
E      Edit Geometry (if selected)
G      Grid On/Off
M      Move Obj

N      New Geometry
R      Rotate
S      Shell Toggle
V      View Fit
X      Flip on X_axis
Y      Flip on Y_axis
~      Show Shortcut List

Space:   En(Dis)able Obj Plot
Ctrl+A   Select All
Ctrl+C   Copy Obj
Ctrl+E   Open Excellon File
Ctrl+G   Open Gerber File
Ctrl+M   Measurement Tool
Ctrl+O   Open Project
Ctrl+S   Save Project As
Delete   Delete Obj'''


Geometry Editor Key shortcut list:
A       Add an 'Arc'
C       Copy Geo Item
G       Grid Snap On/Off
K       Corner Snap On/Off
M       Move Geo Item

N       Add an 'Polygon'
O       Add a 'Circle'
P       Add a 'Path'
R       Add an 'Rectangle'
S       Select Tool Active


~        Show Shortcut List
Space:   Rotate Geometry
Enter:   Finish Current Action
Escape:  Abort Current Action
Delete:  Delete Obj

22.05.2018

- Added Marlin preprocessor
- Added a new entry into the Geometry and Excellon Object's UI: Feedrate rapid: the purpose is to set a feedrate for the G0 command that some firmwares like Marlin don't intepret as 'move with highest speed'
- FlatCAM was not making the conversion from one type of units to another for a lot of parameters. Corrected that.
- Modified the Marlin preprocessor so it will generate the required GCODE.

21.05.2018

- added new icons for menu entries
- added shortcuts that work on the Project tab but also over Plot. Shorcut list is accesed with shortcut key '~' sau '`'
- small GUI modification: on each "New File" command it will switch to the Project Tab regardless on which tab we were.
- removed the global shear entries and checkbox as they can be damaging and it will build effect upon effect, which is not good
- solved bug in that the Edit -> Shear on X (Y)axis could adjust only in integers. Now the angle can be adjusted in float with 3 decimals.
- changed the tile of QInputDialog to a more general one
- changed the "follow" Tcl command to the new format
- added a new entry in the Menu -> File, to open a Gerber with the follow parameter = True
- added a new checkbox in the Gerber Object Selection Tab that when checked it will create a "follow" geometry
- added a few lines in Mill Holes Tcl command to check if there are promises and raise an Tcl error if there are any.
- started to modify the Export_Svg Tcl command

20.05.2018

- changed the interpretation of the axis for the rotate and skew commands. Actually I reversed them to reflect reality.
- for the rotate command a positive angle now rotates CW. It was reversed.
- added shortcuts (for outside CANVAS; the CANVAS has it's own set of shortcuts) Ctrl+C will copy to clipboard the name of the selected object Ctrl+A will Select All objects
"X" key will flip the selected objects on X axis
"Y" key will flip the selected objects on Y axis
"R" key will rotate CW with a 45 degrees step

- changed the layout for the top of th Options page. Added a checkbox and entries for parameters for skew command. When the checkbox is checked it will save (and load at the next startup of the program) the option that at each CNCJob generation (be it from Excellon or Geometry) it will perform the Skew command with the parametrs set in the nearby field boxes (Skew X and Skey Y angles). It is useful in case the CNC router is not perfectly alligned between the X and Y axis
- added some protection in case the skew command receive a None parameter
- BUG solved: made an UGLY (really UGLY) HACK so now, when there is a panel geometry generated from GUI, the project WILL save. I had to create a copy of the generated panel geometry and delete the original panel geometry. This way there is no complain from JSON module about circular reference.
- removed the Save buttons previously added on each Group in Application Defaults. Replaced them with a single Save button that stays always on top of the Options TAB
- added settings for defaults for the Grid that are persistent
- changed the default view at FlatCAM startup: now the origin is in the center of the screen

19.05.2018

- last object that is opened (created) is always automatically selected and the name of the object is automatically copied to clipboard; useful when using the TCL command :)
- added new commands in MENU -> EDIT named: "Copy Object" and "Copy Obj as Geom". The first command will duplicate any object (Geometry, Gerber, Excellon). The second command will duplicate the object as a geometry. For example, holes in Excello now are just circles that can be "painted" if one wants it.
- added new Tool named ToolFreeformCutout. It does what it says, it will make a board cutout from a "any shape" Gerber or Geometry file
- solved bug in the TCL command "drillcncjob" that always used the endz parameter value as the toolchangez parameter value and for the endz value used a default value = 1
- added preprocessor name into the TCL command "drillcncjob" parameters
- when adding a new geometry the default name is now: "New_Geometry" instead of "New Geometry". TCL commands don't handle the spaces inside the name and require adding quotes.
- solved bug in "cncjob" TCL command in which it used multidepth parameter as always True regardless of the argument provided
- added a checkbox for Multidepth in the Options Tab -> Application Defaults

18.05.2018

- added an "Defaults" button in Excellon Defaults Group; it loads the following configuration (Excellon_format_in 2:4, Excellon_format_mm 3:3, Excellon_zeros LZ)
- added Save buttons for each Defaults Group; in the future more parameters will be propagated in the app, for now they are a few
- added functions for Skew on X axis and for Skew on Y menu stubs. Now, clicking on those Menu -> Options -> Transform Object menu entries will trigger those functions
- added a CheckBox button in the Options Tab -> Application Defaults that control the behaviour of the TCL shell: checking it will make the TCL shell window visible at each start-up, unchecking it the TCL shell window will be hidden until needed
- Depth/pass parameter from Geometry Object CNC Job is now in the defaults and it will keep it's value until changed in the Application Defaults.

17.05.2018

- added messages box for the Flip commands to show error in case there is no object selected when the command is executed
- added field entries in the Options TAB - > Application Defaults for the following newly introduced parameters: 
excellon_format_upper_in
excellon_format_lower_in
excellon_format_upper_mm
excellon_format_lower_mm

The ones with upper indicate how many digits are allocated for the units and the ones with lower indicate how many digits from coordinates are alocated for the decimals.

[  Eg: Excellon format 2:4 in INCH
   excellon_format_upper_in = 2
   excellon_format_lower_in = 4
where the first 2 digits are for units and the last 4 digits are
decimals so from a number like 235589 we will get a coordinate 23.5589
]

- added Radio button in the Options TAB - > Application Defaults for the Excellon_zeros parameter
After each change of those parameters the user will have to press "Save defaults" from File menu in order to propagate the new values, or wait for the autosave to kick in (each 20sec).
Those parameters can be set in the set_sys TCL command.

15.05.2018
- modified SetSys TCL command: now it can change units
- modified SetSys TCL command: now it can set new parameters: excellon_format_mm and excellon_format_in. the first one is when the excellon units are MM and the second is for when the excellon units are in INCH. Those parameters can be set with a number between 1 and 5 and it signify how many digits are before coma.
- added new GUI command in EDIT -> Select All. It will select all objects on the first mouse click and on the second will deselect all (toggle action)
- added new GUI commands in Options -> Transform object. Added Rotate selection, Flip on X axis of the selection and Flip on Y axis of the selection For the Rotate selection command, negative numbers means rotation CCW and positive numbers means rotation CW.
- cleaned up a bit the module imports
- worked on the excellon parsing for the case of trailing zeros. If there are more than 6digits in the coordinates, in case that there is no period, now the software will identify the issue and attempt to correct it by dividing the coordinate  further by 10 for each additional digit over 6. If the number of digits is less than 6 then the software will multiply by 10 the coordinates

14.05.2018

- fixed bug in Geometry CNCJob generation that prevented generating the object
- added GRBL 1.1 preprocessor and Laser preprocessor (adapted from the work of MARCO A QUEZADA)

13.05.2018

- added postprocessing in correct form
- added the possibility to select an preprocessor for Excellon Object
- added a new preprocessor, manual_toolchange.py. It allows to change the tools and adjust the drill tip to touch the surface manually, always in the X=0, Y=0, Z = toolchangeZ coordinates.
- fixed drillcncjob TCL command by adding toolchangeZ parameter
- fixed the preprocessor file template 'default.py' in the toolchange command section
- after I created a feature that the message in infobar is cleared by moving mouse on canvas, it generated a bug in TCL shell: everytime  mouse was moved it will add a space into the TCL read only section. Now this bug is fixed.
- added an EndZ parameter for the drillcncjob and cncjob TCL commands: it will specify at what Z value to park the CNC when job ends
- the spindle will be turned on after the toolchange and it will be turned off just before the final end move.

Previously:
- added GRID based working of FLATCAM
- added Set Origin command
- added FilmTool, PanelizeTool GUI, MoveTool
- and others

24.04.2018

- Remade the Measurement Tool: it now ask for the Start point of the measurement and then for the Stop point. After it will display the measurement until we left click again on the canvas and so on. Previously you clicked the start point and reset the X and Y coords displayed and then you moved the mouse pointer wherever you wanted to measure, but moving the mouse from there you lost the measurement.
- Added Relative measurement on the main plot
- Now both the measuring tool and the relative measurement will work only with the left click of the mouse button because middle mouse click and right mouse click are used for panning
- Renamed the tools files starting with Tool so they are grouped (in the future they may have their own folder like for TCL Commands)

- Commented some shortcut keys and functions for features that are not present anymore or they are planned to be in the future but unfinished (like buffer tool, paint tool)
- minor corrections regarding PEP8 (Pycharm complains about the m)
- solved bug in TclCommandsSetSys.py Everytime that the command was executed it complain about the parameter not being in the list (something like this). There was a missing “else:”
- when using the command “set_sys excellon_zeros” with parameter in lower case (either ‘l’ or ‘t’) now it is always written in the defaults file as capital letter

- solved a bug introduced by me: when apertures macros were detected in Excellon file, FlatCam will complain about missing dictionary key “size”. Now it first check if the aperture is a macro and perform the check for zero value only for apertures with “size” key
- solved a bug that didn't allowed FC to detect if Excellon file has leading zeros or trailing zeros
- solved a bug that FC was searching for char ‘%’ that signal end of Excellon header even in commented lines (latest versions of Eagle end the commented line with a ‘%’)


============================================
This fork features:

- Added buttons in the menu bar for opening of Gerber and Excellon files;
- Reduced number of decimals for drill bits to two decimals;
- Updated make_win32.py so it will work with cx_freeze 5.0.1 
- Added capability so FlatCAM can now read Gerber files with traces having zero value (aperture size is zero);
- Added Paint All / Seed based Paint functions from the JP's FlatCAM;
- Added Excellon move optimization (travelling salesman algorithm) cherry-picked from David Kahler: https://bitbucket.org/dakahler/flatcam
- Corrected small typo in DblSidedTool.py
- Added the TCL commands in the new format. Picked from FLATCAM master.
- Hack to fix the issue with geometry not being updated after a TCL command was executed. Now after each TCL command the plot_all() function is executed and the canvas is refreshed.
- Added GUI for panelization TCL command
- Added GUI tool for the panelization TCL command: Changed some ToolTips.
============================================

Previously added features by Dennis

- "Clear non-copper" feature, supporting multi-tool work.
- Groups in Project view.
- Pan view by dragging in visualizer window with pressed MMB.
- OpenGL-based visualizer.
