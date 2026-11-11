"""Reject an AppDir whose ELF binaries require a newer glibc than the Linux baseline."""

import re
import subprocess
import sys
from pathlib import Path


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve(strict=True)
    ceiling: tuple[int, int] = (2, 28)
    highest = (0, 0)
    checked = 0
    failures = []
    for binary in root.rglob('*'):
        if not binary.is_file():
            continue
        with binary.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        result = subprocess.run(['readelf', '--version-info', str(binary)],
                                check=True, capture_output=True, text=True)
        versions = [(int(major), int(minor)) for major, minor in
                    re.findall(r'\bGLIBC_(\d+)\.(\d+)\b', result.stdout)]
        required = max(versions, default=(0, 0))
        highest = max(highest, required)
        checked += 1
        if required > ceiling:
            failures.append(f'{binary.relative_to(root)} requires GLIBC_{required[0]}.{required[1]}')
    if not checked:
        raise SystemExit('No ELF binaries found in the AppDir')
    if failures:
        raise SystemExit('AppImage exceeds the glibc 2.28 baseline:\n' + '\n'.join(failures))
    print(f'Checked {checked} ELF binaries; highest required glibc: {highest[0]}.{highest[1]}')
