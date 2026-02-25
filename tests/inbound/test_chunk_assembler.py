import asyncio
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.api.chunk_assembler import ChunkAssembler
from src.core.session import SessionManager
from src.core.state import ConversationStateManager, MemoryStateStore
from src.config import SessionSettings


def _build_assembler(**kwargs):
    state_manager = ConversationStateManager(store=MemoryStateStore())
    assembler = ChunkAssembler(state_manager=state_manager, **kwargs)
    return assembler, state_manager


def test_chunk_assembler_fast_path_immediate() -> None:
    assembler, state_manager = _build_assembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    decision = asyncio.run(assembler.ingest(scope_key="u1", text="请帮我查一下案件进展。", now=0.0))

    assert decision.should_process is True
    assert decision.text == "请帮我查一下案件进展。"
    assert decision.reason == "fast_path"
    assert state_manager.get_message_chunk("u1", now=0.0) is None


def test_chunk_assembler_aggregates_within_window_until_complete() -> None:
    assembler, state_manager = _build_assembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    first = asyncio.run(assembler.ingest(scope_key="u1", text="请帮我查一下", now=0.0))
    second = asyncio.run(assembler.ingest(scope_key="u1", text="李四的", now=1.0))
    buffered = state_manager.get_message_chunk("u1", now=1.0)
    buffered_segments = list(buffered.segments) if buffered is not None else []
    third = asyncio.run(assembler.ingest(scope_key="u1", text="案件。", now=2.0))

    assert first.should_process is False
    assert second.should_process is False
    assert buffered is not None
    assert buffered_segments == ["请帮我查一下", "李四的"]
    assert third.should_process is True
    assert third.text == "请帮我查一下\n李四的\n案件。"
    assert third.reason == "fast_path"
    assert state_manager.get_message_chunk("u1", now=2.0) is None


def test_chunk_assembler_flushes_on_segment_fuse_limit() -> None:
    assembler, _ = _build_assembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    for idx in range(4):
        decision = asyncio.run(assembler.ingest(scope_key="u1", text=f"片段{idx}", now=float(idx)))
        assert decision.should_process is False

    fused = asyncio.run(assembler.ingest(scope_key="u1", text="片段4", now=2.5))

    assert fused.should_process is True
    assert fused.reason == "fuse_limit"
    assert fused.text.count("\n") == 4


def test_chunk_assembler_flushes_on_char_fuse_limit() -> None:
    assembler, _ = _build_assembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    first = asyncio.run(assembler.ingest(scope_key="u1", text="a" * 280, now=0.0))
    second = asyncio.run(assembler.ingest(scope_key="u1", text="b" * 280, now=1.0))

    assert first.should_process is False
    assert second.should_process is True
    assert second.reason == "fuse_limit"
    assert len(second.text) == 500


def test_chunk_assembler_flushes_stale_buffer_before_new_context() -> None:
    assembler, _ = _build_assembler(
        enabled=True,
        window_seconds=3,
        stale_window_seconds=10,
        max_segments=5,
        max_chars=500,
    )

    first = asyncio.run(assembler.ingest(scope_key="u1", text="删除第一个", now=0.0))
    second = asyncio.run(assembler.ingest(scope_key="u1", text="帮我查张三", now=30.0))
    third = asyncio.run(assembler.ingest(scope_key="u1", text="的案件。", now=31.0))

    assert first.should_process is False
    assert second.should_process is True
    assert second.reason == "stale_window_elapsed"
    assert second.text == "删除第一个"
    assert third.should_process is True
    assert third.text == "帮我查张三\n的案件。"


def test_chunk_assembler_flushes_orphan_chunks_on_session_expire() -> None:
    assembler, _ = _build_assembler(enabled=True, window_seconds=3, stale_window_seconds=10)
    session_manager = SessionManager(SessionSettings(ttl_minutes=1))

    flushed_texts: list[str] = []

    def on_expired(session_key: str) -> None:
        decision = asyncio.run(assembler.drain(session_key))
        if decision.should_process:
            flushed_texts.append(decision.text)

    session_manager.register_expire_listener(on_expired)
    session_key = "group:oc_g1:user:ou_u1"
    session_manager.add_message(session_key, "user", "帮我查")
    now = time.time()
    first = asyncio.run(assembler.ingest(scope_key=session_key, text="帮我查", now=now))
    assert first.should_process is False

    # 手动将会话标记为过期，触发 cleanup 回调
    session_manager._sessions[session_key].last_active = session_manager._sessions[session_key].last_active.replace(year=2000)
    session_manager.cleanup_expired()

    assert flushed_texts == ["帮我查"]


def test_chunk_assembler_debounce_flush_clears_message_chunk_state() -> None:
    assembler, state_manager = _build_assembler(enabled=True, window_seconds=3, stale_window_seconds=10)
    scope_key = "feishu:group:oc_g1:user:ou_u1"

    first = asyncio.run(assembler.ingest(scope_key=scope_key, text="A", now=0.0))
    second = asyncio.run(assembler.ingest(scope_key=scope_key, text="B", now=1.0))
    third = asyncio.run(assembler.ingest(scope_key=scope_key, text="C", now=2.0))

    assert first.should_process is False
    assert second.should_process is False
    assert third.should_process is False

    # 模拟再等待 4 秒后触发窗口冲刷。
    flushed = asyncio.run(assembler.drain(scope_key))

    assert flushed.should_process is True
    assert flushed.text == "A\nB\nC"
    assert state_manager.get_message_chunk(scope_key, now=6.0) is None
