# Одинаковая среда на любом компьютере (дом / работа).
#
#   powershell -ExecutionPolicy Bypass -File setup_env.ps1
#
# Создаёт .venv (если нет), ставит точные версии из requirements-dev.txt,
# браузер Chromium для проверки AMDM и запускает проверку среды и тесты.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$want = (Get-Content .python-version).Trim()
if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "Создаю .venv на Python $want"
    py -$want -m venv .venv
}
$py = ".venv\Scripts\python.exe"
$have = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($have -ne $want) {
    Write-Warning "В .venv Python $have, а нужен $want (.python-version). Удалите .venv и запустите скрипт снова."
}

& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-dev.txt
& $py -m playwright install chromium

& $py -m src.envcheck
& $py -m pytest -q
