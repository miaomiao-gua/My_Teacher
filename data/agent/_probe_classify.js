/* 直接问渲染器：这几行分别被认成什么？（用 box._classify，那是它自己导出的入口） */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page' && t.url.includes('/agent/app'));
  if (!page) throw new Error('没有工作台页面');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: APP });
  await sleep(6000);
  await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>(x.textContent||'').includes('计算器')); if (b) b.click(); })()`);
  await sleep(1000);
  const out = await ev(`(() => {
    const box = document.querySelector('.au-calc');
    if (!box || !box._classify) return { error: '没有 _classify' };
    const cases = ['y=sin x', 'y=sin(x)', 'y=x²', 'y=sin²(x)', 'y=sin(30°)'];
    const res = {};
    for (const c of cases) {
      const r = box._classify(c);
      res[c] = { kind: r && r.kind, err: r && r.err, params: r && r.params ? Object.keys(r.params) : null };
    }
    return res;
  })()`);
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().catch(e => { console.error('失败：' + e.message); process.exit(1); });
