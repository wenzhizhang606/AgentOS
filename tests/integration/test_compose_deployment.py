"""Read-only checks for an already running deployment; opt in after Compose startup."""

import json
import os
from pathlib import Path
import subprocess
from urllib.request import urlopen

import pytest


pytestmark = pytest.mark.skipif(os.getenv("AGENTOS_TEST_COMPOSE") != "1",
                                reason="Requires an explicitly selected running Compose project")
ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    return subprocess.check_output(["docker", *args], cwd=ROOT, text=True, timeout=20).strip()


def test_ac028_compose_services_and_probes():
    for service in ("mysql", "api", "worker"):
        ids = docker("compose", "ps", "--quiet", service).splitlines()
        assert ids, service
        for container in ids:
            assert docker("inspect", "--format", "{{.State.Health.Status}}", container) == "healthy", service
    migration = docker("compose", "ps", "--all", "--quiet", "migrate")
    assert docker("inspect", "--format", "{{.State.Status}}:{{.State.ExitCode}}", migration) == "exited:0"
    address = docker("compose", "port", "api", "8000")
    for route, status in (("healthz", "alive"), ("readyz", "ready")):
        with urlopen(f"http://{address}/{route}", timeout=8) as reply:
            assert json.load(reply) == {"status": status}
    with urlopen(f"http://{address}/monitor", timeout=8) as reply:
        assert reply.status == 200 and reply.headers.get_content_type() == "text/html"
