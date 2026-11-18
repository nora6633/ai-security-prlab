"""Mask detected secrets in model output using Gitleaks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from guardrail import OutputDecision, guardrail_settings, output_settings


def inspect_output(content: str, config: dict[str, Any]) -> OutputDecision:
    if not guardrail_settings(config).get("enabled", True) or not output_settings(config).get("enabled", False):
        return OutputDecision(content=content)
    # Keep scan input and report in private, temporary files, preferably RAM-backed.
    ram_dir = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    try:
        with tempfile.TemporaryDirectory(prefix="lab-gitleaks-", dir=ram_dir) as temp_dir:
            root = Path(temp_dir)
            scan_dir = root / "scan"
            scan_dir.mkdir(mode=0o700)
            (scan_dir / "response.txt").write_text(content, encoding="utf-8")
            report_path = root / "report.json"
            result = subprocess.run(
                ["gitleaks", "dir", str(scan_dir), "--no-banner", "--no-color", "--log-level", "error",
                 "--exit-code", "42", "--report-format", "json", "--report-path", str(report_path)],
                capture_output=True, timeout=20, check=False,
            )
            if result.returncode not in (0, 42):
                raise RuntimeError("Gitleaks scan failed")
            if not report_path.exists():
                if result.returncode == 0:
                    return OutputDecision(content=content)
                raise RuntimeError("Gitleaks report missing")
            findings = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(findings, list) or (result.returncode == 42 and not findings):
                raise RuntimeError("Gitleaks report invalid")
            masked = content
            categories = []
            for finding in findings:
                secret = finding.get("Secret")
                if not isinstance(secret, str) or not secret or secret not in content:
                    raise RuntimeError("Gitleaks finding cannot be safely masked")
                masked = masked.replace(secret, "[REDACTED]")
                rule = finding.get("RuleID")
                if isinstance(rule, str) and rule not in categories:
                    categories.append(rule)
            return OutputDecision(content=masked, categories=tuple(categories))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        raise RuntimeError("Gitleaks scan failed") from exc
