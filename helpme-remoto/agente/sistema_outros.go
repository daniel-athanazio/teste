//go:build !windows

// Só para testes fora do Windows.
package main

import (
	"errors"
	"log"
	"os"
	"os/exec"
)

func prepararProcesso() {}

func instaladoNoPC() bool { return os.Getenv("FN_TESTE_INSTALADO") == "1" }

func iniciarProcessoTela(canal string) (processoTela, error) {
	cmd := exec.Command(os.Args[0], "tela", "-canal", canal)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd.Process, nil
}

var abrirJanelaHook func()

func abrirJanelaCliente() {
	if abrirJanelaHook != nil {
		abrirJanelaHook()
		return
	}
	log.Print("[teste] abrir janela do cliente")
}

func instalar(origem string) error { return errors.New("instalação só no Windows") }
func desinstalar() error           { return errors.New("só no Windows") }

func abrirApp(url, perfil string) { log.Printf("[teste] janela do cliente em %s", url) }

func pastaInstalada() string { return pastaDados() }
