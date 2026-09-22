"""Choose and walk a small patch of encounter ground using ordinary inputs."""

from collections.abc import Generator
from functools import lru_cache

from .actions import Action, ExecutionPlan
from .state import MapPosition, Observation


@lru_cache(maxsize=32)
def _encounter_ground(map_id: tuple[int, int]) -> frozenset[tuple[int, int]]:
    from modules.map import get_map_data

    return frozenset(
        tile.local_position
        for column in get_map_data(map_id, (0, 0)).all_tiles()
        for tile in column
        if tile.has_encounters and not tile.is_surfable and tile.collision == 0
    )


def training_spots(position: MapPosition | None) -> tuple[tuple[int, int], ...]:
    """Owner-thread lookup; transition frames never become cached empty results.

    Map identity and player coordinates can briefly describe different maps at
    a connection. Check ROM dimensions before asking upstream to index tiles.
    """
    if position is None:
        return ()
    from modules.map import get_map_data

    try:
        width, height = get_map_data(position.map_id, (0, 0)).map_size
        x, y = position.coordinates
        if not (0 <= x < width and 0 <= y < height):
            return ()
        return _reachable_training_spots(position)
    except (IndexError, RuntimeError, ValueError):
        # An upstream map/path snapshot may still be transitioning. Exceptions
        # escape the cached function, so the next frame can retry this position.
        return ()


@lru_cache(maxsize=128)
def _reachable_training_spots(position: MapPosition) -> tuple[tuple[int, int], ...]:
    """Cache stable terrain routes; execution rechecks moving NPC obstructions."""
    from modules.map_path import calculate_path, PathFindingError

    ground = _encounter_ground(position.map_id)
    px, py = position.coordinates
    nearby = sorted(ground, key=lambda point: (abs(point[0] - px) + abs(point[1] - py), point))[:32]
    for first in nearby:
        x, y = first
        for second in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
            if second not in ground:
                continue
            try:
                paths = (
                    calculate_path((position.map_id, position.coordinates), (position.map_id, first),
                                   avoid_encounters=False, no_surfing=True),
                    calculate_path((position.map_id, first), (position.map_id, second),
                                   avoid_encounters=False, no_surfing=True),
                    calculate_path((position.map_id, second), (position.map_id, first),
                                   avoid_encounters=False, no_surfing=True),
                )
            except PathFindingError:
                continue
            if all(step.map == position.map_id for path in paths for step in path):
                return first, second
    return ()


def training_actions(observation: Observation) -> tuple[Action, ...]:
    """Expose one deliberate training choice, keeping route decisions with Jev."""
    if (
        observation.game_state != 'OVERWORLD' or not observation.controllable
        or observation.menu_phase != 'none' or observation.position is None
        or len(observation.training_spots) != 2
        or not any(member.hp > 0 and not member.is_egg for member in observation.party)
    ):
        return ()
    first, second = observation.training_spots
    group, number = observation.position.map_id
    identifier = f'train:{group}:{number}:{first[0]}:{first[1]}:{second[0]}:{second[1]}'
    return (Action(identifier, f'Seek a wild encounter for training or catching in grass near {first}', observation.context_id),)


def training_plan(action: Action) -> ExecutionPlan:
    """Alternate adjacent ground tiles until a battle interrupts normal walking."""
    try:
        kind, group, number, x1, y1, x2, y2 = action.id.split(':')
        map_id = int(group), int(number)
        first, second = (int(x1), int(y1)), (int(x2), int(y2))
        if kind != 'train' or min(*map_id, *first, *second) < 0:
            raise ValueError
        if abs(first[0] - second[0]) + abs(first[1] - second[1]) != 1:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError(f'invalid training action: {action.id}') from error

    def seek() -> Generator[None, None, None]:
        from modules.modes.util.walking import navigate_to

        while True:
            for destination in (first, second):
                yielded = False
                for _ in navigate_to(map_id, destination, avoid_encounters=False):
                    yielded = True
                    yield
                # Already standing at a target is a zero-frame navigation. Keep
                # the frame budget effective even if both targets become stale.
                if not yielded:
                    yield

    return ExecutionPlan(seek, frozenset({'OVERWORLD'}))
