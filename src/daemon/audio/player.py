"""
Non-Blocking Audio Player for TTS Spoken Output
"""
import queue
import threading

try:
    import sounddevice as sd
except ImportError:
    sd = None


class AudioPlayer:
    """
    Thread-safe non-blocking audio player that queues audio buffers
    and plays them sequentially without hanging the main loop.
    """
    def __init__(self, sample_rate: int = 22050):
        self.sample_rate = sample_rate
        self.queue = queue.Queue()
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        # Clear remaining queue
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

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
            print(f"[AudioPlayer] Error parsing WAV bytes: {e}")

    def _worker(self):
        import numpy as np
        while self._running and not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue

            pcm_data, sr = item
            if not pcm_data:
                continue

            try:
                if sd:
                    audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                    sd.play(audio_array, samplerate=sr)
                    sd.wait()
                else:
                    print("[AudioPlayer] Warning: sounddevice is not available.")
            except Exception as e:
                print(f"[AudioPlayer] Error playing audio chunk: {e}")
            finally:
                self.queue.task_done()
