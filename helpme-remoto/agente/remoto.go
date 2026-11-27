package main

// Acesso à tela: captura, envia só os blocos que mudaram (JPEG) e aplica mouse/teclado do técnico.
//
// Protocolo (WebSocket, retransmitido pelo servidor entre este processo e o navegador do técnico):
//   agente → técnico, binário:  [1][x u16][y u16][JPEG]   bloco alterado
//                               [2][seq u32]               fim do quadro (o técnico responde {"t":"ack"})
//   agente → técnico, texto JSON: tela (tamanho/monitores), cursor, clip, desktop, aviso
//   técnico → agente, texto JSON: ack, m/md/mu (mouse), w (roda), k (tecla por scancode), txt, cad,
//                                 clip, q (qualidade), mon (monitor), bloq (bloquear cliente), refresh, fim
// Controle de fluxo: no máximo 2 quadros sem confirmação; em conexões lentas a taxa cai sozinha.

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/jpeg"
	"log"
	"runtime"
	"time"

	"github.com/coder/websocket"
)

type Monitor struct {
	X         int  `json:"x"`
	Y         int  `json:"y"`
	W         int  `json:"w"`
	H         int  `json:"h"`
	Principal bool `json:"principal"`
}

// capturador: implementação por sistema (tela_windows.go; tela_outros.go é só para testes).
// Todos os métodos são chamados pela mesma thread (exigência do Windows para trocar de área de trabalho).
type capturador interface {
	Monitores() []Monitor
	Selecionar(i int) // -1 = todos os monitores
	Capturar() (*image.RGBA, error)
	Cursor() (x, y int, forma string, visivel bool)
	Mouse(acao string, botao, x, y int)
	Roda(dx, dy int)
	Tecla(scan uint16, estendida, solta bool)
	Texto(s string)
	CtrlAltDel() error
	Bloquear(on bool) error
	Desktop() string
	Fechar()
}

const tamBloco = 64

func rodarTela(ctx context.Context, canal string) error {
	if canal == "" {
		return errors.New("canal vazio")
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	cap, err := novoCapturador()
	if err != nil {
		return fmt.Errorf("captura: %w", err)
	}
	defer cap.Fechar()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, urlWS("/ws/remoto/agente?canal="+canal),
		&websocket.DialOptions{HTTPHeader: cabecalhoAgente(), CompressionMode: websocket.CompressionDisabled})
	if err != nil {
		return err
	}
	defer conn.CloseNow()
	conn.SetReadLimit(4 << 20)
	log.Printf("tela: conectado (canal %s…)", canal[:6])

	cmds := make(chan map[string]any, 512)
	go func() {
		defer close(cmds)
		for {
			_, b, err := conn.Read(ctx)
			if err != nil {
				return
			}
			var m map[string]any
			if json.Unmarshal(b, &m) == nil {
				cmds <- m
			}
		}
	}()
	enviarJSON := func(v any) error {
		b, _ := json.Marshal(v)
		wctx, c := context.WithTimeout(ctx, 20*time.Second)
		defer c()
		return conn.Write(wctx, websocket.MessageText, b)
	}

	clip := novoClipboard()
	defer clip.fechar()
	var (
		anterior   []byte
		w, h       int
		seq        uint32
		pendentes  int
		qualidade  = 55
		tudo       = true
		monitor    = 0
		cursorAnt  string
		desktopAnt string
		ultClip    = time.Now()
		bloqueado  bool
		ultQuadro  time.Time
		ultAck     = time.Now()
	)
	defer func() {
		if bloqueado {
			_ = cap.Bloquear(false)
		}
	}()
	cap.Selecionar(monitor)
	enviarInfo := func(img *image.RGBA) {
		_ = enviarJSON(map[string]any{"t": "tela", "w": img.Rect.Dx(), "h": img.Rect.Dy(),
			"monitores": cap.Monitores(), "monitor": monitor, "versao": versao, "portatil": modoPortatil})
	}
	tratar := func(m map[string]any) bool {
		n := func(k string) int { f, _ := m[k].(float64); return int(f) }
		s := func(k string) string { v, _ := m[k].(string); return v }
		b := func(k string) bool { v, _ := m[k].(bool); return v }
		switch s("t") {
		case "ack":
			if pendentes > 0 {
				pendentes--
			}
			ultAck = time.Now()
		case "m":
			cap.Mouse("mover", 0, n("x"), n("y"))
		case "md":
			cap.Mouse("baixo", n("b"), n("x"), n("y"))
		case "mu":
			cap.Mouse("solto", n("b"), n("x"), n("y"))
		case "w":
			cap.Roda(n("dx"), n("dy"))
		case "k":
			cap.Tecla(uint16(n("sc")), b("e"), !b("d"))
		case "txt":
			cap.Texto(s("s"))
		case "cad":
			if err := cap.CtrlAltDel(); err != nil {
				_ = enviarJSON(map[string]any{"t": "aviso", "texto": "Ctrl+Alt+Del: " + err.Error()})
			}
		case "clip":
			clip.escrever(s("s"))
		case "q":
			if q := n("v"); q >= 10 && q <= 95 {
				qualidade, tudo = q, true
			}
		case "mon":
			monitor = n("i")
			cap.Selecionar(monitor)
			anterior, tudo = nil, true
		case "bloq":
			if err := cap.Bloquear(b("v")); err != nil {
				_ = enviarJSON(map[string]any{"t": "aviso", "texto": "Bloquear teclado/mouse: " + err.Error()})
			} else {
				bloqueado = b("v")
				_ = enviarJSON(map[string]any{"t": "bloq", "v": bloqueado})
			}
		case "refresh": // técnico novo na tela: manda tudo de novo, inclusive tamanho e monitores
			tudo, w, h = true, 0, 0
			pendentes = 0
			cursorAnt, desktopAnt = "", ""
		case "fim":
			return false
		}
		return true
	}

	for ctx.Err() == nil {
		// comandos que já chegaram
	drenar:
		for {
			select {
			case m, ok := <-cmds:
				if !ok || !tratar(m) {
					return nil
				}
			default:
				break drenar
			}
		}
		if pendentes >= 2 && time.Since(ultAck) < 3*time.Second {
			select {
			case m, ok := <-cmds:
				if !ok || !tratar(m) {
					return nil
				}
			case <-time.After(15 * time.Millisecond):
			}
			continue
		}
		if pendentes >= 2 { // confirmação perdida: recomeça com o quadro inteiro
			pendentes, tudo = 0, true
		}
		if d := 33*time.Millisecond - time.Since(ultQuadro); d > 0 { // até ~30 quadros/s
			select {
			case m, ok := <-cmds:
				if !ok || !tratar(m) {
					return nil
				}
			case <-time.After(d):
			}
			continue
		}
		ultQuadro = time.Now()

		if d := cap.Desktop(); d != desktopAnt {
			desktopAnt = d
			_ = enviarJSON(map[string]any{"t": "desktop", "nome": d})
			tudo = true
		}
		img, err := cap.Capturar()
		if err != nil {
			time.Sleep(150 * time.Millisecond)
			continue
		}
		if img.Rect.Dx() != w || img.Rect.Dy() != h {
			w, h = img.Rect.Dx(), img.Rect.Dy()
			anterior, tudo = nil, true
			enviarInfo(img)
		}
		blocos := blocosAlterados(anterior, img, tudo)
		tudo = false
		if len(blocos) > 0 {
			var buf bytes.Buffer
			for _, r := range blocos {
				buf.Reset()
				buf.Write([]byte{1, 0, 0, 0, 0})
				binary.BigEndian.PutUint16(buf.Bytes()[1:3], uint16(r.Min.X))
				binary.BigEndian.PutUint16(buf.Bytes()[3:5], uint16(r.Min.Y))
				if err := jpeg.Encode(&buf, img.SubImage(r), &jpeg.Options{Quality: qualidade}); err != nil {
					continue
				}
				wctx, c := context.WithTimeout(ctx, 30*time.Second)
				err := conn.Write(wctx, websocket.MessageBinary, buf.Bytes())
				c()
				if err != nil {
					return err
				}
			}
			seq++
			fimQ := []byte{2, 0, 0, 0, 0}
			binary.BigEndian.PutUint32(fimQ[1:], seq)
			if err := conn.Write(ctx, websocket.MessageBinary, fimQ); err != nil {
				return err
			}
			pendentes++
			if len(anterior) != len(img.Pix) {
				anterior = make([]byte, len(img.Pix))
			}
			copy(anterior, img.Pix)
		}
		if x, y, forma, vis := cap.Cursor(); true {
			k := fmt.Sprint(x, y, forma, vis)
			if k != cursorAnt {
				cursorAnt = k
				_ = enviarJSON(map[string]any{"t": "cursor", "x": x, "y": y, "forma": forma, "v": vis})
			}
		}
		if time.Since(ultClip) > 700*time.Millisecond {
			ultClip = time.Now()
			if txt, novo := clip.novo(); novo {
				_ = enviarJSON(map[string]any{"t": "clip", "s": txt})
			}
		}
	}
	return nil
}

// blocosAlterados compara com o quadro anterior em blocos de 64×64 e junta os blocos vizinhos da mesma faixa.
func blocosAlterados(ant []byte, img *image.RGBA, tudo bool) []image.Rectangle {
	w, h := img.Rect.Dx(), img.Rect.Dy()
	var out []image.Rectangle
	if tudo || len(ant) != len(img.Pix) {
		for y := 0; y < h; y += tamBloco * 2 {
			out = append(out, image.Rect(0, y, w, min(y+tamBloco*2, h)).Add(img.Rect.Min))
		}
		return out
	}
	st := img.Stride
	for by := 0; by < h; by += tamBloco {
		y1 := min(by+tamBloco, h)
		ini := -1
		for bx := 0; bx <= w; bx += tamBloco {
			mudou := false
			if bx < w {
				x1 := min(bx+tamBloco, w)
				for y := by; y < y1 && !mudou; y++ {
					o := y*st + bx*4
					if !bytes.Equal(ant[o:o+(x1-bx)*4], img.Pix[o:o+(x1-bx)*4]) {
						mudou = true
					}
				}
			}
			if mudou && ini < 0 {
				ini = bx
			} else if !mudou && ini >= 0 {
				out = append(out, image.Rect(ini, by, min(bx, w), y1).Add(img.Rect.Min))
				ini = -1
			}
		}
	}
	return out
}
