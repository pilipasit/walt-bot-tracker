"""Run the live bot on GitHub Actions instead of this PC.

`cloud-setup` (run once by the owner, on this PC):
  copies the bot, its settings and its current state into the tracker repo's `bot/` folder,
  encrypts the owner's Walt sign-in with a new random key, stores that key as the GitHub
  secret WALT_TOKEN_KEY, moves the local sign-in file aside (so the PC bot can no longer
  trade with it), and pushes. It refuses to run while a local `waltbot live` process exists.
  Trading stays off until the owner sets the repo variable LIVE_ENABLED to true.

`cloud-run` (run by the workflow every ~10 minutes):
  decrypts the sign-in, runs one bot cycle (`prices`, `dry` or `live`), rebuilds the tracker
  page, and re-encrypts the sign-in, which Walt renews on every run.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .contracts import ROOT

SITE = ROOT.parent / "walt-bot-tracker"
REPO = "pilipasit/walt-bot-tracker"
TOKEN = Path.home() / ".waltbot" / "walt_oauth.json"


def _enc_path(base: Path) -> Path:
    return base / "secrets" / "walt_oauth.enc"


def _fernet(key: str):
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


# ------------------------------------------------------------------ setup (owner, local)
def _local_bot_running() -> bool:
    if os.name != "nt":
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    else:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ForEach-Object CommandLine"],
                             capture_output=True, text=True).stdout
    return any("waltbot live" in line and "cloud" not in line for line in out.splitlines())


def setup() -> None:
    from cryptography.fernet import Fernet

    if _local_bot_running():
        sys.exit("The bot is still running on this PC. Stop it first (Ctrl-C in its window), then run this again.")
    if not TOKEN.exists():
        sys.exit("No Walt sign-in found on this PC. Run: python -m waltbot live-login")
    dest = SITE / "bot"
    if (dest / "waltbot").exists():
        shutil.rmtree(dest / "waltbot")
    shutil.copytree(ROOT / "waltbot", dest / "waltbot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(ROOT / "config.live.json", dest / "config.live.json")
    (dest / "data" / "live").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "data" / "contracts_raw.json", dest / "data" / "contracts_raw.json")
    for f in ("state.json", "log.jsonl"):
        if (ROOT / "data" / "live" / f).exists():
            shutil.copyfile(ROOT / "data" / "live" / f, dest / "data" / "live" / f)
    (dest / "requirements.txt").write_text("mcp==2.3.0\ncryptography==50.0.2\n", encoding="utf-8")
    (dest / ".gitignore").write_text("__pycache__/\ndata/candles/\ndata/research/\ndashboard.html\ndata/live/dashboard_data.js\n", encoding="utf-8")
    key = Fernet.generate_key().decode()
    _enc_path(dest).parent.mkdir(parents=True, exist_ok=True)
    _enc_path(dest).write_bytes(_fernet(key).encrypt(TOKEN.read_bytes()))
    r = subprocess.run(["gh", "secret", "set", "WALT_TOKEN_KEY", "--repo", REPO], input=key, text=True, capture_output=True)
    if r.returncode:
        sys.exit("Could not store the key on GitHub: " + r.stderr.strip())
    # the sign-in now belongs to the cloud bot; two bots renewing it would lock each other out
    TOKEN.rename(TOKEN.with_name("walt_oauth.moved-to-github.json"))
    git = lambda *a: subprocess.run(["git", "-C", str(SITE), *a], capture_output=True, text=True)  # noqa: E731
    git("pull", "--rebase", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "Move the live bot to GitHub Actions")
    p = git("push", "-q")
    if p.returncode:
        sys.exit("Push failed: " + p.stderr.strip())
    print("Done. The bot, its state and your encrypted Walt sign-in are on GitHub; the key is a GitHub secret.")
    print("The sign-in file on this PC was renamed, so the PC bot can no longer trade.")
    print("Next, test without trading:   gh workflow run bot.yml --repo " + REPO + " -f mode=dry")
    print("Then switch real trading on:  gh variable set LIVE_ENABLED --body true --repo " + REPO)


# ------------------------------------------------------------------ move (owner, local: the one command)
def _pc_processes(fragment: str) -> list[int]:
    """PIDs of python processes on this PC whose command line contains `fragment`."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                          "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                         capture_output=True, text=True).stdout
    me = os.getpid()
    return [int(line.split("\t", 1)[0]) for line in out.splitlines()
            if "\t" in line and fragment in line and "cloud" not in line and int(line.split("\t", 1)[0]) != me]


def move() -> None:
    """Everything the owner needs, in one command: stop the PC bot right after a cycle, hand the bot to GitHub,
    start the cloud loop, stop the PC tracker publisher, and show the first cloud cycle."""
    import time

    log = ROOT / "data" / "live" / "log.jsonl"
    pids = _pc_processes("waltbot live")
    if pids:
        # Stop BETWEEN cycles, never in the middle of one: wait for the next log line, then give it 10 seconds.
        print("Waiting for the PC bot to finish its current 10-minute cycle (up to 11 minutes)…", flush=True)
        start = log.stat().st_mtime if log.exists() else 0
        deadline = time.time() + 660
        while time.time() < deadline and (log.stat().st_mtime if log.exists() else 0) == start:
            time.sleep(5)
        time.sleep(10)
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(2)
        if _local_bot_running():
            sys.exit("Could not stop the PC bot. Close its window and run this again.")
        print("PC bot stopped.", flush=True)
    # The PC tracker publisher pushes to the same repo; stop it first so the hand-over push can't collide with it.
    for pid in _pc_processes("publish_tracker.py"):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    setup()
    started = time.time()
    r = subprocess.run(["gh", "workflow", "run", "bot.yml", "--repo", REPO, "-f", "mode=live", "-f", "minutes=330"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("The bot is on GitHub but its first run did not start: " + r.stderr.strip()
                 + "\nIt starts by itself within the hour (backup schedule), or run: gh workflow run bot.yml --repo "
                 + REPO + " -f mode=live")
    print("Cloud bot started. Waiting for its first cycle (a few minutes)…", flush=True)
    cloud_log = SITE / "bot" / "data" / "live" / "log.jsonl"
    for _ in range(48):  # up to 8 minutes
        time.sleep(10)
        subprocess.run(["git", "-C", str(SITE), "pull", "--rebase", "-q"], capture_output=True)
        lines = cloud_log.read_text(encoding="utf-8").splitlines() if cloud_log.exists() else []
        if lines and json.loads(lines[-1]).get("time", "") >= time.strftime("%Y-%m-%d %H:%M", time.gmtime(started)):
            break
        out = subprocess.run(["gh", "run", "list", "--repo", REPO, "--workflow", "bot.yml", "-L", "1",
                              "--json", "status,conclusion,url"], capture_output=True, text=True).stdout
        try:
            last = json.loads(out)[0]
        except Exception:  # noqa: BLE001
            continue
        if last["status"] == "completed" and last["conclusion"] != "success":
            sys.exit("The first cloud run failed. Open " + last["url"] + " and send the link to Claude.")
    else:
        print("No cloud cycle seen yet; it may still be starting. Check later with: python -m waltbot cloud-status")
    status()
    print("\nDone. The bot now runs on GitHub every 10 minutes, without this PC. The PC can sleep or be off.")
    print("Watch it: https://pilipasit.github.io/walt-bot-tracker/   Stop it: set LIVE_ENABLED to false on GitHub.")


# ------------------------------------------------------------------ relogin (owner, local)
def relogin() -> None:
    """The cloud bot's Walt sign-in expired: sign in again in the browser and hand ONLY the new sign-in to GitHub.
    The cloud bot's code, state and log stay as they are on GitHub (the PC copies are older)."""
    import asyncio

    from cryptography.fernet import Fernet

    from .walt_client import connect

    async def go():
        async with connect(interactive=True) as walt:
            print("Signed in. Server time:", await walt.call("get_server_time"), flush=True)

    asyncio.run(go())
    if not TOKEN.exists():
        sys.exit("No sign-in was saved; run this again and finish the sign-in in the browser.")
    git = lambda *a: subprocess.run(["git", "-C", str(SITE), *a], capture_output=True, text=True)  # noqa: E731
    git("pull", "--rebase", "-q")
    key = Fernet.generate_key().decode()
    _enc_path(SITE / "bot").write_bytes(_fernet(key).encrypt(TOKEN.read_bytes()))
    r = subprocess.run(["gh", "secret", "set", "WALT_TOKEN_KEY", "--repo", REPO], input=key, text=True, capture_output=True)
    if r.returncode:
        sys.exit("Could not store the key on GitHub: " + r.stderr.strip())
    TOKEN.replace(TOKEN.with_name("walt_oauth.moved-to-github.json"))  # one owner of the sign-in: the cloud bot
    git("add", str(_enc_path(SITE / "bot")))
    git("commit", "-q", "-m", "New Walt sign-in for the cloud bot")
    for _ in range(4):
        if git("pull", "--rebase", "-q", "-X", "theirs").returncode == 0 and git("push", "-q").returncode == 0:
            break
    else:
        sys.exit("Push failed; run this again.")
    # A running loop holds the OLD key from when it started, so restart it on the new one.
    ids = subprocess.run(["gh", "run", "list", "--repo", REPO, "--workflow", "bot.yml", "--status", "in_progress",
                          "--json", "databaseId", "-q", ".[].databaseId"], capture_output=True, text=True).stdout.split()
    for i in ids:
        subprocess.run(["gh", "run", "cancel", i, "--repo", REPO], capture_output=True)
    import time
    time.sleep(15)
    subprocess.run(["gh", "workflow", "run", "bot.yml", "--repo", REPO, "-f", "mode=live", "-f", "minutes=330"],
                   capture_output=True)
    print("Done. The cloud bot restarted with the new sign-in; its first check runs within a minute. The PC can be off.")


# ------------------------------------------------------------------ run (workflow)
def run(mode: str) -> None:
    base = ROOT
    enc = _enc_path(base)
    key = os.environ.get("WALT_TOKEN_KEY", "")
    before = None
    try:
        if mode == "prices":
            from .__main__ import _live_config
            from .live import market_signal

            cfg = _live_config(str(base / "config.live.json"))
            for s in cfg["symbols"]:
                t, bar, px = market_signal(s, cfg)
                print(f"{s}: rule {'long' if t > 0 else 'short' if t < 0 else 'flat'}, last 4h bar {bar}, price {px}")
            return
        if not key or not enc.exists():
            sys.exit("Missing WALT_TOKEN_KEY secret or encrypted sign-in; run cloud-setup on the PC first.")
        TOKEN.parent.mkdir(parents=True, exist_ok=True)
        TOKEN.write_bytes(_fernet(key).decrypt(enc.read_bytes()))
        before = hashlib.sha256(TOKEN.read_bytes()).hexdigest()
        from .__main__ import _live_config
        from .live import run as live_run
        from .walt_client import connect

        if mode != "markets":  # "markets" = the analysis page only: no trading code runs at all
            cfg = _live_config(str(base / "config.live.json"))
            print(("LIVE" if mode == "live" else "Dry run") + " cycle", flush=True)
            asyncio.run(live_run(cfg, mode == "live", False, connect))
        try:  # sample Walt's prices for the analysis page with the same sign-in
            from .markets import sample

            async def _sample():
                async with connect() as client:
                    return await sample(client)

            print(f"market prices sampled: {asyncio.run(_sample())}", flush=True)
        except Exception as e:  # noqa: BLE001
            print("market sampling skipped:", str(e)[:200], flush=True)
    finally:
        try:
            from .markets import build

            build()
        except Exception as e:  # noqa: BLE001
            print("market analysis not rebuilt:", str(e)[:200], flush=True)
        if before is not None and TOKEN.exists() and hashlib.sha256(TOKEN.read_bytes()).hexdigest() != before:
            enc.write_bytes(_fernet(key).encrypt(TOKEN.read_bytes()))
            print("sign-in renewed and saved", flush=True)
        if TOKEN.exists():
            TOKEN.unlink()
        if mode == "markets":
            return
        try:
            from .dashboard import write_data

            write_data()
            shutil.copyfile(base / "dashboard.html", base.parent / "bot.html")  # index.html is the markets page
        except Exception as e:  # noqa: BLE001
            print("tracker page not rebuilt:", e, flush=True)


def status() -> None:
    """Print what the bot's GitHub copy last did (for the owner and the hourly check)."""
    log = SITE / "bot" / "data" / "live" / "log.jsonl"
    subprocess.run(["git", "-C", str(SITE), "pull", "--rebase", "-q"], capture_output=True)
    if not log.exists():
        print("No cloud log yet.")
        return
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    for e in lines[-8:]:
        print(e["time"], e["mode"], e["event"], e.get("symbol", ""), e.get("value", ""), e.get("reason", ""), str(e.get("error", ""))[:80])
