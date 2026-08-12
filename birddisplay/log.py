"""Logging setup shared by both entrypoints.

Under systemd, stderr goes straight to journald, so there is no log file
to rotate and nothing to configure:

    journalctl -u birddisplay-fetch -n 50
"""

from __future__ import annotations

import logging
import os
import sys


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # journald adds its own timestamp; a second one is just noise.
    under_systemd = bool(os.environ.get("JOURNAL_STREAM") or os.environ.get("INVOCATION_ID"))
    fmt = "%(levelname)s %(name)s: %(message)s"
    if not under_systemd:
        fmt = "%(asctime)s " + fmt

    logging.basicConfig(
        level=level,
        format=fmt,
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    # urllib3 logs every connection at DEBUG; only useful when debugging it.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
