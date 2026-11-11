"""SharePoint Online document source via Microsoft Graph.

Produces the same ``SourceDocument`` objects as the local source, so the ingestion,
retrieval and security-trimming pipeline is identical.

Permission mapping: each item's Graph permissions are read and the *group object IDs*
granted access become ``allowed_groups``. At query time the user's token ``groups`` claim
(Entra group object IDs) is intersected with them - i.e. the backend never returns content
a user could not open in SharePoint. Sensitivity labels are read via the driveItem
``extractSensitivityLabels`` action; documents labelled Highly Confidential are skipped.

Requires app permission ``Sites.Selected`` (granted to this one site only - least privilege)
and a tenant. Verified here with ``httpx.MockTransport`` unit tests only.

Note: in the Copilot Studio deployment the agent uses Copilot Studio's *native* SharePoint
knowledge source, which already enforces the signed-in user's permissions. This adapter
is for the custom backend RAG path (e.g. the web demo or a non-Copilot client).
"""
from __future__ import annotations

import logging
from typing import Iterable

from app.integrations.common import IntegrationError, with_retry
from app.integrations.graph_client import GraphClient
from app.services.knowledge.ingestion import SUPPORTED_EXTENSIONS, SourceDocument

logger = logging.getLogger(__name__)
BLOCKED_LABELS = {"highly confidential", "restricted"}


class SharePointGraphSource:
    name = "sharepoint"

    def __init__(self, client: GraphClient, site_id: str, drive_id: str, max_items: int = 500):
        self._graph = client
        self._site = site_id
        self._drive = drive_id
        self._max = max_items

    def _children(self, item_id: str | None) -> Iterable[dict]:
        path = (f"/sites/{self._site}/drives/{self._drive}/items/{item_id}/children" if item_id
                else f"/sites/{self._site}/drives/{self._drive}/root/children")
        url: str | None = path
        while url:
            page = with_retry("sharepoint_list", lambda u=url: self._graph.get_json(u))
            yield from page.get("value", [])
            url = page.get("@odata.nextLink")

    def _walk(self) -> Iterable[dict]:
        stack: list[str | None] = [None]
        seen = 0
        while stack:
            for item in self._children(stack.pop()):
                if "folder" in item:
                    stack.append(item["id"])
                elif "file" in item:
                    seen += 1
                    if seen > self._max:
                        return
                    yield item

    def _groups_for(self, item_id: str) -> list[str]:
        perms = with_retry("sharepoint_permissions", lambda: self._graph.get_json(
            f"/sites/{self._site}/drives/{self._drive}/items/{item_id}/permissions"))
        groups: set[str] = set()
        for perm in perms.get("value", []):
            for identity in perm.get("grantedToIdentitiesV2", []) + [perm.get("grantedToV2", {})]:
                group = (identity or {}).get("group") or (identity or {}).get("siteGroup")
                if group and group.get("id"):
                    groups.add(group["id"])
        return sorted(groups)

    def _label_for(self, item_id: str) -> str:
        try:
            resp = self._graph.request(
                "POST", f"/sites/{self._site}/drives/{self._drive}/items/{item_id}/extractSensitivityLabels")
            labels = resp.json().get("labels", [])
            return labels[0].get("name", "General") if labels else "General"
        except IntegrationError:
            return "Unknown"

    def iter_documents(self) -> Iterable[SourceDocument]:
        for item in self._walk():
            name = item.get("name", "")
            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            label = self._label_for(item["id"])
            if label.lower() in BLOCKED_LABELS:
                logger.info("skipping labelled document", extra={"item_id": item["id"], "label": label})
                continue
            groups = self._groups_for(item["id"])
            if not groups:
                continue   # fail closed: unknown audience -> not ingested
            content = with_retry("sharepoint_download", lambda i=item["id"]: self._graph.request(
                "GET", f"/sites/{self._site}/drives/{self._drive}/items/{i}/content",
                follow_redirects=True).content)
            yield SourceDocument(source="sharepoint", source_uri=item.get("webUrl", item["id"]),
                                 title=name.rsplit(".", 1)[0], content=content, extension=ext,
                                 allowed_groups=groups, sensitivity=label)
