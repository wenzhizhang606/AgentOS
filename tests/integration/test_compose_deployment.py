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


@pytest.mark.parametrize(("configured", "expected"), [
    ("", "https://pypi.org/simple"),
    ("https://mirror.example/simple", "https://mirror.example/simple"),
])
def test_ac028_compose_build_package_index(monkeypatch, configured, expected):
    monkeypatch.setenv("AGENTOS_PYPI_INDEX_URL", configured)
    services = json.loads(docker("compose", "config", "--format", "json"))["services"]
    assert services["migrate"]["build"]["args"]["PIP_INDEX_URL"] == expected
    exposed = [name for name, config in services.items() if "PIP_INDEX_URL" in config.get("environment", {})]
    assert exposed == []


def test_ac010_ac028_compose_passes_release_overrides_and_scopes_model_key(monkeypatch):
    values = {
        "AGENTOS_MODEL_NAME": "pinned-example",
        "AGENTOS_MODEL_API_URL": "https://model.example/v1/chat/completions",
        "AGENTOS_MODEL_WINDOW_TOKENS": "16384",
        "AGENTOS_MODEL_MAX_OUTPUT_TOKENS": "1024",
        "AGENTOS_MODEL_SAFETY_MARGIN_TOKENS": "128",
        "AGENTOS_MODEL_INPUT_MICROS_PER_MILLION": "500000",
        "AGENTOS_MODEL_OUTPUT_MICROS_PER_MILLION": "2000000",
        "AGENTOS_RAG_TOP_K": "3",
        "AGENTOS_RAG_MIN_SCORE": "0.25",
        "AGENTOS_RAG_TITLE_WEIGHT": "3",
        "AGENTOS_RAG_BODY_WEIGHT": "0.5",
    }
    for key in ("MYSQL_ROOT_PASSWORD", "AGENTOS_MIGRATION_PASSWORD",
                "AGENTOS_MYSQL_PASSWORD", "AGENTOS_MODEL_API_KEY"):
        monkeypatch.setenv(key, "fixture-only")
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    services = json.loads(docker("compose", "config", "--format", "json"))["services"]
    for name in ("api", "worker"):
        environment = services[name]["environment"]
        assert {key: environment.get(key) for key in values} == values
    assert services["worker"]["environment"]["AGENTOS_MODEL_API_KEY"] == "fixture-only"
    key_recipients = [name for name, service in services.items()
                      if "AGENTOS_MODEL_API_KEY" in service.get("environment", {})]
    assert key_recipients == ["worker"]


@pytest.mark.parametrize(("configured", "expected"), [("", "18080"), ("18081", "18081")])
def test_ac013_ac028_monitor_overlay_publishes_only_tls_and_keeps_api_private(monkeypatch, configured, expected):
    for key in ("MYSQL_ROOT_PASSWORD", "AGENTOS_MIGRATION_PASSWORD",
                "AGENTOS_MYSQL_PASSWORD", "AGENTOS_MODEL_API_KEY"):
        monkeypatch.setenv(key, "fixture-only")
    monkeypatch.setenv("AGENTOS_PUBLIC_IP", "192.0.2.10")
    monkeypatch.setenv("AGENTOS_PRIVATE_API_PORT", configured)
    # The overlay must replace, rather than merge with, the base API publication.
    monkeypatch.setenv("AGENTOS_API_PORT", "19999")
    services = json.loads(docker("compose", "-f", "compose.yaml", "-f", "compose.monitor.yaml",
                                 "config", "--format", "json"))["services"]
    published = {
        (name, port.get("host_ip") or "0.0.0.0", str(port["published"]), port["target"], port["protocol"])
        for name, service in services.items() for port in service.get("ports", [])
    }
    assert published == {
        ("api", "127.0.0.1", expected, 8000, "tcp"),
        ("monitor-gateway", "0.0.0.0", "8000", 8000, "tcp"),
        ("monitor-gateway", "0.0.0.0", "443", 443, "tcp"),
    }


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
    with urlopen(f"http://{address}/", timeout=8) as reply:
        assert reply.status == 200 and reply.geturl() == f"http://{address}/console"
        assert "img-src 'self'" in reply.headers["Content-Security-Policy"]
    with urlopen(f"http://{address}/console/assets/architecture.webp", timeout=8) as reply:
        assert reply.status == 200 and reply.headers.get_content_type() == "image/webp"
        assert reply.read(12)[8:12] == b"WEBP"
