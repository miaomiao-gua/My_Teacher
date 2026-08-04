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

复制 `project/config.json`（首次运行会自动生成默认配置），主要配置项：

```json
{
  "enable_local_ollama": true,
  "ollama_base_url": "http://127.0.0.1:11434",
  "ollama_model": "qwen2.5:7B",
  "cloud_api_key": "你的硅基流动 API Key",
  "cloud_model": "deepseek-ai/DeepSeek-V3"
}
```

也可通过界面的「设置」面板直接配置。

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
└── .gitignore
```
