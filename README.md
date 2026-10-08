# ☁️ Discord Username Checker

Tokenless Discord username availability hunter. Finds free usernames by checking
Discord's public unique-username endpoint — **no token, no login, no captcha**.

> Built around hunting short (3–4 char) usernames. Pure-letter short names are
> effectively all taken, so the default generator targets 4-character mixes with
> at least one digit — where the actually-available names still live.

## ✨ Features

- **Tokenless** — uses Discord's public `username-attempt-unauthed` endpoint
- **Interactive wizard** — proxies, username style, webhooks, pacing
- **Resume support** — remembers every checked username in `checked.txt`;
  never checks the same name twice, even across restarts
- **Sticky proxy rotation** — rides one proxy until it's rate-limited, then
  moves to the next, round-robin back to the first after the last
- **Free proxy scraper** — pulls from multiple public lists and **validates
  each proxy against Discord before using it** (typically only ~0.1% pass)
- **Embed reports** — every check (success *and* fail) can be streamed to
  Discord webhooks as color-coded embeds, with the proxy used
- **Persistent config** — settings saved to `config.json`, reused next run

## 🚀 Quick start

```bash
pip install aiohttp
python checker.py
```

The wizard walks you through everything:

```
— checked usernames —
  * y) continue from where we left off (recommended)
    n) start over (wipes checked.txt)

— proxies —
  * 1) scrape free proxies + auto-validate (no setup)
    2) load from my own file (one host:port per line)
    3) no proxies (your own IP — expect rate limits)

— usernames —
  * 1) 4 characters, letters+digits, at least one digit (best odds)
    2) 3 letters (almost all taken — slow going)
    3) 4 letters (almost all taken)
    4) load from my own file (one username per line)

— webhooks —
  * 1) success only (free usernames)
    2) fails only (taken usernames)
    3) both — separate success and fail webhooks
    4) single webhook for everything
    5) no webhooks (console only)
```

## 📁 Files

| File | Purpose |
|------|---------|
| `checker.py` | the whole tool |
| `config.json` | saved wizard settings (auto-created) |
| `checked.txt` | every username ever checked — resume data |
| `hits.txt` | available usernames found |
| `proxies.txt` | validated proxy cache (auto-created) |

## 🌐 Proxy modes

| Mode | Speed | Notes |
|------|-------|-------|
| Free scrape + validate | slow but free | ~1 in 1000 free proxies actually works |
| Your own proxies | best | one `host:port` per line |
| No proxies (own IP) | ~1 check / 1.5s | Discord rate-limits quickly |

Free proxies are validated by making a real availability request through each
one — only proxies that answer correctly enter rotation.

## 📡 Webhook embeds

Every check can be reported:

- ✅ **green** — username is available
- ❌ **red** — username is taken
- 🟠 **orange** — notices (proxy switched, errors)

Embeds include the username, the proxy used, and a timestamp. Sends are
throttled to stay under Discord's webhook rate limit (5 msg / 2 s).

## ⚙️ How the checker works

Discord exposes an unauthenticated endpoint used by their signup page:

```
POST https://discord.com/api/v9/unique-username/username-attempt-unauthed
{"username": "candidate"}   ->   {"taken": true|false}
```

That's it — no account needed. The tool never logs in, never touches any
account, and can't claim names for you; when it finds a free one, **you** go
claim it.

## ⚠️ Disclaimer

For educational purposes. Don't hammer Discord's API — the default 1.5s delay
exists for a reason. Respect rate limits and use responsibly.

## 📄 License

MIT
