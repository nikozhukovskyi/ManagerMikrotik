@echo off
python.exe -m pip install --upgrade pip
echo Installing required Python libraries...
pip install requests
pip install netmiko
pip install paramiko
pip install json
pip install datetime
echo Installation complete.
python.exe -m pip install --upgrade pip
timeout /t 5