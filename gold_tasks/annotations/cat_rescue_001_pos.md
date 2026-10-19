# Cat Rescue provisional POS annotation

This record documents the POS reference derived from the approved,
deidentified `cat_rescue_001` transcript. It enables end-to-end development
and model comparison before an authoritative POS analysis is available.

## Status

The annotation was prepared as a project development reference on October 19,
2026. It has **not** received independent linguistic or clinical review and
must not be described as a validated gold standard. Model agreement with it
measures agreement with these provisional decisions only.

The application nevertheless loads it from `gold_pos_breakdown.txt` because
`gold_*.txt` is the configured reference-file interface. When a reviewed POS
analysis becomes available, it can replace that file without application-code
changes.

## Source

The source is the transcript preserved in [`cat_rescue_001.md`](cat_rescue_001.md).
The maintainer previously confirmed that transcript is real, fully
deidentified, free of PHI, approved for project use and redistribution, and
clinically reviewed for its CIU analysis. Those confirmations do not imply
that this derived POS annotation has been reviewed.

## Tokenization conventions

- Exclude `PAR:` speaker labels and punctuation used only for transcription
  formatting.
- Retain the two instances of `um` and two instances of `uh`; tag them `INTJ`.
- Expand `there’s` to `there is` and `that’s` to `that is`, matching the
  normalized word analysis.
- Preserve every remaining word in transcript order, including discourse
  conjunctions and the phrase `I assume`.
- The resulting item contains 69 fixed tokens: the 65 counted CIU-analysis
  words plus four filled pauses.

## UPOS decisions

The labels follow Universal Dependencies v2 UPOS conventions and the rules in
`tasks/pos_breakdown.yaml`. Decisions worth making explicit are:

- English possessive `her`, existential `there`, and relative `that` are
  `PRON` under this project convention.
- Progressive and copular forms of `be`, including existential `is`, are
  `AUX`.
- Infinitival `to` is `PART`.
- Directional `up` in `climbed up` and `out` in `fell out` are `ADV`; `up` in
  `up the tree` links a nominal complement and is `ADP`.
- `so` in `and so` is `ADV`; every `and` is `CCONJ`.
- `all` in `all the excitement` is an adnominal quantifier tagged `DET`.

## Provisional distribution

| UPOS | Count |
|---|---:|
| ADJ | 1 |
| ADP | 5 |
| ADV | 3 |
| AUX | 4 |
| CCONJ | 6 |
| DET | 14 |
| INTJ | 4 |
| NOUN | 14 |
| PART | 2 |
| PRON | 6 |
| VERB | 10 |
| **Total** | **69** |

Tags absent from this item have a count of zero.

## Replacement and review checklist

- [x] Source transcript and derivation documented
- [x] Tokenization and tagging conventions documented in the task prompt
- [x] Provisional status disclosed in the reference file and documentation
- [ ] Independent annotation by a qualified reviewer completed
- [ ] Disagreements adjudicated or inter-rater agreement documented
- [ ] Authoritative replacement approved by the maintainer

When replacing the provisional reference, preserve the `token_table` format
(`index<TAB>token<TAB>tag`), rerun the task validator and test suite, update
this record, and advance the POS task version if tokenization or tagging
conventions change.
