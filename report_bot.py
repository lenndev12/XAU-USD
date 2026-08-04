#!/usr/bin/env python3
"""
XAUUSD ORB Report Bot
Luistert naar /report commando op Telegram.
Berekent voor elk signaal of SL of TP geraakt werd en stuurt een clean overzicht.
"""

import os
import base64
import json
import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TWELVEDATA_KEY   = os.environ["TWELVEDATA_KEY"]
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPOSITORY", "")

RISK_PER_TRADE_PCT = 1.0  # Aanname: 1% risico per trade voor de P&L berekening


# ── GITHUB HELPERS ───────────────────────────────────────

def github_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, timeout=10)
    if r.status_code == 404:
        return None, None
    data = r.json()
    content = json.loads(base64.b64decode(data["content"]).decode())
    return content, data["sha"]


def github_put_file(path, content, sha=None, message="Update"):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(json.dumps(content, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, json=body, timeout=10)
    return r.ok


# ── TELEGRAM HELPERS ─────────────────────────────────────

def telegram(msg: str):
    """Verstuurt een bericht naar Telegram. Splits automatisch bij >4096 tekens."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=10)


def get_updates(offset: int = 0) -> list:
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 5},
        timeout=15,
    )
    if not r.ok:
        return []
    return r.json().get("result", [])


# ── PRIJS DATA ───────────────────────────────────────────

def fetch_candles_since(since_utc_str: str) -> list:
    """
    Haalt 15-min candles op van TwelveData vanaf een bepaald tijdstip.
    since_utc_str formaat: "2026-08-03T14:30:00Z"
    Geeft candles terug van oud naar nieuw.
    """
    start = since_utc_str[:19].replace("T", " ")
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol":     "XAU/USD",
            "interval":   "15min",
            "start_date": start,
            "outputsize": 150,
            "apikey":     TWELVEDATA_KEY,
            "timezone":   "UTC",
            "format":     "JSON",
        },
        timeout=15,
    )
    data = r.json()
    if "values" not in data:
        return []
    return list(reversed(data["values"]))  # Oudste eerst


def get_current_price() -> float:
    """Haalt de huidige XAUUSD prijs op."""
    r = requests.get(
        "https://api.twelvedata.com/price",
        params={"symbol": "XAU/USD", "apikey": TWELVEDATA_KEY},
        timeout=10,
    )
    data = r.json()
    return float(data.get("price", 0))


# ── OUTCOME BEREKENING ───────────────────────────────────

def determine_outcome(trade: dict) -> dict:
    """
    Kijkt of SL of TP geraakt werd na het signaal.
    Verwerkt candles chronologisch en stopt bij de eerste hit.
    """
    candles = fetch_candles_since(trade["timestamp"])
    direction = trade["direction"]
    sl = trade["sl"]
    tp = trade["tp"]

    for c in candles:
        high = float(c["high"])
        low  = float(c["low"])
        dt   = c["datetime"]

        if direction == "LONG":
            if low <= sl:
                trade["outcome"]      = "LOSS"
                trade["pnl_points"]   = round(sl - trade["entry"], 2)
                trade["outcome_time"] = dt
                break
            if high >= tp:
                trade["outcome"]      = "WIN"
                trade["pnl_points"]   = round(tp - trade["entry"], 2)
                trade["outcome_time"] = dt
                break
        else:  # SHORT
            if high >= sl:
                trade["outcome"]      = "LOSS"
                trade["pnl_points"]   = round(trade["entry"] - sl, 2)
                trade["outcome_time"] = dt
                break
            if low <= tp:
                trade["outcome"]      = "WIN"
                trade["pnl_points"]   = round(trade["entry"] - tp, 2)
                trade["outcome_time"] = dt
                break

    # Nog open: bereken huidige floating P&L
    if trade.get("outcome") is None:
        current = get_current_price()
        if current:
            if direction == "LONG":
                trade["pnl_points"] = round(current - trade["entry"], 2)
            else:
                trade["pnl_points"] = round(trade["entry"] - current, 2)

    return trade


# ── RAPPORT GENERATIE ─────────────────────────────────────

def generate_report(trades: list) -> str:
    if not trades:
        return (
            "📊 <b>RAPPORT — XAUUSD ORB</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Nog geen signalen gevonden.\n"
            "De bot heeft nog geen trades verstuurd."
        )

    winners  = [t for t in trades if t.get("outcome") == "WIN"]
    losers   = [t for t in trades if t.get("outcome") == "LOSS"]
    open_t   = [t for t in trades if t.get("outcome") is None]
    closed   = winners + losers

    win_rate = round(len(winners) / len(closed) * 100) if closed else 0

    # P&L berekening op basis van 1% risico per trade
    # WIN = +2% (RR 2:1), LOSS = -1%
    pnl_pct = (len(winners) * RISK_PER_TRADE_PCT * 2.0) - (len(losers) * RISK_PER_TRADE_PCT)
    pnl_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"
    pnl_emoji = "📈" if pnl_pct >= 0 else "📉"

    # Header
    lines = [
        "📊 <b>RAPPORT — XAUUSD ORB</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Totaal signalen: {len(trades)}",
        f"✅ Winners: {len(winners)}  ❌ Losers: {len(losers)}  ⏳ Open: {len(open_t)}",
        f"🎯 Win rate: {win_rate}%",
        f"{pnl_emoji} Theoretische P&L: <b>{pnl_str}</b>",
        f"   (basis: 1% risico/trade, RR 2:1)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>DETAIL PER SIGNAAL</b>",
        "",
    ]

    # Detail per trade (nieuwste eerst)
    for t in reversed(trades):
        ts       = t.get("timestamp", "")
        datum    = ts[8:10] + "/" + ts[5:7] + "/" + ts[0:4]
        tijd     = ts[11:16]
        emoji    = "🟢" if t["direction"] == "LONG" else "🔴"
        outcome  = t.get("outcome")
        pnl      = t.get("pnl_points", 0) or 0
        out_time = (t.get("outcome_time") or "")[:16]

        if outcome == "WIN":
            res = f"✅ TP geraakt om {out_time} UTC | +{abs(pnl):.1f} pts (+{RISK_PER_TRADE_PCT * 2:.1f}%)"
        elif outcome == "LOSS":
            res = f"❌ SL geraakt om {out_time} UTC | -{abs(pnl):.1f} pts (-{RISK_PER_TRADE_PCT:.1f}%)"
        else:
            sign = "+" if pnl >= 0 else ""
            res  = f"⏳ Nog open | Nu: {sign}{pnl:.1f} pts"

        lines.append(
            f"{emoji} <b>{t['direction']} — {t['session']}</b>\n"
            f"   📅 {datum} {tijd} UTC\n"
            f"   Entry: {t['entry']:.2f} | SL: {t['sl']:.2f} | TP: {t['tp']:.2f}\n"
            f"   {res}\n"
        )

    # Footer
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"⚠️ Dit zijn de uitkomsten als je <b>elk signaal</b> had gevolgd.",
        f"Jouw eigen resultaat hangt af van welke trades je effectief nam.",
    ]

    return "\n".join(lines)


# ── MAIN ─────────────────────────────────────────────────

def main():
    # Lees state voor Telegram offset (voorkomt dubbele verwerking)
    state, state_sha = github_get_file("bot_state.json")
    if state is None:
        state = {"last_update_id": 0}

    offset  = state.get("last_update_id", 0) + 1
    updates = get_updates(offset)

    report_requested = False
    new_offset = state.get("last_update_id", 0)

    for update in updates:
        uid = update.get("update_id", 0)
        if uid > new_offset:
            new_offset = uid
        msg  = update.get("message", {}) or update.get("channel_post", {})
        text = msg.get("text", "").strip().lower()
        if text in ("/report", "/rapport", "/stats"):
            report_requested = True

    if report_requested:
        print("Rapport gevraagd — aan het genereren...")
        telegram("⏳ Rapport wordt gegenereerd, even geduld...")

        trades, trades_sha = github_get_file("trades.json")

        if not trades:
            telegram("📊 Nog geen signalen gevonden. De bot heeft nog geen trades verstuurd.")
        else:
            # Update outcomes voor open trades
            updated = False
            for i, trade in enumerate(trades):
                if trade.get("outcome") is None:
                    print(f"Outcome berekenen voor {trade['id']}...")
                    trades[i] = determine_outcome(trade)
                    updated = True

            if updated:
                github_put_file("trades.json", trades, trades_sha, "Outcomes bijgewerkt via /report")

            report = generate_report(trades)
            telegram(report)
            print("Rapport verstuurd.")

    # Sla nieuwe offset op
    if new_offset > state.get("last_update_id", 0):
        state["last_update_id"] = new_offset
        github_put_file("bot_state.json", state, state_sha, "Telegram offset update")

    print(f"Updates verwerkt: {len(updates)} | Rapport: {report_requested}")


if __name__ == "__main__":
    main()
