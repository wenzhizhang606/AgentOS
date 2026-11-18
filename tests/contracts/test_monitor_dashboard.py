"""AC-013. The monitor is a same-origin, read-only client of authorized summaries."""

from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from agentos.api.app import TaskStore, create_app
from agentos.governance.auth import Authenticator


class DashboardMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id = {}
        self.asset_urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.by_id[attributes["id"]] = attributes
        for key in ("src", "href", "action"):
            if attributes.get(key):
                self.asset_urls.append(attributes[key])


@pytest.fixture
def monitor_page():
    store = Mock(spec=TaskStore)
    with TestClient(create_app(store, Authenticator({}), "pinned")) as client:
        response = client.get("/monitor")
    assert not store.mock_calls
    return response


def test_ac013_monitor_shell_contains_no_data_or_remote_assets(monitor_page):
    assert monitor_page.status_code == 200
    assert monitor_page.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in monitor_page.headers["content-security-policy"]
    assert monitor_page.headers["referrer-policy"] == "no-referrer"
    markup = DashboardMarkup()
    markup.feed(monitor_page.text)
    assert not markup.asset_urls
    assert markup.by_id["token"]["type"] == "password"
    assert "value" not in markup.by_id["token"]
    assert "hidden" in markup.by_id["report"]
    assert {"metrics", "registered", "capabilities", "tasks", "timeline"} <= markup.by_id.keys()


def test_ac013_browser_drilldown_revocation_and_credential_lifetime(monitor_page):
    """Optional browser case: install playwright and its Chromium browser before running."""
    playwright = pytest.importorskip("playwright.sync_api")
    origin = "http://monitor.invalid"
    tenant_project = "returns"
    task_id = "task01"
    token = "opaque-test-credential"
    capability = {"name": "lookup<em>source</em>", "kind": "tool", "digest": "d1", "version": "v1"}
    overview = {
        "scope": "subject", "project_id": tenant_project, "window_start": "2026-11-17T10:00:00Z",
        "window_end": "2026-11-18T10:00:00Z", "active_release": {
            "version_id": "pinned", "capabilities": [capability, dict(capability, name="unused")]},
        "summary": {"tasks": 1, "queued": 0, "running": 0, "succeeded": 0, "failed": 1,
                    "cancelled": 0, "timed_out": 0, "tasks_with_spans": 1, "dispatches": 1,
                    "usage_receipts": 0, "pending_receipts": 1, "input_tokens": "0", "output_tokens": "0",
                    "settled_micros": "0", "reserved_micros": "9007199254740993"},
        "capabilities": [dict(capability, calls=1, succeeded=0, failed=1, unfinished=0,
                              avg_duration_ms=12, max_duration_ms=12)], "capabilities_truncated": False,
        "recent_tasks": [{"task_id": task_id, "created_at": "2026-11-18T09:55:00Z", "state": "failed",
                          "accounting_state": "pending", "error_code": "EXTERNAL_OUTCOME_UNKNOWN"}],
    }
    timeline = {"task_id": task_id, "state": "failed", "agent_version_id": "pinned", "truncated": False,
                "events": [dict(capability, occurred_at="2026-11-18T09:55:00Z", event="failed",
                                attempt=1, duration_ms=12, error_code="LOOKUP_FAILED", span_id="span-1")]}
    summary_path = "/v1/projects/returns/monitoring"
    timeline_path = "/v1/tasks/task01/timeline"
    seen, unexpected = [], []
    revoke = False

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "monitor.invalid" or request.method != "GET":
            unexpected.append(request.url)
            route.abort()
        elif url.path == "/monitor":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=monitor_page.content,
                          headers={"Content-Security-Policy": monitor_page.headers["content-security-policy"]})
        elif url.path in {summary_path, timeline_path}:
            seen.append((url.path, request.headers.get("authorization"), url.query))
            if revoke:
                route.fulfill(status=403, json={"error": {"code": "FORBIDDEN"}})
            else:
                route.fulfill(status=200, json=overview if url.path == summary_path else timeline)
        elif url.path == "/favicon.ico":
            route.fulfill(status=204)
        else:
            unexpected.append(request.url)
            route.abort()

    with playwright.sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Optional browser check requires python -m playwright install chromium")
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.route("**/*", serve)
            page.goto(origin + "/monitor")
            assert not seen and not unexpected
            page.get_by_label("项目 ID", exact=True).fill(tenant_project)
            page.get_by_label("访问令牌", exact=True).fill(token)
            page.get_by_role("button", name="载入项目").click()
            page.locator("#report").wait_for()
            assert "当前主体可见任务" in page.locator("#scope").inner_text()
            assert "¥9007199254.740993" in page.locator("#metrics").inner_text()
            assert "unused" in page.locator("#registered").inner_text()
            assert "unused" not in page.locator("#capabilities").inner_text()
            assert not page.locator("#report em").count()
            assert page.locator("#token").input_value() == ""
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0

            page.get_by_role("button", name=task_id).click()
            page.locator("#timeline").get_by_text("LOOKUP_FAILED").wait_for()
            assert "span-1" in page.locator("#timeline").inner_text()
            revoke = True
            page.get_by_role("button", name="刷新", exact=True).click()
            page.get_by_text("读取失败（HTTP 403）").wait_for()
            assert page.locator("#report").is_hidden()
            assert page.locator("#tasks").inner_text() == page.locator("#timeline").inner_text() == ""
            assert page.get_by_role("button", name="载入项目").is_visible()
            assert [path for path, _, _ in seen] == [summary_path, timeline_path, summary_path]
            assert all(header == "Bearer " + token and token not in query for _, header, query in seen)
            assert not unexpected
        finally:
            browser.close()
