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
[gold]
1<TAB>1<TAB>The<TAB>WORD<TAB>CIU
2<TAB>1<TAB>um<TAB>NOT_WORD<TAB>NA
3<TAB>1<TAB>dog<TAB>WORD<TAB>CIU
```

`[input]` contains `key: value` scalar fields and `[input.<name>]` contains a
multiline field. The task's parser interprets `[gold]`: `kv` accepts scalar
`key: value` pairs, `token_table` accepts tab-separated POS rows, and
`ciu_token_table` accepts tab-separated token/word/CIU rows. See
[`docs/gold-file-format.md`](../docs/gold-file-format.md) for the complete
format and validation rules.

Synthetic demonstration files belong in `examples/` and must be clearly
labeled as synthetic. Run the validator against them with:

```console
GOLD_TASKS_DIR=gold_tasks/examples python -m aphasia_eval.services.task_loader --validate
```

Supporting annotation records may be kept under `annotations/`. These files
are for provenance and audit only: the application does not load them or send
them to a model. Do not use an annotation sidecar to bypass the provenance,
privacy, or redistribution requirements below.

## Bundled reference status

- The five `gold_ciu_*.txt` files contain clinically reviewed token labels for
  Cat Rescue, Broken Window, Refused Umbrella, Cinderella, and Procedural PB&J.
  They are separate benchmark tasks so researchers can run one stimulus, any
  combination, or all of them without collapsing unlike elicitation tasks.
- `gold_ciu_scoring.txt` also retains the manually entered Cat Rescue item
  documented in `annotations/cat_rescue_001.md`.
- Imported items retain deidentified participant and test/retest session fields.
  Use `participant_id` as a grouping variable in any resampling or data split;
  the two sessions are not statistically independent participants.
- `gold_pos_breakdown.txt` contains a provisional development annotation
  derived from the same transcript. It enables end-to-end POS testing but has
  not been independently reviewed and is not an authoritative gold standard.
  See `annotations/cat_rescue_001_pos.md` before interpreting its results.

Before adding any real gold file, maintainers must document its source,
annotation and verification process, participant-consent and IRB constraints,
data license, and whether repository redistribution is permitted. Do not commit
PHI, identifiable participant data, or content whose redistribution rights are
uncertain. When redistribution is not permitted, keep the file outside this
repository and point `GOLD_TASKS_DIR` to that private location.

## DOCX converter

The strict converter in `aphasia_eval.services.ciu_docx_converter` reads Word
run-level strikethrough, verifies the declared word and CIU totals, requires
exact token alignment between `CIU_words` and `CIU_complete`, and writes one
gold file per elicitation task. Intrarater, interrater, and alternate-rater
documents are inventoried but never treated as independent benchmark items.

Run an audit without writing files:

```console
python -m aphasia_eval.services.ciu_docx_converter /path/to/docx-directory \
  --report-json /tmp/ciu-import-report.json
```

Write only the clean subset after reviewing the report:

```console
python -m aphasia_eval.services.ciu_docx_converter /path/to/docx-directory \
  --write --allow-partial --output-dir /tmp/converted-ciu
```

An exit status of `1` means at least one candidate still requires review, even
when `--allow-partial` safely wrote the accepted subset. The converter never
guesses through tracked changes, mixed strikethrough, mismatched totals, or
token alignment differences. See
[`docs/ciu-docx-import.md`](../docs/ciu-docx-import.md) for the complete policy.
