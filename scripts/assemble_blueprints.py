#!/usr/bin/env python3
"""
assemble_blueprints.py — Assemblatore di moduli Blueprint per le preferenze dell'Assistente Vocale.

Legge i moduli componenti situati in `data/ui/prefs/` e li unisce nel file consolidato `data/ui/prefs.blp`.
"""

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
_PREFS_DIR = os.path.join(_PROJECT_ROOT, "data", "ui", "prefs")
_OUTPUT_BLP = os.path.join(_PROJECT_ROOT, "data", "ui", "prefs.blp")

_PAGE_ORDER = [
    "page_general.blp",
    "page_wakeword.blp",
    "page_stt.blp",
    "page_llm.blp",
    "page_tts.blp",
]

_SUBPAGE_ORDER = [
    "subpages.blp",
    "subpage_models.blp",
    "subpage_audio.blp",
    "subpage_speaker_enrollment.blp",
    "subpage_bugreport.blp",
]


def indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(pad + line)
        else:
            lines.append("")
    return "\n".join(lines)


def assemble() -> str:
    window_path = os.path.join(_PREFS_DIR, "window.blp")
    if not os.path.exists(window_path):
        window_path = os.path.join(_PREFS_DIR, "sidebar.blp")

    if not os.path.exists(window_path):
        raise FileNotFoundError(f"File non trovato: {window_path}")

    with open(window_path, "r", encoding="utf-8") as f:
        window_content = f.read()

    pages_assembled = []
    for page_name in _PAGE_ORDER:
        page_path = os.path.join(_PREFS_DIR, page_name)
        if not os.path.exists(page_path):
            raise FileNotFoundError(f"Modulo pagina non trovato: {page_path}")
        with open(page_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        pages_assembled.append(indent(content, 2))

    all_pages = "\n\n".join(pages_assembled)

    subpages_assembled = []
    for subpage_name in _SUBPAGE_ORDER:
        subpage_path = os.path.join(_PREFS_DIR, subpage_name)
        if not os.path.exists(subpage_path):
            raise FileNotFoundError(f"Modulo sottopagina non trovato: {subpage_path}")
        with open(subpage_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        subpages_assembled.append(content)

    all_subpages = "\n\n".join(subpages_assembled)

    assembled = window_content.replace("// PAGES_PLACEHOLDER", all_pages)
    assembled = assembled.replace("// SUBPAGES_PLACEHOLDER", all_subpages)

    with open(_OUTPUT_BLP, "w", encoding="utf-8") as f:
        f.write(assembled)

    print(f"[assemble_blueprints] Assemblati con successo {len(_PAGE_ORDER)} schede e {len(_SUBPAGE_ORDER)} sottopagine in: {_OUTPUT_BLP}")
    return assembled


if __name__ == "__main__":
    assemble()
