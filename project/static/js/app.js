// ============== AI 全能私教 - 前端逻辑 ==============

const menuScreen = document.getElementById('menu-screen');
const sceneScreen = document.getElementById('scene-screen');
const lessonListEl = document.getElementById('lesson-list');
const conversationEl = document.getElementById('conversation-history');
const dialogueTextEl = document.getElementById('dialogue-text');
const topicInput = document.getElementById('topic-input') || document.getElementById('menu-topic-input');
const menuTopicInput = document.getElementById('menu-topic-input');
const messageInput = document.getElementById('message-input');
const resourceListEl = document.getElementById('resource-list') || document.createElement('div');
const examListEl = document.getElementById('exam-list') || document.createElement('div');
const examActionsEl = document.getElementById('exam-actions') || document.createElement('div');
const configModal = document.getElementById('config-modal');
const newCourseModal = document.getElementById('new-course-modal');
const speakerNameEl = document.getElementById('dialogue-speaker-name') || document.createElement('span');
const typingIndicatorEl = document.getElementById('typing-indicator');
const toastContainer = document.getElementById('toast-container');
const unitProgressEl = document.getElementById('unit-progress');
const unitProgressFillEl = document.getElementById('unit-progress-fill');
const unitProgressLabelEl = document.getElementById('unit-progress-label');
const unitProgressTitleEl = document.getElementById('unit-progress-title');
const sidePanelEl = document.getElementById('side-panel');

let activeLessonFolder = '';
// 配置弹窗上下文：'global' 或 'course'
let configModalContext = 'global';
// 课程级配置缓存
let courseConfigCache = {};
let activeAssistantName = '艾琳老师';
let currentResources = [];
let examQuestions = [];
let examAnswers = [];
let currentExamIndex = 0;
let examSubmitted = false;
let typingToken = 0;
let lastAudioUrl = '';
let cachedConfig = null;
let currentAudio = null;
// 分课进度缓存：{ current_unit, total_units, units:[{title,...}], has_units }
let unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
// 分段输出队列：收到多段回复时缓冲，按 Enter 逐段展示
let segmentQueue = [];
let segmentWaiting = false;

// ============== 工具函数 ==============

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

/**
 * 从原始文本中提取 LaTeX 数学公式，返回处理后的文本和数学项数组。
 * 支持：\(...\)（行内）、\[...\]（块级）、$...$（行内）、$$...$$（块级）
 */
function extractMath(text) {
  const mathItems = [];
  let processed = text;
  let idx = 0;

  const addItem = (latex, display) => {
    const placeholder = `\u0000MATH_${idx++}\u0000`;
    mathItems.push({ placeholder, latex, display });
    return placeholder;
  };

  // 1. 块级 $$...$$（优先匹配，避免被 $...$ 吃掉）
  processed = processed.replace(/\$\$([\s\S]+?)\$\$/g, (m, latex) => addItem(latex.trim(), true));
  // 2. 块级 \[...\]
  processed = processed.replace(/\\\[([\s\S]+?)\\\]/g, (m, latex) => addItem(latex.trim(), true));
  // 3. 行内 \(...\)
  processed = processed.replace(/\\\(([\s\S]+?)\\\)/g, (m, latex) => addItem(latex.trim(), false));
  // 4. 行内 $...$（排除已处理的 $$...$$）
  processed = processed.replace(/\$([^\$\n]+?)\$/g, (m, latex) => addItem(latex.trim(), false));

  return { processed, mathItems };
}

/**
 * 用 KaTeX 渲染数学项，返回 HTML 字符串。
 */
function renderMathItems(mathItems) {
  if (!mathItems.length || !window.katex) {
    // KaTeX 未加载时，降级显示原始 LaTeX
    return mathItems.map(m => {
      const escaped = escapeHtml(m.latex);
      return m.display
        ? `<div class="math-fallback">[${escaped}]</div>`
        : `<span class="math-fallback">(${escaped})</span>`;
    });
  }
  return mathItems.map(m => {
    try {
      const html = window.katex.renderToString(m.latex, {
        displayMode: m.display,
        throwOnError: false,
        strict: false,
      });
      return html;
    } catch (e) {
      const escaped = escapeHtml(m.latex);
      return m.display
        ? `<div class="math-fallback">[${escaped}]</div>`
        : `<span class="math-fallback">(${escaped})</span>`;
    }
  });
}

/**
 * 极简 Markdown 渲染：支持代码块、行内代码、标题、粗体、列表、链接、换行、LaTeX 公式。
 * 仅用于教师回复（已先转义 HTML，防 XSS）。
 */
function renderMarkdown(text) {
  if (!text) return '';
  // 1. 先清理 \c 分段标记（防止遗漏）
  let cleaned = text.replace(/\\c/g, '');
  // 2. 提取 LaTeX 公式为占位符（在 HTML 转义之前）
  const mathPlaceholders = [];
  cleaned = cleaned.replace(/(\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$[^\$\n]+?\$)/g, (m) => {
    const idx = mathPlaceholders.length;
    mathPlaceholders.push(m);
    return `\x00MATH${idx}\x00`;
  });
  // 3. HTML 转义
  let escaped = escapeHtml(cleaned);
  // 4. 代码块（先处理，避免内部格式被误匹配）
  escaped = escaped.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="md-code">$2</pre>');
  // 5. 行内代码
  escaped = escaped.replace(/`([^`]+)`/g, '<code class="md-inline">$1</code>');
  // 6. 粗体/斜体/下划线
  escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  escaped = escaped.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  escaped = escaped.replace(/__([^_]+)__/g, '<u>$1</u>');
  // 7. 标题
  escaped = escaped.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>');
  escaped = escaped.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>');
  escaped = escaped.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>');
  // 8. 列表
  escaped = escaped.replace(/^\s*\d+\.\s+(.+)$/gm, '<li>$1</li>');
  escaped = escaped.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  // 9. 换行
  escaped = escaped.replace(/\n/g, '<br>');
  // 10. 还原 LaTeX 公式为渲染后的 HTML
  escaped = escaped.replace(/\x00MATH(\d+)\x00/g, (m, idx) => {
    const raw = mathPlaceholders[parseInt(idx)];
    return renderLatexFormula(raw);
  });
  return escaped;
}

/** 将 LaTeX 公式原始文本转为可读 HTML */
function renderLatexFormula(raw) {
  let inner = raw;
  let isBlock = false;
  // 块级 $$...$$
  if (inner.startsWith('$$') && inner.endsWith('$$')) {
    inner = inner.slice(2, -2);
    isBlock = true;
  } else if (inner.startsWith('\\(') && inner.endsWith('\\)')) {
    inner = inner.slice(2, -2);
  } else if (inner.startsWith('\\[') && inner.endsWith('\\]')) {
    inner = inner.slice(2, -2);
    isBlock = true;
  } else if (inner.startsWith('$') && inner.endsWith('$')) {
    inner = inner.slice(1, -1);
  }
  const rendered = convertLatexCmds(inner);
  if (isBlock) {
    return `<div class="math-block">${rendered}</div>`;
  }
  return `<span class="math-inline">${rendered}</span>`;
}

/** 将常见 LaTeX 命令转为 Unicode/可读文本 */
function convertLatexCmds(text) {
  let t = text;
  // 分数 \frac{a}{b} → a/b
  t = t.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '$1/$2');
  // 平方根 \sqrt{x} → √x
  t = t.replace(/\\sqrt\{([^{}]*)\}/g, '√($1)');
  // 上下标 ^{2} → ², _{n} → ₙ（常见）
  t = t.replace(/\^\{([^{}])\}/g, (m, c) => toSuperscript(c));
  t = t.replace(/_\{([^{}])\}/g, (m, c) => toSubscript(c));
  // 单字符上下标 ^2 → ², _n → ₙ
  t = t.replace(/\^(\w)/g, (m, c) => toSuperscript(c));
  t = t.replace(/_(\w)/g, (m, c) => toSubscript(c));
  // 常见 LaTeX 命令 → Unicode
  const cmdMap = {
    '\\times': '×', '\\div': '÷', '\\pm': '±', '\\mp': '∓',
    '\\cdot': '·', '\\leq': '≤', '\\geq': '≥', '\\neq': '≠',
    '\\approx': '≈', '\\equiv': '≡', '\\infty': '∞',
    '\\rightarrow': '→', '\\leftarrow': '←', '\\Rightarrow': '⇒', '\\Leftarrow': '⇐',
    '\\alpha': 'α', '\\beta': 'β', '\\gamma': 'γ', '\\delta': 'δ',
    '\\epsilon': 'ε', '\\theta': 'θ', '\\lambda': 'λ', '\\mu': 'μ',
    '\\pi': 'π', '\\rho': 'ρ', '\\sigma': 'σ', '\\omega': 'ω',
    '\\Delta': 'Δ', '\\Sigma': 'Σ', '\\Omega': 'Ω', '\\Phi': 'Φ',
    '\\sum': '∑', '\\prod': '∏', '\\int': '∫', '\\partial': '∂',
    '\\nabla': '∇', '\\forall': '∀', '\\exists': '∃',
    '\\in': '∈', '\\notin': '∉', '\\subset': '⊂', '\\supset': '⊃',
    '\\cup': '∪', '\\cap': '∩', '\\emptyset': '∅',
    '\\textbf': '', '\\text': '', '\\mathrm': '', '\\mathbf': '',
    '\\left': '', '\\right': '', '\\displaystyle': '',
    '\\,': ' ', '\\;': ' ', '\\:': ' ', '\\!': '',
    '\\quad': '  ', '\\qquad': '    ',
    '\\text{m/s}': 'm/s', '\\text{m/s}^2': 'm/s²',
  };
  for (const [cmd, sym] of Object.entries(cmdMap)) {
    t = t.split(cmd).join(sym);
  }
  // \text{...} → ...
  t = t.replace(/\\text\{([^{}]*)\}/g, '$1');
  t = t.replace(/\\mathrm\{([^{}]*)\}/g, '$1');
  t = t.replace(/\\mathbf\{([^{}]*)\}/g, '$1');
  // 去除剩余的花括号
  t = t.replace(/\{/g, '').replace(/\}/g, '');
  // 清理多余空格
  t = t.replace(/\s{2,}/g, ' ').trim();
  return t;
}

/** 字符转上标 */
function toSuperscript(ch) {
  const map = { '0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵','6':'⁶','7':'⁷','8':'⁸','9':'⁹',
    '+':'⁺','-':'⁻','=':'⁼','(':'ⁿ',')':'','a':'ᵃ','b':'ᵇ','c':'ᶜ','d':'ᵈ','e':'ᵉ','f':'ᶠ',
    'g':'ᵍ','h':'ʰ','i':'ⁱ','j':'ʲ','k':'ᵏ','l':'ˡ','m':'ᵐ','n':'ⁿ','o':'ᵒ','p':'ᵖ',
    'r':'ʳ','s':'ˢ','t':'ᵗ','u':'ᵘ','v':'ᵛ','w':'ʷ','x':'ˣ','y':'ʸ','z':'ᶻ' };
  return map[ch] || '^' + ch;
}

/** 字符转下标 */
function toSubscript(ch) {
  const map = { '0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅','6':'₆','7':'₇','8':'₈','9':'₉',
    '+':'₊','-':'₋','=':'₌','a':'ₐ','e':'ₑ','i':'ᵢ','o':'ₒ','r':'ᵣ','u':'ᵤ','v':'ᵥ','x':'ₓ','n':'ₙ' };
  return map[ch] || '_' + ch;
}

// ============== 屏幕切换 ==============

function showMenu() {
  menuScreen.classList.remove('hidden');
  sceneScreen.classList.add('hidden');
  if (menuBgEl) menuBgEl.classList.remove('menu-bg-hidden');
}

function showScene() {
  sceneScreen.classList.remove('hidden');
  menuScreen.classList.add('hidden');
  if (menuBgEl) menuBgEl.classList.add('menu-bg-hidden');
}

function setSpeakerName(name) {
  activeAssistantName = name || '艾琳老师';
  if (speakerNameEl) speakerNameEl.textContent = activeAssistantName;
}

// ============== 教师头像 ==============

const DEFAULT_AVATAR_URL = '/static/images/teacher.svg';
const portraitEl = document.getElementById('portrait');
const avatarPreviewEl = document.getElementById('avatar-preview');
const avatarFileInput = document.getElementById('avatar-file-input');

function setAvatar(url) {
  const finalUrl = url || DEFAULT_AVATAR_URL;
  if (portraitEl) portraitEl.src = finalUrl;
  if (avatarPreviewEl) avatarPreviewEl.src = finalUrl;
}

function applyPortraitSettings(cfg) {
  const wrap = document.querySelector('.teacher-portrait-wrap');
  if (!wrap) return;
  const posX = cfg.portrait_pos_x != null ? cfg.portrait_pos_x : 50;
  const posY = cfg.portrait_pos_y != null ? cfg.portrait_pos_y : 44;
  const scale = cfg.portrait_scale != null ? cfg.portrait_scale : 1.15;
  const floatAmp = cfg.portrait_float_amplitude != null ? cfg.portrait_float_amplitude : 8;
  const floatEnabled = cfg.portrait_float_enabled !== false;

  wrap.style.setProperty('--portrait-pos-x', posX + '%');
  wrap.style.setProperty('--portrait-pos-y', posY + '%');
  wrap.style.setProperty('--portrait-scale', scale);
  wrap.style.setProperty('--portrait-float-amp', floatAmp + 'px');
  wrap.style.setProperty('--portrait-float-state', floatEnabled ? 'running' : 'paused');
}

async function uploadAvatarFile(file) {
  if (!file) return;
  const btn = document.getElementById('avatar-upload-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '上传中…'; }
  try {
    const form = new FormData();
    form.append('avatar', file);
    if (configModalContext === 'course' && activeLessonFolder) {
      form.append('lesson_folder', activeLessonFolder);
    }
    const response = await fetch('/api/upload_avatar', { method: 'POST', body: form });
    const data = await response.json();
    if (data.ok) {
      if (configModalContext === 'course' && activeLessonFolder) {
        courseConfigCache[activeLessonFolder] = data.config || {};
        showToast('✅ 当前课程头像已更新', 'success');
      } else {
        cachedConfig = data.config || cachedConfig || {};
        cachedConfig.avatar_url = data.avatar_url;
        showToast('✅ 全局头像已更新', 'success');
      }
      setAvatar(data.avatar_url);
    } else {
      showToast(`❌ ${data.message || '头像上传失败'}`, 'error');
    }
  } catch (error) {
    showToast('❌ 头像上传请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '选择图片上传'; }
    if (avatarFileInput) avatarFileInput.value = '';
  }
}

async function resetAvatar() {
  const btn = document.getElementById('avatar-reset-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '处理中…'; }
  try {
    const payload = {};
    if (configModalContext === 'course' && activeLessonFolder) {
      payload.lesson_folder = activeLessonFolder;
    }
    const response = await fetch('/api/reset_avatar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (data.ok) {
      if (configModalContext === 'course' && activeLessonFolder) {
        courseConfigCache[activeLessonFolder] = data.config || {};
        showToast('✅ 已恢复当前课程默认头像', 'success');
      } else {
        cachedConfig = data.config || cachedConfig || {};
        showToast('✅ 已恢复全局默认头像', 'success');
      }
      setAvatar(data.avatar_url);
    } else {
      showToast('❌ 恢复失败', 'error');
    }
  } catch (error) {
    showToast('❌ 恢复请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '恢复默认'; }
  }
}

// ============== 场景背景 ==============

const sceneBgEl = document.querySelector('.scene-bg');
const menuBgEl = document.querySelector('.menu-bg');
const bgPresetButtons = document.querySelectorAll('.bg-preset-btn');
const menuBgPresetButtons = document.querySelectorAll('.menu-bg-preset');
const bgFileInput = document.getElementById('bg-file-input');
const menuBgFileInput = document.getElementById('menu-bg-file-input');

const VALID_BG_THEMES = ['warm', 'sakura', 'bamboo', 'snow', 'dusk', 'night', 'custom'];

function applyBackground(bgTheme, bgUrl) {
  const theme = (bgTheme && VALID_BG_THEMES.includes(bgTheme)) ? bgTheme : 'warm';
  const url = bgUrl || '';
  if (!sceneBgEl) return;

  // 清除所有主题类
  sceneBgEl.classList.remove('theme-warm', 'theme-sakura', 'theme-bamboo', 'theme-snow', 'theme-dusk', 'theme-night', 'theme-custom');
  sceneBgEl.classList.add(`theme-${theme}`);

  if (theme === 'custom' && url) {
    // 用多层 background-image：渐变遮罩在上 + 图片在下
    sceneBgEl.style.backgroundImage = `linear-gradient(180deg, rgba(8, 5, 3, 0.15), rgba(4, 2, 1, 0.55)), url("${url}")`;
    sceneBgEl.style.backgroundSize = 'cover';
    sceneBgEl.style.backgroundPosition = 'center';
    sceneBgEl.style.backgroundRepeat = 'no-repeat';
    sceneBgEl.style.backgroundAttachment = 'fixed';
  } else {
    sceneBgEl.style.backgroundImage = '';
    sceneBgEl.style.backgroundSize = '';
    sceneBgEl.style.backgroundPosition = '';
    sceneBgEl.style.backgroundRepeat = '';
    sceneBgEl.style.backgroundAttachment = '';
  }

  // 同步预设按钮激活状态
  bgPresetButtons.forEach(btn => {
    const t = btn.dataset.theme;
    btn.classList.toggle('active', t === theme);
  });
}

function syncBackgroundPresetUI(theme) {
  const t = (theme && VALID_BG_THEMES.includes(theme)) ? theme : 'warm';
  bgPresetButtons.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.theme === t);
  });
}

async function setBackgroundTheme(theme) {
  if (!VALID_BG_THEMES.includes(theme)) return;
  syncBackgroundPresetUI(theme);
  try {
    const payload = { bg_theme: theme };
    if (configModalContext === 'course' && activeLessonFolder) {
      payload.lesson_folder = activeLessonFolder;
    }
    const response = await fetch('/api/set_background_theme', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (data.ok) {
      if (configModalContext === 'course' && activeLessonFolder) {
        courseConfigCache[activeLessonFolder] = data.config || {};
        showToast('✅ 当前课程背景主题已切换', 'success');
      } else {
        cachedConfig = data.config || cachedConfig || {};
        showToast('✅ 全局背景主题已切换', 'success');
      }
      applyBackground(data.bg_theme, data.bg_url);
      syncBackgroundPresetUI(data.bg_theme);
    } else {
      showToast(`❌ ${data.message || '切换失败'}`, 'error');
    }
  } catch (error) {
    showToast('❌ 主题切换请求失败', 'error');
  }
}

async function uploadBackgroundFile(file) {
  if (!file) return;
  const btn = document.getElementById('bg-upload-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '上传中…'; }
  try {
    const form = new FormData();
    form.append('background', file);
    if (configModalContext === 'course' && activeLessonFolder) {
      form.append('lesson_folder', activeLessonFolder);
    }
    const response = await fetch('/api/upload_background', { method: 'POST', body: form });
    const data = await response.json();
    if (data.ok) {
      if (configModalContext === 'course' && activeLessonFolder) {
        courseConfigCache[activeLessonFolder] = data.config || {};
        showToast('✅ 当前课程自定义背景已更新', 'success');
      } else {
        cachedConfig = data.config || cachedConfig || {};
        showToast('✅ 全局自定义背景已更新', 'success');
      }
      applyBackground(data.bg_theme, data.bg_url);
      syncBackgroundPresetUI(data.bg_theme);
    } else {
      showToast(`❌ ${data.message || '背景上传失败'}`, 'error');
    }
  } catch (error) {
    showToast('❌ 背景上传请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '上传自定义背景'; }
    if (bgFileInput) bgFileInput.value = '';
  }
}

async function resetBackground() {
  const btn = document.getElementById('bg-reset-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '恢复中…'; }
  try {
    const payload = {};
    if (configModalContext === 'course' && activeLessonFolder) {
      payload.lesson_folder = activeLessonFolder;
    }
    const response = await fetch('/api/reset_background', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (data.ok) {
      if (configModalContext === 'course' && activeLessonFolder) {
        courseConfigCache[activeLessonFolder] = data.config || {};
        showToast('✅ 已恢复当前课程默认背景', 'success');
      } else {
        cachedConfig = data.config || cachedConfig || {};
        showToast('✅ 已恢复全局默认背景', 'success');
      }
      applyBackground(data.bg_theme, data.bg_url);
      syncBackgroundPresetUI(data.bg_theme);
    } else {
      showToast('❌ 恢复失败', 'error');
    }
  } catch (error) {
    showToast('❌ 恢复请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '恢复默认主题'; }
  }
}

// ============== 主页（菜单）背景 ==============

function applyMenuBackground(bgTheme, bgUrl) {
  const theme = (bgTheme && VALID_BG_THEMES.includes(bgTheme)) ? bgTheme : 'warm';
  const url = bgUrl || '';
  if (!menuBgEl) return;

  menuBgEl.classList.remove('theme-warm', 'theme-sakura', 'theme-bamboo', 'theme-snow', 'theme-dusk', 'theme-night', 'theme-custom');
  menuBgEl.classList.add(`theme-${theme}`);

  if (theme === 'custom' && url) {
    menuBgEl.style.backgroundImage = `linear-gradient(180deg, rgba(8, 5, 3, 0.15), rgba(4, 2, 1, 0.55)), url("${url}")`;
    menuBgEl.style.backgroundSize = 'cover';
    menuBgEl.style.backgroundPosition = 'center';
    menuBgEl.style.backgroundRepeat = 'no-repeat';
    menuBgEl.style.backgroundAttachment = 'fixed';
  } else {
    menuBgEl.style.backgroundImage = '';
    menuBgEl.style.backgroundSize = '';
    menuBgEl.style.backgroundPosition = '';
    menuBgEl.style.backgroundRepeat = '';
    menuBgEl.style.backgroundAttachment = '';
  }

  syncMenuBgPresetUI(theme);
}

function syncMenuBgPresetUI(theme) {
  const t = (theme && VALID_BG_THEMES.includes(theme)) ? theme : 'warm';
  menuBgPresetButtons.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.theme === t);
  });
}

async function setMenuBackgroundTheme(theme) {
  if (!VALID_BG_THEMES.includes(theme)) return;
  syncMenuBgPresetUI(theme);
  try {
    const response = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ menu_bg_theme: theme }),
    });
    const data = await response.json();
    if (data.status === 'ok') {
      cachedConfig = data.config || cachedConfig || {};
      cachedConfig.menu_bg_theme = theme;
      showToast('✅ 主页背景主题已切换', 'success');
      applyMenuBackground(theme, cachedConfig?.menu_bg_url || '');
    } else {
      showToast('❌ 切换失败', 'error');
    }
  } catch (error) {
    showToast('❌ 主题切换请求失败', 'error');
  }
}

async function uploadMenuBackgroundFile(file) {
  if (!file) return;
  const btn = document.getElementById('menu-bg-upload-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '上传中…'; }
  try {
    const form = new FormData();
    form.append('background', file);
    form.append('target', 'menu');
    const response = await fetch('/api/upload_background', { method: 'POST', body: form });
    const data = await response.json();
    if (data.ok) {
      cachedConfig = data.config || cachedConfig || {};
      showToast('✅ 主页自定义背景已更新', 'success');
      applyMenuBackground('custom', data.menu_bg_url || data.bg_url);
    } else {
      showToast(`❌ ${data.message || '背景上传失败'}`, 'error');
    }
  } catch (error) {
    showToast('❌ 背景上传请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '上传自定义背景'; }
    if (menuBgFileInput) menuBgFileInput.value = '';
  }
}

async function resetMenuBackground() {
  const btn = document.getElementById('menu-bg-reset-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '恢复中…'; }
  try {
    const response = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ menu_bg_theme: 'warm', menu_bg_url: '' }),
    });
    const data = await response.json();
    if (data.status === 'ok') {
      cachedConfig = data.config || cachedConfig || {};
      showToast('✅ 已恢复主页默认背景', 'success');
      applyMenuBackground('warm', '');
    } else {
      showToast('❌ 恢复失败', 'error');
    }
  } catch (error) {
    showToast('❌ 恢复请求失败', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '恢复默认主题'; }
  }
}

function clearConversation() {
  if (conversationEl) conversationEl.innerHTML = '';
  if (dialogueTextEl) {
    dialogueTextEl.textContent = '';
    dialogueTextEl.classList.add('empty');
  }
  segmentQueue = [];
  segmentWaiting = false;
  const indicator = document.getElementById('continue-indicator');
  if (indicator) indicator.remove();
}

// ============== 分段输出（Enter 继续） ==============

function showContinueIndicator() {
  let indicator = document.getElementById('continue-indicator');
  if (!indicator) {
    indicator = document.createElement('div');
    indicator.id = 'continue-indicator';
    indicator.className = 'continue-indicator';
    indicator.textContent = '▼ 按回车继续';
  }
  const dialogueContent = document.querySelector('.dialogue-content');
  if (dialogueContent) dialogueContent.appendChild(indicator);
  indicator.style.display = 'block';
  segmentWaiting = true;
}

function hideContinueIndicator() {
  const indicator = document.getElementById('continue-indicator');
  if (indicator) indicator.style.display = 'none';
  segmentWaiting = false;
}

/** 显示下一段缓冲内容 */
function showNextSegment() {
  if (segmentQueue.length === 0) {
    hideContinueIndicator();
    segmentWaiting = false;
    return false;
  }
  segmentWaiting = false;
  const text = segmentQueue.shift();
  // 同时写入历史面板和底部对话框
  appendBubble(text, 'teacher');
  // 还有下一段时恢复等待状态
  if (segmentQueue.length > 0) {
    segmentWaiting = true;
    showContinueIndicator();
  } else {
    hideContinueIndicator();
  }
  return true;
}

// ============== 对话气泡 ==============

function appendBubble(text, speaker = 'teacher') {
  const bubble = document.createElement('div');
  bubble.className = `bubble ${speaker}`;
  if (speaker === 'teacher') {
    bubble.innerHTML = renderMarkdown(text);
    bubble.setAttribute('data-raw', text);
  } else {
    bubble.textContent = text;
  }
  if (conversationEl) {
    conversationEl.appendChild(bubble);
    conversationEl.scrollTop = conversationEl.scrollHeight;
  }
  // 同步到底部对话框 + 切换说话者名牌
  if (dialogueTextEl) {
    dialogueTextEl.classList.remove('empty');
    if (speaker === 'teacher') {
      dialogueTextEl.innerHTML = renderMarkdown(text);
      if (speakerNameEl && activeAssistantName) speakerNameEl.textContent = activeAssistantName;
    } else {
      dialogueTextEl.textContent = text;
      if (speakerNameEl) speakerNameEl.textContent = '你';
    }
  }
  return bubble;
}

/** 进入新单元后，自动请求老师用云端模型讲解全部知识点 */
async function autoExplainNewUnit() {
  if (!activeLessonFolder) return;
  const explainMsg = '请开始系统性地讲解本课的全部知识点，覆盖所有核心概念和要点。';
  // 标记为云端强制讲解（后端会使用云端模型生成完整课程包）
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: explainMsg,
        lesson_folder: activeLessonFolder,
        force_cloud: true,
        explain_mode: 'full_lesson_package',
      }),
    });
    if (!response.ok) {
      appendBubble('❌ 自动讲解请求失败', 'teacher');
      return;
    }
    // 复用相同的流式读取逻辑
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = false;

    appendBubble(explainMsg, 'user');

    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) { done = true; break; }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      for (const part of parts) {
        const clean = part.trim();
        if (!clean.startsWith('data:')) continue;
        let payload;
        try { payload = JSON.parse(clean.replace(/^data:\s*/, '')); } catch (e) { continue; }

        if (payload.done) {
          if (!payload.tool_event && payload.content) {
            appendBubble(payload.content, 'teacher');
          }
          if (payload.audio_url) playAudio(payload.audio_url);
          if (payload.tool_event === 'start_exam') {
            showToast('📝 本课知识点已讲解完毕，开始随堂测验！', 'info', 3200);
            setActivePanel('exam');
            setTimeout(() => { generateExam(); }, 600);
          }
        } else {
          // 流式中：只更新第一段到底部对话框
          const segIdx = payload.segment || 0;
          const content = payload.content || '';
          if (segIdx === 0 && content && dialogueTextEl) {
            dialogueTextEl.classList.remove('empty');
            dialogueTextEl.innerHTML = renderMarkdown(content);
            if (speakerNameEl && activeAssistantName) speakerNameEl.textContent = activeAssistantName;
          }
        }
      }
    }
  } catch (error) {
    console.error('[autoExplainNewUnit] error:', error);
    appendBubble('❌ 自动讲解失败：' + (error.message || '未知错误'), 'teacher');
  }
}

function showTypingIndicator() {
  if (!typingIndicatorEl) return;
  typingIndicatorEl.classList.remove('hidden');
  conversationEl.scrollTop = conversationEl.scrollHeight;
}

function hideTypingIndicator() {
  if (!typingIndicatorEl) return;
  typingIndicatorEl.classList.add('hidden');
}

function stopTyping() {
  typingToken += 1;
  hideTypingIndicator();
}

// ============== 面板切换 ==============

function setActivePanel(name) {
  document.querySelectorAll('.icon-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.panel === name);
  });
  document.querySelectorAll('.side-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.panel === name);
  });
  document.querySelectorAll('.side-panel-tab').forEach((panel) => {
    panel.classList.toggle('active', panel.dataset.panel === name);
  });
}

// ============== 课时进度条 ==============

/** 根据 unitProgress 缓存刷新顶部进度条 UI。 */
function renderUnitProgress() {
  if (!unitProgressEl) return;
  const { current_unit, total_units, units, has_units } = unitProgress;
  if (!has_units || total_units <= 0) {
    // 旧课程（无 units）：隐藏进度条
    unitProgressEl.classList.add('hidden');
    return;
  }
  unitProgressEl.classList.remove('hidden');
  const idx = Math.max(0, Math.min(current_unit, total_units - 1));
  const pct = total_units > 0 ? ((idx + 1) / total_units) * 100 : 0;
  if (unitProgressFillEl) unitProgressFillEl.style.width = `${pct}%`;
  if (unitProgressLabelEl) unitProgressLabelEl.textContent = `第 ${idx + 1} 课 / 共 ${total_units} 课`;
  const title = units[idx]?.title || '';
  if (unitProgressTitleEl) unitProgressTitleEl.textContent = title ? `· ${title}` : '';
}

/** 从 metadata + progress 构造进度缓存并渲染。 */
function applyUnitProgressFromMetadata(metadata, progress) {
  const units = metadata?.units || [];
  if (!units.length) {
    unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
  } else {
    unitProgress = {
      current_unit: Number(progress?.current_unit || 0),
      total_units: units.length,
      units: units.map((u) => ({ title: u.title || '', summary: u.summary || '' })),
      has_units: true,
    };
  }
  renderUnitProgress();
}

/** 拉取 /api/progress 刷新进度条（用于工具事件后）。 */
async function refreshUnitProgress() {
  if (!activeLessonFolder) return;
  try {
    const resp = await fetch(`/api/progress?lesson_folder=${encodeURIComponent(activeLessonFolder)}`);
    const data = await resp.json();
    if (!resp.ok) return;
    unitProgress = {
      current_unit: Number(data.current_unit || 0),
      total_units: Number(data.total_units || 0),
      units: data.units || [],
      has_units: !!data.has_units,
    };
    renderUnitProgress();
  } catch (e) {
    // 静默失败，不打扰用户
  }
}

// ============== 资源渲染 ==============

function renderResources(resources) {
  resourceListEl.innerHTML = '';
  if (!resources.length) {
    resourceListEl.innerHTML = '<div class="resource-item empty">暂无资料，可先点击“开始备课”生成</div>';
    return;
  }
  resources.forEach((resource, index) => {
    const item = document.createElement('div');
    item.className = 'resource-item';
    const type = resource.type || 'unknown';
    const typeIcon = { pdf: '📄', docx: '📘', doc: '📘', webpage: '🌐', txt: '📝', md: '📝' }[type] || '📎';
    item.innerHTML = `
      <label>
        <input type="checkbox" checked data-index="${index}" />
        <div class="resource-body">
          <div class="resource-title">${typeIcon} ${escapeHtml(resource.title || `资源 ${index + 1}`)}</div>
          <div class="resource-desc"><span class="tag">${escapeHtml(type)}</span> ${escapeHtml(resource.description || '无描述')}</div>
          ${resource.url ? `<a class="resource-url" href="${escapeHtml(resource.url)}" target="_blank" rel="noopener">${escapeHtml(resource.url)}</a>` : ''}
        </div>
      </label>
    `;
    resourceListEl.appendChild(item);
  });
}

// ============== 课程列表 ==============

function renderLessons(lessons) {
  lessonListEl.innerHTML = '';
  if (!lessons.length) {
    lessonListEl.innerHTML = '<div class="lesson-card empty">暂无课程，点击上方“创建课程”开始</div>';
    return;
  }
  lessons.forEach((lesson) => {
    const card = document.createElement('div');
    card.className = 'lesson-card';
    const unitInfo = lesson.units_count
      ? `📚 共 ${lesson.units_count} 课${lesson.last_access ? ` · 当前第 ${(lesson.current_unit || 0) + 1} 课` : ''}`
      : '📁 单课课程';
    card.innerHTML = `
      <div class="lesson-card-head">
        <div class="lesson-card-info">
          <div class="lesson-name">${escapeHtml(lesson.topic || lesson.name)}</div>
          <div class="lesson-meta">${escapeHtml(lesson.name)}</div>
        </div>
        <button class="lesson-delete-btn" title="删除课程" data-name="${escapeHtml(lesson.name)}" data-topic="${escapeHtml(lesson.topic || lesson.name)}">✕</button>
      </div>
      <div class="lesson-meta">${unitInfo}</div>
      <div class="lesson-meta">🕒 ${escapeHtml(lesson.created_at || '未知')}</div>
      <div class="lesson-meta">✏️ ${escapeHtml(lesson.last_access || '未开始')}</div>
    `;
    // 点击卡片主体切换课程；删除按钮单独阻止冒泡
    card.addEventListener('click', () => switchLesson(lesson.name));
    const delBtn = card.querySelector('.lesson-delete-btn');
    if (delBtn) {
      delBtn.addEventListener('click', async (event) => {
        event.stopPropagation();
        await deleteLesson(lesson.name, lesson.topic || lesson.name);
      });
    }
    lessonListEl.appendChild(card);
  });
}

async function deleteLesson(lessonFolder, topic) {
  if (!lessonFolder) return;
  // 用 confirm 二次确认，避免误删
  const ok = window.confirm(`确定要删除课程「${topic}」吗？\n该课程的全部资料与对话记录将被永久删除，且无法恢复。`);
  if (!ok) return;
  try {
    const resp = await fetch(`/api/lessons/${encodeURIComponent(lessonFolder)}`, { method: 'DELETE' });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(`❌ 删除失败：${data.error || '未知错误'}`, 'error');
      appendBubble(`❌ 删除课程失败：${data.error || ''}`);
      return;
    }
    // 若删除的是当前激活课程，清空场景页状态
    if (activeLessonFolder === lessonFolder) {
      activeLessonFolder = '';
      currentResources = [];
      clearConversation();
      renderResources([]);
      unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
      renderUnitProgress();
    }
    showToast(`🗑️ 已删除课程「${topic}」`, 'success');
    await fetchLessons();
  } catch (error) {
    showToast('❌ 删除请求失败', 'error');
  }
}

async function fetchLessons() {
  try {
    const response = await fetch('/api/lessons');
    const data = await response.json();
    renderLessons(data.lessons || []);
  } catch (error) {
    lessonListEl.innerHTML = '<div class="lesson-card empty">课程加载失败，请检查后端服务</div>';
  }
}

// ============== 配置 ==============

async function loadCachedConfig(force = false) {
  if (cachedConfig && !force) return cachedConfig;
  try {
    const response = await fetch('/api/config');
    const globalCfg = await response.json();
    cachedConfig = globalCfg;
  } catch (error) {
    cachedConfig = {};
  }
  return cachedConfig;
}

// 获取课程级配置（仅头像/背景/立绘等视觉类字段）
async function loadCourseConfig(lessonFolder, force = false) {
  if (!lessonFolder) return {};
  if (courseConfigCache[lessonFolder] && !force) return courseConfigCache[lessonFolder];
  try {
    const response = await fetch(`/api/lesson/${encodeURIComponent(lessonFolder)}/config`);
    const data = await response.json();
    if (data.ok) {
      courseConfigCache[lessonFolder] = data.config || {};
      return courseConfigCache[lessonFolder];
    }
  } catch (error) { /* ignore */ }
  return {};
}

// 获取当前生效的配置（全局 + 课程级覆盖）
async function getEffectiveConfig(force = false) {
  const globalCfg = await loadCachedConfig(force);
  if (!activeLessonFolder) return { ...globalCfg };
  const courseCfg = await loadCourseConfig(activeLessonFolder, force);
  // 课程级覆盖全局（仅覆盖有值的字段）
  const merged = { ...globalCfg };
  const visualFields = ['avatar_url', 'bg_theme', 'bg_url', 'portrait_pos_x', 'portrait_pos_y', 'portrait_scale', 'portrait_float_amplitude', 'portrait_float_enabled'];
  visualFields.forEach(key => {
    if (courseCfg[key] !== undefined && courseCfg[key] !== '' && courseCfg[key] !== null) {
      merged[key] = courseCfg[key];
    }
  });
  return merged;
}

function invalidateConfigCache() {
  cachedConfig = null;
  courseConfigCache = {};
}

function syncCloudModelSelect() {
  const value = document.getElementById('siliconflow_model').value || 'deepseek-ai/DeepSeek-V3';
  const select = document.getElementById('cloud_model_select');
  const matched = Array.from(select.options).some((option) => option.value === value);
  select.value = matched ? value : 'custom';
}

function syncTtsCloudModelSelect() {
  const value = document.getElementById('tts_cloud_model').value || 'FunAudioLLM/CosyVoice2-0.5B';
  const select = document.getElementById('tts_cloud_model_select');
  const matched = Array.from(select.options).some((option) => option.value === value);
  select.value = matched ? value : 'custom';
}

function syncChatModelSelect() {
  const value = (document.getElementById('chat_model').value || '').trim();
  const select = document.getElementById('chat_model_select');
  if (!value) { select.value = ''; return; }
  const matched = Array.from(select.options).some((option) => option.value === value);
  select.value = matched ? value : 'custom';
}

// 不同服务商对应的 API Key 提示文案，让用户清楚知道该填哪家的 key
const CLOUD_PROVIDER_KEY_HINTS = {
  siliconflow: { label: 'SiliconFlow API Key', placeholder: 'sk-xxx（硅基流动控制台获取）' },
  deepseek: { label: 'DeepSeek API Key', placeholder: 'sk-xxx（DeepSeek 控制台获取）' },
  openai_compatible: { label: 'API Key', placeholder: '对应服务的 API Key' },
  openai: { label: 'OpenAI API Key', placeholder: 'sk-xxx（OpenAI 平台获取）' },
};

function applyCloudProviderHint(provider) {
  const hint = CLOUD_PROVIDER_KEY_HINTS[provider] || CLOUD_PROVIDER_KEY_HINTS.openai_compatible;
  const labelEl = document.getElementById('cloud_api_key_label');
  const inputEl = document.getElementById('siliconflow_api_key');
  if (labelEl) labelEl.textContent = hint.label;
  if (inputEl) inputEl.placeholder = hint.placeholder;
}

function applyChatProviderHint(provider) {
  const hint = CLOUD_PROVIDER_KEY_HINTS[provider] || CLOUD_PROVIDER_KEY_HINTS.openai_compatible;
  const labelEl = document.getElementById('chat_api_key_label');
  const inputEl = document.getElementById('chat_api_key');
  if (labelEl) labelEl.textContent = `${hint.label}（留空 = 复用备课 Key）`;
  if (inputEl) inputEl.placeholder = `留空则复用备课 Key，独立配置则填 ${hint.placeholder}`;
}

async function openConfigModal(source = 'menu') {
  // source: 'menu' = 主页打开（全局），'scene' = 对话界面打开（课程级）
  configModalContext = (source === 'scene' && activeLessonFolder) ? 'course' : 'global';

  // 加载基础配置（模型等全局设置）
  const globalConfig = await loadCachedConfig();
  // 加载当前生效的视觉配置（头像/背景/立绘：课程级覆盖全局）
  const effectiveConfig = await getEffectiveConfig();

  // 模型配置始终用全局
  document.getElementById('ollama_base_url').value = globalConfig.ollama_base_url || 'http://127.0.0.1:11434';
  document.getElementById('ollama_model').value = globalConfig.ollama_model || 'qwen2.5:7B';
  document.getElementById('ollama_num_ctx').value = globalConfig.ollama_num_ctx || 16384;
  document.getElementById('ollama_temperature').value = globalConfig.ollama_temperature ?? 0.7;
  document.getElementById('ollama_num_predict').value = globalConfig.ollama_num_predict || 1024;
  document.getElementById('tts_base_url').value = globalConfig.tts_base_url || 'http://127.0.0.1:8000';
  document.getElementById('tts_voice').value = globalConfig.tts_voice || 'zh-CN-XiaoxiaoNeural';
  document.getElementById('tts_enabled').checked = !!globalConfig.tts_enabled;
  document.getElementById('tts_provider').value = globalConfig.tts_provider || 'local';
  document.getElementById('enable_local_ollama').checked = globalConfig.enable_local_ollama !== false;
  // 备课云端
  const cloudProvider = globalConfig.cloud_provider || 'siliconflow';
  document.getElementById('cloud_provider').value = cloudProvider;
  document.getElementById('cloud_base_url').value = globalConfig.cloud_base_url || 'https://api.siliconflow.cn/v1';
  document.getElementById('siliconflow_api_key').value = globalConfig.cloud_api_key || globalConfig.siliconflow_api_key || '';
  document.getElementById('siliconflow_model').value = globalConfig.cloud_model || globalConfig.siliconflow_model || 'deepseek-ai/DeepSeek-V3';
  applyCloudProviderHint(cloudProvider);
  // 对话云端（独立配置）
  const chatProvider = globalConfig.chat_provider || 'openai_compatible';
  document.getElementById('chat_provider').value = chatProvider;
  document.getElementById('chat_base_url').value = globalConfig.chat_base_url || '';
  document.getElementById('chat_api_key').value = globalConfig.chat_api_key || '';
  document.getElementById('chat_model').value = globalConfig.chat_model || '';
  document.getElementById('chat_enable_search').checked = !!globalConfig.chat_enable_search;
  applyChatProviderHint(chatProvider);
  // 语音 TTS
  document.getElementById('tts_provider').value = globalConfig.tts_provider || 'local';
  document.getElementById('tts_base_url').value = globalConfig.tts_base_url || 'http://127.0.0.1:8000';
  document.getElementById('tts_voice').value = globalConfig.tts_voice || 'zh-CN-XiaoxiaoNeural';
  document.getElementById('tts_enabled').checked = !!globalConfig.tts_enabled;
  // 修正旧配置
  const rawTtsModel = globalConfig.tts_cloud_model && globalConfig.tts_cloud_model !== 'FunAudioLLM/Speech-1'
    ? globalConfig.tts_cloud_model
    : 'FunAudioLLM/CosyVoice2-0.5B';
  document.getElementById('tts_cloud_model').value = rawTtsModel;
  const ttsVoiceSelect = document.getElementById('tts_cloud_voice');
  const savedVoice = globalConfig.tts_cloud_voice || 'anna';
  ttsVoiceSelect.value = Array.from(ttsVoiceSelect.options).some((o) => o.value === savedVoice) ? savedVoice : 'anna';
  document.getElementById('tts_cloud_response_format').value = globalConfig.tts_cloud_response_format || 'mp3';
  document.getElementById('auto_play_tts').checked = globalConfig.auto_play_tts !== false;
  // 教师人格 / 默认主题（全局）
  document.getElementById('personality_prompt').value = globalConfig.personality_prompt || '你是一位温柔、专业、耐心的 AI 学习导师。请以启发式提问方式指导学生，先解释概念，再给出例子和练习。';
  document.getElementById('assistant_name').value = globalConfig.assistant_name || '艾琳老师';
  document.getElementById('default_topic').value = globalConfig.default_topic || 'Python 基础';
  document.getElementById('enable_search').checked = globalConfig.enable_search !== false;
  syncCloudModelSelect();
  syncTtsCloudModelSelect();
  syncChatModelSelect();

  // 视觉配置：使用 effective（课程级覆盖全局）
  setAvatar(effectiveConfig.avatar_url || DEFAULT_AVATAR_URL);
  applyBackground(effectiveConfig.bg_theme || 'warm', effectiveConfig.bg_url || '');
  syncBackgroundPresetUI(effectiveConfig.bg_theme || 'warm');
  // 主页背景（仅全局配置）
  applyMenuBackground(globalConfig.menu_bg_theme || 'warm', globalConfig.menu_bg_url || '');
  syncMenuBgPresetUI(globalConfig.menu_bg_theme || 'warm');

  // 立绘位置与动画（用 effective config）
  const posX = effectiveConfig.portrait_pos_x ?? 50;
  const posY = effectiveConfig.portrait_pos_y ?? 44;
  const scale = effectiveConfig.portrait_scale ?? 1.15;
  const floatAmp = effectiveConfig.portrait_float_amplitude ?? 8;
  const floatEnabled = effectiveConfig.portrait_float_enabled !== false;

  const posXInput = document.getElementById('portrait-pos-x');
  const posYInput = document.getElementById('portrait-pos-y');
  const scaleInput = document.getElementById('portrait-scale');
  const floatAmpInput = document.getElementById('portrait-float-amp');
  const floatEnabledInput = document.getElementById('portrait-float-enabled');

  if (posXInput) posXInput.value = posX;
  if (posYInput) posYInput.value = posY;
  if (scaleInput) scaleInput.value = scale;
  if (floatAmpInput) floatAmpInput.value = floatAmp;
  if (floatEnabledInput) floatEnabledInput.checked = floatEnabled;

  document.getElementById('portrait-pos-x-val').textContent = posX;
  document.getElementById('portrait-pos-y-val').textContent = posY;
  document.getElementById('portrait-scale-val').textContent = Number(scale).toFixed(2);
  document.getElementById('portrait-float-val').textContent = floatAmp;

  applyPortraitSettings({
    portrait_pos_x: posX, portrait_pos_y: posY,
    portrait_scale: scale, portrait_float_amplitude: floatAmp,
    portrait_float_enabled: floatEnabled,
  });

  // 绑定滑块实时预览
  const bindPortraitPreview = () => {
    const liveCfg = {
      portrait_pos_x: Number(posXInput.value),
      portrait_pos_y: Number(posYInput.value),
      portrait_scale: Number(scaleInput.value),
      portrait_float_amplitude: Number(floatAmpInput.value),
      portrait_float_enabled: floatEnabledInput.checked,
    };
    document.getElementById('portrait-pos-x-val').textContent = liveCfg.portrait_pos_x;
    document.getElementById('portrait-pos-y-val').textContent = liveCfg.portrait_pos_y;
    document.getElementById('portrait-scale-val').textContent = liveCfg.portrait_scale.toFixed(2);
    document.getElementById('portrait-float-val').textContent = liveCfg.portrait_float_amplitude;
    applyPortraitSettings(liveCfg);
  };
  [posXInput, posYInput, scaleInput, floatAmpInput, floatEnabledInput].forEach(el => {
    if (el) el.oninput = bindPortraitPreview;
  });
  document.getElementById('portrait-reset-btn').onclick = () => {
    posXInput.value = 50; posYInput.value = 44;
    scaleInput.value = 1.15; floatAmpInput.value = 8;
    floatEnabledInput.checked = true;
    bindPortraitPreview();
  };

  // 上下文提示横幅
  const banner = document.getElementById('lesson-config-banner');
  const bannerText = document.getElementById('lesson-config-banner-text');
  const bannerHint = document.querySelector('.lesson-config-hint');
  const resetGlobalBtn = document.getElementById('reset-to-global-btn');
  if (banner && bannerText) {
    banner.classList.remove('hidden');
    if (configModalContext === 'course') {
      bannerText.textContent = `当前编辑：课程「${activeLessonFolder}」的设置`;
      if (bannerHint) bannerHint.textContent = '（仅影响此对话的头像/背景/立绘）';
      if (resetGlobalBtn) resetGlobalBtn.style.display = '';
    } else {
      bannerText.textContent = '当前编辑：全局设置';
      if (bannerHint) bannerHint.textContent = '（影响所有课程的模型/人设等）';
      if (resetGlobalBtn) resetGlobalBtn.style.display = 'none';
    }
  }

  configModal.classList.remove('hidden');
}

function closeConfigModal() {
  configModal.classList.add('hidden');
}

// ============== 新建课程配置弹窗 ==============

async function openNewCourseModal() {
  const config = await loadCachedConfig();
  // 预填默认值（来自全局配置）
  document.getElementById('nc-topic').value = config.default_topic || menuTopicInput.value || 'Python 基础';
  document.getElementById('nc-assistant-name').value = config.assistant_name || '艾琳老师';
  document.getElementById('nc-personality').value = config.personality_prompt || '你是一位温柔、专业、耐心的 AI 学习导师。请以启发式提问方式指导学生，先解释概念，再给出例子和练习。';

  // 模型下拉：匹配全局备课模型
  const globalModel = config.cloud_model || config.siliconflow_model || '';
  const modelSelect = document.getElementById('nc-model-select');
  const matched = Array.from(modelSelect.options).some((o) => o.value === globalModel);
  modelSelect.value = matched ? globalModel : (globalModel ? 'custom' : '');
  const customInput = document.getElementById('nc-model-custom');
  customInput.value = matched ? '' : globalModel;
  customInput.classList.toggle('hidden', modelSelect.value !== 'custom');

  // 音色：匹配全局云端音色
  const globalVoice = config.tts_cloud_voice || '';
  const voiceSelect = document.getElementById('nc-voice');
  voiceSelect.value = globalVoice && Array.from(voiceSelect.options).some((o) => o.value === globalVoice) ? globalVoice : '';

  newCourseModal.classList.remove('hidden');
}

function closeNewCourseModal() {
  newCourseModal.classList.add('hidden');
}

function showCompletionModal() {
  const modal = document.getElementById('completion-modal');
  if (modal) modal.classList.remove('hidden');
}

function closeCompletionModal() {
  const modal = document.getElementById('completion-modal');
  if (modal) modal.classList.add('hidden');
}

function collectConfigFromForm() {
  return {
    ollama_base_url: document.getElementById('ollama_base_url').value,
    ollama_model: document.getElementById('ollama_model').value,
    ollama_num_ctx: Number(document.getElementById('ollama_num_ctx').value) || 16384,
    ollama_temperature: Number(document.getElementById('ollama_temperature').value),
    ollama_num_predict: Number(document.getElementById('ollama_num_predict').value) || 1024,
    tts_base_url: document.getElementById('tts_base_url').value,
    tts_voice: document.getElementById('tts_voice').value,
    tts_enabled: document.getElementById('tts_enabled').checked,
    tts_provider: document.getElementById('tts_provider').value,
    tts_cloud_voice: document.getElementById('tts_cloud_voice').value,
    tts_cloud_model: document.getElementById('tts_cloud_model').value,
    tts_cloud_response_format: document.getElementById('tts_cloud_response_format').value,
    enable_local_ollama: document.getElementById('enable_local_ollama').checked,
    // 备课云端
    cloud_provider: document.getElementById('cloud_provider').value,
    cloud_base_url: document.getElementById('cloud_base_url').value,
    cloud_api_key: document.getElementById('siliconflow_api_key').value,
    cloud_model: document.getElementById('siliconflow_model').value,
    siliconflow_api_key: document.getElementById('siliconflow_api_key').value,
    siliconflow_model: document.getElementById('siliconflow_model').value,
    enable_search: document.getElementById('enable_search').checked,
    // 对话云端（独立配置）
    chat_provider: document.getElementById('chat_provider').value,
    chat_base_url: document.getElementById('chat_base_url').value,
    chat_api_key: document.getElementById('chat_api_key').value,
    chat_model: document.getElementById('chat_model').value,
    chat_enable_search: document.getElementById('chat_enable_search').checked,
    // 人格 / 默认值
    personality_prompt: document.getElementById('personality_prompt').value,
    assistant_name: document.getElementById('assistant_name').value,
    default_topic: document.getElementById('default_topic').value,
    auto_play_tts: document.getElementById('auto_play_tts').checked,
    // 立绘位置与动画
    portrait_pos_x: Number(document.getElementById('portrait-pos-x').value),
    portrait_pos_y: Number(document.getElementById('portrait-pos-y').value),
    portrait_scale: Number(document.getElementById('portrait-scale').value),
    portrait_float_amplitude: Number(document.getElementById('portrait-float-amp').value),
    portrait_float_enabled: document.getElementById('portrait-float-enabled').checked,
  };
}

async function saveConfigFromForm(event) {
  event.preventDefault();
  const payload = collectConfigFromForm();

  if (configModalContext === 'course' && activeLessonFolder) {
    // 课程级保存：只保存视觉类字段到课程 config.json
    const visualFields = ['avatar_url', 'bg_theme', 'bg_url', 'portrait_pos_x', 'portrait_pos_y', 'portrait_scale', 'portrait_float_amplitude', 'portrait_float_enabled'];
    const visualPayload = {};
    visualFields.forEach(key => { if (payload[key] !== undefined) visualPayload[key] = payload[key]; });

    try {
      const response = await fetch(`/api/lesson/${encodeURIComponent(activeLessonFolder)}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(visualPayload),
      });
      const data = await response.json();
      if (data.ok) {
        courseConfigCache[activeLessonFolder] = { ...courseConfigCache[activeLessonFolder], ...visualPayload };
        // 重新计算生效配置并应用
        const effective = await getEffectiveConfig(true);
        setAvatar(effective.avatar_url || DEFAULT_AVATAR_URL);
        applyBackground(effective.bg_theme || 'warm', effective.bg_url || '');
        applyPortraitSettings(effective);
        showToast('✅ 当前课程设置已保存', 'success');
      } else {
        showToast('❌ 课程设置保存失败', 'error');
      }
      closeConfigModal();
    } catch (error) {
      showToast('❌ 保存请求失败', 'error');
    }
  } else {
    // 全局保存
    try {
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      cachedConfig = data.config || payload;
      setSpeakerName(cachedConfig.assistant_name);
      if (data.status === 'ok') {
        showToast('✅ 全局配置已保存', 'success');
        setAvatar(cachedConfig.avatar_url || DEFAULT_AVATAR_URL);
        applyBackground(cachedConfig.bg_theme || 'warm', cachedConfig.bg_url || '');
        applyPortraitSettings(cachedConfig);
      } else {
        showToast('❌ 配置保存失败', 'error');
      }
      closeConfigModal();
    } catch (error) {
      showToast('❌ 配置保存请求失败', 'error');
    }
  }
}

async function testLocalConnection(kind) {
  const payload = { kind, config: collectConfigFromForm() };
  const btn = kind === 'ollama' ? document.getElementById('test-ollama-btn') : document.getElementById('test-tts-btn');
  if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = '测试中…'; }
  try {
    const response = await fetch('/api/config/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    appendBubble(`🔍 ${data.message || '测试结果为空'}`);
  } catch (error) {
    appendBubble('❌ 测试请求失败');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.original || '测试'; }
  }
}

// ============== 备课 ==============

function setBusy(btn, busy, busyText = '处理中…') {
  if (!btn) return;
  if (busy) {
    btn.dataset.original = btn.textContent;
    btn.disabled = true;
    btn.textContent = busyText;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.original || btn.textContent;
  }
}

async function prepareLesson(source = 'scene', overrides = {}) {
  // source='menu' 表示从主页触发，优先用主页输入框；否则优先场景页输入框
  const fromMenu = source === 'menu';
  const topic = (overrides.topic || (fromMenu ? menuTopicInput.value : topicInput.value) || (fromMenu ? topicInput.value : menuTopicInput.value) || '').trim();
  if (!topic) {
    showToast('⚠️ 请输入一个学习主题', 'warning');
    appendBubble('请输入一个学习主题再开始备课。');
    return;
  }
  topicInput.value = topic;
  menuTopicInput.value = topic;

  // 新备课：清空旧对话/旧资源/旧课程上下文，避免串到上一门课
  stopTyping();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  activeLessonFolder = '';
  currentResources = [];
  clearConversation();
  renderResources([]);
  // 清空考试状态
  examQuestions = [];
  examAnswers = [];
  currentExamIndex = 0;
  examSubmitted = false;
  if (examListEl) examListEl.innerHTML = '';
  if (examActionsEl) {
    examActionsEl.innerHTML = '';
    examActionsEl.classList.add('hidden');
  }
  // 清空进度
  unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
  renderUnitProgress();

  showScene();
  const cfg = await getEffectiveConfig();
  const assistantName = overrides.assistant_name || cfg.assistant_name || '艾琳老师';
  setSpeakerName(assistantName);
  // 应用生效配置（此时 activeLessonFolder 为空，等同于全局）
  setAvatar(cfg.avatar_url || DEFAULT_AVATAR_URL);
  applyBackground(cfg.bg_theme || 'warm', cfg.bg_url || '');
  syncBackgroundPresetUI(cfg.bg_theme || 'warm');
  applyPortraitSettings(cfg);
  showPrepareRow();
  appendBubble(`正在为"${topic}"准备课程内容……`);
  showTypingIndicator();

  const btn = document.getElementById('prepare-btn');
  setBusy(btn, true, '备课中…');

  const requestBody = {
    topic,
    personality_prompt: overrides.personality_prompt || cfg.personality_prompt || '',
    assistant_name: assistantName,
    tts_voice: overrides.tts_voice || cfg.tts_voice || 'zh-CN-XiaoxiaoNeural',
  };
  // 若用户在新建课程弹窗里指定了模型/音色，覆盖全局配置
  if (overrides.cloud_model) {
    requestBody.cloud_model = overrides.cloud_model;
  }
  if (overrides.tts_cloud_voice) {
    requestBody.tts_cloud_voice = overrides.tts_cloud_voice;
  }

  try {
    const response = await fetch('/api/prepare_lesson', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody),
    });

    const data = await response.json();
    hideTypingIndicator();
    if (!response.ok) {
      appendBubble(`❌ ${data.error || '备课失败'}`);
      return;
    }

    activeLessonFolder = data.lesson_folder;

    // 备课完成后，重新加载生效配置（课程级覆盖全局）
    const effCfg = await getEffectiveConfig(true);
    setAvatar(effCfg.avatar_url || DEFAULT_AVATAR_URL);
    applyBackground(effCfg.bg_theme || 'warm', effCfg.bg_url || '');
    syncBackgroundPresetUI(effCfg.bg_theme || 'warm');
    applyPortraitSettings(effCfg);

    currentResources = data.plan.resources || [];
    renderResources(currentResources);
    const quizCount = (data.plan.quiz_preset || []).length;
    // 备课返回 units 结构时刷新进度条
    const units = data.plan.units || [];
    if (units.length) {
      unitProgress = {
        current_unit: 0,
        total_units: units.length,
        units: units.map((u) => ({ title: u.title || '', summary: u.summary || '' })),
        has_units: true,
      };
    } else {
      unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
    }
    renderUnitProgress();
    const unitHint = units.length ? `、${units.length} 课` : '';
    const msg =
      `✅ 备课完成：${data.lesson_folder}\n\n已生成课程大纲与 ${currentResources.length} 个资源${
        quizCount ? `、${quizCount} 道预设测验题` : ''
      }${unitHint}。你可以现在下载资料，或直接和我开始对话。`;
    appendBubble(msg);
    showToast(`✅ 备课完成（${currentResources.length} 份资料${quizCount ? `+${quizCount}道题` : ''}${unitHint}）`, 'success');
    hidePrepareRow();
    await fetchLessons();
  } catch (error) {
    hideTypingIndicator();
    showToast('❌ 备课失败', 'error');
    appendBubble('❌ 备课请求失败，请检查后端服务是否已启动。');
  } finally {
    setBusy(btn, false);
  }
}

async function switchLesson(lessonFolder) {
  stopTyping();
  try {
    const response = await fetch('/api/switch_lesson', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lesson_folder: lessonFolder }),
    });

    const data = await response.json();
    if (!response.ok) {
      showToast('❌ 切换失败', 'error');
      appendBubble(`❌ ${data.error || '切换课程失败'}`);
      return;
    }

    activeLessonFolder = lessonFolder;
    currentResources = data.metadata?.resources || [];
    renderResources(currentResources);
    clearConversation();
    // 清空考试状态
    examQuestions = [];
    examAnswers = [];
    currentExamIndex = 0;
    examSubmitted = false;
    if (examListEl) examListEl.innerHTML = '';
    if (examActionsEl) {
      examActionsEl.innerHTML = '';
      examActionsEl.classList.add('hidden');
    }
    // 应用生效配置（全局 + 课程级覆盖）
    const effectiveCfg = await getEffectiveConfig();
    setSpeakerName(effectiveCfg.assistant_name || '艾琳老师');
    setAvatar(effectiveCfg.avatar_url || DEFAULT_AVATAR_URL);
    applyBackground(effectiveCfg.bg_theme || 'warm', effectiveCfg.bg_url || '');
    syncBackgroundPresetUI(effectiveCfg.bg_theme || 'warm');
    applyPortraitSettings(effectiveCfg);
    const history = data.conversation || [];
    history.forEach((entry) => {
      appendBubble(entry.content, entry.role === 'user' ? 'user' : 'teacher');
    });
    // 切换课程后刷新进度条（有 units 显示，无则隐藏）
    applyUnitProgressFromMetadata(data.metadata, data.progress);
    showScene();
    hidePrepareRow();
    showToast('✅ 已加载课程', 'success');
    appendBubble(data.message || `已加载${lessonFolder}课程，继续上次的进度`);
  } catch (error) {
    showToast('❌ 切换失败', 'error');
    appendBubble('❌ 切换课程请求失败');
  }
}

async function downloadSelectedResources() {
  const selected = [];
  resourceListEl.querySelectorAll('input[type="checkbox"]').forEach((checkbox, index) => {
    if (checkbox.checked) selected.push(index);
  });

  if (!selected.length) {
    showToast('⚠️ 请先勾选要下载的资料', 'warning');
    appendBubble('请选择至少一个资源后再下载。');
    return;
  }

  const btn = document.getElementById('download-btn');
  setBusy(btn, true, '下载中…');
  showToast(`📥 开始下载 ${selected.length} 份资料…`, 'info');
  appendBubble(`开始下载并转换 ${selected.length} 个资源……`);

  try {
    const response = await fetch('/api/download_resources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected, lesson_folder: activeLessonFolder }),
    });
    const data = await response.json();
    if (!response.ok) {
      showToast(`❌ 下载失败：${data.error || '网络错误'}`, 'error');
      appendBubble(`❌ ${data.error || '下载失败'}`);
      return;
    }
    showToast(`✅ 已下载 ${data.downloads.length} 份资料`, 'success');
    appendBubble(`✅ 资源已下载完成，共 ${data.downloads.length} 个文件，已自动转换为 Markdown 供对话使用。`);
  } catch (error) {
    showToast('❌ 下载请求失败', 'error');
    appendBubble('❌ 下载请求失败');
  } finally {
    setBusy(btn, false);
  }
}

// ============== 对话 ==============

function playAudio(url, onEnded) {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  const audio = new Audio(url);
  currentAudio = audio;
  audio.addEventListener('ended', () => { if (onEnded) onEnded(); });
  audio.play().catch(() => { if (onEnded) onEnded(); });
}

async function sendMessage() {
  if (!activeLessonFolder) {
    appendBubble('请先选择或新建课程，再开始聊天。');
    return;
  }

  const text = messageInput.value.trim();
  if (!text) return;

  // CLI 命令拦截：以 / 开头走命令处理
  if (text.startsWith('/')) {
    handleCliCommand(text);
    messageInput.value = '';
    return;
  }

  appendBubble(text, 'user');
  messageInput.value = '';
  stopTyping();
  hideContinueIndicator();
  const replyToken = ++typingToken;
  showTypingIndicator();

  const sendBtn = document.getElementById('send-btn');
  setBusy(sendBtn, true, '生成中…');

  // 流式期间收集每段的完整文本（用于 done 后的分段队列）
  const segmentTextMap = {};  // { segIdx: latestText }
  let isMultiSegment = false;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, lesson_folder: activeLessonFolder }),
    });

    if (!response.ok) {
      hideTypingIndicator();
      appendBubble('❌ 对话请求失败');
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';

      for (const part of parts) {
        const clean = part.trim();
        if (!clean.startsWith('data:')) continue;
        let payload;
        try {
          payload = JSON.parse(clean.replace(/^data:\s*/, ''));
        } catch (e) {
          continue;
        }
        if (replyToken !== typingToken) continue;
        if (!payload.content && !payload.done) continue;

        if (payload.done) {
          hideTypingIndicator();
          const isToolEvent = !!payload.tool_event;
          const segCount = payload.segment_count || 1;

          if (!isToolEvent) {
            if (segCount <= 1) {
              // 单段：直接创建气泡
              if (payload.content) {
                appendBubble(payload.content, 'teacher');
              }
            } else {
              // 多段模式：收集所有段文本，第一段入历史，其余入队等待 Enter
              const collected = [];
              const segIdxs = Object.keys(segmentTextMap).map(Number).sort((a, b) => a - b);
              for (const idx of segIdxs) {
                if (segmentTextMap[idx]) collected.push(segmentTextMap[idx]);
              }
              if (payload.content && collected.length === 0) {
                collected.push(payload.content);
              }
              if (collected.length > 0) {
                // 第一段创建历史气泡 + 更新底部对话
                appendBubble(collected[0], 'teacher');
                // 其余入队
                segmentQueue = collected.slice(1);
                segmentWaiting = segmentQueue.length > 0;
                if (segmentWaiting) showContinueIndicator();
              }
            }
          }
          if (payload.audio_url && !isToolEvent) {
            lastAudioUrl = payload.audio_url;
            const cfg = cachedConfig || {};
            const shouldAutoPlay = cfg.auto_play_tts !== false;
            if (shouldAutoPlay) {
              playAudio(payload.audio_url);
            }
          }
          if (payload.tool_event === 'start_exam') {
            showToast('📝 老师认为本课讲完啦，开始随堂测验！', 'info', 3200);
            setActivePanel('exam');
            setTimeout(() => { generateExam(); }, 600);
          } else if (payload.tool_event === 'next_unit') {
            showToast('➡️ 已进入下一课', 'success', 2800);
            if (payload.progress && payload.units) {
              unitProgress = {
                current_unit: Number(payload.progress.current_unit || 0),
                total_units: (payload.units || []).length,
                units: (payload.units || []).map((u) => ({ title: u.title || '', summary: u.summary || '' })),
                has_units: true,
              };
              renderUnitProgress();
            } else {
              refreshUnitProgress();
            }
            // 自动触发：老师讲解新单元知识点（云端一次性返回完整课程包）
            setTimeout(() => {
              autoExplainNewUnit();
            }, 1200);
          }
        } else {
          // 流式中：只更新底部对话区（仅第一段），不创建历史气泡
          const segIdx = payload.segment || 0;
          const content = payload.content || '';
          segmentTextMap[segIdx] = content;
          // 超过1段标记为多段
          if (Object.keys(segmentTextMap).length > 1) isMultiSegment = true;

          hideTypingIndicator();
          // 只有第一段（segIdx===0）才更新底部对话框，后续段只收集不入显示
          if (segIdx === 0 && dialogueTextEl) {
            dialogueTextEl.classList.remove('empty');
            dialogueTextEl.innerHTML = renderMarkdown(content);
            if (speakerNameEl && activeAssistantName) speakerNameEl.textContent = activeAssistantName;
          }
        }
      }
    }
    hideTypingIndicator();
  } catch (error) {
    hideTypingIndicator();
    appendBubble('❌ 对话请求失败，请确认后端服务已启动。');
  } finally {
    setBusy(sendBtn, false);
  }
}

// ============== 测验 ==============

function optionLetter(option, index) {
  const m = String(option || '').trim().match(/^([A-Za-z])[.、)]\s*/);
  if (m) return m[1].toUpperCase();
  return String.fromCharCode(65 + index);
}

function optionText(option) {
  return String(option || '').replace(/^\s*[A-Za-z][.、)]\s*/, '').trim();
}

async function generateExam() {
  const btn = document.getElementById('exam-btn');
  setBusy(btn, true, '出题中…');
  examListEl.innerHTML = '';
  if (examActionsEl) {
    examActionsEl.innerHTML = '';
    examActionsEl.classList.add('hidden');
  }

  const loading = document.createElement('div');
  loading.className = 'exam-item';
  loading.textContent = '正在生成题目…';
  examListEl.appendChild(loading);

  try {
    const response = await fetch('/api/exam/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic: topicInput.value || 'Python 基础' }),
    });
    const data = await response.json();
    examQuestions = data.questions || [];
    examListEl.innerHTML = '';

    if (!examQuestions.length) {
      const empty = document.createElement('div');
      empty.className = 'exam-item empty';
      empty.textContent = '暂无题目';
      examListEl.appendChild(empty);
      return;
    }

    // 初始化逐题答题状态
    examAnswers = new Array(examQuestions.length).fill(null);
    currentExamIndex = 0;
    examSubmitted = false;
    renderExamQuestion(currentExamIndex);
  } catch (error) {
    console.error('[exam] generate error:', error);
    examListEl.innerHTML = '';
    if (examActionsEl) {
      examActionsEl.innerHTML = '';
      examActionsEl.classList.add('hidden');
    }
    const err = document.createElement('div');
    err.className = 'exam-item empty';
    err.textContent = '题目生成失败';
    examListEl.appendChild(err);
  } finally {
    setBusy(btn, false);
  }
}

// 保存当前题的答案到 examAnswers
function saveCurrentAnswer() {
  const index = currentExamIndex;
  const item = examQuestions[index];
  if (!item) return;
  const qtype = item.type || 'single';

  if (qtype === 'multiple') {
    const container = document.querySelector(`.exam-opts[data-qindex="${index}"]`);
    const selected = container ? container.querySelectorAll('.opt-row.selected') : [];
    const letters = Array.from(selected).map(el => el.dataset.value || '').sort().join('');
    examAnswers[index] = letters || null;
  } else if (qtype === 'fill') {
    const input = document.querySelector(`.fill-input[data-qindex="${index}"]`);
    const val = input ? input.value.trim() : '';
    examAnswers[index] = val || null;
  } else {
    const container = document.querySelector(`.exam-opts[data-qindex="${index}"]`);
    const selected = container ? container.querySelector('.opt-row.selected') : null;
    const val = selected ? selected.dataset.value || '' : '';
    examAnswers[index] = val || null;
  }
}

// 渲染指定题目
function renderExamQuestion(index) {
  const item = examQuestions[index];
  if (!item) return;

  examListEl.innerHTML = '';

  const block = document.createElement('div');
  block.className = 'exam-item';
  const qtype = item.type || 'single';

  // 进度指示
  const progressEl = document.createElement('div');
  progressEl.className = 'exam-progress';
  progressEl.textContent = `第 ${index + 1} / ${examQuestions.length} 题`;
  block.appendChild(progressEl);

  const typeLabel = { single: '【单选】', multiple: '【多选】', boolean: '【判断】', fill: '【填空】' }[qtype] || '';
  const qTitle = document.createElement('div');
  qTitle.className = 'exam-q';
  qTitle.innerHTML = renderMarkdown(`${typeLabel} ${item.question || ''}`);
  block.appendChild(qTitle);

  const optsDiv = document.createElement('div');
  optsDiv.className = 'exam-opts';
  optsDiv.dataset.qtype = qtype;
  optsDiv.dataset.qindex = index;

  if (qtype === 'single' || qtype === 'multiple') {
    (item.options || []).forEach((option, oi) => {
      const letter = optionLetter(option, oi);
      const text = optionText(option);
      const row = document.createElement('div');
      row.className = 'opt-row';
      row.dataset.value = letter;
      row.dataset.qtype = qtype;
      row.dataset.qindex = index;

      // 恢复已选状态
      if (qtype === 'multiple') {
        if (examAnswers[index] && examAnswers[index].includes(letter)) {
          row.classList.add('selected');
        }
      } else {
        if (examAnswers[index] === letter) {
          row.classList.add('selected');
        }
      }

      const letterSpan = document.createElement('span');
      letterSpan.className = 'opt-letter';
      letterSpan.textContent = letter;

      const textSpan = document.createElement('span');
      textSpan.className = 'opt-text';
      textSpan.textContent = text;

      row.appendChild(letterSpan);
      row.appendChild(textSpan);

      row.addEventListener('click', () => {
        if (qtype === 'multiple') {
          row.classList.toggle('selected');
        } else {
          optsDiv.querySelectorAll('.opt-row').forEach(r => r.classList.remove('selected'));
          row.classList.add('selected');
        }
      });

      optsDiv.appendChild(row);
    });
  } else if (qtype === 'boolean') {
    [{ v: 'T', sym: '✓', txt: '正确' }, { v: 'F', sym: '✗', txt: '错误' }].forEach((opt) => {
      const row = document.createElement('div');
      row.className = 'opt-row';
      row.dataset.value = opt.v;
      row.dataset.qtype = 'boolean';
      row.dataset.qindex = index;

      // 恢复已选状态
      if (examAnswers[index] === opt.v) {
        row.classList.add('selected');
      }

      const symSpan = document.createElement('span');
      symSpan.className = 'opt-letter';
      symSpan.textContent = opt.sym;

      const textSpan = document.createElement('span');
      textSpan.className = 'opt-text';
      textSpan.textContent = opt.txt;

      row.appendChild(symSpan);
      row.appendChild(textSpan);

      row.addEventListener('click', () => {
        optsDiv.querySelectorAll('.opt-row').forEach(r => r.classList.remove('selected'));
        row.classList.add('selected');
      });

      optsDiv.appendChild(row);
    });
  } else if (qtype === 'fill') {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'fill-input';
    input.dataset.qindex = index;
    input.placeholder = '请输入答案…';
    // 恢复已填答案
    if (examAnswers[index]) {
      input.value = examAnswers[index];
    }
    optsDiv.appendChild(input);
  }

  block.appendChild(optsDiv);
  examListEl.appendChild(block);

  // 渲染底部按钮
  if (examActionsEl) {
    examActionsEl.innerHTML = '';
    examActionsEl.classList.remove('hidden');

    // 上一题按钮
    if (index > 0) {
      const prevBtn = document.createElement('button');
      prevBtn.type = 'button';
      prevBtn.className = 'ghost-btn';
      prevBtn.textContent = '上一题';
      prevBtn.addEventListener('click', () => {
        saveCurrentAnswer();
        currentExamIndex--;
        renderExamQuestion(currentExamIndex);
      });
      examActionsEl.appendChild(prevBtn);
    }

    const isLast = index === examQuestions.length - 1;
    const nextBtn = document.createElement('button');
    nextBtn.type = 'button';
    nextBtn.className = 'primary-btn';
    nextBtn.textContent = isLast ? '提交答案' : '下一题';
    nextBtn.addEventListener('click', () => {
      console.log('[exam] button clicked, isLast=', isLast, 'currentIndex=', currentExamIndex);
      // 先保存当前题答案
      const item = examQuestions[currentExamIndex];
      if (item) {
        const qtype = item.type || 'single';
        const idx = currentExamIndex;
        if (qtype === 'multiple') {
          const container = document.querySelector(`.exam-opts[data-qindex="${idx}"]`);
          const selected = container ? container.querySelectorAll('.opt-row.selected') : [];
          examAnswers[idx] = Array.from(selected).map(el => el.dataset.value || '').sort().join('') || null;
        } else if (qtype === 'fill') {
          const input = document.querySelector(`.fill-input[data-qindex="${idx}"]`);
          examAnswers[idx] = input ? input.value.trim() || null : null;
        } else {
          const container = document.querySelector(`.exam-opts[data-qindex="${idx}"]`);
          const selected = container ? container.querySelector('.opt-row.selected') : null;
          examAnswers[idx] = selected ? selected.dataset.value || null : null;
        }
      }
      if (isLast) {
        console.log('[exam] calling submitExam, answers=', JSON.stringify(examAnswers));
        submitExam(nextBtn);
      } else {
        currentExamIndex++;
        renderExamQuestion(currentExamIndex);
      }
    });
    examActionsEl.appendChild(nextBtn);
  }
}

async function submitExam(btn) {
  console.log('[exam] submitExam called, examSubmitted=', examSubmitted);
  if (examSubmitted) {
    appendBubble('⏳ 正在提交中，请稍候…');
    return;
  }
  if (btn) {
    setBusy(btn, true, '⏳ 提交中…');
    btn.style.pointerEvents = 'none';
  }
  // 视觉反馈：在考试区显示进度动画
  const statusEl = document.createElement('div');
  statusEl.className = 'exam-submit-status';
  statusEl.innerHTML = '<span class="submit-spinner"></span> 正在提交答案到服务器…';
  if (examListEl) examListEl.appendChild(statusEl);

  const answers = examAnswers.slice();
  console.log('[exam] answers for submit:', JSON.stringify(answers));
  let unanswered = false;
  answers.forEach((a) => { if (!a) unanswered = true; });

  if (unanswered) {
    statusEl.remove();
    appendBubble('⚠️ 还有题目未作答，请全部完成后再提交。');
    if (btn) {
      setBusy(btn, false);
      btn.style.pointerEvents = '';
    }
    return;
  }

  examSubmitted = true;
  statusEl.innerHTML = '<span class="submit-spinner"></span> 等待服务器返回成绩…';

  try {
    console.log('[exam] sending fetch to /api/exam/submit');
    const t0 = Date.now();
    const response = await fetch('/api/exam/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
    });
    console.log('[exam] fetch completed in', Date.now() - t0, 'ms, status=', response.status);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    console.log('[exam] response data:', JSON.stringify(data));
    statusEl.remove();
    const score = data.score ?? 0;
    const passed = score >= 80;
    const tag = score === 100 ? '🎉' : passed ? '✅' : '❌';
    const wrongDetails = data.wrong_details || [];

    // 立即用 toast 显示成绩（在任何面板都可见）
    showToast(`${tag} 考试结果：${score} 分（答对 ${data.correct}/${data.total} 题）`, passed ? 'success' : 'error', 4000);

    // 切回对话界面
    setActivePanel('chat');

    // 在聊天框显示成绩
    let resultText;
    if (data.total === 0) {
      resultText = '⚠️ 考试系统异常：本次未生成有效题目，请重新发起随堂测验。';
    } else {
      resultText = `${tag} 考试结果：${score} 分（答对 ${data.correct}/${data.total} 题）`;
      if (wrongDetails.length) {
        resultText += `\n\n错题：\n${wrongDetails.map(w => `第${w.index}题：${w.question}\n  你的答案：${w.student_answer}\n  正确答案：${w.correct_answer}`).join('\n')}`;
      }
    }
    appendBubble(resultText);

    // 清空考试区域
    examListEl.innerHTML = '';
    if (examActionsEl) {
      examActionsEl.innerHTML = '';
      examActionsEl.classList.add('hidden');
    }

    if (passed && unitProgress.has_units && data.total > 0) {
      const cur = unitProgress.current_unit;
      const total = unitProgress.total_units;
      if (cur + 1 < total) {
        // 及格且有下一课：进入下一课并触发老师讲课
        setTimeout(async () => {
          appendBubble('📖 考试通过，自动进入下一章节…');
          try {
            const resp = await fetch('/api/lesson/next_unit', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ folder: activeLessonFolder }),
            });
            const nd = await resp.json();
            if (nd.success) {
              showToast('➡️ 已进入下一课', 'success');
              if (nd.progress && nd.units) {
                unitProgress = {
                  current_unit: Number(nd.progress.current_unit || 0),
                  total_units: (nd.units || []).length,
                  units: (nd.units || []).map(u => ({ title: u.title || '', summary: u.summary || '' })),
                  has_units: true,
                };
                renderUnitProgress();
              }
              clearConversation();
            } else {
              showToast(nd.message || '无法进入下一课', 'error');
            }
          } catch {
            showToast('请求失败', 'error');
          }
          messageInput.value = '请开始讲下一课';
          sendMessage();
        }, 2000);
      } else {
        // 最后一章及格：弹出恭喜界面
        setTimeout(() => {
          showCompletionModal();
        }, 1500);
      }
    } else if (data.total === 0) {
      // 未生成有效题目：不自动发消息，让用户重试
      examSubmitted = false;
      if (btn) {
        setBusy(btn, false);
        btn.style.pointerEvents = '';
      }
    } else {
      // 不及格或无分课：让老师讲解错题
      examSubmitted = false;
      if (btn) {
        setBusy(btn, false);
        btn.style.pointerEvents = '';
      }
      // 自动发消息给老师
      if (wrongDetails.length) {
        const wrongList = wrongDetails.map(w => `第${w.index}题（你选了${w.student_answer}，正确答案是${w.correct_answer}）`).join('，');
        messageInput.value = `刚刚的考试情况如下：我得了${score}分，答对${data.correct}/${data.total}题。其中${wrongList}做错了，请你为我讲解这些知识点。`;
      } else {
        // 无错题但未通过：可能是0分，让老师重新讲解
        messageInput.value = `刚刚的考试情况如下：我得了${score}分，请你为我重新讲解本课的知识点。`;
      }
      setTimeout(() => { sendMessage(); }, 800);
    }
  } catch (error) {
    console.error('[exam] submit error:', error);
    if (statusEl.parentNode) statusEl.remove();
    appendBubble('❌ 提交答案失败：' + (error.message || '未知错误'));
    examSubmitted = false;
    if (btn) {
      setBusy(btn, false);
      btn.style.pointerEvents = '';
    }
  } finally {
    // 兜底：只要 examSubmitted 为 false（未通过/出错），都恢复按钮
    if (btn && !examSubmitted) {
      setBusy(btn, false);
      btn.style.pointerEvents = '';
    }
  }
}

// ============== CLI 命令 ==============

function handleCliCommand(raw) {
  const cmd = raw.trim().toLowerCase();
  const parts = cmd.split(/\s+/);
  const action = parts[0];

  switch (action) {
    case '/exam':
    case '/出题':
      if (!activeLessonFolder) { appendBubble('⚠️ 请先创建课程。'); return; }
      appendBubble(raw, 'user');
      setActivePanel('exam');
      generateExam();
      break;

    case '/next':
    case '/下一课':
      if (!activeLessonFolder) { appendBubble('⚠️ 请先创建课程。'); return; }
      appendBubble(raw, 'user');
      forceNextUnit();
      break;

    case '/reset':
    case '/重置':
      if (!activeLessonFolder) { appendBubble('⚠️ 请先创建课程。'); return; }
      appendBubble(raw, 'user');
      forceResetProgress();
      break;

    case '/help':
    case '/帮助':
      appendBubble(raw, 'user');
      appendBubble(
        '可用命令：\n' +
        '• /exam — 强制生成随堂测验\n' +
        '• /next — 进入下一课\n' +
        '• /reset — 重置课程进度\n' +
        '• /help — 显示此帮助'
      );
      break;

    default:
      appendBubble(raw, 'user');
      appendBubble(`未知命令：${action}\n输入 /help 查看可用命令。`);
  }
}

function forceNextUnit() {
  const folder = activeLessonFolder;
  fetch('/api/lesson/next_unit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        showToast('➡️ 已进入下一课', 'success');
        if (data.progress && data.units) {
          unitProgress = {
            current_unit: Number(data.progress.current_unit || 0),
            total_units: (data.units || []).length,
            units: (data.units || []).map(u => ({ title: u.title || '', summary: u.summary || '' })),
            has_units: true,
          };
          renderUnitProgress();
        }
      } else {
        showToast(data.message || '无法进入下一课', 'error');
      }
    })
    .catch(() => showToast('请求失败', 'error'));
}

function forceResetProgress() {
  const folder = activeLessonFolder;
  fetch('/api/lesson/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        showToast('🔄 进度已重置', 'success');
        unitProgress = { current_unit: 0, total_units: 0, units: [], has_units: false };
        renderUnitProgress();
      } else {
        showToast(data.message || '重置失败', 'error');
      }
    })
    .catch(() => showToast('请求失败', 'error'));
}

// ============== Toast 提示 ==============

function showToast(message, type = 'info', durationMs = 2600) {
  if (!toastContainer) return;
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  toastContainer.appendChild(el);
  // 触发入场动画
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 280);
  }, durationMs);
}

// ============== 事件绑定 ==============

const cloudModelSelect = document.getElementById('cloud_model_select');
if (cloudModelSelect) {
  cloudModelSelect.addEventListener('change', (event) => {
    const value = event.target.value;
    const modelInput = document.getElementById('siliconflow_model');
    if (value === 'custom') {
      modelInput?.focus();
      return;
    }
    if (modelInput) modelInput.value = value;
  });
}

const ttsCloudModelSelect = document.getElementById('tts_cloud_model_select');
if (ttsCloudModelSelect) {
  ttsCloudModelSelect.addEventListener('change', (event) => {
    const value = event.target.value;
    const modelInput = document.getElementById('tts_cloud_model');
    if (value === 'custom') {
      modelInput?.focus();
      return;
    }
    if (modelInput) modelInput.value = value;
  });
}

const chatModelSelect = document.getElementById('chat_model_select');
if (chatModelSelect) {
  chatModelSelect.addEventListener('change', (event) => {
    const value = event.target.value;
    const modelInput = document.getElementById('chat_model');
    if (value === 'custom') {
      modelInput?.focus();
      return;
    }
    if (modelInput) modelInput.value = value || '';
  });
}

// 服务商切换时同步 API Key 标签/占位符，让用户清楚该填哪家的 key
const cloudProviderSelect = document.getElementById('cloud_provider');
if (cloudProviderSelect) {
  cloudProviderSelect.addEventListener('change', (event) => applyCloudProviderHint(event.target.value));
}
const chatProviderSelect = document.getElementById('chat_provider');
if (chatProviderSelect) {
  chatProviderSelect.addEventListener('change', (event) => applyChatProviderHint(event.target.value));
}

// 备课/切换课程后隐藏备课行（避免重复备课）
function hidePrepareRow() {
  const row = document.querySelector('.prepare-row');
  if (row) row.classList.add('hidden');
}
function showPrepareRow() {
  const row = document.querySelector('.prepare-row');
  if (row) row.classList.remove('hidden');
}

function bindClick(id, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', handler);
}

bindClick('prepare-btn', prepareLesson);
bindClick('download-btn', downloadSelectedResources);
bindClick('send-btn', sendMessage);
bindClick('exam-btn', generateExam);

bindClick('new-course-btn', openNewCourseModal);
bindClick('close-new-course-btn', closeNewCourseModal);
// 快速创建：直接用主页输入框的内容开始备课，跳过弹窗
bindClick('menu-new-course-btn', () => {
  const topic = menuTopicInput.value.trim();
  if (!topic) { showToast('⚠️ 请输入课程主题', 'warning'); return; }
  prepareLesson('menu', { topic });
});
bindClick('config-btn', () => openConfigModal('menu'));
bindClick('scene-config-btn', () => openConfigModal('scene'));
bindClick('close-config-btn', closeConfigModal);
bindClick('completion-close-btn', closeCompletionModal);
bindClick('completion-back-btn', () => {
  closeCompletionModal();
  sceneScreen.classList.add('hidden');
  menuScreen.classList.remove('hidden');
});

const configForm = document.getElementById('config-form');
if (configForm) configForm.addEventListener('submit', saveConfigFromForm);

// 重置课程级设置为全局
bindClick('reset-to-global-btn', async () => {
  if (!activeLessonFolder) return;
  if (!confirm('确定将此课程的视觉设置（头像/背景/立绘）清除，恢复为全局设置？')) return;
  try {
    // 清空课程 config.json 中的视觉字段
    const courseCfg = await loadCourseConfig(activeLessonFolder);
    const visualFields = ['avatar_url', 'bg_theme', 'bg_url', 'portrait_pos_x', 'portrait_pos_y', 'portrait_scale', 'portrait_float_amplitude', 'portrait_float_enabled'];
    const cleared = {};
    visualFields.forEach(key => { cleared[key] = null; });
    const response = await fetch(`/api/lesson/${encodeURIComponent(activeLessonFolder)}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cleared),
    });
    const data = await response.json();
    if (data.ok) {
      courseConfigCache[activeLessonFolder] = {};
      // 重新加载全局和课程配置
      invalidateConfigCache();
      const effective = await getEffectiveConfig(true);
      setAvatar(effective.avatar_url || DEFAULT_AVATAR_URL);
      applyBackground(effective.bg_theme || 'warm', effective.bg_url || '');
      applyPortraitSettings(effective);
      showToast('✅ 已恢复为全局设置', 'success');
      // 刷新弹窗
      openConfigModal('scene');
    } else {
      showToast('❌ 重置失败', 'error');
    }
  } catch (error) {
    showToast('❌ 请求失败', 'error');
  }
});

// 新建课程弹窗：表单提交
const newCourseForm = document.getElementById('new-course-form');
if (newCourseForm) {
  newCourseForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const topic = document.getElementById('nc-topic').value.trim();
    if (!topic) { showToast('⚠️ 请输入课程主题', 'warning'); return; }

    const overrides = {
      topic,
      personality_prompt: document.getElementById('nc-personality').value.trim(),
      assistant_name: document.getElementById('nc-assistant-name').value.trim(),
    };
    const modelSelect = document.getElementById('nc-model-select').value;
    if (modelSelect === 'custom') {
      const customModel = document.getElementById('nc-model-custom').value.trim();
      if (customModel) overrides.cloud_model = customModel;
    } else if (modelSelect) {
      overrides.cloud_model = modelSelect;
    }
    const voice = document.getElementById('nc-voice').value;
    if (voice) overrides.tts_cloud_voice = voice;

    closeNewCourseModal();
    prepareLesson('menu', overrides);
  });
}

// 新建课程弹窗：模型下拉切换
const ncModelSelect = document.getElementById('nc-model-select');
if (ncModelSelect) {
  ncModelSelect.addEventListener('change', () => {
    const customInput = document.getElementById('nc-model-custom');
    customInput.classList.toggle('hidden', ncModelSelect.value !== 'custom');
  });
}

// 新建课程弹窗：点击遮罩关闭
if (newCourseModal) {
  newCourseModal.addEventListener('click', (event) => {
    if (event.target === newCourseModal) closeNewCourseModal();
  });
}

bindClick('test-ollama-btn', () => testLocalConnection('ollama'));
bindClick('test-tts-btn', () => testLocalConnection('tts'));

bindClick('avatar-upload-btn', () => avatarFileInput && avatarFileInput.click());
bindClick('avatar-reset-btn', resetAvatar);
if (avatarFileInput) {
  avatarFileInput.addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) uploadAvatarFile(file);
  });
}

bindClick('bg-upload-btn', () => bgFileInput && bgFileInput.click());
bindClick('bg-reset-btn', resetBackground);
if (bgFileInput) {
  bgFileInput.addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) uploadBackgroundFile(file);
  });
}
bgPresetButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    const theme = btn.dataset.theme;
    if (theme && theme !== 'custom') setBackgroundTheme(theme);
  });
});

// 主页背景事件
bindClick('menu-bg-upload-btn', () => menuBgFileInput && menuBgFileInput.click());
bindClick('menu-bg-reset-btn', resetMenuBackground);
if (menuBgFileInput) {
  menuBgFileInput.addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) uploadMenuBackgroundFile(file);
  });
}
menuBgPresetButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    const theme = btn.dataset.theme;
    if (theme && theme !== 'custom') setMenuBackgroundTheme(theme);
  });
});

bindClick('back-menu-btn', () => {
  stopTyping();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  showMenu();
});

document.querySelectorAll('.icon-btn').forEach((btn) => {
  btn.addEventListener('click', () => setActivePanel(btn.dataset.panel));
});

// 侧面板 tab 切换
document.querySelectorAll('.side-tab').forEach((tab) => {
  tab.addEventListener('click', () => setActivePanel(tab.dataset.panel));
});

// 历史记录面板折叠/展开
bindClick('history-toggle-btn', () => {
  if (sidePanelEl) sidePanelEl.classList.toggle('collapsed');
});

if (messageInput) {
  messageInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      // 分段等待模式：Enter 继续显示下一段
      if (segmentWaiting) {
        showNextSegment();
        return;
      }
      sendMessage();
    }
  });
}

// 全局 Enter 键：分段等待时继续（即使输入框无焦点）
document.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && segmentWaiting) {
    // 不在输入框中时也响应
    const target = event.target;
    if (!target || (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA')) {
      event.preventDefault();
      showNextSegment();
    }
  }
});

// 全局 ESC 键：关闭弹窗 / 返回菜单 / 跳过剩余分段
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;

  // 1. 分段等待中：跳过所有剩余分段
  if (segmentWaiting) {
    event.preventDefault();
    segmentQueue = [];
    hideContinueIndicator();
    segmentWaiting = false;
    return;
  }

  // 2. 关闭弹窗（优先级：completion > config > new-course）
  const completionModal = document.getElementById('completion-modal');
  if (completionModal && !completionModal.classList.contains('hidden')) {
    event.preventDefault();
    closeCompletionModal();
    return;
  }
  if (configModal && !configModal.classList.contains('hidden')) {
    event.preventDefault();
    closeConfigModal();
    return;
  }
  if (newCourseModal && !newCourseModal.classList.contains('hidden')) {
    event.preventDefault();
    closeNewCourseModal();
    return;
  }

  // 3. 在对话界面：返回菜单
  if (sceneScreen && !sceneScreen.classList.contains('hidden')) {
    event.preventDefault();
    stopTyping();
    if (currentAudio) { currentAudio.pause(); currentAudio = null; }
    showMenu();
  }
});

// 点击 modal 遮罩关闭
if (configModal) {
  configModal.addEventListener('click', (event) => {
    if (event.target === configModal) closeConfigModal();
  });
}

// ============== 初始化 ==============

(async function init() {
  showMenu();
  await loadCachedConfig();
  setSpeakerName(cachedConfig?.assistant_name || '艾琳老师');
  setAvatar(cachedConfig?.avatar_url || DEFAULT_AVATAR_URL);
  applyBackground(cachedConfig?.bg_theme || 'warm', cachedConfig?.bg_url || '');
  syncBackgroundPresetUI(cachedConfig?.bg_theme || 'warm');
  applyMenuBackground(cachedConfig?.menu_bg_theme || 'warm', cachedConfig?.menu_bg_url || '');
  applyPortraitSettings(cachedConfig || {});
  if (cachedConfig?.default_topic) {
    topicInput.value = cachedConfig.default_topic;
    menuTopicInput.value = cachedConfig.default_topic;
  }
  fetchLessons();
  renderResources([]);
  setActivePanel('chat');
})();
