# FNOS TV 项目维护入口

这是 FNOS TV 本地项目的主仓库。项目由两个独立 Git 仓库组成，一次需求可以同时涉及两者。

## 仓库与上下文

- 当前仓库 `fnos-tv/`：Python/Flask 后端、Nginx 模板、字幕与弹幕服务。
- 同级仓库 `../fnos-tv-web/`：Vue 3、Vite、Naive UI、ArtPlayer 前端。
- 先阅读本仓库 `LOCAL-MAINTENANCE.md` 并检查 Git 状态。涉及页面、播放器或前后端联动时，再读取前端的 `AGENTS.md`、`LOCAL-MAINTENANCE.md` 和相关源码。
- Windows 本地工作区为 `E:/YagiYui/Documents/Repositories/`。正式修改在这两个仓库进行；日期命名的 Codex 调试目录只作为历史证据。
- 新对话通过仓库文档、代码和当前部署状态接手，不假定拥有旧对话的完整上下文。

## 修改与协作

- 先定位实际生效的容器、挂载、配置和请求链路，再修改。历史路由和端口需要重新核对。
- 用户要求先确认界面方案时，先给出具体方案；已确认的方案直接执行后续修改和验证。
- 按当次任务范围完成本地修改、部署、提交和推送，明确哪些步骤已完成、哪些仍待执行。
- 保护用户未提交的改动，避免整仓覆盖。跨仓修改分别检查差异并记录对应版本。
- 页面构建成功、HTTP 请求可达、真实客户端播放成功属于不同验证结果，分别说明。

## 后端与接口

- 优先沿用既有 Flask、字幕、弹幕和外部播放实现，避免覆盖定制功能。
- `config/nginx.conf.template` 是持久化 Nginx 模板；检查 `config/entrypoint.sh` 的渲染方式，不把只改容器内生成文件当作持久修复。
- `/fnos/v/api/v1/play/record` 的 POST 经 Flask 记录进度；DELETE 去掉 `/fnos` 后进入现有 `/v/` 官方代理。保留方法、请求体、查询参数和鉴权头，避免把 DELETE 改为 POST。
- `FNOS_URL` 以当前环境与 Compose 为准；不把历史的 `8005`、`5666`、`5667` 等端口混为同一入口。
- 不在源码、日志、测试数据或文档中写入账号密码、Authorization、Authx、Cookie、OpenToken 或临时播放票据。

## 验证

- 根据改动选择必要检查，后端测试说明见 `tests/README.md`。
- 标准库回归入口：`python tests/test_backend.py`、`python tests/test_ass_support.py`、`python tests/test_manual_danmaku.py`。
- `test_external_play.py` 需要 Flask/requests 与隔离的本地 HTTP 测试环境。
- `test_hardening.py` 会启动测试服务与 Nginx，只能在符合 `tests/README.md` 条件的隔离环境运行，禁止在生产容器运行。
- Nginx 修改先校验配置再热加载；Compose 修改先做配置校验，再只重建必要服务。

## NAS 部署与回退

- NAS SSH 目标为现有 `192.168.123.6` 主机配置；实际端口、身份和可达性以本机 SSH 配置为准。
- 服务目录 `/vol7/1000/docker/fnos-tv`，主 Compose `/vol7/1000/docker/docker-compose.yml`，容器名 `fnos-tv`；部署前重新核对。
- 运行态通常使用 `patches/nginx.conf.template`、`patches/frontend/` 与 `web/`。通过 `docker inspect` 确认当前绑定关系后再同步。
- 变更前备份到 `/vol7/1000/docker/.backups/` 下的独立时间戳目录，保留明确的配置或静态资源回退路径。
- 沿用现有 `/vol7/1000/docker` 布局，只更新本服务所需文件，避免影响其他服务；检查无关容器身份和启动状态。
- 静态发布先复制带散列的资源，最后原子切换 `index.html`，保留旧资源供已有页面加载；仅需静态更新时无需重启容器。
- 仓库 Dockerfile 尚不能完整复现现有生产定制环境，不能直接用上游镜像覆盖部署。具体差异见 `LOCAL-MAINTENANCE.md`。

## Git 与交付

- 两个仓库分别维护 `dev` 分支。`origin` 是 YagiYui 的 fork，`upstream` 是 thshu 上游；推送前核对真实远端与分支。
- 只暂存本次相关文件；不提交运行数据、凭据、`node_modules`、`dist`、影片素材、临时文件或部署备份。
- 不强推，不擅自推送 upstream。需要提交时沿用既有作者约定，不修改全局 Git 身份。
- 更新对应维护说明中的长期有效决策、验证边界和部署步骤，交付时报告提交、推送、部署及人工验证状态。
