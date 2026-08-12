"""Serve a board to the frame from this machine, for bring-up.

The frame normally downloads from GitHub, which means every change to the
layout is a commit, a workflow run and a wait. During setup that is a
miserable loop, and it puts two systems in the way of answering one
question: does the panel draw what we drew?

So point the frame at a laptop instead. Plain HTTP, no TLS, no GitHub:

    python -m birddisplay.show --backend inky --preview-path board.png
    python -m scripts.serve_board

then put the two URLs it prints into the frame's secrets.py and press
button A. Put the raw.githubusercontent.com URLs back when you are done.

Serves exactly two files and nothing else -- it is a bring-up tool on a
home network, not a web server.
"""

from __future__ import annotations

import argparse
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTENT_TYPES = {".png": "image/png", ".json": "application/json"}


def local_address() -> str:
    """This machine's address on the LAN, as the frame would reach it.

    Asking the routing table which interface would carry a packet to the
    outside world is the only reliable way; the hostname usually resolves
    to 127.0.0.1, which the frame cannot use.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routed, never answers
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def make_handler(directory: Path, names: set[str]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            name = self.path.lstrip("/").split("?", 1)[0]
            if name not in names:
                self.send_error(404, "this server has two files")
                return
            path = directory / name
            try:
                payload = path.read_bytes()
            except OSError as exc:
                self.send_error(404, str(exc))
                return
            self.send_response(200)
            self.send_header(
                "Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream")
            )
            self.send_header("Content-Length", str(len(payload)))
            # The frame decides what to download by comparing the sha in
            # the manifest, so a cached manifest would defeat the point.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args) -> None:
            print(f"  {self.address_string()}  {fmt % args}")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path("."))
    parser.add_argument("--image", default="board.png")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    directory = args.dir.resolve()
    manifest = Path(args.image).with_suffix(".json").name
    names = {args.image, manifest}

    missing = [name for name in sorted(names) if not (directory / name).exists()]
    if missing:
        print(f"nothing to serve: {', '.join(missing)} not in {directory}")
        print("run: python -m birddisplay.show --backend inky --preview-path board.png")
        return 1

    host = local_address()
    print(f"serving {', '.join(sorted(names))} from {directory}\n")
    print("Put these in the frame's secrets.py:\n")
    print(f'    MANIFEST_URL = "http://{host}:{args.port}/{manifest}"')
    print(f'    BOARD_URL = "http://{host}:{args.port}/{args.image}"')
    print(
        f"\n({host} is this machine's address as the routing table sees it. "
        "If the\nframe cannot reach it, use whatever ifconfig or ipconfig says "
        "instead.)"
    )
    print("\nCtrl-C to stop.\n")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(directory, names))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
