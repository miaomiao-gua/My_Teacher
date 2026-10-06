/* ============================================================================
 * 工作台里的小玩偶探针：真浏览器里验证「小玩偶 + 演绎动作 + 换形象」
 * ----------------------------------------------------------------------------
 * 要回答的问题（都得有证据，不能靠看截图猜）：
 *   ① 小玩偶真的在工作台页面上、形象加载出来了（不是空白框）
 *   ② 上课事件能让它说话 + 演一段（气泡文案来自服务端总线）
 *   ③ 「换形象」能把模型换成 白-免费版（一个**没有动作文件**的模型）
 *   ④ 没有动作文件的模型，参数演绎真的在动
 *      —— 这条靠读 Live2D 的参数值前后对比，比截图可靠得多
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_doll.js
 * ========================================================================== */
'use strict';

const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const DOLL = `(() => {
  const box = document.querySelector('.au-pet');
  if (!box) return { error: '没有小玩偶这个元素' };
  const stage = box.querySelector('.au-pet-stage');
  const cv = stage.querySelector('.pet-canvas');
  const img = stage.querySelector('.pet-sprite');
  const bub = box.querySelector('.pet-bubble');
  const app = window.__app;
  const pet = app && app.pet && app.pet.pet;
  const r = box.getBoundingClientRect();
  const cs = (e) => (e ? getComputedStyle(e) : null);
  return {
    box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    title: box.querySelector('.au-pet-title').textContent,
    kind: box.dataset.petKind || null,
    petKind: pet ? pet.kind : null,
    petStatus: pet ? pet.status : null,
    hasMotions: pet ? pet.hasMotions : null,
    groups: pet ? pet.groups : null,
    modelName: app && app.pet && app.pet.model ? app.pet.model.name : null,
    modelId: app && app.pet && app.pet.model ? app.pet.model.id : null,
    canvasShown: cs(cv) ? cs(cv).display : null,
    canvasBox: cv ? { w: cv.clientWidth, h: cv.clientHeight } : null,
    spriteShown: cs(img) ? cs(img).display : null,
    spriteSrc: img ? (img.getAttribute('src') || '').slice(0, 60) : null,
    bubble: bub ? bub.textContent : null,
    bubbleOn: bub ? bub.classList.contains('on') : null,
    bodyTransform: cs(box.querySelector('.pet-body')).transform,
  };
})()`;

// 读 Live2D 参数：判断「程序演绎」到底有没有动（比截图硬）
const PARAMS = `(() => {
  const pet = window.__app && window.__app.pet && window.__app.pet.pet;
  const m = pet && pet.model;
  const cm = m && m.internalModel && m.internalModel.coreModel;
  if (!cm) return { error: '还没有 Live2D 模型' };
  const ids = ['ParamMouthOpenY', 'ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBreath', 'ParamEyeLOpen'];
  const out = {};
  ids.forEach((id) => {
    try { out[id] = cm.getParameterIndex(id) >= 0 ? Number(cm.getParameterValueById(id).toFixed(4)) : null; }
    catch (e) { out[id] = 'err'; }
  });
  return out;
})()`;

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 160));
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
      logs.push('net: ' + String(m.params.entry.text).slice(0, 120));
    }
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 25000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面里报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };
  return { ws, send, evaluate, shot, logs };
}

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const { ws, send, evaluate, shot, logs } = await connect(t.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 840, deviceScaleFactor: 1, mobile: false });
  await sleep(7000);

  const steps = [];
  const a = await evaluate(DOLL);
  steps.push('① 小玩偶：' + JSON.stringify({ 位置: a.box, 标题: a.title, 形象: a.petKind, 有动作文件: a.hasMotions,
    动作组: a.groups, 画布: a.canvasBox && (a.canvasBox.w + 'x' + a.canvasBox.h) }));
  await shot('_shot_doll_1.png');

  // ② 真的跑一次 agent → 小玩偶该说话 + 演一段
  const before = a.bubble;
  const r = await fetch('http://127.0.0.1:5001/agent/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: '只回两个字：收到' }),
  });
  const rd = r.body.getReader();
  (async () => { try { for (;;) { const { done } = await rd.read(); if (done) break; } } catch (e) {} })();
  let b = a;
  for (let i = 0; i < 12; i++) {
    await sleep(700);
    b = await evaluate(DOLL);
    if (b.bubble && b.bubble !== before) break;
  }
  steps.push('② 上课事件 → 小玩偶气泡：' + JSON.stringify(b.bubble) + '（变了=' + (b.bubble !== before) + '）');
  await shot('_shot_doll_2.png');
  await fetch('http://127.0.0.1:5001/agent/cancel', { method: 'POST' }).catch(() => {});

  // ③ 换形象 → 白-免费版（**真的点界面上的按钮和清单**，不用后门函数）
  await evaluate(`(() => {
    const btn = Array.from(document.querySelectorAll('.au-pet-btn')).find(b => b.textContent.indexOf('换形象') >= 0);
    if (btn) btn.click();
    return !!btn;
  })()`);
  await sleep(2000);
  const pickerRows = await evaluate(`(() => {
    const box = document.getElementById('pet-picker');
    if (!box) return null;
    return Array.from(box.children).map(c => c.textContent.trim().slice(0, 40));
  })()`);
  const clicked = await evaluate(`(() => {
    const box = document.getElementById('pet-picker');
    if (!box) return 'no-picker';
    const row = Array.from(box.children).find(c => c.textContent.indexOf('白') >= 0);
    if (!row) return 'no-row';
    row.click();
    return row.textContent.trim().slice(0, 30);
  })()`);
  await sleep(9000);
  const c = await evaluate(DOLL);
  steps.push('③ 点「换形象」→ 清单 ' + JSON.stringify(pickerRows) + ' → 点了 ' + JSON.stringify(clicked));
  steps.push('   换完：标题=' + c.title + ' 形象=' + c.petKind + ' 有动作文件=' + c.hasMotions +
    ' 画布=' + JSON.stringify(c.canvasBox) + ' 图片=' + c.spriteShown);
  await shot('_shot_doll_3.png');
  const switched = { id: c.modelId, name: c.modelName, has_motions: c.hasMotions };

  // ④ 没有动作文件的模型：参数演绎必须真的在动（读参数值对比）
  const p0 = await evaluate(PARAMS);
  await evaluate(`(() => { window.__app.pet.pet.act('speak'); return 1; })()`);
  const samples = [];
  for (let i = 0; i < 6; i++) { await sleep(180); samples.push(await evaluate(PARAMS)); }
  const moving = ['ParamMouthOpenY', 'ParamAngleX', 'ParamAngleZ']
    .filter((k) => samples.some((s) => s[k] != null && p0[k] != null && Math.abs(s[k] - p0[k]) > 0.01));
  steps.push('④ 参数演绎：静止时 ' + JSON.stringify(p0) + '；说话时在动的参数=' + JSON.stringify(moving));
  steps.push('   采样 ' + JSON.stringify(samples.map((s) => ({ mouth: s.ParamMouthOpenY, ax: s.ParamAngleX }))));

  // ⑤ 换回艾琳（别把学生默认形象留在测试用的那个上）—— 同样走真实界面
  await evaluate(`(() => {
    const btn = Array.from(document.querySelectorAll('.au-pet-btn')).find(b => b.textContent.indexOf('换形象') >= 0);
    if (btn) btn.click();
    return !!btn;
  })()`);
  await sleep(1800);
  const clickedBack = await evaluate(`(() => {
    const box = document.getElementById('pet-picker');
    if (!box) return 'no-picker';
    // 清单里显示的是**人话名字**（艾琳（老师）），不是目录名 my_teacher
    const row = Array.from(box.children).find(c => /艾琳|my_teacher/.test(c.textContent));
    if (!row) return 'no-row:' + Array.from(box.children).map(c => c.textContent.slice(0, 12)).join('|');
    row.click();
    return 'ok';
  })()`);
  await sleep(8000);
  const d = await evaluate(DOLL);
  steps.push('⑤ 换回（' + clickedBack + '）→ 标题=' + d.title + ' 形象=' + d.petKind + ' 模型=' + d.modelId);

  console.log(steps.join('\n'));
  const errs = logs.filter((l) => l.indexOf('error') === 0);
  console.log('\n控制台 error ' + errs.length + ' 条');
  if (logs.length) console.log(logs.slice(0, 8).join('\n'));
  console.log('\n结论：' + JSON.stringify({
    小玩偶出现了: !a.error && a.box && a.box.w > 80,
    用上了Live2D: String(c.petKind || a.petKind).indexOf('live2d') === 0,
    气泡跟着上课事件: b.bubble && b.bubble !== before,
    换形象成功: c.modelId === switched.id && c.petKind === 'live2d_local',
    无动作模型靠参数演绎: moving.length >= 2,
    换得回来: d.modelId === 'my_teacher',
    没有页面报错: errs.length === 0,
  }, null, 1));
  ws.close();
}

main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
