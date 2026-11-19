# 厂商维护公告来源协议

GET /vendor/index按page=0起分页，每页1条，返回index_revision、total、next。索引在新增公告时会挪动。厂商网页 /vendor/advisory?id=ADV-A 和旧镜像 /mirror/advisory?id=ADV-A 含来源修订及带data-sha256的机器文件链接；/machine?id=ADV-A&revision=1按请求修订提供JSON原文。版本范围为introduced包含、fixed不包含；excluded是该产品明确排除的具体版本，不能整篇公告排除。
产品runtime别名runtime-old；workbench与runtime同带engine组件，却有各自版本范围。external_ids共享外部引用不表示内部公告相同，references可指向索引尚欠抓取的公告。GET /assets按修订返回资产清单。所有公告只有防御维护范围和缓解，没有利用代码、扫描或漏洞复现步骤。
GET /policy定义允许来源路径、主机和凭据作用域。/vendor/redirect返回未经允许的外域，客户端不得跟随带凭据请求。控制exclude更新组件排除，index_shift移动索引，mismatch使机器文档与网页摘要不一致，withdraw撤销公告，offline临时503，throttle返回429 Retry-After=3，asset_upgrade更新当前资产版本。

本地来源控制通过 POST /__control 发送JSON，例如 {"event":"reset"} 可重置样本。控制切换只影响合成来源，不能用来替业务处理方决定批准、签发或确认。来源服务与自动化客户端分别承担各自责任。
