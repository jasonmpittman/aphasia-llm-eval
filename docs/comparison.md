# Writing comparisons

The comparison layer performs deterministic arithmetic between a parsed model
answer and a gold answer. It does not grade clinical quality, call another
model, or combine unrelated tasks into a global score.

## Result pipeline

For each model, task, item, and repetition, the application:

1. Extracts the first JSON object from model text and validates it against the
   task's output schema.
2. Computes declared derived values for the model and gold answers with the
   same fixed function.
3. Invokes the configured comparator.
4. Stores the raw answer, parsed answer, derived values, per-item details,
   statistics, and match flag.

A parse failure is retained and counted but excluded from difference
statistics. Model output is never sent to a second model.

## Ablation conditions

An ablation run repeats the same model/task/item matrix under several declared
parameter conditions. M7 varies only `max_tokens`; temperature and prompt
hashes remain fixed. Aggregates are partitioned by `condition_id`, and failures
remain in the comparable-response denominator. The stored experiment metadata
contains the requested settings and whether the gateway rejected and caused
the client to remove optional parameters.

The best-observed condition is selected independently for each model/task pair
by comparable-response rate, then the task's declared headline direction,
then lower estimated cost and latency. This is an exploratory convenience, not
held-out model selection. Use separate development and evaluation items before
making comparative performance claims.

## Derived values

Task YAML may use only registered functions:

| Function | Meaning | Undefined result |
|---|---|---|
| `percentage` | numerator / denominator × 100 | `null` for missing/non-numeric/zero denominator |
| `per_minute` | value / seconds × 60 | `null` for missing/non-numeric/zero duration |
| `ratio` | numerator / denominator | `null` for missing/non-numeric/zero denominator |
| `counts_by` | frequency map for a string sequence | `null` for an invalid sequence |

Arguments are field paths resolved from item inputs, the answer, and earlier
derived values. There is no expression evaluator. Mark a declaration
`optional: true` when a missing top-level argument is acceptable.

## Built-in comparators

### `numeric_fields`

This comparator reports the model value, gold value, signed difference,
absolute difference, tolerance, and tolerance match for every declared field.
An item matches only when every field is within tolerance. Aggregate output
includes mean absolute difference, mean signed difference, and exact-match
rate by field.

### `token_tags`

This comparator aligns model tags to gold token indices. Missing, duplicate,
extra, and out-of-tagset predictions remain visible and count as
disagreements. It reports token agreement, alignment status, category counts
and proportions, and signed count differences. Raw agreement is not
chance-corrected.

## Comparator plugin contract

A new answer shape requires a class with a stable `name` and three methods:

```python
class Comparator:
    name: str

    def validate_gold(self, task, gold_answer) -> tuple[str, ...]: ...

    def compare(self, task, model_answer, gold_answer):
        # Return: details, match flag (bool or None), aggregate statistics
        ...

    def view_descriptor(self, task) -> dict:
        # JSON-serializable instructions for the task-detail UI
        ...
```

Register an instance with `register_comparator()` in
`aphasia_eval.comparison`. Then add the comparator name and its schema options
to `tasks/schema/task.schema.json`. Keep all returned values JSON-serializable
and ensure malformed model values produce explicit disagreements or validation
errors rather than exceptions.

The aggregation service needs to understand any new statistic names. The
frontend also needs a renderer if the new view descriptor cannot use an
existing table or chart. A new parser is separate: parsers normalize gold file
syntax, while comparators operate on already normalized answers.

## Testing a plugin

Add hand-computed cases for exact matches, disagreement direction, tolerance
boundaries, missing values, malformed alignment, and zero denominators as
applicable. Also test gold validation, aggregation, JSON serialization, and the
view descriptor. Run the full suite because comparisons feed storage, UI, and
all export formats.
