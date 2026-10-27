@echo off
setlocal
REM Install translate-shell for a native-Windows clawdmeter-daemon, so non-English
REM calendar titles are translated to English like on macOS/Linux instead of being
REM stripped on the device. Needs Git for Windows (bash + gawk) and curl.exe
REM (built into Windows 10 1803+). Installs into %USERPROFILE%\.local\bin.

set "DEST=%USERPROFILE%\.local\bin"
set "SRC=%~dp0"
set "TRANS_URL=https://raw.githubusercontent.com/soimort/translate-shell/gh-pages/trans"

if not exist "%ProgramFiles%\Git\usr\bin\gawk.exe" (
    echo Git for Windows not found at "%ProgramFiles%\Git". Install it from https://git-scm.com first.
    goto :fail
)
if not exist "%DEST%\trans-shims" mkdir "%DEST%\trans-shims" || goto :fail

if exist "%DEST%\trans.sh" copy /y "%DEST%\trans.sh" "%DEST%\trans.sh.bak" >nul
echo Downloading translate-shell ...
curl.exe -fsSL "%TRANS_URL%" -o "%DEST%\trans.sh" || ( echo Download failed. & goto :fail )
copy /y "%SRC%trans.cmd" "%DEST%\trans.cmd" >nul || goto :fail
copy /y "%SRC%hexdump" "%DEST%\trans-shims\hexdump" >nul || goto :fail

echo Testing (French to English through Bing) ...
call "%DEST%\trans.cmd" -e bing -b :en "bonjour le monde" || ( echo Test call failed. & goto :fail )

echo %PATH% | find /i "%DEST%" >nul || (
    echo.
    echo NOTE: %DEST% is not on your PATH. Add it under Settings ^> System ^> About ^>
    echo Advanced system settings ^> Environment Variables ^(user Path^), then sign out
    echo and back in so the tray daemon sees it.
)
echo.
echo Done. Restart the daemon ^(tray ^> Quit, then start-daemon.bat^) if it was running
echo before %DEST% was on PATH; otherwise the next calendar poll picks trans up.
goto :end

:fail
echo.
echo translate-shell install failed - see the messages above.
:end
endlocal
