# 防御性公告采集客户端（collector/）

采集端只做三件防御性工作：**取得公告原文、保留来源与适用关系依据、把资产与公告条款核对**。
不包含漏洞利用、扫描、复现步骤，也不会把"没抓到公告"当成资产安全。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `version.py` | 版本解析；introduced 含、fixed 不含的区间判断；excluded 具体版本排除 |
| `policy.py` | 来源 `/policy` 约束：允许路径、跳转主机、凭据作用域、限速、页面引用动态授权 |
| `fetch.py` | 受限 GET：不自动跟随跳转、外域拦截、凭据不离开作用域、429/5xx 分类 |
| `parse.py` | 解析网页锚点（修订、状态、机器文件 `data-sha256`）与机器可读 JSON |
| `store.py` | SQLite 原件存档（按哈希，永不覆盖）、观察时刻/地址、适用关系、评估、待办 |
| `engine.py` | 采集编排与全部联动规则 |
| `report.py` | 值班员回查视图：资产结论、证据链、待办（区分采集欠缺与来源状态） |
| `cli.py` | `once` / `poll` / `report` 命令行入口 |

## 关键语义（与来源契约一一对应）

1. **多组件条款各自独立**：一篇公告的每个产品标识/组件单独绑定区间、排除与缓解；
   某组件被明确排除只撤该组件关联，同篇其他条款仍有效。
2. **产品别名与内嵌组件**：`runtime-old` 经别名匹配 `runtime`；发行包内嵌的同名组件
   按组件版本核对，依据中注明匹配路径，不能只看包名或标题。
3. **外部编号不去重**：共享 `external_ids` 不等于同一公告；去重以内部公告标识+修订为准，
   `Store.advisories_sharing_external_id` 只把它们并列展示。
4. **镜像不覆盖更新原文**：镜像复制的是来源旧修订，与厂商更新原文按修订分别存档。
5. **网页与机器文件不一致时保留分歧**：哈希或修订对不上时，两份来源各自留档、登记
   `digest_mismatch` 待办，不拼接为权威结论，机器内容暂不用于适用性判断。
6. **索引覆盖**：分页逐页取全并检测抓取期间的索引修订挪动；`references`/`replaced_by`
   引用而索引未覆盖的公告，通过工作队列做引用闭包补取。
7. **来源状态后果分离**：
   - 503/offline、429 → `fetch_failed` 采集欠缺待办，不动既有结论；
   - withdrawn → 正式撤销，撤下该公告全部关联；
   - replaced → 旧公告关联撤下并补取接续公告；
   - 外域跳转 → `redirect_blocked`，停止且不携带凭据。
8. **资产升级整体重算**：清除旧评估后按当前资产版本重新核对，不沿用旧包版本结论。
9. **无公告 ≠ 安全**：没有任何已取得条款可核对的资产记 `unknown` 与覆盖待办，
   报告中明确写"待核对，不代表版本安全"。

## 证据保留

- `artifacts` 表按 SHA-256 保存网页与机器文件原件，内容不变即复用、变化即新增，无就地覆盖；
- `observations` 记录每次观察的 URL、来源类型、状态码、哈希与 UTC 时刻；
- `page_documents` / `machine_documents` 分别保存网页锚定修订与机器内容自报修订；
- `applicability` 保存每条区间/排除条款及其失效原因；`evaluations` 保存逐资产判断依据；
- `Reporter.evidence_chain(advisory_id, revision)` 可从结论回查到原件哈希与观察记录。

## 运行

```bash
# 先启动合成来源（见仓库 README），再执行单次采集
python -m collector.cli --base-url http://127.0.0.1:8765 \
  --db ./var/collector.db --input ./inputs/request.json once

# 持续采集（默认 60 秒一轮，处理来源修订与资产清单变更）
python -m collector.cli --db ./var/collector.db poll --interval 60

# 输出值班报告
python -m collector.cli --db ./var/collector.db report
```

`--credential` 提供的受限凭据只会在来源 `/policy` 声明的 `credential_scope`
（演练中为 `/vendor/`）路径内发送；镜像、机器文件与任何跳转后的请求都不携带。
