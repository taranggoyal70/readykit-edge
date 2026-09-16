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

  document.getElementById("readout").innerHTML = `
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

async function boot() {
  const response = await fetch("./data/inspections.json");
  DATA = await response.json();

  document.getElementById("score-total").textContent = DATA.summary.total;
  document.getElementById("score-bad").textContent = DATA.summary.would_release_a_bad_kit;

  renderScenes();
  renderVoice();

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
