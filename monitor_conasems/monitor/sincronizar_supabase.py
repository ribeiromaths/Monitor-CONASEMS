from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
HISTORY_FILE = ROOT / "dados" / "historico.xlsx"

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ndmuexdgukgfmzyoilda.supabase.co").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

TABLE_URL = f"{SUPABASE_URL}/rest/v1/publicacoes"
CHUNK_SIZE = 100

def iso_date(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    dt = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(dt):
        return None
    return dt.strftime("%Y-%m-%d")

def clean(v):
    if pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None

def main():
    if not SUPABASE_SECRET_KEY:
        raise SystemExit(
            "SUPABASE_SECRET_KEY não definida. "
            "No GitHub Actions, crie esse Secret com a chave sb_secret_... do Supabase."
        )

    if not HISTORY_FILE.exists():
        raise SystemExit(f"Histórico não encontrado: {HISTORY_FILE}")

    df = pd.read_excel(HISTORY_FILE, dtype=str).fillna("")

    required = [
        "FONTE","DATA","ÓRGÃO","CATEGORIA","RELEVÂNCIA",
        "PALAVRAS_ENCONTRADAS","PORTARIA","CNES","MUNICÍPIO",
        "VALORES","TÍTULO","RESUMO","LINK","COLETA_EM"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"Colunas ausentes no histórico: {missing}")

    rows = []
    for _, r in df.iterrows():
        link = clean(r["LINK"])
        titulo = clean(r["TÍTULO"])
        data = iso_date(r["DATA"])
        if not link or not titulo or not data:
            continue

        rows.append({
            "fonte": clean(r["FONTE"]) or "CONASEMS",
            "data": data,
            "orgao": clean(r["ÓRGÃO"]),
            "categoria": clean(r["CATEGORIA"]),
            "relevancia": clean(r["RELEVÂNCIA"]),
            "palavras": clean(r["PALAVRAS_ENCONTRADAS"]),
            "portaria": clean(r["PORTARIA"]),
            "cnes": clean(r["CNES"]),
            "municipio": clean(r["MUNICÍPIO"]),
            "valores": clean(r["VALORES"]),
            "titulo": titulo,
            "resumo": clean(r["RESUMO"]),
            "link": link,
            "coleta_em": clean(r["COLETA_EM"]),
        })

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
        "User-Agent": "Radar-SUS-GitHub-Actions/1.0",
    }

    total = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i:i+CHUNK_SIZE]
        for attempt in range(3):
            try:
                response = requests.post(
                    TABLE_URL,
                    params={"on_conflict": "link"},
                    headers=headers,
                    json=chunk,
                    timeout=90,
                )
                if 200 <= response.status_code < 300:
                    total += len(chunk)
                    print(f"Supabase: {total}/{len(rows)} registros sincronizados.")
                    break

                if attempt == 2:
                    raise SystemExit(
                        f"Supabase respondeu HTTP {response.status_code}: "
                        f"{response.text[:1000]}"
                    )
                time.sleep(2 ** attempt)

            except requests.RequestException as exc:
                if attempt == 2:
                    raise SystemExit(f"Falha de conexão com o Supabase: {exc}")
                time.sleep(2 ** attempt)

    print(f"Sincronização concluída. Total enviado: {total}")

if __name__ == "__main__":
    main()
