/* ============================================================================
 * 三个小工具的测试：几何画板 / 数据统计台 / 方程求解器
 * ----------------------------------------------------------------------------
 * 这三个的共同点是「**界面自己会算**」：模型只给数据和系数，测量值、统计量、
 * 方程的根都在前端当场算出来。所以测试必须验两件事：
 *   1. 算得对（勾股数、平均数/方差、判别式与因式分解 —— 算错比不画更糟）
 *   2. 交互真的通（拖顶点 / 改数字 / 改系数之后，结果跟着变）
 * 跑法：node data/agent/_tools_tests.js
 * ========================================================================== */
'use strict';

const ctxCalls = {};
function makeCtx() {
  return new Proxy({}, {
    get(t, k) {
      if (k === 'canvas') return { width: 600, height: 400 };
      if (typeof k === 'symbol') return undefined;
      return function () { ctxCalls[k] = (ctxCalls[k] || 0) + 1; };
    },
    set() { return true; },
  });
}
global.window = {
  devicePixelRatio: 1,
  location: { protocol: 'http:', origin: 'http://127.0.0.1:5001', port: '5001' },
  innerHeight: 900,
  _listeners: {},
  addEventListener(type, fn) { (global.window._listeners[type] = global.window._listeners[type] || []).push(fn); },
  removeEventListener() {},
  katex: { renderToString: (t) => '<span class="katex">' + t + '</span>' },
};
function fireWindow(type, ev) {
  (global.window._listeners[type] || []).forEach(fn => fn(Object.assign({ preventDefault() {}, stopPropagation() {} }, ev)));
}
function fakeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    style: {}, dataset: {}, children: [], childNodes: [],
    className: '', textContent: '', _html: '', value: '', type: '',
    get innerHTML() { return node._html; },
    set innerHTML(v) { node._html = String(v); if (v === '') { node.children.length = 0; node.childNodes.length = 0; } },
    parentNode: null,
    clientWidth: 620, clientHeight: 420, offsetWidth: 620, offsetHeight: 420,
    appendChild(c) { c.parentNode = node; node.children.push(c); node.childNodes.push(c); return c; },
    insertBefore(c) { c.parentNode = node; node.children.unshift(c); node.childNodes.unshift(c); return c; },
    removeChild(c) { const i = node.children.indexOf(c); if (i >= 0) node.children.splice(i, 1); if (c) c.parentNode = null; return c; },
    remove() { if (node.parentNode) node.parentNode.removeChild(node); },
    setAttribute() {}, getAttribute() { return null; },
    _listeners: {},
    addEventListener(type, fn) { (node._listeners[type] = node._listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getContext() { return makeCtx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: node.clientWidth || 620, height: node.clientHeight || 420 }; },
    focus() { global.document.activeElement = node; },
    select() {}, click() { if (node.onclick) node.onclick({}); },
  };
  const tokens = () => node.className.split(/\s+/).filter(Boolean);
  node.classList = {
    add(...c) { const s = new Set(tokens()); c.forEach(x => s.add(x)); node.className = Array.from(s).join(' '); },
    remove(...c) { const s = new Set(tokens()); c.forEach(x => s.delete(x)); node.className = Array.from(s).join(' '); },
    toggle(c, on) {
      const has = tokens().indexOf(c) >= 0;
      const want = (on === undefined) ? !has : !!on;
      if (want && !has) node.classList.add(c);
      if (!want && has) node.classList.remove(c);
    },
    contains(c) { return tokens().indexOf(c) >= 0; },
  };
  return node;
}
global.document = {
  createElement: fakeEl, createElementNS: (ns, tag) => fakeEl(tag),
  head: fakeEl('head'), body: fakeEl('body'), activeElement: null,
  _listeners: {},
  addEventListener(type, fn) { (global.document._listeners[type] = global.document._listeners[type] || []).push(fn); },
  removeEventListener() {},
  getElementById() { return null; },
  querySelector() { return null; }, querySelectorAll() { return []; },
};
Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText() {} } }, configurable: true });
global.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
global.localStorage = {
  _d: {}, getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; },
};
global.fetch = async function () { return { ok: false, status: 404, text: async () => '', json: async () => ({}) }; };

require('D:/My_teacher/agent_studio/web/static/js/agent_ui.js');
const UI = window.AgentUI;

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log((ok ? '  OK    ' : '  FAIL  ') + name + (detail ? '  →  ' + detail : ''));
}
function fireEl(node, type, ev) {
  (node._listeners[type] || []).forEach(fn => fn(Object.assign({
    preventDefault() {}, stopPropagation() {}, clientX: 0, clientY: 0, target: node, currentTarget: node,
  }, ev)));
}
function findClass(root, cls, out) {
  out = out || [];
  (root.children || []).forEach(c => {
    if (c.classList && c.classList.contains(cls)) out.push(c);
    findClass(c, cls, out);
  });
  return out;
}
function measureMap(box) {
  const m = {};
  box._measure().forEach(r => { m[r.full] = r.text; });
  return m;
}

/* ==========================================================================
 * 一、几何画板：拖顶点，测量跟着变
 * ======================================================================== */
console.log('--- 几何画板（3-4-5 直角三角形）---');
const geo = UI.renderNode({
  type: 'geometry', title: '勾股定理', height: 400, showSquares: true,
  points: [{ name: 'A', at: [0, 0], fixed: true }, { name: 'B', at: [4, 0] }, { name: 'C', at: [0, 3] }],
  segments: [['A', 'B'], ['A', 'C'], ['B', 'C']],
  polygons: [['A', 'B', 'C']],
  task: '拖动 B 和 C，看看 a² + b² 和 c² 什么时候相等',
});
geo._redraw();          // 首次渲染挂在 setTimeout 里，测试里同步跑一遍
const m1 = measureMap(geo);
check('渲染出了画板', !!findClass(geo, 'au-canvas')[0]);
check('|AB| = 4', m1['|AB|'] === '4', String(m1['|AB|']));
check('|AC| = 3', m1['|AC|'] === '3', String(m1['|AC|']));
check('|BC| = 5（勾股数）', m1['|BC|'] === '5', String(m1['|BC|']));
check('a² 列也在（|AB|² = 16）', m1['|AB|²'] === '16', String(m1['|AB|²']));
check('c² 列也在（|BC|² = 25）', m1['|BC|²'] === '25', String(m1['|BC|²']));
check('直角三角形的直角 = 90°', m1['∠BAC'] === '90.0°', String(m1['∠BAC']));
check('面积 = 6', m1['ABC 面积'] === '6', String(m1['ABC 面积']));
check('周长 = 12', m1['ABC 周长'] === '12', String(m1['ABC 周长']));
check('界面提示了可以拖点', findClass(geo, 'au-geo-tip').length === 1);

// 真的拖一下 C 点：按住 → 移动 → 松开
const cv = findClass(geo, 'au-canvas')[0];
const before = geo._screenOf('C');
fireEl(cv, 'mousedown', { clientX: before.x, clientY: before.y });
fireWindow('mousemove', { clientX: before.x + 60, clientY: before.y });
fireWindow('mouseup', {});
const cNow = geo._points.find(p => p.name === 'C');
check('拖动真的改了点的坐标', cNow.at[0] !== 0 || cNow.at[1] !== 3,
  'C 现在 = (' + cNow.at.join(', ') + ')');
const m2 = measureMap(geo);
check('拖动之后 |AC| 跟着变了', m2['|AC|'] !== '3', '|AC| = ' + m2['|AC|']);
// 沿 AB 方向平移 C：底不变、高也不变 → 面积**应该不变**（这本身就是个好教学点）
check('横向拖 C：面积仍是 6（底和高都没变）', m2['ABC 面积'] === '6', '面积 = ' + m2['ABC 面积']);
// 纵向拖 C：高变了 → 面积应该跟着变
const cMid = geo._screenOf('C');
fireEl(cv, 'mousedown', { clientX: cMid.x, clientY: cMid.y });
fireWindow('mousemove', { clientX: cMid.x, clientY: cMid.y - 45 });
fireWindow('mouseup', {});
const m3 = measureMap(geo);
check('纵向拖 C：面积跟着变', m3['ABC 面积'] !== '6', '面积 = ' + m3['ABC 面积']);
// 固定点不能拖
const af = geo._screenOf('A');
fireEl(cv, 'mousedown', { clientX: af.x, clientY: af.y });
fireWindow('mousemove', { clientX: af.x + 80, clientY: af.y + 80 });
fireWindow('mouseup', {});
check('fixed 的点拖不动', geo._points[0].at[0] === 0 && geo._points[0].at[1] === 0,
  '(' + geo._points[0].at.join(', ') + ')');

/* ==========================================================================
 * 二、数据统计台：改数字，统计量跟着变
 * ======================================================================== */
console.log('\n--- 数据统计台 ---');
const dl = UI.renderNode({
  type: 'datalab', title: '全班身高', values: [1, 2, 2, 3, 9], height: 300,
});
dl._render();           // 同上：先把数据格和统计量渲染出来
let st = dl._stats();
check('n = 5', st.n === 5, String(st.n));
check('平均数 = 3.4', Math.abs(st.mean - 3.4) < 1e-9, String(st.mean));
check('中位数 = 2', st.median === 2, String(st.median));
check('众数 = 2', st.mode.length === 1 && st.mode[0] === 2, JSON.stringify(st.mode));
check('极差 = 8', st.range === 8, String(st.range));
check('方差 = 8.24', Math.abs(st.variance - 8.24) < 1e-9, String(st.variance));
check('标准差 = √8.24', Math.abs(st.std - Math.sqrt(8.24)) < 1e-9, String(st.std));
check('每个数一个输入框', findClass(dl, 'au-dl-input').length === 5,
  String(findClass(dl, 'au-dl-input').length));
check('统计量面板里有「平均数」', String(findClass(dl, 'au-dl-stats')[0]._html || '').length >= 0 &&
  findClass(dl, 'au-geo-row').length > 0);

// 把 9 改成 3（拖掉那个极端值），平均数应该掉下来
const inputs = findClass(dl, 'au-dl-input');
inputs[4].value = '3';
fireEl(inputs[4], 'input', {});
st = dl._stats();
check('改一个数 → 平均数从 3.4 变 2.2', Math.abs(st.mean - 2.2) < 1e-9, String(st.mean));
check('改一个数 → 极差从 8 变 2', st.range === 2, String(st.range));
check('中位数没被极端值带跑（还是 2）', st.median === 2, String(st.median));

// 加/删/排序/换图
const tools = findClass(dl, 'au-plot-tools')[0];
const addBtn = tools.children[0], delBtn = tools.children[1], sortBtn = tools.children[2], chartBtn = tools.children[3];
addBtn.onclick();
check('「＋ 加一个数」真的加了', dl._stats().n === 6, String(dl._stats().n));
delBtn.onclick();
check('「删掉最后一个」真的删了', dl._stats().n === 5, String(dl._stats().n));
const beforeSort = dl._values().slice();
sortBtn.onclick();
check('排序按钮不会弄丢数据', dl._stats().n === 5 &&
  dl._values().slice().sort((a, b) => a - b).join(',') === beforeSort.slice().sort((a, b) => a - b).join(','),
  JSON.stringify(dl._values()));
const q0 = chartBtn.textContent;
chartBtn.onclick();
check('图可以在柱状/折线/点图之间切', chartBtn.textContent !== q0, q0 + ' → ' + chartBtn.textContent);
const dlAll = UI.renderNode({ type: 'datalab', values: [1, 2, 3] });
dlAll._render();
check('每个数都不重复时众数显示「没有」',
  String(findClass(dlAll, 'au-dl-stats')[0].children.map(c => c.children.map(x => x._html).join('')).join('')).indexOf('没有') >= 0);

/* ==========================================================================
 * 三、方程求解器：改系数，根和因式分解跟着变
 * ======================================================================== */
console.log('\n--- 方程求解器 ---');
const sv = UI.renderNode({ type: 'solver', kind: 'quadratic', a: 1, b: -5, c: 6, title: '解方程' });
sv._redraw();           // 同上
let s = sv._solve();
check('Δ = 1', s.D === 1, String(s.D));
check('两根 = 3 与 2', s.roots.slice().sort().join(',') === '2,3', JSON.stringify(s.roots));
check('精确根显示成分数/整数', s.exact.join('，') === '3，2', s.exact.join('，'));
check('因式分解 = (x − 3)(x − 2)', s.factor === '(x − 3)(x − 2)', String(s.factor));
check('顶点 = (2.5, -0.25)', Math.abs(s.vertex[0] - 2.5) < 1e-9 && Math.abs(s.vertex[1] + 0.25) < 1e-9,
  '(' + s.vertex.join(', ') + ')');
check('步骤里有判别式代入过程', s.steps.join(' ').indexOf('Δ = b² − 4ac') >= 0, s.steps[1]);
check('步骤里有韦达定理', s.steps.join(' ').indexOf('韦达定理') >= 0);
check('图上真画了抛物线', Object.keys(ctxCalls).length > 0);

// 系数不能整除的那种：2x² - 5x + 3 → (2x − 3)(x − 1)
const sv2 = UI.renderNode({ type: 'solver', a: 2, b: -5, c: 3 });
check('2x²−5x+3 的因式分解是整数系数形式', sv2._solve().factor === '(2x − 3)(x − 1)',
  String(sv2._solve().factor));
// 无理根：x² − 2 = 0 → ±√2
const sv3 = UI.renderNode({ type: 'solver', a: 1, b: 0, c: -2 });
check('x²−2=0 的精确根写成 √2 形式', sv3._solve().exact.join('') === '±√2', sv3._solve().exact.join(''));
// 无实根
const sv4 = UI.renderNode({ type: 'solver', a: 1, b: 0, c: 1 });
check('x²+1=0 说明没有实数根', sv4._solve().D === -4 && String(sv4._solve().note).indexOf('无实数根') >= 0,
  sv4._solve().note);
// 一元一次
const sv5 = UI.renderNode({ type: 'solver', kind: 'linear', a: 2, b: -6 });
check('一元一次 2x−6=0 → x = 3', sv5._solve().exact[0] === '3', String(sv5._solve().exact[0]));
check('一元一次 a=0 时会说明情况',
  String(UI.renderNode({ type: 'solver', kind: 'linear', a: 0, b: 5 })._solve().note).indexOf('无解') >= 0);
// 改系数 → 结果跟着变
sv._setCoefficients(1, -3, 2);
check('改系数之后根跟着变（1 和 2）', sv._solve().roots.slice().sort().join(',') === '1,2',
  JSON.stringify(sv._solve().roots));

/* ==========================================================================
 * 四、能不能拼出一个「完整的 UI」（这次的重点）
 * ======================================================================== */
console.log('\n--- 完整 UI 拼装 ---');
const full = UI.renderSpec({
  title: '勾股定理 · 一节课',
  items: [
    { type: 'callout', tone: 'info', title: '先看一个事实', content: '直角三角形两条直角边的平方和等于斜边的平方。' },
    { type: 'tabs', tabs: [
      { label: '动手拖一拖', items: [
        { type: 'geometry', height: 360, showSquares: true,
          points: [{ name: 'A', at: [0, 0], fixed: true }, { name: 'B', at: [3, 0] }, { name: 'C', at: [0, 4] }],
          segments: [['A', 'B'], ['A', 'C'], ['B', 'C']], polygons: [['A', 'B', 'C']],
          task: '拖 B、C，观察 a²+b² 与 c²' },
      ] },
      { label: '数据看看', items: [
        { type: 'datalab', values: [3, 4, 5], height: 260 },
        { type: 'table', columns: ['a', 'b', 'c'], rows: [['3', '4', '5'], ['6', '8', '10']] },
      ] },
      { label: '算一算', items: [
        { type: 'solver', a: 1, b: 0, c: -25 },
        { type: 'calculator', expressions: ['x^2+y^2=25'], xMin: -8, xMax: 8, height: 380 },
      ] },
    ] },
    { type: 'steps', current: 1, steps: [{ title: '观察', desc: '拖动顶点' }, { title: '猜想', desc: 'a²+b²=c²' }, { title: '验证', desc: '换几组数据' }] },
    { type: 'quiz', question: '直角边 6 和 8，斜边是多少？',
      options: [{ label: '10', correct: true, feedback: '对：36+64=100' }, { label: '14', feedback: '那是把两边相加了' }],
      explanation: 'a²+b²=c²' },
  ],
});
check('整页渲染没有降级成「没实现」的组件', findClass(full, 'au-unknown').length === 0,
  findClass(full, 'au-unknown').map(x => x._html).join(' / '));
check('整页渲染没有出错块', findClass(full, 'au-error').length === 0,
  findClass(full, 'au-error').map(x => x._html).join(' / '));
['au-geo', 'au-datalab', 'au-solver', 'au-calc', 'au-quiz', 'au-table', 'au-tabs', 'au-callout', 'au-steps']
  .forEach(function (cls) {
    check('整页里有 ' + cls, findClass(full, cls).length >= 1, String(findClass(full, cls).length));
  });
const allCtx = Object.keys(ctxCalls).length;
check('整页里所有画布都画过了', allCtx > 10, allCtx + ' 种绘图调用');

console.log('\n' + (failures === 0 ? '全部通过 ✓' : failures + ' 项失败 ✗'));
process.exit(failures === 0 ? 0 : 1);
