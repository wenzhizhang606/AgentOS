import logging
from pathlib import Path
import signal
import socket
from threading import Event
import time
from uuid import uuid4

from agentos.contracts.errors import PlatformError
from agentos.runtime.managed_task import run_one


def heartbeat_is_recent(path: Path) -> bool:
    try:
        last_poll = path.stat().st_mtime
    except OSError:
        return False
    return 0 <= time.time() - last_poll < 180


def serve(store, model, release, *, heartbeat_path: Path | None = None):
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    worker_id = f"{socket.gethostname()[:48]}-{uuid4().hex}"
    if heartbeat_path is not None:
        heartbeat_path.unlink(missing_ok=True)
    while not stop.is_set():
        try:
            handled = run_one(store, model, release, worker_id)
            if heartbeat_path is not None:
                heartbeat_path.touch()
            if not handled:
                stop.wait(1)
        except PlatformError as exc:
            logging.warning("Worker operation stopped code=%s", exc.code)
            stop.wait(1)
