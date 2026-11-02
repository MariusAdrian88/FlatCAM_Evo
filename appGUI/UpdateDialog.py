"""Non-blocking update offer dialog."""

from __future__ import annotations

import gettext
import html

from PyQt6 import QtCore, QtGui, QtWidgets


_ = gettext.gettext

_MANUAL_REASONS = {
    "appimage": _("This FlatCAM copy runs from an AppImage."),
    "windows-source": _("This is a source installation on Windows."),
    "unsupported-architecture": _("This build does not match the published Windows x64 archive."),
    "frozen-linux": _("The packaged Linux application is replaced by hand."),
    "git-checkout": _("This is a Git checkout."),
    "read-only": _("The installation folder is read-only."),
    "macos": _("macOS builds are replaced by hand."),
    "unsupported-platform": _("This platform has no automatic package."),
}


def _human_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(number) < 1024:
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


def _human_date(value: str) -> str:
    """Show a release date for people: '2026-11-02T22:11:02Z' becomes the local date and time in the format of the
    locale, for example '2 November 2026, 23:11'. A date with no time (2026/11/02, 2026-11-02) shows only the date.
    A value that is not a date stays as it is."""
    text = value.strip()
    locale = QtCore.QLocale()
    moment = QtCore.QDateTime.fromString(text, QtCore.Qt.DateFormat.ISODate)
    if moment.isValid() and "T" in text:
        moment = moment.toLocalTime()
        return "%s, %s" % (locale.toString(moment.date(), QtCore.QLocale.FormatType.LongFormat),
                           locale.toString(moment.time(), QtCore.QLocale.FormatType.ShortFormat))
    day = QtCore.QDate.fromString(text.replace("/", "-")[:10], QtCore.Qt.DateFormat.ISODate)
    if day.isValid():
        return locale.toString(day, QtCore.QLocale.FormatType.LongFormat)
    return value


# GitHub Markdown, but raw HTML in the notes stays text: the notes come from the network
_NOTES_MARKDOWN = (QtGui.QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                   | QtGui.QTextDocument.MarkdownFeature.MarkdownNoHTML)


def _open_web_link(url: QtCore.QUrl) -> None:
    if url.scheme() in ("http", "https"):
        QtGui.QDesktopServices.openUrl(url)


def _release_details(payload: dict) -> dict:
    """Read release details from the pre-staging checker payload."""
    manifest = payload.get("manifest")
    version = build = notes = published = ""
    size = None
    if isinstance(manifest, dict):
        archive = manifest.get("archive")
        if not isinstance(archive, dict):
            archive = {}
        notes = payload.get("release_notes", manifest.get("release_notes"))
        version = payload.get("version") or manifest.get("version") or payload.get("tag") or ""
        build = payload.get("build_string") or manifest.get("build") or manifest.get("build_string") or ""
        published = payload.get("published_at") or manifest.get("version_date") or ""
        size = archive.get("size")
    elif manifest is not None:
        archive = getattr(manifest, "archive", None)
        notes = getattr(manifest, "release_notes", "")
        version = getattr(manifest, "version", "")
        build = getattr(manifest, "build_string", "")
        published = getattr(manifest, "version_date", "")
        size = getattr(archive, "size", None)
    else:
        notes = payload.get("release_notes")
        version = payload.get("version") or payload.get("tag") or ""
        build = payload.get("build_string") or ""
        published = payload.get("published_at") or ""
    asset = payload.get("asset")
    if payload.get("install") == "windows_asset" and isinstance(asset, dict) and not size:
        size = asset.get("size")
    return {
        "version": str(version or ""),
        "build": str(build or ""),
        "notes": str(notes or ""),
        "published": str(published or ""),
        "size": size if type(size) is int and size > 0 else None,
        "release_page": str(payload.get("release_page") or ""),
    }


class UpdateDialog(QtWidgets.QDialog):
    """Display release details without blocking the application event loop."""

    def __init__(self, parent, payload: dict) -> None:
        super().__init__(parent)
        details = _release_details(payload)
        current = payload.get("current", {})
        self._mandatory = bool(payload.get("mandatory", False))
        self._manual = bool(payload.get("manual", False))
        self.choice = "later"

        self.setWindowTitle(_("FlatCAM update available: %s") % (details["version"] or _("unknown version")))
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        current_text = "%s (%s)" % (current.get("version", "unknown"), current.get("build", ""))
        available_text = "%s (%s)" % (details["version"] or _("unknown"), details["build"])
        header = QtWidgets.QLabel(
            _("A new version is available: <b>%s</b><br>You have: <b>%s</b>")
            % (html.escape(available_text), html.escape(current_text))
        )
        header.setWordWrap(True)
        header.setAccessibleName(_("Update version information"))
        layout.addWidget(header)

        if details["size"] is None:
            size_text = _("Update size: unknown; the source archive is prepared automatically.")
        else:
            size_text = _("Full archive size: %s") % _human_bytes(details["size"])
        archive_size = QtWidgets.QLabel(size_text)
        archive_size.setAccessibleName(_("Update archive size"))
        layout.addWidget(archive_size)

        if details["published"]:
            published = QtWidgets.QLabel(_("Published: %s") % _human_date(details["published"]))
            published.setAccessibleName(_("Release date"))
            layout.addWidget(published)

        notes = QtWidgets.QTextBrowser()
        notes.setAccessibleName(_("Release notes"))
        # a click on a link opens the web browser; the notes never load another page in place
        notes.setOpenLinks(False)
        notes.anchorClicked.connect(_open_web_link)
        notes.document().setMarkdown(details["notes"] or _("No release notes provided."), _NOTES_MARKDOWN)
        notes.moveCursor(QtGui.QTextCursor.MoveOperation.Start)  # the notes start at the top, not at the end
        self.setMinimumHeight(480)
        layout.addWidget(notes)

        if self._manual:
            reason = _MANUAL_REASONS.get(
                payload.get("manual_reason", ""),
                _("Automatic replacement is not available for this installation."),
            )
            manual_label = QtWidgets.QLabel(
                _("This update cannot be installed automatically. %s "
                  "Download the release from its page and replace this installation manually.")
                % reason
            )
            manual_label.setWordWrap(True)
            manual_label.setAccessibleName(_("Manual installation guidance"))
            layout.addWidget(manual_label)
            if details["release_page"].startswith(("https://", "http://")):
                link = QtWidgets.QLabel(
                    '<a href="%s">%s</a>'
                    % (html.escape(details["release_page"], quote=True), html.escape(details["release_page"]))
                )
                link.setOpenExternalLinks(True)
                link.setAccessibleName(_("Open the release page"))
                layout.addWidget(link)

        if self._mandatory:
            warning = QtWidgets.QLabel(
                _("This update is mandatory. Update now or exit FlatCAM.")
            )
            warning.setWordWrap(True)
            warning.setAccessibleName(_("Mandatory update warning"))
            layout.addWidget(warning)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.update_button = None
        if not self._manual:
            self.update_button = QtWidgets.QPushButton(_("Update now"))
            self.update_button.setDefault(True)
            self.update_button.setAccessibleName(_("Update now"))
            self.update_button.clicked.connect(self._on_update)
            buttons.addWidget(self.update_button)
        self.other_button = QtWidgets.QPushButton(
            _("Exit") if self._mandatory else _("Later")
        )
        if self._manual:
            self.other_button.setDefault(True)
        self.other_button.setAccessibleName(self.other_button.text())
        self.other_button.clicked.connect(self._on_other)
        buttons.addWidget(self.other_button)
        layout.addLayout(buttons)

    def _on_update(self) -> None:
        self.choice = "update"
        self.accept()

    def _on_other(self) -> None:
        self.choice = "exit" if self._mandatory else "later"
        self.reject()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.choice = "exit" if self._mandatory else "later"
        super().closeEvent(event)
