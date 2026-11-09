# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_11_no_M6 import GRBL_11_no_M6


class grblHAL_no_M6(GRBL_11_no_M6):

    def start_code(self, p):
        return super().start_code(p).replace('GRBL firmware', 'grblHAL firmware')
