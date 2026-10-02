"""Optional: instant price quotes through a PrintQuote-compatible MCP endpoint.

Set ``PRINTQUOTE_MCP_URL`` to a connector URL (e.g. https://…/mcp/pq_…) from
https://printquote-mcp.aiemailgenie.workers.dev — or any MCP server exposing a
``get_quote`` tool with the same arguments (product, quantity, size, paper, sides,
finishes, width_ft, height_ft, rush).
"""
from __future__ import annotations

import json
import os
import urllib.request

from .specs import ProductSpec

# print-ready product id -> (PrintQuote product, size)
MAPPING = {
    "business_card": ("business_cards", "standard"),
    "business_card_eu": ("business_cards", "standard"),
    "business_card_square": ("business_cards", "square"),
    "flyer_a6": ("flyers", "A6"),
    "flyer_a5": ("flyers", "A5"),
    "flyer_a4": ("flyers", "A4"),
    "poster_a3": ("flyers", "A3"),
    "letterhead_a4": ("letterheads", "A4"),
    "brochure_a4_trifold": ("brochures", "A4 tri-fold"),
    "sticker_2in": ("stickers", "2 x 2 in"),
    "sticker_3in": ("stickers", "3 x 3 in"),
    "flex_banner_6x3ft": ("flex_banners", None),
    "rollup_banner": ("flex_banners", None),
}


class QuoteUnavailable(RuntimeError):
    pass


def get_quote(spec: ProductSpec, quantity: int, *, double_sided: bool = False, paper: str | None = None,
              finishes: list[str] | None = None, rush: bool = False, customer_name: str | None = None,
              url: str | None = None, timeout: float = 20) -> dict:
    url = url or os.environ.get("PRINTQUOTE_MCP_URL")
    if not url:
        raise QuoteUnavailable(
            "No quote service configured. Set PRINTQUOTE_MCP_URL to your PrintQuote connector URL "
            "(free key at https://printquote-mcp.aiemailgenie.workers.dev), or ask your local printer."
        )
    base_id = spec.id
    if base_id not in MAPPING:
        raise QuoteUnavailable(f"Instant quotes aren't available for '{spec.name}' yet.")
    product, size = MAPPING[base_id]
    args: dict = {"product": product, "quantity": int(quantity), "rush": bool(rush)}
    if size:
        args["size"] = size
    if product == "flex_banners":
        args["width_ft"] = round(spec.width_mm / 304.8, 2)
        args["height_ft"] = round(spec.height_mm / 304.8, 2)
    else:
        args["sides"] = "double" if double_sided else "single"
    if paper:
        args["paper"] = paper
    if finishes:
        args["finishes"] = finishes
    if customer_name:
        args["customer_name"] = customer_name
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_quote", "arguments": args}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        raise QuoteUnavailable(f"Quote service error: {e}") from e
    if "error" in data:
        raise QuoteUnavailable(data["error"].get("message", "Quote service error"))
    result = data.get("result", {})
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    return {"text": text, "data": result.get("structuredContent"), "is_error": bool(result.get("isError"))}
