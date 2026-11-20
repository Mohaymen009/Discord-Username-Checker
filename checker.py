#!/usr/bin/env python3
"""
Discord Username Checker: find available usernames, no token or login needed.

Just run it:                 python checker.py
Check specific names:        python checker.py --check cool.name abc123
Change settings (guided):    python checker.py --setup
See every setting:           python checker.py --show
All options:                 python checker.py --help

Every setting has a sensible default, can be saved in config.json, and can be
overridden for one run with a flag (for example --length 3 --speed fast).
It never logs in and cannot claim a name for you.

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

VERSION = "3.0.0"
ENDPOINT = os.environ.get(
    "DUC_ENDPOINT", "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
)
HOME = Path(os.environ.get("DUC_HOME") or Path(__file__).resolve().parent)
CONFIG_FILE = HOME / "config.json"

SPEEDS = {"safe": 2.0, "normal": 1.2, "fast": 0.6}      # seconds between checks, per worker
KINDS = {"mix": "letters and numbers mixed", "letters": "letters only",
         "digits": "numbers only", "any": "anything (letters and numbers)"}
WEBHOOK_MODES = {"off": "never", "hits": "only when a name is free",
                 "taken": "only for taken names", "all": "every check"}
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
# Settings: one table drives defaults, config.json, command-line flags, --show
# and the optional "more options" part of the setup. (key: default, type, help)
# ---------------------------------------------------------------------------

SCHEMA: dict[str, dict] = {
    # what to check
    "length": dict(default="4", type="length", help="username length: a number (4) or a range (3-5)"),
    "kind": dict(default="mix", type="choice", choices=list(KINDS), flag="--type",
                 help="what generated names are made of: " + ", ".join(KINDS)),
    "chars": dict(default="", type="str", help="your own alphabet instead of --type, e.g. abc123_"),
    "pattern": dict(default="", type="str",
                    help="a template: ? any char, # a digit, @ a letter, anything else is literal (e.g. a@@#)"),
    "names_file": dict(default="", type="str", flag="--names", help="check names from this file instead of generating"),
    # how fast
    "speed": dict(default="safe", type="choice", choices=list(SPEEDS),
                  help="safe = 1 check / 2s, normal = 1.2s, fast = 0.6s"),
    "delay": dict(default=0.0, type="float", help="exact seconds between checks per worker (0 = use --speed)"),
    "workers": dict(default=1, type="int", min=1, max=50,
                    help="checks running at once (more than 1 needs proxies or you will be rate-limited)"),
    # proxies
    "proxies": dict(default="none", type="proxies", help="none, free, or the path to your own proxy file"),
    "proxy_file": dict(default="", type="str", help="proxy file to use when proxies = file"),
    "proxy_strikes": dict(default=3, type="int", min=1, max=20, help="failures before a proxy is dropped"),
    # notifications
    "webhook": dict(default="", type="str", help="Discord webhook URL for notifications"),
    "webhook_fail": dict(default="", type="str", help="separate webhook for taken names (default: same as webhook)"),
    "webhook_mode": dict(default="hits", type="choice", choices=list(WEBHOOK_MODES),
                         help="when to notify: " + ", ".join(f"{k} ({v})" for k, v in WEBHOOK_MODES.items())),
    "beep": dict(default=False, type="bool", help="play a sound when a free name is found"),
    # when to stop
    "stop_after_hits": dict(default=0, type="int", min=0, help="stop after this many free names (0 = never)"),
    "max_checks": dict(default=0, type="int", min=0, help="stop after this many checks (0 = never)"),
    # files and reliability
    "hits_file": dict(default="hits.txt", type="str", help="where free names are saved"),
    "checked_file": dict(default="checked.txt", type="str", help="where checked names are remembered"),
    "max_attempts": dict(default=6, type="int", min=1, max=50, help="tries per name before skipping it"),
    "verbose": dict(default=False, type="bool", help="show detailed retry and proxy messages"),
}
QUICK_KEYS = {"length", "kind", "chars", "pattern", "names_file", "speed", "proxies", "proxy_file", "webhook"}


def defaults() -> dict:
    return {k: v["default"] for k, v in SCHEMA.items()}


def coerce(key: str, raw):
    """Turn user input into a valid value for `key` or raise ValueError with a plain message."""
    spec = SCHEMA[key]
    kind = spec["type"]
    text = str(raw).strip()
    if kind == "str":
        if key == "pattern" and text:
            sample = "".join("a" if c in "?@" else "1" if c == "#" else c for c in text.lower())
            if not NAME_RE.match(sample) or ".." in sample:
                raise ValueError("use 2-32 characters: ? any, # digit, @ letter, or literal a-z 0-9 . _")
        if key == "chars" and text and not re.fullmatch(r"[a-zA-Z0-9._]+", text):
            raise ValueError("only a-z, 0-9, . and _ are allowed")
        return text
    if kind == "bool":
        if text.lower() in ("1", "true", "yes", "y", "on"):
            return True
        if text.lower() in ("0", "false", "no", "n", "off"):
            return False
        raise ValueError("use on or off")
    if kind == "int":
        try:
            value = int(text)
        except ValueError:
            raise ValueError("use a whole number") from None
        if value < spec.get("min", -10**9) or value > spec.get("max", 10**9):
            raise ValueError(f"must be between {spec.get('min', '-')} and {spec.get('max', 'any')}")
        return value
    if kind == "float":
        try:
            value = float(text)
        except ValueError:
            raise ValueError("use a number like 1.5") from None
        if value < 0:
            raise ValueError("cannot be negative")
        return value
    if kind == "choice":
        if text.lower() not in spec["choices"]:
            raise ValueError("choose one of: " + ", ".join(spec["choices"]))
        return text.lower()
    if kind == "length":
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", text)
        if not m:
            raise ValueError("use a number like 4, or a range like 3-5")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 2 <= lo <= hi <= 32:
            raise ValueError("lengths must be between 2 and 32")
        return f"{lo}" if lo == hi else f"{lo}-{hi}"
    if kind == "proxies":
        return text or "none"      # none, free, file, or a path to a proxy file
    raise ValueError("unknown setting")


def length_range(cfg: dict) -> tuple[int, int]:
    lo, _, hi = cfg["length"].partition("-")
    return int(lo), int(hi or lo)


def normalise(cfg: dict) -> dict:
    """Make the combined settings consistent (a path in `proxies` means 'use this file')."""
    if cfg["proxies"].lower() in ("none", "free", "file"):
        cfg["proxies"] = cfg["proxies"].lower()
    else:
        cfg["proxy_file"], cfg["proxies"] = cfg["proxies"], "file"
    if cfg["proxies"] == "file" and not cfg["proxy_file"]:
        cfg["proxy_file"] = "proxies.txt"
    if cfg["pattern"]:
        sample = "".join("a" if c in "?@" else "1" if c == "#" else c for c in cfg["pattern"].lower())
        if not NAME_RE.match(sample):
            raise ValueError("pattern: names must be 2-32 characters from a-z, 0-9, . and _")
    if cfg["chars"] and not re.fullmatch(r"[a-z0-9._]+", cfg["chars"].lower()):
        raise ValueError("chars: only a-z, 0-9, . and _ are allowed")
    return cfg


def load_config() -> dict:
    cfg = defaults()
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    for key, value in saved.items():
        if key in SCHEMA:
            try:
                cfg[key] = coerce(key, value)
            except ValueError as e:
                print(f"Ignoring saved setting '{key}': {e}")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def describe(cfg: dict) -> str:
    if cfg["names_file"]:
        what = f"names from {cfg['names_file']}"
    elif cfg["pattern"]:
        what = f"names like {cfg['pattern']}"
    else:
        what = f"{cfg['length']}-character names, " + (f"from '{cfg['chars']}'" if cfg["chars"] else KINDS[cfg["kind"]])
    delay = cfg["delay"] or SPEEDS[cfg["speed"]]
    proxies = {"none": "no proxies", "free": "free proxies"}.get(cfg["proxies"], "your proxy file")
    more = f" x{cfg['workers']} workers" if cfg["workers"] > 1 else ""
    return f"{what} | one check every {delay:g}s{more} | {proxies}"


def file_in_home(name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else HOME / p


# ---------------------------------------------------------------------------
# Small file helpers
# ---------------------------------------------------------------------------


def load_set(path: Path) -> set[str]:
    try:
        return {ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def append_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def why_invalid(name: str) -> str | None:
    if not 2 <= len(name) <= 32:
        return "usernames are 2 to 32 characters long"
    if not re.fullmatch(r"[a-z0-9._]+", name):
        return "only letters a-z, numbers, '.' and '_' are allowed"
    if ".." in name:
        return "two '.' in a row are not allowed"
    return None


# ---------------------------------------------------------------------------
# Setup wizard
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


def ask_setting(key: str, cfg: dict) -> None:
    spec = SCHEMA[key]
    print(f"\n{key}: {spec['help']}")
    while True:
        shown = "on" if cfg[key] is True else "off" if cfg[key] is False else cfg[key]
        raw = input(f"  [{shown}]: ").strip()
        if not raw:
            return
        try:
            cfg[key] = coerce(key, raw)
            return
        except ValueError as e:
            print(f"  {e}. Try again, or press Enter to keep the current value.")


def wizard(cfg: dict) -> dict:
    pick = choose("What kind of usernames do you want?", [
        "4 characters, letters + numbers mixed (like a1b2)",
        "3 letters (like abc)",
        "4 letters (like abcd)",
        "4 numbers (like 1234)",
        "Names from my own file",
        "Custom (length, type, pattern...)",
    ])
    cfg["names_file"] = cfg["pattern"] = cfg["chars"] = ""
    presets = {1: ("4", "mix"), 2: ("3", "letters"), 3: ("4", "letters"), 4: ("4", "digits")}
    if pick in presets:
        cfg["length"], cfg["kind"] = presets[pick]
    elif pick == 5:
        cfg["names_file"] = input("\nPath to your file (one name per line) [names.txt]: ").strip() or "names.txt"
    else:
        for key in ("length", "kind", "chars", "pattern"):
            ask_setting(key, cfg)

    cfg["speed"], cfg["delay"] = ["safe", "normal", "fast"][choose("How fast?", [
        "Safe: one check every 2 seconds, least likely to get blocked",
        "Normal: one check every 1.2 seconds",
        "Fast: one check every 0.6 seconds (works best with your own proxies)",
    ]) - 1], 0.0

    p = choose("Proxies?", [
        "No proxies (simple; if Discord slows you down, the tool just waits)",
        "Free public proxies (slow to set up, unreliable, but can go faster)",
        "My own proxy file (one host:port per line)",
    ])
    cfg["proxies"] = ["none", "free", "file"][p - 1]
    if p == 3:
        cfg["proxy_file"] = input("\nPath to your proxy file [proxies.txt]: ").strip() or "proxies.txt"

    cfg["webhook"] = input("\nDiscord webhook URL for notifications (press Enter to skip): ").strip()
    if input("\nCustomize more options (workers, stop conditions, patterns, webhook mode...)? [y/N]: ").strip().lower() == "y":
        print("\nPress Enter to keep each value.")
        for key in SCHEMA:
            if key not in QUICK_KEYS:
                ask_setting(key, cfg)
    try:
        normalise(cfg)
    except ValueError as e:
        sys.exit(f"Not saved: {e}\nRun  python checker.py --setup  again and adjust that setting.")
    save_config(cfg)
    print("\nSaved. Next time just run:  python checker.py\n")
    return cfg


# ---------------------------------------------------------------------------
# Name generation
# ---------------------------------------------------------------------------


def alphabet_for(cfg: dict) -> str:
    if cfg["chars"]:
        return "".join(dict.fromkeys(cfg["chars"].lower()))
    return {"letters": LETTERS, "digits": DIGITS}.get(cfg["kind"], LETTERS + DIGITS)


def gen_names(count: int, cfg: dict, skip: set[str]) -> list[str]:
    alphabet = alphabet_for(cfg)
    lo, hi = length_range(cfg)
    pattern = cfg["pattern"].lower()
    need_mix = cfg["kind"] == "mix" and not cfg["chars"] and not pattern
    rng = random.Random()
    out: set[str] = set()
    tries = 0
    while len(out) < count and tries < count * 400:
        tries += 1
        if pattern:
            name = "".join(rng.choice(LETTERS) if c == "@" else rng.choice(DIGITS) if c == "#"
                           else rng.choice(alphabet) if c == "?" else c for c in pattern)
        else:
            name = "".join(rng.choice(alphabet) for _ in range(rng.randint(lo, hi)))
        if need_mix and (name.isalpha() or name.isdigit()):
            continue
        if ".." in name or name in skip:
            continue
        out.add(name)
    return list(out)


def file_names(path: str, skip: set[str]) -> tuple[list[str], int]:
    """Valid, unchecked names from a file, and how many lines were not valid names."""
    try:
        lines = file_in_home(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        sys.exit(f"Could not read {path}: {e.strerror}")
    names, invalid, seen = [], 0, set()
    for line in lines:
        name = line.strip().lower()
        if not name:
            continue
        if why_invalid(name):
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
    """A list of proxies; each worker uses its own slot, and a proxy that keeps failing is dropped."""

    def __init__(self, items: list[str], strikes: int):
        self.items, self.limit, self.shift = items, strikes, 0
        self.strikes: dict[str, int] = {}
        self.total = len(items)

    @property
    def active(self) -> bool:
        return bool(self.items)

    def current(self, slot: int = 0) -> str | None:
        return self.items[(self.shift + slot) % len(self.items)] if self.items else None

    def good(self, proxy: str | None) -> None:
        if proxy:
            self.strikes.pop(proxy, None)

    def bad(self, proxy: str | None, slot: int = 0) -> bool:
        """Count a failure and move this slot on. Returns True if the proxy was dropped."""
        if not self.items or proxy not in self.items:
            return False
        self.strikes[proxy] = self.strikes.get(proxy, 0) + 1
        if self.strikes[proxy] >= self.limit:
            self.items.remove(proxy)
            self.strikes.pop(proxy, None)
            return True
        self.shift += 1
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
    sample = random.sample(sorted(raw), min(len(raw), 4000))
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


async def setup_proxies(cfg: dict, session: aiohttp.ClientSession, say) -> Proxies:
    items: list[str] = []
    if cfg["proxies"] == "file":
        path = file_in_home(cfg["proxy_file"])
        if path.exists():
            items = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if PROXY_RE.match(ln.strip())]
        if not items:
            say(f"No usable proxies in {path}; continuing without proxies.")
    elif cfg["proxies"] == "free":
        items = await scrape_free_proxies(session, say)
        if not items:
            say("No working free proxies found; continuing without proxies (slower, but it works).")
    return Proxies(items, cfg["proxy_strikes"])


# ---------------------------------------------------------------------------
# Display and notifications
# ---------------------------------------------------------------------------


class Screen:
    def __init__(self, verbose: bool):
        self.verbose, self.width = verbose, 0

    def say(self, text: str) -> None:
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
        print("\r" + text + " " * max(self.width - len(text), 0), end="", flush=True)
        self.width = len(text)


class Reporter:
    """Throttled Discord webhook sender. Which results it sends depends on webhook_mode."""

    GAP = 0.45     # stays under Discord's 5 messages / 2 seconds

    def __init__(self, session: aiohttp.ClientSession, cfg: dict):
        self.session, self.mode = session, cfg["webhook_mode"]
        self.hit_url = cfg["webhook"] or None
        self.taken_url = cfg["webhook_fail"] or cfg["webhook"] or None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.last = 0.0
        self.enabled = self.mode != "off" and bool(self.hit_url or self.taken_url)
        self.task = asyncio.create_task(self._worker()) if self.enabled else None

    async def _post(self, url: str, payload: dict) -> None:
        for _ in range(2):
            try:
                async with self.session.post(url, json=payload) as resp:
                    if resp.status != 429:
                        return
                    await asyncio.sleep(float((await resp.json(content_type=None)).get("retry_after", 2)))
            except Exception:
                return

    async def _worker(self) -> None:
        while True:
            url, payload = await self.queue.get()
            wait = self.last + self.GAP - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            await self._post(url, payload)
            self.last = time.monotonic()
            self.queue.task_done()

    def report(self, name: str, status: str, proxy: str | None) -> None:
        if not self.enabled:
            return
        if status == "free" and self.mode in ("hits", "all"):
            url, title, color, desc = self.hit_url, "AVAILABLE", 0x30D158, f"**{name}** is free. Go claim it!"
        elif status == "taken" and self.mode in ("taken", "all"):
            url, title, color, desc = self.taken_url, "Taken", 0xFF453A, f"**{name}** is already claimed."
        else:
            return
        if not url:
            return
        self.queue.put_nowait((url, {"embeds": [{
            "title": title, "description": desc, "color": color,
            "fields": [{"name": "Username", "value": name, "inline": True},
                       {"name": "Proxy", "value": f"`{proxy or 'none'}`", "inline": True}],
            "footer": {"text": "Discord Username Checker"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        }]}))

    async def finish(self) -> None:
        if self.task:
            try:
                await asyncio.wait_for(self.queue.join(), 15)
            except asyncio.TimeoutError:
                pass
            self.task.cancel()


# ---------------------------------------------------------------------------
# Looking up one name (shared by the hunt and --check)
# ---------------------------------------------------------------------------


async def lookup(session, pool: Proxies, slot: int, name: str, cfg: dict, stats: dict,
                 screen: Screen, waits: dict, refresh) -> tuple[str, str | None]:
    """Ask Discord about one name, retrying through errors and rate limits.
    Returns (status, proxy_used); status is 'free', 'taken' or 'gave_up'."""
    attempt = 0
    while True:
        attempt += 1
        proxy = pool.current(slot)
        status, wait = await check(session, name, proxy)
        if status in ("free", "taken"):
            pool.good(proxy)
            return status, proxy
        stats["retries"] += 1
        if pool.active:
            dropped = pool.bad(proxy, slot)
            screen.debug(f"{status} via {proxy}" + (" (proxy dropped)" if dropped else ""))
            if not pool.active:
                screen.say("All proxies failed; continuing without proxies (slower).")
                attempt = 0
            continue
        if status == "limited":
            stats["limited"] += 1
            waits[slot] = min(max(wait, 3.0), 60.0)
            while waits[slot] > 0:
                refresh()
                await asyncio.sleep(1)
                waits[slot] -= 1
            waits[slot] = 0.0
        else:
            await asyncio.sleep(min(2 * attempt, 10))
        if attempt >= cfg["max_attempts"]:
            return "gave_up", proxy


def new_stats() -> dict:
    return {"checked": 0, "free": 0, "hits": [], "limited": 0, "retries": 0, "skipped": 0, "invalid_noted": False}


# ---------------------------------------------------------------------------
# The hunt
# ---------------------------------------------------------------------------


async def hunt(cfg: dict, screen: Screen, stats: dict) -> None:
    delay = cfg["delay"] or SPEEDS[cfg["speed"]]
    checked_path, hits_path = file_in_home(cfg["checked_file"]), file_in_home(cfg["hits_file"])
    checked = load_set(checked_path)
    skipped: set[str] = set()
    inflight: set[str] = set()
    batch: list[str] = []
    waits: dict[int, float] = {}
    screen.say(f"{len(checked):,} usernames already checked" + (" (resuming)" if checked else ""))

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), trust_env=False) as session:
        pool = await setup_proxies(cfg, session, screen.say)
        reporter = Reporter(session, cfg)
        if cfg["workers"] > 1 and not pool.active:
            screen.say(f"Note: {cfg['workers']} workers will share your own connection, so expect rate limits. "
                       "Proxies make extra workers worthwhile.")
        limits = []
        if cfg["stop_after_hits"]:
            limits.append(f"after {cfg['stop_after_hits']} free name(s)")
        if cfg["max_checks"]:
            limits.append(f"after {cfg['max_checks']:,} checks")
        screen.say(f"Running: {describe(cfg)}\nPress Ctrl+C any time to stop; progress is saved."
                   + (f" Stops automatically {' or '.join(limits)}." if limits else "") + "\n")
        started = time.monotonic()
        state = {"stop": False, "exhausted": False}

        def refresh(last: str = "") -> None:
            elapsed = time.monotonic() - started
            rate = f"{stats['checked'] / (elapsed / 60):.0f}/min" if elapsed >= 10 else "--/min"
            proxy_part = f" | proxies {len(pool.items)}/{pool.total}" if pool.total else ""
            wait = max(waits.values(), default=0)
            wait_part = f" | rate-limited, waiting {wait:.0f}s" if wait else ""
            screen.live(f"  checked {stats['checked']:,} | free {stats['free']} | {rate}{proxy_part}{wait_part} {last}")

        def limits_reached() -> bool:
            return bool((cfg["stop_after_hits"] and stats["free"] >= cfg["stop_after_hits"]) or
                        (cfg["max_checks"] and stats["checked"] + len(inflight) >= cfg["max_checks"]))

        def next_name() -> str | None:
            nonlocal batch
            if state["stop"] or limits_reached():
                return None
            if not batch:
                taken = checked | skipped | inflight
                if cfg["names_file"]:
                    names, invalid = file_names(cfg["names_file"], taken)
                    if invalid and not stats["invalid_noted"]:
                        stats["invalid_noted"] = True
                        screen.say(f"Ignoring {invalid} lines in {cfg['names_file']} that are not valid usernames.")
                    batch = names[:500]
                else:
                    batch = gen_names(500, cfg, taken)
                if not batch:
                    state["exhausted"] = True
                    return None
            return batch.pop()

        async def worker(slot: int) -> None:
            while (name := next_name()) is not None:
                inflight.add(name)
                try:
                    status, proxy = await lookup(session, pool, slot, name, cfg, stats, screen, waits, refresh)
                finally:
                    inflight.discard(name)
                if status == "gave_up":
                    skipped.add(name)       # never mark a name as checked without a real answer
                    stats["skipped"] += 1
                    screen.debug(f"gave up on {name} (no answer); it will be retried next run")
                    continue
                checked.add(name)
                append_line(checked_path, name)
                stats["checked"] += 1
                if status == "free":
                    stats["free"] += 1
                    stats["hits"].append(name)
                    append_line(hits_path, name)
                    screen.say("\n" + "*" * 46 + f"\n   AVAILABLE: {name}   (saved to {hits_path.name})\n" + "*" * 46 + "\n")
                    if cfg["beep"]:
                        print("\a", end="", flush=True)
                reporter.report(name, status, proxy)
                refresh(f"| last: {name}")
                await asyncio.sleep(delay)

        try:
            await asyncio.gather(*(worker(i) for i in range(cfg["workers"])))
        finally:
            await reporter.finish()
        if state["exhausted"]:
            screen.say("No new usernames left to check. Done!")
        elif limits_reached():
            screen.say("Reached your stop limit. Done!")


def summary(stats: dict, started: float, cfg: dict) -> None:
    mins = (time.monotonic() - started) / 60
    print(f"\n\n--- Summary ---\n  Checked this session: {stats['checked']:,} in {mins:.1f} min")
    print(f"  Free names found: {stats['free']}" + (f"  ->  {', '.join(stats['hits'])}" if stats["hits"] else ""))
    if stats["free"]:
        print(f"  (also saved in {cfg['hits_file']})")
    if stats["skipped"]:
        print(f"  Skipped {stats['skipped']} names Discord never answered for (they will be retried next run).")
    if stats["limited"]:
        print(f"  Rate-limited {stats['limited']} times; try a slower speed, fewer workers, or your own proxies.")


# ---------------------------------------------------------------------------
# Checking specific names (--check)
# ---------------------------------------------------------------------------


async def check_names(cfg: dict, names: list[str], screen: Screen) -> int:
    """Check the given names (or ask for them one at a time). Returns the exit code."""
    stats, waits = new_stats(), {}
    checked_path, hits_path = file_in_home(cfg["checked_file"]), file_in_home(cfg["hits_file"])
    problems = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12), trust_env=False) as session:
        pool = await setup_proxies(cfg, session, screen.say)
        reporter = Reporter(session, cfg)

        async def one(raw: str) -> None:
            nonlocal problems
            name = raw.strip().lower().lstrip("@")
            if not name:
                return
            reason = why_invalid(name)
            if reason:
                print(f"  {name:<34} INVALID  ({reason})")
                problems += 1
                return
            status, proxy = await lookup(session, pool, 0, name, cfg, stats, screen, waits,
                                         lambda: screen.live(f"  {name}: rate-limited, waiting {max(waits.values(), default=0):.0f}s"))
            screen.clear()
            if status == "gave_up":
                print(f"  {name:<34} COULD NOT CHECK (no answer from Discord; try again in a minute)")
                problems += 1
                return
            append_line(checked_path, name)
            if status == "free":
                append_line(hits_path, name)
                print(f"  {name:<34} FREE      <- available! (saved to {hits_path.name})")
                if cfg["beep"]:
                    print("\a", end="", flush=True)
            else:
                print(f"  {name:<34} taken")
            reporter.report(name, status, proxy)

        try:
            if names:
                print()
                for i, n in enumerate(names):
                    await one(n)
                    if i < len(names) - 1:
                        await asyncio.sleep(cfg["delay"] or SPEEDS[cfg["speed"]])
            else:
                print("Type a username and press Enter to check it. Press Enter on an empty line to quit.\n")
                while True:
                    try:
                        raw = await asyncio.to_thread(input, "username> ")
                    except EOFError:
                        break
                    if not raw.strip():
                        break
                    await one(raw)
        finally:
            await reporter.finish()
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Find available Discord usernames. Run with no options to start.",
        epilog="Every setting below also lives in config.json. Flags change a single run; add --save to keep them.")
    ap.add_argument("--setup", action="store_true", help="guided setup: a few plain questions, saved for next time")
    ap.add_argument("--check", nargs="*", metavar="NAME", help="check specific usernames (no names = ask me one at a time)")
    ap.add_argument("--show", action="store_true", help="show every setting, its current value and its default")
    ap.add_argument("--save", action="store_true", help="save the flags you pass as your new defaults")
    ap.add_argument("--defaults", action="store_true", help="forget saved settings and go back to the built-in defaults")
    ap.add_argument("--reset", action="store_true", help="forget every checked username (asks first)")
    for key, spec in SCHEMA.items():
        flag = spec.get("flag", "--" + key.replace("_", "-"))
        if spec["type"] == "bool":
            ap.add_argument(flag, dest=key, nargs="?", const="on", metavar="on|off", help=spec["help"])
        else:
            ap.add_argument(flag, dest=key, metavar=spec.get("metavar", "VALUE"), help=f"{spec['help']} (default: {spec['default'] or 'none'})")
    return ap


def apply_flags(cfg: dict, args: argparse.Namespace) -> list[str]:
    errors = []
    for key in SCHEMA:
        raw = getattr(args, key, None)
        if raw is None:
            continue
        try:
            cfg[key] = coerce(key, raw)
        except ValueError as e:
            errors.append(f"--{SCHEMA[key].get('flag', key).lstrip('-').replace('_', '-')}: {e}")
    return errors


def show(cfg: dict) -> None:
    saved = CONFIG_FILE.exists()
    print(f"Settings ({'from config.json' if saved else 'built-in defaults; nothing saved yet'}):\n")
    for key, spec in SCHEMA.items():
        value, default = cfg[key], spec["default"]
        fmt = lambda v: "on" if v is True else "off" if v is False else (str(v) if str(v) != "" else "(empty)")
        flag = spec.get("flag", "--" + key.replace("_", "-"))
        mark = "" if value == default else f"   (default: {fmt(default)})"
        print(f"  {flag:<18} {fmt(value)}{mark}\n  {'':<18} {spec['help']}")
    print("\nChange one run with a flag; add --save to keep it. Guided changes: python checker.py --setup")


def main() -> None:
    args = build_parser().parse_args()
    print(f"\n  Discord Username Checker v{VERSION}\n")
    if args.reset:
        path = file_in_home(load_config()["checked_file"])
        if input(f"This forgets all {len(load_set(path)):,} checked names. Type YES to confirm: ").strip() == "YES":
            path.unlink(missing_ok=True)
            print("Done.")
        else:
            print("Cancelled; nothing changed.")
        return
    if args.defaults:
        if input("Go back to the built-in defaults? Type YES to confirm: ").strip() == "YES":
            CONFIG_FILE.unlink(missing_ok=True)
            print("Done. Settings are back to the defaults.")
        else:
            print("Cancelled; nothing changed.")
        return

    first_run = not CONFIG_FILE.exists()
    cfg = load_config()
    if args.setup:
        try:
            cfg = wizard(cfg)
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nSetup cancelled; nothing was saved.")
    errors = apply_flags(cfg, args)
    try:
        normalise(cfg)
    except ValueError as e:
        errors.append(str(e))
    if errors:
        sys.exit("Problem with your settings:\n  " + "\n  ".join(errors))
    if args.save:
        save_config(cfg)
        print(f"Saved your settings to {CONFIG_FILE.name}.\n")
    if args.show:
        show(cfg)
        return
    if first_run and not args.setup and not args.save and args.check is None:
        print("First run: starting with simple defaults. Run  python checker.py --setup  to change them,\n"
              "or  python checker.py --show  to see everything you can customize.\n")

    screen = Screen(cfg["verbose"])
    if args.check is not None:
        try:
            code = asyncio.run(check_names(cfg, args.check, screen))
        except KeyboardInterrupt:
            code = 130
        sys.exit(code)

    stats, started = new_stats(), time.monotonic()
    try:
        asyncio.run(hunt(cfg, screen, stats))
    except KeyboardInterrupt:
        pass
    finally:
        screen.clear()
        summary(stats, started, cfg)


if __name__ == "__main__":
    main()
