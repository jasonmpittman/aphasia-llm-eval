(function () {
  "use strict";

  if (document.body.dataset.page !== "history") return;
  const {api, clearAlert, element, formatDate, formatNumber, showAlert, statusBadge} = window.AphasiaEval;
  const alertNode = document.getElementById("history-alert");
  const body = document.getElementById("history-body");
  const empty = document.getElementById("history-empty");
  const count = document.getElementById("history-count");
  const renameElement = document.getElementById("rename-modal");
  const renameModal = bootstrap.Modal.getOrCreateInstance(renameElement);
  const state = {runs: [], details: new Map(), tasks: new Map()};

  function tags(values, label) {
    const list = element("div", {className: "tag-list", "aria-label": label});
    values.forEach((value) => list.append(element("span", {className: "badge mini-badge", text: value})));
    return list;
  }

  function headline(detail) {
    const values = [];
    detail.tasks.forEach(({task_id: taskId}) => {
      const meta = state.tasks.get(taskId);
      const field = meta?.headline?.field || null;
      detail.models.forEach(({model_id: modelId}) => {
        const aggregate = detail.aggregates.find((entry) => entry.model_id === modelId && entry.task_id === taskId && entry.stat_name === "headline" && entry.field === field);
        if (aggregate) values.push(`${modelId} · ${meta?.title || taskId}: ${formatNumber(aggregate.value, 3)}`);
      });
    });
    if (!values.length) return element("span", {className: "text-secondary", text: "No headline results"});
    return tags(values, "Headline results");
  }

  function runLabel(run) {
    return run.label || `Run ${run.id.slice(0, 8)}`;
  }

  function openRename(run) {
    document.getElementById("rename-run-id").value = run.id;
    document.getElementById("rename-label").value = run.label || "";
    renameModal.show();
    renameElement.addEventListener("shown.bs.modal", () => document.getElementById("rename-label").focus(), {once: true});
  }

  async function removeRun(run) {
    if (!window.confirm(`Delete “${runLabel(run)}”? This removes the local run and all of its results.`)) return;
    clearAlert(alertNode);
    try {
      await api(`/api/runs/${encodeURIComponent(run.id)}`, {method: "DELETE", body: {}});
      state.runs = state.runs.filter((candidate) => candidate.id !== run.id);
      state.details.delete(run.id);
      render();
    } catch (error) {
      showAlert(alertNode, error.message);
    }
  }

  function actions(run) {
    const group = element("div", {className: "btn-group btn-group-sm", role: "group", "aria-label": `Actions for ${runLabel(run)}`});
    group.append(
      element("a", {className: "btn btn-outline-primary", href: `/runs/${encodeURIComponent(run.id)}`, text: "Open"}),
      element("button", {className: "btn btn-outline-secondary", type: "button", text: "Rename", onclick: () => openRename(run)}),
      element("button", {className: "btn btn-outline-danger", type: "button", text: "Delete", onclick: () => removeRun(run)}),
    );
    return group;
  }

  function render() {
    body.replaceChildren();
    count.textContent = `${state.runs.length} run${state.runs.length === 1 ? "" : "s"}`;
    empty.classList.toggle("d-none", state.runs.length > 0);
    state.runs.forEach((run) => {
      const detail = state.details.get(run.id);
      const identity = element("td", {}, [
        element("a", {className: "history-run-label", href: `/runs/${encodeURIComponent(run.id)}`, text: runLabel(run)}),
        element("span", {className: "history-run-date", text: formatDate(run.created_at)}),
      ]);
      body.append(element("tr", {}, [
        identity,
        element("td", {}, tags(run.models, "Models")),
        element("td", {}, tags(run.tasks.map((taskId) => state.tasks.get(taskId)?.title || taskId), "Tasks")),
        element("td", {}, detail ? headline(detail) : element("span", {className: "text-secondary", text: "Unavailable"})),
        element("td", {}, statusBadge(run.status)),
        element("td", {className: "text-end"}, actions(run)),
      ]));
    });
  }

  async function load() {
    clearAlert(alertNode);
    try {
      const [runData, taskData] = await Promise.all([api("/api/runs"), api("/api/tasks")]);
      state.runs = runData.runs;
      taskData.tasks.forEach((task) => state.tasks.set(task.task_id, task));
      const details = await Promise.all(state.runs.map(async (run) => {
        try { return [run.id, await api(`/api/runs/${encodeURIComponent(run.id)}`)]; }
        catch (_error) { return [run.id, null]; }
      }));
      details.forEach(([runId, detail]) => { if (detail) state.details.set(runId, detail); });
      render();
    } catch (error) {
      count.textContent = "Unable to load";
      showAlert(alertNode, error.message);
    }
  }

  document.getElementById("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const runId = document.getElementById("rename-run-id").value;
    const label = document.getElementById("rename-label").value.trim() || null;
    clearAlert(alertNode);
    try {
      await api(`/api/runs/${encodeURIComponent(runId)}`, {method: "PATCH", body: {label}});
      const run = state.runs.find((candidate) => candidate.id === runId);
      if (run) run.label = label;
      renameModal.hide();
      render();
    } catch (error) {
      renameModal.hide();
      showAlert(alertNode, error.message);
    }
  });

  load();
})();
