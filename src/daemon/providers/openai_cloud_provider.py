"""
Cloud Speech-To-Text Provider for OpenAI Whisper and Groq Whisper Cloud APIs.
Sends batch audio PCM int16 buffer to cloud transcription endpoints over HTTPS.
"""
import os
import io
import wave
import json
import logging
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any
from .base import STTProvider

logger = logging.getLogger("VoiceAssistant.CloudSTT")

class OpenAICloudSTTProvider(STTProvider):
    """
    Cloud STT Provider supporting OpenAI Whisper API and Groq Cloud Whisper API.
    """
    def __init__(self, model: str = "whisper-1", hardware: str = "cloud", extra: Optional[dict] = None, progress_callback=None, models_dir=None, download_only=False):
        self.model = model or "whisper-1"
        extra = extra or {}
        self.api_key = extra.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self.endpoint = extra.get("endpoint") or "https://api.openai.com/v1/audio/transcriptions"
        self.language = extra.get("language") or "it"
        self.audio_buffer = bytearray()

    def process_chunk(self, data: bytes) -> tuple[str, str]:
        """Accumula i chunk audio in memoria durante l'ascolto."""
        if data:
            self.audio_buffer.extend(data)
        return "", ""

    def flush_and_transcribe(self) -> str:
        """Sintetizza l'audio accumulato in formato WAV e invia la richiesta all'API Cloud."""
        if not self.audio_buffer:
            return ""

        pcm_data = bytes(self.audio_buffer)
        self.reset()

        if len(pcm_data) < 3200:  # Meno di 0.1s di audio
            return ""

        # Converti PCM 16kHz Int16 Mono in WAV in memoria
        wav_buffer = io.BytesIO()
        try:
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm_data)
        except Exception as e:
            logger.error(f"[CloudSTT] Errore conversione WAV: {e}")
            return ""

        wav_bytes = wav_buffer.getvalue()

        api_key = self.api_key
        if not api_key:
            logger.warning("[CloudSTT] Nessuna chiave API trovata per Cloud STT.")
            return ""

        # Costruzione del payload multipart/form-data
        boundary = "----WebKitFormBoundaryVoiceAssistantCloudSTT"
        body = []

        # Campo model
        body.append(f"--{boundary}\r\n".encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="model"\r\n\r\n{self.model}\r\n'.encode('utf-8'))

        # Campo language
        if self.language:
            body.append(f"--{boundary}\r\n".encode('utf-8'))
            body.append(f'Content-Disposition: form-data; name="language"\r\n\r\n{self.language}\r\n'.encode('utf-8'))

        # Campo file audio
        body.append(f"--{boundary}\r\n".encode('utf-8'))
        body.append(f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'.encode('utf-8'))
        body.append(b'Content-Type: audio/wav\r\n\r\n')
        body.append(wav_bytes)
        body.append(b'\r\n')

        body.append(f"--{boundary}--\r\n".encode('utf-8'))

        payload = b''.join(body)

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}"
        }

        try:
            req = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15.0) as response:
                resp_text = response.read().decode('utf-8')
                try:
                    res_json = json.loads(resp_text)
                    transcript = res_json.get("text", "").strip()
                except Exception:
                    transcript = resp_text.strip()

                logger.info(f"[CloudSTT] Risposta API Cloud: '{transcript}'")
                return transcript
        except Exception as e:
            logger.error(f"[CloudSTT] Errore richiesta HTTP Cloud STT ({self.endpoint}): {e}")
            return ""

    def reset(self):
        self.audio_buffer.clear()

    @classmethod
    def get_available_models(cls) -> list[dict]:
        return [
            {
                "id": "whisper-1",
                "provider": "openai_cloud",
                "name": "OpenAI Whisper Cloud (whisper-1)",
                "subtitle": "OpenAI Cloud • High Accuracy • Fast",
                "lang": "multilingual",
                "lang_text": "Multilingual",
                "size_text": "Cloud API"
            },
            {
                "id": "whisper-large-v3",
                "provider": "groq_cloud",
                "name": "Groq Whisper Cloud (whisper-large-v3)",
                "subtitle": "Groq Cloud • Ultra Fast Whisper",
                "lang": "multilingual",
                "lang_text": "Multilingual",
                "size_text": "Cloud API"
            }
        ]
