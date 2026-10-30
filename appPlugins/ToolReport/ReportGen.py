"""Object report geometry calculations on explicit data. No Qt."""

from shapely import MultiPolygon, Polygon
from shapely.ops import unary_union

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def calculate_dimensions(geo, tools, kind, units, bounds=None):
    """Return (box area, length, width, hull area, copper area) and recoverable errors.

    Keep the report's existing unit conversions, including copper area / 100 in
    inch projects, and its per-tool hull fallback rather than changing displayed values.
    """
    length = 0.0
    width = 0.0
    area = 0.0
    copper_area = 0.0
    errors = []
    if geo:
        if bounds is not None:
            try:
                xmin, ymin, xmax, ymax = bounds
                length = abs(xmax - xmin)
                width = abs(ymax - ymin)
            except Exception as ee:
                errors.append("PropertiesTool.addItems() -> calculate dimensions --> %s" % str(ee))
        if units.lower() == 'mm':
            area = (length * width) / 100
        else:
            area = length * width
        if kind.lower() == 'gerber':
            try:
                for geo_el in geo:
                    copper_area += geo_el.area
            except TypeError:
                copper_area += geo.area
            copper_area /= 100
    else:
        xmin = []
        ymin = []
        xmax = []
        ymax = []
        # Preserve the two passes used by the CNCJob report, including error recovery.
        for _ in range(2 if kind.lower() == 'cncjob' else 1):
            try:
                for tool_k in tools:
                    x0, y0, x1, y1 = unary_union(tools[tool_k]['solid_geometry']).bounds
                    xmin.append(x0)
                    ymin.append(y0)
                    xmax.append(x1)
                    ymax.append(y1)
            except Exception as ee:
                errors.append("PropertiesTool.addItems() --> %s" % str(ee))
        try:
            xmin = min(xmin)
            ymin = min(ymin)
            xmax = max(xmax)
            ymax = max(ymax)
            length = abs(xmax - xmin)
            width = abs(ymax - ymin)
            if units.lower() == 'mm':
                area = (length * width) / 100
            else:
                area = length * width
            if kind.lower() == 'gerber':
                geo_tools = []
                for tool_k in tools:
                    if 'solid_geometry' in tools[tool_k]:
                        for geo_el in tools[tool_k]['solid_geometry']:
                            geo_tools.append(geo_el)
                geo_tools_mp = MultiPolygon(geo_tools)
                try:
                    for geo_el in geo_tools_mp.geoms:
                        copper_area += geo_el.area
                except TypeError:
                    copper_area += geo_tools_mp.area
                copper_area /= 100
        except Exception as err:
            errors.append("Properties.addItems() --> %s" % str(err))

    area_chull = 0.0
    if kind.lower() != 'cncjob':
        if geo:
            if isinstance(geo, list) and geo[0] is not None:
                if isinstance(geo, MultiPolygon):
                    env_obj = geo.convex_hull
                elif (isinstance(geo, MultiPolygon) and len(geo.geoms) == 1) or \
                        (isinstance(geo, list) and len(geo) == 1) and isinstance(geo[0], Polygon):
                    env_obj = unary_union(geo)
                    env_obj = env_obj.convex_hull
                else:
                    env_obj = unary_union(geo)
                    env_obj = env_obj.convex_hull
                area_chull = env_obj.area
            else:
                area_chull = 0
        else:
            try:
                area_chull = []
                for tool_k in tools:
                    area_el = unary_union(tools[tool_k]['solid_geometry']).convex_hull
                    area_chull.append(area_el.area)
                area_chull = max(area_chull)
            except Exception as er:
                area_chull = None
                errors.append("Properties.addItems() --> %s" % str(er))
    if units.lower() == 'mm' and area_chull:
        area_chull = area_chull / 100
    if area_chull is None:
        area_chull = 0
    return (area, length, width, area_chull, copper_area), errors


MAX_REPORT_ITEMS = 50
_FF_ORDER = ('kind', 'layer', 'side', 'type', 'plated', 'from_to', 'edge', 'index', 'extra')


def _flat(value):
    if isinstance(value, (list, tuple)):
        return '-'.join(str(v) for v in value if v not in (None, ''))
    return str(value)


def gerber_attribute_rows(obj):
    """Rows (section, name, value) with the Gerber X2/X3 (or Excellon XNC) attributes of obj; [] when it has none. No Qt.

    Sections: attributes, apertures, nets, components. Values are strings.
    """
    from appParsers.gerber_attributes import component_of, parse_aper_function

    file_attrs = getattr(obj, 'file_attributes', None) or {}
    raw_tf = (getattr(obj, 'attributes', None) or {}).get('TF') or {}
    to_table = getattr(obj, 'to_attributes', None) or {}
    tools = getattr(obj, 'tools', None) or {}

    rows = []
    typed = {k: v for k, v in file_attrs.items() if v is not None}
    if typed:
        labels = (
            ('part', _("Part")), ('file_function', _("File function")), ('file_polarity', _("Polarity")),
            ('same_coordinates', _("Same coordinates")), ('creation_date', _("Date")),
            ('generation_software', _("Software")), ('project_id', _("Project ID")), ('md5', _("MD5")),
        )
        for key, label in labels:
            value = typed.get(key)
            if value is None:
                continue
            if key == 'file_function' and isinstance(value, dict):
                value = ', '.join(_flat(value[k]) for k in _FF_ORDER if value.get(k) not in (None, '', [], ()))
            rows.append(('attributes', label, _flat(value)))
    else:
        for name, values in raw_tf.items():
            rows.append(('attributes', str(name), ','.join(str(v) for v in values)))

    kinds = {}
    for tool in tools.values():
        values = (tool.get('attributes') or {}).get('.AperFunction')
        if values:
            kind = parse_aper_function(values)['kind']
            kinds[kind] = kinds.get(kind, 0) + 1
    for kind, count in kinds.items():
        rows.append(('apertures', kind, str(count)))

    if to_table:
        used = {}
        for tool in tools.values():
            if 'drills' in tool or 'slots' in tool:     # Excellon: the ids of the holes
                from appParsers.ParseExcellon import hole_ids
                for kind in ('drills', 'slots'):
                    for ident in hole_ids(tool, kind) or ():
                        if ident:
                            used[int(ident)] = used.get(int(ident), 0) + 1
                continue
            for rec in tool.get('geometry') or ():
                ident = rec.get('to') if isinstance(rec, dict) else None
                if ident is not None:
                    used[int(ident)] = used.get(int(ident), 0) + 1
        nets = {}
        for ident, state in to_table.items():
            for net in state.get('.N', ()):
                if net not in ('', 'N/C'):
                    nets[net] = nets.get(net, 0) + used.get(int(ident), 0)
        if nets:
            rows.append(('nets', _("Count"), str(len(nets))))
            for net in sorted(nets)[:MAX_REPORT_ITEMS]:
                rows.append(('nets', net, str(nets[net])))
            if len(nets) > MAX_REPORT_ITEMS:
                rows.append(('nets', '...', ''))

        comps = component_of(to_table)
        if comps:
            rows.append(('components', _("Count"), str(len(comps))))
            for refdes in sorted(comps)[:MAX_REPORT_ITEMS]:
                comp = comps[refdes]
                parts = [comp.get('CVal'), comp.get('CFtp') or comp.get('CPgN'), comp.get('CRot')]
                rows.append(('components', refdes, ', '.join(str(p) for p in parts if p not in (None, ''))))
            if len(comps) > MAX_REPORT_ITEMS:
                rows.append(('components', '...', ''))
    return rows
