'use strict';
/* 判定パネル: 種のチェックボックス（キー付き）、固定項目、種の行ごとの追加項目（＋で増やせる）、対象ごとの追加項目、メモ、確定・取り消し */

function speciesKeyMap() {
  const m = {};
  (S.species.fixed || []).forEach(s => { if (s.key) m[s.key.toUpperCase()] = s.code; });
  (S.species.species || []).forEach(s => { if (s.enabled && s.key) m[s.key.toUpperCase()] = s.code; });
  return m;
}

function loadJudgement(d) {
  S.sel = (d.judgements || []).map(j => j.species);
  S.spFields = {};
  (d.judgements || []).forEach(j => { S.spFields[j.species] = JSON.parse(JSON.stringify(j.fields || {})); });
  S.itemFields = JSON.parse(JSON.stringify(d.fields || {}));
  $('#noteInput').value = d.note || '';
  S.dirty = false;
  renderJudgeBoxes(d);
  renderSpeciesPanel();
  renderFieldsPanel();
  renderJudgeInfo(d);
}

/* 右の欄の上の 2 つの箱: 「人の判定」（緑）と「AI の判定」（青）を同じ作りで縦に並べる。
   人の判定 = 確認済みの判定、または Timelapse / CSV / 手動で付けた未確認の判定。AI の判定 = モデルの判定（auto_json） */
function humanJudgements(d) {
  return (d.judgements || []).filter(j => d.status === 'verified' || j.origin === 'csv' || j.origin === 'manual');
}
function humanSource(d) {
  const js = humanJudgements(d);
  if (d.status === 'verified') return `${t('st_verified')}${d.verified_by ? ' · ' + d.verified_by : ''}${d.recheck ? ' · ' + t('jb_rechecked') : ''}`;
  if (!js.length) return '';
  const m = js[0].model || '';
  const src = js[0].origin === 'csv' ? (/\.(ddb|tdb)$/i.test(m) ? 'Timelapse' : 'CSV') : t('jb_manual');
  return `${src} · ${t('jb_unverified')}`;
}
function renderJudgeBoxes(d) {
  const hb = $('#humanBox'), ab = $('#aiBox');
  const pc = v => (100 * (v || 0)).toFixed(0) + '%';
  // 人の判定
  const hj = humanJudgements(d);
  if (hj.length) {
    const chips = hj.map(j => `<span class="chip human">${esc(spLabel(j.species))}</span>`).join('');
    const hint = d.status === 'verified' ? '' : `<div class="info">${esc(t('auto_accept'))}</div>`;
    hb.innerHTML = `<div class="box-head"><span class="jtag human">${esc(t('cmp_human'))}</span><span>${esc(t('jb_human_title'))}</span><span class="info">${esc(humanSource(d))}</span></div>${chips}${hint}`;
    hb.classList.remove('hidden');
  } else { hb.classList.add('hidden'); hb.innerHTML = ''; }
  // AI の判定
  const aiOwn = !hj.length && (d.judgements || []).length && (d.judgements || []).every(j => j.origin === 'auto');   // 判定パネルに入っているのが AI の判定そのもの
  if (d.auto || aiOwn) {
    const list = d.auto || (d.judgements || []).map(j => ({ species: j.species, confidence: j.confidence }));
    const low = d.status !== 'verified' && d.flag === 'low';
    const chips = list.length ? list.map(a => `<span class="chip ai">${esc(spLabel(a.species))}${a.confidence != null ? ' ' + pc(a.confidence) : ''}</span>`).join('')
      : `<span class="chip ai low">${esc(t('det_low'))}</span>`;
    const mark = hj.length ? (d.agree === 1 ? `<span class="aimark ok">${esc(t('cmp_agree'))}</span>` : d.agree === 2 ? `<span class="aimark part">${esc(t('cmp_partial'))}</span>` : d.agree === 3 ? `<span class="aimark ng">${esc(t('cmp_disagree'))}</span>` : '') : '';
    const model = (d.auto_model || (d.cls && d.cls.model) || (d.det && d.det.model) || '').replace(/^kaggle:google\/speciesnet\/pyTorch\//, 'SpeciesNet ').replace(/\/1 \+ /, ' + ');
    const lines = [];
    if (d.det) {
      const x = d.det;
      const parts = [`${t('det_animal')} ${x.animal_frames}/${x.n_frames} (${pc(x.max_animal)})`];
      if (x.person_frames) parts.push(`${t('det_person')} ${x.person_frames} (${pc(x.max_person)})`);
      if (x.vehicle_frames) parts.push(`${t('det_vehicle')} ${x.vehicle_frames} (${pc(x.max_vehicle)})`);
      lines.push(`${esc(x.model || 'MegaDetector')}: ${esc(parts.join(' · '))}`);
    }
    if (d.cls && d.cls.probs) {
      const top = Object.entries(d.cls.probs).filter(([c, v]) => v >= 0.05).slice(0, 4).map(([c, v]) => `${spLabel(c)} ${pc(v)}`);
      if (top.length) lines.push(`SpeciesNet (${d.cls.n_crops || 0}): ${esc(top.join(' · '))}`);
    }
    const lowNote = low ? `<div class="flag-low">${esc(t(list.some(a => a.species === 'unknown') ? 'det_low_note2' : 'det_low_note'))}</div>` : '';
    const hint = (!hj.length && d.status !== 'verified') ? `<div class="info">${esc(list.length ? t('auto_accept') : t('det_low_accept'))}</div>` : '';
    const details = (model || lines.length) ? `<details class="jdet"><summary>${esc(t('jb_details'))}</summary>${model ? `<div class="info">${esc(model)}</div>` : ''}${lines.map(l => `<div class="info">${l}</div>`).join('')}</details>` : '';
    ab.innerHTML = `<div class="box-head"><span class="jtag ai">AI</span><span>${esc(t('jb_ai_title'))}</span>${mark}${details}</div>${chips}${lowNote}${hint}`;
    ab.classList.remove('hidden');
  } else { ab.classList.add('hidden'); ab.innerHTML = ''; }
}

function renderSpeciesPanel() {
  const grid = $('#speciesGrid'), fixed = $('#fixedRow');
  const enabled = (S.species.species || []).filter(s => s.enabled);
  const extra = S.sel.filter(c => { const s = spByCode(c); return s && !s.fixed && !s.enabled; }).map(c => spByCode(c));   // 「その他」から選んだ種
  const row = s => `<label class="${S.sel.includes(s.code) ? 'on' : ''}" data-code="${esc(s.code)}"><input type="checkbox" ${S.sel.includes(s.code) ? 'checked' : ''}> ${s.key ? `<span class="k">${esc(s.key)}</span>` : ''}<span>${esc(spName(s))}</span>${s.non_native ? '<span class="nn">*</span>' : ''}</label>`;
  grid.innerHTML = enabled.concat(extra).map(row).join('');
  fixed.innerHTML = (S.species.fixed || []).map(row).join('');
  grid.querySelectorAll('label').forEach(l => { l.onclick = e => { e.preventDefault(); toggleSpecies(l.dataset.code); }; });
  fixed.querySelectorAll('label').forEach(l => { l.onclick = e => { e.preventDefault(); toggleSpecies(l.dataset.code); }; });
}

function toggleSpecies(code) {
  if (!S.cur) return;
  const i = S.sel.indexOf(code);
  if (i >= 0) S.sel.splice(i, 1);
  else {
    if (code === 'empty') S.sel = [];                 // 動物なしはほかと一緒に付けない
    else S.sel = S.sel.filter(c => c !== 'empty');
    S.sel.push(code);
    if (!S.spFields[code]) S.spFields[code] = defaultSpFields();
  }
  S.dirty = true;
  renderSpeciesPanel(); renderFieldsPanel();
}
function defaultSpFields() {
  const out = {};
  (S.fields.fields || []).filter(f => f.scope === 'species' && !f.repeat && f.default).forEach(f => { out[f.key] = [f.default]; });
  return out;
}

// ---- 追加項目 ----
function fieldInput(f, value, onchange, idAttr) {
  const v = value ?? '';
  const common = `${idAttr || ''}`;
  if (f.type === 'select') return `<select ${common}><option value=""></option>${f.options.map(o => `<option value="${esc(o)}" ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`;
  if (f.type === 'multiselect') { const set = new Set(String(v).split(';').filter(Boolean)); return `<span class="ms" ${common}>${f.options.map(o => `<label><input type="checkbox" value="${esc(o)}" ${set.has(o) ? 'checked' : ''}>${esc(o)}</label>`).join('')}</span>`; }
  if (f.type === 'check') return `<input type="checkbox" ${common} ${v === '1' || v === 'true' ? 'checked' : ''}>`;
  if (f.type === 'number') return `<input type="number" step="any" ${common} value="${esc(v)}">`;
  if (f.type === 'time') return `<span style="display:flex;gap:4px"><input type="number" step="0.01" min="0" ${common} value="${esc(v)}"><button class="small now" type="button" title="${esc(t('now_time'))}">⏱</button></span>`;
  return `<input type="text" ${common} value="${esc(v)}" spellcheck="false">`;
}
function readInput(f, el) {
  if (!el) return '';
  if (f.type === 'multiselect') return [...el.querySelectorAll('input:checked')].map(i => i.value).join(';');
  if (f.type === 'check') return el.checked ? '1' : '';
  return el.value;
}
function bindField(container, f, get, set) {
  const el = container.querySelector(`[data-f="${f.key}"]`);
  if (!el) return;
  const ev = (f.type === 'text' || f.type === 'number' || f.type === 'time') ? 'input' : 'change';
  if (f.type === 'multiselect') el.querySelectorAll('input').forEach(i => i.addEventListener('change', () => { set(readInput(f, el)); S.dirty = true; }));
  else el.addEventListener(ev, () => { set(readInput(f, el)); S.dirty = true; });
  const now = el.parentElement && el.parentElement.querySelector('button.now');
  if (now) now.onclick = () => { const s = currentTimeSec(); if (s != null) { el.value = s.toFixed(2); set(el.value); S.dirty = true; } };
}
function groupsOf(fields) {   // ＋の項目を「組」ごとにまとめる（組がなければ項目ごと）
  const groups = [];
  fields.filter(f => f.repeat).forEach(f => { const g = f.group || f.key; let gr = groups.find(x => x.name === g); if (!gr) { gr = { name: g, fields: [] }; groups.push(gr); } gr.fields.push(f); });
  return groups;
}
function renderRepeat(store, group) {
  const n = Math.max(0, ...group.fields.map(f => (store[f.key] || []).length));
  const rows = [];
  for (let i = 0; i < n; i++) rows.push(`<tr data-i="${i}">${group.fields.map(f => `<td>${fieldInput(f, (store[f.key] || [])[i], null, `data-f="${f.key}" data-i="${i}"`)}</td>`).join('')}<td class="x"><button type="button" class="del" title="×">×</button></td></tr>`);
  const head = group.fields.map(f => `<th>${esc(fdLabel(f))}</th>`).join('') + '<th></th>';
  return `<div class="rep-head"><span>${esc(fdLabel(group.fields[0]))}</span><button type="button" class="small add">${esc(t('add_record'))}</button></div>
    <table class="rep"><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}
function bindRepeat(container, store, group, rerender) {
  container.querySelector('button.add').onclick = () => {
    group.fields.forEach(f => { store[f.key] = store[f.key] || []; });
    const n = Math.max(0, ...group.fields.map(f => store[f.key].length));
    group.fields.forEach(f => { while (store[f.key].length < n) store[f.key].push(''); store[f.key].push(f.default || ''); });
    S.dirty = true; rerender();
    const last = container.querySelector('tbody tr:last-child input, tbody tr:last-child select'); if (last) last.focus();
  };
  container.querySelectorAll('tbody tr').forEach(tr => {
    const i = parseInt(tr.dataset.i);
    group.fields.forEach(f => {
      const el = tr.querySelector(`[data-f="${f.key}"]`);
      if (!el) return;
      const ev = (f.type === 'text' || f.type === 'number' || f.type === 'time') ? 'input' : 'change';
      const set = () => { store[f.key] = store[f.key] || []; while (store[f.key].length <= i) store[f.key].push(''); store[f.key][i] = readInput(f, el); S.dirty = true; };
      if (f.type === 'multiselect') el.querySelectorAll('input').forEach(x => x.addEventListener('change', set)); else el.addEventListener(ev, set);
      const now = el.parentElement && el.parentElement.querySelector('button.now');
      if (now) now.onclick = () => { const s = currentTimeSec(); if (s != null) { el.value = s.toFixed(2); set(); } };
    });
    tr.querySelector('button.del').onclick = () => { group.fields.forEach(f => { if (store[f.key]) store[f.key].splice(i, 1); }); S.dirty = true; rerender(); };
  });
}
function renderFieldsPanel() {
  const fields = S.fields.fields || [];
  const spF = fields.filter(f => f.scope === 'species'), itF = fields.filter(f => f.scope === 'item');
  // 種の行ごと
  const host = $('#spFields');
  host.innerHTML = '';
  const addBtn = scope => `<button type="button" class="small right addf" data-scope="${scope}" title="${esc(t('fd_add_tip'))}">${esc(t('fd_add_short'))}</button>`;
  S.sel.filter(c => !['empty', 'area'].includes(c)).forEach(code => {
    const store = S.spFields[code] = S.spFields[code] || {};
    const div = document.createElement('div'); div.className = 'box spf';
    const simple = spF.filter(f => !f.repeat);
    div.innerHTML = `<div class="box-head">${esc(spLabel(code))}${addBtn('species')}</div>` +
      simple.map(f => `<div class="fld" title="${esc(f.help || '')}"><span>${esc(fdLabel(f))}</span>${fieldInput(f, (store[f.key] || [])[0] ?? f.default, null, `data-f="${f.key}"`)}</div>`).join('') +
      groupsOf(spF).map(g => `<div class="repwrap" data-g="${esc(g.name)}">${renderRepeat(store, g)}</div>`).join('');
    simple.forEach(f => bindField(div, f, () => (store[f.key] || [])[0], v => { store[f.key] = [v]; }));
    groupsOf(spF).forEach(g => bindRepeat(div.querySelector(`.repwrap[data-g="${g.name}"]`), store, g, renderFieldsPanel));
    host.appendChild(div);
  });
  // 対象ごと（項目がなくても「＋ 項目」のために箱は出す）
  const ib = $('#itemFields');
  ib.classList.remove('hidden');
  const simple = itF.filter(f => !f.repeat);
  ib.innerHTML = `<div class="box-head">${esc(t('fields_item'))}${addBtn('item')}</div>` +
    simple.map(f => `<div class="fld" title="${esc(f.help || '')}"><span>${esc(fdLabel(f))}</span>${fieldInput(f, (S.itemFields[f.key] || [])[0] ?? f.default, null, `data-f="${f.key}"`)}</div>`).join('') +
    groupsOf(itF).map(g => `<div class="repwrap" data-g="${esc(g.name)}">${renderRepeat(S.itemFields, g)}</div>`).join('');
  simple.forEach(f => bindField(ib, f, () => (S.itemFields[f.key] || [])[0], v => { S.itemFields[f.key] = [v]; }));
  groupsOf(itF).forEach(g => bindRepeat(ib.querySelector(`.repwrap[data-g="${g.name}"]`), S.itemFields, g, renderFieldsPanel));
  document.querySelectorAll('#spFields button.addf, #itemFields button.addf').forEach(b => { b.onclick = () => addFieldDialog(b.dataset.scope); });
}
function renderJudgeInfo(d) {
  const el = $('#judgeInfo');
  const parts = [`${t('st_' + d.status)}`];
  if (d.verified_by) parts.push(`${d.verified_by} · ${(d.verified_at || '').replace('T', ' ').slice(0, 16)}`);
  if (d.assignee) parts.push(`${t('assign')}: ${d.assignee}`);
  el.textContent = parts.join(' · ');
}

// ---- 保存 ----
function cleanFields(store) {
  const out = {};
  Object.entries(store || {}).forEach(([k, vals]) => { const arr = (Array.isArray(vals) ? vals : [vals]).map(v => v == null ? '' : String(v)); if (arr.some(v => v !== '')) out[k] = arr; });
  return out;
}
async function confirmAndNext() {
  if (!S.cur || S.busy) return;
  if (!S.sel.length) { toast(t('need_species'), 'error'); return; }
  if (S.picked.size) return confirmSelection();          // 選択中: 同じ判定を選択した全部に付ける
  S.busy = true;
  try {
    const spf = {}; S.sel.forEach(c => { spf[c] = cleanFields(S.spFields[c]); });
    const res = await api(`/api/items/${S.cur.id}/save`, { species: S.sel, fields: cleanFields(S.itemFields), species_fields: spf, note: $('#noteInput').value });
    S.cur.status = 'verified'; S.cur.verified_by = res.state.verified_by; S.cur.verified_at = res.state.verified_at; S.cur.judgements = res.state.judgements;
    patchRow({ id: S.cur.id, status: 'verified', judgements: res.state.judgements, note: res.state.note, agree: res.agree == null ? null : res.agree });
    if (S.cur) { S.cur.agree = res.agree; S.cur.status = 'verified'; S.cur.judgements = res.state.judgements; S.cur.verified_by = S.settings.user_name || ''; renderJudgeBoxes(S.cur); }
    renderJudgeInfo(S.cur); renderJudgeBoxes(S.cur);
    toast(t('saved'));
    S.busy = false;
    if (S.facets && S.facets.counts) refreshFacets();
    if (S.settings.auto_advance !== false) {
      // 絞り込みが「未処理」などで、確定した対象が一覧から外れるときは、位置が 1 つずれるので同じ位置を開く
      const dropsOut = (S.filter.status === 'new' || S.filter.status === 'auto' || S.filter.status === 'unverified');
      if (dropsOut) { const idx = S.curIndex; await reloadList(true); const r = await rowAtAsync(Math.min(idx, S.total - 1)); if (r && r.id !== S.cur.id) openItem(r.id, Math.min(idx, S.total - 1)); else if (!r) toast(t('last_item')); }
      else goStep(1);
    }
  } catch (e) { S.busy = false; toast(e.message, 'error'); }
}
/* 選択した全部に、いまの判定（種・項目・メモ）を付けて確認済みにする */
async function confirmSelection() {
  const ids = selectionIds();
  if (!await confirmModal(t('sel_judge_title'), `<div>${ids.length} ${t('items')}: ${esc(S.sel.map(c => spLabel(c)).join(', '))}</div><div class="note">${esc(t('sel_judge_help'))}</div>`, t('confirm'))) return;
  S.busy = true;
  try {
    const spf = {}; S.sel.forEach(c => { spf[c] = cleanFields(S.spFields[c]); });
    const r = await api('/api/items/bulk', { ids, action: 'judge', payload: { species: S.sel, fields: cleanFields(S.itemFields), species_fields: spf, note: $('#noteInput').value } });
    toast(`${r.n} ${t('sel_judged')}`);
    S.busy = false;
    clearSelection(false);
    await refreshFacets(); await reloadList(true);
    if (S.cur) refreshCurrent();
  } catch (e) { S.busy = false; toast(e.message, 'error'); }
}
async function clearJudgement() {
  if (!S.cur || S.busy) return;
  if (S.picked.size) {
    const ids = selectionIds();
    if (!await confirmModal(t('clear_judgement'), `<div>${ids.length} ${t('items')}</div>`, t('clear_judgement'), true)) return;
    try { const r = await api('/api/items/bulk', { ids, action: 'clear' }); toast(`${r.n} ${t('cleared')}`); clearSelection(false); await refreshFacets(); await reloadList(true); if (S.cur) refreshCurrent(); } catch (e) { toast(e.message, 'error'); }
    return;
  }
  try {
    const res = await api(`/api/items/${S.cur.id}/save`, { species: [], status: 'new', note: $('#noteInput').value });
    S.cur.status = 'new'; S.cur.judgements = []; S.cur.verified_by = null;
    patchRow({ id: S.cur.id, status: 'new', judgements: [] });
    loadJudgement(Object.assign(S.cur, res.state, { judgements: [] }));
    refreshFacets(); toast(t('cleared'));
  } catch (e) { toast(e.message, 'error'); }
}
async function undoLast() {
  try {
    const res = await api('/api/undo', {});
    toast(t('undone'));
    await reloadList(true); refreshFacets();
    if (S.cur && S.cur.id === res.item_id) openItem(res.item_id, S.curIndex); else openItem(res.item_id);
  } catch (e) { toast(e.message, 'error'); }
}
// → キー: 種にチェックがあって未保存なら確定してから次へ、なければそのまま次へ（← は前へ）
async function confirmOrStep(step) {
  if (!S.cur || S.busy) return;
  if (S.dirty && S.sel.length) {
    const adv = S.settings.auto_advance;
    S.settings.auto_advance = false;
    try { await confirmAndNext(); } finally { S.settings.auto_advance = adv; }
    if (S.cur.status !== 'verified') return;      // 保存に失敗したら動かない
  }
  goStep(step);
}

async function speciesAddDialog() {
  const m = showModal(`<h3>${esc(t('as_title'))}</h3><div class="note">${esc(t('as_help'))}</div>
    <div class="form-row"><label>${esc(t('sp_ja'))}</label><input type="text" id="asJa"></div>
    <div class="form-row"><label>${esc(t('sp_en'))}</label><input type="text" id="asEn"></div>
    <div class="form-row"><label>${esc(t('sp_sci'))}</label><input type="text" id="asSci"></div>
    <div class="form-row"><label>${esc(t('sp_csv'))}</label><input type="text" id="asCsv" placeholder="(英名から)"></div>
    <div class="form-row"><label>${esc(t('sp_key'))}</label><input type="text" id="asKey" maxlength="1" style="width:40px"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('ok'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  const ok = async () => {
    const row = { name_ja: m.querySelector('#asJa').value.trim(), name_en: m.querySelector('#asEn').value.trim(), name_sci: m.querySelector('#asSci').value.trim(),
      csv_label: m.querySelector('#asCsv').value.trim(), key: m.querySelector('#asKey').value.trim(), rank: '種', enabled: 1 };
    try {
      const r = await api('/api/species/add', row);
      const existed = (S.species.species || []).some(s => s.code === r.added.code);
      S.species = { species: r.species, fixed: r.fixed }; rebuildSpMap();
      closeModal();
      if (existed) toast(t('as_exists'));
      if (S.cur && !S.sel.includes(r.added.code)) toggleSpecies(r.added.code); else { renderSpeciesPanel(); renderFieldsPanel(); }
      renderFilters();
    } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelector('#mOk').onclick = ok;
  m.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); ok(); } }));
}

function wireJudge() {
  $('#btnConfirm').onclick = confirmAndNext;
  $('#btnAddSpecies').onclick = () => S.project && speciesAddDialog();
  $('#btnFav').onclick = toggleFavorite;
  $('#btnTime').onclick = () => S.cur && timeDialog();
  $('#btnClear').onclick = clearJudgement;
  $('#btnUndo').onclick = undoLast;
  $('#btnOtherSpecies').onclick = () => otherSpeciesDialog();
  let noteTimer = null;
  $('#noteInput').addEventListener('input', () => { S.dirty = true; clearTimeout(noteTimer); noteTimer = setTimeout(() => { if (S.cur) api(`/api/items/${S.cur.id}/note`, { note: $('#noteInput').value }).then(() => patchRow({ id: S.cur.id, note: $('#noteInput').value })).catch(() => {}); }, 600); });
}
