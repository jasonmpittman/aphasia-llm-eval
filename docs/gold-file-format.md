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
[gold]
1<TAB>1<TAB>The<TAB>WORD<TAB>CIU
2<TAB>1<TAB>um<TAB>NOT_WORD<TAB>NA
3<TAB>1<TAB>dog<TAB>WORD<TAB>CIU
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

`token_table` reads three tab-separated columns per row, or four when an
utterance number is supplied:

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

The optional four-column form is
`index<TAB>utterance<TAB>token<TAB>tag`. It preserves utterance boundaries for
the Token Analysis view.

### `ciu_token_table`

`ciu_token_table` reads exactly five tab-separated columns:

```text
[gold]
1<TAB>1<TAB>The<TAB>WORD<TAB>CIU
2<TAB>1<TAB>um<TAB>NOT_WORD<TAB>NA
3<TAB>1<TAB>dog<TAB>WORD<TAB>CIU
4<TAB>1<TAB>and<TAB>WORD<TAB>NOT_CIU
```

The columns are token index, utterance number, token text, word label, and CIU
label. Token indices must be contiguous
starting at 1. Word labels are `WORD` or `NOT_WORD`; a word uses `CIU` or
`NOT_CIU`, while a non-word must use `NA`. The parser supplies fixed tokens to
the prompt and produces `token_count`, `word_indices`, and `ciu_indices` as the
gold answer.

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
