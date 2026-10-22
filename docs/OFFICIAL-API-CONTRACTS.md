# 官方接口接入核查

核查日期：2026-10-22。依据为当前 NAS 官方 `/v/` 页面公开加载的前端代码；未使用账户凭据或修改真实媒体状态。公开调用代码可证明客户端使用的路径与字段，不等于当前账户／所有版本均已完成端到端验证。

实际主 Compose 的 `FNOS_URL` 为 `http://192.168.123.6:5666`。本次比较确认 5666 与 8005 的官方首页和下述关键静态文件散列相同，接口调用仍以实际 `FNOS_URL` 为准，不推断两端口的所有 API 等价。

## 音轨与播放会话

- `POST /v/api/v1/play/play`：沿用本项目既有会话创建流程，音轨使用 `audio_guid`、`audio_encoder`、`channels`，进度使用 `startTimestamp`。
- 官方前端音轨列表包含 `guid`、`index`、`language`、`title`、`codec_name`、`channel_layout`／`channels`、`is_default`。官方实现按保存的轨道、默认轨道或首轨选择，并允许空音轨标识。
- 官方还使用 `POST /v/api/v1/media/p` 的 `req=media.resetAudio`，字段包括 `audioEncoder`、`channels`、`audioIndex`、`playLink` 和 `startTimestamp`。此路径已找到公开调用，但本轮音轨选择沿用已有新建播放链接流程，减少与字幕／引擎状态的耦合。
- 本轮切轨保留当前进度与暂停状态；成功后记录本设备、当前账户的首选语言；无音轨发送空标识。需要以真实多音轨和无音轨素材验证服务器行为。
- 证据资源：`/v/assets/bf29a647181e25c03987ab8cc8f81a1a-DB2rIeMx.js` 与 `/v/assets/74c95604043427f0bee1d0e16bfa53af-DZn1lJtt.js`。文件名会随官方版本变化。

## 媒体列表与筛选

- 已确认 `POST /v/api/v1/item/list`，现有分页参数 `page`、`page_size`、`ancestor_guid`、`exclude_grouped_video`、`sort_type`、`sort_column`、`tags`。
- 官方库首页使用有限条目数；本项目改为每库 21 条、最多 3 个并发请求、60 秒账户内存缓存，入口仍可进入完整分页列表。
- 官方筛选代码出现 `type`、`genres`、`resolution`、`color_range`、`audio_type`、`locate`、`decade`、`recognition_status`、`watched`。尚未确认这些值在当前账户下的完整请求结构，不将它们猜测为年份区间或评分区间。
- 媒体全库搜索参数尚未得到明确调用证据。需要一条脱敏的官方搜索请求或可验证的接口契约；不将首页对已加载条目的过滤当作服务端全库搜索。
- 证据资源：`/v/assets/14a77ac952fc83b785b65592c335e16d-BGXwY7Sz.js` 与 `/v/assets/dba38fcc7e14373d7386c091144609ff-xCPc841t.js`。

## 收藏与观看状态

- 公开 API 注册表列出 `PUT /v/api/v1/item/favorite`、`DELETE /v/api/v1/item/favorite`、`POST /v/api/v1/favorite/list`。
- 已看操作为 `POST /v/api/v1/item/watched`，撤销为 `DELETE /v/api/v1/item/watched`。
- 尚未核实单项／批量请求体、整季作用范围及错误返回语义。按 TODO 顺序，在 F1.2 完成后继续核查 F1.3；不能仅凭路径就对用户资料执行批量写操作。

## 用户数据与暂缓项

- 既有跳过片段和弹幕 URL 配置继续按媒体 ID 共享；本轮没有重分配已有数据。新设备偏好按已获取的账户 ID／用户名分区。
- 跨设备偏好、儿童模式和权限隔离仍需服务端用户作用域设计与至少两个账户验证。
- 真实手机／平板的音轨切换、字幕、返回、后台切换和长时播放单独记录验收；不能以接口注册表或构建结果替代。
