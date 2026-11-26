# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing        #
# File by:  Marius Adrian Stanciu (c)                      #
# Date:     11/26/2026                                     #
# License:  MIT Licence                                    #
# ##########################################################

import hmac
import json
import math
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from appPlugins.ToolGcodeSender.SenderPendantPage import PAGE_HTML

MAX_BODY = 4096

JOG_DIRECTIONS = ('xplus', 'xminus', 'yplus', 'yminus', 'zplus', 'zminus', 'aplus', 'aminus',
                  'xminus_yplus', 'xplus_yplus', 'xminus_yminus', 'xplus_yminus')
AXES = ('x', 'y', 'z', 'a', 'all')
FEED_VALUES = ('+10', '-10', '100')
SAFE_ACTIONS = ('hold', 'resume', 'stop', 'estop', 'feed_ov')
MOTION_ACTIONS = ('jog', 'zero', 'home')


def new_token():
    return secrets.token_hex(8)


class _BadRequest(Exception):
    pass


class PendantServer:
    """Web pendant. The server thread only calls snapshot() and post(); it never touches a widget or a port."""

    def __init__(self, snapshot, post, token, motion_allowed, max_jog_step, host='127.0.0.1', port=8080):
        self.snapshot = snapshot
        self.post = post
        self.token = token
        self.motion_allowed = motion_allowed
        self.max_jog_step = max_jog_step
        self.host = host
        self._port = port
        self._httpd = None
        self._thread = None

    @property
    def port(self):
        return self._httpd.server_address[1] if self._httpd else self._port

    def url(self):
        return 'http://%s:%d/?t=%s' % (self.host, self.port, self.token)

    def start(self):
        if self._httpd:
            return
        self._httpd = ThreadingHTTPServer((self.host, self._port), self._handler())
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name='SenderPendant', daemon=True)
        self._thread.start()

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd:
            self._port = httpd.server_address[1]
            httpd.shutdown()
            httpd.server_close()
        if self._thread:
            self._thread.join(2)
            self._thread = None

    def checked_command(self, body):
        """Return (action, plain args) for a body that passes every rule; raise _BadRequest or PermissionError."""
        try:
            data = json.loads(body.decode('utf-8'))
        except ValueError:
            raise _BadRequest('bad json')
        if not isinstance(data, dict):
            raise _BadRequest('bad json')
        action = data.get('action')
        if action not in SAFE_ACTIONS and action not in MOTION_ACTIONS:
            raise _BadRequest('unknown action')
        if action in MOTION_ACTIONS and not self.motion_allowed():
            raise PermissionError('motion not allowed')
        if action == 'feed_ov':
            if data.get('value') not in FEED_VALUES:
                raise _BadRequest('bad value')
            return action, {'value': data['value']}
        if action == 'jog':
            step = data.get('step')
            if data.get('direction') not in JOG_DIRECTIONS:
                raise _BadRequest('bad direction')
            if isinstance(step, bool) or not isinstance(step, (int, float)) or not math.isfinite(step) \
                    or step <= 0 or step > self.max_jog_step():
                raise _BadRequest('bad step')
            return action, {'direction': data['direction'], 'step': float(step)}
        if action == 'zero':
            if data.get('axis') not in AXES:
                raise _BadRequest('bad axis')
            return action, {'axis': data['axis']}
        return action, {}

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _send(self, code, body, kind='application/json'):
                self.send_response(code)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code, payload):
                self._send(code, json.dumps(payload, separators=(',', ':')).encode('utf-8'))

            def _authorized(self, query):
                given = self.headers.get('X-Token') or (query.get('t') or [''])[0]
                token = server.token
                return bool(token) and hmac.compare_digest(given.encode('utf-8'), token.encode('utf-8'))

            def do_GET(self):
                parts = urlsplit(self.path)
                if not self._authorized(parse_qs(parts.query)):
                    return self._json(403, {'error': 'forbidden'})
                if parts.path == '/':
                    return self._send(200, PAGE_HTML, 'text/html; charset=utf-8')
                if parts.path == '/state':
                    return self._json(200, server.snapshot())
                self._json(404, {'error': 'not found'})

            def do_POST(self):
                parts = urlsplit(self.path)
                if not self._authorized(parse_qs(parts.query)):
                    return self._json(403, {'error': 'forbidden'})
                if parts.path != '/cmd':
                    return self._json(404, {'error': 'not found'})
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    size = -1
                if size < 0 or size > MAX_BODY:
                    return self._json(400, {'error': 'bad body'})
                try:
                    action, args = server.checked_command(self.rfile.read(size))
                except _BadRequest as err:
                    return self._json(400, {'error': str(err)})
                except PermissionError as err:
                    return self._json(403, {'error': str(err)})
                server.post(action, args)
                self._json(200, {'ok': True})

        return Handler
