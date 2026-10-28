# Opus 5.5 handover: native DipTrace importer

Date: 2026-10-28. Target: continue the FlatCAM EVO native DipTrace implementation.

## User's current instructions

- Continue until all requested features are implemented. Download needed evidence.
- The user doesn't have DipTrace installed. Find public evidence rather than
  asking them to generate fixtures or export files.
- **Use fresh agents.** Don't resume agents from this session. All dispatched
  agents have returned; none is still editing files.
- The latest request was to create this handover for Opus 5.5. Implementation
  stopped for the handover. The overall feature plan is **not complete**.
- Preserve existing work. Don't commit, push, install applications, or make
  architectural changes without the relevant explicit authorization.

## Workspace and navigation

- Root: `D:\1.Development\1.PROJECTS\FlatCAM_EVO`.
- Branch: `1.0`; HEAD observed at handover:
  `5dfc6871 docs(changelog): record the changes of 28.10.2026`.
- jCode handle: `src/Beta`. Resolve it first and use jCode for code navigation.
- Project docs handle: `local/FlatCAM_EVO`. An indexed file can be absent from
  jDoc even when it exists on disk. Don't interpret that as filesystem absence.
- Temp research root: `C:\Users\Marius\AppData\Local\Temp\opencode`.
  Its jCode handle is `local/opencode-b0fde996`; individual probes may be stale
  or missing from that index. The docs handle `local/opencode` also exists.
- Use PowerShell and `.venv\Scripts\python.exe` (Python 3.14.3 here).
- Follow repository `CLAUDE.md`, global `AGENTS.md`, applicable skills, minimal
  localized changes, behavior-focused TDD, and the existing test budget.
- Only one implementation agent should edit the parser/topical test at a time.
  Independent research can run in parallel. Supply fresh agents concrete context.

### Dirty working tree

Modified at handover: `CHANGELOG.md`, `appGUI/ImportDialog.py`,
`appGUI/MainGUI.py`, `appHandlers/appIO.py`,
`appHandlers/appSignalConnector.py`, `appMain.py`, `defaults.py`,
`tests/gates/slow_files.txt`, `tests/test_pcbdoc_import.py`,
`tests/test_pcbdoc_parser.py`.

**Untracked:** `appParsers/ParseDipTrace.py`, the entire new
`tests/fixtures/diptrace/` tree, and this handover. Ordinary `git diff` doesn't
show untracked parser/fixture contents. There is unrelated/concurrent work;
don't revert or stage it wholesale. No commits or pushes were made by this work.

## Implemented production behavior

Main file: `appParsers/ParseDipTrace.py` (1,726 lines before this note).
Topical tests: `tests/test_pcbdoc_parser.py::TestDipTraceBoard`.

1. Native versions accepted: **37, 38, 39, 40, 41, 45, 46, 54, 60**.
   Acceptance means decoded record kinds, not universal support for each version.
2. Existing File/recent-file import integration, asynchronous parsing, layer
   selection, units conversion and inherited-opening-default prompt/reparse
   remain in place. See `appHandlers/appIO.py` and `appGUI/ImportDialog.py`.
3. Pads, arbitrary modern placement, offsets, angles, mirrored components,
   holes/slots, physical copper spans, hidden/blind layers, plane rings,
   outlines/arcs, explicit documentation graphics, saved pour strokes/spokes.
4. Complete primary net membership/defaults and exact owned trace offsets.
   Net ownership prevents phantom vias where unrelated nets share coordinates.
5. Circular routes, including CW/CCW/major arcs, use the existing KiCad geometry
   helper. Ordinary mode-3 point defaults don't invent vias without connectivity.
6. Hidden-reference placement falls back to dense frame order only when all
   counts, component dimensions and available identities agree. Hidden markings
   tolerate inactive alignment values. Unnamed artwork supports zero TextLines.
7. Rounded corners use native fraction `/1000000 * min(width, height)`, not
   `/30000` length units. Verified against independent Habitat Gerber apertures.
8. **New this session: `_pad_modes()`**, approximately lines 738-766. Modern
   authoritative modes are four biased three-byte integers per pad in a
   separate table, including both unused sentinels. These supersede old primary
   Boolean flags, including explicit Common=0. Saved paste caches alone never
   activate segmentation. Segmented paste and By Paste mask use effective modes.
9. **New: counted component field-ID lists** in `_components()` replace the
   previous fixed header skip. Differential_Pairs has lists of 0, 2 and 3 IDs.
10. **New: first trace-point width is ignored** because segment properties
    belong to the ending point. All actual segment widths remain validated.
11. **New: `_documentation_tables()`** validates and preserves grid/cell text,
    font, rectangle and raw parameters before the pour count. **Tables aren't
    rendered**: layer mapping remains unknown and an explicit warning is emitted.
12. **New: `_pair_metadata()`** retains validated differential-pair membership,
    center points, side offsets and raw parameters. Expanded primary member
    traces supply copper; centerlines never add copper. Discovery currently
    depends on matching `label_P`/`label_N` net names. Malformed candidates can
    be skipped, so don't claim universal pair-metadata discovery.
13. **New: D-shaped pads and terminals**, `_pad_outline()` around 1031-1070.
    Style 4 is accepted. At angle zero, flat bottom and rounded top in Y-up.
    For W<=H, radius W/2; cap center `(0,H/2-W/2)`. For H<W, the observed
    silhouette is an upper half-ellipse with radii W/2 and H, flat at `-H/2`.
    Existing pad rotation, mirroring and drill handling are retained.
    **D mask/paste offsets use Shapely contour buffering; equivalence to a native
    D-opening export hasn't been independently verified.**

## Remaining scope: don't mark these complete

- Full net-class/per-layer/global/ClassToClass rules and inheritance.
- Saved teardrop production import and any required meander-specific geometry.
- General differential/meander editing semantics beyond saved expanded traces.
- Legacy automatic markings and unresolved legacy opening defaults.
- Routed-via mask/paste openings; correct effective static-via export inclusion.
- Documentation-table rendering on a verified native layer.
- Embedded raster-only pictures and remaining font/stroke fidelity.
- Per-layer custom pad stacks **if the product actually supports them**. No
  native schema has been proved. Don't assume `mask[0]` is a custom-stack flag.
- **Net-aware refilling:** not implemented. Saved native caches remain
  authoritative. Don't enable an approximate filler with incomplete rules.

## Research breakthroughs ready for the next stage

### Effective modes and Habitat manufacturing comparison

Pinned source:
https://github.com/machadoleonardo94/HabitatControl-hardware/tree/f2850affb26705506b007f9fd41cef257eeb1445/Habitat_V4-5ch

Local native:
`corpus-machadoleonardo94--HabitatControl-hardware--Habitat_V4-5ch--layout.dip`.
Local CAM: `habitat-v4-5ch-cam.zip` (both under the temp root).

- Effective mode table starts **743038**, placed components end **758110**,
  embedded-library records continue through **764386**.
- Q1.3 record **746038**; U3.6 **757330**; U4.9 **757462**; subsequent thermal
  pad records increment by 132. States `[Common,Common,Segments,Common]`.
- Top Paste area improved **508.8363 -> 436.5133 mm²**, published CAM
  **436.2215 mm²**. All seven segmented pads match at 0.5 micrometre tolerance.
  Remaining aggregate extra: **0.190273 mm²** at 0.5 micrometre tolerance;
  **0.003730 mm²** at 2 micrometres. Full paste equivalence isn't established.
- Top Mask remains **992.7905 vs 828.3074 mm²**. Exactly **258 static vias**
  explain **164.116030 mm² per side** of the discrepancy. They have Common
  modes, not native Tented overrides. Don't force them tented to match CAM.
- Source object type is the first three-byte integer after the two leading
  flags in metadata parsed by `_legacy_placements`: **14 free Pad, 15 mechanical
  hole, 16 static Via**. The next two integers aren't proven ViaStyle indices.
- Gerber object-inclusion settings are external **`gsettings.dat`**, signature
  `GERBERSET`, current version 5, records keyed by layer label. Fields:
  Pads, Vias, Pad/Via Holes, Mechanical Holes, Text, Tables, Pictures, Mirror,
  Flip Text, Traces, Dimensions, Export Enabled. Global `pastesmd` defaults true.
  Original board-author settings and explicit-mode/category precedence remain
  unresolved. Export selection isn't the same as native tenting.
- Copper extra at 0.5 micrometre tolerance remains **0.471957 mm² Top** and
  **1.391189 mm² Bottom**. Nothing was clipped to hide those differences.

Relevant temp probes: `sol_effective_mask_20261028.py`,
`sol_paired_mask_20261028.py`, `sol_mask_help_20261028.py`,
`diptrace_habitat_cam.py`, `diptrace_pad_modes_probe.py`.

### Base net classes and saved teardrops: research, not production

**Read first:** `sol_native_rules_handoff_20261028.md` in the temp root.
Raw records: `sol-native-class-records-20261028.json` and
`sol-native-teardrops-CNC_controller-v54-c3d580ba.json`.

- Base class framing verified for **30 classes / 74 property rows / 15 boards**.
  Table bounds: Habitat **404..1191**, GPSDO **466..1003**, Differential_Pairs
  **921..1864**, SJTwo **505..1437**, PCB_5 **430..604**, rotate v60 **242..410**,
  official Banana Pi **731..2723**.
- Each class has name/type/flags, counted **25-number (100-byte) property rows**,
  scalars, allowed-via IDs, autorouting flags/values, uncoupled-length/tolerance,
  route-layer count and UpdateId. Positional class index differs from UpdateId.
- `AllLayers=true` can retain inactive differing per-layer rows. `AllVias=true`
  can retain a nonempty inactive allowed-via list. Native **-10000** means unset,
  not a negative physical clearance. Banana Pi supplies real six-layer rules.
- Property slots 5..24, some flags, nonempty route-layer payloads, later
  neck/length extensions, global DRC fallback and ClassToClass remain unproved.
- Official CNC controller has **496 saved teardrop records across 94 nets**:
  490 area-bearing and 6 one-point records. Four contours are invalid under
  ordinary polygon validity. Don't silently repair without native fill-rule proof.
- Teardrop settings: **4 three-byte types + 6 four-byte ratios = 36 bytes**.
  Each net carries settings, per-trace counted references, then counted cached
  geometry: layer, DrawX/DrawY, counted absolute XY. IDs are positional, with no
  trailing ID. Don't translate points by DrawX/DrawY or regenerate saved curves.
- CNC pad-settings block **1957951..1997443**, net extension
  **1997443..2034635**. Habitat corresponding blocks
  **1202807..1248023..1250492**. The **production start anchor is unresolved**;
  the research locator searches default-ratio values. Don't ship that signature.
- Ignored 14-byte pour block: two three-byte enums, two four-byte values
  (fill type, thermal style, line spacing, thermal width). Native spokes can
  override width: Habitat has 97 at raw 9144 and 30 at 15240.
- Banana Pi pour 14 disproves an existing assumed constant: final field at
  **1419456** is `0f4241`, not `0f4240`; meaning unresolved. Don't zero it out.

### Differential_Pairs and Marking evidence

Read `diptrace_native_layout_20261028\reports\Native DipTrace layout findings.md`
under the temp root. Public files: https://github.com/SorokinRed/DipTrace/tree/master/Examples .

- Differential_Pairs v41 now imports: **402 objects, 108 nets, 381 traces,
  2153 points, 19 metadata pairs, 355 centers**. Net table **356519..467661**;
  pair table **1562692..1584830**.
- The first-width fix addresses 90 zero-width *initial* points. Segment-ending
  points have positive widths. Pair members are expanded saved primary traces.
- Exactly **2153 x 11 bytes** of point extensions at **1584830..1608513** remain
  semantically unresolved. First values reach 18; they aren't simply a Meander
  enum. Nonnegative references on ordinary MISO/MOSI/SCK don't prove pairing.
- Marking v38 has valid Arial settings at **11478**, documentation count at
  **11515**, one table **11518..12518**, then zero pours at **12518**.
  Don't treat missing font candidates as permission to skip the settings block.
- Table has one column, four instructional cells, dimensions **30.48 x 22.352
  mm**. Row heights **4.064,5.588,5.588,7.112 mm**. Rendering layer unresolved.
- Routed arcs independently exercised in `arm.dip` (887 midpoint flags) and
  `arctest_routed.dip` (1245), rather than the unrouted `arctest.dip` alone.

### D-shape and custom-stack evidence provenance

Read these temp reports:

- `DipTrace native evidence 20261028\reports\DipTrace native evidence.md`
- `DipTrace native evidence 20261028\reports\DipTrace D pad orientation proof.md`

Official video: https://www.youtube.com/watch?v=8tvZ8BT7AHg&t=70s .
Official library: https://diptrace.com/downloads/patterns.zip .

The video shows 0.26 x 0.72 mm D pads. The official library has angle-zero
D terminals in `_qfn_p0.40.lib` and MainStack style-4 pads in `_son_p0.50.lib`.
The orientation research additionally used static inspection and emulation of
geometry/export routines extracted from signed official 4.3.0.6 and 5.3.5.1
packages. No installer or full DipTrace application was launched. Keep this
provenance explicit; don't describe all evidence as CAM-only or visual-only.
Production geometry was constructed mathematically rather than copying assembly.

Custom-stack survey: **78 distinct board hashes**, **35,773 decoded live pads**
in 71 boards; none had nonzero `mask[0]`. Official 2018 support said arbitrary
different pad shape/size per layer wasn't supported. Current specs document one
MainStack plus hidden/blind layers, spokes and plane rings. This doesn't prove
all current custom-stack capabilities absent; it does disprove treating an
unverified flag as a ready-to-implement stack schema.

## Verification: distinguish the generations

- **Latest after D-pad implementation:** parser suite **76 passed, 2 skipped,
  102 subtests passed**, 286.86 seconds. Optional downloaded-example environment
  wasn't enabled in that run. One earlier verbose run timed out at 240 seconds;
  the later run completed. No post-D broad importer suite or SJTwo check yet.
- **Before D pads, after table/pair/header fixes:** parser + import suite with
  downloaded examples enabled: **99 passed, 111 subtests passed**.
- **Earlier, before this session's mode/table/pair/D changes:** broad nine-file
  importer suite: **139 tests and 120 subtests passed**. Don't present it as
  validation of the current final parser.
- SJTwo's seven layers passed 0.5 micrometre tolerance after the effective-mode
  fix, before D pads. Historical drills: 174 routed vias, 256 PTH features
  including two slots, four NPTH; no missing/extra. Recheck current code.
- `rtk git diff --check` passed again at handover before adding this note.
- A fresh agent source-checked the final implementation for this handover.
  D-pad changes haven't completed the normal two-stage implementation review.
  Earlier mode/header/table/pair changes had review, but source-review conclusions
  shouldn't replace real manufacturing checks.
- An early review alleged malformed-input state clearing failed. The test was
  updated to corrupt the **effective mode**, not a superseded primary Boolean.
  Subsequent actual run passed all 12 malformed-input subtests. Don't chase the
  obsolete report as a live parser-reset bug.

Suggested current verification (PowerShell, workspace root):

```powershell
$env:DIPTRACE_V38_EXAMPLES='C:\Users\Marius\AppData\Local\Temp\opencode'
.venv\Scripts\python.exe -m pytest tests/test_pcbdoc_parser.py tests/test_pcbdoc_import.py tests/test_eagle_brd_import.py tests/test_kicad_pcb_import.py tests/test_sprint_layout_import.py tests/test_allegro_brd_import.py tests/test_project_selector.py tests/test_file_filters.py tests/test_qt_command_connections.py -q -n 4 -p no:cacheprovider --tb=short
.venv\Scripts\python.exe "C:\Users\Marius\AppData\Local\Temp\opencode\sjtwo_cam_check.py" "Rev.1.E.2" "tolerances"
.venv\Scripts\python.exe "C:\Users\Marius\AppData\Local\Temp\opencode\sjtwo_drill_check.py"
rtk git diff --check
```

Use a sufficiently long tool timeout (480000 ms for the broad parallel suite).
The valid SJTwo CAM folder is **Rev.1.E.2**, not stale Rev1.E. Native and CAM are
pinned to `kammce/SJTwo` commit `71c0454bd80e321cb968c9908375ef504d213592`.
Occasional installed-font FT_Exception warnings aren't new geometry failures.

## Recommended continuation

1. Fresh reviewer checks D geometry, offsets and actual coverage. Run the current
   broad suite and pinned manufacturing baselines before further production edits.
2. Fresh research agents read the detailed class/teardrop reports. Establish a
   structural start anchor for net extensions and native fill rules for the four
   invalid teardrop contours. Integrate only validated saved geometry.
3. Retain fully decoded class metadata and source object types, then finish the
   effective per-layer/global/class-to-class rule semantics. Raw data retention
   isn't equivalent to applying those rules.
4. Decode external/source mask-paste settings and precedence. Reuse the existing
   source-default prompt if source-installation information is genuinely absent.
   Don't quietly select defaults just to reduce a CAM residual.
5. Finish legacy markings, table layer mapping, raster storage and remaining
   routing extensions using actual specimens. Verify D opening offsets separately.
6. Only enable independently checked refilling once net ownership, clearances,
   priority, thermal overrides, islands and keepouts are all resolved.

Binary basics: big-endian three-byte bias **1000000**, four-byte bias
**1000000000**; XY `/30000 mm`, native Y inverted. v37 strings use biased
three-byte byte counts/single-byte text; later verified versions use two-byte
character counts and UTF-16BE. Preserve explicit zero versus inheritance.
Plane layer type isn't negative image polarity. Open cache paths must remain
open. Unknown manufacturing geometry must fail/report, not become straight or
silently disappear. Never require companion Gerber files at import runtime.

Official semantics, not native byte schemas:
https://diptrace.com/books/xml_specs/DipTrace_PCB_XML_Specification.md and
https://diptrace.com/books/xml_specs/DipTrace_PattEdit_XML_Specification.md .

Existing fixture notes (`tests/fixtures/diptrace/VERSION_COMPATIBILITY.md`,
`V38_EVIDENCE.md`) predate some breakthroughs above and need reconciliation.
Downloaded public corpus redistribution licenses aren't universally established;
keep research assets temporary unless their fixture provenance is checked.
