// Assistant dashboard + phone app. No build step, no framework.
const $ = (s, el = document) => el.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};

const state = { conv: null, busy: false, settings: {}, name: "Claude", user: "" };

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && !opts.allow401) { showGate(); throw new Error("Please log in."); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

// Reads a POST response as Server-Sent Events.
async function* sse(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401) { showGate(); return; }
  if (!res.ok) { const d = await res.json().catch(() => ({})); yield { event: "error", data: { message: d.detail || `Error ${res.status}` } }; return; }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      let event = "message", data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

// ---------------------------------------------------------------- markdown (small and safe)
const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function inline(s) {
  return s
    .replace(/!\[([^\]]*)\]\((\/api\/screens\/[\w.-]+\.jpg)\)/g, '<a href="$2" target="_blank" rel="noopener"><img src="$2" alt="$1" loading="lazy"></a>')
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function markdown(src) {
  const out = [];
  const parts = esc(src).split(/```/);
  parts.forEach((part, idx) => {
    if (idx % 2 === 1) { out.push(`<pre><code>${part.replace(/^[\w+-]*\n/, "")}</code></pre>`); return; }
    const lines = part.split("\n");
    let list = null, para = [], table = [];
    const flushPara = () => { if (para.length) out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; };
    const flushList = () => { if (list) out.push(`<${list.type}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.type}>`); list = null; };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
      out.push(`<table>${rows.map((r, i) => `<tr>${r.map((c) => i ? `<td>${c}</td>` : `<th>${c}</th>`).join("")}</tr>`).join("")}</table>`);
      table = [];
    };
    for (const line of lines) {
      let m;
      if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); table.push(line.trim()); continue; }
      flushTable();
      if ((m = line.match(/^\s*[-*]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ul") { flushList(); list = { type: "ul", items: [] }; } list.items.push(m[1]); }
      else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (!list || list.type !== "ol") { flushList(); list = { type: "ol", items: [] }; } list.items.push(m[1]); }
      else if ((m = line.match(/^#{1,6}\s+(.*)/))) { flushPara(); flushList(); out.push(`<h3>${inline(m[1])}</h3>`); }
      else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
    }
    flushPara(); flushList(); flushTable();
  });
  return out.join("");
}

// ---------------------------------------------------------------- gate: login + first-run wizard
async function showGate() {
  $("#app").hidden = true;
  $("#settings").hidden = true;
  $("#gate").hidden = false;
  const s = await api("/api/state", { allow401: true });
  setName(s.name);
  if (!s.password_set) return wizardPassword();
  if (!s.logged_in) return loginForm();
  if (!s.setup_done) return wizardKeys();
  startApp();
}

function gateRender(...nodes) { const b = $("#gate-body"); b.replaceChildren(...nodes); $("input", b)?.focus(); }

function loginForm() {
  const err = h("div", { class: "err" });
  const pw = h("input", { class: "input", type: "password", autocomplete: "current-password", placeholder: "Password" });
  gateRender(h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try { await api("/api/login", { body: { password: pw.value }, allow401: true }); showGate(); }
    catch (x) { err.textContent = x.message; pw.select(); }
  } }, h("h1", {}, "Welcome back"), h("p", {}, "Enter your password."), pw, err,
    h("div", { class: "actions" }, h("button", { class: "btn primary" }, "Log in"))));
}

function wizardPassword() {
  const err = h("div", { class: "err" });
  const name = h("input", { class: "input", autocomplete: "given-name", placeholder: "First name" });
  const home = h("input", { class: "input", placeholder: "e.g. Oslo, Norway" });
  const pw = h("input", { class: "input", type: "password", autocomplete: "new-password", placeholder: "At least 10 characters" });
  const pw2 = h("input", { class: "input", type: "password", autocomplete: "new-password", placeholder: "Same again" });
  gateRender(h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    if (pw.value !== pw2.value) { err.textContent = "The two passwords don't match."; return; }
    try {
      await api("/api/setup/password", { body: { password: pw.value, user_name: name.value, home: home.value }, allow401: true });
      wizardKeys();
    } catch (x) { err.textContent = x.message; }
  } },
    h("div", { class: "step-count" }, "Setup · You"),
    h("h1", {}, "Hi. Let's get you set up."),
    h("p", {}, "Your name and where you live help the assistant answer well. Then choose a password: only your own devices can reach this page, and this is the second lock. Save it in your password manager."),
    h("div", { class: "grid2" }, h("div", { class: "field" }, h("label", {}, "Your name"), name),
      h("div", { class: "field" }, h("label", {}, "Where you live"), home)),
    h("div", { class: "field" }, h("label", {}, "Password"), pw),
    h("div", { class: "field" }, h("label", {}, "Confirm"), pw2), err,
    h("div", { class: "actions" }, h("button", { class: "btn primary" }, "Continue"))));
}

async function wizardKeys(index = 0) {
  const steps = await api("/api/setup/steps");
  if (index >= steps.length) return wizardPush();
  const s = steps[index];
  const err = h("div", { class: "err" });
  const input = h("input", { class: "input", type: "password", placeholder: s.set ? "Saved. Paste a new one to replace it." : s.placeholder, autocomplete: "off", spellcheck: "false" });
  const save = h("button", { class: "btn primary" }, "Save and continue");
  gateRender(h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    if (!input.value.trim()) {
      if (s.required && !s.set) { err.textContent = "This one is needed for the assistant to work."; return; }
      return wizardKeys(index + 1);
    }
    save.disabled = true; save.textContent = "Checking...";
    try { await api("/api/secrets", { body: { key: s.key, value: input.value } }); wizardKeys(index + 1); }
    catch (x) { err.textContent = x.message; save.disabled = false; save.textContent = "Save and continue"; }
  } },
    h("div", { class: "step-count" }, `Setup · ${index + 1} of ${steps.length + 1}`),
    h("h1", {}, s.title),
    h("p", {}, s.why),
    h("div", { class: "howto" }, s.how, " ", h("a", { href: s.link, target: "_blank", rel: "noopener" }, s.link_label + " ↗")),
    input, err,
    h("div", { class: "actions" }, save,
      !s.required || s.set ? h("button", { type: "button", class: "link", onclick: () => wizardKeys(index + 1) }, "Skip") : null)));
}

function wizardPush() {
  const err = h("div", { class: "err" });
  const finish = async () => { await api("/api/setup/done", { method: "POST" }); $("#gate").hidden = true; startApp(); };
  gateRender(
    h("div", { class: "step-count" }, "Setup · Last step"),
    h("h1", {}, "Notifications on this device"),
    h("p", {}, "Allow notifications so the assistant can tell you when something finishes. Do this on your phone too. On iPhone, first add this page to the Home Screen (Share > Add to Home Screen), then open it from there."),
    err,
    h("div", { class: "actions" },
      h("button", { class: "btn primary", onclick: async () => {
        try { await enablePush(); await finish(); } catch (x) { err.textContent = x.message; }
      } }, "Allow notifications"),
      h("button", { class: "link", onclick: finish }, "Not now")));
}

// ---------------------------------------------------------------- push notifications
function b64ToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}
async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window))
    throw new Error("This browser can't receive notifications. On iPhone, add the page to the Home Screen first.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notifications were blocked. Allow them in the browser's site settings.");
  const reg = await navigator.serviceWorker.ready;
  const { key } = await api("/api/push/key");
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await api("/api/push/subscribe", { body: sub.toJSON() });
}
async function pushEnabled() {
  try { const reg = await navigator.serviceWorker.ready; return !!(await reg.pushManager.getSubscription()) && Notification.permission === "granted"; }
  catch { return false; }
}

// ---------------------------------------------------------------- app
let started = false;
async function startApp() {
  $("#gate").hidden = true;
  $("#app").hidden = false;
  if (started) return;
  started = true;
  const st = await api("/api/settings");
  state.settings = st.settings;
  state.user = st.settings.user_name;
  setName(st.settings.assistant_name);
  const { id } = await api("/api/conversations/latest/id");
  await loadConversation(id);
  refreshNotifications(); refreshCosts(); refreshStatus();
  connectEvents();
  setInterval(refreshStatus, 30000);
}

function setName(name) {
  state.name = name || "Claude";
  document.querySelectorAll("[data-name]").forEach((el) => (el.textContent = state.name));
  document.title = state.name;
}

function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? "Good night" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
}

function renderEmpty() {
  const box = $("#messages");
  const ask = (t) => () => send(t);
  box.replaceChildren(h("div", { class: "empty" },
    h("h1", {}, state.user ? `${greeting()}, ${state.user}.` : `${greeting()}.`),
    h("p", {}, "Ask anything, by text or voice."),
    h("div", { class: "suggest" },
      h("button", { onclick: ask("What's the weather in Oslo this week?") }, "What's the weather in Oslo this week?"),
      h("button", { onclick: ask("Remember that I mostly make games in Godot.") }, "Remember that I mostly make games in Godot."),
      h("button", { onclick: ask("What can you do so far?") }, "What can you do so far?"))));
}

async function loadConversation(id) {
  state.conv = id;
  const conv = await api(`/api/conversations/${id}`);
  const box = $("#messages");
  box.replaceChildren();
  if (!conv.messages.length) return renderEmpty();
  for (const m of conv.messages) addMessage(m.role, m.text);
  box.scrollTop = box.scrollHeight;
}

function addMessage(role, text) {
  const box = $("#messages");
  $(".empty", box)?.remove();
  const bubble = h("div", { class: "bubble" });
  if (role === "assistant") bubble.innerHTML = markdown(text || "");
  else bubble.textContent = text;
  const wrap = h("div", { class: `msg ${role}` }, h("div", {}, bubble));
  box.append(wrap);
  box.scrollTop = box.scrollHeight;
  return { wrap, bubble, col: wrap.firstChild };
}

async function send(text) {
  text = text.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("#btn-send").disabled = true;
  addMessage("user", text);
  const { bubble, col } = addMessage("assistant", "");
  bubble.replaceChildren(h("span", { class: "typing" }, h("i"), h("i"), h("i")));
  const status = h("div", { class: "status", hidden: true });
  col.append(status);
  let reply = "";
  let raf = 0;
  const paint = () => { raf = 0; bubble.innerHTML = markdown(reply); $("#messages").scrollTop = 1e9; };
  try {
    for await (const { event, data } of sse("/api/chat", { text, conversation_id: state.conv })) {
      if (event === "meta") state.conv = data.conversation_id;
      else if (event === "delta") { reply += data.text; status.hidden = true; if (!raf) raf = requestAnimationFrame(paint); }
      else if (event === "status") { status.textContent = data.text; status.hidden = false; }
      else if (event === "error") { col.parentElement.classList.add("error"); reply += (reply ? "\n\n" : "") + data.message; paint(); }
      else if (event === "done") {
        status.remove();
        const meta = h("div", { class: "meta" }, data.instant ? `instant · ${data.ms ?? 0} ms` : data.cost_usd ? `$${data.cost_usd.toFixed(4)}` : "");
        col.append(meta);
      }
    }
  } catch (x) {
    col.parentElement.classList.add("error");
    reply += (reply ? "\n\n" : "") + "Lost the connection to the hub. Check that Tailscale is on.";
  }
  if (!reply) { col.parentElement.classList.add("error"); reply = "No reply came back. Try again."; }
  paint();
  status.remove();
  if (state.settings.speak_replies && reply) speak(reply);
  state.busy = false;
  $("#btn-send").disabled = false;
  refreshCosts();
}

// ---------------------------------------------------------------- composer
const input = $("#input");
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; }
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && matchMedia("(pointer: fine)").matches) {
    e.preventDefault(); $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = input.value; input.value = ""; autosize(); send(t);
});
$("#btn-new").addEventListener("click", async () => {
  const { id } = await api("/api/conversations", { method: "POST" });
  await loadConversation(id); input.focus();
});

// ---------------------------------------------------------------- voice: speech-to-text + level meter
const bars = [...document.querySelectorAll("#voice i")];
let rec = null, meter = null;
function setBars(levels) { bars.forEach((b, i) => (b.style.height = `${3 + Math.round(levels[i] * 15)}px`)); }

async function startMeter() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const an = ctx.createAnalyser(); an.fftSize = 256;
    ctx.createMediaStreamSource(stream).connect(an);
    const data = new Uint8Array(an.frequencyBinCount);
    const tick = () => {
      if (!meter) return;
      an.getByteFrequencyData(data);
      const band = (a, b) => data.slice(a, b).reduce((x, y) => x + y, 0) / (b - a) / 255;
      const lv = [band(2, 6), band(6, 12), band(12, 22), band(22, 36), band(36, 60)].map((v) => Math.min(1, v * 1.8));
      setBars([lv[3], lv[1], lv[0], lv[2], lv[4]]);
      meter.raf = requestAnimationFrame(tick);
    };
    meter = { stream, ctx, raf: 0 }; tick();
  } catch { meter = null; }
}
function stopMeter() {
  if (meter) { cancelAnimationFrame(meter.raf); meter.stream.getTracks().forEach((t) => t.stop()); meter.ctx.close(); }
  meter = null; setBars([0, 0, 0, 0, 0]);
}

function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast("Voice input", "This browser has no speech recognition. Chrome and Edge do; full voice comes in milestone 3."); return; }
  if (rec) { rec.stop(); return; }
  rec = new SR();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  let finalText = "";
  rec.onresult = (e) => {
    let interim = "";
    for (const r of e.results) (r.isFinal ? (finalText = r[0].transcript) : (interim += r[0].transcript));
    input.value = finalText || interim; autosize();
  };
  rec.onend = () => {
    rec = null; $("#btn-mic").classList.remove("listening"); $("#voice").classList.remove("live"); stopMeter();
    if (finalText.trim()) { input.value = ""; autosize(); send(finalText); }
  };
  rec.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") toast("Voice input", e.error); };
  rec.start();
  $("#btn-mic").classList.add("listening"); $("#voice").classList.add("live");
  startMeter();
}
$("#btn-mic").addEventListener("click", toggleMic);
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.ctrlKey) { e.preventDefault(); toggleMic(); }
});

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  const clean = text.replace(/```[\s\S]*?```/g, " (code) ").replace(/[*_`#>|]/g, "").replace(/\]\([^)]*\)/g, "]").slice(0, 1200);
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = /[æøå]/i.test(clean) ? "nb-NO" : "en-GB";
  speechSynthesis.cancel(); speechSynthesis.speak(u);
}

// ---------------------------------------------------------------- live events
function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("hello", () => $("#conn").classList.add("on"));
  es.addEventListener("notification", (e) => { const n = JSON.parse(e.data); toast(n.title, n.body); refreshNotifications(); });
  es.addEventListener("costs", () => refreshCosts());
  es.addEventListener("memory", () => { if (!$("#settings").hidden) renderMemories(); });
  es.addEventListener("pc", () => { refreshStatus(); pairWatch?.(); });
  es.addEventListener("pc_status", (e) => updatePcStatus(JSON.parse(e.data)));
  es.addEventListener("job", () => { clearTimeout(jobsTimer); jobsTimer = setTimeout(refreshJobs, 150); });
  es.addEventListener("job_log", (e) => appendJobLog(JSON.parse(e.data)));
  es.addEventListener("confirm", (e) => addConfirm(JSON.parse(e.data)));
  es.addEventListener("confirm_done", (e) => $(`#confirm-${JSON.parse(e.data).id}`)?.remove());
  es.onerror = () => $("#conn").classList.remove("on");
}
let jobsTimer = 0;

function toast(title, body) {
  const t = h("div", { class: "toast" }, h("b", {}, title), body ? h("span", {}, body) : null);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 5000);
}

const ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "now" : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
};

async function refreshNotifications() {
  const list = await api("/api/notifications");
  const box = $("#notifications");
  box.replaceChildren(...(list.length ? list.slice(0, 8).map((n) =>
    h("div", { class: `notif ${n.level}` }, h("b", {}, n.title), h("time", {}, ago(n.created)), n.body ? h("span", {}, n.body) : null))
    : [h("p", { class: "note" }, "Nothing yet.")]));
}

const usd = (n) => `$${n.toFixed(2)}`;
async function refreshCosts() {
  const c = await api("/api/costs");
  $("#cost-month").textContent = c.month;
  const rows = c.rows.map((r) => {
    const pct = r.cap ? Math.min(100, (r.spent / r.cap) * 100) : 0;
    return h("div", { class: "cost" },
      h("span", {}, r.name),
      h("span", { class: "amt" }, r.fixed ? `${usd(r.spent)} fixed` : r.cap ? `${usd(r.spent)} / ${usd(r.cap)}` : usd(r.spent)),
      r.cap ? h("div", { class: `bar ${pct >= 80 ? "warn" : ""}` }, h("i", { style: `width:${pct}%` })) : null);
  });
  rows.push(h("div", { class: "cost-total" }, h("span", {}, "This month"), h("span", {}, usd(c.total))));
  $("#costs").replaceChildren(...rows);
}

// ---------------------------------------------------------------- PC panel
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    renderPcs(s.agents);
    renderJobs(s.jobs);
    $("#confirms").replaceChildren();
    s.confirmations.forEach(addConfirm);
  } catch {}
}

function meterRow(label, v) {
  return [h("span", {}, label), h("div", { class: `bar ${v >= 85 ? "warn" : ""}` }, h("i", { style: `width:${v || 0}%` }))];
}

function renderPcs(list) {
  const box = $("#pc");
  if (!list.length) {
    box.replaceChildren(h("p", { class: "note" }, "No PC paired yet. Pair one to control it and run coding jobs."),
      h("div", { class: "chips" }, h("button", { class: "chip", onclick: openPairing }, "Pair a PC")));
    return;
  }
  box.replaceChildren(...list.map((a) => {
    const st = a.status || {};
    return h("div", { class: "pc", id: `pc-${a.id}` },
      h("div", { class: "row" }, h("span", { class: `dot ${a.online ? "on" : ""}` }), h("b", {}, a.name),
        h("span", { class: "muted mono", style: "font-size:11px" }, a.role === "relay" ? "relay" : a.os)),
      a.online && a.role !== "relay" ? h("div", { class: "meters", "data-meters": "" }, ...meterRow("CPU", st.cpu), ...meterRow("RAM", st.ram)) : null,
      a.online && st.active ? h("div", { class: "sub", "data-active": "" }, st.active) : null,
      !a.online ? h("div", { class: "sub" }, a.last_seen ? `Offline · last seen ${ago(a.last_seen)} ago` : "Offline") : null,
      a.role === "pc" ? h("div", { class: "chips" },
        a.online
          ? [h("button", { class: "chip", onclick: () => send("screenshot") }, "Screenshot"),
             h("button", { class: "chip", onclick: () => send("lock") }, "Lock"),
             h("button", { class: "chip", onclick: () => send("sleep") }, "Sleep")]
          : h("button", { class: "chip", onclick: async (e) => {
              e.target.disabled = true;
              try { const r = await api("/api/pc/wake", { method: "POST" }); toast("Wake", r.message); } finally { e.target.disabled = false; }
            } }, "Wake")) : null);
  }));
}

function updatePcStatus(s) {
  const el = $(`#pc-${s.agent_id}`);
  if (!el) return refreshStatus();
  const m = $("[data-meters]", el);
  if (m) m.replaceChildren(...meterRow("CPU", s.cpu), ...meterRow("RAM", s.ram));
  const act = $("[data-active]", el);
  if (act) act.textContent = s.active || "";
}

let pairWatch = null;
async function openPairing() {
  const { code, expires_in } = await api("/api/agent/pair-code", { method: "POST" });
  const hub = location.origin;
  const raw = "https://raw.githubusercontent.com/Sh4dwDev/claude-assistant/main/agent";
  const win = `$env:HUB='${hub}'; $env:PAIR='${code}'; irm ${raw}/install-windows.ps1 | iex`;
  const lin = `curl -fsSL ${raw}/install-linux.sh | HUB=${hub} PAIR=${code} bash`;
  const relay = `curl -fsSL ${raw}/install-linux.sh | HUB=${hub} PAIR=${code} ROLE=relay bash`;
  const copyRow = (cmd) => h("div", { class: "cmd" }, h("code", {}, cmd),
    h("button", { class: "btn", onclick: async (e) => { await navigator.clipboard.writeText(cmd); e.target.textContent = "Copied"; } }, "Copy"));
  const left = h("span", { class: "mono muted" });
  const until = Date.now() + expires_in * 1000;
  const tick = setInterval(() => {
    const s = Math.max(0, Math.round((until - Date.now()) / 1000));
    left.textContent = s ? `valid for ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "expired, close and try again";
    if (!s || $("#modal").hidden) clearInterval(tick);
  }, 1000);
  const before = (await api("/api/status")).agents.length;
  pairWatch = async () => {
    const now = (await api("/api/status")).agents;
    if (now.length > before && !$("#modal").hidden) { closeModal(); toast("Paired", `${now[now.length - 1].name} is connected.`); pairWatch = null; }
  };
  openModal("Pair a PC",
    h("p", { class: "muted" }, "On the PC, make sure Tailscale is on. Then paste one command. It installs the agent, pairs it with this code and starts it at every login."),
    h("div", { class: "pair-code" }, code), left,
    h("h2", { class: "group-title" }, "Windows: paste into PowerShell"), copyRow(win),
    h("h2", { class: "group-title" }, "CachyOS / Hyprland: paste into a terminal"), copyRow(lin),
    h("h2", { class: "group-title" }, "Optional: a Raspberry Pi or other always-on device at home"),
    h("p", { class: "note" }, "This makes “wake my PC” work while you're away. The hub is on a server outside your home, so it can't wake the PC on its own."),
    copyRow(relay));
}

function openModal(title, ...body) {
  $("#modal-title").textContent = title;
  $("#modal-body").replaceChildren(...body);
  $("#modal").hidden = false;
}
function closeModal() { $("#modal").hidden = true; openJob = null; }
$("#btn-close-modal").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
$("#btn-pair").addEventListener("click", openPairing);

// ---------------------------------------------------------------- jobs
async function refreshJobs() { try { renderJobs(await api("/api/jobs")); if (openJob) showJob(openJob, true); } catch {} }

function renderJobs(list) {
  const box = $("#jobs");
  if (!list.length) { box.replaceChildren(h("p", { class: "note" }, "No jobs yet. Ask for something to be built, e.g. “make me a Godot prototype of a space miner”.")); return; }
  box.replaceChildren(...list.slice(0, 6).map((j) => h("div", { class: "job", onclick: () => showJob(j.id) },
    h("span", { class: `dot ${j.status === "running" || j.status === "waking" ? "running" : j.status === "done" ? "on" : ""}` }),
    h("b", {}, j.title), h("span", { class: `st ${j.status}` }, j.status),
    j.last_log ? h("span", { class: "sub" }, j.last_log) : null)));
}

let openJob = null;
async function showJob(id, refreshOnly = false) {
  const j = await api(`/api/jobs/${id}`);
  openJob = id;
  const logbox = h("div", { class: "logbox", id: "job-log" },
    ...(j.logs.length ? j.logs.map((l) => h("div", {}, h("span", { class: "t" }, new Date(l.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })), l.text))
      : [h("div", { class: "t" }, "Waiting for the first output...")]));
  const active = ["queued", "waking", "running"].includes(j.status);
  const follow = h("input", { class: "input", placeholder: active ? "Wait for it to finish to add instructions" : "Follow-up instructions, e.g. add a score counter" , disabled: active || j.status === "cancelled" ? true : false });
  const cancelBtn = h("button", { class: "btn", onclick: async (e) => {
    if (e.target.dataset.sure) { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); return; }
    e.target.dataset.sure = "1"; e.target.textContent = "Tap again to cancel";
  } }, "Cancel job");
  const body = [
    h("div", { class: "row" }, h("span", { class: `st mono muted` }, `${j.status} · Projects/${j.project}`)),
    j.summary ? h("div", { class: "summary bubble" }) : null,
    logbox,
    h("div", { class: "actions" },
      active ? cancelBtn : null,
      !active && j.status !== "cancelled" ? h("form", { style: "display:flex;gap:8px;flex:1", onsubmit: async (e) => {
        e.preventDefault(); if (!follow.value.trim()) return;
        try { await api(`/api/jobs/${id}/followup`, { body: { text: follow.value } }); follow.value = ""; } catch (x) { toast("Follow-up", x.message); }
      } }, follow, h("button", { class: "btn primary" }, "Send")) : null),
  ];
  if (refreshOnly && !$("#modal").hidden && $("#modal-title").textContent === j.title) {
    $("#modal-body").replaceChildren(...body.filter(Boolean));
  } else if (!refreshOnly) openModal(j.title, ...body.filter(Boolean));
  if (j.summary) $(".summary", $("#modal-body")).innerHTML = markdown(j.summary);
  logbox.scrollTop = 1e9;
}

function appendJobLog({ id, text }) {
  if (openJob !== id) return;
  const box = $("#job-log");
  if (!box) return;
  box.append(h("div", {}, h("span", { class: "t" }, new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })), text));
  box.scrollTop = 1e9;
}

// ---------------------------------------------------------------- confirmations (ask-first actions)
function addConfirm(c) {
  if ($(`#confirm-${c.id}`)) return;
  const el = h("div", { class: "confirm", id: `confirm-${c.id}` }, h("span", {}, c.question),
    h("button", { class: "btn primary", onclick: () => answer(true) }, "Yes"),
    h("button", { class: "btn", onclick: () => answer(false) }, "No"));
  async function answer(approve) {
    el.remove();
    const r = await api(`/api/confirm/${c.id}`, { body: { approve, conversation_id: state.conv } });
    addMessage("assistant", r.message);
  }
  $("#confirms").append(el);
  setTimeout(() => el.remove(), (c.expires_in || 120) * 1000);
}

// ---------------------------------------------------------------- clock + tabs
function tickClock() { $("#clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
tickClock(); setInterval(tickClock, 10000);

$("#tabs").addEventListener("click", (e) => {
  const v = e.target.closest("button")?.dataset.view;
  if (!v) return;
  if (v === "settings") return openSettings();
  $("#app").dataset.view = v;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
});

// ---------------------------------------------------------------- settings
$("#btn-settings").addEventListener("click", openSettings);
$("#btn-close-settings").addEventListener("click", () => ($("#settings").hidden = true));
$("#settings").addEventListener("click", (e) => { if (e.target.id === "settings") $("#settings").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#settings").hidden = true; });

const field = (label, el) => h("div", { class: "field" }, h("label", {}, label), el);

async function openSettings() {
  $("#settings").hidden = false;
  const { settings: s, secrets, models } = await api("/api/settings");
  const steps = await api("/api/setup/steps");
  const inp = (k, type = "text", extra = {}) => h("input", { class: "input", type, value: s[k], "data-k": k, ...extra });
  const sel = (k, opts) => { const el = h("select", { class: "input", "data-k": k }, ...opts.map(([v, t]) => h("option", { value: v }, t))); el.value = s[k]; return el; };
  const saveBtn = h("button", { class: "btn primary" }, "Save");
  const msg = h("span", { class: "muted" });

  const general = h("div", { class: "group" }, h("h2", {}, "General"),
    h("div", { class: "grid2" },
      field("Assistant name", inp("assistant_name")),
      field("Your name", inp("user_name")),
      field("Where you live", inp("home")),
      field("Time zone", inp("timezone")),
      field("Chat model", sel("chat_model", models.map((m) => [m, m]))),
      field("Thinking effort", sel("chat_effort", [["low", "Low (fast, cheap)"], ["medium", "Medium"], ["high", "High"]]))),
    h("label", { class: "toggle" }, h("input", { type: "checkbox", "data-k": "speak_replies", checked: s.speak_replies }), "Read replies aloud"));

  const caps = h("div", { class: "group" }, h("h2", {}, "Monthly costs and caps"),
    h("div", { class: "grid2" },
      field("Server (EUR per month)", inp("server_cost_eur", "number", { step: "0.01" })),
      field("Claude API cap (USD)", inp("cap_claude_api_usd", "number", { step: "1" })),
      field("Calls and SMS cap (USD)", inp("cap_phone_usd", "number", { step: "1" })),
      field("Voice TTS cap (USD)", inp("cap_tts_usd", "number", { step: "1" })),
      field("PC auto-sleep after idle (min, 0 = off)", inp("pc_idle_sleep_min", "number", { step: "5", min: "0" }))),
    h("div", { class: "actions" }, saveBtn, msg));

  saveBtn.addEventListener("click", async () => {
    const body = {};
    document.querySelectorAll("#settings-body [data-k]").forEach((el) => {
      const k = el.dataset.k;
      body[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? parseFloat(el.value) : el.value;
    });
    try { await api("/api/settings", { body }); state.settings = { ...state.settings, ...body }; state.user = body.user_name; setName(body.assistant_name); msg.textContent = "Saved."; }
    catch (x) { msg.textContent = x.message; }
  });
  // save button lives in caps group but saves both groups
  general.append(h("p", { class: "note" }, "Opus is the smartest; Sonnet costs half; Haiku is the cheapest. Changes save with the button below."));

  const keys = h("div", { class: "group" }, h("h2", {}, "Keys and connections"),
    ...steps.map((st) => h("div", { class: "secret-row" },
      h("span", { class: `dot ${secrets[st.key] ? "on" : ""}` }), st.title,
      h("button", { class: "chip", onclick: () => { $("#settings").hidden = true; $("#app").hidden = true; $("#gate").hidden = false; wizardKeyOne(st); } }, secrets[st.key] ? "Replace" : "Add"))),
    h("p", { class: "note" }, "Stored only in data/.env on your server."));

  const pushOn = await pushEnabled();
  const notif = h("div", { class: "group" }, h("h2", {}, "Notifications"),
    h("div", { class: "secret-row" }, h("span", { class: `dot ${pushOn ? "on" : ""}` }), "This device",
      h("button", { class: "chip", onclick: async (e) => {
        try { await enablePush(); e.target.textContent = "On"; toast("Notifications", "This device will get alerts."); } catch (x) { toast("Notifications", x.message); }
      } }, pushOn ? "Re-enable" : "Enable")),
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => api("/api/notifications/test", { method: "POST" }) }, "Send a test")));

  const mem = h("div", { class: "group" }, h("h2", {}, "Memory"), h("div", { id: "mem-list" }),
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: async () => { await api("/api/memories", { body: { text: "New memory", category: "general" } }); renderMemories(); } }, "Add memory")));

  const screen = h("div", { class: "group" }, h("h2", {}, "This screen"),
    h("div", { class: "actions" },
      h("button", { class: "btn", onclick: () => document.documentElement.requestFullscreen?.() }, "Full screen"),
      h("button", { class: "btn", onclick: async () => { await api("/api/logout", { method: "POST" }); location.reload(); } }, "Log out")));

  const { agents: pcs } = await api("/api/status");
  const devices = h("div", { class: "group" }, h("h2", {}, "Paired devices"),
    ...(pcs.length ? pcs.map((a) => h("div", { class: "secret-row" }, h("span", { class: `dot ${a.online ? "on" : ""}` }),
      `${a.name} (${a.role === "relay" ? "wake relay" : a.os})`,
      h("button", { class: "chip", onclick: async (e) => {
        if (!e.target.dataset.sure) { e.target.dataset.sure = 1; e.target.textContent = "Tap again to remove"; return; }
        await api(`/api/pc/${a.id}/forget`, { method: "POST" }); openSettings();
      } }, "Remove"))) : [h("p", { class: "note" }, "None yet.")]),
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => { $("#settings").hidden = true; openPairing(); } }, "Pair a device")));

  $("#settings-body").replaceChildren(general, caps, devices, mem, notif, keys, screen);
  renderMemories();
}

async function wizardKeyOne(step) {
  const err = h("div", { class: "err" });
  const input = h("input", { class: "input", type: "password", placeholder: step.placeholder, autocomplete: "off" });
  const back = () => { $("#gate").hidden = true; $("#app").hidden = false; openSettings(); };
  gateRender(h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/api/secrets", { body: { key: step.key, value: input.value } }); back(); } catch (x) { err.textContent = x.message; }
  } }, h("h1", {}, step.title), h("p", {}, step.why),
    h("div", { class: "howto" }, step.how, " ", h("a", { href: step.link, target: "_blank", rel: "noopener" }, step.link_label + " ↗")),
    input, err,
    h("div", { class: "actions" }, h("button", { class: "btn primary" }, "Save"), h("button", { type: "button", class: "link", onclick: back }, "Cancel"))));
}

const CATS = ["preference", "project", "person", "fact", "general"];
async function renderMemories() {
  const box = $("#mem-list");
  if (!box) return;
  const list = await api("/api/memories");
  box.replaceChildren(...(list.length ? list.map((m) => {
    const ta = h("textarea", { class: "input" }); ta.value = m.text;
    const cat = h("select", { class: "input" }, ...CATS.map((c) => h("option", { value: c }, c))); cat.value = m.category;
    const save = () => api(`/api/memories/${m.id}`, { method: "PUT", body: { text: ta.value, category: cat.value } });
    ta.addEventListener("change", save); cat.addEventListener("change", save);
    return h("div", { class: "mem" }, cat, ta, h("button", { class: "chip", onclick: async () => { await api(`/api/memories/${m.id}`, { method: "DELETE" }); renderMemories(); } }, "Delete"));
  }) : [h("p", { class: "note" }, "Nothing saved yet. Tell the assistant something to remember.")]));
}

// ---------------------------------------------------------------- boot
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
showGate();
