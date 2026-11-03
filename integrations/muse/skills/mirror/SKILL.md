---
name: mirror
description: Use Mirror's memory of the user. Call mirror_context at the start of any task that depends on the user's habits or preferences (what to eat, how to write, how to review code, how to plan, how they like answers), and mirror_remember when the user states a decision, preference or correction.
---

# Mirror

Mirror keeps what it has learned about the user from past conversations. It is reached through
the `mirror` MCP server (`mirror_context`, `mirror_remember`, `mirror_feedback`,
`mirror_scenario`). If those tools are not available, do nothing and carry on normally.

## When to use it

- Before answering anything that depends on the user's own habits or preferences, call
  `mirror_context` with one short sentence describing the task. Examples: choosing food or a
  plan, writing in their voice, reviewing their code, deciding how much detail to give.
- Do not call it for plain factual questions or generic coding help that does not depend on who
  the user is.
- When the user states a decision, a preference or a correction ("I always...", "I never...",
  "no, I prefer..."), call `mirror_remember` with their own words.
- When the user says a remembered point is wrong, call `mirror_feedback` with the hypothesis id
  and the verdict `wrong`. Use `dont_use` if they do not want it used again.
- When the reply names a situation (for example code review or deadline pressure) and the user
  wants a full plan, `mirror_scenario` runs it.

## How to use what comes back

- Treat each statement as a possibility with some evidence, not a fact. Weigh the evidence count.
- Use it quietly. Do not recite the list or say "my memory says". Let it shape the answer.
- Never describe the user's personality or type, and never use it to diagnose anything.
- If nothing comes back, answer normally.
