"""Block user input that matches the lab's Elastic detection rule phrases."""
import re
from typing import Any
from guardrail import GuardrailDecision, guardrail_settings, input_settings


def _tokens(text: str) -> list[str]:
    # Approximate Elasticsearch's standard analyzer: case-insensitive word tokens.
    return re.findall(r"\w+", text.casefold())


def _contains_phrase(tokens: list[str], phrase: str) -> bool:
    wanted = _tokens(phrase)
    return bool(wanted) and any(tokens[i:i + len(wanted)] == wanted for i in range(len(tokens) - len(wanted) + 1))


def inspect_content(message: str, config: dict[str, Any]) -> GuardrailDecision:
    settings = input_settings(config)
    if not guardrail_settings(config).get("enabled", True) or not settings.get("enabled", True):
        return GuardrailDecision(triggered=False)
    # Elastic rules match one log line at a time, so only the newest message is checked.
    tokens = _tokens(message)
    categories = tuple(rule_id for rule_id, phrases in settings.get("_detection_keywords", {}).items()
                       if any(_contains_phrase(tokens, phrase) for phrase in phrases))
    return GuardrailDecision(triggered=bool(categories), categories=categories)
