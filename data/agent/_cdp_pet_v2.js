/* ============================================================================
 * 桌宠外观 + 设置里的「大小 / 上传 / 语音」验收探针
 * ----------------------------------------------------------------------------
 * 学生这次的要求：
 *   ① 她身上和头顶都**不许有控件**（去掉脚下的「换形象 / 放大」）
 *   ② 不需要独立桌宠网页（入口撤掉）
 *   ③ 设置里能调**大小**、能**上传自己的 Live2D**、能配 **TTS**
 * 每条都要有数字/接口结果做证据。
 * ========================================================================== */
'use strict';
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PET = `(() => {
  const box = document.querySelector('.au-pet');
  if (!box) return { error: '没有桌宠' };
  const r = box.getBoundingClientRect();
  const stage = box.querySelector('.au-pet-stage').getBoundingClientRect();
  const kids = Array.from(box.children).map(c => String(c.className));
  const btns = Array.from(box.querySelectorAll('button')).length;
  const above = [];
  Array.from(box.children).forEach((c) => {
    const b = c.getBoundingClientRect();
    const cs = getComputedStyle(c);
    if (cs.display !== 'none' && b.height > 1 && b.bottom <= stage.top + 2) above.push(String(c.className));
  });
  return { 宽: Math.round(r.width), 高: Math.round(r.height), 孩子: kids, 按钮数: btns,
           头顶上方的元素: above, 形象: (window.__app && __app.pet && __app.pet.pet) ? __app.pet.pet.kind : null };
})()`;

async function main() {
  const t = await (await fetch(`http://127.0.0.1:${PORT}/json/new?` + encodeURIComponent(APP), { method: 'PUT' })).json();
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
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); rej(new Error(method + ' 超时')); } }, 25000);
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
  async function clickAt(x, y, button) {
    const b = button || 'left';
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
      await send('Input.dispatchMouseEvent', { type, x: Math.round(x), y: Math.round(y), button: b,
        buttons: type === 'mousePressed' ? (b === 'right' ? 2 : 1) : 0, clickCount: type === 'mouseMoved' ? 0 : 1 });
      await sleep(90);
    }
    await sleep(400);
  }

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1707, height: 1024, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: APP });
  await sleep(9000);

  const pet0 = await evaluate(PET);
  console.log('① 她身上有什么：' + JSON.stringify(pet0));
  await shot('_shot_pet_clean.png');

  // ② 右键她 → 菜单
  const r = await evaluate(`(() => { const b = document.querySelector('.au-pet').getBoundingClientRect();
    return [b.x + b.width / 2, b.y + b.height * 0.55]; })()`);
  await clickAt(r[0], r[1], 'right');
  const menu = await evaluate(`(() => {
    const m = document.querySelector('.au-menu');
    return m ? Array.from(m.querySelectorAll('.au-menu-item')).map(i => i.textContent.trim()) : null;
  })()`);
  console.log('② 右键她的菜单：' + JSON.stringify(menu));
  await shot('_shot_pet_menu.png');
  await evaluate(`(() => { const m = document.querySelector('.au-menu'); if (m) m.remove(); return 1; })()`);

  // ③ 打开设置：桌宠组 + 语音组的内容
  await evaluate(`(() => { document.querySelector('.au-set-btn').click(); return 1; })()`);
  await sleep(2600);
  const panel = await evaluate(`(() => {
    const card = document.querySelector('.au-modal-card');
    if (!card) return { error: '面板没打开' };
    const titles = Array.from(card.querySelectorAll('.au-set-title')).map(e => e.textContent.trim());
    const petGroup = Array.from(card.querySelectorAll('.au-set-group'))
      .find(g => (g.querySelector('.au-set-title') || {}).textContent === '桌宠');
    const ttsGroup = Array.from(card.querySelectorAll('.au-set-group'))
      .find(g => (g.querySelector('.au-set-title') || {}).textContent || ''.indexOf('语音') >= 0);
    const scale = card.querySelector('[data-key="pet_scale"]');
    const pick = card.querySelector('input[type=file]');
    const say = Array.from(card.querySelectorAll('button')).find(b => b.textContent.indexOf('听一下') >= 0);
    return {
      分组: titles,
      有大小滑杆: !!scale && scale.type === 'range',
      大小范围: scale ? [scale.min, scale.max, scale.value] : null,
      有形象下拉: !!card.querySelector('[data-pet-model]'),
      上传框是选目录的: !!pick && pick.hasAttribute('webkitdirectory'),
      有试听按钮: !!say,
      语音下拉: Array.from(card.querySelectorAll('[data-key="tts_provider"] option')).map(o => o.value),
      嗓子选项数: card.querySelectorAll('[data-key="tts_windows_voice"] option').length,
      试听状态文案: (card.querySelector('.au-set-hint') || {}).textContent,
    };
  })()`);
  console.log('③ 设置面板：' + JSON.stringify(panel, null, 1));
  await shot('_shot_settings_pet_tts.png');

  // ④ 拖动「大小」滑杆 → 她的框应该真的变大
  const scaled = await evaluate(`(async () => {
    const card = document.querySelector('.au-modal-card');
    const sc = card.querySelector('[data-key="pet_scale"]');
    const before = document.querySelector('.au-pet').getBoundingClientRect();
    sc.value = '1.6';
    sc.dispatchEvent(new Event('input', { bubbles: true }));
    // .au-pet 上有 width/height 的 transition(.18s)：必须等动画走完再量，
    // 否则量到的还是旧尺寸（我第一版就是这么误判成"滑杆没生效"的）
    await new Promise(r => setTimeout(r, 600));
    const after = document.querySelector('.au-pet').getBoundingClientRect();
    return { 前: [Math.round(before.width), Math.round(before.height)],
             后: [Math.round(after.width), Math.round(after.height)],
             当前scale: window.__app.pet.scale };
  })()`);
  console.log('④ 拖大小滑杆：' + JSON.stringify(scaled));
  await sleep(600);
  await shot('_shot_pet_bigger.png');

  // ⑤ 先把语音打开（默认是关的），再试听
  const ttsApi = await evaluate(`(async () => {
    await fetch('/agent/settings/all', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tts_provider: 'windows', tts_windows_voice: 0 }) });
    const r = await fetch('/agent/tts/say', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '你好，我是艾琳老师。' }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); return { ok: false, status: r.status, detail: d.detail }; }
    const b = await r.blob();
    // 界面上那个「听一下」按钮也要能用（本地合成很快）
    const card = document.querySelector('.au-modal-card');
    const say = Array.from(card.querySelectorAll('button')).find(x => x.textContent.indexOf('听一下') >= 0);
    if (say) { say.click(); await new Promise(res => setTimeout(res, 4000)); }
    const hints = Array.from(card.querySelectorAll('.au-set-hint')).map(h => h.textContent);
    return { ok: true, status: r.status, type: r.headers.get('content-type'), kb: Math.round(b.size / 1024),
             界面反馈: hints.filter(t => t.indexOf('播放') >= 0 || t.indexOf('没播') >= 0 || t.indexOf('合成') >= 0).slice(-1)[0] };
  })()`);
  console.log('⑤ 试听：' + JSON.stringify(ttsApi));
  // 试听完把语音关回去（默认状态，别替学生决定开不开声音）
  await evaluate(`(async () => { await fetch('/agent/settings/all', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tts_provider: 'off' }) }); return 1; })()`);

  // ⑥ 上传接口（用「不是模型」的目录试，应该给人话拒绝；真上传在单测里已覆盖）
  const upApi = await evaluate(`(async () => {
    const fd = new FormData();
    fd.append('folder', 'x');
    fd.append('files', new Blob([new Uint8Array([1,2,3])]), 'readme.txt');
    fd.append('paths_readme.txt', 'readme.txt');
    const r = await fetch('/agent/pet/model/upload', { method: 'POST', body: fd });
    const d = await r.json().catch(() => ({}));
    return { status: r.status, detail: d.detail };
  })()`);
  console.log('⑥ 上传非模型目录：' + JSON.stringify(upApi));

  // 收尾：关面板、把大小改回 1.0（别把学生的界面留在放大状态）
  await evaluate(`(async () => {
    const card = document.querySelector('.au-modal-card');
    const sc = card && card.querySelector('[data-key="pet_scale"]');
    if (sc) { sc.value = '1'; sc.dispatchEvent(new Event('input', { bubbles: true })); }
    await fetch('/agent/settings/all', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pet_scale: 1.0 }) });
    const b = card && card.querySelector('[data-act="close"]'); if (b) b.click();
    return 1;
  })()`);

  const errs = logs.filter((l) => l.indexOf('error') === 0);
  console.log('\n页面 JS error ' + errs.length + ' 条' + (errs.length ? '\n' + errs.slice(0, 4).join('\n') : ''));
  console.log('结论：' + JSON.stringify({
    她身上没有控件: pet0.按钮数 === 0 && pet0.孩子.length === 1,
    头顶上方也没有控件: Array.isArray(pet0.头顶上方的元素) && pet0.头顶上方的元素.length === 0,
    右键有菜单: Array.isArray(menu) && menu.length >= 3,
    设置里有桌宠和语音两组: panel.分组 && panel.分组.join('|').indexOf('桌宠') >= 0 && panel.分组.join('|').indexOf('语音') >= 0,
    有大小滑杆: panel.有大小滑杆,
    有形象下拉和上传选目录: panel.有形象下拉 && panel.上传框是选目录的,
    大小真的生效: scaled.后[0] > scaled.前[0] + 30,
    有试听按钮: panel.有试听按钮,
    试听接口出音频: ttsApi.ok && ttsApi.kb > 5,
    上传非模型被拒: upApi.status === 400,
    没有页面报错: errs.length === 0,
  }, null, 1));
  ws.close();
}
main().catch((e) => { console.error('探针失败：' + (e && e.message)); process.exit(1); });
