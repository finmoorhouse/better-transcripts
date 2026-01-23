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
- **Railway CLI**: Use `railway connect postgres` to open a psql shell to production
- **Schema changes**: `SQLModel.metadata.create_all()` only creates missing tables, not columns. For new columns in production, run `ALTER TABLE` manually via Railway CLI.

## Admin Dashboard

- Access at `/admin` (superusers only)
- Set superuser locally: `sqlite3 test.db "UPDATE user SET is_superuser = 1 WHERE email = 'you@example.com';"`
- Set superuser in production: `railway connect postgres` then `UPDATE "user" SET is_superuser = TRUE WHERE email = 'you@example.com';`

## Tooling

- The HTMX source is saved at `static/js/htmx.min.js`. You just have to reference it locally.
