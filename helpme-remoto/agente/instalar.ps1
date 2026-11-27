# Help me! - instalação no computador do cliente (executado pelo próprio HelpMe.exe, como Administrador)
param([string]$Exe, [string]$Origem = "", [string]$Nome = "Help me!")
$ErrorActionPreference = "Stop"
Write-Host "==== $(Get-Date -Format s) instalação"
$dest    = Join-Path $env:ProgramFiles "HelpMe"
$dados   = Join-Path $env:ProgramData "HelpMe"
$publico = Join-Path $dados "publico"
$pedidos = Join-Path $dados "pedidos"
$alvo    = Join-Path $dest "HelpMe.exe"
New-Item -ItemType Directory -Force -Path $dest, $dados, $publico, $pedidos | Out-Null

# Dados (token, logs, scripts temporários): somente SYSTEM e Administradores
icacls $dados /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
# Número de acesso e estado da janela: leitura para todos os usuários
icacls $publico /grant "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
# Pedidos da janela (pedir ajuda, chat, encerrar acesso, cancelar acesso contínuo): usuários podem gravar
icacls $pedidos /grant "*S-1-5-32-545:(OI)(CI)M" | Out-Null

# Para a versão anterior (se houver) e copia o executável
$tarefa = "Help me Agente"
Get-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-Process -Name HelpMe, helpme-agente -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $alvo -or $_.Name -eq "helpme-agente" } | Stop-Process -Force
Start-Sleep -Milliseconds 500
if ($Exe -ne $alvo) { Copy-Item -Force $Exe $alvo }

# Identidade: mantém o número do modo sem instalar; senão registra agora
if (-not (Test-Path (Join-Path $dados "config.json"))) {
  if ($Origem -and (Test-Path (Join-Path $Origem "config.json"))) {
    $c = Get-Content -Raw (Join-Path $Origem "config.json") | ConvertFrom-Json
    $c.portatil = $false
    $c | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $dados "config.json")
    Write-Host "Número de acesso mantido: $($c.numero_acesso)"
  } else {
    $p = Start-Process -FilePath $alvo -ArgumentList "registrar","-silencioso" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Falha no registro (verifique a internet)." }
  }
}
$c = Get-Content -Raw (Join-Path $dados "config.json") | ConvertFrom-Json
$n = "$($c.numero_acesso)"; if ($n.Length -eq 9) { $n = $n.Substring(0,3) + " " + $n.Substring(3,3) + " " + $n.Substring(6) }
Set-Content -Encoding ASCII -Path (Join-Path $publico "numero.txt") -Value $n

# Ctrl+Alt+Del pelo técnico (SendSAS a partir de serviços)
New-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name SoftwareSASGeneration `
  -PropertyType DWord -Value 1 -Force | Out-Null

# Serviço: tarefa agendada SYSTEM ao ligar o computador, reinicia sozinha
$acao      = New-ScheduledTaskAction -Execute $alvo -Argument "executar"
$gatilho   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$config    = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
               -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $tarefa -Action $acao -Trigger $gatilho -Principal $principal `
  -Settings $config -Description "Suporte remoto $Nome" -Force | Out-Null
Start-ScheduledTask -TaskName $tarefa

# Atalhos: área de trabalho pública e menu Iniciar
$ws = New-Object -ComObject WScript.Shell
$menu = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\$Nome"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\Help me!")
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$atalhos = @(
  @((Join-Path $env:PUBLIC "Desktop\$Nome.lnk"), ""),
  @((Join-Path $menu "$Nome.lnk"), ""),
  @((Join-Path $menu "Cancelar acesso contínuo.lnk"), "revogar"),
  @((Join-Path $menu "Desinstalar $Nome.lnk"), "desinstalar")
)
foreach ($a in $atalhos) {
  $lnk = $ws.CreateShortcut($a[0]); $lnk.TargetPath = $alvo; $lnk.Arguments = $a[1]
  $lnk.WorkingDirectory = $dest; $lnk.IconLocation = "$alvo,0"; $lnk.Save()
}

# Aplicativos instalados (Configurações › Aplicativos)
$u = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\HelpMe"
New-Item -Force -Path $u | Out-Null
$ver = (Get-Item $alvo).VersionInfo.ProductVersion; if (-not $ver) { $ver = "1.0" }
foreach ($kv in @(@("DisplayName", $Nome), @("DisplayIcon", "$alvo,0"), @("Publisher", "FN Info House"),
                  @("UninstallString", "`"$alvo`" desinstalar"), @("InstallLocation", $dest), @("DisplayVersion", $ver))) {
  New-ItemProperty -Path $u -Name $kv[0] -Value $kv[1] -PropertyType String -Force | Out-Null
}
New-ItemProperty -Path $u -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $u -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
Write-Host "Instalado. Número de acesso: $n"
exit 0
