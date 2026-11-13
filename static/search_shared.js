'use strict';
/* 高度な検索: 本体の画面（search_main.js）と別窓（search.js）の両方が使う部分。
   絞り込みの鍵の一覧、adv（JSON の文字列。形は trapnote/search.py の先頭）の読み書き、条件を文にする（「いまの条件」の表示）、条件を 1 つ外す。
   t() / fdLabel() は i18n.js、文字列は i18n_search.js */

const ADV_SIMPLE_KEYS = ['station', 'season', 'folder', 'folder_prefix', 'status', 'kind', 'species', 'species_src', 'date_from', 'date_to', 'assignee', 'flag', 'missing', 'text',
  'include_missing', 'favorite', 'corrected', 'det', 'agree', 'exclude', 'boxes', 'batch'];      // 簡易の絞り込みの鍵（server.py の FILTER_KEYS）
const ADV_KEYS = ADV_SIMPLE_KEYS.concat(['adv']);
const ADV_PARTS = ['station', 'season', 'species', 'time', 'duration', 'boxes', 'conf', 'fields'];           // adv の中の項目（この順に書く）
const ADV_CHANNEL = 'trapnote-adv';      // 本体と別窓のやりとり（BroadcastChannel の名前）

function advHas(v) { return v !== undefined && v !== null && v !== '' && v !== false && v !== 'all'; }
function advOn(v) { return v === true || v === 1 || v === '1' || v === 'true'; }
/* adv を読む: 空なら {}、読めなければ null */
function advParse(s) {
  if (!advHas(s)) return {};
  if (typeof s === 'object') return Array.isArray(s) ? null : s;
  try { const d = JSON.parse(s); return d && typeof d === 'object' && !Array.isArray(d) ? d : null; } catch (e) { return null; }
}
/* adv を決まった順の JSON の文字列に（条件がなければ ''）。知らない項目はそのまま残す */
function advPack(a) {
  if (!a || typeof a !== 'object') return '';
  const out = { v: a.v || 1 };
  ADV_PARTS.forEach(k => { const v = a[k]; if (v && !(Array.isArray(v) && !v.length)) out[k] = v; });
  Object.keys(a).sort().forEach(k => { if (k !== 'v' && !ADV_PARTS.includes(k) && advHas(a[k])) out[k] = a[k]; });
  return Object.keys(out).length > 1 ? JSON.stringify(out) : '';
}
/* 簡易の絞り込みで地点・調査回・種・枠の数を選んでいれば、adv の同じ項目は外す（1 つの項目に条件は 1 つ。簡易の欄で選んだ方が効く）。変えたら true */
function advNormalize(f) {
  if (!advHas(f.adv)) return false;
  const a = advParse(f.adv);
  if (!a) return false;
  let ch = false;
  ['station', 'season', 'species', 'boxes'].forEach(k => { if (advHas(f[k]) && a[k]) { delete a[k]; ch = true; } });
  if (ch) f.adv = advPack(a);
  return ch;
}
/* 絞り込みを比べるための文字列（空の鍵は数えない。adv は決まった順に直す） */
function advCanon(f) {
  const o = {};
  Object.keys(f || {}).sort().forEach(k => {
    const v = f[k];
    if (!advHas(v) || k === 'ids') return;
    if (k === 'adv') { const a = advParse(v); o[k] = a ? advPack(a) : String(v); if (!o[k]) delete o[k]; } else o[k] = String(v);
  });
  return JSON.stringify(o);
}

// ---------- 条件を文にする ----------
function advT(key, fallback) { const s = t(key); return s === key ? fallback : s; }
function advList(names, max, sep) {      // 「A, B, C ほか 2」
  max = max || 4;
  return names.length <= max ? names.join(sep || ', ') : `${names.slice(0, max).join(sep || ', ')} ${t('adv_more').replace('{n}', names.length - max)}`;
}
function advRangeText(lo, hi, unit) {
  const u = unit || '', a = advHas(lo) || lo === 0, b = advHas(hi) || hi === 0;
  if (a && b) return String(lo) === String(hi) ? `${lo}${u}` : `${lo}${t('adv_to')}${hi}${u}`;
  if (a) return t('adv_ge').replace('{v}', lo + u);
  if (b) return t('adv_le').replace('{v}', hi + u);
  return '';
}
function advSpeciesText(v, src, sp) {
  const pre = src === 'ai' ? `${t('sp_src_ai_short')}: ` : '';
  if (v === 'none') return pre + t('sp_none');
  if (v === 'any') return pre + t('sp_any');
  if (v === 'multi') return pre + t('sp_multi');
  const all = v.includes('+');
  return pre + advList(v.split(all ? '+' : ',').map(x => x.trim()).filter(Boolean).map(sp), 6, all ? ' + ' : ' / ');
}
function advBoxesText(v) {
  const m = /^(\d+)(\+|-(\d+))?$/.exec(String(v));
  if (!m) return String(v);
  return m[2] === '+' ? advRangeText(m[1], '') : advRangeText(m[1], m[3] !== undefined ? m[3] : m[1]);
}
/* いま付いている条件の一覧: [{k, text, own}]。k = 条件の名前（advRemove に渡す）、own = 本体の「絞り込み中」の表示（app.js の activeFilterNames）が自分では出さないもの。
   ctx = { sp: 種コード → 名前, fd: 項目の key → 項目の定義 } */
function advDescribe(f, ctx) {
  f = f || {};
  const out = [];
  const add = (k, text, own) => { if (text) out.push({ k, text, own: !!own }); };
  const sp = c => (ctx && ctx.sp ? ctx.sp(c) : c);
  const lab = (key, v) => `${t(key)}: ${v}`;
  if (advHas(f.station)) add('station', lab('adv_station', f.station));
  if (advHas(f.season)) add('season', lab('adv_season', f.season));
  if (advHas(f.batch)) add('batch', lab('filter_batch', f.batch));
  if (advHas(f.folder_prefix)) add('folder_prefix', lab('adv_folder', f.folder_prefix));
  if (advHas(f.folder)) add('folder', lab('adv_folder', f.folder + t('adv_folder_only')), true);
  if (advHas(f.status)) add('status', lab('adv_status', advT('st_' + f.status, f.status)));
  if (advHas(f.kind)) add('kind', lab('adv_kind', f.kind === 'video' ? t('adv_kind_video') : f.kind === 'image' ? t('adv_kind_image') : f.kind), true);
  if (advHas(f.species)) add('species', lab('adv_species', advSpeciesText(String(f.species), f.species_src, sp)));
  if (advHas(f.date_from) || advHas(f.date_to)) add('date', lab('adv_date', `${String(f.date_from || '').replace(/-/g, '/')}${t('adv_to')}${String(f.date_to || '').replace(/-/g, '/')}`), true);
  if (advOn(f.corrected)) add('corrected', t('adv_corrected'), true);
  if (advHas(f.assignee)) add('assignee', lab('adv_assignee', f.assignee));
  if (advHas(f.text)) add('text', lab('adv_text', f.text));
  if (advOn(f.favorite)) add('favorite', t('adv_fav'));
  if (advHas(f.flag)) add('flag', f.flag === 'low' ? t('det_low') : f.flag === 'delete' ? t('adv_flag_delete') : lab('adv_flag', f.flag), f.flag !== 'low');
  if (advOn(f.missing)) add('missing', lab('adv_missing', t('adv_missing_only')), true);
  else if (advHas(f.include_missing)) add('missing', lab('adv_missing', t('adv_missing_inc')), true);
  if (advHas(f.det)) add('det', lab('adv_det', advT('det_' + f.det, f.det)));
  if (advHas(f.agree)) add('agree', f.agree === 'differ' ? t('adv_cmp_differ') : advT('cmp_' + f.agree, f.agree));
  if (advHas(f.boxes)) add('boxes', lab('adv_boxes', advBoxesText(f.boxes)));
  if (advOn(f.exclude)) add('exclude', t('adv_excl'));
  const a = advParse(f.adv);
  if (a === null) add('adv.bad', t('adv_bad'), true);
  else {
    [['station', 'adv_station', 'adv_none_station'], ['season', 'adv_season', 'adv_none_season']].forEach(([k, lk, nk]) => {
      const c = a[k];
      if (c && Array.isArray(c.in)) add('adv.' + k, `${t(lk)}${c.not ? t('adv_not_tag') : ''}: ${advList(c.in.map(v => (v === '' ? t(nk) : v)))}`, true);
    });
    const s = a.species;
    if (s && Array.isArray(s.in)) add('adv.species', `${t('adv_species')}${s.not ? t('adv_not_tag') : ''}: ${s.src === 'ai' ? t('sp_src_ai_short') + ': ' : ''}${advList(s.in.map(sp), 6, s.all && !s.not ? ' + ' : ', ')}`, true);
    if (a.time) add('adv.time', lab('adv_time', `${a.time.from || ''}${t('adv_to')}${a.time.to || ''}`), true);
    if (a.duration) add('adv.duration', lab('adv_duration', advRangeText(a.duration.min, a.duration.max)), true);
    if (a.boxes) add('adv.boxes', lab('adv_boxes', advRangeText(a.boxes.min, a.boxes.max)), true);
    if (a.conf) add('adv.conf', lab('adv_conf', advRangeText(a.conf.min, a.conf.max)), true);
    (Array.isArray(a.fields) ? a.fields : []).forEach(c => {
      if (!c || !c.key) return;
      const d = ctx && ctx.fd ? ctx.fd(c.key) : null, parts = [];
      if (Array.isArray(c.in)) parts.push(c.in.join(' / '));
      const r = advRangeText(c.min, c.max);
      if (r) parts.push(r);
      if (advHas(c.text)) parts.push(`${t('adv_contains')} "${c.text}"`);
      if (c.check === true) parts.push(t('adv_yes'));
      if (c.check === false) parts.push(t('adv_no'));
      if (c.filled) parts.push(t('adv_filled'));
      if (c.blank) parts.push(t('adv_blank'));
      add(`adv.field:${c.key}|${c.scope}`, `${d ? fdLabel(d) : c.key}: ${parts.join(' / ')}`, true);
    });
    Object.keys(a).forEach(k => { if (k !== 'v' && !ADV_PARTS.includes(k)) add('adv.' + k, `${k}: ${JSON.stringify(a[k])}`, true); });
  }
  Object.keys(f).forEach(k => { if (!ADV_KEYS.includes(k) && k !== 'ids' && advHas(f[k])) add('other:' + k, `${k}: ${f[k]}`); });      // 知らない鍵（あとから増えた絞り込み）
  return out;
}
/* 条件 1 つ（advDescribe の k）を外した絞り込みを返す（元の f は変えない） */
function advRemove(f, k) {
  const g = Object.assign({}, f);
  const m = { species: ['species'], date: ['date_from', 'date_to'], missing: ['missing', 'include_missing'] };
  if (k === 'adv.bad') g.adv = '';
  else if (k.startsWith('adv.')) {
    const a = Object.assign({}, advParse(g.adv) || {});
    if (k.startsWith('adv.field:')) a.fields = (a.fields || []).filter(c => `${c.key}|${c.scope}` !== k.slice(10));
    else delete a[k.slice(4)];
    g.adv = advPack(a);
  } else if (k.startsWith('other:')) g[k.slice(6)] = '';
  else (m[k] || [k]).forEach(x => { g[x] = ''; });
  return g;
}
