"""The Sender detects the E-Cut adapter by its own banner; no board is needed."""
from appPlugins.Common.grbl.grbl_client import (
    ECUT_BANNER, is_controller_banner, is_grblhal_banner)


def test_both_controller_banners_are_detected():
    assert is_controller_banner("Grbl 1.1h ['$' for help]")
    assert is_controller_banner("GrblHAL 1.1f ['$' or '$HELP' for help]")
    assert is_controller_banner(ECUT_BANNER)
    assert is_controller_banner('  ' + ECUT_BANNER.upper() + '\r\n')


def test_grblhal_is_told_from_legacy_grbl():
    # grblHAL prints its own name at COMPATIBILITY_LEVEL 0; a grblHAL build at a higher
    # compatibility level prints the plain GRBL banner and cannot be told apart
    assert is_grblhal_banner("GrblHAL 1.1f ['$' or '$HELP' for help]")
    assert not is_grblhal_banner("Grbl 1.1h ['$' for help]")
    assert not is_grblhal_banner(ECUT_BANNER)


def test_other_lines_are_not_banners():
    for line in ('ok', 'error:20', 'ALARM:1', '[MSG:E-Cut: refused]', '', None, 'E-Cut host adapter'):
        assert not is_controller_banner(line)
