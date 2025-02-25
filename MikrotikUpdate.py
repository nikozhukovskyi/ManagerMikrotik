import requests
import time
import json
from datetime import datetime
from netmiko import ConnectHandler


# Завантажуємо конфігурацію
CONFIG_FILE = './config.json'
CHAT_IDS_FILE = './chat_ids.json'
BACKUP_DIR = './BackUp/'

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

TELEGRAM_BOT_TOKEN = config['telegram_token']

def load_chat_ids():
    try:
        with open(CHAT_IDS_FILE, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def send_telegram_message(message):
    chat_ids = load_chat_ids()
    if not chat_ids:
        print("Не знайдено жодного chat_id для відправки повідомлення.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in chat_ids:
        print(f"Відправка повідомлення до чату: {chat_id}")
        message = message.replace('*', '')  # Прибрати жирний шрифт
        message = message.replace('_', '')  # Прибрати курсив
        response = requests.get(url, params={'chat_id': chat_id, 'text': message.strip()})
        print(f"Telegram API відповідь: {response.status_code}, {response.text}")


def attempt_connection(mikrotik, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            device = {
                "device_type": "mikrotik_routeros",
                "host": mikrotik['host'],
                "username": mikrotik['user'],
                "password": mikrotik['password'],
            }

            with ConnectHandler(**device) as ssh_conn:
                print(f"Підключено до {mikrotik['host']}")
                return True
        except Exception as e:
            print(f"Помилка підключення до {mikrotik['host']} (спроба {attempt}): {e}")
            if attempt < max_retries:
                print("Зачекайте 3 секунд перед повторною спробою...")
                time.sleep(3)

    return False

def check_and_update_mikrotik(mikrotik):
        try:
            device = {
                "device_type": "mikrotik_routeros",
                "host": mikrotik['host'],
                "username": mikrotik['user'],
                "password": mikrotik['password'],
            }

            with ConnectHandler(**device) as ssh_conn:
                output = ssh_conn.send_command('/system package update check-for-updates')
                installed_version = next(
                    (line.split(':')[1].strip() for line in output.splitlines() if "installed-version" in line), None)
                latest_version = next(
                    (line.split(':')[1].strip() for line in output.splitlines() if "latest-version" in line), None)
                if installed_version and latest_version:
                    if tuple(map(int, installed_version.split('.'))) < tuple(map(int, latest_version.split('.'))):
                        ssh_conn.send_command('/system package update install')
                        return f"Оновлення для MikroTik *#{mikrotik['name']}* завершено."
                    else:
                        return f"MikroTik *#{mikrotik['name']}* має актуальну версію."
        except Exception as e:
            send_telegram_message(f"Помилка при оновленні #{mikrotik['name']}: {e}")
            return f"Помилка при оновленні #{mikrotik['name']}."

if __name__ == "__main__":
    send_telegram_message(f"🔹 Розпочато планові Оновлення! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    failed_mikrotiks = []

    for idx, mikrotik in enumerate(config['mikrotiks'], start=1):
        if attempt_connection(mikrotik):  # Перевірка підключення

                update_message = check_and_update_mikrotik(mikrotik)

                send_telegram_message(f"🔹 #{idx} *#{mikrotik['name']}* ({mikrotik['host']}):\n"
                                      f"✅{update_message}")
        else:
            failed_mikrotiks.append(mikrotik)
        time.sleep(5)

    # Повторна спроба для тих, хто не підключився
    if failed_mikrotiks:
        send_telegram_message(f"🔴 Повторні спроби підключення до MikroTik, які не вдалися:")
        for mikrotik in failed_mikrotiks:
            send_telegram_message(f"Повторна спроба підключення до {mikrotik['host']}...")
            if attempt_connection(mikrotik):
                send_telegram_message(f"✅ Підключення до {mikrotik['host']} вдалося після повторної спроби.")
            else:
                send_telegram_message(f"❌ Не вдалося підключитись до {mikrotik['host']} після повторної спроби.")

    send_telegram_message(f"✅ Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
