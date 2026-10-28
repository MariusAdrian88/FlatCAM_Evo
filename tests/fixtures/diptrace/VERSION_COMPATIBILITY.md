# Native DipTrace version evidence

The parser is independently written. Binary framing was inspected directly in
the genuine files below. No external importer's source, algorithms or expected
geometry were copied or translated. Existing FlatCAM geometry helpers perform
the rendering. Official field semantics were checked against:

- https://diptrace.com/support/tutorials/xml_specs_PCB_html/
  (coordinate frames, copper-layer ids, via styles, Shape/Points).
- https://diptrace.com/books/xml_specs/DipTrace_PattEdit_XML_Specification.md
  (pad shapes, mounting holes, component-local geometry and mask/paste states).

These XML documents describe semantics, **not the native binary layout**.
Native versions are not DipTrace product-version numbers. Acceptance is limited
to decoded record kinds, not a promise that every board of a version is supported.

## Redistributed, unmodified genuine fixtures

Downloaded from the data-only directory
`qa/data/pcbnew/plugins/diptrace/` of
https://github.com/KiCad/kicad-source-mirror at commit
`fc2c41836e853a169d918511ee00de2adf65d618`.
The directory's README records third-party provenance and licenses.

| File | Native version | Author / license | SHA-256 |
| --- | --- | --- | --- |
| `rotate4.dip` | 54 | KiCad Developers, GPL-3.0; `LICENSE_GPL-3.0.txt` | `6b5401de90b99c162391b595d5f386f6fe0209f4b9960a2a5dc25adf5fe47997` |
| `rotate.dip` | 60 | KiCad Developers, GPL-3.0; `LICENSE_GPL-3.0.txt` | `eee2151cd949d71bb009ade2cd31ebb98a8aa2143c943756947f5e0205e49d1e` |
| `re-arm_pub.dip` | 41 | Panucatt Devices, CERN OHL v1.2; `LICENSE_CERN-OHL-1.2.txt` | `884732eade92a4b1db3510fed4333250342430d60c477fe6ce70583f5a7a34ff` |

The Re-ARM board itself contains the notice "Licensed under CERN OHL" and its
original `http://www.ohwr.org/projects/cernohl` reference. Its native board text
and attribution are unchanged. The CERN license text is from SPDX's
`license-list-data/json/details/CERN-OHL-1.2.json` (`licenseText`). The fixture
licenses apply to their respective data, not the independently written parser.

Tests check actual pad silhouettes and drilled centers, including the two
rotation boards' different arbitrary angles. The v41 test checks all four copper
layers and the rotated JD1/JP1 drills plus a standalone mechanical hole. Neither
board needs Gerber, Excellon or other companions at runtime.

## Other genuine files inspected locally, not redistributed

No license was established for these downloaded samples. Their geometry was
checked locally; the permanent legacy regression uses an independent small
record writer rather than redistributing their designs or changing headers.

| File | Version | Result | SHA-256 |
| --- | --- | --- | --- |
| `project4.dip` | 37 | 27 components, 117 PTH drills; outline bounds `(9.4302,110.9169667,69.5955,180.1822667)` mm | `265ca535523fee34700e76aab9b178d6cdc3c713a23a0516222e3376b9f63dbe` |
| `iot.dip` | 39 | 58 components, 34 PTH + 2 NPTH drills; outline `(0,0,71.12,35.56)` mm | `f00fc87100e358a9a7ec28e48e480e9f302076299f79689a0fef15f7095c452a` |
| `156bus_narrow.dip` | 41 | Imports; its edited nine-point component Rectangle keeps the saved contour | `68768a7fbbb30ff4a7e706f1dfae8aea8ee32fed4b219ef81b0d1328f92262ca` |
| `z80_board.dip` | 45 | 57 components, 892 PTH + 4 NPTH drills; outline `(-127.73,-48.26,22.27,71.74)` mm | `907622ada7e0a637f245baa7337134a7d1cd36c7777961f010fb188b07856fce` |
| `logic_probe.dip` | 46 | 113 components, 129 PTH drills; outline `(-73.66,-19.05,60.96,19.05)` mm | `3b87aa82b868ed60347312cb8d3ac9362d8594f5e3403e2a4d33d39d3209ef8d` |

Original sources identified by the data-directory provenance: project4 is
Sadiaahmed97/PCB-design- (`tatsad project 4.dip`); 156bus is vaeinoe/moraydular;
z80 is lightemittingresistor/z80-board-hardware; logic-probe is
ElectronicToast/ee13-logic-probe. The earlier download's upstream for `iot.dip`
wasn't established in this session.

## Layout findings and limitations

- **37:** Strings use biased three-byte byte counts and single-byte text,
  including pad names, user fields and sheet settings. Only ASCII is verified;
  a non-ASCII legacy code page produces an explicit unsupported error. Numeric
  coordinates, outline point framing, pad tables and routed nodes matched the
  existing legacy layout in the genuine sample. Its saved pour count is zero.
- **39/40/41/45/46:** Strings use two-byte character counts and UTF-16BE. The
  bounded preceding component metadata retains quarter-turn orientation. The
  legacy pad, route, board-graphics and sheet frames decoded without substituting
  version headers. Saved pour counts are zero for the v39 IoT board, six for
  v41 Re-ARM, two for v45 Z80 and two for v46 logic-probe. v45/v46 read the
  global mask/paste offset frame; v37..v44 openings use explicit pad offsets or
  the supplied source defaults (reported when unresolved). Automatic markings
  remain omitted with a warning; explicit saved graphics are retained.
- **44:** One specimen, SorokinRed `ESP_training_board.dip` (c7bb16aa), decodes
  on the v41 path: 15 components, nets 21930..30677, routing end 30532. No CAM.
- **41:** Unused mechanical-hole default records can retain nonzero coordinates.
  Unused trailing pad records retain old next-pad ids (Re-ARM JD1 and JP1 both
  retain 7 despite different live-pad counts). Live-pad identities remain checked.
  Surface copper uses the physical first/last copper layer, not hardcoded id 1.
- **46:** The logic-probe has an outline Obround with equal X endpoints at
  `(-49.53,16.51)` and `(-49.53,13.97)` mm. Its zero-width centerline still has
  the saved `1/30` mm stroke; it isn't an invalid filled oval or an invented pad.
- **54/60:** The genuine four-capacitor boards retain the bounded real-angle
  placement and graphics extensions, mask defaults and primary geometry framing.
  The independent saves differ in angles, positions and section offsets. v60
  must not fall back to legacy quarter-turn geometry. This verifies the decoded
  subset, not every newer v60 record kind.
- **50/53/58:** read with the v54/v60 placement, marking, pad-mode and extension
  readers. v53 parsed on the legacy v46 path is wrong against its CAM (Top extra
  2.17 mm², TopMask extra 3.52 mm²), so the modern path is required.
- **49:** official BGA_autorouter, Differential_Pairs and BeagleBone_Black decode
  on the modern path (placement frames, pad-mode table and saved-teardrop counts
  agree on all three; forcing v46 boards onto it fails). BeagleBone's Default via
  style has an unset last layer (-1, byte 599) and U12 has five layerless arcs
  (1011749..1012085) that duplicate its pin-1 circle: not drawn, one warning.
- **Mask/paste frame:** three 0/1 display flags, then two zero points, follow the
  two global offsets: `000001` on most boards, `000101` PCB_2/PCB_4 v54,
  `000100` PCB_6 v54 and v49.
- **Route points:** the two three-byte fields after the drill are the via span
  (first/last physical copper index; Banana Pi v54 `(0,5)/(0,1)/(1,4)`, SJTwo
  `(0,3)`), `(0,0)` without a via. Every route point keeps copied via defaults, so
  a via exists only at a layer change or a joined end where two layers meet. CAM
  drills: Habitat V4-5ch 339/339, LazyRolls nema17 (da74c017) 52/52, SJTwo 174
  vias exact. A custom route via without a style uses its saved span.
- Unknown extended pad states, negative planes, crossing graphic contours and
  unsupported record layouts fail or warn; they aren't silently approximated.
  A contour that touches or retraces itself without crossing is filled as saved.

## Accepted versions (2026-10-28)

Corpus `D:\1.Development\DipTrace_corpus`: 68 of 69 boards import (root and
sidecars). Only `tango_1_imported.dip` fails: signature `0b 'DTBOARD2.21'`
(DipTrace 2.2 era), reported as older than supported. specimens_teardrops: 23 of
29; MakerPnP x3, two empty template boards (no components) and tango fail.

| Version | Path | Evidence |
| --- | --- | --- |
| 37, 38, 39, 40, 41 | legacy | parse; v40 DAP42 and v41 Re-ARM regression geometry |
| 44 | legacy (v41) | parse only, 1 board, no CAM |
| 45 | legacy + offset frame | CAM: SJTwo Rev.1.E.2, 7 layers 0 missing/extra at 0.5 µm, drills exact |
| 46 | legacy + offset frame | parse; logic-probe, RA5-512K, Habitat_V1, Motus |
| 49 | modern | Differential_Pairs = its v41 save: 296 drills, 108 nets, inner planes; BGA_autorouter, BeagleBone_Black parse, copper not provable |
| 50 | modern | parse (3 interstellar boards); LazyRolls nema17 CAM (da74c017) drills 52/52 |
| 53 | modern | CAM: valves-manipulator jlcpcb, 128 steps. Top 1366.8253/1366.7534, Bottom 3583.3862/3583.3847, TopMask 713.3536/713.2524, BottomMask 575.1245/575.1234, TopPaste 109.1403/109.0577 mm²; 0 missing/extra at 10 µm; drills 85/85 |
| 54 | modern | CAM: Habitat V4-5ch (below), bluestoothilizer Top/TopMask/TopPaste, drills 28/28; PCB_4 = its v38 save (254 drills); GPSDO, CNC_controller regression |
| 58 | modern | CAM: TouchPad, copper/mask 0 missing/extra at 0.5 µm; drills 12/12 |
| 60 | modern | parse; rotate.dip angles and Continental boards |

Silk is not CAM-equivalent on v53/v58 (text uses the approximate stroke font;
v53 top-side THT part silk appears on CAM BottomSilk, unexplained).

## Imported features and their limits

- D-shaped pads/terminals (style 4); D mask/paste offsets are contour buffers,
  not compared with a native D-opening export.
- Via tenting: an import option (default tented). Native boards don't store it;
  DipTrace takes it from the export's per-layer Vias inclusion (`gsettings.dat`).
- Documentation tables: drawn on Top Assembly only when parameters 0, 1 and 3
  are 0 (Habitat_V3 TopAssembly.gbr). Which byte holds the layer is unproved;
  other values warn and are skipped.
- Saved teardrops (modern path): 496 records in 94 nets on CNC_controller are
  added as copper. Mapping of the record layer id on boards with more than two
  copper layers is unproved; unknown ids warn.
- Legacy sidecar pictures (`<board>.dipb<N>`): pixels darker than 128 (gray)
  become copper/silk boxes, with a warning. The threshold is a choice, not a
  decoded value. Modern raster-only pictures and missing sidecars warn.
- Zero-area filled rectangles/obrounds aren't drawn (one warning); a collapsed
  outline rectangle/obround keeps its line-width stroke.
- A board outline that retraces part of its edge (m2c v1_2/v1_3, 1.27 mm spur)
  becomes its enclosed polygon with a warning; outlines with holes still fail.

Not implemented (dropped by the user): net-class, per-layer and ClassToClass
rules; pour refilling (saved caches are authoritative); per-layer custom pad
stacks (no native schema found in 35,773 pads); meander and differential-pair
editing semantics (saved expanded traces supply the copper).

Unproved: legacy automatic marking positions (not reconstructed), the table
layer byte, the picture threshold, teardrop layer mapping on more than two
copper layers.

Further limits:
- Markings with an unset alignment (`Vert = -1` on a Position anchor: Astable
  R1..R4, bluestoothilizer U2, BGA_autorouter 55 records) are accepted but not
  drawn, with one warning; saved text geometry without a custom font stays
  drawn. bluestoothilizer U2's CAM text fits no Center/Bottom/Top anchor.
- A polygon of one repeated point (Motus v46 @1432863) isn't drawn (one warning).
- Bottom-side placement: Motus CAM has all paste parts on the bottom, the import
  places them on top; bluestoothilizer misses one bottom pad, both bottom paste
  pads and has mirrored bottom-mask pairs and a shifted bottom logo. The side
  encoding isn't found.
- MakerPnP v60 (base/core/stepper1): placement frames with other state numbers
  and an undecoded dimension-display record; not provable, still rejected.
- Banana Pi v54 parses; its route-point via set has no CAM.

## Public corpus findings, 2026-10-28

Additional native boards and CAM exports were obtained online. These designs
aren't redistributed here. The existing topical tests use small mutations of
the already available GPSDO fixture to exercise the newly established framing.

- HabitatControl v4-5ch, native v54: both `Habitat_V4-5ch/layout.dip` and
  `Habitat_V4-5ch/layout_gerberx2.zip` at
  https://github.com/machadoleonardo94/HabitatControl-hardware/tree/f2850affb26705506b007f9fd41cef257eeb1445/ .
  The native rectangular-pad corner value `206612` on a `1.21 x 1.57 mm`
  pad corresponds to the exported `0.25 mm` corner in Gerber `OUTLINE0`.
  The stored value is a fraction of the shorter side, scaled by `1000000`,
  rather than a length divided by `30000`. Several other aperture sizes
  independently confirm the same scale. Terminal corners use this framing too.
- HabitatControl retains 361 component placement extensions in dense component
  order. Its unnamed mounting holes have no reference text in those frames.
  Positional matching is accepted only when the complete frame count, every
  component's dimensions and all available reference identities agree.
  Named reordered frames still resolve by identity.
- HabitatControl's first five board graphics have zero `TextLines`, rather
  than one empty string. The counted zero-line frame is validated against the
  entire primary board-graphics table. The board now imports without bypassing
  artwork or substituting a manufacturing file.
- deck65 v18, native v54: `diptrace/v18/main.dip` at
  https://github.com/and3rson/deck65/tree/dfdab07166e7b9ab6c783e055d7862b633b5a36b/ .
  Its 390 component frames independently confirm dense placement order. J8
  retains `Horz=-1` on an explicitly hidden marking. Such inactive alignment
  fields don't reject the board or restore hidden ink; visible alignment values
  remain validated. The board imports with 96 retained nets.

### Remaining differences in the new CAM reference

Import success isn't full manufacturing equivalence. HabitatControl v4-5ch at
256 steps, after the effective pad-mode table (from 743038) and the tented-via
default: Top Mask `828.6745` vs CAM `828.3074 mm²` (extra 0.2388 at 0.5 µm,
0.0044 at 2 µm); Top Paste `436.5133` vs `436.2215 mm²` (extra 0.190273 /
0.003730); Bottom Mask equal. With the route-point via rule, copper extra at
0.5 µm is `0.202152 mm²` Top (0.003863 at 2 µm) and 0 Bottom (before: 0.471957 /
1.391189); drills 339 = CAM. Nothing is clipped to hide a residual.

The existing SJTwo Rev.1.E.2 comparison still passes the seven checked layers
at `0.0005 mm` positional tolerance and has no missing or extra drill features.
