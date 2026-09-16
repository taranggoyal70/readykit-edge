/*
 * Replays recorded pipeline output. This file renders data; it decides nothing.
 *
 * Every verdict it displays was produced by the Python InspectionEngine at
 * build time and written to data/inspections.json. There is deliberately no
 * verdict logic here - a second implementation of resolve_verdict living in
 * a web page is exactly the kind of drift this project argues against.
 */

const GLYPH = { found: "+", absent: "−", damaged: "!", unreadable: "?" };
const CLASS_FOR = { pass: "pass", fail: "fail", indeterminate: "hold" };

let DATA = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]
  );
}

/* Which flag, if any, is the reason this row is interesting. Order matters:
 * a positive finding of non-compliance outranks doubt, because that is the
 * order resolve_verdict applies them in. */
function rowState(scene, key) {
  const f = scene.flags;
  if (f.missing.includes(key)) return ["row-bad", "missing"];
  if (f.damaged.includes(key)) return ["row-bad", "damaged"];
  if (f.expired.includes(key)) return ["row-bad", "expired"];
  if (f.short.includes(key)) return ["row-bad", "short"];
  if (f.unresolved.includes(key)) return ["row-hold", "unresolved"];
  if (f.expiring_soon.includes(key)) return ["row-hold", "expiring soon"];
  if (f.advisories.includes(key)) return ["row-advisory", "advisory"];
  return ["row-ok", ""];
}

function renderScenes() {
  const host = document.getElementById("scenes");
  host.innerHTML = DATA.scenes
    .map((scene, i) => {
      const cls = CLASS_FOR[scene.verdict] || "hold";
      const color = { pass: "var(--verdict-pass)", fail: "var(--verdict-fail)", hold: "var(--verdict-hold)" }[cls];
      return `<button class="scene-btn" role="tab" data-i="${i}" aria-selected="false">
        <span class="dot" style="background:${color}"></span>${esc(scene.scene)}
      </button>`;
    })
    .join("");

  host.querySelectorAll(".scene-btn").forEach((btn) => {
    btn.addEventListener("click", () => select(Number(btn.dataset.i)));
  });
}

function select(index) {
  const scene = DATA.scenes[index];
  if (!scene) return;

  document.querySelectorAll(".scene-btn").forEach((b) => {
    b.setAttribute("aria-selected", String(Number(b.dataset.i) === index));
  });

  renderReadout(scene, "readout");
}

/* One renderer for both. A live verdict must not be displayed by more
 * forgiving code than a replayed one - if the live path had its own renderer,
 * the two could drift and the page would be showing two different products. */
function renderReadout(scene, target) {
  const cls = CLASS_FOR[scene.verdict] || "hold";
  const items = DATA.manifest.items;

  const rows = items
    .map((item) => {
      const sighting = scene.sightings.find((s) => s.key === item.key);
      const [rowCls, note] = rowState(scene, item.key);
      const glyph = sighting ? GLYPH[sighting.presence] || "?" : "?";
      const conf = sighting ? sighting.confidence.toFixed(2) : "—";
      const extra = [
        note,
        sighting && sighting.count != null && item.quantity > 1
          ? `${sighting.count}/${item.quantity}`
          : "",
        sighting && sighting.expiry ? `exp ${sighting.expiry}` : "",
      ]
        .filter(Boolean)
        .join(" · ");

      return `<div class="row ${rowCls}">
        <span class="glyph">${glyph}</span>
        <span class="label">${esc(item.label)}</span>
        <span class="note">${esc(extra)}</span>
        <span class="conf">${conf}</span>
      </div>`;
    })
    .join("");

  const bp = scene.blueprint;
  const blueprintHtml = !bp
    ? `<div class="blueprint bp-ok">
         <div class="blueprint-h">The original blueprint</div>
         The model produced no reply for its parser to read, so there is nothing
         to compare. Not comparable is not the same as agreed.
       </div>`
    : `<div class="blueprint ${bp.dangerous ? "bp-bad" : "bp-ok"}">
         <div class="blueprint-h">The original blueprint, on this same reply</div>
         ${
           bp.dangerous
             ? `<span class="verdictword">Would have RELEASED the latch.</span> ${esc(bp.reason)}`
             : `<span class="verdictword">${esc(bp.would_release ? "Would have released" : "Would have held")}.</span> ${esc(bp.reason)}`
         }
         <span class="sig">wrote: ${esc(bp.signal)}</span>
       </div>`;

  const liveNote = scene.live
    ? `<div class="live-note">Decided by <code>${esc(scene.engine || "a hosted model")}</code>
        over the network, then judged by the same <code>resolve_verdict</code> the
        hardware runs. The latch state below is what the firmware would have been
        commanded to do - there is no board at the other end of a web page.</div>`
    : "";

  document.getElementById(target).innerHTML = `
    ${liveNote}
    <div class="banner b-${cls}">
      <span class="banner-verdict">${esc(scene.verdict.toUpperCase())}</span>
      <span class="banner-latch">latch ${esc(scene.latch)}</span>
      <span class="banner-reason">${esc(scene.reason)}</span>
    </div>
    <div class="checklist">${rows}</div>
    <div class="said">
      <div class="said-h"><span>What the model actually said</span><span class="hint">verbatim · scroll</span></div>
      <div class="said-q">${esc(scene.raw_reply || "(no reply - the engine failed before producing one)")}</div>
    </div>
    ${blueprintHtml}
  `;
}

function renderVoice() {
  const host = document.getElementById("voice");
  host.innerHTML = DATA.voice
    .map((v) => {
      const cls = v.refused ? "v-refused" : v.actionable ? "v-ok" : "v-none";
      const outcome = v.refused
        ? "REFUSED"
        : v.actionable
          ? v.intent.replace(/_/g, " ")
          : "ignored";
      return `<div class="vrow ${cls}">
        <span class="said-text">${esc(v.said)}</span>
        <span class="outcome">${esc(outcome)}</span>
        <span class="why">${esc(v.reason)}</span>
      </div>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ camera */

/* The browser opens the camera, not the server - so this page needs no camera
 * permission of its own and the frame never touches disk. The frame does leave
 * the device, to a hosted model, which is the one thing about this section
 * that must never be soft-pedalled: the banner says so before you press it. */

let stream = null;

function $(id) {
  return document.getElementById(id);
}

async function startCamera() {
  if (stream) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("this browser cannot open a camera from this page");
  }
  stream = await navigator.mediaDevices.getUserMedia({
    video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "environment" },
    audio: false,
  });
  const video = $("cam-video");
  video.srcObject = stream;
  video.hidden = false;
  $("cam-empty").hidden = true;
  await video.play();
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  const video = $("cam-video");
  video.srcObject = null;
  video.hidden = true;
  $("cam-empty").hidden = false;
}

/* Longest side capped: a bigger upload is not a better look at a kit, and the
 * model is billed per call. Matches MAX_CAMERA_SIDE on the host. */
const MAX_SIDE = 960;

function grabFrame() {
  const video = $("cam-video");
  if (!video.videoWidth) return null;
  const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function setStatus(text, busy) {
  const el = $("cam-status");
  el.textContent = text;
  el.dataset.busy = busy ? "1" : "";
}

async function inspectFromCamera() {
  const button = $("cam-inspect");
  button.disabled = true;

  try {
    await startCamera();
    setStatus("Hold the items still\u2026", true);
    // One beat so autoexposure settles; a frame grabbed the instant the camera
    // opens is usually a dark one, and a dark frame reads as INDETERMINATE.
    await new Promise((r) => setTimeout(r, 700));

    const canvas = grabFrame();
    if (!canvas) throw new Error("the camera produced no frame");

    $("cam-shot").src = canvas.toDataURL("image/jpeg", 0.85);
    $("cam-shot").hidden = false;
    $("cam-shot-empty").hidden = true;
    setStatus("Looking\u2026 this takes a few seconds", true);

    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.85));
    const response = await fetch("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: blob,
    });
    const payload = await response.json();

    if (!response.ok) {
      // An unconfigured deployment is not a shy verdict. Say which it is, and
      // point at the part of the page that still works.
      if (payload.unconfigured) {
        $("cam-readout").innerHTML =
          `<div class="live-note">${esc(payload.error)}</div>`;
        $("cam-readout").hidden = false;
        setStatus("Live inference unavailable.", false);
        return;
      }
      setStatus(payload.error || `the server answered ${response.status}`, false);
      return;
    }

    renderReadout(payload, "cam-readout");
    $("cam-readout").hidden = false;
    setStatus("", false);
  } catch (error) {
    const blocked = String(error.name || "").includes("NotAllowed");
    setStatus(
      blocked
        ? "Camera access was blocked. Allow it for this page in the address bar."
        : `Could not inspect: ${error.message}`,
      false,
    );
  } finally {
    button.disabled = false;
  }
}

function wireCamera() {
  $("cam-inspect").addEventListener("click", inspectFromCamera);
  $("cam-stop").addEventListener("click", () => {
    stopCamera();
    setStatus("Camera off.", false);
  });
}

async function boot() {
  const response = await fetch("./data/inspections.json");
  DATA = await response.json();

  document.getElementById("score-total").textContent = DATA.summary.total;
  document.getElementById("score-bad").textContent = DATA.summary.would_release_a_bad_kit;

  renderScenes();
  renderVoice();
  wireCamera();

  // Open on `expired`: a kit where every item is present and every tick is
  // green, and which fails anyway. It is the scene that makes the argument.
  const expired = DATA.scenes.findIndex((s) => s.scene === "expired");
  select(expired >= 0 ? expired : 0);

  document.querySelectorAll("[data-scene]").forEach((el) => {
    el.addEventListener("click", () => {
      const i = DATA.scenes.findIndex((s) => s.scene === el.dataset.scene);
      if (i >= 0) {
        select(i);
        document.getElementById("proof").scrollIntoView({ behavior: "smooth" });
      }
    });
  });
}

boot();
