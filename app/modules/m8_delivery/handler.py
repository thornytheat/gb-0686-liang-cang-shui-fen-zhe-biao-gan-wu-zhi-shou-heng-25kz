"""M8 分次提货 HTTP 入口。

POST /api/m8/register  {run_id}                     登记仓批提货账户
POST /api/m8/take      {run_id,bin,idempotency_key,taken_dry_kg}  提货开凭证（幂等）
GET  /api/m8/overview?run=...                       凭证与可提余量、对账
GET  /api/m8?run=...                                同 overview（保持模块 GET 约定）
"""
from flask import Blueprint, request, jsonify
from app.errors import DomainError
from . import service

bp = Blueprint("m8", __name__)


def _json():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise DomainError("BODY", "请求必须是对象")
    return payload


@bp.route("/api/m8", methods=["GET"])
def module_view():
    return jsonify(service.overview(request.args.get("run", "")))


@bp.route("/api/m8/register", methods=["POST"])
def register():
    payload = _json()
    return jsonify(service.register_from_run(payload.get("run_id", "")))


@bp.route("/api/m8/take", methods=["POST"])
def take():
    payload = _json()
    return jsonify(service.take(
        payload.get("run_id", ""),
        payload.get("bin", ""),
        payload.get("idempotency_key", ""),
        payload.get("taken_dry_kg"),
    ))


@bp.route("/api/m8/overview", methods=["GET"])
def overview():
    return jsonify(service.overview(request.args.get("run", "")))
