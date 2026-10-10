@echo off
setlocal
set "CLIPBOARD_SYNC_SELF=%~f0"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:CLIPBOARD_SYNC_SELF,[Text.Encoding]::UTF8); & ([ScriptBlock]::Create(($s -split '(?m)^# CLIPBOARD_POWERSHELL\r?$',2)[1]))"
set "CLIPBOARD_SYNC_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %CLIPBOARD_SYNC_EXIT%
# CLIPBOARD_POWERSHELL
# 两个 CMD 需一起部署；共用上传入口内嵌的实现，下载路径不执行 push。
try {
    $root = Split-Path -Parent $env:CLIPBOARD_SYNC_SELF
    $upload = Join-Path $root '上传剪贴板.cmd'
    $source = [IO.File]::ReadAllText($upload, [Text.Encoding]::UTF8)
    & ([ScriptBlock]::Create(($source -split '(?m)^# CLIPBOARD_POWERSHELL\r?$', 2)[1])) -Action Download -Root $root
}
catch {
    Write-Host ('拉取失败，请确认两个 CMD 都在同一目录。' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
