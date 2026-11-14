
from PyQt6 import QtWidgets

from appGUI.preferences.utilities.AutoCompletePrefGroupUI import AutoCompletePrefGroupUI
from appGUI.preferences.utilities.FAGrbPrefGroupUI import FAGrbPrefGroupUI
from appGUI.preferences.utilities.FAGcoPrefGroupUI import FAGcoPrefGroupUI
from appGUI.preferences.utilities.FAExcPrefGroupUI import FAExcPrefGroupUI
from appGUI.preferences.utilities.FAFlatPrefGroupUI import FAFlatPrefGroupUI

from appGUI.ColumnarFlowLayout import ColumnarFlowLayout


class UtilPreferencesUI(QtWidgets.QWidget):

    def __init__(self, app, parent=None):
        QtWidgets.QWidget.__init__(self, parent=parent)
        self.layout = ColumnarFlowLayout()
        self.setLayout(self.layout)

        self.fa_excellon_group = FAExcPrefGroupUI(app=app)
        self.fa_excellon_group.setMinimumWidth(260)

        self.fa_gcode_group = FAGcoPrefGroupUI(app=app)
        self.fa_gcode_group.setMinimumWidth(260)

        self.fa_gerber_group = FAGrbPrefGroupUI(app=app)
        self.fa_gerber_group.setMinimumWidth(260)

        self.fa_flat_group = FAFlatPrefGroupUI(app=app)
        self.fa_flat_group.setMinimumWidth(260)

        self.kw_group = AutoCompletePrefGroupUI(app=app)
        self.kw_group.setMinimumWidth(260)

        # Unregister buttons and the FlatCAM files group; app.regFK exists by the time a button is clicked
        for btn, obj_type, unregister in (
                (self.fa_excellon_group.exc_unreg_btn, 'excellon', True),
                (self.fa_gcode_group.gco_unreg_btn, 'gcode', True),
                (self.fa_gerber_group.grb_unreg_btn, 'gerber', True),
                (self.fa_flat_group.unreg_btn, 'flatcam', True),
                (self.fa_flat_group.reg_btn, 'flatcam', False)):
            btn.clicked.connect(
                lambda checked=False, t=obj_type, u=unregister: self._on_assoc(app, t, u))

        self.vlay = QtWidgets.QVBoxLayout()

        self.vlay.addWidget(self.fa_excellon_group)
        self.vlay.addWidget(self.fa_gcode_group)
        self.vlay.addStretch()

        self.layout.addLayout(self.vlay)
        self.layout.addWidget(self.fa_gerber_group)
        self.layout.addWidget(self.fa_flat_group)

        self.layout.addWidget(self.kw_group)

        self.layout.addStretch(1)

    @staticmethod
    def _on_assoc(app, obj_type, unregister):
        reg = app.regFK
        types = ('project', 'config') if obj_type == 'flatcam' else (obj_type,)
        for one_type in types:
            if unregister:
                reg.on_unregister_files(obj_type=one_type)
            else:
                reg.on_register_files(obj_type=one_type)
