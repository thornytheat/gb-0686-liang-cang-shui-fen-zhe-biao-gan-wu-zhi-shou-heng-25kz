"""分次提货服务：从已复核运行登记仓批，编排提货与对账视图。"""
from datetime import datetime, timezone
from decimal import Decimal as D
from app import store as run_store
from app.errors import DomainError
from . import store as dlv
from .rules import as_decimal, quantize_dry, effective_rate, voucher_amount, reconcile, CENT


def _now():
    return datetime.now(timezone.utc).isoformat()


def _require_run(run_id):
    if not run_id:
        raise DomainError("BODY", "缺少 run_id")
    return run_store.fetch(run_id)


def register_from_run(run_id):
    """以一个已复核运行的 M5 仓批干物质 + M6 单价/标准水分为依据，登记提货账户。

    target_amount 取仓批内各采信车次 M6 已复核金额之和（分币守恒，与运行总额一致）。
    rate 为干物质口径有效单价 price/(1−标准水分/100)，全精度保留，不提前舍入。
    """
    result = _require_run(run_id)
    if result.get("review") != "通过":
        raise DomainError("NOT_REVIEWED", "仓批须复核通过后方可登记提货", 409)
    price = D(result["rules"]["price"])
    rate = effective_rate(price, D(result["rules"]["standard_moisture"]))
    amount_by_bin, dry_by_bin = {}, {}
    for rec in result["records"]:
        b = rec["bin"]
        amount_by_bin[b] = amount_by_bin.get(b, D("0")) + D(rec["amount"])
        dry_by_bin[b] = dry_by_bin.get(b, D("0")) + D(rec["dry_kg"])
    bins = []
    for balance in result["balances"]:
        b = balance["bin"]
        if b not in dry_by_bin:
            continue
        bins.append({
            "run_id": run_id, "bin": b,
            "price": str(price), "rate": str(rate),
            "target_amount": str(amount_by_bin.get(b, D("0")).quantize(CENT)),
            "total_dry_kg": str(quantize_dry(dry_by_bin[b])),
            "created": _now(),
        })
    if not bins:
        raise DomainError("EMPTY", "该运行没有可登记的仓批干物质")
    dlv.upsert_bins(bins)
    return overview(run_id)


def take(run_id, bin_name, idem_key, taken_raw):
    """一次提货：按实扣干物质、即时锁定凭证金额。返回凭证与最新账户。"""
    _require_run(run_id)
    if not bin_name:
        raise DomainError("BODY", "缺少 bin")
    if not idem_key or not isinstance(idem_key, str):
        raise DomainError("BODY", "缺少幂等键 idempotency_key")
    taken = as_decimal(taken_raw, "提货干物质")

    def amount_fn(rate, prior_cum, take_kg, new_remaining, target, opened):
        return voucher_amount(rate, prior_cum, take_kg, new_remaining, target, opened)

    voucher, bin_row, replayed = dlv.take_delivery(
        run_id, bin_name, idem_key.strip(), taken, amount_fn, _now()
    )
    return {"voucher": voucher, "bin": bin_row, "replayed": replayed}


def overview(run_id):
    """结算页视图：仓批可提余量 + 凭证 + 逐仓批与总额对账。"""
    _require_run(run_id)
    bins = dlv.list_bins(run_id)
    vouchers = dlv.list_vouchers(run_id)
    grouped = {}
    for v in vouchers:
        grouped.setdefault(v["bin"], []).append(v)
    bin_views, grand_target, grand_opened, all_closed, all_matched = [], D("0"), D("0"), True, True
    for b in bins:
        rows = grouped.get(b["bin"], [])
        target = D(b["target_amount"])
        opened = D(b["opened_amount"])
        rec = reconcile(target, opened, b["remaining_dry_kg"])
        grand_target += target
        grand_opened += opened
        all_closed = all_closed and rec["closed"]
        all_matched = all_matched and rec["matched"]
        bin_views.append({
            **b, "voucher_count": len(rows), "vouchers": rows, "reconcile": rec,
        })
    return {
        "run_id": run_id,
        "bins": bin_views,
        "vouchers": vouchers,
        "totals": {
            "target_amount": str(grand_target.quantize(CENT)),
            "opened_amount": str(grand_opened.quantize(CENT)),
            "diff_amount": str((grand_target - grand_opened).quantize(CENT)),
            "all_closed": all_closed and bool(bins),
            "all_matched": all_matched and bool(bins),
        },
    }
