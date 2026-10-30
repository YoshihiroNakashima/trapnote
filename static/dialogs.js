'use strict';
/* 窓: プロジェクトを開く、設定（種リスト・追加項目）、取り込み、書き出し、統合、裏の処理、その他の種、担当 */

function pathRow(id, label, kind, value, help, name) {
  return `<div class="form-row"><label>${esc(label)}</label><input type="text" id="${id}" value="${esc(value || '')}" spellcheck="false"><button type="button" class="small" data-pick="${id}" data-kind="${kind}" data-name="${esc(name || '')}">${esc(t('pick'))}</button>${help ? `<div class="help">${esc(help)}</div>` : ''}</div>`;
}
function wirePicks(m) {
  m.querySelectorAll('button[data-pick]').forEach(b => { b.onclick = async () => { const inp = m.querySelector('#' + b.dataset.pick); const p = await pickPath(b.dataset.kind, inp.value || undefined, b.dataset.name || undefined); if (p) { inp.value = p; inp.dispatchEvent(new Event('change')); } }; });
}

// ---------- プロジェクトを開く ----------
async function openProjectDialog() {
  // 2 種類のプロジェクトを並べて出す: 調査回のプロジェクト（動画のフォルダ）と横断プロジェクト（調査回をまたぐ: 統合 / 学習）。最近の一覧には種類と × を付ける
  const recent = (S.snap && S.snap.recent_projects) || [];
  const nameOf = p => p.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean).slice(-2, -1)[0] || p;
  const li = (p, d) => `<li data-p="${esc(p)}" class="${d && d.exists === false ? 'gone' : ''}"><span class="chip ${d && d.kind === 'cross' ? 'human' : 'ai'} op-kind" title="${esc(d && d.kind === 'cross' ? t('cross_chip_tip') : t('op_season_tip'))}">${esc(!d ? '…' : (d.kind === 'cross' ? t('cross_chip') : t('op_season_chip')))}</span> <b>${esc(d ? d.name : nameOf(p))}</b>${d && d.kind === 'cross' && d.purpose ? ` <span class="info">(${esc(d.purpose === 'merge' ? t('cr_purpose_merge') : t('cr_purpose_train'))})</span>` : ''} <span class="info op-path">${esc(p)}</span>${d && d.exists === false ? ` <span class="ng">${esc(t('op_gone'))}</span>` : ''}<button type="button" class="x" data-forget="${esc(p)}" title="${esc(t('op_forget_tip'))}">×</button></li>`;
  const m = showModal(`<h3>${esc(t('open_title'))}</h3>
    <div class="op-cards">
      <div class="op-card"><h4>${esc(t('op_season_h'))}</h4><div class="note">${esc(t('open_help'))}</div>${pathRow('opPath', t('open_project'), 'folder', '', '')}<div class="btn-row"><button type="button" id="mOk" class="primary">${esc(t('op_open_btn'))}</button></div></div>
      <div class="op-card"><h4>${esc(t('cross_new_h'))}</h4><div class="note">${esc(t('cross_new_help'))}</div>${pathRow('opCross', t('cross_folder'), 'folder', '', '')}<div class="btn-row"><button type="button" class="primary" id="opCrossMake">${esc(t('cross_make'))}</button></div></div>
    </div>
    ${recent.length ? `<h4>${esc(t('recent'))} <span class="info">${esc(t('op_recent_help'))}</span></h4><ul class="recent" id="opRecent">${recent.map(p => li(p, null)).join('')}</ul>` : ''}
    <div id="opStatus"></div>
    <div class="modal-actions"><span class="info" style="margin-right:auto">${esc(S.project ? `${t('op_now')}: ${S.project.name}` : t('no_project'))}</span><button id="mCancel">${esc(S.project ? t('op_back') : t('close'))}</button></div>`);
  wirePicks(m);
  histWire(m.querySelector('#opPath'), 'open_path'); histWire(m.querySelector('#opCross'), 'cross_place');
  m.querySelector('#mCancel').onclick = closeModal;
  // 開く・作る: 窓は開いたままで「開いています…」を出し、開けたら閉じる（失敗したら窓に理由を出してやり直せる）
  const go = async (p, create, kind) => {
    const st = m.querySelector('#opStatus');
    m.querySelectorAll('button').forEach(b => { b.disabled = true; });
    st.innerHTML = `<div class="info">${esc(t('op_opening'))}: ${esc(p)}</div>`;
    const ok = await openProjectPath(p, create, kind);
    if (ok) return;                                    // 開けたら openProjectPath が窓を閉じる（横断で動画がまだなければ横断の手順の窓が開く）
    if (!m.isConnected || !m.querySelector('#opStatus')) return;
    m.querySelectorAll('button').forEach(b => { b.disabled = false; });
    st.innerHTML = `<div class="ng">${esc(t('op_failed'))}</div>`;
  };
  m.querySelector('#mOk').onclick = () => { const p = m.querySelector('#opPath').value.trim(); if (p) go(p); };
  m.querySelector('#opPath').addEventListener('keydown', e => { if (e.key === 'Enter') m.querySelector('#mOk').click(); });
  m.querySelector('#opCrossMake').onclick = () => { const p = m.querySelector('#opCross').value.trim(); if (!p) { toast(t('cross_need_folder'), 'error'); return; } go(p, true, 'cross'); };
  const wire = () => {
    m.querySelectorAll('.recent li').forEach(el => { el.onclick = e => { if (e.target.closest('button')) return; go(el.dataset.p); }; });
    m.querySelectorAll('.recent [data-forget]').forEach(b => { b.onclick = async e => {
      e.stopPropagation();
      try { const r = await api('/api/projects/forget', { path: b.dataset.forget }); if (r.state) setSnapshot(r.state); b.closest('li').remove(); if (!m.querySelectorAll('.recent li').length) { const h = m.querySelector('#opRecent'); if (h) { h.previousElementSibling.remove(); h.remove(); } } }
      catch (err) { toast(err.message, 'error'); }
    }; });
  };
  wire();
  if (recent.length) {                       // 種類（横断 / 調査回）と見えるかは project.json を読んで後から埋める（NAS が眠っていると数秒かかる）
    try {
      const r = await api('/api/projects/recent');
      if (!m.isConnected) return;
      for (const d of r.projects || []) { const el = m.querySelector(`.recent li[data-p="${CSS.escape(d.path)}"]`); if (el) el.outerHTML = li(d.path, d); }
      wire();
    } catch (e) { /* 一覧はそのまま */ }
  }
}

// ---------- 設定 ----------
async function settingsDialog() {
  const s = S.settings, p = S.project, ps = (p && p.settings) || {};
  const ff = (S.snap && S.snap.ffmpeg) || {};
  const m = showModal(`<h3>${esc(t('settings_title'))}</h3>
    <div class="form-row"><label>${esc(t('s_user'))}</label><input type="text" id="sUser" value="${esc(s.user_name || '')}"></div>
    <div class="form-row"><label>${esc(t('s_lang'))}</label><select id="sLang"><option value="ja" ${s.language === 'ja' ? 'selected' : ''}>日本語</option><option value="en" ${s.language === 'en' ? 'selected' : ''}>English</option></select></div>
    ${pathRow('sFfmpeg', t('s_ffmpeg'), 'exe', s.ffmpeg_path, `${t('s_ffmpeg_help')} ${ff.ffmpeg || '—'}`)}
    <div class="form-row"><label>${esc(t('s_cache'))}</label><input type="number" id="sCache" value="${esc(s.cache_limit_gb)}" min="1"></div>
    <div class="form-row"><label>${esc(t('s_width'))}</label><input type="number" id="sWidth" value="${esc(s.transcode_max_width)}" min="320" step="16"></div>
    <div class="form-row"><label>${esc(t('s_prefetch'))}</label><input type="number" id="sPrefetch" value="${esc(s.prefetch_n)}" min="0" max="20"></div>
    <div class="form-row"><label>${esc(t('s_copy_workers'))}</label><input type="number" id="sCopyWorkers" value="${esc(s.copy_workers ?? 3)}" min="1" max="8"><div class="help">${esc(t('s_copy_workers_help'))}</div></div>
    <div class="form-row"><label>${esc(t('s_avi_direct'))}</label><input type="checkbox" id="sAviDirect" ${s.avi_direct !== false ? 'checked' : ''}><div class="help">${esc(t('s_avi_direct_help'))}</div></div>
    <div class="form-row"><label>${esc(t('s_auto_advance'))}</label><input type="checkbox" id="sAdv" ${s.auto_advance !== false ? 'checked' : ''}></div>
    <div class="form-row"><label>${esc(t('s_port'))}</label><input type="number" id="sPort" value="${esc(s.port)}"></div>
    ${p ? `<h4>${esc(t('s_project'))}</h4>
    <div class="form-row"><label>${esc(t('s_name'))}</label><input type="text" id="pName" value="${esc(p.name)}"></div>
    <div class="form-row"><label>${esc(t('s_root'))}</label><input type="text" id="pRoot" value="${esc(p.root || '')}" readonly><button type="button" class="small" id="pRootPick">${esc(t('root_pick'))}</button></div>
    <div class="form-row"><label>${esc(t('s_pattern'))}</label><input type="text" id="pPattern" value="${esc(ps.pattern || '')}" spellcheck="false"><button type="button" class="small" id="pPrev">${esc(t('s_preview'))}</button><div class="help">${esc(t('s_pattern_help'))}</div></div>
    <div id="pPrevBox"></div>
    <div class="form-row"><label>${esc(t('s_skip'))}</label><input type="text" id="pSkip" value="${esc((ps.skip_names || []).join(', '))}"></div>
    <div class="form-row"><label>${esc(t('s_z'))}</label><input type="checkbox" id="pZ" ${ps.z_is_utc ? 'checked' : ''}><div class="help">${esc(t('s_z_help'))}</div></div>
    <div class="btn-row" style="margin-top:8px"><button type="button" id="pSpecies">${esc(t('s_species_edit'))}</button><button type="button" id="pFields">${esc(t('s_fields_edit'))}</button><button type="button" id="pMeta">${esc(t('s_meta_redo'))}</button></div>
    <h4>${esc(t('s_cache_title'))}</h4><div class="note">${esc(t('s_cache_help'))}</div><div id="pCache" class="info">${esc(t('loading'))}</div>` : ''}
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('save'))}</button></div>`);
  wirePicks(m);
  m.querySelector('#mCancel').onclick = closeModal;
  if (p) {
    m.querySelector('#pPrev').onclick = async () => {
      const r = await api('/api/project/pattern/preview', { pattern: m.querySelector('#pPattern').value });
      const names = r.names.filter(n => n !== 'file');
      m.querySelector('#pPrevBox').innerHTML = r.rows.length ? `<table class="preview"><tr><th>rel_path</th>${names.map(n => `<th>${esc(n)}</th>`).join('')}</tr>${r.rows.map(row => `<tr><td>${esc(row.rel_path)}</td>${names.map(n => `<td>${esc(row[n] || '')}</td>`).join('')}</tr>`).join('')}</table>` : `<div class="note">${esc(t('pattern_preview_none'))}</div>`;
    };
    m.querySelector('#pRootPick').onclick = async () => { const r = await pickPath('folder', p.root); if (r) { const res = await api('/api/project/root', { path: r }); setSnapshot(res.state); m.querySelector('#pRoot').value = r; reloadList(); } };
    m.querySelector('#pSpecies').onclick = () => speciesDialog();
    m.querySelector('#pFields').onclick = () => fieldsDialog();
    const gb = b => (b / 1e9).toFixed(2) + ' GB';
    const renderCache = async () => {
      try {
        const c = await api('/api/project/cache');
        const kinds = [['video', t('s_cache_video')], ['thumb', t('s_cache_thumb')], ['crops', t('s_cache_crops')], ['features', t('s_cache_features')]];
        m.querySelector('#pCache').innerHTML = (c.disk ? `<div>${esc(t('s_disk_free'))}: <b>${gb(c.disk.free)}</b> / ${gb(c.disk.total)}</div>` : '') +
          kinds.map(([k, label]) => `<div class="fld"><span>${esc(label)}</span><span>${gb(c[k].bytes)} (${c[k].n})</span> <button type="button" class="small" data-cc="${k}" ${c[k].n ? '' : 'disabled'}>${esc(t('s_cache_clear'))}</button></div>`).join('');
        m.querySelectorAll('[data-cc]').forEach(b => { b.onclick = async () => {
          const k = b.dataset.cc;
          if (k === 'features' && !confirm(t('s_cache_features_q'))) return;
          try { const r = await api('/api/project/cache/clear', { kinds: [k] }); toast(`${t('s_cache_cleared')}: ${gb(r.freed)}`); renderCache(); } catch (e) { toast(e.message, 'error'); }
        }; });
      } catch (e) { m.querySelector('#pCache').textContent = e.message; }
    };
    renderCache();
    m.querySelector('#pMeta').onclick = async () => { await api('/api/jobs/meta', { redo: true, filters: {} }); toast(t('s_meta_redo')); };
  }
  m.querySelector('#mOk').onclick = async () => {
    try {
      const ch = { user_name: m.querySelector('#sUser').value, language: m.querySelector('#sLang').value, ffmpeg_path: m.querySelector('#sFfmpeg').value,
        cache_limit_gb: parseFloat(m.querySelector('#sCache').value), transcode_max_width: parseInt(m.querySelector('#sWidth').value), prefetch_n: parseInt(m.querySelector('#sPrefetch').value), copy_workers: parseInt(m.querySelector('#sCopyWorkers').value),
        auto_advance: m.querySelector('#sAdv').checked, port: parseInt(m.querySelector('#sPort').value), avi_direct: m.querySelector('#sAviDirect').checked };
      S.settings = await api('/api/settings', ch);
      if (p) {
        const pc = { name: m.querySelector('#pName').value, pattern: m.querySelector('#pPattern').value, skip_names: m.querySelector('#pSkip').value, z_is_utc: m.querySelector('#pZ').checked };
        const r = await api('/api/project/settings', pc);
        setSnapshot(r.state);
      } else await refreshState();
      closeModal(); reloadList(true);
      if (S.cur) { renderSpeciesPanel(); renderFieldsPanel(); }
    } catch (e) { toast(e.message, 'error'); }
  };
}

// ---------- 種リスト ----------
async function speciesDialog() {
  const sp = await api('/api/species');
  let rows = JSON.parse(JSON.stringify(sp.species));
  const render = () => {
    const m = showModal(`<h3>${esc(t('sp_title'))}</h3><div class="note">${esc(t('sp_help'))}</div>
      <div class="btn-row"><button type="button" class="small" id="spAllOn">${esc(t('sp_all_on'))}</button><button type="button" class="small" id="spAllOff">${esc(t('sp_all_off'))}</button><button type="button" class="small" id="spAdd">${esc(t('sp_add'))}</button><button type="button" class="small" id="spImport">${esc(t('sp_import'))}</button><button type="button" class="small" id="spExport">${esc(t('sp_export'))}</button></div>
      <div style="max-height:60vh;overflow:auto"><table class="list"><tr><th>${esc(t('sp_use'))}</th><th>${esc(t('sp_ja'))}</th><th>${esc(t('sp_en'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sp_rank'))}</th><th>${esc(t('sp_csv'))}</th><th>${esc(t('sp_nn'))}</th><th>${esc(t('sp_key'))}</th><th>SpeciesNet</th><th></th></tr>
      ${rows.map((r, i) => `<tr class="${r.enabled ? '' : 'off'}" data-i="${i}"><td><input type="checkbox" data-k="enabled" ${r.enabled ? 'checked' : ''}></td><td><input data-k="name_ja" value="${esc(r.name_ja)}"></td><td><input data-k="name_en" value="${esc(r.name_en)}"></td><td><input data-k="name_sci" value="${esc(r.name_sci)}"></td><td><input data-k="rank" value="${esc(r.rank)}" style="width:40px"></td><td><input data-k="csv_label" value="${esc(r.csv_label)}"></td><td><input type="checkbox" data-k="non_native" ${r.non_native ? 'checked' : ''}></td><td><input data-k="key" value="${esc(r.key)}" maxlength="1" style="width:28px"></td><td><input data-k="speciesnet" value="${esc(r.speciesnet)}"></td><td><button type="button" class="small del">×</button></td></tr>`).join('')}
      </table></div>
      <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('save'))}</button></div>`);
    const read = () => { m.querySelectorAll('tr[data-i]').forEach(tr => { const r = rows[parseInt(tr.dataset.i)]; tr.querySelectorAll('[data-k]').forEach(inp => { r[inp.dataset.k] = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value; }); }); };
    m.querySelectorAll('button.del').forEach(b => { b.onclick = () => { read(); rows.splice(parseInt(b.closest('tr').dataset.i), 1); render(); }; });
    m.querySelector('#spAllOn').onclick = () => { read(); rows.forEach(r => r.enabled = 1); render(); };
    m.querySelector('#spAllOff').onclick = () => { read(); rows.forEach(r => r.enabled = 0); render(); };
    m.querySelector('#spAdd').onclick = () => { read(); rows.push({ code: '', name_ja: '', name_en: '', name_sci: '', rank: '種', csv_label: '', non_native: 0, speciesnet: '', key: '', enabled: 1 }); render(); m.querySelector('tr:last-child input[data-k=name_ja]').focus(); };
    m.querySelector('#spImport').onclick = async () => { const p = await pickPath('csv'); if (!p) return; read(); await api('/api/species', { rows }); const r = await api('/api/species/import', { path: p }); rows = r.species; render(); toast(`${r.n}`); };
    m.querySelector('#spExport').onclick = async () => { const p = await pickPath('savecsv', undefined, 'species.csv'); if (!p) return; read(); await api('/api/species', { rows }); const r = await api('/api/species/export', { path: p }); toast(r.path); };
    m.querySelector('#mCancel').onclick = closeModal;
    m.querySelector('#mOk').onclick = async () => { read(); try { S.species = await api('/api/species', { rows }); rebuildSpMap(); closeModal(); renderFilters(); if (S.cur) { renderSpeciesPanel(); renderFieldsPanel(); } renderWindow(true); } catch (e) { toast(e.message, 'error'); } };
  };
  render();
}

// ---------- 追加項目 ----------
async function fieldsDialog() {
  const fd = await api('/api/fields');
  let rows = JSON.parse(JSON.stringify(fd.fields));
  const types = ['select', 'multiselect', 'check', 'number', 'text', 'time'];
  const render = () => {
    const m = showModal(`<h3>${esc(t('fd_title'))}</h3><div class="note">${esc(t('fd_help'))}</div>
      <div class="btn-row"><button type="button" class="small" id="fdAdd">${esc(t('fd_add'))}</button></div>
      <div style="max-height:60vh;overflow:auto"><table class="list"><tr><th>${esc(t('fd_key'))}</th><th>${esc(t('fd_ja'))}</th><th>${esc(t('fd_en'))}</th><th>${esc(t('fd_type'))}</th><th>${esc(t('fd_scope'))}</th><th>${esc(t('fd_repeat'))}</th><th>${esc(t('fd_group'))}</th><th>${esc(t('fd_options'))}</th><th>${esc(t('fd_default'))}</th><th></th></tr>
      ${rows.map((f, i) => `<tr data-i="${i}"><td><input data-k="key" value="${esc(f.key)}" style="width:90px"></td><td><input data-k="label_ja" value="${esc(f.label_ja)}"></td><td><input data-k="label_en" value="${esc(f.label_en)}"></td>
        <td><select data-k="type">${types.map(x => `<option value="${x}" ${f.type === x ? 'selected' : ''}>${esc(t('t_' + x))}</option>`).join('')}</select></td>
        <td><select data-k="scope"><option value="item" ${f.scope === 'item' ? 'selected' : ''}>${esc(t('sc_item'))}</option><option value="species" ${f.scope === 'species' ? 'selected' : ''}>${esc(t('sc_species'))}</option></select></td>
        <td><input type="checkbox" data-k="repeat" ${f.repeat ? 'checked' : ''}></td><td><input data-k="group" value="${esc(f.group || '')}" style="width:80px"></td><td><input data-k="options" value="${esc((f.options || []).join(', '))}"></td><td><input data-k="default" value="${esc(f.default || '')}" style="width:60px"></td>
        <td><button type="button" class="small up">↑</button><button type="button" class="small del">×</button></td></tr>`).join('')}
      </table></div>
      <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('save'))}</button></div>`);
    const read = () => { m.querySelectorAll('tr[data-i]').forEach(tr => { const f = rows[parseInt(tr.dataset.i)]; tr.querySelectorAll('[data-k]').forEach(inp => { f[inp.dataset.k] = inp.type === 'checkbox' ? inp.checked : inp.value; }); }); };
    m.querySelectorAll('button.del').forEach(b => { b.onclick = () => { read(); rows.splice(parseInt(b.closest('tr').dataset.i), 1); render(); }; });
    m.querySelectorAll('button.up').forEach(b => { b.onclick = () => { read(); const i = parseInt(b.closest('tr').dataset.i); if (i > 0) { [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; render(); } }; });
    m.querySelector('#fdAdd').onclick = () => { read(); rows.push({ key: '', label_ja: '', label_en: '', type: 'text', scope: 'species', repeat: false, group: '', options: [], default: '' }); render(); };
    m.querySelector('#mCancel').onclick = closeModal;
    m.querySelector('#mOk').onclick = async () => { read(); try { S.fields = await api('/api/fields', { fields: rows.map((f, i) => Object.assign({}, f, { order: i })) }); closeModal(); if (S.cur) renderFieldsPanel(); } catch (e) { toast(e.message, 'error'); } };
  };
  render();
}

/* 右の欄の「＋ 項目」: 項目を 1 つその場で足す（細かい編集は 設定 → 追加項目） */
async function addFieldDialog(scope) {
  const types = ['select', 'multiselect', 'check', 'number', 'text', 'time'];
  const m = showModal(`<h3>${esc(t('fd_add'))}</h3><div class="note">${esc(t('fd_add_help'))}</div>
    <div class="form-row"><label>${esc(t('fd_ja'))}</label><input type="text" id="afJa" spellcheck="false"></div>
    <div class="form-row"><label>${esc(t('fd_en'))}</label><input type="text" id="afEn" spellcheck="false" placeholder="${esc(t('fd_optional'))}"></div>
    <div class="form-row"><label>${esc(t('fd_key'))}</label><input type="text" id="afKey" spellcheck="false" style="width:160px" placeholder="${esc(t('fd_key_auto'))}"></div>
    <div class="form-row"><label>${esc(t('fd_type'))}</label><select id="afType">${types.map(x => `<option value="${x}">${esc(t('t_' + x))}</option>`).join('')}</select></div>
    <div class="form-row" id="afOptRow"><label>${esc(t('fd_options'))}</label><input type="text" id="afOpts" spellcheck="false" placeholder="${esc(t('fd_options_ph'))}"></div>
    <div class="form-row"><label>${esc(t('fd_scope'))}</label><select id="afScope"><option value="species">${esc(t('sc_species'))}</option><option value="item">${esc(t('sc_item'))}</option></select></div>
    <div class="form-row"><label>${esc(t('fd_default'))}</label><input type="text" id="afDef" spellcheck="false" style="width:160px"></div>
    <div class="form-row"><label>${esc(t('fd_repeat'))}</label><label class="chk"><input type="checkbox" id="afRepeat"> ${esc(t('fd_repeat_l'))}</label></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('fd_add_ok'))}</button></div>`);
  const q = sel => m.querySelector(sel);
  q('#afScope').value = scope === 'item' ? 'item' : 'species';
  const slug = v => v.normalize('NFKD').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  const optRow = () => q('#afOptRow').classList.toggle('hidden', !['select', 'multiselect'].includes(q('#afType').value));
  q('#afType').addEventListener('change', optRow); optRow();
  q('#afEn').addEventListener('input', () => { if (!q('#afKey').dataset.edited) q('#afKey').value = slug(q('#afEn').value).toLowerCase(); });
  q('#afKey').addEventListener('input', () => { q('#afKey').dataset.edited = '1'; });
  q('#mCancel').onclick = closeModal;
  q('#mOk').onclick = async () => {
    const ja = q('#afJa').value.trim(), en = q('#afEn').value.trim();
    if (!ja && !en) { toast(t('fd_need_name'), 'error'); return; }
    const existing = (S.fields.fields || []);
    let key = slug(q('#afKey').value.trim() || en || '').toLowerCase();
    if (!key || /^[0-9]/.test(key)) key = 'field' + (existing.length + 1);
    while (existing.some(f => f.key === key)) key += '_';
    const type = q('#afType').value;
    const nf = { key, label_ja: ja || en, label_en: en || ja, type, scope: q('#afScope').value, repeat: q('#afRepeat').checked, group: q('#afRepeat').checked ? key : '',
      options: ['select', 'multiselect'].includes(type) ? q('#afOpts').value.split(/[,、\n]/).map(x => x.trim()).filter(Boolean) : [], default: q('#afDef').value.trim() };
    try {
      S.fields = await api('/api/fields', { fields: existing.concat([nf]).map((f, i) => Object.assign({}, f, { order: i })) });
      closeModal();
      if (S.cur) renderFieldsPanel();
      toast(`${t('fd_added')}: ${ja || en}`);
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#afJa').focus();
}

// ---------- 書き出し ----------
async function exportDialog(preset) {
  // preset = {ids: [...]}（一覧で選んだ動画を右クリック → 書き出し。範囲に「選択した n 件」が加わり、それが既定になる）
  const def = `${S.project.dir}/exports/trapnote_${new Date().toISOString().slice(0, 10)}.csv`;
  const selIds = (preset && preset.ids) || [];
  const m = showModal(`<h3>${esc(t('ex_title'))}</h3>
    ${pathRow('exPath', t('ex_path'), 'savecsv', def, '', 'trapnote.csv')}
    <div class="form-row"><label>${esc(t('ex_range'))}</label><select id="exRange">${selIds.length ? `<option value="selection">${esc(t('ex_selection'))} (${selIds.length})</option>` : ''}<option value="all">${esc(t('ex_all'))}</option><option value="filter">${esc(t('ex_filter'))}</option><option value="verified">${esc(t('ex_verified'))}</option></select></div>
    <div class="form-row"><label></label><label><input type="checkbox" id="exRecords"> ${esc(t('ex_records'))}</label></div>
    <div class="form-row"><label></label><label><input type="checkbox" id="exJudged"> ${esc(t('ex_judged_only'))}</label></div>
    <div id="exResult" class="note"></div>
    <h4>${esc(t('ex_index'))}</h4><div class="note">${esc(t('ex_index_help'))}</div><button type="button" id="exIndex">${esc(t('ex_index'))}</button>
    <h4>${esc(t('ex_md'))}</h4><div class="note">${esc(t('ex_md_help'))}</div><button type="button" id="exMd">${esc(t('ex_md'))}</button>
    <h4>${esc(t('ex_tl'))}</h4><div class="note">${esc(t('ex_tl_help'))}</div>
    ${pathRow('exTlPath', t('ex_path'), 'saveddb', `${S.project.root}/${(S.project.root || '').split('/').pop()}.ddb`, t('ex_tl_path_help'), `${(S.project.root || '').split('/').pop()}.ddb`)}
    <div class="form-row"><label>${esc(t('ex_tl_style'))}</label><select id="exTlStyle"><option value="choice">${esc(t('ex_tl_choice'))}</option><option value="flags">${esc(t('ex_tl_flags'))}</option></select></div>
    <div class="form-row"><label></label><span><label class="chk"><input type="checkbox" id="exTlFields" checked> ${esc(t('ex_tl_fields'))}</label>　<label class="chk"><input type="checkbox" id="exTlCorrected" checked> ${esc(t('ex_tl_corrected'))}</label></span></div>
    <button type="button" id="exTl">${esc(t('ex_tl_run'))}</button>
    <h4>${esc(t('ex_yolo'))}</h4><div class="note">${esc(t('ex_yolo_help'))}</div>
    <div class="form-row"><label>${esc(t('ex_yolo_where'))}</label><select id="exYoloWhere"><option value="new">${esc(t('ex_yolo_new'))}</option><option value="in_place">${esc(t('ex_yolo_in_place'))}</option></select></div>
    <div id="exYoloNewRows">${pathRow('exYoloDir', t('ex_yolo_dir'), 'folder', `${S.project.dir}/exports/yolo`, '')}
    <div class="form-row"><label>${esc(t('ex_yolo_class'))}</label><select id="exYoloClass"><option value="animal0">${esc(t('ex_yolo_class0'))}</option><option value="species">${esc(t('ex_yolo_class_sp'))}</option></select></div>
    <div class="form-row"><label></label><span><label class="chk"><input type="checkbox" id="exYoloPer" checked> ${esc(t('ex_yolo_per'))}</label>　<label class="chk"><input type="checkbox" id="exYoloImg" checked> ${esc(t('ex_yolo_img'))}</label></span></div></div>
    <button type="button" id="exYolo">${esc(t('ex_yolo_run'))}</button><div id="exYoloStatus" class="note"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="mOk" class="primary">${esc(t('run'))}</button></div>`);
  exportModalEl = m;
  m.querySelector('#exYoloWhere').addEventListener('change', () => { m.querySelector('#exYoloNewRows').classList.toggle('hidden', m.querySelector('#exYoloWhere').value === 'in_place'); });
  m.querySelector('#exYolo').onclick = async () => {
    const range = m.querySelector('#exRange').value;
    const filters = range === 'selection' ? { ids: selIds } : (range === 'filter' ? Object.assign({}, S.filter) : (range === 'verified' ? { status: 'verified' } : {}));
    const inPlace = m.querySelector('#exYoloWhere').value === 'in_place';
    if (inPlace) { exportModalEl = null; if (!await confirmModal(t('ex_yolo'), esc(t('ex_yolo_in_place_q')), t('run'), true)) { exportDialog(); return; } await exportDialog(); }
    const mm = exportModalEl || m;
    try {
      const j = await api('/api/yolo/export', { out_dir: mm.querySelector('#exYoloDir').value.trim(), filters, per_species: mm.querySelector('#exYoloPer').checked, class_mode: mm.querySelector('#exYoloClass').value, write_images: mm.querySelector('#exYoloImg').checked, in_place: inPlace });
      mm._yoloRunning = true; yoloExportOnJobs(j);
    } catch (e) { toast(e.message, 'error'); }
  };
  wirePicks(m);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#exIndex').onclick = async () => { const r = await api('/api/export/index', {}); m.querySelector('#exResult').textContent = `${t('ex_done')}: ${r.path} (${r.rows})`; };
  m.querySelector('#exMd').onclick = async () => {
    const range = m.querySelector('#exRange').value;
    const filters = range === 'selection' ? { ids: selIds } : (range === 'filter' ? Object.assign({}, S.filter) : (range === 'verified' ? { status: 'verified' } : {}));
    const path = await pickPath('savejson', `${S.project.dir}/exports`, 'detections_md.json');
    if (!path) return;
    try { const r = await api('/api/export/md_json', { path, filters }); m.querySelector('#exResult').textContent = `${t('ex_done')}: ${r.path} (${r.items} ${t('items')})`; } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelector('#exTl').onclick = async () => {
    const range = m.querySelector('#exRange').value;
    const filters = range === 'selection' ? { ids: selIds } : (range === 'filter' ? Object.assign({}, S.filter) : (range === 'verified' ? { status: 'verified' } : {}));
    const path = m.querySelector('#exTlPath').value.trim();
    if (!path) { toast(t('ex_tl_need_path'), 'error'); return; }
    try {
      const r = await api('/api/export/timelapse', { path, filters, species_style: m.querySelector('#exTlStyle').value, include_fields: m.querySelector('#exTlFields').checked, use_corrected: m.querySelector('#exTlCorrected').checked });
      m.querySelector('#exResult').textContent = `${t('ex_done')}: ${r.ddb} / ${r.tdb} (${r.rows} ${t('items')}${r.skipped ? `, ${t('ex_tl_skipped')} ${r.skipped}` : ''})`;
    } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelector('#mOk').onclick = async () => {
    const range = m.querySelector('#exRange').value;
    const filters = range === 'selection' ? { ids: selIds } : (range === 'filter' ? Object.assign({}, S.filter) : (range === 'verified' ? { status: 'verified' } : {}));
    try {
      const r = await api('/api/export/csv', { path: m.querySelector('#exPath').value, filters, records: m.querySelector('#exRecords').checked, judged_only: m.querySelector('#exJudged').checked });
      m.querySelector('#exResult').textContent = `${t('ex_done')}: ${r.path} (${r.rows} ${t('im_rows')}, ${r.items} ${t('items')})${r.records ? ' + ' + r.records.path : ''}`;
    } catch (e) { toast(e.message, 'error'); }
  };
}

// ---------- 取り込み ----------
async function importDialog(kind) {
  if (kind === 'timelapse') return timelapseDialog();
  const m = showModal(`<h3>${esc(t('import'))}</h3>
    <div class="form-row"><label>${esc(t('im_kind'))}</label><select id="imKind"><option value="csv">${esc(t('im_kind_csv'))}</option><option value="timelapse">${esc(t('im_kind_tl'))}</option></select></div>
    <div class="note">${esc(t('im_help'))}</div>
    ${pathRow('imPath', t('im_csv'), 'csv', '', '')}
    <div id="imBody"></div>
    <div class="form-row"><label></label><label><input type="checkbox" id="imVerified"> ${esc(t('im_as_verified'))}</label></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="mOk" class="primary" disabled>${esc(t('run'))}</button></div>`);
  wirePicks(m);
  m.querySelector('#imKind').addEventListener('change', () => { if (m.querySelector('#imKind').value === 'timelapse') timelapseDialog(); });
  m.querySelector('#mCancel').onclick = closeModal;
  let pv = null, values = null;
  const colSel = (id, cols, val) => `<select id="${id}"><option value=""></option>${cols.map(c => `<option value="${esc(c)}" ${c === val ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
  const allCodes = (S.species.species || []).concat(S.species.fixed || []);
  const load = async () => {
    const p = m.querySelector('#imPath').value.trim(); if (!p) return;
    try { pv = await api('/api/import/csv/preview', { path: p }); } catch (e) { toast(e.message, 'error'); return; }
    const g = pv.guess || {};
    m.querySelector('#imBody').innerHTML = `<table class="preview"><tr>${pv.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr>${pv.rows.slice(0, 4).map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table>
      <div class="form-row"><label>${esc(t('im_pathcols'))}</label><span style="display:flex;gap:4px">${colSel('imP1', pv.columns, g.path[0] || '')}${colSel('imP2', pv.columns, g.path[1] || '')}${colSel('imP3', pv.columns, g.path[2] || '')}</span></div>
      <div class="form-row"><label>${esc(t('im_species'))}</label>${colSel('imSp', pv.columns, g.species || '')}</div>
      <div class="form-row"><label>${esc(t('im_datetime'))}</label>${colSel('imDt', pv.columns, g.datetime || '')}</div>
      <div class="form-row"><label>${esc(t('im_corrected'))}</label>${colSel('imDtc', pv.columns, pv.columns.find(c => c.toLowerCase() === 'datetimecorrected') || '')}</div>
      <h4>${esc(t('im_mapping'))}</h4><div class="note">${esc(t('im_mapping_help'))}</div><div id="imMap"></div><div id="imResult" class="note"></div>`;
    const loadValues = async () => {
      const col = m.querySelector('#imSp').value; if (!col) return;
      values = await api('/api/import/csv/values', { path: p, column: col });
      m.querySelector('#imMap').innerHTML = `<table class="list tl-map"><tr><th>CSV</th><th>n</th><th>→</th></tr>${values.values.map(v => `<tr class="${v.exact ? '' : (v.guess ? 'guess' : 'nomatch')}"><td>${esc(v.value)}</td><td>${v.count}</td><td><select data-v="${esc(v.value)}"><option value="">${esc(t('tl_skip_choice'))}</option>${allCodes.map(c => `<option value="${c.code}" ${(v.guess || '') === c.code ? 'selected' : ''}>${esc(spName(c))} (${esc(c.csv_label || c.code)})</option>`).join('')}</select>${v.exact ? '' : (v.guess ? ` <span class="info">${esc(t('tl_guess'))}</span>` : (v.value ? ` <span class="flag-low">${esc(t('tl_nomatch'))}</span>` : ''))}</td></tr>`).join('')}</table>`;
      m.querySelector('#mOk').disabled = false;
    };
    m.querySelector('#imSp').addEventListener('change', loadValues);
    if (g.species) loadValues();
  };
  m.querySelector('#imPath').addEventListener('change', load);
  m.querySelector('#imPath').addEventListener('keydown', e => { if (e.key === 'Enter') load(); });
  m.querySelector('#mOk').onclick = async () => {
    const mapping = {}; m.querySelectorAll('#imMap select').forEach(s => { if (s.value) mapping[s.dataset.v] = s.value; });
    const path_cols = ['imP1', 'imP2', 'imP3'].map(id => m.querySelector('#' + id).value).filter(Boolean);
    try {
      m.querySelector('#mOk').disabled = true;
      const r = await api('/api/import/csv', { path: m.querySelector('#imPath').value, path_cols, species_col: m.querySelector('#imSp').value, datetime_col: m.querySelector('#imDt').value, corrected_col: m.querySelector('#imDtc').value, mapping, as_verified: m.querySelector('#imVerified').checked });
      m.querySelector('#imResult').innerHTML = `<b>${esc(t('im_result'))}</b>: ${r.rows} ${t('im_rows')} · ${t('im_items')} ${r.items} · ${t('im_skipped')} ${r.skipped_verified} · ${t('im_unmatched')} ${r.unmatched_paths}${r.unmatched_path_examples.length ? ' (' + esc(r.unmatched_path_examples.join(', ')) + ')' : ''}${r.unmatched_labels.length ? ' · ' + t('im_unlabel') + ': ' + esc(r.unmatched_labels.map(x => x.value + '×' + x.count).join(', ')) : ''}`;
      m.querySelector('#mOk').disabled = false;
      refreshFacets(); reloadList(true);
    } catch (e) { m.querySelector('#mOk').disabled = false; toast(e.message, 'error'); }
  };
}

// ---------- Timelapse の取り込み（手順つきの窓） ----------
async function timelapseDialog() {
  const fixedOpts = (S.species.fixed || []);
  const m = showModal(`<h3>${esc(t('timelapse'))}</h3><div class="note">${esc(t('tl_help'))}</div>
    <div class="btn-row"><button type="button" class="small" id="tlYolo">${esc(t('yl_title'))}…</button><span class="info">${esc(t('yl_link'))}</span></div>
    <div class="step" id="tlS1"><div class="num">1</div><div class="head">${esc(t('tl_step1'))}</div>
      <div class="body"><div class="note">${esc(t('tl_step1_help'))}</div>
        <div class="pathbox"><input type="text" id="tlPath" placeholder=".tdb / .ddb" spellcheck="false"><button type="button" class="primary" id="tlPick">${esc(t('pick'))}</button></div></div></div>
    <div class="step off" id="tlS2"><div class="num">2</div><div class="head">${esc(t('tl_step2'))}</div><div class="body" id="tlBody2"><div class="note">${esc(t('tl_step2_help'))}</div></div></div>
    <div class="step off" id="tlS3"><div class="num">3</div><div class="head">${esc(t('tl_step3'))}</div><div class="body" id="tlBody3"></div></div>
    <div class="step off" id="tlS4"><div class="num">4</div><div class="head">${esc(t('tl_step4'))}</div><div class="body"><div class="btn-row"><button type="button" class="primary" id="tlRun" disabled>${esc(t('run'))}</button></div><div id="tlResult" class="note"></div></div></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#tlYolo').onclick = () => yoloDialog();
  let pv = null;
  const roleLabel = r => t('tl_role_' + r);
  const load = async () => {
    const p = m.querySelector('#tlPath').value.trim(); if (!p) return;
    try { pv = await api('/api/import/timelapse/preview', { path: p }); } catch (e) { toast(e.message, 'error'); return; }
    const tpl = pv.template;
    const rows = pv.species_map || [];
    const allSp = (S.species.species || []);
    const spOpt = (sp, sel) => `<option value="${esc(sp.code)}" ${sel === sp.code ? 'selected' : ''}>${esc(spName(sp))}${sp.name_sci ? ' (' + esc(sp.name_sci) + ')' : ''}${sp.enabled ? '' : ' ' + t('tl_sp_disabled')}</option>`;
    const mapRow = r => {
      const isFlag = r.kind === 'flag';
      const sel = r.guess || (isFlag ? '__field__' : '__new__');
      const mark = r.exact ? '' : (r.guess ? ` <span class="info" title="${esc(t('tl_guess_tip'))}">${esc(t('tl_guess'))}</span>` : (isFlag ? '' : ` <span class="flag-low">${esc(t('tl_nomatch'))}</span>`));
      return `<tr class="${r.exact ? '' : (r.guess ? 'guess' : (isFlag ? '' : 'nomatch'))}"><td>${esc(r.label)}${r.in_template ? '' : ' <span class="info">' + esc(t('tl_data_only')) + '</span>'}</td><td>${r.n}</td><td>
        <select data-map="${esc(r.key)}">
          ${isFlag ? `<option value="__field__" ${sel === '__field__' ? 'selected' : ''}>${esc(t('tl_keep_field'))}</option>` : ''}
          <optgroup label="${esc(t('tl_grp_species'))}">${allSp.map(sp => spOpt(sp, sel)).join('')}</optgroup>
          <optgroup label="${esc(t('tl_grp_fixed'))}">${fixedOpts.map(f => `<option value="${f.code}" ${sel === f.code ? 'selected' : ''}>${esc(spName(f))}</option>`).join('')}</optgroup>
          <option value="__new__" ${sel === '__new__' ? 'selected' : ''}>${esc(t('tl_new_species'))} (${esc(r.label.replace(/ \(Flag\)$/, ''))})</option>
          <option value="">${esc(t('tl_skip_choice'))}</option>
        </select>${mark}</td></tr>`;
    };
    m.querySelector('#tlBody2').innerHTML = `<div class="note">${esc(t('tl_step2_help'))}</div>
      <div class="note">${esc(t('tl_version'))}: ${esc(tpl.version || '?')} · ${esc(t('tl_controls'))}: ${tpl.controls.length}${tpl.has_data ? ` · ${esc(t('tl_data'))}: ${tpl.n_data} ${esc(t('tl_rows'))}` : ''}</div>
      <div style="max-height:22vh;overflow:auto"><table class="list"><tr><th>DataLabel</th><th>Label</th><th>Type</th><th>Default</th><th>${esc(t('tl_role'))}</th></tr>
      ${tpl.controls.map(c => `<tr class="${c.role === 'skip' ? 'off' : ''}"><td>${esc(c.data_label)}</td><td>${esc(c.label)}</td><td>${esc(c.type)}</td><td>${esc(c.default)}</td><td>${esc(roleLabel(c.role))}${c.role === 'species' ? ` (${c.choices.length})` : ''}</td></tr>`).join('')}</table></div>
      ${rows.length ? `<h4>${esc(t('tl_map_title'))}</h4><div class="note">${esc(t('tl_map_help'))}</div>
      <div style="max-height:34vh;overflow:auto"><table class="list tl-map"><tr><th>Timelapse</th><th>n</th><th>→ TrapNote</th></tr>${rows.map(mapRow).join('')}</table></div>
      <label class="chk" style="margin-top:6px"><input type="checkbox" id="tlOnly" checked> ${esc(t('tl_only_mapped'))}</label>` : ''}`;
    m.querySelector('#tlBody3').innerHTML = `
      <div class="form-row"><label></label><label><input type="checkbox" id="tlTpl" checked> ${esc(t('tl_do_template'))}</label></div>
      ${tpl.has_data ? `<div class="form-row"><label></label><label><input type="checkbox" id="tlData" checked> ${esc(t('tl_do_data'))}</label></div>
      <div class="form-row"><label>${esc(t('tl_prefix'))}</label><input type="text" id="tlPrefix" value="${esc(pv.prefix || '')}" spellcheck="false"><div class="help">${esc(t('tl_prefix_help'))}${pv.folders ? ' · RelativePath: ' + esc(pv.folders.slice(0, 5).join(', ')) + (pv.folders.length > 5 ? ' …' : '') : ''}</div></div>
      <div class="form-row"><label></label><label><input type="checkbox" id="tlVerified"> ${esc(t('im_as_verified'))}</label></div>` : ''}`;
    ['tlS2', 'tlS3', 'tlS4'].forEach(id => m.querySelector('#' + id).classList.remove('off'));
    m.querySelector('#tlRun').disabled = false;
  };
  m.querySelector('#tlPick').onclick = async () => { const p = await pickPath('timelapse', S.project.root); if (p) { m.querySelector('#tlPath').value = p; load(); } };
  m.querySelector('#tlPath').addEventListener('change', load);
  m.querySelector('#tlPath').addEventListener('keydown', e => { if (e.key === 'Enter') load(); });
  m.querySelector('#tlRun').onclick = async () => {
    if (!pv) return;
    const hasData = pv.template.has_data;
    const mapping = {}; m.querySelectorAll('select[data-map]').forEach(sel => { mapping[sel.dataset.map] = sel.value; });
    try {
      m.querySelector('#tlRun').disabled = true;
      const only = m.querySelector('#tlOnly');
      const r = await api('/api/import/timelapse', { path: m.querySelector('#tlPath').value, template: m.querySelector('#tlTpl').checked, mapping, only_mapped: !!(only && only.checked),
        data: hasData && m.querySelector('#tlData').checked, prefix: hasData ? m.querySelector('#tlPrefix').value : '', as_verified: hasData && m.querySelector('#tlVerified').checked });
      S.species = r.species; S.fields = r.fields; rebuildSpMap();
      renderSpeciesPanel(); renderFieldsPanel(); renderFilters();
      const parts = [];
      if (r.template) parts.push(`${t('tl_sp_added')} ${r.template.species_added} · ${t('tl_fd_added')} ${r.template.fields_added}${r.template.fields_removed ? ' · ' + t('tl_fd_removed') + ' ' + r.template.fields_removed : ''}`);
      if (r.data) parts.push(`${r.data.rows} ${t('im_rows')} · ${t('im_items')} ${r.data.items} · ${t('im_skipped')} ${r.data.skipped_verified} · ${t('tl_favs')} ${r.data.favorites} · ${t('tl_notes')} ${r.data.notes} · ${t('im_unmatched')} ${r.data.unmatched_paths}${r.data.unmatched_path_examples.length ? ' (' + esc(r.data.unmatched_path_examples.slice(0, 3).join(', ')) + ')' : ''}${r.data.unmatched_labels.length ? ' · ' + t('im_unlabel') + ': ' + esc(r.data.unmatched_labels.map(x => x.value + '×' + x.count).join(', ')) : ''}`);
      m.querySelector('#tlResult').innerHTML = `<b>${esc(t('tl_result'))}</b>: ${parts.join('<br>')}<br><br>${esc(t('tl_next'))}`;
      m.querySelector('#tlRun').disabled = false;
      renderFilters(); refreshFacets(); reloadList(true);
      if (S.cur) { renderSpeciesPanel(); renderFieldsPanel(); }
    } catch (e) { m.querySelector('#tlRun').disabled = false; toast(e.message, 'error'); }
  };
}
function spByCodeLabel(label) {   // CSV 表記・コード・英名・和名・学名で種を引く（画面側）
  const s = (label || '').trim().toLowerCase();
  const all = (S.species.species || []).concat(S.species.fixed || []);
  const hit = all.find(c => [c.csv_label, c.code, c.name_en, c.name_ja, c.name_sci].some(x => (x || '').toLowerCase() === s));
  return hit ? hit.code : null;
}

// ---------- 統合 ----------
async function mergeDialog() {
  const conflicts = (await api('/api/merge/conflicts')).conflicts;
  const jstr = st => (st.judgements || []).map(j => spLabel(j.species)).join(', ') + (st.note ? ' · ' + st.note : '') + (st.verified_by ? ` (${st.verified_by})` : '');
  const m = showModal(`<h3>${esc(t('mg_title'))}</h3><div class="note">${esc(t('mg_help'))}</div>
    <div class="btn-row"><button type="button" id="mgExport">${esc(t('mg_export'))}</button><button type="button" id="mgImport">${esc(t('mg_import'))}</button></div>
    <div id="mgResult" class="note"></div>
    <h4>${esc(t('mg_conflict_list'))} (${conflicts.length})</h4>
    <div id="mgConf" style="max-height:50vh;overflow:auto">${conflicts.length ? conflicts.map(c => `<div class="conflict" data-id="${c.item.id}"><div><b>${esc(c.item.rel_path)}</b> · ${esc(c.item.datetime || '')}</div>
      <div class="two"><div><b>${esc(t('mg_mine'))}</b>: ${esc(jstr(c.mine))}</div><div><b>${esc(t('mg_theirs'))}</b> (${esc(c.by || '')}): ${esc(jstr(c.theirs))}</div></div>
      <div class="btn-row"><button type="button" class="small keep">${esc(t('mg_keep_mine'))}</button><button type="button" class="small take">${esc(t('mg_take_theirs'))}</button><button type="button" class="small open">▶</button></div></div>`).join('') : `<div class="note">${esc(t('mg_none'))}</div>`}</div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#mgExport').onclick = async () => { const p = await pickPath('savejson', `${S.project.dir}/exports`, `judgements_${S.settings.user_name || 'user'}.json`); if (!p) return; const r = await api('/api/merge/export', { path: p }); m.querySelector('#mgResult').textContent = `${r.path} (${r.n})`; };
  m.querySelector('#mgImport').onclick = async () => {
    const p = await pickPath('json'); if (!p) return;
    try {
      const r = await api('/api/merge/import', { path: p });
      m.querySelector('#mgResult').innerHTML = `<b>${esc(t('mg_result'))}</b> (${esc(r.by || '')}, ${esc((r.exported_at || '').slice(0, 16))}): ${t('mg_applied')} ${r.applied} · ${t('mg_kept')} ${r.kept} · ${t('mg_same')} ${r.same} · ${t('mg_conflicts')} ${r.conflicts} · ${t('mg_missing')} ${r.missing} / ${r.total}`;
      refreshState(); reloadList(true);
      if (r.conflicts) setTimeout(mergeDialog, 1500);
    } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelectorAll('.conflict').forEach(div => {
    const id = parseInt(div.dataset.id);
    const done = async (choice) => { await api('/api/merge/resolve', { item_id: id, choice }); div.remove(); refreshState(); reloadList(true); };
    div.querySelector('.keep').onclick = () => done('mine');
    div.querySelector('.take').onclick = () => done('theirs');
    div.querySelector('.open').onclick = () => { closeModal(); openItem(id); };
  });
}

// ---------- 保存用の変換 ----------
let archiveModalEl = null;
function gb(b) { return b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB'; }
async function archiveDialog() {
  const ps = (S.project && S.project.settings) || {};
  const m = showModal(`<h3>${esc(t('ar_title'))}</h3><div class="note">${esc(t('ar_help'))}</div>
    <div class="form-row"><label>${esc(t('ar_range'))}</label><select id="arRange"><option value="all">${esc(t('ex_all'))}</option><option value="filter">${esc(t('ex_filter'))}</option></select></div>
    <div id="arPlan" class="note">${esc(t('loading'))}</div>
    <div class="form-row"><label>${esc(t('ar_codec'))}</label><select id="arCodec"><option value="auto">${esc(t('ar_codec_auto'))}</option><option value="nvenc">${esc(t('ar_codec_nvenc'))}</option><option value="cpu">${esc(t('ar_codec_cpu'))}</option></select></div>
    <div class="form-row"><label>${esc(t('ar_crf'))}</label><input type="number" id="arCrf" min="14" max="28" value="${esc(ps.archive_crf || 19)}"></div>
    <div class="form-row"><label>${esc(t('ar_original'))}</label><select id="arOrig"><option value="keep">${esc(t('ar_keep'))}</option><option value="move">${esc(t('ar_move'))}</option><option value="delete">${esc(t('ar_delete'))}</option></select></div>
    ${pathRow('arMoveTo', t('ar_move_to'), 'folder', ps.archive_move_to || '', '')}
    <div id="arStatus"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="arStop" class="danger-outline hidden">${esc(t('ar_stop'))}</button><button id="mOk" class="primary">${esc(t('ar_start'))}</button></div>`);
  wirePicks(m);
  archiveModalEl = m;
  m.querySelector('#arCodec').value = ps.archive_codec || 'auto';
  m.querySelector('#arOrig').value = ps.archive_original || 'keep';
  const plan = async () => {
    const filters = m.querySelector('#arRange').value === 'filter' ? Object.assign({}, S.filter) : {};
    try {
      const r = await api('/api/archive/plan', { filters });
      m.querySelector('#arPlan').innerHTML = `${esc(t('ar_targets'))}: <b>${r.n}</b> ${esc(t('ar_targets_n'))} ${gb(r.bytes)}${r.containers.length ? ' · ' + esc(r.containers.join(', ')) : ''}` +
        (r.pending_meta ? `<br>${esc(t('ar_pending_meta'))}: ${r.pending_meta}` : '') +
        `<br>${esc(t('ar_done'))}: ${r.done.n} (${gb(r.done.orig_bytes)} → ${gb(r.done.new_bytes)})` +
        `<br>${esc(t('ar_nvenc'))}: ${r.nvenc ? esc(t('ar_available')) : esc(t('ar_unavailable'))} · ffmpeg: ${esc(r.ffmpeg || '—')}`;
    } catch (e) { m.querySelector('#arPlan').textContent = e.message; }
  };
  m.querySelector('#arRange').addEventListener('change', plan);
  plan();
  m.querySelector('#mCancel').onclick = () => { archiveModalEl = null; closeModal(); };
  m.querySelector('#arStop').onclick = async () => { await api('/api/archive/stop', {}); };
  m.querySelector('#mOk').onclick = async () => {
    const original = m.querySelector('#arOrig').value;
    if (original === 'delete' && !confirm(t('ar_confirm_delete'))) return;
    const filters = m.querySelector('#arRange').value === 'filter' ? Object.assign({}, S.filter) : null;
    try {
      const j = await api('/api/archive/start', { filters: filters || undefined, codec: m.querySelector('#arCodec').value, crf: parseInt(m.querySelector('#arCrf').value), original, move_to: m.querySelector('#arMoveTo').value });
      archiveOnJobs(j);
    } catch (e) { toast(e.message, 'error'); }
  };
  archiveOnJobs(S.snap && S.snap.jobs);
}
function archiveOnJobs(jobs) {
  const m = archiveModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.archive) return;
  const cur = jobs.archive.current, rec = (jobs.archive.recent || [])[0];
  const st = m.querySelector('#arStatus');
  if (cur) {
    const pct = cur.total ? Math.round(100 * (cur.done + cur.failed) / cur.total) : 0;
    st.innerHTML = `<div><b>${esc(t('ar_running'))}</b>: ${cur.done + cur.failed} / ${cur.total} (${pct}%)${cur.remain != null ? ' · ' + Math.round(cur.remain / 60) + ' min' : ''}${cur.encoder ? ' · ' + esc(cur.encoder) : ''}</div><div class="prog"><div style="width:${pct}%"></div></div><div class="info">${esc(cur.current || '')}</div><div class="info">${esc(cur.message || '')}</div>`;
    m.querySelector('#arStop').classList.remove('hidden'); m.querySelector('#mOk').disabled = true;
  } else {
    st.innerHTML = rec ? `<div><b>${esc(t('ar_result'))}</b> (${esc(t('jb_state_' + rec.state))}): ${esc(rec.message || rec.error || '')}</div>` : '';
    m.querySelector('#arStop').classList.add('hidden'); m.querySelector('#mOk').disabled = false;
  }
}

// ---------- 自動判定（MegaDetector ＋ SpeciesNet） ----------
let detectModalEl = null;
async function detectDialog(preset) {
  // preset = {mode: 'reclass', classifier: 'local:<名前>'}（追加学習の窓から「付け直す」で開いたとき）
  const m = showModal(`<h3>${esc(t('dt_title'))}</h3><div class="note">${esc(t('dt_help'))}</div>
    <div id="dtEnv" class="env-row">${esc(t('loading'))}</div>
    <div class="form-row"><label>${esc(t('dt_mode'))}</label><select id="dtMode"><option value="detect">${esc(t('dt_mode_detect'))}</option><option value="reclass">${esc(t('dt_mode_reclass'))}</option></select></div>
    <div id="dtModeHelp" class="note hidden">${esc(t('dt_mode_reclass_help'))}</div>
    <div class="form-row"><label>${esc(t('dt_range'))}</label><select id="dtRange"><option value="all">${esc(t('ex_all'))}</option><option value="filter">${esc(t('ex_filter'))}</option></select></div>
    <div id="dtPlan" class="dt-counts"></div>
    <div class="form-row dt-det"><label>${esc(t('dt_model'))}</label><select id="dtModel"></select></div>
    <div class="form-row"><label>${esc(t('dt_classifier'))}</label><select id="dtCls"><option value="speciesnet">SpeciesNet</option><option value="none">${esc(t('dt_cls_none'))}</option></select></div>
    <div class="form-row"><label>${esc(t('dt_device'))}</label><select id="dtDevice"><option value="auto">${esc(t('dt_dev_auto'))}</option><option value="cuda">GPU (CUDA)</option><option value="mps">GPU (Apple)</option><option value="cpu">CPU</option></select></div>
    <div class="form-row dt-det"><label>${esc(t('dt_redo'))}</label><span style="display:flex;flex-direction:column;gap:2px"><label class="chk"><input type="checkbox" id="dtRedo"> ${esc(t('dt_redo_l'))}</label><label class="chk"><input type="checkbox" id="dtVerified"> ${esc(t('dt_verified_l'))}</label><label class="chk"><input type="checkbox" id="dtExisting"> ${esc(t('dt_existing_l'))}</label><span class="info">${esc(t('dt_redo_help'))}</span></span></div>
    <details class="adv dt-det"><summary>${esc(t('dt_adv'))}</summary>
      <div class="form-row"><label>${esc(t('dt_frames'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="dtFirstSec" step="0.5" min="0" style="width:70px"> s × <input type="number" id="dtFirstFps" step="1" min="1" style="width:60px"> fps, ${esc(t('dt_then'))} <input type="number" id="dtInterval" step="0.1" min="0.1" style="width:90px"> s <span class="info">${esc(t('dt_interval_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('dt_animal_rule'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${esc(t('dt_conf'))} ≥ <input type="number" id="dtAth" step="0.05" min="0.05" max="0.95" style="width:70px"> × <input type="number" id="dtAmin" step="1" min="1" style="width:60px"> ${esc(t('dt_frames_u'))}</span></div>
      <div class="form-row"><label>${esc(t('dt_person_rule'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${esc(t('dt_conf'))} ≥ <input type="number" id="dtPth" step="0.05" min="0.05" max="0.95" style="width:70px"> × <input type="number" id="dtPmin" step="1" min="1" style="width:60px"> ${esc(t('dt_frames_u'))}</span></div>
      <div class="form-row"><label>${esc(t('dt_species_rule'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">p1 <input type="number" id="dtP1" step="0.05" min="0.1" max="0.99" style="width:70px"> p2 <input type="number" id="dtP2" step="0.05" min="0.1" max="0.99" style="width:70px"> K <input type="number" id="dtK" step="1" min="1" style="width:60px"> ${esc(t('dt_crops'))} <input type="number" id="dtCrops" step="1" min="1" max="64" style="width:60px"> <span title="${esc(t('dt_crop_spread_tip'))}">${esc(t('dt_crop_gap'))} <input type="number" id="dtCropGap" step="0.1" min="0" max="10" style="width:60px"> s · ${esc(t('dt_crop_per_frame'))} <input type="number" id="dtCropPer" step="1" min="0" max="12" style="width:50px"></span></span></div>
      <div class="form-row"><label>${esc(t('dt_country'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="dtCountry" maxlength="3" style="width:70px;text-transform:uppercase" spellcheck="false"> <span class="info">${esc(t('dt_country_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('dt_batch'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="dtBatch" step="1" min="1" max="64" style="width:70px"> ${esc(t('dt_imgsize'))} <input type="number" id="dtImgSize" step="32" min="0" max="2560" style="width:80px"> <label class="chk"><input type="checkbox" id="dtFeat"> ${esc(t('dt_features'))}</label></span></div>
      <div class="note">${esc(t('dt_adv_help'))}</div>
    </details>
    <div id="dtStatus"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="dtStop" class="danger-outline hidden">${esc(t('ar_stop'))}</button><button id="mOk" class="primary">${esc(t('dt_start'))}</button></div>`);
  detectModalEl = m;
  const q = sel => m.querySelector(sel);
  let planData = null;
  const isReclass = () => q('#dtMode').value === 'reclass';
  const modeUi = () => {
    const rc = isReclass();
    m.querySelectorAll('.dt-det').forEach(el => el.classList.toggle('hidden', rc));
    q('#dtModeHelp').classList.toggle('hidden', !rc);
    const none = q('#dtCls').querySelector('option[value="none"]');
    if (none) { none.disabled = rc; if (rc && q('#dtCls').value === 'none') q('#dtCls').value = 'speciesnet'; }
    q('#mOk').textContent = rc ? t('tr_apply') : t('dt_start');
  };
  const fill = ps => {
    const locals = (planData && planData.local_models) || [];
    const want = preset && preset.classifier ? preset.classifier : ps.classifier;
    fillSelect(q('#dtCls'), [['speciesnet', 'SpeciesNet']].concat(locals.map(x => ['local:' + x.name, `SpeciesNet + ${x.name} (${x.kind})${ps.classifier === 'local:' + x.name ? ' (' + t('tr_in_use') + ')' : ''}`]), [['none', t('dt_cls_none')]]), want === 'none' ? 'none' : (want && want.startsWith('local:') && locals.some(x => 'local:' + x.name === want) ? want : 'speciesnet'));
    q('#dtFirstSec').value = ps.frame_first_sec; q('#dtFirstFps').value = ps.frame_first_fps; q('#dtInterval').value = (ps.frame_interval_sec === 'auto' || !ps.frame_interval_sec) ? '' : ps.frame_interval_sec; q('#dtInterval').placeholder = t('dt_interval_auto');
    q('#dtAth').value = ps.animal_threshold; q('#dtAmin').value = ps.animal_min_frames; q('#dtPth').value = ps.person_threshold; q('#dtPmin').value = ps.person_min_frames;
    q('#dtP1').value = ps.species_p1; q('#dtP2').value = ps.species_p2; q('#dtK').value = ps.species_k; q('#dtCrops').value = ps.max_crops; q('#dtCropGap').value = ps.crop_min_gap_sec == null ? 0.6 : ps.crop_min_gap_sec; q('#dtCropPer').value = ps.crop_per_frame == null ? 2 : ps.crop_per_frame;
    q('#dtImgSize').value = ps.detect_image_size || 0; q('#dtFeat').checked = ps.save_features !== false;
    q('#dtCountry').value = ps.country == null ? 'JPN' : ps.country;
  };
  const plan = async () => {
    const filters = q('#dtRange').value === 'filter' ? Object.assign({}, S.filter) : {};
    try {
      const r = await api('/api/detect/plan', { filters });
      planData = r;
      const env = r.env, chip = (ok, label) => `<span class="${ok ? 'ok' : 'ng'}">${esc(label)} ${ok ? '✓' : '✗'}</span>`;
      let envHtml = env.fake ? `<span class="ok">${esc(t('dt_fake'))}</span>` : chip(env.torch, 'torch' + (env.torch_info && env.torch_info.version ? ' ' + env.torch_info.version : '')) + chip(env.megadetector, 'megadetector') + chip(env.speciesnet, 'speciesnet') + (env.dill ? '' : chip(false, 'dill'));
      const ti = env.torch_info;
      if (ti && !ti.error) envHtml += ti.cuda ? `<span class="ok">CUDA ✓ ${esc(ti.gpu || '')}</span>` : (ti.mps ? `<span class="ok">Apple MPS ✓</span>` : `<span class="ng">CUDA ✗</span>`);
      const ld = env.loaded || {};
      if (ld.device_name) envHtml += `<span class="ok">${esc(ld.device_name)}</span>`;
      if (ti && !ti.error && !ti.cuda && !ti.mps) envHtml += `<div class="note">${esc(t('dt_no_cuda'))}</div>`;
      if (!env.fake && env.torch && !env.dill) envHtml += `<div class="note">${esc(t('dt_no_dill'))}</div>`;
      if (ld.detector) envHtml += `<span>${esc(ld.detector)}</span>`;
      if (ld.classifier) envHtml += `<span class="clickable" id="dtLabels" title="${esc(t('dt_labels_tip'))}">${esc(t('dt_labels'))}</span>`;
      if (r.n_done) envHtml += `<span class="clickable" id="dtEval" title="${esc(t('ag_tip'))}">${esc(t('ag_title'))}</span>`;
      if (ld.loading) envHtml += `<span>${esc(ld.loading)}</span>`;
      if (!env.ok) envHtml += `<div class="note">${esc(t('dt_env_missing'))}</div>`;
      q('#dtEnv').innerHTML = envHtml;
      q('#dtPlan').innerHTML = `<span>${esc(t('dt_n_all'))} <b>${r.n_all}</b></span><span>${esc(t('dt_n_todo'))} <b>${r.n_todo}</b>${r.n_todo !== r.n_todo_unverified ? ` (${esc(t('dt_unverified'))} ${r.n_todo_unverified})` : ''}</span><span>${esc(t('dt_n_done'))} <b>${r.n_done}</b></span>` +
        (r.n_low ? `<span>${esc(t('det_low'))} <b>${r.n_low}</b></span>` : '') + (r.n_failed ? `<span>${esc(t('det_failed'))} <b>${r.n_failed}</b></span>` : '') +
        (r.pending_meta ? `<span class="flag-low">${esc(t('ar_pending_meta'))}: ${r.pending_meta}</span>` : '');
      if (!q('#dtModel').options.length) {
        const files = env.md_files || {};
        fillSelect(q('#dtModel'), (env.md_choices || []).map(([k, label]) => [k, `${k === 'auto' ? '' : k + ' — '}${label}${k !== 'auto' && files[k] === false ? ' (' + t('dt_download') + ')' : ''}`]), r.settings.md_model || 'auto');
        q('#dtDevice').value = r.device || 'auto';
        q('#dtBatch').value = r.batch_size || 8;
        fill(r.settings);
        if (preset && preset.mode) q('#dtMode').value = preset.mode;
        modeUi();
      }
      q('#mOk').disabled = !env.ok;
      const lb = q('#dtLabels');
      if (lb) lb.onclick = labelsDialog;
      const ev = q('#dtEval');
      if (ev) ev.onclick = () => evalDialog(q('#dtRange').value === 'filter' ? Object.assign({}, S.filter) : {}, () => detectDialog());
    } catch (e) { q('#dtPlan').textContent = e.message; }
  };
  q('#dtRange').addEventListener('change', plan);
  q('#dtMode').addEventListener('change', modeUi);
  plan();
  q('#mCancel').onclick = () => { detectModalEl = null; closeModal(); };
  q('#dtStop').onclick = async () => { await api('/api/detect/stop', {}); };
  q('#mOk').onclick = async () => {
    const filters = q('#dtRange').value === 'filter' ? Object.assign({}, S.filter) : null;
    if (isReclass()) {                                   // 判定の付け直し: 保存してある特徴量・枠の画像から（動画は読まない）
      const want = q('#dtCls').value === 'none' ? 'speciesnet' : q('#dtCls').value;
      const dev = q('#dtDevice').value;
      detectModalEl = null;
      if (!await confirmModal(t('dt_title'), `<div>${esc(t('tr_apply_q'))}</div><div class="note">${esc(want === 'speciesnet' ? 'SpeciesNet' : want.slice(6))}</div>`, t('tr_apply'))) { detectDialog({ mode: 'reclass', classifier: want }); return; }
      await detectDialog({ mode: 'reclass', classifier: want });
      try {
        const j = await api('/api/train/use', { classifier: want, reclassify: true, filters: filters || undefined, device: dev });
        S._bgWasRunning = true;
        if (S.project) S.project.settings = Object.assign(S.project.settings || {}, { classifier: want });
        const mm = detectModalEl; if (mm) mm._wasRunning = true;
        detectOnJobs(j);
      } catch (e) { toast(e.message, 'error'); }
      return;
    }
    const redo = q('#dtRedo').checked;
    const body = {                                     // 確認の窓で中身が入れ替わる前に読んでおく
      filters: filters || undefined, redo, include_verified: q('#dtVerified').checked, use_existing: q('#dtExisting').checked,
      md_model: q('#dtModel').value, classifier: q('#dtCls').value, device: q('#dtDevice').value, batch_size: parseInt(q('#dtBatch').value) || 8,
      frame_first_sec: parseFloat(q('#dtFirstSec').value), frame_first_fps: parseFloat(q('#dtFirstFps').value), frame_interval_sec: parseFloat(q('#dtInterval').value) || 'auto',
      animal_threshold: parseFloat(q('#dtAth').value), animal_min_frames: parseInt(q('#dtAmin').value), person_threshold: parseFloat(q('#dtPth').value), person_min_frames: parseInt(q('#dtPmin').value),
      species_p1: parseFloat(q('#dtP1').value), species_p2: parseFloat(q('#dtP2').value), species_k: parseInt(q('#dtK').value), max_crops: parseInt(q('#dtCrops').value), crop_min_gap_sec: parseFloat(q('#dtCropGap').value) || 0, crop_per_frame: parseInt(q('#dtCropPer').value) || 0,
      detect_image_size: parseInt(q('#dtImgSize').value) || 0, save_features: q('#dtFeat').checked, country: q('#dtCountry').value.trim().toUpperCase(),
    };
    if (redo && !body.use_existing) {
      detectModalEl = null;
      if (!await confirmModal(t('dt_title'), esc(t('dt_redo_confirm')), t('dt_start'))) { detectDialog(); return; }
    }
    try {
      const j = await api('/api/detect/start', body);
      S._bgWasRunning = true;                              // 終わったら一覧と件数を読み直す
      if (S.project) S.project.settings = Object.assign(S.project.settings || {}, body);
      if (redo && !body.use_existing) await detectDialog();   // 確認の窓で入れ替わったので開き直す
      const mm = detectModalEl || m;
      mm._wasRunning = true;
      detectOnJobs(j);
    } catch (e) { toast(e.message, 'error'); }
  };
  detectOnJobs(S.snap && S.snap.jobs);
}
async function labelsDialog() {
  let r;
  try { r = await api('/api/detect/labels'); } catch (e) { toast(e.message, 'error'); return; }
  const codes = Object.keys(r.map).sort((a, b) => r.map[b].n - r.map[a].n);
  const rows = codes.map(c => `<tr><td><b>${esc(spLabel(c))}</b> <span class="info">${esc(c)}</span></td><td>${r.map[c].n}</td><td class="info">${esc(r.map[c].names.join(', '))}${r.map[c].n > r.map[c].names.length ? ' …' : ''}</td></tr>`).join('');
  const m = showModal(`<h3>${esc(t('dt_labels'))}</h3><div class="note">${esc(t('dt_labels_help'))} (${esc(r.model)}, ${r.n})</div>
    <div style="max-height:60vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('species'))}</th><th>n</th><th>SpeciesNet</th></tr>${rows}</table></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = () => detectDialog();
}
async function evalDialog(filters, back, mode) {
  // 一致率: 比べる相手 = AI の判定（検出済みのもの）/ 既存の種名（CSV・Timelapse・以前の判定と、確定した種名）
  const st = { mode: mode || 'ai', useFilter: !!(filters && Object.keys(filters).length), by: '' };
  const m = showModal(`<h3>${esc(t('ag_title'))}</h3>
    <div class="form-row"><label>${esc(t('ag_against'))}</label><span style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><select id="agMode" style="width:auto"><option value="ai">${esc(t('ag_against_ai'))}</option><option value="prior">${esc(t('ag_against_prior'))}</option></select>
      <label class="inline"><input type="checkbox" id="agFilter" ${st.useFilter ? 'checked' : ''}> ${esc(t('ag_use_filter'))}</label>
      <span id="agByWrap" class="hidden"><label class="inline">${esc(t('ag_by'))} <select id="agBy" style="width:auto"><option value="">${esc(t('ag_by_all'))}</option></select></label></span></span></div>
    <div id="agBody"></div>
    <div id="agResult" class="note"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="agExport" class="primary">${esc(t('ag_export'))}</button></div>`);
  const q = sel => m.querySelector(sel);
  q('#agMode').value = st.mode;
  const curFilters = () => (q('#agFilter').checked ? Object.assign({}, S.filter) : {});
  const render = async () => {
    st.mode = q('#agMode').value;
    q('#agByWrap').classList.toggle('hidden', st.mode !== 'prior');
    const body = q('#agBody');
    body.innerHTML = `<div class="note">${esc(t('loading'))}</div>`;
    let r;
    try { r = st.mode === 'prior' ? await api('/api/judge/eval_prior', { filters: curFilters(), by: st.by }) : await api('/api/detect/eval', { filters: curFilters() }); } catch (e) { body.innerHTML = `<div class="ng">${esc(e.message)}</div>`; return; }
    body.innerHTML = st.mode === 'prior' ? evalPriorHtml(r) : evalAiHtml(r);
    if (st.mode === 'prior') {
      const sel = q('#agBy'); const cur = st.by;
      sel.innerHTML = `<option value="">${esc(t('ag_by_all'))}</option>` + (r.people || []).map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
      sel.value = (r.people || []).includes(cur) ? cur : '';
    }
  };
  q('#agMode').onchange = render; q('#agFilter').onchange = render; q('#agBy').onchange = () => { st.by = q('#agBy').value; render(); };
  q('#mCancel').onclick = () => (back ? back() : closeModal());
  q('#agExport').onclick = async () => {
    try {
      const x = st.mode === 'prior' ? await api('/api/judge/eval_prior/csv', { filters: curFilters(), by: st.by }) : await api('/api/detect/eval/csv', { filters: curFilters() });
      q('#agResult').textContent = `${t('ag_exported')}: ${x.path} / ${x.matrix_path}`;
    } catch (e) { toast(e.message, 'error'); }
  };
  await render();
}
function evalAiHtml(r) {
  const pct = (a, b) => b ? (100 * a / b).toFixed(0) + '%' : '—';
  const nm = c => c === '(low)' ? t('det_low') : spLabel(c);
  const spRows = sortCodes(Object.keys(r.by_species));
  const rows = spRows.map(c => { const d = r.by_species[c]; return `<tr><td><b>${esc(spLabel(c))}</b></td><td>${d.n}</td><td>${d.correct} <span class="info">(${pct(d.correct, d.n - d.low)})</span></td><td>${d.low}</td><td class="info">${esc(Object.entries(d.conf).map(([k, v]) => `${nm(k)} ${v}`).join(' · '))}</td></tr>`; }).join('');
  const cols = sortCodes(new Set([].concat(r.ai_cols || [], Object.keys(r.matrix || {}))));
  const mRows = sortCodes(Object.keys(r.matrix || {}));
  const matrix = cols.length ? `<h4>${esc(t('ag_matrix'))}</h4><div style="max-height:40vh;overflow:auto"><table class="tbl matrix"><tr><th>${esc(t('cmp_human'))} \\ AI</th>${cols.map(c => `<th>${esc(nm(c))}</th>`).join('')}<th>n</th></tr>
    ${mRows.map(h => { const row = r.matrix[h]; return `<tr><td><b>${esc(spLabel(h))}</b></td>${cols.map(c => `<td class="${c === h ? 'diag' : (row[c] ? 'off' : '')}">${row[c] || ''}</td>`).join('')}<td>${Object.values(row).reduce((a, b) => a + b, 0)}</td></tr>`; }).join('')}</table></div>` : '';
  const overall = r.n_model ? (100 * r.correct / r.n_model).toFixed(1) : '—';
  return `<div class="note">${esc(t('ag_help'))}</div>
    <div class="ag-big"><span class="ag-num">${overall}%</span><span class="info">${esc(t('ag_overall'))} · ${r.correct} / ${r.n_model}</span>
      <span class="info">${esc(t('dt_eval_n'))} ${r.n} · ${esc(t('det_low'))} ${r.n_low} (${pct(r.n_low, r.n)})</span></div>
    <div style="max-height:34vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('dt_eval_human'))}</th><th>${esc(t('ag_n'))}</th><th>${esc(t('ag_rate'))}</th><th>${esc(t('det_low'))}</th><th>${esc(t('dt_eval_model'))}</th></tr>${rows || `<tr><td colspan="5" class="note">${esc(t('dt_eval_none'))}</td></tr>`}</table></div>
    ${matrix}
    ${r.n_folded ? `<div class="note">${esc(t('ag_folded'))} (${r.n_folded})</div>` : ''}`;
}
function evalPriorHtml(r) {
  const pct = (a, b) => b ? (100 * a / b).toFixed(0) + '%' : '—';
  const nm = c => c === '(none)' ? t('ag_prior_none_lbl') : spLabel(c);
  const spRows = sortCodes(Object.keys(r.by_species));
  const rows = spRows.map(c => { const d = r.by_species[c]; return `<tr><td><b>${esc(nm(c))}</b></td><td>${d.n}</td><td>${d.correct} <span class="info">(${pct(d.correct, d.n)})</span></td><td>${d.exact} <span class="info">(${pct(d.exact, d.n)})</span></td><td class="info">${esc(Object.entries(d.conf).map(([k, v]) => `${nm(k)} ${v}`).join(' · '))}</td></tr>`; }).join('');
  const cols = sortCodes(new Set([].concat(r.cols || [], Object.keys(r.matrix || {}))));
  const mRows = sortCodes(Object.keys(r.matrix || {}));
  const matrix = cols.length ? `<h4>${esc(t('ag_prior_matrix'))}</h4><div style="max-height:40vh;overflow:auto"><table class="tbl matrix"><tr><th>${esc(t('ag_prior'))} \\ ${esc(t('ag_verified'))}</th>${cols.map(c => `<th>${esc(nm(c))}</th>`).join('')}<th>n</th></tr>
    ${mRows.map(h => { const row = r.matrix[h]; return `<tr><td><b>${esc(nm(h))}</b></td>${cols.map(c => `<td class="${c === h ? 'diag' : (row[c] ? 'off' : '')}">${row[c] || ''}</td>`).join('')}<td>${Object.values(row).reduce((a, b) => a + b, 0)}</td></tr>`; }).join('')}</table></div>` : '';
  const overall = r.n ? (100 * r.correct / r.n).toFixed(1) : '—';
  return `<div class="note">${esc(t('ag_prior_help'))}</div>
    <div class="ag-big"><span class="ag-num">${overall}%</span><span class="info">${esc(t('ag_overall'))} · ${r.correct} / ${r.n} · ${esc(t('ag_exact'))} ${r.exact} (${pct(r.exact, r.n)})</span>
      <span class="info">${esc(t('ag_n_verified'))} ${r.n_verified} · ${esc(t('ag_prior_none'))} ${r.n_none} · ${esc(t('ag_unchecked'))} ${r.n_unchecked}</span></div>
    <div style="max-height:34vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('ag_prior'))}</th><th>${esc(t('ag_n'))}</th><th>${esc(t('ag_rate'))}</th><th>${esc(t('ag_exact'))}</th><th>${esc(t('ag_verified'))}</th></tr>${rows || `<tr><td colspan="5" class="note">${esc(t('ag_prior_empty'))}</td></tr>`}</table></div>
    ${matrix}`;
}
async function bulkDialog() {
  let n = 0;
  try { n = (await api('/api/judge/bulk_verify', { filters: S.filter, count_only: true })).n; } catch (e) { toast(e.message, 'error'); return; }
  const m = showModal(`<h3>${esc(t('bulk_title'))}</h3><div class="note">${esc(t('bulk_help'))}</div>
    <div class="dt-counts"><span>${esc(t('bulk_n'))} <b>${n}</b></span><span class="info">${esc(activeFilterNames().join(' · ') || t('all_status'))}</span></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary" ${n ? '' : 'disabled'}>${esc(t('ok'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#mOk').onclick = async () => {
    try { const r = await api('/api/judge/bulk_verify', { filters: S.filter }); closeModal(); toast(`${r.n} ${t('bulk_done')}`); await refreshFacets(); await reloadList(true); if (S.cur) refreshCurrent(); }
    catch (e) { toast(e.message, 'error'); }
  };
}
function detectOnJobs(jobs) {
  const m = detectModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.ml) return;
  const cur = jobs.ml.current, rec = (jobs.ml.recent || [])[0];
  const st = m.querySelector('#dtStatus');
  if (cur) {
    const pct = cur.total ? Math.round(100 * (cur.done + cur.failed) / cur.total) : 0;
    const c = cur.counts || {};
    const counts = cur.done ? `${t('det_animal')} ${c.animal} · ${t('det_person')} ${c.person} · ${t('det_vehicle')} ${c.vehicle} · ${t('det_none')} ${c.empty} · ${t('det_low')} ${c.low}` : '';
    st.innerHTML = `<div><b>${esc(cur.kind === 'reclassify' ? t('tr_reclassifying') : (cur.phase === 'load' ? t('dt_loading') : t('ar_running')))}</b>: ${cur.done + cur.failed} / ${cur.total} (${pct}%)${cur.remain != null && cur.phase === 'run' ? ' · ' + Math.round(cur.remain / 60) + ' min' : ''}</div><div class="prog"><div style="width:${pct}%"></div></div><div class="info">${esc(cur.current || '')}</div><div class="info">${esc(counts)} ${esc(cur.message || '')}</div>`;
    m.querySelector('#dtStop').classList.remove('hidden'); m.querySelector('#mOk').disabled = true;
    m._wasRunning = true;
  } else if (jobs.ml.queued && jobs.ml.queued.length) {
    st.innerHTML = `<div><b>${esc(t('jb_queued'))}</b></div>`;
    m.querySelector('#mOk').disabled = true;
  } else {
    st.innerHTML = rec ? `<div><b>${esc(t('ar_result'))}</b> (${esc(t('jb_state_' + rec.state))}): ${esc(rec.message || rec.error || '')}</div>` : '';
    m.querySelector('#dtStop').classList.add('hidden'); m.querySelector('#mOk').disabled = false;
    if (m._wasRunning) { m._wasRunning = false; m.querySelector('#dtRange').dispatchEvent(new Event('change')); }
  }
}

// ---------- 時刻の修正 ----------
function dtToInput(s) { if (!s) return ''; const m = s.match(/^(\d{4})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2}):(\d{2})$/); return m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}` : ''; }
function dtParse(s) { const m = (s || '').match(/^(\d{4})[-/](\d{2})[-/](\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0)) / 1000 : null; }
async function timeDialog() {
  const d = S.cur;
  const orig = d.datetime || '';
  const m = showModal(`<h3>${esc(t('tm_title'))}</h3><div class="note">${esc(t('tm_help'))}</div>
    <div class="form-row"><label>${esc(t('tm_scope'))}</label><select id="tmScope">
      <option value="this">${esc(t('tm_this'))}</option>
      <option value="folder" selected>${esc(t('tm_folder'))} (${esc(d.folder || '/')})</option>
      ${d.station ? `<option value="station">${esc(t('tm_station'))} (${esc(d.station)})</option>` : ''}
      <option value="filter">${esc(t('tm_filter'))}</option></select><span id="tmN" class="info"></span></div>
    <div class="form-row"><label>${esc(t('tm_orig'))}</label><input type="text" value="${esc(orig)}" readonly></div>
    <div class="form-row"><label>${esc(t('tm_correct'))}</label><input type="datetime-local" id="tmCorrect" step="1" value="${esc(dtToInput(d.datetime_corrected || orig))}"></div>
    <div class="form-row"><label>${esc(t('tm_offset'))}</label><span style="display:flex;gap:4px;align-items:center"><input type="number" id="tmOff" step="1" value="${esc(d.time_offset_sec != null ? d.time_offset_sec : 0)}" style="width:120px"> s <span id="tmOffFmt" class="info"></span></span></div>
    <div class="form-row"><label>${esc(t('tm_note'))}</label><input type="text" id="tmNote" value="${esc(d.time_note || '')}"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="tmClear" class="danger-outline">${esc(t('tm_clear'))}</button><button id="mOk" class="primary">${esc(t('tm_apply'))}</button></div>`);
  const scopeFilters = () => {
    const v = m.querySelector('#tmScope').value;
    if (v === 'this') return { ids: [d.id] };
    if (v === 'folder') return { filters: { folder: d.folder || '' } };
    if (v === 'station') return { filters: { station: d.station } };
    return { filters: Object.assign({}, S.filter) };
  };
  const count = async () => {
    const sc = scopeFilters();
    if (sc.ids) { m.querySelector('#tmN').textContent = `${t('tm_n')}: 1`; return; }
    try { const r = await api(`/api/items/count?${qs(Object.assign({}, sc.filters, { include_missing: 1 }))}`); m.querySelector('#tmN').textContent = `${t('tm_n')}: ${r.total}`; } catch (e) { /* */ }
  };
  const base = dtParse(orig);
  const syncFromCorrect = () => { const c = dtParse(m.querySelector('#tmCorrect').value); if (base != null && c != null) { m.querySelector('#tmOff').value = Math.round(c - base); } showOff(); };
  const syncFromOff = () => { const off = parseInt(m.querySelector('#tmOff').value || '0'); if (base != null) { const dt = new Date((base + off) * 1000); m.querySelector('#tmCorrect').value = dt.toISOString().slice(0, 19); } showOff(); };
  const showOff = () => { m.querySelector('#tmOffFmt').textContent = fmtOffset(parseInt(m.querySelector('#tmOff').value || '0')); };
  m.querySelector('#tmCorrect').addEventListener('change', syncFromCorrect);
  m.querySelector('#tmOff').addEventListener('input', syncFromOff);
  m.querySelector('#tmScope').addEventListener('change', count);
  showOff(); count();
  m.querySelector('#mCancel').onclick = closeModal;
  const done = async (clear) => {
    const sc = scopeFilters();
    const off = parseInt(m.querySelector('#tmOff').value || '0');
    try {
      const r = await api('/api/time/apply', Object.assign({ offset_sec: off, clear, note: m.querySelector('#tmNote').value }, sc));
      closeModal(); toast(`${r.n} ${t(clear ? 'tm_cleared' : 'tm_applied')}`);
      await refreshFacets(); await reloadList(true);
      openItem(d.id, S.curIndex);
    } catch (e) { toast(e.message, 'error'); }
  };
  m.querySelector('#mOk').onclick = () => done(false);
  m.querySelector('#tmClear').onclick = () => done(true);
}

// ---------- 裏の処理 ----------
async function jobsDialog() {
  const j = await api('/api/jobs');
  const line = x => x ? `<div>${jobLine(x)} · ${esc(t('jb_state_' + x.state))}${x.current ? ' · ' + esc(x.current) : ''}${x.error ? ' · ' + esc(x.error) : ''}${x.message ? ' · ' + esc(x.message) : ''}</div>${x.total ? `<div class="prog"><div style="width:${Math.round(100 * (x.done + x.skipped + x.failed) / x.total)}%"></div></div>` : ''}` : `<div class="note">${esc(t('jb_none'))}</div>`;
  const m = showModal(`<h3>${esc(t('jb_title'))}</h3>
    <h4>${esc(t('jb_bg'))}</h4>${line(j.bg.current)}${j.bg.queued.length ? `<div class="note">${t('jb_queued')}: ${j.bg.queued.map(x => esc(x.label)).join(', ')}</div>` : ''}
    <div class="btn-row"><button type="button" class="small" id="jbStopBg">${esc(t('jb_stop'))}</button><button type="button" class="small" id="jbMeta">${esc(t('jb_meta_run'))}</button></div>
    <h4>${esc(t('detect'))}</h4>${line(j.ml && j.ml.current)}
    <div class="btn-row"><button type="button" class="small" id="jbStopMl">${esc(t('jb_stop'))}</button><button type="button" class="small" id="jbDetect">${esc(t('dt_title'))}…</button></div>
    <h4>${esc(t('archive'))}</h4>${line(j.archive.current)}
    <div class="btn-row"><button type="button" class="small" id="jbStopAr">${esc(t('jb_stop'))}</button></div>
    <h4>${esc(t('jb_tc'))}</h4>${line(j.transcode.current)}${j.transcoding.length ? `<div class="note">⟳ ${j.transcoding.length}</div>` : ''}
    <div class="btn-row"><button type="button" class="small" id="jbStopTc">${esc(t('jb_stop'))}</button></div>
    ${j.cross ? `<h4>${esc(t('jb_cross'))}</h4><div class="note">${esc(t('jb_cross_help'))}</div>${line(j.cross.current)}${j.cross.queued.length ? `<div class="note">${t('jb_queued')}: ${j.cross.queued.map(x => esc(x.label)).join(', ')}</div>` : ''}
    <div class="btn-row"><button type="button" class="small" id="jbStopCr">${esc(t('jb_stop'))}</button></div>` : ''}
    <h4>${esc(t('jb_recent'))}</h4>${(j.bg.recent || []).concat((j.ml && j.ml.recent) || [], j.archive.recent || [], j.transcode.recent || [], (j.cross && j.cross.recent) || []).map(line).join('')}
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#jbStopBg').onclick = async () => { await api('/api/jobs/stop', { lane: 'bg' }); jobsDialog(); };
  m.querySelector('#jbStopTc').onclick = async () => { await api('/api/jobs/stop', { lane: 'transcode' }); jobsDialog(); };
  m.querySelector('#jbStopAr').onclick = async () => { await api('/api/jobs/stop', { lane: 'archive' }); jobsDialog(); };
  m.querySelector('#jbStopMl').onclick = async () => { await api('/api/jobs/stop', { lane: 'ml' }); jobsDialog(); };
  if (m.querySelector('#jbStopCr')) m.querySelector('#jbStopCr').onclick = async () => { await api('/api/jobs/stop', { lane: 'cross' }); jobsDialog(); };
  m.querySelector('#jbDetect').onclick = () => detectDialog();
  m.querySelector('#jbMeta').onclick = async () => { await api('/api/jobs/meta', {}); jobsDialog(); };
}

// ---------- その他の種 ----------
function otherSpeciesDialog() {
  if (!S.cur) return;
  const all = (S.species.species || []);
  const m = showModal(`<h3>${esc(t('other_title'))}</h3><div class="note">${esc(t('other_help'))}</div>
    <div class="form-row"><label>${esc(t('search'))}</label><input type="text" id="osQ" spellcheck="false"></div>
    <div id="osList" style="max-height:50vh;overflow:auto" class="sp-grid"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  const render = () => {
    const q = m.querySelector('#osQ').value.trim().toLowerCase();
    const hit = all.filter(s => !q || [s.name_ja, s.name_en, s.name_sci, s.csv_label, s.code].some(x => (x || '').toLowerCase().includes(q)));
    m.querySelector('#osList').innerHTML = hit.map(s => `<label class="${S.sel.includes(s.code) ? 'on' : ''}" data-code="${esc(s.code)}"><input type="checkbox" ${S.sel.includes(s.code) ? 'checked' : ''}> <span>${esc(spName(s))}</span> <span class="info">${esc(s.name_sci || '')}</span></label>`).join('');
    m.querySelectorAll('#osList label').forEach(l => { l.onclick = e => { e.preventDefault(); toggleSpecies(l.dataset.code); render(); }; });
  };
  m.querySelector('#osQ').addEventListener('input', render);
  render();
}

// ---------- 担当 ----------
async function assignDialog() {
  const m = showModal(`<h3>${esc(t('assign_title'))}</h3><div class="note">${esc(t('assign_help'))}</div>
    <div class="form-row"><label>${esc(t('assign'))}</label><input type="text" id="asName" value="${esc(S.settings.user_name || '')}"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('cancel'))}</button><button id="mOk" class="primary">${esc(t('ok'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  m.querySelector('#mOk').onclick = async () => { const r = await api('/api/assign', { filters: S.filter, assignee: m.querySelector('#asName').value }); closeModal(); toast(`${r.n}`); refreshFacets(); reloadList(true); };
}

// ---------- 追加学習（第 3 段階） ----------
let trainModalEl = null;
function fmtPct(a, b) { return b ? (100 * a / b).toFixed(0) + '%' : '—'; }
function evalLine(ev) {    // {before, after} → 「一致率 0% → 45%」の列
  if (!ev || !ev.before || !ev.before.n) return '';
  const b = ev.before, a = ev.after;
  return `${fmtPct(b.exact, b.n)} → <b>${fmtPct(a.exact, a.n)}</b>`;
}
async function trainDialog(showName, preset) {
  // preset = {holdout_batches: ['gold']}（横断の「学習と評価」から開いたとき。評価用の組を検証用にして始める）
  const m = showModal(`<h3>${esc(t('tr_title'))}</h3><div class="note">${esc(t('tr_help'))}</div>
    <div id="trEnv" class="env-row">${esc(t('loading'))}</div>
    <div id="trPlan" class="dt-counts"></div>
    <div id="trSpecies" class="info"></div>
    <div class="form-row"><label>${esc(t('tr_kind'))}</label><select id="trKind"><option value="A">${esc(t('tr_kind_a'))}</option><option value="B">${esc(t('tr_kind_b'))}</option></select></div>
    <div class="form-row"><label>${esc(t('tr_name'))}</label><input type="text" id="trName" spellcheck="false" style="width:260px"></div>
    <div class="form-row hidden" id="trBatchRow"><label>${esc(t('tr_hold_batches'))}</label><span style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span id="trBatches"></span><span class="info">${esc(t('tr_hold_batches_help'))}</span></span></div>
    <div class="form-row"><label>${esc(t('tr_holdout'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="trHold" step="0.05" min="0" max="0.6" style="width:70px"> <span class="info" id="trHoldHelp">${esc(t('tr_holdout_help'))}</span><label class="chk" title="${esc(t('tr_final_wo_help'))}"><input type="checkbox" id="trFinalHold"> ${esc(t('tr_final_wo'))}</label></span></div>
    <details class="adv" id="trSrcDetails"><summary>${esc(t('tr_sources'))} <span id="trSrcN" class="info"></span></summary>
      <div class="note">${esc(t('tr_sources_help'))}</div>
      <div id="trSources" class="sp-grid" style="max-height:20vh;overflow:auto"></div>
      <div class="form-row"><label>${esc(t('tr_src_path'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="trSrcPath" spellcheck="false" style="width:320px"><button type="button" class="small" data-pick="trSrcPath" data-kind="folder" data-name="">${esc(t('pick'))}</button><button type="button" class="small" id="trSrcAdd">${esc(t('tr_src_add'))}</button></span></div>
      <div id="trSrcInfo"></div>
    </details>
    <details class="adv" id="trStDetails"><summary>${esc(t('tr_pick_stations'))} <span id="trStN" class="info"></span></summary><div style="margin:4px 0"><button type="button" class="small" id="trStClear">${esc(t('tr_st_clear'))}</button></div><div id="trStations" class="sp-grid" style="max-height:20vh;overflow:auto"></div><div class="note">${esc(t('tr_pick_help'))}</div></details>
    <details class="adv"><summary>${esc(t('dt_adv'))}</summary>
      <div class="form-row"><label>${esc(t('tr_min_conf'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="trMinConf" step="0.05" min="0.1" max="0.95" style="width:70px"> <span class="info">${esc(t('tr_min_conf_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('tr_w_hard'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="trWHard" step="0.5" min="1" max="10" style="width:70px"> ${esc(t('tr_times'))} <span class="info">${esc(t('tr_w_hard_help'))}</span></span></div>
      <div class="form-row"><label>${esc(t('tr_min_per'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="trMinPer" step="1" min="1" style="width:70px"> <label class="chk"><input type="checkbox" id="trEmpty"> ${esc(t('tr_use_empty'))}</label></span></div>
      <div class="form-row tr-a"><label>${esc(t('tr_mix'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="number" id="trMix" step="0.05" min="0" max="0.9" style="width:70px"> <span class="info">${esc(t('tr_mix_help'))}</span></span></div>
      <div class="form-row tr-b"><label>${esc(t('tr_b_opts'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${esc(t('tr_epochs'))} <input type="number" id="trEpochs" step="1" min="1" max="50" style="width:60px"> ${esc(t('dt_batch'))} <input type="number" id="trBatch" step="1" min="1" max="64" style="width:60px"> ${esc(t('tr_unfreeze'))} <input type="number" id="trUnfreeze" step="0.1" min="0" max="1" style="width:60px"> ${esc(t('tr_lr'))} <input type="number" id="trLr" step="0.00005" min="0.000001" max="0.01" style="width:90px"></span></div>
      <div class="form-row tr-b"><label>${esc(t('dt_device'))}</label><select id="trDevice"><option value="auto">${esc(t('dt_dev_auto'))}</option><option value="cuda">GPU (CUDA)</option><option value="mps">GPU (Apple)</option><option value="cpu">CPU</option></select></div>
    </details>
    <div class="form-row tr-b"><label>${esc(t('tr_init'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><select id="trInit"><option value="">${esc(t('tr_init_base'))}</option></select> <span class="info">${esc(t('tr_init_help'))}</span></span></div>
    <div id="trStatus"></div>
    <h4>${esc(t('tr_models'))}</h4>
    <div id="trModels"></div>
    <div class="note">${esc(t('tr_use_moved'))}</div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="trStop" class="danger-outline hidden">${esc(t('ar_stop'))}</button><button id="mOk" class="primary">${esc(t('tr_start'))}</button></div>`);
  trainModalEl = m;
  wirePicks(m);
  const q = sel => m.querySelector(sel);
  histWire(q('#trSrcPath'), 'train_source');
  let planData = null;
  let srcList = [];          // 材料に加える候補 [{path, name, checked, info}]
  let srcSeq = 0;
  const srcChecked = () => srcList.filter(x => x.checked);
  const renderStations = () => {
    const main = (planData ? planData.stations : []).map(x => ({ st: x.station || '', label: x.station || '—', n: x.n }));
    const extra = [];
    for (const x of srcChecked()) if (x.info && x.info.ok && !x.info.same) for (const y of x.info.stations || []) if (y.station) extra.push({ st: `${x.info.name}:${y.station}`, label: `${x.info.name}:${y.station}`, n: y.n });
    const keep = new Set([...m.querySelectorAll('#trStations input:checked')].map(c => c.dataset.st));
    q('#trStations').innerHTML = main.concat(extra).map(x => `<label><input type="checkbox" data-st="${esc(x.st)}" ${keep.has(x.st) ? 'checked' : ''}> <span>${esc(x.label)}</span> <span class="info">${x.n}</span></label>`).join('');
    m.querySelectorAll('#trStations input').forEach(c => { c.onchange = stationsN; });
    stationsN();
  };
  const stationsN = () => { const n = m.querySelectorAll('#trStations input:checked').length; q('#trStN').textContent = n ? `(${n})` : ''; };
  const batchesChecked = () => [...m.querySelectorAll('#trBatches input:checked')].map(c => c.dataset.b);
  const batchUi = () => {      // 検証用の組があるときは、地点の割合は使わず、検証用は保存するモデルにも入れない（gold を含まないモデルにする）
    const on = batchesChecked().length > 0;
    q('#trHold').disabled = on; q('#trHoldHelp').textContent = on ? t('tr_holdout_off_batches') : t('tr_holdout_help');
    if (on) q('#trFinalHold').checked = true;
    q('#trFinalHold').disabled = on;
  };
  const renderBatches = (checked) => {
    const bs = (planData && planData.batches) || [];
    q('#trBatchRow').classList.toggle('hidden', !bs.length);
    q('#trBatches').innerHTML = bs.map(b => `<label class="chk" title="${esc(t('tr_hold_batch_tip'))}"><input type="checkbox" data-b="${esc(b.batch)}" ${checked.includes(b.batch) ? 'checked' : ''}> ${esc(b.batch)} <span class="info">${b.n_ready} / ${b.n}</span></label>`).join('');
    m.querySelectorAll('#trBatches input').forEach(c => { c.onchange = batchUi; });
    batchUi();
  };
  q('#trStClear').onclick = () => { m.querySelectorAll('#trStations input').forEach(c => { c.checked = false; }); stationsN(); };
  const renderSources = () => {
    q('#trSources').innerHTML = srcList.length ? srcList.map(x => `<label><input type="checkbox" data-src="${x.id}" ${x.checked ? 'checked' : ''}> <span>${esc(x.name)}</span> <span class="info" title="${esc(x.path)}">${esc(x.path)}</span></label>`).join('') : `<div class="note">${esc(t('tr_src_none'))}</div>`;
    m.querySelectorAll('#trSources input[data-src]').forEach(c => { c.onchange = () => { const x = srcList.find(y => String(y.id) === c.dataset.src); if (x) x.checked = c.checked; refreshSources(); }; });
    const sel = srcChecked();
    q('#trSrcN').textContent = sel.length ? `(${sel.length} ${t('tr_src_selected')})` : '';
  };
  const refreshSources = async () => {
    const sel = srcChecked();
    renderSources();
    if (!sel.length) { q('#trSrcInfo').innerHTML = ''; renderStations(); return; }
    try {
      const r = await api('/api/train/sources', { paths: sel.map(x => x.path) });
      sel.forEach((x, i) => { x.info = r.sources[i]; });
      const nOk = sel.filter(x => x.info && x.info.ok && !x.info.same).length;
      q('#trSrcN').textContent = nOk ? `(${nOk} ${t('tr_src_selected')})` : '';
      q('#trSrcInfo').innerHTML = sel.map(x => { const i = x.info || {}; if (!i.ok) return `<div class="ng">${esc(x.name)}: ${esc(i.error || '')}</div>`; if (i.same) return `<div class="info">${esc(x.name)}: ${esc(t('tr_src_same'))}</div>`;
        return `<div><b>${esc(i.name)}</b>: ${esc(t('tr_n_ready'))} ${i.n_ready} <span class="info">(${esc(t('tr_n_feat'))} ${i.n_features})</span> · ${esc(t('tr_n_stations'))} ${i.stations.length}${i.species.length ? ` · ${i.species.map(y => `${esc(spLabel(y.code))} ${y.n}`).join(' · ')}` : ''}${i.mismatch ? ` <span class="ng">${esc(t('tr_src_mismatch'))} (${esc(i.speciesnet_model || '')})</span>` : ''} <button type="button" class="small" data-holdall="${esc(i.name)}" title="${esc(t('tr_src_holdall_tip'))}">${esc(t('tr_src_holdall'))}</button></div>`; }).join('');
    } catch (e) { q('#trSrcInfo').innerHTML = `<div class="ng">${esc(e.message)}</div>`; }
    renderStations();
    m.querySelectorAll('[data-holdall]').forEach(b => { b.onclick = () => { m.querySelectorAll('#trStations input').forEach(c => { if (c.dataset.st.startsWith(b.dataset.holdall + ':')) c.checked = true; }); q('#trStDetails').open = true; stationsN(); }; });
  };
  q('#trSrcAdd').onclick = () => {
    const pth = q('#trSrcPath').value.trim(); if (!pth) return;
    let x = srcList.find(y => y.path.toLowerCase() === pth.toLowerCase());
    if (!x) { x = { id: ++srcSeq, path: pth, name: pth.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean).slice(-2).join('/'), checked: true }; srcList.push(x); } else x.checked = true;
    q('#trSrcPath').value = '';
    refreshSources();
  };
  const kindUi = () => { const b = q('#trKind').value === 'B'; m.querySelectorAll('.tr-a').forEach(el => el.classList.toggle('hidden', b)); m.querySelectorAll('.tr-b').forEach(el => el.classList.toggle('hidden', !b)); if (!q('#trName').dataset.edited) q('#trName').value = defaultName(); };
  const defaultName = () => { const d = new Date(), z = n => String(n).padStart(2, '0'); return `${q('#trKind').value}_${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}`; };
  const renderModels = r => {
    const ms = r.models || [];
    const cur = r.classifier || 'speciesnet';
    q('#trModels').innerHTML = ms.length ? `<table class="tbl"><tr><th>${esc(t('tr_name'))}</th><th>${esc(t('tr_kind'))}</th><th>${esc(t('tr_trained_at'))}</th><th>${esc(t('tr_data'))}</th><th>${esc(t('tr_eval_short'))}</th><th></th></tr>` +
      ms.map(x => `<tr class="${cur === 'local:' + x.name ? 'cur' : ''}"><td><b>${esc(x.name)}</b>${cur === 'local:' + x.name ? ` <span class="chip ai">${esc(t('tr_in_use'))}</span>` : ''}${x.shared || x.also_shared ? ` <span class="chip human" title="${esc(t('tr_shared_tip'))}">${esc(t('tr_shared'))}</span>` : ''}${x.init_from ? `<div class="info">${esc(t('tr_init_from'))}: ${esc((x.init_chain || [x.init_from]).join(' → '))}</div>` : ''}${x.sources && x.sources.length ? `<div class="info">${esc(t('tr_src_with'))}: ${esc([x.project || ''].concat(x.sources.map(y => y.name)).filter(Boolean).join(' + '))}</div>` : ''}${x.final_without_holdout ? `<div class="info">${esc(t('tr_final_wo_short'))}</div>` : ''}</td><td>${esc(x.kind)}</td><td class="info">${esc((x.trained_at || '').slice(0, 16).replace('T', ' '))}</td><td class="info">${x.n_items || 0} ${esc(t('items'))} · ${x.n_crops || 0} ${esc(t('tr_crops'))} · ${(x.classes || []).length} ${esc(t('tr_species_u'))}</td><td>${x.eval ? `${evalLine(x.eval)} <span class="info">(${x.eval.n_items}${x.eval.batches && x.eval.batches.length ? ` · ${esc(x.eval.batches.join(', '))}` : ''})</span>` : `<span class="info">${esc(t('tr_no_holdout'))}</span>`}</td><td><button type="button" class="small" data-res="${esc(x.name)}">${esc(t('tr_result'))}</button> <button type="button" class="small" data-use="${esc(x.name)}" title="${esc(t('tr_row_apply_tip'))}">${esc(t('tr_row_apply'))}</button> ${!x.shared && !x.also_shared ? `<button type="button" class="small" data-share="${esc(x.name)}" title="${esc(t('tr_share_tip'))}">${esc(t('tr_share'))}</button> ` : ''}<button type="button" class="small" data-del="${esc(x.name)}">${esc(t('delete'))}</button></td></tr>`).join('') + '</table>'
      : `<div class="note">${esc(t('tr_none_models'))}</div>`;
    m.querySelectorAll('[data-use]').forEach(b => { b.onclick = () => { trainModalEl = null; detectDialog({ mode: 'reclass', classifier: 'local:' + b.dataset.use }); }; });
    const initNow = q('#trInit').value;
    fillSelect(q('#trInit'), [['', t('tr_init_base')]].concat(ms.filter(x => x.kind === 'B').map(x => [x.name, x.name + (x.shared ? ` (${t('tr_shared')})` : '')])), ms.some(x => x.kind === 'B' && x.name === initNow) ? initNow : '');
    m.querySelectorAll('[data-res]').forEach(b => { b.onclick = () => trainResultDialog(b.dataset.res); });
    m.querySelectorAll('[data-share]').forEach(b => { b.onclick = async () => { try { const r = await api('/api/train/models/share', { name: b.dataset.share }); toast(`${t('tr_shared_done')}: ${r.path}`); trainModalEl = null; trainDialog(); } catch (e) { toast(e.message, 'error'); } }; });
    m.querySelectorAll('[data-del]').forEach(b => { b.onclick = async () => {
      trainModalEl = null;
      if (await confirmModal(t('tr_title'), `${esc(t('tr_delete_q'))}: ${esc(b.dataset.del)}`, t('delete'), true)) { try { await api('/api/train/models/delete', { name: b.dataset.del }); } catch (e) { toast(e.message, 'error'); } }
      trainDialog();
    }; });
  };
  const plan = async () => {
    try {
      const r = await api('/api/train/plan');
      planData = r;
      const env = r.env, chip = (ok, label) => `<span class="${ok ? 'ok' : 'ng'}">${esc(label)} ${ok ? '✓' : '✗'}</span>`;
      let envHtml = env.fake ? `<span class="ok">${esc(t('dt_fake'))}</span>` : chip(env.torch, 'torch') + chip(env.speciesnet, 'speciesnet');
      const ti = env.torch_info;
      if (ti && !ti.error) envHtml += ti.cuda ? `<span class="ok">CUDA ✓ ${esc(ti.gpu || '')}</span>` : (ti.mps ? `<span class="ok">Apple MPS ✓</span>` : `<span class="ng">CUDA ✗</span>`);
      q('#trEnv').innerHTML = envHtml;
      q('#trPlan').innerHTML = `<span>${esc(t('st_verified'))} <b>${r.n_verified}</b></span><span>${esc(t('tr_n_ready'))} <b>${r.n_ready}</b> <span class="info">(${esc(t('tr_n_feat'))} ${r.n_features})</span></span><span>${esc(t('det_excluded'))} <b>${r.n_excluded}</b></span><span title="${esc(t('tr_w_hard_help'))}">${esc(t('tr_n_hard'))} <b>${r.n_hard}</b></span><span>${esc(t('tr_n_stations'))} <b>${r.stations.length}</b></span>`;
      q('#trSpecies').innerHTML = r.species.length ? `${esc(t('tr_species_counts'))}: ` + r.species.map(x => `${esc(spLabel(x.code))} ${x.n}`).join(' · ') : '';
      if (!srcList.length) srcList = (r.recent || []).map(x => ({ id: ++srcSeq, path: x.path, name: x.name, checked: false }));
      renderSources();
      renderStations();
      const firstTime = !q('#trName').value;
      if (firstTime) {
        const ps = r.settings;
        q('#trHold').value = ps.train_holdout_frac; q('#trMinConf').value = ps.train_min_conf; q('#trWHard').value = ps.train_weight_hard; q('#trMinPer').value = ps.train_min_per_class;
        q('#trEmpty').checked = ps.train_use_empty !== false; q('#trFinalHold').checked = !!ps.train_final_without_holdout; q('#trMix').value = ps.train_base_mix; q('#trEpochs').value = ps.train_epochs_b; q('#trBatch').value = ps.train_batch_b;
        q('#trUnfreeze').value = ps.train_unfreeze; q('#trLr').value = ps.train_lr_b; q('#trDevice').value = r.device || 'auto';
        kindUi();
      }
      renderBatches(firstTime ? ((preset && preset.holdout_batches) || r.eval_batches || []) : batchesChecked());      // 設定を入れた後に（検証用の組があれば「保存するモデルにも入れない」を付ける）
      renderModels(r);
      const ld = env.loaded || {};
      if (ld.classifier) q('#trEnv').innerHTML += ld.feat_dim ? `<span class="ok">${esc(t('tr_feat_layer'))} ${esc(ld.feat_layer || '')} (${ld.feat_dim})</span>` : `<span class="ng">${esc(t('tr_feat_none'))}</span>`;
      q('#mOk').disabled = !r.n_ready || !r.n_features;
      if (!r.n_ready) q('#trStatus').innerHTML = `<div class="note">${esc(t('tr_no_data'))}</div>`;
      else if (!r.n_features) q('#trStatus').innerHTML = `<div class="note">${esc(t('tr_no_feat'))}</div>`;
      else if (r.stations.length < 3) q('#trStatus').innerHTML = `<div class="note">${esc(t('tr_few_stations'))}</div>`;
    } catch (e) { q('#trPlan').textContent = e.message; }
  };
  q('#trKind').addEventListener('change', kindUi);
  q('#trName').addEventListener('input', () => { q('#trName').dataset.edited = '1'; });
  q('#mCancel').onclick = () => { trainModalEl = null; closeModal(); };
  q('#trStop').onclick = async () => { await api('/api/train/stop', {}); };
  q('#mOk').onclick = async () => {
    const body = {
      kind: q('#trKind').value, name: q('#trName').value.trim(), device: q('#trDevice').value,
      train_holdout_frac: parseFloat(q('#trHold').value), train_min_conf: parseFloat(q('#trMinConf').value), train_weight_hard: parseFloat(q('#trWHard').value),
      train_min_per_class: parseInt(q('#trMinPer').value) || 5, train_use_empty: q('#trEmpty').checked, train_final_without_holdout: q('#trFinalHold').checked, train_base_mix: parseFloat(q('#trMix').value),
      train_epochs_b: parseInt(q('#trEpochs').value) || 5, train_batch_b: parseInt(q('#trBatch').value) || 16, train_unfreeze: parseFloat(q('#trUnfreeze').value), train_lr_b: parseFloat(q('#trLr').value),
      holdout_stations: [...m.querySelectorAll('#trStations input:checked')].map(c => c.dataset.st),
      holdout_batches: batchesChecked(),
      init_from: q('#trKind').value === 'B' ? q('#trInit').value : '',
      sources: srcChecked().map(x => x.path),
    };
    try {
      const j = await api('/api/train/start', body);
      S._bgWasRunning = true;
      m._wasRunning = j.name;
      q('#trName').dataset.edited = '';
      trainOnJobs(j);
    } catch (e) { toast(e.message, 'error'); }
  };
  await plan();
  trainOnJobs(S.snap && S.snap.jobs);
  if (showName) trainResultDialog(showName);
}
function trainOnJobs(jobs) {
  const m = trainModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.ml) return;
  const cur = jobs.ml.current, rec = (jobs.ml.recent || [])[0];
  const st = m.querySelector('#trStatus');
  const isMine = j => j && (j.kind === 'train' || j.kind === 'reclassify');
  if (cur) {
    const pct = cur.total ? Math.round(100 * (cur.done + cur.failed) / cur.total) : 0;
    const phase = cur.kind === 'train' ? t('tr_phase_' + (cur.phase || 'collect')) : (cur.kind === 'reclassify' ? t('tr_reclassifying') : cur.label);
    st.innerHTML = `<div><b>${esc(phase)}</b>: ${cur.done}${cur.total ? ' / ' + cur.total : ''}${cur.total ? ` (${pct}%)` : ''}</div>${cur.total ? `<div class="prog"><div style="width:${pct}%"></div></div>` : ''}<div class="info">${esc(cur.current || '')}</div><div class="info">${esc(cur.message || '')}</div>`;
    m.querySelector('#trStop').classList.toggle('hidden', !isMine(cur)); m.querySelector('#mOk').disabled = true;
    if (isMine(cur)) m._wasRunning = m._wasRunning || cur.name || 'x';
  } else if (jobs.ml.queued && jobs.ml.queued.length) {
    st.innerHTML = `<div><b>${esc(t('jb_queued'))}</b></div>`;
    m.querySelector('#mOk').disabled = true;
  } else {
    st.innerHTML = rec && isMine(rec) ? `<div><b>${esc(t('ar_result'))}</b> (${esc(t('jb_state_' + rec.state))}): ${esc(rec.message || rec.error || '')}</div>` : '';
    m.querySelector('#trStop').classList.add('hidden'); m.querySelector('#mOk').disabled = false;
    if (m._wasRunning) {
      const name = m._wasRunning; m._wasRunning = false;
      trainModalEl = null;
      trainDialog(rec && rec.state === 'done' && rec.kind === 'train' && name !== 'reclassify' ? rec.name : null);
    }
  }
}
async function trainResultDialog(name) {
  let r;
  try { r = await api(`/api/train/result?name=${encodeURIComponent(name)}`); } catch (e) { toast(e.message, 'error'); return; }
  const nm = c => c === '(low)' ? t('det_low') : spLabel(c);
  const ev = r.eval;
  const block = (title, e, n, stations, batches) => {
    if (!e || !e.before || !e.before.n) return '';
    const b = e.before, a = e.after;
    const sp = sortCodes(Object.keys(Object.assign({}, b.by_species, a.by_species)));
    const cols = sortCodes(new Set([].concat(...Object.values(a.matrix || {}).map(row => Object.keys(row)), Object.keys(a.matrix || {}))));
    return `<h4>${esc(title)}</h4>${batches && batches.length ? `<div class="info">${esc(t('tr_hold_batches'))}: ${esc(batches.join(', '))}</div>` : ''}${stations && stations.length ? `<div class="info">${esc(t('tr_stations'))}: ${esc(stations.join(', '))}</div>` : ''}
      <div class="ag-big"><span class="ag-num">${fmtPct(b.exact, b.n)} → ${fmtPct(a.exact, a.n)}</span><span class="info">${esc(t('ag_overall'))} · ${b.exact} → ${a.exact} / ${n}</span>
        <span class="info">${esc(t('tr_first_ok'))} ${fmtPct(b.first, b.n)} → ${fmtPct(a.first, a.n)} · ${esc(t('det_low'))} ${b.low} → ${a.low}</span></div>
      <div style="max-height:30vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('dt_eval_human'))}</th><th>n</th><th>${esc(t('ag_rate'))} ${esc(t('tr_before'))} → ${esc(t('tr_after'))}</th><th>${esc(t('tr_first_ok'))}</th></tr>
      ${sp.map(c => { const x = b.by_species[c] || { n: 0, exact: 0, first: 0 }, y = a.by_species[c] || { n: 0, exact: 0, first: 0 }; return `<tr><td><b>${esc(spLabel(c))}</b></td><td>${y.n}</td><td>${fmtPct(x.exact, x.n)} → <b>${fmtPct(y.exact, y.n)}</b></td><td class="info">${fmtPct(x.first, x.n)} → ${fmtPct(y.first, y.n)}</td></tr>`; }).join('')}</table></div>
      ${cols.length ? `<details class="adv"><summary>${esc(t('ag_matrix'))} (${esc(t('tr_after'))})</summary><div style="max-height:30vh;overflow:auto"><table class="tbl matrix"><tr><th>${esc(t('cmp_human'))} \\ AI</th>${cols.map(c => `<th>${esc(nm(c))}</th>`).join('')}</tr>
        ${sortCodes(Object.keys(a.matrix)).map(h => { const row = a.matrix[h]; return `<tr><td><b>${esc(spLabel(h))}</b></td>${cols.map(c => `<td class="${c === h ? 'diag' : (row[c] ? 'off' : '')}">${row[c] || ''}</td>`).join('')}</tr>`; }).join('')}</table></div></details>` : ''}`;
  };
  const counts = Object.entries(r.counts || {}).map(([c, n]) => `${spLabel(c)} ${n}`).join(' · ');
  const dropped = Object.entries(r.dropped || {}).map(([c, n]) => `${spLabel(c)} ${n}`).join(' · ');
  const hist = ((r.train_record_all || r.train_record || {}).history || []);
  const m = showModal(`<h3>${esc(t('tr_result'))}: ${esc(r.name)} <span class="info">(${esc(t('tr_kind'))} ${esc(r.kind)} · ${esc((r.trained_at || '').slice(0, 16).replace('T', ' '))} · ${r.elapsed || 0} s)</span></h3>
    <div class="note">${esc(t('tr_result_help'))}</div>
    <div class="info">${esc(t('tr_data'))}: ${r.n_items} ${esc(t('items'))} · ${r.n_crops} ${esc(t('tr_crops'))} · ${(r.classes || []).length} ${esc(t('tr_species_u'))}（${esc(counts)}）${dropped ? `<br>${esc(t('tr_dropped'))}: ${esc(dropped)}` : ''}${r.n_nofeat ? `<br>${esc(t('tr_nofeat'))}: ${r.n_nofeat}` : ''}${r.init_from ? `<br>${esc(t('tr_init_from'))}: SpeciesNet → ${esc((r.init_chain || [r.init_from]).join(' → '))}` : ''}${r.sources && r.sources.length ? `<br>${esc(t('tr_src_with'))}: ${esc([r.project || ''].concat(r.sources.map(y => `${y.name} (${y.n_items})`)).filter(Boolean).join(' + '))}` : ''}${r.final_without_holdout ? `<br>${esc(t('tr_final_wo_short'))}: ${esc(((r.holdout_batches || []).map(b => `${t('filter_batch')} ${b}`)).concat(r.holdout_stations || []).join(', '))}` : ''}</div>
    ${ev ? block(t('tr_eval_title'), ev, ev.n_items, ev.stations, ev.batches) : `<div class="note">${esc(t('tr_no_holdout'))}</div>`}
    ${r.fit_all ? block(t('tr_fit_all'), r.fit_all, r.fit_all.after.n, null) : ''}
    ${hist.length ? `<details class="adv"><summary>${esc(t('tr_history'))}</summary><div class="info">${hist.map(h => `${h.epoch}: ${h.train_loss}${h.val_loss != null ? ' / ' + h.val_loss + (h.val_acc != null ? ' (' + (100 * h.val_acc).toFixed(0) + '%)' : '') : ''}`).join(' · ')}</div></details>` : ''}
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button></div>`);
  m.querySelector('#mCancel').onclick = () => trainDialog();
}

// ---------- YOLO 形式の学習データ（<種>/images + <種>/labels）を読む ----------
async function yoloDialog() {
  let pv;
  try { pv = await api('/api/yolo/preview'); } catch (e) { toast(e.message, 'error'); return; }
  const all = (S.species.species || []).concat(S.species.fixed || []);
  const opts = [['', t('yl_skip')]].concat(all.map(s => [s.code, spName(s)]));
  const rows = pv.folders.map(f => `<tr><td><b>${esc(f.folder)}</b></td><td>${f.n_images}</td><td>${f.n_labels}</td><td><select data-folder="${esc(f.folder)}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${v === (f.guess || '') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>${f.guess ? '' : ` <span class="flag-low">${esc(t('yl_noguess'))}</span>`}</td></tr>`).join('');
  const m = showModal(`<h3>${esc(t('yl_title'))}</h3><div class="note">${esc(t('yl_help'))}</div>
    <div class="dt-counts"><span>${esc(t('yl_n_images'))} <b>${pv.n_images}</b></span><span>${esc(t('yl_n_matching'))} <b>${pv.n_matching}</b></span><span>${esc(t('yl_n_named'))} <b>${pv.n_named}</b></span></div>
    ${pv.folders.length ? `<div style="max-height:45vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('yl_folder'))}</th><th>${esc(t('yl_images'))}</th><th>${esc(t('yl_labels'))}</th><th>${esc(t('species'))}</th></tr>${rows}</table></div>` : `<div class="note">${esc(t('yl_none'))}</div>`}
    <div class="form-row"><label></label><span style="display:flex;flex-direction:column;gap:2px"><label class="chk"><input type="checkbox" id="ylVerified" checked> ${esc(t('yl_verified'))}</label><label class="chk"><input type="checkbox" id="ylStation" checked> ${esc(t('yl_station'))}</label><label class="chk"><input type="checkbox" id="ylOverwrite"> ${esc(t('yl_overwrite'))}</label></span></div>
    <div id="ylResult" class="note"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="ylStationOnly" ${pv.folders.length ? '' : 'disabled'} data-title="yl_station_only_tip">${esc(t('yl_station_only'))}</button><button id="mOk" class="primary" ${pv.folders.length ? '' : 'disabled'}>${esc(t('run'))}</button></div>`);
  m.querySelector('#mCancel').onclick = closeModal;
  yoloModalEl = m;
  const run = async (stationOnly) => {
    const mapping = {};
    m.querySelectorAll('select[data-folder]').forEach(sel => { mapping[sel.dataset.folder] = sel.value || null; });
    m.querySelector('#mOk').disabled = true;
    try {
      const j = await api('/api/yolo/import', { mapping, as_verified: m.querySelector('#ylVerified').checked, set_station: m.querySelector('#ylStation').checked, overwrite_verified: m.querySelector('#ylOverwrite').checked, station_only: !!stationOnly });
      S._bgWasRunning = true; m._wasRunning = true;
      yoloOnJobs(j);
    } catch (e) { toast(e.message, 'error'); m.querySelector('#mOk').disabled = false; }
  };
  m.querySelector('#mOk').onclick = () => run(false);
  m.querySelector('#ylStationOnly').onclick = () => run(true);
  yoloOnJobs(S.snap && S.snap.jobs);
}
let yoloModalEl = null;
function yoloOnJobs(jobs) {   // 取り込みは裏の処理（bg レーン）で動くので、2 秒ごとの状況で進み具合と結果を出す
  const m = yoloModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.bg) return;
  const cur = jobs.bg.current, rec = (jobs.bg.recent || []).find(x => x.kind === 'yolo');
  const el = m.querySelector('#ylResult');
  if (!el) return;
  if (cur && cur.kind === 'yolo') {
    const pct = cur.total ? Math.round(100 * cur.done / cur.total) : 0;
    el.innerHTML = `<div><b>${esc(t('yl_running'))}</b>: ${cur.done} / ${cur.total} (${pct}%)</div><div class="prog"><div style="width:${pct}%"></div></div>`;
    m.querySelector('#mOk').disabled = true; m._wasRunning = true;
  } else if (jobs.bg.queued && jobs.bg.queued.some(x => x.kind === 'yolo')) {
    el.innerHTML = `<div><b>${esc(t('jb_queued'))}</b></div>`;
  } else if (m._wasRunning && rec) {
    m._wasRunning = false;
    el.textContent = `${t(rec.state === 'done' ? 'yl_done' : 'ar_result')}: ${rec.message || rec.error || ''}`;
    m.querySelector('#mOk').disabled = false;
    api('/api/state').then(snap => { setSnapshot(snap); reloadList(); if (S.cur) refreshCurrent(); }).catch(() => {});
  }
}

let exportModalEl = null;
// ---------- 選んだ動画を別のフォルダへプロジェクトごと写す（一覧の右クリック） ----------
let subsetModalEl = null;
async function subsetDialog(ids) {
  const m = showModal(`<h3>${esc(t('sub_title'))}</h3><div class="note">${esc(t('sub_help'))}</div>
    <div class="dt-counts"><span>${esc(t('items'))} <b>${ids.length}</b></span></div>
    ${pathRow('subDir', t('sub_dir'), 'folder', '', t('sub_dir_help'))}
    <div class="form-row"><label>${esc(t('sub_name'))}</label><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" id="subName" spellcheck="false" style="width:220px" placeholder="TrapNote"> <span class="info">${esc(t('sub_name_help'))}</span></span></div>
    <div class="form-row"><label></label><label class="chk"><input type="checkbox" id="subVideos" checked> ${esc(t('sub_videos'))}</label></div>
    <div id="subStatus"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('close'))}</button><button id="subStop" class="danger-outline hidden" title="${esc(t('sub_stop_tip'))}">${esc(t('jb_stop'))}</button><button id="mOk" class="primary">${esc(t('sub_run'))}</button></div>`);
  subsetModalEl = m;
  wirePicks(m);
  histWire(m.querySelector('#subDir'), 'subset_dir');
  const nameOf = p => p.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean).pop() || '';
  const autoName = () => { if (!m.querySelector('#subName').dataset.edited) { const n = nameOf(m.querySelector('#subDir').value.trim()); m.querySelector('#subName').value = n ? `TrapNote_${n}` : ''; } };
  m.querySelector('#subDir').addEventListener('input', autoName); m.querySelector('#subDir').addEventListener('change', autoName);
  m.querySelector('#subName').addEventListener('input', () => { m.querySelector('#subName').dataset.edited = '1'; });
  m.querySelector('#mCancel').onclick = () => { subsetModalEl = null; closeModal(); };
  m.querySelector('#subStop').onclick = async () => { try { const j = await api('/api/jobs/stop', { lane: 'bg' }); m.querySelector('#subStop').disabled = true; subsetOnJobs(j); } catch (e) { toast(e.message, 'error'); } };
  m.querySelector('#mOk').onclick = async () => {
    const out = m.querySelector('#subDir').value.trim();
    if (!out) { toast(t('cross_need_dir'), 'error'); return; }
    try {
      const j = await api('/api/export/subset', { ids, out_dir: out, copy_videos: m.querySelector('#subVideos').checked, project_dirname: m.querySelector('#subName').value.trim() });
      histRemember('subset_dir', out);
      S._bgWasRunning = true; m._running = true; m.querySelector('#mOk').disabled = true;
      subsetOnJobs(j);
    } catch (e) { toast(e.message, 'error'); }
  };
}
function subsetOnJobs(jobs) {
  const m = subsetModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.bg) return;
  const el = m.querySelector('#subStatus'); if (!el) return;
  const cur = jobs.bg.current, rec = (jobs.bg.recent || []).find(x => x.kind === 'subset_export');
  const stopBtn = m.querySelector('#subStop');
  if (cur && cur.kind === 'subset_export') {
    const pct = cur.total ? Math.round(100 * cur.done / cur.total) : 0;
    el.innerHTML = `<div><b>${esc(t('ar_running'))}</b>: ${cur.done} / ${cur.total} (${pct}%)</div><div class="prog"><div style="width:${pct}%"></div></div><div class="info">${esc(cur.current || '')}</div>`;
    m._running = true;
    stopBtn.classList.remove('hidden'); stopBtn.disabled = false;
  } else if (m._running && rec) {
    m._running = false;
    stopBtn.classList.add('hidden');
    const r = rec.result || {};
    el.innerHTML = `<div class="${rec.state === 'done' && !r.stopped ? 'ok' : 'warn'}"><b>${esc(t('ar_result'))}</b>: ${esc(rec.message || rec.error || '')}</div>${r.project_dir ? `<div class="btn-row"><button type="button" class="small" id="subOpen">${esc(t('sub_open'))}</button></div>` : ''}`;
    m.querySelector('#mOk').disabled = false;
    const b = el.querySelector('#subOpen'); if (b) b.onclick = () => { subsetModalEl = null; closeModal(); openProjectPath(rec.result.project_dir); };
  }
}

// ---------- 入力欄の候補（過去に入れた値。× で消せる） ----------
// histWire(input, key, extra): 欄に候補の窓を付ける。候補 = 設定の input_history[key] ∪ extra − input_hidden[key]。値を決めたら histRemember(key, value)
function histValues(key, extra) {
  const st = S.settings || {};
  const hid = new Set(((st.input_hidden || {})[key]) || []);
  const seen = new Set(); const out = [];
  for (const v of ((st.input_history || {})[key] || []).concat(extra || [])) { if (v && !hid.has(v) && !seen.has(v)) { seen.add(v); out.push(v); } }
  return out;
}
async function histSave(patch) { try { const d = await api('/api/settings', patch); S.settings = Object.assign(S.settings || {}, d); } catch (e) { /* 候補は無くても困らない */ } }
function histRemember(key, value) {
  value = (value || '').trim(); if (!value) return;
  const st = S.settings || {}; const h = Object.assign({}, st.input_history || {});
  h[key] = [value].concat((h[key] || []).filter(v => v !== value)).slice(0, 30);
  const hid = Object.assign({}, st.input_hidden || {}); if (hid[key]) hid[key] = hid[key].filter(v => v !== value);
  histSave({ input_history: h, input_hidden: hid });
}
function histForget(key, value) {
  const st = S.settings || {}; const h = Object.assign({}, st.input_history || {}), hid = Object.assign({}, st.input_hidden || {});
  h[key] = (h[key] || []).filter(v => v !== value);
  hid[key] = [value].concat((hid[key] || []).filter(v => v !== value)).slice(0, 30);
  histSave({ input_history: h, input_hidden: hid });
}
function histWire(input, key, extra) {
  if (!input) return;
  input.setAttribute('autocomplete', 'off');
  let box = null;
  const close = () => { if (box) { box.remove(); box = null; } };
  const open = () => {
    close();
    const typed = input.value.trim().toLowerCase();
    const vals = histValues(key, typeof extra === 'function' ? extra() : extra).filter(v => !typed || v.toLowerCase().includes(typed));
    if (!vals.length) return;
    box = document.createElement('div'); box.className = 'hist';
    box.innerHTML = vals.map(v => `<div class="hrow"><span class="hv" title="${esc(v)}">${esc(v)}</span><button type="button" class="hx" title="${esc(t('hist_forget'))}">×</button></div>`).join('');
    const r = input.getBoundingClientRect();
    box.style.left = r.left + 'px'; box.style.top = (r.bottom + 2) + 'px'; box.style.minWidth = Math.max(160, r.width) + 'px';
    document.body.appendChild(box);
    box.querySelectorAll('.hrow').forEach((row, i) => {
      row.querySelector('.hv').onmousedown = e => { e.preventDefault(); input.value = vals[i]; input.dispatchEvent(new Event('input')); input.dispatchEvent(new Event('change')); close(); };
      row.querySelector('.hx').onmousedown = e => { e.preventDefault(); e.stopPropagation(); histForget(key, vals[i]); row.remove(); if (!box.querySelector('.hrow')) close(); };
    });
  };
  input.addEventListener('focus', open);
  input.addEventListener('input', open);
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  input.addEventListener('change', () => histRemember(key, input.value));
}

function yoloExportOnJobs(jobs) {
  const m = exportModalEl;
  if (!m || !document.body.contains(m) || !jobs || !jobs.bg) return;
  const el = m.querySelector('#exYoloStatus'); if (!el) return;
  const cur = jobs.bg.current, rec = (jobs.bg.recent || []).find(x => x.kind === 'yolo_export');
  if (cur && cur.kind === 'yolo_export') {
    const pct = cur.total ? Math.round(100 * cur.done / cur.total) : 0;
    el.innerHTML = `<div><b>${esc(t('ar_running'))}</b>: ${cur.done} / ${cur.total} (${pct}%)</div><div class="prog"><div style="width:${pct}%"></div></div>`;
    m._yoloRunning = true;
  } else if (m._yoloRunning && rec) {
    m._yoloRunning = false;
    el.textContent = `${t(rec.state === 'done' ? 'ex_done' : 'ar_result')}: ${rec.message || rec.error || ''}`;
  }
}
