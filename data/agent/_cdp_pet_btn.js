/* 小探针：工作台页面里「🐾 桌宠」入口在不在、渲染器版本对不对
 * ----------------------------------------------------------------------------
 * 注意：一定要**自己新开一个标签页**（/json/new），不要用「列表里第一个 page」——
 * 上次就因为这个连到了另一个 about:blank 标签，量到 sideTools 为空，
 * 差点得出「按钮没加上」的错误结论。
 */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 20000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  return { ws, send, evaluate };
}

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const { ws, send, evaluate } = await connect(t.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1200, height: 800, deviceScaleFactor: 1, mobile: false });
  await sleep(5000);

  const out = await evaluate(`(() => {
    const btns = Array.from(document.querySelectorAll('.au-side-tools button, .au-side-tools .au-wchip'))
      .map(b => b.textContent.trim());
    return {
      href: location.href,
      version: window.AgentUI && window.AgentUI.version,
      sideTools: btns,
      hasPet: btns.some(t => t.indexOf('桌宠') >= 0),
    };
  })()`);
  console.log(JSON.stringify(out, null, 1));

  const box = await evaluate(`(() => {
    const b = Array.from(document.querySelectorAll('.au-side-tools button')).find(x => x.textContent.indexOf('桌宠') >= 0);
    if (!b) return null;
    const r = b.getBoundingClientRect();
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
  })()`);
  // 必须用**真实鼠标事件**点：Runtime.evaluate 里 element.click() 不算用户手势，
  // 浏览器会把 window.open 当弹窗拦掉（上一版就是这么误判成「点了没开」的）。
  if (box) {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
      await send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1 });
      await sleep(120);
    }
  }
  await sleep(4000);
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const petTargets = targets.filter((x) => (x.url || '').indexOf('/agent/pet') >= 0);

  // 桌宠窗口里形象路线是什么（顺带确认那个页面真的活着）
  let petMode = null;
  if (petTargets.length) {
    const p = await connect(petTargets[0].webSocketDebuggerUrl);
    await p.send('Runtime.enable');
    await sleep(4000);
    petMode = await p.evaluate(`(window.PetPage ? window.PetPage.mode() : null)`);
    p.ws.close();
  }
  console.log('结论：' + JSON.stringify({
    版本是新版: out.version === '0.11.0',
    有桌宠按钮: out.hasPet,
    点了以后开出了桌宠窗口: petTargets.length > 0,
    桌宠形象路线: petMode,
  }, null, 1));
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
