"""Gera os modelos de impressao de OS para o Uniplus (iReport/JasperReports), com QR code de consulta."""
import re, uuid, sys, threading
from xml.sax.saxutils import escape

BASE_QR = "https://os.fninfohouse.com.br"          # definidos por gerar()
CHAVE = ""
LOGO = ""                                           # logo em base64 (vazio = sem logo)
_trava = threading.Lock()

def logo_expr():
    """A logo vai embutida no proprio modelo: nao depende de internet nem de arquivo no servidor do Uniplus."""
    return f'new java.io.ByteArrayInputStream(java.util.Base64.getDecoder().decode("{LOGO}"))'
RODAPE = "fninfohouse.com.br  ·  WhatsApp (21) 96438-2098  ·  Seg a sex e feriados 9h–18h  ·  Sáb e dom 9h–14h"
TERMOS_CURTO = ("GARANTIA, contada da retirada, sobre o serviço executado e as peças substituídas: 90 dias para manutenção de computadores, "
    "notebooks e impressoras; 30 dias para instalação e configuração de sistemas e programas. Não cobre mau uso, quedas, contato com líquidos, "
    "oscilações da rede elétrica, vírus ou programas instalados após a entrega, lacre violado e reparo feito por terceiros. "
    "ORÇAMENTO válido por 10 dias; nenhum serviço é executado sem aprovação. Orçamento não aprovado: taxa de diagnóstico de R$ 50,00, paga na retirada; "
    "se o equipamento não tiver reparo, nada é cobrado. BACKUP dos arquivos é responsabilidade do cliente, salvo se contratado nesta OS. "
    "RETIRADA em até 6 meses (180 dias) após o aviso de conclusão; vencido o prazo sem retirada, o equipamento será considerado abandonado e poderá ser "
    "descartado de forma ambientalmente adequada ou vendido para compensar os serviços e as peças aplicados.")
TERMOS_CUPOM = ("Apresente este cupom na retirada. Garantia a partir da retirada: 90 dias (sistemas e programas: 30 dias). "
    "Orçamento não aprovado: taxa de R$ 50,00; sem reparo, sem custo. Retirada em até 6 meses após o aviso de conclusão; "
    "depois disso o equipamento é considerado abandonado e pode ser descartado ou vendido para cobrir serviços e peças.")
VERSO = [
 ("1. Orçamento", "O orçamento discrimina mão de obra, peças, condições de pagamento e prazo de execução, e vale por 10 dias contados do recebimento pelo cliente (CDC, art. 40, § 1º). Nenhum serviço é executado sem aprovação do cliente.\nOrçamento não aprovado pelo cliente: será cobrada taxa de diagnóstico de R$ 50,00, paga na retirada do equipamento. Se o equipamento não tiver reparo, nada é cobrado."),
 ("2. Garantia", "Contada a partir da retirada do equipamento, sobre o serviço executado e as peças substituídas:\n  •  Manutenção de computadores e notebooks, com as peças aplicadas: 90 dias\n  •  Manutenção de impressoras, com as peças aplicadas: 90 dias\n  •  Instalação e configuração de sistemas e programas: 30 dias\nA garantia não cobre: mau uso, quedas, contato com líquidos, oscilações da rede elétrica, vírus ou programas instalados após a entrega, lacre violado, reparo feito por terceiros e defeitos diferentes do que foi reparado."),
 ("3. Dados e backup", "O cliente é responsável pela cópia de segurança dos seus arquivos antes da entrega do equipamento, salvo quando o serviço de backup for contratado nesta ordem de serviço."),
 ("4. Retirada do equipamento", "O cliente será avisado da conclusão do serviço, ou do orçamento não aprovado, por mensagem no telefone ou WhatsApp informado nesta OS, e se compromete a manter esse contato atualizado.\nO prazo máximo para retirada é de 6 meses (180 dias) contados dessa mensagem. Antes do fim do prazo, a loja enviará um novo aviso com 30 dias de antecedência.\nVencido o prazo sem retirada, o equipamento será considerado abandonado (Código Civil, art. 1.275, III) e poderá ser encaminhado ao descarte ambientalmente adequado ou vendido para compensar os serviços e as peças aplicados. Havendo saldo após a compensação, ele fica à disposição do cliente."),
 ("5. Ciência", "Declaro que li e concordo com os termos acima e que as informações do equipamento e dos acessórios entregues estão corretas."),
]

CAMPOS = {  # nome: classe (iguais aos do exemplo oficial ordemservico-comfotos.jrxml)
 "doc_iddocumento": "Long", "doc_codigo": "Long", "doc_dataabertura": "sql.Timestamp", "doc_previsaoconclusao": "sql.Date",
 "doc_atendente": "String", "doc_problema": "String", "doc_descricaoitem": "String", "doc_descricaoitemproduto": "String",
 "doc_descricaoitemobjeto": "String", "doc_caracteristicas": "String", "doc_localatendimento": "String", "doc_numerofabricacao": "String",
 "doc_observacao": "String", "doc_laudotecnico": "String", "doc_valortotal": "BigDecimal", "doc_valorprodutos": "BigDecimal",
 "doc_descontosubtotal": "BigDecimal", "doc_condicaopagamento": "String",
 "filial_razaosocial": "String", "filial_cnpj": "String", "filial_endereco": "String", "filial_numero": "String", "filial_bairro": "String",
 "filial_cidade": "String", "filial_uf": "String", "filial_cep": "String", "filial_telefone": "String", "filial_email": "String",
 "cliente_codigo": "String", "cliente_nome": "String", "cliente_endereco": "String", "cliente_numero": "String", "cliente_complemento": "String",
 "cliente_bairro": "String", "cliente_cidade": "String", "cliente_estado": "String", "cliente_cep": "String", "cliente_cnpjcpf": "String",
 "cliente_telefone": "String", "cliente_celular": "String", "cliente_email": "String",
 "item_id": "Long", "item_sequencia": "Integer", "item_codigoproduto": "String", "item_nomeproduto": "String",
 "item_quantidade": "BigDecimal", "item_precounitario": "BigDecimal", "item_valortotal": "BigDecimal",
}
CL = {"Long": "java.lang.Long", "Integer": "java.lang.Integer", "String": "java.lang.String", "BigDecimal": "java.math.BigDecimal",
      "sql.Timestamp": "java.sql.Timestamp", "sql.Date": "java.sql.Date"}

# Consulta propria (nao usa a view do sistema): le direto as tabelas, so com as colunas que os modelos usam.
# Motivo: no Uniplus Web a view ordemservicoitem_view so devolveu as OS finalizadas.
SQL_OS = """select os.id as doc_iddocumento, os.codigo as doc_codigo, os.data as doc_dataabertura, os.previsaoconclusao as doc_previsaoconclusao,
 at.nome as doc_atendente, os.problemadescrito as doc_problema, os.descricaoitem as doc_descricaoitem, proos.nome as doc_descricaoitemproduto,
 obj.descricao as doc_descricaoitemobjeto, os.caracteristicas as doc_caracteristicas,
 case when os.localatendimento = 0 then 'Balcão' when os.localatendimento = 1 then 'Externo' when os.localatendimento = 2 then 'Suporte Online' else 'Terceirizado' end as doc_localatendimento,
 os.numerofabricacao as doc_numerofabricacao, os.observacao as doc_observacao, os.laudotecnico as doc_laudotecnico,
 os.valor as doc_valortotal, (os.valor + coalesce(os.descontosubtotal, 0)) as doc_valorprodutos, os.descontosubtotal as doc_descontosubtotal,
 condpag.descricao as doc_condicaopagamento,
 f.razaosocial as filial_razaosocial, f.cnpj as filial_cnpj, f.endereco as filial_endereco, f.numero as filial_numero, f.bairro as filial_bairro,
 f.cidade as filial_cidade, f.estado as filial_uf, f.cep as filial_cep, f.telefone as filial_telefone, f.email as filial_email,
 cli.codigo as cliente_codigo, cli.nome as cliente_nome, cli.endereco as cliente_endereco, cli.numeroendereco as cliente_numero,
 cli.complemento as cliente_complemento, cli.bairro as cliente_bairro, cidcli.nome as cliente_cidade, estcli.codigo as cliente_estado,
 cli.cep as cliente_cep, cli.cnpjcpf as cliente_cnpjcpf, cli.telefone as cliente_telefone, cli.celular as cliente_celular, cli.email as cliente_email,
 i.id as item_id, i.contador as item_sequencia, pro.codigo as item_codigoproduto, pro.nome as item_nomeproduto,
 i.quantidade as item_quantidade, i.preco as item_precounitario, (i.total + coalesce(i.descontosubtotal, 0)) as item_valortotal
 from ordemservico os
 left join ordemservicoitem i on os.id = i.idordemservico
 left join produto pro on i.idproduto = pro.id
 left join produto proos on os.idproduto = proos.id
 left join objeto obj on os.idobjeto = obj.id
 left join condicaopagamento condpag on os.idcondicaopagamento = condpag.id
 left join filial f on os.idfilial = f.id
 left join entidade cli on os.idcliente = cli.id
 left join entidade at on os.idatendente = at.id
 left join cidade cidcli on cli.idcidade = cidcli.id
 left join estado estcli on cidcli.idestado = estcli.id
 where os.id = $P{idDocumento}"""

def u(): return str(uuid.uuid4())
def nz(c): return f'($F{{{c}}} == null ? "" : $F{{{c}}}.trim())'
def lista(partes, sep=" · "):
    """Expressao Java que une textos com o separador, pulando os vazios (sem repetir subexpressoes)."""
    j = (' + "%s" + ' % sep).join(f"({p})" for p in partes)
    return f'({j}).replaceAll("^({sep})+|({sep})+$", "").replaceAll("({sep}){{2,}}", "{sep}")'
def rot(r, c): return f'({nz(c)}.isEmpty() ? "" : "{r}" + {nz(c)})'
EQUIP = f'(!{nz("doc_descricaoitem")}.isEmpty() ? {nz("doc_descricaoitem")} : (!{nz("doc_descricaoitemobjeto")}.isEmpty() ? {nz("doc_descricaoitemobjeto")} : {nz("doc_descricaoitemproduto")}))'
NUM_OS = 'new java.text.DecimalFormat("000000").format($F{doc_codigo})'
ENTRADA = '($F{doc_dataabertura} == null ? "" : new java.text.SimpleDateFormat("dd/MM/yyyy HH:mm").format($F{doc_dataabertura}))'
PREVISAO = '($F{doc_previsaoconclusao} == null ? "a combinar" : new java.text.SimpleDateFormat("dd/MM/yyyy").format($F{doc_previsaoconclusao}))'
MOEDA = lambda c: f'($F{{{c}}} == null ? "" : new java.text.DecimalFormat("#,##0.00", new java.text.DecimalFormatSymbols(new java.util.Locale("pt", "BR"))).format($F{{{c}}}))'
QTD = '($F{item_quantidade} == null ? "" : new java.text.DecimalFormat("#,##0.###", new java.text.DecimalFormatSymbols(new java.util.Locale("pt", "BR"))).format($F{item_quantidade}))'
FILIAL_END = lista([lista([nz("filial_endereco"), nz("filial_numero")], ", "), nz("filial_bairro"), lista([nz("filial_cidade"), nz("filial_uf")], "/"), rot("CEP ", "filial_cep")])
FILIAL_DOC = lista([rot("CNPJ ", "filial_cnpj"), rot("Tel. ", "filial_telefone"), nz("filial_email")])
CLI_FONE = lista([nz("cliente_telefone"), nz("cliente_celular")], "  ·  ")
CLI_END = lista([lista([nz("cliente_endereco"), nz("cliente_numero")], ", "), nz("cliente_complemento"), nz("cliente_bairro"), lista([nz("cliente_cidade"), nz("cliente_estado")], "/"), rot("CEP ", "cliente_cep")])

def fonte(tam, negrito):
    return f'<font fontName="SansSerif" size="{tam}" isBold="{"true" if negrito else "false"}" pdfFontName="{"Helvetica-Bold" if negrito else "Helvetica"}" pdfEncoding="Cp1252"/>'
def el(x, y, w, h, extra=""):
    return f'<reportElement x="{x}" y="{y}" width="{w}" height="{h}" uuid="{u()}"{extra}/>'
def txt(x, y, w, h, expr, tam=9, b=False, al="Left", estica=False, flutua=False, some=False, cor=None, quando=None, val="Top"):
    ex = ""
    if flutua: ex += ' positionType="Float"'
    if some: ex += ' isRemoveLineWhenBlank="true"'
    if cor: ex += f' forecolor="{cor}"'
    re_ = f'<reportElement x="{x}" y="{y}" width="{w}" height="{h}" uuid="{u()}"{ex}' + (f'><printWhenExpression><![CDATA[{quando}]]></printWhenExpression></reportElement>' if quando else '/>')
    return (f'<textField isStretchWithOverflow="{"true" if estica else "false"}" isBlankWhenNull="true">{re_}'
            f'<textElement textAlignment="{al}" verticalAlignment="{val}">{fonte(tam, b)}</textElement>'
            f'<textFieldExpression><![CDATA[{expr}]]></textFieldExpression></textField>')
def fixo(x, y, w, h, texto, tam=9, b=False, al="Left", flutua=False, cor=None, quando=None, val="Top", fundo=None):
    ex = ""
    if flutua: ex += ' positionType="Float"'
    if cor: ex += f' forecolor="{cor}"'
    if fundo: ex += f' mode="Opaque" backcolor="{fundo}"'
    re_ = f'<reportElement x="{x}" y="{y}" width="{w}" height="{h}" uuid="{u()}"{ex}' + (f'><printWhenExpression><![CDATA[{quando}]]></printWhenExpression></reportElement>' if quando else '/>')
    return f'<staticText>{re_}<textElement textAlignment="{al}" verticalAlignment="{val}">{fonte(tam, b)}</textElement><text><![CDATA[{texto}]]></text></staticText>'
def linha(x, y, w, esp="0.5", flutua=False, estilo=None, quando=None):
    ex = ' positionType="Float"' if flutua else ""
    re_ = f'<reportElement x="{x}" y="{y}" width="{w}" height="1" uuid="{u()}"{ex}' + (f'><printWhenExpression><![CDATA[{quando}]]></printWhenExpression></reportElement>' if quando else '/>')
    return f'<line>{re_}<graphicElement><pen lineWidth="{esp}"' + (f' lineStyle="{estilo}"' if estilo else "") + '/></graphicElement></line>'
def caixa(x, y, w, h, esp="0.5", flutua=False, quando=None, estica=False):
    ex = (' positionType="Float"' if flutua else "") + (' stretchType="RelativeToBandHeight"' if estica else "") + ' mode="Transparent"'
    re_ = f'<reportElement x="{x}" y="{y}" width="{w}" height="{h}" uuid="{u()}"{ex}' + (f'><printWhenExpression><![CDATA[{quando}]]></printWhenExpression></reportElement>' if quando else '/>')
    return f'<rectangle radius="3">{re_}<graphicElement><pen lineWidth="{esp}"/></graphicElement></rectangle>'
def imagem(x, y, w, h, expr, flutua=False, quando=None, al="Center"):
    ex = ' positionType="Float"' if flutua else ""
    re_ = f'<reportElement x="{x}" y="{y}" width="{w}" height="{h}" uuid="{u()}"{ex}' + (f'><printWhenExpression><![CDATA[{quando}]]></printWhenExpression></reportElement>' if quando else '/>')
    return f'<image scaleImage="RetainShape" hAlign="{al}" vAlign="Middle" onErrorType="Blank">{re_}<imageExpression><![CDATA[{expr}]]></imageExpression></image>'
def qr(x, y, lado, **k):
    return imagem(x, y, lado, lado, f'"{BASE_QR}/qr/" + $F{{doc_codigo}} + ".png?m={CHAVE}"', **k)
def banda(altura, conteudo, quando=None, split="Stretch"):
    pw = f'<printWhenExpression><![CDATA[{quando}]]></printWhenExpression>' if quando else ""
    return f'<band height="{altura}" splitType="{split}">{pw}{"".join(conteudo)}</band>'

def cabecalho(nome, larg, alt, margem, sql, extras=None, attrs=""):
    campos = dict(CAMPOS); campos.update(extras or {})
    f = "".join(f'<field name="{k}" class="{CL[v]}"/>' for k, v in campos.items())
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            f'xsi:schemaLocation="http://jasperreports.sourceforge.net/jasperreports http://jasperreports.sourceforge.net/xsd/jasperreport.xsd" name="{nome}" '
            f'pageWidth="{larg}" pageHeight="{alt}" columnWidth="{larg - 2 * margem}" leftMargin="{margem}" rightMargin="{margem}" topMargin="{margem}" bottomMargin="{margem}"{attrs} uuid="{u()}">'
            f'<property name="ireport.zoom" value="1.0"/><property name="ireport.x" value="0"/><property name="ireport.y" value="0"/>'
            f'<import value="net.sf.jasperreports.engine.*"/><import value="java.util.*"/>'
            f'<parameter name="idDocumento" class="java.lang.Long"><defaultValueExpression><![CDATA[]]></defaultValueExpression></parameter>'
            f'<queryString><![CDATA[{sql}]]></queryString>{f}')

# ============================================================ A4
def a4(verso=False):
    W, CZ = 547, "#555555"
    t = []
    xt = 0
    if LOGO:
        t.append(imagem(0, 0, 62, 62, logo_expr(), al="Left")); xt = 70
    t += [txt(xt, 0, 372 - xt, 30, nz("filial_razaosocial"), 12, True, estica=True),
          txt(xt, 32, 372 - xt, 20, FILIAL_END, 8, estica=True, flutua=True), txt(xt, 54, 372 - xt, 20, FILIAL_DOC, 8, estica=True, flutua=True),
          fixo(380, 2, 92, 11, "ORDEM DE SERVIÇO", 8, True, cor=CZ), txt(380, 13, 92, 28, NUM_OS, 22, True, val="Middle"),
          fixo(380, 44, 92, 22, "Acompanhe o andamento\npelo QR code →", 7, cor=CZ),
          qr(475, 0, 72), linha(0, 76, W, "1.5")]
    y = 84; lw = W // 4
    for i, (r, e) in enumerate([("ENTRADA", ENTRADA), ("PREVISÃO DE ENTREGA", PREVISAO), ("ATENDENTE", nz("doc_atendente")), ("LOCAL DO ATENDIMENTO", nz("doc_localatendimento"))]):
        t += [fixo(i * lw + 2, y, lw - 4, 9, r, 6, True, cor=CZ), txt(i * lw + 2, y + 9, lw - 4, 18, e, 9, True, estica=True)]
    y = 114
    t += [caixa(0, y, W, 72), fixo(6, y + 4, 200, 9, "CLIENTE", 6, True, cor=CZ),
          txt(6, y + 14, 380, 14, nz("cliente_nome"), 11, True), txt(390, y + 14, 151, 12, rot("Cód. ", "cliente_codigo"), 8, al="Right"),
          txt(6, y + 30, 260, 12, rot("CPF/CNPJ: ", "cliente_cnpjcpf"), 9), txt(270, y + 30, 271, 12, f'({CLI_FONE}).isEmpty() ? "" : "Telefone: " + {CLI_FONE}', 9),
          txt(6, y + 43, 535, 12, CLI_END, 9), txt(6, y + 56, 535, 12, rot("E-mail: ", "cliente_email"), 9)]
    y = 192
    t += [caixa(0, y, W, 58), fixo(6, y + 4, 200, 9, "EQUIPAMENTO", 6, True, cor=CZ),
          txt(6, y + 14, 370, 14, EQUIP, 11, True), txt(380, y + 14, 161, 12, rot("Nº série: ", "doc_numerofabricacao"), 9, True, al="Right"),
          txt(6, y + 30, 535, 24, rot("Acessórios / características: ", "doc_caracteristicas"), 9, estica=True)]
    y = 256
    t += [fixo(0, y, 300, 9, "DEFEITO RELATADO PELO CLIENTE", 6, True, cor=CZ, flutua=True), txt(0, y + 10, W, 26, nz("doc_problema"), 10, estica=True, flutua=True),
          fixo(0, y + 40, 300, 9, "LAUDO TÉCNICO", 6, True, cor=CZ, flutua=True), txt(0, y + 50, W, 26, nz("doc_laudotecnico"), 10, estica=True, flutua=True),
          txt(0, y + 80, W, 12, rot("Observações: ", "doc_observacao"), 9, estica=True, flutua=True, some=True)]
    titulo = banda(352, t)
    col = [(0, 60, "CÓDIGO", "Left"), (62, 263, "DESCRIÇÃO DOS PRODUTOS / SERVIÇOS", "Left"), (327, 50, "QTD.", "Right"), (379, 80, "V. UNITÁRIO", "Right"), (461, 84, "V. TOTAL", "Right")]
    ch = banda(16, [fixo(0, 0, W, 15, "", fundo="#E6E6E6")] + [fixo(x + 2, 2, w - 4, 11, r, 7, True, al=a) for x, w, r, a in col] + [linha(0, 15, W)])
    det = banda(15, [txt(2, 2, 58, 12, nz("item_codigoproduto"), 9), txt(64, 2, 259, 12, nz("item_nomeproduto"), 9, estica=True),
                     txt(329, 2, 46, 12, QTD, 9, al="Right"), txt(381, 2, 76, 12, MOEDA("item_precounitario"), 9, al="Right"),
                     txt(463, 2, 80, 12, MOEDA("item_valortotal"), 9, al="Right"), linha(0, 14, W, "0.25", flutua=True)], quando="new Boolean($F{item_id} != null)")
    s = [linha(0, 0, W, "1"),
         txt(0, 6, 320, 12, rot("Condição de pagamento: ", "doc_condicaopagamento"), 9),
         fixo(340, 6, 110, 12, "Subtotal", 9, al="Right"), txt(455, 6, 90, 12, MOEDA("doc_valorprodutos"), 9, al="Right"),
         fixo(340, 19, 110, 12, "Desconto", 9, al="Right"), txt(455, 19, 90, 12, MOEDA("doc_descontosubtotal"), 9, al="Right"),
         fixo(340, 34, 110, 16, "TOTAL  R$", 11, True, al="Right", val="Middle"), txt(455, 34, 90, 16, MOEDA("doc_valortotal"), 13, True, al="Right", val="Middle"),
         fixo(0, 58, 200, 9, "CONDIÇÕES DO SERVIÇO", 6, True, cor=CZ), fixo(0, 68, W, 74, TERMOS_CURTO, 7, al="Justified"),
         fixo(0, 146, W, 20, "Declaro que li e concordo com as condições acima e que as informações do equipamento e dos acessórios entregues estão corretas.", 8),
         linha(0, 200, 250), fixo(0, 202, 250, 10, "Assinatura do cliente", 7, al="Center", cor=CZ),
         linha(275, 200, 110), fixo(275, 202, 110, 10, "Data", 7, al="Center", cor=CZ),
         linha(410, 200, 137), fixo(410, 202, 137, 10, "Responsável pela loja", 7, al="Center", cor=CZ)]
    frente = banda(216, s)
    v = []
    if verso:
        y = 0
        s = v
        s += [txt(0, y, 330, 20, '"Termos e condições"', 16, True), txt(330, y + 4, 217, 14, f'"OS Nº " + {NUM_OS}', 10, True, al="Right"), linha(0, y + 24, W, "1.5")]
        y += 34
        for tit, corpo in VERSO:
            h = 12 * (corpo.count("\n") + 1 + len(corpo) // 112)
            s += [fixo(0, y, W, 13, tit, 10, True), fixo(0, y + 15, W, h, corpo, 9, al="Justified")]
            y += 15 + h + 10
        y += 40
        s += [linha(0, y, 300), fixo(0, y + 2, 300, 10, "Assinatura do cliente", 7, al="Center", cor=CZ), linha(360, y, 187), fixo(360, y + 2, 187, 10, "Data", 7, al="Center", cor=CZ)]
    pf = banda(22, [linha(0, 2, W, "0.5"), fixo(0, 5, 360, 10, RODAPE, 7, cor=CZ), txt(360, 5, 187, 10, f'"Consulta: {BASE_QR.split("//")[1]}/c/" + $F{{doc_codigo}}', 7, True, al="Right")])
    return (cabecalho("os_a4_qr_verso" if verso else "os_a4_qr", 595, 842, 24, "select v.* from (" + SQL_OS + ") v order by v.item_sequencia, v.item_id", attrs=' isSummaryNewPage="true"' if verso else "")
            + '<group name="os"><groupExpression><![CDATA[$F{doc_iddocumento}]]></groupExpression>' + f"<groupFooter>{frente}</groupFooter></group>"
            + f"<title>{titulo}</title><columnHeader>{ch}</columnHeader><detail>{det}</detail><pageFooter>{pf}</pageFooter>"
            + (f"<summary>{banda(y + 16, v)}</summary>" if verso else "") + "</jasperReport>")

# ============================================================ CUPOM 80 mm, 2 vias
def cupom():
    W = 204                      # 80 mm = 226 pt; area util de 72 mm
    C = lambda y, h, e, tam=8, b=False, **k: txt(0, y, W, h, e, tam, b, al="Center", **k)
    F = lambda y, h, s_, tam=8, b=False, **k: fixo(0, y, W, h, s_, tam, b, al="Center", **k)
    g = [C(0, 24, nz("filial_razaosocial"), 10, True, estica=True),
         C(25, 20, FILIAL_END, 7, estica=True, flutua=True), C(46, 10, FILIAL_DOC.replace(' · ', '  '), 7, estica=True, flutua=True),
         linha(0, 60, W, "1.5", flutua=True), F(64, 10, "ORDEM DE SERVIÇO", 8, True, flutua=True), C(74, 30, NUM_OS, 22, True, flutua=True, val="Middle"),
         C(105, 10, f'"Entrada " + {ENTRADA}', 8, flutua=True),
         C(116, 11, '($F{via}.intValue() == 1 ? "VIA DO CLIENTE" : "VIA DA LOJA")', 8, True, flutua=True), linha(0, 129, W, "1.5", flutua=True),
         fixo(0, 134, W, 9, "CLIENTE", 7, True, flutua=True), txt(0, 143, W, 12, nz("cliente_nome"), 10, True, estica=True, flutua=True),
         txt(0, 156, W, 10, CLI_FONE, 8, flutua=True, some=True), linha(0, 169, W, "0.5", flutua=True, estilo="Dashed"),
         fixo(0, 173, W, 9, "EQUIPAMENTO", 7, True, flutua=True), txt(0, 182, W, 12, EQUIP, 10, True, estica=True, flutua=True),
         txt(0, 195, W, 10, rot("Série: ", "doc_numerofabricacao"), 8, flutua=True, some=True),
         txt(0, 206, W, 10, rot("Acessórios: ", "doc_caracteristicas"), 8, estica=True, flutua=True, some=True), linha(0, 219, W, "0.5", flutua=True, estilo="Dashed"),
         fixo(0, 223, W, 9, "DEFEITO RELATADO", 7, True, flutua=True), txt(0, 232, W, 11, nz("doc_problema"), 8, estica=True, flutua=True),
         linha(0, 246, W, "0.5", flutua=True, estilo="Dashed"), fixo(0, 250, W, 9, "ITENS", 7, True, flutua=True)]
    alt_cab = 262
    if LOGO:                     # logo centralizada no topo; o restante desce
        g = [re.sub(r' y="(\d+)"', lambda m: f' y="{int(m.group(1)) + 50}"', e) for e in g]
        g.insert(0, imagem(52, 0, 100, 46, logo_expr()))
        alt_cab += 50
    det = banda(12, [txt(0, 1, 150, 10, f'{QTD} + "× " + {nz("item_nomeproduto")}', 8, estica=True), txt(150, 1, 54, 10, MOEDA("item_valortotal"), 8, al="Right")],
                quando="new Boolean($F{item_id} != null)")
    tot = banda(72, [txt(0, 0, 150, 10, '"Desconto"', 8, quando="new Boolean($F{doc_descontosubtotal} != null && $F{doc_descontosubtotal}.signum() > 0)"),
                     txt(150, 0, 54, 10, f'"- " + {MOEDA("doc_descontosubtotal")}', 8, al="Right", quando="new Boolean($F{doc_descontosubtotal} != null && $F{doc_descontosubtotal}.signum() > 0)"),
                     linha(0, 12, W, "1.5"), fixo(0, 15, 80, 18, "TOTAL", 9, True, val="Middle"), txt(80, 15, 124, 18, f'"R$ " + {MOEDA("doc_valortotal")}', 14, True, al="Right", val="Middle"),
                     caixa(0, 38, W, 32, "1"), F(41, 9, "Previsão de entrega", 7), C(50, 18, PREVISAO, 13, True, val="Middle")])
    cli = banda(250, [fixo(0, 238, W, 10, "- - - - - - - - - - -   corte aqui   - - - - - - - - - - -", 7, al="Center"), qr(47, 4, 110), F(116, 11, "Acompanhe sua OS online", 9, True),
                      C(127, 10, f'"{BASE_QR.split("//")[1]}/c/" + $F{{doc_codigo}}', 7),
                      fixo(0, 142, W, 66, TERMOS_CUPOM, 7, al="Center"),
                      linha(0, 210, W, "0.5"), F(213, 18, "Seg a sex e feriados 9h–18h  ·  Sáb e dom 9h–14h\nfninfohouse.com.br  ·  (21) 96438-2098", 7)],
                 quando="new Boolean($F{via}.intValue() == 1)")
    loja = banda(150, [fixo(0, 6, W, 66, TERMOS_CUPOM, 7, al="Center"),
                       fixo(0, 76, W, 28, "Declaro que li e concordo com as condições acima e que as informações do equipamento e dos acessórios entregues estão corretas.", 7, al="Center"),
                       linha(12, 132, W - 24, "0.5"), F(134, 10, "Assinatura do cliente", 7)],
                  quando="new Boolean($F{via}.intValue() == 2)")
    sql = ("select v.*, n.via from (" + SQL_OS + ") v cross join (select 1 as via union all select 2 as via) n "
           "order by n.via, v.item_sequencia, v.item_id")
    return (cabecalho("os_cupom80_qr_2vias", 226, 740, 11, sql, {"via": "Integer"})
            + '<group name="via" isStartNewPage="true"><groupExpression><![CDATA[$F{via}]]></groupExpression>'
            + f'<groupHeader>{banda(alt_cab, g)}</groupHeader><groupFooter>{tot}{cli}{loja}</groupFooter></group>'
            + f'<detail>{det}</detail></jasperReport>')

TIPOS = {"a4": ("OS-A4-QR.jrxml", lambda: a4()), "a4verso": ("OS-A4-QR-com-verso.jrxml", lambda: a4(True)),
         "cupom": ("OS-CUPOM-80mm-QR-2-vias.jrxml", lambda: cupom())}

def gerar(tipo, base_qr, chave="", logo_b64=""):
    """Devolve (nome do arquivo, conteudo .jrxml) ja com o endereco do gestor, a chave do QR e a logo embutidos."""
    global BASE_QR, CHAVE, LOGO
    with _trava:
        BASE_QR, CHAVE, LOGO = base_qr.rstrip("/"), chave, logo_b64
        nome, f = TIPOS[tipo]
        return nome, f()
