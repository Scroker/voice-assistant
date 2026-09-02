"""Offline skill registry for semantic intent matching.

This module provides the minimal feature set needed for offline matching of colloquial
phrases against known intents without requiring an external vector database.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("VoiceAssistant.SkillRegistry")


class SkillRegistry:
    """Registry of skills and semantic triggers."""

    DEFAULT_SKILLS = [
        {
            "intent": "volume_up",
            "name": "Volume Up",
            "triggers": [
                "alza il volume",
                "aumenta il volume",
                "più forte",
                "rendi più alto il volume",
                "metti il volume più alto",
                "volume up",
                "alza volume",
                "alza un po' il volume",
            ],
            "params": {"delta": 10},
        },
        {
            "intent": "volume_down",
            "name": "Volume Down",
            "triggers": [
                "abbassa il volume",
                "più basso il volume",
                "riduci volume",
                "volume down",
            ],
            "params": {"delta": -10},
        },
        {
            "intent": "mute",
            "name": "Mute",
            "triggers": [
                "silenzia il volume",
                "mute audio",
                "smetti il suono",
                "zitto",
            ],
            "params": {},
        },
        {
            "intent": "set_theme_dark",
            "name": "Dark Theme",
            "triggers": [
                "tema scuro",
                "modalità scura",
                "attiva il tema scuro",
                "metti scuro",
                "voglio il tema scuro",
            ],
            "params": {"dark": True},
        },
        {
            "intent": "set_theme_light",
            "name": "Light Theme",
            "triggers": [
                "tema chiaro",
                "modalità chiara",
                "attiva tema chiaro",
                "metti chiaro",
            ],
            "params": {"dark": False},
        },
        {
            "intent": "launch_app",
            "name": "Launch App",
            "triggers": [
                "apri firefox",
                "lancia il browser",
                "apri il terminale",
                "avvia il calendario",
                "apri le impostazioni",
            ],
            "params": {"app": "firefox"},
        },
        {
            "intent": "get_time",
            "name": "Get Time",
            "triggers": [
                "che ore sono",
                "che ora e",
                "dimmi l'ora",
                "orario",
            ],
            "params": {},
        },
        {
            "intent": "get_date",
            "name": "Get Date",
            "triggers": [
                "che giorno e",
                "data di oggi",
                "dimmi la data",
            ],
            "params": {},
        },
    ]

    @staticmethod
    def _parse_markdown_skill(path: Path) -> Optional[Dict[str, Any]]:
        """Parse a basic SKILL.md file with YAML frontmatter."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
        if not match:
            return None

        frontmatter = match.group(1)
        body = match.group(2).strip()
        metadata: Dict[str, Any] = {"_body": body}
        lines = frontmatter.splitlines()
        idx = 0

        while idx < len(lines):
            raw_line = lines[idx]
            line = raw_line.strip()
            if not line or line.startswith("#"):
                idx += 1
                continue
            if ":" not in line:
                idx += 1
                continue

            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            if value == "":
                values: List[str] = []
                idx += 1
                while idx < len(lines):
                    next_line = lines[idx]
                    stripped = next_line.strip()
                    if not stripped or stripped.startswith("#"):
                        idx += 1
                        continue
                    item_match = re.match(r"^-\s*(.*)$", stripped)
                    if not item_match:
                        break
                    item = item_match.group(1).strip()
                    if item.startswith('"') and item.endswith('"'):
                        item = item[1:-1]
                    elif item.startswith("'") and item.endswith("'"):
                        item = item[1:-1]
                    values.append(item)
                    idx += 1
                metadata[key] = values
                continue

            if value.startswith("[") and value.endswith("]"):
                try:
                    metadata[key] = json.loads(value)
                except Exception:
                    metadata[key] = [item.strip().strip('"') for item in value[1:-1].split(",") if item.strip()]
            elif value.startswith('"') and value.endswith('"'):
                metadata[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                metadata[key] = value[1:-1]
            else:
                metadata[key] = value
            idx += 1

        if "triggers" not in metadata and "name" not in metadata:
            return None

        skill = {
            "name": metadata.get("name", path.stem.replace("_", " ").title()),
            "description": metadata.get("description", ""),
            "triggers": metadata.get("triggers", []),
            "tools_allowed": metadata.get("tools_allowed", []),
            "intent": metadata.get("intent", path.stem.lower().replace("-", "_")),
            "source": str(path),
            "_body": body,
        }
        return skill

    def __init__(self, skills: Optional[Iterable[Dict[str, Any]]] = None):
        self.skills: List[Dict[str, Any]] = list(skills or [])

    @classmethod
    def from_directory(cls, base_dir: Path) -> "SkillRegistry":
        """Build a registry from a directory containing JSON skills and/or SKILL.md files."""
        registry = cls()
        base_dir = Path(base_dir)
        if not base_dir.exists():
            return registry

        for skill_file in sorted(base_dir.iterdir()):
            if skill_file.suffix.lower() == ".json":
                try:
                    with skill_file.open("r", encoding="utf-8") as fh:
                        payload = json.load(fh)
                    if isinstance(payload, dict):
                        registry.skills.append(payload)
                    elif isinstance(payload, list):
                        registry.skills.extend(payload)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to load skill file %s: %s", skill_file, exc)
            elif skill_file.name.lower().endswith(".md"):
                parsed = cls._parse_markdown_skill(skill_file)
                if parsed:
                    registry.skills.append(parsed)

        return registry

    @classmethod
    def from_default_directory(cls, base_dir: Optional[Path] = None) -> "SkillRegistry":
        """Build a registry from the built-in default skills.

        If a real skills directory exists, it is used; otherwise the in-memory default list is used.
        """
        registry = cls()
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent

        user_dir = Path.home() / ".config" / "voice-assistant" / "skills"
        searched_dirs = [
            base_dir / "default_skills",
            base_dir / "skills" / "default_skills",
            user_dir,
        ]

        for skill_dir in searched_dirs:
            if skill_dir.exists():
                registry = cls.from_directory(skill_dir)
                if registry.skills:
                    break

        if not registry.skills:
            registry.skills = [dict(skill) for skill in cls.DEFAULT_SKILLS]

        return registry

    def add_skill(self, skill: Dict[str, Any]) -> None:
        self.skills.append(dict(skill))

    def find_by_intent(self, intent: str) -> Optional[Dict[str, Any]]:
        for skill in self.skills:
            if skill.get("intent") == intent:
                return skill
        return None

    def iter_triggers(self) -> Iterable[str]:
        for skill in self.skills:
            for trigger in skill.get("triggers", []):
                yield str(trigger)
