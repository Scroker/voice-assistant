"""
Componente per la configurazione del riconoscimento del parlante (Speaker ID)
e la sottopagina di registrazione dell'impronta vocale (Voice Enrollment).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gtk, Adw, Gio, GLib

from .base import bind_setting, has_setting_key

_log = logging.getLogger("VoiceAssistant.GUI.Settings.SpeakerID")

_MODES = ["disabled", "informative", "gate"]


def _get_mode_labels() -> list[str]:
    try:
        from core.locale_utils import get_system_language
        lang = get_system_language(default="it")
    except Exception:
        lang = "it"

    if lang and lang.startswith("en"):
        return [
            "Disabled",
            "Informative only — personalizes responses, does not block",
            "Gate — executes only voice requests from your voice",
        ]
    return [
        "Disabilitato",
        "Solo informativo — personalizza le risposte, non blocca nulla",
        "Barriera — esegue solo le richieste vocali della tua voce",
    ]


def _load_enrollment_locale() -> dict[str, Any]:
    """Carica le stringhe di registrazione da speaker_enrollment.json."""
    try:
        from core.data_loader import load_json_data
        data = load_json_data("locales/speaker_enrollment.json", fallback_default={})
        if data:
            return data
    except Exception:
        pass

    candidates = [
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "locales", "speaker_enrollment.json")),
        os.path.expanduser("~/.local/share/gnome-shell/extensions/voice-assistant@scroker.github.io/locales/speaker_enrollment.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


class SpeakerIdSettings:
    """Gestisce la configurazione del riconoscimento del parlante e la registrazione vocale."""

    def __init__(
        self,
        builder: Gtk.Builder,
        settings: Gio.Settings | None,
        parent_window: Gtk.Window | None = None,
        daemon_client: Any | None = None,
    ):
        self.builder = builder
        self.settings = settings
        self.parent_window = parent_window
        self.daemon_client = daemon_client

        self._profiles: list[dict[str, Any]] = []
        self._is_recording = False
        self._suppress_mode_notify = False
        self._speaker_status: dict[str, Any] = {
            "available": True,
            "downloading": False,
            "download_percent": 0,
            "status": "unknown",
        }
        self._sig_progress_id: int | None = None
        self._sig_finished_id: int | None = None
        self._sig_dl_id: int | None = None

        self._mode_radios: dict[str, Any] = {}
        self._setup_mode_and_threshold()
        self._setup_profile_row()
        self._setup_enrollment_subpage()
        self._subscribe_daemon_signals()
        self.refresh_profiles()

    def _setup_mode_and_threshold(self) -> None:
        threshold_row = self.builder.get_object("speaker_id_threshold_row")

        # Threshold spin binding
        bind_setting(self.settings, "speaker-id-threshold", self.builder, "speaker_id_threshold_row", "value")

        if not self.settings or not has_setting_key(self.settings, "speaker-id-mode"):
            return

        # Localize ActionRow titles and subtitles
        try:
            from core.locale_utils import get_system_language
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"

        is_it = not (lang and lang.startswith("en"))
        if is_it:
            labels = {
                "disabled": ("Disabilitato", "Nessuna verifica; tutte le richieste vocali vengono elaborate"),
                "informative": ("Solo informativo", "Personalizza le risposte con l'identità del parlante senza bloccare"),
                "gate": ("Barriera", "Esegue solo le richieste vocali corrispondenti al tuo profilo vocale"),
            }
            for m, (t, s) in labels.items():
                row = self.builder.get_object(f"speaker_id_mode_{m}_row")
                if row:
                    row.set_title(t)
                    row.set_subtitle(s)

        self._mode_radios = {
            "disabled": self.builder.get_object("speaker_id_mode_disabled_radio"),
            "informative": self.builder.get_object("speaker_id_mode_informative_radio"),
            "gate": self.builder.get_object("speaker_id_mode_gate_radio"),
        }

        curr_mode = (self.settings.get_string("speaker-id-mode") or "disabled").strip().lower()
        if curr_mode not in _MODES:
            curr_mode = "disabled"

        self._sync_radio_from_mode(curr_mode)
        self._sync_sensitivity(curr_mode)

        for mode, radio in self._mode_radios.items():
            if radio:
                radio.connect("notify::active", self._on_radio_active, mode)

        self.settings.connect("changed::speaker-id-mode", self._on_setting_mode_changed)

    def _sync_sensitivity(self, mode: str) -> None:
        threshold_row = self.builder.get_object("speaker_id_threshold_row")
        profile_row = self.builder.get_object("speaker_id_profile_row")
        is_enabled = (mode != "disabled")

        if threshold_row:
            threshold_row.set_sensitive(is_enabled)
        if profile_row:
            profile_row.set_sensitive(True)

    def _sync_radio_from_mode(self, mode: str) -> None:
        self._suppress_mode_notify = True
        try:
            for m, radio in self._mode_radios.items():
                if radio:
                    radio.set_active(m == mode)
        finally:
            self._suppress_mode_notify = False

    def _on_radio_active(self, radio: Gtk.CheckButton, _pspec: Any, mode: str) -> None:
        if self._suppress_mode_notify or not self.settings:
            return

        if not radio.get_active():
            return

        old_mode = (self.settings.get_string("speaker-id-mode") or "disabled").strip().lower()
        if mode == old_mode:
            return

        # Missing profile gate check: must have at least one valid profile
        if mode == "gate" and not self._has_valid_profile():
            self._sync_radio_from_mode(old_mode)
            self._show_no_profile_alert()
            return

        self.settings.set_string("speaker-id-mode", mode)
        self._sync_sensitivity(mode)

    def _on_setting_mode_changed(self, _settings: Gio.Settings, _key: str) -> None:
        if not self.settings:
            return
        curr_mode = (self.settings.get_string("speaker-id-mode") or "disabled").strip().lower()
        if curr_mode not in _MODES:
            curr_mode = "disabled"
        self._sync_radio_from_mode(curr_mode)
        self._sync_sensitivity(curr_mode)

    def _has_valid_profile(self) -> bool:
        if not self._profiles:
            return False
        curr_user_profs = [p for p in self._profiles if p.get("is_current_user")]
        if curr_user_profs:
            return any(not p.get("needs_reenroll", False) for p in curr_user_profs)
        return any(not p.get("needs_reenroll", False) for p in self._profiles)

    def _show_no_profile_alert(self) -> None:
        dlg = Adw.AlertDialog(
            heading="Registra prima la tua voce",
            body="La modalità Barriera richiede un profilo vocale registrato per verificare le richieste vocali.",
        )
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("enroll", "Registra ora")
        dlg.set_response_appearance("enroll", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("enroll")
        dlg.set_close_response("cancel")

        def _on_resp(_d: Any, response: str) -> None:
            if response == "enroll":
                self._open_enrollment_subpage()

        dlg.connect("response", _on_resp)
        if self.parent_window:
            dlg.present(self.parent_window)

    def _setup_profile_row(self) -> None:
        profile_row = self.builder.get_object("speaker_id_profile_row")
        if not profile_row:
            return

        profile_row.connect("activated", lambda _r: self._open_enrollment_subpage())

    def _show_toast(self, text: str) -> None:
        """Mostra una notifica toast nella finestra delle preferenze."""
        try:
            toast = Adw.Toast.new(text)
            toast.set_timeout(4)
            if self.parent_window and hasattr(self.parent_window, "add_toast"):
                self.parent_window.add_toast(toast)
        except Exception as e:
            _log.debug("Impossibile mostrare toast: %s", e)

    def _open_enrollment_subpage(self) -> None:
        subpage = self.builder.get_object("speaker_enrollment_subpage")
        if not subpage or not self.parent_window:
            return

        if self._speaker_status.get("downloading", False):
            self._show_toast("Download del modello vocale in corso. La registrazione è temporaneamente disabilitata.")
        elif not self._speaker_status.get("available", True):
            self._show_toast("Componenti vocali non disponibili. Verifica le dipendenze.")

        if hasattr(self.parent_window, "push_subpage"):
            self.parent_window.push_subpage(subpage)
        else:
            nav = self.builder.get_object("content_navigation_view")
            if nav and hasattr(nav, "push"):
                nav.push(subpage)

    def _setup_enrollment_subpage(self) -> None:
        banner = self.builder.get_object("enrollment_download_banner")
        intro_label = self.builder.get_object("enrollment_intro_label")
        name_entry = self.builder.get_object("enrollment_name_entry")
        text_label = self.builder.get_object("enrollment_text_label")
        record_btn = self.builder.get_object("enrollment_record_btn")
        cancel_btn = self.builder.get_object("enrollment_cancel_btn")
        delete_btn = self.builder.get_object("enrollment_delete_btn")
        level_bar = self.builder.get_object("enrollment_level_bar")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        status_label = self.builder.get_object("enrollment_status_label")
        subpage = self.builder.get_object("speaker_enrollment_subpage")

        if banner:
            banner.set_revealed(False)

        # Load localized text
        loc_all = _load_enrollment_locale()
        try:
            from core.locale_utils import get_system_language
            lang = get_system_language(default="it")
        except Exception:
            lang = "it"

        loc = loc_all.get(lang) or loc_all.get("it", {})
        if intro_label and loc.get("instructions"):
            intro_label.set_label(loc["instructions"])
        if text_label and loc.get("text"):
            text_label.set_label(loc["text"])

        # Display GNOME session user identity
        user_row = self.builder.get_object("enrollment_user_row")
        username = GLib.get_user_name() or "user"
        real_name = GLib.get_real_name()
        if real_name and real_name.strip() and real_name.strip() != "Unknown":
            user_label = f"{real_name} ({username})"
        else:
            user_label = username

        if user_row:
            user_row.set_subtitle(user_label)

        # Default speaker name
        default_name = real_name if (real_name and real_name.strip() not in ("", "Unknown")) else username
        if name_entry:
            name_entry.set_text(default_name)

        if level_bar:
            level_bar.set_value(0.0)
        if progress_bar:
            progress_bar.set_fraction(0.0)
        if status_label:
            status_label.set_label("Pronto per la registrazione")

        if record_btn:
            record_btn.connect("clicked", self._on_record_clicked)
        if cancel_btn:
            cancel_btn.connect("clicked", self._on_cancel_clicked)
        if delete_btn:
            sig = "activated" if isinstance(delete_btn, Adw.ButtonRow) else "clicked"
            delete_btn.connect(sig, self._on_delete_clicked)

        # Cancel enrollment if user leaves subpage
        if subpage:
            if hasattr(subpage, "connect"):
                try:
                    subpage.connect("hidden", self._on_subpage_hidden)
                except Exception:
                    pass

    def _on_subpage_hidden(self, _page: Any) -> None:
        if self._is_recording and self.daemon_client:
            self._is_recording = False
            self.daemon_client.cancel_speaker_enrollment()
            self._reset_subpage_ui("Registrazione interrotta.")

    def _subscribe_daemon_signals(self) -> None:
        if not self.daemon_client or not hasattr(self.daemon_client, "subscribe"):
            return

        self._sig_progress_id = self.daemon_client.subscribe(
            "SpeakerEnrollmentProgress",
            self._on_enrollment_progress,
        )
        self._sig_finished_id = self.daemon_client.subscribe(
            "SpeakerEnrollmentFinished",
            self._on_enrollment_finished,
        )
        self._sig_dl_id = self.daemon_client.subscribe(
            "DownloadProgress",
            self._on_download_progress,
        )

    def refresh_profiles(self) -> None:
        if not self.daemon_client or not hasattr(self.daemon_client, "get_speaker_profiles"):
            self._update_profile_display([])
            return

        self.daemon_client.get_speaker_profiles(self._on_profiles_received)
        if hasattr(self.daemon_client, "get_speaker_status"):
            self.daemon_client.get_speaker_status(self._on_status_received)

    def _on_status_received(self, status: dict[str, Any], exc: Exception | None) -> None:
        if exc or not isinstance(status, dict):
            _log.debug("Errore o risposta vuota per GetSpeakerStatus: %s", exc)
            return
        self._speaker_status = status
        GLib.idle_add(self._update_status_ui)

    def _on_download_progress(self, provider: str, model: str, percent: int) -> None:
        p = str(provider).lower()
        m = str(model).lower()
        if "speaker" in p or "resemblyzer" in p or "speaker" in m or "resemblyzer" in m:
            is_dl = (0 <= percent < 100)
            was_dl = self._speaker_status.get("downloading", False)
            self._speaker_status["downloading"] = is_dl
            self._speaker_status["download_percent"] = max(0, percent)
            if is_dl:
                self._speaker_status["status"] = "downloading"
            elif percent >= 100:
                self._speaker_status["status"] = "ready"
                self._speaker_status["available"] = True
                if was_dl:
                    self._notify_user_download_complete()
            GLib.idle_add(self._update_status_ui)

    def _notify_user_download_complete(self) -> None:
        try:
            import notify2
            notif = notify2.Notification(
                "Assistente Vocale",
                "Download del modello vocale completato. Ora puoi registrare il tuo profilo vocale.",
                "vocal-assistant-icon",
            )
            notif.show()
        except Exception:
            pass
        self._show_toast("Download del modello completato! Ora puoi registrare la tua voce.")

    def _update_status_ui(self) -> None:
        is_dl = self._speaker_status.get("downloading", False)
        is_avail = self._speaker_status.get("available", True)
        dl_percent = self._speaker_status.get("download_percent", 0)

        record_btn = self.builder.get_object("enrollment_record_btn")
        cancel_btn = self.builder.get_object("enrollment_cancel_btn")
        status_label = self.builder.get_object("enrollment_status_label")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        banner = self.builder.get_object("enrollment_download_banner")
        profile_row = self.builder.get_object("speaker_id_profile_row")

        if is_dl:
            if banner:
                banner.set_title(f"Download del modello vocale in corso ({dl_percent}%)… La registrazione è inibita.")
                banner.set_revealed(True)
            if record_btn:
                record_btn.set_sensitive(False)
                record_btn.set_label("Download in corso…")
            if cancel_btn:
                cancel_btn.set_visible(False)
            if status_label and not self._is_recording:
                status_label.set_label(f"Download del modello in corso ({dl_percent}%)… Attendi il completamento per registrare.")
            if progress_bar and not self._is_recording:
                progress_bar.set_fraction(dl_percent / 100.0)
            if profile_row:
                profile_row.set_subtitle(f"Download modello in corso ({dl_percent}%)…")
        elif not is_avail:
            if banner:
                banner.set_title("Componenti per il riconoscimento vocale non disponibili.")
                banner.set_revealed(True)
            if record_btn:
                record_btn.set_sensitive(False)
                record_btn.set_label("Servizio non disponibile")
            if status_label and not self._is_recording:
                status_label.set_label("Servizio di elaborazione vocale non disponibile: componenti mancanti.")
            if profile_row and not self._profiles:
                profile_row.set_subtitle("Servizio non disponibile")
        else:
            if banner:
                banner.set_revealed(False)
            if record_btn and not self._is_recording:
                record_btn.set_sensitive(True)
                has_profiles = bool(self._profiles)
                record_btn.set_label("Registra di nuovo" if has_profiles else "Avvia registrazione")
            if status_label and not self._is_recording:
                txt = status_label.get_label() or ""
                if "Download" in txt or "non disponibile" in txt:
                    status_label.set_label("Pronto per la registrazione")
            self._update_profile_display(self._profiles)

    def _on_profiles_received(self, profiles: list[dict[str, Any]], exc: Exception | None) -> None:
        if exc:
            _log.warning("Errore recupero profili speaker: %s", exc)
            self._update_profile_display([])
            return
        self._profiles = profiles or []
        self._update_profile_display(self._profiles)

    def _update_profile_display(self, profiles: list[dict[str, Any]]) -> None:
        if self._speaker_status.get("downloading", False):
            return

        profile_row = self.builder.get_object("speaker_id_profile_row")
        delete_btn = self.builder.get_object("enrollment_delete_btn")
        name_entry = self.builder.get_object("enrollment_name_entry")

        if not profiles:
            if profile_row:
                profile_row.set_subtitle("Non registrato")
            if delete_btn:
                delete_btn.set_sensitive(False)
            return

        current_user_profs = [p for p in profiles if p.get("is_current_user")]
        prof = current_user_profs[0] if current_user_profs else profiles[0]
        name = prof.get("display_name") or "Utente"
        username = prof.get("username")
        needs_reenroll = prof.get("needs_reenroll", False)

        user_suffix = f" ({username})" if username else ""
        if profile_row:
            if needs_reenroll:
                profile_row.set_subtitle(f"Da registrare di nuovo: {name}{user_suffix}")
            else:
                profile_row.set_subtitle(f"Registrato: {name}{user_suffix}")

        if delete_btn:
            delete_btn.set_sensitive(True)
        if name_entry and not self._is_recording:
            name_entry.set_text(name)

    def _on_record_clicked(self, btn: Gtk.Button) -> None:
        if self._is_recording:
            return

        if self._speaker_status.get("downloading", False):
            self._show_toast("Download del modello vocale in corso. Attendi il termine dello scaricamento.")
            return

        if not self._speaker_status.get("available", True):
            self._show_toast("Servizio di elaborazione vocale non disponibile: componenti mancanti.")
            return

        if not self.daemon_client or not self.daemon_client.is_ready:
            status_label = self.builder.get_object("enrollment_status_label")
            if status_label:
                status_label.set_label("Il servizio dell'assistente non è in esecuzione.")
            return

        name_entry = self.builder.get_object("enrollment_name_entry")
        name = (name_entry.get_text() if name_entry else "").strip()
        if not name:
            name = GLib.get_real_name() or "Utente"

        self._is_recording = True
        btn.set_sensitive(False)

        cancel_btn = self.builder.get_object("enrollment_cancel_btn")
        if cancel_btn:
            cancel_btn.set_visible(True)

        level_bar = self.builder.get_object("enrollment_level_bar")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        status_label = self.builder.get_object("enrollment_status_label")

        if level_bar:
            level_bar.set_value(0.0)
        if progress_bar:
            progress_bar.set_fraction(0.0)
        if status_label:
            status_label.set_label("Registrazione in corso… Parla con tono naturale.")

        self.daemon_client.start_speaker_enrollment(name, 8.0, self._on_start_enrollment_result)

    def _on_start_enrollment_result(self, started: bool, exc: Exception | None) -> None:
        if exc or not started:
            self._is_recording = False
            err = f"Impossibile avviare registrazione: {exc}" if exc else "Registrazione non avviata."
            self._reset_subpage_ui(err)

    def _on_cancel_clicked(self, _btn: Gtk.Button) -> None:
        if not self._is_recording:
            return
        self._is_recording = False
        if self.daemon_client:
            self.daemon_client.cancel_speaker_enrollment()
        self._reset_subpage_ui("Registrazione annullata.")

    def _on_enrollment_progress(self, progress: float, level: float) -> None:
        if not self._is_recording:
            return

        level_bar = self.builder.get_object("enrollment_level_bar")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        status_label = self.builder.get_object("enrollment_status_label")

        clamped_level = max(0.0, min(1.0, float(level)))
        clamped_prog = max(0.0, min(1.0, float(progress)))

        if level_bar:
            level_bar.set_value(clamped_level)
        if progress_bar:
            progress_bar.set_fraction(clamped_prog)

        if clamped_prog >= 0.99 and status_label:
            status_label.set_label("Elaborazione del profilo vocale…")

    def _on_enrollment_finished(self, success: bool, profile_id: str, message: str) -> None:
        self._is_recording = False
        record_btn = self.builder.get_object("enrollment_record_btn")
        cancel_btn = self.builder.get_object("enrollment_cancel_btn")
        level_bar = self.builder.get_object("enrollment_level_bar")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        status_label = self.builder.get_object("enrollment_status_label")

        if cancel_btn:
            cancel_btn.set_visible(False)
        if record_btn:
            record_btn.set_sensitive(True)
        if level_bar:
            level_bar.set_value(0.0)

        reasons_map = {
            "downloading": "Download del modello vocale in corso. La registrazione sarà disponibile al termine.",
            "cancelled": "Registrazione annullata.",
            "insufficient_speech": "Audio vocale insufficiente (almeno 4 secondi di parlato richiesti). Riprova.",
            "unavailable": "Servizio di elaborazione vocale non disponibile.",
            "no_audio": "Nessun segnale audio utile rilevato dal microfono.",
            "timeout": "Tempo scaduto senza audio sufficiente.",
        }

        if success:
            if progress_bar:
                progress_bar.set_fraction(1.0)
            if status_label:
                status_label.set_label("Profilo vocale registrato con successo!")
            if record_btn:
                record_btn.set_label("Registra di nuovo")
        else:
            if progress_bar:
                progress_bar.set_fraction(0.0)
            err_text = reasons_map.get(str(message), f"Errore: {message}")
            if status_label:
                status_label.set_label(err_text)
            if record_btn:
                record_btn.set_label("Riprova")

        self.refresh_profiles()

    def _reset_subpage_ui(self, status_message: str) -> None:
        record_btn = self.builder.get_object("enrollment_record_btn")
        cancel_btn = self.builder.get_object("enrollment_cancel_btn")
        level_bar = self.builder.get_object("enrollment_level_bar")
        progress_bar = self.builder.get_object("enrollment_progress_bar")
        status_label = self.builder.get_object("enrollment_status_label")

        if cancel_btn:
            cancel_btn.set_visible(False)
        if record_btn:
            record_btn.set_sensitive(True)
            record_btn.set_label("Avvia registrazione")
        if level_bar:
            level_bar.set_value(0.0)
        if progress_bar:
            progress_bar.set_fraction(0.0)
        if status_label:
            status_label.set_label(status_message)

    def _on_delete_clicked(self, _btn: Any) -> None:
        if not self._profiles:
            return

        dlg = Adw.AlertDialog(
            heading="Eliminare il profilo vocale?",
            body="Non potrai più usare la barriera vocale finché non registri nuovamente la tua voce.",
        )
        dlg.add_response("cancel", "Annulla")
        dlg.add_response("delete", "Elimina")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def _do_delete(_d: Any, response: str) -> None:
            if response != "delete" or not self._profiles:
                return
            curr_user_profs = [p for p in self._profiles if p.get("is_current_user")]
            target = curr_user_profs[0] if curr_user_profs else self._profiles[0]
            pid = target.get("id", "")
            if not pid:
                return
            if self.daemon_client:
                self.daemon_client.delete_speaker_profile(pid, self._on_delete_completed)

        dlg.connect("response", _do_delete)
        dlg.present(self.parent_window)

    def _on_delete_completed(self, success: bool, exc: Exception | None) -> None:
        if not success or exc:
            _log.warning("Errore eliminazione profilo: %s", exc)
            return

        # If current mode is gate, downgrade to informative
        if self.settings and has_setting_key(self.settings, "speaker-id-mode"):
            curr_mode = (self.settings.get_string("speaker-id-mode") or "").strip().lower()
            if curr_mode == "gate":
                self.settings.set_string("speaker-id-mode", "informative")

        self.refresh_profiles()
        self._reset_subpage_ui("Profilo vocale eliminato.")

    def destroy(self) -> None:
        """Pulisce le sottoscrizioni D-Bus e annulla registrazioni in corso."""
        if self._is_recording and self.daemon_client:
            self._is_recording = False
            try:
                self.daemon_client.cancel_speaker_enrollment()
            except Exception:
                pass

        if self.daemon_client and hasattr(self.daemon_client, "unsubscribe"):
            if self._sig_progress_id is not None:
                self.daemon_client.unsubscribe("SpeakerEnrollmentProgress", self._sig_progress_id)
                self._sig_progress_id = None
            if self._sig_finished_id is not None:
                self.daemon_client.unsubscribe("SpeakerEnrollmentFinished", self._sig_finished_id)
                self._sig_finished_id = None
            if self._sig_dl_id is not None:
                self.daemon_client.unsubscribe("DownloadProgress", self._sig_dl_id)
                self._sig_dl_id = None
