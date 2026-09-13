"""Speaker Identification service package."""

from .backend import SpeakerEmbeddingBackend, create_backend
from .enrollment import EnrollmentRecorder
from .identifier import SpeakerIdentifier, SpeakerMatch
from .policy import SpeakerDecision, SpeakerPolicy
from .profiles import (
    SpeakerProfileStore,
    get_gnome_session_display_name,
    get_gnome_session_uid,
    get_gnome_session_user,
    sanitize_profile_id,
)
from .session import PreRollBuffer, SpeakerVerdict, VoiceSpeakerSession

__all__ = [
    "SpeakerEmbeddingBackend",
    "create_backend",
    "EnrollmentRecorder",
    "SpeakerIdentifier",
    "SpeakerMatch",
    "SpeakerPolicy",
    "SpeakerDecision",
    "SpeakerProfileStore",
    "sanitize_profile_id",
    "get_gnome_session_user",
    "get_gnome_session_uid",
    "get_gnome_session_display_name",
    "VoiceSpeakerSession",
    "PreRollBuffer",
    "SpeakerVerdict",
]
