# First gym autonomous run

Approved intent: earn the first Emerald badge with Jev choosing meaningful actions and Luna coaching, using ordinary game inputs. The user delegated implementation decisions and authorized execution. Full-game completion remains future work.

Extend the existing observation → legal menu → model → bounded executor loop. Default the app to `first-gym`; retain explicit `rival` scope for existing benchmarks. Replace the rival terminal boundary with target-aware progression. First-gym completion requires an observed Roxanne win followed by a newly acquired Stone Badge; loaded completed saves are labeled checkpoints, never fresh victories.

Read badge and Petalburg tutorial state on the emulator thread. Carry compact milestone knowledge and Pokémon reference facts to both player and coach. Reference facts describe species mechanics, never hidden opponent moves or RNG. Existing evidence memory remains the persistence mechanism; retain current objective/route guidance across map transitions. Coach once at new post-opening milestones and before gym preparation, plus existing repeated-attempt triggers. Keep call/token budgets explicit.

Extend normal-input skills only as needed through Rustboro: reachable centers, meaningful choices, shop/potion handling, level-up/evolution and fainted replacement handling. Reuse upstream routines and data. No new services, dependencies, injected party/story state, or silent player replacement.

Viewer: prominent objective, party HP, milestones, badge count, current action, coaching and manual-action accounting. Detailed probabilities and tokens become secondary. Do not label pause/resume as gameplay intervention or claim external emulator inputs are observable if they are not.

Validation: focused progression/trigger/action tests, full existing suite and service checks, real-ROM probes, browser rendering, and a bounded fresh autonomous run. Preserve all failed attempts and report actual outcome; implementation alone is not evidence of a badge win.
