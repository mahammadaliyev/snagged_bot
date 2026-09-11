"""
commands.py — turns what you type into an item.

Accepts three shapes, all equally valid:

  1. The template (from /new):
        Link: https://zara.com/...
        Sizes: M, L
        Countries: es, de
        Alerts: restock, low, price
        Every: 30m

  2. A one-liner:  <url> M L es de 30m

  3. Just a URL — everything falls back to your defaults.
"""

import re

from zara import COUNTRIES, parse_product_url

TEMPLATE = """Link: 
Sizes: 
Countries: 
Alerts: restock, low, price
Every: 30m"""

ALERT_WORDS = {
    "restock": "t_restock", "stock": "t_restock", "back": "t_restock",
    "low": "t_lowstock", "lowstock": "t_lowstock", "running": "t_lowstock",
    "price": "t_price", "drop": "t_price", "sale": "t_price",
}

FIELD_ALIASES = {
    "link": "link", "url": "link", "product": "link",
    "size": "sizes", "sizes": "sizes",
    "country": "countries", "countries": "countries", "store": "countries",
    "alert": "alerts", "alerts": "alerts", "watch": "alerts",
    "every": "every", "interval": "every", "frequency": "every",
    "drop": "drop", "threshold": "drop",
}

SIZE_RE = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|\d{1,3})$", re.I)
INTERVAL_RE = re.compile(r"^(\d+)\s*(m|min|mins|h|hr|hrs|hour|hours)$", re.I)
PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")


def parse_interval(tok):
    m = INTERVAL_RE.match(tok.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    mins = n * 60 if unit.startswith("h") else n
    return max(15, min(1440, mins))


def _split(v):
    return [x.strip() for x in re.split(r"[,\s]+", v) if x.strip()]


def parse_item(text, defaults):
    """
    Returns (item_dict, warnings) or (None, [error]).
    `defaults` supplies countries / sizes / alerts / interval when omitted.
    """
    warn = []
    fields = {}
    leftovers = []

    # --- template form: "Label: value" lines -------------------------------
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z]+)\s*:\s*(.*)$", line)
        if m and m.group(1).lower() in FIELD_ALIASES and "://" not in m.group(1):
            fields[FIELD_ALIASES[m.group(1).lower()]] = m.group(2).strip()
        else:
            leftovers.append(line)

    # --- one-liner / bare-url form ----------------------------------------
    if "link" not in fields:
        blob = " ".join(leftovers) if leftovers else text
        for tok in blob.split():
            if "zara.com" in tok:
                fields["link"] = tok
            elif tok.lower() in COUNTRIES:
                fields.setdefault("countries", "")
                fields["countries"] += " " + tok
            elif parse_interval(tok):
                fields["every"] = tok
            elif PCT_RE.match(tok):
                fields["drop"] = tok
            elif SIZE_RE.match(tok):
                fields.setdefault("sizes", "")
                fields["sizes"] += " " + tok
            elif tok.lower() in ALERT_WORDS:
                fields.setdefault("alerts", "")
                fields["alerts"] += " " + tok

    link = (fields.get("link") or "").strip()
    if not link:
        return None, ["No Zara link found. Send /new for a blank form."]

    info = parse_product_url(link)
    if not info:
        return None, ["That link isn't a Zara product page. "
                      "Open the item on zara.com and copy the full URL."]

    # --- sizes -------------------------------------------------------------
    raw_sizes = fields.get("sizes", "").strip()
    if not raw_sizes or raw_sizes.lower() in ("all", "any", "*"):
        sizes = defaults.get("sizes", ["*"])
    else:
        sizes = []
        for s in _split(raw_sizes):
            if s.lower() in ("all", "any", "*"):
                sizes = ["*"]
                break
            if SIZE_RE.match(s):
                sizes.append(s.upper())
            else:
                warn.append(f"Ignored '{s}' — not a size.")
        sizes = sizes or defaults.get("sizes", ["*"])

    # --- countries ---------------------------------------------------------
    raw_cc = fields.get("countries", "").strip()
    if not raw_cc:
        countries = list(defaults.get("countries", ["es"]))
    else:
        countries = []
        for c in _split(raw_cc):
            c = c.lower()
            if c in COUNTRIES:
                if c not in countries:
                    countries.append(c)
            else:
                warn.append(f"Ignored country '{c}' — unknown code.")
        countries = countries or list(defaults.get("countries", ["es"]))

    if info["country"] not in countries:
        countries.insert(0, info["country"])

    # --- alerts ------------------------------------------------------------
    raw_al = fields.get("alerts", "").strip()
    if not raw_al:
        al = dict(defaults.get("alerts", {"t_restock": 1, "t_lowstock": 1, "t_price": 1}))
    else:
        al = {"t_restock": 0, "t_lowstock": 0, "t_price": 0}
        for w in _split(raw_al):
            key = ALERT_WORDS.get(w.lower())
            if key:
                al[key] = 1
            else:
                warn.append(f"Ignored alert type '{w}'.")
        if not any(al.values()):
            al = dict(defaults.get("alerts", {"t_restock": 1, "t_lowstock": 1, "t_price": 1}))
            warn.append("No valid alert types — used your defaults.")

    # --- interval ----------------------------------------------------------
    every = parse_interval(fields.get("every", "")) or defaults.get("interval_m", 30)

    # --- price threshold ---------------------------------------------------
    drop = defaults.get("drop_pct", 5)
    m = PCT_RE.match(fields.get("drop", "").strip())
    if m:
        drop = float(m.group(1))

    item = {
        "url": info["url"], "reference": info["reference"],
        "home_cc": info["country"], "home_lang": info["lang"],
        "home_pid": info["product_id"],
        "sizes": sizes, "countries": countries,
        "interval_m": every, "drop_pct": drop, **al,
    }
    return item, warn
