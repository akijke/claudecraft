"""MCP example. Use the bridge venv interpreter in manifest.command when needed."""
import platform
import shutil
from mcp.server.fastmcp import FastMCP

server = FastMCP('system-info')

@server.tool()
def system_info() -> dict:
    """Report OS and available command-line integrations, without reading credentials."""
    return {'os': platform.system(), 'architecture': platform.machine(),
            'docker': bool(shutil.which('docker')), 'ssh': bool(shutil.which('ssh'))}

if __name__ == '__main__':
    server.run(transport='stdio')
