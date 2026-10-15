#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Factory Desktop (v0.198.1+) Clean Uninstaller & Restorer
Unloads LaunchAgent, stages APFS clone in /private/tmp, restores original app.asar, Info.plist, and bin/droid,
re-signs inside-out, atomically swaps back into Factory.app, cleans up ~/.factory-zh, and restarts Factory.
"""
from __future__ import annotations

import argparse
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
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
BACKUP_DIR = (
    os.path.join(PERSIST_DIR, "backup")
    if os.path.isdir(os.path.join(PERSIST_DIR, "backup"))
    else os.path.join(BASE_DIR, "backup")
)


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
BACKUP_ASAR = os.path.join(BACKUP_DIR, "app.asar.orig")
BACKUP_PLIST = os.path.join(BACKUP_DIR, "Info.plist.orig")
STATE_FILE = os.path.join(BACKUP_DIR, "patch-state.json")
LOCK_DIR = f"/tmp/factory-zh-{REAL_UID}.lock"
PLIST_LABEL = "com.factory.zh-cn"
PLIST_PATH = os.path.join(REAL_HOME, "Library", "LaunchAgents", f"{PLIST_LABEL}.plist")

ENTITLEMENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>com.apple.security.cs.allow-jit</key>
\t<true/>
\t<key>com.apple.security.cs.allow-unsigned-executable-memory</key>
\t<true/>
\t<key>com.apple.security.cs.disable-library-validation</key>
\t<true/>
\t<key>com.apple.security.device.audio-input</key>
\t<true/>
\t<key>com.apple.security.device.camera</key>
\t<true/>
\t<key>com.apple.security.automation.apple-events</key>
\t<true/>
</dict>
</plist>
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


def acquire_lock(timeout_sec: float = 15.0) -> bool:
    shutil.rmtree(os.path.join(BACKUP_DIR, ".patcher.lock"), ignore_errors=True)
    shutil.rmtree(os.path.join(BASE_DIR, "backup", ".patcher.lock"), ignore_errors=True)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            os.mkdir(LOCK_DIR, 0o700)
            with open(os.path.join(LOCK_DIR, "pid"), "w", encoding="utf-8") as fp:
                fp.write(str(os.getpid()))
            chown_to_real_user(LOCK_DIR)
            return True
        except FileExistsError:
            try:
                with open(os.path.join(LOCK_DIR, "pid"), "r", encoding="utf-8") as fp:
                    pid = int(fp.read().strip())
                if pid > 0:
                    os.kill(pid, 0)
            except Exception:
                shutil.rmtree(LOCK_DIR, ignore_errors=True)
                continue
            time.sleep(0.2)
    return False


def release_lock() -> None:
    shutil.rmtree(LOCK_DIR, ignore_errors=True)


def is_running_inside_factory() -> bool:
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
            "[factory-zh] 检测到当前在内置终端中运行（或指定了 --skip-kill），已跳过自动重启。\n"
            "[factory-zh] 请按 Cmd + Q 完全退出并重新打开 Factory 即可恢复英文原版界面！"
        )
        return
    try:
        print("[factory-zh] 正在为您自动重启 Factory.app 以恢复英文原版界面...")
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
        print("[factory-zh] 请手动重启 Factory.app 以恢复英文原版界面。")


def collect_files_recursive(root_dir: str, suffixes: tuple[str, ...] | None = None) -> list[str]:
    result: list[str] = []
    if not os.path.isdir(root_dir):
        return result
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            if suffixes is None or full.endswith(suffixes):
                result.append(full)
    return result


def cleanup_persist_dir() -> None:
    if not os.path.isdir(PERSIST_DIR):
        return
    custom_titles = os.path.join(PERSIST_DIR, "backup", "custom-session-titles.json")
    has_custom = os.path.isfile(custom_titles)
    for entry in os.listdir(PERSIST_DIR):
        full = os.path.join(PERSIST_DIR, entry)
        if entry == "backup" and has_custom:
            for bfile in os.listdir(full):
                if bfile != "custom-session-titles.json":
                    bfull = os.path.join(full, bfile)
                    if os.path.isdir(bfull):
                        shutil.rmtree(bfull, ignore_errors=True)
                    else:
                        try:
                            os.remove(bfull)
                        except Exception:
                            pass
        else:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                try:
                    os.remove(full)
                except Exception:
                    pass
    if not has_custom:
        shutil.rmtree(PERSIST_DIR, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Factory Desktop Clean Uninstaller & Restorer")
    parser.add_argument("--skip-kill", action="store_true", help="Do not auto-restart Factory.app after restoring")
    args = parser.parse_args()

    uid_str = str(REAL_UID)
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid_str}/{PLIST_LABEL}"], stderr=subprocess.DEVNULL)
    if os.path.exists(PLIST_PATH):
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{uid_str}", PLIST_PATH], stderr=subprocess.DEVNULL)
        try:
            os.remove(PLIST_PATH)
            print(f"[factory-zh] Unloaded and removed LaunchAgent: {PLIST_PATH}")
        except Exception:
            pass

    if not os.path.exists(BACKUP_ASAR) or not os.path.exists(BACKUP_PLIST):
        print("[factory-zh] No backup files found in ~/.factory-zh/backup; nothing to restore.")
        return

    if not acquire_lock():
        raise RuntimeError("[factory-zh] Timed out waiting for active patcher lock.")

    work_tmp_dir = tempfile.mkdtemp(prefix="factory-zh-restore-", dir="/private/tmp")
    staging_app = os.path.join(work_tmp_dir, "Factory.app.staging")
    old_app = os.path.join(work_tmp_dir, "Factory.app.old")
    ent_path = os.path.join(work_tmp_dir, "entitlements.plist")

    try:
        with open(ent_path, "w", encoding="utf-8") as fp:
            fp.write(ENTITLEMENTS_XML)

        subprocess.run(["/bin/cp", "-cR", APP_PATH, staging_app], check=True)
        shutil.copy2(BACKUP_ASAR, os.path.join(staging_app, "Contents/Resources/app.asar"))
        shutil.copy2(BACKUP_PLIST, os.path.join(staging_app, "Contents/Info.plist"))
        backup_droid = os.path.join(BACKUP_DIR, "droid.orig")
        if os.path.exists(backup_droid):
            shutil.copy2(backup_droid, os.path.join(staging_app, "Contents/Resources/bin/droid"))

        contents = os.path.join(staging_app, "Contents")
        resources = os.path.join(contents, "Resources")
        frameworks_dir = os.path.join(contents, "Frameworks")

        unpacked_nodes = collect_files_recursive(
            os.path.join(resources, "app.asar.unpacked"), (".node", ".dylib")
        )
        resource_bins = collect_files_recursive(os.path.join(resources, "bin"), None)
        frameworks = (
            [
                os.path.join(frameworks_dir, n)
                for n in os.listdir(frameworks_dir)
                if n.endswith(".framework")
            ]
            if os.path.isdir(frameworks_dir)
            else []
        )
        helper_apps = (
            [
                os.path.join(frameworks_dir, n)
                for n in os.listdir(frameworks_dir)
                if n.endswith(".app")
            ]
            if os.path.isdir(frameworks_dir)
            else []
        )

        sign_plain = unpacked_nodes + resource_bins + frameworks
        sign_ent = helper_apps + [staging_app]

        for t in sign_plain:
            if os.path.exists(t):
                subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", t], check=True)
        for t in sign_ent:
            if os.path.exists(t):
                subprocess.run(
                    ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", "--entitlements", ent_path, t],
                    check=True,
                )

        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", staging_app], check=True)

        os.rename(APP_PATH, old_app)
        try:
            os.rename(staging_app, APP_PATH)
        except Exception:
            os.rename(old_app, APP_PATH)
            raise
        shutil.rmtree(old_app, ignore_errors=True)

        backup_local_droid = os.path.join(BACKUP_DIR, "local-droid.orig")
        local_droid = os.path.join(REAL_HOME, ".local/bin/droid")
        if os.path.exists(backup_local_droid) and os.path.exists(local_droid):
            try:
                tmp_local = f"{local_droid}.zh-restore-{os.getpid()}"
                shutil.copy2(backup_local_droid, tmp_local)
                os.replace(tmp_local, local_droid)
                subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", local_droid], check=True)
                chown_to_real_user(local_droid)
            except Exception:
                pass

        try:
            from localize_sessions import restore_all_sessions
            restore_all_sessions()
        except Exception:
            pass

        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", APP_PATH], check=True)
        cleanup_persist_dir()
        print("[factory-zh] Restored original Factory.app, cleaned ~/.factory-zh, and verified codesign!")
        restart_or_prompt_factory(args.skip_kill)
    except PermissionError:
        print(
            f"\n[factory-zh] ⚠️ 权限不足：当前「终端」没有修改 {APP_PATH} 的权限。\n"
            "请任选以下一种方式解决：\n"
            "  方式 1（推荐）：运行  sudo python3 restore.py\n"
            "  方式 2：打开「系统设置 -> 隐私与安全性 -> App 管理」，开启「终端 (Terminal)」权限后重试。",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        shutil.rmtree(work_tmp_dir, ignore_errors=True)
        release_lock()


if __name__ == "__main__":
    main()
