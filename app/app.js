/*
  AmperEC phone app (a web app you add to the home screen; works on iPhone and Android).
  Reads the ESP32 readings and the power save settings/log from Supabase, after signing in.
  Demo mode with made-up data: open the page with ?demo at the end of the address.
*/
(function () {
  "use strict";

  const CFG = window.AMPEREC_CONFIG || {};
  const DEVICE = CFG.device || "esp32-lab-1";
  const VOLTS = CFG.volts || 12;
  const OFFLINE_AFTER_S = 30;
  const DEMO = new URLSearchParams(location.search).has("demo");

  // main color + partner color for the logo; the same choices as the website, plus Blue
  const COLORS = {
    Teal: ["#3FE0D0", "#7CF29B"],
    Blue: ["#4DA3FF", "#7CE7FF"],
    Red: ["#FF5362", "#FF9AA4"],
    Lime: ["#D4FF3A", "#7EDB1F"],
    Gold: ["#FFC845", "#FF9A1F"],
    Violet: ["#BDA4FF", "#7AC8FF"],
  };
  const EVENT_TEXT = {
    warning: "Warning: lamp 2 will switch off soon",
    cancelled: "Cancelled",
    turned_off: "Lamp 2 switched off",
    would_turn_off: "Would switch lamp 2 off (no relay yet)",
    restored: "Lamp 2 switched back on",
  };

  const app = document.getElementById("app");
  let sb = null;
  let tab = "now";
  let timer = null;
  let data = { readings: [], settings: null, events: [], error: null };
  let draft = null; // power save settings being edited
  let isAdmin = false; // only the owner (table app_admins) may change power save; new accounts only view
  let loginMode = "signin";

  // ---------- small helpers ----------
  function store(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; }
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function fmtTime(d) { return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  function ago(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + " s ago";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return Math.floor(s / 86400) + " days ago";
  }
  function toast(msg) {
    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }

  function setColor(name) {
    const c = COLORS[name] || COLORS.Teal;
    document.documentElement.style.setProperty("--accent", c[0]);
    document.documentElement.style.setProperty("--accent2", c[1]);
    store("amperec_color", name);
  }
  function colorName() { const n = store("amperec_color"); return COLORS[n] ? n : "Teal"; }

  // the logo mark: A with a lightning bolt, a circuit E, and a C with a leaf; gradient main -> partner color
  let gid = 0;
  function mark(height) {
    const id = "g" + (gid++);
    return `<svg height="${height}" viewBox="0 0 280 150" aria-hidden="true">
      <defs><linearGradient id="${id}" x1="0" x2="1" y1="0" y2="0">
        <stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent2)"/></linearGradient></defs>
      <g fill="none" stroke="url(#${id})" stroke-width="9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M30 136 L72 16 L114 136"/>
        <path d="M58 62 L38 98 H62 L44 138" stroke-width="7"/>
        <path d="M96 92 C112 92 116 68 132 68"/>
        <path d="M178 22 H132 V128 H178 M132 75 H166"/>
        <path d="M262 44 A50 50 0 1 0 262 106"/>
      </g>
      <g fill="url(#${id})">
        <circle cx="178" cy="22" r="7"/><circle cx="178" cy="128" r="7"/><circle cx="166" cy="75" r="6"/><circle cx="262" cy="44" r="7"/><circle cx="262" cy="106" r="7"/>
        <path d="M218 98 C200 84 206 58 236 54 C240 80 230 96 218 98 Z"/>
        <circle cx="252" cy="62" r="3"/><circle cx="256" cy="74" r="3"/><circle cx="255" cy="86" r="3"/>
      </g>
      <path d="M218 98 L230 66" stroke="var(--ground)" stroke-width="3" stroke-linecap="round"/>
    </svg>`;
  }
  function wordmark(size) {
    return `<div><div class="word" style="font-size:${size}px"><span class="am">AMPER</span><span class="ec">EC</span></div>
      <div class="tag">SMART ELECTRICAL CONSUMPTION</div></div>`;
  }

  const ICONS = {
    now: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3L4 14h7l-1 7 9-11h-7l1-7z"/></svg>',
    alerts: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 01-3.4 0"/></svg>',
    save: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="18" height="10" rx="2"/><path d="M22 11v2"/><path d="M11 9l-2 3h3l-2 3"/></svg>',
  };

  // ---------- data ----------
  function demoData() {
    const now = Date.now();
    const readings = [];
    for (let i = 720; i > 0; i--) {
      const t = now - i * 5000;
      const a = i > 420 ? 0.07 : i > 200 ? 3.52 : 1.76;
      readings.push({ created_at: new Date(t).toISOString(), current_a: a + 0.04 * Math.sin(i / 3), peak_a: a + 0.2,
                      threshold_a: 1, over_threshold: a > 1, rssi_dbm: -52 });
    }
    const ev = (m, e, a) => ({ created_at: new Date(now - m * 60000).toISOString(), event: e, current_a: a });
    const events = [ev(25, "warning", 3.5), ev(24, "turned_off", 3.5), ev(6, "restored", 1.8), ev(0.3, "warning", 3.5)];
    const settings = data.settings && data.settings._demo ? data.settings
      : { _demo: true, enabled: true, limit_a: 3, after_min: 10, warn_s: 60, lamp_w: 21 };
    return { readings, events, settings, error: null };
  }

  async function load() {
    if (DEMO) { data = demoData(); return; }
    const since = (h) => new Date(Date.now() - h * 3600000).toISOString();
    try {
      const [r, s, e] = await Promise.all([
        sb.from("sensor_readings").select("created_at,current_a,peak_a,threshold_a,over_threshold,rssi_dbm")
          .eq("device_id", DEVICE).gte("created_at", since(1)).order("created_at", { ascending: true }).limit(2000),
        sb.from("power_save_settings").select("enabled,limit_a,after_min,warn_s,lamp_w").eq("device_id", DEVICE),
        sb.from("power_save_events").select("created_at,event,current_a")
          .eq("device_id", DEVICE).gte("created_at", since(24)).order("created_at", { ascending: true }).limit(500),
      ]);
      const err = r.error || s.error || e.error;
      data = {
        readings: r.data || [],
        settings: (s.data && s.data[0]) || { enabled: false, limit_a: 3, after_min: 10, warn_s: 60, lamp_w: 21 },
        events: e.data || [],
        error: err ? (err.message || "Could not load the data.") : null,
      };
    } catch (x) {
      data.error = "No internet connection.";
    }
  }

  async function saveSettings(fields) {
    if (DEMO) { Object.assign(data.settings, fields); return null; }
    const { error } = await sb.from("power_save_settings")
      .upsert({ device_id: DEVICE, ...fields, updated_at: new Date().toISOString() }, { onConflict: "device_id" });
    return error ? error.message : null;
  }

  // same rules as powersave.status() on the website
  function powerState() {
    const s = data.settings || {};
    const last = data.events.length ? data.events[data.events.length - 1] : null;
    if (last && last.event === "turned_off") return { state: "turned_off", since: new Date(last.created_at) };
    if (!s.enabled) return { state: "off" };
    if (last && last.event === "warning") {
      const left = s.warn_s - (Date.now() - new Date(last.created_at).getTime()) / 1000;
      if (left > -15) return { state: "warning", left: Math.max(0, Math.round(left)), since: new Date(last.created_at) };
    }
    return { state: "watching" };
  }
  function savedWh() {
    let total = 0, off = null;
    for (const e of data.events) {
      const t = new Date(e.created_at).getTime();
      if (e.event === "turned_off" && off === null) off = t;
      else if (e.event === "restored" && off !== null) { total += t - off; off = null; }
    }
    if (off !== null) total += Date.now() - off;
    return (data.settings ? data.settings.lamp_w : 21) * total / 3600000;
  }
  // groups of readings above the limit, newest first
  function overEpisodes() {
    const out = [];
    let cur = null;
    for (const r of data.readings) {
      if (r.over_threshold) {
        if (!cur) { cur = { start: r.created_at, peak: r.current_a, limit: r.threshold_a }; out.push(cur); }
        cur.peak = Math.max(cur.peak, r.current_a);
        cur.end = r.created_at;
      } else cur = null;
    }
    return out.reverse();
  }

  // ---------- screens ----------
  function chart(readings) {
    if (readings.length < 2) return '<div class="empty small">Not enough readings yet.</div>';
    const W = 320, H = 120, P = 4;
    const t0 = new Date(readings[0].created_at).getTime(), t1 = new Date(readings[readings.length - 1].created_at).getTime();
    const limit = readings[readings.length - 1].threshold_a || 1;
    const max = Math.max(limit * 1.3, ...readings.map((r) => r.current_a)) * 1.1;
    const x = (t) => P + (W - 2 * P) * (t - t0) / Math.max(t1 - t0, 1);
    const y = (a) => H - P - (H - 2 * P) * a / max;
    const pts = readings.map((r) => x(new Date(r.created_at).getTime()).toFixed(1) + "," + y(r.current_a).toFixed(1)).join(" ");
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Current over the last hour">
      <line x1="0" x2="${W}" y1="${y(limit)}" y2="${y(limit)}" stroke="var(--danger)" stroke-dasharray="4 4" stroke-width="1"/>
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linejoin="round"/></svg>
      <div class="row small muted"><span>${fmtTime(new Date(t0))}</span><span>limit ${limit.toFixed(1)} A</span><span>${fmtTime(new Date(t1))}</span></div>`;
  }

  function warningCard(ps) {
    return `<div class="card alertcard"><div class="t">Lamp 2 switches off in ${ps.left} s</div>
      <div class="lbl" style="margin:4px 0 ${isAdmin ? 12 : 0}px">Power save: current above ${data.settings.limit_a} A for ${data.settings.after_min} min</div>
      ${isAdmin ? '<button class="btn" data-act="cancel">Cancel</button>' : ""}</div>`;
  }
  function restoreButton() {
    return isAdmin ? '<button class="btn" data-act="restore" style="margin-top:12px">Turn lamp 2 back on</button>' : "";
  }

  function screenNow() {
    const r = data.readings, last = r[r.length - 1];
    const ps = powerState();
    let html = `<div class="head"><div class="brand">${mark(34)}${wordmark(17)}</div></div>`;
    if (data.error) html += `<div class="card alertcard"><div class="t">${esc(data.error)}</div></div>`;
    if (ps.state === "warning") html += warningCard(ps);
    if (!last) {
      return html + `<div class="card empty">No readings in the last hour.<br>Turn on the ESP32: it sends one reading every 5 seconds.</div>`;
    }
    const age = (Date.now() - new Date(last.created_at).getTime()) / 1000;
    const online = age <= OFFLINE_AFTER_S;
    const peak = Math.max(...r.map((x) => x.peak_a));
    const alerts = r.filter((x) => x.over_threshold).length;
    html += `<div class="card row"><span><span class="dot" style="background:${online ? "var(--ok)" : "var(--danger)"}"></span>${esc(DEVICE)} ${online ? "online" : "offline"}</span>
      <span class="pill">${ago(age)}</span></div>`;
    if (online && last.over_threshold) html += `<div class="card alertcard"><div class="t">Current above the limit (${last.current_a.toFixed(2)} A &gt; ${last.threshold_a.toFixed(2)} A)</div></div>`;
    html += `<div class="card center"><div class="lbl">Current now</div><div class="big">${last.current_a.toFixed(2)} A</div>
      <div class="lbl">about ${(last.current_a * VOLTS).toFixed(1)} W at ${VOLTS} V</div></div>
      <div class="card"><div class="lbl" style="margin-bottom:8px">Last hour</div>${chart(r)}</div>
      <div class="grid2"><div class="card"><div class="lbl">Peak</div><div class="mid">${peak.toFixed(2)} A</div></div>
      <div class="card"><div class="lbl">Readings above limit</div><div class="mid">${alerts}</div></div></div>`;
    return html;
  }

  function screenAlerts() {
    const ps = powerState();
    let html = `<div class="head"><h1>Alerts</h1></div>`;
    if (ps.state === "warning") html += warningCard(ps);
    if (ps.state === "turned_off") {
      html += `<div class="card alertcard"><div class="t">Lamp 2 is off (power save, ${fmtTime(ps.since)})</div>${restoreButton()}</div>`;
    }
    const items = [];
    for (const e of data.events) items.push({ t: new Date(e.created_at), title: EVENT_TEXT[e.event] || e.event, sub: `Power save · ${(e.current_a || 0).toFixed(2)} A` });
    for (const o of overEpisodes()) items.push({ t: new Date(o.start), title: `Current above ${o.limit.toFixed(1)} A`, sub: `Peak ${o.peak.toFixed(2)} A · until ${fmtTime(new Date(o.end))}` });
    items.sort((a, b) => b.t - a.t);
    if (!items.length) return html + `<div class="card empty">No alerts in the last 24 hours.</div>`;
    return html + items.slice(0, 60).map((i) => `<div class="card"><div class="row"><span>${esc(i.title)}</span><span class="small muted">${fmtTime(i.t)}</span></div>
      <div class="lbl" style="margin-top:3px">${esc(i.sub)}</div></div>`).join("");
  }

  function stepper(key, value, unit, step, digits) {
    return `<div class="stepper"><button data-step="${key}" data-d="-${step}" aria-label="Less">−</button>
      <span>${Number(value).toFixed(digits)} ${unit}</span><button data-step="${key}" data-d="${step}" aria-label="More">+</button></div>`;
  }

  function screenSave() {
    if (!draft) draft = { ...(data.settings || {}) };
    const ps = powerState();
    const times = data.events.filter((e) => e.event === "turned_off").length;
    let html = `<div class="head"><h1>Power save</h1></div>`;
    if (ps.state === "warning") html += warningCard(ps);
    if (ps.state === "turned_off") {
      html += `<div class="card alertcard"><div class="t">Lamp 2 is off since ${fmtTime(ps.since)}</div>${restoreButton()}</div>`;
    }
    if (!isAdmin) {
      const s = data.settings || {};
      return html + `<div class="card"><div class="field"><span>Power save mode</span><span class="pill">${s.enabled ? "On" : "Off"}</span></div>
        <div class="field"><span class="lbl">Switch off above</span><span>${Number(s.limit_a).toFixed(1)} A</span></div>
        <div class="field"><span class="lbl">For more than</span><span>${Number(s.after_min).toFixed(0)} min</span></div>
        <div class="field"><span class="lbl">Warning first</span><span>${Number(s.warn_s).toFixed(0)} s</span></div></div>
        <p class="small muted center" style="margin:0 0 12px">Only the owner can change power save.</p>
        <div class="grid2" style="margin-bottom:12px"><div class="card"><div class="lbl">Saved (24 h)</div><div class="mid" style="color:var(--accent)">${savedWh().toFixed(1)} Wh</div></div>
        <div class="card"><div class="lbl">Times off (24 h)</div><div class="mid">${times}</div></div></div>
        ${colorCard()}${DEMO ? "" : '<button class="btn ghost" data-act="logout">Sign out</button>'}`;
    }
    html += `<div class="card"><div class="field"><span>Power save mode</span>
        <button class="switch ${draft.enabled ? "on" : ""}" data-act="toggle" aria-label="Power save mode" aria-pressed="${!!draft.enabled}"></button></div>
      <div class="field"><span class="lbl">Switch off above</span>${stepper("limit_a", draft.limit_a, "A", 0.5, 1)}</div>
      <div class="field"><span class="lbl">For more than</span>${stepper("after_min", draft.after_min, "min", 1, 0)}</div>
      <div class="field"><span class="lbl">Warning first</span>${stepper("warn_s", draft.warn_s, "s", 10, 0)}</div>
      <div class="field"><span class="lbl">Lamp 2 power</span>${stepper("lamp_w", draft.lamp_w, "W", 1, 0)}</div></div>
      <button class="btn" data-act="save" style="margin-bottom:12px">Save</button>
      <div class="grid2" style="margin-bottom:12px"><div class="card"><div class="lbl">Saved (24 h)</div><div class="mid" style="color:var(--accent)">${savedWh().toFixed(1)} Wh</div></div>
      <div class="card"><div class="lbl">Times off (24 h)</div><div class="mid">${times}</div></div></div>
      ${colorCard()}
      ${DEMO ? "" : '<button class="btn ghost" data-act="logout">Sign out</button>'}`;
    return html;
  }
  function colorCard() {
    return `<div class="card"><div class="lbl" style="margin-bottom:10px">App color</div><div class="swatches">
      ${Object.keys(COLORS).map((n) => `<button class="swatch ${n === colorName() ? "sel" : ""}" data-color="${n}" aria-label="${n}" style="background:${COLORS[n][0]}"></button>`).join("")}
      </div></div>`;
  }

  function render() {
    const body = tab === "now" ? screenNow() : tab === "alerts" ? screenAlerts() : screenSave();
    app.innerHTML = `${DEMO ? '<div class="demo">DEMO</div>' : ""}<div class="screen">${body}</div>
      <nav class="tabs"><div class="in">
      ${[["now", "Now"], ["alerts", "Alerts"], ["save", "Power save"]].map(([k, n]) =>
        `<button class="${tab === k ? "sel" : ""}" data-tab="${k}">${ICONS[k]}<span>${n}</span></button>`).join("")}
      </div></nav>`;
  }

  async function refresh() {
    await load();
    if (document.querySelector(".login")) return; // signed out meanwhile
    render();
  }

  // ---------- actions ----------
  app.addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    if (b.dataset.tab) { tab = b.dataset.tab; if (tab !== "save") draft = null; render(); window.scrollTo(0, 0); return; }
    if (b.dataset.color) { setColor(b.dataset.color); render(); return; }
    if (b.dataset.step) {
      const k = b.dataset.step, lim = { limit_a: [0.5, 30], after_min: [1, 1440], warn_s: [10, 600], lamp_w: [1, 5000] }[k];
      draft[k] = Math.min(lim[1], Math.max(lim[0], Math.round((Number(draft[k]) + Number(b.dataset.d)) * 10) / 10));
      render();
      return;
    }
    const act = b.dataset.act;
    if (act === "toggle") { draft.enabled = !draft.enabled; render(); return; }
    if (act === "save") {
      const err = await saveSettings({ enabled: !!draft.enabled, limit_a: draft.limit_a, after_min: draft.after_min, warn_s: draft.warn_s, lamp_w: draft.lamp_w });
      toast(err ? "Not saved: " + err : "Saved. The ESP32 picks it up within about 10 seconds.");
      draft = null; await refresh();
    }
    if (act === "cancel") {
      const until = new Date(Date.now() + (data.settings.after_min || 10) * 60000).toISOString();
      if (DEMO) data.events.push({ created_at: new Date().toISOString(), event: "cancelled", current_a: 3.5 });
      const err = DEMO ? null : await saveSettings({ snooze_until: until });
      toast(err ? "Not cancelled: " + err : "Cancelled.");
      if (!DEMO) await refresh(); else render();
    }
    if (act === "restore") {
      const err = await saveSettings({ restore_at: new Date().toISOString() });
      toast(err ? "Failed: " + err : "Lamp 2 switches on within about 10 seconds.");
      if (!DEMO) await refresh();
    }
    if (act === "logout") { await sb.auth.signOut(); showLogin(); }
  });

  // ---------- sign in ----------
  function showLogin(msg) {
    clearInterval(timer);
    const up = loginMode === "signup";
    app.innerHTML = `<div class="login">
      <div class="center" style="margin-bottom:28px">${mark(96)}<div style="display:flex;justify-content:center;margin-top:14px">${wordmark(26)}</div></div>
      <div class="seg"><button type="button" data-mode="signin" class="${up ? "" : "sel"}">Sign in</button>
        <button type="button" data-mode="signup" class="${up ? "sel" : ""}">Create account</button></div>
      <form id="lf"><input id="em" type="email" autocomplete="username" placeholder="Email" required>
      <input id="pw" type="password" autocomplete="${up ? "new-password" : "current-password"}" placeholder="${up ? "Password (at least 8 characters)" : "Password"}" minlength="${up ? 8 : 1}" required>
      ${up ? '<input id="pw2" type="password" autocomplete="new-password" placeholder="Password again" required>' : ""}
      <div class="err" id="er">${msg ? esc(msg) : ""}</div><button class="btn" type="submit">${up ? "Create account" : "Sign in"}</button></form>
      <p class="small muted center" style="margin-top:18px">${up ? "New accounts can view the readings and alerts. Only the owner can change power save." : "Use the email and password of your AmperEC account."}</p></div>`;
    app.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { loginMode = b.dataset.mode; showLogin(); }));
    document.getElementById("lf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const er = document.getElementById("er");
      const email = document.getElementById("em").value.trim(), pw = document.getElementById("pw").value;
      if (up) {
        if (pw.length < 8) { er.textContent = "The password needs at least 8 characters."; return; }
        if (pw !== document.getElementById("pw2").value) { er.textContent = "The two passwords are not the same."; return; }
        er.textContent = "Creating your account…";
        const { data: d, error } = await sb.auth.signUp({ email, password: pw });
        if (error) { er.textContent = /registered|exists/i.test(error.message) ? "This email already has an account. Sign in instead." : error.message; return; }
        if (!d.session) { loginMode = "signin"; showLogin("Account created. Check your email to confirm it, then sign in."); return; }
        start();
        return;
      }
      er.textContent = "Signing in…";
      const { error } = await sb.auth.signInWithPassword({ email, password: pw });
      if (error) { er.textContent = /confirm/i.test(error.message) ? "Confirm your email first (check your inbox)." : "Wrong email or password."; return; }
      start();
    });
  }

  async function start() {
    app.innerHTML = '<div class="login"><div class="center muted">Loading…</div></div>';
    isAdmin = DEMO && !new URLSearchParams(location.search).has("viewer"); // ?demo&viewer shows the view-only screens
    if (!DEMO) {
      try { const { data: a } = await sb.rpc("is_app_admin"); isAdmin = a === true; } catch (e) { isAdmin = false; }
    }
    await load();
    render();
    clearInterval(timer);
    timer = setInterval(() => { if (!document.hidden) refresh(); }, 5000);
  }

  setColor(colorName());
  if (DEMO) { start(); }
  else if (!window.supabase || !CFG.url || !CFG.key) {
    app.innerHTML = '<div class="login"><div class="err">The app is not set up (config.js) or there is no internet.</div></div>';
  } else {
    sb = window.supabase.createClient(CFG.url, CFG.key, { auth: { persistSession: true, autoRefreshToken: true } });
    sb.auth.getSession().then(({ data: d }) => (d.session ? start() : showLogin()));
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !document.querySelector(".login")) refresh(); });

  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
})();
