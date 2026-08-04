   #!/usr/bin/env python3
"""
XAUUSD ORB Signal Bot
Stuurt Entry / SL / TP signalen naar Telegram en slaat elke trade op in trades.json.
"""

import os
import base64
import json
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────
TWELVEDATA_KEY   = os.environ["TWELVEDATA_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPOSITORY", "")

SYMBOL    = "XAU/USD"
SL_BUFFER = 0.80
RR        = 2.0

SESSIONS = {
    "London": {"range_hour": 9,  "range_min": 0,  "signal_start": (9,  30), "signal_end": (12, 0)},
    "NY":     {"range_hour": 15, "range_min": 30, "signal_start": (16, 0),  "signal_end": (19, 0)},
}
# ─────────────────────────────────────────────────────────


def brussels_offset() -> int:
    now = datetime.now(timezone.utc)
    y = now.year
    def last_sunday(month):
        for d in range(31, 24, -1):
            try:
                if datetime(y, month, d).weekday() == 6:
                    return d
            except ValueError:
                continue
    cest_start = datetime(y, 3,  last_sunday(3),  1, 0, tzinfo=timezone.utc)
    cest_end   = datetime(y, 10, last_sunday(10), 1, 0, tzinfo=timezone.utc)
    return 2 if cest_start <= now < cest_end else 1


def now_brussels():
    return datetime.now(timezone.utc) + timedelta(hours=brussels_offset())


def utc_to_brussels(dt_utc):
    return dt_utc + timedelta(hours=brussels_offset())


def telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"Telegram error: {r.text}")


def fetch_candles(interval: str, outputsize: int = 30) -> list:
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": SYMBOL, "interval": interval, "outputsize": outputsize,
                "apikey": TWELVEDATA_KEY, "timezone": "UTC", "format": "JSON"},
        timeout=15,
    )
    data = r.json()
    if "values" not in data:
        print(f"TwelveData fout: {data}")
        return []
    return data["values"]


def github_get_file(path):
    """Lees een bestand uit de GitHub repo. Geeft (inhoud, sha) terug."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, timeout=10)
    if r.status_code == 404:
        return None, None
    if not r.ok:
        print(f"GitHub read fout: {r.text}")
        return None, None
    data = r.json()
    content = json.loads(base64.b64decode(data["content"]).decode())
    return content, data["sha"]


def github_put_file(path, content, sha=None, message="Update"):
    """Schrijf een bestand naar de GitHub repo."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(json.dumps(content, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, json=body, timeout=10)
    if not r.ok:
        print(f"GitHub write fout: {r.text}")
    return r.ok


def save_trade(trade: dict):
    """Sla een nieuw signaal op in trades.json in de repo."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("Geen GitHub token/repo — trade niet opgeslagen.")
        return
    trades, sha = github_get_file("trades.json")
    if trades is None:
        trades = []
    trades.append(trade)
    ok = github_put_file("trades.json", trades, sha, f"Signaal: {trade['id']}")
    print(f"Trade opgeslagen in repo: {trade['id']} — {'OK' if ok else 'MISLUKT'}")


def get_opening_range(session_name: str):
    cfg   = SESSIONS[session_name]
    today = now_brussels().date()
    candles = fetch_candles("30min", outputsize=10)
    for c in candles:
        dt_utc     = datetime.strptime(c["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_brussels = utc_to_brussels(dt_utc)
        if (dt_brussels.date() == today and
            dt_brussels.hour   == cfg["range_hour"] and
            dt_brussels.minute == cfg["range_min"]):
            return float(c["high"]), float(c["low"])
    return None, None


def average_candle_size(candles_15m: list) -> float:
    sizes = [float(c["high"]) - float(c["low"]) for c in candles_15m[:20]]
    return sum(sizes) / len(sizes) if sizes else 0


def is_fresh(candle: dict) -> bool:
    dt_utc = datetime.strptime(candle["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    age    = (datetime.now(timezone.utc) - dt_utc).total_seconds() / 60
    return age <= 16


def check_news() -> list:
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"},
        )
        events  = r.json()
        today   = now_brussels().strftime("%Y-%m-%d")
        results = []
        for e in events:
            if e.get("impact") == "High" and e.get("currency") == "USD":
                if e.get("date", "")[:10] == today:
                    time_str = e.get("date", "")[11:16]
                    results.append(f"{time_str} UTC — {e.get('title', '?')}")
        return results
    except Exception as ex:
        print(f"News check mislukt: {ex}")
        return []


def build_signal_message(direction, session, entry, sl, tp, rng_high, rng_low):
    emoji = "🟢" if direction == "LONG" else "🔴"
    risk  = abs(entry - sl)
    return (
        f"{emoji} <b>{direction} — XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📍 Entry:  <b>{entry:.2f}</b>\n"
        f"🛑 SL:     <b>{sl:.2f}</b>\n"
        f"🎯 TP:     <b>{tp:.2f}</b>\n"
        f"📊 RR:     1:{RR:.0f}  |  Risk: ${risk:.2f}\n"
        f"🕐 Sessie: {session}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Range: {rng_low:.2f} – {rng_high:.2f}"
    )


def main():
    now_bxl = now_brussels()
    print(f"Brussels: {now_bxl.strftime('%H:%M')} | UTC: {datetime.now(timezone.utc).strftime('%H:%M')}")

    active_session = None
    for name, cfg in SESSIONS.items():
        t_now   = now_bxl.hour * 60 + now_bxl.minute
        t_start = cfg["signal_start"][0] * 60 + cfg["signal_start"][1]
        t_end   = cfg["signal_end"][0]   * 60 + cfg["signal_end"][1]
        if t_start <= t_now < t_end:
            active_session = name
            break

    if active_session is None:
        print("Buiten signaal-venster. Klaar.")
        return

    print(f"Actieve sessie: {active_session}")

    cfg          = SESSIONS[active_session]
    t_now        = now_bxl.hour * 60 + now_bxl.minute
    t_start      = cfg["signal_start"][0] * 60 + cfg["signal_start"][1]
    is_first_run = abs(t_now - t_start) <= 15

    news = check_news()
    if news:
        if is_first_run:
            news_lines = "\n".join([f"⚠️ {n}" for n in news])
            telegram(
                f"📰 <b>HIGH-IMPACT NEWS VANDAAG</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{news_lines}\n\n"
                f"❌ Geen signalen gegenereerd vandaag."
            )
        print(f"Nieuwsdag — geen signalen: {news}")
        return

    rng_high, rng_low = get_opening_range(active_session)
    if rng_high is None:
        print(f"Opening range voor {active_session} nog niet beschikbaar.")
        return

    print(f"Opening range: {rng_low:.2f} – {rng_high:.2f}")

    candles_15m = fetch_candles("15min", outputsize=25)
    if not candles_15m:
        print("Geen 15-min data.")
        return

    last = candles_15m[0]
    if not is_fresh(last):
        print(f"Candle te oud ({last['datetime']}). Al verwerkt.")
        return

    close     = float(last["close"])
    candle_sz = float(last["high"]) - float(last["low"])
    avg_sz    = average_candle_size(candles_15m)

    print(f"Candle: close={close:.2f} | grootte={candle_sz:.2f} | gem={avg_sz:.2f}")

    if candle_sz < avg_sz:
        print("Candle te klein — overgeslagen.")
        return

    direction = None
    if close > rng_high + SL_BUFFER:
        direction = "LONG"
    elif close < rng_low - SL_BUFFER:
        direction = "SHORT"

    if direction is None:
        print("Geen breakout.")
        return

    entry = close
    if direction == "LONG":
        sl = rng_low  - SL_BUFFER
        tp = entry + (entry - sl) * RR
    else:
        sl = rng_high + SL_BUFFER
        tp = entry - (sl - entry) * RR

    # Stuur signaal naar Telegram
    msg = build_signal_message(direction, active_session, entry, sl, tp, rng_high, rng_low)
    telegram(msg)
    print(f"Signaal: {direction} Entry:{entry:.2f} SL:{sl:.2f} TP:{tp:.2f}")

    # Sla trade op in repo
    trade_id = f"{active_session[:3].upper()}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    save_trade({
        "id":         trade_id,
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session":    active_session,
        "direction":  direction,
        "entry":      entry,
        "sl":         sl,
        "tp":         tp,
        "rr":         RR,
        "range_high": rng_high,
        "range_low":  rng_low,
        "outcome":    None,
        "pnl_points": None,
        "outcome_time": None,
    })


if __name__ == "__main__":
    main()
