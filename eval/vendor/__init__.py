"""
Vendor 包 — 集成外部官方 baseline 实现。

每个子模块都是一个 thin adapter，调用 vendor/<repo>/ 里的官方 API。
正式实验不静默降级；依赖、权重或资产缺失时直接报错。
"""

import logging

logger = logging.getLogger("vendor")


def print_status() -> None:
    """打印所有 vendor adapter 的接入状态。"""
    import importlib
    adapters = ["vdocrag_adapter", "vidorag_adapter", "react_adapter"]
    for name in adapters:
        try:
            m = importlib.import_module(f"eval.vendor.{name}")
            status = getattr(m, "STATUS", "unknown")
            print(f"  [{name}] {status}")
        except Exception as e:
            print(f"  [{name}] IMPORT FAILED: {e}")
