import os
import requests
import feedparser
import threading
import time
from datetime import datetime
from flask import Flask, request
import google.generativeai as genai
from groq import Groq

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel("gemini-2.5-flash")
groq_client = Groq(api_key=GROQ_API_KEY)

TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Son gönderilen haberleri takip et (aynı haberi tekrar gönderme)
sent_alerts = set()
last_prices = {}

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
    fiyat_dict = {}
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
                fiyat_dict[name] = {"fiyat": bugun, "degisim": degisim}
            elif len(closes) == 1:
                fiyatlar.append(f"◆ {name}: {closes[-1]:.2f}")
                fiyat_dict[name] = {"fiyat": closes[-1], "degisim": 0}
        except:
            fiyatlar.append(f"◆ {name}: veri alınamadı")
    return "\n".join(fiyatlar), fiyat_dict

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

def ask_groq(prompt):
    """Groq ile hızlı analiz"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Groq hatası: {str(e)}"

def check_price_alerts():
    """Sert fiyat değişimlerini kontrol et ve bildir"""
    global last_prices
    try:
        _, fiyat_dict = get_prices()

        for varlik, veri in fiyat_dict.items():
            degisim = veri["degisim"]
            # %2'den fazla değişim = önemli hareket
            if abs(degisim) >= 2:
                alert_key = f"{varlik}_{datetime.now().strftime('%Y%m%d%H')}"
                if alert_key not in sent_alerts:
                    sent_alerts.add(alert_key)
                    yon = "🚀 YUKARI" if degisim > 0 else "🔴 AŞAĞI"
                    prompt = f"""Sen uzman yatırım analistisin. Türkçe, kısa ve net yaz.

{varlik} şu an {degisim:+.2f}% hareket etti. Bu önemli bir değişim.

Şu soruları yanıtla:
1. Bu hareket neden olabilir? (1-2 cümle)
2. Kısa vadeli beklenti ne? (AL/SAT/BEKLE)
3. Dikkat edilmesi gereken seviyeler?

⚠️ Yatırım tavsiyesi değildir."""

                    analiz = ask_groq(prompt)
                    mesaj = f"⚡ ANI FİYAT HAREKETİ\n\n{yon} HAREKET: {varlik}\nDeğişim: {degisim:+.2f}%\nFiyat: {veri['fiyat']:.2f}\n\n{analiz}"
                    send_message(CHAT_ID, mesaj)

    except Exception as e:
        print(f"Fiyat alert hatası: {e}")

def check_news_alerts():
    """Önemli haberleri kontrol et ve bildir"""
    try:
        feeds = [
            "https://news.google.com/rss/search?q=merkez+bankası+faiz+karar&hl=tr&gl=TR&ceid=TR:tr",
            "https://news.google.com/rss/search?q=fed+rate+decision+market&hl=en&gl=US&ceid=US:en",
            "https://news.google.com/rss/search?q=savaş+ambargo+petrol+altın&hl=tr&gl=TR&ceid=TR:tr",
            "https://news.google.com/rss/search?q=war+sanctions+oil+gold+market+crash&hl=en&gl=US&ceid=US:en",
        ]

        anahtar_kelimeler = [
            "faiz", "merkez bankası", "fed", "savaş", "ambargo", "kriz",
            "çöküş", "rekor", "acil", "rate", "war", "sanction", "crash",
            "emergency", "record", "ban", "trump", "erdoğan"
        ]

        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:3]:
                    baslik = entry.title.lower()
                    haber_id = entry.get("id", entry.title)[:100]

                    if haber_id in sent_alerts:
                        continue

                    if any(kelime in baslik for kelime in anahtar_kelimeler):
                        sent_alerts.add(haber_id)

                        prompt = f"""Sen uzman yatırım analistisin. Türkçe, kısa ve net yaz.

Önemli haber: "{entry.title}"

Bu haberin piyasalara etkisini analiz et:
1. Hangi varlıklar etkilenir? (altın, dolar, borsa, petrol vs.)
2. Kısa vadeli beklenti? (yükselir mi, düşer mi?)
3. Tavsiye: AL/SAT/BEKLE

⚠️ Yatırım tavsiyesi değildir."""

                        analiz = ask_groq(prompt)
                        mesaj = f"📰 ÖNEMLİ HABER ALARMI\n\n{entry.title}\n\n{analiz}"
                        send_message(CHAT_ID, mesaj)
                        time.sleep(2)  # Çok hızlı gönderme
            except:
                pass
    except Exception as e:
        print(f"Haber alert hatası: {e}")

def generate_and_send_report(chat_id):
    """Gemini ile sabah raporu"""
    try:
        prices_text, _ = get_prices()
        news = get_news()
        bugun = datetime.now().strftime("%d.%m.%Y")

        prompt = f"""Sen uzman bir yatırım analistisin. Türkçe yaz. Kısa ve net ol.

Bugün ({bugun}) için şu varlıkları analiz et: Altın, Gümüş, Petrol, Dolar/TL, Euro/TL, BTC, BIST 100

FİYATLAR:
{prices_text}

HABERLER:
{news}

Her varlık için şu formatta yaz:
📊 VARLIK
Yön: ▲/▼/◆ | Güç: X/10 | Tavsiye: AL/SAT/BEKLE
Gerekçe: (1 cümle)

En sona: 💡 GÜNÜN ÖNERİSİ: (en iyi 1-2 varlık)
⚠️ Yatırım tavsiyesi değildir."""

        response = gemini.generate_content(prompt)
        report = f"🌅 SABAH RAPORU - {bugun}\n\n" + response.text
        send_message(chat_id, report)
    except Exception as e:
        send_message(chat_id, f"❌ Rapor hatası: {str(e)}")

def ask_and_send(chat_id, user_message):
    """Groq ile kullanıcı sorularını yanıtla"""
    try:
        prices_text, _ = get_prices()
        news = get_news()

        prompt = f"""Sen uzman bir yatırım analistisin. Türkçe yaz. Kısa ve net ol.

FİYATLAR:
{prices_text}

HABERLER:
{news}

SORU: {user_message}

Analiz formatı:
📊 VARLIK ADI
Yön: ▲/▼/◆ | Güç: X/10 | Tavsiye: AL/SAT/BEKLE
Gerekçe: (2-3 cümle)

⚠️ Yatırım tavsiyesi değildir."""

        reply = ask_groq(prompt)
        send_message(chat_id, reply)
    except Exception as e:
        send_message(chat_id, f"❌ Hata: {str(e)}")

def scheduler():
    """Ana zamanlayıcı"""
    while True:
        now = datetime.utcnow()

        # Sabah raporu: UTC 06:00 = Türkiye 09:00
        if now.hour == 6 and now.minute == 0:
            if CHAT_ID:
                threading.Thread(target=generate_and_send_report, args=(CHAT_ID,), daemon=True).start()
            time.sleep(61)

        # Her 30 dakikada fiyat değişimi kontrolü
        if now.minute in [0, 30] and now.second < 30:
            if CHAT_ID:
                threading.Thread(target=check_price_alerts, daemon=True).start()

        # Her saatte haber kontrolü
        if now.minute == 15 and now.second < 30:
            if CHAT_ID:
                threading.Thread(target=check_news_alerts, daemon=True).start()

        time.sleep(20)

scheduler_thread = threading.Thread(target=scheduler, daemon=True)
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
                "🌅 Her sabah 09:00'da otomatik rapor gelir!\n"
                "⚡ Sert fiyat hareketlerinde anlık bildirim gelir!\n"
                "📰 Önemli haberlerde anlık bildirim gelir!\n\n"
                "Soru da sorabilirsin!\n\n"
                "⚠️ Bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."
            )
            return "ok"

        if text == "/fiyatlar":
            prices_text, _ = get_prices()
            send_message(chat_id, "📊 Güncel Fiyatlar\n\n" + prices_text)
            return "ok"

        if text == "/haberler":
            send_message(chat_id, "📰 Son Haberler\n\n" + get_news())
            return "ok"

        if text == "/rapor":
            send_message(chat_id, "⏳ Rapor hazırlanıyor, 30-40 saniye bekle...")
            threading.Thread(target=generate_and_send_report, args=(chat_id,), daemon=True).start()
            return "ok"

        # Normal soru → Groq ile yanıtla
        send_message(chat_id, "⏳ Analiz ediliyor...")
        threading.Thread(target=ask_and_send, args=(chat_id, text), daemon=True).start()

    except Exception as e:
        print(f"Hata: {e}")

    return "ok"

@app.route("/")
def home():
    return "✅ Bot çalışıyor!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
