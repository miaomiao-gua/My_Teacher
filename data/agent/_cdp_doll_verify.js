/* ============================================================================
 * 工作台 + 小桌宠的验收探针
 * ----------------------------------------------------------------------------
 * 要证明的四件事（都要有数字，不靠感觉）：
 *   ① 左中右三栏还在，中栏能正常开对话（不会再出现「这个对话读不出来」）
 *   ② 小桌宠是**一只没有窗口壳子的角色**（背景/边框透明），不是一块面板
 *   ③ **抓住她身体任何地方都能拖**（这是用户的原话要求：可以自由移动）
 *   ④ 拖动后位置被记住；点一下（不拖）会做动作，不会误当拖动
 * 用法：node data/agent/_cdp_doll_verify.js
 * ========================================================================== */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const LAYOUT = `(() => {
  const q = (s) => document.querySelector(s);
  const box = (s) => { const e = q(s); if (!e) return null; const r = e.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; };
  return {
    三栏: { 左: box('.au-nav'), 中: box('.au-main'), 右: box('.au-side') },
    分栏线数: document.querySelectorAll('.au-splitter').length,
    中栏有没有报错: (q('.au-main') ? q('.au-main').textContent : '').indexOf('读不出来') >= 0,
    中栏前60字: (q('.au-main') ? q('.au-main').textContent : '').replace(/\s+/g, ' ').slice(0, 60),
    左边激活的对话: (function () { const a = q('.au-session.au-active'); return a ? a.textContent.slice(0, 20) : null; })(),
    版本: window.AgentUI && window.AgentUI.version,
  };
})()`;

const DOLL = `(() => {
  const box = document.querySelector('.au-pet');
  if (!box) return { error: '页面上没有小桌宠' };
  const cs = getComputedStyle(box);
  const r = box.getBoundingClientRect();
  const stage = box.querySelector('.au-pet-stage');
  const sr = stage ? stage.getBoundingClientRect() : null;
  const app = window.__app;
  const pet = app && app.pet && app.pet.pet;
  return {
    位置: { x: Math.round(r.x), y: Math.round(r.y) },
    尺寸: { w: Math.round(r.width), h: Math.round(r.height) },
    背景色: cs.backgroundColor,
    边框宽: cs.borderTopWidth,
    有抓手: !!box.querySelector('.au-pet-handle'),
    角色区: sr ? { x: Math.round(sr.x), y: Math.round(sr.y), w: Math.round(sr.width), h: Math.round(sr.height) } : null,
    形象: pet ? pet.kind : null,
    模型: app && app.pet && app.pet.model ? (app.pet.model.label || app.pet.model.name) : null,
    悬停前控件透明度: getComputedStyle(box.querySelector('.au-pet-bar')).opacity,
  };
})()`;

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
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
  async function mouse(type, x, y, buttons, clickCount) {
    await send('Input.dispatchMouseEvent', {
      type: type, x: Math.round(x), y: Math.round(y), button: 'left',
      buttons: buttons, clickCount: clickCount === undefined ? (type === 'mouseMoved' ? 0 : 1) : clickCount,
    });
  }
  async function dragFrom(x, y, dx, dy) {
    await mouse('mouseMoved', x, y, 0, 0);
    await mouse('mousePressed', x, y, 1);
    for (let i = 1; i <= 10; i++) await mouse('mouseMoved', x + dx * i / 10, y + dy * i / 10, 1, 0);
    await mouse('mouseReleased', x + dx, y + dy, 0, 1);
    await sleep(400);
  }

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });

  // ---- ① 先故意塞一个**不存在的对话 id**，验证「读不出来」这种情况会不会被修好 ----
  await evaluate(`(() => { try { localStorage.setItem('au_session', 'deadbeef0000'); } catch (e) {} return 1; })()`);
  await send('Page.navigate', { url: APP });
  await sleep(9000);
  const layout = await evaluate(LAYOUT);
  console.log('① 三栏：' + JSON.stringify(layout.三栏) + '  分栏线=' + layout.分栏线数 + '  v' + layout.版本);
  console.log('   中栏报错了吗=' + layout.中栏有没有报错 + '  激活对话=' + JSON.stringify(layout.左边激活的对话));
  console.log('   中栏前 60 字：' + JSON.stringify(layout.中栏前60字));
  await shot('_shot_layout_after_fix.png');

  // ---- ② 小桌宠是不是「没有壳子的角色」----
  const doll0 = await evaluate(DOLL);
  console.log('② 小桌宠：' + JSON.stringify(doll0));
  await shot('_shot_pet_chromeless.png');

  // ---- ③ 抓住**角色本人**拖（不是标题栏）----
  const grab = doll0.角色区 || doll0.位置;
  const gx = grab.x + grab.w / 2, gy = grab.y + grab.h * 0.55;
  await dragFrom(gx, gy, -320, -180);
  const doll1 = await evaluate(DOLL);
  const movedFar = Math.abs(doll1.位置.x - doll0.位置.x) > 100 && Math.abs(doll1.位置.y - doll0.位置.y) > 60;
  console.log('③ 抓住角色拖 320×180：' + JSON.stringify(doll0.位置) + ' → ' + JSON.stringify(doll1.位置) + '  动了=' + movedFar);
  await shot('_shot_pet_dragged.png');

  // ---- ④ 松开后再点一下（不拖）应该只做动作，不移动 ----
  await mouse('mouseMoved', doll1.位置.x + 90, doll1.位置.y + 150, 0, 0);
  await mouse('mousePressed', doll1.位置.x + 90, doll1.位置.y + 150, 1, 1);
  await mouse('mouseReleased', doll1.位置.x + 90, doll1.位置.y + 150, 0, 1);
  await sleep(700);
  const doll2 = await evaluate(DOLL);
  console.log('④ 点一下：' + JSON.stringify(doll1.位置) + ' → ' + JSON.stringify(doll2.位置) +
    '  位置没变=' + (doll2.位置.x === doll1.位置.x && doll2.位置.y === doll1.位置.y));

  // ---- ⑤ 刷新后位置是否记得 ----
  await send('Page.navigate', { url: APP });
  await sleep(8000);
  const doll3 = await evaluate(DOLL);
  console.log('⑤ 刷新后位置：' + JSON.stringify(doll3.位置) + '  记住了=' +
    (Math.abs(doll3.位置.x - doll2.位置.x) < 6 && Math.abs(doll3.位置.y - doll2.位置.y) < 6));

  console.log('\n结论：' + JSON.stringify({
    三栏还在: !!(layout.三栏.左 && layout.三栏.中 && layout.三栏.右),
    中栏不再报读不出来: layout.中栏有没有报错 === false,
    小桌宠没有窗口壳: doll0.背景色.indexOf('rgba(0, 0, 0, 0)') === 0 && doll0.边框宽 === '0px',
    抓住角色能拖动: movedFar,
    点一下不会乱跑: doll2.位置.x === doll1.位置.x && doll2.位置.y === doll1.位置.y,
    位置记得住: Math.abs(doll3.位置.x - doll2.位置.x) < 6,
    形象加载出来了: !!doll0.形象,
  }, null, 1));
  ws.close();
}
main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
