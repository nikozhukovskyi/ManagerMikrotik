import requests
import time
import json
import os
import paramiko
from datetime import datetime
from netmiko import ConnectHandler
import ftplib

# Завантажуємо конфігурацію
CONFIG_FILE = 'config.json'
CHAT_IDS_FILE = 'chat_ids.json'
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
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in chat_ids:
        requests.get(url, params={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown'})

def create_backup(mikrotik):
    try:
        backup_name = f"{mikrotik['name']}-Backup-{datetime.now().strftime('%Y%m%d-%H%M')}"
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
        }

        with ConnectHandler(**device) as ssh_conn:
            ssh_conn.send_command(f'/system backup save name={backup_name}')
            time.sleep(3)
            ssh_conn.send_command(f'/export file={backup_name}')
            time.sleep(1)
        return backup_name
    except Exception as e:
        send_telegram_message(f"Помилка створення бекапу #{mikrotik['name']} ({mikrotik['host']}): {e}")
        return None

def download_backup(mikrotik, backup_name):
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        local_backup = os.path.join(BACKUP_DIR, f"{backup_name}.backup")
        local_rsc = os.path.join(BACKUP_DIR, f"{backup_name}.rsc")

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
        send_telegram_message(f"Помилка завантаження #{mikrotik['name']} ({mikrotik['host']}): {e}")
        return None, None

def upload_backup_to_ftp(local_file, backup_name, ftp_config, file_type='backup'):
    try:
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
        send_telegram_message(f"Помилка завантаження на FTP {backup_name}: {e}")
        return False

def delete_old_backups(mikrotik, keep_count=1):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
        }

        with ConnectHandler(**device) as ssh_conn:
            # Отримуємо список всіх файлів
            backups = ssh_conn.send_command('/file print')
            # Вибираємо лише файли .backup і .rsc
            backup_files = [
                line.split()[0] for line in backups.splitlines()
                if '.backup' in line or '.rsc' in line
            ]

            # Якщо немає файлів для видалення
            if not backup_files:
                send_telegram_message(f"На #{mikrotik['name']} немає файлів для видалення.")
                return False

            # Сортуємо файли за іменами (найновіші будуть в кінці)
            backup_files.sort()

            # Видаляємо всі файли, окрім найновішого
            for file in backup_files[keep_count:]:  # Залишаємо лише найновіший
                #send_telegram_message(f"Видаляється старий файл: {file}")
                ssh_conn.send_command(f'/file remove {file}')

        return True
    except Exception as e:
        send_telegram_message(f"Помилка видалення старих бекапів #{mikrotik['name']} ({mikrotik['host']}): {e}")
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

    for idx, mikrotik in enumerate(config['mikrotiks'], start=1):
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
        time.sleep(5)

    send_telegram_message(f"✅ Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
