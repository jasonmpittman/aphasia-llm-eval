# Adding a task

A task is configuration, not application code. For an answer shape already
handled by `numeric_fields` or `token_tags`, adding a task requires one YAML
file and one compatible gold file. The selection UI discovers valid task files
automatically.

Do not use real participant material while developing a task. Start with
clearly labeled synthetic examples, then have the final prompt and gold data
reviewed under the project's provenance and redistribution requirements.

## 1. Choose a stable task ID

Use lowercase letters, digits, and underscores. The filename and `task_id`
must agree:

```text
tasks/word_count.yaml
task_id: word_count
gold_tasks/gold_word_count.txt
```

Changing an ID or prompt after collecting results creates a different
experimental condition. Increment the task `version` and retain exported run
metadata whenever the task definition changes.

## 2. Write the task YAML

This minimal numeric task illustrates all required sections:

```yaml
schema_version: 1
task_id: word_count
title: Word Count
category: language-sample
version: "0.1.0"
description: Counts words under a documented convention.

gold:
  file: gold_word_count.txt
  parser: kv

input:
  fields: [transcript]

prompt:
  system: |
    Follow the supplied counting rules and return JSON only.
  user_template: |
    Apply the lab's documented word-count rules to this transcript:
    {transcript}
    Return JSON: {{"word_count": <integer>}}

output:
  format: json
  schema:
    type: object
    required: [word_count]
    properties:
      word_count: {type: integer, minimum: 0}
    additionalProperties: false

comparison:
  type: numeric_fields
  fields: [word_count]
  tolerance: {word_count: 0}

headline: {stat: mean_abs_diff, field: word_count, better: lower}
```

The full contract is enforced by `tasks/schema/task.schema.json`.

- `input.fields` are required in every gold item. `optional_fields` may be
  omitted.
- Prompt placeholders may reference only declared input fields, `tagset`, or
  fields provided by the selected renderer. Attribute access, conversions,
  and format specifications are rejected.
- Literal JSON braces in prompt templates must be doubled, as shown above.
- `output.schema` validates both gold answers and parsed model responses.
- `comparison.type` selects deterministic arithmetic; it never invokes a
  model.
- `headline` selects the one task-specific value shown in the overview. Do
  not invent a cross-task composite.
- `defaults.temperature` and `defaults.max_tokens` are optional task-specific
  defaults.

The built-in `plain` renderer substitutes declared fields directly. The
`numbered_tokens` renderer formats the tokens supplied by `token_table` as
one-based, tab-separated lines and exposes `{numbered_tokens}`.

## 3. Add gold data

Create a small synthetic file first. For the YAML above:

```text
# Synthetic development fixture; not clinical data.
=== item: example_001 ===
[input.transcript]
blue square
[gold]
word_count: 2
```

See [Gold file format](gold-file-format.md) for parser details and the required
review checklist before introducing real gold data.

## 4. Validate and run

With the project virtual environment active:

```bash
python -m aphasia_eval.services.task_loader --validate
```

To validate against a separate synthetic or private gold directory:

```bash
GOLD_TASKS_DIR=/path/to/gold python -m aphasia_eval.services.task_loader --validate
```

The command reports every invalid task and includes item and line context for
gold errors. A missing or invalid gold file disables only that task. Once the
validator reports `OK`, start the application and confirm that the task card
shows the expected title, item count, and gold status.

## 5. When Python is required

No Python change is needed when an existing parser and comparator cover the
new task. A new `[gold]` syntax needs one parser registered in
`aphasia_eval.gold`. A fundamentally new answer shape needs one comparator
registered in `aphasia_eval.comparison`; follow
[Writing comparisons](comparison.md).

Before opening a pull request, run Ruff, the task validator, and pytest, then
complete the pull request's gold provenance and redistribution checklist.
