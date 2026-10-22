'use strict';
/* 検出枠の編集: 「枠を編集」で、いま見えている枠（静止画は全部、動画はその時刻の枠）を
   ドラッグで動かす・角や辺で大きさを変える・空いている所をドラッグして新しい枠を作る・Delete で消す。
   保存すると detections 表を置き換え、種判別と特徴量は付け直しが要る状態（cls_state = 0）になる
   （自動判定の「枠はいまあるものを使う」で、編集した画像だけ付け直せる） */

const BE = { on: false, sel: -1, drag: null, orig: null, cat: 'animal', hover: null };
const HANDLE = 7;   // 角・辺のつまみの半径（px）

function boxEditToggle() { if (BE.on) boxEditEnd(false); else boxEditStart(); }
function boxEditStart() {
  if (!S.cur || !S.cur.det) { toast(t('box_edit_need'), 'error'); return; }
  const pl = player(); if (pl && !pl.paused && pl.pause) pl.pause();
  BE.on = true; BE.sel = -1; BE.drag = null;
  BE.orig = JSON.parse(JSON.stringify(P.boxes));
  $('#vbBoxes').checked = true;
  $('#overlay').classList.add('editing');
  $('#boxTools').classList.remove('hidden');
  $('#vbEdit').classList.add('active');
  drawBoxes();
}
function boxEditEnd(saved) {
  if (!saved && BE.orig) P.boxes = BE.orig;
  BE.on = false; BE.sel = -1; BE.drag = null; BE.orig = null;
  $('#overlay').classList.remove('editing');
  $('#boxTools').classList.add('hidden');
  $('#vbEdit').classList.remove('active');
  P.boxTimes = [...new Set(P.boxes.map(b => b.t))].sort((a, b) => a - b);
  drawBoxes();
}
function boxEditCurrentTime() {   // 新しい枠を付ける時刻（静止画は 0、動画はいま見ている枠の時刻か再生位置）
  const v = player();
  if (!v || v.classList.contains('hidden') || !v.src) return 0;
  const now = boxesNow();
  if (now.length) return now[0].t;
  return Math.round((v.currentTime || 0) * 100) / 100;
}
function boxEditVisible() { return BE.on ? boxesNow() : []; }
function boxEditPos(e) {   // マウス → 画像の中の 0〜1 の座標
  const r = mediaRect(); if (!r) return null;
  const cv = $('#overlay'), rect = cv.getBoundingClientRect();
  const px = e.clientX - rect.left, py = e.clientY - rect.top;
  return { nx: (px - r.x) / r.w, ny: (py - r.y) / r.h, px, py, r };
}
function boxEditHandles(b, r) {   // 8 つのつまみの画面位置
  const x = r.x + b.x * r.w, y = r.y + b.y * r.h, w = b.w * r.w, h = b.h * r.h;
  return [['nw', x, y], ['n', x + w / 2, y], ['ne', x + w, y], ['e', x + w, y + h / 2], ['se', x + w, y + h], ['s', x + w / 2, y + h], ['sw', x, y + h], ['w', x, y + h / 2]];
}
function boxEditHit(pos) {   // 何を掴んだか: {kind: 'handle'|'box'|null, box, handle}
  const vis = boxEditVisible();
  if (BE.sel >= 0 && P.boxes[BE.sel]) {
    for (const [name, hx, hy] of boxEditHandles(P.boxes[BE.sel], pos.r)) {
      if (Math.abs(pos.px - hx) <= HANDLE && Math.abs(pos.py - hy) <= HANDLE) return { kind: 'handle', box: P.boxes[BE.sel], handle: name };
    }
  }
  let best = null;
  for (const b of vis) {
    if (pos.nx >= b.x && pos.nx <= b.x + b.w && pos.ny >= b.y && pos.ny <= b.y + b.h) {
      if (!best || b.w * b.h < best.w * best.h) best = b;      // 重なっていれば小さい方
    }
  }
  return best ? { kind: 'box', box: best } : { kind: null };
}
function boxEditCursor(hit) {
  if (!hit || !hit.kind) return 'crosshair';
  if (hit.kind === 'box') return 'move';
  return { n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize', nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize' }[hit.handle] || 'move';
}
function boxEditDown(e) {
  if (!BE.on || e.button !== 0) return;
  const pos = boxEditPos(e); if (!pos) return;
  e.preventDefault();
  const hit = boxEditHit(pos);
  if (hit.kind === 'handle') {
    BE.drag = { kind: 'resize', box: hit.box, handle: hit.handle, start: { ...hit.box }, nx: pos.nx, ny: pos.ny };
  } else if (hit.kind === 'box') {
    BE.sel = P.boxes.indexOf(hit.box);
    BE.drag = { kind: 'move', box: hit.box, start: { ...hit.box }, nx: pos.nx, ny: pos.ny, moved: false };
  } else {
    const nb = { t: boxEditCurrentTime(), c: BE.cat, p: 1.0, x: Math.min(Math.max(pos.nx, 0), 1), y: Math.min(Math.max(pos.ny, 0), 1), w: 0, h: 0, sp: null, sp_p: null, edited: true };
    P.boxes.push(nb); BE.sel = P.boxes.length - 1;
    BE.drag = { kind: 'new', box: nb, ax: nb.x, ay: nb.y };
  }
  drawBoxes();
}
function boxEditMove(e) {
  if (!BE.on) return;
  const pos = boxEditPos(e); if (!pos) return;
  const cv = $('#overlay');
  if (!BE.drag) { cv.style.cursor = boxEditCursor(boxEditHit(pos)); return; }
  const d = BE.drag, b = d.box, cl = v => Math.min(Math.max(v, 0), 1);
  if (d.kind === 'move') {
    const dx = pos.nx - d.nx, dy = pos.ny - d.ny;
    b.x = cl(d.start.x + dx); b.y = cl(d.start.y + dy);
    b.x = Math.min(b.x, 1 - b.w); b.y = Math.min(b.y, 1 - b.h);
    d.moved = d.moved || Math.abs(dx) > 0.002 || Math.abs(dy) > 0.002;
  } else if (d.kind === 'new') {
    const x0 = Math.min(d.ax, cl(pos.nx)), y0 = Math.min(d.ay, cl(pos.ny));
    b.x = x0; b.y = y0; b.w = Math.abs(cl(pos.nx) - d.ax); b.h = Math.abs(cl(pos.ny) - d.ay);
  } else if (d.kind === 'resize') {
    const s = d.start, h = d.handle;
    let x0 = s.x, y0 = s.y, x1 = s.x + s.w, y1 = s.y + s.h;
    if (h.includes('w')) x0 = cl(pos.nx); if (h.includes('e')) x1 = cl(pos.nx);
    if (h.includes('n')) y0 = cl(pos.ny); if (h.includes('s')) y1 = cl(pos.ny);
    b.x = Math.min(x0, x1); b.y = Math.min(y0, y1); b.w = Math.abs(x1 - x0); b.h = Math.abs(y1 - y0);
  }
  b.edited = true;
  drawBoxes();
}
function boxEditUp(e) {
  if (!BE.on || !BE.drag) return;
  const d = BE.drag; BE.drag = null;
  const r = mediaRect();
  if (d.kind === 'new' && r && (d.box.w * r.w < 4 || d.box.h * r.h < 4)) {       // 小さすぎる = クリックだけ → 作らない（選択解除）
    P.boxes.splice(P.boxes.indexOf(d.box), 1); BE.sel = -1;
  }
  drawBoxes();
}
function boxEditDelete() {
  if (!BE.on || BE.sel < 0 || !P.boxes[BE.sel]) return;
  P.boxes.splice(BE.sel, 1); BE.sel = -1; drawBoxes();
}
function boxEditClearHere() {   // いま見えている枠を全部消す
  if (!BE.on) return;
  const vis = new Set(boxEditVisible());
  P.boxes = P.boxes.filter(b => !vis.has(b)); BE.sel = -1; drawBoxes();
}
function boxEditDraw(ctx, r) {   // drawBoxes のあとに呼ばれる: 編集中の選択枠のつまみ
  if (!BE.on) return;
  ctx.save();
  ctx.globalAlpha = 1;
  if (BE.sel >= 0 && P.boxes[BE.sel]) {
    const b = P.boxes[BE.sel];
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.strokeRect(r.x + b.x * r.w, r.y + b.y * r.h, b.w * r.w, b.h * r.h);
    ctx.setLineDash([]);
    for (const [, hx, hy] of boxEditHandles(b, r)) { ctx.fillStyle = '#fff'; ctx.fillRect(hx - 4, hy - 4, 8, 8); ctx.strokeStyle = '#333'; ctx.lineWidth = 1; ctx.strokeRect(hx - 4, hy - 4, 8, 8); }
  }
  ctx.restore();
}
async function boxEditSave() {
  if (!BE.on || !S.cur) return;
  const boxes = P.boxes.filter(b => b.w > 0 && b.h > 0).map(b => ({ t: b.t, c: b.c, p: b.p == null ? 1.0 : b.p, x: +b.x.toFixed(4), y: +b.y.toFixed(4), w: +b.w.toFixed(4), h: +b.h.toFixed(4), sp: b.edited ? null : (b.sp || null), sp_p: b.edited ? null : (b.sp_p == null ? null : b.sp_p) }));
  try {
    const r = await api(`/api/items/${S.cur.id}/boxes`, { boxes });
    S.cur.det = r.det; S.cur.det_state = 1; S.cur.cls = null;
    boxEditEnd(true);
    toast(`${t('box_saved')} (${r.n})`);
    loadBoxes(S.cur);
    if (typeof renderJudgeBoxes === 'function') renderJudgeBoxes(S.cur);
    patchRow({ id: S.cur.id });
  } catch (e) { toast(e.message, 'error'); }
}
function boxEditKey(e) {
  if (!BE.on || inInput(e)) return;
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); e.stopPropagation(); boxEditDelete(); }
  else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); boxEditEnd(false); }
  else if (e.key === 'Enter' && !e.ctrlKey) { e.preventDefault(); e.stopPropagation(); boxEditSave(); }
}
function wireBoxEdit() {
  const cv = $('#overlay');
  cv.addEventListener('mousedown', boxEditDown);
  window.addEventListener('mousemove', boxEditMove);
  window.addEventListener('mouseup', boxEditUp);
  document.addEventListener('keydown', boxEditKey, true);
  $('#vbEdit').onclick = boxEditToggle;
  $('#beCat').addEventListener('change', () => { BE.cat = $('#beCat').value; if (BE.sel >= 0 && P.boxes[BE.sel]) { P.boxes[BE.sel].c = BE.cat; P.boxes[BE.sel].edited = true; drawBoxes(); } });
  $('#beDel').onclick = boxEditDelete;
  $('#beClear').onclick = boxEditClearHere;
  $('#beSave').onclick = boxEditSave;
  $('#beCancel').onclick = () => boxEditEnd(false);
}
