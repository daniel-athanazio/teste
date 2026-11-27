"""Testa a conexao com o Uniplus e mostra como os campos da OS foram lidos.
Uso:  ./venv/bin/python diagnostico.py      (nao exibe credenciais)"""
import json, collections
import config, uniplus

if config.DEMO:
    raise SystemExit("UNIPLUS_SERVIDOR vazio no .env - rodando em modo demonstracao.")
brutas = uniplus.listar_os_brutas(max_paginas=3)
print(f"OS lidas (amostra): {len(brutas)}")
if brutas:
    print("\nCampos que a API devolve:", ", ".join(sorted(brutas[0].keys())))
    print("\nComo o gestor leu a primeira OS:")
    print(json.dumps(uniplus.normalizar(brutas[0]), ensure_ascii=False, indent=2))
    cont = collections.Counter((uniplus.normalizar(b)["status"], uniplus.normalizar(b)["situacao"]) for b in brutas)
    print("\nStatus do Uniplus -> coluna do painel:")
    for (st, sit), n in cont.most_common():
        print(f"  {st!r:40} -> {sit:14} ({n})")
