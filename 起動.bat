@echo off
rem TrapNote を起動する（conda 環境 trapnote（なければ rfdetr-env）の pythonw.exe で app.py を動かす）
setlocal
set "APP=%~dp0app.py"
set "PY="
for %%p in ("%USERPROFILE%\.conda\envs\trapnote\pythonw.exe" "%USERPROFILE%\anaconda3\envs\trapnote\pythonw.exe" "%USERPROFILE%\miniconda3\envs\trapnote\pythonw.exe" "%LOCALAPPDATA%\anaconda3\envs\trapnote\pythonw.exe" "%LOCALAPPDATA%\miniconda3\envs\trapnote\pythonw.exe" "C:\ProgramData\Anaconda3\envs\trapnote\pythonw.exe" "C:\ProgramData\miniconda3\envs\trapnote\pythonw.exe" "C:\trapnote\miniconda3\envs\trapnote\pythonw.exe" "C:\areacheck\miniconda3\envs\trapnote\pythonw.exe") do (
  if not defined PY if exist %%p set "PY=%%~p"
)
for %%p in ("%USERPROFILE%\.conda\envs\rfdetr-env\pythonw.exe" "%USERPROFILE%\anaconda3\envs\rfdetr-env\pythonw.exe" "%USERPROFILE%\miniconda3\envs\rfdetr-env\pythonw.exe" "%LOCALAPPDATA%\anaconda3\envs\rfdetr-env\pythonw.exe" "%LOCALAPPDATA%\miniconda3\envs\rfdetr-env\pythonw.exe" "C:\ProgramData\Anaconda3\envs\rfdetr-env\pythonw.exe" "C:\ProgramData\miniconda3\envs\rfdetr-env\pythonw.exe" "C:\trapnote\miniconda3\envs\rfdetr-env\pythonw.exe" "C:\areacheck\miniconda3\envs\rfdetr-env\pythonw.exe") do (
  if not defined PY if exist %%p set "PY=%%~p"
)
if not defined PY (
  echo conda 環境 trapnote の Python が見つかりません。インストール.bat を実行するか、README.md を見てください。
  pause
  exit /b 1
)
start "" "%PY%" "%APP%" %*
