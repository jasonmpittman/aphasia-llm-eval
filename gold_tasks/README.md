# Gold files

Verified answers belong in `gold_<task_id>.txt`, where `<task_id>` exactly
matches the corresponding task YAML. The default proposed layout is documented
in README section 9.1. Synthetic demonstration files belong in `examples/` and
must be clearly labeled as synthetic.

Before adding any real gold file, maintainers must document its source,
annotation and verification process, participant-consent and IRB constraints,
data license, and whether repository redistribution is permitted. Do not commit
PHI, identifiable participant data, or content whose redistribution rights are
uncertain. When redistribution is not permitted, keep the file outside this
repository and point `GOLD_TASKS_DIR` to that private location.
