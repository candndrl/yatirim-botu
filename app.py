import os
import requests
import feedparser
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# === API KEY'LERİ BURAYA YAZ (Render'da Environment Variable olarak ekle) ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "BURAYA_YAZ")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "BURAYA_YAZ")
# ============================================================================

# Gemini ayarla
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

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

Yanıtların kısa ve net olsun. Kullanıcı birden fazla varlık sorarsa hepsini listele."""

# Kullanıcı sohbet geçmişleri
chat_sessions = {}

def get_news():
    """Google News'ten güncel finans haberlerini çek"""
    feeds = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F,SI=F,CL=F&region=US&lang=en-US",
        "https://news.google.com/rss/search?q=altın+dolar+borsa+piyasa&hl=tr&gl=TR&ceid=TR:tr",
        "https://news.google.com/rss/search?q=gold+silver+oil+market+economy&hl=en&gl=US&ceid=US:en",
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
    """Yahoo Finance'ten güncel fiyatları çek"""
    symbols = {
        "Altın (XAU/USD)": "GC=F",
        "Gümüş (XAG/USD)": "SI=F", 
        "Petrol (WTI)": "CL=F",
        "Dolar/TL": "TRY=X",
        "Euro/TL": "EURTRY=X",
        "BTC/USD": "BTC-USD",
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
    """Gemini'ye sor - güncel haber ve fiyatlarla birlikte"""
    
    # Güncel veri çek
    prices = get_prices()
    news = get_news()
    
    # Kullanıcı geçmişini al
    if user_id not in chat_sessions:
        chat_sessions[user_id] = []
    
    # Bağlam oluştur
    context = f"""
{SYSTEM_PROMPT}

=== GÜNCEL PİYASA FİYATLARI ===
{prices}

=== SON DAKİKA HABERLERİ ===
{news}

=== KULLANICI SORUSU ===
{user_message}
"""
    
    # Sohbet geçmişine ekle
    chat_sessions[user_id].append({
        "role": "user",
        "parts": [context if len(chat_sessions[user_id]) == 0 else user_message]
    })
    
    # Geçmişi sınırla
    if len(chat_sessions[user_id]) > 20:
        chat_sessions[user_id] = chat_sessions[user_id][-20:]
    
    try:
        chat = model.start_chat(history=chat_sessions[user_id][:-1])
        response = chat.send_message(
            context if len(chat_sessions[user_id]) == 1 else user_message
        )
        reply = response.text
        
        # Cevabı geçmişe ekle
        chat_sessions[user_id].append({
            "role": "model",
            "parts": [reply]
        })
        
        return reply
    except Exception as e:
        return f"❌ Hata oluştu: {str(e)}"

def send_message(chat_id, text):
    """Telegram'a mesaj gönder"""
    # Telegram 4096 karakter limiti var, uzunsa böl
    if len(text) > 4000:
        text = text[:4000] + "\n...(devamı kesildi)"
    
    requests.post(f"{TELEGRAM_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    })

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
        
        # Komutlar
        if text == "/start":
            send_message(chat_id, 
                "👋 *Yatırım Asistanına Hoş Geldin!*\n\n"
                "Güncel haber ve fiyatları analiz ederek yatırım yorumu yapabilirim.\n\n"
                "📌 *Örnek sorular:*\n"
                "• Bugün altın almalı mıyım?\n"
                "• Trump'ın açıklaması piyasaları nasıl etkiler?\n"
                "• Dolar/TL için beklentin ne?\n"
                "• Hangi emtiayı al?\n\n"
                "⚠️ Yanıtlarım bilgilendirme amaçlıdır, yatırım tavsiyesi değildir."
            )
            return "ok"
        
        if text == "/fiyatlar":
            send_message(chat_id, "📊 *Güncel Piyasa Fiyatları*\n\n" + get_prices())
            return "ok"
        
        if text == "/haberler":
            send_message(chat_id, "📰 *Son Dakika Haberleri*\n\n" + get_news())
            return "ok"
        
        # Normal soru → Gemini'ye sor
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
