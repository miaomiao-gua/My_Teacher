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

  const AgentUI = { version: '0.3.0' };
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

  function fmtBlock(text) {
    // 段落级：空行分段，行内交给 fmt
    return String(text == null ? '' : text)
      .split(/\n{2,}/).map(function (p) {
        return '<p>' + fmt(p).replace(/\n/g, '<br>') + '</p>';
      }).join('');
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

  function tokenizeExpr(src) {
    const s = String(src).replace(/\s+/g, '');
    const tokens = [];
    let i = 0;
    while (i < s.length) {
      const ch = s[i];
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
      throw new Error('表达式里有不认识的字符：' + ch);
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
    if (pos < tokens.length) throw new Error('表达式有多余内容：' + tokens[pos].t);
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

  /** 编译表达式；隐式乘法（2x、3sin(x)）也帮它补上 */
  function compileExpr(src) {
    let text = String(src);
    text = text.replace(/(\d)\s*([a-zA-Z(])/g, '$1*$2')
      .replace(/\)\s*(\d|[a-zA-Z(])/g, ')*$1');
    const ast = parseExpr(text);
    return {
      ast: ast,
      eval: function (env) { return evalAst(ast, env || {}); },
    };
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

  /** 画网格 + 坐标轴 + 刻度数字（用 map 的实际可视范围，等比时也正确） */
  function drawGrid(ctx, w, h, map) {
    const pad = map.pad;
    const xr = map.xr, yr = map.yr;
    const step = gridStep(Math.min(map.xUnit, map.yUnit), 30);
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(0,0,0,.07)';
    ctx.beginPath();
    const x0 = Math.ceil(xr[0] / step) * step;
    for (let x = x0; x <= xr[1] + 1e-9; x += step) {
      const px = Math.round(map.x2p(x)) + 0.5;
      ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b);
    }
    const y0 = Math.ceil(yr[0] / step) * step;
    for (let y = y0; y <= yr[1] + 1e-9; y += step) {
      const py = Math.round(map.y2p(y)) + 0.5;
      ctx.moveTo(pad.l, py); ctx.lineTo(w - pad.r, py);
    }
    ctx.stroke();

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
    ctx.fillStyle = 'rgba(0,0,0,.5)';
    ctx.font = '11px "Segoe UI",system-ui,sans-serif';
    const axisY = (yr[0] <= 0 && yr[1] >= 0) ? Math.min(h - pad.b - 4, Math.max(pad.t + 12, map.y2p(0) + 13)) : h - pad.b + 13;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let x = x0; x <= xr[1] + 1e-9; x += step) {
      if (Math.abs(x) < 1e-9 && yr[0] <= 0 && yr[1] >= 0) continue;
      ctx.fillText(fmtTick(x), map.x2p(x), axisY);
    }
    const axisX = (xr[0] <= 0 && xr[1] >= 0) ? Math.max(pad.l + 4, Math.min(w - pad.r - 18, map.x2p(0) - 6)) : pad.l - 6;
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let y = y0; y <= yr[1] + 1e-9; y += step) {
      if (Math.abs(y) < 1e-9) continue;
      ctx.fillText(fmtTick(y), axisX, map.y2p(y));
    }
    ctx.restore();
  }

  function fmtTick(v) {
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
    const node = el(tag, sizes[n.size] || 'au-p', fmt(n.content));
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

    function draw() {
      const height = canvas.dataset.height ? +canvas.dataset.height : 380;
      const view = setupCanvas(canvas, height);
      compileAll();
      if (!frame) autoFrame(view.w, view.h);
      const map = makeMapper(view.w, view.h, frame.xr, frame.yr, PAD, true);
      frame = { xr: map.xr, yr: map.yr };              // 用等比后的真实可视范围回写
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
    } else {
      list.querySelectorAll('.au-quiz-opt').forEach(function (b, i) {
        b.classList.toggle('au-selected', i === index);
      });
      box._pick(index);
    }
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
    const captionBar = el('div', 'au-board-caption');
    const bar = el('div', 'au-board-bar');
    const prevBtn = el('button', 'au-btn au-btn-mini', '← 上一步');
    const nextBtn = el('button', 'au-btn au-btn-mini', '下一步 →');
    const allBtn = el('button', 'au-btn au-btn-mini', '全部显示');
    const counter = el('span', 'au-board-counter', '');
    bar.appendChild(prevBtn); bar.appendChild(nextBtn); bar.appendChild(allBtn); bar.appendChild(counter);
    box.appendChild(captionBar);
    box.appendChild(bar);

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
      captionBar.innerHTML = '<b>第 ' + (current + 1) + ' / ' + steps.length + ' 步</b>' + (cap ? '　' + fmt(cap) : '');
      counter.textContent = '';
      prevBtn.disabled = current === 0;
      nextBtn.disabled = current >= steps.length - 1;
    }

    prevBtn.onclick = function () { if (current > 0) { current--; render(); } };
    nextBtn.onclick = function () { if (current < steps.length - 1) { current++; render(); } };
    allBtn.onclick = function () { current = steps.length - 1; render(); };
    setTimeout(render, 0);
    box._redraw = render;
    return box;
  };

  /** 渲染一个完整 UI 规格（{title, items:[...]}） */
  function renderSpec(spec) {
    const box = el('div', 'au-spec');
    if (!spec || !spec.items) return box;
    if (spec.title) box.appendChild(el('div', 'au-spec-title', fmt(spec.title)));
    children(box, spec.items);
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
  /* 可拖拽分栏：用户自己决定左右比例（比例会记住，双击恢复默认） */
  .au-splitter{flex:0 0 7px;cursor:col-resize;position:relative;background:var(--au-panel)}
  .au-splitter::after{content:'';position:absolute;top:0;bottom:0;left:3px;width:1px;background:var(--au-line-strong);
    transition:background .12s,width .12s}
  .au-splitter:hover::after,.au-splitter.au-dragging::after{background:var(--au-accent);width:2px;left:2px}
  .au-splitter:focus{outline:none}
  .au-splitter:focus::after{background:var(--au-accent);width:2px;left:2px}
  .au-root:not(.au-has-surface) .au-splitter{display:none}
  .au-has-surface .au-col-left{border-right:none}   /* 有分栏条时不再重复画分界线 */
  .au-chat{flex:1 1 auto;min-height:0;overflow:auto;padding:10px 14px}
  .au-stage{flex:1 1 auto;min-height:0;overflow:auto;padding:14px 16px;background:var(--au-bg)}
  .au-stage-empty{color:var(--au-muted);border:1px dashed var(--au-line-strong);border-radius:8px;
    padding:32px;text-align:center;font-size:13.5px;background:var(--au-panel);line-height:1.9}
  .au-input{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--au-line);background:var(--au-panel)}
  /* 窄屏改成上下堆叠：对话在上，工作台在下 */
  @media (max-width:900px){
    .au-root{flex-direction:column}
    .au-col-left{flex:0 0 auto;max-width:none;min-width:0;border-right:none;border-bottom:1px solid var(--au-line)}
    .au-chat{max-height:34vh}
    .au-col-right{flex:1 1 auto;min-height:220px}
    .au-splitter{display:none}                        /* 上下堆叠时没有「左右比例」可调 */
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
  .au-plot-tools{display:flex;gap:6px;flex-wrap:wrap}
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
  .au-state{font-size:12.5px;color:var(--au-muted);margin:8px 0}
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
    };
  }

  /* ==========================================================================
   * 七、课堂视图：把事件流画成「对话 + 工具动作 + 界面」
   * ======================================================================== */
  function mountChat(root, opts) {
    injectCSS();
    opts = opts || {};
    root.classList.add('au-root');
    root.innerHTML = '';

    // 布局：**左对话、右界面**。
    // 左侧是老师和学生说话的地方（固定宽度、可滚动），右侧是「工作台」——
    // 老师要弹出图表 / 画板 / 实验台 / 图形计算器时，自动出现在右边，不用切换页面。
    // 窄屏（<900px）自动改成上下堆叠，手机上也能用。
    const left = el('div', 'au-col-left');
    const right = el('div', 'au-col-right');

    const stage = el('div', 'au-stage');
    const stageEmpty = el('div', 'au-stage-empty',
      '<b>工作台</b><br>老师生成的图表、推导、画板、虚拟实验都会出现在这里。<br>' +
      '先在左边问点什么试试。');
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

    // 可拖拽分栏：用户自己决定「对话 ↔ 工作台」的比例。
    // 比例记在 localStorage 里，下次打开还是他调好的样子；双击分栏条恢复默认。
    const splitter = el('div', 'au-splitter');
    splitter.title = '拖动调整左右比例（双击恢复默认）';
    splitter.setAttribute('role', 'separator');
    splitter.setAttribute('aria-label', '调整对话区宽度');
    splitter.tabIndex = 0;

    root.appendChild(left);
    root.appendChild(splitter);
    root.appendChild(right);

    const DEFAULT_LEFT = 368;
    let leftWidth = null;
    try {
      const saved = parseFloat(localStorage.getItem('au_left_width'));
      if (saved >= 200) leftWidth = saved;
    } catch (e) { /* 隐私模式下 localStorage 可能不可用 */ }

    function applyLeftWidth(w, persist) {
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
    splitter.addEventListener('mousedown', function (e) {
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
      splitter.classList.remove('au-dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      applyLeftWidth(leftWidth, true);
    });
    splitter.addEventListener('dblclick', function () {
      left.style.flex = '';
      left.style.maxWidth = '';
      leftWidth = null;
      try { localStorage.removeItem('au_left_width'); } catch (e) {}
      applyLeftWidth(null, true);
    });
    // 键盘也能调（无障碍 + 手抖的时候更好用）
    splitter.addEventListener('keydown', function (e) {
      const step = e.shiftKey ? 48 : 16;
      if (e.key === 'ArrowLeft') { applyLeftWidth((leftWidth || DEFAULT_LEFT) - step, true); e.preventDefault(); }
      if (e.key === 'ArrowRight') { applyLeftWidth((leftWidth || DEFAULT_LEFT) + step, true); e.preventDefault(); }
    });

    // 右侧有内容时才占地方：空的时候把空间还给对话（避免大片空白）
    function syncColumns() {
      const hasSurface = right.querySelector('.au-surface-item') !== null;
      root.classList.toggle('au-has-surface', hasSurface);
      if (!hasSurface) {
        left.style.flex = '';
        left.style.maxWidth = '';
      } else if (leftWidth) {
        applyLeftWidth(leftWidth, false);
      }
    }
    syncColumns();

    const client = createClient(opts);
    let bubble = null;      // 当前正在写的助手气泡
    let uiCount = 0;

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
      try {
        box.appendChild(renderSpec(spec));
      } catch (err) {
        // 渲染失败也必须让学生看见「失败了」而不是一片空白 ——
        // 静默失败会让人以为是模型没画，其实是渲染错了。
        box.appendChild(el('div', 'au-error',
          '界面渲染失败：' + esc(err.message) + '<br><span class="au-muted">' +
          '（这是渲染器的问题，不是老师没画。把这句话发给我就能定位。）</span>'));
        console.error('AgentUI render error', err);
      }
      const bar = el('div', 'au-state', '界面由 AI 现场生成 · 可以直接在上面操作');
      box.appendChild(bar);
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
        onEvent: function (evt) { handleEvent(evt); },
        onError: function (msg) { addTool(esc(msg), true); setBusy(false); },
        onDone: function () { setBusy(false); },
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
      setSession: function (id) { client.sessionId = id; },
    };
  }

  AgentUI.mountChat = mountChat;
  AgentUI.createClient = createClient;
  AgentUI.detectBase = detectBase;
  AgentUI.injectCSS = injectCSS;
  AgentUI._fmt = fmt;          // 供 node 侧做单元验证（渲染逻辑出问题时能脱离浏览器定位）
  AgentUI._makeMapper = makeMapper;
  AgentUI._gridStep = gridStep;
  window.AgentUI = AgentUI;
})();
