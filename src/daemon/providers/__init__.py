from .base import STTProvider
from .vosk_provider import VoskProvider
from .whisper_provider import WhisperProvider

def get_provider(provider_name: str, model: str, hardware: str, extra: dict, progress_callback=None, models_dir=None) -> STTProvider:
    if provider_name == "vosk":
        return VoskProvider(model, hardware, extra, progress_callback, models_dir=models_dir)
    elif provider_name == "whisper":
        return WhisperProvider(model, hardware, extra, progress_callback, models_dir=models_dir)
    else:
        raise ValueError(f"Provider STT non supportato: {provider_name}")

def get_available_models(provider_name: str) -> list[dict]:
    p = provider_name.lower()
    if p == "vosk":
        return VoskProvider.get_available_models()
    elif p == "whisper":
        return WhisperProvider.get_available_models()
    return []

