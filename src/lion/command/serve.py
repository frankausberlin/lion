"""The ``lion serve`` command: run the MCP server over stdio.

The command is a thin shell around :func:`lion.program.serve.build_server`: it
builds the server (which reads and validates the prompt defaults once) and runs
it on the stdio transport. Nothing is collected and nothing is written except
by the two ADR tools; every log line stays on stderr, stdout carries only
JSON-RPC.
"""

from lion.program.serve import build_server


def run() -> None:
    """Start the LION MCP server on stdio (blocking)."""
    server = build_server()
    server.run(transport="stdio", show_banner=False)
