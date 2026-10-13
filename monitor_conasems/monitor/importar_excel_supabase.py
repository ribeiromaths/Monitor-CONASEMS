from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

SUPABASE_URL_PADRAO = "https://ndmuexdgukgfmzyoilda.supabase.co"
TIMEOUT = 90
CHUNK_PADRAO = 100

COLUNAS = {
    "FONTE": "fonte",
    "DATA": "data",
    "ÓRGÃO": "orgao",
    "CATEGORIA": "categoria",
    "RELEVÂNCIA": "relevancia",
    "PALAVRAS_ENCONTRADAS": "palavras",
    "PORTARIA": "portaria",
    "CNES": "cnes",
    "MUNICÍPIO": "municipio",
    "VALORES": "valores",
    "TÍTULO": "titulo",
    "RESUMO": "resumo",
    "LINK": "link",
    "COLETA_EM": "coleta_em",
}


def clean(v):
    if pd.isna(v):
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    return s


def date_iso(v):
    if pd.isna(v) or str(v).strip() == "":
        return None
    d = pd.to_datetime(v, dayfirst=True, errors="coerce")
    return None if pd.isna(d) else d.strftime("%Y-%m-%d")


def read_excel(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError("Use um arquivo .xlsx, .xlsm ou .xls.")
    df = pd.read_excel(path, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in COLUNAS if c not in df.columns]
    if missing:
        raise ValueError(
            "Colunas faltantes: " + ", ".join(missing) +
            "\nColunas encontradas: " + ", ".join(df.columns)
        )
    return df


def prepare(df):
    rows, ignored = [], []
    for excel_line, (_, r) in enumerate(df.iterrows(), start=2):
        link = clean(r["LINK"])
        titulo = clean(r["TÍTULO"])
        data = date_iso(r["DATA"])

        reasons = []
        if not link:
            reasons.append("LINK vazio")
        if not titulo:
            reasons.append("TÍTULO vazio")
        if not data:
            reasons.append("DATA inválida/vazia")

        if reasons:
            ignored.append({
                "linha": excel_line,
                "motivo": "; ".join(reasons),
                "link": link or "",
                "titulo": titulo or "",
            })
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

    # LINK é a chave única do sistema.
    unique = {r["link"]: r for r in rows}
    return list(unique.values()), ignored, len(rows) - len(unique)


def credentials(url_arg):
    url = (url_arg or os.getenv("SUPABASE_URL") or SUPABASE_URL_PADRAO).rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not key:
        key = getpass.getpass("SUPABASE_SECRET_KEY: ").strip()
    if not key:
        raise ValueError("Nenhuma SUPABASE_SECRET_KEY foi informada.")
    return url, key


def headers(key):
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
        "User-Agent": "Radar-SUS-Importador-Excel/1.0",
    }


def send_batch(url, hdrs, batch):
    endpoint = f"{url}/rest/v1/publicacoes"
    for attempt in range(1, 4):
        try:
            r = requests.post(
                endpoint,
                params={"on_conflict": "link"},
                headers=hdrs,
                json=batch,
                timeout=TIMEOUT,
            )
            if 200 <= r.status_code < 300:
                return True, ""
            err = f"HTTP {r.status_code}: {r.text[:1500]}"
        except requests.RequestException as exc:
            err = f"Erro de conexão: {exc}"

        if attempt < 3:
            time.sleep(2 ** (attempt - 1))
        else:
            return False, err
    return False, "Falha desconhecida"


def main():
    ap = argparse.ArgumentParser(
        description="Importa um Excel de publicações para public.publicacoes no Supabase."
    )
    ap.add_argument("arquivo", help="Excel .xlsx/.xlsm/.xls")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--chunk-size", type=int, default=CHUNK_PADRAO)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.chunk_size < 1:
        ap.error("--chunk-size deve ser maior que zero.")

    path = Path(args.arquivo).expanduser().resolve()
    df = read_excel(path)
    rows, ignored, duplicates = prepare(df)

    print("=" * 70)
    print("RADAR SUS — IMPORTAÇÃO EXCEL → SUPABASE")
    print("=" * 70)
    print(f"Arquivo: {path}")
    print(f"Linhas lidas: {len(df):,}")
    print(f"Registros válidos: {len(rows):,}")
    print(f"Duplicados por LINK removidos: {duplicates:,}")
    print(f"Linhas ignoradas: {len(ignored):,}")
    print()

    if ignored:
        report = path.parent / f"{path.stem}_linhas_ignoradas.xlsx"
        pd.DataFrame(ignored).to_excel(report, index=False)
        print(f"Relatório de ignorados: {report}")
        print()

    if not rows:
        raise ValueError("Nenhum registro válido para importar.")

    if args.dry_run:
        print("DRY-RUN: nenhum dado foi enviado.")
        for r in rows[:5]:
            print(f"  {r['data']} | {r['portaria'] or r['titulo']} | {r['link']}")
        return 0

    url, key = credentials(args.supabase_url)
    hdrs = headers(key)

    total_ok = 0
    failures = []
    total_batches = (len(rows) + args.chunk_size - 1) // args.chunk_size

    for n, start in enumerate(range(0, len(rows), args.chunk_size), 1):
        batch = rows[start:start + args.chunk_size]
        print(f"Lote {n}/{total_batches}: {len(batch)} registro(s)...", end=" ")
        ok, err = send_batch(url, hdrs, batch)
        if ok:
            total_ok += len(batch)
            print(f"OK ({total_ok}/{len(rows)})")
        else:
            failures.append((n, len(batch), err))
            print("FALHA")
            print(f"  {err}")

    print()
    print("=" * 70)
    print(f"Importados/atualizados: {total_ok:,}")
    print(f"Falhas: {len(failures):,}")
    print("=" * 70)

    if failures:
        raise SystemExit(2)

    print(
        "Concluído. O script não envia nem altera "
        "analise, analise_em ou analise_por; portanto, "
        "análises gerenciais existentes são preservadas."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperação cancelada.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nERRO: {exc}")
        sys.exit(1)
