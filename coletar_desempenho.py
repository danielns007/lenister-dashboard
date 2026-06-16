import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import time
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
import re

# ─── CONFIGURAÇÕES ───────────────────────────────────────────────
ID_PLANILHA = os.environ.get('ML_SPREADSHEET_ID', '18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk')

_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS = 'credenciais.json' if os.path.exists('credenciais.json') else _creds_default

# Modo headless: ativado por env var (GitHub Actions) ou ausência de display
HEADLESS = os.environ.get('CHROME_HEADLESS', '').lower() in ('1', 'true', 'yes')
# Cookies de sessão ML (base64 ou JSON) armazenados como Secret no GitHub
ML_COOKIES_JSON = os.environ.get('ML_COOKIES_JSON', '')

PRODUTOS = [
    {"nome": "Sirene Estroboscópica",     "id": "MLB6168880144"},
    {"nome": "Fonte 12V",                 "id": "MLB6128512354"},
    {"nome": "Fonte 24V",                 "id": "MLB6128447010"},
    {"nome": "Sonda 0-10mca",             "id": "MLB4470736687"},
    {"nome": "Central Laço 12V Manual",   "id": "MLB4559395191"},
    {"nome": "Fechadura Vidro",           "id": "MLB6718341398"},
    {"nome": "Extensor PoE Giga",         "id": "MLB6508001372"},
    {"nome": "Protetor Cabo",             "id": "MLB5482550358"},
    {"nome": "Fechadura Sobrepor",        "id": "MLB4205584415"},
    {"nome": "Sonda 0-4mca",              "id": "MLB3904989803"},
    {"nome": "Central Laço 12V Preto",    "id": "MLB5697266066"},
    {"nome": "Extensor PoE Hi-AT13FL",    "id": "MLB4241298943"},
    {"nome": "Central Laço 220V",         "id": "MLB5694900528"},
    {"nome": "Sensor Pressão 10 Bar",     "id": "MLB6294668236"},
]

hoje = datetime.now()
ontem = hoje - timedelta(days=1)
data_inicio = ontem.strftime("%Y-%m-%d")   # coleta apenas ontem (1 dia)
data_fim    = ontem.strftime("%Y-%m-%d")
data_referencia = ontem.strftime("%d/%m/%Y")  # data do dado (ontem), usada na planilha
data_coleta     = hoje.strftime("%d/%m/%Y")   # data em que o script rodou

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

# ─── SELENIUM ────────────────────────────────────────────────────
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

# ─── FUNÇÕES AUXILIARES ──────────────────────────────────────────
def limpar_numero(texto):
    if not texto:
        return ""
    # Remove R$, %, espaços e pontos de milhar. Mantém vírgula decimal (parseLocalNumber no JS espera formato BR)
    texto = re.sub(r'[R$%\s]', '', texto)
    texto = texto.replace('.', '')
    return texto.strip()

def eh_numero_valido(texto):
    """Verifica se o texto é um número ou valor monetário válido"""
    if not texto:
        return False
    limpo = re.sub(r'[R$%\s\.,]', '', texto)
    return limpo.isdigit() and len(limpo) <= 10

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


def selecionar_todas_opcoes(driver, timeout=15):
    """
    Se a pagina tiver dropdown 'Condicao de venda', seleciona 'Todas as opcoes de venda'.
    Necessario para produtos com variantes (ex: Extensor PoE Hi-AT13FL).
    Usa JS click para funcionar em headless.
    """
    try:
        fim = time.time() + timeout
        while time.time() < fim:
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
            except Exception:
                time.sleep(1)
                continue

            # Se "Todas as opcoes" ja esta no body como elemento unico = ja selecionado
            if "Todas as op" in body_text:
                els = driver.find_elements(By.XPATH, "//*[contains(text(),'Todas as op')]")
                # Se apenas 1 elemento (o botao fechado), ja esta selecionado
                if len(els) == 1:
                    print("    [info] Todas as opcoes ja selecionado")
                    return
                # Se > 1, o dropdown esta aberto mostrando a opcao — clica nela
                if len(els) > 1:
                    try:
                        driver.execute_script("arguments[0].click();", els[-1])
                        time.sleep(2)
                        print("    OK: Todas as opcoes de venda selecionado")
                    except Exception:
                        pass
                    return

            # Pagina tem variantes mas "Todas as opcoes" nao esta visivel ainda
            if "Condi" in body_text and "Op" in body_text:
                # Abre dropdown via JS — busca qualquer botao com "Op" no texto
                try:
                    abriu = driver.execute_script("""
                        var btns = document.querySelectorAll('button, [role="button"]');
                        for (var b of btns) {
                            if (b.innerText && b.innerText.includes('Op')) {
                                b.click();
                                return true;
                            }
                        }
                        return false;
                    """)
                    if abriu:
                        time.sleep(2)
                        # Agora clica em "Todas as opcoes de venda"
                        clicou = driver.execute_script("""
                            var els = document.querySelectorAll('li, button, [role="option"], [role="menuitem"]');
                            for (var el of els) {
                                if (el.innerText && el.innerText.includes('Todas as op')) {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        """)
                        if clicou:
                            time.sleep(2)
                            print("    OK: Todas as opcoes de venda selecionado")
                            return
                except Exception as e:
                    print(f"    [warn] JS click falhou: {e}")
                time.sleep(1)
                continue

            # Sem dropdown de variantes na pagina
            break

    except Exception as e:
        print(f"    [warn] selecionar_todas_opcoes: {e}")


def extrair_kpis(driver, mlb_id=""):
            break

    except Exception as e:
        print(f"    ⚠️ selecionar_todas_opcoes: {e}")


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

# ─── COLETA POR PRODUTO ──────────────────────────────────────────
resultados = []

for produto in PRODUTOS:
    nome = produto["nome"]
    mlb_id = produto["id"]

    url = (
        f"https://www.mercadolivre.com.br/metricas/{mlb_id}/performance-item"
        f"?start_period_evolutionary=custom|{data_inicio}T03:00:00.000Zto{data_fim}T03:00:00.000Z"
    )

    print(f"📦 Coletando: {nome} ({mlb_id})")

    try:
        driver.get(url)
        # Aguarda SPA inicializar antes de buscar KPIs
        time.sleep(5 if not HEADLESS else 8)

        # Se produto tiver variantes, seleciona "Todas as opções de venda"
        selecionar_todas_opcoes(driver)

        kpis = extrair_kpis(driver, mlb_id)

        # Debug: se todos KPIs vazios, imprimir trecho da página
        if all(v == "" for v in kpis.values()):
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text[:300]
                print(f"    [debug] página vazia — body[:300]: {repr(body_text)}")
            except Exception:
                pass

        linha = [
            data_referencia,                          # Data (ontem — data do dado)
            nome,                                     # Produto
            mlb_id,                                   # MLB ID
            limpar_numero(kpis["vendas_brutas"]),     # Vendas Brutas (R$)
            limpar_numero(kpis["vendas_concluidas"]), # Vendas Concluídas (R$)
            limpar_numero(kpis["unidades"]),          # Unidades Vendidas
            limpar_numero(kpis["preco_medio"]),       # Preço Médio (R$)
            limpar_numero(kpis["visitas_unicas"]),    # Visitas Únicas
            limpar_numero(kpis["total_visitas"]),     # Total de Visitas
            limpar_numero(kpis["compradores_unicos"]),# Compradores Únicos
            limpar_numero(kpis["conversao"]),         # Conversão (%)
            limpar_numero(kpis["funil_visitas"]),     # Funil: Visitas Únicas
            limpar_numero(kpis["funil_intencao"]),    # Funil: Intenção de Compra (R$)
            limpar_numero(kpis["funil_vendas"]),      # Funil: Vendas Brutas (R$)
            limpar_numero(kpis["qtd_vendas_brutas"]), # Quantidade de Vendas Brutas
            data_coleta,                              # Data Coleta (quando o script rodou)
        ]

        resultados.append(linha)
        print(f"  ✅ Vendas: {kpis['vendas_brutas']} | Un: {kpis['unidades']} | Visitas: {kpis['visitas_unicas']} | Conv: {kpis['conversao']} | Compradores: {kpis['compradores_unicos']}")

    except Exception as e:
        print(f"  ❌ Erro: {e}")
        resultados.append([data_referencia, nome, mlb_id] + ["ERRO"] * 13)

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

driver.quit()
print("\n🎉 Coleta concluída!")
print(f"📎 Planilha: https://docs.google.com/spreadsheets/d/{ID_PLANILHA}")
