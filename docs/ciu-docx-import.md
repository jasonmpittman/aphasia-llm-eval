# Importing CIU DOCX annotations

The CIU DOCX converter turns reviewed Word annotations into the application's
token-level gold format. Its unit of evaluation is one participant, one
test/retest session, and one elicitation task. It does not combine Cat Rescue,
Broken Window, Refused Umbrella, Cinderella, or Procedural PB&J into a composite
clinical score.

## Accepted source structure

The converter recognizes task headings followed by `CIU_words` and
`CIU_complete` sections. Participant productions may use `PAR:`, `*PAR:`, or
`*PAR=` prefixes, or omit the prefix in older reviewed documents. It recognizes
both `WORDS = 65` and `WORD COUNT: 65` conventions, plus their CIU equivalents.

Word run strikethrough is authoritative:

- In `CIU_words`, struck tokens become `NOT_WORD`; the others become `WORD`.
- Counted words are aligned exactly, in order, with `CIU_complete`.
- In `CIU_complete`, struck tokens become `NOT_CIU`; the others become `CIU`.
- Every declared total must equal the labels recovered from formatting.

Contractions and other clinically normalized forms are not transformed by the
converter. It preserves the tokens chosen by the reviewed source annotation.

## Fail-closed review policy

An item is excluded and reported when any of these conditions occurs:

- a required section or unique total is absent;
- Word formatting identifies a different total than the reviewer recorded;
- counted-word tokens do not align exactly with the CIU section;
- a token is only partly struck through;
- an unresolved tracked change occurs in a scored paragraph;
- participant/session metadata cannot be derived from the filename; or
- the same task, participant, and session would be imported twice.

`--allow-partial` writes only items with no errors. It does not weaken any of
these checks. The JSON review report contains filenames and issue locations but
does not modify the source directory.

## Reliability and repeated measures

Filenames marked intrarater, interrater, or alternate-rater (`JMA`) are counted
in the audit and excluded from benchmark items. They are evidence for a future
reliability/adjudication workflow, not additional independent samples.

Imported item IDs use the form `rcNNN_sN`. Gold inputs also retain
`participant_id`, `session`, and `source_document`. Analyses that split or
resample these data must group on `participant_id` so session one and session
two for the same person cannot leak across groups.

## Commands

Always run the converter and validator from the project virtual environment:

```console
.venv/bin/python -m aphasia_eval.services.ciu_docx_converter \
  /path/to/Test_retest_CIU \
  --report-json /tmp/ciu-import-report.json

.venv/bin/python -m aphasia_eval.services.ciu_docx_converter \
  /path/to/Test_retest_CIU \
  --write --allow-partial \
  --output-dir /tmp/converted-ciu

.venv/bin/python -m aphasia_eval.services.task_loader --validate
```

Use `--base-gold-dir` only when a generated set must retain separately curated
seed items. Duplicate item IDs are rejected. Existing output files are not
overwritten unless `--force` is explicit.
