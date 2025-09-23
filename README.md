# Book Club Bot

## Overview

This repository contains **Book Club Bot**, a Discord bot for managing book nominations, elections, voting, and prediction reminders within a book club community. It is implemented in Python using `discord.py`, backed by PostgreSQL and Redis, and deployed via Docker Compose.

## Features

* `/nominate <link>`: Scrape Amazon book data and summarize via OpenAI.
* `/open_voting [hours]`: Open a time-limited election with a ranked ballot.
* `/vote`: Allocate weighted votes per election.
* `/close_voting`: Close elections early and post results with discussion threads.
* `/predict <date> <odds> <text>`: Schedule and remind predictions.
* `/help`: List available commands.

## Setting up a development environment

1. **Prerequisites**

   * Docker & Docker Compose
   * Python 3.12 installed (for running tests locally)
2. **Clone repository**

   ```bash
   git clone https://github.com/your-org/book-club-bot.git
   cd book-club-bot
   ```
3. **Environment variables**
   Create a `.env` file in the project root with the following keys:

   ```dotenv
   DISCORD_BOT_TOKEN=your_token
   BOOKCLUB_CHANNEL_ID=1234567890
   BOOKCLUB_NOM_CHANNEL_ID=1234567891
   BOOKCLUB_RESULTS_CHANNEL_ID=1234567892
   BOOKCLUB_ROLE_ID=123456
   VOTE_WEIGHT_INNER=100
   VOTE_WEIGHT_OUTER=30
   OPENAI_API_KEY=your_openai_key
   DATABASE_URL=postgresql+asyncpg://user:pwd@db/bot
   REDIS_URL=redis://redis:6379/0
   PREDICTIONS_CHANNEL_ID=1234567893
   ```
4. **Start services**

   ```bash
   docker compose up --build
   ```
5. **Run tests**

   ```bash
   docker compose exec bot pytest --maxfail=1 --disable-warnings -q
   ```

## Git hooks

Install the provided pre-commit hook so commits run the unit test suite automatically.

```bash
uv tool run pre-commit install --hook-type pre-commit
```

The hook runs `uv run pytest tests` on each commit. Set `SKIP=unit-tests` when committing if you need to bypass it temporarily. The hook pins `UV_CACHE_DIR=.uv-cache` so it can write inside the repo sandbox.

## Contributing

1. Fork the repository and create a feature branch: `git checkout -b feature/my-feature`
2. Write tests for new functionality (target ≥95% coverage).
3. Follow code style: `black .` and `flake8 .`.
4. Commit changes with descriptive messages.
5. Open a pull request against `main` and request a review.
