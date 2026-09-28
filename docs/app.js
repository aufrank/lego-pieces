"use strict";

// Brick Round Planner: pick builds, swap colors, get one BrickLink order.
// Data (docs/data.enc.json) is AES-GCM encrypted by `uv run export-site`; see src/lego_pieces/site.py.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SOURCES = { SS: "Student Scissors", BM: "BrickMecha" };
const PASS_KEY = "brp.pass";
const STATE_KEY = "brp.state";
const SPEC_ROUNDS = 300;

let D = null;             // decrypted data
let BUILDS = new Map();   // id -> build
let AVAIL = new Map();    // part -> Set of colors it's made in
let S = defaultState();
let lastOrder = null;

function defaultState() {
  return { sel: [], per: {}, glob: [], spec: { SS: 0, BM: 0 }, cov: 0.75, joint: "cheapest", fallback: true, cond: "X",
           src: "all", q: "", sort: "title" };
}

// ---------- storage & sharing ----------
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* storage unavailable */ } },
};
const b64url = {
  enc: (s) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  dec: (s) => decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/")))),
};
function sharedState() {
  const { sel, per, glob, spec, cov, joint, fallback, cond } = S;
  return { sel, per, glob, spec, cov, joint, fallback, cond };
}
function saveState() { store.set(STATE_KEY, JSON.stringify(sharedState())); }
function loadState() {
  let loaded = null;
  const m = location.hash.match(/^#p=(.+)$/);
  if (m) { try { loaded = JSON.parse(b64url.dec(m[1])); } catch { /* bad link */ } }
  if (!loaded) { try { loaded = JSON.parse(store.get(STATE_KEY) || "null"); } catch { loaded = null; } }
  S = { ...defaultState(), ...(loaded || {}) };
  S.sel = S.sel.filter((id) => BUILDS.has(id));
  S.glob = (S.glob || []).filter((r) => Array.isArray(r) && r.length === 2);
}

// ---------- crypto ----------
async function decrypt(enc, pass) {
  const bytes = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: bytes(enc.salt), iterations: enc.iter, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: bytes(enc.iv) }, key, bytes(enc.data));
  const stream = new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

async function unlock(pass, remember) {
  const res = await fetch("data.enc.json", { cache: "no-cache" });
  if (!res.ok) throw new Error(`couldn't load data (${res.status})`);
  const enc = await res.json();
  let data;
  try { data = await decrypt(enc, pass); } catch { throw new Error("That passphrase didn't work."); }
  if (remember) store.set(PASS_KEY, pass); else store.del(PASS_KEY);
  start(data);
}

// ---------- data helpers ----------
const colorOf = (c) => D.colors[c] || { n: c.replace(/^name:/, ""), rgb: "999999", t: false, g: false, sel: false };
const partOf = (p) => D.parts[p] || { n: p, m: false, a: [], k: null };
const swatch = (c) => `<span class="sw${colorOf(c).t ? " trans" : ""}" style="background-color:#${colorOf(c).rgb}" title="${esc(colorOf(c).n)}"></span>`;
const isAvailable = (part, color) => AVAIL.get(part)?.has(color) ?? false;

function visibleColors(b) {
  const pcs = new Map();
  for (const [, color, qty, kind] of b.l) if (kind === "v") pcs.set(color, (pcs.get(color) || 0) + qty);
  return [...pcs.entries()].sort((a, z) => z[1] - a[1]);
}
function kindCounts(b) {
  const out = { v: 0, f: 0, x: 0 };
  for (const [, , qty, kind] of b.l) out[kind] += qty;
  return out;
}
function globalMap() {
  const m = new Map();
  for (const [from, to] of S.glob) if (from && to && from !== to) m.set(from, to);
  return m;
}
function buildMap(b, gmap) {
  const m = new Map(gmap);
  for (const [from, to] of Object.entries(S.per[b.id] || {})) if (to) m.set(from, to);
  return m;
}

// Resolve one lot to the color it'll be bought in. note: "kept" (swap color doesn't exist, fell back) or "notmade".
function resolveLot(part, color, kind, map) {
  if (kind === "x") return { color, note: null };
  const target = kind === "f" && S.joint === "cheapest" ? (partOf(part).k || color) : (map.get(color) || color);
  if (target === color || isAvailable(part, target)) return { color: target, note: null };
  return S.fallback ? { color, note: "kept", wanted: target } : { color: target, note: "notmade" };
}

function demandOf(b, map, notes) {
  const out = new Map();
  for (const [part, color, qty, kind] of b.l) {
    const r = resolveLot(part, color, kind, map);
    const key = `${part}|${r.color}`;
    out.set(key, (out.get(key) || 0) + qty);
    if (r.note && notes) notes.push({ build: b, part, from: color, to: r.wanted || r.color, qty, note: r.note });
  }
  return out;
}

// ---------- speculative stock ----------
function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function specStock(gmap) {
  const want = { SS: Math.max(0, S.spec.SS | 0), BM: Math.max(0, S.spec.BM | 0) };
  if (!want.SS && !want.BM) return new Map();
  const chosen = new Set(S.sel);
  const pools = { SS: [], BM: [] };
  for (const b of D.builds) if (!chosen.has(b.id)) pools[b.s].push(b);
  const demands = new Map();
  const demand = (b) => demands.get(b.id) || demands.set(b.id, [...demandOf(b, gmap).entries()]).get(b.id);
  const rand = mulberry32(20261225 + want.SS * 97 + want.BM);
  const counts = new Map();
  for (let r = 0; r < SPEC_ROUNDS; r++) {
    for (const src of ["SS", "BM"]) {
      const pool = pools[src];
      const n = Math.min(want[src], pool.length);
      const idx = pool.map((_, i) => i);
      for (let i = 0; i < n; i++) {
        const j = i + Math.floor(rand() * (idx.length - i));
        [idx[i], idx[j]] = [idx[j], idx[i]];
        for (const [key, qty] of demand(pool[idx[i]])) {
          let arr = counts.get(key);
          if (!arr) counts.set(key, (arr = new Uint16Array(SPEC_ROUNDS)));
          arr[r] += qty;
        }
      }
    }
  }
  const stock = new Map();
  const rank = Math.max(0, Math.ceil(S.cov * SPEC_ROUNDS) - 1);
  for (const [key, arr] of counts) {
    const q = [...arr].sort((a, z) => a - z)[rank];
    if (q > 0) stock.set(key, q);
  }
  return stock;
}

// ---------- the order ----------
function computeOrder() {
  const gmap = globalMap();
  const lines = new Map();
  const notes = [];
  const warnings = [];
  const add = (key, qty, field) => {
    let line = lines.get(key);
    if (!line) {
      const [part, color] = key.split("|");
      lines.set(key, (line = { part, color, sel: 0, spec: 0 }));
    }
    line[field] += qty;
  };
  for (const id of S.sel) {
    const b = BUILDS.get(id);
    const map = buildMap(b, gmap);
    const byTarget = new Map();
    for (const [color] of visibleColors(b)) {
      const t = map.get(color) || color;
      byTarget.set(t, [...(byTarget.get(t) || []), color]);
    }
    for (const [t, froms] of byTarget) {
      if (froms.length > 1) {
        const names = froms.map((c) => esc(colorOf(c).n));
        const list = names.length === 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
        warnings.push(`${esc(b.t)}: ${list} ${names.length === 2 ? "both" : "all"} become ${esc(colorOf(t).n)}, so those areas merge.`);
      }
    }
    for (const [key, qty] of demandOf(b, map, notes)) add(key, qty, "sel");
  }
  for (const [key, qty] of specStock(gmap)) add(key, qty, "spec");
  return { lines: [...lines.values()], notes, warnings };
}

// ---------- rendering ----------
function renderPicker() {
  $$("#sourceTabs button").forEach((btn) => btn.setAttribute("aria-selected", String(btn.dataset.src === S.src)));
  const q = S.q.trim().toLowerCase();
  let list = D.builds.filter((b) => (S.src === "all" || b.s === S.src) && (!q || b.t.toLowerCase().includes(q)));
  const sorters = {
    title: (a, z) => a.t.localeCompare(z.t),
    pieces: (a, z) => z.p - a.p,
    fewest: (a, z) => a.p - z.p,
    newest: (a, z) => (z.d || "").localeCompare(a.d || "") || a.t.localeCompare(z.t),
  };
  list.sort(sorters[S.sort] || sorters.title);
  const chosen = new Set(S.sel);
  $("#pickerCount").textContent = `${list.length} of ${D.builds.length}`;
  $("#buildList").innerHTML = list.map((b) => {
    const swatches = visibleColors(b).slice(0, 6).map(([c]) => swatch(c)).join("");
    return `<li><button type="button" data-build="${esc(b.id)}" aria-pressed="${chosen.has(b.id)}">
      <span class="title" title="${esc(b.t)}">${esc(b.t)}</span>
      <span class="add" aria-hidden="true">${chosen.has(b.id) ? "✓" : "+"}</span>
      <span class="meta"><span class="badge ${b.s}">${b.s === "SS" ? "SS" : "BM"}</span>${b.p} pcs <span class="swatches">${swatches}</span></span>
    </button></li>`;
  }).join("") || `<li class="empty">No builds match.</li>`;
}

// Options for a color's replacement. For a build's own swap, "" means "follow the all-builds swap, if any",
// and choosing the original color keeps it for this build even when an all-builds swap exists.
function colorOptions(fromColor, current, { perBuild = true, globalTo = null } = {}) {
  const trans = colorOf(fromColor).t;
  const targets = Object.entries(D.colors).filter(([, c]) => c.sel && c.t === trans).sort((a, z) => a[1].n.localeCompare(z[1].n));
  const opts = [];
  if (perBuild) {
    opts.push(globalTo
      ? `<option value="">${esc(colorOf(globalTo).n)} (all-builds swap)</option>
         <option value="${esc(fromColor)}"${current === fromColor ? " selected" : ""}>${esc(colorOf(fromColor).n)} (keep for this build)</option>`
      : `<option value="">${esc(colorOf(fromColor).n)} (as designed)</option>`);
  }
  for (const [id, c] of targets) {
    if (perBuild && id === fromColor) continue;
    opts.push(`<option value="${esc(id)}"${id === current ? " selected" : ""}>${esc(c.n)}</option>`);
  }
  return opts.join("");
}

function renderSelected() {
  const gmap = globalMap();
  if (!S.sel.length) {
    $("#selected").innerHTML = `<p class="empty">Pick builds on the left. Each one's colors show up here so you can swap them.</p>`;
    return;
  }
  $("#selected").innerHTML = S.sel.map((id) => {
    const b = BUILDS.get(id);
    const per = S.per[id] || {};
    const k = kindCounts(b);
    const rows = visibleColors(b).map(([color, pcs]) => {
      const own = per[color] || "";
      const effective = own || gmap.get(color) || color;
      return `<div class="swap-row">
        <span class="from">${swatch(color)}<span class="cname">${esc(colorOf(color).n)}</span><span class="pcs">${pcs}</span></span>
        <span class="to">${swatch(effective)}<select data-per="${esc(id)}" data-from="${esc(color)}" aria-label="New color for ${esc(colorOf(color).n)} in ${esc(b.t)}">${colorOptions(color, own, { globalTo: gmap.get(color) })}</select></span>
      </div>`;
    }).join("");
    const extras = [k.f && `${k.f} joint pieces (any color)`, k.x && `${k.x} kept as designed (special finish, tires, printed)`].filter(Boolean).join(" · ");
    return `<article class="card">
      <div class="card-head"><span class="badge ${b.s}">${b.s}</span><span class="name">${esc(b.t)}</span>
        <button type="button" class="icon" data-remove="${esc(id)}" aria-label="Remove ${esc(b.t)}">✕</button></div>
      <div class="card-meta">${b.p} pieces${extras ? " · " + extras : ""} · <a href="${esc(b.u)}" target="_blank" rel="noopener">instructions</a></div>
      <div class="swaps">${rows}</div>
    </article>`;
  }).join("");
}

function renderGlobal() {
  const present = new Set();
  for (const id of S.sel) for (const [c] of visibleColors(BUILDS.get(id))) present.add(c);
  const fromChoices = [...new Set([...present, ...Object.keys(D.colors).filter((c) => D.colors[c].sel)])]
    .sort((a, z) => (present.has(z) - present.has(a)) || colorOf(a).n.localeCompare(colorOf(z).n));
  $("#globalSwaps").innerHTML = S.glob.map(([from, to], i) => `
    <div class="global-row">
      <select data-glob-from="${i}" aria-label="Swap from">
        <option value="">Color…</option>
        ${fromChoices.map((c) => `<option value="${esc(c)}"${c === from ? " selected" : ""}>${esc(colorOf(c).n)}${present.has(c) ? "" : " (not in picks)"}</option>`).join("")}
      </select>
      <span class="muted">→ ${from && to ? swatch(to) : ""}</span>
      <select data-glob-to="${i}" aria-label="Swap to" ${from ? "" : "disabled"}>
        <option value="">New color…</option>${from ? colorOptions(from, to, { perBuild: false }) : ""}
      </select>
      <button type="button" class="icon" data-glob-del="${i}" aria-label="Remove swap">✕</button>
    </div>`).join("") || `<p class="hint">No swaps for all builds yet.</p>`;
}

function renderOrder() {
  const order = (lastOrder = computeOrder());
  const lines = order.lines.filter((l) => l.sel + l.spec > 0);
  const total = lines.reduce((s, l) => s + l.sel + l.spec, 0);
  const sel = lines.reduce((s, l) => s + l.sel, 0);
  const spec = total - sel;
  $("#summary").innerHTML = [
    [total, "pieces"], [lines.length, "lots"], [sel, "for picked builds"], [spec, "speculative stock"],
  ].map(([n, label]) => `<div class="stat"><b>${n.toLocaleString()}</b><span>${label}</span></div>`).join("");

  const byColor = new Map();
  for (const l of lines) {
    const g = byColor.get(l.color) || { color: l.color, qty: 0, lines: [] };
    g.qty += l.sel + l.spec;
    g.lines.push(l);
    byColor.set(l.color, g);
  }
  const groups = [...byColor.values()].sort((a, z) => z.qty - a.qty);
  $("#colorBar").innerHTML = groups.map((g) => `<span style="width:${(100 * g.qty) / (total || 1)}%;background:#${colorOf(g.color).rgb}" title="${esc(colorOf(g.color).n)}: ${g.qty}"></span>`).join("");

  const warn = [...order.warnings];
  const kept = order.notes.filter((n) => n.note === "kept");
  const notMade = order.notes.filter((n) => n.note === "notmade");
  const describe = (list) => list.slice(0, 8).map((n) => `${esc(partOf(n.part).n)} in ${esc(colorOf(n.to).n)} (${esc(n.build.t)})`).join("; ") + (list.length > 8 ? `; and ${list.length - 8} more` : "");
  if (kept.length) warn.push(`${kept.reduce((s, n) => s + n.qty, 0)} pieces kept their designed color because the part isn't made in the new one: ${describe(kept)}.`);
  if (notMade.length) warn.push(`${notMade.reduce((s, n) => s + n.qty, 0)} pieces are in colors those parts aren't made in (marked below): ${describe(notMade)}.`);
  const unlisted = lines.filter((l) => l.part.startsWith("ldraw:") || l.color.startsWith("name:"));
  if (unlisted.length) warn.push(`${unlisted.length} ${unlisted.length === 1 ? "lot has" : "lots have"} no BrickLink id and ${unlisted.length === 1 ? "is" : "are"} left out of the XML (${unlisted.length === 1 ? "it's" : "they're"} in the CSV).`);
  $("#warnings").innerHTML = warn.map((w) => `<div class="warn">${w}</div>`).join("");

  const notMadeKeys = new Set(notMade.map((n) => `${n.part}|${n.to}`));
  $("#orderTable").innerHTML = groups.map((g) => `
    <section class="group">
      <div class="group-head">${swatch(g.color)} ${esc(colorOf(g.color).n)}<span class="count">${g.qty} pcs · ${g.lines.length} lots</span></div>
      ${g.lines.sort((a, z) => (z.sel + z.spec) - (a.sel + a.spec)).map((l) => `
        <div class="line">
          <div class="line-main">
            <div class="pname">${esc(partOf(l.part).n)}${notMadeKeys.has(`${l.part}|${l.color}`) ? `<span class="flag">not made in this color</span>` : ""}</div>
            <div class="line-sub"><a href="https://www.bricklink.com/v2/catalog/catalogitem.page?P=${encodeURIComponent(l.part)}#T=C&C=${encodeURIComponent(l.color)}" target="_blank" rel="noopener">${esc(l.part)}</a>${l.sel && l.spec ? ` · ${l.sel} picked + ${l.spec} speculative` : l.spec ? " · speculative" : ""}</div>
          </div>
          <div class="qty">${l.sel + l.spec}</div>
        </div>`).join("")}
    </section>`).join("") || `<p class="empty">Your order shows up here once you pick builds or add speculative ones.</p>`;
}

function renderControls() {
  $("#search").value = S.q;
  $("#sort").value = S.sort;
  $("#specSS").value = S.spec.SS;
  $("#specBM").value = S.spec.BM;
  $("#cov").value = S.cov;
  $("#covOut").textContent = `${Math.round(S.cov * 100)}%`;
  $$('input[name="joint"]').forEach((r) => { r.checked = r.value === S.joint; });
  $("#fallback").checked = S.fallback;
  $("#cond").value = S.cond;
}

function renderPlan() {
  renderSelected();
  renderGlobal();
  renderOrder();
  saveState();
}

// ---------- exports ----------
function exportLines() {
  return (lastOrder?.lines || []).filter((l) => l.sel + l.spec > 0)
    .sort((a, z) => colorOf(a.color).n.localeCompare(colorOf(z.color).n) || a.part.localeCompare(z.part));
}
function bricklinkXml() {
  const items = exportLines().filter((l) => !l.part.startsWith("ldraw:") && !l.color.startsWith("name:")).map((l) =>
    `<ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>${esc(l.part)}</ITEMID><COLOR>${esc(l.color)}</COLOR><MINQTY>${l.sel + l.spec}</MINQTY><CONDITION>${S.cond}</CONDITION></ITEM>`);
  return `<INVENTORY>\n${items.join("\n")}\n</INVENTORY>\n`;
}
function csv() {
  const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
  const rows = [["bl_item", "part", "bl_color", "color", "qty", "for_picked_builds", "speculative"]];
  for (const l of exportLines()) rows.push([l.part, partOf(l.part).n, l.color, colorOf(l.color).n, l.sel + l.spec, l.sel, l.spec]);
  return rows.map((r) => r.map(q).join(",")).join("\n") + "\n";
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2200);
}
async function copyText(text, done) {
  try { await navigator.clipboard.writeText(text); toast(done); } catch { toast("Couldn't copy; use Download instead."); }
}

// ---------- events ----------
function wire() {
  $("#sourceTabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-src]");
    if (btn) { S.src = btn.dataset.src; renderPicker(); }
  });
  $("#search").addEventListener("input", (e) => { S.q = e.target.value; renderPicker(); });
  $("#sort").addEventListener("change", (e) => { S.sort = e.target.value; renderPicker(); });
  $("#buildList").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-build]");
    if (!btn) return;
    const id = btn.dataset.build;
    S.sel = S.sel.includes(id) ? S.sel.filter((x) => x !== id) : [...S.sel, id];
    renderPicker();
    renderPlan();
  });
  $("#selected").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-remove]");
    if (!btn) return;
    S.sel = S.sel.filter((x) => x !== btn.dataset.remove);
    delete S.per[btn.dataset.remove];
    renderPicker();
    renderPlan();
  });
  $("#selected").addEventListener("change", (e) => {
    const sel = e.target.closest("select[data-per]");
    if (!sel) return;
    const per = (S.per[sel.dataset.per] ||= {});
    if (sel.value) per[sel.dataset.from] = sel.value; else delete per[sel.dataset.from];
    renderPlan();
  });
  $("#addSwap").addEventListener("click", () => { S.glob.push(["", ""]); renderPlan(); });
  $("#globalSwaps").addEventListener("change", (e) => {
    const from = e.target.closest("select[data-glob-from]");
    const to = e.target.closest("select[data-glob-to]");
    if (from) S.glob[+from.dataset.globFrom] = [from.value, ""];
    if (to) S.glob[+to.dataset.globTo][1] = to.value;
    renderPlan();
  });
  $("#globalSwaps").addEventListener("click", (e) => {
    const del = e.target.closest("button[data-glob-del]");
    if (del) { S.glob.splice(+del.dataset.globDel, 1); renderPlan(); }
  });
  const clampInt = (v, max) => Math.max(0, Math.min(max, parseInt(v, 10) || 0));
  $("#specSS").addEventListener("change", (e) => { S.spec.SS = clampInt(e.target.value, 20); e.target.value = S.spec.SS; renderOrder(); saveState(); });
  $("#specBM").addEventListener("change", (e) => { S.spec.BM = clampInt(e.target.value, 40); e.target.value = S.spec.BM; renderOrder(); saveState(); });
  $("#cov").addEventListener("input", (e) => { S.cov = +e.target.value; $("#covOut").textContent = `${Math.round(S.cov * 100)}%`; });
  $("#cov").addEventListener("change", () => { renderOrder(); saveState(); });
  $$('input[name="joint"]').forEach((r) => r.addEventListener("change", () => { S.joint = r.value; renderPlan(); }));
  $("#fallback").addEventListener("change", (e) => { S.fallback = e.target.checked; renderPlan(); });
  $("#cond").addEventListener("change", (e) => { S.cond = e.target.value; saveState(); });

  $("#copyXml").addEventListener("click", () => copyText(bricklinkXml(), "BrickLink XML copied. Paste it at Want › Upload."));
  $("#dlXml").addEventListener("click", () => download("brick-order-bricklink.xml", bricklinkXml(), "application/xml"));
  $("#dlCsv").addEventListener("click", () => download("brick-order.csv", csv(), "text/csv"));
  $("#shareBtn").addEventListener("click", () => {
    const url = `${location.origin}${location.pathname}#p=${b64url.enc(JSON.stringify(sharedState()))}`;
    copyText(url, "Link copied. It holds the plan, not the passphrase.");
  });
  $("#resetBtn").addEventListener("click", () => {
    if (!confirm("Clear picked builds, swaps and settings?")) return;
    S = { ...defaultState(), src: S.src, sort: S.sort };
    history.replaceState(null, "", location.pathname);
    renderControls();
    renderPicker();
    renderPlan();
  });
  $("#lockBtn").addEventListener("click", () => { store.del(PASS_KEY); location.hash = ""; location.reload(); });
}

function start(data) {
  D = data;
  BUILDS = new Map(D.builds.map((b) => [b.id, b]));
  AVAIL = new Map(Object.entries(D.parts).map(([p, info]) => [p, new Set(info.a)]));
  loadState();
  if (location.hash) history.replaceState(null, "", location.pathname);
  $("#gate").hidden = true;
  $("#app").hidden = false;
  $("#topActions").hidden = false;
  const counts = { SS: 0, BM: 0 };
  for (const b of D.builds) counts[b.s]++;
  $("#dataNote").textContent = `${counts.SS} Student Scissors + ${counts.BM} BrickMecha builds · data from ${D.generated}`;
  wire();
  renderControls();
  renderPicker();
  renderPlan();
}

// ---------- boot ----------
$("#gateForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#unlockBtn");
  btn.disabled = true;
  $("#gateMsg").textContent = "Unlocking…";
  try { await unlock($("#pass").value, $("#remember").checked); } catch (err) { $("#gateMsg").textContent = err.message; }
  btn.disabled = false;
});
(async () => {
  const saved = store.get(PASS_KEY);
  if (!saved) return;
  $("#gateMsg").textContent = "Unlocking…";
  try { await unlock(saved, true); } catch { store.del(PASS_KEY); $("#gateMsg").textContent = ""; }
})();
