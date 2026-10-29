'use strict';
/* 横断プロジェクト（2 階層目）の窓: 3 つの段
   1. 参照先（調査回のプロジェクトか NAS の親フォルダ）を登録 → カタログ（全動画の目録）を作る
   2. カタログ: 表で絞り込み・集計し、扱う動画を選んで取り込む（取り込んだものが一覧と再生の対象になる）
   3. 同期と書き出し: 判定の書き戻し・再取得、動画のコピー、一覧 CSV、取り込みの記録 */

let crossModalEl = null;
const CR = { tab: 'sources', f: { source: '', season: '', station: '', species: '', status: '', imported: '', text: '', exclude_stations: '', single_only: false, csv_text: '' }, sort: 'rel', desc: false, page: 0, checked: new Set(), counts: null };
const CR_PAGE = 200;

async function crossDialog(tab) {
  let info;
  try { info = await api('/api/cross/info'); } catch (e) { toast(e.message, 'error'); return; }
  if (tab) CR.tab = tab;
  else if (!info.sources.length) CR.tab = 'sources';
  else if (!info.n_catalog) CR.tab = 'sources';
  const m = showModal(`<h3>${esc(t('cross_title'))} <span class="info">${esc(info.project.name)}</span></h3>
    <div class="tabs" id="crTabs">${[['sources', t('cross_tab_sources')], ['catalog', t('cross_tab_catalog')], ['sync', t('cross_tab_sync')]].map(([k, l]) => `<button type="button" class="tab ${CR.tab === k ? 'on' : ''}" data-tab="${k}">${esc(l)}</button>`).join('')}
      <span class="info" style="margin-left:auto">${esc(t('cross_sources_n'))} ${info.sources.length} · ${esc(t('cross_catalog_n'))} ${info.n_catalog} · ${esc(t('cross_items_n'))} ${info.n_items}</span></div>
    <div id="crBody"></div>
    <div id="crStatus"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  crossModalEl = m; m._info = info;
  m.querySelector('#mCancel').onclick = () => { crossModalEl = null; closeModal(); };
  m.querySelectorAll('#crTabs .tab').forEach(b => { b.onclick = () => { CR.tab = b.dataset.tab; m.querySelectorAll('#crTabs .tab').forEach(x => x.classList.toggle('on', x === b)); crossRenderTab(m); }; });
  crossRenderTab(m);
  crossOnJobs(S.snap && S.snap.jobs);
}

function crossRenderTab(m) {
  const body = m.querySelector('#crBody');
  if (CR.tab === 'sources') crossTabSources(m, body);
  else if (CR.tab === 'catalog') crossTabCatalog(m, body);
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
    const un = (l.unmapped || []).map(([v, n]) => `${v} (${n})`).join(', ');
    const bad = files.filter(f => f.error);
    return `<span class="info" title="${esc(files.map(f => `${f.path} (${f.error || f.n})`).join('\n'))}">${esc(t('cross_labels'))} ${files.length} ${esc(l.auto ? t('cross_labels_auto') : t('cross_labels_given'))} · ${l.n_here ?? l.n ?? 0} ${esc(t('items'))}${bad.length ? ` <span class="ng">${esc(t('cross_labels_bad'))} ${bad.length}</span>` : ''}${l.n_unmapped ? ` <span class="ng" title="${esc(un)}">${esc(t('cross_unmapped'))} ${l.n_unmapped}</span>` : ''}</span>`;
  };
  const rows = srcs.map(s => `<tr><td><b>${esc(s.name)}</b> <span class="chip ${s.kind === 'tree' ? 'human' : 'ai'}">${esc(s.kind === 'tree' ? t('cross_kind_tree') : t('cross_kind_project'))}</span>${s.ok ? '' : ` <span class="ng">✗</span>`}</td>
    <td class="info" title="${esc(s.dir || s.root || '')}">${esc(s.ok ? (s.root || '') : (s.error || ''))}${s.ok && !s.available ? ` <span class="ng">${esc(t('cross_unavailable'))}</span>` : ''}${s.kind === 'tree' && s.seasons ? `<div>${esc(t('cross_seasons'))} ${s.seasons.length}${(s.project_seasons || []).length ? ` (${esc(t('cross_proj_seasons'))} ${s.project_seasons.length})` : ''} · ${labInfo(s)}</div>` : ''}</td>
    <td>${s.ok && s.n_items != null ? s.n_items : '—'}</td><td>${s.ok && s.n_verified != null ? s.n_verified : '—'}</td><td>${s.ok && s.n_human != null ? s.n_human : '—'}</td><td>${s.n_imported || 0}</td>
    <td>${s.kind === 'tree' ? `<button type="button" class="small" data-lab="${esc(s.name)}">${esc(t('cross_set_labels'))}</button> ` : ''}<button type="button" class="small" data-rm="${esc(s.name)}">${esc(t('cross_remove'))}</button></td></tr>`).join('');
  const cat = info.catalog;
  body.innerHTML = `<div class="note">${esc(t('cross_sources_help'))}</div>
    ${srcs.length ? `<div style="max-height:30vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('cross_src_name'))}</th><th>${esc(t('cross_src_root'))}</th><th>${esc(t('items'))}</th><th>${esc(t('st_verified'))}</th><th>${esc(t('cross_human'))}</th><th>${esc(t('cross_imported'))}</th><th></th></tr>${rows}</table></div>` : `<div class="note">${esc(t('cross_no_sources'))}</div>`}
    <h4>${esc(t('cross_add_src'))} <span class="info">${esc(t('cross_add_src_multi'))}</span></h4>
    <div class="form-row"><label>${esc(t('cross_folder_l'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcPath" spellcheck="false" style="width:380px" placeholder="Y:\\PRM_N"><button type="button" class="small" data-pick="crSrcPath" data-kind="folder" data-name="">${esc(t('pick'))}</button><button type="button" class="small" id="crSrcUp" title="${esc(t('cross_up_tip'))}">${esc(t('cross_up'))}</button></span></div>
    <div class="form-row"><label></label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><button type="button" class="primary small" id="crSrcAddUnder">${esc(t('cross_add_under'))}</button><button type="button" class="small" id="crSrcAdd">${esc(t('cross_add_one'))}</button></span></div>
    <div class="note">${esc(t('cross_add_src_help'))}</div>
    <details class="adv"><summary>${esc(t('cross_add_opts'))}</summary>
      <div class="form-row"><label>${esc(t('cross_src_name'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcName" spellcheck="false" style="width:120px" placeholder="${esc(t('cross_name_ph'))}"> <span class="info">${esc(t('cross_name_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('cross_labels_csv'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crSrcLabels" spellcheck="false" style="width:380px"><button type="button" class="small" data-pick="crSrcLabels" data-kind="csv" data-name="">${esc(t('pick'))}</button> <span class="info">${esc(t('cross_labels_help'))}</span></span></div>
    </details>
    <h4>${esc(t('cross_catalog_h'))}</h4>
    <div class="form-row"><label></label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><button type="button" class="primary small" id="crBuild" ${srcs.length ? '' : 'disabled'}>${esc(cat ? t('cross_build_again') : t('cross_build'))}</button>
      <span class="info">${cat ? `${cat.n} ${esc(t('items'))} · ${esc((cat.built_at || '').slice(0, 16).replace('T', ' '))}` : esc(t('cross_no_catalog'))}</span></span></div>
    <div class="note">${esc(t('cross_catalog_help'))}</div>`;
  wirePicks(m);
  const q = sel => m.querySelector(sel);
  q('#crSrcUp').onclick = () => { const v = q('#crSrcPath').value.trim().replace(/[\\/]+$/, ''); const i = Math.max(v.lastIndexOf('/'), v.lastIndexOf('\\')); if (i > 1) q('#crSrcPath').value = v.slice(0, i); };
  q('#crSrcAdd').onclick = async () => {
    const pth = q('#crSrcPath').value.trim(); if (!pth) return;
    try { const r = await api('/api/cross/sources/add', { path: pth, name: q('#crSrcName').value.trim(), labels_csv: q('#crSrcLabels').value.trim() }); setSnapshot(r.state); toast(`${t('cross_added')}: ${r.name}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  };
  q('#crSrcAddUnder').onclick = async () => {
    const pth = q('#crSrcPath').value.trim(); if (!pth) return;
    try { const r = await api('/api/cross/sources/add_under', { path: pth, labels_csv: q('#crSrcLabels').value.trim() }); setSnapshot(r.state);
      toast(`${t('cross_added')}: ${r.added.length}${r.skipped.length ? ` (${t('cross_already')} ${r.skipped.length})` : ''}${r.errors.length ? ` · ${r.errors[0]}` : ''}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelectorAll('[data-lab]').forEach(b => { b.onclick = async () => {
    const pth = q('#crSrcLabels').value.trim() || await pickPath('csv');
    if (!pth) { toast(t('cross_pick_labels'), 'error'); return; }
    try { const r = await api('/api/cross/sources/labels', { name: b.dataset.lab, labels_csv: pth }); toast(`${t('cross_labels')} ${r.labels ? r.labels.n : 0}`); crossModalEl = null; crossDialog('sources'); } catch (e) { toast(e.message, 'error'); }
  }; });
  m.querySelectorAll('[data-rm]').forEach(b => { b.onclick = async () => {
    crossModalEl = null;
    if (await confirmModal(t('cross_title'), `${esc(t('cross_remove_q'))}: ${esc(b.dataset.rm)}`, t('cross_remove'), true)) { try { const r = await api('/api/cross/sources/remove', { name: b.dataset.rm }); setSnapshot(r.state); } catch (e) { toast(e.message, 'error'); } }
    crossDialog('sources');
  }; });
  q('#crBuild').onclick = async () => { try { const j = await api('/api/cross/catalog/build', {}); m._wasRunning = 'catalog'; m._after = 'catalog'; crossOnJobs(j); } catch (e) { toast(e.message, 'error'); } };
}

// ---------- 2. カタログ ----------
function crossTabCatalog(m, body) {
  const info = m._info;
  if (!info.n_catalog) { body.innerHTML = `<div class="note">${esc(t('cross_no_catalog_yet'))}</div>`; return; }
  const srcs = info.sources || [];
  body.innerHTML = `<div class="note">${esc(t('cross_catalog_tab_help'))}</div>
    <div class="cr-filters">
      <select id="cfSource"><option value="">${esc(t('cross_all_sources'))}</option>${srcs.map(s => `<option value="${esc(s.name)}" ${CR.f.source === s.name ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
      <select id="cfSeason"><option value="">${esc(t('all_seasons'))}</option></select>
      <input type="text" id="cfStation" placeholder="${esc(t('cross_station_ph'))}" value="${esc(CR.f.station)}" spellcheck="false" style="width:150px">
      <select id="cfSpecies"><option value="">${esc(t('all_species'))}</option><option value="none" ${CR.f.species === 'none' ? 'selected' : ''}>${esc(t('sp_none'))}</option><option value="any" ${CR.f.species === 'any' ? 'selected' : ''}>${esc(t('sp_any'))}</option></select>
      <select id="cfStatus"><option value="">${esc(t('all_status'))}</option><option value="verified" ${CR.f.status === 'verified' ? 'selected' : ''}>${esc(t('st_verified'))}</option><option value="auto" ${CR.f.status === 'auto' ? 'selected' : ''}>${esc(t('cross_st_labelled'))}</option><option value="new" ${CR.f.status === 'new' ? 'selected' : ''}>${esc(t('st_new'))}</option></select>
      <select id="cfImported"><option value="">${esc(t('cross_imp_all'))}</option><option value="0" ${CR.f.imported === '0' ? 'selected' : ''}>${esc(t('cross_imp_no'))}</option><option value="1" ${CR.f.imported === '1' ? 'selected' : ''}>${esc(t('cross_imp_yes'))}</option></select>
      <input type="text" id="cfText" placeholder="${esc(t('filter_text'))}" value="${esc(CR.f.text)}" spellcheck="false" style="width:130px">
      <label class="chk" title="${esc(t('cross_single'))}"><input type="checkbox" id="cfSingle" ${CR.f.single_only ? 'checked' : ''}> ${esc(t('cross_single_short'))}</label>
      <button type="button" class="small" id="cfApply">${esc(t('cross_apply'))}</button><button type="button" class="small" id="cfClear">${esc(t('cross_clear'))}</button>
    </div>
    <details class="adv"><summary>${esc(t('cross_more_filters'))}</summary>
      <div class="form-row"><label>${esc(t('cross_exclude'))}</label><input type="text" id="cfExclude" value="${esc(CR.f.exclude_stations)}" spellcheck="false" style="width:420px" placeholder="BSN0590, BSN0620, …"></div>
      <div class="form-row"><label>${esc(t('cross_csv'))}</label><span style="display:flex;flex-direction:column;gap:4px"><textarea id="cfCsv" rows="4" style="width:520px;font-family:monospace" spellcheck="false" placeholder="Season,Station,File&#10;N240128,BSN0812,IMG_0011.AVI">${esc(CR.f.csv_text)}</textarea><span class="info">${esc(t('cross_csv_filter_help'))}</span></span></div>
    </details>
    <div id="cfSummary" class="dt-counts"></div>
    <details class="adv" id="cfPivotBox"><summary>${esc(t('cross_pivot'))}</summary><div id="cfPivot" style="max-height:30vh;overflow:auto"></div></details>
    <div class="cr-actions">
      <span>${esc(t('cross_batch'))} <input type="text" id="cfBatch" spellcheck="false" style="width:120px" list="cfBatchList" placeholder="gold, round1"><datalist id="cfBatchList">${(info.batches || []).filter(b => b.batch).map(b => `<option value="${esc(b.batch)}">`).join('')}</datalist></span>
      <button type="button" class="primary small" id="cfImportChecked">${esc(t('cross_import_checked'))} (<span id="cfNChecked">0</span>)</button>
      <button type="button" class="small" id="cfImportAll">${esc(t('cross_import_filtered'))} (<span id="cfNAll">0</span>)</button>
      <button type="button" class="small" id="cfExport">${esc(t('cross_export_catalog'))}</button>
    </div>
    <details class="adv" id="cfSampleBox"><summary>${esc(t('cross_sample_h'))}</summary><div class="note">${esc(t('cross_sample_help'))}</div>
      <div class="cr-actions">${esc(t('cross_n_per'))} <input type="number" id="cfN" value="200" min="1" step="10" style="width:70px"> ${esc(t('cross_cap'))} <input type="number" id="cfCap" value="5" min="0" style="width:60px"> ${esc(t('cross_gap'))} <input type="number" id="cfGap" value="10" min="0" style="width:60px"> ${esc(t('cross_min'))} ${esc(t('cross_seed'))} <input type="number" id="cfSeed" value="1" min="0" style="width:60px">
        <button type="button" class="small" id="cfSampleCount">${esc(t('cross_count'))}</button><button type="button" class="primary small" id="cfSampleImport">${esc(t('cross_do_import'))}</button></div>
      <div id="cfSampleOut" class="info"></div></details>
    <div id="cfTable"></div>`;
  const q = sel => m.querySelector(sel);
  const readF = () => { CR.f = { source: q('#cfSource').value, season: q('#cfSeason').value, station: q('#cfStation').value.trim(), species: q('#cfSpecies').value, status: q('#cfStatus').value, imported: q('#cfImported').value, text: q('#cfText').value.trim(), exclude_stations: q('#cfExclude').value.trim(), single_only: q('#cfSingle').checked, csv_text: q('#cfCsv').value }; CR.page = 0; CR.checked.clear(); };
  q('#cfApply').onclick = () => { readF(); crossLoadCatalog(m); };
  q('#cfClear').onclick = () => { CR.f = { source: '', season: '', station: '', species: '', status: '', imported: '', text: '', exclude_stations: '', single_only: false, csv_text: '' }; CR.page = 0; CR.checked.clear(); crossRenderTab(m); crossLoadCatalog(m); };
  ['#cfStation', '#cfText'].forEach(sel => q(sel).addEventListener('keydown', e => { if (e.key === 'Enter') { readF(); crossLoadCatalog(m); } }));
  const batchOrWarn = () => { const b = q('#cfBatch').value.trim(); if (!b) toast(t('cross_need_batch'), 'error'); return b; };
  q('#cfImportChecked').onclick = async () => {
    const b = batchOrWarn(); if (!b || !CR.checked.size) return;
    try { const j = await api('/api/cross/import', { mode: 'catalog', ids: [...CR.checked], batch: b }); m._wasRunning = 'import'; m._after = 'catalog'; crossOnJobs(j); } catch (e) { toast(e.message, 'error'); }
  };
  q('#cfImportAll').onclick = async () => {
    const b = batchOrWarn(); if (!b) return;
    readF();
    const n = CR.counts ? CR.counts.total - CR.counts.imported : 0;
    crossModalEl = null;
    const ok = await confirmModal(t('cross_title'), `${esc(t('cross_import_filtered_q'))}: ${n}`, t('cross_do_import'));
    await crossDialog('catalog');
    if (!ok) return;
    try { const j = await api('/api/cross/import', { mode: 'catalog_sample', filters: CR.f, params: { n_per_species: 0 }, batch: b }); const mm = crossModalEl; if (mm) { mm._wasRunning = 'import'; mm._after = 'catalog'; } crossOnJobs(j); } catch (e) { toast(e.message, 'error'); }
  };
  q('#cfExport').onclick = async () => { readF(); try { const r = await api('/api/cross/catalog/export', { filters: CR.f }); toast(`${t('cross_list_done')}: ${r.path} (${r.n})`); } catch (e) { toast(e.message, 'error'); } };
  const sampleParams = () => ({ n_per_species: parseInt(q('#cfN').value) || 0, cap: parseInt(q('#cfCap').value) || 0, gap_min: parseFloat(q('#cfGap').value) || 0, seed: parseInt(q('#cfSeed').value) || 1, species: CR.f.species && !['none', 'any'].includes(CR.f.species) ? [CR.f.species] : [] });
  q('#cfSampleCount').onclick = async () => { readF(); try { const r = await api('/api/cross/catalog/sample', { filters: CR.f, params: sampleParams() }); q('#cfSampleOut').innerHTML = `${esc(t('cross_will_import'))} <b>${r.n}</b> · ` + Object.entries(r.counts).map(([k, v]) => `${esc(spLabel(k))} ${v} <span class="info">/ ${r.available[k] || 0}</span>`).join(' · '); } catch (e) { toast(e.message, 'error'); } };
  q('#cfSampleImport').onclick = async () => {
    const b = batchOrWarn(); if (!b) return; readF();
    try { const j = await api('/api/cross/import', { mode: 'catalog_sample', filters: CR.f, params: sampleParams(), batch: b }); m._wasRunning = 'import'; m._after = 'catalog'; crossOnJobs(j); } catch (e) { toast(e.message, 'error'); }
  };
  crossLoadCatalog(m);
}

async function crossLoadCatalog(m) {
  const q = sel => m.querySelector(sel);
  if (!q('#cfTable')) return;
  let cnt, rows;
  try {
    [cnt, rows] = await Promise.all([api('/api/cross/catalog/counts', { filters: CR.f }), api('/api/cross/catalog/rows', { filters: CR.f, sort: CR.sort, desc: CR.desc, offset: CR.page * CR_PAGE, limit: CR_PAGE })]);
  } catch (e) { q('#cfTable').innerHTML = `<div class="ng">${esc(e.message)}</div>`; return; }
  if (!m.isConnected) return;
  CR.counts = cnt;
  // 絞り込みの候補（調査回・種）は集計から
  const seasonSel = q('#cfSeason');
  if (seasonSel && seasonSel.options.length <= 1) fillSelect(seasonSel, [['', t('all_seasons')]].concat(cnt.seasons.map(s => [s, s])), CR.f.season);
  const spSel = q('#cfSpecies');
  if (spSel && spSel.options.length <= 3) {
    const codes = sortCodes(Object.keys(cnt.by_species).filter(c => c !== '(none)'));
    for (const c of codes) { const o = document.createElement('option'); o.value = c; o.textContent = `${spLabel(c)} (${cnt.by_species[c]})`; if (CR.f.species === c) o.selected = true; spSel.appendChild(o); }
  }
  q('#cfSummary').innerHTML = `<span>${esc(t('cross_catalog_n'))} <b>${cnt.total}</b></span><span>${esc(t('cross_imported'))} <b>${cnt.imported}</b></span>${Object.entries(cnt.by_source).map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join('')}${Object.entries(cnt.by_status).map(([k, v]) => `<span>${esc(k === 'verified' ? t('st_verified') : (k === 'auto' ? t('cross_st_labelled') : t('st_new')))} <b>${v}</b></span>`).join('')}`;
  q('#cfNAll').textContent = cnt.total - cnt.imported;
  // 種 × 調査回
  const seasons = cnt.seasons, sp = sortCodes(Object.keys(cnt.pivot).filter(c => c !== '(none)')).concat(cnt.pivot['(none)'] ? ['(none)'] : []);
  q('#cfPivot').innerHTML = sp.length ? `<table class="tbl matrix"><tr><th>${esc(t('species'))} \\ ${esc(t('cross_seasons'))}</th>${seasons.map(s => `<th>${esc(s || '—')}</th>`).join('')}<th>${esc(t('ag_n'))}</th></tr>${sp.map(c => `<tr><td><b>${esc(c === '(none)' ? t('sp_none') : spLabel(c))}</b></td>${seasons.map(s => `<td>${cnt.pivot[c][s] || ''}</td>`).join('')}<td><b>${cnt.by_species[c]}</b></td></tr>`).join('')}</table>` : '';
  // 行
  const hdr = (k, label) => `<th class="clickable" data-sort="${k}">${esc(label)}${CR.sort === k ? (CR.desc ? ' ▼' : ' ▲') : ''}</th>`;
  const stName = s => s === 'verified' ? t('st_verified') : (s === 'auto' ? t('cross_st_labelled') : t('st_new'));
  const sps = v => (v || '').split(';').filter(Boolean).map(spLabel).join(', ');
  const pages = Math.max(1, Math.ceil(rows.total / CR_PAGE));
  q('#cfTable').innerHTML = `<div class="cr-pager"><span class="info">${rows.total} ${esc(t('items'))} · ${CR.page + 1} / ${pages}</span><button type="button" class="small" id="cfPrev" ${CR.page ? '' : 'disabled'}>‹</button><button type="button" class="small" id="cfNext" ${CR.page + 1 < pages ? '' : 'disabled'}>›</button>
      <label class="chk"><input type="checkbox" id="cfCheckPage"> ${esc(t('cross_check_page'))}</label><span class="info">${esc(t('cross_check_help'))}</span></div>
    <div style="max-height:40vh;overflow:auto"><table class="tbl cr-table"><tr><th></th>${hdr('rel', t('cross_src_name'))}${hdr('season', t('filter_season_s'))}${hdr('station', t('filter_station_s'))}<th>${esc(t('cross_col_file'))}</th>${hdr('datetime', t('cross_col_dt'))}${hdr('status', t('cross_col_status'))}${hdr('species', t('species'))}<th>AI</th><th>${esc(t('cross_imp_short'))}</th></tr>
    ${rows.rows.map(r => `<tr class="${r.item_id ? 'cur' : ''}"><td><input type="checkbox" data-cid="${r.id}" ${CR.checked.has(r.id) ? 'checked' : ''} ${r.item_id ? 'disabled' : ''}></td><td>${esc(r.source)}</td><td>${esc(r.season || '')}</td><td>${esc(r.station || '')}</td><td class="info" title="${esc(r.rel)}">${esc(r.name)}</td><td class="info">${esc((r.datetime || '').slice(0, 16))}</td><td>${esc(stName(r.status))}</td><td>${esc(sps(r.species))}</td><td class="info">${esc(sps(r.ai))}${r.agree === 3 ? ' <span class="ng">≠</span>' : ''}</td><td>${r.item_id ? `<a href="#" data-open="${r.item_id}">${esc(t('cross_imp_yes'))}</a>` : ''}</td></tr>`).join('')}</table></div>`;
  q('#cfPrev').onclick = () => { CR.page--; crossLoadCatalog(m); };
  q('#cfNext').onclick = () => { CR.page++; crossLoadCatalog(m); };
  m.querySelectorAll('[data-sort]').forEach(h => { h.onclick = () => { if (CR.sort === h.dataset.sort) CR.desc = !CR.desc; else { CR.sort = h.dataset.sort; CR.desc = false; } CR.page = 0; crossLoadCatalog(m); }; });
  const upd = () => { q('#cfNChecked').textContent = CR.checked.size; };
  m.querySelectorAll('[data-cid]').forEach(c => { c.onchange = () => { const id = parseInt(c.dataset.cid); if (c.checked) CR.checked.add(id); else CR.checked.delete(id); upd(); }; });
  q('#cfCheckPage').onchange = () => { m.querySelectorAll('[data-cid]:not(:disabled)').forEach(c => { c.checked = q('#cfCheckPage').checked; const id = parseInt(c.dataset.cid); if (c.checked) CR.checked.add(id); else CR.checked.delete(id); }); upd(); };
  m.querySelectorAll('[data-open]').forEach(a => { a.onclick = e => { e.preventDefault(); crossModalEl = null; closeModal(); openItem(parseInt(a.dataset.open)).catch(() => {}); }; });
  upd();
}

// ---------- 3. 同期と書き出し ----------
function crossTabSync(m, body) {
  const info = m._info;
  const imports = (info.imports || []).slice().reverse();
  const hasProj = (info.n_sync || 0) > 0 || (info.sources || []).some(s => s.kind !== 'tree' || (s.project_seasons || []).length);
  body.innerHTML = `<h4>${esc(t('cross_sync_h'))}</h4><div class="note">${esc(t('cross_sync_help'))}${hasProj ? '' : ' ' + esc(t('cross_sync_tree_note'))}</div>
    <div class="btn-row"><button type="button" class="small" id="crPushPlan" ${hasProj ? '' : 'disabled'}>${esc(t('cross_push_count'))}</button><button type="button" class="small" id="crPush" ${hasProj ? '' : 'disabled'}>${esc(t('cross_push'))}</button><button type="button" class="small" id="crPullPlan" ${hasProj ? '' : 'disabled'}>${esc(t('cross_pull_count'))}</button><button type="button" class="small" id="crPull" ${hasProj ? '' : 'disabled'}>${esc(t('cross_pull'))}</button><label class="chk"><input type="checkbox" id="crOverwrite"> ${esc(t('cross_overwrite'))}</label></div>
    <div id="crSyncOut"></div>
    <h4>${esc(t('cross_export_h'))}</h4>
    <div class="form-row"><label>${esc(t('cross_copy_to'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="crCopyDir" spellcheck="false" style="width:340px"><button type="button" class="small" data-pick="crCopyDir" data-kind="folder" data-name="">${esc(t('pick'))}</button><button type="button" class="small" id="crCopy">${esc(t('cross_copy'))}</button><button type="button" class="small" id="crList">${esc(t('cross_list'))}</button></span></div>
    <div class="note">${esc(t('cross_export_help'))}</div>
    ${imports.length ? `<h4>${esc(t('cross_log'))} (${imports.length})</h4><div style="max-height:30vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('tr_trained_at'))}</th><th>${esc(t('cross_batch'))}</th><th>n</th><th>${esc(t('cross_params'))}</th></tr>
      ${imports.map(r => `<tr><td class="info">${esc((r.at || '').slice(0, 16).replace('T', ' '))}</td><td>${esc(r.batch || '')}</td><td>${r.n}</td><td class="info">${esc(JSON.stringify(Object.assign({}, r.params, { csv_text: undefined })).replace(/"(\w+)":/g, '$1:').slice(0, 200))} ${r.counts ? esc(Object.entries(r.counts).map(([k, v]) => `${spLabel(k)} ${v}`).join(' · ')) : ''}</td></tr>`).join('')}</table></div>` : ''}`;
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
    try { const j = await api('/api/cross/sync', { direction: dir, overwrite: ow }); S._bgWasRunning = true; const mm = crossModalEl; if (mm) { mm._wasRunning = 'sync'; mm._after = 'sync'; } crossOnJobs(j); } catch (e) { toast(e.message, 'error'); }
  };
  q('#crPush').onclick = () => doSync('push');
  q('#crPull').onclick = () => doSync('pull');
  q('#crCopy').onclick = async () => {
    const out = q('#crCopyDir').value.trim(); if (!out) { toast(t('cross_need_dir'), 'error'); return; }
    try { const j = await api('/api/cross/copy', { out_dir: out, filters: Object.assign({}, S.filter) }); m._wasRunning = 'copy'; m._after = 'sync'; crossOnJobs(j); } catch (e) { toast(e.message, 'error'); }
  };
  q('#crList').onclick = async () => { try { const r = await api('/api/cross/export_list', { filters: Object.assign({}, S.filter) }); toast(`${t('cross_list_done')}: ${r.path} (${r.n})`); } catch (e) { toast(e.message, 'error'); } };
}

// ---------- 裏の処理の表示 ----------
function crossOnJobs(jobs) {
  const m = crossModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.bg) return;
  const mine = j => j && (j.kind === 'cross_import' || j.kind === 'cross_sync' || j.kind === 'cross_copy' || j.kind === 'cross_catalog');
  const cur = jobs.bg.current, rec = (jobs.bg.recent || []).find(mine);
  const queued = (jobs.bg.queued || []).some(mine);
  const st = m.querySelector('#crStatus');
  if (cur && mine(cur)) {
    const pct = cur.total ? Math.round(100 * cur.done / cur.total) : 0;
    st.innerHTML = `<div><b>${esc(cur.label)}</b>: ${cur.done}${cur.total ? ' / ' + cur.total + ` (${pct}%)` : ''}</div>${cur.total ? `<div class="prog"><div style="width:${pct}%"></div></div>` : ''}<div class="info">${esc(cur.current || cur.message || '')}</div>`;
    m._wasRunning = m._wasRunning || cur.kind;
  } else if (queued) {
    st.innerHTML = `<div><b>${esc(t('jb_queued'))}</b></div>`;
    m._wasRunning = m._wasRunning || 'queued';
  } else if (!cur && m._wasRunning) {
    m._wasRunning = false;
    const after = m._after || CR.tab;
    crossModalEl = null;
    S._bgWasRunning = true;
    crossDialog(after).then(() => { const mm = crossModalEl; if (mm && rec && mine(rec)) mm.querySelector('#crStatus').innerHTML = `<div><b>${esc(t('ar_result'))}</b> (${esc(t('jb_state_' + rec.state))}): ${esc(rec.message || rec.error || '')}</div>`; });
  }
}
