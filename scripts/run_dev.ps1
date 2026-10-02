param([int]$Port = 8876)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $DevPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
}
& $DevPython -m pip install -r (Join-Path $ProjectRoot 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw '安装开发依赖失败' }
$PriorDataDir = $env:CHEM_DATA_DIR
$PriorPort = $env:CHEM_PORT
$PriorDisableAI = $env:CHEM_DISABLE_AI
try {
    $env:CHEM_DATA_DIR = Join-Path $ProjectRoot '.dev/data'
    $env:CHEM_PORT = [string]$Port
    $env:CHEM_DISABLE_AI = '1'
    & $DevPython (Join-Path $ProjectRoot 'scripts/seed_dev.py')
    if ($LASTEXITCODE -ne 0) { throw '开发样本导入失败' }
    Write-Host "隔离开发预览 http://127.0.0.1:$Port  数据目录 $env:CHEM_DATA_DIR  AI 已停用"
    & $DevPython (Join-Path $ProjectRoot 'app.py') serve
} finally {
    $env:CHEM_DATA_DIR = $PriorDataDir
    $env:CHEM_PORT = $PriorPort
    $env:CHEM_DISABLE_AI = $PriorDisableAI
}
