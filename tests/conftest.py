# tests/conftest.py
import os
import sys
import types
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

if SRC.as_posix() not in sys.path:
    sys.path.insert(0, SRC.as_posix())

# Ensure required environment variables are present for Settings()
warnings.filterwarnings(
    "ignore",
    message=r".*@wait_container_is_ready decorator is deprecated.*",
    category=DeprecationWarning,
    module=r"testcontainers\..*",
)

_DEFAULT_ENV = {
    "DISCORD_BOT_TOKEN": "test-token",
    "BOOKCLUB_CHANNEL_ID": "1",
    "BOOKCLUB_NOM_CHANNEL_ID": "2",
    "BOOKCLUB_RESULTS_CHANNEL_ID": "3",
    "PREDICTIONS_CHANNEL_ID": "4",
    "BOOKCLUB_ROLE_ID": "5",
    "VOTE_WEIGHT_INNER": "10",
    "VOTE_WEIGHT_OUTER": "5",
    "BALLOT_SIZE": "3",
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "REDIS_URL": "redis://localhost:6379/0",
    "OPENAI_API_KEY": "test-openai",
}
for key, value in _DEFAULT_ENV.items():
    os.environ.setdefault(key, value)

# Provide a lightweight discord.py shim so modules import without the real dependency
if "discord" not in sys.modules:
    discord_module = types.ModuleType("discord")

    class _Embed:
        def __init__(self, title=None, description=None):
            self.title = title
            self.description = description
            self.fields = []

        def add_field(self, *, name, value, inline=False):
            self.fields.append({"name": name, "value": value, "inline": inline})

    class _Client:
        def __init__(self):
            self._channels = {}

        def add_channel(self, channel_id, channel):
            self._channels[channel_id] = channel

        def get_channel(self, channel_id):
            return self._channels.get(channel_id)

        async def fetch_channel(self, channel_id):
            return self._channels.get(channel_id)

    class _Intents:
        @classmethod
        def default(cls):
            return cls()

    def _decorator(*args, **kwargs):
        def wrapper(func):
            return func

        return wrapper

    discord_module.Embed = _Embed
    discord_module.Client = _Client
    discord_module.Intents = _Intents
    class _Interaction:
        pass

    discord_module.Interaction = _Interaction

    class _RawReactionActionEvent:
        pass

    discord_module.RawReactionActionEvent = _RawReactionActionEvent
    class _Permissions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    discord_module.Permissions = _Permissions

    app_commands_module = types.ModuleType("discord.app_commands")
    app_commands_module.command = _decorator
    app_commands_module.default_permissions = _decorator
    discord_module.app_commands = app_commands_module

    ext_module = types.ModuleType("discord.ext")
    commands_module = types.ModuleType("discord.ext.commands")

    class _Bot:
        def __init__(self, *args, **kwargs):
            self._channels = {}
            self.added_cogs = []
            self.user = types.SimpleNamespace()
            self.tree = types.SimpleNamespace(sync=self._sync)

        async def _sync(self):
            return []

        async def add_cog(self, cog):
            self.added_cogs.append(cog)

        def event(self, func):
            setattr(self, func.__name__, func)
            return func

        def add_channel(self, channel_id, channel):
            self._channels[channel_id] = channel

        def get_channel(self, channel_id):
            return self._channels.get(channel_id)

        async def fetch_channel(self, channel_id):
            return self._channels.get(channel_id)

    class _Cog:
        @staticmethod
        def listener(*_args, **_kwargs):
            def decorator(func):
                return func

            return decorator

    commands_module.Cog = _Cog
    commands_module.Bot = _Bot
    commands_module.command = _decorator
    commands_module.group = _decorator
    ext_module.commands = commands_module

    tasks_module = types.ModuleType("discord.ext.tasks")

    class _Loop:
        def __init__(self, func):
            self.func = func
            self.started = False

        def start(self):
            self.started = True

        async def __call__(self, *args, **kwargs):
            return await self.func(*args, **kwargs)

    def _loop(*_args, **_kwargs):
        def decorator(func):
            return _Loop(func)

        return decorator

    tasks_module.loop = _loop
    ext_module.tasks = tasks_module

    ui_module = types.ModuleType("discord.ui")

    class _Modal:
        def __init_subclass__(cls, **kwargs):
            cls.default_title = kwargs.get("title")

        def __init__(self, *args, **kwargs):
            self.children = []

        def add_item(self, item):
            self.children.append(item)

    class _TextInput:
        def __init__(self, *args, **kwargs):
            self.label = kwargs.get("label")
            self.value = kwargs.get("default", "")
            self.placeholder = kwargs.get("placeholder")
            self.required = kwargs.get("required")

    ui_module.Modal = _Modal
    ui_module.TextInput = _TextInput

    discord_module.ui = ui_module

    sys.modules["discord"] = discord_module
    sys.modules["discord.app_commands"] = app_commands_module
    sys.modules["discord.ext"] = ext_module
    sys.modules["discord.ext.commands"] = commands_module
    sys.modules["discord.ext.tasks"] = tasks_module
    sys.modules["discord.ui"] = ui_module

# Avoid importing database drivers during tests by replacing the async engine factory
try:
    import sqlalchemy.ext.asyncio as sa_asyncio
except ModuleNotFoundError:
    sa_asyncio = None

if sa_asyncio is not None:
    class _DummyEngine:
        pass

    async def _unusable_session(*args, **kwargs):
        raise RuntimeError("async_session should be patched within tests")

    def _dummy_sessionmaker(*args, **kwargs):
        return _unusable_session

    sa_asyncio.create_async_engine = lambda *args, **kwargs: _DummyEngine()
    sa_asyncio.async_sessionmaker = _dummy_sessionmaker


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests that require external services.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring explicit opt-in")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-e2e"):
        return
    skip_marker = pytest.mark.skip(reason="requires --run-e2e to execute")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_marker)
