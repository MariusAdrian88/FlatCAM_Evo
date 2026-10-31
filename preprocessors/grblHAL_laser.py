# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Matthieu Berthomé                           #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_laser import GRBL_laser


class grblHAL_laser(GRBL_laser):

    def start_code(self, p):
        return super().start_code(p).replace('GRBL firmware', 'grblHAL firmware')
