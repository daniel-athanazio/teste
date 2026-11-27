# Gestor de OS — Uniplus

Painel de ordens de serviço que lê as OS do Uniplus Web, alerta as fora do prazo, oferece consulta ao cliente por QR code,
pesquisa de satisfação e avisos por WhatsApp (API oficial).

## Publicar no VPS (Ubuntu 24.04)
1. No painel da Hostinger (DNS do fninfohouse.com.br), crie um registro **A**: nome `os`, valor = IP do VPS.
2. Envie o `gestor-os.zip` para o VPS e, como root:
   ```
   apt install -y unzip && unzip gestor-os.zip -d /root/ && cd /root/gestor-os
   bash deploy/instalar.sh os.fninfohouse.com.br SEU-EMAIL
   ```
   O script mostra no final a senha do painel e a chave do telão.
3. Ligar no Uniplus: entre no painel, abra a aba **Configuração**, informe o servidor e o código de autorização
   (Ferramentas > Configuração da API) e salve. O teste de conexão aparece na própria tela.

Se o servidor já usa Caddy nas portas 80/443, o instalador acrescenta o site no Caddy em vez de instalar o nginx.

Atualizar depois: envie o novo zip, descompacte e rode o `instalar.sh` de novo (o `.env` e o banco são preservados).

## Endereços
| Endereço | Uso |
|---|---|
| `/painel` | Painel (senha) |
| `/painel?tv=1&chave=CHAVE_TV` | Telão: sem login, atualiza a cada 60 s |
| `/terceirizada` | Equipamentos na terceirizada, com semáforo verde/amarelo/vermelho |
| `/chamados` | Chamados de suporte ao sistema (independentes do Uniplus) |
| `/chamados?tv=1&chave=CHAVE_TV` | Telão dos chamados |
| `/ch/...` | Acompanhamento e avaliação do chamado (link enviado ao cliente) |
| `/config` | Configuração (Uniplus, senha, prazos, WhatsApp); pede a senha atual para salvar |
| `/c/NUMERO-DA-OS` | Consulta do cliente (QR da OS). Pede os 4 últimos dígitos do telefone |
| `/avaliar/...` | Pesquisa de satisfação (link enviado ao finalizar) |
| `/api/os.json` | Dados para outros agentes (voz, WhatsApp) |
| `/saude` | Verificação do serviço |

**QR na OS impressa:** o conteúdo do QR é `https://os.fninfohouse.com.br/c/` + número da OS.

## Regras (arquivo .env)
- `PRAZO_PADRAO_DIAS` e `PRAZO_VISITA_DIAS`: usados quando a OS não tem previsão de conclusão no Uniplus.
- `META_MES`: meta de serviços do mês (R$).
- Colunas do painel: o status do Uniplus é encaixado por palavras-chave em `config.py` (`SITUACOES`).
- WhatsApp: preencha `WA_TOKEN` e `WA_PHONE_ID` e crie na Meta os 3 modelos (variáveis: nome, nº da OS, link).
  Avisa em: aguardando aprovação, pronta para retirada e finalizada (com link da avaliação). A primeira sincronização nunca envia avisos.

## Chamados de suporte
- Fluxo: Aberto → Em atendimento ⇄ Aguardando cliente → Concluído (ou Cancelado).
- Tempos por chamado: espera, atendimento (sem contar o tempo aguardando o cliente), aguardando e total.
- Alerta vermelho: aberto sem atendimento há mais de `ALERTA_CHAMADO_MIN` minutos, ou aguardando o cliente há mais de `ALERTA_AGUARDANDO_HORAS` horas.
- A cada etapa o cliente é avisado pelo WhatsApp (modelos `WA_MODELO_CHAMADO_*`, variáveis: nome, nº do chamado, link).
  Com a API desligada, a tela mostra a mensagem pronta com o botão "Abrir no WhatsApp".
- Ao concluir, o link leva o cliente à avaliação (nota de 1 a 5 e comentário).
