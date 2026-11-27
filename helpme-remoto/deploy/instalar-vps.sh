#!/bin/bash
# Instala o Help me! + Gestor de OS num VPS Ubuntu 24.04, do zero, com HTTPS.
# Uso (como root, dentro da pasta helpme-remoto/deploy):
#   bash instalar-vps.sh suporte.fninfohouse.com.br os.fninfohouse.com.br SEU-EMAIL
# Pode rodar de novo para atualizar: senhas, banco e configurações são mantidos.
set -euo pipefail
DOM_HM=${1:?informe o domínio do Help me! (ex.: suporte.fninfohouse.com.br)}
DOM_OS=${2:?informe o domínio do gestor (ex.: os.fninfohouse.com.br)}
EMAIL=${3:?informe seu e-mail (aviso de vencimento do certificado HTTPS)}
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Rode como root (sudo -i)."; exit 1; }

echo "==> Instalando Docker, Nginx e Certbot"
apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 nginx certbot python3-certbot-nginx openssl >/dev/null
systemctl enable --now docker >/dev/null

aleatorio() { openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c "$1"; }

if [ ! -f .env ]; then
  SENHA_HM=$(aleatorio 14)
  cat > .env <<EOF
DOMINIO=$DOM_HM
FN_ADMIN_USUARIO=admin
FN_ADMIN_SENHA=$SENHA_HM
FN_EMPRESA=FN Info House
FN_CONTATO=FN Info House – WhatsApp (21) 96438-2098
EOF
  chmod 600 .env
fi
if [ ! -f gestor.env ]; then
  cp gestor.env.exemplo gestor.env
  sed -i "s|^SENHA_PAINEL=.*|SENHA_PAINEL=$(aleatorio 14)|; s|^CHAVE_TV=.*|CHAVE_TV=$(aleatorio 20)|; \
          s|^SEGREDO=.*|SEGREDO=$(aleatorio 40)|; s|^URL_PUBLICA=.*|URL_PUBLICA=https://$DOM_OS|; \
          s|^HELPME_URL=.*|HELPME_URL=https://$DOM_HM|" gestor.env
  chmod 600 gestor.env
fi
mkdir -p helpme-dados gestor-dados

echo "==> Compilando e subindo (a primeira vez demora alguns minutos)"
docker compose up -d --build

echo "==> Configurando o Nginx"
sed "s/suporte\.fninfohouse\.com\.br/$DOM_HM/; s/os\.fninfohouse\.com\.br/$DOM_OS/" nginx-helpme.conf \
  > /etc/nginx/sites-available/helpme
ln -sf /etc/nginx/sites-available/helpme /etc/nginx/sites-enabled/helpme
nginx -t && systemctl reload nginx

echo "==> Certificado HTTPS"
certbot --nginx -n --agree-tos -m "$EMAIL" -d "$DOM_HM" -d "$DOM_OS" --redirect || \
  echo "!! O HTTPS falhou: confira se os registros A de $DOM_HM e $DOM_OS apontam para este VPS e rode o script de novo."

echo "==> Ligando a integração Help me! <-> gestor"
for i in $(seq 1 30); do curl -fs http://127.0.0.1:8010/api/marca >/dev/null && break; sleep 2; done
SEGREDO=$(grep '^SEGREDO=' gestor.env | cut -d= -f2-)
CHAVE=$(python3 -c "import hmac,hashlib,sys;print(hmac.new(sys.argv[1].encode(),b'helpme-api',hashlib.sha256).hexdigest()[:32])" "$SEGREDO")
SENHA_HM=$(grep '^FN_ADMIN_SENHA=' .env | cut -d= -f2-)
TOK=$(curl -fs -XPOST http://127.0.0.1:8010/api/login -H 'Content-Type: application/json' \
      -d "{\"usuario\":\"admin\",\"senha\":\"$SENHA_HM\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])") || TOK=""
if [ -n "$TOK" ]; then
  curl -fs -XPUT http://127.0.0.1:8010/api/config/gestor -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
    -d "{\"ativo\":true,\"url\":\"http://gestor:8000\",\"token\":\"$CHAVE\"}" >/dev/null && echo "Integração ligada."
fi

echo
echo "================================================================"
echo " PRONTO!"
echo " Painel dos técnicos:  https://$DOM_HM      usuário: admin   senha: $SENHA_HM"
echo " Site para o cliente:  https://$DOM_HM/baixar"
echo " Gestor de OS:         https://$DOM_OS      senha: $(grep '^SENHA_PAINEL=' gestor.env | cut -d= -f2-)"
echo " Guarde estas senhas. Elas também estão em $(pwd)/.env e gestor.env"
echo "================================================================"
