/* ============================================================================
 * 图形计算器探针（CDP）：重点验「学生自己敲公式」这条路
 * ----------------------------------------------------------------------------
 * 为什么要用真浏览器：点输入框 → 打字 → 回车 → 重画，这条链路上全是真事件
 * （focus / input / keydown）和真排版（列表滚动、焦点位置），桩里验不出来。
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_calc_probe.js
 * ========================================================================== */
'use strict';

const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const SPEC = JSON.parse(fs.readFileSync('D:/My_teacher/data/agent/_calc_demo_spec.json', 'utf8'));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const i = ++id; pending.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); rej(new Error(method + ' 超时')); } }, 25000);
  });
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  async function shot(name, clip) {
    const p = { format: 'png' };
    if (clip) p.clip = Object.assign({ scale: 1 }, clip);
    const r = await send('Page.captureScreenshot', p);
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  }
  async function clickAt(x, y) {
    const p = { x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 };
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseMoved', buttons: 0 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mousePressed', buttons: 1 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseReleased', buttons: 0 }, p));
    await sleep(150);
  }
  async function pressEnter() {
    const k = { windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, key: 'Enter', code: 'Enter' };
    await send('Input.dispatchKeyEvent', Object.assign({ type: 'rawKeyDown' }, k));
    await send('Input.dispatchKeyEvent', Object.assign({ type: 'keyUp' }, k));
    await sleep(400);
  }

  await send('Page.enable');
  await send('Runtime.enable');

  const out = { steps: [] };
  const step = (s, extra) => { out.steps.push(s + (extra ? ' → ' + extra : '')); };

  // 画布指纹：像素校验和 + 着色像素数（用来判断「有没有真的重画」）
  const CANVAS_FN = `(function(){
    const cv = document.querySelector('.au-calc .au-canvas');
    const d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
    let sum = 0, colored = 0;
    for (let i = 0; i < d.length; i += 4) {
      sum = (sum * 31 + d[i] * 3 + d[i+1] * 5 + d[i+2] * 7 + d[i+3]) % 2147483647;
      if (d[i+3] > 8) colored++;
    }
    return {sum: sum, colored: colored, size: [cv.width, cv.height]};
  })()`;

  const CALC_FN = `(function(){
    const c = window.__calc;
    const rows = c._rows;
    return {narrow: c.classList.contains('au-calc-narrow'), w: c.clientWidth,
      n: rows.length,
      rows: rows.map(r => (r.raw || '(空)') + ' [' + (r.kind || '?') + ']'),
      lastRaw: rows[rows.length-1].raw,
      param: (c.querySelector('.au-calc-params input[type=range]') || {}).value};
  })()`;

  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(4500);

    await ev(`window.__app.chat.renderSpec(${JSON.stringify(SPEC)})`);
    await sleep(900);
    await ev("window.__calc = document.querySelector('.au-calc'); 'ok'");
    out.start = await ev(CALC_FN);
    out.px0 = await ev(CANVAS_FN);
    step('注入计算器', out.start.n + ' 行，宽 ' + out.start.w + 'px，窄布局=' + out.start.narrow +
      '，着色像素=' + out.px0.colored);
    await shot('_calc_1_injected.png');

    // ---------- 学生点最后一行输入框（先确认它在可视区里、点得到） ----------
    const inp = await ev(`(function(){
      const rows = window.__calc._rows;
      const input = rows[rows.length-1].dom.input;
      input.scrollIntoView({block:'nearest'});
      const b = input.getBoundingClientRect();
      const host = window.__calc.querySelector('.au-calc-list').getBoundingClientRect();
      return {x: b.x + 30, y: b.y + b.height/2, h: b.height,
              visible: b.y >= host.y - 1 && b.y + b.height <= host.y + host.height + 1};
    })()`);
    out.inputBox = inp;
    step('常驻输入行在可视区内', 'y=' + Math.round(inp.y) + ' 可见=' + inp.visible);
    await clickAt(inp.x, inp.y);
    out.focus = await ev("(document.activeElement && document.activeElement.className) || '(没聚焦)'");
    step('点一下就聚焦到输入框', out.focus);

    // ---------- 打字 + 回车 ----------
    await send('Input.insertText', { text: 'y=sin(x)' });
    await sleep(450);
    out.typed = await ev(CALC_FN);
    out.px1 = await ev(CANVAS_FN);
    step('打字之后', '最后一行 = "' + out.typed.lastRaw + '"，着色像素 ' + out.px0.colored + ' → ' + out.px1.colored);
    await pressEnter();
    out.afterEnter = await ev(CALC_FN);
    out.px2 = await ev(CANVAS_FN);
    step('回车之后', out.afterEnter.n + ' 行：' + out.afterEnter.rows.slice(-2).join(' / '));
    step('打字就已经让画布重画了', '着色像素 ' + out.px0.colored + ' → ' + out.px1.colored +
      (out.px1.sum !== out.px0.sum ? '（变了 ✓）' : '（没变 ✗）'));
    step('回车的效果', '只是把这一行"提交"并补一行空的（图在打字时就已经画好了），' +
      out.afterEnter.n + ' 行，像素校验和 ' + out.px1.sum + ' → ' + out.px2.sum);
    await shot('_calc_2_typed.png');

    // ---------- 拖参数滑块 ----------
    const sl = await ev(`(function(){
      const s = window.__calc.querySelector('.au-calc-params input[type=range]');
      const b = s.getBoundingClientRect();
      return {x:b.x, y:b.y, w:b.width, h:b.height, val:s.value};
    })()`);
    const cy = Math.round(sl.y + sl.h / 2);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: Math.round(sl.x + sl.w * 0.2), y: cy, button: 'left', buttons: 1, clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(sl.x + sl.w * 0.8), y: cy, button: 'left', buttons: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: Math.round(sl.x + sl.w * 0.8), y: cy, button: 'left', buttons: 0, clickCount: 1 });
    await sleep(500);
    out.afterSlider = await ev(CALC_FN);
    out.px3 = await ev(CANVAS_FN);
    step('拖参数滑块', 'a: ' + sl.val + ' → ' + out.afterSlider.param +
      '，参数定义行变成 "' + (out.afterSlider.rows.find(r => r.indexOf('a ') === 0 || r.indexOf('a=') === 0) || '?') + '"');
    step('滑块也让图跟着变', out.px2.sum + ' → ' + out.px3.sum + (out.px3.sum !== out.px2.sum ? '（变了 ✓）' : '（没变 ✗）'));

    // ---------- 「＋ 文字」按钮 ----------
    const btn = await ev(`(function(){
      const bs = window.__calc.querySelectorAll('.au-calc-add button');
      const b = bs[1].getBoundingClientRect();
      return {x: b.x + b.width/2, y: b.y + b.height/2};
    })()`);
    await clickAt(btn.x, btn.y);
    out.afterText = await ev(CALC_FN);
    step('点「＋ 文字」', out.afterText.n + ' 行，最后一行 = "' + out.afterText.lastRaw + '" [' +
      out.afterText.rows[out.afterText.rows.length - 1].split('[')[1]);

    // ---------- 窄/宽布局切换（按组件自己的宽度，不是窗口宽度） ----------
    out.layout = await ev(`(function(){
      const c = window.__calc;
      const narrowNow = c.classList.contains('au-calc-narrow');
      return {w: c.clientWidth, narrow: narrowNow};
    })()`);
    step('窄布局（工作台栏里）', JSON.stringify(out.layout));
    await shot('_calc_3_final.png');
  } catch (err) {
    out.error = err.message;
  }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}

main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
