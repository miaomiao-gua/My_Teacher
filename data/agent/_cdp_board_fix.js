/* 用**你那个对话里存着的那份 spec**（单步画板）在真浏览器里看效果：
   标题是不是只出现一次、有没有多余的「上一步/下一步」、✕ 关闭能不能用。 */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// 就是用户那条会话里存的 last_ui，原样搬过来
const SPEC = {
  title: '圆的坐标几何',
  items: [{
    type: 'board', title: '圆的坐标几何', height: 400,
    xRange: [-2, 8], yRange: [-2, 8],
    steps: [{
      caption: '画一个圆并标出圆心和半径',
      shapes: [
        { kind: 'circle', center: [2, 3], r: 4, label: '圆' },
        { kind: 'dot', point: [2, 3], label: '圆心 (2,3)' },
        { kind: 'line', from: [2, 3], to: [6, 3], label: '半径 = 4' },
      ],
    }],
  }],
};

async function main() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const page = list.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res) => { ws.onopen = res; });
  let id = 0; const pending = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); } };
  const send = (method, params) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const ev = async (x) => {
    const r = await send('Runtime.evaluate', { expression: x, returnByValue: true });
    if (r.exceptionDetails) throw new Error('页面报错：' + JSON.stringify(r.exceptionDetails.exception));
    return r.result.value;
  };
  async function shot(name, clip) {
    const p = { format: 'png' };
    if (clip) p.clip = Object.assign({ scale: 1 }, clip);
    const r = await send('Page.captureScreenshot', p);
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  }

  await send('Page.enable'); await send('Runtime.enable');
  const out = { steps: [] };
  const step = (s, d) => out.steps.push(s + (d ? ' → ' + d : ''));
  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(5000);
    out.ver = await ev("window.AgentUI.version");
    // 清掉工作台里原有内容，只放这一份，方便看清
    await ev("document.querySelectorAll('.au-surface-item .au-surface-close').forEach(function(b){ b.onclick(); }); 'cleared'");
    await sleep(200);
    await ev(`window.__app.chat.renderSpec(${JSON.stringify(SPEC)})`);
    await sleep(800);

    out.dom = await ev(`(function(){
      const it = document.querySelector('.au-surface-item');
      const txt = (it.innerText || '').replace(/\\s+/g, ' ').trim();
      return {
        titleCount: (txt.match(/圆的坐标几何/g) || []).length,
        hasStepBar: !!it.querySelector('.au-board-bar'),
        hasNextBtn: txt.indexOf('下一步') >= 0,
        hasStepWord: /第 \\d+ \\/ \\d+ 步/.test(txt),
        caption: txt,
        staticClass: !!it.querySelector('.au-board-static'),
        closeBtns: document.querySelectorAll('.au-surface-close').length,
      };
    })()`);
    step('单步画板的实际 DOM（版本 v' + out.ver + '）', JSON.stringify(out.dom));
    await shot('_board_fixed.png');

    // ✕ 关闭：点掉这个窗口，工作台应该回到「空 + 占位提示」
    const before = await ev("document.querySelectorAll('.au-surface-item').length");
    await ev("document.querySelector('.au-surface-close').onclick(); 'closed'");
    await sleep(300);
    out.afterClose = await ev(`(function(){
      return {items: document.querySelectorAll('.au-surface-item').length,
              placeholder: !!document.querySelector('.au-stage-empty'),
              placeholderText: (document.querySelector('.au-stage-empty') || {}).innerText || ''};
    })()`);
    step('点「✕ 关闭」', before + ' 个窗口 → ' + out.afterClose.items + ' 个，占位提示 = ' +
      out.afterClose.placeholder);
    await shot('_board_closed.png');
  } catch (err) { out.error = err.message; }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
