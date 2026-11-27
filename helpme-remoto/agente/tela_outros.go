//go:build !windows

// Tela de demonstração para testar o acesso remoto fora do Windows: desenha um quadro que muda sozinho,
// marca os cliques recebidos e conta as teclas (assim dá para conferir que mouse e teclado chegam).
package main

import (
	"image"
	"image/color"
	"log"
	"time"
)

type telaTeste struct {
	img     *image.RGBA
	ini     time.Time
	cliques []image.Point
	teclas  int
	mx, my  int
}

func novoCapturador() (capturador, error) {
	return &telaTeste{img: image.NewRGBA(image.Rect(0, 0, 1280, 720)), ini: time.Now()}, nil
}

func (t *telaTeste) Monitores() []Monitor {
	return []Monitor{{0, 0, 1280, 720, true}}
}
func (t *telaTeste) Selecionar(int)  {}
func (t *telaTeste) Desktop() string { return "Default" }
func (t *telaTeste) Fechar()         {}

func (t *telaTeste) preencher(r image.Rectangle, c color.RGBA) {
	r = r.Intersect(t.img.Rect)
	for y := r.Min.Y; y < r.Max.Y; y++ {
		for x := r.Min.X; x < r.Max.X; x++ {
			o := y*t.img.Stride + x*4
			t.img.Pix[o], t.img.Pix[o+1], t.img.Pix[o+2], t.img.Pix[o+3] = c.R, c.G, c.B, 255
		}
	}
}

func (t *telaTeste) Capturar() (*image.RGBA, error) {
	// fundo fixo (área de trabalho), barra de tarefas e uma "janela"
	t.preencher(t.img.Rect, color.RGBA{24, 90, 160, 255})
	t.preencher(image.Rect(0, 680, 1280, 720), color.RGBA{30, 30, 36, 255})
	t.preencher(image.Rect(200, 120, 900, 520), color.RGBA{245, 245, 245, 255})
	t.preencher(image.Rect(200, 120, 900, 150), color.RGBA{46, 184, 114, 255})
	// relógio: barra que avança a cada segundo
	s := int(time.Since(t.ini).Seconds()) % 60
	t.preencher(image.Rect(220, 170, 220+s*11, 190), color.RGBA{232, 77, 77, 255})
	// teclas recebidas
	for i := 0; i < t.teclas && i < 60; i++ {
		t.preencher(image.Rect(220+i*11, 210, 228+i*11, 230), color.RGBA{40, 40, 40, 255})
	}
	// cliques recebidos
	for _, p := range t.cliques {
		t.preencher(image.Rect(p.X-5, p.Y-5, p.X+5, p.Y+5), color.RGBA{255, 200, 0, 255})
	}
	return t.img, nil
}

func (t *telaTeste) Cursor() (int, int, string, bool) { return t.mx, t.my, "default", true }

func (t *telaTeste) Mouse(acao string, botao, x, y int) {
	t.mx, t.my = x, y
	if acao == "baixo" {
		t.cliques = append(t.cliques, image.Pt(x, y))
		log.Printf("[teste] clique botão %d em %d,%d", botao, x, y)
	}
}
func (t *telaTeste) Roda(dx, dy int) { log.Printf("[teste] roda %d,%d", dx, dy) }
func (t *telaTeste) Tecla(sc uint16, ext, solta bool) {
	if !solta {
		t.teclas++
		log.Printf("[teste] tecla scancode 0x%02X estendida=%v", sc, ext)
	}
}
func (t *telaTeste) Texto(s string)         { t.teclas += len([]rune(s)); log.Printf("[teste] texto %q", s) }
func (t *telaTeste) CtrlAltDel() error      { log.Print("[teste] Ctrl+Alt+Del"); return nil }
func (t *telaTeste) Bloquear(on bool) error { log.Printf("[teste] bloquear=%v", on); return nil }

type clipboardTeste struct {
	texto string
	novoT bool
}

func novoClipboard() *clipboardTeste { return &clipboardTeste{} }
func (c *clipboardTeste) escrever(s string) {
	log.Printf("[teste] área de transferência recebida: %q", s)
	c.texto = s
}
func (c *clipboardTeste) novo() (string, bool) { return "", false }
func (c *clipboardTeste) fechar()              {}
