# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/24/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################
"""
Machine Profiles: the gate of the file writers (stage 1: check only).

A writer calls `gate_file_text` with the text it is about to write. With the profile "None" nothing runs. No Qt,
no app import.
"""

import builtins
import gettext
import re
from typing import NamedTuple, Optional

from appCommon.machine_profile.correct import CorrectionRefused
from appCommon.machine_profile.output import correction_matrix, has_reject, prepare_output, setup_from_profile
from appCommon.machine_profile.profile import RESERVED_NAME, active_profile, resolve_profiles_folder
from appCommon.machine_profile.records import CheckResult, CheckStatus, Purpose, ViolationKind

if '_' not in builtins.__dict__:
    _ = gettext.gettext

_MISSING_WARNED = set()


class OutputRejected(Exception):
    """The profile rejects the file: the writer must not write it."""


class FileGate(NamedTuple):
    ok: bool = True
    error: Optional[str] = None     # the message of a rejected file
    note: Optional[str] = None      # a status bar line for a written file (warnings, partial check, missing profile)
    result: Optional[CheckResult] = None    # the full check; None with profile None or a missing profile
    text: Optional[str] = None      # the corrected text to write; None: write the text as it is
    ask_source: bool = False        # the source has unknown provenance and was not declared: a GUI may ask the user
    source_note: Optional[str] = None   # why the text was not corrected (unknown provenance, declared as not to)


def _units_word(units: str) -> str:
    return 'inch' if str(units).upper() in ('IN', 'INCH') else 'mm'


def _join(*notes) -> Optional[str]:
    notes = [n for n in notes if n]
    return '. '.join(notes) if notes else None


def calibration_file_text(name: str, configured_folder: str, data_path: str, text: str, dialect: Optional[str],
                          units: str, decimals: int = 4) -> FileGate:
    """
    The gate of the calibration pattern (Purpose.CALIBRATION): the text is checked but never corrected, not by the
    matrix of the profile (which is the one under test) and not by a candidate. The text to write (`FileGate.text`)
    has the first comment line `(Machine profile: <name>, uncorrected (calibration))`. With the profile "None" or a
    missing profile nothing is added (`text` is None).
    """
    if not name or name == RESERVED_NAME:
        return FileGate()
    profile = active_profile(name, resolve_profiles_folder(configured_folder or '', data_path))
    if profile is None:
        return FileGate(note=_('Machine profile "%s" not found: the output is not checked') % name)
    out, result = prepare_output(text, Purpose.CALIBRATION, setup_from_profile(profile, dialect, _units_word(units)),
                                 profile, decimals)
    if has_reject(result):
        first = next(v for v in result.violations if v.severity == 'reject')
        return FileGate(False, _('Machine profile "%s": file not written. Line %d: %s') % (
            name, first.line, first.message), result=result)
    label = re.sub(r'[()\r\n;]', ' ', profile.name)
    header = '(Machine profile: %s, uncorrected (calibration))\n' % label
    note = None
    if result.status is CheckStatus.VIOLATIONS:
        note = _('Machine profile: %d warnings') % sum(1 for v in result.violations
                                                       if v.kind is not ViolationKind.UNVERIFIED)
    elif result.status is CheckStatus.PARTIAL:
        note = _('Machine profile: partially checked')
    return FileGate(note=note, result=result, text=header + out)


def gate_file_text(name: str, configured_folder: str, data_path: str, text: str, dialect: Optional[str],
                   units: str, correct: bool = False, provenance: Optional[dict] = None,
                   uncorrected_source: Optional[bool] = None, decimals: int = 4) -> FileGate:
    """
    Check the text of a file output against the active profile `name` (the app option).

    A writer that passes `correct=True` also gets the correction of the profile (when its calibration is on): the
    text to write is `FileGate.text`. `provenance` is the `machine_profile` record of the job (None: unknown
    provenance, which is corrected only when `uncorrected_source` is True).
    """
    if not name or name == RESERVED_NAME:
        return FileGate()
    profile = active_profile(name, resolve_profiles_folder(configured_folder or '', data_path))
    if profile is None:
        if name in _MISSING_WARNED:
            return FileGate()
        _MISSING_WARNED.add(name)
        return FileGate(note=_('Machine profile "%s" not found: the output is not checked') % name)
    apply = False
    source_note = None
    ask = False
    if correct and correction_matrix(profile) is not None:
        if provenance:
            if provenance.get('bed_legacy'):
                return FileGate(False, _('Machine profile "%s": file not written. The job holds the old bed '
                                         'compensation, so the correction would apply twice: regenerate the job.')
                                % name)
            apply = True
        elif uncorrected_source is True:
            apply = True
        elif uncorrected_source is False:
            source_note = _('not corrected: declared as not to correct')
        else:
            source_note = _('not corrected: the source has unknown provenance')
            ask = True
    try:
        out, result = prepare_output(text, Purpose.FILE, setup_from_profile(profile, dialect, _units_word(units)),
                                     profile, decimals, correct=apply)
    except CorrectionRefused as refused:
        return FileGate(False, _('Machine profile "%s": file not written. Correction refused at line %d, %s: %s') % (
            name, refused.line, refused.word, refused.reason))
    new_text = out if apply else None
    if has_reject(result):
        first = next(v for v in result.violations if v.severity == 'reject')
        return FileGate(False, _('Machine profile "%s": file not written. Line %d: %s') % (
            name, first.line, first.message), result=result)
    if result.status is CheckStatus.VIOLATIONS:
        count = sum(1 for v in result.violations if v.kind is not ViolationKind.UNVERIFIED)
        return FileGate(note=_join(_('Machine profile: %d warnings') % count, source_note), result=result,
                        text=new_text, ask_source=ask, source_note=source_note)
    if result.status is CheckStatus.PARTIAL:
        return FileGate(note=_join(_('Machine profile: partially checked'), source_note), result=result,
                        text=new_text, ask_source=ask, source_note=source_note)
    return FileGate(note=source_note, result=result, text=new_text, ask_source=ask, source_note=source_note)
