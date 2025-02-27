import sys
import os
import time as time_module
import re
from datetime import datetime
import ftplib
import paramiko
import routeros_api
from netmiko import ConnectHandler, exceptions as netmiko_exceptions
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, \
    QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QCheckBox, QTextEdit, QFrame, QProgressBar, QDialog
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon
import pyodbc
import requests
from concurrent.futures import ThreadPoolExecutor
import traceback
import subprocess
from importlib.metadata import distribution

try:
    import qdarkstyle
except ImportError:
    print("Бібліотека qdarkstyle не встановлена. Використовуватиму стандартний стиль.")

# Початкові константи
BACKUP_DIR = "./BackUp/"
CHAT_IDS = []

# Функції
def check_and_install_dependencies():
    if getattr(sys, 'frozen', False):
        print("Запущено скомпільований .exe. Усі залежності мають бути включені під час компіляції.")
        return

    required_packages = {
        'PyQt5': 'PyQt5',
        'pyodbc': 'pyodbc',
        'netmiko': 'netmiko',
        'paramiko': 'paramiko',
        'requests': 'requests',
        'routeros_api': 'routeros-api',
        'qdarkstyle': 'qdarkstyle'
    }

    max_attempts = 3

    for package_name, pip_name in required_packages.items():
        attempts = 0
        installed = False

        while attempts < max_attempts and not installed:
            try:
                distribution(package_name)
                installed = True
            except ImportError:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
                    installed = True
                except subprocess.CalledProcessError as e:
                    print(f"Помилка при встановленні {package_name}: {str(e)}")
                    attempts += 1
                    if attempts == max_attempts:
                        sys.exit(1)

def attempt_connection(mikrotik, max_retries=3, log_func=None):
    for attempt in range(1, max_retries + 1):
        try:
            device = {
                "device_type": "mikrotik_routeros",
                "host": mikrotik['host'],
                "username": mikrotik['user'] if 'user' in mikrotik and mikrotik['user'] else "admin",
                "password": mikrotik['password'] if 'password' in mikrotik and mikrotik['password'] else "",
                "port": 22,
                "timeout": 20,
                "conn_timeout": 30
            }
            with ConnectHandler(**device):
                if log_func and attempt == 1: log_func(f"Успішно підключено до {mikrotik['host']}")
                return True
        except netmiko_exceptions.NetmikoAuthenticationException as e:
            if log_func and attempt == max_retries: log_func(f"Помилка автентифікації до {mikrotik['host']}: {str(e)}")
            if attempt < max_retries:
                time_module.sleep(1)
        except Exception as e:
            if log_func and attempt == max_retries: log_func(f"Помилка підключення до {mikrotik['host']}: {str(e)}")
            if attempt < max_retries:
                time_module.sleep(1)
    if log_func: log_func(f"Не вдалося підключитися до {mikrotik['host']} після {max_retries} спроб.")
    return False

def check_versions(mikrotik, log_func=None):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'] if 'user' in mikrotik and mikrotik['user'] else "admin",
            "password": mikrotik['password'] if 'password' in mikrotik and mikrotik['password'] else "",
            "port": 22,
            "timeout": 20,
            "conn_timeout": 30
        }
        with ConnectHandler(**device) as ssh_conn:
            output_package = ssh_conn.send_command('/system package update check-for-updates', delay_factor=2.0)
            output_routerboard = ssh_conn.send_command('/system routerboard print', delay_factor=2.0)

            installed_version = next((line.split(':')[1].strip() for line in output_package.splitlines() if "installed-version" in line), None)
            latest_version = next((line.split(':')[1].strip() for line in output_package.splitlines() if "latest-version" in line), None)

            routerboard_firmware = None
            for line in output_routerboard.splitlines():
                if "current-firmware" in line:
                    routerboard_firmware = line.split(':')[1].strip()
                    break

            if not installed_version or not latest_version or not routerboard_firmware:
                if log_func: log_func(f"Не вдалося отримати всі версії для {mikrotik['host']}")
                return None, None, None

            if log_func: log_func(f"Версії для {mikrotik['host']}: Installed={installed_version}, Latest={latest_version}, RouterBoard={routerboard_firmware}")
            return installed_version, latest_version, routerboard_firmware
    except Exception as e:
        if log_func: log_func(f"Помилка перевірки версій для {mikrotik['host']}: {str(e)}")
        return None, None, None

def create_backup(mikrotik, log_func=None):
    try:
        backup_name = f"{mikrotik['name']}-Backup-{datetime.now().strftime('%Y%m%d-%H%M')}"
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
            "port": 22,
            "timeout": 20,
            "conn_timeout": 30
        }

        with ConnectHandler(**device) as ssh_conn:
            ssh_conn.send_command(f'/system backup save name={backup_name}', delay_factor=2.0)
            time_module.sleep(3)
            ssh_conn.send_command(f'/export file={backup_name}', delay_factor=2.0)
            time_module.sleep(1)
        if log_func: log_func(f"Бекап {backup_name} створено для {mikrotik['host']}")
        return backup_name, None
    except Exception as e:
        error_message = f"Помилка створення бекапу на #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[:200]
        if log_func: log_func(error_message)
        return None, error_message

def download_backup(mikrotik, backup_name, log_func=None):
    try:
        mikrotik_dir = os.path.join(BACKUP_DIR, mikrotik['name'])
        os.makedirs(mikrotik_dir, exist_ok=True)

        local_backup = os.path.join(mikrotik_dir, f"{backup_name}.backup")
        local_rsc = os.path.join(mikrotik_dir, f"{backup_name}.rsc")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(mikrotik['host'], username=mikrotik['user'], password=mikrotik['password'], port=22, timeout=20)

        sftp = ssh.open_sftp()
        sftp.get(f"/{backup_name}.backup", local_backup)
        time_module.sleep(3)
        sftp.get(f"/{backup_name}.rsc", local_rsc)
        time_module.sleep(1)
        sftp.close()
        ssh.close()

        if log_func: log_func(f"Бекап {backup_name} завантажено для {mikrotik['host']}")
        return local_backup, local_rsc, None
    except Exception as e:
        error_message = f"Помилка завантаження бекапу для #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[:200]
        if log_func: log_func(error_message)
        return None, None, error_message

def upload_backup_to_ftp(local_file, backup_name, ftp_config, file_type='backup', log_func=None):
    try:
        ftp = ftplib.FTP(ftp_config['host'], timeout=20)
        ftp.login(ftp_config['username'], ftp_config['password'])
        remote_dir = f"{ftp_config['dir']}/{backup_name.split('-')[0]}"

        try:
            ftp.mkd(remote_dir)
        except ftplib.error_perm:
            pass

        remote_file = f"{remote_dir}/{backup_name}.{file_type}"
        with open(local_file, 'rb') as file:
            ftp.storbinary(f"STOR {remote_file}", file)

        ftp.quit()
        if log_func: log_func(f"Бекап {backup_name}.{file_type} завантажено на FTP")
        return True, None
    except Exception as e:
        error_message = f"Помилка завантаження на FTP {backup_name}: {str(e)}"[:200]
        if log_func: log_func(error_message)
        return False, error_message

def delete_old_backups(mikrotik, keep_count=2, log_func=None):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
            "port": 22,
            "timeout": 20,
            "conn_timeout": 30
        }

        with ConnectHandler(**device) as ssh_conn:
            backups = ssh_conn.send_command('/file print', delay_factor=2.0)
            backup_files = [match.group(1) for line in backups.splitlines() if (match := re.search(r'(\S+\.backup|\S+\.rsc)', line))]

            if not backup_files:
                if log_func: log_func(f"На {mikrotik['name']} немає файлів для видалення.")
                return False

            def extract_datetime(file_name):
                match = re.search(r'(\d{8}-\d{4})', file_name)
                return datetime.strptime(match.group(1), "%Y%m%d-%H%M") if match else None

            backup_files_with_dates = [(file, extract_datetime(file)) for file in backup_files]
            backup_files_with_dates = [item for item in backup_files_with_dates if item[1] is not None]

            if not backup_files_with_dates:
                if log_func: log_func(f"Жоден файл не має правильної дати на {mikrotik['name']}.")
                return False

            backup_files_with_dates.sort(key=lambda x: x[1])
            files_to_delete = backup_files_with_dates[:-keep_count]

            if not files_to_delete:
                if log_func: log_func(f"На {mikrotik['name']} немає файлів для видалення.")
                return False

            for file, _ in files_to_delete:
                ssh_conn.send_command(f'/file remove "{file}"', delay_factor=2.0)

            if log_func: log_func(f"Старі бекапи видалено на {mikrotik['name']}")
            return True
    except Exception as e:
        error_message = f"Помилка видалення бекапів на {mikrotik['name']} ({mikrotik['host']}): {str(e)}"[:200]
        if log_func: log_func(error_message)
        return False

def send_telegram_message_async(token, message, log_func=None):
    if not CHAT_IDS:
        if log_func: log_func("Не знайдено chat_id для відправки.")
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
            if log_func: log_func(f"Помилка відправки до chat_id {chat_id}: {str(e)}")
        time_module.sleep(0.5)

    if log_func and failures > 0: log_func(f"Успішно: {successes}, невдало: {failures}")

# Потік для резервного копіювання
class BackupWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token, ftp_config):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token
        self.ftp_config = ftp_config

    def run(self):
        self.update_signal.emit(f"Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token, f"🔹 Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})", self.update_signal.emit)

        for idx, mikrotik in enumerate(self.devices, start=1):
            if self.isInterruptionRequested():
                self.update_signal.emit("Резервне копіювання перервано.")
                break

            try:
                if attempt_connection(mikrotik, log_func=self.update_signal.emit):
                    backup_name, backup_error = create_backup(mikrotik, self.update_signal.emit)
                    if backup_name:
                        local_backup, local_rsc, download_error = download_backup(mikrotik, backup_name, self.update_signal.emit)
                        if local_backup and local_rsc:
                            upload_backup_to_ftp(local_backup, backup_name, self.ftp_config, 'backup', self.update_signal.emit)
                            upload_backup_to_ftp(local_rsc, backup_name, self.ftp_config, 'rsc', self.update_signal.emit)
                            delete_old_backups(mikrotik, log_func=self.update_signal.emit)
                        status = f"Бекап для {mikrotik['name']} завершено успішно: {backup_name}"
                        self.update_device_status(mikrotik['id'], status, "OK")
                        self.update_signal.emit(status)
                        send_telegram_message_async(self.telegram_token, f"🔹 #{idx} *#{mikrotik['name']}* ({mikrotik['host']}):\n{status}", self.update_signal.emit)
                    else:
                        self.update_signal.emit(backup_error)
                        self.update_device_status(mikrotik['id'], backup_error, "Error")
                        send_telegram_message_async(self.telegram_token, backup_error, self.update_signal.emit)
                else:
                    error_msg = f"❌ Не вдалося підключитись до {mikrotik['host']} після 3 спроб."
                    self.update_signal.emit(error_msg)
                    self.update_device_status(mikrotik['id'], error_msg, "Error")
                    send_telegram_message_async(self.telegram_token, error_msg, self.update_signal.emit)
            except Exception as e:
                error_msg = f"Помилка обробки {mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_signal.emit(error_msg)
                self.update_device_status(mikrotik['id'], error_msg, "Error")
                send_telegram_message_async(self.telegram_token, error_msg, self.update_signal.emit)

            time_module.sleep(2)

        self.update_signal.emit(f"Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token, f"✅ Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})", self.update_signal.emit)
        self.finished_signal.emit()

    def update_device_status(self, device_id, status, final_status):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = installed_version, 
                        latest_version = latest_version, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, device_id)
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")

# Потік для перевірки оновлень
class CheckUpdatesWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token

    def parse_version(self, version_str):
        if not version_str:
            return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            parts = version_str.split('.')
            while len(parts) < 3:
                parts.append('0')
            return tuple(int(x) for x in parts[:3])
        except ValueError:
            return (0, 0, 0)

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Перевірка оновлень перервана.")
                break

            try:
                installed_version, latest_version, routerboard_firmware = check_versions(mikrotik, self.update_signal.emit)
                if installed_version and latest_version and routerboard_firmware:
                    installed_ver_tuple = self.parse_version(installed_version)
                    latest_ver_tuple = self.parse_version(latest_version)
                    update_needed = installed_ver_tuple < latest_ver_tuple
                    status = (f"MikroTik *#{mikrotik['name']}* має актуальну версію {installed_version}."
                              if not update_needed
                              else f"#{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version}")
                    self.update_versions_and_firmware(mikrotik['id'], installed_version, latest_version, routerboard_firmware)
                    self.update_device_status(mikrotik['id'], status, "OK" if not update_needed else "Needs Update")
                    self.update_signal.emit(status)
                    if update_needed and self.telegram_token:
                        send_telegram_message_async(self.telegram_token,
                                                    f"⚠ #{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version} | RouterBoard Firmware: {routerboard_firmware}",
                                                    self.update_signal.emit)
                else:
                    error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']})"
                    self.update_device_status(mikrotik['id'], error, "Error")
                    self.update_signal.emit(error)
            except Exception as e:
                error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_device_status(mikrotik['id'], error, "Error")
                self.update_signal.emit(error)

            time_module.sleep(2)
        self.update_signal.emit("Перевірка оновлень завершена.")
        self.finished_signal.emit()

    def update_versions_and_firmware(self, device_id, installed_version, latest_version, routerboard_firmware):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET installed_version = ?, latest_version = ?, routerboard_firmware = ?, backup_status_final = backup_status_final
                    WHERE id = ?
                """, installed_version, latest_version, routerboard_firmware, device_id)
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення версій для ID {device_id}: {str(e)}")

    def update_device_status(self, device_id, status, final_status):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = installed_version, 
                        latest_version = latest_version, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, device_id)
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")

# Потік для оновлення
class UpgradeWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token
        self.log_text = None
        self.active_workers = []

    def set_log_text(self, log_text):
        self.log_text = log_text

    def parse_version(self, version_str):
        if not version_str:
            return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            parts = version_str.split('.')
            while len(parts) < 3:
                parts.append('0')
            return tuple(int(x) for x in parts[:3])
        except ValueError:
            return (0, 0, 0)

    def needs_update(self, installed_version, latest_version):
        installed_ver_tuple = self.parse_version(installed_version)
        latest_ver_tuple = self.parse_version(latest_version)
        return installed_ver_tuple < latest_ver_tuple

    def get_versions_from_db(self, device_id):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT installed_version, latest_version 
                    FROM [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    WHERE id = ?
                """, device_id)
                row = cursor.fetchone()
                if row:
                    return row.installed_version or '0.0', row.latest_version or '0.0'
                return None, None
        except Exception as e:
            self.update_signal.emit(f"Помилка отримання версій з бази для ID {device_id}: {str(e)}")
            return None, None

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Оновлення перервано.")
                break

            try:
                installed_version, latest_version = self.get_versions_from_db(mikrotik['id'])
                if not installed_version or not latest_version:
                    self.update_signal.emit(f"Не вдалося отримати версії з бази для {mikrotik['name']}. Пропускаємо.")
                    continue

                if self.needs_update(installed_version, latest_version):
                    username = mikrotik.get('user', 'admin')
                    password = mikrotik.get('password', '')
                    api = routeros_api.RouterOsApiPool(
                        host=mikrotik['host'],
                        username=username,
                        password=password,
                        port=8728,
                        plaintext_login=True
                    )
                    connection = api.get_api()
                    start_message = f"🔹 Оновлюється RouterOS для *#{mikrotik['name']}* ({mikrotik['host']}), перевірте через 3 хвилини"
                    self.update_signal.emit(start_message)
                    send_telegram_message_async(self.telegram_token, start_message, self.update_signal.emit)

                    package_update = connection.get_resource('/system/package/update')
                    package_update.call('install')
                    self.update_signal.emit(f"Оновлення розпочато для {mikrotik['name']} до версії {latest_version}")
                    api.disconnect()
                    self.check_connection_after_reboot(mikrotik)
                else:
                    status = f"#{mikrotik['name']} має актуальну версію {installed_version}."
                    self.update_signal.emit(status)
                    self.update_device_status(mikrotik['id'], status, "OK", installed_version, latest_version)
                    send_telegram_message_async(self.telegram_token, status, self.update_signal.emit)

            except Exception as e:
                error = f"Помилка оновлення {mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_signal.emit(error)
                self.update_device_status(mikrotik['id'], error, "Error", installed_version, latest_version)

            time_module.sleep(2)

        for worker in self.active_workers:
            worker.wait()

        self.update_signal.emit(f"Оновлення RouterOS завершено! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        self.finished_signal.emit()

    def check_connection_after_reboot(self, mikrotik):
        worker = RebootCheckWorker(mikrotik, self.conn_str, self.telegram_token, self.update_device_status)
        worker.update_signal.connect(self.update_log)
        worker.finished_signal.connect(lambda: self.cleanup_worker(worker))
        self.active_workers.append(worker)
        worker.start()

    def update_device_status(self, device_id, status, final_status, installed_version, latest_version):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = ?, 
                        latest_version = ?, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, installed_version, latest_version, device_id)
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")

    def update_log(self, message):
        if self.log_text:
            self.log_text.append(message)

    def cleanup_worker(self, worker):
        if worker in self.active_workers:
            worker.wait()
            self.active_workers.remove(worker)

# Потік для оновлення RouterBoard
class RouterBoardWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token
        self.log_text = None
        self.active_workers = []

    def set_log_text(self, log_text):
        self.log_text = log_text

    def run(self):
        from concurrent.futures import ThreadPoolExecutor

        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Оновлення RouterBoard перервано.")
                break

            try:
                username = mikrotik.get('user', 'admin')
                password = mikrotik.get('password', '')
                api = routeros_api.RouterOsApiPool(
                    host=mikrotik['host'],
                    username=username,
                    password=password,
                    port=8728,
                    plaintext_login=True
                )
                connection = api.get_api()
                start_message = f"🔹 Розпочато оновлення RouterBoard для *#{mikrotik['name']}* ({mikrotik['host']}), перевірте через 3 хвилини"
                self.update_signal.emit(start_message)
                send_telegram_message_async(self.telegram_token, start_message, self.update_signal.emit)

                connection.get_resource('/system/routerboard/settings').call('set', {'auto-upgrade': 'no'})
                connection.get_resource('/system/routerboard').call('upgrade')
                connection.get_resource('/system').call('reboot')
                self.update_signal.emit(f"Оновлення RouterBoard розпочато для {mikrotik['name']}")
                api.disconnect()
                self.check_connection_after_reboot(mikrotik)

            except Exception as e:
                error = f"Помилка оновлення RouterBoard для {mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_signal.emit(error)
                self.update_device_status(mikrotik['id'], error, "Error")

            time_module.sleep(2)

        for worker in self.active_workers:
            worker.wait()

        self.update_signal.emit(f"Оновлення RouterBoard завершено! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        self.finished_signal.emit()

    def delayed_telegram_message(self, message, delay):
        time_module.sleep(delay)
        send_telegram_message_async(self.telegram_token, message, self.update_signal.emit)

    def check_connection_after_reboot(self, mikrotik):
        worker = RebootCheckWorker(mikrotik, self.conn_str, self.telegram_token, self.update_device_status)
        worker.update_signal.connect(self.update_log)
        worker.finished_signal.connect(lambda: self.cleanup_worker(worker))
        self.active_workers.append(worker)
        worker.start()

    def update_device_status(self, device_id, status, final_status):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = installed_version, 
                        latest_version = latest_version, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, device_id)
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")

    def update_log(self, message):
        if self.log_text:
            self.log_text.append(message)

    def cleanup_worker(self, worker):
        if worker in self.active_workers:
            worker.wait()
            self.active_workers.remove(worker)

# Потік для збору chat_id
class ChatIdWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, token, conn_str):
        super().__init__()
        self.token = token
        self.conn_str = conn_str
        self.running = False

    def run(self):
        self.running = True
        offset = None

        while self.running:
            updates = self.get_updates(offset)
            if updates.get("ok") and updates.get("result"):
                with pyodbc.connect(self.conn_str, timeout=30) as conn:
                    cursor = conn.cursor()
                    for update in updates["result"]:
                        chat_id = update['message']['from']['id']
                        try:
                            cursor.execute("INSERT INTO [ManagerMikrotik].[dbo].[TelegramChatIds] ([chat_id]) VALUES (?)", chat_id)
                            conn.commit()
                            self.update_signal.emit(f"Додано chat_id: {chat_id}")
                        except pyodbc.IntegrityError:
                            pass
                        offset = update['update_id'] + 1
            time_module.sleep(1)
        self.finished_signal.emit()

    def get_updates(self, offset=None):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {'offset': offset, 'timeout': 10}
        try:
            response = requests.get(url, params=params, timeout=15)
            return response.json()
        except Exception as e:
            self.update_signal.emit(f"Помилка отримання оновлень: {str(e)}")
            return {"ok": False, "result": []}

    def stop(self):
        self.running = False

# Потік для перевірки підключення після перезавантаження
class RebootCheckWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, mikrotik, conn_str, telegram_token, update_status_callback):
        super().__init__()
        self.mikrotik = mikrotik
        self.conn_str = conn_str
        self.telegram_token = telegram_token
        self.update_status_callback = update_status_callback

    def run(self):
        self.update_signal.emit(f"Пристрій {self.mikrotik['name']} перезавантажується, перевірте через 3 хвилини")
        self.finished_signal.emit()

# Потік для оновлення версій
class VersionUpdateWorker(QThread):
    update_signal = pyqtSignal(str)
    update_versions_signal = pyqtSignal(int, str, str, str)

    def __init__(self, device, conn_str):
        super().__init__()
        self.device = device
        self.conn_str = conn_str

    def run(self):
        try:
            installed_ver, latest_ver, routerboard_firmware = check_versions(self.device, self.update_signal.emit)
            if installed_ver and latest_ver and routerboard_firmware:
                self.update_versions_signal.emit(
                    self.device.get("id", 0),
                    installed_ver,
                    latest_ver,
                    routerboard_firmware
                )
            else:
                self.update_signal.emit(f"Помилка оновлення версій для {self.device['name']}")
        except Exception as e:
            self.update_signal.emit(f"Помилка при оновленні версій для {self.device['name']}: {str(e)}")

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

# Вікно для перегляду стану пристрою
class DeviceStatusWindow(QDialog):
    def __init__(self, device, conn_str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Стан пристрою: {device['name']}")
        self.setFixedSize(400, 450)
        self.device = device
        self.conn_str = conn_str

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        self.device_name_label = QLabel(f"Пристрій: {device['name']}")
        self.device_name_label.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(self.device_name_label)

        self.update_status_label = QLabel(f"Статус оновлення: {device.get('backup_status_final', 'Невідомо')}")
        self.update_status_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.update_status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        status = device.get('backup_status_final', 'Невідомо')
        self.progress_bar.setValue(100 if status == "OK" else 0 if status == "Error" else 50)
        layout.addWidget(self.progress_bar)

        self.version_info_label = QLabel(f"Встановлена версія: {device.get('installed_version', 'Невідомо')}\nОстання версія: {device.get('latest_version', 'Невідомо')}")
        self.version_info_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.version_info_label)

        self.routerboard_label = QLabel(f"RouterBoard Firmware: {device.get('routerboard_firmware', 'Невідомо')}")
        self.routerboard_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.routerboard_label)

        self.cpu_label = QLabel("CPU використання: Завантаження...")
        self.cpu_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.cpu_label)
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setRange(0, 100)
        self.cpu_progress.setValue(0)
        layout.addWidget(self.cpu_progress)

        self.memory_label = QLabel("Пам’ять: Завантаження...")
        self.memory_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.memory_label)

        self.uptime_label = QLabel("Uptime: Завантаження...")
        self.uptime_label.setFont(QFont("Arial", 12))
        layout.addWidget(self.uptime_label)

        refresh_button = QPushButton("Оновити")
        refresh_button.clicked.connect(self.update_system_status)
        layout.addWidget(refresh_button)

        close_button = QPushButton("Закрити")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)

        self.setStyleSheet("""
            QDialog { background-color: #2c3e50; color: #ffffff; }
            QLabel { font-size: 12px; color: #ffffff; }
            QProgressBar { background-color: #4a6074; border: 1px solid #465c71; border-radius: 4px; padding: 2px; color: #ffffff; text-align: center; }
            QProgressBar::chunk { background-color: #3498db; border-radius: 2px; }
            QPushButton { background-color: #3498db; color: white; padding: 6px 12px; border-radius: 6px; font-size: 12px; }
            QPushButton:hover { background-color: #2980b9; }
        """)

        self.update_system_status()

    def update_system_status(self):
        self.cpu_label.setText("CPU Usage: Loading...")
        self.memory_label.setText("Memory: Loading...")
        self.uptime_label.setText("Uptime: Loading...")
        self.cpu_progress.setValue(0)
        QApplication.processEvents()

        try:
            ssh_device = {
                "device_type": "mikrotik_routeros",
                "host": self.device['host'],
                "username": self.device['user'] if 'user' in self.device and self.device['user'] else "admin",
                "password": self.device['password'] if 'password' in self.device and self.device['password'] else "",
                "port": 22,
                "timeout": 20,
                "conn_timeout": 30
            }

            with ConnectHandler(**ssh_device) as ssh_conn:
                output = ssh_conn.send_command("system resource print", delay_factor=2.0)

                cpu_load = 0
                free_memory = 0
                total_memory = 0
                uptime = "0s"

                for line in output.splitlines():
                    if "cpu-load" in line:
                        cpu_load = int(re.search(r'cpu-load: (\d+)%', line).group(1)) if re.search(r'cpu-load: (\d+)%', line) else 0
                    elif "free-memory" in line:
                        free_memory = float(re.search(r'free-memory: (\d+\.?\d*)MiB', line).group(1)) if re.search(r'free-memory: (\d+\.?\d*)MiB', line) else 0
                    elif "total-memory" in line:
                        total_memory = float(re.search(r'total-memory: (\d+\.?\d*)MiB', line).group(1)) if re.search(r'total-memory: (\d+\.?\d*)MiB', line) else 0
                    elif "uptime" in line:
                        uptime = re.search(r'uptime: (.+)', line).group(1) if re.search(r'uptime: (.+)', line) else "0s"

                self.cpu_progress.setValue(cpu_load)
                self.cpu_label.setText(f"CPU Usage: {cpu_load}%")
                used_memory = total_memory - free_memory if total_memory > 0 else 0
                self.memory_label.setText(f"Memory: Free {free_memory:.1f} MiB / Used {used_memory:.1f} MiB / Total {total_memory:.1f} MiB")
                self.uptime_label.setText(f"Uptime: {uptime}")

        except netmiko_exceptions.NetmikoAuthenticationException as e:
            error_msg = f"Помилка автентифікації: {str(e)}"
            self.cpu_label.setText(f"CPU Usage: {error_msg}")
            self.memory_label.setText(f"Memory: {error_msg}")
            self.uptime_label.setText(f"Uptime: {error_msg}")
            self.cpu_progress.setValue(0)
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"SSH Error for {self.device['name']}: {error_msg}")
        except Exception as e:
            error_msg = f"Помилка: {str(e)}"
            self.cpu_label.setText(f"CPU Usage: {error_msg}")
            self.memory_label.setText(f"Memory: {error_msg}")
            self.uptime_label.setText(f"Uptime: {error_msg}")
            self.cpu_progress.setValue(0)
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"SSH Error for {self.device['name']}: {error_msg}")

class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Вхід до MS SQL")
        self.setFixedSize(400, 400)

        icon_path = get_resource_path("UI/ico/icon.ico")
        print(f"Шлях до іконки: {icon_path}")
        self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        font = QFont("Arial", 16)

        self.server_label = QLabel("Сервер:")
        self.server_label.setFont(font)
        self.server_input = QLineEdit()
        self.server_input.setFont(font)
        self.db_label = QLabel("База даних:")
        self.db_label.setFont(font)
        self.db_input = QLineEdit()
        self.db_input.setFont(font)
        self.user_label = QLabel("Ім'я користувача:")
        self.user_label.setFont(font)
        self.user_input = QLineEdit()
        self.user_input.setFont(font)
        self.pass_label = QLabel("Пароль:")
        self.pass_label.setFont(font)
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        self.pass_input.setFont(font)
        self.login_button = QPushButton("Увійти")
        self.login_button.setFont(QFont("Arial", 20))

        for widget in [self.server_label, self.server_input, self.db_label, self.db_input,
                       self.user_label, self.user_input, self.pass_label, self.pass_input, self.login_button]:
            layout.addWidget(widget)

        self.setLayout(layout)
        self.login_button.clicked.connect(self.check_login)

        self.setStyleSheet("""
            QWidget {
                background-color: #2c3e50;
                color: #ffffff;
            }
            QLabel {
                font-size: 16px;
                color: #ffffff;
            }
            QLineEdit {
                background-color: #4a6074;
                color: #ffffff;
                border: 1px solid #465c71;
                border-radius: 4px;
                padding: 4px;
                font-size: 16px;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                padding: 8px 24px;
                border-radius: 8px;
                font-size: 20px;
                min-width: 180px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)

    def check_login(self):
        server = self.server_input.text()
        database = self.db_input.text()
        username = self.user_input.text()
        password = self.pass_input.text()

        if not all([server, database, username, password]):
            QMessageBox.warning(self, "Помилка введення", "Усі поля мають бути заповнені!")
            return

        conn_str = (
            f'DRIVER={{SQL Server}};'
            f'SERVER={server};'
            f'DATABASE={database};'
            f'UID={username};'
            f'PWD={password}'
        )

        try:
            with pyodbc.connect(conn_str, timeout=30) as conn:
                self.main_window = MainWindow(conn_str)
                self.main_window.showMaximized()
                self.close()
        except pyodbc.Error as e:
            error_msg = f"Помилка авторизації: {str(e)}"
            QMessageBox.critical(self, "Помилка входу", error_msg)
        except Exception as e:
            error_msg = f"Неочікувана помилка: {str(e)}"
            QMessageBox.critical(self, "Помилка входу", error_msg)

class MainWindow(QMainWindow):
    def __init__(self, conn_str):
        super().__init__()
        self.setWindowTitle("Mikrotik Manager by M. Zhukovskyi")
        self.conn_str = conn_str
        self.telegram_token = None
        self.ftp_config = None
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.version_workers = []

        icon_path = get_resource_path("UI/ico/icon.ico")
        print(f"Шлях до іконки: {icon_path}")
        self.setWindowIcon(QIcon(icon_path))

        self.setStyleSheet("""
            QMainWindow {
                background-color: #2c3e50;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 18px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QTableWidget {
                background-color: #34495e;
                color: #ffffff;
                border: 1px solid #465c71;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTextEdit {
                background-color: #34495e;
                color: #ffffff;
                border: 1px solid #465c71;
                font-size: 14px;
            }
            QLabel {
                font-size: 14px;
                color: #ffffff;
            }
            QLineEdit {
                background-color: #4a6074;
                color: #ffffff;
                border: 1px solid #465c71;
                border-radius: 4px;
                padding: 8px;
                font-size: 14px;
            }
            QCheckBox {
                color: #ffffff;
                font-size: 14px;
            }
            QFrame {
                background-color: #34495e;
                border: 1px solid #465c71;
                border-radius: 6px;
            }
            .footer-label {
                font-size: 20px;
                color: rgba(255, 255, 255, 128);
                margin-top: 10px;
            }
        """)

        self.load_settings()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()

        button_frame = QFrame()
        button_frame.setLayout(QVBoxLayout())
        button_frame.layout().setSpacing(8)
        button_frame.layout().setContentsMargins(8, 8, 8, 8)

        self.backup_button = QPushButton("Резервне копіювання")
        self.check_update_button = QPushButton("Перевірити оновлення")
        self.upgrade_button = QPushButton("Оновити пристрої")
        self.routerboard_button = QPushButton("Оновити RouterBoard")
        self.get_chatid_button = QPushButton("Отримати ChatID")
        self.stop_chatid_button = QPushButton("Зупинити get ChatID")
        self.check_all_button = QPushButton("Поставити на всі")
        self.uncheck_all_button = QPushButton("Зняти з всіх")
        self.check_updates_button = QPushButton("Позначити на оновлення")
        self.clear_log_button = QPushButton("Очистити лог")
        self.exit_button = QPushButton("Вихід")
        self.status_button = QPushButton("Переглянути стан")

        self.backup_button.clicked.connect(self.perform_backup)
        self.check_update_button.clicked.connect(self.check_updates)
        self.upgrade_button.clicked.connect(self.perform_upgrade)
        self.routerboard_button.clicked.connect(self.perform_routerboard)
        self.get_chatid_button.clicked.connect(self.start_collecting_chat_ids)
        self.stop_chatid_button.clicked.connect(self.stop_collecting_chat_ids)
        self.check_all_button.clicked.connect(self.check_all)
        self.uncheck_all_button.clicked.connect(self.uncheck_all)
        self.check_updates_button.clicked.connect(self.check_for_updates)
        self.clear_log_button.clicked.connect(self.clear_log)
        self.exit_button.clicked.connect(self.exit_application)
        self.status_button.clicked.connect(self.show_device_status)

        for button in [self.backup_button, self.check_update_button, self.upgrade_button, self.routerboard_button,
                       self.get_chatid_button, self.stop_chatid_button, self.check_all_button, self.uncheck_all_button,
                       self.check_updates_button, self.clear_log_button, self.exit_button, self.status_button]:
            button.setMinimumWidth(100)

        button_frame.layout().addWidget(self.backup_button)
        button_frame.layout().addWidget(self.check_update_button)
        button_frame.layout().addWidget(self.upgrade_button)
        button_frame.layout().addWidget(self.routerboard_button)
        button_frame.layout().addWidget(self.get_chatid_button)
        button_frame.layout().addWidget(self.stop_chatid_button)
        button_frame.layout().addWidget(self.check_all_button)
        button_frame.layout().addWidget(self.uncheck_all_button)
        button_frame.layout().addWidget(self.check_updates_button)
        button_frame.layout().addWidget(self.clear_log_button)
        button_frame.layout().addWidget(self.exit_button)
        button_frame.layout().addWidget(self.status_button)

        footer_label = QLabel("Mikrotik Manager\nPowered by M. Zhukovskyi ©.\nv1.3.2")
        footer_label.setObjectName("footer-label")
        footer_label.setAlignment(Qt.AlignCenter)

        left_layout.addWidget(button_frame)
        left_layout.addWidget(footer_label)
        left_layout.addStretch()

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["Pick", "Назва", "Хост", "Встановлена версія", "Остання версія", "Статус бекапу", "RouterBoard Firmware"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet("""
            QHeaderView::section {
                background-color: #3498db;
                color: white;
                padding: 6px;
                border: 1px solid #465c71;
                font-size: 15px;
                font-weight: bold;
            }
        """)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 103)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 160)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 200)

        self.load_devices()

        self.log_text.setMinimumWidth(400)
        self.log_text.setMaximumWidth(500)

        main_layout.addLayout(left_layout, 1)
        main_layout.addWidget(self.table, 2)
        main_layout.addWidget(self.log_text, 1)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.backup_worker = BackupWorker([], self.conn_str, self.telegram_token, self.ftp_config)
        self.backup_worker.update_signal.connect(self.update_log)
        self.backup_worker.finished_signal.connect(self.backup_finished)

        self.check_updates_worker = CheckUpdatesWorker([], self.conn_str, self.telegram_token)
        self.check_updates_worker.update_signal.connect(self.update_log)
        self.check_updates_worker.finished_signal.connect(self.check_updates_finished)

        self.upgrade_worker = UpgradeWorker([], self.conn_str, self.telegram_token)
        self.upgrade_worker.set_log_text(self.log_text)
        self.upgrade_worker.update_signal.connect(self.update_log)
        self.upgrade_worker.finished_signal.connect(self.upgrade_finished)

        self.routerboard_worker = RouterBoardWorker([], self.conn_str, self.telegram_token)
        self.routerboard_worker.set_log_text(self.log_text)
        self.routerboard_worker.update_signal.connect(self.update_log)
        self.routerboard_worker.finished_signal.connect(self.routerboard_finished)

        self.chatid_worker = ChatIdWorker(self.telegram_token, self.conn_str)
        self.chatid_worker.update_signal.connect(self.update_log)
        self.chatid_worker.finished_signal.connect(self.chatid_worker_finished)
        self.get_chatid_button.setEnabled(bool(self.telegram_token))

        self.version_workers = []

    def parse_version(self, version_str):
        if not version_str:
            return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            parts = version_str.split('.')
            while len(parts) < 3:
                parts.append('0')
            return tuple(int(x) for x in parts[:3])
        except ValueError:
            return (0, 0, 0)

    def load_settings(self):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT TOP 1 [token] FROM [ManagerMikrotik].[dbo].[TelegramSettings]")
                row = cursor.fetchone()
                if row:
                    self.telegram_token = row.token
                else:
                    self.log_text.append("Не знайдено Telegram токен у базі даних!")

                cursor.execute("SELECT TOP 1 [host], [username], [password], [dir] FROM [ManagerMikrotik].[dbo].[FTPSettings]")
                row = cursor.fetchone()
                if row:
                    self.ftp_config = {
                        "host": row.host,
                        "username": row.username,
                        "password": row.password,
                        "dir": row.dir
                    }
                else:
                    self.log_text.append("Не знайдено FTP налаштування у базі даних!")

                cursor.execute("SELECT [chat_id] FROM [ManagerMikrotik].[dbo].[TelegramChatIds]")
                CHAT_IDS.clear()
                chat_ids = [str(row.chat_id) for row in cursor.fetchall()]
                CHAT_IDS.extend(chat_ids)
                if not CHAT_IDS:
                    self.log_text.append("Не знайдено жодного chat_id у базі даних.")
                else:
                    self.log_text.append(f"Завантажено {len(CHAT_IDS)} chat_id: {', '.join(CHAT_IDS)}")
        except Exception as e:
            self.log_text.append(f"Помилка завантаження налаштувань: {str(e)}")
            traceback.print_exc()

    def load_devices(self):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT [id], [name], [host], [username], [password], [installed_version], [latest_version], 
                           [backup_status], [backup_status_final], [routerboard_firmware] 
                    FROM [ManagerMikrotik].[dbo].[MikroTikDevices]
                """)
                rows = cursor.fetchall()

                sorted_rows = []
                for row in rows:
                    if row.installed_version and row.latest_version and row.routerboard_firmware:
                        installed_ver_tuple = self.parse_version(row.installed_version)
                        latest_ver_tuple = self.parse_version(row.latest_version)
                        needs_update = installed_ver_tuple < latest_ver_tuple
                    else:
                        needs_update = True
                    sorted_rows.append((row, needs_update))

                sorted_rows.sort(key=lambda x: x[1], reverse=True)

                self.table.setRowCount(0)
                self.devices_data = []
                for i, (row, _) in enumerate(sorted_rows):
                    self.table.insertRow(i)
                    checkbox = QCheckBox()
                    checkbox.setFont(QFont("Arial", 18))
                    self.table.setCellWidget(i, 0, checkbox)
                    self.table.setItem(i, 1, QTableWidgetItem(row.name or "Без назви"))
                    self.table.setItem(i, 2, QTableWidgetItem(row.host or "Невідомий хост"))
                    self.table.setItem(i, 3, QTableWidgetItem(
                        str(row.installed_version) if row.installed_version else "Невідомо"))
                    self.table.setItem(i, 4,
                                       QTableWidgetItem(str(row.latest_version) if row.latest_version else "Невідомо"))
                    self.table.setItem(i, 5, QTableWidgetItem(
                        str(row.backup_status_final) if row.backup_status_final else "Невідомо"))
                    self.table.setItem(i, 6, QTableWidgetItem(
                        str(row.routerboard_firmware) if row.routerboard_firmware else "Невідомо"))
                    device_data = {
                        "id": row.id,
                        "name": row.name,
                        "host": row.host,
                        "user": row.username,
                        "password": row.password,
                        "installed_version": row.installed_version,
                        "latest_version": row.latest_version,
                        "backup_status": row.backup_status,
                        "backup_status_final": row.backup_status_final,
                        "routerboard_firmware": row.routerboard_firmware
                    }
                    self.devices_data.append(device_data)

                    if not row.installed_version or not row.latest_version or not row.routerboard_firmware or row.latest_version == "0.0":
                        self.log_text.append(f"Запускаємо перевірку версій для {device_data['name']}")
                        worker = VersionUpdateWorker(device_data, self.conn_str)
                        worker.update_signal.connect(self.update_log)
                        worker.update_versions_signal.connect(self.update_versions_and_firmware)
                        worker.start()
                        self.version_workers.append(worker)

                self.log_text.append("Пристрої завантажено.")
        except Exception as e:
            self.log_text.append(f"Помилка завантаження пристроїв: {str(e)}")
            traceback.print_exc()
            self.table.setRowCount(0)
            for i in range(10):
                self.table.insertRow(i)
                checkbox = QCheckBox()
                checkbox.setFont(QFont("Arial", 14))
                self.table.setCellWidget(i, 0, checkbox)
                self.table.setItem(i, 1, QTableWidgetItem(f"Пристрій {i + 1}"))
                self.table.setItem(i, 2, QTableWidgetItem(f"192.168.{i + 1}.1"))
                self.table.setItem(i, 3, QTableWidgetItem("Невідомо"))
                self.table.setItem(i, 4, QTableWidgetItem("Невідомо"))
                self.table.setItem(i, 5, QTableWidgetItem("Невідомо"))
                self.table.setItem(i, 6, QTableWidgetItem("Невідомо"))

    def update_versions_and_firmware(self, device_id, installed_version, latest_version, routerboard_firmware):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET installed_version = ?, latest_version = ?, routerboard_firmware = ?, backup_status_final = backup_status_final
                    WHERE id = ?
                """, (installed_version, latest_version, routerboard_firmware, device_id))
                conn.commit()
            self.log_text.append(
                f"Оновлено версії для ID {device_id}: Installed={installed_version}, Latest={latest_version}")
            for i, device in enumerate(self.devices_data):
                if device["id"] == device_id:
                    self.table.setItem(i, 3,
                                       QTableWidgetItem(str(installed_version) if installed_version else "Невідомо"))
                    self.table.setItem(i, 4, QTableWidgetItem(str(latest_version) if latest_version else "Невідомо"))
                    self.table.setItem(i, 6, QTableWidgetItem(
                        str(routerboard_firmware) if routerboard_firmware else "Невідомо"))
                    break
        except Exception as e:
            self.log_text.append(f"Помилка оновлення версій для ID {device_id}: {str(e)}")

    def update_device_status(self, device_id, status, final_status, installed_version=None, latest_version=None):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, 
                        installed_version = COALESCE(?, installed_version), 
                        latest_version = COALESCE(?, latest_version), 
                        routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, installed_version, latest_version, device_id)
                conn.commit()
            self.log_text.append(f"Статус оновлено для ID {device_id}: {final_status}")
        except Exception as e:
            self.log_text.append(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")

    def perform_backup(self):
        if not self.telegram_token or not self.ftp_config:
            self.log_text.append("Помилка: Не завантажено Telegram токен або FTP налаштування!")
            return

        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.backup_worker.devices = selected_devices
        self.backup_worker.start()
        self.backup_button.setEnabled(False)

    def backup_finished(self):
        self.backup_button.setEnabled(True)
        self.log_text.append("Резервне копіювання завершено.")

    def check_updates(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.check_updates_worker.devices = selected_devices
        self.check_updates_worker.start()
        self.check_update_button.setEnabled(False)

    def check_updates_finished(self):
        self.check_update_button.setEnabled(True)
        self.log_text.append("Перевірка оновлень завершена.")
        self.load_devices()

    def perform_upgrade(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.upgrade_worker.devices = selected_devices
        self.upgrade_worker.start()
        self.upgrade_button.setEnabled(False)

    def upgrade_finished(self):
        self.upgrade_button.setEnabled(True)
        self.log_text.append("Оновлення завершено.")
        self.load_devices()

    def perform_routerboard(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.routerboard_worker.devices = selected_devices
        self.routerboard_worker.start()
        self.routerboard_button.setEnabled(False)

    def routerboard_finished(self):
        self.routerboard_button.setEnabled(True)
        self.log_text.append("Оновлення RouterBoard завершено.")

    def get_selected_devices(self):
        selected = []
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            if checkbox.isChecked():
                selected.append(self.devices_data[i])
        return selected

    def check_all(self):
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            checkbox.setChecked(True)
        self.log_text.append("Усі галочки поставлено.")

    def uncheck_all(self):
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            checkbox.setChecked(False)
        self.log_text.append("Усі галочки зняті.")

    def check_for_updates(self):
        for i in range(self.table.rowCount()):
            mikrotik = self.devices_data[i]
            installed_version = mikrotik['installed_version']
            latest_version = mikrotik['latest_version']
            checkbox = self.table.cellWidget(i, 0)
            if installed_version and latest_version:
                installed_ver_tuple = self.parse_version(installed_version)
                latest_ver_tuple = self.parse_version(latest_version)
                update_needed = installed_ver_tuple < latest_ver_tuple
                checkbox.setChecked(update_needed)
        self.log_text.append("Позначено пристрої, що потребують оновлення.")

    def start_collecting_chat_ids(self):
        if not self.telegram_token:
            self.log_text.append("Помилка: Не завантажено Telegram токен!")
            return
        self.get_chatid_button.setEnabled(False)
        self.stop_chatid_button.setEnabled(True)
        self.log_text.append("Розпочато збір chat_id...")
        self.chatid_worker.start()

    def stop_collecting_chat_ids(self):
        self.chatid_worker.stop()

    def update_log(self, message):
        self.log_text.append(message)

    def clear_log(self):
        self.log_text.clear()
        self.log_text.append("Лог очищено.")

    def exit_application(self):
        for worker in self.version_workers:
            worker.terminate()
        self.close()
        if not QApplication.instance().topLevelWidgets():
            login_window = LoginWindow()
            login_window.show()

    def chatid_worker_finished(self):
        self.get_chatid_button.setEnabled(True)
        self.stop_chatid_button.setEnabled(False)
        self.log_text.append("Збір chat_id завершено.")
        self.load_settings()

    def show_device_status(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return
        device = selected_devices[0]
        status_window = DeviceStatusWindow(device, self.conn_str, self)
        status_window.exec_()

if __name__ == "__main__":
    check_and_install_dependencies()

    try:
        app = QApplication(sys.argv)
        if 'qdarkstyle' in sys.modules:
            app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        else:
            app.setStyleSheet("""
                QMainWindow { background-color: #2c3e50; color: #ffffff; }
                QPushButton { background-color: #3498db; color: white; padding: 6px 12px; border-radius: 6px; font-size: 12px; min-width: 100px; }
                QPushButton:hover { background-color: #2980b9; }
                QTableWidget { background-color: #34495e; color: #ffffff; border: 1px solid #465c71; font-size: 14px; }
                QTableWidget::item { padding: 4px; }
                QTextEdit { background-color: #34495e; color: #ffffff; border: 1px solid #465c71; font-size: 14px; }
                QLabel { font-size: 14px; color: #ffffff; }
                QLineEdit { background-color: #4a6074; color: #ffffff; border: 1px solid #465c71; border-radius: 4px; padding: 8px; font-size: 14px; }
                QCheckBox { color: #ffffff; font-size: 18px; }
                QFrame { background-color: #34495e; border: 1px solid #465c71; border-radius: 6px; }
                .footer-label { font-size: 20px; color: rgba(255, 255, 255, 128); margin-top: 10px; }
            """)
        login_window = LoginWindow()
        login_window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"Критична помилка: {str(e)}")
        traceback.print_exc()