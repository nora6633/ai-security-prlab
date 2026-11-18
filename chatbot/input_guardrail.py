"""User input detection rules."""
import re
from typing import Any
from guardrail import GuardrailDecision, guardrail_settings, input_settings

_RULES: dict[str, re.Pattern[str]] = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,100}\b"
        r"(previous|prior|above|all|system|developer)\b.{0,40}\b"
        r"(instruction|prompt|rule|message)s?\b"
        r"|忽略.{0,12}(之前|先前|所有|上面).{0,12}(指示|指令|規則|提示詞)", re.IGNORECASE,
    ),
    "prompt_disclosure": re.compile(
        r"\b(reveal|show|print|repeat|dump|expose|tell me)\b.{0,100}\b"
        r"(system prompt|hidden prompt|initial prompt|secret instruction)s?\b"
        r"|顯示.{0,12}(系統|隱藏|初始).{0,8}(提示詞|指令)|輸出.{0,12}(系統|隱藏).{0,8}(提示詞|指令)", re.IGNORECASE,
    ),
    "secret_extraction": re.compile(
        r"\b(reveal|show|print|dump|expose|return|find|give me|tell me)\b"
        r".{0,100}\b(flag|password|api key|token|credential|secret)s?\b"
        r"|(?:找出|給我|告訴我|輸出|顯示|列出).{0,20}(flag|密碼|金鑰|憑證|機密|token)", re.IGNORECASE,
    ),
}

def _tokens(text: str) -> list[str]:
    # Approximate Elasticsearch's standard analyzer: case-insensitive word tokens.
    return re.findall(r"\w+", text.casefold())


def _contains_phrase(tokens: list[str], phrase: str) -> bool:
    wanted = _tokens(phrase)
    return bool(wanted) and any(tokens[i:i + len(wanted)] == wanted for i in range(len(tokens) - len(wanted) + 1))


def inspect_content(content: str, config: dict[str, Any], latest_message: str = "") -> GuardrailDecision:
    settings = input_settings(config)
    if not guardrail_settings(config).get("enabled", True) or not settings.get("enabled", True):
        return GuardrailDecision(triggered=False)
    enabled_rules = settings.get("rules", {})
    categories = tuple(name for name, pattern in _RULES.items() if enabled_rules.get(name, True) and pattern.search(content))
    # Elastic rules match one log line at a time, so only check the newest message.
    latest_tokens = _tokens(latest_message)
    categories += tuple(rule_id for rule_id, phrases in settings.get("_detection_keywords", {}).items()
                        if any(_contains_phrase(latest_tokens, phrase) for phrase in phrases))
    return GuardrailDecision(triggered=bool(categories), categories=categories)


