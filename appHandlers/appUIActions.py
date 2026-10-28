# FlatCAM Evo: 2D Post-processing for Manufacturing
# Date: 2/5/2014
# MIT Licence
# Modified by Marius Stanciu (2019)

from PyQt6 import QtGui, QtWidgets, QtCore
from PyQt6.QtCore import QPoint, Qt

import webbrowser
from copy import deepcopy
import traceback
import random
import platform

from appGUI.GUIElements import (
    FCLabel,
    FCButton,
    GLay,
    FCMessageBox,
    FCInputDoubleSpinner,
    FCInputSpinner,
    VerticalScrollArea,
)
from Bookmark import BookmarkManager
from appDatabase import ToolsDB2

import builtins
import gettext

if '_' not in builtins.__dict__:
    _ = gettext.gettext


def _find_tools_database(ui):
    try:
        plot_area = ui.plot_tab_area
        for idx in range(plot_area.count()):
            if plot_area.tabText(idx) == _("Tools Database"):
                return plot_area.widget(idx), None
    except (AttributeError, RuntimeError):
        pass

    try:
        detached_tabs = plot_area.detachedTabs
    except (AttributeError, RuntimeError, UnboundLocalError):
        return None, None

    for name, detached_window in list(detached_tabs.items()):
        try:
            database = detached_window.contentWidget
            if database.objectName() == 'database_tab' or str(name).endswith(_("Tools Database")):
                return database, detached_window
        except (AttributeError, RuntimeError):
            try:
                del detached_tabs[name]
            except (KeyError, RuntimeError):
                pass
    return None, None


class AppUIActions(QtCore.QObject):
    """Handler for UI action methods: dialogs, preferences, tabs, workspace, grid."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.log = app.log
        self.inform = app.inform
        self.options = app.options
        self.defaults = app.defaults
        self.ui = app.ui

    def _pref(self, key):
        """Return an application preference with its factory-default fallback."""
        factory_defaults = getattr(self.defaults, "factory_defaults", {})
        return self.defaults.get(key, factory_defaults.get(key))

    def _ensure_option(self, key):
        """Return a mutable application option, initializing missing values safely."""
        if key not in self.options:
            self.options[key] = deepcopy(self._pref(key))
        return self.options.get(key, self._pref(key))

    # --------------------------------------------------------------------------
    # Dialogs
    # --------------------------------------------------------------------------

    def on_about(self):
        """
        Displays the "about" dialog found in the Menu --> Help.

        :return: None
        """
        self.defaults.report_usage("on_about")

        version = self.app.version
        version_date = self.app.version_date
        beta = self.app.beta

        class AboutDialog(QtWidgets.QDialog):
            # noinspection PyUnresolvedReferences
            def __init__(self, app, parent):
                QtWidgets.QDialog.__init__(self, parent=parent)

                self.app = app
                self.app_icon = self.app.ui.app_icon

                # Icon and title
                self.setWindowIcon(self.app_icon)
                self.setWindowTitle(_("About"))
                self.resize(600, 200)

                logo = FCLabel()
                logo.setPixmap(QtGui.QPixmap(self.app.resource_location + '/app256.png'))

                title_text = _("PCB Manufacturing files Viewer/Editor with Plugins")
                development_label = _("Development")
                download_label = _("DOWNLOAD")
                issue_label = _("Issue tracker")

                devel_link = "https://github.com/MariusAdrian88/FlatCAM_Evo/tree/1.0"
                download_link = "https://github.com/MariusAdrian88/FlatCAM_Evo"
                issues_link = "https://github.com/MariusAdrian88/FlatCAM_Evo/issues"

                title = FCLabel(
                    f"<font size=8><B>FlatCAM Evo</B></font><BR>"
                    f"{title_text}<BR>"
                    f"<BR><BR>"
                    f"<BR><BR>"
                    f'<a href="{devel_link}"><B>{development_label}</B></a><BR>'
                    f'<a href="{download_link}"><B>{download_label}</B></a><BR>'
                    f'<a href="{issues_link}"><B>{issue_label}</B></a><BR>'
                )
                title.setOpenExternalLinks(True)

                closebtn = FCButton(_("Close"))

                tab_widget = QtWidgets.QTabWidget()
                description_label = FCLabel(
                    "FlatCAM Evo {version} {beta} ({date}) - {arch}<br>"
                    "<a href = \"http://flatcam.org/\">http://flatcam.org</a><br>".format(
                        version=version,
                        beta=('BETA' if beta else ''),
                        date=version_date,
                        arch=platform.architecture()[0])
                )
                description_label.setOpenExternalLinks(True)

                lic_lbl_header = FCLabel(
                    '%s:<br>%s<br>' % (
                        _('Licensed under the MIT license'),
                        "<a href = \"http://www.opensource.org/licenses/mit-license.php\">"
                        "http://www.opensource.org/licenses/mit-license.php</a>"
                    )
                )
                lic_lbl_header.setOpenExternalLinks(True)

                lic_lbl_body = FCLabel(
                    _(
                        'Permission is hereby granted, free of charge, to any person obtaining a copy\n'
                        'of this software and associated documentation files (the "Software"), to deal\n'
                        'in the Software without restriction, including without limitation the rights\n'
                        'to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n'
                        'copies of the Software, and to permit persons to whom the Software is\n'
                        'furnished to do so, subject to the following conditions:\n\n'
                        'The above copyright notice and this permission notice shall be included in\n'
                        'all copies or substantial portions of the Software.\n\n'
                        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
                        'IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n'
                        'FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n'
                        'AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n'
                        'LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n'
                        'OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN\n'
                        'THE SOFTWARE.'
                    )
                )

                attributions_label = FCLabel(
                    _(
                        'Some of the icons used are from the following sources:<br>'
                        '<div>Icons by <a href="https://www.flaticon.com/authors/freepik" '
                        'title="Freepik">Freepik</a> from <a href="https://www.flaticon.com/"             '
                        'title="Flaticon">www.flaticon.com</a></div>'
                        '<div>Icons by <a target="_blank" href="https://icons8.com">Icons8</a></div>'
                        'Icons by <a href="http://www.onlinewebfonts.com">oNline Web Fonts</a>'
                        '<div>Icons by <a href="https://www.flaticon.com/authors/pixel-perfect" '
                        'title="Pixel perfect">Pixel perfect</a> from <a href="https://www.flaticon.com/" '
                        'title="Flaticon">www.flaticon.com</a></div>'
                        '<div>Icons by <a href="https://www.flaticon.com/authors/anggara" '
                        'title="Anggara"> Anggara </a> from <a href="https://www.flaticon.com/" '
                        'title="Flaticon">www.flaticon.com</a></div>'
                        '<div>Icons by <a href="https://www.flaticon.com/authors/kharisma" '
                        'title="Kharisma"> Kharisma </a> from <a href="https://www.flaticon.com/" '
                        'title="Flaticon">www.flaticon.com</a></div>'
                    )
                )
                attributions_label.setOpenExternalLinks(True)

                # layouts
                layout1 = QtWidgets.QVBoxLayout()
                layout1_1 = QtWidgets.QHBoxLayout()
                layout1_2 = QtWidgets.QHBoxLayout()

                layout2 = QtWidgets.QHBoxLayout()
                layout3 = QtWidgets.QHBoxLayout()

                self.setLayout(layout1)
                layout1.addLayout(layout1_1)
                layout1.addLayout(layout1_2)

                layout1.addLayout(layout2)
                layout1.addLayout(layout3)

                layout1_1.addStretch()
                layout1_1.addWidget(description_label)
                layout1_2.addWidget(tab_widget)

                self.splash_tab = QtWidgets.QWidget()
                self.splash_tab.setObjectName("splash_about")
                self.splash_tab_layout = QtWidgets.QHBoxLayout(self.splash_tab)
                self.splash_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.splash_tab, _("Splash"))

                self.programmmers_tab = QtWidgets.QWidget()
                self.programmmers_tab.setObjectName("programmers_about")
                self.programmmers_tab_layout = QtWidgets.QVBoxLayout(self.programmmers_tab)
                self.programmmers_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.programmmers_tab, _("Programmers"))

                self.translators_tab = QtWidgets.QWidget()
                self.translators_tab.setObjectName("translators_about")
                self.translators_tab_layout = QtWidgets.QVBoxLayout(self.translators_tab)
                self.translators_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.translators_tab, _("Translators"))

                self.license_tab = QtWidgets.QWidget()
                self.license_tab.setObjectName("license_about")
                self.license_tab_layout = QtWidgets.QVBoxLayout(self.license_tab)
                self.license_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.license_tab, _("License"))

                self.attributions_tab = QtWidgets.QWidget()
                self.attributions_tab.setObjectName("attributions_about")
                self.attributions_tab_layout = QtWidgets.QVBoxLayout(self.attributions_tab)
                self.attributions_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.attributions_tab, _("Attributions"))

                self.splash_tab_layout.addWidget(logo, stretch=0)
                self.splash_tab_layout.addWidget(title, stretch=1)

                pal = QtGui.QPalette()
                pal.setColor(QtGui.QPalette.ColorRole.Window, Qt.GlobalColor.white)

                programmers = [
                    {'name': "Denis Hayrullin", 'description': '', 'email': ''},
                    {'name': "Kamil Sopko", 'description': '', 'email': ''},
                    {'name': "David Robertson", 'description': '', 'email': ''},
                    {'name': "Matthieu Berthomé", 'description': '', 'email': ''},
                    {'name': "Mike Evans", 'description': '', 'email': ''},
                    {'name': "Victor Benso", 'description': '', 'email': ''},
                    {'name': "Jørn Sandvik Nilsson", 'description': '', 'email': ''},
                    {'name': "Lei Zheng", 'description': '', 'email': ''},
                    {'name': "Leandro Heck", 'description': '', 'email': ''},
                    {'name': "Marco A Quezada", 'description': '', 'email': ''},
                    {'name': "Cedric Dussud", 'description': '', 'email': ''},
                    {'name': "Chris Hemingway", 'description': '', 'email': ''},
                    {'name': "David Kahler", 'description': '', 'email': ''},
                    {'name': "Damian Wrobel", 'description': '', 'email': ''},
                    {'name': "Daniel Sallin", 'description': '', 'email': ''},
                    {'name': "Bruno Vunderl", 'description': '', 'email': ''},
                    {'name': "Gonzalo Lopez", 'description': '', 'email': ''},
                    {'name': "Jakob Staudt", 'description': '', 'email': ''},
                    {'name': "Mike Smith", 'description': '', 'email': ''},
                    {'name': "Barnaby Walters", 'description': '', 'email': ''},
                    {'name': "Steve Martina", 'description': '', 'email': ''},
                    {'name': "Thomas Duffin", 'description': '', 'email': ''},
                    {'name': "Andrey Kultyapov", 'description': '', 'email': ''},
                    {'name': "Alex Lazar", 'description': '', 'email': ''},
                    {'name': "Chris Breneman", 'description': '', 'email': ''},
                    {'name': "Eric Varsanyi", 'description': '', 'email': ''},
                    {'name': "Lubos Medovarsky", 'description': '', 'email': ''},
                    {'name': "@Idechix", 'description': '', 'email': ''},
                    {'name': "@SM", 'description': '', 'email': ''},
                    {'name': "@grbf", 'description': '', 'email': ''},
                    {'name': "@Symonty", 'description': '', 'email': ''},
                    {'name': "@mgix", 'description': '', 'email': ''},
                    {'name': "Emily Ellis", 'description': '', 'email': ''},
                    {'name': "Maksym Stetsyuk", 'description': '', 'email': ''},
                    {'name': "Peter Nitschneider", 'description': '', 'email': ''},
                    {'name': "Bogusz Jagoda", 'description': '', 'email': ''},
                    {'name': "Andre Spahlinger", 'description': '', 'email': ''},
                    {'name': "Hans Boot", 'description': '', 'email': ''},
                    {'name': "Dmitriy Klabukov", 'description': '', 'email': ''},
                    {'name': "Robert Niemöller", 'description': '', 'email': ''},
                    {'name': "Adam Coddington", 'description': '', 'email': ''},
                    {'name': "Ali Khalil", 'description': '', 'email': ''},
                    {'name': "Maftei Albert-Alexandru", 'description': '', 'email': ''},
                    {'name': "@gargamel778", 'description': '', 'email': ''},
                    {'name': "@ErkanGench", 'description': '', 'email': ''},
                ]

                self.prog_grid_lay = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 0, 1])
                self.prog_grid_lay.setHorizontalSpacing(20)

                prog_widget = QtWidgets.QWidget()
                prog_widget.setLayout(self.prog_grid_lay)
                prog_scroll = QtWidgets.QScrollArea()
                prog_scroll.setWidget(prog_widget)
                prog_scroll.setWidgetResizable(True)
                prog_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                prog_scroll.setPalette(pal)

                self.programmmers_tab_layout.addWidget(prog_scroll)

                # Headers
                self.prog_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("Programmer")), 0, 0)
                self.prog_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("Status")), 0, 1)
                self.prog_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("E-mail")), 0, 2)

                # FlatCAM Author
                self.prog_grid_lay.addWidget(FCLabel('%s' % "Juan Pablo Caram"), 1, 0)
                self.prog_grid_lay.addWidget(FCLabel('%s' % _("FlatCAM Author")), 1, 1)

                # FlatCAM EVO Author
                self.prog_grid_lay.addWidget(FCLabel('%s' % "Marius Stanciu"), 2, 0)
                self.prog_grid_lay.addWidget(FCLabel('%s' % _("FlatCAM Evo Author/Maintainer")), 2, 1)
                self.prog_grid_lay.addWidget(FCLabel('%s' % "<marius_adrian@yahoo.com>"), 2, 2)
                self.prog_grid_lay.addWidget(FCLabel(''), 3, 0)

                # randomize the order of the programmers at each launch
                random.shuffle(programmers)
                line = 4
                for prog in programmers:
                    self.prog_grid_lay.addWidget(FCLabel('%s' % prog['name']), line, 0)
                    self.prog_grid_lay.addWidget(FCLabel('%s' % prog['description']), line, 1)
                    self.prog_grid_lay.addWidget(FCLabel('%s' % prog['email']), line, 2)

                    line += 1
                    if (line % 4) == 0:
                        self.prog_grid_lay.addWidget(FCLabel(''), line, 0)
                        line += 1

                self.translator_grid_lay = GLay(v_spacing=5, h_spacing=3, c_stretch=[0, 0, 1, 0])

                translators = [
                    {'language': 'BR - Portuguese', 'authors': [("Carlos Stein", '<carlos.stein@gmail.com>')]},
                    {'language': 'Chinese Simplified', 'authors': [("余俊潇 (Yu Junxiao)", '')]},
                    {'language': 'French', 'authors': [("Michel Maciejewski", '<micmac589@gmail.com>'), ('Olivier Cornet', '')]},
                    {'language': 'Italian', 'authors': [("Massimiliano Golfetto", '<golfetto.pcb@gmail.com>')]},
                    {'language': 'German', 'authors': [("Marius Stanciu (Google-Tr)", ''), ('Jens Karstedt', ''), ('Detlef Eckardt', '')]},
                    {'language': 'Romanian', 'authors': [("Marius Stanciu", '<marius_adrian@yahoo.com>')]},
                    {'language': 'Russian', 'authors': [("Andrey Kultyapov", '<camellan@yandex.ru>')]},
                    {'language': 'Spanish', 'authors': [("Marius Stanciu (Google-Tr)", '')]},
                    {'language': 'Turkish', 'authors': [("Mehmet Kaya", '<malatyakaya480@gmail.com>')]},
                ]

                trans_widget = QtWidgets.QWidget()
                trans_widget.setLayout(self.translator_grid_lay)
                trans_scroll = QtWidgets.QScrollArea()
                trans_scroll.setWidget(trans_widget)
                trans_scroll.setWidgetResizable(True)
                trans_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                trans_scroll.setPalette(pal)
                self.translators_tab_layout.addWidget(trans_scroll)

                self.translator_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("Language")), 0, 0)
                self.translator_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("Translator")), 0, 1)
                self.translator_grid_lay.addWidget(FCLabel('<b>%s</b>' % _("E-mail")), 0, 2)

                line = 1
                for i in translators:
                    self.translator_grid_lay.addWidget(FCLabel('%s' % i['language'], color='blue', bold=False), line, 0)
                    for author in range(len(i['authors'])):
                        auth_widget = FCLabel('%s' % i['authors'][author][0])
                        email_widget = FCLabel('%s' % i['authors'][author][1])
                        self.translator_grid_lay.addWidget(auth_widget, line, 1)
                        self.translator_grid_lay.addWidget(email_widget, line, 2)
                        line += 1

                    line += 1

                self.translator_grid_lay.setColumnStretch(1, 1)
                self.translators_tab_layout.addStretch()

                self.license_tab_layout.addWidget(lic_lbl_header)
                self.license_tab_layout.addWidget(lic_lbl_body)

                self.license_tab_layout.addStretch()

                self.attributions_tab_layout.addWidget(attributions_label)
                self.attributions_tab_layout.addStretch()

                layout3.addStretch()
                layout3.addWidget(closebtn)

                closebtn.clicked.connect(self.accept)

        AboutDialog(app=self.app, parent=self.ui).exec()

    def on_howto(self):
        """
        Displays the "about" dialog found in the Menu --> Help.

        :return: None
        """

        class HowtoDialog(QtWidgets.QDialog):
            def __init__(self, app, parent):
                QtWidgets.QDialog.__init__(self, parent=parent)

                self.app = app
                self.app_icon = self.app.ui.app_icon

                open_source_link = "<a href = 'https://opensource.org/'><b>Open Source</b></a>"
                new_features_link = "<a href = 'https://github.com/MariusAdrian88/FlatCAM_Evo/pulls'>" \
                                    "<b>click</b></a>"

                bugs_link = "<a href = 'https://github.com/MariusAdrian88/FlatCAM_Evo/issues/new'><b>click</b></a>"
                donation_link = "<a href = 'https://www.paypal.com/cgi-bin/webscr?cmd=_" \
                                "donations&business=WLTJJ3Q77D98L&currency_code=USD&source=url'><b>click</b></a>"

                # Icon and title
                self.setWindowIcon(self.app_icon)
                self.setWindowTitle('%s ...' % _("How To"))
                self.resize(750, 375)

                logo = FCLabel()
                logo.setPixmap(QtGui.QPixmap(self.app.resource_location + '/contribute256.png'))

                content = FCLabel(
                    "%s<br>"
                    "%s<br><br>"
                    "%s,<br>"
                    "%s<br>"
                    "<ul>"
                    "<li> &nbsp;%s %s</li>"
                    "<li> &nbsp;%s %s</li>"
                    "</ul>"
                    "<br><br>"
                    "%s <br>"
                    "<span style='color: blue;'>%s</span> %s %s<br>" %
                    (
                        _("This program is %s and free in a very wide meaning of the word.") % open_source_link,
                        _("Yet it cannot evolve without <b>contributions</b>."),
                        _("If you want to see this application grow and become better and better"),
                        _("you can <b>contribute</b> to the development yourself by:"),
                        _("Pull Requests on the GitHub repository, if you are a developer"),
                        new_features_link,
                        _("Bug Reports by providing the steps required to reproduce the bug"),
                        bugs_link,
                        _("If you like what you have seen so far ..."),
                        _("Donations are NOT required."), _("But they are welcomed"),
                        donation_link
                    )
                )
                content.setOpenExternalLinks(True)

                # palette
                pal = QtGui.QPalette()
                pal.setColor(QtGui.QPalette.ColorRole.Base, Qt.GlobalColor.white)

                # layouts
                main_layout = QtWidgets.QVBoxLayout()
                self.setLayout(main_layout)

                tab_layout = QtWidgets.QHBoxLayout()
                buttons_hlay = QtWidgets.QHBoxLayout()

                main_layout.addLayout(tab_layout)
                main_layout.addLayout(buttons_hlay)

                tab_widget = QtWidgets.QTabWidget()
                tab_layout.addWidget(tab_widget)

                closebtn = FCButton(_("Close"))
                buttons_hlay.addStretch()
                buttons_hlay.addWidget(closebtn)

                # CONTRIBUTE section
                self.intro_tab = QtWidgets.QWidget()
                self.intro_tab_layout = QtWidgets.QHBoxLayout(self.intro_tab)
                self.intro_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.intro_tab, _("Contribute"))

                self.grid_lay = GLay(v_spacing=5, h_spacing=20)

                intro_wdg = QtWidgets.QWidget()
                intro_wdg.setLayout(self.grid_lay)
                intro_scroll_area = QtWidgets.QScrollArea()
                intro_scroll_area.setWidget(intro_wdg)
                intro_scroll_area.setWidgetResizable(True)
                intro_scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                intro_scroll_area.setPalette(pal)

                self.grid_lay.addWidget(logo, 0, 0)
                self.grid_lay.addWidget(content, 0, 1)
                self.intro_tab_layout.addWidget(intro_scroll_area)

                # LINKS EXCHANGE section
                self.links_tab = QtWidgets.QWidget()
                self.links_tab_layout = QtWidgets.QVBoxLayout(self.links_tab)
                self.links_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.links_tab, _("Links Exchange"))

                self.links_lay = QtWidgets.QHBoxLayout()

                links_wdg = QtWidgets.QWidget()
                links_wdg.setLayout(self.links_lay)
                links_scroll_area = QtWidgets.QScrollArea()
                links_scroll_area.setWidget(links_wdg)
                links_scroll_area.setWidgetResizable(True)
                links_scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                links_scroll_area.setPalette(pal)

                self.links_lay.addWidget(
                    FCLabel('%s' % _("Soon ...")), alignment=Qt.AlignmentFlag.AlignCenter)
                self.links_tab_layout.addWidget(links_scroll_area)

                # HOW TO section
                self.howto_tab = QtWidgets.QWidget()
                self.howto_tab_layout = QtWidgets.QVBoxLayout(self.howto_tab)
                self.howto_tab_layout.setContentsMargins(2, 2, 2, 2)
                tab_widget.addTab(self.howto_tab, _("How To's"))

                self.howto_lay = QtWidgets.QHBoxLayout()

                howto_wdg = QtWidgets.QWidget()
                howto_wdg.setLayout(self.howto_lay)
                howto_scroll_area = QtWidgets.QScrollArea()
                howto_scroll_area.setWidget(howto_wdg)
                howto_scroll_area.setWidgetResizable(True)
                howto_scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
                howto_scroll_area.setPalette(pal)

                self.howto_lay.addWidget(
                    FCLabel('%s' % _("Soon ...")), alignment=Qt.AlignmentFlag.AlignCenter)
                self.howto_tab_layout.addWidget(howto_scroll_area)

                # BUTTONS section
                closebtn.clicked.connect(self.accept)

        HowtoDialog(app=self.app, parent=self.ui).exec()

    # --------------------------------------------------------------------------
    # Bookmarks
    # --------------------------------------------------------------------------

    def install_bookmarks(self, book_dict=None):
        """
        Install the bookmarks actions in the Help menu -> Bookmarks

        :param book_dict:   a dict having the actions text as keys and the weblinks as the values
        :return:            None
        """

        bookmarks = self._ensure_option("global_bookmarks")
        if book_dict is None:
            bookmarks.update(
                {
                    '1': ['FlatCAM', "http://flatcam.org"],
                    '2': [_('Backup Site'), ""]
                }
            )
        else:
            bookmarks.clear()
            bookmarks.update(book_dict)

        # Remove the generated actions while preserving the Bookmark Manager action.
        for act in self.ui.menuhelp_bookmarks.actions():
            try:
                act.triggered.disconnect()
            except TypeError:
                pass

            if act is not self.ui.menuhelp_bookmarks_manager:
                self.ui.menuhelp_bookmarks.removeAction(act)

        bm_limit = int(self.options.get("global_bookmarks_limit", self._pref("global_bookmarks_limit")))
        sorted_bookmarks = sorted(
            list(bookmarks.items())[:bm_limit],
            key=lambda item: int(item[0])
        )
        for entry, bookmark in sorted_bookmarks:
            title, weblink = bookmark
            act = QtGui.QAction(parent=self.ui.menuhelp_bookmarks)
            act.setText(title)
            act.setIcon(QtGui.QIcon(self.app.resource_location + '/link16.png'))

            if title == _('Backup Site') and weblink == "":
                act.triggered.connect(self.on_backup_site)
            else:
                act.triggered.connect(lambda checked=False, link=weblink: webbrowser.open(link))

            self.ui.menuhelp_bookmarks.insertAction(self.ui.menuhelp_bookmarks_manager, act)

        self.ui.menuhelp_bookmarks_manager.triggered.connect(self.on_bookmarks_manager)

    def on_bookmarks_manager(self):
        """
        Adds the bookmark manager in a Tab in Plot Area.

        :return:
        """
        for idx in range(self.ui.plot_tab_area.count()):
            if self.ui.plot_tab_area.tabText(idx) == _("Bookmarks Manager"):
                # there can be only one instance of Bookmark Manager at one time
                return

        # BookDialog(app=self, storage=self.options.get("global_bookmarks", self._pref("global_bookmarks")), parent=self.ui).exec()
        self.app.book_dialog_tab = BookmarkManager(app=self.app, storage=self._ensure_option("global_bookmarks"), parent=self.ui)
        self.app.book_dialog_tab.setObjectName("bookmarks_tab")

        # add the tab if it was closed
        self.ui.plot_tab_area.addTab(self.app.book_dialog_tab, _("Bookmarks Manager"))

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")

        # hide coordinates toolbars in the infobar while in DB
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        # Switch plot_area to preferences page
        self.ui.plot_tab_area.setCurrentWidget(self.app.book_dialog_tab)

    def on_backup_site(self):
        """
        Called when the user click on the menu entry Help -> Bookmarks -> Backup Site

        :return:
        :rtype:
        """
        msgbox = FCMessageBox(parent=self.ui)
        title = _("Alternative website")
        txt = _("This entry will resolve to another website if:\n\n"
                "1. FlatCAM.org website is down\n"
                "2. Someone forked FlatCAM project and wants to point\n"
                "to his own website\n\n"
                "If you can't get any informations about the application\n"
                "use the YouTube channel link from the Help menu.")
        msgbox.setWindowTitle(title)  # taskbar still shows it
        msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
        msgbox.setText('<b>%s</b>\n\n' % title)
        msgbox.setInformativeText(txt)

        msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/globe16.png'))

        bt_yes = msgbox.addButton(_('Close'), QtWidgets.QMessageBox.ButtonRole.YesRole)

        msgbox.setDefaultButton(bt_yes)
        msgbox.exec()

    # --------------------------------------------------------------------------
    # Workspace
    # --------------------------------------------------------------------------

    def on_workspace_modified(self):
        # self.save_defaults(silent=True)

        self.app.plotcanvas.delete_workspace()
        self.app.preferencesUiManager.defaults_read_form()
        self.app.plotcanvas.draw_workspace(workspace_size=self.options.get('global_workspaceT', self._pref('global_workspaceT')))

    def on_workspace(self):
        if self.ui.general_pref_form.general_app_set_group.workspace_cb.get_value():
            self.app.plotcanvas.draw_workspace(workspace_size=self.options.get('global_workspaceT', self._pref('global_workspaceT')))
            self.inform[str, bool].emit(_("Workspace enabled."), False)
        else:
            self.app.plotcanvas.delete_workspace()
            self.inform[str, bool].emit(_("Workspace disabled."), False)
        self.app.preferencesUiManager.defaults_read_form()
        # self.save_defaults(silent=True)

    def on_workspace_toggle(self):
        state = False if self.ui.general_pref_form.general_app_set_group.workspace_cb.get_value() else True
        try:
            self.ui.general_pref_form.general_app_set_group.workspace_cb.stateChanged.disconnect(
                self.app.on_workspace
            )
        except TypeError:
            pass

        self.ui.general_pref_form.general_app_set_group.workspace_cb.set_value(state)
        self.ui.general_pref_form.general_app_set_group.workspace_cb.stateChanged.connect(self.app.on_workspace)
        self.app.on_workspace()

    # --------------------------------------------------------------------------
    # Log & Cursor
    # --------------------------------------------------------------------------

    def on_show_log(self):
        import os
        import sys
        import subprocess
        if sys.platform == 'win32':
            subprocess.Popen('explorer %s' % self.app.log_path())
        elif sys.platform == 'darwin':
            os.system('open "%s"' % self.app.log_path())
        else:
            subprocess.Popen(['xdg-open', self.app.log_path()])
        self.inform.emit('[success] %s' % _("FlatCAM log opened."))

    def on_cursor_type(self, val, control_cursor=True):
        """

        :param val:                 type of mouse cursor, set in Preferences ('small' or 'big')
        :param control_cursor:      if True, it is enabled only if the grid snap is active
        :return: None
        """
        self.app.app_cursor.enabled = False

        if val == 'small':
            self.ui.general_pref_form.general_app_set_group.cursor_size_entry.setDisabled(False)
            self.ui.general_pref_form.general_app_set_group.cursor_size_lbl.setDisabled(False)
            self.app.app_cursor = self.app.plotcanvas.new_cursor()
        else:
            self.ui.general_pref_form.general_app_set_group.cursor_size_entry.setDisabled(True)
            self.ui.general_pref_form.general_app_set_group.cursor_size_lbl.setDisabled(True)
            self.app.app_cursor = self.app.plotcanvas.new_cursor(big=True)

        if control_cursor is True:
            if self.ui.grid_snap_btn.isChecked():
                self.app.app_cursor.enabled = True
            else:
                self.app.app_cursor.enabled = False
        else:
            self.app.app_cursor.enabled = True

    # --------------------------------------------------------------------------
    # Keyboard Shortcuts
    # --------------------------------------------------------------------------

    def on_tool_add_keypress(self):
        # ## Current application units in Upper Case
        self.app.units = self.app.app_units.upper()

        current_widget = self.app.ui.notebook.currentWidget()
        if current_widget is None:
            return
        notebook_widget_name = current_widget.objectName()

        # work only if the notebook tab on focus is the properties_tab and only if the object is Geometry
        if notebook_widget_name == 'properties_tab':
            active_obj = self.app.collection.get_active()
            if active_obj is None:
                return
            if active_obj.kind == 'geometry':
                # Tool add works for Geometry only if Advanced is True in Preferences
                if self.options.get("global_app_level", self._pref("global_app_level")) == 'a':
                    tool_add_popup = FCInputSpinner(title='%s...' % _("New Tool"),
                                                    text='%s:' % _('Enter a Tool Diameter'),
                                                    min=0.0000, max=100.0000, decimals=self.app.decimals, step=0.1)
                    tool_add_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/letter_t_32.png'))
                    tool_add_popup.wdg.selectAll()

                    val, ok = tool_add_popup.get_value()
                    if ok:
                        if float(val) == 0:
                            self.inform.emit('[WARNING_NOTCL] %s' %
                                             _("Please enter a tool diameter with non-zero value, in Float format."))
                            return
                        try:
                            self.app.collection.get_active().on_tool_add(dia=float(val))
                        except Exception as tadd_err:
                            self.log.debug("App.on_tool_add_keypress() --> %s" % str(tadd_err))
                    else:
                        self.inform.emit('[WARNING_NOTCL] %s...' % _("Adding Tool cancelled"))
                else:
                    msgbox = FCMessageBox(parent=self.app.ui)
                    title = _("Tool adding ...")
                    txt = _("Adding Tool works only when Advanced is checked.\n"
                            "Go to Preferences -> General - Show Advanced Options.")
                    msgbox.setWindowTitle(title)  # taskbar still shows it
                    msgbox.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/app128.png'))
                    msgbox.setText('<b>%s</b>' % title)
                    msgbox.setInformativeText(txt)
                    msgbox.setIconPixmap(QtGui.QPixmap(self.app.resource_location + '/warning.png'))

                    bt_ok = msgbox.addButton(_('Ok'), QtWidgets.QMessageBox.ButtonRole.AcceptRole)

                    msgbox.setDefaultButton(bt_ok)
                    msgbox.exec()

        # work only if the notebook tab on focus is the Tools_Tab
        if notebook_widget_name == 'plugin_tab':
            try:
                tool_widget = self.app.ui.plugin_scroll_area.widget().objectName()
            except AttributeError:
                return

            # and only if the tool is NCC Tool
            if tool_widget == self.app.ncclear_tool.pluginName:
                self.app.ncclear_tool.on_add_tool_by_key()

            # and only if the tool is Paint Area Tool
            elif tool_widget == self.app.paint_tool.pluginName:
                self.app.paint_tool.on_add_tool_by_key()

            # and only if the tool is Solder Paste Dispensing Tool
            elif tool_widget == self.app.paste_tool.pluginName:
                self.app.paste_tool.on_add_tool_by_key()

            # and only if the tool is Isolation Tool
            elif tool_widget == self.app.isolation_tool.pluginName:
                self.app.isolation_tool.on_add_tool_by_key()

    # --------------------------------------------------------------------------
    # Preferences
    # --------------------------------------------------------------------------

    def on_toggle_preferences(self):
        pref_open = False
        for idx in range(self.ui.plot_tab_area.count()):
            if self.ui.plot_tab_area.tabText(idx) == _("Preferences"):
                pref_open = True

        if pref_open:
            for idx in range(self.ui.plot_tab_area.count()):
                if self.ui.plot_tab_area.tabText(idx) == _("Preferences"):
                    self.ui.plot_tab_area.removeTab(idx)
                    break

            self.log.debug("Preferences GUI was closed.")
            self.app.preferencesUiManager.clear_preferences_gui()
            self.ui.pref_status_label.setStyleSheet("")
        else:
            self.on_preferences()

    def on_preferences(self):
        """
        Adds the Preferences in a Tab in Plot Area

        :return:
        """

        self.app.preferencesUiManager.show_preferences_gui()

        # add the tab if it was closed
        self.ui.plot_tab_area.addTab(self.ui.preferences_tab, _("Preferences"))

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")
        # hide coordinates toolbars in the infobar while in DB
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        # Switch plot_area to preferences page
        self.ui.plot_tab_area.setCurrentWidget(self.ui.preferences_tab)
        # self.ui.show()

        self.ui.pref_status_label.setStyleSheet("""
                                                QLabel
                                                {
                                                    color: black;
                                                    background-color: lightseagreen;
                                                }
                                                """
                                                )

        # detect changes in the preferences
        for idx in range(self.ui.pref_tab_area.count()):
            for tb in self.ui.pref_tab_area.widget(idx).findChildren(QtWidgets.QWidget):
                try:
                    try:
                        tb.textEdited.disconnect(self.app.preferencesUiManager.on_preferences_edited)
                    except (TypeError, AttributeError):
                        pass
                    tb.textEdited.connect(self.app.preferencesUiManager.on_preferences_edited)
                except AttributeError:
                    pass

                try:
                    try:
                        tb.modificationChanged.disconnect(self.app.preferencesUiManager.on_preferences_edited)
                    except (TypeError, AttributeError):
                        pass
                    tb.modificationChanged.connect(self.app.preferencesUiManager.on_preferences_edited)
                except AttributeError:
                    pass

                try:
                    try:
                        tb.toggled.disconnect(self.app.preferencesUiManager.on_preferences_edited)
                    except (TypeError, AttributeError):
                        pass
                    tb.toggled.connect(self.app.preferencesUiManager.on_preferences_edited)
                except AttributeError:
                    pass

                try:
                    try:
                        tb.valueChanged.disconnect(self.app.preferencesUiManager.on_preferences_edited)
                    except (TypeError, AttributeError):
                        pass
                    tb.valueChanged.connect(self.app.preferencesUiManager.on_preferences_edited)
                except AttributeError:
                    pass

                try:
                    try:
                        tb.currentIndexChanged.disconnect(self.app.preferencesUiManager.on_preferences_edited)
                    except (TypeError, AttributeError):
                        pass
                    tb.currentIndexChanged.connect(self.app.preferencesUiManager.on_preferences_edited)
                except AttributeError:
                    pass

    # --------------------------------------------------------------------------
    # Tools Database
    # --------------------------------------------------------------------------

    def on_tools_database(self, source='app'):
        """
        Adds the Tools Database in a Tab in Plot Area.

        :return:
        """
        if source in ('app', 'milling'):
            callback = self.on_geometry_tool_add_from_db_executed
        elif source == 'ncc':
            callback = self.app.ncclear_tool.on_ncc_tool_add_from_db_executed
        elif source == 'paint':
            callback = self.app.paint_tool.on_paint_tool_add_from_db_executed
        elif source == 'iso':
            callback = self.app.isolation_tool.on_iso_tool_add_from_db_executed
        elif source == 'cutout':
            callback = self.app.cutout_tool.on_cutout_tool_add_from_db_executed
        else:
            self.log.error('Unknown Tools Database source: %s' % source)
            return 'fail'

        database, detached_window = _find_tools_database(self.ui)
        if database is not None:
            try:
                self.app.tools_db_tab = database
                database.on_tool_request = callback
                if detached_window is None:
                    self.ui.plot_tab_area.setCurrentWidget(database)
                else:
                    detached_window.show()
                    detached_window.raise_()
                    detached_window.activateWindow()
                database.set_context(source)
                if not getattr(database, '_db_load_valid', False):
                    return 'fail'
                return 'success'
            except RuntimeError:
                pass

        try:
            self.app.tools_db_tab = ToolsDB2(
                app=self.app,
                parent=self.ui if isinstance(self.ui, QtWidgets.QWidget) else None,
                callback_on_tool_request=callback
            )
        except Exception as error:
            self.log.error("App.on_tools_database() --> %s" % str(error))
            return 'fail'

        # add the tab if it was closed
        try:
            self.ui.plot_tab_area.addTab(self.app.tools_db_tab, _("Tools Database"))
            self.app.tools_db_tab.setObjectName("database_tab")
        except Exception as e:
            self.log.error("App.on_tools_database() --> %s" % str(e))
            return 'fail'

        # delete the absolute and relative position and messages in the infobar
        # self.ui.position_label.setText("")
        # self.ui.rel_position_label.setText("")

        # hide coordinates toolbars in the infobar while in DB
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        # Switch plot_area to preferences page
        self.ui.plot_tab_area.setCurrentWidget(self.app.tools_db_tab)

        # detect changes in the Tools in Tools DB, connect signals from table widget in tab
        self.app.tools_db_tab.ui_connect()
        self.app.tools_db_tab.set_context(source)
        if not self.app.tools_db_tab._db_load_valid:
            return 'fail'
        return 'success'

    # --------------------------------------------------------------------------
    # 3D Area
    # --------------------------------------------------------------------------

    def on_3d_area(self):
        from appGUI.PlotCanvas3d import PlotCanvas3d

        if getattr(self.app, 'plotcanvas3d', None) is not None:
            self.ui.plot_tab_area.setCurrentWidget(self.app.area_3d_tab)
            return

        # add the tab if it was closed
        try:
            self.ui.plot_tab_area.addTab(self.app.area_3d_tab, _("Plot 3D"))
            self.app.area_3d_tab.setObjectName("3D_area_tab")
        except Exception as e:
            self.log.error("App.on_3d_area() --> %s" % str(e))
            return

        plot_container_3d = QtWidgets.QVBoxLayout()
        # a gap on the right: without the G-code console the canvas touched the border of the window
        plot_container_3d.setContentsMargins(0, 0, 6, 0)
        self.app.area_3d_tab.setLayout(plot_container_3d)

        try:
            plotcanvas3d = PlotCanvas3d(plot_container_3d, self.app)
            self.app.plotcanvas3d = plotcanvas3d
        except Exception as er:
            msg_txt = traceback.format_exc()
            self.log.error("App.on_3d_area() failed -> %s" % str(er))
            self.log.error("OpenGL canvas initialization failed with the following error.\n" + msg_txt)
            msg = '[ERROR_NOTCL] %s' % _("An internal error has occurred. See shell.\n")
            msg += msg_txt
            self.inform.emit(msg)
            return 'fail'

        # So it can receive key presses
        plotcanvas3d.native.setFocus()

        pan_button = 2 if self.options.get("global_pan_button", self._pref("global_pan_button")) == '2' else 3
        # Set the mouse button for panning
        plotcanvas3d.view.camera.pan_button_setting = pan_button

        # self.mm = plotcanvas3D.graph_event_connect('mouse_move', self.on_mouse_move_over_plot)
        # self.mp = plotcanvas3D.graph_event_connect('mouse_press', self.on_mouse_click_over_plot)
        # self.mr = plotcanvas3D.graph_event_connect('mouse_release', self.on_mouse_click_release_over_plot)
        # self.mdc = plotcanvas3D.graph_event_connect('mouse_double_click', self.on_mouse_double_click_over_plot)

        # Keys over plot enabled
        # self.kp = plotcanvas3D.graph_event_connect('key_press', self.ui.keyPressEvent)

        # hide coordinates toolbars in the infobar
        self.ui.coords_toolbar.hide()
        self.ui.delta_coords_toolbar.hide()

        # Switch plot_area to Area 3D page
        self.ui.plot_tab_area.setCurrentWidget(self.app.area_3d_tab)

    # --------------------------------------------------------------------------
    # Geometry Tool from DB
    # --------------------------------------------------------------------------

    def on_geometry_tool_add_from_db_executed(self, tool):
        """
        Here add the tool from DB  in the selected geometry object.

        :return:
        """
        tool_from_db = deepcopy(tool)
        obj = self.app.collection.get_active()

        if obj is None:
            self.inform.emit('[ERROR_NOTCL] %s' % _("No object is selected."))
            return 'fail'

        if obj.kind == 'geometry':
            if tool['data']['tool_target'] not in [0, 1]:  # General, Milling Type
                self.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
                return 'fail'

            result = self.app.milling_tool.on_tool_from_db_inserted(tool=tool_from_db)
        elif obj.kind == 'gerber':
            if tool['data']['tool_target'] not in [0, 3]:  # General, Isolation Type
                self.inform.emit('[ERROR_NOTCL] %s' % _("Selected tool can't be used here. Pick another."))
                return 'fail'
            result = self.app.isolation_tool.on_tool_from_db_inserted(tool=tool_from_db)
        else:
            self.inform.emit('[ERROR_NOTCL] %s' % _("Adding tool from DB is not allowed for this object."))
            return 'fail'

        if result in ('fail', False):
            return 'fail'
        self.inform.emit('[success] %s' % _("Tool from DB added in Tool Table."))
        return True

    # --------------------------------------------------------------------------
    # Plot Area Tab Management
    # --------------------------------------------------------------------------

    def on_plot_area_tab_closed(self, tab_obj_name, index=None):
        """
        Executed whenever a QTab is closed in the Plot Area.

        :param tab_obj_name: The objectName of the Tab that was closed. This objectName is assigned on Tab creation
        :param index:        The index of the closed Tab; the Tab is still in the Plot Area when this runs
        :return:
        """

        # the widget of the closed tab, taken now: the exit of the G-code Editor below closes its tab itself
        closed_widget = self.ui.plot_tab_area.widget(index) if index is not None else None
        if closed_widget is not None and closed_widget.objectName() != tab_obj_name:
            closed_widget = None

        if tab_obj_name == "preferences_tab":
            self.app.preferencesUiManager.on_close_preferences_tab(parent=self.ui)
        elif tab_obj_name == "database_tab":
            self.app.tools_db_tab.ui_disconnect()
            self.app.tools_db_changed_flag = False
            self.app.tools_db_tab.deleteLater()
        elif tab_obj_name == "text_editor_tab":
            self.app.toggle_codeeditor = False
        elif tab_obj_name == "bookmarks_tab":
            self.app.book_dialog_tab.rebuild_actions()
            self.app.book_dialog_tab.deleteLater()
        elif tab_obj_name == "3D_area_tab":
            self.app.plotcanvas3d = None
            self.app.area_3d_tab.deleteLater()
            self.app.area_3d_tab = QtWidgets.QWidget()
        elif tab_obj_name == "gcode_editor_tab":
            self.app.on_editing_finished()
            # the G-code Editor and the Code Review make a new tab each time and removeTab() does not delete the
            # widget, which holds the whole G-code: delete it (not when the editor did not exit)
            if closed_widget is not None and self.app.call_source != 'gcode_editor':
                closed_widget.deleteLater()
        elif tab_obj_name == "code_editor_tab":
            if closed_widget is not None:
                closed_widget.deleteLater()
        else:
            pass

        # restore the coords toolbars
        self.ui.toggle_coords(checked=self.options.get("global_coords_bar_show", self._pref("global_coords_bar_show")))
        self.ui.toggle_delta_coords(checked=self.options.get("global_delta_coords_bar_show", self._pref("global_delta_coords_bar_show")))

    def on_plot_area_tab_double_clicked(self):
        # tab_obj_name = self.ui.plot_tab_area.widget(index).objectName()
        # print(tab_obj_name)
        self.ui.on_toggle_notebook()

    # --------------------------------------------------------------------------
    # Notebook (plugin area) Management
    # --------------------------------------------------------------------------

    def on_notebook_closed(self):

        # closed_plugin_name = self.ui.plugin_scroll_area.widget().objectName()
        # # print(closed_plugin_name)
        # if closed_plugin_name == _("Levelling"):
        #     # clear the possible drawn probing shapes
        #     self.levelling_tool.probing_shapes.clear(update=True)
        # elif closed_plugin_name in [_("Isolation"), _("NCC"), _("Paint"), _("Punch Gerber")]:
        #     self.tool_shapes.clear(update=True)

        # disconnected_tool = self.ui.plugin_scroll_area.widget()

        # try:
        #     # if the closed plugin name is Milling
        #     disconnected_tool.disconnect_signals()
        #     disconnected_tool.ui_disconnect()
        #     disconnected_tool.clear_ui(disconnected_tool.layout)
        #
        # except Exception as err:
        #     print(str(err))

        try:
            # this signal is used by the Plugins to change the selection on App objects combo boxes when the
            # selection happen in Project Tab (collection view)
            # when the plugin is closed then it's not needed
            self.app.proj_selection_changed.disconnect()    # noqa
        except (TypeError, AttributeError):
            pass

        try:
            # clear the possible drawn probing shapes for Levelling Tool
            self.app.levelling_tool.probing_shapes.clear(update=True)
        except AttributeError:
            pass

        try:
            # clear the possible drawn probing shapes for GRBL Sender Plugin
            self.app.grbl_sender_tool.probing_shapes.clear(update=True)
        except AttributeError:
            pass

        try:
            # clean possible tool shapes for Isolation, NCC, Paint, Punch Gerber Plugins
            if self.app.tool_shapes is not None:
                self.app.tool_shapes.clear(update=True)
        except AttributeError:
            pass

        # clean the Tools Tab
        found_idx = None
        for idx in range(self.ui.notebook.count()):
            if self.ui.notebook.widget(idx).objectName() == "plugin_tab":
                found_idx = idx
                break
        if found_idx is not None:
            # #########################################################################################################
            # first do the Plugin cleanup
            # #########################################################################################################
            for plugin in self.app.app_plugins:
                try:
                    # execute this only for the current active plugin
                    if self.ui.notebook.tabText(found_idx) != plugin.pluginName:
                        continue
                    plugin.on_plugin_cleanup()
                except AttributeError:
                    # not all plugins have this implemented
                    # print("This does not have it", self.ui.notebook.tabText(tab_idx))
                    pass
            self.ui.notebook.setCurrentWidget(self.ui.properties_tab)
            self.ui.notebook.removeTab(found_idx)

        # HACK: the content was removed but let's create it again
        self.ui.plugin_tab = QtWidgets.QWidget()
        self.ui.plugin_tab.setObjectName("plugin_tab")
        self.ui.plugin_tab_layout = QtWidgets.QVBoxLayout(self.ui.plugin_tab)
        self.ui.plugin_tab_layout.setContentsMargins(2, 2, 2, 2)
        # self.notebook.addTab(self.plugin_tab, _("Tool"))

        self.ui.plugin_scroll_area = VerticalScrollArea()
        # self.plugin_scroll_area.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        self.ui.plugin_tab_layout.addWidget(self.ui.plugin_scroll_area)

    # --------------------------------------------------------------------------
    # Properties Tab
    # --------------------------------------------------------------------------

    def on_properties_tab_click(self):
        tab_wdg = self.ui.properties_scroll_area.widget()
        if tab_wdg and tab_wdg.objectName() == 'default_properties':
            self.app.setup_default_properties_tab()

    def on_notebook_tab_changed(self):
        """
        Slot for current tab changed in self.ui.notebook

        :return:
        """
        if self.ui.notebook.tabText(self.ui.notebook.currentIndex()) == _("Properties"):
            active_obj = self.app.collection.get_active()
            if active_obj:
                try:
                    active_obj.build_ui()
                except RuntimeError:
                    active_obj.set_ui(active_obj.ui_type(app=self.app))
                    active_obj.build_ui()
                except Exception:
                    self.app.setup_default_properties_tab()
                    return

                # Safety net: verify widget was actually placed
                if self.ui.properties_scroll_area.widget() is not active_obj.ui:
                    try:
                        active_obj.set_ui(active_obj.ui_type(app=self.app))
                        active_obj.build_ui()
                    except Exception:
                        self.app.setup_default_properties_tab()
            else:
                self.app.setup_default_properties_tab()

    def setup_default_properties_tab(self):
        """
        Default text for the Properties tab when is not taken by the Object UI.

        :return:
        """
        from appGUI.GUIElements import FCTree

        # Tree Widget
        d_properties_tw = FCTree(columns=2)
        d_properties_tw.setObjectName("default_properties")
        d_properties_tw.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        d_properties_tw.setStyleSheet("QTreeWidget {border: 0px;}")

        root = d_properties_tw.invisibleRootItem()
        font = QtGui.QFont()
        font.setBold(True)
        p_color = QtGui.QColor("#000000") if self.options.get('global_theme', self._pref('global_theme')) in ['default', 'light'] else \
            QtGui.QColor("#FFFFFF")

        # main Items categories
        general_cat = d_properties_tw.addParent(root, _('General'), expanded=True, color=p_color, font=font)
        d_properties_tw.addChild(parent=general_cat,
                                 title=['%s:' % _("Name"), '%s' % _("FlatCAM Evo")], column1=True)
        d_properties_tw.addChild(parent=general_cat,
                                 title=['%s:' % _("Version"), '%s' % str(self.app.version)], column1=True)
        d_properties_tw.addChild(parent=general_cat,
                                 title=['%s:' % _("Release date"), '%s' % str(self.app.version_date)], column1=True)

        grid_cat = d_properties_tw.addParent(root, _('Grid'), expanded=True, color=p_color, font=font)
        d_properties_tw.addChild(parent=grid_cat,
                                 title=['%s:' % _("Displayed"), '%s' % str(self.options.get('global_grid_lines', self._pref('global_grid_lines')))],
                                 column1=True)
        d_properties_tw.addChild(parent=grid_cat,
                                 title=['%s:' % _("Snap"), '%s' % str(self.options.get('global_grid_snap', self._pref('global_grid_snap')))],
                                 column1=True)
        d_properties_tw.addChild(parent=grid_cat,
                                 title=['%s:' % _("X value"), '%s' % str(self.ui.grid_gap_x_entry.get_value())],
                                 column1=True)
        d_properties_tw.addChild(parent=grid_cat,
                                 title=['%s:' % _("Y value"), '%s' % str(self.ui.grid_gap_y_entry.get_value())],
                                 column1=True)

        canvas_cat = d_properties_tw.addParent(root, _('Canvas'), expanded=True, color=p_color, font=font)
        d_properties_tw.addChild(parent=canvas_cat,
                                 title=['%s:' % _("Axis"), '%s' % str(self.options.get('global_axis', self._pref('global_axis')))],
                                 column1=True)
        d_properties_tw.addChild(parent=canvas_cat,
                                 title=['%s:' % _("Workspace active"),
                                        '%s' % str(self.options.get('global_workspace', self._pref('global_workspace')))],
                                 column1=True)
        d_properties_tw.addChild(parent=canvas_cat,
                                 title=['%s:' % _("Workspace size"),
                                        '%s' % str(self.options.get('global_workspaceT', self._pref('global_workspaceT')))],
                                 column1=True)
        d_properties_tw.addChild(parent=canvas_cat,
                                 title=['%s:' % _("Workspace orientation"),
                                         '%s' % _("Portrait") if self.options.get(
                                                                    'global_workspace_orientation', self._pref('global_workspace_orientation')) == 'p' else
                                        _("Landscape")],
                                 column1=True)
        d_properties_tw.addChild(parent=canvas_cat,
                                 title=['%s:' % _("HUD"), '%s' % str(self.options.get('global_hud', self._pref('global_hud')))],
                                 column1=True)
        # take the current widget out first: QScrollArea.setWidget() deletes the old widget and that can be the UI
        # of an object, which the object still uses
        self.ui.properties_scroll_area.takeWidget()
        self.ui.properties_scroll_area.setWidget(d_properties_tw)

    # --------------------------------------------------------------------------
    # Grid
    # --------------------------------------------------------------------------

    def grid_status(self):
        return True if self.ui.grid_snap_btn.isChecked() else False

    def populate_cmenu_grids(self):
        units = self.app.app_units.lower()

        # for act in self.ui.cmenu_gridmenu.actions():
        #     act.triggered.disconnect()
        self.ui.cmenu_gridmenu.clear()

        grid_context_menu = self._ensure_option("global_grid_context_menu")
        factory_grid_context_menu = self._pref("global_grid_context_menu") or {}
        sorted_list = sorted(grid_context_menu.get(
            str(units), factory_grid_context_menu.get(str(units), [])
        ))

        grid_toggle = self.ui.cmenu_gridmenu.addAction(QtGui.QIcon(self.app.resource_location + '/grid32_menu.png'),
                                                       _("Grid On/Off"))
        grid_toggle.setCheckable(True)
        grid_toggle.setChecked(True) if self.grid_status() else grid_toggle.setChecked(False)

        self.ui.cmenu_gridmenu.addSeparator()
        for grid in sorted_list:
            action = self.ui.cmenu_gridmenu.addAction(QtGui.QIcon(self.app.resource_location + '/grid32_menu.png'),
                                                      "%s" % str(grid))
            action.triggered.connect(self.set_grid)

        self.ui.cmenu_gridmenu.addSeparator()
        grid_add = self.ui.cmenu_gridmenu.addAction(QtGui.QIcon(self.app.resource_location + '/plus32.png'),
                                                    _("Add"))
        grid_delete = self.ui.cmenu_gridmenu.addAction(QtGui.QIcon(self.app.resource_location + '/delete32.png'),
                                                       _("Delete"))
        grid_add.triggered.connect(self.on_grid_add)
        grid_delete.triggered.connect(self.on_grid_delete)
        grid_toggle.triggered.connect(lambda: self.ui.grid_snap_btn.trigger())

    def set_grid(self):
        menu_action = self.app.sender()
        assert isinstance(menu_action, QtGui.QAction), "Expected QAction got %s" % type(menu_action)

        self.ui.grid_gap_x_entry.setText(menu_action.text())
        self.ui.grid_gap_y_entry.setText(menu_action.text())

    def on_grid_add(self):
        # ## Current application units in lower Case
        units = self.app.app_units.lower()

        grid_add_popup = FCInputDoubleSpinner(title=_("New Grid ..."),
                                              text=_('Enter a Grid Value:'),
                                              min=0.0000, max=99.9999, decimals=self.app.decimals,
                                              parent=self.ui)
        grid_add_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/plus32.png'))

        val, ok = grid_add_popup.get_value()
        if ok:
            if float(val) == 0:
                self.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Please enter a grid value with non-zero value, in Float format."))
                return
            else:
                grid_context_menu = self._ensure_option("global_grid_context_menu")
                factory_grid_context_menu = self._pref("global_grid_context_menu") or {}
                grid_values = grid_context_menu.setdefault(
                    str(units), deepcopy(factory_grid_context_menu.get(str(units), []))
                )
                if val not in grid_values:
                    grid_values.append(val)
                    self.inform.emit('[success] %s...' % _("New Grid added"))
                else:
                    self.inform.emit('[WARNING_NOTCL] %s...' % _("Grid already exists"))
        else:
            self.inform.emit('[WARNING_NOTCL] %s...' % _("Adding New Grid cancelled"))

    def on_grid_delete(self):
        # ## Current application units in lower Case
        units = self.app.app_units.lower()

        grid_del_popup = FCInputDoubleSpinner(title="Delete Grid ...",
                                              text='Enter a Grid Value:',
                                              min=0.0000, max=99.9999, decimals=self.app.decimals,
                                              parent=self.ui)
        grid_del_popup.setWindowIcon(QtGui.QIcon(self.app.resource_location + '/delete32.png'))

        val, ok = grid_del_popup.get_value()
        if ok:
            if float(val) == 0:
                self.inform.emit('[WARNING_NOTCL] %s' %
                                 _("Please enter a grid value with non-zero value, in Float format."))
                return
            else:
                grid_context_menu = self._ensure_option("global_grid_context_menu")
                grid_values = grid_context_menu.get(str(units))
                if grid_values is None:
                    self.inform.emit('[ERROR_NOTCL]%s...' % _("Grid Value does not exist"))
                    return
                try:
                    grid_values.remove(val)
                except ValueError:
                    self.inform.emit('[ERROR_NOTCL]%s...' % _("Grid Value does not exist"))
                    return
                self.inform.emit('[success] %s...' % _("Grid Value deleted"))
        else:
            self.inform.emit('[WARNING_NOTCL] %s...' % _("Delete Grid value cancelled"))
