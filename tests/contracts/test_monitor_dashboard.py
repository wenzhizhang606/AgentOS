"""AC-013. The monitor is a same-origin, read-only client of authorized summaries."""

from html.parser import HTMLParser
from importlib.resources import files
import os
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from agentos.api.app import TaskStore, create_app
from agentos.governance.auth import Authenticator


HOME_PHOTO = "/console/assets/architecture.webp"


def route_dashboard(page, serve):
    """Serve the packaged public photograph alongside the case-specific API fixtures."""
    def photo(route):
        assert route.request.method == "GET"
        assert "authorization" not in route.request.headers
        image = files("agentos.observability").joinpath("architecture.webp").read_bytes()
        route.fulfill(content_type="image/webp", body=image)

    page.route("**/*", serve)
    page.route("http://monitor.invalid" + HOME_PHOTO, photo)


def launch_chromium(playwright):
    executable = os.environ.get("AGENTOS_TEST_CHROMIUM_EXECUTABLE")
    if executable and not Path(executable).is_file():
        pytest.fail("Configured Chromium executable does not exist")
    if not executable and not Path(playwright.chromium.executable_path).is_file():
        pytest.skip("Install Chromium with Playwright or set AGENTOS_TEST_CHROMIUM_EXECUTABLE")
    return playwright.chromium.launch(executable_path=executable)


class DashboardMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id = {}
        self.asset_urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            assert attributes["id"] not in self.by_id, f"Duplicate control ID: {attributes['id']}"
            self.by_id[attributes["id"]] = attributes
        for key in ("src", "href", "action"):
            if attributes.get(key):
                self.asset_urls.append((tag, key, attributes[key]))


@pytest.fixture
def monitor_page():
    store = Mock(spec=TaskStore)
    with TestClient(create_app(store, Authenticator({}), "pinned")) as client:
        response = client.get("/console")
    assert not store.mock_calls
    return response


def test_ac013_monitor_shell_contains_no_data_or_remote_assets(monitor_page):
    assert monitor_page.status_code == 200
    assert monitor_page.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in monitor_page.headers["content-security-policy"]
    assert monitor_page.headers["referrer-policy"] == "no-referrer"
    assert "<!-- console styles -->" not in monitor_page.text
    assert "<!-- console scripts -->" not in monitor_page.text
    markup = DashboardMarkup()
    markup.feed(monitor_page.text)
    allowed = {("img", "src", HOME_PHOTO)}
    allowed.update(("a", "href", path) for path in (
        "/console", "/console#login", "/console#overview", "/console#runs", "/console#resources"))
    assert ("img", "src", HOME_PHOTO) in markup.asset_urls
    assert set(markup.asset_urls) <= allowed
    assert markup.by_id["home-entry"]["href"] == "/console#login"
    assert markup.by_id["login-home"]["href"] == "/console"
    assert markup.by_id["token"]["type"] == "password"
    assert "value" not in markup.by_id["token"]
    assert all("hidden" in markup.by_id[panel] for panel in (
        "actions", "report", "views", "explorer", "run-detail", "project-chip", "disconnect", "connect-panel"))
    assert "hidden" not in markup.by_id["home-panel"]
    assert markup.by_id["connect-status"]["role"] == "status"
    assert markup.by_id["overview-view"]["aria-controls"] == "report"
    assert markup.by_id["runs-view"]["aria-controls"] == "explorer"
    assert {"metrics", "registered", "capabilities", "tasks", "timeline"} <= markup.by_id.keys()
    assert {"status-chart", "call-chart", "waterfall"} <= markup.by_id.keys()
    assert {"agent-version", "instances", "run-id", "run-facts", "trace", "events-next", "events-prev"} <= markup.by_id.keys()


def test_ac013_browser_public_home_login_and_project_history(monitor_page):
    """Public navigation needs no token; leaving login cancels even a late successful read."""
    playwright = pytest.importorskip("playwright.sync_api")
    origin = "http://monitor.invalid"
    overview_path = "/v1/projects/returns/monitoring"
    summary = dict(project_id="returns", scope="project", window_start="2026-11-17T00:00:00Z",
                   window_end="2026-11-18T00:00:00Z", summary={"tasks": 0}, recent_tasks=[],
                   capabilities=[], active_release={"version_id": "current", "capabilities": []})
    reads, documents, unexpected = [], [], []
    revoked = False

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc == "monitor.invalid" and request.method == "GET" and url.path == "/console":
            documents.append(request.url)
            route.fulfill(body=monitor_page.content, content_type="text/html; charset=utf-8",
                          headers={"Content-Security-Policy": monitor_page.headers["content-security-policy"]})
        elif url.netloc == "monitor.invalid" and request.method == "GET" and url.path == overview_path:
            reads.append(request.headers.get("authorization"))
            if revoked:
                route.fulfill(status=403, json={"error": {"code": "FORBIDDEN"}})
            else:
                route.fulfill(json=summary)
        elif request.url == origin + "/favicon.ico":
            route.fulfill(status=204)
        else:
            unexpected.append(request.url)
            route.abort()

    with playwright.sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            route_dashboard(page, serve)
            page.goto(origin + "/console")
            home, form = page.locator("#home-panel"), page.locator("#connect")
            entry = home.locator("#home-entry")
            assert home.is_visible() and form.is_hidden() and not reads
            photo = home.locator("img[src='" + HOME_PHOTO + "']")
            assert photo.is_visible() and photo.evaluate("img => img.complete && img.naturalWidth > 0")
            for width in (320, 1280):
                page.set_viewport_size({"width": width, "height": 800})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert entry.is_visible()
            entry.focus()
            page.keyboard.press("Enter")
            form.wait_for()
            assert page.url == origin + "/console#login" and home.is_hidden()
            page.go_back()
            home.wait_for()
            page.go_forward()
            form.wait_for()
            assert not reads

            # Delay the return of an actual, already-authorized response beyond a visit to home.
            page.evaluate("""() => {
                const fetch = window.fetch.bind(window);
                let firstReadArrived;
                window.firstReadArrived = new Promise(resolve => { firstReadArrived = resolve; });
                window.fetch = async (...args) => {
                    const response = await fetch(...args);
                    window.fetch = fetch;
                    const saved = new Response(await response.text(), {status: response.status});
                    await new Promise(resolve => { window.releaseFirstRead = resolve; firstReadArrived(); });
                    return saved;
                };
            }""")
            form.get_by_label("项目 ID", exact=True).fill("returns")
            token = form.get_by_label("访问令牌", exact=True)
            token.fill("fixture-observer")
            form.locator("button[type=submit]").click()
            page.evaluate("() => window.firstReadArrived")
            page.locator("#login-home").click()
            home.wait_for()
            page.evaluate("""async () => {
                window.releaseFirstRead();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""")
            assert home.is_visible() and page.locator("#report").is_hidden()
            assert page.locator("#connected-project").text_content() == "" and token.input_value() == ""

            entry.click()
            form.get_by_label("项目 ID", exact=True).fill("returns")
            token.fill("fixture-observer")
            form.locator("button[type=submit]").click()
            page.locator("#report").wait_for()
            assert page.url == origin + "/console#overview"
            page.get_by_role("link", name="AgentOS 主页", exact=True).click()
            home.wait_for()
            assert page.locator("#report").is_hidden()
            entry.click()
            page.locator("#report").wait_for()
            assert page.url == origin + "/console#overview" and form.is_hidden()
            assert page.locator("#connected-project").inner_text() == "returns"
            page.get_by_role("link", name="AgentOS 主页", exact=True).click()
            home.wait_for()
            revoked = True
            entry.click()
            form.locator("#connect-status").filter(has_text="HTTP 403").wait_for()
            assert token.input_value() == "" and page.locator("#report").is_hidden()
            assert reads == ["Bearer fixture-observer"] * 4
            assert len(documents) == 1 and not unexpected
        finally:
            browser.close()


def test_ac013_browser_project_login_waits_for_permission_and_recovers(monitor_page):
    """A bookmarked runs view stays on the form until its own project read succeeds."""
    playwright = pytest.importorskip("playwright.sync_api")
    origin = "http://monitor.invalid"
    agents_path = "/v1/projects/returns/agents"
    token = "fixture-observer"
    catalog = dict(project_id="returns", scope="project", agents=[], next_cursor=None,
                   window_start="2026-11-17T00:00:00Z", window_end="2026-11-18T00:00:00Z")
    pending, headers, unexpected = [], [], []

    def serve(route):
        url = urlsplit(route.request.url)
        if url.netloc == "monitor.invalid" and url.path == "/console":
            route.fulfill(content_type="text/html; charset=utf-8", body=monitor_page.content,
                          headers={"Content-Security-Policy": monitor_page.headers["content-security-policy"]})
        elif url.netloc == "monitor.invalid" and url.path == agents_path and route.request.method == "GET":
            headers.append(route.request.headers.get("authorization"))
            pending.append(route)
            route.request.frame.evaluate("count => { window.heldRequests = count; }", len(headers))
        elif url.netloc == "monitor.invalid" and url.path == "/favicon.ico":
            route.fulfill(status=204)
        else:
            unexpected.append(route.request.url)
            route.abort()

    with playwright.sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page(viewport={"width": 320, "height": 760})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            route_dashboard(page, serve)
            page.goto(origin + "/console#runs")
            form = page.locator("#connect")
            project = form.get_by_label("项目 ID", exact=True)
            credential = form.get_by_label("访问令牌", exact=True)
            submit = form.locator("button[type=submit]")
            message = form.locator("#connect-status")
            assert form.is_visible() and not headers
            assert page.get_by_text("未连接", exact=True).count() == 0
            project.fill("returns")
            credential.fill(token)

            # Hold the real fetch at the HTTP boundary so the pending form can be inspected.
            for attempt, outcome in enumerate((401, 403, 503, "offline", 200), start=1):
                with page.expect_request(lambda request: urlsplit(request.url).path == agents_path):
                    submit.click()
                page.wait_for_function("expected => window.heldRequests === expected", arg=attempt)
                message.filter(has_text="正在验证项目访问权限").wait_for()
                assert submit.is_disabled() and form.is_visible()
                for selector in ("#views", "#disconnect", "#project-chip", "#page-title", "#explorer"):
                    assert page.locator(selector).is_hidden()
                assert page.locator("#connected-project").text_content() == ""
                if attempt == 1:
                    form.evaluate("form => form.requestSubmit()")
                assert len(pending) == 1
                route = pending.pop()
                if outcome == 200:
                    route.fulfill(json=catalog)
                    break
                if outcome == "offline":
                    route.abort("failed")
                    message.filter(has_text="重试").wait_for()
                else:
                    route.fulfill(status=outcome, json={"error": {"code": "FIXTURE"}})
                    message.filter(has_text=f"HTTP {outcome}").wait_for()
                assert project.input_value() == "returns" and submit.is_enabled()
                if outcome in (401, 403):
                    assert credential.input_value() == ""
                    assert credential.evaluate("el => el === document.activeElement")
                    credential.fill(token)
                else:
                    assert credential.input_value() == token

            page.locator("#status").filter(has_text="更新于").wait_for()
            assert page.locator("#connected-project").inner_text() == "returns"
            assert page.locator("#explorer").is_visible() and page.locator("#views").is_visible()
            assert page.locator("#page-title").evaluate("el => el === document.activeElement")
            assert form.is_hidden() and credential.input_value() == ""
            assert headers == ["Bearer " + token] * 5

            # Simulate a response already received when the user signs out.
            page.evaluate("""() => {
                const fetch = window.fetch.bind(window);
                window.fetch = async (...args) => {
                    const response = await fetch(...args);
                    const saved = new Response(await response.text(), {status: response.status});
                    await new Promise(resolve => { window.finishRefresh = resolve; });
                    return saved;
                };
            }""")
            with page.expect_request(lambda request: urlsplit(request.url).path == agents_path):
                page.locator("#refresh").click()
            page.wait_for_function("window.heldRequests === 6")
            pending.pop().fulfill(json=catalog)
            page.wait_for_function("typeof window.finishRefresh === 'function'")
            page.locator("#disconnect").click()
            page.evaluate("""async () => {
                window.finishRefresh();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""")
            assert page.locator("#home-panel").is_visible() and form.is_hidden()
            assert page.locator("#explorer").is_hidden() and page.url == origin + "/console"
            assert page.locator("#agent-scope").text_content() == page.locator("#connected-project").text_content() == ""
            assert not unexpected and not errors
        finally:
            browser.close()


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
        "summary": {"tasks": 4, "queued": 0, "running": 0, "succeeded": 3, "failed": 1,
                    "cancelled": 0, "timed_out": 0, "tasks_with_spans": 1, "dispatches": 1,
                    "skill_calls": "17", "tool_calls": "34",
                    "usage_receipts": 0, "pending_receipts": 1, "input_tokens": "9007199254740993", "output_tokens": "0",
                    "settled_micros": "0", "reserved_micros": "9007199254740993"},
        "capabilities": [dict(capability, calls=1, succeeded=0, failed=1, unfinished=0,
                              avg_duration_ms=12, max_duration_ms=12)], "capabilities_truncated": False,
        "recent_tasks": [{"task_id": task_id, "created_at": "2026-11-18T09:55:00Z", "state": "failed",
                          "accounting_state": "pending", "error_code": "EXTERNAL_OUTCOME_UNKNOWN"}],
    }
    timeline = {"task_id": task_id, "project_id": tenant_project, "state": "failed", "agent_version_id": "pinned", "truncated": False,
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
        elif url.path == "/console":
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
        browser = launch_chromium(p)
        page = browser.new_page(viewport={"width": 320, "height": 760})
        try:
            route_dashboard(page, serve)
            page.goto(origin + "/console")
            assert not seen and not unexpected
            fits_viewport = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            assert page.evaluate(fits_viewport)
            assert page.locator("#home-panel").is_visible()
            page.locator("#home-entry").click()
            page.get_by_label("项目 ID", exact=True).fill(tenant_project)
            page.get_by_label("访问令牌", exact=True).fill(token)
            page.get_by_role("button", name="载入项目").click()
            page.locator("#report").wait_for()
            assert page.url == origin + "/console#overview"
            assert page.get_by_role("heading", level=1, name="项目概览").is_visible()
            assert page.locator("#project-chip").is_visible()
            assert page.locator("#connected-project").inner_text() == tenant_project
            assert page.locator("#page-title").evaluate("el => el === document.activeElement")
            assert page.locator("#connect-panel").is_hidden()
            assert "当前主体可见任务" in page.locator("#scope").inner_text()
            page.get_by_label("显示更多运行指标").check()
            assert "¥9007199254.740993" in page.locator("#metrics").inner_text()
            metrics = page.locator("#metrics > div")
            assert metrics.filter(has_text="Skill 调用总数").locator("dd").inner_text() == "17"
            assert metrics.filter(has_text="Tool 调用总数").locator("dd").inner_text() == "34"
            assert metrics.filter(has_text="已知输入 Token").locator("dd").inner_text() == "9007199254740993"
            assert page.locator('#tasks [data-state="pending"]').inner_text() == "待结算"
            legend = page.locator("#status-chart .chart-legend")
            for state, count in {"succeeded": "3", "failed": "1", "queued": "0"}.items():
                assert legend.locator(f'li[data-state="{state}"] strong').inner_text() == count
            # Only one task is returned by the paginated table; charts use the full cohort.
            ring = page.locator("#status-chart circle.ring-segment")
            assert ring.count() == 2
            for state, ratio in {"succeeded": 75, "failed": 25}.items():
                segment = page.locator(f'#status-chart circle.ring-segment[data-state="{state}"]')
                assert float(segment.get_attribute("stroke-dasharray").split()[0]) == ratio
            calls = page.locator("#call-chart")
            for kind, count in {"tool": "34", "skill": "17", "model": "1"}.items():
                assert calls.locator(f'[data-kind="{kind}"] .call-count').inner_text() == count
            assert page.evaluate(fits_viewport)
            assert "unused" in page.locator("#registered").inner_text()
            assert "unused" not in page.locator("#capabilities").inner_text()
            assert not page.locator("#report em").count()
            assert page.locator("#token").input_value() == ""
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0

            page.get_by_role("button", name=task_id).focus()
            page.keyboard.press("Enter")
            page.locator("#timeline").get_by_text("LOOKUP_FAILED").wait_for()
            assert "span-1" in page.locator("#timeline").inner_text()
            timeline["project_id"] = "different-project"
            page.get_by_role("button", name=task_id, exact=True).click()
            page.locator("#detail-status").get_by_text("时间线读取失败。").wait_for()
            assert page.locator("#timeline").inner_text() == ""
            page.get_by_role("button", name="退出项目").click()
            for chart in ("status-chart", "call-chart"):
                assert page.locator("#" + chart).text_content() == ""
            # Reconnect to an empty cohort with a legacy report missing one of the counts.
            overview["summary"].update(tasks=0, failed=0, succeeded=0, tool_calls=0, dispatches=0)
            overview["summary"].pop("skill_calls")
            overview.update(recent_tasks=[], capabilities=[])
            page.locator("#home-entry").click()
            page.get_by_label("项目 ID", exact=True).fill(tenant_project)
            page.get_by_label("访问令牌", exact=True).fill(token)
            page.get_by_role("button", name="载入项目").click()
            page.locator("#report").wait_for()
            assert page.locator("#status-chart circle.ring-segment").count() == 0
            assert calls.locator('[data-kind="tool"] .call-count').inner_text() == "0"
            assert calls.locator('[data-kind="skill"] .call-count').inner_text() == "—"
            empty_tasks = page.locator("#tasks").get_by_role("cell", name="该时间范围内没有可见任务。")
            assert empty_tasks.get_attribute("colspan") == "5"
            assert not empty_tasks.locator("svg:not([aria-hidden=true])").count()
            revoke = True
            page.get_by_role("button", name="刷新", exact=True).click()
            page.locator("#connect-status").filter(has_text="HTTP 403").wait_for()
            assert page.locator("#report").is_hidden()
            assert page.locator("#tasks").inner_text() == page.locator("#timeline").inner_text() == ""
            assert page.locator("#status-chart").text_content() == calls.text_content() == ""
            assert page.get_by_role("button", name="载入项目").is_visible()
            assert page.locator("#connected-project").inner_text() == ""
            assert page.locator("#project-chip").is_hidden() and page.locator("#disconnect").is_hidden()
            assert [path for path, _, _ in seen] == [summary_path, timeline_path, timeline_path, summary_path, summary_path]
            assert all(header == "Bearer " + token and token not in query for _, header, query in seen)
            assert not unexpected
        finally:
            browser.close()


def test_ac013_browser_agent_paging_attempts_history_scope_and_revocation(monitor_page):
    """Optional browser check using the same CSP and routes as the deployed gateway."""
    playwright = pytest.importorskip("playwright.sync_api")
    token = "run-reader"
    window = dict(project_id="returns", scope="subject", window_start="2026-11-17T10:00:00.000000Z",
                  window_end="2026-11-18T10:00:00.000000Z", next_cursor=None)
    overview = dict(window, summary={"tasks": 1}, capabilities=[],
                    recent_tasks=[dict(task_id="task01", state="succeeded", accounting_state="final",
                                       created_at=window["window_start"])],
                    active_release={"version_id": "current", "capabilities": []})
    base = "/v1/projects/returns"
    runs_path = base + "/agents/older/runs"
    seen, unexpected = [], []
    revoke = False

    def run(task, attempt):
        return dict(task_id=task, attempt=attempt, run_id=f"{task}:{attempt}", worker_id=None,
                    started_at=window["window_start"], state="superseded" if attempt == 1 else "succeeded")

    def trace(task, attempt):
        past = task == "archive"
        first = attempt == 1
        started = "2025-01-02T01:00:00.000012Z" if past else window["window_start"]
        events = [dict(kind="task", event="claimed", occurred_at=started, attempt=attempt)]
        if not first:
            events.append(dict(kind="context", event="started", name="assemble", span_id="span-2",
                               occurred_at="2026-11-17T10:00:00.001200Z", attempt=attempt))
            events.append(dict(kind="context", event="succeeded", name="assemble", span_id="span-2",
                               occurred_at="2026-11-17T10:00:00.001700Z", snapshot_id="snapshot-2",
                               attempt=attempt, duration_ms=0.5))
            events.append(dict(kind="model", event="receipt_settled", occurred_at="2026-11-17T10:00:00.009800Z", attempt=attempt,
                               dispatch_attempt_id="dispatch-2", receipt_ref="receipt-2", actual_micros="7"))
        return dict(run(task, attempt), project_id="other" if task == "foreign" else "returns",
                    agent_version_id="different" if task == "wrong-version" else "older", profile="support",
                    started_at=started, ended_at=None if first else "2026-11-17T10:00:00.012335Z",
                    events=events, has_capability_spans=not first, truncated=False, error_code=None,
                    accounting=dict(state="no_dispatch" if first else "final", settled_micros="0" if first else "7",
                                    reserved_micros="0", input_tokens=None if first else "9007199254740993",
                                    output_tokens=None if first else "12"))

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "monitor.invalid" or request.method != "GET":
            unexpected.append(request.url)
            route.abort()
            return
        if url.path == "/monitor":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=monitor_page.content,
                          headers={"Content-Security-Policy": monitor_page.headers["content-security-policy"]})
            return
        if url.path == "/favicon.ico":
            route.fulfill(status=204)
            return
        query = parse_qs(url.query)
        seen.append((url.path, query, request.headers.get("authorization")))
        if url.path == base + "/monitoring":
            data = overview
        elif url.path == "/v1/tasks/task01/timeline":
            data = dict(task_id="task01", project_id="returns", state="succeeded", agent_version_id="older",
                        events=[dict(kind="task", event=event, attempt=attempt, occurred_at=window["window_start"])
                                for event, attempt in (("queued", 0), ("claimed", 1), ("claimed", 2))])
        elif url.path == base + "/agents":
            if revoke:
                route.fulfill(status=403, json={"error": {"code": "FORBIDDEN"}})
                return
            # The second page is empty, as can happen if earlier claims have been removed.
            data = dict(window, agents=[], next_cursor=None)
            if not query.get("cursor"):
                data.update(agents=[dict(agent_version_id="older", profile="support", run_count=2,
                                         last_started_at=window["window_start"])], next_cursor="agent-next")
        elif url.path == runs_path:
            later_page = query.get("cursor") == ["runs-next"]
            data = dict(window, agent_version_id="older", runs=[run("task01", 1 if later_page else 2)],
                        next_cursor=None if later_page else "runs-next")
        elif url.path.startswith("/v1/tasks/") and url.path.endswith("/trace"):
            _, _, _, task, _, attempt, _ = url.path.split("/")
            if revoke or task == "missing":
                route.fulfill(status=404, json={"error": {"code": "RESOURCE_NOT_FOUND"}})
                return
            data = trace(task, int(attempt))
            if task == "task01" and attempt == "2":
                if query.get("before") == ["previous-boundary"]:
                    data["events"] = [dict(kind="tool", event="started", name="earlier-only",
                                           span_id="early-span", attempt=2, occurred_at=window["window_start"])]
                else:
                    data.update(truncated=True, next_before="previous-boundary")
        else:
            unexpected.append(request.url)
            route.abort()
            return
        route.fulfill(status=200, json=data)

    with playwright.sync_playwright() as p:
        browser = launch_chromium(p)
        try:
            page = browser.new_page()
            route_dashboard(page, serve)
            page.goto("http://monitor.invalid/monitor")
            page.get_by_label("项目 ID", exact=True).fill("returns")
            page.get_by_label("访问令牌", exact=True).fill(token)
            page.get_by_role("button", name="载入项目").click()
            page.locator("#report").wait_for()
            page.get_by_role("button", name="task01", exact=True).click()
            task_links = page.locator("#timeline").get_by_role("button")
            task_links.last.wait_for()
            assert task_links.count() == 2
            page.get_by_role("button", name="查看任务 task01 第 2 次运行轨迹").click()
            page.locator("#trace-id").get_by_text("task01:2", exact=True).wait_for()
            assert page.locator("#trace-heading").evaluate("el => el === document.activeElement")
            page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: async value => { window.copiedRun = value; }}})""")
            page.get_by_role("button", name="复制运行 ID", exact=True).press("Enter")
            assert page.evaluate("window.copiedRun") == "task01:2"
            assert page.locator("#trace-id").get_by_role("status").inner_text() == "已复制"
            assert page.url == "http://monitor.invalid/console#runs"
            assert page.get_by_role("heading", level=1, name="Agent 运行轨迹").is_visible()
            assert page.locator("#runs-view").get_attribute("aria-pressed") == "true"
            page.get_by_role("button", name="下一页版本").click()
            page.get_by_text("这个时间范围内没有可见运行。").wait_for()
            page.get_by_role("button", name="上一页版本").click()
            chooser = page.get_by_label("Agent 发布版本")
            chooser.select_option(label="support · older · 2 次运行")
            page.get_by_role("button", name="查看任务 task01 第 2 次运行").click()
            events = page.locator("#trace")
            events.get_by_text("回执摘要 receipt-2").wait_for()
            assert "snapshot-2" in events.inner_text() and "span-2" in events.inner_text()
            assert "¥0.000007" in events.inner_text()
            waterfall = page.locator("#waterfall")
            context_rows = waterfall.locator('.waterfall-row[data-kind="context"]')
            assert context_rows.count() == 1
            assert context_rows.locator(".waterfall-value").inner_text() == "0.5 ms"
            interval = context_rows.locator(".waterfall-track line")
            for endpoint, offset_ms in {"x1": 1.2, "x2": 1.7}.items():
                percent = float(interval.get_attribute(endpoint).removesuffix("%"))
                assert percent == pytest.approx(100 * offset_ms / 12.335)
            assert waterfall.locator('[data-kind="model"] .waterfall-value').inner_text() == "本页仅结算"
            facts = page.locator("#run-facts")
            assert "9007199254740993 / 12" in facts.inner_text() and "12.335 ms" in facts.inner_text()
            assert facts.locator('[data-state="final"]').inner_text() == "已结算"
            assert facts.locator("div").filter(has_text="Worker").locator("dd").inner_text() == "—"
            assert "task01:2" in page.locator("#trace-id").inner_text()
            page.get_by_role("button", name="更早事件").click()
            events.get_by_text("earlier-only").wait_for()
            assert "receipt-2" not in events.inner_text()
            assert waterfall.locator('.waterfall-row').count() == 1
            assert waterfall.locator('[data-kind="tool"] .waterfall-value').inner_text() == "本页仅开始"
            assert page.get_by_role("button", name="更早事件").is_disabled()
            with page.expect_response(lambda response: response.url.endswith("trace?before=previous-boundary")):
                page.get_by_role("button", name="刷新", exact=True).click()
            events.get_by_text("earlier-only").wait_for()
            page.get_by_role("button", name="较新事件").click()
            events.get_by_text("回执摘要 receipt-2").wait_for()
            page.get_by_role("button", name="下一页实例").click()
            page.get_by_role("button", name="查看任务 task01 第 1 次运行").click()
            page.locator("#trace-note").get_by_text("本次运行没有可用的能力观测记录", exact=False).wait_for()
            assert "已被后续尝试接替" in facts.inner_text() and "无模型准入" in facts.inner_text()
            assert "receipt-2" not in events.inner_text()
            assert not waterfall.locator('[data-kind="context"], [data-kind="model"], [data-kind="tool"]').count()

            run_id = page.get_by_label("运行 ID", exact=True)
            find = page.get_by_role("button", name="按 ID 查看轨迹")
            # Delay a parsed response to exercise an older request completing after a new selection.
            page.evaluate("""() => {
                const underlying = window.fetch.bind(window);
                window.fetch = async (...args) => {
                    const response = await underlying(...args);
                    if (!String(args[0]).includes('/tasks/slow/')) return response;
                    const saved = new Response(await response.text(), {status: response.status});
                    await new Promise(resolve => { window.releaseSlowTrace = resolve; });
                    return saved;
                };
            }""")
            run_id.fill("slow:2")
            find.click()
            page.wait_for_function("typeof window.releaseSlowTrace === 'function'")
            run_id.fill("archive:1")
            find.click()
            page.locator("#trace-id").get_by_text("archive:1", exact=False).wait_for()
            page.evaluate("""async () => {
                window.releaseSlowTrace();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""")
            assert page.locator("#trace-id code").inner_text() == "archive:1"
            assert "span-2" not in events.inner_text()
            with page.expect_response(lambda response: response.url.endswith("/tasks/archive/runs/1/trace")):
                page.get_by_role("button", name="刷新", exact=True).click()
            assert chooser.input_value() == "older" and run_id.input_value() == "archive:1"
            assert "2025" in facts.inner_text()

            for invalid, message in [
                ("foreign:1", "这次运行不属于当前项目。"),
                ("wrong-version:2", "这次运行与所选发布版本不一致。请更改或清除版本选择。"),
                ("../bad:1", "请输入有效的运行 ID，格式为 任务 ID:尝试次数。"),
            ]:
                run_id.fill(invalid)
                find.click()
                page.get_by_text(message, exact=True).wait_for()
                assert page.locator("#run-detail").is_hidden() and not events.inner_text()
                assert waterfall.text_content() == ""
            previous_calls = len(seen)
            run_id.fill("missing:1")
            find.click()
            page.get_by_text("这次运行不存在或在当前权限下不可见。").wait_for()
            assert [path for path, _, _ in seen[previous_calls:]] == [
                "/v1/tasks/missing/runs/1/trace", base + "/agents", runs_path]
            assert chooser.input_value() == "older" and run_id.input_value() == "missing:1"
            assert page.locator("#run-detail").is_hidden()
            chooser.select_option(value="")
            run_id.fill("archive:1")
            find.click()
            page.locator("#trace-id").get_by_text("archive:1", exact=False).wait_for()
            chooser.select_option(value="older")
            run_button = page.get_by_role("button", name="查看任务 task01 第 2 次运行")
            run_button.wait_for()
            revoke = True
            previous_calls = len(seen)
            run_button.click()
            page.locator("#connect-status").filter(has_text="HTTP 403").wait_for()
            assert [path for path, _, _ in seen[previous_calls:]] == [
                "/v1/tasks/task01/runs/2/trace", base + "/agents"]
            assert page.locator("#views").is_hidden() and page.locator("#connect").is_visible()
            assert run_id.input_value() == "" and events.inner_text() == facts.inner_text() == ""
            assert waterfall.text_content() == ""
            assert not page.locator("#instances").inner_text() and not chooser.locator("option").count()
            assert all(header == "Bearer " + token for _, _, header in seen)
            assert (base + "/agents", {"hours": ["24"], "limit": ["25"], "cursor": ["agent-next"]},
                    "Bearer " + token) in seen
            assert (runs_path, {"hours": ["24"], "limit": ["25"], "cursor": ["runs-next"]},
                    "Bearer " + token) in seen
            trace_queries = [(path, query) for path, query, _ in seen if path.endswith("/trace") and query]
            assert trace_queries == [("/v1/tasks/task01/runs/2/trace", {"before": ["previous-boundary"]})] * 2
            assert not unexpected
        finally:
            browser.close()


def test_ac013_ac023_browser_project_resource_directory(monitor_page):
    playwright = pytest.importorskip("playwright.sync_api")
    origin, token = "http://monitor.invalid", "fixture-observer"
    resources_api = "/v1/projects/returns/resources"
    overview_api = "/v1/projects/returns/monitoring"
    catalog = {
        "project_id": "returns", "agent_version_id": "a" * 64, "counts": {"skill": 2, "tool": 1},
        "resources": [
            {"kind": "tool", "name": "lookup", "version": "1", "digest": "1" * 64},
            {"kind": "skill", "name": "unused-guidance", "version": "2", "digest": "2" * 64},
            {"kind": "skill", "name": "citations", "version": "4", "digest": "3" * 64},
        ],
    }
    overview = {
        "project_id": "returns", "scope": "project", "window_start": "2026-11-17T00:00:00Z",
        "window_end": "2026-11-18T00:00:00Z", "summary": {"tasks": 0, "skill_calls": 0, "tool_calls": 0},
        "recent_tasks": [], "active_release": {"version_id": "a" * 64, "capabilities": []}, "capabilities": [],
    }
    requests, pages, unexpected = [], [], []
    status = 200

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "monitor.invalid" or request.method != "GET" or token in request.url:
            unexpected.append(request.url)
            route.abort()
        elif url.path in ("/resources", "/monitor", "/console"):
            pages.append(url.path)
            route.fulfill(content_type="text/html; charset=utf-8", body=monitor_page.content,
                          headers={"Content-Security-Policy": monitor_page.headers["content-security-policy"]})
        elif url.path in (resources_api, overview_api):
            requests.append((url.path, url.query, request.headers.get("authorization")))
            if status != 200:
                route.fulfill(status=status, json={"error": {"code": "FIXTURE"}})
            else:
                route.fulfill(json=catalog if url.path == resources_api else overview)
        elif url.path == "/favicon.ico":
            route.fulfill(status=204)
        else:
            unexpected.append(request.url)
            route.abort()

    with playwright.sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(viewport={"width": 320, "height": 760})
        try:
            route_dashboard(page, serve)
            page.goto(origin + "/resources")
            assert page.url == origin + "/console#resources"
            assert page.locator("#connect-panel").is_visible()
            assert page.locator("#home-panel").is_hidden()
            assert not requests
            skill_count, tool_count = page.locator("#skill-inventory"), page.locator("#tool-inventory")
            table = page.locator("#catalog-table")
            search = page.get_by_role("searchbox", name="资源名称")
            reset = page.get_by_role("button", name="清除筛选")
            dialog = page.get_by_role("dialog", name="资源详情")
            detail_facts = page.locator("#resource-detail-facts")

            def login():
                page.get_by_label("项目 ID", exact=True).fill("returns")
                page.get_by_label("访问令牌", exact=True).fill(token)
                page.get_by_role("button", name="载入项目").click()
                page.locator("#resources").wait_for()

            login()
            assert requests == [(resources_api, "", "Bearer " + token)]
            assert page.locator("#hours").is_hidden()
            assert page.locator("#catalog-release").inner_text() == catalog["agent_version_id"]
            resource = table.get_by_role("button", name="unused-guidance")
            assert resource.is_visible()
            assert "2" * 64 not in table.text_content()
            preview = table.get_by_text("22222222…222222")
            assert preview.is_hidden()
            page.set_viewport_size({"width": 1280, "height": 760})
            assert preview.is_visible()
            page.set_viewport_size({"width": 320, "height": 760})
            resource.focus()
            page.keyboard.press("Enter")
            assert dialog.is_visible()
            assert dialog.locator("dd").all_text_contents()[:3] == ["unused-guidance", "Skill", "2"]
            assert dialog.get_by_text("2" * 64, exact=True).is_visible()
            assert dialog.get_by_role("button", name="关闭详情").evaluate("b => b === document.activeElement")
            assert dialog.evaluate("d => d.clientWidth >= d.scrollWidth && d.getBoundingClientRect().right <= innerWidth")
            page.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: async value => { window.copiedDigest = value; }}})""")
            copy = dialog.get_by_role("button", name="复制完整摘要", exact=True)
            copy.press("Enter")
            assert page.evaluate("window.copiedDigest") == "2" * 64
            page.evaluate("() => Object.defineProperty(navigator, 'clipboard', {value: undefined})")
            copy.press("Space")
            assert page.evaluate("window.getSelection().toString()") == "2" * 64
            assert dialog.get_by_role("status").inner_text() == "请手动复制"
            page.keyboard.press("Escape")
            assert dialog.is_hidden() and detail_facts.text_content() == ""
            assert resource.evaluate("b => b === document.activeElement")
            resource.click()
            dialog.get_by_role("button", name="关闭详情").click()
            assert resource.evaluate("b => b === document.activeElement")
            assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            for kind, expected in (("tool", ["lookup"]), ("skill", ["unused-guidance", "citations"])):
                page.get_by_label("资源类型").select_option(kind)
                assert table.locator(".resource-name").all_text_contents() == expected
                assert (skill_count.inner_text(), tool_count.inner_text()) == ("2", "1")
            search.fill(" GUIDance ")
            assert table.locator(".resource-name").all_text_contents() == ["unused-guidance"]
            page.get_by_label("资源类型").select_option("tool")
            assert table.get_by_text("没有匹配的资源。").is_visible()
            assert page.locator("#catalog-result").inner_text() == "显示 0 / 3 项资源"
            reset.click()
            assert reset.is_disabled() and search.evaluate("input => input === document.activeElement")
            assert table.locator(".resource-name").count() == 3
            assert len(requests) == 1

            page.locator("#overview-view").click()
            page.locator("#report").wait_for()
            assert page.url == origin + "/console#overview"
            assert table.text_content() == "" and not page.locator("#resources").is_visible()
            page.locator("#resources-view").click()
            table.get_by_text("unused-guidance").wait_for()
            assert page.url == origin + "/console#resources" and table.locator(".resource-name").count() == 3
            resource.click()
            page.go_back()
            page.locator("#report").wait_for()
            assert dialog.is_hidden() and detail_facts.text_content() == ""
            page.go_forward()
            table.get_by_text("unused-guidance").wait_for()
            assert pages == ["/resources"]
            assert [path for path, _, _ in requests] == [
                resources_api, overview_api, resources_api, overview_api, resources_api]

            page.clock.install()
            page.locator("#automatic").check()
            resource.click()
            with page.expect_response(lambda response: resources_api in response.url):
                page.clock.fast_forward(30_000)
            page.locator("#status").filter(has_text="更新于").wait_for()
            assert dialog.is_visible() and detail_facts.get_by_text("2" * 64).is_visible()
            catalog.update(resources=[], counts={"skill": 0, "tool": 0})
            page.clock.fast_forward(30_000)
            table.get_by_text("当前发布尚未启用 Skill 或 Tool。").wait_for()
            assert dialog.is_hidden() and detail_facts.text_content() == ""
            assert search.evaluate("input => input === document.activeElement")
            assert (skill_count.inner_text(), tool_count.inner_text()) == ("0", "0")
            catalog.update(resources=[{"kind": "tool", "name": "lookup", "version": "1", "digest": "1" * 64}],
                           counts={"skill": 0, "tool": 1})
            page.get_by_role("button", name="刷新", exact=True).click()
            table.get_by_text("lookup").wait_for()
            for status in (503, 401, 403):
                if status in (503, 403):
                    page.locator("#automatic").check()
                    table.get_by_role("button", name="lookup").click()
                    page.clock.fast_forward(30_000)
                else:
                    page.get_by_role("button", name="刷新", exact=True).click()
                message = page.locator("#connect-status" if status in (401, 403) else "#status")
                message.filter(has_text=f"HTTP {status}").wait_for()
                assert dialog.is_hidden() and detail_facts.text_content() == ""
                assert table.text_content() == skill_count.text_content() == tool_count.text_content() == ""
                assert page.locator("#catalog-release").text_content() == ""
                if status in (401, 403):
                    assert page.locator("#views").is_hidden() and page.locator("#connect").is_visible()
                    assert page.locator("#token").input_value() == page.locator("#connected-project").text_content() == ""
                else:
                    assert page.locator("#connected-project").inner_text() == "returns"
                    assert page.locator("#refresh").is_enabled() and page.locator("#connect").is_hidden()
                if status == 401:
                    status = 200
                    login()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert all(header == "Bearer " + token for _, _, header in requests)
            assert not unexpected
        finally:
            browser.close()
