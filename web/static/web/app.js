/* Murimi Farm ERP web app.
 *
 * One page. It talks to the Django server only through /api/... (JSON), the
 * same way as Murimi Intelligent Hub. Screens are chosen by the part of the
 * address after # (for example #/farms), so the page never reloads.
 *
 *   API       sends requests (with the CSRF token) and turns errors into messages
 *   S         what the app currently knows: who is logged in, which organisation
 *   openForm  one reusable pop-up form for adding and editing anything
 *   pages     one function per screen: home, farms, seasons, team, rates, history
 */
'use strict';

/* ---------------- small helpers ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ico = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`;
const num = (v, dp = 2) => (v === null || v === undefined || v === '') ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
const ha = (v) => `${num(v)} ha`;
const money = (v, cur) => (v === null || v === undefined || v === '') ? '—' : `${cur === 'USD' ? 'US$' : cur + ' '}${num(v)}`;
const fmtDate = (iso) => iso ? new Date(iso + (iso.length === 10 ? 'T12:00:00' : '')).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const todayISO = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private window: fine */ } },
};
function cookie(name) { const m = document.cookie.match('(?:^|; )' + name + '=([^;]*)'); return m ? decodeURIComponent(m[1]) : ''; }
function toast(msg, force) {
  if (!force && toast.hold && Date.now() - toast.hold < 2500) return;  // keep "saved on this phone" on screen
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 2600);
}

/* ---------------- offline: pages saved on this phone, and the outbox ---------------- */
function uuid() {
  if (window.isSecureContext && crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16)); b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
const DB = {
  db: null,
  open() {
    if (!this.db) this.db = new Promise((res, rej) => {
      if (!window.indexedDB) return rej(new Error('No storage on this browser'));
      const r = indexedDB.open('murimi-erp', 1);
      r.onupgradeneeded = () => { r.result.createObjectStore('pages'); r.result.createObjectStore('outbox', { keyPath: 'id' }); };
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
    return this.db;
  },
  async run(store, mode, fn) {
    const db = await this.open();
    return new Promise((res, rej) => {
      const t = db.transaction(store, mode); const req = fn(t.objectStore(store));
      t.oncomplete = () => res(req ? req.result : undefined); t.onerror = () => rej(t.error);
    });
  },
  get(store, key) { return this.run(store, 'readonly', (s) => s.get(key)).catch(() => null); },
  put(store, val, key) { return this.run(store, 'readwrite', (s) => (key === undefined ? s.put(val) : s.put(val, key))).catch(() => null); },
  del(store, key) { return this.run(store, 'readwrite', (s) => s.delete(key)).catch(() => null); },
  all(store) { return this.run(store, 'readonly', (s) => s.getAll()).catch(() => []); },
  clear(store) { return this.run(store, 'readwrite', (s) => s.clear()).catch(() => null); },
};
// Field records that can be made with no network. Each carries its own id, so sending it twice saves it once.
const OFFLINE_OK = [/\/labour\/work\/$/, /\/stock\/(issue|receive|transfer|count)\/$/, /\/trade\/harvests\/$/];
function outboxLabel(url, b) {
  const on = b.date ? ` on ${fmtDate(b.date)}` : '';
  if (/labour\/work/.test(url)) { const n = (b.entries || [b]).length; return `Work: ${n} ${n === 1 ? 'person' : 'people'}${on}`; }
  if (/stock\/issue/.test(url)) return `${b.equipment ? 'Fuel filled' : 'Stock used'}: ${b.quantity || ''}${on}`;
  if (/stock\/receive/.test(url)) return `Stock received: ${b.quantity || ''}${on}`;
  if (/stock\/transfer/.test(url)) return `Stock moved: ${b.quantity || ''}${on}`;
  if (/stock\/count/.test(url)) return `Stock take: counted ${b.counted || ''}${on}`;
  if (/harvests/.test(url)) return `Harvest: ${b.tonnes || ''} t${on}`;
  return 'Record';
}
const Sync = {
  state: 'online', since: null, pending: 0, failed: 0, busy: false, needLogin: false,
  offline(at) { const was = this.state; this.state = 'offline'; if (at) this.since = at; if (was !== 'offline') this.render(); },
  online() { const was = this.state; this.state = 'online'; this.since = null; if (was !== 'online') { this.render(); this.flush(); } },
  async refresh() {
    const all = await DB.all('outbox');
    this.pending = all.filter((x) => !x.error).length; this.failed = all.filter((x) => x.error).length;
    this.render();
  },
  render() {
    const chip = $('#sync-chip');
    if (chip) {
      const off = this.state === 'offline';
      chip.hidden = !off && !this.pending && !this.failed;
      chip.className = `sync-chip${this.failed ? ' bad' : off ? ' off' : ''}`;
      chip.innerHTML = `${ico('cloud')}${off ? 'Offline' : 'Online'}${this.pending ? ` · ${this.pending} waiting` : ''}${this.failed ? ` · ${this.failed} need you` : ''}`;
    }
    const ban = $('#offline-banner');
    if (ban) {
      ban.hidden = this.state !== 'offline';
      ban.innerHTML = `${ico('cloud')}<span>No network. You're seeing what this phone saved${this.since ? ` at ${new Date(this.since).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : ''}. Work, stock, fuel and harvests you record now are kept on the phone and sent when the network is back.</span>`;
    }
  },
  async add(item) {
    await DB.put('outbox', { ...item, id: uuid(), at: Date.now() });
    toast.hold = Date.now();
    toast('Saved on this phone. It will be sent when you have network.', true);
    await this.refresh();
  },
  async flush() {
    if (this.busy || !navigator.onLine && this.state === 'offline') return;
    this.busy = true; let sent = 0;
    try {
      const items = (await DB.all('outbox')).filter((x) => !x.error).sort((a, b) => a.at - b.at);
      for (const it of items) {
        let r;
        try {
          r = await fetch(it.url, { method: it.method, credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cookie('csrftoken') }, body: JSON.stringify(it.body) });
        } catch (e) { this.offline(); break; }
        this.state = 'online';
        if (r.ok) { await DB.del('outbox', it.id); sent += 1; continue; }
        if (r.status >= 500) break;  // the server is struggling: try again later
        let data = null; try { data = await r.json(); } catch (e) { /* not JSON */ }
        if (r.status === 401 || !data) { this.needLogin = true; break; }  // logged out, or the security cookie expired
        await DB.put('outbox', { ...it, error: data.error || `Refused (${r.status})`, failed_at: Date.now() });
      }
    } finally {
      this.busy = false; await this.refresh();
      if (sent) toast(`${sent} saved record${sent > 1 ? 's' : ''} sent to Murimi`, true);
    }
  },
};
setInterval(() => { if (Sync.pending) Sync.flush(); }, 20000);
window.addEventListener('online', () => Sync.flush());
window.addEventListener('offline', () => Sync.offline());

/* ---------------- talking to the server ---------------- */
class ApiError extends Error { constructor(msg, status, fields) { super(msg); this.status = status; this.fields = fields || {}; } }
const API = {
  async req(method, url, body) {
    const queueable = method === 'POST' && body && OFFLINE_OK.some((re) => re.test(url));
    if (queueable) {  // give every field record an id before the first try
      body = Array.isArray(body.entries) ? { ...body, entries: body.entries.map((e) => (e.id ? e : { ...e, id: uuid() })) }
        : (body.id ? body : { ...body, id: uuid() });
    }
    let r;
    try {
      r = await fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': cookie('csrftoken') },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      if (method === 'GET') {
        const hit = await DB.get('pages', url);
        if (hit) { Sync.offline(hit.at); return hit.data; }
      }
      if (queueable) {
        await Sync.add({ method, url, body, label: outboxLabel(url, body), org: S.org ? S.org.name : '' });
        Sync.offline();
        return { offline: true, queued: true, results: [] };
      }
      Sync.offline();
      throw new ApiError(method === 'GET' ? "No network, and this page hasn't been opened on this phone before."
        : "No network. This needs a connection: try again when you have signal.", 0);
    }
    if (Sync.state !== 'online') Sync.online();
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty reply */ }
    if (!r.ok) throw new ApiError(data.error || 'Something went wrong. Please try again.', r.status, data.errors);
    if (method === 'GET' && !url.includes('/csv/')) DB.put('pages', { at: Date.now(), data }, url);
    return data;
  },
  get(url, params) { const q = params ? '?' + new URLSearchParams(params) : ''; return this.req('GET', url + q); },
  post(url, body) { return this.req('POST', url, body); },
  patch(url, body) { return this.req('PATCH', url, body); },
  del(url) { return this.req('DELETE', url); },
};
const S = { user: null, orgs: [], org: null, lists: null };
const O = (path = '') => `/api/erp/orgs/${S.org.id}/${path}`;
const can = (action) => !!(S.org && S.org.can && S.org.can.includes(action));

/* ---------------- theme ---------------- */
function setTheme(t) { document.documentElement.dataset.theme = t; store.set('murimi-erp-theme', t); }
setTheme(store.get('murimi-erp-theme') === 'dark' ? 'dark' : 'light');

/* ---------------- one reusable pop-up form ----------------
 * fields: [{ key, label, type: text|number|select|date|textarea|check|tel|password, options: [[value,label]], required, hint, wide, step, placeholder }]
 */
function fieldHTML(f, v) {
  const id = 'fm-' + f.key, req = f.required ? ' required' : '';
  const hint = f.hint ? `<span class="hint">${esc(f.hint)}</span>` : '';
  let input;
  if (f.type === 'select') {
    input = `<select id="${id}" name="${f.key}"${req}>${f.required ? '' : '<option value="">—</option>'}${(f.options || []).map(([val, lab]) =>
      `<option value="${esc(val)}"${String(v ?? '') === String(val) ? ' selected' : ''}>${esc(lab)}</option>`).join('')}</select>`;
  } else if (f.type === 'textarea') {
    input = `<textarea id="${id}" name="${f.key}" maxlength="2000">${esc(v ?? '')}</textarea>`;
  } else if (f.type === 'check') {
    return `<div class="f check${f.wide ? ' wide' : ''}" data-f="${f.key}"><input type="checkbox" id="${id}" name="${f.key}"${v ? ' checked' : ''}><label for="${id}">${esc(f.label)}</label><span class="err"></span></div>`;
  } else {
    const type = f.type === 'number' ? 'text' : (f.type || 'text');
    const mode = f.type === 'number' ? ' inputmode="decimal"' : '';
    input = `<input id="${id}" name="${f.key}" type="${type}"${mode} value="${esc(v ?? '')}"${req}${f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : ''}${f.autocomplete ? ` autocomplete="${f.autocomplete}"` : ''}>`;
  }
  return `<div class="f${f.wide ? ' wide' : ''}" data-f="${f.key}"><label for="${id}">${esc(f.label)}${f.required ? '' : ' <span class="muted small">(optional)</span>'}</label>${input}${hint}<span class="err"></span></div>`;
}
function readForm(form, fields) {
  const out = {};
  for (const f of fields) {
    const el = form.elements.namedItem(f.key);
    if (!el) continue;
    out[f.key] = f.type === 'check' ? el.checked : el.value.trim();
  }
  return out;
}
function showErrors(form, ex) {
  form.querySelectorAll('.invalid').forEach((b) => { b.classList.remove('invalid'); $('.err', b).textContent = ''; });
  let first = null;
  for (const [k, msg] of Object.entries(ex.fields || {})) {
    const box = form.querySelector(`[data-f="${k}"]`);
    if (!box) continue;
    box.classList.add('invalid'); $('.err', box).textContent = msg;
    if (!first) first = box;
  }
  const top = $('.form-error', form);
  top.textContent = ex.message; top.hidden = false;
  if (first) { const inp = first.querySelector('input,select,textarea'); if (inp) inp.focus(); }
}
function openForm({ title, intro, fields, values = {}, submitLabel = 'Save', onSubmit }) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="backdrop"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="m-title">
    <div class="modal-head"><h2 id="m-title">${esc(title)}</h2><button class="btn icon" type="button" data-close aria-label="Close">${ico('x')}</button></div>
    ${intro ? `<p class="muted">${intro}</p>` : ''}
    <form class="grid" novalidate><p class="form-error wide f" hidden style="grid-column:1/-1"></p>
      ${fields.map((f) => fieldHTML(f, values[f.key])).join('')}
      <div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="submit">${esc(submitLabel)}</button></div>
    </form></div></div>`;
  const form = $('form', root);
  const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  root.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
  $('.backdrop', root).addEventListener('mousedown', (e) => { if (e.target.classList.contains('backdrop')) close(); });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try { await onSubmit(readForm(form, fields)); close(); }
    catch (ex) { showErrors(form, ex); }
    finally { btn.disabled = false; }
  });
  const firstInput = form.querySelector('input,select,textarea'); if (firstInput) firstInput.focus();
}
function confirmBox(message, yesLabel, onYes) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="backdrop"><div class="modal" role="alertdialog" aria-modal="true" style="max-width:440px">
    <p>${message}</p><p class="form-error" hidden></p>
    <div class="actions"><button class="btn" data-no>Cancel</button><button class="btn primary" data-yes>${esc(yesLabel)}</button></div></div></div>`;
  $('[data-no]', root).onclick = () => { root.innerHTML = ''; };
  $('[data-yes]', root).onclick = async () => {
    try { await onYes(); root.innerHTML = ''; }
    catch (ex) { const er = $('.form-error', root); er.textContent = ex.message; er.hidden = false; }
  };
  $('[data-yes]', root).focus();
}

/* ---------------- start-up ---------------- */
let installPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installPrompt = e; });
function bindRoute() { if (!bindRoute.done) { window.addEventListener('hashchange', route); bindRoute.done = true; } }
async function boot() {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    // The phone keeps a copy of the app so it opens with no network. When Murimi has been updated,
    // the new copy takes over: load it straight away, so nobody sees yesterday's version.
    const hadOne = !!navigator.serviceWorker.controller, opened = Date.now();
    navigator.serviceWorker.register('/sw.js').then((reg) => reg.update()).catch(() => {});
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadOne || boot.reloaded) return;
      boot.reloaded = true;
      if (Date.now() - opened < 15000) location.reload();
      else toast('Murimi has been updated. Close and open it again to use the new version.', true);
    });
  }
  Sync.refresh().then(() => Sync.flush());
  try { await API.get('/api/health/'); } catch (e) { /* the cookie is still set by the page */ }
  try { S.user = (await API.get('/api/auth/me/')).user; } catch (e) { S.user = null; }
  bindRoute();
  if (/^#\/join\//.test(location.hash)) { if (S.user) await loadOrgs(); return route(); }
  if (!S.user) return renderAuth();
  await loadOrgs();
  route();
}
async function loadOrgs() {
  S.lists = S.lists || await API.get('/api/erp/lists/');
  S.orgs = (await API.get('/api/erp/orgs/')).results;
  const saved = store.get('murimi-erp-org');
  S.org = S.orgs.find((o) => o.id === saved) || S.orgs[0] || null;
}

/* ---------------- log in / sign up ---------------- */
function renderAuth(mode = 'login') {
  const loginFields = [
    { key: 'login', label: 'WhatsApp number or username', required: true, wide: true, placeholder: 'e.g. 077 123 4567 or tendai_moyo', autocomplete: 'username' },
    { key: 'password', label: 'Password', type: 'password', required: true, wide: true, autocomplete: 'current-password' },
  ];
  const signupFields = [
    { key: 'full_name', label: 'Full name', required: true, wide: true, autocomplete: 'name' },
    { key: 'phone', label: 'WhatsApp number', type: 'tel', required: true, placeholder: '077 123 4567', autocomplete: 'tel' },
    { key: 'username', label: 'Username', hint: 'A second way to log in', autocomplete: 'username' },
    { key: 'email', label: 'Email', type: 'email', wide: true, autocomplete: 'email' },
    { key: 'password', label: 'Password', type: 'password', required: true, wide: true, hint: '8 or more characters, not only numbers', autocomplete: 'new-password' },
  ];
  const fields = mode === 'login' ? loginFields : signupFields;
  $('#app').innerHTML = `<div class="auth">
    <section class="auth-art"><div class="brand"><span class="logo">${ico('leaf')}</span>Murimi Farm ERP</div>
      <div><h1>Run your farm in USD and ZiG, even offline.</h1>
      <ul>
        <li>${ico('map')} Farms, fields and seasons in one place</li>
        <li>${ico('swap')} USD, ZiG and rand side by side, with the rate you used</li>
        <li>${ico('users')} Your team, each with the right access</li>
        <li>${ico('history')} Every change recorded: who, what and when</li>
      </ul></div>
      <p class="small" style="opacity:.8">Part of Murimi Intelligent Hub</p></section>
    <section class="auth-card"><div class="panel">
      <div class="tabs" role="tablist"><button role="tab" aria-selected="${mode === 'login'}" data-mode="login">Log in</button><button role="tab" aria-selected="${mode === 'signup'}" data-mode="signup">Create account</button></div>
      ${S.invite ? `<p class="note invite-note">${ico('users')}<span>You're invited to join <b>${esc(S.invite.organisation)}</b> as <b>${esc(S.invite.role_label)}</b>. ${mode === 'login' ? 'Log in' : 'Create your account'} to accept.</span></p>` : ''}
      <h2>${mode === 'login' ? 'Welcome back' : 'Create your account'}</h2>
      <form class="grid" novalidate><p class="form-error f wide" hidden style="grid-column:1/-1"></p>${fields.map((f) => fieldHTML(f, '')).join('')}
        <div class="actions"><button class="btn primary" type="submit" style="width:100%;justify-content:center">${mode === 'login' ? 'Log in' : 'Create account'}</button></div></form>
      ${mode === 'login' ? '<p class="center small"><a href="#" id="forgot">Forgot password?</a></p>' : ''}
    </div></section></div>`;
  document.querySelectorAll('[data-mode]').forEach((b) => b.onclick = () => renderAuth(b.dataset.mode));
  if ($('#forgot')) $('#forgot').onclick = (e) => { e.preventDefault(); renderForgot(($('[name=login]') || {}).value || ''); };
  const form = $('.auth form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try {
      const data = readForm(form, fields);
      S.user = (await API.post(mode === 'login' ? '/api/auth/login/' : '/api/auth/register/', data)).user;
      if (S.invite) return acceptInvite(S.invite.token);
      await loadOrgs();
      bindRoute();
      location.hash = '#/home'; route();
    } catch (ex) { showErrors(form, ex); } finally { btn.disabled = false; }
  });
  $('input', form).focus();
}

/* ---------------- forgot password, and choosing a new one ---------------- */
function authCard(inner) {
  $('#app').innerHTML = `<div class="auth"><section class="auth-art"><div class="brand"><span class="logo">${ico('leaf')}</span>Murimi Farm ERP</div>
    <div><h1>Run your farm in USD and ZiG, even offline.</h1></div><p class="small" style="opacity:.8">Part of Murimi Intelligent Hub</p></section>
    <section class="auth-card"><div class="panel">${inner}</div></section></div>`;
}
function renderForgot(login = '') {
  const fields = [{ key: 'login', label: 'WhatsApp number or username', required: true, wide: true, autocomplete: 'username' }];
  authCard(`<h2>Forgot your password?</h2><p class="muted">We'll send a 6-digit code to the WhatsApp number on your account.</p>
    <form class="grid" novalidate><p class="form-error f wide" hidden style="grid-column:1/-1"></p>${fields.map((f) => fieldHTML(f, login)).join('')}
      <div class="actions"><button class="btn" type="button" id="back">Back</button><button class="btn primary" type="submit">Send code</button></div></form>`);
  $('#back').onclick = () => renderAuth('login');
  const form = $('.auth form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try {
      const d = readForm(form, fields);
      const r = await API.post('/api/auth/password/forgot/', d);
      if (r.whatsapp_off) {
        authCard(`<h2>Ask your farm owner</h2><p>${esc(r.message)}</p>
          <p class="note">The owner opens <b>Team</b>, finds your name and presses <b>Reset password</b>. They give you a temporary password, and you choose your own the first time you log in.</p>
          <div class="actions"><button class="btn primary" id="back">Back to log in</button></div>`);
        $('#back').onclick = () => renderAuth('login');
        return;
      }
      renderResetCode(d.login, r);
    } catch (ex) { showErrors(form, ex); } finally { btn.disabled = false; }
  });
  $('input', form).focus();
}
function renderResetCode(login, info) {
  const fields = [
    { key: 'code', label: '6-digit code', required: true, wide: true, autocomplete: 'one-time-code', placeholder: '123456' },
    { key: 'password', label: 'New password', type: 'password', required: true, wide: true, hint: '8 or more characters, not only numbers', autocomplete: 'new-password' },
  ];
  authCard(`<h2>Enter the code</h2><p class="muted">${esc(info.message)}</p>
    ${info.test_code ? `<p class="note warn">Test mode (this computer only): the code is <b class="mono">${esc(info.test_code)}</b></p>` : ''}
    <form class="grid" novalidate><p class="form-error f wide" hidden style="grid-column:1/-1"></p>${fields.map((f) => fieldHTML(f, '')).join('')}
      <div class="actions"><button class="btn" type="button" id="resend" disabled>Send again</button><button class="btn primary" type="submit">Save password</button></div></form>`);
  let left = info.resend_in || 60;
  const rb = $('#resend');
  const tick = setInterval(() => { left -= 1; rb.textContent = left > 0 ? `Send again (${left})` : 'Send again'; rb.disabled = left > 0; if (left <= 0) clearInterval(tick); }, 1000);
  rb.onclick = async () => { try { renderResetCode(login, await API.post('/api/auth/password/forgot/', { login })); } catch (ex) { toast(ex.message); } };
  const form = $('.auth form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try {
      S.user = (await API.post('/api/auth/password/reset/', { login, ...readForm(form, fields) })).user;
      clearInterval(tick);
      await loadOrgs(); bindRoute();
      location.hash = '#/home'; route(); toast('Password changed');
    } catch (ex) { showErrors(form, ex); } finally { btn.disabled = false; }
  });
  $('input', form).focus();
}
function renderMustChange() {
  const fields = [
    { key: 'password', label: 'Your new password', type: 'password', required: true, wide: true, hint: '8 or more characters, not only numbers', autocomplete: 'new-password' },
    { key: 'again', label: 'Type it again', type: 'password', required: true, wide: true, autocomplete: 'new-password' },
  ];
  authCard(`<h2>Choose your own password</h2><p class="muted">Hello ${esc(S.user.full_name.split(' ')[0])}. You logged in with a temporary password from your farm owner. Choose one only you know.</p>
    <form class="grid" novalidate><p class="form-error f wide" hidden style="grid-column:1/-1"></p>${fields.map((f) => fieldHTML(f, '')).join('')}
      <div class="actions"><button class="btn" type="button" id="out">Log out</button><button class="btn primary" type="submit">Save password</button></div></form>`);
  $('#out').onclick = logout;
  const form = $('.auth form');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const d = readForm(form, fields);
    if (d.password !== d.again) return showErrors(form, new ApiError('The two passwords are different.', 400, { again: "Doesn't match." }));
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try {
      S.user = (await API.patch('/api/auth/me/', { full_name: S.user.full_name, username: S.user.username, email: S.user.email, password: d.password })).user;
      await loadOrgs(); location.hash = '#/home'; route(); toast('Password saved');
    } catch (ex) { showErrors(form, ex); } finally { btn.disabled = false; }
  });
  $('input', form).focus();
}

/* ---------------- invite links ---------------- */
async function renderJoin(token) {
  let info;
  try { info = await API.get(`/api/erp/invites/${token}/`); }
  catch (ex) {
    authCard(`<h2>Invite link</h2><p class="form-error">${esc(ex.message)}</p><p class="muted">Ask the owner of the farm business to send you a new link.</p>
      <div class="actions"><button class="btn primary" id="go">${S.user ? 'Open Murimi' : 'Go to log in'}</button></div>`);
    $('#go').onclick = () => { S.invite = null; location.hash = '#/home'; };
    return;
  }
  S.invite = { ...info, token };
  if (!S.user) return renderAuth('signup');
  authCard(`<h2>Join ${esc(info.organisation)}</h2>
    <p>${info.invited_by ? `<b>${esc(info.invited_by)}</b> invited you` : 'You are invited'} to join <b>${esc(info.organisation)}</b> on Murimi Farm ERP as <b>${esc(info.role_label)}</b>.</p>
    <p class="muted small">You're logged in as ${esc(S.user.full_name)} (${esc(S.user.phone)}). Not you? Log out and open the link again.</p>
    <p class="form-error" hidden></p>
    <div class="actions"><button class="btn" id="no">Not now</button><button class="btn primary" id="yes">Join ${esc(info.organisation)}</button></div>`);
  $('#no').onclick = () => { S.invite = null; location.hash = '#/home'; };
  $('#yes').onclick = () => acceptInvite(token);
}
async function acceptInvite(token) {
  try {
    const r = await API.post(`/api/erp/invites/${token}/accept/`, {});
    S.invite = null;
    await loadOrgs();
    S.org = S.orgs.find((o) => o.id === r.organisation) || S.org;
    if (S.org) store.set('murimi-erp-org', S.org.id);
    location.hash = '#/home';
    bindRoute();
    await route();
    toast(r.already ? `You're already in ${S.org ? S.org.name : 'this business'}` : `Welcome! You joined as ${r.role_label}`);
  } catch (ex) {
    S.invite = null;
    authCard(`<h2>Couldn't join</h2><p class="form-error">${esc(ex.message)}</p><div class="actions"><button class="btn primary" id="go">Open Murimi</button></div>`);
    $('#go').onclick = async () => { await loadOrgs(); location.hash = '#/home'; route(); };
  }
}
function shareInvite(inv, orgName) {
  const text = `You're invited to join ${orgName} on Murimi Farm ERP as ${inv.role_label}. Open this link to create your account or log in: ${inv.link}`;
  infoBox(`<h2>Invite link ready</h2><p>Send this to <b>${esc(inv.name || 'the person')}</b>. It works once, for 7 days, and makes them <b>${esc(inv.role_label)}</b>.</p>
    <p class="invite-link mono">${esc(inv.link)}</p>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap">
      <a class="btn primary" target="_blank" rel="noopener" href="https://wa.me/?text=${encodeURIComponent(text)}">Send on WhatsApp</a>
      <button class="btn" id="copy-inv">Copy link</button></div>`);
  $('#copy-inv').onclick = async () => {
    try { await navigator.clipboard.writeText(inv.link); toast('Link copied'); }
    catch (e) { const r = document.createRange(); r.selectNodeContents($('.invite-link')); getSelection().removeAllRanges(); getSelection().addRange(r); toast('Select and copy the link'); }
  };
}

/* ---------------- first organisation ---------------- */
const orgFields = () => [
  { key: 'name', label: 'Business name', required: true, wide: true, placeholder: 'e.g. Moyo Family Farms (Pvt) Ltd' },
  { key: 'trading_name', label: 'Trading name' },
  { key: 'reporting_currency', label: 'Main reporting currency', type: 'select', required: true, options: S.lists.currencies.map((c) => [c, c === 'ZWG' ? 'ZiG (ZWG)' : c]) },
  { key: 'province', label: 'Province', type: 'select', options: S.lists.provinces.map((p) => [p, p]) },
  { key: 'district', label: 'District' },
  { key: 'phone', label: 'Business phone', type: 'tel' },
  { key: 'email', label: 'Business email', type: 'email' },
  { key: 'tin', label: 'ZIMRA TIN / BP number' },
  { key: 'vat_number', label: 'VAT number', hint: 'Leave empty if not VAT-registered' },
];
function newOrgForm(first) {
  openForm({
    title: first ? 'Set up your farm business' : 'Add another farm business',
    intro: 'This is the business that owns the farms. You will be its owner, and you can add your team after.',
    fields: orgFields(), values: { reporting_currency: 'USD' }, submitLabel: 'Create',
    onSubmit: async (data) => {
      const org = await API.post('/api/erp/orgs/', data);
      S.orgs.push(org); S.org = org; store.set('murimi-erp-org', org.id);
      location.hash = '#/home'; route(); toast('Business created');
    },
  });
}
function renderNoOrg() {
  $('#app').innerHTML = `<div class="auth-card" style="min-height:100vh"><div class="panel" style="max-width:520px">
    <div class="brand"><span class="logo">${ico('leaf')}</span>Murimi Farm ERP</div>
    <h1>Welcome, ${esc(S.user.full_name.split(' ')[0])}</h1>
    <p class="muted">Start by adding your farm business. If your farm already uses Murimi Farm ERP, ask the owner for an <b>invite link</b>, or to add you with your WhatsApp number (${esc(S.user.phone)}).</p>
    <div class="actions" style="justify-content:flex-start"><button class="btn primary" id="first-org">${ico('plus')}Set up my farm business</button><button class="btn" id="lo">${ico('logout')}Log out</button></div>
  </div></div>`;
  $('#first-org').onclick = () => newOrgForm(true);
  $('#lo').onclick = logout;
}
async function logout() {
  const waiting = (await DB.all('outbox')).length;
  const go = async () => {
    try { await API.post('/api/auth/logout/'); } catch (e) { /* offline: still clear this phone */ }
    await DB.clear('pages'); await DB.clear('outbox');
    location.hash = ''; location.reload();
  };
  if (waiting) return confirmBox(`<b>${waiting} record${waiting > 1 ? 's have' : ' has'} not been sent yet.</b> If you log out now ${waiting > 1 ? 'they' : 'it'} will be lost. Get network and open <a href="#/sync">Phone & sync</a> first.`, 'Log out anyway', go);
  return go();
}

/* ---------------- the shell (menu + page) ---------------- */
const NAV = [
  ['home', 'Home', 'home'], ['farms', 'Farms & fields', 'map'], ['seasons', 'Seasons & crops', 'calendar'],
  ['stock', 'Stock', 'box'], ['fuel', 'Fuel', 'fuel'], ['labour', 'Labour & payroll', 'wallet'],
  ['sales', 'Sales', 'up'], ['purchases', 'Purchases', 'down'], ['reports', 'Reports', 'chart'],
  ['rates', 'Exchange rates', 'swap'], ['team', 'Team', 'users'], ['history', 'Change history', 'history'],
  ['sync', 'Phone & sync', 'phone'],
];
const SOON = [];
const NAV_NEEDS = { sales: ['view_trade'], purchases: ['view_trade', 'edit_purchases'], reports: ['view_reports'] };
const navOk = ([k]) => !NAV_NEEDS[k] || NAV_NEEDS[k].some(can);
const PHONE_NAV = [['home', 'Home', 'home'], ['labour', 'Labour', 'wallet'], ['stock', 'Stock', 'box'], ['seasons', 'Seasons', 'calendar'], ['more', 'More', 'menu']];
function renderShell(page) {
  const opts = S.orgs.map((o) => `<option value="${o.id}"${o.id === S.org.id ? ' selected' : ''}>${esc(o.name)}</option>`).join('');
  $('#app').innerHTML = `<div class="shell">
    <aside class="side">
      <div class="brand"><span class="logo">${ico('leaf')}</span><span>Murimi ERP</span><a id="sync-chip" class="sync-chip" href="#/sync" hidden></a></div>
      <div class="org-switch"><label class="small muted" for="org-pick">Business</label>
        <select id="org-pick">${opts}<option value="__new">+ Add another business</option></select></div>
      <nav class="nav" aria-label="Main">${NAV.filter(navOk).map(([k, l, i]) => `<a href="#/${k}"${k === page ? ' aria-current="page"' : ''}>${ico(i)}${l}</a>`).join('')}
        ${SOON.map(([l, i]) => `<a class="soon" aria-disabled="true">${ico(i)}${l}<small>SOON</small></a>`).join('')}</nav>
      <div class="side-foot">
        <div class="me"><span class="av">${esc(S.user.full_name.charAt(0))}</span><span class="who"><b>${esc(S.user.full_name)}</b><span class="small muted">${esc(S.org.my_role_label)}</span></span></div>
        <div style="display:flex;gap:8px"><button class="btn small" id="theme" aria-label="Switch light or dark">${ico(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon')}</button>
        <button class="btn small" id="logout">${ico('logout')}Log out</button></div>
      </div>
    </aside>
    <main class="main"><div id="offline-banner" class="offline-banner" hidden></div><div id="page" class="page"><p class="muted">Loading…</p></div></main>
    <nav class="bottom-nav" aria-label="Main">${PHONE_NAV.map(([k, l, i]) => `<a href="#/${k}"${k === page || (k === 'more' && !PHONE_NAV.some((x) => x[0] === page)) ? ' aria-current="page"' : ''}>${ico(i)}${l}</a>`).join('')}</nav>
  </div>`;
  $('#org-pick').onchange = (e) => {
    if (e.target.value === '__new') { e.target.value = S.org.id; return newOrgForm(false); }
    S.org = S.orgs.find((o) => o.id === e.target.value); store.set('murimi-erp-org', S.org.id); route();
  };
  $('#logout').onclick = logout;
  $('#theme').onclick = () => { setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); route(); };
}
async function route() {
  const jm = location.hash.match(/^#\/join\/([\w-]+)/);
  if (jm) return renderJoin(jm[1]);
  if (!S.user) return renderAuth();
  if (S.user.must_change_password) return renderMustChange();
  if (!S.org) return renderNoOrg();
  const page = (location.hash.replace(/^#\/?/, '').split('/')[0]) || 'home';
  const fn = PAGES[page] || PAGES.home;
  renderShell(PAGES[page] ? page : 'home');
  const el = $('#page');
  Sync.render();
  try { await fn(el); }
  catch (ex) {
    if (ex.status === 401) { S.user = null; return renderAuth(); }
    if (ex.status === 404) { await loadOrgs(); }
    el.innerHTML = `<div class="panel"><p class="form-error">${esc(ex.message)}</p></div>`;
  }
  el.focus?.();
}
function head(title, lead, actions = '') {
  return `<div class="page-head"><div><span class="tag">${esc(S.org.name)}</span><h1>${esc(title)}</h1>${lead ? `<p class="lead">${lead}</p>` : ''}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${actions}</div></div>`;
}

/* ---------------- pages ---------------- */
const PAGES = {};

PAGES.home = async (el) => {
  const [sum, rates, alerts, stockNow, lab] = await Promise.all([API.get(O('summary/')), API.get(O('rates/latest/')),
    API.get(O('stock/alerts/')), API.get(O('stock/')), API.get(O('labour/summary/'))]);
  const below = lab.below_minimum || [];
  const nAlerts = alerts.low_stock.length + alerts.expiring.length + (alerts.fuel_flags ? 1 : 0) + below.length + (lab.draft_runs ? 1 : 0);
  const maxHa = Math.max(1, ...sum.by_crop.map((c) => Number(c.ha)));
  const rateRows = rates.results.map((r) => r.missing
    ? `<div class="rate"><span><b>${r.currency === 'ZWG' ? 'ZiG' : 'Rand'}</b> <span class="muted small">no rate yet</span></span><a class="btn small" href="#/rates">Add rate</a></div>`
    : `<div class="rate"><span class="mono">1 USD = <b>${num(r.units_per_usd, 4)}</b> ${r.currency}</span><span class="small muted">${esc(r.source_label)}, ${fmtDate(r.date)}${r.age_days > 7 ? ` <span class="chip warn">${r.age_days} days old</span>` : ''}</span></div>`).join('');
  el.innerHTML = `${head(`Hello, ${S.user.full_name.split(' ')[0]}`, 'Your farms at a glance.')}
    <div class="stats">
      <div class="stat"><span class="n">${lab.workers}</span><span class="l">Workers${lab.cost_this_month_usd !== undefined ? ` · ${money(lab.cost_this_month_usd, 'USD')} of work recorded this month` : ''}</span></div>
      <div class="stat"><span class="n">${num(sum.field_ha, 1)}</span><span class="l">Hectares in ${sum.fields} fields</span></div>
      <div class="stat"><span class="n">${num(sum.planted_ha, 1)}</span><span class="l">Hectares planned ${sum.season ? 'in ' + esc(sum.season.name) : ''}</span></div>
      <div class="stat"><span class="n">${money(stockNow.total_usd, 'USD')}</span><span class="l">Stock value${stockNow.total_zwg_today ? ` · ZiG ${num(stockNow.total_zwg_today, 0)} today` : ''}</span></div>
    </div>
    ${nAlerts ? `<section class="panel"><div class="panel-head"><h2>Needs your attention</h2><a class="btn small" href="#/stock">Open stock</a></div><div class="alerts">
      ${alerts.low_stock.map((a) => `<div class="alert">${ico('box')}<span><b>${esc(a.name)}</b> is running low: ${num(a.qty, 0)} ${esc(a.unit)} left (warning at ${num(a.reorder_level, 0)})</span></div>`).join('')}
      ${alerts.expiring.map((a) => `<div class="alert${a.expired ? ' bad' : ''}">${ico('warn')}<span><b>${esc(a.name)}</b> in ${esc(a.store)}: ${num(a.qty, 0)} ${esc(a.unit)} ${a.expired ? 'expired on' : 'expires'} ${fmtDate(a.expiry)}${a.expired ? '' : ` (${a.days} days)`}</span></div>`).join('')}
      ${below.map((b) => `<div class="alert bad">${ico('wallet')}<span><b>${esc(b.worker)}</b> is paid about US$${num(b.pay_usd)} a month, below the NEC minimum for grade ${esc(b.grade)} (US$${num(b.minimum_usd)}). <a href="#/labour/workers">Check workers</a></span></div>`).join('')}
      ${lab.draft_runs ? `<div class="alert">${ico('wallet')}<span>${lab.draft_runs} pay run${lab.draft_runs > 1 ? 's are' : ' is'} waiting to be approved. <a href="#/labour/pay">Open pay runs</a></span></div>` : ''}
      ${alerts.fuel_flags ? `<div class="alert bad">${ico('fuel')}<span><b>${alerts.fuel_flags} fuel warning${alerts.fuel_flags > 1 ? 's' : ''}</b>: unusual use or meter readings. <a href="#/fuel">Check the Fuel page</a></span></div>` : ''}
    </div></section>` : ''}
    <div class="grid2">
      <section class="panel"><div class="panel-head"><h2>Crops ${sum.season ? 'in ' + esc(sum.season.name) : ''}</h2><a class="btn small" href="#/seasons">Open seasons</a></div>
        ${sum.by_crop.length ? `<div class="bars">${sum.by_crop.map((c) => `<div class="row"><b>${esc(c.crop)}</b><div class="bar"><span style="width:${Number(c.ha) / maxHa * 100}%"></span></div><span class="num mono">${num(c.ha, 1)} ha</span></div>`).join('')}</div>`
          : `<div class="empty">${ico('calendar')}<p>No crops planned yet.</p>${can('edit_seasons') ? '<a class="btn primary small" href="#/seasons">Plan a season</a>' : ''}</div>`}
      </section>
      <section class="panel"><div class="panel-head"><h2>Today's exchange rates</h2><a class="btn small" href="#/rates">Rates & converter</a></div>
        <div class="rates">${rateRows}</div>
        <p class="note">Every money record will store the rate it used, so your old figures never change when new rates are added.</p>
      </section>
    </div>
    ${sum.farms === 0 && can('edit_farms') ? `<section class="panel"><div class="empty">${ico('map')}<h2>Add your first farm</h2><p>Then add its fields (blocks) with their sizes in hectares.</p><a class="btn primary" href="#/farms">Add a farm</a></div></section>` : ''}
    <section class="panel"><h2>What's coming to Murimi Farm ERP</h2><div class="roadmap">
      <div class="done">${ico('check')} Stage 1: businesses, team roles, farms, fields, seasons, crop plans, USD/ZiG rates, change history</div>
      <div class="done">${ico('check')} Stage 2: stock in batches with USD and ZiG values, and fuel tracking</div>
      <div class="done">${ico('check')} Stage 3: labour, piece work and payroll (PAYE, AIDS levy, NSSA, minimum wages)</div>
      <div class="done">${ico('check')} Stage 4: sales, purchases, VAT, harvests and reports such as cost per hectare</div>
      <div class="done">${ico('check')} Stage 5: the phone app that works with no network and syncs later (<a href="#/sync">Phone & sync</a>)</div>
    </div></section>`;
};

/* farms & fields */
const farmFields = () => [
  { key: 'name', label: 'Farm name', required: true, wide: true, placeholder: 'e.g. Chakadenga Farm' },
  { key: 'province', label: 'Province', type: 'select', required: true, options: S.lists.provinces.map((p) => [p, p]) },
  { key: 'district', label: 'District', required: true },
  { key: 'size_ha', label: 'Total size (ha)', type: 'number' },
  { key: 'notes', label: 'Notes', type: 'textarea', wide: true },
];
const fieldFields = (farms) => [
  { key: 'farm', label: 'Farm', type: 'select', required: true, options: farms.map((f) => [f.id, f.name]) },
  { key: 'name', label: 'Field or block name', required: true, placeholder: 'e.g. Block A, Pivot 2' },
  { key: 'size_ha', label: 'Size (ha)', type: 'number', required: true },
  { key: 'soil_type', label: 'Soil type', placeholder: 'e.g. sandy loam' },
  { key: 'irrigated', label: 'Irrigated', type: 'check', wide: true },
  { key: 'latitude', label: 'Latitude', type: 'number', hint: 'From a phone map, e.g. -18.1834' },
  { key: 'longitude', label: 'Longitude', type: 'number', hint: 'e.g. 31.5512' },
  { key: 'notes', label: 'Notes', type: 'textarea', wide: true },
];
PAGES.farms = async (el) => {
  const [farms, fields] = await Promise.all([API.get(O('farms/')), API.get(O('fields/'))]);
  const edit = can('edit_farms');
  const byFarm = (id) => fields.results.filter((f) => f.farm === id);
  el.innerHTML = `${head('Farms & fields', 'Each farm is split into fields (blocks). Crop plans, stock use, fuel and labour will all be recorded against a field.',
    edit ? `<button class="btn" id="add-field"${farms.results.length ? '' : ' disabled'}>${ico('plus')}Add field</button><button class="btn primary" id="add-farm">${ico('plus')}Add farm</button>` : '')}
    ${farms.results.length ? farms.results.map((f) => `<section class="panel farm">
      <div class="farm-head"><div><h2>${esc(f.name)}</h2><p class="muted">${esc(f.district)}, ${esc(f.province)}${f.size_ha ? ` · ${ha(f.size_ha)} in total` : ''} · ${byFarm(f.id).length} fields, ${ha(f.field_ha)} mapped</p></div>
        ${edit ? `<div style="display:flex;gap:6px"><button class="btn small" data-edit-farm="${f.id}">${ico('edit')}Edit</button><button class="btn small danger" data-archive-farm="${f.id}">${ico('archive')}Archive</button></div>` : ''}</div>
      ${f.size_ha && Number(f.field_ha) > Number(f.size_ha) ? `<p class="note warn">${ico('warn')}The fields add up to more than the farm's total size. Check the sizes.</p>` : ''}
      ${byFarm(f.id).length ? `<div class="table-wrap"><table><thead><tr><th>Field</th><th class="num">Size</th><th>Water</th><th>Soil</th><th></th></tr></thead><tbody>
        ${byFarm(f.id).map((x) => `<tr><td><b>${esc(x.name)}</b>${x.notes ? `<div class="small muted">${esc(x.notes)}</div>` : ''}</td><td class="num mono">${ha(x.size_ha)}</td>
          <td>${x.irrigated ? `<span class="chip green">${ico('drop')}Irrigated</span>` : '<span class="chip">Dryland</span>'}</td><td>${esc(x.soil_type) || '—'}</td>
          <td>${edit ? `<div class="row-actions"><button class="btn small" data-edit-field="${x.id}" aria-label="Edit ${esc(x.name)}">${ico('edit')}</button><button class="btn small danger" data-archive-field="${x.id}" aria-label="Archive ${esc(x.name)}">${ico('archive')}</button></div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>` : `<p class="muted">No fields yet.${edit ? ` <button class="btn small" data-add-field-to="${f.id}">${ico('plus')}Add a field</button>` : ''}</p>`}
    </section>`).join('')
    : `<section class="panel"><div class="empty">${ico('map')}<h2>No farms yet</h2><p>Add the farms your business runs.</p>${edit ? '<button class="btn primary" id="add-farm-2">Add a farm</button>' : ''}</div></section>`}`;
  const addFarm = () => openForm({ title: 'Add a farm', fields: farmFields(), submitLabel: 'Add farm',
    onSubmit: async (d) => { await API.post(O('farms/'), d); toast('Farm added'); route(); } });
  const addField = (farmId) => openForm({ title: 'Add a field', fields: fieldFields(farms.results), values: { farm: farmId || farms.results[0]?.id }, submitLabel: 'Add field',
    onSubmit: async (d) => { await API.post(O('fields/'), d); toast('Field added'); route(); } });
  if (!edit) return;
  [$('#add-farm'), $('#add-farm-2')].forEach((b) => b && (b.onclick = addFarm));
  if ($('#add-field')) $('#add-field').onclick = () => addField();
  el.querySelectorAll('[data-add-field-to]').forEach((b) => b.onclick = () => addField(b.dataset.addFieldTo));
  el.querySelectorAll('[data-edit-farm]').forEach((b) => b.onclick = () => {
    const f = farms.results.find((x) => x.id === b.dataset.editFarm);
    openForm({ title: `Edit ${f.name}`, fields: farmFields(), values: f, onSubmit: async (d) => { await API.patch(O(`farms/${f.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-archive-farm]').forEach((b) => b.onclick = () => {
    const f = farms.results.find((x) => x.id === b.dataset.archiveFarm);
    confirmBox(`Archive <b>${esc(f.name)}</b>? It will be hidden, but its records are kept.`, 'Archive', async () => { await API.del(O(`farms/${f.id}/`)); toast('Farm archived'); route(); });
  });
  el.querySelectorAll('[data-edit-field]').forEach((b) => b.onclick = () => {
    const f = fields.results.find((x) => x.id === b.dataset.editField);
    openForm({ title: `Edit ${f.name}`, fields: fieldFields(farms.results), values: f, onSubmit: async (d) => { await API.patch(O(`fields/${f.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-archive-field]').forEach((b) => b.onclick = () => {
    const f = fields.results.find((x) => x.id === b.dataset.archiveField);
    confirmBox(`Archive field <b>${esc(f.name)}</b>? Its history is kept.`, 'Archive', async () => { await API.del(O(`fields/${f.id}/`)); toast('Field archived'); route(); });
  });
};

/* seasons & crop plans */
const seasonFields = () => [
  { key: 'name', label: 'Season name', required: true, wide: true, placeholder: 'e.g. 2026/27 Summer' },
  { key: 'start_date', label: 'Starts', type: 'date', required: true },
  { key: 'end_date', label: 'Ends', type: 'date', required: true },
  { key: 'status', label: 'Status', type: 'select', required: true, options: S.lists.season_statuses.map((s) => [s.value, s.label]) },
];
const planFields = (seasons, fields) => [
  { key: 'season', label: 'Season', type: 'select', required: true, options: seasons.map((s) => [s.id, s.name]) },
  { key: 'field', label: 'Field', type: 'select', required: true, options: fields.map((f) => [f.id, `${f.farm_name} / ${f.name} (${num(f.size_ha)} ha)`]) },
  { key: 'crop', label: 'Crop', type: 'select', required: true, options: S.lists.crops.map((c) => [c, c]) },
  { key: 'variety', label: 'Variety', placeholder: 'e.g. SC 727' },
  { key: 'planted_ha', label: 'Area (ha)', type: 'number', required: true },
  { key: 'target_yield_t_per_ha', label: 'Target yield (t/ha)', type: 'number' },
  { key: 'planting_date', label: 'Planting date', type: 'date' },
  { key: 'expected_harvest', label: 'Expected harvest', type: 'date' },
  { key: 'budget_amount', label: 'Budget for this crop', type: 'number', hint: 'Inputs, fuel and labour for the whole area' },
  { key: 'budget_currency', label: 'Budget currency', type: 'select', required: true, options: S.lists.currencies.map((c) => [c, c]) },
  { key: 'status', label: 'Status', type: 'select', required: true, options: S.lists.plan_statuses.map((s) => [s.value, s.label]) },
  { key: 'notes', label: 'Notes', type: 'textarea', wide: true },
];
PAGES.seasons = async (el) => {
  const [seasons, fields] = await Promise.all([API.get(O('seasons/')), API.get(O('fields/'))]);
  const edit = can('edit_seasons');
  let current = store.get('murimi-erp-season-' + S.org.id);
  if (!seasons.results.find((s) => s.id === current)) current = (seasons.results.find((s) => s.status === 'active') || seasons.results[0] || {}).id;
  const season = seasons.results.find((s) => s.id === current);
  const plans = season ? (await API.get(O('crop-plans/'), { season: season.id })).results : [];
  const costs = season ? await API.get(O('stock/plan-costs/'), { season: season.id }) : { plans: {} };
  const hv = season ? (await API.get(O('trade/harvests/'), { season: season.id })).results : [];
  const harvested = (p) => hv.filter((h) => h.crop_plan === p.id).reduce((t, h) => t + Number(h.tonnes), 0);
  const zwgRate = costs.zwg_rate_today ? Number(costs.zwg_rate_today) : null;
  const budgetUsd = (p) => p.budget_amount === null ? null : p.budget_currency === 'USD' ? Number(p.budget_amount)
    : p.budget_currency === 'ZWG' && zwgRate ? Number(p.budget_amount) / zwgRate : null;
  const inputs = (p) => costs.plans[p.id] ? Number(costs.plans[p.id].inputs_usd) : 0;
  const labour = (p) => costs.plans[p.id] ? Number(costs.plans[p.id].labour_usd || 0) : 0;
  const used = (p) => inputs(p) + labour(p);
  const usedBy = (fid) => plans.filter((p) => p.field === fid && p.status !== 'abandoned').reduce((t, p) => t + Number(p.planted_ha), 0);
  el.innerHTML = `${head('Seasons & crops', 'Plan what grows on each field, how many hectares, the target yield and the budget.',
    (can('record_harvest') && plans.length ? `<button class="btn" id="add-harvest">${ico('leaf')}Record harvest</button>` : '') + (edit ? `<button class="btn" id="add-season">${ico('plus')}New season</button>${season ? `<button class="btn primary" id="add-plan"${fields.results.length ? '' : ' disabled'}>${ico('plus')}Add crop plan</button>` : ''}` : ''))}
    ${seasons.results.length ? `<section class="panel"><div class="panel-head">
      <div class="f" style="min-width:240px"><label for="season-pick">Season</label><select id="season-pick">${seasons.results.map((s) => `<option value="${s.id}"${s.id === current ? ' selected' : ''}>${esc(s.name)} (${esc(s.status_label)})</option>`).join('')}</select></div>
      ${season ? `<div class="small muted">${fmtDate(season.start_date)} to ${fmtDate(season.end_date)} · ${num(season.planted_ha, 1)} ha planned ${edit ? `<button class="btn small" id="edit-season">${ico('edit')}Edit season</button>` : ''}</div>` : ''}
    </div>
    ${!fields.results.length ? '<p class="note warn">Add farms and fields first, then plan crops on them.</p>' : ''}
    ${plans.length ? `<div class="table-wrap"><table><thead><tr><th>Field</th><th>Crop</th><th class="num">Area</th><th class="num">Harvest / target</th><th class="num">Budget</th><th class="num">Per ha</th><th class="num">Spent so far</th><th>Status</th><th></th></tr></thead><tbody>
      ${plans.map((p) => `<tr><td><b>${esc(p.field_name)}</b><div class="small muted">${esc(p.farm_name)}</div></td><td>${esc(p.crop)}${p.variety ? `<div class="small muted">${esc(p.variety)}</div>` : ''}</td>
        <td class="num mono">${ha(p.planted_ha)}</td><td class="num mono">${harvested(p) ? `<b>${num(harvested(p), 1)} t</b><div class="small muted">${p.target_tonnes ? 'of ' + num(p.target_tonnes, 1) + ' t' : 'harvested'}</div>` : p.target_tonnes ? num(p.target_tonnes, 1) + ' t' : '—'}</td>
        <td class="num mono">${money(p.budget_amount, p.budget_currency)}</td><td class="num mono">${money(p.budget_per_ha, p.budget_currency)}</td>
        <td class="num mono">${used(p) ? money(used(p), 'USD') : '—'}${used(p) ? `<div class="small muted">inputs ${num(inputs(p), 0)} · labour ${num(labour(p), 0)}</div>` : ''}${used(p) && budgetUsd(p) ? `<div class="small ${used(p) > budgetUsd(p) ? 'over' : 'muted'}">${Math.round(used(p) / budgetUsd(p) * 100)}% of budget</div>` : ''}</td>
        <td><span class="chip${p.status === 'planted' ? ' green' : p.status === 'abandoned' ? ' warn' : ''}">${esc(p.status_label)}</span></td>
        <td>${edit ? `<div class="row-actions"><button class="btn small" data-edit-plan="${p.id}" aria-label="Edit">${ico('edit')}</button><button class="btn small danger" data-del-plan="${p.id}" aria-label="Delete">${ico('x')}</button></div>` : ''}</td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${ico('calendar')}<p>No crops planned in this season yet.</p></div>`}
    </section>
    ${season && fields.results.length ? `<section class="panel"><h2>How full each field is this season</h2><div class="bars">
      ${fields.results.map((f) => { const used = usedBy(f.id), pct = Math.min(100, used / Number(f.size_ha) * 100);
        return `<div class="row"><span><b>${esc(f.name)}</b><div class="small muted">${esc(f.farm_name)}</div></span><div class="bar"><span style="width:${pct}%"></span></div><span class="num mono small">${num(used, 1)} / ${num(f.size_ha, 1)}</span></div>`; }).join('')}
    </div></section>` : ''}`
    : `<section class="panel"><div class="empty">${ico('calendar')}<h2>No seasons yet</h2><p>Create a season, for example "2026/27 Summer", then plan crops on your fields.</p>${edit ? '<button class="btn primary" id="add-season-2">New season</button>' : ''}</div></section>`}`;
  if ($('#season-pick')) $('#season-pick').onchange = (e) => { store.set('murimi-erp-season-' + S.org.id, e.target.value); route(); };
  if ($('#add-harvest')) $('#add-harvest').onclick = async () => {
    const [items, stores] = await Promise.all([API.get(O('items/')), API.get(O('stores/'))]);
    harvestForm(plans.filter((p) => p.status !== 'abandoned'), items.results, stores.results);
  };
  if (!edit) return;
  const addSeason = () => openForm({ title: 'New season', fields: seasonFields(), values: { status: 'planning' }, submitLabel: 'Create season',
    onSubmit: async (d) => { const s = await API.post(O('seasons/'), d); store.set('murimi-erp-season-' + S.org.id, s.id); toast('Season created'); route(); } });
  [$('#add-season'), $('#add-season-2')].forEach((b) => b && (b.onclick = addSeason));
  if ($('#edit-season')) $('#edit-season').onclick = () => openForm({ title: `Edit ${season.name}`, fields: seasonFields(), values: season,
    onSubmit: async (d) => { await API.patch(O(`seasons/${season.id}/`), d); toast('Saved'); route(); } });
  if ($('#add-plan')) $('#add-plan').onclick = () => openForm({ title: 'Add a crop plan', fields: planFields(seasons.results, fields.results),
    values: { season: season.id, budget_currency: S.org.reporting_currency, status: 'planned' }, submitLabel: 'Add plan',
    onSubmit: async (d) => { await API.post(O('crop-plans/'), d); toast('Crop plan added'); route(); } });
  el.querySelectorAll('[data-edit-plan]').forEach((b) => b.onclick = () => {
    const p = plans.find((x) => x.id === b.dataset.editPlan);
    openForm({ title: `Edit ${p.crop} on ${p.field_name}`, fields: planFields(seasons.results, fields.results), values: p,
      onSubmit: async (d) => { await API.patch(O(`crop-plans/${p.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-del-plan]').forEach((b) => b.onclick = () => {
    const p = plans.find((x) => x.id === b.dataset.delPlan);
    confirmBox(`Delete the plan for <b>${esc(p.crop)}</b> on ${esc(p.field_name)}?`, 'Delete', async () => { await API.del(O(`crop-plans/${p.id}/`)); toast('Plan deleted'); route(); });
  });
};

/* exchange rates */
PAGES.rates = async (el) => {
  const data = await API.get(O('rates/'));
  const edit = can('edit_rates');
  el.innerHTML = `${head('Exchange rates', 'Rates are written as <b>1 US dollar = how many ZiG (or rand)</b>. Murimi adds official rates for everyone; you can add the rate your bank actually gave you, and yours is used first.',
    edit ? `<button class="btn" id="fetch-rate">${ico('down')}Get today's rate online</button><button class="btn primary" id="add-rate">${ico('plus')}Add a rate</button>` : '')}
    <div class="grid2">
      <section class="panel"><h2>Converter</h2>
        <form class="convert" id="conv"><div class="f"><label for="cv-a">Amount</label><input id="cv-a" inputmode="decimal" value="100"></div>
          <div class="f"><label for="cv-f">From</label><select id="cv-f">${S.lists.currencies.map((c) => `<option${c === 'USD' ? ' selected' : ''}>${c}</option>`).join('')}</select></div>
          <span style="padding-bottom:12px">${ico('swap')}</span>
          <div class="f"><label for="cv-t">To</label><select id="cv-t">${S.lists.currencies.map((c) => `<option${c === 'ZWG' ? ' selected' : ''}>${c}</option>`).join('')}</select></div>
          <div class="f wide" style="grid-column:1/-1"><label for="cv-d">On date</label><input id="cv-d" type="date" value="${todayISO()}"></div>
          <div class="result" id="cv-out" aria-live="polite"></div></form>
      </section>
      <section class="panel"><h2>Why the rate is saved with every record</h2>
        <p class="muted">A bag of fertiliser bought for ZiG 13,425 on 15 October is worth US$500 at that day's rate of 26.85. If the rate changes next week, the bag's cost stays US$500, so stock values and reports don't jump around. Every money record in later stages keeps the rate it used.</p>
      </section>
    </div>
    <section class="panel"><div class="panel-head"><h2>Rates</h2><p class="small muted">${data.online.auto ? 'Today\'s ZiG and rand rates are fetched online automatically each day. ' : ''}Online rates: <a href="${esc(data.online.url)}" target="_blank" rel="noopener">${esc(data.online.text)}</a></p></div>
      ${data.results.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Currency</th><th class="num">1 USD =</th><th>Source</th><th>Added by</th><th></th></tr></thead><tbody>
        ${data.results.map((r) => `<tr><td>${fmtDate(r.date)}</td><td>${r.currency === 'ZWG' ? 'ZiG (ZWG)' : 'Rand (ZAR)'}</td><td class="num mono"><b>${num(r.units_per_usd, 4)}</b></td>
          <td>${esc(r.source_label)}${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ''}</td><td>${r.murimi_wide ? '<span class="chip green">Murimi</span>' : esc(r.created_by)}</td>
          <td>${edit && !r.murimi_wide ? `<div class="row-actions"><button class="btn small danger" data-del-rate="${r.id}" aria-label="Remove">${ico('x')}</button></div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('swap')}<p>No rates yet. Add today's ZiG rate to start.</p></div>`}
    </section>`;
  const out = $('#cv-out');
  const run = async () => {
    const amount = $('#cv-a').value.trim();
    if (!amount) { out.textContent = ''; return; }
    try {
      const r = await API.get(O('convert/'), { amount, from: $('#cv-f').value, to: $('#cv-t').value, date: $('#cv-d').value });
      out.innerHTML = `${money(r.result, r.to)} <div class="small muted" style="font-weight:500">${r.rates_used.map((x) => `1 USD = ${num(x.units_per_usd, 4)} ${x.currency} (${fmtDate(x.date)}${x.murimi_wide ? ', Murimi rate' : ', your rate'})`).join(' · ') || 'Same currency'}</div>`;
    } catch (ex) { out.innerHTML = `<span class="small" style="color:var(--bad)">${esc(ex.message)}</span>`; }
  };
  $('#conv').addEventListener('input', () => { clearTimeout(run.t); run.t = setTimeout(run, 250); });
  $('#conv').addEventListener('submit', (e) => { e.preventDefault(); run(); });
  run();
  if (!edit) return;
  $('#fetch-rate').onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      const r = await API.post(O('rates/fetch/'), {});
      toast(r.results.length ? r.results.map((x) => `1 USD = ${num(x.units_per_usd, 4)} ${x.currency}`).join(' · ') : 'Rates are already up to date');
      route();
    } catch (ex) { toast(ex.message); b.disabled = false; }
  };
  $('#add-rate').onclick = () => openForm({
    title: 'Add an exchange rate', intro: 'Write it as 1 US dollar = how many units. For example, if US$1 buys ZiG 26.85, enter 26.85.',
    fields: [
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'currency', label: 'Currency', type: 'select', required: true, options: [['ZWG', 'ZiG (ZWG)'], ['ZAR', 'Rand (ZAR)']] },
      { key: 'units_per_usd', label: '1 USD =', type: 'number', required: true, placeholder: 'e.g. 26.85' },
      { key: 'source', label: 'Where the rate comes from', type: 'select', required: true, options: data.sources.map((s) => [s.value, s.label]) },
      { key: 'note', label: 'Note', wide: true, placeholder: 'e.g. CBZ rate for the fertiliser payment' },
    ],
    values: { date: todayISO(), currency: 'ZWG', source: 'bank' }, submitLabel: 'Add rate',
    onSubmit: async (d) => { await API.post(O('rates/'), d); toast('Rate added'); route(); },
  });
  el.querySelectorAll('[data-del-rate]').forEach((b) => b.onclick = () => confirmBox('Remove this rate?', 'Remove', async () => { await API.del(O(`rates/${b.dataset.delRate}/`)); toast('Rate removed'); route(); }));
};

/* team */
PAGES.team = async (el) => {
  const manage = can('manage_members');
  const [data, org, inv] = await Promise.all([API.get(O('members/')), API.get(O()), manage ? API.get(O('invites/')) : Promise.resolve({ results: [] })]);
  const openInv = inv.results.filter((i) => i.status === 'open');
  const roleHelp = { owner: 'Everything, including the team and approving pay runs', manager: 'Farms, seasons, rates, workers and pay runs', accountant: 'Rates, workers, pay runs (and approving them), pay rules; sees history',
    storekeeper: 'Items, stores, receiving stock, stock takes and fuel', supervisor: 'Records stock, fuel and who worked in the field; never sees wages', viewer: 'Can look, cannot change' };
  el.innerHTML = `${head('Team', 'Add the people who work with you and choose what each can do. They log in with their own WhatsApp number and password.',
    manage ? `<button class="btn primary" id="invite">${ico('users')}Invite someone</button><button class="btn" id="add-member">${ico('plus')}Add by number</button>` : '')}
    ${openInv.length ? `<section class="panel"><h2>Invites waiting</h2><div class="table-wrap"><table><tbody>${openInv.map((i) => `<tr><td><b>${esc(i.name || 'Someone')}</b><div class="small muted">as ${esc(i.role_label)} · expires ${fmtDate(i.expires_at.slice(0, 10))}</div></td>
      <td><div class="row-actions"><button class="btn small" data-share="${i.id}">Share link</button><button class="btn small danger" data-cancel-inv="${i.id}">Cancel</button></div></td></tr>`).join('')}</tbody></table></div></section>` : ''}
    <div class="grid2">
      <section class="panel"><h2>People</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>WhatsApp</th><th>Role</th><th></th></tr></thead><tbody>
        ${data.results.map((mm) => `<tr><td><b>${esc(mm.full_name)}</b>${mm.user_id === S.user.id ? ' <span class="chip">You</span>' : ''}</td><td class="mono small">${esc(mm.phone)}</td>
          <td>${manage ? `<select data-role="${mm.id}" aria-label="Role for ${esc(mm.full_name)}">${data.roles.map((r) => `<option value="${r.value}"${r.value === mm.role ? ' selected' : ''}>${r.label}</option>`).join('')}</select>` : esc(mm.role_label)}</td>
          <td>${manage && mm.user_id !== S.user.id ? `<div class="row-actions">${mm.role !== 'owner' ? `<button class="btn small" data-reset="${mm.id}">Reset password</button>` : ''}<button class="btn small danger" data-remove="${mm.id}" aria-label="Remove">${ico('x')}</button></div>` : ''}
            ${mm.must_change_password ? '<div class="small muted">Will choose a new password at next log-in</div>' : ''}</td></tr>`).join('')}
      </tbody></table></div></section>
      <section class="panel"><h2>What each role can do</h2><div class="table-wrap"><table><tbody>
        ${data.roles.map((r) => `<tr><td><b>${r.label}</b></td><td class="small muted">${roleHelp[r.value]}</td></tr>`).join('')}</tbody></table></div></section>
    </div>
    <section class="panel"><div class="panel-head"><h2>Business details</h2>${can('edit_organisation') ? `<button class="btn small" id="edit-org">${ico('edit')}Edit</button>` : ''}</div>
      <div class="table-wrap"><table><tbody>
        <tr><td class="muted">Business name</td><td><b>${esc(org.name)}</b></td></tr>
        <tr><td class="muted">Where</td><td>${esc([org.district, org.province].filter(Boolean).join(', ')) || '—'}</td></tr>
        <tr><td class="muted">ZIMRA TIN / BP</td><td class="mono">${esc(org.tin) || '—'}</td></tr>
        <tr><td class="muted">VAT</td><td>${org.vat_registered ? `Registered, <span class="mono">${esc(org.vat_number)}</span>` : 'Not VAT-registered'}</td></tr>
        <tr><td class="muted">Main reporting currency</td><td>${esc(org.reporting_currency)}</td></tr></tbody></table></div></section>`;
  if (can('edit_organisation')) $('#edit-org').onclick = () => openForm({ title: 'Business details', fields: orgFields(), values: org,
    onSubmit: async (d) => { const o = await API.patch(O(), d); Object.assign(S.org, o); S.orgs = S.orgs.map((x) => x.id === o.id ? { ...x, ...o } : x); toast('Saved'); route(); } });
  if (!manage) return;
  $('#invite').onclick = () => openForm({
    title: 'Invite someone', intro: 'You get a link to send by WhatsApp. They open it, create their account (or log in) and join with the role you choose. The link works once, for 7 days.',
    fields: [{ key: 'name', label: 'Who is it for?', wide: true, placeholder: 'e.g. Sipho, the new supervisor' },
      { key: 'role', label: 'Role', type: 'select', required: true, wide: true, options: data.roles.filter((r) => r.value !== 'owner').map((r) => [r.value, `${r.label}: ${roleHelp[r.value]}`]) }],
    values: { role: 'supervisor' }, submitLabel: 'Make link',
    onSubmit: async (d) => { const i = await API.post(O('invites/'), d); route(); setTimeout(() => shareInvite(i, org.name), 80); },
  });
  el.querySelectorAll('[data-share]').forEach((b) => b.onclick = () => shareInvite(openInv.find((i) => i.id === b.dataset.share), org.name));
  el.querySelectorAll('[data-cancel-inv]').forEach((b) => b.onclick = () => confirmBox('Cancel this invite? The link will stop working.', 'Cancel invite',
    async () => { await API.del(O(`invites/${b.dataset.cancelInv}/`)); toast('Invite cancelled'); route(); }));
  $('#add-member').onclick = () => openForm({
    title: 'Add by number', intro: 'For someone who already has a Murimi account. Otherwise, use Invite someone.',
    fields: [{ key: 'login', label: 'Their WhatsApp number or username', required: true, wide: true },
      { key: 'role', label: 'Role', type: 'select', required: true, wide: true, options: data.roles.map((r) => [r.value, `${r.label}: ${roleHelp[r.value]}`]) }],
    values: { role: 'viewer' }, submitLabel: 'Add person',
    onSubmit: async (d) => { await API.post(O('members/'), d); toast('Person added'); route(); },
  });
  el.querySelectorAll('[data-role]').forEach((s) => s.onchange = async () => {
    try { await API.patch(O(`members/${s.dataset.role}/`), { role: s.value }); toast('Role changed'); }
    catch (ex) { toast(ex.message); route(); }
  });
  el.querySelectorAll('[data-reset]').forEach((b) => b.onclick = () => {
    const mm = data.results.find((x) => x.id === b.dataset.reset);
    confirmBox(`Give <b>${esc(mm.full_name)}</b> a temporary password? Their old password stops working, and they choose a new one when they log in.`, 'Reset password', async () => {
      const r = await API.post(O(`members/${mm.id}/reset-password/`), {});
      setTimeout(() => infoBox(`<h2>Temporary password</h2><p>Give this to <b>${esc(mm.full_name)}</b> in person or by phone. It is shown only once.</p>
        <p class="temp-pass mono">${esc(r.temporary_password)}</p>
        <p class="note">They log in with their WhatsApp number (<span class="mono">${esc(mm.phone)}</span>) and this password, then choose their own.</p>`), 50);
      route();
    });
  });
  el.querySelectorAll('[data-remove]').forEach((b) => b.onclick = () => confirmBox('Remove this person from the business? They keep their Murimi account.', 'Remove',
    async () => { await API.del(O(`members/${b.dataset.remove}/`)); toast('Removed'); route(); }));
};

/* change history */
PAGES.history = async (el) => {
  if (!can('view_audit')) { el.innerHTML = `${head('Change history')}<section class="panel"><p class="muted">Only the owner, manager or accountant can see the change history.</p></section>`; return; }
  const data = await API.get(O('audit/'));
  el.innerHTML = `${head('Change history', 'Every change in this business: who, what and when. These records cannot be edited or deleted.')}
    <section class="panel audit">${data.results.length ? `<ul>${data.results.map((e) => `<li><span class="small muted">${new Date(e.at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}<br><b style="color:var(--ink)">${esc(e.who)}</b></span>
      <span>${esc(e.summary)}${Object.keys(e.changes || {}).length && e.action === 'update' ? `<div class="changes">${Object.entries(e.changes).map(([k, v]) => `${esc(k)}: ${esc(Array.isArray(v) ? `${v[0] ?? '—'} → ${v[1] ?? '—'}` : v)}`).join(' · ')}</div>` : ''}</span></li>`).join('')}</ul>`
    : '<p class="muted">Nothing yet.</p>'}</section>`;
};

/* ---------------- stage 2: stock and fuel ---------------- */
function infoBox(html) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="backdrop"><div class="modal" role="alertdialog" aria-modal="true" style="max-width:460px">${html}
    <div class="actions"><button class="btn primary" data-ok>OK</button></div></div></div>`;
  $('[data-ok]', root).onclick = () => { root.innerHTML = ''; };
  $('[data-ok]', root).focus();
}
async function stockRefs() {
  const [items, stores, equipment, opts, seasons, fields] = await Promise.all([
    API.get(O('items/')), API.get(O('stores/')), API.get(O('equipment/')), API.get(O('stock/options/')),
    API.get(O('seasons/')), API.get(O('fields/'))]);
  const open = seasons.results.filter((s) => s.status !== 'closed');
  const season = open.find((s) => s.status === 'active') || open[0];
  const plans = season ? (await API.get(O('crop-plans/'), { season: season.id })).results.filter((p) => p.status !== 'abandoned') : [];
  return { items: items.results, stores: stores.results, equipment: equipment.results, opts, season, plans, fields: fields.results };
}
const withUnit = (txt, unit) => `${txt} ${/^\d/.test(unit) ? '× ' : ''}${esc(unit)}`;
const itemOpts = (R, filter = () => true) => R.items.filter(filter).map((i) => [i.id, `${i.name} (${i.unit})`]);
const storeOpts = (R, filter = () => true) => R.stores.filter(filter).map((s) => [s.id, s.name + (s.kind === 'tank' ? ' (tank)' : '')]);

function receiveForm(R, { fuelOnly = false, values = {} } = {}) {
  const items = itemOpts(R, (i) => !fuelOnly || i.category === 'fuel');
  if (!items.length) return infoBox(`<h2>Add an item first</h2><p class="muted">Add ${fuelOnly ? 'diesel or petrol' : 'the item'} under Stock → Items, then receive it.</p>`);
  if (!R.stores.length) return infoBox('<h2>Add a store first</h2><p class="muted">Add a store or fuel tank under Stock → Stores.</p>');
  openForm({
    title: fuelOnly ? 'Receive fuel' : 'Receive stock', intro: 'A delivery arriving in a store. Enter the price for ONE unit, in the currency you paid.',
    fields: [
      { key: 'item', label: 'Item', type: 'select', required: true, wide: true, options: items },
      { key: 'store', label: fuelOnly ? 'Tank' : 'Store', type: 'select', required: true, options: storeOpts(R, (s) => !fuelOnly || s.kind === 'tank') },
      { key: 'date', label: 'Date received', type: 'date', required: true },
      { key: 'quantity', label: 'Quantity', type: 'number', required: true, hint: "In the item's unit (bags, litres, kg...)" },
      { key: 'unit_cost', label: 'Price for one unit', type: 'number', required: true },
      { key: 'currency', label: 'Paid in', type: 'select', required: true, options: S.lists.currencies.map((c) => [c, c === 'ZWG' ? 'ZiG (ZWG)' : c]) },
      { key: 'supplier', label: 'Supplier', placeholder: 'e.g. Windmill, Farm & City' },
      { key: 'lot_number', label: 'Batch or lot number' },
      { key: 'expiry', label: 'Expiry date', type: 'date', hint: 'For chemicals and seed' },
      { key: 'reason', label: 'Note', wide: true, placeholder: 'e.g. invoice number' },
    ],
    values: { date: todayISO(), currency: S.org.reporting_currency, ...values }, submitLabel: 'Receive',
    onSubmit: async (d) => { await API.post(O('stock/receive/'), d); toast('Stock received'); route(); },
  });
}
function useForm(R, { fuelOnly = false, values = {} } = {}) {
  const items = itemOpts(R, (i) => !fuelOnly || i.category === 'fuel');
  if (!items.length || !R.stores.length) return infoBox('<h2>Nothing to use yet</h2><p class="muted">Add items and stores, then receive stock first.</p>');
  const planOpts = R.plans.map((p) => [p.id, `${p.crop} on ${p.farm_name} / ${p.field_name}`]);
  openForm({
    title: fuelOnly ? 'Fill a machine' : 'Record stock used',
    intro: fuelOnly ? 'Diesel or petrol put into a tractor, truck, generator or pump. Write the hour meter or km reading every time: it shows fuel use and warns about theft.'
      : `Stock taken out of a store and used. Link it to a crop${R.season ? ` in ${esc(R.season.name)}` : ''} so its cost counts against that crop's budget.`,
    fields: fuelOnly ? [
      { key: 'equipment', label: 'Machine', type: 'select', required: true, wide: true, options: R.equipment.map((e) => [e.id, e.name]) },
      { key: 'item', label: 'Fuel', type: 'select', required: true, options: items },
      { key: 'store', label: 'From tank', type: 'select', required: true, options: storeOpts(R, (s) => s.kind === 'tank') },
      { key: 'quantity', label: 'Litres', type: 'number', required: true },
      { key: 'meter_reading', label: 'Meter reading now', type: 'number', hint: 'Hours or km on the machine' },
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'crop_plan', label: 'Work on crop (optional)', type: 'select', options: planOpts },
      { key: 'reason', label: 'Note', wide: true, placeholder: 'e.g. ploughing Block A' },
    ] : [
      { key: 'item', label: 'Item', type: 'select', required: true, wide: true, options: items },
      { key: 'store', label: 'From store', type: 'select', required: true, options: storeOpts(R) },
      { key: 'quantity', label: 'Quantity used', type: 'number', required: true },
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'crop_plan', label: 'Used on crop', type: 'select', options: planOpts, hint: 'Best choice: its cost goes to that crop' },
      { key: 'field', label: '…or just a field', type: 'select', options: R.fields.map((f) => [f.id, `${f.farm_name} / ${f.name}`]) },
      { key: 'rate_per_ha', label: 'Rate per hectare', type: 'number', hint: 'Optional, e.g. 0.8 bags/ha' },
      { key: 'equipment', label: 'Machine (if fuel or oil)', type: 'select', options: R.equipment.map((e) => [e.id, e.name]) },
      { key: 'reason', label: 'Note', wide: true, placeholder: 'e.g. basal dressing, top-up spray' },
    ],
    values: { date: todayISO(), ...values }, submitLabel: 'Save',
    onSubmit: async (d) => {
      const r = await API.post(O('stock/issue/'), d);
      route();
      if (r.safe_reentry || r.earliest_harvest) {
        setTimeout(() => infoBox(`<h2>${ico('warn')} Chemical safety</h2><div class="safety">${r.safe_reentry ? `<p>${esc(r.safe_reentry)}</p>` : ''}${r.earliest_harvest ? `<p>Don't harvest this field before <b>${fmtDate(r.earliest_harvest)}</b>.</p>` : ''}</div>`), 50);
      } else toast('Saved');
    },
  });
}
function moveForm(R, values = {}) {
  openForm({
    title: 'Move stock between stores', fields: [
      { key: 'item', label: 'Item', type: 'select', required: true, wide: true, options: itemOpts(R) },
      { key: 'from_store', label: 'From', type: 'select', required: true, options: storeOpts(R) },
      { key: 'to_store', label: 'To', type: 'select', required: true, options: storeOpts(R) },
      { key: 'quantity', label: 'Quantity', type: 'number', required: true },
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'reason', label: 'Note', wide: true },
    ], values: { date: todayISO(), ...values }, submitLabel: 'Move',
    onSubmit: async (d) => { await API.post(O('stock/transfer/'), d); toast('Stock moved'); route(); },
  });
}
function countForm(R, values = {}) {
  openForm({
    title: 'Stock take', intro: 'Count what is really on the shelf. If it is different from the records, the difference is recorded with your reason.',
    fields: [
      { key: 'item', label: 'Item', type: 'select', required: true, wide: true, options: itemOpts(R) },
      { key: 'store', label: 'Store', type: 'select', required: true, options: storeOpts(R) },
      { key: 'counted', label: 'You counted', type: 'number', required: true },
      { key: 'date', label: 'Date', type: 'date', required: true },
      { key: 'reason', label: 'Why is it different?', required: true, placeholder: 'e.g. bags burst, stolen, not recorded' },
    ], values: { date: todayISO(), ...values }, submitLabel: 'Save count',
    onSubmit: async (d) => { await API.post(O('stock/count/'), d); toast('Stock take saved'); route(); },
  });
}
const itemFields = (R) => [
  { key: 'name', label: 'Name', required: true, wide: true, placeholder: 'e.g. Compound D, Ammonium Nitrate, SC 727 seed, Diesel' },
  { key: 'category', label: 'Type', type: 'select', required: true, options: R.opts.categories.map((c) => [c.value, c.label]) },
  { key: 'unit', label: 'Counted in', type: 'select', required: true, options: R.opts.units.map((u) => [u, u]) },
  { key: 'reorder_level', label: 'Warn me when it falls to', type: 'number' },
  { key: 'active_ingredient', label: 'Active ingredient', hint: 'Chemicals only' },
  { key: 'reentry_hours', label: 'Re-entry interval (hours)', type: 'number', hint: 'From the label' },
  { key: 'preharvest_days', label: 'Pre-harvest interval (days)', type: 'number', hint: 'From the label' },
  { key: 'notes', label: 'Notes', type: 'textarea', wide: true },
];
const storeFields = (R) => [
  { key: 'name', label: 'Name', required: true, wide: true, placeholder: 'e.g. Main store, Chemical shed, Diesel tank 1' },
  { key: 'kind', label: 'Type', type: 'select', required: true, options: R.opts.store_kinds.map((k) => [k.value, k.label]) },
  { key: 'farm', label: 'On farm', type: 'select', options: [] },
  { key: 'capacity', label: 'Capacity (litres, tanks only)', type: 'number' },
];
const equipmentFields = (R) => [
  { key: 'name', label: 'Name', required: true, wide: true, placeholder: 'e.g. Tractor 2 (MF 375)' },
  { key: 'kind', label: 'Type', type: 'select', required: true, options: R.opts.equipment_kinds.map((k) => [k.value, k.label]) },
  { key: 'meter', label: 'Meter', type: 'select', required: true, options: R.opts.meters.map((k) => [k.value, k.label]) },
  { key: 'registration', label: 'Registration' },
];

PAGES.stock = async (el) => {
  const tab = location.hash.split('/')[2] || 'onhand';
  const R = await stockRefs();
  const farms = (await API.get(O('farms/'))).results;
  const tabs = [['onhand', 'In stock'], ['history', 'History'], ['items', 'Items'], ['stores', 'Stores']];
  const acts = [
    can('receive_stock') ? `<button class="btn" data-act="receive">${ico('down')}Receive</button>` : '',
    can('issue_stock') ? `<button class="btn primary" data-act="use">${ico('up')}Record use</button>` : '',
    can('count_stock') ? `<button class="btn" data-act="move">${ico('swap')}Move</button><button class="btn" data-act="count">${ico('count')}Stock take</button>` : ''].join('');
  let body = '';
  if (tab === 'onhand') {
    const cat = store.get('murimi-erp-cat') || '';
    const data = await API.get(O('stock/'), cat ? { category: cat } : {});
    body = `<section class="panel"><div class="panel-head"><div class="seg">${[['', 'All'], ...R.opts.categories.map((c) => [c.value, c.label])].map(([v, l]) =>
        `<button data-cat="${v}" aria-current="${v === cat}">${l}</button>`).join('')}</div>
        <div class="small muted">Worth <b class="mono">${money(data.total_usd, 'USD')}</b>${data.total_zwg_today ? ` · <span class="mono">ZiG ${num(data.total_zwg_today)}</span> at today's rate` : ''}</div></div>
      ${data.results.length ? `<div class="table-wrap"><table><thead><tr><th>Item</th><th>Store</th><th class="num">In stock</th><th class="num">Value US$</th><th class="num">ZiG paid</th><th class="num">ZiG today</th><th></th></tr></thead><tbody>
        ${data.results.map((r) => `<tr><td><b>${esc(r.item_name)}</b></td><td>${esc(r.store_name)}</td>
          <td class="num mono${r.low ? ' qty-low' : ''}">${withUnit(num(r.qty, r.qty.endsWith('.000') ? 0 : 2), r.unit)}${r.low ? '<div class="small">running low</div>' : ''}</td>
          <td class="num mono">${num(r.value_usd)}</td><td class="num mono">${r.value_zwg_bought ? num(r.value_zwg_bought) : '—'}</td><td class="num mono">${r.value_zwg_today ? num(r.value_zwg_today) : '—'}</td>
          <td><div class="row-actions"><button class="btn small" data-batches="${r.item}|${r.store}">Batches</button>${can('issue_stock') ? `<button class="btn small" data-use="${r.item}|${r.store}">Use</button>` : ''}${can('count_stock') ? `<button class="btn small" data-count="${r.item}|${r.store}">Count</button>` : ''}</div></td></tr>`).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('box')}<p>Nothing in stock${cat ? ' in this group' : ''} yet.</p>${!R.items.length ? '<p class="small">Start by adding items (Compound D, seed, chemicals, diesel) and a store.</p>' : ''}</div>`}
      ${data.out_of_stock.length ? `<p class="note warn">${ico('warn')}Out of stock: ${data.out_of_stock.map((i) => esc(i.item_name)).join(', ')}</p>` : ''}
      <p class="note"><b>ZiG paid</b> is what the stock cost in ZiG on the day it arrived. <b>ZiG today</b> is its US dollar cost at today's rate${data.zwg_rate_today ? ` (1 USD = ${num(data.zwg_rate_today, 4)} ZiG)` : ''}. The US$ value never changes when rates move.</p></section>`;
  } else if (tab === 'history') {
    const data = await API.get(O('stock/movements/'));
    const undo = can('undo_stock');
    const sign = (g) => g.kind === 'receipt' || g.kind === 'adjust_in' ? 'in' : g.kind === 'transfer' ? '' : 'out';
    body = `<section class="panel">${data.results.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>What happened</th><th>Item</th><th class="num">Qty</th><th class="num">US$</th><th>For / where</th><th>By</th><th></th></tr></thead><tbody>
      ${data.results.map((g) => `<tr class="${g.reversed ? 'reversed' : ''}"><td class="small">${fmtDate(g.date)}</td>
        <td><span class="kind ${g.is_reversal ? '' : sign(g)}">${g.is_reversal ? 'Undo' : esc(g.kind_label)}</span></td>
        <td><b>${esc(g.item_name)}</b><div class="small muted">${esc(g.stores.join(' → '))}</div></td>
        <td class="num mono">${withUnit(num(Math.abs(Number(g.qty)), Number.isInteger(Number(g.qty)) ? 0 : 2), g.unit)}</td><td class="num mono">${num(Math.abs(Number(g.value_usd)))}</td>
        <td class="small">${esc([g.crop && `${g.crop}`, g.field, g.equipment, g.meter_reading && `meter ${g.meter_reading}`].filter(Boolean).join(' · '))}${g.reason ? `<div class="muted">${esc(g.reason)}</div>` : ''}</td>
        <td class="small">${esc(g.by)}</td>
        <td>${undo && !g.is_reversal && !g.reversed ? `<button class="btn small" data-undo="${g.group}" aria-label="Undo">${ico('undo')}</button>` : g.reversed ? '<span class="chip">Undone</span>' : ''}</td></tr>`).join('')}
    </tbody></table></div>` : `<div class="empty">${ico('history')}<p>No stock records yet.</p></div>`}
    <p class="note">Records are never deleted. "Undo" adds an opposite record, so the history always shows what really happened.</p></section>`;
  } else if (tab === 'items') {
    body = `<section class="panel"><div class="panel-head"><h2>Items</h2>${can('edit_stock_setup') ? `<button class="btn primary small" data-act="add-item">${ico('plus')}Add item</button>` : ''}</div>
      ${R.items.length ? `<div class="table-wrap"><table><thead><tr><th>Item</th><th>Type</th><th>Unit</th><th class="num">Warn at</th><th>Safety</th><th></th></tr></thead><tbody>
      ${R.items.map((i) => `<tr><td><b>${esc(i.name)}</b>${i.active_ingredient ? `<div class="small muted">${esc(i.active_ingredient)}</div>` : ''}</td><td>${esc(i.category_label)}</td><td>${esc(i.unit)}</td>
        <td class="num mono">${i.reorder_level ? num(i.reorder_level, 0) : '—'}</td>
        <td class="small">${[i.reentry_hours && `Re-entry ${i.reentry_hours} h`, i.preharvest_days && `Pre-harvest ${i.preharvest_days} d`].filter(Boolean).join(' · ') || '—'}</td>
        <td>${can('edit_stock_setup') ? `<div class="row-actions"><button class="btn small" data-edit-item="${i.id}">${ico('edit')}</button><button class="btn small danger" data-archive-item="${i.id}">${ico('archive')}</button></div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('box')}<p>No items yet. Add what you keep: fertiliser, seed, chemicals, diesel.</p></div>`}</section>`;
  } else {
    body = `<div class="grid2"><section class="panel"><div class="panel-head"><h2>Stores and tanks</h2>${can('edit_stock_setup') ? `<button class="btn primary small" data-act="add-store">${ico('plus')}Add</button>` : ''}</div>
      ${R.stores.length ? `<div class="table-wrap"><table><tbody>${R.stores.map((s) => `<tr><td><b>${esc(s.name)}</b><div class="small muted">${esc(s.kind_label)}${s.farm_name ? ' · ' + esc(s.farm_name) : ''}${s.capacity ? ` · ${num(s.capacity, 0)} L` : ''}</div></td>
        <td>${can('edit_stock_setup') ? `<div class="row-actions"><button class="btn small" data-edit-store="${s.id}">${ico('edit')}</button><button class="btn small danger" data-archive-store="${s.id}">${ico('archive')}</button></div>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="muted">No stores yet.</p>'}</section>
      <section class="panel"><div class="panel-head"><h2>Machines</h2><a class="btn small" href="#/fuel">Open fuel</a></div>
        ${R.equipment.length ? `<p class="muted">${R.equipment.map((e) => esc(e.name)).join(', ')}</p>` : '<p class="muted">Add tractors and other machines on the Fuel page.</p>'}</section></div>`;
  }
  el.innerHTML = `${head('Stock', 'What is in your stores, what it cost in US dollars and ZiG, and where it was used. The oldest stock is always used first.', acts)}
    <div class="seg" role="tablist">${tabs.map(([k, l]) => `<a href="#/stock/${k}" aria-current="${k === tab}">${l}</a>`).join('')}</div>${body}`;

  const pair = (v) => { const [item, st] = v.split('|'); return { item, store: st }; };
  el.querySelectorAll('[data-act]').forEach((b) => b.onclick = () => ({
    receive: () => receiveForm(R), use: () => useForm(R), move: () => moveForm(R), count: () => countForm(R),
    'add-item': () => openForm({ title: 'Add an item', fields: itemFields(R), values: { category: 'fertiliser', unit: '50 kg bag' }, submitLabel: 'Add item',
      onSubmit: async (d) => { await API.post(O('items/'), d); toast('Item added'); route(); } }),
    'add-store': () => { const f = storeFields(R); f[2].options = farms.map((x) => [x.id, x.name]);
      openForm({ title: 'Add a store or tank', fields: f, values: { kind: 'store' }, submitLabel: 'Add',
        onSubmit: async (d) => { await API.post(O('stores/'), d); toast('Added'); route(); } }); },
  }[b.dataset.act])());
  el.querySelectorAll('[data-cat]').forEach((b) => b.onclick = () => { store.set('murimi-erp-cat', b.dataset.cat); route(); });
  el.querySelectorAll('[data-use]').forEach((b) => b.onclick = () => useForm(R, { values: pair(b.dataset.use) }));
  el.querySelectorAll('[data-count]').forEach((b) => b.onclick = () => countForm(R, pair(b.dataset.count)));
  el.querySelectorAll('[data-batches]').forEach((b) => b.onclick = async () => {
    const data = await API.get(O('stock/batches/'), pair(b.dataset.batches));
    infoBox(`<h2>Batches, oldest first</h2><p class="muted small">Used in this order.</p><div class="table-wrap"><table><thead><tr><th>Arrived</th><th class="num">Left</th><th class="num">Paid</th><th class="num">US$ each</th><th>Expiry</th></tr></thead><tbody>
      ${data.results.map((x) => `<tr><td class="small">${fmtDate(x.received_on)}<div class="muted">${esc(x.supplier)}${x.lot_number ? ' · ' + esc(x.lot_number) : ''}</div></td>
        <td class="num mono">${num(x.qty_remaining, 0)}/${num(x.qty_received, 0)}</td><td class="num mono">${money(x.unit_cost, x.currency)}${x.rate_zwg && x.currency !== 'USD' ? `<div class="small muted">@ ${num(x.rate_zwg, 2)}</div>` : ''}</td>
        <td class="num mono">${num(x.unit_cost_usd)}</td><td class="small">${x.expiry ? fmtDate(x.expiry) : '—'}</td></tr>`).join('')}</tbody></table></div>`);
  });
  el.querySelectorAll('[data-undo]').forEach((b) => b.onclick = () => openForm({
    title: 'Undo this record', intro: 'An opposite record is added; nothing is deleted.',
    fields: [{ key: 'reason', label: 'Why?', required: true, wide: true, placeholder: 'e.g. wrong field, typed 40 instead of 4' }], submitLabel: 'Undo',
    onSubmit: async (d) => { await API.post(O(`stock/movements/${b.dataset.undo}/undo/`), d); toast('Undone'); route(); } }));
  el.querySelectorAll('[data-edit-item]').forEach((b) => b.onclick = () => {
    const i = R.items.find((x) => x.id === b.dataset.editItem);
    openForm({ title: `Edit ${i.name}`, fields: itemFields(R), values: i, onSubmit: async (d) => { await API.patch(O(`items/${i.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-archive-item]').forEach((b) => b.onclick = () => confirmBox('Archive this item? Its history is kept.', 'Archive',
    async () => { await API.del(O(`items/${b.dataset.archiveItem}/`)); toast('Archived'); route(); }));
  el.querySelectorAll('[data-edit-store]').forEach((b) => b.onclick = () => {
    const s = R.stores.find((x) => x.id === b.dataset.editStore); const f = storeFields(R); f[2].options = farms.map((x) => [x.id, x.name]);
    openForm({ title: `Edit ${s.name}`, fields: f, values: s, onSubmit: async (d) => { await API.patch(O(`stores/${s.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-archive-store]').forEach((b) => b.onclick = () => confirmBox('Archive this store? Its history is kept.', 'Archive',
    async () => { await API.del(O(`stores/${b.dataset.archiveStore}/`)); toast('Archived'); route(); }));
};

PAGES.fuel = async (el) => {
  const [R, data] = await Promise.all([stockRefs(), API.get(O('fuel/'))]);
  const acts = [
    can('receive_stock') ? `<button class="btn" data-act="receive">${ico('down')}Receive fuel</button>` : '',
    can('issue_stock') ? `<button class="btn primary" data-act="fill">${ico('fuel')}Fill a machine</button>` : '',
    can('edit_stock_setup') ? `<button class="btn" data-act="machine">${ico('plus')}Add machine</button>` : ''].join('');
  el.innerHTML = `${head('Fuel', 'Diesel and petrol in your tanks, which machine used it, and warnings when use looks wrong.', acts)}
    ${data.tanks.length ? `<section class="panel"><h2>Tanks</h2><div class="tanks">${data.tanks.map((t) => `<div class="tank"><b>${esc(t.name)}</b>
      <span class="mono">${num(t.litres, 0)} L${t.capacity ? ` of ${num(t.capacity, 0)} L` : ''}</span>${t.pct !== null ? `<div class="bar"><span style="width:${Math.min(100, t.pct)}%"></span></div>` : ''}</div>`).join('')}</div></section>`
      : `<section class="panel"><div class="empty">${ico('fuel')}<p>No fuel tank yet. Add one under Stock → Stores (type "Fuel tank / bowser"), and add Diesel as an item.</p><a class="btn small" href="#/stock/stores">Open stores</a></div></section>`}
    ${data.machines.length ? data.machines.map((mc) => `<section class="panel machine"><div class="panel-head"><div><h2>${ico('tractor')} ${esc(mc.name)}</h2>
        <p class="muted small">${esc(mc.kind_label)} · ${num(mc.total_litres, 0)} L used (US$ ${num(mc.total_usd)})${mc.usual_use ? ` · usual use <b>${num(mc.usual_use)} ${esc(mc.unit)}</b>` : ''}${mc.last_reading ? ` · meter ${num(mc.last_reading, 1)}` : ''}</p></div>
        ${mc.flags ? `<span class="flag">${ico('warn')}${mc.flags} warning${mc.flags > 1 ? 's' : ''}</span>` : '<span class="chip green">No warnings</span>'}</div>
      ${mc.fills.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Litres</th><th class="num">Meter</th><th class="num">Use</th><th class="num">US$</th><th>Warnings</th></tr></thead><tbody>
        ${mc.fills.map((f) => `<tr><td class="small">${fmtDate(f.date)}</td><td class="num mono">${num(f.litres, 1)}</td><td class="num mono">${f.reading ? num(f.reading, 1) : '—'}</td>
          <td class="num mono">${f.use ? `${num(f.use)} ${esc(mc.unit)}` : '—'}</td><td class="num mono">${num(f.value_usd)}</td>
          <td>${f.flags.map((x) => `<span class="flag">${esc(x)}</span>`).join('') || ''}</td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">No fuel recorded yet.</p>'}
    </section>`).join('') : `<section class="panel"><div class="empty">${ico('tractor')}<p>No machines yet. Add your tractors, trucks, generators and pumps.</p></div></section>`}
    <section class="panel"><h2>How the warnings work</h2><p class="muted">Each time a machine is filled, the litres put in replace what it used since the last fill. Murimi divides by the hours (or km) since then to get its use, and compares that with the machine's usual use. A fill that is 30% or more above usual, a meter that goes backwards, or a missing reading is flagged so you can ask questions early.</p></section>`;
  el.querySelectorAll('[data-act]').forEach((b) => b.onclick = () => ({
    receive: () => receiveForm(R, { fuelOnly: true }),
    fill: () => R.equipment.length ? useForm(R, { fuelOnly: true }) : infoBox('<h2>Add a machine first</h2><p class="muted">Click "Add machine" and enter your tractor or generator.</p>'),
    machine: () => openForm({ title: 'Add a machine', fields: equipmentFields(R), values: { kind: 'tractor', meter: 'hours' }, submitLabel: 'Add',
      onSubmit: async (d) => { await API.post(O('equipment/'), d); toast('Machine added'); route(); } }),
  }[b.dataset.act])());
};

PAGES.more = async (el) => {
  el.innerHTML = `${head('More')}<div class="more-list">${NAV.filter(navOk).map(([k, l, i]) => `<a href="#/${k}">${ico(i)}${l}</a>`).join('')}
    <a href="#" id="more-theme">${ico(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon')}Light / dark</a>
    <a href="#" id="more-logout">${ico('logout')}Log out</a></div>`;
  $('#more-theme').onclick = (e) => { e.preventDefault(); setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); route(); };
  $('#more-logout').onclick = (e) => { e.preventDefault(); logout(); };
};

/* ---------------- stage 3: labour and payroll ---------------- */
const L = (p) => O('labour/' + p);
const payMoney = (v, cur) => money(v, cur);
function modalShell(title, inner, wide) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="backdrop"><div class="modal${wide ? ' wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="m-title">
    <div class="modal-head"><h2 id="m-title">${esc(title)}</h2><button class="btn icon" type="button" data-close aria-label="Close">${ico('x')}</button></div>${inner}</div></div>`;
  const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  root.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
  $('.backdrop', root).addEventListener('mousedown', (e) => { if (e.target.classList.contains('backdrop')) close(); });
  return { root, close };
}
async function labourRefs() {
  const [opts, workers, tasks, seasons, fields] = await Promise.all([API.get(L('options/')), API.get(L('workers/')), API.get(L('tasks/')),
    API.get(O('seasons/')), API.get(O('fields/'))]);
  const open = seasons.results.filter((s) => s.status !== 'closed');
  const season = open.find((s) => s.status === 'active') || open[0];
  const plans = season ? (await API.get(O('crop-plans/'), { season: season.id })).results.filter((p) => p.status !== 'abandoned') : [];
  return { opts, can: opts.can, workers: workers.results, tasks: tasks.results, season, plans, fields: fields.results };
}

function registerForm(R, preset = {}) {
  const active = R.workers.filter((w) => w.is_active);
  if (!active.length) return infoBox('<h2>Add workers first</h2><p class="muted">Add your workers under Labour → Workers, then record their work here.</p>');
  const planOpts = R.plans.map((p) => `<option value="${p.id}">${esc(p.crop)} on ${esc(p.farm_name)} / ${esc(p.field_name)}</option>`).join('');
  const { root, close } = modalShell('Record work', `
    <p class="muted">Tick everyone who worked. For piece work, write how much each person did. Linking the work to a crop puts its cost on that crop.</p>
    <form class="grid" novalidate id="reg"><p class="form-error wide f" hidden style="grid-column:1/-1"></p>
      <div class="f"><label for="rg-date">Date</label><input id="rg-date" type="date" value="${preset.date || todayISO()}" max="${todayISO()}"></div>
      <div class="f"><label for="rg-kind">Type of work</label><select id="rg-kind"><option value="day">Days worked</option>${R.tasks.length ? '<option value="piece">Piece work</option>' : ''}</select></div>
      <div class="f" id="rg-task-box" hidden><label for="rg-task">Task</label><select id="rg-task">${R.tasks.map((t) => `<option value="${t.id}">${esc(t.name)} (per ${esc(t.unit)})</option>`).join('')}</select></div>
      <div class="f"><label for="rg-plan">Crop <span class="muted small">(optional)</span></label><select id="rg-plan"><option value="">—</option>${planOpts}</select></div>
      <div class="f wide"><label for="rg-note">Note <span class="muted small">(optional)</span></label><input id="rg-note" placeholder="e.g. weeding, harvesting, spraying"></div>
      <div class="register" style="grid-column:1/-1">
        <div class="reg-head"><label class="f check" style="margin:0"><input type="checkbox" id="rg-all"><span>Everyone</span></label><span class="small muted" id="rg-col">Days</span></div>
        ${active.map((w) => `<div class="reg-row" data-w="${w.id}" data-basis="${w.pay_basis}"><label><input type="checkbox" data-tick><span><b>${esc(w.full_name)}</b><span class="small muted">${esc(w.job_title || w.kind_label)}</span></span></label>
          <select data-days aria-label="Days for ${esc(w.full_name)}"><option value="1">Full day</option><option value="0.5">Half day</option></select>
          <input data-qty inputmode="decimal" placeholder="Qty" aria-label="Quantity for ${esc(w.full_name)}" hidden></div>`).join('')}
      </div>
      <div class="actions"><span class="small muted" id="rg-count">0 people</span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="submit">Save</button></div>
    </form>`, true);
  const form = $('#reg', root);
  const kind = () => $('#rg-kind', root).value;
  const sync = () => {
    const piece = kind() === 'piece';
    $('#rg-task-box', root).hidden = !piece;
    const t = R.tasks.find((x) => x.id === $('#rg-task', root).value);
    $('#rg-col', root).textContent = piece ? `Amount (${t ? t.unit : ''})` : 'Days';
    root.querySelectorAll('.reg-row').forEach((r) => {
      const pieceOnly = r.dataset.basis === 'piece';
      $('[data-days]', r).hidden = piece; $('[data-qty]', r).hidden = !piece;
      r.classList.toggle('off', !piece && pieceOnly);
      if (!piece && pieceOnly) $('[data-tick]', r).checked = false;
    });
    count();
  };
  const count = () => { $('#rg-count', root).textContent = `${root.querySelectorAll('[data-tick]:checked').length} people`; };
  $('#rg-kind', root).onchange = sync; $('#rg-task', root).onchange = sync;
  $('#rg-all', root).onchange = (e) => { root.querySelectorAll('.reg-row:not(.off) [data-tick]').forEach((c) => { c.checked = e.target.checked; }); count(); };
  root.querySelectorAll('[data-tick]').forEach((c) => c.onchange = count);
  root.querySelectorAll('[data-qty]').forEach((q) => q.oninput = () => { const c = $('[data-tick]', q.parentElement); if (q.value) { c.checked = true; count(); } });
  if (preset.kind) { $('#rg-kind', root).value = preset.kind; }
  sync();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('.form-error', form); err.hidden = true;
    const piece = kind() === 'piece';
    const entries = [...root.querySelectorAll('.reg-row')].filter((r) => $('[data-tick]', r).checked).map((r) => piece
      ? { worker: r.dataset.w, quantity: $('[data-qty]', r).value.trim() } : { worker: r.dataset.w, days: $('[data-days]', r).value });
    if (!entries.length) { err.textContent = 'Tick at least one person.'; err.hidden = false; return; }
    if (piece && entries.some((x) => !x.quantity)) { err.textContent = 'Write how much each ticked person did.'; err.hidden = false; return; }
    const btn = $('button[type=submit]', form); btn.disabled = true;
    try {
      const shared = { date: $('#rg-date', root).value, kind: kind(), note: $('#rg-note', root).value.trim() };
      if (piece) shared.task = $('#rg-task', root).value;
      if ($('#rg-plan', root).value) shared.crop_plan = $('#rg-plan', root).value;
      // A fresh id per line, so pressing Save twice (or a weak network) never records the work twice.
      await API.post(L('work/'), { ...shared, entries: entries.map((x) => ({ ...x, id: uuid() })) });
      close(); toast(`Saved work for ${entries.length} ${entries.length === 1 ? 'person' : 'people'}`); route();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
  });
}

const workerFields = (R) => [
  { key: 'full_name', label: 'Full name', required: true, wide: true },
  { key: 'job_title', label: 'Job', placeholder: 'e.g. General hand, Tractor driver' },
  { key: 'grade', label: 'NEC grade', placeholder: 'e.g. A1', hint: 'Used to check the minimum wage' },
  { key: 'kind', label: 'Employment', type: 'select', required: true, options: R.opts.kinds.map((k) => [k.value, k.label]) },
  { key: 'pay_basis', label: 'Paid', type: 'select', required: true, options: R.opts.bases.map((k) => [k.value, k.label]) },
  { key: 'rate', label: 'Monthly salary or daily rate', type: 'number', hint: 'Leave empty for piece work only' },
  { key: 'currency', label: 'Paid in', type: 'select', required: true, options: [['USD', 'US dollars'], ['ZWG', 'ZiG']] },
  { key: 'zig_share_pct', label: 'Part paid in ZiG (%)', type: 'number', hint: 'e.g. 35 if 35% of a US$ wage is paid in ZiG' },
  { key: 'farm', label: 'Works on farm', type: 'select', options: R.farms ? R.farms.map((f) => [f.id, f.name]) : [] },
  { key: 'start_date', label: 'First day', type: 'date' },
  { key: 'end_date', label: 'Last day', type: 'date', hint: 'For contracts and seasonal workers' },
  { key: 'national_id', label: 'National ID number' },
  { key: 'phone', label: 'Phone', type: 'tel' },
  { key: 'nssa_number', label: 'NSSA number' },
  { key: 'pay_method', label: 'Pay by', type: 'select', required: true, options: R.opts.pay_methods.map((k) => [k.value, k.label]) },
  { key: 'pay_account', label: 'EcoCash or bank account' },
  { key: 'nssa_exempt', label: 'No NSSA deduction for this person', type: 'check', wide: true },
  { key: 'notes', label: 'Notes', type: 'textarea', wide: true },
];

function payslipHTML(run, s) {
  const c = s.currency;
  const row = (l, v, neg) => Number(v) ? `<tr><td>${l}</td><td class="num mono">${neg ? '−' : ''}${num(v)}</td></tr>` : '';
  const w = s.workings || {};
  return `<div class="slip"><div class="slip-head"><div><b>${esc(S.org.name)}</b><div class="small muted">Payslip · ${esc(run.name)} (${fmtDate(run.period_start)} – ${fmtDate(run.period_end)})</div></div>
      <div style="text-align:right"><b>${esc(s.worker_name)}</b><div class="small muted">${esc([s.job_title, s.grade && 'Grade ' + s.grade, s.nssa_number && 'NSSA ' + s.nssa_number].filter(Boolean).join(' · '))}</div></div></div>
    <table><tbody>
      <tr><th colspan="2">Earnings (${c === 'USD' ? 'US$' : 'ZiG'})</th></tr>
      ${row('Basic salary', s.basic)}${row(`Days worked (${num(s.days_worked, 1)})`, s.day_pay)}${row('Piece work', s.piece_pay)}${row(`Extra pay${s.extra_note ? ': ' + esc(s.extra_note) : ''}`, s.extra_pay)}
      <tr class="total"><td>Gross pay</td><td class="num mono">${num(s.gross)}</td></tr>
      <tr><th colspan="2">Deductions</th></tr>
      ${row(`NSSA (${w.nssa_pct || '4.5'}%)`, s.nssa_employee, true)}${row('PAYE', s.paye, true)}${row(`AIDS levy (${w.aids_pct || '3'}% of PAYE)`, s.aids_levy, true)}
      ${row('Advances', s.advances, true)}${row(`Other${s.other_note ? ': ' + esc(s.other_note) : ''}`, s.other_deductions, true)}
      <tr class="total"><td>Net pay</td><td class="num mono">${money(s.net, c)}</td></tr>
      ${Number(s.net_in_zwg) && c === 'USD' ? `<tr><td>Paid as</td><td class="num mono">US$ ${num(s.net_in_usd)} + ZiG ${num(s.net_in_zwg)}</td></tr>` : ''}
    </tbody></table>
    <p class="small muted">${esc(s.pay_method)}${s.pay_account ? ' · ' + esc(s.pay_account) : ''} · Employer also pays NSSA ${money(s.nssa_employer, c)}. Tax band: ${w.band ? `${w.band.rate}% less ${w.band.deduct}${w.scale && w.scale !== '1' ? ` (×${w.scale} for a part month)` : ''}` : '—'}.</p></div>`;
}
function printSlips(run, slips) {
  const css = [...document.styleSheets].map((ss) => { try { return [...ss.cssRules].map((r) => r.cssText).join('\n'); } catch (e) { return ''; } }).join('\n');
  const w = window.open('', '_blank');
  if (!w) return toast('Allow pop-ups to print payslips');
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Payslips ${esc(run.name)}</title><style>${css}
    body{background:#fff;padding:20px} .slip{break-inside:avoid;margin-bottom:24px}</style></head><body>${slips.map((s) => payslipHTML(run, s)).join('')}</body></html>`);
  w.document.close(); setTimeout(() => w.print(), 300);
}

async function payRunPage(el, id, R) {
  const run = await API.get(L(`pay-runs/${id}/`));
  const draft = run.status === 'draft';
  const tot = Object.entries(run.totals);
  const c = R.can;
  el.innerHTML = `${head(run.name, `${fmtDate(run.period_start)} to ${fmtDate(run.period_end)} · pay day ${fmtDate(run.pay_date)} · <span class="chip${draft ? ' warn' : ' green'}">${esc(run.status_label)}</span>${run.rate_zwg ? ` · 1 USD = ${num(run.rate_zwg, 4)} ZiG` : ''}`,
    `<a class="btn" href="#/labour/pay">${ico('undo')}All pay runs</a>
     ${draft && c.run_payroll ? `<button class="btn" id="pr-recalc">${ico('swap')}Recalculate</button>` : ''}
     ${draft && c.approve_payroll ? `<button class="btn primary" id="pr-approve">${ico('check')}Approve</button>` : ''}
     ${!draft && c.approve_payroll ? `<button class="btn" id="pr-reopen">${ico('edit')}Reopen</button>` : ''}`)}
    ${draft ? `<p class="note warn">${ico('warn')}Draft: figures update when you record more work or press Recalculate. Approving locks the work, advances and payslips.</p>` : `<p class="note">Approved by ${esc(run.approved_by)} on ${fmtDate(run.approved_at.slice(0, 10))}.</p>`}
    ${tot.map(([cur, t]) => `<div class="stats">
      <div class="stat"><span class="n">${money(t.net, cur)}</span><span class="l">Net pay for ${t.people} ${t.people === 1 ? 'person' : 'people'}${Number(t.zwg_part) && cur === 'USD' ? `: US$ ${num(t.usd_part)} cash + ZiG ${num(t.zwg_part)}` : ''}</span></div>
      <div class="stat"><span class="n">${money(t.to_zimra, cur)}</span><span class="l">To ZIMRA: PAYE ${num(t.paye)} + AIDS levy ${num(t.aids)}</span></div>
      <div class="stat"><span class="n">${money(t.to_nssa, cur)}</span><span class="l">To NSSA: workers ${num(t.nssa_e)} + farm ${num(t.nssa_r)}</span></div>
      <div class="stat"><span class="n">${money(t.cost, cur)}</span><span class="l">Total cost to the farm (gross + NSSA)</span></div></div>`).join('') || ''}
    <section class="panel"><div class="panel-head"><h2>Payslips</h2><div class="row-actions">
      ${run.payslips.length ? `<button class="btn small" id="pr-print">Print all payslips</button><a class="btn small" href="${L(`pay-runs/${run.id}/csv/`)}">Download spreadsheet (CSV)</a>` : ''}
      ${draft && c.run_payroll ? `<button class="btn small danger" id="pr-delete">${ico('x')}Delete draft</button>` : ''}</div></div>
      ${run.payslips.length ? `<div class="table-wrap"><table><thead><tr><th>Worker</th><th class="num">Gross</th><th class="num">NSSA</th><th class="num">PAYE + AIDS</th><th class="num">Advances & other</th><th class="num">Net pay</th><th></th></tr></thead><tbody>
        ${run.payslips.map((s) => `<tr><td><b>${esc(s.worker_name)}</b><div class="small muted">${esc(s.pay_method)}${s.currency === 'ZWG' ? ' · ZiG' : ''}</div>
          ${s.warnings.map((x) => `<div class="small over">${ico('warn')} ${esc(x)}</div>`).join('')}</td>
          <td class="num mono">${num(s.gross)}</td><td class="num mono">${num(s.nssa_employee)}</td><td class="num mono">${num(Number(s.paye) + Number(s.aids_levy))}</td>
          <td class="num mono">${num(Number(s.advances) + Number(s.other_deductions))}</td><td class="num mono"><b>${money(s.net, s.currency)}</b>${Number(s.net_in_zwg) && s.currency === 'USD' ? `<div class="small muted">US$ ${num(s.net_in_usd)} + ZiG ${num(s.net_in_zwg)}</div>` : ''}</td>
          <td><div class="row-actions"><button class="btn small" data-view="${s.id}">Payslip</button>${draft && c.run_payroll ? `<button class="btn small" data-extra="${s.id}">${ico('edit')}</button>` : ''}</div></td></tr>`).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('wallet')}<p>Nobody to pay in this period yet. Add monthly staff, or record days and piece work for this period.</p></div>`}
      <p class="note">PAYE uses ZIMRA's monthly tables (US$ or ZiG, by how the person is paid). NSSA is taken only up to the monthly ceiling, and PAYE is worked out after NSSA. A pay run shorter than a month shrinks the tax bands to match. Check new tax tables each year under Labour → Pay rules.</p>
    </section>`;
  const find = (sid) => run.payslips.find((s) => s.id === sid);
  el.querySelectorAll('[data-view]').forEach((b) => b.onclick = () => {
    const s = find(b.dataset.view);
    const { root } = modalShell('Payslip', `${payslipHTML(run, s)}<div class="actions"><button class="btn" data-close>Close</button><button class="btn primary" id="one-print">Print</button></div>`);
    $('#one-print', root).onclick = () => printSlips(run, [s]);
  });
  el.querySelectorAll('[data-extra]').forEach((b) => b.onclick = () => {
    const s = find(b.dataset.extra);
    openForm({ title: `Adjust ${s.worker_name}'s pay`, intro: 'Extra pay is added before tax. Other deductions come off after tax.', fields: [
      { key: 'extra_pay', label: 'Extra pay', type: 'number', hint: 'Overtime, bonus, allowances' },
      { key: 'extra_note', label: 'What for' },
      { key: 'other_deductions', label: 'Other deductions', type: 'number', hint: 'e.g. rations, damaged tools' },
      { key: 'other_note', label: 'What for' },
    ], values: s, onSubmit: async (d) => { await API.patch(L(`payslips/${s.id}/`), d); toast('Payslip updated'); route(); } });
  });
  if ($('#pr-print')) $('#pr-print').onclick = () => printSlips(run, run.payslips);
  if ($('#pr-recalc')) $('#pr-recalc').onclick = async () => { await API.post(L(`pay-runs/${run.id}/recalculate/`), {}); toast('Recalculated'); route(); };
  if ($('#pr-approve')) $('#pr-approve').onclick = () => confirmBox(`Approve <b>${esc(run.name)}</b>? The work, advances and payslips lock, so the same work can't be paid twice.`, 'Approve',
    async () => { await API.post(L(`pay-runs/${run.id}/approve/`), {}); toast('Pay run approved'); route(); });
  if ($('#pr-reopen')) $('#pr-reopen').onclick = () => openForm({ title: 'Reopen this pay run', intro: 'Only do this if it was not paid yet, or to correct a mistake.',
    fields: [{ key: 'reason', label: 'Why?', required: true, wide: true }], submitLabel: 'Reopen',
    onSubmit: async (d) => { await API.post(L(`pay-runs/${run.id}/reopen/`), d); toast('Reopened'); route(); } });
  if ($('#pr-delete')) $('#pr-delete').onclick = () => confirmBox('Delete this draft pay run? The work records stay.', 'Delete',
    async () => { await API.del(L(`pay-runs/${run.id}/`)); location.hash = '#/labour/pay'; toast('Deleted'); });
}

PAGES.labour = async (el) => {
  const parts = location.hash.split('/');
  const R = await labourRefs();
  R.farms = (await API.get(O('farms/'))).results;
  const c = R.can;
  const tabs = [['work', 'Work register'], ['workers', 'Workers'], ...(c.view_pay ? [['pay', 'Pay runs'], ['advances', 'Advances']] : []), ['rules', 'Pay rules']];
  let tab = parts[2] || 'work';
  if (!tabs.some((t) => t[0] === tab)) tab = 'work';
  if (tab === 'pay' && parts[3]) return payRunPage(el, parts[3], R);
  const acts = [c.record_work ? `<button class="btn primary" data-act="register">${ico('check')}Record work</button>` : '',
    c.edit_workers ? `<button class="btn" data-act="add-worker">${ico('plus')}Add worker</button>` : '',
    c.run_payroll ? `<button class="btn" data-act="new-run">${ico('wallet')}New pay run</button>` : ''].join('');
  let body = '';
  if (tab === 'work') {
    const day = store.get('murimi-erp-workday') || todayISO();
    const from = new Date(new Date(day + 'T12:00:00').getTime() - 6 * 864e5).toISOString().slice(0, 10);
    const data = await API.get(L('work/'), { date_from: from, date_to: day });
    const byDate = {};
    data.results.forEach((e) => (byDate[e.date] = byDate[e.date] || []).push(e));
    body = `<section class="panel"><div class="panel-head"><div class="f" style="min-width:200px"><label for="wk-day">Week ending</label><input type="date" id="wk-day" value="${day}" max="${todayISO()}"></div>
        ${c.view_pay && data.total_cost_usd !== null ? `<div class="small muted">Labour cost this week <b class="mono">${money(data.total_cost_usd, 'USD')}</b></div>` : ''}</div>
      ${Object.keys(byDate).length ? Object.entries(byDate).map(([d, rows]) => `<h3 class="day-h">${fmtDate(d)} · ${new Set(rows.map((r) => r.worker)).size} people</h3>
        <div class="table-wrap"><table><tbody>${rows.map((e) => `<tr><td><b>${esc(e.worker_name)}</b></td>
          <td>${e.kind === 'piece' ? `${num(e.quantity, Number(e.quantity) % 1 ? 2 : 0)} ${esc(e.unit)} · ${esc(e.task_name)}` : (Number(e.days) === 1 ? 'Full day' : `${num(e.days, 1)} day`)}</td>
          <td class="small">${esc([e.crop, e.field_name].filter(Boolean).join(' · ') || '—')}${e.note ? `<div class="muted">${esc(e.note)}</div>` : ''}</td>
          ${c.view_pay ? `<td class="num mono">${Number(e.amount) ? money(e.amount, e.currency) : '<span class="small muted">salary</span>'}</td>` : ''}
          <td class="small muted">${esc(e.by)}</td>
          <td>${e.paid ? '<span class="chip green">Paid</span>' : c.record_work ? `<button class="btn small danger" data-del-work="${e.id}" aria-label="Remove">${ico('x')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`).join('')
        : `<div class="empty">${ico('calendar')}<p>No work recorded in these 7 days.</p>${c.record_work ? '<button class="btn primary small" data-act="register">Record work</button>' : ''}</div>`}
      <p class="note">Record who worked each day (or how much piece work each person did). Daily and piece pay is added up into the next pay run; for monthly staff the days show where their time went, so their salary counts in each crop's costs.</p></section>`;
  } else if (tab === 'workers') {
    const wk = (await API.get(L('workers/'), { include_archived: store.get('murimi-erp-wk-arch') === '1' ? '1' : '0' })).results;
    body = `<section class="panel"><div class="panel-head"><h2>Workers</h2><label class="f check small"><input type="checkbox" id="wk-arch"${store.get('murimi-erp-wk-arch') === '1' ? ' checked' : ''}>Show people who left</label></div>
      ${wk.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Job</th><th>Employment</th>${c.view_pay ? '<th class="num">Pay</th><th>NSSA</th>' : ''}<th></th></tr></thead><tbody>
      ${wk.map((w) => `<tr class="${w.is_active ? '' : 'reversed'}"><td><b>${esc(w.full_name)}</b><div class="small muted">${esc(w.farm_name || '')}${w.phone ? ' · ' + esc(w.phone) : ''}</div></td>
        <td>${esc(w.job_title || '—')}${w.grade ? ` <span class="chip">${esc(w.grade)}</span>` : ''}</td><td>${esc(w.kind_label)}<div class="small muted">${esc(w.pay_basis_label)}</div></td>
        ${c.view_pay ? `<td class="num mono">${w.pay_basis === 'piece' ? '<span class="small muted">by the piece</span>' : `${money(w.rate, w.currency)}<div class="small muted">${w.pay_basis === 'monthly' ? 'a month' : 'a day'}${Number(w.zig_share_pct) ? ` · ${num(w.zig_share_pct, 0)}% in ZiG` : ''}</div>`}</td>
        <td class="small">${w.nssa_exempt ? 'Exempt' : esc(w.nssa_number || '—')}</td>` : ''}
        <td>${c.edit_workers ? `<div class="row-actions"><button class="btn small" data-edit-worker="${w.id}">${ico('edit')}</button>${w.is_active ? `<button class="btn small danger" data-arch-worker="${w.id}" aria-label="Left the farm">${ico('archive')}</button>` : ''}</div>` : ''}</td></tr>`).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('users')}<p>No workers yet.</p></div>`}</section>
      <section class="panel"><div class="panel-head"><h2>Piece-work rates</h2>${c.edit_workers ? `<button class="btn small primary" data-act="add-task">${ico('plus')}Add rate</button>` : ''}</div>
        ${R.tasks.length ? `<div class="table-wrap"><table><tbody>${R.tasks.map((t) => `<tr><td><b>${esc(t.name)}</b></td><td class="num mono">${t.currency === 'USD' ? 'US$' : 'ZiG'} ${esc(t.rate)} per ${esc(t.unit)}</td>
          <td>${c.edit_workers ? `<div class="row-actions"><button class="btn small" data-edit-task="${t.id}">${ico('edit')}</button><button class="btn small danger" data-arch-task="${t.id}">${ico('archive')}</button></div>` : ''}</td></tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">No piece rates yet, e.g. "Picking cotton: US$0.05 per kg" or "Grading tobacco: US$2 per bale".</p>'}</section>`;
    R.listed = wk;
  } else if (tab === 'pay') {
    const runs = (await API.get(L('pay-runs/'))).results;
    body = `<section class="panel">${runs.length ? `<div class="table-wrap"><table><thead><tr><th>Pay run</th><th>Period</th><th class="num">People</th><th class="num">Net pay</th><th class="num">To ZIMRA</th><th class="num">To NSSA</th><th>Status</th><th></th></tr></thead><tbody>
      ${runs.map((r) => { const cur = Object.keys(r.totals); const f = (k) => cur.map((x) => money(r.totals[x][k], x)).join('<br>') || '—';
        return `<tr><td><b>${esc(r.name)}</b></td><td class="small">${fmtDate(r.period_start)} – ${fmtDate(r.period_end)}</td><td class="num mono">${r.people}</td>
        <td class="num mono">${f('net')}</td><td class="num mono">${f('to_zimra')}</td><td class="num mono">${f('to_nssa')}</td>
        <td><span class="chip${r.status === 'approved' ? ' green' : ' warn'}">${esc(r.status_label)}</span></td><td><a class="btn small" href="#/labour/pay/${r.id}">Open</a></td></tr>`; }).join('')}
      </tbody></table></div>` : `<div class="empty">${ico('wallet')}<p>No pay runs yet. Start one for last month, check it, then approve it.</p>${c.run_payroll ? '<button class="btn primary small" data-act="new-run">New pay run</button>' : ''}</div>`}</section>`;
  } else if (tab === 'advances') {
    const adv = (await API.get(L('advances/'))).results;
    body = `<section class="panel"><div class="panel-head"><h2>Advances</h2>${c.run_payroll ? `<button class="btn small primary" data-act="add-advance">${ico('plus')}Give an advance</button>` : ''}</div>
      ${adv.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Worker</th><th class="num">Amount</th><th>Reason</th><th>Taken off</th><th></th></tr></thead><tbody>
      ${adv.map((a) => `<tr><td class="small">${fmtDate(a.date)}</td><td><b>${esc(a.worker_name)}</b></td><td class="num mono">${money(a.amount, a.currency)}</td><td class="small">${esc(a.reason || '—')}</td>
        <td>${a.recovered ? `<span class="chip green">${esc(a.pay_run)}</span>` : '<span class="chip warn">Next pay run</span>'}</td>
        <td>${!a.recovered && c.run_payroll ? `<button class="btn small danger" data-del-adv="${a.id}" aria-label="Remove">${ico('x')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="muted">No advances. An advance is taken off the person\'s next pay run automatically (up to three quarters of that pay).</p>'}</section>`;
  } else {
    const rl = await API.get(L('rules/'));
    const cur = (k) => rl.rules.find((r) => r.id === rl.current[k]);
    const bandsTable = (r) => `<table><thead><tr><th>Monthly pay up to</th><th class="num">Rate</th><th class="num">Less</th></tr></thead><tbody>
      ${r.bands.map((b, i) => `<tr><td class="mono">${b[0] === null ? `above ${num(r.bands[i - 1][0], 0)}` : num(b[0], 0)}</td><td class="num mono">${b[1]}%</td><td class="num mono">${num(b[2])}</td></tr>`).join('')}</tbody></table>`;
    const mins = rl.minimum_wages.filter((x) => x.sector === rl.nec_sector);
    body = `<div class="grid2">${['USD', 'ZWG'].map((k) => { const r = cur(k); return `<section class="panel"><h2>PAYE in ${k === 'USD' ? 'US dollars' : 'ZiG'}</h2>
      ${r ? `<p class="small muted">From ${fmtDate(r.effective_from)} · ${r.murimi_wide ? 'Murimi\'s national table' : 'your own table'}</p>${bandsTable(r)}
        <p class="small">AIDS levy ${num(r.aids_levy_pct, 0)}% of PAYE · NSSA ${num(r.nssa_employee_pct, 1)}% worker + ${num(r.nssa_employer_pct, 1)}% farm, ${r.nssa_ceiling ? `up to ${money(r.nssa_ceiling, k)} a month` : 'ceiling = the US$ ceiling at the pay run\'s rate'} · ${r.working_days} working days a month</p>
        ${r.source ? `<p class="note">${esc(r.source)}</p>` : ''}` : '<p class="muted">No table yet.</p>'}</section>`; }).join('')}</div>
      <section class="panel"><div class="panel-head"><div><h2>NEC minimum wages</h2><p class="small muted">Sub-sector: <b>${esc(rl.nec_sector)}</b>. Murimi warns when someone is paid below their grade's minimum.</p></div>
        ${c.edit_rules ? `<div class="row-actions"><button class="btn small" data-act="sector">Change sub-sector</button><button class="btn small primary" data-act="add-min">${ico('plus')}Add a grade</button></div>` : ''}</div>
        ${mins.length ? `<div class="table-wrap"><table><thead><tr><th>Grade</th><th class="num">Monthly minimum</th><th>From</th><th>Source</th></tr></thead><tbody>
          ${mins.map((x) => `<tr><td><b>${esc(x.grade)}</b></td><td class="num mono">${money(x.monthly_usd, 'USD')}</td><td class="small">${fmtDate(x.effective_from)}</td><td class="small muted">${x.murimi_wide ? 'Murimi' : 'Yours'} · ${esc(x.source)}</td></tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">No minimum wages for this sub-sector yet.</p>'}</section>
      <section class="panel"><div class="panel-head"><h2>When the law changes</h2>${c.edit_rules ? `<button class="btn small" data-act="add-rule">${ico('plus')}New tax table</button>` : ''}</div>
        <p class="muted">Tax tables, NSSA and minimum wages change often. Add a new table with the date it starts: pay runs before that date keep using the old one. These figures are a starting point. <b>Have your accountant check them against ZIMRA, NSSA and your NEC schedule before you pay people.</b></p></section>`;
    R.rules = rl;
  }
  el.innerHTML = `${head('Labour & payroll', 'Who worked, piece work, advances and pay runs with PAYE, AIDS levy and NSSA worked out for you.', acts)}
    <div class="seg" role="tablist">${tabs.map(([k, l]) => `<a href="#/labour/${k}" aria-current="${k === tab}">${l}</a>`).join('')}</div>${body}`;

  const actions = {
    register: () => registerForm(R),
    'add-worker': () => openForm({ title: 'Add a worker', fields: workerFields(R), values: { kind: 'permanent', pay_basis: 'monthly', currency: 'USD', pay_method: 'cash', zig_share_pct: '0', start_date: todayISO() },
      submitLabel: 'Add worker', onSubmit: async (d) => { await API.post(L('workers/'), d); toast('Worker added'); route(); } }),
    'add-task': () => openForm({ title: 'Add a piece-work rate', fields: [
      { key: 'name', label: 'Task', required: true, wide: true, placeholder: 'e.g. Picking cotton' },
      { key: 'unit', label: 'Paid per', required: true, placeholder: 'kg, row, bale, hectare' },
      { key: 'rate', label: 'Pay for one', type: 'number', required: true },
      { key: 'currency', label: 'Currency', type: 'select', required: true, options: [['USD', 'US$'], ['ZWG', 'ZiG']] }],
      values: { currency: 'USD' }, submitLabel: 'Add', onSubmit: async (d) => { await API.post(L('tasks/'), d); toast('Added'); route(); } }),
    'new-run': () => {
      const t = new Date(); const s = new Date(t.getFullYear(), t.getMonth() - 1, 1); const e = new Date(t.getFullYear(), t.getMonth(), 0);
      const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      openForm({ title: 'New pay run', intro: 'Usually one calendar month. For casual workers paid weekly or fortnightly, choose those days; the tax bands shrink to match.', fields: [
        { key: 'period_start', label: 'First day', type: 'date', required: true }, { key: 'period_end', label: 'Last day', type: 'date', required: true },
        { key: 'pay_date', label: 'Pay day', type: 'date' }, { key: 'name', label: 'Name', placeholder: 'e.g. September 2026' }],
        values: { period_start: iso(s), period_end: iso(e), pay_date: iso(e) }, submitLabel: 'Work it out',
        onSubmit: async (d) => { const r = await API.post(L('pay-runs/'), d); location.hash = `#/labour/pay/${r.id}`; } });
    },
    'add-advance': () => openForm({ title: 'Give an advance', intro: 'It is taken off the person\'s next pay run.', fields: [
      { key: 'worker', label: 'Worker', type: 'select', required: true, wide: true, options: R.workers.filter((w) => w.is_active).map((w) => [w.id, w.full_name]) },
      { key: 'amount', label: 'Amount', type: 'number', required: true }, { key: 'currency', label: 'Currency', type: 'select', required: true, options: [['USD', 'US$'], ['ZWG', 'ZiG']] },
      { key: 'date', label: 'Date', type: 'date', required: true }, { key: 'reason', label: 'Reason', placeholder: 'e.g. school fees' }],
      values: { date: todayISO(), currency: 'USD' }, submitLabel: 'Save', onSubmit: async (d) => { await API.post(L('advances/'), d); toast('Advance saved'); route(); } }),
    sector: () => openForm({ title: 'NEC sub-sector', fields: [{ key: 'nec_sector', label: 'Your NEC Agriculture sub-sector', type: 'select', required: true, wide: true,
      options: [...new Set([...R.opts.sectors, 'General agriculture', 'Horticulture', 'Tobacco', 'Sugarcane', 'Forestry', 'Tea, coffee and macadamia'])].map((x) => [x, x]) }],
      values: { nec_sector: R.rules.nec_sector }, onSubmit: async (d) => { await API.post(L('rules/'), { type: 'sector', ...d }); toast('Saved'); route(); } }),
    'add-min': () => openForm({ title: 'Add a minimum wage', fields: [
      { key: 'sector', label: 'Sub-sector', required: true }, { key: 'grade', label: 'Grade', required: true, placeholder: 'e.g. A2' },
      { key: 'monthly_usd', label: 'Monthly minimum (US$)', type: 'number', required: true }, { key: 'effective_from', label: 'From', type: 'date', required: true },
      { key: 'source', label: 'Source', wide: true, placeholder: 'e.g. NEC Agriculture circular, June 2026' }],
      values: { sector: R.rules.nec_sector }, onSubmit: async (d) => { await API.post(L('rules/'), { type: 'minimum', ...d }); toast('Added'); route(); } }),
    'add-rule': () => {
      const base = R.rules.rules.find((r) => r.id === R.rules.current.USD) || {};
      openForm({ title: 'New tax table', intro: 'One line per band: <b>pay up to, rate %, amount to take off</b>. Leave "pay up to" empty on the last line, e.g. <span class="mono">, 40, 335</span>. Amounts are per month.', fields: [
        { key: 'currency', label: 'Currency', type: 'select', required: true, options: [['USD', 'US$'], ['ZWG', 'ZiG']] },
        { key: 'effective_from', label: 'Starts on', type: 'date', required: true },
        { key: 'bands', label: 'Bands', type: 'textarea', required: true, wide: true },
        { key: 'aids_levy_pct', label: 'AIDS levy (% of PAYE)', type: 'number', required: true },
        { key: 'nssa_employee_pct', label: 'NSSA worker %', type: 'number', required: true }, { key: 'nssa_employer_pct', label: 'NSSA farm %', type: 'number', required: true },
        { key: 'nssa_ceiling', label: 'NSSA monthly ceiling', type: 'number' }, { key: 'working_days', label: 'Working days a month', type: 'number' },
        { key: 'source', label: 'Source', wide: true, placeholder: 'e.g. ZIMRA PAYE tables January 2027' }],
        values: { currency: 'USD', ...base, effective_from: todayISO(), bands: (base.bands || []).map((b) => `${b[0] ?? ''}, ${b[1]}, ${b[2]}`).join('\n'), source: '' },
        onSubmit: async (d) => {
          const bands = d.bands.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => l.split(',').map((x) => x.trim()));
          await API.post(L('rules/'), { ...d, bands: bands.map(([a, b, cc]) => [a === '' ? null : a, b, cc || 0]) }); toast('New table saved'); route();
        } });
    },
  };
  el.querySelectorAll('[data-act]').forEach((b) => b.onclick = () => actions[b.dataset.act]());
  if ($('#wk-day')) $('#wk-day').onchange = (e) => { store.set('murimi-erp-workday', e.target.value || todayISO()); route(); };
  if ($('#wk-arch')) $('#wk-arch').onchange = (e) => { store.set('murimi-erp-wk-arch', e.target.checked ? '1' : '0'); route(); };
  el.querySelectorAll('[data-del-work]').forEach((b) => b.onclick = () => confirmBox('Remove this work record?', 'Remove',
    async () => { await API.del(L(`work/${b.dataset.delWork}/`)); toast('Removed'); route(); }));
  el.querySelectorAll('[data-edit-worker]').forEach((b) => b.onclick = () => {
    const w = R.listed.find((x) => x.id === b.dataset.editWorker);
    openForm({ title: `Edit ${w.full_name}`, fields: workerFields(R), values: w, onSubmit: async (d) => { await API.patch(L(`workers/${w.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-arch-worker]').forEach((b) => b.onclick = () => confirmBox('Mark this person as no longer working here? Their records and payslips are kept.', 'Yes, they left',
    async () => { await API.del(L(`workers/${b.dataset.archWorker}/`)); toast('Done'); route(); }));
  el.querySelectorAll('[data-edit-task]').forEach((b) => b.onclick = () => {
    const t = R.tasks.find((x) => x.id === b.dataset.editTask);
    openForm({ title: `Edit ${t.name}`, fields: [{ key: 'name', label: 'Task', required: true, wide: true }, { key: 'unit', label: 'Paid per', required: true },
      { key: 'rate', label: 'Pay for one', type: 'number', required: true }, { key: 'currency', label: 'Currency', type: 'select', required: true, options: [['USD', 'US$'], ['ZWG', 'ZiG']] }],
      values: t, onSubmit: async (d) => { await API.patch(L(`tasks/${t.id}/`), d); toast('Saved'); route(); } });
  });
  el.querySelectorAll('[data-arch-task]').forEach((b) => b.onclick = () => confirmBox('Remove this piece rate? Past work keeps its pay.', 'Remove',
    async () => { await API.del(L(`tasks/${b.dataset.archTask}/`)); toast('Removed'); route(); }));
  el.querySelectorAll('[data-del-adv]').forEach((b) => b.onclick = () => confirmBox('Remove this advance?', 'Remove',
    async () => { await API.del(L(`advances/${b.dataset.delAdv}/`)); toast('Removed'); route(); }));
};

/* ---------------- stage 4: sales, purchases, VAT, harvests, reports ---------------- */
const T = (p) => O('trade/' + p);
const round2 = (x) => Math.round((x + Number.EPSILON) * 100) / 100;
async function tradeRefs(kind) {
  const [opts, contacts, items, stores, onhand, seasons] = await Promise.all([API.get(T('options/')),
    API.get(T('contacts/'), { kind: kind === 'sale' ? 'customer' : 'supplier' }), API.get(O('items/')), API.get(O('stores/')),
    kind === 'sale' ? API.get(O('stock/')) : Promise.resolve({ results: [] }), API.get(O('seasons/'))]);
  const open = seasons.results.filter((s) => s.status !== 'closed');
  const season = open.find((s) => s.status === 'active') || open[0];
  const plans = season ? (await API.get(O('crop-plans/'), { season: season.id })).results.filter((p) => p.status !== 'abandoned') : [];
  return { opts, can: opts.can, contacts: contacts.results, items: items.results, stores: stores.results, onhand: onhand.results, plans, season };
}

function lineHTML(kind, R, ln = {}) {
  const sel = (name, opts, v, empty) => `<select data-k="${name}">${empty ? `<option value="">${empty}</option>` : ''}${opts.map(([a, b]) => `<option value="${esc(a)}"${String(v ?? '') === String(a) ? ' selected' : ''}>${esc(b)}</option>`).join('')}</select>`;
  const stockPick = kind === 'sale'
    ? sel('stockpair', R.onhand.map((r) => [`${r.item}|${r.store}`, `${r.item_name} from ${r.store_name} (${num(r.qty, 0)} ${r.unit})`]), ln.item ? `${ln.item}|${ln.store}` : '', 'Not from stock')
    : `${sel('item', R.items.map((i) => [i.id, `${i.name} (${i.unit})`]), ln.item, 'Not a stock item')}${sel('store', R.stores.map((s) => [s.id, s.name]), ln.store, 'Into store…')}`;
  return `<div class="dl ${kind}${R.opts.vat_registered ? '' : ' novat'}">
    <div class="f dl-desc"><label>What</label><input data-k="description" value="${esc(ln.description || '')}" placeholder="${kind === 'sale' ? 'e.g. Maize grain, grade A' : 'e.g. Compound D, tractor service'}"></div>
    ${kind === 'purchase' ? `<div class="f dl-cat"><label>For</label>${sel('category', R.opts.purchase_categories.map((c) => [c.value, c.label]), ln.category || 'other')}</div>` : ''}
    <div class="f dl-qty"><label>Qty</label><input data-k="quantity" inputmode="decimal" value="${esc(ln.quantity ?? '1')}"></div>
    <div class="f dl-unit"><label>Unit</label><input data-k="unit" value="${esc(ln.unit || '')}" placeholder="tonne, bag, hour"></div>
    <div class="f dl-price"><label>Price each</label><input data-k="unit_price" inputmode="decimal" value="${esc(ln.unit_price ?? '')}"></div>
    ${R.opts.vat_registered ? `<div class="f dl-vat"><label>VAT</label>${sel('vat_category', R.opts.vat_categories.map((c) => [c.value, c.value === 'standard' ? `${num(R.opts.vat_pct_today, 1)}%` : c.label]), ln.vat_category || 'standard')}</div>` : ''}
    <div class="f dl-crop"><label>Crop</label>${sel('crop_plan', R.plans.map((p) => [p.id, `${p.crop}, ${p.field_name}`]), ln.crop_plan, '—')}</div>
    <div class="f dl-stock"><label>${kind === 'sale' ? 'Take from stock' : 'Put into stock'}</label><div class="dl-pair">${stockPick}</div></div>
    <div class="dl-sum mono" data-sum></div>
    <button class="btn icon small" type="button" data-rm aria-label="Remove line">${ico('x')}</button></div>`;
}

function docEditor(kind, R, doc) {
  const sale = kind === 'sale';
  const who = sale ? 'customer' : 'supplier';
  const { root, close } = modalShell(doc ? `Edit invoice` : sale ? 'New sales invoice' : 'Record a purchase', `
    ${sale ? '' : '<p class="muted">Enter the supplier\'s invoice. Stock lines go straight into the store at the price without VAT.</p>'}
    <form class="grid" novalidate id="doc"><p class="form-error wide f" hidden style="grid-column:1/-1"></p>
      <div class="f"><label for="d-contact">${sale ? 'Customer' : 'Supplier'}</label><select id="d-contact">${R.contacts.map((c) => `<option value="${c.id}"${doc && doc.contact === c.id ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}<option value="__new"${R.contacts.length ? '' : ' selected'}>+ New ${who}</option></select></div>
      <div class="f" id="d-new-box"><label for="d-new">New ${who}'s name</label><input id="d-new" placeholder="${sale ? 'e.g. GMB Marondera' : 'e.g. Windmill (Pvt) Ltd'}"></div>
      <div class="f"><label for="d-date">Date</label><input id="d-date" type="date" value="${doc ? doc.date : todayISO()}" max="${todayISO()}"></div>
      ${sale ? `<div class="f"><label for="d-due">Pay by</label><input id="d-due" type="date" value="${doc ? doc.due_date : ''}"></div>`
        : '<div class="f"><label for="d-number">Supplier\'s invoice number</label><input id="d-number"></div>'}
      <div class="f"><label for="d-cur">Currency</label><select id="d-cur">${R.opts.currencies.map((c) => `<option${(doc ? doc.currency : S.org.reporting_currency) === c ? ' selected' : ''}>${c}</option>`).join('')}</select></div>
      <div class="f"><label for="d-ref">Reference <span class="muted small">(optional)</span></label><input id="d-ref" value="${esc(doc ? doc.reference : '')}" placeholder="Order or delivery note number"></div>
      ${!sale && R.opts.vat_registered ? `<div class="f"><label for="d-fis">Supplier's fiscal receipt number</label><input id="d-fis" placeholder="On their fiscal tax invoice"><span class="hint">Needed to claim the VAT back</span></div>
        <div class="f"><label for="d-fcode">Its verification code <span class="muted small">(optional)</span></label><input id="d-fcode"></div>` : ''}
      ${R.opts.vat_registered ? `<label class="f check wide"><input type="checkbox" id="d-incl"${doc && doc.prices_include_vat ? ' checked' : ''}><span>Prices already include VAT</span></label>`
        : '<p class="note wide" style="grid-column:1/-1">Your business has no VAT number in its details, so no VAT is added. If you are VAT-registered, add the number under Team → Business details.</p>'}
      <div class="lines" style="grid-column:1/-1" id="d-lines"></div>
      <div style="grid-column:1/-1;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center">
        <button class="btn small" type="button" id="d-add">${ico('plus')}Add a line</button><div class="doc-tot mono" id="d-tot"></div></div>
      ${sale ? '' : '<p class="note" style="grid-column:1/-1">Seed, fertiliser, chemicals and fuel: choose the stock item and store, and its cost counts against a crop when it is used. Other costs (contractors, repairs) can be linked to a crop directly.</p>'}
      <div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" type="submit">${sale ? 'Save draft' : 'Save purchase'}</button></div>
    </form>`, true);
  const box = $('#d-lines', root);
  const addLine = (ln) => { box.insertAdjacentHTML('beforeend', lineHTML(kind, R, ln)); wire(); };
  const pct = Number(R.opts.vat_pct_today) / 100;
  const read = () => [...box.querySelectorAll('.dl')].map((el) => {
    const g = (k) => { const x = el.querySelector(`[data-k="${k}"]`); return x ? x.value.trim() : ''; };
    const out = { description: g('description'), quantity: g('quantity'), unit: g('unit'), unit_price: g('unit_price'),
      vat_category: g('vat_category') || 'standard', crop_plan: g('crop_plan'), category: g('category') };
    if (sale) { const p = g('stockpair'); if (p) { const [item, store] = p.split('|'); Object.assign(out, { item, store }); } }
    else Object.assign(out, { item: g('item'), store: g('store') });
    return out;
  });
  const totals = () => {
    let net = 0, vat = 0; const incl = $('#d-incl', root)?.checked;
    box.querySelectorAll('.dl').forEach((el, i) => {
      const ln = read()[i]; const amt = round2(Number(ln.quantity || 0) * Number(ln.unit_price || 0));
      let n = amt, v = 0;
      if (R.opts.vat_registered && ln.vat_category === 'standard') { if (incl) { n = round2(amt / (1 + pct)); v = round2(amt - n); } else v = round2(amt * pct); }
      net += n; vat += v; $('[data-sum]', el).textContent = num(n + v);
    });
    const c = $('#d-cur', root).value;
    $('#d-tot', root).innerHTML = `${R.opts.vat_registered ? `Before VAT ${num(net)} · VAT ${num(vat)} · ` : ''}<b>Total ${money(round2(net + vat), c)}</b>`;
  };
  function wire() {
    box.querySelectorAll('.dl').forEach((el) => {
      el.querySelectorAll('input,select').forEach((x) => { x.oninput = totals; x.onchange = totals; });
      $('[data-rm]', el).onclick = () => { if (box.querySelectorAll('.dl').length > 1) { el.remove(); totals(); } };
      const sp = el.querySelector('[data-k="stockpair"]'), it = el.querySelector('[data-k="item"]');
      const fillFrom = (itemId) => { const i = R.items.find((x) => x.id === itemId); if (!i) return;
        const d = el.querySelector('[data-k="description"]'), u = el.querySelector('[data-k="unit"]');
        if (!d.value) d.value = i.name; u.value = i.unit; if (!sale) el.querySelector('[data-k="category"]').value = ['fuel'].includes(i.category) ? 'fuel' : ['seed', 'fertiliser', 'chemical'].includes(i.category) ? 'inputs' : el.querySelector('[data-k="category"]').value; };
      if (sp) sp.addEventListener('change', () => fillFrom(sp.value.split('|')[0]));
      if (it) it.addEventListener('change', () => fillFrom(it.value));
    });
  }
  (doc ? doc.lines : [{}]).forEach(addLine);
  totals();
  const newBox = $('#d-new-box', root);
  const syncNew = () => { newBox.hidden = $('#d-contact', root).value !== '__new'; };
  $('#d-contact', root).onchange = syncNew; syncNew();
  $('#d-add', root).onclick = () => { addLine({}); totals(); };
  ['#d-incl', '#d-cur'].forEach((s) => { if ($(s, root)) $(s, root).onchange = totals; });
  $('#doc', root).addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('.form-error', root); err.hidden = true;
    const btn = $('button[type=submit]', root); btn.disabled = true;
    try {
      let contact = $('#d-contact', root).value;
      if (contact === '__new') {
        const name = $('#d-new', root).value.trim();
        if (!name) throw new Error(`Write the new ${who}'s name.`);
        contact = (await API.post(T('contacts/'), { name, kind: sale ? 'customer' : 'supplier' })).id;
      }
      const data = { kind, contact, date: $('#d-date', root).value, currency: $('#d-cur', root).value, reference: $('#d-ref', root).value.trim(),
        prices_include_vat: !!$('#d-incl', root)?.checked, lines: read() };
      if (sale) data.due_date = $('#d-due', root).value;
      else { data.number = $('#d-number', root).value.trim(); data.fiscal_receipt_no = $('#d-fis', root)?.value.trim() || ''; data.fiscal_code = $('#d-fcode', root)?.value.trim() || ''; }
      const r = doc ? await API.patch(T(`documents/${doc.id}/`), data) : await API.post(T('documents/'), { ...data, id: uuid() });
      close(); toast(sale ? 'Invoice saved' : 'Purchase recorded');
      location.hash = `#/${sale ? 'sales' : 'purchases'}/view/${r.id}`; route();
    } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
  });
}

async function docView(el, id, kind, R) {
  const d = await API.get(T(`documents/${id}/`));
  const sale = d.kind === 'sale';
  const c = R.can, base = sale ? 'sales' : 'purchases';
  const live = d.status === 'issued';
  el.innerHTML = `${head(sale ? (d.number || 'Draft invoice') : `Purchase ${d.number || ''}`, `${esc(d.contact_name)} · ${fmtDate(d.date)} · <span class="chip${d.status === 'issued' ? ' green' : d.status === 'void' ? ' warn' : ''}">${esc(d.status_label)}</span>${d.overdue ? ' <span class="chip warn">Overdue</span>' : ''}`,
    `<a class="btn" href="#/${base}">${ico('undo')}Back</a>
     ${sale && d.status === 'draft' && c.edit_sales ? `<button class="btn" id="dv-edit">${ico('edit')}Edit</button><button class="btn primary" id="dv-issue">${ico('check')}Issue invoice</button>` : ''}
     ${live && Number(d.balance) > 0 && (sale ? c.edit_sales : c.edit_purchases) ? `<button class="btn primary" id="dv-pay">${ico('wallet')}${sale ? 'Record payment' : 'Record what we paid'}</button>` : ''}
     ${live && d.fiscal_status === 'none' ? `<button class="btn" id="dv-fiscal">${ico('check')}${sale ? 'Add fiscal receipt' : 'Add their fiscal receipt'}</button>` : ''}
     ${d.status !== 'void' ? `<button class="btn" onclick="window.print()">Print</button>` : ''}
     ${d.status !== 'void' && d.status !== 'draft' && c.void_trade ? `<button class="btn danger" id="dv-void">${ico('x')}Void</button>` : ''}
     ${d.status === 'draft' && c.edit_sales ? `<button class="btn danger" id="dv-del">${ico('x')}Delete draft</button>` : ''}`)}
    ${d.status === 'void' ? `<p class="note warn">${ico('warn')}Void: ${esc(d.void_reason)}</p>` : ''}
    ${sale && live && d.fiscal_status === 'none' ? `<p class="note warn no-print">${ico('warn')}Not fiscalised yet. Put this sale through your fiscal device and add its receipt number, so the invoice is a valid fiscal tax invoice.</p>` : ''}
    <section class="panel invoice">
      <div class="inv-top"><div><h2>${esc(S.org.trading_name || S.org.name)}</h2><div class="small muted">${esc([S.org.district, S.org.province].filter(Boolean).join(', '))}${S.org.phone ? ' · ' + esc(S.org.phone) : ''}</div>
          <div class="small">${S.org.tin ? 'TIN ' + esc(S.org.tin) : ''}${S.org.vat_number ? ' · VAT ' + esc(S.org.vat_number) : ''}</div></div>
        <div class="inv-title"><b>${sale ? (d.vat_registered ? 'TAX INVOICE' : 'INVOICE') : 'PURCHASE'}</b><div class="mono">${esc(d.number || 'DRAFT')}</div><div class="small muted">${fmtDate(d.date)}${d.due_date ? ' · pay by ' + fmtDate(d.due_date) : ''}</div></div></div>
      <div class="inv-to"><span class="small muted">${sale ? 'To' : 'From'}</span><b>${esc(d.contact_detail.name)}</b><span class="small">${esc([d.contact_detail.address, d.contact_detail.tin && 'TIN ' + d.contact_detail.tin, d.contact_detail.vat_number && 'VAT ' + d.contact_detail.vat_number].filter(Boolean).join(' · '))}</span>${d.reference ? `<span class="small">Ref: ${esc(d.reference)}</span>` : ''}</div>
      <div class="table-wrap"><table><thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Price</th>${d.vat_registered ? '<th>VAT</th><th class="num">Before VAT</th><th class="num">VAT</th>' : ''}<th class="num">Amount</th></tr></thead><tbody>
        ${d.lines.map((l) => `<tr><td>${esc(l.description)}<div class="small muted">${esc([l.category_label, l.crop && 'Crop: ' + l.crop].filter(Boolean).join(' · '))}</div></td><td class="num mono">${num(l.quantity, Number(l.quantity) % 1 ? 2 : 0)} ${esc(l.unit)}</td><td class="num mono">${num(l.unit_price)}</td>
          ${d.vat_registered ? `<td class="small">${l.vat_category === 'standard' ? num(l.vat_pct, 1) + '%' : esc(l.vat_label)}</td><td class="num mono">${num(l.net)}</td><td class="num mono">${num(l.vat)}</td>` : ''}<td class="num mono">${num(l.total)}</td></tr>`).join('')}
      </tbody></table></div>
      <div class="inv-tot">${d.vat_registered ? `<div><span>Before VAT</span><span class="mono">${num(d.net)}</span></div><div><span>VAT</span><span class="mono">${num(d.vat)}</span></div>` : ''}
        <div class="grand"><span>Total</span><span class="mono">${money(d.total, d.currency)}</span></div>
        ${d.currency !== 'USD' ? `<div class="small muted"><span>= US$ ${num(d.total_usd)} at 1 USD = ${num(d.units_per_usd, 4)} ${d.currency}</span></div>` : ''}
        ${Number(d.paid) ? `<div><span>Paid</span><span class="mono">${num(d.paid)}</span></div><div class="grand"><span>Still owed</span><span class="mono">${money(d.balance, d.currency)}</span></div>` : ''}</div>
      <div class="inv-fiscal small">${d.fiscal_status !== 'none' ? `Fiscal receipt <b class="mono">${esc(d.fiscal_receipt_no)}</b>${d.fiscal_code ? ` · verification <span class="mono">${esc(d.fiscal_code)}</span>` : ''}${d.fiscal_device ? ` · device ${esc(d.fiscal_device)}` : ''}` : sale && d.vat_registered ? 'Not yet fiscalised.' : ''}</div>
    </section>
    ${d.payments.length ? `<section class="panel no-print"><h2>Payments</h2><div class="table-wrap"><table><tbody>${d.payments.map((p) => `<tr><td class="small">${fmtDate(p.date)}</td><td>${esc(p.method_label)}${p.reference ? ` · ${esc(p.reference)}` : ''}</td><td class="num mono">${money(p.amount, d.currency)}</td>
      <td>${c.void_trade ? `<button class="btn small danger" data-del-pay="${p.id}" aria-label="Remove">${ico('x')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div></section>` : ''}`;
  const on = (sel, fn) => { const b = $(sel); if (b) b.onclick = fn; };
  on('#dv-edit', () => docEditor('sale', R, d));
  on('#dv-issue', () => confirmBox(`Issue this invoice for <b>${money(d.total, d.currency)}</b>? It gets the next invoice number, stock lines leave the store, and it can no longer be edited.`, 'Issue',
    async () => { await API.post(T(`documents/${d.id}/issue/`), {}); toast('Invoice issued'); route(); }));
  on('#dv-pay', () => openForm({ title: sale ? 'Record a payment' : 'Record what we paid', fields: [
    { key: 'amount', label: `Amount (${d.currency})`, type: 'number', required: true }, { key: 'date', label: 'Date', type: 'date', required: true },
    { key: 'method', label: 'How', type: 'select', required: true, options: R.opts.methods.map((x) => [x.value, x.label]) }, { key: 'reference', label: 'Reference' }],
    values: { amount: d.balance, date: todayISO(), method: 'bank' }, onSubmit: async (x) => { await API.post(T(`documents/${d.id}/payments/`), x); toast('Payment saved'); route(); } }));
  on('#dv-fiscal', () => openForm({ title: sale ? 'Fiscal receipt' : "Supplier's fiscal receipt", intro: sale ? 'After putting this sale through your fiscal device, copy the receipt number and verification code it prints.' : 'Copy these from the fiscal tax invoice the supplier gave you. Without them the VAT cannot be claimed back.', fields: [
    { key: 'fiscal_receipt_no', label: 'Fiscal receipt / invoice number', required: true, wide: true }, { key: 'fiscal_code', label: 'Verification code', wide: true },
    ...(sale ? [{ key: 'fiscal_device', label: 'Fiscal device ID' }] : [])], submitLabel: 'Save',
    onSubmit: async (x) => { await API.post(T(`documents/${d.id}/fiscalise/`), x); toast('Saved'); route(); } }));
  on('#dv-void', () => openForm({ title: 'Void this document', intro: d.fiscal_status !== 'none' && sale ? 'This invoice was fiscalised: put a credit note through your fiscal device first, then write "Credit note" and its number below.' : 'It stays on record marked void, and any stock goes back.',
    fields: [{ key: 'reason', label: 'Reason', required: true, wide: true }], submitLabel: 'Void',
    onSubmit: async (x) => { await API.post(T(`documents/${d.id}/void/`), x); toast('Voided'); route(); } }));
  on('#dv-del', () => confirmBox('Delete this draft?', 'Delete', async () => { await API.del(T(`documents/${d.id}/`)); location.hash = `#/${base}`; }));
  el.querySelectorAll('[data-del-pay]').forEach((b) => b.onclick = () => confirmBox('Remove this payment?', 'Remove', async () => { await API.del(T(`payments/${b.dataset.delPay}/`)); toast('Removed'); route(); }));
}

const contactFields = (kind) => [
  { key: 'name', label: 'Name', required: true, wide: true }, { key: 'kind', label: 'Type', type: 'select', required: true, options: [['customer', 'Customer'], ['supplier', 'Supplier'], ['both', 'Both']] },
  { key: 'phone', label: 'Phone', type: 'tel' }, { key: 'tin', label: 'TIN' }, { key: 'vat_number', label: 'VAT number' },
  { key: 'email', label: 'Email' }, { key: 'address', label: 'Address', wide: true }, { key: 'notes', label: 'Notes', type: 'textarea', wide: true }];

async function tradePage(el, kind) {
  const sale = kind === 'sale', base = sale ? 'sales' : 'purchases';
  const parts = location.hash.split('/');
  const R = await tradeRefs(kind);
  if (parts[2] === 'view' && parts[3]) return docView(el, parts[3], kind, R);
  const tab = parts[2] === 'contacts' ? 'contacts' : 'list';
  const canEdit = sale ? R.can.edit_sales : R.can.edit_purchases;
  let body;
  if (tab === 'list') {
    const filt = store.get('murimi-erp-filter-' + kind) || 'all';
    const q = filt === 'unpaid' ? { unpaid: '1' } : filt === 'all' ? {} : { status: filt };
    const docs = (await API.get(T('documents/'), { kind, ...q })).results;
    const owed = docs.filter((d) => d.status === 'issued').reduce((t, d) => t + Number(d.balance) / Number(d.units_per_usd), 0);
    body = `<section class="panel"><div class="panel-head"><div class="seg">${[['all', 'All'], ...(sale ? [['draft', 'Drafts']] : []), ['unpaid', sale ? 'Not paid' : 'We still owe'], ['void', 'Void']].map(([v, l]) => `<button data-filt="${v}" aria-current="${v === filt}">${l}</button>`).join('')}</div>
        ${filt !== 'void' ? `<div class="small muted">${sale ? 'Customers owe you' : 'You owe suppliers'} <b class="mono">${money(owed, 'USD')}</b> (in US$)</div>` : ''}</div>
      ${docs.length ? `<div class="table-wrap"><table><thead><tr><th>${sale ? 'Invoice' : 'Their invoice'}</th><th>${sale ? 'Customer' : 'Supplier'}</th><th>Date</th><th class="num">Total</th><th class="num">Owed</th><th>Status</th><th></th></tr></thead><tbody>
        ${docs.map((d) => `<tr class="${d.status === 'void' ? 'reversed' : ''}"><td class="mono"><b>${esc(d.number || 'Draft')}</b></td><td>${esc(d.contact_name)}</td><td class="small">${fmtDate(d.date)}</td>
          <td class="num mono">${money(d.total, d.currency)}</td><td class="num mono">${d.status === 'issued' && Number(d.balance) ? money(d.balance, d.currency) : '—'}</td>
          <td><span class="chip${d.status === 'issued' ? ' green' : d.status === 'void' ? ' warn' : ''}">${esc(d.status_label)}</span>${d.overdue ? ' <span class="chip warn">Overdue</span>' : ''}${d.status === 'issued' && d.fiscal_status === 'none' && (sale || d.vat !== '0.00') ? ` <span class="chip" title="No fiscal receipt yet">No fiscal no.</span>` : ''}</td>
          <td><a class="btn small" href="#/${base}/view/${d.id}">Open</a></td></tr>`).join('')}</tbody></table></div>`
        : `<div class="empty">${ico(sale ? 'swap' : 'box')}<p>${sale ? 'No invoices here yet.' : 'No purchases here yet.'}</p></div>`}</section>`;
  } else {
    const all = (await API.get(T('contacts/'))).results.filter((c) => c.kind === 'both' || c.kind === (sale ? 'customer' : 'supplier'));
    body = `<section class="panel"><div class="panel-head"><h2>${sale ? 'Customers' : 'Suppliers'}</h2>${canEdit ? `<button class="btn small primary" id="add-contact">${ico('plus')}Add</button>` : ''}</div>
      ${all.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Phone</th><th>TIN / VAT</th><th></th></tr></thead><tbody>${all.map((x) => `<tr><td><b>${esc(x.name)}</b><div class="small muted">${esc(x.address)}</div></td><td class="small">${esc(x.phone || '—')}</td>
        <td class="small mono">${esc([x.tin, x.vat_number].filter(Boolean).join(' / ') || '—')}</td><td>${canEdit ? `<div class="row-actions"><button class="btn small" data-edit-c="${x.id}">${ico('edit')}</button></div>` : ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="muted">None yet.</p>'}</section>`;
    R.allContacts = all;
  }
  el.innerHTML = `${head(sale ? 'Sales' : 'Purchases', sale ? 'Invoices to your customers, what they still owe, and fiscal receipt numbers. Linking a sale to a crop shows that crop\'s profit.'
      : 'Supplier invoices: inputs go into stock, other costs can be linked to a crop. VAT is claimable only with the supplier\'s fiscal tax invoice.',
    canEdit ? `<button class="btn primary" id="new-doc">${ico('plus')}${sale ? 'New invoice' : 'Record a purchase'}</button>` : '')}
    <div class="seg" role="tablist"><a href="#/${base}" aria-current="${tab === 'list'}">${sale ? 'Invoices' : 'Purchases'}</a><a href="#/${base}/contacts" aria-current="${tab === 'contacts'}">${sale ? 'Customers' : 'Suppliers'}</a></div>${body}`;
  if ($('#new-doc')) $('#new-doc').onclick = () => docEditor(kind, R);
  el.querySelectorAll('[data-filt]').forEach((b) => b.onclick = () => { store.set('murimi-erp-filter-' + kind, b.dataset.filt); route(); });
  if ($('#add-contact')) $('#add-contact').onclick = () => openForm({ title: `Add a ${sale ? 'customer' : 'supplier'}`, fields: contactFields(), values: { kind: sale ? 'customer' : 'supplier' },
    onSubmit: async (d) => { await API.post(T('contacts/'), d); toast('Added'); route(); } });
  el.querySelectorAll('[data-edit-c]').forEach((b) => b.onclick = () => { const x = R.allContacts.find((y) => y.id === b.dataset.editC);
    openForm({ title: `Edit ${x.name}`, fields: contactFields(), values: x, onSubmit: async (d) => { await API.patch(T(`contacts/${x.id}/`), d); toast('Saved'); route(); } }); });
}
PAGES.sales = (el) => tradePage(el, 'sale');
PAGES.purchases = (el) => tradePage(el, 'purchase');

function harvestForm(plans, items, stores, preset = {}) {
  openForm({ title: 'Record a harvest', intro: 'How much came off the field. If it goes into a store (a grain shed, a tobacco barn), choose the stock item too, so you can sell it from stock later.', fields: [
    { key: 'crop_plan', label: 'Crop', type: 'select', required: true, wide: true, options: plans.map((p) => [p.id, `${p.crop} on ${p.farm_name} / ${p.field_name}`]) },
    { key: 'date', label: 'Date', type: 'date', required: true }, { key: 'tonnes', label: 'Tonnes', type: 'number', required: true, hint: 'e.g. 45.5' },
    { key: 'grade', label: 'Grade', placeholder: 'e.g. A, Export' },
    { key: 'item', label: 'Put into stock as', type: 'select', options: items.map((i) => [i.id, `${i.name} (${i.unit})`]) },
    { key: 'store', label: 'Store', type: 'select', options: stores.map((s) => [s.id, s.name]) },
    { key: 'quantity', label: 'Quantity in the item\'s unit', type: 'number', hint: 'Worked out for you if the item is in tonnes, kg or bags' },
    { key: 'note', label: 'Note', wide: true }], values: { date: todayISO(), ...preset }, submitLabel: 'Save',
    onSubmit: async (d) => { await API.post(T('harvests/'), d); toast('Harvest saved'); route(); } });
}

PAGES.reports = async (el) => {
  const tab = location.hash.split('/')[2] || 'crops';
  const tabs = [['crops', 'Crops: cost and profit'], ['profit', 'Profit and loss'], ['vat', 'VAT'], ['owing', 'Who owes what']];
  const rng = { date_from: store.get('murimi-erp-rfrom') || `${todayISO().slice(0, 4)}-01-01`, date_to: store.get('murimi-erp-rto') || todayISO() };
  const range = `<div class="range no-print"><div class="f"><label for="r-from">From</label><input type="date" id="r-from" value="${rng.date_from}"></div><div class="f"><label for="r-to">To</label><input type="date" id="r-to" value="${rng.date_to}"></div></div>`;
  let body = '';
  if (tab === 'crops') {
    const sid = store.get('murimi-erp-season-' + S.org.id);
    const r = await API.get(T('reports/crops/'), sid ? { season: sid } : {});
    if (!r.season) body = `<section class="panel"><div class="empty">${ico('calendar')}<p>Add a season and crop plans first.</p></div></section>`;
    else {
      const t = r.totals;
      body = `<section class="panel"><div class="panel-head"><div class="f no-print" style="min-width:220px"><label for="r-season">Season</label><select id="r-season">${r.seasons.map((s) => `<option value="${s.id}"${s.id === r.season.id ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></div>
          <div class="small muted">All figures in US$. Costs: stock used, labour and other costs linked to each crop.</div></div>
        <div class="stats"><div class="stat"><span class="n">${money(t.revenue_usd, 'USD')}</span><span class="l">Sales</span></div><div class="stat"><span class="n">${money(t.cost_usd, 'USD')}</span><span class="l">Costs: inputs ${num(t.inputs_usd, 0)}, labour ${num(t.labour_usd, 0)}, other ${num(t.other_usd, 0)}</span></div>
          <div class="stat"><span class="n${Number(t.profit_usd) < 0 ? ' over' : ''}">${money(t.profit_usd, 'USD')}</span><span class="l">Profit so far</span></div><div class="stat"><span class="n">${num(t.harvested_t, 1)} t</span><span class="l">Harvested from ${num(t.ha, 1)} ha</span></div></div>
        <div class="table-wrap"><table><thead><tr><th>Crop</th><th class="num">Ha</th><th class="num">Yield t/ha</th><th class="num">Cost</th><th class="num">Cost / ha</th><th class="num">Cost / t</th><th class="num">Sales</th><th class="num">Profit</th><th class="num">Profit / ha</th></tr></thead><tbody>
          ${r.plans.map((p) => `<tr><td><b>${esc(p.crop)}</b><div class="small muted">${esc(p.farm)} / ${esc(p.field)}</div></td><td class="num mono">${num(p.ha, 1)}</td>
            <td class="num mono">${Number(p.harvested_t) ? num(p.t_per_ha, 2) : '—'}${p.target_t_per_ha ? `<div class="small muted">target ${num(p.target_t_per_ha, 1)}</div>` : ''}</td>
            <td class="num mono">${num(p.cost_usd)}${p.budget_usd ? `<div class="small ${Number(p.cost_usd) > Number(p.budget_usd) ? 'over' : 'muted'}">${Math.round(Number(p.cost_usd) / Number(p.budget_usd) * 100)}% of budget</div>` : ''}</td>
            <td class="num mono">${num(p.cost_per_ha)}</td><td class="num mono">${p.cost_per_t ? num(p.cost_per_t) : '—'}</td><td class="num mono">${num(p.revenue_usd)}</td>
            <td class="num mono${Number(p.profit_usd) < 0 ? ' over' : ''}">${num(p.profit_usd)}</td><td class="num mono${Number(p.profit_per_ha) < 0 ? ' over' : ''}">${num(p.profit_per_ha)}</td></tr>`).join('')}</tbody></table></div>
        <p class="note">Stock bought counts against a crop when it is used on it (Stock → Record use). Harvests come from Seasons → Record harvest; sales from invoice lines linked to the crop.</p></section>`;
    }
  } else if (tab === 'profit') {
    const r = await API.get(T('reports/profit/'), rng);
    const max = Math.max(1, ...r.months.flatMap((x) => [Number(x.income), Number(x.costs)]));
    body = `${range}<div class="stats" style="grid-template-columns:repeat(3,1fr)"><div class="stat"><span class="n">${money(r.total_income, 'USD')}</span><span class="l">Income (before VAT)</span></div>
        <div class="stat"><span class="n">${money(r.total_costs, 'USD')}</span><span class="l">Costs</span></div><div class="stat"><span class="n${Number(r.profit) < 0 ? ' over' : ''}">${money(r.profit, 'USD')}</span><span class="l">${Number(r.profit) < 0 ? 'Loss' : 'Profit'}</span></div></div>
      <div class="grid2"><section class="panel"><h2>Income</h2><table><tbody>${r.income.map((x) => `<tr><td>${esc(x.label)}</td><td class="num mono">${num(x.usd)}</td></tr>`).join('') || '<tr><td class="muted">No sales in this period.</td></tr>'}
          <tr class="tot"><td>Total</td><td class="num mono">${num(r.total_income)}</td></tr></tbody></table></section>
        <section class="panel"><h2>Costs</h2><table><tbody>${r.costs.filter((x) => Number(x.usd)).map((x) => `<tr><td>${esc(x.label)}</td><td class="num mono">${num(x.usd)}</td></tr>`).join('') || '<tr><td class="muted">No costs in this period.</td></tr>'}
          <tr class="tot"><td>Total</td><td class="num mono">${num(r.total_costs)}</td></tr></tbody></table></section></div>
      ${r.months.length ? `<section class="panel"><h2>Month by month</h2><div class="legend small"><span><i class="sw in"></i>Income</span><span><i class="sw out"></i>Costs</span></div><div class="months">${r.months.map((x) => `<div class="mon"><div class="pair">
        <span class="b in" style="height:${Number(x.income) / max * 100}%" title="Income US$ ${num(x.income)}"></span><span class="b out" style="height:${Number(x.costs) / max * 100}%" title="Costs US$ ${num(x.costs)}"></span></div>
        <span class="small muted">${new Date(x.month + '-15').toLocaleDateString('en-GB', { month: 'short' })}</span></div>`).join('')}</div></section>` : ''}
      <p class="note">${esc(r.note)} Figures are in US$ using each record's own exchange rate. This is a management report; your accountant prepares the official accounts.</p>`;
  } else if (tab === 'vat') {
    const r = await API.get(T('reports/vat/'), rng);
    const curs = Object.entries(r.by_currency);
    body = `${range}${!r.vat_registered ? '<p class="note warn">Your business has no VAT number in its details. If you are not VAT-registered you charge no VAT. Registration is required once taxable sales pass US$25,000 in 12 months.</p>' : ''}
      ${curs.length ? curs.map(([cur, v]) => `<section class="panel"><div class="panel-head"><h2>VAT in ${cur}</h2><span class="small muted">${fmtDate(r.date_from)} – ${fmtDate(r.date_to)}${r.vat_number ? ' · VAT ' + esc(r.vat_number) : ''}</span></div>
        <div class="table-wrap"><table><tbody>
          <tr><th colspan="3">Sales</th></tr>
          <tr><td>Standard-rated sales</td><td class="num mono">${num(v.sales.standard.net)}</td><td class="num mono">VAT ${num(v.sales.standard.vat)}</td></tr>
          <tr><td>Zero-rated sales</td><td class="num mono">${num(v.sales.zero.net)}</td><td></td></tr>
          <tr><td>Exempt sales</td><td class="num mono">${num(v.sales.exempt.net)}</td><td></td></tr>
          <tr class="tot"><td>Output VAT</td><td></td><td class="num mono">${num(v.output_vat)}</td></tr>
          <tr><th colspan="3">Purchases</th></tr>
          <tr><td>Input VAT with a fiscal tax invoice (claimable)</td><td></td><td class="num mono">${num(v.input_claimable)}</td></tr>
          <tr><td>Input VAT without one (not claimable)</td><td></td><td class="num mono muted">${num(v.input_not_claimable)}</td></tr>
          <tr class="tot"><td>${Number(v.payable) >= 0 ? 'VAT to pay ZIMRA' : 'VAT refund due'}</td><td></td><td class="num mono">${money(Math.abs(Number(v.payable)), cur)}</td></tr>
        </tbody></table></div>
        ${v.missing_fiscal.length ? `<p class="note warn">${ico('warn')}${v.missing_fiscal.length} purchase${v.missing_fiscal.length > 1 ? 's have' : ' has'} VAT but no supplier fiscal receipt number: ${v.missing_fiscal.map((x) => `<a href="#/purchases/view/${x.id}">${esc(x.supplier)} ${esc(x.number)}</a>`).join(', ')}. Add the numbers to claim ${money(v.input_not_claimable, cur)}.</p>` : ''}
      </section>`).join('') : `<section class="panel"><div class="empty">${ico('swap')}<p>No sales or purchases in this period.</p></div></section>`}
      <p class="note">VAT is shown in the currency of each sale or purchase. Returns are due by the 25th of the month after your tax period. Most unprocessed farm produce has been exempt from VAT since 1 January 2026 (it used to be zero-rated): exempt sales carry no VAT, and VAT on costs for them can't be claimed. Have your accountant confirm the category of each product and file the return.</p>`;
  } else {
    const r = await API.get(T('reports/owing/'));
    const block = (title, b, link) => `<section class="panel"><div class="panel-head"><h2>${title}</h2><b class="mono">${money(b.total_usd, 'USD')}</b></div>
      <div class="aging">${['not due', '1-30', '31-60', '61-90', '90+'].map((k) => `<div><span class="small muted">${k === 'not due' ? 'Not due yet' : k + ' days late'}</span><b class="mono${k === '90+' && Number(b.buckets[k] || 0) ? ' over' : ''}">${num(b.buckets[k] || 0)}</b></div>`).join('')}</div>
      ${b.rows.length ? `<div class="table-wrap"><table><tbody>${b.rows.map((x) => `<tr><td><b>${esc(x.contact)}</b><div class="small muted mono">${esc(x.number || '')}</div></td><td class="small">${fmtDate(x.date)}</td>
        <td class="num mono">${money(x.balance, x.currency)}</td><td class="small${x.days_late > 60 ? ' over' : ''}">${x.days_late ? x.days_late + ' days late' : 'not due'}</td><td><a class="btn small" href="#/${link}/view/${x.id}">Open</a></td></tr>`).join('')}</tbody></table></div>` : '<p class="muted">Nothing owed.</p>'}</section>`;
    body = `<div class="grid2">${block('Customers owe you', r.customers_owe_us, 'sales')}${block('You owe suppliers', r.we_owe_suppliers, 'purchases')}</div>`;
  }
  el.innerHTML = `${head('Reports', 'How each crop is doing, profit and loss, VAT for your return, and who owes what. All in US$ unless shown.', '<button class="btn" onclick="window.print()">Print</button>')}
    <div class="seg no-print" role="tablist">${tabs.map(([k, l]) => `<a href="#/reports/${k}" aria-current="${k === tab}">${l}</a>`).join('')}</div>${body}`;
  const rs = () => { store.set('murimi-erp-rfrom', $('#r-from').value); store.set('murimi-erp-rto', $('#r-to').value); route(); };
  if ($('#r-from')) { $('#r-from').onchange = rs; $('#r-to').onchange = rs; }
  if ($('#r-season')) $('#r-season').onchange = (e) => { store.set('murimi-erp-season-' + S.org.id, e.target.value); route(); };
};


/* ---------------- stage 5: phone app and sync ---------------- */
PAGES.sync = async (el) => {
  const items = (await DB.all('outbox')).sort((a, b) => a.at - b.at);
  const waiting = items.filter((x) => !x.error), failed = items.filter((x) => x.error);
  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const apk = await fetch('/static/murimi-erp.apk', { method: 'HEAD' }).then((r) => r.ok).catch(() => false);
  const row = (x) => `<tr><td><b>${esc(x.label)}</b><div class="small muted">${esc(x.org || '')} · saved ${new Date(x.at).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</div>
    ${x.error ? `<div class="small over">${ico('warn')} Murimi refused it: ${esc(x.error)}</div>` : ''}</td>
    <td><div class="row-actions">${x.error ? `<button class="btn small" data-retry="${x.id}">Try again</button>` : ''}<button class="btn small danger" data-discard="${x.id}" aria-label="Discard">${ico('x')}</button></div></td></tr>`;
  el.innerHTML = `${head('Phone & sync', 'Use Murimi on a phone in the field, even with no network. Records wait on the phone and are sent when there is signal.',
      `<button class="btn primary" id="sync-now"${items.length ? '' : ' disabled'}>${ico('cloud')}Send now</button>`)}
    <div class="stats" style="grid-template-columns:repeat(3,1fr)">
      <div class="stat"><span class="n">${Sync.state === 'offline' ? 'Offline' : 'Online'}</span><span class="l">${Sync.state === 'offline' ? 'No network right now' : 'Connected to Murimi'}</span></div>
      <div class="stat"><span class="n">${waiting.length}</span><span class="l">Records waiting to send</span></div>
      <div class="stat"><span class="n${failed.length ? ' over' : ''}">${failed.length}</span><span class="l">Refused, need you</span></div></div>
    ${Sync.needLogin ? `<p class="note warn">${ico('warn')}Murimi needs you to log in again before it can send. Your records are safe on this phone: <a href="#" id="relogin">log out and back in</a> once you have network.</p>` : ''}
    <section class="panel"><h2>On this phone, not sent yet</h2>
      ${items.length ? `<div class="table-wrap"><table><tbody>${[...failed, ...waiting].map(row).join('')}</tbody></table></div>`
        : `<div class="empty">${ico('check')}<p>Everything has been sent.</p></div>`}
      <p class="note">Works with no network: the work register, recording stock used, stock received, moves and stock takes, fuel fills, and harvests. Pages you have opened before can be read offline. Invoices, payroll and settings need the network.</p>
    </section>
    <section class="panel"><h2>Put Murimi on your phone</h2>
      ${standalone ? `<p class="note">${ico('check')} You're using the installed app.</p>` : ''}
      <div class="install">
        <div><h3>${ico('phone')} Android</h3>
          ${apk ? `<p>Download the Murimi app and open the file. Android will ask you to allow installing from this source: allow it once.</p><a class="btn primary" href="/static/murimi-erp.apk" download>Download the app (APK)</a>`
            : `<p>Open this page in Chrome on the phone, then use <b>Install app</b> below, or Chrome's menu ⋮ → <b>Add to Home screen</b>.</p>`}
          ${installPrompt ? '<button class="btn" id="install-now">Install app</button>' : ''}</div>
        <div><h3>${ico('phone')} iPhone</h3><p>Open this page in Safari, tap the Share button, then <b>Add to Home Screen</b>.</p></div>
        <div><h3>${ico('cloud')} Before going to the field</h3><p>Open the Labour, Stock, Fuel and Seasons pages once while you have signal. The phone keeps them, so the lists of workers, items and crops are there offline.</p></div>
      </div>
      ${!window.isSecureContext ? '<p class="note warn">This address is not secure (no https), so the phone cannot keep the app for offline use. Use the internet address of your Murimi server.</p>' : ''}
    </section>`;
  $('#sync-now').onclick = async () => { await Sync.flush(); route(); };
  if ($('#relogin')) $('#relogin').onclick = (e) => { e.preventDefault(); logout(); };
  if ($('#install-now')) $('#install-now').onclick = async () => { installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; route(); };
  el.querySelectorAll('[data-retry]').forEach((b) => b.onclick = async () => {
    const x = items.find((i) => i.id === b.dataset.retry); delete x.error; await DB.put('outbox', x); await Sync.flush(); route();
  });
  el.querySelectorAll('[data-discard]').forEach((b) => b.onclick = () => confirmBox('Throw away this record? It was never sent to Murimi.', 'Discard',
    async () => { await DB.del('outbox', b.dataset.discard); await Sync.refresh(); route(); }));
};

boot();
