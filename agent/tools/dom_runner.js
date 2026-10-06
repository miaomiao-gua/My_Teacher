/* ============================================================================
 * dom_runner.js —— 在 Node 里把网页「跑一遍，并挨个点按钮」
 * ----------------------------------------------------------------------------
 * 为什么需要它：
 *   1) `node --check` 只查语法，抓不到 `document.getElementById('x') = y` 这类错误——
 *      它语法合法、**运行时**才抛 `ReferenceError`，却会让从那一刻起整页脚本失效。
 *   2) 更隐蔽的是：这类错误常常藏在某个按钮的回调里（点之前一切正常，点完就死）。
 *      第一版酸碱盐仿真就是这样——不选指示剂能玩，一选酚酞/石蕊整页失灵。
 *   3) 受环境限制时 headless 浏览器拿不到 DOM，所以这里自己造最小 DOM 桩。
 *
 * 做法：
 *   1. 抽出所有内联 <script>（跳过带 src 的）
 *   2. Proxy 造桩 DOM；元素支持 addEventListener（回调被收集下来）
 *   3. 执行脚本（vm.runInNewContext + lineOffset，报错行号直接对应 HTML 行号）
 *   4. 跑 setTimeout/requestAnimationFrame 排队的回调
 *   5. **交互冒烟**：把页面里所有 on* 内联处理函数 + 注册的 click/input/change 回调各触发一次
 *   6. 输出 `AUCHECK|<错误列表>` 给 Python 读
 *
 * 边界（要诚实）：它抓初始化异常、点交互炸点、console.error；
 * 抓不到纯视觉问题（布局、动画、配色）——那还是要人眼看。
 * 用法：node dom_runner.js <html文件路径>
 * ========================================================================== */
'use strict';

const fs = require('fs');
const vm = require('vm');

const file = process.argv[2];
if (!file) {
  console.log('AUCHECK|缺少 HTML 文件参数');
  process.exit(0);
}

let html = '';
try {
  html = fs.readFileSync(file, 'utf8');
} catch (err) {
  console.log('AUCHECK|读不到文件：' + err.message);
  process.exit(0);
}

/* ---------- 1. 抽出内联脚本 ---------- */
const scripts = [];
const scriptRe = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
let m;
while ((m = scriptRe.exec(html)) !== null) {
  if (/\bsrc\s*=/i.test(m[1])) continue;
  const lineOffset = html.slice(0, m.index + m[0].indexOf(m[2])).split('\n').length - 1;
  scripts.push({ code: m[2], lineOffset: lineOffset });
}

/* ---------- 2. 桩 DOM ---------- */
const listeners = [];   // {name, fn}  元素上注册的回调
const errors = [];      // {phase, name, message}

function makeStub(label) {
  const target = function () {};
  target.__label = label;
  // 事件系统：把回调收集起来，稍后统一触发一次（这是抓「点完就死」的关键）
  target.addEventListener = function (type, fn) {
    if (typeof fn === 'function') listeners.push({ name: label + ':' + type, fn: fn });
  };
  target.removeEventListener = function () {};
  target.dispatchEvent = function () { return true; };
  target.click = function () {};
  target.focus = function () {};
  target.blur = function () {};
  target.setAttribute = function () {};
  target.getAttribute = function () { return null; };
  target.appendChild = function (child) { return child; };
  target.removeChild = function () {};
  target.insertBefore = function (child) { return child; };
  target.remove = function () {};
  target.contains = function () { return false; };
  target.querySelector = function () { return makeStub('el'); };
  target.querySelectorAll = function () { return [makeStub('el'), makeStub('el'), makeStub('el')]; };

  const handler = {
    get(t, prop) {
      if (prop === 'then') return undefined;               // 别让 await/Promise 误判
      if (prop === Symbol.toPrimitive) return () => '';
      if (prop === Symbol.iterator) return undefined;
      if (prop === 'toString') return () => '';
      if (prop === 'valueOf') return () => 0;
      if (prop === 'length') return 0;
      if (prop === 'nodeType') return 1;
      if (prop === 'tagName') return 'DIV';
      if (prop === 'textContent' || prop === 'innerHTML' || prop === 'value') return '';
      if (prop in t) return t[prop];
      const child = makeStub(label + '.' + String(prop));
      t[prop] = child;
      return child;
    },
    set(t, prop, value) { t[prop] = value; return true; },
    has() { return true; },
    apply() { return makeStub(label + '()'); },
    construct() { return makeStub(label + '{}'); },
  };
  return new Proxy(target, handler);
}

const timers = [];
const sandbox = {
  console: {
    log: function () {}, info: function () {}, warn: function () {}, debug: function () {},
    error: function () {
      errors.push({ phase: 'console.error', name: '', message: Array.prototype.join.call(arguments, ' ').slice(0, 200) });
    },
  },
  setTimeout: function (fn) { if (typeof fn === 'function') timers.push(fn); return timers.length; },
  setInterval: function () { return 0; },
  clearTimeout: function () {},
  clearInterval: function () {},
  requestAnimationFrame: function (fn) { if (typeof fn === 'function') timers.push(fn); return 0; },
  cancelAnimationFrame: function () {},
  alert: function () {}, confirm: function () { return true; }, prompt: function () { return ''; },
  fetch: function () { return Promise.resolve(makeStub('fetch')); },
  XMLHttpRequest: function () { return makeStub('xhr'); },
  Math: Math, JSON: JSON, Date: Date, Object: Object, Array: Array, String: String,
  Number: Number, Boolean: Boolean, RegExp: RegExp, Error: Error, TypeError: TypeError,
  Promise: Promise, isNaN: isNaN, isFinite: isFinite,
  parseInt: parseInt, parseFloat: parseFloat,
  encodeURIComponent: encodeURIComponent, decodeURIComponent: decodeURIComponent,
  document: makeStub('document'),
  navigator: { userAgent: 'dom-runner', language: 'zh-CN', clipboard: makeStub('clipboard') },
  location: { href: 'about:blank', search: '', hash: '', reload: function () {} },
  localStorage: makeStub('localStorage'),
  sessionStorage: makeStub('sessionStorage'),
  performance: { now: function () { return Date.now(); } },
  devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
  Audio: function () { return makeStub('audio'); },
  Image: function () { return makeStub('image'); },
  Event: function () { return makeStub('event'); },
  CustomEvent: function () { return makeStub('customevent'); },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
sandbox.document.body = makeStub('body');
sandbox.document.head = makeStub('head');
sandbox.document.documentElement = makeStub('html');

const context = vm.createContext(sandbox);

/* ---------- 3. 执行脚本 ---------- */
function record(phase, name, err) {
  const msg = (err && err.name ? err.name + ': ' : '') + ((err && err.message) || String(err));
  // 行号在 stack 里（vm 已按 lineOffset 映射成 HTML 的真实行号），取最深的那一帧
  let where = '';
  const stack = String((err && err.stack) || '');
  const hit = stack.match(/page\.html:(\d+):(\d+)/);
  if (hit) where = '（第 ' + hit[1] + ' 行）';
  errors.push({ phase: phase, name: name, message: (msg + where).slice(0, 300) });
}

function runBlock(code, lineOffset, phase, name) {
  try {
    vm.runInNewContext(code, context, { filename: 'page.html', lineOffset: lineOffset, timeout: 4000 });
  } catch (err) {
    record(phase, name, err);
  }
}

scripts.forEach(function (s, i) { runBlock(s.code, s.lineOffset, '加载时', '脚本' + (i + 1)); });

const runTimer = function (fn, phase) {
  try {
    fn();
  } catch (err) {
    record(phase, '', err);
  }
};
let guard = 0;
while (timers.length && guard < 20) { runTimer(timers.shift(), '延迟执行'); guard++; }

/* ---------- 4. 交互冒烟：把「按钮/回调」都触发一次 ---------- */
// 4a. 内联 on* 属性里的代码（onclick="addAcid()" 这种）
const inlineHandlers = [];
const attrRe = /\son(click|input|change|submit|mousedown|mouseup)\s*=\s*("([^"]*)"|'([^']*)')/gi;
let am;
while ((am = attrRe.exec(html)) !== null) {
  const code = (am[3] !== undefined ? am[3] : am[4]) || '';
  if (code.trim()) inlineHandlers.push({ name: 'on' + am[1], code: code });
  if (inlineHandlers.length >= 12) break;
}
sandbox.__auInline = inlineHandlers;
sandbox.__auListeners = listeners;
sandbox.__auErrors = errors;
sandbox.__auMakeStub = makeStub;   // 让桩内的事件对象也用完整桩，避免"桩太薄"造成假阳性

// 4b. 统一在 vm 里跑交互（自带超时，避免某个回调死循环把检查卡住）
const HARNESS = `
(function(){
  var errs = __auErrors;
  function rec(phase, name, e) {
    errs.push({ phase: phase, name: name, message: ((e && e.name ? e.name + ': ' : '') + ((e && e.message) || String(e))).slice(0, 300) });
  }
  // 内联处理函数
  for (var i = 0; i < __auInline.length; i++) {
    var h = __auInline[i];
    try { (0, eval)(h.code); } catch (e) { rec('交互测试', h.name, e); }
  }
  // 注册的事件回调（每个只触发一次）
  var limit = Math.min(__auListeners.length, 12);
  for (var j = 0; j < limit; j++) {
    var item = __auListeners[j];
    try {
      var ev = __auMakeStub('event');
      ev.type = 'click';
      ev.preventDefault = function(){};
      ev.stopPropagation = function(){};
      ev.target = __auMakeStub('target');
      item.fn.call(__auMakeStub('this'), ev);
    } catch (e) { rec('交互测试', item.name, e); }
  }
})();
`;

try {
  vm.runInContext(HARNESS, context, { timeout: 6000 });
} catch (err) {
  record('交互测试', '整体', err);
}

/* ---------- 5. 输出 ----------
 * 只报「**不可能由桩环境造成**」的错误类型：ReferenceError（用了没定义的变量 / 给函数结果赋值）
 * 与 SyntaxError。这是踩过坑之后的教训——
 *   桩 DOM 的 dataset/value 取到的是桩对象而不是字符串，页面拿它当 key 查表必然查不到，
 *   于是会报出根本不存在的 `TypeError: Cannot read properties of undefined`。
 *   第一版把这类错也报给模型，结果模型老老实实追着改了 4 轮、白烧 8 分钟，最后什么都没产出。
 * **假错误比没有检查更糟**——它消耗的是模型的轮次，而轮次是有限资源。
 * 所以 TypeError / console.error 一律不上报（它们在真浏览器里可能完全正常）。
 */
const reportable = errors.filter(function (e) {
  return /ReferenceError|SyntaxError/.test(e.message);
});

const parts = reportable.slice(0, 5).map(function (e) {
  return '[' + e.phase + (e.name ? '·' + e.name : '') + '] ' + e.message;
});
console.log('AUCHECK|' + (parts.length ? parts.join(' ;; ') : 'OK'));
