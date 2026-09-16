"""Voice operation, and the one thing it must never be able to do.

The bulk of this file is about refusal. That is deliberate: the interesting
claim is not that voice works, it is that voice cannot open a latch.
"""

from __future__ import annotations

import json

import pytest

from readykit.voice import (
    Intent,
    SimulatedTranscriber,
    TranscriptionError,
    Utterance,
    load_transcriber,
    resolve_intent,
)
from readykit.voice.intent import DEFAULT_FLOOR
from readykit.voice.speechmatics import (
    DEFAULT_URL,
    SpeechmaticsTranscriber,
    _is_air_gapped,
)


def said(text: str, confidence: float = 0.95, is_final: bool = True) -> Utterance:
    return Utterance(text=text, confidence=confidence, is_final=is_final)


# -- the structural guarantee ------------------------------------------------


def test_no_intent_can_release_the_latch() -> None:
    """The security boundary, asserted as a boundary.

    If someone adds an actuating member to Intent, this fails - which is the
    point. The safety argument says voice cannot open the latch; that claim
    should break loudly rather than quietly becoming untrue.
    """
    forbidden = ("release", "unlock", "open", "actuate", "grant")
    for member in Intent:
        assert not any(word in member.value for word in forbidden), member


def test_every_intent_is_reachable_or_refusing() -> None:
    """No dead members: each is produced by some phrase, or exists to refuse."""
    produced = {
        resolve_intent(said(text)).intent
        for text in (
            "start watching",
            "stop watching",
            "what's wrong",
            "why did it fail",
            "read the last five inspections",
            "switch to the electrical toolbox kit",
            "just unlock it",
            "what's the weather",
        )
    }
    assert produced == set(Intent)


# -- refusal -----------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "unlock it",
        "just open the cabinet",
        "override the check",
        "bypass this",
        "ignore the expiry",
        "force it open",
        "release the latch",
        "let me in",
        "disable the lock",
        "skip the tourniquet",
    ],
)
def test_override_attempts_are_refused(phrase: str) -> None:
    recognition = resolve_intent(said(phrase))
    assert recognition.intent is Intent.OVERRIDE
    assert not recognition.actionable


def test_an_override_is_refused_even_when_barely_heard() -> None:
    """Order matters: override detection runs before the confidence floor.

    A refusal that only fires on well-enunciated attempts is a refusal that
    stops nobody, and it loses the record of the mumbled ones.
    """
    recognition = resolve_intent(said("mmh just unlock it", confidence=0.05))
    assert recognition.intent is Intent.OVERRIDE
    assert not recognition.actionable


def test_an_override_keeps_the_verbatim_words() -> None:
    """An auditor should find the words, not just the conclusion."""
    recognition = resolve_intent(said("override it, I'm in a hurry"))
    assert recognition.utterance.text == "override it, I'm in a hurry"
    assert "recorded" in recognition.reason


def test_override_is_not_actionable_but_is_distinct_from_unresolved() -> None:
    """Both do nothing; only one means somebody tried."""
    override = resolve_intent(said("unlock"))
    nothing = resolve_intent(said("what's the weather"))
    assert not override.actionable and not nothing.actionable
    assert override.intent is not nothing.intent


# -- the floor ---------------------------------------------------------------


def test_low_confidence_is_never_a_command() -> None:
    recognition = resolve_intent(said("start watching", confidence=DEFAULT_FLOOR - 0.01))
    assert recognition.intent is Intent.UNRESOLVED
    assert not recognition.actionable


def test_at_the_floor_exactly_is_allowed() -> None:
    recognition = resolve_intent(said("start watching", confidence=DEFAULT_FLOOR))
    assert recognition.intent is Intent.START_WATCH


def test_partials_are_never_acted_on() -> None:
    """A partial is a sentence the transcriber has not finished revising."""
    recognition = resolve_intent(said("start watching", is_final=False))
    assert recognition.intent is Intent.UNRESOLVED


def test_silence_is_unresolved() -> None:
    assert resolve_intent(said("   ")).intent is Intent.UNRESOLVED


def test_an_unknown_phrase_does_nothing() -> None:
    """Every gap in this grammar is silence, never a command.

    This is the property that makes keyword matching defensible here when it
    was catastrophic in naive.py - the failure falls the other way.
    """
    for phrase in ("what's the weather", "hello there", "abcdef", "kit"):
        assert resolve_intent(said(phrase)).intent is Intent.UNRESOLVED


# -- slots -------------------------------------------------------------------


def test_record_count_is_read_as_a_digit_or_a_word() -> None:
    assert resolve_intent(said("read the last 5 inspections")).slot == 5
    assert resolve_intent(said("read the last five inspections")).slot == 5


def test_an_uncounted_record_request_leaves_the_slot_empty() -> None:
    """No default. A count never established is not a count of one.

    The same rule the Manifest applies to a quantity the model could not see.
    """
    recognition = resolve_intent(said("read the inspection log"))
    assert recognition.intent is Intent.READ_RECORDS
    assert recognition.slot is None


def test_manifest_selection_carries_the_name() -> None:
    recognition = resolve_intent(said("switch to the electrical toolbox"))
    assert recognition.intent is Intent.SELECT_MANIFEST
    assert recognition.slot == "electrical-toolbox"


# -- the air gap -------------------------------------------------------------


def test_the_default_endpoint_is_a_local_container() -> None:
    assert _is_air_gapped(DEFAULT_URL)
    assert SpeechmaticsTranscriber().air_gapped


@pytest.mark.parametrize(
    "url,expected",
    [
        ("ws://127.0.0.1:9000/v2", True),
        ("ws://localhost:9000/v2", True),
        ("ws://[::1]:9000/v2", True),
        ("wss://eu.rt.speechmatics.com/v2", False),
        ("wss://global.rt.speechmatics.com/v2", False),
        ("wss://something.else.example.com/v2", False),
    ],
)
def test_air_gap_is_derived_from_the_url(url: str, expected: bool) -> None:
    """Derived, never passed in, so the flag cannot disagree with reality."""
    assert _is_air_gapped(url) is expected


def test_an_unknown_remote_host_is_assumed_to_be_off_device() -> None:
    """The safe reading of an unrecognised destination is 'somewhere else'."""
    assert not _is_air_gapped("wss://unknown.example.org/v2")


def test_the_cloud_path_refuses_to_run_silently_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)
    with pytest.raises(TranscriptionError, match="air-gapped"):
        SpeechmaticsTranscriber(url="wss://eu.rt.speechmatics.com/v2")


def test_the_engine_name_says_where_the_audio_went() -> None:
    """Unambiguous in the record, the way ollama:<model> is."""
    assert SpeechmaticsTranscriber().name == "speechmatics:local"
    cloud = SpeechmaticsTranscriber(
        url="wss://eu.rt.speechmatics.com/v2", api_key="k"
    )
    assert cloud.name == "speechmatics:cloud"
    assert not cloud.air_gapped


# -- the wire protocol -------------------------------------------------------


class FakeSocket:
    """Stands in for a Speechmatics RT websocket."""

    def __init__(self, inbound: list[object]) -> None:
        self.sent: list[object] = []
        self._inbound = list(inbound)
        self.closed = False

    def send(self, payload: object) -> None:
        self.sent.append(payload)

    def recv(self, timeout: float | None = None) -> object:
        if not self._inbound:
            raise TimeoutError
        return self._inbound.pop(0)

    def close(self) -> None:
        self.closed = True


def transcript(words: list[tuple[str, float]], final: bool = True) -> str:
    return json.dumps(
        {
            "message": "AddTranscript" if final else "AddPartialTranscript",
            "transcript": " ".join(word for word, _ in words),
            "results": [
                {"type": "word", "alternatives": [{"content": w, "confidence": c}]}
                for w, c in words
            ],
        }
    )


def build(inbound: list[object]) -> tuple[SpeechmaticsTranscriber, FakeSocket]:
    socket = FakeSocket(inbound)
    engine = SpeechmaticsTranscriber(connect=lambda url, headers: socket)
    return engine, socket


def test_start_recognition_matches_the_documented_shape() -> None:
    engine, socket = build([])
    engine.listen()
    message = json.loads(str(socket.sent[0]))
    assert message["message"] == "StartRecognition"
    assert message["audio_format"] == {
        "type": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }
    assert message["transcription_config"]["language"] == "en"


def test_audio_is_sent_as_a_binary_frame() -> None:
    engine, socket = build([])
    engine.send_audio(b"\x00\x01")
    assert b"\x00\x01" in socket.sent


def test_confidence_is_the_weakest_word_not_the_average() -> None:
    """The unclear token is routinely the one that matters."""
    engine, _ = build([transcript([("read", 0.99), ("five", 0.2), ("records", 0.98)])])
    heard = engine.listen()
    assert heard[0].confidence == pytest.approx(0.2)


def test_a_weak_word_drags_the_whole_phrase_below_the_floor() -> None:
    """End to end: the averaging bug would have let this through."""
    engine, _ = build([transcript([("read", 0.99), ("five", 0.1), ("records", 0.99)])])
    recognition = resolve_intent(engine.listen()[0])
    assert recognition.intent is Intent.UNRESOLVED


def test_partials_are_surfaced_but_flagged() -> None:
    """Shown to the operator, refused by resolve_intent."""
    engine, _ = build([transcript([("start", 0.9), ("watching", 0.9)], final=False)])
    heard = engine.listen()
    assert heard[0].is_final is False
    assert resolve_intent(heard[0]).intent is Intent.UNRESOLVED


def test_an_unscored_transcript_scores_zero() -> None:
    """Absence of a confidence is not evidence of a confident hearing."""
    engine, _ = build(
        [json.dumps({"message": "AddTranscript", "transcript": "unlock", "results": []})]
    )
    assert engine.listen()[0].confidence == 0.0


def test_a_server_error_raises_rather_than_returning_nothing() -> None:
    """Silence and a failure are different, and only one is a dead microphone."""
    engine, _ = build(
        [json.dumps({"message": "Error", "type": "job_error", "reason": "nope"})]
    )
    with pytest.raises(TranscriptionError, match="job_error"):
        engine.listen()


def test_unparseable_frames_are_skipped_not_guessed_at() -> None:
    engine, _ = build(["not json at all", transcript([("stop", 0.9), ("watching", 0.9)])])
    heard = engine.listen()
    assert len(heard) == 1
    assert resolve_intent(heard[0]).intent is Intent.STOP_WATCH


def test_a_failed_connection_is_a_transcription_error() -> None:
    def explode(url: str, headers: dict[str, str]) -> object:
        raise OSError("no container here")

    engine = SpeechmaticsTranscriber(connect=explode)
    with pytest.raises(TranscriptionError, match="could not start recognition"):
        engine.listen()


def test_closing_releases_the_socket() -> None:
    engine, socket = build([])
    engine.listen()
    engine.close()
    assert socket.closed


# -- the loader and the simulator -------------------------------------------


def test_load_transcriber_resolves_the_simulator() -> None:
    assert isinstance(load_transcriber("simulated"), SimulatedTranscriber)


def test_an_unknown_transcriber_is_refused_by_name() -> None:
    with pytest.raises(TranscriptionError, match="unknown transcriber"):
        load_transcriber("whisper")


def test_the_script_walks_the_whole_decision_tree() -> None:
    """Every intent, both refusal paths, both routes to UNRESOLVED."""
    transcriber = SimulatedTranscriber()
    seen: list[Intent] = []
    while heard := transcriber.listen():
        seen.extend(resolve_intent(u).intent for u in heard)
    assert set(seen) == set(Intent)


def test_the_simulator_never_claims_to_be_a_person() -> None:
    assert SimulatedTranscriber().name == "simulated"
