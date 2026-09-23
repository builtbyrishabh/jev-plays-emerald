"""Pure legal choices for the menus and supplies needed through Rustboro."""
from __future__ import annotations

from .actions import Action
from .state import Observation


# Map identities from the pinned Emerald MapRSE table. Keep center trips local:
# crossing Petalburg Woods just to heal is a separate journey decision.
CENTER_MAPS = {
    'oldale': frozenset({(0, 10), (0, 18), (2, 2)}),
    'petalburg': frozenset({(0, 0), (8, 4)}),
    'rustboro': frozenset({(0, 3), (11, 5)}),
}
CENTER_INTERIORS = {'oldale': (2, 2), 'petalburg': (8, 4), 'rustboro': (11, 5)}
FIELD_HEALING_ITEMS = frozenset({'Potion', 'Super Potion', 'Oran Berry'})


def healing_actions(observation: Observation) -> tuple[Action, ...]:
    from .opening import party_needs_healing

    if not observation.position or not observation.controllable or not party_needs_healing(observation.party):
        return ()
    return tuple(Action(f'heal:{name}', f'Restore party HP, status and PP at {name.title()} Pokémon Center', observation.context_id)
                 for name, maps in CENTER_MAPS.items() if observation.position.map_id in maps)


def item_actions(observation: Observation) -> tuple[Action, ...]:
    if not observation.controllable or observation.menu_phase != 'none':
        return ()
    return tuple(Action(f'field-item:{item.name}:{index}', f'Use {item.name} on {member.species} (slot {index + 1}, {member.hp}/{member.max_hp} HP)', observation.context_id)
                 for item in observation.inventory if item.quantity > 0 and item.name in FIELD_HEALING_ITEMS
                 for index, member in enumerate(observation.party) if 0 < member.hp < member.max_hp)


def extra_actions(observation: Observation) -> tuple[Action, ...]:
    """Return exclusive menu decisions; overworld item options are additive."""
    def action(identifier: str, label: str) -> Action:
        return Action(identifier, label, observation.context_id)

    if observation.game_state == "NAMING_SCREEN" and observation.party:
        return (action("nickname:keep", "Keep the Pokémon’s default species name"),)
    if "Task_HandleCaughtMonPageInput" in observation.tasks:
        return (action("caught-dex:continue", "Close the caught Pokémon’s Pokédex entry"),)
    if observation.tutorial_battle:
        return (action("tutorial:advance", "Watch Wally’s catching tutorial"),)
    match observation.menu_phase:
        case 'yes_no':
            if any(script.startswith('LittlerootTown_ProfessorBirchsLab_EventScript_GiveStarter') or 'Nickname' in script for script in observation.scripts):
                return (action('answer:no', 'Keep the Pokémon’s species name'),)
            return (action('answer:yes', 'Answer Yes to the current question'),
                    action('answer:no', 'Answer No to the current question'))
        case 'shop':
            return tuple(action(f'shop-buy:{item.name}:1', f'Buy 1 {item.name} for ₽{item.price} (money ₽{observation.money})')
                         for item in observation.shop_items if 0 < item.price <= observation.money) + (action('shop-exit', 'Leave the shop menu'),)
        case 'forced_switch':
            return tuple(action(f'forced-switch:{index}', f'Send out {member.species} (slot {index + 1}, {member.hp}/{member.max_hp} HP)')
                         for index, member in enumerate(observation.party) if member.hp > 0 and not member.is_egg)
        case 'learn_move':
            index = observation.learning_party_index
            new_move = observation.learning_move
            if index is None or new_move is None or index >= len(observation.party):
                return ()
            return tuple(action(f'learn-move:{slot}', f'Forget {move.name} and learn {new_move.name} on {observation.party[index].species}')
                         for slot, move in enumerate(observation.party[index].moves)) + (action('learn-move:skip', f'Do not learn {new_move.name}'),)
        case 'evolution':
            return (action('evolve:yes', 'Allow this Pokémon to evolve'), action('evolve:no', 'Cancel this evolution'))
    return ()
