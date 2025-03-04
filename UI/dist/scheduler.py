import sys
import time
from datetime import datetime, timedelta
import pyodbc
import configparser
import os
import logging
from cryptography.fernet import Fernet
import routeros_api
import threading
import requests
from concurrent.futures import ThreadPoolExecutor

# Assuming these are defined in MikrotikManager.py
from UI_Test import attempt_connection, create_backup, download_backup, upload_backup_to_ftp, delete_old_backups, check_versions

# Configure logging
logging.basicConfig(filename='scheduler.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Read configuration
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')

if not os.path.exists(config_path):
    logging.error("Configuration file config.ini not found!")
    raise FileNotFoundError("Configuration file config.ini not found!")

config.read(config_path)

key = config['Encryption']['key'].encode()
cipher = Fernet(key)

conn_str = (
    f"DRIVER={{{config['Database']['driver']}}};"
    f"SERVER={config['Database']['server']};"
    f"DATABASE={config['Database']['database']};"
    f"UID={config['Database']['username']};"
    f"PWD={cipher.decrypt(config['Database']['password'].encode()).decode()}"
)

ftp_config = {
    "host": config['FTP']['host'],
    "username": config['FTP']['username'],
    "password": cipher.decrypt(config['FTP']['password'].encode()).decode(),
    "dir": config['FTP']['dir']
}

telegram_token = None
CHAT_IDS = []

def send_telegram_message_async(token, message, log_func=None):
    if not CHAT_IDS or not token:
        if log_func: log_func("No chat_id or Telegram token found for sending.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    successes = 0
    failures = 0
    for chat_id in CHAT_IDS:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                cleaned_message = message.replace('*', '').replace('_', '').strip()
                future = executor.submit(requests.get, url, params={'chat_id': chat_id, 'text': cleaned_message}, timeout=15)
                response = future.result(timeout=20)
                if response.status_code == 200 and response.json().get('ok'):
                    successes += 1
                else:
                    failures += 1
        except Exception as e:
            failures += 1
            if log_func: log_func(f"Error sending to chat_id {chat_id}: {str(e)}")
        time.sleep(0.5)
    if log_func and failures > 0: log_func(f"Success: {successes}, Failed: {failures}")

def load_telegram_settings():
    global telegram_token, CHAT_IDS
    try:
        with pyodbc.connect(conn_str, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT TOP 1 [token] FROM [ManagerMikrotik].[dbo].[TelegramSettings]")
            row = cursor.fetchone()
            if row and row.token:
                telegram_token = row.token
                logging.info("Telegram token loaded from database.")
            else:
                logging.warning("Telegram token not found in database!")
            cursor.execute("SELECT [chat_id] FROM [ManagerMikrotik].[dbo].[TelegramChatIds]")
            CHAT_IDS = [str(row.chat_id) for row in cursor.fetchall()]
            logging.info(f"Loaded {len(CHAT_IDS)} chat_ids: {', '.join(CHAT_IDS)}")
    except pyodbc.Error as e:
        logging.error(f"Database connection error while loading Telegram settings: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error while loading Telegram settings: {str(e)}")

def load_device(device_id):
    try:
        with pyodbc.connect(conn_str, timeout=10) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM [MikroTikDevices] WHERE id = ?", device_id)
            row = cursor.fetchone()
            if row:
                return {"id": row.id, "name": row.name, "host": row.host, "user": row.username, "password": row.password}
            else:
                logging.error(f"Device with ID {device_id} not found")
                return None
    except pyodbc.Error as e:
        logging.error(f"Database error loading device {device_id}: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error loading device {device_id}: {str(e)}")
        return None

def execute_task(task, device):
    try:
        if task == "Бекап":
            backup_name, error = create_backup(device, logging.info)
            if backup_name:
                local_backup, local_rsc, _ = download_backup(device, backup_name, logging.info)
                upload_backup_to_ftp(local_backup, backup_name, ftp_config, 'backup', logging.info)
                upload_backup_to_ftp(local_rsc, backup_name, ftp_config, 'rsc', logging.info)
                delete_old_backups(device, logging.info)
                msg = f"🔹 Backup for {device['name']} completed successfully: {backup_name}"
                logging.info(msg)
                send_telegram_message_async(telegram_token, msg, logging.info)
            else:
                logging.error(f"Backup error for {device['name']}: {error}")
                send_telegram_message_async(telegram_token, f"❌ Backup error for {device['name']}: {error}", logging.info)
        elif task == "Оновлення RouterOS":
            api = routeros_api.RouterOsApiPool(host=device['host'], username=device['user'], password=device['password'], port=8728, plaintext_login=True)
            connection = api.get_api()
            connection.get_resource('/system/package/update').call('install')
            api.disconnect()
            msg = f"🔹 RouterOS update for {device['name']} started"
            logging.info(msg)
            send_telegram_message_async(telegram_token, msg, logging.info)
        elif task == "Оновлення RouterBoard":
            api = routeros_api.RouterOsApiPool(host=device['host'], username=device['user'], password=device['password'], port=8728, plaintext_login=True)
            connection = api.get_api()
            connection.get_resource('/system/routerboard/settings').call('set', {'auto-upgrade': 'no'})
            connection.get_resource('/system/routerboard').call('upgrade')
            connection.get_resource('/system').call('reboot')
            api.disconnect()
            msg = f"🔹 RouterBoard update for {device['name']} started"
            logging.info(msg)
            send_telegram_message_async(telegram_token, msg, logging.info)
        elif task == "Перезавантаження":
            api = routeros_api.RouterOsApiPool(host=device['host'], username=device['user'], password=device['password'], port=8728, plaintext_login=True)
            connection = api.get_api()
            connection.get_resource('/system').call('reboot')
            api.disconnect()
            msg = f"🔹 Reboot for {device['name']} completed"
            logging.info(msg)
            send_telegram_message_async(telegram_token, msg, logging.info)
    except Exception as e:
        error_msg = f"❌ Error executing task {task} for {device['name']}: {str(e)}"
        logging.error(error_msg)
        send_telegram_message_async(telegram_token, error_msg, logging.info)

def main():
    logging.info("Scheduler started")
    threading.Thread(target=load_telegram_settings, daemon=True).start()
    time.sleep(1)
    logging.info("Starting schedule check loop")

    running = True
    while running:
        try:
            start_time = time.time()
            logging.debug(f"Starting loop iteration at {datetime.now()}")

            try:
                with pyodbc.connect(conn_str, timeout=10) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM [Schedules] WHERE execution_time <= ?", datetime.now())
                    schedules = cursor.fetchall()
                    logging.info(f"Found {len(schedules)} schedules to execute")
            except pyodbc.Error as e:
                logging.error(f"Database connection error: {str(e)}")
                time.sleep(60)
                continue

            for schedule in schedules:
                device = load_device(schedule.device_id)
                if device is None:
                    logging.error(f"Device for schedule {schedule.id} not found, skipping")
                    continue
                logging.info(f"Executing task {schedule.task} for device {device['name']}")
                execute_task(schedule.task, device)

                with pyodbc.connect(conn_str, timeout=10) as conn:
                    cursor = conn.cursor()
                    try:
                        execution_time = datetime.strptime(str(schedule.execution_time), "%Y-%m-%d %H:%M:%S")
                        logging.debug(f"Schedule {schedule.id} has time: {execution_time}")
                    except ValueError as ve:
                        logging.error(f"Invalid time format for schedule {schedule.id}: {str(ve)}")
                        continue

                    if schedule.repeat_mode == "Одноразово":
                        cursor.execute("DELETE FROM [Schedules] WHERE id = ?", schedule.id)
                        logging.info(f"Schedule {schedule.id} deleted (one-time)")
                    elif schedule.repeat_mode == "Щоденно":
                        new_time = execution_time + timedelta(days=1)
                        cursor.execute("UPDATE [Schedules] SET execution_time = ? WHERE id = ?", new_time, schedule.id)
                        logging.info(f"Schedule {schedule.id} updated to {new_time} (daily)")
                    elif schedule.repeat_mode == "Щотижня":
                        new_time = execution_time + timedelta(weeks=1)
                        cursor.execute("UPDATE [Schedules] SET execution_time = ? WHERE id = ?", new_time, schedule.id)
                        logging.info(f"Schedule {schedule.id} updated to {new_time} (weekly)")
                    conn.commit()

            end_time = time.time()
            logging.debug(f"Iteration completed in {end_time - start_time} seconds")
            time.sleep(300)  # Check every 5 minutes
        except KeyboardInterrupt:
            logging.info("Scheduler stopped by user")
            running = False
        except Exception as e:
            logging.error(f"Error in scheduler loop: {str(e)}")
            time.sleep(60)

if __name__ == "__main__":
    main()