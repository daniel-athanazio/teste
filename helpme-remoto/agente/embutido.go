package main

import (
	"encoding/binary"
	"encoding/json"
	"os"
)

// Configuração embutida no final do HelpMe.exe pelo site de downloads do servidor:
//
//	<exe original><JSON><tamanho do JSON: uint32 little-endian><"HELPMECFG1">
//
// O Windows ignora bytes depois do executável, então o mesmo .exe compilado uma vez serve para
// qualquer servidor e para o link de cada empresa (código de ativação), sem recompilar.
const marcaEmbutido = "HELPMECFG1"

type Embutido struct {
	Servidor     string `json:"servidor"`
	ChavePublica string `json:"chave_publica"`
	Codigo       string `json:"codigo"`
	Marca        string `json:"marca"`
	Empresa      string `json:"empresa"`
	Contato      string `json:"contato"`
}

var emb Embutido

func lerEmbutido() Embutido {
	e := Embutido{}
	if exe, err := os.Executable(); err == nil {
		e = lerEmbutidoDe(exe)
	}
	if e.Servidor == "" {
		e.Servidor = servidorPadrao
	}
	if e.ChavePublica == "" {
		e.ChavePublica = chavePublica
	}
	if s := os.Getenv("FN_SERVIDOR"); s != "" { // testes
		e.Servidor = s
	}
	return e
}

func lerEmbutidoDe(caminho string) Embutido {
	var e Embutido
	f, err := os.Open(caminho)
	if err != nil {
		return e
	}
	defer f.Close()
	st, err := f.Stat()
	n := int64(len(marcaEmbutido) + 4)
	if err != nil || st.Size() < n {
		return e
	}
	fim := make([]byte, n)
	if _, err := f.ReadAt(fim, st.Size()-n); err != nil || string(fim[4:]) != marcaEmbutido {
		return e
	}
	tam := int64(binary.LittleEndian.Uint32(fim[:4]))
	if tam <= 0 || tam > 64<<10 || tam > st.Size()-n {
		return e
	}
	b := make([]byte, tam)
	if _, err := f.ReadAt(b, st.Size()-n-tam); err == nil {
		_ = json.Unmarshal(b, &e)
	}
	return e
}
