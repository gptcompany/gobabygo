"""JSON Schema validation for mesh command events.

Uses Draft 2020-12 via the jsonschema library. The schema is loaded
from schemas/command_event.schema.json at module level.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "schemas"
_SCHEMA_PATH = _SCHEMA_DIR / "command_event.schema.json"

_schema: dict | None = None
_validator: Draft202012Validator | None = None


def _get_validator() -> Draft202012Validator:
    """Lazy-load and cache the schema validator."""
    global _schema, _validator
    if _validator is None:
        with open(_SCHEMA_PATH) as f:
            _schema = json.load(f)
        assert _schema is not None
        _validator = Draft202012Validator(_schema)
    return _validator


def load_schema() -> dict:
    """Load and return the command event JSON Schema."""
    _get_validator()
    assert _schema is not None
    return _schema


def validate_event_data(data: dict) -> list[str]:
    """Validate event data dict against the command event schema.

    Returns a list of error messages. Empty list means valid.
    """
    validator = _get_validator()
    errors = []
    for error in validator.iter_errors(data):
        errors.append(error.message)
    return errors
