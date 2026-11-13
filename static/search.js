'use strict';
/* 高度な検索の別窓（/search）。本体の画面（index.html）の「高度な検索」のボタンから開く。

   絞り込みの持ち主は本体（S.filter）。この窓は本体から絞り込みをまるごと受け取って画面の入力（W.q）に直し、入力が変わるたびに
   絞り込みに戻してまるごと本体へ送る（BroadcastChannel。search_main.js）。本体はそれで一覧を読み直す。当てはまる数はこの窓が自分でサーバーに聞く。
   簡易の絞り込みの鍵で表せる条件はその鍵に、表せない条件（複数選択・除外・時刻・長さ・AI の信頼度・記録した項目）は adv（JSON）に入れる。
   組み合わせ: 項目どうしは AND、1 つの項目の中で選んだ値どうしは OR（adv の形と SQL は trapnote/search.py）。
   文字列は i18n.js / i18n_search.js、本体と共通の部分は search_shared.js */

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
async function api(path, body, method) {
  const opt = { method: method || (body === undefined ? 'GET' : 'POST'), headers: {}, cache: 'no-store' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const err = new Error((data && data.error) || `${r.status} ${r.statusText}`); err.status = r.status; throw err; }
  if (data && data.error) throw new Error(data.error);
  return data;
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

const W = {
  project: null, facets: {}, species: { species: [], fixed: [] }, spMap: {}, fields: [], edition: 'research', infoKey: '', infoAt: 0,
  saved: [], q: null, keys: ADV_KEYS.slice(), lastKey: '{}',
  chan: null, bind: '', mid: null, rev: null, linked: null, ackTimer: null, work: Promise.resolve(), byeAt: 0,
  pushTimer: null, seq: 0, total: null, all: null, countAt: 0, urlMax: 7000, build: null, pingFail: 0, gone: false,
};

// ---------- 小道具 ----------
let toastTimer = null;
function toast(msg, kind) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg; el.className = kind === 'error' ? 'error' : '';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), kind === 'error' ? 6000 : 2500);
}
function confirmBox(msg, detail, okLabel, danger) {
  return new Promise(resolve => {
    const m = $('#swModal'), back = $('#swBack');
    m.innerHTML = `<div class="msg">${esc(msg)}</div>${detail ? `<div class="detail">${esc(detail)}</div>` : ''}
      <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="${danger ? 'danger-outline' : 'primary'}">${esc(okLabel || t('ok'))}</button></div>`;
    const key = e => { if (e.key === 'Escape') { e.preventDefault(); done(false); } };
    const done = v => { m.classList.add('hidden'); back.classList.add('hidden'); m.innerHTML = ''; document.removeEventListener('keydown', key, true); resolve(v); };
    m.classList.remove('hidden'); back.classList.remove('hidden');
    m.querySelector('#mOk').onclick = () => done(true);
    m.querySelector('#mCancel').onclick = () => done(false);
    back.onclick = () => done(false);
    document.addEventListener('keydown', key, true);
    m.querySelector('#mOk').focus();
  });
}
function spLabel(code) { const s = W.spMap[code]; return s ? spName(s) : code; }
function fieldDef(key) { return W.fields.find(f => f.key === key); }
function ctx() { return { sp: spLabel, fd: fieldDef }; }
const numIn = v => (v === undefined || v === null || v === '' ? '' : String(v));
const isNum = v => v !== '' && v !== null && v !== undefined && isFinite(Number(v));

// ---------- 画面の入力（W.q）と絞り込みの行き来 ----------
function emptyQ() {
  return {
    sp: { src: '', kind: '', codes: [], mode: 'any', not: false },      // 種: 判定の元（'' = 人 / ai）、kind（none / any / multi）か印を付けた種
    st: { vals: [], not: false }, se: { vals: [], not: false },         // 地点・調査回
    date_from: '', date_to: '', time_from: '', time_to: '', corrected: false, dur_min: '', dur_max: '',
    status: '', kind: '', favorite: false, flag: '', assignee: '', batch: '', text: '', folder: '', folder_sub: true, missing: '',
    det: '', agree: '', box_min: '', box_max: '', conf_min: '', conf_max: '', exclude: false,
    fields: {},        // 記録した項目: key → { in: [], min, max, text, check: '' / yes / no, filled, blank }
    orphans: [],       // この画面の欄に出せない項目の条件（消えた項目、型が変わった項目）。「いまの条件」には出て、そのまま送る
    other: {}, advOther: {}, bad: '',      // 知らない鍵・adv の知らない項目・読めない adv。そのまま送る
  };
}
function fieldDflt(fd) { return fd.default && !fd.repeat ? String(fd.default) : ''; }      // 値を入れていない行に画面が出す既定値（＋で増やす項目にはない）
function fieldKind(fd) { return fd.type === 'select' || fd.type === 'multiselect' ? 'pick' : fd.type === 'check' ? 'check' : (fd.type === 'number' || fd.type === 'time') ? 'range' : 'text'; }
function fieldFits(fd, c) {      // 条件 c を、いまの項目の欄で表せるか
  if (!fd || fd.scope !== c.scope) return false;
  const k = fieldKind(fd);
  const has = { pick: Array.isArray(c.in), range: advHas(c.min) || advHas(c.max), text: advHas(c.text) || !!c.filled, check: c.check === true || c.check === false };
  if (Object.keys(has).some(x => x !== k && has[x])) return false;
  if (has.pick && !!c.multi !== (fd.type === 'multiselect')) return false;
  if (c.blank && (fieldDflt(fd) || k === 'check')) return false;
  return true;
}
function qFromFilter(f) {
  const q = emptyQ();
  f = f || {};
  let a = advParse(f.adv);
  if (a === null) { q.bad = String(f.adv); a = {}; }
  // 種
  const sv = advHas(f.species) ? String(f.species) : '';
  q.sp.src = f.species_src === 'ai' ? 'ai' : '';
  if (sv === 'none' || sv === 'any' || sv === 'multi') q.sp.kind = sv;
  else if (sv) { const all = sv.includes('+'); q.sp.codes = sv.split(all ? '+' : ',').map(x => x.trim()).filter(Boolean); q.sp.mode = all ? 'all' : 'any'; }
  else if (a.species && Array.isArray(a.species.in)) {
    q.sp.codes = a.species.in.map(String); q.sp.not = !!a.species.not; q.sp.mode = a.species.all ? 'all' : 'any'; q.sp.src = a.species.src === 'ai' ? 'ai' : '';
  }
  // 地点・調査回（簡易の鍵で 1 つ選んでいればそれ）
  [['st', 'station'], ['se', 'season']].forEach(([k, key]) => {
    if (advHas(f[key])) q[k].vals = [String(f[key])];
    else if (a[key] && Array.isArray(a[key].in)) { q[k].vals = a[key].in.map(String); q[k].not = !!a[key].not; }
  });
  // 日時・長さ
  const dateIn = v => { const s = String(v || '').replace(/\//g, '-').slice(0, 10); return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : ''; };
  const clockIn = v => { const m = /^(\d{1,2}):(\d{2})$/.exec(String(v || '')); return m ? `${m[1].padStart(2, '0')}:${m[2]}` : ''; };
  q.date_from = dateIn(f.date_from); q.date_to = dateIn(f.date_to);
  if (a.time) { q.time_from = clockIn(a.time.from); q.time_to = clockIn(a.time.to); }
  q.corrected = advOn(f.corrected);
  if (a.duration) { q.dur_min = numIn(a.duration.min); q.dur_max = numIn(a.duration.max); }
  // 状態・ファイル
  ['status', 'kind', 'flag', 'assignee', 'batch', 'text', 'det', 'agree'].forEach(k => { q[k] = advHas(f[k]) ? String(f[k]) : ''; });
  q.favorite = advOn(f.favorite); q.exclude = advOn(f.exclude);
  if (advHas(f.folder)) { q.folder = String(f.folder); q.folder_sub = false; } else if (advHas(f.folder_prefix)) { q.folder = String(f.folder_prefix).replace(/^\/+|\/+$/g, ''); q.folder_sub = true; }
  q.missing = advOn(f.missing) ? 'only' : (advHas(f.include_missing) ? 'include' : '');
  // 検出・AI
  const bx = /^(\d+)(\+|-(\d+))?$/.exec(String(f.boxes || ''));
  if (bx) { q.box_min = bx[1]; q.box_max = bx[2] === '+' ? '' : (bx[3] !== undefined ? bx[3] : bx[1]); }
  else if (a.boxes) { q.box_min = numIn(a.boxes.min); q.box_max = numIn(a.boxes.max); }
  if (a.conf) { q.conf_min = numIn(a.conf.min); q.conf_max = numIn(a.conf.max); }
  // 記録した項目
  (Array.isArray(a.fields) ? a.fields : []).forEach(c => {
    if (!c || !c.key) return;
    const fd = fieldDef(c.key);
    if (!fieldFits(fd, c) || q.fields[c.key]) { q.orphans.push(c); return; }
    q.fields[c.key] = { in: Array.isArray(c.in) ? c.in.map(String) : [], min: numIn(c.min), max: numIn(c.max), text: c.text || '', check: c.check === true ? 'yes' : c.check === false ? 'no' : '', filled: !!c.filled, blank: !!c.blank };
  });
  Object.keys(a).forEach(k => { if (k !== 'v' && !ADV_PARTS.includes(k)) q.advOther[k] = a[k]; });
  Object.keys(f).forEach(k => { if (!ADV_KEYS.includes(k) && k !== 'ids' && advHas(f[k])) q.other[k] = f[k]; });
  return q;
}
function filterFromQ(q) {
  const f = {};
  W.keys.forEach(k => { f[k] = ''; });       // 鍵は全部入れる（本体がそのまま置き換えられるように）
  Object.assign(f, q.other);
  const a = Object.assign({}, q.advOther);
  // 種: 除くときだけ adv。含むときは簡易の鍵（本体の種の欄にも出る）
  f.species_src = q.sp.src;
  if (q.sp.codes.length) {
    if (q.sp.not) a.species = Object.assign({ in: q.sp.codes.slice(), not: true }, q.sp.src === 'ai' ? { src: 'ai' } : {});
    else f.species = q.sp.codes.join(q.sp.mode === 'all' ? '+' : ',');
  } else if (q.sp.kind) f.species = q.sp.kind;
  // 地点・調査回: 1 つだけ選んで除かないときは簡易の鍵（本体の欄にも出る）。それ以外は adv
  [['st', 'station'], ['se', 'season']].forEach(([k, key]) => {
    const v = q[k].vals;
    if (!v.length) return;
    if (v.length === 1 && !q[k].not && v[0] !== '') f[key] = v[0];
    else a[key] = q[k].not ? { in: v.slice(), not: true } : { in: v.slice() };
  });
  f.date_from = q.date_from; f.date_to = q.date_to;
  if (q.time_from || q.time_to) a.time = Object.assign({}, q.time_from ? { from: q.time_from } : {}, q.time_to ? { to: q.time_to } : {});
  f.corrected = q.corrected ? '1' : '';
  const range = (lo, hi) => (isNum(lo) || isNum(hi) ? Object.assign({}, isNum(lo) ? { min: Number(lo) } : {}, isNum(hi) ? { max: Number(hi) } : {}) : null);
  const dur = range(q.dur_min, q.dur_max);
  if (dur) a.duration = dur;
  ['status', 'kind', 'flag', 'assignee', 'batch', 'det', 'agree'].forEach(k => { f[k] = q[k]; });
  f.text = q.text.trim();
  f.favorite = q.favorite ? '1' : ''; f.exclude = q.exclude ? '1' : '';
  if (q.folder) { if (q.folder_sub) f.folder_prefix = q.folder; else f.folder = q.folder; }
  if (q.missing === 'only') f.missing = '1'; else if (q.missing === 'include') f.include_missing = '1';
  const b0 = isNum(q.box_min) ? Math.max(0, Math.floor(Number(q.box_min))) : null, b1 = isNum(q.box_max) ? Math.max(0, Math.floor(Number(q.box_max))) : null;
  // 枠の数: 0 だけ・1 以上から始まる範囲は簡易の鍵（本体の欄の「枠 0 / 1 / 2 / 3 以上」にも出る）。
  // 0 から始まる範囲は adv（検出済みの動画だけにするため。簡易の鍵の範囲は、まだ検出していない動画も拾ってしまう）
  if (b0 !== null || b1 !== null) {
    const lo = b0 === null ? 0 : b0;
    if (lo === 0 && b1 !== 0) a.boxes = b1 === null ? { min: 0 } : { min: 0, max: b1 };
    else f.boxes = b1 === null ? `${lo}+` : (lo === b1 ? String(lo) : `${lo}-${b1}`);
  }
  const conf = range(q.conf_min, q.conf_max);
  if (conf) a.conf = conf;
  const fl = [];
  W.fields.forEach(fd => {
    const c = q.fields[fd.key];
    if (!c) return;
    const o = { key: fd.key, scope: fd.scope }, k = fieldKind(fd);
    if (k === 'pick') { if (c.in && c.in.length) { o.in = c.in.slice(); if (fd.type === 'multiselect') o.multi = true; } }
    else if (k === 'check') { if (c.check === 'yes') o.check = true; else if (c.check === 'no') o.check = false; }
    else if (k === 'range') { if (isNum(c.min)) o.min = Number(c.min); if (isNum(c.max)) o.max = Number(c.max); }
    else { if (c.text && c.text.trim()) o.text = c.text.trim(); if (c.filled) o.filled = true; }
    if (c.blank && k !== 'check' && !fieldDflt(fd)) o.blank = true;
    if (Object.keys(o).length > 2) { if (fieldDflt(fd)) o.dflt = fieldDflt(fd); fl.push(o); }
  });
  q.orphans.forEach(c => fl.push(c));
  if (fl.length) a.fields = fl;
  f.adv = advPack(a) || q.bad || '';
  return f;
}

// ---------- 描く ----------
const chk = (attrs, on, label, cls, title) => `<label class="chk${cls ? ' ' + cls : ''}"${title ? ` title="${esc(title)}"` : ''}><input type="checkbox" ${attrs}${on ? ' checked' : ''}> ${label}</label>`;
const to = () => ` ${esc(t('adv_to'))} `;      // 範囲の「〜」
const radio = (name, value, on, label, title) => `<label class="chk"${title ? ` title="${esc(title)}"` : ''}><input type="radio" name="${name}" value="${esc(value)}"${on ? ' checked' : ''}> ${esc(label)}</label>`;
function sel(key, opts, value) {      // 選択欄。いまの値が選択肢になければ足す（知らない値もそのまま残すため）
  if (value && !opts.some(o => o[0] === value)) opts = opts.concat([[value, value]]);
  return `<select data-q="${key}">${opts.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
}
function spList() {      // 本体の種の欄と同じ並び（種リストの順、固定の項目は後）。使っている種・数のある種だけ
  const sc = W.facets.species_counts || {}, q = W.q.sp;
  const all = (W.species.species || []).concat(W.species.fixed || []).filter(s => s.enabled || sc[s.code] || s.fixed || q.codes.includes(s.code));
  q.codes.forEach(c => { if (!all.some(s => s.code === c)) all.push({ code: c }); });
  return all;
}
function secSpecies() {
  const q = W.q.sp, sc = W.facets.species_counts || {}, lite = W.edition === 'lite';
  const all = spList();
  const box = s => `<label class="chk" title="${esc(s.name_sci || s.code)}"><input type="checkbox" data-sp="${esc(s.code)}"${q.codes.includes(s.code) ? ' checked' : ''}> <span class="nm">${esc(spName(s))}</span>${sc[s.code] ? `<span class="info">(${sc[s.code]})</span>` : ''}</label>`;
  const src = (!lite || q.src === 'ai') ? `<div class="row"><span class="lbl">${esc(t('sp_src'))}</span>${radio('spSrc', '', q.src !== 'ai', t('sp_src_human'), t('sp_src_human_tip'))}${radio('spSrc', 'ai', q.src === 'ai', t('sp_src_ai'), t('sp_src_ai_tip'))}</div>` : '';
  return `<section class="card" id="secSp"><h3>${esc(t('adv_species'))}<span class="info" id="spN"></span>${chk('id="spNot"', q.not, esc(t('adv_exclude')), 'not', t('adv_exclude_sp_tip'))}</h3>
    ${src}
    <div class="row">${radio('spKind', '', !q.kind, t('all_species'))}${radio('spKind', 'none', q.kind === 'none', t('sp_none'))}${radio('spKind', 'any', q.kind === 'any', t('sp_any'))}${radio('spKind', 'multi', q.kind === 'multi', t('sp_multi'), t('sp_multi_tip'))}</div>
    <div class="row"><span class="lbl">${esc(t('sp_pick_help'))}</span><span id="spModes">${radio('spMode', 'any', q.mode !== 'all', t('sp_mode_any'), t('sp_mode_any_tip'))} ${radio('spMode', 'all', q.mode === 'all', t('sp_mode_all'), t('sp_mode_all_tip'))}</span>
      <button type="button" class="small" id="spAll" title="${esc(t('sp_all_animals_tip'))}">${esc(t('sp_all_animals'))}</button><button type="button" class="small" id="spClr">${esc(t('sp_clear_picks'))}</button></div>
    <div class="grid" id="spGrid">${all.filter(s => !s.fixed).map(box).join('')}</div>
    <div class="fixed-row">${all.filter(s => s.fixed).map(box).join('')}</div></section>`;
}
function pickNames(k) {      // 地点・調査回の候補。地点のない動画があれば「（地点なし）」も。絞り込みに入っているのに候補にないものも出す（印を外せるように）
  const q = W.q[k], f = W.facets;
  const names = ((k === 'st' ? f.stations : f.seasons) || []).map(String);
  if (k === 'st' && (f.by_station || []).some(r => !r.station && r.total)) names.push('');
  const known = new Set(names);
  q.vals.forEach(v => { if (!known.has(v)) names.push(v); });
  return { names, known };
}
function secPick(k) {
  const q = W.q[k], st = k === 'st', { names, known } = pickNames(k);
  const cnt = {};
  if (st) (W.facets.by_station || []).forEach(r => { cnt[r.station || ''] = r.total; });
  const name = v => (v === '' ? t(st ? 'adv_none_station' : 'adv_none_season') : v);
  const box = v => `<label class="chk" data-nm="${esc(name(v).toLowerCase())}" title="${esc(name(v))}"><input type="checkbox" data-v="${esc(v)}"${q.vals.includes(v) ? ' checked' : ''}> <span class="nm">${esc(name(v))}</span>${cnt[v] ? `<span class="info">(${cnt[v]})</span>` : ''}${known.has(v) ? '' : `<span class="info">${esc(t('adv_not_listed'))}</span>`}</label>`;
  const find = st && names.length > 8 ? `<input type="text" id="${k}Find" placeholder="${esc(t('adv_find_station'))}" spellcheck="false">` : '';
  const body = names.length ? `<div class="row">${find}<button type="button" class="small" id="${k}All">${esc(t('adv_tick_shown'))}</button><button type="button" class="small" id="${k}Clr">${esc(t('sp_clear_picks'))}</button></div>
    <div class="grid pick" id="${k}Grid">${names.map(box).join('')}</div>` : `<div class="info">${esc(t(st ? 'adv_no_stations' : 'adv_no_seasons'))}</div>`;
  return `<section class="card" id="sec_${k}"><h3>${esc(t(st ? 'adv_station' : 'adv_season'))}<span class="info" id="${k}N"></span>${chk(`id="${k}Not"`, q.not, esc(t('adv_exclude')), 'not', t(st ? 'adv_exclude_st_tip' : 'adv_exclude_se_tip'))}</h3>${body}</section>`;
}
function secDate() {
  const q = W.q, tip = esc(t('adv_dt_tip'));
  return `<section class="card" id="secDate"><h3>${esc(t('adv_datetime'))}</h3>
    <div class="row" title="${tip}"><span class="lbl">${esc(t('adv_date'))}</span><input type="date" data-q="date_from" value="${esc(q.date_from)}">${to()}<input type="date" data-q="date_to" value="${esc(q.date_to)}"></div>
    <div class="row" title="${tip}"><span class="lbl">${esc(t('adv_time'))}</span><input type="time" data-q="time_from" value="${esc(q.time_from)}">${to()}<input type="time" data-q="time_to" value="${esc(q.time_to)}"><span class="info">${esc(t('adv_time_hint'))}</span></div>
    <div class="row">${chk('data-q="corrected"', q.corrected, esc(t('adv_corrected')))}</div>
    <div class="row"><span class="lbl">${esc(t('adv_duration'))}</span><input type="number" min="0" step="any" data-q="dur_min" data-r="dur" value="${esc(q.dur_min)}">${to()}<input type="number" min="0" step="any" data-q="dur_max" data-r="dur" value="${esc(q.dur_max)}"></div></section>`;
}
function folderList() {      // フォルダの候補: 動画のあるフォルダと、その上のフォルダ全部
  const set = new Set();
  (W.facets.folders || []).forEach(f => { let p = ''; String(f.folder || '').split('/').filter(Boolean).forEach(x => { p = p ? `${p}/${x}` : x; set.add(p); }); });
  return [...set].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}
function secState() {
  const q = W.q, f = W.facets, asg = (f.assignees || []).map(String), bt = (f.batches || []).filter(Boolean).map(String);
  const any = [['', t('adv_any')]];
  return `<section class="card" id="secState"><h3>${esc(t('adv_state'))}</h3>
    <div class="row"><span class="lbl">${esc(t('adv_status'))}</span>${sel('status', [['', t('all_status')], ['new', t('st_new')], ['auto', t('st_auto')], ['verified', t('st_verified')], ['unverified', t('st_unverified')]], q.status)}</div>
    <div class="row"><span class="lbl">${esc(t('adv_kind'))}</span>${sel('kind', any.concat([['video', t('adv_kind_video')], ['image', t('adv_kind_image')]]), q.kind)}${chk('data-q="favorite"', q.favorite, esc(t('adv_fav')))}</div>
    <div class="row"><span class="lbl">${esc(t('adv_flag'))}</span>${sel('flag', any.concat([['low', t('det_low')], ['delete', t('adv_flag_delete')]]), q.flag)}</div>
    ${asg.length || q.assignee ? `<div class="row"><span class="lbl">${esc(t('adv_assignee'))}</span>${sel('assignee', [['', t('all_assignees')]].concat(asg.map(x => [x, x])), q.assignee)}</div>` : ''}
    ${bt.length || q.batch ? `<div class="row"><span class="lbl">${esc(t('filter_batch'))}</span>${sel('batch', [['', t('all_batches')]].concat(bt.map(x => [x, x])), q.batch)}</div>` : ''}
    <div class="row"><span class="lbl">${esc(t('adv_text'))}</span><input type="text" class="wide" data-q="text" value="${esc(q.text)}" placeholder="${esc(t('filter_text'))}" spellcheck="false"></div>
    <div class="row"><span class="lbl">${esc(t('adv_folder'))}</span>${sel('folder', [['', t('adv_folder_all')]].concat(folderList().map(x => [x, x])), q.folder)}${chk('data-q="folder_sub"', q.folder_sub, esc(t('adv_folder_sub')))}</div>
    <div class="row"><span class="lbl">${esc(t('adv_missing'))}</span>${sel('missing', [['', t('adv_missing_no')], ['include', t('adv_missing_inc')], ['only', t('adv_missing_only')]], q.missing)}</div></section>`;
}
function secAi() {
  const q = W.q, lite = W.edition === 'lite';      // Lite 版では AI の種に関する条件（AI との比較・AI の信頼度）を出さない（条件が付いているときは外せるように出す）
  const any = [['', t('adv_any')]];
  const det = any.concat(['todo', 'done', 'animal', 'person', 'vehicle', 'none', 'failed', 'crops', 'train'].filter(v => !lite || (v !== 'crops' && v !== 'train') || q.det === v).map(v => [v, t('det_' + v)]));      // Lite には学習がないので、学習枠の選択肢も出さない
  const ag = any.concat([['disagree', t('cmp_disagree')], ['partial', t('cmp_partial')], ['differ', t('adv_cmp_differ')], ['agree', t('cmp_agree')], ['nohuman', t('cmp_nohuman')], ['any', t('cmp_any')], ['rechecked', t('cmp_rechecked')]]);
  return `<section class="card" id="secAi"><h3>${esc(t(lite ? 'adv_det' : 'adv_ai'))}</h3>
    <div class="row"><span class="lbl">${esc(t('adv_det'))}</span>${sel('det', det, q.det)}</div>
    ${!lite || q.agree ? `<div class="row"><span class="lbl">${esc(t('adv_agree'))}</span>${sel('agree', ag, q.agree)}</div>` : ''}
    <div class="row" title="${esc(t('adv_boxes_tip'))}"><span class="lbl">${esc(t('adv_boxes'))}</span><input type="number" min="0" step="1" data-q="box_min" data-r="box" value="${esc(q.box_min)}">${to()}<input type="number" min="0" step="1" data-q="box_max" data-r="box" value="${esc(q.box_max)}"></div>
    ${!lite || q.conf_min !== '' || q.conf_max !== '' ? `<div class="row" title="${esc(t('adv_conf_tip'))}"><span class="lbl">${esc(t('adv_conf'))}</span><input type="number" min="0" max="100" step="1" data-q="conf_min" data-r="conf" value="${esc(q.conf_min)}">${to()}<input type="number" min="0" max="100" step="1" data-q="conf_max" data-r="conf" value="${esc(q.conf_max)}"></div>` : ''}
    ${!lite || q.exclude ? `<div class="row">${chk('data-q="exclude"', q.exclude, esc(t('adv_excl')))}</div>` : ''}</section>`;
}
function fieldRow(fd) {
  const c = W.q.fields[fd.key] || {}, k = fieldKind(fd), key = esc(fd.key), dflt = fieldDflt(fd);
  const tip = [fd.help || '', dflt ? t('adv_dflt_tip').replace('{v}', dflt) : ''].filter(Boolean).join('\n');
  const blank = dflt ? '' : chk(`data-fk="${key}" data-fp="blank"`, c.blank, esc(t('adv_blank')));      // 既定値のある項目に「未記入」はない（値のない行は既定値として比べる）
  let body;
  if (k === 'pick') {
    const opts = (fd.options || []).concat((c.in || []).filter(v => !(fd.options || []).includes(v)));
    body = `<span class="opts">${opts.map(o => `<label class="chk"><input type="checkbox" data-fk="${key}" data-fp="in" value="${esc(o)}"${(c.in || []).includes(o) ? ' checked' : ''}> ${esc(o)}</label>`).join('')}${blank}</span>`;
  } else if (k === 'check') {
    body = `<select data-fk="${key}" data-fp="check"><option value="">${esc(t('adv_any'))}</option><option value="yes"${c.check === 'yes' ? ' selected' : ''}>${esc(t('adv_yes'))}</option><option value="no"${c.check === 'no' ? ' selected' : ''}>${esc(t('adv_no'))}</option></select>`;
  } else if (k === 'range') {
    body = `<input type="number" step="any" data-fk="${key}" data-fp="min" data-r="f_${key}" value="${esc(c.min || '')}">${to()}<input type="number" step="any" data-fk="${key}" data-fp="max" data-r="f_${key}" value="${esc(c.max || '')}">${blank}`;
  } else {
    body = `<input type="text" data-fk="${key}" data-fp="text" value="${esc(c.text || '')}" placeholder="${esc(t('adv_contains'))}" spellcheck="false">${chk(`data-fk="${key}" data-fp="filled"`, c.filled, esc(t('adv_filled')))}${blank}`;
  }
  return `<div class="row frow" title="${esc(tip)}"><span class="lbl">${esc(fdLabel(fd))}</span>${body}</div>`;
}
function secFields() {
  const spF = W.fields.filter(f => f.scope === 'species'), itF = W.fields.filter(f => f.scope !== 'species');
  if (!W.fields.length) return `<section class="card" id="secFd"><h3>${esc(t('adv_fields'))}</h3><div class="info">${esc(t('adv_no_fields'))}</div></section>`;
  return `<section class="card" id="secFd"><h3>${esc(t('adv_fields'))}</h3>
    ${spF.length ? `<h4>${esc(t('adv_fields_sp'))}</h4><div class="info">${esc(t('adv_fields_sp_help'))}</div>${spF.map(fieldRow).join('')}` : ''}
    ${itF.length ? `<h4>${esc(t('fields_item'))}</h4>${itF.map(fieldRow).join('')}` : ''}</section>`;
}
function render() {
  if (W.gone) return;
  applyLang();
  document.title = `TrapNote — ${t('adv_title')}`;
  $('#swProject').textContent = W.project ? W.project.name : '';
  const main = $('#swMain'), top = main.scrollTop;
  const keep = {};
  ['st', 'se'].forEach(k => { const g = $(`#${k}Grid`), fd = $(`#${k}Find`); keep[k] = { top: g ? g.scrollTop : 0, find: fd ? fd.value : '' }; });
  if (!W.project) {
    main.innerHTML = `<div class="sw-msg">${esc(t('adv_no_project'))}</div>`;
  } else {
    main.innerHTML = `<p class="sw-rule">${esc(t('adv_rule'))}</p>
      <div class="sw-cols"><div class="sw-col">${secSpecies()}${secPick('st')}${secPick('se')}</div><div class="sw-col">${secDate()}${secState()}${secAi()}${secFields()}</div></div>`;
    wireMain();
    ['st', 'se'].forEach(k => { const g = $(`#${k}Grid`), fd = $(`#${k}Find`); if (fd && keep[k].find) { fd.value = keep[k].find; fd.dispatchEvent(new Event('input')); } if (g) g.scrollTop = keep[k].top; });
    main.scrollTop = top;
  }
  ['#svSel', '#svLoad', '#svDel', '#svName', '#svSave', '#swClear'].forEach(s => { $(s).disabled = !W.project; });
  renderSaved(); renderChips(); renderCount(); renderLink(); marks();
}
function renderChips() {
  if (W.gone) return;
  const el = $('#swChips');
  if (!W.project) { el.innerHTML = ''; return; }
  const f = filterFromQ(W.q), ds = advDescribe(f, ctx());
  el.innerHTML = `<span class="lbl">${esc(t('adv_active'))}:</span>` + (ds.length
    ? ds.map((d, i) => `<span class="chipx${d.k === 'adv.bad' ? ' bad' : ''}" title="${esc(d.text)}"><span>${esc(d.text)}</span><button type="button" data-i="${i}">✕</button></span>`).join('')
    : `<span class="info">${esc(t('adv_no_cond'))}</span>`);
  el.querySelectorAll('button[data-i]').forEach(b => { b.onclick = () => { setFilter(advRemove(f, ds[+b.dataset.i].k)); changed(0); }; });
  $('#swClear').disabled = !ds.length;
}
function renderCount(err) {
  if (W.gone) return;
  const el = $('#swCount');
  el.classList.remove('busy'); el.classList.toggle('err', !!err);
  if (err) { el.textContent = err; return; }
  if (!W.project || W.total === null) { el.textContent = ''; return; }
  el.innerHTML = `${esc(t('adv_count'))} <b>${W.total.toLocaleString()}</b> ${esc(t('items'))}<span class="info"> / ${esc(t('adv_count_of').replace('{n}', (W.all || 0).toLocaleString()))}</span>`;
}
function renderLink() {
  if (W.gone) return;
  const el = $('#swLink');
  if (W.linked === null) { el.innerHTML = ''; return; }
  el.className = 'sw-link' + (W.linked ? '' : ' off');
  el.innerHTML = `<span class="dot"></span>${esc(t(W.linked ? 'adv_linked' : 'adv_unlinked'))}`;
}
function setLinked(v) { if (W.linked !== v) { W.linked = v; renderLink(); } }
function renderSaved() {
  if (W.gone) return;
  const s = $('#svSel'), cur = s.value;
  s.innerHTML = `<option value="">${esc(t(W.saved.length ? 'adv_saved_pick' : 'adv_saved_none'))}</option>` + W.saved.map(r => `<option value="${esc(r.name)}">${esc(r.name)}</option>`).join('');
  s.value = W.saved.some(r => r.name === cur) ? cur : '';
  $('#svLoad').disabled = $('#svDel').disabled = !W.project || !s.value;
}
/* 条件の入っている箱に印、選んだ値の数、範囲が逆の入力に印 */
function marks() {
  if (!W.project || W.gone) return;
  const q = W.q, on = (id, v) => { const el = $(id); if (el) el.classList.toggle('on', !!v); };
  on('#secSp', q.sp.codes.length || q.sp.kind);
  on('#sec_st', q.st.vals.length); on('#sec_se', q.se.vals.length);
  on('#secDate', q.date_from || q.date_to || q.time_from || q.time_to || q.corrected || isNum(q.dur_min) || isNum(q.dur_max));
  on('#secState', q.status || q.kind || q.favorite || q.flag || q.assignee || q.batch || q.text.trim() || q.folder || q.missing);
  on('#secAi', q.det || q.agree || isNum(q.box_min) || isNum(q.box_max) || isNum(q.conf_min) || isNum(q.conf_max) || q.exclude);
  on('#secFd', (advParse(filterFromQ(q).adv) || {}).fields);
  // 種: 印の数。「いずれか / 全部含む」は、除くとき・印が 2 つ未満のときは効かないので薄くする
  const n = q.sp.codes.length, spN = $('#spN'), modes = $('#spModes');
  if (spN) spN.textContent = n ? t('adv_picked').replace('{n}', n).replace('{all}', $$('#secSp input[data-sp]').length) : '';
  if (modes) modes.querySelectorAll('label').forEach(l => l.classList.toggle('dim', q.sp.not || n < 2));
  [['#spNot', n], ['#stNot', q.st.vals.length], ['#seNot', q.se.vals.length]].forEach(([id, m]) => { const el = $(id); if (el) el.parentElement.classList.toggle('dim', !m); });      // 印がなければ「選んだものを除く」は効かない
  ['st', 'se'].forEach(k => { const el = $(`#${k}N`); if (el) el.textContent = q[k].vals.length ? t('adv_picked').replace('{n}', q[k].vals.length).replace('{all}', $$(`#sec_${k} input[data-v]`).length) : ''; });
  $$('#swMain input[data-sp], #swMain input[data-v]').forEach(c => c.parentElement.classList.toggle('sel', c.checked));
  const groups = {};
  $$('#swMain input[data-r]').forEach(el => { (groups[el.dataset.r] = groups[el.dataset.r] || []).push(el); });
  Object.values(groups).forEach(g => {
    const bad = g.length === 2 && isNum(g[0].value) && isNum(g[1].value) && Number(g[0].value) > Number(g[1].value);
    g.forEach(el => { el.classList.toggle('bad', bad); el.title = bad ? t('adv_range_bad') : ''; });
  });
}

// ---------- 入力 → W.q ----------
function wireMain() {
  const q = W.q;
  // 種
  const sec = $('#secSp');
  const spBoxes = () => [...sec.querySelectorAll('input[data-sp]')];
  const spRead = () => { q.sp.codes = spBoxes().filter(c => c.checked).map(c => c.dataset.sp); };
  sec.querySelectorAll('input[name=spSrc]').forEach(r => { r.onchange = () => { q.sp.src = r.value; changed(); }; });
  sec.querySelectorAll('input[name=spKind]').forEach(r => { r.onchange = () => { q.sp.kind = r.value; spBoxes().forEach(c => { c.checked = false; }); spRead(); changed(); }; });
  sec.querySelectorAll('input[name=spMode]').forEach(r => { r.onchange = () => { q.sp.mode = r.value; changed(); }; });
  const spPicked = () => { spRead(); if (q.sp.codes.length) { q.sp.kind = ''; sec.querySelector('input[name=spKind][value=""]').checked = true; } changed(); };
  spBoxes().forEach(c => { c.onchange = spPicked; });
  $('#spNot').onchange = () => { q.sp.not = $('#spNot').checked; changed(); };
  $('#spAll').onclick = () => { const fixed = new Set((W.species.fixed || []).map(s => s.code)); spBoxes().forEach(c => { c.checked = !fixed.has(c.dataset.sp); }); spPicked(); };      // 動物の種に一括で印（動物なし・人・車・有効範囲・不明は付けない）
  $('#spClr').onclick = () => { spBoxes().forEach(c => { c.checked = false; }); spRead(); changed(); };
  // 地点・調査回
  ['st', 'se'].forEach(k => {
    const s = $(`#sec_${k}`), boxes = () => [...s.querySelectorAll('input[data-v]')];
    const read = () => { q[k].vals = boxes().filter(c => c.checked).map(c => c.dataset.v); changed(); };
    boxes().forEach(c => { c.onchange = read; });
    $(`#${k}Not`).onchange = () => { q[k].not = $(`#${k}Not`).checked; changed(); };
    const find = $(`#${k}Find`), all = $(`#${k}All`), clr = $(`#${k}Clr`);
    if (find) find.oninput = () => { const w = find.value.trim().toLowerCase(); s.querySelectorAll('label[data-nm]').forEach(l => l.classList.toggle('hidden', !!w && !l.dataset.nm.includes(w))); };
    if (all) all.onclick = () => { s.querySelectorAll('label[data-nm]:not(.hidden) input').forEach(c => { c.checked = true; }); read(); };
    if (clr) clr.onclick = () => { boxes().forEach(c => { c.checked = false; }); read(); };
  });
  // ふつうの欄（data-q = W.q の鍵）
  $$('#swMain [data-q]').forEach(el => {
    const k = el.dataset.q, typing = el.tagName === 'INPUT' && el.type !== 'checkbox';
    el.addEventListener(typing ? 'input' : 'change', () => { q[k] = el.type === 'checkbox' ? el.checked : el.value; changed(typing ? 400 : 150); });
  });
  // 記録した項目（data-fk = 項目の key、data-fp = 条件の部分）
  $$('#swMain [data-fk]').forEach(el => {
    const key = el.dataset.fk, part = el.dataset.fp, typing = el.tagName === 'INPUT' && el.type !== 'checkbox';
    el.addEventListener(typing ? 'input' : 'change', () => {
      const c = q.fields[key] = q.fields[key] || {};
      if (part === 'in') c.in = $$(`#swMain input[data-fk="${key}"][data-fp="in"]`).filter(x => x.checked).map(x => x.value);
      else c[part] = el.type === 'checkbox' ? el.checked : el.value;
      changed(typing ? 400 : 150);
    });
  });
}
function wireTop() {
  $('#swClear').onclick = () => { setFilter({}); changed(0); };
  $('#swClose').onclick = () => window.close();
  $('#svSel').onchange = renderSaved;
  $('#svLoad').onclick = () => {
    const r = W.saved.find(x => x.name === $('#svSel').value);
    if (!r) return;
    const f = Object.assign({}, r.filter);
    if (f.adv && typeof f.adv === 'object') f.adv = advPack(f.adv);
    $('#svName').value = r.name;
    setFilter(f); changed(0);
    toast(`${t('adv_recalled')}: ${r.name}`);
  };
  $('#svDel').onclick = async () => {
    const name = $('#svSel').value;
    if (!name) { toast(t('adv_need_pick'), 'error'); return; }
    if (!await confirmBox(t('adv_delete_q'), name, t('delete'), true)) return;
    try { W.saved = (await api('/api/search/saved/delete', { name })).searches || []; renderSaved(); toast(`${t('adv_deleted')}: ${name}`); } catch (e) { toast(e.message, 'error'); }
  };
  const save = async () => {
    const name = $('#svName').value.trim(), f = filterFromQ(W.q);
    if (!name) { toast(t('adv_need_name'), 'error'); $('#svName').focus(); return; }
    if (!advDescribe(f, ctx()).length) { toast(t('adv_need_cond'), 'error'); return; }
    if (W.saved.some(r => r.name === name) && !await confirmBox(t('adv_overwrite_q'), name, t('adv_overwrite'))) return;
    try {
      W.saved = (await api('/api/search/saved', { name, filter: f })).searches || [];
      renderSaved(); $('#svSel').value = name; renderSaved();
      toast(`${t('adv_saved_done')}: ${name}`);
    } catch (e) { toast(e.message, 'error'); }
  };
  $('#svSave').onclick = save;
  $('#svName').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
}

// ---------- 条件が変わったら: 本体へ送り、当てはまる数を数える ----------
function setFilter(f) { W.q = qFromFilter(f); render(); }
function changed(ms) {
  renderChips(); marks();
  clearTimeout(W.pushTimer);
  W.pushTimer = setTimeout(push, ms === undefined ? 150 : ms);
}
function queryLength(f) {      // 本体が一覧を読むときの URL の長さ（おおよそ）
  return Object.entries(f).filter(([, v]) => advHas(v)).reduce((n, [k, v]) => n + encodeURIComponent(k).length + encodeURIComponent(v).length + 2, 80);
}
function push() {
  W.pushTimer = null;
  if (!W.project || W.gone) return;
  const f = filterFromQ(W.q), key = advCanon(f);
  if (key === W.lastKey) return;
  if (queryLength(f) > W.urlMax) { W.seq++; renderCount(t('adv_too_long')); return; }      // 長すぎる条件は送らない（一覧が読めなくなるため）
  W.lastKey = key;
  if (W.chan) {
    post({ t: 'apply', filter: f });
    clearTimeout(W.ackTimer);
    W.ackTimer = setTimeout(unbind, 2500);      // 本体から返事（state）が来なければ、つながっていない（次に返事をした本体を相手にする）
  }
  count(f);
}
async function count(f) {
  if (!W.project || W.gone) return;
  const seq = ++W.seq;
  $('#swCount').classList.add('busy');
  try {
    const r = await api('/api/search/count', { filters: f || filterFromQ(W.q) });
    if (seq !== W.seq) return;
    W.total = r.total; W.all = r.all; W.countAt = Date.now();
    renderCount();
  } catch (e) { if (seq === W.seq && !W.gone) renderCount(e.message); }
}

// ---------- 本体とのやりとり ----------
/* 相手にする本体の窓は 1 つ（W.bind = その窓の印 mid）。ボタンで開かれたときは、開いた本体の印が URL の # に入っている。
   印がないとき（この窓だけ読み直した）・相手が閉じたときは、最初に返事をした本体を相手にする。本体の窓が 2 つあっても、入力が行き来で消えない */
function post(msg) {
  if (!W.chan) return;
  try { W.chan.postMessage(Object.assign({ to: W.bind || '' }, msg)); } catch (e) { console.warn(e); }
}
function unbind() { W.bind = ''; W.mid = null; setLinked(false); }
function adopt(f) {      // 本体の絞り込みに画面を合わせる（本体の側で変わったとき）
  clearTimeout(W.pushTimer); W.pushTimer = null;
  W.keys = [...new Set(ADV_KEYS.concat(Object.keys(f || {}).filter(k => k !== 'ids')))];
  W.q = qFromFilter(f || {});
  W.lastKey = advCanon(f || {});
  render();
  count(f || {});
}
async function onState(m) {
  if (W.gone || (W.bind && m.mid !== W.bind)) return;      // ほかの本体の窓の知らせは聞かない
  W.bind = m.mid;
  clearTimeout(W.ackTimer);
  setLinked(true);
  if ((m.project || '') !== (W.project ? W.project.dir : '') || (m.lang && m.lang !== LANG)) {      // 本体が別のプロジェクトを開いた・言語を変えた
    try { applyInfo(await fetchInfo()); } catch (e) { console.warn(e); }
    W.mid = null;
  }
  if (m.mid !== W.mid || m.rev !== W.rev) { W.mid = m.mid; W.rev = m.rev; adopt(m.filter); }
  else if (!W.pushTimer && Date.now() - W.countAt > 1500) count(filterFromQ(W.q));      // 本体が一覧を読み直した（確定・削除・裏の処理）→ 数え直す
}
function openChannel() {
  const fromHash = () => { try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return ''; } };
  W.bind = fromHash();
  if (typeof BroadcastChannel !== 'function') { setLinked(false); count(); return; }
  W.chan = new BroadcastChannel(ADV_CHANNEL);
  W.chan.onmessage = ev => {
    const m = ev.data || {};
    if (m.t === 'state') W.work = W.work.then(() => onState(m)).catch(e => console.warn(e));
    else if (m.t === 'bye' && m.mid && m.mid === W.bind) { unbind(); W.byeAt = Date.now(); setTimeout(ping, 700); setTimeout(ping, 1800); }
  };
  window.addEventListener('hashchange', () => { W.bind = fromHash(); W.mid = null; post({ t: 'hello' }); });      // 別の本体の窓のボタンから呼ばれた
  post({ t: 'hello' });
  setTimeout(() => { if (!W.linked) { unbind(); post({ t: 'hello' }); if (W.total === null) count(); } }, 1200);      // 印の本体から返事がない → どの本体でもよいので呼ぶ
  setInterval(() => { if (!W.linked && !W.gone) post({ t: 'hello' }); }, 2500);
}

// ---------- サーバーから読む ----------
async function fetchInfo() {
  const snap = await api('/api/state');
  let saved = [];
  if (snap.project && snap.project.root) { try { saved = (await api('/api/search/saved')).searches || []; } catch (e) { saved = []; } }
  return { snap, saved };
}
function applyInfo(info) {      // 読んだものを W に入れる（入力と入れ違わないように、ここは一息に）。前と変わっていたら true
  const snap = info.snap;
  W.infoAt = Date.now();
  if (!W.build) W.build = snap.build;
  LANG = (snap.settings && snap.settings.language) || 'ja';
  W.edition = snap.edition || 'research';      // public / lite / research（サーバーが返さなければ research）
  W.project = snap.project && snap.project.root ? snap.project : null;
  W.facets = snap.facets || {};
  W.species = snap.species || { species: [], fixed: [] };
  W.fields = ((snap.fields || {}).fields || []).slice();
  W.spMap = {};
  (W.species.species || []).concat(W.species.fixed || []).forEach(s => { W.spMap[s.code] = s; });
  W.saved = W.project ? info.saved : [];
  const f = W.facets;
  const key = JSON.stringify([LANG, W.edition, W.project && [W.project.dir, W.project.name], f.stations, f.seasons, f.batches, f.assignees, f.by_station, f.species_counts, (f.folders || []).map(x => x.folder), W.species, W.fields, W.saved]);
  const ch = key !== W.infoKey;
  W.infoKey = key;
  return ch;
}
function refreshInfo() {      // 地点・種・項目の候補を読み直す（窓に戻ってきたとき）。変わっていたら描き直す。本体からの知らせと同じ列に並べて、入れ違わないようにする
  W.work = W.work.then(async () => {
    if (W.gone) return;
    const info = await fetchInfo();
    if (W.pushTimer) return;                // 入力の途中なら今回は見送る（次に窓に戻ったときに読み直す）
    const cur = filterFromQ(W.q);           // いまの項目の定義で絞り込みに直してから、定義を入れ替える
    if (applyInfo(info)) { W.q = qFromFilter(cur); render(); }
  }).catch(() => { /* 終了中など */ });
  return W.work;
}
async function probeUrl() {      // サーバーが長い URL を受けるか確かめる（受けなければ、一覧に送る条件の長さを抑える）
  try { const r = await fetch('/api/ping?pad=' + 'x'.repeat(20000), { cache: 'no-store' }); W.urlMax = r.ok ? 200000 : 7000; } catch (e) { W.urlMax = 7000; }
}
async function ping() {      // サーバーが止まった・起動し直した・版（edition）が変わった、を見る
  try {
    const r = await fetch('/api/ping', { cache: 'no-store' });
    const d = await r.json();
    W.pingFail = 0;
    if (W.gone || (W.build && d.build && d.build !== W.build)) { location.reload(); return; }
    if (d.edition && d.edition !== W.edition) refreshInfo();
  } catch (e) {
    if (++W.pingFail < 2 || W.gone) return;
    W.gone = true;
    document.body.innerHTML = `<div class="sw-msg">${esc(t('adv_quit'))}</div>`;
    if (W.byeAt && Date.now() - W.byeAt < 20000) setTimeout(() => window.close(), 800);      // 本体を終了したとき（本体の窓も閉じた）は、この窓も閉じる
  }
}
function savePos() { try { localStorage.setItem('tn_adv_win', JSON.stringify({ w: window.innerWidth, h: window.innerHeight, x: window.screenX, y: window.screenY })); } catch (e) { /* */ } }

async function init() {
  W.q = emptyQ();
  wireTop();
  try { applyInfo(await fetchInfo()); } catch (e) { toast(e.message, 'error'); }
  render();
  openChannel();
  probeUrl();
  setInterval(ping, 3000);
  window.addEventListener('focus', () => { if (Date.now() - W.infoAt > 4000) refreshInfo(); });
  window.addEventListener('pagehide', savePos);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('#swModal').classList.contains('hidden') && e.target && e.target.blur && e.target !== document.body) e.target.blur(); });
}
init();
