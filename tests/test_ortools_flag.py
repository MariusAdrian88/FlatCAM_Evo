"""The OR-Tools flag in camlib: it is True only on a 64-bit Python where the import worked."""
import builtins
import sys

import pytest

import camlib

# camlib needs the gettext builtin; the other camlib tests get it from their own setup
builtins.__dict__.setdefault('_', lambda s: s)


def test_64bit_without_ortools_is_false(monkeypatch):
    # a None entry in sys.modules makes "import ortools..." raise ImportError
    monkeypatch.setitem(sys.modules, 'ortools', None)
    monkeypatch.setitem(sys.modules, 'ortools.constraint_solver', None)
    assert camlib._ortools_available('64bit') is False


