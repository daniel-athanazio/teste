// Help me! - acesso remoto e suporte técnico (FN Info House).
//
// Um único executável (HelpMe.exe) faz tudo no computador do cliente:
//
//	HelpMe.exe                 abre a janela do cliente. Se ainda não estiver instalado, roda no modo
//	                           "sem instalar" (como o AnyDesk portátil): gera o número de acesso na hora.
//	HelpMe.exe instalar        instala para acesso permanente (pede administrador)
//	HelpMe.exe desinstalar     remove tudo (pede administrador)
//	HelpMe.exe executar        serviço (tarefa agendada SYSTEM): heartbeat, scripts, plugins, pedidos
//	HelpMe.exe tela -canal X   captura a tela e recebe mouse/teclado (aberto pelo serviço na sessão do usuário)
//	HelpMe.exe janela          só a janela do cliente
//	HelpMe.exe registrar -servidor URL [-codigo CODIGO] [-silencioso]
//	HelpMe.exe numero | revogar | status
//
// O endereço do servidor, a chave pública e o código da empresa vêm embutidos no final do .exe
// (o site de downloads acrescenta ao entregar o arquivo). Veja embutido.go.
package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Preenchidas na compilação (opcional): -ldflags "-X main.chavePublica=<base64> -X main.servidorPadrao=https://..."
var (
	chavePublica   = ""
	servidorPadrao = ""
	versao         = "1.0.0"
)

// Nome do aplicativo nas janelas do cliente; o servidor envia o nome configurado em Configurações › Marca.
var tituloApp = "Help me!"

// modoPortatil: rodando sem instalar (dados em %LOCALAPPDATA%), como usuário comum.
var modoPortatil = false

const (
	intervalo      = 5 * time.Second
	intervaloInfo  = time.Hour
	limiteSaida    = 100_000
	tempoAutorizar = 120 // segundos para o usuário responder
)

func contatoLoja() string {
	if emb.Contato != "" {
		return emb.Contato
	}
	return "FN Info House – WhatsApp (21) 96438-2098"
}

func empresa() string {
	if emb.Empresa != "" {
		return emb.Empresa
	}
	return "FN Info House"
}

type Config struct {
	Servidor      string `json:"servidor"`
	ChavePublica  string `json:"chave_publica"`
	EquipamentoID int    `json:"equipamento_id"`
	Token         string `json:"token"`
	Grupo         string `json:"grupo"`
	NumeroAcesso  string `json:"numero_acesso"`
	Portatil      bool   `json:"portatil"`
}

// Tarefa = script a executar. Mensagem = aviso/pedido ao cliente ou ajuste do agente.
type Tarefa struct {
	ID         int    `json:"id"`
	Conteudo   string `json:"conteudo"`
	Payload    string `json:"payload"`
	Assinatura string `json:"assinatura"`
}

type Mensagem struct {
	ID         int    `json:"id"`
	Payload    string `json:"payload"`
	Assinatura string `json:"assinatura"`
}

type Plano struct {
	ID         int    `json:"id"`
	Payload    string `json:"payload"`
	Assinatura string `json:"assinatura"`
	Passos     []struct {
		Indice   int    `json:"indice"`
		Conteudo string `json:"conteudo"`
	} `json:"passos"`
}

type Verificar struct {
	Tipo  string `json:"tipo"`
	Valor string `json:"valor"`
}

type PlanoPayload struct {
	PlanoID           int    `json:"plano_id"`
	EquipamentoID     int    `json:"equipamento_id"`
	Plugin            string `json:"plugin"`
	Descricao         string `json:"descricao"`
	Tecnico           string `json:"tecnico"`
	RequerAutorizacao bool   `json:"requer_autorizacao"`
	Automatico        bool   `json:"automatico"`
	ExpiraEm          string `json:"expira_em"`
	Arquivos          []struct {
		Nome    string `json:"nome"`
		Sha256  string `json:"sha256"`
		Tamanho int64  `json:"tamanho"`
	} `json:"arquivos"`
	Passos []struct {
		Indice    int       `json:"indice"`
		Nome      string    `json:"nome"`
		Sha256    string    `json:"sha256"`
		TimeoutS  int       `json:"timeout_s"`
		Verificar Verificar `json:"verificar"`
		EmFalha   string    `json:"em_falha"`
	} `json:"passos"`
}

type Pedido struct {
	ExecucaoID        int    `json:"execucao_id"`
	EquipamentoID     int    `json:"equipamento_id"`
	SessaoID          int    `json:"sessao_id"`
	Script            string `json:"script"`
	Descricao         string `json:"descricao"`
	Tecnico           string `json:"tecnico"`
	ConteudoSHA256    string `json:"conteudo_sha256"`
	RequerAutorizacao bool   `json:"requer_autorizacao"`
	Notificar         bool   `json:"notificar"`
	TimeoutS          int    `json:"timeout_s"`
	ExpiraEm          string `json:"expira_em"`
}

type MsgPayload struct {
	MensagemID    int               `json:"mensagem_id"`
	EquipamentoID int               `json:"equipamento_id"`
	Tipo          string            `json:"tipo"`
	Dados         map[string]string `json:"dados"`
	ExpiraEm      string            `json:"expira_em"`
}

var (
	atualizarInfo = make(chan struct{}, 1) // pede nova coleta de configuração (impressoras, portas…)
	acordar       = make(chan struct{}, 1) // o servidor avisou que há algo novo: heartbeat imediato
	cfg           Config
	cliente       = &http.Client{Timeout: 30 * time.Second}
	vistos        = map[string]bool{}
	vistosMu      sync.Mutex
)

func pastaDados() string {
	if p := os.Getenv("FN_AGENTE_DIR"); p != "" {
		return p
	}
	if modoPortatil {
		if la := os.Getenv("LOCALAPPDATA"); la != "" {
			return filepath.Join(la, "HelpMe", "portatil")
		}
		return filepath.Join(os.TempDir(), "helpme-portatil")
	}
	if pd := os.Getenv("ProgramData"); pd != "" {
		return filepath.Join(pd, "HelpMe")
	}
	return "/tmp/helpme"
}

// publico: legível por todos os usuários (número de acesso e estado para a janela do cliente).
// pedidos: gravável pelos usuários (pedir ajuda, chat, encerrar acesso, cancelar acesso contínuo).
func pastaPublica() string  { return filepath.Join(pastaDados(), "publico") }
func pastaPedidos() string  { return filepath.Join(pastaDados(), "pedidos") }
func caminhoConfig() string { return filepath.Join(pastaDados(), "config.json") }

func carregarConfig() error {
	b, err := os.ReadFile(caminhoConfig())
	if err != nil {
		return fmt.Errorf("agente não registrado (%w)", err)
	}
	return json.Unmarshal(b, &cfg)
}

func salvarConfig() error {
	if err := os.MkdirAll(pastaDados(), 0o700); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(cfg, "", "  ")
	return os.WriteFile(caminhoConfig(), b, 0o600)
}

func formatarNumero(n string) string {
	if len(n) == 9 {
		return n[:3] + " " + n[3:6] + " " + n[6:]
	}
	return n
}

func main() {
	emb = lerEmbutido()
	if emb.Marca != "" {
		tituloApp = emb.Marca
	}
	prepararProcesso()
	cmd := ""
	if len(os.Args) > 1 {
		cmd = os.Args[1]
	}
	switch cmd {
	case "", "abrir":
		abrir()
	case "instalar":
		fs := flag.NewFlagSet("instalar", flag.ExitOnError)
		origem := fs.String("de", "", "pasta do modo sem instalar (mantém o mesmo número de acesso)")
		_ = fs.Parse(os.Args[2:])
		if err := instalar(*origem); err != nil {
			caixaMensagem(tituloApp, "Não foi possível instalar: "+err.Error()+"\n\n"+contatoLoja())
			os.Exit(1)
		}
	case "desinstalar":
		if err := desinstalar(); err != nil {
			caixaMensagem(tituloApp, "Não foi possível remover: "+err.Error())
			os.Exit(1)
		}
	case "registrar":
		fs := flag.NewFlagSet("registrar", flag.ExitOnError)
		servidor := fs.String("servidor", emb.Servidor, "URL do servidor Help me! (https://...)")
		codigo := fs.String("codigo", emb.Codigo, "código de ativação do grupo (opcional)")
		silencioso := fs.Bool("silencioso", false, "não mostrar janela com o número")
		_ = fs.Parse(os.Args[2:])
		_ = os.MkdirAll(pastaDados(), 0o700)
		if err := registrar(strings.TrimRight(*servidor, "/"), *codigo, *silencioso); err != nil {
			log.Println(err)
			os.Exit(1)
		}
	case "executar":
		_ = os.MkdirAll(pastaDados(), 0o700)
		configurarLog("agente.log")
		if err := carregarConfig(); err != nil {
			log.Fatal(err)
		}
		laco(context.Background())
	case "tela":
		fs := flag.NewFlagSet("tela", flag.ExitOnError)
		canal := fs.String("canal", "", "canal do acesso remoto")
		portatil := fs.Bool("portatil", false, "")
		_ = fs.Parse(os.Args[2:])
		modoPortatil = *portatil
		configurarLog("tela.log")
		if err := carregarConfig(); err != nil {
			log.Fatal(err)
		}
		if err := rodarTela(context.Background(), *canal); err != nil {
			log.Println("tela:", err)
		}
	case "janela":
		rodarJanela(nil)
	case "numero":
		b, err := os.ReadFile(filepath.Join(pastaPublica(), "numero.txt"))
		if err != nil {
			caixaMensagem(tituloApp, "O "+tituloApp+" ainda não foi instalado corretamente neste computador.\n\n"+contatoLoja())
			os.Exit(1)
		}
		n := strings.TrimSpace(string(b))
		fmt.Println(n)
		caixaMensagem(tituloApp+" – número de acesso", "Seu número de acesso é:\n\n        "+n+
			"\n\nInforme este número ao técnico da "+contatoLoja()+".")
	case "revogar":
		if !caixaPergunta(tituloApp, "Deseja CANCELAR o acesso contínuo da "+empresa()+" a este computador?\n\n"+
			"Depois disso, cada novo atendimento precisará da sua autorização.") {
			return
		}
		if err := gravarPedido("revogar", map[string]string{"usuario": usuarioAtual()}); err != nil {
			caixaMensagem(tituloApp, "Não foi possível registrar o pedido: "+err.Error()+"\n\n"+contatoLoja())
			os.Exit(1)
		}
		caixaMensagem(tituloApp, "Pedido registrado. O acesso contínuo será cancelado em alguns segundos "+
			"e você receberá a confirmação na tela.")
	case "status":
		if err := carregarConfig(); err != nil {
			fmt.Println(err)
			os.Exit(1)
		}
		fmt.Printf("Servidor: %s\nNúmero de acesso: %s\nGrupo: %s\nEquipamento nº %d\nVersão %s\n",
			cfg.Servidor, formatarNumero(cfg.NumeroAcesso), cfg.Grupo, cfg.EquipamentoID, versao)
	default:
		fmt.Println("comando desconhecido:", cmd)
		os.Exit(2)
	}
}

// abrir: duplo clique no HelpMe.exe baixado do site.
func abrir() {
	if instaladoNoPC() { // já instalado: só mostra a janela do serviço instalado
		rodarJanela(nil)
		return
	}
	modoPortatil = true
	_ = os.MkdirAll(pastaDados(), 0o700)
	configurarLog("agente.log")
	if err := carregarConfig(); err != nil {
		if err := registrar(emb.Servidor, emb.Codigo, true); err != nil {
			caixaMensagem(tituloApp, "Não foi possível conectar ao suporte:\n"+err.Error()+
				"\n\nVerifique a internet e tente de novo.\n"+contatoLoja())
			os.Exit(1)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	go laco(ctx)
	rodarJanela(&controlePortatil{parar: cancel})
}

func configurarLog(nome string) {
	_ = os.MkdirAll(pastaDados(), 0o700)
	f, err := os.OpenFile(filepath.Join(pastaDados(), nome), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err == nil {
		if st, _ := f.Stat(); st != nil && st.Size() > 5<<20 { // log não cresce sem limite
			_ = f.Truncate(0)
		}
		log.SetOutput(io.MultiWriter(os.Stdout, f))
	}
}

// ------------------------------------------------------------------ HTTP
func requisicao(metodo, caminho string, corpo any, saida any) error {
	var r io.Reader
	if corpo != nil {
		b, _ := json.Marshal(corpo)
		r = bytes.NewReader(b)
	}
	req, err := http.NewRequest(metodo, cfg.Servidor+caminho, r)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "HelpMeAgente/"+versao)
	if cfg.Token != "" {
		req.Header.Set("Authorization", "Bearer "+cfg.Token)
	}
	resp, err := cliente.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if resp.StatusCode >= 300 {
		return &erroHTTP{resp.StatusCode, fmt.Sprintf("%s %s: HTTP %d %s", metodo, caminho, resp.StatusCode,
			strings.TrimSpace(string(b)))}
	}
	if saida != nil {
		return json.Unmarshal(b, saida)
	}
	return nil
}

type erroHTTP struct {
	codigo int
	texto  string
}

func (e *erroHTTP) Error() string { return e.texto }

// recusado: o servidor respondeu 4xx (não adianta tentar de novo).
func recusado(err error) bool {
	var e *erroHTTP
	return errors.As(err, &e) && e.codigo >= 400 && e.codigo < 500
}

func registrar(servidor, codigo string, silencioso bool) error {
	if servidor == "" {
		return errors.New("servidor não informado")
	}
	if !strings.HasPrefix(servidor, "https://") && os.Getenv("FN_PERMITIR_HTTP") != "1" {
		return errors.New("o servidor precisa usar https://")
	}
	cfg = Config{Servidor: servidor, ChavePublica: emb.ChavePublica, Portatil: modoPortatil}
	if cfg.ChavePublica == "" { // sem chave embutida: confia no primeiro contato
		var k struct {
			ChavePublica string `json:"chave_publica"`
		}
		if err := requisicao("GET", "/agente/chave-publica", nil, &k); err != nil {
			return err
		}
		cfg.ChavePublica = k.ChavePublica
	}
	host, _ := os.Hostname()
	var r struct {
		EquipamentoID int    `json:"equipamento_id"`
		Token         string `json:"token"`
		Grupo         string `json:"grupo"`
		NumeroAcesso  string `json:"numero_acesso"`
		NumeroFmt     string `json:"numero_fmt"`
	}
	err := requisicao("POST", "/agente/registrar", map[string]any{
		"codigo_ativacao": codigo, "hostname": host, "usuario_windows": usuarioLogado(),
		"info": coletarInfo(),
	}, &r)
	if err != nil {
		return err
	}
	cfg.EquipamentoID, cfg.Token, cfg.Grupo, cfg.NumeroAcesso = r.EquipamentoID, r.Token, r.Grupo, r.NumeroAcesso
	if err := salvarConfig(); err != nil {
		return err
	}
	prepararPastas()
	fmt.Printf("Registrado. Número de acesso: %s\n", r.NumeroFmt)
	if !silencioso {
		caixaMensagem(tituloApp, "Instalação concluída!\n\nSeu número de acesso é:\n\n        "+r.NumeroFmt+
			"\n\nEnvie este número para a "+contatoLoja()+".")
	}
	return nil
}

func prepararPastas() {
	_ = os.MkdirAll(pastaPublica(), 0o755)
	_ = os.MkdirAll(pastaPedidos(), 0o777)
	_ = os.WriteFile(filepath.Join(pastaPublica(), "numero.txt"), []byte(formatarNumero(cfg.NumeroAcesso)+"\r\n"), 0o644)
}

// ------------------------------------------------------------------ laço principal
func laco(ctx context.Context) {
	log.Printf("%s %s iniciado – equipamento %d (nº %s) em %s (portátil=%v)", tituloApp, versao, cfg.EquipamentoID,
		formatarNumero(cfg.NumeroAcesso), cfg.Servidor, modoPortatil)
	prepararPastas()
	estadoLocal.iniciar()
	go canalControle(ctx)
	go vigiarPedidos(ctx)
	ultimaInfo := time.Time{}
	for ctx.Err() == nil {
		corpo := map[string]any{"usuario_windows": usuarioLogado(), "portatil": modoPortatil,
			"telas": telasAtivas()}
		select {
		case <-atualizarInfo:
			ultimaInfo = time.Time{}
		default:
		}
		if time.Since(ultimaInfo) > intervaloInfo {
			info := coletarInfo()
			info["portatil"] = modoPortatil
			corpo["info"] = info
		}
		var r struct {
			Tarefas   []Tarefa   `json:"tarefas"`
			Mensagens []Mensagem `json:"mensagens"`
			Planos    []Plano    `json:"planos"`
			Marca     string     `json:"marca"`
			Estado    EstadoSrv  `json:"estado"`
			Chat      []ChatMsg  `json:"chat"`
		}
		if err := requisicao("POST", "/agente/heartbeat", corpo, &r); err != nil {
			log.Println("heartbeat:", err)
			estadoLocal.offline()
		} else {
			if _, ok := corpo["info"]; ok {
				ultimaInfo = time.Now()
			}
			if r.Marca != "" {
				tituloApp = r.Marca
			}
			estadoLocal.servidor(r.Estado, r.Chat)
			if len(r.Chat) > 0 {
				abrirJanelaCliente() // mensagem nova do técnico: mostra a janela
			}
			for _, m := range r.Mensagens { // avisos em ordem; pedidos ao cliente rodam em paralelo
				processarMensagem(m)
			}
			for _, t := range r.Tarefas {
				go processar(t)
			}
			for _, pl := range r.Planos {
				go processarPlano(pl)
			}
		}
		select {
		case <-ctx.Done():
		case <-acordar:
		case <-time.After(intervalo):
		}
	}
	pararTodasTelas()
}

func acordarAgora() {
	select {
	case acordar <- struct{}{}:
	default:
	}
}

func assinaturaValida(payload, assinatura string) bool {
	chave, err := base64.StdEncoding.DecodeString(cfg.ChavePublica)
	if err != nil || len(chave) != ed25519.PublicKeySize {
		return false
	}
	sig, err := base64.StdEncoding.DecodeString(assinatura)
	return err == nil && ed25519.Verify(chave, []byte(payload), sig)
}

func primeiraVez(chave string) bool {
	vistosMu.Lock()
	defer vistosMu.Unlock()
	if vistos[chave] {
		return false
	}
	vistos[chave] = true
	return true
}

func expirou(s string) bool {
	exp, err := time.Parse(time.RFC3339, s)
	return err != nil || time.Now().After(exp)
}

func verificar(t Tarefa) (Pedido, error) {
	var p Pedido
	if !assinaturaValida(t.Payload, t.Assinatura) {
		return p, errors.New("assinatura inválida")
	}
	if err := json.Unmarshal([]byte(t.Payload), &p); err != nil {
		return p, err
	}
	soma := sha256.Sum256([]byte(t.Conteudo))
	switch {
	case p.ExecucaoID != t.ID:
		return p, errors.New("id não confere")
	case p.EquipamentoID != cfg.EquipamentoID:
		return p, errors.New("pedido de outro equipamento")
	case hex.EncodeToString(soma[:]) != p.ConteudoSHA256:
		return p, errors.New("conteúdo alterado")
	case expirou(p.ExpiraEm):
		return p, errors.New("pedido expirado")
	case !primeiraVez(fmt.Sprintf("x%d", p.ExecucaoID)):
		return p, errors.New("pedido repetido")
	}
	return p, nil
}

func processar(t Tarefa) {
	p, err := verificar(t)
	if err != nil {
		log.Printf("tarefa %d RECUSADA: %v", t.ID, err)
		return
	}
	log.Printf("tarefa %d: %s (técnico %s)", p.ExecucaoID, p.Script, p.Tecnico)
	if p.RequerAutorizacao {
		ok, usuario := perguntar(tituloApp+" – autorização", textoAutorizacao(p, t.Conteudo))
		_ = requisicao("POST", fmt.Sprintf("/agente/execucoes/%d/resposta", p.ExecucaoID),
			map[string]any{"autorizado": ok, "usuario": usuario}, nil)
		if !ok {
			log.Printf("tarefa %d negada por %q", p.ExecucaoID, usuario)
			return
		}
	} else if p.Notificar {
		notificar(tituloApp+" – procedimento em execução",
			fmt.Sprintf("%s está executando neste computador:\n\n%s\n%s", p.Tecnico, p.Script, p.Descricao))
	}
	ini := time.Now().UTC()
	codigo, saida := rodarScript(t.Conteudo, time.Duration(p.TimeoutS)*time.Second, nil)
	fim := time.Now().UTC()
	if len(saida) > limiteSaida {
		saida = saida[:limiteSaida] + "\n[saída cortada]"
	}
	err = requisicao("POST", fmt.Sprintf("/agente/execucoes/%d/resultado", p.ExecucaoID), map[string]any{
		"exit_code": codigo, "saida": saida, "inicio": ini.Format(time.RFC3339), "fim": fim.Format(time.RFC3339),
	}, nil)
	if err != nil {
		log.Printf("tarefa %d: falha ao enviar resultado: %v", p.ExecucaoID, err)
	}
	log.Printf("tarefa %d concluída (código %d)", p.ExecucaoID, codigo)
}

func processarMensagem(m Mensagem) {
	var p MsgPayload
	switch {
	case !assinaturaValida(m.Payload, m.Assinatura):
		log.Printf("mensagem %d RECUSADA: assinatura inválida", m.ID)
		return
	case json.Unmarshal([]byte(m.Payload), &p) != nil, p.MensagemID != m.ID:
		log.Printf("mensagem %d RECUSADA: conteúdo inválido", m.ID)
		return
	case p.EquipamentoID != cfg.EquipamentoID:
		log.Printf("mensagem %d RECUSADA: outro equipamento", m.ID)
		return
	case expirou(p.ExpiraEm):
		log.Printf("mensagem %d RECUSADA: expirada", m.ID)
		return
	case !primeiraVez(fmt.Sprintf("m%d", p.MensagemID)):
		return
	}
	responder := func(aceito bool, usuario, detalhe string) {
		err := requisicao("POST", fmt.Sprintf("/agente/mensagens/%d/resposta", m.ID),
			map[string]any{"aceito": aceito, "usuario": usuario, "detalhe": detalhe}, nil)
		if err != nil {
			log.Printf("mensagem %d: falha ao responder: %v", m.ID, err)
		}
	}
	switch p.Tipo {
	case "aviso", "continuo_encerrado":
		pedirAtualizacaoInfo()
		notificar(p.Dados["titulo"], p.Dados["texto"])
	case "pedir_inicio":
		pedirAtualizacaoInfo()
		go func() {
			ok, usuario := perguntar(tituloApp+" – pedido de atendimento", p.Dados["texto"])
			responder(ok, usuario, "")
			log.Printf("pedido de atendimento: aceito=%v por %q", ok, usuario)
			if ok {
				abrirJanelaCliente()
			}
		}()
	case "pedir_continuo":
		go func() {
			ok, usuario := perguntar(tituloApp+" – acesso contínuo", p.Dados["texto"])
			responder(ok, usuario, "modo "+p.Dados["modo"])
			log.Printf("pedido de acesso contínuo (%s): aceito=%v por %q", p.Dados["modo"], ok, usuario)
			if ok {
				modo := "Você será avisado a cada acesso."
				if p.Dados["modo"] == "autorizar" {
					modo = "Cada acesso continuará pedindo a sua autorização."
				}
				notificar(tituloApp, "Acesso contínuo ativado. "+modo+
					"\nPara cancelar: abra o "+tituloApp+" e clique em Cancelar acesso contínuo.")
			}
		}()
	case "remoto": // o técnico abriu a tela remota (o atendimento já foi liberado pelo cliente)
		canal := p.Dados["canal"]
		if canal == "" {
			return
		}
		log.Printf("acesso à tela: canal %s… técnico %s", canal[:6], p.Dados["tecnico"])
		if err := iniciarTela(canal); err != nil {
			log.Println("iniciar tela:", err)
			responder(false, "sistema", err.Error())
			return
		}
		responder(true, "sistema", "")
		abrirJanelaCliente()
	case "remoto_fim":
		pararTela(p.Dados["canal"])
	case "senha": // versões antigas (cliente RustDesk): não se aplica mais
		responder(true, "sistema", "ignorado")
	default:
		log.Printf("mensagem %d: tipo desconhecido %q", m.ID, p.Tipo)
	}
}

func textoAutorizacao(p Pedido, conteudo string) string {
	cmd := conteudo
	if len(cmd) > 700 {
		cmd = cmd[:700] + "\n[...]"
	}
	return fmt.Sprintf("O técnico %s, da %s, pede autorização para executar neste computador:\n\n"+
		"%s\n%s\n\nComandos:\n%s\n\n"+
		"A execução e o resultado ficam registrados no relatório do atendimento (LGPD).\n"+
		"Deseja autorizar?", p.Tecnico, empresa(), p.Script, p.Descricao, cmd)
}

func contexto(d time.Duration) (context.Context, context.CancelFunc) {
	if d <= 0 {
		d = 2 * time.Minute
	}
	return context.WithTimeout(context.Background(), d)
}

// ------------------------------------------------------------------ plugins de ação
func processarPlano(pl Plano) {
	var p PlanoPayload
	switch {
	case !assinaturaValida(pl.Payload, pl.Assinatura):
		log.Printf("plano %d RECUSADO: assinatura inválida", pl.ID)
		return
	case json.Unmarshal([]byte(pl.Payload), &p) != nil, p.PlanoID != pl.ID:
		log.Printf("plano %d RECUSADO: conteúdo inválido", pl.ID)
		return
	case p.EquipamentoID != cfg.EquipamentoID:
		log.Printf("plano %d RECUSADO: outro equipamento", pl.ID)
		return
	case expirou(p.ExpiraEm):
		log.Printf("plano %d RECUSADO: expirado", pl.ID)
		return
	case !primeiraVez(fmt.Sprintf("p%d", p.PlanoID)) || len(pl.Passos) != len(p.Passos):
		return
	}
	conteudos := map[int]string{}
	for _, x := range pl.Passos {
		soma := sha256.Sum256([]byte(x.Conteudo))
		conteudos[x.Indice] = x.Conteudo
		for _, ps := range p.Passos {
			if ps.Indice == x.Indice && ps.Sha256 != hex.EncodeToString(soma[:]) {
				log.Printf("plano %d RECUSADO: passo %d alterado", pl.ID, x.Indice)
				return
			}
		}
	}
	base := fmt.Sprintf("/agente/planos/%d", p.PlanoID)
	fim := func(detalhe string) {
		pedirAtualizacaoInfo()
		if err := requisicao("POST", base+"/fim", map[string]string{"detalhe": detalhe}, nil); err != nil {
			log.Printf("plano %d: falha ao finalizar: %v", p.PlanoID, err)
		}
	}
	log.Printf("plano %d: %s (%d passos, técnico %s)", p.PlanoID, p.Plugin, len(p.Passos), p.Tecnico)
	if p.RequerAutorizacao {
		ok, usuario := perguntar(tituloApp+" – autorização",
			fmt.Sprintf("%s, da %s, pede autorização para executar neste computador:\n\n%s\n%s\n\n"+
				"A execução fica registrada no relatório do atendimento (LGPD).\nDeseja autorizar?",
				p.Tecnico, empresa(), p.Plugin, p.Descricao))
		_ = requisicao("POST", base+"/resposta", map[string]any{"autorizado": ok, "usuario": usuario}, nil)
		if !ok {
			log.Printf("plano %d negado por %q", p.PlanoID, usuario)
			return
		}
	} else {
		quem := p.Tecnico + " está executando"
		if p.Automatico {
			quem = "Verificação automática do atendimento de " + p.Tecnico + ":"
		}
		notificar(tituloApp+" – procedimento em execução", fmt.Sprintf("%s\n\n%s\n%s", quem, p.Plugin, p.Descricao))
	}

	dir := filepath.Join(pastaDados(), "tmp", fmt.Sprintf("plano_%d", p.PlanoID))
	_ = os.MkdirAll(dir, 0o700)
	defer os.RemoveAll(dir)
	for _, a := range p.Arquivos {
		if err := baixarArquivo(a.Nome, a.Sha256, a.Tamanho, filepath.Join(dir, a.Nome)); err != nil {
			log.Printf("plano %d: arquivo %s: %v", p.PlanoID, a.Nome, err)
			fim("arquivo " + a.Nome + ": " + err.Error())
			return
		}
	}
	env := []string{"HM_ARQUIVOS=" + dir}
	for _, ps := range p.Passos {
		ini := time.Now().UTC()
		codigo, saida := rodarScript(conteudos[ps.Indice], time.Duration(ps.TimeoutS)*time.Second, env)
		fimPasso := time.Now().UTC()
		if len(saida) > limiteSaida {
			saida = saida[:limiteSaida] + "\n[saída cortada]"
		}
		ok := avaliar(ps.Verificar, codigo, saida)
		var r struct {
			Ok bool `json:"ok"`
		}
		if err := requisicao("POST", base+"/passo", map[string]any{"indice": ps.Indice, "exit_code": codigo,
			"saida": saida, "inicio": ini.Format(time.RFC3339), "fim": fimPasso.Format(time.RFC3339)}, &r); err == nil {
			ok = r.Ok // o servidor tem a palavra final
		}
		log.Printf("plano %d passo %d (%s): ok=%v código %d", p.PlanoID, ps.Indice+1, ps.Nome, ok, codigo)
		if !ok && ps.EmFalha != "continuar" {
			fim("parou no passo " + ps.Nome)
			return
		}
	}
	fim("")
}

func avaliar(v Verificar, codigo int, saida string) bool {
	if codigo != 0 {
		return false
	}
	switch v.Tipo {
	case "", "codigo_zero":
		return true
	case "contem":
		return strings.Contains(strings.ToLower(saida), strings.ToLower(v.Valor))
	case "nao_contem":
		return !strings.Contains(strings.ToLower(saida), strings.ToLower(v.Valor))
	case "regex":
		re, err := regexp.Compile("(?im)" + v.Valor)
		return err == nil && re.MatchString(saida)
	}
	return false
}

func baixarArquivo(nome, hash string, tamanho int64, destino string) error {
	req, err := http.NewRequest("GET", cfg.Servidor+"/agente/arquivos/"+nome, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+cfg.Token)
	c := &http.Client{Timeout: 2 * time.Hour}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	f, err := os.OpenFile(destino, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(f, h), io.LimitReader(resp.Body, tamanho+1))
	f.Close()
	if err != nil {
		return err
	}
	if n != tamanho || hex.EncodeToString(h.Sum(nil)) != hash {
		os.Remove(destino)
		return errors.New("arquivo corrompido ou diferente do cadastrado")
	}
	return nil
}

func pedirAtualizacaoInfo() {
	select {
	case atualizarInfo <- struct{}{}:
	default:
	}
}
