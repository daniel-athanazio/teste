#!/bin/sh
# Compila o HelpMe.exe (Windows 10/11, 64 bits).
#
# Não é preciso compilar um .exe por servidor nem por empresa: o site de downloads do servidor
# (/baixar) acrescenta o endereço, a chave pública e o código da empresa ao entregar o arquivo.
#
# Uso:  ./compilar.sh                        -> gera HelpMe.exe nesta pasta
#       ./compilar.sh /caminho/HelpMe.exe    -> gera no caminho informado
# Opcional: com o go-winres instalado (go install github.com/tc-hib/go-winres@v0.3.3),
# o .exe sai com ícone, versão e manifesto do Windows.
set -e
cd "$(dirname "$0")"
SAIDA=${1:-HelpMe.exe}
if command -v go-winres >/dev/null 2>&1; then
  go-winres make --in winres/winres.json --arch amd64
fi
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -H windowsgui" -o "$SAIDA" .
echo "Pronto: $SAIDA ($(du -h "$SAIDA" | cut -f1))"
