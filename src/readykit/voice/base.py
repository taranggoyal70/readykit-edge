"""The Transcriber interface.

A Transcriber turns audio into an `Utterance`. It never returns an Intent, and
it certainly never returns a Command - deciding what a phrase is allowed to
mean is `voice.intent`'s job alone, exactly as deciding a Verdict is
`resolve_verdict`'s job alone. The split is the same one the rest of the
system uses, for the same reason: the safety rule lives in one place and every
backend is held to it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Utterance:
    """One thing the operator said, as the transcriber heard it.

    An observation, never a decision - the voice counterpart of a Sighting.
    """

    text: str
    """Verbatim, as transcribed. Recorded alongside whatever was done with it,
    because "the operator asked for this" is a weak claim unless you can show
    the words."""

    confidence: float = 0.0
    """Lowest word-level confidence in the utterance, not the mean.

    A mean hides the one word that matters. "Read the last five inspections"
    heard at 0.95 average but with `five` at 0.2 is not a request anyone
    established, and averaging would have concealed exactly the token that was
    unclear.
    """

    is_final: bool = True
    """False for a partial hypothesis. Partials are shown, never acted on -
    they are revised by design, and acting on a phrase the transcriber is
    still changing its mind about is acting on something nobody said yet.
    """


class TranscriptionError(RuntimeError):
    """Audio could not be turned into an utterance.

    Never recoverable by guessing. The microphone died, the container refused
    the connection, the stream closed mid-phrase - all of them mean nothing
    was established, and nothing established is not a command.
    """


class Transcriber(ABC):
    """Turns audio into Utterances."""

    name: str = "unknown"

    air_gapped: bool = True
    """Whether audio stays on this device.

    False means audio is leaving for a third party, and the console and the
    CLI both say so on screen. This is a field rather than a comment because
    a claim that the system is offline has to be answerable by the code that
    would be breaking it.
    """

    @abstractmethod
    def listen(self) -> list[Utterance]:
        """Return the utterances heard since the last call.

        An empty list is legitimate and distinct from raising: it means the
        transcriber ran and heard nothing. Both do nothing.
        """

    def close(self) -> None:  # noqa: B027 - optional hook, the simulator holds nothing
        """Release the socket or the capture handle."""

    def __enter__(self) -> Transcriber:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
