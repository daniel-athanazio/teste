import os, sqlite3, json, threading
import config

_local = threading.local()

def con():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(config.BANCO, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c

def iniciar():
    c = con()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS os (
        codigo TEXT PRIMARY KEY, cliente TEXT, telefone TEXT, equipamento TEXT, serie TEXT,
        problema TEXT, status TEXT, situacao TEXT, tecnico TEXT, visita INTEGER DEFAULT 0,
        abertura TEXT, previsao TEXT, conclusao TEXT, valor REAL DEFAULT 0,
        bruto TEXT, atualizado TEXT);
    CREATE TABLE IF NOT EXISTS historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, situacao TEXT, status TEXT, quando TEXT);
    CREATE TABLE IF NOT EXISTS avaliacao (
        codigo TEXT PRIMARY KEY, nota INTEGER, comentario TEXT, quando TEXT);
    CREATE TABLE IF NOT EXISTS aviso (
        id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT, tipo TEXT, ok INTEGER, detalhe TEXT, quando TEXT);
    CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
    """)
    cols = {r[1] for r in c.execute("PRAGMA table_info(os)")}
    for nome in ("status_cod", "doc4", "cod_cliente", "laudo", "evento_em", "garantia", "itens", "obs"):
        if nome not in cols:
            c.execute(f"ALTER TABLE os ADD COLUMN {nome} TEXT DEFAULT ''")
    c.execute("CREATE TABLE IF NOT EXISTS cliente (codigo TEXT PRIMARY KEY, telefone TEXT, quando TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS tentativa (codigo TEXT, quando TEXT)")
    if c.execute("SELECT 1 FROM os WHERE bruto<>'' LIMIT 1").fetchone():     # versoes antigas guardavam a OS inteira (com CPF completo)
        c.execute("UPDATE os SET bruto=''")
        c.commit()
        c.execute("VACUUM")
    for arq in (config.BANCO, config.BANCO + "-wal", config.BANCO + "-shm"):
        if os.path.exists(arq):
            os.chmod(arq, 0o600)
    c.commit()

def meta(chave, valor=None):
    c = con()
    if valor is None:
        r = c.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
        return r["valor"] if r else None
    c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, str(valor)))
    c.commit()
