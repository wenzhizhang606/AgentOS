"use strict";

// Shared native DOM renderers. Callers own their data, listeners and view lifetime.
const ConsoleUI = (() => {
  function el(tag, value) {
    const node = document.createElement(tag);
    node.textContent = value == null ? "—" : String(value);
    return node;
  }

  function svgElement(tag, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }

  const iconPaths = {
    tasks: "M8 5H5v16h14V5h-3 M9 3h6v4H9z M8 11h8m-8 4h6",
    skill: "M3 4.5c3.5-.8 6-.3 9 1.5 3-1.8 5.5-2.3 9-1.5V19c-3.6-.8-6.2-.3-9 1.5-2.8-1.8-5.4-2.3-9-1.5z M12 6v14",
    tool: "M14.6 6.7 18 3.3a5.4 5.4 0 0 0-6.8 6.9l-7.6 7.6a2.4 2.4 0 0 0 3.4 3.4l7.6-7.6a5.4 5.4 0 0 0 6.9-6.8l-3.4 3.4z",
    cost: "M4 6h16v13H4z M4 6l12-3v3 M15 10h5v5h-5z",
    empty: "M3 12.5 7.5 5h9l4.5 7.5V19H3z M3 12.5h5l1.5 2.5h5l1.5-2.5h5"
  };
  function icon(name, className) {
    const node = svgElement("svg", {class:className, viewBox:"0 0 24 24", width:18, height:18,
      fill:"none", stroke:"currentColor", "stroke-width":1.6, "stroke-linecap":"round",
      "stroke-linejoin":"round", "aria-hidden":"true", focusable:"false"});
    node.append(svgElement("path", {d:iconPaths[name]}));
    return node;
  }

  function cell(row, value, className) {
    const node = el("td", value);
    if (className) node.className = className;
    row.append(node);
    return node;
  }

  function statusChip(state, labels) {
    const label = Object.hasOwn(labels, state) ? labels[state] : state;
    const chip = el("span", label);
    chip.className = "status-chip ui-status";
    if (state != null) chip.dataset.state = state;
    return chip;
  }

  function stateCell(row, state, labels) {
    cell(row, "").append(statusChip(state, labels));
  }

  function rowAction(label, onClick, className, accessibleName) {
    const node = el("button", label);
    node.type = "button";
    node.className = "ui-table-action " + className;
    if (accessibleName) node.setAttribute("aria-label", accessibleName);
    node.addEventListener("click", onClick);
    return node;
  }

  function copyValue(value, label) {
    const wrapper = el("span", "");
    wrapper.className = "copy-value";
    const code = el("code", value);
    wrapper.append(code);
    if (!value) return wrapper;
    const feedback = el("span", "");
    feedback.className = "copy-feedback";
    feedback.setAttribute("role", "status");
    const button = rowAction("复制", async () => {
      try {
        await navigator.clipboard.writeText(value);
        feedback.textContent = "已复制";
      } catch {
        if (!code.isConnected) return;
        const selection = document.getSelection();
        if (selection) {
          const range = document.createRange();
          range.selectNodeContents(code);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        feedback.textContent = "请手动复制";
      }
    }, "copy-action", "复制" + label);
    wrapper.append(button, feedback);
    return wrapper;
  }

  function empty(tbody, columns, message) {
    const row = el("tr", "");
    const messageCell = cell(row, "", "note ui-empty");
    messageCell.colSpan = columns;
    const content = el("span", "");
    content.className = "ui-empty__content";
    content.append(icon("empty", "ui-empty__icon"), el("span", message));
    messageCell.append(content);
    tbody.append(row);
  }

  // One entry represents a term and its value in a <dl>. Values retain their wire precision.
  function facts(container, entries) {
    const fragment = document.createDocumentFragment();
    for (const [label, value, options = {}] of entries) {
      const item = el("div", "");
      item.className = options.warning ? "ui-fact warning" : "ui-fact";
      const term = el("dt", label);
      term.className = "ui-fact__label";
      if (options.icon) term.prepend(icon(options.icon, "ui-fact__icon"));
      const description = el("dd", value);
      description.className = "ui-fact__value";
      if (options.statusLabels) description.replaceChildren(statusChip(value, options.statusLabels));
      if (options.copyLabel) description.replaceChildren(copyValue(value, options.copyLabel));
      item.append(term, description);
      fragment.append(item);
    }
    container.replaceChildren(fragment);
  }

  return Object.freeze({el, svgElement, cell, stateCell, rowAction, copyValue, empty, facts});
})();
