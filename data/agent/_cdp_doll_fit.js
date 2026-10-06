/* ============================================================================
 * 小玩偶取景检查：把每个形象的画布尺寸量出来，并截图对比「像不像一只玩偶」
 * 用法：node data/agent/_cdp_doll_fit.js
 * ========================================================================== */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const INFO = `(() => {
  const app = window.__app;
  const pet = app && app.pet && app.pet.pet;
  const m = pet && pet.model;
  if (!m) return { error: '没有模型' };
  const im = m.internalModel || {};
  const box = document.querySelector('.au-pet').getBoundingClientRect();
  const stage = document.querySelector('.au-pet-stage').getBoundingClientRect();
  return {
    model: app.pet.model && app.pet.model.name,
    canvas: [im.originalWidth, im.originalHeight],
    scale: Number(m.scale.x.toFixed(5)),
    drawn: [Math.round(im.originalWidth * m.scale.x), Math.round(im.originalHeight * m.scale.x)],
    stage: [Math.round(stage.width), Math.round(stage.height)],
    box: [Math.round(box.x), Math.round(box.y)],
    露出画布比例: Number((stage.height / (im.originalHeight * m.scale.x)).toFixed(2)),
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
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 30000);
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
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 840, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(8000);
  // 位置清掉，看默认位置对不对
  await evaluate(`(() => { try { localStorage.removeItem('au_pet_pos_v1'); } catch(e){} return 1; })()`);

  const names = ['my_teacher', '白', 'hiyori_free', 'Q版'];
  const out = [];
  for (const key of names) {
    const ok = await evaluate(`(async () => {
      const cat = await PetCore.catalog();
      const t = (cat.models || []).find(m => m.name.indexOf(${JSON.stringify(key)}) >= 0);
      if (!t) return null;
      await window.__app.pet.switchTo(t);
      return t.name;
    })()`);
    if (!ok) { out.push({ 跳过: key }); continue; }
    await sleep(7000);
    const info = await evaluate(INFO);
    out.push(info);
    await shot('_shot_fit_' + String(key).replace(/[^\w]/g, '') + '.png');
    console.log(JSON.stringify(info));
  }
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
