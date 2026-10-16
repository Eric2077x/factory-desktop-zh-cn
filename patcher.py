#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Factory Desktop (v0.199.1+) Chinese Localization Patcher & LaunchAgent Installer
Usage:
  python3 patcher.py             # Apply localization, persist runtime to ~/.factory-zh & install LaunchAgent
  python3 patcher.py --force     # Rebuild from clean backup & re-apply localization
  python3 patcher.py --no-guard  # Apply localization without touching LaunchAgent
  python3 patcher.py --skip-kill # Do not auto-restart Factory.app after patching
  python3 patcher.py --check     # Check current patch status
"""
from __future__ import annotations

import argparse
import os
import plistlib
import pwd
import shlex
import shutil
import subprocess
import sys
import time


def get_real_user_info() -> tuple[int, int, str, str]:
    uid = os.getuid()
    gid = os.getgid()
    if uid != 0:
        pw = pwd.getpwuid(uid)
        return uid, gid, pw.pw_name, pw.pw_dir
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_uid and sudo_uid.isdigit() and int(sudo_uid) > 0:
        pw = pwd.getpwuid(int(sudo_uid))
        return pw.pw_uid, pw.pw_gid, pw.pw_name, pw.pw_dir
    if sudo_user and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
            return pw.pw_uid, pw.pw_gid, pw.pw_name, pw.pw_dir
        except KeyError:
            pass
    try:
        st = os.stat("/dev/console")
        if st.st_uid > 0:
            pw = pwd.getpwuid(st.st_uid)
            return pw.pw_uid, pw.pw_gid, pw.pw_name, pw.pw_dir
    except Exception:
        pass
    pw = pwd.getpwuid(0)
    return 0, 0, pw.pw_name, pw.pw_dir


REAL_UID, REAL_GID, REAL_USER, REAL_HOME = get_real_user_info()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSIST_DIR = os.path.join(REAL_HOME, ".factory-zh")
PERSIST_ENGINE_SCRIPT = os.path.join(PERSIST_DIR, "engine", "asar-patcher.js")
PERSIST_GUARD_SCRIPT = os.path.join(PERSIST_DIR, "guard.js")


def resolve_app_path() -> str:
    candidates = [
        os.environ.get("FACTORY_APP_PATH"),
        "/Applications/Factory.app",
        os.path.join(REAL_HOME, "Applications", "Factory.app"),
    ]
    for c in candidates:
        if c and os.path.exists(os.path.join(c, "Contents", "Resources", "app.asar")):
            return c
    return "/Applications/Factory.app"


APP_PATH = resolve_app_path()
DROID_BIN = os.path.join(APP_PATH, "Contents", "Resources", "bin", "droid")
PLIST_LABEL = "com.factory.zh-cn"
PLIST_PATH = os.path.join(REAL_HOME, "Library", "LaunchAgents", f"{PLIST_LABEL}.plist")

GUARD_JS_CONTENT = """#!/usr/bin/env node
/**
 * Factory Desktop (v0.199.1+) Auto-Heal Guard
 * Automatically generated into ~/.factory-zh/guard.js by patcher.py
 */
'use strict';

const fs = require('fs');
const path = require('path');
const {
  isAlreadyPatched,
  runPatch,
  computeAsarHeaderHash,
  BACKUP_DIR,
  ASAR_PATH,
  STATE_FILE
} = require('./engine/asar-patcher.js');

const LOG_FILE = path.join(BACKUP_DIR, 'guard.log');
const MAX_LOG_BYTES = 256 * 1024;

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\\n`;
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true, mode: 0o700 });
    if (fs.existsSync(LOG_FILE)) {
      const st = fs.statSync(LOG_FILE);
      if (st.size > MAX_LOG_BYTES) {
        const tail = fs.readFileSync(LOG_FILE).subarray(st.size - Math.floor(MAX_LOG_BYTES / 2));
        fs.writeFileSync(LOG_FILE, tail, { mode: 0o600 });
      }
    }
    fs.appendFileSync(LOG_FILE, line, { encoding: 'utf8', mode: 0o600 });
  } catch (_) {}
}

function main() {
  let currentHash = '';
  try {
    if (!fs.existsSync(ASAR_PATH)) {
      return;
    }
    if (isAlreadyPatched()) {
      return;
    }
    currentHash = computeAsarHeaderHash(ASAR_PATH);
    if (fs.existsSync(STATE_FILE)) {
      try {
        const st = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        if (st.lastFailedHash && st.lastFailedHash === currentHash) {
          return;
        }
      } catch (_) {}
    }
    log(`Detected unpatched or updated Factory.app (hash=${currentHash.slice(0, 12)}...); applying Chinese localization patch...`);
    const res = runPatch({ force: false });
    log(`Auto-heal result: ${JSON.stringify(res)}`);
  } catch (err) {
    log(`Auto-heal error: ${err && err.stack ? err.stack : String(err)}`);
    if (currentHash) {
      try {
        let st = {};
        if (fs.existsSync(STATE_FILE)) {
          st = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        }
        st.lastFailedHash = currentHash;
        st.lastFailedAt = new Date().toISOString();
        fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true, mode: 0o700 });
        fs.writeFileSync(STATE_FILE, JSON.stringify(st, null, 2), 'utf8');
      } catch (_) {}
    }
    process.exitCode = 1;
  }
}

main();
"""


def chown_to_real_user(target_path: str) -> None:
    if os.getuid() != 0 or REAL_UID == 0 or not os.path.exists(target_path):
        return
    try:
        subprocess.run(
            ["/usr/sbin/chown", "-R", f"{REAL_UID}:{REAL_GID}", target_path],
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass


def sync_runtime_to_persist() -> None:
    """
    Sync runtime scripts and dictionaries to ~/.factory-zh so that even if the user
    deletes their Downloads folder after installing, auto-heal LaunchAgent and restore.py
    continue working permanently.
    """
    os.makedirs(os.path.join(PERSIST_DIR, "backup"), mode=0o700, exist_ok=True)
    if os.path.abspath(BASE_DIR) != os.path.abspath(PERSIST_DIR):
        for subdir in ("engine", "locales"):
            src_dir = os.path.join(BASE_DIR, subdir)
            dst_dir = os.path.join(PERSIST_DIR, subdir)
            if os.path.isdir(src_dir):
                os.makedirs(dst_dir, exist_ok=True)
                for fname in os.listdir(src_dir):
                    sfile = os.path.join(src_dir, fname)
                    dfile = os.path.join(dst_dir, fname)
                    if os.path.isfile(sfile):
                        shutil.copy2(sfile, dfile)
        for fname in ("patcher.py", "restore.py", "localize_sessions.py"):
            sfile = os.path.join(BASE_DIR, fname)
            dfile = os.path.join(PERSIST_DIR, fname)
            if os.path.isfile(sfile):
                shutil.copy2(sfile, dfile)

        # Migrate any legacy backup files from BASE_DIR/backup if present
        legacy_backup = os.path.join(BASE_DIR, "backup")
        if os.path.isdir(legacy_backup):
            for fname in os.listdir(legacy_backup):
                if fname == ".patcher.lock":
                    continue
                sfile = os.path.join(legacy_backup, fname)
                dfile = os.path.join(PERSIST_DIR, "backup", fname)
                if os.path.isfile(sfile) and not os.path.exists(dfile):
                    shutil.copy2(sfile, dfile)
            shutil.rmtree(os.path.join(legacy_backup, ".patcher.lock"), ignore_errors=True)

    with open(PERSIST_GUARD_SCRIPT, "w", encoding="utf-8") as fp:
        fp.write(GUARD_JS_CONTENT)
    os.chmod(PERSIST_GUARD_SCRIPT, 0o755)

    # Record current Python executable path for reference
    try:
        with open(os.path.join(PERSIST_DIR, "backup", "python-bin.txt"), "w", encoding="utf-8") as fp:
            fp.write(sys.executable or "/usr/bin/python3")
    except Exception:
        pass

    chown_to_real_user(PERSIST_DIR)


def resolve_js_runtime() -> tuple[str, dict[str, str]]:
    for candidate in ("/opt/homebrew/bin/node", "/usr/local/bin/node", shutil.which("node")):
        if candidate and os.path.exists(candidate):
            return candidate, {}
    if os.path.exists(DROID_BIN):
        return DROID_BIN, {"BUN_BE_BUN": "1"}
    return "node", {}


def run_engine(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["FACTORY_APP_PATH"] = APP_PATH
    runtime_bin, extra_env = resolve_js_runtime()
    env.update(extra_env)
    script_path = (
        PERSIST_ENGINE_SCRIPT
        if os.path.exists(PERSIST_ENGINE_SCRIPT)
        else os.path.join(BASE_DIR, "engine", "asar-patcher.js")
    )
    cmd = [runtime_bin, script_path] + args
    return subprocess.run(cmd, env=env, text=True, capture_output=True, check=False)


def install_launch_agent() -> None:
    launch_agents_dir = os.path.dirname(PLIST_PATH)
    os.makedirs(launch_agents_dir, exist_ok=True)
    chown_to_real_user(launch_agents_dir)

    runtime_bin, extra_env = resolve_js_runtime()
    env_vars = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "FACTORY_APP_PATH": APP_PATH,
    }
    env_vars.update(extra_env)
    watch_paths = [
        os.path.dirname(APP_PATH),
        os.path.join(APP_PATH, "Contents", "Resources"),
    ]
    plist_data = {
        "Label": PLIST_LABEL,
        "ProgramArguments": [runtime_bin, PERSIST_GUARD_SCRIPT],
        "EnvironmentVariables": env_vars,
        "RunAtLoad": True,
        "StartInterval": 120,
        "WatchPaths": watch_paths,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist_data, f)
    os.chmod(PLIST_PATH, 0o644)
    chown_to_real_user(PLIST_PATH)

    uid_str = str(REAL_UID)
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid_str}/{PLIST_LABEL}"], stderr=subprocess.DEVNULL)
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid_str}", PLIST_PATH], stderr=subprocess.DEVNULL)
    subprocess.run(["/bin/launchctl", "enable", f"gui/{uid_str}/{PLIST_LABEL}"], stderr=subprocess.DEVNULL)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{uid_str}", PLIST_PATH], stderr=subprocess.DEVNULL, check=False)
    print(f"[factory-zh] Installed and loaded LaunchAgent: {PLIST_PATH} -> {PERSIST_GUARD_SCRIPT}")


def is_running_inside_factory() -> bool:
    """
    Walk parent process tree to detect if patcher.py is running inside Factory's
    built-in terminal or Droid agent session so we never kill our own parent process.
    """
    pid = os.getpid()
    visited = set()
    while pid and pid > 1 and pid not in visited:
        visited.add(pid)
        try:
            out = subprocess.check_output(
                ["/bin/ps", "-o", "ppid=,comm=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if not out:
                break
            parts = out.split(None, 1)
            ppid = int(parts[0])
            comm = parts[1] if len(parts) > 1 else ""
            if any(k in comm for k in ("Factory.app", "/Factory", "bin/droid")):
                return True
            pid = ppid
        except Exception:
            break
    return False


def is_factory_running() -> bool:
    res = subprocess.run(["/usr/bin/pgrep", "-x", "Factory"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0


def restart_or_prompt_factory(skip_kill: bool) -> None:
    if not is_factory_running():
        return
    if skip_kill or is_running_inside_factory():
        print(
            "[factory-zh] 检测到当前在内置终端中运行（或指定了 --skip-kill），已跳过自动重启以防中断。\n"
            "[factory-zh] 请按 Cmd + Q 完全退出并重新打开 Factory（或按 Cmd + R 刷新窗口）即可生效！"
        )
        return
    try:
        print("[factory-zh] 正在为您自动重启 Factory.app 以应用全量中文界面...")
        subprocess.run(["/usr/bin/killall", "Factory"], stderr=subprocess.DEVNULL, check=False)
        time.sleep(1.0)
        if os.getuid() == 0 and REAL_USER != "root":
            subprocess.run(
                ["/usr/bin/su", "-", REAL_USER, "-c", f"open -a {shlex.quote(APP_PATH)}"],
                check=False,
            )
        else:
            subprocess.run(["/usr/bin/open", "-a", APP_PATH], check=False)
    except Exception:
        print("[factory-zh] 请手动重启 Factory.app 以应用中文界面。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Factory Desktop Chinese Localization Patcher")
    parser.add_argument("--force", action="store_true", help="Force re-patch from clean backup")
    parser.add_argument("--no-guard", action="store_true", help="Do not install/reload LaunchAgent")
    parser.add_argument("--skip-kill", action="store_true", help="Do not auto-restart Factory.app after patching")
    parser.add_argument("--check", action="store_true", help="Check if Factory is currently patched")
    args = parser.parse_args()

    if args.check:
        res = run_engine(["--check"])
        if res.stdout:
            print(res.stdout.strip())
        sys.exit(res.returncode)

    if not os.path.exists(os.path.join(APP_PATH, "Contents", "Resources", "app.asar")):
        print(
            "[factory-zh] 错误：未找到 Factory.app（已检查 /Applications/Factory.app 与 ~/Applications/Factory.app）。\n"
            "请确认已安装 Factory Desktop，或通过 FACTORY_APP_PATH=/path/to/Factory.app 指定安装路径。"
        )
        sys.exit(1)

    sync_runtime_to_persist()

    engine_args = ["--force"] if args.force else []
    res = run_engine(engine_args)
    if res.stdout:
        print(res.stdout, end="")
    if res.returncode != 0:
        err_text = (res.stderr or "") + (res.stdout or "")
        if res.stderr:
            print(res.stderr, end="", file=sys.stderr)
        if any(k in err_text for k in ("EACCES", "EPERM", "Permission denied", "Operation not permitted")):
            print(
                f"\n[factory-zh] ⚠️ 权限不足：当前「终端」没有修改 {APP_PATH} 的权限。\n"
                "请任选以下一种方式解决：\n"
                "  方式 1（推荐）：运行  sudo python3 patcher.py\n"
                "  方式 2：打开「系统设置 -> 隐私与安全性 -> App 管理」，开启「终端 (Terminal)」权限后重试。",
                file=sys.stderr,
            )
        sys.exit(res.returncode)

    try:
        from localize_sessions import localize_all_sessions
        sres = localize_all_sessions()
        if sres["updated_jsonl"] or sres["updated_db"]:
            print(
                f"[factory-zh] Localized session titles: "
                f"{sres['updated_jsonl']} .jsonl files, {sres['updated_db']} SQLite rows."
            )
    except Exception as e:
        print(f"[factory-zh] Warning localizing sessions: {e}")

    if not args.no_guard:
        install_launch_agent()

    chown_to_real_user(PERSIST_DIR)
    restart_or_prompt_factory(args.skip_kill)


if __name__ == "__main__":
    main()
