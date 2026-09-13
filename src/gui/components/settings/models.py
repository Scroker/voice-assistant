"""
Componente per la gestione dello storage dei modelli (scansione, visualizzazione, pulizia).
"""

import os
import shutil
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Adw, Gio

try:
    from core.model_registry import get_model_registry
    from core.path_utils import get_models_dir
    _DEFAULT_MODELS_DIR = str(get_models_dir())
except ImportError:
    d = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "daemon"))
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        from core.model_registry import get_model_registry
        from core.path_utils import get_models_dir
        _DEFAULT_MODELS_DIR = str(get_models_dir())
    except Exception:
        from core.model_registry import get_model_registry
        _DEFAULT_MODELS_DIR = os.path.expanduser("~/.local/share/voice-assistant/models")


def fmt_size(n: int) -> str:
    """Formatta i byte in unità leggibili (B, KB, MB, GB, TB)."""
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} TB"


def entry_size(path: str) -> int:
    """Calcola la dimensione totale di un file o di una directory ricorsivamente."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    try:
        for e in os.scandir(path):
            total += entry_size(e.path)
    except OSError:
        pass
    return total


def active_match(name: str, active: str) -> bool:
    """Verifica se il nome del modello coincide con il modello attivo."""
    if not active:
        return False
    n, a = name.lower(), active.lower()
    return n == a or a in n or n in a


class ModelsStorageManager:
    """Gestisce la pagina Storage and Models: scansione disco, percorsi e pulizia modelli."""

    def __init__(self, builder: Gtk.Builder, settings: Gio.Settings | None, parent_window: Gtk.Window | None = None):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self._setup()

    def _base_dir(self) -> str:
        if self.settings:
            return self.settings.get_string("models-dir") or _DEFAULT_MODELS_DIR
        return _DEFAULT_MODELS_DIR

    def _full_path(self, subdir: str | None = None) -> str:
        base = os.path.expanduser(self._base_dir())
        return os.path.join(base, subdir) if subdir else base

    def _scan(self, subdir: str) -> list[tuple[str, str, int, str, str]]:
        reg = get_model_registry(models_dir=self._base_dir())
        if subdir in ("wakeword", "ww"):
            installed = []
            seen_keys = set()

            # 1. Modelli espliciti openwakeword e sherpa-onnx
            for prov in ("openwakeword", "sherpa-onnx"):
                for m in reg.get_installed_models(prov):
                    key = m.get("path") or m.get("id") or m.get("name")
                    if key and key not in seen_keys:
                        installed.append(m)
                        seen_keys.add(key)

            # 2. Eventuali altri modelli con service="wakeword"
            for m in reg.get_installed_models("wakeword"):
                key = m.get("path") or m.get("id") or m.get("name")
                if key and key not in seen_keys:
                    installed.append(m)
                    seen_keys.add(key)

            # 3. Modelli vosk usati o utilizzabili come wake word
            vosk_ww = (self.settings.get_string("vosk-ww-model") if self.settings else "") or "vosk-model-small-it-0.22"
            for m in reg.get_installed_models("vosk"):
                m_id = (m.get("id") or "").lower()
                if "small" in m_id or active_match(m_id, vosk_ww):
                    key = m.get("path") or m.get("id") or m.get("name")
                    if key and key not in seen_keys:
                        installed.append(m)
                        seen_keys.add(key)
        else:
            prov_map = {
                "stt": "stt",
                "llm": "gguf",
                "tts": "piper",
                "sherpa": "sherpa-onnx",
            }
            prov = prov_map.get(subdir, subdir)
            installed = reg.get_installed_models(prov)

        result = []
        for m in sorted(installed, key=lambda x: (x.get("name") or x["id"]).lower()):
            name = m.get("name") or m.get("filename") or m.get("id", "")
            path = m.get("path") or ""
            sz = m.get("size_bytes") or 0
            prov = m.get("provider") or ""
            mid = m.get("id") or ""
            result.append((name, path, sz, prov, mid))
        return result

    def _make_row(self, name: str, size: int, is_active: bool = False) -> Adw.ActionRow:
        row = Adw.ActionRow(title=name, subtitle=fmt_size(size))
        if is_active:
            row.add_suffix(Gtk.Image(icon_name="check-plain-symbolic", valign=Gtk.Align.CENTER))
        return row

    def _swap_rows(self, grp: Adw.PreferencesGroup | None, rows: list) -> None:
        if not grp:
            return
        for r in getattr(grp, "_current_rows", []):
            try:
                grp.remove(r)
            except Exception:
                pass
        for r in rows:
            grp.add(r)
        grp._current_rows = rows  # type: ignore[attr-defined]

    def _setup(self) -> None:
        if not self.settings:
            return

        base_row = self.builder.get_object("models_path_row")
        choose_btn = self.builder.get_object("choose_path_btn")
        reset_btn = self.builder.get_object("reset_path_btn")
        clean_btn = self.builder.get_object("clean_unused_btn")
        ww_open = self.builder.get_object("ww_open_btn")
        stt_open = self.builder.get_object("stt_open_btn")
        llm_open = self.builder.get_object("llm_open_btn")
        tts_open = self.builder.get_object("tts_open_btn")

        if base_row:
            base_row.set_subtitle(self._base_dir())
        if choose_btn:
            choose_btn.connect("clicked", self._on_choose_models_dir)
        if reset_btn:
            reset_btn.connect("clicked", lambda _: self.settings.reset("models-dir") if self.settings else None)

        for btn, subdir in ((ww_open, "wakeword"), (stt_open, "stt"), (llm_open, "llm"), (tts_open, "tts")):
            if btn:
                d = subdir
                btn.connect("clicked", lambda _b, sub=d: (
                    os.makedirs(self._full_path(sub), exist_ok=True),
                    Gio.AppInfo.launch_default_for_uri(f"file://{self._full_path(sub)}", None),
                ))

        if clean_btn:
            clean_btn.connect("activated", self._on_clean)

        self.refresh()

        for key in ("models-dir", "wakeword-engine", "oww-model", "sherpa-ww-model-dir",
                    "vosk-ww-model", "sherpa-model",
                    "stt-model", "llm-model", "tts-voice"):
            self.settings.connect(f"changed::{key}", lambda *_: self.refresh())

    def refresh(self) -> None:
        """Ricarica le informazioni dei modelli installati e aggiorna la UI."""
        base_row = self.builder.get_object("models_path_row")
        if base_row:
            base_row.set_subtitle(self._base_dir())

        total_size_row = self.builder.get_object("models_total_size_row")
        if total_size_row:
            try:
                base_path = self._full_path()
                total_bytes = entry_size(base_path)
                total_size_row.set_subtitle(fmt_size(total_bytes))
            except Exception:
                total_size_row.set_subtitle("0 B")

        active_stt = self.settings.get_string("stt-model") if self.settings else ""
        active_llm = self.settings.get_string("llm-model") if self.settings else ""
        active_tts = self.settings.get_string("tts-voice") if self.settings else ""
        engine = (self.settings.get_string("wakeword-engine") if self.settings else "") or "vosk"

        active_ww = ""
        active_ww_prov = ""
        if engine == "vosk":
            active_ww = (self.settings.get_string("vosk-ww-model") if self.settings else "") or "vosk-model-small-it-0.22"
            active_ww_prov = "vosk"
        elif engine == "sherpa-onnx":
            active_ww = (self.settings.get_string("sherpa-model") if self.settings else "") or ""
            active_ww_prov = "sherpa-onnx"
        elif engine == "openwakeword":
            active_ww = (self.settings.get_string("oww-model") if self.settings else "") or ""
            active_ww_prov = "openwakeword"

        def _dir_rows(subdir: str, active: str, active_prov: str | None = None) -> list:
            items = self._scan(subdir)
            if items:
                rows = []
                for n, path, sz, prov, mid in items:
                    if active_prov is not None:
                        is_active = (prov == active_prov) and (
                            active_match(n, active) or active_match(mid, active) or (path and active_match(path, active))
                        )
                    else:
                        is_active = active_match(n, active) or active_match(mid, active)
                    rows.append(self._make_row(n, sz, is_active))
                return rows
            return [Adw.ActionRow(title="Nessun modello installato", subtitle=self._full_path(subdir))]

        ww_rows = _dir_rows("wakeword", active_ww, active_ww_prov)

        grp_ww = self.builder.get_object("ww_models_group")
        grp_stt = self.builder.get_object("stt_models_group")
        grp_llm = self.builder.get_object("llm_models_group")
        grp_tts = self.builder.get_object("tts_models_group")

        self._swap_rows(grp_ww, ww_rows)
        self._swap_rows(grp_stt, _dir_rows("stt", active_stt))
        self._swap_rows(grp_llm, _dir_rows("llm", active_llm))
        self._swap_rows(grp_tts, _dir_rows("tts", active_tts))

    def _on_choose_models_dir(self, _btn) -> None:
        chooser = Gtk.FileChooserNative(
            title="Seleziona directory modelli",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            transient_for=self.parent_window,
            modal=True,
        )

        def on_response(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                folder = dialog.get_file()
                if folder and self.settings:
                    self.settings.set_string("models-dir", folder.get_path())
            dialog.destroy()

        chooser.connect("response", on_response)
        chooser.show()

    def _on_clean(self, *_args) -> None:
        if not self.settings:
            return

        engine = (self.settings.get_string("wakeword-engine") if self.settings else "") or "vosk"
        if engine == "openwakeword":
            ww_active = self.settings.get_string("oww-model") or "alexa"
            ww_prov = "openwakeword"
        elif engine == "sherpa-onnx":
            ww_active = self.settings.get_string("sherpa-model") or ""
            ww_prov = "sherpa-onnx"
        else:
            ww_active = self.settings.get_string("vosk-ww-model") or ""
            ww_prov = "vosk"

        stt_active = self.settings.get_string("stt-model") or ""
        llm_active = self.settings.get_string("llm-model") or ""
        tts_active = self.settings.get_string("tts-voice") or ""

        reg = get_model_registry(models_dir=self._base_dir())
        unused: list[dict] = []

        all_installed = reg.get_installed_models()
        for m in all_installed:
            prov = m.get("provider", "").lower()
            serv = m.get("service", "").lower()
            name = m.get("name") or m.get("filename") or m.get("id", "")
            mid = m.get("id", "")
            fname = m.get("filename") or ""

            is_used = False
            if serv == "stt" or prov in ("stt", "vosk", "whisper"):
                if active_match(name, stt_active) or active_match(mid, stt_active) or (fname and active_match(fname, stt_active)):
                    is_used = True
                if engine == "vosk" and (active_match(name, ww_active) or active_match(mid, ww_active)):
                    is_used = True
            elif serv == "llm" or prov in ("gguf", "llm", "ollama"):
                if active_match(name, llm_active) or active_match(mid, llm_active) or (fname and active_match(fname, llm_active)):
                    is_used = True
            elif serv == "tts" or prov in ("piper", "tts"):
                if active_match(name, tts_active) or active_match(mid, tts_active) or (fname and active_match(fname, tts_active)):
                    is_used = True
            elif serv == "wakeword" or prov in ("openwakeword", "sherpa-onnx"):
                if engine == prov and (active_match(name, ww_active) or active_match(mid, ww_active) or (fname and active_match(fname, ww_active))):
                    is_used = True

            if not is_used:
                unused.append(m)

        if not unused:
            dlg = Adw.AlertDialog(
                heading="Nessun modello da rimuovere",
                body="Tutti i modelli installati sono attualmente in uso.",
            )
            dlg.add_response("ok", "OK")
            dlg.present(self.parent_window)
            return

        names = "\n".join(f"  • {m.get('name', m['id'])} ({m.get('size_text', '')})" for m in unused)
        dlg = Adw.AlertDialog(
            heading=f"Rimuovere {len(unused)} modelli non utilizzati?",
            body=f"I seguenti modelli verranno eliminati permanentemente dal disco:\n{names}",
        )
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("delete", "Elimina")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _do_delete(_, response: str) -> None:
            if response != "delete":
                return
            for m in unused:
                prov = m.get("provider", "")
                mid = m["id"]
                reg.delete_model(prov, mid)
            self.refresh()

        dlg.connect("response", _do_delete)
        dlg.present(self.parent_window)
