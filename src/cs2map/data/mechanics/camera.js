// @fragment camera
// @requires core
// @imports Instance, CSInputs, CustomCameraMode
// camera: FOLLOW_POSITION camera on an entity (FPV and chase views, R switches). CONTROLLED does not
// read the mouse, FOLLOW_POSITION does: the player keeps looking around.
//   const camera = Camera({core, follow: () => body, views: config.camera});   // {forward, up, chase:{forward, up}|false, start?:"fpv"|"chase"}
//   views.pilot: the FPV view follows the eyes of cfg.pilot() (the seated player) instead of the body origin.
function Camera(cfg) {
    const {core} = cfg;
    let cam, chase = cfg.views.start === "chase" && !!cfg.views.chase, held = false;
    const view = () => (chase && cfg.views.chase) ? cfg.views.chase : cfg.views;
    const usesEyes = c => !!(c.pilot && cfg.pilot && core.valid(cfg.pilot()));
    // Ahead of the pilot's eyes along the HORIZONTAL view, and only as far as the view points along the body:
    // a camera-space offset rises into the roof when looking up and reaches the glass when looking aside, where
    // the near clip plane cuts the cabin (sky through the roof, stripes at the window). World-space offset.
    function eyeOffset(c) {
        const a = cfg.pilot().GetEyeAngles(), body = cfg.follow();
        const rel = core.valid(body) ? (a.yaw - body.GetAbsAngles().yaw) * core.rad : 0;
        const k = (c.forward || 0) * Math.max(0, Math.cos(rel));
        return {x: Math.cos(a.yaw * core.rad) * k, y: Math.sin(a.yaw * core.rad) * k, z: 0};
    }
    function apply() {
        if (!core.valid(cam)) return;
        const c = view(), eyes = usesEyes(c);
        cam.SetFollowConfig({followEntity: eyes ? cfg.pilot() : cfg.follow(), followEyes: eyes,
                             followOffset: eyes ? eyeOffset(c) : {x: 0, y: c.right || 0, z: c.up},
                             cameraOffset: {x: eyes ? 0 : c.forward, y: 0, z: 0}, clipCameraOffset: !eyes});
    }
    return {
        get chase() { return chase; },
        // Another controller (a story camera, another vehicle) may own this player's camera.
        free(p) { return p.GetCustomCamera().GetMode() === CustomCameraMode.DISABLED; },
        start(p) { cam = p.GetCustomCamera(); apply(); cam.SetMode(CustomCameraMode.FOLLOW_POSITION); return cam.GetMode(); },
        stop() { if (core.valid(cam)) cam.SetMode(CustomCameraMode.DISABLED); cam = undefined; },
        mode() { return core.valid(cam) ? cam.GetMode() : 0; },
        apply,
        // Call every tick for the seated player: R (cfg.key) toggles FPV / chase, if a chase view exists.
        update(p) {
            const key = p.IsInputPressed(cfg.key || CSInputs.RELOAD);
            if (key && !held && cfg.views.chase) { chase = !chase; apply(); core.log("camera", {chase}); }
            else if (usesEyes(view())) apply();        // the eye offset follows the view
            held = key;
        },
    };
}
