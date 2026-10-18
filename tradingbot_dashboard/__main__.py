"""Run with: py -3.12 -m tradingbot_dashboard --open"""

import argparse
import webbrowser

from .server import HOST, PORT, create_server


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown/mistyped arguments must not echo accidentally supplied secrets.
        super().error('Invalid dashboard arguments. Use --help.')


def main(argv=None):
    parser = _Parser(description='Velocity Trading Group read-only dashboard')
    parser.add_argument('--open', action='store_true', help='Open the local dashboard in your browser')
    parser.add_argument('--market-data-preview', action='store_true',
                        help='Allow explicit user EOD loads; personal/non-business use only')
    parser.add_argument('--watchlist', help='One to five uppercase tickers, comma-separated; requires preview')
    args = parser.parse_args(argv)
    if args.watchlist is not None and not args.market_data_preview:
        parser.error('--watchlist requires --market-data-preview')
    try:
        options = {'market_data_preview': True} if args.market_data_preview else {}
        if args.watchlist is not None: options['watchlist'] = tuple(args.watchlist.split(','))
        server = create_server(**options)
    except ValueError:
        parser.exit(2, 'Invalid preview settings. Use one to five distinct uppercase stock tickers.\n')
    except OSError:
        parser.exit(1, f'Cannot open local port {PORT}. Stop the other local server and try again.\n')
    url = f'http://{HOST}:{PORT}'
    print(f'Velocity Trading Group dashboard\n{url}\nREAD-ONLY VISUAL MODE\nPress Ctrl+C to stop.', flush=True)
    try:
        if args.open:
            try:
                webbrowser.open(url, new=2)
            except Exception:
                print('Browser could not open automatically. Open the local URL above.', flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        print('\nDashboard stopped.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
