"""Single fixed-host HTTPS read. No redirects, URLs from callers or logging."""

from http.client import HTTPSConnection
import ssl

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
