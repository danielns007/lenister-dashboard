# -*- coding: utf-8 -*-
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import json
import time
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ─── CONFIGURAÇÕES ───────────────────────────────────────────────
import os as _os
ML_CLIENT_ID       = _os.environ.get('ML_CLIENT_ID',     '739123530612333')
ML_CLIENT_SECRET   = _os.environ.get('ML_CLIENT_SECRET', 'tCZxVQNaeUZKMm8AxQFlsaGTgMYLj4U1')
ML_ADVERTISER_ID   = _os.environ.get('ML_ADVERTISER_ID', '111396')
ID_PLANILHA        = _os.environ.get('ML_SPREADSHEET_ID', '18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk')

_token_default = r"C:\Users\DanielNS\Lenister\ml_token.json"
TOKEN_FILE     = _os.environ.get('ML_TOKEN_FILE', _token_default)

_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS_GOOGLE = 'credenciais.json' if _os.path.exists('credenciais.json') else _creds_default

METRICAS = (
    "clicks,prints,cost,roas,acos,tacos,"
    "direct_amount,indirect_amount,total_amount,"
    "direct_items_quantity,indirect_items_quantity,"
    "direct_units_quantity,indirect_units_quantity"
)

HOJE  = datetime.now().date()
ONTEM = HOJE - timedelta(days=1)

# ─── AUTENTICAÇÃO ML ─────────────────────────────────────────────
def obter_token():
    try:
        with open(TOKEN_FILE) as f:
            td = json.load(f)
    except:
        print("❌ Token não encontrado. Execute autenticar_ml.py.")
        sys.exit(1)

    saved_at = datetime.fromisoformat(td.get('saved_at', '2000-01-01'))
    if (datetime.now() - saved_at).total_seconds() > 18000:
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

# ─── DETECTAR ÚLTIMO DIA NO SHEETS ───────────────────────────────
def ultimo_dia_no_sheets(aba):
    """
    Lê coluna A (Data DD/MM/YYYY) e retorna o dia mais recente.
    Retorna None se aba vazia (só cabeçalho).
    """
    valores = aba.col_values(1)
    datas = []
    for v in valores[1:]:
        v = v.strip()
        if not v:
            continue
        try:
            datas.append(datetime.strptime(v, "%d/%m/%Y").date())
        except ValueError:
            pass
    return max(datas) if datas else None

# ─── BUSCAR UM DIA NA API ────────────────────────────────────────
def buscar_dia(token, data_iso):
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
    print(f"    ⚠ API {data_iso}: {resp.status_code} — {resp.text[:80]}")
    return []

def montar_linha(data_br, c):
    m       = c.get('metrics', {})
    inv     = m.get('cost',                    0) or 0
    rec     = m.get('total_amount',            0) or 0
    rec_ind = m.get('indirect_amount',         0) or 0
    v_dir   = m.get('direct_items_quantity',   0) or 0  # pedidos diretos
    v_ind   = m.get('indirect_items_quantity', 0) or 0  # pedidos indiretos
    u_dir   = m.get('direct_units_quantity',   0) or 0  # unidades diretas
    u_ind   = m.get('indirect_units_quantity', 0) or 0  # unidades indiretas
    cliques = m.get('clicks',                 0) or 0
    impr    = m.get('prints',                 0) or 0
    roas_r  = m.get('roas',                  0) or 0
    acos_v  = m.get('acos',                 0) or 0
    tacos_v = m.get('tacos',               '') or ''
    roas_obj  = c.get('roas_target', 0) or 0
    status    = c.get('status',      '') or ''
    orcamento = c.get('budget',      0) or 0
    alerta    = 'SIM' if roas_obj > 0 and roas_r > 0 and roas_r < (roas_obj * 0.5) and inv > 0 else ''

    raw_dc = c.get('date_created', '')
    date_created = (
        datetime.fromisoformat(raw_dc.replace('Z', '+00:00')).strftime('%d/%m/%Y')
        if raw_dc else ''
    )

    return [
        data_br,
        c.get('name', ''),
        v_dir,
        v_ind,
        round(rec,    2),
        round(inv,    2),
        int(cliques),
        int(impr),
        '',           # Exibidos %
        '',           # Não Exibidos Orçamento %
        '',           # Não Exibidos Classificação %
        str(tacos_v),
        alerta,
        '',           # Vendas Perdidas/Mês
        round(roas_obj, 2),
        round(roas_r,   4),
        round(acos_v,   4),
        status,
        round(orcamento, 2),
        round(rec_ind,   2),
        date_created,    # [20] Data Criação
        u_dir,           # [21] Unidades Diretas ADS
        u_ind,           # [22] Unidades Indiretas ADS
    ]

# ─── MAIN ────────────────────────────────────────────────────────
print("=" * 60)
print("  COLETAR ADS API — Lenister Dashboard")
print("=" * 60)

print("\n🔗 Conectando ao Google Sheets...")
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = Credentials.from_service_account_file(CREDENCIAIS_GOOGLE, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(ID_PLANILHA)
aba = sh.worksheet("Dashboard_ADS")
print("✅ Conectado.")

print("\n🔐 Obtendo token Mercado Livre...")
token = obter_token()
print("✅ Token válido.")

# ─── DETECTAR GAP ────────────────────────────────────────────────
print("\n📅 Detectando último dia registrado no Sheets...")
ultimo_dia = ultimo_dia_no_sheets(aba)

if ultimo_dia is None:
    # Aba vazia — coleta os últimos 90 dias como histórico inicial
    data_inicio = HOJE - timedelta(days=90)
    print(f"  ⚠️  Aba vazia. Coletando histórico dos últimos 90 dias.")
else:
    # Re-coleta a partir do último dia registrado (inclusive), pois
    # esse dia pode ter mais dados desde a última sincronização.
    data_inicio = ultimo_dia
    print(f"  Último dia no Sheets: {ultimo_dia.strftime('%d/%m/%Y')}")

print(f"  Coletando de:         {data_inicio.strftime('%d/%m/%Y')}")
print(f"  Coletando até:        {ONTEM.strftime('%d/%m/%Y')} (ontem)")

if data_inicio > ONTEM:
    print(f"\n✅ Dados já estão atualizados até {ONTEM.strftime('%d/%m/%Y')}. Nada a coletar.")
    sys.exit(0)

# ─── BUSCAR DIAS NA API ──────────────────────────────────────────
dias = []
d = data_inicio
while d <= ONTEM:
    dias.append(d)
    d += timedelta(days=1)

print(f"\n📡 Buscando {len(dias)} dias na API ML...")
dias_dados = {}  # {date: [linhas]}

for dia in dias:
    dia_iso = dia.strftime('%Y-%m-%d')
    dia_br  = dia.strftime('%d/%m/%Y')
    resultados = buscar_dia(token, dia_iso)
    if resultados:
        dias_dados[dia_iso] = [montar_linha(dia_br, c) for c in resultados]
        print(f"  ✅ {dia_br}: {len(resultados)} campanhas")
    else:
        print(f"  ⚠  {dia_br}: sem dados")
    time.sleep(0.3)

novas_linhas = []
for dia in dias:
    iso = dia.strftime('%Y-%m-%d')
    if iso in dias_dados:
        novas_linhas.extend(dias_dados[iso])

if not novas_linhas:
    print("\n⚠️  Nenhuma linha nova para adicionar.")
    sys.exit(0)

# ─── DEDUP E SALVAR ──────────────────────────────────────────────
# Datas que serão reescritas (formato DD/MM/YYYY)
datas_atualizar = {dia.strftime('%d/%m/%Y') for dia in dias}

print(f"\n🔄 Substituindo {len(datas_atualizar)} datas no Sheets...")
todas = aba.get_all_values()
cabecalho = todas[0]
# Mantém linhas fora do intervalo re-coletado
linhas_manter = [r for r in todas[1:] if r and r[0] not in datas_atualizar]
todos_dados = [cabecalho] + linhas_manter + novas_linhas

print(f"  Linhas preservadas (fora do período): {len(linhas_manter)}")
print(f"  Linhas novas da API:                  {len(novas_linhas)}")
print(f"  Total final:                          {len(todos_dados) - 1}")

print(f"\n💾 Salvando no Sheets...")
try:
    aba.clear()
    aba.update('A1', todos_dados, value_input_option='USER_ENTERED')
    print("✅ Dados salvos na aba Dashboard_ADS (sem duplicatas).")
except Exception as e:
    print(f"❌ Erro ao salvar: {e}")
    sys.exit(1)

print(f"\n{'=' * 60}")
print(f"  ✅ Coleta ADS concluída!")
print(f"  📅 Período: {data_inicio.strftime('%d/%m/%Y')} → {ONTEM.strftime('%d/%m/%Y')}")
print(f"  📦 {len(novas_linhas)} linhas inseridas/atualizadas")
print(f"  📎 Planilha: https://docs.google.com/spreadsheets/d/{ID_PLANILHA}")
print(f"{'=' * 60}")
