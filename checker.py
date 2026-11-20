#!/usr/bin/env python3
"""
Discord Username Checker: find available usernames, no token or login needed.

Just run it:            python checker.py
Change the settings:    python checker.py --setup
See all options:        python checker.py --help

It checks Discord's public sign-up endpoint, remembers every name it has already
checked, and saves free names to hits.txt. It never logs in and cannot claim a
name for you; when it finds a free one, you go claim it.

Educational use only. Respect Discord's rate limits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import string
import sys
import time
from pathlib import Path

try:
    import aiohttp
except ImportError:
    sys.exit("Missing dependency. Run:  pip install aiohttp   (or double-click run.bat)")

VERSION = "2.0.0"
ENDPOINT = os.environ.get(
    "DUC_ENDPOINT", "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
)
HOME = Path(os.environ.get("DUC_HOME") or Path(__file__).resolve().parent)
CHECKED_FILE = HOME / "checked.txt"
HITS_FILE = HOME / "hits.txt"
CONFIG_FILE = HOME / "config.json"
PROXY_FILE = HOME / "proxies.txt"

SPEEDS = {"safe": 2.0, "normal": 1.2, "fast": 0.6}   # seconds between checks
KINDS = {"mix": "letters and numbers mixed", "letters": "letters only",
         "digits": "numbers only", "any": "anything"}
DEFAULTS = {"length": 4, "kind": "mix", "speed": "safe", "proxies": "none",
            "proxy_file": "", "names_file": "", "webhook": ""}

MAX_ATTEMPTS = 6        # tries per name before it is skipped (and NOT marked as checked)
MAX_STRIKES = 3         # failures before a proxy is dropped
FREE_PROXY_SAMPLE = 4000
LETTERS, DIGITS = string.ascii_lowercase, string.digits
NAME_RE = re.compile(r"^[a-z0-9._]{2,32}$")
PROXY_RE = re.compile(r"^(?:https?://)?(?:[^@\s]+@)?[a-zA-Z0-9][a-zA-Z0-9\-.]*:\d{1,5}$")
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
]

# ---------------------------------------------------------------------------
# Files and settings
# ---------------------------------------------------------------------------


def load_set(path: Path) -> set[str]:
    try:
        return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def append_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def describe(cfg: dict) -> str:
    if cfg["names_file"]:
        what = f"names from {cfg['names_file']}"
    else:
        what = f"{cfg['length']}-character names, {KINDS[cfg['kind']]}"
    proxies = {"none": "no proxies", "free": "free proxies"}.get(cfg["proxies"], "your proxy file")
    return f"{what} | {cfg['speed']} speed | {proxies}"


# ---------------------------------------------------------------------------
# Setup wizard (only runs with --setup)
# ---------------------------------------------------------------------------


def choose(title: str, options: list[str], default: int = 1) -> int:
    print(f"\n{title}")
    for i, text in enumerate(options, 1):
        print(f"  {i}) {text}{'   <- recommended' if i == default else ''}")
    while True:
        raw = input(f"Choose 1-{len(options)} [{default}]: ").strip() or str(default)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print("  Please type one of the numbers above.")


def wizard() -> dict:
    cfg = dict(DEFAULTS)
    pick = choose("What kind of usernames do you want?", [
        "4 characters, letters + numbers mixed (like a1b2)",
        "3 letters (like abc)",
        "4 letters (like abcd)",
        "4 numbers (like 1234)",
        "Names from my own file",
        "Custom length and type",
    ])
    if pick == 1:
        cfg.update(length=4, kind="mix")
    elif pick == 2:
        cfg.update(length=3, kind="letters")
    elif pick == 3:
        cfg.update(length=4, kind="letters")
    elif pick == 4:
        cfg.update(length=4, kind="digits")
    elif pick == 5:
        cfg["names_file"] = input("\nPath to your file (one name per line) [names.txt]: ").strip() or "names.txt"
    else:
        raw = input("\nLength, 2 to 6 [4]: ").strip() or "4"
        cfg["length"] = min(max(int(raw) if raw.isdigit() else 4, 2), 6)
        cfg["kind"] = ["mix", "letters", "digits", "any"][choose(
            "Made of?", ["Letters and numbers mixed", "Letters only", "Numbers only", "Anything"]) - 1]

    cfg["speed"] = ["safe", "normal", "fast"][choose("How fast?", [
        "Safe: one check every 2 seconds, least likely to get blocked",
        "Normal: one check every 1.2 seconds",
        "Fast: one check every 0.6 seconds (works best with your own proxies)",
    ]) - 1]

    p = choose("Proxies?", [
        "No proxies (simple; if Discord slows you down, the tool just waits)",
        "Free public proxies (slow to set up, unreliable, but can go faster)",
        "My own proxy file (one host:port per line)",
    ])
    if p == 2:
        cfg["proxies"] = "free"
    elif p == 3:
        cfg["proxies"] = "file"
        cfg["proxy_file"] = input("\nPath to your proxy file [proxies.txt]: ").strip() or "proxies.txt"

    cfg["webhook"] = input("\nDiscord webhook URL to ping when a name is free (press Enter to skip): ").strip()
    save_config(cfg)
    print(f"\nSaved. Next time just run:  python checker.py\n")
    return cfg


# ---------------------------------------------------------------------------
# Name generation
# ---------------------------------------------------------------------------


def gen_names(count: int, length: int, kind: str, skip: set[str]) -> list[str]:
    alphabet = {"letters": LETTERS, "digits": DIGITS}.get(kind, LETTERS + DIGITS)
    rng = random.Random()
    out: set[str] = set()
    tries = 0
    while len(out) < count and tries < count * 400:
        tries += 1
        name = "".join(rng.choice(alphabet) for _ in range(length))
        if kind == "mix" and (name.isalpha() or name.isdigit()):
            continue
        if name not in skip:
            out.add(name)
    return list(out)


def file_names(path: str, skip: set[str]) -> tuple[list[str], int]:
    """Valid, unchecked names from a file, and how many lines were not valid names."""
    names, invalid = [], 0
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        sys.exit(f"Could not read {path}: {e.strerror}")
    seen: set[str] = set()
    for line in lines:
        name = line.strip().lower()
        if not name:
            continue
        if not NAME_RE.match(name):
            invalid += 1
        elif name not in skip and name not in seen:
            seen.add(name)
            names.append(name)
    return names, invalid


# ---------------------------------------------------------------------------
# Talking to Discord
# ---------------------------------------------------------------------------


async def check(session: aiohttp.ClientSession, name: str, proxy: str | None) -> tuple[str, float]:
    """Returns (status, wait): status is 'free', 'taken', 'limited' or 'error'."""
    try:
        async with session.post(ENDPOINT, json={"username": name}, proxy=proxy) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and isinstance(data.get("taken"), bool):
                    return ("taken" if data["taken"] else "free"), 0.0
                return "error", 0.0
            if resp.status == 429:
                try:
                    wait = float((await resp.json(content_type=None)).get("retry_after", 5))
                except Exception:
                    wait = float(resp.headers.get("Retry-After", 5) or 5)
                return "limited", wait
            return "error", 0.0
    except Exception:
        return "error", 0.0


class Proxies:
    """A list of proxies used one at a time; a proxy that keeps failing is dropped."""

    def __init__(self, mode: str, items: list[str]):
        self.mode, self.items, self.idx = mode, items, 0
        self.strikes: dict[str, int] = {}
        self.total = len(items)

    @property
    def active(self) -> bool:
        return bool(self.items)

    def current(self) -> str | None:
        return self.items[self.idx % len(self.items)] if self.items else None

    def good(self, proxy: str | None) -> None:
        if proxy:
            self.strikes.pop(proxy, None)

    def bad(self, proxy: str | None) -> bool:
        """Count a failure and move on. Returns True if the proxy was dropped."""
        if not self.items or proxy not in self.items:
            return False
        self.strikes[proxy] = self.strikes.get(proxy, 0) + 1
        if self.strikes[proxy] >= MAX_STRIKES:
            position = self.items.index(proxy)
            self.items.remove(proxy)
            self.strikes.pop(proxy, None)
            if self.items:
                self.idx = position % len(self.items)   # the next proxy slid into this slot
            return True
        self.idx = (self.idx + 1) % len(self.items)
        return False


async def scrape_free_proxies(session: aiohttp.ClientSession, say) -> list[str]:
    say("Looking for working free proxies. This can take a few minutes and often finds very few.")
    raw: set[str] = set()

    async def fetch(url: str) -> None:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    raw.update(p.strip() for p in (await resp.text()).splitlines() if PROXY_RE.match(p.strip()))
        except Exception:
            pass

    await asyncio.gather(*(fetch(u) for u in PROXY_SOURCES))
    sample = random.sample(sorted(raw), min(len(raw), FREE_PROXY_SAMPLE))
    say(f"Found {len(raw)} candidates, testing {len(sample)} against Discord...")
    sem, working, done = asyncio.Semaphore(200), [], 0

    async def test(proxy: str) -> None:
        nonlocal done
        async with sem:
            status, _ = await check(session, "aa", proxy)
            if status == "taken":
                working.append(proxy)
            done += 1
            if done % 500 == 0:
                say(f"  tested {done}/{len(sample)}, {len(working)} working so far")

    await asyncio.gather(*(test(p) for p in sample))
    say(f"{len(working)} working proxies found.")
    return working


# ---------------------------------------------------------------------------
# Live display
# ---------------------------------------------------------------------------


class Screen:
    def __init__(self, verbose: bool):
        self.verbose, self.width = verbose, 0

    def say(self, text: str) -> None:
        """Print a message on its own line."""
        self.clear()
        print(text, flush=True)

    def debug(self, text: str) -> None:
        if self.verbose:
            self.say(f"  {text}")

    def clear(self) -> None:
        if self.width:
            print("\r" + " " * self.width + "\r", end="", flush=True)
            self.width = 0

    def live(self, text: str) -> None:
        pad = max(self.width - len(text), 0)
        print("\r" + text + " " * pad, end="", flush=True)
        self.width = len(text)


async def notify(session: aiohttp.ClientSession, url: str, name: str) -> None:
    payload = {"embeds": [{"title": "Username available", "description": f"**{name}** is free. Go claim it!",
                           "color": 0x30D158, "footer": {"text": "Discord Username Checker"}}]}
    for _ in range(2):
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status != 429:
                    return
                await asyncio.sleep(float((await resp.json(content_type=None)).get("retry_after", 2)))
        except Exception:
            return


# ---------------------------------------------------------------------------
# The hunt
# ---------------------------------------------------------------------------


async def hunt(cfg: dict, screen: Screen, stats: dict) -> None:
    delay = SPEEDS[cfg["speed"]]
    checked = load_set(CHECKED_FILE)
    skipped: set[str] = set()
    screen.say(f"{len(checked):,} usernames already checked" + (" (resuming)" if checked else ""))

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), trust_env=False) as session:
        # --- proxies
        pool = Proxies(cfg["proxies"], [])
        if cfg["proxies"] == "file":
            path = Path(cfg["proxy_file"])
            items = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                     if PROXY_RE.match(ln.strip())] if path.exists() else []
            if not items:
                screen.say(f"No usable proxies in {path}; continuing without proxies.")
            pool = Proxies("file", items)
        elif cfg["proxies"] == "free":
            pool = Proxies("free", await scrape_free_proxies(session, screen.say))
            if not pool.active:
                screen.say("No working free proxies found; continuing without proxies (slower, but it works).")

        screen.say(f"Running: {describe(cfg)}\nPress Ctrl+C any time to stop; progress is saved.\n")
        started = time.monotonic()
        waiting = 0.0
        batch: list[str] = []

        def refresh(last: str = "") -> None:
            elapsed = time.monotonic() - started
            rate = f"{stats['checked'] / (elapsed / 60):.0f}/min" if elapsed >= 10 else "--/min"
            proxy_part = f" | proxies {len(pool.items)}/{pool.total}" if pool.total else ""
            wait_part = f" | rate-limited, waiting {waiting:.0f}s" if waiting else ""
            screen.live(f"  checked {stats['checked']:,} | free {stats['free']} | "
                        f"{rate}{proxy_part}{wait_part} {last}")

        while True:
            if not batch:
                if cfg["names_file"]:
                    names, invalid = file_names(cfg["names_file"], checked | skipped)
                    if invalid and not stats["note_invalid"]:
                        stats["note_invalid"] = True
                        screen.say(f"Ignoring {invalid} lines in {cfg['names_file']} that are not valid usernames.")
                    batch = names[:500]
                else:
                    batch = gen_names(500, cfg["length"], cfg["kind"], checked | skipped)
                if not batch:
                    screen.say("No new usernames left to check. Done!")
                    return

            name = batch.pop()
            attempt = 0
            while True:
                attempt += 1
                proxy = pool.current()
                status, wait = await check(session, name, proxy)
                if status in ("free", "taken"):
                    pool.good(proxy)
                    break
                stats["retries"] += 1
                if pool.active:
                    dropped = pool.bad(proxy)
                    screen.debug(f"{status} via {proxy}" + (" (proxy dropped)" if dropped else ""))
                    if not pool.active:
                        screen.say("All proxies failed; continuing without proxies (slower).")
                        attempt = 0   # a fresh start for this name, now on your own connection
                    continue
                if status == "limited":
                    stats["limited"] += 1
                    waiting = min(max(wait, 3.0), 60.0)
                    while waiting > 0:
                        refresh()
                        await asyncio.sleep(1)
                        waiting -= 1
                    waiting = 0.0
                else:
                    await asyncio.sleep(min(2 * attempt, 10))
                if attempt >= MAX_ATTEMPTS:
                    status = "gave_up"
                    break
            if status == "gave_up":
                # Never mark a name as checked when we could not get a real answer.
                skipped.add(name)
                stats["skipped"] += 1
                screen.debug(f"gave up on {name} (no answer); it will not be marked as checked")
                continue

            checked.add(name)
            append_line(CHECKED_FILE, name)
            stats["checked"] += 1
            if status == "free":
                stats["free"] += 1
                stats["hits"].append(name)
                append_line(HITS_FILE, name)
                screen.say("\n" + "*" * 46 + f"\n   AVAILABLE: {name}   (saved to hits.txt)\n" + "*" * 46 + "\n")
                if cfg["webhook"]:
                    await notify(session, cfg["webhook"], name)
            refresh(f"| last: {name}")
            await asyncio.sleep(delay)


def summary(stats: dict, started: float) -> None:
    mins = (time.monotonic() - started) / 60
    print(f"\n\n--- Summary ---\n  Checked this session: {stats['checked']:,} in {mins:.1f} min")
    print(f"  Free names found: {stats['free']}" + (f"  ->  {', '.join(stats['hits'])}" if stats["hits"] else ""))
    if stats["free"]:
        print(f"  (also saved in {HITS_FILE.name})")
    if stats["skipped"]:
        print(f"  Skipped {stats['skipped']} names Discord never answered for (they will be retried next run).")
    if stats["limited"]:
        print(f"  Rate-limited {stats['limited']} times; try the 'safe' speed or your own proxies.")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Find available Discord usernames. Run with no options to start.")
    ap.add_argument("--setup", action="store_true", help="answer a few simple questions and save your settings")
    ap.add_argument("--length", type=int, choices=range(2, 7), metavar="2-6", help="username length")
    ap.add_argument("--type", choices=list(KINDS), help="letters, digits, mix (both) or any")
    ap.add_argument("--speed", choices=list(SPEEDS), help="safe (default), normal or fast")
    ap.add_argument("--proxies", metavar="none|free|FILE", help="proxy mode or a file with one host:port per line")
    ap.add_argument("--names", metavar="FILE", help="check names from a file instead of generating them")
    ap.add_argument("--webhook", metavar="URL", help="Discord webhook to ping when a name is free")
    ap.add_argument("--verbose", action="store_true", help="show detailed proxy and retry messages")
    ap.add_argument("--reset", action="store_true", help="forget every checked username (asks first)")
    return ap.parse_args()


def settings_from(args: argparse.Namespace) -> dict:
    if args.setup:
        return wizard()
    first_run = not CONFIG_FILE.exists()
    cfg = load_config()
    if first_run:
        print("First run: starting with simple defaults. Run  python checker.py --setup  to change them.\n")
    if args.length:
        cfg["length"] = args.length
    if args.type:
        cfg["kind"] = args.type
    if args.speed:
        cfg["speed"] = args.speed
    if args.names:
        cfg["names_file"] = args.names
    if args.webhook:
        cfg["webhook"] = args.webhook
    if args.proxies:
        if args.proxies in ("none", "free"):
            cfg["proxies"] = args.proxies
        else:
            cfg["proxies"], cfg["proxy_file"] = "file", args.proxies
    if cfg["speed"] not in SPEEDS or cfg["kind"] not in KINDS:
        cfg.update(speed="safe", kind="mix")
    return cfg


def main() -> None:
    args = parse_args()
    print(f"\n  Discord Username Checker v{VERSION}\n")
    if args.reset:
        if input(f"This forgets all {len(load_set(CHECKED_FILE)):,} checked names. Type YES to confirm: ").strip() == "YES":
            CHECKED_FILE.unlink(missing_ok=True)
            print("Done.")
        else:
            print("Cancelled; nothing changed.")
        return
    cfg = settings_from(args)
    stats = {"checked": 0, "free": 0, "hits": [], "limited": 0, "retries": 0, "skipped": 0, "note_invalid": False}
    screen, started = Screen(args.verbose), time.monotonic()
    try:
        asyncio.run(hunt(cfg, screen, stats))
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear()
        summary(stats, started)


if __name__ == "__main__":
    main()
