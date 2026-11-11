# EasyEDA Pro fixtures

Real project of the EasyEDA Pro reader (`appParsers/ParseEasyEDAPro.py`), unchanged, with the Gerber and
Excellon files that EasyEDA Pro wrote at the same moment (client v4.1.78, 2026-10-27 17:08 and 17:09).

| File | Source | Licence |
| --- | --- | --- |
| `Agent-Light-v1-layout.epro2` | <https://github.com/SimpleAstronaut/Agent-Light/blob/020c25e487ddfff3a3ccdfa38775ae60a271e570/hardware/eda/Agent-Light-v1-layout.epro2> | MIT, Copyright (c) 2026 SimpleAstronaut and Agent Light contributors (`Agent-Light-LICENSE.txt`, verbatim) |
| `Agent-Light-RevA-DRAFT-Gerber.zip` | <https://github.com/SimpleAstronaut/Agent-Light/blob/020c25e487ddfff3a3ccdfa38775ae60a271e570/hardware/eda/Agent-Light-RevA-DRAFT-Gerber.zip> | same |

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `Agent-Light-v1-layout.epro2` | 203089 | `5c887e0af5b87871acdeefa320065ac7a239789eb5d59d06bed96c9856df7f69` |
| `Agent-Light-RevA-DRAFT-Gerber.zip` | 66941 | `2a37c507263b2652728a6d2b3c007bf7a2ee4e2d1afac6721f7ae751affa1ed2` |

The project is a V3 archive (`project2.json` and one `.epru` log): a 12 x 40 mm two-layer board with one GND pour per
side, 305 tracks, 32 vias, 35 parts (SMD passives, a USB-C connector with slot holes and polygon pads, two
non-plated 0.6 mm holes written as multi-layer fills). `tests/test_easyeda_pro_parser.py` compares the reader with
the Gerber files: copper, solder mask and paste differ by less than 0.2 % of the film area (paste 0.7 %), the outline is
identical, and the drill files agree tool by tool (diameter, holes, slots). The silkscreen text is not stored as
polygons and is skipped with a warning (silkscreen differs by 9.4 %, all of it text).

The other tests build small synthetic V2 projects (the line-array layout of
<https://github.com/easyeda/easyeda-file-format-v2>) in a temporary folder.

## Units found while measuring (the vendor text says 0.01 inch for everything)

* every length of a record: mil (0.0254 mm); `CANVAS` unit is a display unit;
* `POURED` (stored pour result) and `FONT` (text cache) paths: 0.01 inch (0.254 mm);
* the `width` of a `POUR` is a length in mm that grows the stored fill on every side by half of it;
* the polygon of a polygon pad is in footprint coordinates, not relative to the pad.
