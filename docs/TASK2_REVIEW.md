# 成员 2 最终检查记录

本记录检查当前接收交付，不将成员 3 的状态分析或成员 4 的仪表盘视为已完成，也不保证分数。

## 已执行检查

| 检查 | 结果与证据 |
|---|---|
| 队友 main 全部文件 | 32 个原始文件清单、完整 CSV 解析、事件重建；`member1_review.json` |
| 正式全量 MQTT 回放 | 2,452 条；原发布器；不是 JSONL 直接导入数据库 |
| 最小发布间隔 | 本轮高精度实测 0.1004622 秒；`audit_full_publisher.log` |
| 每条数据内容 | ID 和 payload 全部相等，接收顺序与源一致；`full_verification.json` |
| 事件数量 | price 433、start 71、meter 1,886、end 62；无源事件被拒收 |
| A1 关联 | 每条 `station_id` 与真实 `ev_id` 一致，全部可关联原站点 |
| A1 保留 | 原有 11 张表双向 EXCEPT ALL 差异均为零；`a1_preservation.json` |
| 契约与存储单元测试 | 本轮 8 项通过，包含全量 2,452 条契约检查、异常字段、重启去重、真实 FK、非法 Unicode 和带时区时间读取 |
| 消息故障恢复 | 错 topic、重复、畸形、retained、broker 重启、subscriber 重启；`recovery_verification.json` |
| 队列溢出 | 测试时人为延迟 writer，触发有界队列报错停止；重放后数据完整 |
| Broker 不可用 | 明确报重试，限时测试未获得 SUBACK 时返回失败；`unavailable_summary.json` |
| Mosquitto 实测 | Docker Compose 配置启动成功；四种事件均经过 MQTT 入库；`mosquitto_verification.json` |
| Mosquitto 离线队列 | 离线发布四条，重启 broker 后用同一 client ID 补送，均正确去重；`mosquitto_offline_recovery.json` |
| 环境依赖 | Python 3.12.14；全新虚拟环境仅安装 requirements.txt 后 8 项测试通过；pip check 无损坏依赖 |
| 下一位同学的查询 | `sql/handoff_queries.sql` 四项示例实测 433、9、433、1,886 行，完成会话合计 62 |
| 文档与源码 | 提供 README、中文交接、报告草稿、DDL、验证工具；语法编译及 git diff 检查 |

证据文件位于 `verification/task2/`。正式库保留所有 1,958 个 A1 站点，其中 433 个 DC 站点收到初始价格；回放末 9 个会话保持活跃。独立故障测试数据库位于 Git 排除的 scratch 中，不污染正式库。

## 检查时发现并修正的问题

1. main 没有原 A1 数据库，只有 CSV；从本地已交付的 A1 包复制真实关系库，添加同 schema 接收表和外键，保留原始 A1 文件。
2. Windows/Python 3.12 粗精度 `monotonic()` 导致原发布器间隔日志显示 0.093 秒；改用 `perf_counter()` 进行测量并重新完整回放，未缩短 0.1 秒 sleep。
3. 超大 JSON 整数可能使有限数检查溢出；已作为非法输入隔离，单元测试覆盖。
4. ACK 线程与重连可能同时访问连接状态；增加连接锁，防止旧连接 packet ID 被错误确认到新连接。
5. 未成功订阅的限时运行原本可能返回成功；现在检测从未收到 QoS 1 SUBACK，并返回失败。
6. 退出时如果写入队列无法排空，使用有限等待并报告失败；不无限等待队列槽位。
7. 本轮复核发现 JSON 可转义非法 Unicode 代理字符，原来会导致 writer 停止；已改为隔离，并通过真实 MQTT 异常消息测试。
8. 本轮复核发现 Python 获取 DuckDB TIMESTAMPTZ 需要 pytz；已加入 requirements.txt，并在全新虚拟环境验证时间列获取和全部单元测试。

## 本轮评分对照与交接复核

本轮重新读取作业说明、FAQ 和评分标准，并重新执行测试，不仅沿用先前记录。与成员 2 直接对应的 **MQTT Data Subscription / Excellent (10 分)** 要求是正确订阅和解码、在同一 schema 正确关联 A1 站点。本实现以 broker 实际接收、严格解码、`core.mqtt_event` 外键及完整 payload 对比提供证据。当前已执行检查未发现该接收范围内未解决的阻断问题；最终分数仍由老师评定。

存储分析 20 分和仪表盘 20 分属于全组后续集成，当前 inbox、外键和初步索引不能单独证明这两项达到 Excellent。成员 3 必须完成状态模型、历史指标、实际查询的物理设计评估；成员 4 必须完成动态地图、详细弹窗和活跃会话实时曲线。报告与代码质量也是全组评分，需以最终整合交付评估。

README 已增加成员 3 的表字段输入契约、完整的交接 SQL、已填充 inbox 的派生回填要求、事务与 ACK 顺序、按事件时间与连接器维护状态的规则，以及成员 3/4 的六项共同验收清单。没有将尚未实现的实时 API 或地图写成已经完成。

本轮证据：`audit_unit_tests.log`、`audit_clean_install.log`、`audit_clean_tests.log`、`audit_recovery.log`、`audit_packaged_database.json`、`audit_a1_preservation.log`、`handoff_query_verification.json`；全新 A1 副本的 MQTT 回放另见 `audit_full_verification.json`、`audit_full_summary.json` 和配套日志。此前 Mosquitto 测试记录仍是此前部署测试，本轮没有启动 Docker。

## 明确边界与待全组联调

- 本包完成订阅、校验、真实 A1 关联、可靠 inbox、异常隔离、恢复和可复现证据。它是成员 3 派生状态与分析表的输入，不代替成员 3 的完整状态模型。
- DuckDB 单写服务与前端查询方式必须由成员 3/4 统一；当前没有宣称已有 API、地图、marker popup 或实时折线图。
- 当前三小时窗口价格不变化；用占用与实时曲线展示动态。需要价格变化必须与成员 1 一起另建并验证更长窗口。
- 保留事件时间与接收时间；晚到/缺少开始的会话由状态层处理，不能将到达顺序直接当成当前状态覆盖顺序。
- 历史指标只覆盖接收窗口；累计读数不可相加；未完成会话不可补造结束。
- Broker 持久化不能替代端到端核验；异常崩溃后保守做法仍是全窗重放和持久化去重。
- 当前测试不模拟操作系统突然断电、磁盘损坏或无限量负载；不把有限测试写成绝对可靠承诺。
- 最终全组报告、贡献说明、genAI 使用报告、仪表盘截图与最终 ZIP 需要共同合并。

## 本地交付

本地路径 `G:/COMP5339/5339-assignment2`，独立分支 `task2-mqtt-ingestion`。修改应通过该分支供队友审查，尚未 push 或合并 main。所有生成协助应如实披露；学生需要理解并能解释提交的代码。
