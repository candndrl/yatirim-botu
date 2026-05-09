import os
import requests
import feedparser
import threading
import time
from datetime import datetime
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash-preview-04-17")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

chat_sessions = {}

def get_news():
    feeds = [
        "https://news.google.com/rss/search?q=altın+dolar+borsa+BIST+piyasa&hl=tr&gl=TR&ceid=TR:tr",
        "https://news.google.com/rss/search?q=gold+silver+oil+market&hl=en&gl=US&ceid=US:en",
    ]
    haberler = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:4]:
                haberler.append(f"- {entry.title}")
        except:
            pass
    return "\n".join(haberler[:10]) if haberler else "Haber alınamadı."

def get_prices():
    symbols = {
        "Altın": "GC=F",
        "Gümüş": "SI=F",
        "Petrol": "CL=F",
        "Dolar/TL": "TRY=X",
        "Euro/TL": "EURTRY=X",
        "BTC": "BTC-USD",
        "BIST 100": "XU100.IS",
    }
    fiyatlar = []
    for name, symbol in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
            r = requests.get(url, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
            data = r.json()
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                bugun = closes[-1]
                dun = closes[-2]
                degisim = ((bugun - dun) / dun) * 100
                yon = "▲" if degisim > 0 else "▼"
                fiyatlar.append(f"{yon} {name}: {bugun:.2f} ({degisim:+.2f}%)")
            elif len(closes) == 1:
                fiyatlar.append(f"◆ {name}: {closes[-1]:.2f}")
        except:
            fiyatlar.append(f"◆ {name}: veri alınamadı")
    return "\n".join(fiyatlar)

def send_message(chat_id, text):
    if len(text) > 4000:
        text = text[:4000] + "\n...(kesildi)"
    try:
        requests.post(f"{TELEGRAM_URL}/sendMessage", json={
            "chat_id": chat_id,
            "text": text
        }, timeout=10)
    except:
        pass

def generate_and_send_report(chat_id):
    """Arka planda rapor oluştur ve gönder"""
    try:
        prices = get_prices()
        news = get_news()
        bugun = datetime.now().strftime("%d.%m.%Y")

        prompt = f"""Sen uzman bir yatırım analistisin. Türkçe yaz. Kısa ve net ol.

Bugün ({bugun}) için şu varlıkları analiz et: Altın, Gümüş, Petrol, Dolar/TL, Euro/TL, BTC, BIST 100

FİYATLAR:
{prices}

HABERLER:
{news}

Her varlık için şu formatta yaz:
📊 VARLIK
Yön: ▲/▼/◆ | Güç: X/10 | Tavsiye: AL/SAT/BEKLE
Gerekçe: (1 cümle)

En sona: 💡 GÜNÜN ÖNERİSİ: (en iyi 1-2 varlık)
⚠️ Yatırım tavsiyesi değildir."""

        response = model.generate_content(prompt)
        report = f"🌅 SABAH RAPORU - {bugun}\n\n" + response.text
        send_message(chat_id, report)
    except Exception as e:
        send_message(chat_id, f"❌ Rapor hatası: {str(e)}")

def morning_report_scheduler():
    while True:
        now = datetime.utcnow()
        if now.hour == 6 and now.minute == 0:
            if CHAT_ID:
                threading.Thread(target=generate_and_send_report, args=(CHAT_ID,), daemon=True).start()
            time.sleep(61)
        time.sleep(20)

scheduler_thread = threading.Thread(target=morning_report_scheduler, daemon=True)
scheduler_thread.start()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data.get("message", {})
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if not text:
            return "ok"

        if text == "/start":
            send_message(chat_id,
                "👋 Yatırım Asistanına Hoş Geldin!\n\n"
                "📌 Komutlar:\n"
                "• /fiyatlar — Anlık fiyatlar\n"
                "• /haberler — Son haberler\n"
                "• /rapor — Hemen rapor al\n\n"
                "🌅 Her sabah 09:00'da otomatik rapor gelir!\n\n"
                "Soru da sorabilirsin, örn: 'Bugün altın almalı mıyım?'\n\n"
                "⚠️ Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."
            )
            return "ok"

        if text == "/fiyatlar":
            send_message(chat_id, "📊 Güncel Fiyatlar\n\n" + get_prices())
            return "ok"

        if text == "/haberler":
            send_message(chat_id, "📰 Son Haberler\n\n" + get_news())
            return "ok"

        if text == "/rapor":
            send_message(chat_id, "⏳ Rapor hazırlanıyor, 30-40 saniye bekle...")
            threading.Thread(target=generate_and_send_report, args=(chat_id,), daemon=True).start()
            return "ok"

        # Normal soru
        send_message(chat_id, "⏳ Analiz ediliyor...")
        threading.Thread(target=ask_and_send, args=(chat_id, str(message["from"]["id"]), text), daemon=True).start()

    except Exception as e:
        print(f"Hata: {e}")

    return "ok"

def ask_and_send(chat_id, user_id, user_message):
    try:
        prices = get_prices()
        news = get_news()

        prompt = f"""Sen uzman bir yatırım analistisin. Türkçe yaz. Kısa ve net ol.

FİYATLAR:
{prices}

HABERLER:
{news}

SORU: {user_message}

Analiz formatı:
📊 VARLIK ADI
Yön: ▲/▼/◆ | Güç: X/10 | Tavsiye: AL/SAT/BEKLE
Gerekçe: (2-3 cümle)

⚠️ Yatırım tavsiyesi değildir."""

        response = model.generate_content(prompt)
        send_message(chat_id, response.text)
    except Exception as e:
        send_message(chat_id, f"❌ Hata: {str(e)}")

@app.route("/")
def home():
    return "✅ Bot çalışıyor!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
