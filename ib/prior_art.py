"""외부 선행 자료 검색: 플랫폼 밖에 이미 비슷한 연구가 있는지 찾아 '이미 알려진 것일 수 있음'을 표시한다.

설계 포인트
- '제공자(Provider)' 구조로 만들어서 arXiv, 특허 DB, Semantic Scholar 등을 갈아 끼울 수 있다.
- 기본값은 꺼져 있다(NullProvider). 환경변수 IB_PRIOR_ART=arxiv 로 켠다.
- 외부 검색이 실패해도 글 올리기는 막지 않는다. 결과는 '판정'이 아니라 '참고 링크'일 뿐이다.

한계(솔직히)
- arXiv는 영어 검색이라, 한국어 주장은 영어 키워드(태그 등)가 있어야 제대로 찾는다.
  실서비스에선 LLM으로 주장을 영어 검색어로 바꾸는 단계를 넣어야 한다.
"""
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
STOPWORDS = {"the", "and", "for", "with", "using", "from", "into", "that", "this"}


def english_keywords(text, tags=(), limit=6):
    """주장 본문과 태그에서 영어 키워드만 뽑는다."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", " ".join([text, *tags]))
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw not in STOPWORDS and lw not in seen:
            seen.add(lw)
            out.append(lw)
    return out[:limit]


def parse_arxiv_atom(xml_text, limit=5):
    """arXiv API 응답(Atom XML)을 [{title, url, published, summary}]로 바꾼다."""
    root = ET.fromstring(xml_text)
    items = []
    for entry in root.findall(ATOM + "entry")[:limit]:
        def text(tag):
            el = entry.find(ATOM + tag)
            return " ".join(el.text.split()) if el is not None and el.text else ""
        items.append({
            "source": "arxiv",
            "title": text("title"),
            "url": text("id"),
            "published": text("published")[:10],
            "summary": text("summary")[:240],
        })
    return items


class NullProvider:
    name = "off"

    def search(self, text, tags=()):
        return {"provider": self.name, "items": [], "note": "외부 선행 자료 검색이 꺼져 있어 (IB_PRIOR_ART=arxiv 로 켤 수 있어)"}


class ArxivProvider:
    name = "arxiv"

    def __init__(self, timeout=5, fetch=None):
        self.timeout = timeout
        self._fetch = fetch or self._http_get   # 테스트에선 가짜 fetch를 넣는다

    def _http_get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "intelligence-base-mvp/0.1"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8")

    def search(self, text, tags=()):
        kws = english_keywords(text, tags)
        if len(kws) < 2:
            return {"provider": self.name, "items": [], "note": "영어 키워드가 부족해서 검색하지 않았어 (영어 태그를 달면 찾아볼 수 있어)"}
        query = " AND ".join(f"all:{k}" for k in kws[:4])
        url = ARXIV_API + "?" + urllib.parse.urlencode({"search_query": query, "max_results": 5})
        try:
            return {"provider": self.name, "query": query, "items": parse_arxiv_atom(self._fetch(url))}
        except Exception as e:  # 네트워크 오류 등: 글 올리기를 막지 않는다
            return {"provider": self.name, "items": [], "note": f"외부 검색 실패: {type(e).__name__}"}


def get_provider():
    return ArxivProvider() if os.environ.get("IB_PRIOR_ART") == "arxiv" else NullProvider()
