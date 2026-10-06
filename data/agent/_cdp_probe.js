/* ============================================================================
 * 真浏览器布局探针（CDP）：验证「右侧分栏线能不能拖」这类只有真排版才知道的事
 * ----------------------------------------------------------------------------
 * 为什么需要它：DOM 桩测不出 flex 的真实行为（比如竖向 flex 容器里 flex-basis
 * 会被当成高度）。这个脚本连到 Edge 的调试端口，真的派发鼠标事件去拖，再量结果。
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_probe.js
 * ========================================================================== */
'use strict';

const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const num = (v) => { const n = Math.round(Number(v)); return Number.isFinite(n) ? n : null; };

const PROBE = `(() => {
  const q = s => document.querySelector(s);
  const all = s => Array.from(document.querySelectorAll(s));
  const box = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) }; };
  const cs = el => el ? getComputedStyle(el) : null;
  const root = q('.au-app');
  const sp = all('.au-splitter');
  const chip = q('.au-wchip');
  return {
    vw: innerWidth,
    rootFlexDir: root ? cs(root).flexDirection : null,
    splitterCount: sp.length,
    splitterDisplay: sp.map(s => cs(s).display),
    splitterFlexBasis: sp.map(s => cs(s).flexBasis),
    splitterBoxes: sp.map(box),
    nav: box(q('.au-nav')), main: box(q('.au-main')), side: box(q('.au-side')),
    sideInline: q('.au-side') ? q('.au-side').style.width : null,
    mainInline: q('.au-main') ? (q('.au-main').style.flex || '') : null,
    chipText: chip ? chip.textContent : null,
    chipBox: box(chip),
    version: window.AgentUI && window.AgentUI.version,
  };
})()`;

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  if (!page) throw new Error('没找到 page 目标：' + JSON.stringify(list.map(t => t.type)));

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });

  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? rej(new Error(msg.method + ' ' + JSON.stringify(msg.error))) : res(msg.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 20000);
  });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面里报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };

  const out = { steps: [] };
  const step = (s, extra) => { out.steps.push(s + (extra ? ' → ' + extra : '')); };

  await send('Page.enable');
  await send('Runtime.enable');

  async function mouse(type, x, y, buttons, clickCount) {
    const nx = num(x), ny = num(y);
    if (nx === null || ny === null) throw new Error('坐标不是数字：x=' + x + ' y=' + y + '（' + type + '）');
    await send('Input.dispatchMouseEvent', {
      type, x: nx, y: ny, button: 'left', buttons: buttons,
      clickCount: clickCount === undefined ? (type === 'mouseMoved' ? 0 : 1) : clickCount,
    });
  }
  async function dragFrom(x, y, dx, steps) {
    await mouse('mouseMoved', x, y, 0, 0);
    await mouse('mousePressed', x, y, 1);
    const n = steps || 8;
    for (let i = 1; i <= n; i++) await mouse('mouseMoved', x + (dx * i / n), y, 1, 0);
    await mouse('mouseReleased', x + dx, y, 0);
    await sleep(150);
  }
  const mid = (b) => [b.x + Math.round(b.w / 2), b.y + Math.round(b.h / 2)];
  async function shot(name) {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  }

  try {
    // ---------- A) 宽窗口 ----------
    await send('Emulation.setDeviceMetricsOverride', { width: 1200, height: 780, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(4500);
    out.wide = await evaluate(PROBE);
    step('A 宽窗口探测完成', 'vw=' + out.wide.vw + ' 分栏线=' + JSON.stringify(out.wide.splitterDisplay) +
      ' 三栏x=' + [out.wide.nav.x, out.wide.main.x, out.wide.side.x].join('/') +
      ' 版本=v' + out.wide.version);

    // A1) 往左拖右分栏线 → 工作台该变宽
    const sr = out.wide.splitterBoxes && out.wide.splitterBoxes[1];
    if (!sr) throw new Error('找不到右侧分栏线：' + JSON.stringify(out.wide.splitterBoxes));
    const [sx, sy] = mid(sr);
    await dragFrom(sx, sy, -160);
    out.afterDragRight = await evaluate(PROBE);
    out.dragRightDelta = out.afterDragRight.side.w - out.wide.side.w;
    step('A1 右分栏线往左拖 160px', '工作台 ' + out.wide.side.w + ' → ' + out.afterDragRight.side.w + 'px（Δ' + out.dragRightDelta + '）');

    // A2) 往右拖回去 → 该变窄（方向不能反）
    const sr2 = (out.afterDragRight.splitterBoxes || [])[1] || sr;
    const [sx2, sy2] = mid(sr2);
    await dragFrom(sx2, sy2, 240);
    out.afterDragBack = await evaluate(PROBE);
    out.dragBackDelta = out.afterDragBack.side.w - out.afterDragRight.side.w;
    step('A2 右分栏线往右拖 240px', '工作台 ' + out.afterDragRight.side.w + ' → ' + out.afterDragBack.side.w + 'px（Δ' + out.dragBackDelta + '）');

    // A3) 左分栏线
    const sl = (out.afterDragBack.splitterBoxes || [])[0];
    if (sl) {
      const [lx, ly] = mid(sl);
      await dragFrom(lx, ly, 120);
      out.afterDragLeft = await evaluate(PROBE);
      out.dragLeftDelta = out.afterDragLeft.nav.w - out.afterDragBack.nav.w;
      step('A3 左分栏线往右拖 120px', '历史栏 ' + out.afterDragBack.nav.w + ' → ' + out.afterDragLeft.nav.w + 'px（Δ' + out.dragLeftDelta + '）');
    }

    // A4) 宽度按钮
    const chipBox = (out.afterDragLeft || out.afterDragBack || out.wide).chipBox;
    if (chipBox) {
      const [cx, cy] = mid(chipBox);
      await mouse('mouseMoved', cx, cy, 0, 0);
      await mouse('mousePressed', cx, cy, 1);
      await mouse('mouseReleased', cx, cy, 0);
      await sleep(200);
      out.afterChip = await evaluate(PROBE);
      const prevW = (out.afterDragLeft || out.afterDragBack || out.wide).side.w;
      out.chipDelta = out.afterChip.side.w - prevW;
      step('A4 点宽度按钮', '工作台 ' + prevW + ' → ' + out.afterChip.side.w + 'px（Δ' + out.chipDelta + '），按钮文字=' + out.afterChip.chipText);
    }
    await shot('_probe_1200.png');

    // ---------- B) 窄窗口：以前这里会被媒体查询竖过来 ----------
    for (const w of [980, 900, 850, 700]) {
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: 720, deviceScaleFactor: 1, mobile: false });
      await sleep(450);
      const p = await evaluate(PROBE);
      out['narrow' + w] = {
        vw: p.vw, rootFlexDir: p.rootFlexDir, splitterDisplay: p.splitterDisplay,
        nav: p.nav, main: p.main, side: p.side,
        sideBySide: p.nav.x < p.main.x && p.main.x < p.side.x,
        withinViewport: p.side.x + p.side.w <= p.vw + 2,
      };
      step('B' + w + ' 窄窗口', '排布=' + p.rootFlexDir + ' 分栏线=' + JSON.stringify(p.splitterDisplay) +
        ' 三栏x=' + [p.nav.x, p.main.x, p.side.x].join('/') + ' 并排=' + out['narrow' + w].sideBySide +
        ' 不溢出=' + out['narrow' + w].withinViewport);
      await shot('_probe_' + w + '.png');
    }

    // B2) 窄窗口里也真的拖一次（以前这里拖了会变成改高度）
    await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 720, deviceScaleFactor: 1, mobile: false });
    await sleep(350);
    const pr = await evaluate(PROBE);
    const sr3 = pr.splitterBoxes[1];
    if (sr3) {
      await dragFrom(...mid(sr3), -120);
      const pr2 = await evaluate(PROBE);
      out.narrow900.afterDragSide = pr2.side.w;
      out.narrow900.dragDelta = pr2.side.w - pr.side.w;
      out.narrow900.heightChange = pr2.side.h - pr.side.h;
      step('B900 窄窗口里拖右分栏线', '工作台 ' + pr.side.w + ' → ' + pr2.side.w + 'px（Δ' + out.narrow900.dragDelta +
        '），高度变化=' + out.narrow900.heightChange);
    }
  } catch (err) {
    out.error = err.message;
  }

  console.log(JSON.stringify(out, null, 1));
  ws.close();
}

main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
