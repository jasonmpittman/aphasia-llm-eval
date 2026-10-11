"""Registry for safe, deterministic gold-section parsers."""

from __future__ import annotations

from .parsers import GoldParseError, GoldParser, ParsedGold, SourceLine, parse_kv, parse_token_table

GOLD_PARSERS: dict[str, GoldParser] = {
    "kv": parse_kv,
    "token_table": parse_token_table,
}


def get_gold_parser(name: str) -> GoldParser:
    """Return a registered parser or raise an actionable configuration error."""

    try:
        return GOLD_PARSERS[name]
    except KeyError as error:
        available = ", ".join(sorted(GOLD_PARSERS))
        raise GoldParseError(
            f"unknown gold parser {name!r}; available parsers: {available}"
        ) from error


__all__ = [
    "GOLD_PARSERS",
    "GoldParseError",
    "GoldParser",
    "ParsedGold",
    "SourceLine",
    "get_gold_parser",
]
