# Human baselines

The ROI computation (SPEC section 6) needs three human-time inputs per workflow:
`minutes_per_task` (time for a person to do the task by hand), `review_minutes_per_task`
(time to check the agent's output), and `fix_minutes_per_failure` (time to repair a failed task).

## Assumptions, not timings

No workflow baseline is timed. Every value in `configs/workflows/*.yaml` is an assumption and is
labeled `o_timed: false`. The original `minutes_per_task` values (15, 10, 20, 12) come from the
seed data of the first version of this console. Review and fix times are assumptions: review is 2
or 3 minutes, and fix is the baseline plus 3 minutes of cleanup. Because these are assumptions, the
sensitivity analysis in `results/e3_roi.json` carries the weight of any ROI claim: it recomputes ROI
at 25, 50, 100, and 150 percent of the baseline minutes and at labor rates of 25, 55, and 100 USD per
hour, and reports the break-even minutes per task.

| Workflow | minutes_per_task | review_minutes | fix_minutes | Source |
|---|---|---|---|---|
| slack_digest | 15 | 2 | 18 | Assumption |
| notion_search | 10 | 2 | 13 | Assumption |
| meeting_notes | 20 | 3 | 23 | Assumption |
| crm_pipeline | 12 | 2 | 15 | Assumption |

## Timing protocol

To replace an assumption with a measurement:

1. Pick 10 tasks of the workflow from the held-out split (`eval/splits.yaml`), in id order.
2. Reset the sandbox for each task (`make reset-sandbox TASK=<task_id>`) and open the live Slack,
   Notion, or Twenty workspace.
3. Start a stopwatch when the task instruction is read; stop it when the end state the validators
   check exists (message posted, page created, activity logged).
4. Record each timing in seconds in `eval/baselines/<workflow_id>.csv` with columns
   `task_id,seconds,notes`.
5. For review time, time reading one correct agent output against its inputs; for fix time, time
   repairing one deliberately broken output.
6. Set `minutes_per_task` to the median of the 10 timings divided by 60, set `o_timed: true`, set
   `source` to the CSV path, and commit the CSV and YAML change together.
