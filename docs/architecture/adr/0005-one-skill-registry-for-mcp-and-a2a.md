# ADR-0005: One skill registry behind MCP and A2A; stateless streamable HTTP

- Status: Accepted
- Date: 2026-10-14

## Context

Agents reach the platform through MCP (tool use) and A2A (agent-to-agent
delegation). Two code paths for the same operations would drift in
validation, authorization or audit.

## Decision

- `protocols/skills.py` defines each skill once: typed input model, handler,
  read-only flag, human-gate flag. MCP tools and A2A skills both call
  `protocols/invoke.py`, which authenticates, runs the use case, meters and
  audits.
- MCP uses the official `mcp` SDK 2.x `MCPServer` (the renamed FastMCP) with
  streamable HTTP mounted at `/mcp`, `stateless_http=True` and JSON
  responses, so any replica can answer and no session state is held.
  DNS-rebinding protection checks `Host` and `Origin` against the public URL.
- A2A implements the Agent Card and JSON-RPC `message/send`. Task states
  carry governance meaning: `input-required` for reapproval awaiting a
  human, `rejected` for policy denials.
- The signature operation is deliberately absent from both registries.

## Consequences

- Adding a skill is one registry entry plus a typed MCP wrapper.
- A2A tasks are not persisted (`tasks/get` is not offered): every operation
  completes synchronously and its result is retrievable via `/v1/reports`.
