"""Comparing an observed value against the golden answer."""

from __future__ import annotations

import re
from typing import Any


def match_value(expected: Any, actual: Any, mode: str = "subset") -> bool:
    """Does ``actual`` satisfy ``expected`` under the given match mode?

    ``subset`` is the default because golden answers should pin down what must
    be true, not forbid every incidental field a server happens to return.
    """
    if mode == "exact":
        return expected == actual
    if mode == "contains":
        return _contains(expected, actual)
    if mode == "regex":
        try:
            return re.search(str(expected), _as_text(actual)) is not None
        except re.error:
            return False
    return _subset(expected, actual)


def _subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return all(any(_subset(item, candidate) for candidate in actual) for item in expected)
    return expected == actual


def _contains(expected: Any, actual: Any) -> bool:
    if isinstance(actual, (list, tuple)):
        return any(_contains(expected, item) for item in actual)
    return str(expected).lower() in _as_text(actual).lower()


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
