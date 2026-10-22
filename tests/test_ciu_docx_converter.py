from __future__ import annotations

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from aphasia_eval.services.ciu_docx_converter import (
    W,
    convert_directory,
    main,
    read_docx_paragraphs,
    render_gold_files,
    write_conversion,
)
from aphasia_eval.services.gold_loader import load_gold_file


def _docx(path: Path, paragraphs: list[list[tuple[str, bool]]], *, tracked=False) -> None:
    document = ElementTree.Element(f"{W}document")
    body = ElementTree.SubElement(document, f"{W}body")
    for paragraph_runs in paragraphs:
        paragraph = ElementTree.SubElement(body, f"{W}p")
        parent = paragraph
        if tracked:
            parent = ElementTree.SubElement(paragraph, f"{W}ins")
        for text, struck in paragraph_runs:
            run = ElementTree.SubElement(parent, f"{W}r")
            if struck:
                properties = ElementTree.SubElement(run, f"{W}rPr")
                ElementTree.SubElement(properties, f"{W}strike")
            node = ElementTree.SubElement(run, f"{W}t")
            node.text = text
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            ElementTree.tostring(document, encoding="utf-8", xml_declaration=True),
        )


def _valid_paragraphs(task: str = "Cat Rescue") -> list[list[tuple[str, bool]]]:
    return [
        [(task, False)],
        [("CIU_words", False)],
        [("*PAR: ", False), ("um ", True), ("the cat climbs", False)],
        [("WORD COUNT: 3", False)],
        [("CIU_complete", False)],
        [("*PAR= the cat climbs", False)],
        [("CIU COUNT: 3", False)],
        [("3/3 = 100%", False)],
    ]


def test_converts_task_separated_gold_and_skips_reliability_files(tmp_path):
    _docx(tmp_path / "CIU1_RC2.docx", _valid_paragraphs())
    _docx(tmp_path / "CIU2_RC2.docx", _valid_paragraphs("Broken Window"))
    _docx(tmp_path / "CIU1_RC2_INTRA-rater.docx", _valid_paragraphs())
    (tmp_path / "._CIU1_RC2.docx").write_bytes(b"appledouble")

    result = convert_directory(tmp_path)

    assert result.summary() == {
        "source_dir": str(tmp_path.resolve()),
        "documents_seen": 3,
        "primary_documents": 2,
        "reliability_documents": 1,
        "unscored_primary_documents": 0,
        "accepted_items": 2,
        "candidate_items": 2,
        "rejected_items": 0,
        "items_by_task": {"ciu_broken_window": 1, "ciu_scoring": 1},
        "errors": 0,
        "warnings": 0,
    }
    cat = next(item for item in result.items if item.task_id == "ciu_scoring")
    assert cat.item_id == "rc002_s1"
    assert cat.source_document == "CIU1_RC2.docx"
    assert [(token.text, token.word, token.ciu) for token in cat.tokens] == [
        ("um", False, None),
        ("the", True, True),
        ("cat", True, True),
        ("climbs", True, True),
    ]

    rendered = render_gold_files(result)
    assert set(rendered) == {"gold_ciu_broken_window.txt", "gold_ciu_scoring.txt"}
    assert 'participant_id: "rc002"' in rendered["gold_ciu_scoring.txt"]
    output = tmp_path / "output"
    written = write_conversion(result, output)
    assert {path.name for path in written} == set(rendered)
    loaded = load_gold_file(output / "gold_ciu_scoring.txt", "ciu_token_table")
    assert loaded.items[0].gold["word_indices"] == [2, 3, 4]
    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_conversion(result, output)
    assert write_conversion(result, output, force=True)

    base = tmp_path / "base"
    base.mkdir()
    seed = rendered["gold_ciu_scoring.txt"].replace("rc002_s1", "manual_seed")
    (base / "gold_ciu_scoring.txt").write_text(seed, encoding="utf-8")
    merged = tmp_path / "merged"
    write_conversion(result, merged, base_gold_dir=base)
    merged_gold = load_gold_file(merged / "gold_ciu_scoring.txt", "ciu_token_table")
    assert [item.item_id for item in merged_gold.items] == ["manual_seed", "rc002_s1"]
    with pytest.raises(ValueError, match="duplicates generated item"):
        write_conversion(result, tmp_path / "duplicate", base_gold_dir=output)


def test_rejects_inconsistent_or_ambiguous_annotations(tmp_path):
    paragraphs = _valid_paragraphs()
    paragraphs[6] = [("CIU = 2", False)]
    _docx(tmp_path / "CIU1_RC9.docx", paragraphs)
    _docx(tmp_path / "CIU2_RC9.docx", _valid_paragraphs(), tracked=True)
    _docx(tmp_path / "CIU1_RC10.docx", [[("raw transcript only", False)]])
    (tmp_path / "CIU1_RC11.docx").write_bytes(b"not a docx")

    result = convert_directory(tmp_path)

    assert not result.items
    assert result.rejected_items == 2
    assert result.unscored_primary_documents == 1
    assert {issue.code for issue in result.errors} == {
        "ciu_total_mismatch",
        "tracked_change",
        "unreadable_docx",
    }
    with pytest.raises(ValueError, match="conversion has"):
        write_conversion(result, tmp_path / "output")
    with pytest.raises(ValueError, match="no accepted items"):
        write_conversion(result, tmp_path / "output", allow_partial=True)


def test_reports_alignment_metadata_and_docx_structure_errors(tmp_path):
    mismatch = _valid_paragraphs()
    mismatch[5] = [("PAR: the dog climbs", False)]
    _docx(tmp_path / "CIU1_RC4.docx", mismatch)
    _docx(tmp_path / "unknown.docx", _valid_paragraphs())
    with zipfile.ZipFile(tmp_path / "CIU1_RC5.docx", "w") as archive:
        archive.writestr("other.xml", "<not-word />")

    result = convert_directory(tmp_path)

    assert {issue.code for issue in result.errors} == {
        "alignment_token",
        "filename_metadata",
        "unreadable_docx",
    }


def test_cli_dry_run_and_write_requirements(tmp_path, capsys):
    _docx(tmp_path / "CIU1_RC7.docx", _valid_paragraphs())

    assert main([str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["accepted_items"] == 1
    assert main([str(tmp_path), "--write"]) == 2
    assert "--output-dir is required" in capsys.readouterr().out

    output = tmp_path / "output"
    report = tmp_path / "report.json"
    assert main(
        [
            str(tmp_path),
            "--write",
            "--output-dir",
            str(output),
            "--report-json",
            str(report),
        ]
    ) == 0
    assert report.exists()
    assert (output / "gold_ciu_scoring.txt").exists()


def test_read_docx_rejects_invalid_xml(tmp_path):
    path = tmp_path / "bad.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<broken")

    with pytest.raises(ValueError, match="invalid WordprocessingML"):
        read_docx_paragraphs(path)
