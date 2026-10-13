#!/usr/bin/env python3
"""Start HydroWitness on this computer and open the dashboard in the browser.

The START_HERE scripts call this after setting up Python. It serves only this computer (127.0.0.1), picks a free
port, makes a random access token the first time and keeps it in .hydrowitness_token, and opens the page already
signed in. With no site profile it runs the demo site and says so on the page.
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_or_make_token(path: Path) -> str:
    try:
        tok = path.read_text().strip()
        if len(tok) >= 20 and all(c.isalnum() or c in "-_" for c in tok):
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    path.write_text(tok + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return tok


def free_port(first: int = 8000, tries: int = 40) -> int:
    for port in range(first, first + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {first} and {first + tries - 1}. Close other programs and try again.")


def open_when_ready(port: int, url: str, show: bool) -> None:
    for _ in range(150):                                   # up to about 30 s
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    else:
        return
    print(f"\nHydroWitness is running.\n  Dashboard: http://127.0.0.1:{port}/\n"
          "  Press Ctrl+C (or close this window) to stop it.\n", flush=True)
    if show:
        webbrowser.open(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="default: the first free port from 8000")
    ap.add_argument("--token-file", default=str(ROOT / ".hydrowitness_token"))
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    token = read_or_make_token(Path(args.token_file))
    os.environ["HYDROWITNESS_TOKEN"] = token
    port = args.port or free_port()
    sys.path.insert(0, str(ROOT))
    try:
        import uvicorn
        from hydrowitness.api import app
    except ImportError as e:
        print(f"A needed package is missing ({e}). Run the START_HERE script again.", file=sys.stderr)
        return 1

    show = not args.no_browser and not os.environ.get("HYDROWITNESS_NO_BROWSER")
    threading.Thread(target=open_when_ready, args=(port, f"http://127.0.0.1:{port}/#token={token}", show),
                     daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
