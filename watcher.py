#!/usr/bin/env python3
"""
watcher.py — runs on a schedule. Each run:
  1. reads any Telegram messages you sent since last time
  2. applies them (add / list / pause / delete / sale …)
  3. checks whichever items are due
  4. sends alerts
  5. saves data.json

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import requests

import zara
from commands import TEMPLATE, parse_item, parse_interval
from zara import AVAILABLE, COUNTRIES, IN_STOCK, LOW_STOCK

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
OWNER = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"

DATA = Path(__file__).parent / "data.json"
SALE_INTERVAL = 15

DEFAULT_DATA = {
    "items": [],
    "state": {},
    "offset": 0,
    "sale_until": 0,
    "next_id": 1,
    "defaults": {
        "countries": ["es", "de", "pl"],
        "sizes": ["*"],
        "alerts": {"t_restock": 1, "t_lowstock": 1, "t_price": 1},
        "interval_m": 30,
        "drop_pct": 5,
    },
}

HELP = f"""<b>Zara watcher</b>

Send /new for a blank form, or paste a link with details on one line.

<b>Commands</b>
/new — blank form to fill in
/list — everything you're watching
/del 3 · /pause 3 · /resume 3
/sale 48 — check everything fast for 48h
/nosale
/defaults — see or change your usual countries
/status

<b>Shortcuts</b>
Paste a bare Zara link → uses your defaults.
Or: <code>&lt;link&gt; M L es de 30m</code>
"""


# ---------------------------------------------------------------------------
def load():
    if DATA.exists():
        try:
            d = json.loads(DATA.read_text(encoding="utf-8"))
            for k, v in DEFAULT_DATA.items():
                d.setdefault(k, v)
            return d
        except Exception:
            traceback.print_exc()
    return json.loads(json.dumps(DEFAULT_DATA))


def save(d):
    DATA.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def send(text, preview=False):
    if not OWNER:
        print("[no chat id]\n" + text)
        return
    try:
        requests.post(f"{API}/sendMessage", json={
            "chat_id": OWNER, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": not preview}, timeout=30)
    except Exception as e:
        print(f"[tg] send failed: {e}")


def get_updates(offset):
    try:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset, "timeout": 0}, timeout=30)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[tg] getUpdates failed: {e}")
        return []


def find(d, item_id):
    for it in d["items"]:
        if it["id"] == item_id:
            return it
    return None


def describe(it, sale):
    what = []
    if it["t_restock"]: what.append("restock")
    if it["t_lowstock"]: what.append("low stock")
    if it["t_price"]: what.append(f"−{it['drop_pct']:g}%")
    sizes = "all sizes" if "*" in it["sizes"] else ", ".join(it["sizes"])
    eff = SALE_INTERVAL if sale else it["interval_m"]
    return (f"<b>#{it['id']} {it.get('name') or it.get('reference') or 'item'}</b>"
            f"{'' if it.get('active', 1) else '  ⏸'}\n"
            f"{sizes} · {' '.join(zara.flag(c) for c in it['countries'])}\n"
            f"{', '.join(what)} · every {eff}m")


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------
def handle_text(d, text):
    text = text.strip()
    low = text.lower()

    if low.startswith("/"):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()

        if cmd in ("/start", "/help"):
            send(HELP)
        elif cmd == "/new":
            df = d["defaults"]
            filled = TEMPLATE.replace(
                "Countries: ", "Countries: " + ", ".join(df["countries"]))
            send("Copy this, fill in the link and sizes, send it back:\n\n"
                 f"<pre>{filled}</pre>\n"
                 "Leave any line blank to use your defaults.")
        elif cmd == "/list":
            items = d["items"]
            if not items:
                send("Nothing watched yet. /new to add something.")
            else:
                sale = sale_active(d)
                send("\n\n".join(describe(it, sale) for it in items)
                     + "\n\n<i>/del N · /pause N · /resume N</i>")
        elif cmd in ("/del", "/delete", "/rm"):
            it = find(d, int(arg)) if arg.isdigit() else None
            if it:
                d["items"].remove(it)
                d["state"] = {k: v for k, v in d["state"].items()
                              if not k.startswith(f"{it['id']}|")}
                send(f"🗑 Deleted #{it['id']}.")
            else:
                send("Which one? e.g. /del 3")
        elif cmd in ("/pause", "/resume"):
            it = find(d, int(arg)) if arg.isdigit() else None
            if it:
                it["active"] = 0 if cmd == "/pause" else 1
                send(f"#{it['id']} {'paused' if not it['active'] else 'resumed'}.")
            else:
                send("Which one? e.g. /pause 3")
        elif cmd == "/sale":
            hours = 48
            try:
                hours = max(1, min(168, float(arg)))
            except ValueError:
                pass
            d["sale_until"] = time.time() + hours * 3600
            for it in d["items"]:
                it["next_check"] = 0
            send(f"🔥 Sale mode on for {hours:g}h — everything checks as often "
                 f"as the schedule allows. Reverts by itself.")
        elif cmd == "/nosale":
            d["sale_until"] = 0
            send("Sale mode off.")
        elif cmd == "/defaults":
            if arg:
                cc = [c.lower() for c in arg.replace(",", " ").split()
                      if c.lower() in COUNTRIES]
                if cc:
                    d["defaults"]["countries"] = cc
                    send("Default countries: "
                         + " ".join(f"{zara.flag(c)} {zara.country_name(c)}" for c in cc))
                else:
                    send("No valid country codes in that.")
            else:
                df = d["defaults"]
                send(f"<b>Defaults</b>\n"
                     f"Countries: {' '.join(zara.flag(c) for c in df['countries'])} "
                     f"({', '.join(df['countries'])})\n"
                     f"Every: {df['interval_m']}m · drop ≥{df['drop_pct']:g}%\n\n"
                     f"Change with e.g. <code>/defaults es de pl tr</code>")
        elif cmd == "/status":
            status(d)
        else:
            send("Unknown command. /help")
        return

    if "zara.com" not in low:
        send("Send /new for a form, or paste a Zara link.")
        return

    item, warn = parse_item(text, d["defaults"])
    if not item:
        send("⚠️ " + warn[0])
        return

    item["id"] = d["next_id"]
    d["next_id"] += 1
    item["active"] = 1
    item["next_check"] = 0
    item["resolved"] = {}
    item["name"] = None
    if item.get("home_pid"):
        item["resolved"][item["home_cc"]] = item["home_pid"]
    d["items"].append(item)

    msg = f"✅ Watching #{item['id']}\n\n" + describe(item, sale_active(d))
    if warn:
        msg += "\n\n⚠️ " + "\n".join(warn)
    msg += "\n\n<i>First check records a baseline — alerts start after that.</i>"
    send(msg)


def status(d):
    active = [i for i in d["items"] if i.get("active", 1)]
    reqs = sum(len(i["countries"]) for i in active)
    lines = ["<b>Status</b>",
             f"Items: {len(d['items'])} ({len(active)} active)",
             f"≈{reqs} requests per full sweep",
             f"Backoff: ×{zara.LIMITER.backoff:g}"]
    if sale_active(d):
        left = (d["sale_until"] - time.time()) / 3600
        lines.append(f"🔥 Sale mode: {left:.1f}h left")
    send("\n".join(lines))


def sale_active(d):
    return d.get("sale_until", 0) > time.time()


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------
def size_wanted(wanted, size):
    return "*" in wanted or size.strip().upper() in [w.strip().upper() for w in wanted]


def check_item(d, it):
    alerts, prices = [], {}

    for cc in it["countries"]:
        if cc not in COUNTRIES:
            continue
        _, lang, cname, fl = COUNTRIES[cc]

        pid = it["resolved"].get(cc)
        if not pid:
            if not it.get("reference"):
                continue
            pid, err = zara.resolve_id(it["reference"], cc, lang)
            if not pid:
                print(f"[chk] #{it['id']} {cc}: {err}")
                continue
            it["resolved"][cc] = pid

        detail, err = zara.fetch(pid, cc, lang)
        if err:
            print(f"[chk] #{it['id']} {cc}: {err}")
            continue

        now_sizes = {s["size"]: s["availability"] for s in detail["sizes"]}
        price = detail["price"]
        if detail.get("name"):
            it["name"] = detail["name"]
        if price is not None:
            prices[cc] = price

        key = f"{it['id']}|{cc}"
        prev = d["state"].get(key)
        link = zara.product_link(it["reference"], pid, cc, lang, it["url"])
        name = it.get("name") or "Zara item"

        if prev:
            ps = prev.get("sizes", {})

            if it["t_restock"]:
                back = [s for s, av in now_sizes.items()
                        if size_wanted(it["sizes"], s) and av in AVAILABLE
                        and s in ps and ps.get(s) not in AVAILABLE]
                if back:
                    alerts.append(f"🟢 <b>BACK IN STOCK</b> {fl} {cname}\n{name}\n"
                                  f"Size <b>{', '.join(back)}</b>\n"
                                  f"{zara.fmt_price(price, cc)}\n{link}")

            if it["t_lowstock"]:
                low = [s for s, av in now_sizes.items()
                       if size_wanted(it["sizes"], s) and av in LOW_STOCK
                       and ps.get(s) in IN_STOCK]
                if low:
                    alerts.append(f"🟡 <b>RUNNING OUT</b> {fl} {cname}\n{name}\n"
                                  f"Size <b>{', '.join(low)}</b> — last few left\n"
                                  f"{zara.fmt_price(price, cc)}\n{link}")

            if it["t_price"] and price is not None and prev.get("price"):
                if price < prev["price"]:
                    pct = (prev["price"] - price) / prev["price"] * 100
                    if pct >= it["drop_pct"]:
                        alerts.append(
                            f"🔻 <b>PRICE DROP</b> {fl} {cname}\n{name}\n"
                            f"{zara.fmt_price(prev['price'], cc)} → "
                            f"<b>{zara.fmt_price(price, cc)}</b> (−{pct:.0f}%)\n{link}")

        d["state"][key] = {"sizes": now_sizes, "price": price, "ts": time.time()}

    if alerts and len(prices) > 1:
        cheap = min(prices, key=prices.get)
        if prices[cheap] < min(v for k, v in prices.items() if k != cheap):
            alerts[-1] += (f"\n\n💡 Cheapest you watch: {zara.flag(cheap)} "
                           f"{zara.country_name(cheap)} "
                           f"{zara.fmt_price(prices[cheap], cheap)}")
    return alerts


def run_checks(d):
    now = time.time()
    sale = sale_active(d)
    sent = 0
    for it in d["items"]:
        if not it.get("active", 1):
            continue
        if it.get("next_check", 0) > now:
            continue
        base = SALE_INTERVAL if sale else it["interval_m"]
        it["next_check"] = now + max(10, base * zara.LIMITER.backoff) * 60
        try:
            for a in check_item(d, it):
                send(a, preview=True)
                sent += 1
                time.sleep(0.4)
        except Exception:
            traceback.print_exc()
    print(f"[run] {sent} alert(s) sent")

    if zara.LIMITER.backoff > 1 and now - d.get("warned", 0) > 3600:
        d["warned"] = now
        send(f"⚠️ Zara is rate-limiting me — I've slowed checks down "
             f"×{zara.LIMITER.backoff:g} automatically. Nothing lost, just slower.")


# ---------------------------------------------------------------------------
def main():
    if not TOKEN:
        print("TELEGRAM_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    d = load()

    for u in get_updates(d.get("offset", 0)):
        d["offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        if OWNER and str(msg["chat"]["id"]) != OWNER:
            continue
        text = msg.get("text")
        if text:
            try:
                handle_text(d, text)
            except Exception:
                traceback.print_exc()
                send("⚠️ Something went wrong with that. /help")

    run_checks(d)
    save(d)


if __name__ == "__main__":
    main()
