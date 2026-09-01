from .base import STTProvider
from .vosk_provider import VoskProvider
from .whisper_provider import WhisperProvider
from .openai_cloud_provider import OpenAICloudSTTProvider

def get_provider(provider_name: str, model: str, hardware: str, extra: dict, progress_callback=None, models_dir=None, download_only=False) -> STTProvider:
    p = provider_name.lower()
    if p == "vosk":
        return VoskProvider(model, hardware, extra, progress_callback, models_dir=models_dir, download_only=download_only)
    elif p == "whisper":
        return WhisperProvider(model, hardware, extra, progress_callback, models_dir=models_dir, download_only=download_only)
    elif p in ("openai_cloud", "groq_cloud", "cloud_stt"):
        if p == "groq_cloud" and "endpoint" not in extra:
            extra = dict(extra or {})
            extra["endpoint"] = "https://api.groq.com/openai/v1/audio/transcriptions"
        return OpenAICloudSTTProvider(model, hardware, extra, progress_callback, models_dir=models_dir, download_only=download_only)
    else:
        raise ValueError(f"Provider STT non supportato: {provider_name}")

def get_available_models(provider_name: str) -> list[dict]:
    p = provider_name.lower()
    if p == "vosk":
        return VoskProvider.get_available_models()
    elif p == "whisper":
        return WhisperProvider.get_available_models()
    elif p in ("openai_cloud", "groq_cloud", "cloud_stt"):
        return OpenAICloudSTTProvider.get_available_models()
    return []


