# Cat Rescue annotation record

This record preserves the material supplied for `cat_rescue_001`. It is an
audit aid and is not loaded or sent to a model by the application.

The project maintainer affirmed the item's provenance, permission for project
use and redistribution, annotation accuracy, and clinical review on October
13, 2026. The maintainer also confirmed that the transcript is real, fully
deidentified, free of PHI, and approved for use. The scoring conventions are
documented in [`ciu_scoring_method.md`](ciu_scoring_method.md).

## Elicitation stimulus

The project maintainer identified and supplied the official Cat Rescue
clinical line drawing used to elicit this transcript on October 13, 2026. The
image is not included in this repository record and is not sent to models by
the v0.1 application, which supports text-only requests. If the image is added
in a later multimodal release, its source/version and redistribution terms
must be documented alongside the asset.

## Original transcript

```text
PAR: um the little girl wants her cat that climbed up in a tree
PAR: and so the man got up the tree to retrieve the cat
PAR: um and the ladder fell out behind him
PAR: and the dog is barking at all the excitement
PAR: and the firemen are coming to rescue, uh, I assume the cat and the man
PAR: and uh there’s a bird on another branch that’s singing.
```

## Supplied word analysis

Strikethrough marks material excluded from the word count.

```text
PAR: ~~um~~ the little girl wants her cat that climbed up in a tree
PAR: and so the man got up the tree to retrieve the cat
PAR: ~~um~~ and the ladder fell out behind him
PAR: and the dog is barking at all the excitement
PAR: and the firemen are coming to rescue, ~~uh~~, I assume the cat and the man
PAR: and ~~uh~~ there is a bird on another branch that is singing.
```

Verified total: **65 words**.

The supplied analysis excludes two instances of `um` and two instances of
`uh`. It expands `there’s` to `there is` and `that’s` to `that is`. The six
`PAR:` speaker labels are metadata and are not part of the count.

## Supplied CIU analysis

Strikethrough marks counted words excluded from the CIU count.

```text
PAR: the little girl wants her cat that climbed up in a tree
PAR: ~~and~~ so the man got up the tree to retrieve the cat
PAR: ~~and~~ the ladder fell out behind him
PAR: ~~and~~ the dog is barking at all the excitement
PAR: ~~and~~ the firemen are coming to rescue, ~~I assume~~ the cat ~~and~~ the man
PAR: ~~and~~ there is a bird on another branch that is singing.
```

The supplied analysis excludes five utterance-initial instances of `and`, the
additional `and` between `cat` and `the man`, and the two words `I assume`.
That excludes eight counted words: **65 − 8 = 57 CIUs**.

## Verified CIU result

- CIUs: **57**
- Words: **65**
- Derived percent CIUs: **87.692307...%** (displayed as **87.69%**)

## Provenance and release checklist

The maintainer attestation resolves provenance, project permission, annotation
accuracy, and clinical review. The remaining dataset-governance details should
be recorded before public release:

- [ ] Source and version of the elicitation material documented
- [x] Annotation accuracy confirmed through clinical review
- [x] CIU and word-count conventions finalized in the task prompt
- [ ] Participant consent, IRB, and data-use restrictions reviewed
- [ ] Dataset and annotation licenses documented
- [x] Project use and repository redistribution permitted by the maintainer
- [x] Transcript confirmed deidentified and free of PHI
- [x] Clinical review completed
