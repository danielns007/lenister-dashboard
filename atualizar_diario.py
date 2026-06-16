#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Atualização diária — Lenister Dashboard.
Executado pelo GitHub Actions às 06:00 BRT (09:00 UTC).
Lê credenciais de variáveis de ambiente (Secrets do GitHub).
Localmente usa os arquivos em C:\\Users\\DanielNS\\Lenister\\ por fallback.
"""
import os
import sys
import json
import subprocess
from datetime import datetime


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def setup_credentials():
    """Cria arquivos de credenciais a partir de env vars (GitHub Actions)."""

    # Google Service Account JSON
    google_creds = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if google_creds:
        with open('credenciais.json', 'w') as f:
            f.write(google_creds)
        log("✅ credenciais.json criado a partir de GOOGLE_CREDENTIALS_JSON")

    # ML Token — forçar renovação via refresh_token
    refresh_token = os.environ.get('ML_REFRESH_TOKEN')
    if refresh_token:
        token_data = {
            "refresh_token": refresh_token,
            "access_token": "",
            "saved_at": "2000-01-01T00:00:00",  # força renovação imediata
        }
        token_path = os.path.abspath('ml_token.json')
        with open(token_path, 'w') as f:
            json.dump(token_data, f, indent=2)
        # Garante que processos filhos encontrem o arquivo no diretório corrente
        os.environ['ML_TOKEN_FILE'] = token_path
        log(f"✅ ml_token.json criado com ML_REFRESH_TOKEN → {token_path}")

    # Validar credenciais Google
    if google_creds:
        try:
            parsed = json.loads(google_creds)
            campos = parsed.keys() if isinstance(parsed, dict) else []
            obrigatorios = {'client_email', 'token_uri', 'private_key'}
            faltando = obrigatorios - set(campos)
            if faltando:
                log(f"⚠️  GOOGLE_CREDENTIALS_JSON está malformado — faltam campos: {faltando}")
                log("   Certifique-se de colar o JSON bruto do credenciais.json SEM usar ConvertTo-Json")
            else:
                log(f"✅ GOOGLE_CREDENTIALS_JSON válido ({len(campos)} campos)")
        except json.JSONDecodeError as e:
            log(f"⚠️  GOOGLE_CREDENTIALS_JSON não é JSON válido: {e}")

    if not google_creds and not refresh_token:
        log("ℹ️  Nenhuma env var de credenciais — usando arquivos locais")


def run(script, extra_env=None):
    log(f"▶ {script}...")
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        [sys.executable, script],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    if r.returncode == 0:
        log(f"✅ {script} OK")
        if r.stdout:
            log(r.stdout[-500:])
        return True
    else:
        log(f"❌ {script} ERRO (código {r.returncode})")
        if r.stderr:
            log(r.stderr[-800:])
        if r.stdout:
            log(r.stdout[-300:])
        return False


if __name__ == '__main__':
    log("=" * 50)
    log("  Atualização Diária — Lenister Dashboard")
    log("=" * 50)

    setup_credentials()

    # Ordem obrigatória
    scripts = [
        'renovar_token.py',
        'coletar_ads_api.py',
        'coletar_vendas_api.py',
        'coletar_custos_api_v2_reports.py',
        'coletar_desempenho.py',
    ]

    resultados = {}
    for s in scripts:
        # coletar_desempenho.py precisa de CHROME_HEADLESS=1 no CI
        extra = {'CHROME_HEADLESS': '1'} if s == 'coletar_desempenho.py' else None
        resultados[s] = run(s, extra_env=extra)

    log("\n=== RESUMO ===")
    for s, ok in resultados.items():
        log(f"  {'✅' if ok else '❌'} {s}")

    falhas = [s for s, ok in resultados.items() if not ok]

    # coletar_desempenho.py é opcional (depende de cookies ML) — não falha o job
    falhas_criticas = [f for f in falhas if f != 'coletar_desempenho.py']

    if falhas:
        log(f"\n⚠️  {len(falhas)} script(s) com falha: {falhas}")
        if not falhas_criticas:
            log("   (apenas coletar_desempenho.py — falha não-crítica)")

    if falhas_criticas:
        log(f"\n❌ {len(falhas_criticas)} falha(s) crítica(s) — saindo com código 1")
        sys.exit(1)

    log("\n✅ Atualização concluída!")
