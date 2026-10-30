# ########################################################## ##
# FlatCAM: 2D Post-processing for Manufacturing               #
# http://flatcam.org                                          #
# Author: Juan Pablo Caram (c)                                #
# Date: 2/5/2014                                              #
# MIT Licence                                                 #
# ########################################################## ##

from camlib import (list_total_bounds, Geometry, grace, affine_geometry_many, affine_translate_matrix, affine_rotate_matrix,
                    affine_scale_matrix, affine_skew_matrix, affine_mirror_matrix, circle_quad_segs, object_circle_steps)

import shapely
import shapely.affinity as affinity
from shapely import Point, LineString, LinearRing, MultiLineString, MultiPolygon
import numpy as np

import math
import re
import logging
import traceback
from copy import deepcopy

from appParsers.gerber_attributes import AttributeState, file_attributes

# import AppTranslation as fcTranslate

import gettext
import builtins

if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


def hole_ids(tool, kind):
    """
    The object attribute (TO) ids of the holes of a tool, parallel to tool['drills'] (kind 'drills') or to
    tool['slots'] (kind 'slots'); 0 is a hole without object attributes. None: the tool has no ids, or the ids are
    not in step with the holes any more (a code path that changed the holes and not the ids), so they are not used.
    """
    ids = tool.get(kind + '_to')
    holes = tool.get(kind)
    if ids is None or holes is None or len(ids) != len(holes):
        return None
    return ids


def to_state(to_attributes, ident):
    """The object attributes {name: [values]} of an id; a project file (JSON) has the ids as strings."""
    if not ident or not to_attributes:
        return None
    state = to_attributes.get(ident)
    return state if state is not None else to_attributes.get(str(ident))


class _ParseState:
    """The state of one Excellon.parse_lines() run."""

    def __init__(self):
        self.tool = None                # the selected tool
        self.in_header = False
        self.header_end_seen = False
        self.allegro = False
        self.name_tool = 0              # Allegro tool counter
        self.line_units_found = False
        self.line_units = ''
        self.units_resolved = False     # the units of the numbers are known

        # modal position, in the file units; the G93 origin
        self.cx = None
        self.cy = None
        self.ox = 0.0
        self.oy = 0.0
        self.incremental = False        # G91 / ICI,ON

        self.mode = 0                   # 0: drill (G05), 1: route (G00 .. G03)
        self.gmode = 0                  # the modal route command: 0 rapid, 1 linear, 2 arc CW, 3 arc CCW

        self.tool_map = {}              # tool number in the file -> tool key, for the tools redefined in the body
        self.drills = {}                # tool -> [(x, y)]
        self.slots = {}                 # tool -> [(x start, y start, x stop, y stop)]
        self.stored = False

        self.recording = None           # the events of the pattern (M25 .. M01) while it is recorded
        self.pattern = []
        self.pattern_origin = (0.0, 0.0)    # the position at M25: M70 / M80 / M90 swap or mirror about it

        self.file_plated = None         # plated flag for the whole file
        self.aper_plated = None         # plated flag for the tools defined next

        self.tool_down = None           # M15 / M16: None until the file uses them (then every G01 .. G03 cuts)
        self.arc_radius = None          # the modal radius of G02 / G03 (the last A)
        self.ended = False              # M30 was read: no more data is used

        self.attrs = AttributeState()   # the attributes of the comments "#@! TF.", "TA.", "TO.", "TD"
        self.drills_to = {}             # tool -> [TO id of each drill]
        self.slots_to = {}              # tool -> [TO id of each slot]
        self.any_to = False             # at least one hole has an object attribute

        self.ignored = 0                # lines that were not understood
        self.dropped = 0                # holes that could not be made


class Excellon(Geometry):
    """
    Here it is done all the Excellon parsing.

    *ATTRIBUTES*

    * ``tools`` (dict): The key is the tool name and the value is
      a dictionary specifying the tool:

    ================  ====================================
    Key               Value
    ================  ====================================
    tooldia           Diameter of the tool
    drills            List that store the Shapely Points for drill points
    slots             List that store the Shapely Points for slots. Each is a tuple: (start_point, stop_point)
    data              dictionary which holds the options for each tool
    solid_geometry    Geometry list for each tool
    ================  ====================================

    """

    defaults = {
        "zeros": "L",
        "excellon_format_upper_mm": '3',
        "excellon_format_lower_mm": '3',
        "excellon_format_upper_in": '2',
        "excellon_format_lower_in": '4',
        "excellon_units": 'INCH',
        "excellon_circle_steps": '32'
    }

    # the XNC coordinates without a period are decimal numbers when the file has no number format (see _axis_value)
    _seen_period = False
    _fmt_declared = False

    def __init__(self, app, zeros=None, excellon_format_upper_mm=None, excellon_format_lower_mm=None,
                 excellon_format_upper_in=None, excellon_format_lower_in=None, excellon_units=None,
                 excellon_circle_steps=None):
        """
        The constructor takes no parameters.

        :return: Excellon object.
        :rtype: Excellon
        """
        if self.__dict__.get('_Excellon_inited'):
            return

        self.app = app

        self.decimals = self.app.decimals

        # The segments of each circle follow the preferences (see circle_steps()); excellon_circle_steps is a fixed
        # count of segments for every circle, None for the preferences.
        self._fixed_steps = None if excellon_circle_steps is None else int(excellon_circle_steps)
        if self._fixed_steps is None:
            self.excellon_circle_steps = int(self._app_option("excellon_circle_steps", 32))
        else:
            self.excellon_circle_steps = self._fixed_steps

        Geometry.__init__(self, geo_steps_per_circle=self.excellon_circle_steps, app=app)

        # dictionary to store tools, see above for description
        self.tools = {}

        self.source_file = ''

        # the FMAT and VER commands of the file
        self.fmat = None
        self.ver = None

        # True: after the parse the tools with the same diameter become one tool (see merge_equal_diameter_tools)
        self.merge_equal_tools = False

        # ## IN|MM -> Units are inherited from Geometry
        self.units = self.app.app_units
        self.units_found = self.app.app_units

        # Trailing "T" or leading "L" (default)
        # self.zeros = "T"
        self.zeros = zeros or self.defaults["zeros"]
        self.zeros_found = deepcopy(self.zeros)

        # this will serve as a default if the Excellon file has no info regarding of tool diameters (this info may be
        # in another file like for PCB WIzard ECAD software
        self.toolless_diam = 1.0
        # signal that the Excellon file has no tool diameter informations and the tools have bogus (random) diameter
        self.diameterless = False

        # Excellon format
        self.excellon_format_upper_in = excellon_format_upper_in or self.defaults["excellon_format_upper_in"]
        self.excellon_format_lower_in = excellon_format_lower_in or self.defaults["excellon_format_lower_in"]
        self.excellon_format_upper_mm = excellon_format_upper_mm or self.defaults["excellon_format_upper_mm"]
        self.excellon_format_lower_mm = excellon_format_lower_mm or self.defaults["excellon_format_lower_mm"]
        self.excellon_units = excellon_units or self.defaults["excellon_units"]
        self.excellon_units_found = None

        # detected Excellon format is stored here:
        self.excellon_format = None

        # The attributes of the file (XNC chapter 4), same names as in Gerber
        self.attributes = {'TF': {}, 'TA': {}, 'TO': {}}
        self.to_attributes = {}
        self.file_attributes = {}

        # Attributes to be included in serialization
        # Always append to it because it carries contents
        # from Geometry.
        self.ser_attrs += ['zeros', 'excellon_format_upper_mm', 'excellon_format_lower_mm',
                           'excellon_format_upper_in', 'excellon_format_lower_in', 'excellon_units', 'source_file',
                           'attributes', 'to_attributes', 'file_attributes']

        # ### Patterns ####
        # Regex basics:
        # ^ - beginning
        # $ - end
        # *: 0 or more, +: 1 or more, ?: 0 or 1
        # The lines are upper case when these are applied (the comments excepted).

        # M48 - Beginning of Part Program Header
        self.hbegin_re = re.compile(r'^M48$')

        # ;HEADER - Beginning of Allegro Program Header
        self.allegro_hbegin_re = re.compile(r'^;\s*HEADER\s*:?\s*$')

        # M95 or % - End of Part Program Header
        # NOTE: % has different meaning in the body
        self.hend_re = re.compile(r'^(?:M95|%)$')

        # Uunits and possible Excellon zeros and possible Excellon format
        # INCH uses 6 digits
        # METRIC uses 5/6
        self.units_re = re.compile(r'^(INCH|METRIC)(?:,([TL])Z)?,?(\d*\.\d+)?.*$')

        # Tool parameters, in any order: C diameter, F feed, S speed, B, H, Z
        self.tool_param_re = re.compile(r'([CFSBHZ]),?([-+]?(?:\d+\.?\d*|\.\d+))')

        self.detect_gcode_re = re.compile(r'^G2([01])$')

        # Tool select
        # Can have additional data after tool number (the parameters)
        self.toolsel_re = re.compile(r'^T(\d+)')

        # Absolute/Incremental G90/G91
        self.absinc_re = re.compile(r'^G9([01])$')

        # Measuring mode
        # 1-metric, 2-inch
        self.meas_re = re.compile(r'^M7([12])$')

        # A plain drill hit: X1.0Y2.0 or X010000Y020000, one or both axes
        self.drill_fast_re = re.compile(r'^(?:X([-+]?(?:\d+\.?\d*|\.\d+)))?(?:Y([-+]?(?:\d+\.?\d*|\.\d+)))?$')

        # A line made only of letter + number tokens (G85, G02, M15, R3, X..Y..); the tokens
        self.line_re = re.compile(r'^(?:[A-Z][-+]?(?:\d+\.?\d*|\.\d+)?)+$')
        self.token_re = re.compile(r'([A-Z])([-+]?(?:\d+\.?\d*|\.\d+)?)')

        # VER, FMAT and ICI commands
        self.command_re = re.compile(r'^(ICI|FMAT|VER),\s*(\w+)')

        # M47 message, M97 / M98 canned text
        self.canned_re = re.compile(r'^M(?:47|97|98),')

        # Commands that do not draw anything and are not worth a warning
        self.benign_re = re.compile(r'^(?:M\d+|G\d+|DETECT|BLKD|SG|TCST|OPTION|ATC|CSAB|NCSB|PCKB|ICI|FMAT|VER)')

        # Various stop/pause commands
        self.stop_re = re.compile(r'^((G04)|(M09)|(M06)|(M00)|(M30))')

        # Allegro Excellon format support
        self.tool_units_re = re.compile(r'(\;\s*Holesize \d+.\s*\=\s*(\d+.\d+).*(MILS|MM))')

        # Altium Excellon format support
        # it's a comment like this: ";FILE_FORMAT=2:5"
        self.altium_format = re.compile(r'^;\s*(?:FILE_FORMAT)?(?:Format)?[=|:]\s*(\d+)[:|.](\d+).*$')

        # KiCad: "; FORMAT={3:3/ absolute / metric / decimal}"
        self.kicad_format_re = re.compile(r'^;\s*FORMAT\s*=\s*\{\s*(\d+)\s*:\s*(\d+)', re.IGNORECASE)

        # Attribute comments: "; #@! TF.FileFunction,Plated,1,2,PTH", TA., TO., TD
        self.attr_re = re.compile(r'^;\s*#@!\s*(T[FAOD])(\..*)?$', re.IGNORECASE)

        # Plated / non plated tags in comments:
        # KiCad "; #@! TF.FileFunction,Plated,1,2,PTH", "; #@! TA.AperFunction,NonPlated,NPTH,..." and "; #@! TD"
        self.attr_plated_re = re.compile(r'^;\s*#@!\s*T([FA])\.(?:FileFunction|AperFunction),\s*(Non)?Plated',
                                         re.IGNORECASE)
        self.attr_delete_re = re.compile(r'^;\s*#@!\s*TD\b', re.IGNORECASE)
        # Altium ";TYPE=PLATED"
        self.type_plated_re = re.compile(r'^;\s*TYPE\s*=\s*(NON[_ -]?)?PLATED', re.IGNORECASE)
        # Allegro "...PLATED MILS" / "...NON_PLATED MILS"
        self.nonplated_re = re.compile(r'NON[_ -]?PLATED', re.IGNORECASE)
        self.plated_re = re.compile(r'\bPLATED\b', re.IGNORECASE)

        self._Excellon_inited = True

    def parse_file(self, filename=None, file_obj=None):
        """
        Reads the specified file as array of lines as passes it to ``parse_lines()``.

        :param filename:    The file to be read and parsed.
        :param file_obj:
        :type filename:     str
        :return:            None, or "fail"
        """
        if file_obj:
            estr = file_obj
        else:
            if filename is None:
                return "fail"

            estr = ""
            decoded = False
            # utf-8-sig first: it drops a BOM and a file that is not UTF-8 fails right away, then the code pages
            encodings_types = ('utf-8-sig', 'cp1252', 'cp850')
            for enc in encodings_types:
                try:
                    with open(filename, 'r', encoding=enc) as efile:
                        estr = efile.readlines()
                        decoded = True
                        break
                except UnicodeDecodeError:
                    pass
            if decoded is False:
                return 'fail'

        try:
            if self.parse_lines(estr) == "fail":
                return "fail"
        except Exception:
            self.app.log.error("Excellon parse_file() failed:\n%s" % traceback.format_exc())
            return "fail"

    def parse_lines(self, elines):
        """
        Main Excellon parser.

        :param elines: List of strings, each being a line of Excellon code.
        :type elines: list
        :return: None
        """

        st = _ParseState()
        self._seen_period = False
        self._fmt_declared = False

        # ## Parsing starts here ## ##
        line_num = 0  # Line number
        eline = ""
        src_parts = []
        stopped = False
        try:
            try:
                for eline in elines:
                    if self.app.abort_flag:
                        # graceful abort requested by the user
                        raise grace

                    line_num += 1
                    src_parts.append(eline)

                    if st.ended:
                        # no data is allowed after M30 (XNC 3.15): the lines are kept in the source only
                        text = eline.strip()
                        if text and not text.startswith(';'):
                            self.app.log.warning("Excellon: data after M30 is ignored. Line %d: %s" % (line_num, text))
                        continue

                    if self._parse_line(st, eline):
                        stopped = True
                        break

                if not stopped:
                    self._finish_parse(st)
            finally:
                # one join instead of a string concatenation per line
                self.source_file += ''.join(src_parts)
                if not st.stored:
                    self._store_holes(st)

            self.app.log.info("Zeros: %s, Units %s." % (self.zeros, self.units))
        except Exception:
            self.app.log.error("Excellon PARSING FAILED. Line %d: %s" % (line_num, eline))
            msg = '[ERROR_NOTCL] %s' % _("An internal error has occurred. See shell.\n")
            msg += '{e_code} {tx} {l_nr}: {line}\n'.format(
                e_code='[ERROR]',
                tx=_("Excellon Parser error.\nParsing Failed. Line"),
                l_nr=line_num,
                line=eline)
            msg += traceback.format_exc()
            self.app.inform.emit(msg)

            return "fail"

    # ############################################################################################################
    # ###################################### line dispatch #######################################################
    # ############################################################################################################
    def _parse_line(self, st, raw):
        """
        Parses one line of the file.

        :param st:  the parse state
        :param raw: the line, as read
        :return:    True if the parsing has to stop (this is not an Excellon file)
        """
        eline = raw.strip(' \t\r\n﻿')
        if not eline:
            return False

        c0 = eline[0]

        # fast path: a plain drill hit in drill mode (the vast majority of the lines)
        if (c0 == 'X' or c0 == 'Y') and st.mode == 0 and not st.in_header:
            match = self.drill_fast_re.match(eline)
            if match:
                self._drill_line(st, match.group(1), match.group(2))
                return False

        if c0 == ';':
            self._comment(st, eline)
            return False

        # the Excellon commands are upper case; only the comments keep their case
        eline = eline.upper()

        # Excellon files and Gcode share some extensions therefore if we detect G20 or G21 it's GCODe
        # and we need to exit from here
        if self.detect_gcode_re.search(eline):
            self.app.log.warning("This is GCODE mark: %s" % eline)
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_('This is GCODE mark'), eline))
            return True

        # Header Begin (M48) #
        if self.hbegin_re.search(eline):
            st.in_header = True
            st.header_end_seen = False
            self.app.log.warning("Found start of the header: %s" % eline)
            return False

        # Header End (% or M95) #
        if self.hend_re.search(eline):
            self._header_end(st, eline)
            return False

        # ## Alternative units format M71/M72
        # Supposed to be just in the body (yes, the body) but some put it in the header (PADS for example).
        # Will detect anywhere. Occurrence will change the object's units.
        match = self.meas_re.match(eline)
        if match:
            units = {"1": "MM", "2": "IN"}[match.group(1)]
            self._set_file_units(st, units)
            self.excellon_units_found = units
            st.units_resolved = True
            self.app.log.debug("Alternative M71/M72 units found: %s" % self.units)
            return False

        # canned text and messages: nothing to do with the geometry
        if self.canned_re.match(eline):
            return False

        match = self.command_re.match(eline)
        if match:
            self._command(st, match)
            return False

        if st.in_header:
            self._header_line(st, eline)
        else:
            self._body_line(st, eline)
        return False

    def _comment(self, st, eline):
        """
        A comment line. Some CAM programs hide information in them: Allegro tools and units, the Altium / KiCad
        number format, the plated / non plated tags. The rest is ignored.
        """
        # attributes: TF, TA, TO, TD (the plated flags are read below too)
        match = self.attr_re.match(eline)
        if match:
            st.attrs.apply(match.group(1).upper(), (match.group(2) or '').strip())

        # Allegro header
        if self.allegro_hbegin_re.match(eline):
            st.in_header = True
            st.allegro = True
            self.app.log.warning("Found ALLEGRO start of the header: %s" % eline)
            return

        # Allegro tool and units
        match = self.tool_units_re.search(eline)
        if match:
            if st.line_units_found is False:
                st.line_units_found = True
                st.line_units = match.group(3)
                units = {"MILS": "IN", "MM": "MM"}[st.line_units]
                self._set_file_units(st, units)
                self.excellon_units_found = units
                st.units_resolved = True
                self.app.log.warning("Type of Allegro UNITS found inline in comments: %s" % st.line_units)

            if match.group(2):
                st.name_tool += 1
                dia = float(match.group(2))
                if st.line_units == 'MILS':
                    dia /= 1000
                self.tools[st.name_tool] = {'tooldia': dia, 'solid_geometry': []}
                if self.nonplated_re.search(eline):
                    self.tools[st.name_tool]['plated'] = False
                elif self.plated_re.search(eline):
                    self.tools[st.name_tool]['plated'] = True
                return

        # search for Altium Excellon Format / Sprint Layout who is included as a comment
        match = self.altium_format.search(eline)
        if match:
            self._fmt_declared = True
            self.excellon_format_upper_mm = match.group(1)
            self.excellon_format_lower_mm = match.group(2)

            self.excellon_format_upper_in = match.group(1)
            self.excellon_format_lower_in = match.group(2)
            self.app.log.warning("Excellon format preset found in comments: %s:%s" % (match.group(1), match.group(2)))
            return

        # KiCad: ; FORMAT={3:3/ absolute / metric / decimal}
        match = self.kicad_format_re.match(eline)
        if match:
            upper, lower = match.group(1), match.group(2)
            self._fmt_declared = True
            text = eline.upper()
            if 'METRIC' in text or 'INCH' not in text:
                self.excellon_format_upper_mm = upper
                self.excellon_format_lower_mm = lower
            if 'INCH' in text or 'METRIC' not in text:
                self.excellon_format_upper_in = upper
                self.excellon_format_lower_in = lower
            return

        # plated / non plated tags
        match = self.attr_plated_re.match(eline)
        if match:
            plated = match.group(2) is None
            if match.group(1).upper() == 'F':
                st.file_plated = plated
            else:
                st.aper_plated = plated
            return
        if self.attr_delete_re.match(eline):
            st.aper_plated = None
            return
        match = self.type_plated_re.match(eline)
        if match:
            st.file_plated = match.group(1) is None
            return

        self.app.log.debug("Line ignored, it's a comment: %s" % eline)

    def _header_end(self, st, eline):
        """
        End of the header (M95 or %). The % in the body (rewind and stop) is ignored.
        """
        if st.in_header is False and st.header_end_seen:
            return

        if st.in_header is False or bool(self.tools) is False:
            self.app.log.warning("Found end of the header but there is no header: %s" % eline)
            self.app.log.warning("The only useful data in header are tools, units and format.")
            self.app.log.warning("Therefore we will create units and format based on defaults.")
            # the units found in the file win over the units in the preferences
            if self.excellon_units_found is None:
                units = self._preference_units()
                if units:
                    self._set_file_units(st, units)

        st.in_header = False
        st.header_end_seen = True
        st.units_resolved = True
        # for Allegro type of Excellons we reset name_tool variable so we can reuse it for toolchange
        if st.allegro is True:
            st.name_tool = 0
        self.app.log.warning("Found end of the header: %s" % eline)

        # In case that the units were not found in the file, we make an evaluation based on a threshold.
        # Most tools in Excellon are greater than 0.1mm therefore if most of the tools are under this
        # value it is safe to assume that the units are in INCH
        if self.excellon_units_found is None and self.tools:
            self._vote_units()

    def _preference_units(self):
        """The units from the preferences as 'IN' / 'MM', None if they are not known."""
        return {"INCH": "IN", "METRIC": "MM", "IN": "IN", "MM": "MM"}.get(str(self.excellon_units).upper())

    def _vote_units(self):
        """Assumes the units of the file from the tool diameters (no conversion, the numbers are the file's)."""
        greater_tools = set()
        lower_tools = set()
        for tool in self.tools:
            tool_dia = float(self.tools[tool]['tooldia'])
            lower_tools.add(tool_dia) if tool_dia <= 0.1 else greater_tools.add(tool_dia)
        self.units = "IN" if len(lower_tools) > len(greater_tools) else "MM"

    def _resolve_units(self, st):
        """The first hole came and the file did not say the units (no header end): preferences or tool diameters."""
        st.units_resolved = True
        if self.excellon_units_found is not None:
            return
        if self.tools:
            self._vote_units()
        else:
            units = self._preference_units()
            if units:
                self._set_file_units(st, units)

    def _set_file_units(self, st, units):
        """
        The numbers that come after this point are in `units`. What was already read is converted so that
        everything stored has the same units (a file can switch with M71 / M72 or INCH / METRIC in the body).
        """
        if units == self.units:
            return
        if self.excellon_units_found is not None or st.drills or st.slots:
            factor = 25.4 if units == 'MM' else 1 / 25.4
            for tool in self.tools.values():
                tool['tooldia'] *= factor
            for tool, pts in st.drills.items():
                st.drills[tool] = [(x * factor, y * factor) for x, y in pts]
            for tool, segs in st.slots.items():
                st.slots[tool] = [(a * factor, b * factor, c * factor, d * factor) for a, b, c, d in segs]
            if st.cx is not None:
                st.cx *= factor
            if st.cy is not None:
                st.cy *= factor
            st.ox *= factor
            st.oy *= factor
        self.units = units

    def _units_line(self, st, match):
        """INCH / METRIC line, with the optional zeros and number format."""
        units = {"METRIC": "MM", "INCH": "IN"}[match.group(1)]
        self._set_file_units(st, units)
        self.excellon_units_found = units
        st.units_resolved = True

        # a bare INCH / METRIC does not say anything about the zeros
        if match.group(2):
            self.zeros = match.group(2)  # "T" or "L"
            self._fmt_declared = True
        self.excellon_format = match.group(3)
        if self.excellon_format:
            self._fmt_declared = True
        if self.excellon_format:
            upper = len(self.excellon_format.partition('.')[0])
            lower = len(self.excellon_format.partition('.')[2])
            if self.units == 'MM':
                self.excellon_format_upper_mm = upper
                self.excellon_format_lower_mm = lower
            else:
                self.excellon_format_upper_in = upper
                self.excellon_format_lower_in = lower

        self.app.log.warning("UNITS found inline: %s, ZEROS: %s" % (self.units, self.zeros))
        if self.units == 'MM':
            self.app.log.warning("Excellon format preset is: %s:%s" %
                                 (str(self.excellon_format_upper_mm), str(self.excellon_format_lower_mm)))
        else:
            self.app.log.warning("Excellon format preset is: %s:%s" %
                                 (str(self.excellon_format_upper_in), str(self.excellon_format_lower_in)))

    def _command(self, st, match):
        """VER, FMAT and ICI commands (header or body)."""
        name, value = match.group(1), match.group(2)
        if name == 'ICI':
            st.incremental = (value == 'ON')
        elif name == 'FMAT':
            self.fmat = int(value) if value.isdigit() else None
        else:
            self.ver = int(value) if value.isdigit() else None

    def _header_line(self, st, eline):
        """A line of the header: tool definitions, units, zeros. The rest is ignored."""
        # ## Tool definitions # ##
        match = self.toolsel_re.match(eline)
        if match:
            params = self._tool_params(eline[match.end():])
            if 'C' in params:
                self._define_tool(st, int(match.group(1)), params['C'])
            elif params:
                self.app.log.warning("Tool without diameter in the header, ignored: %s" % eline)
            return

        # ## Units and number format # ##
        match = self.units_re.match(eline)
        if match:
            self._units_line(st, match)
            return

        # Search for units type again it might be alone on the line
        if "INCH" in eline:
            self._set_file_units(st, "IN")
            self.excellon_units_found = "IN"
            st.units_resolved = True
            self.app.log.warning("Type of UNITS found inline, in header: IN. Excellon format preset is: %s:%s" %
                                 (str(self.excellon_format_upper_in), str(self.excellon_format_lower_in)))
            return
        if "METRIC" in eline:
            self._set_file_units(st, "MM")
            self.excellon_units_found = "MM"
            st.units_resolved = True
            self.app.log.warning("Type of UNITS found inline, in header: MM. Excellon format preset is: %s:%s" %
                                 (str(self.excellon_format_upper_mm), str(self.excellon_format_lower_mm)))
            return

        # Search for zeros type again because it might be alone on the line
        match = re.search(r'[LT]Z', eline)
        if match:
            self.zeros = match.group()
            self._fmt_declared = True
            self.app.log.warning("Type of ZEROS found: %s" % self.zeros)
            return

        self.app.log.debug("Line ignored: %s" % eline)

    def _tool_params(self, text):
        """The parameters of a tool line (C diameter, F feed, S speed, B, H, Z), in any order."""
        params = {}
        for key, value in self.tool_param_re.findall(text):
            try:
                params[key] = float(value)
            except ValueError:
                pass
        return params

    def _define_tool(self, st, number, dia, select=False):
        """
        A tool definition: in the header, or inline in the body (T1C0.8).
        A tool that already drilled with another diameter is not changed: the new diameter gets a new tool.
        """
        key = st.tool_map.get(number, number)
        tool = self.tools.get(key)
        if tool is not None and abs(tool['tooldia'] - dia) > 1e-9 and (st.drills.get(key) or st.slots.get(key)):
            new_key = max(max(self.tools), max(st.tool_map.values(), default=0)) + 1
            st.tool_map[number] = new_key
            key = new_key
            tool = None
        if tool is None:
            tool = self.tools[key] = {'tooldia': dia, 'solid_geometry': []}
            if st.aper_plated is not None:
                tool['plated'] = st.aper_plated
            if st.attrs.ta:
                tool['attributes'] = {name: list(values) for name, values in st.attrs.ta.items()}
        else:
            tool['tooldia'] = dia
        self.app.log.debug("Tool definition: %s %s" % (key, dia))
        if select:
            st.tool = key

    def _use_tool(self, st):
        """The selected tool, created with a placeholder diameter if the file does not define it. None: no tool."""
        tool = st.tool
        if tool is None:
            return None
        if tool not in self.tools:
            self._placeholder_tool(tool)
        return tool

    def _placeholder_tool(self, number):
        # it's possible that the Excellon file has no tool diameters (those could be in another file like PCB Wizard
        # does). Each tool will have a slightly different diameter starting with a default value, to allow Excellon
        # editing after that
        self.diameterless = True
        if self.units == 'MM':
            diam = self.toolless_diam + (int(number) - 1) / 100
        else:
            diam = (self.toolless_diam + (int(number) - 1) / 100) / 25.4
        self.tools[number] = {'tooldia': diam, 'solid_geometry': []}
        self.app.inform.emit('[WARNING] %s%s %s' %
                             (_("No tool diameter info's. See shell.\n"
                                "A tool change event: T"),
                              str(number),
                              _("was found but the Excellon file "
                                "have no informations regarding the tool "
                                "diameters therefore the application will try to load it "
                                "by using some 'fake' diameters.\n"
                                "The user needs to edit the resulting Excellon object and "
                                "change the diameters to reflect the real diameters.")
                              )
                             )

    # ############################################################################################################
    # ########################################## body ############################################################
    # ############################################################################################################
    def _body_line(self, st, eline):
        # ## Units and number format outside the header # ##
        if eline.startswith(('INCH', 'METRIC')):
            match = self.units_re.match(eline)
            if match:
                self._units_line(st, match)
                return

        # ## Tool change ###
        match = self.toolsel_re.match(eline)
        if match:
            number = int(match.group(1))
            params = self._tool_params(eline[match.end():])
            if number == 0:
                # T0: no tool
                st.tool = None
            elif 'C' in params:
                # the tool is defined inline (headerless files, or a new diameter for the tool)
                self._define_tool(st, number, params['C'], select=True)
            else:
                st.tool = st.tool_map.get(number, number)
            self.app.log.debug("Tool change: %s" % st.tool)
            return

        # ## End of file ###
        if eline == 'M30':
            st.ended = True
            return

        # ## Allegro Type Tool change ###
        if st.allegro is True:
            if self.absinc_re.search(eline) or self.stop_re.search(eline):
                st.name_tool += 1
                st.tool = st.name_tool
                self.app.log.debug("Tool change for Allegro type of Excellon: %d" % st.tool)
                return

        if not self.line_re.match(eline):
            if not self.benign_re.match(eline):
                st.ignored += 1
            self.app.log.warning("Line ignored: %s" % eline)
            return

        # split the tokens: before the G85 (or all of them) and after it (the end of a drilled slot)
        gcodes = []
        mcodes = []
        pre = {}
        post = {}
        target = pre
        repeat = None
        for letter, value in self.token_re.findall(eline):
            if letter == 'G':
                gcodes.append(int(value) if value.isdigit() else -1)
                if gcodes[-1] == 85:
                    target = post
            elif letter == 'M':
                mcodes.append(int(value) if value.isdigit() else -1)
            elif letter == 'R':
                repeat = value
            else:
                target[letter] = value

        # ## M codes ##
        for code in mcodes:
            if code == 15:
                st.tool_down = True     # the cut is done by the G01 / G02 / G03
            elif code == 16 or code == 17:
                # tool up: end of the route
                st.tool_down = False
                st.mode = 0
            elif code == 25:
                st.recording = []
                st.pattern_origin = (st.cx or 0.0, st.cy or 0.0)
            elif code == 1:
                if st.recording is not None:
                    st.pattern = st.recording
                    st.recording = None
        if 2 in mcodes:
            # M02 X..Y..: repeat the pattern with that offset; M70 / M80 / M90 on the same line swap or mirror the copy
            orient = {code for code in mcodes if code in (70, 80, 90)}
            if pre or orient:
                self._pattern_repeat(st, pre, orient)
            return

        # ## G codes ##
        slot = False
        for code in gcodes:
            if code == 5:
                st.mode = 0
            elif code == 90:
                st.incremental = False
            elif code == 91:
                st.incremental = True
            elif code == 0 or code == 1 or code == 2 or code == 3:
                st.mode = 1
                st.gmode = code
            elif code == 85:
                slot = True
            elif code == 93:
                # set the zero: the next coordinates are relative to this point
                x = self._axis_value(pre.get('X'))
                y = self._axis_value(pre.get('Y'))
                if x is not None:
                    st.ox = x
                if y is not None:
                    st.oy = y
                return
            else:
                # dwell, cutter compensation and the others: nothing to drill
                self.app.log.debug("Line ignored: %s" % eline)
                return

        if not (pre or post) and repeat is None:
            return

        if not st.units_resolved:
            self._resolve_units(st)

        # ## Repeat of the last hole: R3X1.0 ##
        if repeat is not None:
            self._repeat_hole(st, repeat, pre)
            return

        # ## drilled slot (G85) ##
        if slot:
            self._slot_line(st, pre, post)
            return

        if st.mode == 0:
            self._drill_line(st, pre.get('X'), pre.get('Y'))
        else:
            self._route_line(st, pre)

    def _axis_value(self, token):
        """
        The value of a coordinate token: a number with a period is used literally, the others by the format.
        A file without a number format that already had a period uses the others as plain decimals (XNC: X8Y8).
        """
        if not token:
            return None
        try:
            if '.' in token:
                self._seen_period = True
                return float(token)
            if self._seen_period and not self._fmt_declared:
                return float(token)
            return self.parse_number(token)
        except ValueError:
            return None

    def _position(self, st, xt, yt):
        """
        The new position from the coordinate tokens (None: the axis keeps its modal value).
        It is stored as the modal position.
        """
        x = self._axis_value(xt)
        y = self._axis_value(yt)
        if x is not None:
            if st.incremental:
                x += st.cx if st.cx is not None else st.ox
            else:
                x += st.ox
            st.cx = x
        if y is not None:
            if st.incremental:
                y += st.cy if st.cy is not None else st.oy
            else:
                y += st.oy
            st.cy = y
        return st.cx, st.cy

    def _drill_line(self, st, xt, yt):
        if not st.units_resolved:
            self._resolve_units(st)
        x, y = self._position(st, xt, yt)
        if x is None or y is None:
            st.dropped += 1
            self.app.log.error("Missing coordinates")
            return
        self._emit_drill(st, x, y)

    @staticmethod
    def _orient(st, orient, x, y):
        """A point of the pattern swapped (M70) or mirrored (M80: X, M90: Y) about the pattern origin."""
        x0, y0 = st.pattern_origin
        x, y = x - x0, y - y0
        if 70 in orient:
            x, y = y, x
        if 80 in orient:
            x = -x
        if 90 in orient:
            y = -y
        return x + x0, y + y0

    def _emit_drill(self, st, x, y):
        tool = self._use_tool(st)
        if tool is None:
            st.dropped += 1
            return
        self._put_drill(st, tool, x, y)

    def _put_drill(self, st, tool, x, y, to=None):
        if to is None:
            to = st.attrs.to_id if st.attrs.to else 0
        if to:
            st.any_to = True
        drills = st.drills.get(tool)
        if drills is None:
            st.drills[tool] = [(x, y)]
            st.drills_to[tool] = [to]
        else:
            drills.append((x, y))
            st.drills_to[tool].append(to)
        if st.recording is not None:
            st.recording.append((tool, x, y, to))

    def _emit_slot(self, st, x0, y0, x1, y1):
        tool = self._use_tool(st)
        if tool is None:
            st.dropped += 1
            return
        self._put_slot(st, tool, x0, y0, x1, y1)

    def _put_slot(self, st, tool, x0, y0, x1, y1, to=None):
        if to is None:
            to = st.attrs.to_id if st.attrs.to else 0
        if to:
            st.any_to = True
        slots = st.slots.get(tool)
        if slots is None:
            st.slots[tool] = [(x0, y0, x1, y1)]
            st.slots_to[tool] = [to]
        else:
            slots.append((x0, y0, x1, y1))
            st.slots_to[tool].append(to)
        if st.recording is not None:
            st.recording.append((tool, x0, y0, x1, y1, to))

    def _slot_line(self, st, pre, post):
        """Drilled slot: X01125Y0022244G85Y0027756 (the start before the G85, the stop after it)."""
        x0, y0 = self._position(st, pre.get('X'), pre.get('Y'))
        x1, y1 = self._position(st, post.get('X'), post.get('Y'))
        if x0 is None or y0 is None or x1 is None or y1 is None:
            st.dropped += 1
            self.app.log.error("Slots are missing some or all coordinates.")
            return
        self._emit_slot(st, x0, y0, x1, y1)

    def _repeat_hole(self, st, count, pre):
        """R3X1.0Y0.5: the last hole is repeated 3 times, each one at the offset from the previous."""
        dx = self._axis_value(pre.get('X')) or 0
        dy = self._axis_value(pre.get('Y')) or 0
        if st.cx is None or st.cy is None:
            st.dropped += 1
            return
        x, y = st.cx, st.cy
        for _i in range(int(count)):
            x += dx
            y += dy
            self._emit_drill(st, x, y)
        st.cx, st.cy = x, y

    def _pattern_repeat(self, st, pre, orient):
        """
        M02X..Y..: the recorded pattern (M25 .. M01) is repeated at the offset.
        M70 / M80 / M90 on the same line swap or mirror the copy about the pattern origin.
        """
        dx = self._axis_value(pre.get('X')) or 0
        dy = self._axis_value(pre.get('Y')) or 0
        for event in list(st.pattern):
            if len(event) == 4:
                x, y = self._orient(st, orient, event[1], event[2]) if orient else (event[1], event[2])
                self._put_drill(st, event[0], x + dx, y + dy, event[3])
            else:
                x0, y0 = self._orient(st, orient, event[1], event[2]) if orient else (event[1], event[2])
                x1, y1 = self._orient(st, orient, event[3], event[4]) if orient else (event[3], event[4])
                self._put_slot(st, event[0], x0 + dx, y0 + dy, x1 + dx, y1 + dy, event[5])

    def _route_line(self, st, pre):
        """
        A routing move in route mode: G00 rapid, G01 linear, G02 / G03 arc (A radius, or I / J centre offsets).
        Every cut is stored as a slot, the arcs as short segments.
        """
        x0, y0 = st.cx, st.cy
        x1, y1 = self._position(st, pre.get('X'), pre.get('Y'))
        if x1 is None or y1 is None:
            st.dropped += 1
            self.app.log.error("Missing coordinates")
            return
        if st.gmode == 0 or st.tool_down is False or x0 is None or y0 is None:
            return

        if st.gmode == 1:
            self._emit_slot(st, x0, y0, x1, y1)
            return

        cw = st.gmode == 2
        di = self._axis_value(pre.get('I'))
        dj = self._axis_value(pre.get('J'))
        radius = self._axis_value(pre.get('A'))
        if radius:
            st.arc_radius = radius
        elif di is None and dj is None:
            radius = st.arc_radius      # the radius is modal
        if di is not None or dj is not None:
            center = (x0 + (di or 0.0), y0 + (dj or 0.0))
        elif radius:
            center = self._arc_center(x0, y0, x1, y1, radius, cw)
        else:
            center = None
        if center is None:
            self._emit_slot(st, x0, y0, x1, y1)
            return

        px, py = x0, y0
        for qx, qy in self._arc_points(x0, y0, x1, y1, center[0], center[1], cw):
            self._emit_slot(st, px, py, qx, qy)
            px, py = qx, qy

    @staticmethod
    def _arc_center(x0, y0, x1, y1, radius, cw):
        """
        The centre of the arc from (x0, y0) to (x1, y1) with the given radius. A positive radius is the arc of up
        to 180 degrees, a negative radius the bigger one.
        """
        dx = x1 - x0
        dy = y1 - y0
        chord = math.hypot(dx, dy)
        if chord == 0:
            return None
        r = max(abs(radius), chord / 2)
        h = math.sqrt(r * r - (chord / 2) ** 2)
        # the centre of the counterclockwise arc of up to 180 degrees is on the left of the chord
        sign = -1 if cw else 1
        if radius < 0:
            sign = -sign
        return (x0 + dx / 2 - sign * h * dy / chord,
                y0 + dy / 2 + sign * h * dx / chord)

    def circle_steps(self, radius):
        """
        The segments of a full circle of the given radius, in the units of this object: the fixed count that was given
        to the constructor, or what the preferences set (adaptive to the radius, or fixed).
        """
        # an object made without Excellon.__init__ (a copy, a stub) has no fixed count: the preferences apply
        fixed = getattr(self, '_fixed_steps', None)
        if fixed is not None:
            return fixed
        return object_circle_steps(self.app.options, 'excellon', radius, self.units)

    def _arc_points(self, x0, y0, x1, y1, cx, cy, cw):
        """The points of the arc after the start point, the last one is the end point."""
        r = math.hypot(x0 - cx, y0 - cy)
        a0 = math.atan2(y0 - cy, x0 - cx)
        a1 = math.atan2(y1 - cy, x1 - cx)
        full = 2 * math.pi
        if math.hypot(x1 - x0, y1 - y0) < 1e-12:
            sweep = -full if cw else full
        elif cw:
            sweep = -((a0 - a1) % full)
        else:
            sweep = (a1 - a0) % full
        steps = max(4, self.circle_steps(r))
        n = max(1, math.ceil(abs(sweep) / full * steps))
        points = [(cx + r * math.cos(a0 + sweep * k / n), cy + r * math.sin(a0 + sweep * k / n))
                  for k in range(1, n)]
        points.append((x1, y1))
        return points

    # ############################################################################################################
    # ######################################### the end ##########################################################
    # ############################################################################################################
    def _finish_parse(self, st):
        """The file is read: store the holes, the plated flags, merge the tools of the same diameter, warn."""
        if not st.units_resolved and self.excellon_units_found is None and self.tools:
            self._vote_units()

        self._store_holes(st)

        self.attributes = st.attrs.as_legacy()
        self.to_attributes = st.attrs.to_table()
        self.file_attributes = file_attributes(st.attrs.tf)

        if st.file_plated is not None:
            for tool in self.tools.values():
                tool.setdefault('plated', st.file_plated)

        if self.merge_equal_tools:
            self.merge_equal_diameter_tools()

        if st.ignored or st.dropped:
            self.app.log.warning("Excellon: %d lines ignored, %d holes skipped." % (st.ignored, st.dropped))
            self.app.inform.emit('[WARNING] %s' % (
                _("Some lines of the Excellon file were not used: {ignored} lines were not understood, "
                  "{dropped} holes were skipped (no tool selected or missing coordinates). See shell.").format(
                    ignored=st.ignored, dropped=st.dropped)))

    def _store_holes(self, st):
        """
        The drills and the slots were collected as numbers; here they become shapely Points, one call per tool.
        The data structure of the Excellon object has to include both the 'drills' and the 'slots' keys otherwise
        I will need to test for them everywhere. Even if there are not drills or slots there is an empty list.
        """
        st.stored = True
        for tool, pts in st.drills.items():
            self.tools[tool]['drills'] = list(shapely.points(np.asarray(pts, dtype=float)))
        for tool, segs in st.slots.items():
            arr = np.asarray(segs, dtype=float)
            self.tools[tool]['slots'] = list(zip(shapely.points(arr[:, 0:2]), shapely.points(arr[:, 2:4])))
        for tool in self.tools:
            if 'drills' not in self.tools[tool]:
                self.tools[tool]['drills'] = []
            if 'slots' not in self.tools[tool]:
                self.tools[tool]['slots'] = []
            if st.any_to:
                # the TO ids, parallel to the drills and the slots (0: no object attribute)
                self.tools[tool]['drills_to'] = list(st.drills_to.get(tool, []))
                self.tools[tool]['slots_to'] = list(st.slots_to.get(tool, []))

    def merge_equal_diameter_tools(self):
        """
        Tools with the same diameter (and the same plated flag) become one tool: the one with the lowest number,
        with the drills and the slots of all of them.
        """
        keepers = {}
        for key in sorted(self.tools):
            tool = self.tools[key]
            signature = (round(tool['tooldia'], 6), tool.get('plated'))
            keeper = keepers.get(signature)
            if keeper is None:
                keepers[signature] = key
                continue
            self.tools[keeper]['drills'].extend(tool['drills'])
            self.tools[keeper]['slots'].extend(tool['slots'])
            for key_to in ('drills_to', 'slots_to'):
                if key_to in self.tools[keeper] and key_to in tool:
                    self.tools[keeper][key_to].extend(tool[key_to])
            del self.tools[key]

    def parse_number(self, number_str):
        """
        Parses coordinate numbers without period.

        :param number_str: String representing the numerical value.
        :type number_str: str
        :return: Floating point representation of the number
        :rtype: float
        """

        # the divisor depends only on the zeros / units / format (and on the number of digits for leading zeros)
        key = (self.zeros, self.units, self.excellon_format_upper_in, self.excellon_format_upper_mm,
               self.excellon_format_lower_in, self.excellon_format_lower_mm)
        cache = self.__dict__.get('_number_cache')
        if cache is None or cache[0] != key:
            leading = self.zeros == "L" or self.zeros == "LZ"
            inch = self.units.lower() == "in"
            if leading:
                # With leading zeros, when you type in a coordinate, the leading zeros must always be included.
                # Trailing zeros are unneeded and may be left off. The CNC-7 will automatically add them.
                # 6 digits are divided by 10^4. If less than size digits, they are automatically added,
                # 5 digits then are divided by 10^3 and so on.
                value = float(self.excellon_format_upper_in if inch else self.excellon_format_upper_mm)
            else:
                # You must show all zeros to the right of the number and can omit all zeros to the left of the
                # number. The CNC-7 will count the number of digits you typed and automatically fill in the
                # missing zeros. FlatCAM expects the number of digits entered into the defaults
                value = 10 ** float(self.excellon_format_lower_in if inch else self.excellon_format_lower_mm)
            cache = self._number_cache = (key, leading, value)

        _key, leading, value = cache
        try:
            # None raises TypeError here: the callers use it as "no value, keep the modal one"
            sign = 1 if number_str[:1] in ('+', '-') else 0
            if leading:
                nr_length = len(number_str) - sign
                return float(number_str) / (10 ** (float(nr_length) - value))
            return float(number_str) / value
        except TypeError:
            raise
        except Exception as e:
            self.app.log.error("Aborted. Operation could not be completed due of %s" % str(e))
            return

    def add_tool(self, dia, drills=(), slots=(), data=None, tool_id=None):
        """
        Adds a tool to ``self.tools`` and returns its id.

        Contract (shared by all the callers that used to build the dict by hand):

        - id: ``tool_id`` when given (any hashable, e.g. the string ``'1'``), else ``max(int(k)) + 1`` over the
          existing ids, or ``1`` when there are no tools. A ``tool_id`` already in ``self.tools`` raises ValueError.
        - the new entry has exactly the keys ``tooldia`` (float), ``drills`` (list of ``Point``, a copy of the
          argument), ``slots`` (list of ``(Point, Point)`` tuples, a copy), ``solid_geometry`` (always an empty list)
          and ``data``.
        - ``data`` is a deepcopy of the argument, or a deepcopy of ``self.default_data`` (``{}`` when the attribute
          does not exist) when the argument is None. Pass ``{}`` to get an empty ``data`` (an empty ``data`` is
          filled from ``default_data`` by ``create_geometry``; a non-empty one is kept).
        - it does NOT call ``create_geometry``: ``solid_geometry`` stays empty until the caller calls it.
        - the arguments are not validated: drills must already be ``Point`` and slots ``(Point, Point)`` tuples.

        :param dia:         Tool diameter.
        :param drills:      Iterable of shapely Points.
        :param slots:       Iterable of (start Point, stop Point) tuples.
        :param data:        Tool data dict, or None for the default data.
        :param tool_id:     Explicit tool id, or None for the next free integer id.
        :return:            The id of the new tool.
        """

        if tool_id is None:
            tool_id = max((int(key) for key in self.tools), default=0) + 1
        elif tool_id in self.tools:
            raise ValueError("Excellon tool id %r already exists" % (tool_id,))

        if data is None:
            data = getattr(self, 'default_data', {})

        self.tools[tool_id] = {
            'tooldia': float(dia),
            'drills': list(drills),
            'slots': [tuple(slot) for slot in slots],
            'solid_geometry': [],
            'data': deepcopy(data)
        }
        return tool_id

    @staticmethod
    def _buffer_drills(drills, radius, quad_segs):
        """
        The circles of the drill points: the circle of one point is buffered once and moved to every centre with
        numpy (same result as Point.buffer(radius, quad_segs)).
        """
        if not drills:
            return []
        arr = np.empty(len(drills), dtype=object)
        arr[:] = drills
        if radius > 0 and not shapely.has_z(arr).any() and not shapely.is_empty(arr).any() and \
                (shapely.get_type_id(arr) == 0).all():
            template = shapely.get_coordinates(shapely.buffer(Point(0.0, 0.0), radius, quad_segs=quad_segs))
            if len(template) >= 4:
                centers = shapely.get_coordinates(arr)
                return list(shapely.polygons(template[None, :, :] + centers[:, None, :]))
        return list(shapely.buffer(arr, radius, quad_segs=quad_segs))

    @staticmethod
    def _buffer_slots(slots, radius, quad_segs):
        """
        One vectorized shapely.buffer for all the slots (same result as LineString([start, stop]).buffer(radius,
        quad_segs)). Slots with Z coordinates or that are not 2 plain Points go through the per-slot path.
        """
        if not slots:
            return []
        try:
            ends = np.empty(2 * len(slots), dtype=object)
            ends[0::2] = [slot[0] for slot in slots]
            ends[1::2] = [slot[1] for slot in slots]
            if shapely.has_z(ends).any() or shapely.is_empty(ends).any() or                     not (shapely.get_type_id(ends) == 0).all():
                raise ValueError('not plain 2D points')
            lines = shapely.linestrings(shapely.get_coordinates(ends).reshape(len(slots), 2, 2))
            return list(shapely.buffer(lines, radius, quad_segs=quad_segs))
        except ValueError:
            return [LineString([slot[0], slot[1]]).buffer(radius, quad_segs=quad_segs) for slot in slots]

    def create_geometry(self):
        """
        Creates circles of the tool diameter at every point
        specified in self.tools[tool]['drills'].
        Also creates geometries (polygons)
        for the slots as specified in self.tools[tool]['slots']
        All the resulting geometry is stored into self.solid_geometry list.
        The list self.solid_geometry has 2 elements: first is a dict with the drills geometry,
        and second element is another similar dict that contain the slots geometry.

        Each dict has as keys the tool diameters and as values lists with Shapely objects, the geometries
        ================  ====================================
        Key               Value
        ================  ====================================
        tool_diameter     list of (Shapely.Point) Where to drill
        ================  ====================================

        :return: None
        """

        self.app.log.debug("appParsers.ParseExcellon.Excellon.create_geometry()")
        self.solid_geometry = []
        try:
            # clear the solid_geometry in self.tools
            for tool in self.tools:
                self.tools[tool]['solid_geometry'] = []

            for tool in self.tools:
                tooldia = self.tools[tool]['tooldia']

                # keep the existing tool data; only a tool without data gets the defaults
                if not self.tools[tool].get('data'):
                    self.tools[tool]['data'] = deepcopy(getattr(self, 'default_data', {}))

                radius = tooldia / 2.0
                # the drills and the slots of one tool have the same segments
                quad_segs = circle_quad_segs(self.circle_steps(radius))
                tool_geo = self.tools[tool]['solid_geometry']

                if 'drills' in self.tools[tool]:
                    polys = Excellon._buffer_drills(self.tools[tool]['drills'], radius, quad_segs)
                    tool_geo.extend(polys)
                    self.solid_geometry.extend(polys)

                if 'slots' in self.tools[tool]:
                    polys = Excellon._buffer_slots(self.tools[tool]['slots'], radius, quad_segs)
                    tool_geo.extend(polys)
                    self.solid_geometry.extend(polys)

        except Exception as e:
            err_msg = "appParsers.ParseExcellon.Excellon.create_geometry() -> " \
                      "Excellon geometry creation failed due of ERROR: %s" % str(e)
            self.app.log.error(err_msg)
            return "fail"

    def bounds(self, flatten=None):
        """
        Returns coordinates of rectangular bounds
        of Excellon geometry: (xmin, ymin, xmax, ymax).

        :param flatten:     No used
        """

        self.app.log.debug("appParsers.ParseExcellon.Excellon.bounds()")

        if self.solid_geometry is None or not self.tools:
            self.app.log.debug("appParsers.ParseExcellon.Excellon -> solid_geometry is None")
            return 0, 0, 0, 0

        def bounds_rec(obj):
            if type(obj) is list:
                minx = np.inf
                miny = np.inf
                maxx = -np.inf
                maxy = -np.inf

                for k in obj:
                    if type(k) is dict:
                        for key in k:
                            minx_, miny_, maxx_, maxy_ = bounds_rec(k[key])
                            minx = min(minx, minx_)
                            miny = min(miny, miny_)
                            maxx = max(maxx, maxx_)
                            maxy = max(maxy, maxy_)
                    else:
                        minx_, miny_, maxx_, maxy_ = bounds_rec(k)
                        minx = min(minx, minx_)
                        miny = min(miny, miny_)
                        maxx = max(maxx, maxx_)
                        maxy = max(maxy, maxy_)
                return minx, miny, maxx, maxy
            else:
                # it's a Shapely object, return it's bounds
                return obj.bounds

        _bounds_rec_slow = bounds_rec

        def bounds_rec(obj):
            res = list_total_bounds(obj)
            return _bounds_rec_slow(obj) if res is None else res

        minx_list = []
        miny_list = []
        maxx_list = []
        maxy_list = []

        for tool in self.tools:
            eminx, eminy, emaxx, emaxy = bounds_rec(self.tools[tool]['solid_geometry'])
            minx_list.append(eminx)
            miny_list.append(eminy)
            maxx_list.append(emaxx)
            maxy_list.append(emaxy)

        return min(minx_list), min(miny_list), max(maxx_list), max(maxy_list)

    def convert_units(self, units):
        """
        This function first convert to the the units found in the Excellon file but it converts tools that
        are not there yet so it has no effect other than it signal that the units are the ones in the file.

        On object creation, in app_obj.new_object(), true conversion is done because this is done at the end of the
        Excellon file parsing, the tools are inside and self.tools is really converted from the units found
        inside the file to the FlatCAM units.

        Kind of convolute way to make the conversion and it is based on the assumption that the Excellon file
        will have detected the units before the tools are parsed and stored in self.tools

        :param units:   'IN' or 'MM'. String

        :return:
        """

        # factor = Geometry.convert_units(self, units)
        obj_units = units
        if obj_units.upper() == self.units.upper():
            factor = 1.0
        elif obj_units.upper() == "MM":
            factor = 25.4
        elif obj_units.upper() == "IN":
            factor = 1 / 25.4
        else:
            self.app.log.error("Unsupported units: %s" % str(obj_units))
            factor = 1.0
        self.app.log.debug("appParsers.ParseExcellon.Excellon.convert_units() --> Factor: %s" % str(factor))

        self.units = obj_units
        self.file_units_factor = factor

        # the drills and the slots move, the tool diameters change units here, 1 time; the geometry is made once
        if factor != 1.0:
            Excellon._transform_tools(self, affine_scale_matrix(factor, factor, (0, 0)))
            self.app.proc_container.new_text = ''
        for tool in self.tools:
            self.tools[tool]['tooldia'] *= factor

        self.create_geometry()
        return factor

    def _transform_tools(self, matrix, transform_solid=False):
        """
        Apply the affine matrix (a, b, d, e, xoff, yoff) to the drills and to the slot ends of every tool with ONE
        vectorized call (camlib.affine_geometry_many). With transform_solid the per tool solid_geometry is moved in
        the same call (only for the transforms that do not recreate the geometry afterwards).

        :param matrix:          the affine matrix, see camlib.affine_geometry_many
        :param transform_solid: also transform self.tools[tool]['solid_geometry']
        """
        tools = list(self.tools.values())
        items = []
        for tool in tools:
            items.append(tool['drills'] if 'drills' in tool else [])
            starts, stops = [], []
            for slot in (tool['slots'] if 'slots' in tool else []):
                starts.append(slot[0])
                stops.append(slot[1])
            items.append(starts)
            items.append(stops)
            if transform_solid:
                items.append(tool.get('solid_geometry', []))

        if transform_solid:
            items.append(self.solid_geometry)

        out = affine_geometry_many(items, matrix)
        if transform_solid:
            self.solid_geometry = out[-1]

        step = 4 if transform_solid else 3
        for idx, tool in enumerate(tools):
            base = idx * step
            if 'drills' in tool:
                tool['drills'] = out[base]
            if 'slots' in tool:
                tool['slots'] = list(zip(out[base + 1], out[base + 2]))
            if transform_solid:
                tool['solid_geometry'] = out[base + 3]

        if tools:
            self.app.proc_container.update_view_text(' %d%%' % 100)

    def scale(self, xfactor, yfactor=None, point=None):
        """
        Scales geometry on the XY plane in the object by a given factor.
        Drill and slot positions use the signed factors. The tool diameters are not changed: a drill is a physical
        tool, and a scale is used to move the holes, for example to correct a file read with a wrong number format.
        convert_units() changes the diameters itself. Feedrates and Z-plane dimensions are untouched.

        :param xfactor:     Number by which to scale the object.
        :type xfactor:      float
        :param yfactor:     Number by which to scale the object.
        :type yfactor:      float
        :param point:       Origin point for scale
        :return:            None
        :rtype:             None
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.scale()")

        if yfactor is None:
            yfactor = xfactor

        if point is None:
            px = 0
            py = 0
        else:
            px, py = point

        if xfactor == 0 and yfactor == 0:
            return

        Excellon._transform_tools(self, affine_scale_matrix(xfactor, yfactor, (px, py)))

        self.create_geometry()
        self.app.proc_container.new_text = ''

    def offset(self, vect):
        """
        Offsets geometry on the XY plane in the object by a given vector.

        :param vect: (x, y) offset vector.
        :type vect: tuple
        :return: None
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.offset()")

        dx, dy = vect

        if dx == 0 and dy == 0:
            return

        # Translation preserves hole/slot outlines: move the solid_geometry too, don't buffer every drill again.
        Excellon._transform_tools(self, affine_translate_matrix(dx, dy), transform_solid=True)
        self.app.proc_container.new_text = ''

    def mirror(self, axis, point):
        """

        :param axis:        "X" or "Y" indicates around which axis to mirror.
        :type axis:         str
        :param point:       [x, y] point belonging to the mirror axis.
        :type point:        list
        :return:            None
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.mirror()")

        px, py = point

        Excellon._transform_tools(self, affine_mirror_matrix(axis, (px, py)))

        # Recreate geometry
        self.create_geometry()
        self.app.proc_container.new_text = ''

    def skew(self, angle_x=None, angle_y=None, point=None):
        """
        Shear/Skew the geometries of an object by angles along x and y dimensions.
        Tool sizes, feedrates an Z-plane dimensions are untouched.

        :param angle_x:
        :param angle_y:
            The shear angle(s) for the x and y axes respectively. These can be
            specified in either degrees (default) or radians by setting
            use_radians=True.
        :param point:       Origin point for Skew

        See shapely manual for more information:
        http://toblerity.org/shapely/manual.html#affine-transformations
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.skew()")

        if angle_x is None:
            angle_x = 0.0

        if angle_y is None:
            angle_y = 0.0

        if angle_x == 0 and angle_y == 0:
            return

        if point is None:
            px, py = 0, 0
        else:
            px, py = point

        Excellon._transform_tools(self, affine_skew_matrix(angle_x, angle_y, (px, py)))

        self.create_geometry()
        self.app.proc_container.new_text = ''

    def rotate(self, angle, point=None):
        """
        Rotate the geometry of an object by an angle around the 'point' coordinates

        :param angle:
        :param point:   tuple of coordinates (x, y)
        :return:        None
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.rotate()")

        if angle == 0:
            return

        if point is None:
            minx, miny, maxx, maxy = self.bounds()
            orig = ((minx + maxx) / 2.0, (miny + maxy) / 2.0)
        else:
            orig = point

        Excellon._transform_tools(self, affine_rotate_matrix(angle, orig))

        self.create_geometry()
        self.app.proc_container.new_text = ''

    def buffer(self, distance, join, factor, only_exterior=False, muted=False):
        """

        :param distance:        if 'factor' is True then distance is the factor
        :param factor:          truthy: multiplicative mode, distance is the multiplier; None or False: distance mode
        :param join:            The type of line joint used by the shapely buffer method: round, square, bevel
        :param only_exterior:   Bool. If True, the LineStrings are buffered only on the outside
        :return:                None on success or a no-op; 'fail' when nothing was changed
        """
        self.app.log.debug("appParsers.ParseExcellon.Excellon.buffer()")

        try:
            finite_distance = bool(np.isfinite(distance))
        except (TypeError, ValueError):
            finite_distance = False

        if factor:
            # -100% gives a factor of 0 and no hole at all: the same rejection as a negative diameter
            try:
                valid_factor = finite_distance and distance > 0
            except TypeError:
                valid_factor = False
            if not valid_factor:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                         (_("Excellon buffer by a factor was not done"),
                                          _("The factor must be a positive number.")))
                return 'fail'
        else:
            if not finite_distance:
                if muted is False:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % _("The buffer distance is not a number."))
                return 'fail'
            if distance == 0:
                return

        # create_geometry() below rebuilds every drill / slot polygon from the tool diameter, so only the diameters
        # change here (buffering the old polygons first would be thrown away). Validate every candidate diameter
        # before the first change: a diameter <= 0 exports C-0.2 and empty geometry.
        old_diameters = {}
        new_diameters = {}
        try:
            for tool in self.tools:
                tooldia = self.tools[tool]['tooldia']
                old_diameters[tool] = tooldia
                new_diameters[tool] = tooldia + (distance * 2) if not factor else tooldia * distance
            valid_diameters = all(np.isfinite(dia) and dia > 0 for dia in new_diameters.values())
        except (TypeError, ValueError, KeyError):
            valid_diameters = False

        if not valid_diameters:
            if muted is False:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                     (_("Excellon buffer was not done"),
                                      _("A tool diameter would become zero or negative.")))
            return 'fail'

        # factor 1 (or a distance that leaves every diameter as it is) changes nothing: no rebuild
        if all(new_diameters[tool] == old_diameters[tool] for tool in new_diameters):
            return

        # The exact state that create_geometry() replaces. Restoring it is the rollback: rebuilding the old
        # geometry would call the same failing routine again.
        try:
            old_geometry = list(self.solid_geometry)
            old_tool_geometry = {tool: list(self.tools[tool].get('solid_geometry', [])) for tool in self.tools}
            old_data = {tool: (self.tools[tool].get('data'), 'data' in self.tools[tool])
                        for tool in self.tools}
        except (TypeError, KeyError, AttributeError):
            if muted is False:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                     (_("Excellon buffer was not done"),
                                      _("The tool data is not valid.")))
            return 'fail'

        for tool in self.tools:
            self.tools[tool]['tooldia'] = new_diameters[tool]

        try:
            failed = self.create_geometry() == 'fail'
        except Exception as e:
            self.app.log.error("appParsers.ParseExcellon.Excellon.buffer() -> create_geometry failed: %s" % str(e))
            failed = True

        if failed:
            # atomic rollback: the exact previous diameters, tool data and geometry (no rebuild in a failing path)
            for tool in self.tools:
                self.tools[tool]['tooldia'] = old_diameters[tool]
                self.tools[tool]['solid_geometry'] = old_tool_geometry[tool]
                data, had_data = old_data[tool]
                if had_data:
                    self.tools[tool]['data'] = data
                else:
                    self.tools[tool].pop('data', None)
            self.solid_geometry = old_geometry
            if muted is False:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Excellon buffer failed."))
            return 'fail'
        self.app.proc_container.new_text = ''
