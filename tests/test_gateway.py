"""The AI Gateway engine - the one path that leaves the device.

Most of this file is about that fact being impossible to hide.
"""

from __future__ import annotations

import base64
import json
import urllib.error

import pytest

from readykit.capture import Frame
from readykit.domain import Manifest, Verdict, resolve_verdict
from readykit.inference import load_engine
from readykit.inference.base import InferenceError
from readykit.inference.gateway import (
    DEFAULT_MODEL,
    GatewayEngine,
    chat_request,
)
from readykit.inference.simulated import SimulatedEngine

MANIFEST = Manifest.from_json(
    json.dumps(
        {
            "manifest_id": "t",
            "name": "T",
            "items": [
                {"key": "shears", "label": "Trauma Shears", "severity": "critical"},
                {"key": "gauze", "label": "Gauze", "severity": "critical"},
            ],
        }
    )
)

FRAME = Frame(image=b"\xff\xd8\xff\xe0jpegbytes", digest="abc", width=8, height=8)


def reply(text: str) -> object:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": text}}]}
            ).encode()

    return Response()


def engine(opener: object) -> GatewayEngine:
    return GatewayEngine(token="t", opener=opener)


GOOD = json.dumps(
    {
        "kit_present": "yes",
        "items": [
            {"key": "shears", "presence": "found", "confidence": 0.95},
            {"key": "gauze", "presence": "found", "confidence": 0.93},
        ],
    }
)


# -- it must be impossible to mistake this for the NPU -----------------------


def test_the_gateway_engine_is_not_air_gapped() -> None:
    assert GatewayEngine(token="t").air_gapped is False


def test_every_on_device_engine_still_claims_the_air_gap() -> None:
    """The flag only means something if the honest engines assert it too."""
    assert SimulatedEngine().air_gapped is True


def test_the_recorded_name_names_the_runtime() -> None:
    assert GatewayEngine(token="t").name == f"gateway:{DEFAULT_MODEL}"


def test_a_missing_credential_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not on the first frame. Finding out when somebody points a camera at
    the page is strictly worse than finding out at startup."""
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(InferenceError, match="no AI Gateway credential"):
        GatewayEngine()


def test_the_oidc_token_is_preferred_over_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment supplies OIDC, so no key has to exist or be rotated."""
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "oidc")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "key")
    sent: dict[str, object] = {}

    def opener(request: object, timeout: float = 0) -> object:
        sent["auth"] = request.headers["Authorization"]  # type: ignore[attr-defined]
        return reply(GOOD)

    GatewayEngine(opener=opener).infer(FRAME, MANIFEST)
    assert sent["auth"] == "Bearer oidc"


def test_the_token_travels_only_in_a_header() -> None:
    """Never in the body, never in a URL, so it cannot land in a log or a
    proxy's access record."""
    secret = "sk-ZZZdistinctiveZZZ"
    seen: dict[str, object] = {}

    def opener(request: object, timeout: float = 0) -> object:
        seen["url"] = request.full_url  # type: ignore[attr-defined]
        seen["body"] = request.data  # type: ignore[attr-defined]
        seen["auth"] = request.headers["Authorization"]  # type: ignore[attr-defined]
        return reply(GOOD)

    GatewayEngine(token=secret, opener=opener).infer(FRAME, MANIFEST)
    assert secret not in str(seen["url"])
    body = seen["body"]
    assert isinstance(body, bytes)
    assert secret.encode() not in body
    assert seen["auth"] == f"Bearer {secret}"


# -- it runs the real pipeline, not a second one -----------------------------


def test_the_prompt_is_the_one_that_ships() -> None:
    """Same build_prompt as GenieX and Ollama. A demo that phrased the prompt
    differently would not be exercising the prompt that ships."""
    from readykit.reply import build_prompt

    body = chat_request(DEFAULT_MODEL, MANIFEST, b"x", 0.1)
    assert body["messages"][0] == {
        "role": "system",
        "content": build_prompt(MANIFEST),
    }


def test_the_image_rides_as_a_data_uri_content_part() -> None:
    body = chat_request(DEFAULT_MODEL, MANIFEST, b"pixels", 0.1)
    parts = body["messages"][1]["content"]
    image = next(p for p in parts if p["type"] == "image_url")
    expected = base64.b64encode(b"pixels").decode()
    assert image["image_url"]["url"] == f"data:image/jpeg;base64,{expected}"


def test_instructions_lead_and_the_frame_follows() -> None:
    body = chat_request(DEFAULT_MODEL, MANIFEST, b"x", 0.1)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_a_good_reply_becomes_sightings_and_then_a_pass() -> None:
    """End to end through the real resolve_verdict, not a copy of it."""
    observation = engine(lambda r, timeout=0: reply(GOOD)).infer(FRAME, MANIFEST)
    resolution = resolve_verdict(MANIFEST, list(observation.sightings))
    assert resolution.verdict is Verdict.PASS


def test_a_missing_item_fails_closed() -> None:
    absent = json.dumps(
        {
            "kit_present": "yes",
            "items": [
                {"key": "shears", "presence": "absent", "confidence": 0.95},
                {"key": "gauze", "presence": "found", "confidence": 0.93},
            ],
        }
    )
    observation = engine(lambda r, timeout=0: reply(absent)).infer(FRAME, MANIFEST)
    resolution = resolve_verdict(MANIFEST, list(observation.sightings))
    assert resolution.verdict is Verdict.FAIL


# -- every failure keeps the latch shut --------------------------------------


def test_unusable_text_keeps_the_words_for_the_comparison() -> None:
    """The blueprint's matcher reads this text, so discarding it would throw
    away the most interesting thing on the page."""
    prose = "Sorry, I could not process that image."
    with pytest.raises(InferenceError) as caught:
        engine(lambda r, timeout=0: reply(prose)).infer(FRAME, MANIFEST)
    assert caught.value.raw_reply == prose


def test_a_text_only_model_is_named_as_the_cause() -> None:
    """It accepts the prompt, ignores the frame, and answers confidently about
    a picture it never saw. The worst failure available here."""

    class Empty:
        def __enter__(self) -> Empty:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": ""}}]}).encode()

    with pytest.raises(InferenceError, match="vision model"):
        engine(lambda r, timeout=0: Empty()).infer(FRAME, MANIFEST)


def test_an_http_error_surfaces_the_actionable_body() -> None:
    """Out of credit and budget exhausted are things a person can fix."""

    def opener(request: object, timeout: float = 0) -> object:
        raise urllib.error.HTTPError(
            "u", 402, "Payment Required", {}, None  # type: ignore[arg-type]
        )

    with pytest.raises(InferenceError, match="402"):
        engine(opener).infer(FRAME, MANIFEST)


def test_an_unreachable_gateway_is_an_inference_error() -> None:
    def opener(request: object, timeout: float = 0) -> object:
        raise urllib.error.URLError("no route")

    with pytest.raises(InferenceError, match="unreachable"):
        engine(opener).infer(FRAME, MANIFEST)


def test_a_malformed_envelope_is_not_guessed_at() -> None:
    class Junk:
        def __enter__(self) -> Junk:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"unexpected": true}'

    with pytest.raises(InferenceError):
        engine(lambda r, timeout=0: Junk()).infer(FRAME, MANIFEST)


# -- the loader --------------------------------------------------------------


def test_the_loader_resolves_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    assert isinstance(load_engine("gateway"), GatewayEngine)


def test_the_loader_names_the_gateway_among_the_options() -> None:
    with pytest.raises(InferenceError, match="gateway"):
        load_engine("nonsense")


# -- pointing it somewhere without a card ------------------------------------


def test_the_endpoint_is_configurable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AI Gateway will not serve a request without a card on file, so the
    default has to be overridable or nobody can try this."""
    monkeypatch.setenv("READYKIT_INFERENCE_HOST", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("READYKIT_INFERENCE_MODEL", "some/vision-model")
    engine = GatewayEngine(token="t")
    assert engine.host == "https://api.groq.com/openai/v1"
    assert engine.model_id == "some/vision-model"


def test_a_non_default_host_is_named_in_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Which model' is half of what an auditor asks about a frame that left
    the device. 'Sent where' is the other half."""
    monkeypatch.setenv("READYKIT_INFERENCE_HOST", "https://api.groq.com/openai/v1")
    assert GatewayEngine(token="t").name.endswith("@api.groq.com")


def test_the_default_host_is_not_spelled_out_in_the_record() -> None:
    assert "@" not in GatewayEngine(token="t").name


def test_an_explicit_argument_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("READYKIT_INFERENCE_MODEL", "from/env")
    assert GatewayEngine(token="t", model="explicit/model").model_id == "explicit/model"


def test_a_local_endpoint_is_still_reported_as_leaving_the_device() -> None:
    """Honest even when it is not: this engine posts over HTTP to something
    it does not control. The air-gapped path is `ollama`, which says so."""
    engine = GatewayEngine(token="t", host="http://127.0.0.1:11434/v1")
    assert engine.air_gapped is False
