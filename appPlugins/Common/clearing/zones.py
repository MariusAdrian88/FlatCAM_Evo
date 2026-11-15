# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/15/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
The exclusion zones of a plugin panel: the zones of one plugin key, stored on the source objects. Used by NCC, Paint
and Isolation (a part of the controller, not a base class).

The tool gives: ``app``, ``ui`` (``excl_cb``, ``excl_count_value``, ``level``), ``zone_sources()`` and ``selector``
(an ``AreaSelector``).
"""

import gettext
import builtins
import appTranslation as fcTranslate

from appHandlers.appHistory import history_of
from appObjects.AppObjectTemplate import FlatCAMObj
from appCommon.ExclusionZones import distinct_zones
from appPlugins.Common.clearing.ui_sections import exclusion_on

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class ZonePanel:
    def __init__(self, tool, plugin, on_changed=None):
        """
        :param tool:        the plugin controller
        :param plugin:      'isolation', 'ncc' or 'paint': the zones of this plugin
        :param on_changed:  called when the zones or their use change (NCC / Paint: clear the preview)
        """
        self.tool = tool
        self.plugin = plugin
        self.on_changed = on_changed
        # the sources whose zones the panel shows now (the zones are the data of the source objects)
        self._view = []
        # the zones a run would remove when the preview was last cleared or kept (see update_count)
        self._in_force = ()
        # True after the panel closed or another plugin replaced it: the signals that stay connected (project
        # selection, combo box, app cleanup) must not show the zones again until the panel opens
        self.closed = False

    @property
    def app(self):
        return self.tool.app

    def live_sources(self):
        """The sources of the panel that can own zones and are still in the project."""
        try:
            sources = self.tool.zone_sources()
        except RuntimeError:        # the panel is gone
            return []
        return [obj for obj in sources if isinstance(obj, FlatCAMObj) and not obj.deleted]

    def source_zones(self):
        """The zones of this plugin on the sources of the panel, each distinct zone once."""
        return distinct_zones([zone for obj in self.live_sources() for zone in obj.zones_of(self.plugin)])

    def zones_of_source(self, source):
        """The zones that limit one source in a run: its own only, each distinct zone once; [] when they do not
        apply. With "All selected objects" each source is limited by its own zones, not by those of the others."""
        if not self.is_on():
            return []
        return distinct_zones(source.zones_of(self.plugin))

    def is_on(self):
        """The zones apply: Advanced mode and the Exclusion Areas check box is set."""
        try:
            return exclusion_on(self.tool.ui)
        except RuntimeError:
            return False

    def refresh(self, *args):
        """Show the zones of the sources (and only those) on the canvas if they apply, and count them."""
        if self.closed:
            return
        sources = self.live_sources() if self.is_on() else []
        for obj in self._view:
            if not any(obj is source for source in sources):
                self._stop_showing(obj)
        for obj in sources:
            if not any(obj is shown for shown in self._view):
                obj.zones_changed.connect(self.update_count)
        self._view = sources
        for obj in sources:
            obj.set_zones_panel_open(self.plugin)
        self.update_count()

    def hide(self):
        """The panel stops showing the zones (the zones stay with their objects)."""
        for obj in self._view:
            self._stop_showing(obj)
        self._view = []

    def _stop_showing(self, obj):
        try:
            obj.zones_changed.disconnect(self.update_count)
        except (TypeError, RuntimeError):
            pass
        if not obj.deleted and obj.zones_panel_open == self.plugin:
            obj.set_zones_panel_open(False)

    def update_count(self, *args):
        """Show the count of the zones of the sources; the zones changed."""
        try:
            zones = self.source_zones()
            self.tool.ui.excl_count_value.setText(str(len(zones)))
            # what a run removes: nothing while the zones do not apply
            in_force = tuple(zone.wkb for zone in zones) if self.is_on() else ()
        except (RuntimeError, AttributeError):
            return
        # a click in the project tree or a combo box that keeps the zones in force leaves the preview alone
        changed = in_force != self._in_force
        self._in_force = in_force
        if changed and self.on_changed is not None:
            self.on_changed()

    def on_drawn(self, zones):
        """Add the drawn zones to every source of the panel: 1 undo step."""
        sources = self.live_sources()
        if not sources:
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no object to add the exclusion zones to."))
            return
        with history_of(self.app).group(_("Add exclusion zones")):
            for obj in sources:
                obj.add_exclusion_zones(self.plugin, zones)
        self.refresh()
        self.app.inform.emit('[success] %s' % _("Exclusion zones added."))

    def on_add(self):
        """Let the user draw exclusion zones (right click or Escape ends)."""
        selector = self.tool.selector
        if selector.active:
            return
        if not self.live_sources():
            self.app.inform.emit('[WARNING_NOTCL] %s' % _("There is no object to add the exclusion zones to."))
            return
        if self.on_changed is not None:
            self.on_changed()
        selector.start('exclusion', commit=self.on_drawn)

    def on_delete(self):
        """Remove the zones of this plugin from every source of the panel: 1 undo step."""
        with history_of(self.app).group(_("Clear exclusion zones")):
            for obj in self.live_sources():
                obj.clear_exclusion_zones(self.plugin)
        self.refresh()
