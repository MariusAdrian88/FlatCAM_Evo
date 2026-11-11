FROM ubuntu:22.04

# Supply a desktop and software OpenGL, but leave Qt's xkbcommon/XCB helpers to the AppImage.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xvfb xauth xdotool mesa-utils libgl1-mesa-dri libegl1 libfontconfig1 libdbus-1-3 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/tmp QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 FLATCAM_DATA_PATH=/tmp/flatcam-settings
WORKDIR /tmp
CMD ["xvfb-run", "-a", "-e", "/dev/stderr", "-s", "-screen 0 1280x1024x24", "bash", "/check_appimage.sh", "/app/AppRun"]
