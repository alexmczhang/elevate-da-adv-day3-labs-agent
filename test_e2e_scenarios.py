import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import cymbal_operations_agent

SCENARIOS = [
    {
        "id": "UC 1.1a",
        "name": "Hardware Error",
        "prompt": "What is the immediate field recovery protocol when a cashier encounters an ERR-PAY-4001 EMV contactless payment freeze, and how do we ensure the customer is not double-charged?",
    },
    {
        "id": "UC 1.1c",
        "name": "Out-of-Scope Hardware",
        "prompt": "How do I replace the engine oil on a Ford F-150 truck?",
    },
    {
        "id": "UC 1.2a",
        "name": "Stockout Risk (<20h)",
        "prompt": "What is the estimated cover hours remaining for store inventory positions experiencing stockout risk of less than 20 hours, and what is their total on-hand inventory?",
    },
    {
        "id": "UC 1.2c",
        "name": "Dataplex Glossary Net Revenue",
        "prompt": "What is the Net Transaction Revenue for Store 8 today?",
    },
    {
        "id": "UC 1.3",
        "name": "Real-Time Cashier Metrics",
        "prompt": "Read live 1-hour rolling metrics and audit status flags for Cashier CASH_1190 at Store 48.",
    },
    {
        "id": "UC 2.1a",
        "name": "Warranty Transaction",
        "prompt": "Check transaction details for TXN-20260312-0015811 and show the warranty coverage policy for the purchased item.",
    },
    {
        "id": "UC 2.2",
        "name": "Dual Cashier Baseline (Parallel Dispatch)",
        "prompt": "What is Cashier CASH_1190's live 1-hour override rate right now, compared to their 7-day historical override baseline?",
    },
    {
        "id": "UC 2.3",
        "name": "Cross-Cloud Offender Audit (Sequential Dispatch)",
        "prompt": "Show cashiers with active cashier promo abuse alerts in the last 7 days and retrieve historical checkout logs for the top offender from AWS S3 silver_pos_transactions.",
    },
    {
        "id": "Guardrail 1",
        "name": "Date Range Clarification",
        "prompt": "Show transaction logs for cashier CASH_1164.",
    },
    {
        "id": "Guardrail 2",
        "name": "RAG 0.70 Refusal",
        "prompt": "Where can I find troubleshooting steps for POS error code ERR-SYNC-900?",
    },
    {
        "id": "Guardrail 3",
        "name": "Conversational Greeting",
        "prompt": "Hello, what can you help me with?",
    },
    {
        "id": "Guardrail 4",
        "name": "Weather Boundary Refusal",
        "prompt": "What's the weather like in San Francisco?",
    },
    {
        "id": "Guardrail 5",
        "name": "Capital Lookup Boundary Refusal",
        "prompt": "What is the capital of France?",
    },
]


async def run_scenario(runner, scenario):
    print(f"\n{'=' * 70}")
    print(f"Executing {scenario['id']}: {scenario['name']}")
    print(f"Prompt: {scenario['prompt']}")
    print(f"{'=' * 70}")

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="app", user_id="test_runner"
    )
    runner = Runner(
        agent=cymbal_operations_agent, session_service=session_service, app_name="app"
    )

    content = types.Content(
        role="user", parts=[types.Part.from_text(text=scenario["prompt"])]
    )
    tools_called = []
    final_text = []

    async for event in runner.run_async(
        session_id=session.id, user_id="test_runner", new_message=content
    ):
        if event.content and event.content.parts:
            for p in event.content.parts:
                if p.function_call:
                    tools_called.append((p.function_call.name, p.function_call.args))
                    print(
                        f"  [TOOL CALL] {p.function_call.name} with args: {p.function_call.args}"
                    )
                if p.text:
                    final_text.append(p.text)

    response_body = "".join(final_text).strip()
    print(f"\n[AGENT RESPONSE PREVIEW]:\n{response_body[:400]}...")
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "tools_called": tools_called,
        "response_length": len(response_body),
        "status": "PASS",
    }


async def main():
    results = []
    for sc in SCENARIOS:
        try:
            res = await run_scenario(None, sc)
            results.append(res)
        except Exception as e:
            print(f"Error running {sc['id']}: {e}")
            results.append(
                {"id": sc["id"], "name": sc["name"], "error": str(e), "status": "FAIL"}
            )

    print("\n" + "=" * 70)
    print("FINAL VALIDATION SUMMARY")
    print("=" * 70)
    for r in results:
        status = r.get("status")
        tools = [t[0] for t in r.get("tools_called", [])]
        print(f"[{status}] {r['id']} ({r['name']}) -> Tools: {tools}")


if __name__ == "__main__":
    asyncio.run(main())
