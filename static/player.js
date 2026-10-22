'use strict';
/* 再生（video 要素）。AVI などはサーバーが再生用 MP4 を作るので、できるまで待つ。速さ 0.5 / 1 / 2 / 4、コマ送り、ループ */

const P = { fps: 25, duration: 0, pollTimer: null, seeking: false, boxes: [], boxTimes: [], boxItem: null, raf: null };

/* いま使っている再生器: <video>（MP4 など）か、MJPEG の AVI をそのまま描く AviPlayer（avi.js。<video> と同じ顔） */
function player() { return (P.avi && P.avi.active) ? P.avi : $('#player'); }

function stopMedia() {
  clearTimeout(P.pollTimer); P.pollTimer = null;
  const v = $('#player');
  v.pause(); v.removeAttribute('src'); v.load();
  if (P.avi) P.avi.close();
  $('#photo').classList.add('hidden'); $('#photo').removeAttribute('src');
  v.classList.add('hidden');
  renderVideoBar();
}

async function showMedia(d, token) {
  const v = $('#player'), img = $('#photo');
  clearTimeout(P.pollTimer); P.pollTimer = null;
  v.pause();
  if (P.avi) P.avi.close();
  if (!d.exists) { stopMedia(); showOverlay(t('file_missing'), d.abs_path); return; }
  if (d.kind === 'image') {
    v.classList.add('hidden'); v.removeAttribute('src'); v.load();
    img.classList.remove('hidden'); img.src = `/api/items/${d.id}/image`;
    img.onload = () => { if (token === S.playToken) hideOverlay(); };
    img.onerror = () => { if (token === S.playToken) showOverlay(t('file_missing')); };
    P.fps = 0; P.duration = 0; renderVideoBar();
    return;
  }
  img.classList.add('hidden'); img.removeAttribute('src');
  P.fps = d.fps || 25; P.duration = d.duration || 0;
  const nextIds = await neighborIds(S.settings.prefetch_n ?? 3);
  if (token !== S.playToken) return;
  let res;
  try { res = await api(`/api/items/${d.id}/prepare`, { next_ids: nextIds }); } catch (e) { showOverlay(e.message); return; }
  if (token !== S.playToken) return;
  if (res.mjpeg && P.avi) {                       // MJPEG の AVI: 変換せずにそのまま
    v.classList.add('hidden'); v.removeAttribute('src'); v.load();
    const ok = await playAvi(d, token);
    if (ok || token !== S.playToken) return;
    try { res = await api(`/api/items/${d.id}/prepare`, { next_ids: nextIds, no_mjpeg: true }); } catch (e) { showOverlay(e.message); return; }   // 読めなければ従来の変換
    if (token !== S.playToken) return;
  }
  v.classList.remove('hidden');
  if (res.ready) { playSrc(d.id, token); return; }
  showOverlay(t('transcoding'), t('transcoding_sub'));
  const poll = async () => {
    if (token !== S.playToken) return;
    let r;
    try { r = await api(`/api/items/${d.id}/prepare`, { no_mjpeg: true }); } catch (e) { showOverlay(e.message); return; }
    if (token !== S.playToken) return;
    if (r.ready) playSrc(d.id, token);
    else if (r.transcoding) P.pollTimer = setTimeout(poll, 1000);
    else showOverlay(t('no_ffmpeg'));
  };
  P.pollTimer = setTimeout(poll, 1000);
}

async function playAvi(d, token) {
  const a = P.avi;
  showOverlayLater(t('loading'), '', 300);
  try {
    const info = await a.open(`/api/items/${d.id}/raw`, () => token === S.playToken,
      (n, total) => { if (token === S.playToken) showOverlay(t('loading'), `${(n / 1e6).toFixed(0)}${total ? ' / ' + (total / 1e6).toFixed(0) : ''} MB`); });
    if (token !== S.playToken) return true;
    P.fps = info.fps || P.fps; P.duration = a.duration || P.duration;
    a.playbackRate = S.settings.play_speed || 1; a.loop = !!$('#vbLoop').checked;
    hideOverlay(); renderVideoBar(); drawBoxes();
    a.play();
    return true;
  } catch (e) {
    if (token !== S.playToken) return true;
    console.warn('AVI direct play failed, falling back to transcode:', e.message);
    a.close();
    return false;
  }
}

function playSrc(id, token) {
  const v = $('#player');
  showOverlayLater(t('loading'), '', 600);
  v.src = `/api/items/${id}/video`;
  v.playbackRate = S.settings.play_speed || 1;
  v.loop = !!$('#vbLoop').checked;
  v.onloadedmetadata = () => { if (token !== S.playToken) return; P.duration = v.duration || P.duration; hideOverlay(); renderVideoBar(); v.play().catch(() => {}); };
  v.onerror = () => { if (token !== S.playToken) return; showOverlay(t('cannot_play'), v.error ? v.error.message : ''); };
  v.load();
}

function renderVideoBar() {
  const v = player();
  const has = !v.classList.contains('hidden') && v.src;
  $('#vbPlay').textContent = (has && !v.paused) ? '❚❚' : '▶';
  $('#vbPlay').disabled = !has; $('#vbSlider').disabled = !has;
  ['vbBack', 'vbFwd', 'vbBack10', 'vbFwd10'].forEach(id => { $('#' + id).disabled = !has; });
  if (has) {
    const cur = v.currentTime || 0, dur = v.duration || P.duration || 0;
    if (!P.seeking) $('#vbSlider').value = dur ? Math.round(1000 * cur / dur) : 0;
    const frame = P.fps ? Math.round(cur * P.fps) : 0;
    $('#vbInfo').textContent = `${fmtSec(cur)} / ${fmtSec(dur)}`;
    $('#vbPos').textContent = P.fps ? `f${frame}` : '';
  } else { $('#vbInfo').textContent = '0.00 / 0.00'; $('#vbPos').textContent = ''; }
  document.querySelectorAll('button.spd').forEach(b => b.classList.toggle('active', parseFloat(b.dataset.v) === (S.settings.play_speed || 1)));
  $('#vbLoop').checked = !!S.settings.loop;
}

function togglePlay() {
  const v = player();
  if (v.classList.contains('hidden') || !v.src) return;
  if (v.paused) v.play().catch(() => {}); else v.pause();
  renderVideoBar();
}
function stepFrame(n) {
  const v = player();
  if (v.classList.contains('hidden') || !v.src) return;
  v.pause();
  const dt = 1 / (P.fps || 25);
  const dur = v.duration || P.duration || 0;
  v.currentTime = Math.max(0, Math.min(dur ? dur - dt / 2 : 1e9, v.currentTime + n * dt));
  renderVideoBar();
}
function setSpeed(x) {
  S.settings.play_speed = x; player().playbackRate = x; renderVideoBar();
  api('/api/settings', { play_speed: x }).catch(() => {});
}
function cycleSpeed(dir) {
  const sp = [0.5, 1, 2, 4]; let i = sp.indexOf(S.settings.play_speed || 1); i = Math.max(0, Math.min(sp.length - 1, i + dir)); setSpeed(sp[i]);
}
function currentTimeSec() { const v = player(); return (!v.classList.contains('hidden') && v.src) ? v.currentTime : null; }

// ---------- 検出枠（MegaDetector の枠を動画・画像に重ねる） ----------
const BOX_COLORS = { animal: '#3ddc84', person: '#ffb020', vehicle: '#4ea1ff' };
async function loadBoxes(d) {
  P.boxes = []; P.boxTimes = []; P.boxItem = d.id;
  const wrap = $('#vbBoxesWrap');
  drawBoxes();
  if (!d.det || d.det_state !== 1) { wrap.classList.add('dim'); return; }
  wrap.classList.remove('dim');
  try {
    const r = await api(`/api/items/${d.id}/detections`);
    if (P.boxItem !== d.id) return;
    // 判定に使った閾値より弱い枠（0.1〜）は描かない（枠が見えるのに「動物なし」、を防ぐ）
    const ath = (d.det && d.det.animal_threshold) || (S.project && S.project.settings && S.project.settings.animal_threshold) || 0.2;
    const pth = (d.det && d.det.person_threshold) || (S.project && S.project.settings && S.project.settings.person_threshold) || 0.2;
    P.boxes = (r.boxes || []).filter(b => b.p >= (b.c === 'animal' ? ath : pth));
    P.boxTimes = [...new Set(P.boxes.map(b => b.t))].sort((a, b) => a - b);
    drawBoxes();
  } catch (e) { /* */ }
}
function mediaRect() {   // 表示されているメディアの位置（object-fit: contain の中身）
  const v = player(), img = $('#photo');
  const el = !v.classList.contains('hidden') ? v : (!img.classList.contains('hidden') ? img : null);
  if (!el) return null;
  const nw = el.videoWidth || el.naturalWidth || 0, nh = el.videoHeight || el.naturalHeight || 0;
  const W = el.clientWidth, H = el.clientHeight;
  if (!nw || !nh || !W || !H) return null;
  const s = Math.min(W / nw, H / nh), w = nw * s, h = nh * s;
  return { x: el.offsetLeft + (W - w) / 2, y: el.offsetTop + (H - h) / 2, w, h };
}
function boxesNow() {
  if (!P.boxes.length) return [];
  const v = player();
  if (v.classList.contains('hidden') || !v.src) return P.boxes;        // 静止画: 全部
  const cur = v.currentTime || 0;
  let best = null;                                                      // いちばん近い抜き出し時刻（0.6 秒まで）
  for (const t of P.boxTimes) { if (best === null || Math.abs(t - cur) < Math.abs(best - cur)) best = t; }
  if (best === null || Math.abs(best - cur) > 0.6) return [];
  return P.boxes.filter(b => b.t === best);
}
function drawBoxes() {
  const cv = $('#overlay'), wrap = $('#viewWrap');
  const W = wrap.clientWidth, H = wrap.clientHeight;
  if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  if (!$('#vbBoxes').checked || !P.boxes.length) return;
  const r = mediaRect();
  if (!r) return;
  ctx.font = '12px system-ui, sans-serif'; ctx.textBaseline = 'top'; ctx.lineWidth = 2;
  for (const b of boxesNow()) {
    const x = r.x + b.x * r.w, y = r.y + b.y * r.h, w = b.w * r.w, h = b.h * r.h;
    const col = BOX_COLORS[b.c] || '#fff';
    ctx.strokeStyle = col; ctx.globalAlpha = Math.max(0.35, Math.min(1, b.p + 0.3)); ctx.strokeRect(x, y, w, h);
    const label = `${t('det_' + b.c) || b.c} ${(100 * b.p).toFixed(0)}%` + (b.sp ? ` · ${spLabel(b.sp)} ${(100 * (b.sp_p || 0)).toFixed(0)}%` : '');
    const tw = ctx.measureText(label).width + 8;
    ctx.globalAlpha = 0.85; ctx.fillStyle = col; ctx.fillRect(x, Math.max(0, y - 16), tw, 16);
    ctx.fillStyle = '#111'; ctx.fillText(label, x + 4, Math.max(0, y - 16) + 2);
    ctx.globalAlpha = 1;
  }
}
function boxLoop() {
  cancelAnimationFrame(P.raf); P.raf = null;
  const v = player();
  if (!P.boxes.length || !$('#vbBoxes').checked) return;
  const step = () => { drawBoxes(); if (!v.paused && !v.ended) P.raf = requestAnimationFrame(step); else P.raf = null; };
  P.raf = requestAnimationFrame(step);
}

function wirePlayer() {
  const v = $('#player'), sl = $('#vbSlider');
  P.avi = (typeof AviPlayer === 'function') ? new AviPlayer($('#mjpeg')) : null;
  $('#vbPlay').onclick = togglePlay;
  $('#vbPrev').onclick = () => confirmOrStep(-1);
  $('#vbNext').onclick = () => confirmOrStep(1);
  $('#vbBack').onclick = () => stepFrame(-1); $('#vbFwd').onclick = () => stepFrame(1);
  $('#vbBack10').onclick = () => stepFrame(-10); $('#vbFwd10').onclick = () => stepFrame(10);
  for (const el of [v, P.avi].filter(Boolean)) {
    el.addEventListener('timeupdate', renderVideoBar);
    el.addEventListener('play', renderVideoBar); el.addEventListener('pause', renderVideoBar); el.addEventListener('ended', renderVideoBar);
    ['timeupdate', 'seeked', 'loadedmetadata', 'pause', 'ended'].forEach(ev => el.addEventListener(ev, drawBoxes));
    el.addEventListener('play', boxLoop);
  }
  $('#mjpeg').addEventListener('click', togglePlay);
  $('#photo').addEventListener('load', drawBoxes);
  window.addEventListener('resize', drawBoxes);
  $('#vbBoxes').checked = S.settings.show_boxes !== false;
  $('#vbBoxes').addEventListener('change', () => { S.settings.show_boxes = $('#vbBoxes').checked; drawBoxes(); boxLoop(); api('/api/settings', { show_boxes: S.settings.show_boxes }).catch(() => {}); });
  v.addEventListener('click', togglePlay);
  sl.addEventListener('input', () => { P.seeking = true; const pl = player(); const dur = pl.duration || P.duration || 0; if (dur) { pl.currentTime = dur * sl.value / 1000; $('#vbInfo').textContent = `${fmtSec(pl.currentTime)} / ${fmtSec(dur)}`; } });
  sl.addEventListener('change', () => { P.seeking = false; });
  document.querySelectorAll('button.spd').forEach(b => { b.onclick = () => setSpeed(parseFloat(b.dataset.v)); });
  $('#vbLoop').addEventListener('change', () => { S.settings.loop = $('#vbLoop').checked; v.loop = S.settings.loop; if (P.avi) P.avi.loop = S.settings.loop; api('/api/settings', { loop: S.settings.loop }).catch(() => {}); });
}
