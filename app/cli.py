"""
Command-line entry point for SaltStocks.

Usage (from the project root):
    python3 -m app.cli serve
    python3 -m app.cli serve --port 8080
    python3 -m app.cli serve --no-browser
    python3 -m app.cli serve --no-reload

    python3 -m app.cli window
    python3 -m app.cli window --port 8000
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


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll until the server responds or timeout expires. Returns True if ready."""
    import urllib.request
    import urllib.error

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def cmd_window(args: argparse.Namespace) -> None:
    try:
        import webview  # type: ignore
    except ImportError:
        print(
            "pywebview is not installed.\n"
            "Install it with:  pip install pywebview\n"
            "Then re-run:      python3 -m app.cli window"
        )
        return

    import uvicorn

    host = "127.0.0.1"
    port = args.port
    url = f"http://{host}:{port}"

    # Build uvicorn server — reload must be off when running in a thread.
    config = uvicorn.Config("app.main:app", host=host, port=port, reload=False)
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if not _wait_for_server(url):
        print("Server did not start in time. Aborting.")
        server.should_exit = True
        return

    # webview.start() must be called on the main thread.
    window = webview.create_window("SaltStocks", url, width=1280, height=800, min_size=(800, 600))  # noqa: F841

    webview.start()

    # Window was closed — shut down the server.
    server.should_exit = True
    server_thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(prog="saltstocks", description="SaltStocks inventory app")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the web server and open in browser")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    serve_parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    serve_parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload on code changes")

    window_parser = sub.add_parser("window", help="Open SaltStocks in a native desktop window (requires pywebview)")
    window_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "window":
        cmd_window(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
