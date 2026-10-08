#!/usr/bin/env python3
"""
Discord Username Checker — tokenless availability hunter.

Finds available Discord usernames by checking Discord's public
unique-username endpoint. No token, no login, no captcha.

Features:
  - Interactive setup wizard (resume, proxies, webhooks, pacing)
  - Username generator: 4-char letter/digit mixes, 3-letter, or your own file
  - Sticky proxy rotation: one proxy at a time, switch on rate-limit,
    round-robin back to the first after the last
  - Never checks the same username twice (checked.txt)
  - Every check (success AND fail) reported to Discord via embeds
  - Saves hits to hits.txt

Educational purposes only — don't spam Discord's API.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import string
import sys
import time
from pathlib import Path

try:
    import aiohttp
except ImportError:
    sys.exit("missing dependency: pip install aiohttp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
ENDPOINT = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"

CHECKED_FILE = "checked.txt"
HITS_FILE = "hits.txt"
CONFIG_FILE = "config.json"
PROXY_FILE_DEFAULT = "proxies.txt"

CONCURRENCY = 1                 # single worker -> single sticky proxy
DEFAULT_DELAY = 1.5             # seconds between checks
MIN_WEBHOOK_GAP = 0.45          # ~2.2 embeds/s, under Discord's 5/2s limit

LETTERS = string.ascii_lowercase
DIGITS = string.digits
ALL_CHARS = LETTERS + DIGITS

GREEN, RED, ORANGE, GRAY = 0x30D158, 0xFF453A, 0xFF9F0A, 0x98989D

PROXY_SOURCES = [
    ("TheSpeedX", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("proxifly", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
    ("roosterkid", "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt"),
    ("openproxylist", "https://api.openproxylist.xyz/http.txt"),
    ("proxyscrape", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
]
PROXY_RE = re.compile(r"^(?:https?://)?(?:[^@\s]+@)?[a-zA-Z0-9](?:[a-zA-Z0-9\-.]*[a-zA-Z0-9])?:\d{1,5}$")

BANNER = f"""
  ┌─────────────────────────────────────────┐
  │   Discord Username Checker  v{VERSION}   │
   │   tokenless · async · embeds reports  │
    └───────────────────────────────────────┘
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def ask_choice(prompt: str, options: dict[str, str], default: str) -> str:
    """options maps key -> description. Returns the chosen key."""
    print(f"\n{prompt}")
    for key, desc in options.items():
        marker = "*" if key == default else " "
        print(f"  {marker} {key}) {desc}")
    while True:
        val = input(f"choice [{default}]: ").strip().lower() or default
        if val in options:
            return val
        print("  invalid choice, try again")


def ask_int(prompt: str, default: float) -> float:
    val = ask(prompt, str(default))
    try:
        return max(0.1, float(val))
    except ValueError:
        return default


def load_set(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def append_line(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    p = Path(CONFIG_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    Path(CONFIG_FILE).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Proxies: scrape + validate
# ---------------------------------------------------------------------------

async def scrape_proxies(session: aiohttp.ClientSession) -> list[str]:
    print("\nscraping free proxy sources...")
    raw: set[str] = set()

    async def fetch(name: str, url: str) -> None:
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"  x {name} (http {resp.status})")
                    return
                found = [p.strip() for p in (await resp.text()).splitlines()
                         if PROXY_RE.match(p.strip())]
                raw.update(found)
                print(f"  + {name}: {len(found)} proxies")
        except Exception as e:
            print(f"  x {name} ({type(e).__name__})")

    await asyncio.gather(*(fetch(n, u) for n, u in PROXY_SOURCES))
    print(f"  = {len(raw)} unique raw proxies")
    return sorted(raw)


async def validate_proxies(session: aiohttp.ClientSession, proxies: list[str],
                           concurrency: int = 300, timeout: float = 10.0) -> list[str]:
    """Test each proxy against the real endpoint; keep only working ones."""
    print(f"validating {len(proxies)} proxies against Discord...")
    sem = asyncio.Semaphore(concurrency)
    working: list[str] = []
    done = 0

    async def test(proxy: str) -> None:
        nonlocal done
        async with sem:
            try:
                async with session.post(
                    ENDPOINT, json={"username": "aa"}, proxy=proxy,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200 and "taken" in await resp.text():
                        working.append(proxy)
            except Exception:
                pass
            done += 1
            if done % 1000 == 0:
                print(f"  tested {done}/{len(proxies)}, {len(working)} working")

    await asyncio.gather(*(test(p) for p in proxies))
    return working


# ---------------------------------------------------------------------------
# Username generator
# ---------------------------------------------------------------------------

def gen_names(count: int, mode: str, checked: set[str]) -> list[str]:
    """mode: '4digit' (4 chars, >=1 digit), '3letter' (3 letters), '4letter'."""
    rng = random.Random()
    out: set[str] = set()
    while len(out) < count:
        if mode == "4digit":
            name = "".join(rng.choice(ALL_CHARS) for _ in range(4))
            if not any(c in DIGITS for c in name):
                continue  # pure letters are effectively all taken
        elif mode == "3letter":
            name = "".join(rng.choice(LETTERS) for _ in range(3))
        else:
            name = "".join(rng.choice(LETTERS) for _ in range(4))
        if name not in checked:
            out.add(name)
    return list(out)


# ---------------------------------------------------------------------------
# Webhook reporter: throttled embed queue
# ---------------------------------------------------------------------------

class Reporter:
    def __init__(self, session: aiohttp.ClientSession,
                 success_url: str | None, fail_url: str | None):
        self.session = session
        self.urls = {"success": success_url, "fail": fail_url}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.last_sent = 0.0
        if success_url or fail_url:
            self.task = asyncio.create_task(self._worker())

    async def _post(self, url: str, payload: dict) -> None:
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 429:
                    data = await resp.json(content_type=None)
                    await asyncio.sleep(float(data.get("retry_after", 2)))
                    await self.session.post(url, json=payload)
        except Exception:
            pass

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            wait = self.last_sent + MIN_WEBHOOK_GAP - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            for url in item["urls"]:
                await self._post(url, {"embeds": [item["embed"]]})
            self.last_sent = time.monotonic()
            self.queue.task_done()

    def report(self, name: str, status: str, proxy: str | None) -> None:
        if status == "free":
            color, title, desc = GREEN, "AVAILABLE", f"**{name}** is free — claim it!"
        elif status == "taken":
            color, title, desc = RED, "Taken", f"**{name}** is already claimed."
        else:
            color, title, desc = ORANGE, "Notice", f"**{name}** — {status}"
        url = self.urls["success" if status == "free" else "fail"]
        if not url:
            return
        self.queue.put_nowait({
            "urls": [url],
            "embed": {
                "title": title,
                "description": desc,
                "color": color,
                "fields": [
                    {"name": "Username", "value": name, "inline": True},
                    {"name": "Proxy", "value": f"`{proxy or 'none'}`", "inline": True},
                ],
                "footer": {"text": "Discord Username Checker"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            },
        })


# ---------------------------------------------------------------------------
# Engine: sticky round-robin proxy hunter
# ---------------------------------------------------------------------------

async def hunt(cfg: dict) -> None:
    delay = cfg["delay"]
    mode = cfg["name_mode"]
    names_file = cfg.get("names_file", "")
    batch_size = 500

    checked = load_set(CHECKED_FILE)
    print(f"\n{len(checked)} usernames already checked"
          f"{' (resuming)' if checked else ''}")

    proxies = [ln.strip() for ln in Path(cfg["proxy_file"]).read_text().splitlines()
               if ln.strip()] if cfg.get("proxy_file") else []

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=12), trust_env=False,
    ) as session:
        reporter = Reporter(session, cfg.get("webhook_success"), cfg.get("webhook_fail"))
        work: asyncio.Queue = asyncio.Queue()
        hits_file = HITS_FILE
        lock = asyncio.Lock()
        stats = {"checked": 0, "free": 0}
        proxy_idx = 0
        cur_proxy: str | None = None
        refill = asyncio.Event()
        refill.set()  # need names immediately

        def current_proxy() -> str | None:
            nonlocal cur_proxy
            if not proxies:
                return None
            if cur_proxy is None:
                cur_proxy = proxies[proxy_idx]
            return cur_proxy

        def switch_proxy(burned: str | None) -> None:
            nonlocal proxy_idx, cur_proxy
            if not proxies:
                return
            proxy_idx = (proxy_idx + 1) % len(proxies)
            cur_proxy = None
            print(f"  rate-limited {burned or 'proxy'} -> switching to #{proxy_idx}")

        async def worker() -> None:
            nonlocal proxy_idx
            while True:
                if refill.is_set():
                    if names_file:
                        pool = load_set(names_file) - checked
                        batch = list(pool)[:batch_size]
                    else:
                        batch = gen_names(batch_size, mode, checked)
                    if not batch:
                        print("no new usernames to check — done.")
                        await asyncio.sleep(5)
                        continue
                    for n in batch:
                        work.put_nowait(n)
                    refill.clear()

                name = await work.get()
                proxy = current_proxy()
                status = "taken"
                try:
                    async with session.post(
                        ENDPOINT, json={"username": name}, proxy=proxy,
                        headers={"Content-Type": "application/json"},
                    ) as resp:
                        if resp.status == 200:
                            if '"taken":false' in await resp.text():
                                status = "free"
                                stats["free"] += 1
                                append_line(hits_file, name)
                                print(f"\n  *** FREE: {name} *** (saved to {hits_file})\n")
                        elif resp.status == 429:
                            switch_proxy(proxy)
                            work.put_nowait(name)   # re-check on next proxy
                            work.task_done()
                            continue
                        else:
                            status = f"http {resp.status}"
                except Exception:
                    status = "connection error"

                reporter.report(name, status, proxy)
                async with lock:
                    checked.add(name)
                    append_line(CHECKED_FILE, name)
                stats["checked"] += 1
                if stats["checked"] % 25 == 0:
                    print(f"  {stats['checked']} checked this session, "
                          f"{stats['free']} free")
                work.task_done()
                await asyncio.sleep(delay)
                if work.empty():
                    refill.set()

        print(f"\nhunting {mode} usernames — ctrl+c to stop\n")
        try:
            await worker()
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def wizard() -> dict:
    cfg = load_config()
    if cfg:
        reuse = ask_choice(
            f"found saved settings ({cfg.get('name_mode', '?')}, delay {cfg.get('delay', '?')}s) — use them?",
            {"y": "use saved settings", "n": "run setup again"}, "y")
        if reuse == "y":
            return cfg

    print("\n— checked usernames —")
    resume = "n"
    if Path(CHECKED_FILE).exists() and load_set(CHECKED_FILE):
        resume = ask_choice(
            f"{len(load_set(CHECKED_FILE))} usernames already checked",
            {"y": "continue from where we left off (recommended)",
             "n": "start over (wipes checked.txt)"}, "y")
        if resume == "n":
            Path(CHECKED_FILE).unlink()
            print("  checked.txt wiped")

    print("\n— proxies —")
    proxy_mode = ask_choice(
        "how do you want to get proxies?",
        {"1": "scrape free proxies + auto-validate (no setup)",
         "2": "load from my own file (one host:port per line)",
         "3": "no proxies (your own IP — expect rate limits)"},
        "1")
    proxy_file = ""
    if proxy_mode == "2":
        proxy_file = ask("proxy file path", PROXY_FILE_DEFAULT)
        if not Path(proxy_file).exists():
            print(f"  '{proxy_file}' not found — falling back to free scrape")
            proxy_mode = "1"

    print("\n— usernames —")
    name_mode = ask_choice(
        "what kind of usernames?",
        {"1": "4 characters, letters+digits, at least one digit (best odds)",
         "2": "3 letters (almost all taken — slow going)",
         "3": "4 letters (almost all taken)",
         "4": "load from my own file (one username per line)"},
        "1")
    names_file = ""
    if name_mode == "4":
        names_file = ask("usernames file path", "names.txt")

    print("\n— webhooks —")
    wh_mode = ask_choice(
        "what should get reported to Discord webhooks?",
        {"1": "success only (free usernames)",
         "2": "fails only (taken usernames)",
         "3": "both — separate success and fail webhooks",
         "4": "single webhook for everything",
         "5": "no webhooks (console only)"},
        "1")
    wh_success = wh_fail = None
    if wh_mode == "1":
        wh_success = ask("success webhook url")
    elif wh_mode == "2":
        wh_fail = ask("fail webhook url")
    elif wh_mode == "3":
        wh_success = ask("success webhook url")
        wh_fail = ask("fail webhook url")
    elif wh_mode == "4":
        one = ask("webhook url")
        wh_success = wh_fail = one

    delay = ask_int("delay between checks in seconds", DEFAULT_DELAY)

    cfg = {
        "name_mode": {"1": "4digit", "2": "3letter", "3": "4letter"}.get(name_mode, "file"),
        "names_file": names_file,
        "proxy_mode": proxy_mode,
        "proxy_file": proxy_file,
        "webhook_success": wh_success or None,
        "webhook_fail": wh_fail or None,
        "delay": delay,
    }
    save_config(cfg)
    print(f"\nsettings saved to {CONFIG_FILE}")
    return cfg


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print(BANNER)
    cfg = wizard()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15), trust_env=False,
    ) as session:
        # resolve proxies
        if cfg["proxy_mode"] == "1":
            path = Path(PROXY_FILE_DEFAULT)
            if path.exists() and load_set(PROXY_FILE_DEFAULT):
                reuse = ask_choice(
                    f"{len(load_set(PROXY_FILE_DEFAULT))} cached proxies in {PROXY_FILE_DEFAULT}",
                    {"y": "reuse them", "n": "scrape + validate fresh ones"}, "y")
            else:
                reuse = "n"
            if reuse == "n":
                raw = await scrape_proxies(session)
                good = await validate_proxies(session, raw)
                path.write_text("\n".join(good) + "\n")
                print(f"  {len(good)} working proxies saved to {path}")
            cfg["proxy_file"] = PROXY_FILE_DEFAULT
        elif cfg["proxy_mode"] == "3":
            cfg["proxy_file"] = ""

    await hunt(cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped. progress saved — run again to resume.")
