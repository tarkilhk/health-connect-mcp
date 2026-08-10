#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:?Usage: $0 <base-url>}"
echo "=== Health check ==="
curl -fsS "${BASE_URL}/health" | python3 -m json.tool

echo "=== Auth check (expect 401) ==="
curl -fsS -o /dev/null -w "%{http_code}" "${BASE_URL}/debug" || true
echo ""

echo "=== MCP initialize ==="
curl -fsS -X POST "${BASE_URL}/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}' | python3 -m json.tool

echo "=== All checks passed ==="
