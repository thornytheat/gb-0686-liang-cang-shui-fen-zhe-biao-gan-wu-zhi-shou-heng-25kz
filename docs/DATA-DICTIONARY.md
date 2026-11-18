# 字段与计算定义

id、bin为字符串，保留前导零；version为正整数。gross、tare为毛重与皮重，unit仅kg或t。净重先换算kg，且毛重不小于皮重。
moisture、impurity为百分数，取值[0,100)，水分超过max_moisture拒收。标准水分必须小于收购上限且低于100。
净粮=净重×(1-杂质/100)，干物质=净粮×(1-水分/100)，折标重=干物质/(1-标准水分/100)。
单价CNY/kg，按未舍入折标重计算总额，再以最大余额法分配分币，余数相同按id升序。全部金额和重量为Decimal，JSON以字符串输出。
同键最高版本采信；同版数值不一致时该键所有行隔离，完全相同则保留最早行。旧版与重复行计入隔离，保证输入行数平衡。
时间仅用于运行清单，保存UTC偏移。空值、哨兵、零分别处理，空文件/仅表头/全空行返回不同原因码。规则参数纳入批次哈希。

## M8 分次提货

提货账户 `delivery_bins` 以 (run_id,bin) 为主键，独立存于 `delivery.sqlite`。仅可对 review=通过 的运行登记；已产生凭证的仓批拒绝重新登记（BIN_LOCKED）。
- price 为折标口径单价，rate 为干物质口径有效单价 = price/(1−标准水分/100)，全精度保存不提前舍入；price×standard_kg ≡ rate×dry_kg。
- target_amount 为仓批内各采信车次 M6 金额之和（到分）；total/remaining/cumulative 干物质内部以 INTEGER 微千克（kg×10^6）存储，对外按 6 位小数字符串输出。
- 凭证 `vouchers`：(run_id,bin,seq) 与 (run_id,bin,idempotency_key) 双唯一。seq 在仓批内从 1 递增；amount 到分，cumulative_amount 为含本张的累计已开金额。
- 金额锁定采用累计差额法：非末次 amount = 应计累计(rate×累计干物质, 到分) − 已开累计；末次（扣后余量为0）amount = target_amount − 已开累计，由末张吸收分币尾差，保证 Σamount ≡ target_amount。
- 状态 status 为 可提/提完；reconcile.closed 要求剩余干物质为0，matched 进一步要求 opened_amount = target_amount、diff_amount = 0.00。
- 写事务 BEGIN IMMEDIATE 串行化，扣量用 `UPDATE ... WHERE remaining_micro >= 本次微千克` 并以 rowcount 终判，超提 OVER_DRAFT(409)，与开凭证同事务回滚；同 idempotency_key 重试返回首单（replayed=true），不重复开单扣量。
