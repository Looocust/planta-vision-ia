"""Recuperación de SOP/manuales (RAG léxico TF-IDF) con citas doc_id + sección."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from services.agent.config import ROOT
from services.agent.security import looks_like_injection, normalize

SOP_DIR = ROOT / "docs" / "sop"
_H1 = re.compile(r"^#\s+(\S+)\s+—\s+(.*)$")
_H2 = re.compile(r"^##\s+\[([^\]]+)\]\s+(.*)$")


@dataclass
class Section:
    doc_id: str
    doc_title: str
    section_id: str
    title: str
    text: str
    path: str
    trust: str


def parse_docs(sop_dir: Path = SOP_DIR) -> list[Section]:
    out: list[Section] = []
    for p in sorted(sop_dir.glob("*.md")):
        doc_id, doc_title, cur, buf = p.stem, p.stem, None, []
        trust = "untrusted" if p.name.startswith("NOTA") else "official"

        def flush():
            if cur:
                out.append(Section(doc_id, doc_title, cur[0], cur[1], "\n".join(buf).strip(),
                                   str(p.relative_to(ROOT)), trust))

        for line in p.read_text().splitlines():
            if m := _H1.match(line):
                doc_id, doc_title = m.group(1), m.group(2)
            elif m := _H2.match(line):
                flush()
                cur, buf = (m.group(1), m.group(2)), []
            elif cur:
                buf.append(line)
        flush()
    return out


class SopIndex:
    def __init__(self, sections: list[Section]):
        self.sections = sections
        corpus = [normalize(f"{s.doc_title} {s.title} {s.title} {s.text}") for s in sections]
        self.vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)
        self.mat = self.vec.fit_transform(corpus)

    def search(self, query: str, k: int = 4, min_score: float = 0.03) -> list[dict]:
        sims = cosine_similarity(self.vec.transform([normalize(query)]), self.mat)[0]
        order = sims.argsort()[::-1][:k]
        res = []
        for i in order:
            if sims[i] < min_score:
                continue
            s = self.sections[i]
            res.append({
                "citation": f"{s.doc_id} §{s.section_id}",
                "doc_id": s.doc_id, "section_id": s.section_id, "section_title": s.title,
                "source_path": s.path, "trust": s.trust, "score": round(float(sims[i]), 3),
                "text": s.text[:900],
                "possible_prompt_injection": looks_like_injection(s.text),
            })
        return res


@lru_cache
def get_index() -> SopIndex:
    return SopIndex(parse_docs())
