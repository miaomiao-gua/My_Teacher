/* ============================================================================
 * 图形计算器测试：公式识别、渲染、以及「学生自己敲公式」这条路
 * ----------------------------------------------------------------------------
 * 为什么值得单测：
 *   1. 行的类型判断（显函数 / 隐式方程 / 不等式 / 点 / 文字 / 参数）错了，
 *      学生看到的是**空白画布且不报错** —— 最难查的那种失败；
 *   2. 「用户也能写函数」是这一版的核心需求，敲进去 → 回车 → 画出来这条路
 *      必须真的能跑通（而不是只有模型调用的那条路能跑）。
 * 跑法：node data/agent/_calc_tests.js
 * ========================================================================== */
'use strict';

const ctxCalls = {};
const ctxSegs = [];      // 记录 moveTo/lineTo 的坐标，用来验「曲线是连续的不是虚线」
let lastMove = null;
function makeCtx() {
  return new Proxy({}, {
    get(t, k) {
      if (k === 'canvas') return { width: 600, height: 400 };
      if (typeof k === 'symbol') return undefined;
      if (k === 'moveTo') return function (x, y) { lastMove = [x, y]; ctxCalls.moveTo = (ctxCalls.moveTo || 0) + 1; };
      if (k === 'lineTo') return function (x, y) {
        if (lastMove) ctxSegs.push([lastMove, [x, y]]);
        lastMove = [x, y];
        ctxCalls.lineTo = (ctxCalls.lineTo || 0) + 1;
      };
      return function () { ctxCalls[k] = (ctxCalls[k] || 0) + 1; };
    },
    set() { return true; },
  });
}
function ctxSince(fn) {
  Object.keys(ctxCalls).forEach(k => delete ctxCalls[k]);
  ctxSegs.length = 0;
  fn();
  return Object.assign({}, ctxCalls);
}
/** 把所有「短的」线段端点两两比对：正确的等值线里，每个交点都被相邻两格共用。
    表连错时曲线会断成一串点，这个共享比例会明显掉下来。 */
function endpointSharing() {
  const short = ctxSegs.filter(s => Math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) < 12);
  const pts = [];
  short.forEach(s => { pts.push(s[0], s[1]); });
  if (!pts.length) return { ratio: 0, n: 0, segs: short.length };
  let shared = 0;
  pts.forEach(p => {
    let n = 0;
    for (let i = 0; i < pts.length; i++) {
      if (Math.abs(pts[i][0] - p[0]) < 0.6 && Math.abs(pts[i][1] - p[1]) < 0.6) n++;
    }
    if (n >= 2) shared++;
  });
  return { ratio: shared / pts.length, n: pts.length, segs: short.length };
}

global.window = {
  devicePixelRatio: 1,
  location: { protocol: 'http:', origin: 'http://127.0.0.1:5001', port: '5001' },
  _listeners: {},
  addEventListener(type, fn) { (global.window._listeners[type] = global.window._listeners[type] || []).push(fn); },
  removeEventListener() {},
  innerHeight: 900,
  katex: { renderToString: (t) => '<span class="katex">' + t + '</span>' },
};

function fakeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    style: {}, dataset: {}, children: [], childNodes: [],
    className: '', textContent: '', _html: '', value: '', type: '',
    get innerHTML() { return node._html; },
    set innerHTML(v) { node._html = String(v); },
    clientWidth: 620, clientHeight: 420, offsetWidth: 620, offsetHeight: 420,
    appendChild(c) { node.children.push(c); node.childNodes.push(c); return c; },
    insertBefore(c) { node.children.unshift(c); node.childNodes.unshift(c); return c; },
    removeChild(c) { const i = node.children.indexOf(c); if (i >= 0) node.children.splice(i, 1); return c; },
    remove() {},
    setAttribute() {}, getAttribute() { return null; },
    _listeners: {},
    addEventListener(type, fn) { (node._listeners[type] = node._listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getContext() { return makeCtx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: node.clientWidth || 620, height: node.clientHeight || 420 }; },
    focus() { global.document.activeElement = node; },
    select() { node._selected = true; },
    click() { if (node.onclick) node.onclick({}); },
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
    preventDefault() {}, stopPropagation() {}, clientX: 100, clientY: 100,
    target: node, currentTarget: node,
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
function clickBtn(node) { if (node && node.onclick) node.onclick({ preventDefault() {} }); }

/* ==========================================================================
 * 一、公式识别：一行文本被认成什么
 * ======================================================================== */
console.log('--- 公式识别（classifyRow）---');
const calc = UI.renderNode({
  type: 'calculator',
  title: '测试计算器',
  expressions: ['x^2+y^2=16', 'y=x^2-2x-3', 'y<=x+1', 'x=3', '(2,3)', '"圆心 O"', 'a=2', 'y=a*x'],
  xMin: -6, xMax: 6, yMin: -5, yMax: 5,
});
const C = calc._classify;
const kindOf = (s) => C(s).kind;
const cases = [
  ['y=x^2-2x-3', 'explicit', '标准显函数'],
  ['x^2', 'explicit', '省略 y = 也认'],
  ['sin(x)+1', 'explicit', '三角函数'],
  ['2x+1', 'explicit', '隐式乘法 2x'],
  ['f(x)=x^2+1', 'explicit', 'f(x)= 写法'],
  ['x^2+y^2=16', 'implicit', '隐式方程（圆）'],
  ['x^2+y^2-16', 'implicit', '只写左边也当方程'],
  ['(x-1)^2+(y-2)^2=9', 'implicit', '带平移的圆'],
  ['x=3', 'vertical', '竖直线'],
  ['y<=x+1', 'ineqY', 'y 的不等式'],
  ['y>2x-1', 'ineqY', '严格不等式'],
  ['x^2+y^2<=4', 'ineqXY', '隐式不等式（圆盘）'],
  ['(2,3)', 'point', '点'],
  ['(-1.5, 2)', 'point', '负坐标点'],
  ['"圆心 O"', 'text', '引号文字'],
  ['("切点", (1,2))', 'text', 'Desmos 文字写法'],
  ['a=2', 'constant', '参数定义'],
  ['', 'empty', '空行'],
  ['import os', 'error', '非数学内容必须报错'],
  ['y=', 'error', '缺右边'],
  // —— 课本/聊天里的常见写法（2026-10-20 学生实测：x² 直接报「不认识的字符」）——
  ['y=x²', 'explicit', '上标 ² 自动转 ^2'],
  ['y=x³-3x', 'explicit', '上标 ³'],
  ['y=2x²+3x-1', 'explicit', '系数+上标混写'],
  ['y=sin²(x)', 'explicit', '函数带指数 sin²(x)'],
  ['y=sin(30°)', 'explicit', '度数 30° 自动转弧度'],
  ['y=sin(πx)', 'explicit', 'π 与隐式乘号'],
  ['y=√x', 'explicit', '根号 √x'],
  ['y=x×2', 'explicit', '乘号 ×'],
  ['y=（x+1）/2', 'explicit', '全角括号'],
  ['y=|x|', 'explicit', '绝对值'],
  ['y=sin x', 'error', '函数没带括号必须报错（不能静默当参数画错）'],
];
cases.forEach(function (c) {
  const got = kindOf(c[0]);
  check('`' + c[0] + '` → ' + c[1] + '（' + c[2] + '）', got === c[1], '实际 ' + got + (C(c[0]).err ? '：' + C(c[0]).err : ''));
});
check('参数定义行认出了名字（a=2 → name 是 a）', C('a=2').name === 'a', String(C('a=2').name));
check('y=a*x 里的 a 被认成参数', !!C('y=a*x').params && !!C('y=a*x').params.a);
check('x 和 y 不会被当成参数', !C('x^2+y^2=16').params.x && !C('x^2+y^2=16').params.y);
check('文字位置解析出来了', (() => {
  const r = C('("切点", (1, 2.5))');
  return r.at && Math.abs(r.at[0].eval({}) - 1) < 1e-9 && Math.abs(r.at[1].eval({}) - 2.5) < 1e-9;
})());

/* ==========================================================================
 * 二、渲染结构
 * ======================================================================== */
console.log('\n--- 渲染结构 ---');
check('计算器挂上了（au-calc）', calc.classList.contains('au-calc'));
check('左边公式面板 + 右边画布', calc.children.length === 2 &&
  calc.children[0].classList.contains('au-calc-panel') &&
  calc.children[1].classList.contains('au-calc-wrap'));
const listEl = findClass(calc, 'au-calc-list')[0];
const paramBox = findClass(calc, 'au-calc-params')[0];
const addBar = findClass(calc, 'au-calc-add')[0];
const overlay = findClass(calc, 'au-calc-overlay')[0];
check('公式列表存在', !!listEl);
check('自动补了一行空行（等学生输入）', calc._rows.length === 9 && calc._rows[8].raw === '',
  calc._rows.length + ' 行');
check('每行一个输入框', !!listEl && listEl.children.length === 9 && listEl.children[0].children[1].tagName === 'INPUT');
check('第一行输入框里就是原公式', listEl.children[0].children[1].value === 'x^2+y^2=16',
  listEl.children[0].children[1].value);
check('参数滑块出现了（a=2 / y=a*x → a）', !!paramBox && paramBox.children.length === 1,
  paramBox ? paramBox.children.length + ' 个滑块' : '没有参数区');
const rangeInput = paramBox && paramBox.children[0].children[1];
check('滑块初值是定义行给的值（a=2）', !!rangeInput && rangeInput.value === '2', rangeInput && rangeInput.value);
check('悬停读数的画布也在（overlay）', !!overlay);
check('工具栏与三个「＋」按钮都在', !!addBar && addBar.children.length === 3);
check('样式已定义', (UI._css || '').indexOf('.au-calc-row{') >= 0 && (UI._css || '').indexOf('.au-calc-overlay{') >= 0);

/* ==========================================================================
 * 三、真的画了吗（数绘图调用）
 * ======================================================================== */
console.log('\n--- 真的画了吗 ---');
function drawOnce(expressions, extra) {
  // 注意：构造时 renderNode 自己会画一次，所以只把 _refresh 那次算进来（两次画会把计数翻倍）
  const box = UI.renderNode(Object.assign(
    { type: 'calculator', expressions: expressions, xMin: -5, xMax: 5, yMin: -4, yMax: 4 }, extra || {}));
  return ctxSince(function () { box._refresh(); });
}
const cText = drawOnce(['"只有文字"']);
const cCurve = drawOnce(['y=x^2-2']);
const cCircle = drawOnce(['x^2+y^2=16']);
const cDisk = drawOnce(['x^2+y^2<=4']);
const cPoint = drawOnce(['(2,3)']);
check('显函数画出了折线（lineTo 明显多于纯文字）',
  (cCurve.lineTo || 0) > (cText.lineTo || 0) * 2, `${cCurve.lineTo || 0} vs ${cText.lineTo || 0}`);
check('隐式方程画出了等值线（圆）',
  (cCircle.lineTo || 0) > (cText.lineTo || 0) * 3, `${cCircle.lineTo || 0} vs ${cText.lineTo || 0}`);
check('不等式铺了区域（fillRect 明显多于纯文字）',
  (cDisk.fillRect || 0) > (cText.fillRect || 0) * 5, `${cDisk.fillRect || 0} vs ${cText.fillRect || 0}`);
check('点是用圆点画的（arc 被调用）', (cPoint.arc || 0) >= 1, String(cPoint.arc || 0));
check('坐标轴与刻度在画（moveTo/fillText）',
  (cText.moveTo || 0) > 0 && (cText.fillText || 0) > 0, `moveTo=${cText.moveTo} fillText=${cText.fillText}`);

// 等值线必须是**连续**的，不能是一串断点：marching squares 的 16 种情况里
// 只要连错两条边，圆就会画成虚线（实测踩过，而且不报任何错）。
console.log('\n--- 等值线连续性（防「圆画成虚线」）---');
const cConn = drawOnce(['x^2+y^2=16']);
const conn = endpointSharing();
check('等值线画出了足够多的短线段', conn.segs > 40, conn.segs + ' 段');
// 门槛定在 99%：正确的等值线是 100% 共用端点；把 16 种情况里的**任意一种**连错，
// 比例就会掉到九成出头（实测把 12 号连错时是 92.4%）。差 0.1% 都能看出来。
check('相邻线段的端点是共用的（曲线连续，不是虚线）', conn.ratio > 0.99,
  '共用端点比例 ' + (conn.ratio * 100).toFixed(1) + '%（' + conn.n + ' 个端点）');
const dConn = drawOnce(['x^2+y^2<=4']);
const conn2 = endpointSharing();
check('不等式区域的边界也连续', conn2.ratio > 0.99, (conn2.ratio * 100).toFixed(1) + '%');
check('网格默认开着（细网格 + 主网格两层）', (cText.stroke || 0) >= 3, 'stroke=' + (cText.stroke || 0));
const cNoGrid = drawOnce(['"x"'], { grid: false });
check('可以关掉网格', (cNoGrid.stroke || 0) < (cText.stroke || 0),
  `关网格 ${cNoGrid.stroke} < 开网格 ${cText.stroke}`);

/* ==========================================================================
 * 四、学生自己敲公式 —— 这一版的核心需求
 * ======================================================================== */
console.log('\n--- 学生自己敲公式 ---');
const box = UI.renderNode({ type: 'calculator', expressions: ['y=x^2'], xMin: -6, xMax: 6 });
const rows = box._rows;
check('初始 1 条公式 + 1 行空行', rows.length === 2, rows.length + ' 行');

// 1) 敲进最后那行 + 回车 → 变成一条公式，并自动再补一行空行
const last = rows[rows.length - 1];
last.dom.input.value = 'x^2+y^2=25';
fireEl(last.dom.input, 'input', {});
fireEl(last.dom.input, 'keydown', { key: 'Enter' });
check('回车后多了一行', box._rows.length === 3, box._rows.length + ' 行');
check('新公式被认成隐式方程（圆）', box._rows[1].kind === 'implicit', box._rows[1].kind);
check('末尾自动补了空行等着继续输入', box._rows[2].raw === '' && box._rows[2].kind === 'empty');
check('输入框重建过（DOM 与数据一致）', box._rows[1].dom.input.value === 'x^2+y^2=25',
  box._rows[1].dom.input.value);

// 2) 改一行 → 类型跟着变
const first = box._rows[0];
first.dom.input.value = 'y<=sin(x)';
fireEl(first.dom.input, 'input', {});
box._refresh();
check('改完立刻按新公式重判类型', first.kind === 'ineqY' && first.op === '<=', first.kind + ' ' + first.op);

// 3) 空行上按退格 = 删掉这行（最后那行是常驻输入行，不清）
const box3b = UI.renderNode({ type: 'calculator', expressions: ['y=x', 'y=2x', 'y=3x'] });
check('三条公式 + 一行常驻输入行', box3b._rows.length === 4, box3b._rows.length + ' 行');
box3b._rows[0].dom.input.value = '';
fireEl(box3b._rows[0].dom.input, 'input', {});
fireEl(box3b._rows[0].dom.input, 'keydown', { key: 'Backspace' });
check('中间的空行按退格会删掉它', box3b._rows.length === 3, '4 → ' + box3b._rows.length);
const before = box._rows.length;
const emptyRow = box._rows[box._rows.length - 1];
fireEl(emptyRow.dom.input, 'keydown', { key: 'Backspace' });
check('最后那行是常驻输入行，不会被删掉', box._rows.length === before, before + ' → ' + box._rows.length);

// 4) 「＋ 文字」/「＋ 点」
const bar = findClass(box, 'au-calc-add')[0];
clickBtn(bar.children[1]);
const textRow = box._rows.filter(r => r.kind === 'text').pop();
check('「＋ 文字」加了一行文字', !!textRow, textRow ? textRow.raw : '没加上');
clickBtn(bar.children[2]);
const pointRow = box._rows.filter(r => r.kind === 'point').pop();
check('「＋ 点」加了一行点', !!pointRow, pointRow ? pointRow.raw : '没加上');

// 5) 写错的公式要当场指出，而不是默默画不出来
const bad = box._rows[0];
bad.dom.input.value = 'x^2+';
fireEl(bad.dom.input, 'input', {});
box._refresh();
check('写错的行被标红并给出原因', bad.kind === 'error' && bad.dom.wrap.classList.contains('au-bad') &&
  bad.dom.err.textContent.length > 0, bad.err || '(没提示)');
check('一行错了不影响别的行继续画', box._rows[1].kind === 'implicit', box._rows[1].kind);

// 6) 拖滑块 → 参数变了，并且写回 `a=2` 那一行
const box2 = UI.renderNode({ type: 'calculator', expressions: ['a=2', 'y=a*x'], xMin: -5, xMax: 5 });
const pbox = findClass(box2, 'au-calc-params')[0];
const slider = pbox.children[0].children[1];
slider.value = '3.5';
fireEl(slider, 'input', {});
const constRow = box2._rows.filter(r => r.kind === 'constant' && r.name === 'a')[0];
check('拖滑块真的改了参数值', constRow && constRow.raw === 'a = 3.5', constRow ? constRow.raw : '没找到参数行');
check('参数定义行的输入框也同步了', constRow && constRow.dom.input.value === 'a = 3.5',
  constRow && constRow.dom.input.value);

// 7) 隐藏 / 删除
const box3 = UI.renderNode({ type: 'calculator', expressions: ['y=x'] });
const row3 = box3._rows[0];
clickBtn(row3.dom.eye);
check('眼睛按钮能隐藏这一行', row3.hidden === true);

/* ==========================================================================
 * 五、面板高度（工作台拉动条要用）
 * ======================================================================== */
console.log('\n--- 高度可调 ---');
const box4 = UI.renderNode({ type: 'calculator', expressions: ['y=x^2'], height: 420 });
check('初始高度记在 canvas 上', box4.children[1].children[0].dataset.height === '420',
  box4.children[1].children[0].dataset.height);
box4._setHeight(700);
check('_setHeight 改高度（给工作台拉动条用）', box4.children[1].children[0].dataset.height === '640',
  box4.children[1].children[0].dataset.height);
box4._setHeight(0);
check('_setHeight(0) 回到默认高度', box4.children[1].children[0].dataset.height === '420',
  box4.children[1].children[0].dataset.height);

/* ==========================================================================
 * 六、精确度（小数位）与画质（采样密度）
 * ======================================================================== */
console.log('\n--- 精确度 ---');
const FT = UI._fmtTick;
check('自动：默认显示到 6 位有效小数', FT(1 / 3) === '0.333333', String(FT(1 / 3)));
check('2 位小数：1/3 → 0.33', FT(1 / 3, 2) === '0.33', String(FT(1 / 3, 2)));
check('4 位小数：1/3 → 0.3333', FT(1 / 3, 4) === '0.3333', String(FT(1 / 3, 4)));
check('末尾的 0 要去掉（1.50 → 1.5，2.00 → 2，100.00 → 100）',
  FT(1.5, 2) === '1.5' && FT(2, 2) === '2' && FT(100, 2) === '100',
  FT(1.5, 2) + ' / ' + FT(2, 2) + ' / ' + FT(100, 2));
check('整数模式：2.7 → 3', FT(2.7, 0) === '3', String(FT(2.7, 0)));
check('-0.0001 取 1 位不会显示成 -0', FT(-0.0001, 1) === '0', String(FT(-0.0001, 1)));
check('整数不会被去零规则吃掉（100 不能变成 1）', FT(100, 1) === '100', String(FT(100, 1)));

// 界面上真的能调：选「2 位小数」之后，视野读数的位数跟着变
function findClass(root, cls, out) {
  out = out || [];
  (root.children || []).forEach(c => {
    if (c.classList && c.classList.contains(cls)) out.push(c);
    findClass(c, cls, out);
  });
  return out;
}
const box5 = UI.renderNode({ type: 'calculator', expressions: ['y=x^2'], xMin: -6.6666666, xMax: 6.6666666 });
const precSel = findClass(box5, 'au-calc-prec-sel')[0];
check('工具栏上有「精确度」选择器', !!precSel);
const viewInfo5 = findClass(box5, 'au-view-info')[0];
check('视野读数存在', !!viewInfo5, viewInfo5 ? String(viewInfo5.textContent).slice(0, 46) : '没有');
check('自动模式下视野读数带很多位小数',
  /\d\.\d{4,}/.test(String(viewInfo5.textContent)), String(viewInfo5.textContent));
if (precSel) {
  precSel.value = '2';
  precSel.onchange();
  const txt = String(viewInfo5.textContent);
  check('选「2 位小数」之后读数最多 2 位',
    !/\.\d{3,}/.test(txt) && /-38\.26/.test(txt), txt);
}
// spec 里直接指定精确度（模型可以这么发）
const box6 = UI.renderNode({ type: 'calculator', expressions: ['y=x^2'], xMin: -6.6666666, xMax: 6.6666666, decimals: 3 });
const vi6 = findClass(box6, 'au-view-info')[0];
check('spec 里的 decimals 生效（3 位）', /-38\.255/.test(String(vi6.textContent)), String(vi6.textContent));

console.log('\n--- 画质（采样密度） ---');
function contourSegs(quality) {
  const b = UI.renderNode({ type: 'calculator', expressions: ['x^2+y^2=16'], xMin: -6, xMax: 6, yMin: -5, yMax: 5, quality: quality });
  ctxSince(function () { b._refresh(); });
  return ctxSegs.filter(s => Math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) < 12).length;
}
const segStd = contourSegs('standard');
const segUltra = contourSegs('ultra');
check('超清画质采样更密（等值线段数明显更多）', segUltra > segStd * 1.5,
  `标准 ${segStd} 段 → 超清 ${segUltra} 段`);
const toolsEl = findClass(box5, 'au-plot-tools')[0];
const qBtn = toolsEl && Array.from(toolsEl.children).find(b => String(b.textContent).indexOf('画质') === 0);
check('工具栏上有「画质」按钮', !!qBtn, qBtn ? String(qBtn.textContent) : '没有');
if (qBtn) {
  const before = String(qBtn.textContent);
  qBtn.onclick();
  check('点一下切到下一档画质', String(qBtn.textContent) !== before,
    before + ' → ' + String(qBtn.textContent));
}

console.log('\n' + (failures === 0 ? '全部通过 ✓' : failures + ' 项失败 ✗'));
process.exit(failures === 0 ? 0 : 1);
