"""Single fixed-host HTTPS read. No redirects, URLs from callers or logging."""

from http.client import HTTPSConnection
from datetime import date
import re
import ssl

from .discovery import (_MAX_BODY as _MAX_GROUPED_BODY, _GroupedLimitError,
                        _GroupedDiagnostic, _GroupedValidationError)

__all__ = ()

_HOST = 'api.massive.com'
_MAX_BODY = 262144


def _read_response(path, credential):
    connection = HTTPSConnection(_HOST, timeout=8, context=ssl.create_default_context())
    connection.set_debuglevel(0)
    try:
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + credential,
            'Accept': 'application/json', 'User-Agent': 'Velocity-Local-EOD-Preview/1'})
        response = connection.getresponse()
        content_type = response.getheader('Content-Type', '')
        retry_after = response.getheader('Retry-After', '')
        body = response.read(_MAX_BODY + 1)
        if len(body) > _MAX_BODY: raise ValueError('Invalid market response')
        return response.status, content_type, retry_after, body
    finally:
        try: connection.close()
        except Exception: pass


def _read_grouped_response(path, credential):
    """Separate fixed policy; existing per-ticker limit stays exactly 256 KiB."""
    match = re.fullmatch(r'/v2/aggs/grouped/locale/us/market/stocks/(\d{4}-\d{2}-\d{2})\?adjusted=true&include_otc=false', path, flags=re.ASCII)
    if not match: raise ValueError('Invalid grouped request')
    date.fromisoformat(match[1])
    connection = HTTPSConnection(_HOST, timeout=12, context=ssl.create_default_context())
    connection.set_debuglevel(0)
    try:
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + credential,
            'Accept': 'application/json', 'User-Agent': 'Velocity-Local-EOD-Preview/1'})
        response = connection.getresponse()
        encoding = response.getheader('Content-Encoding', 'identity')
        if type(encoding) is not str or encoding.lower() not in ('identity', ''):
            raise _GroupedValidationError(_GroupedDiagnostic.HTTP_CONTENT_ENCODING_INVALID)
        length = response.getheader('Content-Length')
        if length is not None:
            if type(length) is not str or not length.isascii() or not length.isdigit() or len(length) > 10:
                raise _GroupedValidationError(_GroupedDiagnostic.HTTP_CONTENT_LENGTH_INVALID)
            if int(length) > _MAX_GROUPED_BODY: raise _GroupedLimitError()
        body = response.read(_MAX_GROUPED_BODY + 1)
        if len(body) > _MAX_GROUPED_BODY: raise _GroupedLimitError()
        if length is not None and len(body) != int(length):
            raise _GroupedValidationError(_GroupedDiagnostic.HTTP_CONTENT_LENGTH_INVALID)
        return response.status, response.getheader('Content-Type', ''), response.getheader('Retry-After', ''), body
    finally:
        try: connection.close()
        except Exception: pass
