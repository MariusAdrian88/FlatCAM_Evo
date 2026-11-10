# PADS board fixtures

Downloaded on 2026-11-10 from KiCad's public QA corpus, **unchanged**. These
are real designs, not generated parser examples. The licenses are specific to
the designs; they are not inferred from KiCad's software license. Keep each
license with its board files when redistributing these fixtures.

## Sources and redistribution

All URLs below are relative to
<https://raw.githubusercontent.com/KiCad/kicad-source-mirror/master/qa/data/pcbnew/plugins/pads/>.
The Git blob IDs and SHA-256 hashes identify the exact downloaded bytes even
if `master` changes. The original directory listings also carry the licenses.

| Local file | Source path | License / purpose |
| --- | --- | --- |
| `ClaySight_MK1.asc` | `ClaySight_MK1/ClaySight_MK1.asc` | MIT, copyright 2024 John Simonis, John LaRocco, Qudsia Tahmina. `ClaySight-LICENSE.txt` is the verbatim `ClaySight_MK1/LICENSE`. Real ASCII 10.0 BASIC, 250-layer-mode export, two routing layers. |
| `TMS1mmX19.asc` | `TMS1mmX19/TMS1mmX19.asc` | BSD-3-Clause, copyright 2017 Yuan Mei. Real ASCII 9.5 BASIC, two routing layers, unrouted connections and bottom-side components. |
| `TMS1mmX19.pcb` | `TMS1mmX19/TMS1mmX19.pcb` | Same BSD-3-Clause design. Native `00 FF`, version `0x2027`. Native import and Gerber/Excellon round-trip fixture. `TMS1mmX19-LICENSE.txt` is the verbatim `TMS1mmX19/LICENSE`. |
| `LCORE_2.asc` | `LCORE_2/LCORE_2.asc` | CERN-OHL-W-2.0. Real ASCII **V2005.0** dialect reference. The unchanged complete board is imported with disclosed pour/teardrop reconstruction and font substitution. Not original-PADS-equivalent CAM output. |
| `LCORE_2.pcb` | `LCORE_2/LCORE_2.pcb` | Same CERN-OHL-W-2.0 design. Native `00 FF`, version `0x2026`. Native import and Gerber/Excellon round-trip fixture. `LCORE_2-LICENSE.txt` is the verbatim `LCORE_2/LICENSE`. Section 3.1 permits conveying verbatim covered source with notices retained. Modified hardware designs and manufactured products have further obligations; consult the license. |

The two `.pcb`/`.asc` pairs are published together under the same board names.
This establishes their source, not proof that both files describe an identical
unchanged design revision. No generated Gerber reference is claimed here.

| Board file | Bytes | Git blob ID | SHA-256 |
| --- | ---: | --- | --- |
| `ClaySight_MK1.asc` | 79802 | `7d9c8c1d9ab223b17a37ef6be6e4d484dc0f9448` | `ca133435555a78280e7a65727017985d5fd61bad1c7ea38f263159c32834414c` |
| `TMS1mmX19.asc` | 651519 | `7cc06dd3ae8962c518b59a4c1bb69c53d921bab7` | `c87d32dbe34f9e94fff5f7298335acc12eee85a8e327a9d838965796674cf360` |
| `TMS1mmX19.pcb` | 911147 | `56cc3347b3ff0b99582193aceda46e4fb2c6290d` | `76d064c8e1615a268a54b43c6c837e6006bacb31d712a964130d684243c08e9e` |
| `LCORE_2.asc` | 158111 | `19c44a96bd991438042c2beebd22bb2f7e5331ab` | `dc48cfc7c0b0bb369a24b20f90028548f629b74a45e6f61c1e03b6d7fcb322d2` |
| `LCORE_2.pcb` | 253744 | `c2962ad1c6356e4fe9fd6a78efd0c012821c312f` | `a6d96528330bcaed50a7914aef0b95aae2573eae2b12d663ace93391ec15f807` |

## ASCII support and checks

`ParsePADS.py` is an independent MIT implementation from the vendor's
[PADS 9.4 ASCII Format Specification](https://www.freecalypso.org/pub/CAD/PADS/pdfdocs/Ppcb_ASCII.pdf),
not a translation of a GPL importer. The implementation accepts POWERPCB/LAYOUT
9.4 through 10.0 and V2005.0 headers with BASIC, MILS, METRIC/MM and INCHES/INCH units.
Versions 9.5 and 10.0 are exercised by the real files; the documented 9.4 decal
syntax and other units are covered by explicit in-memory test records.

V2005.0 is an older dialect, not a version newer than 10.0. LCORE's embedded
record descriptions and values establish three differences: graphics have
`kind count width layer` (no later linestyle column), PARTTYPE has a units
column before type/gate counts, and square/rectangular finger pads have no
later corner-radius column before the drill. Treating that drill as a radius
loses holes or rejects valid pads. BASIC remains 1/1,500,000 mm even for decals
marked `I`. Named silkscreen layers override numeric defaults (LCORE uses 22/25).
The public [KiCad format documentation](https://dev-docs.kicad.org/en/import-formats/pads/index.html)
independently describes BASIC scaling, alternate decals, layer naming and the
fact that POUR records are zone definitions, not saved fabrication polygons.
Its conflicting special pad-stack summary isn't used here; the vendor's
mounted/inner/opposite stack convention remains authoritative.

Supported manufacturing subset:

- Cartesian millimetre output, 1--32 routing layers; named technical layers,
  including the explicit 250-layer-mode mask/paste layers in ClaySight.
- Embedded decals and part types, alternate/modified decal selection, top and
  mirrored bottom placements. The saved TMS JM3.54 endpoint establishes that
  the placement rotates before mirroring in the board frame.
- Round/square/rectangular/oval finger pads, offsets, rounded/chamfered corners,
  per-layer stack overrides; explicit plated/unplated round drills and slots.
  Stack codes follow the vendor specification: **-2 mounted side, -1 inner,
  0 opposite side** (not the conflicting mapping in some secondary summaries).
- Straight and circular-arc routes, named vias and partial drill spans.
  Layer-0 airwires do not become copper. Repeated shared route/via records do
  not duplicate drilling.
- Board profiles/cutouts, line/circle/arc graphics, filled copper and associated
  voids. Tag groups keep their void ownership separate.

Unsupported copper fails the import rather than publishing a partial board:
negative/implicit planes without explicit pour boundaries, saved HATOUT caches,
thermal/antipad-only conductive stacks, odd/annular pads, unsupported copper fonts,
non-default teardrop styles, jumpers/reuse, unsupported sections and malformed
records. Unsupported non-copper fonts are reported as omitted. Attribute labels
(`Ref.Des.` and `Part Type`, per their visibility field) are drawn with substitute
glyphs: labels of the top/bottom copper layer go to the silkscreen layer of that
side, labels of inner copper layers or of layer 0 are skipped with a warning, so a
label never becomes copper. Attributes without a value in the file are skipped. Only
explicit mask/paste shapes are imported; no inherited CAM openings are guessed.
Unassociated pour cutouts are constraints, not holes to subtract from pads or
tracks. Romansim free text is rendered on its saved layer with an explicit substitute-font warning.

ClaySight checks include the 101.6 x 104.14 mm profile, eight profile cutouts,
124 explicit round drills and 16 saved short slots, terminal positions, routed
copper, and explicit mask layers. The import integration test checks selection,
inch-project conversion, actual Gerber export/reparse, and recent-file kind.

LCORE coverage explicitly separates a **test-only extract** of unchanged PCB,
LINES, VIA, PARTDECAL, PARTTYPE and PART records from the complete board. The
extract checks U2's 0.8 x 0.35 mm top pad, mirrored U1 pin 2 against a saved ROUTE
endpoint, two 2.0 mm and two 1.6 mm NPTH holes, the curved outline against its
saved circle box, and explicit layer-25 copper. The full unchanged-board import/export
test checks both copper layers in inches and real Gerber and Excellon export/reparse.
Separate V2005 records check named
silk layers, finger-pad drilling/slots, route arcs/vias and local copper voids.
The downloaded fixture bytes and hashes above remain unchanged.

### What KiCad actually does (2026-11-10)

Source inspection is pinned to **`4b753b460125b7f8fe90f19877d16dd04d913108`**.
These are implementation findings, not claims that KiCad reproduces PADS CAM:

- [PADS zone loading, `pcb_io_pads.cpp:1834--1983`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pcb_io_pads.cpp#L1834-L1983)
  creates editable zones from POUROUT; it does **not** run the filler. HATOUT
  polygons are inflated by half their width and matching VOIDOUT regions are
  subtracted to populate saved fill. LCORE has no such saved fill.
- [Pour settings, `pads_pcb_converter.cpp:673--705`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pads_pcb_converter.cpp#L673-L705)
  sets the net, reverses priority ordering, maps width to minimum thickness,
  uses STMINCLEAR/THERLINEWID for thermal gap/spoke width, and defaults to solid
  pad connection. RT/ST stack rows set pad-local thermal overrides
  ([`pcb_io_pads.cpp:853--880`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pcb_io_pads.cpp#L853-L880)).
  DEFAULTCLEAR maps to netclass clearance, not zone-local clearance
  ([lines 2256--2292](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pcb_io_pads.cpp#L2256-L2292)).
  The importer doesn't reproduce every THERFLAGS/PLANEFLAGS switch.
- [Generic filler, `zone_filler.cpp:3110--3513`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/zone_filler.cpp#L3110-L3513)
  applies thermal knockouts, electrical/keepout clearances, spokes and
  minimum-width pruning. The higher-level Fill operation removes islands.
  This is a distinct geometric operation from importing editable boundaries.
- [Route parser, `pads_parser.cpp:2088--2145,2286--2290`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pads_parser.cpp#L2088-L2145)
  stores P/N teardrop data. [ASCII track conversion, lines 1382--1542](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pcb_io_pads.cpp#L1382-L1542)
  never consumes that vector. The separate [generic teardrop manager](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/teardrop/teardrop.cpp#L107-L146)
  creates net-owned polygon zones; the PADS loading path doesn't call it.
  Thus KiCad isn't an oracle for the original PADS Default 90/90 geometry.
- [Text conversion, `pads_pcb_converter.cpp:438--501`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pads_pcb_converter.cpp#L438-L501)
  creates PCB_TEXT without selecting the PADS font. It compensates size/anchor,
  applies justification and mirroring, but doesn't load Romansim strokes.
  [Default font resolution](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/common/font/font.cpp#L133-L150)
  uses KiCad's [newstroke font](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/common/font/stroke_font.cpp#L61-L74).
  Copper attribute labels may also be redirected to silkscreen, and values hidden.
- [LCORE QA, `test_pads_import.cpp:108--198,338--341`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/qa/tests/pcbnew/pcb_io/pads/test_pads_import.cpp#L108-L198)
  checks structural validity, with warnings for known parser limitations. It
  doesn't compare LCORE fabrication polygons. The pinned LCORE directory has
  ASC, PCB and LICENSE only; no matched `.kicad_pcb` or Gerber golden output.

**Installed-runtime correction:** KiCad **10.0.8** was found at
`C:\Program Files\KiCad\10.0\bin`, despite not being on PATH. Its bundled Python
imports pcbnew and ZONE_FILLER; the project's `.venv` doesn't. No installation
or dependency was added. This binary isn't assumed to match the pinned source.
Running `kicad-cli pcb import --format pads` on unchanged LCORE succeeded, with
30 footprints, 245 track/via objects and two zones, **both on F.Cu**. There was
no circular B.Cu pour. Both zones initially had zero fill polygons. Running
ZONE_FILLER generated 111.394849 mm² for the top GND zone and 2.555655 mm² for
an unnamed top zone, but couldn't restore the absent bottom boundary. There
were zero teardrop zones and zero enabled pad teardrops. The copper text used
the default font (for height 2.54 mm: size 1.6256 x 1.752599 mm, stroke 0.254 mm).
Actual top/bottom Gerbers and separate PTH/NPTH drills were exported in the
approved temporary directory. Successful export isn't evidence of complete
PADS copper. The newer pinned parser explicitly handles circular pour pieces
([`pads_parser.cpp:1311--1353`](https://github.com/KiCad/kicad-source-mirror/blob/4b753b460125b7f8fe90f19877d16dd04d913108/pcbnew/pcb_io/pads/pads_parser.cpp#L1311-L1353)).

### FlatCAM reconstruction policy and limitations

The request to investigate KiCad and implement PADS features isn't approval of
approximations or font substitution. The prior description of this policy as
user-approved was incorrect. The implementation reconstructs artwork and reports
its substitutions before import acceptance; it doesn't establish original-PADS
equivalence. No GPL implementation or unlicensed Romansim font bytes were copied.
This is an independent Shapely implementation:

- POUROUT polygon/arc/circle boundaries are filled on their actual layers.
  Lower priority numbers fill first. Pads, vias and tracks retain route-derived
  net ownership, including named terminals. Unknown-net artwork gets clearance.
  DEFAULTCLEAR/MINCLEAR and a conservative maximum of declared COPPER_TO_*
  rules form the clearance envelope; absent rules use 0.2 mm. This deliberately
  doesn't reproduce pair-specific rule precedence.
- Board edges/cutouts, keepouts, unassociated pour voids, foreign copper and
  drill holes constrain **new fill only**. NPTH holes include copper clearance;
  blind/buried holes only constrain layers in their drill span. Marked same-net thermal nodes use
  four orthogonal spokes at THERLINEWID (0.3 mm fallback). Minimum-width opening
  prunes slivers. Islands without a same-net copper anchor are removed. An empty
  result or a pour without a net anchor is rejected, not silently published.
  Vendor flood flags are replaced by the disclosed policy, not guessed.
- Default P/N teardrops become straight local pad/track tapers clipped to
  foreign-net clearance. Short collinear segments can be spanned; bends/layer
  changes can't. **90/90 isn't used as a percentage.** Curved/line/auto-adjust
  modifiers remain rejected rather than being misrepresented as exact defaults.
- Romansim free text uses the existing MIT FlatCAM stroke glyphs, including
  corresponding lowercase glyphs already used by the KiCad and ODB readers.
  Letter case is preserved; lowercase is no longer converted to small caps. Height/spacing and
  shapes are approximate. Saved layer, mirror, rotation and justification are
  applied; decal free text is placed with each component. Missing glyphs and
  unsupported copper fonts fail rather than disappearing. This isn't KiCad
  newstroke, PADS Romansim, or a licensed copy of CAM350's `romansim.fnt`.
  Only `Regular <Romansim Stroke Font>` free text is substituted. Other copper
  font/style names are rejected; other non-copper fonts are omitted with
  warnings. Attribute labels use the same substitute glyphs. Characters absent from the combined glyph tables
  (for example Cyrillic Ж) are rejected, never uppercased or silently dropped.
  Text height/stroke must be positive and finite. Supported justification is
  LEFT/CENTER/RIGHT and UP/CENTER/DOWN.

The unchanged LCORE SHA-256 above was rechecked. At 128 circle steps the ASCII
result has a **bottom GND fill of 86.882929 mm²** and **top GND fill of
130.916153 mm²**, plus saved pads/routes and substituted lettering; drills are
16 PTH and four NPTH. Those areas document this reconstruction, not a PADS
golden comparison. The worker test imports the full original file, selects
both copper layers and drill groups, exports real Gerbers in inches and
Excellon in millimetres, then reparses geometry and hole counts/diameters.
All approximation warnings travel through `parsed.warnings` to the visible
warning label in the layer/drill selection dialog, before acceptance. The full
LCORE import test checks that this dialog displays pour reconstruction, approximate
teardrops and substitute glyph shapes/metrics with preserved case. There isn't
a separate persistent PADS import-report file.

**Inspect reconstructed clearances/connectivity before manufacturing.** No
matched PADS CAM comparison or electrical DRC of the reconstructed result is
claimed. Exact original lettering and proprietary flood/Default-teardrop
behavior would still require original artwork or independent calibration.

## Native binary import: 0x2026 and 0x2027

`ParsePADSBinary.PADSBinaryReader` independently reads **0x2026 and 0x2027**
without another application or companion ASC. Decoded records are normalized
in memory into the existing PADS geometry pipeline, so pad placement, graphics,
pour filling and unit handling use the same implementation as ASCII import.
The PADS chooser/menu accepts `.asc` and `.pcb`; recent files retain kind `pads`.
Both original binary fixtures now pass actual Gerber and Excellon export/reparse.
This supports a tested manufacturing subset, not arbitrary SDB versions or
original-PADS-equivalent CAM. Substitutions are visible before import acceptance.

The new source investigation is pinned to KiCad commit
**`d29d059fcde12633ef6821638cd54e6d874dc18a`**. Relevant files under
[`pcbnew/pcb_io/pads/`](https://github.com/KiCad/kicad-source-mirror/tree/d29d059fcde12633ef6821638cd54e6d874dc18a/pcbnew/pcb_io/pads)
are `pads_sdb.cpp`, `pads_sdb.h`, `pads_binary_parser.cpp`, and `pads_binary.ksy`.
These are GPL-2.0-or-later references for field layouts, not MIT code to paste
into FlatCAM. No upstream implementation or generated parser is bundled.
This source contains substantive native readers, unlike TRACE's header-only
recognition of these versions.

### Decoded and checked against unchanged native bytes

- Directory slot count at byte 26 is **75**, not 74, for both local fixtures.
  Directory views start at byte 10; the physical stream starts at `6 + 75*16`.
  Flat controllers, paged allocation descriptors, live route-rule state words,
  and post-layer `PowerSYS` framing are accounted for. Directory counts aren't
  all byte sizes. The final 42-byte tail is a 38-byte GUID plus a 4-byte back-pointer.
- Board origin is computed from the stored directory count and section-2
  view-state count. BASIC is 1/1,500,000 mm; placed positions subtract that origin.
- Sections 4/5 carry default/per-layer padstack dimensions. Section 14 decal
  descriptors resolve section-15 terminals and section-16 `(pin, padstack)` pairs.
  Section-22 placements resolve through section-17 alternate decal indices or
  their explicit override. Rotations are stored in 1/1,800,000 degree units;
  bottom-side local points rotate before board-X mirroring.
- Section 69 supplies native layer names and functions. Both boards name their
  copper layers `Top`/`Bottom`; defaults from the ASCII parser aren't substituted.
  Section 23 supplies native net names and anchors. Section-24 terminal edges
  propagate ownership through connected pin components; named nets take precedence
  over `$$$` aliases. Invalid endpoints and conflicting named anchors are rejected.
- Section-60 via junctions resolve their own decal/padstack. Co-located equal
  via records are deduplicated; conflicting stacks fail. The low-level diagnostic
  Plating is the stored flag: section 4, first word of the record after the
  padstack, bit 0x2 set = non-plated (bit 0x8 of that word is the slot carrier).
  150 of 150 stack references of both fixtures agree with the ASCII P/N
  declarations. KiCad infers plating from dimensions; that rule is not used.
- All used extents, counts and references are bounded before iteration/use.
  Tests corrupt headers, footer, directory count, extents, placement references,
  route cells/layers, drawing shapes, pour arcs and drill spans. The asynchronous
  import path reports malformed native input and releases the request without
  publishing objects; truncated data doesn't crash the worker.

| Native fixture | Placements | Decals | Net names | Decoded hole locations |
| --- | ---: | ---: | ---: | --- |
| `LCORE_2.pcb` | 30 | 12 | 15 | Four component holes (2 x 2.0 mm, 2 x 1.6 mm) and 16 x 0.2 mm via holes |
| `TMS1mmX19.pcb` | 373 | 33 | 347 | 83 round component holes and 3 slots; no via holes in the decoded junctions |

Native LCORE H1 is `(5.17633, -19.31867)` mm. Via row 74 at byte
`114415 + 74*64` stores raw `(-6265000, -228305500)`. Subtracting the board
origin `(-2290000, -213230500)` gives `(-2.65, -10.05)` mm. Native C4 is
`(1.4, -14.2)` mm, 90 degrees, mirrored. Native TMS JM3 is
`(97.989644, 88.9)` mm, 270 degrees, mirrored. These checks don't establish
whole-board equivalence of either ASC/PCB pair. Additional geometry comparisons
now establish matching saved pads, tracks and curved outlines (see below).
Both native fixture files remain unchanged.

### External-runtime experiment

KiCad **10.0.8** was tested on **`LCORE_2.pcb`**, not its ASCII companion:

```powershell
& 'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe' pcb import --format pads --report-format json --report-file <temp-report.json> --output <temp-board.kicad_pcb> tests/fixtures/pads/LCORE_2.pcb
```

It printed success and wrote a board. Loading that output with its bundled
`bin/python.exe`/pcbnew found **zero footprints, zero track/via objects, zero
zones, and zero drawings**. This installed build isn't a binary conversion
oracle. No external runtime dependency or wrapper was added.

### Conversion and independent checks

- Sections 25/26/27/29/61 resolve serialized route handles to actual layers.
  Section 49 associates junctions with nets; nearest-junction graph traversal
  assigns each route object its net, rejecting absent or conflicting ownership.
  Sections 62/63/64 decode the rotated object ring, layer permutation and cells.
  Zero-width objects are airwires, not copper. All five TMS route descriptors are
  zero-width; its import correctly has no routed tracks. LCORE's positive-width
  descriptors reproduce its saved 0.3/0.5/0.6 mm routes.
- Sections 10/11/12/13 provide drawing/decal owner runs, pieces, corners and arc
  boxes. Curves, circles, board outlines, placed decal graphics, copper fills,
  associated voids, group tags and pour keepouts enter the existing geometry reader.
  Unknown drawing owners/shapes fail rather than disappearing.
- Sections 8/9 supply free text and its string pool. Saved location, rotation,
  layer, mirroring and justification (presentation word bits 24--31) are
  retained; the font face comes from section 73. FlatCAM stroke lettering is
  substituted with the existing warning; original fonts/metrics aren't reproduced.
  Decal-owned text (association tag 0x0a; 11 TMS records) is emitted with its decal.
- Attribute labels are section-8 chains: decal defaults from the section-14 head
  (+80), placement overrides from the section-22 head (+96). All 17 + 58 LCORE and
  72 + 766 TMS labels equal the ASCII records; the native and ASCII label
  geometry is equal on both silkscreen layers. Part types use the real
  section-17 names. Unproven values are rejected: attribute kinds other than
  `Ref.Des.`/`Part Type`, RIGHT justification. A clear visibility bit gives `NONE`.
- Sections 52/53/54 supply each pour's own anchor, net, layer and polygon/circle
  boundary. Both LCORE GND pours are reconstructed. The clearance rules are the
  39 named rows of the single global section-41 record (slot order = ASCII
  declaration order), not the board-setup LINEWIDTH setting; layer-specific rule
  records are rejected. All TMS rows equal its `.asc`. **LCORE's `.pcb` holds
  225000 (0.15 mm) in the 28 distance rows, its `.asc` 450000 (0.3 mm): the two
  files are different save states.** The `.pcb` is imported with its own values.
  The board-parameter word at P+1396 is VIAPSHAPE, not a clearance.
- Global THERLINEWID/THERSMDWID/THERFLAGS/STMINCLEAR/STMINSPOKES come from the
  section-3 parameter block; only THERLINEWID is used by the fill. Pad RT/ST/RA
  rows are decoded for 0x2027 (10 TMS rows equal the `.asc`); SA rows and such
  rows in 0x2026 are rejected as unproven. They don't change the artwork.
- Routes are emitted as the pin-pair paths of the section-24 edges (section-49
  junction sets, breadth-first main path plus spur walk): 55 of 55 LCORE paths.
  A route end at a section-60 pin or via junction carries THERMAL. Teardrop
  presence is the section-62 style word (0x200/0x400 = none at the first/last
  end) with the packed 90/90 word of section 3; unequal width/length is rejected
  because the field order is unproven. The tapers are the same approximate
  reconstruction as for ASCII. Proven on LCORE (0x2026) only; TMS has no routed
  copper. A copper object that is in no path rejects the file.
- Against the ASCII import of LCORE with the rule values of the binary patched in
  memory: routed copper and teardrops differ by less than 1e-6 mm²; with pours the
  difference is below 0.015 / 0.002 mm² (layer 1 / 2), slivers of the pour opening.
  TMS full copper equals its `.asc` import.
- LCORE produces 16 PTH vias and four NPTH holes. TMS produces 61 round PTH holes,
  three PTH slots and 22 round NPTH holes. TMS's three SMA slots use a 0.889 mm
  cutter with 2.8 mm overall length; orientations are 90/90/0 degrees.
- Geometry comparisons against **both** independent ASCII exports establish
  matching saved pad copper (including net-owned pads), routed copper and curved
  board outlines to less than 1e-7 mm² symmetric difference. These establish
  correspondence for those records, not all design rules or generated artwork.
- The integration tests open both `.pcb` fixtures through the file chooser and
  worker, display the disclosures, select both copper layers and PTH/NPTH groups,
  create objects in an inch project and export/reparse real Gerber and Excellon.
  They verify copper, reconstructed fill retention, drill diameters/positions/counts,
  slot diameters/endpoints, and the `pads` recent-file event.

Unsupported native features fail explicitly when encountered: other SDB versions,
blind/buried drill spans, unknown signal pad shapes, jumper/special route objects,
pour arcs, unknown drawing types, layer-specific clearance rules and SA pad rows.
Original-PADS flood policy, thermal flags, teardrop taper shape and lettering
aren't claimed. No original vendor CAM golden or DRC
comparison is available; inspect reconstructed output before manufacturing.

The current [padsts compatibility table](https://github.com/tscircuit/padsts/blob/main/docs/compatibility.md)
was checked again: pads/holes remain undecoded, routes/vias debug-only, and
strict native manufacturing conversion is expected to fail. Its
[section map](https://github.com/tscircuit/padsts/blob/main/docs/native-section-map.md)
is explicitly observational and disagrees with the stored directory count and
some sections here; it isn't used as an authoritative layout.

The production path doesn't depend on KiCad, padsts, or a nearby `.asc` file.
The imported geometry and round-trip checks come from the actual binary bytes.
