#!/usr/bin/env python3
"""
XAUUSD ORB Bot — Unified 24/7
Draait continu op Koyeb. Combineert:
  - Instant Telegram commando's (/report, /status)
  - Signalen elke 5 min tijdens London & NY sessie
  - Dagelijkse briefing om 09:00 Brussel
  - Automatisch weekrapport elke vrijdag 17:00 Brussel
  - Streak tracker met waarschuwingen
"""

import os, base64, json, time, threading, requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ────────────────────────────────────────────────
TWELVEDATA_KEY   = os.environ["TWELVEDATA_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN     = os.environ["GITHUB_TOKEN"]
GITHUB_REPO      = os.environ["GITHUB_REPOSITORY"]  # bv. "lenndev12/XAU-USD"

SYMBOL    = "XAU/USD"
SL_BUFFER = 0.80
RR        = 2.0
RISK_PCT  = 1.0

SESSIONS = {
    "London": {"range_hour": 9,  "range_min": 0,  "signal_start": (9,  30), "signal_end": (12, 0)},
    "NY":     {"range_hour": 15, "range_min": 30, "signal_start": (16, 0),  "signal_end": (19, 0)},
}
# ─────────────────────────────────────────────────────────

# In-memory state
telegram_offset   = 0
last_signal       = {}     # {"London": "2026-08-04T09:45:00Z", ...}
briefing_sent     = {}     # {"2026-08-04": True}
weekly_sent       = {}     # {"2026-W32": True}
signal_lock       = threading.Lock()


# ── TIJD ─────────────────────────────────────────────────

def brussels_offset() -> int:
    now = datetime.now(timezone.utc)
    y = now.year
    def last_sunday(m):
        for d in range(31, 24, -1):
            try:
                if datetime(y, m, d).weekday() == 6:
                    return d
            except ValueError:
                continue
    cs = datetime(y, 3,  last_sunday(3),  1, 0, tzinfo=timezone.utc)
    ce = datetime(y, 10, last_sunday(10), 1, 0, tzinfo=timezone.utc)
    return 2 if cs <= now < ce else 1

def now_bxl():
    return datetime.now(timezone.utc) + timedelta(hours=brussels_offset())

def to_bxl(dt_utc):
    return dt_utc + timedelta(hours=brussels_offset())


# ── TELEGRAM ─────────────────────────────────────────────

def tg(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            print(f"Telegram fout: {e}")

def get_updates(offset=0) -> list:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 10}, timeout=15,
        )
        return r.json().get("result", []) if r.ok else []
    except:
        return []


# ── GITHUB ───────────────────────────────────────────────

def gh_read(path):
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, timeout=10,
        )
        if r.status_code == 404:
            return None, None
        d = r.json()
        return json.loads(base64.b64decode(d["content"]).decode()), d["sha"]
    except Exception as e:
        print(f"GitHub read fout ({path}): {e}")
        return None, None

def gh_write(path, content, sha=None, msg="Update"):
    try:
        body = {"message": msg, "content": base64.b64encode(json.dumps(content, indent=2).encode()).decode()}
        if sha:
            body["sha"] = sha
        r = requests.put(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"}, json=body, timeout=10,
        )
        return r.ok
    except Exception as e:
        print(f"GitHub write fout ({path}): {e}")
        return False

def save_trade(trade: dict):
    trades, sha = gh_read("trades.json")
    if trades is None:
        trades = []
    trades.append(trade)
    ok = gh_write("trades.json", trades, sha, f"Signaal: {trade['id']}")
    print(f"Trade opgeslagen: {trade['id']} — {'OK' if ok else 'MISLUKT'}")

def load_trades() -> list:
    trades, _ = gh_read("trades.json")
    return trades or []


# ── TWELVEDATA ───────────────────────────────────────────

def fetch_candles(interval: str, outputsize=30, start_date=None) -> list:
    params = {"symbol": SYMBOL, "interval": interval, "outputsize": outputsize,
              "apikey": TWELVEDATA_KEY, "timezone": "UTC", "format": "JSON"}
    if start_date:
        params["start_date"] = start_date
    try:
        r = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=15)
        data = r.json()
        return data.get("values", [])
    except:
        return []

def current_price() -> float:
    try:
        r = requests.get("https://api.twelvedata.com/price",
                        params={"symbol": SYMBOL, "apikey": TWELVEDATA_KEY}, timeout=10)
        return float(r.json().get("price", 0))
    except:
        return 0.0


# ── CORE LOGICA ───────────────────────────────────────────

def check_news() -> list:
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                        timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        today = now_bxl().strftime("%Y-%m-%d")
        return [
            f"{e.get('date','')[11:16]} UTC — {e.get('title','?')}"
            for e in r.json()
            if e.get("impact") == "High" and e.get("currency") == "USD"
            and e.get("date","")[:10] == today
        ]
    except:
        return []

def get_opening_range(session_name: str):
    cfg   = SESSIONS[session_name]
    today = now_bxl().date()
    for c in fetch_candles("30min", outputsize=10):
        dt = to_bxl(datetime.strptime(c["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
        if dt.date() == today and dt.hour == cfg["range_hour"] and dt.minute == cfg["range_min"]:
            return float(c["high"]), float(c["low"])
    return None, None

def avg_candle_size(candles: list) -> float:
    sizes = [float(c["high"]) - float(c["low"]) for c in candles[:20]]
    return sum(sizes) / len(sizes) if sizes else 0

def is_fresh(candle: dict) -> bool:
    dt = datetime.strptime(candle["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60 <= 6

def get_streak(trades: list) -> int:
    closed = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
    if not closed:
        return 0
    last = closed[-1]["outcome"]
    s = 0
    for t in reversed(closed):
        if t["outcome"] == last:
            s += 1 if last == "WIN" else -1
        else:
            break
    return s

def determine_outcome(trade: dict) -> dict:
    start = trade["timestamp"][:19].replace("T", " ")
    candles = list(reversed(fetch_candles("15min", outputsize=150, start_date=start)))
    d, sl, tp = trade["direction"], trade["sl"], trade["tp"]
    for c in candles:
        h, l, dt = float(c["high"]), float(c["low"]), c["datetime"]
        if d == "LONG":
            if l <= sl:
                trade.update({"outcome": "LOSS", "pnl_points": round(sl - trade["entry"], 2), "outcome_time": dt})
                break
            if h >= tp:
                trade.update({"outcome": "WIN",  "pnl_points": round(tp - trade["entry"], 2), "outcome_time": dt})
                break
        else:
            if h >= sl:
                trade.update({"outcome": "LOSS", "pnl_points": round(trade["entry"] - sl, 2), "outcome_time": dt})
                break
            if l <= tp:
                trade.update({"outcome": "WIN",  "pnl_points": round(trade["entry"] - tp, 2), "outcome_time": dt})
                break
    if trade.get("outcome") is None:
        p = current_price()
        if p:
            trade["pnl_points"] = round(p - trade["entry"] if d == "LONG" else trade["entry"] - p, 2)
    return trade


# ── BERICHTEN ────────────────────────────────────────────

def streak_text(s: int) -> str:
    if s >= 3:   return f"🔥 {s} wins op rij"
    if s > 0:    return f"✅ {s} win(s) op rij"
    if s <= -3:  return f"❄️ {abs(s)} losses op rij — wees voorzichtig"
    if s < 0:    return f"❌ {abs(s)} loss(es) op rij"
    return "➡️ Geen actieve streak"

def signal_msg(direction, session, entry, sl, tp, rng_h, rng_l, streak):
    emoji = "🟢" if direction == "LONG" else "🔴"
    warn  = f"\n⚠️ <b>{abs(streak)} losses op rij — wees voorzichtig!</b>" if streak <= -3 else \
            f"\n🔥 {streak} wins op rij" if streak >= 3 else ""
    return (
        f"{emoji} <b>{direction} — XAUUSD</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📍 Entry:  <b>{entry:.2f}</b>\n"
        f"🛑 SL:     <b>{sl:.2f}</b>\n"
        f"🎯 TP:     <b>{tp:.2f}</b>\n"
        f"📊 RR:     1:{RR:.0f}  |  Risk: ${abs(entry-sl):.2f}\n"
        f"🕐 Sessie: {session}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Range: {rng_l:.2f} – {rng_h:.2f}{warn}"
    )

def report_msg(trades: list, titel="RAPPORT") -> str:
    if not trades:
        return "📊 Nog geen signalen gevonden."
    wins   = [t for t in trades if t.get("outcome") == "WIN"]
    losses = [t for t in trades if t.get("outcome") == "LOSS"]
    open_t = [t for t in trades if t.get("outcome") is None]
    closed = wins + losses
    wr     = round(len(wins) / len(closed) * 100) if closed else 0
    pnl    = (len(wins) * RISK_PCT * 2.0) - (len(losses) * RISK_PCT)
    pnl_s  = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
    streak = get_streak(trades)

    lines = [
        f"📊 <b>{titel} — XAUUSD ORB</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Totaal: {len(trades)}  ✅ {len(wins)}  ❌ {len(losses)}  ⏳ {len(open_t)}",
        f"🎯 Win rate: {wr}%",
        f"{'📈' if pnl >= 0 else '📉'} Theoretische P&L: <b>{pnl_s}</b>  (1% risico/trade)",
        f"📊 Streak: {streak_text(streak)}",
        "━━━━━━━━━━━━━━━━━━━━━━", "",
        "<b>DETAIL (laatste 15)</b>", "",
    ]
    for t in reversed(trades[-15:]):
        ts    = t.get("timestamp","")
        datum = f"{ts[8:10]}/{ts[5:7]}/{ts[0:4]} {ts[11:16]}"
        emoji = "🟢" if t["direction"] == "LONG" else "🔴"
        p     = t.get("pnl_points",0) or 0
        ot    = (t.get("outcome_time") or "")[:16]
        if t.get("outcome") == "WIN":
            res = f"✅ TP geraakt {ot} | +{abs(p):.1f} pts (+{RISK_PCT*2:.1f}%)"
        elif t.get("outcome") == "LOSS":
            res = f"❌ SL geraakt {ot} | -{abs(p):.1f} pts (-{RISK_PCT:.1f}%)"
        else:
            res = f"⏳ Open | {'+' if p >= 0 else ''}{p:.1f} pts nu"
        lines.append(
            f"{emoji} <b>{t['direction']} — {t['session']}</b> — {datum} UTC\n"
            f"   Entry: {t['entry']:.2f} | SL: {t['sl']:.2f} | TP: {t['tp']:.2f}\n"
            f"   {res}\n"
        )
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              "⚠️ Uitkomsten als je <b>elk signaal</b> had gevolgd."]
    return "\n".join(lines)

def status_msg(trades: list) -> str:
    n     = now_bxl()
    t_now = n.hour * 60 + n.minute
    active = next(
        (name for name, cfg in SESSIONS.items()
         if cfg["signal_start"][0]*60+cfg["signal_start"][1] <= t_now < cfg["signal_end"][0]*60+cfg["signal_end"][1]),
        None
    )
    next_s = next(
        (f"{name} om {cfg['signal_start'][0]:02d}:{cfg['signal_start'][1]:02d}"
         for name, cfg in SESSIONS.items()
         if cfg["signal_start"][0]*60+cfg["signal_start"][1] > t_now),
        "London morgen om 09:30"
    )
    today = n.strftime("%Y-%m-%d")
    sig_today = sum(1 for t in trades if t.get("timestamp","")[:10] == today)
    streak = get_streak(trades)
    sess_line = f"🟢 Actieve sessie: <b>{active}</b>" if active else f"⏸ Geen actieve sessie\n⏰ Volgende: {next_s}"
    return (
        f"📡 <b>BOT STATUS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {n.strftime('%H:%M')} Brussel\n\n"
        f"{sess_line}\n\n"
        f"📈 Signalen vandaag: {sig_today}\n"
        f"📊 Streak: {streak_text(streak)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔵 London:  09:30 – 12:00\n"
        f"🟠 NY:      16:00 – 19:00"
    )

def briefing_msg(trades: list) -> str:
    n      = now_bxl()
    dagen  = ["Ma","Di","Wo","Do","Vr","Za","Zo"]
    datum  = f"{dagen[n.weekday()]} {n.strftime('%d/%m/%Y')}"
    news   = check_news()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [t for t in trades if
              datetime.strptime(t["timestamp"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= cutoff]
    wins   = sum(1 for t in recent if t.get("outcome") == "WIN")
    losses = sum(1 for t in recent if t.get("outcome") == "LOSS")
    closed = wins + losses
    wr     = round(wins / closed * 100) if closed else 0
    pnl    = (wins * RISK_PCT * 2.0) - (losses * RISK_PCT)
    pnl_s  = f"+{pnl:.1f}%" if pnl >= 0 else f"{pnl:.1f}%"
    streak = get_streak(trades)

    news_blok = (
        "📰 <b>NIEUWS VANDAAG — GEEN SIGNALEN</b>\n" + "\n".join(f"⚠️ {n}" for n in news)
        if news else "✅ Geen high-impact nieuws vandaag"
    )
    perf_blok = (
        f"📊 <b>LAATSTE 7 DAGEN</b>\n"
        f"✅ {wins} wins  ❌ {losses} losses  🎯 {wr}%\n"
        f"{'📈' if pnl >= 0 else '📉'} Theoretische P&L: <b>{pnl_s}</b>"
        if closed else "📊 Nog geen data van de afgelopen 7 dagen"
    )
    return (
        f"🌅 <b>GOEDEMORGEN — {datum}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{news_blok}\n\n"
        f"🕐 <b>SESSIES VANDAAG</b>\n"
        f"🔵 London:  09:30 – 12:00\n"
        f"🟠 NY:      16:00 – 19:00\n\n"
        f"{perf_blok}\n\n"
        f"📈 Streak: {streak_text(streak)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Veel succes vandaag 💪"
    )


# ── GEPLANDE TAKEN ────────────────────────────────────────

def job_check_signal():
    """Elke 5 minuten: check of er een ORB breakout is."""
    with signal_lock:
        n     = now_bxl()
        t_now = n.hour * 60 + n.minute

        active = next(
            (name for name, cfg in SESSIONS.items()
             if cfg["signal_start"][0]*60+cfg["signal_start"][1] <= t_now < cfg["signal_end"][0]*60+cfg["signal_end"][1]),
            None
        )
        if not active:
            return

        # Voorkom duplicate signalen binnen 10 minuten
        last = last_signal.get(active)
        if last:
            diff = (datetime.now(timezone.utc) - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60
            if diff < 10:
                return

        news = check_news()
        if news:
            cfg     = SESSIONS[active]
            t_start = cfg["signal_start"][0]*60 + cfg["signal_start"][1]
            if abs(t_now - t_start) <= 5:
                tg("📰 <b>HIGH-IMPACT NEWS VANDAAG</b>\n━━━━━━━━━━━━━━━━\n" +
                   "\n".join(f"⚠️ {x}" for x in news) + "\n\n❌ Geen signalen vandaag.")
            return

        rng_h, rng_l = get_opening_range(active)
        if rng_h is None:
            return

        candles = fetch_candles("15min", outputsize=25)
        if not candles:
            return

        last_c = candles[0]
        if not is_fresh(last_c):
            return

        close = float(last_c["close"])
        csize = float(last_c["high"]) - float(last_c["low"])
        if csize < avg_candle_size(candles):
            print(f"Candle te klein ({csize:.2f} < {avg_candle_size(candles):.2f})")
            return

        direction = None
        if close > rng_h + SL_BUFFER:
            direction = "LONG"
        elif close < rng_l - SL_BUFFER:
            direction = "SHORT"

        if not direction:
            return

        entry = close
        sl    = rng_l - SL_BUFFER if direction == "LONG" else rng_h + SL_BUFFER
        tp    = entry + abs(entry-sl)*RR if direction == "LONG" else entry - abs(sl-entry)*RR

        trades = load_trades()
        streak = get_streak(trades)

        tg(signal_msg(direction, active, entry, sl, tp, rng_h, rng_l, streak))
        last_signal[active] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"Signaal: {direction} {active} Entry:{entry:.2f} SL:{sl:.2f} TP:{tp:.2f}")

        trade_id = f"{active[:3].upper()}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        save_trade({
            "id": trade_id, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session": active, "direction": direction, "entry": entry, "sl": sl, "tp": tp,
            "rr": RR, "range_high": rng_h, "range_low": rng_l,
            "outcome": None, "pnl_points": None, "outcome_time": None,
        })


def job_briefing():
    """Elke weekdag om 09:00 Brussel."""
    n    = now_bxl()
    key  = n.strftime("%Y-%m-%d")
    if n.weekday() >= 5 or briefing_sent.get(key):
        return
    briefing_sent[key] = True
    trades = load_trades()
    tg(briefing_msg(trades))
    print("Briefing verstuurd.")


def job_weekly_report():
    """Elke vrijdag om 17:00 Brussel."""
    n   = now_bxl()
    key = f"{n.year}-W{n.isocalendar()[1]}"
    if n.weekday() != 4 or weekly_sent.get(key):
        return
    weekly_sent[key] = True
    trades = load_trades()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    week   = [t for t in trades if
              datetime.strptime(t["timestamp"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= cutoff]

    # Update outcomes
    updated = False
    for i, t in enumerate(week):
        if t.get("outcome") is None:
            week[i] = determine_outcome(t)
            updated = True
    if updated:
        all_trades, sha = gh_read("trades.json")
        if all_trades:
            id_map = {t["id"]: t for t in week}
            for i, t in enumerate(all_trades):
                if t["id"] in id_map:
                    all_trades[i] = id_map[t["id"]]
            gh_write("trades.json", all_trades, sha, "Outcomes update weekrapport")

    tg(report_msg(week, titel="WEEKRAPPORT"))
    print("Weekrapport verstuurd.")


# ── TELEGRAM COMMAND HANDLER ──────────────────────────────

def handle_command(text: str):
    text = text.strip().lower()
    print(f"Commando ontvangen: {text}")

    if text in ("/report", "/rapport"):
        tg("⏳ Rapport wordt gegenereerd...")
        trades, sha = gh_read("trades.json")
        trades = trades or []
        updated = False
        for i, t in enumerate(trades):
            if t.get("outcome") is None:
                trades[i] = determine_outcome(t)
                updated = True
        if updated:
            gh_write("trades.json", trades, sha, "Outcomes update via /report")
        tg(report_msg(trades))

    elif text == "/status":
        trades = load_trades()
        tg(status_msg(trades))

    elif text == "/briefing":
        trades = load_trades()
        tg(briefing_msg(trades))

    elif text == "/help":
        tg(
            "🤖 <b>XAUUSD ORB BOT — COMMANDO'S</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "/report — volledig rapport met alle signalen\n"
            "/status — huidige sessie, signalen vandaag, streak\n"
            "/briefing — dagelijks overzicht op aanvraag\n"
            "/help — dit menu"
        )


# ── TELEGRAM POLLING LOOP ─────────────────────────────────

def poll_loop():
    global telegram_offset
    print("Telegram polling gestart...")
    while True:
        try:
            updates = get_updates(telegram_offset + 1)
            for u in updates:
                uid = u.get("update_id", 0)
                if uid > telegram_offset:
                    telegram_offset = uid
                msg  = u.get("message", {}) or u.get("channel_post", {})
                text = msg.get("text", "")
                if text:
                    handle_command(text)
        except Exception as e:
            print(f"Poll fout: {e}")
        time.sleep(2)


# ── SCHEDULER LOOP ────────────────────────────────────────

def scheduler_loop():
    last_signal_check = 0
    last_briefing_check = 0
    last_weekly_check = 0

    print("Scheduler gestart...")
    while True:
        now_ts = time.time()
        n      = now_bxl()

        # Signaalcheck elke 5 minuten
        if now_ts - last_signal_check >= 300:
            last_signal_check = now_ts
            try:
                job_check_signal()
            except Exception as e:
                print(f"Signal fout: {e}")

        # Briefing check: elke minuut tussen 08:55-09:05 Brussel
        if 8*60+55 <= n.hour*60+n.minute <= 9*60+5:
            if now_ts - last_briefing_check >= 60:
                last_briefing_check = now_ts
                try:
                    job_briefing()
                except Exception as e:
                    print(f"Briefing fout: {e}")

        # Weekrapport check: vrijdag tussen 16:55-17:05 Brussel
        if n.weekday() == 4 and 16*60+55 <= n.hour*60+n.minute <= 17*60+5:
            if now_ts - last_weekly_check >= 60:
                last_weekly_check = now_ts
                try:
                    job_weekly_report()
                except Exception as e:
                    print(f"Weekrapport fout: {e}")

        time.sleep(20)


# ── MAIN ─────────────────────────────────────────────────

if __name__ == "__main__":
    print("XAUUSD ORB Bot gestart 🚀")
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    tg("🚀 <b>XAUUSD ORB Bot is online!</b>\nType /help voor alle commando's.")
    while True:
        time.sleep(60)
