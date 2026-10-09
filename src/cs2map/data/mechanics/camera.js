// @fragment camera
// @requires core
// @imports Instance, CSInputs, CustomCameraMode
// camera: FOLLOW_POSITION camera on an entity (FPV and chase views, R switches). CONTROLLED does not
// read the mouse, FOLLOW_POSITION does: the player keeps looking around.
//   const camera = Camera({core, follow: () => body, views: config.camera});   // {forward, up, chase:{forward, up}|false, start?:"fpv"|"chase"}
function Camera(cfg) {
    const {core} = cfg;
    let cam, chase = cfg.views.start === "chase" && !!cfg.views.chase, held = false;
    const view = () => (chase && cfg.views.chase) ? cfg.views.chase : cfg.views;
    function apply() {
        if (!core.valid(cam)) return;
        const c = view();
        cam.SetFollowConfig({followEntity: cfg.follow(), followOffset: {x: 0, y: c.right || 0, z: c.up},
                             cameraOffset: {x: c.forward, y: 0, z: 0}, clipCameraOffset: true});
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
            held = key;
        },
    };
}
