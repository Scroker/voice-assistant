"""Forbid raw GLib.idle_add / GLib.timeout_add outside the scheduling helpers."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
PATTERN = re.compile(r"GLib\.(idle_add|timeout_add|timeout_add_seconds)\(")
# Files allowed to call GLib directly (the helpers themselves).
ALLOWED = {"daemon/core/glib_scheduling.py"}
# Temporary list of legacy call sites, emptied in Phase 2 (§6.1).
LEGACY = {
    "daemon/core/assistant_runtime.py:560",
    "daemon/core/assistant_runtime.py:593",
    "daemon/core/assistant_runtime.py:595",
    "daemon/core/assistant_runtime.py:1318",
    "daemon/core/lifecycle.py:29",
    "daemon/core/lifecycle.py:94",
    "daemon/core/logger.py:583",
    "daemon/core/provider_manager.py:93",
    "daemon/core/provider_manager.py:163",
    "daemon/core/provider_manager.py:181",
    "daemon/core/provider_manager.py:202",
    "daemon/core/provider_manager.py:222",
    "daemon/core/provider_manager.py:226",
    "daemon/core/provider_manager.py:491",
    "daemon/core/provider_manager.py:530",
    "daemon/core/provider_manager.py:626",
    "daemon/core/provider_manager.py:643",
    "daemon/core/provider_manager.py:793",
    "daemon/core/runtime_manager.py:453",
    "daemon/core/runtime_manager.py:455",
    "daemon/core/runtime_manager.py:457",
    "daemon/core/runtime_manager.py:1272",
    "daemon/main.py:769",
    "daemon/main.py:784",
    "gui/assistant_window.py:412",
    "gui/assistant_window.py:413",
    "gui/assistant_window.py:449",
    "gui/assistant_window.py:461",
    "gui/assistant_window.py:469",
    "gui/assistant_window.py:477",
    "gui/assistant_window.py:481",
    "gui/assistant_window.py:486",
    "gui/assistant_window.py:513",
    "gui/assistant_window.py:649",
    "gui/components/chat/chat_view.py:40",
    "gui/components/daemon_client.py:111",
    "gui/components/daemon_client.py:140",
    "gui/components/daemon_client.py:144",
    "gui/components/settings/bugreport.py:63",
    "gui/components/settings/bugreport.py:65",
    "gui/components/settings/bugreport.py:67",
    "gui/components/settings/model_selector.py:132",
    "gui/components/settings/model_selector.py:854",
    "gui/components/settings/model_selector.py:940",
    "gui/components/settings/skills.py:222",
    "gui/components/settings/skills.py:555",
    "gui/components/settings/skills.py:587",
    "gui/components/settings/speaker_id.py:371",
    "gui/components/settings/speaker_id.py:388",
    "gui/dependency_installer.py:296",
    "gui/dependency_installer.py:299",
    "gui/dependency_installer.py:304",
    "gui/dependency_installer.py:307",
    "gui/dependency_installer.py:312",
    "gui/dependency_installer.py:315",
    "gui/dependency_installer.py:319",
    "gui/dependency_installer.py:325",
    "gui/settings_window.py:123",
}


def test_no_raw_glib_scheduling():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "venv" in path.parts or rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PATTERN.search(line) and f"{rel}:{lineno}" not in LEGACY:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "Use schedule_idle/schedule_periodic instead:\n" + "\n".join(offenders)
