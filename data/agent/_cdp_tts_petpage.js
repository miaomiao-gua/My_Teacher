/* 独立桌宠页（/agent/pet —— 桌面小窗跑的就是这一页）会不会念？ */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const URL = process.env.APP_URL || 'http://127.0.0.1:5001/agent/pet';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(URL), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 140));
    }
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 30000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 460, height: 700, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: URL });
  await sleep(9000);

  const out = await evaluate(`(async () => {
    window.__calls = [];
    const orig = window.fetch;
    window.fetch = function (u, i) {
      if (String(u).indexOf('/agent/tts/say') >= 0) window.__calls.push(1);
      return orig.apply(this, arguments);
    };
    const before = window.PetPage ? 'has-api' : 'no-api';
    window.PetPage.say('下课啦，记得做错题本。');
    await new Promise((r) => setTimeout(r, 4000));
    return {
      api: before,
      tts请求: window.__calls.length,
      气泡: (document.querySelector('.pet-bubble') || {}).textContent,
      形象: window.PetPage.kind ? window.PetPage.kind() : null,
    };
  })()`);
  console.log('独立页（桌面小窗那一页）：' + JSON.stringify(out, null, 1));
  const errs = logs.filter((l) => l.indexOf('error') === 0);
  console.log('页面 error ' + errs.length + ' 条' + (errs.length ? '\n' + errs.slice(0, 3).join('\n') : ''));
  console.log('结论：' + JSON.stringify({ 独立页也会念: out.tts请求 === 1, 形象加载: !!out.形象 }));
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
