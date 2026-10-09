"""MCP sound tools and map operations."""
from .. import sound as sound_impl, checks


def register(mcp, project):
    @mcp.tool()
    def sound_search(query: str = '', limit: int = 30) -> dict:
        """Find stock/addon event names with their .vsnd files, plus raw sounds. Limit 1..100.
        Use returned event in add_sound; raw vsnd needs sound_event. Example query='wind'."""
        return sound_impl.sound_search(project, query, limit)

    @mcp.tool()
    def sound_import(name: str, file: str, loop: bool = False) -> dict:
        """Import local WAV (PCM/float), MP3 or OGG, mono/stereo <=120 s, and compile.
        MP3/OGG use the installed bundled Blender aud decoder without downloads.
        Converts to PCM16 at 44.1/48 kHz; loop adds cue/smpl markers. Does not smooth imported seams.
        Returns vsnd, duration seconds, channels, rate Hz, loop, RMS/peak/seam, log."""
        return sound_impl.sound_import(project, name, file, loop)

    @mcp.tool()
    def sound_generate(name: str, preset: str, seconds: float = 2, params: dict | None = None) -> dict:
        """Synthesize and compile beep, alarm, hum, wind, rain, drone, click, whoosh or rumble.
        Duration .05..120 s. Params: seed, gain .001..95%, frequency Hz, rate 44100/48000,
        channels 1/2, loop bool. Ambient presets loop by default; seamless generated loops.
        Example name='alarm', preset='alarm', seconds=4, params={'gain':0.4}."""
        return sound_impl.sound_generate(project, name, preset, seconds, params)

    @mcp.tool()
    def sound_event(name: str, kind: str, sound: str | None = None, sounds: list[str] | None = None,
                    volume: float = 1, pitch: float = 1, distance: float = 1500,
                    interval: list[float] | None = None, radius: list[float] | None = None,
                    children: list[str] | None = None, reverb: str | None = None, fade: float | None = None) -> dict:
        """Create/replace mcp.name, preserve and back up all other addon events, then compile.
        Kinds: oneshot/loop 3D (distance units), ambient/ui 2D, random_around
        (interval [min,max] seconds, radius [min,max] units), soundscape (children + reverb).
        Optional reverb is an installed Valve reverb_* preset; fade .05..10 seconds uses event curves.
        Supply sound OR sounds as .vsnd paths; soundscape only takes children event names.
        Example name='alarm', kind='loop', sound='sounds/mcp/alarm.vsnd'."""
        return sound_impl.sound_event(project, name, kind, sound, sounds, volume, pitch, distance,
                                 interval, radius, children, reverb, fade)

    project.register_op('add_sound', sound_impl.add_sound)
    project.register_op('add_soundscape', sound_impl.add_soundscape)
    project.register_op('add_ambience', sound_impl.add_ambience)
    checks.register_check('sound', sound_impl.check_sound)
