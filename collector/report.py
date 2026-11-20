"""面向值班员的回查视图。

所有结论都可回溯到：公告原件哈希、观察时刻、来源地址、产品条款与判断依据。
明确区分“有明确受影响依据”“明确排除/不受影响”“待核对（采集欠缺）”，
待核对绝不表述为安全。
"""
from __future__ import annotations

import json

from .store import Store

VERDICT_LABEL = {
    "affected": "明确受影响（有公告区间依据）",
    "excluded": "厂商明确排除该版本",
    "not_affected": "版本不在公告区间内",
    "unknown": "待核对：缺少可依据的公告条款",
}


class Reporter:
    def __init__(self, store: Store):
        self.store = store

    def asset_digest(self) -> list[dict]:
        """每个资产一条：当前结论 + 回查指针。"""
        rows = self.store.evaluations()
        assets = {a["asset_id"]: a for a in self.store.assets()}
        by_asset: dict[str, list[dict]] = {}
        for row in rows:
            by_asset.setdefault(row["asset_id"], []).append(dict(row))
        digest = []
        for asset_id, asset in assets.items():
            evals = by_asset.get(asset_id, [])
            verdicts = sorted({e["verdict"] for e in evals})
            if not evals:
                headline = VERDICT_LABEL["unknown"]
            elif "affected" in verdicts:
                headline = VERDICT_LABEL["affected"]
            elif "excluded" in verdicts and all(v in ("excluded",) for v in verdicts):
                headline = VERDICT_LABEL["excluded"]
            elif all(v == "not_affected" for v in verdicts):
                headline = VERDICT_LABEL["not_affected"]
            else:
                headline = "部分排除/不受影响，详见逐条依据"
            digest.append({
                "asset_id": asset_id,
                "package": asset["package"],
                "version": asset["version"],
                "components": json.loads(asset["components"]),
                "headline": headline,
                "evaluations": [
                    {
                        "advisory_id": e["advisory_id"],
                        "advisory_revision": e["advisory_revision"],
                        "product_id": e["product_id"],
                        "component": e["component"],
                        "matched_version": e["matched_version"],
                        "verdict": e["verdict"],
                        "verdict_label": VERDICT_LABEL[e["verdict"]],
                        "basis": e["basis"],
                    }
                    for e in evals
                ],
            })
        # 没有任何评估记录的资产（无条款可核对）也要显式列出
        known = {d["asset_id"] for d in digest}
        for asset_id, asset in assets.items():
            if asset_id not in known:
                digest.append({
                    "asset_id": asset_id,
                    "package": asset["package"],
                    "version": asset["version"],
                    "components": json.loads(asset["components"]),
                    "headline": VERDICT_LABEL["unknown"],
                    "evaluations": [],
                })
        return digest

    def evidence_chain(self, advisory_id: str, revision: int) -> dict:
        """回查单篇公告修订的完整证据链：网页、机器文件、观察时刻与来源地址。"""
        machine = self.store.get_machine_document(advisory_id, revision)
        pages = self.store.conn.execute(
            "SELECT * FROM page_documents WHERE advisory_id=? AND revision=? ORDER BY source_kind",
            (advisory_id, revision),
        ).fetchall()
        observations = self.store.conn.execute(
            "SELECT * FROM observations WHERE sha256 IN "
            "(SELECT artifact_sha256 FROM page_documents WHERE advisory_id=? AND revision=? "
            "UNION SELECT artifact_sha256 FROM machine_documents WHERE advisory_id=? AND revision=?) "
            "ORDER BY observed_at",
            (advisory_id, revision, advisory_id, revision),
        ).fetchall()
        return {
            "advisory_id": advisory_id,
            "revision": revision,
            "machine_document": dict(machine) if machine else None,
            "pages": [dict(p) for p in pages],
            "observations": [dict(o) for o in observations],
            "artifact_retrieval": "凭 artifact_sha256 可从 artifacts 表取回未篡改原件",
        }

    def open_todos(self) -> list[dict]:
        return [
            {
                "kind": row["kind"],
                "subject": row["subject"],
                "detail": row["detail"],
                "created_at": row["created_at"],
                "interpretation": {
                    "fetch_failed": "采集欠缺：来源暂时不可达，需重试，不代表资产安全",
                    "unreferenced_fetch": "采集欠缺：公告被引用但原文尚未取得",
                    "coverage_gap": "采集欠缺：索引/资产覆盖可能不足",
                    "digest_mismatch": "来源不一致：网页与机器文件修订对不上，已各自保留",
                    "withdrawn": "公告状态：已被厂商正式撤销，关联已撤下",
                    "replaced": "公告状态：已被另一篇公告接续",
                    "redirect_blocked": "策略拦截：跳转未经允许的外部域，已停止未带凭据",
                }.get(row["kind"], row["kind"]),
            }
            for row in self.store.open_todos()
        ]

    def text_report(self) -> str:
        lines = ["# 公告适用性值班报告", ""]
        lines.append("## 资产核对（结论均可回查公告原文）")
        for item in self.asset_digest():
            lines.append(
                "- %s | %s %s 组件=%s → %s"
                % (item["asset_id"], item["package"], item["version"],
                   json.dumps(item["components"], ensure_ascii=False), item["headline"])
            )
            for ev in item["evaluations"]:
                lines.append(
                    "    * %s 修订%s 产品=%s 组件=%s 版本=%s：%s | 依据: %s"
                    % (ev["advisory_id"], ev["advisory_revision"], ev["product_id"],
                       ev["component"], ev["matched_version"], ev["verdict_label"], ev["basis"])
                )
        lines.append("")
        lines.append("## 待办（均为采集/来源状态事项，不是安全结论）")
        todos = self.open_todos()
        if not todos:
            lines.append("- 无未决待办")
        for todo in todos:
            lines.append("- [%s] %s — %s（%s）" % (
                todo["kind"], todo["subject"], todo["detail"], todo["interpretation"]))
        return "\n".join(lines)
