/* 关掉我自己起的探针浏览器（通过 CDP 的 Browser.close）
 * ----------------------------------------------------------------------------
 * 为什么不直接 taskkill msedge：用户自己的 Edge 可能开着（这个 Web 界面就在浏览器里），
 * 一锅端会把他的窗口也关掉。CDP 的 Browser.close 只作用于**我连的这个实例**。
 */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
(async () => {
  const v = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  const ws = new WebSocket(v.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('连不上')); });
  ws.send(JSON.stringify({ id: 1, method: 'Browser.close' }));
  await new Promise((r) => setTimeout(r, 1500));
  try { ws.close(); } catch (e) {}
  console.log('已请求关闭探针浏览器：' + v.Browser);
})().catch((e) => { console.error('关闭失败：' + e.message); process.exit(1); });
