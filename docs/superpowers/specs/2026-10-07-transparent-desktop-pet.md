# 真·透明桌面桌宠（B 方案）—— 设计记录与实测

> 2026-10-06 实现并提交（`252ae32d`）；**2026-10-07 实测、修坑、重写验收判定**（本文档记录的就是这两次）。
> 相关：`docs/HANDOFF.md` 第 0 节（最新状态）、第 5 节（硬约束）。

---

## 1. 为什么走这条绕路

浏览器窗口**不可能只显示角色**：窗口总有底、有边框、有阴影。所以反过来做 ——
**让无头 Edge 去渲染角色，我们把带 alpha 的帧抓出来，贴到一个逐像素透明的分层窗口上**。

```
/agent/pet?frames=1  ← 纯角色页（body.frames：底色透明、界面元素全隐藏）
        │  无头 Edge（--headless=new --use-angle=swiftshader）
        ▼
Page.captureScreenshot(png) + Emulation.setDefaultBackgroundColorOverride(alpha=0)   ← 拿到带 alpha 的 PNG
        │  CDP / websocket
        ▼
desktop/frames.py   FrameSource：抓帧线程（自己的 asyncio 事件循环常驻）
        ▼
desktop/livepet.py  LivePet：合成气泡 + 订阅桌宠总线 + 抓不到帧时降级成 Pillow 形象
        ▼
desktop/petwin.py   LivePetWindow：预乘 BGRA → UpdateLayeredWindow（ULW_ALPHA）逐像素透明
        ▼
桌面上的她（没有窗口框、没有底，透明处鼠标点得穿）
```

演示时**别用 WebView2**：原以为"桌面透明 Live2D 必须要 WebView2"，实测**不必**
（自己起分层窗口 + CDP 抓 alpha 就够了，代码量还小）。

## 2. 三个文件各管什么

| 文件 | 职责 | 关键点 |
| --- | --- | --- |
| `desktop/frames.py` | 起无头 Edge、按需吐 RGBA 帧 | ① `Emulation.setDefaultBackgroundColorOverride` 才会给 alpha；② `--use-angle=swiftshader --enable-unsafe-swiftshader` 无头下才有 WebGL；③ `--window-size` 在无头下不一定生效，必须 `Emulation.setDeviceMetricsOverride` 定死视口；④ **事件循环要在自己的线程里 `run_forever`**（websockets 的保活需要循环一直转，只在自己抓帧时转一会儿 → 第二次抓帧永久挂住）；⑤ `stop()` 要 cancel 抓帧任务，否则退出一条 `Task was destroyed but it is pending!` |
| `desktop/petwin.py` | 分层窗口：上屏、拖动、点穿、右键菜单 | ① `UpdateLayeredWindow` 要**预乘 alpha**（用 Pillow 的 `ImageChops.multiply`，别写 Python 循环：15 万次迭代一帧 200ms）；② `WM_NCHITTEST` 按 `alpha_at()` 返回 `HTTRANSPARENT`/`HTCLIENT` 实现点穿；③ 右键菜单用 Win32 原生（这窗口里没有 tk）；④ `make_process_dpi_aware()`（见坑 1） |
| `desktop/livepet.py` | 合成（角色 + 气泡）、订阅总线、降级 | ① 气泡用 **RGBA 画**（不要色键，会有色边）；② 预热期"抓不到帧"**不算失败**（模型加载 4~5 秒，我第一版把它当失败，桌宠刚起来就自己降级了）；③ 抓帧断 2.5 秒才降级成 `pet_art` 形象 |

## 3. 实测数据（2026-10-07，2560×1600 / 150% 缩放，机器上还开着游戏）

| 量什么 | 数字 | 怎么量的 |
| --- | --- | --- |
| 页面自己渲染 | **70.8 fps** | 页内 `requestAnimationFrame` 计数 2 秒 |
| `captureScreenshot`（fromSurface=True, optimizeForSpeed） | 中位 **69ms**，base64 ≈109KB | 连打 10 次取中位 |
| PNG 解码（Python 侧） | **52ms** | `Image.open(...).convert("RGBA")` |
| 抓帧线程端到端 | **74~93ms/帧（9~13fps）** | `FrameSource.avg_ms` |
| 有负载时 | **274ms/帧（3fps）** | 同一份代码，学生在旁边开游戏 |
| 帧里的 alpha | 透明 **67%** / 实体 **29%** | `tools/probe_frames.py` |
| 逐像素透明 | 蒙版内变化 **99.4%**、蒙版外 **0.0%** | `tools/probe_livepet.py`（3 次取最干净） |

**结论：瓶颈在"抓帧 + 解码"（≈120ms），不在 Live2D；9~13fps 是这条路线的现实值**，
机器一忙会掉到 3fps（会明显卡）。要更顺只能换抓帧方式（见第 5 节）。

## 4. 坑（按杀伤力排序，都是实测踩出来的）

### 坑 1（最坑）：DPI 感知是"起窗口时"才打开的，坐标空间中途会换

- 进程刚起来时 **DPI 不感知**：`GetSystemMetrics` = **1707×1067**（缩放后的逻辑值）；
- `petwin` 起窗口时才把进程设成感知（`PER_MONITOR_AWARE_V2`）→ 同一个进程里变成 **2560×1600**；
- 于是"起窗口前算的坐标/截的底图"和"之后的"属于**两个坐标系** —— 探针因此假故障满天飞。

规矩：**算任何屏幕坐标之前，先 `petwin.make_process_dpi_aware()`**（幂等）。
`desktop/shell.py` 的 `_screen_size()` 已经这么做了（它以前调的是老的 `SetProcessDPIAware()`）。

顺带两个 API 细节（写错会**静默**退回 V1，混合 DPI 多显示器上角色会被系统再拉一遍，糊）：

- `SetProcessDpiAwarenessContext` 在 **user32** 里，`shcore` 里只有 `SetProcessDpiAwareness`；
  写 `shcore.SetProcessDpiAwarenessContext(...)` 会 AttributeError 被 except 吞掉 → 一直走 V1。
- 参数是**伪句柄**（-4 = PER_MONITOR_AWARE_V2），要声明成 `c_void_p`。

### 坑 2：屏幕像素判定在"活的桌面"上会骗人（验收仪器的问题）

原来的验收写法：「显示桌宠前后各截一张，变化像素应该只有角色那一小块」。
同一份代码、同一台机器，实测变化占比 **53% → 94% 反复横跳**，误报过好几次 ❌。两个原因：

1. **坐标空间中途换了**（坑 1）；
2. **学生桌面自己在动**（他在旁边开游戏/切窗口）—— 背景变化只会**增加**变化量。

正确的做法（已落成 `agent_studio/tools/_screen_check.py`）：

- 把窗口用 `SetWindowPos` **搬到屏幕外再搬回**当开关（只隔 0.12 秒；不要"关掉再重建窗口"，那要几百毫秒）；
- 按**窗口当前那一帧的 alpha 蒙版**分开判：蒙版内该大面积变、蒙版外只该剩背景噪声；
  不透明方块的特征是"蒙版外也整块变（≈100%）"；
- 量 **3 次取蒙版外变化最小的那次**（背景只可能加噪声，不会减）；
- 判不出来（蒙版外也大、且背景噪声高）就说「**这次不算数**」，**不要报 ❌**。

对照实验（证明桌宠本体是好的）：全透明帧 → 屏幕几乎不变；
只画一个 120×120 红方块的合成帧 → 变化 **9.5%**，理论值 9.2%。

### 坑 3：气泡会把窗口撑高，判定时要先清掉

气泡挂在角色**上方**，`livepet.frame()` 会让画布变高 → 窗口尺寸变 → 蒙版和区域对不上，
"蒙版外"就变成"气泡那一块"，好的一次会被误判。探针里判定前会把 `bubble_text/bubble_until` 清掉。

### 坑 4：`Task was destroyed but it is pending!` 不是崩溃

`frames.FrameSource.stop()` 以前只关 websocket、没 cancel 抓帧任务，循环带着 pending task 结束 →
Python 打这行字。现在 `stop()` 先 `task.cancel()` 再关循环。

## 5. 还没做 / 没实测

- **真鼠标点击穿透**没实测（只验了纯函数：透明处 `HTTRANSPARENT`、角色上 `HTCLIENT`）；拖动、右键菜单同样没实测。
- **帧率**：页面 rAF 跑 70fps、我们只抓 9~13fps，等于白烧 CPU（学生还要拿这台机器学习）。
  想省 CPU 可以在 frames 模式下限帧；想更顺可以研究 CDP `Page.startScreencast`
  （但 screencast 给的是 JPEG/无 alpha，会丢掉 B 方案的根本，除非能拿到 PNG + alpha）。
- `desktop/pet.py`（老的 tkinter 桌宠）已不在默认路径里，留着只会让人改错地方，待用户表态删不删。
