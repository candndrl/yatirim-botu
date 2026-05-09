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
CHAT_ID = os.environ.get("CHAT_ID", "")  # Sabah raporu gönderilecek Telegram chat ID

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYSTEM_PROMPT = """Sen uzman bir yatırım analisti asistanısın. Türkçe yanıt veriyorsun.

Görevin:
- Güncel haberleri ve piyasa verilerini analiz etmek
- Altın, gümüş, petrol, döviz, hisse senetleri hakkında yorum yapmak
- Geopolitik olayların (savaş, ambargo, açıklamalar) piyasalara etkisini açıklamak
- "Al / Sat / Bekle" tavsiyesi vermek ve gerekçesini açıklamak
- Geçmiş benzer olaylarla karşılaştırma yapmak

Yanıt formatın:
📊 VARLIK ADI
Yön: ▲ Yükseliş / ▼ Düşüş / ◆ Yatay
Güç: X/10
Tavsiye: AL / SAT / BEKLE
Gerekçe: (2-3 cümle)

⚠️ Her yanıtın sonuna "Bu yatırım tavsiyesi değil, bilgilendirme amaçlıdır." ekle.
Yanıtların kısa ve net olsun."""

chat_sessions = {}

def get_news():
    feeds = [
        "https://news.google.com/rss/search?q=altın+dolar+borsa+BIST+piyasa&hl=tr&gl=TR&ceid=TR:tr",
        "https://news.google.com/rss/search?q=gold+silver+oil+market+economy&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=BIST+borsa+istanbul+hisse&hl=tr&gl=TR&ceid=TR:tr",
    ]
    haberler = []
    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                haberler.append(f"- {entry.title}")
        except:
            pass
    return "\n".join(haberler[:15]) if haberler else "Haber çekilemedi."

def get_prices():
    symbols = {
        "Altın (XAU/USD)": "GC=F",
        "Gümüş (XAG/USD)": "SI=F",
        "Petrol (WTI)": "CL=F",
        "Dolar/TL": "TRY=X",
        "Euro/TL": "EURTRY=X",
        "BTC/USD": "BTC-USD",
        "BIST 100": "XU100.IS",
    }
    fiyatlar = []
    for name, symbol in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
            r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
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

def ask_gemini(user_id, user_message):
    prices = get_prices()
    news = get_news()
    if user_id not in chat_sessions:
        chat_sessions[user_id] = []

    context = f"""
{SYSTEM_PROMPT}

=== GÜNCEL PİYASA FİYATLARI ===
{prices}

=== SON DAKİKA HABERLERİ ===
{news}

=== KULLANICI SORUSU ===
{user_message}
"""
    chat_sessions[user_id].append({"role": "user", "parts": [context if len(chat_sessions[user_id]) == 0 else user_message]})
    if len(chat_sessions[user_id]) > 20:
        chat_sessions[user_id] = chat_sessions[user_id][-20:]

    try:
        chat = model.start_chat(history=chat_sessions[user_id][:-1])
        response = chat.send_message(context if len(chat_sessions[user_id]) == 1 else user_message)
        reply = response.text
        chat_sessions[user_id].append({"role": "model", "parts": [reply]})
        return reply
    except Exception as e:
        return f"❌ Hata oluştu: {str(e)}"

def generate_morning_report():
    prices = get_prices()
    news = get_news()
    bugun = datetime.now().strftime("%d.%m.%Y")

    prompt = f"""
{SYSTEM_PROMPT}

Bugün ({bugun}) için kapsamlı bir sabah yatırım raporu hazırla.
Şu varlıkların hepsini tek tek analiz et:
Altın, Gümüş, Petrol, Dolar/TL, Euro/TL, Bitcoin (BTC), BIST 100

=== GÜNCEL FİYATLAR ===
{prices}

=== GÜNCEL HABERLER ===
{news}

Raporu şu formatta yaz:

🌅 GÜNLÜK YATIRIM RAPORU - {bugun}

[Her varlık için format:]
📊 VARLIK ADI
Yön: ▲/▼/◆
Güç: X/10
Tavsiye: AL/SAT/BEKLE
Gerekçe: (1-2 cümle)

---

💡 GÜNÜN ÖNERİSİ:
En karlı görünen 1-2 varlığı kısaca özetle.

⚠️ Bu yatırım tavsiyesi değil, bilgilendirme amaçlıdır.
"""
    try:
        response = model.generate_content(prompt, request_options={"timeout": 25})
        return response.text
    except Exception as e:
        return f"❌ Rapor oluşturulamadı: {str(e)}"

def send_message(chat_id, text):
    if len(text) > 4000:
        text = text[:4000] + "\n...(devamı kesildi)"
    requests.post(f"{TELEGRAM_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    })

def morning_report_scheduler():
    """Her sabah 09:00 Türkiye saatinde (UTC+3 = UTC 06:00) rapor gönder"""
    while True:
        now = datetime.utcnow()
        if now.hour == 6 and now.minute == 0:
            chat_id = CHAT_ID
            if chat_id:
                report = generate_morning_report()
                send_message(chat_id, report)
            time.sleep(60)
        time.sleep(30)

# Arka planda zamanlayıcıyı başlat
scheduler_thread = threading.Thread(target=morning_report_scheduler, daemon=True)
scheduler_thread.start()

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data.get("message", {})
        chat_id = message["chat"]["id"]
        user_id = str(message["from"]["id"])
        text = message.get("text", "")

        if not text:
            return "ok"

        if text == "/start":
            send_message(chat_id,
                "👋 *Yatırım Asistanına Hoş Geldin!*\n\n"
                "Güncel haber ve fiyatları analiz ederek yatırım yorumu yapabilirim.\n\n"
                "📌 *Komutlar:*\n"
                "• /fiyatlar — Anlık piyasa fiyatları\n"
                "• /haberler — Son dakika haberleri\n"
                "• /rapor — Hemen sabah raporu al\n\n"
                "📌 *Örnek sorular:*\n"
                "• Bugün altın almalı mıyım?\n"
                "• Trump'ın açıklaması piyasaları nasıl etkiler?\n"
                "• Hangi emtiayı al?\n\n"
                "🌅 Her sabah 09:00'da otomatik rapor gelir!\n\n"
                "⚠️ Yanıtlarım bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."
            )
            return "ok"

        if text == "/fiyatlar":
            send_message(chat_id, "📊 *Güncel Piyasa Fiyatları*\n\n" + get_prices())
            return "ok"

        if text == "/haberler":
            send_message(chat_id, "📰 *Son Dakika Haberleri*\n\n" + get_news())
            return "ok"

        if text == "/rapor":
            send_message(chat_id, "⏳ Rapor hazırlanıyor, bekle...")
            report = generate_morning_report()
            send_message(chat_id, report)
            return "ok"

        send_message(chat_id, "⏳ Analiz ediliyor...")
        reply = ask_gemini(user_id, text)
        send_message(chat_id, reply)

    except Exception as e:
        print(f"Hata: {e}")

    return "ok"

@app.route("/")
def home():
    return "✅ Yatırım Asistanı Bot çalışıyor!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
