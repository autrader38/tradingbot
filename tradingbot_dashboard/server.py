"""Loopback-only, fixed-route, GET/HEAD visual server. No broker integration."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs

from .demo_data import build_demo_snapshot
from tradingbot_marketdata.massive import _DEFAULT_WATCHLIST, _MarketDataPreview


HOST = '127.0.0.1'
PORT = 8765
_STATIC = Path(__file__).with_name('static')
_ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
           '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
           '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
           '/market-data.js': ('market-data.js', 'text/javascript; charset=utf-8'),
           '/eod-scanner.js': ('eod-scanner.js', 'text/javascript; charset=utf-8'),
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

    def __init__(self, port, market_data_preview, watchlist):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Invalid local port')
        # No externally supplied handler, host, path, request or snapshot.
        self.assets = {route: (mime, (_STATIC / name).read_bytes())
                       for route, (name, mime) in _ASSETS.items()}
        self.snapshot_json = json.dumps(build_demo_snapshot().to_payload(), allow_nan=False,
                                        ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.market_data = _MarketDataPreview(enabled=market_data_preview, watchlist=watchlist)
        super().__init__((HOST, port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = 'VelocityDashboard'
    sys_version = ''
    timeout = 5

    def log_message(self, format, *args):
        pass  # No arbitrary request/account/path contents printed to the console.

    def _respond(self, status, content_type, body, *, allow='GET, HEAD'):
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
            self.send_header('Allow', allow)
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
        elif self.path == '/api/market-data':
            self._json(self.server.market_data.configuration())
        elif self.path == '/api/market-data/research':
            self._json(self.server.market_data.research())
        elif self.path.partition('?')[0] == '/api/market-data/observation':
            if self.command == 'HEAD':
                self._respond(405, 'text/plain; charset=utf-8', b'Explicit GET load required.\n', allow='GET')
                return
            # A cross-site image/link cannot initiate key-bearing provider work.
            # Custom-header fetches require a preflight, which this server rejects.
            if (self.headers.get('X-Velocity-Market-Preview') != '1'
                or self.headers.get('Origin') not in (None, f'http://{expected}')
                or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none')):
                self._json({'state': 'UNAVAILABLE', 'reason': 'EXPLICIT_LOCAL_LOAD_REQUIRED'}, status=403)
                return
            try:
                query = self.path.partition('?')[2]
                if len(query) > 128: raise ValueError('Invalid query')
                fields = parse_qs(query, strict_parsing=True, keep_blank_values=True, max_num_fields=2)
                if set(fields) != {'ticker', 'view'} or any(len(v) != 1 for v in fields.values()):
                    raise ValueError('Invalid query')
                ticker, view = fields['ticker'][0], fields['view'][0]
                if ticker not in self.server.market_data.configuration()['watchlist'] or view not in ('previous', 'history', 'reference'):
                    raise ValueError('Invalid query')
            except ValueError:
                self._json({'state': 'UNAVAILABLE', 'reason': 'INVALID_REQUEST'}, status=400)
                return
            self._json(self.server.market_data.observe(ticker, view))
        elif self.path in self.server.assets:
            mime, body = self.server.assets[self.path]
            self._respond(200, mime, body)
        else:
            self._respond(404, 'text/plain; charset=utf-8', b'Not found.\n')

    def _json(self, payload, *, status=200):
        body = json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self._respond(status, 'application/json; charset=utf-8', body)

    def do_GET(self):
        self._read()

    def do_HEAD(self):
        self._read()

    def _not_allowed(self):
        self.close_connection = True
        allow = 'GET' if self.path.partition('?')[0] == '/api/market-data/observation' else 'GET, HEAD'
        self._respond(405, 'text/plain; charset=utf-8', b'Read-only visual mode.\n', allow=allow)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _not_allowed


def create_server(*, port=PORT, market_data_preview=False, watchlist=_DEFAULT_WATCHLIST):
    """Always loopback. Port zero is available for isolated local test servers."""
    return _LocalServer(port, market_data_preview, watchlist)
