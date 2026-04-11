from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError


def validate_json_payload(schema: Mapping[str, Any] | None, payload: Any) -> List[str]:
    if not schema:
        return []

    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
    except SchemaError as exc:
        return [f"Invalid JSON schema: {exc.message}"]

    errors = sorted(validator.iter_errors(payload), key=lambda item: item.json_path)
    messages: List[str] = []
    for error in errors:
        location = error.json_path if error.json_path and error.json_path != "$" else "$"
        messages.append(f"{location}: {error.message}")
    return messages


def extract_partial_outputs(
    outputs: Dict[str, Any],
    schema: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Attempt to recover required fields from malformed node outputs.

    When output validation fails, this function tries to extract whatever
    required fields exist — from embedded JSON strings, nested dicts, etc.
    Returns the enriched outputs with a ``_degraded: True`` marker if any
    fields were recovered from non-standard locations.
    """
    if not schema or not isinstance(schema, Mapping):
        return outputs

    required = schema.get("required", [])
    if not required or not isinstance(required, list):
        return outputs

    missing = [field for field in required if field not in outputs]
    if not missing:
        return outputs

    recovered: Dict[str, Any] = {}

    # Strategy 1: Parse JSON from "text" field
    text_value = outputs.get("text")
    if isinstance(text_value, str):
        parsed = _try_parse_json(text_value)
        if isinstance(parsed, dict):
            _collect_fields(parsed, missing, recovered)

    # Strategy 2: Recursively search nested dicts
    still_missing = [f for f in missing if f not in recovered]
    if still_missing:
        _collect_fields(outputs, still_missing, recovered)

    if not recovered:
        return outputs

    result = dict(outputs)
    result.update(recovered)
    result["_degraded"] = True
    return result


def _try_parse_json(text: str) -> Any:
    """Try to parse JSON from a string, handling common wrapping patterns."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _collect_fields(
    source: Any,
    fields: list,
    target: Dict[str, Any],
    max_depth: int = 5,
) -> None:
    """Recursively search source dict for required fields."""
    if max_depth <= 0 or not isinstance(source, Mapping):
        return
    for field in fields:
        if field in target:
            continue
        if field in source and source[field] is not None:
            target[field] = source[field]
    still_missing = [f for f in fields if f not in target]
    if not still_missing:
        return
    for value in source.values():
        if isinstance(value, Mapping):
            _collect_fields(value, still_missing, target, max_depth - 1)
            still_missing = [f for f in fields if f not in target]
            if not still_missing:
                return
