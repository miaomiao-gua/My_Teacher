/* 图形计算器逐项隔离诊断：一次只画一样东西，看像素到底长什么样 */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const CASES = {
  circle:   { expressions: ['x^2+y^2=16'], xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  curve:    { expressions: ['y=x/2+2'],    xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  ineqY:    { expressions: ['y<=x+3'],     xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  point:    { expressions: ['(4,0)'],      xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  text:     { expressions: ['("圆心 O", (0,2))'], xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  vertical: { expressions: ['x=3'],        xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  disk:     { expressions: ['x^2+y^2<=9'], xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  // 和第一张截图里那份完全一样（8 行），用来查「整片蓝」到底是谁画的
  demo:     { expressions: ['x^2+y^2=16', 'y=x/2+2', 'y<=x+3', '(4,0)',
                            '("圆心 O(0,0)", (0,4.6))', '("半径 r=4", (2.2,2.6))',
                            'a=1', 'y=a*x-2'], xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
  demoNoIneq: { expressions: ['x^2+y^2=16', 'y=x/2+2', '(4,0)',
                            '("圆心 O(0,0)", (0,4.6))', 'a=1', 'y=a*x-2'], xMin: -7, xMax: 7, yMin: -5, yMax: 5 },
};

const ANALYZE = `(function(){
  const cv = document.querySelector('.au-calc .au-canvas');
  const ctx = cv.getContext('2d');
  const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
  const hist = {};
  let minX=1e9,minY=1e9,maxX=-1,maxY=-1,n=0;
  let opaque = 0;
  for (let y = 0; y < cv.height; y++) for (let x = 0; x < cv.width; x++) {
    const i = (y*cv.width+x)*4, r=d[i],g=d[i+1],b=d[i+2],a=d[i+3];
    if (a < 8) continue;
    opaque++;
    const key = r+','+g+','+b+','+a;
    hist[key] = (hist[key]||0)+1;
    // 「有颜色」= 明显不是黑/白/灰
    const mx = Math.max(r,g,b), mn = Math.min(r,g,b);
    if (mx - mn > 40) { n++; if(x<minX)minX=x; if(x>maxX)maxX=x; if(y<minY)minY=y; if(y>maxY)maxY=y; }
  }
  const top = Object.keys(hist).sort((a,b)=>hist[b]-hist[a]).slice(0,8).map(k=>k+'×'+hist[k]);
  const box = n ? {w:maxX-minX+1,h:maxY-minY+1,x:minX,y:minY} : {w:0,h:0};
  return {canvas:[cv.width,cv.height], painted:opaque, colored:n, box:box,
          ratio: box.h ? +(box.w/box.h).toFixed(3) : 0, top:top};
})()`;

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res) => { ws.onopen = res; });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => (await send('Runtime.evaluate', { expression: x, returnByValue: true })).result.value;

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(4500);

  for (const name of Object.keys(CASES)) {
    const spec = Object.assign({ title: name, items: [Object.assign({ type: 'calculator', height: 420 }, CASES[name])] });
    // 只留一个算器：先清空工作台再注入
    await ev(`window.__app.chat.renderSpec({items:[]}); window.__app.chat.renderSpec(${JSON.stringify(spec)}); 'ok'`);
    await sleep(700);
    const r = await ev(ANALYZE);
    console.log('[' + name + '] 着色像素=' + r.colored + ' 外接框=' + r.box.w + '×' + r.box.h +
      ' 宽高比=' + r.ratio + ' 已画=' + r.painted + '/' + (r.canvas[0]*r.canvas[1]));
    console.log('        主要颜色: ' + r.top.join('  '));
    // 只截画布、放大 2 倍，才能看清线条质量（圆是不是连续的、有没有断成虚线）
    const rect = await ev(`(function(){const b=document.querySelector('.au-calc .au-canvas').getBoundingClientRect();
      return {x:b.x,y:b.y,w:b.width,h:b.height};})()`);
    const png = await send('Page.captureScreenshot', {
      format: 'png',
      clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 2 },
    });
    fs.writeFileSync('D:/My_teacher/data/agent/_diag_' + name + '.png', Buffer.from(png.data, 'base64'));
  }
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('DIAG FAILED: ' + e.message); process.exit(1); });
