"""Excel 导出 —— 完全对标 03-样本数据 的三份报表，并额外提供更多字段。

对标的样本（甲方原始交付物）：
  product_analysis_report.xlsx  — 20 列
  sales_promotion_report.xlsx   — 13 列
  trend_report.xlsx             — 8 列
本模块输出的工作簿在「完全复刻这三张表的列名与顺序」之外，额外提供
「商品全字段」「分类树」「站点概览」三张扩展表 —— 信息只多不少。
"""
from __future__ import annotations

import io

import pandas as pd
from sqlalchemy.orm import Session

from .models import Category, Product, Promotion, Site, Trend

# ---- 对标 product_analysis_report.xlsx 的 20 列（列名/顺序与样本完全一致）----
PRODUCT_SAMPLE_COLS = [
    "NO.", "Sku", "Image", "Title", "label", "VariantId", "Attributes",
    "Sale Price", "Price", "30-Days Sales", "30-Days Revenue", "Ratings",
    "Reviews", "Status", "Category", "Inventory", "Video", "Free shipping",
    "Created Time", "Update Time",
]
# ---- 对标 sales_promotion_report.xlsx 的 13 列 ----
PROMO_SAMPLE_COLS = [
    "NO.", "SKU", "Update Time", "Product Title", "Product Image", "Type",
    "Name", "Discount", "Orignal-Price", "Post-Price", "Threshold",
    "Start Time", "End Time",
]
# ---- 对标 trend_report.xlsx 的 8 列 ----
TREND_SAMPLE_COLS = [
    "NO.", "Date", "Sku Count", "New Product Count", "Sales", "Revenue",
    "Traffic", "Conversion Rate",
]
# ---- 扩展表：规格 §4.1.2 全部 32 个 SKU 字段 ----
PRODUCT_FULL_COLS = [
    "NO.", "site", "brand", "sku", "spu", "variant_id", "title", "description",
    "category_path", "product_type", "attributes", "tags", "label",
    "sale_price", "original_price", "currency", "ratings", "review_count",
    "thirty_day_sales", "thirty_day_revenue", "status", "inventory",
    "has_video", "has_free_shipping", "mpn", "gtin", "weight", "shipping_time",
    "return_policy_days", "image_count", "image_urls", "product_url",
    "is_new", "is_bestseller", "published_at", "created_time", "updated_time",
]

_STATUS = {"on_sale": "on sale", "out_of_stock": "out of stock",
           "discontinued": "discontinued"}


def _yn(v) -> str:
    return "YES" if v else "NO"


def _attrs(a) -> str:
    if not a:
        return ""
    return " ".join(f"{k}:{v}" for k, v in a.items())


def _list(v) -> str:
    return ", ".join(str(x) for x in v) if v else ""


def _dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M:%S") if v else ""


def _apply_cat_filter(q, model_class, categories: list[str] | None):
    """对 query 加品类过滤（OR 模糊匹配 category_path 任一关键词）。"""
    if not categories:
        return q
    from sqlalchemy import or_
    return q.filter(or_(*[model_class.category_path.ilike(f"%{c}%")
                          for c in categories]))


# ---------- 对标样本：商品分析报表 ----------
def products_sample_df(session: Session, site: str | None = None,
                       categories: list[str] | None = None) -> pd.DataFrame:
    q = session.query(Product)
    if site:
        q = q.filter(Product.site == site)
    q = _apply_cat_filter(q, Product, categories)
    rows = []
    for i, p in enumerate(q.order_by(Product.id).all(), start=1):
        rows.append({
            "NO.": i, "Sku": p.sku, "Image": (p.image_urls or [""])[0],
            "Title": p.title, "label": p.label or "", "VariantId": p.variant_id,
            "Attributes": _attrs(p.attributes), "Sale Price": p.sale_price,
            "Price": p.original_price, "30-Days Sales": p.thirty_day_sales or 0,
            "30-Days Revenue": p.thirty_day_revenue or 0.0,
            "Ratings": p.ratings or 0.0, "Reviews": p.review_count or 0,
            "Status": _STATUS.get(p.status, p.status), "Category": p.category_path,
            "Inventory": p.inventory, "Video": _yn(p.has_video),
            "Free shipping": _yn(p.has_free_shipping),
            "Created Time": _dt(p.created_time), "Update Time": _dt(p.updated_time),
        })
    return pd.DataFrame(rows, columns=PRODUCT_SAMPLE_COLS)


# ---------- 对标样本：销售促销报表 ----------
def promotions_sample_df(session: Session, site: str | None = None,
                         categories: list[str] | None = None) -> pd.DataFrame:
    q = session.query(Promotion)
    if site:
        q = q.filter(Promotion.site == site)
    if categories:
        # Promotion 没 category_path，通过 Product join 过滤
        from sqlalchemy import or_
        skus = [r[0] for r in session.query(Product.sku).filter(
            or_(*[Product.category_path.ilike(f"%{c}%") for c in categories])).all()]
        if skus:
            q = q.filter(Promotion.sku.in_(skus))
        else:
            q = q.filter(Promotion.id == -1)  # empty result
    rows = []
    for i, p in enumerate(q.all(), start=1):
        rows.append({
            "NO.": i, "SKU": p.sku, "Update Time": _dt(p.detected_time),
            "Product Title": p.product_title, "Product Image": p.product_image,
            "Type": p.promotion_type, "Name": p.promotion_name or p.promotion_type,
            "Discount": p.discount_percent, "Orignal-Price": p.original_price,
            "Post-Price": p.promotion_price, "Threshold": p.threshold or "/",
            "Start Time": _dt(p.start_time), "End Time": _dt(p.end_time),
        })
    return pd.DataFrame(rows, columns=PROMO_SAMPLE_COLS)


# ---------- 对标样本：趋势报表 ----------
def trends_sample_df(session: Session, site: str | None = None) -> pd.DataFrame:
    q = session.query(Trend)
    if site:
        q = q.filter(Trend.site == site)
    rows = []
    for i, t in enumerate(q.order_by(Trend.date).all(), start=1):
        rows.append({
            "NO.": i, "Date": t.date.isoformat() if t.date else "",
            "Sku Count": t.sku_count, "New Product Count": t.new_product_count,
            "Sales": t.estimated_sales, "Revenue": t.estimated_revenue,
            "Traffic": t.traffic if t.traffic is not None else "/",
            "Conversion Rate": t.conversion_rate
            if t.conversion_rate is not None else "/",
        })
    return pd.DataFrame(rows, columns=TREND_SAMPLE_COLS)


# ---------- 扩展表：商品全字段（32 字段，信息只多不少）----------
def products_full_df(session: Session, site: str | None = None,
                     categories: list[str] | None = None) -> pd.DataFrame:
    q = session.query(Product)
    if site:
        q = q.filter(Product.site == site)
    q = _apply_cat_filter(q, Product, categories)
    rows = []
    for i, p in enumerate(q.order_by(Product.id).all(), start=1):
        rows.append({
            "NO.": i, "site": p.site, "brand": p.brand, "sku": p.sku,
            "spu": p.spu, "variant_id": p.variant_id, "title": p.title,
            "description": (p.description or "")[:500], "category_path": p.category_path,
            "product_type": p.product_type, "attributes": _attrs(p.attributes),
            "tags": _list(p.tags), "label": p.label, "sale_price": p.sale_price,
            "original_price": p.original_price, "currency": p.currency,
            "ratings": p.ratings, "review_count": p.review_count,
            "thirty_day_sales": p.thirty_day_sales,
            "thirty_day_revenue": p.thirty_day_revenue, "status": p.status,
            "inventory": p.inventory, "has_video": _yn(p.has_video),
            "has_free_shipping": _yn(p.has_free_shipping), "mpn": p.mpn,
            "gtin": p.gtin, "weight": p.weight, "shipping_time": p.shipping_time,
            "return_policy_days": p.return_policy_days,
            "image_count": len(p.image_urls or []), "image_urls": _list(p.image_urls),
            "product_url": p.product_url, "is_new": _yn(p.is_new),
            "is_bestseller": _yn(p.is_bestseller), "published_at": _dt(p.published_at),
            "created_time": _dt(p.created_time), "updated_time": _dt(p.updated_time),
        })
    return pd.DataFrame(rows, columns=PRODUCT_FULL_COLS)


# ---------- 扩展表：分类树 ----------
def categories_df(session: Session, site: str | None = None) -> pd.DataFrame:
    q = session.query(Category)
    if site:
        q = q.filter(Category.site == site)
    rows = []
    for i, c in enumerate(q.all(), start=1):
        rows.append({
            "NO.": i, "site": c.site, "category_id": c.category_id,
            "category_name": c.category_name, "category_url": c.category_url,
            "parent_id": c.parent_id, "level": c.level,
            "product_count": c.product_count, "collected_time": _dt(c.collected_time),
        })
    return pd.DataFrame(rows, columns=["NO.", "site", "category_id",
                        "category_name", "category_url", "parent_id", "level",
                        "product_count", "collected_time"])


# ---------- 扩展表：站点概览 ----------
def sites_overview_df(session: Session) -> pd.DataFrame:
    rows = []
    for i, s in enumerate(session.query(Site).all(), start=1):
        sku = session.query(Product).filter(Product.site == s.site).count()
        spu = (session.query(Product.spu)
               .filter(Product.site == s.site).distinct().count())
        cats = session.query(Category).filter(Category.site == s.site).count()
        promo = session.query(Promotion).filter(Promotion.site == s.site).count()
        rows.append({
            "NO.": i, "site": s.site, "brand": s.brand, "country": s.country,
            "url": s.url, "platform": s.platform, "proxy_tier": s.proxy_tier,
            "SKU数": sku, "SPU数": spu, "分类数": cats, "促销数": promo,
            "最后采集": _dt(s.last_crawled),
        })
    return pd.DataFrame(rows, columns=["NO.", "site", "brand", "country", "url",
                        "platform", "proxy_tier", "SKU数", "SPU数", "分类数",
                        "促销数", "最后采集"])


def export_workbook(session: Session, site: str | None = None,
                    categories: list[str] | None = None) -> bytes:
    """导出 6-Sheet Excel：3 张完全对标样本 + 3 张扩展。
    categories: 可选品类过滤列表（OR 模糊匹配 category_path），用于批量按品类下载。"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        products_sample_df(session, site, categories).to_excel(
            w, sheet_name="商品分析", index=False)
        promotions_sample_df(session, site, categories).to_excel(
            w, sheet_name="销售促销", index=False)
        trends_sample_df(session, site).to_excel(
            w, sheet_name="趋势报告", index=False)
        products_full_df(session, site, categories).to_excel(
            w, sheet_name="商品全字段(扩展)", index=False)
        categories_df(session, site).to_excel(
            w, sheet_name="分类树(扩展)", index=False)
        sites_overview_df(session).to_excel(
            w, sheet_name="站点概览(扩展)", index=False)
    return buf.getvalue()
