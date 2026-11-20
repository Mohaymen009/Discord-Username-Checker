# Discord Username Checker

Find available Discord usernames. No token, no login, no captcha.

## Quick start

1. Install [Python](https://www.python.org/downloads/) (on Windows, tick **Add Python to PATH**).
2. Download this repo and **double-click `run.bat`** (Windows) or run `./run.sh` (Linux/macOS).
3. That's it. It starts checking right away. When it finds a free name you'll see:

```
**********************************************
   AVAILABLE: a1b2   (saved to hits.txt)
**********************************************
```

Press **Ctrl+C** any time to stop. You get a summary, and progress is saved, so the next run
picks up where you left off and never re-checks a name.

On the first run it uses simple defaults: 4-character names (letters and numbers mixed), a
safe speed, no proxies. You don't need to answer any questions.

## Change the settings

```
python checker.py --setup
```

Four plain questions: what kind of names, how fast, proxies or not, and an optional
Discord webhook to ping you when a name is free. Your answers are saved, so afterwards you
just run it normally.

Prefer options? Everything has a flag:

| Flag | What it does |
|------|--------------|
| `--length 3` | username length, 2 to 6 |
| `--type letters` | `letters`, `digits`, `mix` (both) or `any` |
| `--speed normal` | `safe` (default, 1 check / 2s), `normal` (1.2s) or `fast` (0.6s) |
| `--names file.txt` | check names from your own file, one per line |
| `--proxies none\|free\|FILE` | no proxies (default), free public ones, or your own file |
| `--webhook URL` | ping a Discord webhook when a name is free |
| `--verbose` | show detailed retry and proxy messages |
| `--reset` | forget every checked name (asks you to type YES first) |

## What you see

One live line that updates in place:

```
  checked 1,234 | free 2 | 28/min | last: ab12
```

If Discord slows you down it says so and waits ("rate-limited, waiting 5s"), then carries on.

## Good to know

- **Short names are nearly all taken.** 3-letter names are almost gone. Mixed letter/number
  names (the default) are where free ones still turn up.
- **Speed vs. getting blocked.** Safe speed is the least likely to be rate-limited. Going
  faster works best with your own proxies.
- **Proxies are optional.** Free public proxies are slow to set up and mostly don't work, so
  they're off by default. If every proxy fails, the tool tells you and keeps going without them.
- **A name is only marked as checked when Discord actually answered.** If Discord is
  unreachable or errors, the name is retried (or skipped and tried again next run), never lost.
- It can't claim names for you. When it finds a free one, you go claim it yourself.

## Files it creates

| File | What it is |
|------|------------|
| `checked.txt` | every name checked so far (so it can resume) |
| `hits.txt` | the free names it found |
| `config.json` | your saved settings (may contain a webhook URL, kept out of git) |
| `proxies.txt` | your own proxy list, if you use one |

## How it works

Discord's sign-up page asks an unauthenticated endpoint whether a username is taken:

```
POST https://discord.com/api/v9/unique-username/username-attempt-unauthed
{"username": "candidate"}   ->   {"taken": true|false}
```

The tool just asks that question for lots of names, politely.

## Disclaimer

For educational purposes. Don't hammer Discord's API; the default pace exists for a reason.
Respect rate limits and use responsibly.

## License

MIT
