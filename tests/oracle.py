"""Test oracle: an LLM client that answers every step correctly from the expected state."""

# ============= Standard Library =============
import json
import re
from typing import Any

# ============= Local =============
from roiconsole.runner.llm import CallMeta, LlmResult
from roiconsole.workflows.schema import TaskDefinition

# ============= Constants =============
TS_RE = re.compile(r"thread_ts (\d+\.\d+)")
OPP_LINE_RE = re.compile(r"\| \$([\d.]+) \| point of contact .*\(contact_id ([^)]+)\)")


# ============= Helpers =============


def _args(task: TaskDefinition, name: str) -> dict[str, Any]:
    """Return a validator's args."""
    return next(v.args for v in task.expected.validators if v.name == name)


def _checks(task: TaskDefinition, step_id: str) -> dict[str, Any]:
    """Return step-check args keyed by (check, field)."""
    return {
        (c.check, c.args.get("field")): c.args for c in task.expected.step_checks.get(step_id, [])
    }


def _result(content: str | None, tool_calls: list | None = None) -> LlmResult:
    """Wrap an answer as a zero-cost result from the fake model."""
    return LlmResult(content, tool_calls or [], "fake/oracle", 0.0, 10, 10, "miss", 0, 0.0)


def _call(index: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build an OpenAI tool call."""
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ============= Oracle =============


class OracleLlm:
    """
    Produces correct step outputs, plans, and agent turns for one task.

    Parameters
    ----------
    task : TaskDefinition
        Task whose expected state defines the correct answers.
    """

    def __init__(self, task: TaskDefinition) -> None:
        self.task = task
        self.calls: list[CallMeta] = []

    # ============= Content =============

    def digest(self) -> str:
        """A digest containing every required term."""
        return "Digest: " + "; ".join(_args(self.task, "message_mentions_terms")["terms"])

    def answer(self) -> str:
        """An answer naming every expected page."""
        return "Relevant pages: " + ", ".join(_args(self.task, "message_mentions_terms")["terms"])

    def meeting(self) -> dict[str, Any]:
        """The correct meeting extraction."""
        checks = _checks(self.task, "extract_meeting")
        owners = checks[("json_list_field_equals", "action_items")]["value"]
        return {
            "title": checks[("json_field_contains", "title")]["value"],
            "date": checks[("json_field_equals", "date")]["value"],
            "attendees": checks[("json_list_equals", "attendees")]["value"],
            "decisions": ["Agreed"],
            "action_items": [{"owner": o, "task": "follow up", "due": "Oct 1"} for o in owners],
        }

    def summary(self) -> str:
        """A pipeline summary with every in-scope name and the total."""
        names = _args(self.task, "message_mentions_terms")["terms"]
        total = _args(self.task, "message_mentions_amount")["amount"]
        return "Pipeline: " + ", ".join(names) + f". Total ${total:,.0f}."

    @staticmethod
    def top_contact_from_text(text: str) -> str:
        """Find the contact of the largest amount in a prompt or tool result."""
        matches = OPP_LINE_RE.findall(text)
        if matches:
            return max(matches, key=lambda m: float(m[0]))[1]
        opportunities = json.loads(text)["opportunities"]
        return max(opportunities, key=lambda o: o["amount"])["point_of_contact"]["id"]

    # ============= Paths =============

    def plan(self, instruction: str) -> dict[str, Any]:
        """A valid plan for the task's workflow."""
        wf = self.task.workflow_id
        if wf == "slack_digest":
            ts = TS_RE.search(instruction).group(1)
            return {
                "steps": [
                    {
                        "id": "s1",
                        "action": "tool",
                        "tool": "slack.read_thread",
                        "args": {"channel": "roi-sandbox-eng", "thread_ts": ts},
                    },
                    {
                        "id": "s2",
                        "action": "generate",
                        "instruction": "[[digest]]",
                        "inputs": ["s1"],
                        "output": "text",
                    },
                    {
                        "id": "s3",
                        "action": "tool",
                        "tool": "slack.post_message",
                        "args": {"channel": "roi-sandbox-digest", "text": "{{s2}}"},
                    },
                ]
            }
        if wf == "notion_search":
            ts = TS_RE.search(instruction).group(1)
            tag = self.task.expected.gold_outputs["extract_filter"]["tag"]
            return {
                "steps": [
                    {
                        "id": "s1",
                        "action": "tool",
                        "tool": "notion.query_database",
                        "args": {
                            "database_id": "knowledge_base",
                            "filter": {"property": "Tags", "multi_select": {"contains": tag}},
                        },
                    },
                    {
                        "id": "s2",
                        "action": "generate",
                        "instruction": "[[answer]]",
                        "inputs": ["s1"],
                        "output": "text",
                    },
                    {
                        "id": "s3",
                        "action": "tool",
                        "tool": "slack.post_message",
                        "args": {
                            "channel": "roi-sandbox-questions",
                            "text": "{{s2}}",
                            "thread_ts": ts,
                        },
                    },
                ]
            }
        if wf == "meeting_notes":
            meeting = self.meeting()
            blocks = [
                {"type": "to_do", "text": f"{a['owner']}: {a['task']} (due {a['due']})"}
                for a in meeting["action_items"]
            ]
            return {
                "steps": [
                    {
                        "id": "s1",
                        "action": "tool",
                        "tool": "notion.create_page",
                        "args": {
                            "parent_id": "meeting_notes",
                            "title": meeting["title"],
                            "properties": {
                                "Date": meeting["date"],
                                "Attendees": meeting["attendees"],
                            },
                        },
                    },
                    {
                        "id": "s2",
                        "action": "tool",
                        "tool": "notion.append_blocks",
                        "args": {"page_id": "{{s1.page_id}}", "blocks": blocks},
                    },
                ]
            }
        return {
            "steps": [
                {
                    "id": "s1",
                    "action": "tool",
                    "tool": "crm.list_opportunities",
                    "args": {"stage": self.task.inputs["stage"]},
                },
                {
                    "id": "s2",
                    "action": "generate",
                    "instruction": "[[summary]]",
                    "inputs": ["s1"],
                    "output": "text",
                },
                {
                    "id": "s3",
                    "action": "generate",
                    "instruction": "[[pick]]",
                    "inputs": ["s1"],
                    "output": "json",
                },
                {
                    "id": "s4",
                    "action": "tool",
                    "tool": "slack.post_message",
                    "args": {"channel": "roi-sandbox-pipeline", "text": "{{s2}}"},
                },
                {
                    "id": "s5",
                    "action": "tool",
                    "tool": "crm.log_activity",
                    "args": {"contact_id": "{{s3.contact_id}}", "note": "pipeline review"},
                },
            ]
        }

    def react_turn(self, messages: list[dict[str, Any]]) -> LlmResult:
        """Return the next agent turn for the task's workflow."""
        instruction = messages[1]["content"]
        tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        turn = len(tool_results)
        wf = self.task.workflow_id
        if wf == "slack_digest":
            ts = TS_RE.search(instruction).group(1)
            script = [
                ("slack__read_thread", {"channel": "roi-sandbox-eng", "thread_ts": ts}),
                ("slack__post_message", {"channel": "roi-sandbox-digest", "text": self.digest()}),
            ]
        elif wf == "notion_search":
            ts = TS_RE.search(instruction).group(1)
            tag = self.task.expected.gold_outputs["extract_filter"]["tag"]
            script = [
                (
                    "notion__query_database",
                    {
                        "database_id": "knowledge_base",
                        "filter": {"property": "Tags", "multi_select": {"contains": tag}},
                    },
                ),
                (
                    "slack__post_message",
                    {"channel": "roi-sandbox-questions", "text": self.answer(), "thread_ts": ts},
                ),
            ]
        elif wf == "meeting_notes":
            meeting = self.meeting()
            page_id = json.loads(tool_results[0])["page_id"] if tool_results else ""
            blocks = [
                {"type": "to_do", "text": f"{a['owner']}: {a['task']}"}
                for a in meeting["action_items"]
            ]
            script = [
                (
                    "notion__create_page",
                    {
                        "parent_id": "meeting_notes",
                        "title": meeting["title"],
                        "properties": {"Date": meeting["date"]},
                    },
                ),
                ("notion__append_blocks", {"page_id": page_id, "blocks": blocks}),
            ]
        else:
            contact = self.top_contact_from_text(tool_results[0]) if tool_results else ""
            script = [
                ("crm__list_opportunities", {"stage": self.task.inputs["stage"]}),
                (
                    "slack__post_message",
                    {"channel": "roi-sandbox-pipeline", "text": self.summary()},
                ),
                ("crm__log_activity", {"contact_id": contact, "note": "pipeline review"}),
            ]
        if turn >= len(script):
            return _result("Done.")
        name, args = script[turn]
        return _result(None, [_call(turn, name, args)])

    # ============= Client =============

    async def chat(
        self, model, messages, meta: CallMeta, max_tokens=512, tools=None, response_format=None
    ) -> LlmResult:
        """Answer one call."""
        self.calls.append(meta)
        last = messages[-1]["content"] if messages[-1]["role"] == "user" else ""
        if meta.step_id.startswith("react_turn"):
            return self.react_turn(messages)
        if meta.step_id == "plan":
            return _result(json.dumps(self.plan(last)))
        step_id = meta.step_id
        if "[[digest]]" in last or step_id == "write_digest":
            return _result(self.digest())
        if "[[answer]]" in last or step_id == "compose_answer":
            return _result(self.answer())
        if step_id == "extract_filter":
            return _result(json.dumps(self.task.expected.gold_outputs["extract_filter"]))
        if step_id == "extract_meeting":
            return _result(json.dumps(self.meeting()))
        if "[[summary]]" in last or step_id == "write_summary":
            return _result(self.summary())
        if "[[pick]]" in last:
            output_text = last.split("Output of s1:\n", 1)[1].split("\n\nRespond", 1)[0]
            return _result(json.dumps({"contact_id": self.top_contact_from_text(output_text)}))
        if step_id == "pick_followup":
            return _result(json.dumps({"contact_id": self.top_contact_from_text(last)}))
        raise AssertionError(f"oracle has no answer for {step_id}")
