"""
Modulo per il tracciamento centralizzato e persistente dei modelli scaricati.
Mantiene un file manifest 'installed_models.json' per ciascun provider,
eliminando la necessità di scansionare euristicamente le cartelle sul filesystem.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("VoiceAssistant.ModelRegistry")

_PROVIDER_ALIASES = {
    "local": "gguf",
    "llama": "gguf",
    "llm": "gguf",
    "gguf": "gguf",
    "vosk": "vosk",
    "stt": "vosk",
    "whisper": "whisper",
    "faster-whisper": "whisper",
    "tts": "piper",
    "piper": "piper",
    "sherpa": "sherpa-onnx",
    "sherpa-onnx": "sherpa-onnx",
    "sherpa_onnx": "sherpa-onnx",
    "wakeword": "sherpa-onnx",
    "ww": "sherpa-onnx",
    "openwakeword": "openwakeword",
    "open_wakeword": "openwakeword",
    "oww": "openwakeword",
    "ollama": "ollama",
}


def canonical_provider(provider: str) -> str:
    """Normalizza il nome del provider in una chiave canonica."""
    p = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


class ModelRegistry:
    """
    Gestore centralizzato del manifest dei modelli installati (installed_models.json).
    Fornisce accesso O(1), registrazione atomica al download, cancellazione esatta
    dei file registrati e riconciliazione automatica con il disco per installazioni pregresse.
    """

    MANIFEST_FILENAME = "installed_models.json"

    def __init__(self, models_dir: Optional[str | Path] = None, manifest_path: Optional[str | Path] = None):
        self._lock = threading.RLock()
        self._models_dir_override = Path(models_dir) if models_dir else None
        self._manifest_path_override = Path(manifest_path) if manifest_path else None
        self._data: Optional[Dict[str, Any]] = None
        self._loaded_path: Optional[Path] = None
        self._ensure_loaded()

    def _get_models_base_dir(self) -> Path:
        if self._models_dir_override:
            return self._models_dir_override
        try:
            from core.path_utils import get_models_dir
            return get_models_dir()
        except Exception:
            data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            return Path(data_home) / "voice-assistant" / "models"

    def get_manifest_path(self) -> Path:
        """Restituisce il percorso del file installed_models.json."""
        if self._manifest_path_override:
            return self._manifest_path_override
        base = self._get_models_base_dir()
        return base / self.MANIFEST_FILENAME

    def _ensure_loaded(self) -> Dict[str, Any]:
        """Carica il manifest dal disco o ne inizializza uno nuovo con auto-reconciliation."""
        path = self.get_manifest_path()
        if self._data is not None and self._loaded_path == path:
            return self._data

        if path.exists() and path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "providers" in data:
                        self._data = data
                        self._loaded_path = path
                        return self._data
            except Exception as e:
                logger.warning(f"Impossibile leggere {path} ({e}), rigenero manifest da disco.")

        # Inizializza nuovo manifest ed esegue discovery iniziale
        data = {
            "version": 1,
            "updated_at": time.time(),
            "providers": {
                "vosk": {},
                "whisper": {},
                "piper": {},
                "gguf": {},
                "sherpa-onnx": {},
                "ollama": {},
            },
        }
        self._data = data
        self._loaded_path = path
        self.reconcile_with_disk()
        return self._data

    def save(self) -> bool:
        """Salva atomicamente il manifest su disco."""
        with self._lock:
            if self._data is None:
                return False
            path = self.get_manifest_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._data["updated_at"] = time.time()
                tmp_path = path.parent / f".{path.name}.tmp"
                with tmp_path.open("w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, path)
                self._loaded_path = path
                return True
            except Exception as e:
                logger.error(f"Errore salvataggio {path}: {e}")
                return False

    def is_installed(self, provider: str, model_id: str) -> bool:
        """
        Verifica O(1) se il modello è registrato come installato.
        Verifica anche che il percorso principale su disco sia ancora esistente.
        """
        if not model_id:
            return False
        with self._lock:
            data = self._ensure_loaded()
            cp = canonical_provider(provider)
            providers_dict = data.get("providers", {})
            prov_entries = providers_dict.get(cp, {})

            m_id_low = model_id.strip().lower()
            m_base_low = m_id_low.split("/")[-1]
            m_file_low = m_id_low.split(":")[-1] if ":" in m_id_low else m_base_low
            m_stem = m_file_low[:-5] if m_file_low.endswith(".gguf") else (m_file_low[:-4] if m_file_low.endswith(".bin") else m_file_low)

            # Controllo diretto per chiave ID o filename/stem
            for key, entry in prov_entries.items():
                if cp == "ollama":
                    k_norm = key.lower() if ":" in key else f"{key.lower()}:latest"
                    m_norm = m_id_low if ":" in m_id_low else f"{m_id_low}:latest"
                    if k_norm == m_norm:
                        return True
                    continue

                k_low = key.lower()
                k_file = entry.get("filename", "").lower()
                k_id = entry.get("id", "").lower()
                k_stem = k_file[:-5] if k_file.endswith(".gguf") else (k_file[:-4] if k_file.endswith(".bin") else k_file)
                k_id_stem = k_id[:-5] if k_id.endswith(".gguf") else (k_id[:-4] if k_id.endswith(".bin") else k_id)

                targets = (k_low, k_id, k_file, k_stem, k_id_stem)
                if (m_id_low in targets or
                    m_base_low in targets or
                    m_file_low in targets or
                    m_stem in targets):
                    # Se il provider è locale su disco, verifica esistenza file/cartella
                    p_path = entry.get("path")
                    if p_path and cp != "ollama":
                        if not os.path.exists(p_path):
                            # File cancellato manualmente dall'esterno, rimuovi da registry
                            logger.info(f"Modello {key} non trovato su disco ({p_path}), pruning dal registry.")
                            prov_entries.pop(key, None)
                            self.save()
                            return False
                    return True

            # Se non trovato nel provider primario, controlla provider specifici alternativi (es. STT vosk vs whisper)
            if provider.lower() == "stt":
                for alt_prov in ("vosk", "whisper"):
                    if alt_prov != cp and self.is_installed(alt_prov, model_id):
                        return True

            return False

    def get_installed_models(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        """Restituisce l'elenco dei modelli registrati come installati."""
        with self._lock:
            data = self._ensure_loaded()
            providers_dict = data.get("providers", {})

            if provider:
                cp = canonical_provider(provider)
                if provider.lower() == "stt":
                    # Unione per il macro-servizio STT
                    vosk_items = list(providers_dict.get("vosk", {}).values())
                    whisper_items = list(providers_dict.get("whisper", {}).values())
                    return vosk_items + whisper_items
                if provider.lower() in ("wakeword", "ww"):
                    sherpa_items = list(providers_dict.get("sherpa-onnx", {}).values())
                    res = list(sherpa_items)
                    seen_ids = {m.get("id") for m in res}
                    for p_dict in providers_dict.values():
                        for m in p_dict.values():
                            if m.get("service") == "wakeword" and m.get("id") not in seen_ids:
                                res.append(m)
                                seen_ids.add(m.get("id"))
                    return res
                prov_entries = providers_dict.get(cp, {})
                return list(prov_entries.values())

            # Tutti i provider
            res = []
            for p_dict in providers_dict.values():
                res.extend(p_dict.values())
            return res

    def get_installed_model_ids(self, provider: str) -> Set[str]:
        """Restituisce un set di identificatori, nomi file e stem dei modelli installati per il provider."""
        ids: Set[str] = set()
        models = self.get_installed_models(provider)
        for m in models:
            m_id = m.get("id", "")
            if m_id:
                ids.add(m_id)
                ids.add(m_id.lower())
            m_fn = m.get("filename", "")
            if m_fn:
                ids.add(m_fn)
                ids.add(m_fn.lower())
                stem = m_fn[:-5] if m_fn.lower().endswith(".gguf") else (m_fn[:-5] if m_fn.lower().endswith(".onnx") else (m_fn[:-4] if m_fn.lower().endswith(".bin") else m_fn))
                ids.add(stem)
                ids.add(stem.lower())
                if "_v" in stem:
                    base_stem = stem.split("_v")[0]
                    ids.add(base_stem)
                    ids.add(base_stem.lower())
            p = m.get("path", "")
            if p:
                bname = os.path.basename(p)
                ids.add(bname)
                ids.add(bname.lower())
                p_no_ext = os.path.splitext(bname)[0]
                ids.add(p_no_ext)
                ids.add(p_no_ext.lower())
        return ids

    def register_model(
        self,
        provider: str,
        model_id: str,
        name: Optional[str] = None,
        service: Optional[str] = None,
        path: Optional[str] = None,
        files: Optional[List[str]] = None,
        size_bytes: int = 0,
        size_text: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Registra un modello installato nel manifest.
        Se size_bytes non è fornito o è 0, calcola automaticamente la dimensione da disco.
        """
        with self._lock:
            data = self._ensure_loaded()
            cp = canonical_provider(provider)
            providers_dict = data.setdefault("providers", {})
            prov_entries = providers_dict.setdefault(cp, {})

            file_list = [str(f) for f in files] if files else ([str(path)] if path else [])
            resolved_path = str(path) if path else (file_list[0] if file_list else "")
            calculated_size = size_bytes
            if calculated_size <= 0 and file_list:
                total = 0
                for f_item in file_list:
                    p_obj = Path(f_item)
                    if p_obj.is_file():
                        total += p_obj.stat().st_size
                    elif p_obj.is_dir():
                        for root, _, fs in os.walk(p_obj):
                            for fname in fs:
                                try:
                                    total += os.path.getsize(os.path.join(root, fname))
                                except OSError:
                                    pass
                calculated_size = total

            if not size_text:
                size_mb = round(calculated_size / (1024 * 1024))
                size_text = f"{size_mb} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"

            filename = os.path.basename(resolved_path) if resolved_path else ""

            entry = {
                "id": model_id,
                "name": name or model_id,
                "service": service or "",
                "provider": cp,
                "filename": filename,
                "path": resolved_path,
                "files": file_list,
                "size_bytes": calculated_size,
                "size_text": size_text,
                "installed_at": time.time(),
                "extra": extra or {},
            }

            prov_entries[model_id] = entry
            self.save()
            logger.info(f"Modello '{model_id}' registrato con successo nel manifest per provider '{cp}'.")
            return entry

    def unregister_model(self, provider: str, model_id: str) -> Optional[Dict[str, Any]]:
        """Rimuove un modello dal manifest senza cancellare i file dal disco."""
        with self._lock:
            data = self._ensure_loaded()
            cp = canonical_provider(provider)
            prov_entries = data.get("providers", {}).get(cp, {})
            target_key = None
            for key, entry in prov_entries.items():
                if cp == "ollama":
                    k_norm = key.lower() if ":" in key else f"{key.lower()}:latest"
                    m_norm = model_id.lower() if ":" in model_id else f"{model_id.lower()}:latest"
                    if k_norm == m_norm:
                        target_key = key
                        break
                    continue

                if (key.lower() == model_id.lower() or
                    entry.get("id", "").lower() == model_id.lower() or
                    entry.get("filename", "").lower() == model_id.lower() or
                    os.path.basename(entry.get("path", "")).lower() == model_id.lower()):
                    target_key = key
                    break

            if target_key:
                removed = prov_entries.pop(target_key)
                self.save()
                logger.info(f"Modello '{model_id}' rimosso dal manifest per provider '{cp}'.")
                return removed
            return None

    def delete_model(self, provider: str, model_id: str) -> bool:
        """
        Elimina fisicamente dal disco i file associati al modello e lo rimuove dal manifest.
        Non fa guessing di cartelle o regex; cancella esattamente i file e la cartella registrati.
        """
        with self._lock:
            cp = canonical_provider(provider)
            entry = self.unregister_model(provider, model_id)

            if entry:
                deleted_any = False
                files_to_remove = entry.get("files", [])
                main_path = entry.get("path", "")

                for f_path in files_to_remove:
                    try:
                        p = Path(f_path)
                        if p.is_file() or p.is_symlink():
                            p.unlink(missing_ok=True)
                            deleted_any = True
                        elif p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                            deleted_any = True
                    except Exception as e:
                        logger.warning(f"Errore rimozione file {f_path}: {e}")

                if main_path and os.path.exists(main_path):
                    try:
                        p = Path(main_path)
                        if p.is_file():
                            p.unlink(missing_ok=True)
                            deleted_any = True
                        elif p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                            deleted_any = True
                    except Exception as e:
                        logger.warning(f"Errore rimozione percorso principale {main_path}: {e}")

                return True

            # Fallback resiliente: se il modello non era registrato nel manifest, prova cleanup disk
            return self._fallback_disk_delete(cp, model_id)

    def _fallback_disk_delete(self, provider: str, model_id: str) -> bool:
        """Pulizia di fallback per modelli non registrati scaricati prima della migrazione."""
        base = self._get_models_base_dir()
        dirs = [
            base,
            base / "stt",
            base / "tts",
            base / "llm",
            base / "sherpa",
            base / "wakeword",
            base / "wakeword" / "openwakeword",
            base / "openwakeword",
        ]
        m_low = model_id.lower()
        m_file = model_id.split(":")[-1].lower() if ":" in model_id else model_id.split("/")[-1].lower()
        deleted = False

        for d in dirs:
            if not d.exists() or not d.is_dir():
                continue
            for entry in d.iterdir():
                e_low = entry.name.lower()
                e_stem = entry.stem.lower()
                if e_low in (m_low, m_file) or e_stem in (m_low, m_file) or (provider == "piper" and e_low.startswith(m_low)):
                    try:
                        if entry.is_dir():
                            shutil.rmtree(entry, ignore_errors=True)
                        else:
                            entry.unlink(missing_ok=True)
                        deleted = True
                    except Exception:
                        pass
        return deleted

    def reconcile_with_disk(self, custom_models_dir: Optional[Path] = None) -> None:
        """
        Riconciliazione trasparente (auto-discovery):
        scansiona le directory per censire i modelli già presenti su disco
        e aggiungerli al file installed_models.json se non ancora censiti.
        """
        with self._lock:
            base = custom_models_dir or self._get_models_base_dir()
            data = self._ensure_loaded()
            provs = data.setdefault("providers", {})

            # Pruning di modelli i cui file non esistono più sul filesystem
            for p_key, p_entries in list(provs.items()):
                if p_key == "ollama":
                    continue
                for m_key, m_val in list(p_entries.items()):
                    m_path = m_val.get("path")
                    if m_path and not os.path.exists(m_path):
                        p_entries.pop(m_key, None)

            # 1. LLM GGUF (cartella base/llm)
            llm_dir = base / "llm"
            gguf_entries = provs.setdefault("gguf", {})
            if llm_dir.exists() and llm_dir.is_dir():
                for entry in llm_dir.iterdir():
                    if entry.is_file() and entry.suffix.lower() in (".gguf", ".bin") and entry.stat().st_size > 0:
                        # Un download interrotto lascia un marcatore .part accanto al file:
                        # censirlo come installato produrrebbe un modello che l'utente vede
                        # disponibile ma che llama.cpp non riesce a caricare.
                        if entry.with_name(entry.name + ".part").exists():
                            logger.info(f"Ignorato {entry.name}: download incompleto in corso.")
                            continue
                        if entry.name not in gguf_entries:
                            clean_name = entry.stem.replace("-GGUF", "").replace("-gguf", "").replace("-", " ")
                            size_bytes = entry.stat().st_size
                            size_mb = round(size_bytes / (1024 * 1024))
                            size_str = f"{size_mb} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"
                            gguf_entries[entry.name] = {
                                "id": entry.name,
                                "name": clean_name,
                                "service": "llm",
                                "provider": "gguf",
                                "filename": entry.name,
                                "path": str(entry),
                                "files": [str(entry)],
                                "size_bytes": size_bytes,
                                "size_text": size_str,
                                "installed_at": entry.stat().st_mtime,
                                "extra": {},
                            }

            # 2. Piper TTS (cartella base/tts)
            tts_dir = base / "tts"
            piper_entries = provs.setdefault("piper", {})
            if tts_dir.exists() and tts_dir.is_dir():
                for entry in tts_dir.iterdir():
                    if entry.is_file() and entry.suffix.lower() == ".onnx" and entry.stat().st_size > 0:
                        v_id = entry.stem
                        if v_id not in piper_entries:
                            json_companion = tts_dir / f"{entry.name}.json"
                            companion_files = [str(entry)]
                            if json_companion.exists():
                                companion_files.append(str(json_companion))
                            size_bytes = entry.stat().st_size + (json_companion.stat().st_size if json_companion.exists() else 0)
                            size_mb = round(size_bytes / (1024 * 1024))
                            size_str = f"{size_mb} MB"
                            piper_entries[v_id] = {
                                "id": v_id,
                                "name": v_id,
                                "service": "tts",
                                "provider": "piper",
                                "filename": entry.name,
                                "path": str(entry),
                                "files": companion_files,
                                "size_bytes": size_bytes,
                                "size_text": size_str,
                                "installed_at": entry.stat().st_mtime,
                                "extra": {},
                            }

            def _calc_size(p: Path) -> tuple[int, str]:
                if p.is_file():
                    b = p.stat().st_size
                elif p.is_dir():
                    b = 0
                    for root, _, fs in os.walk(p):
                        for f in fs:
                            try:
                                b += os.path.getsize(os.path.join(root, f))
                            except OSError:
                                pass
                else:
                    b = 0
                mb = round(b / (1024 * 1024))
                txt = f"{mb} MB" if mb < 1024 else f"{mb / 1024:.1f} GB"
                return b, txt

            # 3. Vosk STT (cartella base/stt o base)
            stt_dir = base / "stt"
            vosk_entries = provs.setdefault("vosk", {})
            search_vosk = [stt_dir, base]
            for sdir in search_vosk:
                if sdir.exists() and sdir.is_dir():
                    for entry in sdir.iterdir():
                        if entry.is_dir() and not entry.name.startswith("."):
                            if entry.name in ("stt", "tts", "llm", "sherpa", "wakeword", ".locks"):
                                continue
                            if entry.name.startswith("vosk-") and not entry.name.startswith("vosk-model-tts-") and not entry.name.startswith("vosk-model-spk-"):
                                if entry.name not in vosk_entries or not vosk_entries[entry.name].get("size_bytes"):
                                    sz_bytes, sz_text = _calc_size(entry)
                                    vosk_entries[entry.name] = {
                                        "id": entry.name,
                                        "name": entry.name,
                                        "service": "stt",
                                        "provider": "vosk",
                                        "filename": entry.name,
                                        "path": str(entry),
                                        "files": [str(entry)],
                                        "size_bytes": sz_bytes,
                                        "size_text": sz_text,
                                        "installed_at": entry.stat().st_mtime,
                                        "extra": {},
                                    }

            # 4. Whisper STT (cartella base/stt o base)
            whisper_entries = provs.setdefault("whisper", {})
            for sdir in search_vosk:
                if sdir.exists() and sdir.is_dir():
                    for entry in sdir.iterdir():
                        if entry.is_dir() and not entry.name.startswith("."):
                            name = entry.name
                            short_id = None
                            if name.startswith("whisper-"):
                                short_id = name.replace("whisper-", "")
                            elif name.startswith("models--Systran--faster-whisper-"):
                                short_id = name.replace("models--Systran--faster-whisper-", "")
                            elif name in ("tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large-v3-turbo"):
                                short_id = name

                            if short_id and (short_id not in whisper_entries or not whisper_entries[short_id].get("size_bytes")):
                                sz_bytes, sz_text = _calc_size(entry)
                                whisper_entries[short_id] = {
                                    "id": short_id,
                                    "name": f"Whisper {short_id.capitalize()}",
                                    "service": "stt",
                                    "provider": "whisper",
                                    "filename": name,
                                    "path": str(entry),
                                    "files": [str(entry)],
                                    "size_bytes": sz_bytes,
                                    "size_text": sz_text,
                                    "installed_at": entry.stat().st_mtime,
                                    "extra": {},
                                }

            # 5. Sherpa ONNX Wakeword (cartella base/sherpa o base)
            sherpa_entries = provs.setdefault("sherpa-onnx", {})
            search_sherpa = [base / "sherpa", base / "wakeword", base]
            for sdir in search_sherpa:
                if sdir.exists() and sdir.is_dir():
                    for entry in sdir.iterdir():
                        if entry.is_dir() and not entry.name.startswith("."):
                            if entry.name in ("stt", "tts", "llm", "sherpa", "wakeword", ".locks"):
                                continue
                            if entry.name.startswith("sherpa-onnx-") or (entry / "tokens.txt").exists():
                                if entry.name not in sherpa_entries or not sherpa_entries[entry.name].get("size_bytes"):
                                    sz_bytes, sz_text = _calc_size(entry)
                                    sherpa_entries[entry.name] = {
                                        "id": entry.name,
                                        "name": entry.name,
                                        "service": "wakeword",
                                        "provider": "sherpa-onnx",
                                        "filename": entry.name,
                                        "path": str(entry),
                                        "files": [str(entry)],
                                        "size_bytes": sz_bytes,
                                        "size_text": sz_text,
                                        "installed_at": entry.stat().st_mtime,
                                        "extra": {},
                                    }

            # 6. OpenWakeWord (cartella base/wakeword/openwakeword, base/wakeword, base/openwakeword)
            oww_entries = provs.setdefault("openwakeword", {})
            search_oww = [base / "wakeword" / "openwakeword", base / "wakeword", base / "openwakeword"]
            oww_excluded_files = {"embedding_model.onnx", "melspectrogram.onnx", "silero_vad.onnx"}
            for sdir in search_oww:
                if sdir.exists() and sdir.is_dir():
                    for entry in sdir.iterdir():
                        if entry.is_file() and entry.suffix.lower() == ".onnx" and not entry.name.startswith("."):
                            if entry.name in oww_excluded_files:
                                continue
                            # Escludi file piper (.onnx con associato .onnx.json)
                            if (entry.parent / f"{entry.name}.json").exists():
                                continue
                            stem = entry.stem
                            clean_id = stem.split("_v")[0] if "_v" in stem else stem
                            if clean_id not in oww_entries or not oww_entries[clean_id].get("size_bytes"):
                                sz_bytes, sz_text = _calc_size(entry)
                                display_name = clean_id.replace("_", " ").title()
                                oww_entries[clean_id] = {
                                    "id": clean_id,
                                    "name": f"OpenWakeWord {display_name}",
                                    "service": "wakeword",
                                    "provider": "openwakeword",
                                    "filename": entry.name,
                                    "path": str(entry),
                                    "files": [str(entry)],
                                    "size_bytes": sz_bytes,
                                    "size_text": sz_text,
                                    "installed_at": entry.stat().st_mtime,
                                    "extra": {"raw_stem": stem},
                                }

            self.save()


# Istanza singleton accessibile globalmente
model_registry = ModelRegistry()


def get_model_registry(models_dir: Optional[str | Path] = None) -> ModelRegistry:
    """Restituisce l'istanza del ModelRegistry, eventualmente per una cartella specifica."""
    if models_dir is not None:
        return ModelRegistry(models_dir=models_dir)
    return model_registry

