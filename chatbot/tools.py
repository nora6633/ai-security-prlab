"""Read-only tools that let the lab chatbot reach its internal data directory."""

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

MAX_FILE_CHARS = 8000
MAX_SEARCH_RESULTS = 50


def _root() -> Path:
    return Path(os.getenv("INTERNAL_DATA_DIR", "/srv/internal"))


def _files(root: Path) -> list[str]:
    # Skip hidden entries such as the ..data links Kubernetes adds to ConfigMap volumes.
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def _config_path() -> Path:
    return _root() / os.getenv("INTERNAL_CONFIG_FILE", "app-config.json")


def tool_definitions() -> list[dict[str, Any]]:
    """Describe the tools, listing what is currently in the data directory."""
    try:
        files = _files(_root())
    except OSError:
        files = []
    try:
        config = json.loads(_config_path().read_text(encoding="utf-8"))
        sections = sorted(config) if isinstance(config, dict) else []
    except (OSError, ValueError):
        sections = []
    path_help = "File path returned by file_search."
    if files:
        path_help += " Available files: " + ", ".join(files)
    key_help = "Dot-separated key such as section.field."
    if sections:
        key_help += " Top-level sections: " + ", ".join(sections)
    return [
        {
            "type": "function",
            "function": {
                "name": "file_search",
                "description": "Searches for files matching a pattern.",
                "parameters": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string", "description": "Glob pattern or part of a file name, e.g. *.md; use * to list all files."}},
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "Reads the contents of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": path_help}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "config_lookup",
                "description": "Looks up configuration values from JSON.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": key_help}},
                    "required": ["key"],
                },
            },
        },
    ]


def _render(value: Any, references: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\{\{([a-z][a-z0-9_]*)\}\}", lambda match: references.get(match.group(1), match.group(0)), value)
    if isinstance(value, dict):
        return {key: _render(item, references) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, references) for item in value]
    return value


def _content_matches(path: Path, terms: list[str]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
    except OSError:
        return False
    return all(term in text for term in terms)


def file_search(pattern: str, references: dict[str, str]) -> dict[str, Any]:
    root = _root()
    files = _files(root)
    pattern = pattern.strip().casefold() or "*"
    name_pattern = pattern if any(char in pattern for char in "*?[") else f"*{pattern}*"
    # Models often search for a topic ("database*host*") rather than a file name,
    # so a file also matches when its contents contain every word of the pattern.
    terms = [term for term in re.split(r"[\s*?\[\]_./-]+", pattern) if term]
    matches = [
        path for path in files
        if fnmatch.fnmatchcase(path.casefold(), name_pattern) or (terms and _content_matches(root / path, terms))
    ]
    if not matches:
        return {"matches": [], "available_files": files, "hint": "Read one of the available files, or use config_lookup for settings."}
    return {"matches": matches[:MAX_SEARCH_RESULTS]}


def file_read(path: str, references: dict[str, str]) -> dict[str, Any]:
    root = _root()
    relative = path.strip().replace("\\", "/").removeprefix(root.as_posix()).lstrip("/").removeprefix("./")
    # Only files listed under the data directory are readable, which rules out traversal.
    if relative not in _files(root):
        return {"error": f"File not found: {path}"}
    content = _render((root / relative).read_text(encoding="utf-8", errors="replace"), references)
    return {"path": relative, "content": content[:MAX_FILE_CHARS], "truncated": len(content) > MAX_FILE_CHARS}


def config_lookup(key: str, references: dict[str, str]) -> dict[str, Any]:
    value: Any = _render(json.loads(_config_path().read_text(encoding="utf-8")), references)
    for part in [part for part in key.strip().split(".") if part]:
        if not isinstance(value, dict) or part not in value:
            return {"error": f"Config key not found: {key}", "available_keys": sorted(value) if isinstance(value, dict) else []}
        value = value[part]
    return {"key": key, "value": value}


_TOOLS = {"file_search": (file_search, "pattern"), "file_read": (file_read, "path"), "config_lookup": (config_lookup, "key")}


def run_tool(name: str, arguments: str, references: dict[str, str]) -> str:
    """Run one model-requested tool call and return its JSON result for the model."""
    try:
        tool, argument = _TOOLS[name]
        parsed = json.loads(arguments or "{}")
        if not isinstance(parsed, dict) or not isinstance(parsed.get(argument, ""), str):
            raise ValueError(f"{name} requires a string '{argument}' argument")
        result = tool(parsed.get(argument, ""), references)
    except KeyError:
        result = {"error": f"Unknown tool: {name}"}
    except (OSError, ValueError) as exc:
        result = {"error": str(exc)}
    return json.dumps(result, ensure_ascii=False)
