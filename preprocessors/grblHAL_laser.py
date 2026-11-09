# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# Author: Marius Stanciu (c)                                #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_laser import GRBL_laser


class grblHAL_laser(GRBL_laser):

    def start_code(self, p):
        return super().start_code(p).replace('GRBL firmware', 'grblHAL firmware')
