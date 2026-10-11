(function () {
  "use strict";

  if (document.body.dataset.page !== "run") return;
  const {api, clearAlert, element, formatCurrency, formatDate, formatNumber, jsonText, showAlert, statusBadge} = window.AphasiaEval;
  const root = document.getElementById("main-content");
  const runId = root.dataset.runId;
  const alertNode = document.getElementById("run-alert");
  const terminalStatuses = new Set(["completed", "cancelled", "failed", "interrupted"]);
  const state = {
    detail: null,
    items: [],
    taskMeta: new Map(),
    modelCounts: new Map(),
    lastFinished: 0,
    itemPage: 1,
    itemPageSize: 25,
    efficiencySort: {key: "model_id", ascending: true},
    efficiencyChart: null,
    detailChart: null,
    source: null,
    refreshTimer: null,
  };

  function aggregate(modelId, taskId, name, field = null) {
    return state.detail.aggregates.find((value) => value.model_id === modelId && value.task_id === taskId && value.stat_name === name && value.field === field)?.value ?? null;
  }

  function taskTitle(taskId) {
    return state.taskMeta.get(taskId)?.title || taskId;
  }

  function models() {
    return state.detail?.models.map((model) => model.model_id) || [];
  }

  function tasks() {
    return state.detail?.tasks.map((task) => task.task_id) || [];
  }

  function setStatus(status) {
    const node = document.getElementById("run-status");
    node.className = `status-pill status-${status}`;
    node.textContent = status.replaceAll("_", " ");
    document.getElementById("cancel-run").disabled = terminalStatuses.has(status);
  }

  function renderProgress(finished = state.detail.progress.finished, total = state.detail.progress.total) {
    const percentage = total ? Math.min(100, finished / total * 100) : 0;
    const progress = document.getElementById("overall-progress");
    progress.value = percentage;
    progress.textContent = `${Math.round(percentage)}%`;
    document.getElementById("progress-label").textContent = `${finished} of ${total} jobs`;
    const perModelTotal = models().length ? total / models().length : 0;
    const list = document.getElementById("model-progress");
    list.replaceChildren();
    models().forEach((model) => {
      const count = state.modelCounts.get(model) || 0;
      list.append(element("div", {className: "model-progress-row"}, [
        element("span", {text: model, title: model}),
        element("strong", {text: `${count} / ${formatNumber(perModelTotal, 0)}`}),
      ]));
    });
  }

  function initializeModelCounts() {
    models().forEach((model) => state.modelCounts.set(model, 0));
    state.items.forEach((item) => state.modelCounts.set(item.model_id, (state.modelCounts.get(item.model_id) || 0) + 1));
    state.lastFinished = state.detail.progress.finished;
  }

  function metricCard(label, value, detail = "") {
    return element("div", {className: "col-sm-6 col-xl-3"}, [
      element("article", {className: "metric-card"}, [
        element("span", {className: "metric-label", text: label}),
        element("strong", {className: "metric-value", text: value}),
        detail ? element("span", {className: "small text-secondary", text: detail}) : null,
      ]),
    ]);
  }

  function renderOverview() {
    const cards = document.getElementById("overview-cards");
    cards.replaceChildren();
    tasks().forEach((taskId) => {
      const meta = state.taskMeta.get(taskId);
      const candidates = models().map((model) => ({model, value: aggregate(model, taskId, "headline", meta?.headline?.field || null)})).filter((entry) => entry.value !== null);
      candidates.sort((a, b) => (meta?.headline?.better === "lower" ? a.value - b.value : b.value - a.value));
      const best = candidates[0];
      cards.append(metricCard(`Best · ${taskTitle(taskId)}`, best ? formatNumber(best.value, 3) : "n/a", best ? `${best.model} · ${meta?.headline?.stat || "headline"}` : "No comparable results"));
    });
    const modelCosts = models().map((model) => ({model, value: tasks().reduce((sum, task) => sum + (aggregate(model, task, "estimated_cost_usd") || 0), 0)}));
    const knownCosts = modelCosts.filter(({model}) => tasks().some((task) => aggregate(model, task, "estimated_cost_usd") !== null)).sort((a, b) => a.value - b.value);
    if (knownCosts[0]) cards.append(metricCard("Lowest estimated cost", formatCurrency(knownCosts[0].value), knownCosts[0].model));
    const times = models().map((model) => ({model, value: tasks().reduce((sum, task) => sum + (aggregate(model, task, "total_elapsed_ms") || 0), 0)})).filter((entry) => entry.value > 0).sort((a, b) => a.value - b.value);
    if (times[0]) cards.append(metricCard("Fastest total request time", `${formatNumber(times[0].value / 1000, 2)} s`, times[0].model));

    const wrapper = element("div", {className: "table-responsive"});
    const table = element("table", {className: "table align-middle"});
    const headRow = element("tr", {}, [element("th", {scope: "col", text: "Model"})]);
    tasks().forEach((task) => headRow.append(element("th", {scope: "col", text: taskTitle(task)})));
    const body = element("tbody");
    const allValues = tasks().flatMap((taskId) => {
      const field = state.taskMeta.get(taskId)?.headline?.field || null;
      return models().map((model) => aggregate(model, taskId, "headline", field)).filter((value) => value !== null);
    });
    const minimum = Math.min(...allValues);
    const maximum = Math.max(...allValues);
    models().forEach((model) => {
      const row = element("tr", {}, [element("th", {scope: "row", text: model})]);
      tasks().forEach((taskId) => {
        const meta = state.taskMeta.get(taskId);
        const value = aggregate(model, taskId, "headline", meta?.headline?.field || null);
        const normalized = value === null || maximum === minimum ? 0 : (value - minimum) / (maximum - minimum);
        const favorable = meta?.headline?.better === "lower" ? 1 - normalized : normalized;
        const level = value === null ? 0 : Math.max(1, Math.min(4, Math.ceil(favorable * 4)));
        const cell = element("td", {className: `heat-cell heat-${level}`, text: value === null ? "n/a" : formatNumber(value, 3)});
        const deviation = aggregate(model, taskId, "headline_stddev", meta?.headline?.field || null);
        if (deviation !== null) cell.append(element("span", {className: "d-block small fw-normal", text: `SD ${formatNumber(deviation, 3)}`}));
        row.append(cell);
      });
      body.append(row);
    });
    table.append(element("thead", {}, headRow), body);
    wrapper.append(table);
    document.getElementById("overview-matrix").replaceChildren(wrapper);
  }

  function efficiencyRows() {
    return models().flatMap((model) => tasks().map((task) => {
      const estimated = state.items.some((item) => item.model_id === model && item.task_id === task && item.tokens_estimated);
      const source = state.detail.models.find((entry) => entry.model_id === model)?.pricing_source || "unknown";
      return {
        model_id: model,
        task_id: task,
        input: aggregate(model, task, "input_tokens"),
        output: aggregate(model, task, "output_tokens"),
        cost: aggregate(model, task, "estimated_cost_usd"),
        elapsed: aggregate(model, task, "total_elapsed_ms"),
        mean: aggregate(model, task, "mean_latency_ms"),
        p95: aggregate(model, task, "p95_latency_ms"),
        estimated,
        source,
      };
    }));
  }

  function renderEfficiency() {
    const rows = efficiencyRows();
    const {key, ascending} = state.efficiencySort;
    rows.sort((a, b) => {
      const left = a[key] ?? Number.POSITIVE_INFINITY;
      const right = b[key] ?? Number.POSITIVE_INFINITY;
      return (typeof left === "string" ? left.localeCompare(right) : left - right) * (ascending ? 1 : -1);
    });
    const columns = [
      ["model_id", "Model"], ["task_id", "Task"], ["input", "Input tokens"], ["output", "Output tokens"],
      ["cost", "Estimated cost"], ["elapsed", "Total time"], ["mean", "Mean latency"], ["p95", "P95 latency"],
    ];
    const table = element("table", {className: "table align-middle"});
    const head = element("tr");
    columns.forEach(([columnKey, label]) => {
      const button = element("button", {className: "sort-button", type: "button", text: label, onclick: () => {
        state.efficiencySort = {key: columnKey, ascending: key === columnKey ? !ascending : true};
        renderEfficiency();
      }});
      head.append(element("th", {scope: "col"}, button));
    });
    const body = element("tbody");
    rows.forEach((row) => {
      const estimated = row.estimated ? " est." : "";
      body.append(element("tr", {}, [
        element("th", {scope: "row", text: row.model_id}),
        element("td", {text: taskTitle(row.task_id)}),
        element("td", {text: `${formatNumber(row.input, 0)}${estimated}`}),
        element("td", {text: `${formatNumber(row.output, 0)}${estimated}`}),
        element("td", {text: `${formatCurrency(row.cost)} · ${row.source.replaceAll("_", " ")}`, title: `Estimated cost source: ${row.source}`}),
        element("td", {text: row.elapsed === null ? "n/a" : `${formatNumber(row.elapsed / 1000, 2)} s`}),
        element("td", {text: row.mean === null ? "n/a" : `${formatNumber(row.mean, 1)} ms`}),
        element("td", {text: row.p95 === null ? "n/a" : `${formatNumber(row.p95, 1)} ms`}),
      ]));
    });
    table.append(element("thead", {}, head), body);
    document.getElementById("efficiency-table").replaceChildren(element("div", {className: "table-responsive"}, table));
    renderEfficiencyChart(rows);
  }

  function chartColors() {
    const styles = getComputedStyle(document.documentElement);
    return {teal: styles.getPropertyValue("--teal").trim(), coral: styles.getPropertyValue("--coral").trim(), ink: styles.getPropertyValue("--ink").trim(), line: styles.getPropertyValue("--line").trim()};
  }

  function renderEfficiencyChart(rows) {
    if (!window.Chart) return;
    state.efficiencyChart?.destroy();
    const colors = chartColors();
    const datasets = tasks().map((taskId, index) => {
      const field = state.taskMeta.get(taskId)?.headline?.field || null;
      return {
        label: taskTitle(taskId),
        data: rows.filter((row) => row.task_id === taskId && row.cost !== null).map((row) => ({x: row.cost, y: aggregate(row.model_id, taskId, "headline", field), model: row.model_id})).filter((point) => point.y !== null),
        backgroundColor: index % 2 ? colors.coral : colors.teal,
        pointRadius: 6,
      };
    });
    state.efficiencyChart = new Chart(document.getElementById("efficiency-chart"), {
      type: "scatter",
      data: {datasets},
      options: {responsive: true, maintainAspectRatio: false, plugins: {tooltip: {callbacks: {label: (context) => `${context.raw.model}: ${formatCurrency(context.raw.x)}, headline ${formatNumber(context.raw.y, 3)}`}}}, scales: {x: {title: {display: true, text: "Estimated cost (USD)"}, grid: {color: colors.line}, ticks: {color: colors.ink}}, y: {title: {display: true, text: "Task headline value"}, grid: {color: colors.line}, ticks: {color: colors.ink}}}},
    });
  }

  function renderTaskSelector() {
    const select = document.getElementById("task-detail-select");
    const current = select.value;
    select.replaceChildren(...tasks().map((task) => element("option", {value: task, text: taskTitle(task)})));
    if (tasks().includes(current)) select.value = current;
    select.onchange = renderTaskDetail;
    renderTaskDetail();
  }

  function renderTaskDetail() {
    state.detailChart?.destroy();
    state.detailChart = null;
    const taskId = document.getElementById("task-detail-select").value || tasks()[0];
    const type = state.taskMeta.get(taskId)?.view_descriptor?.type;
    if (type === "token_tags" || taskId === "pos_breakdown") renderTokenDetail(taskId);
    else renderNumericDetail(taskId);
  }

  function renderNumericDetail(taskId) {
    const container = document.getElementById("task-detail-view");
    container.replaceChildren();
    const taskItems = state.items.filter((item) => item.task_id === taskId && item.status === "ok");
    models().forEach((model) => {
      const rows = taskItems.filter((item) => item.model_id === model);
      const block = element("section", {className: "detail-model-block"}, [element("h3", {className: "h5", text: model})]);
      const table = element("table", {className: "table align-middle"});
      const head = element("tr", {}, [element("th", {scope: "col", text: "Item"}), element("th", {scope: "col", text: "Field"}), element("th", {scope: "col", text: "Model"}), element("th", {scope: "col", text: "Gold"}), element("th", {scope: "col", text: "Signed difference"})]);
      const body = element("tbody");
      rows.forEach((item) => Object.entries(item.comparison.details.fields).forEach(([field, detail]) => body.append(element("tr", {}, [element("th", {scope: "row", text: item.item_id}), element("td", {text: field}), element("td", {text: formatNumber(detail.model, 3)}), element("td", {text: formatNumber(detail.gold, 3)}), element("td", {text: formatNumber(detail.difference, 3)})]))));
      table.append(element("thead", {}, head), body);
      block.append(element("div", {className: "table-responsive"}, table));
      container.append(block);
    });
    const points = taskItems.map((item) => ({x: item.comparison.details.fields.ciu_count?.gold, y: item.comparison.details.fields.ciu_count?.model, model: item.model_id})).filter((point) => point.x !== undefined && point.y !== undefined);
    if (points.length && window.Chart) {
      const panel = element("div", {className: "chart-panel"}, [element("h3", {className: "h5", text: "Model versus gold CIU count"}), element("div", {className: "chart-frame"}, element("canvas", {role: "img", "aria-label": "Scatter chart comparing model and gold CIU counts"}))]);
      container.prepend(panel);
      const colors = chartColors();
      const max = Math.max(...points.flatMap((point) => [point.x, point.y]));
      state.detailChart = new Chart(panel.querySelector("canvas"), {type: "scatter", data: {datasets: [{label: "Model outputs", data: points, backgroundColor: colors.teal}, {label: "Identity line", type: "line", data: [{x: 0, y: 0}, {x: max, y: max}], borderColor: colors.coral, pointRadius: 0}]}, options: {responsive: true, maintainAspectRatio: false, scales: {x: {title: {display: true, text: "Gold CIU count"}}, y: {title: {display: true, text: "Model CIU count"}}}}});
    }
  }

  function renderTokenDetail(taskId) {
    const container = document.getElementById("task-detail-view");
    container.replaceChildren();
    const taskItems = state.items.filter((item) => item.task_id === taskId && item.status === "ok");
    const tagset = state.taskMeta.get(taskId)?.view_descriptor?.chart?.categories || [];
    const chartDatasets = [];
    models().forEach((model, modelIndex) => {
      const rows = taskItems.filter((item) => item.model_id === model);
      const alignment = rows.filter((item) => item.comparison.statistics.alignment_problem).length;
      const ratioValues = rows.map((item) => item.comparison.details.derived_ratios).filter(Boolean);
      const block = element("section", {className: "detail-model-block"}, [
        element("h3", {className: "h5", text: model}),
        element("div", {className: "row g-3 mb-3"}, [
          metricCard("Mean token agreement", formatNumber(aggregate(model, taskId, "token_agreement") * 100, 1) + "%").firstChild,
          metricCard("Alignment problems", `${alignment} / ${rows.length}`).firstChild,
          metricCard("Derived ratios", ratioValues.length ? jsonText(ratioValues[0]) : "n/a").firstChild,
        ]),
      ]);
      const table = element("table", {className: "table align-middle"});
      const body = element("tbody");
      const modelCounts = {};
      const goldCounts = {};
      tagset.forEach((tag) => { modelCounts[tag] = 0; goldCounts[tag] = 0; });
      rows.forEach((item) => tagset.forEach((tag) => {
        modelCounts[tag] += item.comparison.details.categories[tag].model_count;
        goldCounts[tag] += item.comparison.details.categories[tag].gold_count;
      }));
      tagset.forEach((tag) => body.append(element("tr", {}, [element("th", {scope: "row", text: tag}), element("td", {text: modelCounts[tag]}), element("td", {text: goldCounts[tag]}), element("td", {text: modelCounts[tag] - goldCounts[tag]})])));
      table.append(element("thead", {}, element("tr", {}, [element("th", {scope: "col", text: "Category"}), element("th", {scope: "col", text: "Model count"}), element("th", {scope: "col", text: "Gold count"}), element("th", {scope: "col", text: "Signed difference"})])), body);
      block.append(element("div", {className: "table-responsive"}, table));
      container.append(block);
      const colors = chartColors();
      chartDatasets.push({label: `${model} · model`, data: tagset.map((tag) => modelCounts[tag]), backgroundColor: modelIndex % 2 ? colors.coral : colors.teal});
      if (modelIndex === 0) chartDatasets.push({label: "Gold", data: tagset.map((tag) => goldCounts[tag]), backgroundColor: colors.line});
    });
    if (tagset.length && window.Chart) {
      const panel = element("div", {className: "chart-panel"}, [element("h3", {className: "h5", text: "Gold and model category counts"}), element("div", {className: "chart-frame"}, element("canvas", {role: "img", "aria-label": "Grouped bars comparing gold and model POS category counts"}))]);
      container.prepend(panel);
      state.detailChart = new Chart(panel.querySelector("canvas"), {type: "bar", data: {labels: tagset, datasets: chartDatasets}, options: {responsive: true, maintainAspectRatio: false, scales: {y: {beginAtZero: true}}}});
    }
  }

  function filteredItems() {
    return state.items.filter((item) => {
      if (document.getElementById("filter-disagreements").checked && !(item.status === "ok" && item.matched === false)) return false;
      if (document.getElementById("filter-parse").checked && item.status !== "parse_failure") return false;
      if (document.getElementById("filter-alignment").checked && !item.comparison?.statistics?.alignment_problem) return false;
      return true;
    });
  }

  function dataBlock(title, value) {
    return element("section", {className: "data-block"}, [element("h4", {text: title}), element("pre", {text: jsonText(value)})]);
  }

  function renderItems() {
    const filtered = filteredItems();
    const pages = Math.max(1, Math.ceil(filtered.length / state.itemPageSize));
    state.itemPage = Math.min(state.itemPage, pages);
    const visible = filtered.slice((state.itemPage - 1) * state.itemPageSize, state.itemPage * state.itemPageSize);
    document.getElementById("items-summary").textContent = `Showing ${visible.length} of ${filtered.length} matching results (${state.items.length} total).`;
    const list = document.getElementById("items-list");
    list.replaceChildren();
    visible.forEach((item) => {
      const metadata = element("div", {}, [element("strong", {text: `${item.item_id} · ${item.model_id}`}), element("span", {className: "d-block small text-secondary", text: `${taskTitle(item.task_id)} · repetition ${item.repetition}${item.tokens_estimated ? " · token counts est." : ""}`})]);
      const body = element("div", {className: "item-result-body"}, [dataBlock("Input", item.input), dataBlock("Gold", item.gold), dataBlock("Model answer", item.parsed_output)]);
      const details = element("details", {className: "raw-output"}, [element("summary", {text: "Raw output and comparison details"}), dataBlock("Raw model output", item.raw_output), dataBlock("Comparison", item.comparison), item.error_message ? element("p", {className: "text-danger mt-3", text: item.error_message}) : null]);
      list.append(element("article", {className: "item-result"}, [element("header", {className: "item-result-header"}, [metadata, statusBadge(item.status)]), body, details]));
    });
    renderPagination(pages);
  }

  function renderPagination(pages) {
    const nav = document.getElementById("items-pagination");
    if (pages <= 1) { nav.replaceChildren(); return; }
    const list = element("ul", {className: "pagination"});
    const add = (label, page, disabled, current = false) => {
      const button = element("button", {className: "page-link", type: "button", text: label, disabled, onclick: () => { state.itemPage = page; renderItems(); document.getElementById("items-pane").focus(); }});
      list.append(element("li", {className: `page-item${disabled ? " disabled" : ""}${current ? " active" : ""}`}, button));
    };
    add("Previous", state.itemPage - 1, state.itemPage === 1);
    for (let page = 1; page <= pages; page += 1) if (pages <= 7 || page === 1 || page === pages || Math.abs(page - state.itemPage) <= 1) add(String(page), page, false, page === state.itemPage);
    add("Next", state.itemPage + 1, state.itemPage === pages);
    nav.replaceChildren(list);
  }

  function infoCard(title, pairs) {
    const list = element("dl");
    pairs.forEach(([term, value, className]) => list.append(element("dt", {text: term}), element("dd", {className: className || "", text: value ?? "n/a"})));
    return element("section", {className: "info-card"}, [element("h3", {className: "h5", text: title}), list]);
  }

  function renderRunInfo() {
    const run = state.detail.run;
    const grid = element("div", {className: "info-grid"}, [
      infoCard("Run", [["Status", run.status], ["Created", formatDate(run.created_at)], ["Started", formatDate(run.started_at)], ["Finished", formatDate(run.finished_at)], ["App version", run.app_version], ["LiteLLM host", run.litellm_host]]),
      infoCard("Settings", Object.entries(run.settings).map(([key, value]) => [key.replaceAll("_", " "), value === null ? "All" : String(value)])),
      ...state.detail.tasks.map((task) => infoCard(taskTitle(task.task_id), [["Task version", task.version], ["Task file SHA-256", task.task_file_sha256, "hash-value"], ["Prompt SHA-256", task.prompt_sha256, "hash-value"], ["Gold file SHA-256", task.gold_file_sha256, "hash-value"]])),
    ]);
    document.getElementById("run-info-view").replaceChildren(grid);
  }

  function renderAll() {
    const run = state.detail.run;
    document.getElementById("run-title").textContent = run.label || `Run ${run.id.slice(0, 8)}`;
    setStatus(run.status);
    renderProgress();
    renderOverview();
    renderEfficiency();
    renderTaskSelector();
    renderItems();
    renderRunInfo();
    document.getElementById("result-refresh").textContent = terminalStatuses.has(run.status) ? `Finished ${formatDate(run.finished_at)}` : "Results update as jobs complete.";
  }

  async function fetchAllItems() {
    const all = [];
    let page = 1;
    while (true) {
      const response = await api(`/api/runs/${encodeURIComponent(runId)}/items?page=${page}&per_page=200`);
      all.push(...response.items);
      if (page >= response.pagination.pages) break;
      page += 1;
    }
    return all;
  }

  async function refresh() {
    const [detail, items] = await Promise.all([api(`/api/runs/${encodeURIComponent(runId)}`), fetchAllItems()]);
    state.detail = detail;
    state.items = items;
    initializeModelCounts();
    renderAll();
  }

  function scheduleResultRefresh() {
    window.clearTimeout(state.refreshTimer);
    state.refreshTimer = window.setTimeout(() => {
      refresh().catch((error) => showAlert(alertNode, error.message));
    }, 400);
  }

  function handleEvent(event) {
    const data = JSON.parse(event.data);
    if (data.model_id && data.finished > state.lastFinished) {
      state.modelCounts.set(data.model_id, (state.modelCounts.get(data.model_id) || 0) + 1);
      state.lastFinished = data.finished;
    }
    if (Number.isFinite(data.finished)) renderProgress(data.finished, data.total);
    if (event.type === "job_completed" || event.type === "job_failed") scheduleResultRefresh();
    if (data.status && terminalStatuses.has(data.status)) {
      window.clearTimeout(state.refreshTimer);
      state.source?.close();
      refresh().catch((error) => showAlert(alertNode, error.message));
    }
  }

  function connectEvents() {
    state.source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    ["run_started", "job_completed", "job_failed", "run_progress", "run_completed", "run_cancelled"].forEach((name) => state.source.addEventListener(name, handleEvent));
    state.source.onerror = () => {
      if (!terminalStatuses.has(state.detail.run.status)) document.getElementById("result-refresh").textContent = "Live connection interrupted; the browser will retry.";
    };
  }

  async function load() {
    clearAlert(alertNode);
    try {
      const [detail, items, taskData] = await Promise.all([api(`/api/runs/${encodeURIComponent(runId)}`), fetchAllItems(), api("/api/tasks")]);
      state.detail = detail;
      state.items = items;
      taskData.tasks.forEach((task) => state.taskMeta.set(task.task_id, task));
      initializeModelCounts();
      renderAll();
      if (!terminalStatuses.has(detail.run.status)) connectEvents();
    } catch (error) {
      showAlert(alertNode, error.message);
      document.getElementById("run-title").textContent = "Run unavailable";
    }
  }

  document.getElementById("cancel-run").addEventListener("click", async () => {
    const button = document.getElementById("cancel-run");
    button.disabled = true;
    button.textContent = "Cancelling…";
    try {
      await api(`/api/runs/${encodeURIComponent(runId)}/cancel`, {method: "POST", body: {}});
      document.getElementById("result-refresh").textContent = "Cancellation requested; in-flight requests will finish.";
    } catch (error) {
      showAlert(alertNode, error.message);
      button.disabled = false;
      button.textContent = "Cancel run";
    }
  });
  ["filter-disagreements", "filter-parse", "filter-alignment"].forEach((id) => document.getElementById(id).addEventListener("change", () => { state.itemPage = 1; renderItems(); }));
  window.addEventListener("beforeunload", () => {
    window.clearTimeout(state.refreshTimer);
    state.source?.close();
  });
  load();
})();
