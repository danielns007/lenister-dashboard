#!/usr/bin/env python3
"""Lenister Promocoes collector (read-only).

Raspa a Central de Promocoes do Mercado Livre (Selenium, mesmo login por
chrome_profile ja usado em coletar_desempenho.py) e classifica cada anuncio
em: ativa, programada, sem_promocao ou sem_estoque - mesmas regras ja
validadas na skill Codex "analisar-promocoes-meli" (que le PDF exportado
manualmente; este script pega o mesmo tipo de dado direto da pagina, sem
precisar de export manual).

Fonte do dado: a pagina injeta um JSON de estado (_n.ctx.r, framework
"Nordic" do ML) diretamente no HTML depois que a aba "Promocoes" e clicada
(troca de estado client-side, nao muda a URL). Sem Selenium nao da pra
pegar esse dado: (1) a pagina exige login, (2) o JSON so aparece apos a
interacao no navegador.

Regras de classificacao (idem a skill, ver PROJETO_SKILL_ANALISE_PROMOCOES_MELI.md):
- itemStatus == "active" -> promocao ativa (registra desconto/preco final/
  voce recebe reais).
- itemStatus == "programmed" -> promocao programada (ainda nao vigente).
- Nenhum box active/programmed, com estoque -> sem promocao (so preco
  normal, nunca preco simulado de oportunidade "Participar").
- "sin stock" / "sem estoque" no campo de deposito -> sem_estoque (fora da
  tabela operacional, so alerta).

Escreve:
- Aba "Promocoes" na planilha ID_PLANILHA (mesma dos outros coletores).
- data/promocoes_latest.json + data/promocoes_YYYY-MM-DD.json.

Escopo v1: leitura apenas. Nao adere, pausa nem altera nenhuma promocao.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

ID_PLANILHA = os.environ.get("ML_SPREADSHEET_ID", "18qObMZY06om7paVmu7RxtakWBOAGb216HY9ScPfyFHk")
_creds_default = r"C:\Users\DanielNS\Lenister\credenciais.json"
CREDENCIAIS = "credenciais.json" if os.path.exists("credenciais.json") else _creds_default
HEADLESS = os.environ.get("CHROME_HEADLESS", "").lower() in ("1", "true", "yes")
URL_PROMOCOES = "https://vendedores.mercadolivre.com.br/anuncios/lista/promos"

hoje = datetime.now()
data_coleta = hoje.strftime("%d/%m/%Y")
data_arquivo = hoje.strftime("%Y-%m-%d")


# ─── SELENIUM: login + navegacao ──────────────────────────────────
def abrir_pagina_promocoes():
    options = Options()
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")

    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-setuid-sandbox")
    else:
        options.add_argument("--start-maximized")
        chrome_profile = r"C:\Users\DanielNS\Lenister\chrome_profile"
        if os.path.exists(chrome_profile):
            options.add_argument(f"--user-data-dir={chrome_profile}")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })

    # Mesma logica de coletar_desempenho.py: em modo headless (CI/GitHub
    # Actions) nao ha chrome_profile persistente, entao precisa injetar
    # cookies de sessao (ML_COOKIES_JSON) - cookies so podem ser adicionados
    # com o dominio ja carregado, por isso visita mercadolivre.com.br antes
    # de ir pra vendedores.mercadolivre.com.br (subdominio diferente).
    ml_cookies_json = os.environ.get("ML_COOKIES_JSON", "")
    if HEADLESS and ml_cookies_json:
        driver.get("https://www.mercadolivre.com.br/")
        time.sleep(4)
        try:
            cookies = json.loads(ml_cookies_json)
            for cookie in cookies:
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    pass
            print("Cookies de sessao injetados.")
        except Exception as exc:
            print(f"AVISO: falha ao injetar cookies: {exc}", file=sys.stderr)

    print("Abrindo Central de Promocoes...")
    driver.get(URL_PROMOCOES)
    time.sleep(8)

    if "login" in driver.current_url.lower() or "identificacao" in driver.current_url.lower():
        if HEADLESS:
            print(
                "ERRO: nao esta logado em modo headless — ML_COOKIES_JSON "
                "nao definido ou cookies invalidos/expirados.",
                file=sys.stderr,
            )
            driver.quit()
            sys.exit(1)
        print("Nao esta logado. Faca login manualmente no Chrome e pressione ENTER...")
        input()

    # Clicar na aba "Promocoes" (ao lado de "Visualizacao rapida") - e onde
    # o JSON de estado com os produtos/promocoes de verdade e populado.
    try:
        aba = driver.find_element(By.XPATH, "//*[normalize-space(text())='Promoções' and not(ancestor::nav)]")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", aba)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", aba)
        time.sleep(6)
    except Exception as exc:
        print(f"AVISO: falha ao clicar na aba Promocoes: {exc}", file=sys.stderr)

    html = driver.page_source
    driver.quit()
    return html


# ─── Extracao do JSON embutido no HTML ────────────────────────────
def extrair_estado(html: str) -> dict:
    idx = html.find("_n.ctx.r=")
    if idx == -1:
        raise RuntimeError("Bloco _n.ctx.r nao encontrado na pagina - layout do ML pode ter mudado.")
    start = idx + len("_n.ctx.r=")

    depth = 0
    i = start
    in_str = False
    esc = False
    while i < len(html):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
        i += 1

    return json.loads(html[start:i])


# ─── Parser das linhas de promocao ────────────────────────────────
def texto_coluna(col: dict) -> str:
    """Concatena o texto de uma coluna. 3 formatos de linha: primaryText.content
    (padrao), type=charges (totalCharges.value - coluna 'Voce recebe'),
    type=inline-text (segments[].content - linha 'Reduzimos R$X das tarifas')."""
    partes = []
    for line in col.get("lines", []):
        pt = line.get("primaryText")
        if pt and pt.get("content"):
            partes.append(pt["content"])
            continue
        if line.get("type") == "charges":
            valor = (line.get("totalCharges") or {}).get("value")
            if valor:
                partes.append(valor)
            continue
        if line.get("type") == "inline-text":
            texto = "".join(s.get("content", "") for s in line.get("segments", []))
            if texto:
                partes.append(texto)
    return " | ".join(partes)


def mlb_da_coluna_recebe(col: dict) -> str | None:
    """itemId especifico (MLB) na coluna 'Voce recebe', quando presente -
    ajuda a distinguir Classico/Premium quando a familia agrupa os dois
    anuncios numa mesma linha com preco em faixa."""
    for line in col.get("lines", []):
        if line.get("type") == "charges":
            item_id = (line.get("totalCharges") or {}).get("itemId")
            if item_id:
                return item_id
    return None


def titulo_e_vigencia(col: dict) -> tuple[str | None, str | None, str | None]:
    """Separa a coluna 0 em (titulo, vigencia, selo) em vez de juntar tudo
    numa string so - a coluna tem ate 3 linhas: titulo da promocao, texto de
    vigencia/data, e um selo tipo=pill (ATIVA/PROGRAMADA) que nao e texto de
    vigencia de verdade."""
    titulo = None
    vigencia = None
    selo = None
    for line in col.get("lines", []):
        if line.get("type") == "pill":
            selo = (line.get("primaryText") or {}).get("content")
            continue
        pt = line.get("primaryText")
        texto = pt.get("content") if pt else None
        if texto is None:
            continue
        if titulo is None:
            titulo = texto
        elif vigencia is None:
            vigencia = texto
    return titulo, vigencia, selo


def parse_box(box: dict) -> dict:
    cols = box.get("columns", [])
    textos = [texto_coluna(c) for c in cols]
    titulo, vigencia, selo = titulo_e_vigencia(cols[0]) if cols else (None, None, None)
    botao = None
    if cols:
        for line in cols[-1].get("lines", []):
            if line.get("type") == "button":
                botao = line.get("button", {}).get("text")
    return {
        "item_status": box.get("itemStatus"),
        "titulo_promocao": titulo,
        "vigencia": vigencia,
        "selo": selo,
        "desconto": textos[1] if len(textos) > 1 else None,
        "preco_final": textos[2] if len(textos) > 2 else None,
        "voce_recebe": textos[3] if len(textos) > 3 else None,
        "mlb_especifico": mlb_da_coluna_recebe(cols[3]) if len(cols) > 3 else None,
        "botao": botao,
    }


def classificar_produto(row: dict) -> dict:
    desc = row["bricks"][0]["bricks"][0]["data"]
    collapsible = row["bricks"][0]["bricks"][1]["data"]
    promo_list = collapsible.get("promotionList", {})
    boxes = promo_list.get("promotionBoxes", []) + promo_list.get("collapsibleRows", [])
    coupons = collapsible.get("couponList", [])

    boxes_parsed = [parse_box(b) for b in boxes]
    ativas = [b for b in boxes_parsed if b["item_status"] == "active"]
    programadas = [b for b in boxes_parsed if b["item_status"] == "programmed"]

    info = (desc.get("info") or "").lower()
    sem_estoque = "sin stock" in info or "sem estoque" in info or "0 u." in info

    if ativas:
        categoria = "ativa"
    elif programadas:
        categoria = "programada"
    elif sem_estoque:
        categoria = "sem_estoque"
    else:
        categoria = "sem_promocao"

    return {
        "id": desc.get("id"),
        "titulo": desc.get("title"),
        "tipo_anuncio": desc.get("extraInfo"),
        "preco": desc.get("price"),
        "estoque_info": desc.get("info"),
        "categoria": categoria,
        "promocoes_ativas": ativas,
        "promocoes_programadas": programadas,
        "total_oportunidades": len(boxes_parsed) - len(ativas) - len(programadas),
        "cupons": [
            {"label": c.get("label"), "discount": c.get("discount"), "status": c.get("status")}
            for c in coupons
        ],
    }


def montar_payload(state: dict) -> dict:
    bricks_raiz = state["appProps"]["pageProps"]["brickTree"]["bricks"]
    # bricks[3] e a lista de produtos - buscado por indice porque foi o unico
    # bloco com promotionBoxes nos testes; se o layout mudar, isso quebra
    # com erro claro (nunca falha silenciosamente pra lista vazia).
    linhas_produto = None
    for b in bricks_raiz:
        if b.get("bricks") and "promotionList" in json.dumps(b)[:2000000] and len(b["bricks"]) > 1:
            linhas_produto = b["bricks"]
            break
    if linhas_produto is None:
        linhas_produto = bricks_raiz[3]["bricks"]

    produtos = [classificar_produto(row) for row in linhas_produto]

    resumo = {"ativa": 0, "programada": 0, "sem_estoque": 0, "sem_promocao": 0}
    for p in produtos:
        resumo[p["categoria"]] += 1

    return {
        "run_at": datetime.now().astimezone().isoformat(),
        "run_date": data_arquivo,
        "total_produtos": len(produtos),
        "resumo": resumo,
        "produtos": produtos,
        "fonte": "Selenium, Central de Promocoes do Mercado Livre (vendedores.mercadolivre.com.br/anuncios/lista/promos)",
    }


# ─── Google Sheets ─────────────────────────────────────────────────
def gravar_sheets(payload: dict) -> None:
    print("Conectando ao Google Sheets...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(CREDENCIAIS, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(ID_PLANILHA)

    try:
        aba = sh.worksheet("Promocoes")
    except gspread.WorksheetNotFound:
        aba = sh.add_worksheet(title="Promocoes", rows=500, cols=12)

    cabecalho = [
        "Data Coleta", "Categoria", "Produto", "ID Anuncio", "Tipo Anuncio",
        "Promocao", "Vigencia", "Desconto", "Preco Final", "Voce Recebe",
        "Preco Normal", "Estoque Info",
    ]
    linhas = [cabecalho]
    for p in payload["produtos"]:
        if p["categoria"] == "ativa":
            for a in p["promocoes_ativas"]:
                linhas.append([
                    data_coleta, "Ativa", p["titulo"], p["id"], p["tipo_anuncio"],
                    a["titulo_promocao"], a["vigencia"], a["desconto"], a["preco_final"],
                    a["voce_recebe"], "", p["estoque_info"],
                ])
        elif p["categoria"] == "programada":
            for pr in p["promocoes_programadas"]:
                linhas.append([
                    data_coleta, "Programada", p["titulo"], p["id"], p["tipo_anuncio"],
                    pr["titulo_promocao"], pr["vigencia"], pr["desconto"], pr["preco_final"],
                    "", "", p["estoque_info"],
                ])
        elif p["categoria"] == "sem_promocao":
            linhas.append([
                data_coleta, "Sem promocao", p["titulo"], p["id"], p["tipo_anuncio"],
                "", "", "", "", "", p["preco"], p["estoque_info"],
            ])
        else:  # sem_estoque
            linhas.append([
                data_coleta, "Sem estoque", p["titulo"], p["id"], p["tipo_anuncio"],
                "", "", "", "", "", p["preco"], p["estoque_info"],
            ])

    # Acrescenta ao final (nunca sobrescreve historico - mesma regra do
    # PAINEL_PROMOCOES.md: nova coleta sempre soma, nunca apaga snapshot
    # anterior). Header so na primeira vez (planilha vazia).
    if not aba.get_all_values():
        aba.append_rows(linhas, value_input_option="USER_ENTERED")
    else:
        aba.append_rows(linhas[1:], value_input_option="USER_ENTERED")

    print(f"OK - {len(linhas) - 1} linhas gravadas na aba 'Promocoes'.")


def salvar_json(payload: dict) -> None:
    Path("data").mkdir(exist_ok=True)
    with open("data/promocoes_latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(f"data/promocoes_{data_arquivo}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"JSON salvo: data/promocoes_latest.json + data/promocoes_{data_arquivo}.json")


def main() -> int:
    html = abrir_pagina_promocoes()
    state = extrair_estado(html)
    payload = montar_payload(state)

    print(
        f"OK - {payload['total_produtos']} produtos | "
        f"{payload['resumo']['ativa']} ativa(s), "
        f"{payload['resumo']['programada']} programada(s), "
        f"{payload['resumo']['sem_promocao']} sem promocao, "
        f"{payload['resumo']['sem_estoque']} sem estoque."
    )

    salvar_json(payload)
    gravar_sheets(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
