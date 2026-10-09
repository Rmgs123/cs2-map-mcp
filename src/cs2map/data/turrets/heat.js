// @fragment turret_heat
// @requires core
// A small, reusable heat accumulator for stationary weapons. The adapter decides when a shot
// happened and calls add(); the fragment owns cooling, lockout, and the HUD text.
function Heat(cfg) {
    const core = cfg.core, max = 1;
    let value = 0, locked = false, last = 0;
    function update(now, dt) {
        const d = Math.max(0, Math.min(.1, dt || 0));
        if (d && cfg.cool > 0) value = Math.max(0, value - cfg.cool * d);
        if (locked && value <= 0) { locked = false; core.log("cooled", {}); }
        last = now;
    }
    function add(amount, now) {
        if (locked) return false;
        value = Math.min(max, value + amount);
        if (value >= max) { value = max; locked = true; core.log("overheat", {value}); }
        return true;
    }
    function text() {
        const n = Math.round(value * 10);
        return "HEAT [" + "#".repeat(n) + "-".repeat(10 - n) + "]" + (locked ? "   OVERHEATED" : "");
    }
    return {update, add, text, get value() { return value; }, get locked() { return locked; }, get last() { return last; }, reset() { value = 0; locked = false; }};
}
