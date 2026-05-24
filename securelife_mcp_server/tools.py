# securelife_mcp_server/tools.py
import os
import sqlite3

# 1. Database connection (shared by all tools)
DB_PATH = "../SecureLife_claims.db"
if not os.path.exists(DB_PATH):
    raise FileNotFoundError(f"⚠ {DB_PATH} not found. Ensure the DB is in the parent directory.")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def query_claims(sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# 2. Tool registration — called by server.py to attach all tools to the MCP instance
def register_tools(mcp):
    """Register every SecureLife claim tool against the given FastMCP instance."""

    @mcp.tool()
    def fetch_claim(claim_id: str) -> dict:
        """Fetch claim with joined customer + policy + hospital."""
        rows = query_claims(
            """SELECT c.*, cu.full_name, cu.city,
                      p.policy_type, p.sum_insured, p.product_name,
                      h.name AS hospital_name, h.network_status, h.fraud_flag_count
               FROM claims c JOIN customers cu ON c.customer_id=cu.customer_id
               JOIN policies p ON c.policy_id=p.policy_id
               LEFT JOIN hospitals h ON c.hospital_id=h.hospital_id
               WHERE c.claim_id = ?""", (claim_id,))
        return rows[0] if rows else {}

    @mcp.tool()
    def verify_documents(claim_id: str) -> dict:
        """Cross-check submitted vs required documents."""
        rows = query_claims(
            """SELECT rd.doc_code, COALESCE(cd.status, 'MISSING') AS status
               FROM claims c
               JOIN required_documents rd ON c.policy_id IN
                    (SELECT policy_id FROM policies WHERE policy_type = rd.claim_type)
               LEFT JOIN claim_documents cd ON cd.claim_id = c.claim_id AND cd.doc_code = rd.doc_code
               WHERE c.claim_id = ?""", (claim_id,))
        missing = [r['doc_code'] for r in rows if r['status'] == 'MISSING']
        return {"complete": len(missing) == 0, "missing": missing,
                "submitted": [r['doc_code'] for r in rows if r['status'] == 'RECEIVED']}

    @mcp.tool()
    def calculate_fraud_score(claim_id: str) -> dict:
        """Sum the weights of all fraud_indicators for this claim."""
        rows = query_claims(
            "SELECT indicator_code, description, weight FROM fraud_indicators WHERE claim_id = ?",
            (claim_id,))
        return {"score": round(sum(r['weight'] for r in rows), 2),
                "indicators": rows, "count": len(rows)}

    @mcp.tool()
    def update_claim_status(claim_id: str, new_status: str, reason: str, actor: str = "agent:claims_pipeline") -> dict:
        """Update claims.status AND insert claim_history audit row — transactional."""
        cur = conn.cursor()
        cur.execute("SELECT status FROM claims WHERE claim_id = ?", (claim_id,))
        row = cur.fetchone()
        if not row:
            return {"error": f"claim {claim_id} not found"}
        prev = row["status"]
        try:
            cur.execute("BEGIN")
            cur.execute("UPDATE claims SET status = ? WHERE claim_id = ?", (new_status, claim_id))
            cur.execute(
                "INSERT INTO claim_history (claim_id, prev_status, new_status, actor, reason) "
                "VALUES (?, ?, ?, ?, ?)", (claim_id, prev, new_status, actor, reason))
            conn.commit()
        except Exception as e:
            conn.rollback()
            return {"error": str(e)}
        return {"claim_id": claim_id, "prev_status": prev, "new_status": new_status,
                "actor": actor, "audit_logged": True}
