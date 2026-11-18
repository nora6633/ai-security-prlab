"""Block model output that repeats the lab's protected reference values verbatim."""
from typing import Any

from guardrail import GuardrailDecision, guardrail_settings, output_settings


def inspect_output(content: str, config: dict[str, Any], references: dict[str, str]) -> GuardrailDecision:
    if not guardrail_settings(config).get("enabled", True) or not output_settings(config).get("enabled", False):
        return GuardrailDecision(triggered=False)
    # Plain substring matching: the value must appear exactly as stored (case-insensitive).
    text = content.casefold()
    categories = tuple(name for name in output_settings(config).get("references", [])
                       if references[name] and references[name].casefold() in text)
    return GuardrailDecision(triggered=bool(categories), categories=categories)
