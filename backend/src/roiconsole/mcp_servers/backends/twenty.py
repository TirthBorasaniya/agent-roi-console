"""Twenty CRM backend over the workspace GraphQL API of a self-hosted Twenty instance."""

# ============= Standard Library =============
import logging
from datetime import UTC, datetime
from typing import Any

# ============= Third-Party =============
import httpx
from mcp.server.fastmcp.exceptions import ToolError

# ============= Local =============
from roiconsole.mcp_servers.backends.crm import (
    UPDATABLE_FIELDS_DICT,
    check_note,
    check_update_fields,
    resolve_stages,
)
from roiconsole.mcp_servers.common import WriteThrottle
from roiconsole.validators.state import CrmActivity

# ============= Constants =============
logger = logging.getLogger(__name__)

GRAPHQL_PATH = "/graphql"
REQUEST_TIMEOUT_S = 15.0
# Twenty allows 100 API requests per minute per key
MIN_REQUEST_INTERVAL_S = 0.65
MAX_BATCH = 60
PAGE_SIZE = 100
MICROS = 1_000_000
NOTE_TITLE = "ROI Console activity"

PERSON_FIELDS = """
    id createdAt jobTitle city
    name { firstName lastName }
    emails { primaryEmail }
    company { id name }
"""
OPPORTUNITY_FIELDS = """
    id name stage
    amount { amountMicros currencyCode }
    company { id name }
    pointOfContact { id name { firstName lastName } }
"""


# ============= Client =============


class TwentyClient:
    """
    Minimal GraphQL client for Twenty's workspace API.

    Parameters
    ----------
    api_url : str
        Base URL of the Twenty server, e.g. http://localhost:3000.
    api_key : str
        Workspace API key (Settings, API and Webhooks).
    http_client : httpx.Client or None
        Injected client for tests.
    """

    def __init__(self, api_url: str, api_key: str, http_client: httpx.Client | None = None) -> None:
        self._url = api_url.rstrip("/") + GRAPHQL_PATH
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http_client or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self._throttle = WriteThrottle(MIN_REQUEST_INTERVAL_S)

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Run one GraphQL operation and return its data.

        Raises
        ------
        ToolError
            On transport errors, non-2xx status, or GraphQL errors.
        """
        self._throttle.wait()
        try:
            response = self._http.post(
                self._url,
                json={"query": query, "variables": variables or {}},
                headers=self._headers,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"twenty request failed: {exc}") from exc
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(e.get("message", "") for e in payload["errors"])
            raise ToolError(f"twenty graphql error: {messages}")
        return payload.get("data") or {}

    def nodes(self, query: str, field: str, variables: dict[str, Any] | None = None) -> list[dict]:
        """Run a connection query and return every node across pages."""
        node_list: list[dict] = []
        cursor = None
        while True:
            data = self.execute(query, {**(variables or {}), "after": cursor})
            connection = data.get(field) or {}
            node_list += [edge["node"] for edge in connection.get("edges", [])]
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return node_list
            cursor = page_info.get("endCursor")


# ============= Mapping Helpers =============


def person_view(node: dict[str, Any]) -> dict[str, Any]:
    """Shape a Twenty Person node as the crm-mcp contact view."""
    name = node.get("name") or {}
    return {
        "id": node["id"],
        "name": f"{name.get('firstName', '')} {name.get('lastName', '')}".strip(),
        "email": (node.get("emails") or {}).get("primaryEmail"),
        "company": (node.get("company") or {}).get("name"),
        "job_title": node.get("jobTitle"),
        "city": node.get("city"),
        "created_at": node.get("createdAt"),
    }


def _full_name(name: dict[str, Any] | None) -> str | None:
    """Join a Twenty FullName into one string, or None when empty."""
    name = name or {}
    joined = f"{name.get('firstName', '')} {name.get('lastName', '')}".strip()
    return joined or None


def opportunity_view(node: dict[str, Any]) -> dict[str, Any]:
    """Shape a Twenty Opportunity node as the crm-mcp opportunity view."""
    amount = node.get("amount") or {}
    contact = node.get("pointOfContact") or {}
    micros = amount.get("amountMicros")
    return {
        "id": node["id"],
        "name": node.get("name"),
        "stage": node.get("stage"),
        "amount": float(micros) / MICROS if micros is not None else None,
        "currency": amount.get("currencyCode"),
        "company": (node.get("company") or {}).get("name"),
        "point_of_contact": {"id": contact.get("id"), "name": _full_name(contact.get("name"))},
    }


def _chunks(item_list: list[Any], size: int) -> list[list[Any]]:
    """Split a list into batches of at most `size` items."""
    return [item_list[i : i + size] for i in range(0, len(item_list), size)]


# ============= Backend =============


class TwentyCrmBackend:
    """
    crm-mcp backend on Twenty.

    Only sandbox records are visible: companies whose domain contains the
    sandbox marker, their people, and their opportunities. Resets delete only
    those records, so a shared workspace keeps its other data.

    Parameters
    ----------
    client : TwentyClient
        GraphQL client.
    open_stage_list : list of str
        Stages counted as open pipeline.
    sandbox_marker : str
        Substring of every sandbox company domain and contact email.
    """

    def __init__(
        self, client: TwentyClient, open_stage_list: list[str], sandbox_marker: str
    ) -> None:
        self._client = client
        self._open_stage_list = list(open_stage_list)
        self._marker_like = f"%{sandbox_marker}%"

    # ============= Sandbox Scope =============

    def _sandbox_company_ids(self) -> list[str]:
        """Return ids of companies whose domain carries the sandbox marker."""
        query = """
            query SandboxCompanies($like: String!, $after: String) {
              companies(filter: {domainName: {primaryLinkUrl: {like: $like}}},
                        first: 100, after: $after) {
                edges { node { id } } pageInfo { hasNextPage endCursor }
              }
            }
        """
        return [
            n["id"] for n in self._client.nodes(query, "companies", {"like": self._marker_like})
        ]

    def _sandbox_person(self, contact_id: str) -> dict[str, Any]:
        """Return a sandbox person or raise ToolError (also enforces the allowlist)."""
        query = f"""
            query Person($id: UUID!) {{
              person(filter: {{id: {{eq: $id}}}}) {{ {PERSON_FIELDS} }}
            }}
        """
        node = self._client.execute(query, {"id": contact_id}).get("person")
        if not node:
            raise ToolError(f"contact {contact_id} not found")
        email = (node.get("emails") or {}).get("primaryEmail") or ""
        if self._marker_like.strip("%") not in email:
            raise ToolError(f"contact {contact_id} is outside the sandbox allowlist")
        return node

    # ============= Tools =============

    def list_new_leads(self, since: str) -> dict[str, Any]:
        """List sandbox people created at or after an ISO timestamp."""
        query = f"""
            query Leads($since: DateTime!, $like: String!, $after: String) {{
              people(filter: {{and: [{{createdAt: {{gte: $since}}}},
                                     {{emails: {{primaryEmail: {{like: $like}}}}}}]}},
                     orderBy: [{{createdAt: AscNullsLast}}], first: 100, after: $after) {{
                edges {{ node {{ {PERSON_FIELDS} }} }} pageInfo {{ hasNextPage endCursor }}
              }}
            }}
        """
        node_list = self._client.nodes(query, "people", {"since": since, "like": self._marker_like})
        return {"leads": [person_view(n) for n in node_list]}

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Return one sandbox contact."""
        return {"contact": person_view(self._sandbox_person(contact_id))}

    def update_contact(self, contact_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Update allowed fields on a sandbox contact."""
        clean_dict = check_update_fields(fields)
        self._sandbox_person(contact_id)
        data_dict = {UPDATABLE_FIELDS_DICT[k]: v for k, v in clean_dict.items()}
        mutation = f"""
            mutation Update($id: UUID!, $data: PersonUpdateInput!) {{
              updatePerson(id: $id, data: $data) {{ {PERSON_FIELDS} }}
            }}
        """
        node = self._client.execute(mutation, {"id": contact_id, "data": data_dict})["updatePerson"]
        return {"contact": person_view(node)}

    def log_activity(self, contact_id: str, note: str, run_id: str) -> dict[str, Any]:
        """Create a note and link it to a sandbox contact through a note target."""
        clean_note = check_note(note)
        self._sandbox_person(contact_id)
        create_note = """
            mutation Note($data: NoteCreateInput!) { createNote(data: $data) { id } }
        """
        note_id = self._client.execute(
            create_note,
            {"data": {"title": f"{NOTE_TITLE} {run_id}", "bodyV2": {"markdown": clean_note}}},
        )["createNote"]["id"]
        create_target = """
            mutation Target($data: NoteTargetCreateInput!) { createNoteTarget(data: $data) { id } }
        """
        self._client.execute(
            create_target, {"data": {"noteId": note_id, "targetPersonId": contact_id}}
        )
        return {"activity_id": note_id, "contact_id": contact_id}

    def list_opportunities(self, stage: str | None) -> dict[str, Any]:
        """List sandbox opportunities in the requested stages."""
        stage_list = resolve_stages(stage, self._open_stage_list)
        company_id_list = self._sandbox_company_ids()
        if not company_id_list:
            return {"opportunities": []}
        query = f"""
            query Opps($stages: [OpportunityStageEnum!], $companies: [UUID!], $after: String) {{
              opportunities(filter: {{and: [{{stage: {{in: $stages}}}},
                                            {{companyId: {{in: $companies}}}}]}},
                            orderBy: [{{name: AscNullsLast}}], first: 100, after: $after) {{
                edges {{ node {{ {OPPORTUNITY_FIELDS} }} }} pageInfo {{ hasNextPage endCursor }}
              }}
            }}
        """
        node_list = self._client.nodes(
            query, "opportunities", {"stages": stage_list, "companies": company_id_list}
        )
        return {"opportunities": [opportunity_view(n) for n in node_list]}

    # ============= Reset and Snapshot =============

    def delete_sandbox_records(self) -> dict[str, int]:
        """
        Hard-delete every sandbox record: notes on sandbox people, opportunities, people, companies.

        Returns
        -------
        counts : dict
            Number of records deleted per object.
        """
        company_id_list = self._sandbox_company_ids()
        people_query = """
            query People($like: String!, $after: String) {
              people(filter: {emails: {primaryEmail: {like: $like}}}, first: 100, after: $after) {
                edges { node { id } } pageInfo { hasNextPage endCursor }
              }
            }
        """
        person_id_list = [
            n["id"] for n in self._client.nodes(people_query, "people", {"like": self._marker_like})
        ]
        note_id_list: list[str] = []
        if person_id_list:
            targets_query = """
                query Targets($people: [UUID!], $after: String) {
                  noteTargets(filter: {targetPersonId: {in: $people}}, first: 100, after: $after) {
                    edges { node { id noteId } } pageInfo { hasNextPage endCursor }
                  }
                }
            """
            target_list = self._client.nodes(
                targets_query, "noteTargets", {"people": person_id_list}
            )
            note_id_list = sorted({t["noteId"] for t in target_list if t.get("noteId")})
        counts_dict = {"notes": len(note_id_list), "people": len(person_id_list)}
        if note_id_list:
            self._client.execute(
                "mutation D($ids: [UUID!]) { destroyNotes(filter: {id: {in: $ids}}) { id } }",
                {"ids": note_id_list},
            )
        if company_id_list:
            opportunity_data = self._client.execute(
                "mutation D($ids: [UUID!]) "
                "{ destroyOpportunities(filter: {companyId: {in: $ids}}) { id } }",
                {"ids": company_id_list},
            )
            counts_dict["opportunities"] = len(opportunity_data.get("destroyOpportunities") or [])
        if person_id_list:
            self._client.execute(
                "mutation D($ids: [UUID!]) { destroyPeople(filter: {id: {in: $ids}}) { id } }",
                {"ids": person_id_list},
            )
        if company_id_list:
            self._client.execute(
                "mutation D($ids: [UUID!]) { destroyCompanies(filter: {id: {in: $ids}}) { id } }",
                {"ids": company_id_list},
            )
        counts_dict["companies"] = len(company_id_list)
        return counts_dict

    def _create_many(self, mutation_name: str, input_type: str, rows: list[dict]) -> list[dict]:
        """Create records in batches and return the created nodes in input order."""
        created_list: list[dict] = []
        mutation = (
            f"mutation C($data: [{input_type}!]!) {{ {mutation_name}(data: $data) {{ id }} }}"
        )
        for batch in _chunks(rows, MAX_BATCH):
            created_list += self._client.execute(mutation, {"data": batch})[mutation_name]
        if len(created_list) != len(rows):
            raise ToolError(f"{mutation_name} created {len(created_list)} of {len(rows)} records")
        return created_list

    def reset(self, crm_fixtures_dict: dict[str, Any]) -> dict[str, str]:
        """Delete sandbox records and create the task's companies, people, and opportunities."""
        self.delete_sandbox_records()
        refs_dict: dict[str, str] = {}
        company_list = crm_fixtures_dict.get("companies", [])
        if company_list:
            rows = [
                {"name": c["name"], "domainName": {"primaryLinkUrl": f"https://{c['domain']}"}}
                for c in company_list
            ]
            for company, node in zip(
                company_list,
                self._create_many("createCompanies", "CompanyCreateInput", rows),
                strict=True,
            ):
                refs_dict[company["ref"]] = node["id"]
        person_list = crm_fixtures_dict.get("people", [])
        if person_list:
            rows = [
                {
                    "name": {"firstName": p["first_name"], "lastName": p["last_name"]},
                    "emails": {"primaryEmail": p["email"]},
                    "jobTitle": p.get("job_title", ""),
                    "companyId": refs_dict.get(p.get("company", "")),
                }
                for p in person_list
            ]
            for person, node in zip(
                person_list,
                self._create_many("createPeople", "PersonCreateInput", rows),
                strict=True,
            ):
                refs_dict[person["ref"]] = node["id"]
        opportunity_list = crm_fixtures_dict.get("opportunities", [])
        if opportunity_list:
            rows = [
                {
                    "name": o["name"],
                    "stage": o["stage"],
                    "amount": {
                        "amountMicros": int(round(o["amount"] * MICROS)),
                        "currencyCode": o.get("currency", "USD"),
                    },
                    "companyId": refs_dict.get(o.get("company", "")),
                    "pointOfContactId": refs_dict.get(o.get("point_of_contact", "")),
                }
                for o in opportunity_list
            ]
            for opportunity, node in zip(
                opportunity_list,
                self._create_many("createOpportunities", "OpportunityCreateInput", rows),
                strict=True,
            ):
                refs_dict[opportunity["ref"]] = node["id"]
        return refs_dict

    def activities_since(self, since: float) -> list[CrmActivity]:
        """Read notes created since a timestamp, one activity per linked person."""
        since_iso = datetime.fromtimestamp(since, tz=UTC).isoformat()
        query = """
            query Notes($since: DateTime!, $after: String) {
              notes(filter: {createdAt: {gte: $since}}, first: 100, after: $after) {
                edges { node {
                  id bodyV2 { markdown }
                  noteTargets { edges { node { targetPersonId } } }
                } }
                pageInfo { hasNextPage endCursor }
              }
            }
        """
        activity_list = []
        for node in self._client.nodes(query, "notes", {"since": since_iso}):
            body = (node.get("bodyV2") or {}).get("markdown") or ""
            for edge in (node.get("noteTargets") or {}).get("edges", []):
                person_id = edge["node"].get("targetPersonId")
                if person_id:
                    activity_list.append(CrmActivity(person_id, body))
        return activity_list
