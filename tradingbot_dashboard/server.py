"""Loopback-only, fixed-route, GET/HEAD visual server. No broker integration."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from socketserver import TCPServer

from .demo_data import build_demo_snapshot


HOST = '127.0.0.1'
PORT = 8765
_STATIC = Path(__file__).with_name('static')
_ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
           '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
           '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
           '/favicon.svg': ('favicon.svg', 'image/svg+xml; charset=utf-8')}
_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; font-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        # HTTPServer normally asks getfqdn() even for a numeric loopback host.
        # The visual workspace needs neither a hostname nor a DNS lookup.
        TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.server_address[1]

    def __init__(self, port):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Invalid local port')
        # No externally supplied handler, host, path, request or snapshot.
        self.assets = {route: (mime, (_STATIC / name).read_bytes())
                       for route, (name, mime) in _ASSETS.items()}
        self.snapshot_json = json.dumps(build_demo_snapshot().to_payload(), allow_nan=False,
                                        ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        super().__init__((HOST, port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = 'VelocityDashboard'
    sys_version = ''
    timeout = 5

    def log_message(self, format, *args):
        pass  # No arbitrary request/account/path contents printed to the console.

    def _respond(self, status, content_type, body):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', _CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        if status == 405:
            self.send_header('Allow', 'GET, HEAD')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _read(self):
        # Defend against DNS rebinding; this app has no public host name or CORS.
        expected = f'{HOST}:{self.server.server_port}'
        if self.headers.get('Host') not in (expected, HOST):
            self._respond(403, 'text/plain; charset=utf-8', b'Local access only.\n')
            return
        # Exact allowlist: never translate a user-controlled URL into a file path.
        if self.path == '/api/snapshot':
            self._respond(200, 'application/json; charset=utf-8', self.server.snapshot_json)
        elif self.path in self.server.assets:
            mime, body = self.server.assets[self.path]
            self._respond(200, mime, body)
        else:
            self._respond(404, 'text/plain; charset=utf-8', b'Not found.\n')

    def do_GET(self):
        self._read()

    def do_HEAD(self):
        self._read()

    def _not_allowed(self):
        self.close_connection = True
        self._respond(405, 'text/plain; charset=utf-8', b'Read-only visual mode.\n')

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _not_allowed


def create_server(*, port=PORT):
    """Always loopback. Port zero is available for isolated local test servers."""
    return _LocalServer(port)
