"""
Install the optional packages of requirements-optional.txt, one at a time.

FlatCAM Evo works without them. A package that fails to install (no wheel for this platform, no compiler) is
skipped with a note, and the next package is installed. The script always ends with exit code 0.

Usage (with the Python of the FlatCAM environment):  python install_optional.py
"""

import subprocess
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().with_name('requirements-optional.txt')


def packages(path=REQUIREMENTS):
    """The requirement lines of the file, without comments and empty lines."""
    lines = (line.split('#', 1)[0].strip() for line in path.read_text(encoding='utf-8').splitlines())
    return [line for line in lines if line]


def main():
    failed = []
    for package in packages():
        print(f"Installing the optional package {package} ...", flush=True)
        if subprocess.call([sys.executable, '-m', 'pip', 'install', package]) != 0:
            failed.append(package)
    if failed:
        print("\nThese optional packages were not installed: %s. FlatCAM Evo works without them." %
              ", ".join(failed))
    else:
        print("\nAll optional packages are installed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
