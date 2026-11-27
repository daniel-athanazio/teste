package main

// Janela do cliente: um servidorzinho local (só 127.0.0.1, com chave aleatória) aberto como aplicativo no Edge.
// Mostra o número de acesso, quem está atendendo, o aviso de tela compartilhada com o botão Encerrar,
// o chat com o técnico e o formulário "Pedir ajuda" (vira chamado no gestor de OS).

import (
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

//go:embed janela.html
var paginaJanela []byte

type controlePortatil struct {
	parar context.CancelFunc
}

type janelaApp struct {
	mu         sync.Mutex
	url        string
	perfil     string
	ultVivo    time.Time
	portatil   *controlePortatil
	instalando bool
	instalErro string
	instalado  bool
}

func pastaUsuario() string {
	if la := os.Getenv("LOCALAPPDATA"); la != "" {
		return filepath.Join(la, "HelpMe")
	}
	if d, err := os.UserCacheDir(); err == nil {
		return filepath.Join(d, "helpme")
	}
	return filepath.Join(os.TempDir(), "helpme-usuario")
}

// pastas onde a janela lê o estado e grava os pedidos
func (j *janelaApp) pastas() (string, string) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.portatil != nil && !j.instalado {
		return pastaPublica(), pastaPedidos()
	}
	return filepath.Join(pastaInstalada(), "publico"), filepath.Join(pastaInstalada(), "pedidos")
}

func rodarJanela(portatil *controlePortatil) {
	_ = os.MkdirAll(pastaUsuario(), 0o700)
	arqInst := filepath.Join(pastaUsuario(), "janela.json")
	if portatil == nil { // já existe uma janela aberta deste usuário? pede para ela aparecer e sai
		var ant struct{ URL string }
		if b, err := os.ReadFile(arqInst); err == nil && json.Unmarshal(b, &ant) == nil && ant.URL != "" {
			c := &http.Client{Timeout: 2 * time.Second}
			if r, err := c.Post(strings.Replace(ant.URL, "/?k=", "/abrir?k=", 1), "text/plain", nil); err == nil {
				r.Body.Close()
				if r.StatusCode == 200 {
					return
				}
			}
		}
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		caixaMensagem(tituloApp, "Não foi possível abrir a janela: "+err.Error())
		return
	}
	kb := make([]byte, 16)
	_, _ = rand.Read(kb)
	chave := hex.EncodeToString(kb)
	porta := ln.Addr().(*net.TCPAddr).Port
	j := &janelaApp{url: fmt.Sprintf("http://127.0.0.1:%d/?k=%s", porta, chave),
		perfil: filepath.Join(pastaUsuario(), "janela"), portatil: portatil, ultVivo: time.Now().Add(40 * time.Second)}
	b, _ := json.Marshal(map[string]any{"URL": j.url, "pid": os.Getpid()})
	_ = os.WriteFile(arqInst, b, 0o600)
	if portatil != nil {
		abrirJanelaHook = j.mostrar
	}

	host := fmt.Sprintf("127.0.0.1:%d", porta)
	mux := http.NewServeMux()
	ok := func(w http.ResponseWriter, r *http.Request) bool {
		if r.Host != host || (r.URL.Query().Get("k") != chave && r.Header.Get("X-K") != chave) {
			http.Error(w, "proibido", http.StatusForbidden)
			return false
		}
		return true
	}
	responder := func(w http.ResponseWriter, v any) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(v)
	}
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" || !ok(w, r) {
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; img-src 'self' https: http: data:; "+
			"style-src 'self' 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
		_, _ = w.Write(paginaJanela)
	})
	mux.HandleFunc("/estado", func(w http.ResponseWriter, r *http.Request) {
		if !ok(w, r) {
			return
		}
		j.mu.Lock()
		j.ultVivo = time.Now()
		inst := map[string]any{"pode": j.portatil != nil && !j.instalado, "andamento": j.instalando,
			"erro": j.instalErro, "feito": j.instalado}
		j.mu.Unlock()
		pub, _ := j.pastas()
		e, err := lerEstado(pub)
		responder(w, map[string]any{"estado": e, "ok": err == nil, "instalacao": inst, "contato": contatoLoja(),
			"empresa": empresa(), "usuario": usuarioAtual()})
	})
	mux.HandleFunc("/pedido", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !ok(w, r) {
			return
		}
		var d map[string]string
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32<<10)).Decode(&d); err != nil {
			http.Error(w, "inválido", 400)
			return
		}
		tipo := d["tipo"]
		if !map[string]bool{"chat": true, "ajuda": true, "encerrar": true, "revogar": true}[tipo] {
			http.Error(w, "inválido", 400)
			return
		}
		limpo := map[string]string{"usuario": usuarioAtual()}
		for _, k := range []string{"texto", "nome", "telefone", "problema"} {
			if v := strings.TrimSpace(d[k]); v != "" {
				if len(v) > 2000 {
					v = v[:2000]
				}
				limpo[k] = v
			}
		}
		_, ped := j.pastas()
		if err := gravarPedidoEm(ped, tipo, limpo); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		responder(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("/instalar", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !ok(w, r) {
			return
		}
		j.mu.Lock()
		if j.portatil == nil || j.instalando || j.instalado {
			j.mu.Unlock()
			responder(w, map[string]bool{"ok": false})
			return
		}
		j.instalando, j.instalErro = true, ""
		j.mu.Unlock()
		go func() {
			err := instalar(pastaDados())
			j.mu.Lock()
			j.instalando = false
			if err != nil {
				j.instalErro = err.Error()
			} else {
				j.instalado = true
			}
			j.mu.Unlock()
			if err == nil { // o serviço instalado assume, com o mesmo número
				j.portatil.parar()
				abrirJanelaHook = nil
				log.Print("instalado: o serviço assumiu o atendimento")
			}
		}()
		responder(w, map[string]bool{"ok": true})
	})
	mux.HandleFunc("/abrir", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !ok(w, r) {
			return
		}
		j.mu.Lock()
		j.ultVivo = time.Now().Add(30 * time.Second)
		j.mu.Unlock()
		abrirApp(j.url, j.perfil) // pedido explícito (duplo clique no atalho): abre mesmo se já houver uma
		responder(w, map[string]bool{"ok": true})
	})
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = srv.Serve(ln) }()
	abrirApp(j.url, j.perfil)

	// fecha quando a janela some; no modo sem instalar, continua enquanto o técnico estiver na tela
	for {
		time.Sleep(3 * time.Second)
		j.mu.Lock()
		parado := time.Since(j.ultVivo) > 25*time.Second
		j.mu.Unlock()
		if !parado {
			continue
		}
		if portatil != nil && !j.instalado && len(telasAtivas()) > 0 {
			j.mostrar() // o técnico ainda está na tela: o cliente precisa ver (e poder encerrar)
			continue
		}
		break
	}
	_ = srv.Close()
	_ = os.Remove(arqInst)
	if portatil != nil {
		portatil.parar()
		pararTodasTelas()
		time.Sleep(500 * time.Millisecond)
	}
}

func (j *janelaApp) mostrar() {
	j.mu.Lock()
	recente := time.Since(j.ultVivo) < 6*time.Second
	if !recente {
		j.ultVivo = time.Now().Add(30 * time.Second) // dá tempo de a janela abrir
	}
	j.mu.Unlock()
	if !recente {
		abrirApp(j.url, j.perfil)
	}
}
