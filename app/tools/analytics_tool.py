"""BigQuery Conversational Data Agent Tool (cymbal_analytics_tool).

Connects to the published BigQuery Conversational Data Agent in global location
to execute natural language queries against Cymbal Retail analytical ledgers.
Includes transient fault tolerance (exponential backoff) and graceful fallback.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

# Constants
DEFAULT_PROJECT_ID = "da-c3-group3"
DEFAULT_DATA_AGENT_ID = "gda-ac82788d-57d5-46a9-88a7-c87293226a97"
DEFAULT_LOCATION = "global"

PROJECT_ID = os.environ.get("PROJECT_ID", DEFAULT_PROJECT_ID)
DATA_AGENT_ID = os.environ.get("DATA_AGENT_ID", DEFAULT_DATA_AGENT_ID)
DATA_AGENT_RESOURCE_NAME = os.environ.get(
    "DATA_AGENT_RESOURCE_NAME",
    f"projects/{PROJECT_ID}/locations/{DEFAULT_LOCATION}/dataAgents/{DATA_AGENT_ID}",
)

MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds


def query_data_agent(
    prompt: str,
    data_agent_resource_name: str = DATA_AGENT_RESOURCE_NAME,
    max_retries: int = MAX_RETRIES,
) -> str:
  """Sends a natural language business query verbatim to the published Data Agent.

  Args:
      prompt: The exact, verbatim business inquiry or natural language question.
      data_agent_resource_name: Full resource name of the Data Agent.
      max_retries: Number of retry attempts for transient errors.

  Returns:
      Formatted markdown string with the analytical results or a fallback message.
  """
  parts = data_agent_resource_name.split("/dataAgents/")[0]
  chat_url = f"https://geminidataanalytics.googleapis.com/v1alpha/{parts}:chat"

  payload = {
      "parent": parts,
      "messages": [{"userMessage": {"text": prompt}}],
      "dataAgentContext": {
          "dataAgent": data_agent_resource_name,
          "contextVersion": "PUBLISHED",
      },
  }

  creds, _ = google.auth.default()
  session = AuthorizedSession(creds)

  last_error: Optional[Exception] = None
  for attempt in range(1, max_retries + 1):
    try:
      response = session.post(chat_url, json=payload, timeout=60)
      if response.status_code == 200:
        data = response.json()
        generated_sql = None
        final_responses: List[str] = []

        for item in data:
          sm = item.get("systemMessage", {})
          if "data" in sm and "generatedSql" in sm["data"]:
            generated_sql = sm["data"]["generatedSql"]
          if "text" in sm and sm["text"].get("textType") == "FINAL_RESPONSE":
            parts_text = "\n".join(sm["text"].get("parts", []))
            if parts_text:
              final_responses.append(parts_text)

        if final_responses:
          result_body = "\n\n".join(final_responses)
        elif generated_sql:
          result_body = f"Generated query executed successfully:\n```sql\n{generated_sql}\n```"
        else:
          result_body = "The analytical inquiry completed, but no direct narrative response was returned."

        return result_body

      logger.warning(
          "Data Agent API returned HTTP %d on attempt %d/%d: %s",
          response.status_code,
          attempt,
          max_retries,
          response.text,
      )
      last_error = RuntimeError(f"HTTP {response.status_code}: {response.text}")

    except Exception as ex:
      logger.warning(
          "Transient error connecting to Data Agent on attempt %d/%d: %s",
          attempt,
          max_retries,
          str(ex),
      )
      last_error = ex

    if attempt < max_retries:
      sleep_time = INITIAL_BACKOFF * (2 ** (attempt - 1))
      time.sleep(sleep_time)

  logger.error("Failed to query Data Agent after %d retries: %s", max_retries, last_error)
  return "Store data is currently unreachable. Please verify network connectivity and try again later."


def cymbal_analytics_tool(query: str) -> str:
  """Executes conversational analytical queries and NL2SQL operations against Cymbal Retail data.

  Use this tool for:
  - Inventory depletion, stockout risk analysis, and estimated cover hours.
  - Historical sales metrics, gross/net transaction revenue, and discount rates.
  - Warranty claims lookup, coverage policies, and item transaction history.
  - Multi-day historical cashier performance baselines and anomaly audit logs.

  Always pass user natural language inquiries and enterprise metric definitions verbatim.

  Args:
      query: The verbatim user inquiry or analytical question to resolve.

  Returns:
      Analytical insights, metrics summary, or data tables formatted in markdown.
  """
  return query_data_agent(prompt=query)


# ADK FunctionTool wrapper
cymbal_analytics_function_tool = FunctionTool(cymbal_analytics_tool)
