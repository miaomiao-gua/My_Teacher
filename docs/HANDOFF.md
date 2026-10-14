# 交接单：My_Teacher 项目现状（2026-10-06；**最新更新 2026-10-14，见第 0 节**）

> 给"下一个我"看的。新对话窗口看不到之前的聊天记录，**这份文件就是记忆**。
> 如果只读一段：**第 0 节（最新的改动与坑）** + 第 1 节（现状）+ 第 5 节（硬约束）+ 第 6 节（待办）。

---

## 0. 最新一次改动（2026-10-14）：开课前先站到「别人整理好的大纲」上

**学生的问题**（他原话给了预期流程）：在「数学 / 9709」目录里开个对话说「你给我讲一下吧」，
期待是「认代号 → 去官网找考纲/教材 → 按官方目录规划 → 汇报并直接开讲第 1 课」。
实际是：模型反问「你想学哪个学科」，然后 `list_courses` 列出全部课程让他挑 —— 一次都没去查。

**现在什么样**（真跑 4 次，最后一次 162 秒，工具链与预期完全一致）：

```
research_syllabus(topic="9709")
  → 真去搜 + 真去读：挑中官方考纲 PDF（697427-2026-2027-syllabus.pdf，HTTP 200）
  → PDF 下载并转成 Markdown（11.1 万字），缓存在会话 meta 的 syllabus_cache 里
prepare_course(topic="9709", syllabus_source="<那份 PDF 的 URL>")   ← 正文不用模型手抄
  → 照缓存正文切分单元，界面与摘要标「备课依据（本会话真的读过）」
lesson_context(unit_index=1) → 直接开讲第 1 课（二次函数与方程 / 判别式 / 二次不等式）
```

改动：`kernel/tools/materials.py` 新增 **`research_syllabus`** 工具（搜索 → 按 URL 相关性打分挑候选
→ HTML 去标签 / **PDF 下载转 Markdown** → 记账 + 缓存）、`kernel/tools/course.py` 让 `prepare_course`
自动取用缓存并如实标注依据、`kernel/prompts.py` 写入开课流程。测试：`tests/test_syllabus_grounding.py`（29 项）。

**这次踩到的四个坑（都别再犯）**

1. **提示词是软约束，靠不住**：第一版只写规则，两次真跑分别「凭记忆编了一份考纲 + 挂官网链接」
   和「干脆不查」—— 所以必须是**工具**把检索这件事包死（服务端自己做，来源天然为真）。
2. **站点首页几乎永远不是答案**：第一组查询命中 cambridgeinternational.org 首页，45k 字全是导航；
   真正的考纲页/PDF 在后面那组查询里。工具早先是「哪个查询先返回能用的就吃哪个」，于是吃到了首页。
   现在收集全部候选 → 按 URL 打分（首页重罚；路径带 syllabus / specification / 考试代号加分）。
3. **官方考纲常常是 PDF**：PDF 字节被当 HTML 去标签 → 51 万字符的 `%PDF-1.7 ... xref` 垃圾，
   还被标成「已查证」。现在 PDF 走「下载 + `convert_document_to_markdown`」（pypdf / pdfplumber / fitz
   本机都有），并统一用 `_looks_like_binary()` 拒收二进制垃圾。
4. **我自己的 `except: pass` 吞了真错**：`materials.py` 忘了 `import time`，`time.time()` 抛 NameError
   被静默吞掉 → 缓存永远写不进去，而工具摘要还在说「已缓存」。现在这种写入失败会打日志，
   并且**由返回值决定摘要怎么写**（写不进去就改让模型手抄正文）。

**顺带**：`tests/*.py` 用 GBK 输出，**一旦有失败项就会崩在 `✗` 的编码上、根本看不到哪一项失败**
（这次踩到了；新测试已改成 UTF-8，其余脚本还有这个雷）。跑测试建议先 `$env:PYTHONUTF8='1'`。

---

## 0.1 上一次（2026-10-07）：透明桌宠收尾 + 把验收仪器修好

上一版提交是 `252ae32d`「真透明桌宠（B 方案）」。这次**没改桌宠的功能逻辑**，做的是
「真去实测一遍 + 修掉三个真问题 + 把验收仪器修好」。

**① 实测结果（真起服务、真在桌面上显示角色，数字都是当场量的）**

| 量什么 | 结果 |
| --- | --- |
| 抓帧这条路通不通 | 通：`Page.captureScreenshot` 拿到的 PNG **真的带 alpha**（透明像素 67%、实体 29%） |
| 抓帧耗时 | 中位 **69~85ms/帧**；页面自己渲染 **70fps** → 瓶颈在"抓帧 + PNG 解码（52ms）"，不在 Live2D |
| 端到端帧率 | **9~13fps**（正常）；机器有负载时（学生在旁边开游戏）掉到 **274ms/帧 ≈ 3fps**，已接受降级 |
| 逐像素透明 | ✅ 蒙版内变化 **99.4%**、蒙版外 **0.0%**（3 次试量取最干净那次） |
| 位置对不对 | ✅ `shell._screen_size()` 算出的 pos 与窗口实际位置**逐像素一致**（2560×1600 屏上是 (2220,1100)） |
| 点得穿 | 只有**纯函数**验证（透明处 `HTTRANSPARENT`／角色上 `HTCLIENT`）；**真鼠标点击这次没测** |
| 拖动 / 右键菜单 | 本次**没实测**（代码在 `petwin.py`，上一版写的） |

**② 修掉的三个真问题**

- `desktop/petwin.py` 新增 `make_process_dpi_aware()` 并统一使用。原来那行是
  `shcore.SetProcessDpiAwarenessContext(-4)` —— **`SetProcessDpiAwarenessContext` 在 `user32` 里，
  shcore 里只有 `SetProcessDpiAwareness`**。AttributeError 被 `except` 吞掉，
  于是一直**静默退回 V1**（混合 DPI 的多显示器上角色会被系统再拉一遍，糊）。
  另外参数是伪句柄，必须声明 `c_void_p`。
- `desktop/shell.py` 的 `_screen_size()` 改成**先** `petwin.make_process_dpi_aware()` **再**问尺寸。
- `desktop/frames.py` 的 `stop()` 现在会 cancel 抓帧任务：不再打
  `Task was destroyed but it is pending!`（看着像崩溃，其实只是噪音）。

**③ 最值钱的产出：把"验收仪器"修好了 —— 它之前一直在骗人**

透明桌宠的验收原来是「前后截两张屏幕，看变化像素占比」。这个判定在**学生自己的桌面上不成立**：
同一份代码、同一台机器，实测变化占比 **53% → 94% 反复横跳**，误报过好几次 ❌。
真正的两个原因（都踩实了才写在这里）：

1. **坐标空间中途换了**：进程刚起来时 DPI **不感知**（`GetSystemMetrics` = 1707×1067），
   起桌宠窗口时 `petwin` 才把进程设成感知 → 同一进程里变成 **2560×1600**。
   "起窗口前截的底图"和"之后的图"因此属于两个坐标系，整块画面都不一样（假故障）。
   → **规矩：算任何屏幕坐标之前，先 `petwin.make_process_dpi_aware()`。**
2. **桌面自己在动**：学生在旁边开游戏/切窗口，背景整块地变。背景变化只会**增加**变化量。

现在判定搬到 `agent_studio/tools/_screen_check.py`，做法：
`SetWindowPos` 把窗口**搬到屏幕外再搬回来**当开关（只隔 0.12 秒，不是关掉重建窗口）→
按**窗口当前那一帧的 alpha 蒙版**分开判（蒙版内该大面积变、蒙版外只该剩背景噪声）→
量 **3 次取蒙版外变化最小的那次**（背景只可能加噪声）→
实在判不出来就说「这次不算数」，**而不是报 ❌**。

> **结论：桌宠本体没问题，是验收方法一直在骗人。**
> 以后凡是要做"屏幕像素判定"的自检，直接用 `_screen_check.py`，别再手搓百分比阈值。

**④ 这次验证用的命令**（服务要先起在 5001；探针会让角色在右下角露几秒）

```powershell
py -3.14 run_agent.py --port 5001            # 起服务（另一个窗口）
cd agent_studio
py -3.14 tools\probe_frames.py               # 抓帧带不带 alpha（不需要桌宠窗口）
py -3.14 tools\probe_petwin.py               # 分层窗口：纯函数 + 逐像素透明（不依赖浏览器）
py -3.14 tools\probe_livepet.py 16           # 端到端：Live2D 抓帧 → 桌面透明（会露 16 秒）
```

---

## 1. 一句话现状

老的 Flask 教学网站（`client/` + `server/`，能跑，别动）旁边，长出了一个**独立的 Agent 项目**
`agent_studio/`：内核 + 课程 + 插件 + 网页工作台 + 桌面壳 + 桌宠。**已经在用，能上课、能打包成 exe。**

- 服务：`py -3.14 run_agent.py --port 5001`（用户双击仓库根的 `start_agent.cmd`）
- 工作台：http://127.0.0.1:5001/agent/app
- 桌宠独立窗口：http://127.0.0.1:5001/agent/pet（或双击 `start_pet.cmd`）
- 桌面版：`agent_studio/MyTeacher.exe`（42.9 MB，双击就用，数据接 `agent_studio/data/`）
- 已注册工具：**34 个**（`GET /agent/tools` 看清单）
- 代码量：kernel + tools + web/js + desktop ≈ **13.9k 行 / 39 个文件**（`agent_ui.js` 一个就 4.9k 行）
- **git 里什么都没有**：所有新东西都是 untracked（见第 6 节风险 1）

## 2. 已经能用的功能（用户视角）

| 功能 | 在哪 | 状态 |
| --- | --- | --- |
| 三栏工作台（历史对话 / 对话 / 生成式界面） | `/agent/app` | 可用，分栏线都能拖 |
| 生成式界面渲染器 | 工作台右栏 | calculator / geometry / datalab / solver / plot / draw_board / quiz / tabs / callout / steps / table… |
| 「＋ 计算器」自己写公式画图 | 工作台右上 | 可用（精确度、画质可调） |
| 对话自动归档（纯聊天，AI 判断该放哪个文件夹） | 自动 | 后端已验证（`tests/test_live_filing.py`） |
| 提醒（模型工具 + 桌宠按钮） | `set_reminder` / `/agent/reminder` | 可用 |
| **工作台里的小玩偶**（Live2D，跟着上课演动作） | `/agent/app` 右下角 | 可用：**整身**、无边框、抓哪儿都能拖；控件在她**脚下**（鼠标移上去才出现），右键也有菜单 |
| **换形象**（含「白-免费版」） | 小玩偶脚下「换形象」/ 右键 / 设置中心 / `/agent/pet` | 可用 |
| **设置中心**（模型链路 / base_url / API Key / 人格提示词 / 运行参数 / **桌宠大小** / **上传自己的 Live2D** / **TTS 语音**） | 工作台**左下角「⚙ 设置」** | 可用；密钥只进不出，带「测试连接」和「听一下」 |
| **桌宠会说话（TTS）** | 设置 → 语音（选 Windows 本机语音 + 勾「气泡自动念出来」） | 可用：气泡出现就念出来（念的是洗过的文字） |
| **看一眼屏幕（读屏问答）** | 快捷键 `Ctrl+Shift+T`，或小玩偶聊天框**打字+回车**（默认带截图） | 可用：本机 qwen2.5vl:7b 实测 9.3s，云端 Qwen3-VL 2.4~3.2s |
| 桌宠独立小窗口（Live2D 整身 + 气泡 + 小聊天） | `/agent/pet` | 可用 |
| 桌面版 exe（透明小桌宠 + Edge 应用窗口 + 本地服务） | `MyTeacher.exe` | 可用，本次重打包 |
| 课程 / 教案 / 材料 等老能力 | 插件 `acid_base_lab` + `course/` | 可用 |

## 3. 目录地图

```
D:\My_teacher\
├─ agent_studio\              ← 新项目，所有开发都在这儿
│  ├─ kernel\                 内核：runtime / session / server / llm / prompts / tools\ / filing / pet_bus / pet_models
│  ├─ course\                 课程与文件处理（docx/pypdf 是**可选**导入，缺了不许崩）
│  ├─ plugins\acid_base_lab\  插件（目前只有这一个）
│  ├─ web\                    工作台前端：index.html / pet.html / static\{js,css}
│  ├─ desktop\                桌面壳：shell.py（起服务+应用窗口）/ pet.py（透明桌宠）/ pet_art.py / build_exe.py
│  ├─ tools\                  自检小工具（见第 7 节）
│  ├─ tests\                  18 套测试（13 套离线 + 5 套 live_* 要模型）
│  ├─ data\                   ★ 新项目的运行数据（会话/工作区/密钥/形象选择/提醒）
│  ├─ start.cmd               在这个目录里双击 = 启动新服务（对的那个）
│  └─ MyTeacher.exe           打包产物（dist_desktop\ 里是同一份）
├─ run_agent.py               仓库根的启动器 → **转给 agent_studio/run.py**（见第 6 节坑 0）
├─ start_agent.cmd            用户常双击的那个（调 run_agent.py）
├─ start_pet.cmd              开桌宠页面（服务没起会顺手拉起来）
├─ agent\                     ⚠️ **旧包，已废弃**（2026-09-23 的 16 工具版本，别再改）
├─ client\  server\  data\     老 Flask 项目（老数据在**仓库根 data\**：app.db、users、config.json）
│  └─ client\agent.html       老前端副本（v0.3.0），历史遗留，别当现行界面
└─ docs\superpowers\specs\     设计记录 + docs\HANDOFF.md（本文件）
```

## 4. 数据在哪（**最容易搞混的一点**）

### 4.01 ⚠️ 这个仓库可能**同时有两个会话在改**（2026-10-06 实测）

用户会开多个对话窗口，两个我可能在改同一批文件 —— 症状是编辑时报
「file changed since it was read」、`git status` 里冒出自己没写过的文件
（例如 `_cdp_settings.js`、`_cdp_winshot.js`、`_settings_report.json`、
`pet_page.js` 里多出一个我没写的「设置面板」）。

对策：
- 写文件前先 `read`/`grep` 看当前内容，别整文件覆盖（我差点把另一个窗口的
  `pet_page.js` 设置面板盖掉）；
- 提交时**只 add 自己动过的路径**，别 `git add -A`（否则会把对方半成品一起提交）；
- 发现冲突先问用户「是不是另一个窗口在改」。


### 4.0 服务怎么起、怎么判断它还活着（运维常识，踩过）

- **用户那条路**：双击 `start_agent.cmd`（或仓库根 `start_pet.cmd`、或 `MyTeacher.exe`）。
- **我调试时那条路**：`py -3.14 agent_studio/run.py --port 5001`。
- ⚠️ **我起的后台服务会被环境回收**：DSH 环境重启时，后台 job 会被杀（Windows 强杀表现为
  `exit code 1`，**不是代码崩了**，日志里看不到任何异常）。而且有时**只有外层 shell 被杀、
  python 子进程活下来变成孤儿**——所以：
  **判断服务是否活着，只看 `curl /agent/health`，不要看 job 状态。**
- 为了避免学生「双击两次看到端口占用就以为坏了」，`agent_studio/run.py` 现在会先探一下
  端口上是不是已经有自己的服务：是的话打印「已经有一个在跑了」并直接退出（0），
  不会抛 `address already in use`。（`tests/test_launcher.py` 有验证）
- 想让服务活得久：让用户自己双击 `start_agent.cmd`（那个黑窗口就是服务的寿命）。

### 4.1 两个 data 目录
有两个 `data/`，**不是同一份**：

- `agent_studio/data/` ← 新项目用这个。`sessions/`、`workspaces/`、`config.json`（API Key）、
  `folders.json`（归档文件夹）、`pet_model.json`（当前形象）、`reminders.json`、`live2d_vendor/`（Live2D 运行时）、`logs/`
- `data/`（仓库根）← **老 Flask 项目**在用（app.db / users / config.json / lessons）。
  我调试时的临时文件（`_server.log`、`_build.log`、`agent/` 探针脚本、`browser/` 探针用的浏览器配置）也堆在这里。

选目录的规则在 `agent_studio/runtime_paths.py`：环境变量 `AGENT_STUDIO_DATA` > exe 同目录 `data/` >
上级 `agent_studio/data` > 上级 `data/` > `%APPDATA%`。**优先级已用测试锁住，不要随便改。**

## 5. 硬约束（这几条不知道就会白折腾很久）

1. **只有 `py -3.14` 装了依赖**（`C:\Users\jinti\AppData\Local\Programs\Python\Python314\python.exe`）。
   Python 3.11 在本机但没有依赖。
2. **本机装不了新 Python 包**：pypi、清华镜像、npmmirror、unpkg、jsdelivr、cubism.live2d.com **全部不通**
   （实测；但 **Edge 浏览器**能通，所以才有"让浏览器下载、交给服务端落盘"那招）。
   能用的是 `cn.bing.com`、`baidu.com`、`api.siliconflow.cn`、`localhost`。
3. **Live2D 运行时已经存到本机**：`agent_studio/data/live2d_vendor/`（三个 JS，783 KB）。
   页面顺序是「本地 → CDN → 服务端画的备用形象」。**别删这个目录**；删了页面还能用（备用形象），
   联网时点页面上「存到本机」按钮就能补回来。
4. **打包（PyInstaller onefile）**：必须 `--clean`（否则复用旧缓存，打出旧版）；
   `--paths course` 会把 torch/cv2/scipy 拖进来（375 MB 事故），course 只能用 `--add-data`。
5. **编码**：中文文件一律用 write/edit 工具读写。**不要**用 PowerShell 的 `Get-Content | Set-Content`
   或 `-replace` 改文件（会变成 GBK/乱码）。`.cmd` 里带中文的括号块会被 cmd 解析错，用标签跳转 + CRLF。
6. **验证必须真跑**：改了渲染器就 `AgentUI.version` + `index.html ?v=` 一起加；
   怀疑"改了没生效"就先看 `/agent/version` 里 renderer 的 mtime。
   真浏览器的验证靠 CDP 探针（第 7 节），**每个探针都要用 `/json/new` 自己开新标签**——
   连到别的标签上量出来的数据全是错的（踩过）。

## 6. 待办 / 需要用户拍板的

**坑（本次会话最后一刻发现，已修）**

0. **启动器指向了旧包**：仓库根的 `run_agent.py` 原本写的是 `agent.server:app`，
   那是九月的旧包 `agent/`（**16 个工具**、没有桌宠/自动归档/新界面）。
   也就是说：**双击 `start_agent.cmd` 会打开老界面，新做的东西一个都看不到**。
   之所以一直没暴露，是因为我调试时手动跑的是 `agent_studio/run.py`，
   用户浏览器一直连着我这个进程。
   现已改为转给 `agent_studio/run.py`（`uvicorn.run("kernel.server:app")`），
   并实测确认：**34 个工具**、`/agent/pet` 与 `/agent/live2d/models` 都是 200。
   → 以后新增功能后，**务必用用户那条入口（双击 start_agent.cmd）验一次**，别只在自己起的进程上验。

**风险（建议尽快处理）**

1. ~~全部代码没进 git~~ → **已提交快照** `a8ae36e8`（177 个文件，1.9 MB；
   `agent_studio/data/`、`dist_desktop/`、`*.exe`、探针截图与浏览器配置都已 ignore）。
   以后改完记得提交，改坏了才有后悔药。
2. `agent_ui.js` 已经 4888 行，改一处要通读上下文。下次要动它之前，先考虑把渲染器拆成
   独立文件（`js/renderers/*.js`），否则每次改动都在赌运气。
3. 仓库根的 `agent/` 是**死包**（旧版内核）。留着会让下一个人改错地方，建议要么删、
   要么在 `agent/__init__.py` 顶上写三行「已废弃，现行代码在 agent_studio/」。**不要**在没确认
   老前端有没有用它之前就删（`client/agent.html` 是老界面副本，可能还指着它）。

**等用户回答**

3. ~~**桌面版有两只桌宠**：窗口里的 Live2D 小玩偶 + 桌面上那只 tkinter 画的透明小桌宠。~~
   **已不再成立（2026-10-07 核对）**：`shell.py` 的默认分支现在起的是**真·透明桌宠**
   （`livepet.LivePet` → 分层窗口），那只 tkinter 的 floater（`desktop/pet.py`）**已经不在默认路径里**
   （只有老 `--pet-window` 分支会起"Edge 小窗"做对照），所以桌面版默认**只有一只**。
   要不要把 `desktop/pet.py`（废弃的 tkinter 桌宠）删掉，仍等用户表态。

**还没做完 / 没实测**

6. **打包后「悄悄少功能」的三个真原因（2026-10-06 全踩过一遍）**：
   - **插件 import 了旧包**：`plugins/acid_base_lab/handler.py` 里写的是
     `from agent.registry import ...`（仓库根那个九月的旧包）。源码里跑得通（老包还在），
     打包后老包不存在 → 插件**静默加载失败**（`/agent/plugins` 里能看到 `No module named 'agent'`），
     桌面版就少了「酸碱盐虚拟实验室」。已改成 `from kernel.registry import ...`。
     **规律：插件是给内核用的，只认 `kernel` 这个包名。**
   - **pywin32 的动态部分**：TTS 用 `win32com` 调 SAPI，PyInstaller 静态分析看不见 →
     必须写进 `build_exe.py` 的 `HIDDEN`（win32com.client / pythoncom / pywintypes / win32api / win32timezone）。
     不写的话：源码里语音好用，exe 里一点声音都没有。
   - **exe 的「项目根」按它自己所在目录算**（`config.py` 的 LEGACY_ROOT = exe_dir().parent）：
     所以 `agent_studio\MyTeacher.exe` 能看到 `client/` 里的模型（7 个），
     `dist_desktop\MyTeacher.exe` 只看到桌面上那几个（3 个）。
     **验 exe 就要验用户双击的那份**（`tools/verify_exe.py` 已默认这份）。
   - 自检工具：`tools/verify_exe.py`（起 exe → 打接口 → 关掉，含 TTS/密钥/插件）、
     `tools/diff_exe_vs_source.py`（源码 vs exe 逐个比工具与形象，专抓"少功能"）。

   - **插件 import 了旧包**：`plugins/acid_base_lab/handler.py` 里写的是
     `from agent.registry import ...`（仓库根那个九月的旧包）。源码里跑得通（老包还在），
     打包后老包不存在 → 插件**静默加载失败**（`/agent/plugins` 里能看到 `No module named 'agent'`），
     桌面版就少了「酸碱盐虚拟实验室」。已改成 `from kernel.registry import ...`。
     **规律：插件是给内核用的，只认 `kernel` 这个包名。**
   - **pywin32 的动态部分**：TTS 用 `win32com` 调 SAPI，PyInstaller 静态分析看不见 →
     必须写进 `build_exe.py` 的 `HIDDEN`（win32com.client / pythoncom / pywintypes / win32api / win32timezone）。
     不写的话：源码里语音好用，exe 里一点声音都没有。
   - **exe 的「项目根」按它自己所在目录算**（`config.py` 的 LEGACY_ROOT = exe_dir().parent）：
     所以 `agent_studio\MyTeacher.exe` 能看到 `client/` 里的模型（7 个），
     `dist_desktop\MyTeacher.exe` 只看到桌面上那几个（3 个）。
     **验 exe 就要验用户双击的那份**（`tools/verify_exe.py` 已默认这份）。
   - 自检工具：`tools/verify_exe.py`（起 exe → 打接口 → 关掉，含 TTS/密钥/插件）、
     `tools/diff_exe_vs_source.py`（源码 vs exe 逐个比工具与形象，专抓"少功能"）。

**7. 设置中心的几个坑（都踩过，别再犯）**
   - 弹层挂在 `document.body` 上，**不在 `.au-root` 里** → `var(--au-panel)` 取不到，
     背景会变透明、底下的对话透上来。所有变量都要写 `var(--x, 兜底值)`。
   - `config.json` 里没写过的键，界面输入框会是空的；学生一按保存，空值变成 `NaN`，
     服务端判「要填数字」，**整份设置都存不进去**。现在 `read_state()` 回**实际生效的默认值**，
     前端也跳过空值（= 不改）。
   - 人格提示词只替换 `BASE_PERSONA` 那一段；`TOOL_POLICY`（工具使用硬规则）**不开放编辑** ——
     那不是人格，是功能约束，删了模型就用错工具。界面上写明了。
   - `pet_scale` 是 float，**它的滑杆分支必须排在「int/float 数值分支」前面**，
     否则会被数值分支先接走，渲染成普通数字框（学生要的是滑杆手感）。
   - **音色是输入框，不是下拉**（学生明确要求）：要支持自定义音色，只能填名字。
     所以「听一下」必须把**界面上正在编辑、还没保存**的值一起发给
     `/agent/tts/say` 的 `override` —— 否则每换一个音色都要先保存才能试听。
   - 测试**不许往学生真实的 `data/config.json` 写假密钥**（服务进程和另一个会话都在读它）。     `test_settings_api.py` / `test_pet_upload_tts.py` 都切到 `data/_*_test` 临时目录；
     临时目录里如果带着复制的 `enc$` 密文，会刷一排「密钥解密失败」的噪音 —— 直接不复制密文字段。
     真实配置是否健康，用 `tools/diag_keys.py` 现场验（它会明说"拿到的还是不是 enc$ 密文"——
     注意 `bool(key)` 判断是错的，密文也是真值）。

4. **「已自动归档」提示自动刷新左侧树**：代码写了（`agent_ui.js` 的 `notice` 分支里调 `refreshTree()`），
   **但没在真浏览器里实测过**（node 桩不支持流式事件）。验法：在工作台里聊一个明显属于某学科的话题，
   等这轮跑完，看左边文件夹树是否自己移动。
5. ~~**桌面 exe 里的 Live2D 小玩偶**：…但**没有做成桌面上的透明 Live2D 浮窗**——那需要 WebView2 分层窗口，还没动。~~
   **已完成（2026-10-06 提交 `252ae32d`，2026-10-07 实测通过）**：桌面版默认就是**真·透明 Live2D 桌宠** ——
   无头 Edge 渲染 `/agent/pet?frames=1` → CDP 抓带 alpha 的 PNG → `UpdateLayeredWindow` 逐像素透明贴到桌面。
   实现见 `desktop/frames.py`（抓帧）+ `desktop/petwin.py`（分层窗口）+ `desktop/livepet.py`（合成/总线/降级）。
   **走的是我们自己的分层窗口，不是 WebView2**（原来以为必须 WebView2，实测不必）。本次实测数据见第 0 节。
6. **形象清单扫盘范围**：目前会扫「项目内 / 用户的 client/static/models / 桌面 / 下载 / Open-LLM-VTuber」。
   想加目录就写 `agent_studio/data/pet_model_dirs.json`（一行一个路径），不用改代码。
7. **老项目还在**：`client/`、`server/` 没删，两套并存是**刻意**的（出问题能退回老界面）。
   什么时候合并/下线，等用户表态。

## 7. 自检怎么跑（都能一条命令）

```powershell
# 服务（用户平时双击 start_agent.cmd）
py -3.14 run_agent.py --port 5001

# 离线测试：13 套全绿（本次会话都跑过）
cd agent_studio; py -3.14 tests\test_paths.py      # 数据目录选择（17 条）
py -3.14 tests\test_launcher.py                    # ★启动器必须指向新项目（别改回旧包）
py -3.14 tests\test_desktop.py                     # 桌面壳/桌宠
py -3.14 tests\test_filing.py                      # 自动归档的硬规则
py -3.14 tests\test_calculator.py tests\test_small_tools.py
py -3.14 tests\test_pet_models.py                  # 形象扫盘/防穿越/记住选择
py -3.14 tests\test_settings_api.py                # 设置中心：密钥只进不出/白名单/人格进提示词
py -3.14 tests\test_pet_page.py --http             # 桌宠页面与接口
py -3.14 tests\test_course_flow.py tests\test_materials.py tests\test_nudge.py
py -3.14 tests\test_quiz_flow.py tests\test_quiz_mapper.py tests\test_whereami.py

# 要模型的（按需，费 token）：tests\test_live_*.py（5 套）

# node 侧（不需要浏览器）
node ..\data\agent\_app_tests.js _ui_tests.js _calc_tests.js _tools_tests.js _md_tests.js _pet_core_tests.js

# 真浏览器（先起 Edge：--headless=new --remote-debugging-port=9333 --user-data-dir=data\browser\cdp_pet）
node ..\data\agent\_cdp_doll.js      # 工作台里的小玩偶：事件驱动/换形象/参数演绎
node ..\data\agent\_cdp_wb_settings.js  # 工作台「设置中心」：入口/字段/密钥不回显/保存生效/桌宠整身无框
node ..\data\agent\_cdp_doll_verify.js  # 三栏还在 + 抓住角色能拖 + 点击不乱跑 + 位置记得住
node ..\data\agent\_cdp_pet_v2.js       # 她身上无控件 / 设置里的滑杆·上传·TTS / 试听出音频
node ..\data\agent\_cdp_tts.js         # TTS 接线：气泡→合成→播放；台词清洗；过程提示不念；不叠着念
node ..\data\agent\_cdp_tts_petpage.js # 独立桌宠页（桌面小窗那一页）也会念 + 形象加载
node ..\data\agent\_cdp_wb_settings.js  # 左下角设置中心：字段/密钥不回显/保存生效
node ..\data\agent\_cdp_pet_head.js    # 她头顶上方没有任何元素
node ..\data\agent\_cdp_pet.js       # 独立窗口页：气泡/提醒/兜底形象
node ..\data\agent\_cdp_probe.js     # 三栏布局与分栏线拖动
node ..\data\agent\_cdp_close.js     # 关掉探针浏览器（只关自己那个实例）

# 自检小工具
py -3.14 tools\list_pet_models.py      # 看扫到哪些形象
py -3.14 tools\check_pet_http.py       # 形象接口端到端
py -3.14 tools\peek_exe_size.py        # exe 里谁占体积
py -3.14 tools\peek_exe_modules.py     # exe 里带没带某个模块（打包后 500 的第一嫌疑）
py -3.14 tools\cleanup_test_sessions.py --apply   # 清掉测试留下的对话

# ★ 真·透明桌宠的三个探针（2026-10-07 重写过判定；服务要先起在 5001）
py -3.14 tools\probe_frames.py         # 抓帧带不带 alpha（不显示桌宠，最快）
py -3.14 tools\probe_petwin.py         # 分层窗口：预乘/点穿纯函数 + 逐像素透明（不靠浏览器）
py -3.14 tools\probe_livepet.py 16     # 端到端：Live2D → 桌面（角色会露 16 秒 + 闪几下）
#   判定逻辑在 tools\_screen_check.py：先把窗口搬到屏幕外当开关（0.12 秒）→
#   按窗口那帧的 alpha 蒙版分开判 → 量 3 次取最干净的一次 → 判不出来就说"不算数"。
#   ⚠️ 别再手搓"变化像素百分比 < 60%"这种阈值：在学生的忙桌面上它一定骗你（第 0 节 ③）。
```

打包：`py -3.14 agent_studio\desktop\build_exe.py --clean`（1~3 分钟，产物 42.9 MB）。

## 8. 「看一眼屏幕」这条链路的要点（2026-10-06 新增）

- **主链路（DeepSeek-V3）看不见图**，所以看图要单独选一条视觉链路：
  `vision_provider` = off/local/cloud，`vision_model` 默认 `qwen2.5vl:7b`（这台机器上就有，免费离线）。
  实测（`tools/bench_vision.py`）：本机 qwen2.5vl:7b 看 1400px 截图 **9.3s**
  （首次要等模型加载，约 40s）；云端 Qwen3-VL-8B 3.2s、Qwen3-VL-30B-A3B 2.4s（花钱、要联网）。
- **截屏在服务端做**（`vision.grab_screen()`），不在前端：这样「按快捷键」和「网页里点按钮」
  走同一条路，只有一份实现。`POST /agent/vision/ask` 的 `image` 可以不带（服务端自己截）。
- **答案走桌宠总线**：服务端 publish 一条 `notice` → 网页版小玩偶和桌面小窗的气泡同时显示
  （一份文案两处显示，别写两套）。
- **全局快捷键在 `desktop/hotkey.py`**：主进程里一条 Win32 消息循环线程，**不创建任何窗口**
  （学生明确说过「别开那么多窗口」）。注册不上只写日志（被别的软件占用很常见），
  界面上还有「👀 看一眼屏幕」按钮可以手点。快捷键解析在 `core.parse_hotkey`
  —— **必须带修饰键**，否则会抢掉普通按键。
- **带上下文**：`vision.ask(..., session=...)` 会把最近 6 轮对话一起发过去。
  学生先问「我在用 PS」，再问「套索在哪」，没有上下文模型不知道「那个」指什么。
- 自检：`tests\test_vision.py --live`（画一张已知内容的图，问它图里的数字，答对才算通）；
  `tools\try_vision.py "问一句"`（对真屏幕打一次接口）。

## 9. 设计记录（踩坑细节）

- `docs/superpowers/specs/2026-10-04-desktop-pet-design.md`：桌面壳与桌宠、打包 8 个坑
- `docs/superpowers/specs/2026-10-06-pet-doll-and-model-switch.md`：小玩偶与换形象，
  含 Live2D 取景三个坑、`afterMotionUpdate` 参数钩子、canvas 上下文不能复用、
  以及"`#stage` 里只能放角色，否则聊天抽屉被清掉"
- `docs/superpowers/specs/2026-10-07-transparent-desktop-pet.md`：**真·透明桌宠（B 方案）**
  —— 整条链路（无头 Edge → CDP 抓 alpha → 分层窗口）、实测数字（70fps 页面 / 69ms 抓帧 /
  9~13fps 端到端）、四个坑（**DPI 坐标空间中途会换**、屏幕像素判定为什么会骗人、
  气泡把窗口撑高、`Task was destroyed` 的真相）、以及还没测的东西

## 10. 新窗口第一句话可以这么说

> 读一下 `docs/HANDOFF.md`（重点第 0 节）和三份 spec，然后我们继续。
> （要做什么就直说；不确定的先问我。）
