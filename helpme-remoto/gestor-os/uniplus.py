"""Cliente da API publica do Uniplus Web + leitura tolerante dos campos da OS."""
import time, unicodedata, random, base64, json
from datetime import datetime, timedelta
import requests
import config

_token = {"valor": None, "expira": 0}

def _basic(conta=None, chave=None):
    conta = config.UNIPLUS_CONTA if conta is None else conta
    chave = config.UNIPLUS_AUTORIZACAO if chave is None else chave
    if conta:
        return base64.b64encode(f"{conta}:{chave}".encode()).decode()
    return chave

def credencial(servidor, conta, chave):
    """Credencial avulsa (nao salva), usada pelo teste da aba Configuracao."""
    return {"servidor": servidor.rstrip("/"), "basic": _basic(conta, chave), "token": None}

def _obter_token(cred=None):
    if cred is None and _token["valor"] and time.time() < _token["expira"] - 300:
        return _token["valor"]
    if cred is not None and cred.get("token"):
        return cred["token"]
    servidor = cred["servidor"] if cred else config.UNIPLUS_SERVIDOR
    basic = cred["basic"] if cred else _basic()
    r = None
    for caminho in ("/oauth/token", "/public-api/oauth/token"):
        r = requests.post(servidor + caminho,
                          headers={"Authorization": "Basic " + basic,
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          data="grant_type=client_credentials&scope=public-api", timeout=30)
        if r.status_code not in (404, 405):
            break
    r.raise_for_status()
    j = r.json()
    if cred is not None:
        cred["token"] = j["access_token"]
        return cred["token"]
    _token.update(valor=j["access_token"], expira=time.time() + int(j.get("expires_in", 3600)))
    return _token["valor"]

def _get(caminho, params=None, cred=None):
    servidor = cred["servidor"] if cred else config.UNIPLUS_SERVIDOR
    for tentativa in (1, 2):
        r = requests.get(servidor + caminho, params=params,
                         headers={"Authorization": "Bearer " + _obter_token(cred)}, timeout=60)
        if r.status_code == 401 and tentativa == 1:
            if cred is not None:
                cred["token"] = None
            else:
                _token["valor"] = None
            continue
        r.raise_for_status()
        return r.json()

def _lista(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("content", "items", "itens", "data", "registros", "result"):
            if isinstance(j.get(k), list):
                return j[k]
    return []

def _ctm(o):
    try:
        return int(o.get("currentTimeMillis") or 0)
    except (TypeError, ValueError):
        return 0

def listar_os_brutas(limite=100, max_paginas=300, desde=None, cred=None):
    """Le as OS. Descobre sozinho se 'offset' e numero da pagina ou de registro.
    Com 'desde' (currentTimeMillis) pede so as alteradas; devolve None se o servidor ignorar o filtro."""
    vistos, saida, offset, passo = set(), [], 0, 1
    for _ in range(max_paginas):
        params = {"limit": limite, "offset": offset}
        if desde:
            params["currentTimeMillis.gt"] = desde
        pagina = _lista(_get("/public-api/v1/ordem-servico", params, cred))
        if not pagina:
            break
        if desde and any(_ctm(o) <= desde for o in pagina):
            return None
        novos = [o for o in pagina if str(pega(o, "codigo", "numero", "id")) not in vistos]
        if not novos:
            break
        if offset == 1 and passo == 1 and len(novos) < len(pagina):
            passo = limite            # offset conta registros, nao paginas
            offset = len(saida) + len(novos) - passo
        for o in novos:
            vistos.add(str(pega(o, "codigo", "numero", "id")))
            saida.append(o)
        if len(pagina) < limite:
            break
        offset += passo
    return saida

def telefone_cliente(codigo):
    """Telefone do cadastro do cliente (a OS da API nao traz telefone). '' se nao achar."""
    try:
        j = _get(f"/public-api/v1/entidades/{codigo}")
    except Exception:
        return ""
    if isinstance(j, dict) and isinstance(j.get("entidade"), dict):
        j = j["entidade"]
    if isinstance(j, list):
        j = j[0] if j else {}
    if not isinstance(j, dict):
        return ""
    melhor = ""
    for k in ("celular", "telefone", "celularEntrega", "telefoneEntrega", "celularCobranca", "telefoneCobranca",
              "telefoneCelular", "whatsapp", "fone", "telefone1", "telefoneComercial", "telefone2"):
        d = "".join(ch for ch in str(j.get(k) or "") if ch.isdigit())
        if len(d) >= 10:
            return d                      # com DDD: serve tambem para o WhatsApp
        if len(d) >= 8 and not melhor:
            melhor = d                    # sem DDD: serve para a consulta (4 ultimos digitos)
    if not melhor:                         # cadastro com o telefone em outro campo: varre qualquer campo que lembre telefone
        def varre(d, nivel=0):
            achados = []
            for k, v in (d.items() if isinstance(d, dict) else enumerate(d)):
                if isinstance(v, (dict, list)) and nivel < 2:
                    achados += varre(v, nivel + 1)
                elif any(t in str(k).lower() for t in ("fone", "celular", "whats", "contato")):
                    dig = "".join(ch for ch in str(v or "") if ch.isdigit())
                    if len(dig) >= 8:
                        achados.append(dig)
            return achados
        ach = varre(j)
        if ach:
            return max(ach, key=len)
    return melhor

# ---------- leitura tolerante ----------
def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")

def pega(d, *nomes):
    """Procura o primeiro campo existente (aceita 'a.b' para campos aninhados)."""
    for n in nomes:
        v = d
        for p in n.split("."):
            v = v.get(p) if isinstance(v, dict) else None
            if v is None:
                break
        if v not in (None, "", [], {}):
            return v
    return None

def _data(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v).isoformat(timespec="minutes")
    s = str(v).strip()
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(f))], f).isoformat(timespec="minutes")
        except ValueError:
            pass
    return None

ORDEM_TESTE = ("canceladas", "terceirizada", "pecas", "aprovacao", "finalizadas", "retirada", "pendentes", "abertas")
NOMES_COD = {"4": "Cancelada", "5": "Finalizada"}

def por_palavras(texto):
    s = _norm(texto)
    for chave in ORDEM_TESTE:                      # mais especificas primeiro
        palavras = next(p for k, _, p in config.SITUACOES if k == chave)
        if any(_norm(p) in s for p in palavras):
            return chave
    return None

def classificar(status):
    return por_palavras(status) or ("pendentes" if _norm(status) else "abertas")

def classificar_os(cod, evento, iniciou):
    """Uniplus Web manda o status como numero; o texto vem do ultimo evento da OS."""
    cod = str(cod or "").strip()
    if cod in ("4", "5") and cod in config.STATUS_MAPA:
        return config.STATUS_MAPA[cod]
    if config._chave(evento) in config.EVENTO_MAPA:          # alinhamento por evento, definido na Configuracao
        return config.EVENTO_MAPA[config._chave(evento)]
    if cod in config.STATUS_MAPA:
        return config.STATUS_MAPA[cod]
    if cod and not cod.isdigit():
        return classificar(cod)
    achou = por_palavras(evento)
    if achou in ("canceladas", "finalizadas"):     # evento antigo; o codigo e quem manda nesses dois
        achou = None
    return achou or ("pendentes" if iniciou else "abertas")

def _garantia(o):
    """Marca de retorno em garantia, lida do campo extra escolhido na Configuracao."""
    if not config.GARANTIA_CAMPO or not isinstance(o, dict):
        return ""
    v = str(o.get(config.GARANTIA_CAMPO) or "").strip()
    return "" if _norm(v) in ("", "n", "nao", "0", "false", "f") else v[:40]

def normalizar(o):
    status = pega(o, "situacao.descricao", "status.descricao", "descricaoSituacao", "descricaoStatus",
                  "situacao", "status", "nomeSituacao", "statusOs") or ""
    if isinstance(status, dict):
        status = pega(status, "descricao", "nome") or ""
    cod = str(status).strip()
    evento = str(pega(o, "descricaoTipoUltimoEvento") or "")
    if cod.isdigit():
        situacao = classificar_os(cod, evento, bool(pega(o, "inicioServico")))
        rotulo = NOMES_COD.get(cod) if cod in ("4", "5") else (evento or f"Status {cod}")
    else:
        situacao, rotulo, cod = classificar(cod), cod, ""
    itens = pega(o, "itens", "servicos", "items") or []
    if isinstance(itens, str):
        try:
            itens = json.loads(itens)
        except ValueError:
            itens = []
    lista = []
    for i in itens if isinstance(itens, list) else []:
        if isinstance(i, dict):
            nome = " ".join(str(pega(i, "nomeProduto", "descricao", "descricaoProduto", "produto.nome") or "").split())[:100]
            if nome:
                try:
                    q = float(pega(i, "quantidade") or 1)
                except (TypeError, ValueError):
                    q = 1
                lista.append({"n": nome, "q": int(q) if q == int(q) else q, "u": str(pega(i, "unidadeMedida") or "")[:4]})
    desc_itens = " ".join(str(pega(i, "descricao", "descricaoProduto", "nomeProduto", "produto.nome") or "") for i in itens if isinstance(i, dict))
    problema = pega(o, "problemaDescrito", "defeito", "problema", "descricaoProblema", "observacao", "observacoes", "descricao") or desc_itens
    equipamento = pega(o, "descricaoItem", "objeto.descricao", "descricaoObjeto", "equipamento", "objeto", "produto.nome") or ""
    if isinstance(equipamento, dict):
        equipamento = pega(equipamento, "descricao", "nome") or ""
    texto = _norm(f"{problema} {desc_itens} {equipamento} {pega(o, 'area', 'tipo') or ''}")
    valor = pega(o, "valorTotal", "total", "valor", "valorLiquido") or 0
    try:
        valor = float(str(valor).replace(",", "."))
    except ValueError:
        valor = 0
    previsao = pega(o, "previsaoConclusao", "dataPrevisao", "dataPrevista", "previsao")
    prev = _data(previsao)
    if prev and len(str(previsao).strip()) <= 10:
        prev = prev[:10] + "T23:59"                 # previsao so com data vale ate o fim do dia
    doc = "".join(c for c in str(pega(o, "cnpjCpfCliente", "cpfCnpjCliente", "cliente.cnpjCpf") or "") if c.isdigit())
    return {
        "codigo": str(pega(o, "codigo", "numero", "id")),
        "cliente": str(pega(o, "nomeCliente", "cliente.nome", "cliente.razaoSocial", "razaoSocialCliente", "cliente") or "")[:120],
        "telefone": "".join(c for c in str(pega(o, "telefoneCliente", "celularCliente", "cliente.celular", "cliente.telefone", "telefone", "celular") or "") if c.isdigit()),
        "cod_cliente": str(pega(o, "codigoCliente", "cliente.codigo") or ""),
        "doc4": doc[-4:] if len(doc) >= 11 else "",
        "equipamento": " ".join(str(equipamento).split())[:120],
        "serie": str(pega(o, "numeroFabricacao", "numeroSerie", "serie", "objeto.numeroSerie", "objeto.numeroFabricacao", "objeto.chave", "chaveObjeto") or "").strip(),
        "problema": str(problema)[:400],
        "laudo": str(pega(o, "laudoTecnico", "laudo") or "")[:1500],
        "itens": json.dumps(lista[:40], ensure_ascii=False) if lista else "",
        "obs": str(pega(o, "observacao", "observacoes") or "")[:800],
        "status": str(rotulo),
        "status_cod": cod,
        "situacao": situacao,
        "tecnico": str(pega(o, "nomeAtendente", "nomeTecnico", "tecnico.nome", "tecnico", "nomeVendedor", "vendedor.nome") or ""),
        "visita": 1 if any(p in texto for p in ("visita", "instalacao", "cftv", "camera", "rede", "presencial")) else 0,
        "abertura": _data(pega(o, "dataAbertura", "data", "dataOrdemServico", "dataEmissao", "dataCadastro", "dataInclusao", "abertura")),
        "previsao": prev,
        "conclusao": _data(pega(o, "fimServico", "dataConclusao", "dataFechamento", "dataEncerramento", "dataEntrega", "dataFinalizacao")),
        "valor": valor,
        "evento_em": _data(pega(o, "dataUltmoEvento", "dataUltimoEvento")),
        "garantia": _garantia(o),
        "ctm": _ctm(o) if isinstance(o, dict) else 0,
    }

# ---------- demonstracao ----------
def os_demo():
    rnd = random.Random(7)
    nomes = ["Ana Souza", "Carlos Lima", "Mercado Bom Preço", "João Pedro", "Padaria Estrela", "Marta Reis", "Oficina do Zé", "Luiza Prado"]
    eqs = [("Notebook Dell Inspiron", "não liga"), ("Impressora HP 1102", "puxando várias folhas"), ("PC Gamer", "tela azul"),
           ("Impressora de cupom Elgin i9", "não imprime"), ("Visita técnica - CFTV 4 câmeras", "instalação"),
           ("Notebook Lenovo", "troca de tela"), ("Visita técnica - rede", "wi-fi sem alcance"), ("Epson L3150", "falha de cores")]
    sts = ["Aberta", "Aguardando aprovação", "Aguardando peças", "Em andamento", "Enviada à terceirizada",
           "Pronta para retirada", "Finalizada", "Finalizada", "Cancelada", "Em andamento", "Aberta", "Finalizada"]
    agora, saida = datetime.now(), []
    for i in range(26):
        e, p = eqs[i % len(eqs)]
        st = sts[i % len(sts)]
        ab = agora - timedelta(days=rnd.randint(0, 14), hours=rnd.randint(0, 8))
        o = {"codigo": 4100 + i, "nomeCliente": nomes[i % len(nomes)], "telefoneCliente": "219%08d" % rnd.randint(0, 99999999),
             "equipamento": e, "numeroSerie": "SN%06d" % rnd.randint(0, 999999), "defeito": p, "situacao": st,
             "nomeTecnico": rnd.choice(["Rafael", "Bruno"]), "dataAbertura": ab.strftime("%Y-%m-%dT%H:%M:%S"),
             "valorTotal": rnd.choice([80, 120, 150, 250, 380])}
        if st == "Finalizada":
            o["dataConclusao"] = (ab + timedelta(days=rnd.randint(1, 6))).strftime("%Y-%m-%dT%H:%M:%S")
        saida.append(o)
    return saida
