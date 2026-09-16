# ReadyKit Edge

**Air-gapped visual kit inspection with physical actuation.**
A camera watches an equipment kit, a vision-language model running entirely
on-device decides whether the kit is complete, undamaged and in date, and that
decision drives a real latch.

[![CI](https://github.com/taranggoyal70/readykit-edge/actions/workflows/ci.yml/badge.svg)](https://github.com/taranggoyal70/readykit-edge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-593%20passing-brightgreen.svg)](tests/)
[![Typed: mypy strict](https://img.shields.io/badge/mypy-strict-blue.svg)](pyproject.toml)

Qualcomm Snapdragon® X Elite (on-device VLM via GenieX, Hexagon NPU)
→ Arduino® UNO Q (STM32U585 latch control)

No network. No cloud. No remote fallback. The decision and the actuation are
both local.

---

## The idea in one table

A verdict has **three** values, not two:

| Verdict | Meaning | Latch |
|---|---|---|
| **PASS** | Every critical item positively found, in date, above the confidence floor | Released |
| **FAIL** | A positive finding of non-compliance | Engaged |
| **INDETERMINATE** | Compliance could not be established | Engaged |

> **Absence of evidence is not evidence of compliance.**

`PASS` is never the fallthrough branch. An occluded lens, a crashed NPU, a
dropped serial link, a smudged expiry date and an unparseable model reply all
land on `INDETERMINATE`, and `INDETERMINATE` never opens anything.

---

## Quick start

Runs fully in simulation. No camera, no board, no model required.

```bash
git clone https://github.com/taranggoyal70/readykit-edge.git
cd readykit-edge
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,console]"

readykit inspect --manifest manifests/trauma-kit-a.json --scene expired
```

```
  FAIL  latch engaged
  Kit non-compliant: expired Vented Chest Seal
    + Windlass Tourniquet    found      0.97  2/2
    x Vented Chest Seal      found      0.96  2/2  EXPIRED 2026-08-02
    + Trauma Shears          found      0.94
    + Hemostatic Gauze       found      0.94  exp 2027-10-21
    + Nasopharyngeal Airway  found      0.95
    + Nitrile Gloves         found      0.92  2/2
    + Triage Marker          found      0.93
  vs blueprint the original blueprint would have RELEASED the latch here
  simulated · 1ms · frame 5e8481faeadd88ed
```

Every item present. Every item undamaged. Every tick green. And it still
fails, because one item went out of date.

Then open the operator console:

```bash
readykit console --manifest manifests/trauma-kit-a.json   # http://127.0.0.1:8420
```

---

## Table of contents

- [Inspiration](#inspiration)
- [What it does](#what-it-does)
- [How we built it](#how-we-built-it)
- [Challenges we ran into](#challenges-we-ran-into)
- [Accomplishments that we're proud of](#accomplishments-that-were-proud-of)
- [What we learned](#what-we-learned)
- [What's next for ReadyKit Edge](#whats-next-for-readykit-edge)
- [Built With](#built-with)
- [Installation](#installation)
- [CLI reference](#cli-reference)
- [Manifests](#manifests)
- [Architecture](#architecture)
- [Hardware](#hardware)
- [Development](#development)
- [Honest limits](#honest-limits)
- [Documentation](#documentation)
- [License](#license)

---

## Inspiration

Some boxes have to be complete, or someone gets hurt.

A medic's trauma kit. A crash cart in a hospital corridor. A toolbox for
someone about to work on live electricity. Every one of them is useless in the
exact moment it is needed if something is missing.

Today these are checked by a person with a clipboard, usually at the end of a
shift, usually at 3am. People get tired. People tick boxes they did not really
look at. And nobody finds out anything was wrong until the worst possible
moment. Expired adrenaline in a crash cart and a trauma kit with no tourniquet
are both real, documented, recurring failures - and they are invisible to
everything except something that can actually look at the kit and read what is
printed on it.

The deeper motivation was a design bug we kept seeing in systems like this. The
reference implementation for this hardware decides with
`if "missing" in reply or "no" in reply`. A two-answer system has nowhere to put
"I could not tell", so doubt quietly becomes a pass - and the lock opens on a
kit nobody actually looked at. That felt like the real problem worth solving.

## What it does

A camera watches an equipment kit. A vision-language model running locally on
the Hexagon NPU judges the kit against a **Manifest** - the authored
specification of what a compliant kit contains. That judgement drives a
physical latch: a compliant kit unlocks, anything else stays shut.

- **Checks presence, damage, quantity and expiry.** Presence is not
  serviceability. A sealed, undamaged, correctly-placed packet of *expired*
  haemostatic gauze passes every visual check and is still not something to
  hand a medic. The model reads the printed use-by date off the packaging and
  reasons about whether it has passed - work object detection cannot do and a
  barcode scanner cannot do.
- **Counts.** Two tourniquets required, one present, is a kit that runs out
  halfway through. If the model cannot count them, that is unresolved, and
  unresolved keeps the latch shut.
- **Distinguishes critical from advisory items** per manifest, so doubt about
  an item the manifest already said the kit can live without does not hold the
  latch.
- **Fails closed, everywhere.** The latch line is fail-secure: de-energised
  means locked. A crash, a pulled cable, a flat battery and a firmware restart
  all land on *shut*. The actuator node treats two seconds of host silence as a
  fault and re-engages rather than holding its last instruction.
- **Keeps watching after it opens.** The `sentinel` loop stays in the frame
  through the hold, so lifting a required item out re-engages the latch under
  your hand. Opening takes a streak of clean frames; closing is quicker,
  because that is taking back something already granted.
- **Runs the old design beside the new one, on every frame,** and records what
  it would have done.
- **Writes a tamper-evident record.** Every inspection is hash-chained:
  the manifest used, every sighting, the verdict, the reason, and what the
  hardware was commanded to do.
- **Ships an operator console** - a loopback-only local web view of the live
  pipeline, the latch state, and the verbatim model reply.
- **Runs entirely offline.** It works in a basement with no signal, which
  matters because these cabinets live in ambulances, field hospitals and
  warehouses.

### We can prove the obvious implementation is broken

We did not just claim the two-answer design is dangerous. We kept it, executable,
and replay it against the same verbatim model output on every single frame:

```bash
readykit compare --manifest manifests/trauma-kit-a.json
```

```
  Field Trauma Kit A  19 scenes

  scene                   blueprint           readykit        divergence
  --------------------------------------------------------------------------
  complete                PASS_KIT            pass            agreed
  complete-negated        ERR_MISSING_TOOL    pass            rejects a good kit
  expired                 PASS_KIT            fail            UNLOCKS A BAD KIT
  occluded                PASS_KIT            indeterminate   UNLOCKS A BAD KIT
  missing-shears          PASS_KIT            fail            UNLOCKS A BAD KIT
  ...

  11 of 19 scenes would have released the latch under the original design.
```

The failure mode is not hypothetical. A real model reply is
`"Absent from the tray: Trauma Shears."` - correct, and it contains neither
"missing" nor "no", so the substring matcher writes `PASS_KIT` and unlocks a
trauma kit with no trauma shears in it.

We kept it honest, too. The old design gets some scenes right, including
rejecting `"Sorry, I could not process that image"` purely by luck, because
"could not" happens to contain "no". There is a test pinning that case
specifically, because a comparison that only ever flattered us would not be
worth showing anyone.

## How we built it

**Domain first.** [`CONTEXT.md`](CONTEXT.md) defines the vocabulary - Manifest,
Kit, Required Item, Sighting, Inspection, Verdict, Latch, Host Link - with an
explicit *avoid* list for each. `src/readykit/domain.py` is a pure module with
no I/O, no hardware and no SDK imports, and its names are exactly those names.
Everything that touches the world lives behind an interface.

**Every hardware layer has a real and a simulated implementation.** Capture,
inference and the host link are each an interface with two or more backends,
so the whole pipeline - latch behaviour and watchdog included - is testable
without a board.

| Layer | Field | Bench |
|---|---|---|
| Capture | OpenCV / ffmpeg camera | scripted scenes, still images from disk |
| Inference | Qualcomm **GenieX** on the Hexagon NPU | **Ollama** (a real VLM, any machine), or a deterministic simulator |
| Host Link | framed serial to the UNO Q | in-process loopback driving a virtual actuator node |

The Ollama backend exists because the Qualcomm stack only runs on the Snapdragon
host, and waiting for that machine to test anything is a bad way to build. It
answers the questions the simulator structurally cannot: does the prompt survive
contact with a real model, and does the parser cope with the prose preamble, the
markdown fence and the key the model invented? The simulator was written by the
same hand as the parser and agrees with it by construction.

**The wire protocol is line-oriented ASCII,** because it has to be parsed by a
hand-written reader on an STM32U585 with no allocator and read on a logic
analyser at 3am:

```
RK1 <seq> <COMMAND> <payload> <CRC8>\n
ACK <seq> <STATUS>\n
```

CRC-8 for integrity, a sequence number for freshness, and an acknowledgement so
the host learns what actually happened. **A command with no ACK is recorded as
not having happened.**

**The firmware is non-blocking.** There is no `delay()` anywhere. The reference
sketch blocked for five seconds mid-hold, during which it read no serial at all
and was deaf even to the command that would have closed the latch.

**The audit chain** is JSON Lines, appended and flushed immediately, never
rewritten, with `hash(n) = blake2b(canonical_json(record(n) with prev_hash = hash(n-1)))`.
The format stays deliberately dumb so a partially-written file after a power cut
is still readable up to the last complete line, which is exactly the situation
an air-gapped field device eventually finds itself in.

**The console binds to loopback and refuses anything else** - not a warning, an
exit. This device releases a physical latch on command, so serving it on a
routable interface would not expose a dashboard, it would expose a remote
unlock to anyone who can reach the port.

## Challenges we ran into

- **The hardest bug was a design bug, not a code bug.** Two-valued verdicts are
  the natural thing to write and they are wrong for anything that gates a lock.
  Getting `INDETERMINATE` to propagate correctly meant every failure of our own
  machinery - dead camera, crashed NPU, unparseable reply, dropped link - had to
  resolve to it, and the engine had to be boring enough that no clever path
  could sneak a `PASS` through.
- **Advisory items broke the first version of the rule.** "Unestablished
  evidence is always indeterminate" is too strong: if a confident *absent* on an
  advisory item passes the kit, then an occluded look at that same item must not
  block it, or doubt outranks certainty. Advisory doubt is now recorded as an
  advisory, and the kit's fate turns on the critical items.
- **Occlusion during the hold.** While the latch is open, the kit is *meant* to
  be reached into. Hands cover items and frames go indeterminate constantly.
  Treating that as a reason to close would slam the cabinet on every legitimate
  use. During the hold, only a positive finding closes it.
- **Two protocol implementations that must agree.** The safety argument assumes
  the C parser and the Python encoder read the same bytes. So the C is compiled
  against a stub `Arduino.h` in CI and fed frames generated by the Python side.
- **Compiling the sketch in CI at all.** The `.ino` is built five times with
  different LED macros defined, so every branch of the onboard-LED pin guards
  gets read. A sketch that will not build is a bad thing to discover at a bench
  with one board.
- **The board is not the board you think it is.** The UNO Q's FQBN is
  `arduino:zephyr:unoq`, it is only in Arduino's staging package index, its
  `Serial` is an RPC bridge rather than a raw UART, and its USB-C port belongs
  to the QRB2210 running Debian, not to the STM32U585 that drives the pins.
- **ARM64 Windows.** OpenCV has no ARM64 wheel for the Snapdragon host, so
  capture needed an ffmpeg path. Legacy console hosts do not process ANSI, which
  is invisible until a demo prints a screenful of escape codes to a room of
  judges.
- **Building for hardware we did not have.** The Snapdragon laptop and UNO Q are
  provided at the event, so every hardware behaviour had to be pinned by a test
  against a simulated implementation first, with a bring-up checklist waiting
  for the moment the boards arrive.

## Accomplishments that we're proud of

- **A three-valued verdict that actually holds** under every failure path we
  could construct, with `INDETERMINATE` wired through capture, inference,
  parsing, aggregation and the link.
- **The comparison harness.** Keeping the broken design executable and replaying
  it on every frame turns an argument into a measurement: 11 of 19 scenes would
  have released the latch.
- **593 tests passing, `ruff` and `mypy --strict` clean, CI across Python
  3.11, 3.12 and 3.13** - including the firmware, compiled and exercised in CI
  with no board attached, plus a live audit script that drives every command
  through the real argument parser and checks exit codes.
- **Expiry reasoning**, which is the check that fails a kit that looks perfect,
  and the reason this needs a VLM rather than object detection.
- **The safety property lives in the pin, not the peripheral.** D5 goes LOW on
  reset, on power loss, on a stale link and on hold expiry whether a relay coil
  or nothing at all is attached. That is why a bare board with one USB-C cable
  is a complete and honest demonstration rather than a compromise.
- **Two LEDs that cannot lie.** Latch and verdict are separate lamps, so the
  one reporting the lock can never contradict the physical state someone is
  about to put their hand into. Amber sitting beside red is the entire thesis
  in one glance.
- **Honesty as an engineering constraint.** The audit tool prints its own
  limitation every time it runs. The bench tool labels simulated timings so they
  cannot be quoted as NPU figures. A test pins the case the old design gets
  right by accident.

## What we learned

- **"Not finding a problem is not the same as checking."** That single sentence
  reorganised the whole system. Any system that gates a physical consequence
  needs a third answer, and that answer must never be the safe-looking one.
- **Naming is design.** Writing `CONTEXT.md` before the code, with an *avoid*
  list per term, caught category errors early. A Sighting is an observation and
  never a decision; once that was written down, code that conflated them looked
  obviously wrong.
- **A simulator written by the author of the parser proves very little.** It
  agrees by construction. Pointing the same pipeline at a real model through
  Ollama found parser problems the simulator could never have surfaced.
- **Asymmetric thresholds are a feature.** Granting something and retracting it
  are different actions and deserve different evidence bars.
- **Being specific about limits buys more credibility than it costs.**
  Tamper-evident rather than tamper-proof; counting is the model's weakest axis;
  it has not run on the hardware yet. Volunteering these makes everything else
  believable.
- **Building against hardware you do not have yet is possible,** if every
  hardware behaviour is expressed as an interface with a simulated
  implementation and a test.

## What's next for ReadyKit Edge

- **Run it on the actual hardware.** The bring-up checklist in
  [`docs/deployment.md`](docs/deployment.md) lists exactly what remains to be
  verified on device, and `verify_board.py` covers every item observable over
  the wire.
- **Strengthen the audit chain** from tamper-evident to tamper-resistant with a
  signing key in a secure element, or by anchoring the head hash outside the
  device.
- **Improve counting**, the model's weakest axis - more frames per inspection
  for multi-quantity kits, and a dedicated counting pass.
- **Fleet view.** Today it is a single station. Multi-site aggregation of
  hash-chained records, without giving up the air gap.
- **More manifests and more domains** - crash carts, electrical safety kits,
  aviation line-maintenance boxes, disaster-response caches.
- **Voice interaction and a wall display** for hands-busy operators.
- **Signed manifests**, so the specification a kit is judged against is itself
  verifiable.

## Built With

**Hardware:** Qualcomm Snapdragon® X Elite · Hexagon NPU · Arduino® UNO Q ·
STM32U585 · QRB2210

**On-device AI:** Qualcomm GenieX · Qualcomm AI Engine Direct SDK ·
Qualcomm AI Hub Models · Hugging Face · Qwen3-VL-4B-Instruct · Ollama

**Host:** Python 3.11+ · FastAPI · Uvicorn · OpenCV · ffmpeg · pySerial ·
blake2b hash chaining

**Firmware:** C++ (Arduino) · Zephyr core (`arduino:zephyr:unoq`) ·
Arduino_RouterBridge · arduino-cli

**Quality:** pytest · pytest-cov · ruff · mypy (strict) · GitHub Actions

**Design:** vanilla HTML/CSS/JS console, visual language vendored from
[VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md)

---

## Installation

Requires **Python 3.11 or newer**.

```bash
git clone https://github.com/taranggoyal70/readykit-edge.git
cd readykit-edge
python -m venv .venv && source .venv/bin/activate
```

Extras are separated so a bench machine does not need the hardware stack:

| Extra | Installs | For |
|---|---|---|
| *(none)* | nothing | the pure simulation pipeline |
| `console` | `fastapi`, `uvicorn` | the operator console |
| `host` | `opencv-python`, `numpy`, `pyserial` | real camera and real serial link |
| `dev` | `pytest`, `pytest-cov`, `ruff`, `mypy`, `httpx` | contributing |

```bash
pip install -e ".[dev,console]"      # bench / development
pip install -e ".[host,console]"     # deployed inspection host
```

Then check the machine before you need it to work:

```bash
readykit doctor --cameras
```

`doctor` reports every dependency, camera index and serial port, and prints the
exact command that fixes anything missing. It changes nothing.

Snapdragon X Elite (ARM64 Windows) has its own walkthrough, including the
OpenCV wheel problem and the GenieX install:
[`docs/windows-setup.md`](docs/windows-setup.md).

## CLI reference

```
readykit {inspect,watch,scenes,console,compare,sentinel,doctor,demo,bench,audit,records}
```

| Command | What it does |
|---|---|
| `inspect` | Run a single inspection and print the verdict |
| `watch` | Inspect continuously on an interval |
| `sentinel` | Watch continuously, and keep watching after the latch opens |
| `console` | Serve the operator console (loopback only) |
| `compare` | Replay every scene against the original blueprint's logic |
| `scenes` | List the simulator scenes |
| `demo` | Run the scripted demonstration sequence, no camera needed |
| `doctor` | Check this machine can run everything, before you need it to |
| `bench` | Measure inference latency on whatever engine is configured |
| `audit` | Verify the inspection record chain has not been altered |
| `records` | Show recent inspection records |

### Common flags

| Flag | Values | Notes |
|---|---|---|
| `--manifest` | path | The specification the kit is judged against |
| `--engine` | `simulated`, `ollama`, `geniex` | Defaults to simulated |
| `--model` | e.g. `qualcomm/Qwen3-VL-4B-Instruct` | A repo id, not a file path |
| `--device` | `auto`, `<runtime>:<compute_unit>` | GenieX `device_map` |
| `--require-npu` | flag | Refuse to run unless the NPU can be shown to be in use |
| `--scene` | scene name, or `missing-<key>` / `damaged-<key>` | Simulator only |
| `--camera` / `--ffmpeg-camera` | index | Use ffmpeg on ARM64 Windows |
| `--image` | one or more paths | Inspect stills from disk instead of a camera |
| `--link` | `loopback`, `serial` | Loopback drives a virtual actuator in-process |
| `--port` | `/dev/ttyACM0`, `COM3` | Serial device for the host link. On `console` this is the HTTP port, and the serial device is `--serial-port`. |
| `--frames` | int | Frames aggregated per inspection |
| `--agreement` | 0.0-1.0 | Fraction of frames that must agree |
| `--interval` | seconds | Between inspections in `watch` / `sentinel` |

### Examples

```bash
# Simulation, no hardware at all
readykit inspect --manifest manifests/trauma-kit-a.json --scene missing-shears
readykit scenes
readykit demo --dwell 6

# A real VLM on an ordinary laptop, virtual latch
readykit watch --manifest manifests/trauma-kit-a.json \
  --engine ollama --model qwen2.5vl:7b --camera 0 --link loopback

# The deployed configuration
readykit watch \
  --manifest manifests/trauma-kit-a.json \
  --engine geniex --model qualcomm/Qwen3-VL-4B-Instruct --device auto \
  --camera 0 \
  --link serial --port /dev/ttyACM0 \
  --interval 3

# The receipts
readykit compare --manifest manifests/trauma-kit-a.json
readykit audit --log records/inspections.jsonl
```

## Manifests

A Manifest is authored ahead of time and is the same for every kit of that
type. Three ship with the repo: `trauma-kit-a`, `electrical-toolbox` and
`desk-rehearsal` (the last one is checkable with objects already on your desk).

```json
{
  "manifest_id": "trauma-kit-a",
  "name": "Field Trauma Kit A",
  "confidence_floor": 0.6,
  "hold_seconds": 5.0,
  "expiry_warning_days": 30,
  "items": [
    { "key": "tourniquet", "label": "Windlass Tourniquet", "severity": "critical", "quantity": 2 },
    { "key": "chest_seal", "label": "Vented Chest Seal",   "severity": "critical", "quantity": 2, "expiry_checked": true },
    { "key": "shears",     "label": "Trauma Shears",       "severity": "critical" },
    { "key": "gloves",     "label": "Nitrile Gloves",      "severity": "advisory", "quantity": 2 }
  ]
}
```

| Field | Meaning |
|---|---|
| `confidence_floor` | The threshold a sighting must clear to count as evidence. Below it contributes `INDETERMINATE`, never `FAIL`. |
| `hold_seconds` | How long a `PASS` releases the latch for |
| `expiry_warning_days` | The window in which an in-date item is reported as *expiring soon* |
| `severity` | `critical` disqualifies the kit; `advisory` is recorded but does not fail it |
| `quantity` | How many are required. Fewer is **Short**: present, undamaged, in date, and still non-compliant. |
| `expiry_checked` | Whether the printed use-by date must be read and judged |

## Architecture

```
Camera ─► Inspection Host ─────────────────────► Actuator Node ─► Latch
          (Snapdragon X Elite)   Host Link       (Arduino UNO Q)
          capture → infer         RK1 framed      STM32U585
          → resolve → enact       CRC-8 + ACK     2s watchdog
          → record                115200 baud     fail-secure pin
```

```
src/readykit/
  domain.py        Pure domain types and resolve_verdict(). No I/O, no SDKs.
  engine.py        The inspection loop. Deliberately boring; every failure
                   of its own machinery resolves to INDETERMINATE.
  aggregate.py     Multi-frame agreement. Frames that disagree produce doubt,
                   never an average.
  reply.py         The model prompt, and the parser for what comes back.
  naive.py         The original blueprint's substring matcher, preserved and
                   executed on every frame. Never touches the Host Link.
  protocol.py      The RK1 wire format. Mirrored by firmware/.../protocol.h.
  recorder.py      Append-only, hash-chained Inspection Records.
  sentinel.py      Watching continuously, including after the latch opens.
  doctor.py        Environment checks, for the first ten minutes on
                   unfamiliar hardware.
  capture.py       FrameSource: OpenCV, ffmpeg, image files, scripted.
  cli.py           Argument parsing and terminal output.
  inference/       InferenceEngine: geniex (NPU), ollama, simulated.
  bridge/          HostLink: serial_link (real), loopback (virtual node).
  console/         Loopback-only FastAPI operator console + static assets.

firmware/mcu_actuator/   The .ino, protocol.h, indicators.h
firmware/test/           Stub Arduino.h and C++ tests, compiled in CI
manifests/               Authored kit specifications
docs/                    Deployment, demo run-of-show, Windows setup
```

## Hardware

**You do not need to wire anything.** A bare UNO Q, one USB-C cable and no
other parts is a complete demonstration, because the safety property is a
property of the pin rather than of the part hanging off it.

The board carries two RGB LEDs wired straight to the STM32U585, which happen to
be exactly the two signals this system has to show:

| LED | Answers | Shows |
|---|---|---|
| **Latch** | Is it locked *right now*? | Red engaged, green released. Never blinks, never dark. |
| **Verdict** | What did the model conclude? | Green pass, red fast blink fail, **amber slow pulse could not tell**, blue wig-wag host gone. |

Flashing:

```bash
arduino-cli core update-index --additional-urls https://downloads.arduino.cc/packages/package_staging_index.json
arduino-cli core install arduino:zephyr --additional-urls https://downloads.arduino.cc/packages/package_staging_index.json
arduino-cli lib install Arduino_RouterBridge
arduino-cli compile --upload -p COMn -b arduino:zephyr:unoq firmware/mcu_actuator
```

For an enclosure, the header pins are the deployed unit's lines: D2 green lamp,
D3 red lamp, D4 buzzer, D5 relay IN, common GND. **Use a fail-secure latch** -
de-energised means locked. A fail-safe latch inverts the entire safety
argument, because every power cut becomes an unlock. Full wiring notes, port
selection and the bring-up checklist are in
[`docs/deployment.md`](docs/deployment.md).

## Development

```bash
pip install -e ".[dev,console]"

pytest                 # 593 passing, 2 skipped
ruff check .
mypy
bash scripts/live-audit.sh    # drives every command through the real parser
```

The firmware is covered by the same `pytest` run, three ways:

- `test_firmware_sketch` compiles the real `.ino` against a stub `Arduino.h`
  five times, with different LED macros each time, so every branch of the pin
  guards is compiled at least once.
- `test_firmware_indicators` drives the real `rkRenderPanel()` and asserts what
  the LEDs actually show - that red means locked, that amber is not red, that
  the buzzer never sounds out of step with the light.
- `test_firmware_protocol` feeds the C parser frames encoded by
  `readykit.protocol`, because the safety argument assumes the two
  implementations agree.

None of them prove anything about the STM32U585 toolchain or the board's own
pin macros. They catch the typo, not the target.

CI runs lint, strict types, the full suite and the live command audit on Python
3.11, 3.12 and 3.13.

If you rename a domain term, rename it in [`CONTEXT.md`](CONTEXT.md) too. The
names in `domain.py` are the names there, and that is deliberate.

## Honest limits

Being straight about this is part of the point.

- **It has not yet run on the real hardware.** The Snapdragon host and the
  UNO Q are provided at the event. Every item on the bring-up checklist in
  [`docs/deployment.md`](docs/deployment.md) is a behaviour a test already
  pins against a simulated implementation, but simulation is not silicon.
- **Counting is the model's weakest axis.** Quantity is enforced and a count
  that was never taken fails closed, but counting small identical objects is
  harder for a VLM than identifying them, so multi-quantity kits want more
  frames per inspection.
- **Tamper-evident, not tamper-proof.** The audit chain catches editing,
  deletion, reordering and corruption. It does not stop someone with write
  access who rebuilds every subsequent hash. That needs a signing key in a
  secure element or an external anchor. The CLI prints this limitation every
  time it runs.
- **Single-station.** No fleet management, no multi-site aggregation.
- **A confidently misread legible date is a model-accuracy problem** we cannot
  solve at this layer. The prompt tells the model to omit the field rather than
  guess, and anything unrecognisable becomes "no date", which is unresolved,
  which keeps the latch shut - so every *ambiguity* resolves the safe way.

## Documentation

| Document | What is in it |
|---|---|
| [`CONTEXT.md`](CONTEXT.md) | The domain vocabulary, with an *avoid* list per term |
| [`docs/what-we-are-building.md`](docs/what-we-are-building.md) | The whole project in plain English, no jargon |
| [`docs/one-pager.md`](docs/one-pager.md) | The single-page summary |
| [`docs/deployment.md`](docs/deployment.md) | Model, firmware, wiring, ports, bring-up checklist, tuning |
| [`docs/windows-setup.md`](docs/windows-setup.md) | Snapdragon X Elite / ARM64 Windows, end to end |
| [`docs/demo.md`](docs/demo.md) | Three-minute run of show, and the questions that follow |
| [`docs/build-brief.md`](docs/build-brief.md) | The original build prompt and staging plan |
| [`DESIGN.md`](DESIGN.md) | Visual language for the operator console |

## License

[MIT](LICENSE) © 2026 Tarang Goyal
