"""
Tests for the SDK-message -> WebSocket-event translator.

These run without the Claude Agent SDK, an API key, or the CLI: synthetic
message sequences go in, exact event lists come out. The properties asserted
here are the ones that break silently in production if they regress - duplicated
text, dropped tool calls, subagent chatter leaking into the answer.
"""

from session import events
from session._sdk_types import (
    AssistantMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from session.translate import TurnState, translate
from tests.helpers import result_message


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def text_delta(text, parent=None):
    return StreamEvent(
        uuid="u",
        session_id="sess-1",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        parent_tool_use_id=parent,
    )


def thinking_delta(text, parent=None):
    return StreamEvent(
        uuid="u",
        session_id="sess-1",
        event={
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": text},
        },
        parent_tool_use_id=parent,
    )


def message_start():
    return StreamEvent(uuid="u", session_id="sess-1", event={"type": "message_start"})


def run(messages, st=None):
    """Feed a sequence through the translator, returning (events, state)."""
    st = st or TurnState()
    out = []
    for msg in messages:
        out.extend(translate(msg, st))
    return out, st


def deltas(evs):
    return [e["content"] for e in evs if e["type"] == "delta"]


# ---------------------------------------------------------------------------
# Text streaming and the double-emit problem
# ---------------------------------------------------------------------------


def test_streams_tokens_and_does_not_duplicate_on_final_message():
    evs, st = run([
        message_start(),
        text_delta("Hello"),
        text_delta(" world"),
        AssistantMessage(content=[TextBlock(text="Hello world")], model="m"),
    ])

    assert deltas(evs) == ["Hello", " world"]
    assert st.assistant_text == "Hello world"
    assert st.text_for_persistence == "Hello world"


def test_emits_whole_message_when_no_partials_arrived():
    """If the CLI stops sending partials, output degrades to one frame, not none."""
    evs, st = run([AssistantMessage(content=[TextBlock(text="Complete answer")], model="m")])

    assert deltas(evs) == ["Complete answer"]
    assert st.assistant_text == "Complete answer"


def test_emits_only_the_untransmitted_tail():
    evs, _ = run([
        message_start(),
        text_delta("Par"),
        AssistantMessage(content=[TextBlock(text="Partial then more")], model="m"),
    ])

    assert deltas(evs) == ["Par", "tial then more"]


def test_divergence_suppresses_re_emit_but_keeps_final_text():
    """Text is never duplicated, even when the streamed prefix doesn't match."""
    evs, st = run([
        message_start(),
        text_delta("streamed"),
        AssistantMessage(content=[TextBlock(text="totally different")], model="m"),
    ])

    assert deltas(evs) == ["streamed"]
    assert st.assistant_text == "totally different"


def test_empty_text_delta_emits_nothing():
    evs, _ = run([message_start(), text_delta("")])
    assert evs == []


def test_text_accumulates_across_multiple_assistant_messages():
    """A tool-using turn produces several assistant messages; all of them count."""
    _, st = run([
        message_start(),
        text_delta("Looking it up. "),
        AssistantMessage(
            content=[TextBlock(text="Looking it up. "), ToolUseBlock(id="t1", name="search_incidents", input={})],
            model="m",
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="3 found")]),
        message_start(),
        text_delta("There are 3."),
        AssistantMessage(content=[TextBlock(text="There are 3.")], model="m"),
    ])

    assert st.assistant_text == "Looking it up. There are 3."


def test_message_start_resets_only_the_inflight_buffer():
    _, st = run([
        message_start(),
        text_delta("first"),
        AssistantMessage(content=[TextBlock(text="first")], model="m"),
        message_start(),
        text_delta("second"),
    ])

    assert st.assistant_text == "first"
    assert st.stream_buf == "second"
    assert st.text_for_persistence == "firstsecond"


# ---------------------------------------------------------------------------
# Subagent output must not leak into the answer
# ---------------------------------------------------------------------------


def test_subagent_deltas_are_dropped():
    evs, st = run([
        message_start(),
        text_delta("visible"),
        text_delta("inner monologue", parent="tool-42"),
    ])

    assert deltas(evs) == ["visible"]
    assert st.stream_buf == "visible"


def test_subagent_assistant_and_user_messages_are_dropped():
    evs, st = run([
        AssistantMessage(content=[TextBlock(text="inner")], model="m", parent_tool_use_id="tool-42"),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="x", content="inner result")],
            parent_tool_use_id="tool-42",
        ),
    ])

    assert evs == []
    assert st.assistant_text == ""


# ---------------------------------------------------------------------------
# Thinking
# ---------------------------------------------------------------------------


def test_thinking_deltas_send_the_running_total():
    """The client replaces its buffer per event, so each frame must be cumulative."""
    evs, _ = run([thinking_delta("Let me "), thinking_delta("check the logs.")])

    assert [e["content"] for e in evs] == ["Let me ", "Let me check the logs."]


def test_thinking_block_emitted_only_when_nothing_streamed():
    evs, _ = run([AssistantMessage(content=[ThinkingBlock(thinking="reasoned", signature="s")], model="m")])
    assert [e["content"] for e in evs if e["type"] == "thinking"] == ["reasoned"]

    evs, _ = run([
        thinking_delta("reasoned"),
        AssistantMessage(content=[ThinkingBlock(thinking="reasoned", signature="s")], model="m"),
    ])
    assert [e["content"] for e in evs if e["type"] == "thinking"] == ["reasoned"]


def test_signature_and_input_json_deltas_are_ignored():
    evs, _ = run([
        StreamEvent(
            uuid="u",
            session_id="s",
            event={"type": "content_block_delta", "delta": {"type": "signature_delta", "signature": "x"}},
        ),
        StreamEvent(
            uuid="u",
            session_id="s",
            event={"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"a'}},
        ),
    ])
    assert evs == []


# ---------------------------------------------------------------------------
# Tools - the events that never shipped before
# ---------------------------------------------------------------------------


def test_tool_use_carries_both_flat_and_nested_shapes():
    evs, st = run([
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="get_incident_by_id", input={"id": "abc"})],
            model="m",
        )
    ])

    assert len(evs) == 1
    ev = evs[0]
    assert ev["type"] == "tool_use"
    assert (ev["id"], ev["name"], ev["input"]) == ("t1", "get_incident_by_id", {"id": "abc"})
    assert ev["content"] == {"id": "t1", "name": "get_incident_by_id", "input": {"id": "abc"}}
    assert st.tool_calls == [{"id": "t1", "name": "get_incident_by_id"}]


def test_todo_write_also_emits_a_todo_update():
    todos = [{"content": "check logs", "status": "pending"}]
    evs, _ = run([
        AssistantMessage(content=[ToolUseBlock(id="t1", name="TodoWrite", input={"todos": todos})], model="m")
    ])

    assert [e["type"] for e in evs] == ["tool_use", "todo_update"]
    assert evs[1]["todos"] == todos


def test_tool_results_arrive_on_user_messages():
    evs, _ = run([
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)])
    ])

    assert evs == [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]


def test_tool_result_error_flag_is_preserved():
    evs, _ = run([
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="boom", is_error=True)])
    ])
    assert evs[0]["is_error"] is True


def test_large_tool_results_are_truncated():
    evs, _ = run([
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="x" * 20000)])
    ])

    ev = evs[0]
    assert ev["truncated"] is True
    assert len(ev["content"]) < 20000
    assert ev["content"].startswith("x" * 100)
    assert "truncated]" in ev["content"]


def test_structured_tool_result_content_is_flattened():
    evs, _ = run([
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content=[{"type": "text", "text": "line one"}])]
        )
    ])
    assert evs[0]["content"] == "line one"


def test_plain_string_user_message_is_ignored():
    """The CLI echoes our own prompt back as a UserMessage."""
    evs, _ = run([UserMessage(content="what happened last night?")])
    assert evs == []


# ---------------------------------------------------------------------------
# Session lifecycle and results
# ---------------------------------------------------------------------------


def test_init_system_message_yields_session_id():
    evs, st = run([SystemMessage(subtype="init", data={"session_id": "claude-abc", "tools": []})])

    assert evs == [{"type": "session_init", "session_id": "claude-abc"}]
    assert st.session_id == "claude-abc"


def test_other_system_messages_are_ignored():
    evs, _ = run([SystemMessage(subtype="compact_boundary", data={})])
    assert evs == []


def test_result_message_captures_metadata_without_emitting():
    evs, st = run([
        result_message(
            duration_ms=1200,
            num_turns=3,
            session_id="claude-abc",
            total_cost_usd=0.0431,
            usage={"input_tokens": 100, "output_tokens": 50},
        )
    ])

    assert evs == []
    assert st.session_id == "claude-abc"
    assert st.cost_usd == 0.0431
    assert st.usage == {"input_tokens": 100, "output_tokens": 50}
    assert st.num_turns == 3
    assert st.error is None


def test_error_result_sets_the_turn_error():
    _, st = run([
        result_message("error_during_execution", is_error=True, result="tool crashed", session_id="s")
    ])
    assert st.error == "tool crashed"


def test_error_result_without_text_still_sets_an_error():
    _, st = run([result_message("error_max_turns", is_error=True, session_id="s")])
    assert st.error and "error_max_turns" in st.error


def test_result_text_is_used_when_nothing_streamed():
    _, st = run([result_message(result="fallback answer", session_id="s")])
    assert st.assistant_text == "fallback answer"


def test_result_text_does_not_overwrite_streamed_text():
    _, st = run([
        message_start(),
        text_delta("streamed answer"),
        AssistantMessage(content=[TextBlock(text="streamed answer")], model="m"),
        result_message(result="streamed answer", session_id="s"),
    ])
    assert st.assistant_text == "streamed answer"


def test_partial_text_survives_an_interrupted_turn():
    """No AssistantMessage arrives when a turn is cut short mid-message."""
    _, st = run([
        message_start(),
        text_delta("Checking the "),
        text_delta("logs now"),
    ])

    assert st.assistant_text == ""
    assert st.text_for_persistence == "Checking the logs now"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_unknown_message_types_are_ignored():
    class Surprise:
        pass

    evs, _ = run([Surprise()])
    assert evs == []


def test_malformed_stream_event_does_not_raise():
    evs, _ = run([
        StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta"}),
        StreamEvent(uuid="u", session_id="s", event={}),
    ])
    assert evs == []


def test_translation_failure_is_contained():
    """A message with an unusable shape loses its events, not the whole turn."""
    st = TurnState()

    # content is meant to be a list of blocks; anything else raises inside the
    # translator and must be swallowed.
    assert translate(AssistantMessage(content=object(), model="m"), st) == []

    # The turn carries on afterwards.
    assert translate(AssistantMessage(content=[TextBlock(text="fine")], model="m"), st) == [
        {"type": "delta", "content": "fine"}
    ]


# ---------------------------------------------------------------------------
# Payload safety
# ---------------------------------------------------------------------------


def test_non_serialisable_tool_input_is_coerced():
    """A raw object in a payload would kill the sender task on send_json."""
    import json
    from dataclasses import dataclass

    @dataclass
    class Weird:
        a: int

    evs, _ = run([
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="x", input={"obj": Weird(a=1), "s": {1, 2}})],
            model="m",
        )
    ])

    json.dumps(evs[0])  # must not raise
    assert evs[0]["input"]["obj"] == {"a": 1}


def test_jsonable_handles_cycles():
    a = {}
    a["self"] = a
    assert events.jsonable(a) is not None
