"""Project-scoped local source tools. Never accept filesystem paths or URLs from a model."""
from __future__ import annotations

import base64
from pathlib import Path

from .common import EvaluationError, Blocked, append_jsonl, digest, file_hash, now


class SourceAccess:
    def __init__(self, sources: list[dict], project: str, role: str, log_path: Path,
                 *, search_max=12, pages_max=24, allowed_pages=None, extra_crossref=0,
                 initial_pages=None, session_id="offline"):
        self.sources = {s["source_id"]: s for s in sources if s["project_id"] == project}
        if not self.sources:
            raise EvaluationError("No authorized source")
        for s in self.sources.values():
            if file_hash(Path(s["pdf"])) != s["sha256"]:
                raise EvaluationError("Source hash changed")
        self.role, self.log_path, self.session_id = role, log_path, session_id
        self.search_max, self.pages_max = search_max, pages_max
        self.allowed_pages = set(map(tuple, allowed_pages)) if allowed_pages is not None else None
        self.initial_pages = set(map(tuple, initial_pages or []))
        self.extra_crossref = extra_crossref
        self.crossrefs: set[tuple] = set()
        self.read_pages: set[tuple] = set()
        self.searches = 0
        self.entries: list[dict] = []

    def _source(self, source_id: str) -> dict:
        if source_id not in self.sources:
            raise EvaluationError("Unauthorized source ID; paths/URLs are not tools")
        return self.sources[source_id]

    def _log(self, entry: dict) -> dict:
        entry = {**entry, "role": self.role, "session_id": self.session_id, "time": now()}
        entry["access_id"] = "L-" + digest([entry, len(self.entries)])
        self.entries.append(entry)
        append_jsonl(self.log_path, entry)
        return entry

    def index(self, source_id: str) -> dict:
        import fitz
        source = self._source(source_id)
        with fitz.open(source["pdf"]) as pdf:
            result = {"source_id": source_id, "pages": len(pdf), "toc": pdf.get_toc()}
        self._log({"operation": "index", "source_id": source_id})
        return result

    def search(self, source_id: str, query: str) -> dict:
        import fitz
        source = self._source(source_id)
        if self.allowed_pages is not None or self.searches >= self.search_max:
            raise Blocked("Search not permitted or query budget exhausted")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise EvaluationError("Invalid local text query")
        self.searches += 1
        hits = []
        with fitz.open(source["pdf"]) as pdf:
            for index, page in enumerate(pdf):
                text = page.get_text()
                offset = text.casefold().find(query.casefold())
                if offset >= 0:
                    hits.append({"pdf_page": index + 1, "excerpt": text[max(0, offset-120):offset+len(query)+240]})
        entry = self._log({"operation": "search", "source_id": source_id, "query": query,
                           "hit_pages": [h["pdf_page"] for h in hits]})
        return {"data_is_untrusted": True, "hits": hits, "access_id": entry["access_id"]}

    def page(self, source_id: str, pdf_page: int, *, image=False, dpi=144, bbox=None, crossref=None) -> dict:
        import fitz
        source = self._source(source_id)
        key = (source_id, pdf_page)
        if type(pdf_page) is not int or not 1 <= pdf_page <= source["pages"]:
            raise EvaluationError("Invalid PDF page")
        if self.allowed_pages is not None and key not in self.allowed_pages:
            raise EvaluationError("Behavior cannot expand frozen source pages")
        if dpi not in {144, 216, 288}:
            raise EvaluationError("Unregistered render DPI")
        if key not in self.read_pages and key not in self.initial_pages:
            ordinary = self.read_pages - self.initial_pages - self.crossrefs
            if len(ordinary) >= self.pages_max:
                if not crossref or len(self.crossrefs) >= self.extra_crossref:
                    raise Blocked("Independent page budget exhausted")
                parent = (source_id, crossref.get("from_page"))
                quote = crossref.get("quote", "")
                previous = next((e for e in self.entries if e.get("operation") == "page" and
                                 (e["source_id"], e["pdf_page"]) == parent), None)
                if previous is None or not quote.strip():
                    raise EvaluationError("Cross-page extension requires an accessed originating page and exact reference quote")
                with fitz.open(source["pdf"]) as pdf:
                    if quote not in pdf[parent[1]-1].get_text():
                        raise EvaluationError("Cross reference quote absent from original page")
                self.crossrefs.add(key)
        self.read_pages.add(key)
        with fitz.open(source["pdf"]) as pdf:
            page = pdf[pdf_page - 1]
            entry = {"operation": "page", "source_id": source_id, "source_sha256": source["sha256"],
                     "pdf_page": pdf_page, "dpi": dpi if image else None, "bbox": bbox, "crossref": crossref}
            if image:
                clip = None
                if bbox is not None:
                    if len(bbox) != 4:
                        raise EvaluationError("Invalid region")
                    clip = fitz.Rect(*bbox)
                    if clip.is_empty or not page.rect.contains(clip):
                        raise EvaluationError("Region outside original page")
                png = page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")
                import hashlib
                entry["image_sha256"] = hashlib.sha256(png).hexdigest()
                # Image is returned intact; render failure raises, never falls back to text.
                result = {"image_base64": base64.b64encode(png).decode("ascii"), "mime_type": "image/png"}
            else:
                result = {"text": page.get_text()}
        entry = self._log(entry)
        return {**result, "data_is_untrusted": True, "source_id": source_id, "pdf_page": pdf_page,
                "access_id": entry["access_id"]}

    def invoke(self, name: str, args: dict) -> dict:
        if name == "source_index" and set(args) == {"source_id"}:
            return self.index(**args)
        if name == "source_search" and set(args) == {"source_id", "query"}:
            return self.search(**args)
        if name == "source_page" and set(args) <= {"source_id", "pdf_page", "image", "dpi", "bbox", "crossref"}:
            return self.page(**args)
        raise EvaluationError("Unauthorized tool or argument")
