# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# File Author: Marius Adrian Stanciu (c)                   #
# Date: 4/23/2019                                          #
# MIT Licence                                              #
# ##########################################################

from PyQt6 import QtWidgets, QtCore     # noqa
from appTool import AppTool
from appGUI.GUIElements import add_case_variants

import logging
from copy import deepcopy
import os
import time
import re
import traceback

from shapely import Point
from appPlugins.ToolPdf.PdfGen import (
    _normalize_ap_dict, _paint_elements, _replay_paint_order, _region_tools, _circular_opening, drill_points
)

import gettext
import appTranslation as fcTranslate
import builtins

from appParsers.ParsePDF import PdfParser
from appParsers.PdfStreamParser import (
    get_pdf_parser,
    PdfParserError,
)
from camlib import grace, flatten_shapely_geometry

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

log = logging.getLogger('base')


class ToolPDF(AppTool):
    """
    Parse a PDF file.
    Reference here: https://www.adobe.com/content/dam/acom/en/devnet/pdf/pdfs/pdf_reference_archives/PDFReference.pdf
    Return a list of geometries
    """
    pluginName = _("PDF Import Tool")

    def __init__(self, app):
        AppTool.__init__(self, app)
        self.app = app
        self.decimals = self.app.decimals

        self.stream_re = re.compile(b'.*?FlateDecode.*?stream(.*?)endstream', re.S)

        self.pdf_decompressed = {}

        # key = file name and extension
        # value is a dict to store the parsed content of the PDF
        self.pdf_parsed = {}

        # QTimer for periodic check
        self.check_thread = QtCore.QTimer()

        # Every time a parser is started we add a promise; every time a parser finished we remove a promise
        # when empty we start the layer rendering
        self.parsing_promises = []

        self.parser = PdfParser(units=self.app.app_units,
                                resolution=self.app.options.get(
                                    "gerber_circle_steps",
                                    self.app.defaults.get(
                                        "gerber_circle_steps",
                                        self.app.defaults.factory_defaults.get("gerber_circle_steps")
                                    )
                                ),
                                # read while a file is parsed, so the Stop button can cancel a long parse
                                abort=lambda: self.app.abort_flag,
                                hole_detection_mode='both',  # Default: detect on both stroke and fill
                                options=self.app.options)

        # Initialize PDF stream parser based on defaults.py configuration
        # pdf_python_parser=True (default) -> pure-python parser
        # pdf_python_parser=False -> pikepdf parser (lazy import)
        self.pdf_stream_parser = get_pdf_parser(self.app.defaults)
        self.app.log.info(f"PDF stream parser initialized: {self.pdf_stream_parser.name}")

    def run(self, toggle=True):
        self.app.defaults.report_usage("ToolPDF()")

        self.set_tool_ui()
        self.on_open_pdf_click()

    def install(self, icon=None, separator=None, **kwargs):
        AppTool.install(self, icon, separator, shortcut='', **kwargs)

    def set_tool_ui(self):
        pass

    def on_open_pdf_click(self):
        """
        File menu callback for opening an PDF file.

        :return: None
        """

        self.app.defaults.report_usage("ToolPDF.on_open_pdf_click()")
        self.app.log.debug("ToolPDF was called. Launching....")
        self.app.log.info(f"ToolPDF PDF: using stream parser: {self.pdf_stream_parser.name}")

        _filter_ = add_case_variants("Adobe PDF Files (*.pdf);;All Files (*.*)")

        try:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open PDF"),
                                                                   directory=self.app.get_last_folder(),
                                                                   filter=_filter_)
        except TypeError:
            filenames, _f = QtWidgets.QFileDialog.getOpenFileNames(caption=_("Open PDF"), filter=_filter_)

        if len(filenames) == 0:
            self.app.inform.emit('[WARNING_NOTCL] %s.' % _("Open PDF cancelled"))
        else:
            # start the parsing timer with a period of 1 second
            self.periodic_check(1000)

            for filename in filenames:
                if filename != '':
                    self.app.worker_task.emit({'fcn': self.open_pdf, 'params': [filename]})

    def open_pdf(self, filename):
        if not os.path.exists(filename):
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("File no longer available."))
            return

        # Check if parser is available (should always be, since pure-python is always available)
        if not self.pdf_stream_parser.is_available():
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Failed."))
            self.app.log.error(f"PDF parser '{self.pdf_stream_parser.name}' is not available")
            return

        short_name = filename.split('/')[-1].split('\\')[-1]
        self.parsing_promises.append(short_name)

        self.pdf_parsed[short_name] = {
            'pdf': {},
            'filename': filename
        }

        self.pdf_decompressed[short_name] = ''

        if self.app.abort_flag:
            # graceful abort requested by the user
            self.parsing_promises.remove(short_name)
            raise grace

        with self.app.proc_container.new('%s...' % _("Parsing")):
            # Use pluggable parser instead of direct pikepdf
            try:
                decomp_file = self.pdf_stream_parser.extract_content_streams(filename)
                self.pdf_decompressed[short_name] = decomp_file
                
            except PdfParserError as e:
                self.app.log.error(f"PDF parsing failed: {str(e)}")
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open PDF"), str(e)))
                self.parsing_promises.remove(short_name)
                return
            except Exception as e:
                self.app.log.error(f"Unexpected PDF parsing error: {str(e)}")
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open PDF"), str(e)))
                self.parsing_promises.remove(short_name)
                return

            if self.pdf_decompressed[short_name] == '':
                self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Failed to open"), str(filename)))
                self.app.log.debug("ToolPDF.open_pdf().obj_init() --> Empty file or error on decompression")
                self.parsing_promises.remove(short_name)
                return

            try:
                self.pdf_parsed[short_name]['pdf'] = self.parser.parse_pdf(
                    pdf_content=self.pdf_decompressed[short_name])
            except grace:
                # Stop: this file is not imported; without its promise the rendering of a next import can start
                self.parsing_promises.remove(short_name)
                self.pdf_decompressed[short_name] = None
                raise
            unsupported = getattr(self.parser, 'unsupported_features', set())
            if unsupported:
                self.app.inform.emit('[WARNING_NOTCL] %s: %s' %
                                     (_("Unsupported PDF content"), ', '.join(sorted(unsupported))))
            # we used it, now we delete it
            if self.pdf_decompressed[short_name]:
                self.pdf_decompressed[short_name] = None

        # removal from list is done in a multithreaded way therefore not always the removal can be done
        # try to remove until it's done
        try:
            while True:
                self.parsing_promises.remove(short_name)
                time.sleep(0.1)
        except Exception as e:
            self.app.log.error("ToolPDF.open_pdf() --> %s" % str(e))
        self.app.inform.emit('[success] %s: %s' % (_("Opened"),  str(filename)))

    def layer_rendering_as_excellon(self, filename, ap_dict, layer_nr):
        outname = filename.split('/')[-1].split('\\')[-1] + "_%s" % str(layer_nr)
        ap_dict = _normalize_ap_dict(ap_dict)
        points = drill_points(ap_dict, getattr(self.app, 'app_units', 'MM'))
        if not points:
            # white paint without round holes: no drill object
            self.app.log.debug("ToolPDF --> layer %s has no drills; no object." % str(layer_nr))
            return

        def obj_init(new_obj, app_obj):
            new_obj.tools = {}
            sorted_dia = sorted(points.keys())

            name_tool = 0
            for dia in sorted_dia:
                name_tool += 1
                tool = str(name_tool)

                # the drill list comes from the points that have this diameter
                new_obj.add_tool(dia, drills=[Point(pt) for pt in points[dia]], tool_id=tool)

            ret = new_obj.create_geometry()
            if ret == 'fail':
                self.app.log.debug("Could not create geometry for Excellon object.")
                return "fail"

            new_obj.regenerate_source(name=outname)

            for tool in new_obj.tools:
                if new_obj.tools[tool]['solid_geometry']:
                    return
            app_obj.inform.emit('[ERROR_NOTCL] %s: %s' % (_("No geometry found in file"), outname))
            return "fail"

        with self.app.proc_container.new(_("Rendering PDF layer #%d ...") % int(layer_nr)):

            ret_val = self.app.app_obj.new_object("excellon", outname, obj_init, autoselected=False)
            if ret_val == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Open PDF file failed.'))
                return
            # Register recent file
            self.app.file_opened.emit("pdf", filename)
            # GUI feedback
            self.app.inform.emit('[success] %s: %s' % (_("Rendered"),  outname))

    def layer_rendering_as_gerber(self, filename, ap_dict, layer_nr):
        outname = filename.split('/')[-1].split('\\')[-1] + "_%s" % str(layer_nr)
        ap_dict = _normalize_ap_dict(ap_dict)
        poly_buff, follow_buf = _replay_paint_order(ap_dict)
        if poly_buff.is_empty:
            # later white paint erased the whole layer: no object
            self.app.log.debug("ToolPDF --> layer %s is empty after the paint order; no object." %
                               str(layer_nr))
            return

        def obj_init(grb_obj, app_obj):
            nonlocal poly_buff
            try:
                poly_buff = poly_buff.buffer(0.0000001)
            except ValueError:
                pass
            try:
                poly_buff = poly_buff.buffer(-0.0000001)
            except ValueError:
                pass

            grb_obj.solid_geometry = flatten_shapely_geometry(poly_buff)
            grb_obj.follow_geometry = deepcopy(follow_buf)
            grb_obj.tools = _region_tools(poly_buff)
            grb_obj.regenerate_source(name=outname)

        with self.app.proc_container.new(_("Rendering PDF layer #%d ...") % int(layer_nr)):

            ret = self.app.app_obj.new_object('gerber', outname, obj_init, autoselected=False)
            if ret == 'fail':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _('Open PDF file failed.'))
                return
            # Register recent file
            self.app.file_opened.emit('pdf', filename)
            # GUI feedback
            self.app.inform.emit('[success] %s: %s' % (_("Rendered"), outname))

    def periodic_check(self, check_period):
        """
        This function starts an QTimer, and it will periodically check if parsing was done

        :param check_period: time at which to check periodically if all plots finished to be plotted
        :return:
        """

        # self.plot_thread = threading.Thread(target=lambda: self.check_plot_finished(check_period))
        # self.plot_thread.start()
        self.app.log.debug("ToolPDF --> Periodic Check started.")

        try:
            self.check_thread.stop()
        except TypeError:
            pass

        self.check_thread.setInterval(check_period)
        try:
            self.check_thread.timeout.disconnect(self.periodic_check_handler)
        except (TypeError, AttributeError):
            pass

        self.check_thread.timeout.connect(self.periodic_check_handler)
        self.check_thread.start()

    def periodic_check_handler(self):
        """
        If the parsing worker finished then start multithreaded rendering
        :return:
        """
        # log.debug("checking parsing --> %s" % str(self.parsing_promises))

        try:
            if not self.parsing_promises:
                self.check_thread.stop()
                self.app.log.debug("PDF --> start rendering")
                # parsing finished start the layer rendering
                if self.pdf_parsed:
                    obj_to_delete = []
                    for object_name in self.pdf_parsed:
                        if self.app.abort_flag:
                            # graceful abort requested by the user
                            raise grace

                        filename = deepcopy(self.pdf_parsed[object_name]['filename'])
                        pdf_content = deepcopy(self.pdf_parsed[object_name]['pdf'])
                        obj_to_delete.append(object_name)
                        for k in pdf_content:
                            if self.app.abort_flag:
                                # graceful abort requested by the user
                                raise grace

                            ap_dict = pdf_content[k]

                            if ap_dict:
                                layer_nr = k
                                if k == 0:
                                    self.app.worker_task.emit({'fcn': self.layer_rendering_as_excellon,
                                                               'params': [filename, ap_dict, layer_nr]})
                                else:
                                    self.app.worker_task.emit({'fcn': self.layer_rendering_as_gerber,
                                                               'params': [filename, ap_dict, layer_nr]})
                    # delete the object already processed, so it will not be processed again for other objects
                    # that were opened at the same time; like in drag & drop on appGUI
                    for obj_name in obj_to_delete:
                        if obj_name in self.pdf_parsed:
                            self.pdf_parsed.pop(obj_name)

                self.app.log.debug("ToolPDF --> Periodic check finished.")
        except Exception:
            traceback.print_exc()
