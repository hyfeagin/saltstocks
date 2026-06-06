"""
Command-line entry point for SaltStocks.

Usage (from the project root):
    python3 -m app.cli serve
    python3 -m app.cli serve --port 8080
    python3 -m app.cli serve --no-browser
    python3 -m app.cli serve --no-reload

    python3 -m app.cli window
    python3 -m app.cli window --port 8000

SSL (HTTPS):
    Place mkcert-generated certs in a certs/ folder at the project root:
        certs/localhost.pem
        certs/localhost-key.pem
    The app detects them automatically and switches to HTTPS.
    Generate with:
        brew install mkcert && mkcert -install
        mkcert -cert-file certs/localhost.pem -key-file certs/localhost-key.pem localhost 127.0.0.1
"""
from __future__ import annotations

import argparse
import os
import ssl
import threading
import time
import webbrowser
from pathlib import Path


_PROJECT_ROOT = Path(__file__).parent.parent


def _find_ssl_certs() -> tuple[str, str] | None:
    """Return (certfile, keyfile) paths if both exist in certs/, else None."""
    cert = _PROJECT_ROOT / "certs" / "localhost.pem"
    key = _PROJECT_ROOT / "certs" / "localhost-key.pem"
    if cert.exists() and key.exists():
        return str(cert), str(key)
    return None


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
    ssl_certs = _find_ssl_certs()
    scheme = "https" if ssl_certs else "http"
    url = f"{scheme}://{host}:{port}"

    if ssl_certs:
        print(f"SSL certs found — running on {url}")

    if not args.no_browser:
        _open_browser(url)

    uvicorn_kwargs: dict = dict(
        host=host,
        port=port,
        reload=not args.no_reload,
    )
    if ssl_certs:
        uvicorn_kwargs["ssl_certfile"] = ssl_certs[0]
        uvicorn_kwargs["ssl_keyfile"] = ssl_certs[1]

    uvicorn.run("app.main:app", **uvicorn_kwargs)


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll until the server responds or timeout expires. Returns True if ready."""
    import urllib.request
    import urllib.error

    # Use an unverified SSL context for the health-check so it works with
    # localhost certs before the OS trust store is consulted.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1, context=ctx)
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
    ssl_certs = _find_ssl_certs()
    scheme = "https" if ssl_certs else "http"
    url = f"{scheme}://{host}:{port}"

    if ssl_certs:
        print(f"SSL certs found — running on {url}")

    uvicorn_kwargs: dict = dict(host=host, port=port, reload=False)
    if ssl_certs:
        uvicorn_kwargs["ssl_certfile"] = ssl_certs[0]
        uvicorn_kwargs["ssl_keyfile"] = ssl_certs[1]

    config = uvicorn.Config("app.main:app", **uvicorn_kwargs)
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


def cmd_reset_password(_args: argparse.Namespace) -> None:
    """Reset the app password after verifying identity via Touch ID or Mac password."""
    from .auth import hash_password, macos_authenticate, set_password_hash
    from .db import get_conn
    from .migrate import migrate
    import getpass

    print("Salt Stocks — Reset Password")
    print("─" * 32)

    authenticated = macos_authenticate("reset the Salt Stocks password")
    if not authenticated:
        # macos_authenticate returns False if the framework is unavailable too,
        # so fall back to the app's current password as a last resort.
        print("Touch ID / Mac password not available. Enter your current app password to continue.")
        from .auth import get_password_hash, verify_password
        conn = get_conn()
        migrate()
        stored = get_password_hash(conn)
        conn.close()
        if stored:
            current = getpass.getpass("Current app password: ")
            if not verify_password(current, stored):
                print("Incorrect password. Aborting.")
                return
        else:
            print("No password set yet — proceeding to create one.")

    new_pw = getpass.getpass("New password (min 8 chars): ")
    if len(new_pw) < 8:
        print("Password must be at least 8 characters. Aborting.")
        return
    confirm = getpass.getpass("Confirm new password: ")
    if new_pw != confirm:
        print("Passwords do not match. Aborting.")
        return

    conn = get_conn()
    migrate()
    set_password_hash(conn, hash_password(new_pw))
    conn.close()
    print("Password updated successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="saltstocks", description="SaltStocks inventory app")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", help="Start the web server and open in browser")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    serve_parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    serve_parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload on code changes")

    window_parser = sub.add_parser("window", help="Open SaltStocks in a native desktop window (requires pywebview)")
    window_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")

    sub.add_parser("reset-password", help="Reset the app password (requires Touch ID or Mac password)")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "window":
        cmd_window(args)
    elif args.command == "reset-password":
        cmd_reset_password(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
