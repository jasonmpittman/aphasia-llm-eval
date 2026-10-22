from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from aphasia_eval.services.task_loader import (
    load_task_file,
    load_tasks,
    main,
    render_prompt,
    validate_prompt_templates,
)

ROOT = Path(__file__).parents[1]
SCHEMA_PATH = ROOT / "tasks/schema/task.schema.json"


def _read_task(name: str):
    return yaml.safe_load((ROOT / f"tasks/{name}.yaml").read_text(encoding="utf-8"))


def test_example_tasks_validate_with_gold_and_hashes():
    results = load_tasks(ROOT / "tasks", ROOT / "gold_tasks/examples")

    assert [result.task_id for result in results] == [
        "ciu_broken_window",
        "ciu_cinderella",
        "ciu_procedural_pbj",
        "ciu_refused_umbrella",
        "ciu_scoring",
        "pos_breakdown",
    ]
    assert all(result.enabled for result in results)
    ciu = next(result.task for result in results if result.task_id == "ciu_scoring")
    assert ciu is not None and ciu.gold is not None
    assert len(ciu.gold.items) == 5
    assert ciu.task_sha256 == hashlib.sha256(ciu.path.read_bytes()).hexdigest()
    assert len(ciu.prompt_sha256) == 64


def test_missing_real_gold_disables_each_task_without_stopping_loader(tmp_path):
    results = load_tasks(ROOT / "tasks", tmp_path)

    assert len(results) == 6
    assert all(not result.enabled for result in results)
    assert all(any("gold file not found" in error for error in result.errors) for result in results)
    assert all(result.task is not None and result.task.gold is None for result in results)


def test_plain_and_numbered_token_renderers():
    ciu = _read_task("ciu_scoring")
    prompt = render_prompt(
        ciu,
        {
            "tokens": [
                {"i": 1, "utterance": 1, "token": "Birds"},
                {"i": 2, "utterance": 1, "token": "sing"},
            ],
            "stimulus": "Synthetic",
            "duration_seconds": 2,
        },
    )
    assert "Utterance 1:\n1\tBirds\n2\tsing" in prompt.user
    assert "Stimulus or topic:\nSynthetic" in prompt.user
    assert 'Never count "and" as a CIU.' in prompt.user
    assert "Split contractions into their constituent words" in prompt.user
    assert "replace with your lab's CIU rules" not in prompt.user
    assert '"word_indices": [<indices counted as words>]' in prompt.user

    pos = _read_task("pos_breakdown")
    prompt = render_prompt(
        pos,
        {"tokens": [{"i": 1, "token": "Blue"}, {"i": 2, "token": "sky"}]},
    )
    assert "1\tBlue\n2\tsky" in prompt.user
    assert "ADJ, ADP, ADV" in prompt.user
    assert 'filled pauses such as "um" and "uh" as INTJ' in prompt.user
    assert "<state how to treat fillers" not in prompt.user
    assert '{"tags": [{"i": 1, "pos": "DET"}' in prompt.user


def test_clinically_reviewed_cat_rescue_gold_matches_annotation():
    result = load_task_file(
        ROOT / "tasks/ciu_scoring.yaml",
        gold_dir=ROOT / "gold_tasks",
        schema_path=SCHEMA_PATH,
    )

    assert result.enabled, result.errors
    assert result.task is not None and result.task.gold is not None
    assert result.task.config["version"] == "0.4.0"
    assert len(result.task.gold.items) == 26
    item = next(item for item in result.task.gold.items if item.item_id == "cat_rescue_001")
    assert item.item_id == "cat_rescue_001"
    assert item.gold["token_count"] == 69
    assert len(item.gold["word_indices"]) == 65
    assert len(item.gold["ciu_indices"]) == 57
    assert set(item.gold["ciu_indices"]).issubset(item.gold["word_indices"])


def test_provisional_cat_rescue_pos_reference_matches_annotation():
    result = load_task_file(
        ROOT / "tasks/pos_breakdown.yaml",
        gold_dir=ROOT / "gold_tasks",
        schema_path=SCHEMA_PATH,
    )

    assert result.enabled, result.errors
    assert result.task is not None and result.task.gold is not None
    assert result.task.config["version"] == "0.2.0"
    assert len(result.task.gold.items) == 1
    item = result.task.gold.items[0]
    assert item.item_id == "cat_rescue_001"
    assert len(item.inputs["tokens"]) == 69
    assert len(item.gold["tags"]) == 69
    assert item.inputs["tokens"][0] == {"i": 1, "utterance": 1, "token": "um"}
    assert item.inputs["tokens"][-1] == {
        "i": 69,
        "utterance": 6,
        "token": "singing",
    }
    assert Counter(tag["pos"] for tag in item.gold["tags"]) == {
        "ADJ": 1,
        "ADP": 5,
        "ADV": 3,
        "AUX": 4,
        "CCONJ": 6,
        "DET": 14,
        "INTJ": 4,
        "NOUN": 14,
        "PART": 2,
        "PRON": 6,
        "VERB": 10,
    }


@pytest.mark.parametrize(
    "template, message",
    [
        ("{stimulus.value}", "unsafe placeholder"),
        ("{stimulus[0]}", "unsafe placeholder"),
        ("{stimulus!r}", "must not use conversion"),
        ("{stimulus:>10}", "must not use conversion or formatting"),
        ("{undeclared}", "undeclared placeholder"),
        ("an unmatched { brace", "invalid braces"),
    ],
)
def test_restricted_prompt_rendering_rejects_unsafe_fields(template, message):
    task = _read_task("ciu_scoring")
    task["prompt"]["user_template"] = template

    with pytest.raises(ValueError, match=message):
        validate_prompt_templates(task)


def test_rendering_rejects_missing_or_bad_renderer_inputs():
    ciu = _read_task("ciu_scoring")
    with pytest.raises(ValueError, match="non-empty tokens list"):
        render_prompt(ciu, {})

    pos = _read_task("pos_breakdown")
    with pytest.raises(ValueError, match="non-empty tokens list"):
        render_prompt(pos, {"tokens": []})
    with pytest.raises(ValueError, match="invalid numbered token index"):
        render_prompt(pos, {"tokens": [{"i": 0, "token": "word"}]})


def test_invalid_yaml_is_skipped_while_valid_sibling_loads(tmp_path):
    tasks_dir = tmp_path / "tasks"
    gold_dir = tmp_path / "gold"
    tasks_dir.mkdir()
    gold_dir.mkdir()
    (tasks_dir / "ciu_scoring.yaml").write_text(
        (ROOT / "tasks/ciu_scoring.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tasks_dir / "broken.yaml").write_text("schema_version: [", encoding="utf-8")
    (gold_dir / "gold_ciu_scoring.txt").write_text(
        (ROOT / "gold_tasks/examples/gold_ciu_scoring.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    results = load_tasks(tasks_dir, gold_dir, SCHEMA_PATH)

    assert len(results) == 2
    assert results[0].task_id == "broken"
    assert not results[0].enabled
    assert "could not read task YAML" in results[0].errors[0]
    assert results[1].enabled


def test_schema_filename_bad_number_and_unknown_inputs_disable_task(tmp_path):
    task_path = tmp_path / "wrong_name.yaml"
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    task_path.write_text(
        (ROOT / "tasks/ciu_scoring.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (gold_dir / "gold_ciu_scoring.txt").write_text(
        "=== item: bad ===\n[input]\nunknown: field\n[gold]\n1\t1\tSynthetic\tWORD\tCIU\n",
        encoding="utf-8",
    )

    result = load_task_file(task_path, gold_dir=gold_dir, schema_path=SCHEMA_PATH)

    assert not result.enabled
    assert any("must match filename stem" in error for error in result.errors)
    # Filename mismatches prevent reading gold; the type/input checks work once the name is valid.
    task_path = tmp_path / "ciu_scoring.yaml"
    task_path.write_text(
        (ROOT / "tasks/ciu_scoring.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = load_task_file(task_path, gold_dir=gold_dir, schema_path=SCHEMA_PATH)
    assert any("unknown input field" in error for error in result.errors)


def test_task_schema_and_output_schema_failures_are_reported(tmp_path):
    invalid_task = _read_task("ciu_scoring")
    del invalid_task["title"]
    path = tmp_path / "ciu_scoring.yaml"
    path.write_text(yaml.safe_dump(invalid_task), encoding="utf-8")
    result = load_task_file(path, gold_dir=tmp_path, schema_path=SCHEMA_PATH)
    assert result.task is None
    assert any("task schema error" in error and "title" in error for error in result.errors)

    invalid_task = _read_task("ciu_scoring")
    invalid_task["output"]["schema"] = {"type": "not-a-json-schema-type"}
    path.write_text(yaml.safe_dump(invalid_task), encoding="utf-8")
    result = load_task_file(path, gold_dir=tmp_path, schema_path=SCHEMA_PATH)
    assert result.task is not None
    assert any("invalid output JSON Schema" in error for error in result.errors)

    invalid_task = _read_task("ciu_scoring")
    invalid_task["derived"][0]["args"] = ["ciu_count", "word_indices"]
    path.write_text(yaml.safe_dump(invalid_task), encoding="utf-8")
    result = load_task_file(path, gold_dir=tmp_path, schema_path=SCHEMA_PATH)
    assert result.task is None
    assert any("too long" in error for error in result.errors)


def test_cli_reports_success_and_failure(capsys, tmp_path):
    assert (
        main(
            [
                "--validate",
                "--tasks-dir",
                str(ROOT / "tasks"),
                "--gold-dir",
                str(ROOT / "gold_tasks/examples"),
            ]
        )
        == 0
    )
    assert "OK ciu_scoring: 5 gold item(s)" in capsys.readouterr().out

    empty_tasks = tmp_path / "tasks"
    empty_tasks.mkdir()
    (empty_tasks / "schema").mkdir()
    (empty_tasks / "schema/task.schema.json").write_text(
        json.dumps(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))), encoding="utf-8"
    )
    assert main(["--validate", "--tasks-dir", str(empty_tasks)]) == 1
    assert "No task YAML files found" in capsys.readouterr().out
