@echo off
rem デスクトップに「TrapNote」のショートカットを作る
setlocal
set "PY="
for %%p in ("%USERPROFILE%\.conda\envs\trapnote\python.exe" "%USERPROFILE%\anaconda3\envs\trapnote\python.exe" "%USERPROFILE%\miniconda3\envs\trapnote\python.exe" "%LOCALAPPDATA%\anaconda3\envs\trapnote\python.exe" "%LOCALAPPDATA%\miniconda3\envs\trapnote\python.exe" "C:\ProgramData\Anaconda3\envs\trapnote\python.exe" "C:\ProgramData\miniconda3\envs\trapnote\python.exe" "C:\trapnote\miniconda3\envs\trapnote\python.exe" "C:\areacheck\miniconda3\envs\trapnote\python.exe") do (
  if not defined PY if exist %%p set "PY=%%~p"
)
for %%p in ("%USERPROFILE%\.conda\envs\rfdetr-env\python.exe" "%USERPROFILE%\anaconda3\envs\rfdetr-env\python.exe" "%USERPROFILE%\miniconda3\envs\rfdetr-env\python.exe" "%LOCALAPPDATA%\anaconda3\envs\rfdetr-env\python.exe" "%LOCALAPPDATA%\miniconda3\envs\rfdetr-env\python.exe" "C:\ProgramData\Anaconda3\envs\rfdetr-env\python.exe" "C:\ProgramData\miniconda3\envs\rfdetr-env\python.exe" "C:\trapnote\miniconda3\envs\rfdetr-env\python.exe" "C:\areacheck\miniconda3\envs\rfdetr-env\python.exe") do (
  if not defined PY if exist %%p set "PY=%%~p"
)
if not defined PY (
  echo conda 環境 trapnote の Python が見つかりません。インストール.bat を実行するか、README.md を見てください。
  pause
  exit /b 1
)
"%PY%" "%~dp0app.py" --make-shortcut
pause
