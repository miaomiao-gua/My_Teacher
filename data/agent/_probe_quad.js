/* 复现：在图形计算器里逐个敲公式，看哪些会报错、错误信息是什么。
 * 用法：node data/agent/_probe_quad.js   （需要 Edge 在 9333、服务在 5001、页面开在工作台）
 */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      logs.push((m.params.args || []).map(a => String(a.value || a.description || '')).join(' ').slice(0, 200));
    }
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(6000);

  // 点「＋ 计算器」建一台计算器
  const clicked = await ev(`(() => {
    const btns = [...document.querySelectorAll('button')];
    const b = btns.find(x => (x.textContent || '').includes('计算器'));
    if (b) { b.click(); return b.textContent.trim(); }
    return null;
  })()`);
  await sleep(1200);

  const cases = ['y=x^2', 'y=x²', 'y=x³-3x', 'y=2x²+3x-1', 'y=sin²(x)', 'y=sin(30°)',
                 'y=sin(πx)', 'y=√x', 'y=x×2', 'y=（x+1）/2', 'y=sin x', 'y=sin(x)',
                 'y=|x|', 'x^2+y^2=9', 'y<=x+1'];
  const out = [];
  for (const expr of cases) {
    const r = await ev(`(() => {
      const rows = [...document.querySelectorAll('.au-calc-row')];
      if (!rows.length) return { error: '没有公式行' };
      const row = rows[0];
      const inp = row.querySelector('.au-calc-input');
      inp.focus();
      inp.value = ${JSON.stringify(expr)};
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true };
    })()`);
    await sleep(350);
    const state = await ev(`(() => {
      const row = document.querySelector('.au-calc-row');
      const err = row ? (row.querySelector('.au-calc-err') || {}).textContent : '';
      return {
        bad: row ? row.classList.contains('au-bad') : null,
        err: (err || '').trim().slice(0, 160),
        summary: (document.querySelector('.au-view-info') || {}).textContent || '',
      };
    })()`);
    out.push({ expr, ...state });
  }
  const payload = { clicked, out, consoleErrors: logs.slice(0, 5) };
  fs.writeFileSync('D:/My_teacher/data/agent/_probe_quad_out.json', JSON.stringify(payload, null, 1), 'utf8');
  for (const r of out) {
    console.log(`${r.expr.padEnd(18)} bad=${String(r.bad).padEnd(5)} ${(r.err || '').slice(0, 70)}`);
  }
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
