@echo off
rem TrapNote を、研究用の版で起動する（カタログ・無作為の取り出し・評価用の組など、全部の機能を出す）。
rem ふつうの 起動.bat は公開版。どの版も同じプログラムと同じプロジェクトを使い、画面に出す機能だけが違う
call "%~dp0起動.bat" --edition research %*
