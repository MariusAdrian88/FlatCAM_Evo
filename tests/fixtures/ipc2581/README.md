# IPC-2581 fixture

## `Easyduino_RP2040.xml.gz`

IPC-2581 revision C export of the Easyduino RP2040 board (4 copper layers, pours, tracks, 137 round holes, 4 plated
slots, components on both sides), gzip -9 -n of the XML (3.0 MB unpacked). `appParsers/ParseIPC2581.py` reads the
gzip file directly.

- Source board: `tests/fixtures/kicad/Easyduino_RP2040.kicad_pcb`, the board that the KiCad PCB import tests already
  use (the same files there: `Gerbers/` and `Easyduino_RP2040.drl` are the references of `tests/test_ipc2581_parser.py`).
  Its licence is not recorded in the repository; the fixture is a derived export of that file and has the same terms.
- Generator: KiCad 10.0.8, `kicad-cli pcb export ipc2581 -o Easyduino_RP2040.xml Easyduino_RP2040.kicad_pcb`
  (defaults: revision C, millimetres, precision 6), then `gzip -9 -n`.
- sha256 of `Easyduino_RP2040.kicad_pcb`: `63160fa964ca0003754dc2973b95e98178ac06b1292476c2397bf1cdb21f8f65`
- sha256 of the unpacked `Easyduino_RP2040.xml`: `e66564f67b6ea07684e269d89cf543ea867787d33eacad99b0dcfe12b5c625c2`
- sha256 of `Easyduino_RP2040.xml.gz`: `78dbfafaea564fa98e111c0dffeacf8cb70d1537ee5e4f75607fce1a4ffa89eb`

Reference numbers kept in the test (areas of the KiCad Gerbers parsed by FlatCAM, drill list of the Excellon file) were
measured on `tests/fixtures/kicad/Gerbers`. Revision B, inch and micron files are covered by small hand-written
documents in the test: KiCad's revision B export has no slot positions (see the module docstring of the parser).
