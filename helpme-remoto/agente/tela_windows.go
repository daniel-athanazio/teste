//go:build windows

// Captura de tela (GDI), mouse/teclado (SendInput), troca de área de trabalho (tela de bloqueio e UAC),
// Ctrl+Alt+Del (SendSAS), bloqueio de teclado/mouse do cliente (BlockInput) e área de transferência.
//
// Instalado, este código roda num processo SYSTEM aberto pelo serviço dentro da sessão do usuário:
// assim também enxerga a tela de bloqueio e as janelas do UAC (área de trabalho "Winlogon").
// No modo sem instalar roda como o usuário: a tela do UAC aparece preta para o técnico.
package main

import (
	"errors"
	"image"
	"runtime"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

var (
	user32  = syscall.NewLazyDLL("user32.dll")
	gdi32   = syscall.NewLazyDLL("gdi32.dll")
	sasDLL  = syscall.NewLazyDLL("sas.dll")
	kernelC = syscall.NewLazyDLL("kernel32.dll")

	pGetDC                    = user32.NewProc("GetDC")
	pReleaseDC                = user32.NewProc("ReleaseDC")
	pOpenInputDesktop         = user32.NewProc("OpenInputDesktop")
	pSetThreadDesktop         = user32.NewProc("SetThreadDesktop")
	pCloseDesktop             = user32.NewProc("CloseDesktop")
	pGetUserObjectInformation = user32.NewProc("GetUserObjectInformationW")
	pEnumDisplayMonitors      = user32.NewProc("EnumDisplayMonitors")
	pGetMonitorInfo           = user32.NewProc("GetMonitorInfoW")
	pGetSystemMetrics         = user32.NewProc("GetSystemMetrics")
	pSendInput                = user32.NewProc("SendInput")
	pGetCursorInfo            = user32.NewProc("GetCursorInfo")
	pLoadCursor               = user32.NewProc("LoadCursorW")
	pBlockInput               = user32.NewProc("BlockInput")
	pSetDpiCtx                = user32.NewProc("SetProcessDpiAwarenessContext")
	pSetDPIAware              = user32.NewProc("SetProcessDPIAware")
	pOpenClipboard            = user32.NewProc("OpenClipboard")
	pCloseClipboard           = user32.NewProc("CloseClipboard")
	pEmptyClipboard           = user32.NewProc("EmptyClipboard")
	pGetClipboardData         = user32.NewProc("GetClipboardData")
	pSetClipboardData         = user32.NewProc("SetClipboardData")
	pClipboardSeq             = user32.NewProc("GetClipboardSequenceNumber")
	pCreateWindowEx           = user32.NewProc("CreateWindowExW")
	pCreateCompatibleDC       = gdi32.NewProc("CreateCompatibleDC")
	pDeleteDC                 = gdi32.NewProc("DeleteDC")
	pCreateDIBSection         = gdi32.NewProc("CreateDIBSection")
	pSelectObject             = gdi32.NewProc("SelectObject")
	pDeleteObject             = gdi32.NewProc("DeleteObject")
	pBitBlt                   = gdi32.NewProc("BitBlt")
	pGdiFlush                 = gdi32.NewProc("GdiFlush")
	pSendSAS                  = sasDLL.NewProc("SendSAS")
	pGlobalAlloc              = kernelC.NewProc("GlobalAlloc")
	pGlobalFree               = kernelC.NewProc("GlobalFree")
	pGlobalLock               = kernelC.NewProc("GlobalLock")
	pGlobalUnlock             = kernelC.NewProc("GlobalUnlock")
)

const (
	srcCopy        = 0x00CC0020
	genericAll     = 0x10000000
	uoiName        = 2
	smXVirtual     = 76
	smYVirtual     = 77
	smCXVirtual    = 78
	smCYVirtual    = 79
	inputMouse     = 0
	inputKeyboard  = 1
	mMove          = 0x0001
	mAbsolute      = 0x8000
	mVirtualDesk   = 0x4000
	mWheel         = 0x0800
	mHWheel        = 0x1000
	kExtended      = 0x0001
	kKeyUp         = 0x0002
	kUnicode       = 0x0004
	kScanCode      = 0x0008
	cfUnicodeText  = 13
	gmemMoveable   = 0x0002
	cursorShowing  = 1
	dpiPerMonitor2 = ^uintptr(3) // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
)

// prepararProcesso: coordenadas em pixels reais (sem isso a captura sai cortada em telas com zoom 125%/150%).
func prepararProcesso() {
	if pSetDpiCtx.Find() == nil {
		if r, _, _ := pSetDpiCtx.Call(dpiPerMonitor2); r != 0 {
			return
		}
	}
	pSetDPIAware.Call()
}

type rect struct{ L, T, R, B int32 }

type bitmapInfo struct {
	Size          uint32
	Width, Height int32
	Planes, Bits  uint16
	Compression   uint32
	SizeImage     uint32
	XPels, YPels  int32
	ClrUsed       uint32
	ClrImportant  uint32
	Cores         [4]byte
}

type entradaMouse struct {
	Tipo  uint32
	_     uint32
	Dx    int32
	Dy    int32
	Dados uint32
	Flags uint32
	Tempo uint32
	_     uint32
	Extra uintptr
}

type entradaTecla struct {
	Tipo  uint32
	_     uint32
	Vk    uint16
	Scan  uint16
	Flags uint32
	Tempo uint32
	_     uint32
	Extra uintptr
	_     [8]byte
}

type telaWin struct {
	desk      uintptr
	deskNome  string
	ultDesk   time.Time
	mons      []Monitor
	sel       int
	area      Monitor // área capturada (coordenadas da área de trabalho virtual)
	dcTela    uintptr
	dcMem     uintptr
	bmp       uintptr
	bits      uintptr
	bw, bh    int
	img       *image.RGBA
	cursores  map[uintptr]string
	bloqueado bool
}

func novoCapturador() (capturador, error) {
	t := &telaWin{cursores: map[uintptr]string{}}
	for id, css := range map[uintptr]string{32512: "default", 32513: "text", 32514: "wait", 32515: "crosshair",
		32642: "nwse-resize", 32643: "nesw-resize", 32644: "ew-resize", 32645: "ns-resize", 32646: "move",
		32648: "not-allowed", 32649: "pointer", 32650: "progress", 32651: "help"} {
		if h, _, _ := pLoadCursor.Call(0, id); h != 0 {
			t.cursores[h] = css
		}
	}
	t.trocarDesktop()
	t.atualizarMonitores()
	t.Selecionar(0)
	return t, nil
}

// trocarDesktop acompanha a área de trabalho ativa (Default, Winlogon na tela de bloqueio/UAC).
func (t *telaWin) trocarDesktop() bool {
	h, _, _ := pOpenInputDesktop.Call(0, 0, genericAll)
	if h == 0 {
		if t.deskNome == "" {
			t.deskNome = "Default"
		}
		return false
	}
	nome := nomeObjeto(h)
	if nome == t.deskNome && t.desk != 0 {
		pCloseDesktop.Call(h)
		return false
	}
	if r, _, _ := pSetThreadDesktop.Call(h); r == 0 {
		pCloseDesktop.Call(h)
		if t.deskNome == "" {
			t.deskNome = "Default"
		}
		return false
	}
	if t.desk != 0 {
		pCloseDesktop.Call(t.desk)
	}
	t.desk, t.deskNome = h, nome
	t.liberarDC()
	return true
}

func nomeObjeto(h uintptr) string {
	buf := make([]uint16, 128)
	var n uint32
	if r, _, _ := pGetUserObjectInformation.Call(h, uoiName, uintptr(unsafe.Pointer(&buf[0])),
		uintptr(len(buf)*2), uintptr(unsafe.Pointer(&n))); r == 0 {
		return ""
	}
	return syscall.UTF16ToString(buf)
}

func (t *telaWin) Desktop() string {
	if time.Since(t.ultDesk) > 400*time.Millisecond {
		t.ultDesk = time.Now()
		if t.trocarDesktop() {
			t.atualizarMonitores()
			t.Selecionar(t.sel)
		}
	}
	return t.deskNome
}

var (
	monsTmp      []Monitor
	callbackMons = syscall.NewCallback(func(hMon, hdc, lprc, lparam uintptr) uintptr {
		var mi struct {
			Size  uint32
			Mon   rect
			Work  rect
			Flags uint32
		}
		mi.Size = uint32(unsafe.Sizeof(mi))
		if r, _, _ := pGetMonitorInfo.Call(hMon, uintptr(unsafe.Pointer(&mi))); r != 0 {
			monsTmp = append(monsTmp, Monitor{int(mi.Mon.L), int(mi.Mon.T), int(mi.Mon.R - mi.Mon.L),
				int(mi.Mon.B - mi.Mon.T), mi.Flags&1 == 1})
		}
		return 1
	})
)

func (t *telaWin) atualizarMonitores() {
	monsTmp = nil
	pEnumDisplayMonitors.Call(0, 0, callbackMons, 0)
	// o principal primeiro
	for i, m := range monsTmp {
		if m.Principal && i > 0 {
			monsTmp[0], monsTmp[i] = monsTmp[i], monsTmp[0]
		}
	}
	if len(monsTmp) == 0 {
		monsTmp = []Monitor{t.virtual()}
	}
	t.mons = monsTmp
}

func metrica(i uintptr) int {
	r, _, _ := pGetSystemMetrics.Call(i)
	return int(int32(r))
}

func (t *telaWin) virtual() Monitor {
	return Monitor{metrica(smXVirtual), metrica(smYVirtual), metrica(smCXVirtual), metrica(smCYVirtual), true}
}

func (t *telaWin) Monitores() []Monitor { return t.mons }

func (t *telaWin) Selecionar(i int) {
	t.sel = i
	if i >= 0 && i < len(t.mons) {
		t.area = t.mons[i]
	} else {
		t.area = t.virtual()
	}
	t.area.W &^= 1 // JPEG/navegador: dimensões pares evitam bordas borradas
	t.area.H &^= 1
}

func (t *telaWin) liberarDC() {
	if t.bmp != 0 {
		pDeleteObject.Call(t.bmp)
		t.bmp = 0
	}
	if t.dcMem != 0 {
		pDeleteDC.Call(t.dcMem)
		t.dcMem = 0
	}
	if t.dcTela != 0 {
		pReleaseDC.Call(0, t.dcTela)
		t.dcTela = 0
	}
}

func (t *telaWin) prepararDC(w, h int) error {
	if t.dcTela != 0 && t.bw == w && t.bh == h {
		return nil
	}
	t.liberarDC()
	t.dcTela, _, _ = pGetDC.Call(0)
	if t.dcTela == 0 {
		return errors.New("GetDC falhou")
	}
	t.dcMem, _, _ = pCreateCompatibleDC.Call(t.dcTela)
	bi := bitmapInfo{Size: 40, Width: int32(w), Height: -int32(h), Planes: 1, Bits: 32}
	t.bmp, _, _ = pCreateDIBSection.Call(t.dcMem, uintptr(unsafe.Pointer(&bi)), 0, uintptr(unsafe.Pointer(&t.bits)), 0, 0)
	if t.bmp == 0 || t.bits == 0 {
		t.liberarDC()
		return errors.New("CreateDIBSection falhou")
	}
	pSelectObject.Call(t.dcMem, t.bmp)
	t.bw, t.bh = w, h
	t.img = image.NewRGBA(image.Rect(0, 0, w, h))
	return nil
}

func (t *telaWin) Capturar() (*image.RGBA, error) {
	a := t.area
	if a.W <= 0 || a.H <= 0 {
		t.atualizarMonitores()
		t.Selecionar(t.sel)
		return nil, errors.New("monitor indisponível")
	}
	if err := t.prepararDC(a.W, a.H); err != nil {
		return nil, err
	}
	if r, _, _ := pBitBlt.Call(t.dcMem, 0, 0, uintptr(a.W), uintptr(a.H), t.dcTela, uintptr(a.X), uintptr(a.Y), srcCopy); r == 0 {
		t.liberarDC() // a área de trabalho mudou (bloqueio/UAC): refaz na próxima
		t.ultDesk = time.Time{}
		return nil, errors.New("BitBlt falhou")
	}
	pGdiFlush.Call()
	src := unsafe.Slice((*byte)(unsafe.Pointer(t.bits)), a.W*a.H*4)
	dst := t.img.Pix
	for i := 0; i < len(src); i += 4 { // BGRA → RGBA
		dst[i], dst[i+1], dst[i+2], dst[i+3] = src[i+2], src[i+1], src[i], 255
	}
	return t.img, nil
}

func (t *telaWin) Cursor() (int, int, string, bool) {
	var ci struct {
		Size   uint32
		Flags  uint32
		Cursor uintptr
		X, Y   int32
	}
	ci.Size = uint32(unsafe.Sizeof(ci))
	if r, _, _ := pGetCursorInfo.Call(uintptr(unsafe.Pointer(&ci))); r == 0 {
		return 0, 0, "default", false
	}
	forma := t.cursores[ci.Cursor]
	if forma == "" {
		forma = "default"
	}
	return int(ci.X) - t.area.X, int(ci.Y) - t.area.Y, forma, ci.Flags&cursorShowing != 0
}

func enviarEntradas(p unsafe.Pointer, n int, tam uintptr) {
	pSendInput.Call(uintptr(n), uintptr(p), tam)
}

func (t *telaWin) Mouse(acao string, botao, x, y int) {
	v := t.virtual()
	if v.W < 2 || v.H < 2 {
		return
	}
	ax := (t.area.X + x - v.X) * 65535 / (v.W - 1)
	ay := (t.area.Y + y - v.Y) * 65535 / (v.H - 1)
	e := entradaMouse{Tipo: inputMouse, Dx: int32(ax), Dy: int32(ay), Flags: mMove | mAbsolute | mVirtualDesk}
	if acao != "mover" {
		// botões do navegador: 0 esquerdo, 1 meio, 2 direito → {pressionar, soltar}
		f := map[int][2]uint32{0: {0x2, 0x4}, 1: {0x20, 0x40}, 2: {0x8, 0x10}}[botao]
		if acao == "baixo" {
			e.Flags |= f[0]
		} else {
			e.Flags |= f[1]
		}
	}
	enviarEntradas(unsafe.Pointer(&e), 1, unsafe.Sizeof(e))
}

func (t *telaWin) Roda(dx, dy int) {
	if dy != 0 {
		e := entradaMouse{Tipo: inputMouse, Flags: mWheel, Dados: uint32(int32(-dy))}
		enviarEntradas(unsafe.Pointer(&e), 1, unsafe.Sizeof(e))
	}
	if dx != 0 {
		e := entradaMouse{Tipo: inputMouse, Flags: mHWheel, Dados: uint32(int32(dx))}
		enviarEntradas(unsafe.Pointer(&e), 1, unsafe.Sizeof(e))
	}
}

// Tecla por scancode: funciona com o layout do cliente (ABNT2 inclusive), independente do teclado do técnico.
func (t *telaWin) Tecla(scan uint16, estendida, solta bool) {
	e := entradaTecla{Tipo: inputKeyboard, Scan: scan, Flags: kScanCode}
	if estendida {
		e.Flags |= kExtended
	}
	if solta {
		e.Flags |= kKeyUp
	}
	enviarEntradas(unsafe.Pointer(&e), 1, unsafe.Sizeof(e))
}

// Texto digita caractere a caractere (Unicode), útil para colar senhas e textos longos.
func (t *telaWin) Texto(s string) {
	for _, u := range utf16.Encode([]rune(s)) {
		if u == '\r' {
			continue
		}
		if u == '\n' {
			t.Tecla(0x1C, false, false)
			t.Tecla(0x1C, false, true)
			continue
		}
		ev := [2]entradaTecla{{Tipo: inputKeyboard, Scan: u, Flags: kUnicode},
			{Tipo: inputKeyboard, Scan: u, Flags: kUnicode | kKeyUp}}
		enviarEntradas(unsafe.Pointer(&ev[0]), 2, unsafe.Sizeof(ev[0]))
		time.Sleep(2 * time.Millisecond)
	}
}

func (t *telaWin) CtrlAltDel() error {
	if err := pSendSAS.Find(); err != nil {
		return errors.New("indisponível neste Windows")
	}
	pSendSAS.Call(0)
	return nil
}

func (t *telaWin) Bloquear(on bool) error {
	v := uintptr(0)
	if on {
		v = 1
	}
	if r, _, err := pBlockInput.Call(v); r == 0 {
		if modoPortatil {
			return errors.New("só disponível com o " + tituloApp + " instalado")
		}
		return err
	}
	t.bloqueado = on
	return nil
}

func (t *telaWin) Fechar() {
	if t.bloqueado {
		pBlockInput.Call(0)
	}
	t.liberarDC()
	if t.desk != 0 {
		pCloseDesktop.Call(t.desk)
	}
}

// ------------------------------------------------------------------ área de transferência
// Roda numa thread própria com uma janela invisível (exigência do SetClipboardData);
// a thread da captura não pode ter janelas, senão não consegue trocar de área de trabalho.
type clipboardWin struct {
	pedidos chan func(hwnd uintptr)
	seq     uintptr
	ultimo  string
}

func novoClipboard() *clipboardWin {
	c := &clipboardWin{pedidos: make(chan func(uintptr), 8)}
	c.seq, _, _ = pClipboardSeq.Call()
	go func() {
		runtime.LockOSThread()
		cls, _ := syscall.UTF16PtrFromString("STATIC")
		hwnd, _, _ := pCreateWindowEx.Call(0, uintptr(unsafe.Pointer(cls)), 0, 0, 0, 0, 0, 0, ^uintptr(2), 0, 0, 0)
		for f := range c.pedidos {
			f(hwnd)
		}
	}()
	return c
}

func abrirClipboard(hwnd uintptr) bool {
	for i := 0; i < 10; i++ {
		if r, _, _ := pOpenClipboard.Call(hwnd); r != 0 {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func (c *clipboardWin) escrever(s string) {
	u, err := syscall.UTF16FromString(s)
	if err != nil {
		return
	}
	pronto := make(chan struct{})
	c.pedidos <- func(hwnd uintptr) {
		defer close(pronto)
		if !abrirClipboard(hwnd) {
			return
		}
		defer pCloseClipboard.Call()
		pEmptyClipboard.Call()
		h, _, _ := pGlobalAlloc.Call(gmemMoveable, uintptr(len(u)*2))
		if h == 0 {
			return
		}
		p, _, _ := pGlobalLock.Call(h)
		copy(unsafe.Slice((*uint16)(unsafe.Pointer(p)), len(u)), u)
		pGlobalUnlock.Call(h)
		if r, _, _ := pSetClipboardData.Call(cfUnicodeText, h); r == 0 {
			pGlobalFree.Call(h)
		}
	}
	<-pronto
	c.seq, _, _ = pClipboardSeq.Call() // não devolve ao técnico o que ele mesmo mandou
	c.ultimo = s
}

// novo devolve o texto copiado no computador do cliente desde a última consulta.
func (c *clipboardWin) novo() (string, bool) {
	seq, _, _ := pClipboardSeq.Call()
	if seq == c.seq {
		return "", false
	}
	c.seq = seq
	var txt string
	pronto := make(chan struct{})
	c.pedidos <- func(hwnd uintptr) {
		defer close(pronto)
		if !abrirClipboard(hwnd) {
			return
		}
		defer pCloseClipboard.Call()
		h, _, _ := pGetClipboardData.Call(cfUnicodeText)
		if h == 0 {
			return
		}
		p, _, _ := pGlobalLock.Call(h)
		if p == 0 {
			return
		}
		defer pGlobalUnlock.Call(h)
		buf := unsafe.Slice((*uint16)(unsafe.Pointer(p)), 1<<20)
		n := 0
		for n < len(buf) && buf[n] != 0 {
			n++
		}
		txt = string(utf16.Decode(buf[:n]))
	}
	<-pronto
	if txt == "" || txt == c.ultimo || len(txt) > 200_000 {
		return "", false
	}
	c.ultimo = txt
	return txt, true
}

func (c *clipboardWin) fechar() { close(c.pedidos) }
