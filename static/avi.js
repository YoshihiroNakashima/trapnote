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

function parseAvi(buf, limit) {
  // limit: ここまで読めている（途中まで届いたファイルでも、届いた分のコマを取り出す。LIST は大きさが足りなくても中を歩く）
  const dv = new DataView(buf), u8 = new Uint8Array(buf), N = Math.min(limit || buf.byteLength, buf.byteLength);
  const fourcc = o => String.fromCharCode(u8[o], u8[o + 1], u8[o + 2], u8[o + 3]);
  if (N < 12 || fourcc(0) !== 'RIFF' || fourcc(8) !== 'AVI ') throw new Error('not an AVI');
  const info = { fps: 0, usec: 0, width: 0, height: 0, handler: '', compression: '', frames: [], totalFrames: 0, partial: N < buf.byteLength };
  let streams = 0, vStream = -1;
  const walk = (start, end, listType) => {
    let o = start;
    while (o + 8 <= end) {
      const id = fourcc(o), size = dv.getUint32(o + 4, true), data = o + 8;
      if (id === 'LIST') {
        if (data + 4 > N) break;
        const type = fourcc(data);
        if (type === 'hdrl' || type === 'strl' || type === 'movi' || type === 'rec ') walk(data + 4, Math.min(data + size, N), type);
      } else if (data + size > N) {
        break;                                           // まだ届いていない（または壊れている）チャンク
      } else if (id === 'avih') {
        info.usec = dv.getUint32(data, true); info.totalFrames = dv.getUint32(data + 16, true); info.width = dv.getUint32(data + 32, true); info.height = dv.getUint32(data + 36, true);
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
        if ((c === 'dc' || c === 'db') && parseInt(id.slice(0, 2), 10) === vStream) info.frames.push({ off: data, len: size });
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

class AviFetch {
  /* AVI を読み込む。先読み（次の動画を見ている間に読んでおく）にも使う。
     大きさが分かれば最初から 1 つのバッファに入れ、届いた分だけで parseAvi できる（届いたコマから表示・再生を始める） */
  constructor(url) {
    this.url = url; this.buf = null; this.loaded = 0; this.total = 0; this.done = false; this.error = null;
    this._chunks = []; this._listeners = new Set(); this._ctrl = new AbortController(); this.aborted = false;
    this.promise = this._run();
  }
  onChunk(fn) { this._listeners.add(fn); return () => this._listeners.delete(fn); }
  abort() { this.aborted = true; try { this._ctrl.abort(); } catch (e) { /* */ } }
  async _run() {
    const resp = await fetch(this.url, { signal: this._ctrl.signal });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const total = parseInt(resp.headers.get('Content-Length') || '0', 10);
    if (total > 1.5e9) { this.abort(); throw new Error('too large for in-browser playback'); }   // 1.5 GB を超えるものは従来の変換で
    this.total = total;
    if (total > 0) this.buf = new Uint8Array(total);
    const reader = resp.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (this.buf) {
        if (this.loaded + value.length > this.buf.length) { const nb = new Uint8Array(this.loaded + value.length); nb.set(this.buf.subarray(0, this.loaded)); this.buf = nb; }
        this.buf.set(value, this.loaded);
      } else this._chunks.push(value);
      this.loaded += value.length;
      for (const fn of this._listeners) { try { fn(this); } catch (e) { /* */ } }
    }
    if (!this.buf) {
      this.buf = new Uint8Array(this.loaded); let o = 0;
      for (const c of this._chunks) { this.buf.set(c, o); o += c.length; }
      this._chunks = [];
    } else if (this.loaded < this.buf.length) this.buf = this.buf.subarray(0, this.loaded);
    this.done = true;
    for (const fn of this._listeners) { try { fn(this); } catch (e) { /* */ } }
    return this.buf;
  }
}

class AviPlayer extends EventTarget {
  constructor(canvas) {
    super();
    this.canvas = canvas; this.ctx = canvas.getContext('2d');
    this.active = false; this.src = ''; this.error = null;
    this.buf = null; this.frames = []; this.fps = 30; this.width = 0; this.height = 0;
    this.index = -1; this._paused = true; this._ended = false; this._rate = 1; this.loop = false;
    this._t0 = 0; this._raf = null; this._cache = new Map(); this._token = 0; this._lastTick = 0; this._abort = null;
    this.bytesLoaded = 0; this.bytesTotal = 0; this._fetch = null; this.loading = false; this.totalFrames = 0;
  }
  // ---- <video> と同じ読み取り ----
  get paused() { return this._paused; }
  get ended() { return this._ended; }
  get duration() { return (this.loading && this.totalFrames > this.frames.length ? this.totalFrames : this.frames.length) / this.fps; }
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

  async open(url, isCurrent, onProgress, pre) {
    /* pre: 先読み済み（または先読み中）の AviFetch。なければここで読む。最初のコマが届いた時点で戻り（loadedmetadata）、残りは裏で読み続ける。
       読み込みが終わる前に再生を始めると、届いているコマの最後で待つ（止まって見える）*/
    this.close();
    const token = ++this._token;
    this.active = true; this.src = url; this.canvas.classList.remove('hidden');
    const fx = pre || new AviFetch(url);
    this._fetch = fx; this.loading = true; this.totalFrames = 0;
    this.bytesTotal = fx.total; this.bytesLoaded = fx.loaded;
    let last = 0, info = null, shown = false;
    const parseNow = () => {                       // 届いた分で parse（LIST の大きさが足りなくても中を歩く）
      if (!fx.buf || fx.loaded < 65536) return null;
      try { return parseAvi(fx.buf.buffer, fx.loaded); } catch (e) { return null; }
    };
    const apply = inf => {
      this.buf = fx.buf; this.frames = inf.frames; this.fps = inf.fps || 30; this.width = inf.width || this.width; this.height = inf.height || this.height;
      this.totalFrames = inf.totalFrames || 0;
    };
    let unsub = null;
    try {
      if (!fx.done) {
        await new Promise((resolve, reject) => {
          const check = f => {
            if (token !== this._token || !isCurrent()) { reject(new Error('cancelled')); return; }
            this.bytesLoaded = f.loaded; this.bytesTotal = f.total;
            if (onProgress && performance.now() - last > 150 && !shown) { last = performance.now(); onProgress(f.loaded, f.total); }
            if (f.done) { resolve(); return; }
            if (!shown && f.buf && (f.loaded >= 2e6 || f.loaded >= f.total / 4)) {       // 2 MB（または 1/4）届いたら最初のコマを出してみる
              const inf = parseNow();
              if (inf && inf.frames.length) { shown = true; apply(inf); resolve(); }
            }
          };
          unsub = fx.onChunk(check);
          fx.promise.then(() => check(fx), reject);
        });
        if (unsub) { unsub(); unsub = null; }
      }
      if (token !== this._token || !isCurrent()) throw new Error('cancelled');
      if (fx.done) {
        if (unsub) { unsub(); unsub = null; }
        info = parseAvi(fx.buf.buffer); apply(info); this.loading = false;
      } else {
        // 残りは裏で: 届くたびに parse し直してコマを増やす。終わったら完全な parse
        const tick = f => {
          if (token !== this._token) { unsub && unsub(); return; }
          this.bytesLoaded = f.loaded;
          if (f.done) {
            unsub && unsub(); unsub = null;
            try { const inf = parseAvi(f.buf.buffer); apply(inf); } catch (e) { /* 途中までのコマのまま */ }
            this.loading = false;
            this.dispatchEvent(new Event('durationchange')); this.dispatchEvent(new Event('timeupdate'));
          } else if (performance.now() - last > 250) {
            last = performance.now();
            const inf = parseNow();
            if (inf && inf.frames.length > this.frames.length) { apply(inf); this.dispatchEvent(new Event('progress')); }
          }
        };
        unsub = fx.onChunk(tick);
        fx.promise.then(() => tick(fx), e => { if (token === this._token) { this.loading = false; this.error = e; } });
      }
    } catch (e) {
      if (unsub) unsub();
      if (!pre) fx.abort();
      throw e;
    }
    this.index = 0; this._paused = true; this._ended = false;
    await this._show(0);
    if (token !== this._token) throw new Error('cancelled');
    this.dispatchEvent(new Event('loadedmetadata'));
    this.dispatchEvent(new Event('timeupdate'));
    return info || { fps: this.fps, width: this.width, height: this.height, frames: this.frames, partial: true };
  }
  close() {
    this.pause();
    this._token++;
    if (this._fetch) { if (!this._fetch.done) this._fetch.abort(); this._fetch = null; }
    this.loading = false;
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
      if (i >= this.frames.length && this.loading) {            // まだ届いていない: 届いているコマの最後で待つ（時間もそこで止める）
        i = this.frames.length - 1;
        this._t0 = performance.now() - i / this.fps / this._rate * 1000;
      }
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
  _prefetch(i) {                      // 次に出すコマを先に解読しておく（4 倍速なら 1 コマおきなど、出す間隔に合わせて飛ばす）
    const step = Math.max(1, Math.round(this._rate * this.fps / 60));
    for (let k = 1; k <= 6; k++) { const j = i + k * step; if (j < this.frames.length) this._decode(j); }
  }
}
