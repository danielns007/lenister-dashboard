import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests
from datetime import datetime, timedelta

# ─── Vendas via afiliados (2026-09-12) ────────────────────────────
# Achado na revisão adversarial ADS Lenister: a Lenister participa de
# verdade do programa de afiliados do Mercado Livre (R$ 2.455,87 em vendas
# nos últimos 7 dias, medido ao vivo em 12/09/2026) e isso nunca foi
# coletado. Não existe API OAuth pra esse dado — a tela do vendedor
# (vendedores.mercadolivre.com.br/seller-affiliates/dashboard) consome um
# endpoint interno preso à sessão de navegador (cookie), que responde 403
# pro token Bearer padrão usado no resto do sistema.
#
# NÃO precisa de Selenium: o endpoint devolve JSON puro pra quem tiver a
# cookie de sessão válida — a mesma cookie que coletar_desempenho.py já usa
# via ML_COOKIES_JSON (Secret do GitHub), só que aqui é uma chamada HTTP
# direta com `requests`, sem abrir Chrome. Mais rápido e sem o risco de
# timeout que o coletor de desempenho tem.
#
# Escreve (mesmo padrão dos outros coletores deste repo):
# - data/afiliados_latest.json
# - data/afiliados_YYYY-MM-DD.json

ML_COOKIES_JSON = os.environ.get('ML_COOKIES_JSON', '')
JANELA_DIAS = 30  # cobre o mes corrente inteiro numa chamada so

URL_BASE = 'https://vendedores.mercadolivre.com.br/meliconnect/api/seller-affiliates/dashboard/products'


def montar_cookies(cookies_json):
    """Mesma fonte (ML_COOKIES_JSON) que coletar_desempenho.py injeta via
    driver.add_cookie() -- aqui vira um dict simples {nome: valor} pro
    requests.Session(). requests nao filtra por dominio no dict simples,
    entao funciona igual independente de qual subdominio cada cookie foi
    exportada (www vs vendedores), desde que a sessao seja a mesma conta."""
    cookies = json.loads(cookies_json)
    return {c['name']: c['value'] for c in cookies if 'name' in c and 'value' in c}


def buscar_pagina(sessao, data_inicio, data_fim, page=0):
    params = {
        'date_from': data_inicio,
        'date_to': data_fim,
        'sales_type': 'net_sales',
        'campaign_sales': 'false',
        'page': page,
    }
    r = sessao.get(URL_BASE, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def main():
    if not ML_COOKIES_JSON:
        print("❌ ML_COOKIES_JSON não definido — sem cookie de sessão, não dá pra autenticar.")
        return 1

    hoje = datetime.now()
    data_fim = hoje.strftime('%Y-%m-%d')
    data_inicio = (hoje - timedelta(days=JANELA_DIAS - 1)).strftime('%Y-%m-%d')

    sessao = requests.Session()
    try:
        sessao.cookies.update(montar_cookies(ML_COOKIES_JSON))
    except (json.JSONDecodeError, TypeError) as e:
        print(f"❌ ML_COOKIES_JSON inválido: {e}")
        return 1

    print(f"🔗 Buscando vendas por afiliados de {data_inicio} a {data_fim}...")

    try:
        primeira = buscar_pagina(sessao, data_inicio, data_fim, page=0)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            print("❌ 403 — sessão não autenticada (cookie expirada/inválida). Mesma causa do coletor de desempenho.")
        else:
            print(f"❌ Erro HTTP: {e}")
        return 1
    except Exception as e:
        print(f"❌ Erro na chamada: {e}")
        return 1

    resumo = primeira.get('summary') or {}
    produtos = list((primeira.get('items') or {}).get('products') or [])
    paginacao = (primeira.get('items') or {}).get('pagination') or {}
    total_paginas = paginacao.get('pages', 1)

    for pagina in range(1, total_paginas):
        try:
            extra = buscar_pagina(sessao, data_inicio, data_fim, page=pagina)
            produtos.extend((extra.get('items') or {}).get('products') or [])
        except Exception as e:
            print(f"⚠️ Falha ao buscar página {pagina}: {e}")
            break

    print(f"✅ {len(produtos)} produto(s) com venda via afiliados no período. "
          f"Total: {resumo.get('totalSales', {}).get('value', 0)} | "
          f"Vendas: {resumo.get('salesCount', 0)} | "
          f"Comissão: {resumo.get('fee', {}).get('value', 0)}")

    payload = {
        'run_at': datetime.now().astimezone().isoformat(),
        'periodo': {'de': data_inicio, 'ate': data_fim},
        'resumo': resumo,
        'produtos': produtos,
        'source': 'vendedores.mercadolivre.com.br/meliconnect/api/seller-affiliates (sessao de navegador, sem API OAuth)',
    }

    os.makedirs('data', exist_ok=True)
    with open('data/afiliados_latest.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(f'data/afiliados_{data_fim}.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON salvo: data/afiliados_latest.json + data/afiliados_{data_fim}.json")
    return 0


if __name__ == '__main__':
    sys.exit(main())
