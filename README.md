# My Teacher v5.O — AI 自适应教学系统
**（O 是大写，不是 0；v4.o 分支为拆分前的单体版）**
> 从“AI 教师应用”升级为“以学习者为中心的自适应教学系统”。  
> 基于 v3 的对话式 AI 私教，v4 新增了学生数字孪生、知识图谱、多 Agent 协作、教学策略引擎和学习评估闭环。  
> **v5.O 在 v4.o 基础上完成工程化改造：前后端拆分（server / client / data）、账号注册登录鉴权、课程按账号完全隔离、越权防护与局域网访问。**  
> —— **AI 不是老师，AI 是驱动“教学操作系统”的引擎。**

---

## 🎯 v5.O 核心升级（相对 v4.o）

| 维度 | v4.o | v5.O |
|---|---|---|
| 工程结构 | 单体应用（project/ 目录） | **前后端拆分：server/（后端）+ client/（前端）+ data/（数据）** |
| 访问控制 | 无鉴权，局域网内任何人可访问 | **账号注册 / 登录，token 鉴权（`/api/auth/*` 白名单 + 全局拦截）** |
| 数据隔离 | 所有用户共享同一份课程 | **课程按账号完全隔离**：只可见本人课程，访问他人课程返回 403 |
| 旧数据迁移 | — | 遗留课程自动划归第一个注册的账号（幂等迁移） |
| 多用户状态 | 全局共享 | ACTIVE_LESSON 等内存状态按用户隔离 |
| 部署访问 | 本机 | 本机 + 局域网 + 安全登录 |

---

## 🎯 v4.0 核心升级

| 维度 | v3 | v4 |
|---|---|---|
| 核心逻辑 | LLM-centric（对话驱动） | Learner-centric（学生模型驱动） |
| 学生记忆 | 当前对话 + 课程进度 | 持续更新的学生数字孪生 |
| 知识表示 | 课程大纲（线性目录） | 知识图谱（节点 + 依赖关系） |
| 教学策略 | Prompt 驱动（“你是一位老师……”） | 教学策略引擎（诊断 → 规划 → 自适应） |
| Agent 架构 | 单一 AI | 多 Agent 协作（Tutor / Planner / Diagnostician / Examiner / Evaluator / Coach） |
| 效果评估 | 测验分数 | 学习增益 + 学习效率 + ROI 闭环 |
| 数据飞轮 | 无 | 诊断 → 教学 → 评估 → 学习 → 数据 → 诊断 |

---

## ✨ 功能特性

### AI 学习引擎（核心）

| 模块 | 说明 |
|---|---|
| **Learner Digital Twin** | 为每个学习者建立持续更新的知识状态画像（掌握度、遗忘曲线、认知特征、学习行为、错题记忆） |
| **Knowledge Graph** | 知识点之间的前置依赖关系图谱，支持动态学习路径生成 |
| **Pedagogical Engine** | 自适应教学策略引擎，根据学生状态决定“讲什么、怎么讲、要不要重讲” |
| **Learning Evaluation** | 学习增益计算（前测 → 后测）、学习效率、学习 ROI、学习曲线可视化 |
| **Agent System** | 6 个协作 Agent：Tutor、Planner、Diagnostician、Examiner、Evaluator、Coach |

### 教学功能（继承 v3 并增强）

| 功能 | 说明 |
|---|---|
| **智能对话** | 本地 Ollama / 云端模型双通道，SSE 流式输出 |
| **AI 备课** | 输入主题自动生成课程大纲、分课教学、知识点、测验题 |
| **PDF 教材优化** | 只提取目录 + 按章节按需提取文本 + easyocr 扫描版 OCR 兜底 |
| **随堂测验** | 单选 / 多选 / 判断 / 填空，自动批改，逐题作答 |
| **代码运行** | Python 代码沙箱安全执行 |
| **语音朗读** | edge-tts（本地）/ CosyVoice2（云端） |
| **Live2D 虚拟老师** | 内置「艾琳老师」，7 种表情 + 9 组动作，支持自定义模型上传 |
| **Galgame 对话体验** | 分段输出、按 Enter 逐段展开、打字机效果 |
| **V4 仪表盘** | 能力雷达图、学习进度曲线、增益分析、教学洞察、错题本 |

### 视觉与体验

- **Galgame 风格界面**（全局主题跟随）
- **全局主题跟随**：6 套背景主题，界面颜色自动适配
- **聊天侧栏宽度可拖拽调节**
- **键盘操作**：ESC 关闭弹窗 / 返回菜单 / 跳过分段

---

## 🧠 V4 架构设计

```
                  ┌─────────────────────────────────────────────┐
                  │               学习数据飞轮                    │
                  └─────────────────────────────────────────────┘
   诊断 ──▶ 教学 ──▶ 评估 ──▶ 学习 ──▶ 数据 ──┐
     ▲                                       │
     └───────────────────────────────────────┘
         更新画像 · 修正图谱 · 调整策略

┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  Learner     │   │ Knowledge    │   │ Pedagogical  │
│  Model       │   │ Graph        │   │ Engine       │
│  学生数字孪生  │   │ 知识图谱      │   │ 教学策略引擎  │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       └──────────────────┼──────────────────┘
                          ▼
┌───────────────────────────────────────────────┐
│             多 Agent 协作团队                   │
│  Planner · Coach · Tutor · Examiner ·          │
│  Diagnostician · Evaluator                     │
└───────────────────────────────────────────────┘
                          ▼
          ┌───────────┐   ┌───────────┐
          │  /api/v4  │   │  V4 仪表盘 │
          │  路由层    │   │  可视化    │
          └───────────┘   └───────────┘
```

**核心数据流**：测验 / 对话结果 → `learning_evaluation` 聚合 → 更新 `learner_model` 画像与遗忘曲线 → 同步 `knowledge_graph` 掌握度 → `pedagogical_engine` 依据「前置依赖 + 薄弱点」推荐学习路径与讲解策略 → 多 Agent 团队执行教学 → 再评估，形成闭环。

### v4.o 备课与教学体验增强

- **整本 PDF 教材导入**：备课只提取目录（秒级），开课后按单元按需提取对应章节，不用一次性处理整本书
- **OCR 扫描版兜底**：无文本层的扫描 PDF 自动走 easyocr（中英识别），模型完整性校验 + 配置开关（`ocr_enabled`）
- **备课对齐教材目录**：按目录顶层章节数生成单元（15 章 → 15 课，自动过滤前言 / 附录）
- **备课过程透明**：备课界面显示当前使用的模型，预览弹窗显示 token 用量（输入 + 输出）
- **交互式课前诊断**（实验功能，可开关）：备课前在**上课界面内**与学生对话摸底，AI 基于上下文自然追问，未知概念在备课时重点展开
- **讲课概念联想**：结合知识图谱，讲课时自然类比相关概念（如 `cout<<` ↔ `print()`），每课最多 1~2 次
- **聊天附件支持 PDF**：上传 PDF 自动提取前 20 页文本随消息发给模型
- **前后端拆分 + 账号鉴权**：目录拆分为 `server/`（后端）+ `client/`（前端）+ `data/`（数据）；所有 `/api/*` 需登录后携带 token 访问（`/api/auth/*` 白名单），课程按创建者隔离，防止越权访问；支持局域网访问（`0.0.0.0:5000`）

---

## 🚀 快速开始

### 1. 克隆项目（v5.O 分支）

```bash
git clone -b v5.O https://github.com/miaomiao-gua/My_Teacher.git
cd My_Teacher
```

### 2. 安装依赖（Python ≥ 3.10）

```bash
cd server
pip install -r requirements.txt
```

### 3. 配置（可选）

复制 `.env.example` 为 `.env` 并填写密钥；不配置也可运行（自动回退本地 Ollama 或浏览器语音合成）：

```bash
cp .env.example .env
```

### 4. 启动

```bash
cd server
python app.py
```

浏览器访问：<http://127.0.0.1:5000/>

> 首次使用请先**注册账号**（登录页默认切到「注册」）；**第一个注册的账号将接管全部遗留旧课程**，之后每个账号课程完全隔离。
> 局域网设备访问：`http://<本机局域网IP>:5000/`（本机为 `192.168.2.31`，以 `ipconfig` 实际为准）。
> Windows 防火墙若拦截，请以管理员身份放行：`New-NetFirewallRule -DisplayName "My Teacher (TCP 5000)" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Any`

### 5. OCR（扫描版 PDF 可选）

系统自动检测 `~/.EasyOCR/model/` 下的 easyocr 模型（检测 + 中 / 英识别，约 100MB），缺失时按提示下载；设置面板 `ocr_enabled` 可开关。

---

## 🐧 Linux 部署

后端为 Flask，代码本身跨平台；终端执行模块在 Linux 上自动使用 `bash` 替代 `cmd/powershell`（`sys.platform` 判断，Windows 行为不变）。

### 方式一：脚本安装（推荐）

```bash
cd deploy/linux
bash install.sh        # 创建 .venv-linux + 安装依赖 + 生成 .env
# 编辑 .env 填写 API Key 后：
bash run.sh            # 开发模式（MODE=dev 默认）
MODE=prod bash run.sh  # 生产模式（gunicorn，默认 2 个 worker）
```

可选环境变量：`HOST`（默认 `0.0.0.0`）、`PORT`（默认 `5000`）、`GUNICORN_WORKERS`、`VENV_DIR`。

### 方式二：systemd 开机自启（生产）

1. 将项目部署到 `/opt/my-teacher` 并执行 `install.sh`
2. 复制服务单元并按实际路径修改其中的 `User` / `WorkingDirectory` / `ExecStart`：

```bash
sudo cp deploy/linux/my-teacher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now my-teacher
journalctl -u my-teacher -f   # 查看日志
```

> 说明：`requirements.txt` 含 python-pptx（后端硬依赖）；`gunicorn` 仅 Linux 生产需要，由 `install.sh` 单独安装，不影响 Windows 开发环境。OCR（easyocr + PyMuPDF + torch）依赖较重，默认跳过安装，需要时取消 `install.sh` 中对应注释即可。

## 📁 项目结构

```
My_Teacher/
├── README.md                   ← 本文件（v4.o）
├── PROJECT_OVERVIEW.md         ← 项目结构文档
├── .env.example                ← 环境变量模板
├── server/                     ← 后端（Python + Flask）
│   ├── app.py                  ← 后端主程序（全部 API 路由，含 /api/v4/* 与鉴权）
│   ├── auth.py                 ← 账号注册 / 登录 / token 鉴权（users.json）
│   ├── file_utils.py           ← 文件 / PDF 目录 / OCR 工具
│   ├── lesson_prep.py          ← AI 备课核心逻辑
│   ├── learner_model.py        ← v4 学生认知画像（数字孪生）
│   ├── knowledge_graph.py      ← v4 知识图谱
│   ├── pedagogical_engine.py   ← v4 教学策略引擎
│   ├── learning_evaluation.py  ← v4 学习评估闭环
│   ├── agents/                 ← v4 多 Agent 系统（6 类角色）
│   ├── code_executor.py        ← 安全代码执行沙箱
│   └── requirements.txt        ← Python 依赖
├── deploy/linux/               ← Linux 部署（install.sh / run.sh / systemd 服务）
├── client/                     ← 前端（原生 JS 单页应用）
│   ├── index.html              ← 前端单页应用（含登录/注册页）
│   └── static/
│       ├── css/style.css       ← 全局样式
│       ├── js/
│       │   ├── base.js         ← 基础状态 / 动作 / 表情 / Live2D 引擎
│       │   ├── interact.js     ← 视图切换 / 聊天 / 备课 / 诊断
│       │   ├── dashboard.js    ← v4 前端仪表盘
│       │   ├── settings.js     ← 设置面板 / 备课预览
│       │   └── pl2d.js         ← Live2D Cubism 运行时（pixi-live2d-display）
│       ├── models/             ← Live2D 模型 / 上传模型
│       ├── audio/              ← TTS 音频缓存
│       └── uploads/            ← 聊天附件 / 板书图片
└── data/                       ← 数据（运行时生成，与代码分离）
    ├── config.json             ← 全局配置
    ├── users.json              ← 用户账号（盐 + sha256 密码哈希 + token）
    ├── lessons/                ← 所有课程（含 learners/ 学生档案）
    ├── py_deps/                ← 本地 python-pptx 等依赖
    └── debug_logs/             ← 运行日志
```

---

## 🔌 API 概览

| 分类 | 路由 | 说明 |
|------|------|------|
| 页面 | `GET /` | 主页面（未登录自动跳登录页） |
| 认证 | `POST /api/auth/register` | 注册账号 |
| | `POST /api/auth/login` | 登录，返回 token |
| | `POST /api/auth/logout` | 退出（token 失效） |
| | `GET /api/auth/me` | 校验登录态 |
| | `GET /api/auth/status` | 是否已有用户（前端引导注册） |
| 配置 | `GET/POST /api/config` | 全局配置读写（含 `ocr_available`，需登录） |
| 备课 | `POST /api/prepare_lesson` | AI 备课（JSON / multipart 教材） |
| 诊断 | `POST /api/prep_diagnose/open` | 开启交互式课前诊断（第一问） |
| | `POST /api/prep_diagnose/answer` | 提交回答，AI 基于上下文追问 |
| | `POST /api/prep_diagnose/finish` | 结束诊断，返回摸底结论 |
| 课程 | `POST /api/apply_lesson` | 确认创建课程（归属当前账号） |
| | `GET /api/lessons` | 课程列表（仅本人 + 遗留共享课程） |
| 学习 | `POST /api/chat` | SSE 流式对话 |
| | `GET /api/progress` | 学习进度 |
| 测验 | `POST /api/exam/generate` | 出题 |
| | `POST /api/exam/submit` | 提交批改 |
| v4 | `GET /api/v4/dashboard` | 学习状态仪表盘数据 |
| | `POST /api/v4/knowledge_graph/generate` | 知识图谱生成 |
| | 其余 `/api/v4/*` | Learner Model / 教学引擎 / 评估等 |

> 除 `/api/auth/*` 外，所有 `/api/*` 接口均需请求头携带 `Authorization: Bearer <token>`，否则返回 401；访问他人课程返回 403。

---

## 🧩 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python + Flask |
| AI | Ollama（本地）/ OpenAI 兼容云端 API（硅基流动等） |
| OCR | easyocr + PyMuPDF（扫描版 PDF 文本提取） |
| TTS | edge-tts（本地）/ CosyVoice2（云端） |
| 前端 | 原生 JS + Canvas + CSS Flex/Grid |
| 角色渲染 | Live2D Cubism SDK + PixiJS（pl2d.js） |
| 数据存储 | JSON 文件（无数据库） |

---

## 📌 版本历史

| 版本 | 分支 | 亮点 |
|------|------|------|
| v5.O | `v5.O` | **前后端拆分（server / client / data）+ 账号注册登录鉴权 + 课程按账号完全隔离 + 越权防护 + 局域网访问**；AI 自适应教学系统（学生数字孪生 / 知识图谱 / 多 Agent / 教学策略引擎 / 评估闭环 / 仪表盘）；PDF 整本教材导入 + OCR；备课对齐目录章节 / 交互式课前诊断 / token 展示；讲课概念联想；性能优化 |
| v4.o | `v4.o` | AI 自适应教学系统（学生数字孪生 / 知识图谱 / 多 Agent / 教学策略引擎 / 评估闭环 / 仪表盘）；PDF 整本教材导入 + OCR；备课对齐目录章节 / 交互式课前诊断 / token 展示；讲课概念联想；性能优化（单体应用，无鉴权） |
| v3.1 | `v3.0` | 模型 / API 可视化配置；聊天附件上传与图片识图；备课与人格提示词自定义 |
| v3.0 | `v3.0` | 新版 Live2D 默认模型；自定义模型上传（Cubism 2.1~5）；全局主题跟随 |
| v2.0 | `v2.0` | 环境变量配置；功能大幅扩展 |
| v1.0 | `main` | 基础版本 |

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)
