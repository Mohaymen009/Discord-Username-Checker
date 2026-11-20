# Discord Username Checker

Find available Discord usernames, or check specific ones. No token, no login, no captcha.

## Quick start

1. Install [Python](https://www.python.org/downloads/) (on Windows, tick **Add Python to PATH**).
2. Download this repo and **double-click `run.bat`** (Windows) or run `./run.sh` (Linux/macOS).
3. It starts hunting right away. When it finds a free name you'll see:

```
**********************************************
   AVAILABLE: a1b2   (saved to hits.txt)
**********************************************
```

Press **Ctrl+C** any time to stop. You get a summary, and progress is saved, so the next run
picks up where you left off and never re-checks a name.

The first run uses simple defaults (4-character names, letters and numbers mixed, safe speed,
no proxies), so you don't have to answer anything. Everything below is optional.

## Check specific usernames

```
python checker.py --check cool.name abc123 ab12
python checker.py --check              # then type names one at a time
```

```
  cool.name                          taken
  abc123                             FREE      <- available! (saved to hits.txt)
  a..b                               INVALID  (two '.' in a row are not allowed)
```

Names are lowercased, a leading `@` is ignored, and invalid names are explained instead of
sent to Discord. Free names are saved to `hits.txt` (and sent to your webhook if you set one).

## Customize everything

Every setting has a default, can be saved, and can be overridden for one run.

| Way | How |
|-----|-----|
| Guided | `python checker.py --setup` asks four plain questions, with an optional "more options" part |
| One run | add flags, e.g. `python checker.py --length 3 --type letters --speed fast` |
| Keep flags | add `--save` to store the flags you passed as your new defaults |
| See it all | `python checker.py --show` lists every setting, its value and its default |
| Edit by hand | change `config.json` (created by `--setup` or `--save`) |
| Start over | `python checker.py --defaults` forgets your saved settings |

Priority: flags for this run, then `config.json`, then the built-in defaults.

### Settings

| Flag (config key) | Default | What it does |
|---|---|---|
| `--length` | `4` | a number (`4`) or a range (`3-5`), 2 to 32 |
| `--type` (`kind`) | `mix` | `mix` (letters and numbers), `letters`, `digits` or `any` |
| `--chars` | none | your own alphabet instead of `--type`, e.g. `abc123_` |
| `--pattern` | none | a template: `?` any character, `#` a digit, `@` a letter, anything else is literal. `a@@#` gives names like `akq8` |
| `--names FILE` (`names_file`) | none | check names from a file (one per line) instead of generating |
| `--speed` | `safe` | `safe` = one check every 2s, `normal` = 1.2s, `fast` = 0.6s |
| `--delay` | `0` | exact seconds between checks per worker (overrides `--speed`) |
| `--workers` | `1` | checks running at once. More than 1 works best with proxies |
| `--proxies` | `none` | `none`, `free`, or the path to your own proxy file |
| `--proxy-file` | none | proxy file used when proxies is `file` |
| `--proxy-strikes` | `3` | failures before a proxy is dropped |
| `--webhook` | none | Discord webhook URL for notifications |
| `--webhook-fail` | none | separate webhook for taken names (default: same as `--webhook`) |
| `--webhook-mode` | `hits` | `off`, `hits` (free names), `taken` (taken names) or `all` (every check) |
| `--beep on` | `off` | play a sound when a free name is found |
| `--stop-after-hits N` | `0` | stop after N free names (0 = never) |
| `--max-checks N` | `0` | stop after N checks (0 = never) |
| `--hits-file` | `hits.txt` | where free names are saved |
| `--checked-file` | `checked.txt` | where checked names are remembered |
| `--max-attempts` | `6` | tries per name before it is skipped |
| `--verbose on` | `off` | show detailed retry and proxy messages |

Other commands: `--reset` forgets every checked name (asks you to type YES first).

### Examples

```
python checker.py --pattern "a@@#" --stop-after-hits 3     # names like akq8, stop after 3 free ones
python checker.py --length 3-4 --type letters --max-checks 500
python checker.py --names my_ideas.txt --webhook URL --webhook-mode all
python checker.py --proxies my_proxies.txt --workers 5 --speed fast --save
```

## What you see

One live line that updates in place:

```
  checked 1,234 | free 2 | 28/min | last: ab12
```

If Discord slows you down it says so and waits ("rate-limited, waiting 5s"), then carries on.

## Good to know

- **Short names are nearly all taken.** 3-letter names are almost gone. Mixed letter/number
  names (the default) are where free ones still turn up.
- **Speed vs. getting blocked.** Safe speed is the least likely to be rate-limited. More
  speed or more workers work best with your own proxies; extra workers on one connection just
  share the same limit.
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
