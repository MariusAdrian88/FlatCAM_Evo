# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Matthieu Berthomé                           #
# MIT Licence                                              #
# ##########################################################

from preprocessors.GRBL_laser_z import GRBL_laser_z


class grblHAL_laser_z(GRBL_laser_z):

    def start_code(self, p):
        return super().start_code(p).replace('GRBL firmware', 'grblHAL firmware')
