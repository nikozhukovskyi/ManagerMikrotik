import pyodbc
from cryptography.fernet import Fernet

key = b"Re0Bqntz-uRHpm1aw5omOfLvuX8LMmNtUUxdGX42mLw="
cipher = Fernet(key)

encrypted_password = "gAAAAABnxOqkwC188tqGGaz_i_fLult2gLNmFOisLO1zED1zyDULFBvnduH_p3XacWDYdKlu1Q-Dkn_d-HaQ_XTnrweGJuJdUg=="
decrypted_password = cipher.decrypt(encrypted_password.encode()).decode()

conn_str = (
    "DRIVER={SQL Server};"
    "SERVER=localhost;"  # Змініть на ваш сервер
    "DATABASE=ManagerMikrotik;"
    "UID=sa;"
    "PWD=" + decrypted_password
)

try:
    with pyodbc.connect(conn_str) as conn:
        print("Підключення успішне!")
except Exception as e:
    print(f"Помилка: {e}")