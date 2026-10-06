/* 验「每个窗口右上角的 ✕」：位置真的在右上角、点得动、关得掉 */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// 两种窗口各来一个：单步画板 + 图形计算器
const BOARD = {
  title: '圆的坐标几何',
  items: [{ type: 'board', title: '圆的坐标几何', height: 320, xRange: [-2, 8], yRange: [-2, 8],
    steps: [{ caption: '画一个圆并标出圆心和半径', shapes: [
      { kind: 'circle', center: [2, 3], r: 4, label: '圆' },
      { kind: 'dot', point: [2, 3], label: '圆心 (2,3)' }] }] }],
};
const CALC = {
  title: '图形计算器',
  items: [{ type: 'calculator', expressions: ['x^2+y^2=9', 'y=x-1'], xMin: -6, xMax: 6, height: 320 }],
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
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };

  await send('Page.enable'); await send('Runtime.enable');
  const out = { steps: [] };
  const step = (s, d) => out.steps.push(s + (d ? ' → ' + d : ''));
  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(5000);
    out.ver = await ev("window.AgentUI.version");
    await ev("document.querySelectorAll('.au-surface-close').forEach(function(b){ b.onclick(); }); 'cleared'");
    await sleep(200);
    await ev(`window.__app.chat.renderSpec(${JSON.stringify(BOARD)})`);
    await sleep(600);
    await ev(`window.__app.chat.renderSpec(${JSON.stringify(CALC)})`);
    await sleep(900);

    out.geom = await ev(`(function(){
      return Array.from(document.querySelectorAll('.au-surface-item')).map(function(it, i){
        const ir = it.getBoundingClientRect();
        const b = it.querySelector('.au-surface-close');
        const br = b ? b.getBoundingClientRect() : null;
        const title = it.querySelector('.au-surface-title');
        const inner = it.querySelector('.au-spec');
        const specTitleCount = it.querySelectorAll('.au-spec-title').length;
        return {
          i: i,
          kind: (it.querySelector('.au-calc') ? 'calculator' : (it.querySelector('.au-board') ? 'board' : 'other')),
          item: [Math.round(ir.width), Math.round(ir.height)],
          btn: br ? [Math.round(br.x - ir.x), Math.round(br.y - ir.y), Math.round(br.width), Math.round(br.height)] : null,
          // 距右上角多远（越小越贴角）
          toRight: br ? Math.round(ir.right - br.right) : null,
          toTop: br ? Math.round(br.top - ir.top) : null,
          visible: !!br && br.width > 8 && br.height > 8 && getComputedStyle(b).display !== 'none',
          label: b ? b.textContent : null,
          title: title ? title.textContent : null,
          specTitleCount: specTitleCount,
          firstChildIsHead: it.children[0].classList.contains('au-surface-head'),
        };
      });
    })()`);
    step('版本 v' + out.ver, JSON.stringify(out.geom.map(g => g.kind + ' 按钮距右上角 ' + g.toRight + ',' + g.toTop +
      'px 尺寸 ' + (g.btn ? g.btn[2] + '×' + g.btn[3] : '-'))));
    await shot('_close_btn.png');

    // 点第一个窗口的 ✕
    const before = await ev("document.querySelectorAll('.au-surface-item').length");
    const box = await ev(`(function(){ const b = document.querySelector('.au-surface-close').getBoundingClientRect();
      return {x: b.x + b.width/2, y: b.y + b.height/2}; })()`);
    const p = { x: Math.round(box.x), y: Math.round(box.y), button: 'left', clickCount: 1 };
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseMoved', buttons: 0 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mousePressed', buttons: 1 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseReleased', buttons: 0 }, p));
    await sleep(300);
    const after = await ev("document.querySelectorAll('.au-surface-item').length");
    step('真鼠标点右上角的 ✕', before + ' 个窗口 → ' + after + ' 个');
    out.ok = {
      everyWindowHasButton: out.geom.length === 2 && out.geom.every(g => g.visible && g.label === '✕'),
      atTopRight: out.geom.every(g => g.toRight !== null && g.toRight <= 16 && g.toTop !== null && g.toTop <= 12),
      headOnTop: out.geom.every(g => g.firstChildIsHead),
      titleOnce: out.geom.every(g => g.specTitleCount === 0),
      clickCloses: after === before - 1,
    };
  } catch (err) { out.error = err.message; }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
