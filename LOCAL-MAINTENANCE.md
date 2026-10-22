# 本地维护说明

此仓库于 2026-10-20 从 YagiYui/fnos-tv 干净克隆，并迁入本次会话的定制源码。

- 当前工作分支：`dev`，迁入基线：`1c73184a871271f1adea8cca54254a66d3d95100`。
- `origin` 指向个人 fork；`upstream` 指向 thshu/fnos-tv。
- 前后端分别维护，另一个仓库位于同级目录。定制修改维护在 dev 分支，后续提交推送到个人 fork 的 origin/dev。
- 原始调试目录保留于 `E:/YagiYui/Documents/Codex/2026-10-19/new-chat/work/fnos-tv-debug/fnos-tv/thshu-fnos-tv-1c73184`；这是历史来源，今后应在本仓库修改。
- 运行数据、账号凭据、临时播放票据、node_modules、dist 和历史部署压缩包不纳入源码迁移。
- public/vendor 内的字幕渲染资源、字体及许可证属于必要发布资源，已保留。

## 验证与发布边界

前端可用锁文件安装依赖：`pnpm install --frozen-lockfile`，再执行 `node --test *.test.mjs` 和 `pnpm build`（PowerShell 需先展开测试文件列表）。后端标准库测试见 tests/README.md。

初次迁移只同步了本地源码，未修改 NAS、构建发布镜像或推送 GitHub。生产部署仍在 `/vol7/1000/docker/fnos-tv`，不能直接用上游镜像更新来覆盖定制配置。后续需配套前后端版本构建和部署，并保留回退版本。

2026-10-22 已补齐 Dockerfile 的 FFmpeg、字体与字体工具、私有 tmpfs 的 Compose 示例、配对前端构建和隔离集成。发布需使用明确的前后端提交及资源清单，不能重新下载上游 latest；现有 NAS 补丁挂载和媒体只读挂载仍需现场核查。传递依赖与 Debian 包未做完整离线锁定，不承诺镜像逐字节复现。

## 2026-10-20 播放记录删除修复

`/fnos/v/api/v1/play/record` 的 DELETE 请求绕过仅支持 POST 的 Flask 进度记录处理器，去掉 `/fnos` 前缀后重新匹配现有 `/v/` 代理规则。请求方法、JSON 请求体和鉴权头保留；POST 继续经过原进度记录逻辑。

持久化 Nginx 模板及容器内生效配置已同步并热加载。`nginx -t` 通过；公网无凭据 DELETE 已进入官方鉴权层，返回 JSON `Auth Failed`，不再返回 405。此检查未删除用户的实际播放记录。

## 2026-10-22 TODO 第一批实现

进度记录先验证请求，再转发官方接口；仅成功结果写入辅助数据库。本地写失败保留官方成功，不自动重试 POST。配置列表按传入字段在媒体范围内事务替换，空列表表示清空；失败回滚，未传字段保留。

旧迁移 upgrade 已改为非破坏、幂等初始化，保留旧记录并补充缺失列；已在 head 的数据库不重跑。迁移失败明确停止启动。历史 downgrade 仍可能删表，回退使用一致性备份。Flask 请求结束统一移除 scoped session。

发布工具与回退约定见 `deploy/README.md`；新增真实路由、迁移、静态发布／回退和 SQLite 备份回归。完整 Linux 集成必须运行在一次性容器，不挂载生产数据。

本轮核对的实际 `FNOS_URL` 为主 Compose 中的 `http://192.168.123.6:5666`。8005 与 5666 本次返回相同官方首页和关键静态代码，不据此将两者视为所有接口等价。后续仍以实时 Compose 为准。
