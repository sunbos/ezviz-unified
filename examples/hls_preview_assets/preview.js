/* 本地 HLS 预览控制脚本。
 * 仅在用户点击“播放”时向同源 /preview.json 取一次地址，校验通过后构造官方
 * window.HlsPlayer 实例；不向 DOM/控制台/存储写入地址、凭据或异常原文。 */
(function () {
  'use strict';

  var statusEl = document.getElementById('status');
  var playBtn = document.getElementById('play-preview');
  var stopBtn = document.getElementById('stop-preview');
  var hostEl = document.getElementById('player');
  if (!statusEl || !playBtn || !stopBtn || !hostEl) return;

  var TEXT = {
    idle: '空闲',
    loading: '加载中',
    playing: '播放中',
    stopped: '已停止',
    failed: '播放失败'
  };

  var generation = 0; // 代次计数：停止/重新播放后让过期回调失效
  var pending = false; // 地址请求进行中
  var player = null;

  function setStatus(state) {
    statusEl.dataset.state = state;
    statusEl.textContent = TEXT[state] || state;
  }

  function refreshButtons() {
    var busy = pending || player !== null;
    playBtn.disabled = busy;
    stopBtn.disabled = !busy;
  }

  // 两个固定同源清单各自的官方解码方式：/media/preview.m3u8 用 'hard'，
  // /media/hevc.m3u8 用 'soft'；除此之外的路径一律拒绝。
  var DECODER_BY_PATH = {
    '/media/preview.m3u8': 'hard',
    '/media/hevc.m3u8': 'soft'
  };

  // 仅接受单一 {"url": "..."} 对象：同源、路径恰为两个固定清单之一、
  // 无用户名/口令/查询串/锚点。返回 {url, decoder} 或 null；decoder 仅由
  // 已知的固定路径决定，不读取地址内容，也不向 DOM/控制台回显地址。
  function previewUrl(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
    var keys = Object.keys(payload);
    if (keys.length !== 1 || keys[0] !== 'url') return null;
    var raw = payload.url;
    if (typeof raw !== 'string' || raw === '') return null;
    var u;
    try {
      u = new URL(raw, window.location.href);
    } catch (e) {
      return null;
    }
    if (u.origin !== window.location.origin) return null;
    var decoder = DECODER_BY_PATH[u.pathname];
    if (decoder === undefined) return null;
    if (u.username !== '' || u.password !== '') return null;
    if (u.search !== '' || u.hash !== '') return null;
    return { url: u.href, decoder: decoder };
  }

  // 先解除引用再 pause/destroy：destroy 期间重入的事件因 player!==p 被忽略，
  // 保证每个实例至多销毁一次。异常不外抛，仅返回是否成功。
  function destroyPlayer(p) {
    if (player === p) player = null;
    delete hostEl.dataset.decoder;
    var ok = true;
    try {
      // pause 返回 false 记为失败；无论其抛错或失败都仍执行 destroy。
      if (p.pause() === false) ok = false;
    } catch (e) {
      ok = false;
    }
    try {
      // destroy 返回 false 记为失败；官方方法返回 undefined 时不臆造 true，
      // 仅统计已观测到的显式 false 或抛错。
      if (p.destroy() === false) ok = false;
    } catch (e) {
      ok = false;
    }
    return ok;
  }

  // 仅采信官方 initSuccess 上报的 info.decoderType（'hard'/'soft'）；
  // 缺省或未知返回 null，不按请求值/别名伪造运行态证据。
  function decoderOf(info) {
    var v = info && info.decoderType;
    return (v === 'hard' || v === 'soft') ? v : null;
  }

  // timeupdate 负载可能是秒数或对象；缺省时退回读取宿主内 video 元素。
  function seconds(t) {
    if (typeof t === 'number') return t;
    if (t && typeof t === 'object') {
      if (typeof t.currentTime === 'number') return t.currentTime;
      if (typeof t.time === 'number') return t.time;
    }
    var video = hostEl.querySelector('video');
    return video ? video.currentTime : NaN;
  }

  function mount(gen, url, decoder) {
    if (gen !== generation || player !== null) {
      refreshButtons();
      return;
    }
    delete hostEl.dataset.decoder;
    var Ctor = window.HlsPlayer;
    if (typeof Ctor !== 'function') {
      setStatus('failed');
      refreshButtons();
      return;
    }
    var p;
    try {
      p = new Ctor({
        container: hostEl,
        url: url,
        staticPath: '/vendor/',
        decoderType: decoder, // 由固定清单路径唯一决定，不读取地址内容
        autoPlay: true,
        muted: true,
        volume: 0,
        isEzviz: false,
        isLive: false,
        disableCollect: true
      });
    } catch (e) {
      setStatus('failed');
      refreshButtons();
      return;
    }
    if (typeof p.on !== 'function') {
      setStatus('failed');
      destroyPlayer(p);
      refreshButtons();
      return;
    }
    player = p;
    refreshButtons();
    try {
      p.on('initSuccess', function (info) {
        if (gen !== generation || p !== player) return;
        var decoder = decoderOf(info);
        if (decoder === null) {
          delete hostEl.dataset.decoder;
        } else {
          hostEl.dataset.decoder = decoder;
        }
      });
      p.on('timeupdate', function (t) {
        if (gen !== generation || p !== player) return;
        if (seconds(t) > 0 && statusEl.dataset.state !== 'playing') {
          setStatus('playing');
        }
      });
      p.on('error', function () {
        if (gen !== generation || p !== player) return;
        setStatus('failed');
        destroyPlayer(p); // player 已置空，destroy 期间重入 error 直接返回
        refreshButtons();
      });
    } catch (e) {
      setStatus('failed');
      destroyPlayer(p);
      refreshButtons();
    }
  }

  playBtn.addEventListener('click', function () {
    if (pending || player !== null || playBtn.disabled) return;
    var gen = ++generation;
    pending = true;
    setStatus('loading');
    refreshButtons();
    fetch('/preview.json', {
      method: 'GET',
      mode: 'same-origin',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      cache: 'no-store',
      redirect: 'error'
    }).then(function (res) {
      if (!res.ok) throw new Error('preview');
      return res.json();
    }).then(function (payload) {
      if (gen !== generation) return; // 期间已被停止，丢弃不挂载
      pending = false;
      var checked = previewUrl(payload);
      if (checked === null) {
        setStatus('failed'); // 非法输出：只置失败一次，不产生任何媒体请求
        refreshButtons();
        return;
      }
      mount(gen, checked.url, checked.decoder);
    }).catch(function () {
      if (gen !== generation) return;
      pending = false;
      setStatus('failed');
      refreshButtons();
    });
  });

  stopBtn.addEventListener('click', function () {
    if (!pending && player === null) return;
    generation += 1;
    pending = false;
    var ok = true;
    if (player) ok = destroyPlayer(player);
    setStatus(ok ? 'stopped' : 'failed');
    refreshButtons();
  });

  window.addEventListener('pagehide', function () {
    generation += 1;
    pending = false;
    if (player) destroyPlayer(player);
  });

  delete hostEl.dataset.decoder;
  setStatus('idle');
  refreshButtons();
})();
