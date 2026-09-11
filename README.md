# Zara Watcher

Telegram alerts when a size comes back, runs low, or drops in price — across the country stores you pick.

Runs on GitHub Actions. Free, no server.

---

## Setup

**1. Telegram bot**

1. @BotFather → `/newbot` → copy the token
2. Open your bot, press Start, send it anything
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → find `"chat":{"id":123456789`

**2. GitHub**

1. Push these files to a repo (private is fine)
2. Settings → Secrets → Actions → add `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`
3. Settings → Actions → General → **Read and write permissions**
4. Actions tab → enable → Run workflow once
5. Send your bot `/help`

Set `TELEGRAM_CHAT_ID` — without it anyone who finds the bot can edit your list.

---

## Using it

Send `/new`. You get a form with a copy button:

```
Link: 
Sizes: 
Countries: es, de, pl
Alerts: restock, low, price
Every: 30m
```

Fill it in, send it back. Blank lines use your defaults.

**Shortcuts** — a one-liner works too, order doesn't matter:

```
https://zara.com/es/es/...-p03253303.html  M L  es de  30m
```

Or paste a bare link and everything falls back to defaults.

**Alerts field:** any mix of `restock`, `low`, `price`. Add `15%` to change the price threshold for that item.

### Commands

| | |
|---|---|
| `/new` | blank form |
| `/list` | everything you're watching |
| `/del 3` `/pause 3` `/resume 3` | manage by number |
| `/sale 48` | fast checks for 48h, auto-reverts |
| `/defaults es de tr` | set your usual countries |
| `/status` | request volume, backoff |

### Alerts

- 🟢 **Back in stock**
- 🟡 **Running out** — in stock → low stock. Usually the most useful one
- 🔻 **Price drop** — past your threshold

When you watch several countries, alerts append the cheapest one. Free — those prices were already fetched.

---

## How the timing works

Nothing is automatic — Zara doesn't notify anyone. The script wakes every 15 min, looks, and tells you. So a size that reappears is found **within 15 minutes**, not instantly.

Per-item `Every: 30m` means it's skipped on ticks where it isn't due yet. Setting anything below 15m doesn't help, since the cron itself is the floor.

**Commands are read on the same schedule.** You send something, it lands at the next tick — so a confirmation can take up to 15 min. Alerts are never delayed; they fire on the tick that finds the change.

**Volume:** 5 items × 4 countries = 20 requests per sweep, ~80/hour, spaced 3s apart. Zara serves millions — this is nothing. A 403 or 429 doubles the delay automatically (up to ×8) and messages you once; 20 clean requests halve it back.

The real blocking risk is the datacenter IP, not your volume. If everything 403s from day one, that's Akamai flagging GitHub's IP range — move `watcher.py` to any always-on machine at home and run it from crontab instead. Same code:

```
*/15 * * * * cd ~/zara && python3 watcher.py
```

---

## Files

| | |
|---|---|
| `watcher.py` | main loop, commands, alerts |
| `commands.py` | parses the form and one-liners |
| `zara.py` | Zara endpoints, rate limiter, countries |
| `data.json` | your watchlist + last-seen state |

`data.json` is committed back each run, so your watchlist survives and the commit log becomes a free price history.

---

## Notes

- First check on a new item is silent — it's recording a baseline.
- A size won't re-alert until it goes out of stock and returns.
- Countries where the item isn't sold are skipped quietly.
- Unrecognised sizes or country codes are ignored with a warning, not a failure.
- Azerbaijan is excluded (franchise store, no online catalogue).
- If Zara renames their JSON fields you'll see `no sizes parsed` in the Actions log — `extract_sizes()` in `zara.py` walks the whole response rather than a fixed path, so it usually needs a one-line fix.
