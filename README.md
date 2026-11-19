# 合成来源演练服务

这是本地生成的外部来源与独立接收端，仅用于提供数据、页面、协议与回执。仓库没有数据采集/RPA客户端，也不包含题目答案。所有单位、厂商、研究记录和门户内容均为虚构数据，不连接真实业务系统。

## 启动

安装Docker后，在项目根目录运行 docker compose up --build，服务默认监听 http://127.0.0.1:8765。若直接在主机运行，Python 3.11+执行 pip install -r requirements.txt，再运行 python -m source.app --port 8765。浏览器任务通过Playwright 1.56.0提供的Chromium测试来源页面。

## 来源说明

服务页面 / 简述当前来源。JSON与XML接口描述输入契约，页面展示来源交互；POST /__control 只用于本地演练人员切换来源修订、可用性和延迟场景。状态可用 --state /path/outside/source-state.json 选择性落盘，默认每次启动恢复初始样本。端点行为和合成记录见 source/app.py。

## 检查

运行 python -m unittest discover -s tests -v 检查来源端、原件、门户状态和合成接收端本身。涉及页面的数据源测试会启动真实Chromium。它不验证任何尚未编写的采集程序，也不代表采集业务验收已经通过。
