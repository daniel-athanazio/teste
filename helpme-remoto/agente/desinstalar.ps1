# Help me! - remoção (executado pelo próprio HelpMe.exe, como Administrador)
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "Help me Agente"
Unregister-ScheduledTask -TaskName "Help me Agente" -Confirm:$false
$dest = Join-Path $env:ProgramFiles "HelpMe"
$pai = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
Get-Process | Where-Object { $_.Path -like "$dest\*" -and $_.Id -ne $pai } | Stop-Process -Force
Get-Process -Name helpme-agente | Stop-Process -Force
Remove-Item -Recurse -Force (Join-Path $env:ProgramData "HelpMe")
Get-ChildItem (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs") -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName "Cancelar acesso contínuo.lnk") } | Remove-Item -Recurse -Force
Get-ChildItem (Join-Path $env:PUBLIC "Desktop") -Filter *.lnk | Where-Object {
  (New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName).TargetPath -like "$dest\*" } | Remove-Item -Force
Remove-Item -Recurse -Force "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\HelpMe"
# o próprio .exe está em uso: é apagado alguns segundos depois de este processo terminar
Start-Process -WindowStyle Hidden cmd.exe -ArgumentList "/c timeout /t 3 >nul & rmdir /s /q `"$dest`""
Write-Host "Removido. Dados locais apagados (LGPD)."
exit 0
