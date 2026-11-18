"""AC-013. The monitor is a same-origin, read-only client of authorized summaries."""

from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

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
    assert all("hidden" in markup.by_id[panel] for panel in ("actions", "report", "views", "explorer", "run-detail"))
    assert "hidden" not in markup.by_id["connect-panel"]
    assert markup.by_id["overview-view"]["aria-controls"] == "report"
    assert markup.by_id["runs-view"]["aria-controls"] == "explorer"
    assert {"metrics", "registered", "capabilities", "tasks", "timeline"} <= markup.by_id.keys()
    assert {"agent-version", "instances", "run-id", "run-facts", "trace", "events-next", "events-prev"} <= markup.by_id.keys()


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
                    "skill_calls": "17", "tool_calls": "34",
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
            assert page.get_by_role("heading", level=1, name="项目概览").is_visible()
            assert page.locator("#connection-state").inner_text() == "已连接"
            assert page.locator("#connect-panel").is_hidden()
            assert "当前主体可见任务" in page.locator("#scope").inner_text()
            assert "¥9007199254.740993" in page.locator("#metrics").inner_text()
            metrics = page.locator("#metrics > div")
            assert metrics.filter(has_text="Skill 调用总数").locator("dd").inner_text() == "17"
            assert metrics.filter(has_text="Tool 调用总数").locator("dd").inner_text() == "34"
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
            assert page.locator("#connected-project").inner_text() == ""
            assert page.locator("#connection-state").inner_text() == "未连接"
            assert [path for path, _, _ in seen] == [summary_path, timeline_path, summary_path]
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
    overview = dict(window, summary={"tasks": 1}, capabilities=[], recent_tasks=[],
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
            events.append(dict(kind="context", event="succeeded", occurred_at=started, span_id="span-2",
                               snapshot_id="snapshot-2", attempt=attempt, duration_ms=0.5))
            events.append(dict(kind="model", event="receipt_settled", occurred_at=started, attempt=attempt,
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
                                           attempt=2, occurred_at=window["window_start"])]
                else:
                    data.update(truncated=True, next_before="previous-boundary")
        else:
            unexpected.append(request.url)
            route.abort()
            return
        route.fulfill(status=200, json=data)

    with playwright.sync_playwright() as p:
        if not Path(p.chromium.executable_path).is_file():
            pytest.skip("Optional browser check requires python -m playwright install chromium")
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", serve)
            page.goto("http://monitor.invalid/monitor")
            page.get_by_label("项目 ID", exact=True).fill("returns")
            page.get_by_label("访问令牌", exact=True).fill(token)
            page.get_by_role("button", name="载入项目").click()
            page.locator("#report").wait_for()
            page.get_by_role("button", name="Agent 运行轨迹").click()
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
            facts = page.locator("#run-facts")
            assert "9007199254740993 / 12" in facts.inner_text() and "12.335 ms" in facts.inner_text()
            assert "task01:2" in page.locator("#trace-id").inner_text()
            page.get_by_role("button", name="更早事件").click()
            events.get_by_text("earlier-only").wait_for()
            assert "receipt-2" not in events.inner_text()
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
            page.get_by_text("读取失败（HTTP 403）").wait_for()
            assert [path for path, _, _ in seen[previous_calls:]] == [
                "/v1/tasks/task01/runs/2/trace", base + "/agents"]
            assert page.locator("#views").is_hidden() and page.locator("#connect").is_visible()
            assert run_id.input_value() == "" and events.inner_text() == facts.inner_text() == ""
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
