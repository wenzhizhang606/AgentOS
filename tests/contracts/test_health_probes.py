"""AC-013 / AC-028. Infrastructure probes expose status without tenant data."""

import os
from threading import Event
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from agentos.api.app import TaskStore, create_app
from agentos.contracts.errors import PlatformError
from agentos.governance.auth import Authenticator
from agentos.worker import service


def test_ac013_api_liveness_is_independent_of_database_and_readiness_is_not():
    store = Mock(spec=TaskStore)
    with TestClient(create_app(store, Authenticator({}), "pinned")) as client:
        assert client.get("/healthz").json() == {"status": "alive"}
        store.ready.assert_not_called()
        store.ready.return_value = False
        offline = client.get("/readyz")
        assert offline.status_code == 503 and offline.json() == {"status": "not_ready"}
        store.ready.return_value = True
        ready = client.get("/readyz")
        assert ready.status_code == 200 and ready.json() == {"status": "ready"}
        store.ready.assert_called_with("pinned")


@pytest.mark.parametrize("error", [False, True])
def test_ac028_worker_heartbeat_requires_successful_poll(tmp_path, monkeypatch, error):
    heartbeat = tmp_path / "worker.heartbeat"
    heartbeat.touch()  # A stamp from the previous process must not imply readiness.
    stop = Event()
    monkeypatch.setattr(service, "Event", lambda: stop)
    monkeypatch.setattr(service.signal, "signal", lambda *_: None)

    def poll(*_):
        stop.set()
        if error:
            raise PlatformError("TRANSIENT_DEPENDENCY_FAILURE", "DB unavailable")
        return False  # An empty queue is a valid poll.

    monkeypatch.setattr(service, "run_one", poll)
    service.serve(None, None, None, heartbeat_path=heartbeat)
    assert service.heartbeat_is_recent(heartbeat) is (not error)


def test_ac028_stale_heartbeat_is_unhealthy(tmp_path):
    path = tmp_path / "worker.heartbeat"
    path.touch()
    os.utime(path, (1, 1))
    assert not service.heartbeat_is_recent(path)
