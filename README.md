My Teacher v4.o(是o不是0) — AI 自适应教学系统
从“AI 教师应用”升级为“以学习者为中心的自适应教学系统”。
基于 v3 的对话式 AI 私教，v4 新增了学生数字孪生、知识图谱、多 Agent 协作、教学策略引擎和学习评估闭环。
—— AI 不是老师，AI 是驱动“教学操作系统”的引擎。

🎯 v4.0 核心升级
维度	v3	v4
核心逻辑	LLM-centric（对话驱动）	Learner-centric（学生模型驱动）
学生记忆	当前对话 + 课程进度	持续更新的学生数字孪生
知识表示	课程大纲（线性目录）	知识图谱（节点 + 依赖关系）
教学策略	Prompt 驱动（“你是一位老师……”）	教学策略引擎（诊断 → 规划 → 自适应）
Agent 架构	单一 AI	多 Agent 协作（Tutor / Planner / Diagnostician / Examiner / Evaluator / Coach）
效果评估	测验分数	学习增益 + 学习效率 + ROI 闭环
数据飞轮	无	诊断 → 教学 → 评估 → 学习 → 数据 → 诊断
✨ 功能特性
AI 学习引擎（核心）
模块	说明
Learner Digital Twin	为每个学习者建立持续更新的知识状态画像（掌握度、遗忘曲线、认知特征、学习行为、错题记忆）
Knowledge Graph	知识点之间的前置依赖关系图谱，支持动态学习路径生成
Pedagogical Engine	自适应教学策略引擎，根据学生状态决定“讲什么、怎么讲、要不要重讲”
Learning Evaluation	学习增益计算（前测 → 后测）、学习效率、学习 ROI、学习曲线可视化
Agent System	6 个协作 Agent：Tutor、Planner、Diagnostician、Examiner、Evaluator、Coach
教学功能（继承 v3 并增强）
功能	说明
智能对话	本地 Ollama / 云端模型双通道，SSE 流式输出
AI 备课	输入主题自动生成课程大纲、分课教学、知识点、测验题
PDF 教材优化	只提取目录 + 按章节按需提取文本 + easyocr 扫描版 OCR 兜底
随堂测验	单选 / 多选 / 判断 / 填空，自动批改，逐题作答
代码运行	Python 代码沙箱安全执行
语音朗读	edge-tts（本地）/ CosyVoice2（云端）
Live2D 虚拟老师	内置「艾琳老师」，7 种表情 + 9 组动作，支持自定义模型上传
Galgame 对话体验	分段输出、按 Enter 逐段展开、打字机效果
V4 仪表盘	能力雷达图、学习进度曲线、增益分析、教学洞察、错题本
视觉与体验
Galgame 风格界面（全局主题跟随）

全局主题跟随：6 套背景主题，界面颜色自动适配

聊天侧栏宽度可拖拽调节

键盘操作：ESC 关闭弹窗 / 返回菜单 / 跳过分段

🧠 V4 架构设计
text
┌─────────────────────────────────────────────────────────────────┐
│                      Presentation Layer                         │
│             Live2D / TTS / Galgame UI / Dashboard              │
├─────────────────────────────────────────────────────────────────┤
│                       Agent System (6 Agents)                   │
│  Tutor │ Planner │ Diagnostician │ Examiner │ Evaluator │ Coach│
├─────────────────────────────────────────────────────────────────┤
│                     Pedagogical Engine                          │
│        Diagnosis │ Planning │ Adaptation │ Learning Path       │
├─────────────────────────────────────────────────────────────────┤
│                       Knowledge Graph                           │
│         Nodes │ Edges │ Prerequisites │ Skills                │
├─────────────────────────────────────────────────────────────────┤
│                      Learner Digital Twin                       │
│   Knowledge State │ Cognitive Features │ Learning Behavior    │
│   Error Memory │ Assessment History │ Goals                   │
├─────────────────────────────────────────────────────────────────┤
│                         Data Layer                             │
│              JSON / SQLite / Redis / Vector DB                 │
└─────────────────────────────────────────────────────────────────┘
📁 项目结构
text
project/
├── app.py                      # Flask 主程序（含 v4 API 路由）
├── lesson_prep.py              # 备课逻辑（含知识图谱生成）
├── code_executor.py            # 代码沙箱安全执行
├── file_utils.py               # 文件 / 课程目录工具
├── learner_model.py            # 🆕 学生数字孪生
├── knowledge_graph.py          # 🆕 知识图谱
├── pedagogical_engine.py       # 🆕 教学策略引擎
├── learning_evaluation.py      # 🆕 学习评估闭环
├── agents/                     # 🆕 多 Agent 系统
│   ├── base_agent.py
│   ├── tutor_agent.py
│   ├── planner_agent.py
│   ├── diagnostician_agent.py
│   ├── examiner_agent.py
│   ├── evaluator_agent.py
│   └── coach_agent.py
├── static/
│   ├── css/style.css           # 全局样式（含仪表盘）
│   ├── js/app.js               # 前端逻辑（含仪表盘交互）
│   └── models/                 # Live2D 模型
├── templates/
│   └── index.html              # 单页应用（含仪表盘视图）
└── lessons/                    # 课程数据（不入库）
    ├── {course_id}/
    │   ├── syllabus.json       # 课程大纲
    │   ├── knowledge_graph.json # 🆕 知识图谱
    │   └── ...
    └── learners/               # 🆕 学生档案（不入库）
        └── learner_{id}.json
🚀 快速开始
1. 克隆项目
bash
git clone -b v4.o https://github.com/miaomiao-gua/My_Teacher.git
cd My_Teacher
2. 安装依赖
bash
cd project
pip install -r requirements.txt
3. 配置环境变量
bash
cp .env.example .env
编辑 .env，至少填写一个云端 API Key：

env
MY_TEACHER_CLOUD_API_KEY=sk-your-key-here
MY_TEACHER_CLOUD_MODEL=deepseek-ai/DeepSeek-V3
MY_TEACHER_CLOUD_BASE_URL=https://api.siliconflow.cn/v1
4. 启动服务
bash
python app.py
浏览器打开 http://127.0.0.1:5000

🔌 API 概览（v4 新增）
路由	方法	说明
/api/v4/learner/<learner_id>	GET	获取学生数字孪生数据
/api/v4/learner/<learner_id>/update	POST	更新知识状态
/api/v4/diagnosis/<learner_id>	GET	运行诊断，返回知识漏洞列表
/api/v4/path/<learner_id>	GET	生成自适应学习路径
/api/v4/dashboard/<learner_id>	GET	获取仪表盘数据（雷达、曲线、增益）
/api/v4/evaluate/<learner_id>	POST	计算学习增益和效率
/api/v4/error_memory/<learner_id>	GET	获取错题本
完整 API 列表见 README.md 中的 API 概览部分。

🧪 数据飞轮
text
诊断 → 教学 → 评估 → 学习 → 数据 → 诊断
每个学生每次交互都会更新其数字孪生，每次诊断都会生成更精准的教学策略，每次评估都会验证教学效果，从而形成持续优化的数据飞轮。

📄 License
MIT License

🙏 致谢
Live2D Cubism

Ollama

PixiJS

SiliconFlow

以及所有开源社区贡献者

v4.o 分支，持续迭代中。

从“会说话的 AI 老师”到“能理解、诊断、规划并持续改变学生的自适应教学系统”。
—— 下一站：AI Education OS。
