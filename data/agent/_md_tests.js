/* ============================================================================
 * Markdown 块级渲染测试
 * ----------------------------------------------------------------------------
 * 背景：聊天窗一开始只按空行分段，于是老师回复里的 `### 第1课：…` 井号、
 * `- 要点` 的短横线全都**原样显示给学生**（用户截图反馈）。
 * 模型是按 Markdown 写作的，渲染端不认块级语法 = 天天露出原始符号。
 *
 * 这个测试盯住两件事：
 *   ① 各类块级语法是否被正确解析（标题/列表/表格/引用/分割线/代码块）
 *   ② 解析之后**不能留下原始标记**（`###`、`|`、``` 等不该出现在渲染结果里）
 * 跑法：node data/agent/_md_tests.js
 * ========================================================================== */
'use strict';

global.window = { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} };
global.window.katex = { renderToString: (t, o) => '<span class="katex" data-display="' + !!(o && o.displayMode) + '">' + t + '</span>' };
function fakeEl(tag) {
  const node = {
    tagName: String(tag || 'div').toUpperCase(), style: {}, dataset: {}, children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    className: '', textContent: '', innerHTML: '',
    clientWidth: 640, clientHeight: 380,
    appendChild(c) { node.children.push(c); return c; }, insertBefore(c) { node.children.unshift(c); return c; },
    removeChild(c) { return c; }, remove() {}, setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return fakeEl(); }, querySelectorAll() { return []; },
    getContext() { return new Proxy({}, { get: () => () => {}, set: () => true }); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 640, height: 380 }; },
    focus() {}, click() {},
  };
  Object.defineProperty(node, 'firstChild', { get() { return node.children[0] || null; } });
  return node;
}
global.document = {
  createElement: fakeEl, createElementNS: (ns, tag) => fakeEl(tag), head: fakeEl('head'), body: fakeEl('body'),
  getElementById() { return null; }, querySelector() { return fakeEl(); }, querySelectorAll() { return []; },
};
Object.defineProperty(global, 'navigator', { value: { clipboard: { writeText() {} } }, configurable: true });
global.ResizeObserver = function () { this.observe = () => {}; this.disconnect = () => {}; };

require('D:/My_teacher/agent_studio/web/static/js/agent_ui.js');
const md = window.AgentUI._fmtBlock;

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log((ok ? '  OK    ' : '  FAIL  ') + name + (detail ? '  →  ' + detail : ''));
}

/* 每项：[说明, 输入, 必须出现的片段[], 绝不能出现的片段[]] */
const CASES = [
  ['标题 ###/####', '### 第1课：Python 列表简介\n#### 1. 认识列表',
    ['<h4', '第1课：Python 列表简介', '<h5', '1. 认识列表'], ['###', '####']],
  ['无序列表', '- 列表可以装不同类型\n- 列表可变\n- 用方括号创建',
    ['<ul class="au-md-list">', '<li>', '列表可变'], ['- 列表']],
  ['有序列表', '1. 先创建列表\n2. 再添加元素\n3. 最后遍历',
    ['<ol class="au-md-list">', '<li>先创建列表</li>'], ['1. 先创建']],
  ['表格', '| 操作 | 方法 |\n|---|---|\n| 添加 | append |\n| 删除 | del |',
    ['<table>', '<th>操作</th>', '<td>append</td>'], ['|---|', '| 操作 |']],
  ['引用', '> 注意：索引从 0 开始', ['<blockquote>', '索引从 0 开始'], ['&gt;', '> 注意']],
  ['分割线', '上面\n\n---\n\n下面', ['<hr class="au-md-hr">', '上面', '下面'], ['---']],
  ['代码围栏', '示例：\n```python\ncols = ["红", "绿"]\nprint(cols[0])\n```\n结束',
    ['<pre class="au-md-code">', 'cols = [&quot;红&quot;, &quot;绿&quot;]'], ['```', 'print(cols[0])\n```']],
  ['代码块里的 # 和 - 不被解析', '```\n# 这是注释不是标题\n- 这是减号不是列表\n```',
    ['<pre', '# 这是注释不是标题', '- 这是减号不是列表'], ['<h', '<ul']],
  ['段落里的行内格式仍生效', '列表用 **append** 在末尾加，`cols[0]` 取第一个。还有 $x^2$',
    ['<strong>append</strong>', '<code>cols[0]</code>', 'katex'], []],
  ['混合内容', '## 要点\n- 创建：`[1,2]`\n- 索引从 **0** 开始\n\n> 记住这一点',
    ['<h3', '<ul', '<strong>0</strong>', '<blockquote>'], ['##', '- 创建']],
  ['纯段落不受影响', '这是一句普通的话。', ['<p class="au-md-p">这是一句普通的话。</p>'], ['<h', '<ul']],
  ['空输入', '', [], ['<p']],
  ['单行长文本', '只有一行，没有块级语法', ['<p class="au-md-p">'], ['<h']],
];

console.log('===== Markdown 块级渲染 =====');
for (const [name, input, mustHave, mustNot] of CASES) {
  const out = md(input);
  const missing = mustHave.filter(s => out.indexOf(s) < 0);
  const leaked = mustNot.filter(s => out.indexOf(s) >= 0);
  const ok = missing.length === 0 && leaked.length === 0;
  check(name, ok, ok ? '' :
    (missing.length ? '缺少 ' + JSON.stringify(missing) + ' ' : '') +
    (leaked.length ? '残留原始标记 ' + JSON.stringify(leaked) : '') +
    (ok ? '' : ' | 实际: ' + out.slice(0, 160)));
}

console.log('\n' + (failures === 0 ? '全部通过 ✓' : failures + ' 项失败 ✗'));
process.exit(failures === 0 ? 0 : 1);
