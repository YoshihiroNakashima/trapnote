/* 地点のフォルダを選ぶ（確認事項 125 回目で決めたもの。129 回目で作った）。サーバー側は station.py
   1 本の動画までのフォルダの並びを見せて、地点（カメラを置いた所）にあたるフォルダと、あれば調査回にあたるフォルダを選んでもらう。
   - フォルダを開いて作ったばかりのプロジェクトでは、走査が済んで動画が見つかったら、一度だけ自動で出す（公開版と Lite）
   - 設定の窓の「地点のフォルダ: 選び直す…」からは、いつでも開ける（どの版でも）
   選ばないこともできる（CSV に、フォルダの各階層が Folder1, Folder2 … の列で出る） */
const STN = { busy: false, asked: {} };       // asked = この起動の間に、自動で出したプロジェクト（閉じ方によらず、もう自動では出さない）
const stnFmt = (key, o) => t(key).replace(/\{(\w+)\}/g, (_, k) => (o[k] != null ? o[k] : ''));
function stnMaybeAsk() {
  if (STN.busy || !S.project || !S.project.root || !S.stationAsk || isResearch() || S.project.kind === 'cross') return;
  if (STN.asked[S.project.dir] || !(S.facets && S.facets.total > 0) || modalOpen()) return;
  STN.asked[S.project.dir] = true;
  stationDialog(true);
}
function stnNames(lv) { return lv ? lv.names.join(', ') + (lv.n > lv.names.length ? ' …' : '') : ''; }
// 設定の窓の行: いまの決め方を 1 行で
function stnNowText(info) {
  if (!info || !info.example) return t('stn_no_videos');
  if (info.custom) return stnFmt('stn_now_custom', { pattern: info.pattern });
  if (!info.has_station || info.station_level == null) return t('stn_now_none');
  return stnFmt('stn_now_lv', { lv: info.station_level + 1, name: info.example.dirs[info.station_level] || '' });
}
async function stationDialog(auto) {
  if (STN.busy) return;
  STN.busy = true;
  let info;
  try { info = await api('/api/project/station'); } catch (e) { if (!auto) toast(e.message, 'error'); return; } finally { STN.busy = false; }
  if (!info.example || (auto && modalOpen())) { if (!auto) toast(t('stn_no_videos')); else if (S.project) delete STN.asked[S.project.dir]; return; }
  const dirs = info.example.dirs, lv = info.levels || [];
  let st = info.has_station ? info.station_level : null, se = info.season_level;
  if (!dirs.length) st = null;
  const seg = i => `<button type="button" class="stn-seg" data-lv="${i}" title="${esc(stnFmt('stn_level_names', { names: stnNames(lv[i]), n: lv[i] ? lv[i].n : 0 }))}"><b>${esc(dirs[i])}</b><small>${esc(stnFmt('stn_level_n', { n: lv[i] ? lv[i].n : 0 }))}</small><span class="stn-tag"></span></button>`;
  const m = showModal(`<h3>${esc(t('stn_title'))}</h3><div class="note">${esc(t('stn_help'))}</div>
    ${info.custom ? `<div class="callout warn">${esc(stnFmt('stn_custom', { pattern: info.pattern }))}</div>` : ''}
    <div class="stn-chain"><span class="stn-root" title="${esc(info.root || '')}">${esc(info.root_name || '')}<small>${esc(t('stn_root'))}</small></span>${dirs.map((d, i) => `<span class="stn-sep">›</span>${seg(i)}`).join('')}<span class="stn-sep">›</span><span class="stn-file">${esc(info.example.file)}</span></div>
    ${dirs.length ? '' : `<div class="callout warn">${esc(t('stn_flat'))}</div>`}
    <div class="form-row"><label>${esc(t('stn_station'))}</label><span id="stnNow"></span></div>
    <div class="form-row"><label>${esc(t('stn_season'))}</label><span style="display:flex;flex-direction:column;gap:4px"><select id="stnSeason"></select><span class="info">${esc(t('stn_season_help'))}</span></span></div>
    <div id="stnPrev"></div>
    <div class="modal-actions"><button id="mCancel">${esc(t(auto ? 'stn_later' : 'cancel'))}</button><button id="stnNo" title="${esc(t('stn_no_tip'))}">${esc(t('stn_no'))}</button><button id="mOk" class="primary">${esc(t('stn_ok'))}</button></div>`);
  const q = sel => m.querySelector(sel);
  let token = 0;
  const render = async () => {
    m.querySelectorAll('.stn-seg').forEach(b => {
      const i = parseInt(b.dataset.lv);
      b.classList.toggle('on', i === st); b.classList.toggle('on2', i === se);
      b.querySelector('.stn-tag').textContent = i === st ? t('stn_station') : (i === se ? t('stn_season_tag') : '');
    });
    q('#stnNow').innerHTML = st == null ? `<span class="info">${esc(t('stn_none'))}</span>` : `<b>${esc(stnFmt('stn_now', { name: dirs[st] }))}</b> <span class="info">${esc(stnFmt('stn_level_names', { names: stnNames(lv[st]), n: lv[st] ? lv[st].n : 0 }))}</span>`;
    q('#stnSeason').innerHTML = `<option value="">${esc(stnFmt('stn_season_none', { name: info.root_name || '' }))}</option>` + dirs.map((d, i) => (i === st ? '' : `<option value="${i}" ${i === se ? 'selected' : ''}>${esc(stnFmt('stn_now', { name: d }))}（${esc(stnNames(lv[i]))}）</option>`)).join('');
    q('#mOk').disabled = st == null;
    const my = ++token;
    try {
      const r = await api('/api/project/station/preview', { station_level: st, season_level: se });
      if (my !== token || !m.isConnected) return;
      q('#stnPrev').innerHTML = `<div class="note">${esc(stnFmt('stn_prev_sum', { st: r.n_stations, se: r.n_seasons }))}${r.n_no_station ? `　<span style="color:#b00020">${esc(stnFmt('stn_prev_miss', { n: r.n_no_station }))}</span>` : ''}</div>
        <div style="max-height:22vh;overflow:auto"><table class="tbl"><tr><th>${esc(t('stn_prev_folder'))}</th><th>${esc(t('stn_prev_n'))}</th><th>${esc(t('stn_station'))}</th><th>${esc(t('stn_season_tag'))}</th></tr>${r.rows.map(x => `<tr><td>${esc(x.folder || '/')}</td><td>${x.n}</td><td>${x.station ? `<b>${esc(x.station)}</b>` : '<span class="info">–</span>'}</td><td>${esc(x.season || '')}</td></tr>`).join('')}</table></div>`;
    } catch (e) { q('#stnPrev').innerHTML = `<div class="note">${esc(e.message)}</div>`; }
  };
  m.querySelectorAll('.stn-seg').forEach(b => { b.onclick = () => { const i = parseInt(b.dataset.lv); st = i; if (se === i) se = null; render(); }; });
  q('#stnSeason').onchange = () => { const v = q('#stnSeason').value; se = v === '' ? null : parseInt(v); render(); };
  const save = async (stLv, seLv) => {
    try {
      const r = await api('/api/project/station/pick', { station_level: stLv, season_level: seLv });
      closeModal();
      setSnapshot(r.state); S.stationAsk = false;
      toast(stLv == null ? t('stn_saved_none') : stnFmt('stn_saved', { n: r.n }));
      await refreshFacets(); await reloadList(true);
    } catch (e) { toast(e.message, 'error'); }
  };
  q('#mOk').onclick = () => save(st, se);
  q('#stnNo').onclick = () => save(null, se);
  q('#mCancel').onclick = closeModal;
  render();
}
