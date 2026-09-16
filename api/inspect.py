"""Inspect one camera frame, for the public showcase site.

This is the server half of the site's camera. The browser holds the camera
permission and posts a JPEG; this runs the **real** pipeline over it - the
same `build_prompt`, the same `parse_reply`, the same `resolve_verdict` the
Snapdragon host runs - and returns the outcome in the same shape as a recorded
scene, so the page renders live and replayed inspections with one code path.

Three things this is not, all of which the page states plainly:

* **Not air-gapped.** `GatewayEngine` sends the frame to a hosted model. It is
  the only engine in the package with `air_gapped = False`, and this endpoint
  reports that flag in every response rather than letting the page assume it.
* **Not a latch.** There is no board at the other end of a serverless
  function. The actuator is `VirtualActuatorNode`, and the latch state in the
  response is what the firmware *would* have been commanded to do.
* **Not the operator console.** That one refuses to bind to anything but
  loopback, because it drives a real lock. Nothing here can.

The verdict, though, is real. That is the whole point: a demo that
reimplemented the decision would be demonstrating different software.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from readykit.bridge.loopback import LoopbackLink  # noqa: E402
from readykit.capture import Frame, FrameSource  # noqa: E402
from readykit.domain import Manifest  # noqa: E402
from readykit.engine import InspectionEngine  # noqa: E402
from readykit.inference.base import InferenceError  # noqa: E402
from readykit.inference.gateway import GatewayEngine  # noqa: E402
from readykit.naive import Divergence  # noqa: E402

MANIFEST_PATH = ROOT / "manifests" / "trauma-kit-a.json"

MAX_FRAME_BYTES = 4 * 1024 * 1024
"""Matches the console's own cap. A frame larger than this is not a better
look at a kit, it is an upload."""

IMAGE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
"""Checked before anything is sent anywhere. The model is billed per call, so
a payload that is not an image should cost nothing to reject."""

MIN_SECONDS_BETWEEN = 3.0
"""A crude per-instance throttle.

Honest about what it is: serverless instances do not share memory, so this
bounds one instance rather than the endpoint. It is enough to stop a held
finger on the Inspect button turning into a bill, and it is not a defence
against a determined caller - that needs a shared store, which this demo does
not have. Said out loud here because an undocumented half-measure is worse
than a documented one.
"""

_last_call = 0.0


class PostedFrame(FrameSource):
    """The bytes the browser sent, as a Frame. No camera, no disk."""

    def __init__(self, image: bytes) -> None:
        self._image = image

    def read(self) -> Frame:
        from hashlib import blake2b

        return Frame(
            image=self._image,
            digest=blake2b(self._image, digest_size=8).hexdigest(),
            width=0,
            height=0,
        )


def serialise(outcome: Any, manifest: Manifest, engine_name: str) -> dict[str, Any]:
    """The same shape `web/build_data.py` writes for a recorded scene.

    Deliberately identical, so the page has one renderer and a live verdict
    cannot accidentally be displayed by more forgiving code than a replayed
    one.
    """
    resolution = outcome.record.resolution
    comparison = outcome.comparison
    labels = {item.key: item.label for item in manifest.items}

    return {
        "scene": "live",
        "live": True,
        "air_gapped": False,
        "engine": engine_name,
        "verdict": resolution.verdict.value,
        "reason": resolution.reason,
        "latch": "released" if resolution.verdict.value == "pass" else "engaged",
        "latch_simulated": True,
        "raw_reply": outcome.record.raw_reply,
        "sightings": [
            {
                "key": s.key,
                "label": labels.get(s.key, s.key),
                "presence": s.presence.value,
                "confidence": round(s.confidence, 2),
                "count": s.count,
                "expiry": s.expiry.isoformat() if s.expiry else None,
            }
            for s in outcome.record.sightings
        ],
        "flags": {
            "missing": list(resolution.missing),
            "damaged": list(resolution.damaged),
            "unresolved": list(resolution.unresolved),
            "expired": list(resolution.expired),
            "expiring_soon": list(resolution.expiring_soon),
            "short": list(resolution.short),
            "advisories": list(resolution.advisories),
        },
        "blueprint": None
        if comparison is None
        else {
            "signal": comparison.signal,
            "divergence": comparison.divergence.value,
            "would_release": comparison.blueprint_unlocks,
            "dangerous": comparison.divergence is Divergence.UNSAFE,
            "reason": comparison.reason,
        },
    }


def configured() -> bool:
    """Whether a credential exists for the hosted model.

    Separated from inference failure on purpose. "Nobody has wired this up
    yet" and "the model looked and could not tell" are different facts, and
    only the second one is about the kit. Collapsing them would let a
    deployment problem masquerade as a cautious verdict, which is the same
    confusion INDETERMINATE exists to prevent.
    """
    from readykit.inference.gateway import _credential

    return _credential() is not None


def run(image: bytes) -> dict[str, Any]:
    """One inspection. Every failure of this machinery is INDETERMINATE."""
    manifest = Manifest.from_json(MANIFEST_PATH.read_text())
    engine = InspectionEngine(
        manifest=manifest,
        source=PostedFrame(image),
        engine=GatewayEngine(),
        link=LoopbackLink(),
    )
    outcome = engine.run_once()
    return serialise(outcome, manifest, engine.engine.name)


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        global _last_call

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send(400, {"error": "no image was sent"})
            return
        if length > MAX_FRAME_BYTES:
            self._send(413, {"error": "image is too large"})
            return

        image = self.rfile.read(length)
        if not image.startswith(IMAGE_SIGNATURES):
            self._send(415, {"error": "send a JPEG or PNG image"})
            return

        if not configured():
            # A deployment problem, not a verdict. Say so in the page's own
            # words rather than handing a visitor an environment variable
            # name, and do not dress it up as INDETERMINATE.
            self._send(
                503,
                {
                    "error": (
                        "Live inference is not configured on this deployment "
                        "yet, so the camera has nothing to decide with. The "
                        "recorded scenes below are unaffected - they are real "
                        "verdicts from the same pipeline."
                    ),
                    "unconfigured": True,
                },
            )
            return

        now = time.monotonic()
        if now - _last_call < MIN_SECONDS_BETWEEN:
            self._send(429, {"error": "one look at a time; try again in a moment"})
            return
        _last_call = now

        try:
            self._send(200, run(image))
        except InferenceError as exc:
            # The model failed, so nothing was established. The page shows this
            # as INDETERMINATE with the latch shut, which is the same thing the
            # engine would have recorded.
            self._send(
                200,
                {
                    "scene": "live",
                    "live": True,
                    "air_gapped": False,
                    "verdict": "indeterminate",
                    "reason": f"inference failed: {exc}",
                    "latch": "engaged",
                    "latch_simulated": True,
                    "raw_reply": exc.raw_reply,
                    "sightings": [],
                    "flags": {
                        "missing": [],
                        "damaged": [],
                        "unresolved": [],
                        "expired": [],
                        "expiring_soon": [],
                        "short": [],
                        "advisories": [],
                    },
                    "blueprint": None,
                },
            )
        except Exception as exc:
            self._send(500, {"error": str(exc)[:200]})

    def do_GET(self) -> None:
        self._send(405, {"error": "post a JPEG or PNG frame"})
