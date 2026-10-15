"""HTTP on a Unix socket. Contract transport. No extra model dependencies."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from taste.api import create_app


def main() -> None:
    sock = os.environ.get("TASTE_SOCK") or str(Path.cwd() / ".taste-4.sock")
    path = Path(sock)
    if path.exists():
        path.unlink()
    app = create_app()
    # uvicorn exposes no uds_permissions option (granian does). Unix sockets
    # bind at 0o777 & ~umask, so a 0o177 umask during the bind yields the
    # same 0o600 owner-only socket as taste-3.
    previous_umask = os.umask(0o177)
    try:
        uvicorn.run(app, uds=sock, log_level="warning", access_log=False)
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    main()
