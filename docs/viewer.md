# Local viewer

The viewer is part of the existing PokéBot process. Run the normal bootstrap and launcher, then open:

<http://127.0.0.1:8888/jev/index.html>

The plugin serves `web/` through PokéBot's existing aiohttp application, so frontend edits are available after a refresh. The bootstrap links the plugin entrypoint instead of copying it. It accepts an existing link to the repository source, migrates a byte-for-byte matching old copy, and refuses unrelated files or links.

The page embeds the upstream `/stream_video?fps=15` feed and polls `/custom_state` four times per second. The game remains the largest element. The decision panel shows the current goal and action, Jev's complete preference distribution, request latency, party and opponent HP, the six most recent choices, and starter/rescue/rival progress. Deterministic choices say that probabilities are unavailable instead of inventing a distribution.

Pending, error, paused, disconnected, and loaded-checkpoint states have distinct labels. A rival flag already present in a loaded save is labeled as a checkpoint; only the mode's `completed` proof marks the current run complete.

Pause and resume send `{ "paused": true | false }` to `POST /jev/control`. The HTTP thread validates that payload and puts a callback on PokéBot's existing `work_queue`. The callback resolves the active `JevEmeraldMode` and calls `set_paused` on the owner thread. The HTTP handler never reads emulator memory or runs an action. Pausing invalidates any pending decision in the mode; resuming causes the frame loop to take a fresh observation.

The plugin's frame listener publishes a new namespaced snapshot at `jev_emerald` from immutable mode properties. The snapshot is an explicit allowlist: it contains viewer status, observations needed by the panel, legal actions, the active action, progress, and recent choices. It does not serialize the mode object, callbacks, request state, API configuration, or credentials.

With `JEV_PLANNER_MODEL` set, a separate Planner advice panel shows the real LLM
objective, model, request count and reason for consulting it. While planning,
inputs remain neutral. Jev's subsequent distribution stays in the action panel;
the planner neither presses buttons nor supplies those probabilities.

Run the focused checks with:

```sh
.venv/bin/pytest tests/test_web_state.py -q
```

The top-level multipart stream may be blocked when opened directly by Chrome. The viewer embeds it as an image, which is the supported path verified for this project.
