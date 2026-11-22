"use strict";
/* CRM met Jira-integratie — frontend (vanilla JS, geen build-stap) */

const S = { user: null, keuzes: null, route: "", timer: null };
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const d = (v) => (v ? new Date(v.length <= 10 ? v + "T00:00" : v).toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "numeric" }) : "—");
const dt = (v) => (v ? new Date(v).toLocaleString("nl-NL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const eur = (v) => (v == null ? "—" : "€ " + Number(v).toLocaleString("nl-NL", { maximumFractionDigits: 0 }));
const n0 = (v) => (v == null ? "—" : Number(v).toLocaleString("nl-NL", { maximumFractionDigits: 1 }));
const dash = (v) => (v == null || v === "" ? "—" : esc(v));
const hidden = '<span class="muted" title="Niet zichtbaar voor jouw rol">afgeschermd</span>';

// ---------------------------------------------------------------- API
async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json", "X-User": S.user?.id ?? "" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error(data.fout || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}
const get = (p) => api("GET", p);
const post = (p, b) => api("POST", p, b || {});
const put = (p, b) => api("PUT", p, b);

function toast(msg, bad) {
  const el = document.createElement("div");
  el.textContent = msg;
  if (bad) el.className = "bad";
  $("#toast").append(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3500);
}
async function act(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message, true); throw e; }
}

// ---------------------------------------------------------------- badges
const TONE = {
  Actief: "ok", Verlengd: "ok", Lopend: "ok", Goedgekeurd: "ok", Uitgevoerd: "ok", Gesloten: "", Ja: "ok", gesynchroniseerd: "ok", verwerkt: "ok", Afgerond: "",
  Prospect: "acc", Concept: "acc", Gepland: "acc", Nieuw: "acc", Beoordeling: "acc", wachtend: "warn", retry: "warn", "Te bepalen": "warn", CAB: "warn", Opgezegd: "warn", Spoed: "bad",
  Verlopen: "bad", Afgewezen: "bad", Nee: "bad", Beëindigd: "bad", Gestopt: "bad", fout: "bad", dlq: "bad", geweigerd: "bad", Inactief: "", genegeerd: "",
};
const badge = (v, t) => (v == null ? "—" : `<span class="badge ${t ?? TONE[v] ?? ""}">${esc(v)}</span>`);
const jira = (k) => (k ? `<a class="jira" href="#/jira?issue=${esc(k)}" title="Openen in Jira">${esc(k)} ↗</a>` : '<span class="muted">—</span>');
const sync = (s) => (s && s !== "n.v.t." ? badge(s) : '<span class="muted small">niet gekoppeld</span>');
const bar = (pct) => { const p = Math.min(100, pct || 0); return `<div class="bar ${pct >= 90 ? "bad" : pct >= 75 ? "warn" : ""}" title="${n0(pct)}%"><i style="width:${p}%"></i></div>`; };

// ---------------------------------------------------------------- formulieren
function field(f, val) {
  const v = val ?? f.v ?? "";
  const req = f.req ? " required" : "";
  const lab = `<span class="${f.req ? "req" : ""}">${esc(f.l)}</span>`;
  let inp;
  if (f.t === "select") {
    const opts = (f.opts || []).map((o) => (typeof o === "object" ? o : { v: o, l: o }));
    inp = `<select name="${f.n}"${req}>${f.req ? "" : '<option value="">—</option>'}${opts.map((o) => `<option value="${esc(o.v)}" ${String(o.v) === String(v) ? "selected" : ""}>${esc(o.l)}</option>`).join("")}</select>`;
  } else if (f.t === "textarea") inp = `<textarea name="${f.n}"${req}>${esc(v)}</textarea>`;
  else if (f.t === "multi") {
    const sel = Array.isArray(v) ? v : [];
    return `<fieldset class="full" style="border:1px solid var(--line);border-radius:6px;grid-column:1/-1"><legend class="small muted">${esc(f.l)}</legend>${f.opts.map((o) => `<label class="chk" style="display:inline-flex;margin-right:14px"><input type="checkbox" name="${f.n}" value="${esc(o)}" ${sel.includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</fieldset>`;
  } else if (f.t === "check") return `<label class="chk ${f.full ? "full" : ""}"><input type="checkbox" name="${f.n}" ${v && v !== "0" ? "checked" : ""}> ${esc(f.l)}</label>`;
  else inp = `<input name="${f.n}" type="${f.t || "text"}" value="${esc(v)}"${req}${f.step ? ` step="${f.step}"` : ""}${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""}${f.ph ? ` placeholder="${esc(f.ph)}"` : ""}>`;
  return `<label class="${f.full ? "full" : ""}">${lab}${inp}${f.hint ? `<span class="small">${esc(f.hint)}</span>` : ""}</label>`;
}
const formHtml = (fields, rec = {}) => `<div class="form">${fields.map((f) => field(f, rec[f.n])).join("")}</div>`;
function readForm(root, fields) {
  const o = {};
  for (const f of fields) {
    if (f.t === "multi") o[f.n] = $$(`[name="${f.n}"]:checked`, root).map((x) => x.value);
    else if (f.t === "check") o[f.n] = $(`[name="${f.n}"]`, root).checked ? 1 : 0;
    else { const el = $(`[name="${f.n}"]`, root); if (el) o[f.n] = el.value === "" ? null : el.value; }
  }
  return o;
}

function modal(title, body, { submit = "Opslaan", onSubmit, wide } = {}) {
  const dlg = $("#modal");
  dlg.style.width = wide ? "min(960px, calc(100vw - 32px))" : "";
  $("#modal-form").innerHTML = `<div class="dh"><h2 style="margin:0">${esc(title)}</h2><button class="btn sm" value="cancel" formnovalidate aria-label="Sluiten">✕</button></div>
    <div class="db">${body}</div><div class="df"><button class="btn" value="cancel" formnovalidate>${onSubmit ? "Annuleren" : "Sluiten"}</button>${onSubmit ? `<button class="btn pri" value="ok" id="m-ok">${esc(submit)}</button>` : ""}</div>`;
  const form = $("#modal-form");
  form.onsubmit = async (e) => {
    if (e.submitter?.value !== "ok") return;
    e.preventDefault();
    const b = $("#m-ok"); b.disabled = true;
    try { await onSubmit(form); dlg.close(); } catch (err) { toast(err.message, true); } finally { b.disabled = false; }
  };
  dlg.showModal();
  return form;
}

// ---------------------------------------------------------------- shell
const NAV = [
  ["", "Dashboard"], ["klanten", "Klanten"], ["contracten", "Contracten"], ["opdrachten", "Opdrachten"], ["rfcs", "RFC's"],
  ["meerwerk", "Meerwerk & offertes"], ["signalen", "Signalen"], ["rapportages", "Rapportages"],
  ["-", "Beheer"], ["integratie", "Integratiestatus"], ["import", "Import / export"], ["-", "Jira"], ["jira", "Jira-simulator"],
];
function shell() {
  $("#app").innerHTML = `<div class="shell">
    <nav class="side" id="side" aria-label="Hoofdmenu"><div class="brand"><i></i> CRM · Jira</div>${NAV.map(([h, l]) => h === "-" ? `<div class="grp">${l}</div>` : `<a href="#/${h}" data-r="${h}">${l}${h === "signalen" ? '<span class="count" id="sigcount" hidden></span>' : ""}</a>`).join("")}
      <div class="grp">Weergave</div><a href="#" id="theme">Thema wisselen</a></nav>
    <div><header class="top"><button class="btn sm menu-btn" id="menu" aria-label="Menu">☰</button>
      <div class="search"><input id="q" type="search" placeholder="Zoek klant, contract, opdracht, RFC of Jira-key…" aria-label="Globale zoekfunctie" autocomplete="off"><div class="res" id="qres" hidden></div></div>
      <div class="who"><span class="nm">${esc(S.user.naam)}</span><span class="badge acc">${esc(S.user.rolnaam)}</span><button class="btn sm" id="logout">Wissel gebruiker</button></div></header>
    <main id="main" tabindex="-1"></main></div></div>`;
  $("#logout").onclick = () => { localStorage.removeItem("crm_user"); S.user = null; boot(); };
  $("#menu").onclick = () => $("#side").classList.toggle("open");
  $("#theme").onclick = (e) => {
    e.preventDefault();
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("crm_theme", document.documentElement.dataset.theme); } catch {}
  };
  let t, sel = -1;
  const q = $("#q"), res = $("#qres");
  q.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (q.value.trim().length < 2) { res.hidden = true; return; }
      const r = await get("/api/zoeken?q=" + encodeURIComponent(q.value.trim()));
      sel = -1;
      res.innerHTML = r.length ? r.map((x) => `<a href="${x.link}"><span class="badge">${esc(x.type)}</span> ${esc(x.titel)} <span class="muted small">${esc(x.sub)}</span></a>`).join("") : '<div class="empty">Geen resultaten</div>';
      res.hidden = false;
    }, 180);
  };
  q.onkeydown = (e) => {
    const items = $$("a", res);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, Math.min(items.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1))); items.forEach((a, i) => a.classList.toggle("sel", i === sel)); }
    if (e.key === "Enter" && items[Math.max(sel, 0)]) { location.hash = items[Math.max(sel, 0)].getAttribute("href"); res.hidden = true; q.blur(); }
    if (e.key === "Escape") res.hidden = true;
  };
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) res.hidden = true; });
  refreshMe();
}
async function refreshMe() {
  try {
    const me = await get("/api/me");
    Object.assign(S.user, me);
    const c = $("#sigcount");
    if (c) { c.textContent = me.ongelezen; c.hidden = !me.ongelezen; }
  } catch {}
}

// ---------------------------------------------------------------- router
const ROUTES = [];
const page = (rx, fn) => ROUTES.push([new RegExp("^" + rx + "$"), fn]);
async function router() {
  const [path, qs] = (location.hash.slice(2) || "").split("?");
  S.route = path;
  S.qs = Object.fromEntries(new URLSearchParams(qs || ""));
  clearInterval(S.timer);
  $$("nav.side a[data-r]").forEach((a) => a.classList.toggle("on", a.dataset.r === path.split("/")[0]));
  $("#side")?.classList.remove("open");
  const main = $("#main");
  for (const [rx, fn] of ROUTES) {
    const m = path.match(rx);
    if (m) {
      try { await fn(main, ...m.slice(1)); }
      catch (e) { main.innerHTML = `<div class="alert bad">${esc(e.message)}</div>`; }
      main.focus({ preventScroll: true });
      return;
    }
  }
  main.innerHTML = '<div class="empty">Pagina niet gevonden</div>';
}
const reload = () => router();
function soon() { setTimeout(reload, 900); setTimeout(() => { reload(); refreshMe(); }, 2600); }
function autoRefresh(ms = 4000) {
  S.timer = setInterval(() => {
    const a = document.activeElement;
    const bezig = a && a.matches("textarea, input") && a.value; // niet verversen terwijl iemand typt
    if (!$("#modal").open && !bezig && !$$("[data-opm-concept]").some((t) => t.value.trim())) reload();
  }, ms);
}

// ---------------------------------------------------------------- opmerkingen (herbruikbaar blok)
async function opmBlock(el, ent, id, onChange) {
  const rows = await get(`/api/opmerkingen/${ent}/${id}`);
  if (!el.closest("dialog")) $$('.tabs [data-tab="opmerkingen"], [data-opm-titel]').forEach((x) => (x.textContent = opmLabel(rows.length)));
  el.innerHTML = `<div class="opm-new"><label class="sr" for="opm-${ent}-${id}">Nieuwe opmerking</label>
      <textarea id="opm-${ent}-${id}" data-opm-concept maxlength="5000" placeholder="Schrijf een opmerking…"></textarea>
      <div class="opm-bar"><button class="btn pri sm" data-opm-add>Plaatsen</button><span class="small muted">Ctrl/⌘ + Enter</span></div></div>
    <ul class="opm-list">${rows.map((o) => `<li data-opm="${o.id}">
      <div class="opm-head"><b>${esc(o.gebruiker)}</b><span class="small muted">${dt(o.ts)}${o.bewerkt ? " · bewerkt" : ""}</span>
        ${o.mag_beheren ? `<span class="opm-act"><button class="btn sm" data-opm-edit>Wijzig</button><button class="btn sm danger" data-opm-del>Verwijder</button></span>` : ""}</div>
      <div class="opm-tekst">${esc(o.tekst)}</div></li>`).join("") || '<li class="empty">Nog geen opmerkingen</li>'}</ul>`;
  const refresh = () => { opmBlock(el, ent, id, onChange); onChange?.(); };
  const ta = $("[data-opm-concept]", el);
  const plaats = async () => {
    if (!ta.value.trim()) return toast("Opmerking is leeg", true);
    await act(() => post(`/api/opmerkingen/${ent}/${id}`, { tekst: ta.value }), "Opmerking geplaatst");
    ta.value = "";
    refresh();
  };
  $("[data-opm-add]", el).onclick = (e) => { e.preventDefault(); plaats(); };
  ta.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); plaats(); } };
  $$("[data-opm-del]", el).forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Opmerking verwijderen?")) return;
    await act(() => post(`/api/opmerkingen/${b.closest("li").dataset.opm}/verwijderen`), "Opmerking verwijderd");
    refresh();
  }));
  $$("[data-opm-edit]", el).forEach((b) => (b.onclick = (e) => {
    e.preventDefault();
    const li = b.closest("li"), tx = $(".opm-tekst", li), oud = rows.find((o) => o.id == li.dataset.opm).tekst;
    tx.innerHTML = `<textarea data-opm-concept maxlength="5000">${esc(oud)}</textarea><div class="opm-bar"><button class="btn pri sm" data-s>Opslaan</button><button class="btn sm" data-c>Annuleren</button></div>`;
    const t = $("textarea", tx); t.focus(); t.setSelectionRange(t.value.length, t.value.length);
    $("[data-c]", tx).onclick = (ev) => { ev.preventDefault(); refresh(); };
    $("[data-s]", tx).onclick = async (ev) => { ev.preventDefault(); await act(() => put(`/api/opmerkingen/${li.dataset.opm}`, { tekst: t.value }), "Opmerking gewijzigd"); refresh(); };
  }));
}
function opmModal(titel, ent, id) {
  const f = modal(`Opmerkingen — ${titel}`, '<div id="opm-modal"></div>', { wide: 1 });
  $("#modal").addEventListener("close", () => reload(), { once: true }); // aantallen bijwerken
  opmBlock($("#opm-modal", f), ent, id);
}
const opmLabel = (n) => `Opmerkingen${n ? ` (${n})` : ""}`;

const head = (title, sub = "", actions = "", crumb = "") =>
  `${crumb ? `<div class="crumb">${crumb}</div>` : ""}<div class="pagehead"><h1>${title}</h1>${sub ? `<span class="sub">${sub}</span>` : ""}<div class="actions">${actions}</div></div>`;
const table = (cols, rows, onclick) => rows.length ? `<div class="tablewrap"><table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.l)}</th>`).join("")}</tr></thead>
  <tbody>${rows.map((r) => `<tr ${onclick ? `class="click" data-href="${onclick(r)}" tabindex="0"` : ""}>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.f ? c.f(r) : dash(r[c.k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : '<div class="empty">Geen records</div>';
document.addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-href]");
  if (tr && !e.target.closest("a,button,input,select")) location.hash = tr.dataset.href;
});
document.addEventListener("keydown", (e) => { const tr = e.target.closest?.("tr[data-href]"); if (tr && e.key === "Enter") location.hash = tr.dataset.href; });
function tabs(main, names, cur, onpick) {
  return `<div class="tabs" role="tablist">${names.map(([k, l]) => `<button role="tab" data-tab="${k}" class="${k === cur ? "on" : ""}" aria-selected="${k === cur}">${l}</button>`).join("")}</div>`;
}
function bindTabs(root, onpick) { $$(".tabs button", root).forEach((b) => (b.onclick = () => onpick(b.dataset.tab))); }

// ================================================================ DASHBOARD
page("", async (main) => {
  const [sig, cal, uren, rfcs, kpi, st] = await Promise.all([
    get("/api/signalen"), get("/api/rapport/contractkalender"), get("/api/rapport/urenverbruik"), get("/api/rfcs"),
    get("/api/rapport/change-kpis"), get("/api/integratie/status"),
  ]);
  const open = rfcs.filter((r) => !["Gesloten", "Afgewezen"].includes(r.status));
  const opzeg = cal.filter((c) => c.dagen_tot_opzeg != null && c.dagen_tot_opzeg >= 0 && c.dagen_tot_opzeg <= 183 && ["Actief", "Verlengd"].includes(c.status));
  main.innerHTML = head(`Goedemiddag, ${esc(S.user.naam.split(" ")[0])}`, new Date().toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" })) + `
  ${st.health !== "ok" ? `<div class="alert bad">Integratie verstoord: ${st.jira_offline ? "Jira niet bereikbaar — wijzigingen worden gebufferd" : ""} ${st.tellingen.dlq ? `${st.tellingen.dlq} bericht(en) in dead-letter queue` : ""}</div>` : ""}
  <div class="grid g4">
    <div class="card stat"><div class="l">Open RFC's</div><div class="v">${open.length}</div><div class="small muted">${open.filter((r) => r.binnen_contract === "Nee").length} buiten contract</div></div>
    <div class="card stat"><div class="l">Opzegmoment &lt; 6 mnd</div><div class="v">${opzeg.length}</div><div class="small muted">contracten</div></div>
    <div class="card stat"><div class="l">Urenbudget ≥ 75%</div><div class="v">${uren.filter((u) => u.pct >= 75).length}</div><div class="small muted">contracten</div></div>
    <div class="card stat"><div class="l">Sync-wachtrij</div><div class="v">${st.queue_diepte}</div><div class="small muted">${st.p95_sync_seconden != null ? `P95 ${st.p95_sync_seconden}s` : "geen recente sync"}</div></div>
  </div>
  <div class="grid g21" style="margin-top:16px">
    <div class="stack">
      <div class="card"><h2>Contracten rond opzegmoment</h2>${table([
        { l: "Contract", f: (r) => `<a href="#/contracten/${r.id}">${esc(r.nummer)}</a>` }, { l: "Klant", k: "klantnaam" }, { l: "Verlenging", k: "verlengingsvorm" },
        { l: "Uiterste opzegdatum", f: (r) => d(r.uiterste_opzegdatum) }, { l: "Nog", num: 1, f: (r) => badge(r.dagen_tot_opzeg + " d", r.dagen_tot_opzeg <= 31 ? "bad" : r.dagen_tot_opzeg <= 92 ? "warn" : "") }], opzeg, (r) => "#/contracten/" + r.id)}</div>
      <div class="card"><h2>Urenverbruik</h2>${table([
        { l: "Contract", f: (r) => `<a href="#/contracten/${r.id}">${esc(r.nummer)}</a>` }, { l: "Klant", k: "klantnaam" },
        { l: "Besteed / begroot", num: 1, f: (r) => `${n0(r.besteed)} / ${n0(r.begroot)}` }, { l: "", f: (r) => bar(r.pct) }, { l: "%", num: 1, f: (r) => n0(r.pct) }], uren.slice(0, 6), (r) => "#/contracten/" + r.id)}</div>
      <div class="card"><h2>Recente RFC's</h2>${rfcTable(open.slice(0, 8))}</div>
    </div>
    <div class="stack">
      <div class="card"><h2>Signalen</h2>${sigList(sig.filter((s) => !s.gelezen).slice(0, 8))}<a href="#/signalen" class="small">Alle signalen →</a></div>
      <div class="card"><h2>Changes</h2><dl class="kv"><dt>Totaal</dt><dd>${kpi.totaal}</dd><dt>Spoed</dt><dd>${kpi.spoed}</dd><dt>Succesratio</dt><dd>${kpi.succesratio == null ? "—" : kpi.succesratio + "%"}</dd><dt>Gem. risicoscore</dt><dd>${kpi.gem_risicoscore ?? "—"}</dd></dl></div>
    </div>
  </div>`;
  bindSig(main);
});
function sigList(s) {
  return s.length ? s.map((x) => `<div class="sig ${x.gelezen ? "read" : ""}">${badge(x.type, x.type === "uren" || x.type === "opzegtermijn" ? "warn" : x.type === "meerwerk" ? "acc" : "")}
    <div style="flex:1"><a href="#/${{ contract: "contracten", rfc: "rfcs", opdracht: "opdrachten" }[x.entiteit] || ""}/${x.entiteit_id}">${esc(x.bericht)}</a><div class="small muted">${dt(x.ts)}${x.ontvanger ? " · " + esc(x.ontvanger) : ""}</div></div>
    ${x.gelezen ? "" : `<button class="btn sm" data-sig="${x.id}">Gelezen</button>`}</div>`).join("") : '<div class="empty">Geen nieuwe signalen</div>';
}
function bindSig(root) { $$("[data-sig]", root).forEach((b) => (b.onclick = async () => { await post(`/api/signalen/${b.dataset.sig}/gelezen`); reload(); refreshMe(); })); }
const rfcTable = (rows) => table([
  { l: "Key", f: (r) => r.jira_key ? `<span class="jira">${esc(r.jira_key)}</span>` : badge(r.jira_sync_status) }, { l: "Titel", f: (r) => `<a href="#/rfcs/${r.id}">${esc(r.titel)}</a>` },
  { l: "Klant", k: "klantnaam" }, { l: "Contract", k: "contractnummer" }, { l: "Type", f: (r) => badge(r.change_type, r.change_type === "Spoed" ? "bad" : "") },
  { l: "Status", f: (r) => badge(r.status) }, { l: "Binnen contract", f: (r) => badge(r.binnen_contract) }, { l: "Risico", num: 1, f: (r) => r.risicoscore == null ? "—" : badge(r.risicoscore, r.risicoscore >= 15 ? "bad" : r.risicoscore >= 9 ? "warn" : "ok") }], rows, (r) => "#/rfcs/" + r.id);

// ================================================================ KLANTEN
const klantVelden = () => [
  { n: "naam", l: "Statutaire naam", req: 1, full: 1 }, { n: "kvk", l: "KvK-nummer", req: 1, ph: "8 cijfers" }, { n: "btw", l: "BTW-nummer", ph: "NL123456789B01" },
  { n: "bezoekadres", l: "Bezoekadres", req: 1 }, { n: "plaats", l: "Plaats" }, { n: "postadres", l: "Postadres" }, { n: "factuuradres", l: "Factuuradres" },
  { n: "email_domein", l: "E-maildomein", ph: "bedrijf.nl" }, { n: "branche", l: "Branche / segment", t: "select", opts: S.keuzes.branche },
  { n: "accountmanager_id", l: "Accountmanager", t: "select", req: 1, opts: S.keuzes.accountmanagers.map((u) => ({ v: u.id, l: u.naam })) },
  { n: "status", l: "Status", t: "select", req: 1, opts: S.keuzes.klant_status, hint: "Bij 'Actief' wordt automatisch een JSM Organization aangemaakt" },
];
page("klanten", async (main) => {
  const st = S.qs.status || "", zoek = S.qs.zoek || "";
  const rows = await get(`/api/klanten?status=${encodeURIComponent(st)}&zoek=${encodeURIComponent(zoek)}&archief=${S.qs.archief || ""}`);
  main.innerHTML = head("Klanten", `${rows.length} klanten`, `<button class="btn pri" id="new">Nieuwe klant</button>`) + `<div class="card">
    <div class="filters"><input id="fz" placeholder="Zoek op naam, KvK of plaats" value="${esc(zoek)}"><select id="fs"><option value="">Alle statussen</option>${S.keuzes.klant_status.map((s) => `<option ${s === st ? "selected" : ""}>${s}</option>`).join("")}</select>
    <label class="small"><input type="checkbox" id="fa" ${S.qs.archief ? "checked" : ""}> Archief</label></div>
    ${table([{ l: "Nr", k: "nummer" }, { l: "Naam", f: (r) => `<a href="#/klanten/${r.id}">${esc(r.naam)}</a>` }, { l: "KvK", k: "kvk" }, { l: "Plaats", k: "plaats" }, { l: "Branche", k: "branche" },
      { l: "Accountmanager", k: "accountmanager" }, { l: "Status", f: (r) => badge(r.status) }, { l: "Contracten", num: 1, k: "actieve_contracten" }, { l: "Open RFC's", num: 1, k: "open_rfcs" },
      { l: "Jira", f: (r) => sync(r.jira_sync_status) }], rows, (r) => "#/klanten/" + r.id)}</div>`;
  const go = () => (location.hash = `#/klanten?zoek=${encodeURIComponent($("#fz").value)}&status=${encodeURIComponent($("#fs").value)}${$("#fa").checked ? "&archief=1" : ""}`);
  $("#fz").onchange = go; $("#fs").onchange = go; $("#fa").onchange = go;
  $("#new").onclick = () => klantForm();
});
function klantForm(rec) {
  const F = klantVelden();
  modal(rec ? "Klant wijzigen" : "Nieuwe klant", `<div id="dub"></div>${formHtml(F, rec || { accountmanager_id: S.user.rol === "accountmanager" ? S.user.id : "", status: "Prospect" })}`, {
    onSubmit: async (f) => {
      const data = readForm(f, F);
      if (rec) { await put(`/api/klanten/${rec.id}`, data); toast("Klant opgeslagen"); return reload(); }
      try { const k = await post("/api/klanten", { ...data, forceer: f.dataset.forceer === "1" }); location.hash = "#/klanten/" + k.id; toast("Klant aangemaakt"); }
      catch (e) {
        if (e.status === 409) {
          $("#dub", f).innerHTML = `<div class="alert"><b>Mogelijk dubbele klant (FR-KL-02):</b><ul>${e.data.dubbelen.map((x) => `<li><a href="#/klanten/${x.id}" target="_blank">${esc(x.naam)}</a> — ${esc(x.reden.join(", "))}</li>`).join("")}</ul>Klik nogmaals op Opslaan om toch aan te maken.</div>`;
          f.dataset.forceer = "1";
          throw new Error("Controleer mogelijke dubbele klant");
        }
        throw e;
      }
    },
  });
}
const cpVelden = () => [
  { n: "voornaam", l: "Voornaam" }, { n: "achternaam", l: "Achternaam", req: 1 }, { n: "email", l: "E-mail", t: "email", req: 1, hint: "Sleutel voor JSM Customer" },
  { n: "telefoon", l: "Telefoon" }, { n: "functie", l: "Functie" }, { n: "communicatievoorkeur", l: "Communicatievoorkeur", t: "select", opts: S.keuzes.communicatievoorkeur },
  { n: "avg_grondslag", l: "AVG-grondslag", t: "select", req: 1, opts: S.keuzes.avg_grondslag },
  { n: "rollen", l: "Rollen (Technisch contact en CAB-lid krijgen toegang tot JSM)", t: "multi", opts: S.keuzes.rollen },
];
function cpForm(kid, rec) {
  const F = cpVelden();
  modal(rec ? "Contactpersoon wijzigen" : "Nieuwe contactpersoon", formHtml(F, rec || { avg_grondslag: "Overeenkomst" }), {
    onSubmit: async (f) => { const data = readForm(f, F); rec ? await put(`/api/contactpersonen/${rec.id}`, data) : await post("/api/contactpersonen", { ...data, klant_id: kid }); toast("Contactpersoon opgeslagen"); soon(); },
  });
}
page("klanten/(\\d+)", async (main, id) => {
  const k = await get("/api/klanten/" + id);
  const tab = S.qs.tab || "overzicht";
  const w = k.mag_schrijven;
  const openR = k.rfcs.filter((r) => !["Gesloten", "Afgewezen"].includes(r.status));
  main.innerHTML = head(esc(k.naam), `${badge(k.status)} <span class="muted">${esc(k.nummer)} · KvK ${esc(k.kvk)}</span>`,
    w ? `<button class="btn" id="edit">Wijzigen</button>${k.gearchiveerd ? "" : '<button class="btn danger" id="arch">Archiveren</button>'}` : "", `<a href="#/klanten">Klanten</a> /`) +
    tabs(main, [["overzicht", "Klantkaart"], ["contacten", `Contactpersonen (${k.contactpersonen.length})`], ["contracten", `Contracten (${k.contracten.length})`],
      ["opdrachten", `Opdrachten (${k.opdrachten.length})`], ["rfcs", `RFC's (${k.rfcs.length})`], ["opmerkingen", opmLabel(k.opmerkingen_aantal)], ["tijdlijn", "Tijdlijn"], ["audit", "Audit trail"]], tab) + `<div id="tab"></div>`;
  bindTabs(main, (t) => (location.hash = `#/klanten/${id}?tab=${t}`));
  if (w) { $("#edit").onclick = () => klantForm(k); const a = $("#arch"); if (a) a.onclick = async () => { if (confirm("Klant archiveren? Records worden niet verwijderd.")) { await act(() => post(`/api/klanten/${id}/archiveren`), "Gearchiveerd"); reload(); } }; }
  const T = $("#tab");
  const cpTable = () => table([{ l: "Naam", f: (c) => `${esc(c.naam)}${c.actief ? "" : ' <span class="badge">inactief</span>'}${c.geanonimiseerd ? ' <span class="badge">geanonimiseerd</span>' : ""}` },
    { l: "Functie", k: "functie" }, { l: "E-mail", k: "email" }, { l: "Telefoon", k: "telefoon" }, { l: "Rollen", f: (c) => c.rollen.map((r) => badge(r)).join(" ") },
    { l: "AVG", k: "avg_grondslag" }, { l: "JSM", f: (c) => c.jira_account_id ? `<span class="jira">${esc(c.jira_account_id)}</span>` : sync(c.jira_sync_status) },
    { l: "Opmerkingen", f: (c) => `<button class="btn sm" data-cpo="${c.id}" aria-label="Opmerkingen bij ${esc(c.naam)}">💬 ${c.opmerkingen_aantal || 0}</button>` },
    ...(w ? [{ l: "", f: (c) => c.geanonimiseerd ? "" : `<button class="btn sm" data-cpe="${c.id}">Wijzig</button> <button class="btn sm" data-cpa="${c.id}" data-on="${c.actief}">${c.actief ? "Deactiveer" : "Activeer"}</button> <button class="btn sm danger" data-cpx="${c.id}" title="AVG-verwijderverzoek">Anonimiseer</button>` }] : [])], k.contactpersonen);
  const ctTable = () => table([{ l: "Nummer", f: (c) => `<a href="#/contracten/${c.id}">${esc(c.nummer)}</a>` }, { l: "Soort", k: "soort" }, { l: "Looptijd", f: (c) => `${d(c.startdatum)} – ${c.onbepaalde_tijd ? "onbepaald" : d(c.einddatum)}` },
    { l: "Duur", num: 1, f: (c) => c.duur_maanden == null ? "—" : c.duur_maanden + " mnd" }, { l: "Waarde", num: 1, f: (c) => c.waarde_verborgen ? hidden : eur(c.waarde) },
    { l: "Uren", f: (c) => c.urenbudget ? `${bar(c.uren_pct)}<span class="small muted">${n0(c.bestede_uren)} / ${n0(c.urenbudget)}</span>` : "—" }, { l: "Regime", k: "change_regime" }, { l: "Status", f: (c) => badge(c.status) }, { l: "Jira", f: (c) => c.jira_key ? `<span class="jira">${esc(c.jira_key)}</span>` : sync(c.jira_sync_status) }], k.contracten, (c) => "#/contracten/" + c.id);
  const opTable = () => opdrachtTable(k.opdrachten);
  if (tab === "overzicht") {
    T.innerHTML = `<div class="grid g21"><div class="stack">
      <div class="card"><h2>Contracten</h2>${ctTable()}</div>
      <div class="card"><h2>Open RFC's</h2>${rfcTable(openR.map((r) => ({ ...r, klantnaam: k.naam })))}</div>
      <div class="card"><h2>Opdrachten</h2>${opTable()}</div></div>
      <div class="stack"><div class="card"><h2>Gegevens</h2><dl class="kv"><dt>Bezoekadres</dt><dd>${dash(k.bezoekadres)}, ${dash(k.plaats)}</dd><dt>Postadres</dt><dd>${dash(k.postadres)}</dd>
        <dt>Factuuradres</dt><dd>${dash(k.factuuradres)}</dd><dt>BTW</dt><dd>${dash(k.btw)}</dd><dt>Branche</dt><dd>${dash(k.branche)}</dd><dt>Accountmanager</dt><dd>${dash(k.accountmanager)}</dd>
        <dt>JSM Organization</dt><dd>${k.jira_org_id ? `<span class="jira">org ${esc(k.jira_org_id)}</span>` : "—"}</dd><dt>Assets-object</dt><dd>${k.jira_key ? `<span class="jira">${esc(k.jira_key)}</span>` : "—"}</dd><dt>Sync</dt><dd>${sync(k.jira_sync_status)}</dd></dl></div>
      <div class="card"><h2>Contactpersonen</h2>${k.contactpersonen.filter((c) => c.actief).map((c) => `<div style="padding:4px 0"><b>${esc(c.naam)}</b> <span class="muted small">${esc(c.functie || "")}</span><br><span class="small">${c.rollen.map((r) => badge(r)).join(" ")}</span></div>`).join("") || '<div class="empty">Geen</div>'}</div>
      <div class="card"><h2>Recente activiteit</h2>${timeline(k.tijdlijn.slice(0, 8))}</div></div></div>`;
  } else if (tab === "contacten") {
    T.innerHTML = `<div class="card">${w ? '<div class="filters"><button class="btn pri" id="cpn">Nieuwe contactpersoon</button></div>' : ""}${cpTable()}</div>`;
    $$("[data-cpo]").forEach((b) => (b.onclick = () => { const c = k.contactpersonen.find((x) => x.id == b.dataset.cpo); opmModal(c.naam, "contactpersoon", c.id); }));
    if (w) {
      $("#cpn").onclick = () => cpForm(k.id);
      $$("[data-cpe]").forEach((b) => (b.onclick = () => cpForm(k.id, k.contactpersonen.find((c) => c.id == b.dataset.cpe))));
      $$("[data-cpa]").forEach((b) => (b.onclick = async () => { await act(() => post(`/api/contactpersonen/${b.dataset.cpa}/${b.dataset.on == 1 ? "deactiveren" : "activeren"}`), "Bijgewerkt; JSM-toegang wordt gesynchroniseerd"); soon(); }));
      $$("[data-cpx]").forEach((b) => (b.onclick = async () => { if (confirm("AVG-verwijderverzoek uitvoeren? De persoonsgegevens worden onomkeerbaar geanonimiseerd in CRM en er gaat een verzoek naar Jira.")) { await act(() => post(`/api/contactpersonen/${b.dataset.cpx}/anonimiseren`), "Geanonimiseerd"); soon(); } }));
    }
  } else if (tab === "contracten") {
    T.innerHTML = `<div class="card">${w ? '<div class="filters"><button class="btn pri" id="ctn">Nieuw contract</button></div>' : ""}${ctTable()}</div>`;
    if (w) $("#ctn").onclick = () => contractForm({ klant_id: k.id });
  } else if (tab === "opdrachten") {
    T.innerHTML = `<div class="card">${w ? '<div class="filters"><button class="btn pri" id="opn">Nieuwe opdracht</button></div>' : ""}${opTable()}</div>`;
    if (w) $("#opn").onclick = () => opdrachtForm({ klant_id: k.id });
  } else if (tab === "rfcs") {
    T.innerHTML = `<div class="card">${["accountmanager", "servicemanager", "beheerder"].includes(S.user.rol) ? '<div class="filters"><button class="btn pri" id="rn">Nieuwe RFC</button></div>' : ""}${rfcTable(k.rfcs.map((r) => ({ ...r, klantnaam: k.naam })))}</div>`;
    const b = $("#rn"); if (b) b.onclick = () => rfcForm({ klant_id: k.id });
  } else if (tab === "tijdlijn") {
    T.innerHTML = `<div class="card">${w ? `<div class="form" style="margin-bottom:12px"><label>Type<select id="at">${S.keuzes.activiteit_type.map((t) => `<option>${t}</option>`).join("")}</select></label><label class="full">Notitie<textarea id="ax" placeholder="Wat is er besproken?"></textarea></label></div><button class="btn pri" id="aadd">Toevoegen</button><hr style="border:0;border-top:1px solid var(--line);margin:16px 0">` : ""}${timeline(k.tijdlijn)}</div>`;
    if (w) $("#aadd").onclick = async () => { await act(() => post(`/api/klanten/${id}/activiteiten`, { type: $("#at").value, tekst: $("#ax").value }), "Toegevoegd"); reload(); };
  } else if (tab === "opmerkingen") {
    T.innerHTML = '<div class="card" id="opm"></div>';
    await opmBlock($("#opm"), "klant", k.id);
  } else if (tab === "audit") T.innerHTML = `<div class="card">${await auditHtml("klant", id)}</div>`;
});
const timeline = (items) => items.length ? `<ul class="timeline">${items.map((t) => `<li><span class="small muted">${dt(t.ts)}</span><span>${badge(t.type, t.bron === "Jira" || t.type === "Jira" ? "acc" : "")} ${esc(t.tekst)} <span class="small muted">— ${esc(t.door)}</span></span></li>`).join("")}</ul>` : '<div class="empty">Nog geen activiteit</div>';
async function auditHtml(ent, id) {
  const rows = await get(`/api/audit?entiteit=${ent}&id=${id}`);
  return table([{ l: "Wanneer", f: (r) => dt(r.ts) }, { l: "Wie", k: "gebruiker" }, { l: "Bron", f: (r) => badge(r.bron, r.bron === "CRM" ? "" : "acc") }, { l: "Actie", k: "actie" },
    { l: "Wijzigingen", f: (r) => { const w = JSON.parse(r.wijzigingen || "{}"); return Object.entries(w).slice(0, 8).map(([k, v]) => `<div class="small"><b>${esc(k)}</b>: ${v && typeof v === "object" && "nieuw" in v ? `${dash(v.oud)} → ${dash(v.nieuw)}` : esc(typeof v === "object" ? JSON.stringify(v) : v)}</div>`).join(""); } }], rows);
}

// ================================================================ CONTRACTEN
const ctVelden = (klanten) => [
  ...(klanten ? [{ n: "klant_id", l: "Klant", t: "select", req: 1, full: 1, opts: klanten.map((k) => ({ v: k.id, l: k.naam })) }] : []),
  { n: "soort", l: "Soort contract", t: "select", req: 1, opts: S.keuzes.contract_soort }, { n: "status", l: "Status", t: "select", req: 1, opts: S.keuzes.contract_status, hint: "Bij 'Actief' wordt een Assets-object Contract aangemaakt" },
  { n: "startdatum", l: "Startdatum", t: "date", req: 1 }, { n: "einddatum", l: "Einddatum", t: "date", hint: "Niet nodig bij onbepaalde tijd" },
  { n: "onbepaalde_tijd", l: "Onbepaalde tijd", t: "check" }, { n: "verlengingsvorm", l: "Verlengingsvorm", t: "select", req: 1, opts: S.keuzes.verlengingsvorm },
  { n: "opzegtermijn", l: "Opzegtermijn (maanden)", t: "number", req: 1, min: 0 }, { n: "change_regime", l: "Change-regime", t: "select", req: 1, opts: S.keuzes.change_regime },
  ...(S.user.waarde_zichtbaar ? [{ n: "waarde", l: "Contractwaarde (EUR)", t: "number", step: "0.01" }] : []), { n: "urenbudget", l: "Urenbudget / plafond", t: "number" },
  { n: "sla_niveau", l: "SLA-niveau", t: "select", opts: S.keuzes.sla_niveau },
];
async function contractForm(rec, edit) {
  const kl = edit ? null : (await get("/api/klanten")).filter((k) => S.user.rol !== "accountmanager" || k.accountmanager_id === S.user.id);
  let F = ctVelden(kl);
  if (S.user.rol === "finance") F = F.filter((f) => ["waarde", "urenbudget"].includes(f.n));
  modal(edit ? `Contract ${rec.nummer} wijzigen` : "Nieuw contract", formHtml(F, { status: "Concept", verlengingsvorm: "Geen", opzegtermijn: 3, change_regime: "Binnen contract", ...rec }) +
    (edit ? '<p class="small muted">Gebruik “Verlengen” of “Addendum” voor wijzigingen die een nieuwe contractversie vormen.</p>' : ""), {
    onSubmit: async (f) => {
      const data = readForm(f, F);
      if (edit) { await put(`/api/contracten/${rec.id}`, data); toast("Contract opgeslagen"); soon(); }
      else { const c = await post("/api/contracten", data); toast("Contract aangemaakt"); location.hash = "#/contracten/" + c.id; }
    },
  });
}
page("contracten", async (main) => {
  const qs = S.qs;
  const rows = await get(`/api/contracten?status=${encodeURIComponent(qs.status || "")}&soort=${encodeURIComponent(qs.soort || "")}&zoek=${encodeURIComponent(qs.zoek || "")}`);
  const can = ["accountmanager", "beheerder"].includes(S.user.rol);
  main.innerHTML = head("Contracten", `${rows.length} contracten`, can ? '<button class="btn pri" id="new">Nieuw contract</button>' : "") + `<div class="card">
    <div class="filters"><input id="fz" placeholder="Zoek op nummer of klant" value="${esc(qs.zoek || "")}"><select id="fs"><option value="">Alle statussen</option>${S.keuzes.contract_status.map((s) => `<option ${s === qs.status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <select id="fo"><option value="">Alle soorten</option>${S.keuzes.contract_soort.map((s) => `<option ${s === qs.soort ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></div>
    ${table([{ l: "Nummer", f: (c) => `<a href="#/contracten/${c.id}">${esc(c.nummer)}</a>` }, { l: "Klant", k: "klantnaam" }, { l: "Soort", k: "soort" },
      { l: "Einddatum", f: (c) => c.onbepaalde_tijd ? "onbepaald" : d(c.einddatum) }, { l: "Duur", num: 1, f: (c) => c.duur_maanden == null ? "—" : c.duur_maanden + " mnd" },
      { l: "Uiterste opzeg", f: (c) => c.uiterste_opzegdatum ? d(c.uiterste_opzegdatum) : "—" }, { l: "Waarde", num: 1, f: (c) => c.waarde_verborgen ? hidden : eur(c.waarde) },
      { l: "Uren", f: (c) => c.urenbudget ? bar(c.uren_pct) : "—" }, { l: "Regime", k: "change_regime" }, { l: "Status", f: (c) => badge(c.status) }, { l: "Jira", f: (c) => c.jira_key ? `<span class="jira">${esc(c.jira_key)}</span>` : sync(c.jira_sync_status) }], rows, (c) => "#/contracten/" + c.id)}</div>`;
  const go = () => (location.hash = `#/contracten?zoek=${encodeURIComponent($("#fz").value)}&status=${encodeURIComponent($("#fs").value)}&soort=${encodeURIComponent($("#fo").value)}`);
  $("#fz").onchange = go; $("#fs").onchange = go; $("#fo").onchange = go;
  if (can) $("#new").onclick = () => contractForm({});
});
page("contracten/(\\d+)", async (main, id) => {
  const c = await get("/api/contracten/" + id);
  const tab = S.qs.tab || "overzicht", w = c.mag_schrijven;
  const actief = ["Actief", "Verlengd"].includes(c.status);
  main.innerHTML = head(esc(c.nummer), `${badge(c.status)} <span class="muted">${esc(c.soort)} · <a href="#/klanten/${c.klant_id}">${esc(c.klantnaam)}</a></span>`,
    w ? `<button class="btn" id="edit">Wijzigen</button>${S.user.rol !== "finance" ? '<button class="btn" id="verl">Verlengen</button><button class="btn" id="add">Addendum</button>' : ""}` : "", '<a href="#/contracten">Contracten</a> /') +
    (!actief ? `<div class="alert ${c.status === "Concept" ? "info" : "bad"}">Status ${esc(c.status)}: er kunnen ${c.status === "Concept" ? "nog " : ""}geen nieuwe RFC's of opdrachten aan dit contract worden gekoppeld.</div>` : "") +
    (c.uren_pct >= 75 ? `<div class="alert ${c.uren_pct >= 90 ? "bad" : ""}">Urenbudget voor ${n0(c.uren_pct)}% verbruikt.</div>` : "") +
    tabs(main, [["overzicht", "Overzicht"], ["versies", `Versies (${c.versies.length})`], ["opmerkingen", opmLabel(c.opmerkingen_aantal)], ["audit", "Audit trail"]], tab) + '<div id="tab"></div>';
  bindTabs(main, (t) => (location.hash = `#/contracten/${id}?tab=${t}`));
  if (w) {
    $("#edit").onclick = () => contractForm(c, true);
    const v = $("#verl");
    if (v) {
      v.onclick = () => { const F = [{ n: "einddatum", l: "Nieuwe einddatum", t: "date", req: 1 }, { n: "urenbudget", l: "Nieuw urenbudget (optioneel)", t: "number" }, { n: "toelichting", l: "Toelichting", t: "textarea", full: 1 }];
        modal("Contract verlengen", formHtml(F, {}), { submit: "Verlengen", onSubmit: async (f) => { await post(`/api/contracten/${id}/verlengen`, readForm(f, F)); toast("Verlengd; nieuwe versie vastgelegd"); soon(); } }); };
      $("#add").onclick = () => { const F = [{ n: "urenbudget", l: "Urenbudget", t: "number" }, ...(S.user.waarde_zichtbaar ? [{ n: "waarde", l: "Contractwaarde", t: "number" }] : []), { n: "sla_niveau", l: "SLA-niveau", t: "select", opts: S.keuzes.sla_niveau }, { n: "change_regime", l: "Change-regime", t: "select", opts: S.keuzes.change_regime }, { n: "toelichting", l: "Toelichting", t: "textarea", full: 1, req: 1 }];
        modal("Addendum vastleggen", formHtml(F, c), { onSubmit: async (f) => { const x = readForm(f, F); Object.keys(x).forEach((k) => x[k] == null && delete x[k]); await post(`/api/contracten/${id}/addendum`, x); toast("Addendum vastgelegd"); soon(); } }); };
    }
  }
  const T = $("#tab");
  if (tab === "overzicht") {
    T.innerHTML = `<div class="grid g2"><div class="card"><h2>Contractgegevens</h2><dl class="kv">
      <dt>Looptijd</dt><dd>${d(c.startdatum)} – ${c.onbepaalde_tijd ? "onbepaalde tijd" : d(c.einddatum)}</dd><dt>Contractduur</dt><dd>${c.duur_maanden == null ? "onbepaald" : c.duur_maanden + " maanden"}</dd>
      <dt>Verlengingsvorm</dt><dd>${esc(c.verlengingsvorm)}</dd><dt>Opzegtermijn</dt><dd>${c.opzegtermijn} maand(en)</dd>
      <dt>Uiterste opzegdatum</dt><dd>${c.uiterste_opzegdatum ? `${d(c.uiterste_opzegdatum)} ${c.dagen_tot_opzeg >= 0 ? badge("nog " + c.dagen_tot_opzeg + " dagen", c.dagen_tot_opzeg <= 92 ? "warn" : "") : badge("verstreken", "bad")}` : "—"}</dd>
      <dt>Contractwaarde</dt><dd>${c.waarde_verborgen ? hidden : eur(c.waarde)}</dd><dt>SLA-niveau</dt><dd>${dash(c.sla_niveau)}</dd><dt>Change-regime</dt><dd>${esc(c.change_regime)}</dd>
      <dt>Versie</dt><dd>${c.versie}</dd><dt>Assets-object</dt><dd>${c.jira_key ? `<span class="jira">${esc(c.jira_key)}</span>` : "—"} ${sync(c.jira_sync_status)}</dd></dl></div>
      <div class="card"><h2>Urenbudget</h2>${c.urenbudget ? `<div class="stat"><div class="v">${n0(c.bestede_uren)} <span class="muted" style="font-size:15px">/ ${n0(c.urenbudget)} uur</span></div></div>${bar(c.uren_pct)}<p class="small muted">${n0(c.uren_pct)}% verbruikt · bron: Jira-worklogs · waarschuwing bij 75% en 90%</p>` : '<div class="empty">Geen urenplafond</div>'}</div></div>
      <div class="card" style="margin-top:16px"><h2>Opdrachten</h2>${opdrachtTable(c.opdrachten)}</div>
      <div class="card" style="margin-top:16px"><h2>RFC's</h2>${rfcTable(c.rfcs.map((r) => ({ ...r, klantnaam: c.klantnaam, contractnummer: c.nummer })))}</div>`;
  } else if (tab === "versies") {
    T.innerHTML = `<div class="card">${table([{ l: "Versie", k: "versie" }, { l: "Soort", k: "soort_wijziging" }, { l: "Toelichting", k: "toelichting" }, { l: "Wijzigingen", f: (v) => esc(Object.entries(JSON.parse(v.snapshot || "{}")).map(([k, x]) => `${k}: ${x}`).join(", ")) }, { l: "Door", k: "gebruiker" }, { l: "Datum", f: (v) => dt(v.ts) }], c.versies)}</div>`;
  } else if (tab === "opmerkingen") {
    T.innerHTML = '<div class="card" id="opm"></div>';
    await opmBlock($("#opm"), "contract", c.id);
  } else T.innerHTML = `<div class="card">${await auditHtml("contract", id)}</div>`;
});

// ================================================================ OPDRACHTEN
const opdrachtTable = (rows) => table([{ l: "Nummer", f: (o) => `<a href="#/opdrachten/${o.id}">${esc(o.nummer)}</a>` }, { l: "Omschrijving", k: "omschrijving" }, { l: "Type", k: "type" },
  { l: "Periode", f: (o) => `${d(o.startdatum)} – ${d(o.einddatum)}` }, { l: "Uren", f: (o) => o.begrote_uren ? `${bar(100 * o.bestede_uren / o.begrote_uren)}<span class="small muted">${n0(o.bestede_uren)} / ${n0(o.begrote_uren)}</span>` : n0(o.bestede_uren) },
  { l: "Voortgang", num: 1, f: (o) => n0(o.voortgang) + "%" }, { l: "Status", f: (o) => badge(o.status) + (o.jira_epic_done && o.status !== "Afgerond" ? ' <span class="badge warn">Epic Done</span>' : "") }, { l: "Epic", f: (o) => o.jira_key ? `<span class="jira">${esc(o.jira_key)}</span>` : sync(o.jira_sync_status) }], rows, (o) => "#/opdrachten/" + o.id);
async function opdrachtForm(rec, edit) {
  const cts = (await get("/api/contracten" + (rec.klant_id ? "?klant_id=" + rec.klant_id : ""))).filter((c) => edit || ["Actief", "Verlengd"].includes(c.status));
  const ct = edit ? cts.find((c) => c.id === rec.contract_id) : null;
  const kid = rec.klant_id || ct?.klant_id;
  const cps = kid ? (await get("/api/klanten/" + kid)).contactpersonen.filter((c) => c.actief) : [];
  const F = [{ n: "contract_id", l: "Contract (alleen actieve)", t: "select", req: 1, full: 1, opts: cts.map((c) => ({ v: c.id, l: `${c.nummer} — ${c.klantnaam} (${c.startdatum} – ${c.einddatum || "onbepaald"})` })) },
    { n: "omschrijving", l: "Omschrijving en scope", t: "textarea", req: 1, full: 1 }, { n: "type", l: "Type", t: "select", req: 1, opts: S.keuzes.opdracht_type },
    { n: "status", l: "Status", t: "select", req: 1, opts: S.keuzes.opdracht_status, hint: "Bij 'Gepland' wordt een Epic in Jira aangemaakt" },
    { n: "startdatum", l: "Startdatum", t: "date", req: 1, hint: "Binnen contractlooptijd" }, { n: "einddatum", l: "Einddatum", t: "date", req: 1 },
    { n: "begrote_uren", l: "Begrote uren", t: "number" }, ...(S.user.waarde_zichtbaar ? [{ n: "budget", l: "Budget (EUR)", t: "number" }] : []),
    { n: "opdrachtgever_id", l: "Opdrachtgever", t: "select", req: 1, opts: cps.map((c) => ({ v: c.id, l: c.naam })) }];
  if (!kid) F.pop();
  const form = modal(edit ? `Opdracht ${rec.nummer} wijzigen` : "Nieuwe opdracht", (cts.length ? "" : '<div class="alert">Geen actief contract beschikbaar.</div>') + formHtml(F, { status: "Gepland", ...rec }), {
    onSubmit: async (f) => { const x = readForm(f, F); const o = edit ? await put("/api/opdrachten/" + rec.id, x) : await post("/api/opdrachten", x); toast("Opdracht opgeslagen"); edit ? soon() : (location.hash = "#/opdrachten/" + o.id); },
  });
  if (!kid) $("[name=contract_id]", form).onchange = async (e) => { const c = cts.find((x) => x.id == e.target.value); $("#modal").close(); opdrachtForm({ ...readForm(form, F), klant_id: c.klant_id }); };
}
page("opdrachten", async (main) => {
  const rows = await get("/api/opdrachten?status=" + encodeURIComponent(S.qs.status || ""));
  const can = ["accountmanager", "beheerder"].includes(S.user.rol);
  main.innerHTML = head("Opdrachten", `${rows.length} opdrachten`, can ? '<button class="btn pri" id="new">Nieuwe opdracht</button>' : "") + `<div class="card">
    <div class="filters"><select id="fs"><option value="">Alle statussen</option>${S.keuzes.opdracht_status.map((s) => `<option ${s === S.qs.status ? "selected" : ""}>${s}</option>`).join("")}</select></div>
    ${opdrachtTable(rows)}</div>`;
  $("#fs").onchange = (e) => (location.hash = "#/opdrachten?status=" + encodeURIComponent(e.target.value));
  if (can) $("#new").onclick = () => opdrachtForm({});
});
page("opdrachten/(\\d+)", async (main, id) => {
  const o = await get("/api/opdrachten/" + id);
  const w = o.mag_schrijven;
  main.innerHTML = head(esc(o.nummer), `${badge(o.status)} <span class="muted">${esc(o.type)} · <a href="#/klanten/${o.klant_id}">${esc(o.klantnaam)}</a> · <a href="#/contracten/${o.contract_id}">${esc(o.contractnummer)}</a></span>`,
    w ? `<button class="btn" id="edit">Wijzigen</button>${o.jira_epic_done && o.status !== "Afgerond" ? '<button class="btn pri" id="af">Afronden bevestigen</button>' : ""}` : "", '<a href="#/opdrachten">Opdrachten</a> /') +
    (o.jira_epic_done && o.status !== "Afgerond" ? '<div class="alert info">De Epic is in Jira op Done gezet. Bevestig het afronden van deze opdracht (FR-OP-04).</div>' : "") +
    `<div class="grid g2"><div class="card"><h2>Opdracht</h2><p>${esc(o.omschrijving)}</p><dl class="kv"><dt>Periode</dt><dd>${d(o.startdatum)} – ${d(o.einddatum)}</dd><dt>Opdrachtgever</dt><dd>${dash(o.opdrachtgever)}</dd>
      <dt>Budget</dt><dd>${S.user.waarde_zichtbaar ? eur(o.budget) : hidden}</dd><dt>Jira Epic</dt><dd>${jira(o.jira_key)} ${sync(o.jira_sync_status)}</dd></dl></div>
      <div class="card"><h2>Uitvoering (uit Jira)</h2><div class="grid g2"><div class="stat"><div class="l">Bestede uren</div><div class="v">${n0(o.bestede_uren)}</div><div class="small muted">van ${n0(o.begrote_uren)} begroot</div></div>
      <div class="stat"><div class="l">Voortgang</div><div class="v">${n0(o.voortgang)}%</div><div class="small muted">afgeronde issues</div></div></div>${o.begrote_uren ? bar(100 * o.bestede_uren / o.begrote_uren) : ""}</div></div>
      <div class="card" style="margin-top:16px"><h2>RFC's op deze opdracht</h2>${rfcTable(o.rfcs.map((r) => ({ ...r, klantnaam: o.klantnaam, contractnummer: o.contractnummer })))}</div>
      <div class="card" style="margin-top:16px"><h2 data-opm-titel>${opmLabel(o.opmerkingen_aantal)}</h2><div id="opm"></div></div>
      <div class="card" style="margin-top:16px"><h2>Audit trail</h2>${await auditHtml("opdracht", id)}</div>`;
  await opmBlock($("#opm"), "opdracht", o.id);
  if (w) { $("#edit").onclick = () => opdrachtForm(o, true); const a = $("#af"); if (a) a.onclick = async () => { await act(() => post(`/api/opdrachten/${id}/afronden`), "Opdracht afgerond"); reload(); }; }
});

// ================================================================ RFC's
async function rfcForm(rec, edit) {
  const kl = await get("/api/klanten?status=Actief");
  const kid = rec.klant_id || (kl.length === 1 ? kl[0].id : null);
  const k = kid ? await get("/api/klanten/" + kid) : null;
  const cts = k ? k.contracten.filter((c) => ["Actief", "Verlengd"].includes(c.status) || c.id === rec.contract_id) : [];
  const F = [
    { n: "klant_id", l: "Klant", t: "select", req: 1, opts: kl.map((x) => ({ v: x.id, l: x.naam })) },
    { n: "contract_id", l: "Contract", t: "select", req: 1, opts: cts.map((c) => ({ v: c.id, l: `${c.nummer} · ${c.soort} · ${c.change_regime}` })) },
    { n: "opdracht_id", l: "Opdracht (optioneel)", t: "select", opts: (k?.opdrachten || []).filter((o) => !rec.contract_id || o.contract_id == rec.contract_id).map((o) => ({ v: o.id, l: `${o.nummer} ${o.omschrijving}` })) },
    { n: "aanvrager_id", l: "Aanvrager", t: "select", req: 1, opts: (k?.contactpersonen || []).filter((c) => c.actief).map((c) => ({ v: c.id, l: c.naam })) },
    { n: "titel", l: "Titel", req: 1, full: 1 }, { n: "omschrijving", l: "Omschrijving", t: "textarea", full: 1 },
    { n: "change_type", l: "Change-type", t: "select", req: 1, opts: S.keuzes.change_type }, { n: "prioriteit", l: "Prioriteit", t: "select", req: 1, opts: S.keuzes.prioriteit },
    { n: "gewenste_datum", l: "Gewenste datum", t: "date" },
  ];
  const form = modal(edit ? "RFC wijzigen" : "Nieuwe RFC", formHtml(edit ? F.slice(1) : F, { change_type: "Normaal", prioriteit: "Midden", ...rec, klant_id: kid }) + '<h3 style="margin-top:16px">Contracttoets (voorlopig)</h3><div id="toets" class="small muted">Kies een contract</div>', {
    wide: 1, onSubmit: async (f) => { const x = readForm(f, edit ? F.slice(1) : F); const r = edit ? await put("/api/rfcs/" + rec.id, x) : await post("/api/rfcs", x); toast(edit ? "RFC opgeslagen; wordt naar Jira gesynchroniseerd" : "RFC aangemaakt; Change-issue wordt in JSM aangemaakt"); edit ? soon() : (location.hash = "#/rfcs/" + r.id); },
  });
  const ks = $("[name=klant_id]", form);
  if (ks) ks.onchange = () => { const x = readForm(form, F); $("#modal").close(); rfcForm({ ...x, klant_id: ks.value, contract_id: null }); };
  const cs = $("[name=contract_id]", form);
  const toets = async () => {
    if (!cs.value) return;
    const t = await post("/api/rfcs/contracttoets", { contract_id: cs.value, gewenste_datum: $("[name=gewenste_datum]", form).value });
    $("#toets").innerHTML = toetsHtml(t);
  };
  cs.onchange = () => { if (!edit) { const x = readForm(form, F); $("#modal").close(); rfcForm({ ...x, klant_id: kid }); } else toets(); };
  $("[name=gewenste_datum]", form).onchange = toets;
  if (cs.value) toets();
}
const toetsHtml = (t) => t ? `<div style="margin-bottom:6px">Uitkomst: ${badge(t.uitkomst)} ${t.meerwerk ? badge("meerwerk", "acc") : ""}</div><ul class="checks">${t.checks.map((c) => `<li>${c.ok === true ? "✅" : c.ok === false ? "❌" : "➖"} <span><b>${esc(c.check)}</b> <span class="muted">${esc(c.detail)}</span></span></li>`).join("")}</ul>` : '<span class="muted">Nog niet uitgevoerd</span>';

page("rfcs", async (main) => {
  const qs = S.qs;
  const p = new URLSearchParams(Object.entries(qs).filter(([, v]) => v)).toString();
  const [rows, kl] = await Promise.all([get("/api/rfcs?" + p), get("/api/klanten")]);
  const can = ["accountmanager", "servicemanager", "beheerder"].includes(S.user.rol);
  main.innerHTML = head("RFC's", `${rows.length} wijzigingsverzoeken`, can ? '<button class="btn pri" id="new">Nieuwe RFC</button>' : "") + `<div class="card">
    <div class="filters"><input id="fz" placeholder="Zoek titel of Jira-key" value="${esc(qs.zoek || "")}">
    <select id="fk"><option value="">Alle klanten</option>${kl.map((k) => `<option value="${k.id}" ${k.id == qs.klant_id ? "selected" : ""}>${esc(k.naam)}</option>`).join("")}</select>
    <select id="fs"><option value="">Alle statussen</option>${S.keuzes.rfc_status.map((s) => `<option ${s === qs.status ? "selected" : ""}>${s}</option>`).join("")}</select>
    <select id="ft"><option value="">Alle types</option>${S.keuzes.change_type.map((s) => `<option ${s === qs.change_type ? "selected" : ""}>${s}</option>`).join("")}</select></div>
    ${rfcTable(rows)}</div>`;
  const go = () => (location.hash = "#/rfcs?" + new URLSearchParams({ zoek: $("#fz").value, klant_id: $("#fk").value, status: $("#fs").value, change_type: $("#ft").value }).toString());
  ["#fz", "#fk", "#fs", "#ft"].forEach((s) => ($(s).onchange = go));
  if (can) $("#new").onclick = () => rfcForm({});
  autoRefresh(6000);
});
page("rfcs/(\\d+)", async (main, id) => {
  const r = await get("/api/rfcs/" + id);
  const tab = S.qs.tab || "overzicht";
  const ia = r.impactanalyses[0];
  main.innerHTML = head(`${r.jira_key ? `<span class="jira" style="font-size:18px">${esc(r.jira_key)}</span> ` : ""}${esc(r.titel)}`, `${badge(r.status)} ${badge(r.change_type, r.change_type === "Spoed" ? "bad" : "")} ${badge("Prio " + r.prioriteit, "")}`,
    `${r.mag_schrijven ? '<button class="btn" id="edit">Wijzigen</button><button class="btn" id="toetsb">Contracttoets opnieuw</button>' : ""}${r.jira_key ? `<a class="btn" href="#/jira?issue=${esc(r.jira_key)}">Open in Jira ↗</a>` : ""}`, '<a href="#/rfcs">RFC\'s</a> /') +
    (r.jira_sync_status !== "gesynchroniseerd" ? `<div class="alert ${r.jira_sync_status === "fout" ? "bad" : ""}">Jira-synchronisatie: ${esc(r.jira_sync_status)}${r.jira_sync_status === "wachtend" ? " — het Change-issue wordt aangemaakt zodra Jira bereikbaar is." : ""}</div>` : "") +
    (r.cab_vereist && !r.cab_besluit ? '<div class="alert">Risicoscore ≥ 15: CAB-behandeling vereist.</div>' : "") +
    tabs(main, [["overzicht", "Overzicht"], ["ia", `Impactanalyse (${r.impactanalyses.length})`], ["opmerkingen", opmLabel(r.opmerkingen_aantal)], ["audit", "Audit trail"]], tab) + '<div id="tab"></div>';
  bindTabs(main, (t) => (location.hash = `#/rfcs/${id}?tab=${t}`));
  if (r.mag_schrijven) { $("#edit").onclick = () => rfcForm(r, true); $("#toetsb").onclick = async () => { await act(() => post(`/api/rfcs/${id}/toets`), "Contracttoets uitgevoerd"); reload(); }; }
  const T = $("#tab");
  if (tab === "overzicht") {
    T.innerHTML = `<div class="grid g21"><div class="stack"><div class="card"><h2>Wijziging</h2><p>${esc(r.omschrijving || "Geen omschrijving")}</p><dl class="kv">
      <dt>Klant</dt><dd><a href="#/klanten/${r.klant_id}">${esc(r.klantnaam)}</a></dd><dt>Contract</dt><dd><a href="#/contracten/${r.contract_id}">${esc(r.contractnummer)}</a> <span class="muted">${esc(r.contractsoort)} · t/m ${r.contract_einddatum ? d(r.contract_einddatum) : "onbepaald"}</span></dd>
      <dt>Opdracht</dt><dd>${r.opdracht_id ? `<a href="#/opdrachten/${r.opdracht_id}">${esc(r.opdrachtnummer)}</a>` : "—"}</dd><dt>Aanvrager</dt><dd>${dash(r.aanvrager)}</dd>
      <dt>Gewenste datum</dt><dd>${d(r.gewenste_datum)}</dd><dt>Bron</dt><dd>${badge(r.bron, r.bron === "Jira" ? "acc" : "")}</dd>
      <dt>Geschatte kosten / uren</dt><dd>${S.user.waarde_zichtbaar ? eur(r.geschatte_kosten) : hidden} / ${n0(r.geschatte_uren)} u</dd>
      <dt>CAB-besluit</dt><dd>${r.cab_besluit ? `${badge(r.cab_besluit)} ${d(r.cab_datum)}` : "—"}</dd><dt>Betrokkenen</dt><dd>${r.betrokkenen.map((b) => `${esc(b.naam)} <span class="muted small">(${esc(b.rol)})</span>`).join(", ") || "—"}</dd></dl></div>
      ${r.offertes.length ? `<div class="card"><h2>Meerwerk</h2>${r.offertes.map((o) => `<div>${badge(o.status, "acc")} ${esc(o.omschrijving)} ${S.user.waarde_zichtbaar ? eur(o.waarde) : ""}</div>`).join("")}<a href="#/meerwerk" class="small">Naar meerwerk-pijplijn →</a></div>` : ""}</div>
      <div class="stack"><div class="card"><h2>Contracttoets ${badge(r.binnen_contract)}</h2>${toetsHtml(r.contracttoets)}<p class="small muted">Regime: ${esc(r.change_regime)} · SLA: ${dash(r.sla_niveau)}</p></div>
      <div class="card"><h2>Risico</h2>${ia ? `<div class="stat"><div class="v">${ia.risicoscore}</div><div class="l">kans ${ia.kans} × impact ${ia.impact} · versie ${ia.versie}</div></div>` : '<div class="empty">Nog geen impactanalyse (wordt in Jira ingevuld)</div>'}</div>
      <div class="card"><h2>Jira</h2><dl class="kv"><dt>Issue</dt><dd>${jira(r.jira_key)}</dd><dt>Sync</dt><dd>${sync(r.jira_sync_status)}</dd><dt>Laatste sync</dt><dd>${dt(r.last_sync)}</dd></dl></div></div></div>`;
  } else if (tab === "ia") {
    T.innerHTML = r.impactanalyses.length ? r.impactanalyses.map((x, i) => `<div class="card" style="margin-bottom:16px"><div class="pagehead" style="margin:0 0 8px"><h2 style="margin:0">Versie ${x.versie} ${i === 0 ? badge("actueel", "ok") : badge("eerdere versie")}</h2><span class="muted small">${esc(x.opgesteld_door)} · ${dt(x.datum)}</span><div class="actions"><a class="btn sm" target="_blank" href="/print/impactanalyse/${x.id}?u=${S.user.id}">PDF voor klant</a></div></div>
      <div class="grid g2"><dl class="kv"><dt>Techniek</dt><dd>${badge(x.impact_techniek)} ${esc(x.toelichting_techniek || "")}</dd><dt>Organisatie</dt><dd>${badge(x.impact_organisatie)} ${esc(x.toelichting_organisatie || "")}</dd>
      <dt>Planning</dt><dd>${dash(x.impact_planning)}</dd><dt>Kosten / uren</dt><dd>${S.user.waarde_zichtbaar ? eur(x.kosten) : hidden} / ${n0(x.uren)} u</dd><dt>Risico</dt><dd><b>${x.risicoscore}</b> (kans ${x.kans} × impact ${x.impact}) ${x.risicoscore >= 15 ? badge("CAB vereist", "bad") : ""}</dd>
      <dt>Contract / SLA</dt><dd>${dash(x.impact_contract)}</dd><dt>Security & privacy</dt><dd>${dash(x.security_privacy)} ${x.dpia_nodig ? badge("DPIA nodig", "warn") : ""}</dd><dt>Terugvalplan</dt><dd>${dash(x.terugvalplan)}</dd></dl>
      <div><h3>Vergelijking met contract</h3>${cvHtml(x.contractverschil)}</div></div></div>`).join("") : '<div class="card empty">De impactanalyse wordt in Jira ingevuld (formulier op het Change-issue) en verschijnt hier automatisch.</div>';
  } else if (tab === "opmerkingen") {
    T.innerHTML = '<div class="card" id="opm"></div>';
    await opmBlock($("#opm"), "rfc", r.id);
    return; // geen auto-refresh tijdens het schrijven van opmerkingen
  } else T.innerHTML = `<div class="card">${await auditHtml("rfc", id)}</div>`;
  autoRefresh(5000);
});
function cvHtml(cv) {
  if (!cv) return "—";
  return `<dl class="kv">${cv.uren ? `<dt>Urenbudget</dt><dd>rest ${n0(cv.uren.rest_budget)} u → na change ${n0(cv.uren.na_change)} u ${cv.uren.past ? badge("past", "ok") : badge("overschrijding", "bad")}</dd>` : "<dt>Urenbudget</dt><dd>geen plafond</dd>"}
    ${cv.kosten ? `<dt>Kosten</dt><dd>${S.user.waarde_zichtbaar ? `${eur(cv.kosten.impact)} = ${n0(cv.kosten.pct_van_contract)}% van contractwaarde` : hidden}</dd>` : ""}
    <dt>SLA</dt><dd>${dash(cv.sla?.niveau)} — ${dash(cv.sla?.impact)}</dd><dt>Regime</dt><dd>${dash(cv.regime)}</dd></dl>`;
}

// ================================================================ MEERWERK & SIGNALEN
page("meerwerk", async (main) => {
  const [rows, off] = await Promise.all([get("/api/rapport/meerwerk"), get("/api/offertes")]);
  main.innerHTML = head("Meerwerk & offertes", "RFC's buiten contract") + `<div class="card"><h2>Offertes</h2>${table([{ l: "RFC", f: (o) => `<a href="#/rfcs/${o.rfc_id}">${esc(o.jira_key || "#" + o.rfc_id)}</a>` }, { l: "Omschrijving", k: "omschrijving" }, { l: "Klant", k: "klantnaam" },
    { l: "Waarde", num: 1, f: (o) => S.user.waarde_zichtbaar ? eur(o.waarde) : hidden }, { l: "Status", f: (o) => badge(o.status, "acc") },
    { l: "", f: (o) => ["accountmanager", "beheerder"].includes(S.user.rol) ? `<select data-off="${o.id}">${["Concept", "Verstuurd", "Geaccepteerd", "Afgewezen"].map((s) => `<option ${s === o.status ? "selected" : ""}>${s}</option>`).join("")}</select>` : "" }], off)}</div>
    <div class="card" style="margin-top:16px"><h2>Pijplijn</h2>${table([{ l: "RFC", f: (r) => `<a href="#/rfcs/${r.id}">${esc(r.jira_key || "#" + r.id)}</a>` }, { l: "Titel", k: "titel" }, { l: "Klant", k: "klantnaam" }, { l: "Contract", k: "contractnummer" }, { l: "Regime", k: "change_regime" },
      { l: "Geschat", num: 1, f: (r) => S.user.waarde_zichtbaar ? eur(r.geschatte_kosten) : hidden }, { l: "Status", f: (r) => badge(r.status) }, { l: "CAB", f: (r) => badge(r.cab_besluit) }, { l: "Offerte", f: (r) => badge(r.offerte_status, "acc") }], rows, (r) => "#/rfcs/" + r.id)}</div>`;
  $$("[data-off]").forEach((s) => (s.onchange = async () => { await act(() => put(`/api/offertes/${s.dataset.off}`, { status: s.value }), "Offerte bijgewerkt"); reload(); }));
});
page("signalen", async (main) => {
  const s = await get("/api/signalen");
  main.innerHTML = head("Signalen", "Opzegtermijnen, urenbudget, meerwerk en afronding") + `<div class="card">${sigList(s)}</div>`;
  bindSig(main);
});

// ================================================================ RAPPORTAGES
const RAPP = [["contractkalender", "Contractkalender"], ["urenverbruik", "Urenverbruik per contract"], ["rfc-per-klant", "RFC-overzicht per klant"], ["meerwerk", "Meerwerk-pijplijn"], ["change-kpis", "Change-KPI's"], ["integratiestatus", "Integratiestatus"]];
page("rapportages", async (main) => {
  const tab = S.qs.r || "contractkalender";
  const f = { klant_id: S.qs.klant_id || "", soort: S.qs.soort || "", van: S.qs.van || "", tot: S.qs.tot || "" };
  const qs = new URLSearchParams(f).toString();
  const [kl, data] = await Promise.all([get("/api/klanten"), get(`/api/rapport/${tab}?${qs}`).catch((e) => ({ fout: e.message }))]);
  main.innerHTML = head("Rapportages", "", `<a class="btn" href="#" id="xls">Export Excel (CSV)</a><button class="btn" onclick="print()">PDF</button>`) +
    tabs(main, RAPP, tab) + `<div class="card"><div class="filters"><select id="fk"><option value="">Alle klanten</option>${kl.map((k) => `<option value="${k.id}" ${k.id == f.klant_id ? "selected" : ""}>${esc(k.naam)}</option>`).join("")}</select>
    <select id="fo"><option value="">Alle contractsoorten</option>${S.keuzes.contract_soort.map((s) => `<option ${s === f.soort ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <label class="small">Van <input type="date" id="fv" value="${f.van}"></label><label class="small">Tot <input type="date" id="ft" value="${f.tot}"></label></div><div id="rap"></div></div>`;
  bindTabs(main, (t) => (location.hash = `#/rapportages?r=${t}&${qs}`));
  const go = () => (location.hash = `#/rapportages?r=${tab}&` + new URLSearchParams({ klant_id: $("#fk").value, soort: $("#fo").value, van: $("#fv").value, tot: $("#ft").value }));
  ["#fk", "#fo", "#fv", "#ft"].forEach((s) => ($(s).onchange = go));
  $("#xls").onclick = (e) => { e.preventDefault(); download(`/api/export-rapport/${tab}?${qs}`); };
  const R = $("#rap");
  if (data.fout) { R.innerHTML = `<div class="alert bad">${esc(data.fout)}</div>`; return; }
  if (tab === "contractkalender") R.innerHTML = table([{ l: "Contract", f: (r) => `<a href="#/contracten/${r.id}">${esc(r.nummer)}</a>` }, { l: "Klant", k: "klantnaam" }, { l: "Soort", k: "soort" }, { l: "Status", f: (r) => badge(r.status) },
    { l: "Einddatum", f: (r) => d(r.einddatum) }, { l: "Uiterste opzegdatum", f: (r) => d(r.uiterste_opzegdatum) }, { l: "Dagen", num: 1, f: (r) => r.dagen_tot_opzeg < 0 ? badge("verstreken") : badge(r.dagen_tot_opzeg, r.dagen_tot_opzeg <= 31 ? "bad" : r.dagen_tot_opzeg <= 92 ? "warn" : "") }, { l: "Verlenging", k: "verlengingsvorm" }, { l: "Waarde", num: 1, f: (r) => S.user.waarde_zichtbaar ? eur(r.waarde) : hidden }], data, (r) => "#/contracten/" + r.id);
  if (tab === "urenverbruik") R.innerHTML = table([{ l: "Contract", f: (r) => `<a href="#/contracten/${r.id}">${esc(r.nummer)}</a>` }, { l: "Klant", k: "klantnaam" }, { l: "Soort", k: "soort" }, { l: "Begroot", num: 1, f: (r) => n0(r.begroot) }, { l: "Besteed", num: 1, f: (r) => n0(r.besteed) },
    { l: "", f: (r) => bar(r.pct) }, { l: "%", num: 1, f: (r) => n0(r.pct) }, { l: "Prognose einde", num: 1, f: (r) => r.prognose_einde == null ? "—" : `${n0(r.prognose_einde)} u (${r.prognose_pct}%)` }], data, (r) => "#/contracten/" + r.id);
  if (tab === "rfc-per-klant") R.innerHTML = table([{ l: "Klant", f: (r) => `<a href="#/rfcs?klant_id=${r.id}">${esc(r.klantnaam)}</a>` }, { l: "Open", num: 1, k: "open" }, { l: "Goedgekeurd", num: 1, k: "goedgekeurd" }, { l: "Afgewezen", num: 1, k: "afgewezen" }, { l: "Totaal", num: 1, k: "totaal" }, { l: "Gem. doorlooptijd (dagen)", num: 1, f: (r) => n0(r.doorlooptijd_dagen) }], data);
  if (tab === "meerwerk") R.innerHTML = table([{ l: "RFC", f: (r) => `<a href="#/rfcs/${r.id}">${esc(r.jira_key || "#" + r.id)}</a>` }, { l: "Titel", k: "titel" }, { l: "Klant", k: "klantnaam" }, { l: "Geschatte waarde", num: 1, f: (r) => S.user.waarde_zichtbaar ? eur(r.geschatte_kosten) : hidden }, { l: "Status", f: (r) => badge(r.status) }, { l: "Offerte", f: (r) => badge(r.offerte_status, "acc") }], data);
  if (tab === "change-kpis") {
    const max = Math.max(1, ...data.per_type.map((x) => x.n), ...data.per_status.map((x) => x.n));
    const bars = (rows, key) => rows.map((x) => `<div style="display:grid;grid-template-columns:110px 1fr 40px;gap:8px;align-items:center;margin:6px 0"><span>${esc(x[key])}</span><div class="bar"><i style="width:${100 * x.n / max}%"></i></div><span class="num">${x.n}</span></div>`).join("");
    R.innerHTML = `<div class="grid g4" style="margin-bottom:16px"><div class="stat"><div class="l">Changes</div><div class="v">${data.totaal}</div></div><div class="stat"><div class="l">Spoed</div><div class="v">${data.spoed}</div></div>
      <div class="stat"><div class="l">Succesratio</div><div class="v">${data.succesratio == null ? "—" : data.succesratio + "%"}</div></div><div class="stat"><div class="l">Gem. risicoscore</div><div class="v">${data.gem_risicoscore ?? "—"}</div></div></div>
      <div class="grid g2"><div><h3>Per type</h3>${bars(data.per_type, "change_type")}</div><div><h3>Per status</h3>${bars(data.per_status, "status")}</div></div>`;
  }
  if (tab === "integratiestatus") R.innerHTML = integratieHtml(data);
});
function download(url) {
  fetch(url, { headers: { "X-User": S.user.id } }).then(async (r) => {
    if (!r.ok) throw new Error((await r.json()).fout);
    const b = await r.blob(); const a = document.createElement("a");
    a.href = URL.createObjectURL(b); a.download = (r.headers.get("content-disposition") || "").split('filename="')[1]?.replace('"', "") || "export.csv"; a.click();
  }).catch((e) => toast(e.message, true));
}

// ================================================================ INTEGRATIE (beheer)
function integratieHtml(st) {
  const t = st.tellingen;
  return `<div class="grid g4"><div class="card stat"><div class="l">Health</div><div class="v">${badge(st.health === "ok" ? "OK" : "Verstoord", st.health === "ok" ? "ok" : "bad")}</div><div class="small muted">${esc(st.adapter)}</div></div>
    <div class="card stat"><div class="l">Queue-diepte</div><div class="v">${st.queue_diepte}</div><div class="small muted">wachtend + retry</div></div>
    <div class="card stat"><div class="l">Sync-vertraging P95</div><div class="v">${st.p95_sync_seconden == null ? "—" : st.p95_sync_seconden + " s"}</div><div class="small muted">norm ≤ 60 s · ${st.verwerkt_laatste_uur} berichten afgelopen uur</div></div>
    <div class="card stat"><div class="l">Foutpercentage</div><div class="v">${st.foutpercentage}%</div><div class="small muted">DLQ ${t.dlq || 0} · geweigerd ${t.geweigerd || 0} · conflicten ${st.open_conflicten}</div></div></div>
    <p class="small muted" style="margin-top:12px">Verwerkt ${t.verwerkt || 0} · genegeerd (lusvoorkoming) ${t.genegeerd || 0} · laatste reconciliatie ${st.laatste_reconciliatie ? `${dt(st.laatste_reconciliatie.start)}: ${st.laatste_reconciliatie.gecontroleerd} gecontroleerd, ${st.laatste_reconciliatie.hersteld} hersteld, ${st.laatste_reconciliatie.opnieuw_aangeboden} opnieuw aangeboden${st.laatste_reconciliatie.fout ? " — fout: " + esc(st.laatste_reconciliatie.fout) : ""}` : "nog niet uitgevoerd"}</p>
    ${st.recente_fouten ? `<h3 style="margin-top:16px">Recente fouten</h3>${table([{ l: "#", k: "id" }, { l: "Richting", k: "richting" }, { l: "Type", k: "type" }, { l: "Status", f: (m) => badge(m.status) }, { l: "Pogingen", num: 1, k: "pogingen" }, { l: "Fout", k: "fout" }], st.recente_fouten)}` : ""}`;
}
page("integratie", async (main) => {
  const st = await get("/api/integratie/status");
  const beh = S.user.rol === "beheerder";
  const tab = S.qs.tab || "status";
  main.innerHTML = head("Integratiestatus", "CRM ↔ integratielaag ↔ Jira", beh ? '<button class="btn" id="dag">Dagtaken uitvoeren</button><button class="btn pri" id="rec">Reconciliatie starten</button>' : "") +
    (st.jira_offline ? '<div class="alert bad">Jira is niet bereikbaar. Het CRM blijft werken; uitgaande berichten worden gebufferd en met exponentiële backoff opnieuw aangeboden (max. 5 pogingen, daarna dead-letter queue).</div>' : "") +
    (beh ? tabs(main, [["status", "Status"], ["berichten", "Berichten"], ["dlq", `Dead-letter queue (${(st.tellingen.dlq || 0) + (st.tellingen.geweigerd || 0)})`], ["conflicten", `Conflicten (${st.open_conflicten})`], ["mapping", "Mapping & configuratie"]], tab) : "") + '<div id="tab"></div>';
  const T = $("#tab");
  if (beh) {
    bindTabs(main, (t) => (location.hash = "#/integratie?tab=" + t));
    $("#rec").onclick = async () => { const r = await act(() => post("/api/integratie/reconciliatie")); toast(r.fout ? "Reconciliatie mislukt: " + r.fout : `Reconciliatie klaar: ${r.gecontroleerd} gecontroleerd, ${r.hersteld} hersteld, ${r.opnieuw_aangeboden} opnieuw aangeboden`, !!r.fout); soon(); };
    $("#dag").onclick = async () => { const r = await act(() => post("/api/integratie/dagtaken")); toast(`Dagtaken: ${r.verlopen} verlopen, ${r.signalen} signalen, ${r.geanonimiseerd} geanonimiseerd`); soon(); };
  }
  if (tab === "status" || !beh) { T.innerHTML = `<div class="card">${integratieHtml(st)}</div>`; autoRefresh(4000); return; }
  if (tab === "berichten" || tab === "dlq") {
    const rows = tab === "dlq" ? [...await get("/api/integratie/berichten?status=dlq"), ...await get("/api/integratie/berichten?status=geweigerd")] : await get("/api/integratie/berichten");
    T.innerHTML = `<div class="card">${table([{ l: "#", k: "id" }, { l: "Tijd", f: (m) => dt(m.ts) }, { l: "Richting", f: (m) => m.richting === "uit" ? "CRM → Jira" : "Jira → CRM" }, { l: "Type", f: (m) => `<span class="mono">${esc(m.type)}</span>` },
      { l: "Object", f: (m) => `${esc(m.entiteit)} ${esc(m.entiteit_id || "")}` }, { l: "Status", f: (m) => badge(m.status) }, { l: "Pogingen", num: 1, k: "pogingen" }, { l: "Volgende", f: (m) => m.status === "retry" ? dt(m.volgende_poging) : "" },
      { l: "Fout", f: (m) => `<span class="small">${dash(m.fout)}</span>` }, { l: "", f: (m) => `<button class="btn sm" data-view="${m.id}">Bekijk</button>${["dlq", "geweigerd", "retry"].includes(m.status) ? ` <button class="btn sm" data-retry="${m.id}">Opnieuw</button>` : ""}` }], rows)}</div>`;
    $$("[data-retry]").forEach((b) => (b.onclick = async () => { await act(() => post(`/api/integratie/berichten/${b.dataset.retry}/opnieuw`), "Opnieuw aangeboden"); soon(); }));
    $$("[data-view]").forEach((b) => (b.onclick = async () => { const m = await get("/api/integratie/berichten/" + b.dataset.view); let p = m.payload; try { p = JSON.stringify(JSON.parse(p), null, 2); } catch {} modal(`Bericht #${m.id} · ${m.type}`, `<dl class="kv"><dt>Event-ID</dt><dd class="mono">${esc(m.event_id)}</dd><dt>Status</dt><dd>${badge(m.status)}</dd><dt>Fout</dt><dd>${dash(m.fout)}</dd></dl><pre>${esc(p)}</pre>`, { wide: 1 }); }));
    if (tab === "berichten") autoRefresh(4000);
  } else if (tab === "conflicten") {
    const rows = await get("/api/integratie/conflicten");
    T.innerHTML = `<div class="card"><p class="small muted">Bij gelijktijdige wijziging van een bidirectioneel veld wint de laatste wijziging; ongeldige contractkeuzes in Jira worden door het CRM teruggezet.</p>${table([{ l: "Tijd", f: (c) => dt(c.ts) }, { l: "Object", f: (c) => `<a href="#/rfcs/${c.entiteit_id}">${esc(c.entiteit)} #${c.entiteit_id}</a>` }, { l: "Veld", k: "veld" }, { l: "CRM-waarde", k: "crm_waarde" }, { l: "Jira-waarde", k: "jira_waarde" }, { l: "Winnaar", f: (c) => badge(c.winnaar, "acc") }, { l: "", f: (c) => c.opgelost ? badge("afgehandeld", "ok") : `<button class="btn sm" data-ok="${c.id}">Afhandelen</button>` }], rows)}</div>`;
    $$("[data-ok]").forEach((b) => (b.onclick = async () => { await post(`/api/integratie/conflicten/${b.dataset.ok}/opgelost`); reload(); }));
  } else if (tab === "mapping") {
    const cfg = await get("/api/integratie/config");
    const keys = ["statusmapping", "veldmapping_rfc", "drempels", "retry", "bewaartermijn_maanden", "jira_sites"];
    T.innerHTML = `<div class="card"><p class="small muted">Veld- en statusmapping zijn configureerbaar door functioneel beheer, zonder code-aanpassing (NFR-BH-01). Wijzigingen worden in de audit trail vastgelegd.</p>
      <h3>Statusmapping (Jira-status → CRM-status)</h3>${table([{ l: "Jira", k: "j" }, { l: "CRM", f: (r) => `<select data-sm="${esc(r.j)}">${S.keuzes.rfc_status.map((s) => `<option ${s === r.c ? "selected" : ""}>${s}</option>`).join("")}</select>` }], Object.entries(cfg.statusmapping).map(([j, c]) => ({ j, c })))}
      <h3 style="margin-top:16px">Veldmapping RFC ↔ Change-issue</h3>${table([{ l: "CRM-veld", k: "crm" }, { l: "Jira-veld", k: "jira" }, { l: "Richting", f: (r) => badge(r.richting, "acc") }], cfg.veldmapping_rfc)}
      <h3 style="margin-top:16px">Overige configuratie (JSON)</h3>${keys.slice(2).map((k) => `<label class="small muted" style="display:block;margin-top:8px">${k}<textarea data-cfg="${k}" class="mono" style="min-height:60px">${esc(JSON.stringify(cfg[k], null, 1))}</textarea></label>`).join("")}
      <div style="margin-top:12px"><button class="btn pri" id="save">Configuratie opslaan</button></div></div>`;
    $("#save").onclick = async () => {
      const body = { statusmapping: Object.fromEntries($$("[data-sm]").map((s) => [s.dataset.sm, s.value])) };
      try { $$("[data-cfg]").forEach((t) => (body[t.dataset.cfg] = JSON.parse(t.value))); } catch (e) { return toast("Ongeldige JSON: " + e.message, true); }
      await act(() => put("/api/integratie/config", body), "Configuratie opgeslagen");
    };
  }
});

// ================================================================ IMPORT / EXPORT
page("import", async (main) => {
  const ex = [["klanten", "Klanten"], ["contactpersonen", "Contactpersonen"], ["contracten", "Contracten"], ["opdrachten", "Opdrachten"], ["rfcs", "RFC's"], ["impactanalyses", "Impactanalyses"], ["audit", "Audit trail"]];
  main.innerHTML = head("Import / export", "Voor initiële migratie en rapportage (CSV, opent in Excel)") + `<div class="grid g2">
    <div class="card"><h2>Exporteren</h2><p class="small muted">Exports worden in de audit trail gelogd. Afgeschermde velden (zoals contractwaarde) blijven afgeschermd voor jouw rol.</p>${ex.map(([k, l]) => `<button class="btn" style="margin:0 6px 6px 0" data-ex="${k}">${l}</button>`).join("")}</div>
    <div class="card"><h2>Importeren</h2><div class="form"><label>Soort<select id="iw"><option value="klanten">Klanten</option><option value="contactpersonen">Contactpersonen</option></select></label>
      <label>Bestand<input type="file" id="if" accept=".csv,text/csv"></label><label class="full">Of plak CSV (scheidingsteken ; of ,)<textarea id="it" class="mono" placeholder="naam;kvk;bezoekadres;plaats;accountmanager;status"></textarea></label>
      <label class="chk full"><input type="checkbox" id="iforce"> Dubbele klanten toch importeren</label></div>
      <p class="small muted">Klanten: naam, kvk, bezoekadres, plaats, email_domein, branche, accountmanager (naam of e-mail), status. Contactpersonen: kvk (van klant), voornaam, achternaam, email, functie, rollen (komma-gescheiden), avg_grondslag.</p>
      <button class="btn pri" id="ib">Importeren</button><div id="ir" style="margin-top:12px"></div></div></div>`;
  $$("[data-ex]").forEach((b) => (b.onclick = () => download("/api/export/" + b.dataset.ex)));
  $("#if").onchange = async (e) => { $("#it").value = await e.target.files[0].text(); };
  $("#ib").onclick = async () => {
    const r = await act(() => post("/api/import/" + $("#iw").value, { csv: $("#it").value, forceer: $("#iforce").checked }));
    $("#ir").innerHTML = `<div class="alert ${r.fouten.length ? "" : "ok"}">${r.aangemaakt} van ${r.ingelezen} regels geïmporteerd.</div>${r.fouten.length ? table([{ l: "Regel", k: "regel" }, { l: "Fout", k: "fout" }, { l: "Mogelijke dubbelen", f: (f) => esc(f.dubbelen.join(", ")) }], r.fouten) : ""}`;
  };
});

// ================================================================ JIRA-SIMULATOR
page("jira", async (main) => {
  const s = await get("/api/jira-sim");
  const focus = S.qs.issue;
  const tab = S.qs.tab || "issues";
  const changes = s.issues.filter((i) => i.issuetype === "Change"), epics = s.issues.filter((i) => i.issuetype === "Epic");
  const contracts = s.assets.filter((a) => a.objtype === "Contract");
  main.innerHTML = head("Jira-simulator", "Sandbox die zich gedraagt als Jira Cloud + JSM + Assets en webhooks naar het CRM stuurt",
    `<label class="btn"><input type="checkbox" id="off" ${s.offline ? "checked" : ""}> Storing simuleren (Jira onbereikbaar)</label><button class="btn pri" id="portal">Request via klantportaal</button>`) +
    `<div class="alert info">Acties hier gebeuren als Jira-gebruiker (agent, CAB-lid, engineer, klant). Het CRM verwerkt de webhooks via de integratielaag; wijzigingen van het serviceaccount <span class="mono">svc-integratie</span> worden niet teruggesynchroniseerd.</div>` +
    tabs(main, [["issues", `Changes (${changes.length})`], ["epics", `Epics & taken (${epics.length})`], ["jsm", `Organizations & Customers (${s.orgs.length})`], ["assets", `Assets (${s.assets.length})`]], tab) + '<div id="tab"></div>';
  bindTabs(main, (t) => (location.hash = "#/jira?tab=" + t));
  $("#off").onchange = async (e) => { await post("/api/jira-sim/offline", { offline: e.target.checked }); toast(e.target.checked ? "Jira is nu onbereikbaar voor de integratielaag" : "Jira is weer bereikbaar; gebufferde berichten worden verwerkt"); };
  $("#portal").onclick = () => {
    const F = [{ n: "reporter", l: "Klant (JSM Customer)", t: "select", req: 1, full: 1, opts: s.customers.filter((c) => c.actief).map((c) => ({ v: c.account_id, l: `${c.naam} <${c.email}>` })) },
      { n: "contract", l: "Contract (Assets-veld, verplicht)", t: "select", req: 1, full: 1, opts: contracts.map((a) => ({ v: a.key, l: `${a.label} · ${a.attrs.Soort} · ${a.attrs.Status}` })) },
      { n: "summary", l: "Samenvatting", req: 1, full: 1 }, { n: "description", l: "Beschrijving", t: "textarea", full: 1 },
      { n: "changeType", l: "Change type", t: "select", req: 1, opts: S.keuzes.change_type }, { n: "priority", l: "Prioriteit", t: "select", req: 1, opts: S.keuzes.prioriteit }, { n: "gewenste_datum", l: "Gewenste datum", t: "date" }];
    modal("Change-request indienen via JSM-portaal", formHtml(F, { changeType: "Normaal", priority: "Midden" }), { submit: "Indienen", onSubmit: async (f) => { const r = await post("/api/jira-sim/portaal", readForm(f, F)); toast(`${r.key} aangemaakt in JSM; CRM koppelt klant en voert contracttoets uit`); setTimeout(() => (location.hash = "#/jira?issue=" + r.key), 300); } });
  };
  const T = $("#tab");
  const fl = (i) => i.fields;
  if (tab === "issues") {
    const list = focus ? [...changes.filter((i) => i.key === focus), ...changes.filter((i) => i.key !== focus)] : changes;
    T.innerHTML = list.map((i) => `<div class="simissue" id="i-${esc(i.key)}" style="${i.key === focus ? "border-color:var(--jira);box-shadow:0 0 0 2px var(--accent-soft)" : ""}">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="jira" style="font-size:14px">${esc(i.key)}</span><b>${esc(i.summary)}</b>${badge(i.status)}<span class="small muted">· ${esc(fl(i).changeType || "")} · prio ${esc(fl(i).priority || "")} · via ${esc(fl(i).kanaal || "?")}</span></div>
      <div class="small muted" style="margin-top:4px">Contract <span class="mono">${esc(fl(i).cf_contract || "—")}</span> ${esc(fl(i).cf_contract_nummer || "")} · ${esc(fl(i).cf_contract_soort || "")} · einde ${esc(fl(i).cf_contract_einddatum || "")} · regime ${esc(fl(i).cf_change_regime || "")} · <b>Binnen contract: ${esc(fl(i).cf_binnen_contract || "—")}</b>${fl(i).cf_risicoscore ? ` · risicoscore <b>${fl(i).cf_risicoscore}</b> (IA v${fl(i).cf_ia_versie})` : ""}${i.parent ? ` · Epic ${esc(i.parent)}` : ""}</div>
      <div class="row">${({ Open: ["Beoordeling"], Beoordeling: ["CAB", "Goedgekeurd", "Afgewezen"], Goedgekeurd: ["Uitgevoerd"], Uitgevoerd: ["Gesloten"], Afgewezen: ["Gesloten"] }[i.status] || []).map((n) => `<button class="btn sm" data-tr="${esc(i.key)}" data-to="${n}">${n === "Beoordeling" && i.status === "Open" ? "Indienen → Beoordeling" : "→ " + n}</button>`).join("")}
        ${["Beoordeling", "CAB"].includes(i.status) ? `<button class="btn sm" data-ia="${esc(i.key)}">Impactanalyse-formulier${fl(i).cf_ia_versie ? " (herzien)" : ""}</button>` : ""}
        ${i.status === "CAB" ? `<button class="btn sm pri" data-ap="${esc(i.key)}" data-b="approve">CAB: goedkeuren</button><button class="btn sm danger" data-ap="${esc(i.key)}" data-b="decline">CAB: afwijzen</button>` : ""}
        <button class="btn sm" data-ed="${esc(i.key)}">Velden wijzigen (agent)</button></div></div>`).join("") || '<div class="card empty">Nog geen Change-issues</div>';
    $$("[data-tr]").forEach((b) => (b.onclick = async () => { await act(() => post(`/api/jira-sim/issue/${b.dataset.tr}/transitie`, { naar: b.dataset.to, als: "change.manager" }), `${b.dataset.tr} → ${b.dataset.to}`); soon(); }));
    $$("[data-ap]").forEach((b) => (b.onclick = async () => { await act(() => post(`/api/jira-sim/issue/${b.dataset.ap}/approval`, { besluit: b.dataset.b, als: "cab-lid.klant" }), "CAB-besluit vastgelegd"); soon(); }));
    $$("[data-ia]").forEach((b) => (b.onclick = () => {
      const i = changes.find((x) => x.key === b.dataset.ia), prev = fl(i).cf_impactanalyse || {};
      const imp = S.keuzes.impact, sc = [1, 2, 3, 4, 5];
      const F = [{ n: "impact_techniek", l: "Impact techniek", t: "select", req: 1, opts: imp }, { n: "toelichting_techniek", l: "Toelichting techniek" },
        { n: "impact_organisatie", l: "Impact organisatie / gebruikers", t: "select", req: 1, opts: imp }, { n: "toelichting_organisatie", l: "Toelichting organisatie" },
        { n: "impact_planning", l: "Impact planning", full: 1 }, { n: "kosten", l: "Kosten (EUR)", t: "number", req: 1 }, { n: "uren", l: "Uren", t: "number", req: 1 },
        { n: "kans", l: "Kans (1–5)", t: "select", req: 1, opts: sc }, { n: "impact", l: "Impact (1–5)", t: "select", req: 1, opts: sc },
        { n: "impact_contract", l: "Impact contract / SLA", t: "textarea", req: 1, full: 1 }, { n: "security_privacy", l: "Security en privacy impact", t: "textarea", req: 1, full: 1 },
        { n: "dpia_nodig", l: "DPIA nodig", t: "check", full: 1 }, { n: "terugvalplan", l: "Terugvalplan", t: "textarea", req: 1, full: 1 }];
      const f = modal(`Impactanalyse — ${i.key}`, formHtml(F, { kans: 3, impact: 3, impact_techniek: "Midden", impact_organisatie: "Laag", ...prev }) + '<p id="rs" class="small"></p>', { wide: 1, submit: "Formulier indienen", onSubmit: async (f) => { const x = readForm(f, F); x.dpia_nodig = !!x.dpia_nodig; await post(`/api/jira-sim/issue/${i.key}/impactanalyse`, { ...x, als: "change.manager" }); toast("Formulier ingediend; nieuwe IA-versie gaat naar CRM"); soon(); } });
      const upd = () => { const v = $("[name=kans]", f).value * $("[name=impact]", f).value; $("#rs").innerHTML = `Risicoscore: <b>${v}</b> ${v >= 15 ? badge("CAB-behandeling vereist", "bad") : ""}`; };
      $("[name=kans]", f).onchange = upd; $("[name=impact]", f).onchange = upd; upd();
    }));
    $$("[data-ed]").forEach((b) => (b.onclick = () => {
      const i = changes.find((x) => x.key === b.dataset.ed);
      const F = [{ n: "summary", l: "Summary", full: 1 }, { n: "changeType", l: "Change type", t: "select", opts: S.keuzes.change_type }, { n: "priority", l: "Priority", t: "select", opts: S.keuzes.prioriteit },
        { n: "cf_contract", l: "Contract (Assets)", t: "select", full: 1, opts: contracts.map((a) => ({ v: a.key, l: `${a.label} · ${a.attrs.Status}` })) }];
      modal(`${i.key} bewerken als agent`, formHtml(F, { summary: i.summary, ...fl(i) }), { onSubmit: async (f) => { await post(`/api/jira-sim/issue/${i.key}/velden`, { ...readForm(f, F), als: "agent.jansen" }); toast("Opgeslagen in Jira; webhook naar CRM"); soon(); } });
    }));
    if (focus) $("#i-" + CSS.escape(focus))?.scrollIntoView({ block: "center" });
  } else if (tab === "epics") {
    const kids = (k) => s.issues.filter((i) => i.parent === k && i.issuetype !== "Change");
    const wl = (k) => s.worklogs.filter((w) => w.issue_key === k || kids(k).some((c) => c.key === w.issue_key)).reduce((a, w) => a + w.uren, 0);
    T.innerHTML = epics.map((e) => `<div class="simissue" style="${e.key === focus ? "border-color:var(--jira)" : ""}"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="jira" style="font-size:14px">${esc(e.key)}</span><b>${esc(e.summary)}</b>${badge(e.status, e.status === "Done" ? "ok" : "acc")}<span class="small muted">· ${esc(fl(e).cf_contract_nummer || "")} · ${n0(wl(e.key))} u gelogd</span></div>
      ${kids(e.key).map((k) => `<div class="small" style="margin:6px 0 0 16px"><span class="jira">${esc(k.key)}</span> ${esc(k.summary)} ${badge(k.status, k.status === "Done" ? "ok" : "")} ${k.status !== "Done" ? `<button class="btn sm" data-st="${esc(k.key)}" data-to="Done">→ Done</button>` : ""} <button class="btn sm" data-wl="${esc(k.key)}">Uren loggen</button></div>`).join("")}
      <div class="row"><button class="btn sm" data-wl="${esc(e.key)}">Uren loggen op Epic</button><button class="btn sm" data-task="${esc(e.key)}">Taak toevoegen</button>${e.status !== "Done" ? `<button class="btn sm" data-st="${esc(e.key)}" data-to="Done">Epic → Done</button>` : ""}</div></div>`).join("") || '<div class="card empty">Nog geen Epics</div>';
    $$("[data-wl]").forEach((b) => (b.onclick = () => { const F = [{ n: "uren", l: "Uren", t: "number", req: 1, step: "0.25", v: 8 }]; modal(`Worklog op ${b.dataset.wl}`, formHtml(F), { submit: "Loggen", onSubmit: async (f) => { await post(`/api/jira-sim/issue/${b.dataset.wl}/worklog`, { ...readForm(f, F), als: "engineer.devries" }); toast("Worklog geboekt; uren gaan naar CRM"); soon(); } }); }));
    $$("[data-task]").forEach((b) => (b.onclick = () => { const F = [{ n: "summary", l: "Samenvatting", req: 1, full: 1 }]; modal(`Taak onder ${b.dataset.task}`, formHtml(F), { onSubmit: async (f) => { await post(`/api/jira-sim/issue/${b.dataset.task}/taak`, { ...readForm(f, F), als: "engineer.devries" }); soon(); } }); }));
    $$("[data-st]").forEach((b) => (b.onclick = async () => { await act(() => post(`/api/jira-sim/issue/${b.dataset.st}/status`, { status: b.dataset.to, als: "engineer.devries" }), `${b.dataset.st} → Done`); soon(); }));
  } else if (tab === "jsm") {
    T.innerHTML = `<div class="grid g2"><div class="card"><h2>Organizations</h2>${table([{ l: "ID", k: "id" }, { l: "Naam", k: "naam" }, { l: "Actieve customers", num: 1, k: "klanten" }], s.orgs)}</div>
      <div class="card"><h2>Customers</h2>${table([{ l: "Account", f: (c) => `<span class="mono">${esc(c.account_id)}</span>` }, { l: "Naam", k: "naam" }, { l: "E-mail", k: "email" }, { l: "Org", k: "org_id" }, { l: "Actief", f: (c) => badge(c.actief ? "ja" : "nee", c.actief ? "ok" : "") }], s.customers)}</div></div>`;
  } else {
    T.innerHTML = `<div class="card">${table([{ l: "Key", f: (a) => `<span class="jira">${esc(a.key)}</span>` }, { l: "Type", k: "objtype" }, { l: "Label", k: "label" }, { l: "Attributen", f: (a) => `<span class="small">${esc(Object.entries(a.attrs).map(([k, v]) => `${k}: ${v}`).join(" · "))}</span>` }, { l: "Bijgewerkt", f: (a) => dt(a.updated) }], s.assets)}</div>`;
  }
});

// ================================================================ LOGIN / BOOT
async function loginScreen() {
  const { users, rollen } = await get("/api/users");
  $("#app").innerHTML = `<div class="login"><div class="brand" style="font-size:18px;padding:0 0 6px"><i></i> CRM · Jira</div><h1 style="margin-bottom:6px">Inloggen</h1>
    <p class="muted">Gesimuleerde SSO (Entra ID). Kies een gebruiker om de rolgebaseerde rechten te ervaren.</p>${users.map((u) => `<button class="btn u" data-u="${u.id}"><span><b>${esc(u.naam)}</b><br><span class="small muted">${esc(u.email)}</span></span><span class="badge acc">${esc(rollen[u.rol])}</span></button>`).join("")}</div>`;
  $$("[data-u]").forEach((b) => (b.onclick = async () => { S.user = await post("/api/login", { id: +b.dataset.u }); try { localStorage.setItem("crm_user", S.user.id); } catch {} boot(); }));
}
async function boot() {
  try { const t = localStorage.getItem("crm_theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  let id = null;
  try { id = localStorage.getItem("crm_user"); } catch {}
  if (!S.user && id) S.user = { id };
  if (!S.user) return loginScreen();
  try { Object.assign(S.user, await get("/api/me")); } catch { S.user = null; return loginScreen(); }
  S.keuzes = await get("/api/keuzes");
  shell();
  router();
}
window.addEventListener("hashchange", router);
setInterval(() => S.user && $("#sigcount") && refreshMe(), 15000);
boot();
