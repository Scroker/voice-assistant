"""
Non-Blocking Audio Player for TTS Spoken Output
"""
import queue
import threading

import sys
import logging
from core.logger import ErrorCollector

logger = logging.getLogger("VoiceAssistant.Audio")

try:
    import sounddevice as sd
except ImportError:
    sd = None


class AudioPlayer:
    """
    Thread-safe non-blocking audio player that queues audio buffers
    and plays them sequentially without hanging the main loop.
    """
    def __init__(self, sample_rate: int = 22050, on_playback_finished=None):
        self.sample_rate = sample_rate
        self.queue = queue.Queue()
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._interrupted = False
        self._is_playing = False
        self.on_playback_finished = on_playback_finished

    @property
    def is_playing(self) -> bool:
        return self._is_playing or not self.queue.empty()

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._interrupted = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        self.stop_playback()

    def prepare_playback(self):
        """Reset interruption flag so new audio playback requests are accepted."""
        self._interrupted = False

    def stop_playback(self):
        """Stops ongoing audio playback instantly and flushes queue without killing worker thread."""
        self._interrupted = True
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        if sd:
            try:
                sd.stop()
            except Exception:
                pass
        self._is_playing = False

    def enqueue_audio(self, pcm_data: bytes, sample_rate: int = None):
        """Enqueue PCM audio bytes for playback."""
        sr = sample_rate or self.sample_rate
        self.queue.put((pcm_data, sr))

    def play_wav_bytes(self, wav_bytes: bytes):
        """Parse WAV header and enqueue audio PCM data for playback."""
        import io
        import wave
        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
                sample_rate = wf.getframerate()
                pcm_data = wf.readframes(wf.getnframes())
                self.enqueue_audio(pcm_data, sample_rate)
        except Exception as e:
            logger.error(f"[AudioPlayer] Error parsing WAV bytes: {e}")
            ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.Audio")

    def play_wakeword_chime(self):
        """Generates and plays a pleasant, quick acoustic audio chime when wakeword is detected."""
        self._interrupted = False
        try:
            import numpy as np
            sr = 16000
            duration = 0.15  # 150ms
            t = np.linspace(0, duration, int(sr * duration), False)
            envelope = np.exp(-15 * t)
            sine_wave = (0.3 * np.sin(2 * np.pi * 523.25 * t) + 
                         0.4 * np.sin(2 * np.pi * 659.25 * t) + 
                         0.3 * np.sin(2 * np.pi * 783.99 * t)) * envelope
            audio_int16 = (sine_wave * 32767).astype(np.int16)
            self.enqueue_audio(audio_int16.tobytes(), sr)
        except Exception as e:
            logger.error(f"[AudioPlayer] Errore riproduzione chime wakeword: {e}")

    def _worker(self):
        import numpy as np
        import time
        while self._running and not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.1)
            except queue.Empty:
                if self._is_playing:
                    self._is_playing = False
                    if self.on_playback_finished:
                        try:
                            self.on_playback_finished()
                        except Exception as cb_err:
                            logger.error(f"[AudioPlayer] Errore callback on_playback_finished: {cb_err}")
                continue

            if self._interrupted:
                self.queue.task_done()
                continue

            pcm_data, sr = item
            if not pcm_data:
                self.queue.task_done()
                continue

            self._is_playing = True
            try:
                if sd and not self._stop_event.is_set() and not self._interrupted:
                    audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                    chunk_duration = len(pcm_data) / (float(sr) * 2.0)
                    sd.play(audio_array, samplerate=sr)
                    
                    start_t = time.time()
                    while (time.time() - start_t) < chunk_duration and not self._interrupted and not self._stop_event.is_set():
                        time.sleep(0.02)

                    if self._interrupted and sd:
                        try:
                            sd.stop()
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"[AudioPlayer] Error playing audio chunk: {e}")
                ErrorCollector.record_error(*sys.exc_info(), component="VoiceAssistant.Audio")
            finally:
                self.queue.task_done()
