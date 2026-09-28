"""AkShare: A-share and Hong Kong market data.

Registered per method in ``dataflows.router.VENDOR_METHODS``, so a run picks it
up by listing ``akshare`` in the relevant ``data_vendors`` category:

    config["data_vendors"]["core_stock_apis"] = "akshare"
    config["data_vendors"]["technical_indicators"] = "akshare"

Anything not implemented here (fundamentals, news, sentiment) simply is not in
the router for that method, so the existing chain falls through to another
vendor. AkShare is imported lazily inside the fetch functions so importing
``quantagent`` does not pull it in.
"""

__all__ = ["market", "ohlcv", "symbols"]
