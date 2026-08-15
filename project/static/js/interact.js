'use strict';

            // ============================
            // 3. 视图切换（边缘吸附抽屉 + 角色推拉联动）
            // ============================

            // 推（打开）—— 手臂向前伸展，把窗口"递出来"
            const VIEW_PUSH_MOTION = {
                exam:     'speak',   // 测验卷：一边讲解一边递出
                resource: 'think',  // 资源：思考/翻找后递出
                lesson:   'hello',  // 课程列表：打招呼后展示
                settings: 'listen', // 设置：倾听用户偏好
            };
            // 拉（关闭）—— 手臂收回，把窗口"拉回去"
            const VIEW_PULL_MOTION = {
                exam:     'listen',
                resource: 'think',
                lesson:   'think',
                settings: 'listen',
            };

            // 缓动函数：先快后慢
            function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

            // 模型位移动画：从当前位置平滑过渡到目标（x, y, scale）
            function tweenModelTo(targetX, targetY, targetScale, duration) {
                if (!live2dModel) return Promise.resolve();
                const sx = live2dModel.x;
                const sy = live2dModel.y;
                const ss = live2dModel.scale.x;
                const dx = targetX - sx, dy = targetY - sy, ds = targetScale - ss;
                return new Promise(function(resolve) {
                    const start = performance.now();
                    const step = function(now) {
                        const t = Math.min(1, (now - start) / duration);
                        const e = easeOutCubic(t);
                        live2dModel.x = sx + dx * e;
                        live2dModel.y = sy + dy * e;
                        live2dModel.scale.set(ss + ds * e);
                        if (t < 1) {
                            requestAnimationFrame(step);
                        } else {
                            resolve();
                        }
                    };
                    requestAnimationFrame(step);
                });
            }

            // 角色"推"出浮层窗口（缓慢浮现，与动作同步）
            // ====================================================
            // Sidebar Tab 切换 v2 — 不再使用浮层动画，直接 tab 切换
            // ====================================================
            function panelHandReveal(viewName) {
                const el = viewContents[viewName];
                if (!el) return;

                // 1) 隐藏其他 pane
                Object.keys(viewContents).forEach(function(k) {
                    if (k !== viewName && viewContents[k].classList.contains('active')) {
                        viewContents[k].classList.remove('active', 'view-closing');
                    }
                });

                // 2) 同步顶部 Tab 高亮
                navBtns.forEach(function(btn) { btn.classList.toggle('active', btn.dataset.view === viewName); });

                // 3) 激活目标 pane
                el.classList.remove('view-closing');
                el.classList.add('active');

                // 4) 角色做动作（保留原有的"角色联动"体验）
                const motion = (typeof VIEW_PUSH_MOTION !== 'undefined' && VIEW_PUSH_MOTION[viewName]) || 'hello';
                if (typeof triggerAction === 'function') {
                    try { triggerAction(motion); } catch (e) { console.warn('triggerAction 失败:', e); }
                }

                // 5) 触发数据加载
                if (viewName === 'lesson')   { if (typeof loadLessons === 'function') loadLessons(); if (typeof loadBoard === 'function') loadBoard(); }
                if (viewName === 'resource') if (typeof loadResources === 'function') loadResources();
            }

            function panelHandHide(viewName) {
                const el = viewContents[viewName];
                if (!el) return;
                el.classList.remove('active', 'view-closing');
                navBtns.forEach(function(b) { if (b.dataset.view === viewName) b.classList.remove('active'); });
                // 不再播放"收回"动作（tab 切换是即时切换，不需要动画）
            }

            function switchView(viewName) {
                if (viewName === 'chat') {
                    // 切回对话主视图
                    Object.keys(viewContents).forEach(function(k) {
                        viewContents[k].classList.remove('active', 'view-closing');
                    });
                    viewContents.chat.classList.add('active');
                    navBtns.forEach(function(btn) { btn.classList.toggle('active', btn.dataset.view === 'chat'); });
                    return;
                }
                panelHandReveal(viewName);
            }

            // 底部导航按钮：单击 → 角色推出浮层；再点同一按钮 → 角色拉回
            navBtns.forEach(btn => {
                btn.addEventListener('click', function() {
                    // 若欢迎页仍显示，点击导航时自动收起（避免覆盖层拦截）
                    if (!menuScreen.classList.contains('hidden')) hideMenu();
                    const v = this.dataset.view;
                    if (v === 'chat') {
                        switchView('chat');
                        return;
                    }
                    const el = viewContents[v];
                    if (el && el.classList.contains('active')) {
                        panelHandHide(v);
                    } else {
                        panelHandReveal(v);
                    }
                });
            });

            // 顶部「🏠」返回首页按钮：独立绑定（不在 .nav-float 里，navBtns 遍历不到）
            const topbarHomeBtn = document.getElementById('nav-home-btn');
            if (topbarHomeBtn) {
                topbarHomeBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    showMenu();
                });
            }

            // 面板内的 ✕ 关闭按钮：也是角色拉回
            document.querySelectorAll('.view-close').forEach(btn => {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    const v = this.dataset.view;
                    if (v === 'chat') return;
                    panelHandHide(v);
                });
            });

            // 点击面板外部 → 切回对话 pane（避免点空白区域导致面板意外关闭，改为不响应）
            document.addEventListener('click', function(e) {
                if (e.target.closest('.view-content')) return;  // 点在面板内
                if (e.target.closest('.nav-btn')) return;       // 点在导航上
                if (e.target.closest('.topbar-tab')) return;    // 点在顶部 tab 上
                if (e.target.closest('.topbar-home-btn')) return;
                // 不再自动关闭面板（侧边栏面板不会被外部点击干扰）
            }, true);

            // 暴露给控制台调试
            window.panelHandReveal = panelHandReveal;
            window.panelHandHide   = panelHandHide;
            window.switchView      = switchView;

            // ============================
            // 3.5 首页菜单逻辑
            // ============================

            // 自定义弹窗：替代 window.prompt（部分预览环境不支持原生 prompt）
            // 自定义确认对话框（无输入框）
            function customConfirm(title, message, okText, cancelText) {
                return new Promise(function(resolve) {
                    const overlay = document.createElement('div');
                    overlay.className = 'custom-modal-overlay';
                    overlay.innerHTML =
                        '<div class="custom-modal">' +
                            '<div class="custom-modal-title"></div>' +
                            '<div class="custom-modal-message"></div>' +
                            '<div class="custom-modal-actions">' +
                                '<button type="button" class="custom-modal-btn cancel"></button>' +
                                '<button type="button" class="custom-modal-btn confirm primary"></button>' +
                            '</div>' +
                        '</div>';
                    overlay.querySelector('.custom-modal-title').textContent = title || '确认';
                    overlay.querySelector('.custom-modal-message').textContent = message || '';
                    overlay.querySelector('.cancel').textContent = (cancelText || '取消');
                    overlay.querySelector('.confirm').textContent = (okText || '确定');
                    document.body.appendChild(overlay);
                    setTimeout(function() { overlay.querySelector('.confirm').focus(); }, 30);

                    function close(result) {
                        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                        document.removeEventListener('keydown', onKey);
                        resolve(result);
                    }
                    function onKey(e) {
                        if (e.key === 'Enter') { e.preventDefault(); close(true); }
                        else if (e.key === 'Escape') { e.preventDefault(); close(false); }
                    }
                    overlay.querySelector('.cancel').addEventListener('click', function() { close(false); });
                    overlay.querySelector('.confirm').addEventListener('click', function() { close(true); });
                    overlay.addEventListener('click', function(e) { if (e.target === overlay) close(false); });
                    document.addEventListener('keydown', onKey);
                });
            }

            function customPrompt(message, defaultValue) {
                return new Promise(function(resolve) {
                    const overlay = document.createElement('div');
                    overlay.className = 'custom-modal-overlay';
                    overlay.innerHTML =
                        '<div class="custom-modal">' +
                            '<div class="custom-modal-title">请输入</div>' +
                            '<div class="custom-modal-message"></div>' +
                            '<input type="text" class="custom-modal-input" value="" />' +
                            '<div class="custom-modal-actions">' +
                                '<button type="button" class="custom-modal-btn cancel">取消</button>' +
                                '<button type="button" class="custom-modal-btn confirm primary">确定</button>' +
                            '</div>' +
                        '</div>';
                    overlay.querySelector('.custom-modal-message').textContent = message;
                    const input = overlay.querySelector('.custom-modal-input');
                    input.value = defaultValue || '';
                    document.body.appendChild(overlay);
                    setTimeout(function() { input.focus(); input.select(); }, 30);

                    function close(result) {
                        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
                        document.removeEventListener('keydown', onKey);
                        resolve(result);
                    }
                    function onKey(e) {
                        if (e.key === 'Enter') { e.preventDefault(); close(input.value.trim()); }
                        else if (e.key === 'Escape') { e.preventDefault(); close(null); }
                    }
                    overlay.querySelector('.cancel').addEventListener('click', function() { close(null); });
                    overlay.querySelector('.confirm').addEventListener('click', function() { close(input.value.trim()); });
                    overlay.addEventListener('click', function(e) { if (e.target === overlay) close(null); });
                    document.addEventListener('keydown', onKey);
                });
            }

            function showMenu() {
                if (menuScreen) menuScreen.classList.remove('hidden');
                renderMenuLessons();
            }
            function hideMenu() {
                menuScreen.classList.add('hidden');
            }

            // 渲染首页课程卡片
            function renderMenuLessons() {
                fetch('/api/lessons')
                    .then(r => r.json())
                    .then(data => {
                        const lessons = data.lessons || [];
                        if (!lessons.length) {
                            if (menuLessonList) menuLessonList.innerHTML = '<div class="menu-empty">还没有课程，点击下方「创建课程」开始吧</div>';
                            return;
                        }
                                                menuLessonList.innerHTML = lessons.map(lesson => {
                            const total = lesson.units_count || 0;
                            const current = (lesson.current_unit || 0) + 1;
                            const completed = (lesson.completed_units || []).length;
                            const pct = lesson.progress_pct || 0;
                            const title = lesson.topic || lesson.name;
                            const isCurrent = lesson.name === currentLesson;
                            const cls = isCurrent ? ' is-current' : '';
                            const lastAccess = lesson.last_access
                                ? '🕒 ' + new Date(lesson.last_access).toLocaleString('zh-CN', { hour12: false })
                                : '🕒 未开始';
                            const unitsInfo = total > 0
                                ? '📚 第 ' + current + ' / ' + total + ' 课 · 已完成 ' + completed
                                : '📚 单课课程';
                            const r = 18;
                            const C = 2 * Math.PI * r;
                            const dash = (C * pct) / 100;
                            return `
                            <div class="menu-lesson-card${cls}" data-name="${lesson.name}">
                                <div class="m-actions">
                                    <button class="m-rename-btn" title="重命名">✎</button>
                                    <button class="m-del-btn" title="删除课程">✕</button>
                                </div>
                                <div class="m-header">
                                    <div class="m-name">${title}</div>
                                    <div class="m-progress-ring">
                                        <svg viewBox="0 0 44 44">
                                            <circle class="m-ring-bg" cx="22" cy="22" r="${r}"></circle>
                                            <circle class="m-ring-fg" cx="22" cy="22" r="${r}"
                                                stroke-dasharray="${C}" stroke-dashoffset="${C - dash}"></circle>
                                        </svg>
                                        <div class="m-ring-label">${pct}%</div>
                                    </div>
                                </div>
                                <div class="m-progress-bar"><div class="m-progress-bar-fill" style="width:${pct}%"></div></div>
                                <div class="m-meta">${unitsInfo}</div>
                                <div class="m-meta">${lastAccess}</div>
                            </div>
                            `;
                        }).join('');
                        document.querySelectorAll('.menu-lesson-card').forEach(card => {
                            card.addEventListener('click', function(e) {
                                if (e.target.closest('.m-actions')) return;
                                enterLesson(this.dataset.name);
                            });
                        });
                        document.querySelectorAll('.menu-lesson-card .m-del-btn').forEach(btn => {
                            btn.addEventListener('click', function(e) {
                                e.stopPropagation();
                                const card = this.closest('.menu-lesson-card');
                                const name = card.dataset.name;
                                const title = card.querySelector('.m-name').textContent;
                                customConfirm('删除课程', `确定要删除「${title}」吗？
所有对话、进度、图片、板书将一起删除，无法恢复。`, '删除', '取消')
                                    .then(ok => {
                                        if (!ok) return;
                                        fetch('/api/lessons/' + encodeURIComponent(name), { method: 'DELETE' })
                                            .then(r => r.json())
                                            .then(data => {
                                                if (data.error) {
                                                    alert('删除失败: ' + data.error);
                                                } else {
                                                    if (name === currentLesson) currentLesson = null;
                                                    renderMenuLessons();
                                                }
                                            })
                                            .catch(err => alert('删除失败: ' + err.message));
                                    });
                            });
                        });
                        document.querySelectorAll('.menu-lesson-card .m-rename-btn').forEach(btn => {
                            btn.addEventListener('click', function(e) {
                                e.stopPropagation();
                                const card = this.closest('.menu-lesson-card');
                                const name = card.dataset.name;
                                const title = card.querySelector('.m-name').textContent;
                                const renamePromptMsg = `当前名称：${title}

（仅修改目录名，对话历史与进度保留）`;
                                customPrompt('重命名课程', renamePromptMsg, title)
                                    .then(newTitle => {
                                        if (!newTitle || newTitle.trim() === '' || newTitle === title) return;
                                        fetch('/api/lessons/' + encodeURIComponent(name) + '/rename', {
                                            method: 'POST',
                                            headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ new_name: newTitle.trim() })
                                        })
                                            .then(r => r.json())
                                            .then(data => {
                                                if (data.error) {
                                                    alert('重命名失败: ' + data.error);
                                                } else {
                                                    if (name === currentLesson) currentLesson = data.new_name;
                                                    renderMenuLessons();
                                                }
                                            })
                                            .catch(err => alert('重命名失败: ' + err.message));
                                    });
                            });
                        });
                    })
                    .catch(err => {
                        if (menuLessonList) menuLessonList.innerHTML = '<div class="menu-empty">课程加载失败: ' + err.message + '</div>';
                    });
            }

            // 进入课程（加载课程 → 关闭菜单 → 进入主界面）
            function enterLesson(name) {
                fetch('/api/switch_lesson', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lesson_folder: name })
                }).then(r => r.json()).then(data => {
                    currentLesson = name;
                    const history = data.conversation || data.history || [];
                    if (history.length) {
                        conversation.innerHTML = '';
                        history.forEach(msg => {
                            // 终端执行记录只作为 AI 上下文，不显示为聊天气泡
                            if (msg.content && String(msg.content).indexOf('[终端执行记录]') === 0) return;
                            const bubble = addBubble(msg.content, msg.role === 'user' ? 'user' : 'teacher');
                            if (msg.role === 'assistant') bubble.innerHTML = renderMarkdown(msg.content);
                        });
                    } else {
                        conversation.innerHTML = `<div class="bubble teacher">已切换到「${name}」，开始学习吧！</div>`;
                    }
                    hideMenu();
                    switchView('chat');
                    loadLessons();
                    loadBoard();
                    // 更新 topbar 课程名
                    const topicName = (data.metadata && data.metadata.topic) || name;
                    updateTopbarCourseName(topicName);
                    // 更新课程进度条：当前单元 X / 总单元 Y
                    if (data.metadata && data.progress) {
                        updateUnitProgress(Object.assign({}, data.metadata, { current_unit: data.progress.current_unit || 0 }));
                    } else if (data.metadata) {
                        updateUnitProgress(data.metadata);
                    }
                }).catch(err => alert('进入课程失败: ' + err.message));
            }

            // 创建课程（输入主题 → AI 备课 → 进入）
            menuCreateBtn.addEventListener('click', function() {
                customPrompt('请输入课程主题，AI 将自动备课：\n\n例如：初中物理牛顿定律 / Python 入门 / Alevel 数学 M1P1')
                    .then(function(topic) {
                        if (topic && topic.trim()) {
                            prepareAndEnter(topic.trim());
                        }
                    });
            });

            // AI 备课并进入课程
            function prepareAndEnter(topic) {
                // 显示备课中状态（使用专用 loading overlay）
                var loadingOverlay = document.getElementById('menu-loading-overlay');
                var loadingText = document.getElementById('menu-loading-text');
                var loadingSub = document.getElementById('menu-loading-sub');
                if (loadingText) loadingText.textContent = '⏳ 正在备课「' + topic + '」…';
                if (loadingSub) loadingSub.innerHTML = 'AI 正在生成课程大纲、知识点与随堂测验<br>约需 1-3 分钟，请稍候';
                if (loadingOverlay) loadingOverlay.classList.add('visible');
                menuCreateBtn.disabled = true;
                const originalText = menuCreateBtn.textContent;
                menuCreateBtn.textContent = '备课中…';

                fetch('/api/prepare_lesson', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topic: topic })
                }).then(r => r.json())
                .then(data => {
                    var loadingOverlay = document.getElementById('menu-loading-overlay');
                    if (loadingOverlay) loadingOverlay.classList.remove('visible');
                    menuCreateBtn.disabled = false;
                    menuCreateBtn.textContent = originalText;
                    if (data.lesson_folder) {
                        _previewLessonFolder = data.lesson_folder;
                        _previewTopic = topic;
                        showPreviewOverlay();
                        renderPreview(data.plan);
                    } else {
                        alert('备课失败: ' + (data.error || '未知错误'));
                        renderMenuLessons();
                    }
                }).catch(err => {
                    var loadingOverlay = document.getElementById('menu-loading-overlay');
                    if (loadingOverlay) loadingOverlay.classList.remove('visible');
                    menuCreateBtn.disabled = false;
                    menuCreateBtn.textContent = originalText;
                    alert('备课失败: ' + err.message);
                    renderMenuLessons();
                });
            }

            // 首页 → 设置
            menuSettingsBtn.addEventListener('click', function() {
                hideMenu();
                switchView('settings');
            });

            // ============================
            // 4. 工具函数
            // ============================
            // 清理孤立的反引号代码块围栏（模型误把 \c 写成 ```c 时产生的乱码）。
            // 逻辑：只处理"未配对"的围栏——正常成对代码块（```lang ... ```）保留。
            function repairCodeFences(text) {
                if (!text || text.indexOf('```') === -1) return text;
                const lines = text.split('\n');
                let paired = new Set();   // 已成对的围栏行下标
                let stack = null;         // 未闭合的开围栏行号
                for (let i = 0; i < lines.length; i++) {
                    const t = lines[i].trim();
                    if (!t.startsWith('```')) continue;
                    const isLang = /^```\S/.test(t);   // ```c 这类带语言标记
                    if (stack === null) {
                        stack = i;
                    } else if (!isLang) {
                        // 裸 ``` 闭合当前代码块
                        paired.add(stack); paired.add(i);
                        stack = null;
                    } else {
                        // 已开又见 ```<lang> → 前一个围栏是误用标记，新围栏另起
                        stack = i;
                    }
                }
                const marker = segmentMarker ? (segmentMarker.value || '\\c') : '\\c';
                return lines.map((l, i) =>
                    (l.trim().startsWith('```') && !paired.has(i)) ? marker : l
                ).join('\n');
            }

            // 彻底清除分段符与指令标签（模型输出/历史存档可能残留）
            function cleanSeg(text) {
                if (!text) return '';
                let out = String(text);
                // 先修复模型误写的 ```c 孤立围栏（还原为分段标记），再统一清理
                out = repairCodeFences(out);
                const marker = segmentMarker ? (segmentMarker.value || '\\c') : '\\c';
                if (marker === '\\c') {
                    out = out.replace(/\\+c/g, '');       // \c / \\c 分段符
                } else if (marker) {
                    out = out.split(marker).join('');     // 自定义分段标记
                }
                // 兜底：清理"独立成行的裸 c"——模型常把 \c 转义丢失后写成单字符行
                // （仅当 marker 为 \c 时启用；只在「整行只有一个 c」时清理，避免误伤 C语言 等正常词）
                if (marker === '\\c') {
                    out = out
                        .split('\n')
                        .map(line => /^\s*c\s*$/.test(line) ? '' : line)
                        .join('\n');
                }
                // 合并多余的空行（连续 3+ 个换行合并为 2 个）
                out = out.replace(/\n{3,}/g, '\n\n').trim();
                out = out
                    .replace(/\[(ACTION|EMOTION|TOOL):[^\]]*\]/gi, '')  // [ACTION:xxx] [EMOTION:xxx] [TOOL:xxx]
                    .replace(/【(ACTION|EMOTION|TOOL):[^】]*】/gi, '');   // 中文括号变体
                return out;
            }

            function renderMarkdown(text) {
                if (!text) return '';
                return DOMPurify.sanitize(marked.parse(cleanSeg(text)));
            }

            function addBubble(text, sender) {
                const bubble = document.createElement('div');
                bubble.className = `bubble ${sender}`;
                bubble.textContent = cleanSeg(text);
                conversation.appendChild(bubble);
                conversation.scrollTop = conversation.scrollHeight;
                return bubble;
            }

            // ============================
            // 5. 对话功能
            // ============================
            sendBtn.addEventListener('click', sendMessage);
            messageInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
            // Auto-resize textarea
            messageInput.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 140) + 'px';
            });

            function sendMessage() {
                const text = messageInput.value.trim();
                const attachments = getPendingAttachments();
                if ((!text && !attachments.length) || isStreaming) return;
                // 斜杠命令：/exam /next /board 等
                const slashMatch = text.match(/^\/(\w+)\s*(.*)$/);
                if (slashMatch) {
                    handleSlashCommand(slashMatch[1].toLowerCase(), slashMatch[2].trim());
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
                    return;
                }
                // 未选择课程时提示
                if (!currentLesson || currentLesson === 'default') {
                    addBubble('请先在首页选择或创建课程，再开始对话。', 'teacher');
                    messageInput.value = '';
                    return;
                }
                messageInput.value = '';
                messageInput.style.height = 'auto';
                addBubble(text || '📎 发送附件', 'user');
                clearPendingAttachments();

                // 重置 Galgame 对话条
                dialogueSegments = [];
                dialogueSegIdx = -1;
                dialogueStreaming = true;
                dialogueBar.style.display = 'block';
                dialogueContent.textContent = '……';
                dialogueContent.classList.remove('type-caret');
                dialogueIndicator.textContent = '老师思考中';
                dialogueIndicator.classList.remove('hidden');

                const teacherBubble = addBubble('', 'teacher');
                isStreaming = true;
                sendBtn.disabled = true;

                // 安全网：若 60 秒后还在 isStreaming，强制释放（避免 SSE 卡死后用户再发消息被锁）
                const _streamTimeout = setTimeout(function() {
                    if (isStreaming) {
                        console.warn('[chat] 流式超时（60s），强制释放 isStreaming');
                        teacherBubble.textContent = (teacherBubble.textContent || '') + '\n（响应超时）';
                        isStreaming = false;
                        sendBtn.disabled = false;
                        dialogueStreaming = false;
                        if (live2dModel) { try { triggerAction('idle'); } catch (e) {} }
                    }
                }, 60000);
                // 提前保存以便 finishDialogue 清除
                window._chatStreamTimeout = _streamTimeout;

                // Trigger speak animation: 通过动作管理器排队播放 Speak_01
                if (live2dModel) {
                    try { triggerAction('speak'); } catch (e) { console.warn('speak 触发失败:', e); }
                }

                fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: text, lesson_folder: currentLesson, attachments: attachments })
                }).then(response => {
                    console.log('[chat] response received, status=' + response.status + ', ok=' + response.ok);
                    if (!response.body) throw new Error('No response body');
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let fullText = '';
                    let finished = false;

                    function finishDialogue() {
                        if (finished) return;
                        finished = true;
                        if (window._chatStreamTimeout) { clearTimeout(window._chatStreamTimeout); window._chatStreamTimeout = null; }
                        isStreaming = false;
                        sendBtn.disabled = false;
                        dialogueStreaming = false;
                        if (live2dModel) {
                            // 回复结束：切换回 idle 待机动作
                            try { triggerAction('idle'); } catch (e) { console.warn('idle 触发失败:', e); }
                        }
                        // 流式结束后：Galgame 逐段打字机播放
                        startGalgamePlayback();
                    }

                    function read() {
                        reader.read().then(({ done, value }) => {
                            if (done) {
                                teacherBubble.innerHTML = renderMarkdown(fullText);
                                conversation.scrollTop = conversation.scrollHeight;
                                finishDialogue();
                                return;
                            }
                            const chunk = decoder.decode(value);
                            const lines = chunk.split('\n');
                            for (const line of lines) {
                                if (line.startsWith('data: ')) {
                                    const payload = line.replace('data: ', '').trim();
                                    if (payload === '[DONE]') {
                                        teacherBubble.innerHTML = renderMarkdown(fullText);
                                        conversation.scrollTop = conversation.scrollHeight;
                                        finishDialogue();
                                        return;
                                    }
                                    try {
                                        const data = JSON.parse(payload);
                                        // 按 segment 分组存储（分段展示，流式中不实时滚动，结束后逐段播放）
                                        if (data.content && !data.done) {
                                            const seg = data.segment !== undefined ? data.segment : 0;
                                            dialogueSegments[seg] = cleanSeg(data.content);
                                            fullText = dialogueSegments.filter(s => s).join('\n\n');
                                            teacherBubble.textContent = fullText;
                                            conversation.scrollTop = conversation.scrollHeight;
                                        }
                                        // done 帧：用完整内容更新侧边栏
                                        if (data.done && data.content) {
                                            // done 帧内容也要走 cleanSeg（修复模型输出孤立的 ```c / 裸 c）
                                            fullText = cleanSeg(data.content);
                                            teacherBubble.innerHTML = renderMarkdown(fullText);
                                            conversation.scrollTop = conversation.scrollHeight;
                                        }
                                        // AI 联动：收到动作指令 → 触发 Live2D 动作
                                        if (data.action) {
                                            triggerAction(data.action);
                                            console.log('AI 触发动作:', data.action);
                                        }
                                        // AI 联动：收到情绪 → 设置表情
                                        if (data.emotion) {
                                            setEmotion(data.emotion);
                                        }
                                        // 流式中间的情绪帧（Open-LLM-VTuber 协议）：实时切表情
                                        if (data.emotion_stream) {
                                            setEmotion(data.emotion_stream);
                                            console.log('[emotion_stream]', data.emotion_stream);
                                        }
                                        // AI 工具调用：收到终端/图片/黑板指令 → 执行
                                        if (data.tool_event && typeof data.tool_event === 'object') {
                                            console.log('AI 工具调用:', data.tool_event);
                                            handleAITool(data.tool_event);
                                        }
                                        // AI 联动：收到 TTS 音频 → 播放 + 口型同步
                                        if (data.audio_url && data.done) {
                                            playTeacherAudio(data.audio_url);
                                        }
                                        // 收到 done 帧 → 结束流式
                                        if (data.done) {
                                            finishDialogue();
                                            return;
                                        }
                                    } catch (e) {}
                                }
                            }
                            read();
                        });
                    }
                    read();
                }).catch(err => {
                    console.error('[chat] fetch error:', err);
                    if (window._chatStreamTimeout) { clearTimeout(window._chatStreamTimeout); window._chatStreamTimeout = null; }
                    teacherBubble.textContent = '\u274c ' + err.message;
                    isStreaming = false;
                    sendBtn.disabled = false;
                    dialogueStreaming = false;
                    dialogueBar.style.display = 'none';
                    if (live2dModel) {
                        // 错误时也恢复 idle 动作
                        try { triggerAction('idle'); } catch (e) { console.warn('idle 触发失败:', e); }
                    }
                });
            }

            // ---- 聊天斜杠命令：/exam /next /board ----
            function handleSlashCommand(cmd, args) {
                switch (cmd) {
                    case 'exam':
                        // 强制开始测验：生成题目并切到测验视图
                        addBubble('📝 强制开始随堂测验，正在出题...', 'teacher');
                        if (typeof examGenerateBtn !== 'undefined' && examGenerateBtn) {
                            examGenerateBtn.click();
                        } else {
                            examGenerateBtn = document.getElementById('exam-generate-btn');
                            if (examGenerateBtn) examGenerateBtn.click();
                        }
                        switchView('exam');
                        break;
                    case 'next':
                        // 强制进入下一课
                        if (!currentLesson || currentLesson === 'default') {
                            addBubble('请先选择课程再使用 /next', 'teacher');
                            return;
                        }
                        addBubble('⏩ 正在进入下一课...', 'teacher');
                        fetch('/api/lesson/next_unit', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ folder: currentLesson })
                        }).then(r => r.json()).then(data => {
                            if (data.success) {
                                addBubble('✅ 已进入下一课', 'teacher');
                                enterLesson(currentLesson);
                            } else {
                                addBubble('⚠️ ' + (data.message || '无法进入下一课'), 'teacher');
                            }
                        }).catch(err => addBubble('❌ /next 失败: ' + err.message, 'teacher'));
                        break;
                    case 'board':
                        // 强制显示板书
                        addBubble('📋 强制显示板书', 'teacher');
                        showBoard();
                        break;
                    case 'action':
                        // 强制播放某个动作：/action hello | /action Speak_01 | /action think
                        if (!args) {
                            addBubble('用法：/action <动作名>，或 /action list 查看全部；指向坐标用 /action point <left|right> <x> <y>', 'teacher');
                            break;
                        }
                        if (args.toLowerCase() === 'list') {
                            addBubble('🎬 内置动作：wave（挥手）、hello（打招呼）、idle（待机）、listen（倾听）、speak（说话）、think（思考）', 'teacher');
                            const builtinKeys = ['point', 'blackboard', 'greet', 'hello', 'idle', 'listen', 'speak', 'think', 'wave'];
                            const customKeys = Object.keys(ACTION_MAP).filter(function(k) { return builtinKeys.indexOf(k) < 0; });
                            if (customKeys.length) {
                                addBubble('🎬 自定义动作：' + customKeys.map(k => k + '→' + ACTION_MAP[k]).join('、'), 'teacher');
                            }
                            addBubble('🎬 指向坐标：/action point <left|right> <x> <y> —— 左/右手指向画面坐标 (x, y)', 'teacher');
                            break;
                        }
                        // /action point left 100 200 —— 左右手指向画面坐标
                        {
                            const pMatch = args.match(/^point\s+(left|right)\s+([-\d.]+)\s+([-\d.]+)$/i);
                            if (pMatch) {
                                const side = pMatch[1].toLowerCase();
                                const px = parseFloat(pMatch[2]);
                                const py = parseFloat(pMatch[3]);
                                pointerTo(side, px, py);
                                addBubble('🎬 角色' + (side === 'left' ? '左手' : '右手') + '指向 (' + px + ', ' + py + ')', 'teacher');
                                break;
                            }
                        }
                        if (typeof triggerAction === 'function') {
                            triggerAction(args).then(function() {
                                addBubble('🎬 已触发动作：' + args, 'teacher');
                            }).catch(function() {
                                addBubble('⚠️ 动作 ' + args + ' 不存在或执行失败', 'teacher');
                            });
                        } else if (typeof live2dModel !== 'undefined' && live2dModel && typeof live2dModel.playAnimation === 'function') {
                            try {
                                live2dModel.playAnimation(args);
                                addBubble('🎬 已触发动作：' + args, 'teacher');
                            } catch (e) {
                                addBubble('❌ 动作执行失败: ' + e.message, 'teacher');
                            }
                        } else {
                            addBubble('⚠️ 动作系统未就绪', 'teacher');
                        }
                        break;
                    case 'emotion':
                        // 强制显示某个表情：/emotion happy | /emotion sad | /emotion think
                        if (!args) {
                            addBubble('用法：/emotion <表情名> 或 /emotion list 查看全部，可选：happy / sad / angry / think / surprised / neutral', 'teacher');
                            break;
                        }
                        if (args.toLowerCase() === 'list') {
                            addBubble('🎭 支持的表情：happy（开心）、sad（难过）、angry（生气）、think（思考）、surprised（惊讶）、neutral（平静）、smirk（俏皮）、fear（害怕）、disgust（嫌弃）', 'teacher');
                            break;
                        }
                        if (typeof setEmotion === 'function') {
                            const emoAlias = { joy: 'happy', happiness: 'happy', sadness: 'sad', anger: 'angry', surprise: 'surprised', shocked: 'surprised' };
                            const emo = emoAlias[args.toLowerCase()] || args.toLowerCase();
                            setEmotion(emo, 8000);
                            addBubble('🎭 已强制显示表情：' + emo, 'teacher');
                        } else {
                            addBubble('⚠️ 表情系统未就绪', 'teacher');
                        }
                        break;
                    case 'ask':
                    case 'llm':
                        // 直接与 LLM 对话（不带课程上下文）
                        if (!args) {
                            addBubble('用法：/ask <内容> —— 直接与 AI 对话（不带课程上下文）', 'teacher');
                            break;
                        }
                        {
                            const askBubble = addBubble('🤖 思考中…', 'teacher');
                            fetch('/api/llm/chat', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ message: args })
                            }).then(r => r.json()).then(data => {
                                if (data.content) {
                                    askBubble.textContent = data.content;
                                } else {
                                    askBubble.textContent = '❌ ' + (data.error || 'AI 无响应');
                                }
                                conversation.scrollTop = conversation.scrollHeight;
                            }).catch(err => {
                                askBubble.textContent = '❌ /ask 失败: ' + err.message;
                                conversation.scrollTop = conversation.scrollHeight;
                            });
                        }
                        break;
                    case 'terminal':
                        // 打开终端弹窗（可交互）：/terminal python print("hi") → 直接执行
                        if (args) {
                            const parts = args.split(/\s+/);
                            const lang = parts[0] || 'python';
                            const code = parts.slice(1).join(' ');
                            addBubble('💻 终端打开，执行: ' + lang + ' ' + code.slice(0, 60), 'teacher');
                            showTerminal({ language: lang, code: code, autoRun: true });
                        } else {
                            addBubble('💻 终端已打开，输入代码即可运行', 'teacher');
                            showTerminal({});
                        }
                        break;
                    case 'image':
                        // 强制显示当前单元图片库
                        if (!currentLesson) {
                            addBubble('请先选择课程再使用 /image', 'teacher');
                            break;
                        }
                        addBubble('🖼️ 正在打开图片库...', 'teacher');
                        // /image 2  → 打开第 2 张图片
                        const imgIdx = parseInt(args);
                        showImagePanel(isNaN(imgIdx) ? {} : { index: imgIdx - 1 });
                        break;
                    case 'help':
                    case 'h':
                        // 显示所有可用命令的帮助
                        addBubble('📖 可用命令：', 'teacher');
                        addBubble('/exam —— 强制开始随堂测验', 'teacher');
                        addBubble('/next —— 进入下一课', 'teacher');
                        addBubble('/board —— 显示黑板', 'teacher');
                        addBubble('/image [编号] —— 打开当前单元图片库', 'teacher');
                        addBubble('/terminal [语言] [代码] —— 打开终端弹窗并执行代码（语言: python/javascript/shell/powershell，可交互）', 'teacher');
                        addBubble('/action <动作名> —— 播放模型动作（/action list 查看全部，自定义动作可在设置中添加）', 'teacher');
                        addBubble('/emotion <表情名> —— 强制显示表情（happy/sad/angry/think/surprised/neutral）', 'teacher');
                        addBubble('/ask <内容> —— 直接与 AI 对话（不带课程上下文）', 'teacher');
                        addBubble('/help —— 显示本帮助', 'teacher');
                        break;
                    default:
                        addBubble('❓ 未知命令 /' + cmd + '，输入 /help 查看可用命令', 'teacher');
                }
            }

            // ---- Galgame 对话条：逐段打字机播放 ----
            function startGalgamePlayback() {
                // 过滤掉空洞（未推送到的段号）
                const validSegments = dialogueSegments.filter(s => s && s.trim());
                console.log('[galgame] startGalgamePlayback, raw.length=' + dialogueSegments.length +
                            ', valid.length=' + validSegments.length +
                            ', segments=' + JSON.stringify(dialogueSegments));
                dialogueSegments = validSegments;
                if (!dialogueSegments.length) {
                    // 兜底：SSE 结束时没有收到任何分段（极少见）。
                    // 至少在对话条里告知用户，而不是立刻消失。
                    console.warn('[galgame] 无有效分段，保留对话条提示');
                    dialogueContent.textContent = '（老师这一轮没有回复文字）';
                    dialogueContent.classList.remove('type-caret');
                    dialogueIndicator.textContent = '—';
                    dialogueIndicator.classList.remove('hidden');
                    return;
                }
                dialogueSegIdx = -1;
                dialogueBar.style.display = 'block';
                dialogueIndicator.classList.remove('hidden');
                advanceDialogue();
            }

            function advanceDialogue() {
                dialogueSegIdx++;
                if (dialogueSegIdx >= dialogueSegments.length) {
                    // 全部播完：保持显示最后一句（永久保留，不响应关闭），指示器切换为「已记录」
                    dialogueTypeTimer && clearInterval(dialogueTypeTimer);
                    dialogueTypeTimer = null;
                    dialogueContent.classList.remove('type-caret');
                    // 显示最后一段的完整内容（保证即使打字被打断也展示全文）
                    const last = dialogueSegments[dialogueSegments.length - 1] || '';
                    dialogueContent.textContent = last;
                    dialogueContent.scrollTop = 0;
                    dialogueIndicator.textContent = '✓ 已记录';
                    dialogueIndicator.classList.remove('hidden');
                    return;
                }
                typeDialogue(dialogueSegments[dialogueSegIdx]);
            }

            function typeDialogue(text) {
                if (dialogueTypeTimer) clearInterval(dialogueTypeTimer);
                const chars = text.split('');
                dialogueContent.textContent = '';
                dialogueContent.classList.add('type-caret');
                dialogueIndicator.textContent = '▼';
                dialogueIndicator.classList.add('hidden');
                let i = 0;
                dialogueTypeTimer = setInterval(function() {
                    if (i < chars.length) {
                        dialogueContent.textContent = chars.slice(0, i + 1).join('');
                        dialogueContent.scrollTop = dialogueContent.scrollHeight;
                        i++;
                    } else {
                        clearInterval(dialogueTypeTimer);
                        dialogueTypeTimer = null;
                        dialogueContent.classList.remove('type-caret');
                        // 打字完成：显示完整文本（保证最后字符一定可见）
                        dialogueContent.textContent = text;
                        dialogueContent.scrollTop = 0;
                        // 如果是最后一段 → 切换为「已记录」（对话条永久保留）；否则提示按 Enter 继续
                        if (dialogueSegIdx >= dialogueSegments.length - 1) {
                            dialogueIndicator.textContent = '✓ 已记录';
                            dialogueIndicator.classList.remove('hidden');
                        } else {
                            dialogueIndicator.textContent = '▼ 按 Enter 继续';
                            dialogueIndicator.classList.remove('hidden');
                        }
                    }
                }, 28);
            }

            // 点击对话条 → 跳过打字 / 下一段；播完后只保留，不再关闭
            dialogueBar.addEventListener('click', function() {
                if (dialogueStreaming) return;
                // 最后一段播完：保留显示，不再响应点击关闭
                if (!dialogueTypeTimer && dialogueSegIdx >= dialogueSegments.length - 1 && dialogueSegments.length > 0) {
                    return;
                }
                advanceDialogueOrSkip();
            });

            function closeDialogue() {
                console.log('[galgame] closeDialogue');
                dialogueBar.style.display = 'none';
                dialogueSegments = [];
                dialogueSegIdx = -1;
            }

            // 按 Enter 才显示下一段（捕获阶段拦截，输入框也不会发送）
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !dialogueStreaming &&
                    dialogueBar.style.display !== 'none' && dialogueBar.style.display !== '') {
                    // 仅当还有未播完的段落（打字中 或 还有后续段）才拦截；
                    // 全部播完后 Enter 恢复正常（可发送消息/命令）
                    const hasPending = dialogueTypeTimer || dialogueSegIdx < dialogueSegments.length - 1;
                    if (hasPending) {
                        e.preventDefault();
                        e.stopPropagation();
                        advanceDialogueOrSkip();
                    }
                }
            }, true);

            function advanceDialogueOrSkip() {
                if (dialogueTypeTimer) {
                    clearInterval(dialogueTypeTimer);
                    dialogueTypeTimer = null;
                    const segText = dialogueSegments[dialogueSegIdx] || '';
                    dialogueContent.textContent = segText;
                    dialogueContent.classList.remove('type-caret');
                    // 跳过当前段打字后，判断是否是最后一段
                    if (dialogueSegIdx >= dialogueSegments.length - 1) {
                        dialogueIndicator.textContent = '✓ 已记录';
                    } else {
                        dialogueIndicator.textContent = '▼ 按 Enter 继续';
                    }
                    dialogueIndicator.classList.remove('hidden');
                    return;
                }
                advanceDialogue();
            }

// ============================
            // 6. 语音朗读
            // ============================
            playBtn.addEventListener('click', function() {
                const last = conversation.querySelector('.bubble.teacher:last-child');
                if (last && last.textContent) {
                    const utter = new SpeechSynthesisUtterance(last.textContent);
                    utter.lang = 'zh-CN';
                    utter.rate = 1.0;
                    speechSynthesis.speak(utter);
                }
            });

            // ============================
            // 7. 测验功能
            // ============================
            examGenerateBtn.addEventListener('click', function() {
                const topic = examTopic.value.trim() || '通用';
                examGenerateBtn.textContent = '生成中...';
                examGenerateBtn.disabled = true;
                fetch('/api/exam/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topic, num_questions: 3 })
                }).then(r => r.json()).then(data => {
                    examGenerateBtn.textContent = '生成题目';
                    examGenerateBtn.disabled = false;
                    if (data.questions && data.questions.length) {
                        renderExamQuestions(data.questions);
                        examSubmitBtn.style.display = 'inline-block';
                    } else {
                        examList.innerHTML = '<span style="color:var(--text-dim);">暂无题目</span>';
                        examSubmitBtn.style.display = 'none';
                    }
                }).catch(err => {
                    examGenerateBtn.textContent = '生成题目';
                    examGenerateBtn.disabled = false;
                    examList.innerHTML = '<span style="color:#e07060;">失败: ' + err.message + '</span>';
                });
            });

            function renderExamQuestions(questions) {
                examList.innerHTML = questions.map((q, idx) => `
                    <div class="exam-question" data-idx="${idx}">
                        <div class="q-title">第 ${idx+1} 题（${q.type || '单选'}）</div>
                        <div style="margin-bottom:6px; font-size:14px; color:var(--text-primary);">${renderMarkdown(q.question)}</div>
                        <div class="q-options">
                            ${q.options ? q.options.map((opt, oi) => `
                                <label>
                                    <input type="${q.type === '多选' ? 'checkbox' : 'radio'}" name="q${idx}" value="${String.fromCharCode(65+oi)}">
                                    ${renderMarkdown(opt)}
                                </label>
                            `).join('') : ''}
                        </div>
                        <div class="q-explanation" style="display:none;"></div>
                    </div>
                `).join('');
            }

            examSubmitBtn.addEventListener('click', function() {
                const questions = document.querySelectorAll('.exam-question');
                const answers = {};
                questions.forEach((qDiv, idx) => {
                    const inputs = qDiv.querySelectorAll('input:checked');
                    answers[idx] = Array.from(inputs).map(i => i.value);
                });
                fetch('/api/exam/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ answers })
                }).then(r => r.json()).then(data => {
                    if (data.details) {
                        document.querySelectorAll('.exam-question').forEach((qDiv, idx) => {
                            const expl = qDiv.querySelector('.q-explanation');
                            if (data.details[idx]) {
                                expl.textContent = '得分: ' + data.details[idx].score + ' | ' + (data.details[idx].explanation || '');
                                expl.style.display = 'block';
                            }
                        });
                    }
                    alert('得分: ' + (data.score || 0) + ' / ' + (data.total || 0));
                }).catch(err => alert('提交失败: ' + err.message));
            });

            // ============================
            // 8. 资源功能
            // ============================
            function loadResources() {
                fetch('/api/list_resources')
                    .then(r => r.json())
                    .then(data => {
                        if (data.resources && data.resources.length) {
                            resourceList.innerHTML = data.resources.map((r, idx) => `
                                <div class="resource-item">
                                    <label>
                                        <input type="checkbox" value="${idx}" checked>
                                        <span><strong>${r.title}</strong> <small>${r.type} · ${r.description || ''}</small></span>
                                    </label>
                                </div>
                            `).join('');
                        } else {
                            resourceList.innerHTML = '<span style="color:var(--text-dim);">暂无资源</span>';
                        }
                    })
                    .catch(() => { resourceList.innerHTML = '<span style="color:var(--text-dim);">加载失败</span>'; });
            }

            downloadBtn.addEventListener('click', function() {
                const checked = document.querySelectorAll('#resource-list input:checked');
                if (!checked.length) { alert('请选择至少一个资源'); return; }
                const selected = Array.from(checked).map(cb => parseInt(cb.value, 10));
                downloadBtn.disabled = true;
                downloadBtn.textContent = '下载中…';
                // 内联状态提示（不遮挡输入框）
                let statusEl = document.getElementById('download-status');
                if (!statusEl) {
                    statusEl = document.createElement('div');
                    statusEl.id = 'download-status';
                    statusEl.style.cssText = 'font-size:12px; color:var(--text-muted); margin-top:8px; line-height:1.6;';
                    downloadBtn.parentNode.insertBefore(statusEl, downloadBtn.nextSibling);
                }
                statusEl.textContent = '⏳ 正在下载 ' + selected.length + ' 个资源…';
                fetch('/api/download_resources', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ selected: selected, lesson_folder: currentLesson })
                }).then(r => r.json()).then(data => {
                    downloadBtn.disabled = false;
                    downloadBtn.textContent = '⬇ 下载选中';
                    if (data.status === 'ok') {
                        const downloads = data.downloads || [];
                        const failed = downloads.filter(d => d.status === 'error');
                        const okList = downloads.filter(d => d.status === 'ok');
                        if (failed.length === downloads.length) {
                            statusEl.textContent = '❌ 下载失败：' + (failed[0].error || '未知错误');
                        } else if (failed.length) {
                            statusEl.innerHTML = '⚠️ 部分成功：' + okList.map(d => d.title).join('、') + '；失败：' + failed.map(d => d.title).join('、');
                        } else {
                            statusEl.innerHTML = '✅ 下载完成：' + okList.map(d => d.title).join('、') + '<br><small style="color:var(--text-dim);">保存在课程目录 lessons/' + (data.lesson_folder || '') + '/</small>';
                        }
                    } else {
                        statusEl.textContent = '❌ 下载失败: ' + (data.error || '未知错误');
                    }
                    setTimeout(() => { statusEl.textContent = ''; }, 8000);
                }).catch(err => {
                    downloadBtn.disabled = false;
                    downloadBtn.textContent = '⬇ 下载选中';
                    statusEl.textContent = '❌ 下载失败: ' + err.message;
                });
            });

            // ============================
            // 9. 课程功能
            // ============================
            function loadLessons() {
                fetch('/api/lessons')
                    .then(r => r.json())
                    .then(data => {
                        if (data.lessons && data.lessons.length) {
                            lessonList.innerHTML = data.lessons.map(lesson => `
                                <div class="lesson-card" data-name="${lesson.name}">
                                    <div class="name">${lesson.name}</div>
                                    <div class="meta">${lesson.last_access || '未学习'} · ${lesson.progress || 0}%</div>
                                    <div class="progress-bar"><div class="fill" style="width:${lesson.progress || 0}%"></div></div>
                                </div>
                            `).join('');
                            document.querySelectorAll('.lesson-card').forEach(card => {
                                card.addEventListener('click', function() {
                                    const name = this.dataset.name;
                                    fetch('/api/switch_lesson', {
                                        method: 'POST',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({ lesson_folder: name })
                                    }).then(r => r.json()).then(data => {
                                        currentLesson = name;
                                        const history = data.conversation || data.history || [];
                                        if (history.length) {
                                            conversation.innerHTML = '';
                                            history.forEach(msg => {
                                                // 终端执行记录只作为 AI 上下文，不显示为聊天气泡
                                                if (msg.content && String(msg.content).indexOf('[终端执行记录]') === 0) return;
                                                const bubble = addBubble(msg.content, msg.role === 'user' ? 'user' : 'teacher');
                                                if (msg.role === 'assistant') {
                                                    bubble.innerHTML = renderMarkdown(msg.content);
                                                }
                                            });
                                        } else {
                                            conversation.innerHTML = '<div class="bubble teacher">已切换到「' + name + '」</div>';
                                        }
                                        // 同步 topbar
                                        const topicName = (data.metadata && data.metadata.topic) || name;
                                        updateTopbarCourseName(topicName);
                                        if (data.metadata) {
                                            if (data.progress) {
                                                updateUnitProgress(Object.assign({}, data.metadata, { current_unit: data.progress.current_unit || 0 }));
                                            } else {
                                                updateUnitProgress(data.metadata);
                                            }
                                        }
                                        switchView('chat');
                                    }).catch(err => alert('切换失败: ' + err.message));
                                });
                            });
                        } else {
                            lessonList.innerHTML = '<span style="color:var(--text-dim);">暂无课程</span>';
                        }
                    })
                    .catch(() => { lessonList.innerHTML = '<span style="color:var(--text-dim);">加载失败</span>'; });
            }

            createLessonBtn.addEventListener('click', function() {
                customPrompt('请输入课程主题，AI 将自动备课：\n\n例如：初中物理牛顿定律 / Python 入门 / Alevel 数学 M1P1')
                    .then(function(topic) {
                        if (topic && topic.trim()) {
                            prepareAndEnter(topic.trim());
                        }
                    });
            });

            // ============================
            // 9.5 板书功能（Canvas 投影法识别文字区域 + 角色指向）
            // ============================

            // 投影法识别板书中的文字/内容区域（OpenCV 投影法的纯 JS 实现）
            function detectBoardKeypoints(imgEl, canvas) {
                const ctx = canvas.getContext('2d');
                canvas.width = imgEl.naturalWidth;
                canvas.height = imgEl.naturalHeight;
                ctx.drawImage(imgEl, 0, 0);
                const w = canvas.width, h = canvas.height;
                if (w === 0 || h === 0) return [];
                let imageData;
                try {
                    imageData = ctx.getImageData(0, 0, w, h);
                } catch (e) {
                    console.warn('无法读取图片像素（跨域？）', e);
                    return [];
                }
                const data = imageData.data;

                // 1. 灰度 + 二值化（暗像素 = 内容）
                const binary = new Uint8Array(w * h);
                for (let i = 0; i < w * h; i++) {
                    const r = data[i*4], g = data[i*4+1], b = data[i*4+2];
                    const gray = 0.299*r + 0.587*g + 0.114*b;
                    binary[i] = gray < 140 ? 1 : 0;
                }

                // 2. 水平投影：统计每行暗像素数 → 找文字行
                const rowProj = new Array(h).fill(0);
                for (let y = 0; y < h; y++) {
                    let count = 0;
                    for (let x = 0; x < w; x++) count += binary[y*w+x];
                    rowProj[y] = count;
                }
                const rowThreshold = Math.max(w * 0.03, 3);
                const rowRanges = [];
                let inRow = false, rowStart = 0;
                for (let y = 0; y < h; y++) {
                    if (rowProj[y] > rowThreshold && !inRow) { inRow = true; rowStart = y; }
                    else if (rowProj[y] <= rowThreshold && inRow) {
                        inRow = false;
                        if (y - rowStart >= 6) rowRanges.push([rowStart, y]);
                    }
                }
                if (inRow && h - rowStart >= 6) rowRanges.push([rowStart, h-1]);

                // 3. 对每个文字行做垂直投影 → 找文字块
                const keypoints = [];
                rowRanges.forEach(function(rr) {
                    const y1 = rr[0], y2 = rr[1];
                    const colProj = new Array(w).fill(0);
                    for (let y = y1; y <= y2; y++) {
                        for (let x = 0; x < w; x++) colProj[x] += binary[y*w+x];
                    }
                    const colThreshold = Math.max((y2-y1) * 0.25, 2);
                    let inBlock = false, blockStart = 0;
                    for (let x = 0; x < w; x++) {
                        if (colProj[x] > colThreshold && !inBlock) { inBlock = true; blockStart = x; }
                        else if (colProj[x] <= colThreshold && inBlock) {
                            inBlock = false;
                            if (x - blockStart >= 12) {
                                keypoints.push({ x: Math.round((blockStart+x)/2), y: Math.round((y1+y2)/2), w: x-blockStart, h: y2-y1 });
                            }
                        }
                    }
                    if (inBlock && w - blockStart >= 12) {
                        keypoints.push({ x: Math.round((blockStart+w)/2), y: Math.round((y1+y2)/2), w: w-blockStart, h: y2-y1 });
                    }
                });

                // 4. 在 Canvas 上绘制关键点标记
                ctx.strokeStyle = 'rgba(232, 90, 90, 0.9)';
                ctx.fillStyle = 'rgba(232, 90, 90, 0.35)';
                ctx.lineWidth = 3;
                keypoints.forEach(function(kp) {
                    ctx.beginPath();
                    ctx.rect(kp.x - kp.w/2, kp.y - kp.h/2, kp.w, kp.h);
                    ctx.fill();
                    ctx.stroke();
                });
                return keypoints;
            }

            // 角色看向板书关键点（头部朝向）
            function lookAtKeypoint(kp, canvasEl) {
                if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel || !live2dApp) return;
                const rect = canvasEl.getBoundingClientRect();
                // 关键点在画布中的比例位置
                const kpRatioX = kp.x / canvasEl.width;
                const kpRatioY = kp.y / canvasEl.height;
                const kpScreenX = rect.left + kpRatioX * rect.width;
                const kpScreenY = rect.top + kpRatioY * rect.height;
                // 角色锚点在画布中的位置
                const modelX = live2dApp.screen.width * (live2dSettings.posX / 100);
                const modelY = live2dApp.screen.height * (live2dSettings.posY / 100);
                const dx = kpScreenX - modelX;
                const dy = kpScreenY - modelY;
                // 偏移量 → 头部角度（clamp ±25）
                const angleX = Math.max(-25, Math.min(25, dx * 0.08));
                const angleY = Math.max(-20, Math.min(20, dy * 0.08));
                const core = live2dModel.internalModel.coreModel;
                const setP = function(id, val) {
                    _coreSetParam(core, id, val);
                };
                setP('ParamAngleX', angleX);
                setP('ParamAngleY', angleY);
                triggerAction('point'); // 指向动作
                // 2 秒后恢复
                setTimeout(function() {
                    setP('ParamAngleX', 0);
                    setP('ParamAngleY', 0);
                }, 2000);
                console.log('角色指向关键点:', Math.round(kp.x), Math.round(kp.y), '角度:', Math.round(angleX), Math.round(angleY));
            }

            // 加载并显示板书
            function loadBoard() {
                console.log('[BOARD] loadBoard, currentLesson =', currentLesson);
                if (!currentLesson) return;
                fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/board')
                    .then(r => r.json())
                    .then(data => {
                        console.log('[BOARD] API 返回:', data);
                        if (data.board_url) {
                            const img = new Image();
                            // 同源请求无需 CORS；设置 crossOrigin 反而会被无 CORS 头的响应拒绝
                            img.onload = function() {
                                console.log('[BOARD] 图片加载成功:', data.board_url, img.naturalWidth + 'x' + img.naturalHeight);
                                boardImageEl = img;
                                boardKeypoints = detectBoardKeypoints(img, boardCanvas);
                                console.log('[BOARD] 检测到关键点:', boardKeypoints.length, boardKeypoints);
                                boardPreview.style.display = 'block';
                                boardEmpty.style.display = 'none';
                                // 点击 Canvas 上的关键点 → 角色指向
                                boardCanvas.onclick = function(e) {
                                    const rect = boardCanvas.getBoundingClientRect();
                                    const px = (e.clientX - rect.left) / rect.width * boardCanvas.width;
                                    const py = (e.clientY - rect.top) / rect.height * boardCanvas.height;
                                    // 找最近的关键点
                                    let nearest = null, minDist = Infinity;
                                    boardKeypoints.forEach(function(kp) {
                                        const d = Math.hypot(px - kp.x, py - kp.y);
                                        if (d < minDist) { minDist = d; nearest = kp; }
                                    });
                                    if (nearest && minDist < 150) lookAtKeypoint(nearest, boardCanvas);
                                };
                            };
                            img.onerror = function() {
                                boardPreview.style.display = 'none';
                                boardEmpty.style.display = 'block';
                            };
                            img.src = data.board_url;
                        } else {
                            boardPreview.style.display = 'none';
                            boardEmpty.style.display = 'block';
                        }
                    })
                    .catch(() => {
                        boardPreview.style.display = 'none';
                        boardEmpty.style.display = 'block';
                    });
            }

            // ============================
            // 板书覆盖层：控制台命令 showBoard() / hideBoard()
            // ============================
            // showBoard(url?)：强制在立绘区展示板书
            //   - 不传 url：加载当前课程的 board_url（config.json 里记录）
            //   - 传 url：直接加载指定路径（如 '/api/lesson/20260804_Python/asset/board.png'）
            // showBoard 的新版本定义在下方（带"举手拉黑板"动画），本注释保留用于说明接口。

            function renderBoardOverlay(url) {
                console.log('[BOARD] 强制展示:', url);
                // 恢复图片模式：显示 canvas，隐藏 AI 内容层
                boardOverlayCanvas.style.display = '';
                const contentLayer = document.getElementById('board-content-layer');
                if (contentLayer) contentLayer.style.display = 'none';
                const img = new Image();
                img.onload = function() {
                    boardOverlayKeypoints = detectBoardKeypoints(img, boardOverlayCanvas);
                    console.log('[BOARD] 覆盖层关键点:', boardOverlayKeypoints.length);
                    boardOverlay.style.display = 'flex';
                    void boardOverlay.offsetWidth;
                    boardOverlay.classList.add('board-active');
                    boardOverlayCanvas.onclick = function(e) {
                        const rect = boardOverlayCanvas.getBoundingClientRect();
                        const px = (e.clientX - rect.left) / rect.width * boardOverlayCanvas.width;
                        const py = (e.clientY - rect.top) / rect.height * boardOverlayCanvas.height;
                        let nearest = null, minDist = Infinity;
                        boardOverlayKeypoints.forEach(function(kp) {
                            const d = Math.hypot(px - kp.x, py - kp.y);
                            if (d < minDist) { minDist = d; nearest = kp; }
                        });
                        if (nearest && minDist < 150) lookAtKeypoint(nearest, boardOverlayCanvas);
                    };
                };
                img.onerror = function() {
                    console.warn('[BOARD] 图片加载失败:', url);
                    // 图片加载失败也显示空黑板，不让用户感觉"没反应"
                    renderEmptyBoardOverlay();
                };
                img.src = url;
            }

            function hideBoard() {
                boardOverlay.classList.remove('board-active');
                boardOverlay.classList.add('board-closing');
                setTimeout(function() {
                    if (!boardOverlay.classList.contains('board-active')) {
                        boardOverlay.classList.remove('board-closing');
                        boardOverlay.style.display = 'none';
                    }
                }, 500);
            }

            // 渲染空黑板（无上传图片时也显示面板）
            function renderEmptyBoardOverlay() {
                // 恢复图片模式：显示 canvas，隐藏 AI 内容层
                boardOverlayCanvas.style.display = '';
                const contentLayer = document.getElementById('board-content-layer');
                if (contentLayer) contentLayer.style.display = 'none';
                const c = boardOverlayCanvas;
                // 适配 canvas 内部像素（保持 CSS 显示尺寸）
                const rect = c.getBoundingClientRect();
                if (c.width !== rect.width || c.height !== rect.height) {
                    c.width = Math.max(300, rect.width);
                    c.height = Math.max(200, rect.height);
                }
                const ctx = c.getContext('2d');
                ctx.clearRect(0, 0, c.width, c.height);
                // 黑板深色背景
                const grad = ctx.createLinearGradient(0, 0, 0, c.height);
                grad.addColorStop(0, '#1c2a22');
                grad.addColorStop(1, '#0f1814');
                ctx.fillStyle = grad;
                ctx.fillRect(0, 0, c.width, c.height);
                // 木质边框
                ctx.strokeStyle = '#5e3a1c';
                ctx.lineWidth = 10;
                ctx.strokeRect(5, 5, c.width - 10, c.height - 10);
                // 提示文字
                ctx.fillStyle = 'rgba(' + accentRGB() + ', 0.85)';
                ctx.font = 'bold ' + Math.max(16, Math.floor(c.width / 22)) + 'px serif';
                ctx.textAlign = 'center';
                ctx.fillText('📋  黑板', c.width / 2, c.height / 2 - 20);
                ctx.fillStyle = 'rgba(180, 180, 180, 0.7)';
                ctx.font = Math.max(12, Math.floor(c.width / 36)) + 'px serif';
                ctx.fillText('当前课程暂无板书内容', c.width / 2, c.height / 2 + 12);
                ctx.fillStyle = 'rgba(150, 150, 150, 0.55)';
                ctx.font = Math.max(11, Math.floor(c.width / 44)) + 'px serif';
                ctx.fillText('可在右侧「板书」面板上传图片，或直接让 AI 在此讲解', c.width / 2, c.height / 2 + 38);
                boardOverlayKeypoints = [];
                boardOverlay.style.display = 'flex';
                void boardOverlay.offsetWidth;
                boardOverlay.classList.add('board-active');
            }

            // 模型"蹲下→伸手出屏幕→站起来"通用表演
            // direction: 'up'(黑板从上方) / 'down'(终端从下方) / 'left'(图片从左)
            // 窗口动画与其并行播放：蹲下时窗口缩在屏幕外，伸手时窗口开始升起放大，站起时窗口放大到位
            // ---------- 模型手臂参数驱动：真"伸手"（比自带动作幅度大、方向可控） ----------
            let _armPoseParam = null;    // 扫描到的手臂/手参数 id 映射
            let _armPoseTicker = null;   // 伸手期间每帧强制覆盖参数的 ticker（防止被 idle 动作覆盖）

            // 模型加载后扫描参数 id，匹配手臂/手部参数（定制模型参数名可能被数字化，故运行时探测）
            function scanModelArmParams() {
                if (!live2dModel || !live2dModel.internalModel) return null;
                try {
                    const cm = live2dModel.internalModel.coreModel;
                    const raw = cm && cm._model && cm._model.parameters && cm._model.parameters.ids;
                    if (!raw || !raw.length) return null;
                    const ids = Array.prototype.slice.call(raw);
                    console.log('[MODEL] 参数列表(' + ids.length + '): ' + ids.join(', '));
                    const has = function(p) { return ids.indexOf(p) >= 0; };
                    _armPoseParam = {
                        rY: has('ParamArmRAngleY') ? 'ParamArmRAngleY' : null,
                        lY: has('ParamArmLAngleY') ? 'ParamArmLAngleY' : null,
                        rZ: has('ParamArmRAngleZ') ? 'ParamArmRAngleZ' : null,
                        lZ: has('ParamArmLAngleZ') ? 'ParamArmLAngleZ' : null,
                        rHand: has('ParamHandROpen') ? 'ParamHandROpen' : null,
                        lHand: has('ParamHandLOpen') ? 'ParamHandLOpen' : null,
                        rBend: has('ParamArmRBend') ? 'ParamArmRBend' : null,
                        lBend: has('ParamArmLBend') ? 'ParamArmLBend' : null,
                    };
                    console.log('[MODEL] 手臂参数映射:', JSON.stringify(_armPoseParam));
                    return _armPoseParam;
                } catch (e) {
                    console.warn('[MODEL] 参数扫描失败', e);
                    return null;
                }
            }

            function _armSet(id, v) {
                if (!id || !live2dModel || !live2dModel.internalModel) return;
                try { live2dModel.internalModel.coreModel.setParameterValueById(id, v, 1); } catch (e) {}
            }
            function _armMax(id) {
                if (!id) return 30;
                try {
                    const cm = live2dModel.internalModel.coreModel;
                    return cm.getParameterMaximumValue(cm.getParameterIndex(id));
                } catch (e) { return 30; }
            }
            function _armDef(id) {
                if (!id) return 0;
                try {
                    const cm = live2dModel.internalModel.coreModel;
                    return cm.getParameterDefaultValue(cm.getParameterIndex(id));
                } catch (e) { return 0; }
            }

            // 开始"伸手"姿态：双手向前伸出 + 手掌张开（ticker 每帧强制，动作/idle 无法覆盖）
            function startArmReach() {
                if (!_armPoseParam || !live2dModel || !live2dApp) return false;
                const p = _armPoseParam;
                if (!p.rY && !p.lY && !p.rHand && !p.lHand) return false;
                const rMax = _armMax(p.rY), lMax = _armMax(p.lY);
                const rhMax = _armMax(p.rHand), lhMax = _armMax(p.lHand);
                const apply = function() {
                    _armSet(p.rY, rMax);
                    _armSet(p.lY, lMax);
                    _armSet(p.rHand, rhMax);
                    _armSet(p.lHand, lhMax);
                };
                apply();
                _armPoseTicker = live2dApp.ticker.add(apply);
                console.log('[MODEL] 开始伸手姿态（手臂参数驱动）');
                return true;
            }

            // 结束"伸手"：停止每帧覆盖，恢复手臂/手参数到默认值
            function stopArmReach() {
                if (_armPoseTicker) {
                    try { live2dApp.ticker.remove(_armPoseTicker); } catch (e) {}
                    _armPoseTicker = null;
                }
                if (_armPoseParam) {
                    const p = _armPoseParam;
                    _armSet(p.rY, _armDef(p.rY)); _armSet(p.lY, _armDef(p.lY));
                    _armSet(p.rZ, _armDef(p.rZ)); _armSet(p.lZ, _armDef(p.lZ));
                    _armSet(p.rHand, _armDef(p.rHand)); _armSet(p.lHand, _armDef(p.lHand));
                    _armSet(p.rBend, _armDef(p.rBend)); _armSet(p.lBend, _armDef(p.lBend));
                }
            }

            function modelReachPanel(direction) {
                if (!live2dModel || !live2dApp) {
                    // 模型未就绪时退化为直接播放动作
                    const fallback = direction === 'down' ? 'think' : 'hello';
                    if (typeof triggerAction === 'function') {
                        try { triggerAction(fallback); } catch (e) {}
                    }
                    return Promise.resolve();
                }
                const h = live2dApp.screen.height;
                const baseX = live2dModel.x;
                const baseY = live2dModel.y;
                const baseS = live2dModel.scale.x;
                return (async function() {
                    // 1) 蹲下：大幅下移（不缩放模型，保持自然体态），像探身去够屏幕外的东西
                    await tweenModelTo(baseX, baseY + h * 0.20, baseS, 320);
                    // 2) 伸手：优先用参数直接驱动手臂（幅度大、不被动作覆盖）；参数不可用才回退 hello 动作
                    const reached = startArmReach();
                    if (!reached && typeof triggerAction === 'function') {
                        try { triggerAction('hello'); } catch (e) { console.warn('triggerAction 失败:', e); }
                    }
                    console.log('[MODEL] %s 方向伸手（参数驱动=%s）', direction, reached);
                    if (direction === 'down') {
                        // 终端在下方：手再往下探，贴近屏幕底部边缘
                        await tweenModelTo(baseX, baseY + h * 0.26, baseS, 200);
                    }
                    // 3) 伸手动画结束后稍微停顿：保持低位伸手姿态，让"手悬在窗口位置、窗口被拉出"的瞬间被看清
                    await new Promise(function(r) { setTimeout(r, 700); });
                    // 4) 收回手，跟随窗口一起站起来：恢复原位（窗口放大到 100% 的后半段同步）
                    stopArmReach();
                    await tweenModelTo(baseX, baseY, baseS, 520);
                })();
            }

            // 重写 showBoard：始终显示面板，并先让模型"蹲下→向上伸手→站起来"再放下黑板
            async function showBoard(url) {
                if (boardOverlay.classList.contains('board-active')) return;
                // 1) 模型表演（不 await，窗口动画并行播放）
                const p = modelReachPanel('up');
                // 2) 等模型蹲下伸手到位（~400ms），再加载黑板并从上方先缩小再拉下放大
                setTimeout(function() {
                    const loadUrl = url || null;
                    if (loadUrl) {
                        renderBoardOverlay(loadUrl);
                    } else if (!currentLesson) {
                        addBubble('请先选择课程再使用 /board', 'teacher');
                        renderEmptyBoardOverlay();
                    } else {
                        fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/board')
                            .then(r => r.json())
                            .then(data => {
                                if (data.board_url) renderBoardOverlay(data.board_url);
                                else                  renderEmptyBoardOverlay();
                            })
                            .catch(() => renderEmptyBoardOverlay());
                    }
                }, 400);
                // 3) 表演完成后，角色眼神看向上方黑板
                await p;
                if (typeof live2dModel !== 'undefined' && live2dModel && typeof live2dModel.focus === 'function') {
                    try { live2dModel.focus(0.0, 0.7); } catch (e) {}
                }
            }

            // 暴露到全局控制台
            window.showBoard = showBoard;
            window.hideBoard = hideBoard;
            window.renderEmptyBoardOverlay = renderEmptyBoardOverlay;

            boardOverlayClose.addEventListener('click', hideBoard);

            // ============================
            // 课程进度条：显示「当前单元 X / 总单元 Y + 单元名」
            // ============================
            const unitProgressBar     = document.getElementById('unit-progress-bar');
            const unitProgressCur     = document.getElementById('unit-progress-current');
            const unitProgressTotal   = document.getElementById('unit-progress-total');
            const unitProgressTitle   = document.getElementById('unit-progress-title');
            const unitProgressFill    = document.getElementById('unit-progress-fill');

            function updateUnitProgress(meta) {
                if (!meta) {
                    unitProgressBar.style.display = 'none';
                    updateTopbarProgress(null, null, null);
                    return;
                }
                const units = meta.units || [];
                if (!units.length) {
                    unitProgressBar.style.display = 'none';
                    updateTopbarProgress(null, null, null);
                    return;
                }
                const curIdx = Math.max(0, Math.min((meta.current_unit || 0), units.length - 1));
                const total = units.length;
                const title = (units[curIdx] && units[curIdx].title) || meta.topic || '当前单元';
                const pct = (((curIdx + 1) / total) * 100).toFixed(1) + '%';
                unitProgressCur.textContent   = (curIdx + 1);
                unitProgressTotal.textContent = total;
                unitProgressTitle.textContent = title;
                unitProgressFill.style.width  = pct;
                unitProgressBar.style.display = 'flex';
                // 同步到顶部状态栏（v2）
                updateTopbarProgress(curIdx + 1, total, title, pct);
            }

            // === Topbar 进度更新（v2 新增） ===
            // DOM 引用已在 IIFE 顶部声明（避免 TDZ）
            function updateTopbarProgress(cur, total, title, pct) {
                if (cur == null || total == null || !total) {
                    topbarProgressCurrent.textContent = '—';
                    topbarProgressTotal.textContent = '—';
                    topbarProgressTitle.textContent = title || '—';
                    topbarProgressFill.style.width = '0%';
                    return;
                }
                topbarProgressCurrent.textContent = cur;
                topbarProgressTotal.textContent = total;
                if (title) topbarProgressTitle.textContent = title;
                if (pct) topbarProgressFill.style.width = pct;
            }

            // === Topbar 课程名更新 ===
            function updateTopbarCourseName(name) {
                if (topbarCourseText) topbarCourseText.textContent = name || '未选择课程';
            }

            // ============================
            // 终端面板（/terminal 触发，模型从上往下"拉下来"）
            // ============================
            const terminalOverlay       = document.getElementById('terminal-overlay');
            const terminalOverlayClose  = document.getElementById('terminal-overlay-close');
            const terminalOutput        = document.getElementById('terminal-output');
            const terminalInput         = document.getElementById('terminal-input');
            const terminalLang          = document.getElementById('terminal-lang');
            const terminalRunBtn        = document.getElementById('terminal-run');
            let replWaiting = false;  // Python REPL 是否在等续行（如冒号块/括号未闭合）

            // 交互式终端：把命令发给后端持久 REPL 会话，展示输出，并把源代码+输出写入对话供 AI 读取
            async function runTerminalCode(code, language, recordToAI) {
                if (terminalRunBtn) terminalRunBtn.disabled = true;
                const lang = (language || (terminalLang ? terminalLang.value : 'python') || 'python').toLowerCase();
                // 空输入：仅当 Python REPL 正在等续行时作为"空行结束块"发送；否则提示
                if (!code && !(replWaiting && lang === 'python')) {
                    appendTerminal('⚠️ 请输入要执行的代码', 'term-stderr');
                    if (terminalRunBtn) terminalRunBtn.disabled = false;
                    return null;
                }
                // REPL 回显：正在等续行时用 ...，否则 python/javascript 用 >>>，shell 用 $
                const promptCh = replWaiting ? '...' : ((lang === 'python' || lang === 'javascript' || lang === 'node') ? '>>>' : '$');
                // 多行输入逐行回显：首行主提示符，后续行续行提示符
                String(code || '').split('\n').forEach(function(ln, i) {
                    appendTerminal((i === 0 ? promptCh : '...') + ' ' + ln.slice(0, 80), 'term-prompt');
                });
                try {
                    // 交互式终端：发送给持久 REPL 会话（变量/cd 状态连续）
                    const resp = await fetch('/api/terminal/command', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ lesson_folder: currentLesson || '', language: lang, cmd: code }),
                    });
                    const data = await resp.json();
                    let output = '';
                    if (!data.ok) {
                        appendTerminal('❌ ' + (data.error || '执行失败'), 'term-stderr');
                        replWaiting = false;
                    } else {
                        output = (data.output || '').trim();
                        if (output) appendTerminal(output, 'term-stdout');
                        replWaiting = !!data.continuation;
                        if (replWaiting) appendTerminal('...', 'term-prompt');
                    }
                    // 记录给 AI（仅在有课程且非纯展示时写入，供 AI 后续读取分析）
                    if (recordToAI !== false && currentLesson) {
                        fetch('/api/terminal_record', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lesson_folder: currentLesson,
                                language: lang,
                                code: code,
                                stdout: output,
                                stderr: data.ok ? '' : (data.error || ''),
                            }),
                        }).catch(function() {});
                    }
                    return data;
                } catch (e) {
                    appendTerminal('❌ 请求失败: ' + e.message, 'term-stderr');
                    return null;
                } finally {
                    if (terminalRunBtn) terminalRunBtn.disabled = false;
                }
            }
            window.runTerminalCode = runTerminalCode;

            // 绑定输入框 / 运行按钮事件（textarea：Enter 提交、Ctrl+Enter 换行、内容自动增高）
            function bindTerminalInputEvents() {
                if (!terminalInput || !terminalRunBtn) return;
                function autoGrowInput() {
                    terminalInput.style.height = 'auto';
                    terminalInput.style.height = Math.min(terminalInput.scrollHeight, 150) + 'px';
                }
                function resetInputHeight() {
                    terminalInput.style.height = '';
                }
                function submitTerminal() {
                    runTerminalCode(terminalInput.value);
                    terminalInput.value = '';
                    resetInputHeight();
                }
                function insertNewlineAtCursor() {
                    const s = terminalInput.selectionStart, e = terminalInput.selectionEnd;
                    const v = terminalInput.value;
                    terminalInput.value = v.slice(0, s) + '\n' + v.slice(e);
                    const pos = s + 1;
                    terminalInput.selectionStart = terminalInput.selectionEnd = pos;
                    autoGrowInput();
                }
                terminalInput.addEventListener('keydown', function(e) {
                    if (e.key !== 'Enter') return;
                    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) {
                        // Ctrl+Enter 换行（Shift+Enter 沿用 textarea 默认换行）
                        if (e.ctrlKey || e.metaKey) {
                            e.preventDefault();
                            insertNewlineAtCursor();
                        }
                        return;
                    }
                    e.preventDefault();
                    submitTerminal();
                });
                terminalInput.addEventListener('input', autoGrowInput);
                terminalRunBtn.addEventListener('click', function() {
                    submitTerminal();
                });
                // 切换语言时重置续行状态
                terminalLang.addEventListener('change', function() {
                    replWaiting = false;
                });
            }
            bindTerminalInputEvents();

            function appendTerminal(text, cls) {
                cls = cls || 'term-stdout';
                // 仅在用户接近底部时才自动跟随滚动（向上翻历史时不被拉走）
                const nearBottom = terminalOutput.scrollTop + terminalOutput.clientHeight >= terminalOutput.scrollHeight - 40;
                const lines = String(text).split(/\r?\n/);
                lines.forEach(function(line) {
                    const el = document.createElement('div');
                    el.className = 'term-line ' + cls;
                    el.textContent = line;
                    terminalOutput.appendChild(el);
                });
                if (nearBottom) terminalOutput.scrollTop = terminalOutput.scrollHeight;
            }

            // 让终端窗口的垂直中心对准模型手部的位置（蹲下伸手时，手正对窗口中间）
            function alignTerminalToHand() {
                if (!terminalOverlay || !live2dModel || !live2dApp) return;
                const parent = terminalOverlay.parentElement;
                if (!parent) return;
                const containerH = parent.clientHeight;
                if (!containerH || !live2dApp.screen.height) return;
                // 坐标换算：模型坐标(逻辑px) → 容器 CSS px
                const k = containerH / live2dApp.screen.height;
                // 手约在模型中心上方 15% 模型高度处（肩/手部区域）
                const handY = (live2dModel.y - live2dModel.height * 0.15) * k;
                // 窗口默认高度 = 容器高 - (top56 + bottom140)
                const initWinH = containerH - 196;
                let top = handY - initWinH / 2;
                top = Math.max(10, Math.min(top, containerH - initWinH - 10));
                terminalOverlay.style.top = top + 'px';
                console.log('[TERMINAL] 窗口中心对准手部 y=' + Math.round(handY) + 'px, top=' + Math.round(top) + 'px');
            }

            async function showTerminal(opts) {
                opts = opts || {};
                replWaiting = false;  // 重新打开时重置续行状态
                // 隐藏其它面板避免重叠
                hideBoard();
                hideImagePanel();
                // 若欢迎页仍显示，先收起（欢迎页全屏覆盖会拦截滚轮/点击，导致终端无法滚动）
                if (menuScreen && !menuScreen.classList.contains('hidden')) hideMenu();

                // 写入内容（先准备好，面板滑入时直接展示；autoRun 时交给 runTerminalCode 回显，避免重复）
                terminalOutput.innerHTML = '';
                if (opts.language && !opts.autoRun) appendTerminal('$ ' + opts.language + ' ' + (opts.code || '').split('\n')[0], 'term-info');
                if (opts.code && !opts.autoRun)    appendTerminal(opts.code, 'term-stdout');
                if (opts.stdout)  appendTerminal(opts.stdout, 'term-stdout');
                if (opts.stderr)  appendTerminal(opts.stderr, 'term-stderr');
                if (!opts.code && !opts.stdout && !opts.stderr) {
                    appendTerminal('交互式终端已就绪。直接在下方输入命令（Enter 运行），变量和状态会保留。', 'term-info');
                }

                // 1) 模型"蹲下→向下伸手→站起来"（窗口动画并行）
                const p = modelReachPanel('down');
                // 2) 等模型蹲下伸手到位（~400ms），终端从屏幕下方先缩小再升起放大
                setTimeout(function() {
                    // 窗口中心对准模型手部位置（此时模型已蹲下伸手）
                    alignTerminalToHand();
                    terminalOverlay.classList.remove('terminal-active', 'terminal-closing');
                    void terminalOverlay.offsetWidth;
                    terminalOverlay.classList.add('terminal-active');
                    // AI 给的代码自动执行（如 [TOOL:show_terminal]）
                    if (opts.autoRun && opts.code) {
                        runTerminalCode(opts.code, opts.language);
                    }
                    // 焦点到输入框，方便直接输入
                    if (terminalInput) { try { terminalInput.focus(); } catch (e) {} }
                    console.log('[TERMINAL] 从下方拉出 + 角色动作');
                }, 400);
                // 3) 表演完成后，角色眼神看向下方终端
                await p;
                if (typeof live2dModel !== 'undefined' && live2dModel && typeof live2dModel.focus === 'function') {
                    try { live2dModel.focus(0.0, -0.4); } catch (e) {}
                }
            }

            function appendTerminalOutput(stream, text) {
                appendTerminal(text, stream === 'stderr' ? 'term-stderr' : 'term-stdout');
                // 如果面板未显示，自动浮现
                if (!terminalOverlay.classList.contains('terminal-active')) {
                    terminalOverlay.classList.remove('terminal-closing');
                    void terminalOverlay.offsetWidth;
                    terminalOverlay.classList.add('terminal-active');
                }
            }

            function hideTerminal() {
                if (!terminalOverlay.classList.contains('terminal-active')) return;
                terminalOverlay.classList.remove('terminal-active');
                // 拖动过：直接隐藏（不再播放收回动画，避免面板跳回原位再滑走）
                if (terminalOverlay.__dragged) {
                    resetDragState(terminalOverlay);
                    terminalOverlay.classList.remove('terminal-closing');
                    return;
                }
                void terminalOverlay.offsetWidth;
                terminalOverlay.classList.add('terminal-closing');
                setTimeout(function() {
                    terminalOverlay.classList.remove('terminal-closing');
                }, 650);
            }

            terminalOverlayClose.addEventListener('click', hideTerminal);
            window.showTerminal = showTerminal;
            window.appendTerminalOutput = appendTerminalOutput;
            window.hideTerminal = hideTerminal;

            // ============================
            // 图片面板（/image 触发，模型从左边"拉出来"）
            // ============================
            const imageOverlay       = document.getElementById('image-overlay');
            const imageOverlayTitle  = document.getElementById('image-overlay-title');
            const imageOverlayStage  = document.getElementById('image-overlay-stage');
            const imageOverlayThumbs = document.getElementById('image-overlay-thumbs');
            const imageOverlayMeta   = document.getElementById('image-overlay-meta');
            const imageOverlayClose  = document.getElementById('image-overlay-close');
            let currentImageList = [];   // 当前单元图片列表
            let currentImageIdx  = 0;

            function renderImageStage(item) {
                if (!item) {
                    imageOverlayStage.innerHTML = '<span class="image-empty">暂无图片</span>';
                    imageOverlayMeta.textContent = '';
                    return;
                }
                imageOverlayStage.innerHTML = '';
                const img = document.createElement('img');
                img.alt = item.title || item.filename;
                img.src = item.url;
                imageOverlayStage.appendChild(img);
                imageOverlayMeta.textContent = (item.title || item.filename) + ' · 来自 ' + (item.folder || 'images');
            }

            function renderImageThumbs(items, activeIdx) {
                imageOverlayThumbs.innerHTML = '';
                items.forEach(function(it, i) {
                    const t = document.createElement('img');
                    t.className = 'image-thumb' + (i === activeIdx ? ' active' : '');
                    t.src = it.url;
                    t.title = it.title || it.filename;
                    t.addEventListener('click', function() {
                        currentImageIdx = i;
                        renderImageStage(it);
                        Array.prototype.forEach.call(imageOverlayThumbs.children, function(c) { c.classList.remove('active'); });
                        t.classList.add('active');
                    });
                    imageOverlayThumbs.appendChild(t);
                });
            }

            async function showImagePanel(opts) {
                opts = opts || {};
                // 隐藏其它面板避免重叠
                hideBoard();
                hideTerminal();
                if (imageOverlay.classList.contains('image-active')) return;
                if (!currentLesson) {
                    addBubble('请先选择课程再使用 /image', 'teacher');
                    return;
                }
                // 1) 模型"蹲下→向左伸手→站起来"（窗口动画并行）
                const p = modelReachPanel('left');
                // 2) 等模型伸手到位（~400ms），图片从左侧先缩小再拉出放大
                setTimeout(function() {
                    fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/unit-images')
                        .then(r => r.json())
                        .then(data => {
                            if (!data.ok) { addBubble('❌ 加载图片失败', 'teacher'); return; }
                            currentImageList = data.images || [];
                            imageOverlayTitle.textContent = '🖼️ 单元图片 · ' + (data.unit_title || ('单元 ' + ((data.unit_index || 0) + 1)));
                            if (!currentImageList.length) {
                                renderImageStage(null);
                                imageOverlayMeta.textContent = '当前单元下暂无图片资源。可把 PNG/JPG 放进 ' + currentLesson + '/images/ 目录。';
                            } else {
                                // 优先按 filename 精确匹配（AI 传"图片路径"时），否则按 index，兜底第一张
                                let targetIdx = 0;
                                if (opts.filename) {
                                    const byName = currentImageList.findIndex(it => it.filename === opts.filename || it.title === opts.filename);
                                    if (byName >= 0) targetIdx = byName;
                                }
                                if (opts.index != null) {
                                    targetIdx = Math.max(0, Math.min(opts.index, currentImageList.length - 1));
                                }
                                currentImageIdx = targetIdx;
                                renderImageStage(currentImageList[currentImageIdx]);
                                renderImageThumbs(currentImageList, currentImageIdx);
                            }
                            imageOverlay.style.display = 'flex';
                            void imageOverlay.offsetWidth;
                            imageOverlay.classList.add('image-active');
                        })
                        .catch(err => addBubble('❌ /image 失败: ' + err.message, 'teacher'));
                }, 300);
                // 3) 表演完成后，角色眼神看向左方图片
                await p;
                if (typeof live2dModel !== 'undefined' && live2dModel && typeof live2dModel.focus === 'function') {
                    try { live2dModel.focus(-0.7, 0.0); } catch (e) {}
                }
            }

            function hideImagePanel() {
                imageOverlay.classList.remove('image-active');
                // 拖动过：直接隐藏
                if (imageOverlay.__dragged) {
                    resetDragState(imageOverlay);
                    imageOverlay.style.display = 'none';
                    return;
                }
                imageOverlay.classList.add('image-closing');
                setTimeout(function() {
                    if (!imageOverlay.classList.contains('image-active')) {
                        imageOverlay.classList.remove('image-closing');
                        imageOverlay.style.display = 'none';
                    }
                }, 500);
            }

            imageOverlayClose.addEventListener('click', hideImagePanel);
            window.showImagePanel = showImagePanel;
            window.hideImagePanel = hideImagePanel;

            // ============================
            // 终端 / 图片弹窗拖动（按住标题栏拖动，保持拖动后位置）
            // ============================
            function makeDraggable(el, handle, opts) {
                opts = opts || {};
                let dragging = false;
                let startX = 0, startY = 0;
                let baseLeft = 0, baseTop = 0;
                handle.addEventListener('pointerdown', function(e) {
                    if (e.button !== 0 || e.target.closest('button')) return;
                    const rect = el.getBoundingClientRect();
                    dragging = true;
                    startX = e.clientX;
                    startY = e.clientY;
                    baseLeft = rect.left;
                    baseTop = rect.top;
                    el.__dragged = true;
                    // 掐掉动画/过渡（动画 forwards 填充和内联定位冲突），改用固定 left/top 保持当前位置
                    el.style.setProperty('animation', 'none', 'important');
                    el.style.setProperty('transition', 'none', 'important');
                    el.style.setProperty('transform', 'translate(0, 0) scale(' + (opts.scale || 1) + ')', 'important');
                    el.style.setProperty('left', baseLeft + 'px', 'important');
                    el.style.setProperty('top', baseTop + 'px', 'important');
                    el.classList.add('dragging');
                    document.body.style.userSelect = 'none';
                    try { handle.setPointerCapture(e.pointerId); } catch (err) {}
                    e.preventDefault();
                });
                handle.addEventListener('pointermove', function(e) {
                    if (!dragging) return;
                    const dx = e.clientX - startX;
                    const dy = e.clientY - startY;
                    let left = baseLeft + dx;
                    let top = baseTop + dy;
                    // 基本限制：左缘最多拖出 60px；右缘始终保留在视口内（保证 ✕ 关闭按钮可点）
                    const rw = el.offsetWidth;
                    const rh = el.offsetHeight;
                    left = Math.max(-rw + 60, Math.min(left, window.innerWidth - rw - 8));
                    top = Math.max(0, Math.min(top, window.innerHeight - 40));
                    el.style.setProperty('left', left + 'px', 'important');
                    el.style.setProperty('top', top + 'px', 'important');
                });
                function endDrag(e) {
                    if (!dragging) return;
                    dragging = false;
                    el.classList.remove('dragging');
                    document.body.style.userSelect = '';
                    el.style.removeProperty('transition');  // 恢复过渡；位置/动画保持拖动后的状态
                    if (handle.releasePointerCapture) { try { handle.releasePointerCapture(e.pointerId); } catch (err) {} }
                }
                handle.addEventListener('pointerup', endDrag);
                handle.addEventListener('pointercancel', endDrag);
            }

            // 清除拖动留下的内联定位/动画，恢复 CSS 默认居中（隐藏时调用）
            function resetDragState(el) {
                el.__dragged = false;
                el.style.removeProperty('left');
                el.style.removeProperty('top');
                el.style.removeProperty('transform');
                el.style.removeProperty('animation');
                el.style.removeProperty('transition');
                el.classList.remove('dragging');
            }

            makeDraggable(terminalOverlay, document.querySelector('#terminal-overlay .terminal-overlay-head'), { scale: 0.85 });
            makeDraggable(imageOverlay, document.querySelector('#image-overlay .image-overlay-head'), { scale: 1 });
