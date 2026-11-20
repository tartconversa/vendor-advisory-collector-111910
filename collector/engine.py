"""采集编排与联动规则。

覆盖需求中的联动场景：
- 索引分页逐页取全，识别覆盖不足；references 指向但索引未覆盖的公告补取；
- 厂商页与机器文件修订对不上时保留不一致并登记待办，不拼出权威结论；
- 镜像只反映来源旧修订，不覆盖已取得的更新原文；
- withdraw（撤销）、replaced（接续）、offline（暂时不可访问）、429（限速）后果不同；
- 组件被明确排除时仅撤掉该组件关联，同篇其他组件仍有效；
- 资产升级后按新版本重新核对，旧包结论不沿用；
- 抓取欠缺只产生待办，绝不据此判定资产安全。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from . import parse as parse_mod
from .fetch import Fetcher, RateLimited, RedirectNotAllowed, SameSiteRedirect, SourceUnavailable
from .store import Store
from .version import in_affected_range, is_excluded


@dataclass
class RunSummary:
    run_id: str
    index_revision: int | None = None
    indexed_ids: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    todos_opened: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class CollectionEngine:
    def __init__(self, store: Store, fetcher: Fetcher):
        self.store = store
        self.fetcher = fetcher
        self.policy = fetcher.policy

    # ---------- 单次带异常分类的抓取 ----------
    def _fetch(self, url_or_path: str, source_kind: str, run_id: str, subject: str):
        """返回 FetchResult；不可访问/限速/跳转拦截时登记待办并返回 None。"""
        try:
            result = self.fetcher.get(url_or_path)
        except SameSiteRedirect as redirect:
            # 同站允许跳转：剥离凭据后重新请求一次
            result = self.fetcher.get_without_credentials(redirect.target)
        except RedirectNotAllowed as exc:
            self.store.add_todo(
                "redirect_blocked", subject,
                "来源试图跳转到未经允许的外部域，已停止且未携带凭据: " + exc.target,
            )
            return None
        except SourceUnavailable as exc:
            # 暂时不可访问 ≠ 撤销：保留待核对待办，不改动既有公告结论
            self.store.add_todo(
                "fetch_failed", subject,
                "来源暂时不可访问 HTTP %s，需稍后重试；不代表公告不存在或资产安全" % exc.status,
            )
            return None
        except RateLimited as exc:
            self.store.add_todo(
                "fetch_failed", subject,
                "来源限速 Retry-After=%s，需按约定退避后重试" % exc.retry_after,
            )
            return None
        except Exception as exc:  # 网络层其他故障同样按采集欠缺处理
            self.store.add_todo("fetch_failed", subject, "抓取异常: %r" % exc)
            return None
        sha = self.store.save_artifact(result.body, result.content_type)
        self.store.record_observation(result.url, source_kind, result.status, sha, run_id)
        return result

    # ---------- 索引：分页取全 + 覆盖识别 ----------
    def collect_index(self, run_id: str) -> tuple[list[dict], int | None]:
        indexed: list[dict] = []
        page = 0
        index_revision: int | None = None
        seen_revisions: set[int] = set()
        while True:
            result = self._fetch(
                "/vendor/index?page=%d" % page, "index", run_id, "index:page:%d" % page
            )
            if result is None:
                self.store.add_todo(
                    "coverage_gap", "index:page:%d" % page,
                    "索引第 %d 页抓取失败，索引覆盖可能不足" % page,
                )
                break
            if result.status != 200:
                self.store.add_todo(
                    "coverage_gap", "index:page:%d" % page,
                    "索引第 %d 页返回 HTTP %s，覆盖可能不足" % (page, result.status),
                )
                break
            info = parse_mod.parse_index(result.body)
            if index_revision is None:
                index_revision = info["index_revision"]
            elif info["index_revision"] != index_revision:
                # 抓取过程中索引发生挪动（新发布）：标记覆盖不足，需下一轮重取
                seen_revisions.add(info["index_revision"])
                self.store.add_todo(
                    "coverage_gap", "index:revision-drift",
                    "抓取期间索引修订由 %s 变为 %s，分页可能挪动，需重新遍历"
                    % (index_revision, info["index_revision"]),
                )
            indexed.extend(info["items"])
            if not info["next"]:
                break
            page += 1
            if page > 1000:  # 防御性上限，避免异常来源导致死循环
                self.store.add_todo("coverage_gap", "index:page-limit", "索引分页超过安全上限")
                break
        if seen_revisions:
            self.store.add_todo(
                "coverage_gap", "index:revision-drift",
                "本轮观察到多个索引修订 %s，覆盖不保证完整" % sorted(seen_revisions | {index_revision}),
            )
        return indexed, index_revision

    # ---------- 单篇公告：网页 + 机器文件 + 镜像 ----------
    def collect_advisory(self, advisory_id: str, run_id: str) -> bool:
        vendor = self._fetch(
            "/vendor/advisory?id=%s" % advisory_id, "vendor_page", run_id, "advisory:" + advisory_id
        )
        if vendor is None:
            return False
        if vendor.status != 200:
            # 404/410 等：与“正式撤销”区分登记，不能当作安全
            self.store.add_todo(
                "fetch_failed", "advisory:" + advisory_id,
                "公告页返回 HTTP %s，状态待核对，不能据此判定资产安全" % vendor.status,
            )
            return False

        page_info = parse_mod.parse_advisory_page(vendor.body, "vendor", advisory_id)
        self.store.save_page_document(
            advisory_id, page_info.page_revision or 0, "vendor", page_info.status,
            page_info.machine_href, page_info.machine_sha256, vendor.sha256,
        )

        machine_result = None
        if page_info.machine_href:
            # 只放行该已授权公告页明确引用的机器文件链接（精确路径，同站校验）
            try:
                self.policy.allow_referenced(page_info.machine_href)
            except PermissionError as exc:
                self.store.add_todo(
                    "redirect_blocked", "machine:" + advisory_id, str(exc),
                )
                return False
            machine_result = self._fetch(
                page_info.machine_href, "machine", run_id, "machine:" + advisory_id
            )
        if machine_result is None or machine_result.status != 200:
            self.store.add_todo(
                "unreferenced_fetch", "machine:" + advisory_id,
                "网页引用的机器可读公告尚未成功获取，适用性待核对",
            )
            return False

        machine = parse_mod.parse_machine_advisory(machine_result.body)
        # 网页锚点给的是“网页声称对应机器原文”的哈希；不一致即两个来源修订对不上
        expected = page_info.machine_sha256
        digest_matches = (expected == machine_result.sha256) if expected else None
        revision_drift = (
            page_info.page_revision is not None
            and machine.revision != page_info.page_revision
        )
        # 先结清该公告历史不一致待办，再按本轮观察重新登记/或保持结清
        self.store.resolve_todo("digest_mismatch", "advisory:" + advisory_id)
        if digest_matches is False or revision_drift:
            self.store.add_todo(
                "digest_mismatch", "advisory:" + advisory_id,
                "网页摘要修订（%s）与机器可读文件修订（%s）对不上；网页期望哈希 %s，实际 %s；"
                "两份来源各自保留，不拼接为权威结论，机器内容暂不用于适用性判断"
                % (page_info.page_revision, machine.revision, expected, machine_result.sha256),
            )
        # 以网页锚定修订为主键存档，机器自报修订另存，保证不一致可回查且不互相覆盖
        anchored_revision = page_info.page_revision or machine.revision
        self.store.save_machine_document(
            advisory_id, anchored_revision, machine_result.sha256, digest_matches,
            machine.raw, content_revision=machine.revision,
        )
        # 只有两个来源一致时才把机器内容应用为适用关系；不一致时保留旧的已验证结论
        if digest_matches is False or revision_drift:
            self.store.add_todo(
                "coverage_gap", "applicability:" + advisory_id,
                "公告 %s 网页与机器来源不一致，当前适用性依据待核对，不采用任一来源单独下结论"
                % advisory_id,
            )
        else:
            self.store.resolve_todo("coverage_gap", "applicability:" + advisory_id)
            self._apply_machine_advisory(machine)

        # 镜像：复制的是来源旧修订；仅在本地没有该公告任何原文时补档，永不覆盖更新修订
        self._collect_mirror(advisory_id, run_id)
        self.store.resolve_todo("unreferenced_fetch", "machine:" + advisory_id)
        self.store.resolve_todo("fetch_failed", "advisory:" + advisory_id)
        self.store.resolve_todo("fetch_failed", "machine:" + advisory_id)
        return True

    def _apply_machine_advisory(self, machine: parse_mod.MachineAdvisory) -> None:
        self.store.upsert_advisory(
            machine.advisory_id, machine.revision, machine.publisher, machine.status,
            machine.replaced_by, list(machine.external_ids), list(machine.references),
        )
        clauses = []
        for product in machine.products:
            # 一篇公告可列多个组件、多个区间；每个产品条款独立判断排除
            for rng in product.affected:
                # 该产品被明确排除的版本在评估层逐版本撤关联；条款本身保留以留依据
                clauses.append({
                    "product_id": product.product_id,
                    "aliases": list(product.aliases),
                    "component": product.component,
                    "introduced": rng.introduced,
                    "fixed": rng.fixed,
                    "excluded": list(product.excluded),
                    "mitigation": product.mitigation,
                    "inactive": False,
                    "inactive_reason": None,
                })
        self.store.replace_applicability(machine.advisory_id, machine.revision, clauses)

        if machine.status == "withdrawn":
            # 正式撤销：撤掉该公告全部关联，但不影响其他公告
            self.store.deactivate_advisory(machine.advisory_id, "公告已被厂商正式撤销")
            self.store.add_todo(
                "withdrawn", "advisory:" + machine.advisory_id,
                "公告 %s 已正式撤销，其受影响关联已撤下" % machine.advisory_id,
            )
        elif machine.status == "replaced" or machine.replaced_by:
            target = machine.replaced_by or machine.status
            self.store.deactivate_advisory(
                machine.advisory_id, "公告已由 %s 接续，旧公告关联撤下" % target
            )
            self.store.add_todo(
                "replaced", "advisory:" + machine.advisory_id,
                "公告 %s 已被 %s 接续，需采集接续公告" % (machine.advisory_id, target),
            )
            # 接续公告成为明确引用但可能尚未获取的对象
            if machine.replaced_by:
                self.store.add_todo(
                    "unreferenced_fetch", "advisory:" + machine.replaced_by,
                    "接续公告 %s 已被引用但尚未确认获取" % machine.replaced_by,
                )

        # references：明确引用而索引可能尚未覆盖的公告，登记补取
        for ref in machine.references:
            ref_id = ref.split("id=")[-1] if "id=" in ref else ref.rsplit("/", 1)[-1]
            self.store.add_todo(
                "unreferenced_fetch", "advisory:" + ref_id,
                "公告 %s 引用了 %s，索引可能尚未覆盖，需补取" % (machine.advisory_id, ref_id),
            )

    def _collect_mirror(self, advisory_id: str, run_id: str) -> None:
        mirror = self._fetch(
            "/mirror/advisory?id=%s" % advisory_id, "mirror_page", run_id, "mirror:" + advisory_id
        )
        if mirror is None or mirror.status != 200:
            return
        mirror_page = parse_mod.parse_advisory_page(mirror.body, "mirror", advisory_id)
        rev = mirror_page.page_revision or 0
        known = self.store.known_revisions(advisory_id)
        # 镜像只复制来源修订：本地已有同修订或更新修订时，仅存档原件，不覆盖网页/机器原文
        self.store.save_page_document(
            advisory_id, rev, "mirror", mirror_page.status,
            mirror_page.machine_href, mirror_page.machine_sha256, mirror.sha256,
        )
        if not known:
            self.store.add_todo(
                "unreferenced_fetch", "machine:" + advisory_id,
                "仅有镜像旧修订（修订 %s），厂商更新原文尚未取得" % rev,
            )

    # ---------- 资产：拉取清单并重新核对 ----------
    def sync_assets(self, run_id: str) -> bool:
        result = self._fetch("/assets", "index", run_id, "assets")
        if result is None or result.status != 200:
            self.store.add_todo("fetch_failed", "assets", "资产清单获取失败，沿用上次快照但标记待核对")
            return False
        doc = json.loads(result.body.decode("utf-8"))
        revision = int(doc["revision"])
        for asset in doc["assets"]:
            self.store.upsert_asset(
                asset["id"], asset["package"], asset["version"],
                asset.get("components", {}), revision,
            )
        return True

    def evaluate_assets(self) -> dict:
        """按当前资产与当前有效适用关系整体重算。

        资产升级后旧包版本结论一律清除重算；抓不到公告的资产保持 unknown 待办，
        不会出现“没公告 = 安全”的结论。
        """
        self.store.clear_evaluations()
        # 上一轮的资产覆盖待办先结清；本轮仍无条款可核对的资产会重新登记，避免悬挂旧待办
        self.store.resolve_todo_prefix("coverage_gap", "asset:")
        clauses = self.store.active_applicability()
        stats = {"affected": 0, "excluded": 0, "not_affected": 0, "unknown": 0}
        assets = self.store.assets()
        for asset in assets:
            components = json.loads(asset["components"])
            matched_any = False
            for row in clauses:
                aliases = json.loads(row["aliases"])
                product_names = (row["product_id"],) + tuple(aliases)
                component = row["component"]
                package_match = asset["package"] in product_names

                if component:
                    # 条款绑定具体组件：发行包自带或内嵌的同名组件版本都要核对
                    if component not in components:
                        continue
                    matched_version = components[component]
                    match_note = ("（包名 %s 经别名匹配）" % asset["package"]) if package_match else (
                        "（发行包 %s 内嵌组件 %s）" % (asset["package"], component)
                    )
                elif package_match:
                    # 无组件条款时只按发行包整体版本核对
                    matched_version = asset["version"]
                    match_note = "（按发行包版本匹配）"
                else:
                    continue

                matched_any = True
                excluded = json.loads(row["excluded"])
                if is_excluded(matched_version, excluded):
                    verdict, basis = "excluded", (
                        "版本 %s 在该公告产品 %s 的明确排除列表 %s 中，受影响关联撤下"
                        % (matched_version, row["product_id"], excluded)
                    )
                elif in_affected_range(matched_version, row["introduced"], row["fixed"]):
                    verdict, basis = "affected", (
                        "版本 %s 落入区间 introduced=%s（含）/ fixed=%s（不含），产品 %s%s"
                        % (matched_version, row["introduced"], row["fixed"], row["product_id"], match_note)
                    )
                else:
                    verdict, basis = "not_affected", (
                        "版本 %s 不在区间 introduced=%s / fixed=%s 内"
                        % (matched_version, row["introduced"], row["fixed"])
                    )
                stats[verdict] += 1
                self.store.save_evaluation(
                    asset["asset_id"], row["advisory_id"], row["advisory_revision"],
                    row["product_id"], component, matched_version, verdict, basis, row["id"],
                )
            if not matched_any:
                # 没有任何适用条款可核对：登记为采集/覆盖欠缺，而非安全
                stats["unknown"] += 1
                self.store.add_todo(
                    "coverage_gap", "asset:" + asset["asset_id"],
                    "资产 %s（%s %s）当前无任何已取得公告条款可核对；"
                    "这是采集覆盖待办，不代表版本安全"
                    % (asset["asset_id"], asset["package"], asset["version"]),
                )
        self.store.commit()
        return stats

    # ---------- 一次完整采集运行 ----------
    def _backfill_references(self, run_id: str, summary: RunSummary) -> None:
        """补取被明确引用但尚未取得的公告，形成引用闭包。

        新补取公告若再引用其他公告，同轮继续；visited 防止重复抓取与引用环。
        抓取失败保留待办，留给后续运行，绝不当作公告不存在。
        """
        visited: set[str] = set(summary.fetched)
        queue = [
            t["subject"].split(":", 1)[1]
            for t in self.store.open_todos()
            if t["kind"] == "unreferenced_fetch" and t["subject"].startswith("advisory:")
        ]
        while queue:
            candidate = queue.pop(0)
            if candidate in visited:
                self.store.resolve_todo("unreferenced_fetch", "advisory:" + candidate)
                continue
            visited.add(candidate)
            if self.collect_advisory(candidate, run_id):
                if candidate not in summary.fetched:
                    summary.fetched.append(candidate)
                self.store.resolve_todo("unreferenced_fetch", "advisory:" + candidate)
                for todo in self.store.open_todos():
                    if todo["kind"] != "unreferenced_fetch":
                        continue
                    ref_id = todo["subject"].split(":", 1)[1]
                    if ref_id not in visited and ref_id not in queue:
                        queue.append(ref_id)
            else:
                summary.failed.append({"id": candidate, "stage": "backfill"})

    def run_once(self) -> RunSummary:
        run_id = uuid.uuid4().hex
        self.store.start_run(run_id)
        summary = RunSummary(run_id=run_id)

        indexed, index_revision = self.collect_index(run_id)
        summary.index_revision = index_revision
        index_ids = [item["id"] for item in indexed]
        summary.indexed_ids = index_ids

        for advisory_id in index_ids:
            ok = self.collect_advisory(advisory_id, run_id)
            if ok:
                summary.fetched.append(advisory_id)
            else:
                summary.failed.append({"id": advisory_id, "stage": "advisory"})

        self._backfill_references(run_id, summary)

        self.sync_assets(run_id)
        stats = self.evaluate_assets()
        summary.notes.append("评估统计: %r" % stats)
        self.store.finish_run(
            run_id, index_revision, len(index_ids), len(summary.fetched),
            "；".join(summary.notes),
        )
        self.store.commit()
        summary.todos_opened = [t["subject"] for t in self.store.open_todos()]
        return summary
