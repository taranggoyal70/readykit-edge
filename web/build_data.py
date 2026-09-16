"""Generate the showcase site's data from the real pipeline.

Nothing on the deployed page is hand-written or mocked. Every verdict, every
sighting, every blueprint divergence below comes from running the actual
`InspectionEngine` against the actual simulator, through the same
`resolve_verdict` the hardware uses. If the safety logic changes, this output
changes with it.

Run from the repo root:  python web/build_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from readykit.bridge.loopback import LoopbackLink
from readykit.domain import Manifest
from readykit.engine import InspectionEngine
from readykit.inference.simulated import SimulatedEngine
from readykit.naive import Divergence
from readykit.voice import Intent, Utterance, resolve_intent

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "public" / "data"

VOICE_PHRASES = [
    "start watching",
    "what's wrong",
    "why did it fail",
    "read the last five inspections",
    "switch to the electrical toolbox kit",
    "just unlock it",
    "override the check, I'm in a hurry",
    "open the cabinet for me",
    "what's the weather",
]


def scene_names(manifest: Manifest) -> list[str]:
    builtin = list(SimulatedEngine.scenes())
    return builtin + [f"missing-{item.key}" for item in manifest.items]


def inspect(manifest: Manifest, scene: str) -> dict[str, object]:
    from readykit.capture import ScriptedSource

    engine = InspectionEngine(
        manifest=manifest,
        source=ScriptedSource(scene),
        engine=SimulatedEngine(),
        link=LoopbackLink(),
    )
    outcome = engine.run_once()
    resolution = outcome.record.resolution
    comparison = outcome.comparison

    return {
        "scene": scene,
        "verdict": resolution.verdict.value,
        "reason": resolution.reason,
        "latch": "released" if resolution.verdict.value == "pass" else "engaged",
        "raw_reply": outcome.record.raw_reply,
        "sightings": [
            {
                "key": s.key,
                "label": next(
                    (i.label for i in manifest.items if i.key == s.key), s.key
                ),
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = Manifest.from_json(
        (ROOT / "manifests" / "trauma-kit-a.json").read_text()
    )

    scenes = [inspect(manifest, name) for name in scene_names(manifest)]
    dangerous = sum(1 for s in scenes if (s["blueprint"] or {}).get("dangerous"))

    payload = {
        "manifest": {
            "id": manifest.manifest_id,
            "name": manifest.name,
            "confidence_floor": manifest.confidence_floor,
            "hold_seconds": manifest.hold_seconds,
            "expiry_warning_days": manifest.expiry_warning_days,
            "items": [
                {
                    "key": i.key,
                    "label": i.label,
                    "severity": i.severity.value,
                    "quantity": i.quantity,
                    "expiry_checked": i.expiry_checked,
                }
                for i in manifest.items
            ],
        },
        "scenes": scenes,
        "summary": {"total": len(scenes), "would_release_a_bad_kit": dangerous},
        "voice": [
            {
                "said": phrase,
                "intent": r.intent.value,
                "reason": r.reason,
                "actionable": r.actionable,
                "refused": r.intent is Intent.OVERRIDE,
            }
            for phrase in VOICE_PHRASES
            for r in [resolve_intent(Utterance(text=phrase, confidence=0.95))]
        ],
    }

    target = OUT / "inspections.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"{target.relative_to(ROOT)}: {len(scenes)} scenes, {dangerous} dangerous")


if __name__ == "__main__":
    main()
