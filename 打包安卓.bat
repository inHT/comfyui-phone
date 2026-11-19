@echo off
cd /d "%~dp0android"
set "JAVA_HOME=C:\Program Files\BellSoft\LibericaJDK-17"
set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
if not exist "%ANDROID_HOME%\platforms\android-34" (
  echo 还没有 Android SDK，先让开发环境装好再打包。
  pause
  exit /b 1
)
if exist "%LOCALAPPDATA%\Android\gradle-8.9\bin\gradle.bat" (
  "%LOCALAPPDATA%\Android\gradle-8.9\bin\gradle.bat" assembleDebug
) else (
  call gradlew.bat assembleDebug
)
if errorlevel 1 (
  pause
  exit /b 1
)
copy /Y "app\build\outputs\apk\debug\app-debug.apk" "..\基础文生图.apk"
echo.
echo 安装包已写成：%~dp0基础文生图.apk
pause
