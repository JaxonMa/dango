# Dango Circuit

在真实 Windows/macOS 桌面控件之间进行多站跳跃的跑酷小游戏。操控一只水色小团子，按顺序落到桌面上的随机检查点，收集可选星星，用更少的坠落、更快的速度和更精准的落点刷新本地最高分。

- Windows 10/11：Tk 透明覆盖层 + UI Automation
- macOS 14+：PySide6 逐像素透明覆盖层 + Accessibility（AXUIElement）
- 一局默认 4 个检查点、3 颗心，单机、离线、本地最高分
- 平台：单主屏；Python 3.10+

## 安装与运行

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

4. 在 3 秒倒计时内切换到想玩的 App，倒计时结束后会捕获该前台 App。

首次启动如果未授权，游戏会显示权限卡片；“打开系统设置”会暂时隐藏覆盖层。授权后点击 Dock 中的 Python 图标返回，覆盖层会自动恢复并重试，也可以手动点击“重新检查”。辅助功能权限只用于读取控件位置和名称，**不需要屏幕录制或输入监控权限**。

### Windows

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py --delay 3
```

在倒计时内切换到目标窗口。

### 常用参数

```bash
python main.py --delay 3
python main.py --hearts 3 --checkpoints 4 --stars 2
python main.py --relaxed          # 练习模式：无限心
python main.py --seed 123         # 固定路线随机种子
python main.py --mute             # 静音
python main.py --desktop          # 扫描整个桌面/全部 App
python main.py --help
```

旧参数 `--limit-retries` / `--max-retries` 仍被接受，但已映射为心数；新版本请使用 `--hearts`。

## 一局游戏怎么玩

1. 程序扫描当前窗口/桌面控件，生成一条**保证逐段基本可达**的路线：起点 + 默认 4 个检查点。
2. 当前检查点用琥珀色闪烁虚线标出；未来检查点有编号，终点标 `GOAL`。
3. 左右键选择方向，长按空格蓄力，松开起跳；落到当前检查点即推进到下一站。
4. 每站可以收集路线外的可选星星（★，+150 分），不要求收集。
5. 落到最后一个检查点完成一局。结算得分、用时、Perfect、Direct、星星、评级和本地最佳。
6. 坠落损失 1 颗心，连击清零，并回到**最近一次到达的检查点**；3 颗心耗尽则本局结束。

路线外控件也可以落脚，它们只是普通平台；不会推进检查点，并会让你失去本段的 Direct 奖励。

## 操作

| 按键 | 作用 |
| --- | --- |
| `←` / `→` | 切换跳跃方向 |
| 长按 `Space` | 蓄力，底部显示 0–100%、预计距离和用时 |
| 松开 `Space` | 起跳 |
| `R` | 重新读取控件并开新一局 |
| `D` | 仅在错误界面可用：改为扫描整个桌面 |
| `S` | 仅在权限界面可用：打开辅助功能设置（macOS） |
| `M` | 静音/开启音效，并写入本地设置 |
| `Esc` | 退出并把键盘焦点还给原窗口 |

> 结算后键盘焦点会还给启动前的窗口；请点击结算卡片上的按钮，或先点击卡片重新聚焦，再使用 `R` / `Esc`。

## 计分

每次落到当前检查点：

- 基础分：`120 + 80 × 站序号`。
- **Perfect**：落点在目标宽度中央 30% 内，`+60`。
- **Direct**：上次检查点后没有落到中间平台，`+80`。
- **Combo**：连续命中检查点且未坠落，倍率 `1 + 0.2 × (combo - 1)`，最高 1.8×。
- 这些分数乘以当前 Combo 倍率。
- 可选星星：每颗 `+150`。
- 完成奖励：`剩余心数 × 300 + max(0, 2000 - 30 × 用时秒数)`。

结算：

- 3 星：完成且未坠落。
- 2 星：完成且最多坠落 1 次。
- 1 星：完成路线。
- 失败：0 星，但仍记录本局分数和游玩次数。

本地最佳保存在：

- macOS：`~/Library/Application Support/DangoJump/progress.json`
- Windows：`%APPDATA%\DangoJump\progress.json`
- 其他/回退：`~/.dango_jump/progress.json`
- 可通过环境变量 `DANGO_PROGRESS_PATH` 覆盖路径。

## 视觉与反馈

- 当前检查点：琥珀色闪烁虚线 + “下一站 x/N”标签
- 已完成检查点：绿色描边和 ✓
- 未来检查点：灰蓝编号；终点 `GOAL`
- 可选星星：金色虚线描边和 ★
- 当前平台：水色实线
- 起点/复活点：水色旗标
- 落地粒子、得分/Combo/Perfect 弹出、飞行尾迹、目标高亮
- 程序化音效：launch、land、checkpoint、perfect、star、fall、victory、game over（无额外音频依赖）

## 可配置项

核心参数集中在 `dango_core.py`：

```python
START_HEARTS = 3
COURSE_CHECKPOINTS = 4
BONUS_STAR_COUNT = 2
PERFECT_CENTER_RATIO = 0.30
CHECKPOINT_BASE_SCORE = 120
CHECKPOINT_SCORE_STEP = 80
PERFECT_BONUS = 60
DIRECT_BONUS = 80
STAR_BONUS = 150
MAX_COMBO_MULTIPLIER = 1.80
GRAVITY = 2200.0
CHARGE_SECONDS = 1.15
JUMP_DISTANCE_MIN = 80.0
JUMP_DISTANCE_MAX = 1200.0
```

macOS 还使用 `MAC_TOP_SAFE_MARGIN = 44`，过滤紧贴菜单栏的顶部控件。

## 文件结构

```text
main.py             跨平台 CLI / 平台分发
dango_core.py       共享核心：Course、路线生成、检查点、计分、物理、状态机
dango_progress.py   本地最高分/静音设置持久化
dango_audio.py      程序化音效与播放
windows_app.py      Windows Tk + UIAutomation 后端
macos_app.py        macOS PySide6 透明覆盖层和游戏外壳
macos_ax.py         macOS 辅助功能权限、App/窗口捕获、AX 枚举
tests/              核心、进度、音频、AX Fake 节点测试
```

macOS 端使用 PySide6 而非 Tk，因为 Tk 9 在 macOS 上的 `-transparent` 仍绘制黑色背景；`-alpha` 只能整体半透明。PyQt5 是 GPLv3，而本仓库是 MIT，因此选用 LGPL 的 PySide6-Essentials。详见 `THIRD_PARTY_NOTICES.md`。

## 测试

```bash
python -m py_compile main.py dango_core.py dango_progress.py dango_audio.py \
  windows_app.py macos_app.py macos_ax.py
python -m unittest discover -s tests -v
```

在 macOS 上安装依赖后会运行完整 AX 测试；没有 PyObjC 的环境会跳过 AX 用例。

测试覆盖：

- 蓄力映射、抛物线、命中与防穿透
- 多站路线生成、逐段可达、缩短路线、可选星星
- 检查点推进、Combo、Perfect、Direct、星星
- 心数、检查点复活、Game Over、练习模式
- 本地最高分、损坏进度文件回退、音效 WAV 生成
- macOS AX 角色/坐标/权限/移动检测 Fake 节点测试

## Windows 手工验收清单

1. 打开控件较多的窗口，运行 `python main.py --delay 3` 并切换过去。
2. 确认覆盖层透明、置顶，控件描边与真实控件边缘对齐。
3. 确认 HUD 显示下一站、分数、Combo、心数、星星、最佳分数和用时。
4. 左右键、蓄力、起跳、首个控件落点。
5. 连续落到 4 个检查点完成一局；途中坠落回最近检查点并扣心。
6. Perfect/Direct/Combo/星星加分和弹出反馈。
7. 结算卡片、评级、本地最佳、再玩一局。
8. 失焦提示与重新聚焦；`M` 静音；`Esc` 归还焦点。
9. 目标移动/消失时尝试保留进度或给出可重试错误。
10. 配置有限心数后耗尽，确认 Game Over。

## macOS 手工验收清单

1. 首次运行的辅助功能权限卡片；打开系统设置时覆盖层隐藏，授权后从 Dock 返回自动恢复。
2. 透明覆盖层、Retina 坐标对齐、点击透明区域能操作底层窗口。
3. 选择控件较多的 App，确认课程生成了多个编号检查点和可选星星。
4. 完成一局；确认检查点推进、心数、Combo、Perfect、Direct、星星和计时。
5. 坠落回最近检查点；3 次坠落后出现结果卡片。
6. 目标移动时路线更新不丢分数；窗口布局剧变时有可重试错误。
7. 结算后焦点回到原 App，点击“再玩一局”可重新开始。
8. 检查本地最佳文件和静音状态持久化。
9. 空闲 CPU 不应持续满载；游戏动画约 30fps。
10. `--desktop` 可跨 App 扫描。

## 已知限制

- 仅支持单主屏；多屏不做虚拟桌面拼接。
- Windows UIA 的可见性/遮挡判断是近似；macOS AX frame 也只判断屏幕相交和隐藏状态。
- 浏览器、Electron、Java、UWP、游戏或受保护窗口可能只暴露少量控件；此时请换窗口或按 `D` 扫描桌面。
- macOS 必须授予辅助功能权限，授予对象是实际启动游戏的 App。
- macOS 菜单栏和 Dock 位于覆盖层之上；顶部/底部 HUD 已避开可用区域，紧贴菜单栏的控件会被过滤。
- 鼠标在透明区域会穿透到下层窗口；不透明 HUD/卡片区域会接收点击。
- 极少数情况下应用 AX/UIA 调用会阻塞；程序在节点之间检查预算，但无法抢占阻塞中的系统调用。
- 不使用全局键盘钩子，也不发送合成按键。
- macOS 端依赖 PySide6-Essentials（LGPL）和 PyObjC；无需用户自行编译原生扩展。
