"""Decision policy evaluation for speaker gating and context injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

DEFAULT_CONTEXT_TEMPLATE = "Parlante identificato: {name}."

STOP_WORDS = ("stop", "basta", "zitto", "fermati", "silenzio", "interrompi")
_STOP_FILLERS = {"ok", "ehi", "dai", "per", "favore", "ora", "subito", "adesso"}


def is_pure_stop_command(text: str, wakeword_variants: Iterable[str] = ()) -> bool:
    words = [w.strip(".,!?;:") for w in (text or "").lower().split()]
    ww_set = {v.lower().strip() for v in wakeword_variants}
    words = [w for w in words if w and w not in _STOP_FILLERS and w not in ww_set]
    return bool(words) and len(words) <= 2 and all(w in STOP_WORDS for w in words)



@dataclass
class SpeakerDecision:
    """Outcome of speaker verification and policy evaluation."""

    allow: bool
    reason: str
    speaker_name: Optional[str] = None
    llm_context: str = ""


class SpeakerPolicy:
    """Evaluates speaker verification decisions based on operating mode."""

    MODES = ("disabled", "informative", "gate")

    @staticmethod
    def evaluate(
        mode: str,
        verdict: Optional[Any],
        *,
        is_voice: bool,
        is_stop_command: bool,
        tool_name: Optional[str] = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
    ) -> SpeakerDecision:
        """Pure decision function matching the specification in docs/speaker-identification-plan.md §4.5."""
        normalized_mode = (mode or "disabled").strip().lower()

        # 1. Feature disabled
        if normalized_mode not in ("informative", "gate"):
            return SpeakerDecision(allow=True, reason="disabled", speaker_name=None, llm_context="")

        # 2. Typed input (non-voice) always allowed without speaker context
        if not is_voice:
            return SpeakerDecision(allow=True, reason="typed_input", speaker_name=None, llm_context="")

        # 3. Stop / Barge-in commands always allowed (D6)
        if is_stop_command:
            speaker_name = None
            if verdict and getattr(verdict, "match", None):
                speaker_name = verdict.match.display_name
            return SpeakerDecision(allow=True, reason="stop_command", speaker_name=speaker_name, llm_context="")

        # 4. Handle missing verdict
        if verdict is None:
            if normalized_mode == "informative":
                return SpeakerDecision(allow=True, reason="unavailable", speaker_name=None, llm_context="")
            # In gate mode, missing verdict is fail-closed
            return SpeakerDecision(allow=False, reason="unavailable", speaker_name=None, llm_context="")

        match = getattr(verdict, "match", None)
        status = getattr(match, "status", "unknown") if match else "unknown"
        speaker_name = getattr(match, "display_name", None) if match else None
        timed_out = getattr(verdict, "timed_out", False)
        overlap_detected = getattr(verdict, "overlap_detected", False)

        # 5. Informative mode (never gates)
        if normalized_mode == "informative":
            if status == "recognized" and speaker_name:
                ctx = context_template.format(name=speaker_name)
                return SpeakerDecision(allow=True, reason="recognized", speaker_name=speaker_name, llm_context=ctx)
            return SpeakerDecision(allow=True, reason=status, speaker_name=speaker_name, llm_context="")

        # 6. Gate mode
        if timed_out:
            return SpeakerDecision(allow=False, reason="timeout", speaker_name=None, llm_context="")

        if status == "recognized":
            if overlap_detected:
                return SpeakerDecision(allow=False, reason="overlap", speaker_name=speaker_name, llm_context="")
            ctx = context_template.format(name=speaker_name) if speaker_name else ""
            return SpeakerDecision(allow=True, reason="recognized", speaker_name=speaker_name, llm_context=ctx)

        # Fail-closed for all other non-recognized states
        reason = status if status in ("unknown", "insufficient_audio", "no_profiles", "unavailable") else "unknown"
        return SpeakerDecision(allow=False, reason=reason, speaker_name=None, llm_context="")
