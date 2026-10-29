'use strict';
/* 横断プロジェクト（2 階層目）の窓: 5 つの段（手順）
   1. 参照先を登録する（調査回のプロジェクトか NAS の親フォルダ）
   2. 種名の対応表（参照先の種名の表記を、この横断プロジェクトの種リストのどの種と見なすか）
   3. カタログを作る（参照先の全動画の目録。必須。どの段でも窓の下の「カタログを作る」から押せる）
   4. 動画を選んで取り込む（ランダム / 表でチェック / 絞り込みの全部。取り込んだものが一覧と再生の対象になる）
   5. 書き戻し・書き出し（判定の書き戻し・再取得、動画のコピー、一覧 CSV、取り込みの記録） */

let crossModalEl = null;
const CR_F0 = { source: '', season: '', station: '', species: '', status: '', imported: '', text: '', exclude_stations: '', single_only: false, csv_text: '' };
const CR = { tab: '', f: Object.assign({}, CR_F0), counts: null,
  smp: { unit: 'station', n: 20, cap: 5, gap: 10, seed: 1 }, labelQ: '' };
const CR_TABS = ['sources', 'labels', 'build', 'pick', 'sync'];

async function crossDialog(tab) {
  let info;
  try { info = await api('/api/cross/info'); } catch (e) { toast(e.message, 'error'); return; }
  const cs = info.catalog_status || {};
  if (tab && CR_TABS.includes(tab)) CR.tab = tab;
  else {                                   // 段を指定せずに開いたときは、いまの状態で次にやる段を出す
    if (!info.sources.length) CR.tab = 'sources';
    else if (!cs.built || !info.n_catalog) CR.tab = 'build';
    else CR.tab = 'pick';
  }
  const stepStatus = {
    sources: info.sources.length ? `${info.sources.length} ${t('items')}` : t('cr_none_yet'),
    labels: info.n_label_map ? `${info.n_label_map} ${t('cr_n_rules')}` : t('cr_auto_only'),
    build: !cs.built ? t('cr_not_built') : (cs.stale ? `${info.n_catalog} · ${crStaleWord(cs)}` : `${info.n_catalog} ${t('items')}`),
    pick: `${t('cross_items_n')} ${info.n_items}`,
    sync: info.n_sync ? `${info.n_sync} ${t('items')}` : '',
  };
  const warnStep = { sources: !info.sources.length, build: info.sources.length && (!cs.built || cs.stale) };
  const m = showModal(`<h3>${esc(t('cross_title'))} <span class="info">${esc(info.project.name)}</span></h3>
    <div class="note">${esc(t('cr_intro'))}</div>
    <div class="steps" id="crTabs">${CR_TABS.map((k, i) => `<button type="button" class="stp ${CR.tab === k ? 'on' : ''} ${warnStep[k] ? 'warn' : ''}" data-tab="${k}"><span class="n">${i + 1}</span><span class="l">${esc(t('cr_tab_' + k))}</span><span class="s">${esc(stepStatus[k] || '')}</span></button>`).join('')}</div>
    <div id="crBody"></div>
    <div id="crStatus"></div>
    <div class="modal-actions cr-foot"><span class="info" id="crFootInfo">${esc(crCatalogLine(info))}</span>
      <button type="button" id="crBuildFoot" class="${info.sources.length && (!cs.built || cs.stale) ? 'primary' : ''}" ${info.sources.length ? '' : 'disabled'} title="${esc(t('cr_build_tip'))}">${esc(crBuildLabel(cs))}</button>
      <button id="mCancel">${esc(t('close'))}</button></div>`);
  crossModalEl = m; m._info = info;
  m.querySelector('#mCancel').onclick = () => { crossModalEl = null; closeModal(); };
  m.querySelectorAll('#crTabs .stp').forEach(b => { b.onclick = () => { CR.tab = b.dataset.tab; m.querySelectorAll('#crTabs .stp').forEach(x => x.classList.toggle('on', x === b)); crossRenderTab(m); }; });
  m.querySelector('#crBuildFoot').onclick = () => crossBuild(m);
  crossRenderTab(m);
  crossOnJobs(S.snap && S.snap.jobs);
}

// 対応表（か種リスト）だけが変わったときは、作り直さずに表記から種を当て直すだけ（数秒）。参照先が変わったときは作り直し
function crOnlyLabels(cs) { return !!(cs.built && cs.stale && (cs.reasons || []).every(r => r === 'labels')); }
function crStaleWord(cs) { return crOnlyLabels(cs) ? t('cr_remap_needed') : t('cr_rebuild_needed'); }
function crStaleText(cs) { return `${crStaleWord(cs)}（${(cs.reasons || []).map(r => t('cr_stale_' + r)).join('・')}）`; }
function crBuildLabel(cs) { return crOnlyLabels(cs) ? t('cr_remap') : (cs.built ? t('cross_build_again') : t('cross_build')); }

function crCatalogLine(info) {
  const cs = info.catalog_status || {};
  if (!info.sources.length) return t('cr_foot_no_sources');
  if (!cs.built) return t('cr_foot_not_built');
  const when = (cs.built_at || '').slice(0, 16).replace('T', ' ');
  return `${t('cross_catalog_n')} ${info.n_catalog} ${t('items')} · ${when}${cs.stale ? ' · ' + crStaleText(cs) : ''}`;
}

async function crossBuild(m, mode, scanFolders) {
  const cs = (m._info && m._info.catalog_status) || {};
  const body = { mode: mode || (crOnlyLabels(cs) ? 'auto' : 'full') };
  if (scanFolders !== undefined) body.scan_folders = scanFolders;
  try { const j = await api('/api/cross/catalog/build', body); crossTrack(m, j, 'pick'); } catch (e) { toast(e.message, 'error'); }
}

function crossRenderTab(m) {
  const body = m.querySelector('#crBody');
  if (CR.tab === 'sources') crossTabSources(m, body);
  else if (CR.tab === 'labels') crossTabLabels(m, body);
  else if (CR.tab === 'build') crossTabBuild(m, body);
  else if (CR.tab === 'pick') crossTabPick(m, body);
  else crossTabSync(m, body);
}

// ---------- 1. 参照先 ----------
function crossTabSources(m, body) {
  const info = m._info, srcs = info.sources || [];
  const labInfo = s => {
    if (s.kind !== 'tree') return '';
    const l = s.labels || {};
    if (l.error) return `<span class="ng" title="${esc(l.error)}">${esc(t('cross_labels_bad'))}</span>`;
    const files = l.files || [];
    if (!files.length) return `<span class="info">${esc(t('cross_no_labels'))}</span>`;
    if (l.pending && l.paused) return `<span class="warn" title="${esc(files.map(f => f.path).join('\n'))}">${esc(t('cr_labels_paused'))} ${files.length} ${esc(l.auto ? t('cross_labels_auto') : t('cross_labels_given'))} · ${(l.size / 1048576).toFixed(l.size > 10485760 ? 0 : 1)} MB <button type="button" class="small" data-read="${esc(s.name)}">${esc(t('cr_labels_read'))}</button></span>`;
    if (l.pending) return `<span class="warn" title="${esc(files.map(f => f.path).join('\n'))}">${esc(t('cross_labels_reading'))} ${files.length} ${esc(l.auto ? t('cross_labels_auto') : t('cross_labels_given'))} · ${(l.size / 1048576).toFixed(l.size > 10485760 ? 0 : 1)} MB ${esc(t('cross_labels_reading_help'))}</span>`;
    const un = (l.unmapped || []).map(([v, n]) => `${v} (${n})`).join(', ');
    const bad = files.filter(f => f.error);
    const colsTxt = l.cols && Object.keys(l.cols).length ? `${t('cr_cols')}: ${Object.entries(l.cols).map(([k, v]) => `${k} = ${v}`).join(', ')}` : '';
    return `<span class="info" title="${esc(files.map(f => `${f.path} (${f.error || f.n})`).join('\n') + (colsTxt ? '\n' + colsTxt : ''))}">${esc(t('cross_labels'))} ${files.length} ${esc(l.auto ? t('cross_labels_auto') : t('cross_labels_given'))} · ${l.n_here ?? l.n ?? 0} ${esc(t('items'))}${colsTxt ? ` <span class="info">(${esc(colsTxt)})</span>` : ''}${bad.length ? ` <span class="ng">${esc(t('cross_labels_bad'))} ${bad.length}</span>` : ''}${l.n_unmapped ? ` <a href="#" class="ng" data-golabels="1" title="${esc(un)}">${esc(t('cross_unmapped'))} ${l.n_unmapped_labels || ''} → ${esc(t('cr_tab_labels'))}</a>` : ''}</span>`;
  };
  const rows = srcs.map(s => `<tr><td><b>${esc(s.name)}</b> <span class="chip ${s.kind === 'tree' ? 'human' : 'ai'}">${esc(s.kind === 'tree' ? t('cross_kind_tree') : t('cross_kind_project'))}</span>${s.ok ? '' : ` <span class="ng">✗</span>`}</td>
    <td class="info" title="${esc(s.dir || s.root || '')}">${esc(s.ok ? (s.root || '') : (s.error || ''))}${s.ok && !s.available ? ` <span class="ng">${esc(t('cross_unavailable'))}</span>` : ''}${s.kind === 'tree' && s.seasons ? `<div>${esc(t('cross_seasons'))} ${s.seasons.length}${(s.project_seasons || []).length ? ` (${esc(t('cross_proj_seasons'))} ${s.project_seasons.length})` : ''} · ${labInfo(s)}</div>` : ''}</td>
    <td>${s.ok && s.n_items != null ? s.n_items : '—'}</td><td>${s.ok && s.n_verified != null ? s.n_verified : '—'}</td><td>${s.ok && s.n_human != null ? s.n_human : '—'}</td><td>${s.n_imported || 0}</td>
    <td>${s.kind === 'tree' ? `<button type="button" class="small" data-lab="${esc(s.name)}">${esc(t('cross_set_labels'))}</button> ` : ''}<button type="button" class="small" data-rm="${esc(s.name)}">${esc(t('cross_remove'))}</button></td></tr>`).join('');
  body.innerHTML = `<div class="note">${esc(t('cross_sources_help'))}</div>
    ${srcs.length ? `<div style="max-height:30vh;overflow:auto"><table class="tbl cr-src"><tr><th>${esc(t('cross_src_name'))}</th><th>${esc(t('cross_src_root'))}</th><th>${esc(t('items'))}</th><th>${esc(t('st_verified'))}</th><th>${esc(t('cross_human'))}</th><th>${esc(t('cross_imported'))}</th><th></th></tr>${rows}</table></div>
      <div class="note">${esc(t('cr_sources_next'))}</div>` : `<div class="callout">${esc(t('cross_no_sources'))}</div>`}
    <h4>${esc(t('cross_add_src'))} <span class="info">${esc(t('cross_add_src_multi'))}</span></h4>
    <div class="form-row"><label>${esc(t('cross_folder_l'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcPath" spellcheck="false" style="width:380px" placeholder="Y:\\PRM_N"><button type="button" class="small" data-pick="crSrcPath" data-kind="folder" data-name="">${esc(t('pick'))}</button><button type="button" class="small" id="crSrcUp" title="${esc(t('cross_up_tip'))}">${esc(t('cross_up'))}</button></span></div>
    <div class="form-row"><label></label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><button type="button" class="primary small" id="crSrcAddUnder">${esc(t('cross_add_under'))}</button><button type="button" class="small" id="crSrcAdd">${esc(t('cross_add_one'))}</button></span></div>
    <div class="note">${esc(t('cross_add_src_help'))}</div>
    <details class="adv"><summary>${esc(t('cross_add_opts'))}</summary>
      <div class="form-row"><label>${esc(t('cross_src_name'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcName" spellcheck="false" style="width:120px" placeholder="${esc(t('cross_name_ph'))}"> <span class="info">${esc(t('cross_name_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('cross_labels_csv'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcLabels" spellcheck="false" style="width:380px"><button type="button" class="small" data-pick="crSrcLabels" data-kind="csv" data-name="">${esc(t('pick'))}</button> <span class="info">${esc(t('cross_labels_help'))}</span></span></div>
    </details>`;
  wirePicks(m);
  const q = sel => m.querySelector(sel);
  q('#crSrcUp').onclick = () => { const v = q('#crSrcPath').value.trim().replace(/[\\/]+$/, ''); const i = Math.max(v.lastIndexOf('/'), v.lastIndexOf('\\')); if (i > 1) q('#crSrcPath').value = v.slice(0, i); };
  q('#crSrcAdd').onclick = async () => {
    const pth = q('#crSrcPath').value.trim(); if (!pth) return;
    try { const r = await api('/api/cross/sources/add', { path: pth, name: q('#crSrcName').value.trim(), labels_csv: q('#crSrcLabels').value.trim() }); setSnapshot(r.state); toast(`${t('cross_added')}: ${r.name}${r.ignored_cross ? ` · ${t('cross_ignored_cross')}: ${r.ignored_cross}` : ''}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  };
  q('#crSrcAddUnder').onclick = async () => {
    const pth = q('#crSrcPath').value.trim(); if (!pth) return;
    try { const r = await api('/api/cross/sources/add_under', { path: pth, labels_csv: q('#crSrcLabels').value.trim() }); setSnapshot(r.state);
      toast(`${t('cross_added')}: ${r.added.length}${r.skipped.length ? ` (${t('cross_already')} ${r.skipped.length})` : ''}${r.errors.length ? ` · ${r.errors[0]}` : ''}${r.ignored_cross ? ` · ${t('cross_ignored_cross')}: ${r.ignored_cross}` : ''}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelectorAll('[data-lab]').forEach(b => { b.onclick = async () => {
    const pth = q('#crSrcLabels').value.trim() || await pickPath('csv');
    if (!pth) { toast(t('cross_pick_labels'), 'error'); return; }
    try { const r = await api('/api/cross/sources/labels', { name: b.dataset.lab, labels_csv: pth }); toast(r.labels && r.labels.pending ? t('cross_labels_reading') : `${t('cross_labels')} ${r.labels ? (r.labels.n ?? '') : 0}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  }; });
  m.querySelectorAll('[data-rm]').forEach(b => { b.onclick = async () => {
    crossModalEl = null;
    if (await confirmModal(t('cross_title'), `${esc(t('cross_remove_q'))}: ${esc(b.dataset.rm)}`, t('cross_remove'), true)) { try { const r = await api('/api/cross/sources/remove', { name: b.dataset.rm }); setSnapshot(r.state); } catch (e) { toast(e.message, 'error'); } }
    crossDialog('sources');
  }; });
  m.querySelectorAll('[data-read]').forEach(b => { b.onclick = async () => { try { const j = await api('/api/cross/labels/read', { name: b.dataset.read }); crossTrack(m, j, 'sources'); } catch (e) { toast(e.message, 'error'); } }; });
  m.querySelectorAll('[data-golabels]').forEach(a => { a.onclick = e => { e.preventDefault(); CR.tab = 'labels'; m.querySelectorAll('#crTabs .stp').forEach(x => x.classList.toggle('on', x.dataset.tab === 'labels')); crossRenderTab(m); }; });
}

// ---------- 2. 種名の対応表 ----------
async function crossTabLabels(m, body) {
  body.innerHTML = `<div class="note">${esc(t('cr_labels_help'))}</div><div class="info">${esc(t('loading'))}</div>`;
  let r;
  try { r = await api('/api/cross/labels'); } catch (e) { body.innerHTML = `<div class="ng">${esc(e.message)}</div>`; return; }
  if (!m.isConnected || CR.tab !== 'labels') return;
  if (r.species) { S.species = r.species; rebuildSpMap(); }
  m._labels = r;
  const spRows = (S.species.species || []), fixed = (S.species.fixed || []);
  const spOpts = (cur) => {
    const on = spRows.filter(s => s.enabled), off = spRows.filter(s => !s.enabled);
    const opt = (s, suffix) => `<option value="${esc(s.code)}" ${cur === s.code ? 'selected' : ''}>${esc(spName(s))}${suffix || ''}</option>`;
    return `<optgroup label="${esc(t('tl_grp_species'))}">${on.map(s => opt(s)).join('')}${off.map(s => opt(s, ' ' + t('cr_sp_off'))).join('')}</optgroup>
      <optgroup label="${esc(t('tl_grp_fixed'))}">${fixed.map(s => opt(s)).join('')}</optgroup>`;
  };
  const rowSel = d => {
    const cur = d.mapped === null || d.mapped === undefined ? '__auto__' : d.mapped;
    const autoLabel = d.auto ? `${t('cr_auto')}: ${spLabel(d.auto)}` : `${t('cr_auto')}: ${t('tl_nomatch')}`;
    return `<select data-label="${esc(d.label)}"><option value="__auto__" ${cur === '__auto__' ? 'selected' : ''}>${esc(autoLabel)}</option>${spOpts(cur)}<option value="" ${cur === '' ? 'selected' : ''}>${esc(t('cr_skip_label'))}</option></select>`;
  };
  const kindChip = d => d.kind === 'csv' ? `<span class="chip human" title="${esc(t('cr_kind_csv_tip'))}">CSV</span>` : (d.kind === 'code' ? `<span class="chip ai" title="${esc(t('cr_kind_code_tip'))}">${esc(t('cross_kind_project'))}</span>` : `<span class="chip human">CSV</span> <span class="chip ai">${esc(t('cross_kind_project'))}</span>`);
  const render = () => {
    const qstr = CR.labelQ.trim().toLowerCase();
    const rows = r.rows.filter(d => !qstr || d.label.toLowerCase().includes(qstr) || (d.name_ja || '').toLowerCase().includes(qstr) || (d.name_en || '').toLowerCase().includes(qstr));
    m.querySelector('#crLabTable').innerHTML = rows.length ? `<table class="tbl tl-map cr-labels"><tr><th><input type="checkbox" id="crLabAll" title="${esc(t('cr_check_all'))}"></th><th>${esc(t('cr_label'))}</th><th>${esc(t('cr_label_from'))}</th><th>${esc(t('cr_auto_guess'))}</th><th>${esc(t('cr_treat_as'))}</th></tr>
      ${rows.map(d => `<tr class="${d.current ? (d.mapped !== null && d.mapped !== undefined ? 'mapped' : 'guess') : 'nomatch'}"><td><input type="checkbox" data-chk="${esc(d.label)}"></td>
        <td><b>${esc(d.label)}</b>${d.name_ja || d.name_en ? ` <span class="info">${esc(d.name_ja || d.name_en)}</span>` : ''} ${kindChip(d)}</td>
        <td class="info">${Object.entries(d.sources).map(([k, v]) => `${esc(k)} ${v}`).join(' · ')}</td>
        <td>${d.auto ? esc(spLabel(d.auto)) : `<span class="ng">${esc(t('tl_nomatch'))}</span>`}</td><td>${rowSel(d)}</td></tr>`).join('')}</table>` : `<div class="note">${esc(t('cr_no_labels_yet'))}</div>`;
    const all = m.querySelector('#crLabAll');
    if (all) all.onchange = () => m.querySelectorAll('[data-chk]').forEach(c => { c.checked = all.checked; });
  };
  body.innerHTML = `<div class="note">${esc(t('cr_labels_help'))}</div>
    <details class="adv"><summary>${esc(t('cr_labels_rule_h'))}</summary><div class="note">${esc(t('cr_labels_rule'))}</div></details>
    ${r.pending.length ? `<div class="callout warn">${(r.paused || []).length ? `${esc(t('cr_labels_paused'))}: ${esc(r.paused.join(', '))} ${r.paused.map(n => `<button type="button" class="small" data-read="${esc(n)}">${esc(t('cr_labels_read'))}</button>`).join(' ')} ` : ''}${r.pending.some(n => !(r.paused || []).includes(n)) ? `${esc(t('cross_labels_reading'))}: ${esc(r.pending.filter(n => !(r.paused || []).includes(n)).join(', '))} ${esc(t('cr_labels_pending'))}` : ''}</div>` : ''}
    <div class="dt-counts"><span>${esc(t('cr_n_labels'))} <b>${r.n_labels}</b></span><span class="${r.n_unresolved ? 'ng' : ''}">${esc(t('tl_nomatch'))} <b>${r.n_unresolved}</b></span><span>${esc(t('cr_n_rules'))} <b>${Object.keys(r.map || {}).length}</b></span></div>
    <div class="cr-actions"><input type="text" id="crLabQ" placeholder="${esc(t('search'))}" value="${esc(CR.labelQ)}" spellcheck="false" style="width:150px">
      <span class="info">${esc(t('cr_bulk'))}</span><select id="crLabBulk">${spOpts('bird')}<option value="">${esc(t('cr_skip_label'))}</option><option value="__auto__">${esc(t('cr_auto'))}</option></select><button type="button" class="small" id="crLabApply">${esc(t('cr_bulk_apply'))}</button></div>
    <div id="crLabTable" style="max-height:48vh;overflow:auto"></div>
    <div class="cr-actions"><button type="button" class="primary" id="crLabSaveBuild">${esc(t('cr_save_build'))}</button><button type="button" id="crLabSave">${esc(t('cr_save_only'))}</button><span class="info">${esc(t('cr_save_help'))}</span></div>`;
  render();
  const q = sel => m.querySelector(sel);
  m.querySelectorAll('[data-read]').forEach(b => { b.onclick = async () => { try { const j = await api('/api/cross/labels/read', { name: b.dataset.read }); crossTrack(m, j, 'labels'); } catch (e) { toast(e.message, 'error'); } }; });
  q('#crLabQ').oninput = () => { CR.labelQ = q('#crLabQ').value; render(); };
  q('#crLabApply').onclick = () => {
    const v = q('#crLabBulk').value;
    const checked = [...m.querySelectorAll('[data-chk]:checked')].map(c => c.dataset.chk);
    if (!checked.length) { toast(t('cr_bulk_none'), 'error'); return; }
    m.querySelectorAll('select[data-label]').forEach(s => { if (checked.includes(s.dataset.label)) s.value = v; });
    toast(`${checked.length} ${t('cr_bulk_done')}`);
  };
  const collect = () => {
    const map = {};
    m.querySelectorAll('select[data-label]').forEach(s => { if (s.value !== '__auto__') map[s.dataset.label] = s.value; });
    // 表に出ていない（検索で隠れている）表記の既存の対応は残す
    for (const [k, v] of Object.entries(r.map || {})) if (!(k in map) && ![...m.querySelectorAll('select[data-label]')].some(s => s.dataset.label.toLowerCase() === k)) map[k] = v;
    return map;
  };
  const save = async rebuild => {
    try {
      const res = await api('/api/cross/labels/save', { map: collect(), rebuild });
      if (res.species) { S.species = res.species; rebuildSpMap(); if (typeof renderSpeciesPanel === 'function') renderSpeciesPanel(); }
      toast(`${t('saved')} (${res.n})`);
      crossModalEl = null;
      if (rebuild) { await crossDialog('build'); const mm = crossModalEl; if (mm) crossTrack(mm, res.jobs, 'pick'); }
      else crossDialog('labels');
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#crLabSave').onclick = () => save(false);
  q('#crLabSaveBuild').onclick = () => save(true);
}

// ---------- 3. カタログを作る ----------
function crossTabBuild(m, body) {
  const info = m._info, cs = info.catalog_status || {};
  const by = Object.entries(cs.by_source || {}).map(([k, v]) => `${esc(k)} <b>${v}</b>`).join(' · ');
  const scan = !!((info.project.settings || {}).catalog_scan_folders);
  body.innerHTML = `<div class="note">${esc(t('cr_build_help'))}</div>
    <div class="callout ${!info.sources.length ? '' : (!cs.built || cs.stale ? 'warn' : 'ok')}">${!info.sources.length ? esc(t('cr_foot_no_sources')) : (!cs.built ? esc(t('cr_build_now')) : (cs.stale ? `${esc(crStaleText(cs))}${crOnlyLabels(cs) ? ' ' + esc(t('cr_remap_fast')) : ''}` : esc(t('cr_built_ok'))))}</div>
    ${cs.built ? `<div class="dt-counts"><span>${esc(t('cross_catalog_n'))} <b>${info.n_catalog}</b></span><span>${esc(t('tr_trained_at'))} <b>${esc((cs.built_at || '').slice(0, 16).replace('T', ' '))}</b></span>${by ? `<span>${by}</span>` : ''}<span>${esc(t('cross_items_n'))} <b>${info.n_items}</b></span></div>` : ''}
    <label class="chk" style="display:block;margin:6px 0"><input type="checkbox" id="crScan" ${scan ? 'checked' : ''}> ${esc(t('cr_scan_folders'))}</label>
    <div class="cr-actions"><button type="button" class="primary" id="crBuildBig" ${info.sources.length ? '' : 'disabled'}>${esc(crBuildLabel(cs))}</button>${crOnlyLabels(cs) ? `<button type="button" id="crBuildFull">${esc(t('cross_build_again'))}</button>` : ''}<span class="info">${esc(t('cr_build_time'))}</span></div>
    <div class="note">${esc(t('cr_build_when'))}</div>`;
  const q = sel => m.querySelector(sel);
  q('#crBuildBig').onclick = () => crossBuild(m, undefined, q('#crScan').checked);
  if (q('#crBuildFull')) q('#crBuildFull').onclick = () => crossBuild(m, 'full', q('#crScan').checked);
  q('#crScan').onchange = async () => { try { await api('/api/project/settings', { catalog_scan_folders: q('#crScan').checked }); if (m._info) m._info.project.settings.catalog_scan_folders = q('#crScan').checked; } catch (e) { toast(e.message, 'error'); } };
}

// ---------- 4. 動画を選んで取り込む ----------
function crossTabPick(m, body) {
  const info = m._info;
  if (!info.n_catalog) { body.innerHTML = `<div class="note">${esc(t('cr_pick_help'))}</div><div class="callout warn">${esc(t('cross_no_catalog_yet'))}</div>`; return; }
  const srcs = info.sources || [], cs = info.catalog_status || {};
  const sm = CR.smp;
  const unitOpts = [['station', t('cr_unit_station')], ['season', t('cr_unit_season')], ['station_season', t('cr_unit_station_season')], ['video', t('cr_unit_video')], ['species', t('cr_unit_species')]];
  body.innerHTML = `<div class="note">${esc(t('cr_pick_help'))}</div>
    ${cs.stale ? `<div class="callout warn">${esc(crStaleText(cs))}: ${esc(t('cr_pick_stale'))}</div>` : ''}
    <h4>${esc(t('cr_filter_h'))} <span class="info">${esc(t('cr_filter_sub'))}</span></h4>
    <div class="cr-filters">
      <select id="cfSource"><option value="">${esc(t('cross_all_sources'))}</option>${srcs.map(s => `<option value="${esc(s.name)}" ${CR.f.source === s.name ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
      <select id="cfSeason"><option value="">${esc(t('all_seasons'))}</option></select>
      <input type="text" id="cfStation" placeholder="${esc(t('cross_station_ph'))}" value="${esc(CR.f.station)}" spellcheck="false" style="width:150px">
      <select id="cfSpecies"><option value="">${esc(t('all_species'))}</option><option value="none" ${CR.f.species === 'none' ? 'selected' : ''}>${esc(t('sp_none'))}</option><option value="any" ${CR.f.species === 'any' ? 'selected' : ''}>${esc(t('sp_any'))}</option></select>
      <select id="cfStatus"><option value="">${esc(t('all_status'))}</option><option value="verified" ${CR.f.status === 'verified' ? 'selected' : ''}>${esc(t('st_verified'))}</option><option value="auto" ${CR.f.status === 'auto' ? 'selected' : ''}>${esc(t('cross_st_labelled'))}</option><option value="new" ${CR.f.status === 'new' ? 'selected' : ''}>${esc(t('st_new'))}</option></select>
      <select id="cfImported"><option value="">${esc(t('cross_imp_all'))}</option><option value="0" ${CR.f.imported === '0' ? 'selected' : ''}>${esc(t('cross_imp_no'))}</option><option value="1" ${CR.f.imported === '1' ? 'selected' : ''}>${esc(t('cross_imp_yes'))}</option></select>
      <input type="text" id="cfText" placeholder="${esc(t('filter_text'))}" value="${esc(CR.f.text)}" spellcheck="false" style="width:130px">
      <label class="chk" title="${esc(t('cross_single'))}"><input type="checkbox" id="cfSingle" ${CR.f.single_only ? 'checked' : ''}> ${esc(t('cross_single_short'))}</label>
      <button type="button" class="small primary" id="cfApply">${esc(t('cross_apply'))}</button><button type="button" class="small" id="cfClear">${esc(t('cross_clear'))}</button>
    </div>
    <details class="adv" ${CR.f.exclude_stations || CR.f.csv_text ? 'open' : ''}><summary>${esc(t('cross_more_filters'))}</summary>
      <div class="form-row"><label>${esc(t('cross_exclude'))}</label><input type="text" id="cfExclude" value="${esc(CR.f.exclude_stations)}" spellcheck="false" style="width:420px" placeholder="BSN0590, BSN0620, …"><span class="info">${esc(t('cross_exclude_help'))}</span></div>
      <div class="form-row"><label>${esc(t('cross_csv'))}</label><span style="display:flex;flex-direction:column;gap:4px"><textarea id="cfCsv" rows="4" style="width:520px;font-family:monospace" spellcheck="false" placeholder="Season,Station,File&#10;N240128,BSN0812,IMG_0011.AVI">${esc(CR.f.csv_text)}</textarea><span class="info">${esc(t('cross_csv_filter_help'))}</span></span></div>
    </details>
    <div id="cfSummary" class="dt-counts"></div>
    <details class="adv" id="cfPivotBox"><summary>${esc(t('cross_pivot'))}</summary><div id="cfPivot" style="max-height:30vh;overflow:auto"></div></details>
    <h4>${esc(t('cross_batch'))}</h4>
    <div class="cr-actions"><input type="text" id="cfBatch" spellcheck="false" style="width:140px" list="cfBatchList" placeholder="gold, round1" value="${esc(CR.batch || '')}"><datalist id="cfBatchList">${(info.batches || []).filter(b => b.batch).map(b => `<option value="${esc(b.batch)}">`).join('')}</datalist><span class="info">${esc(t('cr_batch_help'))}</span></div>
    <h4>${esc(t('cr_random_h'))}</h4>
    <div class="note">${esc(t('cr_random_help'))}</div>
    <div class="cr-actions"><span>${esc(t('cr_unit'))} <select id="cfUnit">${unitOpts.map(([v, l]) => `<option value="${v}" ${sm.unit === v ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></span>
      <span>${esc(t('cr_n'))} <input type="number" id="cfN" value="${sm.n}" min="1" step="1" style="width:70px"></span>
      <span id="cfCapWrap" title="${esc(t('cross_cap_help'))}">${esc(t('cross_cap'))} <input type="number" id="cfCap" value="${sm.cap}" min="0" style="width:60px"></span>
      <span id="cfGapWrap" title="${esc(t('cross_gap_help'))}">${esc(t('cross_gap'))} <input type="number" id="cfGap" value="${sm.gap}" min="0" style="width:60px"> ${esc(t('cross_min'))}</span>
      <span title="${esc(t('cr_seed_help'))}">${esc(t('cross_seed'))} <input type="number" id="cfSeed" value="${sm.seed}" min="0" style="width:60px"></span>
      <button type="button" class="small" id="cfSampleCount">${esc(t('cross_count'))}</button><button type="button" class="primary small" id="cfSampleImport">${esc(t('cr_random_import'))}</button></div>
    <div id="cfSampleOut" class="cr-sample-out"></div>
    <h4>${esc(t('cr_all_h'))}</h4>
    <div class="note">${esc(t('cr_all_help'))}</div>
    <div class="cr-actions">
      <button type="button" class="small" id="cfImportAll">${esc(t('cross_import_filtered'))} (<span id="cfNAll">0</span>)</button>
      <button type="button" class="small" id="cfExport">${esc(t('cross_export_catalog'))}</button>
    </div>
    <div id="cfLoad"></div>`;
  const q = sel => m.querySelector(sel);
  const readF = () => { CR.f = { source: q('#cfSource').value, season: q('#cfSeason').value, station: q('#cfStation').value.trim(), species: q('#cfSpecies').value, status: q('#cfStatus').value, imported: q('#cfImported').value, text: q('#cfText').value.trim(), exclude_stations: q('#cfExclude').value.trim(), single_only: q('#cfSingle').checked, csv_text: q('#cfCsv').value }; };
  q('#cfApply').onclick = () => { readF(); crossLoadCatalog(m); };
  q('#cfClear').onclick = () => { CR.f = Object.assign({}, CR_F0); crossRenderTab(m); };
  ['#cfStation', '#cfText'].forEach(sel => q(sel).addEventListener('keydown', e => { if (e.key === 'Enter') { readF(); crossLoadCatalog(m); } }));
  const batchOrWarn = () => { const b = q('#cfBatch').value.trim(); CR.batch = b; if (!b) { toast(t('cross_need_batch'), 'error'); q('#cfBatch').focus(); } return b; };
  q('#cfBatch').onchange = () => { CR.batch = q('#cfBatch').value.trim(); };
  q('#cfImportAll').onclick = async () => {
    const b = batchOrWarn(); if (!b) return;
    readF();
    const n = CR.counts ? CR.counts.total - CR.counts.imported : 0;
    crossModalEl = null;
    const ok = await confirmModal(t('cross_title'), `${esc(t('cross_import_filtered_q'))}: ${n}`, t('cross_do_import'));
    await crossDialog('pick');
    if (!ok) return;
    try { const j = await api('/api/cross/import', { mode: 'catalog_sample', filters: CR.f, params: { unit: 'all' }, batch: b }); const mm = crossModalEl; if (mm) { crossTrack(mm, j, 'pick'); } } catch (e) { toast(e.message, 'error'); }
  };
  q('#cfExport').onclick = async () => { readF(); try { const r = await api('/api/cross/catalog/export', { filters: CR.f }); toast(`${t('cross_list_done')}: ${r.path} (${r.n})`); } catch (e) { toast(e.message, 'error'); } };
  // ランダムに選ぶ
  const unitUi = () => { const u = q('#cfUnit').value; CR.smp.unit = u; q('#cfCapWrap').classList.toggle('hidden', u !== 'species'); q('#cfGapWrap').classList.toggle('hidden', u !== 'species' && u !== 'video'); };
  q('#cfUnit').onchange = unitUi; unitUi();
  const sampleParams = () => { CR.smp = { unit: q('#cfUnit').value, n: parseInt(q('#cfN').value) || 0, cap: parseInt(q('#cfCap').value) || 0, gap: parseFloat(q('#cfGap').value) || 0, seed: parseInt(q('#cfSeed').value) || 1 };
    return { unit: CR.smp.unit, n: CR.smp.n, cap: CR.smp.cap, gap_min: CR.smp.gap, seed: CR.smp.seed, species: CR.f.species && !['none', 'any'].includes(CR.f.species) ? [CR.f.species] : [] }; };
  const showSample = r => {
    const unitName = { station: t('filter_station_s'), season: t('filter_season_s'), station_season: t('cr_unit_station_season_s') }[r.unit];
    let html = `<div><b>${esc(t('cross_will_import'))} ${r.n} ${esc(t('items'))}</b>`;
    if (r.units && r.units.length) html += ` · ${esc(unitName)} ${r.units.length} / ${r.n_units_available}: <span class="info">${esc(r.units.map(([k, n]) => `${k} (${n})`).join(', '))}</span>`;
    else if (r.unit === 'video') html += ` <span class="info">/ ${r.n_units_available}</span>`;
    html += '</div>';
    const cnt = Object.entries(r.counts || {});
    if (cnt.length) html += `<div class="info">${esc(t('species'))}: ${cnt.map(([k, v]) => `${esc(k === '(none)' ? t('sp_none') : spLabel(k))} ${v}${r.available && r.available[k] != null ? ` <span class="info">/ ${r.available[k]}</span>` : ''}`).join(' · ')}</div>`;
    q('#cfSampleOut').innerHTML = html;
  };
  q('#cfSampleCount').onclick = async () => { readF(); try { showSample(await api('/api/cross/catalog/sample', { filters: CR.f, params: sampleParams() })); } catch (e) { toast(e.message, 'error'); } };
  q('#cfSampleImport').onclick = async () => {
    const b = batchOrWarn(); if (!b) return; readF();
    try { const r = await api('/api/cross/catalog/sample', { filters: CR.f, params: sampleParams() }); showSample(r);
      if (!r.n) { toast(t('cr_nothing'), 'error'); return; }
      const j = await api('/api/cross/import', { mode: 'catalog_sample', filters: CR.f, params: sampleParams(), batch: b }); crossTrack(m, j, 'pick'); } catch (e) { toast(e.message, 'error'); }
  };
  crossLoadCatalog(m);
}

async function crossLoadCatalog(m) {
  const q = sel => m.querySelector(sel);
  if (!q('#cfSummary')) return;
  let cnt;
  q('#cfLoad').innerHTML = `<span class="info">${esc(t('loading'))}</span>`;
  try {
    cnt = await api('/api/cross/catalog/counts', { filters: CR.f });
  } catch (e) { if (q('#cfLoad')) q('#cfLoad').innerHTML = `<div class="ng">${esc(e.message)}</div>`; return; }
  if (!m.isConnected || !q('#cfSummary')) return;
  q('#cfLoad').innerHTML = '';
  CR.counts = cnt;
  // 絞り込みの候補（調査回・種）は集計から
  const seasonSel = q('#cfSeason');
  if (seasonSel && seasonSel.options.length <= 1) fillSelect(seasonSel, [['', t('all_seasons')]].concat(cnt.seasons.map(s => [s, s])), CR.f.season);
  const spSel = q('#cfSpecies');
  if (spSel && spSel.options.length <= 3) {
    const codes = sortCodes(Object.keys(cnt.by_species).filter(c => c !== '(none)'));
    for (const c of codes) { const o = document.createElement('option'); o.value = c; o.textContent = `${spLabel(c)} (${cnt.by_species[c]})`; if (CR.f.species === c) o.selected = true; spSel.appendChild(o); }
  }
  q('#cfSummary').innerHTML = `<span>${esc(t('cr_pool'))} <b>${cnt.total}</b></span><span>${esc(t('cross_imported'))} <b>${cnt.imported}</b></span><span>${esc(t('filter_station_s'))} <b>${(cnt.stations || []).filter(Boolean).length}</b></span><span>${esc(t('filter_season_s'))} <b>${(cnt.seasons || []).filter(Boolean).length}</b></span>${Object.entries(cnt.by_source).map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join('')}${Object.entries(cnt.by_status).map(([k, v]) => `<span>${esc(k === 'verified' ? t('st_verified') : (k === 'auto' ? t('cross_st_labelled') : t('st_new')))} <b>${v}</b></span>`).join('')}`;
  q('#cfNAll').textContent = cnt.total - cnt.imported;
  // 種 × 調査回
  const seasons = cnt.seasons, sp = sortCodes(Object.keys(cnt.pivot).filter(c => c !== '(none)')).concat(cnt.pivot['(none)'] ? ['(none)'] : []);
  q('#cfPivot').innerHTML = sp.length ? `<table class="tbl matrix"><tr><th>${esc(t('species'))} \\ ${esc(t('cross_seasons'))}</th>${seasons.map(s => `<th>${esc(s || '—')}</th>`).join('')}<th>${esc(t('ag_n'))}</th></tr>${sp.map(c => `<tr><td><b>${esc(c === '(none)' ? t('sp_none') : spLabel(c))}</b></td>${seasons.map(s => `<td>${cnt.pivot[c][s] || ''}</td>`).join('')}<td><b>${cnt.by_species[c]}</b></td></tr>`).join('')}</table>` : '';
}

// ---------- 5. 書き戻し・書き出し ----------
function crossTabSync(m, body) {
  const info = m._info;
  const imports = (info.imports || []).slice().reverse();
  const hasProj = (info.n_sync || 0) > 0 || (info.sources || []).some(s => s.kind !== 'tree' || (s.project_seasons || []).length);
  const fmtParams = r => {       // 取り込みの条件を 1 行に: 選び方（unit、n、seed …）、絞り込み、選んだ地点・調査回
    const p = Object.assign({}, r.params || {});
    const inner = p.params && typeof p.params === 'object' ? p.params : {};
    const units = p.units, filt = p.filters || {};
    const skip = new Set(['csv_text', 'filters', 'units', 'params', 'counts', 'ids_n']);
    const parts = [];
    for (const [k, v] of Object.entries(Object.assign({}, p, inner))) { if (skip.has(k) || v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) continue; parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`); }
    const f = Object.entries(filt).filter(([k, v]) => v && k !== 'csv_text').map(([k, v]) => `${k}=${v}`).join(', ');
    if (f) parts.push(`${t('cr_filter_h')}: ${f}`);
    let s = parts.join(', ');
    if (units && units.length) s += ` · ${units.map(([k, n]) => `${k} (${n})`).join(', ')}`;
    return s.slice(0, 400);
  };
  body.innerHTML = `<div class="note">${esc(t('cr_sync_intro'))}</div>
    <h4>${esc(t('cross_sync_h'))}</h4><div class="note">${esc(t('cross_sync_help'))}${hasProj ? '' : ' ' + esc(t('cross_sync_tree_note'))}</div>
    <div class="btn-row"><button type="button" class="small" id="crPushPlan" ${hasProj ? '' : 'disabled'}>${esc(t('cross_push_count'))}</button><button type="button" class="small" id="crPush" ${hasProj ? '' : 'disabled'}>${esc(t('cross_push'))}</button><button type="button" class="small" id="crPullPlan" ${hasProj ? '' : 'disabled'}>${esc(t('cross_pull_count'))}</button><button type="button" class="small" id="crPull" ${hasProj ? '' : 'disabled'}>${esc(t('cross_pull'))}</button><label class="chk"><input type="checkbox" id="crOverwrite"> ${esc(t('cross_overwrite'))}</label></div>
    <div id="crSyncOut"></div>
    <h4>${esc(t('cross_export_h'))}</h4>
    <div class="form-row"><label>${esc(t('cross_copy_to'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crCopyDir" spellcheck="false" style="width:340px"><button type="button" class="small" data-pick="crCopyDir" data-kind="folder" data-name="">${esc(t('pick'))}</button><button type="button" class="small" id="crCopy">${esc(t('cross_copy'))}</button><button type="button" class="small" id="crList">${esc(t('cross_list'))}</button></span></div>
    <div class="note">${esc(t('cross_export_help'))}</div>
    ${imports.length ? `<h4>${esc(t('cross_log'))} (${imports.length})</h4><div class="note">${esc(t('cr_log_help'))}</div><div style="max-height:30vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('tr_trained_at'))}</th><th>${esc(t('cross_batch'))}</th><th>n</th><th>${esc(t('cross_params'))}</th></tr>
      ${imports.map(r => `<tr><td class="info">${esc((r.at || '').slice(0, 16).replace('T', ' '))}</td><td>${esc(r.batch || '')}</td><td>${r.n}</td><td class="info">${esc(fmtParams(r))} ${r.counts && Object.keys(r.counts).length ? esc(Object.entries(r.counts).map(([k, v]) => `${k === '(none)' ? t('sp_none') : spLabel(k)} ${v}`).join(' · ')) : ''}</td></tr>`).join('')}</table></div>` : ''}`;
  wirePicks(m);
  const q = sel => m.querySelector(sel);
  const showSync = (dir, r) => {
    const bs = Object.entries(r.by_source || {}).map(([k, v]) => v.error ? `${esc(k)}: <span class="ng">${esc(v.error)}</span>` : `${esc(k)}: ${v.update} / ${v.conflict} / ${v.same}`).join(' · ');
    q('#crSyncOut').innerHTML = `<div class="dt-counts"><span>${esc(dir === 'push' ? t('cross_push') : t('cross_pull'))}: ${esc(t('cross_sync_update'))} <b>${r.n_update}</b></span><span>${esc(t('cross_sync_conflict'))} <b>${r.n_conflict}</b></span><span>${esc(t('cross_sync_same'))} <b>${r.n_same}</b></span></div><div class="info">${bs}</div>` +
      (r.conflicts && r.conflicts.length ? `<div style="max-height:20vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('cross_conflict_item'))}</th><th>${esc(t('cross_here'))}</th><th>${esc(t('cross_there'))}</th></tr>${r.conflicts.map(c => `<tr><td class="info">${esc(c.rel_path)}</td><td>${esc(c.here.map(spLabel).join(', '))}</td><td>${esc(c.there.map(spLabel).join(', '))}</td></tr>`).join('')}</table></div>` : '');
  };
  q('#crPushPlan').onclick = async () => { try { showSync('push', await api('/api/cross/sync/plan', { direction: 'push' })); } catch (e) { toast(e.message, 'error'); } };
  q('#crPullPlan').onclick = async () => { try { showSync('pull', await api('/api/cross/sync/plan', { direction: 'pull' })); } catch (e) { toast(e.message, 'error'); } };
  const doSync = async dir => {
    const ow = q('#crOverwrite').checked;
    crossModalEl = null;
    const ok = await confirmModal(t('cross_title'), `<div>${esc(dir === 'push' ? t('cross_push_q') : t('cross_pull_q'))}</div>`, dir === 'push' ? t('cross_push') : t('cross_pull'));
    await crossDialog('sync');
    if (!ok) return;
    try { const j = await api('/api/cross/sync', { direction: dir, overwrite: ow }); S._bgWasRunning = true; const mm = crossModalEl; if (mm) { crossTrack(mm, j, 'sync'); } } catch (e) { toast(e.message, 'error'); }
  };
  q('#crPush').onclick = () => doSync('push');
  q('#crPull').onclick = () => doSync('pull');
  q('#crCopy').onclick = async () => {
    const out = q('#crCopyDir').value.trim(); if (!out) { toast(t('cross_need_dir'), 'error'); return; }
    try { const j = await api('/api/cross/copy', { out_dir: out, filters: Object.assign({}, S.filter) }); crossTrack(m, j, 'sync'); } catch (e) { toast(e.message, 'error'); }
  };
  q('#crList').onclick = async () => { try { const r = await api('/api/cross/export_list', { filters: Object.assign({}, S.filter) }); toast(`${t('cross_list_done')}: ${r.path} (${r.n})`); } catch (e) { toast(e.message, 'error'); } };
}

// ---------- 裏の処理の表示 ----------
const crMine = j => j && (j.kind === 'cross_import' || j.kind === 'cross_sync' || j.kind === 'cross_copy' || j.kind === 'cross_catalog' || j.kind === 'cross_labels');

// 処理を入れた直後に呼ぶ: 入れた処理の id を控え、終わったら after の段で窓を開き直す（入れた処理が既に終わっていれば次の表示で開き直る）
// 横断の処理は cross レーン（種名 CSV・カタログ。プロジェクトを閉じても続く）と bg レーン（取り込み・書き戻し・コピー）に分かれる → 1 つにまとめて見る
function crLanes(jobs) {
  const bg = (jobs && jobs.bg) || {}, cr = (jobs && jobs.cross) || {};
  const cur = (crMine(cr.current) ? cr.current : null) || (crMine(bg.current) ? bg.current : null);
  const queued = (cr.queued || []).concat(bg.queued || []).filter(crMine);
  const recent = (cr.recent || []).concat(bg.recent || []).filter(crMine).sort((a, b) => (b.id || 0) - (a.id || 0));
  const other = cur ? null : (cr.current || bg.current || null);       // 自分の処理が待っているとき、いま動いている別の処理
  const lane = cur ? (cur === cr.current ? 'cross' : 'bg') : (queued.length ? ((cr.queued || []).some(crMine) ? 'cross' : 'bg') : null);
  return { cur, queued, recent, other, lane };
}

function crossTrack(m, jobs, after) {
  m._after = after;
  m._wasRunning = m._wasRunning || 'submitted';
  const L = crLanes(jobs);
  const j = L.cur || L.queued[0];
  if (j && j.id != null) m._jobId = j.id;
  crossOnJobs(jobs);
  const st = m.querySelector('#crStatus');
  if (st && !st.innerHTML && document.body.contains(m)) st.innerHTML = `<div><b>${esc(t('jb_queued'))}</b></div>`;   // 入れた直後でまだ状態が来ていないときも、押した手応えを出す
}

function crossOnJobs(jobs) {
  const m = crossModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.bg) return;
  const L = crLanes(jobs);
  const cur = L.cur, rec = L.recent[0], queued = L.queued.length > 0;
  const st = m.querySelector('#crStatus');
  if (m._recId === undefined) m._recId = rec ? rec.id : null;      // 窓を開いたときの最後の処理（これより新しい処理が終わったときだけ「終わった」と見なす）
  const finished = !cur && !queued && m._wasRunning && rec && (m._jobId != null ? L.recent.some(x => x.id === m._jobId) : rec.id !== m._recId);
  const stopBtn = `<button type="button" class="small" id="crStop" title="${esc(t('cr_stop_tip'))}">${esc(t('jb_stop'))}</button>`;
  if (cur) {
    const pct = cur.total ? Math.round(100 * cur.done / cur.total) : 0;
    const count = cur.kind === 'cross_labels' ? `${pct}%` : `${cur.done}${cur.total ? ' / ' + cur.total + ` (${pct}%)` : ''}`;
    st.innerHTML = `<div><b>${esc(cur.label)}</b>: ${count} ${stopBtn} <span class="info">${esc(t('cr_bg_note'))}</span></div>${cur.total ? `<div class="prog"><div style="width:${pct}%"></div></div>` : ''}<div class="info">${esc(cur.current || '')}${cur.current && cur.message ? ' · ' : ''}${esc(cur.message || '')}</div>`;
    m._wasRunning = m._wasRunning || cur.kind;
  } else if (queued) {                         // 自分の処理は列にある: いま何が動いているかも出す（別の処理の後ろで待っていることがある）
    const o = L.other;
    const pct = o && o.total ? ` ${o.done} / ${o.total} (${Math.round(100 * o.done / o.total)}%)` : (o && o.done ? ` ${o.done}` : '');
    st.innerHTML = `<div><b>${esc(t('jb_queued'))}</b>${o ? ` · ${esc(t('cr_now_running'))}: ${esc(o.label)}${pct} <span class="info">${esc(o.current || o.message || '')}</span> · ${esc(t('cr_starts_after'))}` : ''} ${stopBtn}</div>`;
    m._wasRunning = m._wasRunning || 'queued';
  } else if (finished) {                       // 待ち → 実行中 の切り替わりの一瞬（どちらにも見えない）では終わったと見なさない
    m._wasRunning = false;
    const after = m._after || CR.tab;
    crossModalEl = null;
    S._bgWasRunning = true;
    crossDialog(after).then(() => { const mm = crossModalEl; if (mm && rec) mm.querySelector('#crStatus').innerHTML = `<div><b>${esc(t('ar_result'))}</b> (${esc(t('jb_state_' + rec.state))}): ${esc(rec.message || rec.error || '')}</div>`; });
  }
  const sb = st.querySelector('#crStop');
  if (sb) sb.onclick = async () => { try { const j = await api('/api/jobs/stop', { lane: L.lane || 'cross' }); m._wasRunning = m._wasRunning || 'stopping'; crossOnJobs(j); } catch (e) { toast(e.message, 'error'); } };
}
