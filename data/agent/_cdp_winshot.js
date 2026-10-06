/* ============================================================================
 * 小窗形态截图探针：把 /agent/pet?win=1 的布局抓下来给人眼确认
 * ----------------------------------------------------------------------------
 * 为什么单独写：桌面小窗是「Edge 应用窗口 + 这一页」，窗口本身没法用 CDP 量，
 * 能验的是**页面在小尺寸下长什么样**（顶栏精简、页脚收起、角色占满、设置面板不溢出）。
 * 用法：node data/agent/_cdp_winshot.js [URL]
 * ========================================================================== */
'use strict';

const CDP = process.env.CDP_PORT || 9333;
const URL = process.argv[2] || 'http://127.0.0.1:5001/agent/pet?win=1';
const W = Number(process.env.W || 420), H = Number(process.env.H || 620);
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${CDP}/json/new?` + encodeURIComponent(URL), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const dialogs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Page.javascriptDialogOpening') {
      dialogs.push(m.params.message);
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog', params: { accept: true } }));
    }
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 45000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };

  await send('Page.enable'); await send('Runtime.enable');
  // 按桌面小窗的实际尺寸量：420×620
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await sleep(11000);

  const info = await evaluate(`(() => {
    const q = (s) => document.querySelector(s);
    const cs = (e) => e ? getComputedStyle(e) : null;
    const stage = q('#stage'), cv = q('.pet-canvas'), img = q('.pet-sprite');
    const r = stage ? stage.getBoundingClientRect() : null;
    return {
      title: document.title,
      brand: q('#brandName') ? q('#brandName').textContent : null,
      winClass: document.body.className,
      mode: q('#mode') ? q('#mode').textContent : null,
      headerH: q('header') ? Math.round(q('header').getBoundingClientRect().height) : null,
      footerShown: cs(q('footer')) ? cs(q('footer')).display : null,
      stage: r ? { w: Math.round(r.width), h: Math.round(r.height) } : null,
      canvas: cv ? { w: cv.clientWidth, h: cv.clientHeight, shown: cs(cv).display } : null,
      sprite: img ? { shown: cs(img).display, natural: img.naturalWidth } : null,
      bubble: q('.pet-bubble') ? q('.pet-bubble').textContent : null,
      settingsOpen: q('#settings') ? q('#settings').classList.contains('on') : null,
      buttons: Array.from(document.querySelectorAll('header button'))
        .map((b) => ({ id: b.id, shown: cs(b).display !== 'none' })),
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
      overflowY: document.documentElement.scrollHeight > window.innerHeight,
    };
  })()`);

  const shot0 = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('D:/My_teacher/data/agent/_shot_win_1.png', Buffer.from(shot0.data, 'base64'));

  // 打开设置面板看一眼小窗里会不会溢出
  await evaluate(`(() => { const b = document.getElementById('settingsBtn'); if (b) b.click(); return 1; })()`);
  await sleep(1500);
  const panel = await evaluate(`(() => {
    const box = document.getElementById('settings');
    if (!box) return null;
    const r = box.getBoundingClientRect();
    return { on: box.classList.contains('on'), w: Math.round(r.width), h: Math.round(r.height),
             right: Math.round(window.innerWidth - r.right), bottom: Math.round(window.innerHeight - r.bottom),
             scrollsInside: box.scrollHeight > box.clientHeight };
  })()`);
  const shot1 = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync('D:/My_teacher/data/agent/_shot_win_2.png', Buffer.from(shot1.data, 'base64'));

  console.log('小窗形态（' + W + 'x' + H + '）：' + JSON.stringify(info, null, 1));
  console.log('设置面板在小窗里：' + JSON.stringify(panel));
  console.log('页面弹窗：' + JSON.stringify(dialogs));
  fs.writeFileSync('D:/My_teacher/data/agent/_winshot.json',
    JSON.stringify({ info: info, panel: panel, dialogs: dialogs }, null, 2));
  ws.close();
}

main().catch((e) => { console.error('截图探针失败：' + ((e && e.message) || e)); process.exit(1); });
