# HUD and UI: themes and own layouts

| Task | Take |
| --- | --- |
| Restyle the HUD of a drone, car, turret, mini-game, weapon, story, ability | `"hud": {"theme", "position", ...}` on that operation |
| One look for the whole map | `set_ui_theme` before the HUD operations |
| Own HUD: score boards, timers, health bars, pictures | `add_hud` (xml + css) + a script that fills it |
| A menu the player clicks (choose a team, a class, a shop) | `add_hud` with `Button`s + `SetInputCaptureEnabled` + `OnCustomHudClicked` |
| Test it | `game_screenshot(hide_hud=false)`, `script_test` step `click` |

CS2 shows custom HUDs from `panorama/layout/custom_game` only: panels (`Panel`, `Label`, `Image`, `Button`) styled with CSS, filled and switched by a `cs_script` (no client scripts). Every HUD the tools make takes the same style keys.

## Style keys

| key | values |
| --- | --- |
| `theme` | `night` (dark blue, gold), `glass` (light frosted), `neon` (black, cyan glow), `paper` (beige, brown), `military` (olive), `minimal` (text only, shadow) |
| `position` | `top`, `bottom`, `left`, `right`, `center`, `top-left`, `top-right`, `bottom-left`, `bottom-right` (top-left stays under the radar) |
| `size` | `s`, `m`, `l`, `xl` (text and padding scale) |
| `width`, `offset` | pixels at 1080p; `offset:[x,y]` moves it (x right, y down) |
| `animation` | `none`, `fade`, `slide` (when the panel appears or hides) |
| `accent`, `background`, `text` | colours `[r,g,b]`, `[r,g,b,alpha]` or `"#rrggbbaa"` over the theme |
| `font` | `default` or `stratum` (CS2 ignores other font names here) |

```json
{"op":"set_ui_theme","theme":"neon","size":"m","animation":"fade"}
```

A map default fills only the keys an operation leaves out; give `position` per HUD. The story HUD keeps its own switches (`objective`, `subtitles`, `timer`) beside the style keys.

## Own layout: `add_hud`

```json
{"op":"add_hud","name":"scoreboard","origin":[0,0,16],
 "xml":"<root><Panel class=\"Root\" hittest=\"false\"><Panel id=\"board\" class=\"Hidden\"><Label id=\"title\" text=\"ROUND\" /><Label id=\"score\" text=\"{s:score}\" /></Panel></Panel></root>",
 "css":".Root { width: 100%; height: 100%; } #board { horizontal-align: center; margin-top: 40px; padding: 10px 24px; background-color: #000000c0; border-radius: 8px; flow-children: down; } #board.Hidden { visibility: collapse; } #title { color: #ffd36bff; font-size: 16px; } #score { color: white; font-size: 34px; }"}
```

`name` becomes the entity name and the layout `mcp_hud_<name>`; the CSS include is added for you. Allowed: `Panel`, `Label` (`text`, `{s:var}` for a script variable), `Image` (`src`: `file://{images}/custom_game/mcp_hud_<name>/<file>.png`, the files given in `images: {"file.png": "<local png>"}`, or a stock `s2r://panorama/images/...`), `Button`; attributes `id`, `class`, `hittest`, `text`, `src`. Useful CSS: `flow-children: down|right`, `horizontal-align`, `vertical-align`, `margin`, `padding`, `width`, `height`, `background-color` (also `gradient( linear, 0% 0%, 100% 0%, from(#..), to(#..) )`), `border`, `border-radius`, `box-shadow`, `color`, `font-size`, `font-weight`, `text-transform`, `letter-spacing`, `opacity`, `transition-property`/`transition-duration`, `:hover` on buttons.

The script drives it:

```js
// @requires core
import { Instance } from "cs_script/point_script";
const core = Core({tag: "SCORE", name: "board", state: "score_state"});
core.on("OnRoundStart", () => {
    const h = Instance.FindEntityByName("scoreboard");
    h.SetHasClass("board", "Hidden", false);
    h.SetDialogVariableString("board", "score", "0 : 0");
});
```

Per player: `SetHasClassForPlayer(slot, panel, class, on)`, `SetDialogVariableStringForPlayer(slot, panel, var, text)`.

## A clickable menu

Buttons need the cursor: `layout.SetInputCaptureEnabled(slot, true)` frees the mouse (the player stops moving) until you set it back to false. Clicks arrive in `Instance.OnCustomHudClicked(({player, layout, buttonId}) => ...)` (with `@requires core`: `core.on("OnCustomHudClicked", fn)`).

```js
function open(p) {
    const h = Instance.FindEntityByName("menu"), s = core.slot(p);
    h.SetHasClassForPlayer(s, "menu", "Hidden", false);
    h.SetInputCaptureEnabled(s, true);
}
core.on("OnCustomHudClicked", e => {
    const s = e.player.GetPlayerSlot();
    core.log("clicked", {button: e.buttonId});
    e.layout.SetHasClassForPlayer(s, "menu", "Hidden", true);
    e.layout.SetInputCaptureEnabled(s, false);
});
```

Verified in CS2 (`b10_menu`): a real click on a `Button` reaches the script, the menu hides and movement returns. In `script_test` click with `{"click":[x,y]}` (fractions of the game window) and check the log with `expect`.

## By hand

Anything a HUD needs is a panel with an id, a class to switch (`Hidden`, `Low`, `Active`) and a `{s:var}` text; keep one layout per mechanic so panels never overlap. Bars: a panel whose `width` class steps (`W10` … `W100`) or a `Label` of block characters. Icons: small PNGs through `images`. Look at the result with `game_screenshot(hide_hud=false)`: fonts, sizes and clipping are only judged in the game.
