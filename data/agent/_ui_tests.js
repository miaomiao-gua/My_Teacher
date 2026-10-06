/* ============================================================================
 * 渲染器回归测试（脱离浏览器，用最小 DOM 桩）
 * ----------------------------------------------------------------------------
 * 为什么需要它：前端这几件事都是「不看代码看不出来」的 ——
 *   · 数学公式的四种定界符是否都能渲染（用户实测 \[...\] 不渲染）
 *   · 坐标是否等比（两轴独立拉伸 → 圆变成椭圆，用户实测撞上过）
 *   · 每个组件是否都能渲染而不抛异常（抛异常 → 界面挂载中断 →
 *     现象是「AI 说画好了但学生什么都没看到」，且没有任何报错）
 * 跑法：node data/agent/_ui_tests.js
 * ========================================================================== */
'use strict';

const RENDERER = 'D:/My_teacher/agent_studio/web/static/js/agent_ui.js';

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log((ok ? '  OK    ' : '  FAIL  ') + name + (detail ? '  →  ' + detail : ''));
}

/* ---------------- 最小 DOM 桩 ---------------- */
global.window = {
  devicePixelRatio: 1,
  location: { protocol: 'http:', origin: 'http://127.0.0.1:5001' },
  addEventListener() {}, removeEventListener() {},
};
global.window.katex = {
  renderToString: (tex, opts) => '<span class="katex" data-display="' + !!(opts && opts.displayMode) + '">' + tex + '</span>',
};
function fakeCtx() {
  return new Proxy({}, { get: () => () => {}, set: () => true });
}
function fakeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    style: {}, dataset: { height: '380' }, children: [],
    className: '', textContent: '', _html: '',
    // innerHTML 必须是真的读写器：el(tag, cls, html) 走的就是它，
    // 如果只是个普通属性，测试里想按 HTML 找内容就永远找不到（踩过）。
    get innerHTML() { return node._html; },
    set innerHTML(v) { node._html = String(v); },
    clientWidth: 640, clientHeight: 380, offsetWidth: 640, offsetHeight: 380,
    appendChild(c) { node.children.push(c); return c; },
    insertBefore(c) { node.children.unshift(c); return c; },
    removeChild(c) { const i = node.children.indexOf(c); if (i >= 0) node.children.splice(i, 1); return c; },
    remove() {},
    setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return fakeEl('div'); }, querySelectorAll() { return []; },
    getContext() { return fakeCtx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 640, height: 380 }; },
    focus() {}, click() {},
  };
  // classList 必须真的读写 className：桩里写死 contains()=false 的话，
  // 「某个元素在不在」这类断言就永远为假，等于没测（而且会掩盖真 bug）。
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
  Object.defineProperty(node, 'firstChild', { get() { return node.children[0] || null; } });
  return node;
}
global.document = {
  createElement: fakeEl, createElementNS: (ns, tag) => fakeEl(tag),
  head: fakeEl('head'), body: fakeEl('body'),
  getElementById() { return null; }, querySelector() { return fakeEl('div'); }, querySelectorAll() { return []; },
};
Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText() {} } }, configurable: true });
global.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };

require(RENDERER);
const UI = window.AgentUI;

/* ---------------- 一、公式定界符 ---------------- */
console.log('\n[1] 公式定界符（四种写法 + 裸环境）');
const fmt = UI._fmt;
[
  ['反斜杠方括号 display', String.raw`\[ \text{HCl} + \text{NaOH} \rightarrow \text{NaCl} \]`],
  ['美元双符 display', '$$E = mc^2$$'],
  ['反斜杠圆括号 inline', String.raw`当 \(x = 3\) 时`],
  ['美元单符 inline', '所以 $x^2 + 1 > 0$ 恒成立'],
  ['裸环境无定界符', String.raw`\begin{aligned} a &= 3 \\ b &= 4 \end{aligned}`],
].forEach(function (pair) {
  const out = fmt(pair[1]);
  const rendered = out.indexOf('katex') >= 0;
  const leftover = /\\\[|\\\]|\\\(|\\\)|\$\$/.test(out);
  check(pair[0], rendered && !leftover, rendered ? (leftover ? '仍有定界符残留' : '') : '没被识别为公式');
});

/* ---------------- 二、坐标等比（圆是圆） ---------------- */
console.log('\n[2] 坐标等比映射（两轴独立拉伸会让圆变成椭圆）');
const makeMapper = UI._makeMapper;
[
  ['正方形画布 · 宽范围', 720, 400, [-5, 5], [-3, 3]],
  ['宽扁画布 · 窄范围', 900, 260, [-2, 2], [-2, 2]],
  ['竖高画布', 420, 520, [-10, 10], [-1, 1]],
  ['非零中心', 640, 480, [3, 9], [-2, 6]],
].forEach(function (c) {
  const m = makeMapper(c[1], c[2], c[3], c[4], { l: 44, r: 16, t: 16, b: 28 }, true);
  const xUnit = Math.abs(m.x2p(1) - m.x2p(0));
  const yUnit = Math.abs(m.y2p(1) - m.y2p(0));
  const round = Math.abs(xUnit - yUnit) < 1e-6 && xUnit > 0;
  const covers = m.xr[0] <= c[3][0] + 1e-6 && m.xr[1] >= c[3][1] - 1e-6 &&
                 m.yr[0] <= c[4][0] + 1e-6 && m.yr[1] >= c[4][1] - 1e-6;
  check(c[0], round && covers,
    'x单位=' + xUnit.toFixed(2) + ' y单位=' + yUnit.toFixed(2) + (covers ? '' : ' 可视范围没覆盖请求范围'));
});

/* ---------------- 三、组件渲染不抛异常 ---------------- */
console.log('\n[3] 组件渲染（抛异常会导致「说了画了但看不到」）');
const specs = {
  'plot 二次函数（含参数）': {
    type: 'plot', title: '二次函数', height: 380, xMin: -8, xMax: 8,
    series: [{ expr: 'a*x^2+b*x+c', label: '抛物线', params: [{ name: 'a', value: 1, min: -3, max: 3 }, { name: 'c', value: 0, min: -5, max: 5 }] }],
  },
  'plot 两条三角函数': {
    type: 'plot', height: 300, xMin: -6.28, xMax: 6.28,
    series: [{ expr: 'sin(x)', label: 'sin' }, { expr: '2*sin(x)', label: '2sin' }],
  },
  'plot 有渐近线（1/x）': { type: 'plot', height: 300, xMin: -5, xMax: 5, series: [{ expr: '1/x' }] },
  'plot 非法表达式': { type: 'plot', series: [{ expr: 'import os' }] },
  'board 圆与内接正方形（分步）': {
    type: 'board', title: '圆与内接正方形', height: 400, xRange: [-5, 5], yRange: [-3, 3],
    steps: [
      { caption: '坐标系', shapes: [{ kind: 'axes' }] },
      { caption: '圆', shapes: [{ kind: 'circle', center: [0, 0], r: 2, label: '⊙O' }] },
      { caption: '内接正方形', shapes: [{ kind: 'polygon', points: [[-1.414, -1.414], [1.414, -1.414], [1.414, 1.414], [-1.414, 1.414]], label: 'ABCD' }] },
      { caption: '半径与直角标记', shapes: [{ kind: 'line', from: [0, 0], to: [1.414, 1.414], label: 'r' }, { kind: 'angle', at: [1.414, -1.414], from: [0, -1.414], to: [1.414, 0] }] },
      { caption: '曲线', shapes: [{ kind: 'curve', expr: 'x^2-2' }] },
      { caption: '标注', shapes: [{ kind: 'measure', from: [-1.414, -1.414], to: [1.414, -1.414], label: '2√2' }, { kind: 'text', at: [0, 2.6], text: '外接圆' }] },
    ],
  },
  'quiz 答题卡': { type: 'quiz', question: '酚酞在酸性溶液中显什么色？', options: [{ label: '无色', correct: true, feedback: '对' }, { label: '红色', feedback: '那是碱性' }], explanation: '酚酞 pH<8.2 无色' },
  'table 数据表': { type: 'table', columns: ['加入量', 'pH'], rows: [['0 mL', '7.0'], ['1 mL', '11.3']], types: ['text', 'num'] },
  'chart 柱状/折线/环形': { type: 'card', items: [
    { type: 'chart', kind: 'bars', data: [{ label: 'A', value: 3 }, { label: 'B', value: 5 }] },
    { type: 'chart', kind: 'line', series: [{ label: 's', data: [1, 3, 2, 5] }], data: [{ label: '一' }, { label: '二' }, { label: '三' }, { label: '四' }] },
    { type: 'chart', kind: 'donut', data: [{ label: '酸', value: 3 }, { label: '碱', value: 7 }] },
  ] },
  'steps/timeline/callout/keyvalue': { type: 'card', items: [
    { type: 'steps', current: 1, steps: [{ title: '第一步' }, { title: '第二步' }] },
    { type: 'timeline', items: [{ title: '起点', time: '0s' }] },
    { type: 'callout', tone: 'info', title: '注意', content: '带 **加粗** 和 $x^2$' },
    { type: 'keyvalue', pairs: [{ key: 'a', value: '1' }] },
  ] },
  'code/json/list/badge': { type: 'card', items: [
    { type: 'code', lang: 'python', code: 'print(1)' },
    { type: 'json', value: { a: 1 } },
    { type: 'list', items: ['一', { title: '二', desc: '说明' }] },
    { type: 'badge', label: '标签' },
  ] },
  'frame 实验台': { type: 'frame', src: '/agent/plugins/acid_base_lab/ui/index.html', height: 720, title: '虚拟实验室' },
  '未知组件（应优雅降级）': { type: '不存在的组件', title: 'x' },
};
Object.keys(specs).forEach(function (name) {
  try {
    const node = UI.renderNode(specs[name]);
    UI.renderSpec({ title: '整页', items: [specs[name]] });
    check(name, true);
    // 每个「可调高度」的组件都要真的调用一次 _setHeight：
    // 这类钩子写错作用域（变量不存在）只有调用时才炸，光渲染是发现不了的（我刚踩过）。
    let box = node;
    if (box && typeof box._setHeight !== 'function' && box.children) {
      box = box.children.find(function (c) { return c && typeof c._setHeight === 'function'; });
    }
    if (box && typeof box._setHeight === 'function') {
      box._setHeight(620);
      box._setHeight(0);
      check('  ↳ ' + name + ' 可调高度（_setHeight 不抛异常）', true);
    }
  } catch (err) {
    check(name, false, err.name + ': ' + err.message);
  }
});

/* ---------------- 四、标题只许出现一次 / 单步画板不摆「下一步」 ---------------- */
console.log('\n[4] 标题重复与「假分步」（用户实测：这一块不应该长这样）');
function textOf(node, out) {
  out = out || [];
  if (node._html) out.push(String(node._html));
  if (node.textContent) out.push(String(node.textContent));
  (node.children || []).forEach(c => textOf(c, out));
  return out.join(' ');
}
function findDeep(root, cls, out) {
  out = out || [];
  (root.children || []).forEach(c => {
    if (c.classList && c.classList.contains(cls)) out.push(c);
    findDeep(c, cls, out);
  });
  return out;
}
// SVG 元素在桩里没有 className（真实浏览器靠 setAttribute('class')，桩的 setAttribute 是空操作），
// 所以按标签名找图元本身，反而更贴近「到底画没画出来」。
function findByTag(root, tag, out) {
  out = out || [];
  (root.children || []).forEach(c => {
    if (String(c.tagName).toUpperCase() === tag) out.push(c);
    findByTag(c, tag, out);
  });
  return out;
}

// 模型经常把同一个标题同时写进 spec 和组件（draw_board 就是这么发的），
// 照字面渲染就是「圆的坐标几何」上下重复两遍。
const dupSpec = UI.renderSpec({
  title: '圆的坐标几何',
  items: [{ type: 'board', title: '圆的坐标几何', height: 400, xRange: [-2, 8], yRange: [-2, 8],
            steps: [{ caption: '画一个圆并标出圆心和半径', shapes: [
              { kind: 'circle', center: [2, 3], r: 4, label: '圆' }] }] }],
});
const dupCount = (textOf(dupSpec).match(/圆的坐标几何/g) || []).length;
check('同名标题只渲染一次', dupCount === 1, '出现了 ' + dupCount + ' 次');
check('画板自己的标题也不再重复挂一遍',
  findDeep(dupSpec, 'au-chart-title').length === 0 || dupCount === 1);

// 只有一步 = 一张静态图，不该出现「第 1 / 1 步」和灰掉的「下一步」
const single = UI.renderNode({
  type: 'board', height: 400, xRange: [-2, 8], yRange: [-2, 8],
  steps: [{ caption: '画一个圆并标出圆心和半径', shapes: [{ kind: 'circle', center: [2, 3], r: 4, label: '圆' }] }],
});
// 画板第一次渲染挂在 setTimeout 里；测试里直接调它的 _redraw 同步跑一遍，
// 不然断言的是「还没画完」的中间状态（这里面踩过一次）。
single._redraw();
check('单步画板没有「上一步 / 下一步」按钮条', findDeep(single, 'au-board-bar').length === 0);
check('单步画板不写「第 1 / 1 步」', textOf(single).indexOf('第 1 / 1 步') < 0, textOf(single).slice(0, 60));
check('单步画板仍然显示那句说明', textOf(single).indexOf('画一个圆并标出圆心和半径') >= 0, textOf(single).slice(0, 60));
// 画板里的「圆」是 <ellipse>（rx=ry，因为要跟着等比映射走），所以按 ellipse 找
const singleShapes = findByTag(single, 'ELLIPSE').length + findByTag(single, 'CIRCLE').length;
check('单步画板仍把图元画出来（真的画了一个圆/椭圆）', singleShapes >= 1, singleShapes + ' 个');
check('单步画板上圆的标注还在', textOf(single).indexOf('圆') >= 0);

// 真·分步画板要保持原样（有步骤条、有「第 n / N 步」）
const multi = UI.renderNode(specs['board 圆与内接正方形（分步）']);
multi._redraw();
check('多步画板仍然有步骤条', findDeep(multi, 'au-board-bar').length === 1);
check('多步画板仍然写「第 1 / 6 步」', textOf(multi).indexOf('第 1 / 6 步') >= 0, textOf(multi).slice(0, 60));
multi.children.find(c => c.className.indexOf('au-board-bar') >= 0).children[1].onclick();
check('多步画板的「下一步」还能点', textOf(multi).indexOf('第 2 / 6 步') >= 0, textOf(multi).slice(0, 60));

console.log('\n' + (failures === 0 ? '全部通过 ✓' : failures + ' 项失败 ✗'));
process.exit(failures === 0 ? 0 : 1);
