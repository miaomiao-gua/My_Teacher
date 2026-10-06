/* ============================================================================
 * 网页版桌宠（/agent/pet）—— 「放大」以后的独立小窗口
 * ----------------------------------------------------------------------------
 * 形象与演绎动作全在 pet_core.js 里（工作台里的小玩偶用的是同一份），
 * 这个文件只负责：页面上的壳（状态牌 / 按钮 / 聊天抽屉）+ 把桌宠总线的事件接上。
 * ========================================================================== */
'use strict';

(function () {
  const el = (id) => document.getElementById(id);
  const state = { pet: null, model: null };

  function say(text, seconds) { if (state.pet) state.pet.say(text, seconds); }

  function setStatus(kind) {
    const chip = el('mode');
    const map = {
      live2d_local: ['Live2D（本机运行时）', 'ok'],
      live2d_cdn: ['Live2D（网络运行时）', 'ok'],
      sprite: ['备用形象', 'warn'],
      loading: ['加载中…', 'muted'],
    };
    const info = map[kind] || map.loading;
    chip.textContent = info[0];
    chip.dataset.tone = info[1];
    if (kind === 'live2d_cdn') offerOffline();          // 网络来的 → 顺手给「存到本机」
    // 这行诊断只在**真的退到备用形象**时才有意义。以前不管什么情况都写「没拿到」，
    // 于是用了本机运行时也显示「Live2D 运行时没拿到」——自己把自己说糊涂了。
    if (kind === 'sprite') {
      el('diag').textContent = (state.pet && state.pet.liveError) || 'Live2D 运行时没拿到';
    } else if (kind && kind.indexOf('live2d') === 0) {
      el('diag').textContent = '';
    }
  }

  /* 浏览器有网、这台机器没网时用得上：把运行时下载下来交给服务端存到本机 */
  async function offerOffline() {
    const btn = el('save');
    if (!btn || btn.dataset.done) return;
    btn.style.display = 'inline-block';
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = '正在保存…';
      try {
        const files = {};
        const urls = {
          'pixi.min.js': 'https://cdn.jsdelivr.net/npm/pixi.js@7.3.2/dist/pixi.min.js',
          'live2dcubismcore.min.js': 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js',
          'cubism4.min.js': 'https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js',
        };
        for (const name in urls) {
          const r = await fetch(urls[name]);
          if (!r.ok) throw new Error(name + ' HTTP ' + r.status);
          files[name] = await r.text();
        }
        const resp = await fetch('/agent/live2d/vendor', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ files: files }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) throw new Error(data.detail || '服务端没存下');
        btn.textContent = '已存到本机 ✓';
        btn.dataset.done = '1';
        say('存好了！以后不联网也能看到我');
        setTimeout(() => { btn.style.display = 'none'; }, 2500);
      } catch (e) {
        btn.disabled = false;
        btn.textContent = '存到本机（重试）';
        say('这次没存上：' + (e.message || e));
      }
    };
  }

  /* ---------------- 小聊天：和主界面同一个 agent ---------------- */
  // 当前会话 id（看屏幕那条链路也要用它当上下文）
  let currentSession = null;
  async function latestSession() {
    if (currentSession) return currentSession;
    try {
      const r = await fetch('/agent/sessions');
      const data = await r.json();
      let best = null;
      (data.tree || []).forEach((s) => (s.textbooks || []).forEach((b) => (b.sessions || []).forEach((x) => {
        if (!best || (x.updated_at || 0) > (best.updated_at || 0)) best = x;
      })));
      currentSession = best && best.id;
    } catch (e) { /* 没有历史就用新对话 */ }
    return currentSession;
  }

  /* 看一眼屏幕：把问题（可空）发给服务端，服务端自己截屏 + 交给视觉模型。
     服务端会把答案推到桌宠总线上，所以气泡会自己出现 —— 这里只负责把对话记进日志。 */
  async function lookAtScreen(question) {
    const log = el('log');
    const q = (question || '').trim();
    log.textContent += (q ? ('我（带屏幕截图）：' + q) : '我：看一眼我的屏幕\n');
    if (state.pet) state.pet.act('think');
    try {
      const sid = await latestSession();
      const r = await fetch('/agent/vision/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sid ? { question: q, session_id: sid } : { question: q }),
      });
      const data = await r.json();
      log.textContent += '老师：' + (data.answer || '（没答上来）') + '\n\n';
      if (state.pet) state.pet.act(data.ok ? 'speak' : 'surprise');
      if (data.answer && state.pet) state.pet.say(data.answer);
    } catch (err) {
      log.textContent += '（看屏幕失败：' + err.message + '）\n\n';
    }
    log.scrollTop = log.scrollHeight;
  }

  async function send(text) {
    if (!text.trim()) return;
    // 勾了「带屏幕截图」→ 走看图那条链路（学生要的就是"打字+回车自动截图"）
    const withShot = el('withShot');
    if (withShot && withShot.checked) { await lookAtScreen(text); el('ask').value = ''; return; }
    const log = el('log');
    log.textContent += '我：' + text + '\n';
    el('ask').value = '';
    if (state.pet) state.pet.act('think');
    let sid = null;
    try {
      const r = await fetch('/agent/sessions');
      const data = await r.json();
      let best = null;
      (data.tree || []).forEach((s) => (s.textbooks || []).forEach((b) => (b.sessions || []).forEach((x) => {
        if (!best || (x.updated_at || 0) > (best.updated_at || 0)) best = x;
      })));
      sid = best && best.id;
    } catch (e) { /* 没有历史就用新对话 */ }
    try {
      const resp = await fetch('/agent/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sid ? { message: text, session_id: sid } : { message: text }),
      });
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      log.textContent += '老师：';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        parts.forEach((p) => {
          const line = p.split('\n').find((l) => l.startsWith('data:'));
          if (!line) return;
          let ev; try { ev = JSON.parse(line.slice(5)); } catch (e) { return; }
          if (ev.type === 'text') log.textContent += ev.delta || '';
          else if (ev.type === 'notice' && ev.data && ev.data.message) log.textContent += '\n（' + ev.data.message + '）\n';
          else if (ev.type === 'run.end') log.textContent += '\n\n';
        });
        log.scrollTop = log.scrollHeight;
      }
    } catch (e) {
      log.textContent += '\n[连不上服务：' + e.message + ']\n';
    }
    if (state.pet) state.pet.act('speak');
  }

  /* 浏览器里没有真正的「置顶窗口」权限，能做的是把选择**记下来**：
     桌面版启动小窗时按这个偏好决定要不要一直显示在最前（见 desktop/core.py）。
     记住的地方有两处，各有各的用处，别删任何一个：
       - localStorage：网页自己刷新后立刻能恢复（不等网络）；
       - 服务端 data/pet_window.json：**桌面版启动器**要读它（它是另一个进程，
         读不到浏览器的 localStorage）—— 少了这条，桌面版的置顶开关就是假的。 */
  const PREF_KEY = 'pet_win_prefs_v1';

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function savePrefs(patch) {
    const next = Object.assign(loadPrefs(), patch);
    try { localStorage.setItem(PREF_KEY, JSON.stringify(next)); } catch (e) { /* 无痕模式就算了 */ }
    fetch('/agent/pet/window', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }).catch(() => { /* 服务没在跑也不影响网页本身 */ });
    return next;
  }

  /** 打开时先按本机的记住值上屏，再拉一次服务端（桌面版/别的窗口改过的话以服务端为准） */
  async function applyPrefs() {
    paintPrefs(loadPrefs());
    try {
      const r = await fetch('/agent/pet/window');
      const data = await r.json();
      if (data && data.prefs) {
        const merged = {
          alwaysTop: !!data.prefs.always_top,
          hideHeader: !!data.prefs.hide_header,
        };
        try { localStorage.setItem(PREF_KEY, JSON.stringify(merged)); } catch (e) {}
        paintPrefs(merged);
      }
    } catch (e) { /* 服务没在跑：用本机记的就行 */ }
  }

  function paintPrefs(p) {
    const top = el('alwaysTop');
    const hh = el('hideHeader');
    if (top) top.checked = !!p.alwaysTop;
    if (hh) hh.checked = !!p.hideHeader;
    // 「只留角色」：把顶栏和页脚收起来，页面上就只剩 Live2D —— 小窗形态用得上
    const header = document.querySelector('header');
    const footer = document.querySelector('footer');
    if (header) header.style.display = p.hideHeader ? 'none' : '';
    if (footer) footer.style.display = p.hideHeader ? 'none' : '';
  }

  /** 是不是桌面版开的小窗（shell.py 打开的地址带 ?win=1） */
  function isWinMode() {
    try { return /(^|[?&])win=1/.test(location.search); } catch (e) { return false; }
  }

  /** 小窗形态：把「网页」的味道收掉，只留角色和必要的按钮。
      桌面版是拿 Edge 的应用窗口开这一页的，所以能做的只有**页面自己**收边：
      顶栏精简、页脚先藏（鼠标移到窗口上再露出来），角色占满。 */
  function applyWinMode() {
    if (!isWinMode()) return;
    document.body.classList.add('pet-win');
    const footer = document.querySelector('footer');
    if (footer) footer.style.display = 'none';         // 诊断信息不该占桌面小窗的地方
    // 小窗里留几个真正会用到的按钮，其余收进设置面板
    ['save', 'open'].forEach((id) => { const b = el(id); if (b) b.style.display = 'none'; });
    const remind = el('remind');
    if (remind) remind.style.display = 'none';         // 提醒改到设置面板里（那有档位和清单）
    // 「收起顶栏（只留角色）」在小窗里是个**自锁开关**：顶栏一收，设置按钮也跟着没了，
    // 就再也点不回来（只能关掉窗口重开）。所以小窗里直接禁掉它，并说清为什么。
    const hh = el('hideHeader');
    if (hh) {
      hh.checked = false;
      hh.disabled = true;
      const label = hh.closest ? hh.closest('label') : null;
      if (label) {
        label.style.opacity = '.55';
        label.title = '桌面小窗里不提供这个开关：顶栏一收，设置入口就一起消失了，点不回来';
      }
      const note = label && label.nextElementSibling;
      if (note && note.classList && note.classList.contains('snote')) {
        note.textContent = '小窗形态下「收起顶栏」不可用（收掉顶栏会把设置入口一起藏起来，那就点不回来了）。';
      }
    }
  }

  /* ---------------- 设置面板 ---------------- */
  function toggleSettings(force) {
    const box = el('settings');
    if (!box) return;
    const on = typeof force === 'boolean' ? force : !box.classList.contains('on');
    box.classList.toggle('on', on);
    if (on) { refreshCurrentModel(); refreshReminders(); }
  }

  function modelLabel(m) {
    if (!m) return '（还没读到形象）';
    return (m.label || m.name || m.id) + ' · ' + (m.has_motions ? '自带动作' : '程序演绎动作');
  }

  async function refreshCurrentModel(m) {
    const chip = el('curModel');
    if (!chip) return;
    if (m) { chip.textContent = '当前：' + modelLabel(m); chip.dataset.tone = 'ok'; return; }
    try {
      const cat = await PetCore.catalog();
      const cur = cat.current || state.model;
      chip.textContent = '当前：' + modelLabel(cur);
      chip.dataset.tone = 'ok';
    } catch (e) {
      chip.textContent = '读不到形象清单（服务没在跑？）';
      chip.dataset.tone = 'warn';
    }
  }

  /** 顶栏品牌名跟着形象走：形象能换成 hiyori / 白-免费版，写死「艾琳」就是明摆着的错 */
  function paintBrand(m) {
    const name = m && (m.label || m.name);
    const brand = el('brandName');
    if (brand) brand.textContent = name ? (name + ' · 桌宠') : '学习搭子 · 桌宠';
    try { document.title = name ? (name + ' · 桌宠') : '学习搭子 · 桌宠'; } catch (e) {}
  }

  /** 提醒列表：定了多少条、还有几分钟，一眼能看见（不然定了就消失，等于没有） */
  async function refreshReminders() {
    const list = el('remindList');
    if (!list) return;
    try {
      const r = await fetch('/agent/reminder');
      const data = await r.json();
      const items = data.reminders || [];
      list.innerHTML = '';
      if (!items.length) {
        const li = document.createElement('li');
        li.textContent = '还没有提醒。';
        list.appendChild(li);
        return;
      }
      items.forEach((it) => {
        const li = document.createElement('li');
        const mins = it.in_minutes >= 60
          ? (Math.round(it.in_minutes / 6) / 10) + ' 小时后'
          : Math.max(1, Math.round(it.in_minutes)) + ' 分钟后';
        li.textContent = mins + '：' + it.text;
        list.appendChild(li);
      });
    } catch (e) {
      list.innerHTML = '';
      const li = document.createElement('li');
      li.textContent = '读不到提醒（服务没在跑？）';
      list.appendChild(li);
    }
  }

  async function addReminder(minutes, text) {
    try {
      await fetch('/agent/reminder', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ minutes: minutes, text: text || (el('ask2') && el('ask2').value) || '该复习啦' }),
      });
      say(minutes >= 60 ? (minutes / 60) + ' 小时后提醒你' : minutes + ' 分钟后提醒你');
      if (state.pet) state.pet.act('nod');
      const box = el('ask2');
      if (box) box.value = '';
      refreshReminders();
    } catch (e) {
      say('提醒没定上：' + (e.message || e));
    }
  }

  function bind() {
    el('send').onclick = () => send(el('ask').value);
    el('ask').addEventListener('keydown', (e) => { if (e.key === 'Enter') send(el('ask').value); });
    const look = el('lookNow');
    if (look) look.onclick = () => lookAtScreen(el('ask') && el('ask').value);
    el('open').onclick = () => window.open('/agent/app', '_blank');
    el('model').onclick = () => {
      PetCore.openPicker(el('model'), {
        current: state.model && state.model.id,
        onPick: async (current) => {
          if (!state.pet) return;
          const cat = await PetCore.catalog().catch(() => null);
          const info = cat && (cat.models || []).filter((m) => m.id === current.id)[0];
          state.model = info || current;
          say('换好啦：' + ((state.model && (state.model.label || state.model.name)) || ''));
          await state.pet.setModel(state.model);
        },
      });
    };
    el('remind').onclick = async () => { await addReminder(20, '该复习啦'); };

    // 设置面板：形象 / 提醒 / 窗口
    if (el('settingsBtn')) el('settingsBtn').onclick = () => toggleSettings();
    if (el('pickModel')) el('pickModel').onclick = () => {
      PetCore.openPicker(el('pickModel'), {
        title: '换个形象（Live2D 模型）',
        current: state.model && state.model.id,
        onPick: async (current) => {
          if (!state.pet) return;
          const cat = await PetCore.catalog().catch(() => null);
          const info = cat && (cat.models || []).filter((m) => m.id === current.id)[0];
          state.model = info || current;
          await state.pet.setModel(state.model);
          refreshCurrentModel(state.model);
          paintBrand(state.model);
          say('换好啦：' + ((state.model && (state.model.label || state.model.name)) || ''));
        },
      });
    };
    document.querySelectorAll('#settings [data-min]').forEach((b) => {
      b.onclick = () => addReminder(parseFloat(b.dataset.min), null);
    });
    if (el('remindCustom')) el('remindCustom').onclick = () => {
      const v = window.prompt('多少分钟后提醒你？', '45');
      if (v == null) return;
      const m = parseFloat(v);
      if (!isFinite(m) || m <= 0) { say('这个数字我看不懂…'); return; }
      addReminder(Math.min(m, 1440), null);
    };
    if (el('remindRefresh')) el('remindRefresh').onclick = refreshReminders;
    if (el('alwaysTop')) el('alwaysTop').onchange = (e) => {
      savePrefs({ alwaysTop: e.target.checked });
      say(e.target.checked ? '记住啦：小窗一直在最前面（桌面版生效）' : '不再强制置顶了');
    };
    if (el('hideHeader')) el('hideHeader').onchange = (e) => {
      savePrefs({ hideHeader: e.target.checked });
      applyPrefs();
      if (state.pet) state.pet.fit();
    };
    el('chatToggle').onclick = () => {
      const panel = el('chat');
      panel.classList.toggle('on');
      el('chatToggle').textContent = panel.classList.contains('on') ? '收起聊天' : '跟她说句话';
    };
    el('stage').addEventListener('click', () => { if (state.pet) state.pet.tap(); });
  }

  async function boot() {
    bind();
    applyWinMode();
    await applyPrefs();
    setStatus('loading');
    let model = null;
    try {
      const cat = await PetCore.catalog();
      model = cat.current || (cat.models || [])[0] || null;
    } catch (e) {
      el('diag').textContent = '读不到形象清单：' + (e.message || e);
    }
    state.model = model;
    // 语音：跟设置中心一致。桌面小窗跑的就是这一页，所以这里也必须接上，
    // 否则会出现「工作台里的小玩偶会说话、桌面小窗里同一只却不会」这种怪事。
    let speakOn = false;
    try {
      const st = await fetch('/agent/settings/all').then((r) => r.json());
      const v = st.values || {};
      speakOn = !!v.tts_auto_speak && String(v.tts_provider || 'off') !== 'off';
    } catch (e) { /* 读不到设置就不念 */ }
    if (window.PetCore) PetCore.setSpeak(speakOn);
    state.pet = PetCore.createPet(el('stage'), {
      mode: 'full', model: model, onStatus: setStatus,
      speechSeconds: 9,
      speak: speakOn,
    });
    const kind = await state.pet.start(model);
    if (kind && kind.indexOf('live2d') === 0) {
      // 问候里不能写死名字：形象可以换成 hiyori / 白-免费版 / 小熊貓，
      // 换完还自称「我是艾琳」就是明摆着的错（这种错学生一眼就看见）。
      const who = model && (model.label || model.name);
      say((who ? '我是' + who : '我在呢') + '～顶栏「设置」里能换形象，想说话就点「跟她说句话」');
    } else {
      say('先用备用形象陪你～这个不影响上课');
    }
    refreshCurrentModel(model);
    paintBrand(model);
    refreshReminders();
    // 事件与演绎动作：文案是服务端给的（kernel/pet_bus.py），动作在这里映射
    const ACT_LABEL = { think: '思考中', speak: '说话', listen: '在听', happy: '开心',
                        surprise: '一惊', nod: '知道了', tap: '被戳了一下' };
    PetCore.subscribe((ev) => {
      if (!ev || !ev.type) return;
      if (ev.text) say(ev.text);
      // 提醒到点 / 新定了提醒 → 设置面板里那份列表也得跟着变（不然它一直显示旧的）
      if (ev.type === 'reminder') refreshReminders();
      const act = PetCore.actFor(ev);
      if (act) {
        state.pet.act(act);
        const chip = el('action');
        if (chip) chip.textContent = ACT_LABEL[act] || act;
        clearTimeout(state.actTimer);
        state.actTimer = setTimeout(() => { if (chip) chip.textContent = '待机'; }, 5000);
      }
    });
  }

  window.PetPage = {
    say: say,
    act: (k) => state.pet && state.pet.act(k),
    kind: () => (state.pet ? state.pet.kind : null),
    model: () => state.model,
  };
  document.addEventListener('DOMContentLoaded', boot);
})();
