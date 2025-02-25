import requests
import time
import json
import os
import paramiko
from datetime import datetime
from netmiko import ConnectHandler
import ftplib
import re

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

def create_backup(mikrotik):
    try:
        backup_name = f"{mikrotik['name']}-Backup-{datetime.now().strftime('%Y%m%d-%H%M')}"
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
        }

        print(f"Підключення до {mikrotik['host']}...")  # Логування підключення
        with ConnectHandler(**device) as ssh_conn:
            print(f"Підключено до {mikrotik['host']}. Створення бекапу...")  # Логування успішного підключення
            ssh_conn.send_command(f'/system backup save name={backup_name}')
            time.sleep(3)
            ssh_conn.send_command(f'/export file={backup_name}')
            time.sleep(1)
        return backup_name
    except Exception as e:
        error_message = f"Помилка авторизації на #{mikrotik['name']} ({mikrotik['host']}): {e}"
        print(error_message)  # Логування помилки
        send_telegram_message(error_message)
        return None

def download_backup(mikrotik, backup_name):
    try:
        # Створення основної директорії та папки з ім'ям мікротика
        mikrotik_dir = os.path.join(BACKUP_DIR, mikrotik['name'])
        os.makedirs(mikrotik_dir, exist_ok=True)

        # Локальні шляхи для файлів .backup і .rsc
        local_backup = os.path.join(mikrotik_dir, f"{backup_name}.backup")
        local_rsc = os.path.join(mikrotik_dir, f"{backup_name}.rsc")

        print(f"Завантаження бекапу з {mikrotik['host']}...")  # Логування завантаження
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(mikrotik['host'], username=mikrotik['user'], password=mikrotik['password'])

        sftp = ssh.open_sftp()
        sftp.get(f"/{backup_name}.backup", local_backup)
        time.sleep(3)
        sftp.get(f"/{backup_name}.rsc", local_rsc)
        time.sleep(1)
        sftp.close()
        ssh.close()

        return local_backup, local_rsc
    except Exception as e:
        error_message = f"Помилка авторизації або завантаження на #{mikrotik['name']} ({mikrotik['host']}): {e}"
        print(error_message)  # Логування помилки
        send_telegram_message(error_message)
        return None, None


def upload_backup_to_ftp(local_file, backup_name, ftp_config, file_type='backup'):
    try:
        print(f"Завантаження на FTP {backup_name}...")  # Логування FTP
        ftp = ftplib.FTP(ftp_config['host'])
        ftp.login(ftp_config['user'], ftp_config['password'])
        remote_dir = f"{ftp_config['dir']}/{backup_name.split('-')[0]}"

        try:
            ftp.mkd(remote_dir)
        except ftplib.error_perm:
            pass  # Папка вже існує

        remote_file = f"{remote_dir}/{backup_name}.{file_type}"
        with open(local_file, 'rb') as file:
            ftp.storbinary(f"STOR {remote_file}", file)

        ftp.quit()
        return True
    except Exception as e:
        error_message = f"Помилка завантаження на FTP {backup_name}: {e}"
        print(error_message)  # Логування помилки
        send_telegram_message(error_message)
        return False

def delete_old_backups(mikrotik, keep_count=2):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
        }

        with ConnectHandler(**device) as ssh_conn:
            backups = ssh_conn.send_command('/file print')

            # Логуємо вхідний список файлів
            print(f"📜 Отриманий список файлів:\n{backups}")

            backup_files = []
            for line in backups.splitlines():
                match = re.search(r'(\S+\.backup|\S+\.rsc)', line)  # Знаходимо файли з розширеннями
                if match:
                    backup_files.append(match.group(1))

            print(f"📂 Вибрані файли для аналізу: {backup_files}")

            if not backup_files:
                print(f"⚠ На {mikrotik['name']} немає файлів для видалення.")
                return False

            # Функція для витягування дати
            def extract_datetime(file_name):
                match = re.search(r'(\d{8}-\d{4})', file_name)  # YYYYMMDD-HHMM
                if match:
                    try:
                        dt = datetime.strptime(match.group(1), "%Y%m%d-%H%M")
                        return dt
                    except ValueError:
                        print(f"⚠ Помилка парсингу дати у файлі: {file_name}")
                        return None
                print(f"⚠ Дата не знайдена у файлі: {file_name}")
                return None

            # Формуємо список кортежів (ім'я файлу, дата)
            backup_files_with_dates = [(file, extract_datetime(file)) for file in backup_files]

            # Видаляємо файли, у яких не вдалося отримати дату
            backup_files_with_dates = [item for item in backup_files_with_dates if item[1] is not None]

            if not backup_files_with_dates:
                print(f"⚠ Жоден файл не має правильної дати! Перевір регулярний вираз.")
                return False

            # Сортуємо за датою
            backup_files_with_dates.sort(key=lambda x: x[1])

            # Логуємо відсортований список
            sorted_files = "\n".join(f"{file} ({date})" for file, date in backup_files_with_dates)
            print(f"📂 Відсортований список файлів на {mikrotik['name']}:\n{sorted_files}")

            # Визначаємо файли для видалення (залишаємо keep_count найновіших)
            files_to_delete = backup_files_with_dates[:-keep_count]

            if not files_to_delete:
                print(f"✅ На {mikrotik['name']} немає файлів для видалення.")
                return False

            for file, date in files_to_delete:
                print(f"❌ Видаляємо файл: {file} ({date})")
                ssh_conn.send_command(f'/file remove "{file}"')

            return True
    except Exception as e:
        error_message = f"Помилка видалення бекапів на {mikrotik['name']} ({mikrotik['host']}): {e}"
        print(error_message)
        return False


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
    send_telegram_message(f"🔹 Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    failed_mikrotiks = []

    for idx, mikrotik in enumerate(config['mikrotiks'], start=1):
        if attempt_connection(mikrotik):  # Перевірка підключення
            backup_name = create_backup(mikrotik)
            if backup_name:
                local_backup, local_rsc = download_backup(mikrotik, backup_name)
                if local_backup and local_rsc:
                    upload_backup_to_ftp(local_backup, backup_name, config['ftp'], 'backup')
                    upload_backup_to_ftp(local_rsc, backup_name, config['ftp'], 'rsc')
                    delete_old_backups(mikrotik)

                update_message = check_and_update_mikrotik(mikrotik)

                send_telegram_message(f"🔹 #{idx} *#{mikrotik['name']}* ({mikrotik['host']}):\n"
                                      f"✅ Бекап і RSC для #{mikrotik['name']} успішно створено та завантажено.\n"
                                      f"Назва файлів: \n*{backup_name}.backup*,\n*{backup_name}.rsc*\n"
                                      f"{update_message}")
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
