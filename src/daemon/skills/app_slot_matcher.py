"""Fuzzy application name resolution against installed .desktop entries.

Used by the Direct Action Engine to turn a free-text app name extracted from
speech (e.g. "calendario", "o aprì calendario") into a concrete executable or
desktop file id that the `app_launcher` MCP tool can actually launch.
"""

from __future__ import annotations

import configparser
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger("VoiceAssistant.AppSlotMatcher")

DEFAULT_DESKTOP_DIRS = [
    "/usr/share/applications",
    os.path.expanduser("~/.local/share/applications"),
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
]


@dataclass
class AppEntry:
    desktop_id: str
    name: str
    exec_name: str


class AppSlotMatcher:
    """Indexes installed .desktop files and resolves free-text app names via fuzzy matching.

    Desktop files usually ship only an English `Name`; on an Italian assistant a raw
    fuzzy match against that alone misfires badly (e.g. "calcolatrice" would not be
    close to "Calculator" at all, or worse, coincidentally closer to an unrelated app).
    Localized `Name[<locale>]`/`GenericName[<locale>]` entries are indexed as extra
    candidates pointing back at the same app so the query can match in either language.
    """

    def __init__(self, desktop_dirs: Optional[List[str]] = None, locale: str = "it"):
        self._dirs = desktop_dirs or DEFAULT_DESKTOP_DIRS
        self._locale = locale
        self._entries: List[AppEntry] = []
        self._candidates: List[str] = []
        self._candidate_entry_idx: List[int] = []
        self._index()

    def _index(self) -> None:
        seen_ids = set()
        for dir_path in self._dirs:
            path = Path(dir_path)
            if not path.is_dir():
                continue
            try:
                desktop_files = sorted(path.glob("*.desktop"))
            except OSError:
                continue
            for desktop_file in desktop_files:
                if desktop_file.name in seen_ids:
                    continue
                parsed = self._parse_desktop_file(desktop_file, self._locale)
                if parsed is None:
                    continue
                entry, labels = parsed
                seen_ids.add(desktop_file.name)
                entry_idx = len(self._entries)
                self._entries.append(entry)
                for label in labels:
                    self._candidates.append(label)
                    self._candidate_entry_idx.append(entry_idx)

    @staticmethod
    def _parse_desktop_file(path: Path, locale: str) -> Optional[tuple]:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read(path, encoding="utf-8")
        except Exception as exc:
            logger.debug(f"Impossibile leggere {path}: {exc}")
            return None

        if "Desktop Entry" not in parser:
            return None

        section = parser["Desktop Entry"]
        if section.get("Type", "Application") != "Application":
            return None
        if section.get("NoDisplay", "false").strip().lower() == "true":
            return None
        if section.get("Hidden", "false").strip().lower() == "true":
            return None

        name = section.get("Name", "").strip()
        exec_line = section.get("Exec", "").strip()
        if not name or not exec_line:
            return None

        exec_name = exec_line.split()[0].split("/")[-1]
        localized_name = section.get(f"Name[{locale}]", "").strip()
        display_name = localized_name or name
        entry = AppEntry(desktop_id=path.name, name=display_name, exec_name=exec_name)

        labels = {name}
        for key in (f"Name[{locale}]", f"GenericName[{locale}]", "GenericName"):
            value = section.get(key, "").strip()
            if value:
                labels.add(value)
        labels.add(exec_name)
        mimetypes = section.get("MimeType", "").lower()
        if "x-scheme-handler/mailto" in mimetypes:
            labels.update(["mail", "email", "posta", "posta elettronica"])

        return entry, list(labels)

    def match(self, query: str, score_cutoff: float = 62.0) -> Optional[Dict[str, object]]:
        """Return the best fuzzy match for *query*, or None below score_cutoff.

        Uses plain Levenshtein ratio (not WRatio) on purpose: WRatio's partial-match
        boost makes short queries match unrelated long names whenever the query is a
        substring of them (e.g. "file" scored higher against "Color Profile Viewer"
        than against "File" itself), which is worse than failing to match at all.
        """
        query = (query or "").strip()
        if not query:
            return None

        # Risoluzione diretta per alias del client di posta elettronica
        mail_aliases = {"mail", "email", "e-mail", "posta", "posta elettronica"}
        if query.lower() in mail_aliases:
            try:
                import gi
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio
                app_info = Gio.AppInfo.get_default_for_type("x-scheme-handler/mailto", False)
                if app_info:
                    return {
                        "name": app_info.get_display_name() or app_info.get_name() or "Posta",
                        "exec": app_info.get_executable() or "evolution",
                        "desktop_id": app_info.get_id() or "org.gnome.Evolution.desktop",
                        "score": 100.0,
                    }
            except Exception as e:
                logger.debug(f"Default mail client lookup failed: {e}")

            for entry in self._entries:
                d_id = entry.desktop_id.lower()
                if any(m in d_id for m in ("evolution", "thunderbird", "geary", "kmail")):
                    return {
                        "name": entry.name,
                        "exec": entry.exec_name,
                        "desktop_id": entry.desktop_id,
                        "score": 100.0,
                    }

        if not self._candidates:
            return None

        result = process.extractOne(
            query,
            self._candidates,
            scorer=fuzz.ratio,
            processor=str.lower,
            score_cutoff=score_cutoff,
        )
        if not result:
            return None

        _, score, idx = result
        entry = self._entries[self._candidate_entry_idx[idx]]
        return {
            "name": entry.name,
            "exec": entry.exec_name,
            "desktop_id": entry.desktop_id,
            "score": float(score),
        }
