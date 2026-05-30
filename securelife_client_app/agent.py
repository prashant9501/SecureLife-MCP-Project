# securelife_client_app/agent.py
import os
import re
import json
from typing import TypedDict, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# Initialize the LLM
llm = ChatOpenAI(model="gpt-5.4", streaming=True)

# ==========================================
# 1. SecureLife MCP Async Client Wrapper
# ==========================================
class SecureLifeMCPAsync:
    def __init__(self, server_url="http://localhost:8765/mcp"):
        self.server_url = server_url

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Connects via Streamable HTTP to the MCP server and handles async communication."""
        async with streamable_http_client(self.server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
                return json.loads(result.content[0].text)

# Initialize the networked client
mcp_client = SecureLifeMCPAsync()

# ==========================================
# 2. Guardrails Setup
# ==========================================
_INJ = [r"ignore\s+(all\s+)?(previous|above)\s+instructions", r"system\s+prompt",
        r"jailbreak|DAN\s+mode", r"approve\s+(this|the|all)?\s*claims?\s+(regardless|anyway)",
        r"(set|reset)\s+fraud[_\s-]?score\s+to\s+0", r"bypass\s+(fraud|document|kyc)\s+check",
        r"\bUNION\s+SELECT\b", r"\bDROP\s+TABLE\b", r";\s*(SELECT|DROP|DELETE|UPDATE)", r"--\s*$"]
_PII = {"PAN": r"[A-Z]{5}\d{4}[A-Z]", "AADHAAR": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
        "IFSC": r"[A-Z]{4}0[A-Z0-9]{6}", "PHONE": r"\+91[-\s]?[6-9]\d{9}",
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"}

class GuardrailPipeline:
    def check_input(self, text):
        if len(text) > 1500: return False, ["oversize"]
        for pat in _INJ:
            if re.search(pat, text, re.IGNORECASE): return False, ["Prompt Injection Attempt"]
        return True, []
    
    def check_output(self, text):
        out = text
        for ptype, pat in _PII.items():
            out = re.sub(pat, f"[{ptype}_REDACTED]", out)
        return out

guard = GuardrailPipeline()

# ==========================================
# 3. Async LangGraph Setup
# ==========================================
class AgentState(TypedDict):
    claim_id: str
    claim_record: dict
    doc_check: dict
    fraud: dict
    decision: dict
    audit_result: dict
    user_note: Optional[str]

async def triage_node(state: AgentState) -> dict:
    note = state.get("user_note") or ""
    if note:
        ok, viols = guard.check_input(note)
        if not ok:
            return {
                "claim_record": {"error": "input blocked", "violations": viols},
                "decision": {"action": "BLOCKED", "reason": f"Input rejected by safety filters: {viols}"}
            }
    # Direct Async Network Call over MCP Streamable HTTP
    rec = await mcp_client._call_tool("fetch_claim", {"claim_id": state["claim_id"]})
    return {"claim_record": rec}

async def doc_verifier_node(state: AgentState) -> dict:
    docs = await mcp_client._call_tool("verify_documents", {"claim_id": state["claim_id"]})
    return {"doc_check": docs}

async def fraud_analyst_node(state: AgentState) -> dict:
    fraud = await mcp_client._call_tool("calculate_fraud_score", {"claim_id": state["claim_id"]})
    return {"fraud": fraud}

decide_prompt = ChatPromptTemplate.from_template(
    "You are SecureLife's senior claims adjudicator. Decide ONE action: APPROVE, REVIEW, or REJECT.\n"
    "Heuristic guidance:\n"
    "- documents incomplete → REVIEW (request docs)\n"
    "- fraud_score ≥ 0.6 → REVIEW or REJECT (flag for senior review)\n"
    "- otherwise APPROVE\n\n"
    "Claim record: {record}\n"
    "Document check: {docs}\n"
    "Fraud analysis: {fraud}\n\n"
    "Return ONLY JSON: {{\"action\": \"APPROVE|REVIEW|REJECT\", \"reason\": \"≤ 1 sentence\"}}"
)
decide_chain = decide_prompt | llm

async def decision_maker_node(state: AgentState) -> dict:
    response = await decide_chain.ainvoke({
        "record": json.dumps(state["claim_record"]),
        "docs":   json.dumps(state["doc_check"]),
        "fraud":  json.dumps(state["fraud"])
    })
    raw = response.content.strip()
    
    if raw.startswith("```"):
        raw = raw.split("```")[1].replace("json", "").strip()
    try:
        d = json.loads(raw)
    except Exception:
        d = {"action": "REVIEW", "reason": "unparseable LLM output"}
        
    d["reason"] = guard.check_output(d.get("reason", ""))
    return {"decision": d}

async def compliance_auditor_node(state: AgentState) -> dict:
    decision = state["decision"]
    new_status = {"APPROVE": "APPROVED", "REVIEW": "UNDER_REVIEW", "REJECT": "REJECTED"}.get(decision["action"], "UNDER_REVIEW")
    
    res = await mcp_client._call_tool("update_claim_status", {
        "claim_id": state["claim_id"], 
        "new_status": new_status,
        "reason": decision.get("reason", ""), 
        "actor": "agent:chainlit_ui"
    })
    return {"audit_result": res}

# Compile Graph Natively Async
graph = StateGraph(AgentState)
graph.add_node("triage", triage_node)
graph.add_node("doc_verifier", doc_verifier_node)
graph.add_node("fraud_analyst", fraud_analyst_node)
graph.add_node("decision_maker", decision_maker_node)
graph.add_node("compliance_auditor", compliance_auditor_node)

def route_after_triage(state: AgentState) -> str:
    # Short-circuit to END when triage's guardrail blocks the request,
    # so downstream nodes don't fire on a half-populated state.
    if state.get("decision", {}).get("action") == "BLOCKED":
        return END
    return "doc_verifier"

graph.set_entry_point("triage")
graph.add_conditional_edges("triage", route_after_triage, {END: END, "doc_verifier": "doc_verifier"})
graph.add_edge("doc_verifier", "fraud_analyst")
graph.add_edge("fraud_analyst", "decision_maker")
graph.add_edge("decision_maker", "compliance_auditor")
graph.add_edge("compliance_auditor", END)

# Export the compiled graph to be imported by app.py
compiled_graph = graph.compile()