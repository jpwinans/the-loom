# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

These are **GitHub labels** on issues in `jpwinans/the-loom` (see `issue-tracker.md`),
applied with `gh issue edit <n> --add-label "..."`. The canonical names are claimed as-is
with no collisions — the same five strings were used in the prior Jira tracker, so the
vocabulary carries over unchanged.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Notes for GitHub

- Unlike Jira, GitHub does **not** create labels implicitly — `gh issue edit --add-label`
  fails if the label doesn't exist on the repo. Create a missing one first:
  `gh label create needs-triage --description "Maintainer needs to evaluate this issue"`.
- The repo already carries labels from its pre-Jira era (`critical`, `high`, `medium`,
  `low`, `epic`, `story`, `agentic-ecosystem`, and `wontfix`). The existing `wontfix`
  coincides with the canonical role and is reused as-is; the others are orthogonal to
  triage state and stay untouched.
- Triage labels are **orthogonal to workflow status**: open/closed and assignees track
  execution; these labels track triage state. Don't collapse one into the other.
