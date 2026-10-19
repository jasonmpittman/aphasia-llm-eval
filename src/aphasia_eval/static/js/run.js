(function () {
  "use strict";

  if (document.body.dataset.page !== "run") return;
  const {api, clearAlert, element, formatCurrency, formatDate, formatNumber, helpButton, jsonText, showAlert, statusBadge} = window.AphasiaEval;
  const root = document.getElementById("main-content");
  const runId = root.dataset.runId;
  const alertNode = document.getElementById("run-alert");
  const terminalStatuses = new Set(["completed", "cancelled", "failed", "interrupted"]);
  const metricDefinitions = {
    precision: "Of the tokens assigned this label by the model, the share whose gold label matches.",
    recall: "Of the gold tokens with this label, the share the model assigned correctly.",
    f1: "The harmonic mean of precision and recall. It is high only when both are high.",
    support: "The number of gold tokens carrying this label.",
    macroF1: "The simple average of defined label-level F1 values. Each label receives equal weight.",
    agreement: "The share of reference token positions where the model and gold labels match exactly.",
    confusion: "Gold labels are rows and model predictions are columns. Diagonal cells are correct; off-diagonal cells are errors.",
    latency: "Time from sending a request until its complete response arrives. M8 does not yet measure time to first token.",
    p95: "A tail-latency estimate: about 95% of observed requests completed at or below this duration.",
  };
  const specialLabelNames = {
    __missing__: "Missing prediction",
    __invalid__: "Invalid prediction",
    __duplicate__: "Duplicate prediction",
    __extra__: "Extra token index",
  };
  const state = {
    detail: null,
    items: [],
    taskMeta: new Map(),
    modelCounts: new Map(),
    lastFinished: 0,
    itemPage: 1,
    itemPageSize: 25,
    efficiencySort: {key: "model_id", ascending: true},
    efficiencyCharts: [],
    detailChart: null,
    ablationCharts: [],
    resultMode: null,
    source: null,
    refreshTimer: null,
  };

  function activeCondition(modelId, taskId) {
    if (!state.detail?.ablation?.enabled) return "baseline";
    if (state.resultMode === "closest") {
      const entries = state.detail.ablation.closest_conditions || state.detail.ablation.best_conditions;
      return entries.find((entry) => entry.model_id === modelId && entry.task_id === taskId)?.condition_id || "baseline";
    }
    if (state.resultMode?.startsWith("condition:")) return state.resultMode.slice(10);
    return "baseline";
  }

  function aggregateEntry(modelId, taskId, name, field = null, conditionId = activeCondition(modelId, taskId)) {
    return state.detail.aggregates.find((value) => value.model_id === modelId && value.task_id === taskId && value.condition_id === conditionId && value.stat_name === name && value.field === field) || null;
  }

  function aggregate(modelId, taskId, name, field = null, conditionId = activeCondition(modelId, taskId)) {
    return aggregateEntry(modelId, taskId, name, field, conditionId)?.value ?? null;
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

  function conditionById(conditionId) {
    return state.detail?.ablation?.conditions.find((condition) => condition.condition_id === conditionId) || {
      condition_id: conditionId,
      label: conditionId === "baseline" ? "Baseline" : conditionId,
      is_baseline: conditionId === "baseline",
      parameter: null,
      value: null,
    };
  }

  function conditionName(conditionId) {
    const condition = conditionById(conditionId);
    if (!state.detail?.ablation?.enabled) return "Baseline configuration";
    const parameter = condition.parameter?.replaceAll("_", " ") || "configuration";
    const value = condition.value ?? state.detail.ablation.baseline_value;
    return `${condition.is_baseline ? "Baseline · " : ""}${parameter}: ${formatNumber(value, 0)}`;
  }

  function pairSummaries(modelId, taskId) {
    return (state.detail?.ablation?.summaries || []).filter((row) => row.model_id === modelId && row.task_id === taskId);
  }

  function closestEntry(modelId, taskId) {
    const entries = state.detail?.ablation?.closest_conditions || state.detail?.ablation?.best_conditions || [];
    return entries.find((entry) => entry.model_id === modelId && entry.task_id === taskId) || null;
  }

  function conditionSummary(modelId, taskId, conditionId = activeCondition(modelId, taskId)) {
    return pairSummaries(modelId, taskId).find((row) => row.condition_id === conditionId) || null;
  }

  function conditionIsUsable(summary) {
    return Boolean(summary && summary.headline !== null && (summary.comparable_response_rate || 0) > 0);
  }

  function headlineLabel(taskId) {
    const headline = state.taskMeta.get(taskId)?.headline || {};
    if (headline.stat === "mean_abs_diff" && headline.field === "ciu_count") return "Mean absolute CIU-count error";
    if (headline.stat === "token_agreement") return "Token agreement";
    return String(headline.stat || "Gold comparison").replaceAll("_", " ");
  }

  function formatHeadline(taskId, value) {
    if (value === null || value === undefined) return "n/a";
    const headline = state.taskMeta.get(taskId)?.headline || {};
    return headline.stat?.includes("agreement")
      ? `${formatNumber(value * 100, 1)}%`
      : formatNumber(value, 3);
  }

  function baselineComparison(modelId, taskId, selectedValue) {
    if (!state.detail?.ablation?.enabled || activeCondition(modelId, taskId) === "baseline") return null;
    const baseline = conditionSummary(modelId, taskId, "baseline");
    if (!conditionIsUsable(baseline)) return "Baseline did not produce a comparable result.";
    const better = state.taskMeta.get(taskId)?.headline?.better || "lower";
    const favorableChange = better === "higher"
      ? selectedValue - baseline.headline
      : baseline.headline - selectedValue;
    if (Math.abs(favorableChange) < Number.EPSILON) return "Matches the baseline gold-comparison result.";
    const direction = favorableChange > 0 ? "Improves on" : "Is worse than";
    const magnitude = state.taskMeta.get(taskId)?.headline?.stat?.includes("agreement")
      ? `${formatNumber(Math.abs(favorableChange) * 100, 1)} percentage points`
      : formatNumber(Math.abs(favorableChange), 3);
    return `${direction} baseline by ${magnitude}.`;
  }

  function failureSummary(items) {
    const counts = new Map();
    items.filter((item) => item.status !== "ok" && item.status !== "cancelled").forEach((item) => {
      const label = item.status === "parse_failure" ? "parse failure" : item.comparison?.diagnostics?.error_code === "truncated" ? "truncation" : "request error";
      counts.set(label, (counts.get(label) || 0) + 1);
    });
    return [...counts].map(([label, count]) => `${count} ${label}${count === 1 ? "" : "s"}`).join(" · ") || "No comparable answer";
  }

  function jobItems(modelId, taskId) {
    const conditionId = activeCondition(modelId, taskId);
    return state.items.filter((item) => item.model_id === modelId && item.task_id === taskId && item.condition_id === conditionId);
  }

  function failedItems(modelId, taskId) {
    return jobItems(modelId, taskId).filter((item) => item.status !== "ok");
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

  function metricCard(label, value, detail = "", definition = "") {
    const labelNode = element("span", {className: "metric-label"}, [label]);
    if (definition) labelNode.append(helpButton(label, definition));
    return element("div", {className: "col-sm-6 col-xl-3"}, [
      element("article", {className: "metric-card"}, [
        labelNode,
        element("strong", {className: "metric-value", text: value}),
        detail ? element("span", {className: "small text-secondary", text: detail}) : null,
      ]),
    ]);
  }

  function renderOverview() {
    const cards = document.getElementById("overview-cards");
    cards.replaceChildren();
    const active = models().flatMap((model) => tasks().flatMap((task) => jobItems(model, task)));
    const comparable = active.filter((item) => item.status === "ok");
    cards.append(metricCard(
      "Comparable selected jobs",
      `${comparable.length} / ${active.length}`,
      comparable.length === active.length ? "Every selected result can be compared with gold." : "Failures remain visible and are not treated as low agreement.",
    ));
    tasks().forEach((taskId) => {
      const meta = state.taskMeta.get(taskId);
      const candidates = models().map((model) => {
        const summary = conditionSummary(model, taskId);
        return {
          model,
          value: aggregate(model, taskId, "headline", meta?.headline?.field || null),
          coverage: summary?.comparable_response_rate ?? aggregate(model, taskId, "comparable_response_rate"),
        };
      }).filter((entry) => entry.value !== null);
      candidates.sort((a, b) => {
        const coverageDifference = (b.coverage || 0) - (a.coverage || 0);
        if (coverageDifference) return coverageDifference;
        return meta?.headline?.better === "lower" ? a.value - b.value : b.value - a.value;
      });
      const best = candidates[0];
      cards.append(metricCard(
        `Closest · ${taskTitle(taskId)}`,
        best ? formatHeadline(taskId, best.value) : "No valid result",
        best ? `${best.model} · ${conditionName(activeCondition(best.model, taskId))} · ${formatNumber((best.coverage || 0) * 100, 1)}% comparable` : "No selected configuration produced a gold-comparable answer.",
      ));
    });
    if (state.detail.ablation?.enabled) {
      const recoveries = models().flatMap((model) => tasks().map((taskId) => ({
        model,
        taskId,
        baseline: conditionSummary(model, taskId, "baseline"),
        closest: closestEntry(model, taskId),
      }))).filter((entry) => !conditionIsUsable(entry.baseline) && entry.closest?.has_comparable_result);
      cards.append(metricCard(
        "Recovered baseline failures",
        String(recoveries.length),
        recoveries.length ? "Another tested condition produced a comparable result." : "No failed baseline was rescued by another condition.",
      ));
    }

    const wrapper = element("div", {className: "table-responsive"});
    const table = element("table", {className: "table align-middle overview-table"});
    const headRow = element("tr", {}, [element("th", {scope: "col", text: "Model"})]);
    tasks().forEach((task) => headRow.append(element("th", {scope: "col", text: taskTitle(task)})));
    const body = element("tbody");
    const taskRanges = new Map(tasks().map((taskId) => {
      const field = state.taskMeta.get(taskId)?.headline?.field || null;
      const values = models().map((model) => aggregate(model, taskId, "headline", field)).filter((value) => value !== null);
      return [taskId, {minimum: Math.min(...values), maximum: Math.max(...values)}];
    }));
    models().forEach((model) => {
      const row = element("tr", {}, [element("th", {scope: "row", text: model})]);
      tasks().forEach((taskId) => {
        const meta = state.taskMeta.get(taskId);
        const value = aggregate(model, taskId, "headline", meta?.headline?.field || null);
        const {minimum, maximum} = taskRanges.get(taskId);
        const normalized = value === null || maximum === minimum ? 0.5 : (value - minimum) / (maximum - minimum);
        const favorable = meta?.headline?.better === "lower" ? 1 - normalized : normalized;
        const level = value === null ? 0 : Math.max(1, Math.min(4, Math.ceil(favorable * 4)));
        const failures = failedItems(model, taskId);
        const conditionId = activeCondition(model, taskId);
        const summary = conditionSummary(model, taskId, conditionId);
        const allSummaries = pairSummaries(model, taskId);
        const validConditions = allSummaries.filter(conditionIsUsable);
        const cell = element("td", {
          className: `heat-cell ${value === null ? "heat-error" : `heat-${level}`}`,
          title: failures[0]?.error_message || "",
        });
        if (value === null) {
          const closest = closestEntry(model, taskId);
          const selectedLabel = conditionId === "baseline" ? "Baseline failed" : "Selected condition failed";
          cell.append(
            element("strong", {className: "overview-outcome", text: state.resultMode === "closest" ? "No valid result" : selectedLabel}),
            element("span", {className: "condition-chip", text: conditionName(conditionId)}),
            element("span", {className: "overview-detail", text: failureSummary(jobItems(model, taskId))}),
          );
          if (validConditions.length && closest) {
            cell.append(element("span", {
              className: "overview-recovery",
              text: `${validConditions.length} of ${allSummaries.length} conditions were usable; closest was ${conditionName(closest.condition_id)}.`,
            }));
          } else if (allSummaries.length) {
            cell.append(element("span", {className: "overview-detail", text: `All ${allSummaries.length} tested conditions failed.`}));
          }
        } else {
          const coverage = summary?.comparable_response_rate ?? aggregate(model, taskId, "comparable_response_rate");
          cell.append(
            element("strong", {className: "overview-outcome", text: formatHeadline(taskId, value)}),
            element("span", {className: "overview-metric", text: headlineLabel(taskId)}),
            element("span", {className: "condition-chip", text: conditionName(conditionId)}),
            element("span", {className: "overview-detail", text: coverage === null ? "Coverage unavailable" : `${formatNumber(coverage * 100, 1)}% comparable responses`}),
          );
          const deviation = aggregate(model, taskId, "headline_stddev", meta?.headline?.field || null);
          if (deviation !== null) cell.append(element("span", {className: "overview-detail", text: `Across-repetition SD ${formatNumber(deviation, 3)}`}));
          const comparison = baselineComparison(model, taskId, value);
          if (comparison) cell.append(element("span", {className: "overview-baseline", text: comparison}));
        }
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
      const conditionId = activeCondition(model, task);
      const summary = conditionSummary(model, task, conditionId);
      const estimated = state.items.some((item) => item.model_id === model && item.task_id === task && item.condition_id === conditionId && item.tokens_estimated);
      const source = state.detail.models.find((entry) => entry.model_id === model)?.pricing_source || "unknown";
      const headline = aggregate(model, task, "headline", state.taskMeta.get(task)?.headline?.field || null);
      const coverage = summary?.comparable_response_rate ?? aggregate(model, task, "comparable_response_rate");
      return {
        model_id: model,
        task_id: task,
        condition_id: conditionId,
        condition: conditionName(conditionId),
        headline,
        coverage,
        outcome_status: headline === null ? "No valid result" : coverage === 1 ? "Comparable" : "Partial coverage",
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
      const left = a[key];
      const right = b[key];
      if (left === null || left === undefined) return right === null || right === undefined ? 0 : 1;
      if (right === null || right === undefined) return -1;
      return (typeof left === "string" ? left.localeCompare(right) : left - right) * (ascending ? 1 : -1);
    });
    const columns = [
      ["model_id", "Model"], ["task_id", "Task"], ["condition", "Configuration"], ["headline", "Gold comparison"],
      ["coverage", "Comparable"], ["outcome_status", "Outcome"], ["cost", "Estimated cost"], ["input", "Input tokens"], ["output", "Output tokens"],
      ["mean", "Mean latency", metricDefinitions.latency], ["p95", "P95 latency", metricDefinitions.p95],
    ];
    const table = element("table", {className: "table align-middle"});
    const head = element("tr");
    columns.forEach(([columnKey, label, definition]) => {
      const button = element("button", {className: "sort-button", type: "button", text: label, onclick: () => {
        state.efficiencySort = {key: columnKey, ascending: key === columnKey ? !ascending : true};
        renderEfficiency();
      }});
      const heading = element("th", {scope: "col"}, button);
      if (definition) heading.append(helpButton(label, definition));
      head.append(heading);
    });
    const body = element("tbody");
    rows.forEach((row) => {
      const estimated = row.estimated ? " est." : "";
      body.append(element("tr", {}, [
        element("th", {scope: "row", text: row.model_id}),
        element("td", {text: taskTitle(row.task_id)}),
        element("td", {}, element("span", {className: "condition-chip", text: row.condition})),
        element("td", {}, [
          element("strong", {text: formatHeadline(row.task_id, row.headline)}),
          element("span", {className: "d-block small text-secondary", text: headlineLabel(row.task_id)}),
        ]),
        element("td", {text: row.coverage === null ? "n/a" : `${formatNumber(row.coverage * 100, 1)}%`}),
        element("td", {}, element("span", {className: `outcome-pill ${row.headline === null ? "outcome-error" : row.coverage === 1 ? "outcome-ok" : "outcome-warning"}`, text: row.outcome_status})),
        element("td", {text: `${formatCurrency(row.cost)} · ${row.source.replaceAll("_", " ")}`, title: `Estimated cost source: ${row.source}`}),
        element("td", {text: `${formatNumber(row.input, 0)}${estimated}`}),
        element("td", {text: `${formatNumber(row.output, 0)}${estimated}`}),
        element("td", {text: row.mean === null ? "n/a" : `${formatNumber(row.mean, 1)} ms`}),
        element("td", {text: row.p95 === null ? "n/a" : `${formatNumber(row.p95, 1)} ms`}),
      ]));
    });
    table.append(element("thead", {}, head), body);
    document.getElementById("efficiency-table").replaceChildren(element("div", {className: "table-responsive"}, table));
    renderEfficiencyCharts(rows);
  }

  function chartColors() {
    const styles = getComputedStyle(document.documentElement);
    return {teal: styles.getPropertyValue("--teal").trim(), coral: styles.getPropertyValue("--coral").trim(), ink: styles.getPropertyValue("--ink").trim(), line: styles.getPropertyValue("--line").trim()};
  }

  function renderEfficiencyCharts(rows) {
    if (!window.Chart) return;
    state.efficiencyCharts.forEach((chart) => chart.destroy());
    state.efficiencyCharts = [];
    const container = document.getElementById("efficiency-charts");
    container.replaceChildren();
    const colors = chartColors();
    tasks().forEach((taskId, index) => {
      const direction = state.taskMeta.get(taskId)?.headline?.better === "higher" ? "Higher is closer to gold." : "Lower is closer to gold.";
      const panel = element("section", {className: "chart-panel"}, [
        element("div", {className: "chart-heading"}, [
          element("h3", {className: "h5", text: `${taskTitle(taskId)} · performance vs. cost`}),
          element("p", {text: `${headlineLabel(taskId)}. ${direction}`}),
        ]),
      ]);
      const points = rows.filter((row) => row.task_id === taskId && row.cost !== null && row.headline !== null).map((row) => ({
        x: row.cost,
        y: row.headline,
        model: row.model_id,
        condition: row.condition,
        coverage: row.coverage,
      }));
      if (!points.length) {
        panel.append(element("p", {className: "empty-chart", text: "No selected configuration produced both a comparable result and known estimated cost."}));
        container.append(panel);
        return;
      }
      const frame = element("div", {className: "chart-frame"});
      const canvas = element("canvas", {role: "img", "aria-label": `${taskTitle(taskId)} gold comparison versus estimated cost`});
      frame.append(canvas);
      panel.append(frame);
      container.append(panel);
      const chart = new Chart(canvas, {
        type: "scatter",
        data: {datasets: [{
          label: taskTitle(taskId),
          data: points,
          backgroundColor: index % 2 ? colors.coral : colors.teal,
          pointRadius: 6,
        }]},
        options: {responsive: true, maintainAspectRatio: false, plugins: {tooltip: {callbacks: {label: (context) => `${context.raw.model} · ${context.raw.condition}: ${formatCurrency(context.raw.x)}, ${formatHeadline(taskId, context.raw.y)}, ${formatNumber((context.raw.coverage || 0) * 100, 1)}% comparable`}}}, scales: {x: {title: {display: true, text: "Estimated cost (USD)"}, grid: {color: colors.line}, ticks: {color: colors.ink}}, y: {title: {display: true, text: headlineLabel(taskId)}, grid: {color: colors.line}, ticks: {color: colors.ink}}}},
      });
      state.efficiencyCharts.push(chart);
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

  function modelDetailHeader(modelId, taskId) {
    const conditionId = activeCondition(modelId, taskId);
    const summary = conditionSummary(modelId, taskId, conditionId);
    const headline = aggregate(modelId, taskId, "headline", state.taskMeta.get(taskId)?.headline?.field || null);
    const coverage = summary?.comparable_response_rate ?? aggregate(modelId, taskId, "comparable_response_rate");
    const chips = [element("span", {className: "condition-chip", text: conditionName(conditionId)})];
    chips.push(element("span", {
      className: `outcome-pill ${headline === null ? "outcome-error" : coverage === 1 ? "outcome-ok" : "outcome-warning"}`,
      text: headline === null ? "No valid result" : coverage === 1 ? "Comparable" : "Partial coverage",
    }));
    if (coverage !== null) chips.push(element("span", {className: "coverage-chip", text: `${formatNumber(coverage * 100, 1)}% comparable responses`}));
    const explanation = [];
    if (state.detail.ablation?.enabled && state.resultMode === "closest") {
      const closest = closestEntry(modelId, taskId);
      explanation.push(closest?.has_comparable_result
        ? `Selected from ${closest.valid_condition_count} usable condition${closest.valid_condition_count === 1 ? "" : "s"} out of ${closest.condition_count} tested.`
        : `None of the ${closest?.condition_count || pairSummaries(modelId, taskId).length} tested conditions produced a gold-comparable result.`);
    }
    if (headline !== null) {
      explanation.push(`${headlineLabel(taskId)}: ${formatHeadline(taskId, headline)}.`);
      const baseline = baselineComparison(modelId, taskId, headline);
      if (baseline) explanation.push(baseline);
    }
    return element("header", {className: "detail-model-heading"}, [
      element("div", {}, [element("h3", {className: "h5 mb-2", text: modelId}), element("div", {className: "detail-chips"}, chips)]),
      explanation.length ? element("p", {text: explanation.join(" ")}) : null,
    ]);
  }

  function emptySelectionNotice(modelId, taskId) {
    const summaries = pairSummaries(modelId, taskId);
    const valid = summaries.filter(conditionIsUsable);
    const closest = closestEntry(modelId, taskId);
    const selected = conditionName(activeCondition(modelId, taskId));
    const detail = valid.length && closest
      ? `${selected} failed, but ${valid.length} of ${summaries.length} tested conditions were usable. The closest valid observed condition is ${conditionName(closest.condition_id)}.`
      : summaries.length
        ? `No gold-comparable answer was returned under any of the ${summaries.length} tested conditions.`
        : "This configuration did not return a gold-comparable answer.";
    return element("div", {className: "selection-empty", role: "status"}, [
      element("strong", {text: "No comparable task detail for this configuration"}),
      element("p", {text: detail}),
    ]);
  }

  function renderNumericDetail(taskId) {
    const container = document.getElementById("task-detail-view");
    container.replaceChildren();
    const taskItems = models().flatMap((model) => jobItems(model, taskId)).filter((item) => item.status === "ok");
    models().forEach((model) => {
      const rows = taskItems.filter((item) => item.model_id === model);
      const block = element("section", {className: "detail-model-block"}, [modelDetailHeader(model, taskId)]);
      if (!rows.length) {
        block.append(emptySelectionNotice(model, taskId));
        appendFailureNotice(block, failedItems(model, taskId));
        container.append(block);
        return;
      }
      const table = element("table", {className: "table align-middle"});
      const head = element("tr", {}, [element("th", {scope: "col", text: "Item"}), element("th", {scope: "col", text: "Field"}), element("th", {scope: "col", text: "Model"}), element("th", {scope: "col", text: "Gold"}), element("th", {scope: "col", text: "Signed difference"})]);
      const body = element("tbody");
      rows.forEach((item) => Object.entries(item.comparison.details.fields).forEach(([field, detail]) => body.append(element("tr", {}, [element("th", {scope: "row", text: item.item_id}), element("td", {text: field}), element("td", {text: formatNumber(detail.model, 3)}), element("td", {text: formatNumber(detail.gold, 3)}), element("td", {text: formatNumber(detail.difference, 3)})]))));
      table.append(element("thead", {}, head), body);
      block.append(element("div", {className: "table-responsive"}, table));
      const agreement = renderAgreementSection(model, taskId);
      if (agreement) block.append(agreement);
      appendFailureNotice(block, failedItems(model, taskId));
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
    const taskItems = models().flatMap((model) => jobItems(model, taskId)).filter((item) => item.status === "ok");
    const tagset = state.taskMeta.get(taskId)?.view_descriptor?.chart?.categories || [];
    const chartDatasets = [];
    models().forEach((model, modelIndex) => {
      const rows = taskItems.filter((item) => item.model_id === model);
      const alignment = rows.filter((item) => item.comparison.statistics.alignment_problem).length;
      const ratioValues = rows.map((item) => item.comparison.details.derived_ratios).filter(Boolean);
      const agreement = aggregate(model, taskId, "token_agreement");
      const block = element("section", {className: "detail-model-block"}, [
        modelDetailHeader(model, taskId),
        element("div", {className: "row g-3 mb-3"}, [
          metricCard("Mean token agreement", agreement === null ? "n/a" : `${formatNumber(agreement * 100, 1)}%`),
          metricCard("Alignment problems", `${alignment} / ${rows.length}`),
          metricCard("Derived ratios", ratioValues.length ? jsonText(ratioValues[0]) : "n/a"),
        ]),
      ]);
      if (!rows.length) {
        block.querySelector(".row")?.remove();
        block.append(emptySelectionNotice(model, taskId));
        appendFailureNotice(block, failedItems(model, taskId));
        container.append(block);
        return;
      }
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
      const agreementPanel = renderAgreementSection(model, taskId);
      if (agreementPanel) block.append(agreementPanel);
      appendFailureNotice(block, failedItems(model, taskId));
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

  function displayLabel(label) {
    if (specialLabelNames[label]) return specialLabelNames[label];
    if (label === "not_word") return "Not word";
    if (label === "not_ciu") return "Word, not CIU";
    if (label === "ciu") return "CIU";
    return label;
  }

  function metricHeading(label, definition) {
    const heading = element("th", {scope: "col"}, [label]);
    if (definition) heading.append(helpButton(label, definition));
    return heading;
  }

  function formatPercent(value) {
    return value === null ? "n/a" : `${formatNumber(value * 100, 1)}%`;
  }

  function classificationAggregates(modelId, taskId, statName) {
    const conditionId = activeCondition(modelId, taskId);
    return state.detail.aggregates.filter((entry) => entry.model_id === modelId && entry.task_id === taskId && entry.condition_id === conditionId && entry.stat_name === statName);
  }

  function renderAgreementSection(modelId, taskId) {
    const declaredLabels = state.taskMeta.get(taskId)?.view_descriptor?.classification_labels || [];
    if (!declaredLabels.length || aggregate(modelId, taskId, "classification_observations") === null) return null;
    const confusionEntries = classificationAggregates(modelId, taskId, "confusion");
    const predictedLabels = new Set(confusionEntries.map((entry) => entry.field?.split(" → ")[1]).filter(Boolean));
    const labels = declaredLabels.filter((label) => (aggregate(modelId, taskId, "support", label) || 0) > 0 || predictedLabels.has(label));
    const rawAgreementName = taskId === "ciu_scoring" ? "token_label_agreement" : "token_agreement";
    const section = element("section", {className: "agreement-section", "aria-label": `${modelId} label agreement`});
    section.append(element("div", {className: "agreement-heading"}, [
      element("div", {}, [
        element("h4", {className: "h6 mb-0", text: "Per-label agreement"}),
        element("p", {text: "Pooled token decisions for the selected condition. Failed or unparseable jobs are excluded."}),
      ]),
      element("a", {href: "/guide#agreement", className: "small", text: "How to read these metrics"}),
    ]));
    section.append(element("div", {className: "row g-3 mb-3"}, [
      metricCard("Raw agreement", formatPercent(aggregate(modelId, taskId, rawAgreementName)), "Exact token-label matches", metricDefinitions.agreement),
      metricCard("Macro F1", formatPercent(aggregate(modelId, taskId, "macro_f1")), "Equal weight per represented label", metricDefinitions.macroF1),
      metricCard("Token decisions", formatNumber(aggregate(modelId, taskId, "classification_observations"), 0), "Includes explicit alignment-error buckets"),
    ]));

    const table = element("table", {className: "table align-middle agreement-table"});
    table.append(element("thead", {}, element("tr", {}, [
      element("th", {scope: "col", text: "Label"}),
      metricHeading("Precision", metricDefinitions.precision),
      metricHeading("Recall", metricDefinitions.recall),
      metricHeading("F1", metricDefinitions.f1),
      metricHeading("Support", metricDefinitions.support),
    ])));
    const body = element("tbody");
    labels.forEach((label) => body.append(element("tr", {}, [
      element("th", {scope: "row", text: displayLabel(label)}),
      element("td", {text: formatPercent(aggregate(modelId, taskId, "precision", label))}),
      element("td", {text: formatPercent(aggregate(modelId, taskId, "recall", label))}),
      element("td", {text: formatPercent(aggregate(modelId, taskId, "f1", label))}),
      element("td", {text: formatNumber(aggregate(modelId, taskId, "support", label), 0)}),
    ])));
    table.append(body);
    section.append(element("div", {className: "table-responsive"}, table));
    const matrix = renderConfusionMatrix(declaredLabels, confusionEntries);
    if (matrix) section.append(matrix);
    const repetitions = aggregate(modelId, taskId, "classification_repetitions");
    if (repetitions > 1) section.append(renderRepetitionSummary(modelId, taskId, labels, repetitions));
    return section;
  }

  function renderConfusionMatrix(declaredLabels, entries) {
    if (!entries.length) return null;
    const pairs = entries.map((entry) => {
      const [gold, predicted] = entry.field.split(" → ");
      return {gold, predicted, value: entry.value};
    });
    const order = [...declaredLabels, "__missing__", "__invalid__", "__duplicate__", "__extra__"];
    const rank = (label) => {
      const index = order.indexOf(label);
      return index < 0 ? order.length : index;
    };
    const goldLabels = [...new Set(pairs.map((pair) => pair.gold))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const predictedLabels = [...new Set(pairs.map((pair) => pair.predicted))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const lookup = new Map(pairs.map((pair) => [`${pair.gold}\u0000${pair.predicted}`, pair.value]));
    const table = element("table", {className: "table confusion-table"});
    table.append(element("thead", {}, element("tr", {}, [
      element("th", {scope: "col", text: "Gold ↓ / Model →"}),
      ...predictedLabels.map((label) => element("th", {scope: "col", text: displayLabel(label)})),
    ])));
    const body = element("tbody");
    goldLabels.forEach((gold) => body.append(element("tr", {}, [
      element("th", {scope: "row", text: displayLabel(gold)}),
      ...predictedLabels.map((predicted) => element("td", {
        className: gold === predicted ? "confusion-diagonal" : "",
        text: formatNumber(lookup.get(`${gold}\u0000${predicted}`) || 0, 0),
      })),
    ])));
    table.append(body);
    return element("div", {className: "confusion-wrap"}, [
      element("h5", {className: "h6"}, ["Confusion matrix", helpButton("Confusion matrix", metricDefinitions.confusion)]),
      element("div", {className: "table-responsive"}, table),
    ]);
  }

  function renderRepetitionSummary(modelId, taskId, labels, repetitions) {
    const table = element("table", {className: "table agreement-table"});
    table.append(element("thead", {}, element("tr", {}, [
      element("th", {scope: "col", text: "Label"}),
      element("th", {scope: "col", text: "Precision mean ± SD"}),
      element("th", {scope: "col", text: "Recall mean ± SD"}),
      element("th", {scope: "col", text: "F1 mean ± SD"}),
    ])));
    const body = element("tbody");
    labels.forEach((label) => {
      const cells = [element("th", {scope: "row", text: displayLabel(label)})];
      ["precision", "recall", "f1"].forEach((metric) => {
        const mean = aggregate(modelId, taskId, `${metric}_repetition_mean`, label);
        const deviation = aggregate(modelId, taskId, `${metric}_repetition_stddev`, label);
        cells.push(element("td", {text: mean === null ? "n/a" : `${formatPercent(mean)} ± ${formatPercent(deviation)}`}));
      });
      body.append(element("tr", {}, cells));
    });
    table.append(body);
    return element("details", {className: "repetition-details"}, [
      element("summary", {text: `Stability across ${formatNumber(repetitions, 0)} comparable repetitions`}),
      element("p", {text: "Mean summarizes repeated runs; SD shows variability. Undefined label metrics are omitted from that metric's repetition summary."}),
      element("div", {className: "table-responsive"}, table),
    ]);
  }

  function appendFailureNotice(container, failures) {
    if (!failures.length) return;
    const list = element("ul", {className: "failure-list"});
    failures.forEach((item) => list.append(element("li", {}, [
      element("strong", {text: `${item.item_id} · ${item.status.replaceAll("_", " ")}`}),
      element("span", {text: item.error_message || "No diagnostic message was returned."}),
    ])));
    container.append(element("section", {className: "failure-panel", role: "status"}, [
      element("h4", {className: "h6", text: "Unsuccessful jobs"}),
      list,
    ]));
  }

  function configureAblationControls() {
    const enabled = Boolean(state.detail.ablation?.enabled);
    document.getElementById("ablation-tab-item").classList.toggle("d-none", !enabled);
    const control = document.getElementById("result-mode-control");
    control.classList.toggle("d-none", !enabled);
    const context = document.getElementById("result-context");
    if (!enabled) {
      state.resultMode = "baseline";
      context.classList.add("d-none");
      context.replaceChildren();
      return;
    }
    const select = document.getElementById("result-mode-select");
    const options = [
      element("option", {value: "closest", text: "Closest valid observed · per model/task"}),
      element("option", {value: "baseline", text: `Baseline only · max tokens ${formatNumber(state.detail.ablation.baseline_value, 0)}`}),
      ...state.detail.ablation.conditions.filter((condition) => !condition.is_baseline).map((condition) => element("option", {
        value: `condition:${condition.condition_id}`,
        text: `Specific condition · max tokens ${formatNumber(condition.value, 0)}`,
      })),
    ];
    select.replaceChildren(...options);
    const validModes = options.map((option) => option.value);
    if (!validModes.includes(state.resultMode)) state.resultMode = "closest";
    select.value = state.resultMode;
    renderResultContext();
    select.onchange = () => {
      state.resultMode = select.value;
      renderResultContext();
      renderOverview();
      renderEfficiency();
      renderTaskDetail();
      renderRunInfo();
    };
  }

  function renderResultContext() {
    const context = document.getElementById("result-context");
    if (!state.detail?.ablation?.enabled) {
      context.classList.add("d-none");
      return;
    }
    context.classList.remove("d-none");
    if (state.resultMode === "closest") {
      context.replaceChildren(
        element("strong", {text: "Showing closest valid observed results."}),
        element("span", {text: "Each model/task cell may use a different tested configuration. Comparable-response coverage is prioritized before closeness to gold, then estimated cost and latency. This selection is exploratory because it uses the displayed gold items."}),
      );
      return;
    }
    const conditionId = state.resultMode === "baseline" ? "baseline" : state.resultMode.slice(10);
    context.replaceChildren(
      element("strong", {text: `Showing ${conditionName(conditionId)}.`}),
      element("span", {text: "Overview, Efficiency, and Task Detail are restricted to this one condition. Other tested conditions remain available here and in the Ablation tab."}),
    );
  }

  function renderAblation() {
    const ablation = state.detail.ablation;
    if (!ablation?.enabled) return;
    document.getElementById("ablation-warning-text").textContent = ablation.same_dataset_warning;
    const summaries = ablation.summaries;
    const closest = new Set((ablation.closest_conditions || ablation.best_conditions).map((entry) => `${entry.model_id}\u0000${entry.task_id}\u0000${entry.condition_id}`));
    const totalJobs = summaries.reduce((sum, row) => sum + (row.items_run || 0), 0);
    const truncations = summaries.reduce((sum, row) => sum + (row.truncated_responses || 0), 0);
    const removed = summaries.reduce((sum, row) => sum + (row.optional_parameters_removed || 0), 0);
    const usableConditions = summaries.filter(conditionIsUsable).length;
    const cards = document.getElementById("ablation-cards");
    cards.replaceChildren(
      metricCard("Conditions", String(ablation.conditions.length), `${ablation.parameter.replaceAll("_", " ")} values`),
      metricCard("Usable model/task conditions", `${usableConditions} / ${summaries.length}`, "A usable condition returned a gold-comparable result."),
      metricCard("Ablation jobs", formatNumber(totalJobs, 0), "Failures remain in comparable-response denominators."),
      metricCard("Token-limit truncations", formatNumber(truncations, 0), "Reported with finish_reason = length."),
    );
    if (removed) cards.append(metricCard("Parameters removed", formatNumber(removed, 0), "Gateway rejected optional request parameters."));

    renderAblationRecommendations();

    const table = element("table", {className: "table align-middle"});
    table.append(element("thead", {}, element("tr", {}, [
      "Model", "Task", "Configuration", "Comparable", "Outcome", "Gold comparison", "Across-repetition SD", "Failures", "Estimated cost", "Mean latency", "Selection",
    ].map((label) => element("th", {scope: "col", text: label})))));
    const body = element("tbody");
    summaries.forEach((row) => {
      const key = `${row.model_id}\u0000${row.task_id}\u0000${row.condition_id}`;
      const selected = closest.has(key) && conditionIsUsable(row);
      const failures = (row.parse_failures || 0) + (row.errors || 0);
      const outcome = !conditionIsUsable(row) ? "No valid result" : row.comparable_response_rate === 1 ? "Comparable" : "Partial coverage";
      body.append(element("tr", {className: selected ? "ablation-best-row" : !conditionIsUsable(row) ? "ablation-error-row" : ""}, [
        element("th", {scope: "row", text: row.model_id}),
        element("td", {text: taskTitle(row.task_id)}),
        element("td", {}, element("span", {className: "condition-chip", text: conditionName(row.condition_id)})),
        element("td", {text: row.comparable_response_rate === null ? "n/a" : `${formatNumber(row.comparable_response_rate * 100, 1)}%`}),
        element("td", {}, element("span", {className: `outcome-pill ${!conditionIsUsable(row) ? "outcome-error" : row.comparable_response_rate === 1 ? "outcome-ok" : "outcome-warning"}`, text: outcome})),
        element("td", {}, [
          element("strong", {text: formatHeadline(row.task_id, row.headline)}),
          element("span", {className: "d-block small text-secondary", text: headlineLabel(row.task_id)}),
        ]),
        element("td", {text: formatNumber(row.headline_stddev, 3)}),
        element("td", {text: `${formatNumber(failures, 0)}${row.truncated_responses ? ` · ${formatNumber(row.truncated_responses, 0)} truncated` : ""}`}),
        element("td", {text: formatCurrency(row.estimated_cost_usd)}),
        element("td", {text: row.mean_latency_ms === null ? "n/a" : `${formatNumber(row.mean_latency_ms, 1)} ms`}),
        element("td", {text: selected ? "Closest valid observed" : row.is_baseline ? "Baseline" : "—"}),
      ]));
    });
    table.append(body);
    document.getElementById("ablation-table").replaceChildren(table);
    renderAblationCharts(summaries);
  }

  function renderAblationRecommendations() {
    const container = document.getElementById("ablation-recommendations");
    container.replaceChildren();
    const entries = state.detail.ablation.closest_conditions || [];
    entries.forEach((entry) => {
      const baseline = conditionSummary(entry.model_id, entry.task_id, "baseline");
      const card = element("article", {className: `recommendation-card${entry.has_comparable_result ? "" : " recommendation-failed"}`}, [
        element("div", {className: "recommendation-heading"}, [
          element("div", {}, [element("strong", {text: entry.model_id}), element("span", {text: taskTitle(entry.task_id)})]),
          element("span", {className: `outcome-pill ${entry.has_comparable_result ? "outcome-ok" : "outcome-error"}`, text: entry.has_comparable_result ? "Usable" : "No valid condition"}),
        ]),
      ]);
      if (entry.has_comparable_result) {
        card.append(
          element("strong", {className: "recommendation-condition", text: conditionName(entry.condition_id)}),
          element("div", {className: "recommendation-result"}, [
            element("strong", {text: formatHeadline(entry.task_id, entry.headline)}),
            element("span", {text: headlineLabel(entry.task_id)}),
          ]),
          element("p", {text: `${formatNumber((entry.comparable_response_rate || 0) * 100, 1)}% comparable responses · ${entry.valid_condition_count} of ${entry.condition_count} conditions usable.`}),
          element("p", {className: "recommendation-baseline", text: conditionIsUsable(baseline) ? baselineComparisonFor(entry.task_id, entry.headline, baseline.headline) : "Baseline failed; another tested condition produced a comparable result."}),
        );
      } else {
        const items = state.items.filter((item) => item.model_id === entry.model_id && item.task_id === entry.task_id);
        card.append(
          element("strong", {className: "recommendation-condition", text: `All ${entry.condition_count} conditions failed`}),
          element("p", {text: failureSummary(items)}),
          element("p", {className: "recommendation-baseline", text: "This model/task pair cannot be ranked against gold from this run."}),
        );
      }
      container.append(card);
    });
  }

  function baselineComparisonFor(taskId, selectedValue, baselineValue) {
    const better = state.taskMeta.get(taskId)?.headline?.better || "lower";
    const favorableChange = better === "higher" ? selectedValue - baselineValue : baselineValue - selectedValue;
    if (Math.abs(favorableChange) < Number.EPSILON) return "Matches the baseline gold-comparison result.";
    const magnitude = state.taskMeta.get(taskId)?.headline?.stat?.includes("agreement")
      ? `${formatNumber(Math.abs(favorableChange) * 100, 1)} percentage points`
      : formatNumber(Math.abs(favorableChange), 3);
    return `${favorableChange > 0 ? "Improves on" : "Is worse than"} baseline by ${magnitude}.`;
  }

  function renderAblationCharts(summaries) {
    if (!window.Chart) return;
    state.ablationCharts.forEach((chart) => chart.destroy());
    state.ablationCharts = [];
    const container = document.getElementById("ablation-task-charts");
    container.replaceChildren();
    const palette = ["#60a5fa", "#22d3ee", "#a78bfa", "#f59e0b", "#34d399", "#fb7185"];
    tasks().forEach((taskId) => {
      const taskSummaries = summaries.filter((row) => row.task_id === taskId);
      const direction = state.taskMeta.get(taskId)?.headline?.better === "higher" ? "Higher is closer to gold." : "Lower is closer to gold.";
      const grid = element("div", {className: "ablation-chart-grid"});
      const performance = ablationChartPanel(`${taskTitle(taskId)} · performance by token budget`, `${headlineLabel(taskId)}. ${direction}`, `${taskTitle(taskId)} performance across maximum-token conditions`);
      const pareto = ablationChartPanel(`${taskTitle(taskId)} · cost and performance`, `${headlineLabel(taskId)} against estimated cost. ${direction}`, `${taskTitle(taskId)} estimated cost and performance by condition`);
      grid.append(performance.panel, pareto.panel);
      container.append(grid);
      const taskModels = [...new Set(taskSummaries.map((row) => row.model_id))];
      const performanceDatasets = taskModels.map((modelId, index) => ({
        label: modelId,
        data: taskSummaries.filter((row) => row.model_id === modelId && row.headline !== null).sort((a, b) => a.value - b.value).map((row) => ({x: row.value, y: row.headline, condition: conditionName(row.condition_id), coverage: row.comparable_response_rate})),
        borderColor: palette[index % palette.length],
        backgroundColor: palette[index % palette.length],
        tension: 0.2,
      }));
      const paretoDatasets = taskModels.map((modelId, index) => ({
        label: modelId,
        data: taskSummaries.filter((row) => row.model_id === modelId && row.estimated_cost_usd !== null && row.headline !== null).map((row) => ({x: row.estimated_cost_usd, y: row.headline, condition: conditionName(row.condition_id), coverage: row.comparable_response_rate})),
        backgroundColor: palette[index % palette.length],
        pointRadius: 6,
      }));
      state.ablationCharts.push(new Chart(performance.canvas, {
        type: "line",
        data: {datasets: performanceDatasets},
        options: {responsive: true, maintainAspectRatio: false, parsing: false, plugins: {tooltip: {callbacks: {label: (context) => `${context.dataset.label} · ${context.raw.condition}: ${formatHeadline(taskId, context.raw.y)}, ${formatNumber((context.raw.coverage || 0) * 100, 1)}% comparable`}}}, scales: {x: {type: "linear", title: {display: true, text: "Maximum output tokens"}}, y: {title: {display: true, text: headlineLabel(taskId)}}}},
      }));
      state.ablationCharts.push(new Chart(pareto.canvas, {
        type: "scatter",
        data: {datasets: paretoDatasets},
        options: {responsive: true, maintainAspectRatio: false, parsing: false, plugins: {tooltip: {callbacks: {label: (context) => `${context.dataset.label} · ${context.raw.condition}: ${formatCurrency(context.raw.x)}, ${formatHeadline(taskId, context.raw.y)}, ${formatNumber((context.raw.coverage || 0) * 100, 1)}% comparable`}}}, scales: {x: {title: {display: true, text: "Estimated cost (USD)"}}, y: {title: {display: true, text: headlineLabel(taskId)}}}},
      }));
    });
  }

  function ablationChartPanel(title, description, ariaLabel) {
    const canvas = element("canvas", {role: "img", "aria-label": ariaLabel});
    return {
      canvas,
      panel: element("section", {className: "chart-panel"}, [
        element("div", {className: "chart-heading"}, [element("h3", {className: "h5", text: title}), element("p", {text: description})]),
        element("div", {className: "chart-frame"}, canvas),
      ]),
    };
  }

  function renderTokenAnalysis() {
    const modelSelect = document.getElementById("token-model-select");
    const itemSelect = document.getElementById("token-item-select");
    const conditionSelect = document.getElementById("token-condition-select");
    const repetitionSelect = document.getElementById("token-repetition-select");
    const viewSelect = document.getElementById("token-view-select");
    const currentModel = modelSelect.value;
    const currentItem = itemSelect.value;
    const currentCondition = conditionSelect.value;
    const currentRepetition = repetitionSelect.value;
    const itemIds = [...new Set(state.items.filter((item) => Array.isArray(item.input?.tokens)).map((item) => item.item_id))].sort();
    const repetitions = [...new Set(state.items.map((item) => item.repetition))].sort((a, b) => a - b);
    const conditions = state.detail.ablation?.enabled
      ? state.detail.ablation.conditions
      : [{condition_id: "baseline", label: "Baseline"}];
    modelSelect.replaceChildren(...models().map((model) => element("option", {value: model, text: model})));
    itemSelect.replaceChildren(...itemIds.map((item) => element("option", {value: item, text: item})));
    conditionSelect.replaceChildren(...conditions.map((condition) => element("option", {value: condition.condition_id, text: condition.label})));
    repetitionSelect.replaceChildren(...repetitions.map((repetition) => element("option", {value: repetition, text: String(repetition)})));
    if (models().includes(currentModel)) modelSelect.value = currentModel;
    if (itemIds.includes(currentItem)) itemSelect.value = currentItem;
    if (conditions.some((condition) => condition.condition_id === currentCondition)) conditionSelect.value = currentCondition;
    document.getElementById("token-condition-control").classList.toggle("d-none", !state.detail.ablation?.enabled);
    if (repetitions.includes(Number(currentRepetition))) repetitionSelect.value = currentRepetition;
    modelSelect.onchange = renderTokenAnalysisView;
    itemSelect.onchange = renderTokenAnalysisView;
    conditionSelect.onchange = renderTokenAnalysisView;
    repetitionSelect.onchange = renderTokenAnalysisView;
    viewSelect.onchange = renderTokenAnalysisView;
    renderTokenAnalysisView();
  }

  function renderTokenAnalysisView() {
    const container = document.getElementById("token-analysis-view");
    const model = document.getElementById("token-model-select").value;
    const itemId = document.getElementById("token-item-select").value;
    const conditionId = document.getElementById("token-condition-select").value || "baseline";
    const repetition = Number(document.getElementById("token-repetition-select").value);
    const view = document.getElementById("token-view-select").value;
    const candidates = state.items.filter((item) => item.model_id === model && item.item_id === itemId && item.condition_id === conditionId && item.repetition === repetition);
    const ciu = candidates.find((item) => item.task_id === "ciu_scoring");
    const pos = candidates.find((item) => item.task_id === "pos_breakdown");
    const source = ciu?.input?.tokens || pos?.input?.tokens;
    container.replaceChildren();
    if (!Array.isArray(source) || !source.length) {
      container.append(element("p", {className: "empty-state", text: "Token annotations are unavailable for this run or task version."}));
      return;
    }

    const ciuDetails = new Map((ciu?.comparison?.details?.tokens || []).map((row) => [row.i, row]));
    const posDetails = new Map((pos?.comparison?.details?.tokens || []).map((row) => [row.i, row]));
    const unavailable = [];
    if (ciu && ciu.status !== "ok") unavailable.push(`CIU: ${ciu.error_message || ciu.status}`);
    if (pos && pos.status !== "ok") unavailable.push(`POS: ${pos.error_message || pos.status}`);
    if (!ciu) unavailable.push("CIU task was not included in this run.");
    if (!pos) unavailable.push("POS task was not included in this run.");
    if (unavailable.length) {
      container.append(element("div", {className: "token-analysis-notice", role: "status"}, unavailable.map((message) => element("p", {text: message}))));
    }

    container.append(element("div", {className: "token-legend", "aria-label": "Token annotation legend"}, [
      annotationBadge("W · CIU", "ciu"),
      annotationBadge("W · non-CIU", "not-ciu"),
      annotationBadge("NW", "not-word"),
      annotationBadge("POS", "pos"),
      element("span", {className: "token-disagreement-key", text: "Outlined token = model disagrees with reference"}),
    ]));

    const utterances = new Map();
    source.forEach((token) => {
      const utterance = Number.isInteger(token.utterance) ? token.utterance : 1;
      if (!utterances.has(utterance)) utterances.set(utterance, []);
      utterances.get(utterance).push(token);
    });
    utterances.forEach((tokens, utterance) => {
      const line = element("div", {className: "token-line"});
      tokens.forEach((token) => line.append(renderAnnotatedToken(token, ciuDetails.get(token.i), posDetails.get(token.i), view)));
      container.append(element("section", {className: "utterance-card"}, [
        element("h3", {className: "utterance-label", text: `Utterance ${utterance}`}),
        line,
      ]));
    });
  }

  function renderAnnotatedToken(token, ciuDetail, posDetail, view) {
    const ciuLabel = view === "gold" ? ciuDetail?.gold : ciuDetail?.model;
    const posLabel = view === "gold" ? posDetail?.gold : posDetail?.model;
    const disagreement = view === "diff" && ((ciuDetail && !ciuDetail.agreed) || (posDetail && !posDetail.agreed));
    const ciuText = ciuLabel === "ciu" ? "W · CIU" : ciuLabel === "not_ciu" ? "W · non-CIU" : ciuLabel === "not_word" ? "NW" : "CIU —";
    const ciuClass = ciuLabel === "ciu" ? "ciu" : ciuLabel === "not_ciu" ? "not-ciu" : ciuLabel === "not_word" ? "not-word" : "unavailable";
    const titleParts = [];
    if (view === "diff" && ciuDetail) titleParts.push(`CIU reference: ${ciuDetail.gold}; model: ${ciuDetail.model}`);
    if (view === "diff" && posDetail) titleParts.push(`POS reference: ${posDetail.gold}; model: ${posDetail.model}`);
    return element("span", {
      className: `annotated-token${disagreement ? " token-disagreement" : ""}`,
      title: titleParts.join(" | "),
    }, [
      element("span", {className: "token-index", text: token.i}),
      element("strong", {className: "token-text", text: token.token}),
      element("span", {className: "token-labels"}, [
        annotationBadge(ciuText, ciuClass),
        annotationBadge(posLabel || "POS —", posLabel ? "pos" : "unavailable"),
      ]),
    ]);
  }

  function annotationBadge(text, kind) {
    return element("span", {className: `annotation-badge annotation-${kind}`, text});
  }

  function filteredItems() {
    return state.items.filter((item) => {
      if (document.getElementById("filter-disagreements").checked && !(item.status === "ok" && item.matched === false)) return false;
      if (document.getElementById("filter-failures").checked && item.status === "ok") return false;
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
      const conditionText = state.detail.ablation?.enabled ? ` · ${item.condition.label}` : "";
      const metadata = element("div", {}, [element("strong", {text: `${item.item_id} · ${item.model_id}`}), element("span", {className: "d-block small text-secondary", text: `${taskTitle(item.task_id)}${conditionText} · repetition ${item.repetition}${item.tokens_estimated ? " · token counts est." : ""}`})]);
      const body = element("div", {className: "item-result-body"}, [dataBlock("Input", item.input), dataBlock("Gold", item.gold), dataBlock("Model answer", item.parsed_output)]);
      if (item.status !== "ok") body.prepend(element("p", {className: "result-error-message", text: item.error_message || "This job did not return a comparable result."}));
      const details = element("details", {className: "raw-output"}, [element("summary", {text: "Raw output and comparison details"}), dataBlock("Raw model output", item.raw_output), dataBlock("Response diagnostics", item.comparison?.diagnostics), dataBlock("Comparison", item.comparison), item.error_message ? element("p", {className: "text-danger mt-3", text: item.error_message}) : null]);
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
    configureAblationControls();
    renderOverview();
    renderEfficiency();
    renderTaskSelector();
    renderTokenAnalysis();
    renderAblation();
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
  ["filter-disagreements", "filter-failures", "filter-alignment"].forEach((id) => document.getElementById(id).addEventListener("change", () => { state.itemPage = 1; renderItems(); }));
  window.addEventListener("beforeunload", () => {
    window.clearTimeout(state.refreshTimer);
    state.source?.close();
  });
  load();
})();
