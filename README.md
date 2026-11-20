# 厂商维护公告来源与防御性采集客户端

本仓库包含两部分：本地生成的外部来源服务（`source/`，提供合成页面、协议与回执），以及与之配套的防御性公告采集客户端（`collector/`，负责原文存档、适用性核对与待办跟踪）。所有单位、厂商、研究记录和门户内容均为虚构数据，不连接真实业务系统。采集端只做防御维护，不含漏洞利用、扫描或复现步骤。

## 启动

安装Docker后，在项目根目录运行 docker compose up --build，服务默认监听 http://127.0.0.1:8765。若直接在主机运行，Python 3.11+执行 pip install -r requirements.txt，再运行 python -m source.app --port 8765。浏览器任务通过Playwright 1.56.0提供的Chromium测试来源页面。

输入示例位于 inputs/request.json；业务字段、页面与协议边界见 docs/source-contract.md。采集客户端架构、联动语义与证据保留见 docs/collector.md。

## 采集客户端

来源服务启动后运行（仅使用 Python 标准库，无需额外依赖）：

```
python -m collector.cli --base-url http://127.0.0.1:8765 --db ./var/collector.db once   # 单次采集
python -m collector.cli --db ./var/collector.db poll --interval 60                       # 持续采集
python -m collector.cli --db ./var/collector.db report                                   # 值班回查报告
```

客户端遵守来源 /policy 的允许路径、跳转主机、凭据作用域与限速：跳到未经允许的外部域即停止且不携带凭据；网页与机器可读文件修订不一致时各自保留、不拼接结论；未抓到公告只产生待办，绝不判定资产安全。

## 来源说明

服务页面 / 简述当前来源。JSON与XML接口描述输入契约，页面展示来源交互；POST /__control 只用于本地演练人员切换来源修订、可用性和延迟场景。状态可用 --state /path/outside/source-state.json 选择性落盘，默认每次启动恢复初始样本。端点行为和合成记录见 source/app.py。

## 检查

运行 python -m unittest discover -s tests -v 检查来源端、原件、门户状态、合成接收端，以及采集客户端的修订/排除/撤销/接续/索引挪动/限速/跳转/资产升级联动。涉及页面的数据源测试会启动真实Chromium。测试均针对本地合成来源，不代表对任何真实系统的验收。
