@echo off

python -m PyInstaller --onefile --windowed --clean --name "Visual Studio Build Stopwatch" --icon "VS_Build_Stopwatch.ico" --add-data "VS_Build_Stopwatch.ico;." --collect-all pygame --hidden-import pystray._win32 "VS_Build_Stopwatch.py"

if exist "Visual Studio Build Stopwatch.exe" del "Visual Studio Build Stopwatch.exe"

move "dist\Visual Studio Build Stopwatch.exe"



