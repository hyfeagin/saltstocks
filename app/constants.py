"""
Single source of truth for all hardcoded string enums used across routes and templates.
Change a value here and it propagates everywhere automatically.
"""
from __future__ import annotations

RESALE_STATUSES: list[str] = ["unlisted", "listed", "sold", "donated", "trashed"]
RESALE_CHANNELS: list[str] = ["unassigned", "ebay", "etsy", "shopify", "local", "mercari", "whatnot"]
EBAY_ENVIRONMENTS: list[str] = ["SANDBOX", "PRODUCTION"]

ITEM_TYPE_RESALE: str = "resale"
DEFAULT_STATUS: str = RESALE_STATUSES[0]       # "unlisted"
DEFAULT_CHANNEL: str = RESALE_CHANNELS[0]      # "unassigned"
DEFAULT_ENVIRONMENT: str = EBAY_ENVIRONMENTS[0]  # "SANDBOX"
