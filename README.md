# factory-desktop-zh-cn — Factory Desktop (macOS) 原生级全量中文汉化包

[![Platform](https://img.shields.io/badge/Platform-macOS-blue)](#)
[![Factory](https://img.shields.io/badge/Factory_Desktop-v0.199.1%2B-success)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

为 macOS 版 **Factory Desktop** 打造的**原生级、零外联、支持软件内直接更新且自动自愈**的简体中文（`zh-CN`）汉化补丁系统。采用四层混合汉化架构与五维安全标准，一步到位汉化全部界面与左侧会话标题。

---

## ✨ 核心特性

1. **四层原生混合汉化架构**
   - **Layer 1 — React `i18next` 原生资源树注入**：直接向渲染进程 `i18next` 注入 `14,920+` 条精准词条、69 条语义消歧路径覆盖（`pathOverrides`）与 58 条动态前缀规则，保留全部 `{{变量}}` 插值与 `<Trans>` 组件结构。
   - **Layer 2 — Electron 主进程与系统菜单汉化**：完整汉化 macOS 顶部应用菜单栏（`文件`、`编辑`、`视图`、`窗口`、`帮助`）、系统托盘菜单与右键上下文菜单。
   - **Layer 3 — 零闪烁 DOM 兜底引擎**：基于 `WeakMap` + `requestAnimationFrame` 微任务批处理的轻量级 `MutationObserver`，自动跳过 `<pre>`、`<code>`、`.monaco-editor`、`.xterm` 等代码与终端区域，并带有全链路 `try...catch` 异常隔离保护罩，杜绝误伤代码标识符与 Git 分支名。
   - **Layer 4 — 左侧会话标题原生中文生成**：通过等长字节补丁（In-place Binary Patch + 优雅降级兜底）将 `bin/droid` 内置的会话标题生成提示词切换为简体中文，**完全不影响主对话模型的推理与回答质量**。

2. **五维安全与跨版本无感更新标准**
   - **原生接管软件内「更新」按钮（告别 `ShipIt` 签名报错）**：内置 macOS 原生更新桥接器，点击软件右上角「有可用更新」时自动校验官方 Apple `Developer ID Application` 证书签名，并在后台自动完成新版本的中文预注入与重签名，**重启后直接进入全中文最新版**。
   - **ASAR 完整性哈希同步**：重打包 `app.asar` 后自动计算新的 SHA-256 Header Hash 并同步写入 `Contents/Info.plist` 的 `ElectronAsarIntegrity`。
   - **APFS 写时复制（Copy-on-Write）原子替换**：在 `/private/tmp` 独立安全目录中完成克隆、打补丁与由内向外动态重签名（兼容 Apple Silicon 与 Intel 架构，保留完整 `entitlements.plist` 麦克风/摄像头/JIT 权限），校验通过后原子替换 `Factory.app`。
   - **独立持久化目录（`~/.factory-zh`）**：运行安装后自动将运行时与原版备份同步至隐藏目录 `~/.factory-zh`，即使安装后删掉下载文件夹，后台自动更新自愈与原版恢复依然永久有效。
   - **智能防自杀与 `sudo` 权限兼容**：自动识别是否在内置终端中运行（防止重启时中断自身脚本），支持 `/Applications/Factory.app` 与 `~/Applications/Factory.app`，完美兼容 `sudo` 执行并自动归还当前用户文件所有权。

---

## 🚀 快速开始（一步到位）

### 环境要求
- macOS 系统，已安装 `Factory.app`（支持 `/Applications/Factory.app` 或 `~/Applications/Factory.app`）
- 系统自带 `python3`（若未安装 Node.js，脚本会自动回退使用 Factory 内置运行时，无需额外配置）

### 1. 克隆或下载仓库并执行一键汉化

打开「终端（Terminal）」，依次运行以下命令：

```bash
git clone https://github.com/Eric2077x/factory-desktop-zh-cn.git
cd factory-desktop-zh-cn
python3 patcher.py
```

> 💡 **权限提示**：如果终端提示 `权限不足`，直接运行 `sudo python3 patcher.py` 即可（脚本会自动识别真实用户并归还所有文件权限）。安装完成后，您可以随时删除下载的源码文件夹，不影响自动更新防掉与卸载还原。

---

## 🛠️ 常用命令与卸载还原

| 命令 | 说明 |
| :--- | :--- |
| `python3 patcher.py` | 一键应用汉化补丁、同步持久化目录 `~/.factory-zh` 并安装自动自愈守护服务（推荐） |
| `python3 patcher.py --check` | 检查当前 `Factory.app` 是否已处于汉化状态 |
| `python3 patcher.py --force` | 从干净备份强制重新构建并写入最新词库（修改 `locales/zh-CN.json` 后使用） |
| `python3 patcher.py --no-guard` | 仅应用汉化补丁，不安装后台自动自愈 LaunchAgent |
| `python3 patcher.py --skip-kill` | 汉化完成后不自动重启 Factory.app |
| `python3 restore.py` | **一键无损还原英文原版**：卸载守护服务、恢复原版 `app.asar` / `bin/droid` / `Info.plist`、清理 `~/.factory-zh` 并重签名 |
| `python3 ~/.factory-zh/restore.py` | **删掉下载文件夹后的一键还原命令**（若提示权限不足可加 `sudo`） |

---

## 📂 目录结构

```text
factory-desktop-zh-cn/
├── engine/
│   ├── asar-patcher.js       # 核心 ASAR 4字节对齐重打包 + Header SHA256 同步 + 二进制补丁与签名引擎
│   └── dom-translator.js     # 渲染进程 DOM 兜底引擎（WeakMap 原文快照 + 代码区白名单 + 异常隔离保护罩）
├── locales/
│   ├── zh-CN.json            # 渲染进程 14,920+ 核心词库、pathOverrides 与动态正则规则
│   └── zh-desktop-i18n.json  # Electron 主进程菜单栏与托盘词库
├── patcher.py                # 一键汉化与 ~/.factory-zh 持久化守护安装入口
├── restore.py                # 一键无损还原原版与清理卸载入口
└── localize_sessions.py      # 本地历史会话标题汉化与无损还原脚本
```

---

## 📄 开源协议

本项目基于 [MIT License](./LICENSE) 开源。
