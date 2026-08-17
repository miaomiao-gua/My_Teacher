'use strict';

            // ============================
            // 1. DOM 引用 & 状态
            // ============================
            const navBtns = document.querySelectorAll('.nav-btn');
            const viewContents = {
                chat: document.getElementById('view-chat'),
                exam: document.getElementById('view-exam'),
                resource: document.getElementById('view-resource'),
                lesson: document.getElementById('view-lesson'),
                settings: document.getElementById('view-settings')
            };

            // 首页菜单
            const menuScreen = document.getElementById('menu-screen');
            const menuLessonList = document.getElementById('menu-lesson-list');
            const menuCreateBtn = document.getElementById('menu-create-btn');
            const menuSettingsBtn = document.getElementById('menu-settings-btn');

            // 备课预览状态（提前声明，避免 TDZ；prepareAndEnter 会用到）
            let _previewPlan = null;      // 当前预览的 plan（可能已被编辑）
            let _previewLessonFolder = null;
            let _previewTopic = null;

            // 备课预览 DOM 引用（提前声明，避免 TDZ）
            const previewOverlay    = document.getElementById('preview-overlay');
            const previewBody       = document.getElementById('preview-body');
            const previewTitle      = document.getElementById('preview-title');
            const previewMeta       = document.getElementById('preview-meta');
            const previewCloseBtn   = document.getElementById('preview-close-btn');
            const previewCancelBtn  = document.getElementById('preview-cancel-btn');
            const previewRegenBtn   = document.getElementById('preview-regen-btn');
            const previewConfirmBtn = document.getElementById('preview-confirm-btn');

            const conversation = document.getElementById('conversation');
            const messageInput = document.getElementById('message-input');
            const sendBtn = document.getElementById('send-btn');
            const playBtn = document.getElementById('play-btn');
            const attachBtn = document.getElementById('attach-btn');
            const attachFileInput = document.getElementById('attach-file-input');
            const attachPanel = document.getElementById('attach-panel');
            let pendingAttachments = [];   // 待发送附件 [{url,name,type,size,content}]

            // ---- 聊天附件（上传 → chip 展示 → 随消息发送） ----
            function renderAttachPanel() {
                if (!attachPanel) return;
                if (!pendingAttachments.length) {
                    attachPanel.style.display = 'none';
                    attachPanel.innerHTML = '';
                    return;
                }
                attachPanel.style.display = 'flex';
                attachPanel.style.flexWrap = 'wrap';
                attachPanel.style.gap = '8px';
                attachPanel.style.marginBottom = '6px';
                attachPanel.innerHTML = '';
                pendingAttachments.forEach((att, idx) => {
                    const chip = document.createElement('div');
                    chip.style.cssText = 'position:relative; display:flex; align-items:center; gap:6px; background:rgba(0,0,0,0.35); border:1px solid var(--border-mid); border-radius:10px; padding:4px 8px; max-width:220px; overflow:hidden;';
                    if (att.type === 'image') {
                        const img = document.createElement('img');
                        img.src = att.url;
                        img.style.cssText = 'width:34px; height:34px; object-fit:cover; border-radius:6px; flex:none;';
                        chip.appendChild(img);
                    } else {
                        chip.appendChild(document.createTextNode('📄'));
                    }
                    const name = document.createElement('span');
                    name.textContent = att.name;
                    name.style.cssText = 'font-size:12px; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;';
                    chip.appendChild(name);
                    const del = document.createElement('button');
                    del.textContent = '✕';
                    del.title = '移除附件';
                    del.style.cssText = 'border:none; background:transparent; color:var(--text-dim); cursor:pointer; font-size:12px; padding:0 2px; flex:none;';
                    del.addEventListener('click', function() {
                        pendingAttachments.splice(idx, 1);
                        renderAttachPanel();
                    });
                    chip.appendChild(del);
                    attachPanel.appendChild(chip);
                });
            }
            if (attachBtn && attachFileInput) {
                attachBtn.addEventListener('click', function() { attachFileInput.click(); });
                attachFileInput.addEventListener('change', function() {
                    const files = Array.from(this.files || []);
                    this.value = '';
                    if (!files.length) return;
                    files.forEach(file => {
                        const fd = new FormData();
                        fd.append('file', file);
                        fetch('/api/upload_file', { method: 'POST', body: fd })
                            .then(r => r.json())
                            .then(data => {
                                if (data.ok) {
                                    pendingAttachments.push({
                                        url: data.url, name: data.name, type: data.type,
                                        size: data.size, content: data.content || ''
                                    });
                                    renderAttachPanel();
                                } else {
                                    addBubble('❌ 上传失败（' + data.name + '）: ' + (data.message || '未知错误'), 'teacher');
                                }
                            })
                            .catch(err => addBubble('❌ 上传失败: ' + err.message, 'teacher'));
                    });
                });
            }
            function clearPendingAttachments() {
                pendingAttachments = [];
                renderAttachPanel();
            }
            function getPendingAttachments() {
                return pendingAttachments.map(a => ({
                    url: a.url, name: a.name, type: a.type,
                    content: (a.type === 'image' || a.type === 'file' && a.content) ? a.content : ''
                }));
            }

            // Topbar v2 DOM 引用（提前声明，避免 TDZ）
            const topbarCourseText       = document.getElementById('topbar-course-text');
            const topbarProgressCurrent  = document.getElementById('topbar-progress-current');
            const topbarProgressTotal    = document.getElementById('topbar-progress-total');
            const topbarProgressTitle    = document.getElementById('topbar-progress-title');
            const topbarProgressFill     = document.getElementById('topbar-progress-fill');

            // Galgame 对话条
            const dialogueBar = document.getElementById('dialogue-bar');
            const dialogueContent = document.getElementById('dialogue-content');
            const dialogueIndicator = document.getElementById('dialogue-indicator');
            let dialogueSegments = [];    // 分段文本数组
            let dialogueSegIdx = -1;      // 当前展示到第几段
            let dialogueStreaming = false;// 是否仍在流式接收
            let dialogueTypeTimer = null; // 打字机定时器

            const examList = document.getElementById('exam-list');
            const examGenerateBtn = document.getElementById('exam-generate-btn');
            const examSubmitBtn = document.getElementById('exam-submit-btn');
            const examTopic = document.getElementById('exam-topic');

            const resourceList = document.getElementById('resource-list');
            const downloadBtn = document.getElementById('download-btn');

            const lessonList = document.getElementById('lesson-list');
            const createLessonBtn = document.getElementById('create-lesson-btn');

            // 板书
            const boardUpload = document.getElementById('board-upload');
            const boardDeleteBtn = document.getElementById('board-delete-btn');
            const boardPreview = document.getElementById('board-preview');
            const boardCanvas = document.getElementById('board-canvas');
            const boardEmpty = document.getElementById('board-empty');
            let boardKeypoints = [];   // 识别的关键点 [{x,y,w,h}]
            let boardImageEl = null;   // 板书图片元素

            // 板书覆盖层（控制台 showBoard() 强制展示）
            const boardOverlay = document.getElementById('board-overlay');
            const boardOverlayCanvas = document.getElementById('board-overlay-canvas');
            const boardOverlayClose = document.getElementById('board-overlay-close');
            let boardOverlayKeypoints = [];

            const avatarUpload = document.getElementById('avatar-upload');
            const resetAvatarBtn = document.getElementById('reset-avatar-btn');
            const voiceSelect = document.getElementById('voice-select');
            const saveSettingsBtn = document.getElementById('save-settings-btn') || null;

            // 模型与 API 设置
            const chatProvider = document.getElementById('chat-provider');
            const chatBaseUrl = document.getElementById('chat-base-url');
            const chatApiKey = document.getElementById('chat-api-key');
            const chatModel = document.getElementById('chat-model');
            const ollamaBaseUrl = document.getElementById('ollama-base-url');
            const ollamaModel = document.getElementById('ollama-model');
            const chatTestBtn = document.getElementById('chat-test-btn');
            const chatTestStatus = document.getElementById('chat-test-status');
            const lessonProvider = document.getElementById('lesson-provider');
            const cloudBaseUrl = document.getElementById('cloud-base-url');
            const cloudApiKey = document.getElementById('cloud-api-key');
            const cloudModel = document.getElementById('cloud-model');
            const lessonSearch = document.getElementById('lesson-search');
            const lessonSearchVal = document.getElementById('lesson-search-val');
            const ttsProvider = document.getElementById('tts-provider');
            const ttsCloudBaseUrl = document.getElementById('tts-cloud-base-url');
            const ttsCloudModel = document.getElementById('tts-cloud-model');
            const ttsCloudVoice = document.getElementById('tts-cloud-voice');
            const ttsBaseUrl = document.getElementById('tts-base-url');
            const ttsTestBtn = document.getElementById('tts-test-btn');
            const ttsTestStatus = document.getElementById('tts-test-status');
            const voiceEnabled = document.getElementById('voice-enabled');
            const voiceEnabledVal = document.getElementById('voice-enabled-val');
            const visionEnabled = document.getElementById('vision-enabled');
            const visionEnabledVal = document.getElementById('vision-enabled-val');
            const visionBaseUrl = document.getElementById('vision-base-url');
            const visionApiKey = document.getElementById('vision-api-key');
            const visionModel = document.getElementById('vision-model');
            const personalityPromptInput = document.getElementById('personality-prompt-input');
            const lessonPromptInput = document.getElementById('lesson-prompt-input');

            // 模型与 API 字段显隐联动
            function syncModelFields() {
                const chatMode = (chatProvider.value || 'auto').toLowerCase();
                document.querySelectorAll('.chat-cloud-field').forEach(el => { el.style.display = (chatMode === 'cloud') ? '' : 'none'; });
                document.querySelectorAll('.chat-ollama-field').forEach(el => { el.style.display = (chatMode === 'ollama' || chatMode === 'auto') ? '' : 'none'; });
                const lessonMode = (lessonProvider.value || 'cloud').toLowerCase();
                document.querySelectorAll('.lesson-cloud-field').forEach(el => { el.style.display = (lessonMode === 'cloud') ? '' : 'none'; });
                const ttsMode = (ttsProvider.value || 'cloud').toLowerCase();
                document.querySelectorAll('.tts-cloud-field').forEach(el => { el.style.display = (ttsMode === 'cloud') ? '' : 'none'; });
                document.querySelectorAll('.tts-local-field').forEach(el => { el.style.display = (ttsMode === 'local') ? '' : 'none'; });
                const visionOn = visionEnabled.checked;
                document.querySelectorAll('.vision-field').forEach(el => { el.style.display = visionOn ? '' : 'none'; });
                if (lessonSearchVal) lessonSearchVal.textContent = lessonSearch.checked ? '开' : '关';
                if (visionEnabledVal) visionEnabledVal.textContent = visionOn ? '开' : '关';
                if (voiceEnabledVal) voiceEnabledVal.textContent = voiceEnabled.checked ? '开' : '关';
            }
            if (chatProvider) chatProvider.addEventListener('change', syncModelFields);
            if (lessonProvider) lessonProvider.addEventListener('change', syncModelFields);
            if (ttsProvider) ttsProvider.addEventListener('change', syncModelFields);
            if (lessonSearch) lessonSearch.addEventListener('change', syncModelFields);
            if (visionEnabled) visionEnabled.addEventListener('change', syncModelFields);
            if (voiceEnabled) voiceEnabled.addEventListener('change', syncModelFields);

            // 角色外观 & 背景设置
            const portraitPosX = document.getElementById('portrait-pos-x');
            const portraitPosY = document.getElementById('portrait-pos-y');
            const portraitScale = document.getElementById('portrait-scale');
            const portraitPosXVal = document.getElementById('portrait-pos-x-val');
            const portraitPosYVal = document.getElementById('portrait-pos-y-val');
            const portraitScaleVal = document.getElementById('portrait-scale-val');
            const bgPresetBtns = document.querySelectorAll('.bg-preset-btn');
            const bgUpload = document.getElementById('bg-upload');
            const bgResetBtn = document.getElementById('bg-reset-btn');

            // 回复分段设置
            const segmentEnabled = document.getElementById('segment-enabled');
            const segmentEnabledVal = document.getElementById('segment-enabled-val');
            const segmentMarker = document.getElementById('segment-marker');
            const segmentMarkerVal = document.getElementById('segment-marker-val');
            const segmentMaxLines = document.getElementById('segment-max-lines');
            const segmentMaxLinesVal = document.getElementById('segment-max-lines-val');

            // 侧栏宽度：由拖拽手柄控制，宽度值跟随系统配置（保存到后端，刷新后保持）
            const chatSidebar = document.getElementById('chat-sidebar');
            const sidebarResizer = document.getElementById('sidebar-resizer');
            const SIDEBAR_WIDTH_DEFAULT = 36;
            const SIDEBAR_WIDTH_MIN = 25;
            const SIDEBAR_WIDTH_MAX = 60;
            let currentSidebarWidth = SIDEBAR_WIDTH_DEFAULT;
            function applySidebarWidth(pct) {
                currentSidebarWidth = clampNum(pct, SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX, SIDEBAR_WIDTH_DEFAULT);
                document.documentElement.style.setProperty('--sidebar-width', currentSidebarWidth + '%');
            }

            // Live2D 外观配置（从后端读取，用于模型位置/缩放/自定义模型）
            const LIVE2D_DEFAULTS = { posX: 50, posY: 50, scale: 32, bgTheme: 'warm', bgUrl: '', modelUrl: '/static/models/my_teacher/female_01Arkit_6.model3.json' };
            let live2dSettings = Object.assign({}, LIVE2D_DEFAULTS);
            let _loadedModelUrl = '';   // 当前已加载的模型 URL

            let isStreaming = false;
            let currentLesson = 'default';

            // ============================
            // 2. Live2D 动作系统
            let live2dModel = null;
            let live2dApp = null;

            // ============================
            // 注视跟随（自绘） + 5 秒不动回正（保留呼吸/摆动）
            // 背景：pl2d 原生 autoFocus 把画布坐标按"模型坐标归一化"映射（模型中心为原点）。
            // 本模型中心贴在画布底部，鼠标在画布中上部时会被归一化到很大的负 ty →
            // 视线总是落在鼠标上方（用户反馈的"盯着鼠标上面一点"）。因此关闭原生 autoFocus，
            // 改为以画布内"头部中性点"为基准、按画布比例映射鼠标位移为注视方向。
            // 全局只注册一次监听；模型重载后通过 live2dApp.ticker.add(_gazeResetTicker) 重挂 ticker。
            // ============================
            const _GAZE_IDLE_MS = 5000;      // 无鼠标活动多少毫秒后回正
            const _GAZE_RESET_MS = 1200;     // 回正过渡时长（ease-out cubic）
            let _gazeLastActiveTs = Date.now();
            let _gazeResetting = false;
            let _gazeResetStartTs = 0;
            let _gazeHasEverMoved = false;
            let _gazeResetFrom = { x: 0, y: 0 };

            function _feedGazeFocus(e) {
                const model = live2dModel;
                if (!model || !model.internalModel || !model.internalModel.focusController) return;
                const canvasEl = document.getElementById('live2d-canvas');
                if (!canvasEl) return;
                const r = canvasEl.getBoundingClientRect();
                if (!r.width || !r.height) return;
                let dx, dy;
                try {
                    // 头部中性点：画布上方 30% 处（本模型头部位于画布上部；模型位置调整后仍近似成立）
                    const neutralY = r.height * 0.30;
                    const radiusX = r.width * 0.55;   // 水平满幅半径
                    const radiusY = r.height * 0.42;  // 垂直满幅半径
                    dx = (e.clientX - r.left - r.width * 0.5) / radiusX;
                    dy = (e.clientY - r.top - neutralY) / radiusY;
                } catch (err) { return; }
                const len = Math.sqrt(dx * dx + dy * dy);
                if (len > 1) { dx /= len; dy /= len; }
                // PIXI 屏幕 y 向下为正；focusController y 向上为正（+y = 抬头）
                model.internalModel.focusController.focus(dx, -dy, false);
            }

            function _gazeOnPointerMove(e) {
                _gazeLastActiveTs = Date.now();
                _gazeResetting = false;  // 用户一动，立即取消回正渐变
                _gazeHasEverMoved = true;
                _feedGazeFocus(e);
            }

            function _gazeResetTicker() {
                if (!live2dModel || !live2dModel.internalModel) return;
                const fc = live2dModel.internalModel.focusController;
                if (!fc) return;
                const idleMs = Date.now() - _gazeLastActiveTs;
                if (_gazeHasEverMoved && idleMs >= _GAZE_IDLE_MS && !_gazeResetting) {
                    _gazeResetting = true;
                    _gazeResetStartTs = Date.now();
                    _gazeResetFrom = { x: fc.x, y: fc.y };
                }
                if (!_gazeResetting) return;
                const progress = Math.min(1, (Date.now() - _gazeResetStartTs) / _GAZE_RESET_MS);
                const ease = 1 - Math.pow(1 - progress, 3); // ease-out cubic
                // instant=true 直接置位，避免 focusController 自身插值干扰渐变
                fc.focus(_gazeResetFrom.x * (1 - ease), _gazeResetFrom.y * (1 - ease), true);
                if (progress >= 1) _gazeResetting = false;
            }

            // 全局只注册一次（防 reloadLive2DModel 重复累积监听）
            if (!window._gazeListenersReady) {
                window._gazeListenersReady = true;
                document.addEventListener('mousemove', _gazeOnPointerMove);
            }

            // 轻量 toast 提示（无依赖）
            function showToast(msg, ms) {
                try {
                    let el = document.getElementById('global-toast');
                    if (!el) {
                        el = document.createElement('div');
                        el.id = 'global-toast';
                        el.style.cssText = 'position:fixed; left:50%; bottom:64px; transform:translateX(-50%); background:rgba(20,14,8,0.92); color:#f5e6cf; font-size:13px; padding:8px 16px; border-radius:10px; border:1px solid rgba(212,163,115,0.4); z-index:99999; max-width:80vw; box-shadow:0 4px 18px rgba(0,0,0,0.35);';
                        document.body.appendChild(el);
                    }
                    el.textContent = msg;
                    el.style.display = 'block';
                    if (el._toastTimer) clearTimeout(el._toastTimer);
                    el._toastTimer = setTimeout(function() { el.style.display = 'none'; }, ms || 2600);
                } catch (e) {}
            }

            // 预设动作映射：语义化动作名 → 模型动作组
            const ACTION_MAP = {
                'point': 'think',       // 指向 → 思考动作
                'blackboard': 'speak',  // 拉黑板 → 说话动作
                'greet': 'wave',    // 打招呼 → 在 triggerAction 中走自定义挥手（模型无可用 hello 动作）
                'hello': 'wave',    // 打招呼（点击手/头）
                'idle': 'idle',
                'listen': 'listen',
                'speak': 'speak',
                'think': 'think',
            };

            // ============================================================
            // 语义动作引擎（借鉴 ActingDoll 的 set_parameter 参数级控制思路）：
            // 不依赖 motion3.json 预设文件，用 ARKit 参数时间轴直接驱动模型。
            // 头部：ParamAngleX(左右转)/Y(上下点头)/Z(侧歪)；身体：ParamBodyAngleX/Y/Z
            // ============================================================
            const SEMANTIC_ACTIONS = {
                nod: { dur: 800, frames: [ // 点头（同意/肯定）
                    { t: 0, params: { ParamAngleY: 0 } },
                    { t: 0.25, params: { ParamAngleY: -16 } },
                    { t: 0.5, params: { ParamAngleY: 0 } },
                    { t: 0.75, params: { ParamAngleY: -16 } },
                    { t: 1, params: { ParamAngleY: 0 } },
                ] },
                shake: { dur: 900, frames: [ // 摇头（否定/不赞成）
                    { t: 0, params: { ParamAngleX: 0 } },
                    { t: 0.15, params: { ParamAngleX: 14 } },
                    { t: 0.3, params: { ParamAngleX: -14 } },
                    { t: 0.45, params: { ParamAngleX: 14 } },
                    { t: 0.6, params: { ParamAngleX: -14 } },
                    { t: 0.8, params: { ParamAngleX: 0 } },
                    { t: 1, params: { ParamAngleX: 0 } },
                ] },
                tilt: { dur: 1200, frames: [ // 歪头（疑惑/好奇）
                    { t: 0, params: { ParamAngleZ: 0 } },
                    { t: 0.4, params: { ParamAngleZ: 16 } },
                    { t: 0.8, params: { ParamAngleZ: 16 } },
                    { t: 1, params: { ParamAngleZ: 0 } },
                ] },
                bow: { dur: 1600, frames: [ // 鞠躬（开场问好/结束道别）
                    { t: 0, params: { ParamBodyAngleX: 0, ParamAngleY: 0 } },
                    { t: 0.25, params: { ParamBodyAngleX: -26, ParamAngleY: -16 } },
                    { t: 0.6, params: { ParamBodyAngleX: -26, ParamAngleY: -16 } },
                    { t: 0.85, params: { ParamBodyAngleX: -8, ParamAngleY: -5 } },
                    { t: 1, params: { ParamBodyAngleX: 0, ParamAngleY: 0 } },
                ] },
                gasp: { dur: 1400, frames: [ // 惊讶（睁大眼+张嘴+挑眉）
                    { t: 0, params: { ParamJawOpen: 0, ParamBrowLForm: 0, ParamBrowRForm: 0 } },
                    { t: 0.2, params: { ParamJawOpen: 0.7, ParamBrowLForm: 1, ParamBrowRForm: 1 } },
                    { t: 0.6, params: { ParamJawOpen: 0.5, ParamBrowLForm: 1, ParamBrowRForm: 1 } },
                    { t: 1, params: { ParamJawOpen: 0, ParamBrowLForm: 0, ParamBrowRForm: 0 } },
                ] },
                cheer: { dur: 1200, frames: [ // 雀跃（学生答对/值得庆祝时）
                    { t: 0, params: { ParamBodyAngleX: 0, ParamMouthSmile: 0 } },
                    { t: 0.2, params: { ParamBodyAngleX: 9, ParamMouthSmile: 1 } },
                    { t: 0.35, params: { ParamBodyAngleX: -9, ParamMouthSmile: 1 } },
                    { t: 0.5, params: { ParamBodyAngleX: 9, ParamMouthSmile: 1 } },
                    { t: 0.65, params: { ParamBodyAngleX: -9, ParamMouthSmile: 1 } },
                    { t: 0.85, params: { ParamBodyAngleX: 0, ParamMouthSmile: 1 } },
                    { t: 1, params: { ParamBodyAngleX: 0, ParamMouthSmile: 0 } },
                ] },
                sigh: { dur: 1500, frames: [ // 叹气（遗憾/无奈）
                    { t: 0, params: { ParamAngleY: 0, ParamBodyAngleX: 0, MouthFrownLeft: 0, MouthFrownRight: 0 } },
                    { t: 0.3, params: { ParamAngleY: -12, ParamBodyAngleX: -8, MouthFrownLeft: 0.3, MouthFrownRight: 0.3 } },
                    { t: 0.7, params: { ParamAngleY: -12, ParamBodyAngleX: -8, MouthFrownLeft: 0.3, MouthFrownRight: 0.3 } },
                    { t: 1, params: { ParamAngleY: 0, ParamBodyAngleX: 0, MouthFrownLeft: 0, MouthFrownRight: 0 } },
                ] },
                agree: { dur: 600, frames: [ // 赞许点头（表扬学生）
                    { t: 0, params: { ParamAngleY: 0, ParamMouthSmile: 0 } },
                    { t: 0.3, params: { ParamAngleY: -14, ParamMouthSmile: 0.8 } },
                    { t: 0.6, params: { ParamAngleY: 0, ParamMouthSmile: 0.8 } },
                    { t: 1, params: { ParamAngleY: 0, ParamMouthSmile: 0 } },
                ] },
            };

            let _semTicker = null;  // 语义动作 ticker 回调
            function runParamMotion(keyframes, duration, intensity) {
                if (!live2dApp || !live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return false;
                stopParamMotion();
                const core = live2dModel.internalModel.coreModel;
                const amp = (intensity == null ? 1 : Math.max(0.1, Math.min(1, Number(intensity) / 100)));
                const kf = keyframes || [];
                if (!kf.length) return false;
                const start = Date.now();
                const dur = duration || 800;
                const tickerFn = function() {
                    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                    const c = live2dModel.internalModel.coreModel;
                    const t = Math.min(1, (Date.now() - start) / dur);
                    let a = kf[0], b = kf[kf.length - 1];
                    for (let i = 0; i < kf.length - 1; i++) {
                        if (t >= kf[i].t && t <= kf[i + 1].t) { a = kf[i]; b = kf[i + 1]; break; }
                    }
                    const segT = (b.t === a.t) ? 0 : (t - a.t) / (b.t - a.t);
                    const allIds = {};
                    kf.forEach(function(k) { Object.keys(k.params).forEach(function(id) { allIds[id] = 1; }); });
                    Object.keys(allIds).forEach(function(id) {
                        const va = (a.params[id] == null ? 0 : a.params[id]);
                        const vb = (b.params[id] == null ? 0 : b.params[id]);
                        _coreSetParam(c, id, (va + (vb - va) * segT) * amp);
                    });
                    if (t >= 1) {
                        try { live2dApp.ticker.remove(tickerFn); } catch (e) {}
                        _semTicker = null;
                    }
                };
                _semTicker = tickerFn;
                live2dApp.ticker.add(tickerFn);
                return true;
            }
            function stopParamMotion() {
                if (_semTicker && live2dApp) {
                    try { live2dApp.ticker.remove(_semTicker); } catch (e) {}
                    _semTicker = null;
                }
            }
            window.getSemanticActionNames = function() { return Object.keys(SEMANTIC_ACTIONS); };

            // 合并用户自定义动作到 ACTION_MAP（名字 → 模型动作ID），供 /action 与 AI 使用
            // 兼容两种格式：旧 {名字: "动作ID"} 与 新 {名字: {id, intensity}}
            function mergeCustomActions(map) {
                if (!map || typeof map !== 'object') return;
                for (const k in map) {
                    const v = map[k];
                    if (typeof v === 'string') ACTION_MAP[k] = v.trim();
                    else if (v && v.id) ACTION_MAP[k] = String(v.id).trim();
                }
            }

            // ============================
            // 自定义动作编辑器（设置页最底部）
            // state: { UI名字: { id: 模型动作ID, intensity: 0-100 } }
            // ============================
            let customActionState = {};

            function renderCustomActionsList() {
                const list = document.getElementById('custom-actions-list');
                if (!list) return;
                const names = Object.keys(customActionState);
                if (!names.length) {
                    list.innerHTML = '<div id="custom-actions-empty">暂无自定义动作。点击"＋ 添加动作"，填模型动作 ID 和 UI 名字即可。</div>';
                    return;
                }
                let html = '';
                names.forEach(function(name) {
                    const it = customActionState[name] || {};
                    const id = (it.id || '');
                    const intensity = (it.intensity == null ? 70 : it.intensity);
                    html +=
                        '<div class="ca-row" data-name="' + name.replace(/"/g, '&quot;') + '">' +
                            '<div class="ca-fields">' +
                                '<input class="ca-id" value="' + id.replace(/"/g, '&quot;') + '" placeholder="模型动作ID（wave/speak/think…）">' +
                                '<span class="ca-arrow">→</span>' +
                                '<input class="ca-name" value="' + name.replace(/"/g, '&quot;') + '" placeholder="UI名字（触发关键字）">' +
                                '<button class="ca-del" title="删除该动作">✕</button>' +
                            '</div>' +
                            '<div class="ca-slider-row">' +
                                '<span>强度</span>' +
                                '<input type="range" class="ca-int" min="0" max="100" value="' + intensity + '">' +
                                '<span class="ca-int-val">' + intensity + '%</span>' +
                                '<span class="ca-hint" style="color:var(--text-dim);">松开滑条实时预览</span>' +
                            '</div>' +
                        '</div>';
                });
                list.innerHTML = html;
            }

            function bindCustomActionsEvents() {
                const list = document.getElementById('custom-actions-list');
                const addBtn = document.getElementById('custom-action-add');
                if (!list || !addBtn) return;
                list.addEventListener('change', function(e) {
                    const row = e.target.closest('.ca-row');
                    if (!row) return;
                    const oldName = row.getAttribute('data-name');
                    const it = customActionState[oldName] || {};
                    if (e.target.classList.contains('ca-id')) {
                        it.id = e.target.value.trim();
                        customActionState[oldName] = it;
                    } else if (e.target.classList.contains('ca-name')) {
                        const newName = e.target.value.trim();
                        if (newName && newName !== oldName) {
                            delete customActionState[oldName];
                            customActionState[newName] = it;
                            renderCustomActionsList();  // 名字是 key，需重渲染
                        } else if (!newName) {
                            e.target.value = oldName;   // 名字不能为空
                        }
                    } else if (e.target.classList.contains('ca-int')) {
                        it.intensity = parseInt(e.target.value, 10) || 0;
                        customActionState[oldName] = it;
                        const valSpan = row.querySelector('.ca-int-val');
                        if (valSpan) valSpan.textContent = it.intensity + '%';
                        // 松开滑条 → 实时预览该动作（调试）
                        if (it.id) {
                            if (typeof triggerAction === 'function') {
                                Promise.resolve(triggerAction(it.id, { intensity: it.intensity })).catch(function() {});
                            } else {
                                try { live2dModel.motion(it.id, 0, 3); } catch (err) {}
                            }
                        }
                    }
                });
                // 滑条拖动过程只更新数值，不触发预览（避免动作队列刷屏）
                list.addEventListener('input', function(e) {
                    if (e.target.classList.contains('ca-int')) {
                        const row = e.target.closest('.ca-row');
                        if (!row) return;
                        const oldName = row.getAttribute('data-name');
                        const it = customActionState[oldName] || {};
                        it.intensity = parseInt(e.target.value, 10) || 0;
                        customActionState[oldName] = it;
                        const valSpan = row.querySelector('.ca-int-val');
                        if (valSpan) valSpan.textContent = it.intensity + '%';
                    }
                });
                list.addEventListener('click', function(e) {
                    const del = e.target.closest('.ca-del');
                    if (!del) return;
                    const row = del.closest('.ca-row');
                    if (!row) return;
                    const name = row.getAttribute('data-name');
                    delete customActionState[name];
                    delete ACTION_MAP[name];
                    renderCustomActionsList();
                });
                addBtn.addEventListener('click', function() {
                    // 生成一个不冲突的临时名字
                    let name = '新动作';
                    let n = 1;
                    while (customActionState[name]) { name = '新动作' + (++n); }
                    customActionState[name] = { id: '', intensity: 70 };
                    renderCustomActionsList();
                    const rows = list.querySelectorAll('.ca-row');
                    const last = rows[rows.length - 1];
                    if (last) {
                        const idInput = last.querySelector('.ca-id');
                        if (idInput) { idInput.focus(); idInput.select(); }
                    }
                });
            }
            bindCustomActionsEvents();

            // 点击命中区域 → 语义化动作（基于模型 .model3.json 的 HitAreas）
            // 模型实际 HitAreas: Head, Body, Legs, HandR, HandL
            const HIT_AREA_ACTION_MAP = {
                'Head': 'hello',    // 点击头 → 打招呼
                'HandR': 'hello',   // 点击右手 → 打招呼
                'HandL': 'hello',   // 点击左手 → 打招呼
                'Body': 'think',    // 点击身体 → 思考
                'Legs': 'think',    // 点击腿 → 思考
            };

            // 动作索引表：{ 动作组名: 该组动作数量 }
            let motionIndex = {};
            let availableMotions = [];
            let motionIndexLoaded = false;

            // 从 model3.json 读取动作索引（页面加载时执行，作为后备数据源）
            function loadMotionIndex() {
                fetch('/static/models/my_teacher/female_01Arkit_6.model3.json')
                    .then(r => r.json())
                    .then(data => {
                        const motions = (data.FileReferences && data.FileReferences.Motions) || {};
                        const names = Object.keys(motions);
                        if (names.length) {
                            availableMotions = names;
                            names.forEach(name => {
                                motionIndex[name] = (motions[name] || []).length;
                            });
                            motionIndexLoaded = true;
                        }
                        console.log('Live2D: 动作索引 =', availableMotions.map(n => n + '(' + motionIndex[n] + ')').join(', '));
                    })
                    .catch(err => console.warn('Live2D: 无法读取动作索引', err));
            }
            loadMotionIndex();

            // 模型加载成功后从 motionManager 直接读取动作（最可靠，保证时序）
            function loadMotionIndexFromModel(model) {
                try {
                    const mm = model.internalModel && model.internalModel.motionManager;
                    if (mm && mm.definitions) {
                        const defs = mm.definitions;
                        const names = Object.keys(defs);
                        if (names.length) {
                            availableMotions = names;
                            names.forEach(name => {
                                motionIndex[name] = (defs[name] || []).length;
                            });
                            motionIndexLoaded = true;
                            console.log('Live2D: 模型动作索引 =', names.map(n => n + '(' + motionIndex[n] + ')').join(', '));
                        }
                    }
                } catch (e) {
                    console.warn('Live2D: 无法从模型读取动作', e);
                }
            }

            // 检查动作是否可用（索引未加载时放行，避免时序竞态误拒）
            function isMotionAvailable(motionName) {
                if (!motionIndexLoaded) return true;
                return motionIndex[motionName] > 0;
            }

            // 动作队列：连续触发排队播放，避免抢占
            let motionQueue = [];
            let motionPlaying = false;
            let onActionCallback = null;
            // 动作开始/结束回调（细粒度，可选）
            let onActionStartCallback = null;
            let onActionEndCallback = null;

            // 等待当前动作播放完成（轮询，最多 10 秒防卡死）
            function waitForMotionEnd() {
                return new Promise(function(resolve) {
                    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.motionManager) {
                        resolve();
                        return;
                    }
                    const mm = live2dModel.internalModel.motionManager;
                    let tries = 0;
                    const check = function() {
                        tries++;
                        try {
                            if (mm.isFinished() || tries > 100) {
                                resolve();
                            } else {
                                setTimeout(check, 100);
                            }
                        } catch (e) {
                            resolve();
                        }
                    };
                    check();
                });
            }

            // 动作队列处理器：依次播放队列中的动作
            async function processMotionQueue() {
                if (motionPlaying) return;
                motionPlaying = true;
                while (motionQueue.length) {
                    const item = motionQueue.shift();
                    const startedAt = Date.now();
                    try {
                        const result = await live2dModel.motion(item, 0, 3); // FORCE 优先级
                        console.log('Live2D: 开始播放', item, '| motion()结果:', result);
                        // 触发开始回调（语义名 + 实际动作组）
                        if (typeof onActionStartCallback === 'function') {
                            try { onActionStartCallback(item, item); } catch (e) {}
                        }
                        await waitForMotionEnd();
                        console.log('Live2D: 动作完成', item, '| 耗时', Date.now() - startedAt, 'ms');
                        // 触发结束回调
                        if (typeof onActionEndCallback === 'function') {
                            try { onActionEndCallback(item, item); } catch (e) {}
                        }
                    } catch (e) {
                        console.warn('Live2D: 动作播放失败', item, e);
                        if (typeof onActionEndCallback === 'function') {
                            try { onActionEndCallback(item, item); } catch (e) {}
                        }
                    }
                }
                motionPlaying = false;
            }

            // 触发动作接口：供点击 / 对话 / 外部模块调用
            // 返回 Promise，便于调用方（如 /action 命令）感知成功与否
            function triggerAction(actionName, opts) {
                opts = opts || {};
                if (!live2dModel) {
                    console.warn('Live2D not loaded, cannot trigger:', actionName);
                    return Promise.reject(new Error('Live2D 未加载'));
                }
                // 自定义动作强度：优先调用方传入；否则取设置里该动作保存的强度
                let intensity = opts.intensity;
                if (intensity == null && customActionState[actionName]) {
                    intensity = customActionState[actionName].intensity;
                }
                // 自定义动作：挥手（模型手指参数未绑定，用唯一有效的叉腰参数 Param40/43 + 身体摆动模拟）
                if (actionName === 'wave') {
                    waveHands(undefined, intensity);
                    console.log('Live2D 触发动作: wave（自定义挥手）');
                    if (typeof onActionCallback === 'function') {
                        onActionCallback(actionName, 'wave');
                    }
                    return Promise.resolve(true);
                }
                // 打招呼：模型 hello 动作组参数（Action01/WaveHello）不存在，改为自定义挥手
                if (actionName === 'hello' || actionName === 'greet') {
                    waveHands(undefined, intensity);
                    console.log('Live2D 触发动作:', actionName, '（挥手打招呼）');
                    if (typeof onActionCallback === 'function') {
                        onActionCallback(actionName, 'wave');
                    }
                    return Promise.resolve(true);
                }
                // 语义动作：参数时间轴直接驱动（不依赖 motion3 预设文件），如 nod/shake/tilt/bow/gasp/cheer/sigh/agree
                if (SEMANTIC_ACTIONS[actionName]) {
                    runParamMotion(SEMANTIC_ACTIONS[actionName].frames, SEMANTIC_ACTIONS[actionName].dur, intensity);
                    console.log('Live2D 触发语义动作:', actionName);
                    if (typeof onActionCallback === 'function') {
                        onActionCallback(actionName, actionName);
                    }
                    return Promise.resolve(true);
                }
                const motion = ACTION_MAP[actionName] || actionName;
                if (!isMotionAvailable(motion)) {
                    console.warn('Live2D: 动作不存在:', motion, '| 可用:', availableMotions);
                }
                // 加入队列播放（校验失败也尝试，motion() 内部会自行拒绝不存在的组）
                motionQueue.push(motion);
                processMotionQueue();
                console.log('Live2D 触发动作:', actionName, '→', motion, '| 队列长度:', motionQueue.length);
                // 兼容旧 API：触发通用回调
                if (typeof onActionCallback === 'function') {
                    onActionCallback(actionName, motion);
                }
                return Promise.resolve(true);
            }

            // 自定义"挥手"动作：模型手指参数（Hand L/R）未绑定到部件、无法驱动；
            // 唯一有效的可见手部参数是 Param40/Param43（叉腰，手臂位移≈10-36px），
            // 用缓动驱动叉腰 0→1→0 + 身体左右微摆，模拟双手摆动的打招呼效果
            let _waveTicker = null;
            let _waveTimer = null;
            function waveHands(duration, intensity) {
                if (!live2dApp || !live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                if (_waveTimer) clearTimeout(_waveTimer);
                if (_waveTicker) {
                    try { live2dApp.ticker.remove(_waveTicker); } catch (e) {}
                    _waveTicker = null;
                }
                const core = live2dModel.internalModel.coreModel;
                const start = Date.now();
                const dur = duration || 3200;
                // 强度（0-100 → 0-1 幅度），默认 100%
                const amp = (intensity == null ? 1 : Math.max(0, Math.min(100, Number(intensity))) / 100);
                const tickerFn = function() {
                    if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                    const c = live2dModel.internalModel.coreModel;
                    const t = (Date.now() - start) / dur;
                    if (t >= 1) {
                        _coreSetParam(c, 'Param40', 0);
                        _coreSetParam(c, 'Param43', 0);
                        _coreSetParam(c, 'ParamBodyAngleX', 0);
                        _coreSetParam(c, 'ParamMouthSmile', 0);
                        try { live2dApp.ticker.remove(tickerFn); } catch (e) {}
                        _waveTicker = null;
                        return;
                    }
                    // 叉腰缓动：0→1（前30%）保持（中间）→1→0（最后20%）
                    let akimbo = 1;
                    if (t < 0.3) akimbo = t / 0.3;
                    else if (t > 0.8) akimbo = Math.max(0, 1 - (t - 0.8) / 0.2);
                    _coreSetParam(c, 'Param40', akimbo * amp);
                    _coreSetParam(c, 'Param43', akimbo * amp);
                    // 身体左右摆动两轮 + 微笑
                    _coreSetParam(c, 'ParamBodyAngleX', Math.sin(t * Math.PI * 4) * 6 * amp);
                    _coreSetParam(c, 'ParamMouthSmile', 0.6 * amp);
                };
                _waveTicker = tickerFn;
                live2dApp.ticker.add(tickerFn);
            }

            // ============================================================
            // 参数直调：/param 命令 + AI [PARAM:...] 协议共用
            // 借鉴 ActingDoll 的 set_parameter：直接对模型参数赋值（带渐变）
            // ============================================================
            const PARAM_RANGES = {
                ParamAngleX: [-45, 45], ParamAngleY: [-45, 45], ParamAngleZ: [-45, 45],
                ParamBodyAngleX: [-45, 45], ParamBodyAngleY: [-45, 45], ParamBodyAngleZ: [-45, 45],
                ParamEyeLOpen: [0, 1], ParamEyeROpen: [0, 1],
                ParamJawOpen: [0, 1], ParamMouthOpenY: [0, 1],
                ParamMouthSmile: [0, 1], ParamEyeLSmile: [0, 1], ParamEyeRSmile: [0, 1],
                ParamMouthPucker: [0, 1], ParamMouthFunnel: [0, 1],
                ParamBrowLForm: [0, 1], ParamBrowRForm: [0, 1],
                ParamBrowLAngle: [-1, 1], ParamBrowRAngle: [-1, 1],
                ParamEyeBallX: [-1, 1], ParamEyeBallY: [-1, 1],
                MouthFrownLeft: [0, 1], MouthFrownRight: [0, 1],
                ParamBreath: [0, 1], Param40: [0, 1], Param43: [0, 1],
            };
            function clampParamValue(name, v) {
                const r = PARAM_RANGES[name];
                if (r) return Math.max(r[0], Math.min(r[1], v));
                return Math.max(-100, Math.min(100, v));
            }
            function _getParamValue(core, id) {
                if (!core) return 0;
                try {
                    let idx = -1;
                    if (typeof core.getParameterIndex === 'function') idx = core.getParameterIndex(id);
                    else if (typeof core.getParamIndex === 'function') idx = core.getParamIndex(id);
                    if (idx >= 0) {
                        if (typeof core.getParameterValueByIndex === 'function') return core.getParameterValueByIndex(idx);
                        if (typeof core.getParameterValueById === 'function') return core.getParameterValueById(id);
                        if (typeof core.getParamFloat === 'function') return core.getParamFloat(idx);
                        if (typeof core.getParameterFloat === 'function') return core.getParameterFloat(idx);
                    }
                } catch (e) {}
                return 0;
            }
            let _paramTickerFn = null;
            let _paramResetTimer = null;
            function setModelParam(name, value, duration, autoResetMs) {
                // 渐变设置单个参数；autoResetMs 指定后自动渐变恢复原值（供 AI 短暂动作用）
                if (!live2dApp || !live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return false;
                const v = clampParamValue(name, Number(value));
                if (isNaN(v)) return false;
                try {
                    if (_paramTickerFn) { live2dApp.ticker.remove(_paramTickerFn); _paramTickerFn = null; }
                    if (_paramResetTimer) { clearTimeout(_paramResetTimer); _paramResetTimer = null; }
                } catch (e) {}
                const core = live2dModel.internalModel.coreModel;
                const from = _getParamValue(core, name);
                const dur = (duration == null ? 300 : Math.max(1, Number(duration)));
                if (dur <= 0 || Math.abs(v - from) < 0.001) {
                    _coreSetParam(core, name, v);
                } else {
                    const start = Date.now();
                    const tickerFn = function() {
                        if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                        const t = Math.min(1, (Date.now() - start) / dur);
                        _coreSetParam(live2dModel.internalModel.coreModel, name, from + (v - from) * t);
                        if (t >= 1) {
                            try { live2dApp.ticker.remove(tickerFn); } catch (e) {}
                            if (_paramTickerFn === tickerFn) _paramTickerFn = null;
                        }
                    };
                    _paramTickerFn = tickerFn;
                    live2dApp.ticker.add(tickerFn);
                }
                // 可选自动恢复
                if (autoResetMs > 0) {
                    _paramResetTimer = setTimeout(function() {
                        _paramResetTimer = null;
                        setModelParam(name, from, Math.min(500, autoResetMs / 2), 0);
                    }, autoResetMs);
                }
                return true;
            }
            function setModelParams(dict, duration, autoResetMs) {
                if (!dict) return false;
                Object.keys(dict).forEach(function(k) { setModelParam(k, dict[k], duration, autoResetMs); });
                return true;
            }
            function resetModelPose() {
                // 恢复头部/身体角度与常用参数为默认
                const zeros = {
                    'ParamAngleX': 0, 'ParamAngleY': 0, 'ParamAngleZ': 0,
                    'ParamBodyAngleX': 0, 'ParamBodyAngleY': 0, 'ParamBodyAngleZ': 0,
                };
                Object.keys(zeros).forEach(function(k) {
                    setModelParam(k, 0, 400, 0);
                });
            }
            // 暴露到全局，供 /param 命令与 AI 参数协议使用
            window.setLive2DParam = setModelParam;
            window.setLive2DParams = setModelParams;
            window.resetLive2DPose = resetModelPose;
            window.getLive2DParamList = function() { return Object.keys(PARAM_RANGES); };

            // 暴露到全局，供外部调用
            window.triggerLive2DAction = triggerAction;
            window.getLive2DModel = function() { return live2dModel; };
            window.setLive2DActionCallback = function(cb) { onActionCallback = cb; };

            // 角色左/右手"指向"画面坐标 (x, y)：头部转向目标 + 身体轻微侧转 + 触发指向动作
            function pointerTo(side, targetX, targetY) {
                if (!live2dApp || !live2dModel || !live2dModel.internalModel) return;
                try {
                    const core = live2dModel.internalModel.coreModel;
                    const modelX = live2dApp.screen.width * (live2dSettings.posX / 100);
                    const modelY = live2dApp.screen.height * (live2dSettings.posY / 100);
                    const dx = targetX - modelX;
                    const dy = targetY - modelY;
                    // 头部转向（clamp ±25/±20）
                    const angleX = Math.max(-25, Math.min(25, dx * 0.08));
                    const angleY = Math.max(-20, Math.min(20, dy * 0.08));
                    _coreSetParam(core, 'ParamAngleX', angleX);
                    _coreSetParam(core, 'ParamAngleY', angleY);
                    // 身体轻微侧转（指向一侧），ARKit 模型支持 ParamBodyAngleX
                    _coreSetParam(core, 'ParamBodyAngleX', side === 'left' ? -8 : 8);
                    // 触发指向动作（点屏幕时已有的语义）
                    triggerAction('point');
                    // 2 秒后恢复
                    setTimeout(function() {
                        if (live2dModel && live2dModel.internalModel) {
                            _coreSetParam(core, 'ParamAngleX', 0);
                            _coreSetParam(core, 'ParamAngleY', 0);
                            _coreSetParam(core, 'ParamBodyAngleX', 0);
                        }
                    }, 2000);
                    console.log('[pointer] 指向:', side, targetX, targetY, '| 角度:', Math.round(angleX), Math.round(angleY));
                } catch (e) {
                    console.warn('[pointer] 指向失败:', e);
                }
            }
            window.setLive2DActionStartCallback = function(cb) { onActionStartCallback = cb; };
            window.setLive2DActionEndCallback = function(cb) { onActionEndCallback = cb; };
            window.getLive2DAvailableMotions = function() { return availableMotions.slice(); };
            window.setLive2DEmotion = setEmotion;

            // ====================================================
            // 情绪映射（Open-LLM-VTuber 协议）
            // ====================================================
            // 模型表情/动作索引（启动时从 /api/live2d/model_info 加载）
            let _live2dExpressions = [];   // [{index, name, file}]
            let _live2dMotions = {};      // {groupName: [{index, file}]}
            let _live2dDefaultEmotionMap = null;

            // 当前情绪映射表：emotion 名 → 模型 expression index
            // 默认与 Open-LLM-VTuber 文档一致，用户可在设置中改
            let _emotionExpressionMap = {
                neutral: 0, happy: 3, sad: 1, angry: 2, surprised: 3,
                fear: 1, disgust: 2, smirk: 3, think: 0,
            };

            // 加载模型表情/动作清单
            function loadLive2DModelInfo() {
                fetch('/api/live2d/model_info')
                    .then(r => r.ok ? r.json() : null)
                    .then(info => {
                        if (!info) return;
                        _live2dExpressions = info.expressions || [];
                        _live2dMotions = info.motions || {};
                        if (info.default_emotion_map) {
                            _live2dDefaultEmotionMap = info.default_emotion_map;
                            // 合并默认（用户在前端配置过的话会覆盖默认）
                            if (!_userCustomizedEmotionMap) {
                                _emotionExpressionMap = Object.assign({}, info.default_emotion_map, _emotionExpressionMap);
                            }
                        }
                        console.log('[Live2D] 模型信息已加载:',
                            _live2dExpressions.length, '个表情,',
                            Object.keys(_live2dMotions).length, '组动作');
                    })
                    .catch(err => console.warn('[Live2D] 加载模型信息失败:', err));
            }

            let _userCustomizedEmotionMap = false;

            // 兼容 Cubism2 与 Cubism4 两套 runtime 接口。
            // 实测 Cubism4（pl2d CubismModel）可用：setParameterValueByIndex / setParameterValueById / getParameterValueByIndex；
            // Cubism2（pl2d Cubism2Model）用 getParamIndex / setParamFloat。
            function _coreSetParam(core, id, val) {
                let idx = -1;
                if (core && typeof core.getParameterIndex === 'function') {
                    idx = core.getParameterIndex(id);
                } else if (core && typeof core.getParamIndex === 'function') {
                    idx = core.getParamIndex(id);
                }
                if (idx >= 0) {
                    if (core && typeof core.setParameterValueByIndex === 'function') {
                        core.setParameterValueByIndex(idx, val, 1);
                    } else if (core && typeof core.setParameterValueById === 'function') {
                        core.setParameterValueById(id, val, 1);
                    } else if (core && typeof core.setParameterFloat === 'function') {
                        core.setParameterFloat(idx, val);
                    } else if (core && typeof core.setParamFloat === 'function') {
                        core.setParamFloat(idx, val);
                    }
                }
                return idx;
            }

            // 表情同步：将 AI 情绪映射到模型表情参数
            function resetEmotionParams() {
                if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                try {
                    const core = live2dModel.internalModel.coreModel;
                    const defaults = {
                        'ParamMouthSmile': 0, 'ParamEyeLSmile': 0, 'ParamEyeRSmile': 0,
                        'ParamBrowLAngle': 0, 'ParamBrowRAngle': 0,
                        'ParamBrowLForm': 0, 'ParamBrowRForm': 0,
                        'ParamEyeLOpen': 1, 'ParamEyeROpen': 1,
                        'ParamEyeBallX': 0, 'ParamEyeBallY': 0,
                        'ParamJawOpen': 0, 'ParamMouthOpenY': 0,
                        'MouthFrownLeft': 0, 'MouthFrownRight': 0,
                    };
                    Object.keys(defaults).forEach(id => {
                        _coreSetParam(core, id, defaults[id]);
                    });
                } catch (e) {}
            }
            let _emotionTimer = null;   // 表情自动恢复定时器
            let _emotionTickerFn = null; // 表情保持 ticker 回调（PIXI 7 ticker.add 返回回调，需用 ticker.remove 清理）
            let _emotionParams = null;  // 当前表情参数集 {id: value}
            function setEmotion(emotion, duration) {
                if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                const core = live2dModel.internalModel.coreModel;
                resetEmotionParams();

                // 1) 优先尝试通过模型内置 expression 切换（更自然，支持表情文件）
                const normEmo = String(emotion || 'neutral').toLowerCase();
                const expIdx = _emotionExpressionMap[normEmo];
                if (typeof expIdx === 'number' && _live2dExpressions.length > expIdx
                    && typeof live2dModel.expression === 'function') {
                    try {
                        live2dModel.expression(expIdx);
                    } catch (e) {
                        console.warn('[Live2D] 切表情失败:', e);
                    }
                }

                // 2) 组装表情参数集并立即写入一次
                const params = {};
                const set = function(id, val) {
                    params[id] = val;
                    _coreSetParam(core, id, val);
                };
                switch (emotion) {
                    case 'happy':
                        set('ParamMouthSmile', 1);
                        set('ParamEyeLSmile', 1);
                        set('ParamEyeRSmile', 1);
                        break;
                    case 'think':
                        set('ParamBrowLAngle', 1);
                        set('ParamBrowRAngle', 1);
                        set('ParamEyeBallX', 0.5);
                        set('ParamEyeBallY', -0.3);
                        break;
                    case 'surprised':
                        set('ParamEyeLOpen', 1);
                        set('ParamEyeROpen', 1);
                        set('ParamJawOpen', 0.8);
                        set('ParamBrowLForm', 1);
                        set('ParamBrowRForm', 1);
                        break;
                    case 'angry':
                        set('ParamBrowLAngle', 1);
                        set('ParamBrowRAngle', 1);
                        set('MouthFrownLeft', 1);
                        set('MouthFrownRight', 1);
                        break;
                    case 'sad':
                        set('ParamBrowLForm', 0.5);
                        set('ParamBrowRForm', 0.5);
                        set('MouthFrownLeft', 0.8);
                        set('MouthFrownRight', 0.8);
                        break;
                    default:
                        break; // neutral
                }

                // 3) 每帧重新写入表情参数，防止被 idle 动画覆盖（保证表情持续可见）
                if (_emotionTickerFn && live2dApp) {
                    try { live2dApp.ticker.remove(_emotionTickerFn); } catch (e) {}
                    _emotionTickerFn = null;
                }
                _emotionParams = params;
                if (emotion !== 'neutral' && live2dApp && Object.keys(params).length) {
                    const appRef = live2dApp;
                    const tickerFn = function() {
                        if (!_emotionParams) return;  // 到期已清空，直接跳过
                        if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                        const c = live2dModel.internalModel.coreModel;
                        Object.keys(_emotionParams).forEach(id => {
                            _coreSetParam(c, id, _emotionParams[id]);
                        });
                    };
                    _emotionTickerFn = tickerFn;
                    appRef.ticker.add(tickerFn);
                }

                // 4) 持续 duration 毫秒后恢复中性表情
                const d = duration || 4000;
                if (_emotionTimer) clearTimeout(_emotionTimer);
                if (emotion !== 'neutral') {
                    _emotionTimer = setTimeout(function() {
                        if (_emotionTickerFn && live2dApp) {
                            try { live2dApp.ticker.remove(_emotionTickerFn); } catch (e) {}
                            _emotionTickerFn = null;
                        }
                        _emotionParams = null;
                        if (live2dModel && live2dModel.internalModel && live2dModel.internalModel.coreModel) {
                            resetEmotionParams();
                        }
                        _emotionTimer = null;
                    }, d);
                }
                console.log('Live2D 表情:', emotion || 'neutral');
            }

            // 口型同步（Lipsync）：播放 TTS 音频时按音量驱动嘴巴开合
            let _audioCtx = null;
            let _analyser = null;
            let _lipSyncRAF = null;
            let _currentAudio = null;
            // 缓存的 Live2D 参数索引（不同 SDK 版本方法名不同，统一降级查找）
            let _paramIdxCache = { jaw: -1, mouth: -1, eyeL: -1, eyeR: -1, ok: false };

            // 兼容不同 Live2D Cubism SDK 版本：依次尝试 getParamIndex / getParameterIndex
            function _resolveParamIndex(core, id) {
                if (!core || !id) return -1;
                if (typeof core.getParamIndex === 'function') return core.getParamIndex(id);
                if (typeof core.getParameterIndex === 'function') return core.getParameterIndex(id);
                // 新版 SDK：可能直接提供 parameters 数组
                if (core.parameters && Array.isArray(core.parameters)) {
                    const idx = core.parameters.indexOf(id);
                    return idx >= 0 ? idx : -1;
                }
                return -1;
            }

            function _setParamSafe(core, idx, value) {
                if (!core || idx < 0) return;
                // Cubism4（pl2d CubismModel）优先：setParameterValueByIndex
                if (typeof core.setParameterValueByIndex === 'function') { core.setParameterValueByIndex(idx, value, 1); return; }
                if (typeof core.setParameterFloat === 'function') { core.setParameterFloat(idx, value); return; }
                // Cubism2
                if (typeof core.setParamFloat === 'function') { core.setParamFloat(idx, value); return; }
            }

            function _initParamIdxCache() {
                if (_paramIdxCache.ok) return _paramIdxCache;
                try {
                    const core = live2dModel && live2dModel.internalModel && live2dModel.internalModel.coreModel;
                    if (core) {
                        _paramIdxCache.jaw = _resolveParamIndex(core, 'ParamJawOpen');
                        _paramIdxCache.mouth = _resolveParamIndex(core, 'ParamMouthOpenY');
                        _paramIdxCache.eyeL = _resolveParamIndex(core, 'ParamEyeLOpen');
                        _paramIdxCache.eyeR = _resolveParamIndex(core, 'ParamEyeROpen');
                        _paramIdxCache.ok = true;
                    }
                } catch (e) {
                    console.warn('Lipsync: 解析参数索引失败（不影响主流程）:', e && e.message);
                }
                return _paramIdxCache;
            }

            function startLipSync(audioEl) {
                if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                try {
                    if (!_audioCtx) _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    if (_audioCtx.state === 'suspended') _audioCtx.resume();
                    const src = _audioCtx.createMediaElementSource(audioEl);
                    _analyser = _audioCtx.createAnalyser();
                    _analyser.fftSize = 256;
                    _analyser.smoothingTimeConstant = 0.7;
                    src.connect(_analyser);
                    _analyser.connect(_audioCtx.destination);
                    const data = new Uint8Array(_analyser.frequencyBinCount);
                    const core = live2dModel.internalModel.coreModel;
                    const idx = _initParamIdxCache();

                    const loop = function() {
                        try {
                            _analyser.getByteFrequencyData(data);
                            let sum = 0;
                            for (let i = 0; i < data.length; i++) sum += data[i];
                            const avg = sum / data.length;
                            // 音量 0-255 → 嘴巴开合 0-10
                            const mouth = Math.min(10, (avg / 255) * 14);
                            _setParamSafe(core, idx.jaw, mouth);
                            _setParamSafe(core, idx.mouth, mouth);
                        } catch (e) {
                            // 单帧出错不影响下一帧
                        }
                        _lipSyncRAF = requestAnimationFrame(loop);
                    };
                    loop();
                } catch (e) {
                    console.warn('Lipsync 初始化失败:', e);
                }
            }

            function stopLipSync() {
                if (_lipSyncRAF) { cancelAnimationFrame(_lipSyncRAF); _lipSyncRAF = null; }
                if (live2dModel && live2dModel.internalModel && live2dModel.internalModel.coreModel) {
                    try {
                        const core = live2dModel.internalModel.coreModel;
                        const idx = _initParamIdxCache();
                        _setParamSafe(core, idx.jaw, 0);
                        _setParamSafe(core, idx.mouth, 0);
                    } catch (e) {
                        console.warn('Lipsync 停止失败（不影响主流程）:', e && e.message);
                    }
                }
            }

            // 播放教师 TTS 音频（支持口型同步）
            function playTeacherAudio(url) {
                if (!url) return;
                if (_currentAudio) {
                    _currentAudio.pause();
                    _currentAudio = null;
                }
                const audio = new Audio(url);
                _currentAudio = audio;
                audio.play().catch(function(e) {
                    console.warn('TTS 播放失败:', e);
                });
                audio.addEventListener('ended', function() {
                    stopLipSync();
                    _currentAudio = null;
                });
                audio.addEventListener('error', function() {
                    stopLipSync();
                    _currentAudio = null;
                });
                startLipSync(audio);
            }

            // 应用 Live2D 位置/缩放设置到模型
            function applyLive2DSettings() {
                if (!live2dModel || !live2dApp) return;
                const s = live2dSettings;
                live2dModel.x = live2dApp.screen.width * (s.posX / 100);
                live2dModel.y = live2dApp.screen.height * (s.posY / 100);
                live2dModel.scale.set(s.scale / 100);
            }

            // 眨眼反馈：临时闭合双眼再睁开
            let _blinkTimer = null;
            function playBlink(duration) {
                if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.coreModel) return;
                try {
                    const core = live2dModel.internalModel.coreModel;
                    const idxL = _coreSetParam(core, 'ParamEyeLOpen', 0);
                    const idxR = _coreSetParam(core, 'ParamEyeROpen', 0);
                    if (idxL < 0 || idxR < 0) return;
                    const d = duration || 150;
                    if (_blinkTimer) clearTimeout(_blinkTimer);
                    _blinkTimer = setTimeout(function() {
                        if (live2dModel && live2dModel.internalModel && live2dModel.internalModel.coreModel) {
                            _coreSetParam(live2dModel.internalModel.coreModel, 'ParamEyeLOpen', 1);
                            _coreSetParam(live2dModel.internalModel.coreModel, 'ParamEyeROpen', 1);
                        }
                        _blinkTimer = null;
                    }, d);
                } catch (e) { /* 眨眼失败不影响主功能 */ }
            }

            // 画布坐标 → PIXI 世界坐标（处理 devicePixelRatio / autoDensity）
            function canvasToWorld(clientX, clientY, canvasEl, app) {
                const r = canvasEl.getBoundingClientRect();
                const sx = app.screen.width / r.width;
                const sy = app.screen.height / r.height;
                return { x: (clientX - r.left) * sx, y: (clientY - r.top) * sy };
            }

            // 应用背景主题到 #app
            const VALID_BG_THEMES = ['warm', 'sakura', 'bamboo', 'snow', 'dusk', 'night', 'custom'];
            function applyBgTheme(theme, bgUrl) {
                const appEl = document.getElementById('app');
                if (!appEl) return;
                const t = VALID_BG_THEMES.includes(theme) ? theme : 'warm';
                // 主题 class 挂在 body 上：让 #app 与 #menu-screen 都跟随主题变量
                VALID_BG_THEMES.forEach(tn => document.body.classList.remove('theme-' + tn));
                document.body.classList.add('theme-' + t);
                if (t === 'custom' && bgUrl) {
                    appEl.style.backgroundImage = 'linear-gradient(180deg, rgba(8,5,3,0.25), rgba(4,2,1,0.55)), url("' + bgUrl + '")';
                    appEl.style.backgroundSize = 'cover';
                    appEl.style.backgroundPosition = 'center';
                    appEl.style.backgroundRepeat = 'no-repeat';
                } else {
                    appEl.style.backgroundImage = '';
                }
                // 高亮预设按钮
                bgPresetBtns.forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.theme === (t === 'custom' ? 'custom' : t));
                });
                live2dSettings.bgTheme = t;
                live2dSettings.bgUrl = t === 'custom' ? (bgUrl || '') : '';
            }

            // 数值钳制工具
            function clampNum(val, min, max, fallback) {
                const n = parseFloat(val);
                if (isNaN(n)) return fallback;
                return Math.min(max, Math.max(min, n));
            }

            // 读取当前主题强调色（canvas/SVG 不解析 CSS 变量，需要实际 RGB 值）
            function accentRGB() {
                const v = getComputedStyle(document.body).getPropertyValue('--accent-rgb').trim();
                return v || '212, 163, 115';
            }

            let live2dInitStarted = false;   // 防止 initLive2D 重复执行（曾导致加载两个模型叠在同一个画布上）
            function initLive2D() {
                if (live2dInitStarted) { console.warn('Live2D: init skipped (live2dInitStarted=true)'); return; }
                live2dInitStarted = true;
                const canvas = document.getElementById('live2d-canvas');
                const portraitWrapper = document.getElementById('portrait-wrapper');
                if (!canvas || !portraitWrapper) {
                    console.warn('Live2D: init skipped, canvas=' + !!canvas + ', wrapper=' + !!portraitWrapper);
                    return;
                }

                // Diagnostics
                console.log('Live2D: init, PIXI=' + (typeof PIXI !== 'undefined') +
                    ', PIXI.live2d=' + (typeof PIXI !== 'undefined' && typeof PIXI.live2d !== 'undefined') +
                    ', Live2DCubismCore=' + (typeof Live2DCubismCore !== 'undefined'));

                if (typeof PIXI === 'undefined' || typeof PIXI.live2d === 'undefined') {
                    console.error('Live2D: Required libraries not loaded!');
                    return;
                }

                const rect = portraitWrapper.getBoundingClientRect();
                const width = Math.max(rect.width, 400);
                const height = Math.max(rect.height, 500);

                console.log('Live2D: creating PIXI.Application, size=' + width + 'x' + height);
                try {
                    const app = new PIXI.Application({
                        view: canvas,
                        width: width,
                        height: height,
                        backgroundAlpha: 0,
                        autoDensity: true,
                        resolution: 1,   // 降低渲染分辨率，减少 WebGL 负载
                        antialias: true,
                        powerPreference: 'high-performance',
                    });
                    live2dApp = app;
                    // 5秒不动回正 ticker：每次重建 app 后重挂（旧 app 已销毁，监听不会累积）
                    live2dApp.ticker.add(_gazeResetTicker);
                    console.log('Live2D: app created OK');

                    const baseUrl = live2dSettings.modelUrl || LIVE2D_DEFAULTS.modelUrl;
                    // 追加时间戳查询参数，绕过 PIXI / fetch 的 JSON 缓存
                    const modelPath = baseUrl + (baseUrl.includes('?') ? '&' : '?') + 't=' + Date.now();
                    _loadedModelUrl = baseUrl;
                    console.log('Live2D: loading model -> ' + baseUrl);
                    // 闭包捕获本次请求的 app 实例：模型加载是异步的，期间可能被 reload 覆盖
                    const currentApp = app;
                    console.log('Live2D: start loading, currentApp==' + (currentApp ? 'ok' : 'null'));
                    PIXI.live2d.Live2DModel.from(modelPath)
                        .then(model => {
                            console.log('Live2D: model promise resolved');
                            // 防御：如果本次 app 已被销毁/替换（live2dApp 已是别的实例），放弃此次加载结果
                            if (!currentApp || currentApp !== live2dApp) {
                                console.warn('Live2D: 模型加载完成但当前 PIXI 实例已被销毁，丢弃结果');
                                return;
                            }
                            live2dModel = model;
                            currentApp.stage.addChild(model);
                            console.log('Live2D: model added to stage');

                            model.anchor.set(0.5, 0.5);
                            applyLive2DSettings();

                            // 模型加载后从 motionManager 读取动作索引
                            loadMotionIndexFromModel(model);
                            // 扫描参数 id，匹配手臂/手部参数（用于"伸手"动作的幅度可控驱动）
                            scanModelArmParams();

                            model.motion('idle');

                            // 打印 drawable IDs，验证 HitAreas 是否匹配
                            try {
                                const ids = model.internalModel.getDrawableIDs();
                                console.log('Live2D: drawables =', ids.length, '个');
                                console.log('Live2D: hitAreas =', Object.keys(model.hitAreas || {}));
                            } catch (e) {
                                console.warn('Live2D: 无法读取 drawable 列表', e);
                            }

                            // 鼠标追踪（眼神跟随） + 5秒不动则渐变回正（保留呼吸/摆动）
                            // 注视跟随改为自绘实现（_feedGazeFocus 在全局 mousemove 中驱动 focusController，
                            // 修正了原生 autoFocus 因模型中心贴画布底部导致的"视线偏上"偏移）。
                            // 回正由 _gazeResetTicker 在 app ticker 中每帧检查并缓动回 (0,0)，
                            // 呼吸/摆动保持原生。ticker 在 initLive2D 创建 app 后统一挂载，这里只关闭原生跟随。
                            model.autoFocus = false;
                            console.log('[idle-gaze] 原生 autoFocus 已关闭，启用自绘注视跟随 + 5秒回正');

                            // 模型命中检测：使用 .model3.json 中的 HitAreas
                            // 优先采用 PIXI 内置 hit-test（精确判定 Head/Hand/Body/Legs）
                            model.on('hit', (hitAreaNames) => {
                                console.log('Live2D hit:', hitAreaNames);
                                let action = null;
                                for (const area of hitAreaNames) {
                                    if (HIT_AREA_ACTION_MAP[area]) {
                                        action = HIT_AREA_ACTION_MAP[area];
                                        break;
                                    }
                                }
                                if (action) {
                                    triggerAction(action);
                                } else {
                                    // 命中区域无映射时降级为打招呼
                                    triggerAction('hello');
                                }
                            });

                            // 点击模型本身：使用模型内置 hit-test
                            // 注意：pixi-live2d-display 在监听 'pointertap' 时自动调用 model.tap(x,y) 触发 'hit' 事件
                            model.interactive = true;
                            model.on('pointertap', (e) => {
                                const worldPoint = canvasToWorld(e.clientX || e.global.x, e.clientY || e.global.y, canvas, app);
                                try {
                                    model.tap(worldPoint.x, worldPoint.y);
                                } catch (err) {
                                    console.warn('Live2D tap 失败:', err);
                                }
                            });

                            // 降级方案：直接监听 canvas 点击事件，用坐标范围模拟命中区域
                            // 触发条件：当模型没有响应 hit 事件时使用此兜底逻辑
                            canvas.addEventListener('click', (e) => {
                                const r = canvas.getBoundingClientRect();
                                const clickY = e.clientY - r.top;
                                const clickX = e.clientX - r.left;
                                // 用模型 posY 配置作为纵向分界线、posX 作为横向分界线
                                const anchorY = (live2dSettings.posY / 100) * r.height;
                                const anchorX = (live2dSettings.posX / 100) * r.width;
                                // 判断左右手：左半 → HandL/Hello，右半 → HandR/Hello
                                const isUpper = clickY < anchorY;
                                const isLeft = clickX < anchorX;
                                if (isUpper) {
                                    // 上半身（头/手） → 打招呼
                                    triggerAction(isLeft ? 'greet' : 'hello');
                                } else {
                                    // 下半身（身体/腿） → 思考
                                    triggerAction('think');
                                }
                            });

                            console.log('Live2D: model loaded OK, scale=' + live2dSettings.scale);
                        })
                        .catch(err => {
                            console.error('Live2D: model error -', err.message || err, err);
                            const ctx = canvas.getContext('2d');
                            if (ctx) {
                                ctx.fillStyle = '#8a7560';
                                ctx.font = '14px "Noto Serif SC", serif';
                                ctx.textAlign = 'center';
                                ctx.fillText('模型加载失败: ' + (err.message || '未知错误'), canvas.width/2, canvas.height/2);
                            }
                        });
                } catch (e) {
                    console.error('Live2D: PIXI error -', e.message || e, e);
                }

                let resizeTimer;
                // 用 ResizeObserver 监听舞台容器：窗口缩放、侧栏拖拽、布局变化都会触发，
                // 避免 canvas 尺寸落后于容器导致角色溢出被裁剪（如左半被切掉）
                function syncStageSize() {
                    clearTimeout(resizeTimer);
                    resizeTimer = setTimeout(() => {
                        if (!live2dApp || !portraitWrapper) return;
                        const r2 = portraitWrapper.getBoundingClientRect();
                        if (r2.width <= 0 || r2.height <= 0) return;
                        live2dApp.renderer.resize(r2.width, r2.height);
                        if (live2dModel) {
                            applyLive2DSettings();
                        }
                    }, 120);
                }
                if (typeof ResizeObserver !== 'undefined') {
                    try {
                        // 复用全局 observer：reload 模型重建 app 时先断开旧的，避免观察器累积
                        if (window._stageObserver) {
                            try { window._stageObserver.disconnect(); } catch (e) {}
                        }
                        window._stageObserver = new ResizeObserver(syncStageSize);
                        window._stageObserver.observe(portraitWrapper);
                    } catch (e) {
                        console.warn('Live2D: ResizeObserver 不可用，回退 window resize', e);
                        window.addEventListener('resize', syncStageSize);
                    }
                } else {
                    window.addEventListener('resize', syncStageSize);
                }
            }

            // 销毁当前 Live2D 实例并用 live2dSettings.modelUrl 重新加载
            // 关键：destroy(true) 会把 canvas 一并从 DOM 移除，导致后续 getElementById 找不到。
            // 必须 destroy(false) 保留 canvas，再手动替换成全新 canvas 后重新 init。
            function reloadLive2DModel() {
                console.log('[reloadLive2DModel] called, modelUrl=' + live2dSettings.modelUrl);
                const wrapper = document.getElementById('portrait-wrapper');
                const oldCanvas = document.getElementById('live2d-canvas');
                if (live2dApp) {
                    try { live2dApp.destroy(false, { children: true }); } catch (e) { console.warn('Live2D destroy:', e); }
                    live2dApp = null;
                    console.log('[reloadLive2DModel] old app destroyed');
                }
                live2dModel = null;
                // 移除旧 canvas（destroy 后它已无 WebGL context），插入全新 canvas
                if (oldCanvas && oldCanvas.parentNode) oldCanvas.parentNode.removeChild(oldCanvas);
                if (wrapper) {
                    const fresh = document.createElement('canvas');
                    fresh.id = 'live2d-canvas';
                    wrapper.appendChild(fresh);
                    console.log('[reloadLive2DModel] new canvas inserted');
                } else {
                    console.warn('[reloadLive2DModel] portrait-wrapper not found!');
                }
                live2dInitStarted = false;
                console.log('[reloadLive2DModel] calling initLive2D');
                initLive2D();
            }

            window.addEventListener('DOMContentLoaded', initLive2D);
            if (document.readyState === 'interactive' || document.readyState === 'complete') {
                setTimeout(initLive2D, 100);
            }
            // 加载 Live2D 模型表情/动作清单，用于情绪映射
            window.addEventListener('DOMContentLoaded', loadLive2DModelInfo);
            if (document.readyState === 'interactive' || document.readyState === 'complete') {
                setTimeout(loadLive2DModelInfo, 100);
            }
