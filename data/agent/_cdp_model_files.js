/* 在页面里逐个请求模型的每个文件，看哪个 404（中文目录名是重点怀疑对象） */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent('http://127.0.0.1:5001/agent/app'), { method: 'PUT' })).json();
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
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 30000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  await send('Runtime.enable');
  await sleep(2500);

  const out = await evaluate(`(async () => {
    const cat = await PetCore.catalog();
    const target = (cat.models || []).find(m => m.name.indexOf('白') >= 0);
    const base = target.url.replace(/[^/]+$/, '');
    const modelUrl = target.url;
    const modelResp = await fetch(modelUrl);
    const modelText = await modelResp.text();
    let refs = {};
    try { const j = JSON.parse(modelText); refs = j.FileReferences || {}; } catch (e) { refs = { parseError: String(e) }; }
    const files = [].concat(refs.Moc || [], refs.Textures || [], refs.Physics || []);
    const results = [];
    for (const f of files) {
      const url = base + f;
      try { const r = await fetch(url); results.push({ f: f, url: decodeURIComponent(url), status: r.status, bytes: (await r.arrayBuffer()).byteLength }); }
      catch (e) { results.push({ f: f, error: String(e) }); }
    }
    return { modelUrl: decodeURIComponent(modelUrl), modelStatus: modelResp.status, modelBytes: modelText.length,
             refKeys: Object.keys(refs), files: results };
  })()`);
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().catch((e) => { console.error('失败：' + e.message); process.exit(1); });
