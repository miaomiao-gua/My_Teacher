/* 专门查「她头顶上方那块区域」有没有任何元素（学生明确要求：不要在她头顶加框）*/
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
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
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(9000);

  const out = await evaluate(`(() => {
    const box = document.querySelector('.au-pet');
    if (!box) return { error: '没有桌宠' };
    const r = box.getBoundingClientRect();
    const stage = box.querySelector('.au-pet-stage').getBoundingClientRect();
    const tools = box.querySelector('.au-pet-tools').getBoundingClientRect();
    const bubble = box.querySelector('.pet-bubble');
    // 逐个子元素列清楚：谁在她头顶上方、谁在她脚下
    const parts = Array.from(box.children).map((c) => {
      const cr = c.getBoundingClientRect();
      const cs = getComputedStyle(c);
      return { cls: String(c.className), top: Math.round(cr.top), bottom: Math.round(cr.bottom),
               h: Math.round(cr.height), display: cs.display, opacity: cs.opacity };
    });
    // 整个页面里，有没有别的元素压在「她头顶上方那条带子」上（排除桌宠自己和她的子元素）
    const bandTop = r.top - 60, bandBottom = stage.top - 2;
    const intruders = [];
    document.querySelectorAll('body *').forEach((el) => {
      if (box.contains(el) || el.contains(box)) return;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return;
      const b = el.getBoundingClientRect();
      if (b.width < 2 || b.height < 2) return;
      if (b.right > r.left && b.left < r.right && b.bottom > bandTop && b.top < bandBottom) {
        intruders.push(String(el.className || el.tagName).slice(0, 40));
      }
    });
    return {
      桌宠框: { top: Math.round(r.top), height: Math.round(r.height) },
      孩子: parts,
      角色区top: Math.round(stage.top),
      控件条top: Math.round(tools.top),
      控件在她脚下: tools.top > stage.top,
      气泡现在可见吗: bubble ? getComputedStyle(bubble).opacity : null,
      压在头顶带子上的外来元素: intruders.slice(0, 8),
    };
  })()`);
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
