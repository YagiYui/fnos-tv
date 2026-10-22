# 正式环境搜索验证与回退

日期：2026-10-23（Asia/Shanghai）。用户指定直接验证正式环境并保留回退能力，本轮不再创建独立运行的测试环境。

## 验证结果

用户在正式环境反馈以下三项通过：搜索“出租车”有结果、可以打开详情、返回后关键词和位置正确保留。这是用户实际操作结果，并非自动浏览器验收；未提供客户端型号或浏览器版本，因此不扩展为所有手机／平板或多账户均已通过。

主机上的只读自动检查通过：

- 70 个发布文件的 HTTP SHA-256 与发布清单一致；当前静态目录和前端源码的备份均逐文件校验。
- 无登录访问私有配置接口为 HTTP 401。
- 无签名搜索为 HTTP 200、业务码 5000；依据当前部署前端的公开签名算法生成签名后，无登录搜索为业务码 -2，说明已通过签名校验并进入账户鉴权。普通查询和中文、空格、`&?+# /` 等特殊字符查询均检查；代理与官方直连响应一致。没有使用或保存用户 Cookie、账户令牌和原始媒体结果。
- 在备份目录的独立目标上使用正式静态发布工具演练“当前版 → 旧版 → 当前版”，两个版本的首页与资源清单均校验通过；没有切换线上首页。
- `nginx -t` 通过，容器健康。验证前后全部容器 ID 与启动时间一致，正式静态文件与前端源码散列未变。

本轮未修正或重新发布业务代码，未调用播放、收藏、已看状态等写操作，也未恢复或修改数据库。

## 回退材料

- 当前版本保护副本：`/vol7/1000/docker/.backups/fnos-tv-verify-search-20261023-013057`。
- 其中 `web-current/` 与 `frontend-current/` 是验证前的当前版本；`web-manifest.json`、`source-manifest.json` 为其散列清单。另存 Compose／Nginx 副本、容器身份快照和自动验证报告。
- 搜索上线前的旧版本：`/vol7/1000/docker/.backups/fnos-tv-search-20261023-001908/web-before/` 与同目录 `frontend-before/`。对应旧网页清单已生成到本轮保护副本的 `previous-web-manifest.json`。
- `rehearsal/` 保存离线恢复演练产生的副本；`manual-acceptance.json` 单独记录用户反馈，自动脚本未登录浏览器。

需撤回搜索首版时，在 NAS 主机执行下列静态发布命令。`--backup` 必须使用一个尚不存在的新目录，保留回退前的状态；先重新核对运行挂载和源清单，不能把新版本发布清单套用于旧版本。

```sh
python3 /vol7/1000/docker/fnos-tv/.stage-liveverify-20261023/deploy_static.py \
  --source /vol7/1000/docker/.backups/fnos-tv-search-20261023-001908/web-before \
  --target /vol7/1000/docker/fnos-tv/web \
  --backup /vol7/1000/docker/.backups/fnos-tv-search-manual-rollback-before \
  --manifest /vol7/1000/docker/.backups/fnos-tv-verify-search-20261023-013057/previous-web-manifest.json
```

此命令恢复网页，先资源后首页，并保留已有散列资源；随后按本批 8 个变更文件清单恢复 `frontend-before/` 中对应源码，旧版不存在的新文件单独核对后处理，不覆盖其他源码。当前保护副本保留 `web-current/` 和 `frontend-current/`，如需恢复搜索首版，可按同样流程使用 `web-manifest.json` 发布。静态回退不需要重启容器或恢复数据库；后端和 Compose 没有本轮变更，不应连带回退其他服务。

## 复验入口与剩余边界

主机脚本为 `scripts/verify_live_search.py`，依赖同目录 `deploy_static.py`，适用于已核实的 `fnos-tv` 容器与宿主端口 18005。参数为 `--service-root`、`--previous-backup`、`--release-manifest`；每次创建独立时间戳备份，拒绝覆盖已有备份。它不会读取账户凭据或自动登录浏览器，也不调用修改媒体状态的接口。

F1.2 继续保持部分完成：服务端分页／截断、筛选及完整片库列表恢复未完成。不同账户权限、剧集详情、长列表展开后的返回、多设备体验及登录过期／真实弱网交互尚未被本轮用户反馈覆盖。
