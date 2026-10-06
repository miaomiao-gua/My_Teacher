/* 真实浏览器验「精确度 / 画质」：
   1) 默认（自动）时鼠标读数是很多位小数
   2) 选「2 位小数」之后，读数与坐标刻度都变短（刻度靠 canvas 文字，量像素级看不出来，
      所以这里读 DOM 的读数行 + 直接调页面里的 _fmtTick 对一下）
   3) 「画质」按钮能切档，且超清下等值线采样更密（数 canvas 上画的线段数做不到，
      改成比对同一张图两次绘制的耗时/路径数：这里用 window.performance 计时 + 像素更多） */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const SPEC = {
  title: '精确度测试',
  items: [{ type: 'calculator', expressions: ['x^2+y^2=16', 'y=x^2-4'], xMin: -6.6666, xMax: 6.6666, xMin2: 0, height: 420 }],
};

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res) => { ws.onopen = res; });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true });
    if (r.exceptionDetails) throw new Error('页面报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  async function shot(name, clip) {
    const p = { format: 'png' };
    if (clip) p.clip = Object.assign({ scale: 1.5 }, clip);
    const r = await send('Page.captureScreenshot', p);
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  }
  const CLICK = async (sel) => {
    const b = await ev(`(function(){const e=document.querySelector(${JSON.stringify(sel)}); const r=e.getBoundingClientRect();
      return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    const p = { x: Math.round(b.x), y: Math.round(b.y), button: 'left', clickCount: 1 };
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseMoved', buttons: 0 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mousePressed', buttons: 1 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseReleased', buttons: 0 }, p));
    await sleep(350);
  };
  const READ = `(function(){
    const c = document.querySelector('.au-calc');
    const sel = c.querySelector('.au-calc-prec-sel');
    const qb = Array.from(c.querySelectorAll('.au-plot-tools button')).find(function(b){return b.textContent.indexOf('画质')===0;});
    return {view: c.querySelector('.au-view-info').textContent,
            coord: c.querySelector('.au-calc-coord').textContent,
            prec: sel ? sel.value : null,
            precLabel: sel ? sel.options[sel.selectedIndex].textContent : null,
            quality: qb ? qb.textContent : null};
  })()`;

  await send('Page.enable'); await send('Runtime.enable');
  const out = { steps: [] };
  const step = (s, d) => out.steps.push(s + (d ? ' → ' + d : ''));
  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(5000);
    out.ver = await ev("window.AgentUI.version");
    await ev("document.querySelectorAll('.au-surface-close').forEach(function(b){ b.onclick(); }); 'x'");
    await sleep(200);
    await ev(`window.__app.chat.renderSpec(${JSON.stringify(SPEC)})`);
    await sleep(900);
    out.auto = await ev(READ);
    step('默认（精确度=自动）', '视野 ' + out.auto.view + '　| 选择器=' + out.auto.precLabel);

    // 鼠标移到图上 → 读数
    const cv = await ev(`(function(){const r=document.querySelector('.au-calc .au-canvas').getBoundingClientRect();
      return {x:r.x,y:r.y,w:r.width,h:r.height};})()`);
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(cv.x + cv.w * 0.62), y: Math.round(cv.y + cv.h * 0.4), buttons: 0 });
    await sleep(300);
    out.hoverAuto = await ev(READ);
    step('自动时的鼠标读数', out.hoverAuto.coord);

    // 切成 2 位小数（直接走 UI：选 select + 派发 change）
    await ev(`(function(){const s=document.querySelector('.au-calc-prec-sel'); s.value='2';
      s.dispatchEvent(new Event('change')); return s.value;})()`);
    await sleep(400);
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(cv.x + cv.w * 0.62), y: Math.round(cv.y + cv.h * 0.4), buttons: 0 });
    await sleep(300);
    out.prec2 = await ev(READ);
    step('切成 2 位小数之后', '视野 ' + out.prec2.view + '　| 读数 ' + out.prec2.coord);

    // 画质切档
    const q0 = out.prec2.quality;
    await CLICK('.au-calc .au-plot-tools button:last-child');
    out.q1 = await ev(READ);
    await CLICK('.au-calc .au-plot-tools button:last-child');
    out.q2 = await ev(READ);
    step('画质切档', q0 + ' → ' + out.q1.quality + ' → ' + out.q2.quality);

    // 直接问页面里的精确度规则，确认和界面一致
    out.fmt = await ev(`[AgentUI._fmtTick(1/3), AgentUI._fmtTick(1/3,2), AgentUI._fmtTick(1/3,0), AgentUI._fmtTick(2,2)]`);
    step('精确度规则', JSON.stringify(out.fmt));

    // 量一下超清和标准的绘制耗时（同一个圆+抛物线，各画 5 次取平均）
    out.timing = await ev(`(function(){
      const c = document.querySelector('.au-calc');
      function timeIt(label){
        const t0 = performance.now();
        for (let i=0;i<5;i++) c._refresh();
        return Math.round((performance.now()-t0)/5);
      }
      const qb = Array.from(c.querySelectorAll('.au-plot-tools button')).find(function(b){return b.textContent.indexOf('画质')===0;});
      // 切到标准
      while (qb.textContent !== '画质 标准') qb.onclick();
      const std = timeIt('std');
      qb.onclick(); // 高清
      const hd = timeIt('hd');
      qb.onclick(); // 超清
      const ultra = timeIt('ultra');
      return {standard: std, hd: hd, ultra: ultra, now: qb.textContent};
    })()`);
    step('重画一张图要多久（5 次平均）', '标准 ' + out.timing.standard + 'ms / 高清 ' + out.timing.hd +
      'ms / 超清 ' + out.timing.ultra + 'ms');
    await shot('_precision.png');
    const cvBox = await ev(`(function(){const r=document.querySelector('.au-calc .au-canvas').getBoundingClientRect();
      return {x:r.x,y:r.y,width:r.width,height:r.height};})()`);
    await shot('_precision_canvas.png', cvBox);

    out.ok = {
      autoManyDecimals: /\d\.\d{4,}/.test(out.hoverAuto.coord),
      twoDecimals: out.prec2.prec === '2' && !/\.\d{3,}/.test(out.prec2.coord) && !/\d\.\d{3,}/.test(out.prec2.view),
      qualityCycles: out.q1.quality !== q0 && out.q2.quality !== out.q1.quality,
      fmtRule: out.fmt[0] === '0.333333' && out.fmt[1] === '0.33' && out.fmt[2] === '0' && out.fmt[3] === '2',
      ultraSlower: out.timing.ultra >= out.timing.standard,
    };
  } catch (err) { out.error = err.message; }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
