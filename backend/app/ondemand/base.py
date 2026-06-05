"""按需抓取的结果容器与采集器基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class OnDemandResult:
    """一次 fetch(url) 的产出:listing 列表 + 评论列表 + 备注。"""

    def __init__(self):
        self.listings: list[dict] = []
        self.reviews: list[dict] = []
        self.notes: list[str] = []

    def add_listing(self, listing: dict) -> None:
        if listing:
            self.listings.append(listing)

    def add_reviews(self, reviews: list[dict]) -> None:
        self.reviews.extend(r for r in (reviews or []) if r)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def summary(self) -> dict:
        return {"listings": len(self.listings),
                "reviews": len(self.reviews),
                "notes": list(self.notes)}


class BaseOnDemand(ABC):
    """平台采集器基类。子类实现解析(纯函数)与 HTTP 抓取。

    platform:    平台标识,如 "mercadolibre" / "lazada" / "shopee"
    proxy_tier:  默认代理档,被 runner 用于取 proxy
    """

    platform = "base"
    proxy_tier = "none"

    @staticmethod
    @abstractmethod
    def parse_item_id(url: str):
        """从商品 URL 解析平台商品 ID。Shopee 返回 (shopid, itemid)。"""
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def parse_listing(data: dict, url: str) -> dict:
        """把平台商品 JSON 解析成可入 Product 表的标准 dict。"""
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def parse_reviews(data: dict, item_id, url: str) -> list[dict]:
        """把平台评论 JSON 解析成可入 Review 表的 dict 列表。"""
        raise NotImplementedError
