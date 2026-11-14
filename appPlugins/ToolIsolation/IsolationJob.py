"""Isolation run adapter: resolved settings, geometry generation and object publication.

The controller owns widgets and canvas selection. IsolationGen owns the geometry kernels.
"""

from copy import deepcopy
from functools import partial
from hashlib import sha256
import math

import shapely
from shapely import Polygon, MultiPolygon

from defaults import AppDefaults
from camlib import grace, flatten_shapely_geometry, clip_paths_to_area, area_outline_paths, \
    boundary_passes_message, pass_covers_area
from appPlugins.ToolIsolation import IsolationGen

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _pref(app, key):
    """A preference as a run starts: the options, then the app defaults, then the factory defaults."""
    defaults = getattr(app, 'defaults', None) or AppDefaults.factory_defaults
    return app.options.get(key, defaults.get(key, AppDefaults.factory_defaults.get(key)))


def pad_report_message(report):
    """The message of the Voronoi pad isolation: how many flashed pads got added cuts and how many were skipped."""
    message = _("Pad isolation: %d unique surviving flashed pads; %d with added cuts; %d skipped.") % (
        report['candidates'], report['cut'], report['skipped'])
    if not report['candidates']:
        message += ' ' + _("No supported flashed-pad provenance; region-only and negative images are skipped.")
    elif report['skipped']:
        message += ' ' + _("Skipped pads have no additional safe room with this cutter and output precision.")
    return ('[INFO_NOTCL] ' if report['cut'] else '[WARNING_NOTCL] ') + message


class IsolationJob:
    def __init__(self, app, exc_geometry=None, *, tool=None, new_object=None, validation_status=False):
        """
        :param app:                 the application
        :param exc_geometry:        the geometry of the excluded area (None: no exclusion)
        :param tool:                the Isolation plugin (or a stand-in): the job then takes its decimals, units,
                                    validation status, object factory, geometry functions, temporary shapes and
                                    Voronoi diagnostic signal. Without it the job is headless: values come from
                                    the app, nothing is drawn on the canvas and no signal is emitted
        :param new_object:          function(kind, name, initialize, source=None, **kwargs) that makes an object;
                                    the default makes it with app.app_obj.new_object() (no autohide, no autoswitch)
        :param validation_status:   headless only: the tools were checked and are valid (success messages, the new
                                    objects are selected)
        """
        self.app = app
        if tool is not None:
            self.decimals = tool.decimals
            self.units = getattr(tool, 'units', '')
            self.validation_status = tool.validation_status
            self.new_object = new_object if new_object is not None else tool.new_object
            self.generate_envelope = getattr(tool, 'generate_envelope', None)
            self.generate_rest_geometry = getattr(tool, 'generate_rest_geometry', None)
            self.plot_temp_shapes = getattr(tool, 'plot_temp_shapes', None)
            self.temp_shapes = getattr(tool, 'temp_shapes', None)
            self.voronoi_diagnostic_sig = getattr(tool, 'voronoi_diagnostic_sig', None)
            self._voronoi_diagnostic_token = getattr(tool, '_voronoi_diagnostic_token', None)
        else:
            self.decimals = app.decimals
            self.units = app.app_units.upper()
            self.validation_status = validation_status
            self.new_object = new_object if new_object is not None else self._app_new_object
            self.generate_envelope = partial(IsolationGen.generate_envelope, log=app.log)
            self.generate_rest_geometry = partial(IsolationGen.generate_rest_geometry, error_handler=app.log.error)
            self.plot_temp_shapes = None
            self.temp_shapes = None
            self.voronoi_diagnostic_sig = None
            self._voronoi_diagnostic_token = None
        # the geometry of the excluded area, resolved by the controller (None: no exclusion)
        self._exc_geometry = exc_geometry
        # the objects of the run: made at once, or (with an outname) queued until the run knows how many there are
        self._outname = None
        self._queue = []
        self.created = []

    def _app_new_object(self, kind, name, initialize, source=None, **kwargs):
        return self.app.app_obj.new_object(kind, name, initialize, **kwargs)

    def _progressive(self, plot):
        """The plotting mode of the geometry functions; progressive plotting draws, so only a plotting run has it."""
        if not plot:
            return 'normal'
        return _pref(self.app, "tools_iso_plotting")

    def _clear_temp_shapes(self, where):
        if self.temp_shapes is None:
            return
        try:
            self.temp_shapes.clear(update=True)
        except Exception as err:
            self.app.log.debug("%s. Clear temp_shapes -> %s" % (where, str(err)))

    def _diagnostic(self, payload):
        if self.voronoi_diagnostic_sig is not None:
            self.voronoi_diagnostic_sig.emit(payload)
        elif payload.get('action') == 'pad_report':
            # a run without the widgets (Tcl) has no diagnostic slot: the pad report is still a message
            self.app.inform.emit(pad_report_message(payload['report']))

    def _publish(self, name, initialize, source, plot):
        """Makes a geometry object (with an outname: queues it, see isolate()). Returns the result of new_object."""
        if self._outname is not None:
            self._queue.append((name, initialize, source, plot))
            return None
        result = self.new_object("geometry", name, initialize, source=source, plot=plot,
                                 autoselected=bool(self.validation_status))
        if not (isinstance(result, str) and result == 'fail'):
            self.created.append(result)
        return result

    def _flush(self, isolated_obj):
        """Makes the queued objects: the only one gets the outname, each of several gets the outname and its suffix."""
        queue, self._queue = self._queue, []
        gerber_name = isolated_obj.obj_options["name"]
        outname, self._outname = self._outname, None
        for old_name, initialize, source, plot in queue:
            if len(queue) == 1:
                name = outname
            else:
                name = outname + (old_name[len(gerber_name):] if old_name.startswith(gerber_name) else '_' + old_name)

            def renamed_init(geo_obj, app_obj, initialize=initialize, old_name=old_name, name=name):
                result = initialize(geo_obj, app_obj)
                # All generated tools belong to this output, including combined and rest tools.
                for tool in (getattr(geo_obj, 'tools', None) or {}).values():
                    data = tool.get('data') if isinstance(tool, dict) else None
                    if isinstance(data, dict):
                        data['name'] = name
                return result

            self._publish(name, renamed_init, source, plot)
        self._outname = outname

    @classmethod
    def run_headless(cls, app, gerber_obj, tools_storage, sel_tools=None, settings=None, *, geometry=None,
                     ref_geometry=None, limited_area=None, negative_dia=False, exc_geometry=None, boundary=None,
                     outname=None, plot=False, validation_status=False, created=None):
        """
        An isolation run from plain values, without the plugin and its widgets (for scripts and Tcl).
        A setting that is missing takes the Isolation preference, as a run of the plugin starts.

        :param app:             the application
        :param gerber_obj:      the Gerber object to isolate
        :param tools_storage:   {uid: {'tooldia', 'data'}}, see IsolationGen.iso_tools(); it is not changed. With a
                                negative_dia the diameters are positive
        :param sel_tools:       the uids of the tools to use (None: all)
        :param settings:        dict with any of the keys of the plugin run_settings() (combine, iso_except, e_passes,
                                area_shape, rest, sel_type, tip_shape, iso_simplification, simplification_tol,
                                boundary, trace_boundary, forced_rest, order, clear_leftovers) and of the job
                                (strategy, voronoi_spacing, pad_isolation); they win over the preferences.
                                A rest run is always combined (as in the plugin). iso_except follows exc_geometry, sel_type follows ref_geometry and
                                limited_area
        :param geometry:        the specific geometry to isolate (None: all the copper); given but empty: error
        :param ref_geometry:    the solid geometry of a Reference Object: the copper minus it is isolated
                                (the Reference Object selection)
        :param limited_area:    isolate only this area (Shapely geometry or a list)
        :param negative_dia:    isolate inside the copper (the diameters of tools_storage are positive)
        :param exc_geometry:    the geometry of an excluded area (None: no exclusion)
        :param boundary:        None or the area (Polygon / MultiPolygon) the isolation is limited to; or settings
        :param outname:         the name of the new object. If the run makes several objects, each gets this name
                                and the suffix of the plugin that follows the Gerber name (e.g. 'out_0.1000_iso2')
        :param plot:            plot the new objects; False never plots, also not progressively
        :param validation_status:   the tools were checked and are valid
        :param created:         optional list that receives the results of new_object() of the new objects
        :return:                'fail' if no object was made (the reason was reported with app.inform), else None
        """
        settings = dict(settings or {})
        if boundary is not None:
            settings['boundary'] = boundary
        selection = 3 if ref_geometry is not None else 1 if limited_area is not None else 0
        values = {
            'combine': _pref(app, 'tools_iso_combine_passes'),
            'e_passes': _pref(app, 'tools_iso_pad_passes'),
            'area_shape': _pref(app, 'tools_iso_area_shape'),
            'rest': _pref(app, 'tools_iso_rest'),
            'tip_shape': _pref(app, 'tools_iso_tool_shape'),
            'iso_simplification': _pref(app, 'tools_iso_simplification'),
            'simplification_tol': _pref(app, 'tools_iso_simplification_tol'),
            'boundary': None,
            'trace_boundary': _pref(app, 'tools_iso_trace_boundary'),
            'forced_rest': _pref(app, 'tools_iso_force'),
            'order': _pref(app, 'tools_iso_order'),
            'clear_leftovers': _pref(app, 'tools_iso_clear_leftovers'),
        }
        values.update({key: value for key, value in settings.items() if value is not None})
        values['iso_except'] = exc_geometry is not None
        values['sel_type'] = selection
        if values['rest']:
            # the plugin forces the combined passes with rest machining; otherwise the flag is ignored
            values['combine'] = True
        if sel_tools is None:
            sel_tools = list(tools_storage)
        if ref_geometry is not None:
            geometry = IsolationGen.geometry_minus_reference(
                gerber_obj.solid_geometry if geometry is None else geometry, ref_geometry)
        job = cls(app, exc_geometry=exc_geometry, validation_status=validation_status)
        try:
            return job.isolate(gerber_obj, sel_tools, tools_storage, geometry=geometry, limited_area=limited_area,
                               negative_dia=True if negative_dia else None, plot=plot, outname=outname, **values)
        finally:
            if created is not None:
                created.extend(job.created)

    def area_subtraction(self, geo):
        return IsolationGen.area_subtraction(geo, self._exc_geometry)

    @staticmethod
    def area_intersection(geo, intersection_geo=None):
        return IsolationGen.area_intersection(geo, intersection_geo)

    def isolate(self, isolated_obj, sel_tools, tools_storage, geometry=None, limited_area=None, negative_dia=None,
                plot=True, outname=None, **args):
        """
        Generate from plain settings; never read widgets, including on worker threads.

        :param outname: the name of the new object (None: the names of the plugin). With an outname the objects are
                        made when the run ends: the only object gets the outname, each of several gets the outname
                        and the suffix of the plugin that follows the Gerber name
        :return:        'fail' if the run failed or no object was made, else None
        """
        self._outname = outname
        self._queue = []
        try:
            result = self._isolate(isolated_obj, sel_tools, tools_storage, geometry=geometry,
                                   limited_area=limited_area, negative_dia=negative_dia, plot=plot, **args)
        except BaseException:
            self._outname, self._queue = None, []
            raise
        if result == 'fail':
            self._outname, self._queue = None, []
            return 'fail'
        if outname is not None:
            self._flush(isolated_obj)
        if not self.created:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
            return 'fail'
        return None

    def _isolate(self, isolated_obj, sel_tools, tools_storage, geometry=None, limited_area=None, negative_dia=None,
                 plot=True, **args):
        use_combine = args['combine']
        use_iso_except = args['iso_except']
        use_extra_passes = args['e_passes']
        sel_area_shape = args['area_shape']
        use_rest = args['rest']
        selection_type = args['sel_type']
        tool_tip_shape = args['tip_shape']
        use_simplification = args['iso_simplification']
        simplification_tol = args['simplification_tol']
        use_clear_leftovers = args['clear_leftovers']
        boundary = args['boundary']
        if boundary == 'fail':
            return 'fail'
        trace_boundary = boundary is not None and args['trace_boundary']
        if use_rest:
            use_forced_rest = args['forced_rest']
            rest_order = args['order']

        if geometry is not None:
            geometry = flatten_shapely_geometry(geometry)
            # a given but empty geometry (e.g. a Reference Object that covers all the copper) is an empty
            # selection: it must not fall back to the isolation of the whole board
            if not geometry:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
                return 'fail'

        # the run uses its own copy of the selected tools, so the plugin tools keep no geometry and no run values
        run_tools = IsolationGen.tools_for_run(tools_storage, sel_tools)
        strategy_given = args.get('strategy')
        if strategy_given is not None and IsolationGen.tool_strategy(strategy_given) is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("The isolation strategy must be Offset or Voronoi."))
            return 'fail'
        for run_tool in run_tools.values():
            tool_data = run_tool['data']
            # the strategy, the Voronoi spacing and the pad isolation are values of each tool;
            # one given in args (e.g. by a script) is for all the tools
            strategy =IsolationGen.tool_strategy(strategy_given if strategy_given is not None
                                                  else tool_data.get('tools_iso_strategy'))
            if strategy is None:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("The isolation strategy must be Offset or Voronoi."))
                return 'fail'
            voronoi_spacing = args['voronoi_spacing'] if args.get('voronoi_spacing') is not None else \
                tool_data.get('tools_iso_voronoi_spacing', 0.05)
            pad_isolation = args['pad_isolation'] if args.get('pad_isolation') is not None else \
                tool_data.get('tools_iso_voronoi_pads', False)
            run_tool['data'].update({
                "tools_iso_rest": use_rest,
                "tools_iso_combine_passes": use_combine,
                "tools_iso_simplification": use_simplification,
                "tools_iso_simplification_tol": simplification_tol,
                "tools_iso_isoexcept": use_iso_except,
                "tools_iso_selection": selection_type,
                "tools_iso_area_shape": sel_area_shape,
                "tools_mill_job_type": 2,
                # Generated paths are already tool centerlines; Milling must not offset them again.
                "tools_iso_centerline": True,
                "tools_mill_tool_shape": run_tool['data'].get('tools_mill_tool_shape', tool_tip_shape)
                    if strategy == 'voronoi' else tool_tip_shape,
                "tools_iso_strategy": strategy,
                "tools_iso_voronoi_spacing": float(voronoi_spacing),
                "tools_iso_voronoi_pads": bool(pad_isolation) and strategy == 'voronoi',
            })
        voronoi_tools = [uid for uid in sel_tools if run_tools[uid]['data']['tools_iso_strategy'] == 'voronoi']
        prog_plot = self._progressive(plot)

        try:
            if voronoi_tools:
                if selection_type != 0:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "The Voronoi strategy needs All copper; area, polygon and reference selections are not supported."))
                    return 'fail'
                if self.voronoi_isolation(
                        iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools, sel_tools=voronoi_tools,
                        boundary=boundary, use_rest=use_rest,
                        negative_dia=negative_dia, iso_except=use_iso_except, extra_passes=use_extra_passes,
                        clear_leftovers=use_clear_leftovers, limited_area=limited_area,
                        simp_en=use_simplification, simp_tol=simplification_tol, plot=plot,
                        diagnostic_token=args.get('diagnostic_token')) == 'fail':
                    return 'fail'
                sel_tools = [uid for uid in sel_tools if uid not in voronoi_tools]
                if not sel_tools:
                    return None

            if use_combine:
                if use_rest:
                    return self.combined_rest(iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools,
                                              lim_area=limited_area, sel_tools=sel_tools, iso_except=use_iso_except,
                                              forced_rest=use_forced_rest, order=rest_order,
                                              negative_dia=negative_dia, simp_en=use_simplification,
                                              simp_tol=simplification_tol, plot=plot, boundary=boundary,
                                              trace_boundary=trace_boundary)
                return self.combined_normal(iso_obj=isolated_obj, iso2geo=geometry, tools_storage=run_tools,
                                            sel_tools=sel_tools, iso_except=use_iso_except, lim_area=limited_area,
                                            negative_dia=negative_dia, extra_passes=use_extra_passes,
                                            simp_en=use_simplification, simp_tol=simplification_tol, plot=plot,
                                            clear_leftovers=use_clear_leftovers, boundary=boundary,
                                            trace_boundary=trace_boundary)

            # the isolation is done over all the geometry of the Gerber object because it is already fused
            work_geo = flatten_shapely_geometry(isolated_obj.solid_geometry) if geometry is None else geometry
            if not work_geo:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
                return 'fail'
            pads = None
            for tool in sel_tools:
                if self.app.abort_flag:
                    raise grace
                tool_data = run_tools[tool]['data']
                iso_t = {'ext': 0, 'int': 1, 'full': 2}[tool_data['tools_iso_isotype']]
                passes = tool_data['tools_iso_passes']
                overlap = tool_data['tools_iso_overlap'] / 100.0
                milling_type = tool_data['tools_iso_milling_type']
                tool_dia = run_tools[tool]['tooldia']
                iso_offsets = [IsolationGen.pass_offset(tool_dia, nr, overlap, negative_dia)
                               for nr in range(passes)]
                buffers = IsolationGen.pass_buffers(isolated_obj, iso_offsets, self.app, geometry=work_geo)
                mill_dir = 0 if milling_type == 'cl' else 1
                outname = "%s_%.*f" % (isolated_obj.obj_options["name"], self.decimals, float(tool_dia))
                made = []
                envelopes = []
                for i in range(passes):
                    if self.app.abort_flag:
                        raise grace
                    iso_offset = iso_offsets[i]
                    suffix = ('_ext_iso', '_int_iso', '_iso')[iso_t]
                    iso_name = outname + suffix + (str(i + 1) if passes > 1 else '')
                    iso_geo = self.generate_envelope(isolated_obj, iso_offset, mill_dir, geometry=work_geo,
                                                     env_iso_type=iso_t, nr_passes=i, prog_plot=prog_plot,
                                                     buffers=buffers)
                    if iso_geo == 'fail':
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                        if plot and prog_plot == 'progressive':
                            self._clear_temp_shapes("ToolIsolation.isolate()")
                        return 'fail'
                    envelope_geo = iso_geo
                    if boundary is not None:
                        iso_geo = clip_paths_to_area(iso_geo, boundary)
                        if not iso_geo:
                            if isinstance(boundary, MultiPolygon) and not (
                                    iso_offset > 0 and pass_covers_area(envelope_geo, boundary)):
                                continue
                            break
                    if use_extra_passes > 0:
                        envelopes += flatten_shapely_geometry(envelope_geo)
                    made.append([i, iso_name, iso_geo])
                passes_made = made[-1][0] + 1 if made else 0
                if made and use_extra_passes > 0:
                    if pads is None:
                        pads = IsolationGen.pad_solids(isolated_obj, geometry)
                    if pads:
                        pad_geo = flatten_shapely_geometry(IsolationGen.pad_passes(
                            pads, envelopes, tool_dia, passes_made, use_extra_passes, overlap, self.app.options,
                            negative_dia=negative_dia, units=IsolationGen.object_units(isolated_obj, self.app)))
                        if boundary is not None:
                            pad_geo = clip_paths_to_area(pad_geo, boundary)
                        made[-1][2] = flatten_shapely_geometry(made[-1][2]) + pad_geo

                for n, (__, iso_name, iso_geo) in enumerate(made):
                    iso_geo = flatten_shapely_geometry(iso_geo)
                    if boundary is not None:
                        if trace_boundary and n == 0:
                            iso_geo += area_outline_paths(boundary)
                        if use_simplification:
                            iso_geo = [g.simplify(tolerance=simplification_tol) for g in iso_geo if not g.is_empty]
                            iso_geo = clip_paths_to_area(iso_geo, boundary)
                    if use_iso_except:
                        self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                        iso_geo = self.area_subtraction(iso_geo)
                    if limited_area:
                        self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                        iso_geo = self.area_intersection(iso_geo, intersection_geo=limited_area)
                    new_solid_geo = flatten_shapely_geometry(
                        iso_geo, simplify_tolerance=simplification_tol if use_simplification and boundary is None
                        else 0.0)
                    obj_data = deepcopy(tool_data)
                    obj_data.update({"name": iso_name, "tools_mill_tooldia": float(tool_dia)})

                    def iso_init(geo_obj, fc_obj, tool_dia=tool_dia, obj_data=obj_data,
                                 new_solid_geo=new_solid_geo):
                        geo_obj.obj_options["tools_mill_tooldia"] = str(tool_dia)
                        geo_obj.tools = {
                            1: {'tooldia': float(tool_dia), 'data': obj_data,
                                'solid_geometry': list(new_solid_geo)}
                        }
                        if not geo_obj.solid_geometry:
                            msg = '[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), geo_obj.obj_options["name"])
                            fc_obj.inform.emit(msg)
                            return 'fail'
                        if self.validation_status:
                            msg = '[success] %s: %s' % (_("Isolation geometry created"),
                                                        geo_obj.obj_options["name"])
                            fc_obj.inform.emit(msg)

                    self._publish(iso_name, iso_init, isolated_obj, plot)
                if boundary is not None:
                    report = boundary_passes_message(outname, passes_made, passes)
                    self.app.log.info("Isolation boundary: " + report)
                    self.app.inform.emit(report)
            if plot and prog_plot == 'progressive':
                self._clear_temp_shapes("ToolIsolation.isolate()")
        except grace:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("Isolation aborted."))
            if plot and prog_plot == 'progressive':
                self._clear_temp_shapes("ToolIsolation.isolate()")
            return 'fail'

    def voronoi_isolation(self, iso_obj, iso2geo, tools_storage, sel_tools, boundary, spacing=None,
                          use_rest=False, negative_dia=None, iso_except=False, extra_passes=0,
                          clear_leftovers=False, limited_area=None, simp_en=False, simp_tol=0.001, plot=True,
                          diagnostic_token=None):
        if use_rest:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support Rest Machining. Use the Offset strategy for rest machining."))
            return 'fail'
        if negative_dia:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support a negative tool diameter (inside-only isolation)."))
            return 'fail'
        if iso_except or limited_area:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support an excluded or a limited area."))
            return 'fail'
        if extra_passes or clear_leftovers:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "The Voronoi strategy does not support extra pad passes or clearing leftovers."))
            return 'fail'
        if boundary is None:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                "Voronoi isolation needs the Boundary option: select the board outline object."))
            return 'fail'
        if diagnostic_token is None:
            diagnostic_token = self._voronoi_diagnostic_token
        self._diagnostic({'action': 'clear', 'token': diagnostic_token})
        boundary = boundary.convex_hull
        work_geo = iso_obj.solid_geometry if iso2geo is None else iso2geo
        prepared = []
        try:
            coord_decimals = int(self.app.options.get('cncjob_coords_decimals', 4))
        except (TypeError, ValueError):
            coord_decimals = 4
        for tool in sel_tools:
            if self.app.abort_flag:
                raise grace
            tool_data = tools_storage[tool]['data']
            passes = int(tool_data['tools_iso_passes'])
            iso_type = tool_data['tools_iso_isotype']
            if passes != 1:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The Voronoi strategy uses one pass: it has no pass-width interpretation."))
                return 'fail'
            if iso_type != 'full':
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "The Voronoi strategy needs Full isolation; exteriors or interiors alone are not supported."))
                return 'fail'
            tool_spacing = float(spacing if spacing is not None else tool_data.get('tools_iso_voronoi_spacing', 0.05))
            tool_dia = float(tools_storage[tool]['tooldia'])
            tool_shape = tool_data.get('tools_mill_tool_shape')
            if tool_shape in (IsolationGen.V_SHAPE_INDEX, 'V', 'v'):
                effective = IsolationGen.v_effective_diameter(tool_data)
                if effective is None:
                    self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                        "The effective diameter of the V-shaped tool is not valid."))
                    return 'fail'
                tool_dia = effective
            if not math.isfinite(tool_dia) or tool_dia <= 0:
                self.app.inform.emit('[ERROR_NOTCL] %s' % _(
                    "Voronoi isolation needs a positive, finite effective tool diameter."))
                return 'fail'
            __, tool_boundary = IsolationGen.voronoi_working_bounds(boundary, tool_dia, coord_decimals)
            try:
                gen_report = {}
                paths = IsolationGen.voronoi_isolation(
                    work_geo, tool_boundary, tool_dia, tool_spacing, units=self.units,
                    simplify_tol=simp_tol if simp_en else 0.0, coord_decimals=coord_decimals,
                    abort=lambda: self.app.abort_flag, isolation_area=boundary, report=gen_report)
                if gen_report.get('spacing', tool_spacing) > tool_spacing:
                    self.app.inform.emit('[WARNING_NOTCL] %s' % (_(
                        "The Voronoi spacing was increased from %.4f to %.4f %s to stay within the site budget.") %
                        (tool_spacing, gen_report['spacing'], self.units)))
                    tool_spacing = gen_report['spacing']
                pad_report = None
                if tool_data.get('tools_iso_voronoi_pads', False):
                    paths, pad_report = IsolationGen.voronoi_pad_isolation(
                        paths, iso_obj, tool_boundary, tool_dia, coord_decimals=coord_decimals,
                        abort=lambda: self.app.abort_flag, isolation_area=boundary)
            except IsolationGen.VoronoiAborted:
                self.app.inform.emit('[WARNING_NOTCL] %s' % _("Voronoi isolation aborted."))
                return 'fail'
            except IsolationGen.VoronoiIsolationError as err:
                self.app.inform.emit('[ERROR_NOTCL] %s' % str(err))
                diagnostics = getattr(err, 'diagnostics', None) or {}
                region = diagnostics.get('region_geometry')
                if region is not None and not region.is_empty:
                    self._diagnostic({'action': 'show', 'region': region, 'token': diagnostic_token})
                return 'fail'
            outname = "%s_%.*f_voronoi" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))
            base_name, count = outname, 1
            while outname in [item[0] for item in prepared]:
                count += 1
                outname = "%s_%d" % (base_name, count)
            obj_data = deepcopy(tool_data)
            obj_data.pop('tools_iso_voronoi_pad_report', None)
            if pad_report is not None:
                obj_data['tools_iso_voronoi_pad_report'] = pad_report
            obj_data.update({
                "name": outname,
                "tools_mill_tooldia": float(tool_dia),
                "tools_mill_job_type": 2,
                "tools_iso_centerline": True,
                "tools_iso_strategy": "voronoi",
                "tools_iso_voronoi_spacing": tool_spacing,
                # CNC generation must preserve the paths certified on this output grid.
                "tools_iso_validated_paths": True,
                "tools_iso_voronoi_coord_decimals": coord_decimals,
                "tools_iso_voronoi_path_signature": sha256(
                    shapely.GeometryCollection(paths).normalize().wkb).hexdigest(),
                "tools_iso_voronoi_units": self.units.upper(),
                "tools_iso_voronoi_tooldia": float(tool_dia),
            })
            prepared.append((outname, paths, tool_dia, obj_data))
        if not prepared:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("Voronoi isolation geometry could not be generated."))
            return 'fail'
        if self.app.abort_flag:
            raise grace
        # Publish only after every selected tool passes clearance and separation checks.
        for outname, paths, tool_dia, obj_data in prepared:
            if 'tools_iso_voronoi_pad_report' in obj_data:
                self._diagnostic({
                    'action': 'pad_report', 'token': diagnostic_token,
                    'report': obj_data['tools_iso_voronoi_pad_report']})

            def iso_init(geo_obj, fc_obj, paths=paths, tool_dia=tool_dia, obj_data=obj_data):
                geo_obj.obj_options["tools_mill_tooldia"] = str(tool_dia)
                geo_obj.tools = {
                    1: {'tooldia': float(tool_dia), 'data': deepcopy(obj_data),
                        'solid_geometry': list(paths)}
                }
                if not geo_obj.solid_geometry:
                    msg = '[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), geo_obj.obj_options["name"])
                    fc_obj.inform.emit(msg)
                    return 'fail'
                if self.validation_status:
                    msg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                    fc_obj.inform.emit(msg)

            self._publish(outname, iso_init, iso_obj, plot)

    def combined_rest(self, iso_obj, iso2geo, tools_storage, lim_area, sel_tools, iso_except=False, forced_rest=False,
                      order=0, negative_dia=None, simp_en=False, simp_tol=0.001, plot=True, boundary=None,
                      trace_boundary=False):
        self.app.log.debug("ToolIsolation.combine_rest()")
        total_solid_geometry = []
        used_tools = []
        iso_name = iso_obj.obj_options["name"] + '_iso_rest'
        work_geo = [geo for geo in flatten_shapely_geometry(iso_obj.solid_geometry if iso2geo is None else iso2geo)
                    if isinstance(geo, Polygon)]
        if not work_geo:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("No geometry to isolate."))
            return 'fail'
        if not sel_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s' % _("There are no tools selected in the Tool Table."))
            return 'fail'
        sorted_tools = sorted(sel_tools, key=lambda uid: float(tools_storage[uid]['tooldia']), reverse=True)
        if order == 1:
            self.app.log.info("ToolIsolation.combined_rest() -> the order Forward is not used with rest machining.")
        prog_plot = self._progressive(plot)
        for tool in sorted_tools:
            if self.app.abort_flag:
                raise grace
            tool_dia = tools_storage[tool]['tooldia']
            tool_data = tools_storage[tool]['data']
            passes = tool_data['tools_iso_passes']
            overlap = tool_data['tools_iso_overlap'] / 100.0
            milling_type = tool_data['tools_iso_milling_type']
            mill_dir = False if milling_type == 'cl' else True
            iso_t = {'ext': 0, 'int': 1, 'full': 2}[tool_data['tools_iso_isotype']]
            outname = "%s_%.*f" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))
            internal_name = outname + ('_ext_iso', '_int_iso', '_iso')[iso_t]
            tool_data.update({"name": internal_name})
            solid_geo, left_geo = self.generate_rest_geometry(
                geometry=work_geo, tooldia=tool_dia, passes=passes, overlap=overlap, invert=mill_dir,
                env_iso_type=iso_t, negative_dia=negative_dia, forced_rest=forced_rest,
                prog_plot=prog_plot, prog_plot_handler=self.plot_temp_shapes, plot=plot)
            if isinstance(solid_geo, str):
                self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                continue
            work_geo = left_geo
            if boundary is not None:
                solid_geo = clip_paths_to_area(solid_geo, boundary)
                if trace_boundary and solid_geo and not total_solid_geometry:
                    solid_geo += area_outline_paths(boundary)
                if simp_en:
                    solid_geo = [g.simplify(tolerance=simp_tol) for g in solid_geo if not g.is_empty]
                    solid_geo = clip_paths_to_area(solid_geo, boundary)
            if iso_except:
                self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                solid_geo = self.area_subtraction(solid_geo)
            if lim_area:
                self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                solid_geo = self.area_intersection(solid_geo, intersection_geo=lim_area)
            new_solid_geo = flatten_shapely_geometry(
                solid_geo, simplify_tolerance=simp_tol if simp_en and boundary is None else 0.0)
            tool_data['tools_mill_tooldia'] = float(tool_dia)
            tools_storage[tool] = {
                'tooldia': float(tool_dia), 'data': tool_data, 'solid_geometry': new_solid_geo
            }
            if new_solid_geo:
                used_tools.append(tool)
                total_solid_geometry += new_solid_geo
            if not work_geo:
                break
        if plot and prog_plot == 'progressive':
            self._clear_temp_shapes("ToolIsolation.combined_rest(). Progressive plotting")
        if not used_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), iso_name))
            result = 'fail'
        else:
            obj_dia = max(float(tools_storage[uid]['tooldia']) for uid in used_tools)

            def iso_init(geo_obj, app_obj):
                geo_obj.obj_options["tools_mill_tooldia"] = str(obj_dia)
                geo_obj.tools = IsolationGen.tools_for_object(tools_storage, used_tools)
                if self.validation_status:
                    mssg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                    app_obj.inform.emit(mssg)

            result = 'fail' if self._publish(iso_name, iso_init, iso_obj, plot) == 'fail' else None
        if work_geo:
            self.app.inform.emit("[WARNING] %s" % _("Partial failure. The geometry was processed with all tools.\n"
                                                    "But there are still not-isolated geometry elements. "
                                                    "Try to include a tool with smaller diameter."))
            msg = _("The following are coordinates for the copper features that could not be isolated:")
            self.app.inform_shell.emit(msg)
            msg = ''
            for geo in work_geo:
                pt = geo.representative_point()
                msg += '(%s, %s), ' % (str(pt.x), str(pt.y))
            self.app.inform_shell.emit(msg)
        return result

    def combined_normal(self, iso_obj, iso2geo, tools_storage, lim_area, sel_tools, iso_except, extra_passes=None,
                        negative_dia=None, simp_en=False, simp_tol=0.001, plot=True, prog_plot=None,
                        clear_leftovers=False, boundary=None, trace_boundary=False):
        self.app.log.debug("ToolIsolation.combined_normal()")
        total_solid_geometry = []
        used_tools = []
        iso_name = iso_obj.obj_options["name"] + '_iso_combined'
        geometry = iso2geo
        work_geo = iso_obj.solid_geometry if geometry is None else geometry
        if prog_plot is None or not plot:
            prog_plot = self._progressive(plot)
        pads = None
        for tool in sel_tools:
            if self.app.abort_flag:
                raise grace
            tool_dia = tools_storage[tool]['tooldia']
            tool_has_offset = tools_storage[tool]['data']['tools_mill_offset_type']
            tool_offset_value = tools_storage[tool]['data']['tools_mill_offset_value']
            tool_type = tools_storage[tool]['data']['tools_mill_tool_shape']
            tool_data = tools_storage[tool]['data']
            iso_t = {'ext': 0, 'int': 1, 'full': 2}[tool_data['tools_iso_isotype']]
            passes = tool_data['tools_iso_passes']
            overlap = tool_data['tools_iso_overlap'] / 100.0
            milling_type = tool_data['tools_iso_milling_type']
            outname = "%s_%.*f" % (iso_obj.obj_options["name"], self.decimals, float(tool_dia))
            internal_name = outname + ('_ext_iso', '_int_iso', '_iso')[iso_t]
            tool_data.update({"name": internal_name})
            use_leftovers = clear_leftovers and iso_t == 2 and not negative_dia
            iso_offsets = [IsolationGen.pass_offset(tool_dia, nr, overlap, negative_dia) for nr in range(passes)]
            buffers = IsolationGen.pass_buffers(iso_obj, iso_offsets, self.app, geometry=work_geo)
            mill_dir = 0 if milling_type == 'cl' else 1

            def pass_envelopes():
                for nr_pass, iso_offset in enumerate(iso_offsets):
                    if self.app.abort_flag:
                        raise grace
                    iso_geo = self.generate_envelope(
                        iso_obj, iso_offset, mill_dir, geometry=work_geo, env_iso_type=iso_t,
                        nr_passes=nr_pass, prog_plot=prog_plot, buffers=buffers)
                    if iso_geo == 'fail':
                        self.app.inform.emit('[ERROR_NOTCL] %s' % _("Isolation geometry could not be generated."))
                    yield iso_geo

            solid_geo, envelopes, leftover_geo, passes_made = IsolationGen.combined_passes(
                pass_envelopes(), iso_offsets, tool_dia,
                self.app.options, mill_dir,
                clear_leftovers=use_leftovers, boundary=boundary,
                units=IsolationGen.object_units(iso_obj, self.app))
            if use_leftovers:
                self.app.log.debug("ToolIsolation.combined_normal() -> %d paths clear the leftovers" %
                                   len(leftover_geo))
            if boundary is not None:
                report = boundary_passes_message(outname, passes_made, passes)
                self.app.log.info("Isolation boundary: " + report)
                self.app.inform.emit(report)
                if passes_made == 0:
                    continue
            if extra_passes is not None and extra_passes > 0:
                if pads is None:
                    pads = IsolationGen.pad_solids(iso_obj, geometry)
                if pads:
                    solid_geo += IsolationGen.pad_passes(
                        pads, envelopes if boundary is not None else solid_geo,
                        tool_dia, passes_made, extra_passes, overlap,
                        self.app.options, negative_dia=negative_dia, units=IsolationGen.object_units(iso_obj, self.app))
            solid_geo += leftover_geo
            if boundary is not None:
                solid_geo = clip_paths_to_area(solid_geo, boundary)
                if trace_boundary and not total_solid_geometry:
                    solid_geo += area_outline_paths(boundary)
                if simp_en:
                    solid_geo = [g.simplify(tolerance=simp_tol) for g in solid_geo if not g.is_empty]
                    solid_geo = clip_paths_to_area(solid_geo, boundary)
            if iso_except:
                self.app.proc_container.update_view_text(' %s' % _("Subtracting Geo"))
                solid_geo = self.area_subtraction(solid_geo)
            if lim_area:
                self.app.proc_container.update_view_text(' %s' % _("Intersecting Geo"))
                solid_geo = self.area_intersection(solid_geo, intersection_geo=lim_area)
            new_solid_geo = flatten_shapely_geometry(
                solid_geo, simplify_tolerance=simp_tol if simp_en and boundary is None else 0.0)
            tool_data['tools_mill_tooldia'] = float(tool_dia)
            tools_storage[tool] = {
                'tooldia': float(tool_dia), 'offset': tool_has_offset,
                'offset_value': tool_offset_value, 'tool_type': tool_type,
                'data': tool_data, 'solid_geometry': new_solid_geo
            }
            if new_solid_geo:
                used_tools.append(tool)
                total_solid_geometry += new_solid_geo
        if plot and prog_plot == 'progressive':
            self._clear_temp_shapes("ToolIsolation.combined_normal(). Progressive plotting")
        if not used_tools:
            self.app.inform.emit('[ERROR_NOTCL] %s: %s' % (_("Empty Geometry in"), iso_name))
            return 'fail'
        obj_dia = max(float(tools_storage[uid]['tooldia']) for uid in used_tools)

        def iso_init(geo_obj, app_obj):
            geo_obj.obj_options["tools_mill_tooldia"] = str(obj_dia)
            geo_obj.tools = IsolationGen.tools_for_object(tools_storage, used_tools)
            if self.validation_status:
                msg = '[success] %s: %s' % (_("Isolation geometry created"), geo_obj.obj_options["name"])
                app_obj.inform.emit(msg)

        if self._publish(iso_name, iso_init, iso_obj, plot) == 'fail':
            return 'fail'
