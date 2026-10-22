
# 天益游 OTA 外置 Mock 服务

基于本机 MySQL 的轻量协议模拟器，按 `E:\developmentWorkspace\tyy\tianyiyou-backend\src\main\java\com\tianyiyou\ota\OtaGateway.java` 和 `OtaWorkflow.java` 的实际调用实现。仅用于开发联调；航班、酒店、门票、预订和票号都是虚构数据，不会连接真实供应商。

## 启动

需要 Python 3.9 或更新版本，以及本机 MySQL 8。安装依赖并通过环境变量配置连接信息：

```powershell
cd E:\developmentWorkspace\tyy-ota-mock
python -m pip install -r requirements.txt
$env:OTA_MOCK_MYSQL_HOST = "127.0.0.1"
$env:OTA_MOCK_MYSQL_PORT = "3306"
$env:OTA_MOCK_MYSQL_USER = "root"
$env:OTA_MOCK_MYSQL_DATABASE = "tyy_ota_mock"
$env:OTA_MOCK_MYSQL_PASSWORD = "<本机 MySQL 密码>"
python .\server.py
```

启动时会自动创建 `tyy_ota_mock` 数据库及 `offers`、`bookings`、`events` 表。默认只监听 `127.0.0.1:8787`。首次启动会生成强随机 Bearer Key 并保存到 `data\api-key.txt`；后端 OTA 配置的 `apiKey` 必须与该文件内容完全相同。也可通过 `OTA_MOCK_TOKEN` 环境变量指定。可用 `OTA_MOCK_HOST`、`OTA_MOCK_PORT`、`OTA_MOCK_MYSQL_HOST`、`OTA_MOCK_MYSQL_PORT`、`OTA_MOCK_MYSQL_USER`、`OTA_MOCK_MYSQL_PASSWORD`、`OTA_MOCK_MYSQL_DATABASE` 覆盖连接配置。存活检查：`GET http://127.0.0.1:8787/health`。

远端后端配置时，将 `baseUrl` 设置为运行中的 Quick Tunnel HTTPS 地址（例如 `https://<随机名称>.trycloudflare.com`），`apiKey` 设置为 `data\api-key.txt` 的内容，并启用 FLIGHT、HOTEL、TICKET。仅本机联调时可用 `http://127.0.0.1:8787`。后端调用会附加 `Authorization: Bearer <apiKey>`。

## 协议（与后端逐项对应）

| 方法和路径 | 后端用途 | 响应要点 |
| --- | --- | --- |
| `GET /offers?business=FLIGHT&date=2026-10-23` | 查询指定日期已保存的报价 | `{items:[offer],total,business,date}`；日期为必填 `YYYY-MM-DD`，结果包含当天全部报价（含已过期报价），按出发时间排序 |
| `POST /offers/search` | 航班/酒店/门票列表 | `{items:[offer],total}`；`business` 取 `FLIGHT`、`HOTEL`、`TICKET`。机票支持 `date/origin/destination/adults/children/infants/cabin/direct/airline/departAfter/departBefore/sort` |
| `GET /offers/{offerId}` | 报价详情 | 报价对象 |
| `POST /offers/{offerId}/reprice` | 下单前验价 | 报价对象，含 `totalCents`、`currency=CNY`、未来的 `expiresAt` |
| `POST /bookings` | 占位预订 | `requestId` 幂等，返回 `supplierOrderId`、`status=RESERVED`、金额、币种、过期时间 |
| `GET /bookings/by-request/{requestId}` | 不确定结果恢复 | 原请求对应的预订，找不到返回 404 |
| `GET /bookings/{supplierOrderId}` | 订单同步 | 当前供应商订单状态 |
| `POST /bookings/{supplierOrderId}/confirm` | 支付后确认/出票 | 更新到 `ISSUED` 并生成虚构 `ticketNumbers` |
| `POST /bookings/{supplierOrderId}/cancel` | 取消及补偿 | `CANCELLED` |
| `POST /bookings/{supplierOrderId}/refund` | 退款申请 | `REFUND_APPROVED` 和 `approvedRefundCents` |

后端 OTA 回调入口是天益游自身的 `POST /api/callbacks/ota`，使用 `X-Provider-Timestamp` 与 `X-Provider-Signature`（HMAC-SHA256，签名原文为 `timestamp + 换行 + body`，密钥为 OTA apiKey）。Mock 本身不主动回调；支付后由后端确认接口驱动模拟出票。

## 错误、幂等与可控场景

每个请求可加 `X-Mock-Scenario`：

| 场景 | 效果 |
| --- | --- |
| `HTTP_503` | 模拟供应商临时不可用，返回 503 |
| `HTTP_429` | 模拟限流，返回 429 |
| `NO_RESULTS` | 搜索返回空列表和 `total=0` |
| `SOLD_OUT` | 搜索报价库存为 0，预订返回 409 |
| `TIMEOUT` | 延迟超过后端最大 30 秒超时，用于验证超时和不确定结果恢复 |
| `BUSINESS_ERROR` | 返回 422 供应商业务错误 |
| `PRICE_CHANGED` | 重验价涨价 12 元，或占位最终金额不一致，触发平台补偿取消路径 |
| `REJECT_BOOKING` | 创建预订时返回拒绝 409 |
| `MISSING_SUPPLIER_ID` | 返回缺少供应商单号的异常响应 |
| `REFUND_PENDING` | 退款保留处理中状态 |

重复 `POST /bookings` 且 `requestId` 相同会返回同一供应商订单，不会重复创建。MySQL `events` 表记录搜索、下单、幂等重放、确认、取消、退款事件；`offers` 与 `bookings` 保存持久状态。可在 HeidiSQL 中使用 MySQL 连接查看 `tyy_ota_mock` 数据库。

机票搜索使用多航司、多时段的模拟时刻表，提供直飞和经停选项、不同价格和余票、经济舱与商务舱，以及按请求日期变动的报价。筛选航司、直飞、起飞时间和乘客数会影响结果；排序支持价格、起飞时间和飞行时长。当天已起飞的航班及余票不足的航班不会出现在普通搜索结果中。`SOLD_OUT` 场景仍返回零余票报价，以便覆盖预订失败与恢复流程。报价包含 `offerId`、航线、`departAt`、`arriveAt`、机场、航司、航班号、飞行时长、经停信息、舱位规则、价格、税费和报价有效期。预订按成人与儿童人数扣减座位，取消或批准退款后释放座位；婴儿计入乘客摘要，不占座。乘客姓名、证件号和联系人资料不会写入 Mock 数据库。

小程序往返预订按两段执行：先查询并下单去程，用户在订单页点击“继续预订返程”后，再以反向航线和返程日期发起第二次查询。Mock 的单次 `/offers/search` 仅查询请求中的出发日期，不把往返合并为一个报价。

MySQL 表的关键接口字段使用独立列保存，完整报价/订单响应和事件详情另存 JSON。SQLite 文件如本机已有，会保留但不再读写；服务不会自动迁移旧数据。

## Cloudflare Quick Tunnel

[Cloudflare 官方 Quick Tunnels 文档](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)说明无需 Cloudflare 账号或域名；启动本地 HTTP 服务后运行：

```powershell
cloudflared tunnel --url http://127.0.0.1:8787
```

终端输出的 `https://<随机名称>.trycloudflare.com` 即临时公网地址，将它作为远端后端 `baseUrl`。所有获得该 URL 的人都能尝试访问；API 除 `/health` 外需要 `Authorization: Bearer <apiKey>`。停止 `cloudflared` 后 URL 失效，Quick Tunnel 只适用于开发测试。请勿复用生产密钥。临时 URL 变化时需更新后端 OTA `baseUrl`。

## 清空本地数据

删除 MySQL `tyy_ota_mock` 数据库后重启服务，会重新创建空库和表。数据库仅包含模拟报价、虚构预订资料和接口事件记录。
# tyy-ota-mock
 c6f926a2c1ebe0dba1969b744cd8485b4d45ac44
