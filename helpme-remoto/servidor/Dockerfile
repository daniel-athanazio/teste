# Contexto de build: a pasta helpme-remoto (veja deploy/docker-compose.yml).
# 1) compila o HelpMe.exe para Windows  2) servidor Python que entrega o .exe pelo site de downloads
FROM golang:1.24 AS agente
WORKDIR /src
COPY agente/go.mod agente/go.sum ./
RUN go mod download
COPY agente/ ./
RUN (go install github.com/tc-hib/go-winres@v0.3.3 && go-winres make --in winres/winres.json --arch amd64) || true
RUN GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -H windowsgui" -o /out/HelpMe.exe .

FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir fastapi==0.115.* "uvicorn[standard]" cryptography httpx
COPY servidor/app ./app
COPY --from=agente /out/HelpMe.exe /srv/agente/HelpMe.exe
ENV FN_DATA_DIR=/data FN_AGENTE_EXE=/srv/agente/HelpMe.exe
VOLUME /data
EXPOSE 8000
# um único processo: a retransmissão da tela fica em memória
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
