# CIU scoring method record

## Basis

The operational standard is based on Nicholas and Brookshire (1993):

> Nicholas LE, Brookshire RH. A system for quantifying the informativeness and
> efficiency of the connected speech of adults with aphasia. *Journal of
> Speech and Hearing Research*. 1993;36(2):338–350.

The project maintainer supplied a clinically reviewed CIU analysis guide on
October 13, 2026. The maintainer affirmed its provenance, accuracy, and
permission for use in this project. The full supplied handout is not copied
into the repository; its operational rules are paraphrased in
`tasks/ciu_scoring.yaml`.

## Implemented conventions

The task prompt separates word identification from CIU identification. It
records the supplied rules for non-lexical and unintelligible material,
corrections, repetitions, revisions, referents, vague language, cohesive
terms, qualifiers, filler phrases, commentary, conjunctions, contractions,
CHAT repair notation, incomplete utterances, morphological and semantic
errors, informal terms, embellishments, auxiliaries, and hyphenated words.

Changing these conventions changes the experimental condition. The CIU task
version was advanced from `0.1.0` to `0.2.0` when this standard replaced the
placeholder prompt.

## Scope and limitation

CIU accuracy and informativeness are relative to a stimulus or topic. The v0.1
application sends text only. The maintainer supplied the official Cat Rescue
clinical image, but it remains external to the repository and is not sent to
the model. The production item therefore supplies only the `Cat Rescue` topic
under its `stimulus` field. A title alone may not contain enough information
for a model to assess picture accuracy reliably, while substituting a newly
written description would change the elicitation context and could reveal
expected content. This limitation must be considered when interpreting v0.1
results. A later multimodal task version can use the image after its asset
source and redistribution terms are documented.
