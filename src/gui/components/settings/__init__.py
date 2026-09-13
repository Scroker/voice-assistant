"""
Package componenti per le pagine e i controlli delle impostazioni.
"""

from .base import bind_setting, bind_radio_group
from .general import GeneralSettings, LanguageSelector, SUPPORTED_LANGUAGES
from .audio import AudioSettings
from .dispatch import DispatchSettings
from .wakeword import WakeWordSettings
from .stt import STTSettings
from .llm import LLMSettings
from .tts import TTSSettings
from .mcp import MCPSettings
from .skills import SkillsSettings
from .model_selector import ModelSelectorController
from .models import ModelsStorageManager
from .bugreport import BugReportSettings
from .about import AboutSettings
from .speaker_id import SpeakerIdSettings

__all__ = [
    "bind_setting",
    "bind_radio_group",
    "GeneralSettings",
    "LanguageSelector",
    "SUPPORTED_LANGUAGES",
    "AudioSettings",
    "DispatchSettings",
    "WakeWordSettings",
    "STTSettings",
    "LLMSettings",
    "TTSSettings",
    "MCPSettings",
    "SkillsSettings",
    "ModelSelectorController",
    "ModelsStorageManager",
    "BugReportSettings",
    "AboutSettings",
    "SpeakerIdSettings",
]
