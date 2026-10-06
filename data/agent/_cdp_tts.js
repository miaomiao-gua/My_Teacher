/* ============================================================================
 * TTS 接线验收：气泡出现时，她**真的**会说话吗？
 * ----------------------------------------------------------------------------
 * 要证明的：
 *   ① 设置里的「气泡自动念出来」真的接到了小玩偶身上（__app.pet.speak === true）
 *   ② 气泡一出现 → 真的发了一次 /agent/tts/say，并播了音频
 *   ③ 念出来的是**洗过的文字**：markdown/链接/emoji 都要去掉，太长要截断
 *   ④ 「老师在想了…」这类过程提示**不念**（念了只会烦人）
 *   ⑤ 新的一句来了会打断上一句（不会叠着念）
 *
 * 注意：浏览器默认禁止"用户没交互就自动播放"。验证时必须用
 *   --autoplay-policy=no-user-gesture-required 起 Edge，
 * 否则测出来的是浏览器策略，不是我们的 bug。
 * 用法：node data/agent/_cdp_tts.js
 * ========================================================================== */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('WS 连不上')); });
  let id = 0; const pending = new Map(); const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      logs.push(m.params.type + ': ' + (m.params.args || []).map((a) => String(a.value || a.description || '')).join(' ').slice(0, 160));
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
  const shot = async (n) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + n, Buffer.from(r.data, 'base64'));
  };

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(9000);

  // 装上计数器：记下每次 TTS 请求的文本 + 播放状态
  await evaluate(`(() => {
    if (window.__ttsHook) return 1;
    window.__ttsCalls = [];
    const orig = window.fetch;
    window.fetch = function (url, init) {
      const u = String(url);
      if (u.indexOf('/agent/tts/say') >= 0) {
        let text = '';
        try { text = JSON.parse((init && init.body) || '{}').text || ''; } catch (e) {}
        window.__ttsCalls.push({ text: text, at: Date.now() });
      }
      return orig.apply(this, arguments);
    };
    const OrigAudio = window.Audio;
    window.__played = [];
    window.Audio = function (src) {
      const a = new OrigAudio(src);
      const play = a.play.bind(a);
      a.play = function () {
        window.__played.push({ src: String(src).slice(0, 24), at: Date.now() });
        return play();
      };
      return a;
    };
    window.__ttsHook = 1;
    return 1;
  })()`);

  const state = await evaluate(`(() => ({
    speakOn: !!(window.__app && __app.pet && __app.pet.speak),
    hasApi: !!(window.PetCore && window.PetCore.speak),
    清洗: window.PetCore.cleanForSpeech('**重点**：看 https://example.com/a 里的 x^2 和第 3 题 😀'),
    长文截断: window.PetCore.cleanForSpeech('啊'.repeat(300)).length,
  }))()`);
  console.log('① 接线与清洗：' + JSON.stringify(state, null, 1));

  // ② 真的说一句（带 markdown / 链接 / emoji，看发出去的是什么）
  const spoken = await evaluate(`(async () => {
    window.__ttsCalls = []; window.__played = [];
    window.__app.pet.show('**下课啦**，记得做错题本：https://example.com/x 😀');
    await new Promise(r => setTimeout(r, 3500));
    return { 请求数: window.__ttsCalls.length, 发出去的文本: (window.__ttsCalls[0] || {}).text,
             播放次数: window.__played.length,
             气泡文案: (document.querySelector('.pet-bubble') || {}).textContent };
  })()`);
  console.log('② 气泡 → 说话：' + JSON.stringify(spoken, null, 1));
  await shot('_shot_tts_speaking.png');

  // ③ 过程提示不念
  const skipped = await evaluate(`(async () => {
    window.__ttsCalls = [];
    window.__app.pet.show('老师在想了…');
    await new Promise(r => setTimeout(r, 1500));
    return { 请求数: window.__ttsCalls.length };
  })()`);
  console.log('③ 「老师在想了…」：' + JSON.stringify(skipped));

  // ④ 连续两句 → 应该两次请求，且第二句会打断第一句（不叠着念）
  const twice = await evaluate(`(async () => {
    window.__ttsCalls = []; window.__played = [];
    window.__app.pet.show('第一句话在这里。');
    await new Promise(r => setTimeout(r, 200));
    window.__app.pet.show('第二句话来了，应该打断上一句。');
    await new Promise(r => setTimeout(r, 3000));
    return { 请求数: window.__ttsCalls.length, 文本: window.__ttsCalls.map(c => c.text), 播放次数: window.__played.length };
  })()`);
  console.log('④ 连续两句：' + JSON.stringify(twice));

  // ⑤ 关掉语音后不该再念
  const off = await evaluate(`(async () => {
    await fetch('/agent/settings/all', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tts_auto_speak: false }) });
    window.__app.pet.setSpeak(false);
    window.__ttsCalls = [];
    window.__app.pet.show('这句话不该被念出来。');
    await new Promise(r => setTimeout(r, 1200));
    const n = window.__ttsCalls.length;
    // 还原成打开（学生刚要求调 TTS，别给他关回去）
    await fetch('/agent/settings/all', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tts_auto_speak: true }) });
    window.__app.pet.setSpeak(true);
    return { 请求数: n, 已还原: window.__app.pet.speak };
  })()`);
  console.log('⑤ 关掉再试：' + JSON.stringify(off));

  const errs = logs.filter((l) => l.indexOf('error') === 0);
  console.log('\n控制台 error ' + errs.length + ' 条' + (errs.length ? '\n' + errs.slice(0, 5).join('\n') : ''));
  console.log('结论：' + JSON.stringify({
    自动念接上了: state.speakOn === true,
    清洗掉了markdown和链接: state.清洗.indexOf('**') < 0 && state.清洗.indexOf('http') < 0 && state.清洗.indexOf('😀') < 0,
    长文被截断: state.长文截断 <= 121,
    气泡会触发合成: spoken.请求数 === 1,
    发的就是洗过的文字: String(spoken.发出去的文本 || '').indexOf('http') < 0 && String(spoken.发出去的文本 || '').indexOf('**') < 0,
    真的播了: spoken.播放次数 >= 1,
    过程提示不念: skipped.请求数 === 0,
    连续两句都会念: twice.请求数 === 2,
    关掉就不念: off.请求数 === 0 && off.已还原 === true,
  }, null, 1));
  ws.close();
}
main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
