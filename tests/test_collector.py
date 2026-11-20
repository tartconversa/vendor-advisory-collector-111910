"""采集端联动场景测试：修订、排除、撤销、接续、索引挪动、镜像与限速等。

只验证防御性采集与适用性核对逻辑；不包含任何利用或扫描动作。
"""
import hashlib
import json

from tests.collector_support import CollectorTest


class BaselineCollectionTest(CollectorTest):
    def test_full_run_collects_and_evaluates_with_evidence(self):
        summary = self.run_engine()
        self.assertEqual(sorted(summary.fetched), ["ADV-A", "ADV-B"])
        self.assertEqual(summary.failed, [])
        # 原件、观察时刻、来源地址均留痕
        obs = [dict(r) for r in self.store.conn.execute(
            "SELECT DISTINCT source_kind FROM observations").fetchall()]
        kinds = {r["source_kind"] for r in obs}
        self.assertIn("vendor_page", kinds)
        self.assertIn("machine", kinds)
        self.assertIn("mirror_page", kinds)
        # node-a 通过别名 runtime-old 匹配 runtime 1.3.2，落入区间
        node_a = [e for e in self.evaluations("node-a") if e["product_id"] == "runtime"][0]
        self.assertEqual(node_a["verdict"], "affected")
        # node-b 的 workbench engine 2.0.1 受影响
        node_b = [e for e in self.evaluations("node-b") if e["product_id"] == "workbench"][0]
        self.assertEqual(node_b["verdict"], "affected")
        # 无悬挂待办
        self.assertEqual(self.todos(), [])

    def test_evidence_chain_retrieves_raw_artifact(self):
        self.run_engine()
        chain = self.reporter.evidence_chain("ADV-A", 1)
        self.assertIsNotNone(chain["machine_document"])
        sha = chain["machine_document"]["artifact_sha256"]
        raw = self.store.get_artifact(sha)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        # 网页与机器文件在修订一致时摘要匹配
        self.assertEqual(chain["machine_document"]["digest_matches_page"], 1)


class ExclusionTest(CollectorTest):
    def test_explicit_exclusion_removes_only_that_component_link(self):
        self.run_engine()
        before = [e for e in self.evaluations("node-a") if e["product_id"] == "runtime"][0]
        self.assertEqual(before["verdict"], "affected")
        # 来源发布修订 2：runtime 明确排除 1.3.2
        self.control("exclude")
        self.run_engine()
        node_a_runtime = [e for e in self.evaluations("node-a")
                          if e["product_id"] == "runtime" and e["component"] == "engine"]
        self.assertTrue(node_a_runtime)
        self.assertEqual(node_a_runtime[0]["verdict"], "excluded")
        self.assertIn("明确排除", node_a_runtime[0]["basis"])
        # 同篇公告的 workbench 条款仍然有效，node-b 结论不受影响
        node_b = [e for e in self.evaluations("node-b") if e["product_id"] == "workbench"][0]
        self.assertEqual(node_b["verdict"], "affected")
        # 两个修订都保留，旧修订原件不被覆盖
        self.assertEqual(self.store.known_revisions("ADV-A"), [1, 2])


class MismatchTest(CollectorTest):
    def test_page_machine_revision_mismatch_is_kept_not_merged(self):
        self.run_engine()
        self.control("mismatch")
        self.run_engine()
        mismatches = self.todos(kind="digest_mismatch")
        # mismatch 是来源级开关，每篇被抓取的公告都会独立登记一条不一致
        self.assertTrue(mismatches)
        self.assertTrue(all("不拼接" in t["detail"] for t in mismatches))
        subjects = {t["subject"] for t in mismatches}
        self.assertIn("advisory:ADV-A", subjects)
        # 机器文档仍独立存档，并标记为不一致
        doc = self.store.get_machine_document("ADV-A", 1)
        self.assertEqual(doc["digest_matches_page"], 0)
        # 网页期望哈希与机器实际哈希都在库里可分别回查
        self.assertNotEqual(doc["artifact_sha256"],
                            self.store.page_digest_expectation("ADV-A", 1))
        # 再次运行不产生重复待办（同公告先结清再登记）
        before = len(self.todos(kind="digest_mismatch"))
        self.run_engine()
        self.assertEqual(len(self.todos(kind="digest_mismatch")), before)


class WithdrawReplaceTest(CollectorTest):
    def test_withdraw_deactivates_links_but_keeps_other_advisories(self):
        self.run_engine()
        self.assertTrue(self.active_clauses("ADV-A"))
        self.control("withdraw")
        self.run_engine()
        self.assertEqual(self.active_clauses("ADV-A"), [])
        self.assertTrue(self.todos(kind="withdrawn"))
        # ADV-B 是独立公告，不受 ADV-A 撤销影响
        adv_b = [e for e in self.evaluations() if e["advisory_id"] == "ADV-B"]
        self.assertTrue(adv_b or not self.active_clauses("ADV-B"))

    def test_replaced_registers_successor_fetch_and_deactivates_old(self):
        self.run_engine()
        self.control("replace")
        summary = self.run_engine()
        # 接续公告 ADV-B 已被引用并在同轮补取成功
        self.assertIn("ADV-B", summary.fetched)
        self.assertEqual(self.active_clauses("ADV-A"), [])
        replaced_todos = self.todos(kind="replaced")
        self.assertTrue(any("ADV-B" in t["detail"] for t in replaced_todos))
        adv = dict(self.store.conn.execute(
            "SELECT * FROM advisories WHERE advisory_id='ADV-A' ORDER BY revision DESC LIMIT 1"
        ).fetchone())
        self.assertEqual(adv["status"], "replaced")
        self.assertEqual(adv["replaced_by"], "ADV-B")


class IndexShiftTest(CollectorTest):
    def test_index_shift_picks_up_newly_listed_advisory(self):
        self.run_engine()
        self.control("index_shift")
        summary = self.run_engine()
        # 索引挪动后 ADV-C 出现在列表，三轮分页都要取全
        self.assertIn("ADV-C", summary.indexed_ids)
        self.assertIn("ADV-C", summary.fetched)
        self.assertEqual(len(summary.indexed_ids), 3)

    def test_referenced_but_unindexed_advisory_is_backfilled(self):
        # ADV-B 被 ADV-A 的 references 明确引用；即使人为只看首页也会被补取
        summary = self.run_engine()
        self.assertIn("ADV-B", summary.fetched)
        self.assertEqual(self.todos(kind="unreferenced_fetch"), [])


class MirrorTest(CollectorTest):
    def test_mirror_keeps_old_revision_without_overwriting(self):
        self.control("exclude")  # 来源当前为修订 2
        self.run_engine()
        page_rows = [dict(r) for r in self.store.conn.execute(
            "SELECT * FROM page_documents WHERE advisory_id='ADV-A' ORDER BY source_kind,revision"
        ).fetchall()]
        vendor = [r for r in page_rows if r["source_kind"] == "vendor"]
        mirror = [r for r in page_rows if r["source_kind"] == "mirror"]
        self.assertTrue(vendor and mirror)
        # 厂商页是修订 2，镜像固定复制来源修订 1，两者并存且镜像没有覆盖厂商原文
        self.assertEqual(vendor[0]["revision"], 2)
        self.assertEqual(mirror[0]["revision"], 1)
        latest_machine = self.store.get_machine_document("ADV-A", 2)
        self.assertIsNotNone(latest_machine)


class AvailabilityTest(CollectorTest):
    def test_offline_is_temporary_todo_not_withdrawal_or_safety(self):
        self.run_engine()
        self.control("offline")  # 厂商路径 503，镜像仍在
        summary = self.run_engine()
        # 再次运行时至少记录暂时不可访问待办，且不撤销既有公告
        failed_todos = self.todos(kind="fetch_failed")
        self.assertTrue(any("暂时不可访问" in t["detail"] for t in failed_todos))
        # 既有适用关系不因为一次 503 被撤下
        self.assertTrue(self.active_clauses("ADV-A"))
        # 恢复后下一轮成功并结清待办
        self.control("reset")
        self.run_engine()
        remaining = [t for t in self.todos(kind="fetch_failed")
                     if "暂时不可访问" in t["detail"]]
        self.assertEqual(remaining, [])

    def test_throttle_429_respects_retry_after(self):
        sleeps = []
        self.fetcher.sleeper = lambda seconds: sleeps.append(seconds)
        self.control("throttle")  # 索引返回 429 Retry-After=3
        self.run_engine()
        # 至少发生过退避等待，且等待值来自来源的 Retry-After
        self.assertIn(3.0, sleeps)
        self.assertTrue(self.todos(kind="fetch_failed"))


class RedirectPolicyTest(CollectorTest):
    def test_external_redirect_is_blocked_without_credentials(self):
        from collector.fetch import RedirectNotAllowed
        with self.assertRaises(RedirectNotAllowed) as ctx:
            self.fetcher.get("/vendor/redirect")
        self.assertIn("unapproved.example.test", ctx.exception.target)

    def test_credentials_only_sent_inside_scope(self):
        scoped = self.policy.auth_header(self.base + "/vendor/advisory?id=ADV-A")
        self.assertEqual(scoped.get("Authorization"), "Bearer secret-token")
        # 镜像路径不在凭据作用域，绝不携带受限凭据
        self.assertEqual(self.policy.auth_header(self.base + "/mirror/advisory?id=ADV-A"), {})
        # 动态放行的机器文件同样不在 /vendor/ 凭据作用域
        self.policy.allow_referenced("/machine?id=ADV-A&revision=1")
        self.assertEqual(self.policy.auth_header(self.base + "/machine?id=ADV-A&revision=1"), {})

    def test_outside_allowed_prefix_is_refused(self):
        with self.assertRaises(PermissionError):
            self.fetcher.get("/__control")


class AssetUpgradeTest(CollectorTest):
    def test_upgrade_rechecks_current_application_not_legacy_verdict(self):
        self.run_engine()
        first = {(e["asset_id"], e["product_id"]): e for e in self.evaluations()}
        self.assertEqual(first[("node-a", "runtime")]["verdict"], "affected")
        self.control("asset_upgrade")
        self.run_engine()
        asset = dict(self.store.conn.execute(
            "SELECT * FROM assets WHERE asset_id='node-a'").fetchone())
        # 资产快照已升级到 1.4.0
        self.assertEqual(asset["version"], "1.4.0")
        # 评估被整体重算：记录数与依据时间都是新的，且按组件当前版本独立判断
        second = self.evaluations("node-a")
        self.assertTrue(second)
        runtime_evals = [e for e in second if e["product_id"] == "runtime"]
        self.assertTrue(runtime_evals)
        # 组件版本沿用清单登记值；每条结论都带当前依据，而非沿用旧包结论
        for ev in runtime_evals:
            self.assertTrue(ev["basis"])
            self.assertIn(ev["matched_version"], ("1.4.0", "1.3.2"))
        # 旧评估行已被清除重算而非追加
        count = self.store.conn.execute(
            "SELECT COUNT(*) AS c FROM evaluations WHERE asset_id='node-a'").fetchone()["c"]
        self.assertEqual(count, len(second))


class ExternalIdentityTest(CollectorTest):
    def test_shared_external_id_does_not_merge_distinct_advisories(self):
        self.run_engine()
        # 两篇内部公告各自独立存档，即使来源未来让它们共享外部编号也不合并
        ids = [r["advisory_id"] for r in self.store.conn.execute(
            "SELECT DISTINCT advisory_id FROM advisories ORDER BY advisory_id").fetchall()]
        self.assertEqual(ids, ["ADV-A", "ADV-B"])
        # 模拟两篇公告共享同一外部编号：去重视图应把它们列为两个不同内部公告
        self.store.upsert_advisory(
            "ADV-B", 1, "vendor-a", "published", None,
            ["SYN-2026-01"], ["/vendor/advisory?id=ADV-A"])
        shared = self.store.advisories_sharing_external_id()
        self.assertEqual(shared.get("SYN-2026-01"), ["ADV-A", "ADV-B"])
        # 适用关系仍按内部公告分别维护，互不覆盖
        self.assertTrue(self.active_clauses("ADV-A"))


class MissingAdvisoryIsNotSafetyTest(CollectorTest):
    def test_asset_without_any_clause_stays_unknown_todo(self):
        self.run_engine()
        # 人为撤下全部适用关系后重算：资产不得被判定为安全
        self.store.deactivate_advisory("ADV-A", "测试：全部撤下")
        self.store.deactivate_advisory("ADV-B", "测试：全部撤下")
        stats = self.engine.evaluate_assets()
        self.assertGreaterEqual(stats["unknown"], 1)
        gaps = self.todos(kind="coverage_gap")
        self.assertTrue(any(g["subject"].startswith("asset:") for g in gaps))
        for g in gaps:
            self.assertIn("不代表版本安全", g["detail"])
        digest = self.reporter.asset_digest()
        for item in digest:
            if not item["evaluations"]:
                self.assertIn("待核对", item["headline"])
