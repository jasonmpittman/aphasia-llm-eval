"""Fixed registry of safe derived-value functions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any


class DerivedValueError(ValueError):
    """A task's derived-value declaration cannot be evaluated safely."""


def percentage(numerator: Any, denominator: Any) -> float | None:
    """Return ``numerator / denominator * 100``, or null when undefined."""

    values = _numeric_pair(numerator, denominator)
    if values is None or values[1] == 0:
        return None
    return values[0] / values[1] * 100


def per_minute(value: Any, duration_seconds: Any) -> float | None:
    """Convert a count over seconds to a per-minute rate."""

    values = _numeric_pair(value, duration_seconds)
    if values is None or values[1] == 0:
        return None
    return values[0] / values[1] * 60


def ratio(numerator: Any, denominator: Any) -> float | None:
    """Return a ratio, or null for a missing or zero denominator."""

    values = _numeric_pair(numerator, denominator)
    if values is None or values[1] == 0:
        return None
    return values[0] / values[1]


def counts_by(values: Any) -> dict[str, int] | None:
    """Count string values, preserving a JSON-serializable mapping."""

    if values is None or isinstance(values, str | bytes) or not isinstance(values, Sequence):
        return None
    if any(not isinstance(value, str) for value in values):
        return None
    return dict(Counter(values))


def length(values: Any) -> int | None:
    """Return the length of a non-string sequence, or null for another value."""

    if values is None or isinstance(values, str | bytes) or not isinstance(values, Sequence):
        return None
    return len(values)


DERIVED_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "percentage": percentage,
    "per_minute": per_minute,
    "ratio": ratio,
    "counts_by": counts_by,
    "length": length,
}
DERIVED_ARITIES = {
    "percentage": 2,
    "per_minute": 2,
    "ratio": 2,
    "counts_by": 1,
    "length": 1,
}

_MISSING = object()


def derive_values(
    declarations: Sequence[Mapping[str, Any]] | None,
    answer: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate declared values in order against answer, inputs, and prior results."""

    derived: dict[str, Any] = {}
    context: dict[str, Any] = dict(inputs or {})
    context.update(answer)
    for declaration in declarations or ():
        name = declaration.get("name")
        function_name = declaration.get("function")
        arguments = declaration.get("args", [])
        optional = declaration.get("optional", False)
        if not isinstance(name, str) or not name:
            raise DerivedValueError("derived value requires a non-empty name")
        if name in context:
            raise DerivedValueError(f"derived value {name!r} conflicts with an existing field")
        try:
            function = DERIVED_FUNCTIONS[function_name]
        except (KeyError, TypeError) as error:
            raise DerivedValueError(f"unknown derived function {function_name!r}") from error
        if not isinstance(arguments, Sequence) or isinstance(arguments, str | bytes):
            raise DerivedValueError(f"derived value {name!r} args must be a list")
        expected_arity = DERIVED_ARITIES[function_name]
        if len(arguments) != expected_arity:
            raise DerivedValueError(
                f"derived function {function_name!r} requires {expected_arity} argument(s)"
            )

        resolved = [_resolve_path(context, argument) for argument in arguments]
        if _MISSING in resolved:
            if not optional:
                missing = arguments[resolved.index(_MISSING)]
                raise DerivedValueError(
                    f"derived value {name!r} cannot resolve argument {missing!r}"
                )
            value = None
        else:
            value = function(*resolved)
        derived[name] = value
        context[name] = value
    return derived


def with_derived_values(
    declarations: Sequence[Mapping[str, Any]] | None,
    answer: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of an answer augmented with its derived values."""

    result = dict(answer)
    result.update(derive_values(declarations, answer, inputs))
    return result


def _resolve_path(context: Mapping[str, Any], path: Any) -> Any:
    if not isinstance(path, str) or not path:
        return _MISSING
    current: Any = context
    for depth, component in enumerate(path.split(".")):
        if isinstance(current, Mapping):
            if component not in current:
                return _MISSING if depth == 0 else None
            current = current[component]
        elif isinstance(current, Sequence) and not isinstance(current, str | bytes):
            values = []
            for item in current:
                if not isinstance(item, Mapping) or component not in item:
                    return _MISSING
                values.append(item[component])
            current = values
        else:
            return _MISSING
    return current


def _numeric_pair(first: Any, second: Any) -> tuple[float, float] | None:
    if (
        first is None
        or second is None
        or isinstance(first, bool)
        or isinstance(second, bool)
        or not isinstance(first, int | float)
        or not isinstance(second, int | float)
    ):
        return None
    return float(first), float(second)
