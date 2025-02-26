-- Створення бази даних UserAuthDB
CREATE DATABASE ManagerMikrotik;
GO

-- Використання бази даних UserAuthDB
USE ManagerMikrotik;
GO

-- Створення таблиці [MikroTikDevices]
CREATE TABLE [dbo].[MikroTikDevices] (
    [id] INT IDENTITY(1,1) PRIMARY KEY, -- Автоматично генерований ідентифікатор
    [name] NVARCHAR(100) NOT NULL, -- Назва пристрою
    [host] NVARCHAR(100) NOT NULL, -- IP-адреса або хост
    [username] NVARCHAR(100), -- Логін для підключення
    [password] NVARCHAR(100), -- Пароль для підключення (може бути зашифрованим у реальному проєкті)
    [installed_version] NVARCHAR(50), -- Встановлена версія ПО
    [latest_version] NVARCHAR(50), -- Остання доступна версія
    [backup_status] NVARCHAR(200), -- Статус останнього бекапу
    [backup_status_final] NVARCHAR(200), -- Останній фінальний статус бекапу
    [routerboard_firmware] NVARCHAR(50) -- Версія прошивки RouterBoard
);
GO

-- Створення таблиці [TelegramSettings]
CREATE TABLE [dbo].[TelegramSettings] (
    [token] NVARCHAR(100) PRIMARY KEY -- Токен API Telegram (унікальний)
);
GO

-- Створення таблиці [FTPSettings]
CREATE TABLE [dbo].[FTPSettings] (
    [host] NVARCHAR(100) NOT NULL, -- Хост FTP-сервера
    [username] NVARCHAR(100) NOT NULL, -- Логін FTP
    [password] NVARCHAR(100) NOT NULL, -- Пароль FTP
    [dir] NVARCHAR(200) NOT NULL, -- Директорія на FTP
    PRIMARY KEY (host, username) -- Композитний первинний ключ для унікальності хоста та логіна
);
GO

-- Створення таблиці [TelegramChatIds]
CREATE TABLE [dbo].[TelegramChatIds] (
    [chat_id] NVARCHAR(50) PRIMARY KEY -- Ідентифікатор чату (може бути числом або рядком)
);
GO

-- Додавання індексів для оптимізації (опціонально)
CREATE INDEX IX_MikroTikDevices_Host ON [dbo].[MikroTikDevices] ([host]);
CREATE INDEX IX_MikroTikDevices_Name ON [dbo].[MikroTikDevices] ([name]);
GO

-- Початкове заповнення даних для тестування

-- Дані для [MikroTikDevices]
INSERT INTO [dbo].[MikroTikDevices] ([name], [host], [username], [password], [installed_version], [latest_version], [backup_status], [backup_status_final], [routerboard_firmware])
VALUES 
    ('MikrotikOffice', '0.0.0.0', 'admin', 'pass', '', '', '', '', '');
GO

-- Дані для [TelegramSettings]
INSERT INTO [dbo].[TelegramSettings] ([token])
VALUES ('token');
GO

-- Дані для [FTPSettings]
INSERT INTO [dbo].[FTPSettings] ([host], [username], [password], [dir])
VALUES ('ftp.kryjivka.com.ua', '', '', '/');
GO

-- Дані для [TelegramChatIds]
INSERT INTO [dbo].[TelegramChatIds] ([chat_id])
VALUES (''), ('');
GO