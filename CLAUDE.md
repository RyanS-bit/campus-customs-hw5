# Homework 5: instructions for Claude Code sessions

Before any work, read `AI_prompts.md` and `README.md`. Those files are the authoritative record.

- **Address the user as Dr. Ryan.** Work one problem at a time, led by Dr. Ryan. Don't start the next problem without Dr. Ryan's explicit approval.
- **Prompt log:**
  - Add every prompt Dr. Ryan submits to `AI_prompts.md`, word for word, under the correct problem.
  - Give each follow-up a one-sentence reason.
  - Put Claude's own words under "Claude's notes".
  - Never invent prompts or log planned ones.
- **Databases:**
  - `data/campus_customs.db` is the read-only original. Never modify it.
  - All operations use `data/campus_customs_new.db`.
  - Business rules R1–R8 are in `output/harness.md` §7a.
- **Models:**
  - Claude is used only for development.
  - The five PydanticAI agents use `gpt-6-luna` through Portkey (`PORTKEY_API_KEY` from `.env`).
  - Use no other runtime model without explicit authorization.
- **Accuracy:**
  - Never invent data, figures, test results, or evidence.
  - Keep offline checks (`mcp_server/verify_tools.py`) separate from live vibe-coder tool calls (`output/mcp_smoke.json`).
- **Scope:**
  - Stay inside `hw5/`.
  - Never commit `.env` or secrets.
  - Edit documents with targeted edits, never by truncating them with shell commands.
