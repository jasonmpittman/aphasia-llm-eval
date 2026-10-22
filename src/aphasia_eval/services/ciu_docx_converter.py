"""Convert clinically reviewed CIU DOCX files into token-level gold files.

The converter intentionally accepts only exact, internally consistent source
annotations.  It never repairs token mismatches or inferred totals: ambiguous
items are reported for review and excluded from generated gold.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

TASKS = {
    "ciu_scoring": "Cat Rescue",
    "ciu_broken_window": "Broken Window",
    "ciu_communication_story": "Communication Story",
    "ciu_cinderella": "Cinderella",
    "ciu_illness_story": "Illness Story",
    "ciu_life_event": "Life Event",
    "ciu_procedural_pbj": "Procedural PB&J",
    "ciu_refused_umbrella": "Refused Umbrella",
}

TASK_PATTERNS = (
    ("ciu_refused_umbrella", re.compile(r"\b(?:refused umbrella|umbrella refusal)\b", re.I)),
    ("ciu_broken_window", re.compile(r"\bbroken window\b", re.I)),
    ("ciu_cat_rescue", re.compile(r"\bcat rescue\b", re.I)),
    ("ciu_cinderella", re.compile(r"\bcinderella(?: story)?\b", re.I)),
    (
        "ciu_procedural_pbj",
        re.compile(r"\b(?:procedural\s+)?p\s*b\s*(?:&|and|n)\s*j\b|\bpeanut butter", re.I),
    ),
    ("ciu_illness_story", re.compile(r"\billness story\b", re.I)),
    (
        "ciu_communication_story",
        re.compile(r"\bcommunication (?:story|question)\b", re.I),
    ),
    ("ciu_life_event", re.compile(r"\b(?:important )?life event\b", re.I)),
)
CANONICAL_TASK_HEADINGS = {
    "broken window",
    "cat rescue",
    "cinderella",
    "cinderella story",
    "communication question",
    "communication story",
    "illness story",
    "important life event",
    "life event",
    "pb&j",
    "procedural pb&j",
    "refused umbrella",
    "umbrella refusal",
}

SECTION_NAMES = {
    "original": "original",
    "ciuoriginal": "original",
    "ciuwords": "words",
    "ciucomplete": "complete",
}
TOKEN_RE = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*|\d+(?:[.,]\d+)*", re.UNICODE)
SPEAKER_RE = re.compile(r"^\s*\*?\s*(PAR|EXP)\s*[:=]\s*", re.I)
COUNT_RE = re.compile(
    r"^\s*(WORDS?|CIU)(?:\s+COUNT)?\s*[:=]\s*(\d+)\s*$", re.I
)
COUNT_SUFFIX_RE = re.compile(
    r"\s+(?:WORDS?|CIU)(?:\s+COUNT)?\s*[:=]\s*\d+\s*$", re.I
)
SUMMARY_RE = re.compile(r"^\s*(?:%?\s*CIU\s*%|\d+\s*/\s*\d+\s*=)", re.I)
FALSE_VALUES = {"0", "false", "off", "no"}


@dataclass(frozen=True, slots=True)
class Fragment:
    text: str
    struck: bool


@dataclass(frozen=True, slots=True)
class Paragraph:
    fragments: tuple[Fragment, ...]
    number: int
    tracked_change: bool = False

    @property
    def text(self) -> str:
        return "".join(fragment.text for fragment in self.fragments)


@dataclass(frozen=True, slots=True)
class Token:
    index: int
    utterance: int
    text: str
    word: bool
    ciu: bool | None


@dataclass(frozen=True, slots=True)
class GoldItem:
    task_id: str
    item_id: str
    stimulus: str
    participant_id: str
    session: int
    source_document: str
    tokens: tuple[Token, ...]


@dataclass(frozen=True, slots=True)
class ConversionIssue:
    severity: str
    code: str
    document: str
    message: str
    task_id: str | None = None


@dataclass(slots=True)
class ConversionResult:
    source_dir: str
    documents_seen: int = 0
    primary_documents: int = 0
    reliability_documents: int = 0
    unscored_primary_documents: int = 0
    candidate_items: int = 0
    rejected_items: int = 0
    items: list[GoldItem] = field(default_factory=list)
    issues: list[ConversionIssue] = field(default_factory=list)

    @property
    def errors(self) -> tuple[ConversionIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    def summary(self) -> dict[str, object]:
        by_task: dict[str, int] = defaultdict(int)
        for item in self.items:
            by_task[item.task_id] += 1
        return {
            "source_dir": self.source_dir,
            "documents_seen": self.documents_seen,
            "primary_documents": self.primary_documents,
            "reliability_documents": self.reliability_documents,
            "unscored_primary_documents": self.unscored_primary_documents,
            "accepted_items": len(self.items),
            "candidate_items": self.candidate_items,
            "rejected_items": self.rejected_items,
            "items_by_task": dict(sorted(by_task.items())),
            "errors": len(self.errors),
            "warnings": sum(issue.severity == "warning" for issue in self.issues),
        }

    def report(self) -> dict[str, object]:
        return {
            "summary": self.summary(),
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(slots=True)
class _TaskSections:
    values: dict[str, list[Paragraph]] = field(default_factory=dict)
    duplicate_sections: set[str] = field(default_factory=set)


def convert_directory(source_dir: str | Path) -> ConversionResult:
    """Convert all primary scored DOCX files in a source directory."""

    source = Path(source_dir)
    result = ConversionResult(source_dir=str(source.resolve()))
    if not source.is_dir():
        raise ValueError(f"source directory not found: {source}")

    seen_items: dict[tuple[str, str], str] = {}
    documents = sorted(
        path for path in source.glob("*.docx") if not path.name.startswith("._")
    )
    result.documents_seen = len(documents)
    for path in documents:
        if _document_kind(path) != "primary":
            result.reliability_documents += 1
            continue
        result.primary_documents += 1
        try:
            paragraphs = read_docx_paragraphs(path)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            result.issues.append(
                ConversionIssue("error", "unreadable_docx", path.name, str(error))
            )
            continue

        sections = _collect_task_sections(paragraphs)
        scored = {
            task_id: block
            for task_id, block in sections.items()
            if "words" in block.values or "complete" in block.values
        }
        if not scored:
            result.unscored_primary_documents += 1
            continue

        metadata = _document_metadata(path)
        if metadata is None:
            result.issues.append(
                ConversionIssue(
                    "error",
                    "filename_metadata",
                    path.name,
                    "could not identify participant and CIU session from filename",
                )
            )
            continue
        participant_id, session = metadata

        for task_id, block in scored.items():
            result.candidate_items += 1
            item_id = f"{participant_id}_s{session}"
            key = (task_id, item_id)
            if key in seen_items:
                result.issues.append(
                    ConversionIssue(
                        "error",
                        "duplicate_item",
                        path.name,
                        f"duplicates {item_id} from {seen_items[key]}",
                        task_id,
                    )
                )
                continue
            item, issues = _convert_task(
                path, task_id, item_id, participant_id, session, block
            )
            result.issues.extend(issues)
            if item is not None:
                seen_items[key] = path.name
                result.items.append(item)
            else:
                result.rejected_items += 1

    result.items.sort(key=lambda item: (item.task_id, item.participant_id, item.session))
    return result


def read_docx_paragraphs(path: str | Path) -> tuple[Paragraph, ...]:
    """Read paragraph text and direct run-level strikethrough from a DOCX."""

    source = Path(path)
    try:
        with zipfile.ZipFile(source) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as error:
        raise ValueError("DOCX has no word/document.xml") from error
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as error:
        raise ValueError(f"invalid WordprocessingML: {error}") from error

    paragraphs: list[Paragraph] = []
    for number, node in enumerate(root.iter(f"{W}p"), start=1):
        fragments: list[Fragment] = []
        tracked_change = False

        def visit(
            element: ElementTree.Element,
            output: list[Fragment],
            *,
            deleted: bool = False,
        ) -> None:
            nonlocal tracked_change
            if element.tag in {f"{W}del", f"{W}ins", f"{W}moveFrom", f"{W}moveTo"}:
                tracked_change = True
                deleted = deleted or element.tag in {f"{W}del", f"{W}moveFrom"}
            if element.tag == f"{W}r":
                if deleted:
                    return
                struck = _run_is_struck(element)
                for descendant in element.iter():
                    if descendant.tag == f"{W}t" and descendant.text:
                        output.append(Fragment(descendant.text, struck))
                    elif descendant.tag == f"{W}tab":
                        output.append(Fragment("\t", struck))
                    elif descendant.tag == f"{W}br":
                        output.append(Fragment("\n", struck))
                    elif descendant.tag == f"{W}noBreakHyphen":
                        output.append(Fragment("-", struck))
                return
            for child in element:
                visit(child, output, deleted=deleted)

        visit(node, fragments)
        paragraphs.append(Paragraph(tuple(fragments), number, tracked_change))
    return tuple(paragraphs)


def render_gold_files(result: ConversionResult) -> dict[str, str]:
    """Render accepted items into one gold file per elicitation task."""

    grouped: dict[str, list[GoldItem]] = defaultdict(list)
    for item in result.items:
        grouped[item.task_id].append(item)

    rendered: dict[str, str] = {}
    for task_id, items in sorted(grouped.items()):
        lines = [
            f"# Converted token-level CIU reference data for {TASKS[task_id]}.",
            "# Source annotations were clinically reviewed; conversion is deterministic.",
            "# Columns: index, utterance, token, WORD|NOT_WORD, CIU|NOT_CIU|NA.",
        ]
        for item in items:
            lines.extend(
                [
                    f"=== item: {item.item_id} ===",
                    "[input]",
                    f"stimulus: {_yaml_scalar(item.stimulus)}",
                    f"participant_id: {_yaml_scalar(item.participant_id)}",
                    f"session: {item.session}",
                    f"source_document: {_yaml_scalar(item.source_document)}",
                    "[gold]",
                ]
            )
            for token in item.tokens:
                word_label = "WORD" if token.word else "NOT_WORD"
                ciu_label = "NA" if token.ciu is None else "CIU" if token.ciu else "NOT_CIU"
                lines.append(
                    f"{token.index}\t{token.utterance}\t{token.text}\t{word_label}\t{ciu_label}"
                )
            lines.append("")
        rendered[f"gold_{task_id}.txt"] = "\n".join(lines).rstrip() + "\n"
    return rendered


def write_conversion(
    result: ConversionResult,
    output_dir: str | Path,
    *,
    force: bool = False,
    allow_partial: bool = False,
    base_gold_dir: str | Path | None = None,
) -> tuple[Path, ...]:
    """Atomically write rendered gold files, refusing unsafe partial output."""

    if result.errors and not allow_partial:
        raise ValueError(
            f"conversion has {len(result.errors)} error(s); review them or use --allow-partial"
        )
    rendered = render_gold_files(result)
    if not rendered:
        raise ValueError("conversion produced no accepted items")
    destination = Path(output_dir)
    if base_gold_dir is not None:
        base_directory = Path(base_gold_dir)
        for filename, generated in tuple(rendered.items()):
            base_path = base_directory / filename
            if base_path.is_file():
                existing_text = base_path.read_text(encoding="utf-8")
                existing_ids = set(re.findall(r"(?m)^=== item: (.+?) ===$", existing_text))
                generated_ids = set(re.findall(r"(?m)^=== item: (.+?) ===$", generated))
                duplicates = sorted(existing_ids & generated_ids)
                if duplicates:
                    raise ValueError(
                        f"base gold {filename} duplicates generated item(s): "
                        + ", ".join(duplicates)
                    )
                rendered[filename] = existing_text.rstrip() + "\n\n" + generated
    destination.mkdir(parents=True, exist_ok=True)
    existing = [
        destination / filename
        for filename in rendered
        if (destination / filename).exists()
    ]
    if existing and not force:
        names = ", ".join(path.name for path in existing)
        raise ValueError(f"refusing to overwrite existing file(s): {names}")

    written: list[Path] = []
    for filename, contents in rendered.items():
        target = destination / filename
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination, delete=False
        ) as handle:
            handle.write(contents)
            temporary = Path(handle.name)
        temporary.replace(target)
        written.append(target)
    return tuple(written)


def _collect_task_sections(paragraphs: Iterable[Paragraph]) -> dict[str, _TaskSections]:
    collected: dict[str, _TaskSections] = defaultdict(_TaskSections)
    current_task: str | None = None
    current_section: str | None = None
    for paragraph in paragraphs:
        text = paragraph.text.strip().rstrip("\\").strip()
        if not text:
            continue
        task_id = _identify_task(text)
        section = _identify_section(text)
        if task_id is not None:
            # Preserve the historical ciu_scoring identifier for Cat Rescue.
            current_task = "ciu_scoring" if task_id == "ciu_cat_rescue" else task_id
            current_section = None
            if section is None and _normalized_heading(text).endswith("original"):
                section = "original"
        if section is not None:
            if current_task is None:
                current_section = None
                continue
            block = collected[current_task]
            if section in block.values:
                block.duplicate_sections.add(section)
            else:
                block.values[section] = []
            current_section = section
            continue
        if current_task is not None and current_section is not None:
            collected[current_task].values[current_section].append(paragraph)
    return dict(collected)


def _convert_task(
    path: Path,
    task_id: str,
    item_id: str,
    participant_id: str,
    session: int,
    block: _TaskSections,
) -> tuple[GoldItem | None, list[ConversionIssue]]:
    issues: list[ConversionIssue] = []

    def error(code: str, message: str) -> None:
        issues.append(ConversionIssue("error", code, path.name, message, task_id))

    if block.duplicate_sections:
        error(
            "duplicate_section",
            "duplicate section(s): " + ", ".join(sorted(block.duplicate_sections)),
        )
    for required in ("words", "complete"):
        if required not in block.values:
            error("missing_section", f"missing CIU_{required} section")
    if issues:
        return None, issues

    word_rows, word_token_issues = _section_tokens(block.values["words"], path, task_id)
    complete_rows, complete_token_issues = _section_tokens(
        block.values["complete"], path, task_id
    )
    issues.extend(word_token_issues)
    issues.extend(complete_token_issues)
    word_total = _declared_total(block.values["words"], "WORDS")
    ciu_total = _declared_total(block.values["complete"], "CIU")
    if word_total is None:
        error("missing_total", "CIU_words has no unique WORDS total")
    if ciu_total is None:
        # A known source variant labels the final CIU total as WORDS.
        ciu_total = _declared_total(block.values["complete"], "WORDS")
        if ciu_total is not None:
            issues.append(
                ConversionIssue(
                    "warning",
                    "mislabeled_ciu_total",
                    path.name,
                    "CIU_complete total was labeled WORDS and interpreted as CIU",
                    task_id,
                )
            )
        else:
            error("missing_total", "CIU_complete has no unique CIU total")

    word_tokens = [row for row in word_rows if not row[2]]
    if word_total is not None and len(word_tokens) != word_total:
        error(
            "word_total_mismatch",
            f"declared {word_total} words but formatting identifies {len(word_tokens)}",
        )
    if ciu_total is not None:
        actual_cius = sum(not struck for _, _, struck in complete_rows)
        if actual_cius != ciu_total:
            error(
                "ciu_total_mismatch",
                f"declared {ciu_total} CIUs but formatting identifies {actual_cius}",
            )
    if len(word_tokens) != len(complete_rows):
        error(
            "alignment_length",
            f"CIU_words has {len(word_tokens)} counted words but CIU_complete has "
            f"{len(complete_rows)} tokens",
        )
    else:
        for position, (word_row, complete_row) in enumerate(
            zip(word_tokens, complete_rows, strict=True), start=1
        ):
            if _normalize_token(word_row[1]) != _normalize_token(complete_row[1]):
                error(
                    "alignment_token",
                    f"word/CIU token mismatch at counted-word position {position}: "
                    f"{word_row[1]!r} != {complete_row[1]!r}",
                )
                break
    if any(issue.severity == "error" for issue in issues):
        return None, issues

    complete_iter = iter(complete_rows)
    tokens: list[Token] = []
    for index, (utterance, text, struck) in enumerate(word_rows, start=1):
        if struck:
            tokens.append(Token(index, utterance, text, False, None))
            continue
        _complete_utterance, _complete_text, complete_struck = next(complete_iter)
        tokens.append(Token(index, utterance, text, True, not complete_struck))
    return (
        GoldItem(
            task_id=task_id,
            item_id=item_id,
            stimulus=TASKS[task_id],
            participant_id=participant_id,
            session=session,
            source_document=path.name,
            tokens=tuple(tokens),
        ),
        issues,
    )


def _section_tokens(
    paragraphs: Iterable[Paragraph], path: Path, task_id: str
) -> tuple[list[tuple[int, str, bool]], list[ConversionIssue]]:
    rows: list[tuple[int, str, bool]] = []
    issues: list[ConversionIssue] = []
    utterance = 0
    tracked_change_reported = False
    for paragraph in paragraphs:
        match = SPEAKER_RE.match(paragraph.text)
        if match is not None and match.group(1).upper() != "PAR":
            continue
        if COUNT_RE.fullmatch(paragraph.text.strip()) or SUMMARY_RE.match(paragraph.text):
            continue
        utterance += 1
        if paragraph.tracked_change and not tracked_change_reported:
            tracked_change_reported = True
            issues.append(
                ConversionIssue(
                    "error",
                    "tracked_change",
                    path.name,
                    f"tracked change in scored paragraph {paragraph.number}",
                    task_id,
                )
            )
        text, flags = _styled_slice(paragraph, match.end() if match else 0)
        suffix = COUNT_SUFFIX_RE.search(text)
        if suffix:
            text = text[: suffix.start()]
            flags = flags[: suffix.start()]
        for token_match in TOKEN_RE.finditer(text):
            token_flags = flags[token_match.start() : token_match.end()]
            if token_flags and any(token_flags) and not all(token_flags):
                issues.append(
                    ConversionIssue(
                        "error",
                        "mixed_strikethrough",
                        path.name,
                        f"partially struck token in paragraph {paragraph.number}",
                        task_id,
                    )
                )
            rows.append((utterance, token_match.group(), bool(token_flags and all(token_flags))))
    return rows, issues


def _styled_slice(paragraph: Paragraph, start: int) -> tuple[str, list[bool]]:
    text = paragraph.text
    flags: list[bool] = []
    for fragment in paragraph.fragments:
        flags.extend([fragment.struck] * len(fragment.text))
    return text[start:], flags[start:]


def _declared_total(paragraphs: Iterable[Paragraph], label: str) -> int | None:
    values = []
    for paragraph in paragraphs:
        match = COUNT_RE.fullmatch(paragraph.text.strip())
        matched_label = match.group(1).upper() if match else ""
        if match and (
            matched_label == label or (label == "WORDS" and matched_label == "WORD")
        ):
            values.append(int(match.group(2)))
    return values[0] if len(values) == 1 else None


def _run_is_struck(run: ElementTree.Element) -> bool:
    properties = run.find(f"{W}rPr")
    if properties is None:
        return False
    for name in ("strike", "dstrike"):
        element = properties.find(f"{W}{name}")
        if element is not None and element.get(f"{W}val", "true").lower() not in FALSE_VALUES:
            return True
    return False


def _identify_task(text: str) -> str | None:
    if SPEAKER_RE.match(text) or len(text) > 120:
        return None
    normalized = _normalized_heading(text).rstrip(":")
    heading_like = normalized in CANONICAL_TASK_HEADINGS or bool(
        re.search(r"\b(?:ciu|original|day\s*[12]|rc\s*\d+)\b", normalized, re.I)
    )
    if not heading_like:
        return None
    for task_id, pattern in TASK_PATTERNS:
        if pattern.search(text):
            return task_id
    return None


def _identify_section(text: str) -> str | None:
    key = re.sub(r"[^a-z]", "", text.casefold())
    return SECTION_NAMES.get(key)


def _normalized_heading(text: str) -> str:
    return " ".join(text.casefold().split())


def _normalize_token(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("\u2019", "'")


def _document_kind(path: Path) -> str:
    name = path.stem.casefold()
    if re.search(r"(?:intra|inter)[\s_-]*rater", name) or "_jma" in name:
        return "reliability"
    return "primary"


def _document_metadata(path: Path) -> tuple[str, int] | None:
    # Underscores are word characters to ``\b``; the source names commonly use
    # ``CIU1_RC2``, so use explicit digit boundaries instead.
    session_match = re.search(r"CIU\s*([12])(?!\d)", path.stem, re.I)
    participant_match = re.search(r"RC\s*0*(\d+)(?!\d)", path.stem, re.I)
    if session_match is None or participant_match is None:
        return None
    participant = f"rc{int(participant_match.group(1)):03d}"
    return participant, int(session_match.group(1))


def _yaml_scalar(value: str) -> str:
    # JSON strings are valid YAML scalars and never add PyYAML's ``...`` marker.
    return json.dumps(value, ensure_ascii=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert reviewed CIU DOCX annotations into task-separated gold files."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--base-gold-dir",
        type=Path,
        help="prepend existing task gold (for manually curated seed items)",
    )
    parser.add_argument("--write", action="store_true", help="write converted gold files")
    parser.add_argument("--force", action="store_true", help="overwrite generated filenames")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="write accepted items even when other items need review",
    )
    parser.add_argument("--report-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = convert_directory(args.source_dir)
        if args.report_json:
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(
                json.dumps(result.report(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        written: tuple[Path, ...] = ()
        if args.write:
            if args.output_dir is None:
                raise ValueError("--output-dir is required with --write")
            written = write_conversion(
                result,
                args.output_dir,
                force=args.force,
                allow_partial=args.allow_partial,
                base_gold_dir=args.base_gold_dir,
            )
    except ValueError as error:
        print(f"error: {error}")
        return 2

    print(json.dumps(result.summary(), indent=2, sort_keys=True))
    for path in written:
        print(f"wrote {path}")
    return 1 if result.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
