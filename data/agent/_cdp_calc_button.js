/* ============================================================================
 * 验「学生点＋计算器 → 自己敲公式 → 画出来」这条完整的用户路径
 * ----------------------------------------------------------------------------
 * 这是学生不看说明书就会走的路：打开页面 → 工作台右上角点「＋ 计算器」→ 敲公式。
 * 必须真的能用，包括：原来工作台里的东西还在、新窗口要滚到眼前、圆要是圆的。
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_calc_button.js
 * ========================================================================== */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true });
    if (r.exceptionDetails) throw new Error('页面报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  async function clickAt(x, y) {
    const p = { x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 };
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseMoved', buttons: 0 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mousePressed', buttons: 1 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseReleased', buttons: 0 }, p));
    await sleep(300);
  }
  async function pressEnter() {
    const k = { windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, key: 'Enter', code: 'Enter' };
    await send('Input.dispatchKeyEvent', Object.assign({ type: 'rawKeyDown' }, k));
    await send('Input.dispatchKeyEvent', Object.assign({ type: 'keyUp' }, k));
    await sleep(350);
  }
  async function shot(name, clip) {
    const p = { format: 'png' };
    if (clip) p.clip = Object.assign({ scale: 2 }, clip);
    const r = await send('Page.captureScreenshot', p);
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  }

  await send('Page.enable'); await send('Runtime.enable');
  const out = { steps: [] };
  const step = (s, d) => out.steps.push(s + (d ? ' → ' + d : ''));

  const ITEMS = `Array.from(document.querySelectorAll('.au-surface-item')).map(function(it){
    const kinds = Array.from(it.querySelectorAll('.au-calc,.au-board,.au-plot,.au-chart,iframe'))
      .map(function(e){ return e.className.split(' ').filter(function(c){return c.indexOf('au-')===0;}).join('.'); });
    const r = it.getBoundingClientRect();
    return {kinds: kinds, top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height)};
  })`;

  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(5000);

    out.ver = await ev("window.AgentUI.version");
    out.before = await ev(ITEMS);
    step('打开页面（版本 v' + out.ver + '）', '工作台里已有 ' + out.before.length + ' 个窗口：' +
      JSON.stringify(out.before.map(i => i.kinds.join(','))));

    // 点右上角「＋ 计算器」（真鼠标事件，不是直接调函数）
    const btn = await ev(`(function(){
      const b = Array.from(document.querySelectorAll('.au-side-head button'))
        .find(function(x){ return x.textContent.indexOf('计算器') >= 0; });
      if (!b) return null;
      const r = b.getBoundingClientRect();
      return {x: r.x + r.width/2, y: r.y + r.height/2, text: b.textContent};
    })()`);
    step('找到按钮', btn ? JSON.stringify(btn.text) : '没找到！');
    if (!btn) throw new Error('工作台标题条上没有「＋ 计算器」按钮');
    await clickAt(btn.x, btn.y);

    out.after = await ev(ITEMS);
    step('点一下之后', '工作台里 ' + out.after.length + ' 个窗口：' + JSON.stringify(out.after.map(i => i.kinds.join(','))));
    // 新窗口是插在**最上面**的（mountSurface 的行为），所以学生点完就看得见
    const first = out.after[0] || {};
    step('新窗口在最上面、在视野里', '第一个窗口 top=' + first.top + '，高 ' + first.h +
      (first.top >= 0 && first.top < 880 && first.kinds.join('.').indexOf('au-calc') >= 0
        ? '（是计算器且在视野里 ✓）' : '（✗）'));

    out.calc = await ev(`(function(){
      const c = Array.from(document.querySelectorAll('.au-calc')).pop();
      if (!c) return null;
      return {rows: c._rows.map(function(r){ return (r.raw||'(空)') + '[' + r.kind + ']'; }),
              narrow: c.classList.contains('au-calc-narrow'), w: c.clientWidth};
    })()`);
    step('新计算器的状态', out.calc ? out.calc.rows.join(' ') + '，宽 ' + out.calc.w + 'px' : '没有计算器 ✗');

    // 学生点输入框 → 敲公式
    const inp = await ev(`(function(){
      const c = Array.from(document.querySelectorAll('.au-calc')).pop();
      const input = c._rows[c._rows.length-1].dom.input;
      input.scrollIntoView({block:'nearest'});
      const r = input.getBoundingClientRect();
      return {x: r.x + 40, y: r.y + r.height/2};
    })()`);
    await clickAt(inp.x, inp.y);
    out.focus = await ev("(document.activeElement && document.activeElement.className) || '(没聚焦)'");
    step('点输入框', '焦点 = ' + out.focus);

    await send('Input.insertText', { text: 'x^2+y^2=9' });
    await sleep(500);
    out.typed = await ev(`(function(){
      const c = Array.from(document.querySelectorAll('.au-calc')).pop();
      const cv = c.querySelector('.au-canvas');
      const d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
      let n=0,minX=1e9,minY=1e9,maxX=-1,maxY=-1;
      for (let y=0;y<cv.height;y++) for (let x=0;x<cv.width;x++){
        const i=(y*cv.width+x)*4, r=d[i],g=d[i+1],b=d[i+2],a=d[i+3];
        if (a<8) continue;
        const mx=Math.max(r,g,b), mn=Math.min(r,g,b);
        if (mx-mn>40){ n++; if(x<minX)minX=x; if(x>maxX)maxX=x; if(y<minY)minY=y; if(y>maxY)maxY=y; }
      }
      const w=maxX-minX+1, h=maxY-minY+1;
      return {rows: c._rows.map(function(r){return (r.raw||'(空)')+'['+r.kind+']';}),
              colored:n, box:[w,h], ratio: h? +(w/h).toFixed(3):0, canvas:[cv.width,cv.height]};
    })()`);
    step('敲进 x^2+y^2=9 之后', out.typed.rows.join(' ') + '，着色像素 ' + out.typed.colored +
      '，外接框 ' + out.typed.box.join('×') + '（宽高比 ' + out.typed.ratio + '）');

    // 再来一行不等式
    await pressEnter();
    await send('Input.insertText', { text: 'y<=x+2' });
    await sleep(600);
    out.two = await ev(`(function(){
      const c = Array.from(document.querySelectorAll('.au-calc')).pop();
      return c._rows.map(function(r){return (r.raw||'(空)')+'['+r.kind+']';});
    })()`);
    step('再敲一行 y<=x+2', out.two.join(' '));
    await shot('_btn_after.png');
    const cvBox = await ev(`(function(){
      const c = Array.from(document.querySelectorAll('.au-calc')).pop();
      const r = c.querySelector('.au-canvas').getBoundingClientRect();
      return {x:r.x,y:r.y,width:r.width,height:r.height};
    })()`);
    await shot('_btn_canvas.png', cvBox);

    out.ok = {
      button: !!btn,
      newItem: out.after.length === out.before.length + 1,
      newestOnTop: out.after.length > 0 && out.after[0].kinds.join('.').indexOf('au-calc') >= 0 &&
        out.after[0].top >= 0 && out.after[0].top < 880,
      emptyRowReady: !!out.calc && out.calc.rows.length === 1 && out.calc.rows[0] === '(空)[empty]',
      focused: out.focus === 'au-calc-input',
      circleTyped: !!out.typed && out.typed.rows[0] === 'x^2+y^2=9[implicit]',
      circleDrawn: !!out.typed && out.typed.colored > 300,
      circleRound: !!out.typed && Math.abs(out.typed.ratio - 1) < 0.06,
      // 敲完第二行还没回车，所以是 2 行（回车之后才会补一行空的）—— 跟 Desmos 一样
      twoRows: !!out.two && out.two.length === 2 &&
        out.two[0] === 'x^2+y^2=9[implicit]' && out.two[1] === 'y<=x+2[ineqY]',
      oldContentKept: out.after.some(i => i.kinds.join('.').indexOf('au-board') >= 0),
    };
  } catch (err) {
    out.error = err.message;
  }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
