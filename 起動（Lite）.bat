@echo off
rem TrapNote を、Lite の版で起動する（MegaDetector による検出と、目視の確認まで。種の AI と追加学習は出さない）。
rem ふつうの 起動.bat は公開版。どの版も同じプログラムと同じプロジェクトを使い、画面に出す機能だけが違う
call "%~dp0起動.bat" --edition lite %*
