from agentcoop.runtime.validation import extract_partial_outputs


def test_extract_from_text_field_with_embedded_json() -> None:
    outputs = {"text": '{"output": {"final_answer": "42", "notes": "computed"}}'}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "42"
    assert result["_degraded"] is True


def test_extract_from_nested_dict() -> None:
    outputs = {"wrapper": {"inner": {"final_answer": "7", "extra": "data"}}}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "7"
    assert result["_degraded"] is True


def test_returns_original_when_no_schema() -> None:
    outputs = {"text": "hello"}
    result = extract_partial_outputs(outputs, None)
    assert result == {"text": "hello"}
    assert "_degraded" not in result


def test_returns_original_when_no_required_fields() -> None:
    outputs = {"text": "hello"}
    schema = {"type": "object"}
    result = extract_partial_outputs(outputs, schema)
    assert result == {"text": "hello"}


def test_preserves_existing_fields_and_adds_recovered() -> None:
    outputs = {"confidence": 0.5, "text": '{"final_answer": "99"}'}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert result["final_answer"] == "99"
    assert result["confidence"] == 0.5
    assert result["_degraded"] is True


def test_handles_completely_missing_fields() -> None:
    outputs = {"text": "no json here at all"}
    schema = {"type": "object", "required": ["final_answer"]}
    result = extract_partial_outputs(outputs, schema)
    assert "final_answer" not in result
