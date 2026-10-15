"""Deterministic generator for the 60-task evaluation suite and its dev/held-out split."""

# ============= Standard Library =============
import hashlib
import random
import re
from pathlib import Path
from typing import Any

# ============= Third-Party =============
import yaml

# ============= Local =============
from roiconsole.evaluation import task_data as td
from roiconsole.workflows.registry import load_workflows
from roiconsole.workflows.templating import render_value

# ============= Constants =============
TASKS_PER_WORKFLOW = 15
DEV_PER_WORKFLOW = 5
SUITE_SEED = 20261014
WORKFLOW_ORDER = ("slack_digest", "notion_search", "meeting_notes", "crm_pipeline")
YEAR = 2026


# ============= Helpers =============


def _due_label(rng: random.Random) -> str:
    """Return a due date written like "Oct 21"."""
    return f"{rng.choice(td.MONTHS)} {rng.randint(1, 28)}"


def _render_instruction(workflow_id: str, inputs_dict: dict[str, Any]) -> str:
    """Render the workflow's agent instruction with logical refs left visible."""
    template = load_workflows()[workflow_id].agent.instruction_template
    return render_value(template, {"inputs": inputs_dict})


def _strip_article(phrase: str) -> str:
    """Drop a leading article so a validator term matches natural phrasing."""
    return re.sub(r"^(a|an|the)\s+", "", phrase, flags=re.IGNORECASE)


def _task_header(workflow_id: str, index: int) -> str:
    """Return a task id such as slack_digest_007."""
    return f"{workflow_id}_{index:03d}"


# ============= Slack Digest =============


def build_slack_digest_task(index: int, rng: random.Random) -> dict[str, Any]:
    """
    Build one Slack digest task from a topic with two decisions and three action items.

    Parameters
    ----------
    index : int
        Task number within the workflow, 1-based.
    rng : random.Random
        Seeded generator.

    Returns
    -------
    task : dict
        Task file content.
    """
    topic_dict = td.DIGEST_TOPICS[index - 1]
    people_list = rng.sample(td.FIRST_NAMES, 4)
    lead, peer, expert, owner3 = people_list
    (q1, wrong1, d1), (q2, wrong2, d2) = topic_dict["decisions"]
    task1, task2, task3 = topic_dict["tasks"]
    due_list = [_due_label(rng) for _ in range(3)]
    message_list = [
        {
            "user": lead,
            "text": (
                f"Kicking off a thread on {topic_dict['topic']}. "
                f"We need to settle {q1} and {q2} today."
            ),
        },
        {"user": peer, "text": f"For {q1} I'd lean toward {wrong1}, but I'm open to {d1}."},
        {"user": expert, "text": f"{d1} is the safer choice given what we saw last quarter."},
        {"user": lead, "text": f"Agreed. Decision: we go with {d1} for {q1}."},
        {
            "user": peer,
            "text": f"On {q2}: {wrong2} was floated, but the group prefers {d2}. Decision: {d2}.",
        },
        {"user": lead, "text": f"Action items: {peer} will {task1} by {due_list[0]}."},
        {"user": expert, "text": f"I'll {task2} by {due_list[1]}."},
        {"user": lead, "text": f"And {owner3} is on point to {task3} by {due_list[2]}."},
        {"user": owner3, "text": "Works for me, thanks all."},
    ]
    terms_list = [_strip_article(d1), _strip_article(d2), peer, expert, owner3] + due_list
    inputs_dict = {
        "source_channel": "roi-sandbox-eng",
        "thread_ts": "{{ refs.thread_main }}",
        "digest_channel": "roi-sandbox-digest",
        "topic": topic_dict["topic"],
    }
    return {
        "version": 1,
        "task_id": _task_header("slack_digest", index),
        "workflow_id": "slack_digest",
        "instruction": _render_instruction("slack_digest", inputs_dict),
        "inputs": inputs_dict,
        "fixtures": {
            "slack": {
                "threads": [
                    {"ref": "thread_main", "channel": "roi-sandbox-eng", "messages": message_list}
                ]
            }
        },
        "expected": {
            "validators": [
                {"name": "slack_message_posted", "args": {"channel": "roi-sandbox-digest"}},
                {
                    "name": "message_mentions_terms",
                    "args": {"channel": "roi-sandbox-digest", "terms": terms_list},
                },
            ],
            "step_checks": {
                "write_digest": [{"check": "text_mentions_terms", "args": {"terms": terms_list}}]
            },
            "gold_outputs": {},
        },
    }


# ============= Notion Search =============


def build_notion_search_task(index: int, rng: random.Random) -> dict[str, Any]:
    """
    Build one knowledge-search task over a sampled knowledge base.

    The knowledge base holds 10 pages from at least four tags; the question
    targets one tag, so the expected answer cites exactly that tag's pages and
    none of the others.
    """
    tag = td.KB_TAGS[(index - 1) % len(td.KB_TAGS)]
    question = td.KB_QUESTIONS[tag][((index - 1) // len(td.KB_TAGS)) % 3]
    tag_page_list = [p for p in td.KB_PAGES if p[1] == tag]
    expected_list = rng.sample(tag_page_list, k=min(len(tag_page_list), rng.randint(2, 3)))
    other_list = [p for p in td.KB_PAGES if p[1] != tag]
    distractor_list = rng.sample(other_list, k=10 - len(expected_list))
    kb_page_list = expected_list + distractor_list
    rng.shuffle(kb_page_list)
    tag_options_list = sorted({p[1] for p in kb_page_list})
    pages_list = [
        {
            "ref": f"page_{i:02d}",
            "title": title,
            "properties": {"Tags": [page_tag], "Summary": summary},
        }
        for i, (title, page_tag, summary) in enumerate(kb_page_list, start=1)
    ]
    asker = rng.choice(td.FIRST_NAMES)
    inputs_dict = {
        "kb_database": "knowledge_base",
        "question": question,
        "answer_channel": "roi-sandbox-questions",
        "question_ts": "{{ refs.question_thread }}",
        "tag_options": tag_options_list,
    }
    expected_titles_list = [p[0] for p in expected_list]
    distractor_titles_list = [p[0] for p in distractor_list]
    gold_pages_list = [
        {"title": p[0], "properties": {"Tags": [p[1]], "Summary": p[2]}} for p in expected_list
    ]
    return {
        "version": 1,
        "task_id": _task_header("notion_search", index),
        "workflow_id": "notion_search",
        "instruction": _render_instruction("notion_search", inputs_dict),
        "inputs": inputs_dict,
        "fixtures": {
            "slack": {
                "threads": [
                    {
                        "ref": "question_thread",
                        "channel": "roi-sandbox-questions",
                        "messages": [{"user": asker, "text": question}],
                    }
                ]
            },
            "notion": {"databases": [{"name": "knowledge_base", "pages": pages_list}]},
        },
        "expected": {
            "validators": [
                {
                    "name": "slack_message_posted",
                    "args": {
                        "channel": "roi-sandbox-questions",
                        "thread_ts": "{{ refs.question_thread }}",
                    },
                },
                {
                    "name": "message_mentions_terms",
                    "args": {
                        "channel": "roi-sandbox-questions",
                        "thread_ts": "{{ refs.question_thread }}",
                        "terms": expected_titles_list,
                    },
                },
                {
                    "name": "message_excludes_terms",
                    "args": {
                        "channel": "roi-sandbox-questions",
                        "thread_ts": "{{ refs.question_thread }}",
                        "terms": distractor_titles_list,
                    },
                },
            ],
            "step_checks": {
                "extract_filter": [
                    {"check": "json_field_equals", "args": {"field": "tag", "value": tag}}
                ],
                "compose_answer": [
                    {"check": "text_mentions_terms", "args": {"terms": expected_titles_list}},
                    {"check": "text_excludes_terms", "args": {"terms": distractor_titles_list}},
                ],
            },
            "gold_outputs": {
                "extract_filter": {"tag": tag},
                "query_kb": {"pages": gold_pages_list},
            },
        },
    }


# ============= Meeting Notes =============


def build_meeting_notes_task(index: int, rng: random.Random) -> dict[str, Any]:
    """Build one meeting-notes task with a written-out date that must be normalized."""
    title = td.MEETING_TITLES[index - 1]
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    iso_date = f"{YEAR}-{month:02d}-{day:02d}"
    attendees_list = rng.sample(td.FIRST_NAMES, rng.randint(3, 5))
    decision_list = rng.sample(td.MEETING_DECISIONS, 2)
    n_actions = rng.randint(2, 3)
    owner_list = rng.sample(attendees_list, n_actions)
    task_list = rng.sample(td.MEETING_TASKS, n_actions)
    due_list = [_due_label(rng) for _ in range(n_actions)]
    action_lines = "\n".join(
        f"- {o} to {t} by {d}" for o, t, d in zip(owner_list, task_list, due_list, strict=True)
    )
    notes = (
        f"{title}\n"
        f"Held on {td.MONTH_NAMES[month - 1]} {day}, {YEAR}\n"
        f"Attendees: {', '.join(attendees_list)}\n\n"
        f"Discussion\n"
        f"{attendees_list[0]} opened with a status update. "
        f"The group reviewed open risks and agreed:\n"
        f"- {decision_list[0]}\n"
        f"- {decision_list[1]}\n\n"
        f"Next steps\n"
        f"{action_lines}\n"
    )
    inputs_dict = {"meetings_database": "meeting_notes", "notes": notes}
    return {
        "version": 1,
        "task_id": _task_header("meeting_notes", index),
        "workflow_id": "meeting_notes",
        "instruction": _render_instruction("meeting_notes", inputs_dict),
        "inputs": inputs_dict,
        "fixtures": {"notion": {"databases": [{"name": "meeting_notes", "pages": []}]}},
        "expected": {
            "validators": [
                {
                    "name": "notion_page_created",
                    "args": {"database": "meeting_notes", "title_contains": title},
                },
                {
                    "name": "notion_property_equals",
                    "args": {
                        "database": "meeting_notes",
                        "title_contains": title,
                        "property": "Date",
                        "value": iso_date,
                    },
                },
                {
                    "name": "notion_todos_mention",
                    "args": {
                        "database": "meeting_notes",
                        "title_contains": title,
                        "terms": owner_list,
                        "min_todos": n_actions,
                    },
                },
            ],
            "step_checks": {
                "extract_meeting": [
                    {"check": "json_field_contains", "args": {"field": "title", "value": title}},
                    {"check": "json_field_equals", "args": {"field": "date", "value": iso_date}},
                    {
                        "check": "json_list_equals",
                        "args": {"field": "attendees", "value": attendees_list},
                    },
                    {
                        "check": "json_list_field_equals",
                        "args": {"field": "action_items", "key": "owner", "value": owner_list},
                    },
                ]
            },
            "gold_outputs": {},
        },
    }


# ============= CRM Pipeline =============


def build_crm_pipeline_task(index: int, rng: random.Random) -> dict[str, Any]:
    """
    Build one CRM pipeline task with a unique largest open deal.

    Odd tasks summarize every open stage; even tasks filter to one stage that
    holds at least two deals.
    """
    company_list = rng.sample(td.COMPANY_NAMES, 5)
    companies = [
        {"ref": f"co_{i}", "name": name, "domain": f"{name.split()[0].lower()}.roi-sandbox.test"}
        for i, name in enumerate(company_list, start=1)
    ]
    people = []
    for i, company in enumerate(companies, start=1):
        first, last = rng.choice(td.FIRST_NAMES), rng.choice(td.LAST_NAMES)
        people.append(
            {
                "ref": f"p_{i}",
                "first_name": first,
                "last_name": last,
                "email": f"{first.lower()}.{last.lower()}@{company['domain']}",
                "company": company["ref"],
                "job_title": rng.choice(["VP Operations", "Head of IT", "CTO", "Procurement Lead"]),
            }
        )
    stage_filter = None if index % 2 == 1 else rng.choice(td.OPEN_STAGES)
    opportunity_list = []
    for i in range(1, 8):
        company_index = (i - 1) % len(companies)
        # with a stage filter, the first two deals guarantee at least two in scope
        stage = stage_filter if stage_filter and i <= 2 else rng.choice(td.ALL_STAGES)
        opportunity_list.append(
            {
                "ref": f"opp_{i}",
                "name": f"{company_list[company_index].split()[0]} {rng.choice(td.DEAL_KINDS)} {i}",
                "stage": stage,
                "amount": rng.randrange(5_000, 150_000, 500),
                "currency": "USD",
                "company": companies[company_index]["ref"],
                "point_of_contact": people[company_index]["ref"],
            }
        )
    open_stage_set = {stage_filter} if stage_filter else set(td.OPEN_STAGES)
    in_scope_list = [o for o in opportunity_list if o["stage"] in open_stage_set]
    # make the largest in-scope deal unique so the follow-up target is unambiguous
    top = max(in_scope_list, key=lambda o: o["amount"])
    top["amount"] = max(o["amount"] for o in opportunity_list) + 2_500
    total = sum(o["amount"] for o in in_scope_list)
    inputs_dict = {"stage": stage_filter, "summary_channel": "roi-sandbox-pipeline"}
    return {
        "version": 1,
        "task_id": _task_header("crm_pipeline", index),
        "workflow_id": "crm_pipeline",
        "instruction": _render_instruction("crm_pipeline", inputs_dict),
        "inputs": inputs_dict,
        "fixtures": {
            "crm": {"companies": companies, "people": people, "opportunities": opportunity_list}
        },
        "expected": {
            "validators": [
                {"name": "slack_message_posted", "args": {"channel": "roi-sandbox-pipeline"}},
                {
                    "name": "message_mentions_terms",
                    "args": {
                        "channel": "roi-sandbox-pipeline",
                        "terms": [o["name"] for o in in_scope_list],
                    },
                },
                {
                    "name": "message_mentions_amount",
                    "args": {"channel": "roi-sandbox-pipeline", "amount": total},
                },
                {
                    "name": "crm_activity_logged",
                    "args": {
                        "contact": "{{ refs." + top["point_of_contact"] + " }}",
                        "contains": "pipeline",
                    },
                },
            ],
            "step_checks": {
                "write_summary": [
                    {
                        "check": "text_mentions_terms",
                        "args": {"terms": [o["name"] for o in in_scope_list]},
                    },
                    {"check": "text_mentions_amount", "args": {"amount": total}},
                ],
                "pick_followup": [
                    {
                        "check": "json_field_equals",
                        "args": {
                            "field": "contact_id",
                            "value": "{{ refs." + top["point_of_contact"] + " }}",
                        },
                    }
                ],
            },
            "gold_outputs": {},
        },
    }


# ============= Suite =============

BUILDERS_DICT = {
    "slack_digest": build_slack_digest_task,
    "notion_search": build_notion_search_task,
    "meeting_notes": build_meeting_notes_task,
    "crm_pipeline": build_crm_pipeline_task,
}


def build_suite(seed: int = SUITE_SEED) -> list[dict[str, Any]]:
    """
    Build all 60 tasks deterministically.

    Parameters
    ----------
    seed : int
        Base seed; each workflow derives its own generator from it.

    Returns
    -------
    tasks : list of dict
        Task file contents ordered by workflow then index.
    """
    task_list = []
    for workflow_id in WORKFLOW_ORDER:
        rng = random.Random(f"{seed}:{workflow_id}")
        builder = BUILDERS_DICT[workflow_id]
        task_list += [builder(i, rng) for i in range(1, TASKS_PER_WORKFLOW + 1)]
    return task_list


def split_task_ids(task_id_list: list[str]) -> dict[str, list[str]]:
    """
    Split task ids into dev and held-out sets by hashing the id.

    The split depends only on task ids, so it is fixed before any prompt work:
    within each workflow, the DEV_PER_WORKFLOW ids with the smallest SHA-256
    digests are dev and the rest are held out.

    Parameters
    ----------
    task_id_list : list of str
        Ids such as slack_digest_001.

    Returns
    -------
    splits : dict
        "dev" and "heldout" to sorted id lists.
    """
    splits_dict: dict[str, list[str]] = {"dev": [], "heldout": []}
    for workflow_id in WORKFLOW_ORDER:
        workflow_id_list = [t for t in task_id_list if t.startswith(f"{workflow_id}_")]
        ranked_list = sorted(
            workflow_id_list, key=lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()
        )
        splits_dict["dev"] += ranked_list[:DEV_PER_WORKFLOW]
        splits_dict["heldout"] += ranked_list[DEV_PER_WORKFLOW:]
    return {name: sorted(id_list) for name, id_list in splits_dict.items()}


def write_suite(eval_dir: Path, seed: int = SUITE_SEED) -> tuple[int, dict[str, list[str]]]:
    """
    Write eval/tasks/*.yaml and eval/splits.yaml.

    Returns
    -------
    n_tasks : int
        Number of task files written.
    splits : dict
        The split written to splits.yaml.
    """
    tasks_dir = eval_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for stale in tasks_dir.glob("*.yaml"):
        stale.unlink()
    task_list = build_suite(seed)
    for task in task_list:
        (tasks_dir / f"{task['task_id']}.yaml").write_text(
            yaml.safe_dump(task, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8"
        )
    splits_dict = split_task_ids([t["task_id"] for t in task_list])
    header = (
        "# Generated by scripts/generate_task_suite.py. The split hashes task ids only and\n"
        "# was fixed before any prompt work. Do not edit by hand.\n"
    )
    (eval_dir / "splits.yaml").write_text(
        header + yaml.safe_dump({"version": 1, "seed": seed, **splits_dict}, sort_keys=False),
        encoding="utf-8",
    )
    return len(task_list), splits_dict
