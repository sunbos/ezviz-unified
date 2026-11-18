'use strict';
/*
 * verify_hls.cjs - opt-in Playwright verifier for the official HLS preview page.
 *
 *   node tools/media/verify_hls.cjs --base http://127.0.0.1:PORT [--chrome /path]
 *
 * Prints exactly one compact JSON receipt on stdout. Exit code is nonzero on
 * any unmet condition; `stage` names the failing phase so repairs stay
 * possible. The receipt never contains URLs, element ids, request bodies, or
 * raw error text, and nothing is written to stderr except fixed argument
 * errors.
 */

const die = (m) => { process.stderr.write(m + '\n'); process.exit(2); };

// ---- strict CLI -------------------------------------------------------------
const argv = process.argv.slice(2);
let baseArg;
let chromeArg;
let chromeSet = false;
for (let i = 0; i < argv.length; i += 1) {
  if (argv[i] === '--base' && baseArg === undefined) baseArg = argv[i += 1];
  else if (argv[i] === '--chrome' && !chromeSet) {
    chromeSet = true;
    chromeArg = argv[i += 1];
  } else die('error: invalid arguments');
}
const bad = (s) => typeof s !== 'string' || s === '' || s[0] === '-';
if (bad(baseArg) || (chromeSet && bad(chromeArg))) die('error: invalid arguments');
let base = null;
try { base = new URL(baseArg); } catch (e) { die('error: invalid base url'); }
// Exact base only: http://127.0.0.1 with an explicit port -- no https, no
// other loopback spellings, no credentials, query, fragment, or subpath.
const baseOk = base.protocol === 'http:' && base.hostname === '127.0.0.1'
  && base.port !== '' && base.username === '' && base.password === ''
  && base.pathname === '/' && base.search === '' && base.hash === '';
if (!baseOk) die('error: invalid base url');

const ALLOWED = new Set(['/', '/preview.js', '/preview.json',
  '/vendor/index.umd.js', '/vendor/style/css.css', '/vendor/decoder.worker.js',
  '/vendor/decoder.wasm', '/media/preview.m3u8']);
const SEGMENT = /^\/media\/segment-\d{3}\.ts$/;
const BUNDLE_SHA256 = 'afb2f9c1f3e32c865db90aa6ee6dbb7a1ef61a90cf4911c1787ac8732fa28e1c';
const { createHash } = require('crypto');

// ---- page-side probes (serialized into the browser by page.evaluate) --------

// Spy on the REAL official global: pause/destroy are wrapped via Reflect.apply
// (returns untouched, constructor untouched). If the UMD defines the methods
// per-instance instead of on the prototype, a transparent Proxy construct/apply
// trap still returns the real instance, now with wrapped original methods.
const installSpy = () => {
  const L = (window.__life = { constructed: 0, pause: 0, destroy: 0 });
  const mark = (o, k) => {
    const f0 = o[k];
    if (typeof f0 !== 'function' || f0.__spied) return;
    const f = function (...a) { L[k] += 1; return Reflect.apply(f0, this, a); };
    f.__spied = true;
    try { o[k] = f; } catch (e) { /* keep original */ }
  };
  const P = window.HlsPlayer;
  if (!P) return false;
  if (P.prototype) { mark(P.prototype, 'pause'); mark(P.prototype, 'destroy'); }
  const wrap = (inst) => {
    if (inst && typeof inst === 'object') {
      L.constructed += 1; mark(inst, 'pause'); mark(inst, 'destroy');
    }
    return inst;
  };
  try {
    window.HlsPlayer = new Proxy(P, {
      construct: (t, a) => wrap(Reflect.construct(t, a)),
      apply: (t, g, a) => wrap(Reflect.apply(t, g, a)),
    });
  } catch (e) { /* keep original */ }
  return window.HlsPlayer !== P;
};

// Resolve the real <video> strictly under #player through open shadow roots
// (no document fallback), require requestVideoFrameCallback, and count
// presented frames until mediaTime has advanced >= 0.15s (or an 8s in-page
// cap). Resolves null if unsupported.
const framesProbe = () => new Promise((resolve) => {
  const dig = (r) => {
    const v = r.querySelector && r.querySelector('video');
    if (v) return v;
    for (const e of r.querySelectorAll('*')) {
      if (e.shadowRoot) { const x = dig(e.shadowRoot); if (x) return x; }
    }
    return null;
  };
  const host = document.getElementById('player');
  const v = host && dig(host.shadowRoot || host);
  if (!v || typeof v.requestVideoFrameCallback !== 'function') { resolve(null); return; }
  const t0 = performance.now();
  let n = 0;
  let f0 = -1;
  let f1 = -1;
  const step = (now, m) => {
    n += 1;
    if (f0 < 0) f0 = m.mediaTime;
    f1 = m.mediaTime;
    if ((n >= 2 && f1 - f0 >= 0.15) || performance.now() - t0 > 8000) {
      resolve({ n, delta: f1 - f0, w: v.videoWidth, h: v.videoHeight });
    } else v.requestVideoFrameCallback(step);
  };
  v.requestVideoFrameCallback(step);
});

// Validate the Content-Security-Policy delivered by the REAL navigation
// response headers (never the DOM -- Chrome exposes no document.csp and the
// page carries no meta policy). Directives are parsed by splitting on ';'
// and whitespace; first occurrence of a repeated directive wins. Boundary
// directives accept only quoted keyword sources: default-src must be exactly
// 'none', connect-src exactly 'self', and script-src must include 'self' and
// 'wasm-unsafe-eval'. Any wildcard or host/scheme source on these directives
// fails the check.
const cspPolicyOk = (header) => {
  if (typeof header !== 'string' || header.trim() === '') return false;
  const dir = {};
  for (const part of header.split(';')) {
    const toks = part.trim().split(/\s+/).filter(Boolean);
    const key = toks.length > 1 ? toks[0].toLowerCase() : '';
    if (key !== '' && dir[key] === undefined) dir[key] = toks.slice(1);
  }
  const quoted = (t) => t.length > 2 && t.charAt(0) === "'" && t.charAt(t.length - 1) === "'";
  const ds = dir['default-src'];
  const cs = dir['connect-src'];
  const ss = dir['script-src'];
  return Array.isArray(ds) && ds.length === 1 && ds[0] === "'none'"
    && Array.isArray(cs) && cs.length === 1 && cs[0] === "'self'"
    && Array.isArray(ss) && ss.every(quoted)
    && ss.indexOf("'self'") !== -1 && ss.indexOf("'wasm-unsafe-eval'") !== -1;
};

// Installed via context.addInitScript: count CSP-blocked external http(s)/ws(s)
// attempts BEFORE route interception. Comparison is on the full URL origin
// (scheme + host + port), so a ws(s) origin never matches the http base and
// every websocket attempt counts. Only counts are recorded -- blockedURIs
// never reach the receipt.
const cspListener = (baseOrigin) => {
  window.__cspBlocked = 0;
  window.addEventListener('securitypolicyviolation', (ev) => {
    let u = null;
    try { u = new URL(ev.blockedURI || ''); } catch (e) { u = null; }
    if (!u) return;
    const ext = (u.protocol === 'http:' || u.protocol === 'https:'
      || u.protocol === 'ws:' || u.protocol === 'wss:') && u.origin !== baseOrigin;
    if (ext) window.__cspBlocked += 1;
  });
};

const versionOf = () => {
  const P = window.HlsPlayer;
  if (!P) return '';
  if (typeof P.version === 'string') return P.version;
  try { return typeof P.getVersion === 'function' ? String(P.getVersion()) : ''; } catch (e) { return ''; }
};
const stateOf = () => {
  const el = document.getElementById('status');
  return el && el.dataset ? (el.dataset.state || '') : '';
};
const decoderOf = () => {
  const el = document.getElementById('player');
  return el && el.dataset && typeof el.dataset.decoder === 'string' ? el.dataset.decoder : '';
};
const lifeOf = () => window.__life || {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- shared state -----------------------------------------------------------
const counts = {
  manifest: 0, segment: 0, sdk: 0, negSdk: 0, unexpected: 0, ws: 0,
  wsClosed: 0, cspBlocked: 0,
};
const negPages = new Set();
let bundleSha = ''; // empty only when the UMD was fetched without a body
let docCspOk = false; // true only when the real document CSP was checked
let stage = 'startup';
let cleanupError = false; // any failed/late cleanup step marks the run failed
let browser = null;
let context = null;
let chromium = null;
let pwVersion = 'unknown';
try {
  ({ chromium } = require('playwright-core'));
  pwVersion = String(require('playwright-core/package.json').version || 'unknown');
} catch (e) { /* surfaces as a startup failure in the receipt */ }

// null below means "never measured": a failed run reports null for phases
// that produced no observation rather than asserting an invented zero. The
// net counters stay real numbers -- they are actual counts at any point.
const receipt = {
  result: 'fail', stage: 'startup', browser: 'unknown', pw: pwVersion,
  versionOk: null, versionReported: 'absent', bundleVerified: null,
  video: { w: null, h: null }, frames: { n: null, mediaDeltaMs: null }, decoder: 'absent',
  life: { constructed: null, pause: null, destroy: null, detached: null, stopped: null, mediaStable: null },
  pos: { manifest: null, segments: null, addressFetches: null, playing: null },
  neg: { failed: null, mediaBefore: null, constructed: null, addressFetches: null, mediaDelta: null, refetch: null },
  net: { unexpected: 0, ws: 0, cspBlocked: 0 },
};

// Hard bound via Promise.race in the runner below: on timeout the receipt is
// emitted once, after real cleanup in the shared finally block.
let watchdogTimer;
const watchdog = new Promise((resolve) => {
  watchdogTimer = setTimeout(() => resolve('timeout'), 90000);
  if (watchdogTimer.unref) watchdogTimer.unref();
});

// Admit only same-origin GET/HEAD on the fixed interface paths (no
// query/hash); count and refuse anything else without echoing the URL.
const onRoute = async (route) => {
  const req = route.request();
  let u = null;
  try { u = new URL(req.url()); } catch (e) { u = null; }
  const p = u ? u.pathname : '';
  const ok = !!u && u.origin === base.origin && u.search === '' && u.hash === ''
    && (req.method() === 'GET' || req.method() === 'HEAD')
    && (ALLOWED.has(p) || SEGMENT.test(p));
  if (!ok) { counts.unexpected += 1; await route.abort().catch(() => {}); return; }
  if (p === '/preview.json') {
    counts.sdk += 1;
    let neg = false;
    try { neg = negPages.has(req.frame().page()); } catch (e) { neg = false; }
    if (neg) {
      counts.negSdk += 1;
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"url":null}' })
        .catch(() => {});
      return;
    }
  }
  if (p === '/media/preview.m3u8') counts.manifest += 1;
  else if (SEGMENT.test(p)) counts.segment += 1;
  let resp;
  try {
    resp = await route.fetch({ timeout: 15000, maxRedirects: 0 });
  } catch (e) {
    counts.unexpected += 1;
    await route.abort().catch(() => {});
    return;
  }
  const st = resp.status();
  if (st >= 300 && st < 400) {
    // 3xx counted and aborted BEFORE any other network hop.
    counts.unexpected += 1;
    try { await resp.dispose(); } catch (e) {}
    await route.abort().catch(() => {});
    return;
  }
  let negReq = false;
  try { negReq = negPages.has(req.frame().page()); } catch (e) { negReq = false; }
  try {
    if (p === '/vendor/index.umd.js' && !negReq) {
      // Digest of the actual official UMD bytes as served to the page.
      bundleSha = createHash('sha256').update(await resp.body()).digest('hex');
    }
    // Pass the real response through untouched: same bytes, same headers.
    // The original document's CSP is validated in-page via cspProbe -- the
    // verifier never substitutes its own policy.
    await route.fulfill({ response: resp });
  } catch (e) {
    counts.unexpected += 1;
    await route.abort().catch(() => {});
  }
};

const collectCsp = async () => {
  // Recompute the total from per-page counters each call -- adding again on
  // top of a previous sum would double-count. Pages that can no longer be
  // evaluated are skipped (their pre-close violations were already recorded
  // by earlier collections); nothing is inferred about them.
  if (!context) return;
  let sum = 0;
  for (const pg of context.pages()) {
    try {
      sum += await pg.evaluate(() => window.__cspBlocked | 0);
    } catch (e) { /* page gone -- contributes nothing new */ }
  }
  counts.cspBlocked = sum;
};

const run = async () => {
  if (!chromium || pwVersion !== '1.57.0') throw new Error('x'); // exact pin
  const opts = {
    headless: true,
    timeout: 30000,
    chromiumSandbox: true,
    args: ['--disable-background-networking'],
  };
  if (chromeSet) opts.executablePath = chromeArg; else opts.channel = 'chrome';
  browser = await chromium.launch(opts); // isolated temp profile per launch
  receipt.browser = browser.version() || 'unknown';
  context = await browser.newContext({ serviceWorkers: 'block' });
  context.setDefaultTimeout(15000);
  await context.route('**/*', onRoute);
  if (typeof context.routeWebSocket !== 'function') throw new Error('x'); // fail closed
  await context.routeWebSocket(/.+/, (ws) => {
    counts.ws += 1;
    try { ws.close(); counts.wsClosed += 1; } catch (e) {}
  });
  await context.addInitScript(cspListener, base.origin);

  // ---- positive case ----
  const page = await context.newPage();
  const nav = await page.goto(base.href, { waitUntil: 'load', timeout: 15000 });
  if (!nav) throw new Error('x');
  await page.waitForFunction(() => Boolean(window.HlsPlayer), null, { timeout: 10000 });
  // Provenance first: the served-bytes digest measured during routing, then
  // the real navigation response headers, then the strict version pin.
  receipt.bundleVerified = bundleSha !== '' && bundleSha === BUNDLE_SHA256;
  if (!receipt.bundleVerified) throw new Error('x');
  const cspHeader = await nav.headerValue('content-security-policy').catch(() => null);
  docCspOk = cspPolicyOk(cspHeader);
  if (!docCspOk) throw new Error('x');
  const ver = await page.evaluate(versionOf).catch(() => '');
  receipt.versionReported = ver === '2.0.0' ? 'exact' : (ver === '' ? 'unreported' : 'mismatch');
  receipt.versionOk = ver === '2.0.0'; // the official bundle reports exactly 2.0.0
  if (!receipt.versionOk) throw new Error('x');
  const proxied = await page.evaluate(installSpy).catch(() => false) === true;
  if (!proxied) throw new Error('x'); // spy is required in both cases

  stage = 'positive-frames';
  await page.click('#play-preview', { timeout: 5000 });
  await page.waitForSelector('#player video', { state: 'attached', timeout: 15000 });
  const m = await Promise.race([
    page.evaluate(framesProbe).catch(() => null),
    sleep(12000).then(() => null),
  ]);
  if (m) {
    receipt.video = { w: m.w | 0, h: m.h | 0 };
    receipt.frames = { n: m.n | 0, mediaDeltaMs: Math.round(m.delta * 1000) };
  }
  if (!(m && m.n >= 2 && m.delta >= 0.15 && m.w === 320 && m.h === 180)) throw new Error('x');
  // Presented frames (requestVideoFrameCallback) and the vendor's
  // timeupdate-driven UI state advance on different cadences: wait for both
  // real markers so ordering is not a race, then still require the actual
  // values below.
  await page.waitForSelector('#status[data-state="playing"]', { state: 'attached', timeout: 5000 });
  await page.waitForFunction(
    () => document.querySelector('#player')?.dataset.decoder === 'hard',
    null, { timeout: 5000 });
  const dec = await page.evaluate(decoderOf).catch(() => '');
  receipt.decoder = dec === '' ? 'absent' : (dec === 'hard' ? 'hard' : 'other');
  receipt.pos.playing = (await page.evaluate(stateOf).catch(() => '')) === 'playing';
  if (receipt.decoder !== 'hard' || !receipt.pos.playing) throw new Error('x');

  stage = 'lifecycle';
  await page.click('#stop-preview', { timeout: 5000 });
  await page.waitForFunction(
    () => window.__life && window.__life.pause >= 1 && window.__life.destroy >= 1,
    null, { timeout: 10000 });
  await page.waitForSelector('#player video', { state: 'detached', timeout: 10000 });
  await page.waitForSelector('#status[data-state="stopped"]', { state: 'attached', timeout: 10000 });
  const med0 = counts.manifest + counts.segment;
  await sleep(650);
  const lf = await page.evaluate(lifeOf);
  receipt.life = {
    constructed: lf.constructed | 0, pause: lf.pause | 0, destroy: lf.destroy | 0,
    detached: true, stopped: true,
    mediaStable: counts.manifest + counts.segment === med0,
  };
  receipt.pos.manifest = counts.manifest;
  receipt.pos.segments = counts.segment;
  receipt.pos.addressFetches = counts.sdk;
  // Exactly one real construction, >=1 pause and >=1 destroy -- no invented
  // zero: the spied counters must reflect the actual calls.
  if (receipt.life.constructed !== 1) throw new Error('x');
  if (!(receipt.life.pause >= 1 && receipt.life.destroy >= 1 && receipt.life.mediaStable)) {
    throw new Error('x');
  }
  if (!(counts.manifest >= 1 && counts.segment >= 1 && counts.sdk === 1)) throw new Error('x');

  // ---- negative case: same context route, per-page {url:null} before mount ----
  stage = 'invalid-url';
  const negMediaBase = counts.manifest + counts.segment; // BEFORE nav/click
  const negSdkBase = counts.sdk;
  const np = await context.newPage();
  negPages.add(np);
  const navNeg = await np.goto(base.href, { waitUntil: 'load', timeout: 15000 });
  if (!navNeg) throw new Error('x');
  const cspHeaderNeg = await navNeg.headerValue('content-security-policy').catch(() => null);
  if (!cspPolicyOk(cspHeaderNeg)) throw new Error('x'); // same real policy on every document
  await np.waitForFunction(() => Boolean(window.HlsPlayer), null, { timeout: 10000 });
  const proxiedNeg = await np.evaluate(installSpy).catch(() => false) === true;
  if (!proxiedNeg) throw new Error('x'); // spy required here too
  await np.click('#play-preview', { timeout: 5000 });
  await np.waitForSelector('#status[data-state="failed"]', { state: 'attached', timeout: 10000 });
  await sleep(600);
  const nl = await np.evaluate(lifeOf);
  // The negative page's fetches of the address endpoint: exactly one
  // (satisfied locally with {url:null}) and zero additional refetches.
  const negDelta = counts.sdk - negSdkBase;
  receipt.neg = {
    failed: true,
    mediaBefore: negMediaBase,
    constructed: nl.constructed | 0,
    addressFetches: counts.negSdk,
    mediaDelta: counts.manifest + counts.segment - negMediaBase,
    refetch: negDelta - 1,
  };
  const quiet = (await np.locator('#player video').count()) === 0
    && negDelta === 1 && counts.negSdk === 1
    && receipt.neg.mediaDelta === 0 && receipt.neg.refetch === 0
    && receipt.neg.constructed === 0;
  if (!quiet) throw new Error('x');

  stage = 'network';
  await collectCsp();
  receipt.net.unexpected = counts.unexpected;
  receipt.net.ws = counts.ws;
  receipt.net.cspBlocked = counts.cspBlocked;
  if (counts.unexpected !== 0 || counts.ws !== 0 || counts.cspBlocked !== 0) {
    throw new Error('x'); // any external attempt fails
  }

  receipt.result = 'pass';
  receipt.stage = 'done';
};

(async () => {
  let timedOut = false;
  try {
    const outcome = await Promise.race([run(), watchdog]);
    if (outcome === 'timeout') { timedOut = true; stage = 'timeout'; }
  } catch (e) {
    receipt.stage = stage;
  } finally {
    // One receipt, written only after cleanup is attempted. The receipt
    // always reflects the actual measured counters -- late forbidden requests
    // are still caught here, and any cleanup failure downgrades the result.
    await collectCsp();
    receipt.net.unexpected = counts.unexpected;
    receipt.net.ws = counts.ws;
    receipt.net.cspBlocked = counts.cspBlocked;
    // Count-backed fields whose measurement was never reached take the actual
    // observed counters (a real zero is reported as zero); every other
    // unmeasured field stays null. Success values are already assigned above
    // and remain identical.
    if (receipt.pos.manifest === null) receipt.pos.manifest = counts.manifest;
    if (receipt.pos.segments === null) receipt.pos.segments = counts.segment;
    if (receipt.pos.addressFetches === null) receipt.pos.addressFetches = counts.sdk;
    if (receipt.neg.addressFetches === null) receipt.neg.addressFetches = counts.negSdk;
    if (receipt.net.unexpected !== 0 || receipt.net.ws !== 0 || receipt.net.cspBlocked !== 0) {
      receipt.result = 'fail';
      if (receipt.stage === 'done') receipt.stage = 'network';
    }
    clearTimeout(watchdogTimer);
    try { if (context) await context.close(); } catch (e) { cleanupError = true; }
    try { if (browser) await browser.close(); } catch (e) { cleanupError = true; }
    if (timedOut) { receipt.result = 'fail'; receipt.stage = 'timeout'; }
    if (cleanupError) {
      receipt.result = 'fail';
      if (receipt.stage === 'done') receipt.stage = 'cleanup';
    }
    process.stdout.write(JSON.stringify(receipt) + '\n'); // at most one receipt
    process.exit(receipt.result === 'pass' ? 0 : 1); // after cleanup
  }
})();
