# My Teacher — AI 全能私教

一个基于 Flask 的 AI 学习辅导平台，支持本地 Ollama / 云端大模型对话、语音朗读（TTS）、按课程分章节教学、随堂测验、代码运行等功能。前端采用 Galgame 视觉小说风格。

## 功能

- **智能对话**：支持本地 Ollama 模型 + 云端模型（硅基流动 / DeepSeek / OpenAI 兼容 API）双通道，自动回退
- **备课系统**：输入主题自动生成课程大纲、分课教学、预设测验题
- **分段输出**：老师回复分段显示，按 Enter 逐段展开（视觉小说体验）
- **随堂测验**：单选 / 多选 / 判断 / 填空，自动批改，支持逐题作答
- **语音朗读**：本地 edge-tts + 云端 CosyVoice2 TTS，回复后自动播放
- **分课进度**：顶部进度条显示当前课时，及格后自动进入下一课
- **代码运行**：对话中可运行 Python 代码（沙箱执行）
- **视觉定制**：教师头像上传、立绘位置与动画调节、场景背景主题 / 主页背景独立设置
- **键盘操作**：ESC 关闭弹窗 / 返回菜单 / 跳过分段

## 快速开始

### 1. 安装依赖

```bash
pip install -r project/requirements.txt
```

### 2. 配置

**方式一：环境变量（推荐）**

```bash
cp .env.example .env
```

编辑 `.env`，至少填写一个云端 API Key：

```env
MY_TEACHER_CLOUD_API_KEY=sk-your-key-here
```

环境变量优先级高于 `config.json`，也可通过 Web UI「设置」面板修改其他选项。

**方式二：Web UI 配置**

首次启动后，项目会自动生成 `project/config.json`，点击界面右上角「⚙️ 设置」直接填写。

### 3. 启动

```bash
cd project
python app.py
```

浏览器打开 `http://127.0.0.1:5000`

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | Python / Flask |
| 前端 | 原生 HTML + CSS + JS |
| 模型 | Ollama（本地）/ 硅基流动 / DeepSeek / OpenAI 兼容 API |
| TTS | edge-tts（本地）/ CosyVoice2（云端） |
| 数学渲染 | KaTeX |

## 项目结构

```
My_teacher/
├── .env.example           # 环境变量模板（复制为 .env 后填写）
├── .gitignore
├── project/
│   ├── app.py              # Flask 主程序
│   ├── lesson_prep.py       # 备课逻辑
│   ├── code_executor.py     # 代码沙箱执行
│   ├── file_utils.py        # 文件/课程工具
│   ├── check_js.py          # JS 语法检查
│   ├── requirements.txt     # Python 依赖
│   ├── config.json          # 配置文件（不纳入版本控制）
│   ├── static/
│   │   ├── css/style.css    # 样式
│   │   ├── js/app.js        # 前端逻辑
│   │   └── images/          # 图片资源
│   ├── templates/
│   │   └── index.html       # 页面模板
│   └── lessons/             # 课程数据（不纳入版本控制）
```
