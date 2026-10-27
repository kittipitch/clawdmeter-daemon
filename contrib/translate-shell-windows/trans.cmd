@echo off
REM translate-shell for a native-Windows clawdmeter-daemon.
REM trans is a bash + gawk script; Git for Windows ships both. This wrapper runs
REM trans.sh (next to it) with Git's tools first on PATH, plus a tiny hexdump shim
REM (trans-shims\hexdump) because Git for Windows has od but no hexdump.
REM Installed by install-trans.bat; the daemon finds it as "trans" on PATH.
setlocal
set "GITUSR=%ProgramFiles%\Git\usr\bin"
if not exist "%GITUSR%\bash.exe" (
    echo trans: Git for Windows not found at "%ProgramFiles%\Git" 1>&2
    exit /b 9
)
set "PATH=%~dp0trans-shims;%GITUSR%;%PATH%"
"%GITUSR%\bash.exe" "%~dp0trans.sh" %*
