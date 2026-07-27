@echo off
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%~dp0memo" %*
) else (
  python "%~dp0memo" %*
)
