"""CRM backend interface and the simulated SQLite CRM used only for CI and demo mode."""

# ============= Standard Library =============
from typing import Any, Protocol

# ============= Third-Party =============
from mcp.server.fastmcp.exceptions import ToolError

# ============= Local =============
from roiconsole.sandbox.store import SandboxStore
from roiconsole.validators.state import CrmActivity

# ============= Constants =============
# fields update_contact may change, mapped to Twenty's Person field names
UPDATABLE_FIELDS_DICT = {"job_title": "jobTitle", "city": "city"}
MAX_NOTE_LENGTH = 4000


# ============= Helpers =============


def check_update_fields(fields_dict: dict[str, Any]) -> dict[str, str]:
    """Reject fields outside the updatable set and coerce values to strings."""
    if not fields_dict:
        raise ToolError("fields must not be empty")
    unknown_list = [k for k in fields_dict if k not in UPDATABLE_FIELDS_DICT]
    if unknown_list:
        raise ToolError(f"cannot update {unknown_list}; allowed: {sorted(UPDATABLE_FIELDS_DICT)}")
    return {k: str(v) for k, v in fields_dict.items()}


def check_note(note: str) -> str:
    """Require a non-empty note within the length limit."""
    if not note or not note.strip():
        raise ToolError("note must not be empty")
    return note.strip()[:MAX_NOTE_LENGTH]


def resolve_stages(stage: str | None, open_stage_list: list[str]) -> list[str]:
    """
    Return the stages to list: one requested stage, or every open stage.

    Raises ToolError for an unknown stage so a typo is not read as an empty pipeline.
    """
    if stage is None or stage == "":
        return list(open_stage_list)
    normalized = stage.strip().upper()
    if normalized not in open_stage_list + ["CUSTOMER"]:
        raise ToolError(f"unknown stage {stage!r}; stages: {open_stage_list + ['CUSTOMER']}")
    return [normalized]


# ============= Interface =============


class CrmBackend(Protocol):
    """Operations behind crm-mcp, plus readers for validators and resets."""

    def list_new_leads(self, since: str) -> dict[str, Any]:
        """Return people created at or after an ISO timestamp as {leads: [...]}."""
        ...

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Return {contact: {...}}."""
        ...

    def update_contact(self, contact_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Update allowed fields and return {contact: {...}}."""
        ...

    def log_activity(self, contact_id: str, note: str, run_id: str) -> dict[str, Any]:
        """Attach a note to a contact and return {activity_id, contact_id}."""
        ...

    def list_opportunities(self, stage: str | None) -> dict[str, Any]:
        """Return {opportunities: [...]} for one stage or every open stage."""
        ...

    def reset(self, crm_fixtures_dict: dict[str, Any]) -> dict[str, str]:
        """Remove sandbox records, load fixtures, and return ref to record id."""
        ...

    def activities_since(self, since: float) -> list[CrmActivity]:
        """Return activities logged at or after `since`."""
        ...


# ============= Simulated Backend =============


def _contact_view(person: dict[str, Any]) -> dict[str, Any]:
    """Shape a sandbox person row like the Twenty contact view."""
    return {
        "id": person["id"],
        "name": f"{person['first_name']} {person['last_name']}",
        "email": person.get("email"),
        "company": person.get("company"),
        "job_title": person.get("fields", {}).get("job_title", person.get("job_title")),
        "city": person.get("fields", {}).get("city"),
        "created_at": person.get("created_at"),
    }


class SimulatedCrmBackend:
    """
    CRM on the SQLite sandbox, used only for CI and demo mode.

    Parameters
    ----------
    store : SandboxStore
        Shared sandbox state.
    open_stage_list : list of str
        Stages counted as open pipeline.
    """

    def __init__(self, store: SandboxStore, open_stage_list: list[str]) -> None:
        self._store = store
        self._open_stage_list = list(open_stage_list)

    def _person(self, contact_id: str) -> dict[str, Any]:
        """Return a person or raise ToolError."""
        person = self._store.person(contact_id)
        if person is None:
            raise ToolError(f"contact {contact_id} not found")
        return person

    def list_new_leads(self, since: str) -> dict[str, Any]:
        """List people created since an ISO timestamp."""
        return {"leads": [_contact_view(p) for p in self._store.people_since(since)]}

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Return one contact."""
        return {"contact": _contact_view(self._person(contact_id))}

    def update_contact(self, contact_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Merge allowed fields into the contact."""
        self._person(contact_id)
        updated = self._store.update_person(contact_id, check_update_fields(fields))
        return {"contact": _contact_view(updated or {})}

    def log_activity(self, contact_id: str, note: str, run_id: str) -> dict[str, Any]:
        """Insert an activity note on an existing contact."""
        self._person(contact_id)
        activity_id = self._store.add_activity(contact_id, check_note(note), run_id=run_id)
        return {"activity_id": activity_id, "contact_id": contact_id}

    def list_opportunities(self, stage: str | None) -> dict[str, Any]:
        """List opportunities in the requested stages."""
        stage_list = resolve_stages(stage, self._open_stage_list)
        opportunity_list = [
            {
                "id": o["id"],
                "name": o["name"],
                "stage": o["stage"],
                "amount": o["amount"],
                "currency": o["currency"],
                "company": o["company"],
                "point_of_contact": {
                    "id": o["point_of_contact_id"],
                    "name": f"{o['first_name']} {o['last_name']}" if o["first_name"] else None,
                },
            }
            for o in self._store.opportunities(stage_list)
        ]
        return {"opportunities": opportunity_list}

    def reset(self, crm_fixtures_dict: dict[str, Any]) -> dict[str, str]:
        """Insert fixture records (the sandbox store itself is cleared by the caller)."""
        refs_dict: dict[str, str] = {}
        for company in crm_fixtures_dict.get("companies", []):
            refs_dict[company["ref"]] = self._store.add_company(
                company["name"], company.get("domain")
            )
        for person in crm_fixtures_dict.get("people", []):
            person_dict = {**person, "company_id": refs_dict.get(person.get("company", ""))}
            refs_dict[person["ref"]] = self._store.add_person(person_dict)
        for opportunity in crm_fixtures_dict.get("opportunities", []):
            opportunity_dict = {
                **opportunity,
                "company_id": refs_dict.get(opportunity.get("company", "")),
                "point_of_contact_id": refs_dict.get(opportunity.get("point_of_contact", "")),
            }
            refs_dict[opportunity["ref"]] = self._store.add_opportunity(opportunity_dict)
        return refs_dict

    def activities_since(self, since: float) -> list[CrmActivity]:
        """Read sandbox activities."""
        return [CrmActivity(a["contact_id"], a["note"]) for a in self._store.activities(since)]
