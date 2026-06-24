"""Bank crawler registry — maps display names to crawler classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finance_sum.crawlers.base import BankCrawler

# Display name → module path and class name (lazy import)
_REGISTRY: dict[str, tuple[str, str]] = {
    "台新": ("finance_sum.crawlers.taishin", "TaishinCrawler"),
    "國泰": ("finance_sum.crawlers.cathay", "CathayCrawler"),
    "土銀": ("finance_sum.crawlers.land_bank", "LandBankCrawler"),
    "王道": ("finance_sum.crawlers.o_bank", "OBankCrawler"),
    "永豐": ("finance_sum.crawlers.sinopac", "SinopacCrawler"),
    "富邦": ("finance_sum.crawlers.fubon", "FubonCrawler"),
    "聯邦": ("finance_sum.crawlers.union_bank", "UnionBankCrawler"),
}

BANK_REGISTRY: list[str] = list(_REGISTRY.keys())


def get_crawler(bank_name: str) -> BankCrawler:
    """Instantiate the crawler for *bank_name* via lazy import."""
    if bank_name not in _REGISTRY:
        raise ValueError(f"不支援的銀行: {bank_name}，支援的銀行: {', '.join(BANK_REGISTRY)}")

    module_path, class_name = _REGISTRY[bank_name]
    import importlib

    module = importlib.import_module(module_path)
    crawler_cls = getattr(module, class_name)
    return crawler_cls()
