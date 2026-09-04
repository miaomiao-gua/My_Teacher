# My Teacher v4.o — AI 自适应教学系统
**(是o不是0)**
> 从“AI 教师应用”升级为“以学习者为中心的自适应教学系统”。  
> 基于 v3 的对话式 AI 私教，v4 新增了学生数字孪生、知识图谱、多 Agent 协作、教学策略引擎和学习评估闭环。  
> —— **AI 不是老师，AI 是驱动“教学操作系统”的引擎。**

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

---

## 🚀 快速开始

### 1. 克隆项目（v4.o 分支）

```bash
git clone -b v4.o https://github.com/miaomiao-gua/My_Teacher.git
cd My_Teacher
```

### 2. 安装依赖（Python ≥ 3.10）

```bash
cd project
pip install -r requirements.txt
```

### 3. 配置（可选）

复制 `.env.example` 为 `.env` 并填写密钥；不配置也可运行（自动回退本地 Ollama 或浏览器语音合成）：

```bash
cp .env.example .env
```

### 4. 启动

```bash
cd project
python app.py
```

浏览器访问：<http://127.0.0.1:5000/>

### 5. OCR（扫描版 PDF 可选）

系统自动检测 `~/.EasyOCR/model/` 下的 easyocr 模型（检测 + 中 / 英识别，约 100MB），缺失时按提示下载；设置面板 `ocr_enabled` 可开关。

---

## 📁 项目结构

```
My_Teacher/
├── README.md                   ← 本文件（v4.o）
├── PROJECT_OVERVIEW.md         ← 项目结构文档
├── .env.example                ← 环境变量模板
└── project/                    ← Flask 项目根目录
    ├── app.py                  ← 后端主程序（全部 API 路由，含 /api/v4/*）
    ├── file_utils.py           ← 文件 / PDF 目录 / OCR 工具
    ├── lesson_prep.py          ← AI 备课核心逻辑
    ├── learner_model.py        ← v4 学生认知画像（数字孪生）
    ├── knowledge_graph.py      ← v4 知识图谱
    ├── pedagogical_engine.py   ← v4 教学策略引擎
    ├── learning_evaluation.py  ← v4 学习评估闭环
    ├── agents/                 ← v4 多 Agent 系统（6 类角色）
    ├── code_executor.py        ← 安全代码执行沙箱
    ├── requirements.txt        ← Python 依赖
    ├── config.json             ← 全局配置（运行时生成）
    ├── templates/index.html    ← 前端单页应用
    └── static/
        ├── css/style.css       ← 全局样式
        └── js/
            ├── base.js         ← 基础状态 / 动作 / 表情 / Live2D 引擎
            ├── interact.js     ← 视图切换 / 聊天 / 备课 / 诊断
            ├── dashboard.js    ← v4 前端仪表盘
            ├── settings.js     ← 设置面板 / 备课预览
            └── pl2d.js         ← Live2D Cubism 运行时（pixi-live2d-display）
```

---

## 🔌 API 概览

| 分类 | 路由 | 说明 |
|------|------|------|
| 页面 | `GET /` | 主页面 |
| 配置 | `GET/POST /api/config` | 全局配置读写（含 `ocr_available`） |
| 备课 | `POST /api/prepare_lesson` | AI 备课（JSON / multipart 教材） |
| 诊断 | `POST /api/prep_diagnose/open` | 开启交互式课前诊断（第一问） |
| | `POST /api/prep_diagnose/answer` | 提交回答，AI 基于上下文追问 |
| | `POST /api/prep_diagnose/finish` | 结束诊断，返回摸底结论 |
| 课程 | `POST /api/apply_lesson` | 确认创建课程 |
| | `GET /api/lessons` | 课程列表 |
| 学习 | `POST /api/chat` | SSE 流式对话 |
| | `GET /api/progress` | 学习进度 |
| 测验 | `POST /api/exam/generate` | 出题 |
| | `POST /api/exam/submit` | 提交批改 |
| v4 | `GET /api/v4/dashboard` | 学习状态仪表盘数据 |
| | `POST /api/v4/knowledge_graph/generate` | 知识图谱生成 |
| | 其余 `/api/v4/*` | Learner Model / 教学引擎 / 评估等 |

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
| v4.o | `v4.o` | AI 自适应教学系统（学生数字孪生 / 知识图谱 / 多 Agent / 教学策略引擎 / 评估闭环 / 仪表盘）；PDF 整本教材导入 + OCR；备课对齐目录章节 / 交互式课前诊断 / token 展示；讲课概念联想；性能优化 |
| v3.1 | `v3.0` | 模型 / API 可视化配置；聊天附件上传与图片识图；备课与人格提示词自定义 |
| v3.0 | `v3.0` | 新版 Live2D 默认模型；自定义模型上传（Cubism 2.1~5）；全局主题跟随 |
| v2.0 | `v2.0` | 环境变量配置；功能大幅扩展 |
| v1.0 | `main` | 基础版本 |

---

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)
