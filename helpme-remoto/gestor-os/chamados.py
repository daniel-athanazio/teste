"""Chamados de suporte ao sistema (independente do Uniplus): tempos, aguardando cliente, avaliacao e avisos.

Integrado ao Help me! (acesso remoto): o pedido de ajuda feito pelo cliente na janela do Help me! vira chamado aqui
(triagem), o painel do Help me! le a fila de chamados abertos e, ao encerrar o atendimento remoto, o chamado e
atualizado com o resultado e o relatorio."""
import hmac
import json
from datetime import datetime, timedelta
from urllib.parse import quote
from flask import request, redirect, render_template, abort, session, jsonify
import config, db, whatsapp

ST = {"aberto": "Aberto", "atendimento": "Em atendimento", "aguardando": "Aguardando cliente",
      "concluido": "Concluído", "cancelado": "Cancelado"}
ATIVOS = ("aberto", "atendimento", "aguardando")
FLUXO = {"aberto": ["atendimento", "cancelado"], "atendimento": ["aguardando", "concluido", "cancelado"],
         "aguardando": ["atendimento", "concluido", "cancelado"], "concluido": [], "cancelado": []}

def iniciar():
    c = db.con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS chamado (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cliente TEXT, telefone TEXT, tecnico TEXT, problema TEXT,
        canal TEXT, tipo TEXT, modalidade TEXT, status TEXT, aberto_em TEXT, inicio_em TEXT, fim_em TEXT,
        solucao TEXT, nota INTEGER, comentario TEXT, avaliado_em TEXT, exemplo INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS chamado_hist (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chamado INTEGER, status TEXT, quando TEXT);
    CREATE TABLE IF NOT EXISTS chamado_remoto (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chamado INTEGER, sessao INTEGER, status TEXT, tecnico TEXT,
        duracao TEXT, resumo TEXT, relatorio TEXT, quando TEXT);
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(chamado)")}
    for nome in ("origem", "empresa", "equipamento", "numero_acesso", "helpme_equip", "helpme_pedido"):
        if nome not in cols:
            c.execute(f"ALTER TABLE chamado ADD COLUMN {nome} TEXT DEFAULT ''")
    c.commit()
    if config.DEMO and c.execute("SELECT COUNT(*) FROM chamado").fetchone()[0] == 0:
        _exemplos()

def _iso(d):
    return d.isoformat(timespec="seconds")

def _exemplos():
    c, a = db.con(), datetime.now()
    dados = [("Mercado Bom Preço", "21988887777", "Rafael", "PDV não emite NFC-e (rejeição 778)", "aberto", 55, []),
             ("Padaria Estrela", "21977776666", "Bruno", "Balança não comunica com o Uniplus", "atendimento", 140, [("atendimento", 120)]),
             ("Farmácia Vida", "21966665555", "Rafael", "Dúvida no fechamento de caixa", "aguardando", 300, [("atendimento", 280), ("aguardando", 200)]),
             ("Oficina do Zé", "21955554444", "Bruno", "Instalação do Uniplus em novo PC", "concluido", 1500, [("atendimento", 1470), ("concluido", 1380)]),
             ("Loja Central", "21944443333", "Rafael", "Erro ao importar XML de entrada", "concluido", 2900, [("atendimento", 2890), ("aguardando", 2850), ("atendimento", 2800), ("concluido", 2760)])]
    for i, (cli, tel, tec, prob, st, mins, hist) in enumerate(dados):
        ab = a - timedelta(minutes=mins)
        ini = next((a - timedelta(minutes=m) for s, m in hist if s == "atendimento"), None)
        fim = next((a - timedelta(minutes=m) for s, m in hist if s in ("concluido", "cancelado")), None)
        cur = c.execute("""INSERT INTO chamado(cliente,telefone,tecnico,problema,canal,tipo,modalidade,status,aberto_em,inicio_em,fim_em,
                        solucao,nota,comentario,exemplo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                        (cli, tel, tec, prob, "WhatsApp", "Erro no sistema", "Remoto", st, _iso(ab), ini and _iso(ini), fim and _iso(fim),
                         "Ajuste de configuração" if fim else None, (5, 4)[i % 2] if fim else None, "Atendimento rápido" if fim else None))
        for s, m in [("aberto", mins)] + hist:
            c.execute("INSERT INTO chamado_hist(chamado,status,quando) VALUES(?,?,?)", (cur.lastrowid, s, _iso(a - timedelta(minutes=m))))
    c.commit()

def fmt_min(m):
    if m is None:
        return "–"
    m = max(0, round(m))
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h {r:02d}" if h < 24 else f"{h // 24} d {h % 24} h"

def enriquecer(r, hist):
    c = dict(r)
    agora = datetime.now()
    ab = datetime.fromisoformat(c["aberto_em"])
    ini = datetime.fromisoformat(c["inicio_em"]) if c["inicio_em"] else None
    fim = datetime.fromisoformat(c["fim_em"]) if c["fim_em"] else agora
    mins = lambda a, b: (b - a).total_seconds() / 60
    aguard, desde = 0.0, None
    for h in hist:                       # soma os periodos em "aguardando cliente"
        q = datetime.fromisoformat(h["quando"])
        if desde is not None:
            aguard += mins(desde, q); desde = None
        if h["status"] == "aguardando":
            desde = q
    aguard_atual = mins(desde, fim) if desde is not None else 0
    aguard += aguard_atual
    c["espera"] = mins(ab, ini or fim)
    c["aguardando"] = aguard
    c["atend"] = max(0, mins(ini, fim) - aguard) if ini else None
    c["total"] = mins(ab, fim)
    c["rotulo"] = ST.get(c["status"], c["status"])
    c["alerta"] = ((c["status"] == "aberto" and c["espera"] > config.ALERTA_CHAMADO_MIN)
                   or (c["status"] == "aguardando" and aguard_atual > config.ALERTA_AGUARDANDO_HORAS * 60))
    c["motivo"] = ("sem atendimento há " + fmt_min(c["espera"])) if c["status"] == "aberto" else ("cliente sem responder há " + fmt_min(aguard_atual))
    c["acoes"] = FLUXO.get(c["status"], [])
    return c

def todos():
    c = db.con()
    hist = {}
    for h in c.execute("SELECT * FROM chamado_hist ORDER BY id"):
        hist.setdefault(h["chamado"], []).append(h)
    return [enriquecer(r, hist.get(r["id"], [])) for r in c.execute("SELECT * FROM chamado")]

def resumo():
    l = todos()
    return {"abertos": sum(c["status"] == "aberto" for c in l), "atendimento": sum(c["status"] == "atendimento" for c in l),
            "aguardando": sum(c["status"] == "aguardando" for c in l), "alertas": sum(c["alerta"] for c in l)}

def texto(c, link):
    nome, n = (c["cliente"] or "cliente").split()[0].title(), f"#{c['id']}"
    return {
        "aberto": f'Olá, {nome}! Recebemos seu chamado {n}: "{c["problema"]}". Já vamos atender. Acompanhe: {link}',
        "atendimento": f'{nome}, o atendimento do chamado {n} começou{" com " + c["tecnico"] if c["tecnico"] else ""}. Acompanhe: {link}',
        "aguardando": f"{nome}, para continuar o chamado {n} precisamos de um retorno seu. Pode nos responder por aqui?",
        "concluido": f'{nome}, o chamado {n} foi concluído.\nSolução: {c["solucao"] or "–"}\n\nComo foi o atendimento? Avalie em: {link}',
        "cancelado": f"{nome}, o chamado {n} foi cancelado. Se ainda precisar, é só nos chamar.",
    }.get(c["status"], "")

def registrar(app, autorizado, token, chave_helpme=None):
    iniciar()
    link = lambda i: f"{config.URL_PUBLICA}/ch/{i}-{token('ch' + str(i))}"

    def notificar(cid):
        """Envia pela API oficial; se estiver desligada ou falhar, a tela oferece o envio manual."""
        c = next(x for x in todos() if x["id"] == cid)
        tipo = "ch_" + c["status"]
        ok, det = (False, "sem modelo")
        if tipo in config.WA_MODELOS:
            ok, det = whatsapp.enviar_modelo(c["telefone"], tipo, [(c["cliente"] or "cliente").split()[0].title(), c["id"], link(cid)])
        k = db.con()
        k.execute("INSERT INTO aviso(codigo,tipo,ok,detalhe,quando) VALUES(?,?,?,?,?)",
                  (f"CH{cid}", tipo, int(ok), det, _iso(datetime.now())))
        k.commit()
        return ok

    @app.route("/chamados")
    def ch_lista():
        if not autorizado():
            return redirect("/login")
        l, f, agora = todos(), request.args.get("f", "ativos"), datetime.now()
        vis = [c for c in l if (c["status"] in ATIVOS if f == "ativos" else f == "todos" or c["status"] == f)]
        vis.sort(key=lambda c: (not c["alerta"], c["aberto_em"]) if f == "ativos" else (c["aberto_em"],), reverse=f != "ativos")
        conc = [c for c in l if c["status"] == "concluido"]
        med = lambda v: (sum(v) / len(v)) if v else None
        notas = [c["nota"] for c in conc if c["nota"]]
        dias = []
        for i in range(13, -1, -1):
            d = (agora - timedelta(days=i)).date().isoformat()
            dias.append({"r": d[8:] + "/" + d[5:7], "a": sum(c["aberto_em"][:10] == d for c in l),
                         "b": sum((c["fim_em"] or "")[:10] == d and c["status"] == "concluido" for c in l)})
        msg = None
        if request.args.get("msg"):
            m = next((c for c in l if str(c["id"]) == request.args["msg"]), None)
            if m:
                t = texto(m, link(m["id"]))
                tel = "".join(x for x in m["telefone"] or "" if x.isdigit())
                tel = tel if tel.startswith("55") and len(tel) > 11 else "55" + tel
                msg = {"c": m, "texto": t, "wa": f"https://wa.me/{tel}?text={quote(t)}", "auto": request.args.get("auto") == "1"}
        return render_template("chamados.html", lista=vis, f=f, msg=msg, st=ST, fm=fmt_min, loja=config.NOME_LOJA,
                               tv=request.args.get("tv") == "1", chave=request.args.get("chave", ""), dias=dias,
                               max_dia=max([1] + [max(d["a"], d["b"]) for d in dias]),
                               dist=[sum(n == i for n in notas) for i in (5, 4, 3, 2, 1)], n_notas=len(notas),
                               k={"abertos": sum(c["status"] == "aberto" for c in l), "atend": sum(c["status"] == "atendimento" for c in l),
                                  "aguard": sum(c["status"] == "aguardando" for c in l), "alertas": sum(c["alerta"] for c in l),
                                  "hoje": sum((c["fim_em"] or "")[:10] == agora.date().isoformat() for c in conc),
                                  "espera": fmt_min(med([c["espera"] for c in conc])), "atendm": fmt_min(med([c["atend"] for c in conc if c["atend"] is not None])),
                                  "nota": med(notas), "ativos": sum(c["status"] in ATIVOS for c in l), "conc": len(conc)},
                               wa=whatsapp.ativo(), alerta_min=config.ALERTA_CHAMADO_MIN, alerta_h=config.ALERTA_AGUARDANDO_HORAS,
                               tem_exemplo=any(c["exemplo"] for c in l))

    @app.route("/chamados/novo", methods=["POST"])
    def ch_novo():
        if not session.get("ok"):
            abort(403)
        f, agora, k = request.form, _iso(datetime.now()), db.con()
        cur = k.execute("""INSERT INTO chamado(cliente,telefone,tecnico,problema,canal,tipo,modalidade,status,aberto_em)
                        VALUES(?,?,?,?,?,?,?,'aberto',?)""",
                        (f.get("cliente", "").strip()[:120], "".join(x for x in f.get("telefone", "") if x.isdigit()), f.get("tecnico", "").strip()[:60],
                         f.get("problema", "").strip()[:1000], f.get("canal", "WhatsApp"), f.get("tipo", ""), f.get("modalidade", "Remoto"), agora))
        k.execute("INSERT INTO chamado_hist(chamado,status,quando) VALUES(?,?,?)", (cur.lastrowid, "aberto", agora))
        k.commit()
        return redirect(f"/chamados?msg={cur.lastrowid}&auto={int(notificar(cur.lastrowid))}")

    @app.route("/chamados/<int:cid>/mover", methods=["POST"])
    def ch_mover(cid):
        if not session.get("ok"):
            abort(403)
        k, novo, agora = db.con(), request.form.get("status", ""), _iso(datetime.now())
        r = k.execute("SELECT * FROM chamado WHERE id=?", (cid,)).fetchone()
        if r is None or novo not in FLUXO.get(r["status"], []):
            abort(400)
        k.execute("UPDATE chamado SET status=?, inicio_em=COALESCE(inicio_em,?), fim_em=?, solucao=COALESCE(?,solucao), tecnico=COALESCE(NULLIF(?,''),tecnico) WHERE id=?",
                  (novo, agora if novo == "atendimento" else None, agora if novo in ("concluido", "cancelado") else None,
                   request.form.get("solucao", "").strip()[:1000] or None, request.form.get("tecnico", "").strip()[:60], cid))
        k.execute("INSERT INTO chamado_hist(chamado,status,quando) VALUES(?,?,?)", (cid, novo, agora))
        k.commit()
        return redirect(f"/chamados?msg={cid}&auto={int(notificar(cid))}")

    @app.route("/chamados/apagar-exemplos", methods=["POST"])
    def ch_apagar():
        if not session.get("ok"):
            abort(403)
        k = db.con()
        k.execute("DELETE FROM chamado_hist WHERE chamado IN (SELECT id FROM chamado WHERE exemplo=1)")
        k.execute("DELETE FROM chamado WHERE exemplo=1")
        k.commit()
        return redirect("/chamados")

    @app.route("/ch/<ref>", methods=["GET", "POST"])
    def ch_publico(ref):
        cid, _, tk = ref.rpartition("-")
        if not cid.isdigit() or not hmac.compare_digest(tk, token("ch" + cid)):
            abort(404)
        c = next((x for x in todos() if x["id"] == int(cid)), None)
        if c is None:
            abort(404)
        if request.method == "POST" and c["status"] == "concluido" and not c["nota"]:
            try:
                nota = max(1, min(5, int(request.form.get("nota", "0"))))
            except ValueError:
                nota = 0
            if nota:
                k = db.con()
                k.execute("UPDATE chamado SET nota=?, comentario=?, avaliado_em=? WHERE id=?",
                          (nota, request.form.get("comentario", "").strip()[:1000], _iso(datetime.now()), int(cid)))
                k.commit()
                c["nota"] = nota
        return render_template("chamado_publico.html", c=c, fm=fmt_min, loja=config.NOME_LOJA, zap=config.WHATSAPP_LOJA)

    # ---------------- integracao com o Help me! (acesso remoto) ----------------
    def api_ok():
        if chave_helpme is None:
            return False
        ch = chave_helpme()
        dado = request.headers.get("X-Api-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(dado) and hmac.compare_digest(dado, ch)

    def abrir(cliente, telefone, problema, extra, tecnico="", status="aberto"):
        k, agora = db.con(), _iso(datetime.now())
        cur = k.execute("""INSERT INTO chamado(cliente,telefone,tecnico,problema,canal,tipo,modalidade,status,aberto_em,
                        origem,empresa,equipamento,numero_acesso,helpme_equip,helpme_pedido)
                        VALUES(?,?,?,?,'Help me!',?,'Remoto','aberto',?,'helpme',?,?,?,?,?)""",
                        (cliente[:120], "".join(x for x in telefone or "" if x.isdigit()), tecnico[:60], problema[:1000],
                         extra.get("tipo", "Pedido pelo Help me!"), agora, extra.get("empresa", "")[:120],
                         extra.get("equipamento", "")[:120], extra.get("numero_acesso", "")[:20],
                         str(extra.get("helpme_equip_id", "")), str(extra.get("helpme_pedido_id", ""))))
        cid = cur.lastrowid
        k.execute("INSERT INTO chamado_hist(chamado,status,quando) VALUES(?,?,?)", (cid, "aberto", agora))
        k.commit()
        return cid

    def mover(cid, novo, tecnico="", solucao=""):
        """Segue o fluxo do chamado (aberto -> atendimento -> concluido); ignora o que nao se aplica."""
        k = db.con()
        r = k.execute("SELECT * FROM chamado WHERE id=?", (cid,)).fetchone()
        if r is None:
            return False
        caminho = [novo]
        if novo == "concluido" and r["status"] == "aberto":
            caminho = ["atendimento", "concluido"]
        mudou = False
        for st in caminho:
            atual = k.execute("SELECT status FROM chamado WHERE id=?", (cid,)).fetchone()["status"]
            if st not in FLUXO.get(atual, []):
                continue
            agora = _iso(datetime.now())
            k.execute("UPDATE chamado SET status=?, inicio_em=COALESCE(inicio_em,?), fim_em=?, solucao=COALESCE(?,solucao), "
                      "tecnico=COALESCE(NULLIF(?,''),tecnico) WHERE id=?",
                      (st, agora if st == "atendimento" else None, agora if st in ("concluido", "cancelado") else None,
                       solucao[:1000] or None, tecnico[:60], cid))
            k.execute("INSERT INTO chamado_hist(chamado,status,quando) VALUES(?,?,?)", (cid, st, agora))
            k.commit()
            mudou = True
        if mudou:
            try:
                notificar(cid)
            except Exception:
                pass
        return mudou

    @app.route("/api/helpme/chamados", methods=["GET", "POST"])
    def api_hm_chamados():
        if not api_ok():
            abort(403)
        if request.method == "POST":   # pedido de ajuda feito pelo cliente na janela do Help me!
            d = request.get_json(silent=True) or {}
            problema = str(d.get("problema", "")).strip()
            if len(problema) < 3:
                return jsonify(erro="problema vazio"), 422
            cliente = str(d.get("cliente") or d.get("empresa") or "Cliente").strip()
            if d.get("empresa") and d.get("empresa") not in cliente:
                cliente = f"{cliente} ({d['empresa']})"
            texto = problema + (f"\n[Equipamento: {d.get('equipamento')} · nº de acesso {d.get('numero_acesso')}]"
                                if d.get("equipamento") else "")
            cid = abrir(cliente, str(d.get("telefone", "")), texto, d)
            try:
                notificar(cid)
            except Exception:
                pass
            return jsonify(id=cid, link=link(cid))
        l = [c for c in todos() if c["status"] in ATIVOS]
        l.sort(key=lambda c: (not c["alerta"], c["aberto_em"]))
        return jsonify([{"id": c["id"], "cliente": c["cliente"], "telefone": c["telefone"], "tecnico": c["tecnico"],
                         "problema": c["problema"], "status": c["status"], "rotulo": c["rotulo"], "alerta": c["alerta"],
                         "espera": fmt_min(c["total"]), "origem": c.get("origem") or "",
                         "helpme_equip": c.get("helpme_equip") or "",
                         "link": f"{config.URL_PUBLICA}/chamados?f=todos#c{c['id']}"} for c in l[:100]])

    @app.route("/api/helpme/chamados/<int:cid>/status", methods=["POST"])
    def api_hm_status(cid):
        if not api_ok():
            abort(403)
        d = request.get_json(silent=True) or {}
        return jsonify(ok=mover(cid, str(d.get("status", "")), str(d.get("tecnico", "")), str(d.get("solucao", ""))))

    @app.route("/api/helpme/webhook", methods=["POST"])
    def api_hm_webhook():
        """Fim de um atendimento remoto: atualiza (ou cria) o chamado e guarda o relatorio."""
        if not api_ok():
            abort(403)
        d = request.get_json(silent=True) or {}
        if d.get("evento") == "teste":
            return jsonify(ok=True, loja=config.NOME_LOJA)
        if d.get("evento") != "atendimento_remoto_encerrado":
            return jsonify(ok=True, ignorado=True)
        tec = (d.get("tecnico") or {}).get("nome") or ""
        cid = d.get("chamado_id")
        k = db.con()
        if cid and k.execute("SELECT 1 FROM chamado WHERE id=?", (cid,)).fetchone() is None:
            cid = None
        if not cid:   # atendimento sem chamado: registra para o historico e os indicadores
            emp = (d.get("empresa") or {}).get("nome") or "Cliente"
            pa = d.get("pedido_ajuda") or {}
            eq = d.get("equipamento") or {}
            cid = abrir(pa.get("nome") or emp, pa.get("telefone", ""),
                        pa.get("problema") or d.get("motivo") or f"Atendimento remoto ({d.get('tipo_problema') or 'suporte'})",
                        {"tipo": d.get("tipo_problema") or "Atendimento remoto", "empresa": emp,
                         "equipamento": eq.get("nome", ""), "numero_acesso": eq.get("numero_acesso", ""),
                         "helpme_equip_id": d.get("equipamento_id", "")}, tecnico=tec)
        nota = (d.get("nota") or "").strip()
        if d.get("status") == "concluido":
            mover(cid, "concluido", tec, nota or f"Resolvido por acesso remoto ({d.get('duracao')}).")
        else:
            mover(cid, "atendimento", tec)
            if nota:
                k.execute("UPDATE chamado SET solucao=? WHERE id=?", ("Pendente: " + nota[:990], cid))
        resumo = {x: d.get(x) for x in ("sessao_id", "os_numero", "tipo_problema", "motivo", "nota", "inicio", "fim",
                                         "duracao", "procedimentos", "plugins", "tecnico", "equipamento", "empresa")}
        k.execute("INSERT INTO chamado_remoto(chamado,sessao,status,tecnico,duracao,resumo,relatorio,quando) "
                  "VALUES(?,?,?,?,?,?,?,?)", (cid, d.get("sessao_id"), d.get("status"), tec, d.get("duracao"),
                                              json.dumps(resumo, ensure_ascii=False)[:200000],
                                              (d.get("relatorio_html") or "")[:5_000_000], _iso(datetime.now())))
        k.commit()
        return jsonify(ok=True, chamado_id=cid)

    @app.route("/chamados/<int:cid>/remoto/<int:rid>")
    def ch_relatorio_remoto(cid, rid):
        if not session.get("ok"):
            return redirect("/login")
        r = db.con().execute("SELECT relatorio FROM chamado_remoto WHERE id=? AND chamado=?", (rid, cid)).fetchone()
        if r is None or not r["relatorio"]:
            abort(404)
        return r["relatorio"], 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.context_processor
    def _remotos():
        def remotos(cid):
            return db.con().execute("SELECT id,sessao,status,tecnico,duracao,quando FROM chamado_remoto WHERE chamado=? "
                                    "ORDER BY id", (cid,)).fetchall()
        return {"remotos": remotos, "helpme_url": config.HELPME_URL}
