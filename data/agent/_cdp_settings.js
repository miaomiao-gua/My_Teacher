/* ============================================================================
 * 探针：桌宠「设置」面板 + 换形象清单（本次新增的东西，必须真点一遍）
 * ----------------------------------------------------------------------------
 * 要回答的问题（都得有证据，不看截图猜）：
 *   ① 顶栏「⚙ 设置」点得开、面板里三组都在（形象 / 提醒 / 窗口）
 *   ② 换形象清单每行是否**真的**带着「人话名字 / 自带动作 or 程序演绎 / 体积」
 *      —— 清单要扫盘读服务端，所以这条同时验证了接口和渲染
 *   ③ 清单里点一只别的模型 → 页面上的角色**真的换了**（读模型 id，不看截图）
 *   ④ 在设置面板里定一条提醒 → 面板里那份清单**立刻多一条**（不然定了就消失）
 *   ⑤ 全程页面没有 JS 报错
 * 用法：先起 Edge（--remote-debugging-port=9333），再 node data/agent/_cdp_settings.js
 *      可选：RESTORE=0 表示测完不把形象换回去（默认换回原来那只）
 * ========================================================================== */
'use strict';

const CDP = process.env.CDP_PORT || 9333;
const URL = process.env.APP_URL || 'http://127.0.0.1:5001/agent/pet';
const BASE = process.env.BASE_URL || 'http://127.0.0.1:5001';
const fs = require('fs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PANEL = `(() => {
  const box = document.getElementById('settings');
  const rows = Array.from(document.querySelectorAll('#pet-picker .pet-pick-row'));
  const list = document.getElementById('remindList');
  return {
    panelOn: box ? box.classList.contains('on') : null,
    panelText: box ? box.textContent.replace(/\\s+/g, ' ').slice(0, 400) : null,
    groups: box ? Array.from(box.querySelectorAll('h3')).map((h) => h.textContent) : [],
    curChip: (document.getElementById('curModel') || {}).textContent || null,
    pickerOpen: !!document.getElementById('pet-picker'),
    pickerRows: rows.map((r) => ({
      id: r.dataset.modelId,
      name: (r.querySelector('.pet-pick-name') || {}).textContent || '',
      meta: (r.querySelector('.pet-pick-meta') || {}).textContent || '',
      isCurrent: !!r.querySelector('.pet-pick-now'),
    })),
    reminders: list ? Array.from(list.querySelectorAll('li')).map((li) => li.textContent) : [],
    petModel: window.PetPage && window.PetPage.model() ? window.PetPage.model().id : null,
    petKind: window.PetPage && window.PetPage.kind ? window.PetPage.kind() : null,
    prefs: (() => { try { return JSON.parse(localStorage.getItem('pet_win_prefs_v1') || '{}'); } catch (e) { return null; } })(),
  };
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
      logs.push('net: ' + String(m.params.entry.text).slice(0, 140));
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
  const sessionBefore = await (await fetch(BASE + '/agent/pet/status')).json();
  const modelBefore = (await (await fetch(BASE + '/agent/live2d/models')).json()).current;

  const t = await (await fetch(`http://127.0.0.1:${CDP}/json/new?` + encodeURIComponent(URL), { method: 'PUT' })).json();
  const { ws, send, evaluate, shot, logs } = await connect(t.webSocketDebuggerUrl);
  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1180, height: 860, deviceScaleFactor: 1, mobile: false });
  await sleep(9000);

  // ① 打开设置面板
  const clicked = await evaluate(`(() => { const b = document.getElementById('settingsBtn'); if (!b) return false; b.click(); return true; })()`);
  await sleep(1200);
  let s = await evaluate(PANEL);
  out.steps.push('① 点「⚙ 设置」（找得到按钮=' + clicked + '）→ 面板打开=' + s.panelOn +
    '，分组=' + JSON.stringify(s.groups));
  out.steps.push('   当前形象牌：' + JSON.stringify(s.curChip));
  await shot('_shot_settings_1.png');

  // ② 打开换形象清单，检查每行的信息量
  await evaluate(`(() => { document.getElementById('pickModel').click(); return 1; })()`);
  await sleep(2500);
  s = await evaluate(PANEL);
  out.steps.push('② 换形象清单：' + s.pickerRows.length + ' 行');
  s.pickerRows.slice(0, 8).forEach((r) => {
    out.steps.push('   · ' + r.name + ' ｜ ' + r.meta + (r.isCurrent ? ' ｜← 当前' : ''));
  });
  await shot('_shot_settings_2.png');

  const rowsOk = s.pickerRows.length > 0 &&
    s.pickerRows.every((r) => r.name.trim() && /MB/.test(r.meta) && /动作/.test(r.meta));
  const currentMarked = s.pickerRows.filter((r) => r.isCurrent).length === 1;

  // ③ 点一只**不是当前**的模型 → 角色真的换
  const other = s.pickerRows.find((r) => !r.isCurrent);
  let switched = null;
  if (other) {
    const targetId = other.id;
    await evaluate(`(() => {
      const row = Array.from(document.querySelectorAll('#pet-picker .pet-pick-row'))
        .find((r) => r.dataset.modelId === ${JSON.stringify(targetId)});
      if (row) row.click();
      return !!row;
    })()`);
    await sleep(11000);
    const after = await evaluate(PANEL);
    const server = (await (await fetch(BASE + '/agent/live2d/models')).json()).current;
    switched = { target: targetId, pageModel: after.petModel, serverModel: server && server.id,
                 kind: after.petKind, chip: after.curChip };
    out.steps.push('③ 点「' + other.name + '」→ 页面模型=' + after.petModel + '，服务端记录=' + (server && server.id) +
      '，形象路线=' + after.petKind);
    await shot('_shot_settings_3.png');
  }

  // ④ 面板里定一条提醒 → 清单立刻多一条
  const beforeList = (await evaluate(PANEL)).reminders;
  const pendingBefore = (await (await fetch(BASE + '/agent/pet/status')).json()).pending_reminders;
  await evaluate(`(() => { document.getElementById('ask2').value = '探针：复习第 3 题'; return 1; })()`);
  await evaluate(`(() => {
    const b = Array.from(document.querySelectorAll('#settings [data-min]')).find((x) => x.dataset.min === '20');
    if (b) b.click();
    return !!b;
  })()`);
  await sleep(2500);
  const afterPanel = await evaluate(PANEL);
  const pendingAfter = (await (await fetch(BASE + '/agent/pet/status')).json()).pending_reminders;
  out.steps.push('④ 面板里定「20 分钟后」提醒 → 待发提醒 ' + pendingBefore + ' → ' + pendingAfter +
    '，面板清单 ' + beforeList.length + ' → ' + afterPanel.reminders.length + ' 条');
  out.steps.push('   清单内容：' + JSON.stringify(afterPanel.reminders));
  await shot('_shot_settings_4.png');

  // ⑤ 窗口开关：勾上要记住（桌面版靠它决定置顶）
  await evaluate(`(() => { const c = document.getElementById('alwaysTop'); c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); return 1; })()`);
  await sleep(900);
  const prefState = (await evaluate(PANEL)).prefs;
  out.steps.push('⑤ 勾「一直在最前面」→ 本机偏好 = ' + JSON.stringify(prefState));

  // 收尾：把形象换回去（别把学生默认形象留在探针挑的那只上）
  let restored = null;
  if (process.env.RESTORE !== '0' && modelBefore && switched && switched.serverModel !== modelBefore.id) {
    await fetch(BASE + '/agent/pet/model', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: modelBefore.id }),
    });
    restored = (await (await fetch(BASE + '/agent/live2d/models')).json()).current.id;
    out.steps.push('⑥ 收尾：形象换回 ' + modelBefore.id + ' → ' + restored);
  }

  const errs = logs.filter((l) => l.indexOf('error') === 0);
  out.steps.push('⑦ 页面 JS error ' + errs.length + ' 条' + (errs.length ? '：' + errs.slice(0, 3).join(' | ') : ''));

  out.verdict = {
    '设置面板能打开': s.panelOn === true,
    '三组都在（形象/提醒/窗口）': JSON.stringify(s.groups) === JSON.stringify(['形象', '提醒', '窗口']),
    '清单每行都有名字+动作类型+体积': rowsOk,
    '当前选中标出来了（且只标一个）': currentMarked,
    '点清单真的换了模型（页面+服务端一致）': !!(switched && switched.pageModel === switched.target && switched.serverModel === switched.target),
    '换完还是 Live2D（没掉回备用形象）': !!(switched && String(switched.kind || '').indexOf('live2d') === 0),
    '面板里定提醒→清单立刻多一条': pendingAfter === pendingBefore + 1 && afterPanel.reminders.length > beforeList.length,
    '窗口开关被记住': !!(prefState && prefState.alwaysTop === true),
    '没有页面报错': errs.length === 0,
  };
  out.console = logs.slice(0, 8);
  out.sessionBefore = sessionBefore;
  console.log(out.steps.join('\n'));
  console.log('\n结论：' + JSON.stringify(out.verdict, null, 1));
  fs.writeFileSync('D:/My_teacher/data/agent/_settings_report.json', JSON.stringify(out, null, 2));
  ws.close();
}

main().catch((e) => { console.error('探针失败：' + ((e && e.message) || e)); process.exit(1); });
