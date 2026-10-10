@echo off
setlocal
set "CLIPBOARD_SYNC_SELF=%~f0"
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:CLIPBOARD_SYNC_SELF,[Text.Encoding]::UTF8); & ([ScriptBlock]::Create(($s -split '(?m)^# CLIPBOARD_POWERSHELL\r?$',2)[1]))"
set "CLIPBOARD_SYNC_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %CLIPBOARD_SYNC_EXIT%
# CLIPBOARD_POWERSHELL
param(
    [ValidateSet('Upload', 'Download')][string]$Action = 'Upload',
    [string]$Root = (Split-Path -Parent $env:CLIPBOARD_SYNC_SELF)
)

# 此文件与“拉取剪贴板.cmd”一起放在 workbrige 根目录。
# CMD 头部只用 ASCII；下方由 PowerShell 显式按 UTF-8 读取。
# 只同步纯文字；使用同一远端的独立 clipboard-sync 分支。
# 原仓库仅用于读取连接配置，不切换分支、不暂存文件、不执行原同步脚本。
# 上传内容会保存在 Git 历史中；仅在用户双击上传入口时读取剪贴板。
Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$script:ClipUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$script:ClipRef = 'refs/heads/clipboard-sync'

function ConvertTo-ClipArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-ClipGit {
    param(
        [string]$Repo,
        [string[]]$Arguments,
        [AllowEmptyString()][string]$InputText = '',
        [switch]$AllowFailure
    )
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'git.exe'
    $gitArgs = @('-C', $Repo, '-c', 'core.hooksPath=NUL', '-c', 'commit.gpgSign=false',
        '-c', 'push.followTags=false', '-c', 'remote.origin.mirror=false',
        '-c', 'user.name=Clipboard Sync', '-c', 'user.email=clipboard@local.invalid') + $Arguments
    $info.Arguments = ($gitArgs | ForEach-Object { ConvertTo-ClipArgument $_ }) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $script:ClipUtf8
    $info.StandardErrorEncoding = $script:ClipUtf8
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -like 'GIT_*') { $info.EnvironmentVariables.Remove($key) }
    }
    $info.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $info.EnvironmentVariables['GCM_INTERACTIVE'] = 'Never'
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    $outputBytes = New-Object System.IO.MemoryStream
    try {
        $null = $process.Start()
        # 直接读字节，避免 StreamReader 把正文开头的 U+FEFF 当成 BOM 吞掉。
        $stdout = $process.StandardOutput.BaseStream.CopyToAsync($outputBytes)
        $stderr = $process.StandardError.ReadToEndAsync()
        $bytes = $script:ClipUtf8.GetBytes($InputText)
        $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(45000)) {
            $process.Kill()
            throw '连接超时，请检查网络后重试。'
        }
        $null = $stdout.GetAwaiter().GetResult()
        $result = [pscustomobject]@{
            Code = $process.ExitCode
            Out = $script:ClipUtf8.GetString($outputBytes.ToArray())
            Err = $stderr.GetAwaiter().GetResult()
        }
        if ($result.Code -ne 0 -and -not $AllowFailure) {
            $detail = ($result.Err + "`n" + $result.Out).Trim()
            $detail = $detail -replace '(https?://)[^/\s@]+@', '$1***@'
            throw "Git 操作失败：$detail"
        }
        return $result
    }
    finally {
        $process.Dispose()
        $outputBytes.Dispose()
    }
}

function Get-ClipRemoteHead([string]$Repo) {
    $found = Invoke-ClipGit $Repo @('ls-remote', '--exit-code', '--heads', 'origin', $script:ClipRef) -AllowFailure
    if ($found.Code -eq 2) { return $null }
    if ($found.Code -ne 0) { throw '无法读取远端剪贴板，请检查网络和现有 Git 连接。' }
    $null = Invoke-ClipGit $Repo @('fetch', '--quiet', '--depth=1', '--no-tags', 'origin', $script:ClipRef)
    return (Invoke-ClipGit $Repo @('rev-parse', 'FETCH_HEAD')).Out.Trim()
}

function Assert-ClipCanUpload([string]$Repo, [string]$Parent) {
    # 首次上传用空提交探测权限，不读取或发送本机剪贴板。
    $candidate = $Parent
    if (-not $candidate) {
        $tree = (Invoke-ClipGit $Repo @('mktree')).Out.Trim()
        $candidate = (Invoke-ClipGit $Repo @('commit-tree', $tree, '-m', 'Clipboard permission probe')).Out.Trim()
    }
    $probe = Invoke-ClipGit $Repo @('push', '--dry-run', '--no-verify', '--porcelain',
        'origin', "${candidate}:$script:ClipRef") -AllowFailure
    if ($probe.Code -ne 0) {
        throw '本机未通过上传权限检查（只读权限、网络或远端状态变化）。请使用“拉取剪贴板.cmd”；有写权限的机器可检查连接后重试。'
    }
}

function Get-SyncClipboardText {
    Add-Type -AssemblyName System.Windows.Forms
    if (-not [System.Windows.Forms.Clipboard]::ContainsText([System.Windows.Forms.TextDataFormat]::UnicodeText)) {
        throw '当前剪贴板没有文字。请先复制需要上传的文字，再运行本文件。'
    }
    $text = [System.Windows.Forms.Clipboard]::GetText([System.Windows.Forms.TextDataFormat]::UnicodeText)
    if ($text.Length -eq 0) { throw '剪贴板为空，本次没有上传。' }
    return $text
}

function Set-SyncClipboardText([string]$Text) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Clipboard]::SetText($Text, [System.Windows.Forms.TextDataFormat]::UnicodeText)
}

function Invoke-ClipboardTransfer([string]$Repo, [string]$Mode) {
    $parent = Get-ClipRemoteHead $Repo
    if ($Mode -eq 'Download') {
        if (-not $parent) { throw '还没有共享剪贴板。请先在有写权限的机器上运行一次“上传剪贴板.cmd”。' }
        $text = (Invoke-ClipGit $Repo @('cat-file', 'blob', "${parent}:clipboard.txt")).Out
        if ($text.Length -eq 0 -or $text.Contains([string][char]0)) {
            throw '远端剪贴板内容无效，未替换本机剪贴板。'
        }
        Set-SyncClipboardText $text
        Write-Host '已拉取到本机剪贴板，现在可以 Ctrl+V 粘贴。' -ForegroundColor Green
        return
    }
    Assert-ClipCanUpload $Repo $parent
    $text = Get-SyncClipboardText
    if ($text.Length -eq 0) { throw '剪贴板为空，本次没有上传。' }
    if ($parent) {
        $previous = Invoke-ClipGit $Repo @('cat-file', 'blob', "${parent}:clipboard.txt") -AllowFailure
        if ($previous.Code -eq 0 -and [string]::Equals($previous.Out, $text, [StringComparison]::Ordinal)) {
            Write-Host '当前文字与远端剪贴板相同，无需重复上传。' -ForegroundColor Green
            return
        }
    }
    $blob = (Invoke-ClipGit $Repo @('hash-object', '-w', '--stdin') -InputText $text).Out.Trim()
    $tree = (Invoke-ClipGit $Repo @('mktree') -InputText "100644 blob $blob`tclipboard.txt`n").Out.Trim()
    $commitArgs = @('commit-tree', $tree, '-m', 'Update shared clipboard')
    if ($parent) { $commitArgs += @('-p', $parent) }
    $commit = (Invoke-ClipGit $Repo $commitArgs).Out.Trim()
    $pushed = Invoke-ClipGit $Repo @('push', '--no-verify', '--porcelain',
        'origin', "${commit}:$script:ClipRef") -AllowFailure
    if ($pushed.Code -ne 0) {
        throw '上传未完成：可能另一台机器刚更新了剪贴板，或当前连接没有写权限。没有强制覆盖远端，请检查后重试。'
    }
    Write-Host '已上传当前文字，其他机器现在可以拉取。' -ForegroundColor Green
}

function Invoke-ClipboardSync([string]$Root, [string]$Mode) {
    $sourceRepo = Join-Path $Root 'workspace'
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRepo '.git'))) {
        throw '没有找到同目录下的 workspace 仓库。请将两个新 CMD 一起放在原 workbrige 根目录。'
    }
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw '没有找到 Git，请先安装 Git for Windows。' }
    $url = (Invoke-ClipGit $sourceRepo @('remote', 'get-url', 'origin')).Out.Trim()
    $pushUrl = (Invoke-ClipGit $sourceRepo @('remote', 'get-url', '--push', 'origin')).Out.Trim()
    if (-not $url) { throw '原仓库没有配置 origin，请先配置原同步工具。' }
    $cacheRoot = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) 'GitSync-Clipboard'))
    $cache = [IO.Path]::GetFullPath((Join-Path $cacheRoot ('run-' + [guid]::NewGuid().ToString('N'))))
    $null = New-Item -ItemType Directory -Path $cache
    try {
        $null = Invoke-ClipGit $cache @('init', '--bare', '--quiet', '.')
        $null = Invoke-ClipGit $cache @('remote', 'add', 'origin', $url)
        $null = Invoke-ClipGit $cache @('remote', 'set-url', '--push', 'origin', $pushUrl)
        # 继承原仓库选用的 SSH 命令和凭据助手，不改变原仓库配置。
        foreach ($key in @('core.sshCommand', 'credential.helper', 'credential.useHttpPath')) {
            $setting = Invoke-ClipGit $sourceRepo @('config', '--get-all', $key) -AllowFailure
            if ($setting.Code -eq 0) {
                if ($key -eq 'credential.helper') {
                    $null = Invoke-ClipGit $cache @('config', '--add', $key, '')
                }
                $values = $setting.Out -replace '\r?\n$', ''
                foreach ($value in ($values -split '\r?\n')) {
                    $null = Invoke-ClipGit $cache @('config', '--add', $key, $value)
                }
            }
            elseif ($key -eq 'core.sshCommand') {
                $null = Invoke-ClipGit $cache @('config', $key,
                    'ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=2')
            }
        }
        Invoke-ClipboardTransfer $cache $Mode
    }
    finally {
        # 仅删除本次创建、且绝对路径位于缓存根目录内的临时仓库。
        $prefix = $cacheRoot.TrimEnd('\') + '\'
        if ($cache.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $cache)) {
            Remove-Item -LiteralPath $cache -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    $label = if ($Action -eq 'Upload') { '上传当前剪贴板' } else { '拉取到当前剪贴板' }
    Write-Host $label -ForegroundColor Cyan
    Invoke-ClipboardSync $Root $Action
    exit 0
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
