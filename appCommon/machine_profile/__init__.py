# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the frame contract (Stage 0, types only, no behavior).

The package holds passive machine facts and the checker of the final machine motion. It has no Qt and it does not
import the Sender, E-Cut or Levelling. The types are in `records.py`.

Frames

1. Units: mm inside the profile and inside the records. Values convert at the edge (G20/G21 of the program).
2. Machine frame M: the bed, the permanent exclusions and the fixed stations (toolchange, end, probe plate).
   The Z reference is the top of the Z travel.
3. Work frame W: the program coordinates. W = M - (active WCS offset + G92 + TLO). The offset is a state that
   changes along the program: G54..G59, G92, G92.1, G43/G49 and a tool change can change it. The checker follows
   these commands.
4. Design frame D: the canvas. Without correction, D = W. With the correction C, W = C(D). A machine boundary shows
   on the canvas as C^-1(boundary - offset): a polygon, not a rectangle.

Setup (the starting state of the check)

5. The setup items are: the profile hash, the dialect, the offset of each WCS, the active WCS, G92, TLO, the start
   XYZ, the units, the distance mode (G90/G91), the arc mode (G90.1/G91.1), the plane and the motion mode that
   the program inherits.
6. Each item is known or unknown. In the types, None means unknown.

Propagation of unknown state

7. A move that depends on an unknown item is UNVERIFIED. Its M start or end is None, and the record keeps the
   reason in `unverified_reason`.
8. A command makes an item known from that point, but only if the command has all of its inputs:
   - G21/G20 set the units, and G90/G91 set the distance mode;
   - G10 L2 sets the WCS offset directly; G10 L20 needs the known position, G92 and TLO, so it stays unknown
     if one of them is unknown;
   - an absolute value for an axis makes that axis of the endpoint known, but the path from an unknown start
     stays UNVERIFIED.
9. For a file output the setup is the declared setup (the profile defaults plus the job), and the result lists the
   assumptions that it used. For a live run (stage 4) the setup is the state read from the controller.

Fixed stations

10. Toolchange, end and probe plate are fixed in M. The correction does not move them.
11. A fixed move is identified by its role (the `fixed_role` of the record), never by its XY. A design rapid can
    have the same XY as a station.
12. After a fixed sequence the position is the true end point, so that the next move with only some axes starts
    from the correct point.

G53, G28, G30

13. G53 is resolved if the setup is known and the dialect supports it: the position is a known machine position,
    and a resolved G53 updates the position for the next move. It is UNVERIFIED only if one of these is unknown.
14. G28 and G30 follow the same rule if the profile stores their positions. G28 with axis words first runs an
    intermediate move. Otherwise they are UNVERIFIED.

Result of a check

15. Three states: CHECKED (no violation), PARTIAL (no confirmed violation, but one or more UNVERIFIED), VIOLATIONS.
    The result never says CHECKED if an UNVERIFIED violation exists, and it lists the assumptions that it used.
"""
