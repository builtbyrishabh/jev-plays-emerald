from jev_plays_emerald.jev import JevChoice, TokenUsage
from jev_plays_emerald.telemetry import DecisionTelemetry


def request(telemetry, attempt=1):
    telemetry.requested(context_id='game', attempt=attempt, state={}, criteria={}, instructions='choose')


def test_usage_counts_stale_and_failed_requests_without_counting_selection_twice(tmp_path):
    telemetry = DecisionTelemetry(tmp_path / 'events.jsonl')
    initial = telemetry.snapshot
    request(telemetry)
    result = JevChoice('talk', {'talk': 1}, 1, TokenUsage(100, 5, 0), 10)
    telemetry.responded(context_id='game', attempt=1, result=result, disposition='accepted')
    telemetry.selected(context_id='game', source='model', action_id='talk', result=result)
    request(telemetry, 2)
    telemetry.responded(context_id='game', attempt=2,
                        result=JevChoice('talk', {}, None, TokenUsage(30, None, 10), 2), disposition='stale')
    request(telemetry, 3)
    telemetry.request_failed(context_id='game', attempt=3, error=TimeoutError(), stale=True, retry=False)
    request(telemetry, 4)
    usage = getattr(telemetry.snapshot, 'decision_usage', None)
    assert usage is not None, 'Live status must expose cumulative decision usage'
    assert (usage.calls, usage.responses, usage.errors, usage.stale, usage.pending) == (4, 2, 1, 2, 1)
    assert (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens) == (130, 5, 10)
    assert (usage.missing_input, usage.missing_output, usage.missing_cached) == (1, 2, 1)
    assert initial.decision_usage.calls == 0
    assert telemetry.snapshot.planner_usage.calls == 0


def test_planner_usage_keeps_missing_fields_distinct_from_reported_zero(tmp_path):
    telemetry = DecisionTelemetry(tmp_path / 'events.jsonl')
    telemetry.planner_event('planner-request', call=1)
    telemetry.planner_event('planner-response', call=1, usage={'input_tokens': 0, 'output_tokens': 8}, disposition='stale')
    telemetry.planner_event('planner-request', call=2)
    telemetry.planner_event('planner-error', call=2, message='timeout', stale=False)
    telemetry.planner_event('planner-intervention', call=2, status='error')
    usage = getattr(telemetry.snapshot, 'planner_usage', None)
    assert usage is not None, 'Planner usage must be counted separately'
    assert (usage.calls, usage.responses, usage.errors, usage.stale, usage.pending) == (2, 1, 1, 1, 0)
    assert (usage.input_tokens, usage.output_tokens) == (0, 8)
    assert (usage.missing_input, usage.missing_output, usage.missing_cached) == (1, 1, 2)
    assert telemetry.snapshot.decision_usage.calls == 0
