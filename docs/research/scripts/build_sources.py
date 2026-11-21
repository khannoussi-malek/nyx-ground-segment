"""Export the PaperLab workspace PDFs next to the research notes and write docs/research/SOURCES.md.

Needs the PaperLab docker stack up and the paperlab-research skill's plab.py (PLAB_DIR, default
~/.claude/skills/paperlab-research/scripts). Set PAPERLAB_COMPOSE to PaperLab's docker-compose.yml:
    export PAPERLAB_COMPOSE=<path to paperlab's docker-compose.yml>
    export PAPERLAB_MCP_CMD="[\"docker\",\"compose\",\"-f\",\"$PAPERLAB_COMPOSE\",\"exec\",\"-T\",\"api\",\"python\",\"-m\",\"mcp_server\"]"
    python3 docs/research/scripts/build_sources.py
PDFs land in docs/research/papers/ (git-ignored); web documents missing from papers/web/ are downloaded
from their URLs (HTML pages saved as plain text).
"""

import html
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.request
from pathlib import Path

PLAB = Path(os.environ.get("PLAB_DIR", Path.home() / ".claude/skills/paperlab-research/scripts"))
WORKSPACE = "Nyx ground segment design"
REPO = Path(__file__).resolve().parents[3]  # docs/research/scripts/ -> repo root
RES = REPO / "docs/research"
OUT = RES / "papers"
WEB = OUT / "web"
COMPOSE = os.environ.get("PAPERLAB_COMPOSE", "")

# PDFs whose PaperLab metadata lacks author/year/title (read from their first page)
FIX = {
    "0e9984dd": ("Saltzer", 1984, "End-to-end arguments in system design"),
    "a9d506f0": ("Krioukov", 2008, "Parity lost and parity regained"),
    "ad0a58c2": ("Schroeder", 2010, "Understanding latent sector errors and how to protect against them"),
    "e6be5b40": ("Bairavasundaram", 2008, "An analysis of data corruption in the storage stack"),
    "0c7209ce": ("INL", 2024, "Evaluation of color in digital nuclear power plant control room displays"),
    "078a15dc": ("Huang", 2022, "Metastable failures in the wild"),
    "8aeafc87": ("Yuan", 2014, "Simple testing can prevent most critical failures"),
    "8962016f": ("Yuan", 2014, "Simple testing can prevent most critical failures (copy 2)"),
    "13aeb185": ("Alquraan", 2018, "An analysis of network-partitioning failures in cloud systems"),
    "f88d1eef": ("Fuertes", 2016, "Improving spacecraft health monitoring with automatic anomaly detection"),
    "83207756": ("Burrows", 2006, "The Chubby lock service for loosely-coupled distributed systems"),
    "c760e945": ("Schroeder", 2006, "Open versus closed: a cautionary tale"),
    "2d4dbb26": ("Trofimov", 2019, "Delivery, consistency, and determinism: rethinking guarantees in distributed stream processing"),  # arXiv metadata: "Thofimov"
}
SKIP = {"53a76937"}
# PDF stored on another version's record (preprint, reprint, tech report) -> the DOI the notes cite
ALIAS = {
    "c3792ca3": "10.1145/351240.351266",  # QuickCheck: 2011 SIGPLAN reprint of the ICFP 2000 paper
    "74803755": "10.1109/icse-companion55297.2022.9793781",  # Schemathesis: arXiv 2112.10328 preprint
    "311b7334": "10.1109/icde53745.2022.00116",  # Wu & Keogh: TKDE 2021 version of the ICDE 2022 paper
    "0c7209ce": "10.1016/j.apergo.2024.104429",  # INL technical report of the Applied Ergonomics paper
}  # failed scan of Therac-25; the OCR'd copy is attached to its DOI entry

# web documents used by R21/R22: (source file name when fetched, saved name, note, URL)
WEBDOCS = [
    ("dsn208b.pdf", "NASA-DSN-810-005-208B-telemetry-data-decoding.pdf", "R22", "https://deepspace.jpl.nasa.gov/dsndocs/810-005/208/208B.pdf"),
    ("ccsds1301g.pdf", "CCSDS-130.1-G-3-TM-sync-channel-coding-green-book.pdf", "R22", "https://ccsds.org/Pubs/130x1g3e1.pdf"),
    ("ccsds1310b.pdf", "CCSDS-131.0-B-5-TM-sync-channel-coding.pdf", "R22", "https://ccsds.org/Pubs/131x0b5.pdf"),
    ("rfc6298.txt", "RFC6298-computing-TCP-retransmission-timer.txt", "R22", "https://www.rfc-editor.org/rfc/rfc6298"),
    ("lambda-kinesis.txt", "AWS-Lambda-Kinesis-on-failure-bisect.txt", "R21", "https://docs.aws.amazon.com/lambda/latest/dg/kinesis-on-failure-destination.html"),
    ("lambda-sqs.txt", "AWS-Lambda-SQS-partial-batch-response.txt", "R21", "https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html"),
    ("redis-xautoclaim.txt", "Redis-XAUTOCLAIM-command.txt", "R21", "https://redis.io/docs/latest/commands/xautoclaim/"),
    ("kip932.txt", "Kafka-KIP-932-queues-for-kafka.txt", "R21", "https://cwiki.apache.org/confluence/display/KAFKA/KIP-932%3A+Queues+for+Kafka"),
    ("e40c.pdf", "ECSS-E-ST-40C-software-6March2009.pdf", "R25", "https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-40C6March2009.pdf"),
    ("q80c.pdf", "ECSS-Q-ST-80C-Rev1-software-product-assurance-15February2017.pdf", "R25", "https://ecss.nl/wp-content/uploads/2017/04/ECSS-Q-ST-80C-Rev.1(15February2017).pdf"),
    ("e70c.pdf", "ECSS-E-ST-70C-ground-systems-and-operations-31July2008.pdf", "R25", "https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-70C31July2008.pdf"),
    ("npr7150.pdf", "NPR-7150.2D-NASA-software-engineering-requirements.pdf", "R25", "https://nodis3.gsfc.nasa.gov/npg_img/N_PR_7150_002D_/N_PR_7150_002D_.pdf"),
]


def slug(s: str, n: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:n].rstrip("-")


def surname(p: dict) -> str:
    a = p.get("authors") or []
    first = a[0] if isinstance(a, list) and a else (a if isinstance(a, str) else "")
    first = first.get("name", "") if isinstance(first, dict) else str(first)
    first = first.split(",")[0] if "," in first else (first.split()[-1] if first.split() else "")
    return unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode()


def meta(p: dict) -> tuple[str, int | None, str]:
    fx = FIX.get(p["id"][:8])
    return fx if fx else (surname(p) or "Unknown", p.get("year"), p.get("title") or "untitled")


def fetch_papers() -> list[dict]:
    sys.path.insert(0, str(PLAB))
    from plab import Paperlab  # noqa: PLC0415
    pl = Paperlab()
    ps = pl.call("list_papers", workspace=WORKSPACE)
    pl.close()
    ps = ps if isinstance(ps, list) else ps.get("result", ps)
    return [{k: p.get(k) for k in ("id", "doi", "title", "year", "authors", "has_pdf", "evidence_access")} for p in ps]


def fetch_web(url: str, dst: Path) -> None:
    """Download a web document; an HTML page saved as .txt is reduced to its text."""
    req = urllib.request.Request(url, headers={"User-Agent": "nyx-research-sources/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if dst.suffix == ".txt" and b"<html" in data[:2000].lower():
        t = data.decode("utf-8", "replace")
        t = re.sub(r"(?s)<(script|style).*?</\1>", " ", t)
        data = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).encode()
    dst.write_bytes(data)


def main() -> None:
    if not COMPOSE or not Path(COMPOSE).is_file():
        sys.exit("set PAPERLAB_COMPOSE to PaperLab's docker-compose.yml (see the module docstring)")
    papers = fetch_papers()
    OUT.mkdir(exist_ok=True)
    WEB.mkdir(exist_ok=True)
    (OUT / ".gitignore").write_text("# paper PDFs stay local (copyright); the index is docs/research/SOURCES.md\n*\n!.gitignore\n")
    cid = subprocess.run(["docker", "compose", "-f", COMPOSE, "ps", "-q", "api"], capture_output=True, text=True).stdout.strip()
    q = "select id, file_path from papers where file_path is not null"
    db = subprocess.run(["docker", "compose", "-f", COMPOSE, "exec", "-T", "db", "sh", "-c",
                         f'psql -U ${{POSTGRES_USER:-postgres}} -d ${{POSTGRES_DB:-paperlab}} -Atc "{q}"'],
                        capture_output=True, text=True).stdout
    stored = dict(line.split("|", 1) for line in db.splitlines() if "|" in line)
    files: dict[str, str] = {}
    for p in papers:
        if not p["has_pdf"] or p["id"][:8] in SKIP:
            continue
        sur, year, title = meta(p)
        name = f"{sur}{year or ''}-{slug(title)}.pdf"
        dst = OUT / name
        if not dst.exists():
            r = subprocess.run(["docker", "cp", f"{cid}:{stored.get(p['id'], '/data/pdfs/' + p['id'] + '.pdf')}", str(dst)], capture_output=True, text=True)
            if r.returncode:
                print("copy failed", p["id"], r.stderr.strip()[:120])
                continue
        files[p["id"]] = name
    for _, dst, _, url in WEBDOCS:
        if not (WEB / dst).exists():
            try:
                fetch_web(url, WEB / dst)
            except OSError as e:
                print("web fetch failed", url, e)

    notes = sorted(RES.glob("[RS][0-9]*.md"), key=lambda q: (q.name[0], int(re.search(r"\d+", q.name).group())))
    doi_re = re.compile(r"10\.\d{4,9}/[^\s\]|,;>\"'`]+", re.I)
    by_doi = {(p["doi"] or "").lower(): p for p in papers if p["doi"]}
    lines = [
        "# Research sources: which paper or document each note is based on",
        "",
        "Generated 2026-11-19 from the notes and the PaperLab workspace **Nyx ground segment design**. "
        "PDFs are in [`papers/`](papers/) next to the notes; that folder is **git-ignored** (paywalled papers, copyright), "
        "so a fresh clone has only this index: re-export with PaperLab or open the DOI.",
        "",
        "How to read it:",
        "- **Local file**: the PDF the note's page numbers refer to (`papers/…`), or a saved copy of a web document (`papers/web/…`).",
        "- **No PDF**: PaperLab holds only the abstract or metadata. The note marks such claims \"abstract only\", \"metadata\" or STILL UNVERIFIED.",
        "- **Matched by**: `DOI` = the note cites the DOI; `author+year` = the note names the first author and year (check if two papers share both).",
        "- Measurements, probes and simulations are not papers: each note names its source (Nyx files, [scripts/](scripts/README.md), test names). Regenerate this file with `scripts/build_sources.py`.",
        "",
    ]
    totals = {"pdf": 0, "nopdf": 0}
    for n in notes:
        text = n.read_text()
        hits: dict[str, tuple[dict, str]] = {}
        for d in doi_re.findall(text):
            d = d.lower().rstrip(".")
            for key in (d, d + ")" if "(" in d and ")" not in d else d):
                if key in by_doi:
                    hits[by_doi[key]["id"]] = (by_doi[key], "DOI")
            if "(" in d:  # DOIs with parentheses, e.g. 10.1016/s0166-4115(08)62386-9
                for k, p in by_doi.items():
                    if k.startswith(d):
                        hits[p["id"]] = (p, "DOI")
        low = text.lower()
        for p in papers:
            if ALIAS.get(p["id"][:8]) and ALIAS[p["id"][:8]] in low:
                hits[p["id"]] = (p, "DOI (other version)")
        for p in papers:
            if p["id"] in hits or p["id"][:8] in SKIP:
                continue
            sur, year, _ = meta(p)
            if sur and year and len(sur) > 2 and re.search(rf"\b{re.escape(sur)}\b[^\n]{{0,80}}\b{year}\b", text):
                hits[p["id"]] = (p, "author+year")
        web = [w for w in WEBDOCS if w[2] == n.name.split("-")[0]]
        urls = sorted({u.rstrip(").,") for u in re.findall(r"https?://[^\s)\]>`]+", text) if "doi.org" not in u})
        lines += [f"## [{n.stem}]({n.name})", ""]
        if hits:
            lines += ["| Source | DOI | Local file | Matched by |", "| --- | --- | --- | --- |"]
            for p, how in sorted(hits.values(), key=lambda t: (meta(t[0])[0], meta(t[0])[1] or 0)):
                sur, year, title = meta(p)
                f = files.get(p["id"])
                totals["pdf" if f else "nopdf"] += 1
                loc = f"[{f}](papers/{f})" if f else f"no PDF ({p.get('evidence_access') or 'metadata'})"
                doi = f"[{p['doi']}](https://doi.org/{p['doi']})" if p["doi"] else "–"
                lines.append(f"| {sur} {year or ''}, {title[:90]} | {doi} | {loc} | {how} |")
        else:
            lines.append("No papers from the library are cited in this note.")
        if web or urls:
            lines += ["", "Web documents and standards:", ""]
            for _, dst, _, url in web:
                lines.append(f"- [{dst}](papers/web/{dst}) – saved copy of {url}")
            for u in urls:
                if not any(u == w[3] for w in web):
                    lines.append(f"- {u} (link only, no saved copy)")
        lines.append("")
    lines += [
        "## Known gaps (checked 2026-11-19)",
        "",
        "- **Liang et al. 2022** (R9, grayscale vs colour situation map): cited through a Consensus link only; no DOI found on Crossref, not in PaperLab. Treat as unverified.",
        "- **Rothenberg 2009** (R8, R14, flood definition): a book, cited second-hand through Wang et al. 2016 p. 7, which is the source actually read.",
        "- **No-PDF rows above** (abstract or metadata only): claims resting on them are marked in the notes as \"abstract only\", \"metadata\" or STILL UNVERIFIED. Uploading the PDF in PaperLab and re-running the export upgrades them.",
        "- **Standards not held**: ISA-18.2, ISA-101, EEMUA 191, NASA SSP 51700 (paid). Claims citing them are secondary and marked so.",
        "- Measurements, probes and simulations (S1, R15–R22) are reproducible from the Nyx repo and the scripts named in each note; they are not papers and are not listed here.",
        "",
    ]
    (RES / "SOURCES.md").write_text("\n".join(lines))
    print(f"{len(files)} PDFs exported to {OUT}; {len(list(WEB.iterdir()))} web docs; "
          f"note-source rows with PDF {totals['pdf']}, without {totals['nopdf']}")


if __name__ == "__main__":
    main()
