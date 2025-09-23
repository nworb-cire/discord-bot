from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Iterable, List


class DummyResult:
    def __init__(self, *, scalar: Any = None, scalars: Iterable[Any] | None = None, rows: Iterable[Any] | None = None):
        self._scalar = scalar
        self._scalars = list(scalars or [])
        self._rows = list(rows or [])

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalars))

    def all(self):
        return list(self._rows)


class DummySession:
    def __init__(self, *, execute_results: List[Any] | None = None, get_results: dict[Any, Any] | None = None, commit_hook=None):
        self.execute_results = list(execute_results or [])
        self.get_results = get_results or {}
        self.added = []
        self.executed = []
        self.commit_calls = 0
        self._commit_hook = commit_hook
        self.deleted = []

    async def execute(self, stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute call")
        result = self.execute_results.pop(0)
        self.executed.append(stmt)
        if callable(result):
            result = result()
        return result

    async def get(self, _model, key):
        value = self.get_results.get(key)
        return value() if callable(value) else value

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1
        if self._commit_hook is not None:
            await self._commit_hook(self)

    async def refresh(self, _obj):
        return None

    async def delete(self, obj):
        self.deleted.append(obj)


@asynccontextmanager
async def session_cm(session: DummySession):
    try:
        yield session
    finally:
        pass


class DummyResponse:
    def __init__(self):
        self.deferred = False
        self.defer_kwargs = None
        self.messages = []
        self.modals = []

    async def defer(self, **kwargs):
        self.deferred = True
        self.defer_kwargs = kwargs

    async def send_message(self, content=None, *, ephemeral=False, embed=None):
        self.messages.append({"content": content, "ephemeral": ephemeral, "embed": embed})

    async def send_modal(self, modal):
        self.modals.append(modal)


class DummyFollowup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, *, ephemeral=False, embed=None):
        self.messages.append({"content": content, "ephemeral": ephemeral, "embed": embed})

class DummyMessage:
    def __init__(self, channel: DummyChannel | None, message_id: int, entry: dict[str, Any]):
        self.channel = channel
        self.id = message_id
        self._entry = entry
        self.reactions: list[Any] = []
        self.deleted = False

    async def add_reaction(self, emoji):
        self._entry.setdefault("reactions", []).append(emoji)

    async def delete(self):
        self.deleted = True


class DummyChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages = []
        self._sent_messages: dict[int, DummyMessage] = {}

    async def send(self, content=None, embed=None):
        self.messages.append({"content": content, "embed": embed})
        message_id = len(self.messages)
        message = DummyMessage(self, message_id, self.messages[-1])
        self._sent_messages[message_id] = message
        return message

    async def fetch_message(self, _message_id):
        return self._sent_messages.get(_message_id, SimpleNamespace(reactions=[]))


class DummyInteraction:
    def __init__(self, *, channel_id=10, user_id=42, user_roles=None, client=None):
        self.response = DummyResponse()
        self.followup = DummyFollowup()
        self.channel = DummyChannel(channel_id)
        self.user = SimpleNamespace(id=user_id, mention=f"<@{user_id}>", roles=list(user_roles or []))
        self.client = client or SimpleNamespace(get_channel=lambda _cid: DummyChannel(_cid))


def run_async(coro):
    return asyncio.run(coro)
