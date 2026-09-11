"""POS Troubleshooting RAG Tool (pos_troubleshooting_rag_tool).

Searches and retrieves hardware diagnostics and step-by-step resolution procedures
from unstructured POS technical manuals in BigQuery using vector search with dense
embeddings (AI.EMBED / text-embedding-005) and adjacent context window stitching.
Includes a 0.70 relevance score guardrail, full-text SEARCH fallback, and clickable GCS links.
"""

import logging
import os
import re
import time

from google.adk.tools import FunctionTool
from google.cloud import bigquery

logger = logging.getLogger(__name__)

# Environment Configuration
DEFAULT_PROJECT_ID = "da-c3-group3"
PROJECT_ID = os.environ.get("PROJECT_ID", DEFAULT_PROJECT_ID)
TABLE_NAME = f"{PROJECT_ID}.cymbal_gold.pos_manual_chunk_embeddings"

MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds
SIMILARITY_THRESHOLD = 0.70


def _format_gcs_url(gcs_uri: str) -> str:
    """Converts a gs:// URI to a clickable HTTPS console URL."""
    if gcs_uri and gcs_uri.startswith("gs://"):
        return gcs_uri.replace("gs://", "https://storage.cloud.google.com/")
    return gcs_uri


def search_pos_manuals(
    query_text: str,
    project_id: str = PROJECT_ID,
    max_retries: int = MAX_RETRIES,
) -> str:
    """Executes vector search with adjacent context window stitching and full-text fallback.

    Args:
        query_text: The user inquiry or error symptom (e.g. 'ERR-PAY-4001 on Lane 3').
        project_id: The GCP project hosting the BigQuery dataset.
        max_retries: Retry attempts for transient database faults.

    Returns:
        Formatted troubleshooting guide with equipment details, stitched steps, and citations.
    """
    client = bigquery.Client(project=project_id)

    vector_sql = f"""
  WITH matched_chunks AS (
    SELECT
      base.document_filename,
      base.document_title,
      base.equipment_covered,
      base.source_pdf_uri,
      base.chunk_index,
      ROUND(1 - distance, 4) AS similarity_score
    FROM VECTOR_SEARCH(
      TABLE `{TABLE_NAME}`,
      'embedding',
      (SELECT (AI.EMBED(@query_text, endpoint => 'text-embedding-005')).result AS embedding),
      top_k => 3,
      distance_type => 'COSINE'
    )
  )
  SELECT
    m.document_filename,
    m.document_title,
    m.equipment_covered,
    m.source_pdf_uri,
    m.chunk_index AS matched_chunk_index,
    m.similarity_score,
    STRING_AGG(c.chunk_content, '\\n---\\n' ORDER BY c.chunk_index ASC) AS stitched_context
  FROM matched_chunks m
  JOIN `{TABLE_NAME}` c
    ON m.document_filename = c.document_filename
    AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
  GROUP BY
    m.document_filename,
    m.document_title,
    m.equipment_covered,
    m.source_pdf_uri,
    m.chunk_index,
    m.similarity_score
  ORDER BY m.similarity_score DESC
  LIMIT 1;
  """

    best_row = None
    last_error: Exception | None = None

    # 1. Vector Search with Exponential Backoff
    for attempt in range(1, max_retries + 1):
        try:
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("query_text", "STRING", query_text)
                ]
            )
            rows = list(client.query(vector_sql, job_config=job_config))
            if rows:
                best_row = rows[0]
            break
        except Exception as ex:
            logger.warning(
                "Vector search error on attempt %d/%d: %s", attempt, max_retries, ex
            )
            last_error = ex
            if attempt < max_retries:
                time.sleep(INITIAL_BACKOFF * (2 ** (attempt - 1)))

    # 2. Check Relevance Score Threshold (0.70)
    # If vector score < 0.70 or no vector results, trigger full-text SEARCH fallback
    if not best_row or best_row.similarity_score < SIMILARITY_THRESHOLD:
        logger.info(
            "Vector search similarity (%s) below threshold (%s). Triggering SEARCH fallback.",
            getattr(best_row, "similarity_score", None),
            SIMILARITY_THRESHOLD,
        )

        # Extract specific error code pattern (e.g., ERR-PAY-4001, ERR-TGCS-PWR-90W)
        err_match = re.search(r"\b(ERR-[A-Za-z0-9-]+)\b", query_text)
        search_token = f"`{err_match.group(1)}`" if err_match else query_text

        fallback_sql = f"""
    WITH matched_chunks AS (
      SELECT
        document_filename,
        document_title,
        equipment_covered,
        source_pdf_uri,
        chunk_index,
        1.0 AS similarity_score
      FROM `{TABLE_NAME}`
      WHERE SEARCH(chunk_content, @search_term)
      ORDER BY chunk_index ASC
      LIMIT 1
    )
    SELECT
      m.document_filename,
      m.document_title,
      m.equipment_covered,
      m.source_pdf_uri,
      m.chunk_index AS matched_chunk_index,
      m.similarity_score,
      STRING_AGG(c.chunk_content, '\\n---\\n' ORDER BY c.chunk_index ASC) AS stitched_context
    FROM matched_chunks m
    JOIN `{TABLE_NAME}` c
      ON m.document_filename = c.document_filename
      AND c.chunk_index BETWEEN (m.chunk_index - 1) AND (m.chunk_index + 1)
    GROUP BY
      m.document_filename,
      m.document_title,
      m.equipment_covered,
      m.source_pdf_uri,
      m.chunk_index,
      m.similarity_score;
    """

        for attempt in range(1, max_retries + 1):
            try:
                fallback_config = bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter(
                            "search_term", "STRING", search_token
                        )
                    ]
                )
                fallback_rows = list(
                    client.query(fallback_sql, job_config=fallback_config)
                )
                if fallback_rows:
                    best_row = fallback_rows[0]
                    logger.info(
                        "SEARCH fallback successfully matched: %s",
                        best_row.document_title,
                    )
                break
            except Exception as ex:
                logger.warning(
                    "Fallback search error on attempt %d/%d: %s",
                    attempt,
                    max_retries,
                    ex,
                )
                last_error = ex
                if attempt < max_retries:
                    time.sleep(INITIAL_BACKOFF * (2 ** (attempt - 1)))

    # 3. Format Response or Graceful Warning
    if best_row:
        https_url = _format_gcs_url(best_row.source_pdf_uri)
        return (
            f"### POS Troubleshooting Manual: {best_row.document_title}\n"
            f"- **Equipment Covered:** {best_row.equipment_covered}\n"
            f"- **Documentation Source:** [{best_row.document_filename}]({https_url})\n"
            f"- **Relevance Score:** {best_row.similarity_score}\n\n"
            f"#### Diagnostic & Resolution Procedure (Stitched Adjacent Window):\n"
            f"{best_row.stitched_context}"
        )

    logger.error(
        "No troubleshooting guide matched or database unreachable: %s", last_error
    )
    return (
        "Warning: No verified POS troubleshooting manual found matching the error code or symptom "
        "with sufficient confidence (threshold 0.70). Please contact hardware depot technical support."
    )


def pos_troubleshooting_rag_tool(query: str) -> str:
    """Searches POS hardware service manuals for troubleshooting procedures and error code solutions.

    Use this tool when users ask about:
    - POS hardware terminal error codes (e.g. ERR-PAY-4001, ERR-TGCS-PWR-90W, continuous beeps).
    - PIN pad timeouts, EMV payment terminal reboots, scanner stalls, and receipt printer jams.
    - Cash drawer opening failures, cable reseating, and hardware maintenance steps.

    Args:
        query: The user inquiry describing the POS hardware issue, terminal error, or symptom.

    Returns:
        Extracted diagnostic and step-by-step resolution instructions with clickable manual links.
    """
    return search_pos_manuals(query_text=query)


# ADK FunctionTool wrapper
pos_troubleshooting_function_tool = FunctionTool(pos_troubleshooting_rag_tool)
