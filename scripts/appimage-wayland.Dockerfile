FROM ubuntu:24.04

# A Wayland compositor without a display (weston headless) and software Mesa; Qt's helpers stay with the AppImage.
# libgtk-3-0 is the host GTK that the bundled Qt GTK3 platform theme loads on GNOME (issue #90).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        weston libgtk-3-0 libegl1 libegl-mesa0 libgl1-mesa-dri libgles2 fontconfig libdbus-1-3 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/tmp LIBGL_ALWAYS_SOFTWARE=1 FLATCAM_DATA_PATH=/tmp/flatcam-settings
WORKDIR /tmp
CMD ["bash", "/check_appimage_wayland.sh", "/app/AppRun"]
