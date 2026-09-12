import unittest
import sys
import numpy as np
from pathlib import Path

daemon_dir = Path(__file__).resolve().parent.parent / 'src' / 'daemon'
sys.path.insert(0, str(daemon_dir))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

try:
    from daemon.audio.vad import SilenceDetector
    from daemon.audio.player import AudioPlayer
    from daemon.audio.filter import AudioFilter
except ImportError:
    from audio.vad import SilenceDetector
    from audio.player import AudioPlayer
    from audio.filter import AudioFilter

from core.audio_runtime import build_filter_config, is_aec_enabled


class _FakeSchema:
    def __init__(self, keys):
        self._keys = set(keys)

    def has_key(self, key):
        return key in self._keys


class _FakeSettings:
    """Minimal Gio.Settings stand-in covering the accessors used by audio_runtime."""

    def __init__(self, values):
        self._values = dict(values)
        self.settings_schema = _FakeSchema(self._values.keys())

    def get_boolean(self, key):
        return bool(self._values[key])

    def get_double(self, key):
        return float(self._values[key])


class TestAudioModule(unittest.TestCase):
    def test_silence_detector_volume(self):
        detector = SilenceDetector(silence_timeout_sec=1.0, max_duration_sec=5.0, volume_threshold=100.0)
        
        # Test silent audio chunk
        silent_pcm = np.zeros(1600, dtype=np.int16).tobytes()
        res = detector.process_chunk(silent_pcm)
        self.assertFalse(res['is_speaking'])
        self.assertEqual(res['volume'], 0.0)

        # Test loud audio chunk
        loud_pcm = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
        res = detector.process_chunk(loud_pcm)
        self.assertTrue(res['is_speaking'])
        self.assertGreater(res['volume'], 100.0)

    def test_audio_player_queue(self):
        player = AudioPlayer()
        player.start()
        self.assertTrue(player._running)
        
        # Enqueue empty audio bytes
        player.enqueue_audio(b"")
        player.stop()
        self.assertFalse(player._running)


class TestAudioFilterConfiguration(unittest.TestCase):
    """Verifica che ogni stadio del filtro sia disattivabile e regolabile a runtime."""

    def _speech_pcm(self, amplitude=3000, samples=1600):
        t = np.arange(samples)
        wave = (amplitude * np.sin(2 * np.pi * 220 * t / 16000)).astype(np.int16)
        return wave.tobytes()

    def test_all_stages_disabled_is_passthrough(self):
        f = AudioFilter(
            highpass_enabled=False,
            agc_enabled=False,
            noise_gate_enabled=False,
        )
        pcm = self._speech_pcm()
        self.assertEqual(f.process(pcm), pcm, "Con tutti gli stadi disattivati il PCM deve restare intatto")

    def test_noise_gate_attenuation_is_configurable(self):
        quiet = (np.ones(1600, dtype=np.int16) * 20).tobytes()

        muting = AudioFilter(highpass_enabled=False, agc_enabled=False, noise_gate_attenuation=0.0)
        out = np.frombuffer(muting.process(quiet), dtype=np.int16)
        self.assertTrue(np.all(out == 0), "attenuation=0.0 deve silenziare l'audio sotto soglia")

        passthrough = AudioFilter(highpass_enabled=False, agc_enabled=False, noise_gate_enabled=False)
        out2 = np.frombuffer(passthrough.process(quiet), dtype=np.int16)
        self.assertTrue(np.any(out2 != 0), "Con il gate disattivato l'audio sotto soglia non va attenuato")

    def test_gate_threshold_multiplier_used(self):
        f = AudioFilter(noise_gate_threshold=4.0)
        f._noise_floor = 200.0
        self.assertEqual(f.get_dynamic_threshold(), 800.0)

    def test_update_config_recomputes_highpass_coefficients(self):
        f = AudioFilter(highpass_cutoff=80.0)
        original = f.b.copy()
        f.update_config({"highpass_cutoff": 300.0})
        self.assertEqual(f.highpass_cutoff, 300.0)
        self.assertFalse(np.array_equal(original, f.b), "I coefficienti biquad devono essere ricalcolati")

    def test_out_of_range_values_are_clamped(self):
        f = AudioFilter()
        f.update_config({
            "highpass_cutoff": 99999.0,
            "agc_max_gain": 0.1,
            "noise_gate_attenuation": 5.0,
            "noise_gate_threshold": -3.0,
        })
        self.assertEqual(f.highpass_cutoff, AudioFilter.HIGHPASS_CUTOFF_RANGE[1])
        self.assertEqual(f.agc_max_gain, AudioFilter.AGC_MAX_GAIN_RANGE[0])
        self.assertEqual(f.noise_gate_attenuation, AudioFilter.GATE_ATTENUATION_RANGE[1])
        self.assertEqual(f.noise_gate_threshold, AudioFilter.GATE_THRESHOLD_RANGE[0])

    def test_disabling_agc_resets_gain(self):
        f = AudioFilter()
        f.current_gain = 1.8
        f.update_config({"agc_enabled": False})
        self.assertEqual(f.current_gain, 1.0)


class TestAudioSettingsBridge(unittest.TestCase):
    """Verifica la traduzione delle chiavi GSettings nella configurazione di AudioFilter."""

    def test_build_filter_config_maps_every_key(self):
        settings = _FakeSettings({
            "audio-highpass-enabled": False,
            "audio-highpass-cutoff": 120.0,
            "audio-agc-enabled": False,
            "audio-agc-target-rms": 900.0,
            "audio-agc-max-gain": 3.5,
            "audio-noise-gate-enabled": True,
            "audio-noise-gate-threshold": 2.5,
            "audio-noise-gate-attenuation": 0.1,
        })
        config = build_filter_config(settings)
        self.assertEqual(config["highpass_enabled"], False)
        self.assertEqual(config["highpass_cutoff"], 120.0)
        self.assertEqual(config["agc_max_gain"], 3.5)
        self.assertEqual(config["noise_gate_attenuation"], 0.1)
        # Il risultato deve essere accettato direttamente dal costruttore.
        AudioFilter(**config)

    def test_missing_keys_are_skipped(self):
        """Uno schema compilato obsoleto non deve far crashare il demone."""
        config = build_filter_config(_FakeSettings({"audio-highpass-cutoff": 90.0}))
        self.assertEqual(config, {"highpass_cutoff": 90.0})

    def test_no_settings_yields_defaults(self):
        self.assertEqual(build_filter_config(None), {})
        self.assertTrue(is_aec_enabled(None), "Senza GSettings l'AEC resta abilitato come da default")

    def test_aec_flag_read_from_settings(self):
        self.assertFalse(is_aec_enabled(_FakeSettings({"audio-aec-enabled": False})))
        self.assertTrue(is_aec_enabled(_FakeSettings({"audio-aec-enabled": True})))


if __name__ == '__main__':
    unittest.main()
