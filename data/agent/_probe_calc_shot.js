/* 截图验收：二次函数 + 直线（根/顶点/交点）、刻度、填色、全屏。
 * 复用当前标签页（不再每次 /json/new 堆标签），每个 CDP 调用带超时。
 * 用法：node data/agent/_probe_calc_shot.js
 */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page' && t.url.includes('/agent/app'));
  if (!page) throw new Error('没有工作台标签页');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      logs.push((m.params.args || []).map(a => String(a.value || a.description || '')).join(' ').slice(0, 160));
    }
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const i = ++id; pending.set(i, res);
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); rej(new Error(method + ' 超时')); } }, 10000);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const shot = async (name, clip) => {
    const p = { format: 'png' };
    if (clip) p.clip = Object.assign({ scale: 1 }, clip);
    const r = await send('Page.captureScreenshot', p);
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };
  await send('Runtime.enable');
  // 视口定死成 1500×1000：否则无头窗口默认 800×600，右栏会变成窄布局、画布跑到折叠区外
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(6500);

  await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>(x.textContent||'').includes('计算器')); if (b) b.click(); })()`);
  await sleep(1200);
  await ev(`(() => { const inp=document.querySelector('.au-calc-row .au-calc-input'); inp.focus(); inp.value='y=x^2-2x-3'; inp.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(500);
  await ev(`(() => { const add=[...document.querySelectorAll('.au-calc-add button')].find(b=>(b.textContent||'').includes('公式')); if (add) add.click(); })()`);
  await sleep(400);
  await ev(`(() => { const rows=[...document.querySelectorAll('.au-calc-row')]; const inp=(rows[1]||rows[0]).querySelector('.au-calc-input'); inp.focus(); inp.value='y=x+1'; inp.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(900);

  const info = await ev(`(() => {
    const c=document.querySelector('.au-calc'); const cv=document.querySelector('.au-calc .au-canvas');
    const btns=[...document.querySelectorAll('.au-calc .au-plot-tools button')].map(b=>b.textContent.trim());
    return { rows:[...document.querySelectorAll('.au-calc-row')].length, tools:btns, canvas: cv?[cv.width,cv.height]:null };
  })()`);
  const box = await ev(`(() => {
    const c = document.querySelector('.au-calc .au-canvas');
    c.scrollIntoView({ block: 'center' });
    const r = c.getBoundingClientRect();
    // 截图 clip 用的是**页面坐标**，getBoundingClientRect 是视口坐标 → 要加滚动偏移
    return { x: r.x + window.scrollX, y: r.y + window.scrollY, w: r.width, h: r.height };
  })()`);
  await shot('_calc_after.png');
  await shot('_calc_canvas.png', { x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.w), height: Math.round(box.h) });

  await ev(`(() => { const b=[...document.querySelectorAll('.au-calc .au-plot-tools button')].find(x=>x.textContent.trim()==='全屏'); if (b) b.click(); })()`);
  await sleep(1000);
  await shot('_calc_full.png');
  const full = await ev(`(() => { const c=document.querySelector('.au-calc'); const cv=document.querySelector('.au-calc .au-canvas'); return { full:c.classList.contains('au-calc-full'), canvas: cv?[cv.width,cv.height]:null }; })()`);
  await ev(`(() => { document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); })()`);
  await sleep(700);
  const back = await ev(`(() => document.querySelector('.au-calc').classList.contains('au-calc-full'))()`);

  // 交互验收：放大 / 双击复位 / 方向键平移（这三条以前只写了没验）
  const viewOf = () => ev(`document.querySelector('.au-calc .au-view-info').textContent`);
  const v0 = await viewOf();
  await ev(`(() => { const b=[...document.querySelectorAll('.au-calc .au-plot-tools button')].find(x=>x.textContent.trim()==='放大'); if (b) b.click(); })()`);
  await sleep(500);
  const vZoom = await viewOf();
  await ev(`(() => { const cv=document.querySelector('.au-calc .au-canvas'); cv.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); })()`);
  await sleep(600);
  const vDbl = await viewOf();
  await ev(`(() => { const cv=document.querySelector('.au-calc .au-canvas'); cv.focus(); cv.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})); })()`);
  await sleep(600);
  const vKey = await viewOf();
  const interactions = {
    zoomChanged: vZoom !== v0,
    dblclickReset: vDbl === v0,
    arrowPanChanged: vKey !== vDbl,
    v0: v0, vZoom: vZoom, vDbl: vDbl, vKey: vKey,
  };
  console.log(JSON.stringify({ info, full, backToNormal: !back, interactions, consoleErrors: logs.slice(0, 5) }, null, 1));
  ws.close();
}
main().catch(e => { console.error('失败：' + e.message); process.exit(1); });
