from cryptography.fernet import Fernet

# Використовуємо ваш ключ із config.ini
key = "Re0Bqntz-uRHpm1aw5omOfLvuX8LMmNtUUxdGX42mLw="
cipher = Fernet(key)

# Паролі
#db_password = "Ghjuhfv93"
ftp_password = "RxJoQUL6X1"  # Замініть на ваш реальний пароль FTP

# Шифрування паролів
#encrypted_db_password = cipher.encrypt(db_password.encode()).decode()
encrypted_ftp_password = cipher.encrypt(ftp_password.encode()).decode()

#print(f"Зашифрований пароль бази даних: {encrypted_db_password}")
print(f"Зашифрований пароль FTP: {encrypted_ftp_password}")