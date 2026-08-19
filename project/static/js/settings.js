'use strict';

            // ============================
            // AI 工具调用：终端 / 图片 / 黑板（由 [TOOL:{"type":...}] 触发，本地模型可随时调用）
            // ============================
            const _aiDelay = (ms) => new Promise(function(r) { setTimeout(r, ms); });

            async function handleAITool(tool) {
                try {
                    if (!tool || typeof tool !== 'object') return;
                    switch (tool.type) {
                        case 'show_terminal':
                            await showTerminal({ language: tool.language, code: tool.code, stdout: tool.stdout, stderr: tool.stderr, autoRun: !tool.noRun });
                            break;
                        case 'show_image':
                            await showImagePanel({ index: tool.index, filename: tool.filename });
                            break;
                        case 'show_board':
                            await showBoardWithContent(tool.content || '');
                            break;
                        default:
                            console.warn('未知 AI 工具类型:', tool.type);
                    }
                } catch (e) {
                    console.warn('AI 工具执行失败:', e);
                }
            }
            window.handleAITool = handleAITool;

            // 黑板打字机渲染入口：模型"蹲下→向上伸手→站起来"后，黑板拉下并逐字书写内容
            let _boardTypingActive = false;   // 黑板正在打字机书写
            let _boardTypingCancel = false;   // 用户已关闭，要求终止书写
            async function showBoardWithContent(content) {
                // 防重复弹出：黑板正在打字机书写时，忽略再次触发
                // （模型在流式回复中偶发输出多个 show_board 标记 / SSE 重复帧，
                //   反复拉起会让用户觉得"莫名其妙弹出"且"关不掉"）
                if (_boardTypingActive) return;
                // 隐藏其它面板避免重叠
                hideImagePanel();
                hideTerminal();
                const contentLayer = document.getElementById('board-content-layer');
                if (!contentLayer) return;
                contentLayer.innerHTML = '';
                // 显示内容层，隐藏 canvas 图片层
                boardOverlayCanvas.style.display = 'none';
                contentLayer.style.display = 'block';
                _boardTypingActive = true;
                _boardTypingCancel = false;
                // 1) 模型"蹲下→向上伸手→站起来"（窗口动画并行）
                const p = modelReachPanel('up');
                // 2) 等模型伸手到位（~400ms），黑板拉下 + 打字机开始书写
                setTimeout(function() {
                    boardOverlay.style.display = 'flex';
                    void boardOverlay.offsetWidth;
                    // 清掉上次拖拽残留的内联 transform/animation，让黑板每次从屏幕上方居中拉下
                    boardOverlay.style.transition = '';
                    boardOverlay.style.animation = '';
                    boardOverlay.style.transform = '';
                    boardOverlay.classList.add('board-active');
                    typeBoardContent(content, contentLayer);
                }, 400);
                // 3) 表演完成后，角色眼神看向上方黑板
                await p;
                if (typeof live2dModel !== 'undefined' && live2dModel && typeof live2dModel.focus === 'function') {
                    try { live2dModel.focus(0.0, 0.7); } catch (e) {}
                }
            }
            window.showBoardWithContent = showBoardWithContent;

            // 板书自由拖拽：按住标题栏拖动整个黑板到任意位置。
            // 注意：黑板的 left/top 被 CSS 用 !important 锁定为 50%/50%（居中模态），
            // 内联 left/top 无法覆盖，因此拖拽必须改用 transform 偏移实现。
            (function initBoardDrag() {
                const overlay = document.getElementById('board-overlay');
                const head = document.querySelector('#board-overlay .board-overlay-head');
                if (!overlay || !head) return;
                let dragging = false, startX = 0, startY = 0;
                let dx = 0, dy = 0;   // 相对 CSS 锚点（视口中心）的累计偏移
                function applyDrag() {
                    overlay.style.transform = 'translate(calc(-50% + ' + dx + 'px), calc(-50% + ' + dy + 'px)) scale(1)';
                }
                head.addEventListener('mousedown', function(e) {
                    if (e.target.closest('#board-overlay-close')) return;   // 点关闭按钮不拖拽
                    dragging = true;
                    startX = e.clientX;
                    startY = e.clientY;
                    // 先按当前视觉位置反推累计偏移，再随鼠标累加，保证拖拽起点不跳变
                    const rect = overlay.getBoundingClientRect();
                    dx = (rect.left + rect.width / 2) - window.innerWidth / 2;
                    dy = (rect.top + rect.height / 2) - window.innerHeight / 2;
                    overlay.style.transition = 'none';   // 拖拽期间禁用过渡
                    overlay.style.animation = 'none';    // 停止"拉下/关闭"动画，改用 transform 定位
                    applyDrag();
                    e.preventDefault();
                });
                document.addEventListener('mousemove', function(e) {
                    if (!dragging) return;
                    dx += e.clientX - startX;
                    dy += e.clientY - startY;
                    startX = e.clientX;
                    startY = e.clientY;
                    applyDrag();
                });
                document.addEventListener('mouseup', function() {
                    if (!dragging) return;
                    dragging = false;
                    // 不恢复 animation/transition：boardEmerge 早已播放完毕（forwards 保持），
                    // 恢复会触发重播导致黑板弹回中心；保持内联 transform 即可停在拖拽位置。
                });
            })();

            // 打字机渲染黑板内容：普通文本逐字 + $$公式$$（KaTeX）+ {graph:y=..} 曲线 + {line:..} 线段
            async function typeBoardContent(content, layer) {
                content = cleanBoardContent(content);
                const tokens = tokenizeBoardContent(content);
                let p = null;        // 当前文本段落
                let caret = null;    // 打字光标
                for (const t of tokens) {
                    if (_boardTypingCancel) return;   // 用户已关闭黑板：立即终止书写
                    if (t.kind === 'char') {
                        if (t.ch === '\n') {
                            if (caret) { caret.remove(); caret = null; }
                            p = null;
                            await _aiDelay(150);
                            continue;
                        }
                        if (!p || p.classList.contains('math-block')) {
                            p = document.createElement('p');
                            layer.appendChild(p);
                        }
                        if (!caret || caret.parentNode !== p) {
                            if (caret) caret.remove();
                            caret = document.createElement('span');
                            caret.className = 'typing-caret';
                            p.appendChild(caret);
                        }
                        caret.before(document.createTextNode(t.ch));
                        await _aiDelay(45);
                        layer.scrollTop = layer.scrollHeight;
                    } else if (t.kind === 'latex') {
                        if (caret) { caret.remove(); caret = null; }
                        if (t.block) {
                            // 块公式：独占一行居中（KaTeX display 模式）
                            p = null;
                            const div = document.createElement('div');
                            div.className = 'math-block';
                            try {
                                if (typeof katex !== 'undefined') {
                                    katex.render(t.value, div, { throwOnError: false, displayMode: true });
                                } else {
                                    div.textContent = t.value;
                                }
                            } catch (e) {
                                div.textContent = t.value;
                            }
                            layer.appendChild(div);
                            await _aiDelay(320);
                        } else {
                            // 行内公式：嵌入当前段落（KaTeX inline 模式），光标保留继续打字
                            if (!p || p.classList.contains('math-block')) { p = document.createElement('p'); layer.appendChild(p); }
                            const span = document.createElement('span');
                            span.className = 'board-inline-math';
                            try {
                                if (typeof katex !== 'undefined') {
                                    katex.render(t.value, span, { throwOnError: false, displayMode: false });
                                } else {
                                    span.textContent = t.value;
                                }
                            } catch (e) {
                                span.textContent = t.value;
                            }
                            if (caret) { caret.before(span); } else { p.appendChild(span); }
                            await _aiDelay(160);
                        }
                        layer.scrollTop = layer.scrollHeight;
                    } else if (t.kind === 'graph') {
                        if (caret) { caret.remove(); caret = null; }
                        p = null;
                        const svg = buildGraphSVG(t.value);
                        if (svg) { svg.classList.add('board-graph'); layer.appendChild(svg); }
                        await _aiDelay(360);
                        layer.scrollTop = layer.scrollHeight;
                    } else if (t.kind === 'line') {
                        if (caret) { caret.remove(); caret = null; }
                        p = null;
                        const svg = buildLineSVG(t.value);
                        if (svg) { svg.classList.add('board-graph'); layer.appendChild(svg); }
                        await _aiDelay(300);
                        layer.scrollTop = layer.scrollHeight;
                    }
                }
                if (caret) caret.remove();
                _boardTypingActive = false;   // 书写完成，允许后续再触发
            }

            // 黑板内容清洗：保护 LaTeX 公式块后清除 \c 分段符（避免黑板上逐字打出 \c / 公式内 \cdot 被误删）
            function cleanBoardContent(content) {
                const held = [];
                let s = String(content || '');
                s = s.replace(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\$[^$\n]*\$|\\\([\s\S]*?\\\)/g,
                    m => { held.push(m); return '\u0000L' + (held.length - 1) + '\u0000'; });
                // 字面量 \n（模型 JSON 转义丢失，直接输出反斜杠+n）→ 真实换行；
                // 换行前残留的反斜杠（\\n → \ 换行）一并清理
                s = s.replace(/\\n/g, '\n');
                s = s.replace(/\\\n/g, '\n');
                s = s.replace(/\\+c/g, ' ');
                s = s.replace(/\u0000L(\d+)\u0000/g, (m, i) => held[+i] || m);
                // 剥 markdown 粗体标记 **（黑板不渲染 markdown，逐字打出会显示星号）
                s = s.replace(/\*\*/g, '');
                // 兜底：独立成行的裸 c（模型把 \c 转义丢失后的写法）
                s = s.split('\n').map(l => /^\s*c\s*$/.test(l) ? '' : l).join('\n');
                return s.trim();
            }

            // 解析黑板内容为 token 流：文本字符 / $$公式$$ / {graph:..} / {line:..}
            function tokenizeBoardContent(content) {
                const tokens = [];
                const s = String(content || '');
                let i = 0;
                while (i < s.length) {
                    // $$...$$ 公式（块）
                    if (s.startsWith('$$', i)) {
                        const end = s.indexOf('$$', i + 2);
                        if (end > i) {
                            tokens.push({ kind: 'latex', value: s.slice(i + 2, end), block: true });
                            i = end + 2;
                            continue;
                        }
                    }
                    // $...$ 行内公式（嵌入段落文本中渲染，不再逐字打出 $ 号）
                    if (s[i] === '$') {
                        const end = s.indexOf('$', i + 1);
                        if (end > i) {
                            tokens.push({ kind: 'latex', value: s.slice(i + 1, end), block: false });
                            i = end + 1;
                            continue;
                        }
                    }
                    // {graph:...} / {line:...} 图形
                    if (s[i] === '{') {
                        const close = s.indexOf('}', i);
                        if (close > i) {
                            const inner = s.slice(i + 1, close);
                            if (inner.startsWith('graph:')) {
                                tokens.push({ kind: 'graph', value: inner.slice(6).trim() });
                                i = close + 1;
                                continue;
                            }
                            if (inner.startsWith('line:')) {
                                tokens.push({ kind: 'line', value: inner.slice(5).trim() });
                                i = close + 1;
                                continue;
                            }
                        }
                    }
                    tokens.push({ kind: 'char', ch: s[i] });
                    i++;
                }
                return tokens;
            }

            // 函数曲线 SVG：{graph:y=sin(x), x:-3.14..3.14}
            function buildGraphSVG(spec) {
                try {
                    const parts = String(spec).split(',');
                    let expr = '', xmin = -5, xmax = 5;
                    parts.forEach(function(part) {
                        part = part.trim();
                        if (part.startsWith('y=')) expr = part.slice(2).trim();
                        else if (part.startsWith('x:')) {
                            const mm = part.slice(2).match(/^([-\d.]+)\.\.([-\d.]+)$/);
                            if (mm) { xmin = parseFloat(mm[1]); xmax = parseFloat(mm[2]); }
                        }
                    });
                    if (!expr) return null;
                    const jsExpr = expr
                        .replace(/\^/g, '**')
                        .replace(/sin\(/g, 'Math.sin(')
                        .replace(/cos\(/g, 'Math.cos(')
                        .replace(/tan\(/g, 'Math.tan(')
                        .replace(/sqrt\(/g, 'Math.sqrt(')
                        .replace(/log\(/g, 'Math.log(')
                        .replace(/abs\(/g, 'Math.abs(')
                        .replace(/pi/g, 'Math.PI');
                    const fn = new Function('x', 'return (' + jsExpr + ');');
                    const W = 400, H = 260, pad = 24, samples = 220;
                    const pts = [];
                    let ymin = Infinity, ymax = -Infinity;
                    for (let k = 0; k <= samples; k++) {
                        const x = xmin + (xmax - xmin) * k / samples;
                        let y;
                        try { y = fn(x); } catch (e) { continue; }
                        if (!isFinite(y)) continue;
                        if (y < ymin) ymin = y;
                        if (y > ymax) ymax = y;
                        pts.push([x, y]);
                    }
                    if (!pts.length || ymin === ymax) return null;
                    const X = function(x) { return pad + (x - xmin) / (xmax - xmin) * (W - 2 * pad); };
                    const Y = function(y) { return pad + (ymax - y) / (ymax - ymin) * (H - 2 * pad); };
                    const NS = 'http://www.w3.org/2000/svg';
                    const svg = document.createElementNS(NS, 'svg');
                    svg.setAttribute('width', W);
                    svg.setAttribute('height', H);
                    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
                    // 坐标轴
                    const ax = document.createElementNS(NS, 'line');
                    ax.setAttribute('x1', X(0)); ax.setAttribute('y1', pad);
                    ax.setAttribute('x2', X(0)); ax.setAttribute('y2', H - pad);
                    ax.setAttribute('stroke', 'rgba(' + accentRGB() + ',0.5)'); ax.setAttribute('stroke-width', '1');
                    const ay = document.createElementNS(NS, 'line');
                    ay.setAttribute('x1', pad); ay.setAttribute('y1', Y(0));
                    ay.setAttribute('x2', W - pad); ay.setAttribute('y2', Y(0));
                    ay.setAttribute('stroke', 'rgba(' + accentRGB() + ',0.5)'); ay.setAttribute('stroke-width', '1');
                    svg.appendChild(ax); svg.appendChild(ay);
                    // 曲线
                    const poly = document.createElementNS(NS, 'polyline');
                    poly.setAttribute('fill', 'none');
                    poly.setAttribute('stroke', '#f0e6c8');
                    poly.setAttribute('stroke-width', '2');
                    poly.setAttribute('points', pts.map(function(pt) {
                        return X(pt[0]).toFixed(1) + ',' + Y(pt[1]).toFixed(1);
                    }).join(' '));
                    svg.appendChild(poly);
                    // 公式标注
                    const label = document.createElementNS(NS, 'text');
                    label.setAttribute('x', pad + 4); label.setAttribute('y', 16);
                    label.setAttribute('fill', 'rgb(' + accentRGB() + ')'); label.setAttribute('font-size', '12');
                    label.textContent = 'y = ' + expr;
                    svg.appendChild(label);
                    return svg;
                } catch (e) {
                    console.warn('[BOARD] graph 解析失败:', spec, e);
                    return null;
                }
            }

            // 线段 SVG：{line:10,80-90,20}（坐标 0-100 归一化）
            function buildLineSVG(spec) {
                try {
                    const m = String(spec).match(/^([-\d.]+)\s*,\s*([-\d.]+)\s*-\s*([-\d.]+)\s*,\s*([-\d.]+)$/);
                    if (!m) return null;
                    const x1 = parseFloat(m[1]), y1 = parseFloat(m[2]);
                    const x2 = parseFloat(m[3]), y2 = parseFloat(m[4]);
                    const W = 400, H = 260, pad = 24;
                    const X = function(v) { return pad + (v / 100) * (W - 2 * pad); };
                    const Y = function(v) { return H - pad - (v / 100) * (H - 2 * pad); };
                    const NS = 'http://www.w3.org/2000/svg';
                    const svg = document.createElementNS(NS, 'svg');
                    svg.setAttribute('width', W);
                    svg.setAttribute('height', H);
                    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
                    const ln = document.createElementNS(NS, 'line');
                    ln.setAttribute('x1', X(x1)); ln.setAttribute('y1', Y(y1));
                    ln.setAttribute('x2', X(x2)); ln.setAttribute('y2', Y(y2));
                    ln.setAttribute('stroke', '#f0e6c8'); ln.setAttribute('stroke-width', '2.5');
                    svg.appendChild(ln);
                    [[x1, y1], [x2, y2]].forEach(function(pt) {
                        const c = document.createElementNS(NS, 'circle');
                        c.setAttribute('cx', X(pt[0])); c.setAttribute('cy', Y(pt[1]));
                        c.setAttribute('r', '3.5'); c.setAttribute('fill', 'rgb(' + accentRGB() + ')');
                        svg.appendChild(c);
                    });
                    return svg;
                } catch (e) {
                    console.warn('[BOARD] line 解析失败:', spec, e);
                    return null;
                }
            }

            // 上传板书
            boardUpload.addEventListener('change', function() {
                if (this.files && this.files[0]) {
                    if (!currentLesson) { alert('请先选择课程'); return; }
                    const formData = new FormData();
                    formData.append('board', this.files[0]);
                    fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/board', {
                        method: 'POST',
                        body: formData
                    }).then(r => r.json()).then(data => {
                        if (data.ok) {
                            alert('板书已上传！');
                            loadBoard();
                        } else {
                            alert('上传失败: ' + (data.message || '未知错误'));
                        }
                    }).catch(err => alert('上传失败: ' + err.message));
                }
                this.value = '';
            });

            // 删除板书
            boardDeleteBtn.addEventListener('click', function() {
                if (!currentLesson) { alert('请先选择课程'); return; }
                fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/board', { method: 'DELETE' })
                    .then(r => r.json()).then(data => {
                        if (data.ok) {
                            boardPreview.style.display = 'none';
                            boardEmpty.style.display = 'block';
                            alert('板书已删除');
                        }
                    }).catch(err => alert('删除失败: ' + err.message));
            });

            // ============================
            // 10. 设置功能（角色外观 / 背景 / 声线）
            // ============================

            // 同步滑块显示值 + 实时应用到 Live2D
            function syncPortraitUI() {
                portraitPosXVal.textContent = portraitPosX.value + '%';
                portraitPosYVal.textContent = portraitPosY.value + '%';
                portraitScaleVal.textContent = (parseInt(portraitScale.value) / 100).toFixed(2);
                live2dSettings.posX = parseInt(portraitPosX.value);
                live2dSettings.posY = parseInt(portraitPosY.value);
                live2dSettings.scale = parseInt(portraitScale.value);
                applyLive2DSettings();
            }

            portraitPosX.addEventListener('input', syncPortraitUI);
            portraitPosY.addEventListener('input', syncPortraitUI);
            portraitScale.addEventListener('input', syncPortraitUI);

            // 从后端加载配置并应用
            function loadTeacherSettings() {
                fetch('/api/config')
                    .then(r => r.json())
                    .then(config => {
                        // 声线（兼容 voice / tts_voice 字段）
                        const savedVoice = config.tts_voice || config.voice;
                        if (savedVoice && Array.from(voiceSelect.options).some(o => o.value === savedVoice)) {
                            voiceSelect.value = savedVoice;
                        }
                        // 位置（0-100）
                        live2dSettings.posX = clampNum(config.portrait_pos_x, 0, 100, 50);
                        live2dSettings.posY = clampNum(config.portrait_pos_y, 0, 100, 55);
                        // 缩放：仅接受合理 Live2D 范围 (0.1-0.8)，否则用默认 0.32
                        const sc = parseFloat(config.portrait_scale);
                        live2dSettings.scale = (sc >= 0.1 && sc <= 0.8) ? Math.round(sc * 100) : 32;
                        // 背景
                        live2dSettings.bgTheme = config.bg_theme || 'warm';
                        live2dSettings.bgUrl = config.bg_url || '';

                        // 情绪映射（Open-LLM-VTuber 协议）
                        if (config.emotion_map && typeof config.emotion_map === 'object') {
                            _emotionExpressionMap = Object.assign({}, config.emotion_map);
                            _userCustomizedEmotionMap = true;
                            renderEmotionMapGrid();
                        }

                        // 回复分段设置
                        if (config.segment_enabled !== undefined) {
                            segmentEnabled.checked = !!config.segment_enabled;
                            syncSegmentUI();
                        }
                        if (config.segment_marker) {
                            segmentMarker.value = config.segment_marker;
                            syncSegmentUI();
                        }
                        if (config.segment_max_lines) {
                            segmentMaxLines.value = config.segment_max_lines;
                            syncSegmentUI();
                        }

                        // 侧栏宽度（百分比 25-60）
                        if (config.sidebar_width !== undefined) {
                            applySidebarWidth(config.sidebar_width);
                        }

                        // 自定义 Live2D 模型（跟随系统配置；URL 变化时重载模型）
                        if (config.live2d_model_url) {
                            const wasDifferent = _loadedModelUrl && _loadedModelUrl !== config.live2d_model_url;
                            live2dSettings.modelUrl = config.live2d_model_url;
                            if (wasDifferent) reloadLive2DModel();
                        }

                        // 自定义动作（用户扩展的动作命令，如 /action 挥手；设置页最底部可编辑/调试）
                        if (config.custom_actions) {
                            mergeCustomActions(config.custom_actions);
                            customActionState = {};
                            for (const k in config.custom_actions) {
                                const v = config.custom_actions[k];
                                if (typeof v === 'string') {
                                    customActionState[k] = { id: v.trim(), intensity: 70 };
                                } else if (v && typeof v === 'object') {
                                    customActionState[k] = {
                                        id: (v.id || '').toString().trim(),
                                        intensity: (v.intensity == null ? 70 : Math.max(0, Math.min(100, Number(v.intensity) || 70)))
                                    };
                                }
                            }
                            renderCustomActionsList();
                        } else {
                            customActionState = {};
                            renderCustomActionsList();
                        }

                        // 填充 UI
                        portraitPosX.value = live2dSettings.posX;
                        portraitPosY.value = live2dSettings.posY;
                        portraitScale.value = live2dSettings.scale;
                        syncPortraitUI();
                        applyBgTheme(live2dSettings.bgTheme, live2dSettings.bgUrl);

                        // 模型与 API 设置
                        if (chatProvider) {
                            const cp = (config.chat_provider || 'auto').toLowerCase();
                            chatProvider.value = (cp === 'openai_compatible') ? 'cloud' : cp;
                            chatBaseUrl.value = config.chat_base_url || '';
                            chatApiKey.value = config.chat_api_key || '';
                            chatModel.value = config.chat_model || '';
                            ollamaBaseUrl.value = config.ollama_base_url || '';
                            ollamaModel.value = config.ollama_model || '';
                        }
                        if (lessonProvider) {
                            lessonProvider.value = (config.lesson_provider || 'cloud').toLowerCase();
                            cloudBaseUrl.value = config.cloud_base_url || '';
                            cloudApiKey.value = config.cloud_api_key || config.siliconflow_api_key || '';
                            cloudModel.value = config.cloud_model || config.siliconflow_model || '';
                            lessonSearch.checked = !!config.enable_search;
                        }
                        if (ttsProvider) {
                            ttsProvider.value = (config.tts_provider || 'cloud').toLowerCase();
                            ttsCloudBaseUrl.value = config.tts_cloud_base_url || '';
                            ttsCloudModel.value = config.tts_cloud_model || '';
                            ttsCloudVoice.value = config.tts_cloud_voice || '';
                            ttsBaseUrl.value = config.tts_base_url || '';
                        }
                        const ttsCloudEnabled = document.getElementById('tts-cloud-enabled');
                        if (ttsCloudEnabled) {
                            ttsCloudEnabled.checked = !!config.tts_cloud_enabled;
                            const val = document.getElementById('tts-cloud-enabled-val');
                            if (val) val.textContent = ttsCloudEnabled.checked ? '开' : '关';
                            ttsCloudEnabled.addEventListener('change', function() {
                                if (val) val.textContent = ttsCloudEnabled.checked ? '开' : '关';
                            });
                        }
                        if (voiceEnabled) {
                            voiceEnabled.checked = !!config.voice_enabled;
                        }
                        if (visionEnabled) {
                            visionEnabled.checked = !!config.vision_enabled;
                            visionBaseUrl.value = config.vision_base_url || '';
                            visionApiKey.value = config.vision_api_key || '';
                            visionModel.value = config.vision_model || '';
                        }
                        if (personalityPromptInput) personalityPromptInput.value = config.personality_prompt || '';
                        if (lessonPromptInput) lessonPromptInput.value = config.lesson_prompt || '';

                        // 字号 / 侧栏宽度初始化
                        const fontSizeInput = document.getElementById('font-size-input');
                        const fontSizeVal = document.getElementById('font-size-val');
                        const fs = parseInt(config.font_size) || 14;
                        if (fontSizeInput) fontSizeInput.value = fs;
                        if (fontSizeVal) fontSizeVal.textContent = fs + ' px';
                        document.documentElement.style.setProperty('--settings-font-size', fs + 'px');

                        const swInput = document.getElementById('sidebar-width-input');
                        const swVal = document.getElementById('sidebar-width-val');
                        const sw = parseInt(config.sidebar_width) || 36;
                        if (swInput) swInput.value = sw;
                        if (swVal) swVal.textContent = sw + '%';
                        document.documentElement.style.setProperty('--sidebar-width', sw + '%');
                        syncModelFields();
                    })
                    .catch(() => {});
            }

            // 背景预设按钮
            bgPresetBtns.forEach(btn => {
                btn.addEventListener('click', function() {
                    const theme = this.dataset.theme;
                    fetch('/api/set_background_theme', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ bg_theme: theme })
                    }).then(r => r.json()).then(data => {
                        if (data.ok) {
                            applyBgTheme(data.bg_theme, data.bg_url || '');
                        } else {
                            alert('背景切换失败: ' + (data.message || '未知错误'));
                        }
                    }).catch(err => alert('背景切换失败: ' + err.message));
                });
            });

            // 上传自定义背景
            bgUpload.addEventListener('change', function() {
                if (this.files && this.files[0]) {
                    const formData = new FormData();
                    formData.append('background', this.files[0]);
                    fetch('/api/upload_background', { method: 'POST', body: formData })
                        .then(r => r.json())
                        .then(data => {
                            if (data.ok) {
                                applyBgTheme('custom', data.bg_url || '');
                                alert('自定义背景已应用！');
                            } else {
                                alert('上传失败: ' + (data.message || '未知错误'));
                            }
                        })
                        .catch(err => alert('上传失败: ' + err.message));
                }
                this.value = '';
            });

            // 恢复默认背景
            bgResetBtn.addEventListener('click', function() {
                fetch('/api/reset_background', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        if (data.ok) {
                            applyBgTheme('warm', '');
                            alert('已恢复默认背景');
                        }
                    })
                    .catch(err => alert('恢复失败: ' + err.message));
            });

            // 自定义 Live2D 模型上传
            const modelUpload = document.getElementById('model-upload');
            const resetModelBtn = document.getElementById('reset-model-btn');
            const modelUploadStatus = document.getElementById('model-upload-status');
            if (modelUpload) {
                modelUpload.addEventListener('change', function() {
                    if (this.files && this.files[0]) {
                        const file = this.files[0];
                        const okExt = /\.(zip|json|moc3|moc)$/i.test(file.name);
                        if (!okExt) {
                            alert('仅支持 .zip（模型包）或 .model3.json / .moc3（Cubism 3/4/5）、model.json / .moc（Cubism 2.1）');
                            this.value = '';
                            return;
                        }
                        if (modelUploadStatus) modelUploadStatus.textContent = '上传中…';
                        const formData = new FormData();
                        formData.append('model', file);
                        fetch('/api/upload_model', { method: 'POST', body: formData })
                            .then(r => r.json())
                            .then(data => {
                                if (data.ok) {
                                    live2dSettings.modelUrl = data.url;
                                    if (modelUploadStatus) modelUploadStatus.textContent = '已应用，正在切换模型…';
                                    if (data.warning) alert(data.warning); else alert('自定义模型已上传并应用！');
                                    reloadLive2DModel();
                                } else {
                                    if (modelUploadStatus) modelUploadStatus.textContent = '';
                                    alert('上传失败: ' + (data.message || '未知错误'));
                                }
                            })
                            .catch(err => {
                                if (modelUploadStatus) modelUploadStatus.textContent = '';
                                alert('上传失败: ' + err.message);
                            });
                    }
                    this.value = '';
                });
            }
            // 恢复默认模型
            if (resetModelBtn) {
                resetModelBtn.addEventListener('click', function() {
                    fetch('/api/reset_model', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            if (data.ok) {
                                live2dSettings.modelUrl = data.url;
                                if (modelUploadStatus) modelUploadStatus.textContent = '已恢复默认模型';
                                alert('已恢复默认模型');
                                reloadLive2DModel();
                            } else {
                                alert('恢复失败: ' + (data.message || '未知错误'));
                            }
                        })
                        .catch(err => alert('恢复失败: ' + err.message));
                });
            }

            avatarUpload.addEventListener('change', function() {
                if (this.files && this.files[0]) {
                    const formData = new FormData();
                    formData.append('avatar', this.files[0]);
                    fetch('/api/upload_avatar', { method: 'POST', body: formData })
                        .then(r => r.json())
                        .then(data => { if (data.url) alert('头像更新成功！'); })
                        .catch(err => alert('上传失败: ' + err.message));
                }
            });

            resetAvatarBtn.addEventListener('click', function() {
                fetch('/api/reset_avatar', { method: 'POST' })
                    .then(r => r.json())
                    .then(() => alert('已恢复默认头像'))
                    .catch(err => alert('重置失败: ' + err.message));
            });

            // ---- 回复分段 UI 同步 ----
            function syncSegmentUI() {
                if (!segmentEnabled || !segmentEnabledVal) return;
                segmentEnabledVal.textContent = segmentEnabled.checked ? '开' : '关';
                if (segmentMarkerVal) {
                    segmentMarkerVal.textContent = (segmentMarker.value || '\\c').length > 10
                        ? (segmentMarker.value || '\\c').slice(0, 10) + '…' : (segmentMarker.value || '\\c');
                }
                if (segmentMaxLinesVal) {
                    segmentMaxLinesVal.textContent = segmentMaxLines.value + ' 行';
                }
            }
            if (segmentEnabled) segmentEnabled.addEventListener('change', syncSegmentUI);
            if (segmentMarker) segmentMarker.addEventListener('input', syncSegmentUI);
            if (segmentMaxLines) segmentMaxLines.addEventListener('input', syncSegmentUI);

            // 侧栏拖拽手柄（拖动 → 实时改宽度 → 鼠标松开自动保存到后端，跟随系统配置）
            (function setupSidebarResizer() {
                if (!sidebarResizer) return;
                let dragging = false;
                let startX = 0, startPct = 0;
                let saveTimer = null;

                function onPointerDown(e) {
                    dragging = true;
                    startX = e.clientX;
                    // 从当前 CSS 变量读取百分比
                    const cur = document.documentElement.style.getPropertyValue('--sidebar-width') || SIDEBAR_WIDTH_DEFAULT + '%';
                    startPct = parseFloat(cur) || SIDEBAR_WIDTH_DEFAULT;
                    sidebarResizer.classList.add('dragging');
                    document.body.classList.add('resizing');
                    e.preventDefault();
                }
                function onPointerMove(e) {
                    if (!dragging) return;
                    const totalWidth = chatSidebar.parentElement.clientWidth;
                    if (totalWidth <= 0) return;
                    // 注意：拖左边手柄 → 向左拖 = 侧栏变宽；向右拖 = 侧栏变窄
                    const deltaPx = startX - e.clientX;
                    const deltaPct = (deltaPx / totalWidth) * 100;
                    let newPct = startPct + deltaPct;
                    newPct = Math.max(SIDEBAR_WIDTH_MIN, Math.min(SIDEBAR_WIDTH_MAX, newPct));
                    applySidebarWidth(Math.round(newPct));
                }
                function onPointerUp() {
                    if (!dragging) return;
                    dragging = false;
                    sidebarResizer.classList.remove('dragging');
                    document.body.classList.remove('resizing');
                    // 拖拽结束后延迟 300ms 自动保存到后端（避免拖拽过程频繁请求）
                    clearTimeout(saveTimer);
                    saveTimer = setTimeout(function() {
                        fetch('/api/config', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ sidebar_width: currentSidebarWidth })
                        }).catch(() => {});
                    }, 300);
                }
                sidebarResizer.addEventListener('mousedown', onPointerDown);
                document.addEventListener('mousemove', onPointerMove);
                document.addEventListener('mouseup', onPointerUp);
                // 触摸支持
                sidebarResizer.addEventListener('touchstart', function(e) {
                    if (e.touches[0]) onPointerDown(e.touches[0]);
                }, { passive: false });
                document.addEventListener('touchmove', function(e) {
                    if (dragging && e.touches[0]) {
                        onPointerMove(e.touches[0]);
                        e.preventDefault();
                    }
                }, { passive: false });
                document.addEventListener('touchend', onPointerUp);
            })();

            function _buildSettingsPayload() {
                // 情绪映射（从 UI 网格收集）
                const emoMap = {};
                document.querySelectorAll('#emotion-map-grid .emotion-row').forEach(function(row) {
                    const emo = row.dataset.emotion;
                    const sel = row.querySelector('select');
                    if (sel && sel.value !== '') {
                        emoMap[emo] = parseInt(sel.value);
                    }
                });
                // 自定义动作（从设置页底部编辑器收集）
                const customActs = {};
                for (const k in customActionState) {
                    const it = customActionState[k] || {};
                    const name = (k || '').trim();
                    const id = (it.id || '').trim();
                    if (name && id) {
                        customActs[name] = {
                            id: id,
                            intensity: (it.intensity == null ? 70 : Math.max(0, Math.min(100, Number(it.intensity) || 70)))
                        };
                    }
                }
                const payload = {
                                    portrait_pos_x: parseInt(portraitPosX.value),
                                    portrait_pos_y: parseInt(portraitPosY.value),
                                    portrait_scale: parseInt(portraitScale.value) / 100,
                                    tts_voice: voiceSelect.value,
                                    bg_theme: live2dSettings.bgTheme,
                                    bg_url: live2dSettings.bgUrl,
                                    emotion_map: emoMap,
                                    segment_enabled: segmentEnabled.checked,
                                    segment_marker: segmentMarker.value.trim() || '\\c',
                                    segment_max_lines: parseInt(segmentMaxLines.value) || 6,
                                    sidebar_width: currentSidebarWidth,
                                    custom_actions: customActs,
                                    // 模型与 API 设置
                                    chat_provider: chatProvider.value,
                                    chat_base_url: chatBaseUrl.value.trim(),
                                    chat_api_key: chatApiKey.value.trim(),
                                    chat_model: chatModel.value.trim(),
                                    ollama_base_url: ollamaBaseUrl.value.trim() || 'http://127.0.0.1:11434',
                                    ollama_model: ollamaModel.value.trim() || 'qwen2.5:7b',
                                    lesson_provider: lessonProvider.value,
                                    cloud_base_url: cloudBaseUrl.value.trim(),
                                    cloud_api_key: cloudApiKey.value.trim(),
                                    cloud_model: cloudModel.value.trim(),
                                    enable_search: lessonSearch.checked,
                                    tts_provider: ttsProvider.value,
                                    tts_cloud_base_url: ttsCloudBaseUrl.value.trim(),
                                    tts_cloud_model: ttsCloudModel.value.trim(),
                                    tts_cloud_voice: ttsCloudVoice.value.trim(),
                                    tts_base_url: ttsBaseUrl.value.trim(),
                                    voice_enabled: voiceEnabled ? voiceEnabled.checked : true,
                                    tts_cloud_enabled: (function() {
                                        const el = document.getElementById('tts-cloud-enabled');
                                        return el ? el.checked : false;
                                    })(),
                                    vision_enabled: visionEnabled.checked,
                                    vision_base_url: visionBaseUrl.value.trim(),
                                    vision_api_key: visionApiKey.value.trim(),
                                    vision_model: visionModel.value.trim(),
                                    personality_prompt: personalityPromptInput.value.trim(),
                                    lesson_prompt: lessonPromptInput.value.trim(),
                                    font_size: (function() {
                                        const el = document.getElementById('font-size-input');
                                        return el ? parseInt(el.value) || 14 : 14;
                                    })(),
                                    sidebar_width: (function() {
                                        const el = document.getElementById('sidebar-width-input');
                                        return el ? parseInt(el.value) || 36 : 36;
                                    })(),
                                };
                return payload;
            }

            // 静默保存（用于自动保存），不弹 alert；按钮保存传 false 会弹 alert。
            function _saveSettingsNow(showAlert) {
                if (showAlert == null) showAlert = true;
                const payload = _buildSettingsPayload();
                const emoMap = payload.emotion_map;
                const customActs = payload.custom_actions;
                return fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                }).then(function(resp) {
                    return resp.json().then(function(data) {
                        // 后端校验失败返回 400：展示中文错误，不落盘
                        if (!resp.ok) {
                            const msgs = (data && data.errors) || ['保存失败'];
                            const joined = msgs.join('；');
                            _setSettingsSaveStatus('保存失败：' + joined, 'err');
                            if (showAlert) alert('设置未保存：' + joined);
                            throw new Error(joined);
                        }
                        return data;
                    });
                }).then(() => {
                        if (emoMap && Object.keys(emoMap).length) {
                            _emotionExpressionMap = emoMap;
                            _userCustomizedEmotionMap = true;
                        }
                        mergeCustomActions(customActs);
                        if (showAlert) alert('设置已保存！');
                        _setSettingsSaveStatus('已保存 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false }), 'ok');
                    })
                    .catch(err => {
                        // 校验失败的错误已在上面提示过，这里不重复弹窗；网络错误才提示
                        if (err && err.message && err.message.indexOf('设置未保存：') !== 0) {
                            console.error('[settings] auto save failed', err);
                            _setSettingsSaveStatus('保存失败', 'err');
                            if (showAlert) alert('保存失败: ' + err.message);
                        }
                    });
            }

            // 防抖自动保存：input/change 触发，500ms 内合并多次修改
            let _autoSaveTimer = null;
            function _autoSaveSettings() {
                clearTimeout(_autoSaveTimer);
                _setSettingsSaveStatus('编辑中…', 'pending');
                _autoSaveTimer = setTimeout(function() {
                    _saveSettingsNow(false);
                }, 500);
            }

            function _setSettingsSaveStatus(text, kind) {
                const el = document.getElementById('settings-save-status');
                if (!el) return;
                el.textContent = text;
                el.dataset.kind = kind || 'pending';
            }

            // 给 settings 视图内所有表单元素挂 input/change 自动保存（不上传按钮/文件）
            (function bindAutoSave() {
                const view = document.getElementById('view-settings');
                if (!view) return;
                const selector = 'input:not([type=file]):not([type=button]):not([type=submit]), select, textarea';
                view.querySelectorAll(selector).forEach(function(el) {
                    // 跳过滑块类的实时预览元素（portrait_*），它们有自己的实时应用 handler
                    if (el.id && /^portrait-(pos|scale)/.test(el.id)) return;
                    const evt = (el.type === 'checkbox' || el.tagName === 'SELECT') ? 'change' : 'input';
                    el.addEventListener(evt, _autoSaveSettings);
                    // textarea / text input 也监听 change 兜底（粘贴后失焦）
                    if (evt === 'input') el.addEventListener('change', _autoSaveSettings);
                });
            })();

if (saveSettingsBtn) saveSettingsBtn.addEventListener('click', function() {
                _saveSettingsNow(false);
            });

            // ---- 模型连接测试 ----
            function testModelConnection(kind, btn, statusEl) {
                if (!btn || !statusEl) return;
                const cfg = {
                    ollama_base_url: (ollamaBaseUrl ? ollamaBaseUrl.value.trim() : '') || 'http://127.0.0.1:11434',
                    ollama_model: (ollamaModel ? ollamaModel.value.trim() : '') || 'qwen2.5:7b',
                    tts_provider: ttsProvider ? ttsProvider.value : 'cloud',
                    tts_cloud_base_url: (ttsCloudBaseUrl ? ttsCloudBaseUrl.value.trim() : '') || 'https://api.siliconflow.cn/v1',
                    tts_cloud_model: (ttsCloudModel ? ttsCloudModel.value.trim() : '') || 'FunAudioLLM/CosyVoice2-0.5B',
                    tts_cloud_voice: ttsCloudVoice ? ttsCloudVoice.value.trim() : 'anna',
                    tts_base_url: (ttsBaseUrl ? ttsBaseUrl.value.trim() : '') || 'http://127.0.0.1:8000',
                    tts_cloud_response_format: 'mp3',
                    cloud_api_key: (cloudApiKey ? cloudApiKey.value.trim() : '') || (chatApiKey ? chatApiKey.value.trim() : ''),
                };
                statusEl.textContent = '测试中...';
                statusEl.style.color = 'var(--text-muted)';
                fetch('/api/config/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind: kind, config: cfg })
                }).then(r => r.json()).then(data => {
                    statusEl.textContent = data.message || (data.ok ? '连接成功' : '连接失败');
                    statusEl.style.color = data.ok ? 'var(--gold)' : '#e07b7b';
                }).catch(err => {
                    statusEl.textContent = '测试失败: ' + err.message;
                    statusEl.style.color = '#e07b7b';
                });
            }
            if (chatTestBtn) chatTestBtn.addEventListener('click', function() {
                testModelConnection('ollama', chatTestBtn, chatTestStatus);
            });
            if (ttsTestBtn) ttsTestBtn.addEventListener('click', function() {
                testModelConnection('tts', ttsTestBtn, ttsTestStatus);
            });

            // ---- 情绪映射 UI ----
            const _EMOTION_LABELS = {
                neutral: '中性', happy: '开心 (joy)', sad: '难过 (sadness)',
                angry: '生气 (anger)', surprised: '惊讶 (surprise)',
                fear: '害怕 (fear)', disgust: '厌恶 (disgust)',
                smirk: '俏皮 (smirk)', think: '思考 (think)'
            };
            function renderEmotionMapGrid() {
                const grid = document.getElementById('emotion-map-grid');
                if (!grid) return;
                // 如果模型信息还没加载完，先建一个默认模板，加载完成后重新填充
                const exps = _live2dExpressions.length ? _live2dExpressions : [
                    { index: 0, name: 'expression1' }, { index: 1, name: 'expression2' },
                    { index: 2, name: 'expression3' }, { index: 3, name: 'expression4' },
                    { index: 4, name: 'expression5' }, { index: 5, name: 'expression6' },
                    { index: 6, name: 'expression7' },
                ];
                const emoKeys = Object.keys(_EMOTION_LABELS);
                grid.innerHTML = emoKeys.map(emo => {
                    const defaultIdx = _emotionExpressionMap[emo];
                    const opts = ['<option value="">— 默认 —</option>']
                        .concat(exps.map(e => {
                            const selected = (e.index === defaultIdx) ? 'selected' : '';
                            return `<option value="${e.index}" ${selected}>${e.index} · ${e.name}</option>`;
                        })).join('');
                    return `
                        <div class="emotion-row" data-emotion="${emo}" style="display:flex; align-items:center; gap:8px;">
                            <label style="font-size:13px; min-width:90px; color:var(--text-secondary);">${_EMOTION_LABELS[emo]}</label>
                            <select data-emo="${emo}" style="flex:1; padding:4px 8px; background:rgba(0,0,0,0.3); border:1px solid rgba(var(--accent-rgb),0.25); border-radius:6px; color:var(--text-primary); font-size:13px;">${opts}</select>
                        </div>
                    `;
                }).join('');
            }
            renderEmotionMapGrid();
            // 模型信息加载完后重新填充（让下拉显示真实表情数）
            const _origLoadInfo = loadLive2DModelInfo;
            loadLive2DModelInfo = function() {
                _origLoadInfo();
                // 100ms 后再渲染网格（确保 fetch 完成）
                setTimeout(renderEmotionMapGrid, 300);
            };
            const mapResetBtn = document.getElementById('emotion-map-reset-btn');
            if (mapResetBtn) {
                mapResetBtn.addEventListener('click', function() {
                    if (_live2dDefaultEmotionMap) {
                        _emotionExpressionMap = Object.assign({}, _live2dDefaultEmotionMap);
                    }
                    _userCustomizedEmotionMap = false;
                    renderEmotionMapGrid();
                });
            }
            const mapTestBtn = document.getElementById('emotion-map-test-btn');
            if (mapTestBtn) {
                mapTestBtn.addEventListener('click', function() {
                    const emos = ['happy', 'sad', 'angry', 'surprised', 'think', 'neutral'];
                    let i = 0;
                    function next() {
                        if (i >= emos.length) return;
                        setEmotion(emos[i], 1500);
                        console.log('[emotion-map test]', emos[i]);
                        i++;
                        setTimeout(next, 1700);
                    }
                    next();
                });
            }

            // ============================
            // 10. 备课预览窗口
            // ============================
            // 预览状态变量 + DOM 引用均已在文件顶部声明（避免 TDZ）

            function showPreviewOverlay() {
                if (previewOverlay) {
                    if (menuScreen) menuScreen.style.display = 'none';
                    if (previewOverlay.parentElement !== document.body) {
                        document.body.appendChild(previewOverlay);
                    }
                    previewOverlay.classList.add('active');
                }
            }
            function hidePreviewOverlay() {
                if (previewOverlay) {
                    previewOverlay.classList.remove('active');
                    // 清除内联样式，恢复 CSS 类的控制
                    var s = previewOverlay.style;
                    s.position = ''; s.top = ''; s.left = ''; s.right = ''; s.bottom = '';
                    s.width = ''; s.height = ''; s.zIndex = ''; s.display = '';
                    s.alignItems = ''; s.justifyContent = ''; s.background = ''; s.opacity = ''; s.visibility = '';
                }
                _previewPlan = null;
                _previewOriginalPlan = null;
                _previewLessonFolder = null;
                _previewTopic = null;
                // 恢复 menu-screen
                if (menuScreen) menuScreen.style.display = '';
            }

            // 渲染预览内容
            function renderPreview(plan) {
                try {
                    _previewPlan = JSON.parse(JSON.stringify(plan));
                    // 首次渲染时保存原始 plan，作为后续"重新生成"对比删除课时的基线
                    if (!_previewOriginalPlan) _previewOriginalPlan = JSON.parse(JSON.stringify(plan));
                    if (previewTitle) previewTitle.textContent = plan.topic || '课程预览';
                    const units = plan.units || [];
                    if (previewMeta) previewMeta.textContent = '共 ' + units.length + ' 课';

                    let html = '';
                    // 全局核心概念
                    const kps = plan.key_points || [];
                    if (kps.length) {
                        html += '<div class="preview-section">'
                            + '<div class="preview-section-label">全局核心概念</div>'
                            + '<div class="preview-kp-tags" id="preview-kp-tags">'
                            + kps.map(function(kp, i) {
                                return '<span class="preview-kp-tag" contenteditable="true" data-kp-idx="' + i + '" '
                                    + 'onkeydown="if(event.key===\'Enter\'){event.preventDefault();this.blur();}">'
                                    + _escHtml(kp) + '</span>';
                            }).join('')
                            + '<span class="preview-kp-tag" style="opacity:0.4;cursor:pointer;" id="preview-add-kp">+ 新增</span>'
                            + '</div></div>';
                    }

                    // 课时列表
                    html += '<div class="preview-section">'
                        + '<div class="preview-section-label">课时安排（点击标题/摘要可直接编辑）</div>'
                        + '<div class="preview-units-list" id="preview-units-list">'
                        + units.map(function(unit, i) {
                            return _renderUnitCard(unit, i);
                        }).join('')
                        + '</div>'
                        + '<div class="preview-add-unit" id="preview-add-unit">➕ 新增课时</div>'
                        + '</div>';

                    if (previewBody) previewBody.innerHTML = html;

                    // 绑定事件
                    _bindPreviewEvents();
                } catch (err) {
                    console.error('[preview] renderPreview error:', err);
                }
            }

            function _renderUnitCard(unit, idx) {
                const kps = unit.key_points || [];
                return '<div class="preview-unit-card" data-unit-idx="' + idx + '">'
                    + '<div class="preview-unit-header">'
                    + '<span class="preview-unit-num">' + (idx + 1) + '</span>'
                    + '<input class="preview-unit-title-input" data-unit-idx="' + idx + '" value="' + _escHtml(unit.title || '') + '" placeholder="课时标题…" />'
                    + '<button class="preview-unit-del" data-unit-idx="' + idx + '" title="删除本课">✕</button>'
                    + '</div>'
                    + '<textarea class="preview-unit-summary-input" data-unit-idx="' + idx + '" rows="2" placeholder="本课要点概述（1-2句）…">' + _escHtml(unit.summary || '') + '</textarea>'
                    + '<div class="preview-unit-kps" data-unit-idx="' + idx + '">'
                    + kps.map(function(kp, ki) {
                        return '<span class="preview-kp-chip" contenteditable="true" data-unit-idx="' + idx + '" data-kp-idx="' + ki + '" '
                            + 'onkeydown="if(event.key===\'Enter\'){event.preventDefault();this.blur();}">'
                            + _escHtml(kp) + '</span>';
                    }).join('')
                    + '<input class="preview-unit-kp-input" data-unit-idx="' + idx + '" placeholder="+ 要点（回车添加）" />'
                    + '</div>'
                    + '</div>';
            }

            function _bindPreviewEvents() {
                // 全局要点：点击"新增"
                var addKpBtn = document.getElementById('preview-add-kp');
                if (addKpBtn) addKpBtn.addEventListener('click', function() {
                    var tags = document.getElementById('preview-kp-tags');
                    var inp = document.createElement('span');
                    inp.className = 'preview-kp-tag';
                    inp.contentEditable = 'true';
                    inp.dataset.kpIdx = _previewPlan.key_points.length;
                    inp.setAttribute('tabindex', '-1');
                    if (tags) tags.insertBefore(inp, addKpBtn);
                    inp.focus();
                    _previewPlan.key_points.push('新概念');
                    _bindKpBlur(inp, 'global');
                });

                // 全局要点失焦：同步到 _previewPlan
                document.querySelectorAll('#preview-kp-tags .preview-kp-tag[data-kp-idx]').forEach(function(el) {
                    _bindKpBlur(el, 'global');
                });

                // 课时标题编辑
                document.querySelectorAll('.preview-unit-title-input').forEach(function(inp) {
                    inp.addEventListener('input', function() {
                        var idx = parseInt(inp.dataset.unitIdx);
                        if (_previewPlan.units[idx]) _previewPlan.units[idx].title = inp.value;
                    });
                });

                // 课时摘要编辑
                document.querySelectorAll('.preview-unit-summary-input').forEach(function(ta) {
                    ta.addEventListener('input', function() {
                        var idx = parseInt(ta.dataset.unitIdx);
                        if (_previewPlan.units[idx]) _previewPlan.units[idx].summary = ta.value;
                    });
                });

                // 删除课时
                document.querySelectorAll('.preview-unit-del').forEach(function(btn) {
                    btn.addEventListener('click', function() {
                        var idx = parseInt(btn.dataset.unitIdx);
                        _previewPlan.units.splice(idx, 1);
                        renderPreview(_previewPlan); // 重新渲染
                    });
                });

                // 课时要点失焦
                document.querySelectorAll('.preview-unit-kps .preview-kp-chip').forEach(function(el) {
                    _bindKpBlur(el, 'unit');
                });

                // 课时要点输入（回车添加）
                document.querySelectorAll('.preview-unit-kp-input').forEach(function(inp) {
                    inp.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            var val = inp.value.trim();
                            if (!val) return;
                            var idx = parseInt(inp.dataset.unitIdx);
                            if (!_previewPlan.units[idx].key_points) _previewPlan.units[idx].key_points = [];
                            _previewPlan.units[idx].key_points.push(val);
                            inp.value = '';
                            // 在 input 前插入新 chip
                            var chip = document.createElement('span');
                            chip.className = 'preview-kp-chip';
                            chip.contentEditable = 'true';
                            chip.dataset.unitIdx = idx;
                            chip.dataset.kpIdx = _previewPlan.units[idx].key_points.length - 1;
                            chip.textContent = val;
                            inp.parentNode.insertBefore(chip, inp);
                            _bindKpBlur(chip, 'unit');
                        }
                    });
                });

                // 新增课时
                var addUnitBtn = document.getElementById('preview-add-unit');
                if (addUnitBtn) addUnitBtn.addEventListener('click', function() {
                    _previewPlan.units.push({
                        title: '新课时',
                        summary: '',
                        key_points: [],
                        source_files: [],
                    });
                    renderPreview(_previewPlan);
                    // 滚动到底部
                    if (previewBody) { previewBody.scrollTop = previewBody.scrollHeight; }
                });
            }

            function _bindKpBlur(el, kind) {
                el.addEventListener('blur', function() {
                    var text = el.textContent.trim();
                    if (!text) {
                        if (kind === 'global') {
                            var idx = parseInt(el.dataset.kpIdx);
                            if (_previewPlan.key_points[idx] !== undefined) {
                                _previewPlan.key_points.splice(idx, 1);
                            }
                        } else {
                            var uid = parseInt(el.dataset.unitIdx);
                            var kid = parseInt(el.dataset.kpIdx);
                            if (_previewPlan.units[uid] && _previewPlan.units[uid].key_points[kid] !== undefined) {
                                _previewPlan.units[uid].key_points.splice(kid, 1);
                            }
                        }
                        el.remove();
                    } else {
                        if (kind === 'global') {
                            var idx2 = parseInt(el.dataset.kpIdx);
                            _previewPlan.key_points[idx2] = text;
                        } else {
                            var uid2 = parseInt(el.dataset.unitIdx);
                            var kid2 = parseInt(el.dataset.kpIdx);
                            if (_previewPlan.units[uid2]) {
                                _previewPlan.units[uid2].key_points[kid2] = text;
                            }
                        }
                    }
                });
            }

            function _escHtml(s) {
                return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
            }

            // 关闭按钮
            if (previewCloseBtn) previewCloseBtn.addEventListener('click', hidePreviewOverlay);
            if (previewCancelBtn) previewCancelBtn.addEventListener('click', hidePreviewOverlay);
            if (previewOverlay) previewOverlay.addEventListener('click', function(e) {
                if (e.target === previewOverlay) hidePreviewOverlay();
            });

            // 重新生成（把用户编辑后的 plan 和原始 plan 一起发给后端，让 AI 保留编辑意图）
            if (previewRegenBtn) previewRegenBtn.addEventListener('click', function() {
                if (previewBody) previewBody.innerHTML = '<div class="preview-loading"><div class="spin"></div>AI 正在根据你的修改重新备课…</div>';
                if (previewRegenBtn) previewRegenBtn.disabled = true;
                fetch('/api/regenerate_lesson', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        plan: _previewPlan,
                        original_plan: _previewOriginalPlan
                    })
                }).then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (previewRegenBtn) previewRegenBtn.disabled = false;
                        if (data.error) { alert(data.error); return; }
                        _previewLessonFolder = data.lesson_folder;
                        _previewOriginalPlan = null; // 新基线：以重新生成结果为新的"原始"
                        renderPreview(data.plan);
                    })
                    .catch(function(err) {
                        if (previewRegenBtn) previewRegenBtn.disabled = false;
                        alert('重新备课失败: ' + err.message);
                    });
            });

            // 确认创建
            if (previewConfirmBtn) previewConfirmBtn.addEventListener('click', function() {
                if (previewConfirmBtn) previewConfirmBtn.disabled = true;
                if (previewConfirmBtn) previewConfirmBtn.textContent = '正在保存…';
                fetch('/api/apply_lesson', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ plan: _previewPlan, topic: (_previewPlan && _previewPlan.topic) || (_previewTopic || '') })
                }).then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (previewConfirmBtn) previewConfirmBtn.disabled = false;
                        if (previewConfirmBtn) previewConfirmBtn.textContent = '✓ 确认创建';
                        if (data.error) { alert(data.error); return; }
                        hidePreviewOverlay();
                        enterLesson(data.lesson_folder);
                    })
                    .catch(function(err) {
                        if (previewConfirmBtn) previewConfirmBtn.disabled = false;
                        if (previewConfirmBtn) previewConfirmBtn.textContent = '✓ 确认创建';
                        alert('保存失败: ' + err.message);
                    });
            });

            // ============================
            // 11. 初始化
            // ============================
            syncSegmentUI();   // 先按 DOM 默认值渲染一次
            loadTeacherSettings();

            // ===== 设置面板顶部分类 tab 切换 =====
            (function initSettingsTabs() {
                const tabsBar = document.getElementById('settings-tabs');
                if (!tabsBar) return;
                tabsBar.addEventListener('click', function(e) {
                    const tab = e.target.closest('.settings-tab');
                    if (!tab) return;
                    const target = tab.dataset.tab;
                    if (!target) return;
                    // 切换 tab 高亮
                    tabsBar.querySelectorAll('.settings-tab').forEach(function(b) {
                        b.classList.toggle('active', b === tab);
                    });
                    // 切换 section 显示
                    document.querySelectorAll('#view-settings .settings-section').forEach(function(s) {
                        s.classList.toggle('active', s.dataset.section === target);
                    });
                });
            })();

            // ===== 应用正文字号 + 侧栏宽度（实时 + 持久化） =====
            (function initDisplayControls() {
                const fontSizeInput = document.getElementById('font-size-input');
                const fontSizeVal = document.getElementById('font-size-val');
                if (fontSizeInput && fontSizeVal) {
                    const apply = function() {
                        const px = parseInt(fontSizeInput.value) || 14;
                        fontSizeVal.textContent = px + ' px';
                        document.documentElement.style.setProperty('--settings-font-size', px + 'px');
                    };
                    fontSizeInput.addEventListener('input', apply);
                    fontSizeInput.addEventListener('change', apply);
                    apply();
                }
                const swInput = document.getElementById('sidebar-width-input');
                const swVal = document.getElementById('sidebar-width-val');
                if (swInput && swVal) {
                    const apply = function() {
                        const v = parseInt(swInput.value) || 36;
                        swVal.textContent = v + '%';
                        document.documentElement.style.setProperty('--sidebar-width', v + '%');
                    };
                    swInput.addEventListener('input', apply);
                    swInput.addEventListener('change', apply);
                    apply();
                }
            })();

            showMenu();   // 启动时显示首页菜单
