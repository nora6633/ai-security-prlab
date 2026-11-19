"""Per-lab challenge graders that compare replies with the lab's reference values."""

from typing import Any


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _compact(value: str) -> str:
    # Spaced-out disclosures such as "l a b u s e r" still count as the value.
    return "".join(value.split())


def _public_result(objective: dict[str, Any], passed: bool) -> dict[str, Any]:
    return {
        "id": objective.get("id"),
        "title": objective.get("title", "挑戰目標"),
        "passed": passed,
    }


def evaluate_response(response_text: str, challenge: dict[str, Any], judge_config: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every objective and return only a pass/fail result."""
    public_objectives = {objective.get("id"): objective for objective in challenge.get("objectives", [])}
    references = challenge.get("_references", {})
    results: dict[str, dict[str, Any]] = {}

    for objective in judge_config.get("objectives", []):
        public_objective = public_objectives.get(objective.get("id"), objective)
        if objective.get("type") == "contains_all_references":
            expected_values = [references.get(key, "") for key in objective.get("references", [])]
            passed = bool(expected_values) and all(expected_values) and all(_compact(value) in _compact(response_text) for value in expected_values)
        else:
            expected = references.get(objective.get("reference", ""), "")
            passed = bool(expected) and _normalize(expected) in _normalize(response_text)
        results[objective.get("id")] = _public_result(public_objective, passed)

    ordered = [results.get(item.get("id"), _public_result(item, False)) for item in challenge.get("objectives", [])]
    return {"type": "guardrail", "objectives": ordered}
