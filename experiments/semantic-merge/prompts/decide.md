# Deciding on advisory findings

You are maintaining several Python projects. A linter, LaNorme, has reported
advisory findings on them. An advisory finding is a question, not an order:
you decide what is right for each project. Work alone, from the code only. Do
not use the web. Do not read anything under `/home/user/lanorme/`.

## The batch

Read the JSON file at `{batch_path}`. It is a list of findings. Each entry has
an `id`, the `root` of a project checkout, the `file` the finding is reported
on, the `finding` text and the `suggested_fix`.

## What to decide

For each finding, read the package it names and whatever else in the project
you need, then decide what you would do as that project's maintainer:

- `merge`: follow the suggested fix and merge the modules.
- `keep`: keep the split as it is and silence the finding.

Do not edit any file. Decide only.

## Output

Write a JSON list to `{output_path}`, one object per finding, in the batch's
order:

```json
{"id": "...", "decision": "merge", "reason": "one sentence"}
```

Keep each reason to one sentence naming the deciding fact. Then reply with one
line: the number of findings decided and the count of each decision. Do not
include anything else in your reply.
