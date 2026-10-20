/* 计算器每帧开销：_refresh 平均耗时（标注开/关各测一次）+ 滚轮合并验证。
 * 用法：node data/agent/_probe_calc_perf.js
 */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page' && t.url.includes('/agent/app'));
  if (!page) throw new Error('没有工作台标签页');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise((res, rej) => {
    const i = ++id; pending.set(i, res);
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); rej(new Error(method + ' 超时')); } }, 12000);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  await send('Runtime.enable');
  await send('Page.navigate', { url: APP });
  await sleep(6500);
  await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>(x.textContent||'').includes('计算器')); if (b) b.click(); })()`);
  await sleep(1200);
  await ev(`(() => {
    const inp=document.querySelector('.au-calc-row .au-calc-input');
    inp.focus(); inp.value='y=x^2-2x-3'; inp.dispatchEvent(new Event('input',{bubbles:true}));
  })()`);
  await sleep(500);
  await ev(`(() => { const add=[...document.querySelectorAll('.au-calc-add button')].find(b=>(b.textContent||'').includes('公式')); if (add) add.click(); })()`);
  await sleep(400);
  await ev(`(() => { const rows=[...document.querySelectorAll('.au-calc-row')]; const inp=rows[rows.length-1].querySelector('.au-calc-input'); inp.focus(); inp.value='y=x+1'; inp.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(900);

  const onMs = await ev(`(() => { const box=document.querySelector('.au-calc'); const t0=performance.now(); for(let i=0;i<15;i++) box._refresh(); return (performance.now()-t0)/15; })()`);
  // 关掉标注再测
  await ev(`(() => { const b=[...document.querySelectorAll('.au-calc .au-plot-tools button')].find(x=>x.textContent.trim()==='标注'); if (b) b.click(); })()`);
  await sleep(300);
  const offMs = await ev(`(() => { const box=document.querySelector('.au-calc'); const t0=performance.now(); for(let i=0;i<15;i++) box._refresh(); return (performance.now()-t0)/15; })()`);
  // 打开回来
  await ev(`(() => { const b=[...document.querySelectorAll('.au-calc .au-plot-tools button')].find(x=>x.textContent.trim()==='标注'); if (b) b.click(); })()`);

  // 滚轮合并：连续派发 12 次 wheel，数实际发生了多少次「一次帧内绘制」
  const rafCount = await ev(`(async () => {
    const cv = document.querySelector('.au-calc .au-canvas');
    const map = { get: null };
    let paints = 0;
    const ctx = cv.getContext('2d');
    const orig = ctx.fillText.bind(ctx);
    ctx.fillText = function(){ paints++; return orig.apply(ctx, arguments); };
    const r = cv.getBoundingClientRect();
    for (let i = 0; i < 12; i++) {
      cv.dispatchEvent(new WheelEvent('wheel', { deltaY: 100, clientX: r.x + r.width/2, clientY: r.y + r.height/2, bubbles: true, cancelable: true }));
    }
    await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
    ctx.fillText = orig;
    return paints;
  })()`);

  console.log(JSON.stringify({ refreshMs_withMarks: +onMs.toFixed(1), refreshMs_noMarks: +offMs.toFixed(1), fillTextAfter12Wheel: rafCount }, null, 1));
  ws.close();
}
main().catch(e => { console.error('失败：' + e.message); process.exit(1); });
