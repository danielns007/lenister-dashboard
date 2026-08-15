import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import time
import re
import requests
import gspread
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from google.oauth2.service_account import Credentials

# ─── CONFIGURAÇÕES ───────────────────────────────────────────────
ID_PLANILHA = os.environ.get('ML_SPREADSHEET_ID', '18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk')

_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS = 'credenciais.json' if os.path.exists('credenciais.json') else _creds_default

_token_default = r"C:\Users\DanielNS\Lenister\ml_token.json"
TOKEN_FILE = os.environ.get('ML_TOKEN_FILE', 'ml_token.json' if os.path.exists('ml_token.json') else _token_default)

# Modo headless: ativado por env var (GitHub Actions) ou ausência de display
HEADLESS = os.environ.get('CHROME_HEADLESS', '').lower() in ('1', 'true', 'yes')
# Cookies de sessão ML (base64 ou JSON) armazenados como Secret no GitHub
ML_COOKIES_JSON = os.environ.get('ML_COOKIES_JSON', '')

# "id" pode ser um MLB único ou uma lista — produtos com mais de um anúncio ativo
# simultâneo (ex: duas variações de cadastro do mesmo item) têm seus KPIs somados.
PRODUTOS = [
    {"nome": "Sirene Estroboscópica",     "id": "MLB6168880144"},
    {"nome": "Fonte 12V",                 "id": "MLB6128512354"},
    {"nome": "Fonte 24V",                 "id": "MLB6128447010"},
    {"nome": "Sonda 0-1mca",              "id": "MLB4960886183"},
    {"nome": "Sonda 0-10mca",             "id": "MLB4470736687"},
    {"nome": "Sonda 0-2mca",              "id": "MLB4811412531"},
    {"nome": "Sonda 0-5mca",              "id": "MLB7277760266"},
    {"nome": "Central Laço 12V Manual",   "id": "MLB4559395191"},
    {"nome": "Fechadura Vidro",           "id": "MLB6718341398"},
    {"nome": "Extensor PoE Giga",         "id": "MLB6508001372"},
    {"nome": "Protetor Cabo",             "id": "MLB5482550358"},
    {"nome": "Fechadura Sobrepor",        "id": "MLB4205584415"},
    {"nome": "Sonda 0-4mca",              "id": "MLB3904989803"},
    {"nome": "Central Laço 12V Preto (Premium)", "id": "MLB5697266066"},
    {"nome": "Central Laço 12V Preto (Clássico)", "id": "MLB4250306527"},
    {"nome": "Extensor PoE Hi-AT13FL",    "id": "MLB4273561454"},
    {"nome": "Central Laço 220V",         "id": "MLB5694900528"},
    {"nome": "Sensor Pressão 10 Bar",     "id": "MLB6294668236"},
]

hoje = datetime.now()
ontem = hoje - timedelta(days=1)
data_inicio = ontem.strftime("%Y-%m-%d")   # coleta apenas ontem (1 dia)
data_fim    = ontem.strftime("%Y-%m-%d")
data_referencia = ontem.strftime("%d/%m/%Y")  # data do dado (ontem), usada na planilha
data_coleta     = hoje.strftime("%d/%m/%Y")   # data em que o script rodou

# ─── STATUS DO ANÚNCIO (API ML) ──────────────────────────────────
STATUS_LEGIVEL = {"active": "Ativo", "paused": "Pausado", "closed": "Fechado"}
MOTIVO_LEGIVEL = {"out_of_stock": "Sem estoque", "paused_by_seller": "Pausado pelo vendedor"}

def obter_token_ml():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f).get('access_token', '')
    except Exception as e:
        print(f"⚠️  Não foi possível ler token ML ({TOKEN_FILE}): {e}")
        return ''

def consultar_status(mlb_id, token):
    """Retorna (status, [sub_status]). Em caso de falha na API, assume 'active'
    para não bloquear a coleta por um erro transitório."""
    if not token:
        return ("active", [])
    try:
        r = requests.get(
            f"https://api.mercadolibre.com/items/{mlb_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"attributes": "id,status,sub_status"},
            timeout=15,
        )
        if r.status_code != 200:
            return ("active", [])
        d = r.json()
        return (d.get("status", "active"), d.get("sub_status") or [])
    except Exception as e:
        print(f"    ⚠️ Falha ao consultar status de {mlb_id}: {e}")
        return ("active", [])

def motivo_texto(sub_status_list):
    if not sub_status_list:
        return ""
    return "; ".join(MOTIVO_LEGIVEL.get(s, s) for s in sub_status_list)

# ─── DESCOBERTA DINÂMICA DE ANÚNCIOS (2026-07-21) ────────────────
# PRODUTOS acima é curado à mão (nome curto + agrupamento de anúncios-irmãos
# do mesmo produto) — não dá pra substituir por API sem perder essas duas
# coisas. Em vez disso, checa contra a API a cada execução e avisa/inclui
# qualquer anúncio ativo que não esteja na lista curada, para não ficar
# invisível até alguém lembrar de editar o código manualmente.

def buscar_seller_id(token):
    if not token:
        return None
    try:
        r = requests.get(
            "https://api.mercadolibre.com/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("id")
    except Exception as e:
        print(f"⚠️  Falha ao obter seller_id: {e}")
    return None

def buscar_todos_ids_api(token, seller_id):
    """Retorna TODOS os IDs de anúncio da conta (ativos E pausados), via API
    (sem Selenium) — /items/search não filtra por status por padrão. O status
    real de cada ID precisa ser checado separadamente via consultar_status()."""
    if not token or not seller_id:
        return []
    ids = []
    offset = 0
    limite = 50
    try:
        while True:
            r = requests.get(
                f"https://api.mercadolibre.com/users/{seller_id}/items/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"offset": offset, "limit": limite},
                timeout=15,
            )
            if r.status_code != 200:
                break
            dados = r.json()
            lote = dados.get("results", [])
            ids.extend(lote)
            total = dados.get("paging", {}).get("total", 0)
            if not lote or len(ids) >= total:
                break
            offset += limite
    except Exception as e:
        print(f"⚠️  Falha ao buscar anúncios ativos via API: {e}")
        return []
    return ids

def buscar_titulo_item(mlb_id, token):
    try:
        r = requests.get(
            f"https://api.mercadolibre.com/items/{mlb_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"attributes": "id,title,status"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("title", mlb_id)
    except Exception:
        pass
    return mlb_id

# ─── GOOGLE SHEETS ───────────────────────────────────────────────
print("🔗 Conectando ao Google Sheets...")
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(CREDENCIAIS, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(ID_PLANILHA)
aba = sh.worksheet("Desempenho_Anuncios")
print("✅ Conectado à planilha")

# ─── VERIFICAR STATUS DE TODOS OS ANÚNCIOS ANTES DE ABRIR O CHROME ──
print("\n🔎 Verificando status dos anúncios via API...")
token_ml = obter_token_ml()
status_por_id = {}
for produto in PRODUTOS:
    ids = produto["id"] if isinstance(produto["id"], list) else [produto["id"]]
    for mlb_id in ids:
        status, sub_status = consultar_status(mlb_id, token_ml)
        status_por_id[mlb_id] = (status, sub_status)
        marca = "✅" if status == "active" else "⏸️"
        print(f"  {marca} {mlb_id}: {STATUS_LEGIVEL.get(status, status)}"
              + (f" ({motivo_texto(sub_status)})" if status != "active" else ""))

# ─── Checar anúncios que não estão na lista curada (PRODUTOS) ───────────
print("\n🔎 Checando anúncios novos via API (comparando com a lista monitorada)...")
ids_conhecidos = set(status_por_id.keys())
produtos_novos_descobertos = []
seller_id = buscar_seller_id(token_ml)
ids_todos_api = buscar_todos_ids_api(token_ml, seller_id)
ids_novos = [i for i in ids_todos_api if i not in ids_conhecidos]

if ids_novos:
    print(f"  ⚠️  {len(ids_novos)} anúncio(s) NÃO estão na lista monitorada (PRODUTOS):")
    novos_ativos = 0
    for mlb_id in ids_novos:
        status, sub_status = consultar_status(mlb_id, token_ml)
        status_por_id[mlb_id] = (status, sub_status)
        titulo = buscar_titulo_item(mlb_id, token_ml)
        marca = "🆕✅" if status == "active" else "🆕⏸️"
        print(f"    {marca} {mlb_id} — {titulo} ({STATUS_LEGIVEL.get(status, status)})")
        produto_auto = {"nome": titulo, "id": mlb_id, "auto_descoberto": True}
        PRODUTOS.append(produto_auto)
        produtos_novos_descobertos.append({**produto_auto, "status": status})
        if status == "active":
            novos_ativos += 1
    print(f"  → {novos_ativos} novo(s) ativo(s) incluído(s) nesta coleta com o título bruto do ML.")
    print(f"    Considere adicionar um nome curto e checar se algum é anúncio-irmão de um produto")
    print(f"    já monitorado (editar PRODUTOS manualmente).")
elif ids_todos_api:
    print(f"  ✅ Nenhum anúncio novo — todos os {len(ids_todos_api)} da conta já estão monitorados.")
else:
    print(f"  ⚠️  Não foi possível confirmar via API (token/seller_id indisponível) — seguindo só com a lista curada.")

produtos_com_ativo = []
for produto in PRODUTOS:
    ids = produto["id"] if isinstance(produto["id"], list) else [produto["id"]]
    ids_ativos = [i for i in ids if status_por_id.get(i, ("active", []))[0] == "active"]
    if ids_ativos:
        produtos_com_ativo.append(produto)

precisa_selenium = len(produtos_com_ativo) > 0

# ─── SELENIUM ────────────────────────────────────────────────────
driver = None
if precisa_selenium:
    print("\n🌐 Iniciando Chrome...")
    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    if HEADLESS:
        print("  Modo headless ativado (GitHub Actions / CI)")
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--remote-debugging-port=9222")
    else:
        options.add_argument("--start-maximized")
        chrome_profile = r"C:\Users\DanielNS\Lenister\chrome_profile"
        if os.path.exists(chrome_profile):
            options.add_argument(f"--user-data-dir={chrome_profile}")

    # Mascarar detecção de automação (contorna bloqueio do ML)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    # Remover navigator.webdriver via CDP
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    wait = WebDriverWait(driver, 20)

    print("\n🔐 Abrindo Mercado Livre...")
    driver.get("https://www.mercadolivre.com.br/")
    time.sleep(4)

    # Injetar cookies de sessão (GitHub Actions)
    if HEADLESS and ML_COOKIES_JSON:
        print("  Injetando cookies de sessão ML...")
        try:
            cookies = json.loads(ML_COOKIES_JSON)
            for cookie in cookies:
                # Selenium só aceita cookies do domínio atual
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    pass
            driver.refresh()
            time.sleep(3)
            print("  Cookies injetados.")
        except Exception as e:
            print(f"  ⚠️ Falha ao injetar cookies: {e}")

    # Verifica se está logado
    if "login" in driver.current_url.lower() or "identificacao" in driver.current_url.lower():
        if HEADLESS:
            print("❌ Não está logado em modo headless — ML_COOKIES_JSON não definido ou cookies inválidos.")
            print("   Para obter cookies: abra o ML no Chrome, exporte os cookies como JSON e salve como Secret ML_COOKIES_JSON.")
            driver.quit()
            sys.exit(1)
        else:
            print("⚠️  Não está logado. Faça o login manualmente no Chrome.")
            print("   Após logar, volte aqui e pressione ENTER para continuar...")
            input()
    else:
        print("✅ Logado. Iniciando coleta...\n")
else:
    print("\n⏭️  Nenhum anúncio ativo entre os produtos monitorados — Chrome não será iniciado.")

# ─── FUNÇÕES AUXILIARES ──────────────────────────────────────────
def limpar_numero(texto):
    if not texto:
        return ""
    # Remove R$, %, espaços e pontos de milhar. Mantém vírgula decimal (parseLocalNumber no JS espera formato BR)
    texto = re.sub(r'[R$%\s]', '', texto)
    texto = texto.replace('.', '')
    return texto.strip()

def para_float(texto_limpo):
    """Converte texto já processado por limpar_numero (vírgula decimal) em float."""
    if not texto_limpo:
        return 0.0
    try:
        return float(texto_limpo.replace(',', '.'))
    except ValueError:
        return 0.0

def formatar_money(valor):
    return f"{valor:.2f}".replace('.', ',')

def formatar_pct(valor):
    return f"{valor:.1f}".replace('.', ',')

def formatar_int(valor):
    return str(int(round(valor)))

def aguardar_pagina_carregada(driver, timeout=20):
    """Aguarda até que a página tenha pelo menos um label de KPI visível."""
    labels_kpi = [
        "Vendas brutas", "Vendas concluídas", "Visitas únicas", "Total de visitas",
        "Conversão", "Unidades vendidas",
    ]
    fim = time.time() + timeout
    while time.time() < fim:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            if any(label in body for label in labels_kpi):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def extrair_kpis(driver, mlb_id=""):
    """
    Extrai os KPIs da página de desempenho do ML.
    Aguarda ativamente o carregamento antes de parsear.
    """
    resultado = {
        "vendas_brutas": "",
        "vendas_concluidas": "",
        "qtd_vendas_brutas": "",
        "unidades": "",
        "preco_medio": "",
        "compradores_unicos": "",
        "visitas_unicas": "",
        "total_visitas": "",
        "conversao": "",
        "funil_visitas": "",
        "funil_intencao": "",
        "funil_vendas": "",
    }

    try:
        carregou = aguardar_pagina_carregada(driver, timeout=25)
        if not carregou:
            print(f"    ⚠️ Página não carregou KPIs esperados — aguardando mais 10s...")
            time.sleep(10)

        body = driver.find_element(By.TAG_NAME, "body").text
        linhas = [l.strip() for l in body.split("\n") if l.strip()]

        mapa = {
            "Vendas brutas": "vendas_brutas",
            "Vendas concluídas": "vendas_concluidas",
            "Quantidade de vendas brutas": "qtd_vendas_brutas",
            "Unidades vendidas": "unidades",
            "Preço médio por unidade": "preco_medio",
            "Compradores únicos": "compradores_unicos",
            "Visitas únicas": "visitas_unicas",
            "Total de visitas": "total_visitas",
            "Conversão": "conversao",
            "Intenção de compra": "funil_intencao",
        }

        for i, linha in enumerate(linhas):
            for label, chave in mapa.items():
                if linha.strip() == label:
                    for j in range(i+1, min(i+8, len(linhas))):
                        candidato = linhas[j].strip()
                        # Aceita: R$ 1.234,56 | 10 | 2,5% | 0
                        if re.match(r'^(R\$\s*)?\d[\d\.,]*(%)?$', candidato):
                            if resultado[chave] == "":
                                resultado[chave] = candidato
                            break

        # Se visitas_unicas vazia mas total_visitas preenchida, usa total como fallback
        if resultado["visitas_unicas"] == "" and resultado["total_visitas"] != "":
            resultado["visitas_unicas"] = resultado["total_visitas"]

        # Funil visitas = visitas_unicas
        resultado["funil_visitas"] = resultado["visitas_unicas"]

        # Funil intenção — busca número após "Intenção de compra" se não encontrado no mapa
        if resultado["funil_intencao"] == "":
            for i, linha in enumerate(linhas):
                if "Inten" in linha and "compra" in linha:
                    for j in range(i+1, min(i+8, len(linhas))):
                        c = linhas[j].strip()
                        if re.match(r'^\d[\d\.]*$', c):
                            resultado["funil_intencao"] = c
                            break
                    break

        resultado["funil_vendas"] = resultado["vendas_brutas"]

    except Exception as e:
        print(f"    ⚠️ Erro na extração: {e}")

    return resultado


def combinar_kpis(lista_kpis):
    """Soma os KPIs de múltiplos anúncios do mesmo produto e recalcula as métricas derivadas
    (preço médio e conversão não são somáveis diretamente)."""
    vendas_brutas = sum(para_float(limpar_numero(k["vendas_brutas"])) for k in lista_kpis)
    vendas_concluidas = sum(para_float(limpar_numero(k["vendas_concluidas"])) for k in lista_kpis)
    unidades = sum(para_float(limpar_numero(k["unidades"])) for k in lista_kpis)
    compradores = sum(para_float(limpar_numero(k["compradores_unicos"])) for k in lista_kpis)
    visitas_unicas = sum(para_float(limpar_numero(k["visitas_unicas"])) for k in lista_kpis)
    total_visitas = sum(para_float(limpar_numero(k["total_visitas"])) for k in lista_kpis)
    qtd_vendas_brutas = sum(para_float(limpar_numero(k["qtd_vendas_brutas"])) for k in lista_kpis)
    funil_intencao = sum(para_float(limpar_numero(k["funil_intencao"])) for k in lista_kpis)

    preco_medio = (vendas_concluidas / unidades) if unidades > 0 else 0.0

    # "Compradores únicos" raramente vem preenchido pelo ML (falha pré-existente da extração),
    # então a conversão combinada é a média das conversões de cada anúncio ponderada pelas
    # respectivas visitas, e não compradores/visitas.
    visitas_por_id = [para_float(limpar_numero(k["visitas_unicas"])) for k in lista_kpis]
    conversoes_por_id = [para_float(limpar_numero(k["conversao"])) for k in lista_kpis]
    soma_ponderada = sum(v * c for v, c in zip(visitas_por_id, conversoes_por_id))
    conversao = (soma_ponderada / visitas_unicas) if visitas_unicas > 0 else 0.0

    return {
        "vendas_brutas": formatar_money(vendas_brutas),
        "vendas_concluidas": formatar_money(vendas_concluidas),
        "unidades": formatar_int(unidades),
        "preco_medio": formatar_money(preco_medio),
        "visitas_unicas": formatar_int(visitas_unicas),
        "total_visitas": formatar_int(total_visitas),
        "compradores_unicos": formatar_int(compradores),
        "conversao": formatar_pct(conversao),
        "funil_visitas": formatar_int(visitas_unicas),
        "funil_intencao": formatar_int(funil_intencao),
        "funil_vendas": formatar_money(vendas_brutas),
        "qtd_vendas_brutas": formatar_int(qtd_vendas_brutas),
        "_ja_limpo": True,  # marcador: valores acima já estão prontos, não passar por limpar_numero de novo
    }

# ─── COLETA POR PRODUTO ──────────────────────────────────────────
resultados = []

for produto in PRODUTOS:
    nome = produto["nome"]
    ids = produto["id"] if isinstance(produto["id"], list) else [produto["id"]]
    mlb_id_coluna = ", ".join(ids)

    ids_ativos = [i for i in ids if status_por_id.get(i, ("active", []))[0] == "active"]
    ids_pausados = [i for i in ids if i not in ids_ativos]

    if not ids_ativos:
        # Todos os anúncios do produto estão pausados/fechados — não abrir o Selenium
        status, sub_status = status_por_id.get(ids[0], ("paused", []))
        status_txt = STATUS_LEGIVEL.get(status, status)
        motivo = motivo_texto(sub_status)
        print(f"⏸️  {nome} ({mlb_id_coluna}) — {status_txt}"
              + (f" ({motivo})" if motivo else ""))

        linha = [
            data_referencia, nome, mlb_id_coluna,
            "", "", "", "", "", "", "", "", "", "", "", "",
            data_coleta, status_txt, motivo,
        ]
        resultados.append(linha)
        continue

    print(f"📦 Coletando: {nome} ({', '.join(ids_ativos)})"
          + (f" — {len(ids_pausados)} anúncio(s) irmão(s) pausado(s)" if ids_pausados else ""))

    try:
        kpis_por_id = []
        for mlb_id in ids_ativos:
            url = (
                f"https://www.mercadolivre.com.br/metricas/{mlb_id}/performance-item"
                f"?start_period_evolutionary=custom|{data_inicio}T03:00:00.000Zto{data_fim}T03:00:00.000Z"
            )
            driver.get(url)
            time.sleep(5 if not HEADLESS else 8)
            kpis_por_id.append(extrair_kpis(driver, mlb_id))
            time.sleep(1)

        if len(kpis_por_id) == 1:
            kpis = kpis_por_id[0]
            ja_limpo = False
        else:
            kpis = combinar_kpis(kpis_por_id)
            ja_limpo = True

        # Debug: se todos KPIs vazios, imprimir trecho da página
        if all(v == "" for k, v in kpis.items() if k != "_ja_limpo"):
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text[:300]
                print(f"    [debug] página vazia — body[:300]: {repr(body_text)}")
            except Exception:
                pass

        def val(chave):
            return kpis[chave] if ja_limpo else limpar_numero(kpis[chave])

        status_txt = "Ativo" if not ids_pausados else "Ativo (parcial)"
        motivo = ("" if not ids_pausados else
                  "; ".join(f"{i}: {motivo_texto(status_por_id[i][1]) or STATUS_LEGIVEL.get(status_por_id[i][0])}"
                            for i in ids_pausados))

        linha = [
            data_referencia,                # Data (ontem — data do dado)
            nome,                            # Produto
            mlb_id_coluna,                   # MLB ID(s)
            val("vendas_brutas"),            # Vendas Brutas (R$)
            val("vendas_concluidas"),        # Vendas Concluídas (R$)
            val("unidades"),                 # Unidades Vendidas
            val("preco_medio"),              # Preço Médio (R$)
            val("visitas_unicas"),           # Visitas Únicas
            val("total_visitas"),            # Total de Visitas
            val("compradores_unicos"),       # Compradores Únicos
            val("conversao"),                # Conversão (%)
            val("funil_visitas"),            # Funil: Visitas Únicas
            val("funil_intencao"),           # Funil: Intenção de Compra (R$)
            val("funil_vendas"),             # Funil: Vendas Brutas (R$)
            val("qtd_vendas_brutas"),        # Quantidade de Vendas Brutas
            data_coleta,                     # Data Coleta (quando o script rodou)
            status_txt,                      # Status Anúncio
            motivo,                          # Motivo (se algum anúncio irmão estiver pausado)
        ]

        resultados.append(linha)
        print(f"  ✅ Vendas: {kpis['vendas_brutas']} | Un: {kpis['unidades']} | Visitas: {kpis['visitas_unicas']} | Conv: {kpis['conversao']} | Compradores: {kpis['compradores_unicos']}")

    except Exception as e:
        print(f"  ❌ Erro: {e}")
        resultados.append([data_referencia, nome, mlb_id_coluna] + ["ERRO"] * 13 + ["Erro", str(e)[:100]])

    time.sleep(2)

# ─── SALVAR NO SHEETS (dedup: remove linhas do dia atual e reinserir) ────────
print("\n💾 Salvando no Google Sheets (dedup)...")
try:
    todas = aba.get_all_values()
    cabecalho = todas[0] if todas else []
    linhas_existentes = todas[1:] if len(todas) > 1 else []

    # Remove linhas com a data de referência (ontem) para evitar duplicatas em re-execuções
    linhas_manter = [r for r in linhas_existentes if r[0] != data_referencia]
    removidas = len(linhas_existentes) - len(linhas_manter)
    if removidas:
        print(f"  🗑️  Removendo {removidas} linha(s) antigas de {data_referencia}...")

    novos_dados = [cabecalho] + linhas_manter + resultados
    aba.clear()
    aba.update('A1', novos_dados, value_input_option='USER_ENTERED')
    print(f"✅ {len(resultados)} produtos salvos na aba Desempenho_Anuncios")
except Exception as e:
    print(f"❌ Erro ao salvar: {e}")

# ─── SALVAR JSON (2026-07-21) ────────────────────────────────────
# Este script roda no GitHub Actions (sem disco persistente entre execuções),
# então o JSON é commitado de volta no repositório pelo workflow — o
# weekly_digest.py (VPS) busca via URL raw do GitHub, mesma técnica de
# urlopen já usada para os CSVs, sem depender de cache de publish-to-web
# do Google Sheets (ver 28_ROADMAP_PIPELINE.md, Fase 6.1 / A5).
print("\n💾 Salvando snapshot JSON...")
try:
    COLUNAS_DESEMPENHO = [
        "data_referencia", "produto", "mlb_id", "vendas_brutas_r$", "vendas_concluidas_r$",
        "unidades_vendidas", "preco_medio_r$", "visitas_unicas", "total_visitas",
        "compradores_unicos", "conversao_pct", "funil_visitas", "funil_intencao_r$",
        "funil_vendas_r$", "qtd_vendas_brutas", "data_coleta", "status_anuncio", "motivo",
    ]
    resultados_json = [dict(zip(COLUNAS_DESEMPENHO, linha)) for linha in resultados]

    payload = {
        "run_at": datetime.now().astimezone().isoformat(),
        "data_referencia": data_referencia,
        "data_coleta": data_coleta,
        "produtos_monitorados": len(PRODUTOS),
        "produtos_novos_descobertos": produtos_novos_descobertos,
        "resultados": resultados_json,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/desempenho_latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    data_arquivo = datetime.strptime(data_referencia, "%d/%m/%Y").strftime("%Y-%m-%d")
    with open(f"data/desempenho_{data_arquivo}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON salvo: data/desempenho_latest.json + data/desempenho_{data_arquivo}.json")
except Exception as e:
    print(f"❌ Erro ao salvar JSON: {e}")

if driver:
    driver.quit()
print("\n🎉 Coleta concluída!")
print(f"📎 Planilha: https://docs.google.com/spreadsheets/d/{ID_PLANILHA}")
