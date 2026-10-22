(function () {
  "use strict";

  if (document.body.dataset.page !== "select") return;
  const {api, clearAlert, element, formatCurrency, showAlert} = window.AphasiaEval;
  const root = document.getElementById("main-content");
  const alertNode = document.getElementById("selection-alert");
  const modelList = document.getElementById("model-list");
  const taskList = document.getElementById("task-list");
  const startButton = document.getElementById("start-run");
  const confirmationThreshold = Number(root.dataset.confirmationThreshold || 250);
  const state = {models: [], tasks: [], submitting: false};
  const presetKey = "aphasia-llm-eval:model-presets:v1";

  function checkedValues(selector) {
    return [...document.querySelectorAll(selector)].filter((input) => input.checked).map((input) => input.value);
  }

  function renderModels() {
    const selected = new Set(checkedValues('input[name="models"]'));
    modelList.replaceChildren();
    const filter = document.getElementById("model-search").value.trim().toLowerCase();
    const showIncompatible = document.getElementById("show-incompatible-models").checked;
    state.models.forEach((model, index) => {
      if (!model.selectable && !showIncompatible) return;
      const wrapper = element("div", {className: "model-option"});
      if (!model.id.toLowerCase().includes(filter)) wrapper.classList.add("d-none");
      const input = element("input", {
        type: "checkbox",
        id: `model-${index}`,
        name: "models",
        value: model.id,
        checked: selected.has(model.id),
        disabled: !model.selectable,
        onchange: updateSummary,
      });
      const known = model.pricing_source !== "unknown";
      const badge = element("span", {
        className: `pricing-badge ${known ? "pricing-known" : "pricing-unknown"}`,
        text: known ? `Priced · ${model.pricing_source.replaceAll("_", " ")}` : "Price unknown",
      });
      const capabilityText = model.text_capability === "supported"
        ? "Text confirmed"
        : model.text_capability === "unsupported" ? "Not text-compatible" : "Text unverified";
      const capabilityBadge = element("span", {
        className: `capability-badge capability-${model.text_capability}`,
        text: capabilityText,
        title: model.capability_reason,
      });
      const label = element("label", {htmlFor: input.id}, [
        element("span", {className: "model-name", text: model.id}),
        element("span", {className: "model-badges"}, [capabilityBadge, badge]),
      ]);
      wrapper.append(input, label);
      if (!model.selectable) {
        wrapper.append(element("p", {className: "model-capability-reason", text: model.capability_reason}));
      }
      modelList.append(wrapper);
    });
  }

  function renderTasks() {
    taskList.replaceChildren();
    state.tasks.forEach((task, index) => {
      const column = element("div", {className: "col-md-6 col-xl-4"});
      const card = element("article", {className: `task-card${task.enabled ? "" : " disabled"}`});
      const input = element("input", {
        className: "form-check-input",
        type: "checkbox",
        id: `task-${index}`,
        name: "tasks",
        value: task.task_id,
        disabled: !task.enabled,
        onchange: () => {
          card.classList.toggle("selected", input.checked);
          updateSummary();
        },
      });
      const badge = element("span", {
        className: `gold-badge gold-${task.gold_status}`,
        text: task.gold_status === "found" ? "Gold ready" : `Gold ${task.gold_status}`,
      });
      const header = element("div", {className: "task-card-header"}, [
        element("div", {}, [
          element("h3", {text: task.title || task.task_id}),
          element("div", {className: "task-meta", text: `${task.category || "Unknown category"} · ${task.item_count} items`}),
        ]),
        badge,
      ]);
      const description = element("p", {className: "task-description", text: task.description || "Task configuration is invalid."});
      const selector = element("label", {className: "task-selector", htmlFor: input.id}, [
        input,
        element("span", {text: task.enabled ? "Include this task" : "Unavailable"}),
      ]);
      card.append(header, description, selector);
      if (!task.enabled) {
        card.append(element("p", {className: "disabled-reason mb-0", text: task.errors.join(" ")}));
      }
      column.append(card);
      taskList.append(column);
    });
  }

  function settings() {
    const rawLimit = document.getElementById("item-limit").value.trim();
    const maxTokens = Number(document.getElementById("max-tokens").value);
    const controls = {
      temperature: Number(document.getElementById("temperature").value),
      max_tokens: maxTokens,
      repetitions: Number(document.getElementById("repetitions").value),
      max_items_per_task: rawLimit ? Number(rawLimit) : null,
    };
    if (document.getElementById("ablation-enabled").checked) {
      const values = document.getElementById("ablation-values").value.split(",").filter((value) => value.trim()).map((value) => Number(value.trim()));
      controls.ablation = {parameter: "max_tokens", values};
    }
    return controls;
  }

  function selectionMetrics() {
    const modelIds = checkedValues('input[name="models"]');
    const taskIds = checkedValues('input[name="tasks"]');
    const selectedTasks = state.tasks.filter((task) => taskIds.includes(task.task_id));
    const selectedModels = state.models.filter((model) => modelIds.includes(model.id));
    const controls = settings();
    const conditionValues = controls.ablation
      ? [...new Set([controls.max_tokens, ...controls.ablation.values])]
      : [controls.max_tokens];
    const conditionsValid = !controls.ablation || (
      controls.ablation.values.length > 0
      && conditionValues.length >= 2
      && conditionValues.length <= 8
      && conditionValues.every((value) => Number.isInteger(value) && value > 0)
    );
    const items = selectedTasks.reduce((total, task) => {
      const count = controls.max_items_per_task === null ? task.item_count : Math.min(task.item_count, controls.max_items_per_task);
      return total + count;
    }, 0);
    return {
      modelIds,
      taskIds,
      selectedModels,
      controls,
      items,
      conditions: conditionValues.length,
      conditionsValid,
      requests: conditionsValid ? modelIds.length * items * controls.repetitions * conditionValues.length : Number.NaN,
    };
  }

  function roughCost(metrics) {
    if (!metrics.selectedModels.length || !metrics.items || !metrics.conditionsValid) return null;
    const values = metrics.controls.ablation
      ? [...new Set([metrics.controls.max_tokens, ...metrics.controls.ablation.values])]
      : [metrics.controls.max_tokens];
    const jobsPerCondition = metrics.items * metrics.controls.repetitions;
    let lower = 0;
    let upper = 0;
    let priced = 0;
    metrics.selectedModels.forEach((model) => {
      if (model.input_usd_per_1m_tokens === null || model.output_usd_per_1m_tokens === null) return;
      priced += 1;
      values.forEach((maxTokens) => {
        lower += jobsPerCondition * 500 * model.input_usd_per_1m_tokens / 1_000_000;
        upper += jobsPerCondition * (500 * model.input_usd_per_1m_tokens + maxTokens * model.output_usd_per_1m_tokens) / 1_000_000;
      });
    });
    return priced ? {lower, upper, priced, total: metrics.selectedModels.length} : null;
  }

  function updateSummary() {
    const metrics = selectionMetrics();
    document.getElementById("ablation-values").setCustomValidity(metrics.conditionsValid ? "" : "Enter positive integers for 2–8 distinct conditions including the baseline.");
    const selectableCount = state.models.filter((model) => model.selectable).length;
    document.getElementById("model-count").textContent = `${metrics.modelIds.length} of ${selectableCount} selectable models selected`;
    document.getElementById("task-count").textContent = `${metrics.taskIds.length} of ${state.tasks.filter((task) => task.enabled).length} available selected`;
    const equation = document.getElementById("run-equation");
    if (!metrics.conditionsValid) {
      equation.textContent = "Enter 2–8 distinct positive maximum-token conditions, including the baseline.";
    } else if (metrics.modelIds.length && metrics.taskIds.length && Number.isFinite(metrics.requests)) {
      equation.textContent = `${metrics.modelIds.length} model${metrics.modelIds.length === 1 ? "" : "s"} across ${metrics.taskIds.length} task${metrics.taskIds.length === 1 ? "" : "s"} · ${metrics.items} total items · ${metrics.conditions} condition${metrics.conditions === 1 ? "" : "s"} · ${metrics.controls.repetitions} repetition${metrics.controls.repetitions === 1 ? "" : "s"} = ${metrics.requests} requests`;
    } else {
      equation.textContent = "Select at least one model and task.";
    }
    const estimate = roughCost(metrics);
    const costNode = document.getElementById("run-cost");
    costNode.textContent = estimate
      ? `Estimated rough range: ${formatCurrency(estimate.lower)}–${formatCurrency(estimate.upper)} (${estimate.priced}/${estimate.total} models priced; assumes ~500 input tokens and the output cap).`
      : "Estimated cost: n/a until priced models are selected.";
    startButton.disabled = state.submitting || !metrics.modelIds.length || !metrics.taskIds.length || !metrics.requests;
  }

  function readPresets() {
    try {
      const value = JSON.parse(localStorage.getItem(presetKey) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (_error) {
      return {};
    }
  }

  function writePresets(presets) {
    localStorage.setItem(presetKey, JSON.stringify(presets));
  }

  function renderPresets(selected = "") {
    const select = document.getElementById("preset-select");
    select.replaceChildren(element("option", {value: "", text: "Choose preset…"}));
    Object.keys(readPresets()).sort().forEach((name) => select.append(element("option", {value: name, text: name})));
    select.value = selected;
  }

  function presetMessage(message) {
    document.getElementById("preset-message").textContent = message;
  }

  function setSelectedModels(modelIds) {
    document.querySelectorAll('input[name="models"]').forEach((input) => {
      input.checked = modelIds.includes(input.value);
    });
    updateSummary();
  }

  async function submitRun() {
    const metrics = selectionMetrics();
    state.submitting = true;
    startButton.disabled = true;
    startButton.textContent = "Creating run…";
    clearAlert(alertNode);
    try {
      const result = await api("/api/runs", {
        method: "POST",
        body: {
          models: metrics.modelIds,
          tasks: metrics.taskIds,
          settings: metrics.controls,
          label: document.getElementById("run-label").value.trim() || null,
        },
      });
      window.location.assign(`/runs/${encodeURIComponent(result.run_id)}`);
    } catch (error) {
      showAlert(alertNode, error.message);
      state.submitting = false;
      startButton.textContent = "Start evaluation";
      updateSummary();
    }
  }

  async function loadData() {
    try {
      const [modelData, taskData] = await Promise.all([api("/api/models"), api("/api/tasks")]);
      state.models = modelData.models;
      state.tasks = taskData.tasks;
      document.getElementById("model-loading").classList.add("d-none");
      document.getElementById("task-loading").classList.add("d-none");
      renderModels();
      renderTasks();
      if (modelData.error) showAlert(alertNode, `Gateway model discovery is degraded: ${modelData.error}`);
      updateSummary();
    } catch (error) {
      document.getElementById("model-loading").classList.add("d-none");
      document.getElementById("task-loading").classList.add("d-none");
      showAlert(alertNode, error.message);
    }
  }

  document.getElementById("model-search").addEventListener("input", renderModels);
  document.getElementById("show-incompatible-models").addEventListener("change", () => {
    renderModels();
    updateSummary();
  });
  document.getElementById("select-all-models").addEventListener("click", () => {
    document.querySelectorAll(".model-option:not(.d-none) input:not(:disabled)").forEach((input) => { input.checked = true; });
    updateSummary();
  });
  document.getElementById("clear-models").addEventListener("click", () => setSelectedModels([]));
  document.getElementById("select-all-tasks").addEventListener("click", () => {
    document.querySelectorAll('input[name="tasks"]:not(:disabled)').forEach((input) => {
      input.checked = true;
      input.closest(".task-card").classList.add("selected");
    });
    updateSummary();
  });
  document.getElementById("clear-tasks").addEventListener("click", () => {
    document.querySelectorAll('input[name="tasks"]').forEach((input) => {
      input.checked = false;
      input.closest(".task-card").classList.remove("selected");
    });
    updateSummary();
  });
  ["temperature", "max-tokens", "repetitions", "item-limit", "ablation-values"].forEach((id) => document.getElementById(id).addEventListener("input", updateSummary));
  document.getElementById("ablation-enabled").addEventListener("change", (event) => {
    document.getElementById("ablation-options").classList.toggle("d-none", !event.currentTarget.checked);
    updateSummary();
  });
  document.getElementById("save-preset").addEventListener("click", () => {
    const name = document.getElementById("preset-name").value.trim();
    const models = checkedValues('input[name="models"]');
    if (!name || !models.length) { presetMessage("Enter a name and select at least one model."); return; }
    const presets = readPresets();
    presets[name] = models;
    writePresets(presets);
    renderPresets(name);
    presetMessage(`Saved “${name}” on this browser.`);
  });
  document.getElementById("load-preset").addEventListener("click", () => {
    const name = document.getElementById("preset-select").value;
    if (name) setSelectedModels(readPresets()[name] || []);
  });
  document.getElementById("delete-preset").addEventListener("click", () => {
    const name = document.getElementById("preset-select").value;
    if (!name) return;
    const presets = readPresets();
    delete presets[name];
    writePresets(presets);
    renderPresets();
    presetMessage(`Deleted “${name}”.`);
  });
  document.getElementById("run-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const metrics = selectionMetrics();
    if (metrics.requests > confirmationThreshold) {
      document.getElementById("confirmation-description").textContent = `This evaluation will send ${metrics.requests} requests to the configured gateway.`;
      bootstrap.Modal.getOrCreateInstance(document.getElementById("confirmation-modal")).show();
    } else {
      submitRun();
    }
  });
  document.getElementById("confirm-run").addEventListener("click", () => {
    bootstrap.Modal.getOrCreateInstance(document.getElementById("confirmation-modal")).hide();
    submitRun();
  });

  renderPresets();
  loadData();
})();
