# 深度登记 粮仓水分折标与干物质守恒

- 规模：中型
- 核心规则：保留干物质：标准重量=净重×(1-实测水分)/(1-标准水分)，杂质先扣，超限退收，舍入只在结算末端
- 使用者与工作流：粮库结算员；接收多车次化验与地磅文件，复核折标数量后向财务结算

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 磅单接收 | 接收磅单并检查结构 | `/api/m1` | `app/modules/m1_intake/rules.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 水杂契约 | 校核水杂和单位 | `/api/m2` | `app/modules/m2_contract/rules.py` `app/modules/m2_contract/service.py` | `tests/test_m2_contract.py` |
| M3 折标计算 | 扣杂后按干物质折标 | `/api/m3` | `app/modules/m3_dryweight/rules.py` `app/modules/m3_dryweight/service.py` | `tests/test_m3_dryweight.py` |
| M4 车次归并 | 按版本裁决冲突磅单 | `/api/m4` | `app/modules/m4_truckmerge/rules.py` `app/modules/m4_truckmerge/service.py` | `tests/test_m4_truckmerge.py` |
| M5 仓批平衡 | 逐仓核对干物质平衡 | `/api/m5` | `app/modules/m5_balance/rules.py` `app/modules/m5_balance/service.py` | `tests/test_m5_balance.py` |
| M6 结算复核 | 结算到分并保留尾差依据 | `/api/m6` | `app/modules/m6_settlement/rules.py` `app/modules/m6_settlement/service.py` | `tests/test_m6_settlement.py` |
| M7 批次追溯 | 检查行数平衡与证据摘要 | `/api/m7` | `app/modules/m7_trace/rules.py` `app/modules/m7_trace/service.py` | `tests/test_m7_trace.py` |
| M8 分次提货 | 按实扣干物质分次提货、即开即锁凭证、末次对账 | `/m/8` `/api/m8` | `app/modules/m8_delivery/rules.py` `app/modules/m8_delivery/store.py` `app/modules/m8_delivery/service.py` | `tests/test_m8_rules.py` `tests/test_m8_delivery.py` `tests/test_m8_http.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | 首列BOM | 主键丢失 | utf-8-sig解码 | `app/normalize.py` | `tests/test_m1_intake.py::test_bom_header` |
| TRAP-06 | 全角千分位 | 数值报错 | NFKC后Decimal | `app/normalize.py` | `tests/test_m2_contract.py::test_thousands_fullwidth` |
| TRAP-07 | 空值哨兵 | 误当0 | 逐行隔离 | `app/modules/m2_contract/rules.py` | `tests/test_m2_contract.py::test_sentinel_not_zero` |
| TRAP-08 | 前导零 | 编号变化 | 字符串保形 | `app/modules/m1_intake/rules.py` | `tests/test_m1_intake.py::test_real_stage_preserves_zero_id` |
| TRAP-10 | 微量累加 | 浮点偏差 | Decimal累计 | `app/modules/m5_balance/rules.py` | `tests/test_m5_balance.py::test_exact_decimal_sum` |
| TRAP-14 | 同版矛盾 | 后写覆盖 | 同版均隔离 | `app/modules/m4_truckmerge/rules.py` | `tests/test_m4_truckmerge.py::test_conflict_keeps_neither` |
| TRAP-15 | 吨与千克 | 数量差千倍 | 统一kg | `app/modules/m2_contract/rules.py` | `tests/test_m2_contract.py::test_unit_ton` |
| TRAP-24 | 结算尾差 | 分录不守恒 | 最大余额分到分 | `app/modules/m6_settlement/rules.py` | `tests/test_m6_settlement.py::test_largest_remainder_conservation` |
| TRAP-31 | 分次凑分尾差 | 各次独立四舍五入，累计与仓批复核额差几分 | 累计差额法锁定，末次钉到 target 吸收尾差 | `app/modules/m8_delivery/rules.py` | `tests/test_m8_rules.py::test_final_voucher_pins_to_target_absorbs_residual` |
| TRAP-32 | 库存单位口径 | 按折标重扣量导致干物质不守恒 | 干物质为唯一库存，rate=price/(1−标准水分/100) 换算金额 | `app/modules/m8_delivery/rules.py` | `tests/test_m8_rules.py::test_effective_rate_matches_settlement_basis` |
| TRAP-33 | TEXT 数值比较 | 字符串字典序使 '99'<'100'，条件扣量误判 | 干物质改 INTEGER 微千克精确整数比较 | `app/modules/m8_delivery/store.py` | `tests/test_m8_delivery.py::test_take_deducts_dry_matter_and_locks_voucher` |
| TRAP-34 | 并发超提 | 两人同时读余量后都放行 | BEGIN IMMEDIATE 串行 + 条件 UPDATE rowcount 终判 | `app/modules/m8_delivery/store.py` | `tests/test_m8_delivery.py::test_concurrent_takes_never_over_draw` |
| TRAP-35 | 重试重复开单 | 网络重试产生第二张凭证、二次扣量 | 幂等键唯一约束，重放返回首单 | `app/modules/m8_delivery/store.py` | `tests/test_m8_delivery.py::test_concurrent_same_idempotency_key_creates_one_voucher` |
| TRAP-36 | 部分生效 | 开单成功但扣量失败（或反之） | 扣量与开单同事务，异常整体回滚 | `app/modules/m8_delivery/store.py` | `tests/test_m8_delivery.py::test_over_draft_rejected_and_nothing_changes` |

## 对账规则

输入逻辑行数 = 采信行数 + 隔离行数；空行单列。每条重复、冲突、超限、缺失均保留原始行号与原因码。计算阶段的输出数量可聚合，原始采信records始终保留，业务平衡另按模块给出的重量、时长、电量或座席数量核对。
相同输入摘要、规则版本、参数产生相同批次ID。运行原子插入SQLite；复核使用整数版本乐观锁。
M8 提货另立 delivery.sqlite：干物质微千克守恒，分次实提之和加剩余等于总量，提完剩余为0；凭证总额对账到 M6 仓批复核金额，diff_amount 恒为 0.00。提货写事务原子提交，幂等键保证重试安全。

## 已知不覆盖

单一粮种，标准水分和单价由批次参数给出。不处理跨粮种价差、税费和在线地磅接入。4MB文件上限。
