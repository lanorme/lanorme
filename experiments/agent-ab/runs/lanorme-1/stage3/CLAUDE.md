# Project guidance

- Python 3.13, managed with uv (`uv init`, `uv add`); run everything through
  `uv run`.
- Write tests with pytest. Run the whole suite and make sure it passes before
  you finish.
- No LLM API key is available in this environment. Tests must use LangChain's
  fake chat models (`langchain_core.language_models.fake_chat_models`); never
  call a real model.
- Keep a short `README.md` saying how to run the service and the tests.

## Code standard: LaNorme

This project holds its code to LaNorme, a Python linter for code quality,
style, architecture and structure. Run it as `uvx lanorme@0.21.0`.

- Before configuring it, explore the tool: `uvx lanorme@0.21.0 rules` lists
  every rule, `uvx lanorme@0.21.0 rule CODE` prints one rule's reference, and
  the documentation index is https://lanorme.github.io/lanorme/llms.txt (every
  page is also served as raw Markdown).
- Decide the configuration that best fits this project, including which of the
  default-off opinionated rules to enable, and write it to `lanorme.toml` at
  the project root. Note your reasoning briefly in comments in that file.
- Run `uvx lanorme@0.21.0 check .` and fix what it reports before you finish.
  Exit code 1 means an error-tier finding to fix.
- You may revise the configuration as the project grows. Fix the code rather
  than loosening the standard to silence a finding.
