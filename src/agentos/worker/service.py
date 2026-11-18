import logging
import signal
import socket
from threading import Event
from uuid import uuid4

from agentos.contracts.errors import PlatformError
from agentos.runtime.managed_task import run_one


def serve(store, model, release):
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    worker_id = f"{socket.gethostname()[:48]}-{uuid4().hex}"
    while not stop.is_set():
        try:
            if not run_one(store, model, release, worker_id):
                stop.wait(1)
        except PlatformError as exc:
            logging.warning("Worker operation stopped code=%s", exc.code)
            stop.wait(1)
