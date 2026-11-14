# Fix a reported bug

You are maintaining the Python web service in `{project}`: a FastAPI app
serving a conversational agent behind guardrails, with its code under `app/`.
Work only in that directory. Do not read or change anything outside it, and do
not use the web.

## The report

{report}

## What to do

Find the cause and fix it with the smallest change that restores the
behaviour described. Do not refactor, rename or move code, and leave no new
file behind.

The directory has no test suite. To check your fix you may run Python from
the project directory with `{python}`; the app is built with
`app.main.create_app(model=...)`, and LangChain's fake chat models work
without an API key.

Reply with one line: the file and line you changed. Nothing else.
