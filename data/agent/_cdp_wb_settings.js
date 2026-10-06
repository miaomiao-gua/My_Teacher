/* ============================================================================
 * 工作台「设置中心」+ 桌宠外观 的验收探针（我这个窗口的工作）
 * ----------------------------------------------------------------------------
 * 注意：data/agent/_cdp_settings.js 是**另一个窗口**写的（它测的是独立小窗里的设置面板），
 * 这个文件专门测工作台左下角那个「⚙ 设置」。两边别互相覆盖。
 *
 * 要证明：
 *   ① 左下角有「⚙ 设置」，点开有面板，字段齐全（含 API Key / 人格提示词）
 *   ② 密钥输入框**不回填明文**（安全底线）
 *   ③ 保存能生效：数值 + 人格 → 服务端真写进去
 *   ④ 桌宠：**头顶没有框**、**是整身**、控件在她脚下
 * ========================================================================== */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PET = `(() => {
  const box = document.querySelector('.au-pet');
  if (!box) return { error: '没有桌宠' };
  const app = window.__app, pet = app && app.pet && app.pet.pet;
  const cv = box.querySelector('.pet-canvas');
  const m = pet && pet.model;
  const im = m && m.internalModel;
  const stage = box.querySelector('.au-pet-stage');
  const r = box.getBoundingClientRect();
  const above = [];
  const stageTop = stage ? stage.getBoundingClientRect().top : r.top;
  Array.from(box.children).forEach((c) => {
    const cr = c.getBoundingClientRect();
    const cs = getComputedStyle(c);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (cr.bottom <= stageTop + 2 && cr.height > 1) above.push(String(c.className));
  });
  const drawnH = im ? im.originalHeight * m.scale.x : 0;
  const stageH = stage ? stage.getBoundingClientRect().height : 0;
  return {
    框高: Math.round(r.height), 框宽: Math.round(r.width),
    头顶上的可见元素: above,
    画布: cv ? { w: cv.clientWidth, h: cv.clientHeight } : null,
    露出比例: drawnH ? Number((stageH / drawnH).toFixed(2)) : null,
    控件在脚下: !!box.querySelector('.au-pet-tools'),
    形象: pet ? pet.kind : null,
    模型: app && app.pet && app.pet.model ? (app.pet.model.label || app.pet.model.name) : null,
  };
})()`;

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 150));
    }
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 25000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const shot = async (n) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + n, Buffer.from(r.data, 'base64'));
  };
  const click = (sel) => evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'no-el'; e.click(); return 'ok'; })()`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(9000);

  const entry = await evaluate(`(() => {
    const b = document.querySelector('.au-set-btn');
    if (!b) return { error: '左下角没有设置按钮' };
    const r = b.getBoundingClientRect();
    return { 文字: b.textContent.trim(), x: Math.round(r.x), y: Math.round(r.y),
             窗口高: window.innerHeight, 在左下角: r.x < 320 && r.y > window.innerHeight - 120,
             版本: window.AgentUI.version };
  })()`);
  console.log('① 左下角入口：' + JSON.stringify(entry));

  await click('.au-set-btn');
  await sleep(2500);
  const panel = await evaluate(`(() => {
    const card = document.querySelector('.au-modal-card');
    if (!card) return { error: '面板没打开' };
    const groups = Array.from(card.querySelectorAll('.au-set-title')).map(e => e.textContent.trim());
    const fields = Array.from(card.querySelectorAll('[data-key]')).map(e => e.dataset.key);
    const secret = card.querySelector('[data-secret]');
    return {
      分组: groups, 字段数: fields.length,
      密钥框类型: secret ? secret.type : null,
      密钥框的值: secret ? secret.value : null,
      密钥框提示: secret ? secret.placeholder : null,
      有人格文本框: !!card.querySelector('[data-persona]'),
      有附加要求框: !!card.querySelector('[data-persona-extra]'),
      有保存: !!Array.from(card.querySelectorAll('button')).find(b => b.textContent.indexOf('保存') >= 0),
      有测试连接: !!Array.from(card.querySelectorAll('button')).find(b => b.textContent.indexOf('测试连接') >= 0),
    };
  })()`);
  console.log('② 面板内容：' + JSON.stringify(panel));
  await shot('_shot_settings_panel.png');

  const saved = await evaluate(`(async () => {
    const card = document.querySelector('.au-modal-card');
    const steps = card.querySelector('[data-key="agent_max_steps"]');
    if (steps) steps.value = '9';
    const p = card.querySelector('[data-persona]');
    if (p) p.value = '你是「小测老师」，回答不超过三句话。';
    const e2 = card.querySelector('[data-persona-extra]');
    if (e2) e2.value = '每次回答结尾留一个问题。';
    const btn = Array.from(card.querySelectorAll('button')).find(b => b.textContent.indexOf('保存') >= 0);
    btn.click();
    await new Promise(r => setTimeout(r, 3000));
    const status = card.querySelector('.au-modal-foot .au-set-hint');
    const st = await (await fetch('/agent/settings/all')).json();
    const hl = await (await fetch('/agent/health')).json();
    return { 状态: status ? status.textContent : null,
             服务端人格: (st.persona || {}).text, 服务端附加: (st.persona || {}).extra,
             服务端max_steps: st.values.agent_max_steps, 链路: hl.chain };
  })()`);
  console.log('③ 保存结果：' + JSON.stringify(saved));
  await shot('_shot_settings_saved.png');

  await evaluate(`(() => { const b = document.querySelector('.au-modal-head button'); if (b) b.click(); return 1; })()`);
  await sleep(900);
  const pet = await evaluate(PET);
  console.log('④ 桌宠外观：' + JSON.stringify(pet));
  await shot('_shot_pet_fullbody.png');

  // ⑤ 把测试改掉的人格/参数**还原**（探针不该把学生的人格改成「小测老师」）
  const restored = await evaluate(`(async () => {
    const before = await (await fetch('/agent/settings/all')).json();
    await fetch('/agent/settings/all', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona: '', persona_extra: '', agent_max_steps: 12 }) });
    const after = await (await fetch('/agent/settings/all')).json();
    return { 人格回到默认: after.persona.is_default, max_steps: after.values.agent_max_steps };
  })()`);
  console.log('⑤ 已还原测试改动：' + JSON.stringify(restored));

  const errs = logs.filter((l) => l.indexOf('error') === 0);
  console.log('\n页面 JS error ' + errs.length + ' 条' + (errs.length ? '\n' + errs.slice(0, 5).join('\n') : ''));
  console.log('结论：' + JSON.stringify({
    左下角有设置: !entry.error && entry.在左下角,
    面板字段齐全: !panel.error && panel.字段数 >= 12,
    密钥不回填明文: panel.密钥框的值 === '' && String(panel.密钥框提示 || '').indexOf('已设置') >= 0,
    有人格提示词可编辑: !!(panel.有人格文本框 && panel.有附加要求框),
    保存生效: String(saved.服务端人格 || '').indexOf('小测老师') >= 0 && saved.服务端max_steps === 9,
    桌宠头顶没有框: Array.isArray(pet.头顶上的可见元素) && pet.头顶上的可见元素.length === 0,
    桌宠是整身: pet.露出比例 !== null && pet.露出比例 >= 0.9,
    没有页面报错: errs.length === 0,
  }, null, 1));
  ws.close();
}
main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
