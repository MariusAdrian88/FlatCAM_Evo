# DAP42 v2 native compatibility fixture

Author: Devan Lai. Native DipTrace binary version: 40.

Source revision: `0267508945f4c6add4f8c6125ae4f7c2dd81ef76` of
<https://github.com/devanlai/dap42-hardware>.

`DAP42v2.dip` and the `gerber/DAP42v2.*` production references are unchanged
input data from that revision (`DAP42v2.dip` and `gerbers-v2/` respectively).
The native file's SHA-256 is
`94ca8a5a8aec3242c530ef6f93d96373971bec37da239e9bc3b152e1039e44b9`.

The author's README dedicates the schematic, PCB layout and associated CAD and
Gerber production files to CC0 1.0:
<https://github.com/devanlai/dap42-hardware/blob/0267508945f4c6add4f8c6125ae4f7c2dd81ef76/README.md#license>.
License: <https://creativecommons.org/publicdomain/zero/1.0/>.

These are format/geometry test inputs, not importer implementation sources.
Gerbers and drill files are read only by tests, never by the native importer.

The published CAM outputs are not a bit-exact snapshot of the native board:
the native routing moves the via at `(2.8575,18.415)` to `(-2.8575,19.685)`
and contains additional vias at `(-10.59,10.16)` and `(-10.59,7.62)` (mm).
The top Gerber's `OUTLINE0` macro also has a malformed rotation literal,
`-2E-6.0`, which the existing Gerber reader cannot interpret. Tests compare an
unchanged copper region outside that macro, check native pad placement, and
account explicitly for those native/CAM drill differences. They do not claim
whole-board Gerber equality for this older sample.
