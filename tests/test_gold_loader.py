from __future__ import annotations

import hashlib

import pytest

from aphasia_eval.gold import GoldParseError, SourceLine, get_gold_parser
from aphasia_eval.gold.parsers import parse_kv, parse_token_table
from aphasia_eval.services.gold_loader import (
    load_gold_file,
    resolve_gold_path,
)


def test_load_kv_example_with_inputs_and_hash():
    path = "gold_tasks/examples/gold_ciu_scoring.txt"
    dataset = load_gold_file(path, "kv")

    assert len(dataset.items) == 5
    assert dataset.items[0].item_id == "synthetic_ciu_001"
    assert dataset.items[0].inputs == {
        "duration_seconds": 6,
        "stimulus": "Synthetic sentence about weather",
        "transcript": "Rain falls today.",
    }
    assert dataset.items[0].gold == {"total_words": 3, "ciu_count": 3}
    assert dataset.sha256 == hashlib.sha256(dataset.path.read_bytes()).hexdigest()


def test_load_token_table_supplies_numbered_tokens():
    dataset = load_gold_file("gold_tasks/examples/gold_pos_breakdown.txt", "token_table")

    item = dataset.items[0]
    assert item.inputs["tokens"] == [
        {"i": 1, "token": "The"},
        {"i": 2, "token": "dog"},
        {"i": 3, "token": "runs"},
        {"i": 4, "token": "."},
    ]
    assert item.gold["tags"][1] == {"i": 2, "pos": "NOUN"}


@pytest.mark.parametrize(
    "contents, parser, message, line",
    [
        ("[gold]\na: 1\n", "kv", "before first item", 1),
        ("=== item: a ===\na: 1\n", "kv", "outside a section", 2),
        ("=== item: a ===\n[input]\na: 1\n", "kv", r"missing a \[gold\]", 1),
        ("=== item: bad id ===\n[gold]\na: 1\n", "kv", "invalid item ID", 1),
        ("=== item: a ===\n[gold]\nnot-a-pair\n", "kv", "key: value", 3),
        ("=== item: a ===\n[gold]\n1 token NOUN\n", "token_table", "tab-separated", 3),
        ("=== item: a ===\n[gold]\nx\ttoken\tNOUN\n", "token_table", "integer", 3),
    ],
)
def test_malformed_gold_reports_line_number(tmp_path, contents, parser, message, line):
    path = tmp_path / "gold.txt"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(GoldParseError, match=message) as caught:
        load_gold_file(path, parser)

    assert caught.value.line_number == line


def test_duplicate_item_ids_report_both_locations(tmp_path):
    path = tmp_path / "gold.txt"
    path.write_text(
        "=== item: repeated ===\n[gold]\na: 1\n=== item: repeated ===\n[gold]\na: 2\n",
        encoding="utf-8",
    )

    with pytest.raises(GoldParseError, match="first declared on line 1") as caught:
        load_gold_file(path, "kv")
    assert caught.value.line_number == 4


def test_duplicate_token_index_is_rejected_at_exact_line(tmp_path):
    path = tmp_path / "gold.txt"
    path.write_text(
        "=== item: tokens ===\n[gold]\n1\tA\tDET\n1\tdog\tNOUN\n",
        encoding="utf-8",
    )
    with pytest.raises(GoldParseError, match="duplicate token index 1") as caught:
        load_gold_file(path, "token_table")
    assert caught.value.line_number == 4


def test_duplicate_input_fields_are_rejected(tmp_path):
    path = tmp_path / "gold.txt"
    path.write_text(
        "=== item: duplicate ===\n[input]\ntext: one\ntext: two\n[gold]\na: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(GoldParseError, match="duplicate input field") as caught:
        load_gold_file(path, "kv")
    assert caught.value.line_number == 4


def test_parser_level_malformed_and_empty_values():
    with pytest.raises(GoldParseError, match="duplicate gold key"):
        parse_kv([SourceLine(1, "a: 1"), SourceLine(2, "a: 2")])
    with pytest.raises(GoldParseError, match="must not be empty"):
        parse_kv([SourceLine(4, "answer:")])
    with pytest.raises(GoldParseError, match="must be a scalar"):
        parse_kv([SourceLine(5, "answer: [1, 2]")])
    with pytest.raises(GoldParseError, match="at least 1"):
        parse_token_table([SourceLine(7, "0\ttoken\tNOUN")])


def test_unknown_parser_and_unsafe_filename_are_rejected(tmp_path):
    with pytest.raises(GoldParseError, match="unknown gold parser"):
        get_gold_parser("does_not_exist")
    with pytest.raises(GoldParseError, match="directory components"):
        resolve_gold_path("task", tmp_path, "../gold.txt")


def test_missing_non_utf8_and_empty_files_are_reported(tmp_path):
    with pytest.raises(GoldParseError, match="not found"):
        load_gold_file(tmp_path / "missing.txt", "kv")

    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(GoldParseError, match="not valid UTF-8"):
        load_gold_file(invalid, "kv")

    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n", encoding="utf-8")
    with pytest.raises(GoldParseError, match="contains no items"):
        load_gold_file(empty, "kv")
