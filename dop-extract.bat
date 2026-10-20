@echo off
REM Drag .orc files or folders onto this file.
python "%~dp0dop_extract.py" %*
pause
