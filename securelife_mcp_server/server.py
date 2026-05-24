# securelife_mcp_server/server.py
from fastmcp import FastMCP
from tools import register_tools

# Initialize FastMCP Server and attach all claim tools from tools.py
mcp = FastMCP("securelife-claims")
register_tools(mcp)

if __name__ == "__main__":
    print("🚀 Starting SecureLife MCP Server on port 8765...")
    mcp.run(transport="streamable-http", port=8765)
