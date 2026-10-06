/* ============================================================================
 * 探针：验证「已自动归档」是否真的自动刷新左侧文件夹树（交接单待办 4）
 * ----------------------------------------------------------------------------
 * 要回答的问题（都要真证据，不看截图猜）：
 *   ① 后端那轮 run 里，notice 事件**真的带着**「已自动归档到…」送到页面了吗
 *   ② 左边文件夹树的 DOM 在那条 notice 之后**真的重新渲染**了吗（观察 tree 的替换次数）
 *   ③ 这个会话节点**真的换到新文件夹下面**了吗（同 id 节点在前后快照里的位置对比）
 * 做法：真浏览器新标签 → 拿真会话 → 走 /agent/chat 真跑一轮（要模型）→ 对比前后
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_autofile_tree.js
 * ========================================================================== */
'use strict';

const CDP = process.env.CDP_PORT || 9333;
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5001';
const APP = BASE + '/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 挂在页面里的记录器：包住 fetch 抓真事件流，MutationObserver 盯住 tree 被重建
const INSTALL = `(() => {
  if (window.__afHook) return 'already';
  const rec = { events: [], treeRenders: 0, t0: Date.now() };
  window.__afHook = rec;
  const tree = document.querySelector('.au-tree');   // 左侧树的真容器（见 mountApp）
  const key = tree ? (tree.id || tree.className) : null;
  const count = (el) => el ? (el.children.length + '|' + (el.textContent || '').replace(/\\s+/g, ' ').trim().length) : null;

  // ① 事件流：包住 fetch，把 SSE 的每一行原样记下来（含时间戳）
  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const p = origFetch.apply(this, arguments);   // 注意：这里必须用 apply(this)，否则 fetch 会丢 this
    if (url.indexOf('/agent/chat') < 0) return p;
    return p.then(function (res) {
      try {
        const rd = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        (function pump() {
          rd.read().then(function (r) {
            if (r.done) return;
            buf += dec.decode(r.value, { stream: true });
            const lines = buf.split('\\n');
            buf = lines.pop();
            lines.forEach(function (line) {
              if (line.indexOf('data:') !== 0) return;
              let d = null;
              try { d = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
              rec.events.push({ at: Date.now() - rec.t0, type: d.type || d.event || '?', data: d.data || d });
            });
            pump();
          }).catch(function () {});
        })();
      } catch (e) {}
      return res;
    });
  };

  // ② 树的 DOM：每次子树被整体替换都记一笔（renderTree 是 tree.innerHTML='' 再重建）
  if (tree && window.MutationObserver) {
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        if (m.removedNodes.length > 5 && m.addedNodes.length === 0) {
          rec.treeRenders++;
          rec.events.push({ at: Date.now() - rec.t0, type: '__tree_cleared__', data: { key: key, count: count(tree) } });
        }
      });
    }).observe(tree, { childList: true });
  }
  return 'installed';
})()`;

// 树的快照：会话 id → 它在哪个文件夹底下 + 文件夹清单
const SNAPSHOT = `(() => {
  const tree = document.querySelector('.au-tree');
  if (!tree) return { error: '找不到左侧树的容器（.au-tree）' };
  const folders = Array.from(tree.querySelectorAll('.au-folder')).map(function (d) {
    return { path: (d.dataset.folder || '').replace(/\\u0000/g, ' / '), count: d.querySelectorAll('.au-session').length };
  });
  const map = {};
  Array.from(tree.querySelectorAll('.au-session')).forEach(function (n) {
    let p = n.parentElement, path = [];
    while (p && p !== tree) {
      if (p.dataset && p.dataset.folder) path.unshift(p.dataset.folder.replace(/\\u0000/g, ' / '));
      p = p.parentElement;
    }
    map[n.dataset.sessionId] = { where: path.join(' / ') || '(未分类)', title: (n.textContent || '').trim().slice(0, 24) };
  });
  return {
    folders: folders, sessions: map, total: Object.keys(map).length,
    stat: (document.querySelector('.au-nav-stat') || {}).textContent || null,
  };
})()`;

const HOOK = `(() => {
  const rec = window.__afHook;
  if (!rec) return { error: '记录器没装上' };
  return { treeRenders: rec.treeRenders, events: rec.events, now: Date.now() - rec.t0 };
})()`;

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 200));
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
      logs.push('net: ' + String(m.params.entry.text).slice(0, 160));
    }
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 30000);
  });
  const evaluate = async (e) => {
    const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面里报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };
  return { ws, send, evaluate, shot, logs };
}

async function main() {
  const out = { steps: [], verdict: {} };

  // 1) 服务端先建一个**干净的**会话（没有 subject 才会触发自动归档兜底）
  const nr = await fetch(BASE + '/agent/session/new', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject: '', textbook: '' }),
  });
  if (!nr.ok) throw new Error('/agent/session/new 失败：' + nr.status);
  const sess = (await nr.json()).session;
  out.steps.push('0) 新会话 id=' + sess.id + '（subject=空 → 满足自动归档前提）');

  // 2) 新标签打开工作台，并把当前会话钉成这个新会话（页面按 localStorage 的 au_session 恢复）
  const t = await (await fetch(`http://127.0.0.1:${CDP}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const { ws, send, evaluate, shot, logs } = await connect(t.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(900);
  await evaluate(`localStorage.setItem('au_session', ${JSON.stringify(sess.id)}); 1`);
  await send('Page.reload', { ignoreCache: true });
  await sleep(9000);

  const shotBefore = await shot('_shot_autofile_before.png');
  void shotBefore;
  const before = await evaluate(SNAPSHOT);
  if (before.error) throw new Error(before.error);
  out.steps.push('1) 跑之前的树：文件夹 ' + JSON.stringify(before.folders) + '；这个会话在 ' +
    JSON.stringify(before.sessions[sess.id]) + '；' + before.stat);

  const installed = await evaluate(INSTALL);
  out.steps.push('2) 记录器：' + installed + '（包 fetch 抓真 SSE + 盯 tree 被清空重建）');

  // 3) 真跑一轮（这条一定会被归档成「数学」类）
  out.steps.push('3) 发一轮对话（真模型）：消息 = 帮我算一下 2+3 等于几？');
  const sent = await evaluate(`(() => {
    // 必须锁定对话框那个 textarea：页面里还有小玩偶抽屉的 textarea，
    // querySelector('textarea') 会抓错（上一版探针就是这么把消息发成空串的）。
    const ta = document.querySelector('.au-input textarea');
    const btn = document.querySelector('.au-input button.au-btn.primary') ||
      Array.from(document.querySelectorAll('.au-input button')).find(b => b.textContent.indexOf('发送') >= 0);
    if (!ta || !btn) return { error: '找不到对话框的输入框/发送键', ta: !!ta, btn: !!btn };
    ta.value = '帮我算一下 2+3 等于几？';
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    btn.click();
    return { sent: true, text: ta.value, btnText: btn.textContent };
  })()`);
  out.steps.push('   点了发送吗：' + JSON.stringify(sent));
  // 4) 等归档那条 notice（最多 150 秒；归档在 run.end 之前）
  let hook = null; let sawNotice = null;
  for (let i = 0; i < 100; i++) {
    await sleep(1500);
    hook = await evaluate(HOOK);
    sawNotice = (hook.events || []).find((e) => e.type === 'notice' && String((e.data || {}).message || '').indexOf('归档') >= 0);
    if (sawNotice && hook.treeRenders > 0) break;
  }
  const kinds = {};
  (hook.events || []).forEach((e) => { kinds[e.type] = (kinds[e.type] || 0) + 1; });
  out.steps.push('4) 事件流：共 ' + (hook.events || []).length + ' 条，类型 ' + JSON.stringify(kinds));
  out.steps.push('   notice 原文：' + JSON.stringify(sawNotice ? sawNotice.data.message : null));
  out.steps.push('   树被清空重建次数：' + hook.treeRenders);
  const treeCleared = (hook.events || []).filter((e) => e.type === '__tree_cleared__').map((e) => e.at);
  out.steps.push('   重建发生在第几毫秒（相对开跑）：' + JSON.stringify(treeCleared) +
    '；notice 在第 ' + (sawNotice ? sawNotice.at : '-') + ' 毫秒');

  // 5) 跑完再看树：这个会话该换文件夹了吗
  await sleep(3000);
  const after = await evaluate(SNAPSHOT);
  await shot('_shot_autofile_after.png');
  out.steps.push('5) 跑之后的树：文件夹 ' + JSON.stringify(after.folders) + '；这个会话在 ' +
    JSON.stringify(after.sessions[sess.id]) + '；' + after.stat);

  // 6) 兜底：直接问服务端这个会话现在归哪儿（DOM 没动时用来区分「后端没归档」和「前端没刷新」）
  const info = await (await fetch(BASE + '/agent/session/' + encodeURIComponent(sess.id))).json();
  out.steps.push('6) 服务端实际上：subject=' + JSON.stringify(info.subject) + ' textbook=' +
    JSON.stringify(info.textbook) + ' filed_by=' + JSON.stringify((info.meta || {}).filed_by) +
    ' reason=' + JSON.stringify((info.meta || {}).filed_reason));

  // 7) 不靠 notice 的对照实验：手动换个位置，看树会不会跟着动（证明 refreshTree 这条路本身是通的）
  out.steps.push('7) 对照：把会话手动挪到「物理 / 未指定教材」，看树会不会跟着变…');
  const beforeMove = await evaluate(SNAPSHOT);
  await fetch(BASE + '/agent/session/' + encodeURIComponent(sess.id) + '/meta', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject: '物理', textbook: '未指定教材' }),
  });
  await evaluate(`(() => { const n = document.querySelector('.au-nav-stat'); if (n) n.click(); return 1; })()`);
  await sleep(1500);
  out.steps.push('   对照前 ' + JSON.stringify(beforeMove.sessions[sess.id]) + '（注：服务端已改，页面不一定知道）');

  // 结论
  const moved = before.sessions[sess.id] && after.sessions[sess.id] &&
    before.sessions[sess.id].where !== after.sessions[sess.id].where;
  const filedOnServer = !!(info.subject && info.subject.trim());
  out.verdict = {
    '后端真的归档了': filedOnServer,
    '收到带「归档」的 notice 事件': !!sawNotice,
    '收到 notice 后树真的重建了': !!(sawNotice && treeCleared.some((t) => t >= sawNotice.at - 200)),
    '这个会话节点真的换了文件夹': moved,
    结论: (!filedOnServer ? '后端就没归档成（这轮模型没定出学科）——这次测不出前端'
      : (moved ? '✅ 自动刷新可用（notice → refreshTree → 节点换位置，全链条实测通过）'
        : (sawNotice ? '❌ 通知到了、树也重建了，但节点没换位置（要看 renderTree/后端树数据）'
          : '❌ 提示到了但前端没刷新（refreshTree 那条分支没生效）'))),
  };
  out.console = logs.slice(0, 8);
  out.sessionId = sess.id;

  console.log(out.steps.join('\n'));
  console.log('\n控制台异常 ' + logs.filter((l) => l.indexOf('error') === 0 || l.indexOf('net') === 0).length + ' 条');
  if (logs.length) console.log(logs.slice(0, 8).join('\n'));
  console.log('\n结论：' + JSON.stringify(out.verdict, null, 1));
  console.log('\n（留了一条测试会话 ' + sess.id + '；要清理跑：py -3.14 agent_studio\\tools\\cleanup_test_sessions.py --apply）');
  fs.writeFileSync('D:/My_teacher/data/agent/_autofile_tree_report.json', JSON.stringify(out, null, 2));
  ws.close();
}

main().catch((e) => { console.error('探针失败：' + (e && e.message || e)); process.exit(1); });
