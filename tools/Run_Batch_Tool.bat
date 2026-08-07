@echo off
rem Launches batch_import_gui.pyw with the real Python install directly, bypassing
rem the .pyw file association (which on this machine routes through the Microsoft
rem Store app-execution-alias stub at WindowsApps\python3.exe instead of a real
rem interpreter - "Unable to create process... The system cannot find the file
rem specified").
set REAL_PYTHONW=C:\Users\Jeff\AppData\Local\Programs\Python\Python313\pythonw.exe
set SCRIPT_DIR=%~dp0

if not exist "%REAL_PYTHONW%" (
    echo Could not find Python at:
    echo   %REAL_PYTHONW%
    echo Edit this .bat file's REAL_PYTHONW line to point at your actual pythonw.exe.
    pause
    exit /b 1
)

start "" "%REAL_PYTHONW%" "%SCRIPT_DIR%batch_import_gui.pyw"
