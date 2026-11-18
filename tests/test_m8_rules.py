"""M8 分次提货规则纯函数测试。"""
from decimal import Decimal as D
import pytest
from app.errors import DomainError
from app.modules.m8_delivery.rules import (
    as_decimal, quantize_dry, effective_rate, accrued, voucher_amount, reconcile,
)


def test_effective_rate_matches_settlement_basis():
    # price × standard_kg ≡ rate × dry_kg，standard=dry/(1−std/100)
    price, std, dry = D("2.6"), D("14"), D("841.5")
    rate = effective_rate(price, std)
    k = 1 - std / D(100)
    assert rate == price / k
    assert (rate * dry).quantize(D("0.01")) == (price * dry / k).quantize(D("0.01"))


def test_voucher_cumulative_difference_locks_amount():
    rate = D("3.023255813953488372093023256")
    a1 = voucher_amount(rate, D("0"), D("100"), D("900"), D("3023.26"), D("0"))
    # 首张 = 应计累计(到分) − 0
    assert a1 == accrued(rate, D("100"))
    a2 = voucher_amount(rate, D("100"), D("100"), D("800"), D("3023.26"), a1)
    # 任意时点 已开累计 == 应计累计(到分)
    assert (a1 + a2) == accrued(rate, D("200"))


def test_final_voucher_pins_to_target_absorbs_residual():
    rate = D("3.023255813953488372093023256")
    target, opened = D("36351.10"), D("35443.75")
    last = voucher_amount(rate, D("11700"), D("323.8272"), D("0"), target, opened)
    # 末次把累计钉到 target，吸收尾差
    assert last == target - opened
    assert (opened + last) == target


def test_reconcile_only_matches_when_closed_and_equal():
    assert reconcile("100.00", "100.00", "0") == {
        "closed": True, "matched": True, "diff_amount": "0.00"}
    rec = reconcile("100.00", "99.99", "0")
    assert rec["closed"] is True and rec["matched"] is False
    assert reconcile("100.00", "100.00", "5")["closed"] is False


def test_as_decimal_rejects_non_positive_and_garbage():
    assert as_decimal("12.5", "x") == D("12.5")
    for bad in ("0", "-3", "abc", ""):
        with pytest.raises(DomainError):
            as_decimal(bad, "x")


def test_quantize_dry_six_places():
    assert quantize_dry(D("1.2345678")) == D("1.234568")
    assert quantize_dry(D("100")) == D("100.000000")
