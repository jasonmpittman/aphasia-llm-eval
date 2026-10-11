# Task definitions

Each YAML file defines a task's inputs, prompt, JSON output schema, derived
values, comparator, and headline statistic. Files are validated against
`schema/task.schema.json`; `task_id` must match the filename stem. See
[`docs/adding-a-task.md`](../docs/adding-a-task.md) for the configuration
walkthrough and [`docs/comparison.md`](../docs/comparison.md) for comparator
contracts.

Task prompts must document the clinical conventions used to create their
matching gold files. The bundled task prompts intentionally retain explicit
placeholders until maintainers supply conventions that match verified gold.

Validate all tasks and their gold files with:

```console
python -m aphasia_eval.services.task_loader --validate
```

For the synthetic demonstration data, set
`GOLD_TASKS_DIR=gold_tasks/examples` before running that command.

The task loader discovers top-level `.yaml` and `.yml` files automatically. A
valid new task therefore appears in the UI without a route or template change.
Missing or invalid gold disables only that task and exposes its validation
errors through `/api/tasks`.
