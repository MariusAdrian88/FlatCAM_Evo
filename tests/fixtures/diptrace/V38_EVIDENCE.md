# Native DipTrace v38 validation

## Source and redistribution

The genuine, unmodified input boards were downloaded on 2026-10-27 from
`https://raw.githubusercontent.com/SorokinRed/DipTrace/master/Examples/<name>.dip`.
No external parser implementation was used. The repository's root `LICENSE`,
`LICENSE.md`, `LICENSE.txt`, and `COPYING` URLs returned 404. This doesn't establish
permission to redistribute the boards, so **none of these boards is vendored**.
The independent record writers in `test_pcbdoc_parser.py` contain serialized
format facts, not copied board designs.

| Board | SHA-256 | Font/cache frame offset |
| --- | --- | ---: |
| PCB_1 | bb5074396aea6a1d8f7d787dd4f53602fb55c49efb597b2284c0fb41b335593a | 41284 |
| PCB_5 | 8eb940fd389b6b05035483877e49d2e3fb330cad110ac6286ebb4e0665c9e6e9 | 259784 |
| PCB_6 | e1810cd8377abf70feb02e820067b96e823763b8cda2060105fade44c7d00413 | 521866 |
| arm | bd40149edecabf21bb6c73714df64bd9af1c00920c938f8a652e3d3cc8b945c0 | 649814 |
| BGA_autorouter | 663b2325872f37569facfabf7c551671af5d9d7c0a92bf4605b600e036a3a007 | 1145789 |
| example_routed | d48e1fa05c4cb842766636e3b8b1183a615034b9da9d4f409c172cdb44e2ebb3 | 521907 |

## Layout and geometry evidence

All six files have native version 38, two-byte UTF-16BE string lengths, the
existing legacy placement frame, and counted pad/route/pour records. No input
version bytes were changed for validation. The parser uses the same bounded
record decoding as the adjacent legacy versions, with these additional facts:

- PCB_1 has three copper layers (0, 1, 2) but retains an unused Default via style
  ending at deleted layer 3. Its live style uses existing layers. Style indices
  must be retained; a live reference to an absent layer is an error.
- PCB_5 U1 and PCB_6/example_routed Q6 have placement metadata state 10. Their
  complete counted metadata ends exactly at the embedded component header.
- PCB_1 custom octagonal pad contours repeat the identical traversal three
  times. Only exact repeated cycles are collapsed. A board-graphic or outline
  ring that only touches or retraces itself (no crossing adds a vertex) is
  filled as saved; a crossing contour still fails.
- After pad vertices, the native frame contains a boolean and three **counted**
  lists: hidden rings, pad layers, and custom thermals. The remaining common
  mask/paste frame is 14 bytes. Treating this as an unconditional 24-byte tail
  loses alignment at the first nonempty list.
- BGA_autorouter StaticVia pads include layer IDs 3, 4, 5, 6, 7. Copper and blind
  drill groups use that span, not all eight copper layers. Hidden rings suppress
  the corresponding pad copper without suppressing its hole.
- PCB_6 and example_routed VIA14/VIA18 contain custom thermal records
  `(layer=-1, connect=0, spoke=4, width=9910/30000 mm)`. Settings and the custom
  raw pre-polygon pad flag are retained in `pad_plane_settings`. Saved pour copper
  already includes the resolved spokes; it isn't regenerated from defaults.

The official XML PCB specification provides semantic corroboration, not binary
offsets: `https://diptrace.com/books/DipTraceXML_Pcb_En.pdf`, sections 4.6.1.1
(PlanePad/NetId/PlaneRing), 4.18.1.15.2–4 (HideRingLay/BlindLay/CustomSpokes).

Independently reading the native cache records gave these plane fill counts:

| Board | Saved zones | Plane cache evidence |
| --- | ---: | --- |
| PCB_1 | 3 | Layer 1: 427 scan segments, 25 contours, 10 spokes |
| PCB_5 | 2 | Layers 1/2: 1651/1690 scans, 78/54 spokes |
| PCB_6 | 5 | Layer 1: 6027 scans, 242 spokes; layer 2 has four split zones |
| arm | 0 | Plane pads/traces only; no board-wide plane fill is invented |
| BGA_autorouter | 0 | Plane pads/traces only; blind-pad spans are preserved |
| example_routed | 5 | All caches empty: boundaries are unchecked documentation, not copper |

PCB_1's first plane scan at y=28.392 mm stops at x=-45.542 and resumes at
x=-43.352667. The midpoint of this clearance remains empty. Its first two
0.330333-mm diagonal spokes intersect at (-24.13, -21.59). The diagonal probe
offset (0.8, 0.8) is copper; the axial offsets (0.8, 0) and (0, 0.8) are empty.
These independently decoded physical probes are tested through `parse_file`,
not through a header-mutated fixture or an alternate runtime importer.

## Reproduction

Put the original boards in a local directory (the local corpus is
`D:\1.Development\DipTrace_corpus`, sha256 in its README) and set:

```powershell
$env:DIPTRACE_V38_EXAMPLES='D:\1.Development\DipTrace_corpus'
.venv\Scripts\python.exe -m pytest tests/test_pcbdoc_parser.py tests/test_pcbdoc_import.py -q -n 4 -p no:cacheprovider
```

Without that opt-in directory, the corpus tests skip (each board subtest also
skips when its file is absent). Independent record geometry tests and all
fixture-based tests still run normally.

## Limits

This validates native saved copper, not a new pour engine or arbitrary negative
polarity. No independent Gerber export was available for a whole-board XOR
of these six v38 boards (CAM checks of other versions are listed in
`VERSION_COMPATIBILITY.md`). In particular, absence of saved fill isn't reconstructed from net
assignments or thermal overrides. Unknown pad plane flags, invalid layer IDs,
noncontiguous blind spans, and invalid custom thermal fields fail explicitly.
Legacy marking and inherited mask/paste limitations remain reported by the
existing warnings. These changes don't claim to resolve those separate features.
