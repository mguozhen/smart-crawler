"""代理池 per-proxy 平台排除机制测试。

标注语法:代理行尾 `# no:amazon` 表示该代理不可用于抓 amazon 平台。
get_proxy(tier, site=...) 选候选时跳过 site 命中排除集的代理。
"""
from app.proxy_pool import ProxyPool


def _pool_from(tmp_path, content):
    f = tmp_path / "proxies.txt"
    f.write_text(content, encoding="utf-8")
    # ProxyPool 读模块级 _PROXY_FILE;这里直接构造并指向临时文件
    pool = ProxyPool()
    import app.proxy_pool as pp
    # 用临时文件路径替换实例加载源
    orig = pp._PROXY_FILE
    pp._PROXY_FILE = f
    try:
        pool._ensure_loaded()
    finally:
        pp._PROXY_FILE = orig
    return pool


def test_excluded_proxy_never_returned_for_amazon(tmp_path):
    pool = _pool_from(tmp_path, """
[datacenter]
http://u:p@1.1.1.1:2333   # no:amazon
http://u:p@1.1.1.2:2333   # no:amazon
""")
    # 抓 amazon → 两个代理都被排除 → 无候选
    for _ in range(10):
        assert pool.get("datacenter", site="amazon_us") is None


def test_excluded_proxy_used_for_other_platforms(tmp_path):
    pool = _pool_from(tmp_path, """
[datacenter]
http://u:p@1.1.1.1:2333   # no:amazon
""")
    # 抓非 amazon → 正常返回
    assert pool.get("datacenter", site="songmics_us") == "http://u:p@1.1.1.1:2333"
    # site=None → 不限平台,正常返回
    assert pool.get("datacenter", site=None) is None or pool.get("datacenter") == "http://u:p@1.1.1.1:2333"


def test_unannotated_proxy_unaffected(tmp_path):
    pool = _pool_from(tmp_path, """
[datacenter]
http://u:p@1.1.1.1:2333
""")
    # 无标注 → 任何平台都能用,包括 amazon
    assert pool.get("datacenter", site="amazon_us") == "http://u:p@1.1.1.1:2333"


def test_mixed_pool_amazon_skips_only_excluded(tmp_path):
    pool = _pool_from(tmp_path, """
[datacenter]
http://u:p@1.1.1.1:2333   # no:amazon
http://u:p@1.1.1.2:2333
""")
    # 抓 amazon 多次 → 只会拿到未排除的 .2,绝不出现 .1
    seen = {pool.get("datacenter", site="amazon_us") for _ in range(20)}
    assert seen == {"http://u:p@1.1.1.2:2333"}
