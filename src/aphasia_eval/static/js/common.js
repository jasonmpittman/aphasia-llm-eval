(function () {
  "use strict";

  const sidebarKey = "aphasia-llm-eval:sidebar-collapsed:v1";
  const sidebarQuery = window.matchMedia("(max-width: 991.98px)");
  const sidebar = document.getElementById("app-sidebar");
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const sidebarScrim = document.getElementById("sidebar-scrim");

  function storedSidebarState() {
    try { return localStorage.getItem(sidebarKey) === "true"; }
    catch (_error) { return false; }
  }

  function updateSidebarAccessibility() {
    const expanded = sidebarQuery.matches
      ? document.body.classList.contains("sidebar-open")
      : !document.body.classList.contains("sidebar-collapsed");
    sidebarToggle.setAttribute("aria-expanded", String(expanded));
    sidebarToggle.setAttribute("aria-label", sidebarQuery.matches
      ? (expanded ? "Close navigation" : "Open navigation")
      : (expanded ? "Collapse navigation" : "Expand navigation"));
    sidebar.inert = sidebarQuery.matches && !expanded;
  }

  function initializeSidebar() {
    document.body.classList.toggle("sidebar-collapsed", !sidebarQuery.matches && storedSidebarState());
    document.body.classList.remove("sidebar-open");
    updateSidebarAccessibility();
  }

  sidebarToggle.addEventListener("click", () => {
    if (sidebarQuery.matches) {
      document.body.classList.toggle("sidebar-open");
    } else {
      const collapsed = document.body.classList.toggle("sidebar-collapsed");
      try { localStorage.setItem(sidebarKey, String(collapsed)); } catch (_error) { /* noop */ }
    }
    updateSidebarAccessibility();
  });
  sidebarScrim.addEventListener("click", () => {
    document.body.classList.remove("sidebar-open");
    sidebarToggle.focus();
    updateSidebarAccessibility();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) {
      document.body.classList.remove("sidebar-open");
      sidebarToggle.focus();
      updateSidebarAccessibility();
    }
  });
  sidebarQuery.addEventListener("change", initializeSidebar);
  initializeSidebar();

  function element(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(options).forEach(([key, value]) => {
      if (value === null || value === undefined) return;
      if (key === "className") node.className = value;
      else if (key === "text") node.textContent = String(value);
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key in node && key !== "list") node[key] = value;
      else node.setAttribute(key, String(value));
    });
    const normalized = Array.isArray(children) ? children : [children];
    normalized.forEach((child) => {
      if (child === null || child === undefined) return;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return node;
  }

  async function api(path, options = {}) {
    const requestOptions = {...options};
    requestOptions.headers = {...(options.headers || {})};
    if (options.body !== undefined && typeof options.body !== "string") {
      requestOptions.headers["Content-Type"] = "application/json";
      requestOptions.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, requestOptions);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const error = new Error(data?.error?.message || `Request failed with HTTP ${response.status}.`);
      error.code = data?.error?.code || "request_failed";
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function showAlert(node, message) {
    node.textContent = message;
    node.classList.remove("d-none");
    node.focus({preventScroll: true});
  }

  function clearAlert(node) {
    node.textContent = "";
    node.classList.add("d-none");
  }

  function formatNumber(value, maximumFractionDigits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
    return new Intl.NumberFormat(undefined, {maximumFractionDigits}).format(Number(value));
  }

  function formatCurrency(value) {
    if (value === null || value === undefined) return "n/a";
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 4,
      maximumFractionDigits: 6,
    }).format(Number(value));
  }

  function formatDate(value) {
    if (!value) return "n/a";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? "n/a" : date.toLocaleString();
  }

  function statusBadge(status) {
    return element("span", {
      className: `status-pill status-${String(status).toLowerCase()}`,
      text: String(status).replaceAll("_", " "),
    });
  }

  function jsonText(value) {
    return JSON.stringify(value, null, 2);
  }

  window.AphasiaEval = Object.freeze({
    api,
    clearAlert,
    element,
    formatCurrency,
    formatDate,
    formatNumber,
    jsonText,
    showAlert,
    statusBadge,
  });
})();
