# Machine Profiles: Stage 0 contracts

Scope: the first release claims only the dialects `default` and `GRBL_11`. Everything outside a table is
`UNVERIFIED` for the check and refused for the correction. File:line numbers were checked against the working
tree on 2026-11-24 (branch 1.0, d45652f8). Frame and type definitions: `__init__.py` and `records.py` of this package.

Programs that the "emitted" columns are based on (made headless with `flatcam.py --headless --shellfile gen.tcl`):

- `C:\Users\Marius\AppData\Local\Temp\claude\4dd58c567e983e74\3fb684a5-9980-41f8-a3d2-186856ff59e2\scratchpad\mill_default.nc`
- `...\scratchpad\mill_GRBL_11.nc`
- `...\scratchpad\drill_default.nc`
- `...\scratchpad\drill_GRBL_11.nc`

(`gen.tcl`, `t.gbr`, `t.drl` are in the same folder.) Both dialects emit only the words G00, G01, G4, G17 (GRBL_11
only), G20/G21, G90, G94, M0, M03/M04, M05, M5, M6, T, S, F, X, Y, Z and `( )` comments. They never emit
G2/G3, G28, G53, G54-G59, G92, G10, G38, G43, canned cycles or G93. Isolation arcs are written as G01 chords.

## 1. Support matrix: `default` and `GRBL_11`

P = `preprocessors/`. "Emitted" is the same for both dialects unless a dialect is named. "Check" values: exact,
approximate, UNVERIFIED. A "yes" in "Correct" means that the first release rewrites the form; "refused" means
that the whole program is refused and the first unsupported line is named.

| Form | Emitted by FlatCAM (method, line) | Check | Correct | Assumptions |
|---|---|---|---|---|
| G0 with XY | yes: `rapid_code` P/default.py:226-229, P/GRBL_11.py:216-217, through `position_code` appPreProcessor.py:121-123 | exact | yes | start known; units and G90 known |
| G0 Z only | yes: `lift_code` default.py:118-122, GRBL_11.py:118-119; `startz_code` default.py:111-114, GRBL_11.py:112-114; Z lines of `toolchange_code` and `end_moves_code` appPreProcessor.py:172-194 | exact | yes (not changed) | Z is in the frame of the offsets (TLO, G92 known) |
| G0 with XYZ in one block | no | exact | yes | same as G0 with XY |
| G0 speed | no word F on G00 (see the programs) | not checked without a rapid cap; with a rapid cap 1 UNVERIFIED record (first G0), the G0 speed is controller data | n/a | controller data (`$110`, `$111`, `$112`) |
| G1 with axes | yes: `linear_code` default.py:231-234 (no F word), GRBL_11.py:219-221 (F on every line); `down_code` default.py:124-127, GRBL_11.py:121-122; `up_to_zero_code` default.py:220-224, GRBL_11.py:213-214 | exact | yes | modal F known from the previous F word |
| G1 with F only (no axis) | yes: `feedrate_code` default.py:243-245, `z_feedrate_code` :247-249 (GRBL_11.py:228-232) | exact (sets the modal feed, no record) | yes (text kept) | none |
| G2 / G3 with I J (center) | no | exact (swept arc) | yes: arc to chords, rule in section 8 | plane G17; arc mode G91.1 |
| G2 / G3 full circle (no end word) | no | exact (extent 2*pi) | yes | same as above |
| G2 / G3 with R | no | approximate (R is rounded text) | refused | R sign rule of the controller |
| G2 / G3 with K or a G18/G19 plane | no | UNVERIFIED | refused | first release is XY only |
| G17 | GRBL_11 only: `start_code` GRBL_11.py:107 | exact | yes (text kept) | none |
| G18, G19 | no | linear moves: exact; arcs: UNVERIFIED | refused | none |
| G20 / G21 | yes: `start_code` default.py:105, GRBL_11.py:105 | exact | yes (one unit for the whole program) | a change inside the program is followed by the check and refused by the correction |
| G90 | yes: default.py:106, GRBL_11.py:106 | exact | yes | none |
| G91 | no | exact (the new interpreter follows it) | refused | start known; any unknown start makes the path UNVERIFIED |
| G90.1 | no | default: exact; GRBL_11: UNVERIFIED (the Sender restart code allows only G91.1, SenderGen.py:76-77) | refused | none |
| G91.1 | no (it is the GRBL default; the restart preamble writes it, SenderGen.py:128) | exact | yes | none |
| G53 (a move) | no | exact if the setup is known and the dialect supports it, else UNVERIFIED; the resolved G53 updates the position | refused | machine zero known |
| G54-G59 | no (the preprocessors never write a WCS) | exact if the offset of that WCS is in the setup, else UNVERIFIED | refused | offsets known |
| G92 | no | exact if position and WCS offset are known, else UNVERIFIED from that line | refused | none |
| G92.1 | no | exact (sets G92 to zero) | refused | none |
| G10 L2 | no | exact (sets the WCS offset; `ecut_gcode_engine.py:656-668` is the model) | refused | none |
| G10 L20 | no | exact only with a known position, G92 and TLO (`ecut_gcode_engine.py:664-667`); else the offset stays unknown | refused | none |
| G28 / G30 without axis words | no | resolved if the profile stores the position, else UNVERIFIED; the position after it is unknown if not resolved | refused | stored position |
| G28 / G30 with axis words | no | the intermediate move is a record, then as above (`ecut_gcode_engine.py:679-685`) | refused | same |
| G28.1 / G30.1 | no | UNVERIFIED | refused | none |
| G38.2-G38.5 | no (probing comes from the Sender, Levelling and `Toolchange_Probe_MACH3`, not claimed) | the swept path up to the target is checked; the end point is unknown, so later moves are UNVERIFIED until an absolute move | refused | none |
| G43 Hn | no | UNVERIFIED (tool table) | refused | none |
| G43.1 Zn / G49 | no (G43.1 and G49 are read by the Sender restart code, SenderGen.py:76-100) | exact (sets or clears TLO) | refused | none |
| G81-G89, G80 | no (drilling is explicit G00/G01, see drill_*.nc) | UNVERIFIED | refused | none |
| G93 | no | UNVERIFIED | refused | none |
| G94 | yes: default.py:107, GRBL_11.py:108 | exact | yes (text kept) | none |
| G4 P | yes: `dwell_code` default.py:259-263, GRBL_11.py:241-245 | exact (no motion) | yes (text kept) | none |
| M3 / M4 with S | yes: `spindle_code` default.py:251-257, GRBL_11.py:234-239 | RPM range: exact when S is a number in the line; without S the speed is inherited, so approximate | yes (text kept) | S is RPM |
| M5 / M05 | yes: `spindle_stop_code` default.py:265-267; `M5` in `toolchange_code` | exact (no motion) | yes (text kept) | none |
| M6 with T | yes: `toolchange_code` default.py:129-218, GRBL_11.py:124-211 | exact (a tool change record; TLO assumed unchanged) | yes (text kept) | no G43 in the program, so TLO stays as declared |
| M0 / `(MSG, ...)` | yes: inside `toolchange_code` | no motion; position after the pause is assumed unchanged | yes (text kept) | the operator does not move the machine |
| S alone, F alone | S: only with M3/M4; F: see G1 with F only | exact | yes (text kept) | none |
| Laser power | no: the two dialects write RPM, not laser power; laser dialects (`GRBL_laser`, `default_laser`) are outside the first release | UNVERIFIED | refused | none |
| Tool diameter | not a word; comment `(TOOL DIAMETER: ...)` (default.py start_code, milling) or `(Change to Tool Dia = ...)` (toolchange) | approximate (read from a comment); absent: UNVERIFIED for the fixture envelope | n/a | comment text is correct |
| Comments `( )` and `;` | yes | no meaning, except the `(MP:...)` markers (section 3) | yes (kept) | one level, no nested `)` |
| Any other word or an expression (`#`, `[`) | no | UNVERIFIED | refused | none |

## 2. Output table

State: checked = the file:line was read in the current code and matches; corrected = the number or the name
in the plan was wrong and is fixed here.

| Output | Place (current code) | Stage | State | Note |
|---|---|---|---|---|
| GUI save, batch save | `CNCJobObject.export_gcode_handler` CNCJobObject.py:807; rename at :820-822; write `_save_gcode_file(filename, self.source_file)` at :827 | 1 check, 3 correct | checked | Run the check before :820. It writes `source_file`. |
| Tcl `write_gcode` | `TclCommandWriteGCode.py:108` (`return obj.export_gcode(...)`); `export_gcode` CNCJobObject.py:1123; text is built :1157-1298, `finalize_gcode` :1300-1306, `_write_gcode` :1308 -> `_save_gcode_file` :1314 | 1 check, 3 correct | corrected | The imported branch is at CNCJobObject.py:1145, not in the Tcl file. This path writes assembled text, not `source_file`: two texts exist. Both reach `_save_gcode_file` (:1326), the one place to hook. A Reject must raise a Tcl error (option list at TclCommandWriteGCode.py:26-30). |
| Tcl `export_gcode` | `TclCommandExportGcode.py:7` | 1 | corrected (row added) | Returns design G-code: `purpose="design"`, no correction. |
| Solder Paste save | `SolderPaste.on_save_gcode` SolderPaste.py:1143-1206 (write at :1191) | 1 check, 3 correct | checked | Own writer. |
| Levelling "Save Probing GCode" | `ToolLevelling.on_save_probing_gcode` Levelling.py:455-503 (writes at :488 and :492) | 1 check; blocked with the correction on until 4 | checked | Own writer. |
| Height map import | `import_height_map` levelling_core.py:1658-1730; `match_nearest_point` at :1710, result at :1730 | 4 | corrected | Range was 1701-1730; the function starts at :1658. |
| Sender "Save levelled G-code" | `ToolGCodeSender.on_save_levelled_gcode` Sender.py:5362-5383 (writes `self.job_lines` at :5379) | blocked from 3, done in 4 | checked | |
| Text editor save | `AppTextEditor.handleSaveGCode` appTextEditor.py:457 (write at :608; the Save button can call a `save_handler`, :339-354) | 1 check; correction with the user's confirmation | checked | Only for G-code files. |
| Sender stream, plain | `on_start` Sender.py:4583-4641 -> `_start_stream_worker` (defined :4827) | blocked from 3, done in 4 | corrected | The plan range 4500-4581 is `build_levelled_job` (:4500) and `build_stream_job` (:4559), which build `job_lines`; it is not a stream route. |
| Sender stream, new probe map | `on_start` Sender.py:4583-4641 (same entry) | same | checked | |
| Sender stream, existing map | `_on_frame_ready` Sender.py:4773-4808 -> `_start_stream_worker` | same | checked | `_start_levelled` is at :4696. |
| Sender start from line | `restart_preamble` call Sender.py:4853-4855, tuple :4862; `SenderGen.restart_preamble` SenderGen.py:51-158 | same | corrected | Plan said :4849-4860; the restart block is :4850-4863. |
| Live probe, standalone Probe | `on_grbl_autolevel` Sender.py:4153-4154 -> `_queue_probe` (:4156, `worker_task` :4189); `grbl_client.py:1366-1398` (height check in the probe run) | blocked from 3, done in 4 | checked | |
| Resume after tool change | `_on_resume_button` Sender.py:4046-4085; `on_tool_change` :5128 | 4 | checked | |
| Cutout | Geometry -> Milling, Excellon -> Drilling | 1 | checked | Covered by the generators. |

Other plan references that were checked and are correct: `draw_workspace` PlotCanvas.py:502; `cncjob_bed_*`
defaults.py:510-515; `bed_xy` appPreProcessor.py:136; `AxisConfig` ecut_config.py:118; `parse_grbl_work_offset`
grbl_protocol.py:112-153; `ecut_gcode_engine.py:656-674`, `:679-681`, `:914-923`; `_fixed_points`
camlib.py:7521-7552; ParseGCode.py:322-325, :1584-1624, :1685-1733, :1855-1897, :1942-1988; appIO.py:4397;
`Milling.py:3420`; `Drilling.py:2442`; `TclCommandDrillcncjob.py:455-457`; `PhotoCarveGcode.py:24-43`;
`defaults_read_form` PreferencesUIManager.py:953; widget maps Milling.py:714-722 and Drilling.py:454-477;
Common.py:694-712 and :835. Corrected: CNCjob reads the `cncjob_bed_*` keys at camlib.py:3164-3170 (plan: 3167-3172).
`Toolchange_Probe_MACH3.py:153-173` has only Z probe moves (G31 Z, G92 Z, lines 163-168); the XY moves are absolute,
so "incremental XY" in the plan does not apply to that file. Only G92 Z has to be named in the matrix.

## 3. Fixed-sequence metadata

Ruling (fixed, from the plan): structured comments, emitted only when the active profile is not None.

```
(MP:FIXED BEGIN toolchange)
...lines of the sequence...
(MP:FIXED END)
```

Roles: `toolchange`, `end`, `probe`. The `role` value maps to `MotionRecord.fixed_role`. The role names hold no
`)` or `;`. Free text never goes into a marker (use `safe_comment_text`, ParseGCode.py:45, if it ever has to).

Tolerance of the readers (checked):

- Both dialects write `( )` comments already (header lines and `(MSG, ...)`, see the .nc files), so the controller
  syntax is accepted.
- Sender and GRBL stream: `SenderGen.clean_lines_numbered` (SenderGen.py:22-39) removes `\([^)]*\)` and `;...`
  and drops lines that become empty. The Sender builds `job_lines` through it (Sender.py:4484, :5924, :6127).
  `grbl_client.dwell_seconds` and the stream code use the same regex (grbl_client.py:80). A marker line never
  reaches the controller and never counts against the line length limit.
- `ParseGCode` strips comments before it reads the words (ParseGCode.py:27-28, :64-106); E-Cut does the same
  (`_strip_comments`, ecut_controller.py:145).
- Consequence: after `clean_lines_numbered` the roles are gone. This is fine while the correction runs before the
  Sender (`prepare_output`, then clean). Stage 4 must not expect a role in `job_lines`.

Where the markers are written. All call sites are in the generators, so the preprocessor files stay unchanged
(the user's own preprocessors work too). Each wraps the text from `toolchange_code`, `end_code` (and the
`startz_code` approach when it is the toolchange approach):

| Generator | Place |
|---|---|
| Milling | `CNCjob._mill_geometry_paths` camlib.py:4767 (`toolchange_code` :4878; `startz_code` :4882); `mill_tool_end_gcode` camlib.py:4972 (`spindle_stop_code` :4977/:4981, `end_code` :4994) |
| Drilling | `CNCjob.excellon_tool_gcode_gen` camlib.py:4069 (`toolchange_code` :4264, `startz_code` :4269, `spindle_stop_code` :4427, `end_code` :4429) |
| Drilling by Tcl | `CNCjob.tcl_gcode_from_excellon_by_tool` camlib.py:4997 (`toolchange_code` :5425, `startz_code` :5321-5324, `end_code` :5479, :5558) |
| Solder Paste | `CNCjob.generate_gcode_from_solder_paste_geo` camlib.py:5669 (`toolchange_code` :5754, `end_code` :5802) |
| Photo Carve | `write_tool_gcode` PhotoCarveGcode.py:24 (`toolchange_code` :77, `end_code` :116; the lift :75 and :78 sit inside the sequence) |
| Preprocessor methods covered | `toolchange_code` default.py:129-218 and GRBL_11.py:124-211; `end_code` default.py:236-241 and GRBL_11.py:223-226 (via `end_moves_code`, appPreProcessor.py:172-194) |
| Probe sequences | `Toolchange_Probe_MACH3.toolchange_code` and the Sender / Levelling probe writers (`LevellingGen.generate_probing_gcode`, the Sender `probing_gcode` at Sender.py:4379). They use role `probe`. Not in the first release, but the form is set now. |

Start code is not a fixed sequence (no motion). "Start" of Milling and Drilling is only the optional first
`startz_code` Z move; it takes the `toolchange` role when it replaces the toolchange Z (default.py:145).

None baseline: with the profile None the generators write no marker and the output is byte-identical.
No problem was found with the ruling. One limit to remember: because of the regex, a role marker inside another
`( )` comment on the same line would be cut at the first `)`. Each marker therefore sits on its own line.

## 4. Legacy transition table for `cncjob_bed_*`

Facts (checked): defaults are max 300/400, offset 0/0, skew 0/0 (defaults.py:510-515). `CNCjob` reads them when
it is made (camlib.py:3164-3170); the job keeps a copy in its options (CNCJobObject.py:114-119). They apply only
to G00 / G01 XY through `PreProc.bed_xy` (appPreProcessor.py:136-156, called by `position_code` :121-123).
The toolchange XY, the end XY and all Z words do not use them (`toolchange_code`, `end_moves_code`). The same
values also feed the travel and time estimate (camlib.py:3490-3545, :3604-3616) and block Voronoi isolation when
nonzero (camlib.py:4597).

`bed_legacy` in the provenance = the keys were applied and at least one of offset or skew is nonzero.

| Case | Bed keys | Provenance | Correction | Note |
|---|---|---|---|---|
| New job, profile None | applied, as now | none | none | byte-identical output |
| New job, active profile, from stage 3 (fixed ruling) | not applied: the job-local snapshot zeroes them | `bed_legacy: false` | the calibration replaces them | status line warns once at profile selection if a key is nonzero |
| New job, active profile, stages 1-2 | applied, as now (no correction exists yet) | `bed_legacy: true` or `false` as applied | none | stage 1 records it so that stage 3 can decide |
| Job made before the feature | applied at that time; values are in the job options | none = unknown provenance | none, unless `-uncorrected_source yes` or the user confirms; never when a stored key is nonzero (the text already has the transform) | warn if a stored key is nonzero |
| Job made in stages 1-2 with a profile | as recorded | `bed_legacy` as recorded | `false`: allowed when the profile hash matches; `true`: refused, offer regeneration | the text carries the legacy transform when `true` |
| Correction off (profile active, calibration off) | from stage 3 not applied (as row 2) | `bed_legacy: false` | none | the output has no skew fix at all; the status line says it |

## 5. Imported and edited text in Tcl and batch export

- Applies to a CNCJob of unknown provenance: imported (`gcode_imported`, CNCJobObject.py:1145) or edited
  (`edited_source_file`, CNCJobObject.py:201, :672).
- Tcl `write_gcode` (and the batch export, which uses the same writer) takes `-uncorrected_source yes|no`.
- Flag missing: no correction. The command result says: "not corrected: the source has unknown provenance".
- `no`: no correction, the check still runs, the result says "declared as not to correct".
- `yes`: the text is declared uncorrected, so the correction runs (if the subset allows it).
- A job with a provenance record ignores the flag; its record decides.
- The GUI asks the user once and stores the answer in the job.

## 6. Result states

`CheckResult.status` (records.py:96-113) is derived from the violation list:

| State | Meaning |
|---|---|
| checked | no violation and no `UNVERIFIED` record |
| partially checked | no confirmed violation, 1 or more `UNVERIFIED` records |
| violations | at least 1 violation that is not `UNVERIFIED` |

"Checked" covers: every record has its M endpoints; the bed bounds, the exclusions (swept path), the process
limits that the profile holds, and the Z travel. The result always lists its assumptions (setup, start position,
offsets, units, TLO). It never says "checked" with an `UNVERIFIED` record.

Stays UNVERIFIED:

- G0 speed, axis caps and overrides, when no controller data is given;
- any move after an unresolved G28, G30, G53 or an unknown start;
- the fixture envelope when the tool size is unknown. The envelope is the centerline grown by the tool radius
  plus a margin, the same as `AppExclusionAreas.grown_shape` (Common.py:729-743): radius + 0.1 mm (0.00393701 in);
- the laser power (no laser dialect in the first release);
- every word outside the section 1 matrix.

## 7. Coverage per release

| Output | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|---|---|---|---|---|
| GUI save, batch save | checked | checked, with permanent exclusions | checked and corrected | same |
| Tcl `write_gcode` | checked | same | checked and corrected | same |
| Tcl `export_gcode` | design, not changed | same | same | same |
| Solder Paste save | checked | same | checked and corrected | same |
| Text editor save | checked | same | checked; corrected only after a confirmation | same |
| Levelling Save Probing GCode | checked | same | blocked with the correction on | checked and corrected |
| Height map import | not checked | not checked | not checked | checked, with the setup of the probe |
| Sender stream, new map, existing map, start from line | not checked (label shown) | not checked | blocked with the correction on | checked and corrected |
| Sender Save levelled G-code | not checked | not checked | blocked with the correction on | checked and corrected |
| Live probe, standalone Probe | not checked | not checked | blocked with the correction on | checked |
| Tool change recheck (Resume) | not checked | not checked | not checked | checked |
| E-Cut | not checked | not checked | not checked | bound by fields, with a confirmation |
| Plot (design check) | checked | checked | checked | checked |

Homing, jog and typed commands in the Sender stay available in every stage.

## 8. Correction rules

1. Only a program whose every command is in the "yes" rows of section 1 is corrected. If not, the whole program is
   refused and the first unsupported line number and word are named. A partial rewrite is never written.
2. Arc: split into the fewest chords. The error of the chords after the transform and after the rounding to the
   output decimals must stay at or below `tol` = 0.005 mm.
3. If the rounding to the output decimals alone is above `tol`, the correction is refused.
4. A chord is a G1 with an explicit `G1` word. A later block that inherited G2/G3 gets an explicit `G2`/`G3`
   word again (and a later block that inherited G1 after the arc keeps its own word).
5. G93 is refused. All forms that section 1 marks "refused" are refused.
6. Non-motion blocks (tool change, spindle, coolant, pause, comments, snippets, M2/M30, offset commands) keep
   their order and their text. Only the coordinate words of motion blocks change.
7. A fixed sequence (section 3) is not mapped. Its destination is resolved in M and written as an absolute move
   from the corrected start point; after it the position is set to its true end point.
8. The correction never writes `source_file`. Preview, refresh and regeneration use `purpose="design"`.

## 9. Input/output examples (each becomes a failing behavior test when its stage starts)

Notation: `W` = work frame, `M` = machine frame, `?` = unknown (None).

1. Fixed station with the same XY as an ordinary rapid (stage 3). Input (`default`, correction on):
   `(MP:FIXED BEGIN toolchange)` / `G00 Z15.0000` / `G00 X0.0000 Y0.0000` / `T1` / `M6` / `G00 Z15.0000` /
   `(MP:FIXED END)` / `G00 X0.0000 Y0.0000`. Out: the station move keeps its machine position; the last rapid
   (design XY 0,0) is mapped by C. Without the marker both would be mapped.
2. Rapid that crosses a forbid area with both ends outside (stage 2). In: area x 10..20, y -5..5;
   `G00 X0 Y0` / `G00 X30 Y0`. Out: one record, start_m (0,0), end_m (30,0), violation `EXCLUSION` with `area` set,
   line 2. The check is on the swept path.
3. WCS / G92 / TLO change inside a program (stage 1). In: `G54` / `G00 X10 Y10` / `G55` / `G00 X10 Y10` /
   `G92 X0 Y0` / `G00 X10 Y10`. Setup: G54 offset (0,0), G55 offset (100,0). Out: end_m of the records (10,10),
   (110,10), then the G92 offset is computed from the known position, end_m of the third record follows it. With
   G55 not in the setup: record 2 and later are `UNVERIFIED`.
4. G28 with axis words (stage 1). In: `G28 Z5`, no stored position in the profile. Out: record 1 = the move to Z5
   (verified), record 2 = the move to the stored position (`UNVERIFIED`); the position after it is unknown, so
   the next `G00 X5` has start unknown and is `UNVERIFIED`; status "partially checked".
5. G10 known and unknown (stage 1). In: `G10 L2 P1 X-50 Y-20` makes the G54 offset known (-50, -20) even if the
   start position is unknown. In: `G10 L20 P1 X0 Y0` with an unknown position or unknown G92: the offset stays
   unknown; the moves after it are `UNVERIFIED`. With position (10,10,0), G92 (0,0,0), TLO 0: the offset becomes
   (10,10,0).
6. Absolute move from an unknown start (stage 1). In: `G00 X10 Y10` with the start unknown: record start `None`,
   end (10,10, Z?) known for X,Y; the path itself is `UNVERIFIED`; the next move starts from (10,10).
7. Restart under a non-identity correction (stage 4). In: `restart_preamble` + resumed program, C = shear 0.01.
   Out: the preamble is checked and not corrected again; the resumed program is corrected once; the check reads
   the concatenation from the live start position.
8. Offline probe round trip with a sloped map and a non-identity correction (stage 4). Out: each reported row maps
   back to the design XY with C^-1 and the setup of the probe, matches exactly one grid point, and the height is
   stored under that point; a duplicate or ambiguous row is refused.
9. Full circle with only the center (stage 1). In: `G17 G90 G91.1` / `G00 X10 Y0` / `G02 I-10 J0`. Out: one
   record, arc_center (0,0), arc_direction cw, arc_extent 2*pi, swept path is the whole circle.
10. Arc correction with an inheriting block (stage 3). In: `G00 X0 Y10`, `G02 X10 Y0 I0 J-10` (cw quarter arc, center
    (0,0)), then the block `X0 Y-10 I-10 J0` without a motion word (it inherits G02). Out: the first arc becomes
    G1 chords, each with an explicit `G01`; the inheriting block, now an arc after G1 chords, gets an explicit
    `G02` (or is split into chords the same way), so it is never read as G01.
11. Complete exported programs, one for each claimed dialect (acceptance): the four .nc files listed above are
    the baseline samples (preamble, tool change, ending) for `default` and `GRBL_11`.
