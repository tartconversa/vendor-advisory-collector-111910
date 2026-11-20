"""采集客户端测试夹具：在内存/临时库上对本地合成来源运行完整引擎。"""
import tempfile
from pathlib import Path

from collector.engine import CollectionEngine
from collector.fetch import Fetcher
from collector.policy import Policy
from collector.report import Reporter
from collector.store import Store
from tests.support import SourceTest


class CollectorTest(SourceTest):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = str(Path(self.tmp.name) / "collector.db")
        self.policy = Policy(
            base_url=self.base,
            allowed_paths=("/vendor/", "/mirror/"),
            redirect_allowed_hosts=("127.0.0.1", "localhost"),
            credential_scope="/vendor/",
            request_interval_ms=0,
            credentials="secret-token",
        )
        self.store = Store(self.db_path)
        self.addCleanup(self.store.close)
        self.fetcher = Fetcher(self.policy)
        self.engine = CollectionEngine(self.store, self.fetcher)
        self.reporter = Reporter(self.store)

    def run_engine(self):
        return self.engine.run_once()

    def todos(self, kind=None, subject=None, only_open=True):
        sql = "SELECT * FROM todos WHERE 1=1"
        params = []
        if only_open:
            sql += " AND resolved_at IS NULL"
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if subject:
            sql += " AND subject=?"
            params.append(subject)
        return [dict(r) for r in self.store.conn.execute(sql, params).fetchall()]

    def evaluations(self, asset_id=None):
        sql = "SELECT * FROM evaluations"
        params = []
        if asset_id:
            sql += " WHERE asset_id=?"
            params.append(asset_id)
        return [dict(r) for r in self.store.conn.execute(sql, params).fetchall()]

    def active_clauses(self, advisory_id=None):
        sql = "SELECT * FROM applicability WHERE active=1"
        params = []
        if advisory_id:
            sql += " AND advisory_id=?"
            params.append(advisory_id)
        return [dict(r) for r in self.store.conn.execute(sql, params).fetchall()]
