"""USD prices per 1M tokens, used for cost numbers on traces and the eval dashboard.

Source: Anthropic pricing as of 2026-11. Update this table when prices change.
"""

PRICES: dict[str, tuple[float, float]] = {
    # model: (input $/1M, output $/1M)
    "claude-haiku-5-5": (0.10, 0.50),  # for prompts up to 100K tokens
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one call in USD. Local (Ollama) and unknown models count as 0."""
    in_price, out_price = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
