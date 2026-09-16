"""Voice operation.

The operator has both hands in a tray and is probably wearing gloves, so voice
is the correct interface here rather than decoration. It is also the interface
with the most direct path to a catastrophic mistake, so the rule is narrow and
absolute: **voice may ask this system questions and may start or stop it
looking. It may not open the latch.** See `voice.intent` for why that is
structural rather than a check.
"""

from .base import Transcriber, TranscriptionError, Utterance
from .intent import DEFAULT_FLOOR, Intent, Recognition, resolve_intent
from .simulated import SimulatedTranscriber

__all__ = [
    "DEFAULT_FLOOR",
    "Intent",
    "Recognition",
    "SimulatedTranscriber",
    "Transcriber",
    "TranscriptionError",
    "Utterance",
    "load_transcriber",
    "resolve_intent",
]


def load_transcriber(name: str, **kwargs: object) -> Transcriber:
    """Resolve a transcriber by name.

    Speechmatics is imported lazily, exactly as GenieX is: it needs a
    websocket dependency and a running container, and the simulated path must
    work without either.
    """
    if name == "simulated":
        return SimulatedTranscriber(**kwargs)  # type: ignore[arg-type]
    if name == "speechmatics":
        from .speechmatics import SpeechmaticsTranscriber

        return SpeechmaticsTranscriber(**kwargs)  # type: ignore[arg-type]
    raise TranscriptionError(
        f"unknown transcriber {name!r}; expected 'speechmatics' or 'simulated'"
    )
