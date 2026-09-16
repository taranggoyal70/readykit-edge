"""A transcriber that reads from a script rather than from a microphone.

Exists for the same reason `inference.simulated` does: the whole voice path -
including the refusal, including the confidence floor - has to be testable on
a machine with no microphone, no container and no API key.

It is honest about what it is. `name` is `simulated`, which is what lands in
the Inspection Record, so nobody reading one can mistake a scripted phrase for
something a person said.
"""

from __future__ import annotations

from .base import Transcriber, Utterance

SCRIPT: tuple[tuple[str, float], ...] = (
    ("start watching", 0.94),
    ("what's wrong", 0.91),
    ("why did it fail", 0.93),
    ("read the last five inspections", 0.9),
    ("switch to the electrical toolbox kit", 0.88),
    ("just unlock it", 0.97),          # refused, loudly and on the record
    ("mmh brr unlock", 0.21),          # refused: an override is caught below the floor
    ("open the cabinet for me", 0.95), # refused
    ("what's the weather", 0.95),      # unresolved: not a phrase this understands
    ("staaart waaatching", 0.32),      # unresolved: below the floor
    ("stop watching", 0.96),
)
"""Ordered to walk the whole decision tree: every intent, both refusal paths,
and both ways of reaching UNRESOLVED."""


class SimulatedTranscriber(Transcriber):
    name = "simulated"
    air_gapped = True

    def __init__(self, script: tuple[tuple[str, float], ...] | None = None) -> None:
        self._script = list(script if script is not None else SCRIPT)
        self._at = 0

    def listen(self) -> list[Utterance]:
        """Return the next scripted utterance, or nothing once exhausted."""
        if self._at >= len(self._script):
            return []
        text, confidence = self._script[self._at]
        self._at += 1
        return [Utterance(text=text, confidence=confidence, is_final=True)]
