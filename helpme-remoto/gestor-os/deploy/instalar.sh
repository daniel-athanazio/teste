#!/usr/bin/env bash
# Instala o Gestor de OS em um VPS Ubuntu 24.04 (Hostinger).
# Uso (como root, dentro da pasta do projeto):  bash deploy/instalar.sh os.fninfohouse.com.br seu@email.com
set -euo pipefail
DOMINIO="${1:?informe o subdominio, ex.: os.fninfohouse.com.br}"
EMAIL="${2:?informe um e-mail para o certificado HTTPS}"
DEST=/opt/gestor-os
ORIGEM="$(cd "$(dirname "$0")/.." && pwd)"

USA_CADDY=0
systemctl is-active --quiet caddy && USA_CADDY=1   # servidor que ja usa Caddy nas portas 80/443

echo ">> Pacotes"
apt-get update -y
if [ "$USA_CADDY" = 1 ]; then
  apt-get install -y python3-venv rsync
else
  apt-get install -y python3-venv nginx certbot python3-certbot-nginx rsync
fi

echo ">> Arquivos em $DEST"
id gestoros >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin gestoros
mkdir -p "$DEST"
rsync -a --exclude venv --exclude '*.db*' --exclude .env --exclude __pycache__ "$ORIGEM/" "$DEST/"
if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  NOVO_ENV=1
  sed -i "s|^SEGREDO=.*|SEGREDO=$(openssl rand -hex 32)|; s|^CHAVE_TV=.*|CHAVE_TV=$(openssl rand -hex 12)|; s|^SENHA_PAINEL=.*|SENHA_PAINEL=$(openssl rand -base64 12)|; s|^URL_PUBLICA=.*|URL_PUBLICA=https://$DOMINIO|" "$DEST/.env"
fi
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"
chown -R gestoros:gestoros "$DEST"
chmod 700 "$DEST"
chmod 600 "$DEST/.env"
[ -f "$DEST/gestor.db" ] && chmod 600 "$DEST"/gestor.db* || true
[ -f "$DEST/logo-os.bin" ] && chown gestoros:gestoros "$DEST/logo-os.bin" || true

echo ">> Servico"
cat > /etc/systemd/system/gestor-os.service <<UNIT
[Unit]
Description=Gestor de OS (Uniplus)
After=network-online.target
[Service]
User=gestoros
WorkingDirectory=$DEST
# 1 processo (a sincronizacao roda dentro dele) com varias threads
ExecStart=$DEST/venv/bin/gunicorn -w 1 --threads 8 -b 127.0.0.1:8017 app:app
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable gestor-os >/dev/null
systemctl restart gestor-os

if [ "$USA_CADDY" = 1 ]; then
  echo ">> Caddy (HTTPS automatico)"
  CF=/etc/caddy/Caddyfile
  if ! grep -qE "^$DOMINIO([[:space:]]|\{|$)" "$CF"; then
    cp "$CF" "$CF.antes-gestor-os"
    printf '\n%s {\n\treverse_proxy 127.0.0.1:8017 {\n\t\theader_up X-Real-IP {remote_host}\n\t}\n}\n' "$DOMINIO" >> "$CF"
  fi
  if caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy
  else
    echo "!! Caddyfile invalido; restaurando o anterior."; [ -f "$CF.antes-gestor-os" ] && cp "$CF.antes-gestor-os" "$CF"
  fi
else
echo ">> Nginx + HTTPS"
cat > /etc/nginx/sites-available/gestor-os <<NGX
server {
    listen 80;
    server_name $DOMINIO;
    location / {
        proxy_pass http://127.0.0.1:8017;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGX
ln -sf /etc/nginx/sites-available/gestor-os /etc/nginx/sites-enabled/gestor-os
nginx -t && systemctl reload nginx
command -v ufw >/dev/null && ufw status | grep -q active && ufw allow 'Nginx Full' || true
certbot --nginx -d "$DOMINIO" -m "$EMAIL" --agree-tos --non-interactive --redirect \
  || echo "!! HTTPS nao emitido: confira se o DNS de $DOMINIO aponta para este servidor e rode de novo."
fi

echo
echo "Pronto: https://$DOMINIO/painel"
if [ "${NOVO_ENV:-0}" = 1 ]; then echo "Senha inicial do painel (troque na aba Configuracao):"; grep -E '^SENHA_PAINEL=' "$DEST/.env"; fi
echo "Para ligar no Uniplus: entre no painel e abra a aba Configuracao."
