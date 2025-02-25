import requests
import time
import json

# Токен бота
TELEGRAM_BOT_TOKEN = '7582759423:AAF2BtSlpRTkaWMKMDJKwlB_jhVLTBSGGOc'


# Функція для отримання оновлень
def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {'offset': offset}
    response = requests.get(url, params=params)
    return response.json()


# Функція для збору chat_id
def collect_chat_ids():
    offset = None
    chat_ids = set()  # Використовуємо set для уникнення дублювання
    while True:
        updates = get_updates(offset)
        for update in updates.get("result", []):
            # Отримуємо chat_id
            chat_id = update['message']['from']['id']
            chat_ids.add(chat_id)

            # Оновлюємо offset для наступного запиту
            offset = update['update_id'] + 1
            print(f"Зібрано chat_id: {chat_id}")

        # Зберігаємо всі зібрані chat_id в файл
        with open('chat_ids.json', 'w') as file:
            json.dump(list(chat_ids), file)

        time.sleep(1)  # Затримка між запитами


# Запускаємо збір chat_id
collect_chat_ids()
