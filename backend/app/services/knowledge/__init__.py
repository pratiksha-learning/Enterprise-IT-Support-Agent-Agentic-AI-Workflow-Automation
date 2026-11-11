"""Knowledge Assistant: ingestion pipeline + permission-trimmed RAG with citations."""
from __future__ import annotations

import hashlib
import logging
import re

import numpy as np
from sqlalchemy.orm import Session

from app.integrations.llm import AnswerGenerator, Embedder, RetrievedPassage
from app.models.orm import KnowledgeChunk, KnowledgeDocument
from app.models.schemas import Citation, IngestResult, KnowledgeAnswer
from app.observability.telemetry import metrics_registry, traced
from app.repositories import KnowledgeRepository
from app.security.principal import Principal
from app.security.prompt_guard import sanitize_retrieved_text
from app.services.knowledge.ingestion import DocumentSource, chunk_text, extract_text, parse_front_matter

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = (
    "I couldn't find this in the IT knowledge sources you have access to, so I won't guess. "
    "I can create an IT support ticket so a service desk analyst can help - just say \"create a ticket\"."
)


def can_read(principal: Principal, doc: KnowledgeDocument) -> bool:
    """Security trimming: the user must share at least one group with the document ACL."""
    return bool(set(doc.allowed_groups or []) & set(principal.groups))


class KnowledgeService:
    def __init__(self, db: Session, embedder: Embedder, generator: AnswerGenerator, *, top_k: int = 4,
                 min_score: float = 0.22, chunk_size: int = 700, chunk_overlap: int = 120):
        self.repo = KnowledgeRepository(db)
        self.embedder = embedder
        self.generator = generator
        self.top_k = top_k
        self.min_score = min_score
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------ ingestion
    def ingest(self, source: DocumentSource, prune: bool = True) -> IngestResult:
        ingested = skipped = chunks_created = 0
        flagged: list[str] = []
        seen: set[str] = set()
        with traced("knowledge.ingest", source=source.name):
            for doc in source.iter_documents():
                seen.add(doc.source_uri)
                digest = hashlib.sha256(doc.content + "|".join(doc.allowed_groups).encode()).hexdigest()
                existing = self.repo.get_by_uri(doc.source_uri)
                if existing and existing.content_hash == digest:
                    skipped += 1
                    continue
                if existing:
                    self.repo.delete(existing)
                try:
                    text = extract_text(doc.content, doc.extension)
                except ValueError as exc:
                    logger.warning("document skipped", extra={"uri": doc.source_uri, "reason": str(exc)})
                    skipped += 1
                    continue
                _, body = parse_front_matter(text)
                pieces = chunk_text(body, self.chunk_size, self.chunk_overlap)
                if not pieces:
                    skipped += 1
                    continue
                clean_texts, flags = [], 0
                for piece in pieces:
                    clean, removed = sanitize_retrieved_text(piece.text)
                    flags += removed
                    clean_texts.append(f"{doc.title} {piece.heading} {clean}")
                vectors = self.embedder.embed(clean_texts)
                record = KnowledgeDocument(source=doc.source, source_uri=doc.source_uri, title=doc.title,
                                           allowed_groups=doc.allowed_groups, sensitivity=doc.sensitivity,
                                           content_hash=digest, injection_flags=flags)
                record.chunks = [KnowledgeChunk(chunk_index=p.index, heading=p.heading[:250], text=p.text,
                                                embedding=vectors[i].astype(np.float32).tobytes())
                                 for i, p in enumerate(pieces)]
                self.repo.add(record)
                ingested += 1
                chunks_created += len(pieces)
                if flags:
                    flagged.append(doc.title)
                    logger.warning("document contains instruction-like content (neutralised)",
                                   extra={"uri": doc.source_uri, "flags": flags})
            if prune:
                for existing in self.repo.documents():
                    if existing.source == source.name and existing.source_uri not in seen:
                        self.repo.delete(existing)
        return IngestResult(documents_ingested=ingested, documents_skipped=skipped, chunks_created=chunks_created,
                            flagged_documents=flagged)

    # ------------------------------------------------------------------ retrieval
    def visible_documents(self, principal: Principal) -> list[KnowledgeDocument]:
        return [d for d in self.repo.documents() if can_read(principal, d)]

    def retrieve(self, principal: Principal, question: str, top_k: int | None = None):
        docs = {d.id: d for d in self.visible_documents(principal)}   # trimming happens BEFORE scoring
        chunks = self.repo.chunks_for(list(docs))
        if not chunks:
            return []
        q = self.embedder.embed([question])[0]
        matrix = np.vstack([np.frombuffer(c.embedding, dtype=np.float32) for c in chunks])
        scores = matrix @ q
        order = np.argsort(-scores)
        results, per_doc = [], {}
        for i in order:
            score = float(scores[i])
            if score < self.min_score or len(results) >= (top_k or self.top_k):
                break
            chunk = chunks[int(i)]
            if per_doc.get(chunk.document_id, 0) >= 2:
                continue
            per_doc[chunk.document_id] = per_doc.get(chunk.document_id, 0) + 1
            results.append((chunk, docs[chunk.document_id], score))
        return results

    def answer(self, principal: Principal, question: str, top_k: int | None = None) -> KnowledgeAnswer:
        with traced("knowledge.answer", provider=self.generator.name) as span:
            hits = self.retrieve(principal, question, top_k)
            passages, citations, blocked = [], [], 0
            for chunk, doc, score in hits:
                clean, removed = sanitize_retrieved_text(chunk.text)
                blocked += removed
                if not clean:
                    continue
                idx = len(passages) + 1
                passages.append(RetrievedPassage(idx, doc.title, chunk.heading, clean, score))
                citations.append(Citation(index=idx, document_id=doc.id, title=doc.title, source_uri=doc.source_uri,
                                          heading=chunk.heading, score=round(score, 3), snippet=clean[:240]))
            text = self.generator.generate(question, passages) if passages else None
            span.set_attribute("knowledge.hits", len(passages))
            span.set_attribute("knowledge.blocked_sentences", blocked)
            if not text:
                metrics_registry.inc("knowledge_queries", outcome="fallback")
                return KnowledgeAnswer(answer=FALLBACK_ANSWER, grounded=False, fallback=True, citations=[],
                                       blocked_content_count=blocked, provider=self.generator.name)
            used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
            citations = [c for c in citations if c.index in used] or citations
            metrics_registry.inc("knowledge_queries", outcome="grounded")
            return KnowledgeAnswer(answer=text, grounded=True, fallback=False, citations=citations,
                                   blocked_content_count=blocked, provider=self.generator.name)
