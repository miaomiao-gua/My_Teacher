/* ============================================================================
 * 三栏工作台冒烟测试：确认 mountApp 能真的搭起来（不抛异常、结构正确）
 * ----------------------------------------------------------------------------
 * 为什么值得测：三栏 + 历史树 + 两条可拖分栏，是纯 DOM 组装 —— 一旦某个元素
 * 少建了/顺序错了，浏览器里就是「一片空白」，而控制台可能只有一行报错。
 * 这里用最小 DOM 桩 + 假的 fetch，把整条启动流程跑一遍。
 * 跑法：node data/agent/_app_tests.js
 * ========================================================================== */
'use strict';

const calls = [];
const RESPONSES = {
  '/agent/health': { ok: true, chain: [{ label: 'openai:deepseek-v3', ok: true }], tools: 21, plugins: ['acid_base_lab'] },
  '/agent/settings': { settings: { llm_provider: 'cloud' }, chain: ['openai:deepseek-v3'] },
  '/agent/sessions': {
    total: 4,
    tree: [{
      name: '数学', count: 3, textbooks: [{
        name: '人教版必修一', count: 3, sessions: [
          { id: 's1', title: '二次函数', subject: '数学', textbook: '人教版必修一', updated_at: 3 },
          { id: 's2', title: '三角函数图像', subject: '数学', textbook: '人教版必修一', updated_at: 2 },
          { id: 's3', title: '指数与对数', subject: '数学', textbook: '人教版必修一', updated_at: 1 },
        ],
      }],
    }, {
      name: '未分类', count: 1, textbooks: [{ name: '未指定教材', count: 1, sessions: [{ id: 's9', title: '新对话' }] }],
    }],
  },
  '/agent/session/s1': {
    id: 's1', title: '二次函数', messages: [
      { role: 'user', content: '讲讲二次函数' },
      { role: 'assistant', content: '### 二次函数\n- 开口方向由 a 决定\n- 顶点坐标 (−b/2a, …)' },
    ],
    last_ui: { title: '二次函数图像', items: [{ type: 'plot', xMin: -5, xMax: 5, series: [{ expr: 'a*x^2' }] }] },
    files: [],
  },
};

global.window = {
  devicePixelRatio: 1,
  location: { protocol: 'http:', origin: 'http://127.0.0.1:5001', port: '5001' },
  _listeners: {},
  addEventListener(type, fn) { (global.window._listeners[type] = global.window._listeners[type] || []).push(fn); },
  removeEventListener() {},
  prompt: () => null, confirm: () => false,
  innerHeight: 900,
};
// 让测试能真的「拖动」：把注册到 window 上的 mousemove/mouseup 派发出去
function fireWindow(type, ev) {
  (global.window._listeners[type] || []).forEach(fn => fn(Object.assign({ preventDefault() {}, stopPropagation() {} }, ev)));
}
function fireEl(node, type, ev) {
  (node._listeners[type] || []).forEach(fn => fn(Object.assign({ preventDefault() {}, stopPropagation() {}, clientX: 100, clientY: 100, target: node, currentTarget: node }, ev)));
}
global.window.katex = { renderToString: (t) => '<span class="katex">' + t + '</span>' };

function fakeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    style: {}, dataset: {}, children: [], childNodes: [],
    className: '', textContent: '', _html: '',
    get innerHTML() { return node._html; },
    // 置空 innerHTML 在真实 DOM 里会**连带删掉子节点**，桩也得照做：
    // 工作台就是靠 `stage.innerHTML = ''` 清掉上一次的内容（不照做的话
    // 「清空」这件事在测试里根本没发生，计数全是错的）。
    set innerHTML(v) {
      node._html = String(v);
      if (v === '') { node.children.length = 0; node.childNodes.length = 0; }
    },
    // 关键保真点：真实浏览器里设置 style.width 会改变元素的实际宽度。
    // 桩如果让宽度保持不变，拖拽的「按下 → 移动 → 松开」就会在松开时被还原，
    // 测试会误报失败（我第一版就踩了），更糟的是还可能把 bug 盖住。
    _cw: undefined,
    get clientWidth() {
      if (node.style.width) return parseFloat(node.style.width) || node._cw || 300;
      return node._cw === undefined ? 300 : node._cw;
    },
    set clientWidth(v) { node._cw = v; },
    clientHeight: 800, offsetWidth: 1400, offsetHeight: 800,
    // parentNode / removeChild 必须是真的：生产代码里「关掉这个窗口」走的就是
    // box.parentNode.removeChild(box)，桩里不记账的话这个功能测不到（也掩盖 bug）。
    parentNode: null,
    appendChild(c) { c.parentNode = node; node.children.push(c); node.childNodes.push(c); return c; },
    insertBefore(c) { c.parentNode = node; node.children.unshift(c); node.childNodes.unshift(c); return c; },
    removeChild(c) {
      const i = node.children.indexOf(c);
      if (i >= 0) node.children.splice(i, 1);
      const j = node.childNodes.indexOf(c);
      if (j >= 0) node.childNodes.splice(j, 1);
      if (c) c.parentNode = null;
      return c;
    },
    remove() { if (node.parentNode) node.parentNode.removeChild(node); },
    setAttribute() {}, getAttribute() { return null; },
    _listeners: {},
    addEventListener(type, fn) { (node._listeners[type] = node._listeners[type] || []).push(fn); },
    removeEventListener() {},
    querySelector(sel) { return node._q = node._q || fakeEl(sel); },
    querySelectorAll() { return []; },
    getContext() { return new Proxy({}, { get: () => () => {}, set: () => true }); },
    getBoundingClientRect() { return { left: 0, top: 0, width: node.clientWidth || 300, height: 800 }; },
    focus() {}, click() {},
  };
  // classList 必须与 className 同步读取 —— 真实 DOM 里 el(tag, 'au-nav') 设的就是 className，
  // 桩如果只认 classList.add()，测试就会误报「元素类型不对」（我自己踩过）。
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
  _listeners: {},
  addEventListener(type, fn) { (global.document._listeners[type] = global.document._listeners[type] || []).push(fn); },
  removeEventListener(type, fn) {
    const arr = global.document._listeners[type] || [];
    const i = arr.indexOf(fn);
    if (i >= 0) arr.splice(i, 1);
  },
  getElementById() { return null; },
  querySelector() { return fakeEl('div'); }, querySelectorAll() { return []; },
};
Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText() {} } }, configurable: true });
global.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };
global.localStorage = {
  _d: {}, getItem(k) { return this._d[k] === undefined ? null : this._d[k]; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; },
};
global.fetch = async function (url, init) {
  const path = String(url).replace(/^https?:\/\/[^/]+/, '').split('?')[0];
  calls.push((init && init.method) || 'GET');
  calls.push(path);
  const body = RESPONSES[path];
  if (body === undefined) return { ok: false, status: 404, text: async () => 'not found' };
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
};

require('D:/My_teacher/agent_studio/web/static/js/agent_ui.js');
const UI = window.AgentUI;

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log((ok ? '  OK    ' : '  FAIL  ') + name + (detail && !ok ? '  →  ' + detail : (detail && ok ? '  →  ' + detail : '')));
}

(async function main() {
  const root = fakeEl('div');
  let app = null;
  try {
    app = UI.mountApp(root, {});
    check('mountApp 不抛异常', true);
  } catch (err) {
    check('mountApp 不抛异常', false, err.name + ': ' + err.message + '\n' + String(err.stack).split('\n')[1]);
    console.log('\n' + failures + ' 项失败 ✗');
    process.exit(1);
  }

  check('三栏外壳搭好了（nav + 分栏 + main + 分栏 + side）', root.children.length === 5,
    '实际 ' + root.children.length + ' 个顶层节点');

  const nav = root.children[0];
  const main = root.children[2];
  const side = root.children[4];
  check('左栏是历史栏', nav.classList.contains('au-nav'));
  check('中栏是对话区', main.classList.contains('au-main'));
  check('右栏是工作台', side.classList.contains('au-side'));
  check('左栏里有树容器', !!nav.children.find(c => c.classList.contains('au-tree')));
  check('左栏里有底部状态', !!nav.children.find(c => c.classList.contains('au-nav-foot')));
  check('中栏里挂了聊天区与输入框', main.children.length >= 2);
  check('右栏里挂了工作台', side.children.length >= 1);

  // 等启动流程（boot 里是一串 await 的 fetch）
  await new Promise(r => setTimeout(r, 400));

  check('调用了健康检查接口', calls.indexOf('/agent/health') >= 0);
  check('调用了会话树接口', calls.indexOf('/agent/sessions') >= 0);
  check('恢复了上次/第一条会话（读了会话详情）', calls.some(c => c.indexOf('/agent/session/') === 0));

  const chatEl = main.children.find(c => c.classList.contains('au-chat'));
  const html = chatEl ? chatEl.innerHTML : '';
  const renderedHistory = chatEl ? chatEl.children.some(w => String(w.innerHTML).indexOf('讲讲二次函数') >= 0 ||
    (w.children || []).some(b => String(b.innerHTML).indexOf('讲讲二次函数') >= 0)) : false;
  check('历史消息回放到了聊天区', renderedHistory,
    '聊天区 ' + (chatEl ? chatEl.children.length : 0) + ' 个气泡');
  const stageEl = side.children.find(c => c.classList.contains('au-stage'));
  const uiMounted = !!stageEl && stageEl.children.some(x => x.classList && x.classList.contains('au-surface-item'));
  check('切回会话时恢复了上次的界面（last_ui）', uiMounted,
    stageEl ? '工作台里有 ' + stageEl.children.length + ' 个节点' : '没找到工作台容器');

  /* ---------- 每个栏目的宽度都要能拖 ---------- */
  console.log('\n--- 列宽可调 ---');
  const splitL = root.children[1];
  const splitR = root.children[3];
  check('两根分栏线都在', splitL.classList.contains('au-splitter') && splitR.classList.contains('au-splitter'));

  // CSS 回归：三栏模式绝不能沿用「工作台空就藏分栏线」的规则（那会导致刚打开时一条都拖不动）
  const css = UI._css || '';
  check('CSS 里的隐藏规则已排除三栏模式',
    css.indexOf('.au-root:not(.au-app):not(.au-has-surface) .au-splitter{display:none}') >= 0,
    '没找到带 :not(.au-app) 的限定');
  check('CSS 里没有不加限定的旧规则（防回归）',
    css.indexOf('.au-root:not(.au-has-surface) .au-splitter{display:none}') < 0,
    '旧规则还在，会把左栏分栏线一起藏掉');

  /* ---------- 分栏线必须「看得见 + 抓得住」（用户实测：拖不动） ----------
     根因有两层：
       1) 命中区只有 7px、视觉上只有 1px 淡线 → 根本抓不准；
       2) 更致命的是窄窗口的媒体查询把 .au-app 改成了 flex-direction:column ——
          分栏线写的是 flex-basis/width，在竖向容器里 flex-basis 被当成「高度」，
          于是拖「左右」分栏线变成改高度、面板还被 width 撑成固定宽度向右伸出。
          用户原话：「我没办法把他拖动，方向反了，他整体向右侧延申（上下被缩放了）」。 */
  console.log('\n--- 分栏线可见性 / 命中区 ---');
  check('分栏线命中区已加宽到 10px', css.indexOf('.au-splitter{flex:0 0 10px') >= 0,
    '还是 7px 那种抓不住的细线');
  check('分栏线中间有可见的抓手', css.indexOf('.au-splitter::before{') >= 0);
  check('三栏模式永远不会被改成上下堆叠（否则拖动会变成改高度）',
    css.indexOf('.au-app{flex-direction:column}') < 0,
    '窄窗口媒体查询里又把 .au-app 竖过来了');
  // 逐条检查「隐藏分栏线」的规则：只要选择器里不带 .au-app，三栏模式就会被一起藏掉
  const hideRe = /([^{}\n]*?)\.au-splitter\{display:none\}/g;
  const bareHides = [];
  let hm;
  while ((hm = hideRe.exec(css)) !== null) {
    if (hm[1].indexOf('.au-app') < 0) bareHides.push(hm[1].trim() || '(无选择器)');
  }
  check('不存在「把三栏分栏线一起藏掉」的裸规则',
    bareHides.length === 0,
    '这几条选择器没排除三栏模式：' + bareHides.join(' / '));
  check('工作台标题条样式已定义（宽度按钮的家）', css.indexOf('.au-side-head{') >= 0);

  // 真的模拟一次拖动：把 window 上的 mousemove/mouseup 派发出去
  root.clientWidth = 1400;
  nav.clientWidth = 262;
  side.clientWidth = 420;       // 右栏常驻（不再有"工作台空就隐藏分栏线"这回事）
  fireEl(splitL, 'mousedown', { clientX: 300 });
  fireWindow('mousemove', { clientX: 500 });
  fireWindow('mouseup', {});
  const navW = parseFloat(nav.style.width) || 0;
  check('拖左分栏线能加宽历史栏', navW > 262, '262 → ' + navW + 'px');

  fireEl(splitL, 'mousedown', { clientX: 300 });
  fireWindow('mousemove', { clientX: 5000 });   // 往死里拖
  fireWindow('mouseup', {});
  const navW2 = parseFloat(nav.style.width) || 0;
  const mainW = 1400 - navW2;
  check('拖到极限时中间栏仍保留最小值（不会把聊天区挤没）', mainW >= 250,
    `历史栏 ${navW2} + 中间栏 ${mainW}`);

  // 右分栏线必须**从一开始就存在且可见** —— 三栏模式下右栏常驻，分栏线不该被藏起来
  // （用户实测：抓不到它。根因是我把"工作台空就不占位"从两栏模式照搬过来，
  //   结果分栏线被藏了、右栏却还占着宽度，自相矛盾。测试第一版还手动点亮它，掩盖了这个 bug。）
  check('右分栏线从一开始就可见（不能藏）',
    splitR.style.display !== 'none',
    `display="${splitR.style.display}"`);
  fireEl(splitL, 'dblclick', {});
  nav.clientWidth = 262;
  side.clientWidth = 420;
  fireEl(splitR, 'mousedown', { clientX: 900 });
  fireWindow('mousemove', { clientX: 700 });    // 往左拖 = 工作台变宽
  fireWindow('mouseup', {});
  const sideW = parseFloat(side.style.width) || 0;
  check('拖右分栏线能加宽工作台', sideW > 420, '420 → ' + sideW + 'px');

  // 备用抓手：工作台标题条上的宽度按钮。设备/浏览器再怎么闹别扭，这条路一定能用。
  function findByClass(root, cls, out) {
    out = out || [];
    (root.children || []).forEach(c => {
      if (c.classList && c.classList.contains(cls)) out.push(c);
      findByClass(c, cls, out);
    });
    return out;
  }
  const sideHead = side.children.find(c => c.classList && c.classList.contains('au-side-head'));
  check('工作台顶上有一条标题栏', !!sideHead);
  const chip = findByClass(sideHead || fakeEl('div'), 'au-wchip')[0];
  check('标题栏上有宽度按钮（拖不动时的备用手段）', !!chip);
  // 学生自己开计算器的入口：没有它，学生就只能干等模型（实测反馈「为啥我没看出来」）
  const calcBtn = (sideHead ? findByClass(sideHead, 'au-btn') : []).find(b => b.innerHTML === '＋ 计算器');
  check('标题栏上有「＋ 计算器」按钮（学生自己写公式的入口）', !!calcBtn,
    sideHead ? findByClass(sideHead, 'au-btn').map(b => b.innerHTML).join(' / ') : '没有标题栏');
  if (chip) {
    side.style.width = ''; side.style.flex = ''; side.clientWidth = 420;
    nav.style.width = ''; nav.clientWidth = 262;
    root.clientWidth = 1400;
    fireEl(chip, 'click', {});
    const w2 = parseFloat(side.style.width) || 0;
    check('点宽度按钮能改工作台宽度', w2 > 420, '420 → ' + w2 + 'px');
  }

  // 点「＋ 计算器」→ 工作台里真的出现一台空白的图形计算器（一行空行等着输入）
  if (calcBtn) {
    const stage = side.children.find(c => c.classList && c.classList.contains('au-stage'));
    const before = stage ? stage.children.filter(c => c.classList.contains('au-surface-item')).length : 0;
    calcBtn.onclick({ preventDefault() {} });
    const after = stage ? stage.children.filter(c => c.classList.contains('au-surface-item')).length : 0;
    check('点一下就多了一个工作台窗口', after === before + 1, before + ' → ' + after);
    const calc = findByClass(stage || fakeEl('div'), 'au-calc')[0];
    check('窗口里就是图形计算器', !!calc);
    check('计算器自动带一行空行（学生直接就能写）',
      !!calc && calc._rows.length === 1 && calc._rows[0].raw === '' && calc._rows[0].kind === 'empty',
      calc ? calc._rows.map(r => '"' + r.raw + '"[' + r.kind + ']').join(' ') : '没有计算器');
    check('空计算器也画了坐标系（不是一片白）', !!findByClass(calc || fakeEl('div'), 'au-canvas')[0]);
  }

  /* ---------- 每个窗口右上角的关闭按钮 ---------- */
  console.log('\n--- 窗口右上角的 ✕ ---------');
  const stageEl2 = side.children.find(c => c.classList && c.classList.contains('au-stage'));
  const items = (stageEl2 ? stageEl2.children : []).filter(c => c.classList && c.classList.contains('au-surface-item'));
  check('工作台里有窗口可以测', items.length >= 1, items.length + ' 个');
  check('每个窗口都有一条标题栏', items.every(it => it.children[0] &&
    it.children[0].classList.contains('au-surface-head')));
  const heads = items.map(it => it.children[0]);
  check('标题栏里有标题文字', heads.every(h => findByClass(h, 'au-surface-title').length === 1));
  const closers = heads.map(h => findByClass(h, 'au-surface-close')[0]);
  check('每个窗口的右上角都有 ✕ 关闭按钮', closers.every(Boolean),
    closers.filter(Boolean).length + '/' + items.length + ' 个');
  check('✕ 在标题栏里（也就是窗口顶部），不是埋在底部状态条里',
    items.every(it => !findByClass(it, 'au-state').some(s => findByClass(s, 'au-surface-close').length)));
  check('标题只在标题栏出现一次（组件里不再重复渲染 spec 标题）',
    items.every(it => findByClass(it, 'au-spec-title').length === 0));
  if (closers[0]) {
    const n0 = items.length;
    closers[0].onclick({});
    const n1 = stageEl2.children.filter(c => c.classList && c.classList.contains('au-surface-item')).length;
    check('点 ✕ 真的关掉了这个窗口', n1 === n0 - 1, n0 + ' → ' + n1);
    check('关掉之后剩下的窗口还有自己的 ✕', stageEl2.children
      .filter(c => c.classList && c.classList.contains('au-surface-item'))
      .every(it => findByClass(it, 'au-surface-close').length === 1));
  }

  // 组件标题和窗口标题一模一样时（draw_board 就是这么发的：spec.title 和 board.title 同名），
  // 窗口里那份也要去掉，否则就是「圆的坐标几何」上下重复两遍（用户实测抱怨的原话）。
  if (app && app.chat && app.chat.renderSpec) {
    app.chat.renderSpec({
      title: '圆的坐标几何',
      items: [{ type: 'board', title: '圆的坐标几何', height: 300, xRange: [-2, 8], yRange: [-2, 8],
                steps: [{ caption: '画一个圆', shapes: [{ kind: 'circle', center: [2, 3], r: 4 }] }] }],
    });
    const newest = stageEl2.children.filter(c => c.classList && c.classList.contains('au-surface-item'))[0];
    const headTitle = newest ? findByClass(newest, 'au-surface-head')[0] : null;
    const shown = headTitle ? findByClass(headTitle, 'au-surface-title')[0] : null;
    const insideBoard = newest ? findByClass(newest, 'au-chart-title').length : -1;
    check('同名标题只出现一次（标题栏那一次）',
      !!shown && String(shown.innerHTML) === '圆的坐标几何' && insideBoard === 0,
      '标题栏="' + (shown ? shown.innerHTML : '无') + '"，组件里还有 ' + insideBoard + ' 个同名标题');
  }

  /* ---------- 右键菜单（IDE 习惯） ---------- */
  console.log('\n--- 右键菜单 ---');
  const treeEl = nav.children.find(c => c.classList.contains('au-tree'));
  const bodyEl = global.document.body;

  fireEl(treeEl, 'contextmenu', {});
  const menus = bodyEl.children.filter(c => c.classList.contains('au-menu'));
  check('空白处右键弹出了菜单', menus.length === 1, '菜单数 ' + menus.length);
  const menuText = menus.length ? menus[0].children.map(c => String(c.innerHTML)).join('|') : '';
  check('空白处菜单里有「新建学科」', menuText.indexOf('新建学科') >= 0);
  check('空白处菜单里有「刷新列表」', menuText.indexOf('刷新列表') >= 0);

  // 找到教材那一层，右键它 → 应该有「新建对话」
  let bookSummary = null;
  const d1 = treeEl.children.find(c => c.classList.contains('au-folder'));
  if (d1) {
    const d2 = (d1.children || []).find(c => c.classList.contains('au-folder-2'));
    if (d2) bookSummary = (d2.children || []).find(c => c.tagName === 'SUMMARY');
  }
  check('树上找到了教材节点', !!bookSummary);
  if (bookSummary) {
    bodyEl.children.length = 0;                       // 清掉上一个菜单
    fireEl(bookSummary, 'contextmenu', {});
    const menus2 = bodyEl.children.filter(c => c.classList.contains('au-menu'));
    const text2 = menus2.length ? menus2[0].children.map(c => String(c.innerHTML)).join('|') : '';
    check('教材上右键有「新建对话」', text2.indexOf('新建对话') >= 0);
    check('教材上右键有「重命名教材」', text2.indexOf('重命名教材') >= 0);
    check('教材上右键有「删除文件夹」', text2.indexOf('删除文件夹') >= 0);
  }

  // 对话上右键 → 打开/重命名/移动到/删除
  let sessionEl = null;
  if (d1) {
    const d2 = (d1.children || []).find(c => c.classList.contains('au-folder-2'));
    if (d2) sessionEl = (d2.children || []).find(c => c.classList.contains('au-session'));
  }
  check('树上找到了对话节点', !!sessionEl);
  if (sessionEl) {
    bodyEl.children.length = 0;
    fireEl(sessionEl, 'contextmenu', {});
    const menus3 = bodyEl.children.filter(c => c.classList.contains('au-menu'));
    const text3 = menus3.length ? menus3[0].children.map(c => String(c.innerHTML)).join('|') : '';
    check('对话上右键有「打开」', text3.indexOf('打开') >= 0);
    check('对话上右键有「移动到」', text3.indexOf('移动到') >= 0);
    check('对话上右键有「删除对话」', text3.indexOf('删除对话') >= 0);
  }
  check('菜单样式已定义', (UI._css || '').indexOf('.au-menu{position:fixed') >= 0);
  check('就地改名样式已定义', (UI._css || '').indexOf('.au-inline-edit') >= 0);

  /* ---------- 工具窗口的高度也要能拖 ---------- */
  console.log('\n--- 工具窗口高度可调 ---');
  check('工作台拖拉条样式已定义', (UI._css || '').indexOf('.au-surface-grip') >= 0);
  const surfaceItem = stageEl && stageEl.children.find(x => x.classList && x.classList.contains('au-surface-item'));
  check('工作台里有一个工具窗口', !!surfaceItem);
  if (surfaceItem) {
    const grip = (surfaceItem.children || []).find(c => c.classList && c.classList.contains('au-surface-grip'));
    check('工具窗口底部有拖拉条', !!grip);
    if (grip) {
      surfaceItem.clientWidth = 900;
      surfaceItem.clientHeight = 400;
      fireEl(grip, 'mousedown', { clientY: 300 });
      fireWindow('mousemove', { clientY: 460 });      // 往下拖 160px
      fireWindow('mouseup', {});
      const h = parseFloat(surfaceItem.style.height) || 0;
      check('拖动后高度真的变了', h > 400, `400 → ${h}px`);
      fireEl(grip, 'dblclick', {});
      check('双击可恢复自适应', !surfaceItem.style.height, `style.height="${surfaceItem.style.height}"`);
    }
  }

  /* ---------- 三栏模式下中栏必须保持「弹性列」 ----------
     两栏模式会把用户拖出来的宽度存进 localStorage.au_left_width；
     三栏模式如果照搬它，就会给中间那栏写死 flex:0 0 368px ——
     三栏全变固定宽，窗口一宽右边空一大块，怎么拖都不对劲。
     这里把那个旧键预先塞进 localStorage，再挂一次 app，验证中栏没被写死。 */
  console.log('\n--- 三栏模式下中栏必须保持弹性 ---');
  global.localStorage.setItem('au_left_width', '368');
  global.localStorage.setItem('au_nav_w', '300');
  global.localStorage.setItem('au_side_w', '520');
  const root2 = fakeEl('div');
  root2.clientWidth = 1600;
  let main2 = null, nav2 = null, side2 = null;
  try {
    UI.mountApp(root2, {});
    main2 = root2.children[2]; nav2 = root2.children[0]; side2 = root2.children[4];
    check('第二次 mountApp 也不抛异常', true);
  } catch (err) {
    check('第二次 mountApp 也不抛异常', false, err.name + ': ' + err.message);
  }
  if (main2) {
    check('中栏没有被两栏模式的旧宽度写死',
      !main2.style.width, 'width="' + main2.style.width + '"');
    check('中栏是弹性列（basis 0 = 只吃剩余空间，不跟左右抢）',
      String(main2.style.flex).indexOf('1 1 0') >= 0 || !main2.style.flex,
      'flex="' + main2.style.flex + '"');
    check('历史栏记忆的宽度被恢复了', parseFloat(nav2.style.width) === 300,
      'width="' + nav2.style.width + '"');
    check('工作台记忆的宽度被恢复了', parseFloat(side2.style.width) === 520,
      'width="' + side2.style.width + '"');
    // 中栏要能「吃掉剩余空间」：三栏宽度加起来必须正好铺满，不留大片空白
    const sum = (parseFloat(nav2.style.width) || 262) + (parseFloat(side2.style.width) || 420);
    check('左右两栏加起来不会超过总宽（中栏还有地方站）', sum < 1600, sum + 'px');
  }

  console.log('\n' + (failures === 0 ? '全部通过 ✓' : failures + ' 项失败 ✗'));
  process.exit(failures === 0 ? 0 : 1);
})();
