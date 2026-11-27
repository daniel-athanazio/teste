//go:build windows

package main

import (
	_ "embed"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

//go:embed instalar.ps1
var scriptInstalar string

//go:embed desinstalar.ps1
var scriptDesinstalar string

func pastaInstalada() string {
	pd := os.Getenv("ProgramData")
	if pd == "" {
		pd = `C:\ProgramData`
	}
	return filepath.Join(pd, "HelpMe")
}

// instaladoNoPC: o serviço instalado já gerou o número de acesso (arquivo legível por qualquer usuário).
func instaladoNoPC() bool {
	_, err := os.Stat(filepath.Join(pastaInstalada(), "publico", "numero.txt"))
	return err == nil
}

func sessaoAtiva() uint32 { return windows.WTSGetActiveConsoleSessionId() }

// iniciarNaSessao abre este executável na sessão do usuário que está na frente do computador.
// comoUsuario=false: processo SYSTEM na sessão do usuário (captura também tela de bloqueio e UAC).
// comoUsuario=true: processo com o token do próprio usuário (janela do cliente).
func iniciarNaSessao(args []string, comoUsuario bool) (*os.Process, error) {
	sess := sessaoAtiva()
	if sess == 0xFFFFFFFF {
		return nil, errors.New("nenhuma sessão ativa")
	}
	var tok windows.Token
	if comoUsuario {
		if err := windows.WTSQueryUserToken(sess, &tok); err != nil {
			return nil, fmt.Errorf("sem usuário conectado: %w", err)
		}
	} else {
		var proprio windows.Token
		if err := windows.OpenProcessToken(windows.CurrentProcess(), windows.TOKEN_ALL_ACCESS, &proprio); err != nil {
			return nil, err
		}
		err := windows.DuplicateTokenEx(proprio, windows.MAXIMUM_ALLOWED, nil, windows.SecurityImpersonation,
			windows.TokenPrimary, &tok)
		proprio.Close()
		if err != nil {
			return nil, err
		}
		if err := windows.SetTokenInformation(tok, windows.TokenSessionId, (*byte)(unsafe.Pointer(&sess)), 4); err != nil {
			tok.Close()
			return nil, fmt.Errorf("SetTokenInformation: %w", err)
		}
	}
	defer tok.Close()
	var env *uint16
	if comoUsuario {
		if err := windows.CreateEnvironmentBlock(&env, tok, false); err == nil {
			defer windows.DestroyEnvironmentBlock(env)
		}
	}
	exe, err := os.Executable()
	if err != nil {
		return nil, err
	}
	linha, _ := windows.UTF16PtrFromString(windows.ComposeCommandLine(append([]string{exe}, args...)))
	desk, _ := windows.UTF16PtrFromString(`winsta0\default`)
	dir, _ := windows.UTF16PtrFromString(filepath.Dir(exe))
	si := windows.StartupInfo{Desktop: desk}
	si.Cb = uint32(unsafe.Sizeof(si))
	var pi windows.ProcessInformation
	if err := windows.CreateProcessAsUser(tok, nil, linha, nil, nil, false,
		windows.CREATE_UNICODE_ENVIRONMENT|windows.CREATE_NO_WINDOW, env, dir, &si, &pi); err != nil {
		return nil, fmt.Errorf("CreateProcessAsUser: %w", err)
	}
	windows.CloseHandle(pi.Thread)
	p, err := os.FindProcess(int(pi.ProcessId))
	windows.CloseHandle(pi.Process)
	return p, err
}

func iniciarProcessoTela(canal string) (processoTela, error) {
	return iniciarNaSessao([]string{"tela", "-canal", canal}, false)
}

// abrirJanelaCliente mostra a janela do Help me! ao usuário (técnico conectou, mensagem nova…).
var abrirJanelaHook func()

func abrirJanelaCliente() {
	if abrirJanelaHook != nil { // modo sem instalar: a janela é deste processo
		abrirJanelaHook()
		return
	}
	if usuarioLogado() == "" {
		return
	}
	if _, err := iniciarNaSessao([]string{"janela"}, true); err != nil {
		log.Println("abrir janela do cliente:", err)
	}
}

func elevado() bool { return windows.GetCurrentProcessToken().IsElevated() }

type shellExecuteInfo struct {
	Size     uint32
	Mask     uint32
	Hwnd     uintptr
	Verbo    *uint16
	Arquivo  *uint16
	Params   *uint16
	Dir      *uint16
	Show     int32
	InstApp  uintptr
	IDList   uintptr
	Classe   *uint16
	ChaveCls uintptr
	HotKey   uint32
	Icone    uintptr
	Processo windows.Handle
}

// comoAdministrador roda este .exe com os argumentos pedindo elevação (UAC) e espera terminar.
func comoAdministrador(args []string) error {
	exe, _ := os.Executable()
	v, _ := windows.UTF16PtrFromString("runas")
	f, _ := windows.UTF16PtrFromString(exe)
	p, _ := windows.UTF16PtrFromString(windows.ComposeCommandLine(args))
	sei := shellExecuteInfo{Verbo: v, Arquivo: f, Params: p, Show: 0, Mask: 0x40 | 0x100}
	sei.Size = uint32(unsafe.Sizeof(sei))
	r, _, err := syscall.NewLazyDLL("shell32.dll").NewProc("ShellExecuteExW").Call(uintptr(unsafe.Pointer(&sei)))
	if r == 0 {
		return fmt.Errorf("permissão de administrador negada (%v)", err)
	}
	defer windows.CloseHandle(sei.Processo)
	windows.WaitForSingleObject(sei.Processo, windows.INFINITE)
	var codigo uint32
	_ = windows.GetExitCodeProcess(sei.Processo, &codigo)
	if codigo != 0 {
		return fmt.Errorf("a instalação terminou com o código %d (veja %s)", codigo,
			filepath.Join(pastaInstalada(), "instalacao.log"))
	}
	return nil
}

func rodarPowerShell(script string, args ...string) error {
	tmp := filepath.Join(os.TempDir(), fmt.Sprintf("helpme_%d.ps1", os.Getpid()))
	if err := os.WriteFile(tmp, []byte("\xef\xbb\xbf"+strings.ReplaceAll(script, "\n", "\r\n")), 0o600); err != nil {
		return err
	}
	defer os.Remove(tmp)
	_ = os.MkdirAll(pastaInstalada(), 0o755)
	logf, _ := os.OpenFile(filepath.Join(pastaInstalada(), "instalacao.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	cmd := exec.Command("powershell.exe", append([]string{"-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp}, args...)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	if logf != nil {
		defer logf.Close()
		cmd.Stdout, cmd.Stderr = logf, logf
	}
	return cmd.Run()
}

// instalar: copia para Arquivos de Programas, cria o serviço (tarefa SYSTEM), atalhos e a entrada em
// "Aplicativos instalados". Se veio do modo sem instalar, mantém o mesmo número de acesso.
func instalar(origem string) error {
	if !elevado() {
		args := []string{"instalar"}
		if origem == "" && modoPortatil {
			origem = pastaDados()
		}
		if origem == "" {
			if la := os.Getenv("LOCALAPPDATA"); la != "" {
				origem = filepath.Join(la, "HelpMe", "portatil")
			}
		}
		if origem != "" {
			args = append(args, "-de", origem)
		}
		return comoAdministrador(args)
	}
	exe, _ := os.Executable()
	return rodarPowerShell(scriptInstalar, "-Exe", exe, "-Origem", origem, "-Nome", tituloApp)
}

func desinstalar() error {
	if !elevado() {
		return comoAdministrador([]string{"desinstalar"})
	}
	if caixaPergunta(tituloApp, "Remover o "+tituloApp+" deste computador?\n\nO suporte remoto deixará de funcionar.") {
		_ = carregarConfig()
		if cfg.Token != "" {
			_ = requisicao("POST", "/agente/desinstalado", map[string]string{"usuario": usuarioAtual()}, nil)
		}
		return rodarPowerShell(scriptDesinstalar)
	}
	return nil
}

// abrirApp abre a janela do cliente como aplicativo (Edge sem barra de endereço); sem Edge, no navegador padrão.
func abrirApp(url, perfil string) {
	for _, base := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles"), os.Getenv("LOCALAPPDATA")} {
		edge := filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe")
		if _, err := os.Stat(edge); err == nil && base != "" {
			cmd := exec.Command(edge, "--app="+url, "--window-size=460,720", "--user-data-dir="+perfil,
				"--no-first-run", "--no-default-browser-check", "--disable-features=Translate,msEdgeSidebarV2")
			if cmd.Start() == nil {
				return
			}
		}
	}
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
}
