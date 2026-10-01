
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
