"""Cost estimate endpoint (/api/cost/estimate)."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["cost"])


@router.get("/api/cost/estimate", response_model=None)
def cost_estimate(request: Request, users: int = 10):
    """Local LLM vs Cloud API のコスト試算 (estimate only). Stage R8-fix: 認証必須."""
    from core.auth import _require_authenticated

    _require_authenticated(request)
    if users < 1:
        users = 1
    if users > 10000:
        users = 10000
    CLOUD_COST_PER_QUERY = 0.004
    QUERIES_PER_USER_MONTH = 500
    LOCAL_INITIAL = 5000
    LOCAL_MONTHLY = 50

    monthly_cloud = users * QUERIES_PER_USER_MONTH * CLOUD_COST_PER_QUERY
    annual_cloud = monthly_cloud * 12
    annual_local = LOCAL_INITIAL + (LOCAL_MONTHLY * 12)
    annual_per_user_cloud = QUERIES_PER_USER_MONTH * CLOUD_COST_PER_QUERY * 12
    if annual_per_user_cloud > 0:
        break_even_users = int(annual_local / annual_per_user_cloud) + 1
    else:
        break_even_users = users
    return {
        "users": users,
        "monthly_cloud_usd": round(monthly_cloud, 2),
        "annual_cloud_usd": round(annual_cloud, 2),
        "annual_local_usd": round(annual_local, 2),
        "annual_savings_usd": round(annual_cloud - annual_local, 2),
        "break_even_users": break_even_users,
        "local_recommended": annual_cloud > annual_local,
        "regulated_note": True,
        "assumptions": {
            "cloud_cost_per_query_usd": CLOUD_COST_PER_QUERY,
            "queries_per_user_per_month": QUERIES_PER_USER_MONTH,
            "local_initial_usd": LOCAL_INITIAL,
            "local_monthly_usd": LOCAL_MONTHLY,
        },
    }
