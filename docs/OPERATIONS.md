# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。

M8 分次提货在同一数据卷新增 delivery.sqlite（WAL 模式，含 -wal/-shm 伴随文件，备份需三者一并拷贝）。提货写事务 BEGIN IMMEDIATE 串行化：超提与并发余量不足返回 409 OVER_DRAFT，未复核登记返回 409 NOT_REVIEWED，已开凭证仓批重复登记返回 422 BIN_LOCKED，未知仓批 404 NOT_FOUND，参数缺失/非正返回 422。服务为 gunicorn 单进程多线程，SQLite 写锁保证跨线程不超提；若未来扩为多进程，应保留单库文件并依赖 busy_timeout 与 IMMEDIATE 事务。
