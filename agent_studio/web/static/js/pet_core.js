/* ============================================================================
 * 桌宠核心（pet_core.js）—— 两个地方共用同一套「形象 + 演绎动作」
 * ----------------------------------------------------------------------------
 * 用的地方：
 *   ① 工作台里的小玩偶（agent_ui.js）—— 默认形态，贴在界面角落，跟着上课动
 *   ② 独立的小窗口（/agent/pet）—— 点「放大」以后的大图
 * 一份实现两处用，是为了避免「大窗口里好好的、小玩偶里动作不对」这类两套代码的烂账。
 *
 * 形象来源（哪来的模型）：服务端扫盘得到的清单（/agent/live2d/models），
 * 学生可以在界面上直接换（含「白-免费版」这类没有动作文件的模型）。
 *
 * 演绎动作的两条路（关键设计）：
 *   A. 模型自己有动作文件 → 按动作组名挑着播（idle/think/speak/listen…）
 *   B. 模型一个动作都没有（白-免费版、Q版小熊貓就是这种）→ **程序驱动参数**：
 *      呼吸、左右摇摆、眨眼、说话时嘴动、惊喜时瞪眼、开心时蹦两下。
 *   两条路不会打架：正在播动作文件时，参数就不写了（写了会把动作顶掉）。
 * ========================================================================== */
'use strict';

window.PetCore = (function () {
  /* ---------------- 运行时（三个 JS） ---------------- */
  const NAMES = { pixi: 'pixi.min.js', core: 'live2dcubismcore.min.js', display: 'cubism4.min.js' };
  const VENDOR = {
    pixi: '/agent/live2d/vendor/pixi.min.js',
    core: '/agent/live2d/vendor/live2dcubismcore.min.js',
    display: '/agent/live2d/vendor/cubism4.min.js',
  };
  const CDN = {
    pixi: 'https://cdn.jsdelivr.net/npm/pixi.js@7.3.2/dist/pixi.min.js',
    core: 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js',
    display: 'https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js',
  };
  let runtime = null;                       // 'local' | 'cdn'

  function loadScript(url, timeoutMs) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      const t = setTimeout(() => { s.onload = s.onerror = null; reject(new Error('超时：' + url)); }, timeoutMs || 6000);
      s.src = url;
      s.onload = () => { clearTimeout(t); resolve(url); };
      s.onerror = () => { clearTimeout(t); reject(new Error('加载失败：' + url)); };
      document.head.appendChild(s);
    });
  }

  async function loadRuntime() {
    if (runtime) return runtime;
    try {
      await loadScript(VENDOR.pixi); await loadScript(VENDOR.core); await loadScript(VENDOR.display);
      runtime = 'local';
      return runtime;
    } catch (e) { /* 本地没有就试网络 */ }
    await loadScript(CDN.pixi); await loadScript(CDN.core); await loadScript(CDN.display);
    runtime = 'cdn';
    return runtime;
  }

  /* ---------------- 演绎动作脚本 ---------------- */
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  // 基础待机（一直都在，动作文件在播的时候会被让位）
  function baseParams(t, st) {
    const p = {
      ParamBreath: 0.5 + 0.5 * Math.sin(t * 1.5),
      ParamAngleX: 7 * Math.sin(t * 0.42),
      ParamAngleY: 4 * Math.sin(t * 0.31),
      ParamAngleZ: 2.5 * Math.sin(t * 0.23),
      ParamBodyAngleX: 3 * Math.sin(t * 0.35),
      ParamBodyAngleZ: 1.5 * Math.sin(t * 0.27),
      ParamEyeBallX: 0.25 * Math.sin(t * 0.5),
      ParamEyeBallY: 0.1 * Math.sin(t * 0.37),
    };
    // 眨眼：每 2.4~5.4 秒闭一次，闭眼用 0.12 秒
    if (t >= st.nextBlink) { st.blinkAt = t; st.nextBlink = t + 2.4 + Math.random() * 3.0; }
    const since = t - (st.blinkAt || -9);
    const closed = since >= 0 && since < 0.13 ? 1 - Math.abs(since - 0.065) / 0.065 : 0;
    p.ParamEyeLOpen = 1 - clamp(closed, 0, 1);
    p.ParamEyeROpen = 1 - clamp(closed, 0, 1);
    return p;
  }

  // 一段表演：params(t) 覆盖基础参数；body(t) 给 DOM 附加位移/旋转（小玩偶的「演」）
  const ACTS = {
    think: {
      dur: 4.4,
      params: (t) => ({ ParamAngleY: 14 + 3 * Math.sin(t * 1.6), ParamAngleX: 6 * Math.sin(t * 1.1),
                        ParamEyeBallY: 0.6, ParamMouthForm: -0.2, ParamBrowLY: 0.3, ParamBrowRY: 0.3 }),
      body: (t) => ({ rot: -2 + 1.5 * Math.sin(t * 1.3) }),
    },
    speak: {
      dur: 4.2,
      params: (t) => ({ ParamMouthOpenY: 0.25 + 0.45 * Math.abs(Math.sin(t * 8.2)),
                        ParamMouthForm: 0.55, ParamAngleX: 5 * Math.sin(t * 2.4),
                        ParamAngleZ: 2 * Math.sin(t * 1.9), ParamParamCheek: 0.4, ParamCheek: 0.4 }),
      body: (t) => ({ y: -1.2 * Math.abs(Math.sin(t * 4.1)) }),
    },
    listen: {
      dur: 3.8,
      params: (t) => ({ ParamAngleY: 5 * Math.sin(t * 2.6), ParamAngleX: 3 * Math.sin(t * 0.9),
                        ParamEyeLOpen: 1.08, ParamEyeROpen: 1.08, ParamMouthForm: 0.3 }),
      body: (t) => ({ rot: 1.2 * Math.sin(t * 1.7) }),
    },
    happy: {
      dur: 2.8,
      params: (t) => ({ ParamEyeLOpen: (t % 0.7) < 0.5 ? 0.25 : 1, ParamEyeROpen: (t % 0.7) < 0.5 ? 0.25 : 1,
                        ParamMouthForm: 1, ParamMouthOpenY: 0.45, ParamAngleZ: 5 * Math.sin(t * 3.4) }),
      body: (t) => ({ y: -Math.abs(Math.sin(t * 3.6)) * 11, rot: 2.5 * Math.sin(t * 1.8) }),
    },
    surprise: {
      dur: 2.0,
      params: (t) => ({ ParamEyeLOpen: 1.45, ParamEyeROpen: 1.45, ParamMouthOpenY: 0.6,
                        ParamAngleZ: 7 * Math.sin(t * 9.5) }),
      body: (t) => ({ x: 3.5 * Math.sin(t * 10), rot: 3 * Math.sin(t * 9) }),
    },
    nod: { dur: 1.7, params: (t) => ({ ParamAngleY: 15 * Math.sin(t * 6.2) }), body: () => ({}) },
    shake: { dur: 1.7, params: (t) => ({ ParamAngleX: 15 * Math.sin(t * 7.4), ParamAngleZ: 5 * Math.sin(t * 7.4) }),
             body: (t) => ({ x: 4 * Math.sin(t * 7.4) }) },
    hop: { dur: 1.5, params: () => ({ ParamMouthForm: 0.8 }), body: (t) => ({ y: -Math.abs(Math.sin(t * 4.2)) * 12 }) },
    tap: { dur: 1.6, params: (t) => ({ ParamEyeLOpen: 0.4 + 0.6 * Math.abs(Math.sin(t * 5)), ParamMouthForm: 0.9,
                                       ParamMouthOpenY: 0.3 }), body: (t) => ({ y: -Math.abs(Math.sin(t * 4.6)) * 9,
                                                                                rot: 3 * Math.sin(t * 3.2) }) },
  };

  // 动作组名匹配（每个模型的动作名都不一样，只能按关键词找）
  // 顺序有讲究：**精确匹配那一轮会按这个顺序找**，所以更具体的名字要排前面
  // （FlickDown 要排在 Flick 前面，否则「惊讶」永远只会播到 Flick）。
  // 另外 tap/flick 这类名字不是「开心」，但拿它当反应动作比什么都不播强 ——
  // hiyori 那套官方免费模型就只有 Idle/Flick/Tap，别浪费了。
  const GROUP_HINTS = {
    idle: ['idle', 'wait', 'stand', 'loop', '呼吸'],
    think: ['think', 'ponder', '思考'],
    speak: ['speak', 'talk', 'voice', 'say', '说'],
    listen: ['listen', 'hear', '听'],
    happy: ['happy', 'joy', 'smile', 'greet', 'hello', '开心', 'tap', 'flick'],
    surprise: ['surprise', 'shock', 'alert', 'flickdown', 'flickup', 'flick3', 'flick', '惊'],
    tap: ['tap', 'touch', 'click', '点'],
    nod: ['nod', 'tap', 'tapbody'],
  };

  function pickGroup(groups, kind) {
    if (!groups || !groups.length) return null;
    const hints = GROUP_HINTS[kind] || [];
    const flat = (s) => String(s).toLowerCase().replace(/[_\-\s\d@]/g, '');
    const lower = groups.map((g) => [g, flat(g)]);
    for (const h of hints) {                       // 先精确（同名/去掉下划线数字后同名）
      for (const [g, l] of lower) if (l === h) return g;
    }
    for (const h of hints) {                       // 再包含
      for (const [g, l] of lower) if (l.indexOf(h) >= 0) return g;
    }
    return null;
  }

  // 备用形象（服务端画好的那只）用什么表情
  const SPRITE_STATE = {
    idle: 'idle', think: 'think', speak: 'happy', listen: 'happy',
    happy: 'happy', surprise: 'alert', nod: 'blink', shake: 'alert', hop: 'happy', tap: 'happy',
  };

  /* ---------------- 小玩偶本体 ---------------- */
  function createPet(host, opts) {
    opts = opts || {};
    const cfg = {
      mode: opts.mode || 'doll',          // doll=小玩偶（半身、小）｜full=整身（大窗口）
      model: opts.model || null,          // {url, name, motion_groups, ...}
      bubble: opts.bubble !== false,
      onStatus: opts.onStatus || function () {},
      speechSeconds: opts.speechSeconds || 8,
    };

    host.classList.add('pet-root');
    // **只清掉自己上次留下的东西**，绝不 host.innerHTML='' ——
    // 宿主容器里可能还有别人（聊天抽屉、工具栏），一把清空会把它们也删掉
    // （踩过：独立窗口页的聊天抽屉一度被清没，「跟她说句话」点了没反应）。
    function clearOwn() {
      Array.prototype.slice.call(host.children).forEach((child) => {
        if (child.classList && (child.classList.contains('pet-body') || child.classList.contains('pet-bubble'))) {
          child.remove();
        }
      });
    }
    clearOwn();
    const wrap = document.createElement('div');       // 「身体」：CSS 变换就加在它身上
    wrap.className = 'pet-body pet-body--' + cfg.mode;
    let canvas = document.createElement('canvas');
    canvas.className = 'pet-canvas';
    const img = document.createElement('img');
    img.className = 'pet-sprite';
    img.alt = '桌宠';
    img.style.display = 'none';
    wrap.appendChild(canvas);
    wrap.appendChild(img);
    host.appendChild(wrap);
    let bubbleEl = null;
    if (cfg.bubble) {
      bubbleEl = document.createElement('div');
      bubbleEl.className = 'pet-bubble';
      host.appendChild(bubbleEl);
    }

    const st = {
      app: null, model: null, kind: 'sprite', groups: [], suppressedUntil: 0,
      act: null, nextBlink: 1.2 + Math.random() * 2, blinkAt: -9, timer: null,
      destroyed: false, bubbleTimer: null, spriteTimer: null, status: 'loading',
    };

    const now = () => performance.now() / 1000;

    function say(text, seconds) {
      if (!bubbleEl || !text) return;
      bubbleEl.textContent = text;
      bubbleEl.classList.add('on');
      clearTimeout(st.bubbleTimer);
      st.bubbleTimer = setTimeout(() => bubbleEl.classList.remove('on'), (seconds || cfg.speechSeconds) * 1000);
    }

    function setStatus(s) { st.status = s; cfg.onStatus(s); }

    /* ---- 取景：小玩偶看头+肩，大窗口看整身 ---- */
    function fit() {
      const m = st.model;
      if (!m || !st.app) return;
      const box = host.getBoundingClientRect();
      const w = Math.max(120, Math.round(box.width) || 220);
      const h = Math.max(120, Math.round(box.height) || 260);
      try { st.app.renderer.resize(w, h); } catch (e) {}
      const im = m.internalModel || {};
      const baseW = im.originalWidth || m.width;
      const baseH = im.originalHeight || m.height;
      if (!baseW || !baseH) return;
      if (cfg.mode === 'doll') {
        // 小玩偶：只露出**画布上方那一截**（头 + 肩），左右允许裁掉一些 ——
        // 玩偶特写本来就是这样的取景。用「画布顶边贴住框顶 + 露出比例」来算，
        // 不去猜每个模型的留白（不同模型的画布比例差很多，实测 2500×2500 ~ 4000×6000）。
        const frac = cfg.dollFrac || 0.32;        // 想露出画布上方这一截（越小越特写）
        const zoom = cfg.dollZoom || 1.0;
        const sByH = h / (frac * baseH);
        const sByW = (w * 1.5) / baseW;           // 最多裁掉两侧各 1/3
        const s = Math.min(sByH, sByW) * zoom;
        m.scale.set(s);
        m.anchor.set(0.5, 1);
        m.x = w / 2;
        m.y = baseH * s;                 // 画布顶边 = y − baseH*s = 0
      } else {
        const s = Math.min(w / baseW, h / baseH) * 0.98;
        m.scale.set(s);
        m.anchor.set(0.5, 1);            // 锚点放脚底：立绘最自然的贴地方式
        m.x = w / 2;
        m.y = h;
      }
    }

    /* ---- 参数写入：在动作计算之后、物理之前（库的 afterMotionUpdate 就是干这个的）---- */
    function setParam(cm, id, v) {
      if (!cm) return;
      if (!(id in st.paramOk)) {
        try { st.paramOk[id] = cm.getParameterIndex(id) >= 0; } catch (e) { st.paramOk[id] = false; }
      }
      if (!st.paramOk[id]) return;
      try { cm.setParameterValueById(id, v); } catch (e) { st.paramOk[id] = false; }
    }
    st.paramOk = {};

    function writeParams() {
      if (!st.model || st.destroyed) return;
      const t = now();
      if (t < st.suppressedUntil) return;          // 正在播动作文件：参数让位，别打架
      const cm = st.model.internalModel && st.model.internalModel.coreModel;
      if (!cm) return;
      const merged = baseParams(t, st);
      if (st.act) {
        const conf = ACTS[st.act.kind];
        const local = t - st.act.start;
        if (conf && local <= conf.dur) Object.assign(merged, conf.params(local));
        else st.act = null;
      }
      for (const id in merged) setParam(cm, id, merged[id]);
    }

    function writeBody() {
      const t = now();
      let tr = '';
      if (st.act) {
        const conf = ACTS[st.act.kind];
        const local = t - st.act.start;
        if (conf && conf.body && local <= conf.dur) {
          const b = conf.body(local) || {};
          tr = 'translate(' + (b.x || 0) + 'px,' + (b.y || 0) + 'px)' +
               (b.rot ? ' rotate(' + b.rot + 'deg)' : '');
        }
      }
      if (wrap.style.transform !== tr) wrap.style.transform = tr;
    }

    function tick() { writeParams(); writeBody(); }

    /* 每次加载 Live2D 都要**换一块新画布**：一个 canvas 只能有一个 WebGL 上下文，
       上一块在 app.destroy() 之后上下文已经失效，拿它再建 PIXI 会直接失败。
       （换形象后掉回备用形象，根因就是这个 —— 报错还很不明显。） */
    function freshCanvas() {
      const c = document.createElement('canvas');
      c.className = 'pet-canvas';
      if (canvas && canvas.parentNode) canvas.parentNode.replaceChild(c, canvas);
      else wrap.insertBefore(c, img);
      canvas = c;
      return c;
    }

    /* 备用形象退场：Live2D 就绪后把图片和它的定时器收掉 */
    function retireSprite() {
      clearInterval(st.spriteTimer); st.spriteTimer = null;
      if (st.raf) { cancelAnimationFrame(st.raf); st.raf = null; }
      canvas.style.display = 'block';
      img.style.display = 'none';
    }

    async function useLive2D(modelInfo) {
      const mode = await loadRuntime();
      const url = modelInfo.url + (modelInfo.url.indexOf('?') >= 0 ? '&' : '?') + 'v=' + Date.now();
      const view = freshCanvas();
      const app = new PIXI.Application({
        view: view, transparent: true, autoStart: true, backgroundAlpha: 0,
        width: Math.max(160, host.clientWidth || 220), height: Math.max(160, host.clientHeight || 260),
      });
      const model = await PIXI.live2d.Live2DModel.from(url, { autoInteract: false });
      app.stage.addChild(model);
      st.app = app; st.model = model; st.kind = mode === 'local' ? 'live2d_local' : 'live2d_cdn';
      img.style.display = 'none'; canvas.style.display = 'block';
      // 动作组以**模型自己声明的**为准（清单里的可能过期，比如刚换了文件）
      try {
        const defs = (model.internalModel && model.internalModel.motionManager &&
                      model.internalModel.motionManager.definitions) || {};
        const live = Object.keys(defs).filter((k) => (defs[k] || []).length);
        st.groups = live.length ? live : (modelInfo.motion_groups || []);
      } catch (e) { st.groups = modelInfo.motion_groups || []; }

      const im = model.internalModel;
      if (im && im.on) im.on('afterMotionUpdate', tick);
      else if (app.ticker) app.ticker.add(tick);      // 兜底：至少每帧都算一次

      fit();
      setTimeout(fit, 300);
      setTimeout(fit, 1200);
      const idleGroup = pickGroup(st.groups, 'idle');
      if (idleGroup) { try { model.motion(idleGroup); } catch (e) {} }
      retireSprite();
      setStatus(st.kind);
      return st.kind;
    }

    function useSprite(reason) {
      st.kind = 'sprite';
      canvas.style.display = 'none';
      img.style.display = 'block';
      const setFrame = (state) => { img.src = '/agent/pet/sprite?state=' + state + '&scale=2&t=' + Date.now(); };
      st.setFrame = setFrame;
      setFrame('idle');
      // 备用形象也「活」起来：换表情 + 由 CSS 变换做动作
      clearInterval(st.spriteTimer);
      st.spriteTimer = setInterval(() => {
        if (st.destroyed) return;
        if (st.act) return;                       // 正在表演就不抢表情
        setFrame(Math.random() < 0.3 ? 'blink' : 'idle');
      }, 3200);
      const raf = () => {
        if (st.destroyed) return;
        writeBody();
        st.raf = requestAnimationFrame(raf);
      };
      st.raf = requestAnimationFrame(raf);
      setStatus('sprite');
      if (reason) console.warn('[pet] 用备用形象：' + reason);
      return 'sprite';
    }

    async function start(modelInfo) {
      const info = modelInfo || cfg.model;
      if (!info || !info.url) { useSprite('没有拿到形象信息'); return st.kind; }
      // **先把角色放出来**（备用形象是服务端画好的，秒出），再去拿 Live2D。
      // 为什么必须这样：Live2D 的脚本要从外网拿，网络被挡时不会报错、只会一直挂着，
      // 结果是用户对着空白等半分钟（这个坑踩过一次）。
      useSprite('正在加载 Live2D 运行时…');
      let timedOut = false;
      const live = useLive2D(info);
      const cap = new Promise((_, rej) => setTimeout(() => {
        timedOut = true;
        rej(new Error('Live2D 运行时 9 秒内没到（网络可能被挡）'));
      }, 9000));
      try {
        await Promise.race([live, cap]);
      } catch (e) {
        st.liveError = String((e && e.message) || e);
        if (timedOut) {
          // 迟到的成功也接住：不该因为我们设了超时就永远看不到 Live2D
          live.then(() => { setStatus(st.kind); }).catch(() => {});
        }
      }
      return st.kind;
    }

    /* ---- 对外：演一段 ---- */
    function act(kind) {
      if (st.destroyed) return;
      const conf = ACTS[kind] || ACTS[kind === 'idle' ? 'idle' : 'tap'];
      if (!conf) return;
      if (st.kind !== 'sprite' && st.model) {
        const group = pickGroup(st.groups, kind);
        if (group) {
          try {
            st.model.motion(group);
            // 动作文件在播的这段时间，参数脚本让位（否则两边抢同一批参数）
            let dur = 3.0;
            try {
              const defs = st.model.internalModel.motionManager.definitions[group] || [];
              const ds = defs.map((d) => Number(d && d.duration) || 0).filter((x) => x > 0);
              if (ds.length) dur = Math.min(8, Math.max.apply(null, ds));
            } catch (e) {}
            st.suppressedUntil = now() + dur;
            st.act = null;
            return;
          } catch (e) { /* 播不了就走参数演绎 */ }
        }
      }
      st.act = { kind: kind, start: now() };
      if (st.kind === 'sprite' && st.setFrame) {
        st.setFrame(SPRITE_STATE[kind] || 'idle');
        setTimeout(() => { if (!st.destroyed && !st.act) st.setFrame('idle'); }, (conf.dur + 0.4) * 1000);
      }
    }

    function setModel(modelInfo) {
      cfg.model = modelInfo;
      destroy(true);
      st.destroyed = false;
      st.paramOk = {};
      st.groups = [];
      st.act = null;
      return start(modelInfo);
    }

    /* 取景微调：滚轮缩放（每个模型的留白不一样，让用户自己捏一下最省事） */
    function setZoom(z) {
      cfg.dollZoom = Math.max(0.55, Math.min(2.4, Number(z) || 1));
      fit();
      return cfg.dollZoom;
    }

    function destroy(keepDom) {
      st.destroyed = true;
      clearTimeout(st.bubbleTimer);
      clearInterval(st.spriteTimer);
      if (st.raf) cancelAnimationFrame(st.raf);
      try { window.removeEventListener('resize', fit); } catch (e) {}
      try { if (st.app) st.app.destroy(false, { children: true }); } catch (e) {}
      st.app = null; st.model = null;
      if (!keepDom) { clearOwn(); host.classList.remove('pet-root'); }
    }

    window.addEventListener('resize', fit);
    const api = {
      start: start, act: act, say: say, fit: fit, setModel: setModel, setZoom: setZoom, destroy: destroy,
      get zoom() { return cfg.dollZoom || 1; },
      get status() { return st.status; },
      get kind() { return st.kind; },
      get groups() { return st.groups.slice(); },
      get hasMotions() { return st.groups.length > 0; },
      get model() { return st.model; },
      host: host,
      choose: function (kind) {           // 有动作就播动作，没有就参数演绎（外部不用关心）
        if (pickGroup(st.groups, kind)) return 'motion';
        return st.kind === 'sprite' ? 'sprite' : 'params';
      },
      tap: function () { act('tap'); },
    };
    return api;
  }

  /* ---------------- 事件（用的是服务端那条桌宠总线） ---------------- */
  function subscribe(onEvent) {
    try {
      const es = new EventSource('/agent/pet/events');
      es.onmessage = (m) => {
        let ev; try { ev = JSON.parse(m.data); } catch (e) { return; }
        onEvent(ev);
      };
      return es;
    } catch (e) {
      return null;
    }
  }

  // 事件 → 该演哪一段（两个界面共用，口径一致）
  function actFor(event) {
    const t = String((event && event.type) || '');
    const d = (event && event.data) || {};
    if (t === 'run.start') return 'think';
    if (t === 'run.end') return 'speak';
    if (t === 'tool.result') return d.ok ? 'happy' : 'surprise';
    if (t === 'quiz') return 'happy';
    if (t === 'reminder' || t === 'alert') return 'surprise';
    if (t === 'notice' || t === 'filed') return 'nod';
    return null;
  }

  /* ---------------- 形象清单与切换 ---------------- */
  async function catalog() {
    const r = await fetch('/agent/live2d/models');
    if (!r.ok) throw new Error('读形象清单失败 HTTP ' + r.status);
    return r.json();
  }

  async function setModel(id) {
    const r = await fetch('/agent/pet/model', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error((data && data.detail) || '换形象失败');
    return data;
  }

  /* 换形象的小弹层：两个界面共用（自己带一点内联样式，免得依赖各自的 CSS）
   *
   * 每一行要**足够让学生自己判断**，不然清单就是一串看不出差别的外文目录名：
   *   ← 人话名字（label）+ ✓ 当前选的
   *   ← 自带动作 / 程序演绎动作（决定它会不会自己动）
   *   ← 体积（12.5MB vs 14.2MB，下不下得动、占不占地方）
   * 名字和提示都靠 DOM 文本节点写进去（不用 innerHTML 拼），所以模型名里带
   * `-`、空格、中文、甚至 `<` 都不会出问题。
   */
  function modelRow(m, cur, onPick, close) {
    const row = document.createElement('div');
    row.className = 'pet-pick-row' + (m.id === cur ? ' on' : '');
    row.dataset.modelId = m.id;
    row.style.cssText = 'display:flex;gap:10px;align-items:center;padding:8px;border-radius:9px;cursor:pointer';
    row.onmouseenter = () => { row.style.background = 'rgba(0,103,192,.08)'; };
    row.onmouseleave = () => { row.style.background = ''; };

    const nm = document.createElement('div');
    nm.style.cssText = 'flex:1;min-width:0';
    const top = document.createElement('div');
    top.className = 'pet-pick-name';
    top.style.cssText = 'font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
    top.textContent = m.label || m.name || m.id;
    if (m.id === cur) {
      const tick = document.createElement('span');
      tick.className = 'pet-pick-now';
      tick.textContent = ' ✓ 当前';
      tick.style.cssText = 'color:#2F7D5B;font-weight:700';
      top.appendChild(tick);
    }
    const meta = document.createElement('div');
    meta.className = 'pet-pick-meta';
    meta.style.cssText = 'font-size:11.5px;color:#6E7A8F';
    // 「自带动作」= 模型里有动作文件，会按组名播；「程序演绎」= 没动作，靠参数驱动（呼吸/眨眼/嘴动）
    meta.textContent = (m.has_motions ? '自带动作' : '程序演绎动作') +
      (m.has_motions && (m.motion_groups || []).length ? '（' + m.motion_groups.length + ' 组）' : '') +
      ' · ' + (m.size_mb || 0) + 'MB';
    nm.appendChild(top);
    nm.appendChild(meta);
    row.appendChild(nm);
    row.title = m.dir || '';                      // 鼠标停一下能看到它在硬盘哪儿（排查用）

    row.onclick = async () => {
      if (m.id === cur) { close(); return; }       // 点当前这个 = 什么都不用做
      row.style.opacity = '.5';
      try {
        const res = await setModel(m.id);
        close();
        if (onPick) onPick(res.current || m, m);
      } catch (e) {
        row.style.opacity = '1';
        alert('换形象没成功：' + (e.message || e));
      }
    };
    return row;
  }

  function openPicker(anchor, opts) {
    opts = opts || {};
    const old = document.getElementById('pet-picker');
    if (old) old.remove();
    const box = document.createElement('div');
    box.id = 'pet-picker';
    box.style.cssText = 'position:fixed;z-index:99;min-width:262px;max-width:340px;max-height:60vh;overflow:auto;' +
      'background:#fff;color:#16233D;border:1px solid rgba(31,46,77,.18);border-radius:12px;' +
      'box-shadow:0 14px 34px rgba(31,46,77,.22);padding:8px;font:13px/1.5 "Microsoft YaHei",system-ui,sans-serif';
    const r = anchor.getBoundingClientRect();
    box.style.left = Math.max(8, Math.min(window.innerWidth - 300, r.left)) + 'px';
    box.style.top = Math.max(8, Math.min(window.innerHeight - 260, r.bottom + 6)) + 'px';
    const title = document.createElement('div');
    title.textContent = opts.title || '换个形象';
    title.style.cssText = 'font-weight:700;padding:4px 6px 8px';
    box.appendChild(title);

    const list = document.createElement('div');
    list.className = 'pet-pick-list';
    box.appendChild(list);

    // 关掉弹层。函数声明会提升，所以上面 modelRow 里调它没问题。
    // （但 modelRow 是**模块级**函数，看不到这里的局部名字 —— 关闭动作必须当参数传进去，
    //   第一版就是漏了传参，点任何一行都抛 ReferenceError，弹一句「换形象没成功」。）
    function boxRemove() { box.remove(); }
    box.dismiss = boxRemove;

    catalog().then((data) => {
      const cur = (data.current && data.current.id) || opts.current || '';
      const models = (data.models || []).slice();
      // 当前选中的排最前面：学生点开多半是想确认「现在用的是哪个」
      models.sort((a, b) => (a.id === cur ? -1 : 0) - (b.id === cur ? -1 : 0));
      models.forEach((m) => list.appendChild(modelRow(m, cur, opts.onPick, boxRemove)));
      if (!models.length) {
        const none = document.createElement('div');
        none.textContent = '没扫到任何 Live2D 形象。';
        none.style.cssText = 'padding:8px;color:#96A0B2';
        list.appendChild(none);
      }
      const tip = document.createElement('div');
      tip.className = 'pet-pick-tip';
      tip.textContent = '清单是扫你电脑上的 Live2D 模型得到的（项目里、桌面、下载都算）。要加目录：' +
        'data/pet_model_dirs.json 一行一个路径。';
      tip.style.cssText = 'font-size:11.5px;color:#96A0B2;padding:6px 8px 2px';
      box.appendChild(tip);
    }).catch((e) => {
      const err = document.createElement('div');
      err.style.cssText = 'padding:8px;color:#B4762A';
      err.textContent = '读不到形象清单：' + (e.message || e) + '（服务没在跑？双击 start_agent.cmd）';
      box.appendChild(err);
    });

    document.body.appendChild(box);
    setTimeout(() => {
      off = (ev) => {
        if (!box.contains(ev.target) && ev.target !== anchor) {
          box.remove(); document.removeEventListener('mousedown', off);
        }
      };
      document.addEventListener('mousedown', off);
    }, 0);
    return box;
  }

  return {
    createPet: createPet, loadRuntime: loadRuntime, subscribe: subscribe, actFor: actFor,
    catalog: catalog, setModel: setModel, openPicker: openPicker, pickGroup: pickGroup,
    ACTS: ACTS, GROUP_HINTS: GROUP_HINTS, SPRITE_STATE: SPRITE_STATE, baseParams: baseParams,
  };
})();
