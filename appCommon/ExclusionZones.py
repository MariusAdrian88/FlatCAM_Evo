# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The exclusion zones of a source object: one list of Shapely polygons for each plugin that uses them.

The store is a dict {plugin key: [Polygon, ...]}. A key with no zones is not in the dict. No Qt here: camlib uses it.
"""

ZONE_PLUGINS = ('isolation', 'ncc', 'paint')
# the plugins that used the flat list of the projects saved before the zones were split
LEGACY_PLUGINS = ('ncc', 'paint')


def distinct_zones(zones):
    """The zones without the repeated ones (same outline), in the order of the first appearance."""
    seen = set()
    found = []
    for zone in zones:
        key = zone.wkb
        if key not in seen:
            seen.add(key)
            found.append(zone)
    return found


def normalize_zones(value):
    """
    A zone store from a saved value: a dict is filtered to the known keys; an old flat list becomes the zones of
    NCC and of Paint; None or empty is {}.
    """
    if not value:
        return {}
    if isinstance(value, dict):
        return {key: list(zones) for key, zones in value.items() if key in ZONE_PLUGINS and zones}
    zones = list(value)
    return {key: list(zones) for key in LEGACY_PLUGINS}


def copy_zones(store):
    """A copy of the store with new lists (the polygons are immutable and shared)."""
    return {key: list(zones) for key, zones in (store or {}).items() if zones}


def zones_of(store, plugin):
    """The zones of one plugin (a new list)."""
    return list((store or {}).get(plugin, ()))


def with_zones(store, plugin, zones):
    """A new store with the zones of one plugin replaced (no zones: the key goes away)."""
    result = copy_zones(store)
    if zones:
        result[plugin] = list(zones)
    else:
        result.pop(plugin, None)
    return result


def all_zones(store):
    """The zones of all the plugins, each distinct zone once."""
    return distinct_zones([zone for key in ZONE_PLUGINS for zone in (store or {}).get(key, ())])


def map_zones(store, function):
    """A new store: function(list of zones) -> list of zones, for each plugin."""
    return {key: list(function(list(zones))) for key, zones in (store or {}).items() if zones}


def merge_zone_stores(stores):
    """One store with the zones of all the stores, for each plugin; no repeated zone."""
    merged = {}
    for store in stores:
        for key, zones in (store or {}).items():
            merged[key] = distinct_zones(merged.get(key, []) + list(zones))
    return {key: zones for key, zones in merged.items() if zones}
