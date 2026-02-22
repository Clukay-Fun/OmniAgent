from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.api.chunk_assembler import ChunkAssembler
from src.core.session import SessionManager
from src.config import SessionSettings


def test_chunk_assembler_fast_path_immediate() -> None:
    assembler = ChunkAssembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    decision = assembler.ingest(scope_key="u1", text="请帮我查一下案件进展。", now=0.0)

    assert decision.should_process is True
    assert decision.text == "请帮我查一下案件进展。"
    assert decision.reason == "fast_path"


def test_chunk_assembler_aggregates_within_window_until_complete() -> None:
    assembler = ChunkAssembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    first = assembler.ingest(scope_key="u1", text="请帮我查一下", now=0.0)
    second = assembler.ingest(scope_key="u1", text="李四的", now=1.0)
    third = assembler.ingest(scope_key="u1", text="案件。", now=2.0)

    assert first.should_process is False
    assert second.should_process is False
    assert third.should_process is True
    assert third.text == "请帮我查一下\n李四的\n案件。"
    assert third.reason == "fast_path"


def test_chunk_assembler_flushes_on_segment_fuse_limit() -> None:
    assembler = ChunkAssembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    for idx in range(4):
        decision = assembler.ingest(scope_key="u1", text=f"片段{idx}", now=float(idx))
        assert decision.should_process is False

    fused = assembler.ingest(scope_key="u1", text="片段4", now=2.5)

    assert fused.should_process is True
    assert fused.reason == "fuse_limit"
    assert fused.text.count("\n") == 4


def test_chunk_assembler_flushes_on_char_fuse_limit() -> None:
    assembler = ChunkAssembler(enabled=True, window_seconds=3, max_segments=5, max_chars=500)

    first = assembler.ingest(scope_key="u1", text="a" * 280, now=0.0)
    second = assembler.ingest(scope_key="u1", text="b" * 280, now=1.0)

    assert first.should_process is False
    assert second.should_process is True
    assert second.reason == "fuse_limit"
    assert len(second.text) == 500


def test_chunk_assembler_flushes_stale_buffer_before_new_context() -> None:
    assembler = ChunkAssembler(
        enabled=True,
        window_seconds=3,
        stale_window_seconds=10,
        max_segments=5,
        max_chars=500,
    )

    first = assembler.ingest(scope_key="u1", text="删除第一个", now=0.0)
    second = assembler.ingest(scope_key="u1", text="帮我查张三", now=30.0)
    third = assembler.ingest(scope_key="u1", text="的案件。", now=31.0)

    assert first.should_process is False
    assert second.should_process is True
    assert second.reason == "stale_window_elapsed"
    assert second.text == "删除第一个"
    assert third.should_process is True
    assert third.text == "帮我查张三\n的案件。"


def test_chunk_assembler_flushes_orphan_chunks_on_session_expire() -> None:
    assembler = ChunkAssembler(enabled=True, window_seconds=3, stale_window_seconds=10)
    session_manager = SessionManager(SessionSettings(ttl_minutes=1))

    flushed_texts: list[str] = []

    def on_expired(session_key: str) -> None:
        decision = assembler.drain(session_key)
        if decision.should_process:
            flushed_texts.append(decision.text)

    session_manager.register_expire_listener(on_expired)
    session_key = "group:oc_g1:user:ou_u1"
    session_manager.add_message(session_key, "user", "帮我查")
    first = assembler.ingest(scope_key=session_key, text="帮我查", now=0.0)
    assert first.should_process is False

    # 手动将会话标记为过期，触发 cleanup 回调
    session_manager._sessions[session_key].last_active = session_manager._sessions[session_key].last_active.replace(year=2000)
    session_manager.cleanup_expired()

    assert flushed_texts == ["帮我查"]
