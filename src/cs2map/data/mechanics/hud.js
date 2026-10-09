// @fragment hud
// @requires core
// @imports Instance, CustomHudLayout
// hud: one custom_hud_layout (panorama/layout/custom_game/<layout>.vxml) with a panel class and one
// text variable, shown per player. Every mechanic has its own layout, so panels never overlap.
//   const hud = Hud({core, name: E.hud, panel: "control", variable: "telemetry"});
function Hud(cfg) {
    const {core} = cfg, due = new Map();
    let layout;
    const slotOf = p => core.slot(p);
    return {
        // Finds the layout entity; true when it is really a CustomHudLayout.
        find() { layout = Instance.FindEntityByName(cfg.name); return typeof CustomHudLayout === "undefined" ? core.valid(layout) : layout instanceof CustomHudLayout; },
        get entity() { return layout; },
        // The panel starts hidden for everybody (call on map/round start).
        hideAll() { if (core.valid(layout)) layout.SetHasClass(cfg.panel, "Hidden", true); },
        reset() { if (core.valid(layout)) layout.Reset(); },
        show(p) { if (core.valid(layout)) layout.SetHasClassForPlayer(slotOf(p), cfg.panel, "Hidden", false); },
        hide(p) { if (core.valid(layout) && slotOf(p) !== undefined) layout.SetHasClassForPlayer(slotOf(p), cfg.panel, "Hidden", true); },
        hideSlot(slot) { if (core.valid(layout)) layout.SetHasClassForPlayer(slot, cfg.panel, "Hidden", true); },
        flag(p, cls, on) { if (core.valid(layout)) layout.SetHasClassForPlayer(slotOf(p), cfg.panel, cls, !!on); },
        text(p, value) { if (core.valid(layout)) layout.SetDialogVariableStringForPlayer(slotOf(p), cfg.panel, cfg.variable, value); },
        // text + visible (weapon-style status line)
        put(p, value) {
            const s = slotOf(p);
            if (!core.valid(layout) || s === undefined) return;
            layout.SetDialogVariableStringForPlayer(s, cfg.panel, cfg.variable, value);
            layout.SetHasClassForPlayer(s, cfg.panel, "Hidden", false);
        },
        // Throttle: true at most once per `period` seconds for this key (a player slot, a name).
        due(key, now, period) { if (now < (due.get(key) || 0)) return false; due.set(key, now + period); return true; },
        resetDue() { due.clear(); },
    };
}
