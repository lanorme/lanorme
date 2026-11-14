# Collapse packages into modules, moving code verbatim

You are doing a purely mechanical refactoring of the Python project in
`{project}`. Do not read or change anything outside that directory, and do not
use the web.

Collapse each of these packages into a single module of the same name beside
it, then delete the package directory:

{packages}

For a package `app/x/` with modules `a.py`, `b.py`: create `app/x.py` holding
the code of `a.py` and `b.py`.

Rules, all strict:

- Move every top-level function, class and constant verbatim: same names,
  same bodies, same docstrings and comments. Do not rename, rewrite, reorder
  statements inside a definition, add or remove behaviour, or "improve"
  anything.
- Order the moved definitions so that each is defined before it is used at
  import time.
- Merge the import statements at the top; drop imports that pointed at the
  modules now merged together.
- Update every import of the old modules everywhere in the project
  (`from app.x.a import Name` becomes `from app.x import Name`).
- Give the new module a one-line docstring. Keep nothing else of the old
  module docstrings.
- Leave every other file's contents unchanged apart from those import lines.

When you are done, check that the project still imports:
`cd {project} && uv run python -c "import app.main"`.

Reply with one line: the modules you created and their line counts. Nothing
else.
