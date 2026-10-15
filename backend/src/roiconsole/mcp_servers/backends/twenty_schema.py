"""Introspection check that a Twenty instance exposes every field the CRM connector uses."""

# ============= Local =============
from roiconsole.mcp_servers.backends.twenty import TwentyClient

# ============= Constants =============
REQUIRED_TYPE_FIELDS = {
    "Company": ["id", "name", "domainName"],
    "Person": ["id", "name", "emails", "jobTitle", "city", "company", "createdAt"],
    "Opportunity": ["id", "name", "stage", "amount", "company", "pointOfContact"],
    "Note": ["id", "title", "bodyV2", "noteTargets", "createdAt"],
    "NoteTarget": ["id", "noteId", "targetPersonId"],
    "OpportunityFilterInput": ["stage", "companyId", "and"],
    "PersonFilterInput": ["createdAt", "emails", "id", "and"],
    "OpportunityStageEnum": ["NEW", "SCREENING", "MEETING", "PROPOSAL", "CUSTOMER"],
}
REQUIRED_QUERIES = ["companies", "people", "person", "opportunities", "notes", "noteTargets"]
REQUIRED_MUTATIONS = [
    "createCompanies",
    "createPeople",
    "createOpportunities",
    "createNote",
    "createNoteTarget",
    "updatePerson",
    "destroyNotes",
    "destroyOpportunities",
    "destroyPeople",
    "destroyCompanies",
]
ROOT_QUERY = "{ __schema { queryType { fields { name } } mutationType { fields { name } } } }"
TYPE_QUERY = """
    query T($name: String!) {
      __type(name: $name) { fields { name } inputFields { name } enumValues { name } }
    }
"""


# ============= Check =============


def check_schema(client: TwentyClient) -> list[str]:
    """
    Return every required query, mutation, type field, or enum value missing from the schema.

    Parameters
    ----------
    client : TwentyClient
        Client for the instance under test.

    Returns
    -------
    missing : list of str
        Entries such as "mutation:createNoteTarget" or "Person.city"; empty when complete.
    """
    root = client.execute(ROOT_QUERY)["__schema"]
    query_set = {f["name"] for f in root["queryType"]["fields"]}
    mutation_set = {f["name"] for f in root["mutationType"]["fields"]}
    missing_list = [f"query:{q}" for q in REQUIRED_QUERIES if q not in query_set]
    missing_list += [f"mutation:{m}" for m in REQUIRED_MUTATIONS if m not in mutation_set]
    for type_name, field_list in REQUIRED_TYPE_FIELDS.items():
        type_dict = client.execute(TYPE_QUERY, {"name": type_name}).get("__type")
        if not type_dict:
            missing_list.append(f"type:{type_name}")
            continue
        present_set = {
            entry["name"]
            for key in ("fields", "inputFields", "enumValues")
            for entry in (type_dict.get(key) or [])
        }
        missing_list += [f"{type_name}.{f}" for f in field_list if f not in present_set]
    return missing_list
