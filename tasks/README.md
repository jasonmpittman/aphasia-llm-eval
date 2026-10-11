# Task definitions

Each YAML file defines a task's inputs, prompt, JSON output schema, derived
values, comparator, and headline statistic. Files are validated against
`schema/task.schema.json`; `task_id` must match the filename stem. See README
sections 8–10 for the configuration and comparator contracts.

Task prompts must document the clinical conventions used to create their
matching gold files. The bundled task prompts intentionally retain explicit
placeholders until maintainers supply conventions that match verified gold.

Validate all tasks and their gold files with:

```console
python -m aphasia_eval.services.task_loader --validate
```

For the synthetic demonstration data, set
`GOLD_TASKS_DIR=gold_tasks/examples` before running that command.
