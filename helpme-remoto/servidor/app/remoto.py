"""Help me! - tela remota pelo navegador, chat, pedidos de ajuda, fila de atendimento e site de downloads.

Tela remota: o técnico abre /remoto no navegador; o servidor retransmite os quadros entre o navegador e o
processo de captura no computador do cliente (WebSocket). Só funciona dentro de um atendimento liberado
pelo cliente (autorizado na tela dele ou acesso contínuo aceito) e cada acesso fica no relatório.
"""
import asyncio
import html
import json
import re
import secrets
import struct
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import main as M
from .main import (AGENTE_ONLINE_SEG, CHAVE_PUBLICA_B64, CONTATO, EMPRESA, STATIC, URL_PUBLICA, _sessao, acordar,
                   agora, app, auditar, db, enviar_mensagem, equipamento_atual, fmt_numero, gestor_req, iso, local,
                   marca_atual, sha256, tecnico_atual)

AGENTE_EXE = Path(__import__("os").environ.get("FN_AGENTE_EXE", "/srv/agente/HelpMe.exe"))
TICKET_SEG = 60
GRACA_SEG = 30          # o técnico pode recarregar a página sem derrubar o acesso


@app.on_event("startup")
async def _guardar_loop():
    M.LOOP = asyncio.get_running_loop()


def _equip_por_token(authorization: str):
    tok = (authorization or "").removeprefix("Bearer ").strip()
    if not tok:
        return None
    with db() as con:
        return con.execute("SELECT * FROM equipamentos WHERE token_hash=?", (sha256(tok),)).fetchone()


# ---------------------------------------------------------------- canal de controle do agente
@app.websocket("/ws/agente")
async def ws_agente(ws: WebSocket):
    eq = await asyncio.to_thread(_equip_por_token, ws.headers.get("authorization", ""))
    await ws.accept()
    if not eq:
        await ws.close(4401)
        return
    M.CONTROLE.setdefault(eq["id"], set()).add(ws)
    try:
        while True:  # o agente não manda nada; só detecta a queda
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        M.CONTROLE.get(eq["id"], set()).discard(ws)


# ---------------------------------------------------------------- tela remota
@dataclass
class Canal:
    id: str
    sessao_id: int
    equip_id: int
    tecnico: str
    criado: float = field(default_factory=time.time)
    tickets: dict = field(default_factory=dict)
    agente: WebSocket | None = None
    viewers: dict = field(default_factory=dict)  # WebSocket -> identificação do técnico
    acesso_id: int | None = None
    pedido_em: float = 0
    fechado: bool = False
    fechar_task: asyncio.Task | None = None


CANAIS: dict[str, Canal] = {}
_canais_lock = threading.Lock()


def _canal_da_sessao(sid: int) -> Canal | None:
    with _canais_lock:
        return next((c for c in CANAIS.values() if c.sessao_id == sid and not c.fechado), None)


def _canais_do_equip(eid: int) -> list[Canal]:
    with _canais_lock:
        return [c for c in CANAIS.values() if c.equip_id == eid and not c.fechado]


@app.post("/api/sessoes/{sid}/remoto")
def abrir_remoto(sid: int, t=Depends(tecnico_atual)):
    """Gera o ingresso (uso único, 60 s) para o navegador do técnico entrar na tela do cliente."""
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Atendimento encerrado")
        if s["liberacao"] not in ("autorizada", "notificado"):
            raise HTTPException(409, "O cliente ainda não autorizou o atendimento")
        e = con.execute("SELECT ultimo_contato FROM equipamentos WHERE id=?", (s["equipamento_id"],)).fetchone()
        if not e["ultimo_contato"] or (agora() - datetime.fromisoformat(e["ultimo_contato"])).total_seconds() > 90:
            raise HTTPException(409, "O computador do cliente está desligado ou sem internet")
        ident = M.ident_tecnico(t)
        with _canais_lock:
            c = next((x for x in CANAIS.values() if x.sessao_id == sid and not x.fechado), None)
            if not c:
                c = Canal(secrets.token_urlsafe(24), sid, s["equipamento_id"], ident)
                CANAIS[c.id] = c
            ticket = secrets.token_urlsafe(24)
            c.tickets[ticket] = (t["id"], ident, time.time() + TICKET_SEG)
            pedir = c.agente is None and time.time() - c.pedido_em > 10
            if pedir:
                c.pedido_em = time.time()
        if pedir:  # comando assinado: o agente abre a captura na sessão do usuário
            enviar_mensagem(con, s["equipamento_id"], "remoto", {"canal": c.id, "tecnico": ident,
                                                                  "sessao_id": str(sid)}, sessao_id=sid, validade_min=2)
        auditar(con, t["usuario"], "tela_remota_solicitada", f"sessao {sid}")
    return {"canal": c.id, "ticket": ticket}


async def _broadcast(c: Canal, msg, binario=False):
    for v in list(c.viewers):
        try:
            if binario:
                await v.send_bytes(msg)
            else:
                await v.send_text(msg)
        except Exception:
            c.viewers.pop(v, None)


def _broadcast_threadsafe(c: Canal, obj: dict):
    if M.LOOP:
        asyncio.run_coroutine_threadsafe(_broadcast(c, json.dumps(obj, ensure_ascii=False)), M.LOOP)


async def _fechar_ws(ws: WebSocket | None, codigo: int, motivo: str):
    if ws is None:
        return
    try:
        await ws.close(codigo, motivo[:100])
    except Exception:
        pass


async def _fechar_canal_async(c: Canal, motivo: str):
    if c.agente:
        try:
            await c.agente.send_text(json.dumps({"t": "fim"}))
        except Exception:
            pass
    await _broadcast(c, json.dumps({"t": "fim", "motivo": motivo}, ensure_ascii=False))
    await _fechar_ws(c.agente, 1000, motivo)
    for v in list(c.viewers):
        await _fechar_ws(v, 4000, motivo)
    with _canais_lock:
        CANAIS.pop(c.id, None)


def _registrar_fim(con, c: Canal, motivo: str):
    """Fecha o registro do acesso e manda o agente parar a captura (comando assinado)."""
    if c.acesso_id:
        con.execute("UPDATE acessos_tela SET fim=?, encerrado_por=? WHERE id=? AND fim IS NULL",
                    (iso(), motivo[:120], c.acesso_id))
    enviar_mensagem(con, c.equip_id, "remoto_fim", {"canal": c.id}, sessao_id=c.sessao_id, validade_min=10)


def fechar_canal(con, c: Canal, motivo: str):
    """Chamado de rotas síncronas (dentro de db())."""
    with _canais_lock:
        if c.fechado:
            return
        c.fechado = True
    _registrar_fim(con, c, motivo)
    if M.LOOP:
        asyncio.run_coroutine_threadsafe(_fechar_canal_async(c, motivo), M.LOOP)


def encerrar_canais_da_sessao(con, sid: int, motivo: str):
    with _canais_lock:
        lst = [c for c in CANAIS.values() if c.sessao_id == sid and not c.fechado]
    for c in lst:
        fechar_canal(con, c, motivo)


async def _fechar_canal_db(c: Canal, motivo: str):
    with _canais_lock:
        if c.fechado:
            return
        c.fechado = True

    def _db():
        with db() as con:
            _registrar_fim(con, c, motivo)
    await asyncio.to_thread(_db)
    await _fechar_canal_async(c, motivo)


async def _fechar_se_vazio(c: Canal):
    await asyncio.sleep(GRACA_SEG)
    if not c.viewers and not c.fechado:
        await _fechar_canal_db(c, "técnico saiu da tela")


@app.websocket("/ws/remoto/agente")
async def ws_remoto_agente(ws: WebSocket, canal: str):
    eq = await asyncio.to_thread(_equip_por_token, ws.headers.get("authorization", ""))
    await ws.accept()
    c = CANAIS.get(canal)
    if not eq or not c or c.fechado or c.equip_id != eq["id"]:
        await ws.close(4403, "canal inválido")
        return
    if c.agente:
        await _fechar_ws(c.agente, 4001, "substituído")
    c.agente = ws
    if not c.acesso_id:
        def _ini():
            with db() as con:
                return con.execute("INSERT INTO acessos_tela(sessao_id,equipamento_id,tecnico_ident,inicio) "
                                   "VALUES(?,?,?,?)", (c.sessao_id, c.equip_id, c.tecnico, iso())).lastrowid
        c.acesso_id = await asyncio.to_thread(_ini)
    await _broadcast(c, json.dumps({"t": "estado", "agente": True}))
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if m.get("bytes") is not None:
                await _broadcast(c, m["bytes"], binario=True)
            elif m.get("text") is not None:
                await _broadcast(c, m["text"])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if c.agente is ws:
            c.agente = None
            if not c.fechado:
                await _broadcast(c, json.dumps({"t": "estado", "agente": False}))


@app.websocket("/ws/remoto/tecnico")
async def ws_remoto_tecnico(ws: WebSocket, canal: str, ticket: str):
    await ws.accept()
    c = CANAIS.get(canal)
    tk = c.tickets.pop(ticket, None) if c else None
    if not c or c.fechado or not tk or tk[2] < time.time():
        await ws.close(4403, "ingresso inválido ou vencido")
        return
    tec_id, ident, _ = tk
    c.viewers[ws] = ident
    if c.fechar_task:
        c.fechar_task.cancel()
        c.fechar_task = None

    def _info():
        with db() as con:
            s = _sessao(con, c.sessao_id)
            hist = con.execute("SELECT de,tecnico,texto,criado_em FROM chat WHERE sessao_id=? ORDER BY id",
                               (c.sessao_id,)).fetchall()
            return {"t": "info", "sessao": c.sessao_id, "equipamento": s["equip_nome"], "grupo": s["grupo_nome"],
                    "numero": fmt_numero(s["numero_acesso"]), "tecnico": ident, "agente": c.agente is not None,
                    "outros": [v for w, v in c.viewers.items() if w is not ws],
                    "chat": [{"de": h["de"], "tecnico": bool(h["tecnico"]), "texto": h["texto"],
                              "quando": h["criado_em"]} for h in hist]}
    await ws.send_text(json.dumps(await asyncio.to_thread(_info), ensure_ascii=False))
    await _broadcast(c, json.dumps({"t": "viewer", "nome": ident, "entrou": True}, ensure_ascii=False))
    if c.agente:
        try:
            await c.agente.send_text(json.dumps({"t": "refresh"}))
        except Exception:
            pass
    try:
        while True:
            texto = await ws.receive_text()
            if len(texto) > 300_000:
                continue
            try:
                j = json.loads(texto)
            except ValueError:
                continue
            if j.get("t") == "chat":  # conversa com o cliente: vai pela janela do Help me!, não pela captura
                msg = str(j.get("texto", "")).strip()[:2000]
                if msg:
                    await asyncio.to_thread(salvar_chat_tecnico, c.sessao_id, c.equip_id, ident, msg)
                continue
            if c.agente:
                try:
                    await c.agente.send_text(texto)
                except Exception:
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        c.viewers.pop(ws, None)
        if not c.fechado:
            await _broadcast(c, json.dumps({"t": "viewer", "nome": ident, "entrou": False}, ensure_ascii=False))
            if not c.viewers:
                c.fechar_task = asyncio.create_task(_fechar_se_vazio(c))


@app.get("/remoto", response_class=HTMLResponse)
def pagina_remoto():
    return HTMLResponse((STATIC / "remoto.html").read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


# ---------------------------------------------------------------- chat
def salvar_chat_tecnico(sid: int, eid: int, ident: str, texto: str):
    with db() as con:
        con.execute("INSERT INTO chat(equipamento_id,sessao_id,de,tecnico,texto,criado_em) VALUES(?,?,?,1,?,?)",
                    (eid, sid, ident, texto, iso()))
    for c in _canais_do_equip(eid):
        _broadcast_threadsafe(c, {"t": "chat", "de": ident, "tecnico": True, "texto": texto, "quando": iso()})
    acordar(eid)


class ChatIn(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)


@app.get("/api/sessoes/{sid}/chat")
def ver_chat(sid: int, _=Depends(tecnico_atual)):
    with db() as con:
        rows = con.execute("SELECT de,tecnico,texto,criado_em,entregue_em FROM chat WHERE sessao_id=? ORDER BY id",
                           (sid,)).fetchall()
    return [dict(r) | {"tecnico": bool(r["tecnico"])} for r in rows]


@app.post("/api/sessoes/{sid}/chat")
def enviar_chat(sid: int, d: ChatIn, t=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Atendimento encerrado")
    salvar_chat_tecnico(sid, s["equipamento_id"], M.ident_tecnico(t), d.texto.strip())
    return {"ok": True}


def chat_para_agente(con, eid: int) -> list[dict]:
    rows = con.execute("SELECT id,de,texto,criado_em FROM chat WHERE equipamento_id=? AND tecnico=1 "
                       "AND entregue_em IS NULL ORDER BY id", (eid,)).fetchall()
    for r in rows:
        con.execute("UPDATE chat SET entregue_em=? WHERE id=?", (iso(), r["id"]))
    return [{"id": r["id"], "de": r["de"], "tecnico": True, "texto": r["texto"], "quando": r["criado_em"]}
            for r in rows]


def estado_para_agente(con, eid: int) -> dict:
    e = con.execute("SELECT e.*, g.nome grupo_nome FROM equipamentos e LEFT JOIN grupos g ON g.id=e.grupo_id "
                    "WHERE e.id=?", (eid,)).fetchone()
    s = con.execute("SELECT s.*, t.telefone FROM sessoes s JOIN tecnicos t ON t.id=s.tecnico_id WHERE "
                    "s.equipamento_id=? AND s.status='em_andamento' AND s.liberacao IN ('autorizada','notificado') "
                    "ORDER BY s.id DESC LIMIT 1", (eid,)).fetchone()
    tela = any(c.agente is not None for c in _canais_do_equip(eid))
    return {"sessao": {"id": s["id"], "tecnico": s["tecnico_ident"], "telefone": s["telefone"] or "",
                       "liberacao": s["liberacao"], "tipo": s["tipo_problema"] or "", "inicio": s["inicio"]} if s else None,
            "tela": tela, "continuo": e["continuo"], "modo": e["modo_acesso"], "empresa": EMPRESA, "contato": CONTATO,
            "grupo": e["grupo_nome"] or "", "nome": e["nome"] or "", "cor_marca": marca_atual()["cor_icone"]}


class ChatAgente(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)
    usuario: str = ""


@app.post("/agente/chat")
def chat_do_cliente(d: ChatAgente, eq=Depends(equipamento_atual)):
    usuario = d.usuario[:120] or eq["usuario_windows"] or "cliente"
    with db() as con:
        s = con.execute("SELECT id FROM sessoes WHERE equipamento_id=? AND status='em_andamento' ORDER BY id DESC",
                        (eq["id"],)).fetchone()
        con.execute("INSERT INTO chat(equipamento_id,sessao_id,de,tecnico,texto,criado_em,entregue_em) "
                    "VALUES(?,?,?,0,?,?,?)", (eq["id"], s["id"] if s else None, usuario, d.texto.strip(), iso(), iso()))
    for c in _canais_do_equip(eq["id"]):
        _broadcast_threadsafe(c, {"t": "chat", "de": usuario, "tecnico": False, "texto": d.texto.strip(), "quando": iso()})
    return {"ok": True}


class EncerrarAgente(BaseModel):
    usuario: str = ""


@app.post("/agente/remoto/encerrar")
def encerrar_pelo_cliente(d: EncerrarAgente, eq=Depends(equipamento_atual)):
    """O cliente clicou em Encerrar na janela do Help me!: a tela é desligada na hora."""
    with db() as con:
        for c in _canais_do_equip(eq["id"]):
            fechar_canal(con, c, f"encerrado pelo cliente ({d.usuario[:60]})")
        auditar(con, f"{eq['hostname']}\\{d.usuario}", "tela_encerrada_pelo_cliente")
    return {"ok": True}


@app.post("/agente/desinstalado")
def desinstalado(d: EncerrarAgente, eq=Depends(equipamento_atual)):
    with db() as con:
        auditar(con, f"{eq['hostname']}\\{d.usuario}", "agente_desinstalado", str(eq["id"]))
        con.execute("UPDATE equipamentos SET ultimo_contato=NULL WHERE id=?", (eq["id"],))
    return {"ok": True}


# ---------------------------------------------------------------- pedidos de ajuda e fila
class AjudaIn(BaseModel):
    nome: str = Field("", max_length=80)
    telefone: str = Field("", max_length=30)
    problema: str = Field(min_length=3, max_length=2000)
    usuario: str = ""


@app.post("/agente/ajuda")
def pedir_ajuda(d: AjudaIn, eq=Depends(equipamento_atual)):
    """O cliente pediu ajuda na janela: entra na fila do painel e vira chamado no gestor de OS (triagem)."""
    with db() as con:
        recentes = con.execute("SELECT COUNT(*) FROM pedidos_ajuda WHERE equipamento_id=? AND criado_em>?",
                               (eq["id"], iso(agora() - timedelta(hours=1)))).fetchone()[0]
        if recentes >= 5:
            raise HTTPException(429, "Muitos pedidos em pouco tempo. Aguarde o contato do técnico.")
        g = con.execute("SELECT * FROM grupos WHERE id=?", (eq["grupo_id"],)).fetchone() if eq["grupo_id"] else None
        tel = re.sub(r"\D", "", d.telefone)
        pid = con.execute("INSERT INTO pedidos_ajuda(equipamento_id,nome,telefone,problema,usuario,criado_em) "
                          "VALUES(?,?,?,?,?,?)", (eq["id"], d.nome.strip(), tel, d.problema.strip(), d.usuario[:120],
                                                  iso())).lastrowid
        auditar(con, f"{eq['hostname']}\\{d.usuario}", "pedido_ajuda", str(pid))
    protocolo, link, detalhe, chamado = f"P{pid}", "", "", None
    try:  # fora da trava do banco: o gestor pode demorar
        r = gestor_req("POST", "/api/helpme/chamados", {
            "cliente": d.nome.strip() or (g["nome"] if g else eq["hostname"]), "empresa": g["nome"] if g else "",
            "telefone": tel, "problema": d.problema.strip(), "equipamento": eq["nome"] or eq["hostname"],
            "numero_acesso": eq["numero_acesso"], "helpme_equip_id": eq["id"], "helpme_pedido_id": pid}, timeout=8)
        if r.status_code < 300:
            j = r.json()
            chamado, link = j.get("id"), j.get("link", "")
            protocolo = f"#{chamado}" if chamado else protocolo
        else:
            detalhe = f"gestor HTTP {r.status_code}"
    except Exception as e:
        detalhe = str(e)[:200]
    with db() as con:
        con.execute("UPDATE pedidos_ajuda SET chamado_id=?, chamado_link=?, gestor_detalhe=? WHERE id=?",
                    (chamado, link, detalhe, pid))
    return {"protocolo": protocolo, "link": link, "pedido_id": pid}


@app.get("/api/fila")
def fila(_=Depends(tecnico_atual)):
    """Pedidos de ajuda dos clientes + chamados abertos no gestor de OS (triagem)."""
    with db() as con:
        rows = con.execute("SELECT p.*, e.nome equip_nome, e.hostname, e.numero_acesso, e.ultimo_contato, e.grupo_id, "
                           "g.nome grupo_nome FROM pedidos_ajuda p JOIN equipamentos e ON e.id=p.equipamento_id "
                           "LEFT JOIN grupos g ON g.id=e.grupo_id WHERE p.status IN ('aberto','em_atendimento') "
                           "OR p.criado_em>? ORDER BY p.id DESC LIMIT 200", (iso(agora() - timedelta(days=2)),)).fetchall()
    pedidos = []
    for r in rows:
        d = dict(r)
        d["online"] = bool(r["ultimo_contato"]) and \
            (agora() - datetime.fromisoformat(r["ultimo_contato"])).total_seconds() < AGENTE_ONLINE_SEG
        d["numero_fmt"] = fmt_numero(r["numero_acesso"])
        pedidos.append(d)
    gestor, erro = [], ""
    try:
        r = gestor_req("GET", "/api/helpme/chamados", timeout=4)
        if r.status_code < 300:
            ligados = {p["chamado_id"] for p in pedidos if p["chamado_id"]}
            gestor = [c for c in r.json() if c.get("id") not in ligados]
        else:
            erro = f"gestor HTTP {r.status_code}"
    except RuntimeError:
        erro = "desligado"
    except Exception as e:
        erro = str(e)[:120]
    return {"pedidos": pedidos, "gestor": gestor, "gestor_erro": erro}


@app.post("/api/pedidos/{pid}/descartar")
def descartar_pedido(pid: int, t=Depends(tecnico_atual)):
    with db() as con:
        if not con.execute("UPDATE pedidos_ajuda SET status='descartado', atendido_por=?, atendido_em=? "
                           "WHERE id=? AND status='aberto'", (M.ident_tecnico(t), iso(), pid)).rowcount:
            raise HTTPException(409, "Pedido não está aberto")
        auditar(con, t["usuario"], "pedido_descartado", str(pid))
    return {"ok": True}


# ---------------------------------------------------------------- relatório
def html_tela_e_chat(con, sid: int) -> str:
    e = html.escape
    ac = con.execute("SELECT * FROM acessos_tela WHERE sessao_id=? ORDER BY id", (sid,)).fetchall()
    ch = con.execute("SELECT * FROM chat WHERE sessao_id=? ORDER BY id", (sid,)).fetchall()
    pa = con.execute("SELECT * FROM pedidos_ajuda WHERE sessao_id=?", (sid,)).fetchone()
    out = ""
    if pa:
        out += (f"<h2>Pedido de ajuda do cliente</h2><table class=kv><tr><td>Enviado em</td><td>{local(pa['criado_em'])}"
                f"</td></tr><tr><td>Cliente</td><td>{e(pa['nome'] or '-')} {e(pa['telefone'] or '')}</td></tr>"
                f"<tr><td>Problema</td><td>{e(pa['problema'])}</td></tr>"
                + (f"<tr><td>Chamado no gestor</td><td>#{pa['chamado_id']}</td></tr>" if pa["chamado_id"] else "")
                + "</table>")
    linhas = "".join(f"<tr><td>{e(a['tecnico_ident'])}</td><td>{local(a['inicio'])}</td><td>{local(a['fim'])}</td>"
                     f"<td>{M.duracao(a['inicio'], a['fim'])}</td><td>{e(a['encerrado_por'] or '-')}</td></tr>" for a in ac)
    out += (f"<h2>Acesso à tela ({len(ac)})</h2><table><tr><th>Técnico</th><th>Início</th><th>Fim</th><th>Duração</th>"
            f"<th>Encerramento</th></tr>{linhas or '<tr><td colspan=5>A tela do cliente não foi acessada.</td></tr>'}</table>")
    if ch:
        out += "<h2>Conversa com o cliente</h2><table>" + "".join(
            f"<tr><td style='width:150px'>{local(c['criado_em'])}</td><td style='width:220px'><b>{e(c['de'])}</b>"
            f"{' (técnico)' if c['tecnico'] else ''}</td><td>{e(c['texto'])}</td></tr>" for c in ch) + "</table>"
    return out


# ---------------------------------------------------------------- site de downloads
def _base(request: Request) -> str:
    if URL_PUBLICA:
        return URL_PUBLICA
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


def _grupo_por_codigo(codigo: str):
    if not codigo:
        return None
    with db() as con:
        return con.execute("SELECT * FROM grupos WHERE codigo_ativacao=?", (codigo.strip().upper(),)).fetchone()


def _nome_arquivo(nome: str) -> str:
    n = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", n).strip()[:40] or "HelpMe"


@app.get("/download/HelpMe.exe")
def baixar_exe(request: Request, g: str = ""):
    """Entrega o HelpMe.exe com o endereço deste servidor, a chave pública e (opcional) o código da empresa."""
    if not AGENTE_EXE.exists():
        raise HTTPException(503, "O instalador ainda não foi gerado no servidor (veja o README: compilar o agente).")
    grupo = _grupo_por_codigo(g)
    if g and not grupo:
        raise HTTPException(404, "Link de instalação inválido ou vencido. Peça um novo ao técnico.")
    mc = marca_atual()
    dados = json.dumps({"servidor": _base(request), "chave_publica": CHAVE_PUBLICA_B64,
                        "codigo": grupo["codigo_ativacao"] if grupo else "", "marca": mc["nome"],
                        "empresa": EMPRESA, "contato": CONTATO}, ensure_ascii=False).encode()
    final = dados + struct.pack("<I", len(dados)) + b"HELPMECFG1"
    tamanho = AGENTE_EXE.stat().st_size + len(final)

    def conteudo():
        with open(AGENTE_EXE, "rb") as f:
            while bloco := f.read(1 << 16):
                yield bloco
        yield final
    base = re.sub(r"\s+", "", _nome_arquivo(mc["nome"]))
    nome = ("HelpMe" if base.lower() == "helpme" else base) + (f" - {_nome_arquivo(grupo['nome'])}" if grupo else "") + ".exe"
    with db() as con:
        auditar(con, request.client.host if request.client else "?", "download_instalador",
                grupo["nome"] if grupo else "-")
    return StreamingResponse(conteudo(), media_type="application/vnd.microsoft.portable-executable", headers={
        "Content-Length": str(tamanho), "Cache-Control": "no-store",
        "Content-Disposition": f"attachment; filename=\"{nome}\""})


@app.get("/baixar", response_class=HTMLResponse)
@app.get("/baixar/{codigo}", response_class=HTMLResponse)
def site_downloads(codigo: str = ""):
    grupo = _grupo_por_codigo(codigo)
    if codigo and not grupo:
        raise HTTPException(404, "Link de instalação inválido. Peça um novo ao técnico.")
    mc = marca_atual()
    e = html.escape
    pag = (STATIC / "baixar.html").read_text(encoding="utf-8")
    trocas = {"{{NOME}}": e(mc["nome"]), "{{COR}}": e(mc["cor_icone"]), "{{COR_NOME}}": e(mc["cor_nome"]),
              "{{EMPRESA}}": e(EMPRESA), "{{CONTATO}}": e(CONTATO),
              "{{LINK}}": "/download/HelpMe.exe" + (f"?g={e(grupo['codigo_ativacao'])}" if grupo else ""),
              "{{GRUPO}}": (f"<p class='grupo'>Instalação para <b>{e(grupo['nome'])}</b></p>" if grupo else ""),
              "{{LOGO}}": "<img src='/api/marca/logo' alt='' class='logo'>" if mc["tem_logo"] else "",
              "{{DISPONIVEL}}": "" if AGENTE_EXE.exists() else "<p class='aviso'>O instalador ainda não foi publicado "
                                                               "neste servidor.</p>"}
    for k, v in trocas.items():
        pag = pag.replace(k, v)
    return HTMLResponse(pag, headers={"Cache-Control": "no-cache"})
