"""Plugins que já vêm no Help me! (PowerShell 5.1, Windows 10/11).

Novos plugins prontos são adicionados a bancos existentes pelo nome, sem mexer nos
que o administrador já editou.
"""

# ---------------------------------------------------------------- diagnóstico
DIAG = {
    "nome": "Diagnóstico inicial", "categoria": "Diagnóstico", "automatico": True,
    "descricao": "Roda sozinho ao liberar o atendimento: sistema, disco, rede e internet.",
    "passos": [
        {"nome": "Sistema e memória", "em_falha": "continuar", "script": r"""
$o=Get-CimInstance Win32_OperatingSystem
"Windows: $($o.Caption) $($o.Version)"
"Memória livre: $([math]::Round($o.FreePhysicalMemory/1MB,1)) GB de $([math]::Round($o.TotalVisibleMemorySize/1MB,1)) GB"
"Ligado desde: $($o.LastBootUpTime)"
"""},
        {"nome": "Espaço em disco (mínimo 5 GB no C:)", "em_falha": "continuar", "script": r"""
$c=Get-PSDrive C; $l=[math]::Round($c.Free/1GB,1); "C: livre $l GB"
if ($l -lt 5) { 'POUCO ESPAÇO NO DISCO C:'; exit 2 }
"""},
        {"nome": "Gateway da rede", "em_falha": "continuar", "script": r"""
$g=(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop
"Gateway: $g"
if (Test-Connection $g -Count 2 -Quiet) { 'OK' } else { 'GATEWAY SEM RESPOSTA'; exit 1 }
"""},
        {"nome": "Internet e DNS", "em_falha": "continuar", "script": r"""
Resolve-DnsName www.google.com -ErrorAction Stop | Select-Object -First 1 | Out-Null; 'DNS OK'
if (Test-NetConnection 8.8.8.8 -Port 443 -InformationLevel Quiet) { 'Internet OK' } else { 'SEM INTERNET'; exit 1 }
"""},
        {"nome": "Impressoras e portas seriais", "em_falha": "continuar", "script": r"""
'Impressoras:'; Get-Printer | Sort-Object Name | ForEach-Object { "  $($_.Name)  [$($_.PortName)]  $($_.PrinterStatus)" }
"Portas COM: $(([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique) -join ', ')"
"""},
    ]}

# ---------------------------------------------------------------- balança
BALANCA = {
    "nome": "Detectar configuração da balança", "categoria": "Automação comercial",
    "descricao": "Testa todas as portas COM, velocidades e formatos e informa a configuração que respondeu.",
    "passos": [
        {"nome": "Varrer portas, velocidades e formatos", "timeout_s": 600,
         "verificar": {"tipo": "contem", "valor": "CONFIGURACAO ENCONTRADA"}, "script": r"""
$portas = [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique
if (-not $portas) { 'Nenhuma porta serial (COM) neste computador. Confira cabo/adaptador USB-serial e o driver.'; exit 1 }
"Portas encontradas: $($portas -join ', ')"
$velocidades = 9600, 4800, 2400, 19200, 38400, 115200
$formatos = @(
  @{ n='8N1'; d=8; p=[System.IO.Ports.Parity]::None },
  @{ n='7E1'; d=7; p=[System.IO.Ports.Parity]::Even },
  @{ n='7O1'; d=7; p=[System.IO.Ports.Parity]::Odd },
  @{ n='8E1'; d=8; p=[System.IO.Ports.Parity]::Even })
$achados = @()
:porta foreach ($porta in $portas) {
  foreach ($v in $velocidades) {
    foreach ($f in $formatos) {
      $s = New-Object System.IO.Ports.SerialPort $porta, $v, $f.p, $f.d, ([System.IO.Ports.StopBits]::One)
      $s.ReadTimeout = 300; $s.WriteTimeout = 300; $s.DtrEnable = $true; $s.RtsEnable = $true
      try { $s.Open() } catch {
        "${porta}: não abriu – em uso por outro programa (feche o PDV/Uniplus) ou sem permissão"; continue porta }
      $resp = ''; $modo = 'contínuo'; $sw = [Diagnostics.Stopwatch]::StartNew()
      try {
        Start-Sleep -Milliseconds 250
        $resp = $s.ReadExisting()
        if (-not $resp) { $s.DiscardInBuffer(); $sw.Restart(); $s.Write([byte[]](5), 0, 1); Start-Sleep -Milliseconds 400
                          $resp = $s.ReadExisting(); $modo = 'ENQ (05h)' }
      } catch { $resp = '' } finally { $sw.Stop(); $s.Close(); $s.Dispose() }
      if ($resp -and $resp -match '\d') {
        $legiveis = ($resp.ToCharArray() | Where-Object { [int]$_ -ge 32 -and [int]$_ -le 126 -or [int]$_ -in 2,3,10,13 }).Count
        $taxa = [math]::Round($legiveis / $resp.Length, 2)
        if ($taxa -ge 0.8) {
          $achados += [pscustomobject]@{ Porta=$porta; Velocidade=$v; Formato=$f.n; Protocolo=$modo; Legivel=$taxa
                                         Tempo_ms=$sw.ElapsedMilliseconds; Resposta=(($resp -replace '[^\x20-\x7E]', '.').Trim()) }
        }
      }
    }
  }
}
if (-not $achados) { 'Nenhuma combinação respondeu. Verifique: balança ligada, cabo, adaptador, e se a balança está em modo de comunicação com PC.'; exit 1 }
$melhor = $achados | Sort-Object @{e='Legivel';Descending=$true}, @{e={ if ($_.Protocolo -like 'ENQ*') {0} else {1} }}, Tempo_ms | Select-Object -First 1
$fmt = @{ '8N1'='8 bits, sem paridade, 1 stop'; '7E1'='7 bits, paridade par, 1 stop'; '7O1'='7 bits, paridade ímpar, 1 stop'; '8E1'='8 bits, paridade par, 1 stop' }
"CONFIGURACAO ENCONTRADA: $($melhor.Porta) | $($melhor.Velocidade) bps | $($melhor.Formato) ($($fmt[$melhor.Formato])) | protocolo $($melhor.Protocolo) | resposta em $($melhor.Tempo_ms) ms"
"Leitura recebida: $($melhor.Resposta)"
''
'Todas as combinações que responderam:'
$achados | Format-Table Porta, Velocidade, Formato, Protocolo, Tempo_ms, Resposta -AutoSize | Out-String -Width 200
"""},
    ]}

# ---------------------------------------------------------------- impressoras
IMP_LISTAR = {
    "nome": "Impressoras: listar, status e fila", "categoria": "Impressoras",
    "descricao": "Mostra impressoras, drivers, portas, situação e documentos presos na fila.",
    "passos": [{"nome": "Listar", "script": r"""
"Spooler: $((Get-Service Spooler).Status)"
Get-Printer | Sort-Object Name | Format-Table Name, DriverName, PortName, PrinterStatus, Shared -AutoSize | Out-String -Width 220
'Documentos na fila:'
$jobs = Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue }
if ($jobs) { $jobs | Format-Table PrinterName, DocumentName, JobStatus, SubmittedTime -AutoSize | Out-String -Width 220 } else { '  nenhum' }
"""}]}

IMP_TESTE_WIN = {
    "nome": "Impressão de teste (página do Windows)", "categoria": "Impressoras",
    "descricao": "Envia a página de teste padrão do Windows para a impressora escolhida.",
    "parametros": [{"nome": "impressora", "rotulo": "Impressora", "tipo": "impressora"}],
    "passos": [{"nome": "Imprimir página de teste", "verificar": {"tipo": "regex", "valor": r"ReturnValue\s*:\s*0"},
                "script": r"""
$p = Get-CimInstance Win32_Printer | Where-Object { $_.Name -eq '{{impressora}}' }
if (-not $p) { 'Impressora não encontrada: {{impressora}}'; exit 1 }
Invoke-CimMethod -InputObject $p -MethodName PrintTestPage | Format-List ReturnValue
"""}]}

IMP_TESTE_TERMICA = {
    "nome": "Teste de impressora térmica (cupom ESC/POS)", "categoria": "Impressoras",
    "descricao": "Envia um cupom de teste direto (RAW), com acentos, linha e corte de papel.",
    "parametros": [{"nome": "impressora", "rotulo": "Impressora de cupom", "tipo": "impressora"}],
    "passos": [{"nome": "Enviar cupom de teste", "verificar": {"tipo": "contem", "valor": "ENVIADO"}, "script": r"""
$cs = @'
using System; using System.Runtime.InteropServices;
public class HmRaw {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public class DOCINFO { public string pDocName; public string pOutputFile; public string pDataType; }
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool OpenPrinter(string n, out IntPtr h, IntPtr d);
  [DllImport("winspool.drv", SetLastError=true)] static extern bool ClosePrinter(IntPtr h);
  [DllImport("winspool.drv", CharSet=CharSet.Unicode, SetLastError=true)] static extern int StartDocPrinter(IntPtr h, int l, [In, MarshalAs(UnmanagedType.LPStruct)] DOCINFO di);
  [DllImport("winspool.drv", SetLastError=true)] static extern bool EndDocPrinter(IntPtr h);
  [DllImport("winspool.drv", SetLastError=true)] static extern bool StartPagePrinter(IntPtr h);
  [DllImport("winspool.drv", SetLastError=true)] static extern bool EndPagePrinter(IntPtr h);
  [DllImport("winspool.drv", SetLastError=true)] static extern bool WritePrinter(IntPtr h, byte[] b, int c, out int w);
  public static string Enviar(string impressora, byte[] dados) {
    IntPtr h; if (!OpenPrinter(impressora, out h, IntPtr.Zero)) return "Erro ao abrir a impressora: " + Marshal.GetLastWin32Error();
    try {
      DOCINFO di = new DOCINFO(); di.pDocName = "Help me! - teste"; di.pDataType = "RAW";
      if (StartDocPrinter(h, 1, di) == 0) return "Erro StartDoc: " + Marshal.GetLastWin32Error();
      StartPagePrinter(h); int w; bool ok = WritePrinter(h, dados, dados.Length, out w); EndPagePrinter(h); EndDocPrinter(h);
      return ok ? "ENVIADO " + w + " bytes" : "Erro WritePrinter: " + Marshal.GetLastWin32Error();
    } finally { ClosePrinter(h); }
  }
}
'@
Add-Type -TypeDefinition $cs
$E = [char]27; $G = [char]29; $L = "`n"; $linha = '-' * 32
$t  = "$E@" + "${E}t" + [char]2 + "${E}a" + [char]1 + "${E}E" + [char]1 + "HELP ME! - TESTE" + "${E}E" + [char]0 + $L
$t += "FN Info House" + $L + "${E}a" + [char]0 + $linha + $L
$t += 'Impressora: {{impressora}}' + $L + "Data: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')" + $L
$t += "Acentos: ação pão café órgão ÇÃÉ" + $L + "Números: 0123456789" + $L + $linha + $L + $L + $L + $L + "${G}V" + [char]1
$r = [HmRaw]::Enviar('{{impressora}}', [Text.Encoding]::GetEncoding(850).GetBytes($t))
$r; 'Se os acentos saírem errados, ajuste a página de código (CP850/CP860) na impressora ou no driver.'
if ($r -notlike 'ENVIADO*') { exit 1 }
"""}]}

IMP_LIMPAR = {
    "nome": "Destravar fila de impressão", "categoria": "Impressoras",
    "descricao": "Para o spooler, apaga os trabalhos presos e inicia de novo.",
    "passos": [{"nome": "Limpar spooler", "verificar": {"tipo": "contem", "valor": "Running"}, "script": r"""
Stop-Service Spooler -Force; Start-Sleep -Seconds 2
$n = (Get-ChildItem "$env:SystemRoot\System32\spool\PRINTERS" -File -ErrorAction SilentlyContinue).Count
Remove-Item "$env:SystemRoot\System32\spool\PRINTERS\*" -Force -ErrorAction SilentlyContinue
Start-Service Spooler; "Arquivos removidos: $n"; "Spooler: $((Get-Service Spooler).Status)"
"""}]}

IMP_INSTALAR = {
    "nome": "Instalar impressora (driver da biblioteca)", "categoria": "Impressoras",
    "descricao": "Instala o driver da biblioteca (.zip com .inf, .exe ou .msi), cria a impressora e imprime teste.",
    "parametros": [{"nome": "pacote", "rotulo": "Driver (biblioteca)", "tipo": "arquivo"},
                   {"nome": "driver", "rotulo": "Nome exato do driver (como aparece no Windows)", "tipo": "impressora"},
                   {"nome": "impressora", "rotulo": "Nome da nova impressora", "tipo": "impressora"},
                   {"nome": "porta", "rotulo": "Porta (USB001, COM1, LPT1 ou IP)", "tipo": "texto", "padrao": "USB001"}],
    "passos": [
        {"nome": "Instalar driver", "timeout_s": 900, "script": r"""
$a = Join-Path $env:HM_ARQUIVOS '{{pacote}}'; $ext = [IO.Path]::GetExtension($a).ToLower()
if ($ext -eq '.zip') {
  $d = Join-Path $env:HM_ARQUIVOS 'driver'; Expand-Archive $a $d -Force
  pnputil /add-driver "$d\*.inf" /subdirs /install; $global:LASTEXITCODE = 0
  Add-PrinterDriver -Name '{{driver}}' -ErrorAction SilentlyContinue
} elseif ($ext -eq '.msi') { $p = Start-Process msiexec.exe -ArgumentList "/i `"$a`" /qn /norestart" -Wait -PassThru; "msiexec: $($p.ExitCode)" }
else { $p = Start-Process $a -ArgumentList '/S' -Wait -PassThru; "Instalador: $($p.ExitCode)" }
if (Get-PrinterDriver -Name '{{driver}}' -ErrorAction SilentlyContinue) { 'DRIVER OK: {{driver}}' }
else { 'Driver "{{driver}}" não apareceu. Drivers instalados:'; Get-PrinterDriver | Select-Object -ExpandProperty Name; exit 1 }
"""},
        {"nome": "Criar impressora", "script": r"""
$porta = '{{porta}}'
if ($porta -match '^\d+\.\d+\.\d+\.\d+$') {
  if (-not (Get-PrinterPort -Name "IP_$porta" -ErrorAction SilentlyContinue)) { Add-PrinterPort -Name "IP_$porta" -PrinterHostAddress $porta }
  $porta = "IP_$porta" }
if (-not (Get-Printer -Name '{{impressora}}' -ErrorAction SilentlyContinue)) { Add-Printer -Name '{{impressora}}' -DriverName '{{driver}}' -PortName $porta }
Get-Printer -Name '{{impressora}}' | Format-List Name, DriverName, PortName, PrinterStatus
"""},
        {"nome": "Página de teste", "verificar": {"tipo": "regex", "valor": r"ReturnValue\s*:\s*0"}, "script": r"""
Invoke-CimMethod -InputObject (Get-CimInstance Win32_Printer | Where-Object { $_.Name -eq '{{impressora}}' }) -MethodName PrintTestPage | Format-List ReturnValue
"""},
    ]}

# ---------------------------------------------------------------- instalação de programas
_IDENTIFICAR = r"""
$a = Join-Path $env:HM_ARQUIVOS '{{arquivo}}'; $ext = [IO.Path]::GetExtension($a).ToLower(); $tipo = 'desconhecido'
if ($ext -eq '.msi') { $tipo = 'MSI' } elseif ($ext -eq '.zip') { $tipo = 'ZIP' } else {
  $fs = [IO.File]::OpenRead($a); $buf = New-Object byte[] ([Math]::Min($fs.Length, 16MB)); [void]$fs.Read($buf, 0, $buf.Length); $fs.Close()
  $t = [Text.Encoding]::ASCII.GetString($buf)
  if ($t -match 'Inno Setup') { $tipo = 'Inno Setup' } elseif ($t -match 'Nullsoft') { $tipo = 'NSIS' }
  elseif ($t -match 'InstallShield') { $tipo = 'InstallShield' } elseif ($t -match 'WixBundle|Windows Installer XML') { $tipo = 'WiX/Burn' }
  elseif ($t -match 'Advanced Installer') { $tipo = 'Advanced Installer' } elseif ($t -match 'install4j') { $tipo = 'install4j' }
  elseif ($t -match 'Setup Factory') { $tipo = 'Setup Factory' } }
Set-Content (Join-Path $env:HM_ARQUIVOS '_tipo.txt') $tipo
$sil = @{ 'MSI'='/qn'; 'Inno Setup'='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'; 'NSIS'='/S'; 'InstallShield'='/s /v"/qn"';
          'WiX/Burn'='/quiet /norestart'; 'Advanced Installer'='/exenoui /qn'; 'install4j'='-q'; 'ZIP'='(extrair)' }
"TIPO DO INSTALADOR: $tipo"
if ($sil.ContainsKey($tipo)) { "Modo silencioso provável: $($sil[$tipo])" } else { 'Sem modo silencioso conhecido: use o modo assistido.' }
"""

_INSTALAR = r"""
$a = Join-Path $env:HM_ARQUIVOS '{{arquivo}}'; $tipo = (Get-Content (Join-Path $env:HM_ARQUIVOS '_tipo.txt')).Trim()
$modo = '{{modo}}'; $argumentos = '{{argumentos}}'
$sil = @{ 'Inno Setup'='/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'; 'NSIS'='/S'; 'InstallShield'='/s /v"/qn"';
          'WiX/Burn'='/quiet /norestart'; 'Advanced Installer'='/exenoui /qn'; 'install4j'='-q' }
if ($modo -eq 'auto') { if ($argumentos -ne 'auto' -or $tipo -in 'MSI','ZIP' -or $sil.ContainsKey($tipo)) { $modo = 'silencioso' } else { $modo = 'assistido' } }
if ($argumentos -eq 'auto') { $argumentos = $sil[$tipo] }
"Tipo: $tipo | modo: $modo | argumentos: $argumentos"
if ($tipo -eq 'ZIP') {
  $dest = Join-Path 'C:\HelpMe\Apps' ([IO.Path]::GetFileNameWithoutExtension($a)); Expand-Archive $a $dest -Force; "Extraído em $dest"
  if (Get-ChildItem $dest -Recurse -Filter *.inf) { pnputil /add-driver "$dest\*.inf" /subdirs /install }
  exit 0 }
if ($modo -eq 'silencioso' -and $tipo -ne 'MSI' -and -not $argumentos) {
  'Não conheço os argumentos silenciosos deste instalador. Informe-os ou use o modo assistido.'; exit 1 }
if ($modo -eq 'silencioso') {
  if ($tipo -eq 'MSI') { $p = Start-Process msiexec.exe -ArgumentList "/i `"$a`" /qn /norestart" -Wait -PassThru }
  else { $p = Start-Process $a -ArgumentList $argumentos -Wait -PassThru }
  "Código de saída: $($p.ExitCode)"; if ($p.ExitCode -in 0, 3010, 1641) { exit 0 } else { exit $p.ExitCode } }
# Modo assistido: abre o instalador na tela do cliente; o técnico conclui pelo acesso remoto.
$u = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $u) { 'Nenhum usuário conectado: a instalação assistida precisa de alguém logado no Windows.'; exit 1 }
$pub = Join-Path $env:PUBLIC 'Downloads\HelpMe'; New-Item -ItemType Directory -Force $pub | Out-Null
$copia = Join-Path $pub ([IO.Path]::GetFileName($a)); Copy-Item $a $copia -Force
$nome = 'HelpMe_InstalacaoAssistida'
if ($tipo -eq 'MSI') { $acao = New-ScheduledTaskAction -Execute 'msiexec.exe' -Argument "/i `"$copia`"" }
else { $acao = New-ScheduledTaskAction -Execute $copia -WorkingDirectory $pub }
$quem = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $nome -Action $acao -Principal $quem -Force | Out-Null
Start-ScheduledTask -TaskName $nome
"INSTALAÇÃO ASSISTIDA: instalador aberto na tela de $u. Conclua pelo acesso remoto (bloqueie o teclado/mouse do cliente se precisar)."
$limite = (Get-Date).AddMinutes(55); Start-Sleep -Seconds 8
while ((Get-ScheduledTask -TaskName $nome).State -eq 'Running' -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 5 }
$r = (Get-ScheduledTaskInfo -TaskName $nome).LastTaskResult
Unregister-ScheduledTask -TaskName $nome -Confirm:$false; Remove-Item $copia -Force -ErrorAction SilentlyContinue
"Instalador fechado (resultado $r)."
"""

_CONFERIR = r"""
if ('{{conferir}}' -eq '-') { 'Conferência não configurada.'; exit 0 }
if (Test-Path '{{conferir}}') { 'INSTALADO: {{conferir}}' } else { 'Não encontrado: {{conferir}}'; exit 1 }
"""


def _instalador(nome, desc, padrao_arquivo=None, modo="auto", conferir="-", categoria="Instalação"):
    return {
        "nome": nome, "categoria": categoria, "descricao": desc,
        "parametros": [
            {"nome": "arquivo", "rotulo": "Instalador (biblioteca)", "tipo": "arquivo", "padrao": padrao_arquivo},
            {"nome": "modo", "rotulo": "Modo: auto, silencioso ou assistido", "tipo": "texto", "padrao": modo},
            {"nome": "argumentos", "rotulo": "Argumentos (auto = detectar)", "tipo": "argumentos", "padrao": "auto"},
            {"nome": "conferir", "rotulo": "Pasta/arquivo que deve existir depois (- = não conferir)", "tipo": "caminho",
             "padrao": conferir}],
        "passos": [
            {"nome": "Identificar o instalador", "script": _IDENTIFICAR},
            {"nome": "Instalar", "timeout_s": 3600, "script": _INSTALAR},
            {"nome": "Conferir instalação", "verificar": {"tipo": "contem", "valor": "INSTALADO"}, "script": _CONFERIR},
        ]}


INSTALAR_BIBLIOTECA = _instalador(
    "Instalar programa da biblioteca",
    "Detecta o tipo do instalador: se tiver modo silencioso, instala sozinho; se não, abre na tela do cliente "
    "para o técnico concluir.")

INSTALAR_UNIPLUS = _instalador(
    "Instalar Uniplus (assistido)",
    "Abre o instalador do Uniplus da biblioteca na tela do cliente e acompanha até o técnico concluir.",
    padrao_arquivo="Uniplus-instalador.exe", modo="assistido", categoria="Uniplus")

COPIAR_DESKTOP = {
    "nome": "Enviar arquivo para a área de trabalho", "categoria": "Instalação",
    "descricao": "Copia um arquivo da biblioteca para a área de trabalho pública do cliente.",
    "parametros": [{"nome": "arquivo", "rotulo": "Arquivo (biblioteca)", "tipo": "arquivo"}],
    "passos": [{"nome": "Copiar", "verificar": {"tipo": "contem", "valor": "COPIADO"}, "script": r"""
$d = Join-Path $env:PUBLIC 'Desktop'; Copy-Item (Join-Path $env:HM_ARQUIVOS '{{arquivo}}') $d -Force
"COPIADO para $d\{{arquivo}}"
"""}]}

CERTIFICADO = {
    "nome": "Instalar certificado digital A1", "categoria": "Certificado digital", "requer_autorizacao": True,
    "descricao": "Importa o .pfx da biblioteca no repositório do computador. A senha não fica gravada.",
    "parametros": [{"nome": "arquivo", "rotulo": "Arquivo .pfx (biblioteca)", "tipo": "arquivo"},
                   {"nome": "senha", "rotulo": "Senha do certificado", "tipo": "segredo"}],
    "passos": [{"nome": "Importar certificado", "verificar": {"tipo": "contem", "valor": "Validade"}, "script": r"""
$a = Join-Path $env:HM_ARQUIVOS '{{arquivo}}'; $s = ConvertTo-SecureString '{{senha}}' -AsPlainText -Force
$c = Import-PfxCertificate -FilePath $a -Password $s -CertStoreLocation Cert:\LocalMachine\My -Exportable
"Titular: $($c.Subject)"; "Validade: $($c.NotAfter)"
"""}]}

REDE_ESCUTAR = {
    "nome": "Teste entre pontos: abrir porta (escuta)", "categoria": "Rede",
    "descricao": "Abre uma porta TCP temporária neste computador e registra quem conseguir conectar.",
    "parametros": [{"nome": "porta", "rotulo": "Porta TCP", "tipo": "numero", "padrao": "9999"},
                   {"nome": "segundos", "rotulo": "Tempo escutando (s)", "tipo": "numero", "padrao": "90"}],
    "passos": [{"nome": "Escutar e registrar conexões", "timeout_s": 900,
                "verificar": {"tipo": "contem", "valor": "CONEXAO RECEBIDA"}, "script": r"""
$porta = {{porta}}; $seg = [Math]::Min({{segundos}}, 600); $regra = "Help me! - teste TCP $porta"
New-NetFirewallRule -DisplayName $regra -Direction Inbound -Protocol TCP -LocalPort $porta -Action Allow | Out-Null
$l = New-Object System.Net.Sockets.TcpListener ([Net.IPAddress]::Any), $porta; $n = 0
try {
  $l.Start()
  $ips = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' }).IPAddress
  "ESCUTANDO na porta $porta por $seg s. IPs deste computador: $($ips -join ', ')"
  $fim = (Get-Date).AddSeconds($seg)
  while ((Get-Date) -lt $fim) {
    if ($l.Pending()) { $c = $l.AcceptTcpClient(); $n++; "CONEXAO RECEBIDA de $($c.Client.RemoteEndPoint) às $(Get-Date -Format HH:mm:ss)"; $c.Close() }
    else { Start-Sleep -Milliseconds 200 }
  }
} finally { $l.Stop(); Remove-NetFirewallRule -DisplayName $regra -ErrorAction SilentlyContinue }
"Conexões recebidas: $n"
if ($n -eq 0) { 'Ninguém conectou: verifique rede, IP e firewall/antivírus.'; exit 1 }
"""}]}

REDE_CONECTAR = {
    "nome": "Teste entre pontos: conectar ao outro computador", "categoria": "Rede",
    "descricao": "Faz ping e 5 conexões TCP até o outro ponto, com tempo de resposta.",
    "parametros": [{"nome": "alvo", "rotulo": "IP ou nome do outro computador", "tipo": "texto"},
                   {"nome": "porta", "rotulo": "Porta TCP", "tipo": "numero", "padrao": "9999"}],
    "passos": [
        {"nome": "Ping", "em_falha": "continuar", "script": r"""
$r = Test-Connection '{{alvo}}' -Count 4 -ErrorAction SilentlyContinue
if ($r) { $r | ForEach-Object { "Resposta de $($_.Address): $($_.ResponseTime) ms" } } else { 'Sem resposta ao ping (pode estar bloqueado pelo firewall)'; exit 1 }
"""},
        {"nome": "Conexão TCP", "verificar": {"tipo": "contem", "valor": "CONECTOU"}, "script": r"""
$ok = 0; $tempos = @()
foreach ($i in 1..5) {
  $c = New-Object System.Net.Sockets.TcpClient; $sw = [Diagnostics.Stopwatch]::StartNew()
  try { $t = $c.ConnectAsync('{{alvo}}', {{porta}}); if ($t.Wait(3000) -and $c.Connected) { $sw.Stop(); $ok++; $tempos += $sw.ElapsedMilliseconds } }
  catch { } finally { $c.Close() }
  Start-Sleep -Milliseconds 300
}
if ($ok) { "CONECTOU em $ok de 5 tentativas | tempo médio $([math]::Round(($tempos | Measure-Object -Average).Average)) ms | máx $(($tempos | Measure-Object -Maximum).Maximum) ms" }
else { 'NÃO CONECTOU em {{alvo}}:{{porta}} (porta fechada, firewall, serviço parado ou rede)'; exit 1 }
"""}]}

PLUGINS_PRONTOS = [DIAG, REDE_ESCUTAR, REDE_CONECTAR, BALANCA, IMP_LISTAR, IMP_TESTE_WIN, IMP_TESTE_TERMICA, IMP_LIMPAR, IMP_INSTALAR,
                   INSTALAR_BIBLIOTECA, INSTALAR_UNIPLUS, COPIAR_DESKTOP, CERTIFICADO]

for _p in PLUGINS_PRONTOS:
    for _s in _p["passos"]:
        _s["script"] = _s["script"].strip()
