# Task definitions

Each YAML file defines a task's inputs, prompt, JSON output schema, derived
values, comparator, and headline statistic. Files are validated against
`schema/task.schema.json`; `task_id` must match the filename stem. See
[`docs/adding-a-task.md`](../docs/adding-a-task.md) for the configuration
walkthrough and [`docs/comparison.md`](../docs/comparison.md) for comparator
contracts.

Task prompts must document the clinical or linguistic conventions used to
create their matching reference files. The bundled CIU prompt reflects the
maintainer-supplied clinical standard. The POS prompt reflects the documented
UPOS conventions used by the provisional Cat Rescue development reference;
that reference must be independently reviewed or replaced before it is
described as an authoritative gold standard.

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

Closely related tasks may declare `extends: <sibling_task_id>`. The loader
shallow-merges the sibling before validation and includes both files in the
task provenance hash. The CIU stimulus tasks use `ciu_scoring` as their shared
prompt/schema definition while retaining independent task IDs and gold files.
