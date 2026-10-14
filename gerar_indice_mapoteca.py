#!/usr/bin/env python3
"""
Gera mapoteca-indice.json — índice próprio da Mapoteca (SEMADES/PMCG) para o Chaos.

O que faz:
  1. Lê todas as mídias PDF do portal da SEMADES/SEMADUR (API pública do WordPress).
  2. Separa só as pranchas de loteamento (descarta TR, EIA/RIMA, modelos, leis etc.).
  3. Normaliza o nome do loteamento (ITAMARACA – JD. folha 03  ->  chave ITAMARACA).
  4. Junta duplicatas e fica com a versão mais recente de cada folha.
  5. Abre cada PDF e extrai as ruas, as quadras rotuladas e a data "ATUALIZADA EM".

Uso:
  python gerar_indice_mapoteca.py                 # coleta completa (rede)
  python gerar_indice_mapoteca.py --sem-pdf       # só a lista, sem abrir os PDFs
  python gerar_indice_mapoteca.py --seed lista.txt   # monta a partir de "titulo | url" (offline)

Requer: pip install pypdf
"""
import argparse, io, json, re, sys, time, unicodedata, urllib.request, urllib.parse
from datetime import datetime, timezone

SITES = ["https://www.campogrande.ms.gov.br/semades", "https://www.campogrande.ms.gov.br/semadur"]
SAIDA = "mapoteca-indice.json"
UA = {"User-Agent": "Chaos-Mapoteca/1.0 (indice interno de escritorio)"}

# Palavras que indicam tipo de parcelamento — saem da chave
STOP = set("""JD JARDIM BAIRRO B PQ PARQUE RES RESID RESIDENCIAL CONJ CONJUNTO NUC NUCLEO HABIT
HABITACIONAL V VL VILA DO DOS DA DAS DE E LOTEAMENTO LOT CEL CORONEL MODEL ZIP PDF
FOLHA FL SETOR""".split())
GLUED = ["LOTEAMENTO", "BAIRRO", "JARDIM", "RESID", "CONJ", "NUC", "HABIT", "PQ", "RES", "JD", "VL", "B", "V"]
EXCLUI = re.compile(r"\b(TR|TERMO|EIA|RIMA|MODELO|DECLARA|ANEXO|LEI|DECRETO|EDITAL|RELAT|MEMORIAL|"
                    r"REQUERIMENTO|PORTARIA|RESOLU|INSTRU|FORMUL|CHECK|LISTA|CARTILHA|MANUAL|LICEN|"
                    r"PARECER|ATA|AUDIENCIA|EXTRATO|CONTRATO|LFS|RVLFS|HEDGE)\b", re.I)
INCLUI = re.compile(r"\b(FOLHA|FL|JD|JARDIM|BAIRRO|VILA|VL|PARQUE|PQ|CONJ|RESID|RES|LOTEAMENTO|NUC|"
                    r"CHACARA|PORTAL|CIDADE|BL|BLOCO|V|B)\b", re.I)


def sem_acento(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().upper()


def chave(titulo):
    s = sem_acento(titulo)
    s = s.replace("&#8211;", " ").replace("&#8217;", " ")
    s = re.sub(r"PREFCG\s*\d+", " ", s)
    s = re.sub(r"FOLHA\s*\d+([_\-/ ]?\d+)?|FL\s*\d+", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    pal = [w for w in s.split() if w not in STOP and not re.fullmatch(r"\d{9,}", w)]
    return "".join(pal)


def chave_colada(titulo):
    """Títulos tipo 'noroestebairrojdfolha04-prefcg-1671540168'."""
    s = sem_acento(titulo)
    s = re.sub(r"PREFCG.*$", "", s)
    s = re.sub(r"FOLHA.*$", "", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    for _ in range(3):
        for g in GLUED:
            if s.endswith(g) and len(s) > len(g) + 3:
                s = s[: -len(g)]
        for p in ("DOS", "DAS", "DO", "DA"):
            if s.startswith(p) and len(s) > len(p) + 3:
                s = s[len(p):]
    return s


def num_folha(titulo, url):
    for txt in (titulo, urllib.parse.unquote(url.rsplit("/", 1)[-1])):
        m = re.search(r"folha[\s\-_]*0?(\d+)(?:[\s_\-/]+0?(\d+))?", txt, re.I)
        if m:
            return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    return 1, None


def nome_limpo(titulo):
    s = titulo.replace("&#8211;", "–").replace("&#8217;", "'")
    s = re.sub(r"[\s–_\-]*folha.*$", "", s, flags=re.I).strip(" –-_.")
    return s


def ano_upload(url):
    m = re.search(r"/sites/\d+/(\d{4})/(\d{2})/", url)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def get_json(url, tentativas=3):
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8")), r.headers
        except Exception as e:
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))


def coletar_midias():
    itens = []
    for site in SITES:
        base = f"{site}/wp-json/wp/v2/media?mime_type=application/pdf&per_page=100&_fields=id,title,source_url,date"
        try:
            dados, h = get_json(base + "&page=1")
        except Exception as e:
            print(f"[aviso] {site}: {e}", file=sys.stderr)
            continue
        paginas = int(h.get("X-WP-TotalPages") or 1)
        itens += dados
        for p in range(2, paginas + 1):
            try:
                d, _ = get_json(base + f"&page={p}")
                itens += d
            except Exception as e:
                print(f"[aviso] {site} pág {p}: {e}", file=sys.stderr)
        print(f"{site}: {paginas} páginas", file=sys.stderr)
    return [{"titulo": (i.get("title") or {}).get("rendered", ""), "url": i.get("source_url", "")} for i in itens]


def eh_prancha(titulo, url):
    t = titulo.replace("&#8211;", " ")
    if EXCLUI.search(t):
        return False
    if "/sites/24/2017/01/" in url:  # carga original da Mapoteca
        return True
    return bool(INCLUI.search(t)) or "folha" in t.lower()


# ---------- leitura do PDF ----------
RUA_RX = re.compile(r"\b(R\.?|RUA|AV\.?|AVENIDA|TV\.?|TRAVESSA|AL\.?|ALAMEDA|ROD\.?|RODOVIA|ESTR\.?|ESTRADA)\s+"
                    r"([A-ZÀ-Ú0-9][A-ZÀ-Ú0-9\.\s'ºª]{2,45}?)(?=\s{2,}|\n|$|\s\d|\s(?:R|RUA|AV|AVENIDA)\b)")
QD_RX = re.compile(r"\b(?:QUADRA|QD\.?)\s*[-:]?\s*([A-Z]?\d{1,4}[A-Z]?|[A-Z]{1,2})\b")
ATU_RX = re.compile(r"ATUALIZAD[AO]\s+EM\s*:?\s*(\d{2}/\d{2}/\d{4})")


def norm_rua(s):
    s = " ".join(sem_acento(s).replace(".", " ").split())
    s = re.sub(r"^(R|RUA|AV|AVENIDA|TV|TRAVESSA|AL|ALAMEDA|ROD|RODOVIA|ESTR|ESTRADA)\.?\s+", "", s)
    s = s.replace("VISC ", "VISCONDE ").replace("GUINTER", "GUNTER").replace("CEL ", "CORONEL ")
    s = re.sub(r"\b(DR|DRA|PROF|PROFA|PE|PADRE|ENG|GAL|GEN|DEP|VER|SEN|DES|MAL|CAP|TEN|SGT)\b\.?", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in ("DE", "DA", "DO", "DAS", "DOS", "E"))


def ler_pdf(url):
    from pypdf import PdfReader
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        dados = r.read()
    txt = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dados)).pages)
    up = sem_acento(txt)
    ruas = sorted({norm_rua(m.group(0)) for m in RUA_RX.finditer(up) if len(norm_rua(m.group(0))) > 3})
    quadras = sorted({m.group(1) for m in QD_RX.finditer(up)})
    atu = ATU_RX.search(up)
    return {"ruas": ruas, "quadras": quadras, "atualizada": atu.group(1) if atu else "", "temTexto": len(txt.strip()) > 200}


def data_ord(f):
    if f.get("atualizada"):
        d, m, a = f["atualizada"].split("/")
        return f"{a}-{m}-{d}"
    return f.get("upload", "")


def montar(midias, abrir_pdf=True, anterior=None):
    anterior = anterior or {}
    cache = {}
    for lot in anterior.get("loteamentos", {}).values():
        for f in lot.get("folhas", []):
            cache[f["url"]] = f
    lots = {}
    coladas = []
    for m in midias:
        t, u = m["titulo"], m["url"]
        if not u.lower().endswith(".pdf") or not eh_prancha(t, u):
            continue
        if " " not in t.strip() and "-" in t:  # título colado (slug)
            coladas.append(m)
            continue
        k = chave(t)
        if len(k) < 3:
            continue
        fo, de = num_folha(t, u)
        lots.setdefault(k, {"nome": nome_limpo(t), "folhas": {}})
        lots[k]["folhas"].setdefault(fo, []).append({"folha": fo, "de": de, "url": u, "upload": ano_upload(u)})
    chaves = sorted(lots, key=len, reverse=True)
    for m in coladas:
        c = chave_colada(m["titulo"])
        k = next((k for k in chaves if c == k or c.startswith(k) or k.startswith(c)), None)
        if not k:
            continue
        fo, de = num_folha(m["titulo"], m["url"])
        lots[k]["folhas"].setdefault(fo, []).append({"folha": fo, "de": de, "url": m["url"], "upload": ano_upload(m["url"])})

    total = sum(len(v) for l in lots.values() for v in l["folhas"].values())
    feitos = 0
    for k, l in lots.items():
        for fo, versoes in l["folhas"].items():
            for v in versoes:
                feitos += 1
                if v["url"] in cache and "ruas" in cache[v["url"]]:
                    v.update({x: cache[v["url"]].get(x) for x in ("ruas", "quadras", "atualizada", "temTexto")})
                elif abrir_pdf:
                    try:
                        v.update(ler_pdf(v["url"]))
                    except Exception as e:
                        v["erro"] = str(e)[:120]
                    if feitos % 20 == 0:
                        print(f"  PDFs lidos: {feitos}/{total}", file=sys.stderr)
                    time.sleep(0.4)
    saida = {}
    dup = 0
    for k, l in sorted(lots.items()):
        folhas = []
        for fo, versoes in sorted(l["folhas"].items()):
            versoes.sort(key=data_ord, reverse=True)
            dup += len(versoes) - 1
            melhor = dict(versoes[0])
            melhor["versoes"] = len(versoes)
            folhas.append(melhor)
        saida[k] = {"nome": l["nome"], "folhas": folhas}
    return {
        "geradoEm": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "fonte": "Mapoteca SEMADES/SEMADUR — Prefeitura de Campo Grande (mídias públicas do portal)",
        "totais": {"loteamentos": len(saida), "folhas": sum(len(v["folhas"]) for v in saida.values()), "duplicatasDescartadas": dup},
        "loteamentos": saida,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", help="arquivo 'titulo | url' por linha (modo offline)")
    ap.add_argument("--sem-pdf", action="store_true")
    ap.add_argument("--saida", default=SAIDA)
    a = ap.parse_args()
    try:
        anterior = json.load(open(a.saida, encoding="utf-8"))
    except Exception:
        anterior = None
    if a.seed:
        midias = []
        for linha in open(a.seed, encoding="utf-8"):
            if "|" in linha:
                t, u = linha.rsplit("|", 1)
                midias.append({"titulo": t.strip(), "url": u.strip()})
    else:
        midias = coletar_midias()
        if not midias:
            sys.exit("Portal sem resposta — índice anterior mantido.")
    idx = montar(midias, abrir_pdf=not a.sem_pdf and not a.seed, anterior=anterior)
    if anterior and idx["totais"]["folhas"] < 0.5 * anterior.get("totais", {}).get("folhas", 0):
        sys.exit("Coleta veio muito menor que a anterior (portal instável?) — índice anterior mantido.")
    json.dump(idx, open(a.saida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(idx["totais"], ensure_ascii=False))


if __name__ == "__main__":
    main()
