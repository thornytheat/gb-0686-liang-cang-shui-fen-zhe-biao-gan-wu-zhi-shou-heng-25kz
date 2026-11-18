"""分次提货存储层。

并发与一致性策略：
- WAL 模式 + 单库文件；写事务统一 BEGIN IMMEDIATE，立即取得写锁，
  两个并发提货在同一仓批上被串行化，避免“读-判-写”竞态导致超提。
- 干物质以 INTEGER 微千克（kg×10^6）存储：比较与加减都是精确整数，
  既避免 TEXT 字典序比较，也避免 REAL 浮点误差，天然保证干物质守恒与归零。
- 扣量使用带条件的 UPDATE ... WHERE remaining_micro >= ?，
  以受影响行数最终判定是否超提；扣量与开凭证在同一事务内，任一失败整体回滚。
- 幂等键 (run_id, bin, idempotency_key) 唯一：客户端重试只返回首次结果，不重复开单。
- 金额以 TEXT 存规范 Decimal 字符串（到分）。
"""
import os
import sqlite3
from decimal import Decimal as D
from pathlib import Path
from app.errors import DomainError
from .rules import quantize_dry

MICRO = 10 ** 6


def to_micro(value_decimal):
    """Decimal kg（已量化到 6 位）→ 整数微千克。"""
    return int((quantize_dry(value_decimal) * MICRO).to_integral_value())


def from_micro(micro):
    """整数微千克 → Decimal kg（6 位小数）。"""
    return D(micro) / MICRO


def _db_path():
    base = Path(os.environ.get("DATA_DIR", "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "delivery.sqlite"


def connection():
    db = sqlite3.connect(_db_path(), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def init_db():
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS delivery_bins(
              run_id TEXT NOT NULL,
              bin TEXT NOT NULL,
              price TEXT NOT NULL,
              rate TEXT NOT NULL,
              target_amount TEXT NOT NULL,
              total_micro INTEGER NOT NULL,
              remaining_micro INTEGER NOT NULL,
              cumulative_micro INTEGER NOT NULL DEFAULT 0,
              opened_amount TEXT NOT NULL DEFAULT '0.00',
              status TEXT NOT NULL DEFAULT '可提',
              created TEXT NOT NULL,
              PRIMARY KEY(run_id,bin)
            );
            CREATE TABLE IF NOT EXISTS vouchers(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              bin TEXT NOT NULL,
              seq INTEGER NOT NULL,
              idempotency_key TEXT NOT NULL,
              taken_micro INTEGER NOT NULL,
              remaining_after_micro INTEGER NOT NULL,
              amount TEXT NOT NULL,
              cumulative_micro INTEGER NOT NULL,
              cumulative_amount TEXT NOT NULL,
              created TEXT NOT NULL,
              UNIQUE(run_id,bin,idempotency_key),
              UNIQUE(run_id,bin,seq)
            );
            """
        )


def _bin_row(row):
    """把整数微千克转成 6 位小数字符串 kg 对外暴露。"""
    return {
        "run_id": row["run_id"], "bin": row["bin"], "price": row["price"],
        "rate": row["rate"], "target_amount": row["target_amount"],
        "total_dry_kg": str(from_micro(row["total_micro"])),
        "remaining_dry_kg": str(from_micro(row["remaining_micro"])),
        "cumulative_dry_kg": str(from_micro(row["cumulative_micro"])),
        "opened_amount": row["opened_amount"], "status": row["status"],
        "created": row["created"],
    }


def _voucher_row(row):
    return {
        "id": row["id"], "run_id": row["run_id"], "bin": row["bin"],
        "seq": row["seq"], "idempotency_key": row["idempotency_key"],
        "taken_dry_kg": str(from_micro(row["taken_micro"])),
        "remaining_after_kg": str(from_micro(row["remaining_after_micro"])),
        "amount": row["amount"],
        "cumulative_dry_kg": str(from_micro(row["cumulative_micro"])),
        "cumulative_amount": row["cumulative_amount"], "created": row["created"],
    }


def upsert_bins(bins):
    """登记/刷新仓批提货账户。仅在该仓批尚无凭证时允许覆盖（避免动到在途库存）。"""
    init_db()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for b in bins:
            existing = db.execute(
                "SELECT 1 FROM vouchers WHERE run_id=? AND bin=? LIMIT 1",
                (b["run_id"], b["bin"]),
            ).fetchone()
            if existing:
                raise DomainError("BIN_LOCKED", f"仓批{b['bin']}已开始提货，不可重新登记")
            total = to_micro(D(b["total_dry_kg"]))
            db.execute(
                """INSERT INTO delivery_bins(run_id,bin,price,rate,target_amount,total_micro,
                      remaining_micro,cumulative_micro,opened_amount,status,created)
                   VALUES(?,?,?,?,?,?,?, 0,'0.00','可提',?)
                   ON CONFLICT(run_id,bin) DO UPDATE SET
                      price=excluded.price,rate=excluded.rate,
                      target_amount=excluded.target_amount,total_micro=excluded.total_micro,
                      remaining_micro=excluded.total_micro,cumulative_micro=0,
                      opened_amount='0.00',status='可提',created=excluded.created""",
                (b["run_id"], b["bin"], b["price"], b["rate"], b["target_amount"],
                 total, total, b["created"]),
            )


def list_bins(run_id):
    init_db()
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM delivery_bins WHERE run_id=? ORDER BY bin", (run_id,)
        ).fetchall()
    return [_bin_row(r) for r in rows]


def get_bin(run_id, bin_name):
    init_db()
    with connection() as db:
        row = db.execute(
            "SELECT * FROM delivery_bins WHERE run_id=? AND bin=?", (run_id, bin_name)
        ).fetchone()
    if row is None:
        raise DomainError("NOT_FOUND", "仓批提货账户不存在", 404)
    return _bin_row(row)


def list_vouchers(run_id, run_bin=None):
    init_db()
    sql = "SELECT * FROM vouchers WHERE run_id=?"
    args = [run_id]
    if run_bin:
        sql += " AND bin=?"
        args.append(run_bin)
    sql += " ORDER BY bin,seq"
    with connection() as db:
        rows = db.execute(sql, args).fetchall()
    return [_voucher_row(r) for r in rows]


def find_voucher(run_id, bin_name, idem_key):
    """按幂等键取回首次凭证；不存在返回 None。"""
    init_db()
    with connection() as db:
        row = db.execute(
            "SELECT * FROM vouchers WHERE run_id=? AND bin=? AND idempotency_key=?",
            (run_id, bin_name, idem_key),
        ).fetchone()
    return _voucher_row(row) if row else None


def take_delivery(run_id, bin_name, idem_key, taken_dry, amount_fn, now):
    """原子提货：校验→条件扣量→开凭证，单事务提交。

    amount_fn(rate, prior_cum_kg, take_kg, new_remaining_kg, target, opened) -> Decimal 金额。
    返回 (voucher_dict, bin_dict, replayed)。幂等键命中时直接返回首次结果，不重复开单。
    """
    init_db()
    take_micro = to_micro(taken_dry)
    db = connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        bin_row = db.execute(
            "SELECT * FROM delivery_bins WHERE run_id=? AND bin=?",
            (run_id, bin_name),
        ).fetchone()
        if bin_row is None:
            raise DomainError("NOT_FOUND", "仓批提货账户不存在", 404)

        # 幂等：同一键重试，返回已开凭证，绝不重复开单（不改动任何余额）。
        existing = db.execute(
            "SELECT * FROM vouchers WHERE run_id=? AND bin=? AND idempotency_key=?",
            (run_id, bin_name, idem_key),
        ).fetchone()
        if existing:
            db.commit()
            return _voucher_row(existing), _bin_row(bin_row), True

        remaining_micro = bin_row["remaining_micro"]
        prior_micro = bin_row["cumulative_micro"]
        opened = D(bin_row["opened_amount"])
        if take_micro > remaining_micro:
            raise DomainError(
                "OVER_DRAFT",
                f"超提：本次{from_micro(take_micro)}kg 超过可提余量{from_micro(remaining_micro)}kg",
                409,
            )

        new_remaining_micro = remaining_micro - take_micro
        new_cum_micro = prior_micro + take_micro
        prior_cum = from_micro(prior_micro)
        take_kg = from_micro(take_micro)
        new_remaining = from_micro(new_remaining_micro)
        amount = amount_fn(
            D(bin_row["rate"]), prior_cum, take_kg, new_remaining,
            D(bin_row["target_amount"]), opened,
        )
        new_opened = (opened + amount).quantize(D("0.01"))
        status = "提完" if new_remaining_micro == 0 else "可提"

        # 条件更新作为最终并发闸门：整数余量不足则 rowcount=0，整单回滚。
        cur = db.execute(
            """UPDATE delivery_bins SET remaining_micro=?, cumulative_micro=?,
                  opened_amount=?, status=?
               WHERE run_id=? AND bin=? AND remaining_micro >= ?""",
            (new_remaining_micro, new_cum_micro, str(new_opened), status,
             run_id, bin_name, take_micro),
        )
        if cur.rowcount != 1:
            raise DomainError("OVER_DRAFT", "并发提货导致余量不足，已回滚", 409)

        seq = db.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS next FROM vouchers WHERE run_id=? AND bin=?",
            (run_id, bin_name),
        ).fetchone()["next"]
        cur = db.execute(
            """INSERT INTO vouchers(run_id,bin,seq,idempotency_key,taken_micro,
                  remaining_after_micro,amount,cumulative_micro,cumulative_amount,created)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, bin_name, seq, idem_key, take_micro, new_remaining_micro,
             str(amount), new_cum_micro, str(new_opened), now),
        )
        db.commit()
        voucher = _voucher_row(db.execute(
            "SELECT * FROM vouchers WHERE id=?", (cur.lastrowid,)).fetchone())
        refreshed = _bin_row(db.execute(
            "SELECT * FROM delivery_bins WHERE run_id=? AND bin=?",
            (run_id, bin_name)).fetchone())
        return voucher, refreshed, False
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
