# Contributing

Thank you for helping improve `aphasia-llm-eval`.

## Development setup

Use Python 3.11 or newer and keep dependencies in a local virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install --no-deps -e .
pre-commit install
```

Copy `.env.example` to `.env` and set a LiteLLM API key when running tests that
exercise the live gateway. Never commit `.env`, credentials, clinical data, or
identifiable transcripts.

Before submitting a change, run:

```bash
ruff check .
ruff format --check .
pytest
```

## Gold data

Every gold-data contribution must state its provenance, validation process,
license, participant-consent constraints, and whether redistribution is
permitted. Do not submit protected health information (PHI) or real data whose
redistribution rights are uncertain. Synthetic fixtures must be unmistakably
labeled as synthetic.

By participating, you agree to follow the project code of conduct. Contributions
to the software are licensed under GPL-3.0-only.
