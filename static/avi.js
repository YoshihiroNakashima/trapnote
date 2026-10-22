'use strict';
/* MJPEG の AVI を変換せずにそのまま再生する。
   カメラトラップの AVI はたいてい MJPEG（1 コマ = 1 枚の JPEG）なので、RIFF を読んでコマの JPEG を取り出し、ブラウザの JPEG デコーダで canvas に描く。
   ffmpeg の変換（CPU で数秒〜十数秒）が要らず、ファイルを読み終えた瞬間から再生・コマ送り・シークができる。音は出ない。
   <video> と同じ顔（currentTime, duration, paused, play(), pause(), playbackRate, loop, イベント）を持ち、player.js は <video> と同じように扱う。
   JPEG にハフマン表（DHT）がない「AVI の MJPEG」もある（古いカメラ）。その場合は標準の表を差し込む（ffmpeg と同じ処置） */

const DHT_STD = Uint8Array.from(('ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d0102030004110512213141061351' +
  '6107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a' +
  '838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa' +
  'ffc4001f0100030101010101010101010000000000000102030405060708090a0bffc400b51100020102040403040705040400010277000102031104052131061241510761711322328108' +
  '144291a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748494a535455565758595a636465666768696a737475767778797a828384858687' +
  '88898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa').match(/../g).map(h => parseInt(h, 16)));

function parseAvi(buf) {
  const dv = new DataView(buf), u8 = new Uint8Array(buf), N = buf.byteLength;
  const fourcc = o => String.fromCharCode(u8[o], u8[o + 1], u8[o + 2], u8[o + 3]);
  if (N < 12 || fourcc(0) !== 'RIFF' || fourcc(8) !== 'AVI ') throw new Error('not an AVI');
  const info = { fps: 0, usec: 0, width: 0, height: 0, handler: '', compression: '', frames: [] };
  let streams = 0, vStream = -1;
  const walk = (start, end, listType) => {
    let o = start;
    while (o + 8 <= end) {
      const id = fourcc(o), size = dv.getUint32(o + 4, true), data = o + 8;
      if (data + size > N + 1) break;
      if (id === 'LIST') {
        const type = fourcc(data);
        if (type === 'hdrl' || type === 'strl' || type === 'movi' || type === 'rec ') walk(data + 4, Math.min(data + size, N), type);
      } else if (id === 'avih') {
        info.usec = dv.getUint32(data, true); info.width = dv.getUint32(data + 32, true); info.height = dv.getUint32(data + 36, true);
      } else if (id === 'strh' && listType === 'strl') {
        if (fourcc(data) === 'vids' && vStream < 0) {
          vStream = streams; info.handler = fourcc(data + 4);
          const scale = dv.getUint32(data + 20, true), rate = dv.getUint32(data + 24, true);
          if (scale && rate) info.fps = rate / scale;
        }
        streams++;
      } else if (id === 'strf' && listType === 'strl' && streams - 1 === vStream && size >= 20) {
        info.compression = fourcc(data + 16);
      } else if ((listType === 'movi' || listType === 'rec ') && size > 0) {
        const c = id.slice(2);
        if ((c === 'dc' || c === 'db') && parseInt(id.slice(0, 2), 10) === vStream) info.frames.push({ off: data, len: Math.min(size, N - data) });
      }
      o = data + size + (size & 1);
    }
  };
  let riff = 0;
  while (riff + 12 <= N && fourcc(riff) === 'RIFF') {        // 1 GB を超える AVI は RIFF AVIX が続く（OpenDML）
    const sz = dv.getUint32(riff + 4, true);
    walk(riff + 12, Math.min(riff + 8 + sz, N), '');
    riff += 8 + sz + (sz & 1);
  }
  if (!info.fps) info.fps = info.usec ? 1e6 / info.usec : 30;
  if (!info.frames.length) throw new Error('no video frames');
  const f0 = info.frames[0];
  if (!(u8[f0.off] === 0xFF && u8[f0.off + 1] === 0xD8)) throw new Error('not MJPEG: ' + (info.compression || info.handler));
  return info;
}

function fixJpeg(u8) {   // DHT がなければ標準の表を SOS の前に入れる
  let i = 2, sos = -1;
  while (i + 4 <= u8.length) {
    if (u8[i] !== 0xFF) { i++; continue; }
    const m = u8[i + 1];
    if (m === 0xFF) { i++; continue; }
    if (m === 0xD8 || m === 0x01 || (m >= 0xD0 && m <= 0xD7)) { i += 2; continue; }
    if (m === 0xC4) return u8;
    if (m === 0xDA) { sos = i; break; }
    i += 2 + ((u8[i + 2] << 8) | u8[i + 3]);
  }
  if (sos < 0) return u8;
  const out = new Uint8Array(u8.length + DHT_STD.length);
  out.set(u8.subarray(0, sos), 0); out.set(DHT_STD, sos); out.set(u8.subarray(sos), sos + DHT_STD.length);
  return out;
}

class AviPlayer extends EventTarget {
  constructor(canvas) {
    super();
    this.canvas = canvas; this.ctx = canvas.getContext('2d');
    this.active = false; this.src = ''; this.error = null;
    this.buf = null; this.frames = []; this.fps = 30; this.width = 0; this.height = 0;
    this.index = -1; this._paused = true; this._ended = false; this._rate = 1; this.loop = false;
    this._t0 = 0; this._raf = null; this._cache = new Map(); this._token = 0; this._lastTick = 0; this._abort = null;
    this.bytesLoaded = 0; this.bytesTotal = 0;
  }
  // ---- <video> と同じ読み取り ----
  get paused() { return this._paused; }
  get ended() { return this._ended; }
  get duration() { return this.frames.length / this.fps; }
  get currentTime() { return Math.max(0, this.index) / this.fps; }
  set currentTime(t) { this.seekFrame(Math.round(t * this.fps)); }
  get playbackRate() { return this._rate; }
  set playbackRate(r) { const t = this.currentTime; this._rate = r || 1; if (!this._paused) this._t0 = performance.now() - t / this._rate * 1000; }
  get videoWidth() { return this.width; }
  get videoHeight() { return this.height; }
  get clientWidth() { return this.canvas.clientWidth; }
  get clientHeight() { return this.canvas.clientHeight; }
  get offsetLeft() { return this.canvas.offsetLeft; }
  get offsetTop() { return this.canvas.offsetTop; }
  get classList() { return this.canvas.classList; }
  removeAttribute() {}
  load() {}

  async open(url, isCurrent, onProgress) {
    this.close();
    const token = ++this._token;
    this.active = true; this.src = url; this.canvas.classList.remove('hidden');
    const ac = new AbortController(); this._abort = ac;
    const resp = await fetch(url, { signal: ac.signal });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const total = parseInt(resp.headers.get('Content-Length') || '0', 10);
    if (total > 1.5e9) { ac.abort(); throw new Error('too large for in-browser playback'); }   // 1.5 GB を超えるものは従来の変換で
    this.bytesTotal = total; this.bytesLoaded = 0;
    const reader = resp.body.getReader();
    const chunks = []; let loaded = 0, last = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value); loaded += value.length; this.bytesLoaded = loaded;
      if (onProgress && performance.now() - last > 150) { last = performance.now(); onProgress(loaded, total); }
      if (token !== this._token || !isCurrent()) { ac.abort(); throw new Error('cancelled'); }
    }
    const buf = new Uint8Array(loaded); let o = 0;
    for (const c of chunks) { buf.set(c, o); o += c.length; }
    if (token !== this._token || !isCurrent()) throw new Error('cancelled');
    const info = parseAvi(buf.buffer);
    this.buf = buf; this.frames = info.frames; this.fps = info.fps || 30; this.width = info.width; this.height = info.height;
    this.index = 0; this._paused = true; this._ended = false;
    await this._show(0);
    if (token !== this._token) throw new Error('cancelled');
    this.dispatchEvent(new Event('loadedmetadata'));
    this.dispatchEvent(new Event('timeupdate'));
    return info;
  }
  close() {
    this.pause();
    this._token++;
    if (this._abort) { try { this._abort.abort(); } catch (e) { /* */ } this._abort = null; }
    for (const p of this._cache.values()) p.then(b => b && b.close && b.close()).catch(() => {});
    this._cache.clear();
    this.buf = null; this.frames = []; this.index = -1; this.src = ''; this.active = false; this._ended = false;
    this.canvas.classList.add('hidden');
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }
  play() {
    if (!this.frames.length) return Promise.resolve();
    if (this._ended || this.index >= this.frames.length - 1) { this.index = 0; this._ended = false; }
    if (!this._paused) return Promise.resolve();
    this._paused = false;
    this._t0 = performance.now() - this.currentTime / this._rate * 1000;
    this.dispatchEvent(new Event('play'));
    this._loop();
    return Promise.resolve();
  }
  pause() {
    if (this._paused) return;
    this._paused = true;
    cancelAnimationFrame(this._raf); this._raf = null;
    this.dispatchEvent(new Event('pause'));
  }
  seekFrame(i) {
    if (!this.frames.length) return;
    i = Math.max(0, Math.min(this.frames.length - 1, i | 0));
    this.index = i; this._ended = false;
    if (!this._paused) this._t0 = performance.now() - this.currentTime / this._rate * 1000;
    this._show(i).then(() => { this.dispatchEvent(new Event('seeked')); this.dispatchEvent(new Event('timeupdate')); });
  }
  _loop() {
    const step = () => {
      if (this._paused) return;
      const el = (performance.now() - this._t0) / 1000 * this._rate;
      let i = Math.floor(el * this.fps);
      if (i >= this.frames.length) {
        if (this.loop) { this._t0 = performance.now(); i = 0; }
        else {
          this.index = this.frames.length - 1; this._show(this.index);
          this._paused = true; this._ended = true; this._raf = null;
          this.dispatchEvent(new Event('timeupdate')); this.dispatchEvent(new Event('pause')); this.dispatchEvent(new Event('ended'));
          return;
        }
      }
      if (i !== this.index) { this.index = i; this._show(i); this._prefetch(i); }
      if (performance.now() - this._lastTick > 100) { this._lastTick = performance.now(); this.dispatchEvent(new Event('timeupdate')); }
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }
  _decode(i) {
    let p = this._cache.get(i);
    if (p) return p;
    const f = this.frames[i];
    const u8 = fixJpeg(this.buf.subarray(f.off, f.off + f.len));
    p = createImageBitmap(new Blob([u8], { type: 'image/jpeg' })).catch(() => null);
    this._cache.set(i, p);
    if (this._cache.size > 24) {           // 遠いコマは捨てる
      for (const k of this._cache.keys()) { if (Math.abs(k - i) > 12) { this._cache.get(k).then(b => b && b.close && b.close()).catch(() => {}); this._cache.delete(k); } }
    }
    return p;
  }
  _show(i) {
    const token = this._token;
    return this._decode(i).then(bmp => {
      if (!bmp || token !== this._token || this.index !== i) return;
      if (this.canvas.width !== bmp.width || this.canvas.height !== bmp.height) { this.canvas.width = bmp.width; this.canvas.height = bmp.height; this.width = bmp.width; this.height = bmp.height; }
      this.ctx.drawImage(bmp, 0, 0);
    });
  }
  _prefetch(i) { for (let k = i + 1; k <= i + 6 && k < this.frames.length; k++) this._decode(k); }
}
