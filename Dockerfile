FROM python:3.12-slim
LABEL authors="charlesdegreauwe"

# env vars
ENV TZ=Europe/Brussels
ENV PYTHONUNBUFFERED=1

# effectieve run
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]