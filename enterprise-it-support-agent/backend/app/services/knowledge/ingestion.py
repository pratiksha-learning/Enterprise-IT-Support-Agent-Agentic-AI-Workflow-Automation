"""Document sources, text extraction and chunking for the knowledge base."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


@dataclass
class SourceDocument:
    source: str                    # local | sharepoint
    source_uri: str
    title: str
    content: bytes
    extension: str
    allowed_groups: list[str] = field(default_factory=lambda: ["all-employees"])
    sensitivity: str = "General"


@dataclass
class Chunk:
    index: int
    heading: str
    text: str


class DocumentSource(Protocol):
    name: str

    def iter_documents(self) -> Iterable[SourceDocument]: ...


# --------------------------------------------------------------------------- extraction
def parse_front_matter(text: str) -> tuple[dict, str]:
    """Minimal ``---`` front-matter parser (key: value, key: [a, b])."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict = {}
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value.strip("'\"")
    return meta, text[end + 4:].lstrip("\n")


def extract_text(content: bytes, extension: str) -> str:
    ext = extension.lower()
    if ext in (".md", ".txt"):
        return content.decode("utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        try:
            import docx  # type: ignore  (python-docx, optional)
        except ImportError as exc:
            raise ValueError("python-docx is required for .docx ingestion") from exc
        document = docx.Document(io.BytesIO(content))
        return "\n\n".join(p.text for p in document.paragraphs)
    raise ValueError(f"unsupported file type: {extension}")


# --------------------------------------------------------------------------- chunking
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> list[Chunk]:
    """Split by markdown headings first, then pack paragraphs into ~chunk_size windows
    with character overlap so that steps are not cut off from their context."""
    sections: list[tuple[str, str]] = []
    positions = [(m.start(), m.group(2).strip()) for m in _HEADING.finditer(text)]
    if not positions or positions[0][0] > 0:
        positions.insert(0, (0, ""))
    for i, (start, heading) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        body = _HEADING.sub("", text[start:end], count=1 if heading else 0).strip()
        if body:
            sections.append((heading, body))

    chunks: list[Chunk] = []
    for heading, body in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        buf = ""
        for para in paragraphs:
            while len(para) > chunk_size:          # very long paragraph -> hard split
                if buf:
                    chunks.append(Chunk(len(chunks), heading, buf))
                    buf = ""
                chunks.append(Chunk(len(chunks), heading, para[:chunk_size]))
                para = para[chunk_size - overlap:]
            if len(buf) + len(para) + 2 > chunk_size and buf:
                chunks.append(Chunk(len(chunks), heading, buf))
                buf = buf[-overlap:].split(" ", 1)[-1] + "\n\n" + para if overlap else para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            chunks.append(Chunk(len(chunks), heading, buf))
    return chunks


# --------------------------------------------------------------------------- local source
class LocalDirectorySource:
    """Reads documents from ``knowledge-base/sample-documents``. Access metadata comes from
    front matter (``allowed_groups``, ``sensitivity``) - mirroring SharePoint permissions
    and sensitivity labels."""
    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def iter_documents(self) -> Iterable[SourceDocument]:
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            raw = path.read_bytes()
            title, groups, sensitivity = path.stem.replace("-", " ").title(), ["all-employees"], "General"
            if path.suffix.lower() in (".md", ".txt"):
                meta, _ = parse_front_matter(raw.decode("utf-8", errors="replace"))
                title = meta.get("title", title)
                groups = meta.get("allowed_groups", groups) or groups
                sensitivity = meta.get("sensitivity", sensitivity)
            yield SourceDocument(source="local", source_uri=f"kb://{path.relative_to(self.root).as_posix()}",
                                 title=title, content=raw, extension=path.suffix.lower(),
                                 allowed_groups=list(groups), sensitivity=sensitivity)
