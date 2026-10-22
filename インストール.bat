@echo off
rem 既にある conda（Miniconda / Anaconda）で、TrapNote の環境（env\trapnote-env.yml）を作る。あれば更新する
rem conda.exe を直接呼ぶと「OpenSSL appears to be unavailable」で失敗することがあるので、先に activate.bat で conda の PATH を通す
setlocal
set "ROOT="
for %%p in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "C:\ProgramData\miniconda3" "C:\ProgramData\Anaconda3" "C:\trapnote\miniconda3" "C:\areacheck\miniconda3") do (
  if not defined ROOT if exist "%%~p\Scripts\activate.bat" set "ROOT=%%~p"
)
if not defined ROOT if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\Scripts\activate.bat" set "ROOT=%CONDA_PREFIX%"
if not defined ROOT (
  echo conda が見つかりません。Miniconda を入れるか（https://docs.conda.io/en/latest/miniconda.html）、areacheck のインストール.bat を先に実行してください。
  pause
  exit /b 1
)
echo conda: %ROOT%
call "%ROOT%\Scripts\activate.bat" "%ROOT%"
if errorlevel 1 (
  echo conda を有効にできません。
  pause
  exit /b 1
)
call conda env list | findstr /B /C:"trapnote " >nul 2>nul
if %errorlevel%==0 (
  echo 環境 trapnote を更新します...
  call conda env update -n trapnote -f "%~dp0env\trapnote-env.yml" --prune
) else (
  echo 環境 trapnote を作ります（数分かかります）...
  call conda env create -f "%~dp0env\trapnote-env.yml"
)
if errorlevel 1 (
  echo.
  echo 失敗しました。上のメッセージを確認してください。
  echo （OpenSSL のエラーが出るときは、スタートメニューの「Anaconda Prompt」を開いて、この bat をそこから実行してください）
  pause
  exit /b 1
)
echo.
rem MegaDetector 本体は依存の衝突（ultralytics-yolov5 と yolov5）を避けるため、依存なしで入れる
echo MegaDetector を入れます...
call conda activate trapnote
call pip install --no-deps megadetector==10.0.28
if errorlevel 1 (
  echo.
  echo MegaDetector を入れられませんでした（ネットワークを確認して、もう一度実行してください）。
  pause
  exit /b 1
)
echo.
rem NVIDIA の GPU があるのに torch が CPU 版なら、PyTorch の CUDA 12.8 用の置き場から入れ直す（pip が PyPI の CPU 版を選ぶことがあるため）
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  call python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
  if errorlevel 1 (
    echo torch が CPU 版なので CUDA 版を入れ直します（2 GB ほど。数分）...
    call pip install --upgrade --force-reinstall --no-deps --index-url https://download.pytorch.org/whl/cu128 torch torchvision
  )
)
rem 足りないモジュールがあれば足す（古い版の env で作った環境向け）
call pip install dill send2trash
echo GPU の確認:
call python -c "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '(GPU なし。CPU で動きます。NVIDIA のドライバが古いと GPU を使えないので、ドライバを更新してください)')"
echo.
echo できました。ショートカットを作る.bat でデスクトップにアイコンを作れます。起動.bat で起動します。
echo 「自動判定」の初回はモデルを取ってくるので時間がかかります（MegaDetector は GitHub、SpeciesNet は Kaggle から）。
pause
