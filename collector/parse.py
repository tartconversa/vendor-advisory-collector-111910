"""网页与机器可读公告解析。

只提取防御维护所需字段：产品标识/别名、组件、受影响区间、明确排除版本、
缓解说明、状态、接续指针与外部编号。解析器不输出也不构造任何利用材料。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser


class AdvisoryPageParser(HTMLParser):
    """提取 /vendor/advisory 与 /mirror/advisory 页面上的关键锚点与段落。"""

    def __init__(self):
        super().__init__()
        self.machine_href: str | None = None
        self.machine_sha256: str | None = None
        self.source_text: str | None = None
        self.status_text: str | None = None
        self._capture: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "machine" in (attrs.get("id") or ""):
            self.machine_href = attrs.get("href")
            self.machine_sha256 = attrs.get("data-sha256")
        if tag == "p" and attrs.get("id") in ("source", "status"):
            self._capture = attrs["id"]
            self._chunks = []

    def handle_data(self, data):
        if self._capture:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self._capture:
            text = "".join(self._chunks).strip()
            if self._capture == "source":
                self.source_text = text
            elif self._capture == "status":
                self.status_text = text
            self._capture = None


@dataclass
class PageInfo:
    kind: str  # vendor | mirror
    advisory_id: str
    page_revision: int | None
    status: str | None
    machine_href: str | None
    machine_sha256: str | None
    source_text: str | None


def parse_advisory_page(html_bytes: bytes, kind: str, advisory_id: str) -> PageInfo:
    text = html_bytes.decode("utf-8", errors="replace")
    parser = AdvisoryPageParser()
    parser.feed(text)
    revision = None
    if parser.source_text:
        # 页面约定文本含“修订 N”
        marker = "修订 "
        if marker in parser.source_text:
            tail = parser.source_text.split(marker, 1)[1].strip()
            num = ""
            for ch in tail:
                if ch.isdigit():
                    num += ch
                else:
                    break
            if num:
                revision = int(num)
    return PageInfo(
        kind=kind,
        advisory_id=advisory_id,
        page_revision=revision,
        status=parser.status_text,
        machine_href=parser.machine_href,
        machine_sha256=parser.machine_sha256,
        source_text=parser.source_text,
    )


@dataclass
class AffectedRange:
    introduced: str | None
    fixed: str | None


@dataclass
class ProductClause:
    """单条公告内绑定到具体产品标识/组件的适用性条款。"""

    product_id: str
    aliases: tuple[str, ...]
    component: str | None
    affected: tuple[AffectedRange, ...]
    excluded: tuple[str, ...]
    mitigation: str | None


@dataclass
class MachineAdvisory:
    publisher: str
    advisory_id: str
    revision: int
    external_ids: tuple[str, ...]
    status: str
    replaced_by: str | None
    references: tuple[str, ...]
    products: tuple[ProductClause, ...]
    raw: dict = field(repr=False, default_factory=dict)


def parse_machine_advisory(raw_bytes: bytes) -> MachineAdvisory:
    doc = json.loads(raw_bytes.decode("utf-8"))
    products = []
    for item in doc.get("products", []):
        ranges = tuple(
            AffectedRange(
                introduced=rng.get("introduced"),
                fixed=rng.get("fixed"),
            )
            for rng in item.get("affected", [])
        )
        products.append(
            ProductClause(
                product_id=item["id"],
                aliases=tuple(item.get("aliases", [])),
                component=item.get("component"),
                affected=ranges,
                excluded=tuple(item.get("excluded", [])),
                mitigation=item.get("mitigation"),
            )
        )
    return MachineAdvisory(
        publisher=doc.get("publisher", ""),
        advisory_id=doc["id"],
        revision=int(doc["revision"]),
        external_ids=tuple(doc.get("external_ids", [])),
        status=doc.get("status", "published"),
        replaced_by=doc.get("replaced_by"),
        references=tuple(doc.get("references", [])),
        products=tuple(products),
        raw=doc,
    )


def parse_index(raw_bytes: bytes) -> dict:
    doc = json.loads(raw_bytes.decode("utf-8"))
    return {
        "index_revision": doc["index_revision"],
        "total": int(doc["total"]),
        "next": bool(doc.get("next")),
        "items": tuple(
            {"id": item["id"], "url": item["url"]} for item in doc.get("items", [])
        ),
    }
