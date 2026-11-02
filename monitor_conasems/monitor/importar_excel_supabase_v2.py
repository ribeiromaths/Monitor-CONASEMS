from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

# ============================================================
# RADAR SUS — IMPORTADOR SEGURO DE HISTÓRICO PARA O SUPABASE
#
# Comportamento padrão:
# - INSERE somente publicações que ainda não existem (conflito por LINK = ignore).
# - Não atualiza registros existentes.
# - Não toca em analise, analise_em ou analise_por.
# - Não apaga correções manuais já feitas no Supabase.
#
# Para reprocessar/coletar dados, use monitor.py. Este script serve
# apenas para importar um Excel histórico já revisado.
# ============================================================

SUPABASE_URL_PADRAO = "https://ndmuexdgukgfmzyoilda.supabase.co"
TABELA = "publicacoes"
TAMANHO_LOTE_PADRAO = 100
TIMEOUT = 90

COLUNAS_OBRIGATORIAS = [
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

# Cadastro mínimo de CNES prioritários conhecidos.
# Pode ser ampliado no futuro sem modificar a planilha histórica.
CNES_CONHECIDOS = {
    "2409194": {
        "estabelecimento": "HOSPITAL DR LUIZ ANTONIO",
        "municipio": "NATAL/RN",
    }
}


def limpar(valor):
    if pd.isna(valor):
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() in {"nan", "nat", "none"}:
        return None
    return texto


def converter_data(valor):
    if pd.isna(valor) or str(valor).strip() == "":
        return None
    data = pd.to_datetime(valor, dayfirst=True, errors="coerce")
    if pd.isna(data):
        return None
    return data.strftime("%Y-%m-%d")


def ler_excel(caminho):
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    if caminho.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("Use um arquivo .xlsx ou .xlsm.")

    df = pd.read_excel(caminho, dtype=str).fillna("")
    df.columns = [str(c).strip() for c in df.columns]

    faltantes = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltantes:
        raise ValueError(
            "O arquivo não possui todas as colunas esperadas.\n"
            f"Colunas faltantes: {', '.join(faltantes)}\n"
            f"Colunas encontradas: {', '.join(df.columns)}"
        )

    # A coluna só existe em históricos gerados após a atualização do monitor.
    if "ESTABELECIMENTO" not in df.columns:
        df["ESTABELECIMENTO"] = ""

    return df


def preparar_registros(df):
    registros = []
    ignorados = []
    vistos = set()
    duplicados_excel = 0

    for numero_linha, (_, linha) in enumerate(df.iterrows(), start=2):
        link = limpar(linha.get("LINK"))
        titulo = limpar(linha.get("TÍTULO"))
        data = converter_data(linha.get("DATA"))

        motivos = []
        if not link:
            motivos.append("LINK vazio")
        if not titulo:
            motivos.append("TÍTULO vazio")
        if not data:
            motivos.append("DATA inválida ou vazia")

        if motivos:
            ignorados.append({
                "LINHA_EXCEL": numero_linha,
                "MOTIVO": "; ".join(motivos),
                "LINK": link or "",
                "TÍTULO": titulo or "",
            })
            continue

        if link in vistos:
            duplicados_excel += 1
            continue
        vistos.add(link)

        cnes = limpar(linha.get("CNES"))
        municipio = limpar(linha.get("MUNICÍPIO"))
        estabelecimento = limpar(linha.get("ESTABELECIMENTO"))

        # Se o código estiver na lista de referência, completa dados ausentes.
        if cnes:
            for codigo in re.findall(r"(?<!\d)\d{7}(?!\d)", cnes):
                info = CNES_CONHECIDOS.get(codigo, {})
                if not estabelecimento:
                    estabelecimento = info.get("estabelecimento") or estabelecimento
                if not municipio:
                    municipio = info.get("municipio") or municipio

        registros.append({
            "fonte": limpar(linha.get("FONTE")) or "CONASEMS",
            "data": data,
            "orgao": limpar(linha.get("ÓRGÃO")),
            "categoria": limpar(linha.get("CATEGORIA")),
            "relevancia": limpar(linha.get("RELEVÂNCIA")),
            "palavras": limpar(linha.get("PALAVRAS_ENCONTRADAS")),
            "portaria": limpar(linha.get("PORTARIA")),
            "cnes": cnes,
            "estabelecimento": estabelecimento,
            "municipio": municipio,
            "valores": limpar(linha.get("VALORES")),
            "titulo": titulo,
            "resumo": limpar(linha.get("RESUMO")),
            "link": link,
            "coleta_em": limpar(linha.get("COLETA_EM")),
        })

    return registros, ignorados, duplicados_excel


def obter_credenciais():
    url = os.getenv("SUPABASE_URL", SUPABASE_URL_PADRAO).rstrip("/")
    chave = os.getenv("SUPABASE_SECRET_KEY", "").strip()

    if not chave:
        print("Informe a Secret Key do Supabase (a digitação ficará oculta).")
        chave = getpass.getpass("SUPABASE_SECRET_KEY: ").strip()

    if not chave:
        raise ValueError("A chave secreta do Supabase não foi informada.")

    if chave.startswith("sb_publishable_"):
        raise ValueError(
            "Foi informada uma Publishable Key. "
            "Use a Secret Key sb_secret_... ou a antiga service_role."
        )

    return url, chave


def enviar_lote(url, chave, lote):
    endpoint = f"{url}/rest/v1/{TABELA}"
    headers = {
        "apikey": chave,
        "Authorization": f"Bearer {chave}",
        "Content-Type": "application/json",
        # IGNORE: se o LINK já existe, não atualiza nem apaga campos existentes.
        "Prefer": "resolution=ignore-duplicates,return=representation",
    }

    for tentativa in range(1, 4):
        try:
            resposta = requests.post(
                endpoint,
                params={"on_conflict": "link"},
                headers=headers,
                json=lote,
                timeout=TIMEOUT,
            )

            if 200 <= resposta.status_code < 300:
                try:
                    inseridos = resposta.json()
                    return True, len(inseridos) if isinstance(inseridos, list) else None, ""
                except ValueError:
                    return True, None, ""

            erro = f"HTTP {resposta.status_code}: {resposta.text[:2000]}"

        except requests.RequestException as exc:
            erro = f"Erro de conexão: {exc}"

        if tentativa < 3:
            espera = 2 ** (tentativa - 1)
            print(f"  Tentativa {tentativa} falhou. Nova tentativa em {espera}s.")
            time.sleep(espera)
        else:
            return False, 0, erro

    return False, 0, "Falha desconhecida."


def salvar_ignorados(caminho_excel, linhas):
    if not linhas:
        return None
    destino = caminho_excel.with_name(
        f"{caminho_excel.stem}_linhas_ignoradas.xlsx"
    )
    pd.DataFrame(linhas).to_excel(destino, index=False)
    return destino


def main():
    parser = argparse.ArgumentParser(
        description="Importa um histórico Excel para o Supabase do Radar SUS."
    )
    parser.add_argument("arquivo", help="Caminho do Excel .xlsx ou .xlsm.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Valida o Excel sem conectar nem enviar dados.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=TAMANHO_LOTE_PADRAO,
        help=f"Registros por lote. Padrão: {TAMANHO_LOTE_PADRAO}.",
    )
    args = parser.parse_args()

    if args.chunk_size < 1:
        parser.error("--chunk-size deve ser maior que zero.")

    caminho = Path(args.arquivo).expanduser().resolve()
    df = ler_excel(caminho)
    registros, ignorados, duplicados_excel = preparar_registros(df)

    datas = [r["data"] for r in registros]

    print("=" * 72)
    print("RADAR SUS — IMPORTAÇÃO SEGURA DO HISTÓRICO")
    print("=" * 72)
    print(f"Arquivo: {caminho}")
    print(f"Linhas lidas: {len(df):,}")
    print(f"Registros válidos: {len(registros):,}")
    print(f"Duplicados dentro do Excel: {duplicados_excel:,}")
    print(f"Linhas ignoradas: {len(ignorados):,}")
    if datas:
        print(f"Período: {min(datas)} até {max(datas)}")
    print("Estratégia: inserir somente LINKs ainda não existentes.")
    print("Registros existentes e análises gerenciais serão preservados.")
    print()

    relatorio = salvar_ignorados(caminho, ignorados)
    if relatorio:
        print(f"Relatório de linhas ignoradas: {relatorio}")

    if not registros:
        raise ValueError("Nenhum registro válido foi encontrado.")

    if args.dry_run:
        print("DRY-RUN: nenhum dado foi enviado ao Supabase.")
        for r in registros[:8]:
            print(
                f"  {r['data']} | {r['portaria'] or r['titulo']} | "
                f"CNES: {r['cnes'] or '—'} | {r['link']}"
            )
        print("\nValidação concluída.")
        return 0

    url, chave = obter_credenciais()

    sucesso = 0
    contagem_indisponivel = False
    falhas = []
    total_lotes = (len(registros) + args.chunk_size - 1) // args.chunk_size

    for numero, inicio in enumerate(
        range(0, len(registros), args.chunk_size), start=1
    ):
        lote = registros[inicio:inicio + args.chunk_size]
        print(
            f"Enviando lote {numero}/{total_lotes} "
            f"({len(lote)} registros)...",
            end=" ",
            flush=True,
        )

        ok, quantidade, erro = enviar_lote(url, chave, lote)
        if ok:
            if quantidade is None:
                contagem_indisponivel = True
                print("OK")
            else:
                sucesso += quantidade
                print(f"OK — {quantidade} novos registros")
        else:
            falhas.append({"lote": numero, "erro": erro})
            print("FALHA")
            print(f"  {erro}")

    print("\n" + "=" * 72)
    print("RESULTADO DA IMPORTAÇÃO")
    print("=" * 72)
    print(f"Registros válidos enviados: {len(registros):,}")
    if not contagem_indisponivel:
        print(f"Novos registros inseridos: {sucesso:,}")
        print(f"Links que já existiam: {len(registros) - sucesso:,}")
    else:
        print("O Supabase aceitou os lotes; a API não retornou contagem individual.")
    print(f"Lotes com falha: {len(falhas):,}")

    if falhas:
        print("Os lotes com falha podem ser reexecutados com segurança.")
        return 2

    print(
        "\nConcluído. Nenhuma publicação existente foi atualizada ou excluída. "
        "Os campos de análise gerencial também não foram enviados."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nERRO: {exc}")
        sys.exit(1)
