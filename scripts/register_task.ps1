$ErrorActionPreference = "Stop"

# 경로에 백슬래시를 쓰지 않는다. PowerShell 은 Windows 에서도 슬래시를 받아주고,
# 백슬래시는 생성 과정에서 이스케이프가 깨져 CR 바이트로 둔갑한 적이 있다.
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root "scripts/run_daily.bat"

if (-not (Test-Path -LiteralPath $bat)) {
    throw "실행 파일을 찾을 수 없습니다: $bat"
}

$action   = New-ScheduledTaskAction -Execute $bat
$trigger  = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "부동산일일브리핑" `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "등록 완료: 부동산일일브리핑 (매일 08:00)"
Write-Host ""
Write-Host "즉시 실행해 확인:"
Write-Host '  Start-ScheduledTask -TaskName "부동산일일브리핑"'
Write-Host '  Get-ScheduledTaskInfo -TaskName "부동산일일브리핑" | Select LastRunTime, LastTaskResult'
Write-Host ""
Write-Host "해제:"
Write-Host '  Unregister-ScheduledTask -TaskName "부동산일일브리핑" -Confirm:$false'
