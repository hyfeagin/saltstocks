"""
Command-line entry point for SaltStocks.

Usage (from the project root):
    python3 -m app.cli serve
    python3 -m app.cli serve --port 8080
    python3 -m app.cli serve --no-browser
    python3 -m app.cli serve --no-reload
"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser


def _open_browser(url: str, delay: float = 1.5) -> None:
    """Open the browser after a short delay to let the server start."""
    def _open():
        time.sleep(delay)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    host = "127.0.0.1"
    port = args.port
    url = f"http://{host}:{port}"

    if not args.no_browser:
        _open_browser(url)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=not args.no_reload,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="saltstocks", description="SaltStocks inventory app")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the web server")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    serve_parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    serve_parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload on code changes")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
