# 定制版本构建、发布与回退

仅适用于本 fork。使用已验证的前后端提交配对，原生产布局与挂载以现场核查为准。

## 构建与验证

1. 前端使用 Node 24、pnpm 11.25.0，运行 `pnpm install --frozen-lockfile`、`node --test *.test.mjs`、`pnpm build`。
2. 将配对前端的 `dist/` 放入后端构建上下文。禁止下载上游 latest 替代。
3. 在后端执行 `python scripts/release_manifest.py --backend . --frontend ../fnos-tv-web --dist dist`。发布前确认清单中的两个 dirty 字段为 false；清单记录两个源码 SHA、迁移版本和静态资源散列。
4. 使用 Dockerfile 构建新镜像，以不可变提交标签标识。`FNOS_URL` 在启动时由环境配置，发布清单不记录运行环境或任何凭据。
5. 在独立测试机或 NAS 上用 `bash scripts/test_image.sh <新镜像>` 验证。该脚本仅启动一次性、无外部网络的容器，不挂载生产数据，也不占用宿主端口。
6. GitHub 的前后端 PR／dev 工作流运行回归；镜像配对构建需要手动提供完整前端 SHA。默认只验证，只有显式设置 publish 才推送镜像。

Python 直接依赖已固定至当前生产核对版本；传递依赖与 Debian 软件包尚未做完整离线锁定，此流程保证配对源码与必需运行依赖，不承诺镜像逐字节可重复。

## 生产部署

1. 核对实际容器、镜像、Compose 文件、端口、媒体只读挂载、补丁文件挂载、`web/` 和私有 tmpfs；记录其他容器身份与启动时间。
2. 在 `/vol7/1000/docker/.backups/` 下创建独立时间戳目录，保存当前 Compose、覆盖配置、镜像标识、静态目录与补丁源码。
3. 数据库使用 SQLite backup API 或停服务后的完整副本做一致性备份；不能直接复制一个正在写入的数据库文件来声称可恢复。不得运行旧版 destructive downgrade。
4. `compose.example.yml` 仅是服务定义示例，不能覆盖已有主 Compose。将必要变更合入当前 fnos-tv 服务，核查旧补丁挂载不会遮蔽新镜像中的源码。媒体目录保持同路径只读挂载，`data/media-roots.json` 配套。
5. Compose 修改先做 `docker compose config --quiet`，仅更新 fnos-tv。私有 tmpfs 和 supervisor 命令必须保留。
6. 静态更新时先上传带散列资源，再原子切换 `index.html`，保留旧资源。后端接口变更与前端配套，避免更新期间旧页面调用不兼容接口。
7. 分别验证健康状态、Nginx 配置、资源散列、接口行为及真实客户端播放；确认无关服务身份和启动时间未变化。

## 回退

静态发布工具：`python scripts/deploy_static.py --source <配对dist> --target <实际web目录> --backup <新的备份目录> --manifest <release-manifest.json>`。它先校验资源散列并备份，再上传资源，最后原子切换首页；旧散列资源保留。恢复旧版本也使用同样流程，以已校验的备份作为 source，并为当前版本另建 backup。`tests/test_release.py` 用临时目录演练发布、回退和损坏资源拒绝切换。

一致性数据库备份函数为 `scripts.deploy_static.backup_database(source, destination)`，使用 SQLite backup API，禁止覆盖已有备份；调用前由实际部署步骤确定源与目标路径。

- 静态回退：原子恢复备份的首页，同时保留其引用的资源；恢复配套的挂载源码。
- 后端回退：恢复备份的镜像、Compose 与补丁版本，只更新 fnos-tv。
- 数据库回退：先停止 fnos-tv，确认备份版本与目标源码兼容，再恢复一致性副本；恢复后核对权限和行数。此步骤会回到备份时的数据状态，不能自动执行覆盖。
- 迁移失败时启动明确失败，不再以 `create_all` 掩盖。先检查隔离副本，修复或恢复后再启动；迁移日志不包含账户数据。

当前两个基线 migration 的 upgrade 已改为幂等、保留行数据；已在旧 head 的数据库不会重跑历史迁移。历史 downgrade 仍可能删除业务表，不用它作为生产回退路径。
