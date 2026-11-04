#!/bin/sh -e

# Installs FlatCAM Evo on Debian, Ubuntu and other apt based Linux distributions.
#
# Run it from the project folder:   ./setup_ubuntu.sh
# Use another Python 3.10+:          PYTHON=python3.12 ./setup_ubuntu.sh
#
# 1. apt installs the system libraries (this step asks for the sudo password).
# 2. The Python packages go into a virtual environment in the .venv folder of the project. Newer distributions
#    refuse a system-wide 'pip install'.
#
# Start FlatCAM Evo after that with:   .venv/bin/python flatcam.py

cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
	echo "FlatCAM Evo needs Python 3.10 or newer. Found: $("$PYTHON" --version 2>&1)" >&2
	exit 1
fi

# ################################
# System packages

sudo apt-get update
sudo apt-get install -y \
	python3-venv \
	python3-dev \
	python3-pip \
	build-essential \
	libgeos-dev \
	libspatialindex-dev \
	libfreetype6-dev \
	libpng-dev \
	gdal-bin \
	libgdal-dev \
	libgl1 \
	libegl1 \
	libxkbcommon-x11-0 \
	libxcb-cursor0

# ################################
# Python packages, in .venv

"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel

# the GDAL Python package must have the same version as the GDAL system library
.venv/bin/python -m pip install "gdal==$(gdal-config --version)"

.venv/bin/python -m pip install -r requirements.txt

# optional packages (pyclipr, OR-Tools): each one is installed separately, a failure skips only that package
.venv/bin/python install_optional.py

echo
echo "Done. Start FlatCAM Evo with:   .venv/bin/python flatcam.py"
