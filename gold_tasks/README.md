# Gold files

Verified answers belong in `gold_<task_id>.txt`, where `<task_id>` exactly
matches the corresponding task YAML. A task can name a different file with its
`gold.file` setting, but the file must still be directly inside
`GOLD_TASKS_DIR`; paths and subdirectories are rejected.

Files are UTF-8 text. Each item has a unique ID, one or more input sections,
and a gold section:

```text
=== item: sample_001 ===
[input]
duration_seconds: 95
[input.transcript]
Free text can span multiple lines.
[gold]
total_words: 112
ciu_count: 74
```

`[input]` contains `key: value` scalar fields and `[input.<name>]` contains a
multiline field. The task's parser interprets `[gold]`: `kv` accepts scalar
`key: value` pairs, while `token_table` accepts tab-separated
`index<TAB>token<TAB>tag` rows. See
[`docs/gold-file-format.md`](../docs/gold-file-format.md) for the complete
format and validation rules.

Synthetic demonstration files belong in `examples/` and must be clearly
labeled as synthetic. Run the validator against them with:

```console
GOLD_TASKS_DIR=gold_tasks/examples python -m aphasia_eval.services.task_loader --validate
```

Before adding any real gold file, maintainers must document its source,
annotation and verification process, participant-consent and IRB constraints,
data license, and whether repository redistribution is permitted. Do not commit
PHI, identifiable participant data, or content whose redistribution rights are
uncertain. When redistribution is not permitted, keep the file outside this
repository and point `GOLD_TASKS_DIR` to that private location.
