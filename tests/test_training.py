"""Training choices expose reachable encounter ground and normal movement only."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from jev_plays_emerald.actions import Action, ActionExecutor, FrameState, Outcome
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags, PartyMember


def observation(**changes):
    return replace(Observation(
        'ctx', 'OVERWORLD', MapPosition((0, 19), (0, 0), 'Up'), True, 'none', 'none',
        (PartyMember('Torchic', 10, 25, 25, 'Healthy', ()),), (), OpeningFlags(True, True, True), None, (),
    ), **changes)


def test_training_action_contains_pair_and_requires_controllable_living_party():
    from jev_plays_emerald.training import training_actions

    obs = observation(training_spots=((1, 1), (2, 1)))
    actions = training_actions(obs)
    assert len(actions) == 1
    assert actions[0].id == 'train:0:19:1:1:2:1'
    assert training_actions(replace(obs, controllable=False)) == ()
    assert training_actions(replace(obs, party=())) == ()
    assert training_actions(replace(obs, party=(replace(obs.party[0], hp=0),))) == ()
    assert training_actions(replace(obs, menu_phase='script')) == ()
    assert training_actions(replace(obs, training_spots=((1, 1),))) == ()


def test_spots_skip_water_blocked_and_unreachable_grass(monkeypatch):
    from jev_plays_emerald.training import training_spots, _encounter_ground, _reachable_training_spots
    import modules.map
    import modules.map_path

    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()
    def tile(x, y, *, encounter=True, surf=False, collision=0):
        return SimpleNamespace(local_position=(x, y), has_encounters=encounter,
                               is_surfable=surf, collision=collision)
    tiles = [[tile(0, 0, encounter=False), tile(0, 1, surf=True)],
             [tile(1, 0, collision=1), tile(1, 1)],
             [tile(2, 0), tile(2, 1)],
             [tile(3, 0), tile(3, 1)]]
    monkeypatch.setattr(modules.map, 'get_map_data', lambda map_id, coords: SimpleNamespace(map_size=(4, 2), all_tiles=lambda: tiles))
    def path(source, destination, *, avoid_encounters, no_surfing):
        assert avoid_encounters is False
        assert no_surfing is True
        if destination[1] in {(1, 1), (2, 0)}:
            raise modules.map_path.PathFindingError('blocked')
        return [SimpleNamespace(map=(0, 19))]
    monkeypatch.setattr(modules.map_path, 'calculate_path', path)
    spots = training_spots(MapPosition((0, 19), (0, 0), 'Up'))
    assert len(spots) == 2
    assert set(spots) <= {(2, 1), (3, 0), (3, 1)}
    assert abs(spots[0][0] - spots[1][0]) + abs(spots[0][1] - spots[1][1]) == 1
    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()


def test_training_executor_walks_between_grass_and_stops_for_battle(monkeypatch):
    from jev_plays_emerald.training import training_plan
    import modules.modes.util.walking

    visited = []
    def navigate(map_id, target, *, avoid_encounters):
        assert avoid_encounters is False
        visited.append((map_id, target))
        yield
    monkeypatch.setattr(modules.modes.util.walking, 'navigate_to', navigate)

    class Boundary:
        state = FrameState('OVERWORLD')
        def read_frame_state(self): return self.state
        def read_context_id(self): return 'ctx'
        def reset_held_buttons(self): pass

    boundary = Boundary()
    executor = ActionExecutor(boundary, dispatch={'train': training_plan})
    run = executor.execute(Action('train:0:19:1:1:2:1', 'Train', 'ctx'))
    for _ in range(5):
        assert next(run) is None
    assert visited[:3] == [((0, 19), (1, 1)), ((0, 19), (2, 1)), ((0, 19), (1, 1))]
    boundary.state = FrameState('BATTLE', 'battle')
    assert next(run) == Outcome.INTERRUPTED
    assert len(visited) == 5


def test_training_without_encounters_still_yields_and_hits_executor_budget(monkeypatch):
    from jev_plays_emerald.training import training_plan
    import modules.modes.util.walking

    # Already-at-destination can complete without yielding: the outer loop must
    # still yield rather than hanging Python when navigation has no work.
    def navigate(*args, **kwargs):
        yield from ()
    monkeypatch.setattr(modules.modes.util.walking, 'navigate_to', navigate)
    boundary = SimpleNamespace(read_frame_state=lambda: FrameState('OVERWORLD'),
                               read_context_id=lambda: 'ctx', reset_held_buttons=lambda: None)
    executor = ActionExecutor(boundary, dispatch={'train': training_plan}, frame_limit=3)
    results = list(executor.execute(Action('train:0:19:1:1:2:1', 'Train', 'ctx')))
    assert results == [None, None, None, Outcome.FAILED]
    assert 'exceeded 3 frames' in executor.last_reason


@pytest.mark.parametrize('identifier', ['train:0:19:1:1:1:1', 'train:0:19:1:1:4:4', 'train:bad'])
def test_invalid_or_stationary_training_pair_is_rejected(identifier):
    from jev_plays_emerald.training import training_plan
    with pytest.raises(ValueError):
        training_plan(Action(identifier, 'Train', 'ctx'))


@pytest.mark.parametrize('coordinates', [(-1, 2), (2, -1), (6, 2), (2, 6)])
def test_transition_coordinates_outside_map_do_not_reach_pathfinder(monkeypatch, coordinates):
    from jev_plays_emerald.training import training_spots, _encounter_ground, _reachable_training_spots
    import modules.map
    import modules.map_path

    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()
    tiles = [[SimpleNamespace(local_position=point, has_encounters=True, is_surfable=False, collision=0)
              for point in ((1, 1), (2, 1))]]
    monkeypatch.setattr(modules.map, 'get_map_data', lambda *_: SimpleNamespace(map_size=(6, 6), all_tiles=lambda: tiles))
    def invalid_path(*args, **kwargs):
        pytest.fail('Out-of-map transition coordinates reached pathfinding')
    monkeypatch.setattr(modules.map_path, 'calculate_path', invalid_path)
    assert training_spots(MapPosition((0, 19), coordinates, 'Up')) == ()
    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()


def test_transient_path_index_failure_recovers_on_same_position(monkeypatch):
    from jev_plays_emerald.training import training_spots, _encounter_ground, _reachable_training_spots
    import modules.map
    import modules.map_path

    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()
    tiles = [[SimpleNamespace(local_position=point, has_encounters=True, is_surfable=False, collision=0)
              for point in ((1, 1), (2, 1))]]
    monkeypatch.setattr(modules.map, 'get_map_data', lambda *_: SimpleNamespace(map_size=(6, 6), all_tiles=lambda: tiles))
    transitioning = True
    def path(*args, **kwargs):
        if transitioning:
            raise IndexError('list index out of range')
        return [SimpleNamespace(map=(0, 19))]
    monkeypatch.setattr(modules.map_path, 'calculate_path', path)
    position = MapPosition((0, 19), (1, 2), 'Up')
    assert training_spots(position) == ()
    transitioning = False
    assert training_spots(position) == ((1, 1), (2, 1))
    _encounter_ground.cache_clear()
    _reachable_training_spots.cache_clear()
