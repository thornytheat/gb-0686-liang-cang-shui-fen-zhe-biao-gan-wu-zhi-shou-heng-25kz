"""M8 分次提货 HTTP 端到端测试。"""
from decimal import Decimal as D


def _reviewed_run(client):
    r = client.post("/api/runs", json={}).json
    rid = r["manifest"]["run_id"]
    client.post(f"/api/review/{rid}", json={"version": r["version"], "decision": "通过"})
    return rid


def test_register_and_take_flow(client):
    rid = _reviewed_run(client)
    resp = client.post("/api/m8/register", json={"run_id": rid})
    assert resp.status_code == 200
    ov = resp.json
    assert ov["totals"]["all_closed"] is False
    bn = ov["bins"][0]["bin"]

    take = client.post("/api/m8/take", json={
        "run_id": rid, "bin": bn, "idempotency_key": "h1", "taken_dry_kg": "500"})
    assert take.status_code == 200
    body = take.json
    assert body["replayed"] is False
    assert D(body["voucher"]["amount"]).as_tuple().exponent == -2
    assert D(body["bin"]["remaining_dry_kg"]) < D(ov["bins"][0]["total_dry_kg"])


def test_retry_same_key_returns_same_voucher(client):
    rid = _reviewed_run(client)
    client.post("/api/m8/register", json={"run_id": rid})
    bn = client.get(f"/api/m8/overview?run={rid}").json["bins"][0]["bin"]
    payload = {"run_id": rid, "bin": bn, "idempotency_key": "k", "taken_dry_kg": "50"}
    first = client.post("/api/m8/take", json=payload).json
    second = client.post("/api/m8/take", json=payload).json
    assert first["voucher"]["id"] == second["voucher"]["id"]
    assert second["replayed"] is True
    vouchers = client.get(f"/api/m8/overview?run={rid}").json["vouchers"]
    assert len(vouchers) == 1


def test_over_draft_returns_409(client):
    rid = _reviewed_run(client)
    client.post("/api/m8/register", json={"run_id": rid})
    bn = client.get(f"/api/m8/overview?run={rid}").json["bins"][0]["bin"]
    resp = client.post("/api/m8/take", json={
        "run_id": rid, "bin": bn, "idempotency_key": "big", "taken_dry_kg": "99999999"})
    assert resp.status_code == 409 and resp.json["code"] == "OVER_DRAFT"


def test_validation_errors(client):
    rid = _reviewed_run(client)
    client.post("/api/m8/register", json={"run_id": rid})
    bn = client.get(f"/api/m8/overview?run={rid}").json["bins"][0]["bin"]
    base = {"run_id": rid, "bin": bn, "taken_dry_kg": "1"}
    assert client.post("/api/m8/take", json=base).status_code == 422  # 缺幂等键
    assert client.post("/api/m8/take", json={**base, "idempotency_key": "z", "taken_dry_kg": "0"}).status_code == 422
    assert client.post("/api/m8/take", json={**base, "idempotency_key": "z", "taken_dry_kg": "x"}).status_code == 422


def test_register_blocked_before_review(client):
    r = client.post("/api/runs", json={}).json
    rid = r["manifest"]["run_id"]
    resp = client.post("/api/m8/register", json={"run_id": rid})
    assert resp.status_code == 409 and resp.json["code"] == "NOT_REVIEWED"


def test_unknown_bin_404(client):
    rid = _reviewed_run(client)
    client.post("/api/m8/register", json={"run_id": rid})
    resp = client.post("/api/m8/take", json={
        "run_id": rid, "bin": "不存在", "idempotency_key": "x", "taken_dry_kg": "1"})
    assert resp.status_code == 404 and resp.json["code"] == "NOT_FOUND"


def test_page_and_overview_render(client):
    rid = _reviewed_run(client)
    assert client.get(f"/m/8?run={rid}").status_code == 200
    assert client.get(f"/api/m8?run={rid}").status_code == 200
    client.post("/api/m8/register", json={"run_id": rid})
    page = client.get(f"/m/8?run={rid}")
    assert page.status_code == 200 and "分次提货".encode() in page.data
    assert client.get(f"/api/m8/overview?run={rid}").status_code == 200


def test_full_reconciliation_over_http(client):
    rid = _reviewed_run(client)
    run_total = client.get(f"/api/runs/{rid}").json["total_amount"]
    client.post("/api/m8/register", json={"run_id": rid})
    overview = client.get(f"/api/m8/overview?run={rid}").json
    for b in overview["bins"]:
        bn, rem = b["bin"], D(b["remaining_dry_kg"])
        client.post("/api/m8/take", json={"run_id": rid, "bin": bn,
                                          "idempotency_key": bn + "-all", "taken_dry_kg": str(rem)})
    final = client.get(f"/api/m8/overview?run={rid}").json
    assert final["totals"]["all_matched"] is True
    assert final["totals"]["opened_amount"] == run_total
    assert final["totals"]["diff_amount"] == "0.00"
