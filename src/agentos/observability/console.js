"use strict";
(() => {
  const byId = id => document.getElementById(id);
  const {el, svgElement, cell, stateCell, empty, facts, rowAction, copyValue} = ConsoleUI;
  const stateNames = {queued:"排队中", running:"运行中", succeeded:"成功", failed:"失败", cancelled:"已取消", timed_out:"已超时", superseded:"已被后续尝试接替"};
  const accounting = {pending:"待结算", final:"已结算", no_dispatch:"无模型准入"};
  const eventNames = {queued:"进入队列", claimed:"领取任务", started:"开始", succeeded:"完成", failed:"失败",
    timed_out:"超时", cancelled:"取消", dispatch_admitted:"模型发送准入", receipt_settled:"费用结算"};
  const kinds = {task:"任务", context:"上下文", skill:"Skill", tool:"Tool", model:"模型"};
  let connection = null, selected = null, projectRead = null, taskRead = null, timer = null;
  const routes = new Set(["login","overview","runs","resources"]);
  function initialView() {
    const fragment = location.hash.slice(1);
    if (routes.has(fragment)) return fragment;
    if (location.pathname === "/resources") return "resources";
    if (location.pathname === "/monitor") return "overview";
    return "home";
  }
  const viewUrl = name => name === "home" ? "/console" : "/console#" + name;
  let view = initialView(), agent = "", instance = null;
  let inventory = [];
  function pageState() { return {previous:[], cursor:null, next:null}; }
  let agentPage = pageState(), runPage = pageState(), tracePage = pageState();
  const date = value => value ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : "—";
  const periodFormat = new Intl.DateTimeFormat("zh-CN", {year:"numeric", month:"2-digit", day:"2-digit",
    hour:"2-digit", minute:"2-digit", hourCycle:"h23"});
  const periodDate = value => value ? periodFormat.format(new Date(value)) : "—";
  const eventTime = value => value ? date(value) + (value.match(/\.\d+(?=Z$)/)?.[0] ?? "") : "—";
  function elapsedMs(start, end) {
    if (!start || !end) return null;
    // Date.parse retains milliseconds. The DB also records the next three fractional digits.
    const subMillis = value => Number((value.split(".")[1] || "").replace(/Z$/, "").padEnd(6, "0").slice(3, 6));
    return (1000 * (Date.parse(end) - Date.parse(start)) + subMillis(end) - subMillis(start)) / 1000;
  }
  const ms = value => value == null ? "—" : value + " ms";
  function money(micros) {
    const digits = String(micros ?? "");
    if (!/^\d+$/.test(digits)) return "—";
    const padded = digits.padStart(7, "0");
    return "¥" + padded.slice(0, -6) + "." + padded.slice(-6);
  }
  function schedule() {
    clearTimeout(timer);
    if (view !== "home" && connection?.verified && byId("automatic").checked && !document.hidden) {
      timer = setTimeout(refresh, 30000);
    }
  }
  function clearDetail() {
    taskRead?.abort(); taskRead = null; selected = null;
    byId("detail").hidden = true;
    for (const id of ["timeline","detail-meta","detail-status"]) byId(id).replaceChildren();
  }
  function clearProject() {
    clearDetail();
    clearInventory();
    resetExplorer();
    byId("report").hidden = true;
    for (const id of ["metrics","status-chart","call-chart","capabilities","tasks","scope","window","task-count","registered","release","cap-status"]) byId(id).replaceChildren();
  }
  function showConnection() {
    const home = view === "home";
    const verified = connection?.verified === true;
    const workspace = !home && verified;
    const checking = connection !== null && !verified;
    byId("home-panel").hidden = !home;
    byId("connect-panel").hidden = home || verified;
    document.querySelector(".page-header").hidden = !workspace;
    for (const id of ["actions","views","status"]) byId(id).hidden = !workspace;
    for (const id of ["project-chip","disconnect"]) byId(id).hidden = !verified;
    byId("connected-project").textContent = verified ? connection.project : "";
    byId("connect").querySelector("button[type=submit]").disabled = checking;
    byId("project").readOnly = checking;
    byId("token").readOnly = checking;
    setViewButtons();
  }
  function openProject() {
    if (connection.verified) return;
    connection.verified = true;
    if (view === "login") {
      view = "overview";
      history.replaceState(null, "", viewUrl(view));
    }
    byId("token").value = "";
    byId("connect-status").textContent = "";
    showConnection();
    byId("page-title").focus();
  }
  function disconnect({message = "", keepToken = false} = {}) {
    clearTimeout(timer);
    projectRead?.abort(); projectRead = null; connection = null;
    clearProject();
    showConnection();
    byId("automatic").checked = false;
    if (!keepToken) byId("token").value = "";
    byId("status").textContent = "";
    byId("connect-status").textContent = message;
    byId("connect-status").dataset.tone = message ? "error" : "";
  }
  function returnToLogin(error) {
    const denied = error.status === 401 || error.status === 403;
    if (connection?.verified && !denied) return false;
    let message;
    if (error.status === 401) message = "访问令牌无效或已过期（HTTP 401）。请输入有效令牌。";
    else if (error.status === 403) message = "没有此项目的观察权限（HTTP 403）。请核对项目 ID 和令牌权限。";
    else if (error.status === 404) message = "无法访问该项目（HTTP 404）。请核对项目 ID 与访问权限。";
    else message = "暂时无法载入项目" + (error.status ? "（HTTP " + error.status + "）" : "") + "。请重试。";
    disconnect({message, keepToken:!denied});
    if (denied) byId("token").focus();
    else byId("connect").querySelector("button[type=submit]").focus();
    return true;
  }
  function setViewButtons() {
    const resources = view === "resources";
    byId("overview-view").setAttribute("aria-pressed", String(view === "overview"));
    byId("runs-view").setAttribute("aria-pressed", String(view === "runs"));
    byId("resources-view").setAttribute("aria-pressed", String(resources));
    const name = resources ? "资源管理" : view === "runs" ? "Agent 运行轨迹" : "项目概览";
    byId("page-title").textContent = name;
    byId("hours").closest("label").hidden = resources;
    document.title = view === "home" ? "AgentOS 控制台" :
      (connection?.verified ? name : "登录项目") + " · AgentOS 控制台";
  }
  function clearTrace() {
    byId("run-detail").hidden = true;
    for (const id of ["trace-id","run-facts","trace","trace-note","waterfall"]) byId(id).replaceChildren();
  }
  function chooseRun(run) {
    instance = run;
    tracePage = pageState();
    for (const row of byId("instances").rows) row.classList.toggle("selected", row.dataset.run === run?.run_id);
    clearTrace();
  }
  function clearRuns() {
    byId("run-list").hidden = true;
    byId("instances").replaceChildren();
    byId("runs-release").replaceChildren();
    clearTrace();
  }
  function resetExplorer() {
    agentPage = pageState(); runPage = pageState(); tracePage = pageState(); agent = ""; instance = null;
    byId("run-id").value = "";
    byId("agent-version").replaceChildren();
    byId("agent-version").disabled = true;
    for (const id of ["agent-scope","agent-note"]) byId(id).replaceChildren();
    for (const id of ["agents-prev","agents-next","instances-prev","instances-next","events-prev","events-next"]) byId(id).disabled = true;
    byId("explorer").hidden = true;
    clearRuns();
  }
  async function read(path, signal) {
    const deadline = AbortSignal.any([signal, AbortSignal.timeout(30000)]);
    const response = await fetch(path, {method:"GET", cache:"no-store", redirect:"error",
      credentials:"omit", mode:"same-origin", referrerPolicy:"no-referrer", signal:deadline,
      headers:{"Accept":"application/json","Authorization":"Bearer " + connection.token}});
    if (!response.ok) {
      const error = new Error("读取失败（HTTP " + response.status + "）");
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function countOf(value) {
    return /^\d+$/.test(String(value)) ? BigInt(value) : null;
  }
  function share(value, total) {
    return total > 0n && value != null ? Number(value * 1000000n / total) / 10000 : 0;
  }
  function renderStatusChart(s) {
    const states = [["succeeded","var(--chart-success, #446187)"],["running","var(--chart-running, #626bb2)"],
      ["queued","var(--chart-queued, #ad793e)"],["failed","var(--chart-fail, #b4223a)"],
      ["timed_out","var(--chart-timeout, #ac5729)"],["cancelled","var(--chart-cancel, #8591a2)"]];
    const ring = svgElement("svg", {viewBox:"0 0 148 148", class:"state-ring", "aria-hidden":"true"});
    const circle = {cx:74, cy:74, r:60, fill:"none", "stroke-width":13};
    ring.append(svgElement("circle", {...circle, class:"ring-track", stroke:"#e8edf1"}));
    const legend = el("ul", "");
    legend.className = "chart-legend";
    const total = countOf(s.tasks);
    let offset = 0;
    for (const [state, color] of states) {
      const value = countOf(s[state]);
      const percent = share(value, total);
      if (percent > 0) {
        ring.append(svgElement("circle", {...circle, class:"ring-segment", "data-state":state, stroke:color,
          pathLength:100, transform:"rotate(-90 74 74)", "stroke-dasharray":[percent, 100 - percent].join(" "),
          "stroke-dashoffset":-offset}));
        offset += percent;
      }
      const item = el("li", "");
      item.dataset.state = state;
      const swatch = svgElement("svg", {class:"state-key", "data-state":state, width:9, height:9, "aria-hidden":"true"});
      swatch.append(svgElement("circle", {cx:4.5, cy:4.5, r:4, fill:color}));
      const name = el("span", stateNames[state]);
      name.className = "state-label";
      item.append(swatch, name, el("strong", value));
      legend.append(item);
    }
    const number = svgElement("text", {x:74, y:74, "text-anchor":"middle", class:"ring-number"});
    const exact = String(total ?? "—");
    // Keep long counts inside the ring; the metrics above retain the exact figure.
    number.textContent = exact.length > 5 ? new Intl.NumberFormat("zh-CN", {notation:"compact"}).format(total) : exact;
    const caption = svgElement("text", {x:74, y:94, "text-anchor":"middle", class:"ring-caption"});
    caption.textContent = "任务总数";
    ring.append(number, caption);
    byId("status-chart").replaceChildren(ring, legend);
  }
  function renderCallChart(s) {
    const calls = [["tool","Tool 执行",countOf(s.tool_calls),"var(--chart-tool, #555f75)"],
      ["skill","Skill 加载",countOf(s.skill_calls),"var(--chart-skill, #a46d8c)"],
      ["model","模型准入",countOf(s.dispatches),"var(--chart-model, #be1832)"]];
    const maximum = calls.reduce((max, item) => item[2] != null && item[2] > max ? item[2] : max, 0n);
    const chart = byId("call-chart");
    chart.replaceChildren();
    for (const [kind, label, count, color] of calls) {
      const row = el("div", "");
      row.className = "call-row";
      row.dataset.kind = kind;
      const name = el("span", label);
      name.className = "call-label";
      const amount = el("strong", count);
      amount.className = "call-count";
      const bar = svgElement("svg", {class:"call-bar", width:"100%", height:6, "aria-hidden":"true"});
      bar.append(svgElement("rect", {x:0, y:0, width:"100%", height:6, rx:3, fill:"#e9edf2"}),
        svgElement("rect", {x:0, y:0, width:share(count, maximum) + "%", height:6, rx:3, fill:color}));
      row.append(name, amount, bar);
      chart.append(row);
    }
  }
  function renderSummary(data) {
    const s = data.summary;
    byId("scope").textContent = data.scope === "project" ? "全项目可见任务" : "当前主体可见任务";
    byId("window").textContent = "创建时间（本地） " + periodDate(data.window_start) + " – " + periodDate(data.window_end);
    facts(byId("metrics"), [
      ["任务数",s.tasks,{icon:"tasks"}],["Skill 调用总数",s.skill_calls,{icon:"skill"}],
      ["Tool 调用总数",s.tool_calls,{icon:"tool"}],["已结算费用",money(s.settled_micros),{icon:"cost"}],
      ["成功 / 失败",[s.succeeded ?? "—",s.failed ?? "—"].join(" / ")],
      ["排队 / 运行",[s.queued ?? "—",s.running ?? "—"].join(" / ")],
      ["取消 / 超时",[s.cancelled ?? "—",s.timed_out ?? "—"].join(" / ")],
      ["有能力记录的任务",s.tasks_with_spans],
      ["模型准入次数",s.dispatches],["含用量的结算记录",s.usage_receipts],
      ["待结算调用",s.pending_receipts,{warning:countOf(s.pending_receipts) > 0n}],
      ["预留费用",money(s.reserved_micros)],
      ["已知输入 Token",s.input_tokens],["已知输出 Token",s.output_tokens]
    ]);
    renderStatusChart(s);
    renderCallChart(s);
    const capabilities = byId("capabilities");
    capabilities.replaceChildren();
    byId("release").textContent = "发布版本 " + data.active_release.version_id;
    const registered = byId("registered");
    registered.replaceChildren();
    for (const c of data.active_release.capabilities) {
      const row = el("tr", "");
      cell(row, c.kind); cell(row, c.name); cell(row, c.version); cell(row, c.digest, "details");
      registered.append(row);
    }
    if (!registered.rows.length) empty(registered, 4, "当前版本未配置 Skill 或 Tool。");
    byId("cap-status").textContent = data.capabilities_truncated
      ? "按窗口内调用量显示前 " + data.capabilities.length + " 组。" : "";
    for (const c of data.capabilities) {
      const row = el("tr", "");
      cell(row, c.kind);
      const identity = cell(row, c.name, "details");
      identity.append(el("small", c.version), el("small", c.digest));
      cell(row, c.calls); cell(row, c.succeeded); cell(row, c.failed, c.failed > 0 ? "warning" : "");
      cell(row, c.unfinished); cell(row, ms(c.avg_duration_ms)); cell(row, ms(c.p95_duration_ms)); cell(row, ms(c.max_duration_ms));
      capabilities.append(row);
    }
    if (!data.capabilities.length) empty(capabilities, 9, "该范围内暂无能力执行记录。");
    byId("task-count").textContent = "显示 " + data.recent_tasks.length + " 项";
    const tasks = byId("tasks");
    tasks.replaceChildren();
    for (const t of data.recent_tasks) {
      const row = el("tr", "");
      row.dataset.task = t.task_id;
      row.classList.toggle("selected", selected === t.task_id);
      cell(row, date(t.created_at));
      cell(row, "").append(rowAction(t.task_id, () => showTask(t.task_id, true), "task-link"));
      stateCell(row, t.state, stateNames);
      stateCell(row, t.accounting_state, accounting);
      cell(row, t.error_code, t.error_code ? "warning" : "");
      tasks.append(row);
    }
    if (!data.recent_tasks.length) empty(tasks, 5, "该时间范围内没有可见任务。");
    byId("report").hidden = false;
  }
  function clearInventory() {
    closeResourceDetail();
    inventory = [];
    byId("resources").hidden = true;
    byId("resource-type").value = "";
    byId("resource-search").value = "";
    byId("reset-resource-filter").disabled = true;
    for (const id of ["skill-inventory","tool-inventory","catalog-table","catalog-release","catalog-result"]) byId(id).replaceChildren();
  }
  function closeResourceDetail() {
    const dialog = byId("resource-detail");
    if (dialog.open) dialog.close();
    byId("resource-detail-facts").replaceChildren();
  }
  function showResourceDetail(item) {
    facts(byId("resource-detail-facts"), [
      ["资源名称",item.name],["类型",kinds[item.kind]],["版本",item.version],
      ["完整摘要（SHA-256）",item.digest,{copyLabel:"完整摘要"}]
    ]);
    byId("resource-detail").showModal();
  }
  function renderResourceTable() {
    const table = byId("catalog-table");
    const kind = byId("resource-type").value;
    const query = byId("resource-search").value.trim().toLowerCase();
    const filtered = inventory.filter(item => (!kind || kind === item.kind) && item.name.toLowerCase().includes(query));
    table.replaceChildren();
    for (const item of filtered) {
      const row = el("tr", "");
      const button = rowAction(item.name, () => {
        button.focus();
        showResourceDetail(item);
      }, "resource-name");
      button.setAttribute("aria-haspopup", "dialog");
      cell(row, "").append(button);
      const chip = el("span", kinds[item.kind]);
      chip.className = "resource-kind";
      chip.dataset.kind = item.kind;
      cell(row, "").append(chip);
      cell(row, item.version);
      cell(row, item.digest.slice(0, 8) + "…" + item.digest.slice(-6), "digest");
      table.append(row);
    }
    byId("reset-resource-filter").disabled = !kind && !byId("resource-search").value;
    byId("catalog-result").textContent = query || kind
      ? "显示 " + filtered.length + " / " + inventory.length + " 项资源" : "共 " + inventory.length + " 项资源";
    if (!filtered.length) empty(table, 4, inventory.length ? "没有匹配的资源。" : "当前发布尚未启用 Skill 或 Tool。");
  }
  function renderInventory(data) {
    const sameCatalog = !byId("resources").hidden && byId("catalog-release").textContent === data.agent_version_id &&
      inventory.length === data.resources.length && data.resources.every((item, index) => {
        const old = inventory[index];
        return old.kind === item.kind && old.name === item.name && old.version === item.version && old.digest === item.digest;
      });
    byId("skill-inventory").textContent = data.counts.skill;
    byId("tool-inventory").textContent = data.counts.tool;
    if (sameCatalog) return;
    const restoreFocus = byId("resource-detail").open || byId("catalog-table").contains(document.activeElement);
    closeResourceDetail();
    byId("catalog-release").textContent = data.agent_version_id;
    inventory = data.resources;
    renderResourceTable();
    byId("resources").hidden = false;
    if (restoreFocus) byId("resource-search").focus();
  }
  async function refresh() {
    if (!connection || view === "home") return;
    if (view === "runs") return loadExplorer("agents");
    clearTimeout(timer);
    projectRead?.abort();
    const request = projectRead = new AbortController();
    byId("status").textContent = "正在读取项目…";
    try {
      const resourceView = view === "resources";
      const path = resourceView ? "/resources" : "/monitoring?hours=" + encodeURIComponent(byId("hours").value);
      const data = await read("/v1/projects/" + encodeURIComponent(connection.project) + path, request.signal);
      if (request.signal.aborted) return;
      if (data.project_id !== connection.project) throw new Error("project");
      if (resourceView) renderInventory(data);
      else renderSummary(data);
      openProject();
      byId("status").textContent = "更新于 " + date(Date.now());
      if (selected) await showTask(selected, false);
    } catch (error) {
      if (request.signal.aborted) return;
      if (returnToLogin(error)) return;
      clearProject();
      byId("status").textContent = error.status ? error.message : "无法读取监控数据，请检查连接后重试。";
    } finally {
      if (projectRead === request) { projectRead = null; schedule(); }
    }
  }
  function query(page) {
    const params = new URLSearchParams({hours:byId("hours").value, limit:"25"});
    if (page.cursor) params.set("cursor", page.cursor);
    return "?" + params;
  }
  function setPaging(prefix, page, next) {
    page.next = next || null;
    byId(prefix + "-prev").disabled = !page.previous.length;
    byId(prefix + "-next").disabled = !page.next;
  }
  function changePage(which, forward) {
    const pages = {agents:agentPage, runs:runPage, trace:tracePage};
    const page = pages[which];
    if (forward) {
      if (!page.next) return;
      page.previous.push(page.cursor); page.cursor = page.next;
    } else {
      if (!page.previous.length) return;
      page.cursor = page.previous.pop();
    }
    page.next = null;
    if (which === "trace") {
      clearTrace();
      loadExplorer("trace", true);
      return;
    }
    if (which === "agents") {
      agent = ""; runPage = pageState();
      byId("agent-version").disabled = true;
      byId("agent-version").replaceChildren();
    }
    instance = null;
    byId("run-id").value = "";
    clearRuns();
    loadExplorer(which);
  }
  function renderAgents(data) {
    const chooser = byId("agent-version");
    chooser.replaceChildren(new Option("选择发布版本", ""));
    for (const a of data.agents) {
      const label = [a.profile, a.agent_version_id, a.run_count + " 次运行"].filter(Boolean).join(" · ");
      chooser.add(new Option(label, a.agent_version_id));
    }
    if (agent && !data.agents.some(a => a.agent_version_id === agent)) {
      // The selected release can move off a refreshed page. Its runs are reauthorized below.
      chooser.add(new Option(agent + "（已选版本）", agent));
    }
    chooser.value = agent;
    chooser.disabled = chooser.options.length <= 1;
    byId("agent-scope").textContent = (data.scope === "project" ? "全项目可见运行" : "当前主体可见运行") + " · 运行开始时间 " +
      date(data.window_start) + " 至 " + date(data.window_end);
    byId("agent-note").textContent = data.agents.length ? "" : "这个时间范围内没有可见运行。";
    setPaging("agents", agentPage, data.next_cursor);
  }
  function renderRuns(data) {
    byId("run-list").hidden = false;
    byId("runs-release").textContent = "发布版本 " + data.agent_version_id;
    const table = byId("instances");
    table.replaceChildren();
    for (const r of data.runs) {
      const row = el("tr", "");
      row.dataset.run = r.run_id;
      row.classList.toggle("selected", instance?.run_id === r.run_id);
      cell(row, date(r.started_at)); cell(row, r.task_id);
      const attempt = r.run_id.slice(r.task_id.length + 1);
      const button = rowAction("第 " + attempt + " 次", () => {
        byId("run-id").value = r.run_id;
        chooseRun({task_id:r.task_id, run_id:r.run_id, attempt});
        loadExplorer("trace", true);
      }, "run-button", "查看任务 " + r.task_id + " 第 " + attempt + " 次运行");
      cell(row, "").append(button);
      stateCell(row, r.state, stateNames);
      cell(row, r.worker_id, "details");
      table.append(row);
    }
    if (!table.rows.length) empty(table, 5, "此页没有可见运行实例。");
    setPaging("instances", runPage, data.next_cursor);
  }
  function waterfallRows(data) {
    const calls = new Map(), milestones = [];
    for (const e of data.events) {
      if (String(e.attempt) !== String(data.attempt)) continue;
      const delta = elapsedMs(data.started_at, e.occurred_at);
      if (!Number.isFinite(delta) || delta < 0) continue;
      const at = Math.round(delta * 1000);
      if (e.kind === "task") {
        milestones.push({kind:e.kind, label:eventNames[e.event] || e.event, from:at, to:at,
          outcome:e.error_code || e.event === "failed" ? "failed" : "point", value:"+" + ms(at / 1000)});
        continue;
      }
      const id = e.kind === "model" ? e.dispatch_attempt_id : e.span_id;
      // A legacy event without an identifier can be drawn but cannot be paired.
      const key = id ? e.kind + ":" + id : Symbol();
      const call = calls.get(key) || {kind:e.kind, name:e.model || e.name, id};
      if (e.event === "started" || e.event === "dispatch_admitted") call.start = at;
      else {
        call.end = at;
        call.error = e.error_code || (e.event === "failed" ? "失败" : null);
        if (typeof e.duration_ms === "number" && Number.isFinite(e.duration_ms) && e.duration_ms >= 0) {
          call.duration = Math.round(e.duration_ms * 1000);
        }
      }
      calls.set(key, call);
    }
    const rows = [...milestones];
    for (const call of calls.values()) {
      const {kind, name, id, start, end, duration, error} = call;
      const from = start ?? (duration == null ? end : Math.max(0, end - duration));
      const to = end ?? start;
      const paired = start != null && end != null && end >= start;
      const known = duration ?? (paired ? end - start : null);
      const outcome = error ? "failed" : known != null ? "complete" : end == null ? "open" : "end";
      const value = known != null ? ms(known / 1000) : end == null ? "本页仅开始" :
        kind === "model" ? "本页仅结算" : "本页仅结束";
      const label = [kinds[kind] || kind, name].filter(Boolean).join(" · ");
      rows.push({kind, label, id, from, to:Math.max(from, to), outcome, value, error});
    }
    return rows.sort((a, b) => a.from - b.from);
  }
  function renderWaterfall(data) {
    const panel = byId("waterfall");
    const rows = waterfallRows(data);
    panel.replaceChildren();
    const note = el("p", rows.length
      ? "相对本次领取时间 · 线段表示区间，圆点表示单个时间点。模型区间从发送准入计至费用结算。"
      : "本页没有可定位的时间记录。");
    note.className = "waterfall-note";
    panel.append(note);
    if (!rows.length) return;
    const ended = elapsedMs(data.started_at, data.ended_at);
    const limit = Math.max(Number.isFinite(ended) ? Math.round(ended * 1000) : 0, ...rows.map(row => row.to));
    const axis = el("div", "");
    axis.className = "waterfall-axis";
    const range = el("span", "");
    range.className = "waterfall-axis-range";
    range.append(el("span", "0 ms"), el("span", limit ? ms(limit / 1000) : ""));
    axis.append(el("span", "运行步骤"), range, el("span", "耗时 / 时间点"));
    const list = el("div", "");
    list.className = "waterfall-list";
    for (const step of rows) {
      const row = el("div", ""), label = el("span", step.label), value = el("span", step.value);
      row.className = "waterfall-row"; row.dataset.kind = step.kind; row.dataset.outcome = step.outcome;
      label.className = "waterfall-label"; value.className = "waterfall-value";
      if (step.error) value.append(document.createTextNode(" · " + step.error));
      row.title = [step.label, step.id, "+" + ms(step.from / 1000), step.value, step.error].filter(Boolean).join(" · ");
      const track = svgElement("svg", {class:"waterfall-track", width:"100%", height:18, "aria-hidden":"true"});
      const x = offset => (limit ? 100 * offset / limit : 0) + "%";
      if (step.to > step.from) {
        track.append(svgElement("line", {x1:x(step.from), x2:x(step.to), y1:9, y2:9,
          stroke:"currentColor", "stroke-width":6, "stroke-linecap":"round"}));
      } else {
        track.append(svgElement("circle", {cx:x(step.from), cy:9, r:3, fill:"currentColor"}));
      }
      row.append(label, track, value);
      list.append(row);
    }
    panel.append(axis, list);
  }
  function renderTrace(data, focus) {
    byId("run-detail").hidden = false;
    if (focus) byId("trace-heading").focus();
    byId("trace-id").replaceChildren(copyValue(data.run_id, "运行 ID"),
      document.createTextNode(" · " + [data.profile, "发布版本 " + data.agent_version_id].filter(Boolean).join(" · ")));
    const bill = data.accounting;
    facts(byId("run-facts"), [
      ["运行状态",data.state,{statusLabels:stateNames}], ["Worker",data.worker_id],
      ["开始时间",eventTime(data.started_at)], ["结束时间",eventTime(data.ended_at)],
      ["观测运行耗时",ms(elapsedMs(data.started_at, data.ended_at))],
      ["结算状态",bill.state,{statusLabels:accounting}], ["已结算",money(bill.settled_micros)],
      ["已预留",money(bill.reserved_micros)], ["已知输入 / 输出 Token",
        (bill.input_tokens ?? "—") + " / " + (bill.output_tokens ?? "—")]
    ]);
    const note = "第 " + (tracePage.previous.length + 1) + " 页 · " + data.events.length + " 个事件（本地时间）";
    byId("trace-note").textContent = [note, data.error_code,
      data.has_capability_spans ? "" : "本次运行没有可用的能力观测记录"].filter(Boolean).join(" · ");
    renderWaterfall(data);
    renderEvents(byId("trace"), data.events);
    setPaging("events", tracePage, data.next_before);
  }
  async function loadExplorer(level, focus = false, missingRun = null) {
    if (!connection || view !== "runs") return;
    clearTimeout(timer);
    projectRead?.abort();
    const request = projectRead = new AbortController();
    const root = "/v1/projects/" + encodeURIComponent(connection.project) + "/agents";
    let stage = level;
    byId("explorer").hidden = !connection.verified;
    byId("status").textContent = "正在读取运行记录…";
    try {
      if (level === "agents") {
        const data = await read(root + query(agentPage), request.signal);
        if (request.signal.aborted) return;
        if (data.project_id !== connection.project) throw new Error("project");
        if (missingRun && data.agents.some(a => a.agent_version_id === missingRun.version)) agent = missingRun.version;
        renderAgents(data);
        openProject();
        byId("explorer").hidden = false;
      }
      if (agent && level !== "trace") {
        stage = "runs";
        const data = await read(root + "/" + encodeURIComponent(agent) + "/runs" + query(runPage), request.signal);
        if (request.signal.aborted) return;
        if (data.project_id !== connection.project || data.agent_version_id !== agent) throw new Error("release");
        renderRuns(data);
      }
      if (instance) {
        stage = "trace";
        const before = tracePage.cursor ? "?" + new URLSearchParams({before:tracePage.cursor}) : "";
        const data = await read("/v1/tasks/" + encodeURIComponent(instance.task_id) +
          "/runs/" + encodeURIComponent(instance.attempt) + "/trace" + before, request.signal);
        if (request.signal.aborted) return;
        if (data.project_id !== connection.project) {
          chooseRun(null);
          byId("status").textContent = "这次运行不属于当前项目。";
          return;
        }
        if (agent && data.agent_version_id !== agent) {
          chooseRun(null);
          byId("status").textContent = "这次运行与所选发布版本不一致。请更改或清除版本选择。";
          return;
        }
        if (data.run_id !== instance.run_id || data.task_id !== instance.task_id) throw new Error("instance");
        renderTrace(data, focus);
      }
      byId("status").textContent = missingRun ? "这次运行不存在或在当前权限下不可见。" : "更新于 " + date(Date.now());
    } catch (error) {
      if (request.signal.aborted) return;
      if (returnToLogin(error)) return;
      if (stage === "trace" && error.status === 404) {
        // A trace hides revoked access behind 404. Rebuild visible lists only after a fresh project check.
        const version = agent, typedId = byId("run-id").value;
        clearProject();
        byId("run-id").value = typedId;
        return loadExplorer("agents", false, {version});
      }
      if (stage === "trace") chooseRun(null);
      else if (stage === "runs") { instance = null; clearRuns(); }
      else { resetExplorer(); byId("explorer").hidden = false; }
      byId("status").textContent = error.status ? error.message : "无法读取运行轨迹，请检查连接后刷新。";
    } finally {
      if (projectRead === request) { projectRead = null; schedule(); }
    }
  }
  async function showTask(id, focus) {
    taskRead?.abort();
    const request = taskRead = new AbortController();
    selected = id;
    for (const row of byId("tasks").rows) row.classList.toggle("selected", row.dataset.task === id);
    byId("detail").hidden = false;
    if (focus) byId("detail-heading").focus();
    byId("detail-meta").textContent = id;
    byId("detail-status").textContent = "正在读取事件…";
    const table = byId("timeline");
    table.replaceChildren();
    try {
      const data = await read("/v1/tasks/" + encodeURIComponent(id) + "/timeline", request.signal);
      if (request.signal.aborted) return;
      if (data.task_id !== id || data.project_id !== connection.project) throw new Error("timeline scope");
      byId("detail-meta").textContent = id + " · " + (stateNames[data.state] || data.state) +
        " · 发布版本 " + data.agent_version_id + (data.error_code ? " · " + data.error_code : "");
      byId("detail-status").textContent = (data.truncated ? "仅显示最近 " : "显示 ") +
        data.events.length + " 个事件（本地时间）";
      renderEvents(table, data.events, id);
    } catch (error) {
      if (request.signal.aborted) return;
      if (!returnToLogin(error)) {
        byId("detail-status").textContent = error.status ? error.message : "时间线读取失败。";
      }
    }
  }
  function renderEvents(table, events, taskId = null) {
    table.replaceChildren();
    for (const e of events) {
      const row = el("tr", "");
      row.dataset.kind = e.kind;
      if (e.error_code || e.event === "failed") row.className = "failed";
      cell(row, eventTime(e.occurred_at)); cell(row, kinds[e.kind] || e.kind);
      cell(row, eventNames[e.event] || e.event);
      const attemptCell = cell(row, e.attempt);
      if (taskId && e.kind === "task" && e.event === "claimed" && /^[1-9]\d*$/.test(e.attempt)) {
        const attempt = String(e.attempt);
        const button = rowAction("第 " + attempt + " 次轨迹", () => switchView("runs", true, {
          task_id:taskId, attempt, run_id:taskId + ":" + attempt
        }), "run-button", "查看任务 " + taskId + " 第 " + attempt + " 次运行轨迹");
        attemptCell.replaceChildren(button);
      }
      const details = cell(row, [e.model || e.name,e.version].filter(Boolean).join(" "), "details");
      for (const [key,label] of [["digest","版本摘要"],["span_id","调用"],["parent_span_id","父调用"],
                                  ["dispatch_attempt_id","模型尝试"],["snapshot_id","快照"],["receipt_ref","回执摘要"]]) {
        if (e[key]) details.append(el("small", label + " " + e[key]));
      }
      if (e.reservation_micros != null) details.append(el("small", "预留 " + money(e.reservation_micros)));
      if (e.actual_micros != null) details.append(el("small", "结算 " + money(e.actual_micros)));
      if (e.input_tokens != null || e.output_tokens != null) {
        details.append(el("small", "输入 / 输出 Token " + (e.input_tokens ?? "—") + " / " + (e.output_tokens ?? "—")));
      }
      cell(row, ms(e.duration_ms)); cell(row, e.error_code, e.error_code ? "warning" : "");
      table.append(row);
    }
    if (!events.length) empty(table, 7, "尚无可见事件。");
  }
  function switchView(next, navigate = true, run = null) {
    if (next === "login" && connection?.verified) next = "overview";
    const url = viewUrl(next);
    if (location.pathname + location.hash !== url) {
      if (navigate) history.pushState(null, "", url);
      else history.replaceState(null, "", url);
    }
    if (view === next) return;
    clearTimeout(timer);
    projectRead?.abort(); projectRead = null;
    view = next;
    if (!connection?.verified) {
      disconnect();
      if (next === "home") byId("project").value = "";
    } else {
      clearProject();
      byId("status").textContent = "";
      showConnection();
    }
    if (view === "home" || !connection) {
      byId(view === "home" ? "home-heading" : "connect-heading").focus();
      return;
    }
    if (run) {
      byId("run-id").value = run.run_id;
      chooseRun(run);
      loadExplorer("agents", true);
    } else refresh();
  }
  byId("overview-view").addEventListener("click", () => switchView("overview"));
  byId("runs-view").addEventListener("click", () => switchView("runs"));
  byId("resources-view").addEventListener("click", () => switchView("resources"));
  byId("resource-search").addEventListener("input", renderResourceTable);
  byId("resource-type").addEventListener("change", renderResourceTable);
  byId("reset-resource-filter").addEventListener("click", () => {
    byId("resource-search").value = "";
    byId("resource-type").value = "";
    renderResourceTable();
    byId("resource-search").focus();
  });
  byId("close-resource-detail").addEventListener("click", closeResourceDetail);
  byId("resource-detail").addEventListener("cancel", event => {
    event.preventDefault();
    closeResourceDetail();
  });
  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey || link.target || link.hasAttribute("download")) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== "/console" || url.search ||
        (url.hash && !routes.has(url.hash.slice(1)))) return;
    event.preventDefault();
    switchView(url.hash.slice(1) || "home");
  });
  window.addEventListener("popstate", () => switchView(initialView(), false));
  window.addEventListener("hashchange", () => switchView(initialView(), false));
  byId("agent-version").addEventListener("change", event => {
    agent = event.target.value; instance = null; runPage = pageState();
    byId("run-id").value = "";
    clearRuns(); loadExplorer("runs");
  });
  byId("find-run").addEventListener("submit", event => {
    event.preventDefault();
    const runId = byId("run-id").value.trim();
    const parts = /^([^\s:/\\?#]+):([1-9]\d{0,19})$/.exec(runId);
    if (!parts || parts[1] === "." || parts[1] === ".." || BigInt(parts[2]) > 18446744073709551615n) {
      projectRead?.abort(); chooseRun(null); schedule();
      byId("status").textContent = "请输入有效的运行 ID，格式为 任务 ID:尝试次数。";
      return;
    }
    chooseRun({run_id:runId, task_id:parts[1], attempt:parts[2]});
    loadExplorer("trace", true);
  });
  for (const [prefix,level] of [["agents","agents"],["instances","runs"],["events","trace"]]) {
    byId(prefix + "-prev").addEventListener("click", () => changePage(level, false));
    byId(prefix + "-next").addEventListener("click", () => changePage(level, true));
  }
  byId("connect").addEventListener("submit", event => {
    event.preventDefault();
    if (connection || view === "home") return;
    const project = byId("project").value.trim(), token = byId("token").value.trim();
    if (!project || !token || project === "." || project === "..") {
      byId("connect-status").textContent = "请填写有效的项目 ID 和访问令牌。";
      byId("connect-status").dataset.tone = "error";
      return;
    }
    connection = {project,token,verified:false};
    byId("connect-status").textContent = "正在验证项目访问权限…";
    byId("connect-status").dataset.tone = "";
    showConnection();
    refresh();
  });
  byId("disconnect").addEventListener("click", () => {
    view = "home";
    history.replaceState(null, "", viewUrl(view));
    disconnect();
    byId("project").value = "";
    byId("home-heading").focus();
  });
  byId("refresh").addEventListener("click", refresh);
  byId("automatic").addEventListener("change", schedule);
  byId("hours").addEventListener("change", () => { clearDetail(); resetExplorer(); refresh(); });
  document.addEventListener("visibilitychange", schedule);
  window.addEventListener("pagehide", () => disconnect());
  history.replaceState(null, "", viewUrl(view));
  showConnection();
})();
