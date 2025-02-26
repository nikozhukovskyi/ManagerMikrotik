import sys
import os
import time as time_module
import re
from datetime import datetime
import ftplib
import paramiko
from netmiko import ConnectHandler, exceptions as netmiko_exceptions
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, \
    QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QCheckBox, QTextEdit, QFrame
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont, QColor, QPalette, QIcon
import pyodbc
import requests
from concurrent.futures import ThreadPoolExecutor
import traceback
import routeros_api
import subprocess
from importlib.metadata import distribution

try:
    import qdarkstyle
except ImportError:
    print("Бібліотека qdarkstyle не встановлена. Використовуватиму стандартний стиль.")

# Початкові константи
BACKUP_DIR = "./BackUp/"
CHAT_IDS = []  # Буде завантажено з бази

def check_and_install_dependencies():
    """
    Перевіряє наявність залежностей і встановлює їх, якщо код запускається як .py.
    У скомпільованому .exe залежності мають бути включені PyInstaller'ом.
    """
    if getattr(sys, 'frozen', False):  # Перевіряємо, чи код скомпільовано в .exe
        print("Запущено скомпільований .exe. Усі залежності мають бути включені під час компіляції.")
        return

    required_packages = {
        'PyQt5': 'PyQt5',
        'pyodbc': 'pyodbc',
        'netmiko': 'netmiko',
        'paramiko': 'paramiko',
        'requests': 'requests',
        'routeros_api': 'routeros_api',
        'qdarkstyle': 'qdarkstyle'
    }

    max_attempts = 3

    for package_name, pip_name in required_packages.items():
        attempts = 0
        installed = False

        while attempts < max_attempts and not installed:
            try:
                print(f"Перевіряємо залежність: {package_name} (спроба {attempts + 1}/{max_attempts})...")
                distribution(package_name)
                print(f"Залежність {package_name} вже встановлена.")
                installed = True
            except ImportError as e:
                print(f"Залежність {package_name} не знайдена. Встановлюємо... (Помилка: {str(e)})")
                try:
                    print(f"Виконуємо: {sys.executable} -m pip install {pip_name}")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
                    print(f"Успішно встановлено {package_name}.")
                    installed = True
                except subprocess.CalledProcessError as e:
                    print(f"Помилка при встановленні {package_name}: {str(e)}")
                    attempts += 1
                    if attempts == max_attempts:
                        print(f"Не вдалося встановити {package_name} після {max_attempts} спроб.")
                        sys.exit(1)

def attempt_connection(mikrotik, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            device = {
                "device_type": "mikrotik_routeros",
                "host": mikrotik['host'],
                "username": mikrotik['user'] if 'user' in mikrotik and mikrotik['user'] else "admin",
                # Типовий логін MikroTik
                "password": mikrotik['password'] if 'password' in mikrotik and mikrotik['password'] else "",
                # Типовий пароль (може бути порожнім або заданим)
                "port": 22,  # Явно вказуємо порт
                "timeout": 20,
                "conn_timeout": 30  # Таймаут для з'єднання
            }
            print(f"Спроба {attempt} підключення до {mikrotik['host']} з логіном {device['username']} і паролем ****")
            with ConnectHandler(**device) as ssh_conn:
                print(f"Успішно підключено до {mikrotik['host']} з логіном {device['username']} і паролем ****")
                return True
        except netmiko_exceptions.NetmikoAuthenticationException as e:
            print(f"Помилка автентифікації до {mikrotik['host']} (спроба {attempt}): {str(e)}")
            if attempt < max_retries:
                print(f"Зачекайте 1 секунду перед повторною спробою для {mikrotik['host']}...")
                time_module.sleep(1)
            continue
        except Exception as e:
            print(f"Помилка підключення до {mikrotik['host']} (спроба {attempt}): {str(e)}")
            if attempt < max_retries:
                print(f"Зачекайте 1 секунду перед повторною спробою для {mikrotik['host']}...")
                time_module.sleep(1)
    print(f"Не вдалося підключитися до {mikrotik['host']} після {max_retries} спроб.")
    return False


def check_versions(mikrotik):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
            "port": 22,  # Явно вказуємо порт
            "timeout": 20,
            "conn_timeout": 30  # Таймаут для з'єднання
        }
        with ConnectHandler(**device) as ssh_conn:
            print(f"Успішно підключено до {mikrotik['host']} для перевірки версій")
            output_package = ssh_conn.send_command('/system package update check-for-updates', delay_factor=2.0)
            output_routerboard = ssh_conn.send_command('/system routerboard print', delay_factor=2.0)

            # Отримання версії пакету
            installed_version = next(
                (line.split(':')[1].strip() for line in output_package.splitlines() if "installed-version" in line),
                None)
            latest_version = next(
                (line.split(':')[1].strip() for line in output_package.splitlines() if "latest-version" in line), None)

            # Отримання версії прошивки RouterBoard
            routerboard_firmware = None
            for line in output_routerboard.splitlines():
                if "current-firmware" in line:
                    routerboard_firmware = line.split(':')[1].strip()
                    break

            return installed_version, latest_version, routerboard_firmware
    except Exception as e:
        print(f"Помилка перевірки версій для {mikrotik['host']}: {str(e)}")
        return None, None, None


def create_backup(mikrotik):
    try:
        backup_name = f"{mikrotik['name']}-Backup-{datetime.now().strftime('%Y%m%d-%H%M')}"
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
            "port": 22,  # Явно вказуємо порт
            "timeout": 20,
            "conn_timeout": 30  # Таймаут для з'єднання
        }

        print(f"Підключення до {mikrotik['host']}...")
        with ConnectHandler(**device) as ssh_conn:
            print(f"Успішно підключено до {mikrotik['host']} для створення бекапу")
            print(f"Підключено до {mikrotik['host']}. Створення бекапу...")
            ssh_conn.send_command(f'/system backup save name={backup_name}', delay_factor=2.0)
            time_module.sleep(3)
            ssh_conn.send_command(f'/export file={backup_name}', delay_factor=2.0)
            time_module.sleep(1)
        return backup_name, None
    except Exception as e:
        error_message = f"Помилка авторизації на #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
        print(error_message)
        return None, error_message


def download_backup(mikrotik, backup_name):
    try:
        mikrotik_dir = os.path.join(BACKUP_DIR, mikrotik['name'])
        os.makedirs(mikrotik_dir, exist_ok=True)

        local_backup = os.path.join(mikrotik_dir, f"{backup_name}.backup")
        local_rsc = os.path.join(mikrotik_dir, f"{backup_name}.rsc")

        print(f"Завантаження бекапу з {mikrotik['host']}...")
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

        return local_backup, local_rsc, None
    except Exception as e:
        error_message = f"Помилка авторизації або завантаження на #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
        print(error_message)
        return None, None, error_message


def upload_backup_to_ftp(local_file, backup_name, ftp_config, file_type='backup'):
    try:
        print(f"Завантаження на FTP {backup_name}...")
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
        return True, None
    except Exception as e:
        error_message = f"Помилка завантаження на FTP {backup_name}: {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
        print(error_message)
        return False, error_message


def delete_old_backups(mikrotik, keep_count=2):
    try:
        device = {
            "device_type": "mikrotik_routeros",
            "host": mikrotik['host'],
            "username": mikrotik['user'],
            "password": mikrotik['password'],
            "port": 22,  # Явно вказуємо порт
            "timeout": 20,
            "conn_timeout": 30  # Таймаут для з'єднання
        }

        with ConnectHandler(**device) as ssh_conn:
            print(f"Успішно підключено до {mikrotik['host']} для видалення старих бекапів")
            backups = ssh_conn.send_command('/file print', delay_factor=2.0)
            print(f"📜 Отриманий список файлів:\n{backups}")

            backup_files = []
            for line in backups.splitlines():
                match = re.search(r'(\S+\.backup|\S+\.rsc)', line)
                if match:
                    backup_files.append(match.group(1))

            print(f"📂 Вибрані файли для аналізу: {backup_files}")

            if not backup_files:
                print(f"⚠ На {mikrotik['name']} немає файлів для видалення.")
                return False

            def extract_datetime(file_name):
                match = re.search(r'(\d{8}-\d{4})', file_name)
                if match:
                    try:
                        dt = datetime.strptime(match.group(1), "%Y%m%d-%H%M")
                        return dt
                    except ValueError:
                        print(f"⚠ Помилка парсингу дати у файлі: {file_name}")
                        return None
                print(f"⚠ Дата не знайдена у файлі: {file_name}")
                return None

            backup_files_with_dates = [(file, extract_datetime(file)) for file in backup_files]
            backup_files_with_dates = [item for item in backup_files_with_dates if item[1] is not None]

            if not backup_files_with_dates:
                print(f"⚠ Жоден файл не має правильної дати! Перевір регулярний вираз.")
                return False

            backup_files_with_dates.sort(key=lambda x: x[1])

            sorted_files = "\n".join(f"{file} ({date})" for file, date in backup_files_with_dates)
            print(f"📂 Відсортований список файлів на {mikrotik['name']}:\n{sorted_files}")

            files_to_delete = backup_files_with_dates[:-keep_count]

            if not files_to_delete:
                print(f"✅ На {mikrotik['name']} немає файлів для видалення.")
                return False

            for file, date in files_to_delete:
                print(f"❌ Видаляємо файл: {file} ({date})")
                ssh_conn.send_command(f'/file remove "{file}"', delay_factor=2.0)

            return True
    except Exception as e:
        error_message = f"Помилка видалення бекапів на {mikrotik['name']} ({mikrotik['host']}): {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
        print(error_message)
        return False


def send_telegram_message_async(token, message):
    if not CHAT_IDS:
        print("Не знайдено жодного chat_id для відправки повідомлення. Повідомлення не відправлено.")
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
                print(f"Telegram API відповідь для chat_id {chat_id}: {response.status_code}, {response.text}")
                if response.status_code == 200 and response.json().get('ok'):
                    successes += 1
                else:
                    failures += 1
                    print(f"Помилка відправки повідомлення до chat_id {chat_id}: {response.text}")
        except requests.RequestException as e:
            failures += 1
            print(f"Мережева помилка відправки до chat_id {chat_id}: {str(e)}")
        except Exception as e:
            failures += 1
            print(f"Невідома помилка відправки до chat_id {chat_id}: {str(e)}")
        time_module.sleep(0.5)  # Затримка між відправками для уникнення лімітів Telegram

    print(f"Успішно надіслано повідомлення: {successes}, невдало: {failures}")


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
        send_telegram_message_async(self.telegram_token,
                                    f"🔹 Розпочато планові бекапи! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

        for idx, mikrotik in enumerate(self.devices, start=1):
            if self.isInterruptionRequested():
                self.update_signal.emit("Резервне копіювання перервано.")
                break

            try:
                if attempt_connection(mikrotik):
                    backup_name, backup_error = create_backup(mikrotik)
                    if backup_name:
                        local_backup, local_rsc, download_error = download_backup(mikrotik, backup_name)
                        if local_backup and local_rsc:
                            upload_backup_to_ftp(local_backup, backup_name, self.ftp_config, 'backup')
                            upload_backup_to_ftp(local_rsc, backup_name, self.ftp_config, 'rsc')
                            delete_old_backups(mikrotik)
                        status = f"Бекап для {mikrotik['name']} завершено успішно: {backup_name}"
                        self.update_device_status(mikrotik['id'], status, "OK")
                        self.update_signal.emit(f"Успіх для {mikrotik['name']}: {status}")
                        send_telegram_message_async(self.telegram_token,
                                                    f"🔹 #{idx} *#{mikrotik['name']}* ({mikrotik['host']}):\n{status}")
                    else:
                        self.update_signal.emit(backup_error)
                        self.update_device_status(mikrotik['id'], backup_error, "Error")
                        send_telegram_message_async(self.telegram_token, backup_error)
                else:
                    error_msg = f"❌ Не вдалося підключитись до {mikrotik['host']} після 3 спроб. Пропускаємо."
                    self.update_signal.emit(error_msg)
                    self.update_device_status(mikrotik['id'], error_msg, "Error")
                    send_telegram_message_async(self.telegram_token, error_msg)
            except Exception as e:
                error_msg = f"Помилка обробки {mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_signal.emit(error_msg)
                self.update_device_status(mikrotik['id'], error_msg, "Error")
                send_telegram_message_async(self.telegram_token, error_msg)

            time_module.sleep(2)  # Збільшена затримка для зменшення навантаження

        self.update_signal.emit(f"Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token,
                                    f"✅ Завдання виконано! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
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
                """, status[:200], final_status, device_id)  # Обмежуємо довжину до 200 символів
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу пристрою: {str(e)}")


# Потік для перевірки оновлень
class CheckUpdatesWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Перевірка оновлень перервана.")
                break

            try:
                installed_version, latest_version, routerboard_firmware = check_versions(mikrotik)
                if installed_version and latest_version:
                    update_needed = tuple(map(int, str(installed_version).split('.'))) < tuple(
                        map(int, str(latest_version).split('.')))
                    status = f"MikroTik *#{mikrotik['name']}* має актуальну версію." if not update_needed else f"#{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version}"
                    self.update_versions_and_firmware(mikrotik['id'], installed_version, latest_version, routerboard_firmware)
                    self.update_device_status(mikrotik['id'], status, "OK" if not update_needed else "Needs Update")
                    self.update_signal.emit(f"{mikrotik['name']}: {status} | RouterBoard Firmware: {routerboard_firmware}")
                    if update_needed and self.telegram_token:
                        send_telegram_message_async(self.telegram_token,
                                                    f"⚠ #{mikrotik['name']} потребує оновлення: {installed_version} -> {latest_version} | RouterBoard Firmware: {routerboard_firmware}")
                else:
                    error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']})"
                    self.update_device_status(mikrotik['id'], error, "Error")
                    self.update_signal.emit(error)
                    if self.telegram_token:
                        send_telegram_message_async(self.telegram_token, error)
            except Exception as e:
                error = f"Помилка при перевірці версій для #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"
                self.update_device_status(mikrotik['id'], error, "Error")
                self.update_signal.emit(error)
                if self.telegram_token:
                    send_telegram_message_async(self.telegram_token, error)

            time_module.sleep(2)  # Збільшена затримка для зменшення навантаження
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
            self.update_signal.emit(f"Помилка оновлення версій та прошивки для пристрою ID {device_id}: {str(e)}")

    def update_device_status(self, device_id, status, final_status):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = installed_version, 
                        latest_version = latest_version, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, device_id)  # Обмежуємо довжину до 200 символів
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу пристрою: {str(e)}")


# Потік для оновлення
class UpgradeWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Оновлення перервано.")
                break

            try:
                device = {
                    "device_type": "mikrotik_routeros",
                    "host": mikrotik['host'],
                    "username": mikrotik['user'],
                    "password": mikrotik['password'],
                    "port": 22,
                    "timeout": 20,  # Загальний таймаут з'єднання
                    "conn_timeout": 30  # Таймаут для з'єднання
                }
                with ConnectHandler(**device) as ssh_conn:
                    print(f"Успішно підключено до {mikrotik['host']} з логіном {mikrotik['user']} і паролем ****")
                    self.update_signal.emit(f"Розпочато перевірку оновлень для {mikrotik['name']} ({mikrotik['host']})")
                    output = ssh_conn.send_command('/system package update check-for-updates', delay_factor=2.0)

                    # Парсинг версій з обробкою можливих форматів
                    def parse_version(version_str):
                        if not version_str:
                            return None
                        # Видаляємо суфікси типу "rc1", "beta" тощо
                        version_str = re.sub(r'[^0-9.]', '', version_str)
                        try:
                            return tuple(map(int, version_str.split('.')))
                        except ValueError:
                            return None

                    installed_version = next(
                        (line.split(':')[1].strip() for line in output.splitlines() if "installed-version" in line),
                        None)
                    latest_version = next(
                        (line.split(':')[1].strip() for line in output.splitlines() if "latest-version" in line), None)

                    installed_ver_tuple = parse_version(installed_version)
                    latest_ver_tuple = parse_version(latest_version)

                    if installed_ver_tuple and latest_ver_tuple:
                        if installed_ver_tuple < latest_ver_tuple:
                            self.update_signal.emit(
                                f"Виконується оновлення для {mikrotik['name']} до версії {latest_version}")
                            ssh_conn.send_command('/system package update install',
                                                  delay_factor=2.0)  # Без expect_string
                            time_module.sleep(60)  # Чекаємо 1 хвилину для ребуту
                            status = f"Оновлення для MikroTik *#{mikrotik['name']}* завершено до версії {latest_version}."
                            self.update_device_status(mikrotik['id'], status, "OK")
                            self.update_signal.emit(status)
                            if self.telegram_token:
                                send_telegram_message_async(self.telegram_token, status)
                        else:
                            status = f"MikroTik *#{mikrotik['name']}* має актуальну версію {installed_version}."
                            self.update_device_status(mikrotik['id'], status, "OK")
                            self.update_signal.emit(status)
                            if self.telegram_token:
                                send_telegram_message_async(self.telegram_token, status)
                    else:
                        error = f"Помилка при отриманні версій для #{mikrotik['name']} ({mikrotik['host']})"
                        self.update_device_status(mikrotik['id'], error, "Error")
                        self.update_signal.emit(error)
                        if self.telegram_token:
                            send_telegram_message_async(self.telegram_token, error)
            except (netmiko_exceptions.NetmikoTimeoutException, netmiko_exceptions.NetmikoAuthenticationException,
                    Exception) as e:
                error = f"Помилка при оновленні #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
                self.update_device_status(mikrotik['id'], error, "Error")
                self.update_signal.emit(error)
                if self.telegram_token:
                    send_telegram_message_async(self.telegram_token, error)

            time_module.sleep(1)  # Зменшена затримка для швидкості

        self.update_signal.emit(
            f"Оновлення завершено для всіх пристроїв! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token,
                                    f"✅ Оновлення завершено для всіх пристроїв! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
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
                """, status[:200], final_status, device_id)  # Обмежуємо довжину до 200 символів
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу пристрою: {str(e)}")


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
                            cursor.execute("INSERT INTO [ManagerMikrotik].[dbo].[TelegramChatIds] ([chat_id]) VALUES (?)",
                                           chat_id)
                            conn.commit()
                            self.update_signal.emit(f"Додано chat_id: {chat_id}")
                        except pyodbc.IntegrityError:
                            self.update_signal.emit(f"chat_id {chat_id} вже існує")
                        offset = update['update_id'] + 1
            else:
                self.update_signal.emit("Немає нових повідомлень для обробки.")
            time_module.sleep(1)
        self.finished_signal.emit()

    def get_updates(self, offset=None):
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {'offset': offset, 'timeout': 10}  # Додаємо timeout для довшого очікування
        try:
            response = requests.get(url, params=params, timeout=15)
            return response.json()
        except Exception as e:
            self.update_signal.emit(f"Помилка отримання оновлень: {str(e)}")
            return {"ok": False, "result": []}

    def stop(self):
        self.running = False


# Потік для оновлення RouterBoard
class RouterBoardWorker(QThread):
    update_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, devices, conn_str, telegram_token):
        super().__init__()
        self.devices = devices
        self.conn_str = conn_str
        self.telegram_token = telegram_token

    def run(self):
        for mikrotik in self.devices:
            if self.isInterruptionRequested():
                self.update_signal.emit("Оновлення RouterBoard перервано.")
                break

            try:
                # Використовуємо routeros_api для підключення
                host = mikrotik['host']
                username = mikrotik['user']
                password = mikrotik['password']

                # Підключення до MikroTik через API
                api = routeros_api.RouterOsApiPool(
                    host=host,
                    username=username,
                    password=password,
                    plaintext_login=True,
                    port=8728  # Типовий порт для API MikroTik
                )
                connection = api.get_api()

                print(f"Успішно підключено до {mikrotik['host']} через API з логіном {mikrotik['user']} і паролем ****")
                self.update_signal.emit(f"Розпочато оновлення RouterBoard для {mikrotik['name']} ({mikrotik['host']})")
                send_telegram_message_async(self.telegram_token,
                                            f"🔹 Розпочато оновлення RouterBoard для *#{mikrotik['name']}* ({mikrotik['host']})")

                # Встановлення auto-upgrade=no (ручне оновлення)
                self.update_signal.emit(f"Налаштування ручного оновлення RouterBoard для {mikrotik['name']}...")
                routerboard_settings = connection.get_resource('/system/routerboard/settings')
                routerboard_settings.set(auto_upgrade='no')
                print(f"Виконано /system routerboard settings set auto-upgrade=no для {mikrotik['name']}")

                # Виконання ручного оновлення RouterBoard через API
                self.update_signal.emit(f"Виконується ручне оновлення RouterBoard для {mikrotik['name']}...")
                routerboard = connection.get_resource('/system/routerboard')
                routerboard.call('upgrade')
                print(f"Виконано /system routerboard upgrade для {mikrotik['name']}")

                # Виконуємо перезавантаження після оновлення через API
                self.update_signal.emit(f"Виконується перезавантаження для {mikrotik['name']} після оновлення...")
                system_resource = connection.get_resource('/system')
                system_resource.call('reboot')
                print(f"Виконано /system reboot для {mikrotik['name']}")

                # Повідомляємо про очікування і чекаємо 1 хвилину для завершення оновлення та ребуту
                self.update_signal.emit(
                    f"Оновлення RouterBoard та перезавантаження для {mikrotik['name']} виконуються. Очікуйте 1 хвилину...")
                time_module.sleep(60)  # Чекаємо 1 хвилину для завершення оновлення та ребуту
                print(f"Оновлення RouterBoard та перезавантаження завершено для {mikrotik['name']}")
                self.update_signal.emit(
                    f"Виконано ручне оновлення RouterBoard та перезавантаження для {mikrotik['name']}")
                send_telegram_message_async(self.telegram_token,
                                            f"✅ Виконано ручне оновлення RouterBoard та перезавантаження для *#{mikrotik['name']}*")

                # Закриття підключення
                api.disconnect()

                # Оновлюємо статус у базі
                self.update_device_status(mikrotik['id'],
                                          f"Ручне оновлення RouterBoard та перезавантаження завершено для {mikrotik['name']}",
                                          "OK")

            except Exception as e:
                error = f"Помилка при оновленні RouterBoard для #{mikrotik['name']} ({mikrotik['host']}): {str(e)}"[
                        :200]  # Обмежуємо довжину до 200 символів
                self.update_signal.emit(error)
                self.update_device_status(mikrotik['id'], error, "Error")
                if self.telegram_token:
                    send_telegram_message_async(self.telegram_token, error)

            time_module.sleep(2)  # Затримка між пристроями для стабільності

        self.update_signal.emit(
            f"Оновлення RouterBoard завершено для всіх пристроїв! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
        send_telegram_message_async(self.telegram_token,
                                    f"✅ Оновлення RouterBoard завершено для всіх пристроїв! ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
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
                """, status[:200], final_status, device_id)  # Обмежуємо довжину до 200 символів
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу пристрою: {str(e)}")


def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        # Якщо код скомпільовано в .exe, використовуємо sys._MEIPASS
        base_path = sys._MEIPASS
    else:
        # Якщо код запускається як .py, використовуємо директорію скрипта
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Вхід до MS SQL")
        self.setFixedSize(400, 400)

        # Динамічний шлях до іконки
        icon_path = get_resource_path("UI/ico/icon.ico")  # Або "ui/ico/icon.ico"
        print(f"Шлях до іконки: {icon_path}")  # Для відладки
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

        # Налаштування стилю
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

        icon_path = get_resource_path("UI/ico/icon.ico")  # Або "ui/ico/icon.ico"
        print(f"Шлях до іконки: {icon_path}")  # Для відладки
        self.setWindowIcon(QIcon(icon_path))

        # Налаштування стилю
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2c3e50;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                padding: 6px 8px;  /* Зменшені відступи для вужчих кнопок */
                border-radius: 6px;  /* Зменшений радіус для компактності */
                font-size: 18px;  /* Зменшений шрифт для кнопок, щоб текст поміщався */
                min-width: 100px;  /* Зменшена мінімальна ширина кнопок (можете налаштувати на свій розсуд) */
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QTableWidget {
                background-color: #34495e;
                color: #ffffff;
                border: 1px solid #465c71;
                font-size: 14px;  /* Збільшений шрифт для таблиці */
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTextEdit {
                background-color: #34495e;
                color: #ffffff;
                border: 1px solid #465c71;
                font-size: 14px;  /* Збільшений шрифт для логу */
            }
            QLabel {
                font-size: 14px;  /* Збільшений шрифт для міток */
                color: #ffffff;
            }
            QLineEdit {
                background-color: #4a6074;
                color: #ffffff;
                border: 1px solid #465c71;
                border-radius: 4px;
                padding: 8px;
                font-size: 14px;  /* Збільшений шрифт для полів введення */
            }
            QCheckBox {
                color: #ffffff;
                font-size: 14px;  /* Збільшений шрифт для чекбоксів */
            }
            QFrame {
                background-color: #34495e;
                border: 1px solid #465c71;
                border-radius: 6px;  /* Зменшений радіус для компактності */
            }
            .footer-label {
                font-size: 20px;  /* Великий шрифт для надпису */
                color: rgba(255, 255, 255, 128);  /* Білий колір із 50% прозорістю */
                margin-top: 10px;  /* Відступ зверху */
            }
        """)

        self.load_settings()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()

        # Фрейм для кнопок
        button_frame = QFrame()
        button_frame.setLayout(QVBoxLayout())
        button_frame.layout().setSpacing(8)  # Зменшений інтервал між кнопками
        button_frame.layout().setContentsMargins(8, 8, 8, 8)  # Зменшені відступи фрейму

        # Іконки для кнопок (з перевіркою наявності та логуванням)
        backup_icon = QIcon.fromTheme("document-save")
        if backup_icon.isNull():
            backup_icon = QIcon("UI/ico/backup_icon.png")  # Замініть на реальний шлях, якщо є
            print("Стандартна іконка 'document-save' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        update_icon = QIcon.fromTheme("system-software-update")
        if update_icon.isNull():
            update_icon = QIcon("UI/ico/update_icon.png")
            print("Стандартна іконка 'system-software-update' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        routerboard_icon = QIcon.fromTheme("network-wired")
        if routerboard_icon.isNull():
            routerboard_icon = QIcon("UI/ico/routerboard_icon.png")
            print("Стандартна іконка 'network-wired' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        chatid_icon = QIcon.fromTheme("im-user")
        if chatid_icon.isNull():
            chatid_icon = QIcon("UI/ico/chatid_icon.png")
            print("Стандартна іконка 'im-user' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        stop_icon = QIcon.fromTheme("media-playback-stop")
        if stop_icon.isNull():
            stop_icon = QIcon("UI/ico/stop_icon.png")
            print("Стандартна іконка 'media-playback-stop' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        check_icon = QIcon.fromTheme("dialog-ok")
        if check_icon.isNull():
            check_icon = QIcon("path/to/check_icon.png")
            print("Стандартна іконка 'dialog-ok' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        uncheck_icon = QIcon.fromTheme("dialog-cancel")
        if uncheck_icon.isNull():
            uncheck_icon = QIcon("path/to/uncheck_icon.png")
            print("Стандартна іконка 'dialog-cancel' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        update_check_icon = QIcon.fromTheme("system-search")
        if update_check_icon.isNull():
            update_check_icon = QIcon("path/to/update_check_icon.png")
            print("Стандартна іконка 'system-search' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        clear_icon = QIcon.fromTheme("edit-clear")
        if clear_icon.isNull():
            clear_icon = QIcon("UI/ico/clear_icon.png")
            print("Стандартна іконка 'edit-clear' не знайдена. Використовуйте власну іконку або перевірте тему системи.")
        exit_icon = QIcon.fromTheme("application-exit")
        if exit_icon.isNull():
            exit_icon = QIcon("UI/ico/exit_icon.png")
            print("Стандартна іконка 'application-exit' не знайдена. Використовуйте власну іконку або перевірте тему системи.")

        self.backup_button = QPushButton("Резервне копіювання")
        self.backup_button.setIcon(backup_icon)
        self.check_update_button = QPushButton("Перевірити оновлення")
        self.check_update_button.setIcon(update_icon)
        self.upgrade_button = QPushButton("Оновити пристрої")
        self.upgrade_button.setIcon(update_icon)
        self.routerboard_button = QPushButton("Оновити RouterBoard")
        self.routerboard_button.setIcon(routerboard_icon)
        self.get_chatid_button = QPushButton("Отримати ChatID")
        self.get_chatid_button.setIcon(chatid_icon)
        self.stop_chatid_button = QPushButton("Зупинити get ChatID")
        self.stop_chatid_button.setIcon(stop_icon)
        self.check_all_button = QPushButton("Поставити на всі")
        self.check_all_button.setIcon(check_icon)
        self.uncheck_all_button = QPushButton("Зняти з всіх")
        self.uncheck_all_button.setIcon(uncheck_icon)
        self.check_updates_button = QPushButton("Позначити на оновлення")
        self.check_updates_button.setIcon(update_check_icon)
        self.clear_log_button = QPushButton("Очистити лог")
        self.clear_log_button.setIcon(clear_icon)
        self.exit_button = QPushButton("Вихід")
        self.exit_button.setIcon(exit_icon)

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

        # Встановлюємо мінімальну ширину для кожної кнопки (альтернативний спосіб)
        for button in [self.backup_button, self.check_update_button, self.upgrade_button, self.routerboard_button,
                       self.get_chatid_button, self.stop_chatid_button, self.check_all_button, self.uncheck_all_button,
                       self.check_updates_button, self.clear_log_button, self.exit_button]:
            button.setMinimumWidth(100)  # Зменшена ширина (можете налаштувати на свій розсуд)

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

        # Додаємо надпис у футер
        footer_label = QLabel("Mikrotik Manager\nPowered by M. Zhukovskyi ©.\nmzhukovskyi@fest.foundation")
        footer_label.setObjectName("footer-label")  # Для стилізації через setStyleSheet
        footer_label.setAlignment(Qt.AlignCenter)  # Центруємо текст

        left_layout.addWidget(button_frame)
        left_layout.addWidget(footer_label)  # Додаємо надпис внизу
        left_layout.addStretch()  # Додаємо розтягування для вирівнювання

        self.table = QTableWidget()
        self.table.setColumnCount(7)  # Додано стовпець для routerboard_firmware
        self.table.setHorizontalHeaderLabels(
            ["Pick", "Назва", "Хост", "Встановлена версія", "Остання версія", "Статус бекапу", "RouterBoard Firmware"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet("""
            QHeaderView::section {
                background-color: #3498db;
                color: white;
                padding: 6px;  /* Зменшений відступ для кращого відображення тексту */
                border: 1px solid #465c71;
                font-size: 15px;  /* Зменшений шрифт для заголовків таблиці */
                font-weight: bold;
            }
        """)

        # Налаштування ширини стовпців для коректного відображення назв
        self.table.setColumnWidth(0, 60)  # "Вибрати" (чекбокс)
        self.table.setColumnWidth(1, 103)  # "Назва"
        self.table.setColumnWidth(2, 120)  # "Хост"
        self.table.setColumnWidth(3, 160)  # "Встановлена версія" (збільшено для повного тексту)
        self.table.setColumnWidth(4, 130)  # "Остання версія" (збільшено для повного тексту)
        self.table.setColumnWidth(5, 120)  # "Статус бекапу"
        self.table.setColumnWidth(6, 200)  # "RouterBoard Firmware" (збільшено для повного тексту)

        self.load_devices()

        self.log_text.setMinimumWidth(400)
        self.log_text.setMaximumWidth(500)

        main_layout.addLayout(left_layout, 1)  # Лівий блок займає 1 частину
        main_layout.addWidget(self.table, 2)  # Таблиця займає 2 частини
        main_layout.addWidget(self.log_text, 1)  # Лог займає 1 частину

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.chatid_worker = ChatIdWorker(self.telegram_token, self.conn_str)
        self.chatid_worker.update_signal.connect(self.update_log)
        self.chatid_worker.finished_signal.connect(self.chatid_worker_finished)
        self.get_chatid_button.setEnabled(bool(self.telegram_token))

        # Ініціалізація потоків
        self.backup_worker = None
        self.check_updates_worker = None
        self.upgrade_worker = None
        self.routerboard_worker = None

    def load_settings(self):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                # Завантажуємо Telegram токен
                cursor.execute("SELECT TOP 1 [token] FROM [ManagerMikrotik].[dbo].[TelegramSettings]")
                row = cursor.fetchone()
                if row:
                    self.telegram_token = row.token
                else:
                    self.log_text.append("Не знайдено Telegram токен у базі даних!")

                # Завантажуємо FTP налаштування
                cursor.execute(
                    "SELECT TOP 1 [host], [username], [password], [dir] FROM [ManagerMikrotik].[dbo].[FTPSettings]")
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

                # Завантажуємо chat_ids
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

                # Сортуємо пристрої: ті, що потребують оновлення (installed_version < latest_version), зверху
                sorted_rows = []
                for row in rows:
                    if row.installed_version and row.latest_version:
                        installed = tuple(map(int, str(row.installed_version).split('.'))) if '.' in str(
                            row.installed_version) else (0,)
                        latest = tuple(map(int, str(row.latest_version).split('.'))) if '.' in str(
                            row.latest_version) else (0,)
                        needs_update = installed < latest
                    else:
                        needs_update = True  # Якщо версії відсутні, ставимо зверху для перевірки
                    sorted_rows.append((row, needs_update))

                sorted_rows.sort(key=lambda x: x[1], reverse=True)  # Зверху ті, що потребують оновлення

                self.table.setRowCount(0)
                self.devices_data = []
                for i, (row, _) in enumerate(sorted_rows):
                    self.table.insertRow(i)
                    checkbox = QCheckBox()
                    checkbox.setFont(QFont("Arial", 18))  # Збільшений шрифт для чекбоксів
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
                    self.devices_data.append({
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
                    })

                    # Оновлюємо версії, якщо вони відсутні або застарілі
                    if not row.installed_version or not row.latest_version or not row.routerboard_firmware:
                        installed_ver, latest_ver, routerboard_firmware = check_versions({
                            "host": row.host,
                            "user": row.username,
                            "password": row.password
                        })
                        if installed_ver and latest_ver and routerboard_firmware:
                            self.update_versions_and_firmware(row.id, installed_ver, latest_ver, routerboard_firmware)
                self.log_text.append("Пристрої завантажено та відсортовані.")
        except Exception as e:
            self.log_text.append(f"Помилка завантаження пристроїв: {str(e)}")
            traceback.print_exc()
            self.table.setRowCount(0)
            for i in range(10):
                self.table.insertRow(i)
                checkbox = QCheckBox()
                checkbox.setFont(QFont("Arial", 14))  # Збільшений шрифт для чекбоксів
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
                """, installed_version, latest_version, routerboard_firmware, device_id)
                conn.commit()
            self.log_text.append(
                f"Оновлено версії та прошивку для пристрою ID {device_id}: {installed_version} -> {latest_version}, RouterBoard Firmware: {routerboard_firmware}")
        except Exception as e:
            self.log_text.append(f"Помилка оновлення версій та прошивки для пристрою ID {device_id}: {str(e)}")
            traceback.print_exc()

    def update_device_status(self, device_id, status, final_status):
        try:
            with pyodbc.connect(self.conn_str, timeout=30) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE [ManagerMikrotik].[dbo].[MikroTikDevices] 
                    SET backup_status = ?, backup_status_final = ?, installed_version = installed_version, 
                        latest_version = latest_version, routerboard_firmware = routerboard_firmware
                    WHERE id = ?
                """, status[:200], final_status, device_id)  # Обмежуємо довжину до 200 символів
                conn.commit()
        except Exception as e:
            self.update_signal.emit(f"Помилка оновлення статусу пристрою: {str(e)}")
            traceback.print_exc()

    def perform_backup(self):
        if not self.telegram_token or not self.ftp_config:
            self.log_text.append("Помилка: Не завантажено Telegram токен або FTP налаштування!")
            return

        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.backup_worker = BackupWorker(selected_devices, self.conn_str, self.telegram_token, self.ftp_config)
        self.backup_worker.update_signal.connect(self.update_log)
        self.backup_worker.finished_signal.connect(self.backup_finished)
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

        self.check_updates_worker = CheckUpdatesWorker(selected_devices, self.conn_str, self.telegram_token)
        self.check_updates_worker.update_signal.connect(self.update_log)
        self.check_updates_worker.finished_signal.connect(self.check_updates_finished)
        self.check_updates_worker.start()
        self.check_update_button.setEnabled(False)

    def check_updates_finished(self):
        self.check_update_button.setEnabled(True)
        self.log_text.append("Перевірка оновлень завершена.")
        self.load_devices()  # Оновлюємо таблицю для відображення нових версій

    def perform_upgrade(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.upgrade_worker = UpgradeWorker(selected_devices, self.conn_str, self.telegram_token)
        self.upgrade_worker.update_signal.connect(self.update_log)
        self.upgrade_worker.finished_signal.connect(self.upgrade_finished)
        self.upgrade_worker.start()
        self.upgrade_button.setEnabled(False)

    def upgrade_finished(self):
        self.upgrade_button.setEnabled(True)
        self.log_text.append("Оновлення завершено.")
        self.load_devices()  # Оновлюємо таблицю для відображення нових версій

    def perform_routerboard(self):
        selected_devices = self.get_selected_devices()
        if not selected_devices:
            self.log_text.append("Попередження: Виберіть хоча б один пристрій!")
            return

        self.routerboard_worker = RouterBoardWorker(selected_devices, self.conn_str, self.telegram_token)
        self.routerboard_worker.update_signal.connect(self.update_log)
        self.routerboard_worker.finished_signal.connect(self.routerboard_finished)
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
                update_needed = tuple(map(int, str(installed_version).split('.'))) < tuple(
                    map(int, str(latest_version).split('.')))
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
        self.close()
        if not QApplication.instance().topLevelWidgets():  # Перевірка, чи немає відкритих вікон
            login_window = LoginWindow()
            login_window.show()

    def chatid_worker_finished(self):
        self.get_chatid_button.setEnabled(True)
        self.stop_chatid_button.setEnabled(False)
        self.log_text.append("Збір chat_id завершено.")
        self.load_settings()  # Оновлюємо CHAT_IDS після завершення


if __name__ == "__main__":
    check_and_install_dependencies()

    try:
        app = QApplication(sys.argv)
        # Якщо встановлено qdarkstyle, застосовуємо темну тему
        if 'qdarkstyle' in sys.modules:
            app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        else:
            # Альтернативний стиль без qdarkstyle
            app.setStyleSheet("""
                QMainWindow {
                    background-color: #2c3e50;
                    color: #ffffff;
                }
                QPushButton {
                    background-color: #3498db;
                    color: white;
                    padding: 6px 12px;  /* Зменшені відступи для вужчих кнопок */
                    border-radius: 6px;  /* Зменшений радіус для компактності */
                    font-size: 12px;  /* Зменшений шрифт для кнопок, щоб текст поміщався */
                    min-width: 100px;  /* Зменшена мінімальна ширина кнопок (можете налаштувати на свій розсуд) */
                }
                QPushButton:hover {
                    background-color: #2980b9;
                }
                QTableWidget {
                    background-color: #34495e;
                    color: #ffffff;
                    border: 1px solid #465c71;
                    font-size: 14px;  /* Збільшений шрифт для таблиці */
                }
                QTableWidget::item {
                    padding: 4px;
                }
                QTextEdit {
                    background-color: #34495e;
                    color: #ffffff;
                    border: 1px solid #465c71;
                    font-size: 14px;  /* Збільшений шрифт для логу */
                }
                QLabel {
                    font-size: 14px;  /* Збільшений шрифт для міток */
                    color: #ffffff;
                }
                QLineEdit {
                    background-color: #4a6074;
                    color: #ffffff;
                    border: 1px solid #465c71;
                    border-radius: 4px;
                    padding: 8px;
                    font-size: 14px;  /* Збільшений шрифт для полів введення */
                }
                QCheckBox {
                    color: #ffffff;
                    font-size: 18px;  /* Збільшений шрифт для чекбоксів */
                }
                QFrame {
                    background-color: #34495e;
                    border: 1px solid #465c71;
                    border-radius: 6px;  /* Зменшений радіус для компактності */
                }
                .footer-label {
                    font-size: 20px;  /* Великий шрифт для надпису */
                    color: rgba(255, 255, 255, 128);  /* Білий колір із 50% прозорістю */
                    margin-top: 10px;  /* Відступ зверху */
                }
            """)
        login_window = LoginWindow()
        login_window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"Критична помилка: {str(e)}")
        traceback.print_exc()