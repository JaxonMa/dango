# Dango Jump

在真实 Windows 桌面/窗口控件之间跳跃的小游戏。玩家操控一只水色小团子，从起点控件出发，借助长按空格蓄力、左右方向选择，跳到随机高亮的目标控件上。

- 覆盖层：Tkinter 无边框、置顶、色键透明全屏窗口
- 控件读取：Windows UI Automation（`uiautomation`）
- 角色/UI：Canvas + Pillow 抗锯齿小团子
- 平台：Windows 10/11，单主屏

## 安装与运行

要求：Windows 10/11、Python 3.10+、Tkinter（Windows 官方 Python 自带）。

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python main.py --delay 3
```

启动后会倒计时 3 秒。**请在倒计时内切换到想玩的窗口**，倒计时结束后程序会读取该前台窗口的控件并显示透明覆盖层。

常用参数：

```bat
python main.py --desktop          # 直接扫描整个桌面
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
| `Esc` | 退出并把键盘焦点还给原窗口 |

> 胜利/机会用尽后，键盘焦点会主动还给启动前的窗口；此时请点击卡片上的“再玩一局”/“退出”，或先点击卡片重新聚焦，再使用 `R` / `Esc`。

规则：

- 小团子只会在**下落时穿过控件上边缘**时落地，路径上第一个控件优先，不会穿透。
- 落到非目标控件也可以继续跳；落到目标控件即获胜。
- 没有落点、飞出屏幕左右边缘或掉出屏幕底部会坠落，默认无限重试，可配置为有限次数。
- 目标控件在游戏中消失或移动时，会自动重新枚举并生成关卡。

## 视觉提示

- 琥珀色闪烁虚线：目标控件
- 水色旗标：本关初始起点
- 水色实线：当前所在控件
- 淡灰色虚线：所有可落脚候选控件
- 角色两侧箭头：当前方向
- 底部面板：蓄力条、百分比、预计距离
- 失去焦点时显示“点击游戏区域重新聚焦”并暂停物理

## 可配置项

所有手感和过滤参数集中在 `main.py` 顶部：

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

例如窗口中的按钮很大、水平距离很远时，可适当增大 `JUMP_DISTANCE_MAX`；如果觉得蓄力太快，可调大 `CHARGE_SECONDS`。

## 实现结构

- `Rect` / `Platform` / `Level`：坐标和关卡数据
- `filter_platforms()`：按类型、可见性、可用性、最小尺寸、本进程、重叠关系过滤控件
- `scan_controls()`：UI Automation BFS 枚举，带 2.5 秒时间预算和异常隔离
- `generate_level()`：随机目标、阈值逐级放宽选择起点、基本可达性检查
- `find_landing()`：扫掠式上边缘命中，防止高速穿透
- `GameModel`：READY / CHARGING / AIRBORNE / VICTORY / GAME_OVER 状态机与物理
- `OverlayApp`：透明窗口、键盘焦点、渲染、目标存活监控和胜利/错误卡片

`uiautomation` 和 Pillow 都在实际启动游戏时才导入；纯逻辑测试不需要安装这些依赖。

## 测试

```bat
python -m py_compile main.py tests/test_core.py
python -m unittest discover -s tests -v
```

测试覆盖蓄力映射、抛物线、首个落点、防穿透、胜负、坠落重试、控件过滤、阈值放宽和移动检测。

## Windows 手工验收清单

1. 打开一个控件较多的窗口（例如 Windows 设置、文件资源管理器或经典控制面板），运行 `python main.py --delay 3` 并切换过去。
2. 确认覆盖层无边框、置顶，桌面/窗口从透明区域透出，控件描边与真实控件边缘对齐（含 125%/150% 缩放）。
3. 左右键切换方向，角色旁箭头明确变化。
4. 长按空格，蓄力条持续增长且有上下限；松开后角色沿抛物线飞行。
5. 分段测试：路径上有多个控件时只落在第一个；落到目标时出现胜利卡片。
6. 点击“再玩一局”或胜利卡片聚焦后按 `R`，确认重新生成关卡。
7. 点击其他窗口使覆盖层失焦，确认出现重新聚焦提示且游戏暂停；点击后继续。
8. 在游戏中移动或关闭目标窗口，确认自动重建关卡或给出可重试错误。
9. 最小化目标窗口后启动，确认显示“无法读取当前窗口控件/控件不足”，`R`/`D`/`Esc` 均可用。
10. 配置 `LIMIT_RETRIES=True` 后连续坠落，确认机会用尽卡片。

## 已知限制

- 仅支持 Windows 单主屏；多屏坐标不做虚拟桌面拼接。
- UI Automation 的 `IsOffscreen` + 屏幕矩形只是“可见”代理，无法完全识别被其他窗口遮挡的控件。
- 浏览器、Electron、UWP、游戏或受保护窗口可能只暴露少量 UIA 控件；此时请换窗口或按 `D` 扫描桌面。
- 读取管理员权限窗口时，本程序需要以相同权限运行。
- 某些应用的 UIA 枚举调用可能在单个 COM 调用内卡住；程序在节点之间检查时间预算，但无法抢占正在阻塞的系统调用。
- 色键透明依赖 Windows Tk 的 `-transparentcolor`；不支持时会显示错误而不是静默降级。
- 不使用全局键盘钩子，也不会向被覆盖窗口发送按键；只有覆盖层获得键盘焦点时才接收游戏键。
