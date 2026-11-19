"""Block or mask model output that discloses protected or sensitive values."""
import re
from collections import Counter
from typing import Any

from guardrail import GuardrailDecision, guardrail_settings, output_settings, spaced_pattern


def inspect_output(content: str, config: dict[str, Any], references: dict[str, str]) -> GuardrailDecision:
    if not guardrail_settings(config).get("enabled", True) or not output_settings(config).get("enabled", False):
        return GuardrailDecision(triggered=False)
    # Plain substring matching: the value must appear exactly as stored (case-insensitive).
    text = content.casefold()
    categories = tuple(name for name in output_settings(config).get("references", [])
                       if references[name] and references[name].casefold() in text)
    return GuardrailDecision(triggered=bool(categories), categories=categories)


def mask_enabled(config: dict[str, Any]) -> bool:
    return guardrail_settings(config).get("enabled", True) and output_settings(config).get("mask", False)


def _mask(label: str) -> str:
    return f"[已遮罩:{label}]"


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


# (label, pattern, group to mask). Group 0 masks the whole match; a named group "value"
# masks only the secret and keeps the surrounding key, e.g. "password: [已遮罩:密碼或金鑰]".
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("私鑰", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}")),
    ("API 金鑰", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bsk-[A-Za-z0-9_-]{20,}")),
    ("連線帳密", re.compile(r"(?<=://)(?P<value>[^\s:/@]+:[^\s/@]+)(?=@)")),
    ("密碼或金鑰", re.compile(
        r"(?i)(?:\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key)\b[\"']?\s*[:=]\s*[\"']?"
        r"|密碼\s*(?:是|為|[:：=])\s*)(?P<value>[^\s\"',;}]+)")),
    ("電子郵件", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("身分證字號", re.compile(r"\b[A-Z][12]\d{8}\b")),
    ("手機號碼", re.compile(r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)")),
    ("信用卡號", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")),
]


def mask_output(content: str, config: dict[str, Any], references: dict[str, str]) -> tuple[str, Counter]:
    """Replace sensitive values with a labelled mask and count what was masked per type.

    This is an optional extra outside the lab's intended path: unlike the exact-match block,
    it also catches the "space between every character" bypass.
    """
    counts: Counter = Counter()
    if not mask_enabled(config):
        return content, counts
    # The lab's own credentials first, longest first, including spaced-out spellings.
    for name in sorted(output_settings(config).get("references", []), key=lambda key: len(references[key]), reverse=True):
        pattern = spaced_pattern(references[name])
        if pattern:
            content, replaced = re.subn(pattern, _mask("資料庫憑證"), content, flags=re.IGNORECASE)
            counts["資料庫憑證"] += replaced

    for label, pattern in _PATTERNS:
        def replace(match: re.Match[str]) -> str:
            text = match.group(0)
            if label == "信用卡號" and not _luhn(re.sub(r"\D", "", text)):
                return text
            # Already-masked values from an earlier pattern stay as they are.
            if "[已遮罩:" in text:
                return text
            counts[label] += 1
            if "value" in pattern.groupindex:
                start, end = match.span("value")
                offset = match.start()
                return text[:start - offset] + _mask(label) + text[end - offset:]
            return _mask(label)
        content = pattern.sub(replace, content)
    return content, +counts
