"""分次提货规则：干物质守恒扣减 + 累计差额法锁定凭证金额。

口径换算：M6 结算金额 = price × standard_kg，而 standard_kg = dry_kg / k，
其中 k = 1 − 标准水分/100。因此干物质口径的有效单价 rate = price / k，
price × standard_kg ≡ rate × dry_kg 代数恒等。

设计要点：
- 干物质（kg）是唯一守恒库存，按实扣减，6 位小数；提完时剩余量归零。
- 凭证金额按“累计差额法”到分：本次凭证 = 应计累计(到分) − 已开累计。
  每张凭证开出瞬间即锁定，不随后续提货变化。
- 末次（扣后余量归零）把累计金额钉到仓批已复核 target_amount，
  由最后一张凭证吸收全部分币尾差，保证 Σ凭证 ≡ target 分文不差，
  与 M6“舍入只在结算末端、最大余额法分到分”的尾差处理同源。
- 全部重量与金额使用 Decimal；JSON 以字符串输出。
"""
from decimal import Decimal as D, ROUND_HALF_UP, InvalidOperation
from app.errors import DomainError

# 干物质库存精度（kg）：保留到毫克，足以承载分次扣量，末次归零。
DRY_SCALE = D("0.000001")
CENT = D("0.01")
ZERO = D("0")


def as_decimal(value, field):
    """把外部输入解析为有限、大于 0 的 Decimal。"""
    try:
        result = D(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError):
        raise DomainError("NUMBER", f"{field}数值格式错误")
    if not result.is_finite():
        raise DomainError("NUMBER", f"{field}必须为有限数")
    if result <= 0:
        raise DomainError("NUMBER", f"{field}必须大于0")
    return result


def quantize_dry(value):
    """干物质库存统一到 6 位小数，Half Up。"""
    return value.quantize(DRY_SCALE, rounding=ROUND_HALF_UP)


def effective_rate(price, standard_moisture):
    """干物质口径有效单价 = price / (1 − 标准水分/100)。保留全精度，不提前舍入。"""
    k = 1 - standard_moisture / D(100)
    return price / k


def accrued(rate, cumulative_dry):
    """截至当前累计已提干物质，按有效单价应计的累计金额（到分，Half Up）。"""
    return (rate * cumulative_dry).quantize(CENT, rounding=ROUND_HALF_UP)


def voucher_amount(rate, prior_cumulative_dry, taken_dry, remaining_after, target_amount, opened_amount):
    """计算本次提货应锁定的凭证金额。

    - 非末次：累计差额法 = 应计累计(到分) − 已开累计，凭证即开即锁。
    - 末次（扣后余量为 0）：把累计钉到 target，本凭证 = target − 已开累计，
      吸收分币尾差，保证全部凭证之和与仓批复核金额完全相符。
    """
    if remaining_after == 0:
        return (D(target_amount) - D(opened_amount)).quantize(CENT)
    after = accrued(rate, prior_cumulative_dry + taken_dry)
    return (after - D(opened_amount)).quantize(CENT)


def reconcile(target_amount, voucher_total, remaining_dry):
    """仓批对账：提完（剩余归零）时凭证总额必须与已复核金额分文不差。"""
    target_amount = D(target_amount)
    voucher_total = D(voucher_total)
    remaining_dry = D(remaining_dry)
    return {
        "closed": remaining_dry == 0,
        "matched": remaining_dry == 0 and target_amount == voucher_total,
        "diff_amount": str((target_amount - voucher_total).quantize(CENT)),
    }
