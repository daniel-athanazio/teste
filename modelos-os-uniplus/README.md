# Modelos de Ordem de Serviço (OS) para Uniplus em iReport

São 5 modelos `.jrxml` (JasperReports/iReport), todos com logotipo:

| Arquivo | Formato | Estilo |
|---|---|---|
| `OS_A4_Classico.jrxml` | A4 | Caixas com bordas, tons de cinza. Imprime bem em impressora P&B |
| `OS_A4_Moderno.jrxml` | A4 | Faixa colorida no cabeçalho, cartões, linhas zebradas e total em destaque |
| `OS_A4_Assistencia_Tecnica.jrxml` | A4 | Checklist de entrada, aprovação do orçamento, 3 assinaturas e canhoto destacável para o cliente |
| `OS_Cupom_80mm.jrxml` | Bobina de 80 mm | Cupom contínuo para impressora térmica |
| `OS_Cupom_58mm.jrxml` | Bobina de 58 mm | Cupom compacto para impressora térmica |

A pasta `exemplos/` tem o PDF e a imagem de cada modelo preenchido com dados de teste.

## 1. Logotipo

O logo vem do parâmetro `LOGO`, que recebe o **caminho do arquivo de imagem** (PNG ou JPG).
O valor padrão é `"C:/Uniplus/logo.png"`. Para trocar:

* salve o logo nesse caminho; **ou**
* no iReport, vá em *Parameters → LOGO → Default Value Expression* e informe o seu caminho, por exemplo
  `"C:/Uniplus/imagens/minha-logo.png"` (use `/` ou `\\`).

Se o arquivo não existir, o espaço do logo fica em branco e o restante da OS é impresso normalmente.
Para cupom térmico, prefira um logo em preto e branco com fundo branco.

## 2. Dados da empresa e texto de garantia

Os parâmetros `EMPRESA_NOME`, `EMPRESA_CNPJ`, `EMPRESA_IE`, `EMPRESA_ENDERECO`, `EMPRESA_CIDADE`,
`EMPRESA_TELEFONE`, `EMPRESA_EMAIL` e `TEXTO_GARANTIA` vêm com valores de exemplo. Edite o
*Default Value Expression* de cada um no iReport, mantendo o texto entre aspas.

## 3. Ligar os campos aos do Uniplus (importante)

Os modelos usam os nomes de campo abaixo. Cada linha do relatório é **um item da OS**, e os dados
do cabeçalho (cliente, equipamento etc.) se repetem em todas as linhas.

Se o seu Uniplus envia os dados com outros nomes, abra no iReport o layout de OS que o Uniplus já
usa, anote os nomes dos campos (painel *Report Inspector → Fields*) e troque os nomes nos modelos.
O jeito mais rápido é abrir o `.jrxml` num editor de texto (Bloco de Notas, Notepad++) e usar
*Substituir tudo*, por exemplo `$F{CLIENTE_NOME}` → `$F{nomeCliente}`, e renomear também a
declaração `<field name="CLIENTE_NOME" ...>`. Ajuste também o tipo (`class`) se for diferente.

| Campo | Tipo | Conteúdo |
|---|---|---|
| `NUMERO` | String | Número da OS |
| `DATA_ABERTURA` | Date | Data/hora de abertura |
| `DATA_PREVISAO` | Date | Previsão de entrega |
| `SITUACAO` | String | Situação/status |
| `TECNICO` | String | Técnico / vendedor |
| `CLIENTE_CODIGO`, `CLIENTE_NOME`, `CLIENTE_CPF_CNPJ`, `CLIENTE_TELEFONE`, `CLIENTE_EMAIL` | String | Dados do cliente |
| `CLIENTE_ENDERECO`, `CLIENTE_BAIRRO`, `CLIENTE_CIDADE`, `CLIENTE_UF`, `CLIENTE_CEP` | String | Endereço do cliente |
| `EQUIPAMENTO`, `MARCA`, `MODELO`, `NUMERO_SERIE`, `ACESSORIOS` | String | Equipamento / objeto |
| `DEFEITO_RELATADO`, `LAUDO_TECNICO`, `OBSERVACAO` | String | Textos livres (crescem conforme o tamanho) |
| `FORMA_PAGAMENTO` | String | Condição de pagamento |
| `ITEM_TIPO` | String | `S` = serviço; qualquer outro valor = peça/produto |
| `ITEM_CODIGO`, `ITEM_DESCRICAO`, `ITEM_UNIDADE` | String | Item |
| `ITEM_QUANTIDADE`, `ITEM_VALOR_UNITARIO`, `ITEM_VALOR_TOTAL` | BigDecimal | Valores do item |
| `VALOR_DESCONTO` | BigDecimal | Desconto total da OS |

Os totais (serviços, peças e total geral = soma dos itens − desconto) são calculados pelo próprio
relatório, nas variáveis `TOTAL_SERVICOS`, `TOTAL_PRODUTOS` e `TOTAL_ITENS`. Se o Uniplus já envia o
total pronto, basta trocar a expressão do campo "TOTAL".

Campos que o Uniplus não fornecer podem ser apagados do layout ou ficam em branco; nenhum campo
vazio gera erro.

## 4. Usar no Uniplus

1. Abra o `.jrxml` no iReport (ou Jaspersoft Studio), ajuste logo, empresa e campos.
2. Compile (botão *Compile Report*) para gerar o `.jasper`.
3. No Uniplus, cadastre o arquivo como layout de impressão da Ordem de Serviço, do mesmo jeito que
   você já faz com os outros layouts personalizados.
4. Para os cupons, selecione a impressora térmica. O cupom é contínuo (sem quebra de página) e
   cresce conforme a quantidade de itens.

## Notas técnicas

* Testados com JasperReports 6.20: todos compilam e geram PDF, inclusive OS longas com várias
  páginas (o cabeçalho da tabela e o rodapé "Página X de Y" se repetem).
* Expressões em Java (`language="java"`), sem Groovy, e atributos compatíveis com o iReport 5.x
  (por isso aparece o aviso de `isStretchWithOverflow` obsoleto nas versões novas, que é inofensivo).
* Margens A4: 28 pt (≈1 cm). Cupom 80 mm = 226 pt de largura; 58 mm = 164 pt.
* Valores no formato brasileiro (`1.234,56`).
