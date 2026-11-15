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

        cfg = _live_config(str(base / "config.live.json"))
        print(("LIVE" if mode == "live" else "Dry run") + " cycle", flush=True)
        asyncio.run(live_run(cfg, mode == "live", False, connect))
    finally:
        if before is not None and TOKEN.exists() and hashlib.sha256(TOKEN.read_bytes()).hexdigest() != before:
            enc.write_bytes(_fernet(key).encrypt(TOKEN.read_bytes()))
            print("sign-in renewed and saved", flush=True)
        if TOKEN.exists():
            TOKEN.unlink()
        try:
            from .dashboard import write_data

            write_data()
            shutil.copyfile(base / "dashboard.html", base.parent / "index.html")
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
