"""持久化：公告原件、观察时刻、来源地址与适用关系依据。

设计原则：
- 原件（网页/机器 JSON）按内容哈希存档，永不就地覆盖；镜像旧修订与更新原文各自独立；
- 网页与其引用的机器文件分别记录修订，修订不一致时保留两条来源记录，不拼接；
- 适用关系绑定到具体公告修订与产品条款，并保留判断依据；
- 组件被明确排除或公告撤销/接续时撤掉对应关联，但同篇公告其他条款仍有效；
- 资产升级后按新资产版本重新核对，旧结论不沿用；
- “未抓到公告”只产生待办，绝不写成资产安全结论。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    content_type TEXT,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    source_kind TEXT NOT NULL,          -- vendor_page | mirror_page | machine | index
    status_code INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    run_id TEXT,
    FOREIGN KEY(sha256) REFERENCES artifacts(sha256)
);
CREATE TABLE IF NOT EXISTS advisories (
    advisory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    publisher TEXT,
    status TEXT NOT NULL,               -- published | withdrawn | replaced
    replaced_by TEXT,
    external_ids TEXT NOT NULL,         -- JSON 数组；共享外部编号不等于同一公告
    ref_list TEXT NOT NULL,             -- JSON 数组（references 为 SQL 保留字）
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY(advisory_id, revision)
);
CREATE TABLE IF NOT EXISTS page_documents (
    advisory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_kind TEXT NOT NULL,          -- vendor | mirror
    page_status TEXT,
    machine_href TEXT,
    machine_sha256_expected TEXT,
    artifact_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(advisory_id, revision, source_kind)
);
CREATE TABLE IF NOT EXISTS machine_documents (
    advisory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    digest_matches_page INTEGER,        -- 1 一致 / 0 网页与机器修订不一致 / NULL 无网页锚点
    content_revision INTEGER,           -- 机器内容自报修订；不一致时可能与网页锚定 revision 不同
    raw_json TEXT NOT NULL,
    PRIMARY KEY(advisory_id, revision)
);
CREATE TABLE IF NOT EXISTS applicability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    advisory_id TEXT NOT NULL,
    advisory_revision INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    aliases TEXT NOT NULL,
    component TEXT,
    introduced TEXT,
    fixed TEXT,
    excluded TEXT NOT NULL,             -- JSON 数组
    mitigation TEXT,
    active INTEGER NOT NULL DEFAULT 1,  -- 排除/撤销/接续后撤掉（0），其余条款不受影响
    inactive_reason TEXT,
    UNIQUE(advisory_id, advisory_revision, product_id, component)
);
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    package TEXT NOT NULL,
    version TEXT NOT NULL,
    components TEXT NOT NULL,           -- JSON 对象
    asset_revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL,
    advisory_id TEXT NOT NULL,
    advisory_revision INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    component TEXT,
    matched_version TEXT NOT NULL,
    verdict TEXT NOT NULL,              -- affected | excluded | not_affected | unknown
    basis TEXT NOT NULL,                -- 判断依据（区间/排除/缺数据）
    applicability_id INTEGER,
    evaluated_at TEXT NOT NULL,
    UNIQUE(asset_id, advisory_id, advisory_revision, product_id, component)
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- unreferenced_fetch | fetch_failed | withdrawn | replaced | digest_mismatch | redirect_blocked | coverage_gap
    subject TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(kind, subject)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    index_revision INTEGER,
    indexed_count INTEGER,
    fetched_count INTEGER DEFAULT 0,
    notes TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- 原件与观察 ----------
    def save_artifact(self, content: bytes, content_type: str | None) -> str:
        import hashlib
        sha = hashlib.sha256(content).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO artifacts(sha256,content,content_type,first_seen) VALUES(?,?,?,?)",
            (sha, content, content_type, now_iso()),
        )
        return sha

    def record_observation(self, url, source_kind, status_code, sha, run_id):
        cur = self.conn.execute(
            "INSERT INTO observations(url,source_kind,status_code,sha256,observed_at,run_id) VALUES(?,?,?,?,?,?)",
            (url, source_kind, status_code, sha, now_iso(), run_id),
        )
        return cur.lastrowid

    def artifact_exists(self, sha: str) -> bool:
        return self.conn.execute("SELECT 1 FROM artifacts WHERE sha256=?", (sha,)).fetchone() is not None

    def get_artifact(self, sha: str) -> bytes | None:
        row = self.conn.execute("SELECT content FROM artifacts WHERE sha256=?", (sha,)).fetchone()
        return row["content"] if row else None

    # ---------- 运行与待办 ----------
    def start_run(self, run_id: str):
        self.conn.execute(
            "INSERT OR IGNORE INTO runs(run_id,started_at) VALUES(?,?)", (run_id, now_iso())
        )

    def finish_run(self, run_id: str, index_revision, indexed_count, fetched_count, notes):
        self.conn.execute(
            "UPDATE runs SET finished_at=?,index_revision=?,indexed_count=?,fetched_count=?,notes=? WHERE run_id=?",
            (now_iso(), index_revision, indexed_count, fetched_count, notes, run_id),
        )

    def add_todo(self, kind: str, subject: str, detail: str):
        """登记待办；同主题已结清时重新打开并刷新说明，未结清时保留首次说明。"""
        self.conn.execute(
            "INSERT INTO todos(kind,subject,detail,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(kind,subject) DO UPDATE SET "
            "detail=excluded.detail, resolved_at=NULL WHERE todos.resolved_at IS NOT NULL",
            (kind, subject, detail, now_iso()),
        )

    def resolve_todo(self, kind: str, subject: str):
        self.conn.execute(
            "UPDATE todos SET resolved_at=? WHERE kind=? AND subject=? AND resolved_at IS NULL",
            (now_iso(), kind, subject),
        )

    def resolve_todo_prefix(self, kind: str, prefix: str):
        """某类待办按主题前缀批量结清（如新一轮重算前清理旧资产覆盖待办）。"""
        self.conn.execute(
            "UPDATE todos SET resolved_at=? WHERE kind=? AND subject LIKE ? AND resolved_at IS NULL",
            (now_iso(), kind, prefix + "%"),
        )

    def open_todos(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM todos WHERE resolved_at IS NULL ORDER BY id"
        ).fetchall()

    # ---------- 公告与来源文档 ----------
    def upsert_advisory(self, advisory_id, revision, publisher, status, replaced_by,
                        external_ids, references):
        existing = self.conn.execute(
            "SELECT 1 FROM advisories WHERE advisory_id=? AND revision=?", (advisory_id, revision)
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE advisories SET status=?,replaced_by=?,external_ids=?,ref_list=?,last_seen=? "
                "WHERE advisory_id=? AND revision=?",
                (status, replaced_by, json.dumps(external_ids, ensure_ascii=False),
                 json.dumps(references, ensure_ascii=False), now_iso(), advisory_id, revision),
            )
        else:
            self.conn.execute(
                "INSERT INTO advisories(advisory_id,revision,publisher,status,replaced_by,external_ids,"
                "ref_list,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                (advisory_id, revision, publisher, status, replaced_by,
                 json.dumps(external_ids, ensure_ascii=False), json.dumps(references, ensure_ascii=False),
                 now_iso(), now_iso()),
            )

    def known_revisions(self, advisory_id: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT revision FROM advisories WHERE advisory_id=? ORDER BY revision", (advisory_id,)
        ).fetchall()
        return [r["revision"] for r in rows]

    def advisories_sharing_external_id(self) -> dict[str, list[str]]:
        """列出共享外部编号的不同内部公告。

        external_ids 相同只代表外部引用重合，不代表公告相同；
        去重必须以内部公告标识+修订为准，不能按外部编号或标题合并。
        """
        shared: dict[str, set] = {}
        for row in self.conn.execute(
            "SELECT advisory_id, external_ids FROM advisories"
        ).fetchall():
            for ext in json.loads(row["external_ids"]):
                shared.setdefault(ext, set()).add(row["advisory_id"])
        return {ext: sorted(ids) for ext, ids in shared.items() if len(ids) > 1}

    def save_page_document(self, advisory_id, revision, source_kind, page_status,
                           machine_href, machine_sha_expected, artifact_sha):
        self.conn.execute(
            "INSERT OR REPLACE INTO page_documents(advisory_id,revision,source_kind,page_status,"
            "machine_href,machine_sha256_expected,artifact_sha256,observed_at) VALUES(?,?,?,?,?,?,?,?)",
            (advisory_id, revision, source_kind, page_status, machine_href,
             machine_sha_expected, artifact_sha, now_iso()),
        )

    def save_machine_document(self, advisory_id, revision, artifact_sha, digest_matches,
                              raw_doc, content_revision=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO machine_documents(advisory_id,revision,artifact_sha256,"
            "observed_at,digest_matches_page,content_revision,raw_json) VALUES(?,?,?,?,?,?,?)",
            (advisory_id, revision, artifact_sha, now_iso(),
             None if digest_matches is None else int(digest_matches),
             content_revision if content_revision is not None else revision,
             json.dumps(raw_doc, ensure_ascii=False)),
        )

    def page_digest_expectation(self, advisory_id, revision):
        row = self.conn.execute(
            "SELECT machine_sha256_expected FROM page_documents WHERE advisory_id=? AND revision=? "
            "AND source_kind='vendor'",
            (advisory_id, revision),
        ).fetchone()
        return row["machine_sha256_expected"] if row else None

    def get_machine_document(self, advisory_id, revision) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM machine_documents WHERE advisory_id=? AND revision=?",
            (advisory_id, revision),
        ).fetchone()

    # ---------- 适用关系 ----------
    def replace_applicability(self, advisory_id, revision, clauses: list[dict]):
        """用某次修订的产品条款整体重建该修订的适用关系。

        已被来源明确排除的组件条款写为 inactive，其他条款保持 active；
        公告级撤销/接续由 deactivate_advisory 处理，不波及同篇其他公告。
        同一公告的旧修订条款标记为被新修订取代（保留原件与依据，不再参与评估），
        避免新旧修订同时对同一资产产生互相冲突的结论。
        """
        self.conn.execute(
            "DELETE FROM applicability WHERE advisory_id=? AND advisory_revision=?",
            (advisory_id, revision),
        )
        self.conn.execute(
            "UPDATE applicability SET active=0, inactive_reason=? "
            "WHERE advisory_id=? AND advisory_revision<? AND active=1",
            ("已被修订 %s 取代" % revision, advisory_id, revision),
        )
        for c in clauses:
            excluded = c.get("excluded", [])
            self.conn.execute(
                "INSERT INTO applicability(advisory_id,advisory_revision,product_id,aliases,component,"
                "introduced,fixed,excluded,mitigation,active,inactive_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (advisory_id, revision, c["product_id"], json.dumps(c.get("aliases", []), ensure_ascii=False),
                 c.get("component"), c.get("introduced"), c.get("fixed"),
                 json.dumps(excluded, ensure_ascii=False), c.get("mitigation"),
                 0 if c.get("inactive") else 1, c.get("inactive_reason")),
            )

    def deactivate_advisory(self, advisory_id, reason):
        """公告撤销或被接续：撤掉其全部适用关联；不影响其他公告。"""
        self.conn.execute(
            "UPDATE applicability SET active=0,inactive_reason=? WHERE advisory_id=? AND active=1",
            (reason, advisory_id),
        )

    def active_applicability(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM applicability WHERE active=1 ORDER BY advisory_id,advisory_revision,id"
        ).fetchall()

    # ---------- 资产与评估 ----------
    def upsert_asset(self, asset_id, package, version, components, asset_revision):
        self.conn.execute(
            "INSERT INTO assets(asset_id,package,version,components,asset_revision,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET package=excluded.package,"
            "version=excluded.version,components=excluded.components,asset_revision=excluded.asset_revision,"
            "updated_at=excluded.updated_at",
            (asset_id, package, version, json.dumps(components, ensure_ascii=False),
             asset_revision, now_iso()),
        )

    def assets(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM assets ORDER BY asset_id").fetchall()

    def clear_evaluations(self):
        """资产或公告变化后整体重算；不保留旧包版本上的过时结论。"""
        self.conn.execute("DELETE FROM evaluations")

    def save_evaluation(self, asset_id, advisory_id, revision, product_id, component,
                        matched_version, verdict, basis, applicability_id):
        self.conn.execute(
            "INSERT INTO evaluations(asset_id,advisory_id,advisory_revision,product_id,component,"
            "matched_version,verdict,basis,applicability_id,evaluated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (asset_id, advisory_id, revision, product_id, component, matched_version,
             verdict, basis, applicability_id, now_iso()),
        )

    def evaluations(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM evaluations ORDER BY asset_id,advisory_id").fetchall()

    def commit(self):
        self.conn.commit()
