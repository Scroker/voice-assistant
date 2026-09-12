"""
Componenti riutilizzabili per l'interfaccia grafica dell'Assistente Vocale.
"""

from .resources import register_resources, register_icons
from .daemon_client import DaemonClient
from .chat import ChatBubble, ChatView
from .settings import (
    LanguageSelector,
    ModelsStorageManager,
    GeneralSettings,
    WakeWordSettings,
    STTSettings,
    LLMSettings,
    TTSSettings,
    BugReportSettings,
    AboutSettings,
)

__all__ = [
    "register_resources",
    "register_icons",
    "DaemonClient",
    "ChatBubble",
    "ChatView",
    "LanguageSelector",
    "ModelsStorageManager",
    "GeneralSettings",
    "WakeWordSettings",
    "STTSettings",
    "LLMSettings",
    "TTSSettings",
    "BugReportSettings",
    "AboutSettings",
]
