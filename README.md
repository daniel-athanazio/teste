# 📺 Painel de Anúncios

Aplicação para exibir **anúncios em imagem e vídeo** no navegador de uma **Smart TV ou TV Box**.
Fica instalada em um computador da loja/escritório, e os anúncios podem ser enviados **pelo celular**
escaneando um **QR code** (celular e computador na mesma rede Wi-Fi).

## Recursos
- Imagens (JPG, PNG, GIF, WebP) e vídeos (MP4, WebM, MOV)
- **Anúncios de texto**, com cor de fundo, cor da letra e tamanho (a letra diminui sozinha para caber na tela)
- **Posts do Instagram e do Facebook** entram sozinhos na TV, com a legenda e o selo da rede social
- **Escrever e postar:** o mesmo texto/foto/vídeo vai para a TV e, se marcar, é postado no Facebook, no Instagram e no WhatsApp
- **Anúncios pelo WhatsApp:** mande texto, foto ou vídeo para o número do painel e aparece na TV
- **TV na horizontal ou na vertical** (gira a imagem para TVs penduradas em pé)
- **Data/hora de início e fim** de cada anúncio (agendamento automático)
- **Duração na tela** por anúncio (imagens usam um tempo padrão; vídeos tocam inteiros se a duração ficar vazia)
- Pausar/ativar, editar, reordenar e excluir anúncios
- QR code para **enviar** pelo celular e QR code para **gerenciar** pelo celular
- Player da TV em tela cheia, com transição suave, que se atualiza sozinho a cada 20 s
- Compatível com navegadores antigos de TV (sem frameworks), sem internet: tudo roda na rede local
- Envio protegido por um código de acesso (vai embutido no QR code)

## Instalação (Windows)
1. Instale o **Node.js LTS**: https://nodejs.org
2. Copie esta pasta para o computador (ex.: `C:\PainelAnuncios`).
3. Dê dois cliques em **`iniciar.bat`**. Na primeira vez ele instala as dependências.
   O painel abre no navegador em `http://localhost:8080/admin`.
4. (Opcional) Dê dois cliques em **`instalar-inicio-automatico.bat`** para o painel iniciar junto com o Windows.

> Se o Windows perguntar sobre o Firewall, clique em **Permitir acesso** (rede privada) — sem isso a TV e o celular não conseguem acessar.

## Instalação (Linux / macOS)
```sh
./iniciar.sh
```

## Como usar
1. **No computador**: abra `http://localhost:8080/admin`.
2. **Na TV / TV Box**: abra o navegador e digite o endereço mostrado no painel em “Exibir na TV”
   (algo como `http://192.168.0.10:8080/tv`). Aperte **OK** no controle para tela cheia.
3. **No celular**: escaneie o QR code “Enviar pelo celular”, escolha fotos/vídeos, defina datas e duração e toque em **Enviar**.
   O anúncio aparece na TV em até 20 segundos.

### TV na vertical
Em **Exibir na TV → Posição da TV**, escolha *Vertical*. Se a imagem ficar de cabeça para baixo, escolha a outra opção de vertical.
A TV muda sozinha em até 20 s. Para ter TVs em posições diferentes, use o endereço com `?orientacao=vertical`,
`?orientacao=vertical-esq` ou `?orientacao=horizontal` em cada uma (tem prioridade sobre o painel).

### Dicas
- **Som nos vídeos:** por padrão os vídeos tocam sem som (os navegadores de TV bloqueiam autoplay com som).
  Para tentar com som, use `http://IP:8080/tv?som=1`.
- **Endereço fixo:** configure no roteador um IP fixo (reserva DHCP) para o computador, assim o endereço da TV não muda.
- **Vídeos:** prefira MP4 (H.264), que funciona em praticamente todas as TVs.
- **Porta diferente:** `PORT=9000 node server.js` (ou `set PORT=9000` no Windows antes de iniciar).
- Os arquivos e a configuração ficam na pasta `data/` (faça backup dela). O código de acesso está em `data/config.json`;
  apague o campo `token` e reinicie para gerar um novo (os QR codes antigos param de funcionar).

## Redes sociais
No painel, seção **Redes sociais** (o computador precisa estar conectado à internet):

| Rede | Funciona? | O que precisa |
|---|---|---|
| **Instagram** | ✅ Posts do feed (fotos, vídeos/Reels, 1ª foto do carrossel) | Conta **Profissional** (Empresa ou Criador) e um token de acesso gerado em developers.facebook.com. O painel renova o token sozinho. |
| **Facebook** | ✅ Posts de uma **Página** (texto, foto, vídeo) | ID da Página e um token de acesso da Página (permissão `pages_read_engagement`). Perfis pessoais não são suportados pela Meta. |
| **WhatsApp** | ✅ Mensagens enviadas ao número do painel | Um número com a **API do WhatsApp Business** (Cloud API) e o endereço público. Os Status não podem ser lidos por nenhum programa — encaminhe a foto/vídeo para o número do painel. |

O passo a passo para conseguir cada token aparece no próprio painel (“Como conseguir o token”).

- O painel verifica posts novos a cada 10 minutos (ou clique em **Sincronizar agora**).
- Cada post fica no ar por um número de dias configurável e depois sai sozinho.
- Um post importado que você excluir não volta.
- Os Stories do Instagram **não** são importados, só os posts do feed.
- Os tokens ficam salvos só no computador, em `data/config.json`.

### Postar nas redes pelo painel
No formulário **Novo anúncio / post** marque onde postar também:

| Rede | Como funciona |
|---|---|
| **Facebook** | Posta texto, foto ou vídeo na Página. O token da Página precisa da permissão `pages_manage_posts`. |
| **Instagram** | Posta foto ou Reels. Texto puro vira uma imagem quadrada com as cores escolhidas. Precisa da permissão `instagram_business_content_publish` e do **endereço público** (o Instagram baixa a mídia pela internet). Use fotos JPG. |
| **WhatsApp** | Abre o WhatsApp com o texto pronto para você escolher contatos, grupos ou o Status (o WhatsApp não permite postar sozinho). |

Com vários arquivos, só o primeiro é postado nas redes. O que foi postado pelo painel não é importado de novo pela sincronização.

### Endereço público (túnel)
O Instagram e o WhatsApp precisam alcançar o painel pela internet. O jeito mais simples é o **cloudflared** (grátis):
```sh
cloudflared tunnel --url http://localhost:8080
```
Copie o endereço `https://….trycloudflare.com` para **Redes sociais → Endereço público**. Pelo túnel ficam acessíveis
**só** as mídias dos anúncios (`/media/…`) e o webhook do WhatsApp — o painel e a API continuam apenas na rede local.
O endereço rápido muda a cada reinício; para um fixo, crie um túnel com nome na sua conta Cloudflare.

### Receber anúncios pelo WhatsApp
1. Use um número que não esteja no app do WhatsApp do celular e cadastre-o no produto **WhatsApp** de um app em developers.facebook.com.
2. Em WhatsApp → Configuração → Webhook, cole o **endereço do webhook** e o **token de verificação** mostrados no painel e assine `messages`.
3. Cole no painel um token de usuário do sistema com `whatsapp_business_messaging` (e, se quiser, a chave secreta do app).
4. Cadastre os **números autorizados** (com DDI e DDD; o 9 extra do celular é opcional).

Depois é só mandar texto, foto ou vídeo para o número do painel: a legenda aparece junto, o painel responde confirmando
e a mensagem fica no ar pelo mesmo número de dias dos posts das redes. Mande **APAGAR** para tirar o último que enviou, ou **AJUDA**.

## Estrutura
```
server.js          servidor (Express) — API, upload, QR codes
social.js          importação e publicação de posts no Instagram e no Facebook
whatsapp.js        recebimento de anúncios pelo WhatsApp (webhook)
public/tv.html     player da TV
public/admin.html  painel de gerenciamento
public/upload.html página de envio pelo celular
public/form.js     formulário de envio (compartilhado)
data/              anúncios, mídias e configuração (criado na primeira execução)
```
