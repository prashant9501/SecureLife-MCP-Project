# securelife_client_app/app.py
import re
import json
import chainlit as cl
from dotenv import load_dotenv

# Ensure environment variables are loaded for the runtime
load_dotenv()

# Import the compiled LangGraph workflow from our decoupled agent file
from agent import compiled_graph

@cl.on_chat_start
async def start():
    await cl.Message(
        content="👋 **Welcome to SecureLife Claims Processing Hub**\n\n"
                "Please submit your requests matching the format:\n"
                "`[CLAIM_ID] | [Optional notes or adversarial text]`\n\n"
                "*Example:* `CLM-2025-0002 | Please process urgently.`"
    ).send()

@cl.on_message
async def main(message: cl.Message):
    # Parse out Claim ID (e.g., CLM-2025-0002)
    match = re.search(r"\bCLM-\d{4}-\d{4}\b", message.content)
    if not match:
        await cl.Message(content="❌ Could not parse a valid Claim ID. Please include a target identifier like `CLM-2025-0002`.").send()
        return

    claim_id = match.group(0)
    
    # Treat anything remaining or separated by '|' as the customer note
    if "|" in message.content:
        user_note = message.content.split("|", 1)[1].strip()
    else:
        user_note = message.content.replace(claim_id, "").strip()

    # Inform user orchestration is beginning
    status_msg = cl.Message(content=f"⚙️ Orchestrating LangGraph pipeline for **{claim_id}**...")
    await status_msg.send()

    # Define initial pipeline state
    initial_state = {
        "claim_id": claim_id,
        "user_note": user_note
    }

    # We will accumulate the final state as the nodes execute
    final_state = {}

    stream = compiled_graph.astream(initial_state, stream_mode="updates")
    try:
        # astream() yields {node_name: state_update} as each node completes.
        async for output in stream:
            for node_name, state_update in output.items():
                # LangGraph can yield empty/None updates for skipped nodes;
                # skip them so dict.update doesn't blow up and we don't render empty UI steps.
                if not state_update:
                    continue

                # Keep our local state updated for the final summary
                final_state.update(state_update)

                # Create a distinct, collapsible UI step for each node
                async with cl.Step(name=f"⚙️ Node: {node_name}") as step:

                    # Custom UI formatting based on which node just finished
                    if node_name == "triage":
                        if "error" in state_update.get("claim_record", {}):
                            step.output = f"🛑 **Guardrail Blocked:** {state_update['claim_record']['violations']}"
                            step.is_error = True
                        else:
                            # Show a snippet of the fetched data
                            step.output = f"✅ Claim record fetched successfully.\n```json\n{json.dumps(state_update.get('claim_record', {}), indent=2)}\n```"

                    elif node_name == "doc_verifier":
                        docs = state_update.get("doc_check", {})
                        step.output = (f"📄 **Documents Complete?** {docs.get('complete')}\n"
                                       f"**Missing:** {docs.get('missing')}\n"
                                       f"**Submitted:** {docs.get('submitted')}")
                                      
                    elif node_name == "fraud_analyst":
                        fraud = state_update.get("fraud", {})
                        step.output = (f"🚨 **Fraud Score:** {fraud.get('score')}\n"
                                       f"**Indicators Found:** {fraud.get('count')}")
                                      
                    elif node_name == "decision_maker":
                        decision = state_update.get("decision", {})
                        step.output = (f"🤖 **Action:** {decision.get('action')}\n"
                                       f"**Reason:** {decision.get('reason')}")
                                      
                    elif node_name == "compliance_auditor":
                        audit = state_update.get("audit_result", {})
                        step.output = (f"💾 **Audit Logged:** {audit.get('audit_logged', False)}\n"
                                       f"**Status Change:** {audit.get('prev_status')} ➡️ {audit.get('new_status')}")
                    
                    else:
                        step.output = f"Processed properties: {list(state_update.keys())}"

        # Retrieve variables from the accumulated final state
        decision = final_state.get("decision", {})
        audit = final_state.get("audit_result", {})

        # Formulate final clean UI response summary
        if decision.get("action") == "BLOCKED":
            response_md = (
                f"### 🛑 Request Terminated by Guardrails\n"
                f"**Reason:** {decision.get('reason')}\n"
                f"No backend queries or updates were performed."
            )
        else:
            response_md = (
                f"### 📋 Final Evaluation Summary\n"
                f"--- \n"
                f"- **Claim Target:** `{claim_id}`\n"
                f"- **Adjudication Choice:** **{decision.get('action')}**\n"
                f"- **Justification:** *{decision.get('reason')}*\n\n"
                f"#### 🔒 Compliance & Audit Verification\n"
                f"- **DB Transition Status:** `{audit.get('prev_status')} ➡️ {audit.get('new_status')}`\n"
                f"- **Actor Signature:** `{audit.get('actor')}`\n"
                f"- **Immutable Transaction Confirmed:** `{audit.get('audit_logged', False)}`"
            )

        await cl.Message(content=response_md).send()

    except Exception as e:
        await cl.Message(content=f"⚠ An execution fault occurred during the network run: {str(e)}").send()
    finally:
        # Explicitly close the async generator so Python doesn't warn
        # "async generator ignored GeneratorExit" if we exited via exception.
        await stream.aclose()
        await status_msg.remove()