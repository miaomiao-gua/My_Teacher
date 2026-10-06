/* 看一眼工作台里的小玩偶现在长什么样：位置、尺寸、能不能拖、盖住了什么 */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  await send('Page.enable'); await send('Runtime.enable');
  // 按用户的屏幕逻辑尺寸来（2560×1600 物理 / 150% = 1707×1067 逻辑）
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(8000);

  const info = await evaluate(`(() => {
    const box = document.querySelector('.au-pet');
    if (!box) return { error: '页面上没有 .au-pet' };
    const cs = getComputedStyle(box);
    const r = box.getBoundingClientRect();
    const bar = box.querySelector('.au-pet-bar');
    const stage = box.querySelector('.au-pet-stage');
    const cv = box.querySelector('.pet-canvas');
    const sr = stage.getBoundingClientRect();
    return {
      小玩偶: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      背景: cs.backgroundColor, 边框: cs.borderTopWidth, 阴影: cs.boxShadow.slice(0, 40),
      标题栏高: bar ? Math.round(bar.getBoundingClientRect().height) : null,
      角色区: { x: Math.round(sr.x), y: Math.round(sr.y), w: Math.round(sr.width), h: Math.round(sr.height) },
      画布: cv ? { w: cv.clientWidth, h: cv.clientHeight } : null,
      可拖动的地方: '只有标题栏（.au-pet-bar）；角色本身点了只会做动作',
      zIndex: cs.zIndex,
    };
  })()`);
  console.log(JSON.stringify(info, null, 1));
  await shot('_shot_doll_before.png');

  // 试着抓住**角色本身**拖一下：现在的实现不会动 —— 这就是用户抱怨的点
  const before = await evaluate(`(() => { const r = document.querySelector('.au-pet').getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y)]; })()`);
  const pt = await evaluate(`(() => { const r = document.querySelector('.au-pet-stage').getBoundingClientRect(); return [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)]; })()`);
  for (const type of ['mouseMoved', 'mousePressed']) {
    await send('Input.dispatchMouseEvent', { type, x: pt[0], y: pt[1], button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1 });
    await sleep(80);
  }
  for (let i = 1; i <= 8; i++) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pt[0] - i * 20, y: pt[1] - i * 12, button: 'left', buttons: 1, clickCount: 0 });
    await sleep(50);
  }
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pt[0] - 160, y: pt[1] - 96, button: 'left', buttons: 0, clickCount: 1 });
  await sleep(600);
  const after = await evaluate(`(() => { const r = document.querySelector('.au-pet').getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y)]; })()`);
  console.log('抓住角色拖之前 ' + JSON.stringify(before) + ' → 之后 ' + JSON.stringify(after) + '（没变=拖不动）');
  await shot('_shot_doll_drag_test.png');
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
