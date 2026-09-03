"""
Interfaccia tipizzata dell'oggetto VoiceAssistant esposta ai controller del demone.

I controller (AssistantRuntimeController, ProviderManager, DaemonLifecycle,
DaemonRuntimeManager, AudioRuntimeController) accedono all'istanza principale
attraverso `self.owner`. Dichiarare `owner: DaemonOwner` nei loro costruttori
permette al type-checker (mypy/pyright) di rilevare attributi mancanti o
firme incompatibili a tempo di sviluppo invece che a runtime.

Regola: ogni attributo/metodo aggiunto a VoiceAssistant e acceduto da un
controller deve essere dichiarato qui.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Dict, Optional, Set, Tuple

try:
    from typing import Protocol
except ImportError:  # Python < 3.8
    from typing_extensions import Protocol  # type: ignore[assignment]


class DaemonOwner(Protocol):
    """Interfaccia che VoiceAssistant espone ai suoi controller interni."""

    # ------------------------------------------------------------------
    # Stato macchina e audio
    # ------------------------------------------------------------------
    _state: str
    q: queue.Queue
    _stream: Any                          # sounddevice.RawInputStream | None
    _ignore_audio_until: float
    _listening_start_time: Optional[float]
    _last_speech_time: Optional[float]
    _last_partial_text: str
    _last_partial_change_time: Optional[float]
    audio_filter: Any                     # audio.filter.AudioFilter
    audio_player: Any                     # audio.player.AudioPlayer
    _audio_thread: Optional[threading.Thread]

    # ------------------------------------------------------------------
    # Wake Word
    # ------------------------------------------------------------------
    wakeword: str
    wakeword_engine: str                  # "vosk" | "openwakeword"
    oww_model_name: str                   # e.g. "alexa", "hey_jarvis"
    oww_model_instance: Any              # openwakeword.model.Model | None
    _oww_buffer: list                    # accumulation buffer for OWW frames

    # Sherpa-ONNX keyword spotter
    sherpa_ww_model_dir: str
    sherpa_spotter: Any                  # sherpa_onnx.KeywordSpotter | None
    sherpa_stream: Any                   # sherpa_onnx stream | None
    ww_recognizer: Any                    # vosk.KaldiRecognizer | None
    ww_model: Any                         # vosk.Model | None
    ww_provider: Any                      # providers.vosk_provider.VoskProvider
    vosk_ww_model: str

    # ------------------------------------------------------------------
    # Provider STT e configurazione
    # ------------------------------------------------------------------
    provider: Any                         # providers.base.STTProvider | None
    provider_name: str
    model_name: str
    hardware: str
    models_dir: str
    extra_config: Dict[str, Any]
    language: str

    # ------------------------------------------------------------------
    # Servizi
    # ------------------------------------------------------------------
    mcp_manager: Any                      # mcp.manager.MCPManager | None
    llm_service: Any                      # services.llm_service.LLMServiceManager
    tts_manager: Any                      # services.tts_service.TTSServiceManager
    model_manager: Any                    # core.model_manager.ModelManager
    state_machine: Any                    # core.state.StateMachine
    pipeline_controller: Any             # core.pipeline.PipelineController
    fast_path: Any                        # core.pipeline.FastPathDispatcher

    # ------------------------------------------------------------------
    # Stato download e provider load
    # ------------------------------------------------------------------
    _downloading_models: Dict[str, int]
    _cancel_requests: Set[str]
    _active_notifs: Dict[Any, Any]
    _stt_load_pending: bool
    _pending_state_after_provider_load: Optional[str]
    _load_id: int
    _reload_timer: Optional[threading.Timer]

    # ------------------------------------------------------------------
    # Sistema
    # ------------------------------------------------------------------
    settings: Any                         # Gio.Settings
    _inhibitor: Any                       # core.power.PowerInhibitor
    _model_idle_watch_id: Any            # GLib source ID (int)

    # ------------------------------------------------------------------
    # Metodi pubblici esposti ai controller
    # ------------------------------------------------------------------
    def set_state(self, state: str) -> None: ...
    def load_provider(self, load_id: int) -> Any: ...
    def emit_download_progress(self, provider: str, model_name: str, percent: int) -> None: ...
    def reset_wakeword_recognizer(self) -> None: ...
    def on_settings_changed(self, settings: Any, key: str) -> None: ...
    def _handle_fast_path_intent(
        self, intent_name: str, params: Any, text: str
    ) -> Tuple[bool, str]: ...
    def _close_stream(self) -> None: ...
    def _create_stream(self) -> None: ...
    def _start_speaking_watchdog(self) -> None: ...
    def _on_playback_finished(self) -> None: ...
    def _on_llm_token(self, token: str) -> None: ...
    def _report_error(self, exc: Exception) -> None: ...

    # ------------------------------------------------------------------
    # Segnali D-Bus (chiamati come metodi normali dal demone)
    # ------------------------------------------------------------------
    def StateChanged(self, new_state: str) -> None: ...
    def DownloadProgress(self, provider: str, model_name: str, percent: int) -> None: ...
    def TranscriptReceived(self, text: str, is_final: bool) -> None: ...
    def ResponseTokenStreamed(self, token: str, is_complete: bool) -> None: ...
