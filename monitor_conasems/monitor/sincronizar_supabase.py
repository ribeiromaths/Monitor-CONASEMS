from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ============================================================
# CONFIGURAÇÃO
# ============================================================

ROOT = Path(__file__).resolve().parent
HISTORY_FILE = ROOT / "dados" / "historico.xlsx"

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "https://ndmuexdgukgfmzyoilda.supabase.co",
).rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()

TABLE_URL = f"{SUPABASE_URL}/rest/v1/publicacoes"
CHUNK_SIZE = 100
MAX_ATTEMPTS = 3

# Complementa os dados quando o CNES conhecido estiver presente na planilha.
# Não cria um registro se o CNES não estiver informado.
CNES_CONHECIDOS = {
    "2409194": {
        "estabelecimento": "HOSPITAL DR LUIZ ANTONIO",
        "municipio": "NATAL/RN",
    },
}

# Campos opcionais: se a célula estiver vazia, a coluna NÃO será enviada ao
# Supabase. Isso evita substituir valores já existentes por NULL.
CAMPOS_OPCIONAIS = {
    "orgao": "ÓRGÃO",
    "categoria": "CATEGORIA",
    "relevancia": "RELEVÂNCIA",
    "palavras": "PALAVRAS_ENCONTRADAS",
    "portaria": "PORTARIA",
    "cnes": "CNES",
    "municipio": "MUNICÍPIO",
    "valores": "VALORES",
    "resumo": "RESUMO",
    "coleta_em": "COLETA_EM",
    "estabelecimento": "ESTABELECIMENTO",
}

CAMPOS_ENRIQUECIDOS = ("cnes", "estabelecimento", "municipio")


def clean(value: Any) -> str | None:
    """Converte valores da planilha em texto limpo ou None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    return text if text else None


def iso_date(value: Any) -> str | None:
    """Converte datas da planilha para YYYY-MM-DD."""
    value = clean(value)
    if not value:
        return None

    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return None

    return parsed.strftime("%Y-%m-%d")


def normalize_cnes(value: str | None) -> str | None:
    """Normaliza CNES quando a planilha o entrega como número com .0."""
    value = clean(value)
    if not value:
        return None

    # Ex.: 2409194.0 -> 2409194. Mantém textos fora desse padrão intactos.
    match = re.fullmatch(r"(\d{7})(?:\.0+)?", value)
    if match:
        return match.group(1)
    return value


def build_records(df: pd.DataFrame) -> tuple[list[dict[str, str]], int, int]:
    """Monta os registros, omite campos vazios e elimina links duplicados."""
    required = [
        "FONTE",
        "DATA",
        "ÓRGÃO",
        "CATEGORIA",
        "RELEVÂNCIA",
        "PALAVRAS_ENCONTRADAS",
        "PORTARIA",
        "CNES",
        "MUNICÍPIO",
        "VALORES",
        "TÍTULO",
        "RESUMO",
        "LINK",
        "COLETA_EM",
    ]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise SystemExit(f"Colunas ausentes no histórico: {missing}")

    # Campo novo: planilhas antigas podem não possuir essa coluna.
    if "ESTABELECIMENTO" not in df.columns:
        df["ESTABELECIMENTO"] = ""

    by_link: dict[str, dict[str, str]] = {}
    ignored = 0
    duplicates = 0

    for _, row in df.iterrows():
        link = clean(row.get("LINK"))
        title = clean(row.get("TÍTULO"))
        date = iso_date(row.get("DATA"))

        if not link or not title or not date:
            ignored += 1
            continue

        # Estes quatro campos são a base mínima de cada publicação.
        record: dict[str, str] = {
            "fonte": clean(row.get("FONTE")) or "CONASEMS",
            "data": date,
            "titulo": title,
            "link": link,
        }

        # Só adiciona campos opcionais que realmente têm conteúdo.
        for db_field, excel_field in CAMPOS_OPCIONAIS.items():
            value = clean(row.get(excel_field))
            if value:
                if db_field == "cnes":
                    value = normalize_cnes(value)
                if value:
                    record[db_field] = value

        # Complementa os dados do CNES 2409194 quando ele aparece na planilha.
        cnes = record.get("cnes")
        if cnes in CNES_CONHECIDOS:
            known = CNES_CONHECIDOS[cnes]
            record.setdefault("estabelecimento", known["estabelecimento"])
            record.setdefault("municipio", known["municipio"])
