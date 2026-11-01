"""Gerber Editor aperture table operations: delete unused, renumber, merge duplicates, round sizes, same type.

Each operation is 1 undo step. The region aperture is found by its type ('REG'), never by its key.
"""

import math
import gettext
import builtins

from shapely import LineString, Point, box
from shapely.errors import GEOSException

from appEditors.grb_plugins.GrbCommon import DrawToolShape, gerber_quad, gerber_steps
from appEditors.grb_plugins.GrbHistory import gerber_operation, mark_flashes, metadata
from appParsers.ParseGerber import Gerber
import appTranslation as fcTranslate

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _rank(key):
    # imported late: appGerberEditor imports the plugins
    from appEditors.appGerberEditor import aperture_rank
    return aperture_rank(key)


def _is_region(aperture):
    return str(aperture.get('type')) == 'REG'


def _refresh(editor, codes):
    """The table and plot of the new state; the plot runs once at the end of the history step."""
    last = editor.last_aperture_selected
    if last not in editor.storage_dict:
        last = next(iter(sorted((key for key, ap in editor.storage_dict.items() if not _is_region(ap)),
                                key=_rank)), None)
        editor.last_aperture_selected = last
    editor.current_storage = editor.storage_dict.get(last, {}).get('geometry', [])
    editor.touch_geometry()
    editor.build_ui()
    editor.sync_aperture_rows([code for code in codes if code in editor.storage_dict])
    editor.history.needs_plot = True


def _mark_flashes_of(editor, apertures):
    """The Point records of R and O apertures are flashes of their aperture, also under a new key."""
    mark_flashes(editor, [shape for aperture in apertures if str(aperture.get('type')) in ('R', 'O')
                          for shape in aperture.get('geometry', ())])


def _forget(editor, code):
    for tid in [tid for tid, value in editor.tid2apcode.items() if value == code]:
        editor.tid2apcode.pop(tid)
    editor.oldapcode_newapcode.pop(code, None)


@gerber_operation(_('Delete unused apertures'), all_apertures=True)
def apply_delete_unused(editor):
    unused = sorted((key for key, aperture in editor.storage_dict.items()
                     if not _is_region(aperture) and not aperture.get('geometry')), key=_rank)
    if unused and len(unused) == sum(1 for aperture in editor.storage_dict.values() if not _is_region(aperture)):
        # the editor needs 1 aperture to draw with
        unused = unused[1:]
    if not unused:
        editor.app.inform.emit(_("No unused aperture found."))
        return []
    for code in unused:
        editor.storage_dict.pop(code)
        _forget(editor, code)
    editor.is_modified = True
    _refresh(editor, [])
    editor.app.inform.emit(_("{n} unused apertures deleted").format(n=len(unused)))
    return unused


@gerber_operation(_('Renumber apertures'), all_apertures=True)
def apply_renumber(editor, start=10):
    try:
        start = int(start)
    except (TypeError, ValueError):
        start = -1
    if start < 10:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % _("The first aperture code must be an integer of 10 or more."))
        return 'fail'
    region_keys = {str(key) for key, aperture in editor.storage_dict.items() if _is_region(aperture)}
    old_codes = sorted((key for key, aperture in editor.storage_dict.items() if not _is_region(aperture)),
                       key=_rank)
    mapping, code = {}, start
    for old in old_codes:
        while str(code) in region_keys:
            code += 1
        mapping[old] = code
        code += 1
    changed = {old: new for old, new in mapping.items() if old != new}
    if not changed:
        return {}
    items = [(mapping.get(key, key), aperture) for key, aperture in editor.storage_dict.items()]
    editor.storage_dict.clear()
    editor.storage_dict.update(items)
    _mark_flashes_of(editor, [editor.storage_dict[new] for new in changed.values()])
    for tid, value in list(editor.tid2apcode.items()):
        editor.tid2apcode[tid] = mapping.get(value, value)
    for old in changed:
        editor.oldapcode_newapcode.pop(old, None)
    editor.oldapcode_newapcode.update({new: new for new in changed.values()})
    editor.last_aperture_selected = mapping.get(editor.last_aperture_selected, editor.last_aperture_selected)
    editor.is_modified = True
    _refresh(editor, [])
    return changed


@gerber_operation(_('Merge duplicate apertures'), all_apertures=True)
def apply_merge_duplicates(editor):
    groups = []
    for key in sorted((key for key, aperture in editor.storage_dict.items() if not _is_region(aperture)),
                      key=_rank):
        meta = metadata(editor.storage_dict[key])
        for group in groups:
            if group[0] == meta:
                group[1].append(key)
                break
        else:
            groups.append((meta, [key]))
    merged = {}
    for meta, keys in groups:
        kept = keys[0]
        storage = editor.storage_dict[kept]['geometry']
        for other in keys[1:]:
            storage.extend(editor.storage_dict.pop(other).get('geometry', ()))
            _forget(editor, other)
            merged[other] = kept
    if not merged:
        editor.app.inform.emit(_("No duplicate aperture found."))
        return {}
    if editor.last_aperture_selected in merged:
        editor.last_aperture_selected = merged[editor.last_aperture_selected]
    _mark_flashes_of(editor, [editor.storage_dict[kept] for kept in set(merged.values())])
    editor.is_modified = True
    _refresh(editor, set(merged.values()))
    editor.app.inform.emit(_("{n} duplicate apertures merged").format(n=len(merged)))
    return merged


def _rounded(aperture, decimals):
    """The metadata values of a rounded aperture, or None when nothing changes."""
    kind = str(aperture.get('type'))
    if kind == 'C':
        names = ['size']
    elif kind in ('R', 'O'):
        names = ['width', 'height']
    else:
        return None
    new = {}
    for name in names:
        value = float(aperture[name])
        if not math.isfinite(value):
            return None
        rounded = round(value, decimals)
        if rounded <= 0:
            return None
        new[name] = rounded
    if kind != 'C':
        new['size'] = math.hypot(new['width'], new['height'])
    if all(abs(new[name] - float(aperture[name])) <= 1e-12 for name in names):
        return None
    return new


def _rebuild(editor, shape, aperture, new_meta):
    """The record for the rounded aperture: a flash is made again, a track grows by half of the change."""
    geo = dict(shape.geo)
    kind = str(aperture['type'])
    follow = geo.get('follow')
    if kind == 'C':
        old_size, new_size = float(aperture['size']), new_meta['size']
    else:
        old_size = max(float(aperture['width']), float(aperture['height']))
        new_size = max(new_meta['width'], new_meta['height'])
    adjust = (new_size - old_size) / 2
    if 'solid' in geo:
        if isinstance(follow, Point) and not follow.is_empty:
            if kind == 'C':
                geo['solid'] = follow.buffer(new_size / 2, gerber_quad(editor.app, new_size / 2))
            elif kind == 'R':
                geo['solid'] = box(follow.x - new_meta['width'] / 2, follow.y - new_meta['height'] / 2,
                                   follow.x + new_meta['width'] / 2, follow.y + new_meta['height'] / 2)
            else:
                geo['solid'] = Gerber.create_flash_geometry(
                    editor, follow, dict(aperture, **new_meta),
                    gerber_steps(editor.app, min(new_meta['width'], new_meta['height']) / 2))
        elif kind == 'C' and type(follow) is LineString and not follow.is_empty:
            geo['solid'] = follow.buffer(new_size / 2, gerber_quad(editor.app, new_size / 2))
        else:
            geo['solid'] = geo['solid'].buffer(adjust, join_style='mitre')
    if getattr(geo.get('clear'), 'is_empty', True) is False:
        geo['clear'] = geo['clear'].buffer(adjust, join_style='mitre')
    if geo.get('solid') is None or geo['solid'].is_empty or not geo['solid'].is_valid:
        raise ValueError(_("The rounded size produces invalid geometry."))
    record = DrawToolShape(geo)
    if kind in ('R', 'O') and isinstance(follow, Point) and not follow.is_empty:
        mark_flashes(editor, [record])
    return record


@gerber_operation(_('Round aperture sizes'), all_apertures=True)
def apply_round_sizes(editor):
    decimals = 3 if str(editor.app.app_units).upper() == 'IN' else 2
    plan = {}
    for code, aperture in editor.storage_dict.items():
        if _is_region(aperture):
            continue
        try:
            new_meta = _rounded(aperture, decimals)
        except (KeyError, TypeError, ValueError):
            new_meta = None
        if new_meta is not None:
            plan[code] = new_meta
    if not plan:
        editor.app.inform.emit(_("All aperture sizes are already rounded."))
        return []
    try:
        records = {code: [_rebuild(editor, shape, editor.storage_dict[code], new_meta)
                          for shape in editor.storage_dict[code].get('geometry', ())]
                   for code, new_meta in plan.items()}
    except (ValueError, TypeError, KeyError, GEOSException) as err:
        editor.app.inform.emit('[WARNING_NOTCL] %s' % str(err))
        return 'fail'
    replaced = {}
    for code, new_meta in plan.items():
        aperture = editor.storage_dict[code]
        replaced.update(zip(aperture.get('geometry', ()), records[code]))
        aperture['geometry'][:] = records[code]
        aperture.update(new_meta)
    editor.selected = [replaced.get(shape, shape) for shape in editor.selected]
    editor.is_modified = True
    _refresh(editor, plan)
    editor.app.inform.emit(_("{n} aperture sizes rounded").format(n=len(plan)))
    return list(plan)


def same_type_codes(editor, code):
    target = next((aperture for key, aperture in editor.storage_dict.items() if str(key) == str(code)), None)
    if target is None:
        return []
    return [key for key, aperture in editor.storage_dict.items() if aperture.get('type') == target.get('type')]
