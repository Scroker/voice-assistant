"""Read/write helpers for user-editable skills, used by the GUI Skills console.

Built-in skills (under `default_skills/`) ship with the extension and are
read-only from the GUI's point of view: they get overwritten on every
update, so writing to them would silently lose the user's edits. Custom
skills created from the console are written as .md files under
`~/.config/voice-assistant/skills/`, the user override directory that
`SkillRegistry.from_default_directory()` already merges in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .skill_registry import SkillRegistry


def list_all_skills() -> List[Dict[str, Any]]:
    """Return every skill known to the registry, tagged with `is_custom`."""
    registry = SkillRegistry.from_default_directory()
    user_dir_str = str(SkillRegistry.get_user_skills_dir())
    result = []
    for skill in registry.skills:
        entry = {k: v for k, v in skill.items() if k != "_body"}
        source = str(entry.get("source", ""))
        entry["is_custom"] = source.startswith(user_dir_str)
        result.append(entry)
    return result


def _slugify_intent(intent: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", intent.strip().lower()).strip("_")
    return slug or "custom_skill"


def _yaml_quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _serialize_markdown_skill(skill: Dict[str, Any]) -> str:
    intent = str(skill["intent"]).strip()
    action_type = skill.get("action_type") or "system"
    lines = [
        "---",
        f"name: {_yaml_quote(skill.get('name') or intent)}",
        f"action_type: {_yaml_quote(action_type)}",
    ]
    if action_type == "system":
        if skill.get("tool"):
            lines.append(f"tool: {_yaml_quote(skill['tool'])}")
        lines.append(f"args: {json.dumps(skill.get('args') or {})}")
    elif action_type == "command":
        lines.append(f"command: {_yaml_quote(skill.get('command') or '')}")
    elif action_type == "prompt":
        lines.append(f"prompt: {_yaml_quote(skill.get('prompt') or '')}")
    elif action_type == "response":
        lines.append(f"response: {_yaml_quote(skill.get('response') or '')}")

    lines.append(f"intent: {_yaml_quote(intent)}")
    lines.append("triggers:")
    for trigger in skill["triggers"]:
        lines.append(f"  - {_yaml_quote(trigger)}")
    lines.append("---")
    lines.append("")
    if action_type == "prompt" and skill.get("prompt"):
        lines.append(str(skill.get("prompt")).strip())
    else:
        lines.append(f"Custom skill for intent '{intent}', created from the Skills console.")
    lines.append("")
    return "\n".join(lines)


def save_user_skill(skill: Dict[str, Any]) -> Path:
    """Validate and write a user-defined skill as a .md file. Returns the written path."""
    intent = str(skill.get("intent", "")).strip()
    if not intent:
        raise ValueError("L'intent è obbligatorio.")

    triggers = [str(t).strip() for t in (skill.get("triggers") or []) if str(t).strip()]
    if not triggers:
        raise ValueError("Serve almeno una frase di attivazione.")

    action_type = str(skill.get("action_type") or "system").strip().lower()
    if action_type not in ("system", "command", "prompt", "response"):
        action_type = "system"

    command = str(skill.get("command") or "").strip()
    prompt = str(skill.get("prompt") or "").strip()
    response = str(skill.get("response") or "").strip()
    tool = str(skill.get("tool") or "").strip()
    args = skill.get("args") or {}

    if action_type == "command":
        if not command:
            raise ValueError("Il comando terminale è obbligatorio.")
    elif action_type == "prompt":
        if not prompt:
            raise ValueError("Le istruzioni/prompt per l'AI sono obbligatorie.")
    elif action_type == "response":
        if not response:
            raise ValueError("Il testo della risposta è obbligatorio.")
    elif action_type == "system":
        if not isinstance(args, dict):
            raise ValueError("Args deve essere un oggetto JSON.")

    normalized = {
        "name": str(skill.get("name") or intent).strip(),
        "intent": intent,
        "action_type": action_type,
        "command": command,
        "prompt": prompt,
        "response": response,
        "tool": tool,
        "args": args if isinstance(args, dict) else {},
        "triggers": triggers,
    }

    user_dir = SkillRegistry.get_user_skills_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{_slugify_intent(intent)}.md"
    path.write_text(_serialize_markdown_skill(normalized), encoding="utf-8")
    return path


def delete_user_skill(intent: str) -> bool:
    """Delete a user-defined skill by intent. Returns False if it wasn't a custom skill."""
    path = SkillRegistry.get_user_skills_dir() / f"{_slugify_intent(intent)}.md"
    if path.exists():
        path.unlink()
        return True
    return False
