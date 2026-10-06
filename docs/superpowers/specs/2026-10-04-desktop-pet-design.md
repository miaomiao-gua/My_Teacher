# 桌面版 + 桌宠（第一代）设计

日期：2026-10-04
状态：**v1 已交付并实测通过**（用户 2026-10-04：「先打包第一代」，预计后续迭代）

## 交付结果（第一代）

| 项 | 结果 |
|---|---|
| 产物 | `agent_studio/艾琳老师.exe`（onefile，52.8 MB，97~148 秒可重建） |
| 双击启动 | ✅ 实测：起服务（自动挑端口）→ 开无地址栏应用窗口 → 桌宠出现 |
| 数据继承 | ✅ 实测 `frozen=True` 时 `data_root=D:\My_teacher\agent_studio\data`（现有会话/密钥原样可用） |
| 桌宠 | ✅ 实测出现在桌面上：可拖、透明无边框、气泡说话、状态心跳写入 `data/pet_state.json` |
| 事件桥 | ✅ `/agent/pet/events` SSE + `set_reminder` 工具；桌宠订阅数 1，交卷/工具结果会冒气泡 |
| 自测 | `tests/test_paths.py`（17 项）、`tests/test_desktop.py`（50 项）、既有的 10 个套件全绿 |

### 打包路上踩到并修掉的坑（都留了注释）

1. **数据目录进只读解包目录** → 引入 `runtime_paths`（只读资源 / 可写数据分开），
   并把四处各写各的路径（`config` / `crypto_utils` / `course.file_utils` / `course.py` 等）统一收口。
2. **`sys.stdout`/`sys.stderr` 在窗口程序里是 `None`** → `print` 会炸；
   uvicorn 的默认 formatter 还要问 `sys.stderr.isatty()`，报「Unable to configure formatter 'default'」
   → 显式给 uvicorn 一份**写文件的日志配置** + 把两个流接到日志文件。
3. **onefile 启动要往 %TEMP% 解包** → 沙箱里会被拒（真机上没问题）；验收脚本改为在放宽权限下跑。
4. **加了 `--paths course` 导致产物 47MB → 375MB** → course/*.py 用 `--add-data` 带过去即可，
   并显式 `--exclude-module torch/cv2/scipy/...`（那是本机 OCR 依赖，教学用不到）。
5. **PyInstaller 复用缓存打出旧代码** → 构建固定加 `--clean`。
6. **`艾琳老师.exe` 复制到项目目录失败（被占用）却只打了一行警告** → 结果我拿旧 exe 查了半天；
   验收改成始终跑 `dist_desktop/` 里那份新的。
7. **透明窗口的色键渗边**（抗锯齿边缘发品红）→ `flatten_for_key()` 把边缘压成描边色。
8. **`Toplevel` 相对导入在直接跑脚本时失败** → desktop 内部统一绝对导入。

### 第一代的已知取舍（后面可以改）

- 主窗口是 Edge/Chrome 的应用模式窗口（不是内嵌浏览器）：机器上没有 pywebview（PyPI 不通），
  自研 WebView 宿主不值得。好处是现有前端一行都不用改。
- 桌宠的气泡说得很短（一句话）；长内容去主界面看 —— 有意为之，避免弹幕。
- 桌宠的「跟艾琳说句话」接在**最近更新的那个对话**里（它不知道主界面正开着哪条）。
- 单实例判定要求「pid 活着 + 端口在监听」；如果上一次没退干净（端口没了），会允许重新启动。
- `--onefile` 每次启动要解包（冷启动约 2~4 秒）；嫌慢可以改 onedir。

## 目标

双击一个 `.exe` 就能用：自带本地服务、独立应用窗口（无地址栏）、桌面上有一只**会互动的学伴角色**。
不重写现有前端（HTML/Canvas/KaTeX 全部复用）。

## 硬约束（已实测）

| 事实 | 影响 |
|---|---|
| PyInstaller 6.21.0 已装，且能在 Python 3.14 上出 exe（实测打出 11.9MB 最小 exe） | 可以打包 |
| tkinter 8.6 + Pillow 12.3 已装 | 桌宠零新依赖；角色形象用 Pillow 现场画 |
| **PyPI 不可达**（pypi.org / 清华镜像均拒连） | 不能引入 pywebview 等新依赖 |
| Python 3.11 上没有 fastapi/uvicorn/openai/httpx | 打包只能基于 3.14 |
| WebView2 运行时已安装（154.0.4258.53） | Edge 应用模式可用（但本代直接用 msedge --app，不自己写 WebView 宿主） |

## 已发现的必解问题

`kernel/config.py` 里 `ROOT = Path(__file__).resolve().parent.parent`、`DATA_DIR = ROOT/data`。
打包后 `__file__` 落在 PyInstaller 的临时解包目录（`_MEIPASS`），于是：

- **运行时数据会写进只读/临时目录** → 每次启动丢失会话、密钥、课程数据。

解决：引入 `kernel/paths.py`：

- `is_frozen()`：是否在打包环境
- `resource_root()`：打包时 `sys._MEIPASS`，否则仓库根 —— 只读资源（web/、plugins/、course/）走它
- `data_root()` 优先级：
  1. 环境变量 `AGENT_STUDIO_DATA`
  2. **exe 同目录的 `data/`**（能与现有 `agent_studio/data/` 无缝重合）
  3. 上级目录里的 `agent_studio/data/`
  4. `%APPDATA%\AgentStudio\data`
- 选中的目录要**打印/记录**出来，便于排查。

## 架构

```
艾琳老师.exe
 ├─ 启动器 desktop/shell.py
 │   ├─ 单实例检查（锁文件 + 端口探测；已在跑就把窗口提到前面）
 │   ├─ 挑空闲端口 → uvicorn 跑在后台线程（复用 kernel.server:app）
 │   ├─ 轮询 /agent/health 就绪
 │   ├─ 打开应用窗口：msedge --app=URL（无地址栏）→ 失败回退默认浏览器
 │   └─ 启动桌宠（tkinter 主循环跑在主线程）
 ├─ 桌宠 desktop/pet.py（无边框 + 透明 + 置顶 + 可拖）
 │   ├─ 形象：desktop/pet_art.py 用 Pillow 画（不塞图片二进制进仓库）
 │   ├─ 状态机：idle / blink / think / happy / sleepy / alert
 │   ├─ 气泡：头顶小窗（同样的透明技巧，带小尾巴）
 │   ├─ 迷你聊天：同一 agent，消息并入当前打开的对话
 │   └─ 右键菜单：打开主界面 / 说句话 / 提醒 / 换配色 / 隐藏 / 退出
 └─ 后端桥
     ├─ GET /agent/pet/events（SSE：run.start / run.end / tool.result / quiz / reminder）
     ├─ kernel/pet_bus.py（进程内广播，runtime 在各节点 publish）
     └─ 工具 set_reminder（写 data/reminders.json；服务端小任务到点 publish）
```

## v1 范围（做）

1. 便携数据目录 + 资源目录（含单测）
2. 启动器：单实例、空闲端口、就绪等待、Edge 应用窗口（回退浏览器）
3. 桌宠：Pillow 画形象、拖拽/置顶/位置记忆、点它开主界面、右键菜单、眨眼/打盹小动作
4. 气泡 + 迷你聊天（并入当前对话）
5. 事件流 `/agent/pet/events` + `set_reminder` 工具 + 到点提醒
6. PyInstaller onefile 打包 `艾琳老师.exe`（含 web/plugins 资源、图标），写在 `desktop/build_exe.py`
7. 测试：paths / 桌宠纯逻辑 / 提醒调度 / 事件总线（无显示器也能跑）+ 真 exe 启动验收

## v1 不做（YAGNI）

- 语音朗读 / 语音输入（本机无可用的离线语音组件）
- 装扮、养成、多套造型商城
- 自动更新、崩溃上报
- 重写前端或自研 WebView 宿主
- 系统托盘图标（用桌宠的右键菜单代替；pystray 装不上）

## 验收标准（第一代）

| 项 | 判定方式 |
|---|---|
| 数据不丢 | exe 放在 `agent_studio/` 下时，`data_root()` 指向 `agent_studio/data`，现有会话/密钥原样可见 |
| 能双击启动 | 真跑 exe：进程活着、`/agent/health` 返回 ok、选中的端口被监听 |
| 应用窗口 | 出现一个无地址栏窗口（标题为「艾琳老师」）；Edge 缺失时用默认浏览器且不报错 |
| 桌宠在桌面上 | 桌宠状态文件 `data/pet_state.json` 有心跳（pid/x/y/state/ts），窗口可拖动（坐标变化被记录） |
| 形象像样 | `py -3.14 -m desktop.pet_art --out pet_states.png` 导出一张状态图，人眼确认不是一团糊 |
| 会互动 | 气泡能被事件触发（跑一次对话，run.start/run.end 至少各收到一次）；右键菜单项齐全 |
| 打包产物 | `艾琳老师.exe` 存在、大小合理（预计 40~60MB），能启动并完成上面各项 |

## 实施顺序

1. `kernel/paths.py` + 改 `config.py`/`server.py` 走它 → 单测
2. `desktop/pet_art.py`（Pillow 画形象）+ `--out` 导出状态图 → 人眼看图
3. `desktop/core.py`（纯逻辑：端口、Edge 参数、状态机、提醒时间、位置夹取）→ 单测
4. `kernel/pet_bus.py` + `/agent/pet/events` + `set_reminder` 工具 → 单测
5. `desktop/pet.py` + `desktop/shell.py` → 手动/半自动验收
6. `desktop/build_exe.py` → 真打包 → 真跑一次
7. README 增补「桌面版」一节
