# Project guidance

- Python 3.13, managed with uv (`uv init`, `uv add`); run everything through
  `uv run`.
- Write tests with pytest. Run the whole suite and make sure it passes before
  you finish.
- No LLM API key is available in this environment. Tests must use LangChain's
  fake chat models (`langchain_core.language_models.fake_chat_models`); never
  call a real model.
- Keep a short `README.md` saying how to run the service and the tests.
