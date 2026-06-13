# 📊 Sistema de Coleta de Custos — Lenister Dashboard

**Data:** 11/06/2026  
**Status:** ✅ Testado e funcionando  
**Próximo passo:** Publicar aba no Sheets e consumir no HTML

---

## 📁 Arquivos criados

### 1. **`criar_aba_pedidos_completos.py`**
Cria a estrutura da aba `Pedidos_Completos` no Google Sheets.

**Como usar:**
```bash
python criar_aba_pedidos_completos.py
```

**O que faz:**
- ✅ Cria aba `Pedidos_Completos` (ou limpa se já existir)
- ✅ Insere 16 cabeçalhos
- ✅ Estrutura pronta para dados

---

### 2. **`coletar_custos_api_v3_detalhado.py`** ⭐ USAR ESTE

Script principal de coleta de custos. Usa `/orders/{id}` para 100% de precisão.

**Como usar:**

**Primeira coleta (12 meses completos):**
```bash
python coletar_custos_api_v3_detalhado.py
```
- Busca 1.175 pedidos dos últimos 12 meses
- ⏱️ Tempo: 20-40 minutos (com throttling automático)
- Salva com fees reais do ML

**Próximas coletas (automática no Task Scheduler):**
- Mesma linha acima
- Script detecta automaticamente que foi primeira coleta e passa para modo semanal
- ⏱️ Tempo: ~2 minutos (para 39 pedidos)

---

## 📊 Dados coletados

### Colunas da aba `Pedidos_Completos`:

| # | Coluna | Fonte | Descrição |
|---|--------|-------|-----------|
| 1 | Data Coleta | Script | Quando o script rodou |
| 2 | Data Venda | API `/orders` | Quando o pedido foi feito |
| 3 | Nº Pedido | API `/orders/search` | ID do pedido no ML |
| 4 | Produto | API `/orders/{id}` | Título completo do item |
| 5 | MLB ID | API `/orders/{id}` | ID do anúncio |
| 6 | Qtd | API `/orders/{id}` | Quantidade vendida |
| 7 | Preço Unitário | API `/orders/{id}` | Preço por unidade |
| 8 | Receita Bruta | Calculado | `Qtd × Preço Unitário` |
| 9 | Fee ML (%) | API `/orders/{id}.sale_fee` | Percentual da taxa ML |
| 10 | Fee ML (R$) | API `/orders/{id}.sale_fee` | Valor da taxa ML |
| 11 | Fee MP (R$) | API `/orders/{id}` | Taxa Mercado Pago |
| 12 | Taxa Envio (R$) | API `/orders/{id}` | Custo do frete |
| 13 | Outras Taxas (R$) | Calculado | Outros descontos |
| 14 | Custo Total (R$) | Calculado | Soma de todas as taxas |
| 15 | Receita Líquida | Calculado | `Bruta - Custo Total` |
| 16 | Margem % | Calculado | `(Líquida / Bruta) × 100` |

---

## 🔄 Fluxo de automação

```
Task Scheduler (01:00 diário)
    ↓
coletar_desempenho.py        → Desempenho_Anuncios
coletar_ads_api.py           → Dashboard_ADS
coletar_vendas_api.py        → Vendas_Produto
coletar_custos_api_v3_detalhado.py  → Pedidos_Completos ⭐ NOVO
coletar_afiliados_metricas.py    → Afiliados_Metricas
coletar_afiliados_vendas.py      → Afiliados_Vendas
    ↓
Google Sheets [Lenister Dashboard]
    ↓
7 abas públicas em CSV
    ↓
HTML Dashboard (fetch nas URLs)
```

---

## 📡 URL pública da aba `Pedidos_Completos`

**Após publicar no Sheets (passo importante!):**

```
https://docs.google.com/spreadsheets/d/e/2PACX-1vQNZmiV55IHIreB_WQjOAbpsJa1iQ0rQ9u3JfMO7GjPUqdTg0LC4OAO26SKV9Un3qamNNtj_uT9gROd/pub?gid=<GID>&single=true&output=csv
```

**Onde encontrar o GID:**
1. Abrir planilha no Sheets
2. Clicar em `Pedidos_Completos` (aba)
3. URL muda para: `https://docs.google.com/spreadsheets/d/.../edit#gid=123456789`
4. Copiar o número após `#gid=`

---

## ✅ Teste realizado

**Data:** 11/06/2026 19:42  
**Período:** 7 dias (últimos pedidos)  
**Pedidos:** 39  
**Resultado:**
- Receita Bruta: R$ 10.466,11
- Custos: R$ 980,44 ✅
- Lucro Bruto: R$ 9.485,67
- Margem: 90,63%

---

## 🚀 Próximos passos

1. ✅ Scripts criados e testados
2. ⏳ **[TODO]** Publicar `Pedidos_Completos` como CSV público no Sheets
3. ⏳ **[TODO]** Atualizar `04_URLS_PUBLICAS_SHEETS.md` com a nova URL
4. ⏳ **[TODO]** Criar HTML do dashboard que consome todas as 7 abas
5. ⏳ **[TODO]** Testar no GitHub Pages

---

## 📝 Notas técnicas

- **Throttling:** 0.5s entre requisições para não sobrecarregar API
- **Rateio de fees:** Quando há múltiplos itens no pedido, fees são rateados pelo peso de cada item
- **Idempotência:** Script pode ser rodado várias vezes no mesmo dia (append sem duplicatas se fizer query bem)
- **Token:** Renovação automática a cada 5h
- **Config:** Rastreamento via aba `_Config` no Sheets

---

## ⚠️ Armadilhas conhecidas

- Não rodar `criar_aba_pedidos_completos.py` em produção com frequência (limpa dados)
- Primeira coleta de 12 meses é **lenta por design** (precisão 100%)
- Se token quebrar, rodar `autenticar_ml.py` manualmente

---

**Pronto para o Dashboard HTML! 🚀**
