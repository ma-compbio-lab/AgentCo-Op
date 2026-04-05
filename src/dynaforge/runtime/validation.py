from __future__ import annotations

from typing import Any, List, Mapping

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
