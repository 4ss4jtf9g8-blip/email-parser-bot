#!/usr/bin/env python3
"""
EMAIL PARSER — Telegram Bot
============================
Установка:
    pip install pytelegrambotapi requests beautifulsoup4 googlesearch-python

Запуск:
    python bot.py

Размещение бесплатно:
    - Railway.app
    - Render.com
    - Koyeb.com
"""

import os
import re
import csv
import io
import time
import threading
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ─── ТОКЕН ───
# 1. Напишите @BotFather в Telegram
# 2. /newbot → дайте имя → получите токен
# 3. Вставьте токен сюда или в переменную окружения BOT_TOKEN
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬТЕ_ТОКЕН_СЮДА")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Хранилище сессий пользователей
sessions = {}  # user_id -> session dict

def get_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = {
            "mode": None,
            "depth": 1,
            "pause": 0.8,
            "stop": False,
            "running": False,
            "results": [],
            "email_set": set(),
            "visited": set(),
            "errors": 0,
            "state": "idle",  # idle, waiting_url, waiting_kw, waiting_depth
        }
    return sessions[user_id]

# ═══════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════
def main_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("🔗 По URL"),
        KeyboardButton("🔍 По ключевому слову"),
        KeyboardButton("⚙️ Настройки"),
        KeyboardButton("📊 Статистика"),
        KeyboardButton("📥 Скачать CSV"),
        KeyboardButton("🗑 Очистить базу"),
    )
    return kb

def stop_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🛑 СТОП"))
    return kb

def depth_keyboard():
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(*[InlineKeyboardButton(str(i), callback_data=f"depth_{i}") for i in range(4)])
    return kb

def pause_keyboard():
    kb = InlineKeyboardMarkup(row_width=3)
    btns = [("Быстро (0.3с)", "0.3"), ("Норм (0.8с)", "0.8"), ("Медленно (2с)", "2.0")]
    kb.add(*[InlineKeyboardButton(t, callback_data=f"pause_{v}") for t, v in btns])
    return kb

# ═══════════════════════════════════════
#  PARSER CORE
# ═══════════════════════════════════════
def fetch_html(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception:
        return None

def extract_emails(html):
    return list(set(EMAIL_RE.findall(html)))

def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    base = urlparse(base_url)
    links = []
    for a in soup.find_all("a", href=True):
        try:
            full = urljoin(base_url, a["href"])
            p = urlparse(full)
            if p.netloc == base.netloc and p.scheme in ("http", "https"):
                links.append(full)
        except:
            pass
    return list(set(links))

def search_sites(keyword, num=10):
    try:
        from googlesearch import search
        results = list(search(keyword + " контакты", num_results=num, lang="ru", sleep_interval=1))
        return list({urlparse(u).scheme+"://"+urlparse(u).netloc for u in results})[:num]
    except:
        pass
    # Fallback: DuckDuckGo
    try:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(keyword + ' контакты email')}"
        html = fetch_html(url)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        urls = []
        for a in soup.select(".result__a"):
            href = a.get("href","")
            try:
                from urllib.parse import parse_qs, urlparse as up
                qs = parse_qs(up(href).query)
                real = qs.get("uddg",[None])[0]
                if real and real.startswith("http"):
                    urls.append(urlparse(real).scheme+"://"+urlparse(real).netloc)
            except:
                pass
        return list(dict.fromkeys(urls))[:num]
    except:
        return []

def crawl(sess, start_url, depth, chat_id, status_msg_id):
    queue = [(start_url, 0)]
    found = 0
    last_update = time.time()

    while queue and not sess["stop"]:
        url, d = queue.pop(0)
        if url in sess["visited"]:
            continue
        sess["visited"].add(url)

        html = fetch_html(url)
        if not html:
            sess["errors"] += 1
            continue

        emails = extract_emails(html)
        new_ones = [e for e in emails if e not in sess["email_set"]]
        for email in new_ones:
            sess["email_set"].add(email)
            domain = urlparse(url).netloc
            t = datetime.now().strftime("%H:%M:%S")
            sess["results"].append({"email": email, "domain": domain, "source": url, "time": t})
            found += 1

        if d < depth:
            links = extract_links(html, start_url)
            for link in links[:8]:
                if link not in sess["visited"]:
                    queue.append((link, d + 1))

        # Обновляем статус каждые 3 секунды
        if time.time() - last_update > 3:
            try:
                bot.edit_message_text(
                    f"⏳ <b>Сканирую...</b>\n\n"
                    f"🌐 <code>{url[:50]}...</code>\n"
                    f"📧 Найдено email: <b>{len(sess['results'])}</b>\n"
                    f"🔍 Сайтов: <b>{len(sess['visited'])}</b>\n"
                    f"❌ Ошибок: <b>{sess['errors']}</b>",
                    chat_id, status_msg_id
                )
                last_update = time.time()
            except:
                pass

        time.sleep(sess["pause"])

    return found


def run_parsing(user_id, chat_id, mode, targets, depth):
    sess = get_session(user_id)
    sess["running"] = True
    sess["stop"] = False
    sess["results"] = []
    sess["email_set"] = set()
    sess["visited"] = set()
    sess["errors"] = 0

    # Отправляем статусное сообщение
    msg = bot.send_message(chat_id,
        "⏳ <b>Запуск парсера...</b>",
        reply_markup=stop_keyboard()
    )
    status_id = msg.message_id

    try:
        if mode == "url":
            urls = targets
            bot.edit_message_text(
                f"⏳ <b>Старт.</b> Сайтов: {len(urls)}, глубина: {depth}",
                chat_id, status_id
            )
            for i, url in enumerate(urls):
                if sess["stop"]:
                    break
                try:
                    bot.edit_message_text(
                        f"⏳ <b>Сканирую [{i+1}/{len(urls)}]</b>\n"
                        f"🌐 <code>{url}</code>\n"
                        f"📧 Найдено: <b>{len(sess['results'])}</b>",
                        chat_id, status_id
                    )
                except:
                    pass
                crawl(sess, url, depth, chat_id, status_id)

        elif mode == "keyword":
            keyword = targets[0]
            try:
                bot.edit_message_text(f"🔍 Ищу сайты по: «<b>{keyword}</b>»...", chat_id, status_id)
            except:
                pass
            sites = search_sites(keyword, num=10)
            if not sites:
                bot.edit_message_text("❌ Сайты не найдены. Попробуйте другой запрос.", chat_id, status_id)
                return
            try:
                bot.edit_message_text(
                    f"✅ Найдено <b>{len(sites)}</b> сайтов. Начинаю сканирование...",
                    chat_id, status_id
                )
            except:
                pass
            time.sleep(1)
            for i, site in enumerate(sites):
                if sess["stop"]:
                    break
                try:
                    bot.edit_message_text(
                        f"⏳ <b>Сканирую [{i+1}/{len(sites)}]</b>\n"
                        f"🌐 <code>{site}</code>\n"
                        f"📧 Найдено: <b>{len(sess['results'])}</b>",
                        chat_id, status_id
                    )
                except:
                    pass
                crawl(sess, site, depth, chat_id, status_id)

    finally:
        sess["running"] = False
        total = len(sess["results"])
        stopped = " (остановлено)" if sess["stop"] else ""

        # Итоговое сообщение
        try:
            bot.edit_message_text(
                f"{'✅' if not sess['stop'] else '🛑'} <b>Готово{stopped}!</b>\n\n"
                f"📧 Email найдено: <b>{total}</b>\n"
                f"🔍 Сайтов просканировано: <b>{len(sess['visited'])}</b>\n"
                f"❌ Ошибок загрузки: <b>{sess['errors']}</b>",
                chat_id, status_id
            )
        except:
            pass

        if total > 0:
            # Отправляем превью первых 10
            preview = "\n".join(f"• <code>{r['email']}</code>" for r in sess["results"][:10])
            if total > 10:
                preview += f"\n<i>...и ещё {total-10}</i>"

            bot.send_message(
                chat_id,
                f"📋 <b>Найденные email:</b>\n\n{preview}\n\n"
                f"👇 Нажмите <b>📥 Скачать CSV</b> чтобы получить полный файл",
                reply_markup=main_keyboard()
            )
        else:
            bot.send_message(chat_id, "📭 Email адреса не найдены.", reply_markup=main_keyboard())

# ═══════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════
@bot.message_handler(commands=["start", "help"])
def cmd_start(msg):
    sess = get_session(msg.from_user.id)
    sess["state"] = "idle"
    bot.send_message(
        msg.chat.id,
        "👋 <b>EMAIL PARSER BOT</b>\n\n"
        "Собираю email-адреса с сайтов.\n\n"
        "🔗 <b>По URL</b> — укажите конкретные сайты\n"
        "🔍 <b>По ключевому слову</b> — найду сайты сам\n\n"
        "Выберите режим 👇",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "🔗 По URL")
def mode_url(msg):
    sess = get_session(msg.from_user.id)
    if sess["running"]:
        bot.send_message(msg.chat.id, "⚠️ Парсер уже работает. Нажмите 🛑 СТОП для остановки.")
        return
    sess["state"] = "waiting_url"
    sess["mode"] = "url"
    bot.send_message(
        msg.chat.id,
        "🔗 <b>Режим: По URL</b>\n\n"
        "Отправьте список сайтов — каждый с новой строки:\n\n"
        "<code>https://example.com\nhttps://company.ru\nhttps://agency.io</code>",
        reply_markup=telebot.types.ReplyKeyboardRemove()
    )

@bot.message_handler(func=lambda m: m.text == "🔍 По ключевому слову")
def mode_keyword(msg):
    sess = get_session(msg.from_user.id)
    if sess["running"]:
        bot.send_message(msg.chat.id, "⚠️ Парсер уже работает. Нажмите 🛑 СТОП для остановки.")
        return
    sess["state"] = "waiting_kw"
    sess["mode"] = "keyword"
    bot.send_message(
        msg.chat.id,
        "🔍 <b>Режим: По ключевому слову</b>\n\n"
        "Введите нишу или запрос, например:\n"
        "• <code>стоматология москва</code>\n"
        "• <code>веб студия екатеринбург</code>\n"
        "• <code>юридическая компания спб</code>",
        reply_markup=telebot.types.ReplyKeyboardRemove()
    )

@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки")
def settings(msg):
    sess = get_session(msg.from_user.id)
    bot.send_message(
        msg.chat.id,
        f"⚙️ <b>Настройки</b>\n\n"
        f"📏 Глубина сканирования: <b>{sess['depth']}</b>\n"
        f"⏱ Пауза между запросами: <b>{sess['pause']}с</b>\n\n"
        f"Выберите глубину (0 = только главная, 3 = весь сайт):",
        reply_markup=depth_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
def statistics(msg):
    sess = get_session(msg.from_user.id)
    if not sess["results"]:
        bot.send_message(msg.chat.id, "📭 База пуста. Запустите парсер.", reply_markup=main_keyboard())
        return

    domains = {}
    for r in sess["results"]:
        domains[r["domain"]] = domains.get(r["domain"], 0) + 1

    top = sorted(domains.items(), key=lambda x: -x[1])[:10]
    domain_text = "\n".join(f"  • <code>{d}</code> — <b>{c}</b>" for d, c in top)

    bot.send_message(
        msg.chat.id,
        f"📊 <b>Статистика</b>\n\n"
        f"📧 Уникальных email: <b>{len(sess['results'])}</b>\n"
        f"🌐 Сайтов просканировано: <b>{len(sess['visited'])}</b>\n"
        f"❌ Ошибок загрузки: <b>{sess['errors']}</b>\n\n"
        f"🏆 <b>Топ доменов:</b>\n{domain_text}",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "📥 Скачать CSV")
def download_csv(msg):
    sess = get_session(msg.from_user.id)
    if not sess["results"]:
        bot.send_message(msg.chat.id, "📭 База пуста. Сначала запустите парсер.", reply_markup=main_keyboard())
        return

    output = io.StringIO()
    output.write('\ufeff')  # BOM для Excel
    writer = csv.writer(output)
    writer.writerow(["Email", "Домен", "Источник URL", "Время"])
    for r in sess["results"]:
        writer.writerow([r["email"], r["domain"], r["source"], r["time"]])

    output.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"emails_{ts}.csv"

    bot.send_document(
        msg.chat.id,
        (filename, output.getvalue().encode("utf-8-sig")),
        caption=f"📧 <b>{len(sess['results'])} email-адресов</b>\n📁 {filename}",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "🗑 Очистить базу")
def clear_db(msg):
    sess = get_session(msg.from_user.id)
    if sess["running"]:
        bot.send_message(msg.chat.id, "⚠️ Сначала остановите парсер.", reply_markup=stop_keyboard())
        return
    sess["results"] = []
    sess["email_set"] = set()
    sess["visited"] = set()
    sess["errors"] = 0
    bot.send_message(msg.chat.id, "🗑 База очищена.", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🛑 СТОП")
def stop_parsing(msg):
    sess = get_session(msg.from_user.id)
    if sess["running"]:
        sess["stop"] = True
        bot.send_message(msg.chat.id, "🛑 Остановка...", reply_markup=main_keyboard())
    else:
        bot.send_message(msg.chat.id, "Парсер не запущен.", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data.startswith("depth_"))
def cb_depth(call):
    sess = get_session(call.from_user.id)
    d = int(call.data.split("_")[1])
    sess["depth"] = d
    desc = ["только главная страница", "главная + подстраницы", "2 уровня вглубь", "весь сайт (медленно)"][d]
    bot.edit_message_text(
        f"✅ Глубина установлена: <b>{d}</b> — {desc}\n\n⏱ Выберите скорость сканирования:",
        call.message.chat.id, call.message.message_id,
        reply_markup=pause_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("pause_"))
def cb_pause(call):
    sess = get_session(call.from_user.id)
    p = float(call.data.split("_")[1])
    sess["pause"] = p
    bot.edit_message_text(
        f"✅ <b>Настройки сохранены</b>\n\n"
        f"📏 Глубина: <b>{sess['depth']}</b>\n"
        f"⏱ Пауза: <b>{p}с</b>",
        call.message.chat.id, call.message.message_id
    )
    bot.send_message(call.message.chat.id, "Готово!", reply_markup=main_keyboard())

# ── Обработка текстового ввода (URL или ключевое слово) ──
@bot.message_handler(func=lambda m: True)
def handle_text(msg):
    sess = get_session(msg.from_user.id)
    state = sess.get("state", "idle")

    if state == "waiting_url":
        urls = [u.strip() for u in msg.text.split("\n") if u.strip()]
        urls = [u if u.startswith("http") else "https://"+u for u in urls]
        if not urls:
            bot.send_message(msg.chat.id, "❌ Список пустой. Отправьте хотя бы один URL.")
            return
        sess["state"] = "idle"
        bot.send_message(
            msg.chat.id,
            f"✅ Получено <b>{len(urls)}</b> сайтов.\n"
            f"⚙️ Глубина: <b>{sess['depth']}</b> | Пауза: <b>{sess['pause']}с</b>\n\n"
            f"Запускаю парсер...",
            reply_markup=stop_keyboard()
        )
        t = threading.Thread(target=run_parsing, args=(msg.from_user.id, msg.chat.id, "url", urls, sess["depth"]), daemon=True)
        t.start()

    elif state == "waiting_kw":
        keyword = msg.text.strip()
        if not keyword:
            bot.send_message(msg.chat.id, "❌ Введите ключевое слово.")
            return
        sess["state"] = "idle"
        bot.send_message(
            msg.chat.id,
            f"✅ Запрос: «<b>{keyword}</b>»\n"
            f"⚙️ Глубина: <b>{sess['depth']}</b> | Пауза: <b>{sess['pause']}с</b>\n\n"
            f"Ищу сайты и запускаю парсер...",
            reply_markup=stop_keyboard()
        )
        t = threading.Thread(target=run_parsing, args=(msg.from_user.id, msg.chat.id, "keyword", [keyword], sess["depth"]), daemon=True)
        t.start()

    else:
        bot.send_message(msg.chat.id, "Выберите действие 👇", reply_markup=main_keyboard())

# ═══════════════════════════════════════
#  START
# ═══════════════════════════════════════
if __name__ == "__main__":
    print("=" * 45)
    print("  EMAIL PARSER BOT — запуск")
    print("=" * 45)
    if BOT_TOKEN == "ВСТАВЬТЕ_ТОКЕН_СЮДА":
        print("\n[!] Вставьте токен бота в переменную BOT_TOKEN\n")
    else:
        print(f"\n✅ Бот запущен. Найдите его в Telegram.\n")
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
