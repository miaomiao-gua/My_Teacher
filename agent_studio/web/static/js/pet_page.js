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
    if (kind === 'sprite') el('diag').textContent = (state.pet && state.pet.liveError) || 'Live2D 运行时没拿到';
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
  async function send(text) {
    if (!text.trim()) return;
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

  function bind() {
    el('send').onclick = () => send(el('ask').value);
    el('ask').addEventListener('keydown', (e) => { if (e.key === 'Enter') send(el('ask').value); });
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
    el('remind').onclick = async () => {
      try {
        await fetch('/agent/reminder', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ minutes: 20, text: '该复习啦' }) });
        say('20 分钟后提醒你');
        if (state.pet) state.pet.act('nod');
      } catch (e) { say('提醒没定上'); }
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
    setStatus('loading');
    let model = null;
    try {
      const cat = await PetCore.catalog();
      model = cat.current || (cat.models || [])[0] || null;
    } catch (e) {
      el('diag').textContent = '读不到形象清单：' + (e.message || e);
    }
    state.model = model;
    state.pet = PetCore.createPet(el('stage'), {
      mode: 'full', model: model, onStatus: setStatus,
      speechSeconds: 9,
    });
    const kind = await state.pet.start(model);
    if (kind && kind.indexOf('live2d') === 0) {
      say('我是艾琳～右上角能打开工作台，想说话就点「跟她说句话」');
    } else {
      say('先用备用形象陪你～这个不影响上课');
    }
    // 事件与演绎动作：文案是服务端给的（kernel/pet_bus.py），动作在这里映射
    const ACT_LABEL = { think: '思考中', speak: '说话', listen: '在听', happy: '开心',
                        surprise: '一惊', nod: '知道了', tap: '被戳了一下' };
    PetCore.subscribe((ev) => {
      if (!ev || !ev.type) return;
      if (ev.text) say(ev.text);
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
