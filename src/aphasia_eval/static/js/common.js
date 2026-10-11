(function () {
  "use strict";

  const themeQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const applyTheme = () => {
    document.documentElement.dataset.bsTheme = themeQuery.matches ? "dark" : "light";
  };
  applyTheme();
  themeQuery.addEventListener("change", applyTheme);

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
