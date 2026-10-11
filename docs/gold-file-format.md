# Gold file format

Gold files are UTF-8, line-oriented reference datasets. The default filename
is `gold_<task_id>.txt` inside `GOLD_TASKS_DIR`; a task may provide a different
basename with `gold.file`. Directory components in that setting are rejected.

The application compares model output with these answers mechanically. It
cannot determine whether an annotation is clinically valid.

## Item structure

```text
# Comments are allowed outside multiline text content.
=== item: sample_001 ===
[input]
duration_seconds: 95
stimulus: cookie_theft
[input.transcript]
Free text can span multiple lines.
Blank lines are retained inside the field.
[gold]
total_words: 112
ciu_count: 74
```

- `=== item: <id> ===` begins an item. IDs may contain letters, digits,
  underscores, periods, and hyphens, and must be unique within the file.
- `[input]` contains `key: value` scalar fields. Values use YAML scalar
  parsing, but lists and mappings are rejected.
- `[input.<field>]` contains multiline text through the next section or item.
- `[gold]` is required and is interpreted by the parser named in the task
  YAML.
- Errors include a source line when possible. Duplicate items, sections,
  fields, or token indices are rejected.

## Built-in parsers

### `kv`

`kv` reads scalar `key: value` lines:

```text
[gold]
total_words: 12
ciu_count: 8
```

Blank lines and comment lines are ignored. Keys must be unique and values
must be non-empty scalars. The resulting object must satisfy the task's output
JSON Schema and comparator requirements.

### `token_table`

`token_table` reads exactly three tab-separated columns per row:

```text
[gold]
1	the	DET
2	dog	NOUN
3	runs	VERB
```

Indices are positive integers and unique. Tokens and tags must be non-empty.
The parser produces `tags` as the gold answer and also supplies numbered
tokens to the task's prompt renderer. The task's declared tagset is validated
separately.

## Validation and traceability

Run:

```bash
python -m aphasia_eval.services.task_loader --validate
```

For the bundled demonstration data:

```bash
GOLD_TASKS_DIR=gold_tasks/examples \
  python -m aphasia_eval.services.task_loader --validate
```

Validation checks input fields, output schema, tagset, derived values,
comparator compatibility, and prompt rendering. A SHA-256 of the exact gold
file is saved with each run.

## Real-data checklist

Before using or distributing a real gold file, document:

1. Dataset source and accession or version.
2. Annotation instructions and the qualified reviewers involved.
3. Verification or adjudication process and reliability evidence.
4. Participant consent, IRB, data-use, and institutional constraints.
5. Dataset and annotation licenses.
6. Whether redistribution in this GPLv3 repository is explicitly permitted.
7. Confirmation that the task prompt uses the same clinical conventions.

Never commit PHI or identifiable participant content. If redistribution is
not permitted, keep the data outside the repository and point
`GOLD_TASKS_DIR` at that private directory. The files under
`gold_tasks/examples/` are synthetic software fixtures, not clinically
verified data.
