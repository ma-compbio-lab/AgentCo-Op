from __future__ import annotations

from typing import Any, Dict, List, Mapping


def validate_json_payload(schema: Mapping[str, Any] | None, payload: Any) -> List[str]:
    if not schema:
        return []

    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError
    except ImportError:
        return _fallback_validate(schema, payload)

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


def _fallback_validate(schema: Mapping[str, Any], payload: Any) -> List[str]:
    messages: List[str] = []
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(payload, dict):
            return [f"$: expected object, got {type(payload).__name__}"]
        required = schema.get("required", [])
        for key in required:
            if key not in payload:
                messages.append(f"$: missing required property '{key}'")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in payload and isinstance(child_schema, dict):
                child_path = f"$.{key}"
                for message in _fallback_validate(child_schema, payload[key]):
                    messages.append(message.replace("$", child_path, 1))
    elif schema_type == "array":
        if not isinstance(payload, list):
            return [f"$: expected array, got {type(payload).__name__}"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(payload):
                for message in _fallback_validate(item_schema, item):
                    messages.append(message.replace("$", f"$[{index}]", 1))
    elif schema_type == "string" and not isinstance(payload, str):
        return [f"$: expected string, got {type(payload).__name__}"]
    elif schema_type == "number" and not isinstance(payload, (int, float)):
        return [f"$: expected number, got {type(payload).__name__}"]
    elif schema_type == "integer" and not isinstance(payload, int):
        return [f"$: expected integer, got {type(payload).__name__}"]
    elif schema_type == "boolean" and not isinstance(payload, bool):
        return [f"$: expected boolean, got {type(payload).__name__}"]
    return messages

