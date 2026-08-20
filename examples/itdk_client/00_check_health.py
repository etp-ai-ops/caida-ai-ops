"""Check the running service's operational endpoints."""

import os

import httpx


def main() -> None:
    mcp_url = os.environ.get("ITDK_MCP_URL", "http://127.0.0.1:8000/mcp")
    service_root = mcp_url.removesuffix("/mcp")
    with httpx.Client(timeout=5) as client:
        for endpoint in ("/healthz", "/readyz"):
            response = client.get(service_root + endpoint)
            print(endpoint, response.status_code, response.text)
            response.raise_for_status()


if __name__ == "__main__":
    main()
