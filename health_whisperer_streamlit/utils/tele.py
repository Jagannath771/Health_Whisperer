# utils/tele.py
import os, requests

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_tg(chat_id: str, text: str, disable_web_page_preview=True):
    if not BOT_TOKEN or not chat_id:
        return None
    try:
        r = requests.post(f"{BASE}/sendMessage", json={
            "chat_id": chat_id, "text": text, "disable_web_page_preview": disable_web_page_preview
        }, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}
