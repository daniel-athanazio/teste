"""Avisos ao cliente pela API oficial (WhatsApp Cloud API), usando modelos aprovados."""
import requests
import config

def ativo():
    return bool(config.WA_TOKEN and config.WA_PHONE_ID)

def enviar_modelo(telefone, tipo, variaveis):
    if not ativo():
        return False, "avisos desligados (WA_TOKEN/WA_PHONE_ID vazios)"
    tel = "".join(c for c in telefone if c.isdigit())
    if len(tel) < 10:
        return False, "telefone invalido"
    if not tel.startswith("55"):
        tel = "55" + tel
    corpo = {"messaging_product": "whatsapp", "to": tel, "type": "template",
             "template": {"name": config.WA_MODELOS[tipo], "language": {"code": config.WA_IDIOMA},
                          "components": [{"type": "body", "parameters": [{"type": "text", "text": str(v)} for v in variaveis]}]}}
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{config.WA_PHONE_ID}/messages",
                          headers={"Authorization": "Bearer " + config.WA_TOKEN}, json=corpo, timeout=30)
        return r.ok, r.text[:300]
    except requests.RequestException as e:
        return False, str(e)[:300]
