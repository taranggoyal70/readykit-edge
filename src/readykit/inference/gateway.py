"""Vercel AI Gateway - a hosted vision model, for the public web demo.

This exists for one job: the showcase site at `web/` has a camera, and a camera
with nothing behind it is theatre. There is no Hexagon NPU on a serverless
function and no Ollama either, so the deployed page needs a third runtime.

**This is not the NPU and it is not air-gapped.** It is the only engine in this
package that sends a frame off the device, so it is the only one with
`air_gapped = False`, and the name recorded on every inspection is
`gateway:<model>` - unambiguous, the way `ollama:<model>` is. The product on
the Snapdragon host does not use this engine and never should. A web demo that
quietly streamed frames to a third party while the banner said "air-gapped"
would be the exact lie this project exists to argue against, so the flag is a
field the page reads rather than a sentence in a README.

What it deliberately shares with the real thing: `build_prompt`, `parse_reply`
and - downstream - `resolve_verdict`. The runtime differs; the decision does
not. That is the same bargain `ollama.py` makes, for the same reason: a demo
that reimplemented the verdict would be demonstrating different software.

Authentication is a bearer token, and the request shape is OpenAI-compatible
chat completions with an image content part:

    POST https://ai-gateway.vercel.sh/v1/chat/completions
    Authorization: Bearer <VERCEL_OIDC_TOKEN or AI_GATEWAY_API_KEY>
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

from ..capture import Frame
from ..domain import Manifest
from ..reply import ReplyParseError, build_prompt, parse_reply
from .base import InferenceEngine, InferenceError, Observation
from .ollama import _image_bytes

DEFAULT_MODEL = "alibaba/qwen3-vl-instruct"
"""Same Qwen3-VL family as the GenieX default, so the runtime is the only
thing that changes between this and the Snapdragon host. Confirmed against
the live catalogue as accepting image input; a text-only model here would
accept the prompt, ignore the frame, and answer confidently about a picture
it never saw."""

DEFAULT_HOST = "https://ai-gateway.vercel.sh/v1"

HOST_ENV = "READYKIT_INFERENCE_HOST"
MODEL_ENV = "READYKIT_INFERENCE_MODEL"
"""Overrides, so a deployment can point this at any OpenAI-compatible endpoint
without a code change.

This matters more than it looks. Vercel's AI Gateway will not service a
request without a card on file, which makes it a poor default for anyone
standing this up to try it. Several providers expose the same chat-completions
shape on a free tier with no card at all - Google's
`generativelanguage.googleapis.com/v1beta/openai`, Groq's
`api.groq.com/openai/v1` - and a local Ollama speaks it too, on loopback,
which is the only one of them that keeps the air gap.

The request shape is the same in every case, so the choice is configuration
rather than another engine. `name` still records where the frame went.
"""

OIDC_ENV = "VERCEL_OIDC_TOKEN"
"""Injected into every Vercel deployment. Preferred, because it means no API
key has to exist, be pasted anywhere, or be rotated."""

API_KEY_ENV = "AI_GATEWAY_API_KEY"
"""The fallback, for running this outside a Vercel deployment."""


def _host_label(host: str) -> str:
    """The endpoint's hostname, for the record. Never the path or a query."""
    from urllib.parse import urlparse

    return urlparse(host).hostname or host


def _credential() -> str | None:
    return os.environ.get(OIDC_ENV) or os.environ.get(API_KEY_ENV)


class GatewayEngine(InferenceEngine):
    """A hosted vision model, reached over AI Gateway."""

    air_gapped = False

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        timeout: float = 90.0,
        temperature: float = 0.1,
        token: str | None = None,
        opener: Any | None = None,
    ) -> None:
        self.model_id = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.host = (host or os.environ.get(HOST_ENV) or DEFAULT_HOST).rstrip("/")
        self._timeout = timeout
        self._temperature = temperature
        self._opener = opener or urllib.request.urlopen

        # Fail at construction, not on the first frame. A missing credential
        # is a deployment problem, and discovering it when somebody points a
        # camera at the page is strictly worse than discovering it at startup.
        self._token = token or _credential()
        if not self._token:
            raise InferenceError(
                f"no AI Gateway credential. A Vercel deployment supplies "
                f"{OIDC_ENV} automatically; elsewhere, set {API_KEY_ENV}."
            )

        # Records the host as well as the model when it is not the default.
        # "which model" is only half the question an auditor asks about a
        # frame that left the device; "sent where" is the other half.
        where = "" if self.host == DEFAULT_HOST else f"@{_host_label(self.host)}"
        self.name = f"gateway:{self.model_id}{where}"

    def infer(self, frame: Frame, manifest: Manifest) -> Observation:
        body = json.dumps(
            chat_request(
                self.model_id, manifest, _image_bytes(frame), self._temperature
            )
        ).encode()

        request = urllib.request.Request(
            f"{self.host}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )

        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # The body can carry the actionable part - out of credit, budget
            # exhausted, rate limited - so it is surfaced, truncated. It never
            # contains the token, which only ever travels in a header.
            raise InferenceError(
                f"AI Gateway returned {exc.code}: {exc.read().decode()[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise InferenceError(f"AI Gateway unreachable: {exc.reason}") from exc
        except (OSError, ValueError) as exc:
            raise InferenceError(f"AI Gateway inference failed: {exc}") from exc

        raw = _content(payload)
        if raw is None:
            raise InferenceError(
                f"AI Gateway returned no text for {self.model_id!r}. "
                "A text-only model does this: it accepts the prompt and "
                "silently ignores the image. Use a vision model."
            )

        try:
            sightings = parse_reply(raw, manifest)
        except ReplyParseError as exc:
            # Kept, not discarded. This text is exactly what the blueprint's
            # substring matcher would have acted on, and the comparison on the
            # page is worth more than the verdict that failed to parse.
            raise InferenceError(
                f"model reply was unusable: {exc}", raw_reply=raw
            ) from exc

        return Observation(sightings=tuple(sightings), raw_reply=raw)


def _content(payload: Any) -> str | None:
    """The assistant text, or None when the response carried none."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) and content.strip() else None


def chat_request(
    model_id: str, manifest: Manifest, image: bytes, temperature: float
) -> dict[str, Any]:
    """The request body for one inspection.

    Instructions lead in a system message, ahead of the frame, matching
    `ollama.chat_request`. Keeping the two the same shape is the point: the
    prompt is the thing under test, and a demo that phrased it differently
    would not be exercising the prompt that ships.
    """
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": build_prompt(manifest)},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            },
        ],
        "temperature": temperature,
        "stream": False,
    }
