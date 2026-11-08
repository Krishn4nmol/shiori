"""Reading JSON out of LLM replies, which often wrap it in code fences or prose."""

import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in an LLM reply. Raises ValueError with a short reason."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("reply contained no JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as err:
        raise ValueError(f"reply was not valid JSON: {err.msg}") from err
    if not isinstance(data, dict):
        raise ValueError("reply JSON was not an object")
    return data
