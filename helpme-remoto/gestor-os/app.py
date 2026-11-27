"""Gestor de OS integrado ao Uniplus: painel, consulta por QR, avaliacao e avisos."""
import hmac, hashlib, json, re, threading, time, traceback
from datetime import datetime, timedelta
from flask import Flask, request, session, redirect, url_for, render_template, jsonify, abort
import os
os.umask(0o077)          # arquivos criados (banco, .env) so para o usuario do servico
import config, db, uniplus, whatsapp, chamados, configuracao, seguranca

app = Flask(__name__)
app.secret_key = config.SEGREDO
app.permanent_session_lifetime = timedelta(days=7)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=config.URL_PUBLICA.startswith("https"))
db.iniciar()
if db.meta("correcao_conclusao_1") is None:      # versoes anteriores gravavam a hora da leitura como data de conclusao
    db.con().execute("UPDATE os SET conclusao=NULL WHERE conclusao IS NOT NULL")
    db.con().execute("DELETE FROM meta WHERE chave='ultima_completa'")
    db.con().commit()
    db.meta("correcao_conclusao_1", "1")
if db.meta("correcao_telefone_1") is None:       # telefones sem DDD eram ignorados: esquece as buscas que voltaram vazias
    db.con().execute("DELETE FROM cliente WHERE telefone='' OR telefone IS NULL")
    db.con().commit()
    db.meta("correcao_telefone_1", "1")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
LOGIN = seguranca.Bloqueio(5, 15 * 60)          # 5 senhas erradas por IP em 15 min

def ip():
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"

@app.after_request
def cabecalhos(r):
    r.headers.setdefault("X-Frame-Options", "DENY")
    r.headers.setdefault("X-Content-Type-Options", "nosniff")
    r.headers.setdefault("Referrer-Policy", "no-referrer")
    r.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    r.headers.setdefault("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    r.headers.setdefault("Cache-Control", "no-store")
    if config.URL_PUBLICA.startswith("https"):
        r.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return r

@app.route("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}

@app.route("/privacidade")
def privacidade():
    return render_template("privacidade.html", loja=config.NOME_LOJA, zap=config.WHATSAPP_LOJA, meses=config.RETENCAO_MESES or 24)

@app.template_filter("reserva")
def f_reserva(nome, ocultar):
    """No telao (visivel ao publico da loja) mostra so o primeiro nome."""
    if not ocultar or not nome:
        return nome
    p = str(nome).split()
    return p[0].title() + (" " + p[-1][0].upper() + "." if len(p) > 1 else "")
TITULOS = {k: t for k, t, _ in config.SITUACOES}
_trava = threading.Lock()

# ---------------- regras ----------------
def agora():
    return datetime.now()

def limite_prazo(o):
    if o["previsao"]:
        return datetime.fromisoformat(o["previsao"])
    if not o["abertura"]:
        return None
    dias = config.PRAZO_VISITA_DIAS if o["visita"] else config.PRAZO_PADRAO_DIAS
    return datetime.fromisoformat(o["abertura"]) + timedelta(days=dias)

COR_TXT = {"verde": "no prazo", "amarelo": "atenção", "vermelho": "vencido", "cinza": ""}

def _sinal(o):
    """Semaforo da etapa atual: verde ate 70% do prazo, amarelo ate o prazo, vermelho depois."""
    ab = datetime.fromisoformat(o["abertura"]) if o["abertura"] else None
    ev = datetime.fromisoformat(o["evento_em"]) if o.get("evento_em") else None
    sit = o["situacao"]
    etapa = {"abertas": ("orçamento", ab, config.PRAZO_ORCAMENTO_DIAS),
             "retirada": ("retirada", ev or ab, config.PRAZO_RETIRADA_DIAS)}.get(sit)
    if sit in ("pecas", "pendentes") and ab:
        lim = o["limite"]
        etapa = ("serviço", ab, max(1, (lim - ab).days) if lim else config.PRAZO_PADRAO_DIAS)
    s = {"cor": "cinza", "txt": "", "legal": "", "dias": None}
    if sit == "terceirizada" and (ev or ab):            # faixas proprias: amarelo e vermelho definidos na Configuracao
        dias = max(0, (agora() - (ev or ab)).days)
        s["dias"] = dias
        s["cor"] = "verde" if dias < config.TERC_AMARELO_DIAS else ("amarelo" if dias < config.PRAZO_TERCEIRIZADA_DIAS else "vermelho")
        s["txt"] = f"na terceirizada há {dias} d"
    elif sit == "aprovacao" and (ev or ab):
        s["txt"] = f"aguarda cliente há {(agora() - (ev or ab)).days} d"
    elif etapa and etapa[1] and etapa[2] > 0:
        nome, ini, prazo = etapa
        dias = max(0, (agora() - ini).days)
        s["cor"] = "verde" if dias < prazo * 0.7 else ("amarelo" if dias <= prazo else "vermelho")
        s["txt"] = f"{nome}: {dias} de {prazo} d"
    if ab and sit not in config.ENCERRADAS + ("retirada",):      # prazo legal de 30 dias, contado da entrada
        d = (agora() - ab).days
        if d >= 30:
            s["cor"], s["legal"] = "vermelho", f"prazo legal vencido ({d} d)"
        elif d >= config.ALERTA_LEGAL_DIAS:
            s["cor"] = "vermelho" if s["cor"] == "vermelho" else "amarelo"
            s["legal"] = f"prazo legal: dia {d} de 30"
    s["rot"] = COR_TXT[s["cor"]]
    return s

def enriquecer(r):
    o = dict(r)
    lim = limite_prazo(o)
    o["limite"] = lim
    o["atrasada"] = bool(lim and o["situacao"] not in config.ENCERRADAS + ("retirada",) and agora() > lim)
    o["dias_atraso"] = (agora() - lim).days if o["atrasada"] else 0
    o["dias_aberta"] = (agora() - datetime.fromisoformat(o["abertura"])).days if o["abertura"] else None
    o["titulo"] = TITULOS.get(o["situacao"], o["situacao"])
    o["sinal"] = _sinal(o)
    return o

def no_periodo(o):
    d = (o["abertura"] or "")[:10]
    if config.PERIODO_INI and (not d or d < config.PERIODO_INI):
        return False
    if config.PERIODO_FIM and (not d or d > config.PERIODO_FIM):
        return False
    return True

CORES_PIZZA = {"abertas": "#2563eb", "aprovacao": "#d97706", "pecas": "#7c3aed", "pendentes": "#0d9488",
               "terceirizada": "#db2777", "retirada": "#16a34a"}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

def comparativo(tudo, metrica):
    """Ultimos 12 meses x os mesmos meses do ano anterior (usa todas as OS, sem o filtro de periodo)."""
    hoje, saida = agora(), []
    for i in range(11, -1, -1):
        m = hoje.month - i
        ano = hoje.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        def soma(a):
            pref = f"{a:04d}-{m:02d}"
            if metrica == "valor":
                return sum(o["valor"] or 0 for o in tudo if o["situacao"] == "finalizadas" and (o["conclusao"] or "").startswith(pref))
            return sum(1 for o in tudo if (o["abertura"] or "").startswith(pref))
        saida.append({"r": f"{MESES[m - 1]}/{ano % 100:02d}", "a": soma(ano), "b": soma(ano - 1)})
    return saida

def token(codigo):
    return hmac.new(config.SEGREDO.encode(), str(codigo).encode(), hashlib.sha256).hexdigest()[:12]

def chave_modelos():
    """Chave embutida nos modelos de OS: so quem imprime pelo Uniplus recebe o QR com acesso direto."""
    return hmac.new(config.SEGREDO.encode(), b"modelos-os-qr", hashlib.sha256).hexdigest()[:20]

def chave_helpme():
    """Chave da integracao com o aplicativo Help me! (consulta de OS pelo nome completo do cliente)."""
    return hmac.new(config.SEGREDO.encode(), b"helpme-api", hashlib.sha256).hexdigest()[:32]

def passe(codigo):
    """Codigo que vai dentro do QR impresso: quem tem o papel da OS consulta sem digitar nada."""
    return hmac.new(config.SEGREDO.encode(), ("passe-os:" + str(codigo)).encode(), hashlib.sha256).hexdigest()[:16]

def link_consulta(codigo):
    return f"{config.URL_PUBLICA}/c/{codigo}"

def link_avaliacao(codigo):
    return f"{config.URL_PUBLICA}/avaliar/{codigo}-{token(codigo)}"

# ---------------- sincronizacao ----------------
AVISOS = {"aprovacao": "aprovacao", "retirada": "retirada", "finalizadas": "finalizada"}

def avisar(o, tipo):
    link = link_avaliacao(o["codigo"]) if tipo == "finalizada" else link_consulta(o["codigo"])
    nome = (o["cliente"] or "cliente").split()[0].title()
    ok, det = whatsapp.enviar_modelo(o["telefone"], tipo, [nome, o["codigo"], link])
    c = db.con()
    c.execute("INSERT INTO aviso(codigo,tipo,ok,detalhe,quando) VALUES(?,?,?,?,?)",
              (o["codigo"], tipo, int(ok), det, agora().isoformat(timespec="seconds")))
    c.commit()

def _telefones(c, limite=40):
    """Busca no cadastro o telefone dos clientes com OS em aberto (a OS da API nao traz telefone)."""
    if config.DEMO:
        return
    falta = c.execute("""SELECT DISTINCT o.cod_cliente FROM os o LEFT JOIN cliente k ON k.codigo=o.cod_cliente
                         WHERE o.cod_cliente<>'' AND o.telefone='' AND o.situacao NOT IN ('finalizadas','canceladas')
                         AND (k.codigo IS NULL OR k.quando < ? OR (k.telefone='' AND k.quando < ?)) LIMIT ?""",
                      ((agora() - timedelta(days=7)).isoformat(), (agora() - timedelta(hours=6)).isoformat(), limite)).fetchall()
    for r in falta:
        tel = uniplus.telefone_cliente(r["cod_cliente"])
        c.execute("INSERT INTO cliente VALUES(?,?,?) ON CONFLICT(codigo) DO UPDATE SET telefone=excluded.telefone, quando=excluded.quando",
                  (r["cod_cliente"], tel, agora().isoformat(timespec="seconds")))
    c.execute("""UPDATE os SET telefone=(SELECT telefone FROM cliente k WHERE k.codigo=os.cod_cliente)
                 WHERE telefone='' AND cod_cliente IN (SELECT codigo FROM cliente WHERE telefone<>'')""")
    c.commit()

def sincronizar(completa=False):
    if not _trava.acquire(blocking=False):
        return "ja em andamento"
    try:
        c = db.con()
        primeira = db.meta("primeira_sync_ok") is None
        ts = agora().isoformat(timespec="seconds")
        if config.DEMO:
            brutas = uniplus.os_demo()
        else:
            ctm = int(db.meta("ctm_max") or 0)
            diaria = (db.meta("ultima_completa") or "") < (agora() - timedelta(hours=24)).isoformat()
            brutas = None
            if ctm and not (completa or primeira or diaria):
                brutas = uniplus.listar_os_brutas(desde=ctm)      # so o que mudou
            if brutas is None:
                brutas = uniplus.listar_os_brutas()
                db.meta("ultima_completa", ts)
        mudancas = []
        corte_ret = (agora() - timedelta(days=30.44 * config.RETENCAO_MESES)).isoformat() if config.RETENCAO_MESES else ""
        if corte_ret:                                   # tambem limpa o que ja estava gravado
            c.execute("""UPDATE os SET cliente='', telefone='', doc4='', cod_cliente='', problema='', laudo=''
                         WHERE situacao IN ('finalizadas','canceladas') AND COALESCE(conclusao, evento_em, abertura, '9') < ? AND cliente<>''""", (corte_ret,))
            c.execute("""UPDATE chamado SET cliente='', telefone='', problema='', solucao='', comentario=''
                         WHERE status IN ('concluido','cancelado') AND COALESCE(fim_em, aberto_em) < ? AND cliente<>''""", (corte_ret,))
            c.execute("DELETE FROM cliente WHERE codigo NOT IN (SELECT cod_cliente FROM os WHERE cod_cliente<>'')")
            c.execute("DELETE FROM aviso WHERE quando < ?", (corte_ret,))
        corte_90 = (agora() - timedelta(days=config.PRAZO_CONSULTA_DIAS)).isoformat() if config.PRAZO_CONSULTA_DIAS else ""
        if corte_90:                                    # detalhes do reparo saem do acesso apos o prazo da consulta
            c.execute("""UPDATE os SET itens='', laudo='', obs=''
                         WHERE situacao='finalizadas' AND COALESCE(conclusao, evento_em, abertura, '9') < ? AND (itens<>'' OR laudo<>'' OR obs<>'')""", (corte_90,))
        for b in brutas:
            o = uniplus.normalizar(b)
            if not o["codigo"] or o["codigo"] == "None":
                continue
            if corte_90 and o["situacao"] == "finalizadas" and (o["conclusao"] or o["evento_em"] or o["abertura"] or "9") < corte_90:
                o.update(itens="", laudo="", obs="")
            if corte_ret and o["situacao"] in config.ENCERRADAS and (o["conclusao"] or o["evento_em"] or o["abertura"] or "9") < corte_ret:
                o.update(cliente="", telefone="", doc4="", cod_cliente="", problema="", laudo="")     # LGPD: so o necessario
            ant = c.execute("SELECT situacao, conclusao, telefone FROM os WHERE codigo=?", (o["codigo"],)).fetchone()
            mudou = ant is None or ant["situacao"] != o["situacao"]
            if o["situacao"] != "finalizadas":
                o["conclusao"] = None
            elif not o["conclusao"]:
                viu_mudar = ant is not None and ant["situacao"] != "finalizadas" and not primeira and not completa
                o["conclusao"] = (ant["conclusao"] if ant and ant["conclusao"] else None) or o.get("evento_em") or (ts[:16] if viu_mudar else None)
            if not o["telefone"] and ant and ant["telefone"] and o["cod_cliente"]:
                o["telefone"] = ant["telefone"]
            c.execute("""INSERT INTO os(codigo,cliente,telefone,equipamento,serie,problema,status,situacao,tecnico,visita,
                         abertura,previsao,conclusao,valor,bruto,atualizado,status_cod,doc4,cod_cliente,laudo,evento_em,garantia,itens,obs)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(codigo) DO UPDATE SET cliente=excluded.cliente,telefone=excluded.telefone,
                         equipamento=excluded.equipamento,serie=excluded.serie,problema=excluded.problema,status=excluded.status,
                         situacao=excluded.situacao,tecnico=excluded.tecnico,visita=excluded.visita,abertura=excluded.abertura,
                         previsao=excluded.previsao,conclusao=excluded.conclusao,valor=excluded.valor,bruto=excluded.bruto,
                         atualizado=excluded.atualizado,status_cod=excluded.status_cod,doc4=excluded.doc4,
                         cod_cliente=excluded.cod_cliente,laudo=excluded.laudo,evento_em=excluded.evento_em,garantia=excluded.garantia,
                         itens=excluded.itens,obs=excluded.obs""",
                      (o["codigo"], o["cliente"], o["telefone"], o["equipamento"], o["serie"], o["problema"], o["status"],
                       o["situacao"], o["tecnico"], o["visita"], o["abertura"], o["previsao"], o["conclusao"], o["valor"],
                       "", ts, o["status_cod"], o["doc4"], o["cod_cliente"], o["laudo"], o.get("evento_em"), o.get("garantia") or "", o["itens"], o["obs"]))
            if mudou:
                c.execute("INSERT INTO historico(codigo,situacao,status,quando) VALUES(?,?,?,?)",
                          (o["codigo"], o["situacao"], o["status"], ts))
                if not primeira and not completa and o["situacao"] in AVISOS:
                    mudancas.append((o["codigo"], AVISOS[o["situacao"]]))
        c.commit()
        if not config.DEMO and brutas:
            db.meta("ctm_max", max([int(db.meta("ctm_max") or 0)] + [uniplus._ctm(b) for b in brutas]))
        db.meta("primeira_sync_ok", "1")
        db.meta("ultima_sync", ts)
        db.meta("ultimo_erro", "")
        try:
            _telefones(c)
        except Exception:
            traceback.print_exc()
        for cod, tipo in mudancas:    # a 1a leitura e as leituras completas pedidas na tela nunca disparam avisos
            avisar(dict(c.execute("SELECT * FROM os WHERE codigo=?", (cod,)).fetchone()), tipo)
        return f"{len(brutas)} OS lidas, {len(mudancas)} avisos"
    except Exception as e:
        db.meta("ultimo_erro", f"{agora():%d/%m %H:%M} {type(e).__name__}: {e}"[:300])
        traceback.print_exc()
        return "erro: " + str(e)
    finally:
        _trava.release()

def _ja_enviado(c, codigo, tipo):
    return c.execute("SELECT 1 FROM aviso WHERE codigo=? AND tipo=?", (codigo, tipo)).fetchone() is not None

def lembretes():
    """Lembretes automaticos ao cliente: orcamento parado e equipamento pronto sem retirada. Cada um sai uma vez."""
    if config.DEMO or not whatsapp.ativo():
        return
    c = db.con()
    marcos = [int(x) for x in config.LEMBRETE_RETIRADA_DIAS.split(",") if x]
    for r in c.execute("SELECT * FROM os WHERE situacao IN ('aprovacao','retirada') AND telefone<>''").fetchall():
        o = dict(r)
        base = o["evento_em"] or o["abertura"]
        if not base:
            continue
        dias = (agora() - datetime.fromisoformat(base)).days
        nome = (o["cliente"] or "cliente").split()[0].title()
        tipos = []
        if o["situacao"] == "aprovacao" and config.LEMBRETE_ORCAMENTO_DIAS and dias >= config.LEMBRETE_ORCAMENTO_DIAS:
            tipos.append(("lembrete_orcamento", "lembrete_orcamento"))
        if o["situacao"] == "retirada":
            passados = [m for m in marcos if dias >= m]
            if passados:
                tipos.append(("lembrete_retirada", f"lembrete_retirada_{max(passados)}"))
        for modelo, marca in tipos:
            if _ja_enviado(c, o["codigo"], marca):
                continue
            ok, det = whatsapp.enviar_modelo(o["telefone"], modelo, [nome, o["codigo"], link_consulta(o["codigo"])])
            c.execute("INSERT INTO aviso(codigo,tipo,ok,detalhe,quando) VALUES(?,?,?,?,?)",
                      (o["codigo"], marca, int(ok), det, agora().isoformat(timespec="seconds")))
            c.commit()

def resumo_diario():
    """Resumo do dia no WhatsApp do responsavel, uma vez por dia apos a hora configurada."""
    if config.DEMO or not whatsapp.ativo() or not config.RESUMO_HORA or not config.RESUMO_TELEFONE:
        return
    hoje = agora().date().isoformat()
    if db.meta("resumo_dia") == hoje or agora().strftime("%H:%M") < config.RESUMO_HORA:
        return
    d = dados_painel()
    entraram = sum(1 for o in d["todas"] if (o["abertura"] or "")[:10] == hoje)
    sairam = sum(1 for o in d["todas"] if o["situacao"] == "finalizadas" and (o["conclusao"] or "")[:10] == hoje)
    vencidas = sum(1 for o in d["todas"] if o["situacao"] not in config.ENCERRADAS and o["sinal"]["cor"] == "vermelho")
    whatsapp.enviar_modelo(config.RESUMO_TELEFONE, "resumo", [agora().strftime("%d/%m"), entraram, sairam, d["abertas_total"], vencidas, d["ch"]["alertas"]])
    db.meta("resumo_dia", hoje)

def _laco():
    while True:
        sincronizar()
        for tarefa in (lembretes, resumo_diario):
            try:
                tarefa()
            except Exception:
                traceback.print_exc()
        for _ in range(max(1, config.SYNC_MINUTOS) * 6):   # relê o intervalo se a configuração mudar
            time.sleep(10)

threading.Thread(target=_laco, daemon=True).start()

# ---------------- acesso ----------------
def autorizado():
    """Login completo, ou a chave do telao -- esta so abre as telas em modo telao (nomes reduzidos, sem busca)."""
    if session.get("ok"):
        return True
    ch = request.args.get("chave", "")
    return (bool(config.CHAVE_TV) and request.args.get("tv") == "1" and request.method == "GET"
            and hmac.compare_digest(ch, config.CHAVE_TV))

@app.route("/")
def raiz():
    return redirect(url_for("painel"))

@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    if request.method == "POST":
        if LOGIN.bloqueado(ip()):
            erro = "Muitas tentativas. Aguarde 15 minutos."
        elif seguranca.confere(request.form.get("senha", ""), config.SENHA_PAINEL):
            if not seguranca.eh_hash(config.SENHA_PAINEL):           # instalacao antiga: passa a guardar so o hash
                try:
                    config.gravar_env({"SENHA_PAINEL": seguranca.gerar_hash(request.form["senha"])})
                except OSError:
                    pass
            LOGIN.limpar(ip())
            session.clear()
            session.permanent = True
            session["ok"] = True
            return redirect(url_for("painel"))
        else:
            LOGIN.falha(ip())
            time.sleep(1)
            erro = "Senha incorreta"
    return render_template("login.html", erro=erro, loja=config.NOME_LOJA)

def dono():
    """Valores em R$ e Configuracao: so com a senha master (sessao de 8 horas)."""
    return bool(session.get("ok")) and session.get("dono_ate", 0) > time.time()

@app.route("/dono", methods=["GET", "POST"])
def rota_dono():
    if not session.get("ok"):
        return redirect(url_for("login"))
    erro = None
    if request.method == "POST":
        if request.form.get("acao") == "ocultar":
            session.pop("dono_ate", None)
            return redirect(url_for("painel"))
        if not config.SENHA_DONO:
            erro = "A senha master ainda não foi definida. Defina na aba Configuração."
        elif LOGIN.bloqueado("dono:" + ip()):
            erro = "Muitas tentativas. Aguarde 15 minutos."
        elif seguranca.confere(request.form.get("senha", ""), config.SENHA_DONO):
            LOGIN.limpar("dono:" + ip())
            session["dono_ate"] = time.time() + 8 * 3600
            return redirect(url_for("painel"))
        else:
            LOGIN.falha("dono:" + ip())
            time.sleep(1)
            erro = "Senha incorreta."
    return render_template("dono.html", erro=erro, loja=config.NOME_LOJA, definida=bool(config.SENHA_DONO))

@app.route("/sair")
def sair():
    session.clear()
    return redirect(url_for("login"))

# ---------------- painel ----------------
def ranking(todas, c):
    """Desempenho por tecnico nos ultimos 90 dias (finalizadas) + situacao atual."""
    corte = (agora() - timedelta(days=90)).isoformat()
    notas = {r["codigo"]: r["nota"] for r in c.execute("SELECT codigo, nota FROM avaliacao")}
    por_cod = {o["codigo"]: o for o in todas}
    t = {}
    def reg(nome):
        return t.setdefault(nome or "(sem técnico)", {"nome": nome or "(sem técnico)", "fin": 0, "dias": [], "abertas": 0, "vencidas": 0, "garantia": 0, "notas": []})
    for o in todas:
        r = reg(o["tecnico"])
        if o["situacao"] == "finalizadas" and (o["conclusao"] or "") >= corte:
            r["fin"] += 1
            if o["abertura"]:
                r["dias"].append(max(0, (datetime.fromisoformat(o["conclusao"]) - datetime.fromisoformat(o["abertura"])).total_seconds() / 86400))
            if o["codigo"] in notas:
                r["notas"].append(notas[o["codigo"]])
        elif o["situacao"] not in config.ENCERRADAS:
            r["abertas"] += 1
            r["vencidas"] += o["sinal"]["cor"] == "vermelho"
        if o.get("garantia") and (o["abertura"] or "") >= corte:      # o retorno conta para quem fez o servico original, se informado
            orig = por_cod.get(re.sub(r"\D", "", o["garantia"]))
            reg(orig["tecnico"] if orig else o["tecnico"])["garantia"] += 1
    for r in t.values():
        r["tempo"] = sum(r["dias"]) / len(r["dias"]) if r["dias"] else None
        r["nota"] = sum(r["notas"]) / len(r["notas"]) if r["notas"] else None
    return sorted([r for r in t.values() if r["fin"] or r["abertas"] or r["garantia"]], key=lambda r: -r["fin"])

def resumo_garantia(todas):
    corte = (agora() - timedelta(days=365)).isoformat()
    ret = [o for o in todas if o.get("garantia") and (o["abertura"] or "") >= corte]
    fin = sum(1 for o in todas if o["situacao"] == "finalizadas" and (o["conclusao"] or "") >= corte)
    return {"n": len(ret), "pct": (len(ret) / fin * 100) if fin else None, "ligado": bool(config.GARANTIA_CAMPO)}

def dados_painel(metrica="qtd"):
    c = db.con()
    tudo = [dict(r) for r in c.execute("SELECT * FROM os")]
    todas = [enriquecer(o) for o in tudo if no_periodo(o)]
    hoje = agora()
    ini_mes = hoje.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    contagem = {k: 0 for k, _, _ in config.SITUACOES}
    for o in todas:
        if o["situacao"] in config.ENCERRADAS:        # encerradas: conta so as do mes
            ref = o["conclusao"] or o["evento_em"] or o["abertura"]      # so datas reais do Uniplus
            if ref and ref >= ini_mes:
                contagem[o["situacao"]] += 1
        else:
            contagem[o["situacao"]] = contagem.get(o["situacao"], 0) + 1
    tempos = [(datetime.fromisoformat(o["conclusao"]) - datetime.fromisoformat(o["abertura"])).total_seconds() / 86400
              for o in todas if o["situacao"] == "finalizadas" and o["conclusao"] and o["abertura"]
              and o["conclusao"] >= (hoje - timedelta(days=90)).isoformat()]
    fat_mes = sum(o["valor"] for o in todas if o["situacao"] == "finalizadas" and (o["conclusao"] or "") >= ini_mes)
    av = c.execute("SELECT COUNT(*) n, AVG(nota) m FROM avaliacao").fetchone()
    ult_av = [dict(r) for r in c.execute("SELECT * FROM avaliacao ORDER BY quando DESC LIMIT 5")]
    atrasadas = sorted([o for o in todas if o["atrasada"]], key=lambda o: -o["dias_atraso"])
    abertas_total = sum(1 for o in todas if o["situacao"] not in config.ENCERRADAS)
    pizza, acum = [], 0.0
    for k, t, _ in config.SITUACOES:
        if k in CORES_PIZZA and contagem[k]:
            p = contagem[k] / abertas_total * 100
            pizza.append({"t": t, "n": contagem[k], "p": p, "cor": CORES_PIZZA[k], "off": 25 - acum, "k": k})
            acum += p
    comp = comparativo(tudo, metrica)
    return dict(todas=todas, contagem=contagem, atrasadas=atrasadas, comp=comp, metrica=metrica,
                max_comp=max([1] + [max(s["a"], s["b"]) for s in comp]), tot_a=sum(s["a"] for s in comp), tot_b=sum(s["b"] for s in comp),
                pizza=pizza, ch=chamados.resumo(),
                legais=[o for o in todas if o["sinal"]["legal"]],
                terc=sorted([o for o in todas if o["situacao"] == "terceirizada"], key=lambda o: -(o["sinal"]["dias"] or 0)),
                tecnicos=ranking(todas, c), garantia=resumo_garantia(todas),
                tempo_medio=(sum(tempos) / len(tempos)) if tempos else None,
                fat_mes=fat_mes, meta=config.META_MES, abertas_total=abertas_total,
                nota_media=av["m"], n_aval=av["n"], ult_av=ult_av,
                ultima_sync=db.meta("ultima_sync"), ultimo_erro=db.meta("ultimo_erro"))

@app.route("/painel")
def painel():
    if not autorizado():
        return redirect(url_for("login"))
    eh_dono = dono() and request.args.get("tv") != "1"
    d = dados_painel("valor" if request.args.get("g") == "valor" and eh_dono else "qtd")
    sit, q = request.args.get("s", ""), request.args.get("q", "").strip().lower()
    lista = d["todas"]
    if sit == "atrasadas":
        lista = d["atrasadas"]
    elif sit == "legal":
        lista = d["legais"]
    elif sit:
        lista = [o for o in lista if o["situacao"] == sit]
    else:
        lista = [o for o in lista if o["situacao"] not in config.ENCERRADAS + ("terceirizada",)]   # terceirizada tem janela propria
    if q and session.get("ok"):
        lista = [o for o in lista if q in " ".join(str(o[k] or "") for k in ("codigo", "cliente", "equipamento", "serie", "telefone")).lower()]
    peso = {"vermelho": 0, "amarelo": 1, "verde": 2, "cinza": 3}
    lista = sorted(lista, key=lambda o: (peso[o["sinal"]["cor"]], -o["dias_atraso"], o["abertura"] or ""))
    return render_template("painel.html", d=d, lista=lista, sit=sit, q=q, tv=request.args.get("tv") == "1",
                           chave=request.args.get("chave", ""), situacoes=config.SITUACOES, loja=config.NOME_LOJA,
                           demo=config.DEMO, wa=whatsapp.ativo(), prazo=config.PRAZO_PADRAO_DIAS,
                           prazo_visita=config.PRAZO_VISITA_DIAS, hoje=agora(), cfg=config, dono=eh_dono)

@app.route("/terceirizada")
def terceirizada():
    if not autorizado():
        return redirect(url_for("login"))
    d = dados_painel()
    cont = {c: sum(1 for o in d["terc"] if o["sinal"]["cor"] == c) for c in ("verde", "amarelo", "vermelho")}
    return render_template("terceirizada.html", d=d, lista=d["terc"], cont=cont, tv=request.args.get("tv") == "1",
                           chave=request.args.get("chave", ""), loja=config.NOME_LOJA, cfg=config, hoje=agora())

@app.route("/sincronizar", methods=["POST"])
def rota_sincronizar():
    if not session.get("ok"):
        abort(403)
    sincronizar(completa=True)
    return redirect(url_for("painel"))

@app.route("/api/os.json")
def api_os():
    if not session.get("ok"):
        abort(403)
    campos = ("codigo", "cliente", "equipamento", "serie", "status", "situacao", "tecnico", "abertura", "atrasada", "dias_atraso") + (("valor",) if dono() else ())
    return jsonify([{k: o[k] for k in campos} for o in dados_painel()["todas"]])

# ---------------- consulta publica (QR da OS) ----------------
_tentativas = {}

def _bloqueado(ip):
    t = [x for x in _tentativas.get(ip, []) if time.time() - x < 600]
    _tentativas[ip] = t
    return len(t) >= 8

ETAPAS = ["abertas", "aprovacao", "pendentes", "retirada", "finalizadas"]
MENSAGEM = {
    "abertas": "Recebemos seu equipamento e ele está na fila de análise.",
    "aprovacao": "O orçamento está pronto e aguarda a sua aprovação.",
    "pecas": "Serviço aprovado. Estamos aguardando a chegada das peças.",
    "pendentes": "Seu serviço está em andamento com nosso técnico.",
    "terceirizada": "O equipamento foi enviado ao nosso parceiro especializado.",
    "retirada": "Tudo pronto! Seu equipamento está disponível para retirada.",
    "finalizadas": "Serviço finalizado e entregue. Obrigado pela preferência!",
    "canceladas": "Esta ordem de serviço foi cancelada.",
}

@app.route("/c", methods=["GET", "POST"])
@app.route("/c/", methods=["GET", "POST"])
def consulta_inicio():
    """Entrada da consulta quando o QR e fixo (modelos graficos): o cliente digita o numero da OS."""
    n = "".join(ch for ch in request.values.get("os", "") if ch.isdigit())[:12]
    if n:
        return redirect(url_for("consulta", codigo=str(int(n))))
    return render_template("consulta.html", estado="numero", loja=config.NOME_LOJA, zap=config.WHATSAPP_LOJA, codigo="")

def _qr_png(texto):
    import io, segno
    b = io.BytesIO()
    segno.make(texto, error="m").save(b, kind="png", scale=8, border=2)
    return b.getvalue(), 200, {"Content-Type": "image/png", "Cache-Control": "public, max-age=86400"}

@app.route("/qr.png")
def qr_fixo():
    return _qr_png(config.URL_PUBLICA + "/c")

@app.route("/qr/<codigo>.png")
def qr_os(codigo):
    """Imagem do QR impresso na OS (modelos iReport). So codifica o endereco da consulta; nao expoe dados."""
    if not codigo.isdigit() or len(codigo) > 12:
        abort(404)
    url = f"{config.URL_PUBLICA}/c/{int(codigo)}"
    if hmac.compare_digest(request.args.get("m", ""), chave_modelos()):
        url += "?k=" + passe(int(codigo))          # pedido veio de um modelo da loja: QR com acesso direto
    return _qr_png(url)

def _helpme_os(r):
    o = dict(r)
    try:
        itens = json.loads(o.get("itens") or "[]")
    except ValueError:
        itens = []
    return {"codigo": o["codigo"], "cliente": o["cliente"], "telefone": o["telefone"], "equipamento": o["equipamento"],
            "serie": o["serie"], "situacao": o["situacao"], "status": o["status"], "problema": o["problema"],
            "laudo": o.get("laudo") or "", "observacao": o.get("obs") or "", "itens": itens, "tecnico": o["tecnico"],
            "abertura": o["abertura"], "conclusao": o["conclusao"]}

@app.route("/api/helpme/os")
@app.route("/api/helpme/os/<codigo>")
def api_helpme(codigo=None):
    """Para o Help me!: busca OS por numero ou nome do cliente e devolve o NOME COMPLETO. Exige a chave da integracao."""
    if not hmac.compare_digest(request.headers.get("X-Api-Key", ""), chave_helpme()):
        abort(403)
    k = db.con()
    if codigo:
        r = k.execute("SELECT * FROM os WHERE codigo=?", (codigo,)).fetchone()
        if r is None:
            abort(404)
        return jsonify(_helpme_os(r))
    q = request.args.get("q", "").strip()[:60]
    if len(q) < 2:
        return jsonify([])
    rs = k.execute("SELECT * FROM os WHERE codigo=? OR cliente LIKE ? ORDER BY COALESCE(evento_em, abertura) DESC LIMIT 20", (q, f"%{q}%")).fetchall()
    return jsonify([_helpme_os(r) for r in rs])

@app.route("/c/<codigo>", methods=["GET", "POST"])
def consulta(codigo):
    r = db.con().execute("SELECT * FROM os WHERE codigo=?", (codigo,)).fetchone()
    base = dict(loja=config.NOME_LOJA, zap=config.WHATSAPP_LOJA, codigo=codigo)
    if r is None:
        return render_template("consulta.html", estado="inexistente", **base), 404
    o = enriquecer(r)
    liberado = session.get("c_" + codigo)
    if not liberado and request.args.get("k") and hmac.compare_digest(request.args["k"], passe(codigo)):
        session["c_" + codigo] = liberado = True       # veio pelo QR impresso na OS
    erro = None
    quem = ip()
    k = db.con()
    k.execute("DELETE FROM tentativa WHERE quando < ?", ((agora() - timedelta(hours=24)).isoformat(),))
    erradas_os = k.execute("SELECT COUNT(*) FROM tentativa WHERE codigo=?", (codigo,)).fetchone()[0]
    k.commit()
    if request.method == "POST" and not liberado:
        if _bloqueado(quem):
            erro = "Muitas tentativas. Aguarde alguns minutos."
        elif erradas_os >= 10:
            erro = "Consulta bloqueada por 24 horas por excesso de tentativas. Fale com a loja."
        elif any(v and hmac.compare_digest(request.form.get("fim", "").strip(), v)
                 for v in (o["telefone"][-4:] if len(o["telefone"]) >= 8 else "", o.get("doc4") or "")):
            session["c_" + codigo] = liberado = True
        else:
            _tentativas.setdefault(quem, []).append(time.time())
            k.execute("INSERT INTO tentativa VALUES(?,?)", (codigo, agora().isoformat()))
            k.commit()
            erro = "Os dígitos não conferem com o cadastro."
    if not liberado:
        return render_template("consulta.html", estado="pedir", erro=erro, sem_tel=len(o["telefone"]) < 8 and not o.get("doc4"), **base)
    fim_rep = (o.get("conclusao") or o.get("evento_em") or "")[:10]
    expirou = bool(config.PRAZO_CONSULTA_DIAS and o["situacao"] == "finalizadas" and fim_rep
                   and fim_rep < (agora() - timedelta(days=config.PRAZO_CONSULTA_DIAS)).strftime("%Y-%m-%d"))
    if expirou:
        return render_template("consulta.html", estado="expirada", dias=config.PRAZO_CONSULTA_DIAS, **base)
    try:
        itens_os = json.loads(o.get("itens") or "[]")
    except ValueError:
        itens_os = []
    etapa_ref = {"pecas": "pendentes", "terceirizada": "pendentes"}.get(o["situacao"], o["situacao"])
    pos = ETAPAS.index(etapa_ref) if etapa_ref in ETAPAS else -1
    hist = [dict(h) for h in db.con().execute("SELECT * FROM historico WHERE codigo=? ORDER BY id", (codigo,))]
    return render_template("consulta.html", estado="ok", o=o, itens=itens_os, dias=config.PRAZO_CONSULTA_DIAS, msg=MENSAGEM.get(o["situacao"], ""), pos=pos,
                           etapas=[TITULOS[e] for e in ETAPAS], hist=hist, titulos=TITULOS,
                           avaliar=link_avaliacao(codigo) if o["situacao"] == "finalizadas" else None, **base)

# ---------------- avaliacao ----------------
@app.route("/avaliar/<ref>", methods=["GET", "POST"])
def avaliar(ref):
    codigo, _, tk = ref.rpartition("-")
    if not codigo or not hmac.compare_digest(tk, token(codigo)):
        abort(404)
    c = db.con()
    if c.execute("SELECT 1 FROM os WHERE codigo=?", (codigo,)).fetchone() is None:
        abort(404)
    feita = c.execute("SELECT * FROM avaliacao WHERE codigo=?", (codigo,)).fetchone()
    if request.method == "POST" and not feita:
        try:
            nota = max(1, min(5, int(request.form.get("nota", "0"))))
        except ValueError:
            nota = 0
        if nota:
            c.execute("INSERT INTO avaliacao VALUES(?,?,?,?)",
                      (codigo, nota, request.form.get("comentario", "").strip()[:1000], agora().isoformat(timespec="seconds")))
            c.commit()
            feita = True
    return render_template("avaliar.html", codigo=codigo, feita=bool(feita), loja=config.NOME_LOJA)

chamados.registrar(app, autorizado, token, chave_helpme)
configuracao.registrar(app, sincronizar)

@app.route("/saude")
def saude():
    return jsonify(ok=True)

@app.template_filter("dt")
def f_dt(v, fmt="%d/%m %H:%M"):
    if not v:
        return "—"
    return (datetime.fromisoformat(v) if isinstance(v, str) else v).strftime(fmt)

@app.template_filter("brl")
def f_brl(v):
    return "R$ " + f"{v or 0:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
