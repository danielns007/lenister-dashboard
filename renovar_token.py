#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Renova o token OAuth via refresh_token"""

import json
import os
import requests
from pathlib import Path

CLIENT_ID     = os.environ.get('ML_CLIENT_ID',     '739123530612333')
CLIENT_SECRET = os.environ.get('ML_CLIENT_SECRET', 'tCZxVQNaeUZKMm8AxQFlsaGTgMYLj4U1')

_token_default = r"C:\Users\DanielNS\Lenister\ml_token.json"
TOKEN_FILE = Path(os.environ.get('ML_TOKEN_FILE', _token_default))

# Carregar token atual
with open(TOKEN_FILE, 'r') as f:
    data = json.load(f)

refresh_token = data.get('refresh_token')

if not refresh_token:
    print("ERRO: refresh_token não encontrado!")
    exit(1)

# Renovar token
print(f"Renovando token...")
resp = requests.post("https://api.mercadolibre.com/oauth/token", data={
    "grant_type": "refresh_token",
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "refresh_token": refresh_token
})

if resp.status_code != 200:
    print(f"ERRO: {resp.status_code}")
    print(resp.text)
    exit(1)

new_data = resp.json()
new_data['refresh_token'] = refresh_token  # Manter o refresh_token antigo

# Salvar
with open(TOKEN_FILE, 'w') as f:
    json.dump(new_data, f, indent=2)

print(f"[OK] Token renovado!")
print(f"   Access Token (novo): {new_data['access_token'][:40]}...")
print(f"   Validade: {new_data['expires_in']} segundos (~6h)")
