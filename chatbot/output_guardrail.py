"""Output credential masking rules."""
import re
from typing import Any
from guardrail import OutputDecision, guardrail_settings, output_settings

_OUTPUT_RULES: dict[str, re.Pattern[str]] = {
    "openai_api_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "connection_string": re.compile(r"(?i)(://[^:/\s]+:)([^@/\s]+)(@)"),
    "secret_assignment": re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|secret)\b"
        r"\s*[:=]\s*[\"']?)([^\s\"',;]{8,})"
    ),
}

def inspect_output(content: str, config: dict[str, Any]) -> OutputDecision:
    """Mask credential-like values in the generated response before returning it."""
    settings = output_settings(config)
    if not guardrail_settings(config).get("enabled", True) or not settings.get("enabled", False):
        return OutputDecision(content=content)
    enabled_rules = settings.get("rules", {})
    masked = content
    categories = []
    for name, pattern in _OUTPUT_RULES.items():
        if not enabled_rules.get(name, True):
            continue
        def redact(match: re.Match[str]) -> str:
            if name == "connection_string":
                return f"{match.group(1)}[REDACTED]{match.group(3)}"
            return f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]"

        masked, count = pattern.subn(redact, masked)
        if count:
            categories.append(name)
    return OutputDecision(content=masked, categories=tuple(categories))


