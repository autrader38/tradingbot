"""Run with: py -3.12 -m tradingbot_dashboard --open"""

import argparse
import webbrowser

from .server import HOST, PORT, create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description='Velocity Trading Group read-only dashboard')
    parser.add_argument('--open', action='store_true', help='Open the local dashboard in your browser')
    args = parser.parse_args(argv)
    try:
        server = create_server()
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
