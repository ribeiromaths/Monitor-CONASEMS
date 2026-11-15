
    
  
        # A planilha pode ter mais de uma linha para o mesmo link. Mantém a
        # última ocorrência, mas não perde os campos enriquecidos já presentes
        # na ocorrência anterior quando a linha mais nova estiver vazia neles.
        if link in by_link:
            duplicates += 1
            previous = by_link[link]
            for field in CAMPOS_ENRIQUECIDOS:
                if field not in record and field in previous:
                    record[field] = previous[field]

        by_link[link] = record

    return list(by_link.values()), ignored, duplicates


def group_by_same_keys(records: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Agrupa objetos com o mesmo conjunto de chaves para evitar PGRST102."""
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)

    for record in records:
        # A ordenação torna a assinatura das chaves determinística.
        key_signature = tuple(sorted(record.keys()))
        groups[key_signature].append(record)

    return list(groups.values())


def send_batch(
    session: requests.Session,
    batch: list[dict[str, str]],
    headers: dict[str, str],
    batch_number: int,
    batch_total: int,
) -> None:
    """Envia um lote homogêneo, repetindo apenas erros temporários."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = session.post(
                TABLE_URL,
                params={"on_conflict": "link"},
                headers=headers,
                json=batch,
                timeout=(20, 90),
            )

            if 200 <= response.status_code < 300:
                print(
                    f"Lote {batch_number}/{batch_total}: "
                    f"{len(batch)} registros sincronizados."
                )
                return

            # Erros 4xx (incluindo erro de estrutura/schema) não se resolvem
            # repetindo a mesma requisição. Interrompe e mostra a resposta.
            if 400 <= response.status_code < 500:
                raise SystemExit(
                    f"Supabase respondeu HTTP {response.status_code}: "
                    f"{response.text[:1500]}"
                )

            if attempt == MAX_ATTEMPTS - 1:
                raise SystemExit(
                    f"Supabase respondeu HTTP {response.status_code} após "
                    f"{MAX_ATTEMPTS} tentativas: {response.text[:1500]}"
                )

            wait = 2 ** attempt
            print(
                f"Erro temporário HTTP {response.status_code}. "
                f"Nova tentativa em {wait}s..."
            )
            time.sleep(wait)

        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise SystemExit(
                    f"Falha de conexão com o Supabase após "
                    f"{MAX_ATTEMPTS} tentativas: {exc}"
                ) from exc

            wait = 2 ** attempt
            print(f"Falha de conexão: {exc}. Nova tentativa em {wait}s...")
            time.sleep(wait)


def main() -> None:
    if not SUPABASE_SECRET_KEY:
        raise SystemExit(
            "SUPABASE_SECRET_KEY não definida. "
            "No GitHub Actions, configure esse Secret com a chave sb_secret_... do Supabase."
        )

    if not HISTORY_FILE.exists():
        raise SystemExit(f"Histórico não encontrado: {HISTORY_FILE}")

    print(f"Lendo histórico: {HISTORY_FILE}")
    df = pd.read_excel(HISTORY_FILE, dtype=str).fillna("")

    records, ignored, duplicates = build_records(df)
    if not records:
        print("Nenhum registro válido encontrado. Nada foi enviado ao Supabase.")
        return

    groups = group_by_same_keys(records)
    batches: list[list[dict[str, str]]] = []
    for group in groups:
        for start in range(0, len(group), CHUNK_SIZE):
            batches.append(group[start : start + CHUNK_SIZE])

    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
        "User-Agent": "Radar-SUS-GitHub-Actions/1.1",
    }

    print(f"Registros válidos: {len(records)}")
    print(f"Linhas ignoradas por falta de link/título/data: {ignored}")
    print(f"Links duplicados consolidados: {duplicates}")
    print(f"Grupos com estrutura de campos homogênea: {len(groups)}")
    print(f"Lotes a enviar: {len(batches)}")

    session = requests.Session()
    try:
        for index, batch in enumerate(batches, start=1):
            send_batch(session, batch, headers, index, len(batches))
    finally:

