"""Deterministic managed-file archive helpers shared by release providers."""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import ManifestError, validate_relative_path


_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "cache",
        "dist",
        "docs",
        "doc",
        "freeze",
        "settings",
        "tests",
        "user_data",
        "user_config",
        "user-settings",
        "user_settings",
        "userdata",
        "venv",
    }
)
_EXCLUDED_FILES = frozenset(
    {
        "build.py",
        "build_freeze.py",
        "freeze.py",
        "make_freeze.py",
        "make_freezed.py",
        "setup_freeze.py",
    }
)
_EXCLUDED_SUFFIXES = (".flatconfig", ".flatprj")
# updater/ is managed: the launcher runs the updater from a copy, so an update can replace it
_PROTECTED_ROOT_NAMES = frozenset({"config"})


@dataclass(frozen=True)
class ExclusionPolicy:
    """Explicit file and directory policy for managed release payloads."""

    excluded_names: frozenset[str] = _EXCLUDED_NAMES
    excluded_files: frozenset[str] = _EXCLUDED_FILES
    excluded_suffixes: tuple[str, ...] = _EXCLUDED_SUFFIXES

    def excludes(self, relative_path: str) -> bool:
        path = validate_relative_path(relative_path)
        parts = path.lower().split("/")
        if parts[0] in _PROTECTED_ROOT_NAMES:
            return True
        if any(part in self.excluded_names for part in parts[:-1]):
            return True
        filename = parts[-1]
        if filename in self.excluded_names or filename in self.excluded_files:
            return True
        return filename.endswith(self.excluded_suffixes)


DEFAULT_EXCLUSION_POLICY = ExclusionPolicy()


def managed_files(root: Path, policy=DEFAULT_EXCLUSION_POLICY) -> tuple[tuple[str, Path], ...]:
    """Return sorted payload files after applying the explicit exclusion policy."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ManifestError(f"Release root {str(root)!r} is not a directory.")
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(root).as_posix()
        if not policy.excludes(relative_path):
            result.append((relative_path, path))
    return tuple(result)


def build_deterministic_zip(root: Path, destination: Path, policy=DEFAULT_EXCLUSION_POLICY) -> Path:
    """Write a sorted, timestamp-independent ZIP archive and replace atomically."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for relative_path, source in managed_files(root, policy):
                info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (source.stat().st_mode & 0o777)) << 16
                with source.open("rb") as input_stream, archive.open(info, "w") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination
