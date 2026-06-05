"""按需抓取历史(OnDemandJob)—— 记录写入 + 列表/详情/删除 的纯逻辑。

routes.py 只做薄路由声明,业务逻辑集中在此,避免 routes.py 膨胀。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import OnDemandJob, Product, Review
from ..ondemand.registry import classify_url, detect_platform


def _status_of(listing_count: int, review_count: int, notes: list) -> str:
    if listing_count == 0 and review_count == 0:
        return "failed"
    if notes:
        return "partial"
    return "success"


def record_job(session: Session, *, ws_id: int | None, username: str | None,
               url: str, result) -> OnDemandJob:
    """把一次 fetch 的 OnDemandResult 落成一条 OnDemandJob。"""
    skus = [l.get("sku") for l in result.listings if l.get("sku")]
    listing_count = len(result.listings)
    review_count = len(result.reviews)
    notes = list(result.notes or [])
    job = OnDemandJob(
        url=url,
        platform=detect_platform(url),
        kind=classify_url(url),
        listing_count=listing_count,
        review_count=review_count,
        status=_status_of(listing_count, review_count, notes),
        notes=notes,
        item_skus=skus,
        workspace_id=ws_id,
        created_by=username,
    )
    session.add(job)
    session.flush()
    return job


def _job_dict(job: OnDemandJob) -> dict:
    return {
        "id": job.id,
        "url": job.url,
        "platform": job.platform,
        "kind": job.kind,
        "listing_count": job.listing_count,
        "review_count": job.review_count,
        "status": job.status,
        "notes": job.notes or [],
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def list_jobs_logic(session: Session, *, ws_id: int | None,
                    platform: str | None, page: int, page_size: int) -> dict:
    q = session.query(OnDemandJob).filter(OnDemandJob.workspace_id == ws_id)
    if platform:
        q = q.filter(OnDemandJob.platform == platform)
    total = q.count()
    rows = (q.order_by(OnDemandJob.created_at.desc(), OnDemandJob.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"total": total, "page": page, "page_size": page_size,
            "jobs": [_job_dict(r) for r in rows]}


def job_detail_logic(session: Session, *, ws_id: int | None,
                     job_id: int) -> dict | None:
    """返回 job + listings + reviews;job 不存在或不属于 ws_id 时返回 None。"""
    job = session.get(OnDemandJob, job_id)
    if job is None or job.workspace_id != ws_id:
        return None
    skus = list(job.item_skus or [])
    listings, reviews = [], []
    if skus:
        prods = (session.query(Product)
                 .filter(Product.site.like("ondemand_%"),
                         Product.sku.in_(skus)).all())
        listings = [{"sku": p.sku, "title": p.title, "sale_price": p.sale_price,
                     "original_price": p.original_price, "currency": p.currency,
                     "image_urls": p.image_urls or [], "product_url": p.product_url}
                    for p in prods]
        revs = (session.query(Review)
                .filter(Review.platform.like("ondemand_%"),
                        Review.sku.in_(skus)).all())
        reviews = [{"review_id": r.review_id, "rating": r.rating,
                    "content": r.content, "review_date":
                    r.review_date.isoformat() if r.review_date else None}
                   for r in revs]
    return {"job": _job_dict(job), "listings": listings, "reviews": reviews}


def delete_job_logic(session: Session, *, ws_id: int | None,
                     job_id: int) -> bool:
    """删单条;不存在或不属于 ws_id 返回 False。只删记录,不删 Product/Review。"""
    job = session.get(OnDemandJob, job_id)
    if job is None or job.workspace_id != ws_id:
        return False
    session.delete(job)
    return True


def clear_jobs_logic(session: Session, *, ws_id: int | None) -> int:
    """清空本 workspace 的记录,返回删除条数。只删记录,不删 Product/Review。"""
    rows = session.query(OnDemandJob).filter(
        OnDemandJob.workspace_id == ws_id).all()
    n = len(rows)
    for r in rows:
        session.delete(r)
    return n
