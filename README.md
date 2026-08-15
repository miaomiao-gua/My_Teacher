# My Teacher — AI 全能私教 v3.0

> 一个基于 Flask 的 AI 学习辅导平台，支持本地 Ollama / 云端大模型对话、语音朗读（TTS）、按课程分章节教学、随堂测验、代码沙箱运行等功能。前端采用 Galgame 视觉小说风格，内置 Live2D 虚拟老师「艾琳老师」，支持自定义模型上传。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| **智能对话** | 支持本地 Ollama 模型 + 云端模型（硅基流动 / DeepSeek / OpenAI 兼容 API）双通道，自动回退，SSE 流式输出 |
| **AI 备课系统** | 输入主题自动生成课程大纲、分课教学、知识点与预设测验题，可预览编辑后确认创建 |
| **Galgame 对话体验** | 老师回复分段输出，按 Enter 逐段展开；对话条可实时控制口型同步 |
| **Live2D 虚拟老师** | 内置「艾琳老师」模型（表情 7 种 / 动作 9 组），说话口型同步、眨眼、动作切换 |
| **自定义模型上传** | 支持上传 zip 压缩包（自动识别 `model3.json` / `model.json`）或单文件，兼容 Cubism 2.1 / 3 / 4 / 5 |
| **全局主题跟随** | 内置 6 套背景主题，全局界面（主界面 / 菜单 / 面板 / 元素）颜色风格自动跟随 |
| **随堂测验** | 单选 / 多选 / 判断 / 填空，自动批改，支持逐题作答与错题讲解 |
| **语音朗读** | 云端 CosyVoice2 / edge-tts TTS，回复后自动播放，可独立开关 |
| **分课进度** | 顶部进度条显示当前课时，达标后自动进入下一单元 |
| **代码运行** | 对话中可直接运行 Python 代码（沙箱安全执行） |
| **板书与资源** | 支持上传课程板书图片、单元图片与课程资源，按单元管理 |
| **视觉定制** | 教师头像上传、Live2D 位置 / 缩放 / 漂浮动画调节、背景主题独立设置 |
| **聊天侧栏** | 右侧聊天记录宽度可拖拽调节，或通过设置面板滑条调整 |
| **键盘操作** | ESC 关闭弹窗 / 返回菜单 / 跳过分段 |

---

## 🚀 快速开始

### 1. 克隆项目（v3 分支）

```bash
git clone -b v3 https://github.com/miaomiao-gua/My_Teacher.git
cd My_Teacher
```

### 2. 安装依赖（Python ≥ 3.10）

```bash
cd project
pip install -r requirements.txt
```

### 3. 配置（可选）

复制 `.env.example` 为 `.env` 并填写密钥：

```bash
cp .env.example .env
```

不配置也可运行：默认使用云端 API（硅基流动）作为备用通道，未填写时自动回退本地 Ollama 或浏览器语音合成。

### 4. 启动

```bash
cd project
python app.py
# 或
python -m flask run --port 5000
```

浏览器访问：<http://127.0.0.1:5000/>

---

## ⚙️ 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MY_TEACHER_CLOUD_API_KEY` | 云端 LLM API Key（硅基流动等） | 空 |
| `MY_TEACHER_CLOUD_MODEL` | 云端模型名 | `deepseek-ai/DeepSeek-V3` |
| `MY_TEACHER_CLOUD_BASE_URL` | 云端 API 地址 | `https://api.siliconflow.cn/v1` |
| `MY_TEACHER_CHAT_*` | 独立的对话模型配置（可选） | 空 |
| `MY_TEACHER_OLLAMA_BASE_URL` | 本地 Ollama 地址 | `http://localhost:11434` |
| `MY_TEACHER_OLLAMA_MODEL` | 本地模型名 | `qwen2.5:7B` |
| `MY_TEACHER_ENABLE_LOCAL_OLLAMA` | 是否启用本地 Ollama | `true` |
| `MY_TEACHER_TTS_*` | TTS 语音相关配置 | `cloud` / `anna` |
| `MY_TEACHER_ASSISTANT_NAME` | 老师名字 | `艾琳老师` |
| `MY_TEACHER_DEFAULT_TOPIC` | 默认教学主题 | `Python 基础` |

---

## 📁 项目结构

```
My_Teacher/
├── README.md                   ← 本文件
├── .env.example                ← 环境变量模板
└── project/                    ← Flask 项目根目录
    ├── app.py                  ← 后端主程序（全部 API 路由）
    ├── file_utils.py           ← 文件 / 课程目录工具
    ├── lesson_prep.py          ← AI 备课核心逻辑
    ├── code_executor.py        ← 安全代码执行沙箱
    ├── gen_motions.py          ← Live2D 动作生成工具
    ├── requirements.txt        ← Python 依赖
    ├── config.json             ← 全局配置（不入库，运行时生成）
    ├── templates/
    │   └── index.html          ← 前端单页应用（内联全部 CSS / JS）
    ├── static/
    │   ├── css/style.css       ← 备用外部样式
    │   ├── js/pl2d.js          ← Live2D Cubism 运行时（pixi-live2d-display）
    │   ├── images/             ← 头像 / 背景图片
    │   └── models/
    │       ├── my_teacher/     ← 内置 Live2D 默认模型（艾琳老师）
    │       └── uploads/        ← 用户上传的模型（不入库）
    └── lessons/                ← 所有已创建的课程（不入库）
```

---

## 🔌 API 概览

| 分类 | 路由 | 说明 |
|------|------|------|
| 页面 | `GET /` | 主页面 |
| 配置 | `GET/POST /api/config` | 全局配置读写 |
| | `POST /api/config/test` | 测试 LLM / TTS 连通性 |
| Live2D | `GET /api/live2d/model_info` | 模型表情 / 动作清单 |
| | `POST /api/upload_model` | 上传自定义模型（zip / 单文件） |
| | `POST /api/reset_model` | 恢复内置默认模型 |
| 上传 | `POST /api/upload_avatar` | 教师头像 |
| | `POST /api/upload_background` | 背景图 |
| | `POST /api/set_background_theme` | 切换背景主题 |
| 课程 | `POST /api/prepare_lesson` | AI 备课 |
| | `POST /api/apply_lesson` | 确认创建课程 |
| | `GET /api/lessons` | 课程列表 |
| 学习 | `POST /api/chat` | SSE 流式对话 |
| | `GET /api/progress` | 学习进度 |
| 测验 | `POST /api/exam/generate` | 出题 |
| | `POST /api/exam/submit` | 提交批改 |

---

## 🎭 Live2D 自定义模型上传

1. 打开设置面板 → 模型区域 → 选择文件。
2. **推荐**：将整个模型文件夹（含 `*.model3.json` / `model.json` 入口文件）压缩为 zip 后上传，系统会自动解压并识别入口文件。
3. 也支持直接上传 `model3.json` / `model.json` / `.moc3` / `.moc` 单文件（单文件缺少贴图 / 动作资源时可能无法完整显示）。
4. 上传成功后刷新页面即生效；可随时点击「恢复默认」回到内置的艾琳老师模型。

---

## 🧩 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python + Flask |
| AI | Ollama（本地）/ OpenAI 兼容云端 API |
| TTS | edge-tts（本地）/ CosyVoice2（云端） |
| 前端 | 原生 JS + Canvas + CSS Flex/Grid |
| 角色渲染 | Live2D Cubism SDK + PixiJS（pl2d.js） |
| 数据存储 | JSON 文件（无数据库） |

---

## 📌 版本历史

| 版本 | 分支 | 亮点 |
|------|------|------|
| v3.0 | `v3` | 内置新版 Live2D 默认模型；支持自定义模型上传（Cubism 2.1~5 全格式）；全局主题跟随背景；界面美化与聊天交互优化 |
| v2.0 | `v2.0` | 环境变量配置；.gitignore 项目隔离；功能大幅扩展 |
| v1.0 | `main` | 基础版本 |

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)
