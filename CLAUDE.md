# Claude.md for Better Transcripts

## Bash commands

- Run the development server: `uv run python main.py`
- Install dependencies: `uv sync`

## Reference

- `readme.md` contains an overall description of the app
- `docs` contains any relevant documentation for quick reference (it's currently empty)

## Database

- **Local development**: SQLite (`sqlite:///./test.db`) - no DATABASE_URL needed
- **Production**: PostgreSQL on Railway.com - set via DATABASE_URL env var

## Tooling

- The HTMX source is saved at `static/js/htmx.min.js`. You just have to reference it locally.
