#!/usr/bin/env python3
"""
XAUUSD ORB Signal Bot
Stuurt Entry / SL / TP signalen naar Telegram op basis van
de 30-minuten opening range van London en New York sessie.
Volledig gratis, draait in de cloud via GitHub Actions.
"""

import os
import sys
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────
TWELVEDATA_KEY  = os.environ["TWELVEDATA_KEY"]
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL      = "XAU/USD"
SL_BUFFER   = 0.80   # extra ruimte achter range voor Stop Loss (in $)
RR          = 2.0    # Risk:Reward ratio

# Sessie opening ranges in BRUSSEL-TIJD
SESSIONS = {
    "London": {"range_hour": 9,  "range_min": 0,  "signal_start": (9,  30), "signal_end": (12, 0)},
    "NY":     {"range_hour": 15, "range_min": 30, "signal_start": (16, 0),  "signal_end": (19, 0)},
}
# ─────────────────────────────────────────────────────────


def brussels_offset() -> int:
    """Geeft UTC+1 (winter) of UTC+2 (zomer) terug voor Brussel."""
    now = datetime.now(timezone.utc)
    y = now.year
    # Laatste zondag in maart (begin CEST) en oktober (einde CEST)
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


def utc_to_brussels(dt_utc: datetime) -> datetime:
    return dt_utc + timedelta(hours=brussels_offset())


def telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    if not r.ok:
        print(f"Telegram error: {r.text}")


def fetch_candles(interval: str, outputsize: int = 30) -> list:
    """Haalt candles op van TwelveData. Nieuwste eerst."""
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol":     SYMBOL,
            "interval":   interval,
            "outputsize": outputsize,
            "apikey":     TWELVEDATA_KEY,
            "timezone":   "UTC",
            "format":     "JSON",
        },
        timeout=15,
    )
    data = r.json()
    if "values" not in data:
        print(f"TwelveData fout: {data}")
        return []
    return data["values"]


def get_opening_range(session_name: str):
    """
    Zoekt de 30-min candle die overeenkomt met het opening-window.
    Geeft (high, low) terug of (None, None) als die candle er niet is.
    """
    cfg   = SESSIONS[session_name]
    today = now_brussels().date()

    candles = fetch_candles("30min", outputsize=10)
    for c in candles:
        dt_utc     = datetime.strptime(c["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_brussels = utc_to_brussels(dt_utc)
        if (dt_brussels.date()   == today and
            dt_brussels.hour     == cfg["range_hour"] and
            dt_brussels.minute   == cfg["range_min"]):
            return float(c["high"]), float(c["low"])
    return None, None


def average_candle_size(candles_15m: list) -> float:
    """Gemiddelde candle-grootte (high - low) over de laatste 20 candles."""
    sizes = [float(c["high"]) - float(c["low"]) for c in candles_15m[:20]]
    return sum(sizes) / len(sizes) if sizes else 0


def is_fresh(candle: dict) -> bool:
    """
    Controleert of de candle recent genoeg is.
    We willen enkel reageren op een candle die de afgelopen 16 minuten sloot.
    Zo sturen we nooit twee keer hetzelfde signaal.
    """
    dt_utc = datetime.strptime(candle["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    age    = (datetime.now(timezone.utc) - dt_utc).total_seconds() / 60
    return age <= 16


def check_news() -> list:
    """
    Haalt high-impact USD events op van ForexFactory (gratis, geen API key nodig).
    Geeft een lijst van event-titels terug als er iets is vandaag.
    """
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        events  = r.json()
        today   = now_brussels().strftime("%Y-%m-%d")
        results = []
        for e in events:
            if e.get("impact") == "High" and e.get("currency") == "USD":
                date_str = e.get("date", "")[:10]
                if date_str == today:
                    time_str = e.get("date", "")[11:16]
                    results.append(f"{time_str} UTC — {e.get('title', '?')}")
        return results
    except Exception as ex:
        print(f"News check mislukt: {ex}")
        return []


def build_signal_message(direction: str, session: str, entry: float, sl: float, tp: float, rng_high: float, rng_low: float) -> str:
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
    print(f"Brussels tijd: {now_bxl.strftime('%H:%M')} | UTC: {datetime.now(timezone.utc).strftime('%H:%M')}")

    # Bepaal welke sessie actief is
    active_session = None
    for name, cfg in SESSIONS.items():
        start_h, start_m = cfg["signal_start"]
        end_h,   end_m   = cfg["signal_end"]
        t_now   = now_bxl.hour * 60 + now_bxl.minute
        t_start = start_h * 60 + start_m
        t_end   = end_h   * 60 + end_m
        if t_start <= t_now < t_end:
            active_session = name
            break

    if active_session is None:
        print("Buiten signaal-venster. Klaar.")
        return

    print(f"Actieve sessie: {active_session}")

    # News check — stuur melding enkel om exacte session-start (09:30 of 16:00 ± 15 min)
    cfg      = SESSIONS[active_session]
    t_now    = now_bxl.hour * 60 + now_bxl.minute
    t_start  = cfg["signal_start"][0] * 60 + cfg["signal_start"][1]
    is_first_run = abs(t_now - t_start) <= 15

    news = check_news()
    if news:
        if is_first_run:
            news_lines = "\n".join([f"⚠️ {n}" for n in news])
            telegram(
                f"📰 <b>HIGH-IMPACT NEWS VANDAAG</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{news_lines}\n\n"
                f"❌ Geen signalen gegenereerd vandaag.\n"
                f"Trade dit zelf enkel als je weet wat je doet."
            )
        print(f"Nieuwsdag — geen signalen. Events: {news}")
        return

    # Haal opening range op
    rng_high, rng_low = get_opening_range(active_session)
    if rng_high is None:
        print(f"Opening range voor {active_session} nog niet beschikbaar.")
        return

    print(f"Opening range: {rng_low:.2f} – {rng_high:.2f}")

    # Haal 15-min candles op
    candles_15m = fetch_candles("15min", outputsize=25)
    if not candles_15m:
        print("Geen 15-min data ontvangen.")
        return

    # Meest recente gesloten candle
    last = candles_15m[0]

    # Freshness check — enkel reageren op een verse candle
    if not is_fresh(last):
        print(f"Candle te oud ({last['datetime']}). Overgeslagen (al verwerkt).")
        return

    close      = float(last["close"])
    candle_sz  = float(last["high"]) - float(last["low"])
    avg_sz     = average_candle_size(candles_15m)

    print(f"Laatste candle: close={close:.2f} | grootte={candle_sz:.2f} | gemiddelde={avg_sz:.2f}")

    # Grootte-filter: candle moet minstens gemiddeld zijn
    if candle_sz < avg_sz:
        print("Candle kleiner dan gemiddelde — te zwak signaal. Overgeslagen.")
        return

    # Breakout check
    direction = None
    if close > rng_high + SL_BUFFER:
        direction = "LONG"
    elif close < rng_low - SL_BUFFER:
        direction = "SHORT"

    if direction is None:
        print("Geen breakout — prijs zit nog in de range.")
        return

    # Bereken Entry / SL / TP
    entry = close
    if direction == "LONG":
        sl = rng_low - SL_BUFFER
        tp = entry + (entry - sl) * RR
    else:
        sl = rng_high + SL_BUFFER
        tp = entry - (sl - entry) * RR

    # Stuur signaal
    msg = build_signal_message(direction, active_session, entry, sl, tp, rng_high, rng_low)
    telegram(msg)
    print(f"Signaal verstuurd: {direction} | Entry:{entry:.2f} SL:{sl:.2f} TP:{tp:.2f}")


if __name__ == "__main__":
    telegram("✅ Test — bot is actief en verbonden!")
