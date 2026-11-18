"""M8 分次提货服务与存储集成测试：守恒、对账、幂等、并发不超提。"""
from decimal import Decimal as D
import threading
import pytest
from app.pipeline import run
from app import store as run_store
from app.errors import DomainError
from app.modules.m8_delivery import service, store as dlv


@pytest.fixture
def reviewed_run(isolate):
    result = run()
    rid = result["manifest"]["run_id"]
    run_store.review(rid, result["version"], "通过")
    return rid


def _bin(rid, name):
    return next(b for b in service.overview(rid)["bins"] if b["bin"] == name)


def test_register_requires_review(isolate):
    result = run()
    rid = result["manifest"]["run_id"]
    with pytest.raises(DomainError) as exc:
        service.register_from_run(rid)
    assert exc.value.code == "NOT_REVIEWED" and exc.value.status == 409


def test_register_seeds_remaining_equal_total(reviewed_run):
    rid = reviewed_run
    ov = service.register_from_run(rid)
    assert ov["bins"], "应登记出仓批"
    for b in ov["bins"]:
        assert D(b["remaining_dry_kg"]) == D(b["total_dry_kg"]) > 0
        assert b["opened_amount"] == "0.00" and b["status"] == "可提"


def test_target_amount_equals_m6_settlement(reviewed_run):
    rid = reviewed_run
    result = run_store.fetch(rid)
    ov = service.register_from_run(rid)
    m6_by_bin = {}
    for rec in result["records"]:
        m6_by_bin[rec["bin"]] = m6_by_bin.get(rec["bin"], D("0")) + D(rec["amount"])
    for b in ov["bins"]:
        assert D(b["target_amount"]) == m6_by_bin[b["bin"]].quantize(D("0.01"))


def test_take_deducts_dry_matter_and_locks_voucher(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    total = D(b["total_dry_kg"])
    out = service.take(rid, b["bin"], "k1", D("123.456789"))
    v = out["voucher"]
    assert D(v["taken_dry_kg"]) == D("123.456789")
    assert D(out["bin"]["remaining_dry_kg"]) == total - D("123.456789")
    assert D(v["amount"]).as_tuple().exponent == -2  # 金额到分
    # 凭证即开即锁：再次查询金额不变
    again = dlv.find_voucher(rid, b["bin"], "k1")
    assert again["amount"] == v["amount"]


def test_over_draft_rejected_and_nothing_changes(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    before = dlv.get_bin(rid, b["bin"])
    with pytest.raises(DomainError) as exc:
        service.take(rid, b["bin"], "big", D(before["remaining_dry_kg"]) + D("1"))
    assert exc.value.code == "OVER_DRAFT" and exc.value.status == 409
    after = dlv.get_bin(rid, b["bin"])
    # 失败回滚：余量与已开金额均不变，且没有凭证落库
    assert after["remaining_dry_kg"] == before["remaining_dry_kg"]
    assert after["opened_amount"] == before["opened_amount"]
    assert dlv.list_vouchers(rid, b["bin"]) == []


def test_idempotent_retry_does_not_reopen(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    first = service.take(rid, b["bin"], "dup", D("10"))
    replay = service.take(rid, b["bin"], "dup", D("10"))
    assert first["voucher"]["id"] == replay["voucher"]["id"]
    assert replay["replayed"] is True and first["replayed"] is False
    vouchers = dlv.list_vouchers(rid, b["bin"])
    assert len(vouchers) == 1
    # 重试只扣一次
    bin_row = dlv.get_bin(rid, b["bin"])
    assert D(bin_row["cumulative_dry_kg"]) == D("10.000000")


def test_full_take_zeroes_remaining_and_reconciles_to_target(reviewed_run):
    rid = reviewed_run
    result = run_store.fetch(rid)
    service.register_from_run(rid)
    # 每个仓批分多次提，末次提剩余量
    for b in service.overview(rid)["bins"]:
        bn = b["bin"]
        total = D(b["total_dry_kg"])
        service.take(rid, bn, bn + "-a", total * D("0.3"))
        service.take(rid, bn, bn + "-b", total * D("0.3"))
        service.take(rid, bn, bn + "-c", total * D("0.3"))
        rem = D(dlv.get_bin(rid, bn)["remaining_dry_kg"])
        service.take(rid, bn, bn + "-last", rem)
    final = service.overview(rid)
    assert final["totals"]["all_closed"] is True
    assert final["totals"]["all_matched"] is True
    # 凭证总额与仓批 M6 已复核总额分文不差
    assert D(final["totals"]["opened_amount"]) == D(result["total_amount"])
    assert final["totals"]["diff_amount"] == "0.00"
    for b in final["bins"]:
        assert D(b["remaining_dry_kg"]) == 0
        assert D(b["opened_amount"]) == D(b["target_amount"])
        assert b["reconcile"]["matched"] is True


def test_dry_matter_conservation_under_split(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    total = D(b["total_dry_kg"])
    for i, frac in enumerate((D("0.21"), D("0.19"), D("0.37"))):
        service.take(rid, b["bin"], f"p{i}", total * frac)
    cur = dlv.get_bin(rid, b["bin"])
    taken = sum(D(v["taken_dry_kg"]) for v in dlv.list_vouchers(rid, b["bin"]))
    assert taken + D(cur["remaining_dry_kg"]) == total


def test_concurrent_takes_never_over_draw(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    total = D(b["total_dry_kg"])
    each = (total / 4).quantize(D("0.000001"))  # 仅够 4 笔
    outcomes, lock = {"ok": 0, "over": 0}, threading.Lock()

    def worker(i):
        try:
            service.take(rid, b["bin"], f"c{i}", each)
            with lock:
                outcomes["ok"] += 1
        except DomainError as exc:
            with lock:
                if exc.code == "OVER_DRAFT":
                    outcomes["over"] += 1
                else:
                    raise

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes["ok"] == 4 and outcomes["over"] == 6
    final = dlv.get_bin(rid, b["bin"])
    assert D(final["remaining_dry_kg"]) >= 0
    assert len(dlv.list_vouchers(rid, b["bin"])) == 4


def test_concurrent_same_idempotency_key_creates_one_voucher(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    bn = service.overview(rid)["bins"][1]["bin"]
    created, lock = {"first": 0, "replay": 0}, threading.Lock()

    def worker():
        out = service.take(rid, bn, "same", D("10"))
        with lock:
            created["first" if not out["replayed"] else "replay"] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert created["first"] == 1 and created["replay"] == 9
    assert len(dlv.list_vouchers(rid, bn)) == 1
    assert D(dlv.get_bin(rid, bn)["cumulative_dry_kg"]) == D("10.000000")


def test_reregister_blocked_after_vouchers(reviewed_run):
    rid = reviewed_run
    service.register_from_run(rid)
    b = service.overview(rid)["bins"][0]
    service.take(rid, b["bin"], "x", D("1"))
    with pytest.raises(DomainError) as exc:
        service.register_from_run(rid)
    assert exc.value.code == "BIN_LOCKED"
