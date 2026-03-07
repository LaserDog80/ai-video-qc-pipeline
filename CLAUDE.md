# CLAUDE.md

> **Purpose:** Persistent instructions for Claude when working in this project.  
> **Rule:** Edit specific sections. Never rewrite this file entirely.

---

## Project Overview

<!-- Update this section when the project scope changes -->

[Project name and one-line description]

---

## Tech Stack

- **Language:** Python 3.x (default — but suggest alternatives if genuinely better suited to the task)
- **Web framework:** [Flask/FastAPI/None—update as needed]
- **Testing:** pytest (or equivalent for the chosen language)
- **Package manager:** pip (use requirements.txt) — adapt if using a different language/ecosystem

---

## Git Workflow

- **Always work on a branch** — never commit directly to main
- At the start of each session, create a new branch from main (e.g. `git checkout -b feature/description`)
- When work is ready, push the branch and create a PR to merge into main
- Do not push directly to main

## Development Workflow

```bash
# 1. Create a branch for this session
git checkout -b feature/short-description

# 2. Create virtual environment (first time only)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run tests
pytest

# 5. Run local server (if web-based)
python app.py  # or: flask run / uvicorn main:app

# 6. Before committing
pytest && git add -A && git commit -m "message"
```

---

## Code Standards

- Use type hints for function signatures
- Docstrings for public functions
- Keep functions under 30 lines where possible
- Prefer explicit over clever

---

## File Structure Conventions

```
project/
├── src/              # Main source code
├── tests/            # Test files (mirror src/ structure)
├── requirements.txt  # Dependencies
├── app.py            # Entry point (if web-based)
└── README.md         # User-facing documentation
```

---

## Known Issues & Workarounds

<!-- Append new issues here as they're discovered -->

- None yet

---

## README Maintenance

- **Always update README.md before the end of a session** with meaningful changes
- The README should be clear and semi-technical — written for someone who wants to use the app, not deep-dive the internals
- Required sections:
  - **What it does** — a plain-English summary of the app's purpose
  - **How it works** — a brief, high-level explanation of the approach (no implementation minutiae)
  - **Installation** — step-by-step setup instructions
  - **Usage** — how to actually run and use the app
- Keep it concise and up to date — if the app changes, the README should reflect that

---

## Do NOT Do

<!-- Append items here when Claude makes mistakes -->

- Do not rewrite CLAUDE.md or LEARNINGS.md entirely—edit sections only
- Do not delete test files without explicit approval
- Do not change the directory structure without discussing first
- Do not commit or push directly to main — always use a branch
