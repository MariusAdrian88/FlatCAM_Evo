FROM almalinux:8

# Supply a desktop and software OpenGL, but leave Qt's xkbcommon/XCB helpers to the AppImage.
RUN dnf install -y epel-release \
    && dnf install -y xorg-x11-server-Xvfb xdotool glx-utils mesa-dri-drivers mesa-libEGL fontconfig dbus-libs glib2 \
    && dnf clean all \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

ENV HOME=/tmp DISPLAY=:99 QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 FLATCAM_DATA_PATH=/tmp/flatcam-settings
WORKDIR /tmp
CMD ["bash", "/check_appimage.sh", "/app/AppRun"]
