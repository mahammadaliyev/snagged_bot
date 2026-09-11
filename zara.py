"""
zara.py — everything that talks to Zara.

Key design point: every outbound request goes through ONE global rate limiter.
Zara doesn't see "item 3 checking every 15 minutes", it sees your total request
volume. So the limiter, not the per-item interval, is what keeps you unblocked.
"""

import json
import random
import re
import threading
import time
import urllib.parse

import requests

# ---------------------------------------------------------------------------
# Country stores. (Azerbaijan deliberately excluded — franchise store.)
# ---------------------------------------------------------------------------
COUNTRIES = {
    "es": ("es", "es", "Spain", "🇪🇸"),
    "pt": ("pt", "pt", "Portugal", "🇵🇹"),
    "fr": ("fr", "fr", "France", "🇫🇷"),
    "de": ("de", "de", "Germany", "🇩🇪"),
    "it": ("it", "it", "Italy", "🇮🇹"),
    "nl": ("nl", "nl", "Netherlands", "🇳🇱"),
    "be": ("be", "en", "Belgium", "🇧🇪"),
    "at": ("at", "de", "Austria", "🇦🇹"),
    "ch": ("ch", "de", "Switzerland", "🇨🇭"),
    "gb": ("gb", "en", "United Kingdom", "🇬🇧"),
    "ie": ("ie", "en", "Ireland", "🇮🇪"),
    "pl": ("pl", "pl", "Poland", "🇵🇱"),
    "cz": ("cz", "en", "Czechia", "🇨🇿"),
    "sk": ("sk", "en", "Slovakia", "🇸🇰"),
    "hu": ("hu", "en", "Hungary", "🇭🇺"),
    "ro": ("ro", "ro", "Romania", "🇷🇴"),
    "gr": ("gr", "el", "Greece", "🇬🇷"),
    "se": ("se", "en", "Sweden", "🇸🇪"),
    "dk": ("dk", "en", "Denmark", "🇩🇰"),
    "tr": ("tr", "tr", "Turkey", "🇹🇷"),
    "us": ("us", "en", "United States", "🇺🇸"),
    "ca": ("ca", "en", "Canada", "🇨🇦"),
    "mx": ("mx", "es", "Mexico", "🇲🇽"),
    "ae": ("ae", "en", "UAE", "🇦🇪"),
    "sa": ("sa", "en", "Saudi Arabia", "🇸🇦"),
    "jp": ("jp", "ja", "Japan", "🇯🇵"),
    "kr": ("kr", "ko", "South Korea", "🇰🇷"),
}

# Shown in the country picker (4 per row). Others still work via /addcountry.
PICKER = ["es", "pt", "fr", "de", "it", "nl", "gb", "ie",
          "pl", "cz", "sk", "hu", "ro", "gr", "tr", "us"]

CURRENCY = {
    "es": "€", "pt": "€", "fr": "€", "de": "€", "it": "€", "nl": "€",
    "be": "€", "at": "€", "ie": "€", "gr": "€", "sk": "€",
    "ch": "CHF", "gb": "£", "pl": "zł", "cz": "Kč", "hu": "Ft",
    "ro": "lei", "se": "kr", "dk": "kr", "tr": "₺", "us": "$",
    "ca": "C$", "mx": "MX$", "ae": "AED", "sa": "SAR", "jp": "¥", "kr": "₩",
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

IN_STOCK = {"in_stock"}
LOW_STOCK = {"low_on_stock"}
AVAILABLE = IN_STOCK | LOW_STOCK


def country_name(code):
    return COUNTRIES.get(code, (code, "en", code.upper(), "🏳️"))[2]


def flag(code):
    return COUNTRIES.get(code, (code, "en", code.upper(), "🏳️"))[3]


def fmt_price(price, code):
    if price is None:
        return "?"
    sym = CURRENCY.get(code, "")
    return f"{price:g} {sym}".strip()


# ---------------------------------------------------------------------------
# Global rate limiter + adaptive backoff
# ---------------------------------------------------------------------------
class RateLimiter:
    """
    Enforces a minimum gap between ALL Zara requests plus an hourly ceiling.
    On 403/429 it multiplies the gap and raises `backoff`, which the scheduler
    also applies to check intervals. Recovers gradually on success.
    """

    def __init__(self, min_gap=3.0, max_per_hour=400):
        self.min_gap = min_gap
        self.max_per_hour = max_per_hour
        self.backoff = 1.0          # multiplier, 1.0 = normal
        self._lock = threading.Lock()
        self._last = 0.0
        self._times = []
        self._ok_streak = 0
        self.blocked_since = None

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                self._times = [t for t in self._times if now - t < 3600]
                if len(self._times) < self.max_per_hour:
                    gap = self.min_gap * self.backoff
                    wait = max(0.0, self._last + gap - now)
                    if wait <= 0:
                        jitter = random.uniform(0, 1.2)
                        self._last = now + jitter
                        self._times.append(now)
                        break
                else:
                    wait = 60.0
            time.sleep(min(wait, 60.0))
        time.sleep(max(0.0, self._last - time.time()))

    def report(self, status):
        """Feed HTTP status back in so the limiter can adapt."""
        with self._lock:
            if status in (403, 429, 503):
                self._ok_streak = 0
                if self.backoff < 8.0:
                    self.backoff = min(8.0, self.backoff * 2)
                if self.blocked_since is None:
                    self.blocked_since = time.time()
                return self.backoff
            if status == 200:
                self._ok_streak += 1
                if self._ok_streak >= 20 and self.backoff > 1.0:
                    self.backoff = max(1.0, self.backoff / 2)
                    self._ok_streak = 0
                    if self.backoff == 1.0:
                        self.blocked_since = None
            return None


LIMITER = RateLimiter()
SESSION = requests.Session()


def headers(cc, lang):
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": f"{lang}-{cc.upper()},{lang};q=0.9,en;q=0.8",
        "Referer": f"https://www.zara.com/{cc}/{lang}/",
        "Connection": "keep-alive",
    }


def get(url, cc, lang, params=None, timeout=25):
    LIMITER.acquire()
    try:
        r = SESSION.get(url, params=params, headers=headers(cc, lang),
                        timeout=timeout)
    except Exception as e:
        return None, f"{type(e).__name__}"
    bo = LIMITER.report(r.status_code)
    if r.status_code != 200:
        msg = f"HTTP {r.status_code}"
        if bo:
            msg += f" (backoff x{bo:g})"
        return None, msg
    try:
        return r.json(), None
    except Exception:
        return None, "bad JSON (bot wall?)"


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------
def parse_product_url(url):
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    if "zara.com" not in p.netloc:
        return None
    parts = [x for x in p.path.split("/") if x]
    country = parts[0].lower() if parts else "es"
    lang = parts[1].lower() if len(parts) > 1 else "es"
    if country not in COUNTRIES:
        country, lang = "es", "es"

    reference = None
    m = re.search(r"-p(\d{6,})\.html", p.path)
    if m:
        d = m.group(1).lstrip("0")
        reference = f"{d[:-3]}/{d[-3:]}" if len(d) >= 7 else d

    qs = urllib.parse.parse_qs(p.query)
    pid = (qs.get("v1") or [None])[0]

    if not reference and not pid:
        return None
    return {"url": url, "country": country, "lang": lang,
            "reference": reference, "product_id": pid}


def product_link(reference, product_id, cc, lang, fallback=None):
    if reference:
        digits = reference.replace("/", "")
        return f"https://www.zara.com/{cc}/{lang}/-p0{digits}.html"
    if product_id:
        return f"https://www.zara.com/{cc}/{lang}/?v1={product_id}"
    return fallback or "https://www.zara.com"


# ---------------------------------------------------------------------------
# Tolerant JSON walking — Zara renames fields; don't hardcode paths.
# ---------------------------------------------------------------------------
def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def find_product_ids(data):
    out = []
    for o in walk(data):
        pid = o.get("id")
        if isinstance(pid, int) and o.get("name") and (o.get("seo") or o.get("detail")):
            out.append(str(pid))
    return out


def extract_sizes(data):
    sizes, seen = [], set()
    for o in walk(data):
        av = o.get("availability")
        if not isinstance(av, str):
            continue
        label = o.get("name") or o.get("size") or o.get("sizeName")
        if not isinstance(label, (str, int)):
            continue
        label = str(label).strip()
        if not label or len(label) > 12:
            continue
        price = o.get("price")
        if isinstance(price, (int, float)) and price > 1000:
            price = round(price / 100, 2)
        if label in seen:
            continue
        seen.add(label)
        sizes.append({"size": label, "availability": av, "price": price})
    return sizes


def extract_price(data, sizes):
    for s in sizes:
        if isinstance(s.get("price"), (int, float)):
            return s["price"]
    for o in walk(data):
        p = o.get("price")
        if isinstance(p, (int, float)) and p > 0:
            return round(p / 100, 2) if p > 1000 else p
    return None


def extract_name(data):
    best = None
    for o in walk(data):
        n = o.get("name")
        if isinstance(n, str) and 3 < len(n) < 80:
            if o.get("seo") or o.get("detail"):
                return n
            best = best or n
    return best


# ---------------------------------------------------------------------------
# High level
# ---------------------------------------------------------------------------
def resolve_id(reference, cc, lang):
    data, err = get(f"https://www.zara.com/{cc}/{lang}/search",
                    cc, lang, {"searchTerm": reference, "ajax": "true"})
    if err:
        return None, err
    ids = find_product_ids(data)
    return (ids[0] if ids else None), (None if ids else "not sold here")


def fetch(product_id, cc, lang):
    """Returns (info_dict, error). info = {name, price, sizes:[{size,availability}]}"""
    data, err = get(f"https://www.zara.com/{cc}/{lang}/products-details",
                    cc, lang, {"productIds": product_id, "ajax": "true"})
    if err:
        return None, err
    sizes = extract_sizes(data)
    if not sizes:
        return None, "no sizes parsed"
    return {
        "name": extract_name(data),
        "price": extract_price(data, sizes),
        "sizes": sizes,
        "raw": data,
    }, None
