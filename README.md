# Dango Jump

在真实 Windows/macOS 桌面控件之间跳跃的小游戏。玩家操控一只水色小团子，从起点控件出发，通过方向键和蓄力空格跳到随机高亮的目标控件上。

- Windows 10/11：Tk 透明覆盖层 + UI Automation
- macOS 14+：PySide6 逐像素透明覆盖层 + Accessibility（AXUIElement）
- 共享核心：关卡生成、控件过滤、物理、命中检测和状态机
- 平台：单主屏；Python 3.10+

## 安装与运行

先安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### macOS

需要 macOS 14+、Python 3.10+，并给启动本程序的 App 授予**辅助功能**权限：

1. 打开“系统设置 → 隐私与安全性 → 辅助功能”。
2. 勾选 Terminal / iTerm / VS Code / Python 中实际启动游戏的那一个。
3. 回到终端运行：

```bash
python main.py --delay 3
```

4. 在 3 秒倒计时内切换到想玩的 App。倒计时结束后会捕获该前台 App，显示透明覆盖层。

第一次启动时如果未授权，游戏会显示权限说明卡片；“打开系统设置”会暂时隐藏覆盖层。授权后点击 Dock 中的 Python 图标返回，覆盖层会自动恢复并重试。也可以手动点击“重新检查”。

辅助功能权限仅用于读取控件位置和名称；**不需要屏幕录制或输入监控权限**。

### Windows

要求 Windows 10/11、Python 3.10+、Tkinter（Windows 官方 Python 自带）。

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py --delay 3
```

在倒计时内切换到目标窗口。Windows 上 Tk 的色键透明区域会让鼠标点击穿透。

### 常用参数

```bash
python main.py --desktop          # 直接扫描整个桌面/全部 App
python main.py --delay 5          # 给切换窗口更多时间
python main.py --limit-retries --max-retries 5
python main.py --help
```

## 操作

| 按键 | 作用 |
| --- | --- |
| `←` / `→` | 切换跳跃方向 |
| 长按 `Space` | 蓄力，底部显示 0–100% 与预计水平距离 |
| 松开 `Space` | 起跳 |
| `R` | 重新读取控件并开新关卡 |
| `D` | 仅在错误界面可用：改为扫描整个桌面 |
| `S` | 仅在权限界面可用：打开辅助功能设置（macOS） |
| `Esc` | 退出并把键盘焦点还给原窗口 |

> 胜利/机会用尽后，键盘焦点会主动还给启动前的窗口；此时请点击卡片上的“再玩一局”/“退出”，或先点击卡片重新聚焦，再使用 `R` / `Esc`。

## 游戏规则

- 小团子只会在**下落时穿过控件上边缘**时落地，路径上第一个控件优先，不会穿透。
- 落到非目标控件也可以继续跳；落到目标控件即获胜。
- 没有落点、飞出屏幕左右边缘或掉出屏幕底部会坠落，默认无限重试，可配置为有限次数。
- 目标控件在游戏中消失或移动时，会自动重新枚举并生成关卡。
- 目标控件随机；起点按水平距离阈值优先选择较远控件，并做基本可达性校验。

## 视觉提示

- 琥珀色闪烁虚线：目标控件
- 水色旗标：本关初始起点
- 水色实线：当前所在控件
- 淡灰色虚线：所有可落脚候选控件
- 角色两侧箭头：当前方向
- 底部面板：蓄力条、百分比、预计距离
- 失焦时暂停并显示“点击游戏区域重新聚焦”
- 落地粒子、目标高亮、胜利彩屑

## 可配置项

所有手感和过滤参数集中在 `dango_core.py` 顶部：

```python
GRAVITY = 2200.0
CHARGE_SECONDS = 1.15
JUMP_DISTANCE_MIN = 80.0
JUMP_DISTANCE_MAX = 1200.0
JUMP_FLIGHT_SECONDS = 0.92
MIN_CONTROL_SIZE = 20
START_DISTANCE_THRESHOLD = 420.0
LIMIT_RETRIES = False
MAX_RETRIES = 5
SCAN_TIMEOUT_SECONDS = 2.5
```

macOS 还使用 `MAC_TOP_SAFE_MARGIN = 44`，过滤紧贴菜单栏、会导致角色不可见的顶部控件。

窗口中的按钮很大、水平距离很远时，可适当增大 `JUMP_DISTANCE_MAX`；如果觉得蓄力太快，可调大 `CHARGE_SECONDS`。

## 文件结构

```text
main.py            跨平台 CLI / 平台分发
dango_core.py      纯逻辑核心：数据、过滤、关卡、物理、状态机
windows_app.py     Windows Tk + UIAutomation 后端
macos_app.py       macOS PySide6 透明覆盖层和游戏外壳
macos_ax.py        macOS 辅助功能权限、App/窗口捕获、AX 枚举
tests/             共享核心与 AX Fake 节点测试
```

- `filter_platforms()`：按类型、可见性、可用性、最小尺寸、本进程、重叠关系过滤控件，并支持平台自定义角色集合。
- `scan_controls()`：Windows UI Automation BFS 枚举，带时间预算和异常隔离。
- `macos_ax.scan_target()` / `scan_desktop()`：macOS AX BFS，读取角色、标题、位置、尺寸、启用状态；排除安全输入框。
- `generate_level()`：随机目标、阈值逐级放宽选择起点、基本可达性检查。
- `find_landing()`：扫掠式上边缘命中，防止高速穿透。
- `GameModel`：READY / CHARGING / AIRBORNE / VICTORY / GAME_OVER 状态机与物理。
- `OverlayApp` / `MacOverlay`：透明窗口、键盘焦点、渲染、目标存活监控和胜利/错误卡片。

macOS 端使用 PySide6 而非 Tk，因为 Tk 9 在 macOS 上的 `-transparent` 仍绘制黑色背景，无法把真实桌面透出；`-alpha` 只能整体半透明。PyQt5 是 GPLv3，而本仓库为 MIT，因此选用 LGPL 的 PySide6-Essentials。详见 `THIRD_PARTY_NOTICES.md`。

## 测试

```bash
python -m py_compile main.py dango_core.py windows_app.py macos_app.py macos_ax.py
python -m unittest discover -s tests -v
```

在当前 macOS 环境中，还需要安装 PyObjC 后运行完整的 AX 测试：

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试覆盖蓄力映射、抛物线、首个落点、防穿透、胜负、坠落重试、控件过滤、阈值放宽、目标移动检测、macOS AX 角色/坐标/权限相关纯逻辑。

## Windows 手工验收清单

1. 打开控件较多的窗口，运行 `python main.py --delay 3` 并切换过去。
2. 确认覆盖层无边框、置顶，桌面/窗口从透明区域透出，控件描边与真实控件边缘对齐（含 125%/150% 缩放）。
3. 左右键切换方向，角色旁箭头明确变化。
4. 长按空格，蓄力条持续增长且有上下限；松开后角色沿抛物线飞行。
5. 路径上有多个控件时只落在第一个；落到目标时出现胜利卡片。
6. 点击“再玩一局”或胜利卡片聚焦后按 `R`，确认重新生成关卡。
7. 点击其他窗口使覆盖层失焦，确认出现重新聚焦提示且游戏暂停；点击后继续。
8. 移动或关闭目标窗口，确认自动重建关卡或给出可重试错误。
9. 最小化目标窗口后启动，确认显示错误卡片，`R`/`D`/`Esc` 均可用。
10. 配置 `LIMIT_RETRIES=True` 后连续坠落，确认机会用尽卡片。

## macOS 手工验收清单

1. 首次运行时确认出现辅助功能权限卡片；打开系统设置时覆盖层隐藏，授权后从 Dock 返回可自动恢复。
2. 打开控件较多的 App（设置、Safari、Finder 等），运行 `python main.py --delay 3` 并切换过去。
3. 确认桌面/目标 App 从透明区域透出，Retina 下控件描边与真实控件边缘对齐。
4. 点击其他 App 后确认覆盖层仍显示“已失去键盘焦点”，点击提示可恢复。
5. 左右键切换方向；长按空格蓄力；松开后角色飞行；路径上多个控件时只落在第一个。
6. 目标控件消失或移动时，确认出现重新扫描提示并重建关卡。
7. 落到目标后出现胜利卡片，键盘焦点回到原 App；点击“再玩一局”可重新开始。
8. 使用 `--desktop` 确认可以跨 App 选择控件。
9. 在权限未授予的情况下确认不会崩溃，并显示可重试说明。
10. 观察 CPU：空闲时不持续 60fps 全屏重绘；游戏中为约 30fps 动态层刷新。

## 已知限制

- 仅支持单主屏；多屏坐标不做虚拟桌面拼接。
- Windows UI Automation 的 `IsOffscreen` + 屏幕矩形只是“可见”代理；macOS 使用 AXFrame/屏幕相交做近似判断，无法完全识别被其他窗口遮挡的控件。
- 浏览器、Electron、Java、UWP、游戏或受保护窗口可能只暴露少量控件；此时请换窗口或按 `D` 扫描桌面。
- Windows 读取管理员权限窗口时需要相同权限运行。
- macOS 必须授予辅助功能权限；权限授予对象是实际启动游戏的 App（Terminal/iTerm/VS Code/Python）。
- macOS 菜单栏和 Dock 始终在覆盖层之上；顶部/底部 HUD 已避开可用区域，紧贴菜单栏的控件会被过滤。
- 鼠标在透明区域会穿透到下层窗口；不透明 HUD/卡片区域会接收鼠标点击，这是预期行为。
- 某些应用的 AX/UIA 调用可能在单个系统调用内卡住；程序在节点之间检查时间预算，但无法抢占正在阻塞的系统调用。
- 不使用全局键盘钩子，也不会向被覆盖窗口发送合成按键；只有覆盖层获得键盘焦点时才接收游戏键。
- macOS 端需要 PySide6-Essentials（LGPL）和 PyObjC；不包含需用户自行编译的原生扩展。
