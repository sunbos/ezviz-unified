'use strict';

/*
 * node tools/media/verify_flv.cjs --base http://127.0.0.1:PORT [--chrome PATH]
 * Reads the SDK example's synthetic FLV. The receipt exposes measured media
 * and lifecycle facts without raw request URLs, pixels, or browser errors.
 */

// Browser probes for the pinned FLV integration.
function observeDocument(origin, observeChildPlayer) {
  const isTop = window === window.top;
  if (!isTop && (location.origin !== origin || location.pathname !== '/player.html')) return;
  const evidence = isTop ? {
    instances: 0, paused: 0, destroyed: 0, blocked: 0, sources: [], video: null,
    staticVersion: null, ended: 0, terminations: 0,
  } : window.top.__flvEvidence;
  window.__flvEvidence = evidence;
  if (!isTop) {
    // The parent constructs the official instance from its iframe load handler.
    window.addEventListener('load', () => { evidence.staticVersion = observeChildPlayer(); }, { once: true });
    document.addEventListener('ended', (event) => {
      if (event.target === evidence.video) evidence.ended += 1;
    }, true);
    const terminate = Worker.prototype.terminate;
    Worker.prototype.terminate = function (...args) {
      const result = Reflect.apply(terminate, this, args);
      evidence.terminations += 1;
      return result;
    };
  }
  const createObjectURL = URL.createObjectURL;
  const revokeObjectURL = URL.revokeObjectURL;
  const remote = /^(https?|wss?):/i;
  window.addEventListener('securitypolicyviolation', (event) => {
    const uri = event.blockedURI || '';
    if (!remote.test(uri)) return;
    try {
      if (new URL(uri).origin !== String(origin)) evidence.blocked += 1;
    } catch (ignore) {
      evidence.blocked += 1;
    }
  });
  URL.createObjectURL = function createObjectURLProbe(value) {
    const url = Reflect.apply(createObjectURL, this, [value]);
    if (value instanceof MediaSource) {
      const record = { source: value, url, opened: false, closed: false, revoked: false };
      value.addEventListener('sourceopen', () => { record.opened = true; });
      value.addEventListener('sourceclose', () => { record.closed = true; });
      evidence.sources.push(record);
    }
    return url;
  };
  URL.revokeObjectURL = function revokeObjectURLProbe(url) {
    const result = Reflect.apply(revokeObjectURL, this, [url]);
    for (const record of evidence.sources) {
      if (record.url === url) record.revoked = true;
    }
    return result;
  };
}
function observePlayer() {
  const evidence = window.__flvEvidence;
  if (!evidence) throw new Error('flv-evidence-missing');
  const sdk = window.EzuikitFlv;
  if (typeof sdk !== 'function') throw new Error('flv-sdk-missing');
  const version = sdk.version;
  if (typeof version !== 'string' || !version) throw new Error('flv-version-missing');
  window.EzuikitFlv = new Proxy(sdk, {
    construct(target, args, newTarget) {
      const instance = Reflect.construct(target, args, newTarget);
      for (const [key, field] of [['pause', 'paused'], ['destroy', 'destroyed']]) {
        const original = instance[key];
        if (typeof original !== 'function') throw new Error('flv-method-missing');
        instance[key] = function flvCounted(...callArgs) {
          const result = Reflect.apply(original, this, callArgs);
          evidence[field] += 1;
          return result;
        };
      }
      evidence.instances += 1;
      return instance;
    }
  });
  return version;
}
function installPagehideWitness() {
  const evidence = window.__flvEvidence;
  const persist = () => {
    try {
      const video = evidence.video;
      const record = evidence.activeSource;
      sessionStorage.setItem('__flvLeft', JSON.stringify({
        instances: evidence.instances,
        paused: evidence.paused,
        destroyed: evidence.destroyed,
        revoked: !!record && record.revoked === true,
        detached: !!video && video.isConnected === false && !video.getAttribute('src'),
        blocked: evidence.blocked,
        terminations: evidence.terminations
      }));
    } catch (ignore) { /* silent during unload */ }
  };
  window.addEventListener('pagehide', persist);
}
async function sampleFrames({ full }) {
  const evidence = window.__flvEvidence;
  if (!evidence) throw new Error('flv-evidence-missing');
  const locate = (node) => {
    const scope = node && (node.shadowRoot || node);
    if (!scope || !scope.querySelector) return null;
    if (scope instanceof HTMLVideoElement) return scope;
    const found = scope.querySelector('video');
    if (found) return found;
    for (const child of scope.querySelectorAll('*')) {
      const deep = child.shadowRoot ? locate(child.shadowRoot) : null;
      if (deep) return deep;
    }
    return null;
  };
  const video = locate(document.querySelector('#flv-stage'));
  if (!video || typeof video.requestVideoFrameCallback !== 'function') {
    throw new Error('flv-video-missing');
  }
  const canvas = full ? document.createElement('canvas') : null;
  if (canvas) { canvas.width = 8; canvas.height = 8; }
  const context = canvas ? canvas.getContext('2d', { willReadFrequently: true }) : null;
  const cycle = { red: 'green', green: 'blue', blue: 'red' };
  return await new Promise((resolve, reject) => {
    let handle = 0, ticks = 0, lastFrames = 0, lastTime = -1, firstTime = -1, settled = false;
    const colors = [];
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      if (handle) video.cancelVideoFrameCallback(handle);
      if (error) reject(error); else resolve(result);
    };
    const timer = window.setTimeout(() => finish(new Error('flv-sample-timeout')), 12000);
    const linkSource = () => {
      const current = video.currentSrc || video.src || '';
      for (const record of evidence.sources) {
        if (record.url === current && record.opened && !record.closed && !record.revoked) {
          evidence.video = video;
          evidence.activeSource = record;
          return record;
        }
      }
      return null;
    };
    const classify = () => {
      const sw = Math.floor(video.videoWidth / 4);
      const sh = Math.floor(video.videoHeight / 4);
      context.drawImage(video, (video.videoWidth - sw) / 2, (video.videoHeight - sh) / 2, sw, sh, 0, 0, 8, 8);
      const data = context.getImageData(0, 0, 8, 8).data;
      let r = 0, g = 0, b = 0;
      for (let i = 0; i < data.length; i += 4) { r += data[i]; g += data[i + 1]; b += data[i + 2]; }
      const n = data.length / 4;
      r /= n; g /= n; b /= n;
      const top = Math.max(r, g, b);
      const mid = r + g + b - top - Math.min(r, g, b);
      if (top < 90 || top - mid < 55) return null;
      return top === r ? 'red' : top === g ? 'green' : 'blue';
    };
    const cyclic = () => colors.length >= 3 && new Set(colors).size === 3
      && colors.every((color, i) => i === 0 || cycle[colors[i - 1]] === color);
    const step = (now, meta) => {
      if (settled) return;
      handle = 0;
      const mediaTime = meta && meta.mediaTime;
      const presented = meta && meta.presentedFrames;
      const grew = Number.isFinite(mediaTime) && Number.isFinite(presented)
        && presented > lastFrames && mediaTime > lastTime;
      if (!grew || !video.videoWidth || !video.videoHeight || !meta.width || !meta.height) {
        handle = video.requestVideoFrameCallback(step);
        return;
      }
      if (video.videoWidth !== 320 || video.videoHeight !== 180 || meta.width !== 320 || meta.height !== 180) {
        finish(new Error('flv-bad-size'));
        return;
      }
      ticks += 1;
      lastFrames = presented;
      if (firstTime < 0) firstTime = mediaTime;
      lastTime = mediaTime;
      const record = linkSource();
      const mediaDeltaMs = Math.floor((lastTime - firstTime) * 1000);
      if (full) {
        const color = classify();
        if (!color) { finish(new Error('flv-bad-color')); return; }
        if (colors[colors.length - 1] !== color) colors.push(color);
        if (record) {
          evidence.partial = { frames: ticks, width: video.videoWidth, height: video.videoHeight, mediaDeltaMs, phases: colors.slice(0, 3) };
        }
        if (ticks >= 3 && mediaDeltaMs >= 500 && record && cyclic()) {
          finish(null, { frames: ticks, width: video.videoWidth, height: video.videoHeight, mediaDeltaMs, phases: colors.slice(0, 3) });
          return;
        }
      } else if (ticks >= 2 && record) {
        finish(null, { frames: ticks, width: video.videoWidth, height: video.videoHeight, mediaDeltaMs, phases: [] });
        return;
      }
      handle = video.requestVideoFrameCallback(step);
    };
    handle = video.requestVideoFrameCallback(step);
  });
}
// End browser probes.

// Playwright orchestration.
const crypto = require('crypto');

const PKG = '2.1.2-beta.2';
const WANT_STATIC = '2.1.2';
const WANT_PW = '1.57.0';
const NAV_MS = 15000;
const STEP_MS = 10000;
const STATUS_MS = 7000;
const SETTLE_MS = 650;
const MEDIA_MAX = 8 * 1024 * 1024;
const COLORS = ['red', 'green', 'blue'];
const ALLOWED = new Set(['/', '/player.html', '/preview.js', '/preview.json', '/media/preview.flv', '/vendor/index.js', '/vendor/style.css', '/vendor/decoder.js', '/vendor/decoder.wasm']);
const PINS = {
  '/vendor/index.js': '5efdb94d01cff42f628693437e46d65110196eb87ee588bf8a569822d1d8489b',
  '/vendor/style.css': '352a3e34535fde4dfb5a198fa7207132dc919e0ab644229d7d2be57a6ec1673a',
  '/vendor/decoder.js': 'f4c6c4046fa6266f12079ddd235c0689816cd6e6e21bfb51f8a8d7ba849bbc87',
  '/vendor/decoder.wasm': 'c91f446fa528a8bb3119f891de27286f88b318811722a77f962fa14404905724',
};
const counts = { unexpectedHttp: 0, socketAttempts: 0, responseFailures: 0, pinFailures: 0, previewJson: 0, mediaRequests: 0 };
const pinned = new Set();
let flvHeaderOk = false;
let emitted = false;
const receipt = { result: 'fail', stage: 'arguments', packageVersion: PKG, staticVersion: null, bundleVerified: false, chromeSandbox: false, cspOk: false, flvHeaderOk: false, unexpectedHttp: 0, socketAttempts: 0, responseFailures: 0, pinFailures: 0, previewJson: 0, mediaRequests: 0, cspBlocked: 0, instances: null, destroyed: null, frames: null, width: null, height: null, mediaDeltaMs: null, phases: null, restartFrames: null, restartDeltaMs: null };

function sync() {
  for (const k of Object.keys(counts)) receipt[k] = counts[k];
  receipt.flvHeaderOk = flvHeaderOk;
  receipt.bundleVerified = pinned.has('/vendor/index.js') && counts.pinFailures === 0;
}
function emit() {
  if (emitted) return;
  emitted = true;
  sync();
  process.stdout.write(JSON.stringify(receipt) + '\n');
  process.exitCode = receipt.result === 'pass' ? 0 : 1;
}
function die() { throw new Error(receipt.stage); }

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (typeof t !== 'string' || !t.startsWith('--')) die();
    const eq = t.indexOf('=');
    let k, v;
    if (eq > 2) { k = t.slice(2, eq); v = t.slice(eq + 1); } else { k = t.slice(2); v = argv[++i]; }
    if ((k !== 'base' && k !== 'chrome') || v === undefined || v === '' || v.startsWith('--') || out[k] !== undefined) die();
    out[k] = v;
  }
  if (!out.base) die();
  const m = /^http:\/\/127\.0\.0\.1:([0-9]{1,5})\/?$/.exec(out.base);
  if (!m) die();
  const port = Number(m[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) die();
  const base = new URL(out.base);
  return { base: base.href, origin: base.origin, chrome: out.chrome || null };
}

function cspOkHeader(h, path = '/') {
  if (typeof h !== 'string') return false;
  const d = {};
  for (const seg of h.split(';')) {
    const t = seg.trim().split(/\s+/).filter(Boolean);
    if (t.length) { const k = t[0].toLowerCase(); if (!(k in d)) d[k] = t.slice(1); }
  }
  const ds = d['default-src'] || [], cs = d['connect-src'] || [], ss = d['script-src'] || [];
  const tok = v => v === "'self'" || v === "'wasm-unsafe-eval'";
  const exact = (k, v) => d[k]?.length === 1 && d[k][0] === v;
  const framePolicy = path === '/' ? exact('frame-src', "'self'") && exact('frame-ancestors', "'none'")
    : exact('frame-ancestors', "'self'");
  return framePolicy && ds.length === 1 && ds[0] === "'none'" && cs.length === 1 && cs[0] === "'self'" && ss.indexOf("'self'") !== -1 && ss.every(tok);
}

function cyclic3(p) {
  if (!Array.isArray(p) || p.length !== 3) return false;
  const ix = p.map(s => COLORS.indexOf(s));
  if (ix.some(i => i < 0)) return false;
  return (ix[1] - ix[0] + 3) % 3 === 1 && (ix[2] - ix[1] + 3) % 3 === 1;
}

function onRoute(origin) {
  return async route => {
    const req = route.request();
    let u;
    try { u = new URL(req.url()); } catch (e) { counts.unexpectedHttp++; return route.abort().catch(() => {}); }
    const path = u.pathname;
    if (req.method() !== 'GET' || u.origin !== origin || u.username || u.password || u.search || u.hash || !ALLOWED.has(path)) {
      counts.unexpectedHttp++;
      return route.abort().catch(() => {});
    }
    const media = path === '/media/preview.flv';
    if (path === '/preview.json') counts.previewJson++;
    if (media) counts.mediaRequests++;
    let resp;
    try {
      resp = await route.fetch({ maxRedirects: 0, timeout: 10000 });
      const st = resp.status();
      let good = st === 200;
      if ((path === '/' || path === '/player.html') && !cspOkHeader(resp.headers()['content-security-policy'], path)) good = false;
      if (media && st === 206) {
        const rg = req.headers()['range'] || '';
        const cr = resp.headers()['content-range'] || '';
        good = /^bytes=0-\d*/.test(rg) && /^bytes 0-\d+\//.test(cr);
      }
      if (!good) { counts.responseFailures++; return route.abort().catch(() => {}); }
      if (media && Number(resp.headers()['content-length'] || 0) > MEDIA_MAX) { counts.responseFailures++; return route.abort().catch(() => {}); }
      const body = await resp.body();
      if (media) {
        if (body.length < 9 || body.length > MEDIA_MAX || body[0] !== 0x46 || body[1] !== 0x4c || body[2] !== 0x56 || body[3] !== 0x01 || body[4] !== 0x01) {
          counts.responseFailures++;
          return route.abort().catch(() => {});
        }
        flvHeaderOk = true;
      }
      if (PINS[path]) {
        if (crypto.createHash('sha256').update(body).digest('hex') !== PINS[path]) { counts.pinFailures++; return route.abort().catch(() => {}); }
        pinned.add(path);
      }
      await route.fulfill({ response: resp, body });
    } catch (e) {
      counts.responseFailures++;
      try { await route.abort(); } catch (_) {}
    } finally {
      if (resp) {
        try { await resp.dispose(); } catch (_) { counts.responseFailures++; }
      }
    }
  };
}

function readEvidence(page) {
  return page.evaluate(() => {
    const e = window.__flvEvidence || {};
    const a = e.activeSource || null;
    const v = e.video || null;
    return {
      instances: typeof e.instances === 'number' ? e.instances : null,
      paused: typeof e.paused === 'number' ? e.paused : null,
      destroyed: typeof e.destroyed === 'number' ? e.destroyed : null,
      blocked: typeof e.blocked === 'number' ? e.blocked : null,
      staticVersion: e.staticVersion === '2.1.2' ? e.staticVersion : null,
      ended: e.ended,
      terminations: e.terminations,
      video: v ? { connected: v.isConnected === true, srcCleared: !v.getAttribute('src') } : null,
      active: a ? { opened: a.opened === true, closed: a.closed === true, revoked: a.revoked === true, readyState: a.source && a.source.readyState || null } : null,
    };
  });
}

async function measureFirst(frame, page) {
  const facts = await readEvidence(page);
  if (facts.staticVersion !== WANT_STATIC || !pinned.has('/vendor/index.js')) die();
  receipt.staticVersion = facts.staticVersion;
  receipt.stage = 'frames';
  const f1 = await frame.evaluate(sampleFrames, { full: true }).catch(() => null);
  if (!f1 || !Number.isInteger(f1.frames) || f1.frames < 3 || f1.width !== 320 || f1.height !== 180 || !(f1.mediaDeltaMs >= 500) || !cyclic3(f1.phases)) {
    let p1 = null;
    try { p1 = await page.evaluate(() => (window.__flvEvidence && window.__flvEvidence.partial) || null); } catch (pe) { p1 = null; }
    if (p1 && Number.isInteger(p1.frames) && p1.frames > 0 && p1.width === 320 && p1.height === 180 && Number.isFinite(p1.mediaDeltaMs) && p1.mediaDeltaMs > 0 && Array.isArray(p1.phases) && p1.phases.length >= 1 && p1.phases.length <= 3 && p1.phases.every(c => COLORS.indexOf(c) !== -1)) {
      receipt.frames = p1.frames; receipt.width = p1.width; receipt.height = p1.height;
      receipt.mediaDeltaMs = Math.floor(p1.mediaDeltaMs); receipt.phases = p1.phases.slice(0, 3);
      if (p1.frames >= 3 && p1.mediaDeltaMs >= 500 && !cyclic3(p1.phases)) receipt.stage = 'pixels';
    }
    die();
  }
  receipt.frames = f1.frames; receipt.width = f1.width; receipt.height = f1.height;
  receipt.mediaDeltaMs = Math.floor(f1.mediaDeltaMs); receipt.phases = f1.phases.slice(0, 3);
}

function observeWorkers(page, origin) {
  const records = [];
  let unexpected = 0;
  page.on('worker', (w) => {
    const rec = { closed: false, bad: false };
    try {
      const u = new URL(w.url());
      rec.bad = u.protocol !== 'blob:' || u.origin !== origin;
    } catch (e) { rec.bad = true; }
    if (rec.bad) unexpected += 1;
    w.once('close', () => { rec.closed = true; });
    records.push(rec);
  });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  return {
    begin() { return records.length; },
    requireActive(start) {
      const batch = records.slice(start);
      if (!batch.length || batch.some((r) => r.closed || r.bad)) {
        throw new Error('worker batch inactive or unexpected');
      }
    },
    async released(start) {
      const end = Date.now() + 7000;
      while (Date.now() < end) {
        const batch = records.slice(start);
        const closed = batch.filter((r) => r.closed).length;
        if (batch.length && closed === batch.length && !batch.some((r) => r.bad)) {
          return { workersStarted: batch.length, workersClosed: closed };
        }
        await sleep(50);
      }
      throw new Error('workers not released');
    },
    clean() { return unexpected === 0 && records.every((r) => r.closed); },
  };
}
async function verifyNaturalEnd(page, startPlayer, readEvidence, workers) {
  receipt.stage = 'eof-start';
  const phase = workers.begin();
  const jsonBefore = counts.previewJson;
  const mediaBefore = counts.mediaRequests;
  const frame = await startPlayer();
  const s = await frame.evaluate(sampleFrames, { full: false });
  if (!s || !Number.isInteger(s.frames) || s.frames < 2 || !(s.mediaDeltaMs > 0) || s.width !== 320 || s.height !== 180) die('eof sample invalid');
  workers.requireActive(phase);
  receipt.stage = 'eof-release';
  await page.waitForSelector('#status[data-state="stopped"]', { timeout: 30000 });
  if (await page.$('#player iframe')) die('player iframe remains');
  const replay = await page.waitForSelector('#play-preview');
  if (!replay || (await replay.isDisabled())) die('replay disabled');
  await page.waitForFunction(() => {
    const a = top.__flvEvidence && top.__flvEvidence.activeSource;
    return Boolean(a?.source) && (a.closed || a.source.readyState === 'closed');
  }, null, { timeout: 30000 });
  const ev = await readEvidence(page);
  if (!(ev.ended >= 1) || ev.instances !== 1 || !(ev.paused >= 1) || ev.destroyed !== 1 || ev.blocked !== 0) die('evidence mismatch');
  if (!ev.active.revoked || !(ev.active.readyState === 'closed' || ev.active.closed)) die('source not closed');
  if (ev.video.connected || !ev.video.srcCleared) die('video not cleared');
  if (counts.previewJson !== jsonBefore + 1) die('address fetch count');
  if (!(counts.mediaRequests > mediaBefore)) die('no media request');
  const settled = counts.mediaRequests;
  await new Promise((r) => setTimeout(r, 650));
  const mediaStable = counts.mediaRequests === settled;
  if (!mediaStable) die('media unstable');
  receipt.stage = 'eof-workers';
  const rel = await workers.released(phase);
  receipt.eofRelease = {
    ended: ev.ended,
    paused: ev.paused,
    destroyed: ev.destroyed,
    workerTerminateCalls: ev.terminations,
    videoDetached: !ev.video.connected,
    sourceRevoked: ev.active.revoked,
    sourceClosed: ev.active.readyState === 'closed' || !!ev.active.closed,
    mediaStable,
    workersStarted: rel.workersStarted,
    workersClosed: rel.workersClosed,
  };
}
// End worker and EOF probes.

async function exercisePlayer(page, a) {
  const workers = observeWorkers(page, a.origin);
  const startPlayer = async () => {
    await page.locator('#play-preview').click();
    await page.frameLocator('#player iframe').locator('#flv-stage video').waitFor({ state: 'attached', timeout: STEP_MS });
    const frame = page.frame({ url: a.origin + '/player.html' });
    if (!frame) die();
    return frame;
  };
  receipt.stage = 'navigate';
  const r1 = await page.goto(a.base, { waitUntil: 'load', timeout: NAV_MS });
  if (!r1 || r1.status() !== 200 || !cspOkHeader(r1.headers()['content-security-policy'])) die();
  receipt.cspOk = true;
  await page.evaluate(installPagehideWitness);
  await page.evaluate(() => { window.__flvOuterMarked = true; });
  if (counts.previewJson !== 0 || counts.mediaRequests !== 0 || counts.pinFailures !== 0) die();
  const e0 = await readEvidence(page);
  if (e0.instances !== 0) die();
  const firstBatch = workers.begin();
  receipt.stage = 'mount';
  const first = await startPlayer();
  await measureFirst(first, page);
  if (counts.previewJson !== 1 || counts.mediaRequests < 1) die();
  const e1 = await readEvidence(page);
  if (!(e1.instances === 1 && e1.destroyed === 0 && e1.active && e1.active.opened && !e1.active.revoked && !e1.active.closed)) die();
  receipt.stage = 'playing';
  await page.waitForSelector('#status[data-state="playing"]', { state: 'attached', timeout: STATUS_MS });
  workers.requireActive(firstBatch);
  receipt.stage = 'stop';
  const src0 = await page.evaluateHandle(() => (window.__flvEvidence && window.__flvEvidence.activeSource) || null);
  await page.locator('#stop-preview').click();
  await page.waitForSelector('#status[data-state="stopped"]', { state: 'attached', timeout: STATUS_MS });
  await page.waitForFunction(a => !!(a && (a.closed === true || (a.source && a.source.readyState === 'closed'))), src0, { timeout: STATUS_MS });
  const e2 = await readEvidence(page);
  if (!(e2.instances === 1 && e2.paused >= 1 && e2.destroyed === 1)) die();
  if (!(e2.video && e2.video.connected === false && e2.video.srcCleared === true)) die();
  if (!(e2.active && e2.active.revoked === true && (e2.active.closed === true || e2.active.readyState === 'closed'))) die();
  if (await page.locator('#player iframe').count() !== 0) die();
  const pj = counts.previewJson, mj = counts.mediaRequests;
  await page.waitForTimeout(SETTLE_MS);
  if (counts.previewJson !== pj || counts.mediaRequests !== mj) die();
  if (e2.blocked !== 0) die();
  receipt.cspBlocked = e2.blocked;
  if (await page.evaluate(() => window.__flvOuterMarked) !== true) die();
  receipt.stage = 'stop-workers';
  const stoppedWorkers = await workers.released(firstBatch);
  receipt.stopRelease = {
    paused: e2.paused, destroyed: e2.destroyed, workerTerminateCalls: e2.terminations,
    videoDetached: !e2.video.connected, sourceRevoked: e2.active.revoked,
    sourceClosed: e2.active.closed || e2.active.readyState === 'closed',
    mediaStable: counts.previewJson === pj && counts.mediaRequests === mj,
    parentOpen: true, ...stoppedWorkers,
  };
  receipt.stage = 'restart';
  const secondBatch = workers.begin();
  const second = await startPlayer();
  const f2 = await second.evaluate(sampleFrames, { full: false });
  if (!f2 || !Number.isInteger(f2.frames) || f2.frames < 2 || f2.width !== 320 || f2.height !== 180 || !(f2.mediaDeltaMs > 0)) die();
  if (Array.isArray(f2.phases) && f2.phases.some(s => COLORS.indexOf(s) < 0)) die();
  receipt.restartFrames = f2.frames; receipt.restartDeltaMs = Math.floor(f2.mediaDeltaMs);
  const e3 = await readEvidence(page);
  if (!(e3.instances === 2 && e3.destroyed === 1 && e3.active && e3.active.opened === true && e3.active.revoked === false)) die();
  if (counts.previewJson !== 2 || counts.mediaRequests < mj + 1) die();
  receipt.instances = e3.instances; receipt.destroyed = e3.destroyed;
  workers.requireActive(secondBatch);
  receipt.stage = 'pagehide';
  const pj2 = counts.previewJson, mj2 = counts.mediaRequests;
  const r2 = await page.reload({ timeout: NAV_MS });
  if (!r2 || r2.status() !== 200 || !cspOkHeader(r2.headers()['content-security-policy'])) die();
  const wit = await page.evaluate(() => {
    const raw = sessionStorage.getItem('__flvLeft');
    sessionStorage.removeItem('__flvLeft');
    try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  });
  if (!(wit && wit.instances === 2 && wit.paused >= 2 && wit.destroyed === 2 && wit.revoked === true && wit.detached === true && wit.blocked === 0)) die();
  await page.waitForTimeout(SETTLE_MS);
  if (await page.locator('#player iframe').count() !== 0) die();
  if (counts.previewJson !== pj2 || counts.mediaRequests !== mj2) die();
  receipt.stage = 'pagehide-workers';
  const hiddenWorkers = await workers.released(secondBatch);
  receipt.pagehideRelease = {
    paused: wit.paused, destroyed: wit.destroyed, workerTerminateCalls: wit.terminations - e2.terminations,
    videoDetached: wit.detached, sourceRevoked: wit.revoked, mediaStable: true, ...hiddenWorkers,
  };
  const e4 = await readEvidence(page);
  if (e4.blocked !== null) receipt.cspBlocked = e4.blocked;
  if (!(counts.unexpectedHttp === 0 && counts.socketAttempts === 0 && counts.responseFailures === 0 && counts.pinFailures === 0 && e4.blocked === 0)) die();
  receipt.instances = wit.instances; receipt.destroyed = wit.destroyed;
  await verifyNaturalEnd(page, startPlayer, readEvidence, workers);
  const eof = await readEvidence(page);
  if (!workers.clean() || counts.previewJson !== 3 || counts.mediaRequests < 3 || eof.blocked !== 0
      || counts.unexpectedHttp || counts.socketAttempts || counts.responseFailures || counts.pinFailures) die();
  receipt.stage = 'done';
  receipt.result = 'pass';
}


async function run() {
  let a;
  try { a = parseArgs(process.argv.slice(2)); } catch (e) { emit(); return; }
  let browser = null, context = null;
  try {
    receipt.stage = 'setup';
    const pw = require('playwright-core');
    if (require('playwright-core/package.json').version !== WANT_PW) die();
    receipt.stage = 'launch';
    const opts = { headless: true, chromiumSandbox: true, args: ['--disable-background-networking', '--disable-quic'] };
    opts.executablePath = a.chrome || pw.chromium.executablePath();
    browser = await pw.chromium.launch(opts);
    const version = browser.version();
    if (!/^\d{1,4}(?:\.\d{1,6}){3}$/.test(version)) die();
    receipt.browserVersion = version;
    receipt.chromeSandbox = true;
    context = await browser.newContext({ serviceWorkers: 'block' });
    context.setDefaultTimeout(STEP_MS);
    context.setDefaultNavigationTimeout(NAV_MS);
    await context.routeWebSocket(/.+/, ws => { counts.socketAttempts++; try { ws.close(); } catch (e) {} });
    await context.route('**/*', onRoute(a.origin));
    await context.addInitScript({
      content: '(' + observeDocument.toString() + ')(' + JSON.stringify(a.origin) + ', ' + observePlayer.toString() + ');',
    });
    const page = await context.newPage();
    await exercisePlayer(page, a);
  } catch (e) {
    /* receipt remains fail at the last assigned stage */
  } finally {
    let bad = false;
    const netBefore = { unexpectedHttp: counts.unexpectedHttp, socketAttempts: counts.socketAttempts, responseFailures: counts.responseFailures, pinFailures: counts.pinFailures };
    if (browser) {
      const cleanup = receipt.cleanup = { pagesClosed: false, contextClosed: false, browserClosed: false };
      if (context) {
        try {
          for (const page of context.pages()) await page.close({ runBeforeUnload: false });
          cleanup.pagesClosed = context.pages().length === 0;
          await context.unrouteAll({ behavior: 'wait' });
        } catch (_) { bad = true; }
        try { await context.close(); cleanup.contextClosed = true; } catch (_) { bad = true; }
      }
      try { await browser.close(); cleanup.browserClosed = !browser.isConnected(); } catch (_) { bad = true; }
      if (!cleanup.pagesClosed || !cleanup.contextClosed || !cleanup.browserClosed) bad = true;
    }
    if (bad) { receipt.result = 'fail'; if (receipt.stage === 'done') receipt.stage = 'cleanup'; }
    if (receipt.result === 'pass' && (counts.unexpectedHttp !== netBefore.unexpectedHttp || counts.socketAttempts !== netBefore.socketAttempts || counts.responseFailures !== netBefore.responseFailures || counts.pinFailures !== netBefore.pinFailures)) { receipt.result = 'fail'; if (receipt.stage === 'done') receipt.stage = 'network'; }
  }
  emit();
}
run().catch(() => emit());
// End Playwright orchestration.
