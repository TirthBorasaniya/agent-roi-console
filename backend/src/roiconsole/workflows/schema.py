"""Pydantic schema for workflow definitions, step classes, and task files."""

# ============= Standard Library =============
from typing import Any, Literal

# ============= Third-Party =============
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ============= Constants =============
PATH_NAMES = ("pipeline", "planner_executor", "react")
STEP_TYPES = ("tool", "llm", "transform")
TOOL_SERVERS = ("slack", "notion", "crm")


# ============= Workflow Definition =============


class StrictModel(BaseModel):
    """Base model that rejects unknown keys so config typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class HumanBaseline(StrictModel):
    """Human time per task and where the number came from."""

    minutes_per_task: float = Field(gt=0)
    source: str
    o_timed: bool = Field(
        description="True only when the value comes from committed manual timings.",
    )


class StepDefinition(StrictModel):
    """
    One workflow step.

    Tool steps call an MCP tool named `server.tool`. LLM steps call the gateway
    with a step class. Transform steps run a named deterministic function.
    Arguments, prompts, and transform inputs are Jinja templates rendered against
    `inputs`, `steps` (prior step outputs), and `refs`.
    """

    step_id: str
    type: Literal["tool", "llm", "transform"]
    tool: str | None = None
    step_class: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str | None = None
    user_prompt: str | None = None
    output: Literal["text", "json"] = "text"
    max_tokens: int = 512
    transform: str | None = None

    @model_validator(mode="after")
    def check_type_fields(self) -> "StepDefinition":
        """Require the fields each step type needs."""
        if self.type == "tool":
            if not self.tool or "." not in self.tool:
                raise ValueError(f"tool step {self.step_id} needs tool: server.name")
            if self.tool.split(".", 1)[0] not in TOOL_SERVERS:
                raise ValueError(f"tool step {self.step_id} uses unknown server {self.tool}")
        if self.type == "llm" and (not self.step_class or not self.user_prompt):
            raise ValueError(f"llm step {self.step_id} needs step_class and user_prompt")
        if self.type == "transform" and not self.transform:
            raise ValueError(f"transform step {self.step_id} needs transform")
        return self

    @property
    def server(self) -> str:
        """Return the MCP server name of a tool step."""
        return (self.tool or "").split(".", 1)[0]

    @property
    def tool_name(self) -> str:
        """Return the tool name without the server prefix."""
        return (self.tool or "").split(".", 1)[-1]


class AgentSettings(StrictModel):
    """Settings for the planner-executor and ReAct paths."""

    planner_step_class: str = "planning"
    executor_step_class: str
    react_step_class: str = "agent_tool_use"
    max_iterations: int = 10
    instruction_template: str


class WorkflowDefinition(StrictModel):
    """A complete workflow definition from configs/workflows/<id>.yaml."""

    version: Literal[1]
    workflow_id: str
    name: str
    value_category: str
    description: str
    human_baseline: HumanBaseline
    labor_rate_usd_per_hour: float = Field(gt=0)
    review_minutes_per_task: float = Field(ge=0)
    review_source: str
    fix_minutes_per_failure: float = Field(ge=0)
    fix_source: str
    tool_cost_usd_per_task: float = 0.0
    default_path: Literal["pipeline", "planner_executor", "react"]
    steps: list[StepDefinition]
    agent: AgentSettings
    validators: list[str]

    @model_validator(mode="after")
    def check_steps(self) -> "WorkflowDefinition":
        """Require unique step ids and at least one tool and one llm step."""
        step_id_list = [s.step_id for s in self.steps]
        if len(set(step_id_list)) != len(step_id_list):
            raise ValueError(f"{self.workflow_id}: duplicate step ids")
        type_set = {s.type for s in self.steps}
        if not {"tool", "llm"} <= type_set:
            raise ValueError(f"{self.workflow_id}: needs at least one tool and one llm step")
        return self

    @property
    def tool_list(self) -> list[str]:
        """Return the distinct `server.tool` names this workflow may call, in step order."""
        seen_list: list[str] = []
        for step in self.steps:
            if step.type == "tool" and step.tool not in seen_list:
                seen_list.append(step.tool or "")
        return seen_list

    @property
    def llm_steps(self) -> list[StepDefinition]:
        """Return the LLM steps in order."""
        return [s for s in self.steps if s.type == "llm"]

    def step_classes(self) -> list[str]:
        """Return every step class any path of this workflow can request."""
        class_list = [s.step_class or "" for s in self.llm_steps]
        class_list += [
            self.agent.planner_step_class,
            self.agent.executor_step_class,
            self.agent.react_step_class,
        ]
        return sorted(set(class_list))


# ============= Step Classes =============


class StepClass(StrictModel):
    """Routing-relevant properties of a step class."""

    description: str
    cacheable: bool
    min_step_success: float = Field(default=0.9, ge=0, le=1)
    o_json: bool = False


class StepClassCatalog(StrictModel):
    """configs/step_classes.yaml."""

    version: Literal[1]
    step_classes: dict[str, StepClass]


# ============= Tasks =============


class ValidatorSpec(StrictModel):
    """A validator invocation with task-specific arguments."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class StepCheckSpec(StrictModel):
    """A step-level check used by experiment E1."""

    check: str
    args: dict[str, Any] = Field(default_factory=dict)


class TaskExpected(StrictModel):
    """Expected end state and step-level checks."""

    validators: list[ValidatorSpec]
    step_checks: dict[str, list[StepCheckSpec]] = Field(default_factory=dict)
    gold_outputs: dict[str, Any] = Field(default_factory=dict)


class TaskDefinition(StrictModel):
    """One evaluation task from eval/tasks/<task_id>.yaml."""

    version: Literal[1]
    task_id: str
    workflow_id: str
    instruction: str
    inputs: dict[str, Any]
    fixtures: dict[str, Any]
    expected: TaskExpected
