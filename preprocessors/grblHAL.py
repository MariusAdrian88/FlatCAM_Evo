# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Matthieu Berthomé                           #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11 import GRBL_11


class grblHAL(GRBL_11):

    def start_code(self, p):
        return super().start_code(p).replace('GRBL firmware', 'grblHAL firmware').replace(
            '(It is configured to be compatible with almost any version of grblHAL firmware.)',
            '(The manual tool change M6 is acknowledged by the Sender; no M0 pause is written after it.)')

    def toolchange_code(self, p):
        # M6 already suspends grblHAL in Tool state; a following M0 would pause a second time.
        return super().toolchange_code(p).replace('\nM0\n', '\n')
