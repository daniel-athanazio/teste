"""Help me! - plugins de ação.

Um plugin é um roteiro de passos (PowerShell) com parâmetros, arquivos (drivers,
instaladores, certificados) e uma verificação por passo. Pode rodar sozinho ao
liberar o atendimento ("automático"). Se um passo falha, o técnico recebe alerta
e pode pedir uma análise da falha à IA (só sugestão: a IA não executa nada).
"""
import hashlib
import html
import json
import re
import secrets
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .main import (DATA_DIR, EMPRESA, EXEC_VALIDADE_MIN, PARAM_RE, PLACEHOLDER_RE, _sessao, admin_atual, agora,
                   app, assinar, auditar, cfg_get, cfg_set, db, duracao, enviar_mensagem, equipamento_atual, iso,
                   local, sha256, tecnico_atual, acordar)

ARQ_DIR = DATA_DIR / "arquivos"
ARQ_DIR.mkdir(parents=True, exist_ok=True)
NOME_ARQ_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
TIPOS_PARAM = {
    "texto": (PARAM_RE, "letras, números e . : - / ,"),
    "numero": (re.compile(r"^\d{1,10}$"), "somente números"),
    "com": (re.compile(r"^COM\d{1,3}$"), "porta no formato COM1, COM3…"),
    "arquivo": (NOME_ARQ_RE, "arquivo da biblioteca"),
    "impressora": (re.compile(r"^[^'\r\n\x00]{1,120}$"), "sem aspas simples"),
    "caminho": (re.compile(r"^[^'\r\n\x00]{1,200}$"), "sem aspas simples"),
    "argumentos": (re.compile(r"^[^'\r\n\x00`$;|&<>]{1,200}$"), "sem aspas simples e sem ` $ ; | & < >"),
    "segredo": (re.compile(r"^[^'\r\n\x00]{1,100}$"), "sem aspas simples"),
}
# Tipos com texto livre: só podem aparecer no script entre aspas simples (o valor nunca vira comando).
TIPOS_ENTRE_ASPAS = {"impressora", "caminho", "argumentos", "segredo"}
VERIFICACOES = {"codigo_zero", "contem", "nao_contem", "regex"}
MASCARA = "••••••"

SCHEMA_PLUGINS = """
CREATE TABLE IF NOT EXISTS plugins(id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT, categoria TEXT,
  versao TEXT, definicao TEXT NOT NULL, automatico INTEGER DEFAULT 0, requer_autorizacao INTEGER DEFAULT 0,
  ativo INTEGER DEFAULT 1, atualizado_por TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS planos(id INTEGER PRIMARY KEY, sessao_id INTEGER, equipamento_id INTEGER, plugin_id INTEGER,
  plugin_nome TEXT, params_json TEXT, arquivos_json TEXT, requer_autorizacao INTEGER, automatico INTEGER,
  payload TEXT, assinatura TEXT, status TEXT DEFAULT 'aguardando', solicitado_por TEXT, solicitado_em TEXT,
  expira_em TEXT, entregue_em TEXT, autorizado_por TEXT, respondido_em TEXT, inicio TEXT, fim TEXT, detalhe TEXT,
  analise_ia TEXT);
CREATE TABLE IF NOT EXISTS plano_passos(id INTEGER PRIMARY KEY, plano_id INTEGER, indice INTEGER, nome TEXT,
  conteudo TEXT, conteudo_envio TEXT, verificar_json TEXT, em_falha TEXT, timeout_s INTEGER, status TEXT DEFAULT 'pendente',
  exit_code INTEGER, saida TEXT, inicio TEXT, fim TEXT);
CREATE TABLE IF NOT EXISTS arquivos(nome TEXT PRIMARY KEY, sha256 TEXT, tamanho INTEGER, enviado_por TEXT,
  enviado_em TEXT);
CREATE TABLE IF NOT EXISTS alertas(id INTEGER PRIMARY KEY, criado_em TEXT, sessao_id INTEGER, plano_id INTEGER,
  equipamento_id INTEGER, titulo TEXT, detalhe TEXT, resolvido_por TEXT, resolvido_em TEXT);
"""


# ---------------------------------------------------------------- validação da definição
class Parametro(BaseModel):
    nome: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    rotulo: str = ""
    tipo: str = Field("texto", pattern="^(texto|numero|com|arquivo|impressora|caminho|argumentos|segredo)$")
    padrao: str | None = None


class Verificar(BaseModel):
    tipo: str = "codigo_zero"
    valor: str = ""


class Passo(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    script: str = Field(min_length=1, max_length=20000)
    timeout_s: int = Field(120, ge=5, le=3600)
    verificar: Verificar = Verificar()
    em_falha: str = Field("parar", pattern="^(parar|continuar)$")


class PluginDef(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str = ""
    categoria: str = "Geral"
    versao: str = "1.0"
    automatico: bool = False
    requer_autorizacao: bool = False
    parametros: list[Parametro] = []
    arquivos: list[str] = []
    passos: list[Passo] = Field(min_length=1, max_length=40)


def validar_definicao(p: PluginDef):
    nomes = {x.nome for x in p.parametros}
    for x in p.parametros:
        if x.padrao is not None and not TIPOS_PARAM[x.tipo][0].match(x.padrao):
            raise HTTPException(422, f"Padrão inválido para '{x.nome}'")
        if x.tipo == "segredo" and x.padrao:
            raise HTTPException(422, f"'{x.nome}' é segredo e não pode ter valor padrão")
    for a in p.arquivos:
        if not NOME_ARQ_RE.match(a):
            raise HTTPException(422, f"Nome de arquivo inválido: {a}")
    segredos = {x.nome for x in p.parametros if x.tipo in TIPOS_ENTRE_ASPAS}
    for i, s in enumerate(p.passos, 1):
        if s.verificar.tipo not in VERIFICACOES:
            raise HTTPException(422, f"Passo {i}: verificação deve ser uma de {sorted(VERIFICACOES)}")
        if s.verificar.tipo == "regex":
            try:
                re.compile(s.verificar.valor)
            except re.error as e:
                raise HTTPException(422, f"Passo {i}: regex inválida ({e})")
        usados = set(PLACEHOLDER_RE.findall(s.script))
        if usados - nomes:
            raise HTTPException(422, f"Passo {i}: parâmetro não declarado: {', '.join(sorted(usados - nomes))}")
        if segredos & usados:  # fora dos textos entre aspas simples não pode sobrar nenhum desses parâmetros
            fora = re.sub(r"'(?:[^']|'')*'", "", s.script)
            for seg in segredos & usados:
                if re.search(r"\{\{\s*" + seg + r"\s*\}\}", fora):
                    raise HTTPException(422, f"Passo {i}: o parâmetro '{seg}' só pode ser usado dentro de aspas simples, "
                                             f"ex.: '{{{{{seg}}}}}'")
    if p.automatico and any(x.padrao is None for x in p.parametros):
        raise HTTPException(422, "Plugin automático precisa de valor padrão em todos os parâmetros")
    if p.automatico and p.arquivos:
        pass  # permitido: arquivos ficam no servidor


def avaliar(verificar: dict, exit_code: int, saida: str) -> bool:
    t, v = verificar.get("tipo", "codigo_zero"), verificar.get("valor", "")
    if t == "codigo_zero":
        return exit_code == 0
    if t == "contem":
        return exit_code == 0 and v.lower() in (saida or "").lower()
    if t == "nao_contem":
        return exit_code == 0 and v.lower() not in (saida or "").lower()
    if t == "regex":
        return exit_code == 0 and re.search(v, saida or "", re.I | re.M) is not None
    return False


# ---------------------------------------------------------------- plugins prontos
from .plugins_prontos import PLUGINS_PRONTOS  # noqa: E402


def init_plugins():
    with db() as con:
        con.executescript(SCHEMA_PLUGINS)
        cols = {r[1] for r in con.execute("PRAGMA table_info(arquivos)")}
        for c in ("categoria", "descricao"):
            if c not in cols:
                con.execute(f"ALTER TABLE arquivos ADD COLUMN {c} TEXT")
        existentes = {r[0] for r in con.execute("SELECT nome FROM plugins")}
        for d in PLUGINS_PRONTOS:  # acrescenta os prontos que ainda não existem (pelo nome)
            if d["nome"] not in existentes:
                p = PluginDef(**d)
                validar_definicao(p)
                _gravar_plugin(con, p, "sistema")


def _gravar_plugin(con, p: PluginDef, quem: str, pid: int | None = None) -> int:
    vals = (p.nome, p.descricao, p.categoria, p.versao, p.model_dump_json(), int(p.automatico),
            int(p.requer_autorizacao), quem, iso())
    if pid:
        if not con.execute("UPDATE plugins SET nome=?,descricao=?,categoria=?,versao=?,definicao=?,automatico=?,"
                           "requer_autorizacao=?,atualizado_por=?,atualizado_em=? WHERE id=?", (*vals, pid)).rowcount:
            raise HTTPException(404, "Plugin não encontrado")
        return pid
    return con.execute("INSERT INTO plugins(nome,descricao,categoria,versao,definicao,automatico,requer_autorizacao,"
                       "atualizado_por,atualizado_em) VALUES(?,?,?,?,?,?,?,?,?)", vals).lastrowid


init_plugins()


# ---------------------------------------------------------------- API: plugins
def _plugin_dict(r) -> dict:
    d = dict(r)
    d["definicao"] = json.loads(r["definicao"])
    return d


@app.get("/api/plugins")
def listar_plugins(_=Depends(tecnico_atual)):
    with db() as con:
        rows = con.execute("SELECT * FROM plugins ORDER BY ativo DESC, categoria, nome").fetchall()
    return [_plugin_dict(r) for r in rows]


@app.post("/api/plugins")
def criar_plugin(p: PluginDef, a=Depends(admin_atual)):
    validar_definicao(p)
    with db() as con:
        pid = _gravar_plugin(con, p, a["usuario"])
        auditar(con, a["usuario"], "plugin_criado", p.nome)
    return {"id": pid}


@app.put("/api/plugins/{pid}")
def editar_plugin(pid: int, p: PluginDef, a=Depends(admin_atual)):
    validar_definicao(p)
    with db() as con:
        _gravar_plugin(con, p, a["usuario"], pid)
        auditar(con, a["usuario"], "plugin_editado", f"{pid}: {p.nome}")
    return {"ok": True}


class AtivoIn(BaseModel):
    ativo: bool


@app.post("/api/plugins/{pid}/ativo")
def ativar_plugin(pid: int, d: AtivoIn, a=Depends(admin_atual)):
    with db() as con:
        con.execute("UPDATE plugins SET ativo=? WHERE id=?", (int(d.ativo), pid))
        auditar(con, a["usuario"], "plugin_ativo" if d.ativo else "plugin_inativo", str(pid))
    return {"ok": True}


# ---------------------------------------------------------------- API: arquivos (drivers, instaladores, certificados)
@app.get("/api/arquivos")
def listar_arquivos(_=Depends(tecnico_atual)):
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM arquivos ORDER BY categoria, nome")]


@app.put("/api/arquivos/{nome}")
async def enviar_arquivo(nome: str, request: Request, categoria: str = "Outros", descricao: str = "",
                         a=Depends(admin_atual)):
    if not NOME_ARQ_RE.match(nome):
        raise HTTPException(422, "Nome inválido (use letras, números, . _ -)")
    tmp = ARQ_DIR / f".{nome}.{secrets.token_hex(4)}"
    h, tam = hashlib.sha256(), 0
    with open(tmp, "wb") as f:
        async for bloco in request.stream():
            h.update(bloco)
            tam += len(bloco)
            if tam > 2 * 1024 ** 3:
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "Arquivo maior que 2 GB")
            f.write(bloco)
    if tam == 0:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, "Arquivo vazio")
    tmp.replace(ARQ_DIR / nome)
    with db() as con:
        con.execute("INSERT INTO arquivos(nome,sha256,tamanho,enviado_por,enviado_em,categoria,descricao) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(nome) DO UPDATE SET sha256=excluded.sha256,"
                    "tamanho=excluded.tamanho,enviado_por=excluded.enviado_por,enviado_em=excluded.enviado_em,"
                    "categoria=excluded.categoria,descricao=excluded.descricao",
                    (nome, h.hexdigest(), tam, a["usuario"], iso(), categoria[:60], descricao[:300]))
        auditar(con, a["usuario"], "arquivo_enviado", f"{nome} {tam} bytes")
    return {"nome": nome, "sha256": h.hexdigest(), "tamanho": tam}


class ArquivoMeta(BaseModel):
    categoria: str = Field("Outros", max_length=60)
    descricao: str = Field("", max_length=300)


@app.patch("/api/arquivos/{nome}")
def editar_arquivo(nome: str, d: ArquivoMeta, a=Depends(admin_atual)):
    with db() as con:
        if not con.execute("UPDATE arquivos SET categoria=?, descricao=? WHERE nome=?",
                           (d.categoria, d.descricao, nome)).rowcount:
            raise HTTPException(404, "Arquivo não encontrado")
    return {"ok": True}


@app.delete("/api/arquivos/{nome}")
def apagar_arquivo(nome: str, a=Depends(admin_atual)):
    if not NOME_ARQ_RE.match(nome):
        raise HTTPException(422, "Nome inválido")
    with db() as con:
        con.execute("DELETE FROM arquivos WHERE nome=?", (nome,))
        auditar(con, a["usuario"], "arquivo_apagado", nome)
    (ARQ_DIR / nome).unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- disparo de plugins
class RodarPluginIn(BaseModel):
    plugin_id: int
    params: dict[str, str] = {}


def _validar_params(defi: dict, valores: dict) -> dict:
    out = {}
    for p in defi.get("parametros", []):
        v = valores.get(p["nome"])
        if v in (None, "") and p.get("padrao") is not None:
            v = p["padrao"]
        if v in (None, ""):
            raise HTTPException(422, f"Informe: {p.get('rotulo') or p['nome']}")
        rx, dica = TIPOS_PARAM[p["tipo"]]
        if not rx.match(v):
            raise HTTPException(422, f"Valor inválido em '{p.get('rotulo') or p['nome']}' ({dica})")
        out[p["nome"]] = v
    return out


def _arquivos_do_plano(defi: dict, params: dict) -> list[str]:
    """Arquivos fixos do plugin + parâmetros que apontam para um arquivo do servidor."""
    nomes = list(defi.get("arquivos", []))
    for p in defi.get("parametros", []):
        if p["tipo"] == "arquivo" and params.get(p["nome"]):
            nomes.append(params[p["nome"]])
    return sorted(set(nomes))


def criar_plano(con, s, plugin, params_in: dict, quem: str, automatico: bool = False) -> int:
    defi = json.loads(plugin["definicao"])
    params = _validar_params(defi, params_in)
    segredos = {p["nome"] for p in defi.get("parametros", []) if p["tipo"] == "segredo"}
    arquivos = []
    for n in _arquivos_do_plano(defi, params):
        a = con.execute("SELECT * FROM arquivos WHERE nome=?", (n,)).fetchone()
        if not a:
            raise HTTPException(422, f"Arquivo '{n}' não está na biblioteca. Envie na aba Biblioteca.")
        arquivos.append({"nome": n, "sha256": a["sha256"], "tamanho": a["tamanho"]})
    requer = bool(defi.get("requer_autorizacao"))
    expira = iso(agora() + timedelta(minutes=EXEC_VALIDADE_MIN))
    params_vis = {k: (MASCARA if k in segredos else v) for k, v in params.items()}
    pid = con.execute("INSERT INTO planos(sessao_id,equipamento_id,plugin_id,plugin_nome,params_json,arquivos_json,"
                      "requer_autorizacao,automatico,solicitado_por,solicitado_em,expira_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (s["id"], s["equipamento_id"], plugin["id"], plugin["nome"], json.dumps(params_vis),
                       json.dumps(arquivos), int(requer), int(automatico), quem, iso(), expira)).lastrowid
    passos_sig = []
    for i, ps in enumerate(defi["passos"]):
        real = PLACEHOLDER_RE.sub(lambda m: params[m.group(1)], ps["script"])
        visivel = PLACEHOLDER_RE.sub(lambda m: MASCARA if m.group(1) in segredos else params[m.group(1)], ps["script"])
        con.execute("INSERT INTO plano_passos(plano_id,indice,nome,conteudo,conteudo_envio,verificar_json,em_falha,"
                    "timeout_s) VALUES(?,?,?,?,?,?,?,?)", (pid, i, ps["nome"], visivel, real,
                                                          json.dumps(ps["verificar"]), ps["em_falha"], ps["timeout_s"]))
        passos_sig.append({"indice": i, "nome": ps["nome"], "sha256": sha256(real), "timeout_s": ps["timeout_s"],
                           "verificar": ps["verificar"], "em_falha": ps["em_falha"]})
    payload, sig = assinar({"plano_id": pid, "equipamento_id": s["equipamento_id"], "sessao_id": s["id"],
                            "plugin": plugin["nome"], "descricao": defi.get("descricao", ""),
                            "tecnico": s["tecnico_ident"], "requer_autorizacao": requer, "automatico": automatico,
                            "arquivos": arquivos, "passos": passos_sig, "expira_em": expira})
    con.execute("UPDATE planos SET payload=?, assinatura=? WHERE id=?", (payload, sig, pid))
    acordar(s["equipamento_id"])
    return pid


@app.post("/api/sessoes/{sid}/plugin")
def rodar_plugin(sid: int, d: RodarPluginIn, t=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Sessão encerrada")
        if s["liberacao"] not in ("autorizada", "notificado"):
            raise HTTPException(409, "Aguardando o cliente autorizar o atendimento")
        pl = con.execute("SELECT * FROM plugins WHERE id=? AND ativo=1", (d.plugin_id,)).fetchone()
        if not pl:
            raise HTTPException(404, "Plugin não encontrado ou inativo")
        pid = criar_plano(con, s, pl, d.params, t["usuario"])
        auditar(con, t["usuario"], "plugin_solicitado", f"sessao {sid}: {pl['nome']}")
    return {"id": pid}


def disparar_automaticos(con, sid: int):
    """Chamado quando o atendimento é liberado pelo cliente (ou notificado no acesso contínuo)."""
    s = _sessao(con, sid)
    for pl in con.execute("SELECT * FROM plugins WHERE ativo=1 AND automatico=1 ORDER BY id").fetchall():
        try:
            criar_plano(con, s, pl, {}, "automático", automatico=True)
        except HTTPException as e:
            criar_alerta(con, s, None, f"Plugin automático '{pl['nome']}' não iniciou", str(e.detail))


def planos_da_sessao(con, sid: int) -> list[dict]:
    out = []
    for p in con.execute("SELECT * FROM planos WHERE sessao_id=? ORDER BY id", (sid,)).fetchall():
        d = {k: p[k] for k in p.keys() if k not in ("payload", "assinatura")}
        d["params"] = json.loads(p["params_json"] or "{}")
        d["passos"] = [dict(x) | {"conteudo_envio": None} for x in
                       con.execute("SELECT * FROM plano_passos WHERE plano_id=? ORDER BY indice", (p["id"],))]
        for x in d["passos"]:
            x.pop("conteudo_envio")
        out.append(d)
    return out


def cancelar_planos(con, sid: int):
    con.execute("UPDATE planos SET status='cancelado' WHERE sessao_id=? AND status IN ('aguardando','entregue')", (sid,))
    con.execute("UPDATE plano_passos SET conteudo_envio=NULL WHERE plano_id IN (SELECT id FROM planos WHERE sessao_id=?)",
                (sid,))


# ---------------------------------------------------------------- alertas
def criar_alerta(con, s, plano_id, titulo, detalhe=""):
    con.execute("INSERT INTO alertas(criado_em,sessao_id,plano_id,equipamento_id,titulo,detalhe) VALUES(?,?,?,?,?,?)",
                (iso(), s["id"], plano_id, s["equipamento_id"], titulo, detalhe[:500]))


@app.get("/api/alertas")
def listar_alertas(abertos: bool = True, _=Depends(tecnico_atual)):
    q = ("SELECT a.*, e.nome equip_nome, g.nome grupo_nome FROM alertas a LEFT JOIN equipamentos e "
         "ON e.id=a.equipamento_id LEFT JOIN grupos g ON g.id=e.grupo_id")
    if abertos:
        q += " WHERE a.resolvido_em IS NULL"
    with db() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY a.id DESC LIMIT 100")]


@app.post("/api/alertas/{aid}/resolver")
def resolver_alerta(aid: int, t=Depends(tecnico_atual)):
    with db() as con:
        con.execute("UPDATE alertas SET resolvido_por=?, resolvido_em=? WHERE id=? AND resolvido_em IS NULL",
                    (t["usuario"], iso(), aid))
    return {"ok": True}


# ---------------------------------------------------------------- IA (opcional, só sugere)
class IACfg(BaseModel):
    ativo: bool
    chave: str | None = None
    modelo: str = Field("claude-sonnet-5-5", max_length=80)


@app.get("/api/config/ia")
def ver_cfg_ia(_=Depends(admin_atual)):
    with db() as con:
        return {"ativo": cfg_get(con, "ia_ativo") == "1", "tem_chave": bool(cfg_get(con, "ia_chave")),
                "modelo": cfg_get(con, "ia_modelo", "claude-sonnet-5-5")}


@app.put("/api/config/ia")
def salvar_cfg_ia(d: IACfg, a=Depends(admin_atual)):
    with db() as con:
        cfg_set(con, "ia_ativo", "1" if d.ativo else "0")
        cfg_set(con, "ia_modelo", d.modelo.strip())
        if d.chave is not None:
            cfg_set(con, "ia_chave", d.chave.strip())
        auditar(con, a["usuario"], "config_ia", f"ativo={d.ativo}")
    return {"ok": True}


@app.post("/api/planos/{pid}/analisar")
def analisar_falha(pid: int, t=Depends(tecnico_atual)):
    with db() as con:
        ativo, chave = cfg_get(con, "ia_ativo") == "1", cfg_get(con, "ia_chave")
        modelo = cfg_get(con, "ia_modelo", "claude-sonnet-5-5")
        p = con.execute("SELECT * FROM planos WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Execução não encontrada")
        passos = con.execute("SELECT * FROM plano_passos WHERE plano_id=? ORDER BY indice", (pid,)).fetchall()
        eq = con.execute("SELECT info_json FROM equipamentos WHERE id=?", (p["equipamento_id"],)).fetchone()
    if not ativo or not chave:
        raise HTTPException(409, "Análise por IA desligada (Configurações)")
    falhas = [x for x in passos if x["status"] == "falhou"] or passos[-1:]
    trechos = "\n\n".join(f"PASSO {x['indice'] + 1}: {x['nome']}\nCOMANDO:\n{x['conteudo']}\nCÓDIGO: {x['exit_code']}\n"
                          f"SAÍDA:\n{(x['saida'] or '')[-4000:]}" for x in falhas)
    prompt = (f"Você ajuda técnicos de suporte de uma loja de informática no Brasil. Um procedimento automático "
              f"falhou num Windows de cliente.\nProcedimento: {p['plugin_nome']}\nEquipamento: "
              f"{(eq['info_json'] if eq else '')[:800]}\n\n{trechos}\n\nResponda em português, curto: 1) causa mais "
              f"provável; 2) o que o técnico deve verificar ou fazer, em passos; 3) se fizer sentido, um comando "
              f"PowerShell de correção para o técnico revisar (não será executado automaticamente).")
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                       headers={"x-api-key": chave, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": modelo, "max_tokens": 900, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        texto = "".join(b.get("text", "") for b in r.json().get("content", []))
    except Exception as e:
        raise HTTPException(502, f"IA indisponível: {str(e)[:200]}")
    with db() as con:
        con.execute("UPDATE planos SET analise_ia=? WHERE id=?", (texto, pid))
        auditar(con, t["usuario"], "analise_ia", str(pid))
    return {"analise": texto}


# ---------------------------------------------------------------- agente
def planos_para_agente(con, equip_id: int) -> list[dict]:
    con.execute("UPDATE planos SET status='expirado' WHERE equipamento_id=? AND status='aguardando' AND expira_em<?",
                (equip_id, iso()))
    rows = con.execute("SELECT p.id,p.payload,p.assinatura FROM planos p JOIN sessoes s ON s.id=p.sessao_id "
                       "WHERE p.equipamento_id=? AND p.status='aguardando' AND s.status='em_andamento' ORDER BY p.id",
                       (equip_id,)).fetchall()
    out = []
    for r in rows:
        passos = con.execute("SELECT indice, conteudo_envio conteudo FROM plano_passos WHERE plano_id=? ORDER BY indice",
                             (r["id"],)).fetchall()
        out.append(dict(r) | {"passos": [dict(x) for x in passos]})
        con.execute("UPDATE planos SET status='entregue', entregue_em=? WHERE id=?", (iso(), r["id"]))
        # o conteúdo real (com segredos) só existe até ser entregue
        con.execute("UPDATE plano_passos SET conteudo_envio=NULL WHERE plano_id=?", (r["id"],))
    return out


def _plano_do_equip(con, pid: int, eq):
    p = con.execute("SELECT * FROM planos WHERE id=? AND equipamento_id=?", (pid, eq["id"])).fetchone()
    if not p:
        raise HTTPException(404, "Execução não encontrada")
    return p


@app.get("/agente/arquivos/{nome}")
def baixar_arquivo(nome: str, eq=Depends(equipamento_atual)):
    if not NOME_ARQ_RE.match(nome):
        raise HTTPException(404, "Arquivo não encontrado")
    with db() as con:  # só arquivos de uma execução em andamento deste equipamento
        ok = any(any(a["nome"] == nome for a in json.loads(p["arquivos_json"] or "[]")) for p in con.execute(
            "SELECT arquivos_json FROM planos WHERE equipamento_id=? AND status IN ('entregue','executando')",
            (eq["id"],)))
    if not ok or not (ARQ_DIR / nome).exists():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(ARQ_DIR / nome, media_type="application/octet-stream", filename=nome)


class RespostaPlano(BaseModel):
    autorizado: bool
    usuario: str = ""


class ResultadoPasso(BaseModel):
    indice: int
    exit_code: int
    saida: str = ""
    inicio: str
    fim: str


class FimPlano(BaseModel):
    detalhe: str = ""


@app.post("/agente/planos/{pid}/resposta")
def resposta_plano(pid: int, d: RespostaPlano, eq=Depends(equipamento_atual)):
    with db() as con:
        p = _plano_do_equip(con, pid, eq)
        if p["status"] != "entregue":
            raise HTTPException(409, f"Execução em status {p['status']}")
        con.execute("UPDATE planos SET status=?, autorizado_por=?, respondido_em=? WHERE id=?",
                    ("executando" if d.autorizado else "negado", d.usuario[:120], iso(), pid))
    return {"ok": True}


@app.post("/agente/planos/{pid}/passo")
def resultado_passo(pid: int, d: ResultadoPasso, eq=Depends(equipamento_atual)):
    with db() as con:
        p = _plano_do_equip(con, pid, eq)
        if p["status"] == "entregue" and not p["requer_autorizacao"]:
            con.execute("UPDATE planos SET status='executando', inicio=? WHERE id=?", (d.inicio, pid))
        elif p["status"] != "executando":
            raise HTTPException(409, f"Execução em status {p['status']}")
        if not p["inicio"]:
            con.execute("UPDATE planos SET inicio=? WHERE id=?", (d.inicio, pid))
        x = con.execute("SELECT * FROM plano_passos WHERE plano_id=? AND indice=?", (pid, d.indice)).fetchone()
        if not x:
            raise HTTPException(404, "Passo não encontrado")
        ok = avaliar(json.loads(x["verificar_json"]), d.exit_code, d.saida)  # o servidor confere de novo
        con.execute("UPDATE plano_passos SET status=?, exit_code=?, saida=?, inicio=?, fim=? WHERE id=?",
                    ("ok" if ok else "falhou", d.exit_code, d.saida[:100000], d.inicio, d.fim, x["id"]))
    return {"ok": ok}


@app.post("/agente/planos/{pid}/fim")
def fim_plano(pid: int, d: FimPlano, eq=Depends(equipamento_atual)):
    with db() as con:
        p = _plano_do_equip(con, pid, eq)
        if p["status"] not in ("executando", "entregue"):
            raise HTTPException(409, f"Execução em status {p['status']}")
        passos = con.execute("SELECT status FROM plano_passos WHERE plano_id=?", (pid,)).fetchall()
        falhas = sum(1 for x in passos if x["status"] == "falhou")
        status = "ok" if falhas == 0 and all(x["status"] == "ok" for x in passos) else \
                 "falhou" if any(x["status"] == "pendente" for x in passos) or falhas == len(passos) else "concluido_com_falhas"
        con.execute("UPDATE planos SET status=?, fim=?, detalhe=? WHERE id=?", (status, iso(), d.detalhe[:500], pid))
        if status != "ok":
            s = _sessao(con, p["sessao_id"])
            primeiro = con.execute("SELECT nome FROM plano_passos WHERE plano_id=? AND status='falhou' ORDER BY indice "
                                   "LIMIT 1", (pid,)).fetchone()
            criar_alerta(con, s, pid, f"{p['plugin_nome']}: {'falhou' if status == 'falhou' else 'concluído com falhas'}",
                         f"Passo: {primeiro['nome'] if primeiro else '-'} · {d.detalhe}")
    return {"status": status}


# ---------------------------------------------------------------- relatório
PLANO_TXT = {"ok": "Concluído", "falhou": "Falhou", "concluido_com_falhas": "Concluído com falhas",
             "negado": "Negado pelo usuário", "cancelado": "Cancelado", "expirado": "Expirado",
             "aguardando": "Não entregue", "entregue": "Sem retorno do agente", "executando": "Interrompido"}


def html_planos(con, sid: int) -> str:
    e = html.escape
    planos = planos_da_sessao(con, sid)
    if not planos:
        return ""
    blocos = []
    for p in planos:
        params = ", ".join(f"{k}={v}" for k, v in p["params"].items())
        aut = "Não exigida (atendimento autorizado)" if not p["requer_autorizacao"] else \
            (f"{'Negado' if p['status'] == 'negado' else 'Autorizado'} por {p['autorizado_por']} em {local(p['respondido_em'])}"
             if p["respondido_em"] else "Sem resposta")
        linhas = "".join(
            f"<tr><td>{x['indice'] + 1}</td><td>{e(x['nome'])}</td><td>{'✔' if x['status'] == 'ok' else '✖' if x['status'] == 'falhou' else '–'} "
            f"{e(x['status'])}{'' if x['exit_code'] is None else ' (código ' + str(x['exit_code']) + ')'}</td>"
            f"<td>{duracao(x['inicio'], x['fim']) if x['inicio'] else '-'}</td></tr>"
            f"<tr class=out><td></td><td colspan=3><details><summary>Comando e saída</summary><pre>{e(x['conteudo'])}</pre>"
            f"<pre>{e((x['saida'] or '')[:20000])}</pre></details></td></tr>" for x in p["passos"])
        blocos.append(
            f"<h3>{e(p['plugin_nome'])} – {e(PLANO_TXT.get(p['status'], p['status']))}"
            f"{' (automático)' if p['automatico'] else ''}</h3><p><small>Solicitado {local(p['solicitado_em'])} por "
            f"{e(p['solicitado_por'])} · autorização: {e(aut)}{' · parâmetros: ' + e(params) if params else ''}"
            f" · tempo {duracao(p['inicio'], p['fim']) if p['inicio'] else '-'}</small></p>"
            f"<table><tr><th>#</th><th>Passo</th><th>Resultado</th><th>Tempo</th></tr>{linhas}</table>"
            + (f"<details><summary>Análise da IA</summary><pre>{e(p['analise_ia'])}</pre></details>" if p["analise_ia"] else ""))
    return f"<h2>Plugins de ação ({len(planos)})</h2>" + "".join(blocos)


def resumo_planos(con, sid: int) -> list[dict]:
    return [{"plugin": p["plugin_nome"], "status": p["status"], "automatico": bool(p["automatico"]),
             "parametros": p["params"], "inicio": p["inicio"], "fim": p["fim"],
             "passos": [{"nome": x["nome"], "status": x["status"], "exit_code": x["exit_code"]} for x in p["passos"]]}
            for p in planos_da_sessao(con, sid)]
