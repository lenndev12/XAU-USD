#!/usr/bin/env python3
"""
XAUUSD ORB Dagelijkse Briefing
Stuurt elke ochtend om 09:00 Brussel een overzicht:
nieuws vandaag, performance laatste 7 dagen, sessietijden, streak.
"""

import os
import base64
import json
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPOSITORY", "")
RISK_PER_TRADE   = 1.0

DAGEN = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]


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


def telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)


def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, timeout=10)
    if r.status_code == 404:
        return None, None
    data = r.json()
    content = json.loads(base64.b64decode(data["content"]).decode())
    return content, data["sha"]


def check_news() -> list:
    try:
        r = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"},
        )
        events = r.json()
        today  = now_brussels().strftime("%Y-%m-%d")
        results = []
        for e in events:
            if e.get("impact") == "High" and e.get("currency") == "USD":
                if e.get("date", "")[:10] == today:
                    time_str = e.get("date", "")[11:16]
                    results.append(f"{time_str} UTC — {e.get('title', '?')}")
        return results
    except:
        return []


def last_7_days_stats(trades: list) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [
        t for t in trades
        if datetime.strptime(t["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= cutoff
    ]
    winners = [t for t in recent if t.get("outcome") == "WIN"]
    losers  = [t for t in recent if t.get("outcome") == "LOSS"]
    closed  = winners + losers
    win_rate = round(len(winners) / len(closed) * 100) if closed else 0
    pnl = (len(winners) * RISK_PER_TRADE * 2.0) - (len(losers) * RISK_PER_TRADE)
    return {"total": len(recent), "wins": len(winners), "losses": len(losers), "win_rate": win_rate, "pnl": pnl}


def get_streak(trades: list) -> int:
    """Positief = win streak, negatief = loss streak."""
    closed = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
    if not closed:
        return 0
    last_outcome = closed[-1]["outcome"]
    streak = 0
    for t in reversed(closed):
        if t["outcome"] == last_outcome:
            streak += 1 if last_outcome == "WIN" else -1
        else:
            break
    return streak


def streak_text(streak: int) -> str:
    if streak >= 3:
        return f"🔥 {streak} wins op rij"
    elif streak > 0:
        return f"✅ {streak} win(s) op rij"
    elif streak <= -3:
        return f"❄️ {abs(streak)} losses op rij — wees voorzichtig vandaag"
    elif streak < 0:
        return f"❌ {abs(streak)} loss(es) op rij"
    else:
        return "➡️ Geen actieve streak"


def main():
    now_bxl = now_brussels()
    dag     = DAGEN[now_bxl.weekday()]
    datum   = now_bxl.strftime(f"{dag} %d/%m/%Y")

    news   = check_news()
    trades, _ = github_get_file("trades.json")
    trades = trades or []
    stats  = last_7_days_stats(trades)
    streak = get_streak(trades)

    # Nieuws sectie
    if news:
        news_lines  = "\n".join([f"⚠️ {n}" for n in news])
        nieuws_blok = f"📰 <b>NIEUWS VANDAAG — GEEN SIGNALEN</b>\n{news_lines}"
    else:
        nieuws_blok = "✅ Geen high-impact nieuws vandaag"

    # Performance sectie
    if stats["total"] == 0:
        perf_blok = "📊 Nog geen data van de afgelopen 7 dagen"
    else:
        pnl_str  = f"+{stats['pnl']:.1f}%" if stats['pnl'] >= 0 else f"{stats['pnl']:.1f}%"
        pnl_icon = "📈" if stats['pnl'] >= 0 else "📉"
        perf_blok = (
            f"📊 <b>LAATSTE 7 DAGEN</b>\n"
            f"✅ {stats['wins']} wins  ❌ {stats['losses']} losses  🎯 {stats['win_rate']}%\n"
            f"{pnl_icon} Theoretische P&L: <b>{pnl_str}</b>"
        )

    bericht = (
        f"🌅 <b>GOEDEMORGEN — {datum}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{nieuws_blok}\n\n"
        f"🕐 <b>SESSIES VANDAAG</b>\n"
        f"🔵 London:  09:30 – 12:00\n"
        f"🟠 NY:      16:00 – 19:00\n\n"
        f"{perf_blok}\n\n"
        f"📈 Streak: {streak_text(streak)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Veel succes vandaag 💪"
    )

    telegram(bericht)
    print("Briefing verstuurd.")


if __name__ == "__main__":
    main()
