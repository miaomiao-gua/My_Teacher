/* ============================================================================
 * 「他能不能做出完整的 UI」——真浏览器验证
 * ----------------------------------------------------------------------------
 * 用**真实工具产出的**那份「一节课」spec（四个标签页 + 小测 + 步骤），验：
 *   1. 整页没有降级块（au-unknown）/ 报错块（au-error）
 *   2. 每个标签页里的东西都真的画出来了
 *   3. 每个工具都**能动手**：拖几何顶点 / 改数据 / 改系数 / 敲公式 / 点小测
 *   4. 截图留档（人眼能看的那种）
 * ========================================================================== */
'use strict';
const fs = require('fs');
const PORT = process.env.CDP_PORT || 9333;
const APP = process.env.APP_URL || 'http://127.0.0.1:5001/agent/app';
const SPEC = JSON.parse(fs.readFileSync('D:/My_teacher/data/agent/_lesson_spec.json', 'utf8'));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

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
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync('D:/My_teacher/data/agent/' + name, Buffer.from(r.data, 'base64'));
  };
  const rectOf = (sel) => ev(`(function(){const e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null;
    const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,h:r.height};})()`);
  const clickAt = async (x, y) => {
    const p = { x: Math.round(x), y: Math.round(y), button: 'left', clickCount: 1 };
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseMoved', buttons: 0 }, p));
    await sleep(60);
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mousePressed', buttons: 1 }, p));
    await send('Input.dispatchMouseEvent', Object.assign({ type: 'mouseReleased', buttons: 0 }, p));
    await sleep(350);
  };
  const dragTo = async (from, dx, dy) => {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(from.x), y: Math.round(from.y), buttons: 0 });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: Math.round(from.x), y: Math.round(from.y), button: 'left', buttons: 1, clickCount: 1 });
    await sleep(60);
    for (let i = 1; i <= 6; i++) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(from.x + dx * i / 6), y: Math.round(from.y + dy * i / 6), button: 'left', buttons: 1 });
      await sleep(30);
    }
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: Math.round(from.x + dx), y: Math.round(from.y + dy), button: 'left', buttons: 0, clickCount: 1 });
    await sleep(250);
  };

  await send('Page.enable'); await send('Runtime.enable');
  const out = { steps: [] };
  const step = (s, d) => out.steps.push(s + (d ? ' → ' + d : ''));
  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send('Page.navigate', { url: APP });
    await sleep(5000);
    out.ver = await ev('window.AgentUI.version');
    await ev("document.querySelectorAll('.au-surface-close').forEach(function(b){ b.onclick(); }); 'x'");
    await sleep(200);
    await ev(`window.__app.chat.renderSpec(${JSON.stringify(SPEC)})`);
    await sleep(1200);

    // 1) 整页结构
    out.overview = await ev(`(function(){
      const st = document.querySelector('.au-stage');
      const q = s => st.querySelectorAll(s).length;
      return {unknown: q('.au-unknown'), errors: q('.au-error'),
        tabs: Array.from(st.querySelectorAll('.au-tab-btn')).map(t => t.textContent.trim()),
        callout: q('.au-callout'), steps: q('.au-steps'), quiz: q('.au-quiz'),
        geometry: q('.au-geo'), datalab: q('.au-datalab'), solver: q('.au-solver'), calc: q('.au-calc'),
        canvases: q('canvas'), rows: q('.au-geo-row'), inputs: q('.au-dl-input')};
    })()`);
    step('整页（v' + out.ver + '）',
      '降级块 ' + out.overview.unknown + '、报错块 ' + out.overview.errors +
      '、标签页 ' + JSON.stringify(out.overview.tabs) + '、画布 ' + out.overview.canvases + ' 块');
    await shot('_full_lesson_tab1.png');

    // 2) 标签页①：拖几何顶点，测量要跟着变
    const measureText = () => ev("Array.from(document.querySelectorAll('.au-geo-row')).map(function(r){return r.textContent;}).join(' | ')");
    const m0 = await measureText();
    const geoCanvas = await rectOf('.au-geo .au-canvas');
    // 抓一个可拖的点：用页面里的钩子算出 C 的屏幕位置
    const cPos = await ev(`(function(){const g=document.querySelector('.au-geo');
      const p=g._screenOf('C'); const r=g.querySelector('.au-canvas').getBoundingClientRect();
      return {x:r.x+p.x, y:r.y+p.y};})()`);
    await dragTo(cPos, -70, -60);
    const m1 = await measureText();
    step('拖几何顶点', (m0 !== m1 ? '测量值变了 ✓' : '测量值没变 ✗') + '　' + m1.slice(0, 90));
    await shot('_full_lesson_drag.png');

    // 3) 标签页②：改数据，统计量跟着变
    const tabBtn = async (i) => ev("(function(){const b=document.querySelectorAll('.au-stage .au-tab-btn')[" + i + "]; const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
    const t2 = await tabBtn(1); await clickAt(t2.x, t2.y);
    const statText = () => ev("Array.from(document.querySelectorAll('.au-datalab .au-geo-row')).map(function(r){return r.textContent;}).join(' | ')");
    const s0 = await statText();
    await ev(`(function(){const i=document.querySelectorAll('.au-datalab .au-dl-input')[5];
      i.value='99'; i.dispatchEvent(new Event('input')); return i.value;})()`);
    await sleep(400);
    const s1 = await statText();
    step('改一个数据', (s0 !== s1 ? '统计量变了 ✓' : '统计量没变 ✗') + '　' + s1.slice(0, 80));
    await shot('_full_lesson_tab2.png');

    // 4) 标签页③：改系数，根跟着变
    const t3 = await tabBtn(2); await clickAt(t3.x, t3.y);
    const solveText = () => ev("(document.querySelector('.au-solver-steps')||{}).textContent || ''");
    const k0 = await solveText();
    await ev(`(function(){const i=document.querySelector('.au-solver .au-solver-input');
      i.value='1'; i.dispatchEvent(new Event('input'));
      const ins=document.querySelectorAll('.au-solver .au-solver-input');
      ins[1].value='-7'; ins[1].dispatchEvent(new Event('input')); return ins.length;})()`);
    await sleep(400);
    const k1 = await solveText();
    step('改方程系数', (k0 !== k1 ? '过程与根重算了 ✓' : '没变 ✗') + '　' + k1.slice(0, 70));
    await shot('_full_lesson_tab3.png');

    // 5) 标签页④：计算器里敲公式
    const t4 = await tabBtn(3); await clickAt(t4.x, t4.y);
    out.calcRows = await ev("document.querySelector('.au-calc')._rows.map(function(r){return r.raw+'['+r.kind+']';})");
    step('计算器里的公式', out.calcRows.join(' '));
    await shot('_full_lesson_tab4.png');

    // 6) 小测：点一下选项，看有没有当场判分
    const opt = await rectOf('.au-stage .au-quiz-opt');
    if (opt) {
      await clickAt(opt.x, opt.y);
      out.quizText = await ev("(document.querySelector('.au-quiz')||{}).textContent || ''");
      step('点小测选项', out.quizText.replace(/\s+/g, ' ').slice(0, 90));
    } else {
      step('小测', '没找到可点的选项（选择器要改）');
    }
    await ev("document.querySelector('.au-stage').scrollTop = 0; 'x'");
    await shot('_full_lesson_final.png');

    out.ok = {
      noUnknown: out.overview.unknown === 0,
      noErrors: out.overview.errors === 0,
      fourTabs: out.overview.tabs.length === 4,
      allTools: out.overview.geometry >= 1 && out.overview.datalab >= 1 &&
        out.overview.solver >= 1 && out.overview.calc >= 1,
      quizAndSteps: out.overview.quiz >= 1 && out.overview.steps >= 1,
      dragWorks: m0 !== m1,
      dataWorks: s0 !== s1,
      solveWorks: k0 !== k1,
      calcHasRows: Array.isArray(out.calcRows) && out.calcRows.length >= 3,
      quizGrades: /对|正确|✓|10/.test(out.quizText || ''),
    };
  } catch (err) { out.error = err.message; }
  console.log(JSON.stringify(out, null, 1));
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('PROBE FAILED: ' + e.message); process.exit(1); });
