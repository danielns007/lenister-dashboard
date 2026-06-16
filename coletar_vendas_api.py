import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests
import json
import gspread
from datetime import datetime, timedelta
from collections import defaultdict
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

HOJE  = datetime.now()
ONTEM = HOJE - timedelta(days=1)

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
        print("❌ Token não encontrado. Execute o script de autenticação primeiro.")
        sys.exit(1)

    saved_at    = datetime.fromisoformat(token_data.get('saved_at', '2000-01-01'))
    idade_horas = (datetime.now() - saved_at).total_seconds() / 3600

    if idade_horas > 5:
        print("🔄 Token expirado, renovando...")
        novo_token = renovar_token(token_data.get('refresh_token'))
        if novo_token:
            print("✅ Token renovado.")
            return novo_token
        else:
            print("❌ Falha ao renovar token. Execute autenticar_ml.py novamente.")
            sys.exit(1)

    return token_data['access_token']

# ─── BUSCAR USER_ID DO VENDEDOR ──────────────────────────────────
def obter_user_id(access_token):
    resp = requests.get(
        "https://api.mercadolibre.com/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if resp.status_code == 200:
        return str(resp.json()['id'])
    print(f"❌ Erro ao obter user_id: {resp.status_code} — {resp.text}")
    sys.exit(1)

# ─── DETECTAR ÚLTIMO DIA DE VENDA NO SHEETS ──────────────────────
def ultimo_dia_vendas_no_sheets(aba):
    """
    Lê coluna B (Data Venda, DD/MM/YYYY) e retorna o dia mais recente.
    Retorna None se a aba estiver vazia (só cabeçalho).
    """
    valores = aba.col_values(2)  # coluna B
    datas = []
    for v in valores[1:]:
        v = v.strip()
        if not v:
            continue
        try:
            datas.append(datetime.strptime(v, "%d/%m/%Y"))
        except ValueError:
            pass
    return max(datas) if datas else None

# ─── BUSCAR PEDIDOS COM PAGINAÇÃO ────────────────────────────────
def buscar_pedidos(access_token, user_id, data_inicio_dt, data_fim_dt):
    """Busca todos os pedidos pagos no intervalo, com paginação."""
    pedidos = []
    offset  = 0
    limite  = 50

    data_inicio_str = data_inicio_dt.strftime("%Y-%m-%dT00:00:00.000-03:00")
    data_fim_str    = data_fim_dt.strftime("%Y-%m-%dT23:59:59.000-03:00")

    print(f"  Buscando pedidos de {data_inicio_dt.strftime('%d/%m/%Y')} até {data_fim_dt.strftime('%d/%m/%Y')}...")

    while True:
        url = (
            f"https://api.mercadolibre.com/orders/search"
            f"?seller={user_id}"
            f"&order.date_created.from={data_inicio_str}"
            f"&order.date_created.to={data_fim_str}"
            f"&order.status=paid"
            f"&sort=date_desc"
            f"&offset={offset}"
            f"&limit={limite}"
        )

        resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})

        if resp.status_code != 200:
            print(f"❌ Erro na API: {resp.status_code} — {resp.text}")
            break

        dados  = resp.json()
        lote   = dados.get('results', [])
        total  = dados.get('paging', {}).get('total', 0)

        pedidos.extend(lote)
        print(f"  📦 Carregados {len(pedidos)}/{total} pedidos...")

        if len(pedidos) >= total or len(lote) == 0:
            break

        offset += limite

    return pedidos

# ─── PROCESSAR PEDIDOS ───────────────────────────────────────────
def processar_pedidos(pedidos):
    agrupado = defaultdict(lambda: {'qtd': 0, 'receita': 0.0})

    for pedido in pedidos:
        date_created = pedido.get('date_created', '')
        data_venda   = date_created[:10] if date_created else 'N/A'
        try:
            data_venda_fmt = datetime.strptime(data_venda, "%Y-%m-%d").strftime("%d/%m/%Y")
        except:
            data_venda_fmt = data_venda

        for item in pedido.get('order_items', []):
            titulo     = item.get('item', {}).get('title', 'Produto desconhecido')
            quantidade = item.get('quantity', 0)
            preco_unit = item.get('unit_price', 0.0)
            receita    = quantidade * preco_unit

            chave = (titulo, data_venda_fmt)
            agrupado[chave]['qtd']     += quantidade
            agrupado[chave]['receita'] += receita

    return agrupado

# ─── MONTAR LINHAS PARA O SHEETS ────────────────────────────────
def montar_linhas(agrupado):
    data_coleta = HOJE.strftime("%d/%m/%Y")
    linhas = []

    chaves_ordenadas = sorted(
        agrupado.keys(),
        key=lambda x: datetime.strptime(x[1], "%d/%m/%Y"),
        reverse=True
    )

    for (titulo, data_venda) in chaves_ordenadas:
        dados      = agrupado[(titulo, data_venda)]
        qtd        = dados['qtd']
        receita    = round(dados['receita'], 2)
        preco_medio = round(receita / qtd, 2) if qtd > 0 else 0.0

        linha = [
            data_coleta,
            data_venda,
            titulo,
            qtd,
            receita,
            preco_medio,
        ]
        linhas.append(linha)
        print(f"  📅 {data_venda} | {titulo[:45]:<45} | Qtd: {qtd} | R${receita:.2f} | Ticket: R${preco_medio:.2f}")

    return linhas

# ─── MAIN ────────────────────────────────────────────────────────
print("=" * 60)
print("  COLETA VENDAS POR PRODUTO — Lenister Dashboard")
print("=" * 60)

print("\n🔗 Conectando ao Google Sheets...")
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(CREDENCIAIS_GOOGLE, scopes=scopes)
gc    = gspread.authorize(creds)
sh    = gc.open_by_key(ID_PLANILHA)

try:
    aba = sh.worksheet("Vendas_Produto")
    print("✅ Aba 'Vendas_Produto' encontrada.")
except gspread.exceptions.WorksheetNotFound:
    print("📋 Aba 'Vendas_Produto' não existe — criando...")
    aba = sh.add_worksheet(title="Vendas_Produto", rows=5000, cols=10)
    aba.append_row([
        "Data Coleta", "Data Venda", "Produto",
        "Qtd Vendida", "Receita Bruta (R$)", "Preço Médio (R$)"
    ])
    print("✅ Aba criada com cabeçalho.")

print("\n🔐 Obtendo token Mercado Livre...")
access_token = obter_token()
print("✅ Token válido.")

print("\n👤 Obtendo ID do vendedor...")
user_id = obter_user_id(access_token)
print(f"✅ User ID: {user_id}")

# ─── DETECTAR GAP ────────────────────────────────────────────────
print("\n📅 Detectando último dia de venda registrado no Sheets...")
ultimo_dia = ultimo_dia_vendas_no_sheets(aba)

if ultimo_dia is None:
    # Aba vazia — coleta últimos 90 dias como histórico inicial
    data_inicio = HOJE - timedelta(days=90)
    print(f"  ⚠️  Aba vazia. Coletando histórico dos últimos 90 dias.")
else:
    # Re-coleta a partir do último dia registrado (inclusive), pois
    # esse dia pode ter mais pedidos desde a última sincronização.
    data_inicio = ultimo_dia
    print(f"  Último dia de venda no Sheets: {ultimo_dia.strftime('%d/%m/%Y')}")

print(f"  Coletando de: {data_inicio.strftime('%d/%m/%Y')}")
print(f"  Coletando até: {ONTEM.strftime('%d/%m/%Y')} (ontem)")

if data_inicio > ONTEM:
    print(f"\n✅ Dados já estão atualizados até {ONTEM.strftime('%d/%m/%Y')}. Nada a coletar.")
    sys.exit(0)

# ─── BUSCAR PEDIDOS ──────────────────────────────────────────────
print("\n📦 Buscando pedidos...")
pedidos = buscar_pedidos(access_token, user_id, data_inicio, ONTEM)
print(f"✅ Total de pedidos encontrados: {len(pedidos)}")

if not pedidos:
    print("⚠️  Nenhum pedido pago encontrado no período.")
    sys.exit(0)

print("\n🔄 Agrupando por produto e dia...")
agrupado = processar_pedidos(pedidos)

print("\n📊 Resultado:")
linhas = montar_linhas(agrupado)

# ─── DEDUP E SALVAR ──────────────────────────────────────────────
# Datas presentes nas novas linhas (coluna B = Data Venda)
datas_novas = set(l[1] for l in linhas)
print(f"\n🔄 Substituindo {len(datas_novas)} datas no Sheets ({min(datas_novas)} a {max(datas_novas)})...")

todas = aba.get_all_values()
cabecalho = todas[0]
# Mantém linhas fora do período re-coletado
linhas_manter = [r for r in todas[1:] if r and r[1] not in datas_novas]
todos_dados = [cabecalho] + linhas_manter + linhas

print(f"  Linhas preservadas (fora do período): {len(linhas_manter)}")
print(f"  Linhas novas da API:                  {len(linhas)}")
print(f"  Total final:                          {len(todos_dados) - 1}")

print(f"\n💾 Salvando na aba 'Vendas_Produto'...")
try:
    aba.clear()
    aba.update('A1', todos_dados, value_input_option='USER_ENTERED')
    print("✅ Dados salvos com sucesso (sem duplicatas).")
except Exception as e:
    print(f"❌ Erro ao salvar: {e}")
    sys.exit(1)

total_qtd     = sum(d['qtd'] for d in agrupado.values())
total_receita = sum(d['receita'] for d in agrupado.values())

print(f"\n{'=' * 60}")
print(f"  ✅ Coleta concluída!")
print(f"  📅 Período: {data_inicio.strftime('%d/%m/%Y')} → {ONTEM.strftime('%d/%m/%Y')}")
print(f"  📦 Total vendido no período: {total_qtd} unidades")
print(f"  💰 Receita bruta no período: R${total_receita:.2f}")
print(f"  📎 Planilha: https://docs.google.com/spreadsheets/d/{ID_PLANILHA}")
print(f"{'=' * 60}")
