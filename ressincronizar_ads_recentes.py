#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Re-sincroniza os últimos 14 dias do Dashboard_ADS via API do ML.

Por quê isso é necessário:
  O ML Product Ads usa attribution window de 7 dias: quando alguém clica
  num anúncio no dia X, qualquer compra nos próximos 7 dias ainda é
  atribuída ao dia X. Então os dados coletados no dia D são preliminares
  — os valores finais só ficam estáveis em D+7.

  Este script deleta as linhas dos últimos 14 dias e re-insere com
  dados atualizados (mais completos para dias >7 dias atrás).

Uso:
    python ressincronizar_ads_recentes.py [--dias N]
    python ressincronizar_ads_recentes.py --dias 30   # re-sync dos últimos 30 dias

Boas práticas:
  - Execute diariamente (ex: cron às 06h), junto com o coletar_ads_api.py
  - Os dias >7 dias atrás já terão dados finais; os últimos 7 dias
    ficam "em consolidação" no dashboard com o badge de aviso.
"""
import sys, re, json, time, argparse
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
import requests
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ─── Configuração ────────────────────────────────────────────────────
import os as _os
ML_CLIENT_ID     = _os.environ.get('ML_CLIENT_ID',     '739123530612333')
ML_CLIENT_SECRET = _os.environ.get('ML_CLIENT_SECRET', 'tCZxVQNaeUZKMm8AxQFlsaGTgMYLj4U1')
ML_ADVERTISER_ID = _os.environ.get('ML_ADVERTISER_ID', '111396')
ID_PLANILHA      = _os.environ.get('ML_SPREADSHEET_ID', '18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk')

_token_default = r"C:\Users\DanielNS\Lenister\ml_token.json"
TOKEN_FILE     = _os.environ.get('ML_TOKEN_FILE', _token_default)

_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS    = 'credenciais.json' if _os.path.exists('credenciais.json') else _creds_default

ABA_ADS = "Dashboard_ADS"

# Métricas solicitadas à API (acrescenta tacos e indirect_amount)
METRICAS = (
    "clicks,prints,cost,roas,acos,tacos,"
    "direct_amount,indirect_amount,total_amount,"
    "direct_items_quantity,indirect_items_quantity,"
    "direct_units_quantity,indirect_units_quantity"
)

# ─── Args ────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--dias', type=int, default=14,
                    help='Quantos dias passados re-sincronizar (padrão: 14)')
args = parser.parse_args()
DIAS = args.dias

# ─── Token ML ────────────────────────────────────────────────────────
def obter_token():
    try:
        with open(TOKEN_FILE) as f:
            td = json.load(f)
    except:
        print("❌ Token não encontrado. Execute autenticar_ml.py.")
        sys.exit(1)

    salvo_em = datetime.fromisoformat(td.get('saved_at', '2000-01-01'))
    if (datetime.now() - salvo_em).total_seconds() > 18000:  # 5h
        print("🔄 Token expirado, renovando...")
        resp = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": td.get('refresh_token'),
            }
        )
        if resp.status_code == 200:
            td = resp.json()
            td['saved_at'] = datetime.now().isoformat()
            with open(TOKEN_FILE, 'w') as f:
                json.dump(td, f, indent=2)
            print("✅ Token renovado.")
        else:
            print(f"❌ Falha ao renovar token: {resp.status_code}")
            sys.exit(1)

    return td['access_token']

# ─── API ML: buscar campanhas para um dia ────────────────────────────
def buscar_dia(token, data_iso):
    """Retorna lista de resultados de campanhas para um dia específico."""
    url = (
        f"https://api.mercadolibre.com/advertising/advertisers/{ML_ADVERTISER_ID}"
        f"/product_ads/campaigns"
        f"?date_from={data_iso}&date_to={data_iso}&metrics={METRICAS}"
    )
    resp = requests.get(url, headers={
        "Authorization": f"Bearer {token}",
        "api-version": "2",
    })
    if resp.status_code == 200:
        return resp.json().get('results', [])
    else:
        print(f"    ⚠ API {data_iso}: {resp.status_code} — {resp.text[:80]}")
        return []

def linha_para_sheet(data_br, c):
    """Monta linha para o sheet a partir de uma campanha da API."""
    m   = c.get('metrics', {})
    inv = m.get('cost',                   0) or 0
    rec = m.get('total_amount',           0) or 0  # Receita ADS (direct + indirect)
    rec_ind = m.get('indirect_amount',    0) or 0  # Receita Indireta ADS (NÃO é orgânica)
    v_dir   = m.get('direct_items_quantity',   0) or 0  # pedidos diretos
    v_ind   = m.get('indirect_items_quantity', 0) or 0  # pedidos indiretos
    u_dir   = m.get('direct_units_quantity',   0) or 0  # unidades diretas
    u_ind   = m.get('indirect_units_quantity', 0) or 0  # unidades indiretas
    cliques = m.get('clicks',             0) or 0
    impr    = m.get('prints',             0) or 0
    roas_r  = m.get('roas',              0) or 0
    acos_v  = m.get('acos',             0) or 0
    tacos_v = m.get('tacos',            '') or ''

    roas_obj  = c.get('roas_target',  0) or 0
    status    = c.get('status',       '') or ''
    orcamento = c.get('budget',       0) or 0
    alerta    = 'SIM' if roas_obj > 0 and roas_r > 0 and roas_r < (roas_obj * 0.5) and inv > 0 else ''

    # date_created: converter de ISO para DD/MM/YYYY
    raw_dc = c.get('date_created', '')
    if raw_dc:
        from datetime import datetime as _dt
        date_created = _dt.fromisoformat(raw_dc.replace('Z', '+00:00')).strftime('%d/%m/%Y')
    else:
        date_created = ''

    # Estrutura das 21 colunas do Dashboard_ADS:
    # [0]Data [1]Campanha [2]V.dir [3]V.ind [4]Receita [5]Invest
    # [6]Cliques [7]Impressoes [8]Exibidos% [9]NaoExib.Orc% [10]NaoExib.Class%
    # [11]%ADS/TACOS [12]AlertaOrc [13]VendasPerdidas [14]ROASObj [15]ROASReal
    # [16]ACOS% [17]Status [18]OrcDiario [19]ReceitaIndADS [20]DataCriacao
    return [
        data_br,
        c.get('name', ''),
        v_dir,
        v_ind,
        round(rec,    2),
        round(inv,    2),
        int(cliques),
        int(impr),
        '',           # [8]  Exibidos %
        '',           # [9]  Não Exibidos Orçamento %
        '',           # [10] Não Exibidos Classificação %
        str(tacos_v), # [11] TACOS da API
        alerta,       # [12] Alerta Orçamento
        '',           # [13] Vendas Perdidas/Mês
        round(roas_obj, 2),
        round(roas_r,   4),
        round(acos_v,   4),
        status,
        round(orcamento, 2),
        round(rec_ind,   2),  # [19] Receita Indireta ADS (indirect_amount)
        date_created,          # [20] Data Criação da campanha (date_created da API)
        u_dir,                 # [21] Unidades Diretas ADS (direct_units_quantity)
        u_ind,                 # [22] Unidades Indiretas ADS (indirect_units_quantity)
    ]

# ─── Google Sheets ────────────────────────────────────────────────────
print("🔗 Conectando ao Google Sheets...")
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_file(CREDENCIAIS, scopes=scopes)
gc = gspread.authorize(creds)
sh = gspread.authorize(creds).open_by_key(ID_PLANILHA)
aba = sh.worksheet(ABA_ADS)
print("✅ Conectado.")

# ─── Coletar dados da API ─────────────────────────────────────────────
hoje  = datetime.now().date()
token = obter_token()
print(f"✅ Token ML válido.")

print(f"\n📡 Buscando {DIAS} dias de dados da API ML...")
dias_dados = {}  # {data_iso: [linhas]}

for delta in range(1, DIAS + 1):
    dia = hoje - timedelta(days=delta)
    dia_iso = dia.strftime('%Y-%m-%d')
    dia_br  = dia.strftime('%d/%m/%Y')

    resultados = buscar_dia(token, dia_iso)
    if resultados:
        dias_dados[dia_iso] = [linha_para_sheet(dia_br, c) for c in resultados]
        status_attr = '(dados finais)' if delta > 7 else '(em consolidação)'
        print(f"  ✅ {dia_br}: {len(resultados)} campanhas {status_attr}")
    else:
        print(f"  ⚠  {dia_br}: sem dados")
    time.sleep(0.3)  # respeitar rate limit

# ─── Atualizar sheet ─────────────────────────────────────────────────
print(f"\n📊 Atualizando sheet Dashboard_ADS...")
todas = aba.get_all_values()
cabecalho = todas[0]
linhas    = todas[1:]  # sem cabeçalho

# Datas a re-sincronizar (formato dd/mm/yyyy)
datas_resync = set()
for delta in range(1, DIAS + 1):
    dia = hoje - timedelta(days=delta)
    datas_resync.add(dia.strftime('%d/%m/%Y'))

# Separar linhas que ficam (fora do período) e as que serão substituídas
linhas_manter = [r for r in linhas if r[0] not in datas_resync]
print(f"  Linhas mantidas (fora do período): {len(linhas_manter)}")
print(f"  Linhas a substituir (no período):  {len(linhas) - len(linhas_manter)}")

# Montar novas linhas (do mais antigo para o mais recente)
novas_linhas = []
for delta in range(DIAS, 0, -1):
    dia_iso = (hoje - timedelta(days=delta)).strftime('%Y-%m-%d')
    if dia_iso in dias_dados:
        novas_linhas.extend(dias_dados[dia_iso])

print(f"  Novas linhas da API: {len(novas_linhas)}")

# Escrever tudo de volta
todos_dados = [cabecalho] + linhas_manter + novas_linhas

print(f"\n  Reescrevendo {len(todos_dados)} linhas ({len(linhas_manter)} antigas + {len(novas_linhas)} novas)...")
aba.clear()
aba.update('A1', todos_dados, value_input_option='USER_ENTERED')

# Ordenar por Data (opcional, mas deixa mais limpo)
# Não vamos re-ordenar para não demorar demais

print(f"\n✅ Re-sincronização concluída!")
print(f"   {len(novas_linhas)} linhas atualizadas ({DIAS} dias)")
print(f"   Dados finais: dias > 7 dias atrás ({(hoje - timedelta(days=8)).strftime('%d/%m/%Y')} para trás)")
print(f"   Em consolidação: últimos 7 dias ({(hoje - timedelta(days=7)).strftime('%d/%m/%Y')} até {(hoje - timedelta(days=1)).strftime('%d/%m/%Y')})")

# ─── Verificar se a API retorna TACOS ────────────────────────────────
print("\n🔍 Verificando se API retorna TACOS...")
amostra_tacos = [l[11] for l in novas_linhas if l[11] and str(l[11]) not in ('', '0', '0.0')]
if amostra_tacos:
    print(f"   ✅ API retornou TACOS! Exemplos: {amostra_tacos[:3]}")
    print("      → coluna '% ADS sobre total de vendas' (col 11) preenchida com TACOS da API")
else:
    print("   ⚠  API não retornou TACOS (métrica 'tacos' não disponível neste endpoint)")
    print("      → TACOS no dashboard usa estimativa (V_sem/V_ads × Receita)")

rec_org_exemplo = [l[19] for l in novas_linhas if l[19] and str(l[19]) not in ('', '0', '0.0')]
if rec_org_exemplo:
    print(f"\n   ✅ Receita Orgânica (indirect_amount) preenchida! Exemplos: {rec_org_exemplo[:3]}")
    print("      → TACOS no dashboard agora usa indirect_amount real da API")
else:
    print(f"\n   ⚠  Receita Orgânica (indirect_amount) retornou 0 para todos os dias no período")
