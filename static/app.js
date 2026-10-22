'use strict';
/* TrapNote の画面（本体）: API、状態、一覧（サーバー側ページングの仮想リスト）、対象を開く、設定。再生は player.js、判定パネルは judge.js、窓は dialogs.js */

const $ = (sel) => document.querySelector(sel);
const PAGE = 200;

async function api(path, body, method) {
  const opt = { method: method || (body === undefined ? 'GET' : 'POST'), headers: {}, cache: 'no-store' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const err = new Error((data && data.error) || `${r.status} ${r.statusText}`); err.status = r.status; err.data = data; throw err; }
  if (data && data.error) throw new Error(data.error);
  return data;
}
function qs(obj) { return Object.entries(obj).filter(([k, v]) => v !== undefined && v !== null && v !== '' && v !== 'all').map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&'); }

const S = {
  snap: null, settings: {}, project: null, facets: null, species: { species: [], fixed: [] }, fields: { fields: [] },
  filter: { station: '', season: '', status: '', species: '', assignee: '', text: '', folder_prefix: '', favorite: '', det: '', flag: '', agree: '', exclude: '', boxes: '' }, sort: 'station', desc: false,
  picked: new Set(), selAnchor: -1,   // 一覧の複数選択（id の集合）と Shift の起点（並びの位置）
  treeOpen: {},
  total: 0, pages: new Map(), pageLoading: new Map(), listToken: 0,
  cur: null, curIndex: -1, detail: null, busy: false, overlayTimer: null, playToken: 0,
  sel: [], spFields: {}, itemFields: {}, dirty: false,
  jobsTimer: null, transcoding: new Set(),
};

// ---------- 小道具 ----------
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmtSec(s) { if (s == null || isNaN(s)) return '–'; return s.toFixed(2); }
function fmtDur(s) { if (s == null) return ''; const m = Math.floor(s / 60), r = s - m * 60; return m ? `${m}:${r.toFixed(1).padStart(4, '0')}` : `${r.toFixed(1)} s`; }
function spByCode(code) { return S.spMap ? S.spMap[code] : null; }
function spOrderKey(code) {   // 種リストの順（固定項目は後、要確認 (low) は最後）。表の行と列をそろえるため
  if (code === '(low)') return 1e9;
  const all = (S.species.species || []).concat(S.species.fixed || []);
  const i = all.findIndex(s => s.code === code);
  return i < 0 ? 1e8 : i;
}
function sortCodes(codes) { return [...codes].sort((a, b) => spOrderKey(a) - spOrderKey(b) || String(a).localeCompare(String(b))); }
function rebuildSpMap() { S.spMap = {}; (S.species.species || []).forEach(s => { S.spMap[s.code] = s; }); (S.species.fixed || []).forEach(s => { S.spMap[s.code] = s; }); }
function spLabel(code) { const s = spByCode(code); return s ? spName(s) : code; }

let toastTimer = null;
function toast(msg, kind) {
  const el = $('#toast');
  el.textContent = msg; el.className = kind === 'error' ? 'error' : '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), kind === 'error' ? 6000 : 2500);
}
function showModal(html) {
  $('#modalBody').innerHTML = html;
  $('#modalBack').classList.remove('hidden'); $('#modal').classList.remove('hidden');
  const first = $('#modal').querySelector('input:not([type=checkbox]), select, textarea, button');
  if (first) first.focus();
  return $('#modalBody');
}
function closeModal() { $('#modalBack').classList.add('hidden'); $('#modal').classList.add('hidden'); $('#modalBody').innerHTML = ''; }
function modalOpen() { return !$('#modal').classList.contains('hidden'); }
function confirmModal(title, body, okLabel, danger) {
  return new Promise(resolve => {
    const m = showModal(`<h3>${esc(title)}</h3><div>${body}</div>
      <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="${danger ? 'danger-outline' : 'primary'}">${esc(okLabel || t('ok'))}</button></div>`);
    m.querySelector('#mOk').onclick = () => { closeModal(); resolve(true); };
    m.querySelector('#mCancel').onclick = () => { closeModal(); resolve(false); };
    m.querySelector('#mOk').focus();
  });
}
function showOverlay(msg, sub) {
  clearTimeout(S.overlayTimer); S.overlayTimer = null;
  const o = $('#viewOverlay');
  o.innerHTML = `<div>${esc(msg)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}`; o.classList.remove('hidden');
}
function showOverlayLater(msg, sub, delayMs) { clearTimeout(S.overlayTimer); S.overlayTimer = setTimeout(() => { S.overlayTimer = null; showOverlay(msg, sub); }, delayMs ?? 400); }
function hideOverlay() { clearTimeout(S.overlayTimer); S.overlayTimer = null; $('#viewOverlay').classList.add('hidden'); }

// ---------- 状態 ----------
function setSnapshot(snap) {
  S.snap = snap; S.settings = snap.settings || {}; S.project = snap.project; S.facets = snap.facets || null;
  if (snap.species) S.species = snap.species;
  if (snap.fields) S.fields = snap.fields;
  rebuildSpMap();
  LANG = S.settings.language || 'ja';
  applyLang();
  $('#verInfo').textContent = 'v' + snap.version + (snap.build_time ? ' (' + String(snap.build_time).slice(5, 16).replace('T', ' ') + ')' : ''); $('#verInfo').title = snap.build_time ? 'build ' + snap.build_time : '';
  renderTarget(); renderFilters(); renderJobs(snap.jobs);
  if (typeof renderSpeciesPanel === 'function') renderSpeciesPanel();
}
async function refreshState() { setSnapshot(await api('/api/state')); }
async function refreshFacets() { if (!S.project) return; S.facets = await api('/api/project/facets'); renderFilters(); renderTarget(); renderCounts(); }
async function refreshCurrent() {   // 開いている対象の判定・検出だけ読み直す（再生はそのまま）
  if (!S.cur || S.busy) return;
  const id = S.cur.id;
  try {
    const d = await api(`/api/items/${id}`);
    if (!S.cur || S.cur.id !== id) return;
    const playing = S.cur.det_state === d.det_state && S.cur.status === d.status && S.cur.updated_at === d.updated_at;
    if (playing) return;
    S.cur = d; S.detail = d;
    renderItemHead(d); loadJudgement(d); loadBoxes(d);
  } catch (e) { /* */ }
}

function renderTarget() {
  const el = $('#targetLabel');
  if (!S.project || !S.project.root) { el.textContent = t('no_project'); return; }
  const f = S.facets || {};
  const c = f.counts || {};
  const parts = [`${esc(S.project.name)}`, `${esc(S.project.root)}`, `${f.total ?? 0} ${t('items')}`, `${t('verified')} ${c.verified || 0} / ${f.total ?? 0}`];
  if (f.missing) parts.push(`<span style="color:#c0392b">${t('missing')} ${f.missing}</span>`);
  if (f.meta_pending) parts.push(`${t('meta_pending')} ${f.meta_pending}`);
  if (f.corrected) parts.push(`⏱ ${f.corrected}`);
  if (S.snap && S.snap.conflicts_n) parts.push(`<span style="color:#c0392b">${t('conflicts_badge')} ${S.snap.conflicts_n}</span>`);
  el.innerHTML = parts.join(' · ');
}

// ---------- 絞り込み ----------
function fillSelect(sel, opts, value) {
  sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  sel.value = value; if (sel.value !== value) sel.value = opts.length ? opts[0][0] : '';
}
function renderFilters() {
  const f = S.facets || { stations: [], seasons: [], assignees: [], species_counts: {} };
  fillSelect($('#fStation'), [['', t('all_stations')]].concat((f.stations || []).map(s => [s, s])), S.filter.station);
  fillSelect($('#fSeason'), [['', t('all_seasons')]].concat((f.seasons || []).map(s => [s, s])), S.filter.season);
  $('#fSeason').classList.toggle('hidden', !(f.seasons || []).length);
  fillSelect($('#fStatus'), [['', t('all_status')], ['new', t('st_new')], ['auto', t('st_auto')], ['verified', t('st_verified')], ['unverified', t('st_unverified')]], S.filter.status);
  const sc = f.species_counts || {};
  const spOpts = [['', t('all_species')], ['none', t('sp_none')], ['any', t('sp_any')]];
  const all = (S.species.species || []).concat(S.species.fixed || []);
  all.filter(s => s.enabled || sc[s.code] || s.fixed).forEach(s => spOpts.push([s.code, `${spName(s)}${sc[s.code] ? ' (' + sc[s.code] + ')' : ''}`]));
  fillSelect($('#fSpecies'), spOpts, S.filter.species);
  const asg = f.assignees || [];
  $('#fAssignee').classList.toggle('hidden', !asg.length);
  fillSelect($('#fAssignee'), [['', t('all_assignees')]].concat(asg.map(a => [a, a])), S.filter.assignee);
  renderTree();
  $('#fFav').checked = !!S.filter.favorite;
  const low = f.low || 0, ag = f.agree || {};
  const cnt = n => n ? ` (${n})` : '';
  fillSelect($('#fDet'), [['', t('all_det')], ['low', `${t('det_low')}${cnt(low)}`],
      ['cmp:disagree', `${t('cmp_disagree')}${cnt(ag.mismatch)}`], ['cmp:partial', `${t('cmp_partial')}${cnt(ag.partial)}`], ['cmp:agree', `${t('cmp_agree')}${cnt(ag.match)}`], ['cmp:nohuman', t('cmp_nohuman')], ['cmp:any', `${t('cmp_any')}${cnt(ag.auto)}`], ['cmp:rechecked', `${t('cmp_rechecked')}${cnt(ag.rechecked)}`], ['excl', `${t('det_excluded')}${cnt(f.excluded)}`],
      ['box:0', t('det_box0')], ['box:1', t('det_box1')], ['box:2', t('det_box2')], ['box:3+', t('det_box3')],
      ['todo', t('det_todo')], ['done', t('det_done')], ['animal', t('det_animal')], ['person', t('det_person')], ['vehicle', t('det_vehicle')], ['none', t('det_none')], ['failed', t('det_failed')]],
    S.filter.flag === 'low' ? 'low' : S.filter.exclude ? 'excl' : S.filter.boxes ? 'box:' + S.filter.boxes : (S.filter.agree ? 'cmp:' + S.filter.agree : (S.filter.det || '')));
  fillSelect($('#sortSel'), [['station', t('sort_station')], ['datetime', t('sort_datetime')], ['name', t('sort_name')], ['path', t('sort_path')], ['status', t('sort_status')], ['updated', t('sort_updated')], ['confidence', t('sort_confidence')], ['boxes', t('sort_boxes')], ['id', t('sort_id')]], S.sort);
  $('#sortDir').textContent = S.desc ? '↑' : '↓';
}
// ---------- フォルダの木（Picasa 風。クリックでその中に絞り込む） ----------
function buildTree(folders) {
  const root = { name: '', path: '', n: 0, verified: 0, favorite: 0, children: new Map() };
  (folders || []).forEach(f => {
    const parts = (f.folder || '').split('/').filter(Boolean);
    let node = root;
    root.n += f.n; root.verified += f.verified || 0; root.favorite += f.favorite || 0;
    let path = '';
    parts.forEach(pn => {
      path = path ? path + '/' + pn : pn;
      if (!node.children.has(pn)) node.children.set(pn, { name: pn, path, n: 0, verified: 0, favorite: 0, children: new Map() });
      node = node.children.get(pn);
      node.n += f.n; node.verified += f.verified || 0; node.favorite += f.favorite || 0;
    });
    if (!parts.length) root.own = (root.own || 0) + f.n;
  });
  return root;
}
function renderTree() {
  const el = $('#folderTree');
  const f = S.facets;
  if (!f || !f.folders || !f.folders.length) { el.innerHTML = ''; return; }
  const root = buildTree(f.folders);
  // 前に開いていたプロジェクトのフォルダが残っていて 0 件、にならないように、木にないフォルダの選択は外す
  const known = new Set((S.facets && S.facets.folders || []).map(f => f.folder || f.path || ''));
  if (S.filter.folder_prefix && S.facets && ![...known].some(k => k === S.filter.folder_prefix || k.startsWith(S.filter.folder_prefix + '/'))) { S.filter.folder_prefix = ''; saveUi(); }
  const sel = S.filter.folder_prefix || '';
  const rows = [];
  const line = (node, depth, isRoot) => {
    const kids = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    const open = isRoot || S.treeOpen[node.path] !== false && (S.treeOpen[node.path] === true || depth < 2 || sel.startsWith(node.path + '/') || sel === node.path);
    const tog = kids.length ? (open ? '▾' : '▸') : '';
    rows.push(`<div class="tn${(isRoot ? sel === '' : sel === node.path) ? ' sel' : ''}" data-p="${esc(node.path)}" style="padding-left:${6 + depth * 14}px" title="${esc(node.path || '/')}">
      <span class="tog" data-tog="${esc(node.path)}">${tog}</span><span class="ico">${isRoot ? '🖿' : '📁'}</span><span class="nm">${esc(isRoot ? (S.project ? S.project.name : '') : node.name)}</span>
      ${node.favorite ? `<span class="fav">★${node.favorite}</span>` : ''}<span class="cnt${node.verified === node.n && node.n ? ' done' : ''}">${node.verified}/${node.n}</span></div>`);
    if (open) kids.forEach(k => line(k, depth + 1, false));
  };
  line(root, 0, true);
  el.innerHTML = rows.join('');
  el.querySelectorAll('.tn').forEach(div => {
    div.onclick = e => {
      const tg = e.target.closest('.tog');
      if (tg && tg.textContent) { S.treeOpen[tg.dataset.tog] = tg.textContent !== '▾'; saveUi(); renderTree(); return; }
      S.filter.folder_prefix = div.dataset.p; saveUi(); renderTree(); reloadList();
    };
  });
}

function listQuery(extra) { return qs(Object.assign({}, S.filter, { sort: S.sort, desc: S.desc ? 1 : 0 }, extra || {})); }
function saveUi() { api('/api/ui', { filter: S.filter, sort: S.sort, desc: S.desc, treeOpen: S.treeOpen }).catch(() => {}); }

// ---------- 一覧（サーバー側ページングの仮想リスト） ----------
const VLIST = { height: 68, first: -1, last: -1, wired: false, ticking: false };
async function reloadList(keepScroll) {
  if (!S.project || !S.project.root) { S.total = 0; S.pages.clear(); renderWindow(true); renderCounts(); return; }
  const token = ++S.listToken;
  S.pages.clear(); S.pageLoading.clear();
  clearSelection(false);
  const ul = $('#itemList');
  if (!keepScroll) ul.scrollTop = 0;
  const d = await api(`/api/items?${listQuery({ offset: 0, limit: PAGE, count: 1 })}`);
  if (token !== S.listToken) return;
  S.total = d.total; S.pages.set(0, d.rows); S.transcoding = new Set(d.transcoding || []);
  renderWindow(true); renderCounts(); renderItemPos();
}
function renderCounts() {
  const f = S.facets || {}; const c = f.counts || {};
  $('#counts').innerHTML = `${S.total} ${t('items')}　` + ['new', 'auto', 'verified'].filter(s => c[s]).map(s => `<span class="cnt st-${s}">${esc(t('st_' + s))} ${c[s]}</span>`).join(' ') +
    (f.low ? ` <span class="cnt st-low" title="${esc(t('det_low_tip'))}">${esc(t('det_low'))} ${f.low}</span>` : '') +
    (f.agree && f.agree.mismatch ? ` <span class="cnt st-mismatch clickable" id="cntMismatch" title="${esc(t('cmp_disagree_tip'))}">≠AI ${f.agree.mismatch}</span>` : '');
  const cm = $('#cntMismatch');
  if (cm) cm.onclick = () => { $('#fDet').value = 'cmp:disagree'; $('#fDet').dispatchEvent(new Event('change')); };
  // 絞り込み（フォルダ以外）が効いているときは、それと分かるようにして外せるようにする（フォルダを選んでも 0 件、を防ぐ）
  const active = activeFilterNames();
  if (active.length) $('#counts').innerHTML += ` <span class="cnt st-filter clickable" id="cntFilter" title="${esc(active.join(' · '))}">${esc(t('filter_on'))} ${active.length} ✕</span>`;
  const cf = $('#cntFilter');
  if (cf) cf.onclick = clearFilters;
  ['fStation', 'fSeason', 'fStatus', 'fSpecies', 'fAssignee', 'fDet'].forEach(id => { const el = $('#' + id); if (el) el.classList.toggle('active', !!el.value); });
  $('#fText').classList.toggle('active', !!S.filter.text);
}
function activeFilterNames() {
  const f = S.filter, out = [];
  if (f.station) out.push(`${t('filter_station')}: ${f.station}`);
  if (f.season) out.push(`${t('filter_season')}: ${f.season}`);
  if (f.status) out.push(`${t('filter_status')}: ${t('st_' + f.status)}`);
  if (f.species) out.push(`${t('filter_species')}: ${f.species === 'none' ? t('sp_none') : f.species === 'any' ? t('sp_any') : spLabel(f.species)}`);
  if (f.assignee) out.push(`${t('filter_assignee')}: ${f.assignee}`);
  if (f.text) out.push(`${t('filter_text')}: ${f.text}`);
  if (f.favorite) out.push('★');
  if (f.flag === 'low') out.push(t('det_low'));
  if (f.exclude) out.push(t('det_excluded'));
  if (f.boxes) out.push(`${t('boxes_n')} ${f.boxes}`);
  if (f.agree) out.push(t('cmp_' + f.agree) || f.agree);
  if (f.det) out.push(t('det_' + f.det) || f.det);
  return out;
}
function clearFilters() {   // フォルダの選択（木）はそのまま
  Object.assign(S.filter, { station: '', season: '', status: '', species: '', assignee: '', text: '', favorite: '', det: '', flag: '', agree: '', exclude: '', boxes: '' });
  $('#fText').value = '';
  renderFilters(); saveUi(); reloadList();
}
function rowAt(i) { const p = Math.floor(i / PAGE); const page = S.pages.get(p); return page ? page[i - p * PAGE] : undefined; }
async function ensurePage(p, token) {
  if (S.pages.has(p) || S.pageLoading.has(p)) return;
  const pr = api(`/api/items?${listQuery({ offset: p * PAGE, limit: PAGE })}`).then(d => {
    if (token !== S.listToken) return;
    S.pages.set(p, d.rows); S.pageLoading.delete(p); renderWindow(true);
  }).catch(e => { S.pageLoading.delete(p); console.error(e); });
  S.pageLoading.set(p, pr);
}
async function rowAtAsync(i) {
  if (i < 0 || i >= S.total) return null;
  const p = Math.floor(i / PAGE);
  if (!S.pages.has(p)) { await ensurePage(p, S.listToken); if (S.pageLoading.has(p)) await S.pageLoading.get(p); }
  return rowAt(i) || null;
}
function rowHtml(it, i) {
  if (!it) return `<li class="item" data-i="${i}"><span class="thumb"></span><span class="name">…</span></li>`;
  const sp = judgementsHtml(it);
  const tc = S.transcoding.has(it.id) ? ' <span class="tc">⟳</span>' : '';
  return `<li class="item st-${it.status}${S.cur && S.cur.id === it.id ? ' current' : ''}${S.picked.has(it.id) ? ' selected' : ''}${it.missing ? ' missing' : ''}" data-id="${it.id}" data-i="${i}" title="${esc(it.rel_path)}">
    <img class="thumb" loading="lazy" src="/api/items/${it.id}/thumb" alt="" onerror="this.style.visibility='hidden'">
    <span class="name">${it.favorite ? '<span class="star">★</span>' : ''}${it.train_exclude ? `<span class="excl" title="${esc(t('det_excluded'))}">🚫</span>` : ''}${it.kind === 'image' ? '🖼 ' : ''}${esc(it.name)}${it.missing ? ' ！' : ''}${tc}</span>
    <span class="badge">${esc(badgeText(it))}</span>
    <span class="sub">${esc(it.station || '')}${it.station ? ' · ' : ''}${it.datetime_corrected ? `<span class="dtc" title="${esc(it.datetime || '')}">⏱ ${esc(it.datetime_corrected)}</span>` : esc(it.datetime || '')}${it.det_state === 1 ? ` · <span class="nbox" title="${esc(t('boxes_n_tip'))}">${esc(t('boxes_n'))} ${it.n_boxes || 0}</span>` : ''}${it.note ? ' · ' + esc(it.note) : ''}</span>
    <span class="jud">${sp || '<span class="info">—</span>'}</span></li>`;
}
/* 状態の札: 未確認のうち、判定が Timelapse / CSV から取り込んだものは「取り込み」、モデルのものは「AI 判定」 */
function badgeText(it) {
  if (it.status === 'auto' && (it.judgements || []).length) {
    if (it.judgements.every(j => j.origin === 'csv')) return t('st_csv');
    if (it.judgements.every(j => j.origin === 'auto')) return t('st_ai');
  }
  return t('st_' + it.status);
}
/* 一覧の行の判定: 「人 ニホンジカ · AI イノシシ ≠」。人 = 緑、AI = 青（右の欄の箱と同じ色）。人の判定と AI が一致なら AI 側は ✓ だけ */
function judgementsHtml(it) {
  const js = it.judgements || [];
  const human = js.filter(j => it.status === 'verified' || j.origin === 'csv' || j.origin === 'manual');
  const aiOwn = !human.length && js.length && js.every(j => j.origin === 'auto');
  const parts = [];
  if (human.length) parts.push(`<span class="jtag human">${esc(t('cmp_human'))}</span><span class="sp human">${esc(human.map(j => spLabel(j.species)).join(', '))}</span>`);
  const ai = it.auto || (aiOwn ? js.map(j => ({ species: j.species })) : null);
  if (ai) {
    const names = ai.length ? ai.map(a => spLabel(a.species)).join(', ') : t('det_low');
    if (human.length && it.agree === 1) parts.push(`<span class="jtag ai">AI</span><span class="sp ai">✓</span>`);
    else parts.push(`<span class="jtag ai">AI</span><span class="sp ai ${it.agree === 3 ? 'ng' : ''}">${esc(names)}${it.agree === 3 ? ' ≠' : (it.agree === 2 ? ' ≈' : '')}</span>`);
  }
  return parts.join(' · ');
}
function renderWindow(force) {
  const ul = $('#itemList'), h = VLIST.height, n = S.total;
  if (!VLIST.wired) {
    VLIST.wired = true;
    ul.addEventListener('scroll', () => { if (!VLIST.ticking) { VLIST.ticking = true; requestAnimationFrame(() => { VLIST.ticking = false; renderWindow(); }); } });
    ul.addEventListener('click', e => {
      const li = e.target.closest('li.item'); if (!li || !li.dataset.id) return;
      const id = parseInt(li.dataset.id), i = parseInt(li.dataset.i);
      if (e.shiftKey) { e.preventDefault(); selectRange(i); return; }
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); toggleSelect(id, i); return; }
      if (S.picked.size) clearSelection(true);
      openItem(id, i);
    });
    ul.addEventListener('contextmenu', e => {
      const li = e.target.closest('li.item'); if (!li || !li.dataset.id) return;
      e.preventDefault();
      const id = parseInt(li.dataset.id), i = parseInt(li.dataset.i);
      if (!S.picked.has(id)) { clearSelection(false); S.picked.add(id); S.selAnchor = i; renderSelection(); }
      showCtxMenu(e.clientX, e.clientY, id, i);
    });
    window.addEventListener('resize', () => renderWindow(true));
  }
  const vis = Math.ceil((ul.clientHeight || 600) / h) + 1, buf = 10;
  const first = Math.max(0, Math.floor(ul.scrollTop / h) - buf), last = Math.min(n, first + vis + 2 * buf);
  if (!force && first === VLIST.first && last === VLIST.last) return;
  VLIST.first = first; VLIST.last = last;
  const token = S.listToken;
  for (let p = Math.floor(first / PAGE); p <= Math.floor(Math.max(first, last - 1) / PAGE); p++) if (p * PAGE < n) ensurePage(p, token);
  const rows = [];
  for (let i = first; i < last; i++) rows.push(rowHtml(rowAt(i), i));
  ul.innerHTML = `<li class="spacer" style="height:${first * h}px"></li>` + rows.join('') + `<li class="spacer" style="height:${Math.max(0, n - last) * h}px"></li>`;
}
function renderItemPos() {   // いまの一覧（フォルダ・絞り込み）の中で何番目か
  const el = $('#itemPos'); if (!el) return;
  const ok = S.cur && S.curIndex >= 0 && S.total;
  el.classList.toggle('hidden', !ok);
  if (ok) el.textContent = `${S.curIndex + 1} / ${S.total}`;
}
function scrollToIndex(i) {
  renderItemPos();
  const ul = $('#itemList');
  if (i < 0) return;
  const top = i * VLIST.height, bottom = top + VLIST.height;
  if (top < ul.scrollTop) ul.scrollTop = top; else if (bottom > ul.scrollTop + ul.clientHeight) ul.scrollTop = bottom - ul.clientHeight;
  renderWindow(true);
}
function patchRow(row) {   // 保存したあとに一覧の行だけ直す（ページを読み直さない）
  for (const [p, page] of S.pages) { const i = page.findIndex(r => r.id === row.id); if (i >= 0) { page[i] = Object.assign({}, page[i], row); } }
  renderWindow(true);
}

// ---------- 一覧の複数選択（Ctrl+A / Shift+クリック / Ctrl+クリック）と、まとめて印・削除 ----------
function clearSelection(redraw) { if (!S.picked.size && S.selAnchor < 0) return; S.picked.clear(); S.selAnchor = -1; if (redraw) { renderSelection(); renderWindow(true); } else renderSelection(); }
function toggleSelect(id, i) {
  if (S.picked.has(id)) S.picked.delete(id); else S.picked.add(id);
  S.selAnchor = i; renderSelection(); renderWindow(true);
}
async function selectRange(i) {   // Shift+クリック: 起点（なければいま開いている対象）から i まで。並びの id はサーバーに聞く（読み込んでいないページもあるため）
  const a = S.selAnchor >= 0 ? S.selAnchor : (S.curIndex >= 0 ? S.curIndex : i);
  const lo = Math.min(a, i), hi = Math.max(a, i);
  try {
    const ids = await currentIds();
    for (let k = lo; k <= hi && k < ids.length; k++) S.picked.add(ids[k]);
    if (S.selAnchor < 0) S.selAnchor = a;
    renderSelection(); renderWindow(true);
  } catch (e) { toast(e.message, 'error'); }
}
async function selectAll() {
  if (!S.project || !S.total) return;
  try { const ids = await currentIds(); S.picked = new Set(ids); if (S.selAnchor < 0) S.selAnchor = 0; renderSelection(); renderWindow(true); } catch (e) { toast(e.message, 'error'); }
}
async function currentIds() { const r = await api(`/api/items/ids?${listQuery()}`); return r.ids || []; }
function renderSelection() {
  renderSelectionNote();
  const bar = $('#selBar'); if (!bar) return;
  const n = S.picked.size;
  bar.classList.toggle('hidden', !n);
  if (n) { $('#selInfo').textContent = `${n} ${t('sel_n')}`; $('#selInfo').title = t('sel_help'); }
  const sb = $('#selBar'); if (sb && sb.dataset.wired) return;
  sb.dataset.wired = '1';
  $('#selFav').onclick = () => bulkAction('favorite');
  $('#selUnfav').onclick = () => bulkAction('unfavorite');
  $('#selExcl').onclick = () => bulkAction('exclude');
  $('#selIncl').onclick = () => bulkAction('include');
  $('#selDel').onclick = () => deleteItems([...S.picked]);
  $('#selClear').onclick = () => clearSelection(true);
}
async function bulkAction(action, ids) {
  ids = ids || [...S.picked];
  if (!ids.length) return;
  try {
    const r = await api('/api/items/bulk', { ids, action });
    const patch = action === 'favorite' ? { favorite: 1 } : action === 'unfavorite' ? { favorite: 0 } : action === 'exclude' ? { train_exclude: 1 } : { train_exclude: 0 };
    const set = new Set(ids);
    for (const [, page] of S.pages) page.forEach((row, k) => { if (row && set.has(row.id)) page[k] = Object.assign({}, row, patch); });
    if (S.cur && set.has(S.cur.id)) { Object.assign(S.cur, patch); renderFav(S.cur); }
    renderWindow(true);
    toast(`${r.n} ${t('sel_done')}`);
    refreshFacets();
  } catch (e) { toast(e.message, 'error'); }
}
async function deleteItems(ids) {
  if (!ids.length) return;
  const ok = await confirmModal(t('del_title'), `<div>${ids.length} ${t('items')}</div><div class="note">${esc(t('del_help'))}</div>`, t('del_n'), true);
  if (!ok) return;
  showOverlayLater(t('loading'));
  try {
    const r = await api('/api/items/bulk', { ids, action: 'delete', to_trash: true });
    hideOverlay();
    const set = new Set(ids);
    if (S.cur && set.has(S.cur.id)) { stopMedia(); S.cur = null; S.detail = null; S.curIndex = -1; $('#judgePanel').classList.add('hidden'); $('#headBtns').classList.add('hidden'); $('#itemName').textContent = t('no_item'); $('#itemMeta').textContent = ''; renderItemPos(); }
    clearSelection(false);
    toast(`${r.n} ${t('del_done')}` + (r.failed ? `　${r.failed} ${t('del_failed')}` : ''), r.failed ? 'error' : undefined);
    if (r.errors && r.errors.length) console.warn(r.errors);
    await refreshFacets(); await reloadList(true);
  } catch (e) { hideOverlay(); toast(e.message, 'error'); }
}
/* 右クリックの品書き: 行が選択に入っていれば選択全部に、そうでなければその行だけに効く */
function showCtxMenu(x, y, id, i) {
  const m = $('#ctxMenu'); if (!m) return;
  const ids = S.picked.has(id) ? [...S.picked] : [id];
  const row = rowAt(i) || {};
  const many = ids.length > 1;
  const items = [
    !many && ['open', t('ctx_open')],
    [many || !row.favorite ? 'favorite' : 'unfavorite', many || !row.favorite ? t('ctx_fav') : t('ctx_unfav')],
    [many || !row.train_exclude ? 'exclude' : 'include', many || !row.train_exclude ? t('ctx_excl') : t('ctx_incl')],
    many && ['unfavorite', t('ctx_unfav')], many && ['include', t('ctx_incl')],
    ['all', t('ctx_sel_all')], 'hr',
    ['delete', `${t('ctx_del')}${many ? ` (${ids.length})` : ''}`, 'danger'],
  ].filter(Boolean);
  m.innerHTML = items.map(it => it === 'hr' ? '<hr>' : `<div data-act="${it[0]}" class="${it[2] || ''}">${esc(it[1])}</div>`).join('');
  m.classList.remove('hidden');
  const mw = m.offsetWidth || 200, mh = m.offsetHeight || 150;
  m.style.left = Math.min(x, window.innerWidth - mw - 4) + 'px';
  m.style.top = Math.min(y, window.innerHeight - mh - 4) + 'px';
  m.onclick = e => {
    const d = e.target.closest('[data-act]'); if (!d) return;
    hideCtxMenu();
    const act = d.dataset.act;
    if (act === 'open') openItem(id, i);
    else if (act === 'all') selectAll();
    else if (act === 'delete') deleteItems(ids);
    else bulkAction(act, ids);
  };
  setTimeout(() => { document.addEventListener('mousedown', ctxOutside); document.addEventListener('keydown', ctxKey); }, 0);
}
function ctxOutside(e) { if (!e.target.closest('#ctxMenu')) hideCtxMenu(); }
function ctxKey(e) { if (e.key === 'Escape') { e.preventDefault(); hideCtxMenu(); } }
function hideCtxMenu() { const m = $('#ctxMenu'); if (m) m.classList.add('hidden'); document.removeEventListener('mousedown', ctxOutside); document.removeEventListener('keydown', ctxKey); }
async function toggleExclude() {
  if (!S.cur) return;
  const on = !S.cur.train_exclude;
  if (S.picked.size) { await bulkAction(on ? 'exclude' : 'include', selectionIds()); return; }       // 選択中は選択した全部に
  try { await api(`/api/items/${S.cur.id}/exclude`, { on }); S.cur.train_exclude = on ? 1 : 0; renderFav(S.cur); patchRow({ id: S.cur.id, train_exclude: S.cur.train_exclude }); refreshFacets(); } catch (e) { toast(e.message, 'error'); }
}

// ---------- 対象を開く ----------
async function openItem(id, index) {
  if (S.busy) return;
  const token = ++S.playToken;
  S.cur = { id }; S.curIndex = (index != null && index >= 0) ? index : S.curIndex;
  renderWindow(true);
  showOverlayLater(t('loading'));
  let d;
  try { d = await api(`/api/items/${id}`); } catch (e) { hideOverlay(); toast(e.message, 'error'); return; }
  if (token !== S.playToken) return;
  S.cur = d; S.detail = d;
  if (index == null || index < 0) { api(`/api/items/${id}/index?${listQuery()}`).then(r => { if (S.cur && S.cur.id === id && r.index != null) { S.curIndex = r.index; scrollToIndex(r.index); } }).catch(() => {}); }
  else scrollToIndex(index);
  renderItemHead(d);
  loadJudgement(d);
  loadBoxes(d);
  await showMedia(d, token);
}
function renderItemHead(d) {
  $('#itemName').textContent = d.name;
  const dt = d.datetime ? `${d.datetime}${d.datetime_source === 'mtime' ? ' (mtime)' : ''}${d.datetime_corrected ? ` → ${d.datetime_corrected} (${t('corrected')} ${fmtOffset(d.time_offset_sec)})` : ''}` : '';
  const meta = [d.station, d.season, dt, d.kind === 'video' ? `${fmtDur(d.duration)} · ${d.width || '?'}×${d.height || '?'} · ${d.fps ? d.fps.toFixed(1) + ' fps' : ''}` : `${d.width || '?'}×${d.height || '?'}`, d.rel_path];
  $('#itemMeta').textContent = meta.filter(Boolean).join(' · ');
  $('#judgePanel').classList.remove('hidden');
  renderFav(d);
  renderDetInfo(d);
}
function renderDetInfo(d) { /* 人と AI の判定は右の欄の 2 つの箱（judge.js の renderJudgeBoxes）に出す */ }
function fmtOffset(sec) {
  if (sec == null) return '';
  const s = Math.abs(sec), sign = sec < 0 ? '−' : '+';
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return sign + (d ? d + 'd ' : '') + `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}
function renderFav(d) {
  const b = $('#btnFav'); $('#headBtns').classList.remove('hidden'); b.textContent = d.favorite ? '★' : '☆'; b.classList.toggle('on', !!d.favorite);
  const x = $('#btnExcl'); if (x) { x.classList.toggle('on', !!d.train_exclude); x.title = d.train_exclude ? t('excl_on_tip') : t('excl_tip'); }
}
async function toggleFavorite() {
  if (!S.cur) return;
  const on = !S.cur.favorite;
  if (S.picked.size) { await bulkAction(on ? 'favorite' : 'unfavorite', selectionIds()); return; }     // 選択中は選択した全部に
  try { await api(`/api/items/${S.cur.id}/favorite`, { on }); S.cur.favorite = on ? 1 : 0; renderFav(S.cur); patchRow({ id: S.cur.id, favorite: S.cur.favorite }); refreshFacets(); } catch (e) { toast(e.message, 'error'); }
}
function selectionIds() {   // 選択した対象（いま開いている対象も含める）
  const ids = new Set(S.picked); if (S.cur) ids.add(S.cur.id); return [...ids];
}
function renderSelectionNote() {   // 右の欄: 選択中なら「n 枚に付きます」と出す
  const el = $('#selNote'); if (!el) return;
  const n = S.picked.size ? selectionIds().length : 0;
  el.classList.toggle('hidden', !n);
  if (n) el.textContent = `${n} ${t('sel_apply_note')}`;
}
async function neighborIds(n) {   // いまの対象の次の n 件の id（先に変換しておくため）
  const out = [];
  if (S.curIndex < 0) return out;
  for (let i = S.curIndex + 1; i <= S.curIndex + n && i < S.total; i++) { const r = await rowAtAsync(i); if (r && r.kind === 'video') out.push(r.id); }
  return out;
}
async function goStep(step) {
  if (!S.cur) { if (S.total) { const r = await rowAtAsync(0); if (r) openItem(r.id, 0); } return; }
  let r = null, idx = S.curIndex;
  if (idx >= 0) { const cand = await rowAtAsync(idx + step); if (cand) { r = cand; idx = idx + step; } }
  if (!r) {
    const d = await api(`/api/items/${S.cur.id}/neighbor?${listQuery({ step })}`).catch(() => null);
    if (d && d.item) { r = d.item; idx = -1; }
  }
  if (!r) { toast(step > 0 ? t('last_item') : t('first_item')); return; }
  openItem(r.id, idx);
}

// ---------- ジョブの表示 ----------
function jobLine(j) {
  if (!j) return '';
  const pct = j.total ? Math.round(100 * (j.done + j.skipped + j.failed) / j.total) : 0;
  const rem = j.remain != null && j.state === 'running' ? ` · ${Math.round(j.remain / 60)} min` : '';
  return `${esc(j.label)}: ${j.done + j.skipped}/${j.total}${j.failed ? ' ✗' + j.failed : ''} (${pct}%)${rem}`;
}
function renderJobs(jobs) {
  if (!jobs) return;
  const cur = (jobs.ml && jobs.ml.current) || (jobs.archive && jobs.archive.current) || (jobs.bg && jobs.bg.current) || (jobs.transcode && jobs.transcode.current);
  const el = $('#jobInfo');
  el.textContent = cur ? jobLine(cur) : t('jb_title');
  el.style.opacity = cur ? '1' : '.6';
  const tc = new Set(jobs.transcoding || []);
  if ([...tc].join() !== [...S.transcoding].join()) { S.transcoding = tc; renderWindow(true); }
  const metaDone = jobs.bg && !jobs.bg.current && jobs.bg.recent && jobs.bg.recent[0] && (Date.now() / 1000 - (S._lastJobSeen || 0) > 0);
  if ((jobs.bg && jobs.bg.current) || (jobs.archive && jobs.archive.current) || (jobs.ml && jobs.ml.current)) S._bgWasRunning = true;
  else if (S._bgWasRunning) { S._bgWasRunning = false; refreshFacets().then(() => reloadList(true)); if (S.cur) refreshCurrent(); }
  if (jobs.ml && jobs.ml.current && jobs.ml.current.done !== S._mlDone) {      // 検出が進んだら一覧の自動判定の表示を更新（2 秒ごと）
    S._mlDone = jobs.ml.current.done;
    if (Date.now() - (S._mlRefresh || 0) > 5000) { S._mlRefresh = Date.now(); refreshFacets().then(() => reloadList(true)); }
  }
  if (typeof archiveOnJobs === 'function') archiveOnJobs(jobs);
  if (typeof detectOnJobs === 'function') detectOnJobs(jobs);
  if (typeof trainOnJobs === 'function') trainOnJobs(jobs);
  if (typeof yoloOnJobs === 'function') yoloOnJobs(jobs);
  if (typeof yoloExportOnJobs === 'function') yoloExportOnJobs(jobs);
}
async function pollJobs() {
  try { const j = await api('/api/jobs'); renderJobs(j); } catch (e) { /* 終了中など */ }
}
function startJobsPolling() { clearInterval(S.jobsTimer); S.jobsTimer = setInterval(pollJobs, 2000); }

// ---------- プロジェクト ----------
async function openProjectPath(path, create) {
  if (!path) return;
  showOverlay(t('loading'));
  try {
    const r = await api('/api/project/open', { path, create: !!create });
    if (r.needs_create) {                       // プロジェクトがなければ作って開く（確認しない）
      toast(t('created_project'));
      return openProjectPath(path, true);
    }
    if (r.needs_root) {
      hideOverlay();
      toast(t('need_root'), 'error');
      const p = await pickPath('folder');
      if (p) { const r2 = await api('/api/project/root', { path: p }); setSnapshot(r2.state); }
      else return;
    } else setSnapshot(r.state);
    if (modalOpen()) closeModal();              // 「プロジェクトを開く」の窓が開いたままなら閉じる
    S.cur = null; S.curIndex = -1; $('#judgePanel').classList.add('hidden'); $('#itemName').textContent = t('no_item'); $('#itemMeta').textContent = ''; renderItemPos();
    stopMedia();
    await reloadList();
    hideOverlay();
    startJobsPolling();
  } catch (e) { hideOverlay(); toast(e.message, 'error'); }
}
async function pickPath(kind, initial, name) {
  try { const r = await api('/api/dialog', { kind, initial, name }); return r.path; } catch (e) { toast(e.message, 'error'); return null; }
}

// ---------- 配線 ----------
function wire() {
  $('#btnOpen').onclick = () => openProjectDialog();
  $('#pathInput').addEventListener('keydown', e => { if (e.key === 'Enter') { const p = $('#pathInput').value.trim(); if (p) openProjectPath(p); } });
  $('#btnRescan').onclick = async () => { if (!S.project) return; await api('/api/project/rescan', {}); toast(t('rescan') + '…'); };
  $('#btnArchive').onclick = () => S.project && archiveDialog();
  $('#btnDetect').onclick = () => S.project && detectDialog();
  $('#btnTrain').onclick = () => S.project && trainDialog();
  $('#btnImport').onclick = () => S.project ? importDialog('csv') : toast(t('tl_open_first'), 'error');
  $('#btnTimelapse').onclick = () => S.project ? timelapseDialog() : toast(t('tl_open_first'), 'error');
  $('#btnExport').onclick = () => S.project && exportDialog();
  $('#btnMerge').onclick = () => S.project && mergeDialog();
  $('#btnSettings').onclick = () => settingsDialog();
  $('#jobInfo').onclick = () => jobsDialog();
  $('#btnLang').onclick = async () => { const lang = LANG === 'ja' ? 'en' : 'ja'; S.settings = await api('/api/settings', { language: lang }); LANG = lang; applyLang(); renderFilters(); renderWindow(true); renderCounts(); renderTree(); if (S.cur) { renderItemHead(S.cur); renderSpeciesPanel(); renderFieldsPanel(); } renderTarget(); };
  $('#btnQuit').onclick = quitApp;
  ['fStation', 'fSeason', 'fStatus', 'fSpecies', 'fAssignee'].forEach(id => {
    $('#' + id).addEventListener('change', () => { S.filter[id.slice(1).toLowerCase()] = $('#' + id).value; saveUi(); reloadList(); });
  });
  $('#fFav').addEventListener('change', () => { S.filter.favorite = $('#fFav').checked ? '1' : ''; saveUi(); reloadList(); });
  $('#fDet').addEventListener('change', () => { const v = $('#fDet').value; S.filter.flag = v === 'low' ? 'low' : ''; S.filter.exclude = v === 'excl' ? '1' : ''; S.filter.boxes = v.startsWith('box:') ? v.slice(4) : ''; S.filter.agree = v.startsWith('cmp:') ? v.slice(4) : ''; S.filter.det = (v === 'low' || v === 'excl' || v.startsWith('cmp:') || v.startsWith('box:')) ? '' : v; saveUi(); reloadList(); });
  let textTimer = null;
  $('#fText').addEventListener('input', () => { clearTimeout(textTimer); textTimer = setTimeout(() => { S.filter.text = $('#fText').value.trim(); saveUi(); reloadList(); }, 300); });
  $('#sortSel').addEventListener('change', () => { S.sort = $('#sortSel').value; saveUi(); reloadList(); });
  $('#sortDir').onclick = () => { S.desc = !S.desc; $('#sortDir').textContent = S.desc ? '↑' : '↓'; saveUi(); reloadList(); };
  $('#btnAssign').onclick = () => S.project && assignDialog();
  $('#btnBulk').onclick = () => S.project && bulkDialog();
  $('#btnAgree').onclick = () => S.project && evalDialog({});
  $('#btnExcl').onclick = toggleExclude;
  renderSelection();
  $('#modalBack').onclick = () => { if (!S.modalLocked) closeModal(); };
  document.addEventListener('keydown', onKey);
  wirePlayer();
  wireJudge();
}
function inInput(e) { const tg = e.target; return tg && (tg.tagName === 'INPUT' || tg.tagName === 'TEXTAREA' || tg.tagName === 'SELECT' || tg.isContentEditable); }
function onKey(e) {
  if (modalOpen()) { if (e.key === 'Escape') { closeModal(); e.preventDefault(); } return; }
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && inInput(e)) { e.preventDefault(); confirmAndNext(); return; }
  if (inInput(e)) { if (e.key === 'Escape') e.target.blur(); return; }
  const k = e.key;
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'z') { e.preventDefault(); undoLast(); return; }
  if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'a') { e.preventDefault(); selectAll(); return; }
  if (k === 'Escape' && S.picked.size) { e.preventDefault(); clearSelection(true); return; }
  if (k === 'Delete' && S.picked.size) { e.preventDefault(); deleteItems([...S.picked]); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (k === ' ') { e.preventDefault(); togglePlay(); return; }
  if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); confirmOrStep(k === 'ArrowRight' ? 1 : -1); return; }
  if (k.toLowerCase() === 'f' && !speciesKeyMap()['F']) { e.preventDefault(); toggleFavorite(); return; }
  if (k === ',' || k === '.') { e.preventDefault(); stepFrame(k === '.' ? 1 : -1); return; }
  if (k === '<' || k === '>') { e.preventDefault(); stepFrame(k === '>' ? 10 : -10); return; }
  if (k === 'PageDown') { e.preventDefault(); goStep(1); return; }
  if (k === 'PageUp') { e.preventDefault(); goStep(-1); return; }
  if (k === 'Enter') { e.preventDefault(); confirmAndNext(); return; }
  if (k === '[' || k === ']') { e.preventDefault(); cycleSpeed(k === ']' ? 1 : -1); return; }
  if (k.toLowerCase() === 'l' && !speciesKeyMap()['L']) { e.preventDefault(); $('#vbLoop').checked = !$('#vbLoop').checked; $('#vbLoop').dispatchEvent(new Event('change')); return; }
  if (k.length === 1) { const code = speciesKeyMap()[k.toUpperCase()]; if (code && S.cur) { e.preventDefault(); toggleSpecies(code); } }
}
async function quitApp() {
  if (!await confirmModal(t('quit'), `<div>${esc(t('quit_q'))}</div>`, t('quit'), true)) return;
  try { await api('/api/quit', {}); } catch (e) { /* 止まるときに切れる */ }
  clearInterval(S.jobsTimer);
  document.body.innerHTML = `<div class="bye">${esc(t('bye'))}</div>`;
  setTimeout(() => window.close(), 500);
}

async function init() {
  wire();
  try {
    const snap = await api('/api/state');
    if (snap.ui && snap.ui.filter) { Object.assign(S.filter, snap.ui.filter); S.sort = snap.ui.sort || S.sort; S.desc = !!snap.ui.desc; S.treeOpen = snap.ui.treeOpen || {}; }
    setSnapshot(snap);
    $('#fText').value = S.filter.text || '';
    if (S.project && S.project.root) {
      await reloadList();
      if (snap.last_item) openItem(snap.last_item).catch(() => {});
    } else if (!S.project) {
      // 起動直後は前回のプロジェクトを裏で開いている途中かもしれないので、少し待って見直す
      setTimeout(async () => { const s2 = await api('/api/state'); if (s2.project) { setSnapshot(s2); await reloadList(); if (s2.last_item) openItem(s2.last_item).catch(() => {}); } else openProjectDialog(); }, 1200);
    }
    startJobsPolling();
  } catch (e) { toast(e.message, 'error'); }
}
