# XAU/USD ORB Bot

Een 24/7 draaiende Telegram-bot voor XAU/USD (goud) op basis van de Opening Range Breakout (ORB) strategie. De bot combineert:

- **Live signalen** elke 5 minuten tijdens de London- en NY-sessie
- **Telegram-commando's** (`/report`, `/status`, `/help`, …)
- **Dagelijkse briefing** om 09:00 (Brussel)
- **Automatisch weekrapport** elke vrijdag om 17:00 (Brussel)
- **Streak tracker** met waarschuwingen

## Vereisten

- Docker (aanbevolen), of Python 3.12+
- De volgende accounts / API-keys:
  - [TwelveData](https://twelvedata.com) API-key (koersdata)
  - Een Telegram-bot via [@BotFather](https://t.me/BotFather)
  - Een GitHub personal access token (voor het opslaan van rapporten)

## Environment-variabelen

De bot leest alle secrets uit environment-variabelen. Zet nooit een key hard in de code of in de `Dockerfile`.

| Variabele            | Beschrijving                                             | Voorbeeld              |
| -------------------- | ------------------------------------------------------- | ---------------------- |
| `TWELVEDATA_KEY`     | API-key van TwelveData voor de koersdata                | `abc123...`            |
| `TELEGRAM_TOKEN`     | Token van je Telegram-bot (van BotFather)               | `123456:ABC-DEF...`    |
| `TELEGRAM_CHAT_ID`   | Chat-ID waar de bot naartoe stuurt                      | `123456789`            |
| `GITHUB_TOKEN`       | GitHub personal access token (repo-scope)               | `ghp_...`              |
| `GITHUB_REPOSITORY`  | Repo waar rapporten worden bewaard, `owner/naam`        | `lenndev12/XAU-USD`    |

## Lokaal draaien met Docker

Bouw de image:

```bash
docker build -t xau-bot .
```

Start de container (vul je eigen keys in):

```bash
docker run --rm \
  -e TWELVEDATA_KEY=... \
  -e TELEGRAM_TOKEN=... \
  -e TELEGRAM_CHAT_ID=... \
  -e GITHUB_TOKEN=... \
  -e GITHUB_REPOSITORY=owner/repo \
  xau-bot
```

Tip: zet de variabelen in een `.env`-bestand en gebruik `--env-file`:

```bash
docker run --rm --env-file .env xau-bot
```

> Voeg `.env` toe aan je `.gitignore` zodat je secrets niet in git belanden.

## Lokaal draaien zonder Docker

```bash
pip install -r requirements.txt
export TWELVEDATA_KEY=...
export TELEGRAM_TOKEN=...
export TELEGRAM_CHAT_ID=...
export GITHUB_TOKEN=...
export GITHUB_REPOSITORY=owner/repo
python bot.py
```

## Deployen op een cloud-service

De bot is een langdraaiend proces (geen webserver, geen open poort). Kies een service die "worker" / "background" / "always-on" containers ondersteunt. Het patroon is overal hetzelfde:

1. Koppel deze repository of push de Docker-image.
2. De service bouwt de `Dockerfile` automatisch.
3. Zet de vijf environment-variabelen in het dashboard.
4. Zorg dat er één instance draait (de bot is niet bedoeld om te schalen).

### Koyeb

- New App → GitHub repo of Docker image
- Type: **Worker** (geen poort nodig)
- Voeg de env-variabelen toe onder *Environment variables*
- Deploy

### Railway

- New Project → Deploy from GitHub repo
- Railway detecteert de `Dockerfile`
- Variables-tab → zet de vijf env-variabelen
- Deploy

### Render

- New → **Background Worker** (niet Web Service)
- Runtime: Docker
- Voeg env-variabelen toe onder *Environment*
- Create Worker

### Fly.io

```bash
fly launch --no-deploy        # genereert fly.toml, gebruikt de Dockerfile
fly secrets set TWELVEDATA_KEY=... TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo
fly deploy
```

In `fly.toml` het `[http_service]`-blok verwijderen — de bot heeft geen inkomend verkeer nodig.

### Eigen VPS (Docker)

```bash
git clone <deze-repo> && cd XAU-USD
docker build -t xau-bot .
docker run -d --restart unless-stopped --name xau-bot --env-file .env xau-bot
```

`--restart unless-stopped` zorgt dat de bot herstart na een crash of reboot.

## Controleren of het werkt

Bij een succesvolle start stuurt de bot dit bericht in Telegram:

```
🚀 XAUUSD ORB Bot is online!
Type /help voor alle commando's.
```

Zie je dat niet, check dan de container-logs (`docker logs xau-bot` of het log-tabblad van je service) op ontbrekende env-variabelen.
