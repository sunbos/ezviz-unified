'use strict';
/*
 * hevc_probe.cjs — observation-only probe for the official HEVC (H.265)
 * soft-decoder HLS preview. Exports { createHevcProbe }.
 *
 * Boundaries: never modifies the vendor UMD/worker/wasm pipeline, never
 * draws to the official canvas, never intercepts the renderer, never logs
 * raw pixels, raw entities, or URLs. Public receipts carry whitelisted
 * counters and measured facts only.
 */
const { createHash } = require('crypto');
const { spawnSync } = require('child_process');

const WORKER_PATH = '/vendor/decoder.worker.js';
const WASM_PATH = '/vendor/decoder.wasm';
const SEGMENT_RE = /^\/media\/segment-\d+\.ts$/;
const EXPECTED = {
  [WORKER_PATH]: {
    sha256: 'fd24bc9d975183ecaee66e0ad0db79a8c192cb8f2f34abd4c4aa82b7128fe709',
    mime: ['text/javascript', 'application/javascript'],
  },
  [WASM_PATH]: {
    sha256: 'acc7bc90ce79c2d1e28a82dc4785437f58dbd1628497145d7d38861bcc75d644',
    mime: ['application/wasm'],
  },
};
const COLORS = ['red', 'green', 'blue']; // index matches dominant RGB channel
// Fixed marker printed ONLY by the worker observer on an external http(s)/ws(s)
// CSP violation. Node catches the exact string via console events; raw
// blockedURIs/event payloads never cross into Node.
const WORKER_CSP_MARK = 'hevc-probe-worker-csp-1';

function sha256(buf) { return createHash('sha256').update(buf).digest('hex'); }

// Require worker script CSP: default-src 'none' AND connect-src 'self' exactly.
// A repeated directive name is rejected outright rather than trusting the last
// occurrence -- the real policy delivers each directive once.
function workerCspOk(value) {
  if (!value) return false;
  const d = {};
  for (const part of String(value).split(';')) {
    const t = part.trim().split(/\s+/).filter(Boolean);
    if (!t.length) continue;
    const k = t[0].toLowerCase();
    if (d[k] !== undefined) return false; // duplicate directive: fail closed
    d[k] = t.slice(1);
  }
  const only = (k, v) => Array.isArray(d[k]) && d[k].length === 1 && d[k][0] === v;
  return only('default-src', "'none'") && only('connect-src', "'self'");
}

// Exact MIME match on the media type with parameters stripped.
function mimeOk(path, header) {
  const mt = String(header || '').split(';', 1)[0].trim().toLowerCase();
  return EXPECTED[path].mime.indexOf(mt) !== -1;
}

// ffprobe the FIRST actual MPEG-TS segment bytes: exactly one HEVC video
// stream, 320x180 yuv420p, no audio. Raw ffprobe output is never exposed.
function probeSegment(bytes) {
  const r = spawnSync('ffprobe', [
    '-v', 'error', '-protocol_whitelist', 'pipe',
    '-f', 'mpegts', '-i', 'pipe:0',
    '-show_entries', 'stream=codec_name,codec_type,width,height,pix_fmt',
    '-show_streams', '-of', 'json',
  ], { input: bytes, timeout: 10000 });
  if (r.error || r.status !== 0) throw new Error('ffprobe could not read first segment');
  let streams = null;
  try { streams = JSON.parse(r.stdout.toString('utf8')).streams; } catch (e) { /* fallthrough */ }
  if (!Array.isArray(streams) || streams.length !== 1) throw new Error('segment must contain exactly one stream');
  const s = streams[0];
  if (!(s.codec_type === 'video' && s.codec_name === 'hevc' &&
        s.width === 320 && s.height === 180 && s.pix_fmt === 'yuv420p')) {
    throw new Error('segment must be 320x180 HEVC yuv420p video only');
  }
  return { streams: 1, codec: 'hevc', codecType: 'video', width: 320, height: 180, pixFmt: 'yuv420p', audio: false };
}

function createHevcProbe(origin) {
  // Exactly http://127.0.0.1:<port> -- no https, no other loopback spellings,
  // no credentials, query, fragment, or subpath.
  let u = null;
  try { u = new URL(String(origin)); } catch (e) { u = null; }
  if (!u || u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || u.port === ''
    || u.username !== '' || u.password !== '' || u.pathname !== '/'
    || u.search !== '' || u.hash !== '') {
    throw new Error('createHevcProbe requires an exact http://127.0.0.1:port origin');
  }
  const workerURL = u.origin + WORKER_PATH;
  const counters = { workerRequests: 0, wasmRequests: 0, workersStarted: 0, workersClosed: 0 };
  // Per-page absolute counters {we,csp,stop,tbefore,tafter}: each page's pushed
  // report and pulled object are max-merged into that page's OWN slot, and the
  // totals sum the 1-2 page slots -- repeated observations stay idempotent.
  const pageCounters = new Map();
  // Per-official-worker absolute CSP poll counts: max per same worker, retained
  // for a worker that was last polled before closing. Plus one console-marker
  // increment per actual external-origin violation emitted by that worker.
  const workerCspPolls = new Map();
  let workerCspMarkers = 0;
  const pageEvalFailed = new Set(); // live pages whose counters are unobservable
  const unapprovedWorkers = new Set(); // live workers whose URL is not the official one
  let unapprovedSeen = 0;
  const openWorkers = new Set();
  const workerEvalFailed = new Set(); // required workers whose counter eval failed
  let pageErrors = 0;
  let workerVerified = false;
  let wasmVerified = false;
  let segmentFacts = null;
  let measuredWithOpenWorker = false;

  // Totals sum the per-page slots (each an absolute max-merged value) plus the
  // worker side: for the single allowed worker, the larger of its exact
  // console-marker count and the summed cached per-worker polls -- never a
  // cumulative addition at each poll.
  const totals = () => {
    let we = 0; let pageCsp = 0; let stop = 0; let tbefore = 0; let tafter = 0;
    for (const c of pageCounters.values()) {
      we += c.we; pageCsp += c.csp; stop += c.stop;
      tbefore += c.tbefore; tafter += c.tafter;
    }
    let polled = 0;
    for (const n of workerCspPolls.values()) polled += n;
    return {
      workerRequests: counters.workerRequests,
      wasmRequests: counters.wasmRequests,
      workersStarted: counters.workersStarted,
      workersClosed: counters.workersClosed,
      errors: pageErrors + we + unapprovedSeen,
      cspBlocked: pageCsp + Math.max(workerCspMarkers, polled),
      stopClick: stop,
      terminateBeforeStop: tbefore,
      terminateAfterStop: tafter,
    };
  };

  // Merge page-side counters (absolute values) into THIS page's own slot, then
  // merge absolute per-worker CSP polls into each worker's own slot.
  async function pull(page) {
    let c = null;
    try {
      c = await page.evaluate(() => window.__hevcProbeCounters || null);
    } catch (e) { c = null; }
    if (c && typeof c === 'object') {
      const cur = pageCounters.get(page) || { we: 0, csp: 0, stop: 0, tbefore: 0, tafter: 0 };
      cur.we = Math.max(cur.we, c.we | 0);
      cur.csp = Math.max(cur.csp, c.csp | 0);
      cur.stop = Math.max(cur.stop, c.stop | 0);
      cur.tbefore = Math.max(cur.tbefore, c.tbefore | 0);
      cur.tafter = Math.max(cur.tafter, c.tafter | 0);
      pageCounters.set(page, cur);
      pageEvalFailed.delete(page);
    } else {
      // A live page whose counters cannot be read is NOT a zero: fail closed so
      // a positive count can never be silently defaulted away.
      let live = false;
      try { live = !page.isClosed(); } catch (e) { live = false; }
      if (live) pageEvalFailed.add(page);
    }
    for (const w of Array.from(openWorkers)) {
      try {
        const o = await w.evaluate(() => (self.__hevcObs ? self.__hevcObs.csp | 0 : null));
        if (o === null) { workerEvalFailed.add(w); continue; } // never confirmed
        // Absolute count on the SAME worker: keep the maximum only -- a re-poll
        // of the same value adds nothing, and a worker that later closes keeps
        // its last seen count.
        workerCspPolls.set(w, Math.max(workerCspPolls.get(w) || 0, o));
      } catch (e) {
        // A failed evaluation on a still-live required worker is NOT a zero.
        if (openWorkers.has(w)) workerEvalFailed.add(w);
      }
    }
  }

  // MUST be called before navigation.
  async function attach(page) {
    // Reports pushed through THIS page's binding update only this page's slot
    // (absolute per-field maxima -- a repeated report adds nothing).
    await page.exposeFunction('__hevcProbeReport', (o) => {
      if (!o || typeof o !== 'object') return;
      const cur = pageCounters.get(page) || { we: 0, csp: 0, stop: 0, tbefore: 0, tafter: 0 };
      cur.we = Math.max(cur.we, o.we | 0);
      cur.csp = Math.max(cur.csp, o.csp | 0);
      pageCounters.set(page, cur);
    });
    await page.addInitScript(() => {
      const c = { we: 0, csp: 0, stop: 0, tbefore: 0, tafter: 0 };
      const report = () => {
        try {
          if (typeof window.__hevcProbeReport === 'function') {
            window.__hevcProbeReport({ we: c.we, csp: c.csp });
          }
        } catch (e) { /* ignore */ }
      };
      try { Object.defineProperty(window, '__hevcProbeCounters', { value: c }); } catch (e) { window.__hevcProbeCounters = c; }
      const WORKER_PATH = '/vendor/decoder.worker.js';
      // Genuine stop intent: capture phase runs before the page's own bubble
      // handler -- only a real click actually targeting the stop button counts.
      document.addEventListener('click', (ev) => {
        try {
          const t = ev && ev.target;
          if (t && t.id === 'stop-preview') c.stop += 1;
        } catch (e) { /* observe only */ }
      }, true);
      const Genuine = window.Worker;
      if (typeof Genuine === 'function') {
        window.Worker = new Proxy(Genuine, {
          construct(target, args, nt) {
            const w = Reflect.construct(target, args, nt); // genuine worker, unmodified
            try {
              // terminate() is wrapped ONLY on a worker whose resolved URL is
              // exactly the official decoder script: the original runs via
              // Reflect.apply and a real termination AFTER the recorded stop
              // click is counted -- nothing is faked or deferred.
              let official = false;
              try {
                const a = args && args[0];
                const s = (a && a.url !== undefined) ? a.url : a;
                official = new URL(String(s), location.href).href
                  === new URL(WORKER_PATH, location.href).href;
              } catch (e) { official = false; }
              if (official) {
                const t0 = w.terminate;
                if (typeof t0 === 'function' && !t0.__hevcTerm) {
                  const t = () => {
                    let stopped = false;
                    try { stopped = (c.stop | 0) > 0; } catch (e) { stopped = false; }
                    const r = Reflect.apply(t0, w, []);
                    if (stopped) c.tafter += 1; else c.tbefore += 1;
                    return r;
                  };
                  t.__hevcTerm = true;
                  // Required observation: if the assignment is rejected or the
                  // genuine method stays installed, fail closed via the error
                  // counter rather than silently losing the causation proof.
                  try {
                    w.terminate = t;
                    if (w.terminate !== t) { c.we += 1; report(); }
                  } catch (e) { c.we += 1; report(); }
                }
              }
              const bump = () => { c.we += 1; report(); };
              w.addEventListener('error', bump);
              w.addEventListener('messageerror', bump);
              w.addEventListener('message', (ev) => {
                const d = ev && ev.data;
                if (!d || typeof d !== 'object') return;
                // Official decoder emits {type:'log', payload:{level,msg}}:
                // the level lives under payload; message text is never kept.
                const p = d.payload && typeof d.payload === 'object' ? d.payload : null;
                const lv = p ? p.level : null;
                if (d.type === 'error' || (d.type === 'log' && (lv === 'error' || lv === 'fatal'))) bump();
              });
            } catch (e) { /* observe only */ }
            return w;
          },
        });
      }
      // Page-side CSP count: only an http(s)/ws(s) blockedURI at a DIFFERENT
      // origin is a violation -- unrelated inline-style eval errors and
      // non-network schemes never inflate the count.
      const pageBase = location.origin;
      self.addEventListener('securitypolicyviolation', (ev) => {
        let u = null;
        try { u = new URL((ev && ev.blockedURI) || ''); } catch (e) { u = null; }
        if (u && (u.protocol === 'http:' || u.protocol === 'https:'
          || u.protocol === 'ws:' || u.protocol === 'wss:')
          && u.origin !== pageBase) {
          c.csp += 1; report();
        }
      });
    });
    page.on('pageerror', () => { pageErrors += 1; });
    // The worker observer prints ONLY the fixed marker on an external
    // http(s)/ws(s) CSP block; the exact string is matched here and the raw
    // event payload never leaves the worker. Each marker message is one real
    // violation -- workers emit one per event -- so every marker counts once.
    // There is no ConsoleMessage.worker() API: attribution stays marker-count
    // based, proven by the worker's own emitted events.
    page.on('console', (m) => {
      let t = '';
      try { t = m.text(); } catch (e) { return; }
      if (t === WORKER_CSP_MARK) workerCspMarkers += 1;
    });
    page.on('worker', (w) => {
      let url = '';
      try { url = w.url(); } catch (e) { url = ''; }
      if (url !== workerURL) {
        // Never silently skip a different worker URL: count it and fail the
        // run (the count lands in totals().errors). It is still tracked while
        // live so release cannot pass with an unapproved worker running.
        unapprovedSeen += 1;
        unapprovedWorkers.add(w);
        w.once('close', () => { unapprovedWorkers.delete(w); });
        return;
      }
      counters.workersStarted += 1;
      openWorkers.add(w);
      w.once('close', () => { counters.workersClosed += 1; openWorkers.delete(w); });
      // Strict release already fails on >1 official worker (workersStarted must
      // equal workersClosed==1-path and a second live worker trips
      // 'a worker is still live'); the allowed single worker's CSP count is
      // reported as max(markers, polls), never cumulative addition.
      // Passive worker-internal CSP observer only -- no fetch replacement; the
      // context route plus the original worker CSP header already bound the
      // worker's network. On a violation it increments its own counter AND
      // emits only the fixed marker for the external http(s)/ws(s) case so the
      // event is visible immediately (never the raw URI/message). Resolves
      // true only when installed on the live worker.
      w.evaluate((mark) => {
        if (!self.__hevcObs) {
          const o = { csp: 0 };
          const base = location.origin;
          // Worker-side CSP count: only an http(s)/ws(s) blockedURI at a
          // DIFFERENT origin counts -- same explicit-origin rule as the page.
          self.addEventListener('securitypolicyviolation', (ev) => {
            let u = null;
            try { u = new URL((ev && ev.blockedURI) || ''); } catch (e) { u = null; }
            if (u && (u.protocol === 'http:' || u.protocol === 'https:'
              || u.protocol === 'ws:' || u.protocol === 'wss:')
              && u.origin !== base) {
              o.csp += 1;
              try { console.debug(mark); } catch (e) { /* ignore */ }
            }
          });
          self.__hevcObs = o;
        }
        return self.__hevcObs === undefined ? false : true;
      }, WORKER_CSP_MARK).then((ok) => {
        if (ok !== true && openWorkers.has(w)) workerEvalFailed.add(w);
      }).catch(() => {
        if (openWorkers.has(w)) workerEvalFailed.add(w); // live but unobserved
      });
    });
  }

  // Called on the accepted route.fetch response, before fulfill. Throws on
  // any invalid decoder byte/MIME/CSP/status or bad first segment.
  async function resource(path, response) {
    if (path === WORKER_PATH || path === WASM_PATH) {
      if (path === WORKER_PATH) counters.workerRequests += 1; else counters.wasmRequests += 1;
      if (response.status() !== 200) throw new Error('decoder resource status ' + response.status());
      const h = response.headers();
      if (!mimeOk(path, h['content-type'])) {
        throw new Error('unexpected MIME for ' + path);
      }
      if (sha256(await response.body()) !== EXPECTED[path].sha256) {
        throw new Error('sha256 mismatch for ' + path);
      }
      if (path === WORKER_PATH) {
        if (!workerCspOk(h['content-security-policy'])) {
          throw new Error("worker CSP must be default-src 'none' + connect-src 'self'");
        }
        workerVerified = true;
      } else wasmVerified = true;
      return;
    }
    if (SEGMENT_RE.test(path) && !segmentFacts) {
      segmentFacts = probeSegment(await response.body()); // first actual segment only
    }
  }

  // After click: the official loader is asynchronous, so first WAIT (bounded)
  // for the page's own observed init -- exactly #player.dataset.decoder==='soft'
  // plus the exact __hevcClock facts on a visible document -- and only then
  // require verified worker/wasm bytes, the first segment, and exactly one open
  // expected worker. Missing resources are never marked true.
  async function measure(page) {
    // Bounded wait for the observed decoder init, not for our own gates: the
    // dataset flag is set by the page from the official initSuccess info.
    await page.waitForFunction(() => {
      if (document.visibilityState !== 'visible') return false;
      const el = document.getElementById('player');
      if (!el || !el.dataset || el.dataset.decoder !== 'soft') return false;
      const c = window.__hevcClock;
      return !!(c && c.wasmLoaded === true &&
        c.video && c.video.codec === 'hevc' &&
        c.video.width === 320 && c.video.height === 180 &&
        typeof c.time === 'number' && isFinite(c.time));
    }, undefined, { timeout: 30000 });
    // Now that init actually happened, the asynchronous resource/worker facts
    // must already be true -- wait finitely for the worker to register, then
    // recheck each gate and fail on the first unmet one (never marked true).
    const wdeadline = Date.now() + 10000;
    while (Date.now() < wdeadline
      && !(workerVerified && wasmVerified && segmentFacts && openWorkers.size === 1)) {
      await page.waitForTimeout(100);
    }
    if (!workerVerified) throw new Error('decoder.worker.js bytes not verified');
    if (!wasmVerified) throw new Error('decoder.wasm bytes not verified');
    if (!segmentFacts) throw new Error('first media segment not verified');
    if (openWorkers.size !== 1) throw new Error('expected exactly one open decoder worker');
    measuredWithOpenWorker = true;
    await pull(page);

    const deadline = Date.now() + 25000;
    const run = [];
    let prev = -1; let lastTime = -1; let canvas = null;
    while (Date.now() < deadline && run.length < 3) {
      // Sample: classify INSIDE the page so no raw RGB crosses into Node. The
      // official canvas is found under #player; a WebGL probe is created only
      // after the element is confirmed as a genuine attached+visible canvas,
      // never on an unrelated candidate.
      const s = await page.evaluate(() => {
        const c = document.querySelector('#player canvas');
        if (!c || !c.isConnected || document.visibilityState !== 'visible') return null;
        const cs = getComputedStyle(c);
        if (!cs || cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return null;
        const r = c.getBoundingClientRect();
        if (!(c.clientWidth > 0 && c.clientHeight > 0) || !(r.width > 0 && r.height > 0)) return null;
        const vw = window.innerWidth || 0; const vh = window.innerHeight || 0;
        if (r.bottom <= 0 || r.right <= 0 || r.left >= vw || r.top >= vh) return null; // in viewport
        const w = c.width | 0; const h = c.height | 0;
        if (!(w > 0 && h > 0)) return null;
        // Genuine official canvas only now: a real rendering context must exist.
        if (!(c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl'))) return null;
        const off = document.createElement('canvas'); // OFF-DOM 16x8 copy per sample
        off.width = 16; off.height = 8;
        const x = off.getContext('2d', { willReadFrequently: true });
        if (!x) return null;
        x.drawImage(c, 0, 0, 16, 8);
        const px = (pxx, pyy) => { const d = x.getImageData(pxx, pyy, 1, 1).data; return [d[0], d[1], d[2]]; };
        // White reference: left strip (x=1 or 2); color: right area (x=12), mid row.
        const a = px(2, 4);
        const b = px(12, 4);
        const white = a[0] >= 195 && a[1] >= 195 && a[2] >= 195;
        const m = Math.max(b[0], b[1], b[2]);
        let idx = -1;
        if (m >= 150) {
          idx = b.indexOf(m);
          for (let j = 0; j < 3; j += 1) if (j !== idx && m - b[j] < 65) { idx = -1; break; }
        }
        const t = window.__hevcClock ? window.__hevcClock.time : null;
        return { w, h, cw: Math.round(r.width), ch: Math.round(r.height),
                 white, idx, time: (typeof t === 'number' && isFinite(t)) ? t : null };
      });
      if (s && s.time !== null && s.time > lastTime && s.white === true) {
        const idx = s.idx;
        if (idx >= 0) {
          canvas = { width: s.w, height: s.h, cssWidth: s.cw, cssHeight: s.ch };
          lastTime = s.time;
          if (idx !== prev) {
            if (prev >= 0 && idx !== (prev + 1) % 3) run.length = 0; // non-cyclic: restart run
            run.push({ name: COLORS[idx], t: s.time });
            prev = idx;
          }
        }
      }
      if (run.length < 3) await page.waitForTimeout(120);
    }
    if (run.length < 3) throw new Error('no three consecutive cyclic red/green/blue phases observed');
    const deltaMs = Math.round((lastTime - run[0].t) * 1000);
    if (!(deltaMs > 500)) throw new Error('media time did not advance >0.5s across phases');
    await pull(page); // collect worker CSP counters before termination
    if (workerEvalFailed.size > 0) {
      throw new Error('live decoder worker could not be observed');
    }
    if (unapprovedSeen > 0) throw new Error('unapproved worker observed');
    if (pageEvalFailed.size > 0) {
      throw new Error('live page counters could not be observed');
    }
    const t = totals();
    if (t.errors > 0) throw new Error('worker/page errors observed');
    if (t.cspBlocked > 0) throw new Error('CSP violations observed');
    return {
      decoder: 'soft',
      wasmLoaded: true,
      video: { codec: 'hevc', width: 320, height: 180 },
      canvas,
      phases: run.length,
      sequence: run.map((p) => p.name),
      mediaDeltaMs: deltaMs,
      firstMediaTime: run[0].t,
      lastMediaTime: lastTime,
      segment: segmentFacts,
      workerVerified,
      wasmVerified,
      workersStarted: counters.workersStarted,
      stopClick: t.stopClick,
      terminateBeforeStop: t.terminateBeforeStop,
      terminateAfterStop: t.terminateAfterStop,
      cspBlocked: t.cspBlocked,
      unexpectedWorkers: unapprovedSeen,
      errors: t.errors,
    };
  }

  // After real stop/destroy, before page/context close: the official worker
  // must have been open at measure, must be closed now with matching
  // started/closed counts, canvas detached, zero errors, and no unapproved or
  // still-live expected worker while the page itself stays open.
  async function released(page) {
    if (!measuredWithOpenWorker || counters.workersStarted < 1) {
      throw new Error('no official decoder worker was open during measure');
    }
    const aliveExpected = () => page.workers().some((w) => {
      try { return w.url() === workerURL; } catch (e) { return false; }
    });
    let canvasGone = false;
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      canvasGone = await page.evaluate(() => !document.querySelector('#player canvas')).catch(() => false);
      if (!aliveExpected() && openWorkers.size === 0 && unapprovedWorkers.size === 0 && canvasGone) break;
      await page.waitForTimeout(150);
    }
    await pull(page);
    if (aliveExpected() || openWorkers.size > 0 || unapprovedWorkers.size > 0) {
      throw new Error('a worker is still live after stop/destroy');
    }
    // Actual matching lifetimes: every started worker must have closed, and at
    // least one must have run -- not merely "none left".
    if (!(counters.workersStarted === counters.workersClosed && counters.workersStarted >= 1)) {
      throw new Error('decoder worker did not close after stop/destroy');
    }
    const t = totals();
    // Causation proof: a real stop click happened and the only observed real
    // terminate on the official worker ran AFTER it -- not an early death.
    if (t.stopClick < 1) throw new Error('no genuine stop click observed');
    if (!(t.terminateBeforeStop === 0 && t.terminateAfterStop === 1)) {
      throw new Error('decoder worker was not terminated by the stop click');
    }
    if (!canvasGone) throw new Error('player canvas still attached after destroy');
    if (!workerVerified || !wasmVerified || !segmentFacts) throw new Error('decoder/segment evidence incomplete');
    if (workerEvalFailed.size > 0 || unapprovedSeen > 0) {
      throw new Error('unobserved or unapproved worker observed');
    }
    if (pageEvalFailed.size > 0) {
      throw new Error('live page counters could not be observed');
    }
    if (t.errors > 0) throw new Error('worker/page errors observed');
    if (t.cspBlocked > 0) throw new Error('CSP violations observed');
    return {
      workersStarted: counters.workersStarted,
      workersClosed: counters.workersClosed,
      stopClick: t.stopClick,
      terminateBeforeStop: t.terminateBeforeStop,
      terminateAfterStop: t.terminateAfterStop,
      cspBlocked: t.cspBlocked,
      errors: t.errors,
      canvasDetached: true,
    };
  }

  function snapshot() { return totals(); } // idempotent, integers only

  return { attach, resource, measure, released, snapshot };
}

module.exports = { createHevcProbe };
