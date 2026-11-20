"""采集服务命令行入口。

用法：
  python -m collector.cli --base-url http://127.0.0.1:8765 --db ./var/collector.db once
  python -m collector.cli --base-url http://127.0.0.1:8765 --db ./var/collector.db poll --interval 60
  python -m collector.cli --db ./var/collector.db report

仅执行防御性公告采集与适用性核对，不包含利用、扫描或复现动作。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .engine import CollectionEngine
from .fetch import Fetcher
from .policy import Policy
from .report import Reporter
from .store import Store


def load_policy(base_url: str, input_path: str | None, credentials: str | None) -> Policy:
    local_paths: list[str] = []
    if input_path and Path(input_path).exists():
        doc = json.loads(Path(input_path).read_text(encoding="utf-8"))
        local_paths = doc.get("allowed_source_paths", [])
    # 策略发现端点：同站单次 GET，不携带任何凭据
    probe = urllib.request.Request(
        base_url.rstrip("/") + "/policy",
        headers={"User-Agent": "defensive-advisory-collector/1.0"},
    )
    try:
        with urllib.request.urlopen(probe, timeout=15) as response:
            policy_doc = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("无法获取来源 /policy，停止: %r" % exc) from exc
    return Policy.from_source(base_url, policy_doc, local_paths, credentials)


def build_engine(base_url: str, db_path: str, input_path: str | None,
                 credentials: str | None) -> tuple[Store, CollectionEngine, Policy]:
    policy = load_policy(base_url, input_path, credentials)
    store = Store(db_path)
    fetcher = Fetcher(policy)
    return store, CollectionEngine(store, fetcher), policy


def cmd_once(args) -> None:
    store, engine, policy = build_engine(args.base_url, args.db, args.input, args.credential)
    try:
        summary = engine.run_once()
        print(json.dumps({
            "run_id": summary.run_id,
            "index_revision": summary.index_revision,
            "indexed": summary.indexed_ids,
            "fetched": summary.fetched,
            "failed": summary.failed,
            "notes": summary.notes,
            "open_todo_subjects": summary.todos_opened,
            "policy": {
                "allowed_paths": list(policy.allowed_paths),
                "credential_scope": policy.credential_scope,
                "request_interval_ms": policy.request_interval_ms,
            },
        }, ensure_ascii=False, indent=2))
    finally:
        store.close()


def cmd_poll(args) -> None:
    store, engine, _policy = build_engine(args.base_url, args.db, args.input, args.credential)
    try:
        while True:
            summary = engine.run_once()
            print("[%s] run=%s fetched=%s todos=%d" % (
                time.strftime("%Y-%m-%dT%H:%M:%S"), summary.run_id,
                summary.fetched, len(summary.todos_opened)), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("收到中断，停止采集循环")
    finally:
        store.close()


def cmd_report(args) -> None:
    store = Store(args.db)
    try:
        print(Reporter(store).text_report())
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="防御性厂商公告采集客户端")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--db", default="./var/collector.db")
    parser.add_argument("--input", default="./inputs/request.json",
                        help="本地输入（含 allowed_source_paths 与资产格式示例）")
    parser.add_argument("--credential", default=None,
                        help="受限来源凭据；仅在 /policy 声明的凭据作用域内发送")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("once").set_defaults(func=cmd_once)
    poll = sub.add_parser("poll")
    poll.add_argument("--interval", type=int, default=60)
    poll.set_defaults(func=cmd_poll)
    sub.add_parser("report").set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
