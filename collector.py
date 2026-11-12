import json, re, html, hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse
from pathlib import Path
import requests, feedparser
from bs4 import BeautifulSoup

UA = "InformaFielBot/0.1 (news aggregation; contact: configure-in-repo)"
MEUTIMAO = "https://www.meutimao.com.br/noticias-do-corinthians"
GE_RSS = "https://pox.globo.com/rss/ge/"
OUT = "docs/news.json"
HEADERS = {"User-Agent": UA}

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()

def key(s):
    s = clean(s).lower()
    s = re.sub(r"[^a-z0-9áéíóúâêôãõç ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def similarity(a, b):
    aa, bb = key(a), key(b)
    if not aa or not bb:
        return 0.0
    ta, tb = set(aa.split()), set(bb.split())
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return max(overlap, SequenceMatcher(None, aa, bb).ratio() * 0.85)

def is_corinthians(title, summary="", source="", url=""):
    """
    Strict relevance filter:
    - Meu Timão: only editorial /noticias-do-corinthians/<id>/ URLs.
    - ge: title must mention Corinthians/Timão/Brabas AND URL must be in
      Corinthians' team section. A historical mention in another club's story
      is not enough.
    """
    t = key(title)
    explicit_terms = ["corinthians", "timão", "timao", "brabas", "brabinhas"]
    has_team_term = any(
        re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", t)
        for term in explicit_terms
    )

    parsed = urlparse(url or "")
    path = parsed.path.lower()
    host = parsed.netloc.lower()

    if source == "Meu Timão":
        return (
            host in {"www.meutimao.com.br", "meutimao.com.br"}
            and re.search(r"/noticias-do-corinthians/\d+/", path) is not None
            and bool(t)
        )

    if source == "ge":
        is_ge = host in {"ge.globo.com", "www.ge.globo.com"}
        corinthians_section = any(marker in path for marker in (
            "/times/corinthians/",
            "/times/corinthians-feminino/",
            "/times/corinthians-sub-",
            "/corinthians/"
        ))
        return is_ge and corinthians_section and has_team_term

    return False

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.text

def parse_meutimao():
    soup = BeautifulSoup(get(MEUTIMAO), "html.parser")
    found, seen = [], set()
    # Restrict to editorial news URLs. The /torcida/ forum/user area is never accepted.
    for a in soup.select('a[href*="/noticias-do-corinthians/"]'):
        href = urljoin(MEUTIMAO, a.get("href", ""))
        parsed = urlparse(href)
        if parsed.netloc != "www.meutimao.com.br":
            continue
        if not re.search(r"/noticias-do-corinthians/\d+/", parsed.path):
            continue
        title = clean(a.get_text(" ", strip=True))
        if not title or len(title) < 18 or href in seen:
            continue
        seen.add(href)
        found.append({
            "source": "Meu Timão",
            "title": title,
            "url": href,
            "published": "",
            "summary": "",
            "source_type": "editorial_news"
        })
    # Enrich candidates with metadata only; do not copy article body.
    enriched = []
    for item in found[:35]:
        try:
            article = BeautifulSoup(get(item["url"]), "html.parser")
            desc = article.find("meta", attrs={"name": "description"})
            ogdesc = article.find("meta", attrs={"property": "og:description"})
            date = article.find("meta", attrs={"property": "article:published_time"})
            author = article.find("meta", attrs={"name": "author"})
            item["summary"] = clean((ogdesc or desc).get("content", "")) if (ogdesc or desc) else ""
            item["published"] = clean(date.get("content", "")) if date else ""
            item["author"] = clean(author.get("content", "")) if author else ""
        except Exception as e:
            item["fetch_note"] = "Metadata unavailable"
        enriched.append(item)
    return enriched

def parse_ge():
    feed = feedparser.parse(GE_RSS)
    out = []
    for entry in feed.entries[:150]:
        title = clean(entry.get("title", ""))
        summary = clean(BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True))
        if not is_corinthians(title, summary, "ge", entry.get("link", "")):
            continue
        out.append({
            "source": "ge",
            "title": title,
            "url": entry.get("link", ""),
            "published": entry.get("published", ""),
            "summary": summary[:600],
            "source_type": "rss_editorial"
        })
    return out

def dedupe(items):
    # Group likely same-event headlines across outlets; keep source records.
    groups = []
    for item in sorted(items, key=lambda x: x.get("published", ""), reverse=True):
        match = None
        for group in groups:
            if similarity(item["title"], group["canonical_title"]) >= 0.72:
                match = group
                break
        if match:
            if item["source"] not in [s["source"] for s in match["sources"]]:
                match["sources"].append(item)
            continue
        groups.append({
            "id": hashlib.sha256(key(item["title"]).encode()).hexdigest()[:16],
            "canonical_title": item["title"],
            "sources": [item]
        })
    return groups

def main():
    candidates, errors = [], []
    for name, fn in [("Meu Timão", parse_meutimao), ("ge", parse_ge)]:
        try:
            candidates.extend(fn())
        except Exception as e:
            errors.append({"source": name, "error": str(e)[:240]})
    candidates = [x for x in candidates if is_corinthians(x["title"], x.get("summary", ""), x.get("source", ""), x.get("url", ""))]
    groups = dedupe(candidates)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "candidate_collection_only",
        "notice": "Candidate headlines and source metadata only. No article body is republished or AI-generated.",
        "items": groups[:60],
        "errors": errors
    }
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(candidates)} candidates; {len(groups)} grouped stories; {len(errors)} source errors")

if __name__ == "__main__":
    main()
