# Better Transcripts

This is a repo for a web app which produces high-quality formatted transcripts. In particular, the key use case is to take audio from a podcast, and return a formatted and lightly edited transcript, in a markdown file.

The author is me, [Fin](https://github.com/finmoorhouse?tab=repositories) (and Claude Code — thanks Claude!)

## Functionality

Currently, Better Transcripts works as follows:

- The user can upload a `.wav` or `.mp3` file containing audio to be transcribed
- The file is sent to the AssemblyAI transcription API
- The result is returned to the user

## Stack

Better Transcripts (currently) uses the following tools:

- [FastAPI](https://fastapi.tiangolo.com/) for the backend
- [HTMX](https://htmx.org/) as the main framework
- [Tailwind](https://tailwindcss.com/) for styling
- [SQLModel](https://sqlmodel.tiangolo.com) for DB management
- [AssemblyAI](https://www.assemblyai.com/) as the transcription API

## Getting Started

To run the development server:

```bash
uv run python main.py
```

The app will be available at http://localhost:8000

## Development environment

- [Claude Code](https://www.anthropic.com/claude-code) is helping write the app
- Important documentation for tooling is saved in `/docs`
- [`uv`](https://github.com/astral-sh/uv) for Python package and project management
- [`fnm`](https://github.com/Schniz/fnm) for Node version management

## Roadmap

Some features I'd like to add:

- Ability to add a custom prompt with vocabulary and instructions
- Sending the completed transcript as an email attachment (likely using [Resend](https://resend.com/home))
- Adding auth, to handle multiple users
- Adding payments, both to monetise, but also so I can share the app publicly without paying the API credits for everyone's usage!