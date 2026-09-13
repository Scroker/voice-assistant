"""Storage and management for speaker voice profiles."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

import unicodedata

logger = logging.getLogger("VoiceAssistant.SpeakerID.Profiles")

MAX_HISTORY = 19


def sanitize_profile_id(name: str) -> str:
    """Generate a safe, filesystem-friendly slug for a profile name."""
    clean = (name or "").strip()
    clean = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode("ascii").lower()
    clean = re.sub(r"[\s.]+", "-", clean)
    clean = re.sub(r"[^a-z0-9_-]", "", clean)
    clean = re.sub(r"-+", "-", clean)
    clean = re.sub(r"_+", "_", clean).strip("-_")
    return clean or "speaker"


def get_gnome_session_user() -> str:
    """Return the username of the active GNOME desktop session."""
    try:
        from gi.repository import GLib
        u = GLib.get_user_name()
        if u and u.strip():
            return u.strip()
    except Exception:
        pass
    import getpass
    return os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser() or "user"


def get_gnome_session_uid() -> Optional[int]:
    """Return the UID of the active GNOME user session."""
    try:
        return os.getuid()
    except Exception:
        return None


def get_gnome_session_display_name() -> str:
    """Return the real name or display name of the GNOME session user."""
    try:
        from gi.repository import GLib
        r = GLib.get_real_name()
        if r and r.strip() and r.strip() != "Unknown":
            return r.strip()
        u = GLib.get_user_name()
        if u and u.strip():
            return u.strip()
    except Exception:
        pass
    import getpass
    return os.environ.get("USER") or getpass.getuser() or "Utente"


class SpeakerProfileStore:
    """Manages speaker profile files (*.npz) on disk with atomic persistence."""

    def __init__(
        self,
        directory: Optional[Path | str] = None,
        active_backend: str = "resemblyzer",
        max_history: int = MAX_HISTORY,
    ) -> None:
        if directory is not None:
            self._dir = Path(directory)
        else:
            from core.path_utils import get_data_dir
            self._dir = get_data_dir() / "speakers"

        self._dir.mkdir(parents=True, exist_ok=True)
        self.active_backend = active_backend
        self.max_history = max_history
        self._lock = threading.RLock()
        self._ref_cache: Dict[str, Any] = {}

    def invalidate_cache(self, profile_id: Optional[str] = None) -> None:
        """Invalidate reference cache for a specific profile or all profiles."""
        with self._lock:
            if profile_id:
                safe_id = sanitize_profile_id(profile_id)
                self._ref_cache.pop(safe_id, None)
            else:
                self._ref_cache.clear()

    @property
    def directory(self) -> Path:
        return self._dir

    def _file_path(self, profile_id: str) -> Path:
        safe_id = sanitize_profile_id(profile_id)
        return self._dir / f"{safe_id}.npz"

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List all available speaker profiles with metadata and session user binding."""
        current_user = get_gnome_session_user()
        current_uid = get_gnome_session_uid()
        with self._lock:
            profiles = []
            if not self._dir.is_dir():
                return profiles

            for path in sorted(self._dir.glob("*.npz")):
                try:
                    profile_data = self._load_file(path)
                    if profile_data is None:
                        continue
                    meta = profile_data.get("meta", {})
                    profile_id = path.stem
                    needs_reenroll = meta.get("backend") != self.active_backend
                    prof_user = meta.get("username") or meta.get("gnome_session_user") or profile_id
                    prof_uid = meta.get("uid")
                    is_current = (prof_user == current_user) or (prof_uid is not None and current_uid is not None and prof_uid == current_uid)
                    profiles.append({
                        "id": profile_id,
                        "display_name": meta.get("display_name", profile_id),
                        "username": prof_user,
                        "uid": prof_uid,
                        "is_current_user": is_current,
                        "created_at": meta.get("created_at", ""),
                        "updated_at": meta.get("updated_at", ""),
                        "needs_reenroll": needs_reenroll,
                        "history_count": len(profile_data.get("history", [])),
                    })
                except Exception as exc:
                    logger.warning("Error reading profile %s: %s", path, exc)

            return profiles

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full profile data including anchor and history embeddings."""
        with self._lock:
            path = self._file_path(profile_id)
            if not path.is_file():
                return None
            return self._load_file(path)

    def get_user_profile(self, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve the voice profile bound to the specified (or current) GNOME session user."""
        target_user = (username or get_gnome_session_user()).strip()
        target_uid = get_gnome_session_uid() if username is None else None
        with self._lock:
            safe_id = sanitize_profile_id(target_user)
            direct_prof = self.get_profile(safe_id)
            if direct_prof:
                return direct_prof

            # Fallback search by metadata
            if self._dir.is_dir():
                for path in sorted(self._dir.glob("*.npz")):
                    prof = self._load_file(path)
                    if prof:
                        meta = prof.get("meta", {})
                        p_user = meta.get("username") or meta.get("gnome_session_user")
                        p_uid = meta.get("uid")
                        if p_user == target_user or (target_uid is not None and p_uid == target_uid):
                            return prof
            return None

    def get_current_user_profile(self) -> Optional[Dict[str, Any]]:
        """Convenience helper to retrieve the voice profile of the active GNOME session user."""
        return self.get_user_profile(get_gnome_session_user())

    def _load_file(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with np.load(path, allow_pickle=False) as data:
                anchor = data["anchor"].astype(np.float32)
                history = data["history"].astype(np.float32)
                meta_raw = str(data["meta"])
                meta = json.loads(meta_raw) if meta_raw else {}
                return {
                    "id": path.stem,
                    "anchor": anchor,
                    "history": history,
                    "meta": meta,
                }
        except Exception as exc:
            logger.error("Failed to load profile from %s: %s", path, exc)
            return None

    def save_enrollment(
        self,
        display_name: str,
        anchor: np.ndarray,
        backend_name: Optional[str] = None,
        username: Optional[str] = None,
        uid: Optional[int] = None,
        profile_id: Optional[str] = None,
    ) -> str:
        """Create or overwrite a profile strictly bound to the GNOME session user."""
        backend = backend_name or self.active_backend
        session_user = (username or get_gnome_session_user()).strip()
        session_uid = uid if uid is not None else get_gnome_session_uid()
        session_disp = (display_name or "").strip() or get_gnome_session_display_name()

        if not profile_id:
            profile_id = sanitize_profile_id(display_name if display_name else session_user)

        anchor_vec = np.asarray(anchor, dtype=np.float32).flatten()
        norm = np.linalg.norm(anchor_vec)
        if norm > 1e-9:
            anchor_vec = anchor_vec / norm

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        meta = {
            "display_name": session_disp,
            "username": session_user,
            "uid": session_uid,
            "gnome_session_user": session_user,
            "created_at": now_iso,
            "updated_at": now_iso,
            "backend": backend,
            "embedding_dim": int(anchor_vec.shape[0]),
        }
        empty_history = np.empty((0, anchor_vec.shape[0]), dtype=np.float32)

        with self._lock:
            self._save_atomic(self._file_path(profile_id), anchor_vec, empty_history, meta)
            self.invalidate_cache(profile_id)
            logger.info(
                "Saved speaker enrollment for '%s' (id: %s, gnome_user: %s, uid: %s)",
                session_disp, profile_id, session_user, session_uid
            )
            return profile_id

    def delete_profile(self, profile_id: str) -> bool:
        """Delete a profile file safely."""
        with self._lock:
            path = self._file_path(profile_id)
            if not path.is_file():
                return False
            try:
                path.unlink()
                self.invalidate_cache(profile_id)
                logger.info("Deleted speaker profile: %s", profile_id)
                return True
            except OSError as exc:
                logger.error("Failed to delete speaker profile %s: %s", profile_id, exc)
                return False

    def add_history(self, profile_id: str, embedding: np.ndarray) -> bool:
        """Append an embedding to profile history, trimming to max_history."""
        emb = np.asarray(embedding, dtype=np.float32).flatten()
        norm = np.linalg.norm(emb)
        if norm > 1e-9:
            emb = emb / norm

        with self._lock:
            path = self._file_path(profile_id)
            if not path.is_file():
                return False

            profile = self._load_file(path)
            if profile is None:
                return False

            history = profile["history"]
            if history.size == 0:
                history = emb.reshape(1, -1)
            else:
                history = np.vstack([history, emb.reshape(1, -1)])

            if len(history) > self.max_history:
                history = history[-self.max_history:]

            meta = profile.get("meta", {})
            meta["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            self._save_atomic(path, profile["anchor"], history, meta)
            self.invalidate_cache(profile_id)
            return True

    def reference_embedding(self, profile_id: str) -> Optional[np.ndarray]:
        """Compute the weighted reference embedding combining anchor and history, with mtime caching."""
        with self._lock:
            path = self._file_path(profile_id)
            if not path.is_file():
                self.invalidate_cache(profile_id)
                return None

            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0

            safe_id = sanitize_profile_id(profile_id)
            cached = self._ref_cache.get(safe_id)
            if cached is not None and cached[0] == mtime:
                return cached[1]

            profile = self.get_profile(profile_id)
            if profile is None:
                return None
            ref = self.compute_reference_embedding(profile["anchor"], profile["history"])
            self._ref_cache[safe_id] = (mtime, ref, profile.get("meta", {}))
            return ref

    def get_all_cached_references(self) -> List[Tuple[str, str, np.ndarray]]:
        """Return list of (profile_id, display_name, ref_emb) using mtime cache without reloading unchanged files."""
        with self._lock:
            results = []
            if not self._dir.is_dir():
                return results
            for path in sorted(self._dir.glob("*.npz")):
                pid = path.stem
                ref = self.reference_embedding(pid)
                if ref is not None:
                    safe_id = sanitize_profile_id(pid)
                    cached = self._ref_cache.get(safe_id)
                    meta = cached[2] if cached else {}
                    disp = meta.get("display_name", pid)
                    results.append((pid, disp, ref))
            return results

    def get_reference_embedding(self, profile_id: str) -> Optional[np.ndarray]:
        """Alias for reference_embedding."""
        return self.reference_embedding(profile_id)

    @staticmethod
    def compute_reference_embedding(anchor: np.ndarray, history: np.ndarray) -> np.ndarray:
        """Linear weighted average of history combined 50/50 with anchor."""
        anchor_vec = np.asarray(anchor, dtype=np.float32).flatten()
        a_norm = np.linalg.norm(anchor_vec)
        if a_norm > 1e-9:
            anchor_vec = anchor_vec / a_norm

        if history is None or len(history) == 0:
            return anchor_vec

        hist_arr = np.asarray(history, dtype=np.float32)
        if hist_arr.ndim == 1:
            hist_arr = hist_arr.reshape(1, -1)

        weights = np.linspace(0.4, 1.0, len(hist_arr))
        weights_sum = weights.sum()
        if weights_sum <= 0:
            history_mean = np.mean(hist_arr, axis=0)
        else:
            history_mean = np.average(hist_arr, axis=0, weights=weights / weights_sum)

        h_norm = np.linalg.norm(history_mean)
        if h_norm < 1e-9:
            return anchor_vec

        combined = (0.5 * anchor_vec) + (0.5 * (history_mean / h_norm))
        c_norm = np.linalg.norm(combined)
        if c_norm < 1e-9:
            return anchor_vec
        return (combined / c_norm).astype(np.float32)

    def _save_atomic(
        self,
        dest_path: Path,
        anchor: np.ndarray,
        history: np.ndarray,
        meta: Dict[str, Any],
    ) -> None:
        """Write arrays and metadata atomically to dest_path."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        meta_json = json.dumps(meta)

        temp_file = tempfile.NamedTemporaryFile(
            prefix=".profile-",
            suffix=".npz",
            dir=str(dest_path.parent),
            delete=False,
        )
        temp_path = Path(temp_file.name)
        temp_file.close()

        try:
            np.savez(temp_path, anchor=anchor, history=history, meta=meta_json)
            os.replace(temp_path, dest_path)
        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise
