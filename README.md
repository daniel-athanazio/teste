# 📺 Painel de Anúncios

Aplicação para exibir **anúncios em imagem e vídeo** no navegador de uma **Smart TV ou TV Box**.
Fica instalada em um computador da loja/escritório, e os anúncios podem ser enviados **pelo celular**
escaneando um **QR code** (celular e computador na mesma rede Wi-Fi).

## Recursos
- Imagens (JPG, PNG, GIF, WebP) e vídeos (MP4, WebM, MOV)
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

### Dicas
- **Som nos vídeos:** por padrão os vídeos tocam sem som (os navegadores de TV bloqueiam autoplay com som).
  Para tentar com som, use `http://IP:8080/tv?som=1`.
- **Endereço fixo:** configure no roteador um IP fixo (reserva DHCP) para o computador, assim o endereço da TV não muda.
- **Vídeos:** prefira MP4 (H.264), que funciona em praticamente todas as TVs.
- **Porta diferente:** `PORT=9000 node server.js` (ou `set PORT=9000` no Windows antes de iniciar).
- Os arquivos e a configuração ficam na pasta `data/` (faça backup dela). O código de acesso está em `data/config.json`;
  apague o campo `token` e reinicie para gerar um novo (os QR codes antigos param de funcionar).

## Estrutura
```
server.js          servidor (Express) — API, upload, QR codes
public/tv.html     player da TV
public/admin.html  painel de gerenciamento
public/upload.html página de envio pelo celular
public/form.js     formulário de envio (compartilhado)
data/              anúncios, mídias e configuração (criado na primeira execução)
```
