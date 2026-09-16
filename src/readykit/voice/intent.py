"""What a spoken phrase is allowed to mean.

Pure: no I/O, no audio, no sockets. Given an `Utterance`, decide which of a
fixed, small set of intents it expresses, or decide that it expresses none.

## Voice cannot open the latch

Not "is checked so that it does not". *Cannot*. There is no member of `Intent`
that releases the Latch, so there is no value this module can return that the
caller could turn into a RELEASE. The refusal is structural rather than a
guard, because a guard is a line of code someone can delete in a hurry and a
missing enum member is not.

`OVERRIDE` exists precisely so that asking is recognised and recorded. An
override attempt that merely failed to parse would be indistinguishable from
silence, and "somebody stood at this cabinet and told it to open" is exactly
the thing an auditor should find in the record.

## Why keyword matching is defensible here and was not in `naive.py`

`readykit.naive` is kept in this repo as an example of how *not* to decide, so
matching on keywords at all deserves an explicit defence.

The blueprint's bug was not that it matched substrings. It was the direction
its failure fell: an unrecognised reply produced `PASS_KIT`, so every gap in
the grammar became an unlock. Absence of a trigger token meant compliance.

Here, an unrecognised utterance produces `UNRESOLVED`, which does nothing at
all. Every gap in this grammar is silence. The grammar can be as incomplete as
you like and the worst outcome is that the operator repeats themselves.

The two directions are therefore deliberately asymmetric:

* **Commands match narrowly.** A phrase must match a known form. Anything else
  is `UNRESOLVED` and nothing happens.
* **Override attempts match broadly.** Erring toward "that was an override
  attempt" costs a refusal and a log line, which is harmless. Erring the other
  way loses the record of someone trying.

That asymmetry is the same shape as the Sentinel's: slow to grant, quick to
withhold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .base import Utterance

DEFAULT_FLOOR = 0.6
"""Confidence a final utterance must clear before it can mean anything.

The Manifest's `confidence_floor` governs what the *model* saw and is a
separate number on purpose: how sure we are about a tourniquet and how sure we
are about a spoken word are unrelated quantities, and one floor serving both
would be a coincidence rather than a design.
"""


class Intent(StrEnum):
    """Everything the operator is permitted to express by voice.

    Read this list as the security boundary it is. Adding a member that
    actuates anything would move this project's safety argument, so it is not
    a change to make quietly.
    """

    STATUS = "status"
    """"What's wrong", "how does it look". Reads the last Verdict aloud."""

    EXPLAIN = "explain"
    """"Why did it fail". Reads the reason, not a new judgement."""

    READ_RECORDS = "read_records"
    """"Read the last five inspections". Reads from the audit log."""

    SELECT_MANIFEST = "select_manifest"
    """"Switch to the electrical toolbox". Changes what is being judged
    against - which can only ever make the check stricter or different, never
    release anything by itself."""

    START_WATCH = "start_watch"
    """Begin inspecting on a rolling interval.

    This is the one intent with any path at all to an open latch, so it is
    worth being exact about it: it starts the camera looking. Whether the
    Latch then moves is decided by `resolve_verdict` from what the model sees,
    exactly as it is when a keyboard starts the loop. Voice supplies the
    occasion, never the evidence, and never the decision.
    """

    STOP_WATCH = "stop_watch"
    """Stop inspecting. Falls toward the safe resting state, so it needs no
    confirmation and no confidence argument."""

    OVERRIDE = "override"
    """Somebody asked it to open. Always refused, always recorded."""

    UNRESOLVED = "unresolved"
    """Nothing was established: too quiet, too unsure, or not a known phrase.

    The voice counterpart of INDETERMINATE, and it behaves the same way - it
    is not a command, and it is never a reason to act.
    """


_LOOKING = r"\b(watch|watching|inspect\w*|scan\w*)\b"

_COMMANDS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (Intent.STOP_WATCH, re.compile(rf"\b(stop|pause|halt)\b.*{_LOOKING}")),
    (Intent.START_WATCH, re.compile(rf"\b(start|begin|resume)\b.*{_LOOKING}")),
    (Intent.EXPLAIN, re.compile(r"\bwhy\b.*\b(fail|failed|locked|shut|holding|held)\b")),
    (
        Intent.READ_RECORDS,
        re.compile(r"\bread\b.*\b(record|records|inspection|inspections|log|history)\b"),
    ),
    (
        Intent.SELECT_MANIFEST,
        re.compile(r"\b(switch|change|select|use)\b.*\b(to|manifest|kit)\b"),
    ),
    (
        Intent.STATUS,
        re.compile(
            r"\b(status|what'?s wrong|what is wrong|how does it look|where are we)\b"
        ),
    ),
)

_OVERRIDE = re.compile(
    r"\b(overrid\w*|bypass\w*|ignore\b|force\b|unlock\w*|open\b|release\w*|"
    r"let me (in|through)|disable\b|skip\b|just open\b)"
)
"""Deliberately broad. Every false positive costs one spoken refusal; every
false negative loses the only record that somebody tried."""

_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


@dataclass(frozen=True, slots=True)
class Recognition:
    """What an utterance was taken to mean, and why.

    Carries the utterance verbatim so the Inspection Record can show the words
    beside the action, rather than only the conclusion drawn from them.
    """

    intent: Intent
    utterance: Utterance
    reason: str
    slot: str | int | None = None
    """The one argument an intent may carry: a manifest name, a record count."""

    @property
    def actionable(self) -> bool:
        """Whether the caller should do anything at all.

        Both `UNRESOLVED` and `OVERRIDE` are False. An override is *handled* -
        refused aloud and written to the record - but it is not carried out,
        and collapsing "refuse this" into "do this" is the mistake this whole
        property exists to make hard to write.
        """
        return self.intent not in (Intent.UNRESOLVED, Intent.OVERRIDE)


def resolve_intent(utterance: Utterance, floor: float = DEFAULT_FLOOR) -> Recognition:
    """Decide what an utterance means. Never raises.

    Order matters: an override attempt is recognised *before* the confidence
    floor is applied. A mumbled "just unlock it" is still somebody asking, and
    a refusal that only fires on clearly-enunciated attempts would be a
    refusal that stops nobody.
    """
    text = utterance.text.strip().lower()

    if not text:
        return Recognition(
            Intent.UNRESOLVED, utterance, "nothing was said"
        )

    # Partials are revised by design. Showing one is fine; acting on one is
    # acting on a sentence the transcriber has not finished changing.
    if not utterance.is_final:
        return Recognition(
            Intent.UNRESOLVED, utterance, "partial hypothesis, not yet settled"
        )

    if _OVERRIDE.search(text):
        return Recognition(
            Intent.OVERRIDE,
            utterance,
            "the latch is not voice-operable; refused and recorded",
        )

    if utterance.confidence < floor:
        return Recognition(
            Intent.UNRESOLVED,
            utterance,
            f"heard at {utterance.confidence:.2f}, below the floor of {floor:.2f}",
        )

    for intent, pattern in _COMMANDS:
        if pattern.search(text):
            return Recognition(
                intent, utterance, "matched a known phrase", _slot_for(intent, text)
            )

    return Recognition(Intent.UNRESOLVED, utterance, "not a phrase this understands")


def _slot_for(intent: Intent, text: str) -> str | int | None:
    """Pull the single argument an intent carries, or None.

    None is not a default. For READ_RECORDS it means no count was established,
    and the caller is expected to treat that as unresolved rather than
    substituting a number of its own - the same rule the Manifest applies to a
    quantity the model could not count.
    """
    if intent is Intent.READ_RECORDS:
        digits = re.search(r"\b(\d+)\b", text)
        if digits:
            return int(digits.group(1))
        for word, value in _COUNT_WORDS.items():
            if re.search(rf"\b{word}\b", text):
                return value
        return None

    if intent is Intent.SELECT_MANIFEST:
        tail = re.search(r"\b(?:to|manifest|kit)\b\s+(?:the\s+)?([a-z0-9 _-]+)", text)
        if tail:
            return tail.group(1).strip().replace(" ", "-") or None
        return None

    return None
