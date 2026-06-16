"""
Script de coleta de custos via API Reporte do Mercado Livre.

Usa o endpoint /reports que retorna transações com breakdown detalhado de fees.
MAIS PRECISO que /orders/search, mas pode ser mais lento.

PRIMEIRA EXECUÇÃO (manual):
  python coletar_custos_api_v2_reports.py
  → Busca últimos 12 meses via reporte
  → Salva em Pedidos_Completos com fees reais

EXECUÇÕES SUBSEQUENTES (automática):
  python coletar_custos_api_v2_reports.py
  → Busca últimos 7 dias (atualização semanal)
"""
# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import json
import gspread
import csv
import io as string_io
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ─── CONFIGURAÇÕES ───────────────────────────────────────────────
import os as _os
ML_CLIENT_ID     = _os.environ.get('ML_CLIENT_ID',     '739123530612333')
ML_CLIENT_SECRET = _os.environ.get('ML_CLIENT_SECRET', 'tCZxVQNaeUZKMm8AxQFlsaGTgMYLj4U1')
ID_PLANILHA      = _os.environ.get('ML_SPREADSHEET_ID', '18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk')

_token_default = r"C:\Users\DanielNS\Lenister\ml_token.json"
TOKEN_FILE     = _os.environ.get('ML_TOKEN_FILE', _token_default)

_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS_GOOGLE = 'credenciais.json' if _os.path.exists('credenciais.json') else _creds_default

# ─── AUTENTICAÇÃO ML ─────────────────────────────────────────────
def carregar_token():
    try:
        with open(TOKEN_FILE, 'r') as f:
            return json.load(f)
    except:
        return None

def salvar_token(token_data):
    token_data['saved_at'] = datetime.now().isoformat()
    with open(TOKEN_FILE, 'w') as f:
        json.dump(token_data, f, indent=2)

def renovar_token(refresh_token):
    resp = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": refresh_token
        }
    )
    if resp.status_code == 200:
        dados = resp.json()
        salvar_token(dados)
        return dados['access_token']
    return None

def obter_token():
    token_data = carregar_token()
    if not token_data:
        print("ERRO: Token não encontrado. Execute autenticar_ml.py primeiro.")
        exit(1)

    saved_at = datetime.fromisoformat(token_data.get('saved_at', '2000-01-01'))
    idade_horas = (datetime.now() - saved_at).total_seconds() / 3600

    if idade_horas > 5:
        print("Renovando token ML...")
        novo_token = renovar_token(token_data.get('refresh_token'))
        if novo_token:
            print("Token renovado com sucesso.")
            return novo_token
        else:
            print("ERRO: Falha ao renovar token. Execute autenticar_ml.py.")
            exit(1)

    return token_data['access_token']

# ─── CONFIG NO SHEETS ────────────────────────────────────────────
def init_config_sheet(spreadsheet):
    try:
        config_ws = spreadsheet.worksheet("_Config")
    except gspread.exceptions.WorksheetNotFound:
        config_ws = spreadsheet.add_worksheet(title="_Config", rows=100, cols=10)
        config_ws.append_row(["Chave", "Valor", "Data Última Atualização"])
        print("Aba _Config criada.")
    return config_ws

def get_config(config_ws, chave):
    try:
        cell = config_ws.find(chave)
        if cell:
            return config_ws.cell(cell.row, 2).value
    except Exception:
        pass
    return None

def set_config(config_ws, chave, valor):
    try:
        cell = config_ws.find(chave)
        if cell:
            row = cell.row
            config_ws.update_cell(row, 2, valor)
            config_ws.update_cell(row, 3, datetime.now().strftime("%d/%m/%Y %H:%M"))
        else:
            config_ws.append_row([
                chave,
                valor,
                datetime.now().strftime("%d/%m/%Y %H:%M")
            ])
    except Exception:
        config_ws.append_row([
            chave,
            valor,
            datetime.now().strftime("%d/%m/%Y %H:%M")
        ])
    print(f"   Config '{chave}' = '{valor}'")

# ─── DETERMINAR PERÍODO ─────────────────────────────────────────
def determine_days(config_ws):
    primeira_coleta_done = get_config(config_ws, "coletar_custos_primeira_coleta")

    if primeira_coleta_done == "SIM":
        days = 7
        print("Atualizacao semanal (ultimos 7 dias)")
        return days, False
    else:
        days = 365
        print("PRIMEIRA COLETA (ultimos 12 meses)")
        return days, True

# ─── BUSCAR REPORTE DE VENDAS ────────────────────────────────────
def buscar_reporte_vendas(access_token, days=7):
    """
    Cria e busca um reporte de vendas via API do ML.
    Retorna CSV com dados completos incluindo fees.

    Endpoint: /reports/sales
    """
    hoje = datetime.now()
    data_fim = hoje.strftime("%Y-%m-%d")
    data_inicio = (hoje - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"\nCriando reporte de vendas ({days} dias)...")
    print(f"Periodo: {data_inicio} ate {data_fim}")

    # 1. Criar o reporte
    url_create = "https://api.mercadolibre.com/reports/sales"

    payload = {
        "begin_date": f"{data_inicio}T00:00:00.000-03:00",
        "end_date": f"{data_fim}T23:59:59.000-03:00",
        "columns": [
            "date",
            "order_id",
            "item_id",
            "item_title",
            "quantity",
            "unit_price",
            "sale_amount",
            "currency",
            "fee_amount",
            "mp_fee_amount",
            "shipping_amount",
            "taxes_amount"
        ]
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    print("Solicitando reporte...")
    resp = requests.post(url_create, json=payload, headers=headers)

    if resp.status_code != 200 and resp.status_code != 201:
        print(f"ERRO ao criar reporte: {resp.status_code}")
        print(f"Resposta: {resp.text}")
        return None

    report_data = resp.json()
    report_href = report_data.get('href')

    if not report_href:
        print("ERRO: Reporte sem href")
        return None

    print(f"Reporte criado. ID: {report_data.get('id')}")

    # 2. Aguardar disponibilidade
    print("Aguardando processamento do reporte... (pode levar 1-2 minutos)")

    import time
    max_tentativas = 60  # ~5 minutos
    tentativa = 0

    while tentativa < max_tentativas:
        tentativa += 1
        time.sleep(2)

        # Checar status
        status_resp = requests.get(report_href, headers=headers)
        if status_resp.status_code == 200:
            print(f"  [{tentativa}] Reporte disponível!")
            break
        else:
            if tentativa % 10 == 0:
                print(f"  [{tentativa}] Processando...")

    if tentativa >= max_tentativas:
        print("TIMEOUT aguardando reporte")
        return None

    # 3. Baixar reporte como CSV
    print("Baixando dados do reporte...")
    csv_resp = requests.get(f"{report_href}?format=csv", headers=headers)

    if csv_resp.status_code != 200:
        print(f"ERRO ao baixar CSV: {csv_resp.status_code}")
        return None

    return csv_resp.text

# ─── PROCESSAR CSV DO REPORTE ───────────────────────────────────
def processar_reporte_csv(csv_text):
    """
    Parse do CSV do reporte e conversão em linhas para o Sheets.

    Colunas do reporte:
    date, order_id, item_id, item_title, quantity, unit_price,
    sale_amount, currency, fee_amount, mp_fee_amount, shipping_amount, taxes_amount
    """
    linhas = []
    data_coleta = datetime.now().strftime("%d/%m/%Y")

    try:
        csv_reader = csv.DictReader(string_io.StringIO(csv_text))

        for row in csv_reader:
            # Parse data
            data_venda_str = row.get('date', '')
            try:
                data_venda = datetime.strptime(data_venda_str, "%Y-%m-%d").strftime("%d/%m/%Y")
            except:
                data_venda = data_venda_str

            # Extrair valores
            nº_pedido = row.get('order_id', '')
            mlb_id = row.get('item_id', '')
            produto = row.get('item_title', '')
            qtd = int(row.get('quantity', 0))
            preco_unitario = float(row.get('unit_price', 0))
            receita_bruta = float(row.get('sale_amount', 0))

            # Fees (em valores monetários)
            fee_ml = float(row.get('fee_amount', 0))
            fee_mp = float(row.get('mp_fee_amount', 0))
            taxa_envio = float(row.get('shipping_amount', 0))
            outras_taxas = float(row.get('taxes_amount', 0))

            # Cálculos
            fee_ml_pct = (fee_ml / receita_bruta * 100) if receita_bruta > 0 else 0
            custo_total = fee_ml + fee_mp + taxa_envio + outras_taxas
            receita_liquida = receita_bruta - custo_total
            margem_pct = (receita_liquida / receita_bruta * 100) if receita_bruta > 0 else 0

            linha = [
                data_coleta,
                data_venda,
                nº_pedido,
                produto,
                mlb_id,
                qtd,
                round(preco_unitario, 2),
                round(receita_bruta, 2),
                round(fee_ml_pct, 2),
                round(fee_ml, 2),
                round(fee_mp, 2),
                round(taxa_envio, 2),
                round(outras_taxas, 2),
                round(custo_total, 2),
                round(receita_liquida, 2),
                round(margem_pct, 2)
            ]

            linhas.append(linha)

    except Exception as e:
        print(f"ERRO ao processar CSV: {e}")
        return []

    return linhas

# ─── MAIN ────────────────────────────────────────────────────────
print("=" * 70)
print("  COLETA DE CUSTOS VIA REPORTE — Lenister Dashboard")
print("=" * 70)

# 1. Google Sheets
print("\nConectando ao Google Sheets...")
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(CREDENCIAIS_GOOGLE, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(ID_PLANILHA)
print("Conectado a planilha: Lenister Dashboard")

# 2. Config
config_ws = init_config_sheet(sh)

# 3. Determinar período
days, is_primeira = determine_days(config_ws)

# 4. Aba de dados
try:
    aba = sh.worksheet("Pedidos_Completos")
    print("Aba 'Pedidos_Completos' encontrada.")
except gspread.exceptions.WorksheetNotFound:
    print("ERRO: Aba 'Pedidos_Completos' nao existe. Execute 'criar_aba_pedidos_completos.py'")
    exit(1)

# 5. Token ML
print("\nObtendo token Mercado Livre...")
access_token = obter_token()
print("Token valido.")

# 6. Buscar reporte
csv_reporte = buscar_reporte_vendas(access_token, days=days)

if not csv_reporte:
    print("Nenhum dado no reporte.")
    set_config(config_ws, "coletar_custos_ultima_execucao_status", "Sem dados no reporte")
    exit(0)

# 7. Processar CSV
print("\nProcessando dados do reporte...")
linhas = processar_reporte_csv(csv_reporte)
print(f"Total de linhas processadas: {len(linhas)}")

if not linhas:
    print("Nenhuma linha gerada.")
    set_config(config_ws, "coletar_custos_ultima_execucao_status", "Reporte vazio")
    exit(0)

# 8. Salvar em lotes
print(f"\nSalvando {len(linhas)} linhas na aba 'Pedidos_Completos'...")
LOTE = 500
total_salvo = 0

for i in range(0, len(linhas), LOTE):
    lote = linhas[i:i + LOTE]
    try:
        aba.append_rows(lote)
        total_salvo += len(lote)
        lote_num = i // LOTE + 1
        print(f"  Lote {lote_num}: {total_salvo}/{len(linhas)} linhas salvas...")
    except Exception as e:
        print(f"  ERRO no lote {i // LOTE + 1}: {e}")
        print("  Aguardando 5 segundos...")
        import time
        time.sleep(5)
        try:
            aba.append_rows(lote)
            total_salvo += len(lote)
            print(f"  Reenvio ok: {total_salvo}/{len(linhas)} linhas")
        except Exception as e2:
            print(f"  ERRO definitivo: {e2}")

# 9. Atualizar config
set_config(config_ws, "coletar_custos_ultima_execucao_status", "SUCESSO")
set_config(config_ws, "coletar_custos_ultima_execucao_linhas", str(total_salvo))
set_config(config_ws, "coletar_custos_ultima_coleta", datetime.now().strftime("%d/%m/%Y %H:%M"))

if is_primeira:
    set_config(config_ws, "coletar_custos_primeira_coleta", "SIM")
    set_config(config_ws, "coletar_custos_tipo_coleta", "HISTORICO (12 meses)")
else:
    set_config(config_ws, "coletar_custos_tipo_coleta", "ATUALIZACAO (7 dias)")

# 10. Resumo
total_receita = sum(float(linha[7]) for linha in linhas if linha[7])
total_custo = sum(float(linha[13]) for linha in linhas if linha[13])
total_lucro = total_receita - total_custo

print("\n" + "=" * 70)
print("  COLETA CONCLUIDA!")
print("=" * 70)
print(f"  Periodo: {days} dias")
print(f"  Linhas salvas: {total_salvo}")
print(f"  Total receita bruta: R${total_receita:,.2f}")
print(f"  Total custos: R${total_custo:,.2f}")
print(f"  Total lucro bruto: R${total_lucro:,.2f}")
if total_receita > 0:
    margem = (total_lucro / total_receita * 100)
else:
    margem = 0
print(f"  Margem media: {margem:.2f}%")
print(f"\n  Planilha: https://docs.google.com/spreadsheets/d/{ID_PLANILHA}")
print("=" * 70 + "\n")
