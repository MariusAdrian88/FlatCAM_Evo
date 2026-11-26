# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

from PyQt6 import QtWidgets, QtCore

from appGUI.GUIElements import FCDetachableTab

import gettext
import appTranslation as fcTranslate
import builtins

fcTranslate.apply_language('strings')
if '_' not in builtins.__dict__:
    _ = gettext.gettext

WIDTH_KEY = 'global_camera_pane_width'
PLOT_TAB_NAME = 'plotarea_tab'


class CameraPane(QtCore.QObject):
    """The camera image in a pane beside the central plot.

    A QSplitter (plot left, camera right) exists only while the pane is open and not detached. The pane is an
    FCDetachableTab with one "Camera" tab: dragged out, it gets its own window and the splitter goes away; when the
    window closes or the tab is dropped back, a new splitter is made. If the plot tab is detached, the splitter is made
    in the window of the plot. Closing the pane keeps the camera view (hidden, never in the dock) and stops the camera."""

    def __init__(self, app, view, stop_camera):
        """
        :param app: the application; app.ui has plot_tab_area, plot_tab and right_lay
        :param view: the camera view widget; it is shown only in the pane, never in the dock
        :param stop_camera: called when the pane is closed: the camera runs only while the pane is shown
        """
        super().__init__()
        self.app = app
        self.view = view
        self.holder = view.parentWidget()   # keeps the hidden view while the pane is closed
        self.stop_camera = stop_camera
        self.pane = None
        self.page = None
        self.splitter = None
        self._place = None      # (wrapped widget, its layout, index, stretch) while the splitter exists
        self._detached = False
        self._closing = False
        self._connected = False

    # ---- state
    def is_open(self):
        return self.pane is not None

    def is_detached(self):
        return self.pane is not None and self._detached

    def has_splitter(self):
        return self.splitter is not None

    # ---- open and close
    def open(self):
        """Open the pane, or show it when it is open; never a second pane."""
        if self.pane is not None:
            self._show()
            return
        self.page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(self.page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.view, 1)
        self.view.show()
        self.view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.pane = FCDetachableTab(protect=False)
        self.pane.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.pane.callback_on_close = self.close
        self.pane.addTab(self.page, _("Camera"))
        self.pane.tab_detached.connect(self._on_detached)
        self.pane.tab_attached.connect(self._on_attached)
        area = self.app.ui.plot_tab_area
        if not self._connected:
            area.tab_detached.connect(self._on_plot_detached)
            area.tab_attached.connect(self._on_plot_attached)
            self._connected = True
        self._detached = False
        if not self._wrap():
            self._discard_pane(stop=False)

    def _show(self):
        if self._detached:
            window = self._pane_window()
            if window is not None:
                window.show()
                window.raise_()
                window.activateWindow()
        elif self.pane is not None:
            self.pane.show()

    def close(self):
        """Close the pane: the plot goes back, the view is kept hidden, the camera stops."""
        if self.pane is None or self._closing:
            return
        self._closing = True
        try:
            self._discard_pane(stop=True)
        finally:
            self._closing = False

    def _discard_pane(self, stop):
        pane = self.pane
        self._unwrap()
        if self._detached:
            # the window gives the page back to the (hidden) pane
            pane.closeDetachedTabs()
        self._detached = False
        if self._connected:
            area = self.app.ui.plot_tab_area
            for signal, slot in ((area.tab_detached, self._on_plot_detached),
                                 (area.tab_attached, self._on_plot_attached)):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            self._connected = False
        # the view never goes back to the dock: the pane keeps it, hidden
        self.view.hide()
        self.view.setParent(self.holder)
        self.pane = None
        self.page = None
        pane.hide()
        pane.setParent(None)
        pane.deleteLater()
        if stop:
            self.stop_camera()

    # ---- the splitter
    def _pane_window(self):
        for window in self.pane.detachedTabs.values():
            if window.contentWidget is self.page:
                return window
        return None

    def _plot_window(self):
        """The window of the detached plot tab, or None when the plot tab is in the tab area."""
        ui = self.app.ui
        if ui.plot_tab_area.indexOf(ui.plot_tab) >= 0:
            return None
        for window in ui.plot_tab_area.detachedTabs.values():
            if window.contentWidget is ui.plot_tab:
                return window
        return None

    def _plot_target(self):
        """The widget that goes into the splitter, and the layout that holds it."""
        window = self._plot_window()
        if window is not None:
            return self.app.ui.plot_tab, window.central_layout
        return self.app.ui.plot_tab_area, self.app.ui.right_lay

    def _wrap(self):
        target, layout = self._plot_target()
        index = layout.indexOf(target)
        if index < 0:
            return False
        stretch = layout.stretch(index)
        size = target.size()
        width = self._saved_width()
        layout.removeWidget(target)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        # the new splitter has the size of the plot it replaces, so the sizes below are exact before the layout runs
        self.splitter.resize(size)
        self.splitter.addWidget(target)
        self.splitter.addWidget(self.pane)
        layout.insertWidget(index, self.splitter, stretch)
        self._place = (target, layout, index, stretch)
        self.pane.show()
        room = max(size.width() - self.splitter.handleWidth(), 2)
        if width <= 0:
            width = room // 2
        width = min(width, room - 1)
        self.splitter.setSizes([max(room - width, 1), width])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._remember_width)
        return True

    def _unwrap(self):
        """Remove the splitter: the plot goes back to its place (same layout, same index)."""
        if self.splitter is None:
            return
        self._remember_width()
        splitter = self.splitter
        target, layout, index, stretch = self._place
        self.splitter = None
        self._place = None
        layout.removeWidget(splitter)
        layout.insertWidget(index, target, stretch)
        target.show()
        self.pane.hide()
        self.pane.setParent(None)
        splitter.setParent(None)
        splitter.deleteLater()

    def _saved_width(self):
        """The width the user gave the pane; 0 when there is none yet (the first open takes a share of the plot)."""
        try:
            width = int(self.app.options.get(WIDTH_KEY, 0))
        except (TypeError, ValueError):
            return 0
        return max(width, 0)

    def _remember_width(self, *_args):
        if self.splitter is None:
            return
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self.app.options[WIDTH_KEY] = int(sizes[1])

    # ---- the pane tab dragged out and back
    def _on_detached(self, _window, _name):
        if self._closing:
            return
        self._detached = True
        self._unwrap()

    def _on_attached(self, _name):
        if self._closing or self.pane is None:
            return
        self._detached = False
        self._wrap()

    # ---- the plot tab dragged out and back
    def _on_plot_detached(self, window, _name):
        if self._closing or self.splitter is None or window.contentWidget is not self.app.ui.plot_tab:
            return
        self._unwrap()
        self._wrap()

    def _on_plot_attached(self, name):
        if self._closing or self.splitter is None or name != PLOT_TAB_NAME:
            return
        target, layout, _index, _stretch = self._place
        if target is not self.app.ui.plot_tab:
            return
        # the window of the plot goes away with its splitter: keep the pane out of it
        self.pane.hide()
        self.pane.setParent(None)
        self.splitter.setParent(None)
        self.splitter.deleteLater()
        self.splitter = None
        self._place = None
        self._wrap()
