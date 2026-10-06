/* ============================================================================
 * Live2D 取景诊断：把模型的各种「宽度/高度」量出来，看看该按哪个数缩放
 * ----------------------------------------------------------------------------
 * 背景：第一版按 model.width/height 缩放到舞台，结果**只看见鞋子**（严重放大）。
 * Live2D 模型有好几个尺寸概念（画布、原始画布、内部布局），猜是没用的，直接量。
 * 用法：起 Edge（9333）后 node data/agent/_cdp_pet_fit.js
 * ========================================================================== */
'use strict';

const PORT = process.env.CDP_PORT || 9333;
const URL = process.env.APP_URL || 'http://127.0.0.1:5001/agent/pet';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MEASURE = `(() => {
  const m = window.PetPage && window.PetPage.model;
  if (!m) return { error: '还没有模型' };
  const im = m.internalModel || {};
  const core = im.coreModel || {};
  const cv = core.canvas || {};
  const box = document.querySelector('#stage').getBoundingClientRect();
  return {
    stage: { w: Math.round(box.width), h: Math.round(box.height) },
    appScreen: m.parent && m.parent.renderer ? { w: m.parent.renderer.width, h: m.parent.renderer.height } : null,
    modelWH: [Math.round(m.width), Math.round(m.height)],
    scale: m.scale.x, pos: [Math.round(m.x), Math.round(m.y)],
    anchor: [m.anchor.x, m.anchor.y],
    originalWH: [im.originalWidth, im.originalHeight],
    coreCanvasWH: [cv.width, cv.height],
    layout: im.layout ? { w: im.layout.width, h: im.layout.height, cx: im.layout.centerX, cy: im.layout.centerY } : null,
  };
})()`;

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 25000);
  });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面里报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 780, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: URL });
  await sleep(7000);

  const m = await evaluate(MEASURE);
  console.log('量到的尺寸：' + JSON.stringify(m, null, 1));

  // 用「内部画布」当基准试几种取景，每种都截图，肉眼定夺
  const variant = (mode) => `(() => {
    const p = window.PetPage, m = p.model;
    const stage = document.querySelector('#stage');
    const W = stage.clientWidth, H = stage.clientHeight;
    const im = m.internalModel;
    let baseW = m.width, baseH = m.height;
    if (${JSON.stringify(mode)} === 'core') { baseW = im.coreModel.canvas.width; baseH = im.coreModel.canvas.height; }
    if (${JSON.stringify(mode)} === 'original') { baseW = im.originalWidth; baseH = im.originalHeight; }
    const s = Math.min(W / baseW, H / baseH);
    m.scale.set(s);
    m.anchor.set(0.5, 1);         // 以「脚底」为锚点：这是立绘最自然的贴地方式
    m.x = W / 2;
    m.y = H;
    return { mode: ${JSON.stringify(mode)}, baseW, baseH, scale: s, x: m.x, y: m.y, drawnW: m.width, drawnH: m.height };
  })()`;

  for (const mode of ['model', 'core', 'original']) {
    const r = await evaluate(variant(mode));
    console.log('取景 ' + JSON.stringify(r));
    await sleep(600);
    await shot('_shot_fit_' + mode + '.png');
  }
  ws.close();
}

main().catch((e) => { console.error('诊断失败：' + (e && e.message)); process.exit(1); });
