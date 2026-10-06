/* ============================================================================
 * 网页桌宠探针：真浏览器里验证 /agent/pet
 * ----------------------------------------------------------------------------
 * 要回答的问题（不靠猜）：
 *   ① 页面到底用了哪条形象路线（Live2D 本地 / Live2D 网络 / 备用形象）？
 *   ② 气泡会不会动 —— 由**服务端**的桌宠总线驱动（POST /agent/reminder）后必须出现文案
 *   ③ 聊天抽屉能不能打开、备用形象是不是真的加载出来了（naturalWidth>0）
 *   ④ 有没有 JS 报错（Live2D 拿不到时的降级路径最容易在这种地方翻车）
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_pet.js
 * ========================================================================== */
'use strict';

const PORT = process.env.CDP_PORT || 9333;
const URL = process.env.APP_URL || 'http://127.0.0.1:5001/agent/pet';
const fs = require('fs');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PROBE = `(() => {
  const q = (s) => document.querySelector(s);
  const chip = q('#mode'), bub = q('.pet-bubble'), img = q('.pet-sprite'), cv = q('.pet-canvas');
  return {
    mode: chip ? chip.textContent : null,
    tone: chip ? chip.dataset.tone : null,
    action: q('#action') ? q('#action').textContent : null,
    bubble: bub ? bub.textContent : null,
    bubbleOn: bub ? bub.classList.contains('on') : null,
    bubbleVisible: bub ? getComputedStyle(bub).opacity : null,
    fallbackShown: img ? getComputedStyle(img).display : null,
    fallbackW: img ? img.naturalWidth : null,
    fallbackSrc: img ? img.getAttribute('src') : null,
    canvasShown: cv ? getComputedStyle(cv).display : null,
    chatOpen: q('#chat') ? q('#chat').classList.contains('on') : null,
    saveBtnShown: q('#save') ? getComputedStyle(q('#save')).display : null,
    hasModelBtn: !!q('#model'),
    diag: q('#diag') ? q('#diag').textContent : null,
    api: !!(window.PetPage && window.PetPage.kind),
    apiMode: window.PetPage ? window.PetPage.kind() : null,
    modelName: window.PetPage && window.PetPage.model() ? (window.PetPage.model().label || window.PetPage.model().name) : null,
    log: q('#log') ? q('#log').textContent.slice(0, 120) : null,
  };
})()`;

async function main() {
  // **自己开一个新标签页**，不要用「列表里第一个 page」—— 已经攒了好几个标签时，
  // 那样会连到别的页面上，量出来的东西全是错的（排查半天才发现连错了页面）。
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(URL), { method: 'PUT' })).json();
  const page = t;
  if (!page || !page.webSocketDebuggerUrl) throw new Error('开不出新标签：' + JSON.stringify(page));

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });

  let id = 0;
  const pending = new Map();
  const consoleMsgs = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.method === 'Runtime.consoleAPICalled') {
      const t = msg.params.type;
      if (t === 'error' || t === 'warning') {
        consoleMsgs.push('console-' + t + ': ' + (msg.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 200));
      }
    }
    if (msg.method === 'Log.entryAdded' && ['error', 'warning'].includes(msg.params.entry.level)) {
      // 浏览器把 404 也记成 error。查不到本地运行时/模型是**预期**的降级路径，不算页面故障。
      consoleMsgs.push('net-' + msg.params.entry.level + ': ' + String(msg.params.entry.text).slice(0, 160));
    }
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? rej(new Error(msg.method + ' ' + JSON.stringify(msg.error))) : res(msg.result);
    }
  };
  const send = (method, params) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params: params || {} }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 25000);
  });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('页面里报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  const click = async (sel) => evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return 'no-el'; e.click(); return 'ok'; })()`);
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };

  const out = { steps: [], console: [] };
  const step = (s) => { out.steps.push(s); console.log('  ' + s); };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 780, deviceScaleFactor: 1, mobile: false });
  if (process.env.FALLBACK === '1') {
    // 把外网运行时**堵死**，只留备用形象这条路 —— 这才叫验证兜底（不然 CDN 一能用就测不到）
    await send('Network.enable');
    await send('Network.setBlockedURLs', { urls: ['*jsdelivr.net*', '*cubism.live2d.com*', '*unpkg.com*', '*npmmirror.com*', '*live2d/vendor*'] });
    step('（已在本浏览器里屏蔽 Live2D 的 CDN，用于验证兜底形象）');
  }
  await send('Page.navigate', { url: URL });
  await sleep(5000);

  out.a = await evaluate(PROBE);
  step(`① 页面形象路线 = ${out.a.mode} / tone=${out.a.tone} / 动作牌=${out.a.action}`);
  step(`   备用形象 display=${out.a.fallbackShown} naturalWidth=${out.a.fallbackW}；canvas display=${out.a.canvasShown}`);
  step(`   气泡："${out.a.bubble}" 可见=${out.a.bubbleVisible}`);
  await shot('_shot_pet_1.png');

  // ② 真的跑一次 agent（走 /agent/chat），运行开始时会往总线推 run.start，
  //    气泡必须立刻变成「老师在想了…」。这是**最接近真实使用**的那条通路。
  const before = out.a.bubble;
  let chatDone = false;
  if (process.env.SKIP_RUN === '1') {
    step('②（已跳过：SKIP_RUN=1）');
  } else {
  const runResp = await fetch('http://127.0.0.1:5001/agent/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: '只回两个字：收到' }),
  });
  const rd = runResp.body.getReader();
  (async () => { try { for (;;) { const { done } = await rd.read(); if (done) break; } } catch (e) {} })().then(() => { chatDone = true; });

  let snapshot = out.a;
  let sawThinking = false;
  for (let i = 0; i < 14; i++) {
    await sleep(700);
    snapshot = await evaluate(PROBE);
    if (snapshot.bubble && snapshot.bubble !== before) { sawThinking = true; break; }
  }
  out.b = snapshot;
  out.runBubble = snapshot.bubble;
  out.bubbleChanged = sawThinking;
  step(`② 触发一次 agent 运行 → 气泡："${before}" → "${snapshot.bubble}"（变了=${out.bubbleChanged}）`);
  await shot('_shot_pet_2.png');
  if (!chatDone) {
    try { await fetch('http://127.0.0.1:5001/agent/cancel', { method: 'POST' }); } catch (e) {}   // 别为了一句测试白烧模型
    step('   （运行还没结束，已请求取消）');
  }
  }

  // ②b 提醒：add_reminder 会把分钟数**夹到最少 1 分钟**（kernel/pet_bus.py），
  //     所以这条要等一分钟左右才有意义 —— 默认跳过，SLOW=1 时才测。
  if (process.env.SLOW === '1') {
    const mark = (await evaluate(PROBE)).bubble;
    await fetch('http://127.0.0.1:5001/agent/reminder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: 1, text: '别忘了做错题本' }),
    });
    let hitText = '';
    for (let i = 0; i < 40; i++) {
      await sleep(2500);
      const s = await evaluate(PROBE);
      if (s.bubble && s.bubble !== mark) { hitText = s.bubble; break; }
    }
    out.reminderBubble = hitText;
    step(`②b 定 1 分钟提醒 → 气泡变成 "${hitText}"（应为「别忘了做错题本」）`);
  }

  // ③ 聊天抽屉
  await click('#chatToggle');
  await sleep(500);
  out.c = await evaluate(PROBE);
  step(`③ 点「跟她说句话」→ 抽屉打开=${out.c.chatOpen}`);

  // ③b 真发一句话（走完整 agent 链路，顺带验证小聊天是通的）
  if (process.env.SKIP_CHAT !== '1') {
    await evaluate(`(() => { document.querySelector('#ask').value = '只回两个字：收到'; return 'ok'; })()`);
    await click('#send');
    const waitMs = Number(process.env.WAIT_CHAT_MS || 12000);
    const base = (out.c.log || '').length;
    for (let i = 0; i < Math.ceil(waitMs / 3000); i++) {
      await sleep(3000);
      out.d = await evaluate(PROBE);
      if ((out.d.log || '').length > base + 6) break;      // 「老师：」后面真的来字了
    }
    const after = (out.d && out.d.log) || '';
    out.chatReplied = after.length > base + 6;
    step(`③b 小聊天收到回复=${out.chatReplied}；日志：${JSON.stringify(after.slice(-120))}`);
    await shot('_shot_pet_3.png');
  }

  // ③c 「存到本机」：把浏览器下到的运行时交给服务端落盘（之后离线也能用）
  out.save = { offered: out.a.saveBtnShown !== 'none' };
  if (out.save.offered) {
    await click('#save');
    await sleep(9000);
    const probeSave = await evaluate(PROBE);
    const r = await fetch('http://127.0.0.1:5001/agent/live2d/vendor/pixi.min.js');
    out.save.pixiStatus = r.status;
    out.save.pixiBytes = (await r.text()).length;
    step(`③c 点「存到本机」→ 服务端 /agent/live2d/vendor/pixi.min.js = ${out.save.pixiStatus}（${out.save.pixiBytes} 字节），按钮显示=${probeSave.saveBtnShown}`);
    await shot('_shot_pet_4.png');
  } else {
    step('③c 「存到本机」按钮没出现（说明用的是本地运行时，本来就不需要存）');
  }

  // ③d 「20 分钟后提醒我」按钮：点一下要真的定上提醒，并且气泡要回话
  if (process.env.CHECK_REMIND === '1') {
    const before = await (await fetch('http://127.0.0.1:5001/agent/pet/status')).json();
    await click('#remind');
    await sleep(2500);
    const after = await (await fetch('http://127.0.0.1:5001/agent/pet/status')).json();
    const snap = await evaluate(PROBE);
    out.remind = { pendingBefore: before.pending_reminders, pendingAfter: after.pending_reminders, bubble: snap.bubble };
    step(`③d 点「提醒我」→ 待发提醒 ${before.pending_reminders} → ${after.pending_reminders}，气泡="${snap.bubble}"`);
  }

  out.console = consoleMsgs.slice(0, 12);
  const errs = consoleMsgs.filter((m) => m.startsWith('console-error'));
  const netNotes = consoleMsgs.filter((m) => m.startsWith('net-'));
  step(`④ 页面 JS error ${errs.length} 条；网络层提示 ${netNotes.length} 条（查不到本地 Live2D 运行时属于预期）`);
  out.verdict = {
    fallbackWorks: out.a.fallbackW > 0 || out.a.canvasShown === 'block',
    bubbleDrivenByServer: out.bubbleChanged === true,
    chatOpens: out.c.chatOpen === true,
    live2dRuntimeSaved: out.save.pixiStatus === 200 || out.save.offered === false,
    noPageErrors: errs.length === 0,
  };
  if (process.env.FALLBACK === '1') {
    out.verdict.fallbackImageVisible = out.a.fallbackShown === 'block' && out.a.fallbackW > 0;
    out.verdict.fallbackModeDeclared = out.a.mode === '备用形象';
  }
  if (out.remind) {
    out.verdict.remindButtonWorks = out.remind.pendingAfter === out.remind.pendingBefore + 1
      && String(out.remind.bubble || '').indexOf('20 分钟') >= 0;
  }
  if (out.reminderBubble !== undefined) out.verdict.reminderReachesPet = out.reminderBubble === '别忘了做错题本';
  console.log('\n结论：' + JSON.stringify(out.verdict));
  console.log('控制台：' + JSON.stringify(out.console, null, 1));
  ws.close();
}

main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
