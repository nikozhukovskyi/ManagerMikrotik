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
    QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QCheckBox, QTextEdit, QFrame, QProgressBar, QDialog, \
    QComboBox, QListWidget, QListWidgetItem, QDateTimeEdit, QScrollArea, QHeaderView, QSizePolicy
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QDateTime
from PyQt5.QtGui import QFont, QIcon
import pyodbc
import requests
import configparser
from concurrent.futures import ThreadPoolExecutor
import traceback
import subprocess
from importlib.metadata import distribution
import logging
from cryptography.fernet import Fernet

try:
    import qdarkstyle
except ImportError:
    print("Бібліотека qdarkstyle не встановлена. Використовуватиму стандартний стиль.")

# Початкові константи
BACKUP_DIR = "./BackUp/"
CHAT_IDS = []  # - з SQL

# logger
logging.basicConfig(filename='schedule_window.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


# Функції
def check_and_install_dependencies():
    if getattr(sys, 'frozen', False):
        print("Запущено скомпільований .exe. Усі залежності мають бути включені під час компіляції.")
        return
    required_packages = {'PyQt5': 'PyQt5', 'pyodbc': 'pyodbc', 'netmiko': 'netmiko', 'paramiko': 'paramiko',
                         'requests': 'requests', 'routeros_api': 'routeros-api', 'qdarkstyle': 'qdarkstyle'}
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


def check_status(host):
    """Перевіряє статус хоста через ping."""
    response = os.system(f"ping -n 1 {host} >nul 2>&1")
    return True if response == 0 else False


def attempt_connection(mikrotik, max_retries=3, log_func=None):
    for attempt in range(1, max_retries + 1):
        try:
            device = {"device_type": "mikrotik_routeros", "host": mikrotik['host'],
                      "username": mikrotik.get('user', 'admin'),
                      "password": mikrotik.get('password', ''), "port": 22, "timeout": 20, "conn_timeout": 30}
            with ConnectHandler(**device):
                if log_func and attempt == 1: log_func(f"Успішно підключено до {mikrotik['host']}")
                return True
        except netmiko_exceptions.NetmikoAuthenticationException as e:
            if log_func and attempt == max_retries: log_func(f"Помилка автентифікації до {mikrotik['host']}: {str(e)}")
            if attempt < max_retries: time_module.sleep(1)
        except Exception as e:
            if log_func and attempt == max_retries: log_func(f"Помилка підключення до {mikrotik['host']}: {str(e)}")
            if attempt < max_retries: time_module.sleep(1)
    if log_func: log_func(f"Не вдалося підключитися до {mikrotik['host']} після {max_retries} спроб.")
    return False


def check_versions(mikrotik, log_func=None):
    try:
        device = {"device_type": "mikrotik_routeros", "host": mikrotik['host'],
                  "username": mikrotik.get('user', 'admin'),
                  "password": mikrotik.get('password', ''), "port": 22, "timeout": 20, "conn_timeout": 30}
        with ConnectHandler(**device) as ssh_conn:
            output_package = ssh_conn.send_command('/system package update check-for-updates', delay_factor=2.0)
            output_routerboard = ssh_conn.send_command('/system routerboard print', delay_factor=2.0)
            installed_version = next(
                (line.split(':')[1].strip() for line in output_package.splitlines() if "installed-version" in line),
                None)
            latest_version = next(
                (line.split(':')[1].strip() for line in output_package.splitlines() if "latest-version" in line), None)
            routerboard_firmware = next(
                (line.split(':')[1].strip() for line in output_routerboard.splitlines() if "current-firmware" in line),
                None)
            if not all([installed_version, latest_version, routerboard_firmware]):
                if log_func: log_func(f"Не вдалося отримати всі версії для {mikrotik['host']}")
                return None, None, None
            if log_func: log_func(
                f"Версії для {mikrotik['host']}: Installed={installed_version}, Latest={latest_version}, RouterBoard={routerboard_firmware}")
            return installed_version, latest_version, routerboard_firmware
    except Exception as e:
        if log_func: log_func(f"Помилка перевірки версій для {mikrotik['host']}: {str(e)}")
        return None, None, None


def create_backup(mikrotik, log_func=None):
    try:
        backup_name = f"{mikrotik['name']}-Backup-{datetime.now().strftime('%Y%m%d-%H%M')}"
        device = {"device_type": "mikrotik_routeros", "host": mikrotik['host'], "username": mikrotik['user'],
                  "password": mikrotik['password'], "port": 22, "timeout": 20, "conn_timeout": 30}
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
        device = {"device_type": "mikrotik_routeros", "host": mikrotik['host'], "username": mikrotik['user'],
                  "password": mikrotik['password'], "port": 22, "timeout": 20, "conn_timeout": 30}
        with ConnectHandler(**device) as ssh_conn:
            backups = ssh_conn.send_command('/file print', delay_factor=2.0)
            backup_files = [match.group(1) for line in backups.splitlines() if
                            (match := re.search(r'(\S+\.backup|\S+\.rsc)', line))]
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
                future = executor.submit(requests.get, url, params={'chat_id': chat_id, 'text': cleaned_message},
                                         timeout=15)
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


# Класи потоків
class BackupWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    backup_complete_signal = pyqtSignal(int, datetime)

    def __init__(self, devices, conn_str, telegram_token, ftp_config):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token
        self.ftp_config = ftp_config

    def run(self):
        self.update_signal.emit(f"Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token,
                                    f"🔹 Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                                    self.update_signal.emit)
        for idx, mikrotik in enumerate(self.devices, start=1):
            if self.isInterruptionRequested():
                self.update_signal.emit("Резервне копіювання перервано.")
                break
            try:
                if attempt_connection(mikrotik, log_func=self.update_signal.emit):
                    backup_name, backup_error = create_backup(mikrotik, self.update_signal.emit)
                    if backup_name:
                        local_backup, local_rsc, download_error = download_backup(mikrotik, backup_name,
                                                                                  self.update_signal.emit)
                        if local_backup and local_rsc:
                            upload_success_backup, ftp_error_backup = upload_backup_to_ftp(local_backup, backup_name,
                                                                                           self.ftp_config, 'backup',
                                                                                           self.update_signal.emit)
                            upload_success_rsc, ftp_error_rsc = upload_backup_to_ftp(local_rsc, backup_name,
                                                                                     self.ftp_config, 'rsc',
                                                                                     self.update_signal.emit)
                            delete_old_backups(mikrotik, log_func=self.update_signal.emit)
                            if upload_success_backup and upload_success_rsc:
                                status = f"Бекап для {mikrotik['name']} завершено успішно: {backup_name}"
                                final_status = "OK"
                            else:
                                status = f"Помилка завантаження на FTP для {mikrotik['name']}: {ftp_error_backup or ftp_error_rsc}"
                                final_status = "Error"
                        else:
                            status = f"Помилка завантаження бекапу для {mikrotik['name']}: {download_error}"
                            final_status = "Error"
                    else:
                        status = f"Помилка створення бекапу для {mikrotik['name']}: {backup_error}"
                        final_status = "Error"
                else:
                    status = f"❌ Не вдалося підключитись до {mikrotik['host']} після 3 спроб."
                    final_status = "Error"
                self.update_device_status(mikrotik['id'], status, final_status)
                self.update_signal.emit(status)
                send_telegram_message_async(self.telegram_token,
                                            f"🔹 #{idx} *#{mikrotik['name']}* ({mikrotik['host']}):\n{status}",
                                            self.update_signal.emit)
                if final_status == "OK":
                    self.backup_complete_signal.emit(mikrotik['id'], datetime.now())
            except Exception as e:
                error_msg = f"Помилка обробки {mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_signal.emit(error_msg)
                self.update_device_status(mikrotik['id'], error_msg, "Error")
                send_telegram_message_async(self.telegram_token, error_msg, self.update_signal.emit)
            time_module.sleep(2)
        self.update_signal.emit(f"Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token,
                                    f"✅ Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                                    self.update_signal.emit)
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
                self.update_signal.emit(f"Статус оновлено для ID {device_id} з backup_status_final: {final_status}")
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")


class CheckUpdatesWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token

    def parse_version(self, version_str):
        if not version_str: return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            return tuple(int(x) for x in (version_str.split('.') + ['0', '0', '0'])[:3])
        except ValueError:
            return (0, 0, 0)

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Перевірка оновлень перервана.")
                break
            try:
                installed_version, latest_version, routerboard_firmware = check_versions(mikrotik,
                                                                                         self.update_signal.emit)
                if installed_version and latest_version and routerboard_firmware:
                    update_needed = self.parse_version(installed_version) < self.parse_version(latest_version)
                    final_status = "Needs Update" if update_needed else "OK"
                    status = f"MikroTik *#{mikrotik['name']}* має актуальну версію {installed_version}" if not update_needed else f"#{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version}"
                    self.update_versions_and_firmware(mikrotik['id'], installed_version, latest_version,
                                                      routerboard_firmware)
                    self.update_device_status(mikrotik['id'], status, final_status)
                    self.update_signal.emit(f"{status} (Статус: {final_status})")
                    if update_needed and self.telegram_token:
                        send_telegram_message_async(self.telegram_token,
                                                    f"⚠ #{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version} | RouterBoard Firmware: {routerboard_firmware}",
                                                    self.update_signal.emit)
                else:
                    error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']})"
                    self.update_device_status(mikrotik['id'], error, "Error")
                    self.update_signal.emit(f"{error} (Статус: Error)")
            except Exception as e:
                error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_device_status(mikrotik['id'], error, "Error")
                self.update_signal.emit(f"{error} (Статус: Error)")
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
                """, (installed_version, latest_version, routerboard_firmware, device_id))
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
                self.update_signal.emit(f"Статус оновлено для ID {device_id} з backup_status_final: {final_status}")
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу для ID {device_id}: {str(e)}")


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
        if not version_str: return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            return tuple(int(x) for x in (version_str.split('.') + ['0', '0', '0'])[:3])
        except ValueError:
            return (0, 0, 0)

    def needs_update(self, installed_version, latest_version):
        return self.parse_version(installed_version) < self.parse_version(latest_version)

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
                return row.installed_version or '0.0', row.latest_version or '0.0' if row else (None, None)
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
                    api = routeros_api.RouterOsApiPool(host=mikrotik['host'], username=mikrotik.get('user', 'admin'),
                                                       password=mikrotik.get('password', ''), port=8728,
                                                       plaintext_login=True)
                    connection = api.get_api()
                    start_message = f"🔹 Оновлюється RouterOS для *#{mikrotik['name']}* ({mikrotik['host']}), перевірте через 3 хвилини"
                    self.update_signal.emit(start_message)
                    send_telegram_message_async(self.telegram_token, start_message, self.update_signal.emit)
                    connection.get_resource('/system/package/update').call('install')
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
        for worker in self.active_workers: worker.wait()
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
        if self.log_text: self.log_text.append(message)

    def cleanup_worker(self, worker):
        if worker in self.active_workers:
            worker.wait()
            self.active_workers.remove(worker)


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
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Оновлення RouterBoard перервано.")
                break
            try:
                api = routeros_api.RouterOsApiPool(host=mikrotik['host'], username=mikrotik.get('user', 'admin'),
                                                   password=mikrotik.get('password', ''), port=8728,
                                                   plaintext_login=True)
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
        for worker in self.active_workers: worker.wait()
        self.update_signal.emit(f"Оновлення RouterBoard завершено! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        self.finished_signal.emit()

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
        if self.log_text: self.log_text.append(message)

    def cleanup_worker(self, worker):
        if worker in self.active_workers:
            worker.wait()
            self.active_workers.remove(worker)


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
                            cursor.execute(
                                "INSERT INTO [ManagerMikrotik].[dbo].[TelegramChatIds] ([chat_id]) VALUES (?)", chat_id)
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
                self.update_versions_signal.emit(self.device.get("id", 0), installed_ver, latest_ver,
                                                 routerboard_firmware)
            else:
                self.update_signal.emit(f"Помилка оновлення версій для {self.device['name']}")
        except Exception as e:
            self.update_signal.emit(f"Помилка при оновленні версій для {self.device['name']}: {str(e)}")


class StatusCheckWorker(QThread):
    update_signal = pyqtSignal(int, bool)  # Рядок, статус (True/False)

    def __init__(self, device, row):
        super().__init__()
        self.device = device
        self.row = row

    def run(self):
        status = check_status(self.device['host'])
        self.update_signal.emit(self.row, status)


def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)


# Класи вікон
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
        self.cpu_label.setText("CPU використання: Підключення...")
        self.memory_label.setText("Пам’ять: Підключення...")
        self.uptime_label.setText("Uptime: Підключення...")
        self.cpu_progress.setValue(0)
        if hasattr(self.parent(), 'log_text'):
            self.parent().log_text.append(f"Початок оновлення статусу для {self.device['name']}")

        try:
            ssh_device = {
                "device_type": "mikrotik_routeros",
                "host": self.device['host'],
                "username": self.device['user'] if 'user' in self.device and self.device['user'] else "admin",
                "password": self.device['password'] if 'password' in self.device and self.device['password'] else "",
                "port": 22,
                "timeout": 30,  # Збільшено timeout
                "conn_timeout": 30
            }
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"Спроба підключення до {self.device['host']} з {ssh_device['username']}")

            with ConnectHandler(**ssh_device) as ssh_conn:
                if hasattr(self.parent(), 'log_text'):
                    self.parent().log_text.append(f"Підключено до {self.device['host']}")
                time_module.sleep(1)  # Затримка для стабільності
                output = ssh_conn.send_command("system resource print", delay_factor=3.0)  # Збільшено delay_factor
                if hasattr(self.parent(), 'log_text'):
                    self.parent().log_text.append(f"Отримано дані: {output[:200]}...")  # Лог лише перших 200 символів

                cpu_load = 0
                free_memory = 0
                total_memory = 0
                uptime = "0s"

                for line in output.splitlines():
                    if "cpu-load" in line.lower():
                        match = re.search(r'cpu-load:\s*(\d+)%', line)
                        cpu_load = int(match.group(1)) if match else 0
                    elif "free-memory" in line.lower():
                        match = re.search(r'free-memory:\s*(\d+\.?\d*)\s*M[iI][bB]', line)
                        free_memory = float(match.group(1)) if match else 0
                    elif "total-memory" in line.lower():
                        match = re.search(r'total-memory:\s*(\d+\.?\d*)\s*M[iI][bB]', line)
                        total_memory = float(match.group(1)) if match else 0
                    elif "uptime" in line.lower():
                        match = re.search(r'uptime:\s*(.+)', line)
                        uptime = match.group(1) if match else "0s"

                self.cpu_progress.setValue(cpu_load)
                self.cpu_label.setText(f"CPU використання: {cpu_load}%")
                used_memory = total_memory - free_memory if total_memory > 0 else 0
                self.memory_label.setText(f"Пам’ять: Free {free_memory:.1f} MiB / Used {used_memory:.1f} MiB / Total {total_memory:.1f} MiB")
                self.uptime_label.setText(f"Uptime: {uptime}")

                # Оновлюємо версії
                installed_version, latest_version, routerboard_firmware = check_versions(self.device, lambda msg: self.parent().log_text.append(msg) if hasattr(self.parent(), 'log_text') else None)
                if installed_version and latest_version and routerboard_firmware:
                    self.version_info_label.setText(f"Встановлена версія: {installed_version}\nОстання версія: {latest_version}")
                    self.routerboard_label.setText(f"RouterBoard Firmware: {routerboard_firmware}")
                    if hasattr(self.parent(), 'update_versions_and_firmware'):
                        self.parent().update_versions_and_firmware(self.device['id'], installed_version, latest_version, routerboard_firmware)
                else:
                    self.version_info_label.setText(f"Встановлена версія: Невідомо\nОстання версія: Невідомо")
                    self.routerboard_label.setText("RouterBoard Firmware: Невідомо")
                    if hasattr(self.parent(), 'log_text'):
                        self.parent().log_text.append(f"Не вдалося отримати версії для {self.device['name']}")

        except netmiko_exceptions.NetmikoAuthenticationException as e:
            error_msg = f"Помилка автентифікації: {str(e)}"
            self.cpu_label.setText(f"CPU використання: {error_msg}")
            self.memory_label.setText(f"Пам’ять: {error_msg}")
            self.uptime_label.setText(f"Uptime: {error_msg}")
            self.cpu_progress.setValue(0)
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"SSH Error for {self.device['name']}: {error_msg}")
        except netmiko_exceptions.NetmikoTimeoutException as e:
            error_msg = f"Таймаут підключення: {str(e)}"
            self.cpu_label.setText(f"CPU використання: {error_msg}")
            self.memory_label.setText(f"Пам’ять: {error_msg}")
            self.uptime_label.setText(f"Uptime: {error_msg}")
            self.cpu_progress.setValue(0)
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"Timeout Error for {self.device['name']}: {error_msg}")
        except Exception as e:
            error_msg = f"Помилка: {str(e)}"
            self.cpu_label.setText(f"CPU використання: {error_msg}")
            self.memory_label.setText(f"Пам’ять: {error_msg}")
            self.uptime_label.setText(f"Uptime: {error_msg}")
            self.cpu_progress.setValue(0)
            if hasattr(self.parent(), 'log_text'):
                self.parent().log_text.append(f"SSH Error for {self.device['name']}: {error_msg}")

        QApplication.processEvents()  # Примусове оновлення UI

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
        self.server_label = QLabel("Сервер:");
        self.server_label.setFont(font)
        self.server_input = QLineEdit("localhost");
        self.server_input.setFont(font)
        self.db_label = QLabel("База даних:");
        self.db_label.setFont(font)
        self.db_input = QLineEdit("ManagerMikrotik");
        self.db_input.setFont(font)
        self.user_label = QLabel("Ім'я користувача:");
        self.user_label.setFont(font)
        self.user_input = QLineEdit("sa");
        self.user_input.setFont(font)
        self.pass_label = QLabel("Пароль:");
        self.pass_label.setFont(font)
        self.pass_input = QLineEdit();
        self.pass_input.setEchoMode(QLineEdit.Password);
        self.pass_input.setFont(font)
        self.login_button = QPushButton("Увійти");
        self.login_button.setFont(QFont("Arial", 20))
        for widget in [self.server_label, self.server_input, self.db_label, self.db_input, self.user_label,
                       self.user_input, self.pass_label, self.pass_input, self.login_button]:
            layout.addWidget(widget)
        self.setLayout(layout)
        self.login_button.clicked.connect(self.check_login)
        self.setStyleSheet("""
            QWidget { background-color: #2c3e50; color: #ffffff; }
            QLabel { font-size: 16px; color: #ffffff; }
            QLineEdit { background-color: #4a6074; color: #ffffff; border: 1px solid #465c71; border-radius: 4px; padding: 4px; font-size: 16px; }
            QPushButton { background-color: #3498db; color: white; padding: 8px 24px; border-radius: 8px; font-size: 20px; min-width: 180px; }
            QPushButton:hover { background-color: #2980b9; }
        """)

    def check_login(self):
        server = self.server_input.text()
        database = self.db_input.text()
        username = self.user_input.text()
        password = self.pass_input.text()
        if not all([server, database, username, password]):
            QMessageBox.warning(self, "Помилка введення", "Усі поля мають бути заповнені!")
            return
        conn_str = f'DRIVER={{SQL Server}};SERVER={server};DATABASE={database};UID={username};PWD={password}'
        try:
            with pyodbc.connect(conn_str, timeout=30) as conn:
                self.main_window = MainWindow(conn_str)
                self.main_window.showMaximized()
                self.close()
        except pyodbc.Error as e:
            QMessageBox.critical(self, "Помилка входу", f"Помилка авторизації: {str(e)}")
        except Exception as e:
            QMessageBox.critical(self, "Помилка входу", f"Неочікувана помилка: {str(e)}")


class DatabaseEditWindow(QDialog):
    def __init__(self, conn_str, devices_data, parent=None):
        """Ініціалізація вікна для редагування бази даних пристроїв."""
        super().__init__(parent)
        self.setWindowTitle("Редагувати базу даних пристроїв")
        self.setFixedSize(900, 600)  # Збільшено до 800x600
        self.conn_str = conn_str
        self.devices_data = devices_data
        self.parent = parent

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Таблиця для відображення пристроїв
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(5)
        self.table_widget.setHorizontalHeaderLabels(["Назва", "Хост", "Користувач", "Пароль", "Дія"])
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setStyleSheet("""
                    QTableWidget { background-color: #34495e; color: #ffffff; border: 1px solid #465c71; font-size: 14px; }
                    QTableWidget::item { padding: 4px; }
                    QPushButton { background-color: #3498db; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
                    QPushButton:hover { background-color: #2980b9; }
                """)
        self.load_devices()

        layout.addWidget(self.table_widget)

        # Панель з кнопками
        button_layout = QHBoxLayout()
        self.add_button = QPushButton("Додати")
        self.add_button.clicked.connect(self.add_device)
        self.save_button = QPushButton("Зберегти")
        self.save_button.clicked.connect(self.save_changes)
        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.save_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        self.setStyleSheet("""
                    QDialog { background-color: #2c3e50; color: #ffffff; }
                    QLabel { font-size: 14px; color: #ffffff; }
                    QLineEdit { background-color: #4a6074; color: #ffffff; border: 1px solid #465c71; border-radius: 4px; padding: 4px; font-size: 14px; }
                    QPushButton { background-color: #3498db; color: white; padding: 6px 12px; border-radius: 6px; font-size: 14px; }
                    QPushButton:hover { background-color: #2980b9; }
                """)

    def load_devices(self):
        """Завантажує список пристроїв у таблицю."""
        self.table_widget.setRowCount(len(self.devices_data))
        for row, device in enumerate(self.devices_data):
            self.table_widget.setItem(row, 0, QTableWidgetItem(device.get("name", "")))
            self.table_widget.setItem(row, 1, QTableWidgetItem(device.get("host", "")))
            self.table_widget.setItem(row, 2, QTableWidgetItem(device.get("user", "")))
            self.table_widget.setItem(row, 3, QTableWidgetItem(device.get("password", "")))

            edit_button = QPushButton("Редагувати")
            edit_button.clicked.connect(lambda checked, r=row: self.edit_device(r))
            delete_button = QPushButton("Видалити")
            delete_button.clicked.connect(lambda checked, r=row: self.delete_device(r))
            self.table_widget.setCellWidget(row, 4, QWidget())
            layout = QHBoxLayout(self.table_widget.cellWidget(row, 4))
            layout.addWidget(edit_button)
            layout.addWidget(delete_button)
            layout.setContentsMargins(0, 0, 0, 0)
            self.table_widget.cellWidget(row, 4).setLayout(layout)

    def add_device(self):
        """Додає новий пристрій у таблицю."""
        row = self.table_widget.rowCount()
        self.table_widget.insertRow(row)
        self.table_widget.setItem(row, 0, QTableWidgetItem(""))
        self.table_widget.setItem(row, 1, QTableWidgetItem(""))
        self.table_widget.setItem(row, 2, QTableWidgetItem(""))
        self.table_widget.setItem(row, 3, QTableWidgetItem(""))

        edit_button = QPushButton("Редагувати")
        edit_button.clicked.connect(lambda checked, r=row: self.edit_device(r))
        delete_button = QPushButton("Видалити")
        delete_button.clicked.connect(lambda checked, r=row: self.delete_device(r))
        self.table_widget.setCellWidget(row, 4, QWidget())
        layout = QHBoxLayout(self.table_widget.cellWidget(row, 4))
        layout.addWidget(edit_button)
        layout.addWidget(delete_button)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table_widget.cellWidget(row, 4).setLayout(layout)

    def edit_device(self, row):
        """Відкриває діалогове вікно для редагування пристрою."""
        name = self.table_widget.item(row, 0).text() if self.table_widget.item(row, 0) else ""
        host = self.table_widget.item(row, 1).text() if self.table_widget.item(row, 1) else ""
        user = self.table_widget.item(row, 2).text() if self.table_widget.item(row, 2) else ""
        password = self.table_widget.item(row, 3).text() if self.table_widget.item(row, 3) else ""

        dialog = QDialog(self)
        dialog.setWindowTitle("Редагувати пристрій")
        dialog.setFixedSize(300, 350)  # Збільшено до 400x250
        layout = QVBoxLayout()
        layout.setSpacing(10)

        name_label = QLabel("Назва:")
        name_input = QLineEdit(name)
        host_label = QLabel("Хост:")
        host_input = QLineEdit(host)
        user_label = QLabel("Користувач:")
        user_input = QLineEdit(user)
        password_label = QLabel("Пароль:")
        password_input = QLineEdit(password)
        password_input.setEchoMode(QLineEdit.Password)

        for label, input_field in [(name_label, name_input), (host_label, host_input), (user_label, user_input),
                                   (password_label, password_input)]:
            layout.addWidget(label)
            layout.addWidget(input_field)

        save_button = QPushButton("Зберегти")
        save_button.clicked.connect(
            lambda: self.save_edited_device(dialog, row, name_input.text(), host_input.text(), user_input.text(),
                                            password_input.text()))
        cancel_button = QPushButton("Скасувати")
        cancel_button.clicked.connect(dialog.reject)
        button_layout = QHBoxLayout()
        button_layout.addWidget(save_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

        dialog.setLayout(layout)
        dialog.exec_()

    def save_edited_device(self, dialog, row, name, host, user, password):
        """Зберігає відредаговані дані пристрою."""
        self.table_widget.setItem(row, 0, QTableWidgetItem(name))
        self.table_widget.setItem(row, 1, QTableWidgetItem(host))
        self.table_widget.setItem(row, 2, QTableWidgetItem(user))
        self.table_widget.setItem(row, 3, QTableWidgetItem(password))
        dialog.accept()

    def delete_device(self, row):
        """Видаляє пристрій із таблиці з перевіркою та видаленням залежностей."""
        if QMessageBox.question(self, "Підтвердження", "Ви впевнені, що хочете видалити цей пристрій?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            device_id = None
            for device in self.devices_data:
                if (device.get("name", "") == self.table_widget.item(row, 0).text() and
                        device.get("host", "") == self.table_widget.item(row, 1).text()):
                    device_id = device.get("id")
                    break

            if device_id:
                try:
                    with pyodbc.connect(self.conn_str, timeout=30) as conn:
                        cursor = conn.cursor()
                        # Спочатку видаляємо залежні записи в Schedules
                        cursor.execute("DELETE FROM [ManagerMikrotik].[dbo].[Schedules] WHERE device_id = ?",
                                       device_id)
                        conn.commit()
                        logging.info(f"Видалено залежні розклади для device_id {device_id}")
                except pyodbc.Error as e:
                    logging.error(f"Помилка при видаленні розкладів для device_id {device_id}: {str(e)}")
                    QMessageBox.critical(self, "Помилка", f"Не вдалося видалити залежні розклади: {str(e)}")
                    return

            self.table_widget.removeRow(row)
            logging.info(f"Пристрій видалено з таблиці (рядок {row})")

    def save_changes(self):
        """Зберігає зміни в базі даних із обробкою конфліктів."""
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                # Спочатку видаляємо всі розклади, пов’язані з пристроями, які будуть оновлені
                existing_device_ids = [device["id"] for device in self.devices_data if "id" in device]
                if existing_device_ids:
                    # Динамічно створюємо запит із кількістю ? відповідною довжині списку
                    placeholders = ','.join('?' * len(existing_device_ids))
                    query = f"DELETE FROM [ManagerMikrotik].[dbo].[Schedules] WHERE device_id IN ({placeholders})"
                    cursor.execute(query, existing_device_ids)
                    conn.commit()
                    logging.info(
                        f"Видалено залежні розклади для device_ids: {', '.join(map(str, existing_device_ids))}")

                # Видаляємо всі старі записи пристроїв
                cursor.execute("DELETE FROM [ManagerMikrotik].[dbo].[MikroTikDevices]")
                conn.commit()

                # Додаємо нові записи
                for row in range(self.table_widget.rowCount()):
                    name = self.table_widget.item(row, 0).text() if self.table_widget.item(row, 0) else ""
                    host = self.table_widget.item(row, 1).text() if self.table_widget.item(row, 1) else ""
                    user = self.table_widget.item(row, 2).text() if self.table_widget.item(row, 2) else ""
                    password = self.table_widget.item(row, 3).text() if self.table_widget.item(row, 3) else ""
                    if name and host:  # Перевіряємо обов’язкові поля
                        cursor.execute("""
                                INSERT INTO [ManagerMikrotik].[dbo].[MikroTikDevices] (name, host, username, password)
                                VALUES (?, ?, ?, ?)
                            """, name, host, user, password)
                conn.commit()
                logging.info("Зміни в базі даних пристроїв збережено")
                QMessageBox.information(self, "Успіх", "Зміни успішно збережено в базі даних!")
                self.parent.load_devices()  # Оновлюємо таблицю в головному вікні
                self.close()
        except pyodbc.Error as e:
            logging.error(f"Помилка при збереженні змін у базі даних: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося зберегти зміни: {str(e)}")
        except Exception as e:
            logging.error(f"Неочікувана помилка при збереженні змін: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося зберегти зміни: {str(e)}")


class ScheduleWindow(QDialog):
    def __init__(self, conn_str, devices_data, parent=None):
        """Ініціалізація вікна для налаштування розкладів."""
        super().__init__(parent)
        self.setWindowTitle("Налаштування розкладу")
        self.setFixedSize(1000, 660)
        self.conn_str = conn_str
        self.devices_data = devices_data

        main_layout = QHBoxLayout()
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # Ліва панель для налаштувань розкладу
        left_layout = QVBoxLayout()
        left_layout.setSpacing(15)  # Зменшено відстань між елементами
        left_layout.setContentsMargins(15, 15, 15, 40)  # Зменшено нижній margin для збалансованості

        self.key_label = QLabel("Ключ шифрування (залиште порожнім для генерації нового):")
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        left_layout.addWidget(self.key_label)
        left_layout.addWidget(self.key_input)

        self.save_key_button = QPushButton("Згенерувати ключ")
        self.save_key_button.clicked.connect(self.save_encryption_key)
        left_layout.addWidget(self.save_key_button)

        self.time_label = QLabel("Час виконання:")
        self.time_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.time_edit.setCalendarPopup(True)
        left_layout.addWidget(self.time_label)
        left_layout.addWidget(self.time_edit)

        self.task_label = QLabel("Тип задачі:")
        self.task_combo = QComboBox()
        self.task_combo.addItems(["Бекап", "Оновлення RouterOS", "Оновлення RouterBoard", "Перезавантаження"])
        left_layout.addWidget(self.task_label)
        left_layout.addWidget(self.task_combo)

        self.repeat_label = QLabel("Повторювати:")
        self.repeat_combo = QComboBox()
        self.repeat_combo.addItems(["Одноразово", "Щоденно", "Щотижня"])
        left_layout.addWidget(self.repeat_label)
        left_layout.addWidget(self.repeat_combo)

        save_cancel_layout = QHBoxLayout()
        save_button = QPushButton("Зберегти розклад")
        save_button.clicked.connect(self.save_schedule)
        cancel_button = QPushButton("Скасувати")
        cancel_button.clicked.connect(self.close)
        save_cancel_layout.addStretch()  # Розтяжка для симетрії
        save_cancel_layout.addWidget(save_button)
        save_cancel_layout.addWidget(cancel_button)
        left_layout.addLayout(save_cancel_layout)

        left_layout.addStretch()
        main_layout.addLayout(left_layout, 2)

        # Права панель для вибору пристроїв і керування службою
        right_layout = QVBoxLayout()
        right_layout.setSpacing(13)

        # self.devices_label = QLabel("Пристрої:")
        # right_layout.addWidget(self.devices_label)

        # Панель з кнопками "Вибрати всі" та "Зняти всі" поза списком
        select_buttons_layout = QHBoxLayout()
        self.select_all_button = QPushButton("Вибрати всі")
        self.select_all_button.clicked.connect(self.select_all_devices)
        self.unselect_all_button = QPushButton("Зняти всі")
        self.unselect_all_button.clicked.connect(self.unselect_all_devices)
        select_buttons_layout.addStretch()  # Розтяжка для симетрії
        select_buttons_layout.addWidget(self.select_all_button)
        select_buttons_layout.addWidget(self.unselect_all_button)
        right_layout.addLayout(select_buttons_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        devices_widget = QWidget()
        devices_layout = QVBoxLayout()
        devices_layout.setSpacing(5)

        self.device_list = QListWidget()
        self.device_list.setMaximumHeight(300)
        self.device_list.setMaximumWidth(400)
        for device in devices_data:
            item = QListWidgetItem(f"{device['name']} ({device['host']})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, device['id'])
            self.device_list.addItem(item)
        devices_layout.addWidget(self.device_list)

        devices_widget.setLayout(devices_layout)
        scroll_area.setWidget(devices_widget)
        right_layout.addWidget(scroll_area)

        self.service_frame = QFrame()
        service_layout = QVBoxLayout()
        service_layout.setSpacing(10)

        self.install_service_button = QPushButton("Встановити службу")
        self.install_service_button.clicked.connect(self.install_service)
        service_layout.addWidget(self.install_service_button)

        self.start_service_button = QPushButton("Запустити службу")
        self.start_service_button.clicked.connect(self.start_service)
        service_layout.addWidget(self.start_service_button)

        self.stop_service_button = QPushButton("Зупинити службу")
        self.stop_service_button.clicked.connect(self.stop_service)
        service_layout.addWidget(self.stop_service_button)

        self.restart_service_button = QPushButton("Перезапустити службу")
        self.restart_service_button.clicked.connect(self.restart_service)
        service_layout.addWidget(self.restart_service_button)

        self.service_frame.setLayout(service_layout)
        right_layout.addWidget(self.service_frame)

        schedule_buttons_layout = QHBoxLayout()
        self.view_schedules_button = QPushButton("Переглянути розклади")
        self.view_schedules_button.clicked.connect(self.view_schedules)
        self.delete_schedules_button = QPushButton("Видалити вибрані")
        self.delete_schedules_button.clicked.connect(self.delete_schedules)
        schedule_buttons_layout.addStretch()  # Розтяжка для симетрії
        schedule_buttons_layout.addWidget(self.view_schedules_button)
        schedule_buttons_layout.addWidget(self.delete_schedules_button)
        right_layout.addLayout(schedule_buttons_layout)

        right_layout.addStretch()
        main_layout.addLayout(right_layout, 1)

        self.setLayout(main_layout)

        self.setStyleSheet("""
            QDialog { background-color: #2c3e50; color: #ffffff; }
            QLabel { font-size: 16px; color: #ffffff; }
            QComboBox, QDateTimeEdit, QLineEdit { 
                background-color: #4a6074; 
                color: #ffffff; 
                border: 1px solid #465c71; 
                border-radius: 4px; 
                padding: 6px;  /* Зменшено padding для менших полів */
                height: 40px;  /* Зменшено висоту полів */
                font-size: 15px;
            }
            QListWidget { 
                background-color: #34495e; 
                color: #ffffff; 
                border: 1px solid #465c71; 
                font-size: 14px; 
            }
            QPushButton { 
                background-color: #3498db; 
                color: white; 
                padding: 10px 20px; 
                border-radius: 6px; 
                font-size: 14px; 
                min-width: 150px; 
            }
            QPushButton:hover { background-color: #2980b9; }
            QFrame { background-color: #34495e; border: 1px solid #465c71; border-radius: 6px; padding: 15px; }
            QScrollArea { background-color: #34495e; border: none; }
        """)

    def save_encryption_key(self):
        """Генерує новий ключ, шифрує паролі з бази даних і оновлює config.ini."""
        reply = QMessageBox.question(self, "Підтвердження",
                                     "Ви впевнені, що хочете згенерувати новий ключ і оновити config.ini?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                # Генеруємо новий ключ
                new_key = Fernet.generate_key()
                cipher = Fernet(new_key)

                # Зчитуємо поточний config.ini
                config = configparser.ConfigParser()
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
                if not os.path.exists(config_path):
                    raise FileNotFoundError(f"Файл config.ini не знайдено за шляхом: {config_path}")
                config.read(config_path)

                # Отримуємо паролі з бази даних
                with pyodbc.connect(self.conn_str, timeout=30) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT setting_name, setting_value FROM [ManagerMikrotik].[dbo].[Settings] WHERE setting_name IN ('pass ftp', 'pass DB')")
                    passwords = {row.setting_name: row.setting_value for row in cursor.fetchall()}

                # Шифруємо паролі новим ключем
                encrypted_ftp_pass = cipher.encrypt(passwords.get('pass ftp', '').encode()).decode()
                encrypted_db_pass = cipher.encrypt(passwords.get('pass DB', '').encode()).decode()

                # Оновлюємо config.ini
                config['Encryption']['key'] = new_key.decode()
                config['Database']['password'] = encrypted_db_pass
                config['FTP']['password'] = encrypted_ftp_pass

                with open(config_path, 'w') as configfile:
                    config.write(configfile)

                # Відображаємо новий ключ для користувача з можливістю копіювання
                dialog = QDialog(self)
                dialog.setWindowTitle("Новий ключ шифрування")
                dialog.setFixedSize(400, 200)
                layout = QVBoxLayout()
                text_edit = QTextEdit(new_key.decode())
                text_edit.setReadOnly(True)
                layout.addWidget(text_edit)
                copy_button = QPushButton("Скопіювати")
                copy_button.clicked.connect(lambda: QApplication.clipboard().setText(new_key.decode()))
                layout.addWidget(copy_button)
                close_button = QPushButton("Закрити")
                close_button.clicked.connect(dialog.accept)
                layout.addWidget(close_button)
                dialog.setLayout(layout)
                dialog.exec_()

                logging.info(f"Новий ключ і зашифровані паролі записано в config.ini")
                QMessageBox.information(self, "Успіх", "Новий ключ і зашифровані паролі успішно оновлено в config.ini!")
            except FileNotFoundError as e:
                logging.error(f"Помилка: {str(e)}")
                QMessageBox.critical(self, "Помилка", str(e))
            except pyodbc.Error as e:
                logging.error(f"Помилка при з’єднанні з базою даних: {str(e)}")
                QMessageBox.critical(self, "Помилка", f"Не вдалося отримати паролі з бази: {str(e)}")
            except Exception as e:
                logging.error(f"Помилка при оновленні config.ini: {str(e)}")
                QMessageBox.critical(self, "Помилка", f"Не вдалося оновити config.ini: {str(e)}")

    def save_schedule(self):
        selected_devices = [self.device_list.item(i).data(Qt.UserRole) for i in range(self.device_list.count()) if
                            self.device_list.item(i).checkState() == Qt.Checked]
        if not selected_devices:
            logging.warning("Не вибрано жодного пристрою для розкладу")
            QMessageBox.warning(self, "Помилка", "Виберіть хоча б один пристрій!")
            return
        with pyodbc.connect(self.conn_str, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT TOP 1 encryption_key FROM [ManagerMikrotik].[dbo].[EncryptionKeys]")
            key_row = cursor.fetchone()
            if not key_row:
                logging.error("Ключ шифрування не знайдено в базі")
                QMessageBox.warning(self, "Помилка", "Спочатку введіть або згенеруйте ключ шифрування!")
                return
            schedule = {"time": self.time_edit.dateTime().toString(Qt.ISODate), "task": self.task_combo.currentText(),
                        "devices": selected_devices, "repeat": self.repeat_combo.currentText()}
            try:
                for device_id in schedule["devices"]:
                    cursor.execute("""
                        INSERT INTO [ManagerMikrotik].[dbo].[Schedules] (device_id, task, execution_time, repeat_mode)
                        VALUES (?, ?, ?, ?)
                    """, device_id, schedule["task"], schedule["time"], schedule["repeat"])
                conn.commit()
                logging.info(f"Розклад успішно збережено: {schedule}")
                QMessageBox.information(self, "Успіх", "Розклад успішно збережено!")
                self.close()
            except Exception as e:
                logging.error(f"Помилка при збереженні розкладу: {str(e)}")
                QMessageBox.critical(self, "Помилка", f"Не вдалося зберегти розклад: {str(e)}")

    def view_schedules(self):
        self.device_list.clear()
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, device_id, task, execution_time, repeat_mode FROM [Schedules]")
                for row in cursor.fetchall():
                    item = QListWidgetItem(
                        f"ID: {row.id}, Device: {row.device_id}, Task: {row.task}, Time: {row.execution_time}, Repeat: {row.repeat_mode}")
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Unchecked)
                    self.device_list.addItem(item)
            logging.info("Список розкладів оновлено")
        except Exception as e:
            logging.error(f"Помилка при перегляді розкладів: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося переглянути розклади: {str(e)}")

    def delete_schedules(self):
        selected_ids = [self.device_list.item(i).text().split(",")[0].replace("ID: ", "") for i in
                        range(self.device_list.count()) if self.device_list.item(i).checkState() == Qt.Checked]
        if not selected_ids:
            logging.warning("Не вибрано жодного розкладу для видалення")
            QMessageBox.warning(self, "Помилка", "Виберіть розклади для видалення!")
            return
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                for schedule_id in selected_ids:
                    cursor.execute("DELETE FROM [Schedules] WHERE id = ?", int(schedule_id))
                conn.commit()
            logging.info(f"Видалено розклади з ID: {', '.join(selected_ids)}")
            QMessageBox.information(self, "Успіх", "Розклади видалено!")
            self.view_schedules()
        except Exception as e:
            logging.error(f"Помилка при видаленні розкладів: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося видалити розклади: {str(e)}")

    def select_all_devices(self):
        for i in range(self.device_list.count()):
            self.device_list.item(i).setCheckState(Qt.Checked)
        logging.info("Усі пристрої вибрано")
        # QMessageBox.information(self, "Успіх", "Усі пристрої вибрано!")

    def unselect_all_devices(self):
        for i in range(self.device_list.count()):
            self.device_list.item(i).setCheckState(Qt.Unchecked)
        logging.info("Усі пристрої знято")
        # QMessageBox.information(self, "Успіх", "Усі пристрої знято!")

    def install_service(self):
        """Встановлює службу за допомогою NSSM."""
        nssm_path = r"./nssm.exe"  # Вкажіть шлях до nssm.exe
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "./scheduler.exe")
        python_path = sys.executable  # Шлях до поточного Python

        if not os.path.exists(nssm_path):
            logging.error(f"Файл nssm.exe не знайдено за шляхом: {nssm_path}")
            QMessageBox.critical(self, "Помилка", "Файл nssm.exe не знайдено! Встановіть NSSM або перевірте шлях.")
            return

        if not os.path.exists(script_path):
            logging.error(f"Файл scheduler.exe не знайдено за шляхом: {script_path}")
            QMessageBox.critical(self, "Помилка", "Файл scheduler.exe не знайдено в директорії програми!")
            return

        try:
            # Команда для встановлення служби через NSSM
            cmd = [nssm_path, "install", "MikrotikScheduler", python_path, script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Встановлення служби виконано: {result.stdout}")
            QMessageBox.information(self, "Успіх", "Служба успішно встановлена!")
        except subprocess.CalledProcessError as e:
            logging.error(f"Помилка при встановленні служби: {e.stderr}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося встановити службу: {e.stderr}")
        except Exception as e:
            logging.error(f"Помилка при встановленні служби: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Помилка при встановленні служби: {str(e)}")

    def start_service(self):
        """Запускає службу за допомогою NSSM."""
        nssm_path = r"./nssm.exe"  # Вкажіть шлях до nssm.exe

        if not os.path.exists(nssm_path):
            logging.error(f"Файл nssm.exe не знайдено за шляхом: {nssm_path}")
            QMessageBox.critical(self, "Помилка", "Файл nssm.exe не знайдено! Встановіть NSSM або перевірте шлях.")
            return

        try:
            cmd = [nssm_path, "start", "MikrotikScheduler"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Запуск служби виконано: {result.stdout}")
            QMessageBox.information(self, "Успіх", "Служба успішно запущена!")
        except subprocess.CalledProcessError as e:
            logging.error(f"Помилка при запуску служби: {e.stderr}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося запустити службу: {e.stderr}")
        except Exception as e:
            logging.error(f"Помилка при запуску служби: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Помилка при запуску служби: {str(e)}")

    def stop_service(self):
        """Зупиняє службу за допомогою NSSM."""
        nssm_path = r"nssm.exe"  # Вкажіть шлях до nssm.exe

        if not os.path.exists(nssm_path):
            logging.error(f"Файл nssm.exe не знайдено за шляхом: {nssm_path}")
            QMessageBox.critical(self, "Помилка", "Файл nssm.exe не знайдено! Встановіть NSSM або перевірте шлях.")
            return

        try:
            cmd = [nssm_path, "stop", "MikrotikScheduler"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Зупинка служби виконано: {result.stdout}")
            QMessageBox.information(self, "Успіх", "Служба успішно зупинена!")
        except subprocess.CalledProcessError as e:
            logging.error(f"Помилка при зупинці служби: {e.stderr}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося зупинити службу: {e.stderr}")
        except Exception as e:
            logging.error(f"Помилка при зупинці служби: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Помилка при зупинці служби: {str(e)}")

    def restart_service(self):
        """Перезапускає службу за допомогою NSSM."""
        nssm_path = r"nssm.exe"  # Вкажіть шлях до nssm.exe

        if not os.path.exists(nssm_path):
            logging.error(f"Файл nssm.exe не знайдено за шляхом: {nssm_path}")
            QMessageBox.critical(self, "Помилка", "Файл nssm.exe не знайдено! Встановіть NSSM або перевірте шлях.")
            return

        try:
            cmd = [nssm_path, "restart", "MikrotikScheduler"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Перезапуск служби виконано: {result.stdout}")
            QMessageBox.information(self, "Успіх", "Служба успішно перезапущена!")
        except subprocess.CalledProcessError as e:
            logging.error(f"Помилка при перезапуску служби: {e.stderr}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося перезапустити службу: {e.stderr}")
        except Exception as e:
            logging.error(f"Помилка при перезапуску служби: {str(e)}")
            QMessageBox.critical(self, "Помилка", f"Помилка при перезапуску служби: {str(e)}")


class StatusUpdateWorker(QThread):
    update_signal = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, device, parent):
        super().__init__()
        self.device = device
        self.parent = parent

    def run(self):
        try:
            self.update_signal.emit({"status": "Підключення до пристрою..."})
            ssh_device = {
                "device_type": "mikrotik_routeros",
                "host": self.device['host'],
                "username": self.device['user'] if 'user' in self.device and self.device['user'] else "admin",
                "password": self.device['password'] if 'password' in self.device and self.device['password'] else "",
                "port": 22,
                "timeout": 30,
                "conn_timeout": 30
            }
            with ConnectHandler(**ssh_device) as conn:
                self.update_signal.emit({"status": "Підключено"})
                time_module.sleep(1)
                self.update_signal.emit({"status": "Отримання даних..."})
                output = conn.send_command("system resource print", delay_factor=3.0)
                if hasattr(self.parent.parent(), 'log_text'):
                    self.parent.parent().log_text.append(f"Отримано дані для {self.device['name']}: {output[:200]}...")

                cpu_load = 0
                free_memory = 0
                total_memory = 0
                uptime = "0s"

                for line in output.splitlines():
                    if "cpu-load" in line.lower():
                        match = re.search(r'cpu-load:\s*(\d+)%', line)
                        cpu_load = int(match.group(1)) if match else 0
                    elif "free-memory" in line.lower():
                        match = re.search(r'free-memory:\s*(\d+\.?\d*)\s*M[iI][bB]', line)
                        free_memory = float(match.group(1)) if match else 0
                    elif "total-memory" in line.lower():
                        match = re.search(r'total-memory:\s*(\d+\.?\d*)\s*M[iI][bB]', line)
                        total_memory = float(match.group(1)) if match else 0
                    elif "uptime" in line.lower():
                        match = re.search(r'uptime:\s*(.+)', line)
                        uptime = match.group(1) if match else "0s"

                installed_version, latest_version, routerboard_firmware = check_versions(self.device, lambda msg: self.update_signal.emit({"log": msg}))
                status = {
                    'cpu_load': cpu_load,
                    'free_memory': free_memory,
                    'total_memory': total_memory,
                    'uptime': uptime,
                    'installed_version': installed_version,
                    'latest_version': latest_version,
                    'routerboard_firmware': routerboard_firmware
                }
                self.update_signal.emit(status)
        except netmiko_exceptions.NetmikoAuthenticationException as e:
            self.update_signal.emit({'error': f"Помилка автентифікації: {str(e)}"})
        except netmiko_exceptions.NetmikoTimeoutException as e:
            self.update_signal.emit({'error': f"Таймаут підключення: {str(e)}"})
        except Exception as e:
            self.update_signal.emit({'error': f"Помилка: {str(e)}"})
        finally:
            self.finished.emit()

class MainWindow(QMainWindow):
    def __init__(self, conn_str):
        """Ініціалізація головного вікна програми."""
        super().__init__()
        self.setWindowTitle("Mikrotik Manager by M. Zhukovskyi")
        self.conn_str = conn_str
        self.telegram_token = None
        self.ftp_config = None
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.version_workers = []
        self.status_workers = []

        icon_path = get_resource_path("UI/ico/icon.ico")
        print(f"Шлях до іконки: {icon_path}")
        self.setWindowIcon(QIcon(icon_path))

        self.setMinimumSize(1200, 600)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.setStyleSheet("""
            QMainWindow { background-color: #2c3e50; color: #ffffff; }
            QPushButton { background-color: #3498db; color: white; padding: 6px 8px; border-radius: 6px; font-size: 14px; min-width: 70px; }
            QPushButton:hover { background-color: #2980b9; }
            QTableWidget { background-color: #34495e; color: #ffffff; border: 1px solid #465c71; font-size: 14px; }
            QTableWidget::item { padding: 4px; }
            QTextEdit { background-color: #34495e; color: #ffffff; border: 1px solid #465c71; font-size: 14px; }
            QLabel { font-size: 14px; color: #ffffff; }
            QLineEdit { background-color: #4a6074; color: #ffffff; border: 1px solid #465c71; border-radius: 4px; padding: 8px; font-size: 14px; }
            QCheckBox { color: #ffffff; font-size: 14px; }
            QFrame { background-color: #34495e; border: 1px solid #465c71; border-radius: 6px; }
            .footer-label { font-size: 20px; color: rgba(255, 255, 255, 128); }
            .status-online { background-color: #2ecc71; border-radius: 10px; }
            .status-offline { background-color: #e74c3c; border-radius: 10px; }
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
        self.status_button = QPushButton("Переглянути стан")
        self.schedule_button = QPushButton("Налаштувати розклад")
        self.edit_database_button = QPushButton("Редагувати DataBase")
        self.exit_button = QPushButton("Вихід")


        self.backup_button.clicked.connect(self.perform_backup)
        self.check_update_button.clicked.connect(self.check_combined_status_and_updates)  # Новий метод
        self.upgrade_button.clicked.connect(self.perform_upgrade)
        self.routerboard_button.clicked.connect(self.perform_routerboard)
        self.get_chatid_button.clicked.connect(self.start_collecting_chat_ids)
        self.stop_chatid_button.clicked.connect(self.stop_collecting_chat_ids)
        self.check_all_button.clicked.connect(self.check_all)
        self.uncheck_all_button.clicked.connect(self.uncheck_all)
        self.check_updates_button.clicked.connect(self.check_for_updates)
        self.clear_log_button.clicked.connect(self.clear_log)
        self.status_button.clicked.connect(self.show_device_status)
        self.schedule_button.clicked.connect(self.open_schedule_window)
        self.edit_database_button.clicked.connect(self.open_database_edit_window)
        self.exit_button.clicked.connect(self.exit_application)

        for button in [self.backup_button, self.check_update_button, self.upgrade_button, self.routerboard_button,
                       self.get_chatid_button, self.stop_chatid_button, self.check_all_button, self.uncheck_all_button,
                       self.check_updates_button, self.clear_log_button, self.status_button,
                       self.schedule_button, self.edit_database_button, self.exit_button]:
            button.setMinimumWidth(70)


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
        button_frame.layout().addWidget(self.status_button)
        button_frame.layout().addWidget(self.schedule_button)
        button_frame.layout().addWidget(self.edit_database_button)
        button_frame.layout().addWidget(self.exit_button)
        button_frame.layout().addStretch()
        #button_frame.layout().setAlignment(Qt.AlignRight)

        footer_label = QLabel("Mikrotik Manager\nPowered by M. Zhukovskyi ©.\nv2.3.1")
        footer_label.setObjectName("footer-label")
        footer_label.setAlignment(Qt.AlignCenter)

        left_layout.addWidget(button_frame)
        left_layout.addStretch()
        left_layout.addWidget(footer_label)
        left_layout.addSpacing(10)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Pick", "Name", "Host", "Install Ver", "Last Ver", "Status (e)", "Last BackUp",
             "RouterBoard", "ON/OFF"])
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
            .status-online { background-color: #2ecc71; border-radius: 10px; }
            .status-offline { background-color: #e74c3c; border-radius: 10px; }
        """)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 103)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(6, 150)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)

        self.load_devices()

        self.log_text.setMinimumWidth(400)
        self.log_text.setMaximumWidth(500)

        main_layout.addLayout(left_layout, 1)
        main_layout.addWidget(self.table, 4)
        main_layout.addWidget(self.log_text, 1)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.backup_worker = BackupWorker([], self.conn_str, self.telegram_token, self.ftp_config)
        self.backup_worker.update_signal.connect(self.update_log)
        self.backup_worker.finished_signal.connect(self.backup_finished)
        self.backup_worker.backup_complete_signal.connect(self.update_last_backup)

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
        self.status_workers = []

    # Новий метод для комбінованої перевірки статусу і оновлень
    def check_combined_status_and_updates(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        # Потік для перевірки статусу через ping
        for device in selected_devices:
            worker = StatusCheckWorker(device, self.devices_data.index(device))
            worker.update_signal.connect(self.update_status_in_table)
            self.status_workers.append(worker)
            worker.start()

        # Потік для перевірки оновлень
        self.check_updates_worker.devices = selected_devices
        self.check_updates_worker.start()
        self.check_update_button.setEnabled(False)
        self.log_text.append("Розпочато перевірку статусу та оновлень.")

    def update_last_backup(self, device_id, backup_date):
        for i, device in enumerate(self.devices_data):
            if device["id"] == device_id:
                device["last_backup"] = backup_date
                self.table.setItem(i, 6, QTableWidgetItem(
                    backup_date.strftime("%Y-%m-%d %H:%M") if backup_date else "Невідомо"))
                try:
                    with pyodbc.connect(self.conn_str, timeout=30) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                            SET last_backup_date = ? WHERE id = ?
                        """, backup_date, device_id)
                        conn.commit()
                except Exception as e:
                    self.log_text.append(f"Помилка оновлення last_backup для ID {device_id}: {str(e)}")
                break

    def load_devices(self):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT [id], [name], [host], [username], [password], [installed_version], [latest_version], 
                           [backup_status], [backup_status_final], [routerboard_firmware], [last_backup_date]
                    FROM [ManagerMikrotik].[dbo].[MikroTikDevices]
                """)
                rows = cursor.fetchall()

                sorted_rows = []
                for row in rows:
                    needs_update = True if not all(
                        [row.installed_version, row.latest_version, row.routerboard_firmware]) else self.parse_version(
                        row.installed_version) < self.parse_version(row.latest_version)
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
                        row.last_backup_date.strftime("%Y-%m-%d %H:%M") if row.last_backup_date else "Невідомо"))
                    self.table.setItem(i, 7, QTableWidgetItem(
                        str(row.routerboard_firmware) if row.routerboard_firmware else "Невідомо"))

                    # Додаємо статус (червоний або зелений круг)
                    status_widget = QWidget()
                    status_layout = QHBoxLayout(status_widget)
                    status_label = QLabel()
                    status_label.setFixedSize(20, 20)
                    status = check_status(row.host)
                    status_label.setStyleSheet(
                        f"background-color: {('#2ecc71' if status else '#e74c3c')}; border-radius: 10px;")
                    status_layout.addWidget(status_label)
                    status_layout.setAlignment(Qt.AlignCenter)
                    status_layout.setContentsMargins(0, 0, 0, 0)
                    self.table.setCellWidget(i, 8, status_widget)

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
                        "routerboard_firmware": row.routerboard_firmware,
                        "last_backup": row.last_backup_date
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

    def check_statuses(self):
        """Перевіряє статус всіх пристроїв через ping."""
        for i in range(self.table.rowCount()):
            device = self.devices_data[i]
            worker = StatusCheckWorker(device, i)
            worker.update_signal.connect(self.update_status_in_table)
            self.status_workers.append(worker)
            worker.start()
        self.log_text.append("Перевірка статусу пристроїв запущена.")

    def update_status_in_table(self, row, status):
        """Оновлює статус у таблиці на основі результату ping."""
        status_widget = self.table.cellWidget(row, 8)
        if status_widget:
            status_label = status_widget.layout().itemAt(0).widget()
            status_label.setStyleSheet(
                f"background-color: {('#2ecc71' if status else '#e74c3c')}; border-radius: 10px;")
            self.log_text.append(
                f"Статус для {self.devices_data[row]['name']} оновлено: {'Онлайн' if status else 'Офлайн'}")
        for worker in self.status_workers:
            if not worker.isRunning():
                self.status_workers.remove(worker)

    def check_for_updates(self):
        """Позначає пристрої, що потребують оновлення, на основі версій."""
        for i in range(self.table.rowCount()):
            mikrotik = self.devices_data[i]
            installed_version = mikrotik['installed_version']
            latest_version = mikrotik['latest_version']
            checkbox = self.table.cellWidget(i, 0)
            if installed_version and latest_version:
                update_needed = self.parse_version(installed_version) < self.parse_version(latest_version)
                checkbox.setChecked(update_needed)
            else:
                checkbox.setChecked(False)
        self.log_text.append("Позначено пристрої, що потребують оновлення.")

    def show_device_status(self):
        """Відкриває вікно стану вибраного пристрою без блокування."""
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return
        device = selected_devices[0]
        status_window = DeviceStatusWindow(device, self.conn_str, self)
        status_window.exec_()

    def open_database_edit_window(self):
        """Відкриває вікно для редагування бази даних пристроїв."""
        edit_window = DatabaseEditWindow(self.conn_str, self.devices_data, self)
        edit_window.exec_()

    def parse_version(self, version_str):
        if not version_str: return (0, 0, 0)
        version_str = re.sub(r'[^0-9.]', '', version_str)
        try:
            return tuple(int(x) for x in (version_str.split('.') + ['0', '0', '0'])[:3])
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
                cursor.execute(
                    "SELECT TOP 1 [host], [username], [password], [dir] FROM [ManagerMikrotik].[dbo].[FTPSettings]")
                row = cursor.fetchone()
                if row:
                    self.ftp_config = {"host": row.host, "username": row.username, "password": row.password,
                                       "dir": row.dir}
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

    def update_versions_and_firmware(self, device_id, installed_version, latest_version, routerboard_firmware):
        """Оновлює версії прошивки в базі даних і таблиці."""
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
                    self.table.setItem(i, 3, QTableWidgetItem(
                        str(installed_version) if installed_version else "Невідомо"))
                    self.table.setItem(i, 4,
                                       QTableWidgetItem(str(latest_version) if latest_version else "Невідомо"))
                    self.table.setItem(i, 6, QTableWidgetItem(
                        str(routerboard_firmware) if routerboard_firmware else "Невідомо"))
                    break
        except Exception as e:
            self.log_text.append(f"Помилка оновлення версій для ID {device_id}: {str(e)}")

    def update_device_status(self, device_id, status, final_status, installed_version=None, latest_version=None):
        """Оновлює статус пристрою в базі даних."""
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
        """Запускає резервне копіювання для вибраних пристроїв із підтвердженням."""
        if not self.telegram_token or not self.ftp_config:
            self.log_text.append("Помилка: Не завантажено Telegram токен або FTP налаштування!")
            return

        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        reply = QMessageBox.question(self, "Підтвердження", "Ви хочете виконати резервне копіювання?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.backup_worker.devices = selected_devices
            self.backup_worker.start()
            self.backup_button.setEnabled(False)

    def backup_finished(self):
        """Завершує резервне копіювання."""
        self.backup_button.setEnabled(True)
        self.log_text.append("Резервне копіювання завершено.")

    def check_updates_finished(self):
        """Завершує перевірку оновлень."""
        self.check_update_button.setEnabled(True)
        self.log_text.append("Перевірка оновлень завершена.")
        self.load_devices()

    def perform_upgrade(self):
        """Оновлює RouterOS для вибраних пристроїв із підтвердженням."""
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        reply = QMessageBox.question(self, "Підтвердження", "Ви хочете оновити пристрої?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.upgrade_worker.devices = selected_devices
            self.upgrade_worker.start()
            self.upgrade_button.setEnabled(False)

    def upgrade_finished(self):
        """Завершує оновлення RouterOS."""
        self.upgrade_button.setEnabled(True)
        self.log_text.append("Оновлення завершено.")
        self.load_devices()

    def perform_routerboard(self):
        """Оновлює RouterBoard для вибраних пристроїв із підтвердженням."""
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        reply = QMessageBox.question(self, "Підтвердження", "Ви хочете оновити RouterBoard?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.routerboard_worker.devices = selected_devices
            self.routerboard_worker.start()
            self.routerboard_button.setEnabled(False)

    def routerboard_finished(self):
        """Завершує оновлення RouterBoard."""
        self.routerboard_button.setEnabled(True)
        self.log_text.append("Оновлення RouterBoard завершено.")

    def get_selected_devices(self):
        """Повертає список вибраних пристроїв."""
        selected = []
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            if checkbox.isChecked():
                selected.append(self.devices_data[i])
        return selected

    def check_all(self):
        """Вибирає всі пристрої у таблиці."""
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            checkbox.setChecked(True)
        self.log_text.append("Усі галочки поставлено.")

    def uncheck_all(self):
        """Знімає вибір з усіх пристроїв у таблиці."""
        for i in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(i, 0)
            checkbox.setChecked(False)
        self.log_text.append("Усі галочки зняті.")

    def check_for_updates(self):
        """Позначає пристрої, що потребують оновлення, на основі версій."""
        for i in range(self.table.rowCount()):
            mikrotik = self.devices_data[i]
            installed_version = mikrotik['installed_version']
            latest_version = mikrotik['latest_version']
            checkbox = self.table.cellWidget(i, 0)
            if installed_version and latest_version:
                update_needed = self.parse_version(installed_version) < self.parse_version(latest_version)
                checkbox.setChecked(update_needed)
            else:
                checkbox.setChecked(False)
        self.log_text.append("Позначено пристрої, що потребують оновлення.")

    def start_collecting_chat_ids(self):
        """Розпочинає збір Telegram chat_id."""
        if not self.telegram_token:
            self.log_text.append("Помилка: Не завантажено Telegram токен!")
            return
        self.get_chatid_button.setEnabled(False)
        self.stop_chatid_button.setEnabled(True)
        self.log_text.append("Розпочато збір chat_id...")
        self.chatid_worker.start()

    def stop_collecting_chat_ids(self):
        """Зупиняє збір Telegram chat_id."""
        self.chatid_worker.stop()

    def update_log(self, message):
        """Додає повідомлення до логів."""
        self.log_text.append(message)

    def clear_log(self):
        """Очищає лог."""
        self.log_text.clear()
        self.log_text.append("Лог очищено.")

    def exit_application(self):
        """Завершує роботу програми."""
        for worker in self.version_workers:
            worker.terminate()
        self.close()
        if not QApplication.instance().topLevelWidgets():
            login_window = LoginWindow()
            login_window.show()

    def chatid_worker_finished(self):
        """Завершує збір chat_id."""
        self.get_chatid_button.setEnabled(True)
        self.stop_chatid_button.setEnabled(False)
        self.log_text.append("Збір chat_id завершено.")
        self.load_settings()

    def show_device_status(self):
        """Відкриває вікно стану вибраного пристрою."""
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return
        device = selected_devices[0]
        status_window = DeviceStatusWindow(device, self.conn_str, self)
        status_window.exec_()

    def open_schedule_window(self):
        """Відкриває вікно налаштування розкладів."""
        schedule_window = ScheduleWindow(self.conn_str, self.devices_data, self)
        schedule_window.exec_()


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
                    .footer-label { font-size: 20px; color: rgba(255, 255, 255, 128); }
                """)
        login_window = LoginWindow()
        login_window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"Критична помилка: {str(e)}")
        traceback.print_exc()
# Основний блок запуску програми