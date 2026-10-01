
from __future__ import annotations

import argparse
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parent
KW_FILE = ROOT / "palavras_chave.xlsx"
DATA_DIR = ROOT / "dados"
RESULT_DIR = ROOT / "resultados"
HISTORY_FILE = DATA_DIR / "historico.xlsx"
STATE_FILE = DATA_DIR / "fontes_estado.xlsx"

CONASEMS_BASE = "https://portal.conasems.org.br"
CONASEMS_PATTERN = (
    CONASEMS_BASE +
    "/legislacao-diaria/{id}_legislacao-diaria-nacional-{slug}"
)

DOE_SEARCH = "https://www.diariooficial.rn.gov.br/dei/dorn3/Search.aspx"
DOE_RESULTS = "https://www.diariooficial.rn.gov.br/dei/dorn3/buscamaterias.aspx"
DOM_SEARCH = "https://www2.natal.rn.gov.br/dom/index.php?p=c"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

TIMEOUT = (15, 75)


# ============================================================
# UTILITÁRIOS
# ============================================================
def norm(text):
    text = str(text or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().upper()


def slug_pt(d):
    meses = [
        "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"
    ]
    return f"{d.day}-de-{meses[d.month-1]}-de-{d.year}"


def parse_date(value):
    if value is None:
        return None
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def http_session():
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


def load_keywords():
    df = pd.read_excel(KW_FILE, dtype=str).fillna("")
    required = {"PALAVRA", "CATEGORIA", "RELEVANCIA", "ATIVO"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colunas ausentes em palavras_chave.xlsx: {missing}")
    df["ATIVO"] = df["ATIVO"].str.upper().str.strip()
    return df[df["ATIVO"].isin(["SIM", "S", "1", "TRUE"])].copy()


def keyword_matches(text, kw):
    n = norm(text)
    found, cats, rels = [], [], []
    for _, r in kw.iterrows():
        k = norm(r["PALAVRA"])
        if k and k in n:
            found.append(str(r["PALAVRA"]))
            cats.append(str(r["CATEGORIA"]))
            rels.append(norm(r["RELEVANCIA"]))

    found = list(dict.fromkeys(found))
    cats = list(dict.fromkeys(cats))
    rank = {"ALTA": 3, "MÉDIA": 2, "MEDIA": 2, "BAIXA": 1}
    relevance = max(rels, key=lambda x: rank.get(x, 0)) if rels else ""
    return "; ".join(found), "; ".join(cats), relevance


def extract_money(text):
    vals = re.findall(
        r"R\$\s*[\d\.\s]+,\d{2}|R\$\s*[\d]+(?:\.\d{3})*(?:,\d{2})?",
        text or "",
        flags=re.I,
    )
    return "; ".join(dict.fromkeys(v.strip() for v in vals))


def extract_portaria(text):
    patterns = [
        r"\bPORTARIA(?:\s+CONJUNTA)?\s+(?:GM/MS|SAES/MS|SE/MS|SGTES/SAES|[A-Z]{2,10}(?:/[A-Z]{2,10})?)?\s*(?:N[ºO°]\s*)?[\d\.]+",
        r"\bPORTARIA\s+N[ºO°]\s*[\d\.]+",
    ]
    found = []
    for p in patterns:
        found += re.findall(p, text or "", flags=re.I)
    return "; ".join(dict.fromkeys(re.sub(r"\s+", " ", x).strip() for x in found))


def extract_cnes(text):
    vals = re.findall(r"\bCNES\s*[:\-]?\s*(\d{7})\b", text or "", flags=re.I)
    return "; ".join(dict.fromkeys(vals))


def extract_uf_municipios(text):
    # Heurística simples para destacar municípios/UF quando aparecem.
    m = re.findall(
        r"(?:Município|município|Municipio|municipio)\s+de\s+([A-ZÁÀÃÂÉÊÍÓÔÕÚÇ][A-Za-zÁÀÃÂÉÊÍÓÔÕÚÇãáàâéêíóôõúç\- ]{2,80})"
        r"(?:,\s*(?:no|na|do|da|em|no Estado|no estado)\s+([A-Z]{2}))?",
        text or "",
    )
    out = []
    for city, uf in m:
        city = re.sub(r"\s+", " ", city).strip(" ,.;:")
        if city:
            out.append(f"{city}/{uf}" if uf else city)
    return "; ".join(dict.fromkeys(out))


def make_row(source, d, title, text, url, kw, orgao=""):
    combined = f"{title} {text}"
    hits, cats, rel = keyword_matches(combined, kw)
    return {
        "FONTE": source,
        "DATA": d.strftime("%d/%m/%Y") if isinstance(d, date) else str(d),
        "ÓRGÃO": orgao,
        "CATEGORIA": cats,
        "RELEVÂNCIA": rel,
        "PALAVRAS_ENCONTRADAS": hits,
        "PORTARIA": extract_portaria(combined),
        "CNES": extract_cnes(combined),
        "MUNICÍPIO": extract_uf_municipios(combined),
        "VALORES": extract_money(combined),
        "TÍTULO": re.sub(r"\s+", " ", title).strip(),
        "RESUMO": re.sub(r"\s+", " ", text).strip()[:5000],
        "LINK": url,
        "ID": url or f"{source}|{d}|{title}",
        "COLETA_EM": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# CONASEMS V3
# ============================================================
# O índice /legislacao-diaria atualmente não entrega os itens no HTML
# inicial; as páginas individuais possuem IDs sequenciais. Temos uma
# âncora conhecida (2849 = 31/08/2026) e o programa procura em uma
# janela ao redor do ID estimado, validando a data REAL da página.
#
# Isso evita depender de uma API interna ou de JavaScript.

ANCHOR_ID = 2849
ANCHOR_DATE = date(2026, 8, 31)


def estimated_conasems_id(d):
    # Aproximação por dias úteis; depois a rotina valida e corrige.
    delta = (d - ANCHOR_DATE).days
    business = 0
    step = 1 if delta >= 0 else -1
    cur = ANCHOR_DATE
    for _ in range(abs(delta)):
        cur += timedelta(days=step)
        if cur.weekday() < 5:
            business += step
    return ANCHOR_ID + business


def fetch_conasems_candidate(s, ident, d, debug=False):
    slug = slug_pt(d)
    url = CONASEMS_PATTERN.format(id=ident, slug=slug)
    try:
        r = s.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.find("h1")
        page_text = soup.get_text(" ", strip=True)
        if not h1:
            return None

        title = h1.get_text(" ", strip=True)
        page_date = parse_date(title.replace("-", "/"))
        # Aceita somente se a página realmente corresponder à data pedida.
        if page_date != d and d.strftime("%d/%m/%Y") not in page_text[:500]:
            return None

        if debug:
            (DATA_DIR / f"conasems_{d.isoformat()}_{ident}.html").write_text(
                r.text, encoding="utf-8", errors="ignore"
            )
        return (url, soup)
    except requests.RequestException:
        return None


def locate_conasems_page(s, d, debug=False):
    est = estimated_conasems_id(d)
    # Janela ampla para absorver feriados, dias sem publicação e mudanças.
    ids = range(max(1, est - 20), est + 21)

    for ident in ids:
        found = fetch_conasems_candidate(s, ident, d, debug)
        if found:
            return found
    return None


def scrape_conasems(start, end, kw, debug=False):
    s = http_session()
    rows = []
    d = start

while d <= end:
# CONASEMS normalmente não possui publicação de legislação diária
# aos sábados e domingos. Não fazemos requisições nesses dias.
    if d.weekday() >= 5:
        print(
            f"    CONASEMS {d:%d/%m/%Y}... fim de semana — ignorado",
            flush=True
        )
        d += timedelta(days=1)
        continue

    print(f"    CONASEMS {d:%d/%m/%Y}...", end=" ", flush=True)
        page = locate_conasems_page(s, d, debug)

        if not page:
            print("sem página")
            d += timedelta(days=1)
            continue

        url, soup = page
        main = soup.find("main") or soup
        anchors = main.find_all("a", href=True)

        count = 0
        for a in anchors:
            href = urljoin(url, a.get("href"))
            title = " ".join(a.stripped_strings)

            # O conteúdo de legislação diária aponta para in.gov.br.
            if "in.gov.br" not in href:
                continue
            if not title:
                continue

            # Pega o bloco imediatamente associado ao link.
            parent = a.parent
            text = parent.get_text(" ", strip=True) if parent else title

            # Acrescenta o título da publicação. O filtro é LOCAL.
            combined = f"{title} {text}"
            hits, _, _ = keyword_matches(combined, kw)
            if not hits:
                continue

            rows.append(
                make_row(
                    "CONASEMS", d, title, text, href, kw,
                    orgao="MINISTÉRIO DA SAÚDE / CONASEMS"
                )
            )
            count += 1

        # Se não houver links in.gov, faz fallback para o texto completo.
        if not count:
            text = main.get_text(" ", strip=True)
            hits, _, _ = keyword_matches(text, kw)
            if hits:
                rows.append(
                    make_row(
                        "CONASEMS", d,
                        f"Legislação Diária Nacional - {d:%d/%m/%Y}",
                        text, url, kw, orgao="CONASEMS"
                    )
                )
                count = 1

        print(f"{count} achado(s)")
        d += timedelta(days=1)

    return rows


# ============================================================
# DOE-RN V3
# ============================================================
# O problema da V2 foi fazer uma requisição pesada para CADA palavra.
# Na V3 fazemos poucas consultas "âncora" e depois filtramos localmente.
# Se o endpoint responder lentamente, retry/backoff evita quebrar o programa.

DOE_ANCHORS = [
    "SAUDE",
    "SESAP",
    "SUS",
    "HOSPITAL",
    "SECRETARIA",
]


def scrape_doe_one(s, q, start, end, kw, debug=False):
    params = {
        "av": "1",
        "dataed": end.strftime("%d/%m/%Y"),
        "ini": start.strftime("%d/%m/%Y"),
        "fim": end.strftime("%d/%m/%Y"),
        "modo": "0",
        "procurar": q,
    }

    try:
        r = s.get(DOE_RESULTS, params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"

        soup = BeautifulSoup(r.text, "html.parser")
        if debug:
            (DATA_DIR / f"doe_{norm(q)}.html").write_text(
                r.text, encoding="utf-8", errors="ignore"
            )

        rows = []
        seen = set()

        # Tenta capturar resultados individuais.
        for a in soup.find_all("a", href=True):
            title = " ".join(a.stripped_strings)
            href = urljoin(DOE_RESULTS, a.get("href"))
            if not title or len(title) < 8:
                continue

            low = href.lower()
            # Descarta navegação.
            if any(x in norm(title) for x in [
                "INÍCIO", "BUSCA", "VOLTAR", "MAPA DO SITE",
                "NÓS, DO RN", "CARTA DE SERVIÇO",
            ]):
                continue

            if href in seen:
                continue

            # Só guarda links que parecem matéria/resultado.
            if not any(x in low for x in ["mater", "busca", "dei", "public"]):
                continue

            seen.add(href)
            parent = a.parent
            text = parent.get_text(" ", strip=True) if parent else title

            hits, _, _ = keyword_matches(f"{title} {text}", kw)
            if hits:
                rows.append(
                    make_row(
                        "DOE-RN", end, title, text, href, kw,
                        orgao="DOE-RN"
                    )
                )

        # Fallback: página inteira. Não perde a consulta se o site mudar o
        # HTML dos resultados.
        if not rows:
            body = soup.get_text(" ", strip=True)
            hits, _, _ = keyword_matches(body, kw)
            if hits and "RESULTADOS DA BUSCA" in norm(body):
                rows.append(
                    make_row(
                        "DOE-RN", end, f"Busca DOE-RN: {q}",
                        body, r.url, kw, orgao="DOE-RN"
                    )
                )

        return rows, "OK"

    except requests.RequestException as exc:
        return [], type(exc).__name__


def scrape_doe(start, end, kw, debug=False):
    # Poucas consultas em paralelo. Se o servidor estiver lento, não
    # bloqueamos 44 vezes em sequência.
    rows = []
    s = http_session()

    print("    Consultas-âncora:", ", ".join(DOE_ANCHORS))
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(scrape_doe_one, s, q, start, end, kw, debug): q
            for q in DOE_ANCHORS
        }
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                r, status = fut.result()
                rows.extend(r)
                print(f"    DOE '{q}': {len(r)} resultado(s) [{status}]")
            except Exception as exc:
                print(f"    DOE '{q}': não disponível [{type(exc).__name__}]")

    # Deduplica o que veio de âncoras diferentes.
    unique = {}
    for r in rows:
        unique[r["ID"]] = r
    return list(unique.values())


# ============================================================
# DOM NATAL V3
# ============================================================
def scrape_dom_one(s, q, start, end, kw, debug=False):
    try:
        r = s.get(DOM_SEARCH, timeout=TIMEOUT)
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"

        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form")
        if not form:
            return [], "formulario-nao-encontrado"

        action = urljoin(DOM_SEARCH, form.get("action") or DOM_SEARCH)
        method = (form.get("method") or "get").lower()

        fields = []
        for inp in form.find_all(["input", "textarea", "select"]):
            fields.append({
                "name": inp.get("name") or "",
                "id": inp.get("id") or "",
                "type": (inp.get("type") or "").lower(),
                "placeholder": inp.get("placeholder") or "",
            })

        def choose(terms):
            for f in fields:
                hay = norm(" ".join(
                    [f["name"], f["id"], f["placeholder"]]
                ))
                if any(norm(t) in hay for t in terms):
                    return f["name"] or f["id"]
            return None

        word = choose(["PALAVRA", "BUSCA", "PROCURAR", "CHAVE"])
        dates = [
            choose(["DATA INICIAL", "INICIO", "INICIAL"]),
            choose(["DATA FINAL", "FINAL", "ATE", "ATÉ"]),
        ]
        dates = [x for x in dates if x]

        if not word:
            candidates = [
                f["name"] or f["id"] for f in fields
                if f["type"] in ("text", "search", "")
            ]
            word = candidates[0] if candidates else None

        if not word:
            return [], "campo-palavra-nao-encontrado"

        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            typ = (inp.get("type") or "").lower()
            if name and typ == "hidden":
                data[name] = inp.get("value", "")

        data[word] = q
        if len(dates) >= 2:
            data[dates[0]] = start.strftime("%d/%m/%Y")
            data[dates[1]] = end.strftime("%d/%m/%Y")

        if method == "post":
            rr = s.post(action, data=data, timeout=TIMEOUT)
        else:
            rr = s.get(action, params=data, timeout=TIMEOUT)

        if rr.status_code != 200:
            return [], f"HTTP {rr.status_code}"

        soup2 = BeautifulSoup(rr.text, "html.parser")
        body = soup2.get_text(" ", strip=True)

        if debug:
            (DATA_DIR / f"dom_{norm(q)}.html").write_text(
                rr.text, encoding="utf-8", errors="ignore"
            )

        rows = []
        seen = set()
        for a in soup2.find_all("a", href=True):
            title = " ".join(a.stripped_strings)
            href = urljoin(action, a.get("href"))
            if not title or len(title) < 10:
                continue
            if href in seen:
                continue
            if any(x in norm(title) for x in [
                "PRINCIPAL", "OUVIDORIA", "MAPA DO SITE", "VOLTAR",
                "BUSCA CONTEUDO", "BUSCA DOM",
            ]):
                continue

            parent = a.parent
            text = parent.get_text(" ", strip=True) if parent else title
            hits, _, _ = keyword_matches(f"{title} {text}", kw)
            if hits:
                seen.add(href)
                rows.append(
                    make_row("DOM Natal", end, title, text, href, kw, orgao="DOM NATAL")
                )

        if not rows and "RESULTADOS DA CONSULTA" in norm(body):
            hits, _, _ = keyword_matches(body, kw)
            if hits:
                rows.append(
                    make_row(
                        "DOM Natal", end, f"Busca DOM Natal: {q}",
                        body, rr.url, kw, orgao="DOM NATAL"
                    )
                )

        return rows, "OK"
    except requests.RequestException as exc:
        return [], type(exc).__name__


def scrape_dom(start, end, kw, debug=False):
    # Âncoras para evitar 44 acessos ao portal.
    anchors = ["SAUDE", "SMS", "SUS", "HOSPITAL", "SECRETARIA"]
    s = http_session()
    rows = []

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            ex.submit(scrape_dom_one, s, q, start, end, kw, debug): q
            for q in anchors
        }
        for fut in as_completed(futures):
            q = futures[fut]
            try:
                r, status = fut.result()
                rows.extend(r)
                print(f"    DOM '{q}': {len(r)} resultado(s) [{status}]")
            except Exception as exc:
                print(f"    DOM '{q}': não disponível [{type(exc).__name__}]")

    unique = {}
    for r in rows:
        unique[r["ID"]] = r
    return list(unique.values())


# ============================================================
# EXCEL
# ============================================================
def load_history():
    if not HISTORY_FILE.exists():
        return pd.DataFrame()
    try:
        return pd.read_excel(HISTORY_FILE, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame()


def save_results(rows):
    if not rows:
        return 0

    new = pd.DataFrame(rows).fillna("")
    old = load_history()

    old_ids = set(old["ID"].astype(str)) if not old.empty and "ID" in old else set()

    # Remove duplicidade dentro da própria execução.
    new = new.drop_duplicates(subset=["ID"], keep="first")
    new_only = new[~new["ID"].astype(str).isin(old_ids)].copy()

    all_df = pd.concat([old, new], ignore_index=True) if not old.empty else new.copy()
    all_df = all_df.drop_duplicates(subset=["ID"], keep="last")

    sort_date = pd.to_datetime(all_df["DATA"], dayfirst=True, errors="coerce")
    all_df = (
        all_df.assign(_DATA=sort_date)
        .sort_values(["_DATA", "RELEVÂNCIA", "FONTE"], ascending=[False, True, True])
        .drop(columns=["_DATA"])
    )

    DATA_DIR.mkdir(exist_ok=True)
    RESULT_DIR.mkdir(exist_ok=True)
    all_df.to_excel(HISTORY_FILE, index=False)

    today = datetime.now().strftime("%Y-%m-%d")
    new_only.to_excel(
        RESULT_DIR / f"publicacoes_{today}.xlsx", index=False
    )

    return len(new_only)


# ============================================================
# MAIN
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description="Monitor de Saúde V3 — DOE-RN, DOM Natal e CONASEMS"
    )
    p.add_argument("--dias", type=int, default=1)
    p.add_argument("--inicio")
    p.add_argument("--fim")
    p.add_argument(
        "--fonte",
        choices=["todas", "conasems", "doe", "dom"],
        default="todas"
    )
    p.add_argument("--debug", action="store_true")
    p.add_argument("--ontem", action="store_true", help="Executa somente para o dia anterior, usando America/Sao_Paulo.")
    args = p.parse_args()

    if args.ontem:
        ontem = datetime.now(ZoneInfo("America/Sao_Paulo")).date() - timedelta(days=1)
        start = end = ontem
    elif args.inicio:
        start = parse_date(args.inicio)
        if not start:
            raise SystemExit("Data inicial inválida. Use DD/MM/AAAA.")
    else:
        start = date.today() - timedelta(days=max(args.dias - 1, 0))

    if args.fim:
        end = parse_date(args.fim)
        if not end:
            raise SystemExit("Data final inválida. Use DD/MM/AAAA.")
    else:
        end = date.today()

    if start > end:
        raise SystemExit("Data inicial maior que data final.")

    kw = load_keywords()

    print("=" * 72)
    print(" MONITOR DE SAÚDE — V3")
    print("=" * 72)
    print(f"Período: {start:%d/%m/%Y} até {end:%d/%m/%Y}")
    print(f"Fonte: {args.fonte}")
    print(f"Palavras: {len(kw)}")
    print()

    rows = []

    if args.fonte in ("todas", "conasems"):
        print("[1] CONASEMS — Legislação Diária")
        r = scrape_conasems(start, end, kw, args.debug)
        rows.extend(r)
        print(f"    Total CONASEMS: {len(r)}")
        print()

    if args.fonte in ("todas", "doe"):
        print("[2] DOE-RN — Busca")
        r = scrape_doe(start, end, kw, args.debug)
        rows.extend(r)
        print(f"    Total DOE-RN: {len(r)}")
        print()

    if args.fonte in ("todas", "dom"):
        print("[3] DOM Natal — Busca")
        r = scrape_dom(start, end, kw, args.debug)
        rows.extend(r)
        print(f"    Total DOM Natal: {len(r)}")
        print()

    new = save_results(rows)

    print("=" * 72)
    print(f"Registros encontrados: {len(rows)}")
    print(f"Novos: {new}")
    print(f"Histórico: {HISTORY_FILE}")
    print(f"Resultados: {RESULT_DIR}")
    print("=" * 72)


if __name__ == "__main__":
    main()
