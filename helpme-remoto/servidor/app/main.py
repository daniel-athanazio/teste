"""Help me! - servidor do acesso remoto (FN Info House).

Grupos (empresas), equipamentos com número de acesso, técnicos identificados,
biblioteca de scripts, sessões de atendimento, acesso contínuo com consentimento
do cliente (modo notificar ou autorizar), acesso à tela pelo navegador (remoto.py),
pedidos de ajuda do cliente, relatório por sessão, integração com o gestor de OS
e o site de downloads do HelpMe.exe.
"""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

VERSAO = "1.0.0"
DATA_DIR = Path(os.environ.get("FN_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "helpme.db"
REPORT_DIR = DATA_DIR / "relatorios"
KEY_PATH = DATA_DIR / "fn_assinatura.key"
TZ = ZoneInfo(os.environ.get("FN_TZ", "America/Sao_Paulo"))
TOKEN_HORAS = int(os.environ.get("FN_TOKEN_HORAS", "12"))
EMPRESA = os.environ.get("FN_EMPRESA", "FN Info House")
CONTATO = os.environ.get("FN_CONTATO", "FN Info House – WhatsApp (21) 96438-2098")
URL_PUBLICA = os.environ.get("FN_URL_PUBLICA", "").rstrip("/")
EXEC_VALIDADE_MIN = 10        # pedido de execução expira se o agente não pegar
PEDIDO_VALIDADE_MIN = 5       # pedidos ao cliente (início, contínuo)
AGENTE_ONLINE_SEG = 30
PARAM_RE = re.compile(r"^[A-Za-z0-9._:\-/,]{1,100}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
MODOS = {"autorizar": "Autorizar cada atendimento", "notificar": "Só notificar"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()


# ---------------------------------------------------------------- utilidades
def agora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or agora()).isoformat(timespec="seconds")


def local(s: str | None) -> str:
    if not s:
        return "-"
    return datetime.fromisoformat(s).astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")


def duracao(ini: str | None, fim: str | None) -> str:
    if not ini:
        return "-"
    f = datetime.fromisoformat(fim) if fim else agora()
    seg = int((f - datetime.fromisoformat(ini)).total_seconds())
    h, r = divmod(max(seg, 0), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_senha(senha: str, sal: str | None = None) -> str:
    sal = sal or secrets.token_hex(16)
    d = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), 200_000)
    return f"{sal}${d.hex()}"


def confere_senha(senha: str, guardado: str) -> bool:
    sal, _ = guardado.split("$", 1)
    return hmac.compare_digest(hash_senha(senha, sal), guardado)


def fmt_numero(n: str | None) -> str:
    return f"{n[:3]} {n[3:6]} {n[6:]}" if n and len(n) == 9 else (n or "-")


def NOME_APP() -> str:
    try:
        return marca_atual()["nome"]
    except Exception:
        return "Help me!"


def ident_tecnico(t) -> str:
    """Identificação do técnico como sai no relatório e na tela do cliente."""
    s = t["nome"]
    if t["funcao"]:
        s += f" – {t['funcao']}"
    return s


# ---------------------------------------------------------------- chave Ed25519
def carregar_chave() -> Ed25519PrivateKey:
    if not KEY_PATH.exists():
        k = Ed25519PrivateKey.generate()
        KEY_PATH.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                             serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
        os.chmod(KEY_PATH, 0o600)
    return serialization.load_pem_private_key(KEY_PATH.read_bytes(), None)


CHAVE = carregar_chave()
CHAVE_PUBLICA_B64 = base64.b64encode(CHAVE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def assinar(obj: dict) -> tuple[str, str]:
    """Retorna (payload canônico, assinatura base64)."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return payload, base64.b64encode(CHAVE.sign(payload.encode())).decode()


# ---------------------------------------------------------------- banco
SCHEMA = """
CREATE TABLE IF NOT EXISTS tecnicos(id INTEGER PRIMARY KEY, nome TEXT NOT NULL, usuario TEXT UNIQUE NOT NULL,
  senha TEXT NOT NULL, funcao TEXT, telefone TEXT, admin INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1, criado_em TEXT);
CREATE TABLE IF NOT EXISTS tokens(token_hash TEXT PRIMARY KEY, tecnico_id INTEGER, expira TEXT);
CREATE TABLE IF NOT EXISTS grupos(id INTEGER PRIMARY KEY, nome TEXT NOT NULL, cnpj TEXT, codigo_ativacao TEXT UNIQUE,
  criado_em TEXT);
CREATE TABLE IF NOT EXISTS equipamentos(id INTEGER PRIMARY KEY, grupo_id INTEGER REFERENCES grupos(id), nome TEXT,
  hostname TEXT, usuario_windows TEXT, rustdesk_id TEXT, numero_acesso TEXT UNIQUE, token_hash TEXT UNIQUE,
  info_json TEXT, ultimo_contato TEXT, criado_em TEXT, vinculado_em TEXT, vinculado_por TEXT,
  modo_acesso TEXT DEFAULT 'autorizar', continuo TEXT DEFAULT 'inativo',
  continuo_consentido_por TEXT, continuo_consentido_em TEXT, senha_remota TEXT, portatil INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS scripts(id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT, conteudo TEXT NOT NULL,
  requer_autorizacao INTEGER DEFAULT 1, timeout_s INTEGER DEFAULT 120, ativo INTEGER DEFAULT 1,
  atualizado_por TEXT, atualizado_em TEXT);
CREATE TABLE IF NOT EXISTS sessoes(id INTEGER PRIMARY KEY, equipamento_id INTEGER REFERENCES equipamentos(id),
  tecnico_id INTEGER REFERENCES tecnicos(id), tecnico_ident TEXT, os_numero TEXT, motivo TEXT, inicio TEXT, fim TEXT,
  tipo_problema TEXT,
  status TEXT DEFAULT 'em_andamento', nota TEXT, relatorio TEXT,
  liberacao TEXT DEFAULT 'aguardando', liberado_por TEXT, liberado_em TEXT,
  gestor_status TEXT, gestor_detalhe TEXT, gestor_em TEXT, chamado_id INTEGER, pedido_id INTEGER);
CREATE TABLE IF NOT EXISTS execucoes(id INTEGER PRIMARY KEY, sessao_id INTEGER REFERENCES sessoes(id),
  equipamento_id INTEGER, script_id INTEGER, script_nome TEXT, conteudo TEXT, params_json TEXT,
  requer_autorizacao INTEGER, timeout_s INTEGER, payload TEXT, assinatura TEXT,
  status TEXT DEFAULT 'aguardando', solicitado_em TEXT, expira_em TEXT, entregue_em TEXT,
  autorizado_por TEXT, respondido_em TEXT, inicio TEXT, fim TEXT, exit_code INTEGER, saida TEXT);
CREATE TABLE IF NOT EXISTS mensagens(id INTEGER PRIMARY KEY, equipamento_id INTEGER, sessao_id INTEGER, tipo TEXT,
  dados_json TEXT, payload TEXT, assinatura TEXT, status TEXT DEFAULT 'aguardando', criado_em TEXT, expira_em TEXT,
  entregue_em TEXT, aceito INTEGER, respondido_por TEXT, respondido_em TEXT);
CREATE TABLE IF NOT EXISTS participantes(sessao_id INTEGER, tecnico_id INTEGER, tecnico_ident TEXT, entrou_em TEXT,
  PRIMARY KEY(sessao_id, tecnico_id));
CREATE TABLE IF NOT EXISTS tipos_problema(id INTEGER PRIMARY KEY, nome TEXT UNIQUE NOT NULL, descricao TEXT,
  categorias TEXT, ordem INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS pedidos_ajuda(id INTEGER PRIMARY KEY, equipamento_id INTEGER, nome TEXT, telefone TEXT,
  problema TEXT, usuario TEXT, criado_em TEXT, status TEXT DEFAULT 'aberto', sessao_id INTEGER, chamado_id INTEGER,
  chamado_link TEXT, gestor_detalhe TEXT, atendido_por TEXT, atendido_em TEXT);
CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY, equipamento_id INTEGER, sessao_id INTEGER, de TEXT,
  tecnico INTEGER, texto TEXT, criado_em TEXT, entregue_em TEXT);
CREATE TABLE IF NOT EXISTS acessos_tela(id INTEGER PRIMARY KEY, sessao_id INTEGER, equipamento_id INTEGER,
  tecnico_ident TEXT, inicio TEXT, fim TEXT, encerrado_por TEXT);
CREATE TABLE IF NOT EXISTS auditoria(id INTEGER PRIMARY KEY, quando TEXT, quem TEXT, acao TEXT, detalhe TEXT);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with _lock:
            yield con
            con.commit()
    finally:
        con.close()


def auditar(con, quem: str, acao: str, detalhe: str = ""):
    con.execute("INSERT INTO auditoria(quando,quem,acao,detalhe) VALUES(?,?,?,?)", (iso(), quem, acao, detalhe))


def migrar(con):
    """Acrescenta colunas novas em bancos criados por versões anteriores."""
    for tabela in ("tecnicos", "equipamentos", "sessoes"):
        existentes = {r[1] for r in con.execute(f"PRAGMA table_info({tabela})")}
        ddl = re.search(rf"CREATE TABLE IF NOT EXISTS {tabela}\((.*?)\);", SCHEMA, re.S).group(1)
        for col in re.split(r",\s*(?![^()]*\))", ddl):
            nome = col.strip().split()[0]
            if nome not in existentes and "PRIMARY KEY" not in col and "UNIQUE" not in col:
                con.execute(f"ALTER TABLE {tabela} ADD COLUMN {col.strip()}")


def cfg_get(con, chave: str, padrao: str = "") -> str:
    r = con.execute("SELECT valor FROM config WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else padrao


def cfg_set(con, chave: str, valor: str):
    con.execute("INSERT INTO config VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))


SCRIPTS_INICIAIS = [
    ("Ping para um ponto da rede", "Testa se um IP ou nome responde (4 pacotes).",
     "ping -n 4 {{alvo}}", 0, 60),
    ("Teste de porta TCP", "Verifica se uma porta de um host está acessível.",
     "Test-NetConnection -ComputerName {{alvo}} -Port {{porta}} | Format-List", 0, 60),
    ("Liberar porta no Firewall (entrada TCP)", "Cria regra de entrada no Firewall do Windows para a porta informada.",
     'New-NetFirewallRule -DisplayName "Help me! - TCP {{porta}}" -Direction Inbound -Protocol TCP '
     "-LocalPort {{porta}} -Action Allow -Profile Any | Format-List DisplayName,Enabled,Action", 1, 60),
    ("Remover regra de Firewall criada pelo Help me!", "Remove a regra 'Help me! - TCP <porta>'.",
     'Remove-NetFirewallRule -DisplayName "Help me! - TCP {{porta}}"; "Regra removida"', 1, 60),
    ("Configuração de rede", "Mostra IP, gateway e DNS.", "ipconfig /all", 0, 60),
    ("Limpar cache DNS", "Esvazia o cache de DNS do Windows.", "ipconfig /flushdns", 0, 60),
    ("Informações do sistema", "Resumo do Windows, CPU, memória e discos.",
     "Get-ComputerInfo -Property WindowsProductName,WindowsVersion,OsBuildNumber,CsProcessors,"
     "CsTotalPhysicalMemory | Format-List; Get-PSDrive -PSProvider FileSystem | Format-Table Name,Used,Free", 0, 120),
]


# Tipo de problema → categorias de plugins sugeridas primeiro no atendimento.
TIPOS_INICIAIS = [
    ("Sistema", "Uniplus e outros programas: instalação, atualização, erros, banco de dados",
     ["Uniplus", "Instalação", "Certificado digital"]),
    ("PDV", "Frente de caixa: impressora de cupom, balança, gaveta, pinpad, emissão de NFC-e",
     ["Automação comercial", "Impressoras", "Uniplus"]),
    ("Equipamentos", "Computador, notebook, impressora: lentidão, drivers, periféricos",
     ["Diagnóstico", "Impressoras", "Instalação"]),
    ("Rede", "Internet, comunicação entre computadores, servidor, portas e firewall",
     ["Rede", "Diagnóstico"]),
    ("Outros", "Demais atendimentos", []),
]


def init_db():
    with db() as con:
        con.executescript(SCHEMA)
        migrar(con)
        if not con.execute("SELECT 1 FROM tecnicos").fetchone():
            usuario = os.environ.get("FN_ADMIN_USUARIO", "admin")
            senha = os.environ.get("FN_ADMIN_SENHA") or secrets.token_urlsafe(12)
            con.execute("INSERT INTO tecnicos(nome,usuario,senha,funcao,admin,criado_em) VALUES(?,?,?,?,1,?)",
                        ("Administrador", usuario, hash_senha(senha), "Administrador", iso()))
            if not os.environ.get("FN_ADMIN_SENHA"):
                (DATA_DIR / "senha_admin_inicial.txt").write_text(f"{usuario} / {senha}\n")
        if not con.execute("SELECT 1 FROM tipos_problema").fetchone():
            for i, (n, d, cats) in enumerate(TIPOS_INICIAIS):
                con.execute("INSERT INTO tipos_problema(nome,descricao,categorias,ordem) VALUES(?,?,?,?)",
                            (n, d, json.dumps(cats), i))
        if not con.execute("SELECT 1 FROM scripts").fetchone():
            for n, d, c, aut, t in SCRIPTS_INICIAIS:
                con.execute("INSERT INTO scripts(nome,descricao,conteudo,requer_autorizacao,timeout_s,"
                            "atualizado_por,atualizado_em) VALUES(?,?,?,?,?,?,?)", (n, d, c, aut, t, "sistema", iso()))
        # integração com gestor: variável de ambiente só preenche o valor inicial
        if os.environ.get("FN_GESTOR_WEBHOOK") and not cfg_get(con, "gestor_url"):
            cfg_set(con, "gestor_url", os.environ["FN_GESTOR_WEBHOOK"])
            cfg_set(con, "gestor_ativo", "1")


init_db()
app = FastAPI(title="Help me! API", version=VERSAO)


# ---------------------------------------------------------------- autenticação
def tecnico_atual(authorization: str = Header("")) -> sqlite3.Row:
    tok = authorization.removeprefix("Bearer ").strip()
    if not tok:
        raise HTTPException(401, "Faça login")
    with db() as con:
        r = con.execute("SELECT t.* FROM tokens k JOIN tecnicos t ON t.id=k.tecnico_id "
                        "WHERE k.token_hash=? AND k.expira>? AND t.ativo=1", (sha256(tok), iso())).fetchone()
    if not r:
        raise HTTPException(401, "Sessão expirada")
    return r


def admin_atual(t=Depends(tecnico_atual)):
    if not t["admin"]:
        raise HTTPException(403, "Somente administrador")
    return t


def equipamento_atual(authorization: str = Header("")) -> sqlite3.Row:
    tok = authorization.removeprefix("Bearer ").strip()
    with db() as con:
        r = con.execute("SELECT * FROM equipamentos WHERE token_hash=?", (sha256(tok),)).fetchone()
    if not tok or not r:
        raise HTTPException(401, "Agente não registrado")
    return r


# ---------------------------------------------------------------- mensagens assinadas ao agente
def enviar_mensagem(con, equip_id: int, tipo: str, dados: dict, sessao_id: int | None = None,
                    validade_min: int = PEDIDO_VALIDADE_MIN) -> int:
    """tipo: aviso | pedir_inicio | pedir_continuo | senha | continuo_encerrado"""
    expira = iso(agora() + timedelta(minutes=validade_min))
    cur = con.execute("INSERT INTO mensagens(equipamento_id,sessao_id,tipo,dados_json,criado_em,expira_em) "
                      "VALUES(?,?,?,?,?,?)", (equip_id, sessao_id, tipo, json.dumps(dados, ensure_ascii=False),
                                              iso(), expira))
    mid = cur.lastrowid
    payload, sig = assinar({"mensagem_id": mid, "equipamento_id": equip_id, "tipo": tipo, "dados": dados,
                            "expira_em": expira})
    con.execute("UPDATE mensagens SET payload=?, assinatura=? WHERE id=?", (payload, sig, mid))
    acordar(equip_id)
    return mid


# ---------------------------------------------------------------- canal de controle (acordar o agente na hora)
LOOP: asyncio.AbstractEventLoop | None = None
CONTROLE: dict[int, set] = {}


def acordar(equip_id: int):
    """Avisa o agente conectado que há algo novo; ele faz o heartbeat imediatamente (os comandos seguem assinados)."""
    if LOOP is None:
        return
    for ws in list(CONTROLE.get(equip_id, ())):
        asyncio.run_coroutine_threadsafe(_enviar_seguro(ws, "acordar"), LOOP)


async def _enviar_seguro(ws, texto: str):
    try:
        await ws.send_text(texto)
    except Exception:
        pass


def nova_senha_remota() -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(14))


# ---------------------------------------------------------------- modelos
class Login(BaseModel):
    usuario: str
    senha: str


class TecnicoIn(BaseModel):
    nome: str = Field(min_length=2, max_length=80)
    usuario: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9._-]+$")
    senha: str = Field(min_length=8)
    funcao: str = Field("", max_length=60)
    telefone: str = Field("", max_length=30)
    admin: bool = False


class TecnicoUpd(BaseModel):
    nome: str | None = Field(None, min_length=2, max_length=80)
    funcao: str | None = Field(None, max_length=60)
    telefone: str | None = Field(None, max_length=30)
    admin: bool | None = None
    ativo: bool | None = None
    senha: str | None = Field(None, min_length=8)


class MinhaSenha(BaseModel):
    senha_atual: str
    senha_nova: str = Field(min_length=8)


class GrupoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    cnpj: str | None = None


class EquipamentoUpd(BaseModel):
    nome: str | None = Field(None, max_length=120)
    grupo_id: int | None = None
    rustdesk_id: str | None = None


class Vincular(BaseModel):
    numero_acesso: str
    grupo_id: int
    nome: str = Field(min_length=1, max_length=120)


class ContinuoIn(BaseModel):
    modo: str = Field(pattern="^(autorizar|notificar)$")


class ScriptIn(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: str = ""
    conteudo: str = Field(min_length=1, max_length=20000)
    requer_autorizacao: bool = True
    timeout_s: int = Field(120, ge=5, le=1800)
    ativo: bool = True


class SessaoIn(BaseModel):
    equipamento_id: int
    pedido_id: int | None = None
    chamado_id: int | None = None
    tipo_problema: str = Field(min_length=1, max_length=40)
    os_numero: str | None = Field(None, max_length=30)
    motivo: str | None = Field(None, max_length=300)


class TipoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=40)
    descricao: str = Field("", max_length=200)
    categorias: list[str] = []
    ordem: int = 0
    ativo: bool = True


class SessaoUpd(BaseModel):
    tipo_problema: str | None = Field(None, min_length=1, max_length=40)
    os_numero: str | None = Field(None, max_length=30)
    motivo: str | None = Field(None, max_length=300)


class ExecutarIn(BaseModel):
    script_id: int
    params: dict[str, str] = {}


class EncerrarIn(BaseModel):
    status: str = Field(pattern="^(concluido|pendente)$")
    nota: str = ""


class GestorCfg(BaseModel):
    ativo: bool
    url: str = Field("", max_length=300)
    token: str | None = Field(None, max_length=300)   # None = manter o atual


class Registro(BaseModel):
    codigo_ativacao: str = ""        # opcional: sem código o equipamento aguarda vínculo
    hostname: str
    usuario_windows: str = ""
    rustdesk_id: str = ""
    info: dict = {}


class Heartbeat(BaseModel):
    usuario_windows: str = ""
    rustdesk_id: str = ""
    info: dict | None = None
    portatil: bool = False
    telas: list[str] = []


class Resposta(BaseModel):
    autorizado: bool
    usuario: str = ""


class Resultado(BaseModel):
    exit_code: int
    saida: str = ""
    inicio: str
    fim: str


class RespostaMsg(BaseModel):
    aceito: bool
    usuario: str = ""
    detalhe: str = ""


class Revogar(BaseModel):
    usuario: str = ""


# ---------------------------------------------------------------- login / técnicos
@app.post("/api/login")
def login(d: Login):
    with db() as con:
        t = con.execute("SELECT * FROM tecnicos WHERE usuario=? AND ativo=1", (d.usuario,)).fetchone()
        if not t or not confere_senha(d.senha, t["senha"]):
            auditar(con, d.usuario, "login_falhou")
            raise HTTPException(401, "Usuário ou senha inválidos")
        tok = secrets.token_urlsafe(32)
        con.execute("INSERT INTO tokens VALUES(?,?,?)", (sha256(tok), t["id"], iso(agora() + timedelta(hours=TOKEN_HORAS))))
        con.execute("DELETE FROM tokens WHERE expira<?", (iso(),))
        auditar(con, t["usuario"], "login")
    return {"token": tok, "nome": t["nome"], "funcao": t["funcao"], "usuario": t["usuario"], "admin": bool(t["admin"])}


@app.get("/api/eu")
def eu(t=Depends(tecnico_atual)):
    return {k: t[k] for k in ("id", "nome", "usuario", "funcao", "telefone", "admin")}


@app.post("/api/eu/senha")
def trocar_minha_senha(d: MinhaSenha, t=Depends(tecnico_atual)):
    if not confere_senha(d.senha_atual, t["senha"]):
        raise HTTPException(400, "Senha atual incorreta")
    with db() as con:
        con.execute("UPDATE tecnicos SET senha=? WHERE id=?", (hash_senha(d.senha_nova), t["id"]))
        auditar(con, t["usuario"], "senha_alterada")
    return {"ok": True}


@app.get("/api/tecnicos")
def listar_tecnicos(_=Depends(admin_atual)):
    with db() as con:
        rows = con.execute("SELECT t.id,t.nome,t.usuario,t.funcao,t.telefone,t.admin,t.ativo,"
                           "(SELECT COUNT(*) FROM sessoes s WHERE s.tecnico_id=t.id) atendimentos "
                           "FROM tecnicos t ORDER BY t.ativo DESC, t.nome").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/tecnicos")
def criar_tecnico(d: TecnicoIn, a=Depends(admin_atual)):
    with db() as con:
        try:
            cur = con.execute("INSERT INTO tecnicos(nome,usuario,senha,funcao,telefone,admin,criado_em) "
                              "VALUES(?,?,?,?,?,?,?)", (d.nome.strip(), d.usuario.lower(), hash_senha(d.senha),
                                                        d.funcao.strip(), d.telefone.strip(), int(d.admin), iso()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Usuário já existe")
        auditar(con, a["usuario"], "tecnico_criado", d.usuario)
    return {"id": cur.lastrowid}


@app.patch("/api/tecnicos/{tid}")
def editar_tecnico(tid: int, d: TecnicoUpd, a=Depends(admin_atual)):
    campos = {k: v for k, v in d.model_dump().items() if v is not None}
    if "senha" in campos:
        campos["senha"] = hash_senha(campos["senha"])
    for k in ("admin", "ativo"):
        if k in campos:
            campos[k] = int(campos[k])
    if tid == a["id"] and (campos.get("ativo") == 0 or campos.get("admin") == 0):
        raise HTTPException(400, "Você não pode desativar nem tirar o administrador de si mesmo")
    if not campos:
        return {"ok": True}
    with db() as con:
        sets = ",".join(f"{k}=?" for k in campos)
        if not con.execute(f"UPDATE tecnicos SET {sets} WHERE id=?", (*campos.values(), tid)).rowcount:
            raise HTTPException(404, "Técnico não encontrado")
        if campos.get("ativo") == 0 or "senha" in campos:
            con.execute("DELETE FROM tokens WHERE tecnico_id=?", (tid,))
        auditar(con, a["usuario"], "tecnico_editado", f"{tid}: {sorted(campos)}")
    return {"ok": True}


# ---------------------------------------------------------------- grupos
@app.get("/api/grupos")
def listar_grupos(_=Depends(tecnico_atual)):
    with db() as con:
        rows = con.execute("SELECT g.*, (SELECT COUNT(*) FROM equipamentos e WHERE e.grupo_id=g.id) qtd "
                           "FROM grupos g ORDER BY g.nome COLLATE NOCASE").fetchall()
        novos = con.execute("SELECT COUNT(*) FROM equipamentos WHERE grupo_id IS NULL").fetchone()[0]
    return {"grupos": [dict(r) for r in rows], "aguardando_vinculo": novos}


@app.post("/api/grupos")
def criar_grupo(d: GrupoIn, t=Depends(tecnico_atual)):
    codigo = secrets.token_hex(4).upper()
    with db() as con:
        cur = con.execute("INSERT INTO grupos(nome,cnpj,codigo_ativacao,criado_em) VALUES(?,?,?,?)",
                          (d.nome.strip(), d.cnpj, codigo, iso()))
        auditar(con, t["usuario"], "grupo_criado", d.nome)
    return {"id": cur.lastrowid, "codigo_ativacao": codigo}


@app.patch("/api/grupos/{gid}")
def editar_grupo(gid: int, d: GrupoIn, t=Depends(tecnico_atual)):
    with db() as con:
        if not con.execute("UPDATE grupos SET nome=?, cnpj=? WHERE id=?", (d.nome.strip(), d.cnpj, gid)).rowcount:
            raise HTTPException(404, "Grupo não encontrado")
        auditar(con, t["usuario"], "grupo_editado", f"{gid}: {d.nome}")
    return {"ok": True}


@app.post("/api/grupos/{gid}/novo-codigo")
def novo_codigo(gid: int, t=Depends(tecnico_atual)):
    codigo = secrets.token_hex(4).upper()
    with db() as con:
        con.execute("UPDATE grupos SET codigo_ativacao=? WHERE id=?", (codigo, gid))
        auditar(con, t["usuario"], "codigo_renovado", str(gid))
    return {"codigo_ativacao": codigo}


# ---------------------------------------------------------------- equipamentos
def _equip_dict(r, mostrar_senha: bool = False) -> dict:
    d = dict(r)
    d.pop("token_hash", None)
    senha = d.pop("senha_remota", None)
    if mostrar_senha:
        d["senha_remota"] = senha
    d["tem_senha_remota"] = bool(senha)
    d["info"] = json.loads(d.pop("info_json") or "{}")
    d["numero_fmt"] = fmt_numero(r["numero_acesso"])
    d["online"] = bool(r["ultimo_contato"]) and \
        (agora() - datetime.fromisoformat(r["ultimo_contato"])).total_seconds() < AGENTE_ONLINE_SEG
    return d


@app.get("/api/equipamentos")
def listar_equipamentos(grupo_id: int | None = None, sem_grupo: bool = False, _=Depends(tecnico_atual)):
    q = "SELECT e.*, g.nome grupo_nome FROM equipamentos e LEFT JOIN grupos g ON g.id=e.grupo_id"
    args: tuple = ()
    if sem_grupo:
        q += " WHERE e.grupo_id IS NULL"
    elif grupo_id:
        q += " WHERE e.grupo_id=?"
        args = (grupo_id,)
    with db() as con:
        rows = con.execute(q + " ORDER BY e.nome COLLATE NOCASE", args).fetchall()
    return [_equip_dict(r) for r in rows]


@app.get("/api/equipamentos/{eid}")
def ver_equipamento(eid: int, t=Depends(tecnico_atual)):
    with db() as con:
        r = con.execute("SELECT e.*, g.nome grupo_nome FROM equipamentos e LEFT JOIN grupos g ON g.id=e.grupo_id "
                        "WHERE e.id=?", (eid,)).fetchone()
        if not r:
            raise HTTPException(404, "Equipamento não encontrado")
        auditar(con, t["usuario"], "equipamento_consultado", str(eid))
    return _equip_dict(r, mostrar_senha=True)


@app.patch("/api/equipamentos/{eid}")
def editar_equipamento(eid: int, d: EquipamentoUpd, t=Depends(tecnico_atual)):
    campos = {k: v for k, v in d.model_dump().items() if v is not None}
    if not campos:
        return {"ok": True}
    with db() as con:
        sets = ",".join(f"{k}=?" for k in campos)
        if not con.execute(f"UPDATE equipamentos SET {sets} WHERE id=?", (*campos.values(), eid)).rowcount:
            raise HTTPException(404, "Equipamento não encontrado")
        auditar(con, t["usuario"], "equipamento_editado", f"{eid}: {campos}")
    return {"ok": True}


@app.post("/api/equipamentos/vincular")
def vincular(d: Vincular, t=Depends(tecnico_atual)):
    numero = re.sub(r"\D", "", d.numero_acesso)
    with db() as con:
        e = con.execute("SELECT * FROM equipamentos WHERE numero_acesso=?", (numero,)).fetchone()
        if not e:
            raise HTTPException(404, "Número de acesso não encontrado. Confira com o cliente.")
        if e["grupo_id"]:
            raise HTTPException(409, "Este equipamento já está vinculado a um grupo")
        if not con.execute("SELECT 1 FROM grupos WHERE id=?", (d.grupo_id,)).fetchone():
            raise HTTPException(404, "Grupo não encontrado")
        con.execute("UPDATE equipamentos SET grupo_id=?, nome=?, vinculado_em=?, vinculado_por=? WHERE id=?",
                    (d.grupo_id, d.nome.strip(), iso(), t["usuario"], e["id"]))
        enviar_mensagem(con, e["id"], "aviso", {
            "titulo": NOME_APP() + " – equipamento cadastrado",
            "texto": f"Este computador foi cadastrado no suporte da {EMPRESA} como \"{d.nome.strip()}\" "
                     f"por {ident_tecnico(t)}.\nNenhum acesso é feito sem o seu conhecimento."})
        auditar(con, t["usuario"], "equipamento_vinculado", f"{e['id']} nº {numero} -> grupo {d.grupo_id}")
    return {"id": e["id"]}


@app.post("/api/equipamentos/{eid}/continuo")
def pedir_continuo(eid: int, d: ContinuoIn, t=Depends(tecnico_atual)):
    """Pede ao cliente, na tela dele, o consentimento para o acesso contínuo."""
    with db() as con:
        e = con.execute("SELECT * FROM equipamentos WHERE id=?", (eid,)).fetchone()
        if not e:
            raise HTTPException(404, "Equipamento não encontrado")
        if not e["grupo_id"]:
            raise HTTPException(409, "Vincule o equipamento a um grupo primeiro")
        con.execute("UPDATE mensagens SET status='cancelado' WHERE equipamento_id=? AND tipo='pedir_continuo' "
                    "AND status IN ('aguardando','entregue')", (eid,))
        enviar_mensagem(con, eid, "pedir_continuo", {
            "modo": d.modo, "tecnico": ident_tecnico(t),
            "texto": (f"{ident_tecnico(t)}, da {EMPRESA}, solicita ACESSO CONTÍNUO a este computador "
                      f"para suporte técnico.\n\nModo escolhido: {MODOS[d.modo].upper()}\n"
                      + ("Você será AVISADO sempre que um técnico acessar ou executar algo, sem precisar autorizar."
                         if d.modo == "notificar" else
                         "Você precisará AUTORIZAR cada atendimento; durante ele, só será avisado dos procedimentos.")
                      + "\n\nTudo fica registrado em relatório (LGPD). Você pode cancelar a qualquer momento em "
                        "Iniciar > Help me! > Cancelar acesso contínuo.\n\nAutoriza o acesso contínuo?")},
            validade_min=60 * 24)
        con.execute("UPDATE equipamentos SET continuo='aguardando_cliente' WHERE id=?", (eid,))
        auditar(con, t["usuario"], "continuo_solicitado", f"{eid}: {d.modo}")
    return {"ok": True}


@app.post("/api/equipamentos/{eid}/continuo/desativar")
def desativar_continuo(eid: int, t=Depends(tecnico_atual)):
    with db() as con:
        e = con.execute("SELECT * FROM equipamentos WHERE id=?", (eid,)).fetchone()
        if not e:
            raise HTTPException(404, "Equipamento não encontrado")
        _encerrar_continuo(con, e, f"desativado por {ident_tecnico(t)}")
        auditar(con, t["usuario"], "continuo_desativado", str(eid))
    return {"ok": True}


def _encerrar_continuo(con, e, motivo: str):
    con.execute("UPDATE equipamentos SET continuo='inativo', modo_acesso='autorizar', senha_remota=NULL WHERE id=?",
                (e["id"],))
    con.execute("UPDATE mensagens SET status='cancelado' WHERE equipamento_id=? AND tipo='pedir_continuo' "
                "AND status IN ('aguardando','entregue')", (e["id"],))
    # troca a senha do acesso remoto por uma aleatória que ninguém conhece
    enviar_mensagem(con, e["id"], "senha", {"senha": nova_senha_remota()}, validade_min=60 * 24 * 7)
    enviar_mensagem(con, e["id"], "continuo_encerrado", {
        "titulo": NOME_APP() + " – acesso contínuo encerrado",
        "texto": f"O acesso contínuo da {EMPRESA} a este computador foi encerrado ({motivo})."},
        validade_min=60 * 24 * 7)


# ---------------------------------------------------------------- scripts
@app.get("/api/scripts")
def listar_scripts(_=Depends(tecnico_atual)):
    with db() as con:
        rows = con.execute("SELECT * FROM scripts ORDER BY ativo DESC, nome COLLATE NOCASE").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = sorted(set(PLACEHOLDER_RE.findall(r["conteudo"])))
        out.append(d)
    return out


@app.post("/api/scripts")
def criar_script(d: ScriptIn, a=Depends(admin_atual)):
    with db() as con:
        cur = con.execute("INSERT INTO scripts(nome,descricao,conteudo,requer_autorizacao,timeout_s,ativo,"
                          "atualizado_por,atualizado_em) VALUES(?,?,?,?,?,?,?,?)",
                          (d.nome, d.descricao, d.conteudo, int(d.requer_autorizacao), d.timeout_s, int(d.ativo),
                           a["usuario"], iso()))
        auditar(con, a["usuario"], "script_criado", d.nome)
    return {"id": cur.lastrowid}


@app.put("/api/scripts/{sid}")
def editar_script(sid: int, d: ScriptIn, a=Depends(admin_atual)):
    with db() as con:
        if not con.execute("UPDATE scripts SET nome=?,descricao=?,conteudo=?,requer_autorizacao=?,timeout_s=?,"
                           "ativo=?,atualizado_por=?,atualizado_em=? WHERE id=?",
                           (d.nome, d.descricao, d.conteudo, int(d.requer_autorizacao), d.timeout_s, int(d.ativo),
                            a["usuario"], iso(), sid)).rowcount:
            raise HTTPException(404, "Script não encontrado")
        auditar(con, a["usuario"], "script_editado", f"{sid}: {d.nome}")
    return {"ok": True}


# ---------------------------------------------------------------- sessões
def _sessao(con, sid: int):
    s = con.execute("SELECT s.*, e.nome equip_nome, e.hostname, e.rustdesk_id, e.numero_acesso, e.modo_acesso, e.info_json, "
                    "e.continuo, g.nome grupo_nome, g.cnpj, t.nome tecnico_nome, t.usuario tecnico_usuario, "
                    "t.funcao tecnico_funcao, t.telefone tecnico_telefone FROM sessoes s "
                    "JOIN equipamentos e ON e.id=s.equipamento_id LEFT JOIN grupos g ON g.id=e.grupo_id "
                    "JOIN tecnicos t ON t.id=s.tecnico_id WHERE s.id=?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "Sessão não encontrada")
    return s


def _modo_notificar(e) -> bool:
    return e["continuo"] == "ativo" and e["modo_acesso"] == "notificar"


@app.post("/api/sessoes")
def iniciar_sessao(d: SessaoIn, t=Depends(tecnico_atual)):
    with db() as con:
        e = con.execute("SELECT * FROM equipamentos WHERE id=?", (d.equipamento_id,)).fetchone()
        if not e:
            raise HTTPException(404, "Equipamento não encontrado")
        if not e["grupo_id"]:
            raise HTTPException(409, "Vincule o equipamento a um grupo antes de atender")
        aberta = con.execute("SELECT id FROM sessoes WHERE equipamento_id=? AND status='em_andamento'",
                             (d.equipamento_id,)).fetchone()
        if aberta:  # o painel oferece "participar" desse atendimento
            raise HTTPException(409, f"SESSAO_ABERTA:{aberta['id']}")
        if not con.execute("SELECT 1 FROM tipos_problema WHERE nome=? AND ativo=1", (d.tipo_problema,)).fetchone():
            raise HTTPException(422, "Escolha o tipo de problema")
        notificar = _modo_notificar(e)
        chamado_id = d.chamado_id
        if d.pedido_id:  # atendimento de um pedido de ajuda feito pelo cliente na janela do Help me!
            pa = con.execute("SELECT * FROM pedidos_ajuda WHERE id=? AND equipamento_id=?",
                             (d.pedido_id, d.equipamento_id)).fetchone()
            if not pa:
                raise HTTPException(404, "Pedido de ajuda não encontrado")
            chamado_id = chamado_id or pa["chamado_id"]
        os_numero = d.os_numero or (f"CH{chamado_id}" if chamado_id else None)
        cur = con.execute("INSERT INTO sessoes(equipamento_id,tecnico_id,tecnico_ident,os_numero,motivo,inicio,"
                          "liberacao,liberado_por,liberado_em,tipo_problema,chamado_id,pedido_id) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (d.equipamento_id, t["id"], ident_tecnico(t), os_numero, d.motivo, iso(),
                           "notificado" if notificar else "aguardando",
                           "acesso contínuo (modo notificar)" if notificar else None, iso() if notificar else None,
                           d.tipo_problema, chamado_id, d.pedido_id))
        sid = cur.lastrowid
        if d.pedido_id:
            con.execute("UPDATE pedidos_ajuda SET status='em_atendimento', sessao_id=?, atendido_por=?, atendido_em=? "
                        "WHERE id=?", (sid, ident_tecnico(t), iso(), d.pedido_id))
        texto_base = (f"{ident_tecnico(t)}, da {EMPRESA}, "
                      + ("iniciou" if notificar else "quer iniciar") + f" um atendimento neste computador ({d.tipo_problema})."
                      + (f"\nOS: {d.os_numero}" if d.os_numero else "") + (f"\nMotivo: {d.motivo}" if d.motivo else ""))
        if notificar:
            enviar_mensagem(con, e["id"], "aviso", {"titulo": NOME_APP() + " – atendimento iniciado", "texto": texto_base},
                            sessao_id=sid)
            from .plugins import disparar_automaticos
            disparar_automaticos(con, sid)
        else:
            enviar_mensagem(con, e["id"], "pedir_inicio", {"tecnico": ident_tecnico(t),
                            "texto": texto_base + "\n\nAo autorizar, o técnico poderá acessar e executar os "
                                                  "procedimentos necessários durante este atendimento; você será "
                                                  "avisado de cada um. Tudo fica registrado em relatório (LGPD)."
                                                  "\nAutoriza o atendimento?"}, sessao_id=sid)
        auditar(con, t["usuario"], "sessao_iniciada", f"{sid} equip {d.equipamento_id}")
    if chamado_id:  # o chamado no gestor passa para "Em atendimento"
        threading.Thread(target=gestor_mover_chamado, args=(chamado_id, "atendimento", t["nome"]), daemon=True).start()
    return {"id": sid, "liberacao": "notificado" if notificar else "aguardando"}


@app.get("/api/tipos-problema")
def listar_tipos(_=Depends(tecnico_atual)):
    with db() as con:
        rows = con.execute("SELECT * FROM tipos_problema ORDER BY ativo DESC, ordem, nome").fetchall()
    return [dict(r) | {"categorias": json.loads(r["categorias"] or "[]")} for r in rows]


@app.post("/api/tipos-problema")
def criar_tipo(d: TipoIn, a=Depends(admin_atual)):
    with db() as con:
        try:
            cur = con.execute("INSERT INTO tipos_problema(nome,descricao,categorias,ordem,ativo) VALUES(?,?,?,?,?)",
                              (d.nome.strip(), d.descricao, json.dumps(d.categorias), d.ordem, int(d.ativo)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Já existe um tipo com esse nome")
        auditar(con, a["usuario"], "tipo_criado", d.nome)
    return {"id": cur.lastrowid}


@app.put("/api/tipos-problema/{tid}")
def editar_tipo(tid: int, d: TipoIn, a=Depends(admin_atual)):
    with db() as con:
        antigo = con.execute("SELECT nome FROM tipos_problema WHERE id=?", (tid,)).fetchone()
        if not antigo:
            raise HTTPException(404, "Tipo não encontrado")
        try:
            con.execute("UPDATE tipos_problema SET nome=?,descricao=?,categorias=?,ordem=?,ativo=? WHERE id=?",
                        (d.nome.strip(), d.descricao, json.dumps(d.categorias), d.ordem, int(d.ativo), tid))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Já existe um tipo com esse nome")
        if antigo["nome"] != d.nome.strip():  # renomear mantém o histórico agrupado
            con.execute("UPDATE sessoes SET tipo_problema=? WHERE tipo_problema=?", (d.nome.strip(), antigo["nome"]))
        auditar(con, a["usuario"], "tipo_editado", f"{tid}: {d.nome}")
    return {"ok": True}


@app.get("/api/tipos-problema/resumo")
def resumo_tipos(dias: int = 30, _=Depends(tecnico_atual)):
    desde = iso(agora() - timedelta(days=max(1, min(dias, 3650))))
    with db() as con:
        rows = con.execute("SELECT tipo_problema, status, inicio, fim FROM sessoes WHERE inicio>=?", (desde,)).fetchall()
    out: dict = {}
    for r in rows:
        t = out.setdefault(r["tipo_problema"] or "Sem tipo", {"total": 0, "concluido": 0, "pendente": 0,
                                                              "em_andamento": 0, "segundos": 0, "encerrados": 0})
        t["total"] += 1
        t[r["status"]] = t.get(r["status"], 0) + 1
        if r["fim"]:
            t["encerrados"] += 1
            t["segundos"] += (datetime.fromisoformat(r["fim"]) - datetime.fromisoformat(r["inicio"])).total_seconds()
    for t in out.values():
        m = int(t["segundos"] / t["encerrados"]) if t["encerrados"] else 0
        t["tempo_medio"] = f"{m // 3600:02d}:{m % 3600 // 60:02d}:{m % 60:02d}"
    return {"dias": dias, "tipos": out}


@app.patch("/api/sessoes/{sid}")
def editar_sessao(sid: int, d: SessaoUpd, t=Depends(tecnico_atual)):
    campos = {k: v for k, v in d.model_dump().items() if v is not None}
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Atendimento já encerrado")
        if "tipo_problema" in campos and not con.execute("SELECT 1 FROM tipos_problema WHERE nome=? AND ativo=1",
                                                         (campos["tipo_problema"],)).fetchone():
            raise HTTPException(422, "Tipo de problema inválido")
        if campos:
            con.execute(f"UPDATE sessoes SET {','.join(k + '=?' for k in campos)} WHERE id=?", (*campos.values(), sid))
            auditar(con, t["usuario"], "sessao_editada", f"{sid}: {campos}")
    return {"ok": True}


@app.post("/api/sessoes/{sid}/participar")
def participar(sid: int, t=Depends(tecnico_atual)):
    """Outro técnico entra no mesmo atendimento (acesso simultâneo); fica no relatório."""
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Atendimento já encerrado")
        if s["tecnico_id"] != t["id"]:
            novo = con.execute("INSERT OR IGNORE INTO participantes VALUES(?,?,?,?)",
                               (sid, t["id"], ident_tecnico(t), iso())).rowcount
            if novo and s["liberacao"] in ("autorizada", "notificado"):
                enviar_mensagem(con, s["equipamento_id"], "aviso", {
                    "titulo": NOME_APP() + " – técnico adicional",
                    "texto": f"{ident_tecnico(t)} entrou no atendimento junto com {s['tecnico_ident']}."}, sessao_id=sid)
            auditar(con, t["usuario"], "participou", str(sid))
    return {"ok": True}


@app.post("/api/sessoes/{sid}/pedir-novamente")
def pedir_novamente(sid: int, t=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento" or s["liberacao"] not in ("aguardando", "negada", "sem_resposta"):
            raise HTTPException(409, "Não há pedido de autorização pendente")
        con.execute("UPDATE mensagens SET status='cancelado' WHERE sessao_id=? AND tipo='pedir_inicio' "
                    "AND status IN ('aguardando','entregue')", (sid,))
        con.execute("UPDATE sessoes SET liberacao='aguardando' WHERE id=?", (sid,))
        enviar_mensagem(con, s["equipamento_id"], "pedir_inicio", {"tecnico": s["tecnico_ident"], "texto":
                        f"{s['tecnico_ident']}, da {EMPRESA}, quer iniciar um atendimento neste computador."
                        "\n\nAo autorizar, o técnico poderá executar os procedimentos necessários durante este "
                        "atendimento; você será avisado de cada um. Tudo fica registrado (LGPD).\nAutoriza o atendimento?"},
                        sessao_id=sid)
    return {"ok": True}


@app.get("/api/sessoes")
def listar_sessoes(status: str | None = None, tipo: str | None = None, limite: int = 100, _=Depends(tecnico_atual)):
    q = ("SELECT s.id,s.status,s.inicio,s.fim,s.os_numero,s.relatorio,s.tecnico_ident,s.gestor_status,s.tipo_problema,"
         "s.gestor_detalhe,e.nome equip_nome,g.nome grupo_nome,t.nome tecnico_nome FROM sessoes s "
         "JOIN equipamentos e ON e.id=s.equipamento_id LEFT JOIN grupos g ON g.id=e.grupo_id "
         "JOIN tecnicos t ON t.id=s.tecnico_id")
    cond, args = [], []
    if status:
        cond.append("s.status=?"); args.append(status)
    if tipo:
        cond.append("s.tipo_problema=?"); args.append(tipo)
    if cond:
        q += " WHERE " + " AND ".join(cond)
    with db() as con:
        rows = con.execute(q + " ORDER BY s.id DESC LIMIT ?", (*args, min(limite, 500))).fetchall()
    return [dict(r) | {"duracao": duracao(r["inicio"], r["fim"])} for r in rows]


@app.get("/api/sessoes/{sid}")
def ver_sessao(sid: int, _=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        ex = con.execute("SELECT id,script_nome,params_json,status,requer_autorizacao,solicitado_em,autorizado_por,"
                         "respondido_em,inicio,fim,exit_code,saida FROM execucoes WHERE sessao_id=? ORDER BY id",
                         (sid,)).fetchall()
        from .plugins import planos_da_sessao
        planos = planos_da_sessao(con, sid)
        part = [dict(r) for r in con.execute("SELECT tecnico_ident, entrou_em FROM participantes WHERE sessao_id=?",
                                             (sid,))]
    d = dict(s)
    d["info"] = json.loads(d.pop("info_json") or "{}")
    return d | {"duracao": duracao(s["inicio"], s["fim"]), "numero_fmt": fmt_numero(s["numero_acesso"]),
                "execucoes": [dict(e) for e in ex], "planos": planos, "participantes": part}


@app.post("/api/sessoes/{sid}/executar")
def executar(sid: int, d: ExecutarIn, t=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Sessão encerrada")
        if s["liberacao"] not in ("autorizada", "notificado"):
            raise HTTPException(409, "Aguardando o cliente autorizar o atendimento")
        sc = con.execute("SELECT * FROM scripts WHERE id=? AND ativo=1", (d.script_id,)).fetchone()
        if not sc:
            raise HTTPException(404, "Script não encontrado ou inativo")
        conteudo = sc["conteudo"]
        faltando = set(PLACEHOLDER_RE.findall(conteudo)) - set(d.params)
        if faltando:
            raise HTTPException(422, f"Informe: {', '.join(sorted(faltando))}")
        for k, v in d.params.items():
            if not PARAM_RE.match(v):
                raise HTTPException(422, f"Valor inválido em '{k}' (use letras, números e . : - / ,)")
        conteudo = PLACEHOLDER_RE.sub(lambda m: d.params[m.group(1)], conteudo)
        # O cliente já liberou o atendimento: dentro dele só os scripts com cadeado pedem nova autorização.
        requer = bool(sc["requer_autorizacao"])
        expira = iso(agora() + timedelta(minutes=EXEC_VALIDADE_MIN))
        cur = con.execute("INSERT INTO execucoes(sessao_id,equipamento_id,script_id,script_nome,conteudo,params_json,"
                          "requer_autorizacao,timeout_s,solicitado_em,expira_em) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (sid, s["equipamento_id"], sc["id"], sc["nome"], conteudo, json.dumps(d.params),
                           int(requer), sc["timeout_s"], iso(), expira))
        eid = cur.lastrowid
        payload, assinatura = assinar({
            "execucao_id": eid, "equipamento_id": s["equipamento_id"], "sessao_id": sid,
            "script": sc["nome"], "descricao": sc["descricao"] or "", "tecnico": s["tecnico_ident"],
            "conteudo_sha256": sha256(conteudo), "requer_autorizacao": requer, "notificar": not requer,
            "timeout_s": sc["timeout_s"], "expira_em": expira})
        con.execute("UPDATE execucoes SET payload=?, assinatura=? WHERE id=?", (payload, assinatura, eid))
        auditar(con, t["usuario"], "script_solicitado", f"sessao {sid}: {sc['nome']} {d.params}")
        acordar(s["equipamento_id"])
    return {"id": eid, "requer_autorizacao": requer}


@app.post("/api/sessoes/{sid}/encerrar")
def encerrar(sid: int, d: EncerrarIn, t=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
        if s["status"] != "em_andamento":
            raise HTTPException(409, "Sessão já encerrada")
        fim = iso()
        con.execute("UPDATE execucoes SET status='cancelado' WHERE sessao_id=? AND status IN ('aguardando','entregue')",
                    (sid,))
        con.execute("UPDATE mensagens SET status='cancelado' WHERE sessao_id=? AND status IN ('aguardando','entregue')",
                    (sid,))
        from .plugins import cancelar_planos
        cancelar_planos(con, sid)
        con.execute("UPDATE sessoes SET status=?, nota=?, fim=? WHERE id=?", (d.status, d.nota, fim, sid))
        con.execute("UPDATE pedidos_ajuda SET status=? WHERE sessao_id=?", (d.status, sid))
        from .remoto import encerrar_canais_da_sessao
        encerrar_canais_da_sessao(con, sid, "atendimento encerrado")
        if s["liberacao"] in ("autorizada", "notificado"):
            enviar_mensagem(con, s["equipamento_id"], "aviso", {
                "titulo": NOME_APP() + " – atendimento encerrado",
                "texto": f"{s['tecnico_ident']} encerrou o atendimento ({duracao(s['inicio'], fim)}). "
                         f"Situação: {'concluído' if d.status == 'concluido' else 'pendente'}."})
        s = _sessao(con, sid)
        ex = con.execute("SELECT * FROM execucoes WHERE sessao_id=? ORDER BY id", (sid,)).fetchall()
        from .plugins import html_planos
        part = con.execute("SELECT tecnico_ident, entrou_em FROM participantes WHERE sessao_id=?", (sid,)).fetchall()
        extra_part = "".join(f"{html.escape(p['tecnico_ident'])} (desde {local(p['entrou_em'])})<br>" for p in part)
        from .remoto import html_tela_e_chat
        arq = gerar_relatorio(s, ex, html_tela_e_chat(con, sid) + html_planos(con, sid), extra_part)
        con.execute("UPDATE sessoes SET relatorio=? WHERE id=?", (arq.name, sid))
        auditar(con, t["usuario"], "sessao_encerrada", f"{sid}: {d.status}")
    gestor = enviar_gestor(sid)
    return {"sessao_id": sid, "status": d.status, "duracao": duracao(s["inicio"], fim), "relatorio": arq.name,
            "gestor": gestor}


@app.post("/api/sessoes/{sid}/reenviar-gestor")
def reenviar_gestor(sid: int, _=Depends(tecnico_atual)):
    return enviar_gestor(sid, forcar=True)


@app.get("/api/sessoes/{sid}/relatorio")
def baixar_relatorio(sid: int, _=Depends(tecnico_atual)):
    with db() as con:
        s = _sessao(con, sid)
    if not s["relatorio"]:
        raise HTTPException(404, "Relatório gerado ao encerrar a sessão")
    return FileResponse(REPORT_DIR / s["relatorio"], media_type="text/html", filename=s["relatorio"])


# ---------------------------------------------------------------- integração opcional com o gestor de OS
def resumo_sessao(s, execucoes, planos_resumo=None) -> dict:
    return {
        "evento": "atendimento_remoto_encerrado", "origem": "Help me!", "sessao_id": s["id"],
        "os_numero": s["os_numero"], "status": s["status"], "tipo_problema": s["tipo_problema"],
        "motivo": s["motivo"], "nota": s["nota"],
        "empresa": {"nome": s["grupo_nome"], "cnpj": s["cnpj"]},
        "equipamento": {"nome": s["equip_nome"], "hostname": s["hostname"], "numero_acesso": s["numero_acesso"]},
        "tecnico": {"nome": s["tecnico_nome"], "usuario": s["tecnico_usuario"], "funcao": s["tecnico_funcao"]},
        "inicio": s["inicio"], "fim": s["fim"], "duracao": duracao(s["inicio"], s["fim"]),
        "duracao_minutos": round((datetime.fromisoformat(s["fim"]) - datetime.fromisoformat(s["inicio"]))
                                 .total_seconds() / 60, 1) if s["fim"] else None,
        "liberacao_cliente": {"situacao": s["liberacao"], "por": s["liberado_por"], "em": s["liberado_em"]},
        "procedimentos": [{"script": x["script_nome"], "parametros": json.loads(x["params_json"] or "{}"),
                           "status": x["status"], "autorizado_por": x["autorizado_por"],
                           "inicio": x["inicio"], "fim": x["fim"], "exit_code": x["exit_code"]} for x in execucoes],
        "relatorio_arquivo": s["relatorio"],
        "plugins": planos_resumo,
        "chamado_id": s["chamado_id"], "equipamento_id": s["equipamento_id"],
    }


def gestor_cfg() -> tuple[bool, str, str]:
    """(ativo, URL base do gestor, chave). Aceita também a URL antiga completa do webhook."""
    with db() as con:
        ativo = cfg_get(con, "gestor_ativo") == "1"
        url, token = cfg_get(con, "gestor_url"), cfg_get(con, "gestor_token")
    url = url.strip().rstrip("/")
    if "/api/" in url:
        url = url[:url.index("/api/")]
    return ativo and bool(url), url, token


def gestor_req(metodo: str, caminho: str, corpo: dict | None = None, timeout: float = 10) -> httpx.Response:
    ativo, url, token = gestor_cfg()
    if not ativo:
        raise RuntimeError("integração com o gestor desligada")
    return httpx.request(metodo, url + caminho, json=corpo, timeout=timeout,
                         headers={"Authorization": f"Bearer {token}", "X-Api-Key": token})


def gestor_mover_chamado(chamado_id: int, status: str, tecnico: str = "", solucao: str = ""):
    try:
        gestor_req("POST", f"/api/helpme/chamados/{chamado_id}/status",
                   {"status": status, "tecnico": tecnico, "solucao": solucao})
    except Exception:
        pass  # o gestor recebe o resultado completo no fim do atendimento (webhook)


def enviar_gestor(sid: int, forcar: bool = False) -> dict:
    ativo, _, _ = gestor_cfg()
    with db() as con:
        s = _sessao(con, sid)
        ex = con.execute("SELECT * FROM execucoes WHERE sessao_id=? ORDER BY id", (sid,)).fetchall()
    if not ativo:
        with db() as con:
            con.execute("UPDATE sessoes SET gestor_status='desativado' WHERE id=?", (sid,))
        return {"status": "desativado"}
    if s["status"] == "em_andamento":
        return {"status": "erro", "detalhe": "Sessão ainda aberta"}
    from .plugins import resumo_planos
    with db() as con:
        pr = resumo_planos(con, sid)
        pa = con.execute("SELECT * FROM pedidos_ajuda WHERE sessao_id=?", (sid,)).fetchone()
    corpo = resumo_sessao(s, ex, pr)
    if pa:
        corpo["pedido_ajuda"] = {"nome": pa["nome"], "telefone": pa["telefone"], "problema": pa["problema"]}
    if s["relatorio"] and (REPORT_DIR / s["relatorio"]).exists():
        corpo["relatorio_html"] = (REPORT_DIR / s["relatorio"]).read_text(encoding="utf-8")
    try:
        r = gestor_req("POST", "/api/helpme/webhook", corpo)
        status = "enviado" if r.status_code < 300 else "erro"
        detalhe = f"HTTP {r.status_code}"
        if r.status_code < 300 and not s["chamado_id"]:
            cid = (r.json() or {}).get("chamado_id")
            if cid:
                with db() as con:
                    con.execute("UPDATE sessoes SET chamado_id=? WHERE id=?", (cid, sid))
    except Exception as e:  # gestor fora do ar: o relatório continua salvo aqui
        status, detalhe = "erro", str(e)[:200]
    with db() as con:
        con.execute("UPDATE sessoes SET gestor_status=?, gestor_detalhe=?, gestor_em=? WHERE id=?",
                    (status, detalhe, iso(), sid))
    return {"status": status, "detalhe": detalhe}


@app.get("/api/config/gestor")
def ver_cfg_gestor(_=Depends(admin_atual)):
    with db() as con:
        return {"ativo": cfg_get(con, "gestor_ativo") == "1", "url": cfg_get(con, "gestor_url"),
                "tem_token": bool(cfg_get(con, "gestor_token"))}


@app.put("/api/config/gestor")
def salvar_cfg_gestor(d: GestorCfg, a=Depends(admin_atual)):
    if d.ativo and not d.url.startswith(("https://", "http://")):
        raise HTTPException(422, "Informe o endereço do gestor de OS (https://...)")
    with db() as con:
        cfg_set(con, "gestor_ativo", "1" if d.ativo else "0")
        cfg_set(con, "gestor_url", d.url.strip())
        if d.token is not None:
            cfg_set(con, "gestor_token", d.token.strip())
        auditar(con, a["usuario"], "config_gestor", f"ativo={d.ativo} url={d.url}")
    return {"ok": True}


@app.post("/api/config/gestor/testar")
def testar_gestor(_=Depends(admin_atual)):
    ativo, url, _ = gestor_cfg()
    if not url:
        raise HTTPException(422, "Endereço não configurado")
    try:
        r = gestor_req("POST", "/api/helpme/webhook", {"evento": "teste", "origem": "Help me!", "quando": iso()})
        return {"ok": r.status_code < 300, "detalhe": f"HTTP {r.status_code}" + (" (chave recusada)" if r.status_code == 403 else "")}
    except Exception as e:
        return {"ok": False, "detalhe": str(e)[:200]}


# ---------------------------------------------------------------- relatório
STATUS_TXT = {"ok": "Executado", "erro": "Erro", "negado": "Negado pelo usuário", "aguardando": "Não entregue",
              "entregue": "Não executado (sem retorno do agente)", "autorizado": "Autorizado, sem resultado",
              "cancelado": "Cancelado", "expirado": "Expirado", "executando": "Em execução"}
LIBERACAO_TXT = {"autorizada": "Autorizado pelo usuário", "notificado": "Acesso contínuo – usuário notificado",
                 "negada": "Negado pelo usuário", "aguardando": "Sem resposta do usuário",
                 "sem_resposta": "Sem resposta do usuário"}


def gerar_relatorio(s, execucoes, extra_html: str = "", participantes_html: str = "") -> Path:
    e = html.escape
    ini = datetime.fromisoformat(s["inicio"]).astimezone(TZ)
    base = f"{s['grupo_nome'] or 'sem-grupo'}_{s['equip_nome'] or s['hostname']}"
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9_]+", "-", base).strip("-")[:60]
    nome = f"{ini:%Y-%m-%d_%H%M}_{slug}_sessao{s['id']}.html"
    linhas = []
    for i, x in enumerate(execucoes, 1):
        params = json.loads(x["params_json"] or "{}")
        quem = f"{x['autorizado_por'] or 'usuário'} em {local(x['respondido_em'])}"
        if not x["requer_autorizacao"]:
            aut = "Usuário notificado (acesso contínuo)"
        elif x["status"] == "negado":
            aut = f"Negado por {quem}"
        elif x["respondido_em"]:
            aut = f"Autorizado por {quem}"
        else:
            aut = "Sem resposta"
        linhas.append(
            f"<tr><td>{i}</td><td>{local(x['solicitado_em'])}</td><td><b>{e(x['script_nome'])}</b>"
            f"{'<br><small>' + e(', '.join(f'{k}={v}' for k, v in params.items())) + '</small>' if params else ''}</td>"
            f"<td>{e(aut)}</td><td>{e(STATUS_TXT.get(x['status'], x['status']))}"
            f"{'' if x['exit_code'] is None else ' (código ' + str(x['exit_code']) + ')'}</td>"
            f"<td>{duracao(x['inicio'], x['fim']) if x['inicio'] else '-'}</td></tr>"
            f"<tr class=out><td></td><td colspan=5><details><summary>Comando e saída</summary>"
            f"<pre>{e(x['conteudo'])}</pre><pre>{e((x['saida'] or '')[:20000])}</pre></details></td></tr>")
    status = {"concluido": "Concluído", "pendente": "Pendente"}.get(s["status"], s["status"])
    mc = marca_atual()
    logo = marca_imagem_data_uri("logo")
    cab_marca = (f"<div style='display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:8px'>"
                 f"<div style='display:flex;align-items:center;gap:10px'><img src='{marca_imagem_data_uri('icone')}' alt='' "
                 f"style='width:40px;height:40px'><span style='font:700 26px system-ui;color:{mc['cor_nome']}'>{e(mc['nome'])}</span></div>"
                 + (f"<img src='{logo}' alt='{e(EMPRESA)}' style='max-height:48px;max-width:200px'>" if logo else "")
                 + f"</div><h1>Relatório de acesso remoto nº {s['id']}</h1>")
    lib = LIBERACAO_TXT.get(s["liberacao"], s["liberacao"] or "-")
    if s["liberado_por"] and s["liberacao"] in ("autorizada", "negada"):
        lib += f" – {s['liberado_por']} em {local(s['liberado_em'])}"
    tec = f"{e(s['tecnico_nome'])}" + (f" – {e(s['tecnico_funcao'])}" if s["tecnico_funcao"] else "") + \
          f" <small>(usuário {e(s['tecnico_usuario'])}{' · ' + e(s['tecnico_telefone']) if s['tecnico_telefone'] else ''})</small>"
    doc = f"""<!DOCTYPE html><html lang=pt-BR><head><meta charset=utf-8><title>Relatório sessão {s['id']}</title>
<style>body{{font:14px system-ui,sans-serif;max-width:960px;margin:24px auto;color:#222}}h1{{font-size:20px}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}}
.kv td:first-child{{width:190px;color:#555}}pre{{background:#f5f5f5;padding:8px;white-space:pre-wrap;font-size:12px}}
tr.out td{{border-bottom:2px solid #bbb}}small{{color:#666}}</style></head><body>
{cab_marca}
<table class=kv>
<tr><td>Prestador</td><td>{e(EMPRESA)}</td></tr>
<tr><td>Técnico responsável</td><td><b>{tec}</b></td></tr>
{'<tr><td>Também participaram</td><td>' + participantes_html + '</td></tr>' if participantes_html else ''}
<tr><td>Empresa / grupo</td><td>{e(s['grupo_nome'] or '-')} {e(s['cnpj'] or '')}</td></tr>
<tr><td>Equipamento</td><td>{e(s['equip_nome'] or '-')} ({e(s['hostname'] or '')}) – nº de acesso {fmt_numero(s['numero_acesso'])}</td></tr>
<tr><td>Tipo de problema</td><td><b>{e(s['tipo_problema'] or '-')}</b></td></tr>
<tr><td>OS</td><td>{e(s['os_numero'] or '-')}</td></tr>
<tr><td>Motivo</td><td>{e(s['motivo'] or '-')}</td></tr>
<tr><td>Liberação do cliente</td><td>{e(lib)}</td></tr>
<tr><td>Início</td><td>{local(s['inicio'])}</td></tr>
<tr><td>Fim</td><td>{local(s['fim'])}</td></tr>
<tr><td>Duração</td><td><b>{duracao(s['inicio'], s['fim'])}</b></td></tr>
<tr><td>Status</td><td><b>{status}</b></td></tr>
<tr><td>Observação</td><td>{e(s['nota'] or '-')}</td></tr></table>
<h2>Scripts avulsos ({len(execucoes)})</h2>
<table><tr><th>#</th><th>Solicitado</th><th>Procedimento</th><th>Autorização (LGPD)</th><th>Resultado</th><th>Tempo</th></tr>
{''.join(linhas) or '<tr><td colspan=6>Nenhum script avulso executado.</td></tr>'}</table>
{extra_html}
<p><small>Gerado em {local(iso())} pelo {e(mc['nome'])} {VERSAO}. Comandos executados somente durante o atendimento e
conforme a autorização do cliente.</small></p></body></html>"""
    p = REPORT_DIR / nome
    p.write_text(doc, encoding="utf-8")
    return p


# ---------------------------------------------------------------- agente
@app.get("/agente/chave-publica")
def chave_publica():
    return {"chave_publica": CHAVE_PUBLICA_B64}


def _novo_numero(con) -> str:
    while True:
        n = str(secrets.randbelow(900_000_000) + 100_000_000)
        if not con.execute("SELECT 1 FROM equipamentos WHERE numero_acesso=?", (n,)).fetchone():
            return n


@app.post("/agente/registrar")
def registrar(d: Registro, request: Request):
    ip = request.client.host if request.client else "?"
    with db() as con:
        g = None
        if d.codigo_ativacao.strip():
            g = con.execute("SELECT * FROM grupos WHERE codigo_ativacao=?",
                            (d.codigo_ativacao.strip().upper(),)).fetchone()
            if not g:
                auditar(con, ip, "registro_negado", d.hostname)
                raise HTTPException(403, "Código de ativação inválido")
        else:  # limite simples contra cadastro em massa sem código
            recentes = con.execute("SELECT COUNT(*) FROM equipamentos WHERE grupo_id IS NULL AND criado_em>?",
                                   (iso(agora() - timedelta(hours=1)),)).fetchone()[0]
            if recentes >= 30:
                raise HTTPException(429, "Muitos cadastros pendentes. Tente mais tarde.")
        tok = secrets.token_urlsafe(32)
        numero = _novo_numero(con)
        cur = con.execute("INSERT INTO equipamentos(grupo_id,nome,hostname,usuario_windows,rustdesk_id,numero_acesso,"
                          "token_hash,info_json,ultimo_contato,criado_em,vinculado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (g["id"] if g else None, d.hostname, d.hostname, d.usuario_windows, d.rustdesk_id, numero,
                           sha256(tok), json.dumps(d.info), iso(), iso(), iso() if g else None))
        auditar(con, d.hostname, "equipamento_registrado", f"nº {numero} grupo {g['nome'] if g else '-'} ip {ip}")
    return {"equipamento_id": cur.lastrowid, "token": tok, "grupo": g["nome"] if g else "",
            "numero_acesso": numero, "numero_fmt": fmt_numero(numero), "empresa": EMPRESA}


@app.post("/agente/heartbeat")
def heartbeat(d: Heartbeat, eq=Depends(equipamento_atual)):
    with db() as con:
        sets, args = ["ultimo_contato=?"], [iso()]
        if d.usuario_windows:
            sets.append("usuario_windows=?"); args.append(d.usuario_windows)
        if d.rustdesk_id:
            sets.append("rustdesk_id=?"); args.append(d.rustdesk_id)
        if d.info is not None:
            sets.append("info_json=?"); args.append(json.dumps(d.info))
        sets.append("portatil=?"); args.append(int(d.portatil))
        con.execute(f"UPDATE equipamentos SET {','.join(sets)} WHERE id=?", (*args, eq["id"]))
        con.execute("UPDATE execucoes SET status='expirado' WHERE equipamento_id=? AND status='aguardando' "
                    "AND expira_em<?", (eq["id"], iso()))
        # pedidos ao cliente que venceram sem resposta
        for m in con.execute("SELECT * FROM mensagens WHERE equipamento_id=? AND status IN ('aguardando','entregue') "
                             "AND expira_em<?", (eq["id"], iso())).fetchall():
            con.execute("UPDATE mensagens SET status='expirado' WHERE id=?", (m["id"],))
            if m["tipo"] == "pedir_inicio":
                con.execute("UPDATE sessoes SET liberacao='sem_resposta' WHERE id=? AND liberacao='aguardando'",
                            (m["sessao_id"],))
            if m["tipo"] == "pedir_continuo":
                con.execute("UPDATE equipamentos SET continuo='inativo' WHERE id=? AND continuo='aguardando_cliente'",
                            (eq["id"],))
        rows = con.execute("SELECT x.id,x.conteudo,x.payload,x.assinatura FROM execucoes x JOIN sessoes s "
                           "ON s.id=x.sessao_id WHERE x.equipamento_id=? AND x.status='aguardando' "
                           "AND s.status='em_andamento' ORDER BY x.id", (eq["id"],)).fetchall()
        for r in rows:
            con.execute("UPDATE execucoes SET status='entregue', entregue_em=? WHERE id=?", (iso(), r["id"]))
        msgs = con.execute("SELECT id,payload,assinatura FROM mensagens WHERE equipamento_id=? AND status='aguardando' "
                           "ORDER BY id", (eq["id"],)).fetchall()
        for m in msgs:
            con.execute("UPDATE mensagens SET status='entregue', entregue_em=? WHERE id=?", (iso(), m["id"]))
        from .plugins import planos_para_agente
        planos = planos_para_agente(con, eq["id"])
        from .remoto import estado_para_agente, chat_para_agente
        estado = estado_para_agente(con, eq["id"])
        chat = chat_para_agente(con, eq["id"])
    return {"tarefas": [dict(r) for r in rows], "mensagens": [dict(m) for m in msgs], "planos": planos,
            "marca": marca_atual()["nome"], "estado": estado, "chat": chat}


def _exec_do_equip(con, xid: int, eq) -> sqlite3.Row:
    x = con.execute("SELECT * FROM execucoes WHERE id=? AND equipamento_id=?", (xid, eq["id"])).fetchone()
    if not x:
        raise HTTPException(404, "Execução não encontrada")
    return x


@app.post("/agente/execucoes/{xid}/resposta")
def resposta(xid: int, d: Resposta, eq=Depends(equipamento_atual)):
    with db() as con:
        x = _exec_do_equip(con, xid, eq)
        if x["status"] != "entregue":
            raise HTTPException(409, f"Execução em status {x['status']}")
        con.execute("UPDATE execucoes SET status=?, autorizado_por=?, respondido_em=? WHERE id=?",
                    ("autorizado" if d.autorizado else "negado", d.usuario[:120], iso(), xid))
        auditar(con, f"{eq['hostname']}\\{d.usuario}", "autorizou" if d.autorizado else "negou", x["script_nome"])
    return {"ok": True}


@app.post("/agente/execucoes/{xid}/resultado")
def resultado(xid: int, d: Resultado, eq=Depends(equipamento_atual)):
    with db() as con:
        x = _exec_do_equip(con, xid, eq)
        if x["status"] not in ("autorizado", "entregue"):
            raise HTTPException(409, f"Execução em status {x['status']}")
        if x["requer_autorizacao"] and x["status"] != "autorizado":
            raise HTTPException(409, "Execução exige autorização do usuário")
        con.execute("UPDATE execucoes SET status=?, exit_code=?, saida=?, inicio=?, fim=? WHERE id=?",
                    ("ok" if d.exit_code == 0 else "erro", d.exit_code, d.saida[:100000], d.inicio, d.fim, xid))
    return {"ok": True}


@app.post("/agente/mensagens/{mid}/resposta")
def resposta_mensagem(mid: int, d: RespostaMsg, eq=Depends(equipamento_atual)):
    usuario = d.usuario[:120]
    with db() as con:
        m = con.execute("SELECT * FROM mensagens WHERE id=? AND equipamento_id=?", (mid, eq["id"])).fetchone()
        if not m:
            raise HTTPException(404, "Mensagem não encontrada")
        if m["status"] != "entregue":
            raise HTTPException(409, f"Mensagem em status {m['status']}")
        con.execute("UPDATE mensagens SET status='respondida', aceito=?, respondido_por=?, respondido_em=? WHERE id=?",
                    (int(d.aceito), usuario, iso(), mid))
        dados = json.loads(m["dados_json"])
        if m["tipo"] == "pedir_inicio":
            con.execute("UPDATE sessoes SET liberacao=?, liberado_por=?, liberado_em=? WHERE id=? "
                        "AND status='em_andamento'", ("autorizada" if d.aceito else "negada", usuario, iso(),
                                                      m["sessao_id"]))
            if d.aceito:
                from .plugins import disparar_automaticos
                disparar_automaticos(con, m["sessao_id"])
        elif m["tipo"] == "pedir_continuo":
            if d.aceito:
                senha = nova_senha_remota() if dados["modo"] == "notificar" else None
                con.execute("UPDATE equipamentos SET continuo='ativo', modo_acesso=?, continuo_consentido_por=?, "
                            "continuo_consentido_em=?, senha_remota=? WHERE id=?",
                            (dados["modo"], usuario, iso(), senha, eq["id"]))
                if senha:  # senha fixa do acesso remoto para entrar sem o clique do cliente
                    enviar_mensagem(con, eq["id"], "senha", {"senha": senha}, validade_min=60 * 24 * 7)
            else:
                con.execute("UPDATE equipamentos SET continuo='recusado' WHERE id=?", (eq["id"],))
        auditar(con, f"{eq['hostname']}\\{usuario}", f"{m['tipo']}_{'aceito' if d.aceito else 'recusado'}",
                d.detalhe[:200])
    return {"ok": True}


@app.post("/agente/revogar")
def revogar(d: Revogar, eq=Depends(equipamento_atual)):
    """O próprio cliente cancelou o acesso contínuo no computador dele."""
    with db() as con:
        _encerrar_continuo(con, eq, f"cancelado pelo usuário {d.usuario[:60]}")
        auditar(con, f"{eq['hostname']}\\{d.usuario}", "continuo_revogado_pelo_cliente")
    return {"ok": True}


# ---------------------------------------------------------------- painel web
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def painel():
    return (STATIC / "index.html").read_text(encoding="utf-8")


from . import plugins  # noqa: E402,F401  (registra as rotas dos plugins de ação)


# ---------------------------------------------------------------- marca (nome, ícone, logo da loja)
MARCA_DIR = DATA_DIR / "marca"
MARCA_DIR.mkdir(parents=True, exist_ok=True)
MARCA_PADRAO = {"nome": "Help me!", "cor_nome": "#E84D4D", "cor_icone": "#2EB872"}
COR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def marca_atual() -> dict:
    con = sqlite3.connect(DB_PATH, timeout=10)  # só leitura, fora da trava: pode ser chamado dentro de db()
    con.row_factory = sqlite3.Row
    try:
        m = {k: cfg_get(con, "marca_" + k, v) for k, v in MARCA_PADRAO.items()}
    finally:
        con.close()
    m["tem_icone"] = any((MARCA_DIR / f"icone.{x}").exists() for x in ("png", "svg"))
    m["tem_logo"] = any((MARCA_DIR / f"logo.{x}").exists() for x in ("png", "svg"))
    return m


def icone_padrao_svg(cor: str) -> str:
    """Quadrado verde arredondado, ! centralizado e sinal de conexão no canto inferior direito."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="2" y="2" width="96" height="96" rx="24" '
            f'fill="{cor}"/><path d="M41.5 18h17l-3 41h-11z" fill="#fff" stroke="#fff" stroke-width="3" stroke-linejoin="round"/>'
            f'<circle cx="50" cy="73" r="7.5" fill="#fff"/><path d="M81 88A7 7 0 0 1 88 81M74 88A14 14 0 0 1 88 74M67 88A21 21 0 0 1 88 67" '
            f'fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round"/></svg>')


@app.get("/api/marca")
def ver_marca():
    """Público: o painel (antes do login), o agente e o relatório leem o nome e as cores."""
    return marca_atual()


@app.get("/api/marca/{qual}")
def imagem_marca(qual: str):
    if qual not in ("icone", "logo"):
        raise HTTPException(404, "Não encontrado")
    for ext, tipo in (("png", "image/png"), ("svg", "image/svg+xml")):
        p = MARCA_DIR / f"{qual}.{ext}"
        if p.exists():
            return FileResponse(p, media_type=tipo, headers={"Cache-Control": "no-cache",
                                                             "Content-Security-Policy": "script-src 'none'"})
    if qual == "icone":
        return HTMLResponse(icone_padrao_svg(marca_atual()["cor_icone"]), media_type="image/svg+xml")
    raise HTTPException(404, "Logo da loja não enviada")


class MarcaIn(BaseModel):
    nome: str = Field(min_length=1, max_length=40)
    cor_nome: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    cor_icone: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


@app.put("/api/config/marca")
def salvar_marca(d: MarcaIn, a=Depends(admin_atual)):
    with db() as con:
        for k in MARCA_PADRAO:
            cfg_set(con, "marca_" + k, getattr(d, k).strip())
        auditar(con, a["usuario"], "marca_alterada", d.nome)
    return marca_atual()


@app.put("/api/marca/{qual}")
async def enviar_imagem_marca(qual: str, request: Request, a=Depends(admin_atual)):
    if qual not in ("icone", "logo"):
        raise HTTPException(404, "Não encontrado")
    corpo = await request.body()
    if not corpo or len(corpo) > 2 * 1024 * 1024:
        raise HTTPException(422, "Envie uma imagem PNG ou SVG de até 2 MB")
    if corpo.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = "png"
    elif b"<svg" in corpo[:2048].lower() and not re.search(rb"<script|on\w+\s*=|javascript:", corpo, re.I):
        ext = "svg"
    else:
        raise HTTPException(422, "Formato aceito: PNG ou SVG (sem scripts)")
    for x in ("png", "svg"):
        (MARCA_DIR / f"{qual}.{x}").unlink(missing_ok=True)
    (MARCA_DIR / f"{qual}.{ext}").write_bytes(corpo)
    with db() as con:
        auditar(con, a["usuario"], f"marca_{qual}_enviado", ext)
    return marca_atual()


@app.delete("/api/marca/{qual}")
def apagar_imagem_marca(qual: str, a=Depends(admin_atual)):
    if qual not in ("icone", "logo"):
        raise HTTPException(404, "Não encontrado")
    for x in ("png", "svg"):
        (MARCA_DIR / f"{qual}.{x}").unlink(missing_ok=True)
    return marca_atual()


def marca_imagem_data_uri(qual: str) -> str:
    """Imagem embutida no relatório (o HTML salvo fica completo, sem depender do servidor)."""
    for ext, tipo in (("png", "image/png"), ("svg", "image/svg+xml")):
        p = MARCA_DIR / f"{qual}.{ext}"
        if p.exists():
            return f"data:{tipo};base64," + base64.b64encode(p.read_bytes()).decode()
    if qual == "icone":
        return "data:image/svg+xml;base64," + base64.b64encode(icone_padrao_svg(marca_atual()["cor_icone"]).encode()).decode()
    return ""


from . import remoto  # noqa: E402,F401  (tela remota, chat, pedidos de ajuda, site de downloads)
