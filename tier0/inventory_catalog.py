"""
inventory_catalog.py — export a data inventory from the Fabric OneLake Catalog.

SYNTHETIC DATA ONLY (accelerator). Read-only: lists items so stewards can review coverage
and drive classification. It returns *metadata* (item names, types, workspaces) — never the
underlying data — which is exactly why catalog discovery is safe (see the EIS one-pager).

Two ways to run:
  1. Inside a Fabric notebook (recommended): uses the current auth context.
  2. Standalone: pass an Entra bearer token with Fabric read scope.

The Fabric REST surface used here is the read-only Items API:
    GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items
Docs: https://learn.microsoft.com/rest/api/fabric/core/items/list-items

This script deliberately performs only GET calls.
"""

from __future__ import annotations

import csv
import json
import sys
from typing import Any
from urllib.request import Request, urlopen

FABRIC_API = "https://api.fabric.microsoft.com/v1"


def _get(url: str, token: str) -> dict[str, Any]:
    req = Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310 - fixed https host, read-only
    with urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https host, read-only
        return json.loads(resp.read().decode("utf-8"))


def list_workspaces(token: str) -> list[dict[str, Any]]:
    """List workspaces the caller can read."""
    data = _get(f"{FABRIC_API}/workspaces", token)
    return data.get("value", [])


def list_items(token: str, workspace_id: str) -> list[dict[str, Any]]:
    """List items (Lakehouses, Warehouses, semantic models, ...) in a workspace.

    Follows continuationToken paging.
    """
    items: list[dict[str, Any]] = []
    url = f"{FABRIC_API}/workspaces/{workspace_id}/items"
    while url:
        data = _get(url, token)
        items.extend(data.get("value", []))
        token_next = data.get("continuationToken")
        url = (
            f"{FABRIC_API}/workspaces/{workspace_id}/items?continuationToken={token_next}"
            if token_next
            else ""
        )
    return items


def build_inventory(token: str) -> list[dict[str, Any]]:
    """Return a flat inventory of (workspace, item) rows across all readable workspaces."""
    rows: list[dict[str, Any]] = []
    for ws in list_workspaces(token):
        ws_id, ws_name = ws.get("id"), ws.get("displayName")
        for item in list_items(token, ws_id):
            rows.append(
                {
                    "workspace": ws_name,
                    "workspace_id": ws_id,
                    "item_name": item.get("displayName"),
                    "item_type": item.get("type"),
                    "item_id": item.get("id"),
                    "description": item.get("description", ""),
                }
            )
    return rows


def write_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        print("No items found (check token scope).")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} inventory rows -> {path}")


def get_token_from_notebook() -> str:
    """Fetch a Fabric-scoped token when running inside a Fabric notebook."""
    import notebookutils  # type: ignore  # provided by the Fabric runtime

    return notebookutils.credentials.getToken("https://api.fabric.microsoft.com")


if __name__ == "__main__":
    # Standalone usage: python inventory_catalog.py <bearer_token> [out.csv]
    if len(sys.argv) < 2:
        print("Usage: python inventory_catalog.py <bearer_token> [out.csv]")
        print("Inside a Fabric notebook, call build_inventory(get_token_from_notebook()).")
        raise SystemExit(1)
    bearer = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "catalog_inventory.csv"
    write_csv(build_inventory(bearer), out)
