# Experimental: player models for CT/T (not supported)

**This is not a feature of CS2 Map MCP.** There are no MCP tools for it and the AI guide
does not mention it. Custom player models for CT and T are not supported: maps use the
stock agents. The code is kept only as a starting point for future research.

What is here:

- `characters.py` reads a manifest `resources/characters/<name>/character.json`, runs Blender
  and writes a ModelDoc `.vmdl` for a player model; `generate_script` writes a `cs_script`
  that sets T/CT models on spawn.
- `blender_chars/`: bone-name presets (Mixamo, VRM, MMD, Unity, ValveBiped), retarget onto the
  CS2 player skeleton (94 bones), DMX export, previews.

Why it is shelved: the CS2 player skeleton, its animation graph and hitboxes are hard to
match. Retargeted models worked in game but looked distorted.

Not affected: custom models for the environment and props (`model_build`, `model_import`),
and future animated props (own skeleton, `prop_dynamic`, script-driven).

`cs2_skeleton.json` and `modeldoc_agent_nodes.json` were extracted from a stock CS2 agent
model; before the repository goes public they must be generated locally instead.
