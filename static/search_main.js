'use strict';
/* 高度な検索の本体側（index.html で app.js などのあとに読む）: 「高度な検索」のボタンで別窓（/search）を開き、別窓と絞り込みをやりとりする。

   絞り込みの持ち主は本体の S.filter。別窓は S.filter をまるごと受け取って大きな画面で編集し、変えるたびにまるごと送り返す（BroadcastChannel）。
   簡易の絞り込みの欄にない条件は、簡易の鍵のうち欄のないもの（日付・種類・時刻の修正・ファイル・フォルダだけ）と S.filter.adv（JSON の文字列）に入る。
   一覧・件数・一括の操作・書き出しは S.filter をそのままサーバーに渡すので、どれも同じ条件で動く。
   やりとり（どれも { t, … }）: 別窓 → 本体 hello（いまの絞り込みを教えて）/ apply（この絞り込みにして）。本体 → 別窓 state（いまの絞り込み）/ bye（閉じる）。
   本体の窓には印（mid）があり、別窓は自分を開いた本体だけを相手にする（to = その印）。

   app.js から呼ばれる関数（どれも typeof で確かめてから呼ぶ 1 行の差し込み）:
     advOnReload()     reloadList の先頭。別窓に、いまの絞り込みを知らせる
     advFilterNames(f) activeFilterNames の最後。簡易の欄にない条件の名前（「絞り込み中 n ✕」と 0 件のときの説明に出る）
     advReset()        clearFilters と showBatch。簡易の欄にない条件を外す */

const ADV = { chan: null, win: null, mid: Math.random().toString(36).slice(2), rev: 0, cur: null };

function advCtx() { return { sp: spLabel, fd: key => (S.fields.fields || []).find(f => f.key === key) }; }
function advKey() { return advCanon(S.filter) + '|' + (S.project ? S.project.dir : ''); }
/* 別窓へ: いまの絞り込み。rev は本体の側で絞り込みが変わるたびに上がる（別窓は rev が変わったときだけ自分の画面を合わせ直す） */
function advPost() {
  if (!ADV.chan) return;
  try { ADV.chan.postMessage({ t: 'state', mid: ADV.mid, rev: ADV.rev, filter: Object.assign({}, S.filter), project: S.project && S.project.root ? S.project.dir : '', lang: LANG }); } catch (e) { console.warn(e); }
}
function advMark() { const b = document.getElementById('btnAdv'); if (b) b.classList.toggle('active', advFilterNames(S.filter).length > 0); }
function advOnReload() {
  try {
    if (advNormalize(S.filter)) saveUi();        // 簡易の欄で地点・調査回・種・枠の数を選んだら、高度な検索の同じ項目は外す（1 つの項目に条件は 1 つ）
    const key = advKey();
    if (key !== ADV.cur) { ADV.cur = key; ADV.rev++; }
    advPost(); advMark();
  } catch (e) { console.warn(e); }
}
function advFilterNames(f) {
  try { return advDescribe(f || S.filter, advCtx()).filter(d => d.own).map(d => d.text); } catch (e) { console.warn(e); return []; }
}
function advReset() { Object.assign(S.filter, { date_from: '', date_to: '', kind: '', corrected: '', missing: '', include_missing: '', folder: '', adv: '' }); }
/* 別窓から: 絞り込みをまるごと置き換えて一覧を読み直す */
function advApply(f) {
  if (!S.project || !S.project.root || !f || typeof f !== 'object') return;
  Object.keys(S.filter).concat(ADV_KEYS).forEach(k => { S.filter[k] = advHas(f[k]) ? f[k] : ''; });
  ADV.cur = advKey();                            // 別窓が変えた分では rev を上げない（別窓の入力を本体が上書きしないように）
  $('#fText').value = S.filter.text || '';
  renderFilters(); saveUi();
  reloadList().catch(e => toast(e.message, 'error'));
}
function advOpen() {
  if (ADV.win && !ADV.win.closed) { try { ADV.win.focus(); return; } catch (e) { /* 開き直す */ } }
  let pos = {};
  try { pos = JSON.parse(localStorage.getItem('tn_adv_win') || '{}') || {}; } catch (e) { pos = {}; }
  const w = Math.max(560, Math.min(pos.w || 900, screen.availWidth || 1600)), h = Math.max(480, Math.min(pos.h || 920, screen.availHeight || 1000));
  const x = pos.x != null ? pos.x : Math.max(0, window.screenX + window.outerWidth - w - 30), y = pos.y != null ? pos.y : Math.max(0, window.screenY + 30);
  // 窓の名前はポートごと（別のポートで動く TrapNote の別窓と混ざらないように）。# に、この本体の窓の印を付ける（別窓はこの窓だけを相手にする）
  const win = window.open('/search#' + ADV.mid, 'trapnote_adv_' + (location.port || '0'), `popup=yes,width=${w},height=${h},left=${x},top=${y}`);
  if (!win) { toast(t('adv_popup_blocked'), 'error'); return; }
  ADV.win = win;
  try { win.focus(); } catch (e) { /* */ }
}
(function advInit() {
  const b = document.getElementById('btnAdv');
  if (b) b.onclick = () => (S.project && S.project.root ? advOpen() : toast(t('no_project'), 'error'));
  if (typeof BroadcastChannel !== 'function') return;
  ADV.chan = new BroadcastChannel(ADV_CHANNEL);
  ADV.chan.onmessage = ev => {
    const m = ev.data || {};
    if (m.to && m.to !== ADV.mid) return;      // ほかの本体の窓あて
    if (m.t === 'hello') advPost();
    else if (m.t === 'apply') advApply(m.filter);
  };
  window.addEventListener('pagehide', () => { try { ADV.chan.postMessage({ t: 'bye', mid: ADV.mid }); } catch (e) { /* */ } });
})();
