"""Load and apply each lab's unified JSON guardrail policy."""

from dataclasses import dataclass
import json
import os
import re
import yaml
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GuardrailDecision:
    triggered: bool
    categories: tuple[str, ...] = ()
    matches: tuple[str, ...] = ()


DEFAULT_CONFIG_PATH = Path(__file__).with_name("guardrail-default.json")
DEFAULT_CHALLENGE_PATH = Path(__file__).with_name("challenge-default.json")

DEFAULT_RESPONSE = "I cannot provide that information."


def load_detection_keywords(rule_ids: list[str]) -> dict[str, tuple[str, ...]]:
    """Read quoted KQL phrases from the lab's exported Elastic detection rules."""
    rules_path = Path(os.getenv("DETECTION_RULES_PATH", "/etc/lab-detection/rules_export.ndjson"))
    try:
        rows = [json.loads(line) for line in rules_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load detection rules: {rules_path}") from exc
    keywords = {}
    for rule_id in rule_ids:
        rule = next((row for row in rows if isinstance(row, dict) and row.get("rule_id") == rule_id), None)
        if not rule or rule.get("language") != "kuery" or not isinstance(rule.get("query"), str):
            raise ValueError(f"Unknown KQL detection rule: {rule_id}")
        phrases = [re.sub(r'\\(["\\])', r"\1", match) for match in re.findall(r'\b[\w.]+\s*:\s*"((?:\\.|[^"\\])*)"', rule["query"])]
        keywords[rule_id] = tuple(dict.fromkeys(phrase for phrase in phrases if phrase.strip()))
    return keywords


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read and validate unified guardrail + evaluator JSON configuration."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot load guardrail config: {config_path}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("guardrail"), dict):
        raise ValueError("Config must contain a guardrail object")
    guardrail = config["guardrail"]
    if not isinstance(guardrail.get("enabled", True), bool):
        raise ValueError("guardrail.enabled must be true or false")
    response = guardrail.get("response", DEFAULT_RESPONSE)
    if not isinstance(response, str) or not response.strip():
        raise ValueError("guardrail.response must be a non-empty string")
    input_config = guardrail.get("input", {})
    if not isinstance(input_config, dict):
        raise ValueError("guardrail.input must be an object")
    if not isinstance(input_config.get("enabled", True), bool):
        raise ValueError("guardrail.input.enabled must be true or false")
    detection_rules = input_config.get("detection_rules", [])
    if not isinstance(detection_rules, list) or any(not isinstance(item, str) or not item for item in detection_rules):
        raise ValueError("guardrail.input.detection_rules must be a list of rule ids")
    input_config["_detection_keywords"] = load_detection_keywords(detection_rules) if detection_rules else {}
    output_config = guardrail.get("output", {})
    if not isinstance(output_config, dict):
        raise ValueError("guardrail.output must be an object")
    if not isinstance(output_config.get("enabled", False), bool):
        raise ValueError("guardrail.output.enabled must be true or false")
    output_references = output_config.get("references", [])
    if not isinstance(output_references, list) or any(not isinstance(item, str) or not item for item in output_references):
        raise ValueError("guardrail.output.references must be a list of challenge reference names")
    judge = config.get("judge", guardrail.get("judge"))
    if not isinstance(judge, dict) or judge.get("type") != "guardrail":
        raise ValueError("guardrail.judge.type must be 'guardrail'")
    if not isinstance(judge.get("objectives", []), list):
        raise ValueError("guardrail.judge.objectives must be a list")
    for objective in judge.get("objectives", []):
        if not isinstance(objective, dict) or not isinstance(objective.get("id"), str):
            raise ValueError("guardrail mode objectives require an id")
        kind = objective.get("type")
        if kind == "contains_reference" and isinstance(objective.get("reference"), str):
            continue
        if kind == "contains_all_references" and isinstance(objective.get("references"), list) and objective["references"] and all(isinstance(item, str) and item for item in objective["references"]):
            continue
        raise ValueError("Invalid guardrail objective references")
    return config


def guardrail_settings(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("guardrail", {})


def input_settings(config: dict[str, Any]) -> dict[str, Any]:
    return guardrail_settings(config).get("input", {})


def judge_settings(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("judge", guardrail_settings(config).get("judge", {}))


def output_settings(config: dict[str, Any]) -> dict[str, Any]:
    return guardrail_settings(config).get("output", {})


def refusal_for(config: dict[str, Any]) -> str:
    return guardrail_settings(config).get("response", DEFAULT_RESPONSE)


def load_challenge(path: str | Path | None = None) -> dict[str, Any]:
    challenge_path = Path(path) if path else DEFAULT_CHALLENGE_PATH
    try:
        with challenge_path.open("r", encoding="utf-8") as stream:
            challenge = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot load challenge config: {challenge_path}") from exc
    if not isinstance(challenge, dict) or not isinstance(challenge.get("system_prompt"), str):
        raise ValueError("Challenge config must define a system_prompt")
    references = challenge.get("references", {})
    if not isinstance(references, dict):
        raise ValueError("Challenge references must be an object")
    resolved = {}
    for name, env_name in references.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not isinstance(env_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
            raise ValueError("Invalid challenge reference")
        value = os.getenv(env_name)
        if not value:
            raise ValueError(f"Missing challenge environment variable: {env_name}")
        resolved[name] = value
    def resolve(match):
        name = match.group(1)
        if name not in resolved:
            raise ValueError(f"Unknown challenge reference: {name}")
        return resolved[name]
    challenge["system_prompt"] = re.sub(r"\{\{([a-z][a-z0-9_]*)\}\}", resolve, challenge["system_prompt"])
    challenge["_references"] = resolved
    objectives = challenge.get("objectives", [])
    if not isinstance(objectives, list) or any(not isinstance(item, dict) for item in objectives):
        raise ValueError("Challenge objectives must be a list of objects")
    return challenge


def append_system_text(messages: list[dict[str, str]], text: str) -> None:
    """Keep existing system messages and append trusted lab instructions."""
    system_message = next((message for message in messages if message["role"] == "system"), None)
    if system_message is None:
        messages.insert(0, {"role": "system", "content": text})
    else:
        system_message["content"] = f"{system_message['content']}\n\n{text}"
