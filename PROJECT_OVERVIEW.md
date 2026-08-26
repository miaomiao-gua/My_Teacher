# AI 全能私教 - 项目结构文档

> **生成时间**：项目当前进度
> **入口**：`d:\My_teacher\project\`
> **访问**：http://127.0.0.1:5000/

---

## 一、项目结构

```
D:\My_teacher\
├── PROJECT_OVERVIEW.md         ← 本文件
└── project/                    ← Flask 项目根目录
    ├── app.py                  ← Flask 后端主程序（所有 API 路由）
    ├── file_utils.py           ← 文件/课程目录工具
    ├── lesson_prep.py          ← AI 备课核心逻辑（生成课程大纲/知识点/测验）
    ├── code_executor.py        ← 安全代码执行沙箱
    ├── gen_motions.py          ← Live2D 动作生成工具
    ├── clean_fake_res.py       ← 测试/调试辅助
    ├── check_js_*.py           ← HTML/JS 语法检查脚本（开发期）
    ├── test_exam_apis.py       ← 测验 API 测试
    │
    ├── requirements.txt        ← Python 依赖
    │
    ├── templates/
    │   └── index.html          ← 前端单页应用（约 5500 行）
    │                              包含全部 CSS/JS/HTML
    │
    ├── static/
    │   ├── css/
    │   │   └── style.css       ← 外部 CSS（备用，目前主样式在 index.html 内联）
    │   ├── js/
    │   │   ├── app.js          ← 外部 JS（备用）
    │   │   └── pl2d.js         ← Live2D Cubism SDK
    │   ├── audio/              ← TTS 生成的音频缓存（自动清理）
    │   ├── images/             ← 头像/背景上传目录
    │   │   ├── avatar.upload.png
    │   │   ├── bg.upload.png
    │   │   └── teacher.svg
    │   └── models/
    │       └── my_teacher/     ← Live2D 角色模型（女老师）
    │           ├── female_01Arkit_6.model3.json
    │           ├── female_01Arkit_6.moc3
    │           ├── female_01Arkit_6.cdi3.json
    │           ├── female_01Arkit_6.physics3.json
    │           ├── expressions/      ← 7 个表情 .exp3.json
    │           ├── motions/          ← 5 组动作 + 备用 .bak
    │           └── female_01Arkit_6.4096/  ← 9 张贴图 texture_XX.png
    │
    ├── data/                   ← 运行时生成
    │   ├── config.json         ← 全局配置（教师人设/默认模型/背景主题）
    │   └── lessons/            ← 所有已创建的课程目录
    │       └── <YYYYMMDD_主题>/
    │           ├── plan.json           ← 课程大纲（大纲+单元+知识点+测验）
    │           ├── metadata.json       ← 课程元数据（创建时间/进度等）
    │           ├── conversation.json   ← 聊天记录
    │           ├── progress.json       ← 学习进度
    │           ├── config.json         ← 课程级配置
    │           ├── unit_<N>/           ← 各单元的上下文（AI prompt 用）
    │           ├── board.png           ← 板书图片（可选）
    │           └── assets/             ← 课程资源图片/文件
    │
    └── debug_logs/             ← 开发期错误日志
        └── null-addEventListener.env
```

---

## 二、技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python + Flask |
| **AI** | Ollama（本地）/ OpenAI 兼容云端 API |
| **TTS** | Edge-TTS（云）/ 本地 TTS 引擎 |
| **前端框架** | 原生 JS（无框架）+ Canvas + CSS Flex/Grid |
| **角色渲染** | Live2D Cubism SDK 5 + PixiJS（pl2d.js）|
| **数据存储** | JSON 文件（无数据库）|

---

## 三、后端 API 路由（`app.py`）

### 页面
| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 渲染主页 `index.html` |
| `/favicon.ico` | GET | 图标 |

### 配置
| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/config` | GET / POST | 全局配置读写 |
| `/api/config/test` | POST | 测试 LLM/TTS 连通性 |
| `/api/live2d/model_info` | GET | 扫描模型表情/动作清单 |

### 资源上传
| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/upload_avatar` | POST | 上传教师头像 |
| `/api/upload_background` | POST | 上传课程背景图 |
| `/api/reset_avatar` | POST | 恢复默认头像 |
| `/api/set_background_theme` | POST | 切换背景主题 |
| `/api/reset_background` | POST | 恢复默认背景 |
| `/api/lesson/<folder>/board` | GET/POST/DELETE | 板书图片增删查 |
| `/api/lesson/<folder>/unit-images` | GET | 列出单元图片 |
| `/api/lesson/<folder>/asset/<file>` | GET | 课程资源访问 |

### 课程管理
| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/lessons` | GET | 列出所有课程 |
| `/api/lessons/<folder>` | DELETE | 删除课程 |
| `/api/lessons/<folder>/rename` | POST | 重命名课程 |
| `/api/lesson/<folder>/config` | GET / PUT | 课程级配置 |
| `/api/prepare_lesson` | POST | **AI 备课**（生成 plan.json） |
| `/api/apply_lesson` | POST | 应用备课结果（确认创建） |
| `/api/regenerate_lesson` | POST | 重新生成课程 |
| `/api/create_lesson` | POST | 直接创建空白课程 |
| `/api/switch_lesson` | POST | 切换当前课程 |
| `/api/list_resources` | GET | 资源列表 |
| `/api/download_resources` | POST | 批量下载资源 |

### 聊天/学习
| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | **SSE 流式对话** |
| `/api/progress` | GET | 学习进度 |
| `/api/lesson/next_unit` | POST | 进入下一单元 |
| `/api/lesson/reset` | POST | 重置课程 |

### 测验
| 路由 | 方法 | 说明 |
|---|---|---|
| `/api/exam/generate` | POST | 出题 |
| `/api/exam/submit` | POST | 提交答案 |

---

## 四、前端布局（`templates/index.html`）

```
<body>
├── <div class="menu-screen">          ← 首页：课程卡片 + 创建按钮 + 备课中 loading
│
├── <div id="app">                     ← 主界面（flex column, 100vh）
│   ├── <div class="main-area">        ← flex row, flex:1
│   │   ├── <div class="nav-float">    ← 左上悬浮：角色名+菜单按钮
│   │   ├── <div class="left-area">    ← flex:1, Live2D 主舞台
│   │   │   ├── <div class="portrait-wrapper">  ← Live2D canvas
│   │   │   ├── #unit-progress-bar    ← 单元进度条
│   │   │   ├── #board-overlay         ← 板书覆盖层
│   │   │   ├── #terminal-overlay      ← 代码执行结果
│   │   │   ├── #image-overlay         ← 单元图片
│   │   │   ├── .dialogue-bar          ← Galgame 对话条
│   │   │   ├── #view-chat             ← 视图：对话
│   │   │   ├── #view-exam             ← 视图：测验
│   │   │   ├── #view-resource         ← 视图：资源
│   │   │   ├── #view-lesson           ← 视图：单元大纲
│   │   │   └── #view-settings         ← 视图：设置
│   │   └── <div class="chat-sidebar"> ← flex:0 0 24%, 聊天记录
│   │
│   └── <div class="input-area">       ← flex-shrink:0, 底部输入框
│
└── <div id="preview-overlay">         ← 备课预览弹窗（移到 body 顶层，避免 stacking 问题）
```

---

## 五、关键前端模块（JS，约 5500 行）

| 段落 | 功能 |
|---|---|
| 1. 全局变量 / DOM 引用 | `previewOverlay`, `chatSidebar`, `messageInput` 等 |
| 2. Live2D 初始化 | `initLive2D()`, `loadLive2DModelInfo()`, `updateExpression/Motion()` |
| 3. 工具函数 | `_escHtml()`, 滚动到底部, markdown 渲染 |
| 4. 聊天核心 | 发送/接收 SSE, 气泡渲染, TTS 触发 |
| 5. 板书/终端/图片 | overlay 切换逻辑 |
| 6. 视图切换 | `switchView('chat'/'exam'/...)` |
| 7. 设置面板 | 模型选择/系统提示词编辑 |
| 8. 备课预览 | `renderPreview()`, `showPreviewOverlay()` |
| 9. 测验模块 | 出题/判分/错题讲解 |
| 10. 课程管理 | 创建/删除/重命名/切换 |
| 11. 初始化 | `loadTeacherSettings()`, `showMenu()`, `renderMenuLessons()` |

---

## 六、当前状态与已完成修复

### ✅ 已修复问题（本次会话）
1. **备课失败 null**：内联 `<script>` 移到 `</body>` 之前
2. **预览弹窗盖不住**：移到 `<body>` 顶层 + `z-index: 9000`
3. **input-area 消失**：从 `main-area` 内部移到 `#app` 内、`main-area` 外
4. **聊天栏占 24% 高度**：把 `chat-sidebar` 移回 `main-area` 内部
5. **HTML 嵌套错误**：清理 1 处孤儿 `</div>` 和 view-settings 多余闭合

### ✅ 当前布局
- `#app` (column) → `main-area` (row, 1:24%) + `input-area` (底部)
- Live2D 占主舞台左 3/4，聊天栏占右 1/4，输入框固定底部

### ✅ 界面美化
- 输入框聚焦双层光晕
- 发送按钮渐变 + 投影
- 课程卡 hover 上浮 + 左侧条变宽
- 预览弹窗淡入动画
- 知识点标签可编辑焦点

### 🟡 已知待优化
- 备用 Live2D 引擎回退
- 设置面板交互细节
- 移动端响应式
- 5000 端口可能被 macOS AirPlay 占用

---

## 七、运行方式

```bash
# 安装依赖
pip install -r d:\My_teacher\project\requirements.txt

# 启动（开发模式，Flask 自动重载）
cd d:\My_teacher\project
python -m flask run --port 5000
# 或：python app.py

# 浏览器访问
http://127.0.0.1:5000/
```

### 前置条件
- Python ≥ 3.10
- **可选**：本地 Ollama 服务（默认配置用云端 LLM，无需 Ollama）
- **可选**：Edge-TTS 配置（无云端 key 时回退到浏览器 SpeechSynthesis）

---

## 八、关键文件快速定位

| 想做什么 | 看哪个文件 |
|---|---|
| 改布局样式 | `templates/index.html` 顶部 `<style>` 块（约前 2000 行） |
| 改对话逻辑 | `templates/index.html` 的 `sendMessage()` / `api_chat` SSE 处理 |
| 改备课逻辑 | `lesson_prep.py` 的 `prepare_lesson()` |
| 改课程目录结构 | `file_utils.py` 的 `ensure_lesson_dir()` |
| 加新 API 路由 | `app.py` 末尾 |
| 改 Live2D 行为 | `templates/index.html` 的 `initLive2D()` / `updateMotion()` |
| 改提示词 | `app.py` 的 `build_system_prompt()` |

---

## 九、数据流

```
用户输入 → sendMessage()
    → POST /api/chat (SSE)
    → local_ollama_reply() OR cloud_llm_reply()
    → 流式返回 token
    → 渲染气泡 / 解析 [emotion] [action] [tool] 标签
    → updateExpression() / playMotion() / extract_tool_call()
    → TTS 播放（cloud_tts_audio / local_tts_audio）
    → 保存到 conversation.json
```

```
用户输入主题 → prepareAndEnter(topic)
    → POST /api/prepare_lesson
    → lesson_prep.prepare_lesson()
    → AI 生成 {topic, units, key_points, quiz_preset, resources, syllabus}
    → 弹窗 showPreviewOverlay() → 用户编辑
    → POST /api/apply_lesson（确认创建）
    → 生成课程目录 + plan.json + 单元上下文
    → 进入对话界面
```