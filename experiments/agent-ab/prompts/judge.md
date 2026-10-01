You are reviewing two implementations of the same task, in the directories `A/`
and `B/` under the current directory. Two different engineers wrote them
independently from the same specification, reproduced in `TASK.md`. Read both
codebases in full (application code and tests) before you judge. You have
read-only tools; you cannot run the code.

Score each implementation from 1 to 10 on each criterion, where 5 is a
competent, unremarkable professional result:

- **correctness**: does it meet the specification, including the edge cases
  (redaction of PII split across stream chunks, whole-word topic matching,
  tool-call limits, tenant isolation, memory scoping)? Look for real bugs.
- **design**: are responsibilities separated sensibly, are the abstractions
  the right size, would the next feature fit without a rewrite?
- **readability**: could a new team member follow it quickly? Naming, function
  size, flow, comments that earn their place.
- **maintainability**: duplication, dead code, test quality (do the tests pin
  behaviour that matters, would they catch a regression?), configuration
  handling.

Cite concrete evidence as `A/path/file.py:LINE` or `B/path/file.py:LINE` for
every score below 5 or above 7, and for every bug you claim. Judge only the
code in front of you: ignore length for its own sake, and do not reward a
style just because it is familiar.

Finish with exactly one fenced JSON block and nothing after it:

```json
{
  "A": {"correctness": 0, "design": 0, "readability": 0, "maintainability": 0},
  "B": {"correctness": 0, "design": 0, "readability": 0, "maintainability": 0},
  "winner": "A or B or tie",
  "confidence": "low or medium or high",
  "bugs": {"A": ["file:line what"], "B": ["file:line what"]},
  "summary": "three to five sentences on the decisive differences"
}
```
