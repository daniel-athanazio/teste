# Help me! – contexto do projeto (FN Info House)

Sistema próprio de acesso remoto e suporte técnico da FN Info House (loja de informática no RJ, revenda do Uniplus).
Leia também o README.md (funções e instalação).

## Arquitetura (1.0)
- `servidor/` – Python 3.12 + FastAPI + SQLite: `app/main.py` (núcleo), `plugins.py`, `plugins_prontos.py`,
  `remoto.py` (tela remota por WebSocket, chat, pedidos de ajuda/fila, site de downloads). Painel em
  `app/static/index.html`, visualizador da tela em `remoto.html`, site em `baixar.html` (JS puro, sem build).
  Um único processo uvicorn (a retransmissão da tela fica em memória).
- `agente/` – Go 1.24 (dependências: coder/websocket e x/sys). Um único `HelpMe.exe` (modo sem instalar, serviço,
  captura, janela do cliente, instalador). `*_windows.go` = Windows; `*_outros.go` = só testes no Linux
  (tela de demonstração). Configuração (servidor, chave, código do grupo) vai no fim do .exe (`embutido.go`).
- `gestor-os/` – Flask: OS do Uniplus e chamados; rotas `/api/helpme/*` (chave = `chave_helpme`).
- `deploy/` – Docker Compose (helpme + gestor) e Nginx com WebSocket. Sem RustDesk.

## Regras que não podem quebrar
- Todo comando/mensagem ao agente é assinado com Ed25519; o agente confere assinatura, equipamento, hash, validade e repetição.
- O cliente autoriza uma vez por atendimento; só itens com 🔒 pedem nova autorização. Acesso contínuo só com aceite do cliente.
- Parâmetros de texto livre (impressora, caminho, argumentos, segredo) só entre aspas simples no PowerShell (o validador exige).
- Segredos nunca vão para banco, relatório ou log.
- A tela remota só abre em atendimento com liberação `autorizada` ou `notificado`; o cliente vê o aviso e pode encerrar.
- Textos e mensagens em português do Brasil.

## Como testar sem Windows
Veja a seção "Como testar sem Windows" do README.md (servidor, gestor e agente com tela de demonstração).

## Pendências
1. Testar no Windows 10/11 real: captura GDI, SendInput, UAC/bloqueio, SendSAS, BlockInput, instalação (nunca executados no Windows).
2. Assinatura de código OV (e como combinar com a configuração anexada ao .exe).
3. Possíveis melhorias: captura por DXGI (mais rápida), gravação da sessão, envio de arquivos pela tela, app Android.
