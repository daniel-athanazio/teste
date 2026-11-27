"""Aba Configuracao: Uniplus, senhas, prazos e WhatsApp pelo navegador (sem comandos).
Fluxo: senha para abrir -> preencher -> Testar (sem senha, nada e gravado) -> senha para salvar."""
import base64, hmac, io, os, re, secrets, time, collections, threading
import requests
from flask import request, redirect, render_template, session, abort, Response
import config, db, uniplus, whatsapp, seguranca, modelos

ABERTA_MIN = 30
_pend = {}        # segredos digitados e ainda nao salvos, por sessao (ficam so na memoria do servidor)
TEXTO = ("UNIPLUS_SERVIDOR", "UNIPLUS_CONTA", "NOME_LOJA", "WHATSAPP_LOJA", "WA_PHONE_ID", "GARANTIA_CAMPO", "LEMBRETE_RETIRADA_DIAS", "RESUMO_HORA", "RESUMO_TELEFONE")
DATAS = ("PERIODO_INI", "PERIODO_FIM")
NUMEROS = {"PRAZO_PADRAO_DIAS": (0, 365), "PRAZO_VISITA_DIAS": (0, 365), "PRAZO_ORCAMENTO_DIAS": (0, 365),
           "PRAZO_TERCEIRIZADA_DIAS": (0, 365), "PRAZO_RETIRADA_DIAS": (0, 3650), "ALERTA_LEGAL_DIAS": (1, 30), "RETENCAO_MESES": (0, 600), "TERC_AMARELO_DIAS": (0, 365), "LEMBRETE_ORCAMENTO_DIAS": (0, 365), "META_MES": (0, 1e9),
           "ALERTA_CHAMADO_MIN": (1, 100000), "ALERTA_AGUARDANDO_HORAS": (1, 100000), "SYNC_MINUTOS": (1, 1440)}
SEGREDOS = ("UNIPLUS_AUTORIZACAO", "WA_TOKEN")

ARQ_LOGO = os.path.join(config.RAIZ, "logo-os.bin")
LOGO_MAX = 30000          # a logo vai embutida no modelo; o Java limita o tamanho do texto

def ler_logo():
    try:
        return open(ARQ_LOGO, "rb").read()
    except OSError:
        return b""

def preparar_logo(dados):
    """Reduz a imagem enviada para caber no modelo. Devolve bytes PNG/JPEG ou levanta ValueError com o motivo."""
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("O servidor ainda não tem o suporte a imagens instalado. Rode o instalador de novo.")
    try:
        im = Image.open(io.BytesIO(dados)); im.load()
    except Exception:
        raise ValueError("Não consegui abrir este arquivo como imagem. Use PNG ou JPG.")
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fundo = Image.new("RGB", im.size, "white"); fundo.paste(im, mask=im.split()[-1]); im = fundo
    else:
        im = im.convert("RGB")
    for lado in (480, 400, 320, 260, 200, 150):
        c = im.copy(); c.thumbnail((lado, lado))
        b = io.BytesIO(); c.save(b, "PNG", optimize=True)
        if b.tell() <= LOGO_MAX:
            return b.getvalue()
        b = io.BytesIO(); c.save(b, "JPEG", quality=85, optimize=True)
        if b.tell() <= LOGO_MAX:
            return b.getvalue()
    raise ValueError("A imagem é complexa demais para embutir no modelo. Envie uma versão mais simples da logo.")

def _mascara(v):
    return ("•" * 8 + v[-4:]) if v and len(v) > 8 else ("•" * 8 if v else "")

def _limpar_demo():
    c = db.con()
    c.executescript("""DELETE FROM os; DELETE FROM historico; DELETE FROM avaliacao; DELETE FROM aviso;
        DELETE FROM chamado_hist WHERE chamado IN (SELECT id FROM chamado WHERE exemplo=1);
        DELETE FROM chamado WHERE exemplo=1; DELETE FROM meta;""")
    c.commit()

def testar_uniplus(servidor, conta, chave):
    """Testa os dados informados (sem gravar). Devolve o resultado e, se falhar, os campos com problema."""
    campos = {}
    if not servidor:
        campos["UNIPLUS_SERVIDOR"] = "Informe o endereço do servidor."
    elif "canal.intelidata" in servidor or "/acesso/" in servidor:
        campos["UNIPLUS_SERVIDOR"] = "Este é o endereço da tela de login, não o da API. Use o endereço do seu servidor Uniplus Web (termina em .uniplusweb.com)."
    if not chave:
        campos["UNIPLUS_AUTORIZACAO"] = "Informe a chave da API."
    if campos:
        return {"ok": False, "campos": campos, "erro": ""}
    try:
        brutas = uniplus.listar_os_brutas(max_paginas=2, cred=uniplus.credencial(servidor, conta, chave))
    except requests.exceptions.HTTPError as e:
        cod = e.response.status_code if e.response is not None else 0
        no_token = "oauth/token" in (e.response.url if e.response is not None else "")
        if no_token and cod in (404, 405):
            campos["UNIPLUS_SERVIDOR"] = "Este endereço respondeu, mas não tem a API do Uniplus."
        elif no_token:
            campos["UNIPLUS_CONTA"] = campos["UNIPLUS_AUTORIZACAO"] = "O Uniplus recusou a conta ou a chave (erro %s). Confira as duas em Ferramentas > Configuração da API." % cod
        elif cod in (401, 403):
            campos["UNIPLUS_AUTORIZACAO"] = "A chave entrou, mas não tem permissão para ler Ordens de Serviço (erro %s)." % cod
        else:
            campos["UNIPLUS_SERVIDOR"] = "O servidor entrou, mas a consulta de OS falhou (erro %s)." % cod
        return {"ok": False, "campos": campos, "erro": f"HTTP {cod}"}
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        campos["UNIPLUS_SERVIDOR"] = "Não consegui conectar neste endereço ou a resposta não é da API do Uniplus."
        return {"ok": False, "campos": campos, "erro": f"{type(e).__name__}: {str(e)[:200]}"}
    lidas = [uniplus.normalizar(b) for b in brutas]
    cont = collections.Counter(((f"{o['status_cod']} · " if o["status_cod"] else "") + (o["status"] or "(vazio)"), o["situacao"]) for o in lidas)
    tit = {k: t for k, t, _ in config.SITUACOES}
    leitura = dict(lidas[0]) if lidas else {}
    for k in ("cliente", "telefone"):
        if leitura:
            leitura[k] = "(oculto)" if leitura[k] else "(vazio)"
    vazios = [k for k in ("cliente", "equipamento", "serie", "status", "abertura", "tecnico") if lidas and not any(o[k] for o in lidas)]
    return {"ok": True, "campos": {}, "n": len(brutas),
            "nomes": sorted(brutas[0].keys()) if brutas and isinstance(brutas[0], dict) else [],
            "mapa": [(st, tit.get(s, s), n) for (st, s), n in cont.most_common()], "leitura": leitura, "vazios": vazios}

def registrar(app, sincronizar):
    def aberta():
        return session.get("cfg_ate", 0) > time.time()

    def pend():
        sid = session.get("cfg_id")
        for k in [k for k, v in _pend.items() if v["ate"] < time.time()]:
            _pend.pop(k, None)
        return _pend.get(sid, {}) if sid else {}

    @app.route("/config/modelo/<tipo>")
    def cfg_modelo(tipo):
        """Baixa o modelo de OS (.jrxml) ja com o endereco deste gestor, a chave do QR e a logo."""
        if not session.get("ok") or not aberta():
            return redirect("/config")
        if tipo not in modelos.TIPOS:
            abort(404)
        import app as principal
        nome, conteudo = modelos.gerar(tipo, config.URL_PUBLICA, principal.chave_modelos(), base64.b64encode(ler_logo()).decode())
        return Response(conteudo, mimetype="application/xml", headers={"Content-Disposition": f'attachment; filename="{nome}"'})

    @app.route("/config/logo")
    def cfg_logo():
        if not session.get("ok") or not aberta():
            abort(403)
        b = ler_logo()
        if not b:
            abort(404)
        return Response(b, mimetype="image/png" if b[:4] == b"\x89PNG" else "image/jpeg")

    @app.route("/config", methods=["GET", "POST"])
    def cfg():
        if not session.get("ok"):                 # a chave do telao nao da acesso a esta aba
            return redirect("/login")
        f = request.form
        acao = f.get("acao", "")
        if not aberta():
            erro = None
            if request.method == "POST" and acao == "abrir":
                if seguranca.confere(f.get("senha", ""), config.SENHA_DONO or config.SENHA_PAINEL):
                    if config.SENHA_DONO:
                        session["dono_ate"] = time.time() + 8 * 3600
                    session["cfg_ate"] = time.time() + ABERTA_MIN * 60
                    session["cfg_id"] = secrets.token_hex(8)
                    return redirect("/config")
                time.sleep(1)
                erro = "Senha incorreta."
            return render_template("config_senha.html", erro=erro, loja=config.NOME_LOJA, tem_dono=bool(config.SENHA_DONO),
                                   expirou=request.method == "POST" and acao != "abrir")
        session["cfg_ate"] = time.time() + ABERTA_MIN * 60
        if acao == "logo":
            arq = request.files.get("logo")
            if not arq or not arq.filename:
                session["cfg_aviso"] = "erro:Escolha o arquivo da logo antes de enviar."
            else:
                try:
                    pronto = preparar_logo(arq.read())
                    with open(ARQ_LOGO, "wb") as fh:
                        fh.write(pronto)
                    session["cfg_aviso"] = "ok:Logo gravada. Baixe os modelos de novo e reenvie no Uniplus para ela aparecer nas OS."
                except (ValueError, OSError) as e:
                    session["cfg_aviso"] = "erro:" + str(e)
            return redirect("/config#modelos")
        if acao == "remover_logo":
            try:
                os.remove(ARQ_LOGO)
            except OSError:
                pass
            session["cfg_aviso"] = "ok:Logo removida. Baixe os modelos de novo para tirá-la das OS."
            return redirect("/config#modelos")
        if acao == "fechar":
            _pend.pop(session.pop("cfg_id", None), None)
            session.pop("cfg_ate", None)
            return redirect("/painel")

        # valores exibidos: o que esta salvo, coberto pelo que foi digitado
        v = {k: getattr(config, k) for k in TEXTO + tuple(NUMEROS) + DATAS}
        v["META_MES"] = "%g" % config.META_MES
        seg = dict(pend())
        msg = teste = None
        campos = {}
        if request.method == "POST":
            for k in TEXTO + tuple(NUMEROS) + DATAS:
                if k in f:
                    v[k] = f[k].strip()
            for k in DATAS:
                if v[k] and not config._data_iso(v[k]):
                    campos[k] = "Data inválida."
            if v["PERIODO_INI"] and v["PERIODO_FIM"] and not campos and v["PERIODO_INI"] > v["PERIODO_FIM"]:
                campos["PERIODO_FIM"] = "A data final é anterior à inicial."
            if v["UNIPLUS_SERVIDOR"] and not v["UNIPLUS_SERVIDOR"].startswith("http"):
                v["UNIPLUS_SERVIDOR"] = "https://" + v["UNIPLUS_SERVIDOR"]
            v["UNIPLUS_SERVIDOR"] = v["UNIPLUS_SERVIDOR"].rstrip("/")
            for k in SEGREDOS:                    # em branco = manter o digitado antes ou o salvo
                if f.get(k, "").strip():
                    seg[k] = f[k].strip()
            if f.get("limpar_WA_TOKEN"):
                seg["WA_TOKEN"] = ""
            _pend[session["cfg_id"]] = {**seg, "ate": time.time() + ABERTA_MIN * 60}
            for k, (mn, mx) in NUMEROS.items():
                try:
                    n = float(str(v[k]).replace(",", "."))
                    if not mn <= n <= mx:
                        raise ValueError
                except ValueError:
                    campos[k] = f"Informe um número entre {mn:g} e {mx:g}."
            if v["GARANTIA_CAMPO"] and not re.fullmatch(r"extra(1[0-6]|[1-9])", v["GARANTIA_CAMPO"]):
                campos["GARANTIA_CAMPO"] = "Campo inválido."
            if v["LEMBRETE_RETIRADA_DIAS"] and not re.fullmatch(r"\s*\d+(\s*,\s*\d+)*\s*", v["LEMBRETE_RETIRADA_DIAS"]):
                campos["LEMBRETE_RETIRADA_DIAS"] = "Use números separados por vírgula, ex.: 30,90,150."
            if v["RESUMO_HORA"] and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v["RESUMO_HORA"]):
                campos["RESUMO_HORA"] = "Use o formato HH:MM, ex.: 18:00."
            if v["RESUMO_HORA"] and len("".join(ch for ch in v["RESUMO_TELEFONE"] if ch.isdigit())) < 12:
                campos["RESUMO_TELEFONE"] = "Informe o WhatsApp com 55 e DDD para receber o resumo."
            if not campos.get("TERC_AMARELO_DIAS") and not campos.get("PRAZO_TERCEIRIZADA_DIAS") and float(v["TERC_AMARELO_DIAS"]) > float(v["PRAZO_TERCEIRIZADA_DIAS"]):
                campos["TERC_AMARELO_DIAS"] = "O amarelo precisa começar antes do vermelho."
            validas = {s for s, _, _ in config.SITUACOES}
            ev_mapa = {f.get("evn_" + k[4:], ""): x for k, x in f.items() if k.startswith("evs_")}
            ev_txt = "|".join(f"{n.replace('|', ' ').replace('=', ' ')}={x}" for n, x in sorted(ev_mapa.items()) if n and x in validas)
            chave = seg.get("UNIPLUS_AUTORIZACAO", config.UNIPLUS_AUTORIZACAO)
            uni_novo = (v["UNIPLUS_SERVIDOR"], v["UNIPLUS_CONTA"], chave)
            uni_mudou = uni_novo != (config.UNIPLUS_SERVIDOR, config.UNIPLUS_CONTA, config.UNIPLUS_AUTORIZACAO)
            precisa_teste = acao == "testar" or (acao == "salvar" and uni_mudou and any(uni_novo))
            if precisa_teste:
                teste = testar_uniplus(*uni_novo)
                campos.update(teste["campos"])
            if acao == "salvar":
                nova = f.get("nova_senha", "")
                if nova or f.get("nova_senha2"):
                    if len(nova) < 8:
                        campos["nova_senha"] = "A nova senha precisa ter pelo menos 8 caracteres."
                    elif nova != f.get("nova_senha2", ""):
                        campos["nova_senha2"] = "A confirmação não é igual à nova senha."
                nd = f.get("nova_dono", "")
                if nd or f.get("nova_dono2"):
                    if len(nd) < 8:
                        campos["nova_dono"] = "A senha master precisa ter pelo menos 8 caracteres."
                    elif nd != f.get("nova_dono2", ""):
                        campos["nova_dono2"] = "A confirmação não é igual à senha master."
                    elif seguranca.confere(nd, config.SENHA_PAINEL) or (nova and nd == nova):
                        campos["nova_dono"] = "A senha master precisa ser diferente da senha da equipe."
                if not seguranca.confere(f.get("senha_atual", ""), config.SENHA_DONO or config.SENHA_PAINEL):
                    time.sleep(1)
                    campos["senha_atual"] = "Senha incorreta. " + ("É a senha master." if config.SENHA_DONO else "É a senha com que você entra no painel hoje.")
                if not campos:
                    era_demo, mapa_antes = config.DEMO, (dict(config.EVENTO_MAPA), config.GARANTIA_CAMPO)
                    m = {k: v[k] for k in TEXTO + tuple(NUMEROS) + DATAS}
                    m.update({k: seg[k] for k in SEGREDOS if k in seg})
                    if any(k.startswith("evn_") for k in f):
                        m["EVENTO_MAPA"] = ev_txt
                    if nova:
                        m["SENHA_PAINEL"] = seguranca.gerar_hash(nova)
                    if nd:
                        m["SENHA_DONO"] = seguranca.gerar_hash(nd)
                    if f.get("nova_chave_tv"):
                        m["CHAVE_TV"] = secrets.token_hex(12)
                    try:
                        config.gravar_env(m)
                    except OSError as e:
                        campos["senha_atual"] = f"Não foi possível gravar a configuração: {e}"
                    else:
                        _pend.pop(session["cfg_id"], None)
                        seg = {}
                        msg = "Configuração salva." + (" A senha da equipe foi trocada." if nova else "") + (" A senha master foi definida." if nd else "")
                        if nd:
                            session["dono_ate"] = time.time() + 8 * 3600
                        if uni_mudou or mapa_antes != (config.EVENTO_MAPA, config.GARANTIA_CAMPO):
                            uniplus._token.update(valor=None, expira=0)
                            if era_demo and not config.DEMO:
                                _limpar_demo()
                            if not config.DEMO:
                                threading.Thread(target=lambda: sincronizar(completa=True), daemon=True).start()
                                msg += " Lendo as OS do Uniplus agora; o painel atualiza em instantes."
                        v = {k: getattr(config, k) for k in TEXTO + tuple(NUMEROS) + DATAS}
                        v["META_MES"] = "%g" % config.META_MES
        eventos = [dict(r) for r in db.con().execute(
            """SELECT status ev, COUNT(*) n, MAX(situacao) sit FROM os
               WHERE status_cod NOT IN ('', '4', '5') AND status<>'' GROUP BY status ORDER BY n DESC LIMIT 60""")]
        tit = {k: t for k, t, _ in config.SITUACOES}
        for r in eventos:
            r["esc"] = config.EVENTO_MAPA.get(config._chave(r["ev"]), "")
            r["auto"] = tit.get(r["sit"], r["sit"])
        if request.method == "POST" and not msg:
            for r in eventos:
                r["esc"] = ev_mapa.get(r["ev"], r["esc"])
        aviso = session.pop("cfg_aviso", "")
        return render_template("config.html", tem_logo=bool(ler_logo()), aviso_ok=aviso[3:] if aviso.startswith("ok:") else "",
                               aviso_erro=aviso[5:] if aviso.startswith("erro:") else "", c=config, v=v, msg=msg, teste=teste, campos=campos, loja=config.NOME_LOJA,
                               aut=_mascara(config.UNIPLUS_AUTORIZACAO), wat=_mascara(config.WA_TOKEN),
                               aut_dig="UNIPLUS_AUTORIZACAO" in seg, wat_dig=bool(seg.get("WA_TOKEN")),
                               wa=whatsapp.ativo(), eventos=eventos, situacoes=config.SITUACOES,
                               salvou=bool(msg), acao=acao, tem_dono=bool(config.SENHA_DONO), chave_helpme=__import__("app").chave_helpme())
