'use strict';

/*
 * 本地合成 H.264/FLV 预览控制器（子文档隔离版）。
 * 每轮点击在 #player 下新建同源 iframe 加载固定 /player.html；
 * 子文档自带官方 CSS/UMD，仅含 #flv-stage 并向自身 window 暴露 EzuikitFlv。
 * DOM 约定：#player 容器、#play-preview / #stop-preview 按钮、#status[role=status]。
 * 约束：不展示动态地址/错误详情/debug，不写 storage，不挂全局实例；
 *       不用 postMessage / srcdoc / blob，不重写原型或 Worker。
 */
(() => {
  /* ---- 固定常量 ---- */
  const CONFIG_URL = '/preview.json';                             // 同源固定配置地址
  const MEDIA_PATH = '/media/preview.flv';                        // 唯一允许的媒体相对字面值
  const APPROVED_MEDIA_URL = location.origin + MEDIA_PATH;        // 交付播放器的固定绝对同源地址
  const CHILD_PATH = '/player.html';                              // 唯一允许的同源子文档
  const APPROVED_CHILD_URL = location.origin + CHILD_PATH;
  const START_TIMEOUT_MS = 15000;                                 // 启动限时：fetch + iframe load + 真实播放反馈
  const PLAYABLE_READY_STATE = HTMLMediaElement.HAVE_FUTURE_DATA; // 3：具备向前播放数据

  /* 五态固定文案（简体中文，不随输入变化） */
  const STATE_TEXT = {
    idle: '就绪',
    starting: '正在启动',
    playing: '正在播放',
    stopped: '已停止',
    error: '播放失败',
  };

  const container = document.getElementById('player');
  const playBtn = document.getElementById('play-preview');
  const stopBtn = document.getElementById('stop-preview');
  const statusEl = document.getElementById('status');
  if (!container || !playBtn || !stopBtn || !statusEl) return;

  /* ---- 状态机会话 ---- */
  let session = null; // 同一时刻至多一个进行中会话
  let genSeq = 0;     // 代次序号，区分新旧会话

  function setState(next) {
    statusEl.dataset.state = next;
    statusEl.textContent = STATE_TEXT[next];
    // 启动/播放中禁用播放键；停止键在启动期间同样可用
    playBtn.disabled = next === 'starting' || next === 'playing';
    stopBtn.disabled = !(next === 'starting' || next === 'playing');
  }

  function newSession() {
    const s = {
      gen: ++genSeq,
      ctrl: new AbortController(),
      player: null,       // 子文档内构造的官方实例
      frame: null,        // 本轮同源 iframe
      stage: null,        // 子文档内 #flv-stage
      onFrameLoad: null,
      onFrameError: null,
      onEnded: null,      // 子 stage 上的捕获 ended 监听
      timer: 0,
      lastTime: -1,       // 上一次采样到的 video.currentTime，用于确认真实递增
      confirmed: false,   // 是否已凭真实播放反馈进入 playing
      disposed: false,
    };
    session = s;
    return s;
  }

  // 会话仍有效：未被 dispose、仍是当前会话、代次未过期
  const alive = (s) => !!s && !s.disposed && session === s && s.gen === genSeq;

  /* ---- 统一释放：回调失效 → abort/清计时器 → 清 frame 监听 → pause+destroy → 移除 iframe ---- */
  function teardown(s, next) {
    if (!s || s.disposed) return;
    s.disposed = true;
    if (session === s) session = null;
    if (s.timer) { clearTimeout(s.timer); s.timer = 0; }
    try { s.ctrl.abort(); } catch (_) { /* 忽略 */ }

    const f = s.frame;
    s.frame = null;
    if (f) {
      if (s.onFrameLoad) f.removeEventListener('load', s.onFrameLoad);
      if (s.onFrameError) f.removeEventListener('error', s.onFrameError);
    }
    s.onFrameLoad = s.onFrameError = null;
    if (s.stage && s.onEnded) s.stage.removeEventListener('ended', s.onEnded, true);
    s.onEnded = null;

    const p = s.player;
    s.player = null;
    let failed = false; // 本轮清理是否出现失败
    // 失败登记：仅当仍属本代且尚无更新会话时才把状态翻转为 error，不污染新代
    const fail = () => {
      failed = true;
      if (s.gen === genSeq && !session) setState('error');
    };
    // 观察单次调用的返回值：false 与 rejection 计失败，fulfilled（含 undefined）计成功
    const watch = (ret) => {
      if (ret === false) { fail(); return; }
      if (ret && typeof ret.then === 'function') {
        ret.then((v) => { if (v === false) fail(); }, fail);
      }
    };
    if (p) {
      // 移除 iframe 前对每个实例显式 pause 与 destroy 各一次；
      // 不 await 未决 promise，销毁信号一定同步发出
      try { watch(p.pause()); } catch (_) { fail(); }
      try { watch(p.destroy()); } catch (_) { fail(); }
    }

    // destroy 不会清除子文档内实际 Worker，无论实例是否建成都必须移除本轮 iframe；
    // 只移除自己创建的节点，不盲清 container 中可能属于新会话的内容
    if (f && f.parentNode) f.parentNode.removeChild(f);
    s.stage = null;

    if (s.gen === genSeq) {
      if (failed) setState('error');
      else if (next) setState(next);
    }
  }

  /* ---- 拉取并校验配置：仅接受 {url:"/media/preview.flv"} 原始字面值 ---- */
  async function fetchApprovedUrl(s) {
    const res = await fetch(CONFIG_URL, {
      method: 'GET',
      mode: 'same-origin',
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: s.ctrl.signal,
    });
    if (!res.ok) throw new Error('config status');
    const data = await res.json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      throw new Error('config shape');
    }
    const keys = Object.keys(data);
    if (keys.length !== 1 || keys[0] !== 'url' || typeof data.url !== 'string') {
      throw new Error('config shape');
    }
    // 不做 URL 归一化，直接比对原始字面值；命中后也只用固定绝对地址
    if (data.url !== MEDIA_PATH && data.url !== APPROVED_MEDIA_URL) {
      throw new Error('config url');
    }
    return APPROVED_MEDIA_URL;
  }

  /* ---- 新建同源子文档：配置校验成功后才会调用；src 之前置好监听与策略 ---- */
  function loadChild(s) {
    return new Promise((resolve, reject) => {
      const frame = document.createElement('iframe');
      s.frame = frame;
      s.onFrameError = () => reject(new Error('frame error'));
      s.ctrl.signal.addEventListener('abort', () => reject(new Error('frame cancelled')), { once: true });
      s.onFrameLoad = () => {
        // load 触发时子文档 defer 脚本已执行完毕
        try {
          if (!alive(s)) return;
          if (frame.parentNode !== container) throw new Error('frame detached');
          const cw = frame.contentWindow;
          if (!cw || cw.location.href !== APPROVED_CHILD_URL) {
            throw new Error('frame location');
          }
          const stage = cw.document && cw.document.getElementById('flv-stage');
          if (!stage) throw new Error('stage missing');
          const Lib = cw.EzuikitFlv; // 只取子窗构造，绝不使用 parent.window
          const ev = Lib && Lib.EVENTS;
          if (typeof Lib !== 'function' || !ev ||
              typeof ev.timeUpdate !== 'string' || typeof ev.error !== 'string') {
            throw new Error('player unavailable');
          }
          resolve({ stage, Lib, ev });
        } catch (e) {
          reject(e);
        }
      };
      frame.addEventListener('load', s.onFrameLoad);
      frame.addEventListener('error', s.onFrameError);
      frame.referrerPolicy = 'no-referrer';
      frame.title = '本地合成视频预览';
      frame.src = CHILD_PATH;
      container.appendChild(frame);
    });
  }

  /* ---- 当前会话子 stage 内的真实 video（跨窗须用子窗构造做 instanceof） ---- */
  function sessionVideo(s) {
    const stage = s.stage;
    const cw = s.frame && s.frame.contentWindow;
    if (!stage || !cw) return null;
    const v = stage.querySelector('video');
    return v instanceof cw.HTMLVideoElement ? v : null;
  }

  /* ---- 真实播放反馈：正数 timeUpdate + 原生 video 尺寸/readyState + currentTime 递增 ---- */
  function onTimeUpdate(s, sec) {
    if (!alive(s) || s.confirmed) return;
    if (!(Number.isFinite(sec) && sec > 0)) return; // 事件载荷须为有限正数秒
    const v = sessionVideo(s);
    if (!v) return;
    if (!(v.videoWidth > 0 && v.videoHeight > 0)) return; // 非零尺寸
    if (v.readyState < PLAYABLE_READY_STATE) return;      // 可播放 readyState
    const t = v.currentTime;
    if (!(typeof t === 'number' && t > 0)) return;
    if (s.lastTime < 0) { s.lastTime = t; return; } // 记录首个样本
    if (t <= s.lastTime) return;                    // 需要真实递增
    s.confirmed = true;
    if (s.timer) { clearTimeout(s.timer); s.timer = 0; } // 启动限时结束
    setState('playing');
  }

  /* ---- 官方 error：微任务延迟清理，避免在第三方派发中破坏其对象 ---- */
  function onOfficialError(s) {
    queueMicrotask(() => {
      if (!alive(s)) return; // 代次检查，丢弃过期 error
      teardown(s, 'error');
    });
  }

  /* ---- 播放入口：仅明确点击触发，先校验配置再建子文档，单会话 + 启动限时 ---- */
  async function onPlay() {
    if (session) return; // 已有进行中的 fetch/iframe/播放
    const s = newSession();
    setState('starting');
    s.timer = setTimeout(() => {
      if (alive(s)) teardown(s, 'error'); // 启动超时：error 并清理
    }, START_TIMEOUT_MS);
    try {
      const url = await fetchApprovedUrl(s); // 非法配置绝不加载 iframe/vendor/media
      if (!alive(s)) return; // 启动期间已被停止/超时，过期结果不得生效
      const { stage, Lib, ev } = await loadChild(s);
      if (!alive(s)) return;
      s.stage = stage;
      // 捕获子 stage 内真实原生 ended：确认同一 video 且 .ended，微任务后释放供重播
      s.onEnded = (e) => {
        const v = e.target;
        if (v !== stage.querySelector('video') || !v.ended) return;
        queueMicrotask(() => {
          if (alive(s) && v === stage.querySelector('video')) teardown(s, 'stopped');
        });
      };
      stage.addEventListener('ended', s.onEnded, true);
      // 官方构造即 autoPlay（不再调 play()）；以子 stage 为容器，不用 id:'player'
      const p = new Lib({
        container: stage,
        url, // 仅固定绝对同源地址，无任意地址 fallback
        staticPath: '',
        decoder: new URL('/vendor/decoder.js', location.origin).href,
        autoPlay: true,
        useMSE: true,
        autoWasm: false,
        hasAudio: false,
        muted: true,
        volume: 0,
        isLive: false,
        isEzviz: false,
        themeData: null,
        messageOptions: null,
        ignoreMonitor: true,
        debug: false,
        loggerOptions: { level: 'ERROR' },
        heartTimeoutReplayTimes: 0,
        loadingTimeoutReplayTimes: 0,
        wasmDecodeErrorReplay: false,
        mutedShowAutoReload: false,
        referrerPolicy: 'no-referrer',
      });
      s.player = p; // 先挂会话，确保后续 on() 抛错也能被释放
      p.on(ev.timeUpdate, (sec) => onTimeUpdate(s, sec));
      p.on(ev.error, () => onOfficialError(s));
    } catch (_) {
      // 所有初始化失败统一清理 frame 并显示 error；
      // 含 abort 拒绝：若会话仍有效才算失败，否则状态已由 stop/timeout 落定
      if (alive(s)) teardown(s, 'error');
    }
  }

  /* ---- 控件与离页 ---- */
  playBtn.addEventListener('click', () => { void onPlay(); });
  stopBtn.addEventListener('click', () => {
    const s = session;
    if (s) teardown(s, 'stopped');
  });
  window.addEventListener('pagehide', () => {
    const s = session;
    if (s) teardown(s, 'stopped'); // 离页显式 pause + destroy + 移除 iframe
  });

  setState('idle');
})();
