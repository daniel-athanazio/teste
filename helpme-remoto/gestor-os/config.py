import os

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARQ_ENV = os.path.join(RAIZ, ".env")
_AMBIENTE = dict(os.environ)          # variaveis do sistema (tem prioridade menor que o .env)

# Situacoes do painel, na ordem de exibicao: (chave, titulo, palavras que identificam o status do Uniplus)
SITUACOES = [
    ("abertas", "Recém-abertas", ["abert", "nova", "entrada", "em analise", "em análise", "analise"]),
    ("aprovacao", "Aguardando aprovação", ["aprova", "orcamento", "orçamento"]),
    ("pecas", "Aguardando peças", ["peca", "peça"]),
    ("pendentes", "Em andamento", ["pendente", "andamento", "execu", "bancada", "agendad"]),
    ("terceirizada", "Na terceirizada", ["terceir", "externo"]),
    ("retirada", "Prontas para retirada", ["retirada", "pronto", "pronta", "conclu", "disponivel", "disponível"]),
    ("finalizadas", "Finalizadas", ["finaliz", "entregue", "fechad", "faturad", "encerrad"]),
    ("canceladas", "Canceladas", ["cancel"]),
]
ENCERRADAS = ("finalizadas", "canceladas")

def ler_env():
    d = {}
    if os.path.exists(ARQ_ENV):
        for linha in open(ARQ_ENV, encoding="utf-8"):
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                d[k.strip()] = v.strip()
    return d

def gravar_env(mudancas):
    """Atualiza chaves no .env preservando comentarios e a ordem; depois recarrega."""
    mudancas = {k: str(v).replace("\n", " ").replace("\r", " ").strip() for k, v in mudancas.items()}
    linhas = open(ARQ_ENV, encoding="utf-8").read().splitlines() if os.path.exists(ARQ_ENV) else []
    feitas, saida = set(), []
    for l in linhas:
        k = l.split("=", 1)[0].strip() if "=" in l and not l.strip().startswith("#") else None
        if k in mudancas:
            saida.append(f"{k}={mudancas[k]}"); feitas.add(k)
        else:
            saida.append(l)
    saida += [f"{k}={v}" for k, v in mudancas.items() if k not in feitas]
    tmp = ARQ_ENV + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(saida) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ARQ_ENV)
    carregar()

def _chave(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s or "").lower().strip()) if unicodedata.category(ch) != "Mn")

def _data_iso(v):
    import datetime
    try:
        return datetime.date.fromisoformat(str(v).strip()).isoformat()
    except ValueError:
        return ""

def _int(v, padrao):
    try:
        return int(float(str(v).replace(",", ".")))
    except (TypeError, ValueError):
        return padrao

def carregar():
    g = globals()
    amb = {**_AMBIENTE, **ler_env()}
    E = lambda k, p="": amb.get(k, p)
    g.update(
        UNIPLUS_SERVIDOR=E("UNIPLUS_SERVIDOR").rstrip("/"),
        UNIPLUS_AUTORIZACAO=E("UNIPLUS_AUTORIZACAO"),
        UNIPLUS_CONTA=E("UNIPLUS_CONTA"),
        STATUS_MAPA={**{"4": "canceladas", "5": "finalizadas"},
                     **dict(p.split("=", 1) for p in E("STATUS_MAPA").replace(" ", "").split(",") if "=" in p and p.split("=", 1)[1])},
        SYNC_MINUTOS=max(1, _int(E("SYNC_MINUTOS"), 5)),
        SENHA_PAINEL=E("SENHA_PAINEL", "troque-esta-senha"),
        SENHA_DONO=E("SENHA_DONO"),
        CHAVE_TV=E("CHAVE_TV"),
        SEGREDO=E("SEGREDO", "dev-segredo"),
        URL_PUBLICA=E("URL_PUBLICA", "http://localhost:8000").rstrip("/"),
        NOME_LOJA=E("NOME_LOJA", "Loja"),
        WHATSAPP_LOJA="".join(c for c in E("WHATSAPP_LOJA") if c.isdigit()),
        PRAZO_PADRAO_DIAS=_int(E("PRAZO_PADRAO_DIAS"), 5),
        PRAZO_VISITA_DIAS=_int(E("PRAZO_VISITA_DIAS"), 2),
        META_MES=float(str(E("META_MES", "0") or 0).replace(",", ".") or 0),
        PRAZO_ORCAMENTO_DIAS=_int(E("PRAZO_ORCAMENTO_DIAS"), 2),
        PRAZO_TERCEIRIZADA_DIAS=_int(E("PRAZO_TERCEIRIZADA_DIAS"), 15),
        PRAZO_RETIRADA_DIAS=_int(E("PRAZO_RETIRADA_DIAS"), 180),
        ALERTA_LEGAL_DIAS=_int(E("ALERTA_LEGAL_DIAS"), 25),
        RETENCAO_MESES=_int(E("RETENCAO_MESES"), 24),
        PRAZO_CONSULTA_DIAS=_int(E("PRAZO_CONSULTA_DIAS"), 90),
        TERC_AMARELO_DIAS=_int(E("TERC_AMARELO_DIAS"), 10),
        GARANTIA_CAMPO=E("GARANTIA_CAMPO"),
        LEMBRETE_ORCAMENTO_DIAS=_int(E("LEMBRETE_ORCAMENTO_DIAS"), 0),
        LEMBRETE_RETIRADA_DIAS=",".join(str(n) for n in sorted({_int(x, 0) for x in E("LEMBRETE_RETIRADA_DIAS").split(",")} - {0})),
        RESUMO_HORA=E("RESUMO_HORA"), RESUMO_TELEFONE="".join(ch for ch in E("RESUMO_TELEFONE") if ch.isdigit()),
        PERIODO_INI=_data_iso(E("PERIODO_INI")), PERIODO_FIM=_data_iso(E("PERIODO_FIM")),
        EVENTO_MAPA={_chave(p.split("=", 1)[0]): p.split("=", 1)[1].strip()
                     for p in E("EVENTO_MAPA").split("|") if "=" in p and p.split("=", 1)[1].strip()},
        ALERTA_CHAMADO_MIN=_int(E("ALERTA_CHAMADO_MIN"), 30),
        ALERTA_AGUARDANDO_HORAS=_int(E("ALERTA_AGUARDANDO_HORAS"), 24),
        WA_TOKEN=E("WA_TOKEN"), WA_PHONE_ID=E("WA_PHONE_ID"), WA_IDIOMA=E("WA_IDIOMA", "pt_BR"),
        WA_MODELOS={
            "aprovacao": E("WA_MODELO_APROVACAO", "os_aguardando_aprovacao"),
            "retirada": E("WA_MODELO_RETIRADA", "os_pronta_retirada"),
            "finalizada": E("WA_MODELO_FINALIZADA", "os_finalizada_avaliacao"),
            "ch_aberto": E("WA_MODELO_CHAMADO_ABERTO", "chamado_aberto"),
            "ch_atendimento": E("WA_MODELO_CHAMADO_ATENDIMENTO", "chamado_em_atendimento"),
            "ch_aguardando": E("WA_MODELO_CHAMADO_AGUARDANDO", "chamado_aguardando_cliente"),
            "ch_concluido": E("WA_MODELO_CHAMADO_CONCLUIDO", "chamado_concluido_avaliacao"),
            "lembrete_orcamento": E("WA_MODELO_LEMBRETE_ORCAMENTO", "os_lembrete_orcamento"),
            "lembrete_retirada": E("WA_MODELO_LEMBRETE_RETIRADA", "os_lembrete_retirada"),
            "resumo": E("WA_MODELO_RESUMO", "resumo_diario"),
        },
        BANCO=E("BANCO", os.path.join(RAIZ, "gestor.db")),
        HELPME_URL=E("HELPME_URL").rstrip("/"),
    )
    g["DEMO"] = not g["UNIPLUS_SERVIDOR"]

carregar()
