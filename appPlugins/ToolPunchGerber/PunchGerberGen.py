from camlib import deepcopy_keep_geometry

from shapely import Point, LineString
from shapely.ops import unary_union

from appParsers.feature_access import normalize_polygonal


def _pad_size(aperture, record):
    """Smallest dimension of the pad made by this record."""
    ap_type = aperture['type']
    if ap_type == 'C' and 'size' in aperture:
        return float(aperture['size'])
    if ap_type in ('R', 'O') and 'width' in aperture and 'height' in aperture:
        return min(float(aperture['width']), float(aperture['height']))
    x0, y0, x1, y1 = record['solid'].bounds
    return min(x1 - x0, y1 - y0)


def _category(aperture, decimals):
    ap_type = aperture['type']
    if ap_type == 'C':
        return 0
    if ap_type == 'O':
        return 1
    if ap_type == 'R':
        same = round(float(aperture['width']), decimals) == round(float(aperture['height']), decimals)
        return 2 if same else 3
    return 4


def _punchable(record):
    return 'solid' in record and isinstance(record.get('follow'), Point)


def punch_geometry(solid_geometry, tools, selected_apertures, method, value, decimals, enabled,
                   manual_pads=None, drill_geometry=None, drill_tools=None, steps=64):
    """Punch pads and return independent solid geometry and exportable clear aperture records.

    Raises ValueError with args[0] in 'empty' (no apertures), 'nothing' (no pad punched), 'size' (hole not smaller
    than the pad) or 'zero' (hole diameter is zero or negative).

    steps: segments of a full circle, a count for every circle or a function of the radius of the circle.
    """
    if not tools:
        raise ValueError('empty')

    def quad_segs_of(radius):
        return max(1, int(steps(radius) if callable(steps) else steps) // 4)

    source = normalize_polygonal(solid_geometry)
    apertures = deepcopy_keep_geometry(tools)
    new_apid = max(int(apid) for apid in apertures)
    holes = {}
    seen = set()

    def add_clear(size, geo):
        key = (size, geo.wkt)
        if key in seen:
            return
        seen.add(key)
        if size not in holes:
            holes[size] = {'type': 'C', 'size': size, 'geometry': []}
        holes[size]['geometry'].append({'clear': geo})

    # pads to punch: (apid, idx, aperture, record)
    pads = []
    if manual_pads is not None:
        done = set()
        for pad in manual_pads:
            key = (pad['apid'], pad['idx'])
            if key in done or pad['apid'] not in tools:
                continue
            done.add(key)
            aperture = tools[pad['apid']]
            if pad['idx'] >= len(aperture.get('geometry', [])):
                continue
            record = aperture['geometry'][pad['idx']]
            if _punchable(record):
                pads.append((pad['apid'], pad['idx'], aperture, record))
    else:
        for apid, aperture in tools.items():
            if apid not in selected_apertures:
                continue
            if method != 'exc' and not enabled[_category(aperture, decimals)]:
                continue
            for idx, record in enumerate(aperture.get('geometry', [])):
                if _punchable(record):
                    pads.append((apid, idx, aperture, record))
    if not pads:
        raise ValueError('nothing')

    if method == 'exc':
        pad_union = unary_union([pad[3]['solid'] for pad in pads])
        cutter = normalize_polygonal(drill_geometry).intersection(pad_union)
        if cutter.is_empty:
            raise ValueError('nothing')
        for tool in (drill_tools or {}).values():
            dia = float(tool['tooldia'])
            # A clear record erases its full footprint in the export, so it needs the whole footprint inside the pads.
            for point in tool.get('drills', []):
                if pad_union.contains(point.buffer(dia / 2, quad_segs=quad_segs_of(dia / 2))):
                    add_clear(dia, point)
            for slot in tool.get('slots', []):
                line = LineString([slot[0], slot[1]])
                if pad_union.contains(line.buffer(dia / 2, quad_segs=quad_segs_of(dia / 2))):
                    add_clear(dia, line)
        punched = source.difference(cutter)
        for apid, idx, _aperture, _record in pads:
            rec = apertures[apid]['geometry'][idx]
            rec['solid'] = rec['solid'].difference(cutter)
    else:
        cutters = []
        for apid, idx, aperture, record in pads:
            size = _pad_size(aperture, record)
            category = _category(aperture, decimals)
            if method == 'fixed':
                dia = value
            elif method == 'ring':
                dia = size - 2 * value[category]
            else:
                dia = size * value
            if dia <= 0:
                raise ValueError('zero')
            if dia >= size:
                raise ValueError('size')
            cutter = record['follow'].buffer(dia / 2, quad_segs=quad_segs_of(dia / 2))
            cutters.append(cutter)
            rec = apertures[apid]['geometry'][idx]
            rec['solid'] = rec['solid'].difference(cutter)
            add_clear(dia, record['follow'])
        punched = source.difference(unary_union(cutters))

    for aperture in holes.values():
        new_apid += 1
        apertures[new_apid] = deepcopy_keep_geometry(aperture)
    return punched, apertures
