#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:?Usage: $0 <base-url>}"
: "${MCP_TOKEN:?Export MCP_TOKEN before running this script}"

curl -fsS "${BASE_URL}/health" | python3 -m json.tool

unauthenticated_status=$(curl -sS -o /dev/null -w '%{http_code}' "${BASE_URL}/mcp")
if [[ "$unauthenticated_status" != 401 ]]; then
    echo "Expected 401 on /mcp without authentication, received $unauthenticated_status" >&2
    exit 1
fi

mcp_request() {
    local method="$1"
    curl -fsS -X POST "${BASE_URL}/mcp" \
        -H "Authorization: Bearer ${MCP_TOKEN}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -H 'MCP-Protocol-Version: 2026-07-28' \
        -H "Mcp-Method: ${method}" \
        --data-binary "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"${method}\",\"params\":{\"_meta\":{\"io.modelcontextprotocol/protocolVersion\":\"2026-07-28\",\"io.modelcontextprotocol/clientCapabilities\":{}}}}" |
        python3 -c '
import json
import sys

raw = sys.stdin.read()
if raw.lstrip().startswith("{"):
    document = json.loads(raw)
else:
    documents = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
    document = next(item for item in documents if item.get("id") == 1)
if "error" in document:
    raise SystemExit("MCP request failed: " + json.dumps(document["error"]))
result = document["result"]
if sys.argv[1] == "server/discover":
    assert result["supportedVersions"] == ["2026-07-28"], result
    print("MCP 2026-07-28 discovery passed")
else:
    names = {tool["name"] for tool in result["tools"]}
    expected = {"daily_summary", "metric_series", "sleep_detail", "get_sync_health", "inspect_last_payload", "list_data_types", "health_records"}
    assert names == expected, names
    print("MCP tools verified: " + ", ".join(sorted(names)))
' "$method"
}

mcp_request server/discover
mcp_request tools/list
printf '%s\n' 'All deployment checks passed'
