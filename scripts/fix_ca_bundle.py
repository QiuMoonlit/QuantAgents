"""Point curl_cffi at a CA bundle on an ASCII-only path.

Why this exists
---------------
yfinance talks to Yahoo through curl_cffi, whose native layer decodes file
paths using the process ANSI code page. This project lives in a folder whose
name contains non-ASCII characters, so ``certifi.where()`` comes back
mangled:

    C:\\Users\\zt000\\Desktop\\½ļ (8)\\.venv\\...\\certifi\\cacert.pem

The file is present and valid, but curl cannot resolve the mangled path and
fails with ``curl: (77) error adding trust anchors``. Every market-data call
dies before it reaches the network.

Workaround: copy the bundle somewhere curl *can* read, and point
``CURL_CA_BUNDLE`` at that copy. ``%USERPROFILE%`` is pure ASCII on this
machine, which makes it a safe home for the copy.

Run after recreating the virtualenv, or whenever the venv's certifi changes:

    python scripts\\fix_ca_bundle.py

It is safe to re-run; it overwrites the copy.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


# ASCII-only destination, first writable one wins.
def _ascii_home() -> Path:
    for candidate in (os.environ.get("USERPROFILE"), os.environ.get("HOME"), os.getcwd()):
        if not candidate or not candidate.isascii():
            continue
        path = Path(candidate)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".quantagent-write-probe"
            probe.touch()
            probe.unlink()
        except OSError:
            continue
        return path
    raise SystemExit("no writable ASCII-only directory found for the CA bundle copy")


def main() -> int:
    try:
        import certifi
    except ImportError:
        print("certifi is not installed in this interpreter", file=sys.stderr)
        return 1

    source = Path(certifi.where())
    if not source.exists():
        print(f"source bundle missing: {source}", file=sys.stderr)
        return 1

    target = _ascii_home() / "quantagent-cacert.pem"
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        print(f"could not write {target}: {exc}", file=sys.stderr)
        print(
            "Copy the bundle by hand from a terminal that can write there:\n"
            f"  copy \"{source}\" \"{target}\"",
            file=sys.stderr,
        )
        return 1

    print(f"copied {source.stat().st_size} bytes")
    print(f"      -> {target}")
    print()
    print("Now make sure .env contains:")
    print(f"    CURL_CA_BUNDLE={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
