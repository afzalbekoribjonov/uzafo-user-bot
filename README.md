# Telegram Broadcast Bot

## Lokal ishga tushirish

1. `python -m venv .venv`
2. `.venv\Scripts\activate` yoki `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `.env` faylini to'ldiring
5. `python bot.py`

## Render webhook sozlamasi

`.env` ichiga quyidagilarni kiriting:

```env
USE_WEBHOOK=true
PORT=10000
WEBHOOK_BASE_URL=https://your-service.onrender.com
WEBHOOK_PATH=/telegram/webhook
WEBHOOK_SECRET=change_me
```

Render avtomatik `PORT` beradi. Bot webhook rejimida `0.0.0.0:$PORT` portda ishga tushadi.

## Asosiy imkoniyatlar

- Telegram akkauntini ulash
- Faqat guruhlarni skanerlash
- Tanlangan guruhlarga xabar yuborish
- Admin panel va kalit boshqaruvi
- Faol jarayonlarni boshqarish
- Webhook orqali Render'da ishlash
