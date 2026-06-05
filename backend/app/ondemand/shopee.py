"""虾皮(Shopee)按需采集器。

❌ 状态(2026-06-05 实测):**现有工具绕不过 Shopee 反爬,暂不可用**。
    实测(住宅代理 + 真浏览器 + 滚动,SG/ID 站):
      · 商品页 / 搜索页 → 被重定向到 `/verify/traffic/error?...type=4`(反爬流量墙)
      · 内部 API get_pc / get_ratings / item/get → 403
        `{"is_login":false,"action_type":2,"error":90309999}`
    关键认知:**卡在「反爬流量墙」而非登录墙**——它在访问任何商品/API 之前先做流量
    指纹检测,判定 bot 就弹验证页。即便有登录账号,bot 流量照样先被这道墙拦。
    Lazada/美客多能靠「住宅代理 + 真浏览器」过墙,Shopee 不行(强一个数量级)。
    可行的绕过路径(均非免费午餐,需额外资源,待选型):
      (A) 真实登录 cookie 注入(需真账号;cookie 会过期;账号有风控/封号风险);
      (B) 付费第三方 Shopee API(Apify / Kameleo 等,维护账号池+反爬,最省事最稳,有费用);
      (C) Shopee 官方 Open API(需 partner_id+key,合规但门槛高);
      (D) 更强反检测浏览器(Kameleo/Multilogin 级指纹伪装,可能过墙,需额外工具调试)。
    下面的接口路径与 parse_* 是早期「假设结构」,**从未经真实数据验证**,选定绕过方案后
    需照 Lazada/美客多 的做法重新逆向真实结构再重写。

listing:  GET https://{host}/api/v4/pdp/get_pc?shop_id={s}&item_id={i}       (403,未验证)
reviews:  GET https://{host}/api/v2/item/get_ratings?shopid={s}&itemid={i}    (403,未验证)
URL->id:  单品 URL 形如  .../<slug>-i.<shopid>.<itemid>  或  /product/<shopid>/<itemid>
反爬:     最强(流量墙 + 登录态 + API 签名),住宅代理+真浏览器均被弹。
价格:     (假设)Shopee 价格字段放大 100000 倍,解析时除回 —— 未验证。
图片:     (假设)字段是 hash,拼 https://cf.{host}/file/<hash> —— 未验证。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from curl_cffi import requests as creq

from ..antiban import check_blocked
from .base import BaseOnDemand

_ID_DOT_RE = re.compile(r"-i\.(\d+)\.(\d+)")
_ID_PATH_RE = re.compile(r"/product/(\d+)/(\d+)")
_PRICE_SCALE = 100000
PLATFORM = "shopee"
SITE = f"ondemand_{PLATFORM}"
_IMG_BASE = "https://cf.shopee.com.my/file/"


class ShopeeOnDemand(BaseOnDemand):
    platform = PLATFORM
    proxy_tier = "residential"

    @staticmethod
    def parse_item_id(url: str):
        m = _ID_DOT_RE.search(url) or _ID_PATH_RE.search(url)
        if not m:
            raise ValueError(f"Shopee URL 无 shopid.itemid: {url}")
        return m.group(1), m.group(2)

    @staticmethod
    def _img(hash_or_url: str) -> str:
        if not hash_or_url:
            return ""
        if hash_or_url.startswith("http"):
            return hash_or_url
        return _IMG_BASE + hash_or_url

    @staticmethod
    def parse_listing(data: dict, url: str) -> dict:
        it = data.get("data", {}).get("item", {}) or {}
        shopid, itemid = it.get("shopid"), it.get("itemid")
        imgs = it.get("images") or ([it["image"]] if it.get("image") else [])
        rating = (it.get("item_rating") or {}).get("rating_star")
        return {
            "sku": f"{shopid}_{itemid}",
            "title": it.get("name"),
            "sale_price": (it.get("price") or 0) / _PRICE_SCALE or None,
            "original_price": (it.get("price_before_discount") or it.get("price") or 0)
            / _PRICE_SCALE or None,
            "currency": it.get("currency"),
            "image_urls": [ShopeeOnDemand._img(h) for h in imgs],
            "ratings": rating,
            "inventory": str(it.get("stock")) if it.get("stock") is not None else None,
            "status": ("on_sale" if (it.get("stock") or 0) > 0 else "out_of_stock"),
            "product_url": url,
            "site": SITE,
            "brand": PLATFORM,
        }

    @staticmethod
    def parse_reviews(data: dict, item_id, url: str) -> list[dict]:
        if isinstance(item_id, tuple):
            sku = f"{item_id[0]}_{item_id[1]}"
        else:
            sku = str(item_id)
        out = []
        for r in (data.get("data", {}).get("ratings") or []):
            ctime = r.get("ctime")
            rdate = (datetime.fromtimestamp(ctime, tz=timezone.utc).isoformat()
                     if ctime else None)
            out.append({
                "review_id": str(r["cmtid"]) if r.get("cmtid") is not None else None,
                "platform": SITE,
                "site": SITE,
                "reviewer_name": r.get("author_username"),
                "rating": r.get("rating_star"),
                "title": None,
                "content": r.get("comment"),
                "review_date": rdate,
                "sku": sku,
                "product_url": url,
            })
        return out

    # ---- HTTP(smoke 路径)----
    def _session(self, proxy):
        s = creq.Session(impersonate="chrome")
        s.headers.update({"Referer": "https://shopee.com/",
                          "X-Requested-With": "XMLHttpRequest"})
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        return s

    def _host(self, url: str) -> str:
        return re.sub(r"^https?://", "", url).split("/")[0]

    def fetch_listing(self, item_id, url: str, proxy=None) -> dict:
        shopid, itemid = item_id
        s = self._session(proxy)
        api = (f"https://{self._host(url)}/api/v4/pdp/get_pc"
               f"?shop_id={shopid}&item_id={itemid}")
        resp = s.get(api, timeout=30)
        check_blocked(resp.status_code, "shopee/pdp")
        resp.raise_for_status()
        return self.parse_listing(resp.json(), url)

    def fetch_reviews(self, item_id, url: str, limit: int = 100, proxy=None):
        shopid, itemid = item_id
        s = self._session(proxy)
        out, offset = [], 0
        while len(out) < limit and offset < 20 * 20:   # 兜底页数上限,与 lazada 一致
            api = (f"https://{self._host(url)}/api/v2/item/get_ratings"
                   f"?shopid={shopid}&itemid={itemid}&offset={offset}&limit=20")
            resp = s.get(api, timeout=30)
            check_blocked(resp.status_code, "shopee/ratings")
            resp.raise_for_status()
            batch = self.parse_reviews(resp.json(), item_id, url)
            if not batch:
                break
            out.extend(batch)
            offset += 20
        return out[:limit]

    def enumerate_listing(self, url: str, max_items: int = 100, proxy=None):
        """店铺/类目页枚举。Shopee 店铺 API:
        GET /api/v4/shop/search_items?shopid=...&limit=...  首版用页面正则兜底。"""
        s = self._session(proxy)
        resp = s.get(url, timeout=30)
        check_blocked(resp.status_code, "shopee/listing")
        resp.raise_for_status()
        ids = []
        for m in list(_ID_DOT_RE.finditer(resp.text)) + list(_ID_PATH_RE.finditer(resp.text)):
            pair = (m.group(1), m.group(2))
            if pair not in ids:
                ids.append(pair)
            if len(ids) >= max_items:
                break
        return ids
