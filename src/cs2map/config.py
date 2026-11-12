"""Project paths and settings.  All paths are absolute and resolved once."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

def vrf_cli():
    """Source2Viewer-CLI of the version pinned in config/tool-sources.json (one place to bump it)."""
    try:
        sources = json.loads((PROJECT_ROOT / 'config' / 'tool-sources.json').read_text(encoding='utf-8'))
        folder = next(a['extract_to'] for a in sources['archives'] if a['id'] == 'vrf-cli')
    except (OSError, ValueError, KeyError, StopIteration):
        folder = 'tools/vrf-cli-20.1'
    return PROJECT_ROOT / folder / 'Source2Viewer-CLI.exe'


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = PROJECT_ROOT / 'config' / 'settings.json'
_NAME = re.compile(r'^[a-z0-9_]{1,64}$')


class ConfigError(RuntimeError):
    pass


RESTART_HUNG_DEFAULT = 180     # seconds a Workshop Tools window must stay "Not responding" before a restart
RESTART_HUNG_MIN = 60          # map loads and light bakes freeze the window for up to ~1 min
# "game_focus": 'never' = CS2 always works behind the user's windows (steps that need a real mouse are emulated or
# refused); 'input_only' = it comes to the front only for a step with real mouse input and gives the focus back at
# once; 'always' = in front for every frame and test (the behaviour before 1.4).
GAME_FOCUS = ('never', 'input_only', 'always')
BUILD_PRIORITY = ('low', 'normal')
# "heavy_builds": 'allow' = light bakes and releases start when the AI asks; 'ask' = they are refused with their
# cost until the user allows them in the panel (permits.heavy_gate).
HEAVY_BUILDS = ('allow', 'ask')
PANEL_PORT = 8717
# Settings a running server reads again when config/settings.json changes (the panel's toggles write them).
LIVE_KEYS = ('game_focus', 'build_priority', 'heavy_builds', 'restart_hung_cs2', 'restart_hung_cs2_seconds',
             'restart_hung_cs2_user', 'panel', 'panel_port')


@dataclass(frozen=True)
class Settings:
    cs2_path: Path
    addon: str
    compile_threads: int = 0
    blender: str = ''                  # explicit blender.exe (settings "blender" or CS2MAP_BLENDER); '' = search
    limits: tuple = ()                 # disk limits (settings "limits"): (("screenshots_mb", 800), ...)
    backups_dir: str = ''              # settings "backups_dir" or CS2MAP_BACKUPS; '' = <project>/backups
    backup_sources: tuple = ()         # settings "backup_sources": more folders for the sources snapshots
    restart_hung_cs2: bool = False     # settings "restart_hung_cs2": restart a hung Workshop Tools the MCP launched
    restart_hung_cs2_seconds: int = RESTART_HUNG_DEFAULT   # how long it must hang first (>= RESTART_HUNG_MIN)
    restart_hung_cs2_user: bool = False   # settings "restart_hung_cs2_user": also a Workshop Tools the user started
    marks_key: str = 'F8'              # settings "marks_key": the CS2 key of player marks (marks.py); '' = off
    game_focus: str = 'input_only'     # settings "game_focus": when the CS2 window may take the focus (GAME_FOCUS)
    build_priority: str = 'low'        # settings "build_priority": 'low' = compiler and Blender jobs below normal
    heavy_builds: str = 'allow'        # settings "heavy_builds": 'ask' = light bakes wait for the user's yes (panel)
    panel: bool = False                # settings "panel": the server starts the local status page (panel.py)
    panel_port: int = PANEL_PORT       # settings "panel_port": its port on 127.0.0.1

    # -- CS2 layout ----------------------------------------------------------
    @property
    def game_bin(self) -> Path:
        return self.cs2_path / 'game' / 'bin' / 'win64'

    @property
    def resourcecompiler(self) -> Path:
        return self.game_bin / 'resourcecompiler.exe'

    @property
    def cs2_exe(self) -> Path:
        return self.game_bin / 'cs2.exe'

    @property
    def content_addon(self) -> Path:
        return self.cs2_path / 'content' / 'csgo_addons' / self.addon

    @property
    def game_addon(self) -> Path:
        return self.cs2_path / 'game' / 'csgo_addons' / self.addon

    # -- project layout -------------------------------------------------------
    @property
    def backups(self) -> Path:
        if not self.backups_dir:
            return PROJECT_ROOT / 'backups'
        path = Path(self.backups_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def logs(self) -> Path:
        return PROJECT_ROOT / 'logs'

    @property
    def screenshots(self) -> Path:
        return PROJECT_ROOT / 'screenshots'

    @property
    def blender_exe(self) -> Path:
        return find_blender(self.blender)

    def limit(self, key: str, default):
        return dict(self.limits).get(key, default)

    def map_path(self, name: str) -> Path:
        """Source .vmap of a map in our addon."""
        check_name(name, 'map name')
        return self.content_addon / 'maps' / f'{name}.vmap'

    def compiled_map(self, name: str) -> Path:
        return self.game_addon / 'maps' / f'{name}.vpk'

    def official_map(self, name: str) -> Path:
        """A map shipped with CS2 (de_inferno, cs_italy...): read-only reference for frames."""
        return self.cs2_path / 'game' / 'csgo' / 'maps' / f'{name}.vpk'

    def map_vpk(self, name: str) -> Path:
        """The addon map, else an official map of that name (references), else the addon path."""
        own = self.compiled_map(name)
        return own if own.exists() or not self.official_map(name).exists() else self.official_map(name)

    def addon_relative(self, path: Path) -> str:
        """Path relative to the content addon, with forward slashes (asset path)."""
        return Path(path).resolve().relative_to(self.content_addon.resolve()).as_posix()


def check_name(name: str, what: str = 'name') -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ConfigError(f'{what} must be 1-64 chars of a-z, 0-9, _ (got {name!r})')
    return name


CS2_DIR = Path('steamapps') / 'common' / 'Counter-Strike Global Offensive'


def steam_libraries(steam: Path | None = None) -> list[Path]:
    """Steam library roots: the Steam folder (registry, read-only) and libraryfolders.vdf."""
    if steam is None:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                steam = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
        except (ImportError, OSError):
            return []
    libs = [Path(steam)]
    vdf = Path(steam) / 'steamapps' / 'libraryfolders.vdf'
    if vdf.is_file():
        text = vdf.read_text(encoding='utf-8', errors='replace')
        libs += [Path(m.replace('\\\\', '\\')) for m in re.findall(r'"path"\s+"([^"]+)"', text)]
    return list(dict.fromkeys(libs))


BLENDER_DEFAULT = PROJECT_ROOT / 'tools' / 'blender-4.5.14-windows-x64' / 'blender.exe'
BLENDER_SUPPORTED = ((4, 5), (5, 9))      # inclusive range of major.minor the pipelines are tested with


def blender_version(exe: Path) -> tuple[int, int] | None:
    """major.minor of a Blender install: the version folder next to blender.exe (e.g. 4.5 or 5.2)."""
    found = [tuple(int(x) for x in d.name.split('.')) for d in Path(exe).parent.glob('[0-9]*.[0-9]*')
             if d.is_dir() and re.fullmatch(r'\d+\.\d+', d.name)]
    return max(found) if found else None


def blender_candidates() -> list[Path]:
    """Installed Blenders in search order: Steam libraries (the usual install), tools/ (a portable fallback,
    newest first), Program Files."""
    out = [lib / 'steamapps' / 'common' / 'Blender' / 'blender.exe' for lib in steam_libraries()]
    out += sorted((PROJECT_ROOT / 'tools').glob('blender-*/blender.exe'), reverse=True)
    for base in {os.environ.get('ProgramFiles', r'C:\Program Files'), r'C:\Program Files'}:
        out += sorted((Path(base) / 'Blender Foundation').glob('Blender*/blender.exe'), reverse=True)
    return [p for p in dict.fromkeys(out) if p.is_file()]


def find_blender(explicit: str = '') -> Path:
    """The Blender every pipeline uses: settings/CS2MAP_BLENDER, else the first installed candidate. Returns the
    pinned default path when nothing is installed (callers report 'Blender not found' with that path)."""
    chosen = os.environ.get('CS2MAP_BLENDER') or explicit
    if chosen:
        return Path(chosen)
    found = blender_candidates()
    # Steam updates Blender by itself: a version the pipelines are not tested with gives way to another install.
    supported = [p for p in found if (v := blender_version(p)) and BLENDER_SUPPORTED[0] <= v <= BLENDER_SUPPORTED[1]]
    return (supported or found or [BLENDER_DEFAULT])[0]


def find_cs2(steam: Path | None = None) -> Path | None:
    """CS2 install folder with Workshop Tools (resourcecompiler), or None."""
    for lib in steam_libraries(steam):
        cs2 = lib / CS2_DIR
        if (cs2 / 'game' / 'bin' / 'win64' / 'resourcecompiler.exe').is_file():
            return cs2
    return None


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    """config/settings.json if present; otherwise CS2 is found through Steam and addon is mcp_lab."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    cs2 = os.environ.get('CS2MAP_CS2_PATH') or data.get('cs2_path') or find_cs2()
    if not cs2:
        raise ConfigError('CS2 with Workshop Tools not found: set cs2_path in config/settings.json '
                          '(see config/settings.example.json) or the CS2MAP_CS2_PATH variable')
    cs2 = Path(cs2)
    addon = os.environ.get('CS2MAP_ADDON', data.get('addon', 'mcp_lab'))
    check_name(addon, 'addon')
    if addon in ('addon_template', 'workshop_items', 'cs_script_demo'):
        raise ConfigError(f'addon {addon!r} belongs to Valve; choose another')
    limits = data.get('limits') or {}
    if not isinstance(limits, dict):
        raise ConfigError('settings "limits" must be an object, e.g. {"screenshots_mb": 800}')
    sources = data.get('backup_sources') or []
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        raise ConfigError('settings "backup_sources" must be a list of folders, e.g. ["my_generators"]')
    restart = data.get('restart_hung_cs2', False)
    seconds = data.get('restart_hung_cs2_seconds', RESTART_HUNG_DEFAULT)
    user = data.get('restart_hung_cs2_user', False)
    if not isinstance(restart, bool) or not isinstance(user, bool):
        raise ConfigError('settings "restart_hung_cs2" and "restart_hung_cs2_user" must be true or false')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < RESTART_HUNG_MIN:
        raise ConfigError(f'settings "restart_hung_cs2_seconds" must be a number >= {RESTART_HUNG_MIN}')
    marks_key = data.get('marks_key', 'F8')
    if not isinstance(marks_key, str) or not re.fullmatch(r'[A-Za-z0-9_]{0,16}', marks_key):
        raise ConfigError('settings "marks_key" must be a CS2 key name such as "F8" (or "" to turn marks off)')
    game_focus = data.get('game_focus', 'input_only')
    if game_focus not in GAME_FOCUS:
        raise ConfigError(f'settings "game_focus" must be one of {", ".join(GAME_FOCUS)}')
    build_priority = data.get('build_priority', 'low')
    if build_priority not in BUILD_PRIORITY:
        raise ConfigError(f'settings "build_priority" must be one of {", ".join(BUILD_PRIORITY)}')
    heavy_builds = data.get('heavy_builds', 'allow')
    if heavy_builds not in HEAVY_BUILDS:
        raise ConfigError(f'settings "heavy_builds" must be one of {", ".join(HEAVY_BUILDS)}')
    panel, port = data.get('panel', False), data.get('panel_port', PANEL_PORT)
    if not isinstance(panel, bool):
        raise ConfigError('settings "panel" must be true or false')
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ConfigError('settings "panel_port" must be a port number 1024..65535')
    s = Settings(cs2_path=cs2, addon=addon, compile_threads=int(data.get('compile_threads', 0)),
                 game_focus=game_focus, build_priority=build_priority, heavy_builds=heavy_builds,
                 panel=panel, panel_port=port,
                 blender=str(data.get('blender') or ''), limits=tuple(sorted(limits.items())),
                 backups_dir=str(os.environ.get('CS2MAP_BACKUPS') or data.get('backups_dir') or ''),
                 backup_sources=tuple(sources), restart_hung_cs2=restart, restart_hung_cs2_seconds=int(seconds),
                 restart_hung_cs2_user=user, marks_key=marks_key)
    check_backups_dir(s)
    return s


def panel_url(s: Settings) -> str | None:
    """The address of the status page when settings "panel" is on."""
    return f'http://127.0.0.1:{s.panel_port}/' if getattr(s, 'panel', False) else None


def update_settings(values: dict, path: Path = SETTINGS_FILE) -> dict:
    """Set keys in config/settings.json, keeping the others (setup.ps1 asks the per-install questions with it)."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')       # replaced in one step: a running server never reads half a file
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)
    return data


def check_backups_dir(s: Settings) -> None:
    """The backups folder must not sit inside what it backs up or what CS2 compiles."""
    real = Path(os.path.realpath(s.backups))
    from . import mylayer
    inside = [('my/', mylayer.root()), ('the CS2 folder', s.cs2_path)]
    inside += [(f'backup source {x}', Path(x) if Path(x).is_absolute() else PROJECT_ROOT / x) for x in s.backup_sources]
    for label, root in inside:
        r = Path(os.path.realpath(root))
        if real == r or r in real.parents:
            raise ConfigError(f'settings "backups_dir" ({s.backups}) is inside {label}; choose a folder outside '
                              'it, best on another disk')


def ensure_inside(path: Path, *roots: Path) -> Path:
    """Refuse writes outside the allowed roots (resolves .., symlinks, junctions)."""
    real = Path(os.path.realpath(path))
    for root in roots:
        r = Path(os.path.realpath(root))
        if real == r or r in real.parents:
            return real
    raise ConfigError(f'refusing to write outside allowed folders: {path}')


def writable_roots(s: Settings) -> tuple[Path, ...]:
    return (PROJECT_ROOT, s.content_addon, s.game_addon, s.backups)
