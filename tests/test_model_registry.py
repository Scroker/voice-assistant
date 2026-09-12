import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

daemon_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "daemon"))
if daemon_dir not in sys.path:
    sys.path.insert(0, daemon_dir)

from core.model_registry import ModelRegistry, canonical_provider


class TestModelRegistry(unittest.TestCase):

    def test_canonical_provider(self):
        self.assertEqual(canonical_provider("local"), "gguf")
        self.assertEqual(canonical_provider("llama"), "gguf")
        self.assertEqual(canonical_provider("GGUF"), "gguf")
        self.assertEqual(canonical_provider("stt"), "vosk")
        self.assertEqual(canonical_provider("whisper"), "whisper")
        self.assertEqual(canonical_provider("piper"), "piper")
        self.assertEqual(canonical_provider("tts"), "piper")
        self.assertEqual(canonical_provider("sherpa"), "sherpa-onnx")
        self.assertEqual(canonical_provider("wakeword"), "sherpa-onnx")
        self.assertEqual(canonical_provider("openwakeword"), "openwakeword")
        self.assertEqual(canonical_provider("oww"), "openwakeword")

    def test_init_creates_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(models_dir=tmpdir)
            manifest = Path(tmpdir) / "installed_models.json"
            self.assertTrue(manifest.exists())
            with open(manifest, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["version"], 1)
            self.assertIn("providers", data)
            self.assertIn("gguf", data["providers"])
            self.assertIn("vosk", data["providers"])

    def test_register_and_is_installed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(models_dir=tmpdir)
            model_file = Path(tmpdir) / "llm" / "test-model.gguf"
            model_file.parent.mkdir(parents=True, exist_ok=True)
            model_file.write_bytes(b"dummy gguf content")

            reg.register_model(
                provider="gguf",
                model_id="test-model.gguf",
                name="Test Model",
                service="llm",
                files=[str(model_file)],
                size_bytes=len(b"dummy gguf content"),
                size_text="18 B",
                extra={"repo": "test/repo"},
            )

            self.assertTrue(reg.is_installed("gguf", "test-model.gguf"))
            self.assertTrue(reg.is_installed("gguf", "test-model"))
            self.assertTrue(reg.is_installed("local", "test-model.gguf"))
            self.assertFalse(reg.is_installed("gguf", "non-existent.gguf"))

            # Check IDs set
            ids = reg.get_installed_model_ids("gguf")
            self.assertIn("test-model.gguf", ids)
            self.assertIn("test-model", ids)

    def test_auto_prune_when_files_removed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(models_dir=tmpdir)
            model_file = Path(tmpdir) / "llm" / "prune-test.gguf"
            model_file.parent.mkdir(parents=True, exist_ok=True)
            model_file.write_bytes(b"temp content")

            reg.register_model(
                provider="gguf",
                model_id="prune-test.gguf",
                files=[str(model_file)],
            )
            self.assertTrue(reg.is_installed("gguf", "prune-test.gguf"))

            # Delete file manually from outside
            model_file.unlink()

            # Next check should detect missing file and prune
            self.assertFalse(reg.is_installed("gguf", "prune-test.gguf"))
            self.assertNotIn("prune-test.gguf", reg.get_installed_model_ids("gguf"))

    def test_delete_model(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reg = ModelRegistry(models_dir=tmpdir)
            tts_dir = Path(tmpdir) / "tts"
            tts_dir.mkdir(parents=True, exist_ok=True)
            onnx_file = tts_dir / "voice.onnx"
            json_file = tts_dir / "voice.onnx.json"
            onnx_file.write_bytes(b"onnx content")
            json_file.write_bytes(b"json content")

            reg.register_model(
                provider="piper",
                model_id="voice",
                files=[str(onnx_file), str(json_file)],
                service="tts",
            )
            self.assertTrue(reg.is_installed("piper", "voice"))
            self.assertTrue(onnx_file.exists())
            self.assertTrue(json_file.exists())

            # Delete model
            success = reg.delete_model("piper", "voice")
            self.assertTrue(success)
            self.assertFalse(onnx_file.exists())
            self.assertFalse(json_file.exists())
            self.assertFalse(reg.is_installed("piper", "voice"))

    def test_reconcile_skips_incomplete_downloads(self):
        """Un GGUF con marcatore .part è un download in corso, non un modello installato."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            llm_dir = base / "llm"
            llm_dir.mkdir(parents=True)

            (llm_dir / "completo.gguf").write_bytes(b"x" * 2048)
            (llm_dir / "parziale.gguf").write_bytes(b"x" * 1024)
            (llm_dir / "parziale.gguf.part").write_text("2104932768")

            reg = ModelRegistry(models_dir=tmpdir)

            self.assertTrue(reg.is_installed("gguf", "completo.gguf"))
            self.assertFalse(
                reg.is_installed("gguf", "parziale.gguf"),
                "Un download incompleto non deve risultare installato",
            )

    def test_reconcile_with_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # 1. Vosk
            vosk_dir = base / "stt" / "vosk-model-small-it-0.22"
            vosk_dir.mkdir(parents=True)
            (vosk_dir / "am").write_bytes(b"dummy am")

            # 2. Whisper
            whisper_dir = base / "stt" / "models--Systran--faster-whisper-base"
            whisper_dir.mkdir(parents=True)
            (whisper_dir / "model.bin").write_bytes(b"dummy whisper")

            # 3. Piper
            tts_dir = base / "tts"
            tts_dir.mkdir(parents=True)
            (tts_dir / "it_IT-paola-medium.onnx").write_bytes(b"paola onnx")
            (tts_dir / "it_IT-paola-medium.onnx.json").write_bytes(b"{}")

            # 4. GGUF
            llm_dir = base / "llm"
            llm_dir.mkdir(parents=True)
            (llm_dir / "Llama-3.2-1B-Instruct-Q4_K_M.gguf").write_bytes(b"dummy gguf")

            # 5. Sherpa
            sherpa_dir = base / "sherpa" / "sherpa-onnx-kws-test"
            sherpa_dir.mkdir(parents=True)
            (sherpa_dir / "tokens.txt").write_bytes(b"tokens")

            # 6. OpenWakeWord
            oww_dir = base / "wakeword" / "openwakeword"
            oww_dir.mkdir(parents=True)
            (oww_dir / "alexa_v0.1.onnx").write_bytes(b"dummy alexa onnx")

            # Initialize ModelRegistry on disk containing models but NO manifest yet
            reg = ModelRegistry(models_dir=tmpdir)

            self.assertTrue(reg.is_installed("vosk", "vosk-model-small-it-0.22"))
            self.assertTrue(reg.is_installed("whisper", "base"))
            self.assertTrue(reg.is_installed("piper", "it_IT-paola-medium"))
            self.assertTrue(reg.is_installed("gguf", "Llama-3.2-1B-Instruct-Q4_K_M.gguf"))
            self.assertTrue(reg.is_installed("sherpa-onnx", "sherpa-onnx-kws-test"))
            self.assertTrue(reg.is_installed("openwakeword", "alexa"))
            self.assertTrue(reg.is_installed("openwakeword", "alexa_v0.1.onnx"))

            # Verify manifest on disk has recorded these
            manifest = base / "installed_models.json"
            self.assertTrue(manifest.exists())
            with open(manifest, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("vosk-model-small-it-0.22", data["providers"]["vosk"])
            self.assertIn("base", data["providers"]["whisper"])
            self.assertIn("it_IT-paola-medium", data["providers"]["piper"])
            self.assertIn("Llama-3.2-1B-Instruct-Q4_K_M.gguf", data["providers"]["gguf"])
            self.assertIn("sherpa-onnx-kws-test", data["providers"]["sherpa-onnx"])
            self.assertIn("alexa", data["providers"]["openwakeword"])

            # Verify deleting piper model cleans both .onnx and .onnx.json
            reg.delete_model("piper", "it_IT-paola-medium")
            self.assertFalse((tts_dir / "it_IT-paola-medium.onnx").exists())
            self.assertFalse((tts_dir / "it_IT-paola-medium.onnx.json").exists())


if __name__ == "__main__":
    unittest.main()
