"""Bigtable MCP Toolset and helper tools for real-time cashier metrics and anomaly alerts."""

import base64
import json
import logging
import os
import struct
import subprocess
import urllib.request
from typing import Any

from google.adk.tools import FunctionTool, McpToolset
from google.adk.tools.mcp_tool.mcp_toolset import StreamableHTTPConnectionParams

logger = logging.getLogger(__name__)

DEFAULT_BIGTABLE_MCP_URL = os.getenv(
    "BIGTABLE_MCP_URL", "https://mcp-toolbox-bigtable-604493420076.us-central1.run.app"
)
IMPERSONATION_SA = "cymbal-sa-data@da-c3-group3.iam.gserviceaccount.com"


def get_bigtable_mcp_oidc_token(target_audience: str | None = None) -> str:
    """Generates a GCP OIDC ID token for authenticating to the Bigtable Cloud Run MCP service."""
    audience = target_audience or DEFAULT_BIGTABLE_MCP_URL
    try:
        token = (
            subprocess.check_output(
                ["gcloud", "auth", "print-access-token"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{IMPERSONATION_SA}:generateIdToken"
        payload = {"audience": audience, "includeEmail": True}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["token"]
    except Exception as e:
        logger.warning(
            "Failed to generate impersonated ID token: %s. Falling back to gcloud identity token.",
            e,
        )
        try:
            return (
                subprocess.check_output(
                    ["gcloud", "auth", "print-identity-token"],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
        except Exception as fallback_err:
            raise RuntimeError(
                f"Could not generate GCP OIDC Token: {fallback_err}"
            ) from e


def create_bigtable_mcp_toolset(
    mcp_url: str | None = None,
) -> McpToolset:
    """Creates and returns the ADK McpToolset connected to the Cloud Run Database Toolbox."""
    base_url = (mcp_url or DEFAULT_BIGTABLE_MCP_URL).rstrip("/")
    mcp_endpoint = f"{base_url}/mcp"
    token = get_bigtable_mcp_oidc_token(base_url)

    connection_params = StreamableHTTPConnectionParams(
        url=mcp_endpoint, headers={"Authorization": f"Bearer {token}"}
    )
    return McpToolset(connection_params=connection_params)


# Exported default McpToolset instance
bigtable_mcp_toolset = create_bigtable_mcp_toolset()


def _decode_double_if_b64(val: Any) -> Any:
    """Helper to decode base64 encoded IEEE 754 8-byte doubles."""
    if isinstance(val, str):
        try:
            raw = base64.b64decode(val)
            if len(raw) == 8:
                return round(struct.unpack(">d", raw)[0], 4)
        except Exception:
            pass
    return val


def read_cashier_realtime_metrics(
    store_id: str,
    cashier_id: str,
) -> dict[str, Any]:
    """Read live 1-hour rolling metrics, anomaly flags, and audit status for a specific cashier.

    Args:
        store_id: Store identifier, e.g. "STORE_048" or "48".
        cashier_id: Cashier identifier, e.g. "CASH_1190" or "1190".

    Returns:
        Dictionary containing parsed live metrics including override counts, promo rates, and audit status.
    """
    clean_store = store_id.strip().upper()
    if not clean_store.startswith("STORE_"):
        clean_store = f"STORE_{int(clean_store):03d}"

    clean_cashier = cashier_id.strip().upper()
    if not clean_cashier.startswith("CASH_"):
        clean_cashier = f"CASH_{int(clean_cashier)}"

    key_prefix = f"{clean_store}#{clean_cashier}%"

    base_url = DEFAULT_BIGTABLE_MCP_URL.rstrip("/")
    mcp_endpoint = f"{base_url}/mcp"
    token = get_bigtable_mcp_oidc_token(base_url)

    mcp_req = urllib.request.Request(
        mcp_endpoint,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "read_cashier_realtime_alerts",
                    "arguments": {"key_prefix": key_prefix},
                },
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(mcp_req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if "error" in result:
            raise RuntimeError(f"MCP RPC Error: {result['error']}")

        res_obj = result.get("result", {})
        content = res_obj.get("content", [])
        if not content:
            return {
                "status": "NO_RECORDS_FOUND",
                "message": f"No alerts found for {clean_store}#{clean_cashier}",
            }

        first_text = content[0].get("text", "{}")
        row = json.loads(first_text)

        parsed = {
            "row_key": row.get("row_key"),
            "audit_status": row.get("audit_status"),
            "last_event_ts": row.get("last_event_ts"),
            "cashier_1h_manual_override_count": row.get(
                "cashier_1h_manual_override_count"
            ),
            "cashier_1h_promo_count": row.get("cashier_1h_promo_count"),
            "cashier_1h_txn_count": row.get("cashier_1h_txn_count"),
            "cashier_1h_avg_discount_pct": _decode_double_if_b64(
                row.get("cashier_1h_avg_discount_pct")
            ),
            "cashier_1h_promo_rate": _decode_double_if_b64(
                row.get("cashier_1h_promo_rate")
            ),
            "cashier_1h_total_discount_usd": _decode_double_if_b64(
                row.get("cashier_1h_total_discount_usd")
            ),
            "risk_score": _decode_double_if_b64(row.get("risk_score")),
        }
        if parsed.get("cashier_1h_txn_count") and parsed["cashier_1h_txn_count"] > 0:
            parsed["cashier_1h_override_rate"] = round(
                (
                    parsed.get("cashier_1h_manual_override_count", 0)
                    / parsed["cashier_1h_txn_count"]
                )
                * 100.0,
                2,
            )
        return parsed


bigtable_realtime_metrics_tool = FunctionTool(func=read_cashier_realtime_metrics)
