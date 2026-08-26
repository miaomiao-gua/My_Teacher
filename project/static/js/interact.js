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
                dashboard:'hello',  // 仪表盘：打招呼后展示
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
                if (viewName === 'settings') if (typeof loadStudentProfileBox === 'function') loadStudentProfileBox();
                if (viewName === 'exam')     if (typeof loadStudyStats === 'function') loadStudyStats();
                if (viewName === 'dashboard') if (typeof loadDashboard === 'function') loadDashboard();

                // 6) 设置面板激活时给右侧 sidebar 加 class，隐藏 chat/lesson/exam/resource tab 头部
                const sidebar = document.getElementById('chat-sidebar');
                if (sidebar) {
                    if (viewName === 'settings') sidebar.classList.add('settings-active');
                    else                          sidebar.classList.remove('settings-active');
                }
            }

            function panelHandHide(viewName) {
                const el = viewContents[viewName];
                if (!el) return;
                el.classList.remove('active', 'view-closing');
                navBtns.forEach(function(b) { if (b.dataset.view === viewName) b.classList.remove('active'); });
                // settings 关闭时移除 settings-active class
                const sidebar = document.getElementById('chat-sidebar');
                if (sidebar && viewName === 'settings') sidebar.classList.remove('settings-active');
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
                    // 全屏设置模式：关闭即返回首页（showMenu 会同时移除 app-settings-mode）
                    const appEl = document.getElementById('app');
                    if (v === 'settings' && appEl && appEl.classList.contains('app-settings-mode')) {
                        showMenu();
                    }
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
                // 退出全屏设置模式（从首页进入设置时加的 class）
                const appEl = document.getElementById('app');
                if (appEl) appEl.classList.remove('app-settings-mode');
                if (menuScreen) menuScreen.classList.remove('hidden');
                renderMenuLessons();
            }
            function hideMenu() {
                menuScreen.classList.add('hidden');
                // Live2D 延迟初始化：菜单关闭后再创建 WebGL 上下文（initLive2D 有防重，只会执行一次）
                if (typeof window.initLive2D === 'function') {
                    try { window.initLive2D(); } catch (e) { console.warn('Live2D init after menu hide:', e); }
                }
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
                                    <button class="m-export-btn" title="导出备份">⬇</button>
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
                        document.querySelectorAll('.menu-lesson-card .m-export-btn').forEach(btn => {
                            btn.addEventListener('click', function(e) {
                                e.stopPropagation();
                                const name = this.closest('.menu-lesson-card').dataset.name;
                                const url = '/api/lessons/' + encodeURIComponent(name) + '/export';
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = '';
                                document.body.appendChild(a);
                                a.click();
                                a.remove();
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
                    if (typeof refreshExamPaperInfo === 'function') refreshExamPaperInfo();
                    const history = data.conversation || data.history || [];
                    if (history.length) {
                        // 性能优化：用 DocumentFragment 批量插入，避免逐条 innerHTML 触发多次重排
                        const frag = document.createDocumentFragment();
                        const teacherBubbles = []; // 收集 teacher 气泡，最后一次性 renderMarkdown
                        history.forEach(msg => {
                            // 终端执行记录只作为 AI 上下文，不显示为聊天气泡
                            if (msg.content && String(msg.content).indexOf('[终端执行记录]') === 0) return;
                            // 历史渲染同样过 cleanSeg：旧存档可能残留 \c 分段符 / 标签 / 孤立围栏
                            const text = msg.role === 'assistant' ? cleanSeg(msg.content) : msg.content;
                            const bubble = addBubble(text, msg.role === 'user' ? 'user' : 'teacher', { batch: true });
                            frag.appendChild(bubble);
                            if (msg.role === 'assistant') teacherBubbles.push(bubble);
                        });
                        conversation.innerHTML = '';
                        conversation.appendChild(frag);
                        // 一次性批量渲染 markdown（marked 解析每条都比逐条 + DOM 插入更省）
                        if (teacherBubbles.length) {
                            requestAnimationFrame(() => {
                                teacherBubbles.forEach(b => { b.innerHTML = renderMarkdown(b.textContent); });
                                conversation.scrollTop = conversation.scrollHeight;
                            });
                        }
                    } else {
                        // 首次进入该单元（对话历史为空）：AI 主动开场讲解基础知识点
                        conversation.innerHTML = '';
                        playUnitWelcome(name);
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

            // 首次进入单元：AI 主动开场讲解本课基础知识点（后端 /api/unit/welcome 流式 SSE）
            function playUnitWelcome(name) {
                addBubble('👩‍🏫 欢迎来到本课，让我先讲讲这节课的基础知识……', 'teacher');
                const bubble = addBubble('', 'teacher');
                // 复用 Galgame 对话条逐段播放
                dialogueSegments = [];
                dialogueSegIdx = -1;
                _previewBuf = '';
                dialogueStreaming = true;
                dialogueBar.style.display = 'block';
                dialogueContent.textContent = '……';
                dialogueContent.classList.remove('type-caret');
                dialogueIndicator.textContent = '老师思考中';
                dialogueIndicator.classList.remove('hidden');
                fetch('/api/unit/welcome', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lesson_folder: name })
                }).then(function(response) {
                    const ct = response.headers.get('content-type') || '';
                    // 已欢迎过（already:true）或出错：后端返回普通 JSON
                    if (ct.indexOf('text/event-stream') === -1) {
                        return response.json().then(function(data) {
                            dialogueStreaming = false;
                            dialogueBar.style.display = 'none';
                            if (data.already) {
                                bubble.remove();
                                conversation.querySelectorAll('.bubble').forEach(b => b.remove());
                                return;
                            }
                            bubble.textContent = '❌ ' + (data.error || '开场讲解失败');
                            return;
                        });
                    }
                    if (!response.body) throw new Error('No response body');
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let fullText = '';
                    function read() {
                        reader.read().then(function(ret) {
                            if (ret.done) {
                                bubble.innerHTML = renderMarkdown(fullText);
                                conversation.scrollTop = conversation.scrollHeight;
                                dialogueStreaming = false;
                                dialogueBar.style.display = 'none';
                                // 对话条逐段打字机播放
                                startGalgamePlayback();
                                return;
                            }
                            const chunk = decoder.decode(ret.value);
                            const lines = chunk.split('\n');
                            for (const line of lines) {
                                if (!line.startsWith('data: ')) continue;
                                const payload = line.replace('data: ', '').trim();
                                if (payload === '[DONE]') continue;
                                try {
                                    const data = JSON.parse(payload);
                                    // preview 帧：生成过程中的 token。不再实时刷新气泡正文——高频 DOM 写入
                                    // 会造成"先卡顿过一遍" + 前端卡顿；内容统一由 content 帧 + 完成后的
                                    // 逐段播放呈现（气泡保持"老师思考中"提示）。
                                    if (data.preview && !data.done) {
                                        _previewBuf += data.preview;   // 仅累积（备用），不写入 DOM
                                    }
                                    if (data.content && !data.done) {
                                        const seg = data.segment !== undefined ? data.segment : 0;
                                        dialogueSegments[seg] = cleanSeg(data.content);
                                        fullText = dialogueSegments.filter(s => s).join('\n\n');
                                        const nearBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 80;
                                        if (nearBottom) conversation.scrollTop = conversation.scrollHeight;
                                    }
                                    if (data.done && data.content) {
                                        fullText = cleanSeg(data.content);
                                        bubble.innerHTML = renderMarkdown(fullText);
                                        conversation.scrollTop = conversation.scrollHeight;
                                    }
                                } catch (e) {}
                            }
                            read();
                        }).catch(function(err) {
                            console.error('[welcome] stream error:', err);
                            bubble.textContent = '❌ ' + err.message;
                            dialogueStreaming = false;
                            dialogueBar.style.display = 'none';
                        });
                    }
                    read();
                }).catch(function(err) {
                    console.error('[welcome] fetch error:', err);
                    bubble.textContent = '❌ ' + err.message;
                    dialogueStreaming = false;
                    dialogueBar.style.display = 'none';
                });
            }

            // 创建课程（拆成两种独立方法，均为独立 modal 弹窗）
            //  - 方法一「✨ 一句话备课」：弹出独立 modal，输入主题，立即触发备课
            //  - 方法二「📚 导入课件备课」：弹出独立 modal，选课件文件 + 输入主题，AI 转 MD 后拆分单元
            const menuCreateFiles = document.getElementById('menu-create-files');
            const menuCreateFilesHint = document.getElementById('menu-create-files-hint');
            const menuCreateImportBtn = document.getElementById('menu-create-import-btn');
            const quickPrepModal = document.getElementById('quick-prep-modal');
            const importPrepModal = document.getElementById('import-prep-modal');
            const quickPrepTopic = document.getElementById('quick-prep-topic');
            const quickPrepStart = document.getElementById('quick-prep-start');
            const quickPrepCancel = document.getElementById('quick-prep-cancel');
            const menuImportTopic = document.getElementById('menu-import-topic');
            const menuImportStartBtn = document.getElementById('menu-import-start-btn');
            const menuImportCancelBtn = document.getElementById('menu-import-cancel-btn');

            // 通用 modal 开关
            function openModal(modal) {
                if (!modal) return;
                modal.classList.add('active');
            }
            function closeModal(modal) {
                if (!modal) return;
                modal.classList.remove('active');
            }
            // Esc 关闭当前打开的备课 modal
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                    if (quickPrepModal && quickPrepModal.classList.contains('active')) closeModal(quickPrepModal);
                    if (importPrepModal && importPrepModal.classList.contains('active')) closeModal(importPrepModal);
                }
            });

            // 文件选择提示
            if (menuCreateFiles && menuCreateFilesHint) {
                menuCreateFiles.addEventListener('change', function() {
                    const names = Array.from(this.files || []).map(function(f) { return f.name; });
                    menuCreateFilesHint.textContent = names.length
                        ? ('已选择 ' + names.length + ' 个文件：' + names.join('、'))
                        : '未选择文件';
                });
            }

            // 方法一：✨ 一句话备课
            menuCreateBtn.addEventListener('click', function() {
                if (quickPrepTopic) {
                    quickPrepTopic.value = '';
                    setTimeout(function() { quickPrepTopic.focus(); }, 80);
                }
                openModal(quickPrepModal);
            });
            if (quickPrepCancel) {
                quickPrepCancel.addEventListener('click', function() { closeModal(quickPrepModal); });
            }
            // 顶部 ✕ 关闭按钮
            document.querySelectorAll('[data-close-quick-prep]').forEach(function(btn) {
                btn.addEventListener('click', function() { closeModal(quickPrepModal); });
            });
            // 点遮罩关闭
            if (quickPrepModal) {
                quickPrepModal.addEventListener('click', function(e) {
                    if (e.target === quickPrepModal) closeModal(quickPrepModal);
                });
            }
            if (quickPrepStart) {
                quickPrepStart.addEventListener('click', function() {
                    const topic = quickPrepTopic ? quickPrepTopic.value.trim() : '';
                    if (!topic) { alert('请先输入课程主题'); return; }
                    closeModal(quickPrepModal);
                    prepareAndEnter(topic, []);
                });
                // 回车直接提交
                if (quickPrepTopic) {
                    quickPrepTopic.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter') { e.preventDefault(); quickPrepStart.click(); }
                    });
                }
            }

            // 方法二：📚 导入课件备课
            if (menuCreateImportBtn) {
                menuCreateImportBtn.addEventListener('click', function() {
                    if (menuImportTopic) menuImportTopic.value = '';
                    if (menuCreateFiles) menuCreateFiles.value = '';
                    if (menuCreateFilesHint) menuCreateFilesHint.textContent = '未选择文件';
                    openModal(importPrepModal);
                    if (menuImportTopic) setTimeout(function() { menuImportTopic.focus(); }, 80);
                });
            }
            if (menuImportCancelBtn) {
                menuImportCancelBtn.addEventListener('click', function() { closeModal(importPrepModal); });
            }
            document.querySelectorAll('[data-close-import-prep]').forEach(function(btn) {
                btn.addEventListener('click', function() { closeModal(importPrepModal); });
            });
            if (importPrepModal) {
                importPrepModal.addEventListener('click', function(e) {
                    if (e.target === importPrepModal) closeModal(importPrepModal);
                });
            }
            if (menuImportStartBtn) {
                menuImportStartBtn.addEventListener('click', function() {
                    const topic = menuImportTopic ? menuImportTopic.value.trim() : '';
                    const files = menuCreateFiles && menuCreateFiles.files ? Array.from(menuCreateFiles.files) : [];
                    if (!topic) { alert('请先输入课程主题'); return; }
                    if (!files.length) { alert('请先选择至少一个课件文件'); return; }
                    closeModal(importPrepModal);
                    prepareAndEnter(topic, files);
                });
                if (menuImportTopic) {
                    menuImportTopic.addEventListener('keydown', function(e) {
                        if (e.key === 'Enter') { e.preventDefault(); menuImportStartBtn.click(); }
                    });
                }
            }

            // AI 备课并进入课程（files：可选课程资料文件数组）
            function prepareAndEnter(topic, files) {
                // 显示备课中状态（使用专用 loading overlay）
                var loadingOverlay = document.getElementById('menu-loading-overlay');
                var loadingText = document.getElementById('menu-loading-text');
                var loadingSub = document.getElementById('menu-loading-sub');
                var loadingModel = document.getElementById('menu-loading-model');
                if (loadingText) loadingText.textContent = '⏳ 正在备课「' + topic + '」…';
                if (loadingSub) {
                    loadingSub.innerHTML = files && files.length
                        ? ('正在解析 ' + files.length + ' 个课程资料文件并由 AI 拆分为单元<br>云端生成完整教案通常需要 2~5 分钟，请耐心等待')
                        : 'AI 正在生成课程大纲、知识点与随堂测验<br>云端生成完整教案通常需要 2~5 分钟，请耐心等待';
                }
                // 展示当前备课所用的模型（从配置读取，完成后会显示实际使用的模型+token）
                if (loadingModel) loadingModel.textContent = '当前模型：获取中…';
                fetch('/api/config').then(r => r.json()).then(cfg => {
                    if (loadingModel) {
                        var m = (cfg.lesson_provider === 'ollama' ? cfg.ollama_model : cfg.cloud_model)
                            || cfg.cloud_model || cfg.ollama_model || '未知';
                        loadingModel.textContent = '当前模型：' + m;
                    }
                }).catch(() => {});
                if (loadingOverlay) loadingOverlay.classList.add('visible');
                menuCreateBtn.disabled = true;
                const originalText = menuCreateBtn.textContent;
                menuCreateBtn.textContent = '备课中…';
                // 每 60 秒更新一次等待提示，避免用户误以为卡死
                var waitStart = Date.now();
                var waitTimer = setInterval(function() {
                    var subEl = document.getElementById('menu-loading-sub');
                    var ovl = document.getElementById('menu-loading-overlay');
                    if (!subEl || !ovl || !ovl.classList.contains('visible')) { clearInterval(waitTimer); return; }
                    var mins = Math.floor((Date.now() - waitStart) / 60000);
                    subEl.innerHTML = '仍在生成中，已等待 ' + mins + ' 分钟<br>云端完整教案通常需要 2~5 分钟，请继续等待';
                }, 60000);

                let fetchOptions;
                if (files && files.length) {
                    const fd = new FormData();
                    fd.append('topic', topic);
                    files.forEach(function(f) { fd.append('files', f); });
                    fetchOptions = { method: 'POST', body: fd };
                } else {
                    fetchOptions = {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ topic: topic })
                    };
                }

                fetch('/api/prepare_lesson', fetchOptions)
                .then(r => r.json())
                .then(data => {
                    clearInterval(waitTimer);
                    var loadingOverlay = document.getElementById('menu-loading-overlay');
                    if (loadingOverlay) loadingOverlay.classList.remove('visible');
                    menuCreateBtn.disabled = false;
                    menuCreateBtn.textContent = originalText;
                    if (data.lesson_folder) {
                        var meta = data.prepared_meta || {};
                        if (meta.fallback) {
                            // 备课服务不可用，当前展示的是基础模板，必须明确告知用户
                            alert('⚠️ 备课服务不可用，已生成基础模板：' + (meta.reason || '未知原因') +
                                  '。\n可先到「设置」中检查 API Key / 本地 Ollama 状态，再重新备课。');
                        } else if (meta.warning) {
                            alert('提示：' + meta.warning);
                        }
                        _previewLessonFolder = data.lesson_folder;
                        _previewTopic = topic;
                        // 记录本次备课实际使用的模型与 token 用量，供预览弹窗展示
                        window._previewPrepMeta = data.prepared_meta || {};
                        showPreviewOverlay();
                        renderPreview(data.plan);
                    } else {
                        alert('备课失败: ' + (data.error || '未知错误'));
                        renderMenuLessons();
                    }
                }).catch(err => {
                    clearInterval(waitTimer);
                    var loadingOverlay = document.getElementById('menu-loading-overlay');
                    if (loadingOverlay) loadingOverlay.classList.remove('visible');
                    menuCreateBtn.disabled = false;
                    menuCreateBtn.textContent = originalText;
                    alert('备课失败: ' + err.message);
                    renderMenuLessons();
                });
            }

            // 首页 → 设置：进入独立设置界面（保留立绘布局，右侧为设置面板，每次进入都刷新配置）
            menuSettingsBtn.addEventListener('click', function() {
                hideMenu();
                const appEl = document.getElementById('app');
                if (appEl) appEl.classList.add('app-settings-mode');
                // 每次进入都重新拉取最新配置，避免残留上次的编辑/旧课程上下文
                if (typeof loadTeacherSettings === 'function') {
                    try { loadTeacherSettings(); } catch (e) { console.warn('刷新设置失败:', e); }
                }
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
                const rawMarker = segmentMarker ? (segmentMarker.value || '\\c') : '\\c';
                // 容错：config/设置面板可能把分段符存成 \\c（两个反斜杠），规约为 \c 再使用
                const marker = rawMarker.replace(/\\\\/g, '\\');
                return lines.map((l, i) =>
                    (l.trim().startsWith('```') && !paired.has(i)) ? marker : l
                ).join('\n');
            }

            // 过滤代码块围栏标记（```python / ```），用于纯文本显示（对话条、流式中途气泡）
            function stripCodeFenceMarks(text) {
                if (!text) return '';
                return String(text)
                    .split('\n')
                    .map(line => /^\s*```/.test(line) ? '' : line)
                    .join('\n')
                    .replace(/\n{3,}/g, '\n\n');
            }

            // 剥离 markdown 语法符号（**加粗** / # 标题 / - 列表 / `代码` 等），
            // 用于对话条纯文本显示：只保留可读内容，不出现 ****** 这类格式化残渣。
            function stripMarkdownSyntax(text) {
                if (!text) return '';
                let s = String(text);
                // LaTeX 公式（$$...$$ / $...$ / \[...\] / \(...\)）→ 纯文本可读形式，
                // 避免对话条打字机里出现 $$ \sum F = 0 $$ 这类原始公式符号
                s = s.replace(/\$\$([\s\S]*?)\$\$/g, (m, inner) => ' ' + latexToPlain(inner) + ' ');
                s = s.replace(/\\\[([\s\S]*?)\\\]/g, (m, inner) => ' ' + latexToPlain(inner) + ' ');
                s = s.replace(/\$([^$\n]*?)\$/g, (m, inner) => ' ' + latexToPlain(inner) + ' ');
                s = s.replace(/\\\(([^\\\n]*?)\\\)/g, (m, inner) => ' ' + latexToPlain(inner) + ' ');
                // 行内反引号代码：`xxx` → xxx
                s = s.replace(/`([^`\n]+)`/g, '$1');
                // 加粗 / 斜体 / 删除线：***x*** / **x** / *x* / ~~x~~ → x
                s = s.replace(/(\*\*\*|\*\*|__|~~|\*|_)([^*_~\n]+?)\1/g, '$2');
                // 残留的孤立星号 / 下划线 / 波浪线（连续多个）直接删除
                s = s.replace(/[*_~]{2,}/g, '');
                // 行首标记：标题 / 引用 / 无序列表 / 有序列表 / 分隔线
                s = s.split('\n').map(function(line) {
                    let t = line.replace(/^\s{0,3}(#{1,6})\s+/, '');          // ### 标题
                    t = t.replace(/^\s{0,3}(>+)\s?/, '');                     // > 引用
                    t = t.replace(/^\s{0,3}[-*+]\s+/, '');                    // - 无序列表
                    t = t.replace(/^\s{0,3}\d+[.、)]\s+/, '');                // 1. 有序列表
                    t = t.replace(/^\s*([-*_])\s*(?:\1\s*){2,}$/, '');        // --- 分隔线
                    return t;
                }).join('\n');
                // 行内链接 / 图片：只保留显示文字 [文字](url) → 文字
                s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
                s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
                // 兜底：删除相邻星号对与残留符号块
                s = s.replace(/\*\s*\*/g, '');
                s = s.replace(/[#>]{2,}/g, '');
                return s;
            }

            // 括号深度计数的 [TOOL:...] 剥离：处理 JSON 内嵌套 } / ]，
            // 并感知 JSON 字符串（字符串值里的 ] 不参与深度计数），
            // 修复非贪婪正则在嵌套括号处截断导致工具标记残留的问题
            function stripToolMarkers(text) {
                if (!text || text.toUpperCase().indexOf('[TOOL') === -1) return text;
                let result = '';
                let i = 0;
                while (i < text.length) {
                    if (text[i] === '[' && text.substr(i, 5).toUpperCase() === '[TOOL') {
                        let j = i + 5, depth = 1, inString = false;
                        while (j < text.length) {
                            const c = text[j];
                            if (inString) {
                                if (c === '\\') j++;          // 转义字符跳过
                                else if (c === '"') inString = false;
                            } else {
                                if (c === '"') inString = true;
                                else if (c === '[' || c === '{') depth++;
                                else if (c === ']' || c === '}') {
                                    depth--;
                                    if (depth === 0) { j++; break; }
                                }
                            }
                            j++;
                        }
                        i = j;  // 跳过整个 [TOOL:...] 标记
                        continue;
                    }
                    result += text[i];
                    i++;
                }
                return result;
            }

            // 彻底清除分段符与指令标签（模型输出/历史存档可能残留）
            function cleanSeg(text) {
                if (!text) return '';
                let out = String(text);
                // 先修复模型误写的 ```c 孤立围栏（还原为分段标记），再统一清理
                out = repairCodeFences(out);
                const rawMarker = segmentMarker ? (segmentMarker.value || '\\c') : '\\c';
                // 容错：config/设置面板可能把分段符存成 \\c（两个反斜杠），规约为 \c 再判断，
                // 否则会走 split 分支，对真实 \c 分毫不清
                const marker = rawMarker.replace(/\\\\/g, '\\');
                if (marker === '\\c') {
                    // 先保护 LaTeX 公式（$$...$$ / $...$ / \(...\) / \[...\]），
                    // 再清 \c 分段符，避免公式里的 \cdot / \cancel 被误删
                    const latexHeld = [];
                    out = out
                        .replace(/\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\$[^$\n]*\$|\\\([\s\S]*?\\\)/g,
                            m => { latexHeld.push(m); return '\u0000L' + (latexHeld.length - 1) + '\u0000'; });
                    out = out.replace(/\\+c/g, '');
                    out = out.replace(/\u0000L(\d+)\u0000/g, (m, i) => latexHeld[+i] || m);
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
                // 先用深度计数剥离 [TOOL:{...}]（可能嵌套），再走通用标签清理
                out = stripToolMarkers(out);
                out = out
                    .replace(/\[(ACTION|EMOTION|TOOL|cheer|joy|happy|sad|anger|surprise|disgust|fear|neutral|speak|listen|wave|nod|agree|shake|tilt|gasp|sigh|bow|think|code|board):?[\s\S]*?\]/gi, '')  // [ACTION:xxx] [EMOTION:xxx] [TOOL:xxx] 以及短标签 [cheer][joy]
                    .replace(/【(ACTION|EMOTION|TOOL):[^】]*】/gi, '');   // 中文括号变体
                // 兜底：清理残留的孤立方括号标签（如 [emoji] / 任何方括号单行）
                out = out
                    .split('\n')
                    .map(line => /^\s*[\[【][^\]】]+[\]】]\s*$/.test(line) ? '' : line)
                    .join('\n');
                // 兜底：修复未闭合的 markdown 代码围栏 ```（常见于出题题面末尾多余的散反引号，
                // 如  ```age = -5`` ` ），避免 marked 把后续整段误识别为 inline code 或拼接错误。
                // 1) 行尾的散反引号（≥2 个、且前面没有成对围栏）直接削掉
                out = out.replace(/[`]{2,}\s*$/g, '');
                // 2) 全文 ``` 计数若为奇数，追加一个 ``` 收尾，保证 marked 解析不出错
                const fenceCount = (out.match(/```/g) || []).length;
                if (fenceCount % 2 === 1) out += '\n```';
                return out;
            }

            // 兜底扫描：部分小模型会直接在正文中输出 [TOOL:show_terminal{...}] 字面量，
            // 后端可能未提取到（regex 对含特殊字符的 JSON 不稳定）。这里从前端兜底再扫一次。
            // 仅在 done 帧、且后端 tool_event 缺失时调用；扫描所有标记，避免漏掉终端。
            function _fallbackScanToolCall(content) {
                if (!content) return;
                try {
                    var re = /\[TOOL:\s*(\{[\s\S]*?\})\s*\]/g;
                    var m;
                    var validTypes = ['show_terminal', 'show_image', 'show_board'];
                    while ((m = re.exec(content)) !== null) {
                        try {
                            var obj = JSON.parse(m[1]);
                            if (validTypes.indexOf(obj.type) === -1) continue;
                            console.log('[fallback] 扫描到工具调用:', obj);
                            if (typeof window.handleAITool === 'function') {
                                window.handleAITool(obj);
                            }
                        } catch (e2) { /* 单个标记解析失败，跳过 */ }
                    }
                } catch (e) {
                    // 静默失败，不是所有正文都符合 JSON 格式
                }
            }

            // ========================
            // 富文本渲染：Markdown + LaTeX 公式（KaTeX）
            // ========================
            // 占位符用 ⟦K<i>⟧（U+27E6/U+27E7 文本 token）而非 \u0000 NUL：
            // NUL 控制字符会被 DOMPurify 剥离（残留 "L0" 之类碎片），导致还原失败。
            // ⟦ ⟧ 不参与 markdown 解析，DOMPurify 也不会动它。
            function extractLatex(text) {
                const blocks = [];
                let s = String(text);
                s = s.replace(/\$\$([\s\S]*?)\$\$/g, (m, inner) => {
                    blocks.push({ expr: inner.trim(), display: true });
                    return '\u27E6K' + (blocks.length - 1) + '\u27E7';
                });
                s = s.replace(/\\\[([\s\S]*?)\\\]/g, (m, inner) => {
                    blocks.push({ expr: inner.trim(), display: true });
                    return '\u27E6K' + (blocks.length - 1) + '\u27E7';
                });
                s = s.replace(/\$([^$\n]*?)\$/g, (m, inner) => {
                    blocks.push({ expr: inner.trim(), display: false });
                    return '\u27E6K' + (blocks.length - 1) + '\u27E7';
                });
                s = s.replace(/\\\(([\s\S]*?)\\\)/g, (m, inner) => {
                    blocks.push({ expr: inner.trim(), display: false });
                    return '\u27E6K' + (blocks.length - 1) + '\u27E7';
                });
                return { text: s, blocks };
            }

            // 把占位符还原为 KaTeX 渲染的 HTML；KaTeX 不可用时回退为纯文本公式
            function restoreLatex(html, blocks) {
                if (!blocks.length) return html;
                return html.replace(/\u27E6K(\d+)\u27E7/g, function(m, i) {
                    const b = blocks[+i];
                    if (!b) return m;
                    try {
                        return window.katex.renderToString(b.expr, {
                            displayMode: b.display,
                            throwOnError: false,
                            strict: false
                        });
                    } catch (e) {
                        return b.expr; // 渲染失败回退为原公式文本
                    }
                });
            }

            // LaTeX → 纯文本（对话条打字机用）：$$...$$ 转成 ∑F = 0 这类可读文本
            function latexToPlain(expr) {
                if (!expr) return '';
                let s = String(expr).trim();
                s = s.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, '($1)/($2)');
                s = s.replace(/\\sqrt\{([^{}]*)\}/g, '√($1)');
                const cmds = {
                    '\\sum': '∑', '\\int': '∫', '\\prod': '∏', '\\sqrt': '√',
                    '\\times': '×', '\\cdot': '·', '\\div': '÷', '\\pm': '±',
                    '\\leq': '≤', '\\geq': '≥', '\\neq': '≠', '\\approx': '≈',
                    '\\rightarrow': '→', '\\Rightarrow': '⇒', '\\leftarrow': '←',
                    '\\infty': '∞', '\\alpha': 'α', '\\beta': 'β', '\\gamma': 'γ',
                    '\\theta': 'θ', '\\pi': 'π', '\\mu': 'μ', '\\sigma': 'σ',
                    '\\omega': 'ω', '\\Delta': 'Δ', '\\lambda': 'λ'
                };
                s = s.replace(/\\[a-zA-Z]+/g, m => cmds[m] || m);
                s = s.replace(/\\left|\\right/g, '');
                s = s.replace(/_\{([^}]*)\}/g, '_$1').replace(/\^\{([^}]*)\}/g, '^$1');
                s = s.replace(/[{}]/g, '');
                return s;
            }

            // 裸 LaTeX 命令兜底：模型偶尔漏写 $...$ 包裹，把 \times \cdotp 等源码直接
            // 丢在正文里。extractLatex 提取不到时，这里把常见命令转成 Unicode 可读符号。
            // （公式占位符已被 extractLatex 提取，本函数只处理真正泄漏的裸命令）
            function fixBareLatex(text) {
                if (!text) return '';
                let s = String(text);
                s = s.replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)');
                s = s.replace(/\\sqrt\s*\{([^{}]*)\}/g, '√($1)');
                s = s.replace(/\\text\s*\{([^{}]*)\}/g, '$1');
                s = s.replace(/\\mathrm\s*\{([^{}]*)\}/g, '$1');
                const cmds = {
                    '\\times': '×', '\\cdotp': '·', '\\cdot': '·', '\\div': '÷', '\\pm': '±', '\\mp': '∓',
                    '\\Delta': 'Δ', '\\delta': 'δ', '\\nabla': '∇',
                    '\\rightarrow': '→', '\\Rightarrow': '⇒', '\\leftarrow': '←', '\\Leftarrow': '⇐',
                    '\\infty': '∞', '\\approx': '≈', '\\leq': '≤', '\\geq': '≥', '\\neq': '≠', '\\equiv': '≡',
                    '\\sum': '∑', '\\int': '∫', '\\prod': '∏', '\\sqrt': '√',
                    '\\alpha': 'α', '\\beta': 'β', '\\gamma': 'γ', '\\theta': 'θ', '\\pi': 'π', '\\mu': 'μ',
                    '\\sigma': 'σ', '\\omega': 'ω', '\\lambda': 'λ', '\\phi': 'φ', '\\xi': 'ξ', '\\eta': 'η',
                    '\\kappa': 'κ', '\\tau': 'τ', '\\rho': 'ρ', '\\zeta': 'ζ', '\\epsilon': 'ε'
                };
                s = s.replace(/\\([a-zA-Z]+)/g, m => cmds[m] || m);
                // 数学转义 \_ \^ \{ \} \\ → 去掉反斜杠
                s = s.replace(/\\([_{}\^\\])/g, '$1');
                // 下标/上标花括号形式平铺后清理花括号
                s = s.replace(/_\{([^}]*)\}/g, '_$1').replace(/\^\{([^}]*)\}/g, '^$1');
                s = s.replace(/[{}]/g, '');
                return s;
            }

            function renderMarkdown(text) {
                if (!text) return '';
                // 1) 先清分段符/标签（cleanSeg 内部已保护 LaTeX 公式）
                // 2) 提取公式 → 占位符（保护 _ * \ 不被 marked 误解析）
                // 3) fixMarkdownPunct 只处理公式外的星号，不会破坏公式
                // 4) marked → DOMPurify → 还原 KaTeX（KaTeX 输出可信，最后注入）
                const cleaned = cleanSeg(text);
                const extracted = extractLatex(cleaned);
                const fixed = fixMarkdownPunct(fixBareLatex(extracted.text));
                let html = marked.parse(fixed);
                // 禁用删除线/水平线标签：模型可能输出 <del>/<s>/<strike> 或 --- 渲染成 <hr>，
                // 显示为"奇怪的中划线/横线"，一律清除（内容文字本身会保留在 DOM 里）
                html = DOMPurify.sanitize(html, { FORBID_TAGS: ['del', 's', 'strike', 'hr'] });
                return restoreLatex(html, extracted.blocks);
            }

            // 修复模型输出中的异常 markdown 标点（未配对的 ** / 孤立星号 / 空加粗 / 连续星号），
            // 避免 marked 把残渣原样输出成 ****** 这类格式化文本（右侧聊天气泡走 markdown 渲染）。
            function fixMarkdownPunct(text) {
                if (!text) return '';
                let s = String(text);
                // 保护代码块（围栏 + 行内反引号），其中的 * 是代码内容，不参与配平
                const blocks = [];
                s = s.replace(/```[\s\S]*?(?:```|$)/g, m => { blocks.push(m); return '\u0000B' + (blocks.length - 1) + '\u0000'; });
                s = s.replace(/`([^`\n]+)`/g, m => { blocks.push(m); return '\u0000I' + (blocks.length - 1) + '\u0000'; });
                // 1) 连续纯星号串（>=3，未配对）：直接删除（如 *** / ***** / ****** 残渣）
                s = s.replace(/\*{3,}/g, '');
                // 2) 配平双星号：** 数量为奇数 → 删掉最后一个 **（模型常见漏写闭合）
                const db = (s.match(/\*\*/g) || []).length;
                if (db % 2 === 1) { const i = s.lastIndexOf('**'); if (i !== -1) s = s.slice(0, i) + s.slice(i + 2); }
                // 3) 配平单星号（斜体）：孤立 * 数量为奇数 → 删掉最后一个 *
                const sg = (s.match(/(?<!\*)\*(?!\*)/g) || []).length;
                if (sg % 2 === 1) { const i = s.lastIndexOf('*'); if (i !== -1) s = s.slice(0, i) + s.slice(i + 1); }
                // 4) 删除线 ~~x~~：去掉波浪线标记保留文字（4B 模型常把 ~~ 当强调用，
                //    marked 会渲染成删除线——文字中间横线，非常奇怪）
                s = s.replace(/~~([^~]+)~~/g, '$1');
                // 5) 恢复代码块
                s = s.replace(/\u0000([BI])(\d+)\u0000/g, (m, t, i) => blocks[+i] || m);
                return s;
            }

            function addBubble(text, sender, opts) {
                const bubble = document.createElement('div');
                bubble.className = `bubble ${sender}`;
                bubble.textContent = cleanSeg(text);
                // 批量模式：不主动插入 DOM，由调用方用 DocumentFragment 收集后一次性插入
                if (!(opts && opts.batch)) {
                    conversation.appendChild(bubble);
                    conversation.scrollTop = conversation.scrollHeight;
                }
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
            // 长文本模式：写作文 / 粘贴长代码时切换到大幅输入区
            let longInputMode = false;
            const longInputBtn = document.getElementById('long-input-btn');
            if (longInputBtn) {
                longInputBtn.addEventListener('click', function() {
                    longInputMode = !longInputMode;
                    longInputBtn.classList.toggle('active', longInputMode);
                    messageInput.classList.toggle('long-mode', longInputMode);
                    autoResizeMessageInput();
                    messageInput.focus();
                });
            }
            function autoResizeMessageInput() {
                const maxH = longInputMode ? Math.round(window.innerHeight * 0.55) : 140;
                messageInput.style.height = 'auto';
                messageInput.style.height = Math.min(messageInput.scrollHeight, maxH) + 'px';
            }
            // Auto-resize textarea
            messageInput.addEventListener('input', autoResizeMessageInput);

            // 真流式预览缓冲（模型生成时逐 token 累积，仅备用，不写入 DOM）
            let _previewBuf = '';

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

                // 重置 Galgame 对话条（并停止上一轮的语音）
                if (typeof stopTeacherAudio === 'function') stopTeacherAudio();
                _pendingWholeAudio = null;
                _previewBuf = '';              // 真流式预览缓冲（仅累积备用，不写 DOM）
                dialogueSegments = [];
                dialogueSegIdx = -1;
                dialogueStreaming = true;
                dialogueBar.style.display = 'block';
                dialogueContent.textContent = '……';
                dialogueContent.classList.remove('type-caret');
                dialogueIndicator.textContent = '老师思考中';
                dialogueIndicator.classList.remove('hidden');

                // 每个 \c 分段一个独立右侧气泡（按 segment 索引一一对应）
                const segmentBubbles = [];
                let latestSeg = 0;
                isStreaming = true;
                sendBtn.disabled = true;

                // 安全网：若 60 秒后还在 isStreaming，强制释放（避免 SSE 卡死后用户再发消息被锁）
                const _streamTimeout = setTimeout(function() {
                    if (isStreaming) {
                        console.warn('[chat] 流式超时（60s），强制释放 isStreaming');
                        const b0 = segmentBubbles[0];
                        const timeoutText = '\n（响应超时）';
                        if (b0) b0.textContent = (b0.textContent || '') + timeoutText;
                        else segmentBubbles[0] = addBubble(timeoutText, 'teacher');
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
                    body: JSON.stringify({ message: text, lesson_folder: currentLesson, attachments: attachments, long_mode: longInputMode })
                }).then(response => {
                    console.log('[chat] response received, status=' + response.status + ', ok=' + response.ok);
                    if (!response.body) throw new Error('No response body');
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let fullText = '';
                    let finished = false;
                    // 性能优化：流式渲染节流
                    // 每帧 SSE 推的内容只缓存到 latestText，再用 RAF 批量写入 DOM；
                    // renderMarkdown 只在 done 时跑一次（流式阶段用 textContent 增量追加）。
                    let latestText = '';
                    let renderQueued = false;
                    let lastRenderTime = 0;
                    const MIN_RENDER_INTERVAL = 50; // ms，最小渲染间隔
                    function scheduleBubbleUpdate() {
                        if (renderQueued) return;
                        renderQueued = true;
                        const now = performance.now();
                        const wait = Math.max(0, MIN_RENDER_INTERVAL - (now - lastRenderTime));
                        setTimeout(() => {
                            renderQueued = false;
                            lastRenderTime = performance.now();
                            // 流式只更新纯文本（当前分段），避免每帧 renderMarkdown 重解析
                            const b = segmentBubbles[latestSeg];
                            if (b) b.textContent = stripCodeFenceMarks(dialogueSegments[latestSeg] || '');
                            // 滚动只在用户已接近底部时跟随，避免抖动
                            const nearBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 80;
                            if (nearBottom) conversation.scrollTop = conversation.scrollHeight;
                        }, wait);
                    }

                    // 流式结束后：每个 \c 分段各自渲染为一个独立 markdown 气泡
                    function renderSegmentsToBubbles() {
                        const segs = dialogueSegments.filter(s => s);
                        if (!segs.length) {
                            // 兜底：无分段帧（模型直接返回 done）→ 用全文渲染单一气泡
                            if (fullText) {
                                if (!segmentBubbles[0]) segmentBubbles[0] = addBubble('', 'teacher');
                                segmentBubbles[0].innerHTML = renderMarkdown(fullText);
                            }
                            conversation.scrollTop = conversation.scrollHeight;
                            return;
                        }
                        for (let si = 0; si < dialogueSegments.length; si++) {
                            if (!dialogueSegments[si]) continue;
                            const b = segmentBubbles[si];
                            if (b) b.innerHTML = renderMarkdown(dialogueSegments[si]);
                        }
                        conversation.scrollTop = conversation.scrollHeight;
                    }

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
                                renderSegmentsToBubbles();
                                finishDialogue();
                                return;
                            }
                            const chunk = decoder.decode(value);
                            const lines = chunk.split('\n');
                            for (const line of lines) {
                                if (line.startsWith('data: ')) {
                                    const payload = line.replace('data: ', '').trim();
                                    if (payload === '[DONE]') {
                                        renderSegmentsToBubbles();
                                        finishDialogue();
                                        return;
                                    }
                                    try {
                                        const data = JSON.parse(payload);
                                        // preview 帧：生成过程中的 token。不再实时刷新正文——高频 DOM 写入
                                        // 会造成"先卡顿过一遍" + 前端卡顿；内容统一由 content 帧 + 完成后
                                        // 的逐段播放呈现（对话条保持"思考中"动画）。
                                        if (data.preview && !data.done) {
                                            _previewBuf += data.preview;   // 仅累积（备用），不写入 DOM
                                        }
                                        // 按 segment 分组存储（分段展示，流式中不实时滚动，结束后逐段播放）
                                        if (data.content && !data.done) {
                                            const seg = data.segment !== undefined ? data.segment : 0;
                                            dialogueSegments[seg] = cleanSeg(data.content);
                                            // 每个 \c 分段对应右侧一个独立气泡（首次到达时创建）
                                            if (!segmentBubbles[seg]) {
                                                segmentBubbles[seg] = addBubble('', 'teacher');
                                            }
                                            latestSeg = seg;
                                            fullText = dialogueSegments.filter(s => s).join('\n\n');
                                            latestText = fullText;
                                            scheduleBubbleUpdate();
                                        }
                                        // done 帧：每个分段独立渲染为右侧气泡
                                        if (data.done && data.content) {
                                            // done 帧内容也要走 cleanSeg（修复模型输出孤立的 ```c / 裸 c）
                                            fullText = cleanSeg(data.content);
                                            renderSegmentsToBubbles();
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
                                        // AI 工具调用：支持一次回复多个工具（黑板→终端→图片按序触发）
                                        if (data.tool_events && Array.isArray(data.tool_events) && data.tool_events.length) {
                                            data.tool_events.forEach(function(t) {
                                                if (t && typeof t === 'object') {
                                                    console.log('AI 工具调用:', t);
                                                    handleAITool(t);
                                                }
                                            });
                                        } else if (data.tool_event && typeof data.tool_event === 'object') {
                                            console.log('AI 工具调用:', data.tool_event);
                                            handleAITool(data.tool_event);
                                        }
                                        // AI 工具调用（字符串协议）：start_exam → 自动出题并切到测验视图；
                                        // next_unit → 自动进入下一课（此前前端未处理导致无反应）
                                        if (data.tool_event && typeof data.tool_event === 'string') {
                                            console.log('AI 工具调用(字符串):', data.tool_event);
                                            handleStringToolEvent(data.tool_event);
                                        }
                                        // 兜底：部分模型会直接在正文输出 [TOOL:show_terminal{...}] 字面量，
                                        // 后端可能未提取到（regex 对含特殊字符的 JSON 不稳定）。
                                        // 这里从 done.content 兜底扫描一次，弥补 4B 模型对工具协议遵循度低的问题。
                                        if (data.done && data.content && (!data.tool_event || typeof data.tool_event !== 'object')) {
                                            _fallbackScanToolCall(data.content);
                                        }
                                        // AI 联动：收到 [PARAM:...] 参数直调 → 渐变设置模型参数（短暂动作，自动恢复）
                                        if (data.params && typeof data.params === 'object') {
                                            console.log('AI 参数直调:', data.params);
                                            if (typeof window.setLive2DParams === 'function') {
                                                window.setLive2DParams(data.params, 400, 2500);
                                            }
                                        }
                                        // AI 联动：收到 TTS 音频（done 帧）→ 暂存，由对话条"逐段朗读"驱动播放；
                                        // 仅当对话条无有效分段（直接 done）时兜底播放整段
                                        if (data.audio_url && data.done) {
                                            _pendingWholeAudio = data.audio_url;
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
                    const b0 = segmentBubbles[0];
                    if (b0) b0.textContent = '\u274c ' + err.message;
                    else segmentBubbles[0] = addBubble('\u274c ' + err.message, 'teacher');
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
            // 处理 AI 主动发起的字符串工具调用（start_exam / next_unit）
            function handleStringToolEvent(event) {
                if (event === 'start_exam') {
                    addBubble('📝 老师发起随堂测验，正在出题...', 'teacher');
                    let btn = (typeof examGenerateBtn !== 'undefined' && examGenerateBtn)
                        ? examGenerateBtn : document.getElementById('exam-generate-btn');
                    switchView('exam');
                    if (btn) btn.click();
                } else if (event === 'next_unit') {
                    if (!currentLesson || currentLesson === 'default') {
                        addBubble('⚠️ 还没有选课，无法进入下一课', 'teacher');
                        return;
                    }
                    addBubble('⏩ 老师带你进入下一课...', 'teacher');
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
                    }).catch(err => addBubble('❌ 进入下一课失败: ' + err.message, 'teacher'));
                }
            }

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
                            addBubble('🎬 语义动作（参数驱动）：nod（点头）、agree（赞许点头）、shake（摇头）、tilt（歪头）、gasp（惊讶）、cheer（雀跃）、sigh（叹气）、bow（鞠躬）', 'teacher');
                            const builtinKeys = ['point', 'blackboard', 'greet', 'hello', 'idle', 'listen', 'speak', 'think', 'wave', 'nod', 'agree', 'shake', 'tilt', 'gasp', 'cheer', 'sigh', 'bow'];
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
                    case 'param':
                        // 直接调整模型参数（关节/五官），如 /param ParamAngleX=-15 或 /param reset
                        if (!args) {
                            addBubble('用法：/param <参数名>=<数值> [参数名2=<数值>...] [时长ms]；/param reset 恢复姿势；/param list 查看可调参数', 'teacher');
                            break;
                        }
                        if (args.toLowerCase() === 'list') {
                            const plist = (typeof window.getLive2DParamList === 'function') ? window.getLive2DParamList() : [];
                            addBubble('🎛️ 常用示例：ParamAngleX/Y/Z（头部左右/上下/侧歪）、ParamBodyAngleX/Y/Z（身体）、ParamEyeLOpen/ROpen（眼睛）、ParamJawOpen/ParamMouthOpenY（嘴巴）、ParamMouthSmile（微笑）、ParamBrowLAngle/RAngle（挑眉）、ParamEyeBallX/Y（眼神）、MouthFrownLeft/Right（撇嘴）、Param40/43（叉腰）', 'teacher');
                            addBubble('🎛️ 可调参数（' + plist.length + ' 个）：' + plist.join('、'), 'teacher');
                            break;
                        }
                        if (args.toLowerCase() === 'reset') {
                            if (typeof window.resetLive2DPose === 'function') {
                                window.resetLive2DPose();
                                addBubble('🧍 已恢复头部与身体角度', 'teacher');
                            } else {
                                addBubble('⚠️ 参数系统未就绪', 'teacher');
                            }
                            break;
                        }
                        {
                            const parts = args.trim().split(/\s+/);
                            const dict = {};
                            let duration = null;
                            parts.forEach(function(part) {
                                const eq = part.indexOf('=');
                                if (eq > 0) {
                                    const k = part.slice(0, eq).trim();
                                    const v = parseFloat(part.slice(eq + 1));
                                    if (k && !isNaN(v)) dict[k] = v;
                                } else if (/^\d+$/.test(part)) {
                                    duration = parseInt(part, 10);
                                }
                            });
                            const keys = Object.keys(dict);
                            if (!keys.length) {
                                addBubble('⚠️ 参数格式错误，示例：/param ParamAngleX=-15 ParamEyeLOpen=0.5 800', 'teacher');
                                break;
                            }
                            if (typeof window.setLive2DParams === 'function') {
                                window.setLive2DParams(dict, duration == null ? 300 : duration, 0);
                                addBubble('🎛️ 已设置参数：' + keys.map(function(k) { return k + '=' + dict[k]; }).join('、'), 'teacher');
                            } else {
                                addBubble('⚠️ 参数系统未就绪', 'teacher');
                            }
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
                        addBubble('/terminal [语言] [代码] —— 打开终端弹窗并执行代码（语言: python/javascript/ruby/perl/php/lua/r/shell/powershell，可交互）', 'teacher');
                        addBubble('/action <动作名> —— 播放模型动作（/action list 查看全部，自定义动作可在设置中添加）', 'teacher');
                        addBubble('/emotion <表情名> —— 强制显示表情（happy/sad/angry/think/surprised/neutral）', 'teacher');
                        addBubble('/param <参数名>=<数值> [..] [时长ms] —— 直接调整模型参数/关节（/param list 查看，/param reset 恢复）', 'teacher');
                        addBubble('/ask <内容> —— 直接与 AI 对话（不带课程上下文）', 'teacher');
                        addBubble('/help —— 显示本帮助', 'teacher');
                        break;
                    default:
                        addBubble('❓ 未知命令 /' + cmd + '，输入 /help 查看可用命令', 'teacher');
                }
            }

            // ---- Galgame 对话条：逐段打字机播放（语音开启时改由"逐段朗读"驱动切换） ----
            let _pendingWholeAudio = null;   // done 帧整段音频（无有效分段时兜底播放）

            // 语音总开关（#voice-enabled checkbox；元素缺失时默认开启）
            function _voiceOn() {
                const el = document.getElementById('voice-enabled');
                return !el ? true : el.checked;
            }

            // 朗读当前段并"随语音同步切换"：音频播完自动进入下一段；
            // 无可读文本/TTS 未配置时保持现状（打字机 + 按 Enter 继续）
            function _speakCurrentSegment() {
                if (!_voiceOn()) return;
                const seg = dialogueSegments[dialogueSegIdx];
                if (!seg) return;
                const reqSeg = dialogueSegIdx;
                const text = stripToolMarkers(cleanSeg(seg)).slice(0, 500);
                if (!text) return;
                fetch('/api/tts/speak', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text })
                }).then(function(r) { return r.json(); }).then(function(data) {
                    // 竞态保护：请求期间用户已切段/关闭对话条 → 丢弃过期音频
                    if (reqSeg !== dialogueSegIdx || dialogueBar.style.display === 'none') {
                        if (typeof stopTeacherAudio === 'function') stopTeacherAudio();
                        return;
                    }
                    if (data.ok && data.audio_url) {
                        playTeacherAudio(data.audio_url, function() {
                            // 音频播完：对话条随语音同步切换——还有后续段自动进入下一段，最后一段标记已记录
                            if (dialogueBar.style.display !== 'none') {
                                if (dialogueSegIdx >= dialogueSegments.length - 1) {
                                    dialogueIndicator.textContent = '✓ 已记录';
                                    dialogueIndicator.classList.remove('hidden');
                                } else {
                                    advanceDialogue();
                                }
                            }
                        });
                    } else {
                        // TTS 不可用（未配置/无可读文本）→ 恢复手动推进指示
                        if (dialogueSegIdx < dialogueSegments.length - 1) {
                            dialogueIndicator.textContent = '▼ 按 Enter 继续';
                        }
                        dialogueIndicator.classList.remove('hidden');
                    }
                }).catch(function() {});
            }

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
                    // 没有分段可逐段朗读 → 兜底播放整段音频
                    if (_pendingWholeAudio) {
                        playTeacherAudio(_pendingWholeAudio);
                        _pendingWholeAudio = null;
                    }
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
                // 切换段前：停止上一段的语音
                if (typeof stopTeacherAudio === 'function') stopTeacherAudio();
                dialogueSegIdx++;
                if (dialogueSegIdx >= dialogueSegments.length) {
                    // 全部播完：保持显示最后一句（永久保留，不响应关闭），指示器切换为「已记录」
                    dialogueTypeTimer && clearInterval(dialogueTypeTimer);
                    dialogueTypeTimer = null;
                    dialogueContent.classList.remove('type-caret');
                    // 显示最后一段的完整内容（保证即使打字被打断也展示全文）
                    const last = dialogueSegments[dialogueSegments.length - 1] || '';
                    dialogueContent.textContent = stripMarkdownSyntax(stripCodeFenceMarks(last));
                    dialogueContent.scrollTop = 0;
                    dialogueIndicator.textContent = '✓ 已记录';
                    dialogueIndicator.classList.remove('hidden');
                    return;
                }
                typeDialogue(dialogueSegments[dialogueSegIdx]);
            }

            function typeDialogue(text) {
                if (dialogueTypeTimer) clearInterval(dialogueTypeTimer);
                dialogueContent.textContent = '';
                dialogueContent.classList.remove('type-caret');
                // 语音开启：直接显示整段（随语音播放同步切换），不逐字打字；由 _speakCurrentSegment 播放
                if (_voiceOn()) {
                    dialogueContent.textContent = stripMarkdownSyntax(stripCodeFenceMarks(text));
                    dialogueContent.scrollTop = 0;
                    dialogueIndicator.textContent = '🔊 朗读中';
                    dialogueIndicator.classList.remove('hidden');
                    _speakCurrentSegment();
                    return;
                }
                dialogueContent.classList.add('type-caret');
                dialogueIndicator.textContent = '▼';
                dialogueIndicator.classList.add('hidden');
                // 拆分「普通文本」与「代码块」：代码块一次性整体显示，普通文本逐字打字
                // 普通文本部分先剥离 markdown 符号（**、#、列表标记等），打字/显示不再出现格式化残渣
                const parts = [];
                const fenceRe = /```[\s\S]*?(?:```|$)/g;
                let last = 0, m;
                while ((m = fenceRe.exec(text)) !== null) {
                    if (m.index > last) parts.push({ type: 'text', value: stripMarkdownSyntax(text.slice(last, m.index)) });
                    parts.push({ type: 'code', value: m[0] });
                    last = m.index + m[0].length;
                }
                if (last < text.length) parts.push({ type: 'text', value: stripMarkdownSyntax(text.slice(last)) });
                if (!parts.length) parts.push({ type: 'text', value: stripMarkdownSyntax(text) });
                let pi = 0, ci = 0;
                dialogueTypeTimer = setInterval(function() {
                    if (pi >= parts.length) {
                        clearInterval(dialogueTypeTimer);
                        dialogueTypeTimer = null;
                        dialogueContent.classList.remove('type-caret');
                        dialogueContent.textContent = stripMarkdownSyntax(stripCodeFenceMarks(text));
                        dialogueContent.scrollTop = 0;
                        // 打字完成：显示完整文本（保证最后字符一定可见）
                        if (dialogueSegIdx >= dialogueSegments.length - 1) {
                            dialogueIndicator.textContent = '✓ 已记录';
                        } else {
                            dialogueIndicator.textContent = '▼ 按 Enter 继续';
                        }
                        dialogueIndicator.classList.remove('hidden');
                        return;
                    }
                    const part = parts[pi];
                    if (part.type === 'code') {
                        // 代码块整体显示（过滤 ``` 围栏标记），不逐字打字
                        dialogueContent.textContent += stripCodeFenceMarks(part.value);
                        pi++;
                        ci = 0;
                        return;
                    }
                    if (ci < part.value.length) {
                        dialogueContent.textContent += part.value[ci];
                        ci++;
                    } else {
                        pi++;
                        ci = 0;
                    }
                    dialogueContent.scrollTop = dialogueContent.scrollHeight;
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
                if (typeof stopTeacherAudio === 'function') stopTeacherAudio();
                _pendingWholeAudio = null;
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
                // 用户手动推进：先停止当前段语音（对话条随语音切换，跳过剩余朗读）
                if (typeof stopTeacherAudio === 'function') stopTeacherAudio();
                if (dialogueTypeTimer) {
                    clearInterval(dialogueTypeTimer);
                    dialogueTypeTimer = null;
                    const segText = dialogueSegments[dialogueSegIdx] || '';
                    dialogueContent.textContent = stripMarkdownSyntax(stripCodeFenceMarks(segText));
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
                if (!last || !last.textContent) return;
                // 统一走后端 TTS 链路（与自动朗读一致）：未配置语音服务时不再
                // 静默使用浏览器 speechSynthesis（修复"空 API 仍能朗读"的问题）
                const text = stripToolMarkers(cleanSeg(last.textContent)).slice(0, 500);
                if (!text) return;
                playBtn.disabled = true;
                fetch('/api/tts/speak', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text })
                }).then(function(r) { return r.json(); }).then(function(data) {
                    playBtn.disabled = false;
                    if (data.ok && data.audio_url) {
                        playTeacherAudio(data.audio_url);
                    } else {
                        console.warn('[tts] 朗读失败:', data.error);
                        if (typeof window.showToast === 'function') {
                            showToast(data.error || '未配置可用的语音服务');
                        } else {
                            alert(data.error || '未配置可用的语音服务，请在设置中配置 TTS');
                        }
                    }
                }).catch(function(err) {
                    playBtn.disabled = false;
                    console.warn('[tts] 朗读请求失败:', err);
                });
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

            function examTypeLabel(type) {
                return { single: '单选', multiple: '多选', boolean: '判断', fill: '填空', essay: '简答' }[type] || (type || '单选');
            }

            function renderExamQuestions(questions) {
                examList.innerHTML = questions.map((q, idx) => {
                    const type = q.type || 'single';
                    let answerInput = '';
                    if (type === 'fill') {
                        answerInput = `
                            <div class="q-fill">
                                <input type="text" class="fill-input" placeholder="请输入你的答案…"
                                    style="width:85%; padding:8px 12px; border:1px solid var(--border); border-radius:8px; background:var(--bg-card); color:var(--text-primary); font-size:14px;">
                            </div>`;
                    } else if (type === 'essay') {
                        answerInput = `
                            <div class="q-essay">
                                <textarea class="essay-input" rows="4" placeholder="请输入你的作答…"
                                    style="width:85%; padding:8px 12px; border:1px solid var(--border); border-radius:8px; background:var(--bg-card); color:var(--text-primary); font-size:14px; resize:vertical;"></textarea>
                            </div>`;
                    } else if (type === 'boolean') {
                        // 判断题：渲染"对/错"选项（答案以中文语义提交，后端按"对/错"比对）
                        answerInput = `
                            <div class="q-options">
                                <label><input type="radio" name="q${idx}" value="对"> 对</label>
                                <label><input type="radio" name="q${idx}" value="错"> 错</label>
                            </div>`;
                    } else if (q.options && q.options.length) {
                        answerInput = `
                            <div class="q-options">
                                ${q.options.map((opt, oi) => `
                                    <label>
                                        <input type="${type === 'multiple' ? 'checkbox' : 'radio'}" name="q${idx}" value="${String.fromCharCode(65 + oi)}">
                                        ${renderMarkdown(opt)}
                                    </label>
                                `).join('')}
                            </div>`;
                    }
                    return `
                        <div class="exam-question" data-idx="${idx}" data-type="${type}">
                            <div class="q-title">第 ${idx + 1} 题（${examTypeLabel(type)}）</div>
                            <div style="margin-bottom:6px; font-size:14px; color:var(--text-primary);">${renderMarkdown(q.question)}</div>
                            ${answerInput}
                            <div class="q-explanation" style="display:none;"></div>
                        </div>
                    `;
                }).join('');
            }

            examSubmitBtn.addEventListener('click', function() {
                const questions = document.querySelectorAll('.exam-question');
                const answers = {};
                questions.forEach((qDiv, idx) => {
                    const type = qDiv.getAttribute('data-type') || 'single';
                    const inputs = qDiv.querySelectorAll('input:checked');
                    if (type === 'fill') {
                        const fillInput = qDiv.querySelector('.fill-input');
                        answers[idx] = fillInput ? fillInput.value.trim() : '';
                    } else if (type === 'essay') {
                        const essayInput = qDiv.querySelector('.essay-input');
                        answers[idx] = essayInput ? essayInput.value.trim() : '';
                    } else if (type === 'multiple') {
                        answers[idx] = Array.from(inputs).map(i => i.value).join(',');
                    } else {
                        answers[idx] = inputs.length ? inputs[0].value : '';
                    }
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

            // —— 期末测验：上传标准试卷 → 按原卷测验 → 按标准答案批改 ——
            const examPaperUpload = document.getElementById('exam-paper-upload');
            const examPaperUploadBtn = document.getElementById('exam-paper-upload-btn');
            const examPaperInfo = document.getElementById('exam-paper-info');
            const examFinalBtn = document.getElementById('exam-final-btn');

            function refreshExamPaperInfo() {
                if (!currentLesson || !examPaperInfo) return;
                fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/exam-paper')
                    .then(r => r.json()).then(data => {
                        if (data.ok && data.count > 0) {
                            examPaperInfo.innerHTML = '✅ 已上传标准试卷 <b>' + (data.source || '') + '</b>（' + data.count + ' 题）。课程讲解已围绕试卷展开，可点击"开始期末测验"。';
                            examFinalBtn.disabled = false;
                        } else {
                            examPaperInfo.innerHTML = '未上传试卷。支持 .txt/.md/.json/.pdf/.docx（每题含题干与"答案："行）。上传后课程讲解将围绕这份试卷展开，学习完可开始期末测验。';
                            examFinalBtn.disabled = true;
                        }
                    }).catch(() => {});
            }

            examPaperUploadBtn.addEventListener('click', function() {
                if (!examPaperUpload.files.length) { alert('请先选择试卷文件'); return; }
                if (!currentLesson) { alert('请先进入课程'); return; }
                const fd = new FormData();
                fd.append('file', examPaperUpload.files[0]);
                examPaperUploadBtn.textContent = '上传中...';
                examPaperUploadBtn.disabled = true;
                fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/exam-paper', { method: 'POST', body: fd })
                    .then(r => r.json()).then(data => {
                        examPaperUploadBtn.textContent = '上传试卷';
                        examPaperUploadBtn.disabled = false;
                        if (data.ok) {
                            alert('上传成功：识别出 ' + data.count + ' 题。课程讲解将围绕这份试卷展开，随时可开始期末测验。');
                            refreshExamPaperInfo();
                        } else {
                            alert(data.message || '解析失败，请检查格式');
                            if (data.format_hint) console.log('[试卷格式说明]\n' + data.format_hint);
                        }
                    }).catch(err => {
                        examPaperUploadBtn.textContent = '上传试卷';
                        examPaperUploadBtn.disabled = false;
                        alert('上传失败: ' + err.message);
                    });
            });

            examFinalBtn.addEventListener('click', function() {
                examFinalBtn.textContent = '加载中...';
                examFinalBtn.disabled = true;
                fetch('/api/exam/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: 'final' })
                }).then(r => r.json()).then(data => {
                    examFinalBtn.textContent = '开始期末测验';
                    examFinalBtn.disabled = false;
                    if (data.questions && data.questions.length) {
                        renderExamQuestions(data.questions);
                        examSubmitBtn.style.display = 'inline-block';
                        examList.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    } else {
                        alert(data.message || '获取试卷失败');
                    }
                }).catch(err => {
                    examFinalBtn.textContent = '开始期末测验';
                    examFinalBtn.disabled = false;
                    alert('失败: ' + err.message);
                });
            });

            // —— 学习统计：正确率趋势 / 错题本 / 薄弱知识点 ——
            function loadStudyStats() {
                if (!currentLesson) return;
                fetch('/api/progress?lesson_folder=' + encodeURIComponent(currentLesson))
                    .then(r => r.json())
                    .then(data => {
                        const stats = data.stats || {};
                        const qc = document.getElementById('stat-quiz-count');
                        const avg = document.getElementById('stat-avg-score');
                        const last = document.getElementById('stat-last-score');
                        const wc = document.getElementById('stat-wrong-count');
                        const ca = document.getElementById('stat-code-attempts');
                        if (qc) qc.textContent = stats.quiz_count || 0;
                        if (avg) avg.textContent = stats.avg_score != null ? stats.avg_score : '–';
                        if (last) last.textContent = stats.last_score != null ? stats.last_score : '–';
                        if (wc) wc.textContent = stats.wrong_book_count || 0;
                        if (ca) ca.textContent = stats.code_attempts || 0;

                        // 正确率趋势（最近 10 次，横向条形）
                        const trendEl = document.getElementById('stat-score-trend');
                        if (trendEl) {
                            const hist = stats.score_history || [];
                            if (hist.length) {
                                const maxW = 100;
                                trendEl.innerHTML = '<div style="font-size:12px; color:var(--text-muted); margin-bottom:4px;">📈 最近成绩趋势：</div>' +
                                    hist.slice(-10).map(r => {
                                        const w = Math.max(4, Math.round(r.score / 100 * maxW));
                                        return '<div style="display:flex; align-items:center; gap:6px; margin-bottom:3px;">' +
                                            '<span style="flex:0 0 40px; color:var(--text-muted); font-size:11px;">' + r.score + '分</span>' +
                                            '<span class="trend-bar" style="width:' + w + 'px;"></span>' +
                                            '<span style="font-size:10px; color:var(--text-dim);">' + (r.timestamp || '').slice(5, 16) + '</span></div>';
                                    }).join('');
                            } else {
                                trendEl.innerHTML = '<span style="color:var(--text-dim);">还没有测验记录，去「随堂测验」生成一题试试吧。</span>';
                            }
                        }

                        // 薄弱知识点
                        const weakEl = document.getElementById('stat-weak-topics');
                        if (weakEl) {
                            const weak = stats.weak_topics || [];
                            weakEl.innerHTML = weak.length
                                ? '<span style="color:var(--text-muted);">🎯 薄弱知识点：</span>' + weak.map(t => '<span style="color:var(--gold);">' + _escHtml(t) + '</span>').join('、')
                                : '';
                        }

                        // 错题本
                        const bookEl = document.getElementById('stat-wrong-book');
                        if (bookEl) {
                            const book = stats.wrong_book || [];
                            if (book.length) {
                                bookEl.innerHTML = book.map(w => {
                                    const typeName = { single: '单选', multiple: '多选', boolean: '判断', fill: '填空', essay: '简答' }[w.type] || w.type;
                                    return '<div class="wrong-item">' +
                                        '<div class="wrong-q">[' + typeName + '] ' + _escHtml(w.question) +
                                        (w.wrong_count > 1 ? ' <span style="color:#e74c3c; font-size:11px;">✗' + w.wrong_count + '次</span>' : '') + '</div>' +
                                        '<div class="wrong-ans">你的答案：' + _escHtml(String(w.student_answer || '（未作答）')) + '</div>' +
                                        '<div class="wrong-correct">正确答案：' + _escHtml(String(w.correct_answer || '')) + '</div>' +
                                        (w.explanation ? '<div style="color:var(--text-muted);">💡 ' + _escHtml(w.explanation) + '</div>' : '') +
                                        '</div>';
                                }).join('');
                            } else {
                                bookEl.innerHTML = '<span style="color:var(--text-dim);">暂无错题，继续保持！</span>';
                            }
                        }
                    })
                    .catch(function(err) {
                        const bookEl = document.getElementById('stat-wrong-book');
                        if (bookEl) bookEl.innerHTML = '<span style="color:var(--text-muted);">统计加载失败：' + _escHtml(err.message) + '</span>';
                    });
            }

            const statRefreshBtn = document.getElementById('stat-refresh-btn');
            if (statRefreshBtn) statRefreshBtn.addEventListener('click', loadStudyStats);

            // —— 历史对话回顾：按单元加载归档对话 + 导出 Markdown ——
            function loadHistoryReview() {
                const listEl = document.getElementById('history-list');
                if (!listEl || !currentLesson) return;
                listEl.innerHTML = '<span style="color:var(--text-muted);">正在加载…</span>';
                fetch('/api/lesson/' + encodeURIComponent(currentLesson) + '/history')
                    .then(r => r.json())
                    .then(data => {
                        const units = data.units || [];
                        if (!units.length) {
                            listEl.innerHTML = '<span style="color:var(--text-dim);">还没有对话记录。学习后可在测验面板回顾每个单元的聊天。</span>';
                            return;
                        }
                        listEl.innerHTML = units.map(u => {
                            const msgs = u.conversation || [];
                            // 折叠面板：每单元一个 summary，展开显示逐条对话
                            const preview = msgs.slice(0, 3).map(m => {
                                const who = m.role === 'user' ? '👤 学生' : '👩‍🏫 老师';
                                return '<div style="margin:2px 0;">' + who + '：' + _escHtml(String(m.content || '').slice(0, 60)) + '</div>';
                            }).join('');
                            return '<details style="border:1px solid var(--border-subtle); border-radius:8px; padding:8px 10px; margin-bottom:8px; background:rgba(0,0,0,0.15);">' +
                                '<summary style="cursor:pointer; font-weight:600; color:var(--gold);">' +
                                '📚 ' + _escHtml(u.title) + '（' + msgs.length + ' 条' + (u.archived ? ' · 已归档' : ' · 当前') + '）</summary>' +
                                '<div style="margin-top:6px; max-height:260px; overflow-y:auto;">' +
                                msgs.map(m => {
                                    const who = m.role === 'user' ? '👤 学生' : '👩‍🏫 老师';
                                    const text = m.role === 'assistant' ? cleanSeg(String(m.content || '')) : String(m.content || '');
                                    return '<div style="margin:4px 0; padding:4px 6px; border-radius:6px; background:' +
                                        (m.role === 'user' ? 'rgba(76,175,80,0.12);' : 'rgba(64,122,255,0.10);') + '">' +
                                        '<span style="color:var(--text-dim); font-size:11px;">' + who + '</span> ' +
                                        renderMarkdown(text.length > 200 ? text.slice(0, 200) + '…' : text) +
                                        '</div>';
                                }).join('') +
                                '</div></details>';
                        }).join('');
                    })
                    .catch(err => {
                        listEl.innerHTML = '<span style="color:var(--text-muted);">加载失败：' + _escHtml(err.message) + '</span>';
                    });
            }

            const historyRefreshBtn = document.getElementById('history-refresh-btn');
            const historyExportBtn = document.getElementById('history-export-btn');
            if (historyRefreshBtn) historyRefreshBtn.addEventListener('click', loadHistoryReview);
            if (historyExportBtn) historyExportBtn.addEventListener('click', function() {
                if (!currentLesson) { alert('请先进入课程'); return; }
                window.open('/api/lesson/' + encodeURIComponent(currentLesson) + '/history/export', '_blank');
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
                                        <span><strong>${r.title}</strong> <small>${r.type === 'video' ? '🎬 视频课程 · ' + (r.platform === 'bilibili' ? 'B站' : r.platform === 'netease_open_course' ? '网易公开课' : '') + ' · <a href="' + (r.url || '#') + '" target="_blank" rel="noopener" style="color:var(--gold);">打开视频</a>' : r.type + ' · ' + (r.description || '')}</small></span>
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
                        const okList = downloads.filter(d => d.status === 'ok' && !d.skipped_video);
                        const videoList = downloads.filter(d => d.skipped_video);
                        const parts = [];
                        if (okList.length) parts.push('✅ 下载完成：' + okList.map(d => d.title).join('、'));
                        if (videoList.length) {
                            parts.push('🎬 视频无需下载（点开链接观看）：' + videoList.map(function(d) {
                                return '<a href="' + (d.url || '#') + '" target="_blank" rel="noopener" style="color:var(--gold);">' + d.title + '</a>';
                            }).join('、'));
                        }
                        if (failed.length) parts.push('⚠️ 失败：' + failed.map(d => d.title + (d.error ? '(' + d.error + ')' : '')).join('、'));
                        if (!parts.length && !failed.length) parts.push('未选中可下载资源');
                        statusEl.innerHTML = parts.join('<br>') + (okList.length ? '<br><small style="color:var(--text-dim);">保存在课程目录 lessons/' + (data.lesson_folder || '') + '/</small>' : '');
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
                                                // 历史渲染同样过 cleanSeg：旧存档可能残留 \c 分段符 / 标签 / 孤立围栏
                                                const text = msg.role === 'assistant' ? cleanSeg(msg.content) : msg.content;
                                                const bubble = addBubble(text, msg.role === 'user' ? 'user' : 'teacher');
                                                if (msg.role === 'assistant') {
                                                    bubble.innerHTML = renderMarkdown(text);
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

            // 课程备份导入：选择 zip → 上传到 /api/lessons/import
            const importLessonBtn = document.getElementById('import-lesson-btn');
            const importLessonFile = document.getElementById('import-lesson-file');
            if (importLessonBtn && importLessonFile) {
                importLessonBtn.addEventListener('click', function() {
                    importLessonFile.value = '';
                    importLessonFile.click();
                });
                importLessonFile.addEventListener('change', function() {
                    const file = this.files && this.files[0];
                    if (!file) return;
                    if (file.name.slice(-4).toLowerCase() !== '.zip') {
                        alert('请选择 .zip 备份文件');
                        return;
                    }
                    importLessonBtn.disabled = true;
                    importLessonBtn.textContent = '⏳ 导入中...';
                    const fd = new FormData();
                    fd.append('file', file);
                    fetch('/api/lessons/import', { method: 'POST', body: fd })
                        .then(r => r.json())
                        .then(data => {
                            if (data.error) {
                                alert('导入失败: ' + data.error);
                            } else {
                                alert('导入成功：课程「' + data.folder + '」（共 ' + data.imported_files + ' 个文件）');
                                loadLessons();
                                if (typeof renderMenuLessons === 'function') renderMenuLessons();
                            }
                        })
                        .catch(err => alert('导入失败: ' + err.message))
                        .finally(() => {
                            importLessonBtn.disabled = false;
                            importLessonBtn.textContent = '📥 导入备份';
                        });
                });
            }

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
                // 立即终止打字机书写并清空内容层，避免关闭后内容还在后台逐字追加、或下一帧又拉起
                try {
                    _boardTypingCancel = true;
                    _boardTypingActive = false;
                    const contentLayer = document.getElementById('board-content-layer');
                    if (contentLayer) contentLayer.innerHTML = '';
                } catch (e) {}
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

            function modelReachPanel(direction, opts) {
                opts = opts || {};
                const noMove = !!opts.noMove;  // 打开终端等场景：不做蹲下/位移，只原地伸手
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
                    if (!noMove) {
                        await tweenModelTo(baseX, baseY + h * 0.20, baseS, 320);
                    }
                    // 2) 伸手：优先用参数直接驱动手臂（幅度大、不被动作覆盖）；参数不可用才回退 hello 动作
                    const reached = startArmReach();
                    if (!reached && typeof triggerAction === 'function') {
                        try { triggerAction('hello'); } catch (e) { console.warn('triggerAction 失败:', e); }
                    }
                    console.log('[MODEL] %s 方向伸手（参数驱动=%s noMove=%s）', direction, reached, noMove);
                    if (direction === 'down' && !noMove) {
                        // 终端在下方：手再往下探，贴近屏幕底部边缘
                        await tweenModelTo(baseX, baseY + h * 0.26, baseS, 200);
                    }
                    // 3) 伸手动画结束后稍微停顿：保持伸手姿态，让"手悬在窗口位置、窗口被拉出"的瞬间被看清
                    await new Promise(function(r) { setTimeout(r, noMove ? 300 : 700); });
                    // 4) 收回手，跟随窗口一起站起来：恢复原位（窗口放大到 100% 的后半段同步）
                    stopArmReach();
                    if (!noMove) {
                        await tweenModelTo(baseX, baseY, baseS, 520);
                    }
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
                    appendTerminal('终端已就绪。在下方输入代码，按 Enter 或 ▶ 运行；运行记录会自动提供给 AI。', 'term-info');
                }

                // 1) 模型原地伸手配合终端拉起（不下蹲、不位移，避免"模型沉下去"的观感）
                const p = modelReachPanel('down', { noMove: true });
                // 2) 等模型伸手到位（~400ms），终端从屏幕下方先缩小再升起放大
                setTimeout(function() {
                    // 窗口中心对准模型手部位置（模型原地伸手，位置即原位手部区域）
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
