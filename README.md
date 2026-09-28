# BALDR FORCE EXE 修改器

面向 Windows 的简体中文存档编辑器与实时战斗修改器。项目不替换游戏程序、不注入代码、不修改 PAC/WAZ 资源；离线与实时写入都必须通过对应的安装身份、字段证据和运行时安全门禁。

> **当前发布定位：受限预览版（v0.4.0）。** 普通武器编辑、Force Crash 离线解锁、玩家战斗 HUD、当前武器只读监控、回血、锁血、降热、热量阈值、同连段武器重复，以及一次 Force Crash 补充均已完成对应安全闭环。

[查看 Windows 发行版](https://github.com/HyGeoIceFairy/BaldrForceEXE-Toolkit/releases) · [报告问题](https://github.com/HyGeoIceFairy/BaldrForceEXE-Toolkit/issues) · [安全策略](SECURITY.md)

## 兼容性

- Windows 10 或 Windows 11，64 位；
- 用户自行合法安装的 BALDR FORCE EXE；本项目不提供游戏程序、资源或存档；
- 存档写入只对工具识别为“已验证样本 2026-09”的中文整合版开放；
- 未识别安装仍可导出诊断，但普通武器、Force Crash 和恢复写入都保持禁用；
- Windows 发行包不要求用户另行安装 Python。

## 能力状态

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| 离线读取、诊断与手动备份 | 只读 | 不授权任何存档写入 |
| 普通武器离线编辑 | 已验证写入 | 仅限已识别安装、普通槽位和已证明字段 |
| Force Crash 离线解锁 | 已验证写入 | 记录映射、候选白名单，以及 21 项隔离游戏内显示、装备、发动、持久化和恢复均已验收 |
| 实时进程连接 | 只读 | 核对路径、唯一实例、x86 架构和指令指纹 |
| 玩家战斗 HUD | 只读 | 自动识别普通战斗与练习模式；菜单、过场、结算和模式切换期间保持连接并等待战斗恢复 |
| 当前武器配置与连段历史 | 只读 | 显示 13 个状态槽的实际武器 ID、最近成功发动槽和当前连段实际武器 ID 历史；没有证据的名称不作猜测 |
| 回血、锁血、降热、热量阈值与连段武器恢复 | 已验证写入 | 目标热量为 0 时保留游戏自然归零事件，使本连段已用武器正常恢复；每次写入前后重验对象、场景、指纹和值域 |
| Force Crash 战斗补充 | 已验证写入 | 玩家属性 `+0x3A` 的 0–10000 仪表已完成静态语义、只读 HUD 与隔离练习进程一次补充验收 |
| 同连段武器可重复使用 | 已验证写入 | 独立清除 12 个普通状态槽的本连段使用门禁，不修改第 13 个特殊槽、热量、伤害或命中；25 项历史在安全边界前回退 |

桌面界面和诊断报告会逐项显示 `unavailable`、`read_only`、`experimental_write` 或 `verified_write`，并给出禁用原因。已有通用内存读写实现和 Fake Adapter 测试不代表真实游戏地址已经得到验证。

## 已实现功能

### 普通武器

- 浏览 63 把官方武器的编号、用户游戏资源提供的名称及原文说明、开发状态、等级、成长值和热度；
- 查看已汉化的开发前置武器、游戏实际 EXP 门槛及当前差额；
- 搜索、状态筛选、收藏、跨槽位比较和 Ctrl/Shift 多选；
- 批量标记开发完成、设置等级、练满熟练度、降低至正常养成热度下限；
- 内存草稿、变更预览、撤销、重做、放弃全部和语义 JSON 预设。
- `Ctrl+1` 至 `Ctrl+7` 快速切换页面，`Ctrl+F` 聚焦当前编辑页搜索框；撤销和重做只作用于当前编辑页。

游戏有效热度按以下公式计算：

```text
max(基础热度 - 降热量, trunc(基础热度 / 3))
```

“最低热度”是正常养成允许的下限，不是零热。

### Force Crash

- 浏览 21 项 Force Crash 的编号、用户游戏资源提供的名称与原文说明、成长记录和解锁状态；
- 单项或批量解锁草稿、预览、撤销、重做和语义预设；
- 只允许记录 192–212 的开发状态字节变为已证明的完成状态 `5`；
- 提供独立候选检查器和不会覆盖既有证据的人工验收 JSON 模板。

### 备份、恢复与诊断

- 每次应用前创建整个 `Save` 目录的一致性快照，并逐文件复核 SHA-256；
- 游戏进程门禁、安装版本门禁、源文件冲突检查、同目录暂存和替换后重读；
- 崩溃事务核对、多实例保护、备份完整性检查、恢复预览和恢复前快照；
- 拒绝路径越界、符号链接和 Windows 重解析点；
- Config、PAC、WAZ、工作区、实时 Profile 和单文件哈希采用分区诊断，单项失败不会遮蔽其他报告。

## 安装与校验

如 GitHub Releases 已发布所需版本，从[发行页](https://github.com/HyGeoIceFairy/BaldrForceEXE-Toolkit/releases)下载与该版本对应的 Windows ZIP，解压后运行：

```text
BaldrForceModifier\BaldrForceModifier.exe
```

当前可执行文件没有 Authenticode 数字签名，Windows 可能显示 SmartScreen 警告。只从本仓库 Releases 下载，并使用 PowerShell 计算压缩包哈希：

```powershell
Get-FileHash '.\BaldrForceModifier-v0.4.0-windows-x64.zip' -Algorithm SHA256
```

结果必须与同一发行页的 `SHA256SUMS.txt` 完全一致；不一致时不要运行。不要从要求关闭杀毒软件的第三方链接下载，也不要移动发行目录中的 `_internal` 文件夹。

仓库本地构建的哈希只对应当次源码与构建环境，不是 GitHub Release 的永久校验值；正式校验只以对应发行页公布的文件为准。

## 使用方法

第一次启动会尝试查找 `C:\Game\Baldr Force Exe`，也可以在“设置”页选择其他目录。默认备份位置为：

```text
%LOCALAPPDATA%\BaldrForceModifier\backups
```

### 编辑普通武器

1. 完全退出游戏及启动器；
2. 启动工具，确认概览页显示预期游戏目录和已识别版本；
3. 在“武器工作台”选择普通 `SaveNNN.dat` 槽位；
4. 搜索或筛选武器，使用 Ctrl/Shift 选择一项或多项；
5. 把语义修改加入草稿；
6. 点击“检查并应用”，核对原值、目标值和影响范围；
7. 工具创建完整 Save 快照后再安全替换所选槽位；
8. 启动游戏检查装备、战斗、重新保存和重启读取结果。

首次使用应先操作不重要的普通槽位。预设导入只生成草稿，不会自动写入。

### 编辑 Force Crash

Force Crash 页使用相同的草稿、预览、完整备份和安全提交流程。21 项提供可搜索的编号；名称和说明在运行时从用户自行合法安装的游戏资源读取，不随发行包分发游戏文本，也不改动游戏资源。21 项已经在隔离测试安装中完成显示、装备、发动、重存、重启持久化和原始槽位恢复验收；正式写入仍只对已识别安装与普通槽位开放。

### 备份与恢复

- 工具不会自动清理旧备份；
- 在“备份与恢复”页先检查完整性和版本兼容性，再预览并恢复单个普通槽位；
- 恢复前会再次备份当前整个 Save；
- 游戏运行、备份被篡改、版本不匹配或当前槽位在预览后变化时，恢复会被拒绝；
- `Save998.dat`、`Save999.dat` 等特殊槽位只读。

### 实时战斗页

点击“连接游戏”后，页面会自动识别当前游戏进程；进入普通战斗或练习模式后显示玩家 HP、普通热量、Force Crash 仪表、13 个状态槽的实际武器 ID、最近成功发动槽和当前连段历史，并开放一次回满 HP、一次降热、一次 Force Crash 充满、最低 HP 维持、热量阈值 / 武器恢复，以及“同连段武器可重复使用”。Force Crash 发动期间保留 HUD，但所有修改暂时暂停，发动结束后自动恢复；Force Crash 只提供用户点击触发的一次补充，不提供持续维持，也不发送键盘输入或切换任何窗口焦点。

练习模式可使用“一键练习”按钮，点击后为当前玩家对象启用满血维持、零目标自然冷却和普通武器重复。只在练习战斗中开放；逐项启用过程中如有一项失效会撤销全部持续效果。紧急全停与切换场景仍会立即停用。

将持续热量目标设为 `0` 时，修改器不会把热量直接钉死在零，而是先降到最小正值，再让游戏自身完成自然归零。这样会正常触发连段结束和已用武器恢复；启用时还会执行一次恢复脉冲，用于修复旧版本锁热已经造成的“热量为零但武器仍不可用”。之后每次攻击产生热量都会再次走同一条原生恢复路径。非零目标仍作为普通最高热量阈值使用。

“同连段武器可重复使用”是与锁热独立的持续效果：它只在连段活动期间恢复 12 个普通状态槽，不触碰第 13 个特殊槽，并在历史数量到 24 时回退到 23，为下一次原生追加保留空间。隔离练习进程已完成同一实际武器 ID `0` 的 26 次连续真实命中验收：总伤害 1680，Force Crash 从 0 自然增长到 3360，原生伤害衰减保留，停止写入后热量、槽门禁和历史自然恢复。该效果不会伪造命中，也不会取消游戏的伤害衰减。

连续攻击时游戏可能恰好在修改器采样 HP、热量或武器状态的中途更新数值。此时会丢弃当前帧、不写入，并在下一帧自动继续已启用的效果，不再因为正常的数值更新断开。连段历史已满 25 项时本帧也不会修改武器状态，等待游戏自然结束连段。若玩家对象、场景门禁、进程实例或指令指纹真正失效，仍会立即停写并按需断开；写入后无法复核时同样停写。

写入热量后若游戏立即发动武器或自然冷却，复核允许普通热量在已验证范围内再次变化，不会将下一次攻击误判为写入失败。写入前仍检查目标、对象与指纹，写入后仍检查玩家归属、战斗状态和数值范围；真正的内存读写故障会停写。

页面保留最近 200 条运行记录。菜单、过场、结算、死亡、模式切换和临时没有玩家对象时会保持游戏连接、关闭持续效果并等待战斗恢复；游戏退出、版本不匹配或进程无法继续读取时才会断开。可选自动重连不会重新启用血量、热量或武器重复持续效果；紧急全停、切换工作区或关闭窗口都会解除重连。

## 从源码运行

项目要求 Python 3.11 或更高版本：

```powershell
git clone https://github.com/HyGeoIceFairy/BaldrForceEXE-Toolkit.git
Set-Location '.\BaldrForceEXE-Toolkit'
python -m pip install -e '.[dev]'
python -m bfmod
```

运行完整自动检查：

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src').Path
$env:QT_QPA_PLATFORM = 'offscreen'
python -m ruff check '.\src' '.\tests'
python -m mypy '.\src\bfmod'
python -m unittest discover -s '.\tests' -v
```

真实或隔离安装测试默认跳过，避免无意读取私人存档。只有明确选择测试副本后才运行：

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src').Path
$env:BFMOD_RUN_GAME_TESTS = '1'
$env:BFMOD_GAME_ROOT = (Resolve-Path '.\work\runtime-validation').Path
python -m unittest '.\tests\test_game_integration.py' -v
```

这些集成测试只读安装目录。不要把环境变量指向含主要游玩进度且不希望被开发工具读取的位置。

## 开发诊断命令

安装项目后可使用脚本名 `bfmod-diagnostics`，也可使用 `python -m bfmod.diagnostics`。

只读检查安装：

```powershell
python -m bfmod.diagnostics inspect --game-root '.\work\runtime-validation'
```

在项目 `work` 目录内创建完整 Save 快照：

```powershell
python -m bfmod.diagnostics snapshot `
  --game-root '.\work\runtime-validation' `
  --output '.\work\baseline'
```

核验源槽位与隔离 Force Crash 候选，并独占创建全部人工检查均为 `pending` 的记录：

```powershell
python -m bfmod.diagnostics validate-force-crash `
  --source '.\work\force-crash-validation\source.dat' `
  --candidate '.\work\force-crash-validation\candidate.dat' `
  --slot-name 'Save002.dat' `
  --output '.\work\force-crash-validation\validation.json'
```

`snapshot` 与 `validate-force-crash` 的输出必须位于项目目录内。验证命令不会把候选复制到游戏 Save，也不会覆盖已经存在的审计记录。

## 构建 Windows 发行包

```powershell
.\build.ps1
```

构建脚本生成 `dist\BaldrForceModifier`、版本化 Windows ZIP 和 `release\SHA256SUMS.txt`。发行目录包含使用说明、安全策略、项目许可证、第三方声明、Python 许可证和 LGPL/GPL v3 文本，不包含游戏程序、资源、研究映像或用户存档。

公开源码不包含游戏本体、存档、内存研究原始材料或从游戏中抄录的长篇文案；游戏说明只在运行时从用户自己的安装读取。发行包必须从归属清晰的源码重建并在下载后重新计算哈希。额外独立 Windows 10/11 环境验收尚未完成，因此当前版本仍定位为受限预览版，不宣称已覆盖其他系统和游戏版本。

### GitHub 发布前核对

Git 与 GitHub CLI (`gh`) 是独立于 Python/Conda 虚拟环境的系统工具；虚拟环境只影响本地测试和打包所使用的 Python 依赖。首次发布前，在项目根目录核对 `git status`、`git diff --check`、`gh auth status`、`git remote -v` 与当前版本号；只提交经过审查的源码，不要提交受忽略的 `work/`、`.scratch/`、`dist/`、`release/` 或游戏、存档与原始诊断。运行 CI 相同的 Ruff、mypy、unittest，再运行 `build.ps1`，核对 ZIP 包内容和 `release/SHA256SUMS.txt` 实际哈希。不要将当前工作树中尚未提交的改动打包后配上旧提交或旧标签。

确认远端可见性与发行说明符合预期、CI 在目标提交上通过后，再创建对应版本标签和 GitHub Release，并上传**同一次构建**的 Windows ZIP 与 `SHA256SUMS.txt`；发布后从发行页独立下载并复核。私人仓库的 Release 不等于公开可下载的发行版；调整仓库可见性之前应另行审查整个 Git 历史中的私人材料。未完成外部系统验收时，发行说明应明确标记受限预览版，不得宣传为跨游戏版本的通用写入工具。

## 当前验证结果

当前受审计源码已通过：

- 全量自动测试、mypy strict 与 Ruff；
- 使用本地隔离安装副本的 HUD 读取，以及回血、降热、最低血量维持、热量阈值和 Force Crash 10000→9000→10000 一次补充真实写入验证；
- PyInstaller Windows 11 构建、发行包哈希生成和包内容审计。

准确的测试数量、构建时间和 ZIP SHA-256 以对应提交的 CI 日志或同一次构建生成的 `release\SHA256SUMS.txt` 为准，避免在文档中保留会失效的本地快照值。以上结果不等于正式 Release，也不替代游戏内行为与额外系统环境验收。

## 尚未完成的外部验收

- 额外独立 Windows 10/11 环境验收；
- 独立下载已发布 Windows ZIP 和校验文件后的复核记录。

自动化测试不能代替不同游戏版本、系统环境和实际游玩流程。公开安全边界见 [`CONTEXT.md`](CONTEXT.md) 与 [`SECURITY.md`](SECURITY.md)；原始研究材料、开发接续记录和外部验收证据只保存在受 Git 忽略的本地目录。

## 第三方依赖与许可

- PySide6 / Qt for Python：桌面界面；
- PyInstaller：仅用于开发时构建 Windows 发行包；
- mypy 与 Ruff：仅用于开发检查，不进入发行包。

项目源码采用 [MIT License](LICENSE)。发行包内 Python、PySide6、Qt、Shiboken6 与 PyInstaller 适用各自许可证，详见 [`THIRD_PARTY_NOTICES.txt`](THIRD_PARTY_NOTICES.txt) 和发行目录的 `LICENSES` 文件夹。

BALDR FORCE EXE 及相关名称、程序和资源归各自权利人所有。本项目是独立社区工具，与游戏开发商或发行商不存在隶属、授权或背书关系。软件按现状提供，不保证适用于未验证的游戏版本。
