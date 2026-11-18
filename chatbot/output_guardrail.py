"""Detect sensitive data in model output through the lab's local detection service."""
import os
from typing import Any

import httpx

from guardrail import GuardrailDecision, guardrail_settings, output_settings


async def inspect_output(content: str, config: dict[str, Any]) -> GuardrailDecision:
    if not guardrail_settings(config).get("enabled", True) or not output_settings(config).get("enabled", False):
        return GuardrailDecision(triggered=False)
    try:
        url = os.getenv("OUTPUT_MASKER_URL", "http://output-masker:8000").rstrip("/")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{url}/mask", json={"text": content})
            response.raise_for_status()
            data = response.json()
        if type(data.get("detected")) is not bool:
            raise ValueError("Invalid detection response")
        return GuardrailDecision(triggered=data["detected"], categories=("sensitive_data",) if data["detected"] else ())
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("Output detection failed") from exc
