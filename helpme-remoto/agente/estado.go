package main

// Comunicação entre o serviço (SYSTEM) e a janela do cliente (usuário comum), por arquivos:
//   publico/estado.json  – o serviço grava; a janela lê (número, técnico, chat, pedido de ajuda…)
//   pedidos/*.json       – a janela grava; o serviço envia ao servidor e apaga
// É o mesmo modelo de permissões da versão anterior (publico: leitura; pedidos: gravação pelos usuários).

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
)

type ChatMsg struct {
	ID      int    `json:"id,omitempty"`
	De      string `json:"de"`
	Tecnico bool   `json:"tecnico"`
	Texto   string `json:"texto"`
	Quando  string `json:"quando"`
}

// EstadoSrv: o que o servidor informa a cada heartbeat.
type EstadoSrv struct {
	Sessao *struct {
		ID        int    `json:"id"`
		Tecnico   string `json:"tecnico"`
		Telefone  string `json:"telefone"`
		Liberacao string `json:"liberacao"`
		Tipo      string `json:"tipo"`
		Inicio    string `json:"inicio"`
	} `json:"sessao"`
	Tela     bool   `json:"tela"`
	Continuo string `json:"continuo"`
	Modo     string `json:"modo"`
	Empresa  string `json:"empresa"`
	Contato  string `json:"contato"`
	Grupo    string `json:"grupo"`
	Nome     string `json:"nome"`
	CorMarca string `json:"cor_marca"`
}

type AjudaInfo struct {
	Protocolo string `json:"protocolo"`
	Link      string `json:"link"`
	Quando    string `json:"quando"`
	Erro      string `json:"erro,omitempty"`
}

type Estado struct {
	Versao     string     `json:"versao"`
	Marca      string     `json:"marca"`
	Servidor   string     `json:"servidor"`
	Numero     string     `json:"numero"`
	Portatil   bool       `json:"portatil"`
	Online     bool       `json:"online"`
	Atualizado string     `json:"atualizado"`
	Srv        EstadoSrv  `json:"srv"`
	TelaLocal  bool       `json:"tela_local"`
	Chat       []ChatMsg  `json:"chat"`
	Ajuda      *AjudaInfo `json:"ajuda"`
}

type estadoAgente struct {
	mu sync.Mutex
	e  Estado
}

var estadoLocal = &estadoAgente{}

func (s *estadoAgente) iniciar() {
	s.mu.Lock()
	s.e = Estado{Versao: versao, Marca: tituloApp, Servidor: cfg.Servidor, Numero: formatarNumero(cfg.NumeroAcesso),
		Portatil: modoPortatil}
	s.mu.Unlock()
	s.gravar()
}

func (s *estadoAgente) servidor(e EstadoSrv, chat []ChatMsg) {
	s.mu.Lock()
	s.e.Online, s.e.Srv, s.e.Marca = true, e, tituloApp
	s.e.Chat = append(s.e.Chat, chat...)
	if s.e.Sessao() == 0 && len(chat) == 0 && len(s.e.Chat) > 0 && !e.Tela {
		// sem atendimento: o chat antigo some depois de um tempo
		if ult, err := time.Parse(time.RFC3339, s.e.Chat[len(s.e.Chat)-1].Quando); err == nil &&
			time.Since(ult) > 2*time.Hour {
			s.e.Chat = nil
		}
	}
	if len(s.e.Chat) > 100 {
		s.e.Chat = s.e.Chat[len(s.e.Chat)-100:]
	}
	s.mu.Unlock()
	s.gravar()
}

func (e *Estado) Sessao() int {
	if e.Srv.Sessao == nil {
		return 0
	}
	return e.Srv.Sessao.ID
}

func (s *estadoAgente) offline() {
	s.mu.Lock()
	s.e.Online = false
	s.mu.Unlock()
	s.gravar()
}

func (s *estadoAgente) chatLocal(m ChatMsg) {
	s.mu.Lock()
	s.e.Chat = append(s.e.Chat, m)
	s.mu.Unlock()
	s.gravar()
}

func (s *estadoAgente) ajuda(a *AjudaInfo) {
	s.mu.Lock()
	s.e.Ajuda = a
	s.mu.Unlock()
	s.gravar()
}

func (s *estadoAgente) gravar() {
	s.mu.Lock()
	s.e.Atualizado = time.Now().Format(time.RFC3339)
	s.e.TelaLocal = len(telasAtivas()) > 0
	b, _ := json.MarshalIndent(s.e, "", " ")
	s.mu.Unlock()
	gravarAtomico(filepath.Join(pastaPublica(), "estado.json"), b, 0o644)
}

func gravarAtomico(caminho string, b []byte, modo os.FileMode) {
	tmp := caminho + ".tmp"
	if err := os.WriteFile(tmp, b, modo); err == nil {
		_ = os.Rename(tmp, caminho)
	}
}

func lerEstado(pasta string) (Estado, error) {
	var e Estado
	b, err := os.ReadFile(filepath.Join(pasta, "estado.json"))
	if err == nil {
		err = json.Unmarshal(b, &e)
	}
	return e, err
}

// gravarPedido é usado pela janela do cliente (e pelos atalhos) no lugar de falar com o servidor.
func gravarPedido(tipo string, dados map[string]string) error {
	return gravarPedidoEm(pastaPedidos(), tipo, dados)
}

func gravarPedidoEm(pasta, tipo string, dados map[string]string) error {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	dados["tipo"] = tipo
	dados["quando"] = time.Now().Format(time.RFC3339)
	j, _ := json.Marshal(dados)
	nome := filepath.Join(pasta, time.Now().Format("20060102150405")+"_"+tipo+"_"+hex.EncodeToString(b)+".json")
	return os.WriteFile(nome, j, 0o666)
}

// vigiarPedidos processa o que a janela do cliente gravou.
func vigiarPedidos(ctx context.Context) {
	for ctx.Err() == nil {
		arqs, _ := filepath.Glob(filepath.Join(pastaPedidos(), "*.json"))
		sort.Strings(arqs)
		for _, a := range arqs {
			processarPedido(a)
		}
		// compatibilidade com o atalho antigo "Cancelar acesso contínuo"
		if b, err := os.ReadFile(filepath.Join(pastaPedidos(), "revogar.txt")); err == nil {
			usuario := strings.SplitN(strings.TrimSpace(string(b)), "\n", 2)[0]
			if err := requisicao("POST", "/agente/revogar", map[string]string{"usuario": usuario}, nil); err == nil || recusado(err) {
				_ = os.Remove(filepath.Join(pastaPedidos(), "revogar.txt"))
			}
		}
		select {
		case <-ctx.Done():
		case <-time.After(time.Second):
		}
	}
}

func processarPedido(arq string) {
	b, err := os.ReadFile(arq)
	if err != nil || len(b) > 64<<10 {
		_ = os.Remove(arq)
		return
	}
	var d map[string]string
	if json.Unmarshal(b, &d) != nil {
		_ = os.Remove(arq)
		return
	}
	usuario := d["usuario"]
	if usuario == "" {
		usuario = usuarioLogado()
	}
	switch d["tipo"] {
	case "revogar":
		err = requisicao("POST", "/agente/revogar", map[string]string{"usuario": usuario}, nil)
		if err == nil {
			log.Printf("acesso contínuo cancelado pelo usuário %q", usuario)
		}
	case "encerrar": // o cliente fechou o acesso à tela
		pararTodasTelas()
		err = requisicao("POST", "/agente/remoto/encerrar", map[string]string{"usuario": usuario}, nil)
		log.Printf("acesso à tela encerrado pelo usuário %q", usuario)
	case "chat":
		texto := strings.TrimSpace(d["texto"])
		if texto == "" {
			break
		}
		err = requisicao("POST", "/agente/chat", map[string]string{"texto": texto, "usuario": usuario}, nil)
		if err == nil {
			estadoLocal.chatLocal(ChatMsg{De: usuario, Texto: texto, Quando: time.Now().Format(time.RFC3339)})
		}
	case "ajuda":
		var r struct {
			Protocolo string `json:"protocolo"`
			Link      string `json:"link"`
		}
		err = requisicao("POST", "/agente/ajuda", map[string]string{"nome": d["nome"], "telefone": d["telefone"],
			"problema": d["problema"], "usuario": usuario}, &r)
		if err == nil {
			estadoLocal.ajuda(&AjudaInfo{Protocolo: r.Protocolo, Link: r.Link, Quando: time.Now().Format(time.RFC3339)})
			acordarAgora()
		} else if recusado(err) {
			estadoLocal.ajuda(&AjudaInfo{Erro: err.Error(), Quando: time.Now().Format(time.RFC3339)})
		}
	}
	if err == nil || recusado(err) {
		_ = os.Remove(arq)
	} else {
		log.Printf("pedido %s: %v (nova tentativa em seguida)", filepath.Base(arq), err)
	}
}

// ------------------------------------------------------------------ canal de controle
// Conexão permanente com o servidor só para receber "acordar": assim um pedido do técnico chega na hora,
// sem esperar o próximo heartbeat. Os comandos continuam vindo pelo heartbeat, assinados.
func urlWS(caminho string) string {
	s := cfg.Servidor
	if strings.HasPrefix(s, "https://") {
		s = "wss://" + s[len("https://"):]
	} else if strings.HasPrefix(s, "http://") {
		s = "ws://" + s[len("http://"):]
	}
	return s + caminho
}

func cabecalhoAgente() http.Header {
	return http.Header{"Authorization": {"Bearer " + cfg.Token}, "User-Agent": {"HelpMeAgente/" + versao}}
}

func canalControle(ctx context.Context) {
	espera := 2 * time.Second
	for ctx.Err() == nil {
		c, _, err := websocket.Dial(ctx, urlWS("/ws/agente"), &websocket.DialOptions{HTTPHeader: cabecalhoAgente()})
		if err == nil {
			espera = 2 * time.Second
			acordarAgora()
			for {
				_, _, err = c.Read(ctx)
				if err != nil {
					break
				}
				acordarAgora()
			}
			c.CloseNow()
		}
		select {
		case <-ctx.Done():
		case <-time.After(espera):
		}
		if espera < time.Minute {
			espera *= 2
		}
	}
}

// ------------------------------------------------------------------ processos de tela
type processoTela interface {
	Kill() error
	Wait() (*os.ProcessState, error)
}

var (
	telas   = map[string]func(){}
	telasMu sync.Mutex
)

func telasAtivas() []string {
	telasMu.Lock()
	defer telasMu.Unlock()
	l := make([]string, 0, len(telas))
	for k := range telas {
		l = append(l, k)
	}
	return l
}

func iniciarTela(canal string) error {
	telasMu.Lock()
	if _, ok := telas[canal]; ok {
		telasMu.Unlock()
		return nil
	}
	telasMu.Unlock()
	var parar func()
	fim := func() {
		telasMu.Lock()
		delete(telas, canal)
		telasMu.Unlock()
		estadoLocal.gravar()
	}
	if modoPortatil { // sem serviço: a captura roda neste mesmo processo, como o usuário
		ctx, cancel := context.WithCancel(context.Background())
		parar = cancel
		go func() {
			if err := rodarTela(ctx, canal); err != nil {
				log.Println("tela:", err)
			}
			fim()
		}()
	} else {
		p, err := iniciarProcessoTela(canal)
		if err != nil {
			return err
		}
		parar = func() { _ = p.Kill() }
		go func() { _, _ = p.Wait(); fim() }()
	}
	telasMu.Lock()
	telas[canal] = parar
	telasMu.Unlock()
	estadoLocal.gravar()
	return nil
}

func pararTela(canal string) {
	telasMu.Lock()
	p := telas[canal]
	telasMu.Unlock()
	if p != nil {
		p()
	}
}

func pararTodasTelas() {
	for _, c := range telasAtivas() {
		pararTela(c)
	}
}
