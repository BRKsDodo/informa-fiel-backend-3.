import json
import re
import html
import hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

import requests
import feedparser
from bs4 import BeautifulSoup


UA = "InformaFielBot/0.2 (news aggregation; contact: configure-in-repo)"
MEUTIMAO = "https://www.meutimao.com.br/noticias-do-corinthians"
GE_RSS = "https://pox.globo.com/rss/ge/"
OUT = "docs/news.json"
HEADERS = {"User-Agent": UA}
TIMEOUT = 20
MAX_MEUTIMAO_ITEMS = 35
MAX_GE_ITEMS = 150
MAX_OUTPUT_ITEMS = 60

STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do",
    "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com",
    "e", "que", "se", "ao", "aos", "à", "às", "após", "sobre", "entre",
    "pelo", "pela", "pelos", "pelas", "como", "mais", "menos", "seu",
    "sua", "seus", "suas", " nesta", "nesta", "neste", "nesta", "já",
    "durante", "segundo", " após"
}


def clean(value):
    """Normalize text without extracting or reproducing article body content."""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def key(value):
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9áéíóúâêôãõç ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def content_tokens(value):
    return {
        token for token in key(value).split()
        if len(token) > 2 and token not in STOPWORDS
    }


def similarity(a, b):
    aa, bb = key(a), key(b)
    if not aa or not bb:
        return 0.0

    ta, tb = content_tokens(aa), content_tokens(bb)
    if not ta or not tb:
        return SequenceMatcher(None, aa, bb).ratio()

    intersection = ta & tb
    union = ta | tb
    jaccard = len(intersection) / max(1, len(union))
    sequence = SequenceMatcher(None, aa, bb).ratio()

    # A lower token-overlap threshold catches differently worded reports
    # of the same event, while requiring at least four shared content words
    # avoids grouping stories that merely mention the same club.
    if len(intersection) >= 4 and jaccard >= 0.45:
        return max(jaccard, sequence)

    return sequence * 0.85


def is_corinthians(title, summary="", source="", url=""):
    """Accept only editorial Corinthians coverage from the selected outlets."""
    title_text = key(title)
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if not title_text:
        return False

    if source == "Meu Timão":
        return (
            host in {"www.meutimao.com.br", "meutimao.com.br"}
            and re.search(r"/noticias-do-corinthians/\d+/", path) is not None
        )

    if source == "ge":
        is_ge = host in {"ge.globo.com", "www.ge.globo.com"}
        corinthians_section = any(marker in path for marker in (
            "/times/corinthians/",
            "/times/corinthians-feminino/",
            "/times/corinthians-sub-",
            "/corinthians/"
        ))
        # The club-specific editorial section is the decisive signal.
        # Do not require the word Corinthians in every headline: valid
        # team-section headlines may mention only a player or competition.
        return is_ge and corinthians_section

    return False


def get(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.text


def extract_metadata(url):
    """
    Read only page metadata (description, publication date and author).
    Never extract the article body. This avoids contaminated RSS summaries.
    """
    soup = BeautifulSoup(get(url), "html.parser")

    def meta_content(*selectors):
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                value = clean(tag.get("content", ""))
                if value:
                    return value
        return ""

    description = meta_content(
        'meta[property="og:description"]',
        'meta[name="description"]'
    )
    published = meta_content(
        'meta[property="article:published_time"]',
        'meta[property="og:article:published_time"]'
    )
    author = meta_content(
        'meta[name="author"]',
        'meta[property="article:author"]'
    )
    return {
        "summary": description,
        "published": published,
        "author": author
    }


def summary_matches_title(title, summary):
    """Reject a description that appears unrelated to its own headline."""
    if not summary:
        return False

    title_tokens = content_tokens(title)
    summary_tokens = content_tokens(summary)
    if not title_tokens or not summary_tokens:
        return True

    shared = title_tokens & summary_tokens
    # A short description can legitimately share only a few words.
    # Requiring two shared content terms catches obvious cross-story pollution.
    return len(shared) >= 2


def parse_meutimao():
    soup = BeautifulSoup(get(MEUTIMAO), "html.parser")
    found = []
    seen = set()

    # Editorial news only. Forum/user content under /torcida/ is excluded.
    for anchor in soup.select('a[href*="/noticias-do-corinthians/"]'):
        href = urljoin(MEUTIMAO, anchor.get("href", ""))
        parsed = urlparse(href)

        if parsed.netloc.lower() not in {
            "www.meutimao.com.br", "meutimao.com.br"
        }:
            continue

        if not re.search(r"/noticias-do-corinthians/\d+/", parsed.path):
            continue

        href = parsed._replace(query="", fragment="").geturl()
        title = clean(anchor.get_text(" ", strip=True))

        if not title or len(title) < 18 or href in seen:
            continue

        seen.add(href)
        found.append({
            "source": "Meu Timão",
            "title": title,
            "url": href,
            "published": "",
            "summary": "",
            "source_type": "editorial_news",
            "author": ""
        })

    enriched = []
    for item in found[:MAX_MEUTIMAO_ITEMS]:
        try:
            metadata = extract_metadata(item["url"])
            item.update(metadata)
            if not summary_matches_title(item["title"], item["summary"]):
                item["summary"] = ""
                item["summary_note"] = "Description did not match headline; omitted"
        except Exception:
            item["fetch_note"] = "Metadata unavailable"

        enriched.append(item)

    return enriched


def parse_ge():
    feed = feedparser.parse(GE_RSS)
    out = []

    for entry in feed.entries[:MAX_GE_ITEMS]:
        title = clean(entry.get("title", ""))
        url = clean(entry.get("link", ""))

        if not is_corinthians(title, "", "ge", url):
            continue

        item = {
            "source": "ge",
            "title": title,
            "url": url,
            "published": clean(entry.get("published", "")),
            # Do not use entry.summary: it can contain unrelated headlines
            # or mixed teaser text from the general ge RSS feed.
            "summary": "",
            "source_type": "rss_editorial"
        }

        try:
            metadata = extract_metadata(url)
            item["summary"] = metadata["summary"]
            if metadata["published"]:
                item["published"] = metadata["published"]
            if metadata["author"]:
                item["author"] = metadata["author"]

            if not summary_matches_title(title, item["summary"]):
                item["summary"] = ""
                item["summary_note"] = "Description did not match headline; omitted"
        except Exception:
            item["fetch_note"] = "Page metadata unavailable"

        out.append(item)

    return out


def same_event_across_outlets(item, group):
    """Only merge likely same-event stories from different publishers."""
    existing_sources = {source.get("source") for source in group["sources"]}
    if item.get("source") in existing_sources:
        return False

    a = item.get("title", "")
    b = group.get("canonical_title", "")
    ta, tb = content_tokens(a), content_tokens(b)
    shared = ta & tb
    union = ta | tb
    jaccard = len(shared) / max(1, len(union))
    sequence = SequenceMatcher(None, key(a), key(b)).ratio()

    # Merge when several specific terms point to the same event, or when
    # the headlines are almost identical. This catches the salary-payment
    # pair without merging a separate story about staff salaries.
    return (len(shared) >= 4 and jaccard >= 0.45) or sequence >= 0.72


def dedupe(items):
    """
    Group likely same-event headlines across different outlets.
    Keep every source record and its credit in the grouped story.
    Same-outlet stories are not merged just because they share broad keywords.
    """
    groups = []

    ordered = sorted(
        items,
        key=lambda item: item.get("published", ""),
        reverse=True
    )

    for item in ordered:
        item_url = item.get("url", "")
        match = None

        # Exact URL duplicates are always the same source record.
        for group in groups:
            if any(source.get("url") == item_url for source in group["sources"]):
                match = group
                break

        if match:
            continue

        for group in groups:
            if same_event_across_outlets(item, group):
                match = group
                break

        if match:
            if not any(source.get("url") == item_url for source in match["sources"]):
                match["sources"].append(item)
            continue

        groups.append({
            "id": hashlib.sha256(key(item["title"]).encode("utf-8")).hexdigest()[:16],
            "canonical_title": item["title"],
            "sources": [item]
        })

    return groups


def main():
    candidates = []
    errors = []

    for name, parser in (
        ("Meu Timão", parse_meutimao),
        ("ge", parse_ge)
    ):
        try:
            candidates.extend(parser())
        except Exception as error:
            errors.append({
                "source": name,
                "error": str(error)[:240]
            })

    candidates = [
        item for item in candidates
        if is_corinthians(
            item.get("title", ""),
            item.get("summary", ""),
            item.get("source", ""),
            item.get("url", "")
        )
    ]

    groups = dedupe(candidates)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "candidate_collection_only",
        "notice": (
            "Candidate headlines and source metadata only. "
            "No article body is republished or AI-generated."
        ),
        "items": groups[:MAX_OUTPUT_ITEMS],
        "errors": errors
    }

    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"{len(candidates)} candidates; "
        f"{len(groups)} grouped stories; "
        f"{len(errors)} source errors"
    )


if __name__ == "__main__":
    main()
