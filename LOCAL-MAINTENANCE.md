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

当前后端 Dockerfile 仍来自上游；它尚未整合生产中额外安装的 FFmpeg、字体、前端构建和 Compose 私有 tmpfs 等配置，不能把直接构建此仓库视作现有生产环境的完整复现。

## 2026-10-20 播放记录删除修复

`/fnos/v/api/v1/play/record` 的 DELETE 请求绕过仅支持 POST 的 Flask 进度记录处理器，去掉 `/fnos` 前缀后重新匹配现有 `/v/` 代理规则。请求方法、JSON 请求体和鉴权头保留；POST 继续经过原进度记录逻辑。

持久化 Nginx 模板及容器内生效配置已同步并热加载。`nginx -t` 通过；公网无凭据 DELETE 已进入官方鉴权层，返回 JSON `Auth Failed`，不再返回 405。此检查未删除用户的实际播放记录。
