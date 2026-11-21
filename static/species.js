// 種リストと、種の登録（確認事項 149 回目）
//   speciesDialog(opts)          種リストの窓。ツールバーの「種リスト」、設定の窓、自動判定の窓から開く。opts.onClose = 閉じたあとに戻る先
//   speciesRegisterDialog(opts)  「種を登録」。登録のしかたを選ぶ: 1 種ずつ入れる / CSV からまとめて / SpeciesNet の候補から / 日本の哺乳類の一覧。
//                                opts.back = 終わったあとに戻る先（なければ窓を閉じる）、opts.selectAfter = 1 種ずつ入れたとき、いま開いている動画でその種を選ぶ
// 種の名前の出し方（混ざらないように、役割を 3 つに分けてある）:
//   名前      画面に出す。日本語の画面では和名、英語の画面では英名（なければ、もう一方 → 学名）
//   学名      自動判定（SpeciesNet）と Camtrap DP 形式は、学名でつなぐ。入れると、自動判定での対応（種・属・科…）が自動で決まる
//   CSV 表記  CSV の Species の列に書く名前。空なら英名から作る

const SP_METHODS = ['manual', 'csv', 'cand', 'defaults'];

async function speciesReload() {
  S.species = await api('/api/species');
  rebuildSpMap(); renderFilters();
  renderSpeciesPanel();                    // 動画を開いていなくても、右の欄の種の並びは入れ替える
  if (S.cur) renderFieldsPanel();
  renderWindow(true);
}

// 自動判定での対応の印（✓ 種で対応 / ✓ 属で対応 / — 対応なし / ? 未確認）
function spAiChip(level) {
  const lv = level || 'none';
  const ok = ['species', 'genus', 'family', 'order', 'class'].includes(lv);
  const tip = t('sp_ai_tip_' + (ok ? (lv === 'species' || lv === 'genus' ? lv : 'higher') : lv));
  return `<span class="sp-chip ${ok ? 'ok' : lv}" title="${esc(tip)}">${ok ? '✓' : (lv === 'unknown' ? '?' : '—')} ${esc(t('sp_ai_' + lv))}</span>`;
}

function spMethodCards() {
  const few = ((S.species && S.species.species) || []).length < 10;        // 日本の哺乳類の一覧は、種がまだ少ないときだけ出す（もう入っているプロジェクトでは要らない）
  return `<div class="sp-methods">${SP_METHODS.filter(k => k !== 'defaults' || few).map(k => `<button type="button" class="sp-method" data-m="${k}"><b>${esc(t('sr_m_' + k))}</b><span>${esc(t('sr_m_' + k + '_help'))}</span></button>`).join('')}</div>`;
}
function spWireMethods(m, opts) {
  const go = { manual: speciesManualDialog, csv: speciesCsvDialog, cand: speciesCandidatesDialog, defaults: speciesDefaultsAdd };
  m.querySelectorAll('.sp-method').forEach(b => { b.onclick = () => go[b.dataset.m](opts); });
}

// ---------- 種リストの窓 ----------
async function speciesDialog(opts) {
  opts = opts || {};
  let sp, st;
  try { [sp, st] = await Promise.all([api('/api/species'), api('/api/species/status')]); } catch (e) { toast(e.message, 'error'); return; }
  S.species = sp; rebuildSpMap();
  let rows = JSON.parse(JSON.stringify(sp.species));
  let adv = false;
  const ja = LANG === 'ja';
  const done = () => { if (opts.onClose) opts.onClose(); else closeModal(); };
  const self = () => speciesDialog(opts);
  const render = () => {
    const showJa = ja || rows.some(r => r.name_ja);
    const n = rows.length, on = rows.filter(r => r.enabled).length;
    const sum = t('sp_sum').replace('{n}', n).replace('{m}', on).replace('{a}', st.ai.length) + (st.no_ai.length ? t('sp_sum_noai').replace('{k}', st.no_ai.length) : '');
    const cInfo = !st.country_known ? `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>` : (st.country ? (st.n_candidates != null ? esc(t('sp_country_n').replace('{c}', st.country).replace('{n}', st.n_candidates)) : '') : esc(t('sp_country_none')));
    const head = `<tr><th>${esc(t('sp_use'))}</th>${showJa ? `<th>${esc(t('sp_ja'))}</th>` : ''}<th>${esc(t(showJa ? 'sp_en' : 'sp_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sp_csv'))}</th><th>${esc(t('sp_key'))}</th><th>${esc(t('sp_ai'))}</th>${adv ? `<th>${esc(t('sp_rank'))}</th><th>${esc(t('sp_nn'))}</th><th>${esc(t('sp_sn'))}</th>` : ''}<th></th></tr>`;
    const body = rows.map((r, i) => `<tr class="${r.enabled ? '' : 'off'}" data-i="${i}"><td><input type="checkbox" data-k="enabled" ${r.enabled ? 'checked' : ''}></td>${showJa ? `<td><input data-k="name_ja" value="${esc(r.name_ja)}"></td>` : ''}<td><input data-k="name_en" value="${esc(r.name_en)}"></td><td><input data-k="name_sci" value="${esc(r.name_sci)}" spellcheck="false"></td><td><input data-k="csv_label" value="${esc(r.csv_label)}" spellcheck="false"></td><td><input data-k="key" value="${esc(r.key)}" maxlength="1" style="width:34px"></td><td class="sp-ai">${r.code ? spAiChip(st.status[r.code]) : ''}</td>${adv ? `<td><input data-k="rank" value="${esc(r.rank)}" style="width:40px"></td><td><input type="checkbox" data-k="non_native" ${r.non_native ? 'checked' : ''}></td><td><input data-k="speciesnet" value="${esc(r.speciesnet)}" spellcheck="false"></td>` : ''}<td><button type="button" class="small del" title="${esc(t('sp_del'))}">✕</button></td></tr>`).join('');
    const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sp_title'))}</h3>
      <div class="note">${esc(t('sp_help'))}</div>
      <div class="sp-summary">${esc(sum)}</div>
      <div class="form-row"><label>${esc(t('sp_country'))}</label><span class="sp-country"><input type="text" id="spCountry" maxlength="3" value="${esc(st.country)}" spellcheck="false"> <span class="info" id="spCountryInfo">${cInfo}</span></span><div class="help">${esc(t('sp_country_help'))}</div></div>
      ${n ? `<div class="btn-row sp-actions"><button type="button" class="primary" id="spRegister">${esc(t('sp_register'))}</button><button type="button" class="small" id="spAllOn">${esc(t('sp_all_on'))}</button><button type="button" class="small" id="spAllOff">${esc(t('sp_all_off'))}</button><button type="button" class="small" id="spAdv">${esc(t(adv ? 'sp_adv_off' : 'sp_adv'))}</button></div>
      <div class="note">${esc(t('sp_names_help'))}</div>
      <div class="sp-tablebox"><table class="list sp-table">${head}${body}</table></div>`
      : `<div class="sp-empty">${esc(t('sp_empty'))}</div>${spMethodCards()}`}
      <div class="modal-actions"><button id="mCancel">${esc(t(n ? 'cancel' : 'close'))}</button>${n ? `<button id="mOk" class="primary">${esc(t('save'))}</button>` : ''}</div></div>`);
    const read = () => { m.querySelectorAll('tr[data-i]').forEach(tr => { const r = rows[parseInt(tr.dataset.i)]; tr.querySelectorAll('[data-k]').forEach(inp => { r[inp.dataset.k] = inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value; }); }); };
    const save = async () => { read(); S.species = await api('/api/species', { rows }); rebuildSpMap(); };
    const regOpts = { back: self };
    m.querySelector('#spCountry').addEventListener('change', async () => {
      const c = m.querySelector('#spCountry').value.trim().toUpperCase();
      if (c && !/^[A-Z]{3}$/.test(c)) { m.querySelector('#spCountryInfo').innerHTML = `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>`; return; }
      try {
        if (n) await save();
        const r = await api('/api/project/settings', { country: c });
        if (S.project) S.project.settings = r.settings;
        self();
      } catch (e) { toast(e.message, 'error'); }
    });
    m.querySelector('#mCancel').onclick = done;
    if (!n) { spWireMethods(m, regOpts); return; }
    m.querySelectorAll('button.del').forEach(b => { b.onclick = () => { read(); rows.splice(parseInt(b.closest('tr').dataset.i), 1); render(); }; });
    m.querySelector('#spAllOn').onclick = () => { read(); rows.forEach(r => { r.enabled = 1; }); render(); };
    m.querySelector('#spAllOff').onclick = () => { read(); rows.forEach(r => { r.enabled = 0; }); render(); };
    m.querySelector('#spAdv').onclick = () => { read(); adv = !adv; render(); };
    m.querySelector('#spRegister').onclick = async () => { try { await save(); speciesRegisterDialog(regOpts); } catch (e) { toast(e.message, 'error'); } };      // 表で直したぶんは、先に保存する
    m.querySelector('#mOk').onclick = async () => { try { await save(); renderFilters(); renderSpeciesPanel(); if (S.cur) renderFieldsPanel(); renderWindow(true); done(); } catch (e) { toast(e.message, 'error'); } };
  };
  render();
}

// ---------- 種を登録: 登録のしかたを選ぶ ----------
function speciesRegisterDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_title'))}</h3><div class="note">${esc(t('sr_help'))}</div>
    ${spMethodCards()}
    <div class="modal-actions"><button id="mCancel">${esc(t(opts.back ? 'sr_back' : 'close'))}</button></div></div>`);
  m.querySelector('#mCancel').onclick = () => { if (opts.back) opts.back(); else closeModal(); };
  spWireMethods(m, opts);
}
// 登録が済んだあと: 種リストを読み直して、戻る先（種リストの窓）へ。なければ窓を閉じる
async function speciesAfterRegister(opts, msg) {
  try { await speciesReload(); } catch (e) { /* 読み直せなくても、登録は済んでいる */ }
  if (msg) toast(msg);
  if (opts && opts.back) opts.back(); else closeModal();
}

// ---- 1 種ずつ入れる ----
function speciesManualDialog(opts) {
  opts = opts || {};
  const ja = LANG === 'ja';
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_manual'))}</h3><div class="note">${esc(t('as_help'))}</div>
    ${ja ? `<div class="form-row"><label>${esc(t('sp_ja'))}</label><input type="text" id="asJa"></div>` : ''}
    <div class="form-row"><label>${esc(t(ja ? 'sp_en' : 'sp_name'))}</label><input type="text" id="asEn"></div>
    <div class="form-row"><label>${esc(t('sp_sci'))}</label><input type="text" id="asSci" spellcheck="false" placeholder="${esc(t('as_sci_ph'))}"><div class="help" id="asAi"></div></div>
    <div class="form-row"><label>${esc(t('sp_csv'))}</label><input type="text" id="asCsv" spellcheck="false" placeholder="${esc(t('as_csv_ph'))}"><div class="help">${esc(t('as_csv_help'))}</div></div>
    <div class="form-row"><label>${esc(t('sp_key'))}</label><input type="text" id="asKey" maxlength="1" style="width:40px"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary">${esc(t('as_ok'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  q('#mCancel').onclick = () => speciesRegisterDialog(opts);
  let timer = null, seq = 0;
  q('#asSci').addEventListener('input', () => {            // 学名を打つと、自動判定（SpeciesNet）での対応を出す
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const sci = q('#asSci') ? q('#asSci').value.trim() : '', me = ++seq;
      if (!sci) { if (q('#asAi')) q('#asAi').textContent = ''; return; }
      try {
        const r = await api('/api/species/suggest?sci=' + encodeURIComponent(sci));
        if (me !== seq || !q('#asAi')) return;
        const lv = r.level || 'none', kind = lv === 'species' || lv === 'genus' || lv === 'none' ? lv : 'higher';
        q('#asAi').textContent = r.available ? t('as_ai_' + kind).replace('{v}', r.value || '') : '';
        q('#asAi').className = 'help' + (r.available && lv === 'none' ? ' sp-warn' : '');
      } catch (e) { /* 出せなくても登録はできる */ }
    }, 300);
  });
  const ok = async () => {
    const row = { name_ja: ja ? q('#asJa').value.trim() : '', name_en: q('#asEn').value.trim(), name_sci: q('#asSci').value.trim(), csv_label: q('#asCsv').value.trim(), key: q('#asKey').value.trim(), rank: '種', enabled: 1 };
    if (!row.name_ja && !row.name_en && !row.name_sci) { toast(t('as_need_name'), 'error'); return; }
    try {
      const r = await api('/api/species/add', row);
      const existed = (S.species.species || []).some(s => s.code === r.added.code);
      S.species = { species: r.species, fixed: r.fixed }; rebuildSpMap();
      if (existed) toast(t('as_exists'));
      if (opts.selectAfter) {                          // 判定の欄の「＋ 登録」から: いま開いている動画で、その種を選ぶ
        closeModal();
        if (S.cur && !S.sel.includes(r.added.code)) toggleSpecies(r.added.code); else { renderSpeciesPanel(); renderFieldsPanel(); }
        renderFilters();
        return;
      }
      speciesAfterRegister(opts, existed ? '' : t('sr_added').replace('{n}', 1));
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#mOk').onclick = ok;
  m.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); ok(); } }));
  (q('#asJa') || q('#asEn')).focus();
}

// ---- CSV からまとめて登録: ひな形を書き出す → 種を書き込む → 読み込む（読む前に、何が変わるかを見せる） ----
function speciesCsvDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_csv'))}</h3>
    <ol class="sp-steps">
      <li><b>${esc(t('sc_1'))}</b><div class="note">${esc(t('sc_1_help'))}</div><button type="button" id="scTpl">${esc(t('sc_1_btn'))}</button> <span class="info" id="scTplStatus"></span></li>
      <li><b>${esc(t('sc_2'))}</b><div class="note">${esc(t('sc_2_help'))}</div></li>
      <li><b>${esc(t('sc_3'))}</b><div class="note">${esc(t('sc_3_help'))}</div><button type="button" id="scPick">${esc(t('sc_3_btn'))}</button> <span class="info" id="scFile"></span><div id="scPlan"></div></li>
    </ol>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary" disabled>${esc(t('sc_apply'))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  let path = '';
  q('#mCancel').onclick = () => speciesRegisterDialog(opts);
  q('#scTpl').onclick = async () => {
    const p = await pickPath('savecsv', `${S.project.dir}/exports`, 'species_template.csv');
    if (!p) return;
    try { const r = await api('/api/species/template', { path: p }); if (q('#scTplStatus')) q('#scTplStatus').textContent = `${t('sc_1_done')}: ${r.path}`; } catch (e) { toast(e.message, 'error'); }
  };
  q('#scPick').onclick = async () => {
    const p = await pickPath('csv', `${S.project.dir}/exports`);
    if (!p) return;
    try {
      const r = await api('/api/species/import/preview', { path: p });
      if (!q('#scPlan')) return;
      path = p;
      q('#scFile').textContent = p;
      const lines = [t('sc_plan').replace('{new}', r.new).replace('{update}', r.update).replace('{same}', r.same)];
      if (r.fixed) lines.push(t('sc_skip_fixed').replace('{n}', r.fixed));
      if (r.dup) lines.push(t('sc_skip_dup').replace('{n}', r.dup));
      if ((r.no_name || []).length) lines.push(t('sc_no_name').replace('{lines}', r.no_name.join(', ')));
      if ((r.unknown || []).length) lines.push(t('sc_unknown').replace('{cols}', r.unknown.join(', ')));
      const show = (r.plan || []).filter(x => x.action === 'new' || x.action === 'update').slice(0, 60);
      const table = show.length ? `<div class="sp-tablebox small"><table class="list"><tr><th>${esc(t('sc_col_line'))}</th><th>${esc(t('sp_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sc_col_do'))}</th></tr>${show.map(x => `<tr><td>${x.line}</td><td>${esc(x.name)}</td><td>${esc(x.sci)}</td><td>${esc(t('sc_a_' + x.action))}${x.changes && x.changes.length ? ` <span class="info">${esc(x.changes.map(c => t('sc_f_' + c)).join(t('ex_dp_sep')))}</span>` : ''}</td></tr>`).join('')}</table></div>` : '';
      q('#scPlan').innerHTML = `<div class="sp-plan ${r.new + r.update ? 'ok' : ''}">${lines.map(esc).join('<br>')}</div>${table}`;
      q('#mOk').disabled = !(r.new + r.update);
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#mOk').onclick = async () => {
    if (!path) return;
    try {
      const r = await api('/api/species/import', { path });
      speciesAfterRegister(opts, t('sc_done').replace('{new}', r.new).replace('{update}', r.update));
    } catch (e) { toast(e.message, 'error'); }
  };
}

// ---- SpeciesNet の候補から選ぶ: 自動判定の AI が知っている種のうち、調査地の国で候補になるもの ----
async function speciesCandidatesDialog(opts) {
  opts = opts || {};
  const m = showModal(`<div class="sp-wrap"><h3>${esc(t('sr_m_cand'))}</h3><div class="note">${esc(t('sk_help'))}</div>
    <div class="form-row"><label>${esc(t('sp_country'))}</label><span class="sp-country"><input type="text" id="skCountry" maxlength="3" spellcheck="false" list="skCountries"><datalist id="skCountries"></datalist> <span class="info" id="skCountryInfo"></span></span><div class="help">${esc(t('sp_country_help'))}</div></div>
    <div id="skBody"><div class="note">${esc(t('loading'))}</div></div>
    <div class="modal-actions"><button id="mCancel">${esc(t('sr_back'))}</button><button id="mOk" class="primary" disabled>${esc(t('sk_add').replace('{k}', 0))}</button></div></div>`);
  const q = sel => m.querySelector(sel);
  const alive = () => document.body.contains(q('#skBody')) && q('#skBody');
  const picked = new Set();
  let data = null, poll = null;
  const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
  q('#mCancel').onclick = () => { stopPoll(); speciesRegisterDialog(opts); };
  const groupOf = x => x.cls === 'mammalia' ? 'mam' : (x.cls === 'aves' ? 'bird' : 'other');
  const visible = () => {
    const text = (q('#skQ').value || '').trim().toLowerCase();
    const g = { mam: q('#skMam').checked, bird: q('#skBird').checked, other: q('#skOther').checked }, higher = q('#skHigher').checked;
    return data.rows.filter(x => g[groupOf(x)] && (higher || x.rank === 'species') && (!text || x.name.toLowerCase().includes(text) || x.sci.toLowerCase().includes(text) || x.order.includes(text) || x.family.includes(text)));
  };
  const count = () => { const btn = q('#mOk'); if (btn) { btn.textContent = t('sk_add').replace('{k}', picked.size); btn.disabled = !picked.size; } const c = q('#skCount'); if (c) c.textContent = t('sk_count').replace('{n}', visible().length).replace('{k}', picked.size); };
  const list = () => {
    const rows = visible();
    q('#skList').innerHTML = `<table class="list"><tr><th></th><th>${esc(t('sk_col_name'))}</th><th>${esc(t('sp_sci'))}</th><th>${esc(t('sk_col_group'))}</th></tr>${rows.map(x => `<tr class="${x.have ? 'off' : ''}"><td><input type="checkbox" data-v="${esc(x.value)}" ${x.have && x.on ? 'checked disabled' : (picked.has(x.value) ? 'checked' : '')}></td><td>${esc(x.name)}${x.rank !== 'species' ? ` <span class="info">${esc(t('sp_ai_' + x.rank))}</span>` : ''}${x.have ? ` <span class="info">${esc(t(x.on ? 'sk_have' : 'sk_have_off'))}</span>` : ''}</td><td><i>${esc(x.sci)}</i></td><td class="info">${esc(x.order)} / ${esc(x.family)}</td></tr>`).join('')}</table>`;
    q('#skList').querySelectorAll('input[data-v]').forEach(c => { c.onchange = () => { if (c.checked) picked.add(c.dataset.v); else picked.delete(c.dataset.v); count(); }; });
    count();
  };
  const show = () => {
    if (!alive()) return;
    q('#skCountry').value = data.country || '';
    q('#skCountries').innerHTML = (data.countries || []).map(c => `<option value="${c}">`).join('');
    q('#skCountryInfo').innerHTML = !data.country_known ? `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>` : (data.country ? '' : esc(t('sp_country_none')));
    if (!data.available) {                     // SpeciesNet のモデルをまだ取ってきていない: 取ってくるボタンを出す（終わるまで、ときどき見に行く）
      q('#skBody').innerHTML = `<div class="sp-empty">${esc(t(data.env_speciesnet ? 'sk_none_avail' : 'sk_no_env'))}</div>${data.env_speciesnet ? `<button type="button" id="skFetch">${esc(t('sk_fetch'))}</button> <span class="info" id="skFetchInfo"></span>` : ''}`;
      const f = q('#skFetch');
      if (f) f.onclick = async () => {
        try {
          await api('/api/species/model', {});
          f.disabled = true; q('#skFetchInfo').textContent = t('sk_fetching');
          stopPoll();
          poll = setInterval(async () => { if (!alive()) { stopPoll(); return; } try { const r = await api('/api/species/candidates'); if (r.available) { stopPoll(); data = r; show(); } } catch (e) { /* 次の回に */ } }, 4000);
        } catch (e) { toast(e.message, 'error'); }
      };
      return;
    }
    q('#skBody').innerHTML = `<div class="sk-tools"><input type="text" id="skQ" placeholder="${esc(t('sk_q'))}" spellcheck="false"><label class="chk"><input type="checkbox" id="skMam" checked> ${esc(t('sk_mam'))}</label><label class="chk"><input type="checkbox" id="skBird"> ${esc(t('sk_bird'))}</label><label class="chk"><input type="checkbox" id="skOther"> ${esc(t('sk_other'))}</label><label class="chk"><input type="checkbox" id="skHigher"> ${esc(t('sk_higher'))}</label></div>
      <div class="sk-bar"><span id="skCount"></span><button type="button" class="small" id="skAll">${esc(t('sk_all'))}</button><button type="button" class="small" id="skClear">${esc(t('sk_clear'))}</button></div>
      <div class="sp-tablebox" id="skList"></div>`;
    q('#skQ').addEventListener('input', list);
    ['#skMam', '#skBird', '#skOther', '#skHigher'].forEach(sel => q(sel).addEventListener('change', list));
    q('#skAll').onclick = () => { visible().forEach(x => { if (!(x.have && x.on)) picked.add(x.value); }); list(); };
    q('#skClear').onclick = () => { picked.clear(); list(); };
    list();
  };
  const load = async country => {
    try { data = await api('/api/species/candidates' + (country == null ? '' : '?country=' + encodeURIComponent(country))); show(); } catch (e) { toast(e.message, 'error'); }
  };
  q('#skCountry').addEventListener('change', async () => {      // 国を変えたら、プロジェクトの国（自動判定の geofence）も同じにする
    const c = q('#skCountry').value.trim().toUpperCase();
    if (c && !/^[A-Z]{3}$/.test(c)) { q('#skCountryInfo').innerHTML = `<span class="sp-warn">${esc(t('sp_country_bad'))}</span>`; return; }
    await load(c);
    if (data && data.country_known) { try { const r = await api('/api/project/settings', { country: c }); if (S.project) S.project.settings = r.settings; } catch (e) { toast(e.message, 'error'); } }
  });
  q('#mOk').onclick = async () => {
    if (!picked.size) return;
    try {
      const r = await api('/api/species/add_many', { values: Array.from(picked) });
      stopPoll();
      speciesAfterRegister(opts, t('sr_added').replace('{n}', r.added) + (r.existed ? t('sr_added_have').replace('{k}', r.existed) : ''));
    } catch (e) { toast(e.message, 'error'); }
  };
  load(null);
}

// ---- 日本の哺乳類の一覧を入れる ----
async function speciesDefaultsAdd(opts) {
  try {
    const r = await api('/api/species/add_many', { defaults: true });
    speciesAfterRegister(opts, r.added ? t('sr_added').replace('{n}', r.added) : t('sr_defaults_none'));
  } catch (e) { toast(e.message, 'error'); }
}

// ---------- 自動判定の窓に出す、種リストの様子（どの種が付くか。1 つもなければ、始めさせない） ----------
// el = 入れる場所。返り値 = 自動判定で付けられる種の数（分からなければ null）
async function speciesBlockForDetect(el, onOpen) {
  let st;
  try { st = await api('/api/species/status'); } catch (e) { return null; }
  if (!document.body.contains(el)) return null;
  const names = codes => { const xs = codes.map(c => spLabel(c)); return xs.slice(0, 14).join(t('ex_dp_sep')) + (xs.length > 14 ? ` ${t('dt_sp_more').replace('{k}', xs.length - 14)}` : ''); };
  const n = st.ai.length;
  el.className = 'dt-species not-lite ' + (n ? 'ok' : 'ng');
  el.innerHTML = `<div class="dt-sp-head"><b>${esc(t('dt_sp_title'))}</b>: ${esc(t('dt_sp_n').replace('{n}', n))} <button type="button" class="small" id="dtSpOpen">${esc(t('dt_sp_open'))}</button></div>`
    + (n ? `<div>${esc(names(st.ai))}</div><div class="info">${esc(t('dt_sp_note'))}</div>` : `<div class="sp-warn">${esc(t('dt_sp_zero'))}</div>`)
    + (st.no_ai.length ? `<div class="info">${esc(t('dt_sp_noai'))}: ${esc(names(st.no_ai))}</div>` : '')
    + `<div class="info">${esc(t('sp_country'))}: ${esc(st.country || t('dt_sp_country_none'))}${st.country_known ? '' : ` — ${esc(t('sp_country_bad'))}`}</div>`;
  const b = el.querySelector('#dtSpOpen');
  if (b) b.onclick = onOpen;
  return n;
}
