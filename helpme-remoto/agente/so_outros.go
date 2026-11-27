//go:build !windows

// Implementação só para testes fora do Windows (Linux/macOS).
package main

import (
	"bytes"
	"errors"
	"log"
	"os"
	"os/exec"
	"runtime"
	"time"
)

func usuarioLogado() string { return os.Getenv("FN_TESTE_USUARIO") }
func usuarioAtual() string  { return os.Getenv("FN_TESTE_USUARIO") }

func perguntar(titulo, texto string) (bool, string) {
	log.Printf("[teste] PERGUNTA %s:\n%s", titulo, texto)
	return os.Getenv("FN_TESTE_AUTORIZA") == "1", usuarioLogado()
}

func notificar(titulo, texto string) { log.Printf("[teste] AVISO %s: %s", titulo, texto) }

func caixaMensagem(titulo, texto string) { log.Printf("[teste] JANELA %s: %s", titulo, texto) }

func caixaPergunta(titulo, texto string) bool {
	log.Printf("[teste] JANELA %s: %s", titulo, texto)
	return true
}

func rodarScript(conteudo string, limite time.Duration, env []string) (int, string) {
	ctx, cancel := contexto(limite)
	defer cancel()
	cmd := exec.CommandContext(ctx, "sh", "-c", conteudo)
	cmd.Env = append(os.Environ(), env...)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err := cmd.Run()
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), out.String()
	}
	if err != nil {
		return -1, out.String() + err.Error()
	}
	return 0, out.String()
}

func coletarInfo() map[string]any {
	return map[string]any{"agente": versao, "windows": runtime.GOOS + " (teste)"}
}
