# ##########################################################
# FlatCAM-Evo: 2D Post-processing for Manufacturing            #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.Fanuc import Fanuc


class Haas(Fanuc):
    """HAAS Next Generation Control milling/drilling profile for manual/touch-off tool changes without ATC or G43."""

    def start_code(self, p):
        # G49 is required: since NGC 100.21.000.1100 a tool length offset applies by default and persists.
        gcode = super().start_code(p)
        for old, new in (
                ('(This preprocessor is used with a FANUC 0i-MC ISO machining center.)',
                 '(This preprocessor is used with a HAAS Next Generation Control.)'),
                ('(Restricted to 3-axis linear milling and drilling; the tool change is manual.)',
                 '(Restricted to 3-axis linear milling and drilling; the tool change is manual/touch-off.)'),
                ('(SETUP: select the work coordinate system and set the tool tip reference)\n'
                 '(with length compensation canceled by G49, then press Cycle Start.)',
                 '(SETUP: select the work coordinate system and set the tool tip reference)\n'
                 '(G49 cancels the tool length offset that NGC applies automatically; then press Cycle Start.)')):
            gcode = self.patch(gcode, old, new)
        return gcode

    def dwell_code(self, p):
        # HAAS G04 P is seconds only with a decimal point: P10. is 10 seconds, P10 is 10 milliseconds.
        if p.dwelltime:
            return 'G04 P' + self.seconds_text(p.dwelltime)
        return ''

    def finalize_gcode(self, gcode):
        # HAAS program numbers are five digits: O00001 is a fixed FlatCAM fallback, not a vendor default; rename the
        # program in the control when O00001 is already in use.
        return self.patch(super().finalize_gcode(gcode), '%\nO0001\n', '%\nO00001\n')
