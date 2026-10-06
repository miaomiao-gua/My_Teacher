/* 诊断：为什么 /agent/app 里 window.AgentUI 是空的 */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  console.log('目标：' + JSON.stringify(list.map((t) => ({ type: t.type, url: t.url })), null, 1));
  const page = list.find((t) => t.type === 'page' && (t.url || '').indexOf('about:blank') >= 0) || list.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled') logs.push(m.params.type + ': ' + (m.params.args || []).map(a => String(a.value || a.description || '')).join(' ').slice(0, 160));
    if (m.method === 'Log.entryAdded') logs.push('log-' + m.params.entry.level + ': ' + String(m.params.entry.text).slice(0, 160));
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 20000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) return { __err: JSON.stringify(r.exceptionDetails.exception) };
    return r.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Page.navigate', { url: 'http://127.0.0.1:5001/agent/app' });
  await sleep(6000);
  console.log('页面自述：' + JSON.stringify(await evaluate(`(() => ({
    href: location.href, title: document.title,
    hasAgentUI: typeof window.AgentUI,
    scripts: Array.from(document.scripts).map(s => s.src),
    bodyStart: document.body.innerHTML.slice(0, 260),
    loadErrorShown: (document.getElementById('load-error') || {}).style ? document.getElementById('load-error').style.display : 'n/a',
  }))()`), null, 1));
  console.log('控制台：' + JSON.stringify(logs.slice(0, 15), null, 1));
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
