"""Custom MCP servers shipped alongside the blog-writer project.

Each subpackage is a standalone MCP server that can be:
  * called in-process by the blog-writer workflow (import its ``core`` module)
  * launched as a separate process and attached to any MCP client
    (Claude Desktop, VS Code AI Toolkit, Cursor, etc).
"""
