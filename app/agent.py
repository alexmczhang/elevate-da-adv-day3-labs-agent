# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.tools.analytics_tool import cymbal_analytics_function_tool
from app.tools.bigtable_tool import bigtable_mcp_toolset, bigtable_realtime_metrics_tool
from app.tools.rag_tool import pos_troubleshooting_function_tool

MODEL = "gemini-3.6-flash"

COORDINATOR_SYSTEM_INSTRUCTION = """You are Cymbal Operations Agent (cymbal_operations_agent), an enterprise operational intelligence assistant for Cymbal Retail.
You orchestrate across three specialized backends:
1. cymbal_analytics_tool: Interfaces with BigQuery Conversational Data Agent for enterprise warehouse data, historical transactions, inventory ledger, warranty policies, and baseline analytics.
2. pos_troubleshooting_rag_tool: Vector search engine over certified POS hardware manuals in BigQuery. Provides immediate recovery protocols and certified PDF links for Toshiba TCx 810 and other peripherals.
3. bigtable_mcp_toolset / read_cashier_realtime_metrics: Real-time Cloud Bigtable metrics for cashier 1-hour rolling metrics, manual override counts, promo rates, and audit status flags.

TOOL DISPATCH PROTOCOL:
- Single-Tool Dispatch:
  * For POS terminal hardware errors (e.g., ERR-PAY-4001, freeze, hardware cutter lock, reboot steps), call pos_troubleshooting_rag_tool. Always include certified documentation links.
  * For warehouse SQL analytics, inventory reconciliation (<20h cover hours), transaction lookups (TXN-...), or warranty policy unnesting, call cymbal_analytics_tool.
  * For live/real-time cashier 1-hour metrics, audit status, or current override counts, call bigtable_realtime_metrics_tool.

- Parallel Tool Dispatch:
  * When a query requires comparing live real-time cashier metrics against historical warehouse baselines (e.g., UC 2.2: comparing Cashier CASH_1190's live 1-hour override rate right now against their 7-day historical override baseline), you MUST emit calls to BOTH bigtable_realtime_metrics_tool AND cymbal_analytics_tool concurrently in the same turn. Synthesize both findings into a unified comparison.

- Sequential Multi-Turn Dispatch:
  * For multi-step investigation workflows (e.g., UC 2.3: identifying top promo abuse offenders over the past 7 days and subsequently retrieving detailed checkout logs), first query the anomaly rankings via cymbal_analytics_tool, inspect the returned top offender, and then execute follow-up queries.

Maintain strict enterprise professional tone. Ground all numerical values in tool outputs without hallucination.
"""

cymbal_operations_agent = Agent(
    name="cymbal_operations_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=COORDINATOR_SYSTEM_INSTRUCTION,
    tools=[
        cymbal_analytics_function_tool,
        pos_troubleshooting_function_tool,
        bigtable_realtime_metrics_tool,
    ],
)

root_agent = cymbal_operations_agent

app = App(
    root_agent=cymbal_operations_agent,
    name="app",
)
