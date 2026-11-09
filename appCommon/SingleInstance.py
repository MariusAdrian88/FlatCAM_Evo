# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File: SingleInstance.py                                  #
# ##########################################################

"""
Hand the files of a second launch to the FlatCAM window that is already open.

The open instance listens on a local socket (a named pipe on Windows, a unix socket elsewhere) whose name belongs to
the current user and this installation. A lock file decides which process listens: on Windows two pipes can have the
same name, so the socket itself cannot decide it. A second launch with files sends them there as one line of JSON and
waits for the answer of the instance (one line of JSON with its process id). Only then it exits: without the answer
the files are not lost, the launch starts as a normal instance. The message has a deadline: after it the sender opens
the files itself, so the instance drops a request that it reads too late.

The listener starts right after the QApplication exists, before the App is built: files that arrive while the App is
starting are kept until it is ready (set_ready).
"""

import getpass
import hashlib
import json
import logging
import os
import sys
import time

from PyQt6 import QtCore, QtNetwork
from PyQt6.QtCore import pyqtSignal

log = logging.getLogger('base')

# a message is a list of paths: a bigger one is not from FlatCAM
MAX_MESSAGE = 1 << 20

# the open instance may be busy (a project is loading, a dialog is open): how long a launch waits for its answer
ACK_TIMEOUT_MS = 10000
# the instance takes the files when the sender closes the connection, or after this time
RELEASE_DELAY_MS = 1000


def server_name():
    """
    Name of the local socket: one per user and installation folder. Kept short (a unix socket path has a small limit).
    The FLATCAM_INSTANCE_NAME environment variable sets another name (the tests use one per process).
    """
    override = os.environ.get('FLATCAM_INSTANCE_NAME')
    if override:
        return override
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
    app_folder = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # named pipes are global on Windows: the same user in two remote desktop sessions gets two names
    session = _windows_session()
    digest = hashlib.sha1(('%s|%s|%s' % (user, app_folder, session)).encode('utf-8', 'replace')).hexdigest()
    return 'FlatCAM_EVO-' + digest[:12]


def _windows_session():
    if sys.platform != 'win32':
        return ''
    try:
        import ctypes
        session = ctypes.c_ulong(0)
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            return str(session.value)
    except Exception:
        pass
    return ''


def _lock_path(name):
    return os.path.join(QtCore.QDir.tempPath(), name + '.lock')


def _allow_foreground(pid):
    """Let the open window come to the front: only the process that has the foreground may give that right."""
    if sys.platform != 'win32' or type(pid) is not int or pid <= 0:
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(pid)
    except Exception:
        pass


def _connect(sock, timeout_ms):
    # the owner of the lock may start to listen a few milliseconds after it took the lock
    deadline = QtCore.QDeadlineTimer(timeout_ms)
    while True:
        sock.connectToServer(server_name())
        if sock.waitForConnected(max(1, deadline.remainingTime())):
            return True
        if deadline.hasExpired():
            return False
        sock.abort()
        QtCore.QThread.msleep(20)


def send_to_running(files, timeout_ms=300, ack_timeout_ms=ACK_TIMEOUT_MS):
    """
    Send the files to the open instance.

    :param files:           list of absolute paths
    :param timeout_ms:      how long to wait for the connection and for the write
    :param ack_timeout_ms:  how long to wait for the answer of the instance
    :return:                True if an instance took them (it answered), False if none is listening or none answered
    """
    sock = QtNetwork.QLocalSocket()
    if not _connect(sock, timeout_ms):
        return False

    # the instance must answer before the deadline; the margin is for the answer to arrive before this launch gives up
    margin_ms = min(2000, ack_timeout_ms // 2)
    deadline = time.time() + (ack_timeout_ms - margin_ms) / 1000.0
    message = json.dumps({'files': [str(f) for f in files], 'deadline': deadline}) + '\n'
    data = message.encode('utf-8')
    sock.write(data)
    sock.flush()
    # on Windows this returns False when the pipe took the bytes already: the result is not the outcome
    sock.waitForBytesWritten(timeout_ms)

    reply = bytearray()
    wait = QtCore.QDeadlineTimer(ack_timeout_ms)
    while b'\n' not in reply:
        # waitForBytesWritten() may have read the answer already: waitForReadyRead() waits only for new data
        if not sock.bytesAvailable() and not sock.waitForReadyRead(max(1, wait.remainingTime())):
            break
        reply += bytes(sock.readAll())
        if wait.hasExpired():
            break

    ack = None
    if b'\n' in reply:
        try:
            ack = json.loads(bytes(reply).partition(b'\n')[0].decode('utf-8'))
        except ValueError:
            ack = None

    if isinstance(ack, dict) and ack.get('ack') is True:
        _allow_foreground(ack.get('pid'))
        accepted = True
    else:
        log.warning("SingleInstance: the open instance did not confirm the files.")
        accepted = False

    sock.disconnectFromServer()
    if sock.state() != QtNetwork.QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(timeout_ms)
    return accepted


class InstanceServer(QtCore.QObject):
    """
    Receives the files that a second launch sends. Runs in the main thread. Until set_ready() the requests are kept.
    """

    files_received = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = None
        self.lock = None
        self.buffers = {}
        self.pending = []
        self.ready = False
        # macOS: the FileOpenFilter of this process, started with the server
        self.file_filter = None

    def start(self):
        """
        :return: True if this process listens now
        """
        name = server_name()
        # the lock is atomic across processes: of two launches that start together only one listens. A lock left by a
        # crashed instance is free again (its process id does not run); 0 turns off only the check of the age.
        self.lock = QtCore.QLockFile(_lock_path(name))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            log.debug("SingleInstance: another instance holds %s." % name)
            self.lock = None
            return False
        # a crashed instance leaves its unix socket file behind; the lock says that it is not in use
        QtNetwork.QLocalServer.removeServer(name)
        self.server = QtNetwork.QLocalServer(self)
        self.server.setSocketOptions(QtNetwork.QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(name):
            log.warning("SingleInstance: cannot listen on %s: %s" % (name, self.server.errorString()))
            self.server = None
            self.lock.unlock()
            self.lock = None
            return False
        self.server.newConnection.connect(self._on_new_connection)
        return True

    def set_ready(self):
        """The App can take files now: hand over the requests that came before."""
        self.ready = True
        requests, self.pending = self.pending, []
        for files in requests:
            self.files_received.emit(files)

    def close(self):
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                pass
        for sock in list(self.buffers):
            try:
                sock.abort()
            except Exception:
                pass
        self.buffers.clear()
        if self.lock is not None:
            self.lock.unlock()
            self.lock = None

    def _on_new_connection(self):
        for _i in range(64):
            if self.server is None or not self.server.hasPendingConnections():
                break
            sock = self.server.nextPendingConnection()
            self.buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            # a sender that is quick may have written and gone before the signals were connected
            if sock.bytesAvailable():
                self._on_ready_read(sock)
            if sock.state() == QtNetwork.QLocalSocket.LocalSocketState.UnconnectedState:
                self._on_disconnected(sock)

    def _on_ready_read(self, sock):
        buf = self.buffers.get(sock)
        if buf is None:
            return
        buf += bytes(sock.readAll())
        if len(buf) > MAX_MESSAGE:
            log.warning("SingleInstance: message too big, dropped.")
            self._drop(sock)
            return
        while b'\n' in buf:
            line, _sep, rest = bytes(buf).partition(b'\n')
            buf[:] = rest
            self._handle(line, sock)

    def _on_disconnected(self, sock):
        # a sender that goes before the answer opens the files itself: what is left in the buffer is not taken
        self._drop(sock)

    def _drop(self, sock):
        self.buffers.pop(sock, None)
        sock.deleteLater()

    def _handle(self, line, sock):
        try:
            data = json.loads(line.decode('utf-8'))
        except Exception:
            log.warning("SingleInstance: ignored a malformed message.")
            return
        files = data.get('files') if isinstance(data, dict) else None
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            log.warning("SingleInstance: ignored a message with an unexpected content.")
            return
        if not files:
            return
        deadline = data.get('deadline')
        if type(deadline) in (int, float) and time.time() > deadline:
            log.warning("SingleInstance: a request came too late, the sender opens the files itself.")
            return

        # the sender gets the process id to give it the right to bring this window to the front, and then it goes:
        # the files are taken at that point, so the window is not raised before the right is given
        done = []

        def release():
            if not done:
                done.append(True)
                self._deliver(files)

        sock.disconnected.connect(release)
        QtCore.QTimer.singleShot(RELEASE_DELAY_MS, release)
        try:
            sock.write((json.dumps({'ack': True, 'pid': os.getpid()}) + '\n').encode('utf-8'))
            sock.flush()
        except Exception:
            release()

    def _deliver(self, files):
        if self.ready:
            self.files_received.emit(files)
        else:
            self.pending.append(files)


def start_listening(qapp):
    """
    Start the listener of this process, before the App exists.

    :param qapp:    the QApplication
    :return:        (server, listening). The server also holds the FileOpen filter on macOS. listening is False when
    another process keeps the socket: this process hands its files to that one.
    """
    server = InstanceServer()
    listening = server.start()
    if sys.platform == 'darwin':
        server.file_filter = FileOpenFilter()
        qapp.installEventFilter(server.file_filter)
    return server, listening


def add_to_os_recent(path):
    """Put a file that was opened in the recent documents of the operating system (the jump list on Windows)."""
    if sys.platform != 'win32' or 'PYTEST_CURRENT_TEST' in os.environ or not path or not os.path.isfile(path):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SHAddToRecentDocs(0x3, ctypes.c_wchar_p(os.path.abspath(path)))  # SHARD_PATHW
    except Exception:
        log.debug("SingleInstance: could not add %s to the recent documents." % path)


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


class FileOpenFilter(QtCore.QObject):
    """
    macOS delivers a double-clicked file as a FileOpen event to the running application, also before the App is built.
    The files are kept until the App asks for them. The same path is taken once.
    """

    files_received = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending = []
        self.ready = False

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.FileOpen:
            path = event.file()
            if path:
                if self.ready:
                    self.files_received.emit([path])
                elif _path_key(path) not in {_path_key(p) for p in self.pending}:
                    self.pending.append(path)
            return True
        return False

    def set_ready(self, skip=()):
        """
        The App can take files now: hand over those that came before.

        :param skip:    paths that the App opens already, they were also given on the command line
        """
        self.ready = True
        skipped = {_path_key(p) for p in skip}
        files = [p for p in self.pending if _path_key(p) not in skipped]
        self.pending = []
        if files:
            self.files_received.emit(files)
