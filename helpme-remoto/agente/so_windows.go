//go:build windows

package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"

	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

var (
	wtsapi32                   = syscall.NewLazyDLL("wtsapi32.dll")
	kernel32                   = syscall.NewLazyDLL("kernel32.dll")
	procWTSSendMessageW        = wtsapi32.NewProc("WTSSendMessageW")
	procWTSQuerySessionInfoW   = wtsapi32.NewProc("WTSQuerySessionInformationW")
	procWTSFreeMemory          = wtsapi32.NewProc("WTSFreeMemory")
	procGetActiveConsoleSessID = kernel32.NewProc("WTSGetActiveConsoleSessionId")
	procMessageBoxW            = syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
)

const (
	semSessao      = 0xFFFFFFFF
	wtsUserName    = 5
	idYes          = 6
	createNoWindow = 0x08000000
	estiloPergunta = 0x4 | 0x20 | 0x100 | 0x1000 | 0x10000 // YESNO|ICONQUESTION|DEFBUTTON2|SYSTEMMODAL|SETFOREGROUND
	estiloAviso    = 0x40 | 0x1000 | 0x10000               // OK|ICONINFORMATION|SYSTEMMODAL|SETFOREGROUND
)

func sessaoConsole() uint32 {
	r, _, _ := procGetActiveConsoleSessID.Call()
	return uint32(r)
}

func usuarioLogado() string {
	s := sessaoConsole()
	if s == semSessao {
		return ""
	}
	var buf *uint16
	var n uint32
	r, _, _ := procWTSQuerySessionInfoW.Call(0, uintptr(s), wtsUserName,
		uintptr(unsafe.Pointer(&buf)), uintptr(unsafe.Pointer(&n)))
	if r == 0 || buf == nil {
		return ""
	}
	defer procWTSFreeMemory.Call(uintptr(unsafe.Pointer(buf)))
	return syscall.UTF16ToString(unsafe.Slice(buf, n/2))
}

func mensagemSessao(titulo, texto string, estilo uintptr, timeout uintptr, esperar bool) (uint32, bool) {
	s := sessaoConsole()
	if s == semSessao {
		return 0, false
	}
	t, _ := syscall.UTF16FromString(titulo)
	m, _ := syscall.UTF16FromString(texto)
	var resp uint32
	w := uintptr(0)
	if esperar {
		w = 1
	}
	r, _, _ := procWTSSendMessageW.Call(0, uintptr(s),
		uintptr(unsafe.Pointer(&t[0])), uintptr((len(t)-1)*2),
		uintptr(unsafe.Pointer(&m[0])), uintptr((len(m)-1)*2),
		estilo, timeout, uintptr(unsafe.Pointer(&resp)), w)
	return resp, r != 0
}

// perguntar mostra a janela Sim/Não na sessão do usuário logado.
// Sem usuário na tela ou sem resposta em 2 minutos = não.
func perguntar(titulo, texto string) (bool, string) {
	usuario := usuarioLogado()
	if usuario == "" {
		return false, "(nenhum usuário conectado)"
	}
	resp, ok := mensagemSessao(titulo, texto, estiloPergunta, tempoAutorizar, true)
	if !ok { // modo sem instalar: janela do próprio processo, com o mesmo limite de 2 minutos
		r := make(chan bool, 1)
		go func() { r <- caixaPergunta(titulo, texto) }()
		select {
		case v := <-r:
			return v, usuario
		case <-time.After(tempoAutorizar * time.Second):
			return false, usuario + " (sem resposta)"
		}
	}
	if resp == 32000 {
		return false, usuario + " (sem resposta)"
	}
	return resp == idYes, usuario
}

// notificar mostra um aviso na tela do usuário sem bloquear o agente.
func notificar(titulo, texto string) {
	if usuarioLogado() == "" {
		return
	}
	if _, ok := mensagemSessao(titulo, texto, estiloAviso, 300, false); !ok {
		go caixaMensagem(titulo, texto)
	}
}

// Janelas do próprio processo (comandos executados pelo usuário: numero, revogar, registrar).
func caixaMensagem(titulo, texto string) {
	t, _ := syscall.UTF16PtrFromString(titulo)
	m, _ := syscall.UTF16PtrFromString(texto)
	procMessageBoxW.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), 0x40|0x10000)
}

func caixaPergunta(titulo, texto string) bool {
	t, _ := syscall.UTF16PtrFromString(titulo)
	m, _ := syscall.UTF16PtrFromString(texto)
	r, _, _ := procMessageBoxW.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), 0x4|0x20|0x100|0x1000|0x10000)
	return r == idYes
}

func usuarioAtual() string {
	return os.Getenv("USERDOMAIN") + "\\" + os.Getenv("USERNAME")
}

func nomeTemp() string {
	b := make([]byte, 12)
	_, _ = rand.Read(b)
	return "s_" + hex.EncodeToString(b) + ".ps1"
}

func rodarScript(conteudo string, limite time.Duration, env []string) (int, string) {
	dir := filepath.Join(pastaDados(), "tmp")
	_ = os.MkdirAll(dir, 0o700)
	arq := filepath.Join(dir, nomeTemp())
	f, err := os.OpenFile(arq, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return -1, "erro ao preparar script: " + err.Error()
	}
	script := "\xef\xbb\xbf$ProgressPreference='SilentlyContinue'\r\n" +
		"[Console]::OutputEncoding=[Text.Encoding]::UTF8\r\n" +
		"$ErrorActionPreference='Stop'\r\n" +
		strings.ReplaceAll(conteudo, "\n", "\r\n") + "\r\n" +
		"if ($LASTEXITCODE) { exit $LASTEXITCODE }\r\n"
	_, _ = f.WriteString(script)
	f.Close()
	defer os.Remove(arq)

	ctx, cancel := contexto(limite)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive",
		"-ExecutionPolicy", "Bypass", "-File", arq)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	cmd.Env = append(os.Environ(), env...)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err = cmd.Run()
	if ctx.Err() != nil {
		return -1, out.String() + "\n[tempo limite atingido]"
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), out.String()
	}
	if err != nil {
		return -1, out.String() + "\n" + err.Error()
	}
	return 0, out.String()
}

const psInfo = `$o=Get-CimInstance Win32_OperatingSystem;$c=Get-CimInstance Win32_Processor|Select -First 1;
$s=Get-CimInstance Win32_ComputerSystem;$b=Get-CimInstance Win32_BIOS;
$d=Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"|%{[pscustomobject]@{unidade=$_.DeviceID;total_gb=[math]::Round($_.Size/1GB,1);livre_gb=[math]::Round($_.FreeSpace/1GB,1)}};
$ip=(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue|?{$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*'}).IPAddress;
[pscustomobject]@{windows="$($o.Caption) $($o.Version)";processador=$c.Name.Trim();nucleos=$c.NumberOfCores;
memoria_gb=[math]::Round($s.TotalPhysicalMemory/1GB,1);fabricante=$s.Manufacturer;modelo=$s.Model;serie=$b.SerialNumber;
discos=@($d);ips=@($ip);ligado_desde=$o.LastBootUpTime.ToString('s');
impressoras=@(Get-Printer -ErrorAction SilentlyContinue|%{$_.Name});portas_com=@([System.IO.Ports.SerialPort]::GetPortNames()|Sort -Unique)}|ConvertTo-Json -Compress -Depth 3`

func coletarInfo() map[string]any {
	info := map[string]any{"agente": versao}
	ctx, cancel := contexto(60 * time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", psInfo)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	out, err := cmd.Output()
	if err == nil {
		_ = json.Unmarshal(bytes.TrimPrefix(out, []byte("\xef\xbb\xbf")), &info)
		info["agente"] = versao
	}
	return info
}
