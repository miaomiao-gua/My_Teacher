/* ============================================================================
 * AgentUI —— 生成式 UI 渲染器 + Agent 事件流客户端
 * ----------------------------------------------------------------------------
 * 它做两件事：
 *   1) 把 AI 生成的「组件树」（JSON）渲染成真实可交互的界面
 *   2) 通过 SSE 跟 Agent 内核对话，把事件流画成「对话 + 工具动作 + 界面」的课堂
 *
 * 设计原则
 *   - 零依赖：不引入任何框架；KaTeX 有就用（公式更漂亮），没有就退化成代码样式
 *   - 样式自带且加 `au-` 前缀：塞进任何宿主页面都不会污染对方样式
 *   - 白名单渲染：只认得词汇表里的组件；不认识的降级成占位块，绝不白屏
 *   - 交互就地完成：小测判分、滑块联动、参数重绘全在本地，不往返服务器
 *
 * 用法（见 client/agent.html）：
 *   const app = AgentUI.mountChat(document.getElementById('root'), {base:'http://127.0.0.1:5001'});
 *   app.send('讲一下酸碱盐');
 * ========================================================================== */
(function () {
  'use strict';

  const AgentUI = { version: '0.17.0' };
  const HTML_NS = 'http://www.w3.org/1999/xhtml';

  /* ==========================================================================
   * 一、基础工具
   * ======================================================================== */
  function esc(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function el(tag, cls, html) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function hasKaTeX() {
    return typeof window.katex !== 'undefined' && window.katex && window.katex.renderToString;
  }

  function renderMath(tex, display) {
    if (hasKaTeX()) {
      try {
        return window.katex.renderToString(tex, {
          displayMode: !!display, throwOnError: false, output: 'html',
        });
      } catch (e) { /* 落到下面 */ }
    }
    return '<code class="au-tex">' + esc(tex) + '</code>';
  }

  /** 行内富文本：**粗体**、`代码`、==高亮==、$公式$、[文字](链接) */
  function fmt(text) {
    if (text == null) return '';
    let s = String(text);
    const maths = [];
    // 先摘出公式（避免被 HTML 转义破坏反斜杠与小于号）。
    // 模型会用四种写法，必须都认：$$...$$、\[...\]、$...$、\(...\)，
    // 另外还会漏掉定界符只写 \begin{aligned}...\end{aligned}（实测踩过：
    // 学生看到的是 `\[ \text{HCl} + \text{NaOH} ... \]` 这样的原始代码）。
    const push = function (tex, display) {
      maths.push({ tex: tex, display: display });
      return '\u0000M' + (maths.length - 1) + '\u0000';
    };
    s = s.replace(/\$\$([\s\S]+?)\$\$/g, function (m, tex) { return push(tex, true); });
    s = s.replace(/\\\[([\s\S]+?)\\\]/g, function (m, tex) { return push(tex, true); });
    s = s.replace(/\\\(([\s\S]+?)\\\)/g, function (m, tex) { return push(tex, false); });
    s = s.replace(/\$([^$\n]+?)\$/g, function (m, tex) { return push(tex, false); });
    // 只有环境、没有定界符的情况（成对出现才处理，避免误伤）
    s = s.replace(/\\begin\{(aligned|align|array|matrix|pmatrix|bmatrix|cases|gathered)\}[\s\S]*?\\end\{\1\}/g,
      function (m) { return push(m, true); });
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/==([^=]+)==/g, '<mark>$1</mark>');
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
    s = s.replace(/\u0000M(\d+)\u0000/g, function (m, i) {
      const item = maths[+i];
      return item ? renderMath(item.tex, item.display) : '';
    });
    return s;
  }

  /**
   * 块级 Markdown 渲染器（老师回复里天然会长这样：标题 + 列表 + 表格 + 代码块）。
   *
   * 为什么必须自己写：聊天窗一开始只按空行分段，于是 `### 第1课：…` 的井号、
   * `- 要点` 的短横线全都原样显示给学生（用户实测截图反馈过）。
   * 模型是按 Markdown 写作的，渲染端不认块级语法 = 天天露出原始符号。
   *
   * 支持：标题 #~####、无序/有序列表、表格、引用、分割线、代码围栏、段落。
   * 行内（加粗/行内码/公式/链接/高亮）交给 fmt()。
   * **代码围栏先摘出来占位**，否则里面的 # - | 会被当成语法解析坏。
   */
  function fmtBlock(text) {
    const src = String(text == null ? '' : text).replace(/\r\n?/g, '\n');
    if (!src.trim()) return '';

    // 1) 先把 ``` 代码块摘成占位符（含语言标记），避免块级语法误伤代码内容
    const codes = [];
    const guarded = src.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g, function (m, lang, code) {
      codes.push({ lang: lang || '', code: code.replace(/\n$/, '') });
      return '\u0000C' + (codes.length - 1) + '\u0000';
    });

    const lines = guarded.split('\n');
    const out = [];
    let para = [];
    let list = null;          // {ordered:bool, items:[]}
    let table = null;         // {head:[], rows:[]}
    let quote = [];

    const flushPara = function () {
      if (!para.length) return;
      out.push('<p class="au-md-p">' + para.map(fmt).join('<br>') + '</p>');
      para = [];
    };
    const flushList = function () {
      if (!list) return;
      const tag = list.ordered ? 'ol' : 'ul';
      out.push('<' + tag + ' class="au-md-list">' + list.items.map(function (it) {
        return '<li>' + fmt(it) + '</li>';
      }).join('') + '</' + tag + '>');
      list = null;
    };
    const flushTable = function () {
      if (!table) return;
      const head = table.head.map(function (c) { return '<th>' + fmt(c) + '</th>'; }).join('');
      const body = table.rows.map(function (row) {
        return '<tr>' + row.map(function (c) { return '<td>' + fmt(c) + '</td>'; }).join('') + '</tr>';
      }).join('');
      out.push(
        '<div class="au-md-table"><table>' +
        (head ? '<thead><tr>' + head + '</tr></thead>' : '') +
        '<tbody>' + body + '</tbody></table></div>'
      );
      table = null;
    };
    const flushQuote = function () {
      if (!quote.length) return;
      out.push('<blockquote>' + quote.map(fmt).join('<br>') + '</blockquote>');
      quote = [];
    };
    const flushAll = function () { flushPara(); flushList(); flushTable(); flushQuote(); };

    const splitRow = function (line) {
      return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '')
        .split('|').map(function (c) { return c.trim(); });
    };
    const isTableSep = function (line) { return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.indexOf('-') >= 0; };

    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      const line = raw.trim();

      // 代码块占位符独占一行
      if (/^\u0000C\d+\u0000$/.test(line)) {
        flushAll();
        const item = codes[+line.replace(/[^\d]/g, '')];
        if (item) {
          out.push('<pre class="au-md-code"><code>' + esc(item.code) + '</code></pre>');
        }
        continue;
      }
      if (!line) { flushAll(); continue; }

      // 分割线
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
        flushAll();
        out.push('<hr class="au-md-hr">');
        continue;
      }
      // 标题
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushAll();
        const level = Math.min(heading[1].length + 1, 6);     // # → h2（h1 留给页面标题）
        out.push('<h' + level + ' class="au-md-h' + level + '">' + fmt(heading[2]) + '</h' + level + '>');
        continue;
      }
      // 表格
      if (line.indexOf('|') >= 0) {
        const cells = splitRow(line);
        if (isTableSep(line)) {
          continue;                                            // 分隔行跳过
        }
        if (table) {
          table.rows.push(cells);
        } else {
          const next = (lines[i + 1] || '').trim();
          if (next.indexOf('|') >= 0 && isTableSep(next)) {
            flushPara(); flushList(); flushQuote();
            table = { head: cells, rows: [] };
            i++;                                               // 吃掉分隔行
          } else if (table === null && cells.length > 1 && lines[i + 1] && isTableSep((lines[i + 1] || '').trim())) {
            continue;
          } else {
            flushAll();
            para.push(line);
          }
        }
        continue;
      }
      if (table) flushTable();

      // 引用
      if (/^>\s?/.test(line)) {
        flushPara(); flushList();
        quote.push(line.replace(/^>\s?/, ''));
        continue;
      }
      if (quote.length) flushQuote();

      // 列表（无序 - * + ；有序 1. / 1) / 一、）
      const ul = line.match(/^[-*+]\s+(.*)$/);
      const ol = line.match(/^(\d{1,2})[.)、]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        const ordered = !!ol;
        if (!list || list.ordered !== ordered) { flushList(); list = { ordered: ordered, items: [] }; }
        list.items.push(ul ? ul[1] : ol[2]);
        continue;
      }
      if (list) flushList();

      // 普通段落（连续行合成一段，用 <br> 换行）
      para.push(line);
    }
    flushAll();
    return out.join('');
  }

  /* ==========================================================================
   * 二、表达式求值器（图形计算器 / 参数探索用）
   * --------------------------------------------------------------------------
   * 自己写一个而不用 eval：eval 能跑任意代码，模型给的表达式不该有那种权力。
   * ======================================================================== */
  const FUNCS = {
    sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos,
    atan: Math.atan, atan2: Math.atan2, sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh,
    sqrt: Math.sqrt, cbrt: Math.cbrt, abs: Math.abs, exp: Math.exp, log: Math.log,
    ln: Math.log, log10: Math.log10, log2: Math.log2, floor: Math.floor, ceil: Math.ceil,
    round: Math.round, sign: Math.sign, pow: Math.pow, min: Math.min, max: Math.max,
    hypot: Math.hypot, degrees: function (r) { return r * 180 / Math.PI; },
    radians: function (d) { return d * Math.PI / 180; },
  };
  const CONSTS = { pi: Math.PI, e: Math.E, tau: Math.PI * 2 };

  /* ========================================================================
   * 输入规整：把「课本 / 聊天正文里常见的数学写法」翻成解析器认的 ASCII
   * ------------------------------------------------------------------------
   * 实测（2026-10-20）：学生抄一句 `y=x²` 进来，界面直接报「不认识的字符：²」；
   * `x³`、`sin²(x)`、`sin(30°)`、`sin(πx)`、`√x`、`x×2`、全角 `（）` 全部画不出来 ——
   * 而这些正是课本、聊天、Word 里的标准写法。所以**先规整再解析**，
   * 但**界面上显示的原样保留**（学生敲进去什么就是什么，不偷偷改他的公式）。
   * ====================================================================== */
  const SUP_DIGITS = { '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
                       '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9' };

  function supToDigits(seq) {
    return seq.split('').map(function (c) { return SUP_DIGITS[c] != null ? SUP_DIGITS[c] : c; }).join('');
  }

  /** sin²(x) / cos³(2x) → sin(x)^2 / cos(2x)^3（要配平括号，所以不能用一个正则搞定） */
  function fixFnSuperscripts(s) {
    const RE = /([A-Za-z_][A-Za-z_0-9]*)\s*([⁰¹²³⁴⁵⁶⁷⁸⁹]+)\s*\(/g;
    let out = '', last = 0, m;
    while ((m = RE.exec(s))) {
      const open = m.index + m[0].length - 1;            // 指向那个 '('
      let depth = 0, i = open;
      for (; i < s.length; i++) {
        if (s[i] === '(') depth++;
        else if (s[i] === ')') { depth--; if (depth === 0) break; }
      }
      if (i >= s.length) break;                          // 括号不配对：交给解析器去报错
      out += s.slice(last, m.index) + m[1] + '(' + s.slice(open + 1, i) + ')^' + supToDigits(m[2]);
      last = i + 1;
      RE.lastIndex = last;
    }
    return out + s.slice(last);
  }

  function normalizeMathText(src) {
    let s = String(src == null ? '' : src);
    // 1) 全角 → 半角（（），＝＋－＊／ 以及全角数字/字母）
    s = s.replace(/[\uFF01-\uFF5E]/g, function (ch) {
      return String.fromCharCode(ch.charCodeAt(0) - 0xFEE0);
    });
    s = s.replace(/[\u3000\u00A0]/g, ' ');
    // 2) 排印符号 → 运算符号
    s = s.replace(/[−–—‒]/g, '-').replace(/[×·⋅∗]/g, '*').replace(/[÷∕]/g, '/');
    s = s.replace(/≤/g, '<=').replace(/≥/g, '>=').replace(/≠/g, '!=');
    // 3) 根号：√x / √(x+1) / 3√x
    s = s.replace(/√\s*(\([^()]*\)|[A-Za-z_][A-Za-z_0-9.]*|[0-9.]+)/g, 'sqrt($1)');
    s = s.replace(/∛\s*(\([^()]*\)|[A-Za-z_][A-Za-z_0-9.]*|[0-9.]+)/g, 'cbrt($1)');
    // 4) 度数：30° / sin(30°) → 换成弧度（解析器只认弧度）
    s = s.replace(/([0-9]+(?:\.[0-9]+)?)\s*°/g, '($1*pi/180)');
    s = s.replace(/°/g, '');
    // 5) π → pi，并补上隐式乘号（πx 不能粘成 pix）
    s = s.replace(/π/g, 'pi').replace(/τ/g, 'tau');
    s = s.replace(/(pi|tau)(?=[A-Za-z0-9_])/g, '$1*');
    // 6) Python 风格的幂、绝对值
    s = s.replace(/\*\*/g, '^');
    s = s.replace(/\|([^|]+)\|/g, 'abs($1)');
    // 7) 函数带指数、剩下的上标
    s = fixFnSuperscripts(s);
    s = s.replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]+/g, function (m) { return '^' + supToDigits(m); });
    return s;
  }

  function tokenizeExpr(src) {
    // ★ 不能先 `.replace(/\s+/g,'')`：那会把 `sin x` 粘成 `sinx`，
    //   于是它被当成一个参数（滑块叫 sinx），画出静默错误的结果。
    //   现在逐个跳过空白，`sin x` 会拆成两个标识符 → 下面会被判成「函数要带括号」。
    const s = String(src);
    const tokens = [];
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
      if (/\s/.test(ch)) { i++; continue; }
      if (/[0-9.]/.test(ch)) {
        let num = '';
        while (i < s.length && /[0-9.eE]/.test(s[i])) {
          if ((s[i] === 'e' || s[i] === 'E') && !/[0-9]/.test(s[i + 1] || '') && s[i + 1] !== '-' && s[i + 1] !== '+') break;
          if ((s[i] === 'e' || s[i] === 'E') && /[+-]/.test(s[i + 1] || '')) { num += s[i] + s[i + 1]; i += 2; continue; }
          num += s[i]; i++;
        }
        tokens.push({ t: 'num', v: parseFloat(num) });
        continue;
      }
      if (/[a-zA-Z_]/.test(ch)) {
        let name = '';
        while (i < s.length && /[a-zA-Z_0-9]/.test(s[i])) { name += s[i]; i++; }
        tokens.push({ t: 'id', v: name });
        continue;
      }
      if ('+-*/^%(),'.indexOf(ch) >= 0) { tokens.push({ t: ch }); i++; continue; }
      throw new Error('表达式里有不认识的字符：' + ch +
        '（只认 ASCII 写法：指数用 ^，乘除用 * /，π 写 pi，根号用 sqrt()，度数用弧度或 30°）');
    }
    return tokens;
  }

  function parseExpr(src) {
    const tokens = tokenizeExpr(src);
    let pos = 0;
    const peek = function () { return tokens[pos]; };
    const eat = function (t) {
      if (!tokens[pos] || tokens[pos].t !== t) {
        throw new Error('表达式语法错误：期望 ' + t + '，实际 ' + (tokens[pos] ? tokens[pos].t : '结尾'));
      }
      return tokens[pos++];
    };

    function parsePrimary() {
      const tok = peek();
      if (!tok) throw new Error('表达式意外结束');
      if (tok.t === 'num') { pos++; return { k: 'num', v: tok.v }; }
      if (tok.t === 'id') {
        pos++;
        const name = tok.v;
        if (peek() && peek().t === '(') {
          pos++;
          const args = [];
          if (peek() && peek().t !== ')') {
            args.push(parseAdd());
            while (peek() && peek().t === ',') { pos++; args.push(parseAdd()); }
          }
          eat(')');
          if (!FUNCS[name]) throw new Error('不支持的函数：' + name);
          return { k: 'call', name: name, args: args };
        }
        return { k: 'var', name: name };
      }
      if (tok.t === '(') { pos++; const inner = parseAdd(); eat(')'); return inner; }
      if (tok.t === '-') { pos++; return { k: 'neg', v: parsePrimary() }; }
      if (tok.t === '+') { pos++; return parsePrimary(); }
      throw new Error('表达式语法错误：' + tok.t);
    }

    function parsePow() {
      let base = parsePrimary();
      if (peek() && peek().t === '^') { pos++; return { k: 'bin', op: '^', a: base, b: parsePow() }; }
      return base;
    }
    function parseMul() {
      let node = parsePow();
      while (peek() && (peek().t === '*' || peek().t === '/' || peek().t === '%')) {
        const op = tokens[pos++].t;
        node = { k: 'bin', op: op, a: node, b: parsePow() };
      }
      return node;
    }
    function parseAdd() {
      let node = parseMul();
      while (peek() && (peek().t === '+' || peek().t === '-')) {
        const op = tokens[pos++].t;
        node = { k: 'bin', op: op, a: node, b: parseMul() };
      }
      return node;
    }

    const ast = parseAdd();
    if (pos < tokens.length) {
      const t = tokens[pos];
      const name = t.t === 'id' ? t.v : '';
      // 人话提示：函数名后面直接跟了东西（`sin x`）→ 告诉他加括号；
      // 其余情况给出实际看到的字符，别只甩一个 token 类型（"id" 谁也看不懂）。
      if (name && FUNCS[name]) {
        throw new Error('「' + name + '」是函数，要带括号写：' + name + '(x)'
          + '（如果要除，写 ' + name + '(x)/x）');
      }
      throw new Error('表达式有多余内容：「' + (name || t.t) + '」—— 检查是不是漏了运算符（乘号、括号）');
    }
    return ast;
  }

  function evalAst(node, env) {
    switch (node.k) {
      case 'num': return node.v;
      case 'var':
        if (node.name in env) return env[node.name];
        if (node.name in CONSTS) return CONSTS[node.name];
        throw new Error('未知变量：' + node.name);
      case 'neg': return -evalAst(node.v, env);
      case 'bin': {
        const a = evalAst(node.a, env), b = evalAst(node.b, env);
        switch (node.op) {
          case '+': return a + b;
          case '-': return a - b;
          case '*': return a * b;
          case '/': return a / b;
          case '%': return a % b;
          case '^': return Math.pow(a, b);
        }
        throw new Error('未知运算符 ' + node.op);
      }
      case 'call': {
        const fn = FUNCS[node.name];
        return fn.apply(null, node.args.map(function (a) { return evalAst(a, env); }));
      }
      default: throw new Error('未知节点 ' + node.k);
    }
  }

  /** 编译表达式；隐式乘法（2x、3sin(x)、xy）也帮它补上。
      模型常顺手写成 `y=x^2`，所以行首的 `y =` / `f(x) =` 先剥掉。 */
  function compileExpr(src) {
    let text = String(src).trim();
    text = text.replace(/^(y|f\s*\(\s*x\s*\))\s*=\s*/i, '');
    // 「两个名字并排」不是数学表达式：tokenizer 会把空格吃掉，`import os` 变成变量
    // importos，然后**静默画出一条空曲线**（最难查的那种失败）。这里先拦下来。
    const words = text.split(/\s+/).filter(Boolean);
    for (let i = 0; i + 1 < words.length; i++) {
      const a = words[i], b = words[i + 1];
      if (/^[a-zA-Z_]\w*$/.test(a) && /^[a-zA-Z_]\w*$/.test(b) &&
          !(a in FUNCS) && !(a in CONSTS) && !(b in FUNCS) && !(b in CONSTS)) {
        throw new Error('看不懂 `' + a + ' ' + b + '`：两个名字不能并排（乘起来要写 `' + a + '*' + b + '`）');
      }
    }
    text = text.replace(/(\d)\s*([a-zA-Z(])/g, '$1*$2')
      .replace(/\)\s*(\d|[a-zA-Z(])/g, ')*$1')
      // `xy` 就是 x·y（Desmos 也这么认）。只在**全是 x/y** 时才拆，
      // 免得把 sin、atan2、speed 这类多字母名字拆坏（参数请用单个字母）。
      .replace(/(^|[^a-zA-Z0-9_.])([xy]{2,})(?![a-zA-Z0-9_(])/g, function (m0, pre, letters) {
        return pre + letters.split('').join('*');
      });
    const ast = parseExpr(text);
    return {
      ast: ast,
      src: text,
      eval: function (env) { return evalAst(ast, env || {}); },
    };
  }

  /* ==========================================================================
   * 二·B、图形计算器的公式引擎（一行一个公式，类型自动识别）
   * --------------------------------------------------------------------------
   * 目标：**模型只写公式就能画，学生还能自己改公式**。所以这里做的不是
   * 「解析一条 y=f(x)」，而是像 Desmos 那样看一行文本判断它是什么：
   *     x^2+y^2=16      隐式方程（圆）    → 等值线
   *     y<=x+1          不等式           → 区域填色 + 边界线
   *     y=x^2-2x-3      显函数           → 曲线
   *     x=3             竖直线
   *     (2,3)           点（可带标签）
   *     "圆心 O"        文字标注（也认 Desmos 的 ("文字",(0,4)) 写法）
   *     a=2             参数 → 自动变成滑块
   * 全程不碰 eval：tokenizer → AST → 自己求值，模型给的东西没有执行代码的权力。
   * ======================================================================== */
  const CALC_COLORS = ['#0067c0', '#0f7b0f', '#c239b3', '#d83b01', '#5c2d91', '#0d7d80', '#8a6d00', '#b4005e'];

  /** 在括号/引号之外找最外层运算符（= < > <= >=），按出现顺序返回 */
  function findTopLevelOps(src) {
    const ops = [];
    let depth = 0, quote = null;
    for (let i = 0; i < src.length; i++) {
      const ch = src[i];
      if (quote) { if (ch === quote) quote = null; continue; }
      if (ch === '"' || ch === "'") { quote = ch; continue; }
      if (ch === '(' || ch === '[' || ch === '{') { depth++; continue; }
      if (ch === ')' || ch === ']' || ch === '}') { depth--; continue; }
      if (depth !== 0) continue;
      if (ch === '<' || ch === '>') {
        const two = src[i + 1] === '=';
        ops.push({ op: two ? ch + '=' : ch, at: i });
        if (two) i++;
      } else if (ch === '=' && src[i + 1] !== '=' && '=<>'.indexOf(src[i - 1] || '') < 0) {
        ops.push({ op: '=', at: i });
      }
    }
    return ops;
  }

  /** 顶层按逗号切分（括号里的逗号不算）：`(1,2)` → ['1','2'] */
  function splitTopLevelCommas(src) {
    const out = [];
    let depth = 0, quote = null, cur = '';
    for (let i = 0; i < src.length; i++) {
      const ch = src[i];
      if (quote) { cur += ch; if (ch === quote) quote = null; continue; }
      if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
      if (ch === '(' || ch === '[') depth++;
      if (ch === ')' || ch === ']') depth--;
      if (ch === ',' && depth === 0) { out.push(cur); cur = ''; continue; }
      cur += ch;
    }
    out.push(cur);
    return out;
  }

  /** 收集 AST 里用到的名字（用来找参数：既不是 x/y、也不是内置常数的单字母） */
  function collectVars(ast, out) {
    out = out || {};
    if (!ast || typeof ast !== 'object') return out;
    if (ast.k === 'var') { out[ast.name] = true; return out; }
    if (ast.k === 'neg') return collectVars(ast.v, out);
    if (ast.k === 'bin') { collectVars(ast.a, out); return collectVars(ast.b, out); }
    if (ast.k === 'call') { (ast.args || []).forEach(function (a) { collectVars(a, out); }); return out; }
    return out;
  }

  /** 看清一个 AST 用了什么：有没有 x、有没有 y、哪些是参数（要变滑块的） */
  function varsOf(ast) {
    const all = collectVars(ast, {});
    const params = {};
    Object.keys(all).forEach(function (k) {
      if (k !== 'x' && k !== 'y' && !(k in CONSTS)) params[k] = true;
    });
    return { hasX: !!all.x, hasY: !!all.y, params: params };
  }

  function tryCompile(src) {
    try { return { fn: compileExpr(src) }; } catch (err) { return { err: err.message }; }
  }

  /** 把 `(1,2)` `(a, 2a)` 这种点拆成两个可求值的表达式 */
  function splitPoint(src) {
    const s = String(src).trim();
    if (s[0] !== '(' || s[s.length - 1] !== ')') return null;
    const parts = splitTopLevelCommas(s.slice(1, -1));
    if (parts.length !== 2) return null;
    const a = tryCompile(parts[0]), b = tryCompile(parts[1]);
    if (!a.fn || !b.fn) return null;
    return { x: a.fn, y: b.fn };
  }

  /**
   * 判断一行公式是什么。返回 {kind, ...}：
   * empty / text / point / constant / explicit / vertical / implicit / ineqY / ineqXY / error
   */
  /* 包一层：拦掉「把函数当变量用」的写法。
     实测：`y=sin x` 旧版本**不报错**，而是把 sin 当成一个参数（滑块叫 sin），
     画出 y=sin·x —— 静默画错比报错更糟。 */
  function classifyRow(raw) {
    const row = classifyRowInner(raw);
    if (row && !row.err && row.params) {
      const bad = Object.keys(row.params).filter(function (k) { return FUNCS[k] || CONSTS[k]; });
      if (bad.length) {
        row.kind = 'error';
        row.err = '「' + bad[0] + '」是函数，要带括号写：' + bad[0] + '(x)'
          + '（相除要写除号，比如 sin(x)/x）';
      }
    }
    // 函数名后面没括号（sin x）时，解析器只会甩一句「多余内容：x」——换成能照做的提示
    if (row && row.err && row.kind !== 'text' && row.kind !== 'empty') {
      const txt = normalizeMathText(raw);
      const bad = txt.match(/(?:^|[^A-Za-z_0-9])(sin|cos|tan|asin|acos|atan|sinh|cosh|tanh|sqrt|cbrt|abs|exp|ln|log10|log2|log|hypot)(?![A-Za-z_0-9]*\s*\()/);
      if (bad) {
        row.err = '「' + bad[1] + '」是函数，要带括号写：' + bad[1] + '(x)'
          + '（乘除写在后面，比如 ' + bad[1] + '(x)/2）';
      }
    }
    return row;
  }

  function classifyRowInner(raw) {
    const shown = String(raw == null ? '' : raw).trim();       // 显示/回填用学生原样
    if (!shown) return { kind: 'empty', raw: shown };
    const row = { kind: 'error', raw: shown };

    // —— 文字：("文字",(1,2)) 或 "文字" 或 "文字" @ (1,2) ——
    //   用原样匹配：规整不该改标签里的文字
    let m = shown.match(/^\(\s*(["'])([\s\S]*?)\1\s*(?:,\s*\(([^)]*)\)\s*)?\)$/);
    if (!m) m = shown.match(/^(["'])([\s\S]*?)\1\s*(?:@\s*\(?([^)]*?)\)?)?$/);
    if (m) {
      row.kind = 'text';
      row.text = m[2];
      if (m[3] && m[3].trim()) {
        const at = splitTopLevelCommas(m[3]).map(function (t) { return tryCompile(t).fn; });
        if (at.length === 2 && at[0] && at[1]) row.at = at;
      }
      return row;
    }
    if (/^["']/.test(shown)) { row.kind = 'text'; row.text = shown.replace(/^["']|["']$/g, ''); return row; }

    // 数学部分用规整后的文本（x² → x^2、30° → 弧度、全角括号 → 半角 …）
    const src = normalizeMathText(shown).trim();
    const ops = findTopLevelOps(src);
    const op = ops.length ? ops[0] : null;

    // —— 没有运算符：要么是点 (2,3)，要么就是 y = 这一行 ——
    if (!op) {
      const pt = splitPoint(src);
      if (pt) {
        const vx = varsOf(pt.x.ast), vy = varsOf(pt.y.ast);
        row.kind = 'point';
        row.x = pt.x;
        row.y = pt.y;
        row.params = mergeParams(mergeParams({}, vx.params), vy.params);
        return row;
      }
      const r = tryCompile(src);
      if (!r.fn) { row.err = r.err; return row; }
      const v0 = varsOf(r.fn.ast);
      // 只写了左边（如 `x^2+y^2-16`）也当方程看：等于 0 的那条线。
      // 模型和学生都经常这么写，直接报错太苛刻了。
      row.kind = v0.hasY ? 'implicit' : 'explicit';
      row.c = r.fn;
      row.params = v0.params;
      return row;
    }

    const lhs = src.slice(0, op.at).trim();
    const rhs = src.slice(op.at + op.op.length).trim();
    if (!lhs || !rhs) { row.err = '运算符两边都要有内容'; return row; }

    if (op.op === '=') {
      // f(x) = ... 也认（跟 y = ... 等价）
      if (/^[a-zA-Z]\w*\s*\(\s*x\s*\)$/.test(lhs)) {
        const r = tryCompile(rhs);
        if (!r.fn) { row.err = r.err; return row; }
        const vf = varsOf(r.fn.ast);
        if (!vf.hasY) { row.kind = 'explicit'; row.c = r.fn; row.params = vf.params; return row; }
      }
      // 参数定义：a = 2（左边单个字母，右边不含 x/y）→ 变成滑块
      if (/^[a-zA-Z]\w*$/.test(lhs) && lhs !== 'x' && lhs !== 'y') {
        const r = tryCompile(rhs);
        if (r.fn) {
          const v = varsOf(r.fn.ast);
          if (!v.hasX && !v.hasY) {
            row.kind = 'constant';
            row.name = lhs;
            row.c = r.fn;
            row.params = v.params;
            return row;
          }
        }
      }
      if (lhs === 'y') {
        const r = tryCompile(rhs);
        if (!r.fn) { row.err = r.err; return row; }
        const v = varsOf(r.fn.ast);
        if (!v.hasY) { row.kind = 'explicit'; row.c = r.fn; row.params = v.params; return row; }
      }
      const r = tryCompile('(' + lhs + ')-(' + rhs + ')');
      if (!r.fn) { row.err = r.err; return row; }
      const v = varsOf(r.fn.ast);
      if (!v.hasX && !v.hasY) {
        row.err = '这一行看不出画什么（定义参数请写单字母，如 a=2）';
        return row;
      }
      row.c = r.fn;
      row.params = v.params;
      row.kind = v.hasY ? 'implicit' : 'vertical';       // 只有 x → 竖直线
      return row;
    }

    // 不等式：y < f(x) 这种按「逐列填色」画（边缘干净，也最常用）
    if (lhs === 'y') {
      const r = tryCompile(rhs);
      if (!r.fn) { row.err = r.err; return row; }
      const v = varsOf(r.fn.ast);
      if (!v.hasY) { row.kind = 'ineqY'; row.op = op.op; row.c = r.fn; row.params = v.params; return row; }
    }
    const r = tryCompile('(' + lhs + ')-(' + rhs + ')');
    if (!r.fn) { row.err = r.err; return row; }
    row.kind = 'ineqXY';
    row.op = op.op;
    row.c = r.fn;
    row.params = varsOf(r.fn.ast).params;
    return row;
  }

  function mergeParams(dst, src) {
    Object.keys(src || {}).forEach(function (k) { dst[k] = true; });
    return dst;
  }

  function fmtNum(v, decimals) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    if (decimals != null && isFinite(decimals)) return fmtTick(v, decimals);
    const r = Number(v.toFixed(4));
    return String(r);
  }

  /* ==========================================================================
   * 三、Canvas 绘图（图表 + 函数图像）
   * ======================================================================== */
  function setupCanvas(canvas, cssHeight) {
    const dpr = window.devicePixelRatio || 1;
    // clientWidth 为 0 说明还没完成布局（组件刚插进 DOM）——用父容器宽度兜底。
    // 但仅仅兜底不够：真实宽度要在布局完成后按真实像素重建画布，
    // 否则就是一张被 CSS 拉伸的糊图（用户反馈「图形计算器看着很奇怪」就包含这个）。
    const parentW = canvas.parentElement ? canvas.parentElement.clientWidth : 0;
    const width = canvas.clientWidth || parentW || 480;
    const height = cssHeight || canvas.clientHeight || 240;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx: ctx, w: width, h: height };
  }

  /** 容器尺寸变化时重画（画布必须按真实像素重建，否则会拉伸变形） */
  function autoResize(box, redraw) {
    let lastW = box.clientWidth || 0;
    const onResize = function () {
      if (Math.abs(box.clientWidth - lastW) < 2) return;
      lastW = box.clientWidth;
      redraw();
    };
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(onResize);
      ro.observe(box);
    } else {
      window.addEventListener('resize', onResize);
    }
  }

  function niceTicks(min, max, count) {
    const span = max - min || 1;
    const raw = span / (count || 5);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 0.001; v += step) {
      out.push(Number(v.toFixed(10)));
    }
    return out;
  }

  /**
   * 建立「数学坐标 → 屏幕像素」映射。
   *
   * equalAspect=true 时 x、y 用**同一个比例**缩放 —— 这是图形计算器的铁律：
   * 两轴分开拉伸的话，圆会变成椭圆、正方形会变成长方形、正弦波会被压扁。
   * （用户实测撞上了这个："我画的圆变成了椭圆""函数图像拉升得太过了"。）
   * 等比映射时实际可视范围会比请求范围更大（居中扩展），这是正确行为。
   */
  function makeMapper(w, h, xr, yr, pad, equalAspect) {
    pad = pad || { l: 46, r: 18, t: 16, b: 30 };
    const availW = Math.max(10, w - pad.l - pad.r);
    const availH = Math.max(10, h - pad.t - pad.b);
    const cx = (xr[0] + xr[1]) / 2;
    const cy = (yr[0] + yr[1]) / 2;
    let xUnit, yUnit, x2p, y2p, xOfPx, yOfPx;

    if (equalAspect) {
      const s = Math.min(availW / (xr[1] - xr[0]), availH / (yr[1] - yr[0]));
      xUnit = yUnit = s;
      const ox = pad.l + availW / 2, oy = pad.t + availH / 2;
      x2p = function (x) { return ox + (x - cx) * s; };
      y2p = function (y) { return oy - (y - cy) * s; };
      xOfPx = function (px) { return cx + (px - ox) / s; };
      yOfPx = function (py) { return cy - (py - oy) / s; };
    } else {
      xUnit = availW / (xr[1] - xr[0]);
      yUnit = availH / (yr[1] - yr[0]);
      x2p = function (x) { return pad.l + (x - xr[0]) * xUnit; };
      y2p = function (y) { return h - pad.b - (y - yr[0]) * yUnit; };
      xOfPx = function (px) { return xr[0] + (px - pad.l) / xUnit; };
      yOfPx = function (py) { return yr[0] + (h - pad.b - py) / yUnit; };
    }

    const visX = [xOfPx(pad.l), xOfPx(pad.l + availW)];
    const visY = [yOfPx(h - pad.b), yOfPx(pad.t)];
    return {
      x2p: x2p, y2p: y2p, xOfPx: xOfPx, yOfPx: yOfPx,
      pad: pad, scale: xUnit, xUnit: xUnit, yUnit: yUnit,
      xr: visX, yr: visY, availW: availW, availH: availH, equal: !!equalAspect,
    };
  }

  /** 选一个「屏幕间距合适」的格子边长（数学单位），让网格看起来像方格纸 */
  function gridStep(unit, minPx) {
    let step = 1;
    const safe = unit > 0 ? unit : 1;
    let guard = 0;
    while (safe * step < (minPx || 30) && guard++ < 40) step *= (step < 2 ? 2 : 2.5);
    guard = 0;
    while (safe * step > 110 && guard++ < 40) step /= 2;
    return step;
  }

  /** 画网格 + 坐标轴 + 刻度数字（用 map 的实际可视范围，等比时也正确）
      opts.grid === false 时只画坐标轴（图形计算器可以让用户关掉网格） */
  function drawGrid(ctx, w, h, map, opts) {
    const pad = map.pad;
    const xr = map.xr, yr = map.yr;
    const step = gridStep(Math.min(map.xUnit, map.yUnit), 30);
    ctx.save();
    ctx.lineWidth = 1;
    const x0 = Math.ceil(xr[0] / step) * step;
    const y0 = Math.ceil(yr[0] / step) * step;
    if (!opts || opts.grid !== false) {
      // 细网格（1/5 格）+ 主网格，看起来才像方格纸
      const fine = step / 5;
      ctx.strokeStyle = 'rgba(0,0,0,.045)';
      ctx.beginPath();
      for (let x = Math.ceil(xr[0] / fine) * fine; x <= xr[1] + 1e-9; x += fine) {
        const px = Math.round(map.x2p(x)) + 0.5;
        ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b);
      }
      for (let y = Math.ceil(yr[0] / fine) * fine; y <= yr[1] + 1e-9; y += fine) {
        const py = Math.round(map.y2p(y)) + 0.5;
        ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py);
      }
      ctx.stroke();
      ctx.strokeStyle = 'rgba(0,0,0,.10)';
      ctx.beginPath();
      for (let x = x0; x <= xr[1] + 1e-9; x += step) {
        const px = Math.round(map.x2p(x)) + 0.5;
        ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b);
      }
      for (let y = y0; y <= yr[1] + 1e-9; y += step) {
        const py = Math.round(map.y2p(y)) + 0.5;
        ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py);
      }
      ctx.stroke();
    }

    // 坐标轴（在可视范围内才画）
    ctx.strokeStyle = 'rgba(0,0,0,.42)';
    ctx.lineWidth = 1.4;
    if (yr[0] <= 0 && yr[1] >= 0) {
      const py = Math.round(map.y2p(0)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py); ctx.stroke();
    }
    if (xr[0] <= 0 && xr[1] >= 0) {
      const px = Math.round(map.x2p(0)) + 0.5;
      ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b); ctx.stroke();
    }

    // 刻度数字：只在轴附近标，避免刷屏
    const dec = (opts && opts.decimals != null) ? opts.decimals : null;   // 精确度（小数位）
    ctx.fillStyle = 'rgba(0,0,0,.5)';
    ctx.font = '11px "Segoe UI",system-ui,sans-serif';
    const axisY = (yr[0] <= 0 && yr[1] >= 0) ? Math.min(h - pad.b - 4, Math.max(pad.t + 12, map.y2p(0) + 13)) : h - pad.b + 13;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let x = x0; x <= xr[1] + 1e-9; x += step) {
      if (Math.abs(x) < 1e-9 && yr[0] <= 0 && yr[1] >= 0) continue;
      ctx.fillText(fmtTick(x, dec), map.x2p(x), axisY);
    }
    const axisX = (xr[0] <= 0 && xr[1] >= 0) ? Math.max(pad.l + 4, Math.min(w - pad.r - 18, map.x2p(0) - 6)) : pad.l - 6;
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let y = y0; y <= yr[1] + 1e-9; y += step) {
      if (Math.abs(y) < 1e-9) continue;
      ctx.fillText(fmtTick(y, dec), axisX, map.y2p(y));
    }
    ctx.restore();
  }

  /**
   * 刻度/读数怎么显示。
   * decimals == null → 自动（按大小决定，最多 6 位有效小数；适合「好看」）
   * decimals 是数字 → **最多保留这么多个小数位**，末尾的 0 去掉
   *   （1.50 → 1.5，2.00 → 2）。这是「精确度」调低时的形态：
   *   老师想把坐标读成整数或一两位小数时用得上。
   */
  function fmtTick(v, decimals) {
    if (v == null || typeof v !== 'number' || !isFinite(v)) return '—';
    if (decimals != null && isFinite(decimals)) {
      const d = Math.max(0, Math.min(8, Math.round(decimals)));
      let s = v.toFixed(d);
      if (d > 0 && s.indexOf('.') >= 0) s = s.replace(/0+$/, '').replace(/\.$/, '');
      if (s === '-0') s = '0';
      return s;
    }
    const rounded = Number(v.toFixed(6));
    if (Math.abs(rounded) < 1e-9) return '0';
    if (Math.abs(rounded) >= 1000 || (Math.abs(rounded) < 0.01 && rounded !== 0)) {
      return rounded.toExponential(1).replace('e+', 'e');
    }
    return String(rounded);
  }

  /** 画函数曲线（带裁剪，跑出视野的部分不画到坐标轴外面） */
  function drawCurves(ctx, map, w, h, series, compiled, samplesPerUnit) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(map.pad.l, map.pad.t, map.availW, map.availH);
    ctx.clip();
    series.forEach(function (s, si) {
      const c = compiled[si];
      if (!c) return;
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2.2;
      ctx.lineJoin = 'round';
      ctx.beginPath();
      const N = Math.max(240, Math.min(2000, Math.round(samplesPerUnit || 600)));
      const xr = map.xr;
      let started = false;
      for (let i = 0; i <= N; i++) {
        const x = xr[0] + (xr[1] - xr[0]) * i / N;
        let y;
        try { y = c.eval(Object.assign({ x: x }, s.params)); } catch (err) { y = NaN; }
        if (typeof y !== 'number' || !isFinite(y)) { started = false; continue; }
        const px = map.x2p(x), py = map.y2p(y);
        // 竖直渐近线（如 1/x）也要断开，否则会出现一条假的竖线
        if (py < map.pad.t - map.availH * 2 || py > h - map.pad.b + map.availH * 2) { started = false; continue; }
        if (!started) { ctx.moveTo(px, py); started = true; } else { ctx.lineTo(px, py); }
      }
      ctx.stroke();
    });
    ctx.restore();
  }

  /** 采样成一张「值网格」：隐式方程 / 不等式靠它画。NaN = 该点算不出来 */
  function valueGrid(map, fn, env, step) {
    const cols = Math.max(6, Math.floor(map.availW / (step || 3)));
    const rows = Math.max(6, Math.floor(map.availH / (step || 3)));
    const g = new Float64Array((cols + 1) * (rows + 1));
    const dx = map.availW / cols, dy = map.availH / rows;
    const e = Object.assign({}, env);
    for (let j = 0; j <= rows; j++) {
      e.y = map.yOfPx(map.pad.t + j * dy);
      for (let i = 0; i <= cols; i++) {
        e.x = map.xOfPx(map.pad.l + i * dx);
        let v;
        try { v = fn.eval(e); } catch (err) { v = NaN; }
        g[j * (cols + 1) + i] = (typeof v === 'number' && isFinite(v)) ? v : NaN;
      }
    }
    return { g: g, cols: cols, rows: rows, dx: dx, dy: dy };
  }

  /** 等值线（marching squares）：画 f(x,y)=0 那条线 —— 圆、椭圆、隐函数都靠它。
      没有它就只能画 y=f(x)，圆和 x^2+y^2=16 这种一律画不出来。 */
  function strokeContour(ctx, map, grid, color, width) {
    const g = grid.g, cols = grid.cols, rows = grid.rows, dx = grid.dx, dy = grid.dy;
    const px = function (i) { return map.pad.l + i * dx; };
    const py = function (j) { return map.pad.t + j * dy; };
    const lerp = function (va, vb) { const t = va / (va - vb); return isFinite(t) ? Math.max(0, Math.min(1, t)) : 0.5; };
    // 16 种符号组合 → 该连哪两条边。角点编号 v0=左上 v1=右上 v2=右下 v3=左下；
    // 边编号 0=上 1=右 2=下 3=左。规则只有一个：**连「符号发生变化的那两条边」**。
    // 连错了不会报错，只是曲线变成一串断开的点（实测：圆画成了虚线，查了半天）。
    // 自查办法：互补的两种组合（如 3 与 12、6 与 9）必须给出同一组边。
    const TABLE = {
      1: [2, 3], 2: [1, 2], 3: [1, 3], 4: [0, 1], 5: [0, 1, 2, 3], 6: [0, 2], 7: [0, 3],
      8: [0, 3], 9: [0, 2], 10: [0, 3, 1, 2], 11: [0, 1], 12: [1, 3], 13: [1, 2], 14: [2, 3],
    };
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 2.2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols; i++) {
        const i00 = j * (cols + 1) + i, i10 = i00 + 1, i01 = i00 + cols + 1, i11 = i01 + 1;
        const v00 = g[i00], v10 = g[i10], v01 = g[i01], v11 = g[i11];
        if (!isFinite(v00) || !isFinite(v10) || !isFinite(v01) || !isFinite(v11)) continue;
        const idx = (v00 > 0 ? 8 : 0) | (v10 > 0 ? 4 : 0) | (v11 > 0 ? 2 : 0) | (v01 > 0 ? 1 : 0);
        const seg = TABLE[idx];
        if (!seg) continue;
        const pts = [
          [px(i) + dx * lerp(v00, v10), py(j)],          // 0 上边
          [px(i + 1), py(j) + dy * lerp(v10, v11)],      // 1 右边
          [px(i) + dx * lerp(v01, v11), py(j + 1)],      // 2 下边
          [px(i), py(j) + dy * lerp(v00, v01)],          // 3 左边
        ];
        for (let k = 0; k + 1 < seg.length; k += 2) {
          ctx.moveTo(pts[seg[k]][0], pts[seg[k]][1]);
          ctx.lineTo(pts[seg[k + 1]][0], pts[seg[k + 1]][1]);
        }
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  function cmpOf(op) {
    if (op === '<') return function (v) { return v < 0; };
    if (op === '<=') return function (v) { return v <= 0; };
    if (op === '>') return function (v) { return v > 0; };
    return function (v) { return v >= 0; };
  }

  /** 区域填色（隐式不等式，如 x^2+y^2<=4）：按行合并成条带，比逐格好画也快 */
  function shadeGrid(ctx, map, grid, test, color) {
    const g = grid.g, cols = grid.cols, rows = grid.rows, dx = grid.dx, dy = grid.dy;
    ctx.save();
    ctx.globalAlpha = 0.17;
    ctx.fillStyle = color;
    for (let j = 0; j < rows; j++) {
      let run = -1;
      for (let i = 0; i <= cols; i++) {
        const v = i < cols ? g[j * (cols + 1) + i] : NaN;
        const inside = isFinite(v) && test(v);
        if (inside && run < 0) run = i;
        if (!inside && run >= 0) {
          ctx.fillRect(map.pad.l + run * dx, map.pad.t + j * dy, (i - run) * dx + 0.7, dy + 0.7);
          run = -1;
        }
      }
    }
    ctx.restore();
  }

  /** y < f(x) 这种按列填色：边缘跟曲线严丝合缝（比格子法好看得多） */
  function shadeUnderCurve(ctx, map, fn, env, color, above) {
    const N = Math.max(120, Math.round(map.availW / 2));
    const wpx = map.availW / N;
    const top0 = map.pad.t, bot0 = map.pad.t + map.availH;
    const e = Object.assign({}, env);
    ctx.save();
    ctx.globalAlpha = 0.17;
    ctx.fillStyle = color;
    for (let i = 0; i < N; i++) {
      const px = map.pad.l + i * wpx;
      e.x = map.xOfPx(px);
      let y;
      try { y = fn.eval(e); } catch (err) { y = NaN; }
      if (typeof y !== 'number' || !isFinite(y)) continue;
      const py = Math.max(top0 - 1, Math.min(bot0 + 1, map.y2p(y)));
      const top = above ? top0 : py;
      const bot = above ? py : bot0;
      if (bot > top) ctx.fillRect(px, top, wpx + 0.8, bot - top);
    }
    ctx.restore();
  }

  /** 单条显函数曲线（带回折断开，1/x 那种不会连出一条假竖线）
      density 是「画质」：采样点密度倍率，越高曲线越顺滑（也越费时间） */
  function drawOneCurve(ctx, map, fn, env, color, width, density) {
    const N = Math.max(360, Math.min(6000, Math.round(map.availW * 1.4 * (density || 1))));
    const e = Object.assign({}, env);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 2.2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    let started = false, prevPy = null;
    for (let i = 0; i <= N; i++) {
      const x = map.xr[0] + (map.xr[1] - map.xr[0]) * i / N;
      e.x = x;
      let y;
      try { y = fn.eval(e); } catch (err) { y = NaN; }
      if (typeof y !== 'number' || !isFinite(y)) { started = false; prevPy = null; continue; }
      const px = map.x2p(x), py = map.y2p(y);
      if (py < map.pad.t - map.availH * 3 || py > map.pad.t + map.availH * 4) { started = false; prevPy = null; continue; }
      if (started && prevPy !== null && Math.abs(py - prevPy) > map.availH * 1.6) started = false;   // 渐近线断开
      if (!started) { ctx.moveTo(px, py); started = true; } else { ctx.lineTo(px, py); }
      prevPy = py;
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawPlotPoint(ctx, map, x, y, color, label) {
    const px = map.x2p(x), py = map.y2p(y);
    if (!isFinite(px) || !isFinite(py)) return;
    ctx.save();
    ctx.beginPath();
    ctx.arc(px, py, 4.6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = 'rgba(255,255,255,.95)';
    ctx.stroke();
    if (label) {
      ctx.font = '12.5px "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif';
      ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
      ctx.lineWidth = 3.2;
      ctx.strokeStyle = 'rgba(255,255,255,.92)';
      ctx.strokeText(label, px + 7, py - 5);
      ctx.fillStyle = color;
      ctx.fillText(label, px + 7, py - 5);
    }
    ctx.restore();
  }

  /** 图上的文字注记（白描边，压在网格线上也读得清） */
  function drawPlotText(ctx, map, x, y, text, color) {
    const px = map.x2p(x), py = map.y2p(y);
    if (!isFinite(px) || !isFinite(py) || !text) return;
    ctx.save();
    ctx.font = '13px "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.lineWidth = 3.4;
    ctx.strokeStyle = 'rgba(255,255,255,.92)';
    ctx.strokeText(String(text), px, py);
    ctx.fillStyle = color || '#1b1b1b';
    ctx.fillText(String(text), px, py);
    ctx.restore();
  }

  function drawBars(canvas, data, horizontal) {
    const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : 240);
    const ctx = view.ctx, w = view.w, h = view.h;
    const values = data.map(function (d) { return +d.value || 0; });
    const maxV = Math.max.apply(null, values.concat([0]));
    const minV = Math.min.apply(null, values.concat([0]));
    const pad = { l: 48, r: 16, t: 14, b: 34 };
    if (horizontal) {
      const labelW = 96;
      const barH = Math.max(10, (h - pad.t - pad.b) / data.length - 8);
      const maxLabel = maxV || 1;
      data.forEach(function (d, i) {
        const y = pad.t + i * (barH + 8);
        ctx.fillStyle = 'rgba(128,138,158,0.9)';
        ctx.font = '12px system-ui, sans-serif';
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        ctx.fillText(String(d.label == null ? '' : d.label).slice(0, 12), labelW - 8, y + barH / 2);
        const width = Math.max(1, (+d.value || 0) / maxLabel * (w - labelW - 60));
        ctx.fillStyle = d.color || 'rgba(96,165,250,0.85)';
        ctx.fillRect(labelW, y, width, barH);
        ctx.fillStyle = 'rgba(128,138,158,1)';
        ctx.textAlign = 'left';
        ctx.fillText(String(d.value), labelW + width + 6, y + barH / 2);
      });
      return;
    }
    const base = h - pad.b;
    const scale = function (v) {
      const span = (maxV - minV) || 1;
      return base - (v - minV) / span * (h - pad.t - pad.b);
    };
    const slot = (w - pad.l - pad.r) / Math.max(1, data.length);
    const barW = Math.min(56, slot * 0.62);
    ctx.font = '11px system-ui, sans-serif';
    data.forEach(function (d, i) {
      const cx = pad.l + slot * i + slot / 2;
      const y0 = scale(0), y1 = scale(+d.value || 0);
      ctx.fillStyle = d.color || 'rgba(96,165,250,0.85)';
      ctx.fillRect(cx - barW / 2, Math.min(y0, y1), barW, Math.abs(y1 - y0) || 1);
      ctx.fillStyle = 'rgba(226,232,240,0.95)';
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ctx.fillText(String(d.value), cx, Math.min(y0, y1) - 3);
      ctx.fillStyle = 'rgba(148,163,184,1)';
      ctx.textBaseline = 'top';
      ctx.fillText(String(d.label == null ? '' : d.label).slice(0, 10), cx, base + 6);
    });
  }

  function drawLine(canvas, series, labels) {
    const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : 240);
    const ctx = view.ctx, w = view.w, h = view.h;
    const all = [];
    series.forEach(function (s) { (s.data || []).forEach(function (v) { all.push(+v || 0); }); });
    let maxV = Math.max.apply(null, all.concat([0]));
    let minV = Math.min.apply(null, all.concat([0]));
    const span = (maxV - minV) || 1;
    maxV += span * 0.08; minV -= span * 0.08;
    const pad = { l: 48, r: 16, t: 14, b: 30 };
    const x2p = function (i, n) { return pad.l + (n <= 1 ? 0 : i / (n - 1)) * (w - pad.l - pad.r); };
    const y2p = function (v) { return h - pad.b - (v - minV) / (maxV - minV) * (h - pad.t - pad.b); };

    ctx.strokeStyle = 'rgba(128,138,158,0.22)';
    ctx.fillStyle = 'rgba(148,163,184,1)';
    ctx.font = '11px system-ui, sans-serif';
    niceTicks(minV, maxV, 5).forEach(function (v) {
      const py = y2p(v);
      ctx.beginPath(); ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py); ctx.stroke();
      ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
      ctx.fillText(String(v), pad.l - 6, py);
    });

    const colors = ['#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa'];
    series.forEach(function (s, si) {
      const pts = (s.data || []).map(function (v, i) { return [x2p(i, s.data.length), y2p(+v || 0)]; });
      if (!pts.length) return;
      const color = s.color || colors[si % colors.length];
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      pts.forEach(function (p, i) { i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); });
      ctx.stroke();
      ctx.lineTo(pts[pts.length - 1][0], h - pad.b);
      ctx.lineTo(pts[0][0], h - pad.b);
      ctx.closePath();
      ctx.globalAlpha = 0.12; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
    });

    if (labels && labels.length) {
      ctx.fillStyle = 'rgba(148,163,184,1)';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      const step = Math.ceil(labels.length / 8);
      labels.forEach(function (lb, i) {
        if (i % step) return;
        ctx.fillText(String(lb).slice(0, 8), x2p(i, labels.length), h - pad.b + 5);
      });
    }
    // 图例
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    series.forEach(function (s, si) {
      if (!s.label || series.length < 2) return;
      const color = s.color || colors[si % colors.length];
      ctx.fillStyle = color;
      ctx.fillRect(pad.l + 4, pad.t + si * 15, 10, 3);
      ctx.fillStyle = 'rgba(203,213,225,0.95)';
      ctx.fillText(String(s.label), pad.l + 20, pad.t + si * 15 - 5);
    });
  }

  function drawDonut(canvas, data) {
    const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : 240);
    const ctx = view.ctx, w = view.w, h = view.h;
    const total = data.reduce(function (a, d) { return a + (+d.value || 0); }, 0) || 1;
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 8, r = R * 0.58;
    const colors = ['#60a5fa', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#22d3ee'];
    let angle = -Math.PI / 2;
    data.forEach(function (d, i) {
      const slice = (+d.value || 0) / total * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, R, angle, angle + slice);
      ctx.arc(cx, cy, r, angle + slice, angle, true);
      ctx.closePath();
      ctx.fillStyle = d.color || colors[i % colors.length];
      ctx.fill();
      angle += slice;
    });
    ctx.fillStyle = 'rgba(226,232,240,0.95)';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = '13px system-ui, sans-serif';
    ctx.fillText(total.toFixed(total % 1 ? 1 : 0), cx, cy);
  }

  /* ==========================================================================
   * 四、组件渲染
   * ======================================================================== */
  const RENDERERS = {};

  function children(parent, nodes) {
    (nodes || []).forEach(function (n) { parent.appendChild(renderNode(n)); });
    return parent;
  }

  function renderNode(node) {
    if (node == null) return el('span');
    if (typeof node === 'string') return el('p', 'au-p', fmt(node));
    if (!node.type) return el('div', 'au-unknown', fmt(JSON.stringify(node)));
    const fn = RENDERERS[node.type];
    if (!fn) {
      return el('div', 'au-unknown',
        '<span class="au-unknown-tag">' + esc(node.type) + '</span> 这个组件这个版本还没实现' +
        (node.title ? '：' + esc(node.title) : ''));
    }
    try {
      return fn(node);
    } catch (err) {
      return el('div', 'au-error', '组件 ' + esc(node.type) + ' 渲染失败：' + esc(err.message));
    }
  }

  RENDERERS.text = function (n) {
    const sizes = { h1: 'au-h1', h2: 'au-h2', h3: 'au-h3', body: 'au-p', muted: 'au-muted', caption: 'au-caption' };
    const tag = n.size === 'h1' ? 'h1' : n.size === 'h2' ? 'h2' : n.size === 'h3' ? 'h3' : 'div';
    const raw = String(n.content == null ? '' : n.content);
    // 多行或含块级语法的内容走 Markdown 渲染（否则 `### 标题`、`- 要点` 会原样露出来）
    const looksBlock = /\n/.test(raw) || /^\s*(#{1,6}\s|[-*+]\s|\d{1,2}[.)、]\s|>|\||```)/m.test(raw);
    const node = el(tag, sizes[n.size] || 'au-p', looksBlock ? fmtBlock(raw) : fmt(raw));
    if (n.center) node.style.textAlign = 'center';
    return node;
  };

  RENDERERS.list = function (n) {
    const ul = el('ul', 'au-list');
    (n.items || []).forEach(function (item) {
      if (item && typeof item === 'object') {
        if (item.type) { const li = el('li'); li.appendChild(renderNode(item)); ul.appendChild(li); return; }
        const li = el('li');
        li.innerHTML = '<strong>' + fmt(item.title) + '</strong>' +
          (item.desc ? '<span class="au-desc">' + fmt(item.desc) + '</span>' : '');
        ul.appendChild(li);
      } else {
        ul.appendChild(el('li', null, fmt(item)));
      }
    });
    return ul;
  };

  RENDERERS.keyvalue = function (n) {
    const box = el('div', 'au-kv');
    (n.pairs || []).forEach(function (p) {
      box.appendChild(el('div', 'au-kv-key', fmt(p.key)));
      box.appendChild(el('div', 'au-kv-val', fmt(p.value)));
    });
    return box;
  };

  RENDERERS.table = function (n) {
    const wrap = el('div', 'au-table-wrap');
    const table = el('table', 'au-table');
    const thead = el('thead');
    const tr = el('tr');
    (n.columns || []).forEach(function (c) { tr.appendChild(el('th', null, fmt(c))); });
    thead.appendChild(tr); table.appendChild(thead);
    const tbody = el('tbody');
    (n.rows || []).forEach(function (row) {
      const r = el('tr');
      (row || []).forEach(function (cell, i) {
        const td = el('td');
        const type = (n.types || [])[i];
        if (type === 'num') { td.className = 'au-num'; td.textContent = String(cell); }
        else if (type === 'badge') { td.innerHTML = '<span class="au-badge">' + fmt(cell) + '</span>'; }
        else if (type === 'bar') {
          const v = Math.max(0, Math.min(100, Number(cell) || 0));
          td.innerHTML = '<span class="au-bar"><i style="width:' + v + '%"></i></span>' + v + '%';
        } else { td.innerHTML = fmt(cell); }
        r.appendChild(td);
      });
      tbody.appendChild(r);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  };

  RENDERERS.steps = function (n) {
    const ol = el('ol', 'au-steps');
    (n.steps || []).forEach(function (s, i) {
      const li = el('li', i + 1 === (n.current || -1) ? 'au-step au-step-active' : 'au-step');
      li.innerHTML = '<span class="au-step-no">' + (i + 1) + '</span>' +
        '<span class="au-step-body"><strong>' + fmt(s.title) + '</strong>' +
        (s.desc ? '<span class="au-desc">' + fmt(s.desc) + '</span>' : '') + '</span>';
      ol.appendChild(li);
    });
    return ol;
  };

  RENDERERS.timeline = function (n) {
    const box = el('div', 'au-timeline');
    (n.items || []).forEach(function (it) {
      const row = el('div', 'au-tl-item');
      row.innerHTML = '<span class="au-tl-dot"></span>' +
        '<div class="au-tl-body">' + (it.time ? '<span class="au-tl-time">' + fmt(it.time) + '</span>' : '') +
        '<strong>' + fmt(it.title) + '</strong>' +
        (it.desc ? '<span class="au-desc">' + fmt(it.desc) + '</span>' : '') + '</div>';
      box.appendChild(row);
    });
    return box;
  };

  RENDERERS.callout = function (n) {
    const tone = ['info', 'success', 'warning', 'error'].indexOf(n.tone) >= 0 ? n.tone : 'info';
    const box = el('div', 'au-callout au-' + tone);
    box.innerHTML = (n.title ? '<div class="au-callout-title">' + fmt(n.title) + '</div>' : '') +
      '<div class="au-callout-body">' + fmtBlock(n.content) + '</div>';
    return box;
  };

  RENDERERS.stat = function (n) {
    const box = el('div', 'au-stat');
    box.innerHTML = '<div class="au-stat-label">' + fmt(n.label) + '</div>' +
      '<div class="au-stat-value">' + fmt(n.value) + '</div>' +
      (n.delta ? '<div class="au-stat-delta">' + fmt(n.delta) + '</div>' : '');
    return box;
  };

  RENDERERS.progress = function (n) {
    const v = Math.max(0, Math.min(100, Number(n.value) || 0));
    const box = el('div', 'au-progress');
    box.innerHTML = '<div class="au-progress-head"><span>' + fmt(n.label || '') + '</span><span>' +
      (n.valueLabel ? fmt(n.valueLabel) : v + '%') + '</span></div>' +
      '<div class="au-progress-track"><i style="width:' + v + '%"></i></div>';
    return box;
  };

  RENDERERS.badge = function (n) {
    const tone = n.tone || 'accent';
    return el('span', 'au-badge au-badge-' + tone, (n.icon ? esc(n.icon) + ' ' : '') + fmt(n.label));
  };

  RENDERERS.code = function (n) {
    const box = el('div', 'au-code');
    const head = el('div', 'au-code-head');
    head.innerHTML = '<span>' + esc(n.lang || n.title || 'code') + '</span>';
    const copy = el('button', 'au-btn au-btn-mini', '复制');
    copy.onclick = function () {
      navigator.clipboard && navigator.clipboard.writeText(n.code || '');
      copy.textContent = '已复制';
      setTimeout(function () { copy.textContent = '复制'; }, 1200);
    };
    head.appendChild(copy);
    const pre = el('pre');
    pre.appendChild(el('code', null, esc(n.code || '')));
    box.appendChild(head); box.appendChild(pre);
    return box;
  };

  RENDERERS.chart = function (n) {
    const box = el('div', 'au-chart');
    if (n.title) box.appendChild(el('div', 'au-chart-title', fmt(n.title)));
    const canvas = el('canvas', 'au-canvas');
    canvas.dataset.height = String(n.height || 240);
    box.appendChild(canvas);
    const draw = function () {
      if (n.kind === 'donut') drawDonut(canvas, n.data || []);
      else if (n.kind === 'line') {
        const series = n.series && n.series.length ? n.series : [{ label: n.title || '', data: (n.data || []).map(function (d) { return d.value; }) }];
        const labels = (n.data || []).map(function (d) { return d.label; });
        drawLine(canvas, series, labels);
      } else drawBars(canvas, n.data || [], !!n.horizontal);
    };
    box._redraw = draw;
    box._setHeight = function (h) {
      // 图表不高清重画就是一张被拉伸的糊图，所以改的是画布的真实像素高度再重画
      canvas.dataset.height = String(h ? Math.max(160, h - 60) : (n.height || 240));
      draw();
    };
    setTimeout(draw, 0);
    return box;
  };

  /* ------------------------------------------------------------------------
   * 图形计算器（plot）
   * 布局参考 Desmos：**左边一栏专门放表达式与常数**（每条曲线一行，颜色标识 +
   * 公式排版 + 该曲线的参数滑块），右边是画布。拖动滑块图像实时重画。
   * 坐标必须是**等比**的（见 makeMapper 的说明），否则圆会变椭圆。
   * ---------------------------------------------------------------------- */
  RENDERERS.plot = function (n) {
    const box = el('div', 'au-plot');
    const canvasWrap = el('div', 'au-plot-canvas');
    const panel = el('div', 'au-plot-panel');

    const canvas = el('canvas', 'au-canvas');
    canvas.dataset.height = String(n.height || 380);
    canvasWrap.appendChild(canvas);

    const colors = ['#0067c0', '#0f7b0f', '#c239b3', '#d83b01', '#5c2d91'];
    const series = (n.series || []).map(function (s, si) {
      return {
        label: s.label,
        color: s.color || colors[si % colors.length],
        expr: String(s.expr || ''),
        params: (function () {
          const p = {};
          (s.params || []).forEach(function (item) { p[item.name] = Number(item.value != null ? item.value : 1); });
          return p;
        })(),
        paramDefs: s.params || [],
      };
    });

    if (n.title) panel.appendChild(el('div', 'au-plot-title', fmt(n.title)));

    // 表达式列表：每条曲线一行（颜色块 + 公式），参数滑块挂在自己那条下面
    const exprList = el('div', 'au-expr-list');
    const readouts = [];
    series.forEach(function (s, si) {
      const row = el('div', 'au-expr');
      const head = el('div', 'au-expr-head');
      head.innerHTML = '<i style="background:' + esc(s.color) + '"></i>' +
        '<span class="au-expr-formula">y = ' + fmt('$' + (s.expr || 'x') + '$') + '</span>';
      if (s.label) head.appendChild(el('span', 'au-expr-label', esc(s.label)));
      row.appendChild(head);
      s.paramDefs.forEach(function (p) {
        const line = el('div', 'au-const');
        const nameEl = el('span', 'au-const-name', esc(p.name) + ' =');
        const input = document.createElement('input');
        input.type = 'range';
        input.min = String(p.min != null ? p.min : -5);
        input.max = String(p.max != null ? p.max : 5);
        input.step = String(p.step || 0.1);
        input.value = String(s.params[p.name]);
        const out = el('b', 'au-const-val', String(s.params[p.name]));
        input.style.accentColor = s.color;
        input.oninput = function () {
          s.params[p.name] = Number(input.value);
          out.textContent = String(input.value);
          draw();
        };
        readouts.push({ series: si, name: p.name, el: out });
        line.appendChild(nameEl); line.appendChild(input); line.appendChild(out);
        row.appendChild(line);
      });
      exprList.appendChild(row);
    });
    panel.appendChild(exprList);

    // 视野信息 + 缩放/重置（真正的图形计算器都让人能自己调视野）
    const viewInfo = el('div', 'au-view-info');
    const tools = el('div', 'au-plot-tools');
    const zoomIn = el('button', 'au-btn au-btn-mini', '放大');
    const zoomOut = el('button', 'au-btn au-btn-mini', '缩小');
    const reset = el('button', 'au-btn au-btn-mini', '重置视野');
    tools.appendChild(reset); tools.appendChild(zoomOut); tools.appendChild(zoomIn);
    panel.appendChild(viewInfo);
    panel.appendChild(tools);

    box.appendChild(panel);
    box.appendChild(canvasWrap);

    // ---- 视野（等比）：xRange 为初始请求范围，yRange 由「等比 + 数据范围」共同决定 ----
    const reqX = [Number(n.xMin != null ? n.xMin : -10), Number(n.xMax != null ? n.xMax : 10)];
    let frame = null;          // { xr, yr }
    let compiled = [];
    const PAD = { l: 44, r: 16, t: 16, b: 28 };

    function compileAll() {
      compiled = series.map(function (s) {
        try { return compileExpr(s.expr || 'x'); } catch (err) { return null; }
      });
    }

    /** 按数据范围自动取景，保证等比且内容不出框 */
    function autoFrame(w, h) {
      const availW = Math.max(10, w - PAD.l - PAD.r);
      const availH = Math.max(10, h - PAD.t - PAD.b);
      const k = availH / availW;                       // 等比要求 ySpan = xSpan * k
      let lo = Infinity, hi = -Infinity;
      const N = 300;
      for (let i = 0; i <= N; i++) {
        const x = reqX[0] + (reqX[1] - reqX[0]) * i / N;
        compiled.forEach(function (c, si) {
          if (!c) return;
          try {
            const y = c.eval(Object.assign({ x: x }, series[si].params));
            if (typeof y === 'number' && isFinite(y)) { lo = Math.min(lo, y); hi = Math.max(hi, y); }
          } catch (err) { /* 跳过非法点 */ }
        });
      }
      if (!isFinite(lo) || !isFinite(hi)) { lo = -5; hi = 5; }
      if (hi - lo < 1e-6) { const m = (hi + lo) / 2; lo = m - 1; hi = m + 1; }
      const yMid = (lo + hi) / 2;
      let xSpan = reqX[1] - reqX[0];
      let ySpan = (hi - lo) * 1.18;                    // 留点边距
      if (ySpan > xSpan * k) xSpan = ySpan / k;        // 数据很高 → 横向扩视野，保持等比
      ySpan = xSpan * k;
      const xMid = (reqX[0] + reqX[1]) / 2;
      frame = { xr: [xMid - xSpan / 2, xMid + xSpan / 2], yr: [yMid - ySpan / 2, yMid + ySpan / 2] };
    }

    let lastScale = 0;          // 记住「每单位多少像素」，用于"窗口变大就多看一点"

    function draw() {
      const height = canvas.dataset.height ? +canvas.dataset.height : 380;
      const view = setupCanvas(canvas, height);
      compileAll();
      if (!frame) autoFrame(view.w, view.h);
      const map = makeMapper(view.w, view.h, frame.xr, frame.yr, PAD, true);
      frame = { xr: map.xr, yr: map.yr };              // 用等比后的真实可视范围回写
      lastScale = map.scale;                           // 供 _setHeight 保持比例用
      drawGrid(view.ctx, view.w, view.h, map);
      drawCurves(view.ctx, map, view.w, view.h, series, compiled, map.availW);
      viewInfo.textContent = '视野 x ∈ [' + fmtTick(map.xr[0]) + ', ' + fmtTick(map.xr[1]) + ']　y ∈ [' +
        fmtTick(map.yr[0]) + ', ' + fmtTick(map.yr[1]) + ']';
    }

    function zoom(factor) {
      if (!frame) return;
      const cx = (frame.xr[0] + frame.xr[1]) / 2;
      const cy = (frame.yr[0] + frame.yr[1]) / 2;
      const sx = (frame.xr[1] - frame.xr[0]) / 2 * factor;
      const sy = (frame.yr[1] - frame.yr[0]) / 2 * factor;
      frame = { xr: [cx - sx, cx + sx], yr: [cy - sy, cy + sy] };
      draw();
    }
    zoomIn.onclick = function () { zoom(0.75); };
    zoomOut.onclick = function () { zoom(1 / 0.75); };
    reset.onclick = function () { frame = null; draw(); };

    // 拖拽平移（像真计算器那样按住图就能挪）
    let drag = null;
    canvas.style.cursor = 'grab';
    canvas.addEventListener('mousedown', function (e) {
      if (!frame) return;
      drag = { x: e.clientX, y: e.clientY, frame: { xr: frame.xr.slice(), yr: frame.yr.slice() } };
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!drag) return;
      const view = { w: canvas.clientWidth || 480, h: canvas.dataset.height ? +canvas.dataset.height : 380 };
      const dx = (e.clientX - drag.x) / Math.max(1, view.w - PAD.l - PAD.r) * (drag.frame.xr[1] - drag.frame.xr[0]);
      const dy = (e.clientY - drag.y) / Math.max(1, view.h - PAD.t - PAD.b) * (drag.frame.yr[1] - drag.frame.yr[0]);
      frame = {
        xr: [drag.frame.xr[0] - dx, drag.frame.xr[1] - dx],
        yr: [drag.frame.yr[0] + dy, drag.frame.yr[1] + dy],
      };
      draw();
    });
    window.addEventListener('mouseup', function () {
      if (!drag) return;
      drag = null;
      canvas.style.cursor = 'grab';
    });
    canvas.addEventListener('wheel', function (e) {
      if (!frame) return;
      e.preventDefault();
      // 以鼠标位置为中心缩放，手感才跟真计算器一致
      const rect = canvas.getBoundingClientRect();
      const map = makeMapper(canvas.clientWidth || 480, canvas.dataset.height ? +canvas.dataset.height : 380,
        frame.xr, frame.yr, PAD, true);
      const mx = map.xOfPx(e.clientX - rect.left);
      const my = map.yOfPx(e.clientY - rect.top);
      const f = e.deltaY > 0 ? 1.12 : 1 / 1.12;
      frame = {
        xr: [mx + (frame.xr[0] - mx) * f, mx + (frame.xr[1] - mx) * f],
        yr: [my + (frame.yr[0] - my) * f, my + (frame.yr[1] - my) * f],
      };
      draw();
    }, { passive: false });

    setTimeout(draw, 0);
    autoResize(box, function () { frame = null; draw(); });
    box._redraw = draw;
    // 窗口变大 = **看到更多**，而不是把图整体放大。
    // 之前按新尺寸重新取景，等比映射为了塞满画布会把比例放大 ——
    // 用户看到的现象就是「图被整体拉伸/往右边撑开」（实测反馈）。
    // 现在保持「每单位多少像素」不变，只把可视范围向四周扩出去。
    box._setHeight = function (h) {
      const newH = h ? Math.max(160, h - 60) : (n.height || 380);
      canvas.dataset.height = String(newH);
      if (frame && lastScale > 0) {
        const availW = Math.max(80, (canvas.clientWidth || 600) - PAD.l - PAD.r);
        const availH = Math.max(80, newH - PAD.t - PAD.b);
        const cx = (frame.xr[0] + frame.xr[1]) / 2;
        const cy = (frame.yr[0] + frame.yr[1]) / 2;
        const halfW = availW / lastScale / 2;
        const halfH = availH / lastScale / 2;
        frame = { xr: [cx - halfW, cx + halfW], yr: [cy - halfH, cy + halfH] };
      }
      draw();
    };
    return box;
  };

  /* ------------------------------------------------------------------------
   * 图形计算器（calculator）—— 模型只写公式，学生自己也能改公式
   * ------------------------------------------------------------------------
   * 和 plot 的分工：plot 是「一次性的函数图像」（只读），calculator 是**一台真的
   * 计算器**：左边是可编辑的公式表，右边是画布；公式是唯一的事实来源，
   * 用户敲进去、删掉、改颜色、隐藏、拖滑块，全都当场重画，不经过模型。
   *
   * 公式怎么写（模型和学生写的是同一套语法）：
   *   y = x^2 - 2x - 3      显函数
   *   x^2 + y^2 = 16        隐式方程（圆/椭圆）→ 等值线
   *   y <= x + 1            不等式 → 区域填色
   *   x = 3                 竖直线
   *   (2, 3)                点
   *   "圆心 O"  或  ("圆心 O", (0, 4.5))   文字注记
   *   a = 2                 参数 → 自动生成滑块（公式里写 a 就能用）
   * ---------------------------------------------------------------------- */
  RENDERERS.calculator = function (n) {
    const box = el('div', 'au-plot au-calc');
    const panel = el('div', 'au-plot-panel au-calc-panel');
    const canvasWrap = el('div', 'au-plot-canvas au-calc-wrap');
    const canvas = el('canvas', 'au-canvas');
    const overlay = el('canvas', 'au-calc-overlay');
    const H0 = Number(n.height || 420);
    canvas.dataset.height = String(H0);
    canvasWrap.appendChild(canvas);
    canvasWrap.appendChild(overlay);
    box.appendChild(panel);
    box.appendChild(canvasWrap);

    if (n.title) panel.appendChild(el('div', 'au-plot-title', fmt(n.title)));

    const listEl = el('div', 'au-calc-list');
    const addBar = el('div', 'au-calc-add');
    const paramBox = el('div', 'au-calc-params');
    // 提示与读数**分成固定两行**：合成一行时，鼠标进出画布会让下面那排按钮上下跳一格
    // （实测证据：点「画质」点不中 —— 提示 2 行、读数 1 行，光标一移到按钮上布局就变了）
    const hintEl = el('div', 'au-calc-hint', '鼠标移到图上读数；点在曲线上加点；滚轮缩放，拖动平移。');
    const coordEl = el('div', 'au-calc-coord');
    const viewInfo = el('div', 'au-view-info');
    panel.appendChild(listEl);
    panel.appendChild(addBar);
    panel.appendChild(paramBox);
    panel.appendChild(hintEl);
    panel.appendChild(coordEl);
    panel.appendChild(viewInfo);

    const PAD = { l: 46, r: 16, t: 16, b: 28 };

    // 参数（滑块）的默认范围：spec 里给了就用，否则 -5..5
    const paramSpec = {};
    function noteParam(name, spec) {
      if (!name) return;
      const cur = paramSpec[name] || {};
      paramSpec[name] = {
        value: spec && spec.value != null ? Number(spec.value) : cur.value,
        min: spec && spec.min != null ? Number(spec.min) : cur.min,
        max: spec && spec.max != null ? Number(spec.max) : cur.max,
        step: spec && spec.step != null ? Number(spec.step) : cur.step,
      };
    }
    (n.params || []).forEach(function (p) { if (p && p.name) noteParam(String(p.name), p); });

    /* ---------- 公式表：行是唯一的事实来源 ---------- */
    const rows = [];
    let rowSeq = 0;
    /** 新行优先用「还没被用过的颜色」——不然 8 行之后会和上面的曲线撞色，分不清谁是谁 */
    function pickColor() {
      const used = {};
      rows.forEach(function (r) { used[r.color] = true; });
      for (let i = 0; i < CALC_COLORS.length; i++) {
        if (!used[CALC_COLORS[i]]) return CALC_COLORS[i];
      }
      return CALC_COLORS[rows.length % CALC_COLORS.length];
    }
    function addRow(raw, extra) {
      const r = Object.assign({
        id: ++rowSeq,
        raw: raw == null ? '' : String(raw),
        color: pickColor(),
        hidden: false, label: '',
      }, extra || {});
      rows.push(r);
      return r;
    }

    // 模型给的每一条都归一化成「一行文本」——学生看得懂、也改得动
    (n.expressions || []).forEach(function (item) {
      if (item == null) return;
      if (typeof item === 'object') {
        if (item.text != null) {
          const at = (item.at && item.at.length === 2)
            ? ', (' + Number(item.at[0]) + ', ' + Number(item.at[1]) + ')' : '';
          addRow('(' + JSON.stringify(String(item.text)) + at + ')', { color: item.color, label: item.label });
          return;
        }
        if (item.point && item.point.length === 2) {
          addRow('(' + Number(item.point[0]) + ', ' + Number(item.point[1]) + ')',
            { color: item.color, label: item.label || '' });
          return;
        }
        if (item.name != null && item.value != null) {
          noteParam(String(item.name), item);
          addRow(String(item.name) + ' = ' + Number(item.value), { color: item.color });
          return;
        }
        (item.params || []).forEach(function (p) { if (p && p.name) noteParam(String(p.name), p); });
        const src = item.expr != null ? item.expr : item.formula;
        if (src != null) addRow(src, { color: item.color, label: item.label || '', hidden: !!item.hidden });
        return;
      }
      addRow(item);
    });
    (n.texts || []).forEach(function (t) {
      if (t == null) return;
      if (typeof t === 'string') { addRow('(' + JSON.stringify(t) + ')'); return; }
      const at = (t.at && t.at.length === 2)
        ? ', (' + Number(t.at[0]) + ', ' + Number(t.at[1]) + ')' : '';
      addRow('(' + JSON.stringify(String(t.text == null ? '' : t.text)) + at + ')',
        { color: t.color, label: t.label });
    });
    function ensureTrailingEmpty() {
      if (!rows.length || rows[rows.length - 1].raw.trim() !== '') addRow('');
    }
    ensureTrailingEmpty();

    function nextColor(current) {
      const i = CALC_COLORS.indexOf(current);
      return CALC_COLORS[(i + 1) % CALC_COLORS.length];
    }
    function pointLabel() {
      const used = rows.filter(function (r) { return r.label; }).length;
      return String.fromCharCode(65 + (used % 26));
    }

    function buildList() {
      listEl.innerHTML = '';
      rows.forEach(function (row, idx) {
        const wrap = el('div', 'au-calc-row');
        const dot = el('button', 'au-calc-dot');
        dot.type = 'button';
        dot.title = '换个颜色';
        dot.style.background = row.color;
        dot.onclick = function () {
          row.color = nextColor(row.color);
          dot.style.background = row.color;
          draw();
        };

        const input = document.createElement('input');
        input.className = 'au-calc-input';
        input.type = 'text';
        input.spellcheck = false;
        input.value = row.raw;
        input.placeholder = idx === rows.length - 1
          ? '输入公式：x^2+y^2=16 ／ y<=x+1 ／ (2,3) ／ "文字"'
          : '';
        input.addEventListener('input', function () { row.raw = input.value; schedule(); });
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            row.raw = input.value;
            if (idx === rows.length - 1 && row.raw.trim()) addRow('');
            buildList();
            refresh();
            focusRow(Math.min(idx + 1, rows.length - 1));
          } else if (e.key === 'Backspace' && input.value === '' && idx < rows.length - 1) {
            // 空行按退格 = 删掉这行。最后那行是「随时可输入的输入行」，不删。
            e.preventDefault();
            removeRow(row, idx - 1);
          }
        });

        const eye = el('button', 'au-mini', row.hidden ? '◌' : '👁');
        eye.type = 'button';
        eye.title = '显示 / 隐藏这条';
        eye.onclick = function () {
          row.hidden = !row.hidden;
          eye.textContent = row.hidden ? '◌' : '👁';
          syncParams();
          draw();
        };

        const del = el('button', 'au-mini', '✕');
        del.type = 'button';
        del.title = '删掉这一行';
        del.onclick = function () { removeRow(row, idx - 1); };

        const err = el('div', 'au-calc-err');
        wrap.appendChild(dot);
        wrap.appendChild(input);
        wrap.appendChild(eye);
        wrap.appendChild(del);
        wrap.appendChild(err);
        row.dom = { wrap: wrap, input: input, dot: dot, eye: eye, err: err };
        listEl.appendChild(wrap);
      });
    }

    function focusRow(i) {
      const row = rows[Math.max(0, Math.min(rows.length - 1, i))];
      if (!row || !row.dom || !row.dom.input) return;
      const inp = row.dom.input;
      // 先把这一行滚进视野再聚焦：公式一多，输入行会掉到列表可视区外面，
      // 学生就「找不到能打字的地方」（实测：点都没点上）。
      try { if (inp.scrollIntoView) inp.scrollIntoView({ block: 'nearest' }); } catch (e) { /* 桩环境 */ }
      try { inp.focus(); } catch (e) { /* 桩环境 */ }
    }
    function removeRow(row, focusIdx) {
      const i = rows.indexOf(row);
      if (i < 0) return;
      rows.splice(i, 1);
      ensureTrailingEmpty();
      buildList();
      refresh();
      focusRow(focusIdx == null ? i - 1 : focusIdx);
    }
    function addAndEdit(text) {
      const r = addRow(text);
      buildList();
      refresh();
      const idx = rows.indexOf(r);
      focusRow(idx);
      const inp = rows[idx] && rows[idx].dom && rows[idx].dom.input;
      if (inp && inp.select) { try { inp.select(); } catch (e) {} }
    }

    /* ---------- 视野与状态（必须在工具栏之前：按钮要读它们的初值） ---------- */
    const wantX = [
      Number(n.xMin != null ? n.xMin : -10),
      Number(n.xMax != null ? n.xMax : 10),
    ];
    if (!(wantX[1] > wantX[0])) { wantX[0] = -10; wantX[1] = 10; }
    const wantY = (n.yMin != null && n.yMax != null && Number(n.yMax) > Number(n.yMin))
      ? [Number(n.yMin), Number(n.yMax)] : null;
    let showGrid = n.grid !== false;
    let equalAspect = n.equalAspect !== false;     // 默认等比：圆才是圆
    // ---- 精确度（小数位）与画质（采样密度）：学生随时能调，改过就记住 ----
    const PREC_OPTS = [
      { label: '自动', value: '' },
      { label: '整数', value: '0' },
      { label: '1 位小数', value: '1' },
      { label: '2 位小数', value: '2' },
      { label: '3 位小数', value: '3' },
      { label: '4 位小数', value: '4' },
      { label: '6 位小数', value: '6' },
    ];
    const QUALITY = {
      standard: { label: '标准', step: 3.0, density: 1.0 },
      hd: { label: '高清', step: 2.0, density: 1.8 },
      ultra: { label: '超清', step: 1.4, density: 2.6 },
    };
    const QUALITY_ORDER = ['standard', 'hd', 'ultra'];
    function savedPref(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
    function savePref(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* 隐私模式 */ } }

    let decimals = null;                            // null = 自动
    if (n.decimals != null && n.decimals !== '') decimals = Number(n.decimals);
    else { const sv = savedPref('au_calc_decimals'); if (sv != null && sv !== '') decimals = Number(sv); }
    if (decimals != null && (!isFinite(decimals) || decimals < 0)) decimals = null;

    let quality = (n.quality && QUALITY[n.quality]) ? String(n.quality) : String(savedPref('au_calc_quality') || 'standard');
    if (!QUALITY[quality]) quality = 'standard';

    // 拖拽/滚轮的时候先用标准画质，停手 180ms 再按学生选的画质重画一遍 ——
    // 否则选了「超清」之后拖图会卡（每帧都要重算几万个采样点）。
    let interacting = false;
    let settleTimer = null;
    const qNow = function () { return interacting ? QUALITY.standard : QUALITY[quality]; };
    function beginInteract() { interacting = true; if (settleTimer) { clearTimeout(settleTimer); settleTimer = null; } }
    function endInteract() {
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(function () {
        settleTimer = null;
        if (!interacting) return;
        interacting = false;
        draw();
      }, 180);
    }
    let frame = null;
    let currentMap = null;
    let lastScale = 0;
    let hover = null;
    let octx = null, ovW = 1, ovH = 1;

    /* ---------- 工具栏 ---------- */
    const btnIn = el('button', 'au-btn au-btn-mini', '放大');
    const btnOut = el('button', 'au-btn au-btn-mini', '缩小');
    const btnReset = el('button', 'au-btn au-btn-mini', '重置视野');
    const btnGrid = el('button', 'au-btn au-btn-mini', '网格');
    const btnEqual = el('button', 'au-btn au-btn-mini', '等比');
    const btnFormula = el('button', 'au-btn au-btn-mini', '＋ 公式');
    const btnText = el('button', 'au-btn au-btn-mini', '＋ 文字');
    const btnPoint = el('button', 'au-btn au-btn-mini', '＋ 点');
    btnIn.onclick = function () { zoom(0.75); };
    btnOut.onclick = function () { zoom(1 / 0.75); };
    btnReset.onclick = function () { frame = null; draw(); };
    btnGrid.onclick = function () {
      showGrid = !showGrid;
      btnGrid.classList.toggle('au-on', showGrid);
      draw();
    };
    btnEqual.onclick = function () {
      equalAspect = !equalAspect;
      btnEqual.classList.toggle('au-on', equalAspect);
      draw();
    };
    btnFormula.onclick = function () { addAndEdit(''); };
    btnText.onclick = function () { addAndEdit('"在这里写字"'); };
    btnPoint.onclick = function () { addAndEdit('(0, 0)'); };
    btnGrid.classList.toggle('au-on', showGrid);
    btnEqual.classList.toggle('au-on', equalAspect);
    [btnFormula, btnText, btnPoint].forEach(function (b) { addBar.appendChild(b); });
    const tools = el('div', 'au-plot-tools');
    [btnIn, btnOut, btnReset, btnGrid, btnEqual].forEach(function (b) { tools.appendChild(b); });

    // 精确度：坐标刻度与鼠标读数保留几位小数（默认「自动」，最多 6 位）
    const precWrap = el('span', 'au-calc-prec');
    precWrap.appendChild(el('span', 'au-calc-prec-label', '精确度'));
    const precSel = document.createElement('select');
    precSel.className = 'au-calc-prec-sel';
    PREC_OPTS.forEach(function (o) {
      const op = document.createElement('option');
      op.value = o.value;
      op.textContent = o.label;
      precSel.appendChild(op);
    });
    precSel.value = decimals == null ? '' : String(decimals);
    precSel.title = '坐标刻度和鼠标读到的坐标，保留几位小数';
    precSel.onchange = function () {
      decimals = precSel.value === '' ? null : Number(precSel.value);
      savePref('au_calc_decimals', precSel.value);
      draw();
    };
    precWrap.appendChild(precSel);
    tools.appendChild(precWrap);

    // 画质：曲线/隐式方程的采样密度（越高越顺滑，也越费时间）
    const btnQuality = el('button', 'au-btn au-btn-mini');
    btnQuality.textContent = '画质 ' + QUALITY[quality].label;
    btnQuality.title = '曲线画得多细：标准 / 高清 / 超清。放大看圆和曲线时差别最明显';
    btnQuality.onclick = function () {
      const i = QUALITY_ORDER.indexOf(quality);
      quality = QUALITY_ORDER[(i + 1) % QUALITY_ORDER.length];
      savePref('au_calc_quality', quality);
      btnQuality.textContent = '画质 ' + QUALITY[quality].label;
      draw();
    };
    tools.appendChild(btnQuality);
    panel.appendChild(tools);

    const paramState = {};
    function paramValues() {
      const e = {};
      Object.keys(paramState).forEach(function (k) { e[k] = paramState[k].value; });
      return e;
    }
    function setParam(name, v) {
      const st = paramState[name];
      if (!st || typeof v !== 'number' || !isFinite(v)) return;
      st.value = v;
      st.wrap.title = name + ' = ' + fmtNum(v);
      // 有 `a = 2` 这种定义行 → 把值写回那一行（Desmos 也是这个行为）
      rows.forEach(function (row) {
        if (row.kind === 'constant' && row.name === name) {
          row.raw = name + ' = ' + fmtNum(v, decimals);
          if (row.dom && row.dom.input !== document.activeElement) row.dom.input.value = row.raw;
        }
      });
      draw();
      schedule();          // 稍后完整重算（保证行内值和滑块一致）
    }
    function ensureSlider(name, value) {
      let st = paramState[name];
      const spec = paramSpec[name] || {};
      if (!st) {
        const wrap = el('div', 'au-const');
        const nameEl = el('span', 'au-const-name', esc(name) + ' =');
        const input = document.createElement('input');
        input.type = 'range';
        input.min = String(spec.min != null ? spec.min : -5);
        input.max = String(spec.max != null ? spec.max : 5);
        if (!(Number(input.max) > Number(input.min))) { input.min = '-5'; input.max = '5'; }
        input.step = String(spec.step != null ? spec.step : 0.1);
        const num = document.createElement('input');
        num.type = 'number';
        num.className = 'au-const-num';
        num.step = input.step;
        st = paramState[name] = { value: value, wrap: wrap, input: input, num: num };
        input.addEventListener('input', function () { setParam(name, Number(input.value)); });
        num.addEventListener('input', function () { setParam(name, Number(num.value)); });
        wrap.appendChild(nameEl);
        wrap.appendChild(input);
        wrap.appendChild(num);
        paramBox.appendChild(wrap);
      }
      st.value = value;
      st.wrap.title = name + ' = ' + fmtNum(value);
      if (document.activeElement !== st.input) st.input.value = String(value);
      if (document.activeElement !== st.num) st.num.value = String(value);
    }
    function syncParams() {
      const need = {};
      rows.forEach(function (row) {
        if (row.hidden) return;
        Object.keys(row.params || {}).forEach(function (k) { need[k] = true; });
        if (row.kind === 'constant' && row.name) need[row.name] = true;
      });
      const defined = {};
      const envNow = paramValues();
      rows.forEach(function (row) {
        if (row.kind !== 'constant' || !row.name || row.hidden || !row.c) return;
        try {
          const v = row.c.eval(envNow);
          if (typeof v === 'number' && isFinite(v)) defined[row.name] = v;
        } catch (e) { /* 依赖别的参数时先跳过 */ }
      });
      Object.keys(need).forEach(function (name) {
        const spec = paramSpec[name] || {};
        let v = defined[name];
        if (v == null) v = paramState[name] ? paramState[name].value : Number(spec.value != null ? spec.value : 1);
        if (!isFinite(v)) v = 1;
        ensureSlider(name, v);
      });
      Object.keys(paramState).forEach(function (name) {
        if (need[name]) return;
        const st = paramState[name];
        if (st.wrap.parentNode) st.wrap.parentNode.removeChild(st.wrap);
        delete paramState[name];
      });
      paramBox.style.display = Object.keys(paramState).length ? '' : 'none';
    }

    /** 每一行重新判断类型（用户打字、滑块写回之后都要走这里） */
    function refresh() {
      rows.forEach(function (row) {
        const info = classifyRow(row.raw);
        row.kind = info.kind;
        row.err = info.err || '';
        row.c = info.c;
        row.x = info.x;
        row.y = info.y;
        row.at = info.at;
        row.text = info.text;
        row.op = info.op;
        row.name = info.name;
        row.params = info.params || {};
        if (row.dom) {
          const bad = row.kind === 'error';
          if (bad) row.dom.wrap.classList.add('au-bad');
          else row.dom.wrap.classList.remove('au-bad');
          if (bad) row.dom.err.textContent = row.err || '这一行看不懂';
          if (row.dom.input !== document.activeElement && row.dom.input.value !== row.raw) {
            row.dom.input.value = row.raw;
          }
        }
      });
      syncParams();
      draw();
    }
    let pending = null;
    function schedule() {
      if (pending) clearTimeout(pending);
      pending = setTimeout(function () { pending = null; refresh(); }, 140);
    }

    function initialFrame(w, h, env) {
      const availW = Math.max(10, w - PAD.l - PAD.r);
      const availH = Math.max(10, h - PAD.t - PAD.b);
      const k = availH / availW;                       // 等比要求 ySpan = xSpan * k
      const xMid = (wantX[0] + wantX[1]) / 2;
      if (wantY) return { xr: [wantX[0], wantX[1]], yr: [wantY[0], wantY[1]] };
      let lo = Infinity, hi = -Infinity;
      rows.forEach(function (row) {
        if (row.kind !== 'explicit' || row.hidden || !row.c) return;
        for (let i = 0; i <= 200; i++) {
          const x = wantX[0] + (wantX[1] - wantX[0]) * i / 200;
          let y;
          try { y = row.c.eval(Object.assign({ x: x }, env)); } catch (e) { y = NaN; }
          if (typeof y === 'number' && isFinite(y)) { if (y < lo) lo = y; if (y > hi) hi = y; }
        }
      });
      if (!isFinite(lo) || !isFinite(hi)) { lo = -(wantX[1] - wantX[0]) * k / 2; hi = -lo; }
      if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
      const yMid = (lo + hi) / 2;
      let xSpan = wantX[1] - wantX[0];
      const need = (hi - lo) * 1.16;
      if (need > xSpan * k) xSpan = need / k;
      const ySpan = xSpan * k;
      return { xr: [xMid - xSpan / 2, xMid + xSpan / 2], yr: [yMid - ySpan / 2, yMid + ySpan / 2] };
    }

    function drawRow(ctx, map, view, row, env) {
      if (!row || row.hidden) return;
      const color = row.color || CALC_COLORS[0];
      const q = qNow();
      if (row.kind === 'explicit') {
        drawOneCurve(ctx, map, row.c, env, color, 2.2, q.density);
      } else if (row.kind === 'vertical') {
        strokeContour(ctx, map, valueGrid(map, row.c, env, q.step), color, 2);
      } else if (row.kind === 'implicit') {
        strokeContour(ctx, map, valueGrid(map, row.c, env, q.step), color, 2.2);
      } else if (row.kind === 'ineqY') {
        shadeUnderCurve(ctx, map, row.c, env, color, row.op === '>' || row.op === '>=');
        drawOneCurve(ctx, map, row.c, env, color, 2, q.density);
      } else if (row.kind === 'ineqXY') {
        const grid = valueGrid(map, row.c, env, q.step);
        shadeGrid(ctx, map, grid, cmpOf(row.op), color);
        strokeContour(ctx, map, grid, color, 2);
      } else if (row.kind === 'point') {
        let x = NaN, y = NaN;
        try { x = row.x.eval(env); y = row.y.eval(env); } catch (e) { /* 参数没定义 */ }
        if (isFinite(x) && isFinite(y)) drawPlotPoint(ctx, map, x, y, color, row.label || '');
      } else if (row.kind === 'text') {
        let x = null, y = null;
        if (row.at) {
          try { x = row.at[0].eval(env); y = row.at[1].eval(env); } catch (e) { x = null; }
        }
        if (x == null || y == null) {        // 没给位置 → 放在视野左上角
          x = map.xr[0] + (map.xr[1] - map.xr[0]) * 0.05;
          y = map.yr[1] - (map.yr[1] - map.yr[0]) * 0.06;
        }
        drawPlotText(ctx, map, x, y, row.text, color);
      }
    }

    function syncOverlay(w, h) {
      const dpr = window.devicePixelRatio || 1;
      ovW = w; ovH = h;
      overlay.width = Math.max(1, Math.round(w * dpr));
      overlay.height = Math.max(1, Math.round(h * dpr));
      overlay.style.width = w + 'px';
      overlay.style.height = h + 'px';
      octx = overlay.getContext('2d');
      octx.setTransform(dpr, 0, 0, dpr, 0, 0);
      octx.clearRect(0, 0, w, h);
    }

    /** 找光标附近那条显函数曲线（屏幕距离 14px 内算命中） */
    function pickCurve(map, mx, my, env) {
      if (mx < map.pad.l || mx > map.pad.l + map.availW) return null;
      if (my < map.pad.t || my > map.pad.t + map.availH) return null;
      const hx = map.xOfPx(mx);
      let pick = null;
      rows.forEach(function (row) {
        if (row.kind !== 'explicit' || row.hidden || !row.c) return;
        let y;
        try { y = row.c.eval(Object.assign({ x: hx }, env)); } catch (e) { y = NaN; }
        if (typeof y !== 'number' || !isFinite(y)) return;
        const d = Math.abs(map.y2p(y) - my);
        if (d <= 14 && (!pick || d < pick.d)) pick = { d: d, x: hx, y: y, color: row.color };
      });
      return pick;
    }

    function drawOverlay() {
      if (!octx || !currentMap) return;
      octx.clearRect(0, 0, ovW, ovH);
      if (!hover) {
        coordEl.textContent = '';      // 提示固定在上一行，这里只放读数（空着也不跳）
        return;
      }
      const map = currentMap;
      const env = paramValues();
      const hx = map.xOfPx(hover.mx), hy = map.yOfPx(hover.my);
      const pick = pickCurve(map, hover.mx, hover.my, env);
      octx.save();
      octx.setLineDash([3, 3]);
      octx.strokeStyle = 'rgba(0,0,0,.16)';
      octx.lineWidth = 1;
      octx.beginPath();
      octx.moveTo(hover.mx, map.pad.t); octx.lineTo(hover.mx, map.pad.t + map.availH);
      octx.moveTo(map.pad.l, hover.my); octx.lineTo(map.pad.l + map.availW, hover.my);
      octx.stroke();
      octx.restore();
      if (pick) drawPlotPoint(octx, map, pick.x, pick.y, pick.color || '#1b1b1b', '(' + fmtNum(pick.x, decimals) + ', ' + fmtNum(pick.y, decimals) + ')');
      else drawPlotPoint(octx, map, hx, hy, 'rgba(0,0,0,.35)', '');
      coordEl.textContent = '(x, y) = (' + fmtNum(hx, decimals) + ', ' + fmtNum(hy, decimals) + ')' + (pick ? '　← 在曲线上' : '');
    }

    function draw() {
      const height = canvas.dataset.height ? +canvas.dataset.height : H0;
      const view = setupCanvas(canvas, height);
      syncOverlay(view.w, view.h);
      const env = paramValues();
      if (!frame) frame = initialFrame(view.w, view.h, env);
      const map = makeMapper(view.w, view.h, frame.xr, frame.yr, PAD, equalAspect);
      frame = { xr: map.xr, yr: map.yr };
      lastScale = map.scale;
      currentMap = map;
      const ctx = view.ctx;
      drawGrid(ctx, view.w, view.h, map, { grid: showGrid, decimals: decimals });
      ctx.save();
      ctx.beginPath();
      ctx.rect(map.pad.l, map.pad.t, map.availW, map.availH);
      ctx.clip();
      rows.forEach(function (row) {
        try { drawRow(ctx, map, view, row, env); } catch (e) { /* 一行画不出来不该拖垮整张图 */ }
      });
      ctx.restore();
      viewInfo.textContent = 'x ∈ [' + fmtTick(map.xr[0], decimals) + ', ' + fmtTick(map.xr[1], decimals) +
        ']　y ∈ [' + fmtTick(map.yr[0], decimals) + ', ' + fmtTick(map.yr[1], decimals) + ']';
      drawOverlay();
    }

    function zoom(factor) {
      if (!frame) return;
      const cx = (frame.xr[0] + frame.xr[1]) / 2;
      const cy = (frame.yr[0] + frame.yr[1]) / 2;
      const sx = (frame.xr[1] - frame.xr[0]) / 2 * factor;
      const sy = (frame.yr[1] - frame.yr[0]) / 2 * factor;
      frame = { xr: [cx - sx, cx + sx], yr: [cy - sy, cy + sy] };
      draw();
    }

    /* ---------- 鼠标：悬停读数 / 点击加点 / 拖动平移 / 滚轮缩放 ---------- */
    canvas.addEventListener('mousemove', function (e) {
      if (drag) { hover = null; return; }
      const rect = canvas.getBoundingClientRect();
      hover = { mx: e.clientX - rect.left, my: e.clientY - rect.top };
      drawOverlay();
    });
    canvas.addEventListener('mouseleave', function () { hover = null; drawOverlay(); });

    let drag = null;
    canvas.style.cursor = 'crosshair';
    canvas.addEventListener('mousedown', function (e) {
      if (!frame) return;
      beginInteract();                     // 拖动期间先用标准画质，停手后再按学生选的画质重画
      const rect = canvas.getBoundingClientRect();
      drag = {
        sx: e.clientX, sy: e.clientY,
        mx: e.clientX - rect.left, my: e.clientY - rect.top,
        moved: false,
        frame: { xr: frame.xr.slice(), yr: frame.yr.slice() },
      };
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!drag) return;
      const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (!drag.moved) return;
      const map = currentMap;
      if (!map) return;
      const dxu = dx / Math.max(1, map.availW) * (drag.frame.xr[1] - drag.frame.xr[0]);
      const dyu = dy / Math.max(1, map.availH) * (drag.frame.yr[1] - drag.frame.yr[0]);
      frame = {
        xr: [drag.frame.xr[0] - dxu, drag.frame.xr[1] - dxu],
        yr: [drag.frame.yr[0] + dyu, drag.frame.yr[1] + dyu],
      };
      draw();
    });
    window.addEventListener('mouseup', function () {
      if (!drag) return;
      const d = drag;
      drag = null;
      canvas.style.cursor = 'crosshair';
      endInteract();
      if (d.moved) return;
      // 没移动 = 点击：点在曲线上就加一个点（Desmos 的手感）
      const map = currentMap;
      if (!map) return;
      const pick = pickCurve(map, d.mx, d.my, paramValues());
      if (!pick) return;
      addRow('(' + fmtNum(pick.x, decimals) + ', ' + fmtNum(pick.y, decimals) + ')', { label: pointLabel() });
      buildList();
      refresh();
    });
    canvas.addEventListener('wheel', function (e) {
      if (!frame || !currentMap) return;
      e.preventDefault();
      beginInteract();
      endInteract();                       // 滚轮停下 180ms 后按学生选的画质重画
      const rect = canvas.getBoundingClientRect();
      const map = currentMap;
      const mx = map.xOfPx(e.clientX - rect.left);
      const my = map.yOfPx(e.clientY - rect.top);
      const f = e.deltaY > 0 ? 1.14 : 1 / 1.14;
      frame = {
        xr: [mx + (frame.xr[0] - mx) * f, mx + (frame.xr[1] - mx) * f],
        yr: [my + (frame.yr[0] - my) * f, my + (frame.yr[1] - my) * f],
      };
      draw();
    }, { passive: false });

    buildList();
    refresh();
    setTimeout(draw, 0);
    // 初始就把列表滚到底：常驻输入行必须在视野里，否则学生不知道该在哪写公式
    setTimeout(function () { focusRow(rows.length - 1); }, 0);
    // 工作台那一栏默认只有 400 出头：算器「左边公式 + 右边画布」并排会挤成一条缝
    // （画布只剩 100px 宽，比不能拖还难受）。所以按**自己的实际宽度**决定布局：
    // 窄了就上下叠（公式在上、画布在下），宽了才并排。别用 viewport 媒体查询 ——
    // 窗口很宽不代表组件很宽，这个坑必须按组件宽度判断。
    function syncLayout() {
      const narrow = (box.clientWidth || 0) > 0 && box.clientWidth < 700;
      box.classList.toggle('au-calc-narrow', narrow);
    }
    syncLayout();
    autoResize(box, function () { syncLayout(); frame = null; draw(); });

    box._redraw = draw;
    box._refresh = refresh;
    box._rows = rows;
    box._classify = classifyRow;      // 测试直接验「这一行被认成什么」
    // 面板变高 = **看到更多**，而不是把图放大（与 plot 一致）
    box._setHeight = function (h) {
      const newH = h ? Math.max(180, h - 60) : H0;
      canvas.dataset.height = String(newH);
      if (frame && lastScale > 0) {
        const availW = Math.max(80, (canvas.clientWidth || 600) - PAD.l - PAD.r);
        const availH = Math.max(80, newH - PAD.t - PAD.b);
        const cx = (frame.xr[0] + frame.xr[1]) / 2;
        const cy = (frame.yr[0] + frame.yr[1]) / 2;
        const halfW = availW / lastScale / 2;
        const halfH = availH / lastScale / 2;
        frame = { xr: [cx - halfW, cx + halfW], yr: [cy - halfH, cy + halfH] };
      }
      draw();
    };
    return box;
  };

  /* ==========================================================================
   * 六·B、三个小工具：可拖的几何画板 / 数据统计台 / 方程求解器
   * --------------------------------------------------------------------------
   * 和图形计算器同一条原则：**模型只写数据，界面自己会算、能拖、能改**。
   * 这样模型算错一个数不致命，学生自己动手才是重点。
   * ======================================================================== */
  const TOOL_COLORS = ['#0067c0', '#0f7b0f', '#c239b3', '#d83b01', '#5c2d91', '#0d7d80'];

  function gcdInt(a, b) {
    a = Math.abs(Math.round(a)); b = Math.abs(Math.round(b));
    while (b) { const t = a % b; a = b; b = t; }
    return a || 1;
  }
  /** √m = k√n（n 不含平方因子）—— 用来给二次方程写「精确根」 */
  function simplifySqrt(m) {
    if (!(m >= 0)) return null;
    let k = 1, n = Math.round(m);
    for (let i = 2; i * i <= n; i++) {
      while (n % (i * i) === 0) { n /= (i * i); k *= i; }
    }
    return { k: k, n: n };
  }
  /** 约分后的分数写法（分母 1 时只写分子） */
  function fracText(num, den) {
    if (!den) return String(num);
    let sign = (num < 0) !== (den < 0) ? -1 : 1;
    num = Math.abs(num); den = Math.abs(den);
    const g = gcdInt(num, den);
    num = num / g; den = den / g;
    if (den === 1) return String(sign * num);
    return (sign < 0 ? '-' : '') + num + '/' + den;
  }

  /* ------------------------------------------------------------------------
   * 可拖动的几何画板（geometry）
   * 模型只给点/线/面，学生拖顶点，边长、角度、面积当场跟着变。
   * 讲勾股定理、三角形内角和、面积、相似，都能用它让学生自己发现规律。
   * ---------------------------------------------------------------------- */
  RENDERERS.geometry = function (n) {
    const box = el('div', 'au-geo');
    if (n.title) box.appendChild(el('div', 'au-geo-title', fmt(n.title)));

    const PAD = { l: 34, r: 16, t: 16, b: 26 };
    const H0 = Number(n.height || 400);
    const canvas = el('canvas', 'au-canvas');
    canvas.dataset.height = String(H0);
    const stage = el('div', 'au-geo-stage');
    stage.appendChild(canvas);
    const tip = el('div', 'au-geo-tip', '拖动带白圈的点，边长和角度会跟着变');
    stage.appendChild(tip);
    box.appendChild(stage);

    // 数据（模型给的）
    const pts = ((n.points && n.points.length) ? n.points : []).map(function (p, i) {
      return {
        name: String(p && p.name != null ? p.name : ('P' + (i + 1))),
        at: [(p && p.at && Number(p.at[0])) || 0, (p && p.at && Number(p.at[1])) || 0],
        fixed: !!(p && p.fixed),
        color: (p && p.color) || TOOL_COLORS[i % TOOL_COLORS.length],
      };
    });
    const byName = {};
    pts.forEach(function (p) { byName[p.name] = p; });
    const segs = ((n.segments || []).map(function (s) { return [String(s[0]), String(s[1])]; }))
      .filter(function (s) { return byName[s[0]] && byName[s[1]]; });
    const polys = (n.polygons || []).map(function (ps) {
      return (ps || []).map(String).filter(function (nm) { return byName[nm]; });
    }).filter(function (ps) { return ps.length >= 3; });
    const circles = (n.circles || []).map(function (c, i) {
      return {
        center: c && c.center != null ? c.center : null,     // [x,y] 或 "点名"
        r: c && c.r != null ? Number(c.r) : null,
        through: c && c.through != null ? String(c.through) : null,
        label: c && c.label, color: (c && c.color) || TOOL_COLORS[(i + 3) % TOOL_COLORS.length],
      };
    });
    let showGrid = n.grid !== false;
    const showLengths = n.showLengths !== false;
    const showAngles = n.showAngles !== false;
    const showArea = n.showArea !== false;
    const showSquares = !!n.showSquares;      // 勾股定理那种：再多给一列 a²

    function pointAt(spec) {
      if (typeof spec === 'string') return byName[spec] ? byName[spec].at : [0, 0];
      if (spec && spec.length === 2) return [Number(spec[0]) || 0, Number(spec[1]) || 0];
      if (spec && spec.name && byName[spec.name]) return byName[spec.name].at;
      return [0, 0];
    }
    function circleGeom(c) {
      const ctr = pointAt(c.center);
      const r = c.r != null ? c.r : (c.through && byName[c.through]
        ? Math.hypot(byName[c.through].at[0] - ctr[0], byName[c.through].at[1] - ctr[1]) : 1);
      return { c: ctr, r: r };
    }

    const panel = el('div', 'au-geo-panel');
    const listEl = el('div', 'au-geo-list');
    const tools = el('div', 'au-plot-tools');
    const fitBtn = el('button', 'au-btn au-btn-mini', '适应视图');
    const gridBtn = el('button', 'au-btn au-btn-mini', '网格');
    if (showGrid) gridBtn.classList.add('au-on');
    tools.appendChild(fitBtn); tools.appendChild(gridBtn);
    panel.appendChild(listEl);
    panel.appendChild(tools);
    if (n.task) panel.appendChild(el('div', 'au-geo-task', fmt(n.task)));
    box.appendChild(panel);

    let frame = null;
    let map = null;
    let drag = null;

    function fitFrame() {
      const xs = [], ys = [];
      pts.forEach(function (p) { xs.push(p.at[0]); ys.push(p.at[1]); });
      circles.forEach(function (c) {
        const g = circleGeom(c);
        xs.push(g.c[0] - g.r, g.c[0] + g.r); ys.push(g.c[1] - g.r, g.c[1] + g.r);
      });
      if (!xs.length) { xs.push(-5, 5); ys.push(-5, 5); }
      let x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      const mx = Math.max(0.8, (x1 - x0) * 0.22), my = Math.max(0.8, (y1 - y0) * 0.22);
      // 等比交给 makeMapper（圆才是圆、正方形才是正方形）
      frame = { xr: [x0 - mx, x1 + mx], yr: [y0 - my, y1 + my] };
    }

    function labelsFor() {
      // 每一项测量 → 面板里一行（拖动时只更新文字）
      const rows = [];
      segs.forEach(function (s) {
        const a = byName[s[0]].at, b = byName[s[1]].at;
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        rows.push({ full: '|' + s[0] + s[1] + '|', text: fmtNum(len, 2) });
        if (showSquares) rows.push({ full: '|' + s[0] + s[1] + '|²', text: fmtNum(len * len, 2) });
      });
      polys.forEach(function (ps) {
        let per = 0, area = 0;
        for (let i = 0; i < ps.length; i++) {
          const a = byName[ps[i]].at, b = byName[ps[(i + 1) % ps.length]].at;
          per += Math.hypot(b[0] - a[0], b[1] - a[1]);
          area += a[0] * b[1] - b[0] * a[1];
        }
        rows.push({ full: ps.join('') + ' 周长', text: fmtNum(per, 2) });
        rows.push({ full: ps.join('') + ' 面积', text: fmtNum(Math.abs(area) / 2, 2) });
        if (showAngles) {
          ps.forEach(function (nm, i) {
            const cur = byName[nm].at;
            const p1 = byName[ps[(i - 1 + ps.length) % ps.length]].at;
            const p2 = byName[ps[(i + 1) % ps.length]].at;
            const v1 = [p1[0] - cur[0], p1[1] - cur[1]], v2 = [p2[0] - cur[0], p2[1] - cur[1]];
            const n1 = Math.hypot(v1[0], v1[1]) || 1, n2 = Math.hypot(v2[0], v2[1]) || 1;
            const cosv = Math.max(-1, Math.min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)));
            // 角的名字按课本习惯写成 ∠BAC（顶点在中间：先相邻点、再顶点、再另一个相邻点）
            rows.push({ full: '∠' + ps[(i + 1) % ps.length] + nm + ps[(i - 1 + ps.length) % ps.length],
                        text: (Math.acos(cosv) * 180 / Math.PI).toFixed(1) + '°' });
          });
        }
      });
      circles.forEach(function (c) {
        const g = circleGeom(c);
        rows.push({ full: (c.label || '圆') + ' 半径', text: fmtNum(g.r, 2) });
        rows.push({ full: (c.label || '圆') + ' 周长', text: fmtNum(2 * Math.PI * g.r, 2) });
        rows.push({ full: (c.label || '圆') + ' 面积', text: fmtNum(Math.PI * g.r * g.r, 2) });
      });
      return rows;
    }

    function renderPanel() {
      const rows = labelsFor();
      listEl.innerHTML = '';
      if (!rows.length) return;
      rows.forEach(function (r) {
        const line = el('div', 'au-geo-row');
        line.appendChild(el('span', 'au-geo-key', esc(r.full)));
        line.appendChild(el('b', 'au-geo-val', esc(r.text)));
        listEl.appendChild(line);
      });
    }

    function draw() {
      const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : H0);
      if (!frame) fitFrame();
      map = makeMapper(view.w, view.h, frame.xr, frame.yr, PAD, true);
      frame = { xr: map.xr, yr: map.yr };
      const ctx = view.ctx;
      drawGrid(ctx, view.w, view.h, map, { grid: showGrid });
      ctx.save();
      ctx.beginPath();
      ctx.rect(map.pad.l, map.pad.t, map.availW, map.availH);
      ctx.clip();

      // 多边形（先铺底）
      polys.forEach(function (ps, i) {
        ctx.beginPath();
        ps.forEach(function (nm, k) {
          const p = byName[nm].at;
          const x = map.x2p(p[0]), y = map.y2p(p[1]);
          if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        });
        ctx.closePath();
        ctx.fillStyle = 'rgba(0,103,192,.08)';
        ctx.fill();
        ctx.strokeStyle = TOOL_COLORS[i % TOOL_COLORS.length];
        ctx.lineWidth = 2;
        ctx.stroke();
        if (showArea) {
          let cx = 0, cy = 0;
          ps.forEach(function (nm) { cx += byName[nm].at[0]; cy += byName[nm].at[1]; });
          cx /= ps.length; cy /= ps.length;
          let area = 0;
          for (let k = 0; k < ps.length; k++) {
            const a = byName[ps[k]].at, b = byName[ps[(k + 1) % ps.length]].at;
            area += a[0] * b[1] - b[0] * a[1];
          }
          drawPlotText(ctx, map, cx, cy, 'S = ' + fmtNum(Math.abs(area) / 2, 2), '#1b1b1b');
        }
      });

      // 圆（等比映射下就是正圆）
      circles.forEach(function (c) {
        const g = circleGeom(c);
        const cx = map.x2p(g.c[0]), cy = map.y2p(g.c[1]);
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(0.5, g.r * map.xUnit), 0, Math.PI * 2);
        ctx.strokeStyle = c.color;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx, cy, 3.2, 0, Math.PI * 2);
        ctx.fillStyle = c.color;
        ctx.fill();
        if (c.label) drawPlotText(ctx, map, g.c[0] + g.r * 0.62, g.c[1] + g.r * 0.78, c.label, c.color);
      });

      // 线段 + 中点标注长度
      segs.forEach(function (s, i) {
        const a = byName[s[0]].at, b = byName[s[1]].at;
        ctx.beginPath();
        ctx.moveTo(map.x2p(a[0]), map.y2p(a[1]));
        ctx.lineTo(map.x2p(b[0]), map.y2p(b[1]));
        ctx.strokeStyle = TOOL_COLORS[(i + 1) % TOOL_COLORS.length];
        ctx.lineWidth = 2.2;
        ctx.stroke();
        if (showLengths) {
          const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
          drawPlotText(ctx, map, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, fmtNum(len, 2),
            TOOL_COLORS[(i + 1) % TOOL_COLORS.length]);
        }
      });

      // 顶点（可拖的加白圈）
      pts.forEach(function (p) {
        drawPlotPoint(ctx, map, p.at[0], p.at[1], p.color, p.name + ' (' + fmtNum(p.at[0], 1) + ', ' + fmtNum(p.at[1], 1) + ')');
        if (!p.fixed) {
          ctx.beginPath();
          ctx.arc(map.x2p(p.at[0]), map.y2p(p.at[1]), 9, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(0,103,192,.35)';
          ctx.lineWidth = 1.2;
          ctx.setLineDash([3, 3]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      });
      ctx.restore();
      renderPanel();
    }

    /** 找出鼠标底下最近的、可以拖的点 */
    function pickPoint(mx, my) {
      if (!map) return null;
      let best = null;
      pts.forEach(function (p) {
        if (p.fixed) return;
        const d = Math.hypot(map.x2p(p.at[0]) - mx, map.y2p(p.at[1]) - my);
        if (d <= 14 && (!best || d < best.d)) best = { p: p, d: d };
      });
      return best ? best.p : null;
    }
    canvas.style.cursor = 'grab';
    canvas.addEventListener('mousedown', function (e) {
      if (!map) return;
      const rect = canvas.getBoundingClientRect();
      const p = pickPoint(e.clientX - rect.left, e.clientY - rect.top);
      if (!p) return;
      drag = { p: p, moved: false };
      canvas.style.cursor = 'grabbing';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!drag || !map) return;
      const rect = canvas.getBoundingClientRect();
      // 吸附到 0.1：手抖也能拖出好数字（老师要的是整数关系）
      const nx = Math.round(map.xOfPx(e.clientX - rect.left) * 10) / 10;
      const ny = Math.round(map.yOfPx(e.clientY - rect.top) * 10) / 10;
      if (nx !== drag.p.at[0] || ny !== drag.p.at[1]) drag.moved = true;
      drag.p.at = [nx, ny];
      draw();
    });
    window.addEventListener('mouseup', function () {
      if (!drag) return;
      drag = null;
      canvas.style.cursor = 'grab';
    });
    fitBtn.onclick = function () { frame = null; draw(); };
    gridBtn.onclick = function () {
      showGrid = !showGrid;
      gridBtn.classList.toggle('au-on', showGrid);
      draw();
    };

    setTimeout(draw, 0);
    autoResize(box, function () { draw(); });   // 窗口变了只重画，不重取景（拖过的点别跳）
    box._redraw = draw;
    box._points = pts;                          // 测试可以直接改点看测量有没有跟着变
    box._measure = function () { return labelsFor(); };
    box._screenOf = function (name) {           // 点在某点的屏幕坐标（测试里模拟拖动用）
      const p = byName[name];
      if (!p || !map) return null;
      return { x: map.x2p(p.at[0]), y: map.y2p(p.at[1]) };
    };
    box._setHeight = function (h) {
      canvas.dataset.height = String(h ? Math.max(180, h - 90) : H0);
      draw();
    };
    return box;
  };

  /* ------------------------------------------------------------------------
   * 数据统计台（datalab）
   * 模型给一串数，学生改任意一个数，平均数/中位数/众数/方差和图形当场变。
   * 讲平均数、中位数、众数、极差、方差的区别时最有用：
   * 改一个极端值，平均数动了、中位数没动 —— 一眼就看出来。
   * ---------------------------------------------------------------------- */
  RENDERERS.datalab = function (n) {
    const box = el('div', 'au-datalab');
    if (n.title) box.appendChild(el('div', 'au-dl-title', fmt(n.title)));

    let values = [];
    if (Array.isArray(n.values)) {
      values = n.values.map(function (v) { return Number(v); }).filter(function (v) { return isFinite(v); });
    } else if (Array.isArray(n.rows)) {
      values = n.rows.map(function (r) { return Number(Array.isArray(r) ? r[0] : r); })
        .filter(function (v) { return isFinite(v); });
    }
    const labels = Array.isArray(n.labels) ? n.labels.map(String) : [];
    let chart = ['bars', 'line', 'dot'].indexOf(n.chart) >= 0 ? n.chart : 'bars';
    let sorted = false;

    const grid = el('div', 'au-dl-grid');
    const statsEl = el('div', 'au-dl-stats');
    const canvas = el('canvas', 'au-canvas');
    const H = Number(n.height || 300);
    canvas.dataset.height = String(H);
    const chartWrap = el('div', 'au-dl-chart');
    chartWrap.appendChild(canvas);

    const tools = el('div', 'au-plot-tools');
    const addBtn = el('button', 'au-btn au-btn-mini', '＋ 加一个数');
    const delBtn = el('button', 'au-btn au-btn-mini', '删掉最后一个');
    const sortBtn = el('button', 'au-btn au-btn-mini', '排序');
    const chartBtn = el('button', 'au-btn au-btn-mini');
    function chartLabel() {
      chartBtn.textContent = '图：' + ({ bars: '柱状', line: '折线', dot: '点图' })[chart];
    }
    chartLabel();
    [addBtn, delBtn, sortBtn, chartBtn].forEach(function (b) { tools.appendChild(b); });

    box.appendChild(tools);
    box.appendChild(grid);
    box.appendChild(chartWrap);
    box.appendChild(statsEl);

    function stats(v) {
      const s = v.slice().sort(function (a, b) { return a - b; });
      const cnt = s.length;
      const sum = s.reduce(function (a, b) { return a + b; }, 0);
      const mean = cnt ? sum / cnt : NaN;
      const median = cnt ? (cnt % 2 ? s[(cnt - 1) / 2] : (s[cnt / 2 - 1] + s[cnt / 2]) / 2) : NaN;
      const counts = {};
      s.forEach(function (x) { counts[x] = (counts[x] || 0) + 1; });
      let best = 0, modes = [];
      Object.keys(counts).forEach(function (k) {
        if (counts[k] > best) { best = counts[k]; modes = [Number(k)]; }
        else if (counts[k] === best) modes.push(Number(k));
      });
      if (best <= 1) modes = [];
      const variance = cnt ? s.reduce(function (a, x) { return a + (x - mean) * (x - mean); }, 0) / cnt : NaN;
      return {
        n: cnt, sum: sum, mean: mean, median: median, mode: modes,
        variance: variance, std: Math.sqrt(variance),
        min: cnt ? s[0] : NaN, max: cnt ? s[cnt - 1] : NaN,
        range: cnt ? s[cnt - 1] - s[0] : NaN,
      };
    }

    function renderStats() {
      const st = stats(values);
      const rows = [
        ['数据个数 n', String(st.n)],
        ['平均数 x̄', fmtNum(st.mean, 2)],
        ['中位数', fmtNum(st.median, 2)],
        ['众数', st.mode.length ? st.mode.join('、') : '没有'],
        ['极差', fmtNum(st.range, 2)],
        ['方差 s²', fmtNum(st.variance, 2)],
        ['标准差 s', fmtNum(st.std, 2)],
        ['总和 / 最小 / 最大', fmtNum(st.sum, 2) + ' / ' + fmtNum(st.min, 2) + ' / ' + fmtNum(st.max, 2)],
      ];
      statsEl.innerHTML = '';
      rows.forEach(function (r) {
        const line = el('div', 'au-geo-row');
        line.appendChild(el('span', 'au-geo-key', esc(r[0])));
        line.appendChild(el('b', 'au-geo-val', esc(r[1])));
        statsEl.appendChild(line);
      });
    }

    function renderGrid() {
      grid.innerHTML = '';
      const list = sorted ? values.slice().sort(function (a, b) { return a - b; }) : values;
      list.forEach(function (v) {
        const cell = el('div', 'au-dl-cell');
        const input = document.createElement('input');
        input.type = 'number';
        input.className = 'au-dl-input';
        input.step = 'any';
        input.value = String(v);
        input.setAttribute('aria-label', '数据值');
        input.addEventListener('input', function () {
          const nv = Number(input.value);
          const i = values.indexOf(v);
          if (i >= 0 && isFinite(nv)) {
            values[i] = nv;
            v = nv;                     // 让同一个 input 继续对应这一格
            renderStats();
            drawChart();
          }
        });
        cell.appendChild(input);
        grid.appendChild(cell);
      });
      if (!list.length) grid.appendChild(el('div', 'au-muted', '（还没有数据）'));
    }

    function chartData() {
      const list = sorted ? values.slice().sort(function (a, b) { return a - b; }) : values;
      return list.map(function (v, i) {
        return { label: labels[i] != null ? labels[i] : String(i + 1), value: v };
      });
    }

    function drawChart() {
      const data = chartData();
      if (!data.length) return;
      if (chart === 'line') {
        drawLine(canvas, [{ label: '数据', data: data.map(function (d) { return d.value; }) }],
          data.map(function (d) { return d.label; }));
      } else if (chart === 'dot') {
        // 点图：最适合看「数据分布」，一列点看散不散
        const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : H);
        const ctx = view.ctx;
        const vals = data.map(function (d) { return d.value; });
        const st = stats(vals);
        const lo = Math.min(st.min, st.mean) , hi = Math.max(st.max, st.mean);
        const pad = { l: 34, r: 18, t: 16, b: 26 };
        const span = (hi - lo) || 1;
        const y2p = function (v) { return view.h - pad.b - (v - lo) / span * (view.h - pad.t - pad.b); };
        const x2p = function (i) { return pad.l + (data.length <= 1 ? 0 : i / (data.length - 1)) * (view.w - pad.l - pad.r); };
        ctx.strokeStyle = 'rgba(0,0,0,.22)';
        niceTicks(lo, hi, 4).forEach(function (v) {
          const y = y2p(v);
          ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(view.w - pad.r, y); ctx.stroke();
          ctx.fillStyle = 'rgba(0,0,0,.5)';
          ctx.font = '11px "Segoe UI",system-ui,sans-serif';
          ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
          ctx.fillText(fmtTick(v), pad.l - 5, y);
        });
        data.forEach(function (d, i) {
          ctx.beginPath();
          ctx.arc(x2p(i), y2p(d.value), 4.4, 0, Math.PI * 2);
          ctx.fillStyle = '#0067c0';
          ctx.fill();
          ctx.font = '11px "Segoe UI",system-ui,sans-serif';
          ctx.fillStyle = 'rgba(0,0,0,.55)';
          ctx.textAlign = 'center'; ctx.textBaseline = 'top';
          ctx.fillText(fmtTick(d.value), x2p(i), view.h - pad.b + 5);
        });
        // 平均线
        const my = y2p(st.mean);
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = '#c239b3';
        ctx.beginPath(); ctx.moveTo(pad.l, my); ctx.lineTo(view.w - pad.r, my); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = '#c239b3';
        ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        ctx.fillText('平均 ' + fmtNum(st.mean, 2), pad.l + 4, my - 3);
      } else {
        drawBars(canvas, data, false);
      }
    }

    function refreshAll() { renderGrid(); renderStats(); drawChart(); }

    addBtn.onclick = function () {
      const last = values.length ? values[values.length - 1] : 0;
      values.push(values.length ? Math.round((last + 1) * 10) / 10 : 1);
      if (labels.length) labels.push('新');
      refreshAll();
    };
    delBtn.onclick = function () { if (values.length) { values.pop(); if (labels.length) labels.pop(); refreshAll(); } };
    sortBtn.onclick = function () {
      sorted = !sorted;
      sortBtn.classList.toggle('au-on', sorted);
      refreshAll();
    };
    chartBtn.onclick = function () {
      chart = chart === 'bars' ? 'line' : chart === 'line' ? 'dot' : 'bars';
      chartLabel();
      drawChart();
    };

    setTimeout(refreshAll, 0);
    autoResize(box, drawChart);
    box._redraw = drawChart;
    box._render = refreshAll;                   // 同步重排一遍（测试用；也方便别处主动刷新）
    box._values = function () { return values; };
    box._stats = function () { return stats(values); };
    box._setHeight = function (h) {
      canvas.dataset.height = String(h ? Math.max(160, h - 220) : H);
      drawChart();
    };
    return box;
  };

  /* ------------------------------------------------------------------------
   * 方程求解器（solver）
   * 模型给系数，学生改系数，判别式 / 根 / 因式分解 / 图像当场重算。
   * 关键点：**过程也要显示**（Δ = b²-4ac = …），不然学生只看到一个答案。
   * ---------------------------------------------------------------------- */
  RENDERERS.solver = function (n) {
    const box = el('div', 'au-solver');
    if (n.title) box.appendChild(el('div', 'au-solver-title', fmt(n.title)));

    let kind = n.kind === 'linear' ? 'linear' : 'quadratic';
    let aa = Number(n.a != null ? n.a : 1);
    let bb = Number(n.b != null ? n.b : 0);
    let cc = Number(n.c != null ? n.c : 0);

    const coefBar = el('div', 'au-solver-coef');
    const coefInputs = {};
    function makeCoef(key, label) {
      const wrap = el('label', 'au-solver-coef-item');
      wrap.appendChild(el('span', 'au-geo-key', label));
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'au-dl-input au-solver-input';
      input.step = '1';
      input.setAttribute('aria-label', label);
      input.addEventListener('input', function () {
        const v = Number(input.value);
        if (!isFinite(v)) return;
        if (key === 'a') aa = v; else if (key === 'b') bb = v; else cc = v;
        render();
      });
      wrap.appendChild(input);
      coefInputs[key] = input;
      coefBar.appendChild(wrap);
    }
    makeCoef('a', 'a');
    makeCoef('b', 'b');
    makeCoef('c', 'c');
    box.appendChild(coefBar);

    const stepsEl = el('div', 'au-solver-steps');
    const canvas = el('canvas', 'au-canvas');
    const H = Number(n.height || 320);
    canvas.dataset.height = String(H);
    const chartWrap = el('div', 'au-solver-chart');
    chartWrap.appendChild(canvas);
    box.appendChild(stepsEl);
    box.appendChild(chartWrap);

    function syncInputs() {
      ['a', 'b', 'c'].forEach(function (k) {
        const elx = coefInputs[k];
        const v = k === 'a' ? aa : k === 'b' ? bb : cc;
        if (elx !== document.activeElement) elx.value = String(v);
      });
      coefInputs.c.parentNode.style.display = kind === 'linear' ? 'none' : '';
    }

    /** 把方程、判别式、根、因式分解、顶点、韦达都算出来（和 Python 侧同一套规则） */
    function solve() {
      const out = { kind: kind, a: aa, b: bb, c: cc, steps: [], roots: [], exact: [], D: null };
      if (kind === 'linear') {
        out.steps.push('解方程 ' + fmtNum(aa, 4) + 'x + ' + fmtNum(bb, 4) + ' = 0');
        if (aa === 0) {
          out.note = bb === 0 ? 'a = 0 且 b = 0：任何 x 都成立（无穷多解）' : 'a = 0 且 b ≠ 0：无解';
          return out;
        }
        const x = -bb / aa;
        out.roots = [x]; out.exact = [fracText(-bb, aa)];
        out.steps.push('x = −b / a = −(' + fmtNum(bb, 4) + ') / ' + fmtNum(aa, 4) + ' = ' + fracText(-bb, aa));
        return out;
      }
      const a = aa, b = bb, c = cc;
      out.steps.push('解方程 ' + fmtNum(a, 4) + 'x² ' + (b < 0 ? '− ' + fmtNum(-b, 4) : '+ ' + fmtNum(b, 4)) + 'x '
        + (c < 0 ? '− ' + fmtNum(-c, 4) : '+ ' + fmtNum(c, 4)) + ' = 0');
      if (a === 0) {
        out.note = 'a = 0：这其实是一元一次方程，把 a 改成非零才是二次方程';
        return out;
      }
      const D = b * b - 4 * a * c;
      out.D = D;
      out.steps.push('Δ = b² − 4ac = (' + fmtNum(b, 4) + ')² − 4×(' + fmtNum(a, 4) + ')×(' + fmtNum(c, 4) + ') = ' + fmtNum(D, 4));
      if (D < 0) {
        out.steps.push('Δ < 0 → 没有实数根（抛物线不与 x 轴相交）');
        out.note = 'Δ < 0，无实数根';
      } else if (D === 0) {
        const r = -b / (2 * a);
        out.roots = [r];
        out.exact = [fracText(-b, 2 * a)];
        out.steps.push('Δ = 0 → 两个相等的实根：x = −b / (2a) = ' + fracText(-b, 2 * a));
      } else {
        const s = simplifySqrt(D);
        if (s.n === 1) {
          const r1 = (-b + s.k) / (2 * a), r2 = (-b - s.k) / (2 * a);
          out.roots = [r1, r2];
          out.exact = [fracText(-b + s.k, 2 * a), fracText(-b - s.k, 2 * a)];
        } else {
          out.roots = [(-b + Math.sqrt(D)) / (2 * a), (-b - Math.sqrt(D)) / (2 * a)];
          const g = gcdInt(gcdInt(b, s.k), 2 * a);
          const bb2 = b / g, kk2 = s.k / g, aa2 = (2 * a) / g;
          const head = (bb2 === 0 ? '' : fmtNum(-bb2, 4));
          const surd = (kk2 === 1 ? '' : kk2) + '√' + s.n;
          out.exact = [(head ? head + ' ± ' : '±') + surd + (aa2 === 1 ? '' : ' / ' + aa2)];
          out.surd = { k: s.k, n: s.n };
        }
        out.steps.push('Δ > 0 → 两个不等的实根：x = (−b ± √Δ) / (2a) = ' +
          (s.n === 1 ? '(' + fmtNum(-b, 4) + ' ± ' + s.k + ') / ' + fmtNum(2 * a, 4) : '(−b ± ' + s.k + '√' + s.n + ') / (2a)'));
        out.steps.push('x₁ ≈ ' + fmtNum(out.roots[0], 4) + '，x₂ ≈ ' + fmtNum(out.roots[1], 4));
      }
      // 因式分解（有理根时给出整数系数形式）
      if (out.roots.length && D >= 0) {
        const r1 = out.roots[0], r2 = out.roots[1];
        const den = 1000000;
        const p1 = Math.round(r1 * den), p2 = Math.round(r2 * den);
        const q1 = den / gcdInt(p1, den), q2 = den / gcdInt(p2, den);
        const n1 = p1 / gcdInt(p1, den), n2 = p2 / gcdInt(p2, den);
        if (q1 * q2 && Number.isInteger(a / (q1 * q2))) {
          const k = a / (q1 * q2);
          const f1 = (q1 === 1 ? '' : q1) + 'x' + (n1 === 0 ? '' : (n1 > 0 ? ' − ' + n1 : ' + ' + (-n1)));
          const f2 = (q2 === 1 ? '' : q2) + 'x' + (n2 === 0 ? '' : (n2 > 0 ? ' − ' + n2 : ' + ' + (-n2)));
          out.factor = (k === 1 ? '' : k) + '(' + f1 + ')(' + f2 + ')';
        } else if (out.exact.length === 2) {
          out.factor = fmtNum(a, 4) + '(x − ' + out.exact[0] + ')(x − ' + out.exact[1] + ')';
        }
      }
      const vx = -b / (2 * a), vy = a * vx * vx + b * vx + c;
      out.vertex = [vx, vy];
      out.steps.push('顶点 (' + fmtNum(vx, 4) + ', ' + fmtNum(vy, 4) + ')，对称轴 x = ' + fmtNum(vx, 4));
      if (out.roots.length === 2) {
        out.steps.push('韦达定理：x₁ + x₂ = ' + fmtNum(out.roots[0] + out.roots[1], 4) +
          ' = −b/a，x₁·x₂ = ' + fmtNum(out.roots[0] * out.roots[1], 4) + ' = c/a');
      }
      return out;
    }

    function render() {
      syncInputs();
      const s = solve();
      stepsEl.innerHTML = '';
      s.steps.forEach(function (t) { stepsEl.appendChild(el('div', 'au-solver-step', fmt(t))); });
      if (s.note) stepsEl.appendChild(el('div', 'au-solver-note', fmt(s.note)));
      const rows = [];
      if (s.kind === 'quadratic' && s.D != null) rows.push(['判别式 Δ', fmtNum(s.D, 4)]);
      if (s.exact.length) rows.push(['根（精确）', 'x = ' + s.exact.join('，x = ')]);
      if (s.roots.length) rows.push(['根（小数）', s.roots.map(function (r) { return fmtNum(r, 4); }).join('，')]);
      if (s.factor) rows.push(['因式分解', s.factor]);
      if (s.vertex) rows.push(['顶点', '(' + fmtNum(s.vertex[0], 3) + ', ' + fmtNum(s.vertex[1], 3) + ')']);
      if (rows.length) {
        const kv = el('div', 'au-solver-kv');
        rows.forEach(function (r) {
          const line = el('div', 'au-geo-row');
          line.appendChild(el('span', 'au-geo-key', esc(r[0])));
          line.appendChild(el('b', 'au-geo-val', esc(r[1])));
          kv.appendChild(line);
        });
        stepsEl.appendChild(kv);
      }
      drawGraph(s);
    }

    function drawGraph(s) {
      if (s.kind !== 'quadratic' || s.a === 0) { chartWrap.style.display = 'none'; return; }
      chartWrap.style.display = '';
      const view = setupCanvas(canvas, canvas.dataset.height ? +canvas.dataset.height : H);
      const ctx = view.ctx;
      const a = s.a, b = s.b, c = s.c;
      const vx = -b / (2 * a);
      let lo = vx, hi = vx;
      if (s.roots.length) { lo = Math.min(lo, s.roots[0], s.roots[1]); hi = Math.max(hi, s.roots[0], s.roots[1]); }
      const span = Math.max(2, (hi - lo) * 1.8);
      const xr = [vx - span, vx + span];
      const vals = [a * xr[0] * xr[0] + b * xr[0] + c, a * xr[1] * xr[1] + b * xr[1] + c, a * vx * vx + b * vx + c, 0];
      let ylo = Math.min.apply(null, vals), yhi = Math.max.apply(null, vals);
      if (yhi - ylo < 1) { ylo -= 1; yhi += 1; }
      const pad = { l: 34, r: 16, t: 14, b: 26 };
      const map = makeMapper(view.w, view.h, xr, [ylo, yhi], pad, false);
      drawGrid(ctx, view.w, view.h, map, { grid: true });
      ctx.save();
      ctx.beginPath();
      ctx.rect(map.pad.l, map.pad.t, map.availW, map.availH);
      ctx.clip();
      const fn = compileExpr('a*x^2+b*x+c');
      drawOneCurve(ctx, map, fn, { a: a, b: b, c: c }, '#0067c0', 2.2, 1.4);
      // 对称轴
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = 'rgba(0,0,0,.35)';
      ctx.beginPath();
      ctx.moveTo(map.x2p(vx), map.pad.t);
      ctx.lineTo(map.x2p(vx), map.pad.t + map.availH);
      ctx.stroke();
      ctx.setLineDash([]);
      s.roots.forEach(function (r) {
        drawPlotPoint(ctx, map, r, 0, '#d83b01', '(' + fmtNum(r, 3) + ', 0)');
      });
      drawPlotPoint(ctx, map, map.xOfPx(map.pad.l + map.availW / 2) * 0 + s.vertex[0], s.vertex[1], '#0f7b0f', '顶点');
      ctx.restore();
    }

    setTimeout(render, 0);
    autoResize(box, render);
    box._redraw = render;
    box._solve = solve;
    box._setCoefficients = function (a, b, c) {
      if (a != null) aa = Number(a);
      if (b != null) bb = Number(b);
      if (c != null) cc = Number(c);
      render();
    };
    box._setHeight = function (h) {
      canvas.dataset.height = String(h ? Math.max(150, h - 240) : H);
      render();
    };
    return box;
  };

  RENDERERS.slider = function (n) {
    const box = el('div', 'au-slider');
    const min = Number(n.min != null ? n.min : 0);
    const max = Number(n.max != null ? n.max : 100);
    const value = Number(n.value != null ? n.value : min);
    box.innerHTML = '<div class="au-slider-head"><span>' + fmt(n.label || '') + '</span>' +
      '<b>' + value + esc(n.unit || '') + '</b></div>';
    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(min); input.max = String(max);
    input.step = String(n.step || 1); input.value = String(value);
    const out = box.querySelector('b');
    input.oninput = function () {
      out.textContent = input.value + (n.unit || '');
      box.dispatchEvent(new CustomEvent('au-slider', {
        bubbles: true, detail: { id: n.id || n.label, value: Number(input.value) },
      }));
    };
    box.appendChild(input);
    return box;
  };

  RENDERERS.quiz = function (n) {
    const box = el('div', 'au-quiz');
    box.appendChild(el('div', 'au-quiz-q', fmt(n.question)));
    const options = n.options || [];
    const list = el('div', 'au-quiz-options');
    let chosen = -1;
    options.forEach(function (opt, i) {
      const label = el('button', 'au-quiz-opt');
      label.type = 'button';
      label.innerHTML = '<span class="au-quiz-key">' + 'ABCDEFGH'[i] + '</span>' + fmt(opt.label);
      label.onclick = function () { if (!box.dataset.done) pick(i, list, options, box, n); };
      list.appendChild(label);
    });
    box.appendChild(list);
    const actions = el('div', 'au-quiz-actions');
    const submit = el('button', 'au-btn', '交卷');
    submit.type = 'button';
    submit.onclick = function () {
      if (chosen < 0) { submit.textContent = '先选一个答案'; return; }
      pick(chosen, list, options, box, n, true);
    };
    actions.appendChild(submit);
    box.appendChild(actions);
    box._pick = function (i) { chosen = i; };
    return box;
  };

  function pick(index, list, options, box, n, final) {
    const correctIdx = options.findIndex(function (o) { return o.correct === true; });
    if (final || correctIdx < 0) {
      box.dataset.done = '1';
      const buttons = list.querySelectorAll('.au-quiz-opt');
      buttons.forEach(function (btn, i) {
        btn.classList.add('au-locked');
        if (options[i].correct) btn.classList.add('au-right');
        else if (i === index) btn.classList.add('au-wrong');
      });
      const ok = index === correctIdx;
      const fb = el('div', 'au-quiz-result ' + (ok ? 'au-success' : 'au-warning'));
      let html = '<strong>' + (ok ? '✅ 答对了' : '🤔 再想想') + '</strong>';
      const chosenFb = options[index] && options[index].feedback;
      if (chosenFb) html += '<div>' + fmt(chosenFb) + '</div>';
      if (n.explanation) html += '<div class="au-quiz-expl">' + fmt(n.explanation) + '</div>';
      fb.innerHTML = html;
      box.appendChild(fb);
      list.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
      const submit = box.querySelector('.au-quiz-actions .au-btn');
      if (submit) submit.style.display = 'none';
      // 判分就地完成，但成绩要落到「学生画像」里 —— 否则老师下次不知道你哪里薄弱
      reportQuizResult(n.record, ok, fb);
    } else {
      list.querySelectorAll('.au-quiz-opt').forEach(function (b, i) {
        b.classList.toggle('au-selected', i === index);
      });
      box._pick(index);
    }
  }

  /** 把答题卡的结果回传后端，写进学生画像（前端判分 + 后端记档，各做各擅长的事）。
   *  没有 record 信息的题（例如模型随手写的单题互动）就跳过，不打扰。 */
  function reportQuizResult(record, correct, fbEl) {
    if (!record || !record.concept) return;
    const ctx = AgentUI._report || {};
    const base = ctx.base || '';
    const sessionId = record.session || ctx.sessionId || '';
    if (!sessionId) return;
    const body = { session_id: sessionId, concept: record.concept, correct: !!correct };
    fetch(base + '/agent/quiz_result', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (fbEl && d && typeof d.mastery === 'number') {
        const line = el('div', 'au-muted',
          '已记入学习档案 ·「' + record.concept + '」掌握度 ' + Math.round(d.mastery * 100) + '%' +
          (d.advice && d.advice.message ? ' · ' + d.advice.message : ''));
        fbEl.appendChild(line);
      }
    }).catch(function () { /* 记档失败不影响判分体验，静默忽略 */ });
  }

  RENDERERS.tabs = function (n) {
    const box = el('div', 'au-tabs');
    const head = el('div', 'au-tab-head');
    const body = el('div', 'au-tab-body');
    (n.tabs || []).forEach(function (tab, i) {
      const btn = el('button', 'au-tab-btn' + (i === 0 ? ' au-active' : ''), fmt(tab.label || ('标签' + (i + 1))));
      btn.type = 'button';
      const panel = el('div', 'au-tab-panel' + (i === 0 ? ' au-active' : ''));
      children(panel, tab.items);
      btn.onclick = function () {
        head.querySelectorAll('.au-tab-btn').forEach(function (b) { b.classList.remove('au-active'); });
        body.querySelectorAll('.au-tab-panel').forEach(function (p) { p.classList.remove('au-active'); });
        btn.classList.add('au-active'); panel.classList.add('au-active');
      };
      head.appendChild(btn); body.appendChild(panel);
    });
    box.appendChild(head); box.appendChild(body);
    return box;
  };

  RENDERERS.card = function (n) {
    const box = el('div', 'au-card');
    if (n.title) box.appendChild(el('div', 'au-card-title', fmt(n.title)));
    children(box, n.items);
    if (n.accent) box.style.borderColor = n.accent;
    return box;
  };

  RENDERERS.grid = function (n) {
    const box = el('div', 'au-grid');
    box.style.gridTemplateColumns = 'repeat(' + Math.max(1, Math.min(4, n.cols || 2)) + ', minmax(0,1fr))';
    children(box, n.items);
    return box;
  };
  RENDERERS.row = function (n) {
    const box = el('div', 'au-row');
    children(box, n.items);
    return box;
  };
  RENDERERS.col = function (n) {
    const box = el('div', 'au-col');
    children(box, n.items);
    return box;
  };

  RENDERERS.divider = function () { return el('hr', 'au-divider'); };
  RENDERERS.spacer = function (n) {
    const box = el('div');
    box.style.height = ((n.height || 12) + 'px');
    return box;
  };

  RENDERERS.frame = function (n) {
    const box = el('div', 'au-frame');
    const head = el('div', 'au-frame-head');
    if (n.title) head.appendChild(el('span', 'au-frame-title', fmt(n.title)));
    const actions = el('span', 'au-frame-actions');
    const reload = el('button', 'au-btn au-btn-mini', '重新加载');
    const zoom = el('button', 'au-btn au-btn-mini', '放大');
    const open = el('a', 'au-btn au-btn-mini', '新窗口打开');
    open.target = '_blank';
    open.rel = 'noreferrer';
    open.href = n.src;
    actions.appendChild(reload); actions.appendChild(zoom); actions.appendChild(open);
    head.appendChild(actions);
    box.appendChild(head);

    const viewport = el('div', 'au-frame-viewport');
    const iframe = document.createElement('iframe');
    // sandbox 里**故意不给 allow-same-origin**：生成页拿不到宿主 Cookie/DOM，只能靠 postMessage 沟通
    iframe.setAttribute('sandbox', 'allow-scripts allow-forms allow-popups allow-modals');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.style.height = ((n.height || 520) + 'px');
    iframe.src = n.src;
    viewport.appendChild(iframe);
    box.appendChild(viewport);

    // 实验类界面是「主舞台」，得能放大到整屏——否则小窗口里做实验很难受
    let zoomed = false;
    const applyZoom = function () {
      zoomed = !zoomed;
      box.classList.toggle('au-frame-zoomed', zoomed);
      zoom.textContent = zoomed ? '还原' : '放大';
      iframe.style.height = zoomed ? 'calc(100vh - 120px)' : ((n.height || 520) + 'px');
    };
    zoom.onclick = applyZoom;
    reload.onclick = function () {
      const src = iframe.src;
      iframe.src = 'about:blank';
      setTimeout(function () { iframe.src = src; }, 60);
    };

    // 底部拖拉条：自己决定这块界面占多高（老师给的默认高度不一定合你的屏幕）
    const grip = el('div', 'au-frame-grip', '拖动调整高度');
    grip.title = '上下拖动调整高度（双击恢复默认）';
    let gDragging = false, gStartY = 0, gStartH = 0;
    const defaultH = (n.height || 520);
    grip.addEventListener('mousedown', function (e) {
      if (zoomed) return;                       // 放大态铺满整屏，不需要调高
      gDragging = true;
      gStartY = e.clientY;
      gStartH = parseFloat(iframe.style.height) || defaultH;
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!gDragging) return;
      const next = Math.max(220, Math.min(window.innerHeight - 160, gStartH + (e.clientY - gStartY)));
      iframe.style.height = next + 'px';
    });
    window.addEventListener('mouseup', function () {
      if (!gDragging) return;
      gDragging = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    });
    grip.addEventListener('dblclick', function () {
      iframe.style.height = defaultH + 'px';
    });
    box.appendChild(grip);
    return box;
  };

  RENDERERS.image = function (n) {
    const box = el('figure', 'au-image');
    const img = document.createElement('img');
    img.src = n.src; img.alt = n.alt || '';
    img.loading = 'lazy';
    box.appendChild(img);
    if (n.alt) box.appendChild(el('figcaption', null, fmt(n.alt)));
    return box;
  };

  RENDERERS.audio = function (n) {
    const box = el('div', 'au-audio');
    const audio = document.createElement('audio');
    audio.controls = true; audio.src = n.src;
    box.appendChild(audio);
    if (n.alt) box.appendChild(el('div', 'au-caption', fmt(n.alt)));
    return box;
  };

  RENDERERS.json = function (n) {
    const box = el('div', 'au-code');
    box.appendChild(el('pre', null, esc(JSON.stringify(n.value, null, 2))));
    return box;
  };

  RENDERERS.mermaid = function (n) {
    if (window.mermaid && window.mermaid.render) {
      const box = el('div', 'au-mermaid');
      const id = 'au-mmd-' + Math.random().toString(36).slice(2);
      try {
        window.mermaid.render(id, n.code || '').then(function (res) { box.innerHTML = res.svg; });
        return box;
      } catch (e) { /* 落到代码块 */ }
    }
    return RENDERERS.code({ lang: 'mermaid', code: n.code || '' });
  };

  RENDERERS.accordion = function (n) {
    const box = el('div', 'au-accordion');
    (n.items || []).forEach(function (item) {
      const d = el('details');
      d.appendChild(el('summary', null, fmt(item.title)));
      const inner = el('div', 'au-acc-body');
      children(inner, item.items);
      d.appendChild(inner);
      box.appendChild(d);
    });
    return box;
  };

  RENDERERS.link = function (n) {
    const a = el('a', 'au-link', fmt(n.label));
    if (/^https?:/.test(n.href || '')) { a.href = n.href; a.target = '_blank'; a.rel = 'noreferrer'; }
    return a;
  };

  RENDERERS.copy = function (n) {
    const btn = el('button', 'au-btn au-btn-mini', fmt(n.label || '复制'));
    btn.onclick = function () {
      navigator.clipboard && navigator.clipboard.writeText(n.text || '');
      btn.textContent = '已复制';
    };
    return btn;
  };

  /* ------------------------------------------------------------------------
   * 画板（board）：分步累积作图，像老师写板书
   * 坐标是「数学坐标」（y 轴向上），渲染时映射到 SVG 屏幕坐标。
   * 第 n 步显示前 n 步的全部图形 —— 这与真实板书一致：边讲边加，而不是一次画完。
   * ---------------------------------------------------------------------- */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] != null) node.setAttribute(k, String(attrs[k]));
    });
    return node;
  }

  RENDERERS.board = function (n) {
    const box = el('div', 'au-board');
    if (n.title) box.appendChild(el('div', 'au-chart-title', fmt(n.title)));

    const W = 720;
    const H = Math.max(220, Math.min(720, Number(n.height) || 400));
    const reqX = (n.xRange && n.xRange.length === 2) ? n.xRange.map(Number) : [-5, 5];
    const reqY = (n.yRange && n.yRange.length === 2) ? n.yRange.map(Number) : [-3, 3];
    const pad = { l: 34, r: 16, t: 16, b: 26 };
    // 等比映射（equalAspect=true）：x、y 用同一个比例，圆才是圆、正方形才是正方形。
    // 用户实测「我画的圆变成了椭圆」就是这个原因——两轴独立填满画布必然变形。
    // 下面的 xr/yr 改用「实际可视范围」，后续图元计算全部基于它。
    const bmap = makeMapper(W, H, reqX, reqY, pad, true);
    const xr = bmap.xr;
    const yr = bmap.yr;
    const x2p = bmap.x2p;
    const y2p = bmap.y2p;
    const sx = bmap.xUnit;
    const sy = bmap.yUnit;

    const svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'au-board-svg', preserveAspectRatio: 'xMidYMid meet' });
    const layer = svgEl('g', {});
    svg.appendChild(layer);
    box.appendChild(svg);

    const steps = (n.steps && n.steps.length) ? n.steps : [{ caption: '', shapes: [] }];
    // 只有一步的「分步作图」根本不是分步：再摆一排「上一步 / 下一步（灰的）/ 第 1 / 1 步」
    // 只会让人以为界面坏了（用户实测原话：「这一块不就是不应该出现吗」）。
    // 一步 = 一张静态图，只留说明文字。
    const multi = steps.length > 1;
    const captionBar = el('div', 'au-board-caption');
    const bar = el('div', 'au-board-bar');
    const prevBtn = el('button', 'au-btn au-btn-mini', '← 上一步');
    const nextBtn = el('button', 'au-btn au-btn-mini', '下一步 →');
    const allBtn = el('button', 'au-btn au-btn-mini', '全部显示');
    const counter = el('span', 'au-board-counter', '');
    bar.appendChild(prevBtn); bar.appendChild(nextBtn); bar.appendChild(allBtn); bar.appendChild(counter);
    box.appendChild(captionBar);
    if (multi) box.appendChild(bar);
    else box.classList.add('au-board-static');

    let current = 0;

    function arrowHead(from, to, color) {
      const dx = to[0] - from[0], dy = to[1] - from[1];
      const len = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / len, uy = dy / len;
      const size = 11;
      const tipX = to[0], tipY = to[1];
      const baseX = tipX - ux * size, baseY = tipY - uy * size;
      const nx = -uy, ny = ux;
      return svgEl('polygon', {
        points: [tipX + ',' + tipY,
                 (baseX + nx * size * 0.42) + ',' + (baseY + ny * size * 0.42),
                 (baseX - nx * size * 0.42) + ',' + (baseY - ny * size * 0.42)].join(' '),
        fill: color || '#f87171',
      });
    }

    function drawShape(shape) {
      const kind = shape.kind;
      const color = shape.color || '#334155';
      const labelColor = shape.labelColor || '#1e293b';

      const addLabel = function (x, y, text, dx, dy) {
        if (!text) return;
        layer.appendChild(svgEl('text', {
          x: x + (dx || 6), y: y + (dy || -8), fill: labelColor,
          'font-size': 13, 'font-weight': 600, 'font-family': 'system-ui, sans-serif',
        })).textContent = String(text);
      };

      if (kind === 'axes') {
        const x0 = x2p(0), y0 = y2p(0);
        // 网格
        if (shape.grid !== false) {
          niceTicks(xr[0], xr[1], 8).forEach(function (v) {
            layer.appendChild(svgEl('line', { x1: x2p(v), y1: pad.t, x2: x2p(v), y2: H - pad.b, stroke: 'rgba(148,163,184,.22)', 'stroke-width': 1 }));
          });
          niceTicks(yr[0], yr[1], 6).forEach(function (v) {
            layer.appendChild(svgEl('line', { x1: pad.l, y1: y2p(v), x2: W - pad.r, y2: y2p(v), stroke: 'rgba(148,163,184,.22)', 'stroke-width': 1 }));
          });
        }
        layer.appendChild(svgEl('line', { x1: pad.l, y1: y0, x2: W - pad.r, y2: y0, stroke: '#94a3b8', 'stroke-width': 1.6 }));
        layer.appendChild(svgEl('line', { x1: x0, y1: pad.t, x2: x0, y2: H - pad.b, stroke: '#94a3b8', 'stroke-width': 1.6 }));
        // 刻度数字
        niceTicks(xr[0], xr[1], 8).forEach(function (v) {
          if (Math.abs(v) < 1e-9) return;
          const t = svgEl('text', { x: x2p(v), y: H - pad.b + 14, fill: '#94a3b8', 'font-size': 11, 'text-anchor': 'middle' });
          t.textContent = String(v);
          layer.appendChild(t);
        });
        niceTicks(yr[0], yr[1], 6).forEach(function (v) {
          if (Math.abs(v) < 1e-9) return;
          const t = svgEl('text', { x: pad.l - 6, y: y2p(v) + 4, fill: '#94a3b8', 'font-size': 11, 'text-anchor': 'end' });
          t.textContent = String(v);
          layer.appendChild(t);
        });
        addLabel(x2p(xr[1]), y0 - 6, shape.xLabel || 'x', -10, -4);
        addLabel(x0, y2p(yr[1]) + 4, shape.yLabel || 'y', 6, 0);
        return;
      }

      if (kind === 'hbar' || kind === 'vbar') {
        const attrs = { stroke: color, 'stroke-width': 1.2, 'stroke-dasharray': '6 4' };
        if (kind === 'hbar') {
          const y = y2p(Number(shape.y) || 0);
          layer.appendChild(svgEl('line', Object.assign({ x1: pad.l, y1: y, x2: W - pad.r, y2: y }, attrs)));
          addLabel(W - pad.r, y, shape.label, -30, -6);
        } else {
          const x = x2p(Number(shape.x) || 0);
          layer.appendChild(svgEl('line', Object.assign({ x1: x, y1: pad.t, x2: x, y2: H - pad.b }, attrs)));
          addLabel(x, pad.t, shape.label, 4, 14);
        }
        return;
      }

      if (kind === 'dot') {
        const p = shape.at || [0, 0];
        layer.appendChild(svgEl('circle', { cx: x2p(p[0]), cy: y2p(p[1]), r: 3.6, fill: color }));
        addLabel(x2p(p[0]), y2p(p[1]), shape.label);
        return;
      }

      if (kind === 'line' || kind === 'arrow' || kind === 'ray' || kind === 'measure') {
        const from = shape.from || [0, 0], to = shape.to || [0, 0];
        const p1 = [x2p(from[0]), y2p(from[1])], p2 = [x2p(to[0]), y2p(to[1])];
        const attrs = { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], stroke: color, 'stroke-width': shape.width || 2 };
        if (shape.dashed || kind === 'measure') attrs['stroke-dasharray'] = '6 4';
        layer.appendChild(svgEl('line', attrs));
        if (kind === 'arrow' || kind === 'ray') layer.appendChild(arrowHead(p1, p2, color));
        if (kind === 'measure') {
          // 尺寸标注：两端加小刻度线
          [[p1, p2], [p2, p1]].forEach(function (pair) {
            const dx = pair[1][0] - pair[0][0], dy = pair[1][1] - pair[0][1];
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            const nx = -dy / len * 6, ny = dx / len * 6;
            layer.appendChild(svgEl('line', {
              x1: pair[0][0] - nx, y1: pair[0][1] - ny, x2: pair[0][0] + nx, y2: pair[0][1] + ny,
              stroke: color, 'stroke-width': 1.6,
            }));
          });
        }
        addLabel((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2, shape.label, 4, -6);
        return;
      }

      if (kind === 'circle') {
        const c = shape.center || [0, 0];
        layer.appendChild(svgEl('ellipse', {
          cx: x2p(c[0]), cy: y2p(c[1]), rx: Math.abs((shape.r || 1) * sx), ry: Math.abs((shape.r || 1) * sy),
          fill: shape.fill || 'none', stroke: color, 'stroke-width': shape.width || 2,
        }));
        addLabel(x2p(c[0]) + Math.abs((shape.r || 1) * sx) * 0.7, y2p(c[1]), shape.label);
        return;
      }

      if (kind === 'polygon' || kind === 'rect') {
        const pts = kind === 'rect'
          ? (function () {
              const a = shape.from || [0, 0], b = shape.to || [0, 0];
              return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]];
            })()
          : (shape.points || []);
        if (pts.length < 3) return;
        const points = pts.map(function (p) { return x2p(p[0]) + ',' + y2p(p[1]); }).join(' ');
        layer.appendChild(svgEl('polygon', {
          points: points, fill: shape.fill || 'rgba(96,165,250,.12)', stroke: color, 'stroke-width': shape.width || 2,
        }));
        // 顶点标记
        pts.forEach(function (p, i) {
          const nm = (shape.pointLabels || [])[i];
          if (nm) addLabel(x2p(p[0]), y2p(p[1]), nm, 4, -6);
        });
        if (shape.label) {
          const cx = pts.reduce(function (a, p) { return a + p[0]; }, 0) / pts.length;
          const cy = pts.reduce(function (a, p) { return a + p[1]; }, 0) / pts.length;
          addLabel(x2p(cx), y2p(cy), shape.label, 0, 0);
        }
        return;
      }

      if (kind === 'arc') {
        const c = shape.center || [0, 0];
        const r = Number(shape.r) || 1;
        const a0 = (Number(shape.start) || 0) * Math.PI / 180;
        const a1 = (Number(shape.end) || 90) * Math.PI / 180;
        const segs = 24;
        let d = '';
        for (let i = 0; i <= segs; i++) {
          const a = a0 + (a1 - a0) * i / segs;
          const px = x2p(c[0] + r * Math.cos(a));
          const py = y2p(c[1] + r * Math.sin(a));
          d += (i ? ' L' : 'M') + px + ' ' + py;
        }
        layer.appendChild(svgEl('path', { d: d, fill: 'none', stroke: color, 'stroke-width': shape.width || 2 }));
        const mid = (a0 + a1) / 2;
        addLabel(x2p(c[0] + r * 1.25 * Math.cos(mid)), y2p(c[1] + r * 1.25 * Math.sin(mid)), shape.label, 0, 0);
        return;
      }

      if (kind === 'angle') {
        // 直角标记：在 at 点画一个小方块
        const at = shape.at || [0, 0], from = shape.from || [at[0] - 1, at[1]], to = shape.to || [at[0], at[1] + 1];
        const size = 0.32 * Math.min(Math.abs(xr[1] - xr[0]) / 10, Math.abs(yr[1] - yr[0]) / 10) || 0.3;
        const u1 = [Math.sign(at[0] - from[0]) || 1, 0];
        const u2 = [0, Math.sign(at[1] - from[1]) || 1];
        const p1 = [at[0] + u1[0] * size, at[1]];
        const p2 = [at[0] + u1[0] * size, at[1] + u2[1] * size];
        const p3 = [at[0], at[1] + u2[1] * size];
        const pts = [at, p1, p2, p3].map(function (p) { return x2p(p[0]) + ',' + y2p(p[1]); }).join(' ');
        layer.appendChild(svgEl('polygon', { points: pts, fill: 'none', stroke: color, 'stroke-width': 1.6 }));
        return;
      }

      if (kind === 'curve') {
        let compiled;
        try { compiled = compileExpr(shape.expr || 'x'); } catch (e) { return; }
        const range = (shape.xRange && shape.xRange.length === 2) ? shape.xRange : xr;
        const segs = 240;
        let d = '', started = false;
        for (let i = 0; i <= segs; i++) {
          const x = range[0] + (range[1] - range[0]) * i / segs;
          let y;
          try { y = compiled.eval({ x: x }); } catch (e) { y = NaN; }
          if (!isFinite(y)) { started = false; continue; }
          const px = x2p(x), py = y2p(y);
          if (py < -2000 || py > H + 2000) { started = false; continue; }   // 超出画布太远就断开
          d += (started ? ' L' : ' M') + px + ' ' + py;
          started = true;
        }
        layer.appendChild(svgEl('path', { d: d.trim(), fill: 'none', stroke: shape.color || '#60a5fa', 'stroke-width': shape.width || 2.4 }));
        if (shape.label) {
          const lx = x2p(range[1]);
          addLabel(lx, y2p(0), shape.label, -60, -8);
        }
        return;
      }

      if (kind === 'text') {
        const p = shape.at || [0, 0];
        const t = svgEl('text', {
          x: x2p(p[0]), y: y2p(p[1]), fill: color,
          'font-size': Number(shape.size) || 14, 'font-family': 'system-ui, sans-serif',
          'font-weight': shape.bold ? 700 : 400,
        });
        t.textContent = String(shape.text || '');
        layer.appendChild(t);
        return;
      }
    }

    function render() {
      while (layer.firstChild) layer.removeChild(layer.firstChild);
      for (let i = 0; i <= current && i < steps.length; i++) {
        (steps[i].shapes || []).forEach(drawShape);
      }
      const cap = steps[current] && steps[current].caption ? steps[current].caption : '';
      captionBar.innerHTML = multi
        ? '<b>第 ' + (current + 1) + ' / ' + steps.length + ' 步</b>' + (cap ? '　' + fmt(cap) : '')
        : fmt(cap);                    // 单步：直接写说明，不报「第 1 / 1 步」
      counter.textContent = '';
      prevBtn.disabled = current === 0;
      nextBtn.disabled = current >= steps.length - 1;
    }

    prevBtn.onclick = function () { if (current > 0) { current--; render(); } };
    nextBtn.onclick = function () { if (current < steps.length - 1) { current++; render(); } };
    allBtn.onclick = function () { current = steps.length - 1; render(); };
    setTimeout(render, 0);
    box._redraw = render;
    box._setHeight = function (h) {
      // 画板是 SVG，靠 preserveAspectRatio 等比缩放：给容器高度就能整体放大
      svg.style.height = h ? Math.max(140, h - 74) + 'px' : '';
      svg.style.width = '100%';
    };
    return box;
  };

  /** 渲染一个完整 UI 规格（{title, items:[...]}） */
  function renderSpec(spec) {
    const box = el('div', 'au-spec');
    if (!spec || !spec.items) return box;
    // 标题只显示一次：工具经常把同一个标题同时写在 spec 和组件上
    // （draw_board 就是这么干的），照字面渲染就是「圆的坐标几何」上下重复两遍。
    const items = spec.items || [];
    const dupTitle = spec.title && items.length === 1 && items[0] && items[0].title === spec.title;
    if (spec.title && !dupTitle) box.appendChild(el('div', 'au-spec-title', fmt(spec.title)));
    children(box, items);
    return box;
  }
  AgentUI.renderSpec = renderSpec;
  AgentUI.renderNode = renderNode;
  AgentUI.compileExpr = compileExpr;

  /* ==========================================================================
   * 五、样式（自带，全部 au- 前缀）
   * ======================================================================== */
  const CSS = `
  /* 配色与控件向 Windows 11 原生（Fluent）靠拢：Segoe UI 字体、浅色 Mica 底、
     白色卡片 + 1px 淡边框、8px 圆角、系统蓝 #0067c0、克制的浅阴影，
     观感目标是「像系统自带应用」，而不是网页模板。 */
  .au-root{--au-bg:#f3f3f3;--au-panel:#ffffff;--au-line:rgba(0,0,0,.08);--au-line-strong:rgba(0,0,0,.14);
    --au-text:#1b1b1b;--au-muted:#616161;--au-accent:#0067c0;--au-accent-soft:rgba(0,103,192,.10);
    --au-ok:#0f7b0f;--au-warn:#9d5d00;--au-err:#c42b1c;
    color:var(--au-text);
    font-family:"Segoe UI Variable Text","Segoe UI","Microsoft YaHei UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
    font-size:14.5px;line-height:1.7;display:flex;flex-direction:row;height:100%;min-height:0;background:var(--au-bg)}
  .au-root.dark{--au-bg:#202020;--au-panel:#272727;--au-line:rgba(255,255,255,.09);
    --au-text:#f2f2f2;--au-muted:#a0a0a0;--au-accent:#60a5fa;--au-accent-soft:rgba(96,165,250,.16)}
  /* 布局：**左对话 / 右工作台**。老师要弹界面时自动出现在右侧，不需要切换页面。
     还没生成任何界面时，工作台不占位置，宽度全给对话。 */
  .au-col-left{flex:0 0 368px;max-width:44%;min-width:272px;display:flex;flex-direction:column;
    min-height:0;background:var(--au-panel);border-right:1px solid var(--au-line)}
  .au-col-right{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;min-height:0;background:var(--au-bg)}
  .au-root:not(.au-has-surface) .au-col-left{flex:1 1 auto;max-width:none;border-right:none}
  .au-root:not(.au-has-surface) .au-col-right{display:none}
  /* 可拖拽分栏：用户自己决定左右比例（比例会记住，双击恢复默认）。
     命中区 10px + 正中间一个「抓手」小方块：原来只有 7px 宽、1px 淡线，
     用户根本看不见也抓不准 —— 实测反馈就是「我没办法把它拖动」。 */
  .au-splitter{flex:0 0 10px;cursor:col-resize;position:relative;background:var(--au-panel);
    z-index:6;touch-action:none}
  .au-splitter::after{content:'';position:absolute;top:0;bottom:0;left:50%;width:1px;margin-left:-.5px;
    background:var(--au-line-strong);transition:background .12s,width .12s,margin .12s}
  .au-splitter::before{content:'';position:absolute;top:50%;left:50%;width:4px;height:40px;
    margin:-20px 0 0 -2px;border-radius:2px;background:var(--au-line-strong);opacity:.55;
    transition:background .12s,opacity .12s}
  .au-splitter:hover::after,.au-splitter.au-dragging::after{background:var(--au-accent);width:2px;margin-left:-1px}
  .au-splitter:hover::before,.au-splitter.au-dragging::before{background:var(--au-accent);opacity:1}
  .au-splitter:focus{outline:none}
  .au-splitter:focus::after{background:var(--au-accent);width:2px;margin-left:-1px}
  /* 两栏模式（独立使用）下，工作台空的时候分栏条没意义 → 藏起来。
     三栏模式（.au-app）绝不能套这条规则：那时「还没有界面」是常态，
     一旦藏了就等于刚打开时一条都拖不动（踩过这个坑）。 */
  .au-root:not(.au-app):not(.au-has-surface) .au-splitter{display:none}
  .au-has-surface .au-col-left{border-right:none}   /* 有分栏条时不再重复画分界线 */

  /* ---------- 三栏工作台（左侧历史树 / 中间对话 / 右侧界面） ---------- */
  .au-app{background:var(--au-bg);overflow-x:auto;overflow-y:hidden}
  .au-nav{flex:0 1 262px;width:262px;min-width:180px;display:flex;flex-direction:column;
    min-height:0;background:var(--au-panel);border-right:1px solid var(--au-line)}
  .au-nav-head{display:flex;align-items:center;justify-content:space-between;gap:8px;
    padding:10px 12px;border-bottom:1px solid var(--au-line)}
  .au-nav-title{font-size:13.5px;font-weight:650}
  .au-tree{flex:1 1 auto;min-height:0;overflow:auto;padding:6px 6px 12px}
  .au-nav-empty{color:var(--au-muted);font-size:12.5px;padding:16px 10px;line-height:1.7}
  .au-nav-foot{border-top:1px solid var(--au-line);padding:8px 12px;display:flex;
    flex-direction:column;gap:6px}
  .au-nav-stat{font-size:11.5px;color:var(--au-muted)}
  .au-nav-meta{display:flex;align-items:center;gap:8px;justify-content:space-between}
  .au-model-chip{font-size:11.5px;color:var(--au-accent);background:var(--au-accent-soft);
    padding:2px 7px;border-radius:4px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .au-model-chip.bad{color:var(--au-err);background:rgba(196,43,28,.08)}
  .au-ver{font-size:11px;color:var(--au-muted)}
  /* 树：学科 / 教材 / 对话 */
  .au-folder{margin:2px 0}
  .au-folder > summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:6px;
    padding:5px 8px;border-radius:5px;font-size:13px;font-weight:600;color:var(--au-text)}
  .au-folder > summary::-webkit-details-marker{display:none}
  .au-folder > summary::before{content:'▸';font-size:10px;color:var(--au-muted);transition:transform .12s}
  .au-folder[open] > summary::before{transform:rotate(90deg)}
  .au-folder > summary:hover{background:rgba(0,0,0,.04)}
  .au-folder-2 > summary{font-weight:500;font-size:12.5px;color:var(--au-muted);padding-left:16px}
  .au-folder-2 > summary::before{content:'▸'}
  .au-folder-name{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .au-count{font-size:11px;color:var(--au-muted);background:rgba(0,0,0,.05);
    border-radius:9px;padding:0 6px;flex:0 0 auto}
  .au-session{display:flex;align-items:center;gap:6px;padding:5px 8px 5px 30px;border-radius:5px;
    cursor:pointer;font-size:12.8px;color:var(--au-text)}
  .au-session:hover{background:rgba(0,0,0,.04)}
  .au-session.au-active{background:var(--au-accent-soft);color:var(--au-accent);font-weight:600}
  .au-session-title{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .au-session-actions{display:none;gap:2px;flex:0 0 auto}
  .au-session:hover .au-session-actions{display:flex}
  .au-mini{border:none;background:transparent;cursor:pointer;color:var(--au-muted);
    font-size:12px;padding:1px 4px;border-radius:3px;line-height:1}
  .au-mini:hover{background:rgba(0,0,0,.08);color:var(--au-text)}
  .au-add{padding-left:30px;color:var(--au-muted);font-size:12.3px}
  /* 工作台里的每个工具窗口：底部一条拖拉条，自由调高度（双击恢复自适应） */
  .au-surface-item{display:flex;flex-direction:column}
  .au-surface-grip{height:12px;margin-top:6px;cursor:row-resize;display:flex;align-items:center;
    justify-content:center;font-size:10.5px;color:var(--au-muted);background:rgba(0,0,0,.025);
    border:1px solid var(--au-line);border-radius:4px;user-select:none}
  .au-surface-grip:hover{background:var(--au-accent-soft);color:var(--au-accent);
    border-color:rgba(0,103,192,.35)}
  .au-surface-item .au-spec{flex:1 1 auto;min-height:0}
  /* 右键菜单（IDE 习惯：空白处新建、文件夹上新建对话/重命名/删除） */
  .au-menu{position:fixed;z-index:9999;min-width:190px;padding:4px;background:var(--au-panel);
    border:1px solid var(--au-line-strong);border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,.16);
    font-size:13px;max-height:70vh;overflow:auto}
  .au-menu-item{display:flex;align-items:center;justify-content:space-between;gap:14px;
    padding:6px 10px;border-radius:4px;cursor:pointer;white-space:nowrap}
  .au-menu-item:hover{background:var(--au-accent-soft);color:var(--au-accent)}
  .au-menu-item.au-danger{color:var(--au-err)}
  .au-menu-item.au-danger:hover{background:rgba(196,43,28,.09)}
  .au-menu-item.au-disabled{color:var(--au-muted);cursor:default}
  .au-menu-item.au-disabled:hover{background:transparent;color:var(--au-muted)}
  .au-menu-hint{font-size:11px;color:var(--au-muted)}

  /* ---------- 设置中心（左下角「⚙ 设置」）----------
     注意：这个弹层挂在 document.body 上，**不在 .au-root 里面**，
     所以 CSS 变量（--au-panel 等定义在 .au-root 上）在这里取不到 ——
     必须写成 var(--x, 兜底值)，否则背景会变成透明的，底下的对话会透上来（实测踩过）。 */
  .au-set-btn{font:inherit;font-size:11.5px;padding:2px 8px;border-radius:999px;cursor:pointer;
    border:1px solid var(--au-line);background:#fff;color:var(--au-text)}
  .au-set-btn:hover{background:var(--au-accent-soft);color:var(--au-accent);border-color:rgba(0,103,192,.3)}
  .au-modal{position:fixed;inset:0;z-index:9998;background:rgba(20,28,45,.42);
    display:flex;align-items:center;justify-content:center}
  .au-modal-card{width:min(780px,94vw);max-height:88vh;display:flex;flex-direction:column;
    background:var(--au-panel,#fff);color:var(--au-text,#1b1b1b);
    border-radius:12px;box-shadow:0 24px 60px rgba(0,0,0,.34);overflow:hidden}
  .au-modal-head{display:flex;align-items:center;gap:10px;padding:12px 16px;
    border-bottom:1px solid var(--au-line,rgba(0,0,0,.08));font-weight:600}
  .au-modal-body{padding:14px 16px;overflow:auto;display:flex;flex-direction:column;gap:14px}
  .au-modal-foot{display:flex;align-items:center;gap:10px;padding:10px 16px;
    border-top:1px solid var(--au-line,rgba(0,0,0,.08));background:rgba(0,0,0,.02)}
  .au-set-group{border:1px solid var(--au-line,rgba(0,0,0,.08));border-radius:10px;padding:10px 12px}
  .au-set-title{font-weight:600;margin-bottom:2px}
  .au-set-hint{font-size:11.5px;color:var(--au-muted,#616161);margin-bottom:8px;line-height:1.5}
  .au-field{display:flex;align-items:center;gap:10px;margin:6px 0}
  .au-field>label{flex:0 0 170px;font-size:12.5px}
  .au-field input[type=text],.au-field input[type=number],.au-field input[type=password],.au-field select{
    flex:1;min-width:0;font:inherit;font-size:12.5px;padding:6px 9px;border-radius:7px;
    border:1px solid var(--au-line,rgba(0,0,0,.08));background:#fff;color:var(--au-text,#1b1b1b)}
  .au-field input[type=checkbox]{width:16px;height:16px}
  .au-field textarea{flex:1;min-width:0;min-height:110px;font:inherit;font-size:12.5px;
    padding:8px 10px;border-radius:7px;border:1px solid var(--au-line,rgba(0,0,0,.08));background:#fff;
    color:var(--au-text,#1b1b1b);line-height:1.6;resize:vertical}
  .au-set-note{font-size:12px;color:var(--au-text,#1b1b1b)}
  .au-menu-sep{height:1px;background:var(--au-line);margin:4px 6px}
  .au-inline-edit{width:100%;font:inherit;font-size:12.6px;padding:1px 4px;border:1px solid var(--au-accent);
    border-radius:3px;background:var(--au-panel);color:var(--au-text);outline:none}
  /* 中间栏的 flex-basis 必须是 0（不能用 auto）：auto = 按内容定基准尺寸，
     而聊天内容很长时基准尺寸会巨大，flex 收缩一算就把左右两栏压到最小宽度、
     自己独占屏幕（实测：1200 宽的工作台被挤成 240px、拖都拖不宽）。
     basis:0 + grow:1 = 「把剩下的空间全吃掉」，左右两栏就稳了。 */
  .au-main{flex:1 1 0;min-width:240px;display:flex;flex-direction:column;min-height:0}
  .au-side{flex:0 1 420px;width:420px;min-width:240px;display:flex;flex-direction:column;min-height:0;
    background:var(--au-bg)}
  /* 工作台标题条：一是说明「这是什么」，二是分栏线的备用抓手 ——
     万一鼠标/触控板拖不动，点右边的宽度按钮照样能调（用户实测反馈过「拖不动」）。 */
  .au-side-head{flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;gap:8px;
    padding:7px 10px 7px 14px;border-bottom:1px solid var(--au-line);background:var(--au-panel)}
  .au-side-title{font-size:12.5px;font-weight:650;color:var(--au-text)}
  .au-side-tools{display:flex;align-items:center;gap:6px;flex:0 0 auto}
  .au-wchip{font-size:11.5px;color:var(--au-muted);background:rgba(0,0,0,.04);border-radius:4px;
    padding:2px 7px;cursor:pointer;white-space:nowrap;border:1px solid transparent;font-variant-numeric:tabular-nums}
  .au-wchip:hover{color:var(--au-accent);background:var(--au-accent-soft);border-color:rgba(0,103,192,.25)}
  /* 三栏模式下聊天区不再限高，交给整体布局 */
  .au-app .au-chat{max-height:none}
  .au-app .au-col-left,.au-app .au-col-right{flex:none;max-width:none;border:none}
  /* 三栏模式（.au-app）**永远不改成上下堆叠**。这一条是血的教训：
     分栏线写的是 flex-basis + width；一旦容器变成竖向 flex，flex-basis 会被
     当成「高度」、width 会把面板撑成固定宽度不再拉伸 —— 于是拖那条「左右」分栏线
     变成改高度、面板还向右伸出去一格。用户的原话是「我没办法把他拖动，方向反了，
     他整体向右侧延申（因为我能看到他上下被缩放了）」，根因就在这。
     窗口窄就让它挤：三栏最小宽度合计约 620px，比这更窄才真的难看。 */
  @media (max-width:1000px){
    .au-nav{flex:0 1 200px;width:200px;min-width:150px}
    .au-main{min-width:200px}
    .au-side{flex:0 1 320px;width:320px;min-width:210px}
  }
  .au-chat{flex:1 1 auto;min-height:0;overflow:auto;padding:10px 14px}
  .au-stage{flex:1 1 auto;min-height:0;overflow:auto;padding:14px 16px;background:var(--au-bg)}
  .au-stage-empty{color:var(--au-muted);border:1px dashed var(--au-line-strong);border-radius:8px;
    padding:32px;text-align:center;font-size:13.5px;background:var(--au-panel);line-height:1.9}
  .au-input{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--au-line);background:var(--au-panel)}
  /* 窄屏（仅两栏独立模式）：改成上下堆叠，分栏线就没意义了。
     注意每条都带 :not(.au-app) —— 三栏工作台不吃这套（见上面的说明）。 */
  @media (max-width:900px){
    .au-root:not(.au-app){flex-direction:column}
    .au-root:not(.au-app) .au-col-left{flex:0 0 auto;max-width:none;min-width:0;border-right:none;border-bottom:1px solid var(--au-line)}
    .au-root:not(.au-app) .au-chat{max-height:34vh}
    .au-root:not(.au-app) .au-col-right{flex:1 1 auto;min-height:220px}
    .au-root:not(.au-app) .au-splitter{display:none}
  }
  /* 极窄窗口兜底：三栏本身横着滚，而不是把某一栏压没或叠起来 */
  @media (max-width:700px){
    .au-nav{flex:0 1 168px;width:168px;min-width:140px}
    .au-side{flex:0 1 260px;width:260px;min-width:190px}
  }
  .au-input textarea{flex:1;resize:none;min-height:46px;max-height:140px;padding:9px 12px;border-radius:6px;
    border:1px solid var(--au-line-strong);background:var(--au-panel);color:var(--au-text);font:inherit;outline:none;
    transition:border-color .12s, box-shadow .12s}
  .au-input textarea:focus{border-color:var(--au-accent);box-shadow:inset 0 -2px 0 0 var(--au-accent)}
  .au-btn{padding:8px 16px;border-radius:6px;border:1px solid var(--au-line-strong);background:var(--au-panel);
    color:var(--au-text);font:inherit;font-size:13.5px;cursor:pointer;text-decoration:none;
    transition:background .12s,border-color .12s}
  .au-btn:hover{background:rgba(0,0,0,.035)}
  .au-btn:disabled{opacity:.45;cursor:default}
  .au-btn.primary{background:var(--au-accent);border-color:var(--au-accent);color:#fff;font-weight:600}
  .au-btn.primary:hover{background:#1975c5}
  .au-btn-mini{padding:3px 10px;font-size:12px;border-radius:4px}
  .au-bubble{margin:9px 0;display:flex;gap:10px;align-items:flex-start}
  .au-bubble-who{flex:0 0 auto;font-size:11.5px;padding:2px 9px;border-radius:4px;background:var(--au-accent-soft);
    color:var(--au-accent);font-weight:600}
  .au-bubble.user .au-bubble-who{background:rgba(15,123,15,.12);color:var(--au-ok)}
  .au-bubble-body{flex:1;min-width:0}
  .au-bubble-body p{margin:.35em 0}
  .au-tool{font-size:12.5px;color:var(--au-muted);margin:5px 0;padding:6px 10px;border-radius:4px;
    background:rgba(0,0,0,.028);border:1px solid var(--au-line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .au-tool code{background:var(--au-accent-soft);padding:1px 5px;border-radius:3px;color:var(--au-accent)}
  .au-tool.err{border-color:rgba(196,43,28,.4);color:var(--au-err);background:rgba(196,43,28,.05)}
  .au-typing::after{content:'▍';animation:au-blink 1s steps(2) infinite;color:var(--au-accent)}
  @keyframes au-blink{50%{opacity:0}}
  .au-spec{display:flex;flex-direction:column;gap:12px}
  .au-spec-title{font-size:18px;font-weight:650;letter-spacing:-.01em}
  .au-h1{font-size:24px;font-weight:700;margin:.2em 0}
  .au-h2{font-size:19px;font-weight:650;margin:.2em 0}
  .au-h3{font-size:16px;font-weight:650;margin:.2em 0}
  .au-p{margin:.3em 0}
  .au-muted{color:var(--au-muted);font-size:13px}
  .au-caption{color:var(--au-muted);font-size:12.5px}
  .au-desc{display:block;color:var(--au-muted);font-size:13px}
  .au-list{margin:.3em 0;padding-left:1.2em}
  .au-list li{margin:.25em 0}
  .au-kv{display:grid;grid-template-columns:auto 1fr;gap:6px 16px}
  .au-kv-key{color:var(--au-muted);font-size:13.5px}
  .au-kv-val{font-weight:550}
  .au-table-wrap{overflow:auto;border:1px solid var(--au-line);border-radius:12px}
  .au-table{width:100%;border-collapse:collapse;font-size:14px}
  .au-table th,.au-table td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--au-line)}
  .au-table th{background:rgba(148,163,184,.08);font-weight:600;position:sticky;top:0}
  .au-table td.au-num{text-align:right;font-variant-numeric:tabular-nums}
  .au-bar{display:inline-block;width:72px;height:7px;border-radius:4px;background:rgba(148,163,184,.22);
    margin-right:8px;vertical-align:middle;overflow:hidden}
  .au-bar i{display:block;height:100%;background:var(--au-accent)}
  /* 聊天/正文里的 Markdown 块级元素（老师的回复天然是 Markdown） */
  .au-md-p{margin:.35em 0}
  .au-md-list{margin:.35em 0;padding-left:1.35em}
  .au-md-list li{margin:.2em 0}
  .au-md-h2{font-size:18px;font-weight:650;margin:.85em 0 .35em;letter-spacing:-.01em}
  .au-md-h3{font-size:16px;font-weight:650;margin:.8em 0 .3em}
  .au-md-h4{font-size:14.8px;font-weight:650;margin:.75em 0 .25em}
  .au-md-h5,.au-md-h6{font-size:14px;font-weight:600;margin:.7em 0 .25em}
  .au-md-h2:first-child,.au-md-h3:first-child,.au-md-h4:first-child,
  .au-md-h5:first-child,.au-md-h6:first-child,.au-md-p:first-child{margin-top:0}
  .au-md-hr{border:none;border-top:1px solid var(--au-line);margin:.9em 0}
  .au-md-code{background:#fafafa;border:1px solid var(--au-line);border-radius:6px;padding:9px 11px;
    overflow:auto;max-height:340px;margin:.5em 0}
  .au-md-code code{font-family:"Cascadia Mono",Consolas,"Courier New",monospace;font-size:12.8px;white-space:pre}
  blockquote{margin:.5em 0;padding:.3em 0 .3em 12px;border-left:3px solid var(--au-line-strong);color:var(--au-muted)}
  .au-md-table{overflow:auto;margin:.5em 0;border:1px solid var(--au-line);border-radius:6px}
  .au-md-table table{width:100%;border-collapse:collapse;font-size:13.5px}
  .au-md-table th,.au-md-table td{padding:6px 10px;border-bottom:1px solid var(--au-line);text-align:left}
  .au-md-table th{background:rgba(0,0,0,.03);font-weight:600}
  .au-md-table tr:last-child td{border-bottom:none}
  .au-steps{list-style:none;padding:0;margin:.3em 0;counter-reset:au-step}
  .au-step{display:flex;gap:10px;padding:9px 12px;border-radius:10px;border:1px solid transparent;margin:4px 0}
  .au-step-active{background:rgba(96,165,250,.10);border-color:rgba(96,165,250,.35)}
  .au-step-no{flex:0 0 22px;height:22px;border-radius:50%;background:rgba(148,163,184,.18);display:flex;
    align-items:center;justify-content:center;font-size:12px}
  .au-timeline{position:relative;padding-left:18px}
  .au-tl-item{position:relative;padding:6px 0 10px 12px;border-left:1px solid var(--au-line)}
  .au-tl-dot{position:absolute;left:-5px;top:12px;width:9px;height:9px;border-radius:50%;background:var(--au-accent)}
  .au-tl-time{display:block;color:var(--au-muted);font-size:12px}
  .au-callout{border-radius:12px;padding:12px 14px;border:1px solid var(--au-line);background:rgba(148,163,184,.07)}
  .au-callout-title{font-weight:650;margin-bottom:4px}
  .au-callout.au-info{border-color:rgba(96,165,250,.4);background:rgba(96,165,250,.10)}
  .au-callout.au-success{border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.10)}
  .au-callout.au-warning{border-color:rgba(251,191,36,.4);background:rgba(251,191,36,.10)}
  .au-callout.au-error{border-color:rgba(248,113,113,.4);background:rgba(248,113,113,.10)}
  .au-callout-body p{margin:.25em 0}
  .au-stat{display:inline-flex;flex-direction:column;gap:2px;padding:10px 16px;border-radius:12px;
    border:1px solid var(--au-line);background:rgba(148,163,184,.07);min-width:110px}
  .au-stat-label{font-size:12.5px;color:var(--au-muted)}
  .au-stat-value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
  .au-stat-delta{font-size:12.5px;color:var(--au-ok)}
  .au-progress-head{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px}
  .au-progress-track{height:9px;border-radius:6px;background:rgba(148,163,184,.2);overflow:hidden}
  .au-progress-track i{display:block;height:100%;background:linear-gradient(90deg,#3b82f6,#22d3ee)}
  .au-badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;
    background:rgba(96,165,250,.16);color:#93c5fd;border:1px solid rgba(96,165,250,.3)}
  .au-badge-success{background:rgba(52,211,153,.16);color:#6ee7b7;border-color:rgba(52,211,153,.3)}
  .au-badge-warn{background:rgba(251,191,36,.16);color:#fcd34d;border-color:rgba(251,191,36,.3)}
  .au-badge-danger{background:rgba(248,113,113,.16);color:#fca5a5;border-color:rgba(248,113,113,.3)}
  .au-code{border:1px solid var(--au-line);border-radius:8px;overflow:hidden;background:#fafafa}
  .au-code-head{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;
    font-size:12px;color:var(--au-muted);background:rgba(0,0,0,.03);border-bottom:1px solid var(--au-line)}
  .au-code pre{margin:0;padding:12px;overflow:auto;max-height:420px}
  .au-code code{font-family:"Cascadia Mono",Consolas,"Courier New",monospace;font-size:13px;white-space:pre}
  .au-tex{background:rgba(0,0,0,.05);padding:1px 5px;border-radius:4px;font-family:Consolas,monospace}
  .au-chart,.au-plot{border:1px solid var(--au-line);border-radius:8px;padding:10px;background:var(--au-panel)}
  /* 图形计算器：左栏表达式与常数、右栏画布（布局参考 Desmos） */
  .au-plot{display:flex;gap:14px;align-items:stretch}
  .au-plot-panel{flex:0 0 236px;min-width:0;display:flex;flex-direction:column;gap:8px}
  .au-plot-canvas{flex:1 1 auto;min-width:0}
  .au-plot-title{font-size:13.5px;font-weight:650}
  .au-expr-list{display:flex;flex-direction:column;gap:8px;overflow:auto;max-height:340px}
  .au-expr{border:1px solid var(--au-line);border-radius:6px;padding:6px 8px;background:rgba(0,0,0,.015)}
  .au-expr-head{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
  .au-expr-head i{width:12px;height:12px;border-radius:3px;display:inline-block;flex:0 0 auto}
  .au-expr-formula{font-size:14px}
  .au-expr-label{font-size:11.5px;color:var(--au-muted)}
  .au-const{display:flex;align-items:center;gap:6px;margin-top:5px;font-size:12.5px;color:var(--au-muted)}
  .au-const input{flex:1;min-width:56px}
  .au-const-name{font-family:Consolas,monospace;color:var(--au-text)}
  .au-const-val{font-variant-numeric:tabular-nums;color:var(--au-accent);min-width:36px;text-align:right}
  .au-view-info{font-size:11.5px;color:var(--au-muted);line-height:1.5}
  .au-plot-tools{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
  /* ---------- 图形计算器（calculator）---------- */
  .au-calc-panel{flex:0 0 272px}
  .au-calc-list{display:flex;flex-direction:column;gap:6px;overflow:auto;max-height:300px;padding-right:2px}
  .au-calc-row{display:grid;grid-template-columns:14px minmax(0,1fr) auto auto;gap:5px;align-items:center;
    border:1px solid var(--au-line);border-radius:6px;padding:4px 6px;background:var(--au-panel)}
  .au-calc-row:focus-within{border-color:var(--au-accent);box-shadow:0 0 0 2px var(--au-accent-soft)}
  .au-calc-row.au-bad{border-color:rgba(196,43,28,.5);background:rgba(196,43,28,.04)}
  .au-calc-dot{width:13px;height:13px;padding:0;border:1px solid rgba(0,0,0,.12);border-radius:3px;
    cursor:pointer;flex:0 0 auto}
  .au-calc-input{width:100%;min-width:0;font-family:Consolas,"Cascadia Mono",ui-monospace,monospace;
    font-size:12.6px;padding:3px 5px;border:1px solid transparent;border-radius:4px;background:transparent;
    color:var(--au-text);outline:none}
  .au-calc-input:hover{background:rgba(0,0,0,.03)}
  .au-calc-input::placeholder{color:var(--au-muted);font-family:inherit}
  .au-calc-err{grid-column:1/-1;font-size:11.5px;color:var(--au-err);line-height:1.5;display:none;padding:0 2px}
  .au-calc-row.au-bad .au-calc-err{display:block}
  .au-calc-add{display:flex;gap:6px;flex-wrap:wrap}
  .au-calc-params{display:flex;flex-direction:column;gap:4px;max-height:170px;overflow:auto}
  .au-calc-params .au-const input[type=range]{flex:1 1 auto;min-width:40px}
  .au-calc-params .au-const-num{flex:0 0 58px;font:inherit;font-size:12px;padding:1px 4px;border-radius:4px;
    border:1px solid var(--au-line-strong);background:var(--au-panel);color:var(--au-text);outline:none;
    font-variant-numeric:tabular-nums}
  .au-calc-hint{font-size:11.5px;color:var(--au-muted);line-height:1.5}
  .au-calc-coord{font-size:11.5px;color:var(--au-text);line-height:1.5;min-height:18px;
    font-variant-numeric:tabular-nums}
  .au-calc-wrap{position:relative}
  .au-calc-wrap .au-canvas{position:relative;z-index:1;border-radius:4px;background:#fff;
    border:1px solid var(--au-line)}
  .au-calc-overlay{position:absolute;left:1px;top:1px;pointer-events:none;z-index:2}
  .au-btn.au-on{border-color:var(--au-accent);color:var(--au-accent);background:var(--au-accent-soft)}
  /* ---------- 小工具：几何画板 / 数据统计台 / 方程求解器 ---------- */
  .au-geo,.au-datalab,.au-solver{border:1px solid var(--au-line);border-radius:8px;padding:10px;
    background:var(--au-panel);display:flex;flex-direction:column;gap:8px}
  .au-geo-title,.au-dl-title,.au-solver-title{font-size:13.5px;font-weight:650}
  .au-geo-stage,.au-dl-chart,.au-solver-chart{position:relative}
  .au-geo-tip{position:absolute;right:6px;top:4px;font-size:11px;color:var(--au-muted);
    background:rgba(255,255,255,.82);border-radius:4px;padding:1px 6px;pointer-events:none}
  .au-geo-panel{display:flex;flex-direction:column;gap:6px}
  .au-geo-list,.au-dl-stats,.au-solver-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
    gap:2px 14px}
  .au-geo-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;
    border-bottom:1px dashed var(--au-line);padding:2px 0;font-size:12.5px}
  .au-geo-key{color:var(--au-muted);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .au-geo-val{color:var(--au-text);font-variant-numeric:tabular-nums;white-space:nowrap}
  .au-geo-task{font-size:12.5px;color:var(--au-text);background:var(--au-accent-soft);
    border-radius:6px;padding:6px 9px;line-height:1.6}
  .au-dl-grid{display:flex;flex-wrap:wrap;gap:6px;max-height:150px;overflow:auto}
  .au-dl-cell{flex:0 0 auto}
  .au-dl-input{width:74px;font:inherit;font-size:12.5px;padding:4px 6px;border-radius:5px;
    border:1px solid var(--au-line-strong);background:var(--au-panel);color:var(--au-text);outline:none;
    font-variant-numeric:tabular-nums}
  .au-dl-input:focus{border-color:var(--au-accent);box-shadow:0 0 0 2px var(--au-accent-soft)}
  .au-solver-coef{display:flex;gap:14px;flex-wrap:wrap}
  .au-solver-coef-item{display:flex;align-items:center;gap:6px;font-size:13px}
  .au-solver-steps{display:flex;flex-direction:column;gap:3px}
  .au-solver-step{font-size:13px;line-height:1.7;color:var(--au-text)}
  .au-solver-note{font-size:12.5px;color:var(--au-warn)}
  /* 精确度选择器（刻度/读数保留几位小数） */
  .au-calc-prec{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--au-muted)}
  .au-calc-prec-sel{font:inherit;font-size:12px;padding:4px 4px;border-radius:6px;color:var(--au-text);
    border:1px solid var(--au-line-strong);background:var(--au-panel);cursor:pointer;outline:none;
    transition:border-color .12s}
  .au-calc-prec-sel:hover{border-color:var(--au-accent)}
  /* 算器自己窄的时候（工作台那一栏通常就是窄的）：公式在上、画布在下 */
  .au-calc.au-calc-narrow{flex-direction:column}
  .au-calc.au-calc-narrow .au-calc-panel{flex:0 0 auto;width:100%}
  .au-calc.au-calc-narrow .au-calc-list{max-height:186px}
  .au-calc.au-calc-narrow .au-calc-params{max-height:120px}
  .au-calc.au-calc-narrow .au-plot-canvas{width:100%}
  @media (max-width:820px){ .au-plot{flex-direction:column} .au-plot-panel{flex:0 0 auto} }
  .au-chart-title{font-size:13.5px;color:var(--au-muted);margin-bottom:6px}
  .au-canvas{width:100%;display:block}
  .au-plot-legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px;font-size:12.5px;color:var(--au-muted)}
  .au-plot-legend-item{display:flex;align-items:center;gap:6px}
  .au-plot-legend-item i{width:14px;height:3px;border-radius:2px;display:inline-block}
  .au-plot-legend-item code{color:var(--au-text);font-family:Consolas,monospace}
  .au-plot-legend-item em{font-style:normal;color:var(--au-accent)}
  .au-params{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px}
  .au-param{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--au-muted)}
  .au-param input{width:130px}
  .au-param b{color:var(--au-text);font-variant-numeric:tabular-nums}
  .au-quiz{border:1px solid var(--au-line);border-radius:12px;padding:14px;background:rgba(148,163,184,.06)}
  .au-quiz-q{font-weight:600;margin-bottom:10px}
  .au-quiz-opt{display:flex;gap:10px;align-items:center;width:100%;text-align:left;margin:6px 0;padding:9px 12px;
    border-radius:10px;border:1px solid var(--au-line);background:transparent;color:inherit;font:inherit;cursor:pointer}
  .au-quiz-opt:hover{border-color:var(--au-accent)}
  .au-quiz-key{flex:0 0 22px;height:22px;border-radius:50%;background:rgba(148,163,184,.2);display:flex;
    align-items:center;justify-content:center;font-size:12px}
  .au-quiz-opt.au-selected{border-color:var(--au-accent);background:rgba(96,165,250,.12)}
  .au-quiz-opt.au-right{border-color:var(--au-ok);background:rgba(52,211,153,.14)}
  .au-quiz-opt.au-wrong{border-color:var(--au-err);background:rgba(248,113,113,.14)}
  .au-quiz-opt.au-locked{cursor:default}
  .au-quiz-result{margin-top:10px;padding:10px 12px;border-radius:10px;border:1px solid var(--au-line)}
  .au-quiz-result.au-success{border-color:rgba(52,211,153,.45);background:rgba(52,211,153,.10)}
  .au-quiz-result.au-warning{border-color:rgba(251,191,36,.45);background:rgba(251,191,36,.10)}
  .au-quiz-expl{margin-top:6px;color:var(--au-muted);font-size:13.5px}
  .au-tabs{border:1px solid var(--au-line);border-radius:12px;overflow:hidden}
  .au-tab-head{display:flex;gap:4px;padding:6px;background:rgba(148,163,184,.08);flex-wrap:wrap}
  .au-tab-btn{padding:6px 12px;border-radius:8px;border:none;background:transparent;color:var(--au-muted);
    font:inherit;cursor:pointer}
  .au-tab-btn.au-active{background:rgba(96,165,250,.18);color:var(--au-text)}
  .au-tab-panel{display:none;padding:12px}
  .au-tab-panel.au-active{display:block}
  .au-card{border:1px solid var(--au-line);border-radius:12px;padding:14px;background:rgba(148,163,184,.06);
    display:flex;flex-direction:column;gap:10px}
  .au-card-title{font-size:16px;font-weight:650}
  .au-grid{display:grid;gap:12px}
  .au-row{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap}
  .au-col{display:flex;flex-direction:column;gap:10px;flex:1;min-width:0}
  .au-divider{border:none;border-top:1px solid var(--au-line);margin:6px 0}
  .au-frame{border:1px solid var(--au-line);border-radius:8px;overflow:hidden;background:var(--au-panel)}
  .au-frame-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 10px;
    font-size:12.5px;color:var(--au-muted);background:rgba(0,0,0,.03);border-bottom:1px solid var(--au-line)}
  .au-frame-actions{display:flex;gap:6px;flex:0 0 auto}
  .au-frame-title{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .au-frame-viewport{background:#fff}
  .au-frame iframe{width:100%;border:none;display:block}
  /* 放大：实验台铺满整屏，小窗口里做实验太憋屈 */
  .au-frame-zoomed{position:fixed;inset:12px;z-index:9999;margin:0;border-radius:8px;
    box-shadow:0 12px 40px rgba(0,0,0,.28);background:var(--au-panel);display:flex;flex-direction:column}
  .au-frame-zoomed .au-frame-viewport{flex:1;min-height:0}
  .au-frame-zoomed iframe{height:100% !important}
  .au-frame-grip{height:12px;cursor:row-resize;display:flex;align-items:center;justify-content:center;
    font-size:10.5px;color:var(--au-muted);background:rgba(0,0,0,.03);border-top:1px solid var(--au-line);
    user-select:none}
  .au-frame-grip:hover{background:var(--au-accent-soft);color:var(--au-accent)}
  .au-frame-zoomed .au-frame-grip{display:none}
  .au-image{margin:0}
  .au-image img{max-width:100%;border-radius:12px;border:1px solid var(--au-line)}
  .au-image figcaption{color:var(--au-muted);font-size:12.5px;margin-top:4px}
  .au-accordion details{border:1px solid var(--au-line);border-radius:10px;padding:8px 12px;margin:6px 0}
  .au-accordion summary{cursor:pointer;font-weight:600}
  .au-unknown{border:1px dashed var(--au-line);border-radius:10px;padding:10px;color:var(--au-muted);font-size:13px}
  .au-unknown-tag{background:rgba(148,163,184,.2);padding:1px 7px;border-radius:6px;margin-right:6px}
  .au-error{border:1px solid rgba(248,113,113,.45);border-radius:10px;padding:10px;color:#fca5a5;font-size:13px}
  .au-files{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
  .au-file{font-size:12.5px;padding:4px 10px;border-radius:999px;border:1px solid var(--au-line);
    background:rgba(148,163,184,.10);color:var(--au-text);text-decoration:none}
  .au-file:hover{border-color:var(--au-accent)}
  .au-state{font-size:12.5px;color:var(--au-muted);margin:8px 0;display:flex;align-items:center;
    justify-content:space-between;gap:10px}
  .au-state-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  /* 每个工作台窗口的标题栏：标题在左，**关闭按钮在右上角** */
  .au-surface-head{display:flex;align-items:center;justify-content:space-between;gap:8px;
    padding:2px 2px 6px;min-height:24px}
  .au-surface-title{font-size:13px;font-weight:650;color:var(--au-text);min-width:0;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .au-surface-close{flex:0 0 auto;width:24px;height:24px;padding:0;line-height:1;font-size:13px;
    display:flex;align-items:center;justify-content:center;border-radius:5px;cursor:pointer;
    border:1px solid transparent;background:transparent;color:var(--au-muted);
    transition:color .12s,background .12s,border-color .12s}
  .au-surface-close:hover{color:var(--au-err);background:rgba(196,43,28,.08);
    border-color:rgba(196,43,28,.35)}
  .au-board{border:1px solid var(--au-line);border-radius:8px;padding:10px;background:var(--au-panel)}
  .au-board-svg{width:100%;display:block;background:#fff;border-radius:4px;border:1px solid var(--au-line)}
  .au-board-caption{font-size:13.5px;color:var(--au-text);margin:8px 2px 6px;min-height:20px}
  .au-board-bar{display:flex;align-items:center;gap:8px}
  .au-board-counter{font-size:12px;color:var(--au-muted)}
  .au-board-bar .au-btn:disabled{opacity:.4;cursor:default}
  `;

  function injectCSS() {
    if (document.getElementById('au-styles')) return;
    const style = el('style');
    style.id = 'au-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  /* ==========================================================================
   * 六、事件流客户端（SSE over fetch POST）
   * ======================================================================== */
  /** 自动找到 Agent 服务在哪：同源优先，其次 127.0.0.1 / localhost 的 5001。
   *  为什么需要：页面可能从别的端口（老界面 5000）或本地文件打开，
   *  也可能服务根本没启动 —— 这时得给出一句人话，而不是「连不上 Agent 服务（）：Failed to fetch」。 */
  const CANDIDATE_BASES = ['', 'http://127.0.0.1:5001', 'http://localhost:5001'];

  async function detectBase() {
    for (let i = 0; i < CANDIDATE_BASES.length; i++) {
      const candidate = CANDIDATE_BASES[i];
      if (window.location.protocol === 'file:' && candidate === '') continue;
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(function () { ctrl.abort(); }, 2500);
        const resp = await fetch(candidate + '/agent/health', { signal: ctrl.signal });
        clearTimeout(timer);
        if (resp.ok) return candidate;
      } catch (err) { /* 试下一个 */ }
    }
    return null;
  }

  function createClient(opts) {
    let base = (opts && opts.base ? opts.base : '').replace(/\/$/, '');
    let controller = null;
    let sessionId = (opts && opts.sessionId) || '';

    async function send(message, handlers) {
      handlers = handlers || {};
      controller = new AbortController();
      const body = { message: message, session_id: sessionId };
      if (opts && opts.lessonFolder) body.lesson_folder = opts.lessonFolder;

      // 第一次发消息前先探一次服务地址；探不到就给可操作的提示（而不是技术错误码）
      if (!base && !createClient._probed) {
        createClient._probed = true;
        const found = await detectBase();
        if (found === null) {
          handlers.onError && handlers.onError(
            '连不上 Agent 服务。请确认它正在运行：双击项目目录里的 start_agent.cmd（或执行 py -3.14 run_agent.py），' +
            '看到「启动中 → http://127.0.0.1:5001/agent」字样后再试。');
          return;
        }
        base = found;
      }

      let resp;
      try {
        resp = await fetch(base + '/agent/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
      } catch (err) {
        handlers.onError && handlers.onError(
          '与 Agent 服务的连接断了（地址 ' + (base || window.location.origin) + '）。' +
          '如果服务已经关掉，双击 start_agent.cmd 重新启动，然后点「重试」。');
        return;
      }
      if (!resp.ok) {
        handlers.onError && handlers.onError('Agent 服务返回 ' + resp.status + '：' + (await resp.text()).slice(0, 300));
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        let chunk;
        try { chunk = await reader.read(); } catch (err) { break; }
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const evt = parseFrame(frame);
          if (!evt) continue;
          if (evt.data && evt.data.session_id) sessionId = evt.data.session_id;
          handlers.onEvent && handlers.onEvent(evt);
        }
      }
      handlers.onDone && handlers.onDone();
    }

    function parseFrame(frame) {
      const lines = frame.split('\n');
      let type = 'message';
      let data = '';
      lines.forEach(function (line) {
        if (line.indexOf('event:') === 0) type = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data += line.slice(5).trim();
      });
      if (!data) return null;
      try { return { type: type, data: JSON.parse(data) }; } catch (e) { return null; }
    }

    function cancel() {
      if (sessionId) {
        fetch(base + '/agent/cancel', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId }),
        }).catch(function () {});
      }
      if (controller) controller.abort();
    }

    return {
      send: send,
      cancel: cancel,
      get sessionId() { return sessionId; },
      set sessionId(v) { sessionId = v; },
      get base() { return base; },     // 探测出来的服务地址（答题卡回传成绩要用）
    };
  }

  /* ==========================================================================
   * 七、课堂视图：把事件流画成「对话 + 工具动作 + 界面」
   * ======================================================================== */
  function mountChat(root, opts) {
    injectCSS();
    opts = opts || {};
    const usePanels = !!(opts.panels && opts.panels.chat && opts.panels.surface);

    if (!usePanels) {
      root.classList.add('au-root');
      root.innerHTML = '';
    }

    // 布局有两种用法：
    //  A) 独立使用（默认）：两栏 —— 左对话、右工作台，中间一条可拖的分栏条。
    //  B) 被 mountApp 接管（opts.panels）：三栏外壳由外层搭好，这里只管把
    //     「聊天区」和「工作台」塞进它给的容器里（左侧历史栏是外层的事）。
    const left = usePanels ? opts.panels.chat : el('div', 'au-col-left');
    const right = usePanels ? opts.panels.surface : el('div', 'au-col-right');

    const stage = el('div', 'au-stage');
    const stageEmpty = el('div', 'au-stage-empty',
      '<b>工作台</b><br>老师生成的图表、推导、画板、虚拟实验都会出现在这里。<br>' +
      '在中间问点什么试试。' +
      (usePanels ? '<br>也可以点右上角 <b>＋ 计算器</b>，自己写公式画图（不用问模型）。' : ''));
    stage.appendChild(stageEmpty);

    const chat = el('div', 'au-chat');
    const inputBar = el('div', 'au-input');
    const textarea = document.createElement('textarea');
    textarea.placeholder = '问点什么…（Enter 发送，Shift+Enter 换行）';
    const sendBtn = el('button', 'au-btn primary', '发送');
    const stopBtn = el('button', 'au-btn', '停止');
    stopBtn.style.display = 'none';
    inputBar.appendChild(textarea);
    inputBar.appendChild(sendBtn);
    inputBar.appendChild(stopBtn);

    left.appendChild(chat);
    left.appendChild(inputBar);
    right.appendChild(stage);

    // 可拖拽分栏（仅在独立使用时生效；三栏模式由 mountApp 管自己的分栏条）
    let splitter = el('div', 'au-splitter');
    splitter.title = '拖动调整左右比例（双击恢复默认）';
    splitter.setAttribute('role', 'separator');
    splitter.setAttribute('aria-label', '调整对话区宽度');
    splitter.tabIndex = 0;

    if (!usePanels) {
      root.appendChild(left);
      root.appendChild(splitter);
      root.appendChild(right);
    } else {
      // 三栏模式：分栏条与容器都由 mountApp 提供，这里不复用
      splitter = null;
    }

    const DEFAULT_LEFT = 368;
    let leftWidth = null;
    // 三栏模式（usePanels）下 `left` 是**中间那栏**，它的宽度由两条分栏线决定，
    // 中栏永远当那条「弹性列」（flex:1 1 auto）把剩余空间吃掉。
    // 这里要是照搬两栏模式存下来的 au_left_width，就会给中栏写死 flex:0 0 368px ——
    // 三栏全变成固定宽，窗口一宽右边就空一大块，怎么拖都不对劲（真实踩过的坑）。
    if (usePanels) {
      left.style.flex = '1 1 0';       // basis 0：只吃「剩下的空间」，不跟左右两栏抢
      left.style.maxWidth = 'none';
    } else {
      try {
        const saved = parseFloat(localStorage.getItem('au_left_width'));
        if (saved >= 200) leftWidth = saved;
      } catch (e) { /* 隐私模式下 localStorage 可能不可用 */ }
    }

    function applyLeftWidth(w, persist) {
      if (usePanels) return;                 // 三栏模式交给 mountApp 的两条分栏线
      const total = root.clientWidth || 1200;
      if (w == null) {
        left.style.flex = '';
        left.style.maxWidth = '';
        leftWidth = null;
        if (persist) { try { localStorage.removeItem('au_left_width'); } catch (e) {} }
        return;
      }
      const clamped = Math.round(Math.max(220, Math.min(total * 0.75, w)));
      left.style.flex = '0 0 ' + clamped + 'px';
      left.style.maxWidth = 'none';
      leftWidth = clamped;
      if (persist) { try { localStorage.setItem('au_left_width', String(clamped)); } catch (e) {} }
    }
    if (leftWidth) applyLeftWidth(leftWidth, false);

    let dragging = false;
    if (splitter) splitter.addEventListener('mousedown', function (e) {
      dragging = true;
      splitter.classList.add('au-dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      const rect = root.getBoundingClientRect();
      applyLeftWidth(e.clientX - rect.left, false);
    });
    window.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      if (splitter) splitter.classList.remove('au-dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      applyLeftWidth(leftWidth, true);
    });
    if (splitter) splitter.addEventListener('dblclick', function () {
      left.style.flex = '';
      left.style.maxWidth = '';
      leftWidth = null;
      try { localStorage.removeItem('au_left_width'); } catch (e) {}
      applyLeftWidth(null, true);
    });
    // 键盘也能调（无障碍 + 手抖的时候更好用）
    if (splitter) splitter.addEventListener('keydown', function (e) {
      const step = e.shiftKey ? 48 : 16;
      if (e.key === 'ArrowLeft') { applyLeftWidth((leftWidth || DEFAULT_LEFT) - step, true); e.preventDefault(); }
      if (e.key === 'ArrowRight') { applyLeftWidth((leftWidth || DEFAULT_LEFT) + step, true); e.preventDefault(); }
    });

    // 右侧有内容时才占地方：空的时候把空间还给对话（避免大片空白）
    function syncColumns() {
      const hasSurface = right.querySelector('.au-surface-item') !== null;
      root.classList.toggle('au-has-surface', hasSurface);
      if (!hasSurface) {
        if (!usePanels) { left.style.flex = ''; left.style.maxWidth = ''; }
      } else if (leftWidth) {
        applyLeftWidth(leftWidth, false);
      }
      // 三栏模式：把「工作台有没有内容」告诉外层，由它决定右栏显隐
      if (typeof opts.onSurfaceChange === 'function') opts.onSurfaceChange(hasSurface);
    }
    syncColumns();

    const client = createClient(opts);
    let bubble = null;      // 当前正在写的助手气泡
    let uiCount = 0;

    // 让答题卡组件知道「找谁回传成绩」（base + 会话 id）
    function syncReportContext() {
      AgentUI._report = { base: client.base || '', sessionId: client.sessionId || '' };
    }
    syncReportContext();

    function addBubble(who, cls) {
      const wrap = el('div', 'au-bubble' + (cls ? ' ' + cls : ''));
      wrap.innerHTML = '<span class="au-bubble-who">' + esc(who) + '</span>';
      const body = el('div', 'au-bubble-body');
      wrap.appendChild(body);
      chat.appendChild(wrap);
      chat.scrollTop = chat.scrollHeight;
      return body;
    }

    function addTool(text, isError) {
      const row = el('div', 'au-tool' + (isError ? ' err' : ''), text);
      chat.appendChild(row);
      chat.scrollTop = chat.scrollHeight;
      return row;
    }

    function mountSurface(spec) {
      if (uiCount === 0) stage.innerHTML = '';
      uiCount++;
      const box = el('div', 'au-surface-item');

      // 每个窗口一条标题栏：左边标题，**右上角就是这个窗口的关闭按钮**。
      // （第一版我把关闭塞在底部的状态条里，用户要的是右上角 —— 跟 IDE / 系统窗口一致。）
      const head = el('div', 'au-surface-head');
      head.appendChild(el('span', 'au-surface-title',
        spec && spec.title ? fmt(spec.title) : ''));
      const closeBtn = el('button', 'au-surface-close', '✕');
      closeBtn.type = 'button';
      closeBtn.title = '关闭这个窗口（对话记录不受影响）';
      closeBtn.setAttribute('aria-label', '关闭这个窗口');
      head.appendChild(closeBtn);
      box.appendChild(head);

      let specEl = null;
      try {
        // 标题已经放进标题栏了，这里不要再渲染一遍。
        // 两种情况都要处理：
        //   ① spec 自己的 title（renderSpec 会画）→ 清掉；
        //   ② 组件自己的 title 和标题栏里那个**一模一样**（draw_board 就是同时发两份）
        //      → 也清掉；否则窗口里还是「圆的坐标几何」重复两遍（用户实测抱怨的就是这个）。
        const items0 = (spec && spec.items) || [];
        const cleanSpec = Object.assign({}, spec, { title: '' });
        if (spec && spec.title && items0.length === 1 && items0[0] &&
            items0[0].title === spec.title) {
          cleanSpec.items = [Object.assign({}, items0[0], { title: '' })];
        }
        specEl = renderSpec(cleanSpec);
        box.appendChild(specEl);
      } catch (err) {
        // 渲染失败也必须让学生看见「失败了」而不是一片空白 ——
        // 静默失败会让人以为是模型没画，其实是渲染错了。
        box.appendChild(el('div', 'au-error',
          '界面渲染失败：' + esc(err.message) + '<br><span class="au-muted">' +
          '（这是渲染器的问题，不是老师没画。把这句话发给我就能定位。）</span>'));
        console.error('AgentUI render error', err);
      }

      // 每个工具窗口都能拖高度（不只是网页/实验台）。
      // 图形计算器、画板这些的高度本来是 AI 定的，学生的屏幕不一定合适 ——
      // 拖到合适为止，双击恢复「按内容自适应」。
      const setInnerHeight = (function () {
        // 优先用组件自己提供的高度钩子（画布要按新高度重画，不能只改 CSS，
        // 否则就是一张被拉伸的糊图）
        if (specEl && typeof specEl._setHeight === 'function') return specEl._setHeight;
        const kids = (specEl && specEl.children) || [];
        for (let i = 0; i < kids.length; i++) {
          if (typeof kids[i]._setHeight === 'function') return kids[i]._setHeight;
        }
        return null;
      })();

      const grip = el('div', 'au-surface-grip', '拖动调整高度');
      grip.title = '上下拖动调整这个窗口的高度（双击恢复自适应）';
      let gDrag = false, gStartY = 0, gStartH = 0;
      grip.addEventListener('mousedown', function (e) {
        gDrag = true;
        gStartY = e.clientY;
        gStartH = (box.getBoundingClientRect && box.getBoundingClientRect().height) || 360;
        document.body.style.cursor = 'row-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
      });
      window.addEventListener('mousemove', function (e) {
        if (!gDrag) return;
        const maxH = Math.max(200, (window.innerHeight || 900) - 140);
        const next = Math.max(160, Math.min(maxH, gStartH + (e.clientY - gStartY)));
        box.style.height = next + 'px';
        box.style.overflow = 'auto';
        if (setInnerHeight) { try { setInnerHeight(next); } catch (err) { /* 忽略 */ } }
      });
      window.addEventListener('mouseup', function () {
        if (!gDrag) return;
        gDrag = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      });
      grip.addEventListener('dblclick', function () {
        box.style.height = '';
        box.style.overflow = '';
        if (setInnerHeight) { try { setInnerHeight(0); } catch (err) { /* 忽略 */ } }
      });
      box.appendChild(grip);

      const bar = el('div', 'au-state');
      bar.appendChild(el('span', 'au-state-text', '界面由 AI 现场生成 · 可以直接在上面操作'));
      box.appendChild(bar);

      // 关闭按钮在右上角（标题栏里）：上一节课的图会一直留在工作台里（新的插在最上面），
      // 学生想收掉旧的只能靠它 —— 不给出口就只能一直往下堆（用户实测抱怨过）。
      closeBtn.onclick = function () {
        if (box.parentNode) box.parentNode.removeChild(box);
        if (!right.querySelector('.au-surface-item')) {
          uiCount = 0;
          stage.appendChild(stageEmpty);      // 全关光了 → 把占位提示放回去
        }
        syncColumns();
      };
      stage.insertBefore(box, stage.firstChild);
      stage.scrollTop = 0;
      syncColumns();
    }

    function send() {
      const text = textarea.value.trim();
      if (!text) return;
      textarea.value = '';
      addBubble('我', 'user').innerHTML = fmtBlock(text);
      bubble = null;
      setBusy(true);
      client.send(text, {
        onEvent: function (evt) { handleEvent(evt); syncReportContext(); },
        onError: function (msg) { addTool(esc(msg), true); setBusy(false); },
        onDone: function () { setBusy(false); syncReportContext(); },
      });
    }

    // 「老师说画了、但界面没送到」的检测：这不是学术问题——用户实测遇到过，
    // 现象是「他说画了但是我没看到」，而且没有任何报错。
    // 最常见的原因是浏览器缓存了旧版渲染器（旧版没有这个组件/没有右侧工作台）。
    const UI_TOOLS = {
      render_ui: 1, plot_function: 1, draw_board: 1, show_artifact: 1,
      acid_base_lab: 1, render_scene: 1,
    };
    let expectedUi = false;
    let uiAtRunStart = 0;

    function handleEvent(evt) {
      try {
        handleEventInner(evt);
      } catch (err) {
        // 单个事件处理出错不能把整条事件流带走——否则后面的事件全部丢失，界面永远不出现
        console.error('AgentUI event error', evt, err);
        addTool('✗ 处理界面事件时出错：' + esc(err.message), true);
      }
    }

    function handleEventInner(evt) {
      const d = evt.data || {};
      switch (evt.type) {
        case 'run.start':
          expectedUi = false;
          uiAtRunStart = uiCount;
          addTool('模型：<code>' + esc(d.model) + '</code>');
          break;
        case 'text':
          if (!bubble) bubble = addBubble(opts.teacherName || '老师');
          bubble.classList.add('au-typing');
          bubble.innerHTML = fmtBlock((bubble.dataset.raw = (bubble.dataset.raw || '') + (d.delta || '')));
          chat.scrollTop = chat.scrollHeight;
          break;
        case 'tool.call':
          if (UI_TOOLS[d.name]) expectedUi = true;
          addTool('⚙ 调用 <code>' + esc(d.name) + '</code> ' +
            esc(JSON.stringify(d.args || {}).slice(0, 160)));
          break;
        case 'tool.result':
          addTool('↳ ' + esc(d.name) + ' 完成 · ' + (d.ms || 0) + 'ms · ' +
            esc(String(d.summary || '').replace(/\n/g, ' ').slice(0, 180)), !d.ok);
          break;
        case 'ui':
          mountSurface(d.spec || {});
          break;
        case 'artifact':
          addTool('📄 生成文件 <a class="au-file" target="_blank" href="' +
            esc((opts.base || '') + (d.url || '')) + '">' + esc(d.name || d.path) + '</a>');
          break;
        case 'notice':
          addTool('ⓘ ' + esc(d.message || ''));
          // 「已自动归档到 数学/9709」这类提示，左边的文件夹树必须跟着动，
          // 否则用户看到一句提示、树里却找不到，只会更困惑。
          if (String(d.message || '').indexOf('归档') >= 0) refreshTree();
          break;
        case 'error':
          addTool('✗ ' + esc(d.message || '出错了'), true);
          break;
        case 'run.end':
          if (bubble) bubble.classList.remove('au-typing');
          addTool('—— 本轮结束：' + esc(d.reason) + ' · ' + (d.steps || 0) + ' 步 · ' +
            Math.round((d.ms || 0) / 100) / 10 + 's ——');
          if (expectedUi && uiCount === uiAtRunStart) {
            addTool(
              '⚠ 老师调用了画图工具，但界面没有送到这个页面。<br>' +
              '最可能的原因：浏览器还在用**缓存的旧版渲染器**。请按 <b>Ctrl + F5</b>（强制刷新）再试一次；' +
              '如果还是这样，把这句话发给我。当前渲染器版本：<code>v' + esc(AgentUI.version) + '</code>',
              true);
          }
          bubble = null;
          break;
        default:
          break;
      }
    }

    function setBusy(busy) {
      sendBtn.disabled = busy;
      stopBtn.style.display = busy ? '' : 'none';
      sendBtn.textContent = busy ? '思考中…' : '发送';
    }

    sendBtn.onclick = send;
    stopBtn.onclick = function () { client.cancel(); setBusy(false); };
    textarea.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });

    return {
      send: function (text) { textarea.value = text; send(); },
      client: client,
      renderSpec: function (spec) { mountSurface(spec); },
      setSession: function (id) { client.sessionId = id; syncReportContext(); },
      // 给三栏外壳（mountApp）用的几个动作：清空聊天 / 回放历史 / 直接展示界面
      clearChat: function () {
        chat.innerHTML = '';
        bubble = null;
      },
      appendHistory: function (messages) {
        (messages || []).forEach(function (m) {
          if (!m || !(m.content || '').trim()) return;
          if (m.role === 'user') addBubble('我', 'user').innerHTML = fmtBlock(m.content);
          else if (m.role === 'assistant') addBubble(opts.teacherName || '老师').innerHTML = fmtBlock(m.content);
        });
        chat.scrollTop = chat.scrollHeight;
      },
      showUi: function (spec) { if (spec && spec.items) mountSurface(spec); },
      focusInput: function () { try { textarea.focus(); } catch (e) {} },
    };
  }

  /* ==========================================================================
   * 八、三栏工作台：左侧历史对话（学科 → 教材 → 对话）· 中间对话 · 右侧界面
   * --------------------------------------------------------------------------
   * 层级不是另建索引：会话自身带 subject / textbook 字段，树由后端聚合
   * （见 SessionStore.tree）。所以「删掉一个对话」树里自然就没了，不会有空目录。
   * 三条竖线都能拖：左栏宽度、左右分栏（独立模式）、右栏宽度。
   * ======================================================================== */
  function mountApp(root, opts) {
    injectCSS();
    opts = opts || {};
    root.classList.add('au-root', 'au-app');
    root.innerHTML = '';

    const nav = el('div', 'au-nav');
    const navHead = el('div', 'au-nav-head');
    navHead.appendChild(el('span', 'au-nav-title', '我的学习'));
    const newBtn = el('button', 'au-btn au-btn-mini', '＋ 新对话');
    navHead.appendChild(newBtn);
    const tree = el('div', 'au-tree');
    const navFoot = el('div', 'au-nav-foot');
    nav.appendChild(navHead);
    nav.appendChild(tree);
    nav.appendChild(navFoot);

    const main = el('div', 'au-main');
    const side = el('div', 'au-side');

    // 工作台标题条。要在 mountChat 之前挂上去，这样它排在 stage 上面。
    const sideHead = el('div', 'au-side-head');
    sideHead.appendChild(el('span', 'au-side-title', '工作台'));
    const sideTools = el('div', 'au-side-tools');
    // 「＋ 计算器」：学生自己就能开一台空的图形计算器，想画什么公式自己敲，
    // **不用等模型**。这就是「用户也能写函数并渲染」的入口 ——
    // 之前只有模型调用才会出现计算器，学生找不到它（实测反馈「为啥我没看出来」）。
    const calcBtn = el('button', 'au-btn au-btn-mini', '＋ 计算器');
    calcBtn.type = 'button';
    calcBtn.title = '打开一个空的图形计算器，自己写公式就能画（一行一个，如 x^2+y^2=16）';
    calcBtn.onclick = function () {
      chatApp.renderSpec({
        title: '图形计算器',
        items: [{
          type: 'calculator',
          title: '自己写公式（一行一个）：x^2+y^2=16 ／ y<=x+1 ／ (2,3) ／ "文字" ／ a=2',
          expressions: [],
          xMin: -10, xMax: 10, height: 430,
        }],
      });
      // 不用自己滚：mountSurface 是「新的插在最上面 + 滚动条归零」，新窗口自己就在眼前。
      // （我一开始多写了个 scrollIntoView(最后一个)，反而把视野滚回到底部那张旧图上了。）
    };
    sideTools.appendChild(calcBtn);
    // 这里以前有个「🐾 桌宠」按钮，用来开独立桌宠网页。学生说不需要单独网页展示，
    // 于是入口撤掉了（桌宠就活在界面右下角；形象/大小/语音全在左下角「⚙ 设置」里）。
    const sideChip = el('span', 'au-wchip', '⇔ 宽度');
    sideChip.title = '点一下换一档工作台宽度（也可以直接拖动左边那条竖线，双击恢复默认）';
    sideTools.appendChild(sideChip);
    sideHead.appendChild(sideTools);
    side.appendChild(sideHead);

    const splitL = el('div', 'au-splitter');
    splitL.title = '拖动调整历史栏宽度（双击恢复默认）';
    splitL.setAttribute('role', 'separator');
    splitL.setAttribute('aria-label', '调整历史栏宽度');
    const splitR = el('div', 'au-splitter');
    splitR.title = '拖动调整工作台宽度（双击恢复默认）';
    splitR.setAttribute('role', 'separator');
    splitR.setAttribute('aria-label', '调整工作台宽度');

    root.appendChild(nav);
    root.appendChild(splitL);
    root.appendChild(main);
    root.appendChild(splitR);
    root.appendChild(side);

    /* ======================================================================
     * 桌宠小玩偶 —— 贴在界面角落的小家伙，跟着上课动
     * ----------------------------------------------------------------------
     * 形象与「演绎动作」都在 pet_core.js（和 /agent/pet 那个大窗口共用一份实现）：
     *   上课开始 → 思考；出结果 → 高兴；提醒到点 → 一惊；讲完 → 说话（嘴动）
     * 模型自己没有动作文件（比如「白-免费版」）时，就走程序驱动参数的那条路。
     *
     * 为什么做成**能拖的小窗口**而不是固定在某个栏里：三栏的宽度学生自己会拖，
     * 钉死在哪一栏里迟早被挤变形；小玩偶该像桌面宠物一样想去哪儿去哪儿。
     * ==================================================================== */
    function mountPetDoll(hostRoot) {
      if (!window.PetCore) return null;                      // 核心没加载出来就当没有这个功能，别把界面拖坏
      const POS_KEY = 'au_pet_pos_v1';
      const MINI_KEY = 'au_pet_mini_v1';

      const box = el('div', 'au-pet');
      // **她身上和头顶都不许有控件**（学生要求：去掉脚下的「换形象 / 放大」）。
      // 想换形象 / 收起 / 改设置：**右键她**（菜单在光标处弹出），所有设置都在左下角「⚙ 设置」里。
      const stage = el('div', 'au-pet-stage');
      box.appendChild(stage);
      hostRoot.appendChild(box);

      // 默认位置：右下角，避开输入框
      function place(pos) {
        const w = box.offsetWidth || 186;
        const h = box.offsetHeight || 252;
        const x = Math.max(4, Math.min(window.innerWidth - w - 4, pos && pos.x != null ? pos.x : window.innerWidth - w - 18));
        const y = Math.max(4, Math.min(window.innerHeight - h - 4, pos && pos.y != null ? pos.y : window.innerHeight - h - 92));
        box.style.left = x + 'px';
        box.style.top = y + 'px';
      }
      let saved = null;
      try { saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null'); } catch (e) {}
      place(saved);

      // 拖动：**抓住她身上任何地方都能拖**（以前只有标题栏那条细边能拖，学生拽角色拽不动）
      let dragging = null;
      let moved = false;
      function onDown(ev) {
        if (ev.target.closest('button')) return;          // 按钮不参与拖动
        dragging = { dx: ev.clientX - box.offsetLeft, dy: ev.clientY - box.offsetTop };
        moved = false;
        try { box.setPointerCapture(ev.pointerId); } catch (e) {}
        ev.preventDefault();
      }
      function onMove(ev) {
        if (!dragging) return;
        const nx = ev.clientX - dragging.dx;
        const ny = ev.clientY - dragging.dy;
        if (Math.abs(nx - box.offsetLeft) > 3 || Math.abs(ny - box.offsetTop) > 3) moved = true;
        box.classList.add('au-pet--dragging');
        box.style.left = nx + 'px';
        box.style.top = ny + 'px';
      }
      function onUp() {
        if (!dragging) return;
        dragging = null;
        box.classList.remove('au-pet--dragging');
        if (!moved) {                                     // 没移动 = 摸了她一下
          if (mini) { mini = false; applyMini(); return; } // 收起状态点一下 = 展开
          if (pet) pet.tap();
          return;
        }
        place({ x: box.offsetLeft, y: box.offsetTop });     // 贴边时校正，别让她跑出屏幕
        try { localStorage.setItem(POS_KEY, JSON.stringify({ x: box.offsetLeft, y: box.offsetTop })); } catch (e) {}
      }
      // 监听器必须挂在**同一个**元素上，setPointerCapture 也要设在它身上：
      // 之前把 capture 设在 box、监听器挂在子元素（stage/bar）上，
      // 捕获后的事件只送到 box，子元素永远收不到 → 拖不动（实测踩到，抓角色纹丝不动）。
      // 挂在 box 上最省事：子元素上的 pointerdown 会冒泡到它，整只都能抓。
      box.addEventListener('pointerdown', onDown);
      box.addEventListener('pointermove', onMove);
      box.addEventListener('pointerup', onUp);
      box.addEventListener('pointercancel', onUp);
      window.addEventListener('resize', function () { place({ x: box.offsetLeft, y: box.offsetTop }); });

      // 收起 / 展开（收起后只剩一个小头像，点它回来）。没有按钮了，走右键菜单。
      let mini = false;
      try { mini = localStorage.getItem(MINI_KEY) === '1'; } catch (e) {}
      function applyMini() {
        box.classList.toggle('au-pet--collapsed', mini);
        try { localStorage.setItem(MINI_KEY, mini ? '1' : '0'); } catch (e) {}
      }
      applyMini();

      // 右键她 = 她的菜单（身上一个控件都不放，符合「不要在她身上/头顶加东西」）
      box.addEventListener('contextmenu', function (ev) {
        ev.preventDefault();
        const r = box.getBoundingClientRect();
        showMenu(ev.clientX || r.x, ev.clientY || r.y, [
          { label: '换个形象', hint: '也可以上传自己的', action: function () { openSettings('pet'); } },
          { label: mini ? '展开' : '收起', action: function () { mini = !mini; applyMini(); } },
          { label: '让她待机', action: function () { if (pet) pet.act('idle'); } },
          { sep: true },
          { label: '设置…', hint: '大小 / 形象 / 语音', action: function () { openSettings(); } },
        ]);
      });

      /* ---- 形象与演绎 ---- */
      let pet = null;
      let model = null;
      const ZOOM_KEY = 'au_pet_zoom_v1';

      function zoomFor(id) {
        try {
          const all = JSON.parse(localStorage.getItem(ZOOM_KEY) || '{}');
          return all[id] || 1;
        } catch (e) { return 1; }
      }
      function saveZoom(id, z) {
        try {
          const all = JSON.parse(localStorage.getItem(ZOOM_KEY) || '{}');
          all[id] = z;
          localStorage.setItem(ZOOM_KEY, JSON.stringify(all));
        } catch (e) {}
      }

      function status(kind) {
        const names = { live2d_local: 'Live2D（本机）', live2d_cdn: 'Live2D（网络）', sprite: '备用形象' };
        const label = (model && (model.label || model.name)) || '艾琳';
        box.title = label + ' · ' + (names[kind] || '加载中') + '（拖我 / 右键我）';
        box.dataset.petKind = kind;
        box.dataset.petModel = (model && model.id) || '';
      }

      async function bootPet() {
        if (pet) { pet.destroy(); pet = null; }
        stage.innerHTML = '';
        pet = PetCore.createPet(stage, {
          mode: 'doll', model: model, onStatus: status, speechSeconds: 6,
          dollZoom: zoomFor(model && model.id),
          speak: speakOn,                    // 设置里的「气泡自动念出来」
        });
        const kind = await pet.start(model);
        if (kind === 'sprite') pet.say('先用备用形象陪你');
        return pet;
      }

      // 滚轮缩放取景（每个模型记一份）：模型的留白各不相同，让人自己捏一下最省事
      stage.addEventListener('wheel', function (ev) {
        if (!pet) return;
        ev.preventDefault();
        const z = pet.setZoom(pet.zoom * (ev.deltaY < 0 ? 1.08 : 0.92));
        saveZoom(model && model.id, z);
      }, { passive: false });

      // 换形象：清单里拿到完整信息（动作组等）再重建，别只拿 id
      async function switchTo(current) {
        const cat = await PetCore.catalog().catch(function () { return null; });
        const info = cat && (cat.models || []).filter(function (m) { return m.id === current.id; })[0];
        model = info || current;
        await bootPet();
        if (pet) pet.say('换好啦：' + ((model && (model.label || model.name)) || ''));
        return model;
      }

      // 大小：设置里那个「桌宠大小」直接缩放整个框（形象跟着一起变）
      let scale = 1;
      // 语音：设置里「气泡自动念出来」+ 语音来源不是关 → 气泡念出来
      let speakOn = false;
      function setSpeak(on) {
        speakOn = !!on && !!window.PetCore;
        if (window.PetCore) PetCore.setSpeak(speakOn);
        if (pet) pet.setSpeak(speakOn);
        return speakOn;
      }
      function applyScale(s) {
        scale = Math.max(0.6, Math.min(2.6, Number(s) || 1));
        box.style.width = Math.round(210 * scale) + 'px';
        box.style.height = Math.round(318 * scale) + 'px';
        if (pet) { try { pet.fit(); } catch (e) {} }
        place({ x: box.offsetLeft, y: box.offsetTop });
        return scale;
      }
      // 点她一下（抚触反应）由上面的 onUp 统一处理：那里区分了「点一下」和「拖动」，
      // 这里不能再挂一个 click，否则拖完松手也会被当成点了一下。

      // 事件 → 说话 + 演一段（用服务端那条桌宠总线，和大窗口口径完全一致）
      PetCore.subscribe(function (ev) {
        if (!ev || !ev.type || !pet) return;
        if (ev.text) pet.say(ev.text);
        const kind = PetCore.actFor(ev);
        if (kind) pet.act(kind);
      });

      (async function init() {
        try {
          const cat = await PetCore.catalog();
          model = cat.current || (cat.models || [])[0] || null;
        } catch (e) {
          hint.textContent = '读不到形象清单';
        }
        await bootPet();
        // 大小 / 是否显示 / 要不要念出来：跟设置中心一致（设置里改完会调 applyScale / setSpeak）
        try {
          const st = await api('/agent/settings/all');
          const v = st.values || {};
          applyScale(v.pet_scale);
          applyVisible(v.pet_visible !== false);
          setSpeak(v.tts_auto_speak && String(v.tts_provider || 'off') !== 'off');
        } catch (e) { /* 设置读不到就用默认大小、不念 */ }
      })();

      function applyVisible(on) {
        box.style.display = on ? '' : 'none';
      }

      return {
        show: function (text) { if (pet) pet.say(text); },
        act: function (k) { if (pet) pet.act(k); },
        switchTo: switchTo,
        applyScale: applyScale,
        applyVisible: applyVisible,
        setSpeak: setSpeak,
        get speak() { return speakOn; },
        get scale() { return scale; },
        get pet() { return pet; },
        get model() { return model; },
        box: box,
      };
    }

    const petDoll = mountPetDoll(root);

    // 聊天与工作台交给 mountChat 管（三栏外壳由这里搭）。
    // **不要**再玩「工作台空就把分栏线藏起来」那一套：三栏模式下右栏本来就常驻
    // （空的时候显示占位提示），把分栏线藏掉只会让人**抓不到它** ——
    // 用户实测就是这么卡住的（我自己的测试还手动点亮了它，把 bug 掩盖了）。
    const chatApp = mountChat(root, Object.assign({}, opts, {
      panels: { chat: main, surface: side },
    }));

    /* ---------- 尺寸：每条分栏线都能拖，宽度记在浏览器里 ----------
       两条硬约束（否则会拖出「中间栏被挤没」的坏布局）：
         · 任一栏都不能超过「总宽 − 另一栏 − 中间栏最小宽 − 两条分栏线」
         · 中间栏始终保底 MIN_MAIN 像素
       另外：这里只写「宽度」（flex-basis + width + min-width），**不写高度**。
       分栏线只在横向 flex 容器里有意义 —— 见 CSS 里为什么三栏模式永不堆叠。 */
    const MIN_MAIN = 240;
    const SPLIT_W = 10;                   // 与 .au-splitter 的 flex-basis 保持一致
    const widthOf = function (elm, fallback) {
      const rect = elm.getBoundingClientRect ? elm.getBoundingClientRect() : null;
      const w = (rect && rect.width) || elm.clientWidth || 0;
      return w > 0 ? w : fallback;
    };

    function makeResizable(handle, target, storageKey, defaults, side_) {
      const saved = parseFloat((function () { try { return localStorage.getItem(storageKey); } catch (e) { return null; } })());
      const otherWidth = function () {
        return side_ === 'right'
          ? widthOf(nav, 262)          // 拖右栏时，要避开左栏
          : widthOf(side, 420);        // 拖左栏时，要避开右栏（右栏现在常驻）
      };
      const apply = function (w, persist) {
        if (w == null) {
          target.style.flex = '';
          target.style.width = '';
          target.style.minWidth = '';
          if (persist) { try { localStorage.removeItem(storageKey); } catch (e) {} }
          return;
        }
        const total = root.clientWidth || 1400;
        const upper = Math.max(defaults.min, total - otherWidth() - MIN_MAIN - 2 * SPLIT_W);
        const clamped = Math.round(Math.max(defaults.min, Math.min(upper, w)));
        // flex-shrink=1（不是 0）：窗口比「记住的宽度之和」还窄时，两栏要能一起让位，
        // 而不是顶着不缩、把中间栏挤出去逼出横向滚动条。
        // grow=0 所以宽窗口下不会超过这个宽度，等于 width 就是上限。
        target.style.flex = '0 1 ' + clamped + 'px';
        target.style.width = clamped + 'px';
        // 显式写出 min-width，免得 CSS 里的 min-width 和这里算出的值打架
        target.style.minWidth = defaults.min + 'px';
        if (persist) { try { localStorage.setItem(storageKey, String(clamped)); } catch (e) {} }
      };
      if (saved >= defaults.min) apply(saved, false);
      let dragging = false;
      let startX = 0, startW = 0;
      const endDrag = function () {
        if (!dragging) return;
        dragging = false;
        handle.classList.remove('au-dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        apply(widthOf(target, defaults.min), true);
      };
      const onMove = function (clientX) {
        if (!dragging) return;
        const delta = clientX - startX;
        apply(side_ === 'right' ? startW - delta : startW + delta, false);
      };
      const begin = function (clientX) {
        dragging = true;
        startX = clientX;
        startW = widthOf(target, defaults.min);
        handle.classList.add('au-dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
      };
      // 有 Pointer Events 就用它（自带指针捕获：鼠标划出窗口也不丢），
      // 没有就退回 mouse —— 两条路都留，老浏览器/测试桩都能跑。
      if (typeof window !== 'undefined' && 'PointerEvent' in window) {
        handle.addEventListener('pointerdown', function (e) {
          begin(e.clientX);
          try { handle.setPointerCapture(e.pointerId); } catch (err) { /* 桩环境没有这个方法 */ }
          e.preventDefault();
        });
        handle.addEventListener('pointermove', function (e) { onMove(e.clientX); });
        handle.addEventListener('pointerup', endDrag);
        handle.addEventListener('pointercancel', endDrag);
      } else {
        handle.addEventListener('mousedown', function (e) { begin(e.clientX); e.preventDefault(); });
        window.addEventListener('mousemove', function (e) { onMove(e.clientX); });
        window.addEventListener('mouseup', endDrag);
      }
      handle.addEventListener('dblclick', function () { apply(null, true); });
      // 键盘也能调（无障碍 + 手抖时更好用）
      handle.tabIndex = 0;
      handle.addEventListener('keydown', function (e) {
        const step = e.shiftKey ? 48 : 16;
        const cur = widthOf(target, defaults.min);
        if (e.key === 'ArrowLeft') { apply(side_ === 'right' ? cur + step : cur - step, true); e.preventDefault(); }
        if (e.key === 'ArrowRight') { apply(side_ === 'right' ? cur - step : cur + step, true); e.preventDefault(); }
      });
      return { apply: apply, defaults: defaults };
    }
    makeResizable(splitL, nav, 'au_nav_w', { min: 180, maxRatio: 0.5 }, 'left');
    const sideResize = makeResizable(splitR, side, 'au_side_w', { min: 240, maxRatio: 0.8 }, 'right');

    // 分栏线的备用抓手：点一下在几档宽度之间循环。
    // 拖拽万一因为设备/浏览器原因不好用，这里一定能用（而且它本身就提示了「能调宽度」）。
    const SIDE_PRESETS = [320, 420, 560, 720];
    const labelSideChip = function () {
      sideChip.textContent = '⇔ ' + Math.round(widthOf(side, 420)) + 'px';
    };
    sideChip.addEventListener('click', function () {
      const cur = widthOf(side, 420);
      let next = SIDE_PRESETS.find(function (w) { return w > cur + 8; });
      if (next == null) next = SIDE_PRESETS[0];      // 已经最宽 → 回到最窄
      sideResize.apply(next, true);
      labelSideChip();
    });
    labelSideChip();
    sideChip.addEventListener('mouseenter', labelSideChip);
    // 拖完分栏线也把按钮上的数字刷一下（不然它还写着旧宽度，看着像没生效）
    document.addEventListener('mouseup', labelSideChip);
    document.addEventListener('pointerup', labelSideChip);

    /* ---------- 后端接口 ---------- */
    async function api(path, options) {
      const o = options || {};
      const base = chatApp.client.base || '';
      const init = { method: o.method || 'GET', headers: { 'Content-Type': 'application/json' } };
      if (o.body) init.body = JSON.stringify(o.body);
      const resp = await fetch(base + path, init);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }

    /* ---------- 右键菜单 / 就地改名（照 IDE 的习惯） ---------- */
    let openMenuEl = null;

    function closeMenu() {
      if (openMenuEl && openMenuEl.parentNode) openMenuEl.parentNode.removeChild(openMenuEl);
      openMenuEl = null;
      document.removeEventListener('mousedown', onDocDown, true);
      document.removeEventListener('keydown', onMenuKey, true);
    }
    function onDocDown(e) { if (openMenuEl && !openMenuEl.contains(e.target)) closeMenu(); }
    function onMenuKey(e) { if (e.key === 'Escape') closeMenu(); }

    function showMenu(x, y, items) {
      closeMenu();
      const menu = el('div', 'au-menu');
      items.forEach(function (it) {
        if (!it || it.sep) { menu.appendChild(el('div', 'au-menu-sep')); return; }
        const row = el('div', 'au-menu-item' + (it.danger ? ' au-danger' : '') + (it.disabled ? ' au-disabled' : ''));
        row.innerHTML = '<span>' + esc(it.label) + '</span>' +
          (it.hint ? '<span class="au-menu-hint">' + esc(it.hint) + '</span>' : '');
        if (!it.disabled) {
          row.addEventListener('click', function (e) {
            e.stopPropagation();
            closeMenu();
            try { it.action(); } catch (err) { console.error(err); }
          });
        }
        menu.appendChild(row);
      });
      document.body.appendChild(menu);
      // 贴着鼠标，但别超出视口
      const rect = menu.getBoundingClientRect ? menu.getBoundingClientRect() : { width: 180, height: items.length * 28 };
      const left = Math.max(4, Math.min(x, (window.innerWidth || 1200) - (rect.width || 180) - 8));
      const top = Math.max(4, Math.min(y, (window.innerHeight || 800) - (rect.height || 200) - 8));
      menu.style.left = left + 'px';
      menu.style.top = top + 'px';
      openMenuEl = menu;
      // 延迟绑定：否则这次右键本身会把菜单立刻关掉
      setTimeout(function () {
        document.addEventListener('mousedown', onDocDown, true);
        document.addEventListener('keydown', onMenuKey, true);
      }, 0);
      return menu;
    }

    /** 就地改名（像 IDE 里按 F2）：Enter 提交、Esc 取消、失焦提交 */
    function inlineEdit(host, initial, onCommit) {
      const input = document.createElement('input');
      input.className = 'au-inline-edit';
      input.value = initial || '';
      const original = host.innerHTML;
      host.innerHTML = '';
      host.appendChild(input);
      try { input.focus(); input.select(); } catch (e) {}
      let done = false;
      const finish = function (save) {
        if (done) return;
        done = true;
        const value = (input.value || '').trim();
        host.innerHTML = original;                       // 先恢复，避免提交失败时界面空白
        if (save && value && value !== initial) onCommit(value);
      };
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        e.stopPropagation();
      });
      input.addEventListener('blur', function () { finish(true); });
      input.addEventListener('click', function (e) { e.stopPropagation(); });
    }

    /* ---------- 历史树 ---------- */
    let currentId = '';

    async function createFolder(subject, textbook) {
      try {
        await api('/agent/folders', { method: 'POST', body: { subject: subject || '', textbook: textbook || '' } });
        await refreshTree();
      } catch (err) { console.error(err); }
    }

    async function renameFolder(subject, textbook, which, nameEl) {
      // which: 'subject' = 改学科名（该学科下所有教材一起搬）；'textbook' = 只改教材名
      const apply = async function (value) {
        const body = which === 'subject'
          ? { subject: subject, textbook: textbook, new_subject: value }
          : { subject: subject, textbook: textbook, new_textbook: value };
        try { await api('/agent/folders/rename', { method: 'POST', body: body }); } catch (err) { console.error(err); }
        await refreshTree();
      };
      if (nameEl) {                                   // 就地改名（像 IDE 按 F2）
        inlineEdit(nameEl, which === 'subject' ? subject : textbook, apply);
        return;
      }
      const label = which === 'subject' ? '学科名' : '教材名';
      const typed = window.prompt('新的' + label + '：', which === 'subject' ? subject : textbook);
      if (typed != null && typed.trim()) apply(typed.trim());
    }

    async function deleteFolder(subject, textbook) {
      const inside = [];
      try {
        const data = await api('/agent/sessions');
        (data.tree || []).forEach(function (s) {
          if (s.name !== subject) return;
          (s.textbooks || []).forEach(function (b) {
            if (b.name === textbook) inside.push(b.count);
          });
        });
      } catch (err) { /* 数不出来就不提数量 */ }
      const n = inside.reduce(function (a, b) { return a + b; }, 0);
      const msg = '删除文件夹「' + subject + ' / ' + textbook + '」？' +
        (n ? '\n里面的 ' + n + ' 个对话会一起删除，无法恢复。' : '\n（这是一个空文件夹）');
      if (!window.confirm(msg)) return;
      try {
        await api('/agent/folders?subject=' + encodeURIComponent(subject) +
          '&textbook=' + encodeURIComponent(textbook), { method: 'DELETE' });
      } catch (err) { console.error(err); }
      await refreshTree();
    }

    function folderMenu(e, subject, textbook, isSubject) {
      e.preventDefault();
      e.stopPropagation();
      const nameEl = e.currentTarget ? e.currentTarget.querySelector('.au-folder-name') : null;
      const items = [];
      if (isSubject) {
        items.push({ label: '新建教材…', hint: '在此学科下', action: function () {
          const name = window.prompt('新教材名字：', '');
          if (name && name.trim()) createFolder(subject, name.trim());
        } });
        items.push({ label: '新建对话', hint: '先选教材', disabled: true });
        items.push({ sep: true });
        items.push({ label: '重命名学科（F2）', action: function () { renameFolder(subject, textbook, 'subject', nameEl); } });
      } else {
        items.push({ label: '新建对话', hint: '在此教材下', action: function () { newSession(subject, textbook); } });
        items.push({ label: '新建教材…', hint: '同一学科下', action: function () {
          const name = window.prompt('新教材名字：', '');
          if (name && name.trim()) createFolder(subject, name.trim());
        } });
        items.push({ sep: true });
        items.push({ label: '重命名教材（F2）', action: function () { renameFolder(subject, textbook, 'textbook', nameEl); } });
      }
      items.push({ sep: true });
      items.push({ label: '删除文件夹', danger: true, action: function () { deleteFolder(subject, textbook); } });
      showMenu(e.clientX, e.clientY, items);
    }

    function sessionMenu(e, sess, subject, textbook) {
      e.preventDefault();
      e.stopPropagation();
      showMenu(e.clientX, e.clientY, [
        { label: '打开', action: function () { openSession(sess.id); } },
        { label: '重命名…', hint: 'F2', action: function () { renameSession(sess); } },
        { label: '移动到…', action: function () { moveSession(sess); } },
        { sep: true },
        { label: '删除对话', danger: true, action: function () { deleteSession(sess); } },
      ]);
    }

    function sessionItem(sess, subject, textbook) {
      const item = el('div', 'au-session' + (sess.id === currentId ? ' au-active' : ''));
      item.dataset.sessionId = sess.id;
      item.innerHTML =
        '<span class="au-session-title" title="' + esc(sess.title || '新对话') + '">' +
        esc(sess.title || '新对话') + '</span>' +
        '<span class="au-session-actions">' +
        '<button class="au-mini" data-act="rename" title="重命名">✎</button>' +
        '<button class="au-mini" data-act="delete" title="删除">✕</button></span>';
      item.addEventListener('click', function (e) {
        const act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
        if (act === 'rename') { e.stopPropagation(); renameSession(sess); return; }
        if (act === 'delete') { e.stopPropagation(); deleteSession(sess); return; }
        openSession(sess.id);
      });
      item.addEventListener('dblclick', function (e) { e.stopPropagation(); renameSession(sess); });
      item.addEventListener('contextmenu', function (e) { sessionMenu(e, sess, subject, textbook); });
      return item;
    }

    function renderTree(subjects) {
      tree.innerHTML = '';
      if (!subjects.length) {
        const empty = el('div', 'au-nav-empty',
          '还没有内容。<br><b>在空白处点右键</b>可以新建学科（文件夹）。');
        tree.appendChild(empty);
        return;
      }
      subjects.forEach(function (subj) {
        const d1 = el('details', 'au-folder');
        d1.open = true;
        d1.dataset.folder = subj.name + '\u0000';
        const s1 = el('summary');
        s1.dataset.folder = subj.name + '\u0000';
        s1.innerHTML = '<span class="au-folder-name">' + esc(subj.name) + '</span>' +
          '<span class="au-count">' + subj.count + '</span>';
        s1.addEventListener('contextmenu', function (e) { folderMenu(e, subj.name, '', true); });
        d1.appendChild(s1);
        (subj.textbooks || []).forEach(function (book) {
          const d2 = el('details', 'au-folder au-folder-2');
          d2.open = true;
          d2.dataset.folder = subj.name + '\u0000' + book.name;
          const s2 = el('summary');
          s2.dataset.folder = subj.name + '\u0000' + book.name;
          s2.innerHTML = '<span class="au-folder-name">' + esc(book.name) + '</span>' +
            '<span class="au-count">' + book.count + '</span>';
          s2.addEventListener('contextmenu', function (e) { folderMenu(e, subj.name, book.name, false); });
          d2.appendChild(s2);
          (book.sessions || []).forEach(function (sess) {
            d2.appendChild(sessionItem(sess, subj.name, book.name));
          });
          d1.appendChild(d2);
        });
        tree.appendChild(d1);
      });
    }

    // 空白处右键 → 新建学科 / 新建教材（就是 IDE 里「在空白处新建文件夹」那个习惯）
    tree.addEventListener('contextmenu', function (e) {
      if (e.target !== tree) return;                     // 点在文件夹/对话上有各自的菜单
      e.preventDefault();
      showMenu(e.clientX, e.clientY, [
        { label: '新建学科…', hint: '最外层文件夹', action: function () {
          const name = window.prompt('新学科名字（如 数学 / 语文）：', '');
          if (name && name.trim()) createFolder(name.trim(), '');
        } },
        { label: '新建教材…', hint: '需要先选学科', disabled: true },
        { sep: true },
        { label: '刷新列表', action: function () { refreshTree(); } },
      ]);
    });
    // 树以外的地方右键也给「新建学科」的入口（避免必须精准点在空白处）
    tree.addEventListener('mousedown', function (e) {
      if (e.button === 2 && e.target === tree) e.preventDefault();
    });

    async function refreshTree() {
      try {
        const data = await api('/agent/sessions');
        renderTree(data.tree || []);
        navFoot.querySelector('.au-nav-stat').textContent = '共 ' + (data.total || 0) + ' 个对话';
      } catch (err) {
        tree.innerHTML = '';
        tree.appendChild(el('div', 'au-nav-empty',
          '读不到历史对话（' + esc(err.message) + '）。<br>服务可能没在跑：双击 start_agent.cmd。'));
      }
    }

    async function openSession(id) {
      currentId = id;
      chatApp.setSession(id);
      chatApp.clearChat();
      try {
        const info = await api('/agent/session/' + encodeURIComponent(id));
        chatApp.appendHistory(info.messages || []);
        if (info.last_ui) chatApp.showUi(info.last_ui);   // 恢复上次的工作台内容
        try { localStorage.setItem('au_session', id); } catch (e) {}
      } catch (err) {
        // 这个对话在服务端已经不存在了（被删过、或者换过数据目录）。
        // **不要**只丢一句「读不出来」就完事 —— 学生看到的是一栏空白，会以为界面坏了
        // （实测就是这个现象：「左中右的对话老师不见了」）。
        // 正确做法：忘掉这个 id，把最近的对话打开；一个都没有才开新的。
        const gone = /404|不存在|not found/i.test(String(err.message || ''));
        try { localStorage.removeItem('au_session'); } catch (e) {}
        currentId = null;
        chatApp.setSession('');
        chatApp.clearChat();
        if (gone) {
          const fallback = await pickMostRecentSession();
          if (fallback && fallback !== id) {
            await openSession(fallback);
            return;
          }
          newSession('', '');
          return;
        }
        chatApp.appendHistory([{ role: 'assistant', content: '（这个对话读不出来：' + err.message + '）' }]);
      }
      refreshTree();
      chatApp.focusInput();
    }

    /* 最近改过的那个对话（用来接住「记住的那个对话不在了」这种情况） */
    async function pickMostRecentSession() {
      try {
        const data = await api('/agent/sessions');
        let best = null;
        (data.tree || []).forEach(function (s) {
          (s.textbooks || []).forEach(function (b) {
            (b.sessions || []).forEach(function (x) {
              if (!best || (x.updated_at || 0) > (best.updated_at || 0)) best = x;
            });
          });
        });
        return best && best.id;
      } catch (e) {
        return null;
      }
    }

    async function newSession(subject, textbook) {
      try {
        const data = await api('/agent/session/new', { method: 'POST', body: { subject: subject || '', textbook: textbook || '' } });
        await openSession(data.session.id);
      } catch (err) {
        chatApp.appendHistory([{ role: 'assistant', content: '（新建对话失败：' + err.message + '）' }]);
      }
    }

    async function renameSession(sess) {
      // 就地改名（IDE 的 F2 习惯）；找不到界面元素才退回弹窗
      const item = tree.querySelector('[data-session-id="' + sess.id + '"]');
      const titleEl = item ? item.querySelector('.au-session-title') : null;
      const apply = async function (value) {
        try {
          await api('/agent/session/' + encodeURIComponent(sess.id) + '/meta',
            { method: 'POST', body: { title: value } });
        } catch (err) { /* 失败就保持原样 */ }
        await refreshTree();
      };
      if (titleEl) { inlineEdit(titleEl, sess.title || '', apply); return; }
      const next = window.prompt('给这个对话起个名字：', sess.title || '');
      if (next != null && next.trim()) apply(next.trim());
    }

    /** 把对话移动到别的学科/教材下（右键「移动到…」） */
    async function moveSession(sess) {
      const subject = window.prompt('移动到哪个学科？（留空 = 未分类）', sess.subject || '');
      if (subject == null) return;
      const textbook = window.prompt('哪个教材？（留空 = 未指定教材）', sess.textbook || '');
      if (textbook == null) return;
      try {
        await api('/agent/session/' + encodeURIComponent(sess.id) + '/meta',
          { method: 'POST', body: { subject: subject.trim(), textbook: textbook.trim() } });
        // 目标文件夹也登记一下，否则空着的话树上看不出它存在
        if (subject.trim() || textbook.trim()) await createFolder(subject.trim(), textbook.trim());
      } catch (err) { console.error(err); }
      await refreshTree();
    }

    async function deleteSession(sess) {
      if (!window.confirm('删除对话「' + (sess.title || '新对话') + '」？对话记录会一起删掉。')) return;
      try {
        await api('/agent/session/' + encodeURIComponent(sess.id), { method: 'DELETE' });
      } catch (err) { /* ignore */ }
      if (sess.id === currentId) {
        currentId = '';
        chatApp.clearChat();
        chatApp.setSession('');
      }
      refreshTree();
    }

    // 顶层「＋ 新对话」：给一个菜单选择（在哪建、建什么），而不是连问两个弹窗
    newBtn.addEventListener('click', function (e) {
      const rect = e.currentTarget.getBoundingClientRect ? e.currentTarget.getBoundingClientRect() : { left: 100, bottom: 40 };
      showMenu(rect.left, rect.bottom + 4, [
        { label: '新建对话', hint: '未分类', action: function () { newSession('', ''); } },
        { sep: true },
        { label: '新建学科…', hint: '先建文件夹', action: function () {
          const name = window.prompt('新学科名字（如 数学 / 语文）：', '');
          if (name && name.trim()) createFolder(name.trim(), '');
        } },
        { label: '新建教材…', hint: '归属于某学科', action: function () {
          const subject = window.prompt('归属学科（如 数学）：', '');
          if (subject == null || !subject.trim()) return;
          const book = window.prompt('新教材名字（如 人教版必修一）：', '');
          if (book && book.trim()) createFolder(subject.trim(), book.trim());
        } },
      ]);
    });

    // F2 重命名当前对话（IDE 习惯）
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'F2' || !currentId) return;
      const item = tree.querySelector('[data-session-id="' + currentId + '"]');
      const titleEl = item ? item.querySelector('.au-session-title') : null;
      if (titleEl) {
        e.preventDefault();
        const title = titleEl.textContent || '';
        inlineEdit(titleEl, title, async function (value) {
          try {
            await api('/agent/session/' + encodeURIComponent(currentId) + '/meta',
              { method: 'POST', body: { title: value } });
          } catch (err) { /* ignore */ }
          refreshTree();
        });
      }
    });

    /* ======================================================================
     * 设置中心 —— 左下角「⚙ 设置」打开
     * ----------------------------------------------------------------------
     * 把**所有可配置项**收在一个地方：模型链路（含 base_url / API Key）、运行参数、
     * 人格提示词、桌宠形象；另外放一块「关于」。
     *
     * 两条纪律：
     *   · 密钥只进不出：输入框永远不回填明文，只显示「已设置 …abcd」；留空=不改。
     *   · 工具规则不给改：那是功能约束（改了模型就用错工具），界面上写明白为什么。
     * ==================================================================== */
    function settingsRow(key, meta, values, secrets, extra) {
      const kind = (meta.types || {})[key] || 'str';
      const label = (meta.labels || {})[key] || key;
      const choices = (meta.choices || {})[key];
      const row = el('div', 'au-field');
      const lab = el('label', '', label);
      lab.title = key;
      row.appendChild(lab);

      let input;
      if (choices) {
        // 只能取固定几个值的字段 → 下拉框（服务端也会校验同样的白名单）
        input = el('select');
        choices.forEach(function (val) {
          const names = {
            cloud: 'cloud（云端 API）', local: 'local（本机 Ollama）', auto: 'auto（云端优先，本地兜底）',
            off: '关闭（不出声）', windows: 'Windows 本机语音（离线，推荐）', openai: '云端语音（OpenAI 兼容）',
          };
          const o = el('option', '', names[val] || val);
          o.value = val;
          if (String(values[key]) === val) o.selected = true;
          input.appendChild(o);
        });
        input.dataset.key = key;
        input.dataset.kind = 'str';
      } else if (key === 'tts_windows_voice' && extra && extra.voices) {
        // 系统的嗓子 → 用下拉列出来（让学生看见 "Microsoft Huihui 中文" 而不是一个数字）
        input = el('select');
        (extra.voices.windows || []).forEach(function (v) {
          const o = el('option', '', v.name);
          o.value = String(v.id);
          if (String(values[key]) === String(v.id)) o.selected = true;
          input.appendChild(o);
        });
        input.dataset.key = key;
        input.dataset.kind = 'int';
      } else if (kind === 'bool') {
        input = el('input');
        input.type = 'checkbox';
        input.checked = !!values[key];
        input.dataset.key = key;
        input.dataset.kind = 'bool';
      } else if (key === 'pet_scale') {
        // 桌宠大小：用滑杆（拖一下就能看出效果），旁边显示当前倍数。
        // ★ 这一支必须放在「int/float」那一支**前面** —— pet_scale 是 float，
        //   否则会被数值分支先接走，渲染成一个普通数字框（学生要的是"调大小"的手感）。
        const wrap = el('div', 'au-field-range');
        input = el('input');
        input.type = 'range';
        input.min = '0.6'; input.max = '2.6'; input.step = '0.05';
        input.value = values[key] != null ? values[key] : 1;
        input.dataset.key = key;
        input.dataset.kind = 'float';
        const shown = el('span', 'au-set-note', Number(input.value).toFixed(2) + '×');
        input.addEventListener('input', function () {
          shown.textContent = Number(input.value).toFixed(2) + '×';
          if (window.__app && __app.pet) __app.pet.applyScale(input.value);   // 当场就能看到大小变化
        });
        wrap.appendChild(input);
        wrap.appendChild(shown);
        row.appendChild(wrap);
        return row;
      } else if (kind === 'int' || kind === 'float') {
        input = el('input');
        input.type = 'number';
        input.step = kind === 'int' ? '1' : '0.1';
        const rng = (meta.ranges || {})[key] || [];
        if (rng[0] != null) input.min = rng[0];
        if (rng[1] != null) input.max = rng[1];
        input.value = values[key] != null ? values[key] : '';
        input.dataset.key = key;
        input.dataset.kind = kind;
      } else if (key === 'llm_provider') {
        // （老分支，留着兼容；现在 choices 那条已经不走到这里了）
        input = el('select');
        [['cloud', 'cloud（云端 API）'], ['local', 'local（本机 Ollama）'], ['auto', 'auto（云端优先，本地兜底）']]
          .forEach(function (pair) {
            const o = el('option', '', pair[1]);
            o.value = pair[0];
            if (String(values[key]) === pair[0]) o.selected = true;
            input.appendChild(o);
          });
        input.dataset.key = key;
        input.dataset.kind = 'str';
      } else {
        input = el('input');
        input.type = (secrets && secrets[key]) ? 'password' : 'text';
        input.dataset.key = key;
        input.dataset.kind = 'str';
        if (secrets && secrets[key]) {
          // 密钥：不回填明文。留空=不改，旁边给个「清除」
          const info = secrets[key];
          input.placeholder = info.set ? ('已设置 ' + (info.tail || '') + '（留空=不改）') : '还没设置';
          input.dataset.secret = '1';
          const clear = el('button', 'au-btn au-btn-mini', '清除');
          clear.type = 'button';
          clear.onclick = function () {
            input.value = '';
            input.dataset.clear = '1';
            input.placeholder = '已标记清除（保存后生效）';
          };
          row.appendChild(input);
          row.appendChild(clear);
          return row;
        }
        input.value = values[key] != null ? values[key] : '';
      }
      row.appendChild(input);
      return row;
    }

    function collectSettings(card) {
      const patch = {};
      card.querySelectorAll('[data-key]').forEach(function (input) {
        const key = input.dataset.key;
        const kind = input.dataset.kind;
        if (input.dataset.secret) {
          if (input.dataset.clear) { patch[key + '_clear'] = true; return; }
          if (input.value && input.value.trim()) patch[key] = input.value.trim();
          return;
        }
        if (kind === 'bool') patch[key] = !!input.checked;
        else if (kind === 'int' || kind === 'float') {
          // 空输入框 **跳过不提交**（= 不改）。不然 parseFloat('') 是 NaN，
          // 提交上去会被服务端判成「要填数字」，整份设置都存不进去（实测踩过）。
          const raw = String(input.value == null ? '' : input.value).trim();
          if (!raw) { input.value = input.dataset.fallback || ''; return; }
          const num = kind === 'int' ? parseInt(raw, 10) : parseFloat(raw);
          if (!isFinite(num)) return;
          patch[key] = num;
        } else patch[key] = input.value;
      });
      const persona = card.querySelector('[data-persona]');
      if (persona) patch.persona = persona.value;
      const extra = card.querySelector('[data-persona-extra]');
      if (extra) patch.persona_extra = extra.value;
      return patch;
    }

    async function openSettings(focus) {
      let meta;
      let ttsInfo = { voices: { windows: [], cloud: [] } };
      try {
        meta = await api('/agent/settings/all');
      } catch (err) {
        window.alert('读不到设置：' + err.message + '\n（服务在跑吗？）');
        return;
      }
      try { ttsInfo = await api('/agent/tts/voices'); } catch (e) { /* 语音读不到也不挡其它设置 */ }
      const overlay = el('div', 'au-modal');
      const card = el('div', 'au-modal-card');
      card.innerHTML =
        '<div class="au-modal-head"><span>⚙ 设置</span>' +
        '<span class="au-set-hint" style="flex:1">改完点右下角「保存」，立刻生效（不用重启）</span>' +
        '<button class="au-btn au-btn-mini" data-act="close">关闭</button></div>';
      const body = el('div', 'au-modal-body');
      card.appendChild(body);

      const groupBoxes = {};
      (meta.groups || []).forEach(function (g) {
        const box = el('div', 'au-set-group');
        box.appendChild(el('div', 'au-set-title', g.title));
        if (g.hint) box.appendChild(el('div', 'au-set-hint', g.hint));
        (g.fields || []).forEach(function (key) {
          box.appendChild(settingsRow(key, meta, meta.values || {}, meta.secrets || {}, ttsInfo));
        });
        groupBoxes[g.key] = box;
        body.appendChild(box);
      });

      // ---- 桌宠：形象下拉 + 上传自己的 Live2D（都在「桌宠」这一组里）----
      let petSel = null;
      if (groupBoxes.pet && window.PetCore) {
        const petBox = groupBoxes.pet;
        const petRow = el('div', 'au-field');
        petRow.appendChild(el('label', '', '当前形象'));
        petSel = el('select');
        petSel.dataset.petModel = '1';
        petRow.appendChild(petSel);
        petBox.appendChild(petRow);
        petBox.appendChild(el('div', 'au-set-hint', '清单是扫你电脑上的模型得到的（含桌面上的免费模型）'));
        PetCore.catalog().then(function (cat) {
          (cat.models || []).forEach(function (m) {
            const o = el('option', '', (m.label || m.name) + (m.uploaded ? '（上传的）' : '') +
              (m.has_motions ? '（自带动作）' : '（程序演绎）'));
            o.value = m.id;
            if (cat.current && cat.current.id === m.id) o.selected = true;
            petSel.appendChild(o);
          });
        }).catch(function () {
          petSel.appendChild(el('option', '', '（读不到形象清单）'));
        });

        const upRow = el('div', 'au-field');
        upRow.appendChild(el('label', '', '上传自己的模型'));
        const pick = el('input');
        pick.type = 'file';
        pick.multiple = true;
        pick.setAttribute('webkitdirectory', '');   // 直接选**整个模型文件夹**，不用先打包
        pick.style.flex = '1';
        upRow.appendChild(pick);
        const upBtn = el('button', 'au-btn au-btn-mini', '上传');
        upBtn.type = 'button';
        upRow.appendChild(upBtn);
        petBox.appendChild(upRow);
        petBox.appendChild(el('div', 'au-set-hint',
          '选中整个模型文件夹（里面要有 .model3.json、.moc3 和贴图）。上传后会自动切成它。'));
        const upMsg = el('div', 'au-set-hint');
        petBox.appendChild(upMsg);
        upBtn.onclick = async function () {
          const files = pick.files ? Array.prototype.slice.call(pick.files) : [];
          if (!files.length) { upMsg.textContent = '先点左边选一个模型文件夹'; return; }
          if (!files.some(function (f) { return /\.model3\.json$/i.test(f.name); })) {
            upMsg.textContent = '这个文件夹里没有 .model3.json —— 请选模型自己的文件夹（不是它的上一层）';
            return;
          }
          const folder = String(files[0].webkitRelativePath || files[0].name).split('/')[0] || 'my_model';
          const fd = new FormData();
          fd.append('folder', folder);
          files.forEach(function (f) {
            fd.append('files', f, f.name);
            fd.append('paths_' + f.name, f.webkitRelativePath || f.name);
          });
          upMsg.textContent = '正在上传 ' + files.length + ' 个文件…';
          upBtn.disabled = true;
          try {
            const resp = await fetch((AgentUI.base || '') + '/agent/pet/model/upload', { method: 'POST', body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || ('HTTP ' + resp.status));
            upMsg.textContent = '上传成功：' + files.length + ' 个文件 → 正在切换形象…';
            // 重新拉清单，把新模型加进下拉并选中它
            const cat = await PetCore.catalog();
            const fresh = (cat.models || []).filter(function (m) { return m.uploaded; });
            const added = (data.model && data.model.id) || (fresh[fresh.length - 1] || {}).id;
            petSel.innerHTML = '';
            (cat.models || []).forEach(function (m) {
              const o = el('option', '', (m.label || m.name) + (m.uploaded ? '（上传的）' : '') +
                (m.has_motions ? '' : '（程序演绎）'));
              o.value = m.id;
              if (m.id === added) o.selected = true;
              petSel.appendChild(o);
            });
            if (added && window.__app && __app.pet) {
              const info = (cat.models || []).filter(function (m) { return m.id === added; })[0];
              if (info) await __app.pet.switchTo(info);
            }
            upMsg.textContent = '已换成你上传的模型 ✓';
          } catch (err) {
            upMsg.textContent = '上传失败：' + err.message;
          }
          upBtn.disabled = false;
        };
      }

      // ---- 语音：试听 ----
      if (groupBoxes.tts) {
        const ttsBox = groupBoxes.tts;
        // 音色是**输入框**（要支持自定义音色），所以给一句提示 + 常用名字的示例
        const voiceInput = card.querySelector('[data-key="tts_cloud_voice"]');
        if (voiceInput) {
          voiceInput.placeholder = '常用：anna / bella / alex / charles；自定义就填服务商文档里的名字';
        }
        const row = el('div', 'au-field');
        row.appendChild(el('label', '', '试听'));
        const sayInput = el('input');
        sayInput.type = 'text';
        sayInput.value = '你好呀，我是艾琳老师。';
        sayInput.style.flex = '1';
        const sayBtn = el('button', 'au-btn au-btn-mini', '听一下');
        sayBtn.type = 'button';
        row.appendChild(sayInput);
        row.appendChild(sayBtn);
        ttsBox.appendChild(row);
        const ttsMsg = el('div', 'au-set-hint',
          '「听一下」用的是你**正在编辑**的音色/语速（不用先保存）——填个自定义音色马上就能试。'
          + ((ttsInfo.current && ttsInfo.current.note) ? ('　当前：' + ttsInfo.current.note) : ''));
        ttsBox.appendChild(ttsMsg);
        sayBtn.onclick = async function () {
          ttsMsg.textContent = '正在合成…（本机语音通常 1 秒内）';
          sayBtn.disabled = true;
          try {
            // 把界面上正在编辑的值一起发过去 → 试听的就是"你现在选的"，不用先保存
            const ov = {};
            ['tts_provider', 'tts_windows_voice', 'tts_cloud_base_url', 'tts_cloud_model',
             'tts_cloud_voice', 'tts_speed', 'tts_volume'].forEach(function (k) {
              const node = card.querySelector('[data-key="' + k + '"]');
              if (!node) return;
              const short = k.replace(/^tts_/, '').replace(/^cloud_/, '');
              let v = node.value;
              if (node.type === 'number' || node.type === 'range') v = Number(v);
              if (short === 'windows_voice') ov.windows_voice = Number(v);
              else if (short === 'provider') ov.provider = v;
              else if (short === 'base_url') ov.base_url = v;
              else if (short === 'volume') ov.volume = Number(v);
              else if (short === 'speed') ov.speed = Number(v);
              else if (short === 'model') ov.model = v;
              else if (short === 'voice') ov.voice = v;
            });
            const resp = await fetch((AgentUI.base || '') + '/agent/tts/say', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ text: sayInput.value, override: ov }),
            });
            if (!resp.ok) {
              const d = await resp.json().catch(function () { return {}; });
              throw new Error(d.detail || ('HTTP ' + resp.status));
            }
            const blob = await resp.blob();
            const audio = new Audio(URL.createObjectURL(blob));
            await audio.play();
            ttsMsg.textContent = '播放中…（' + Math.round(blob.size / 1024) + ' KB，用的音色：'
              + (ov.voice || '本机嗓子') + '）';
          } catch (err) {
            ttsMsg.textContent = '没播出来：' + err.message;
          }
          sayBtn.disabled = false;
        };
      }

      // ---- 人格提示词 ----
      const pbox = el('div', 'au-set-group');
      pbox.appendChild(el('div', 'au-set-title', '人格提示词（老师是谁、怎么教）'));
      pbox.appendChild(el('div', 'au-set-hint',
        (meta.persona && meta.persona.note) || '') );
      const prow = el('div', 'au-field');
      prow.appendChild(el('label', '', '人格'));
      const pta = el('textarea');
      pta.dataset.persona = '1';
      pta.value = (meta.persona && meta.persona.text) || '';
      pta.placeholder = (meta.persona && meta.persona.default) || '';
      prow.appendChild(pta);
      pbox.appendChild(prow);
      const erow = el('div', 'au-field');
      erow.appendChild(el('label', '', '附加要求'));
      const eta = el('textarea');
      eta.dataset.personaExtra = '1';
      eta.value = (meta.persona && meta.persona.extra) || '';
      eta.placeholder = '例如：每次课结束都留一道思考题；回答尽量短';
      erow.appendChild(eta);
      pbox.appendChild(erow);
      const phint = el('div', 'au-set-hint');
      phint.textContent = (meta.persona && meta.persona.is_default)
        ? '现在用的是内置人格（清空输入框 = 恢复内置）' : '现在用的是你自定义的人格';
      pbox.appendChild(phint);
      body.appendChild(pbox);

      // ---- 关于 ----
      const abox = el('div', 'au-set-group');
      abox.appendChild(el('div', 'au-set-title', '关于'));
      const about = meta.about || {};
      const lines = [
        ['版本', about.version],
        ['工具数', about.tools],
        ['可用形象', about.pet_models],
        ['数据目录', about.data_root],
        ['服务版本', (meta.chain || []).join(' → ')],
      ];
      lines.forEach(function (kv) {
        if (kv[1] == null || kv[1] === '') return;
        const r = el('div', 'au-field');
        r.appendChild(el('label', '', kv[0]));
        const v = el('div', 'au-set-note', String(kv[1]));
        v.style.flex = '1';
        v.style.wordBreak = 'break-all';
        r.appendChild(v);
        abox.appendChild(r);
      });
      body.appendChild(abox);

      const foot = el('div', 'au-modal-foot');
      const status = el('span', 'au-set-hint');
      status.style.flex = '1';
      const btnTest = el('button', 'au-btn', '测试连接');
      const btnSave = el('button', 'au-btn au-btn-primary', '保存');
      [btnTest, btnSave].forEach(function (b) { b.type = 'button'; });
      foot.appendChild(status);
      foot.appendChild(btnTest);
      foot.appendChild(btnSave);
      card.appendChild(foot);
      overlay.appendChild(card);
      document.body.appendChild(overlay);

      function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
      function onKey(ev) { if (ev.key === 'Escape') close(); }
      document.addEventListener('keydown', onKey);
      overlay.addEventListener('mousedown', function (ev) { if (ev.target === overlay) close(); });
      card.querySelector('[data-act="close"]').onclick = close;

      btnTest.onclick = async function () {
        status.textContent = '正在测试…（会给模型发一句话）';
        btnTest.disabled = true;
        try {
          const r = await api('/agent/settings/test', { method: 'POST', body: {} });
          const parts = (r.results || []).map(function (x) {
            return (x.label || x.provider || '?') + (x.ok ? ' ✓' : (' ✗ ' + (x.error || '')));
          });
          status.textContent = (r.ok ? '通了：' : '没通：') + (parts.join(' | ') || r.error || '');
          status.style.color = r.ok ? '' : '#c62b1c';   // 弹层在 .au-root 外，取不到 --au-err
        } catch (err) {
          status.textContent = '测试失败：' + err.message;
        }
        btnTest.disabled = false;
      };

      btnSave.onclick = async function () {
        status.textContent = '保存中…';
        btnSave.disabled = true;
        try {
          const patch = collectSettings(card);
          // 桌宠形象单独走它自己的接口（保存位置不同）
          const petId = petSel.value;
          const r = await api('/agent/settings/all', { method: 'POST', body: patch });
          if (!r.ok) {
            status.textContent = '没保存：' + (r.errors || []).join('；');
            status.style.color = '#c62b1c';
            btnSave.disabled = false;
            return;
          }
          let petMsg = '';
          if (petId && window.PetCore) {
            try {
              await PetCore.setModel(petId);
              if (window.__app && __app.pet && __app.pet.switchTo) {
                const cat = await PetCore.catalog();
                const info = (cat.models || []).filter(function (m) { return m.id === petId; })[0];
                if (info) await __app.pet.switchTo(info);
              }
              petMsg = '，形象也换了';
            } catch (err) { petMsg = '，但形象没换成：' + err.message; }
          }
          // 语音开关/大小也要**当场**接上（不然学生改完得刷新页面才生效）
          try {
            const after = r.state || {};
            const v2 = after.values || {};
            if (window.__app && __app.pet) {
              __app.pet.setSpeak(v2.tts_auto_speak && String(v2.tts_provider || 'off') !== 'off');
              __app.pet.applyScale(v2.pet_scale);
            }
          } catch (e) {}
          status.textContent = '已保存 ✓' + petMsg + '（下一条消息就用新设置）';
          status.style.color = '';
          checkHealth();
        } catch (err) {
          status.textContent = '保存失败：' + err.message;
          status.style.color = '#c62b1c';
        }
        btnSave.disabled = false;
      };
    }


    navFoot.innerHTML =
      '<div class="au-nav-stat">正在读取…</div>' +
      '<div class="au-nav-meta"><button class="au-set-btn" type="button" title="模型 / API Key / 人格提示词 / 运行参数">⚙ 设置</button>' +
      '<span class="au-model-chip" title="点击切换云端/本地模型">模型 …</span>' +
      '<span class="au-ver" title="渲染器版本（页面缓存旧版时比一比）">v' + esc(AgentUI.version) + '</span></div>';
    const modelChip = navFoot.querySelector('.au-model-chip');
    navFoot.querySelector('.au-set-btn').addEventListener('click', function () { openSettings(); });
    modelChip.addEventListener('click', async function () {
      try {
        const cur = await api('/agent/settings');
        const next = (cur.settings || {}).llm_provider === 'local' ? 'cloud' : 'local';
        await api('/agent/settings', { method: 'POST', body: { llm_provider: next } });
        checkHealth();
      } catch (err) { /* ignore */ }
    });

    async function checkHealth() {
      try {
        const d = await api('/agent/health');
        const first = (d.chain || [])[0] || {};
        const okCount = (d.chain || []).filter(function (c) { return c.ok; }).length;
        modelChip.textContent = (okCount ? '' : '⚠ ') + (first.label || '?') + ' · ' + (d.tools || 0) + ' 工具';
        modelChip.classList.toggle('bad', !okCount);
      } catch (err) {
        modelChip.textContent = '服务未运行';
        modelChip.classList.add('bad');
      }
    }

    /* ---------- 启动：恢复上次会话，没有就开一个新的 ---------- */
    (async function boot() {
      await checkHealth();
      let last = '';
      try { last = localStorage.getItem('au_session') || ''; } catch (e) {}
      if (last) {
        try {
          await openSession(last);
        } catch (err) { /* 会话可能已被删除 */ }
      }
      await refreshTree();
      if (!currentId) {
        const data = await api('/agent/sessions');
        const subjects = data.tree || [];
        const first = subjects[0] && subjects[0].textbooks[0] && subjects[0].textbooks[0].sessions[0];
        if (first) await openSession(first.id);
        else await newSession('', '');
      }
      setInterval(checkHealth, 20000);
      setInterval(refreshTree, 30000);      // 对话标题会随首句自动更新，定期刷新
    })();

    return {
      chat: chatApp,
      pet: petDoll,
      refreshTree: refreshTree,
      openSession: openSession,
      newSession: newSession,
      checkHealth: checkHealth,
    };
  }

  AgentUI.mountChat = mountChat;
  AgentUI.mountApp = mountApp;
  AgentUI.createClient = createClient;
  AgentUI.detectBase = detectBase;
  AgentUI.injectCSS = injectCSS;
  AgentUI._fmt = fmt;          // 供 node 侧做单元验证（渲染逻辑出问题时能脱离浏览器定位）
  AgentUI._fmtBlock = fmtBlock;
  AgentUI._makeMapper = makeMapper;
  AgentUI._gridStep = gridStep;
  AgentUI._fmtTick = fmtTick;   // 精确度（小数位）的规则要能脱离浏览器直接验
  AgentUI._css = CSS;          // 供测试断言样式规则（有些 bug 只在 CSS 里，比如分栏线被误藏）
  window.AgentUI = AgentUI;
})();
