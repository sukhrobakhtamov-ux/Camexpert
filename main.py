import os
import json
import hmac
import html
import logging
import threading

import telebot
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("netbot")

# ---------- Настройки ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
PORT = int(os.environ.get("PORT", 8080))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "net_state.json")
FIELDS = ("wan_ip", "gateway", "local_ip", "wifi_name", "updated_at")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
state_lock = threading.Lock()


# ---------- Работа с файлом состояния ----------
def load_devices():
    """Возвращает словарь {имя_устройства: данные}."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("devices"), dict):
        return data["devices"]
    if "wan_ip" in data:  # старый формат (один телефон)
        return {"phone": data}
    return {}


def save_devices(devices):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"devices": devices}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def esc(value):
    return html.escape(str(value)) if value not in (None, "") else "—"


# ---------- Flask ----------
@app.route("/", methods=["GET"])
def index():
    return "ok", 200


@app.route("/update", methods=["POST"])
def update():
    secret = request.headers.get("X-Secret", "")
    if not hmac.compare_digest(secret.encode(), WEBHOOK_SECRET.encode()):
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid json"}), 400

    device = str(payload.get("device") or "phone").strip()[:32] or "phone"
    new_data = {k: payload.get(k) for k in FIELDS}

    with state_lock:
        devices = load_devices()
        old_data = devices.get(device) or {}
        devices[device] = new_data
        save_devices(devices)

    old_ip = old_data.get("wan_ip")
    new_ip = new_data.get("wan_ip")

    if old_ip and new_ip and old_ip != new_ip:
        text = (
            "⚠️ <b>Внешний IP изменился!</b>\n"
            f"📱 Устройство: <b>{esc(device)}</b>\n\n"
            f"Был: <code>{esc(old_ip)}</code>\n"
            f"Стал: <code>{esc(new_ip)}</code>\n"
            f"Сеть: {esc(new_data.get('wifi_name'))}\n"
            f"Время: {esc(new_data.get('updated_at'))}"
        )
        try:
            bot.send_message(ADMIN_ID, text)
        except Exception as e:
            log.error("Не удалось отправить уведомление: %s", e)

    return jsonify({"status": "ok"}), 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ---------- Telegram ----------
def is_admin(message):
    return message.from_user and message.from_user.id == ADMIN_ID


@bot.message_handler(commands=["start", "info"])
def cmd_info(message):
    if not is_admin(message):
        return
    devices = load_devices()
    if not devices:
        bot.send_message(message.chat.id, "Данных пока нет. Телефоны ещё не присылали обновление.")
        return

    blocks = []
    for name in sorted(devices):
        d = devices[name]
        blocks.append(
            f"📱 <b>{esc(name)}</b>\n"
            f"🔹 Внешний IP: <code>{esc(d.get('wan_ip'))}</code>\n"
            f"🔹 IP роутера: <code>{esc(d.get('gateway'))}</code>\n"
            f"🔹 Локальный IP: <code>{esc(d.get('local_ip'))}</code>\n"
            f"🔹 Wi-Fi: <b>{esc(d.get('wifi_name'))}</b>\n"
            f"🕒 Обновлено: {esc(d.get('updated_at'))}"
        )
    bot.send_message(message.chat.id, "🌐 <b>Состояние сети</b>\n\n" + "\n\n".join(blocks))


if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    log.info("Flask запущен на порту %s", PORT)
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
