# Help me! 1.0 – acesso remoto próprio + gestor de OS (FN Info House)

Sistema de suporte remoto no estilo AnyDesk, sem mensalidade e sem depender de RustDesk, integrado ao gestor de OS:

| Pasta | O que é |
|---|---|
| `agente/` | **HelpMe.exe**: um único executável para o cliente (Go, Windows 10/11). Gera o número de acesso, mostra a janela do cliente, transmite a tela, recebe mouse/teclado, roda scripts/plugins e instala o serviço. |
| `servidor/` | Servidor (Python/FastAPI): painel dos técnicos, **tela remota no navegador**, fila de atendimento, chat, relatórios, plugins e **site de downloads** (`/baixar`). |
| `gestor-os/` | Gestor de OS (Flask): OS do Uniplus e **chamados**. Faz a triagem: os pedidos de ajuda viram chamados e o atendimento remoto atualiza o chamado no fim. |
| `deploy/` | Docker Compose (servidor + gestor) e Nginx com WebSocket para o VPS. |

## Como o cliente usa
1. Você manda o link do site de downloads: `https://suporte.fninfohouse.com.br/baixar`. Também há um link por empresa: `/baixar/CODIGO`, que fica no painel, no grupo, com o botão **Enviar no WhatsApp**.
2. O cliente baixa o **HelpMe.exe** e dá dois cliques. **Não precisa instalar.** Na hora aparece a janela com o **número de acesso** (ex.: `469 977 979`).
3. O cliente passa o número ou clica em **Pedir ajuda** na própria janela. O pedido entra na aba **Fila** do painel e vira um chamado no gestor de OS.
4. Você clica em **Atender**. O cliente autoriza na tela dele. Então você clica em **🖥 Abrir tela remota**.
5. Durante o acesso, a janela do cliente mostra um aviso vermelho: *"Fulano está vendo e controlando a sua tela"*. Ao lado ficam o botão **Encerrar** e o chat.
6. Na janela, o botão **Instalar para acesso permanente** instala o programa no computador. Ele continua com o mesmo número e passa a funcionar como serviço do Windows: atende com a tela bloqueada e mostra as janelas do UAC. O acesso contínuo fica disponível, sempre com o aceite do cliente.

## O que faz (e por que é melhor que o AnyDesk para a loja)
- **O técnico não instala nada.** A tela abre no navegador (Chrome/Edge), com estes recursos:
  - mouse e teclado por *scancode*, que funciona com o teclado ABNT2 do cliente;
  - Ctrl+Alt+Del e atalhos (Win, Alt+Tab, Gerenciador de tarefas, Win+R…);
  - tela cheia com captura de Alt+Tab e da tecla Windows;
  - vários monitores;
  - qualidade Rápida, Normal ou Alta;
  - "Só ver";
  - captura de imagem;
  - área de transferência nos dois sentidos e "Digitar um texto no cliente";
  - **bloquear teclado e mouse do cliente**;
  - vários técnicos na mesma tela.
- **Transmissão eficiente.** Só os blocos 64×64 que mudaram são enviados, em JPEG, com controle de fluxo. Em internet lenta a taxa de quadros cai sozinha, sem acumular atraso.
- **LGPD de verdade:**
  - o cliente autoriza cada atendimento ou aceita o acesso contínuo;
  - vê o aviso o tempo todo e encerra com um clique;
  - todo comando ao computador é assinado (Ed25519);
  - o relatório registra cada acesso à tela (técnico, início, fim e quem encerrou), a conversa, os scripts e os plugins.
- **Triagem integrada ao gestor de OS:**
  - Pedir ajuda → chamado no gestor (com aviso por WhatsApp, se configurado);
  - Atender → o chamado vai para "Em atendimento";
  - Encerrar → o chamado fica "Concluído", com a solução e o relatório anexado.
  - O painel mostra os pedidos de ajuda e os chamados abertos do gestor na aba **Fila**, com som e notificação a cada pedido novo.
- **Tudo da versão 0.7 continua**:
  - grupos e técnicos;
  - scripts e plugins (balança, impressoras, biblioteca de drivers, Uniplus assistido, certificado A1);
  - alertas e análise de falha por IA;
  - tela dividida A/B;
  - tipos de problema e marca configurável.

## Instalação no VPS – jeito fácil (um comando)
1. No painel da Hostinger (DNS de fninfohouse.com.br), crie dois registros **A**, `suporte` e `os`, com o IP do VPS.
2. Envie o `helpme-remoto.zip` para o VPS (no terminal do navegador da Hostinger, botão de upload, ou por WinSCP).
3. No terminal do VPS:
   ```bash
   apt install -y unzip && unzip -o helpme-remoto.zip -d /root/ && cd /root/helpme-remoto/deploy
   bash instalar-vps.sh suporte.fninfohouse.com.br os.fninfohouse.com.br SEU-EMAIL
   ```
   O script instala tudo (Docker, Nginx, HTTPS), compila o HelpMe.exe, liga a integração com o gestor e mostra
   no final as senhas e os endereços. Para atualizar depois, envie o zip novo e rode o mesmo comando.

## Instalação no VPS – passo a passo manual
1. No DNS (Registro.br/Hostinger), crie os registros **A** `suporte` e `os` apontando para o IP do VPS.
2. No firewall do VPS, libere apenas as portas 80 e 443. Diferente do RustDesk, não há mais portas 211xx.
3. Envie a pasta `helpme-remoto` para o VPS e rode:
   ```bash
   cd helpme-remoto/deploy && cp .env.exemplo .env && cp gestor.env.exemplo gestor.env
   nano .env          # domínio, senha do admin, contato
   nano gestor.env    # senha do painel, SEGREDO, URL_PUBLICA=https://os.fninfohouse.com.br, Uniplus, WhatsApp
   docker compose up -d --build
   sudo cp nginx-helpme.conf /etc/nginx/sites-available/helpme
   sudo ln -s /etc/nginx/sites-available/helpme /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d suporte.fninfohouse.com.br -d os.fninfohouse.com.br
   ```
   O `docker compose` já **compila o HelpMe.exe** dentro da imagem (estágio Go do `servidor/Dockerfile`). Não é preciso compilar nada no seu computador.
4. Entre em `https://suporte.fninfohouse.com.br` com o admin do `.env` e cadastre os técnicos.
5. Ligue a integração:
   - No gestor, abra **Configuração › Integração com o Help me!** e copie a chave.
   - No Help me!, abra **Configurações › Integração com o gestor de OS**, informe `https://os.fninfohouse.com.br` e a chave, e clique em **Testar**.
6. Pronto. O site de downloads é `https://suporte.fninfohouse.com.br/baixar`.

> O `.exe` baixado já vem configurado com o endereço do servidor, a chave pública de assinatura e, no link por empresa, o código do grupo. Esses dados vão no final do arquivo (`agente/embutido.go`). Por isso um único build serve a todos.

## Antes da distribuição em massa
- **Assinatura de código (certificado OV/EV)**: sem ela, o Windows SmartScreen mostra "O Windows protegeu o computador" (o cliente clica em *Mais informações › Executar assim mesmo*). A assinatura tem um detalhe: o site acrescenta a configuração ao final do .exe, e isso invalida uma assinatura Authenticode comum. Ao assinar, a configuração precisa ir para o espaço reservado do certificado, ou o site passa a servir um .exe já assinado por empresa. Isso é tarefa para quando houver o certificado.
- **Teste num Windows 10/11 real.** A captura (GDI), o SendInput, a troca de área de trabalho (UAC e bloqueio), o SendSAS e a instalação foram escritos para o Windows. Eles compilam, mas só a versão de teste para Linux foi executada (veja abaixo). Confira nesta ordem:
  1. Rodar sem instalar.
  2. Instalar e abrir a tela.
  3. UAC.
  4. Tela bloqueada.
  5. Ctrl+Alt+Del.
  6. Bloquear o cliente.
  7. Dois monitores.
  8. Área de transferência.
  9. Desinstalar.

## Compilar o HelpMe.exe à mão (opcional)
```bash
cd agente && ./compilar.sh            # gera HelpMe.exe (Go 1.24+; com go-winres sai com ícone)
```
O GitHub Actions (`.github/workflows/helpme-agente.yml`) também compila a cada alteração do agente. O arquivo fica em *Actions › execução › Artifacts*. Para servir um .exe compilado fora do Docker, aponte `FN_AGENTE_EXE` para ele.

## Como testar sem Windows
```bash
# servidor e gestor
cd servidor && pip install fastapi "uvicorn[standard]" cryptography httpx
FN_DATA_DIR=/tmp/hm FN_ADMIN_SENHA=Teste12345 FN_AGENTE_EXE=/tmp/HelpMe.exe uvicorn app.main:app --port 8010
cd gestor-os && pip install -r requirements.txt && BANCO=/tmp/g.db SEGREDO=teste python app.py   # porta 8000
# agente com tela de demonstração (desenha um quadro, marca cliques e conta teclas)
cd agente && go build -o /tmp/hm .
export FN_AGENTE_DIR=/tmp/ag FN_PERMITIR_HTTP=1 FN_TESTE_AUTORIZA=1 FN_TESTE_USUARIO=maria FN_SERVIDOR=http://127.0.0.1:8010
/tmp/hm registrar -silencioso && /tmp/hm executar      # ou só /tmp/hm para o modo sem instalar
```
No painel (`http://127.0.0.1:8010`):
1. vincule o número;
2. clique em **Atender**;
3. clique em **🖥 Abrir tela remota**.

A janela do cliente sai no log como `janela do cliente em http://127.0.0.1:PORTA/?k=...`. Abra esse endereço no navegador.

## Comandos do HelpMe.exe
| Comando | Uso |
|---|---|
| (duplo clique) | abre a janela; sem instalação, roda no modo "sem instalar" |
| `instalar` / `desinstalar` | pede administrador; mantém o número do modo sem instalar |
| `executar` | serviço (tarefa agendada SYSTEM "Help me Agente") |
| `tela -canal X` | captura da tela (aberto pelo serviço na sessão do usuário) |
| `janela`, `numero`, `revogar`, `status`, `registrar` | auxiliares |

## Arquitetura da tela remota
```
navegador do técnico ──WebSocket── servidor (/ws/remoto/tecnico ↔ /ws/remoto/agente) ──WebSocket── HelpMe.exe tela
                                     │  só abre dentro de atendimento liberado pelo cliente
serviço HelpMe.exe executar ──/ws/agente (acordar) + heartbeat com comandos assinados (remoto, remoto_fim…)
janela do cliente (Edge --app, 127.0.0.1) ⇄ arquivos publico/estado.json e pedidos/*.json ⇄ serviço
```
