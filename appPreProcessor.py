# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Matthieu Berthomé                           #
# Date: 5/26/2017                                          #
# MIT Licence                                              #
# ##########################################################

import importlib.util
import os
import sys
from abc import ABCMeta, abstractmethod
import math     # keep this: older preprocessors get it with 'from appPreProcessor import *'

# module-root dictionary of preprocessors

import logging

log = logging.getLogger('base')
preprocessors = {}


class ABCPreProcRegister(ABCMeta):
    # handles preprocessors registration on instantiation
    def __new__(cls, clsname, bases, attrs):
        newclass = super(ABCPreProcRegister, cls).__new__(cls, clsname, bases, attrs)
        if object not in bases:
            if newclass.__name__ in preprocessors:
                log.warning('Preprocessor %s has been overriden' % newclass.__name__)
            preprocessors[newclass.__name__] = newclass()  # here is your register function
        return newclass


class PreProc(object, metaclass=ABCPreProcRegister):
    @abstractmethod
    def start_code(self, p):
        pass

    @abstractmethod
    def lift_code(self, p):
        pass

    @abstractmethod
    def down_code(self, p):
        pass

    @abstractmethod
    def toolchange_code(self, p):
        pass

    @abstractmethod
    def up_to_zero_code(self, p):
        pass

    @abstractmethod
    def rapid_code(self, p):
        pass

    @abstractmethod
    def linear_code(self, p):
        pass

    @abstractmethod
    def end_code(self, p):
        pass

    @abstractmethod
    def feedrate_code(self, p):
        pass

    @abstractmethod
    def spindle_code(self, p):
        pass

    @abstractmethod
    def spindle_stop_code(self, p):
        pass

    # the methods below are called by the CNCjob too; a preprocessor that does not have them gets these G-code forms
    coordinate_format = "%.*f"
    feedrate_format = '%.*f'

    def startz_code(self, p):
        if p.startz is not None:
            return 'G00 Z' + self.coord(p, p.startz)
        return ''

    def z_feedrate_code(self, p):
        return 'G01 F' + self.feedrate_format % (p.fr_decimals, p.z_feedrate)

    def dwell_code(self, p):
        # the dwell time is in seconds
        if p.dwelltime:
            return 'G4 P' + str(p.dwelltime)
        return ''

    def position_code(self, p):
        x_pos, y_pos = self.bed_xy(p)
        return 'X' + self.coord(p, x_pos) + ' Y' + self.coord(p, y_pos)

    def coord(self, p, value):
        """
        A coordinate as text, with the coordinate decimals of the job.

        :param p:       preprocessor parameters
        :param value:   the coordinate
        :return:        str
        """
        return self.coordinate_format % (p.coords_decimals, float(value))

    @staticmethod
    def bed_xy(p, x=None, y=None):
        """
        The machine position of a job point: the bed offset added and the bed skew applied. The skew on X moves a point
        by y / bed_limit_y * skew_x (the tangent of the skew angle), the skew on Y by x / bed_limit_x * skew_y.

        :param p:   preprocessor parameters
        :param x:   the X of the point; by default p.x
        :param y:   the Y of the point; by default p.y
        :return:    (x, y) as floats, in the job units
        """
        x = float(p.x if x is None else x)
        y = float(p.y if y is None else y)
        x_pos = x + float(p._bed_offset_x)
        y_pos = y + float(p._bed_offset_y)
        if p._bed_skew_x and not p._bed_limit_y or p._bed_skew_y and not p._bed_limit_x:
            raise ValueError('Bed-skew correction needs a non-zero bed size.')
        if p._bed_skew_x:
            x_pos += (y / float(p._bed_limit_y)) * float(p._bed_skew_x)
        if p._bed_skew_y:
            y_pos += (x / float(p._bed_limit_x)) * float(p._bed_skew_y)
        return x_pos, y_pos

    @staticmethod
    def end_xy_move_z(p):
        """
        The height of the final X-Y move to the end position: End Z, or the Travel Z when End Z is lower, so the
        tool does not travel over the workpiece and the clamps below the Travel Z.

        :param p:   preprocessor parameters
        :return:    the Z of the final X-Y move
        """
        try:
            return max(float(p['z_end']), float(p['z_move']))
        except (KeyError, TypeError, ValueError):
            return p['z_end']

    def end_moves_code(self, p, rapid='G00', z_rapid=None, suffix=''):
        """
        The moves at the end of the job: to End Z, then the X-Y move to the end position (if one is set).
        When End Z is below the Travel Z, the X-Y move is made at the Travel Z and the tool goes down to End Z after it.

        :param p:           preprocessor parameters
        :param rapid:       the rapid move command of the X-Y move ('G00' or 'G0')
        :param z_rapid:     the rapid move command of the Z moves; by default the same as rapid
        :param suffix:      text added at the end of each move (e.g. the rapid feedrate)
        :return:            G-code
        """
        z_rapid = rapid if z_rapid is None else z_rapid
        coords_xy = p['xy_end']

        if not coords_xy:
            return z_rapid + ' Z' + self.coord(p, p.z_end) + suffix + "\n"

        z_xy = self.end_xy_move_z(p)
        gcode = z_rapid + ' Z' + self.coord(p, z_xy) + suffix + "\n"
        gcode += rapid + ' X' + self.coord(p, coords_xy[0]) + ' Y' + self.coord(p, coords_xy[1]) + suffix + "\n"
        if z_xy != p.z_end:
            gcode += z_rapid + ' Z' + self.coord(p, p.z_end) + suffix + "\n"
        return gcode


class AppPreProcTools(object, metaclass=ABCPreProcRegister):
    @abstractmethod
    def start_code(self, p):
        pass

    @abstractmethod
    def lift_code(self, p):
        pass

    @abstractmethod
    def down_z_start_code(self, p):
        pass

    @abstractmethod
    def lift_z_dispense_code(self, p):
        pass

    @abstractmethod
    def down_z_stop_code(self, p):
        pass

    @abstractmethod
    def toolchange_code(self, p):
        pass

    @abstractmethod
    def rapid_code(self, p):
        pass

    @abstractmethod
    def linear_code(self, p):
        pass

    @abstractmethod
    def end_code(self, p):
        pass

    @abstractmethod
    def feedrate_xy_code(self, p):
        pass

    @abstractmethod
    def z_feedrate_code(self, p):
        pass

    @abstractmethod
    def feedrate_z_dispense_code(self, p):
        pass

    @abstractmethod
    def spindle_fwd_code(self, p):
        pass

    @abstractmethod
    def spindle_rev_code(self, p):
        pass

    @abstractmethod
    def spindle_off_code(self, p):
        pass

    @abstractmethod
    def dwell_fwd_code(self, p):
        pass

    @abstractmethod
    def dwell_rev_code(self, p):
        pass


def builtin_preprocessors_path():
    """
    The folder of the preprocessors that come with the application: next to this file, or in lib/ next to the
    executable in a frozen build (make_freeze.py puts the modules in lib/library.zip and the data folders in lib/).
    It does not depend on the current working directory.

    :return:    the path of the folder
    :rtype:     str
    """
    if getattr(sys, 'frozen', False):
        app_dir = os.path.join(os.path.dirname(os.path.realpath(sys.executable)), 'lib')
    else:
        app_dir = os.path.dirname(os.path.realpath(__file__))

    return os.path.join(app_dir, 'preprocessors')


def load_preprocessor_file(file_path):
    """
    Execute a preprocessor file as a module of its own. The preprocessor classes defined in it are registered in the
    'preprocessors' dict by the ABCPreProcRegister metaclass.

    :param file_path:   the path of the preprocessor file (*.py)
    :type file_path:    str
    :return:            the module
    """
    module_name = 'FlatCAMPostProcessor_%s' % os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_preprocessors(app):
    import glob

    # first the user preprocessors in the data folder, then the ones that come with the application; the folders are
    # absolute paths, escaped because '[' or ']' in a folder name is a glob pattern
    preprocessors_path_search = [
        os.path.join(glob.escape(os.path.join(app.data_path, 'preprocessors')), '*.py'),
        os.path.join(glob.escape(builtin_preprocessors_path()), '*.py')
    ]
    for path_search in preprocessors_path_search:
        for file in glob.glob(path_search):
            try:
                load_preprocessor_file(file)
            except Exception as e:
                app.log.error(str(e))
    return preprocessors
