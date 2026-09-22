# 馆际作品借展

处理复合作品信息、借展约束、状态交接与策展引用，服务于「两位画家会面百年」纪念展这类多馆协作场景。

`fixtures/domain.json` 保存领域名词与核心不变量，便于接口联调时保持一致语义。运行 `python3 service.py --check` 可检查基础配置；执行 `npm test` 运行全部领域与 HTTP 契约测试。

## 领域结构

| 概念 | 说明 |
| --- | --- |
| 作品实体 | 独立作品、合作画、历史图录、长卷，独立于普通藏品表建档 |
| 组成区段 | 长卷按引首/画心/拖尾/题签等分节，有自己的顺序与权属 |
| 作者贡献 | 归属到整作或具体区段，记录角色（绘/题跋/题签/鉴藏）与合作顺序 |
| 历史展览 | 每件作品的既往著录 |
| 当前权属 | 整作权属与分段权属可并存（同一长卷分属多家博物馆） |
| 借展协议 | 约束展期、展厅、照度上限、运输、保险、数字传播用途；改期与条款修订均留痕 |
| 交接事件 | 出库、到馆、布展、撤展、归还五段，双方签认才完成 |
| 损伤风险 | 报损即冻结；前后图像哈希必须同时登记，解除后才恢复流转 |
| 展签版本 | 草稿 → 已发布；发布时锁定证据快照及 SHA-256 哈希，更正另立版本 |

## 核心规则

- **分段立约**：协议范围是「作品 + 可选区段」列表；同一长卷的不同区段可由不同出借馆签订不同条款（照度、保险、数字用途等），覆盖范围重叠返回 `409`。
- **扫码幂等**：同一扫码码全局对应唯一交接事件，重复扫码回显原事件并带 `already_scanned: true`，不产生第二次交接。
- **按序签认**：五段交接严格有序，每段必须由交出方、接收方两方代表分别签认；待签认期间不得另发同段扫码。
- **展期约束**：交接日期必须落在协议当前展期内；跨馆改期后旧展期扫码被拒，原始展期保留在 `initial` 与 `revisions` 中。
- **布展校验**：展厅必须在协议清单内，登记照度不得超过协议上限。
- **损伤冻结**：未解除风险存在时，该作品一切后续交接返回 `409`；风险可定位到区段，前后图像哈希随风险永久保全。
- **证据快照**：展签发布时深拷贝所引作品的区段、贡献、权属、历史展览、作品关系与协议条款，计算 SHA-256；之后的学术更正（新增作者、改期、权属更新）不改写旧版。
- **追踪链**：`GET /labels/{id}/trace?version=N` 从任一已发布展签版本定位到实体、贡献区段、当前状态与保管责任方、当前授权范围（展厅/照度/运输/保险/数字用途）、各区段最新状况注记与未解除风险。

## HTTP 接口

写请求为 `POST` + JSON 体（创建返回 `201`），查询为 `GET`；违反领域规则返回 `400/404/409` 与 `{"error": "..."}`。

```
POST /artworks                                 建档；category=独立作品/合作画/历史图录/长卷
POST /artworks/{id}/segments                   增加组成区段
POST /artworks/{id}/contributions              增加作者贡献（segment_id 可空，role/order）
POST /artworks/{id}/historical-exhibitions     增加历史展览
POST /artworks/{id}/ownership                  登记整作或某区段权属
GET  /artworks/{id}                            作品完整视图
GET  /artworks/{id}/state                      当前状态、保管责任、展厅、冻结标记
POST /relations                                作品间叙事关系（供策展引用）

POST /agreements                               立约（scope 为作品/区段列表 + 六类条款）
POST /agreements/{id}/reschedule               跨馆改期（留修订痕）
POST /agreements/{id}/amend                    修订展厅/照度/运输/保险/数字用途
GET  /agreements/{id}                          协议视图（含 initial 与 revisions）

POST /handovers                                扫码发起交接（stage/code/双方在签认时给出）
POST /handovers/{id}/sign                      交接一方签认（party/actor）
GET  /handovers/{id}                           交接事件

POST /damages                                  报损（artwork_id/segment_id/前后图像哈希）
POST /risks/{id}/resolve                       解除风险（resolution/actor）

POST /labels                                   创建展签草稿
POST /labels/{id}/publish                      发布并锁定证据快照
POST /labels/{id}/revise                       基于已发布版另立更正草稿
GET  /labels/{id}                              全部版本
GET  /labels/{id}/trace?version=N              展签 → 实体/区段/责任/授权/风险 追踪
GET  /health                                   服务身份
```

当前实现为进程内内存登记处（`domain.LoanRegistry`），接口契约与存储无关；持久化落地时替换注册表实现即可。
