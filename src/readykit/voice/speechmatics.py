"""Speechmatics real-time transcription, over the documented v2 protocol.

This is written against the real Speechmatics RT WebSocket API, not an
invented one:

    ws  = connect(url, extra_headers={"Authorization": f"Bearer {key}"})
    ws.send(json({"message": "StartRecognition",
                  "audio_format": {"type": "raw",
                                   "encoding": "pcm_s16le",
                                   "sample_rate": 16000},
                  "transcription_config": {"language": "en",
                                           "enable_partials": True,
                                           "max_delay": 0.7}}))
    ws.send(pcm_bytes)                      # AddAudio is a binary frame
    ...  {"message": "AddTranscript", "metadata": {...}, "results": [...]}

## The air gap is the whole reason this module is shaped like this

Speechmatics ships **CPU and GPU containers you run yourself**, and that is
the deployment this project is built for: the container sits on loopback, the
audio never leaves the device, and the offline claim survives intact. So the
default `url` is a local container, and `air_gapped` is True.

The cloud endpoints work too, and the class does not stop you using them. What
it will not do is let you use them quietly. Pointing this at
`*.rt.speechmatics.com` sets `air_gapped = False`, which the CLI and the
console both surface on screen, and which is written into every Inspection
Record made while it is running. A system that says "fully offline" on a
banner while streaming a medic's voice to a third party is telling the one lie
this project exists to argue against.

## Confidence is the minimum, not the mean

Speechmatics reports confidence per word. This takes the lowest word in the
utterance, because a mean hides the single token that was unclear - and the
single token is routinely the one that matters, as in "read the last *five*
inspections".
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from .base import Transcriber, TranscriptionError, Utterance

DEFAULT_URL = "ws://127.0.0.1:9000/v2"
"""The on-prem real-time container, on loopback. Air-gapped by construction:
nothing addressed here leaves the machine."""

CLOUD_HOSTS = ("rt.speechmatics.com",)
"""Recognised cloud endpoints. Matching one of these flips `air_gapped` to
False - the flag is derived from the URL rather than passed in, so it cannot
disagree with where the audio is actually going."""

DEFAULT_SAMPLE_RATE = 16000
API_KEY_ENV = "SPEECHMATICS_API_KEY"


def _is_air_gapped(url: str) -> bool:
    """Whether audio sent to this URL stays on the device.

    Loopback and a bare hostname are local; anything resolving to a known
    cloud host is not. Unrecognised remote hosts are treated as **not**
    air-gapped, because the safe reading of an unknown destination is that it
    is somewhere else.
    """
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        return True
    if any(host.endswith(cloud) for cloud in CLOUD_HOSTS):
        return False
    return False


class SpeechmaticsTranscriber(Transcriber):
    """Streams microphone audio to a Speechmatics RT endpoint."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        language: str = "en",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        max_delay: float = 0.7,
        api_key: str | None = None,
        connect: Any | None = None,
    ) -> None:
        self.url = url
        self.language = language
        self.sample_rate = sample_rate
        self.max_delay = max_delay
        self.air_gapped = _is_air_gapped(url)

        # A cloud endpoint needs a key; a local container does not. Read from
        # the environment, never from an argument default, and never written
        # to a record.
        self.api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self.air_gapped and not self.api_key:
            raise TranscriptionError(
                f"{url} is a cloud endpoint and no API key was given. "
                f"Set {API_KEY_ENV}, or point --voice-url at a local "
                f"Speechmatics container to keep the device air-gapped."
            )

        # Recorded on every inspection. Unambiguous on purpose, in the same
        # way `ollama:<model>` is: nobody reading a record should have to
        # work out whether the audio left the building.
        self.name = f"speechmatics:{'local' if self.air_gapped else 'cloud'}"

        self._connect = connect or self._default_connect
        self._socket: Any | None = None

    # -- transport -----------------------------------------------------------

    @staticmethod
    def _default_connect(url: str, headers: dict[str, str]) -> Any:
        """Open the websocket. Isolated so tests can substitute a fake.

        `websockets` is an optional dependency, in the `voice` extra. It is
        imported here rather than at module scope so that importing
        `readykit.voice` works on a machine that will only ever use the
        simulated transcriber - the same treatment `geniex` gets.
        """
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise TranscriptionError(
                "the speechmatics transcriber needs the websockets package: "
                'pip install -e ".[voice]"'
            ) from exc
        return connect(url, additional_headers=headers)

    def _open(self) -> Any:
        if self._socket is not None:
            return self._socket

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            socket = self._connect(self.url, headers)
            socket.send(
                json.dumps(
                    {
                        "message": "StartRecognition",
                        "audio_format": {
                            "type": "raw",
                            "encoding": "pcm_s16le",
                            "sample_rate": self.sample_rate,
                        },
                        "transcription_config": {
                            "language": self.language,
                            "enable_partials": True,
                            "max_delay": self.max_delay,
                        },
                    }
                )
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(
                f"could not start recognition on {self.url}: {exc}"
            ) from exc

        self._socket = socket
        return socket

    # -- listening -----------------------------------------------------------

    def send_audio(self, pcm: bytes) -> None:
        """Push one chunk of 16-bit little-endian PCM. AddAudio is binary."""
        socket = self._open()
        try:
            socket.send(pcm)
        except Exception as exc:
            raise TranscriptionError(f"audio send failed: {exc}") from exc

    def listen(self) -> list[Utterance]:
        """Drain whatever the server has ready, as Utterances.

        Partials are returned with `is_final=False` rather than dropped, so a
        console can show the operator they are being heard while `resolve_intent`
        still refuses to act on them.
        """
        socket = self._open()
        utterances: list[Utterance] = []

        while True:
            try:
                raw = socket.recv(timeout=0)
            except TimeoutError:
                break
            except Exception as exc:
                raise TranscriptionError(f"transcript stream failed: {exc}") from exc

            if raw is None:
                break
            message = self._decode(raw)
            if message is None:
                continue

            kind = message.get("message")
            if kind == "Error":
                raise TranscriptionError(
                    f"speechmatics error {message.get('type')}: {message.get('reason')}"
                )
            if kind in ("AddTranscript", "AddPartialTranscript"):
                utterance = self._to_utterance(message, is_final=kind == "AddTranscript")
                if utterance is not None:
                    utterances.append(utterance)
            if kind == "EndOfTranscript":
                break

        return utterances

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any] | None:
        """Parse one server frame. Unparseable frames are skipped, not guessed."""
        if isinstance(raw, bytes):
            return None
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _to_utterance(message: dict[str, Any], *, is_final: bool) -> Utterance | None:
        """Build an Utterance from AddTranscript, or None if it carried nothing.

        Confidence is the minimum across words. An utterance whose words carry
        no confidence at all scores 0.0, which is below every floor - an
        unscored transcript is not evidence that it was heard well.
        """
        text = str(message.get("transcript") or "").strip()
        results = message.get("results") or []

        confidences: list[float] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            alternatives = result.get("alternatives") or []
            if alternatives and isinstance(alternatives[0], dict):
                value = alternatives[0].get("confidence")
                if isinstance(value, int | float):
                    confidences.append(float(value))
                if not text:
                    text = f"{text} {alternatives[0].get('content', '')}".strip()

        if not text:
            return None

        return Utterance(
            text=text,
            confidence=min(confidences) if confidences else 0.0,
            is_final=is_final,
        )

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
