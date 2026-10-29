"""Indicative Bedrock inference prices; never a substitute for AWS billing.

DeepSeek Sydney standard: https://aws.amazon.com/bedrock/pricing/
Nova Lite text: https://aws.amazon.com/blogs/machine-learning/customizing-text-content-moderation-with-amazon-nova/
Titan V2: https://aws.amazon.com/blogs/machine-learning/get-started-with-amazon-titan-text-embeddings-v2-a-new-state-of-the-art-embeddings-model-on-amazon-bedrock/
The two AWS articles give general model rates; region-specific differences and
credits, taxes, retries, service tiers or billing adjustments are not captured.
"""

from typing import Any

PRICE_REFERENCE = "2026-10-29"


def estimated_cost_usd(call: dict[str, Any]) -> float | None:
    incoming, outgoing = call.get("input_tokens"), call.get("output_tokens")
    if (
        not isinstance(incoming, int)
        or not isinstance(outgoing, int)
        or call.get("status") != "completed"
    ):
        return None
    model = str(call.get("model_id", ""))
    if model == "deepseek.v3.2" and call.get("region") == "ap-southeast-2":
        rates = (0.6386, 1.9055)
    elif model.endswith("amazon.nova-lite-v1:0"):
        rates = (0.06, 0.24)
    elif model == "amazon.titan-embed-text-v2:0":
        rates = (0.02, 0.0)
    else:
        return None
    return round((incoming * rates[0] + outgoing * rates[1]) / 1_000_000, 8)
