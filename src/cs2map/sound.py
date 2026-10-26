"""CS2 sound assets, addon events and map operations. No external converters."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import re
import shutil
import struct
import threading
import time
from pathlib import Path

import numpy as np

from . import compiler, kv3, resource
from .config import PROJECT_ROOT, check_name, ensure_inside


class SoundError(ValueError):
    pass


def _name(value, label):
    check_name(value, label)
    if not re.fullmatch(r'[a-z0-9_]{1,64}', value):
        raise SoundError(f'{label} must contain only a-z, 0-9 and underscore')


def decode_events(data: bytes, block: str = 'DATA') -> dict:
    """Decode the installed CS2 KV3 v5 DATA tree (uncompressed/LZ4/Zstd).

    The two scalar pools and typed auxiliary arrays are read structurally;
    string-table proximity is never treated as an event-to-sound association.
    Binary blobs and other KV3 versions are rejected, not guessed.
    Layout reference: ValveResourceFormat ResourceTypes/BinaryKV3.cs.
    """
    from .vtex import _lz4_block
    try:
        start, size = resource.blocks(data)[block]
        raw = data[start:start + size]
        if len(raw) < 120 or struct.unpack_from('<I', raw)[0] != 0x4B563305:
            raise SoundError('sound events require binary KV3 v5')
        method, dictionary, frame = struct.unpack_from('<IHH', raw, 20)
        n1, n4, n8, nt = struct.unpack_from('<4I', raw, 28)
        total, compressed, blobs, blob_bytes, n2, block_sizes = struct.unpack_from('<6I', raw, 48)
        u1, c1, u2, c2, b1, b2, b4, b8, _, objects, _, _ = struct.unpack_from('<12I', raw, 72)
        if dictionary or blobs or blob_bytes or block_sizes or total != u1 + u2:
            raise SoundError('unsupported KV3 dictionary/blob layout')
        if max(total, u1, u2) > 128 * 1024 * 1024:
            raise SoundError('KV3 sound event buffer exceeds 128 MiB')
        pos = 120

        def unpack_buffer(unpacked, packed):
            nonlocal pos
            length = unpacked if method == 0 else packed
            payload = raw[pos:pos + length]; pos += length
            if len(payload) != length:
                raise SoundError('truncated compressed sound events')
            if method == 0:
                result = payload
            elif method == 1 and frame == 16384:
                result = _lz4_block(payload, unpacked)
            elif method == 2:
                from compression import zstd
                result = zstd.decompress(payload)
            else:
                raise SoundError(f'unsupported sound KV3 compression {method}/{frame}')
            if len(result) != unpacked:
                raise SoundError('wrong sound KV3 decompressed size')
            return result

        first = unpack_buffer(u1, c1); second = unpack_buffer(u2, c2)

        def pools(buf, counts, offset=0):
            out = {}
            for width, count in zip((1, 2, 4, 8), counts):
                if count:
                    offset = (offset + width - 1) // width * width
                end = offset + width * count
                if end > len(buf):
                    raise SoundError('truncated KV3 scalar pool')
                out[width] = [buf[offset:end], 0]; offset = end
            return out, offset

        aux, end1 = pools(first, (n1, n2, n4, n8))
        main, end2 = pools(second, (b1, b2, b4, b8), objects * 4)

        def take(pool, fmt):
            width = struct.calcsize(fmt); buf, offset = pool[width]
            value = struct.unpack_from('<' + fmt, buf, offset)[0]
            pool[width][1] += width
            return value

        count_strings = take(aux, 'i')
        if not 0 <= count_strings <= n1:
            raise SoundError('invalid KV3 string count')
        strings = []; sp = 0
        for _ in range(count_strings):
            end = aux[1][0].index(b'\0', sp)
            strings.append(aux[1][0][sp:end].decode('utf-8')); sp = end + 1
        aux[1][1] = sp
        types = second[end2:end2 + nt]; tp = 0; op = 0
        if second[end2 + nt:end2 + nt + 4] != b'\x00\xdd\xee\xff':
            raise SoundError('invalid KV3 event trailer')

        def read_type():
            nonlocal tp
            tag = types[tp]; tp += 1
            if tag & 0x80:
                tp += 1; tag &= 0x3f  # resource/soundevent flags do not alter scalar values
            return tag

        def string(index):
            return '' if index == -1 else strings[index]

        def read(tag, pool, depth=0):
            nonlocal op
            if depth > 100:
                raise SoundError('KV3 sound events exceed nesting limit')
            constants = {1: None, 13: True, 14: False, 15: 0, 16: 1, 17: 0., 18: 1.}
            if tag in constants:
                return constants[tag]
            scalar = {2: 'B', 3: 'q', 4: 'Q', 5: 'd', 11: 'i', 12: 'I',
                      19: 'f', 20: 'h', 21: 'H', 23: 'B'}
            if tag in scalar:
                value = take(pool, scalar[tag])
                return bool(value) if tag == 2 else value
            if tag == 6:
                return string(take(pool, 'i'))
            if tag == 9:
                count = struct.unpack_from('<i', second, op)[0]; op += 4
                if not 0 <= count <= 1000000 or op > objects * 4:
                    raise SoundError('invalid KV3 object count')
                obj = {}
                for _ in range(count):
                    child = read_type(); key = string(take(pool, 'i'))
                    obj[key] = read(child, pool, depth + 1)
                return obj
            if tag in (8, 10, 24, 25):
                count = take(pool, 'i' if tag in (8, 10) else 'B')
                if not 0 <= count <= 1000000:
                    raise SoundError('invalid KV3 array count')
                if tag == 8:
                    return [read(read_type(), pool, depth + 1) for _ in range(count)]
                subtype = read_type(); selected = aux if tag == 25 else pool
                return [read(subtype, selected, depth + 1) for _ in range(count)]
            raise SoundError(f'unsupported KV3 sound type {tag}')

        result = read(read_type(), main)
        if not isinstance(result, dict) or tp != len(types) or op != objects * 4:
            raise SoundError('unconsumed sound event structure')
        for pool in (main, aux):
            if any(offset != len(buf) for buf, offset in pool.values()):
                raise SoundError('unconsumed sound event scalar pool')
        if end1 != len(first) or end2 + nt + 4 != len(second) or pos != len(raw):
            raise SoundError('unconsumed sound event bytes')
        return result
    except SoundError:
        raise
    except (IndexError, KeyError, struct.error, UnicodeError, ValueError) as exc:
        raise SoundError(f'invalid compiled sound events: {exc}') from exc


def _source_events(project):
    path = ensure_inside(project.s.content_addon / 'soundevents' / 'soundevents_addon.vsndevts',
                         project.s.content_addon / 'soundevents')
    if not path.is_file():
        raise SoundError('addon soundevents_addon.vsndevts is missing; initialize the addon first')
    header, tree = kv3.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(tree, dict):
        raise SoundError('sound event source root must be an object')
    return path, header, tree


def _vsnds(value):
    if isinstance(value, kv3.Flagged):
        value = value.value
    if isinstance(value, str):
        return [value] if value.endswith('.vsnd') else []
    if isinstance(value, (dict, list)):
        return sorted({s for child in (value.values() if isinstance(value, dict) else value)
                       for s in _vsnds(child)})
    return []


_INDEX_LOCK = threading.RLock()
_INDEX_MEMORY = {}


def stock_index(project):
    files = project.game
    fingerprint = [[str(v.path), v.path.stat().st_size, v.path.stat().st_mtime_ns] for _, v in files.vpks]
    key = json.dumps(fingerprint)
    cache = ensure_inside(PROJECT_ROOT / '.local' / 'sound' / 'stock_index.json', PROJECT_ROOT)
    with _INDEX_LOCK:
        if key in _INDEX_MEMORY:
            return _INDEX_MEMORY[key]
        if cache.is_file():
            try:
                stored = json.loads(cache.read_text(encoding='utf-8'))
                if stored.get('version') == 1 and stored.get('fingerprint') == fingerprint:
                    _INDEX_MEMORY[key] = stored; return stored
            except (OSError, ValueError):
                pass
        events = {}; failed = []
        for path in files.search('', 'vsndevts', 10000):
            try:
                tree = decode_events(files.read(path + '_c'))
                for name, body in tree.items():
                    if isinstance(body, dict):
                        events.setdefault(name, {'event': name, 'file': path, 'sounds': _vsnds(body),
                                                'base': body.get('base', '')})
            except (SoundError, OSError) as exc:
                failed.append({'file': path, 'error': str(exc)})
        def inherited(name, seen):
            if isinstance(name, dict):
                return inherited(name.get('event_name', ''), seen)
            if isinstance(name, list):
                return sorted({s for base in name for s in inherited(base, seen)})
            if name not in events or name in seen:
                return []
            event = events[name]
            return event['sounds'] or inherited(event['base'], seen | {name})
        for name in events:
            events[name]['sounds'] = inherited(name, set())
        stored = {'version': 1, 'fingerprint': fingerprint, 'events': events, 'failed': failed}
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix('.tmp'); tmp.write_text(json.dumps(stored), encoding='utf-8'); os.replace(tmp, cache)
        _INDEX_MEMORY[key] = stored
        return stored


def sound_search(project, query: str = '', limit: int = 30) -> dict:
    if not isinstance(query, str) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise SoundError('query must be text and limit must be 1..100')
    words = query.casefold().split(); index = stock_index(project)
    _, _, own = _source_events(project)
    matches = []
    def own_sounds(name, seen):
        if name in seen or name not in own or not isinstance(own[name], dict): return []
        body = own[name]; found = _vsnds(body)
        base = body.get('base', '')
        if isinstance(base, str): found += own_sounds(base, seen | {name})
        child = body.get('soundevent_01', [])
        if isinstance(child, str): child = [child]
        for c in child:
            found += own_sounds(c, seen | {name}) if c in own else index['events'].get(c, {}).get('sounds', [])
        return sorted(set(found))
    for name, body in own.items():
        if isinstance(body, dict):
            matches.append({'kind': 'event', 'event': name, 'sounds': own_sounds(name, set()), 'source': 'addon'})
    for body in index['events'].values():
        matches.append({'kind': 'event', 'event': body['event'], 'sounds': body['sounds'], 'source': 'stock'})
    matches = [m for m in matches if all(w in (m['event'] + ' ' + ' '.join(m['sounds'])).casefold() for w in words)]
    def rank(m):
        text = m['event'] + ' ' + ' '.join(m['sounds'])
        exact = sum(bool(re.search(r'(?<![a-z])' + re.escape(w) + r'(?![a-z])', text, re.I)) for w in words)
        return (-exact, m['source'] != 'addon', 'diagnostics' in text or 'test' in m['event'],
                not bool(m['sounds']), len(m['event']), m['event'])
    matches.sort(key=rank)
    assets = project.game.search(query, 'vsnd', limit)
    folder = project.s.content_addon / 'sounds'
    assets += [p.relative_to(project.s.content_addon).with_suffix('.vsnd').as_posix()
               for p in folder.rglob('*.wav') if all(w in p.as_posix().casefold() for w in words)]
    matches += [{'kind': 'sound', 'vsnd': name} for name in sorted(set(assets))]
    return {'query': query, 'matches': matches[:limit], 'count': len(matches[:limit]),
            'stock_events': len(index['events']), 'index_failed': len(index['failed']), 'cache': str(PROJECT_ROOT / '.local/sound/stock_index.json')}


def _number(value, label, low, high):
    if isinstance(value, bool):
        raise SoundError(f'{label} must be a finite number in {low}..{high}')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise SoundError(f'{label} must be a finite number in {low}..{high}') from None
    if not math.isfinite(result) or not low <= result <= high:
        raise SoundError(f'{label} must be a finite number in {low}..{high}')
    return result


def read_wav(path: Path):
    """RIFF PCM 8/16/24/32 and IEEE float 32/64, mono/stereo; bounded reads."""
    if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise SoundError('input must be a local WAV file up to 128 MiB')
    if path.suffix.casefold() != '.wav':
        raise SoundError('only WAV import is supported (PCM/IEEE float); convert MP3/OGG to WAV externally')
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise SoundError('input is not a RIFF WAVE file')
    fmt = None; audio = None; pos = 12
    while pos + 8 <= len(raw):
        tag, n = struct.unpack_from('<4sI', raw, pos); pos += 8
        if pos + n > len(raw):
            raise SoundError('truncated WAV chunk')
        if tag == b'fmt ': fmt = raw[pos:pos + n]
        if tag == b'data': audio = raw[pos:pos + n]
        pos += n + n % 2
    if fmt is None or audio is None or len(fmt) < 16:
        raise SoundError('WAV needs fmt and data chunks')
    codec, channels, rate, _, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if codec == 0xfffe:
        if len(fmt) < 40 or fmt[28:40] != bytes.fromhex('00001000800000aa00389b71'):
            raise SoundError('unsupported extensible WAV subtype')
        codec = struct.unpack_from('<I', fmt, 24)[0]
    if channels not in (1, 2) or not 8000 <= rate <= 192000:
        raise SoundError('WAV must have 1 or 2 channels and sample rate 8000..192000 Hz')
    if align <= 0 or align != channels * bits // 8 or len(audio) % align:
        raise SoundError('WAV frame size does not match PCM data')
    if codec == 1 and bits == 24:
        b = np.frombuffer(audio, np.uint8).reshape(-1, 3).astype(np.int32)
        x = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16
        samples = ((x ^ 0x800000) - 0x800000) / 8388608.
    elif codec == 1 and bits in (8, 16, 32):
        dtype = {8: 'u1', 16: '<i2', 32: '<i4'}[bits]
        samples = np.frombuffer(audio, dtype).astype(np.float64)
        samples = (samples - 128) / 128 if bits == 8 else samples / (2 ** (bits - 1))
    elif codec == 3 and bits in (32, 64):
        samples = np.frombuffer(audio, '<f4' if bits == 32 else '<f8').astype(np.float64)
    else:
        raise SoundError(f'unsupported WAV codec {codec}, {bits} bits; use PCM or IEEE float')
    samples = samples.reshape(-1, channels)
    if not len(samples) or len(samples) / rate > 120 or not np.isfinite(samples).all():
        raise SoundError('WAV must contain finite samples and be 0..120 seconds long')
    if rate not in (44100, 48000):
        target = 48000; count = round(len(samples) * target / rate)
        points = np.arange(count) * rate / target
        samples = np.column_stack([np.interp(points, np.arange(len(samples)), samples[:, c]) for c in range(channels)])
        rate = target
    peak = float(np.max(np.abs(samples)))
    if peak > 0.95:
        samples *= 0.95 / peak
    return samples, rate


def _chunk(tag, payload):
    return tag + struct.pack('<I', len(payload)) + payload + (b'\0' if len(payload) % 2 else b'')


def wav_bytes(samples, rate, loop=False):
    samples = np.asarray(samples)
    if samples.ndim == 1: samples = samples[:, None]
    channels = samples.shape[1]; count = len(samples)
    fmt = struct.pack('<HHIIHH', 1, channels, rate, rate * channels * 2, channels * 2, 16)
    audio = np.round(np.clip(samples, -0.999, 0.999) * 32767).astype('<i2').tobytes()
    chunks = _chunk(b'fmt ', fmt) + _chunk(b'data', audio)
    if loop:
        # Valve interior_01 uses two cue markers; vent_01 additionally has smpl.
        # cue end is exclusive, smpl end inclusive. Include both for compatibility.
        cue = struct.pack('<I', 2) + b''.join(struct.pack('<II4sIII', i, p, b'data', 0, 0, p)
                                              for i, p in ((1, 0), (2, count)))
        smpl = struct.pack('<9I', 0, 0, round(1e9 / rate), 60, 0, 0, 0, 1, 0)
        smpl += struct.pack('<6I', 1, 0, 0, count - 1, 0, 0)
        chunks += _chunk(b'cue ', cue) + _chunk(b'smpl', smpl)
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


def metrics(samples):
    samples = np.asarray(samples)
    return {'rms': round(float(np.sqrt(np.mean(samples ** 2))), 6),
            'peak': round(float(np.max(np.abs(samples))), 6),
            'loop_seam': round(float(np.max(np.abs(samples[0] - samples[-1]))), 8)}


def synthesize(preset: str, seconds: float, params: dict | None = None):
    seconds = _number(seconds, 'seconds', 0.05, 120)
    params = dict(params or {})
    presets = ('beep', 'alarm', 'hum', 'wind', 'rain', 'drone', 'click', 'whoosh', 'rumble', 'charge', 'zap')
    if preset not in presets:
        raise SoundError(f'preset must be one of {presets}')
    allowed = {'seed', 'gain', 'frequency', 'rate', 'channels', 'loop'}
    if set(params) - allowed:
        raise SoundError(f'unknown synthesis params: {sorted(set(params) - allowed)}')
    rate = int(_number(params.get('rate', 44100), 'rate', 44100, 48000))
    if rate not in (44100, 48000): raise SoundError('rate must be 44100 or 48000')
    channels = int(_number(params.get('channels', 1), 'channels', 1, 2))
    if channels != params.get('channels', 1): raise SoundError('channels must be 1 or 2')
    loop = params.get('loop', preset in ('alarm', 'hum', 'wind', 'rain', 'drone', 'rumble'))
    if not isinstance(loop, bool): raise SoundError('loop must be boolean')
    gain = _number(params.get('gain', 0.5), 'gain', 0.001, 0.95)
    freq = _number(params.get('frequency', {'hum': 60, 'drone': 45, 'rumble': 30}.get(preset, 700)), 'frequency', 10, rate / 4)
    seed = params.get('seed', 0)
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
        raise SoundError('seed must be an integer in 0..4294967295')
    n = round(seconds * rate); t = np.arange(n) / rate; duration = n / rate
    rng = np.random.default_rng(seed)
    def tone(f):
        return np.sin(2 * np.pi * (round(f * duration) / duration if loop else f) * t)
    def noise(cutoff, slope=2):
        frequencies = np.fft.rfftfreq(n, 1 / rate)
        spectrum = np.fft.rfft(rng.normal(size=n))
        spectrum *= 1 / (1 + (frequencies / cutoff) ** slope); spectrum[0] = 0
        return np.fft.irfft(spectrum, n)
    if preset == 'beep': x = tone(freq)
    elif preset == 'alarm':
        mod = max(1, round(duration * 1.5)) / duration
        carrier = round(freq * duration) / duration if loop else freq
        x = np.sin(2 * np.pi * carrier * t + 25 * np.sin(2 * np.pi * mod * t))
    elif preset == 'hum': x = tone(freq) + 0.35 * tone(freq * 2) + 0.12 * tone(freq * 3)
    elif preset == 'wind': x = noise(650) * (0.65 + 0.25 * tone(0.5))
    elif preset == 'rain': x = noise(6000, 1) + 0.25 * noise(300)
    elif preset == 'drone': x = tone(freq) + 0.65 * tone(freq * 1.04) + 0.5 * noise(120)
    elif preset == 'click': x = rng.normal(size=n) * np.exp(-t * 80) + 0.2 * tone(1800) * np.exp(-t * 50)
    elif preset == 'whoosh': x = noise(3000) * np.sin(np.pi * np.arange(n) / (n - 1)) ** 2
    elif preset == 'charge':
        # Rising two-octave sweep that swells: a weapon or machine charging up.
        phase = 2 * np.pi * freq * duration / np.log(4) * (4 ** (t / duration) - 1)
        x = (np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.05 * noise(4000)) * (0.25 + 0.75 * t / duration)
    elif preset == 'zap':
        # Sharp electric discharge: bright noise crack plus a falling tone.
        phase = 2 * np.pi * freq * 3 * duration / np.log(6) * (1 - 6 ** (-t / duration))
        x = noise(9000, 1) * np.exp(-t * 30) + 0.8 * np.sin(phase) * np.exp(-t * 6) + 0.3 * noise(400) * np.exp(-t * 4)
    else: x = noise(90) + 0.3 * tone(freq)
    x -= x.mean()
    if loop:
        # A 5 ms raised-cosine seam has zero endpoints and nearly zero slopes.
        # Never amplify a noisy endpoint derivative into an audible correction spike.
        span = min(n // 4, max(16, int(rate * 0.005)))
        ramp = .5 - .5 * np.cos(np.linspace(0, np.pi, span))
        x[:span] *= ramp; x[-span:] *= ramp[::-1]
    else:
        fade = min(n // 4, int(rate * 0.01))
        x[:fade] *= np.linspace(0, 1, fade); x[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(np.abs(x)))
    if peak: x *= gain / peak
    samples = x[:, None] if channels == 1 else np.column_stack((x, x * .98))
    return samples, rate, loop


_WRITE_LOCK = threading.RLock()


@contextlib.contextmanager
def _asset_lock():
    import msvcrt
    lock = ensure_inside(PROJECT_ROOT / '.local/sound/write.lock', PROJECT_ROOT)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, lock.open('a+b') as fh:
        if lock.stat().st_size == 0: fh.write(b'0'); fh.flush()
        deadline = time.monotonic() + 90
        while True:
            try:
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.monotonic() >= deadline: raise SoundError('sound asset writer is busy; retry')
                time.sleep(.1)
        try: yield
        finally:
            fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _backup(project, path):
    if not path.exists(): return None
    target = ensure_inside(project.s.backups / 'sound' / dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') / path.name,
                           project.s.backups)
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
    return str(target)


def _compile(project, path):
    expected = ensure_inside(project.s.game_addon / path.relative_to(project.s.content_addon), project.s.game_addon)
    expected = expected.with_suffix('.vsnd_c' if path.suffix == '.wav' else path.suffix + '_c')
    previous = expected.stat().st_mtime_ns if expected.is_file() else -1
    job = compiler.compile_assets(project.s, project.jobs, [path], force=True, timeout=85)
    done = project.jobs.wait(job.id, 90)
    fresh = expected.is_file() and expected.stat().st_mtime_ns > previous
    if done.state != 'succeeded' or not done.result.get('ok') or done.result.get('errors') or not fresh:
        raise SoundError(f'sound compilation failed: {done.result.get("errors", [])}; log: {done.log}')
    return {'ok': True, 'job': done.id, 'log': done.log, 'compiled': str(expected)}


def _write_audio(project, name, samples, rate, loop):
    _name(name, 'sound name')
    path = ensure_inside(project.s.content_addon / 'sounds/mcp' / (name + '.wav'), project.s.content_addon / 'sounds/mcp')
    with _asset_lock():
        backup = _backup(project, path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes(samples, rate, loop))
        try:
            compiled = _compile(project, path)
            metadata = decode_events(Path(compiled['compiled']).read_bytes(), 'CTRL')['m_vSound']
            if loop != (metadata['m_nLoopStart'] >= 0):
                raise SoundError('compiled WAV loop markers do not match the request')
        except Exception:
            if backup:
                shutil.copy2(backup, path); _compile(project, path)
            else:
                destination = ensure_inside(PROJECT_ROOT / '_to_delete' / 'G_sound' / str(time.time_ns()) / path.name, PROJECT_ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(path), str(destination))
            raise
    return {**compiled, 'vsnd': f'sounds/mcp/{name}.vsnd', 'seconds': round(len(samples) / rate, 6),
            'channels': samples.shape[1], 'rate': rate, 'compiled_rate': metadata['m_nRate'],
            'loop': loop, 'loop_start': metadata['m_nLoopStart'], 'loop_end': metadata['m_nLoopEnd'], **metrics(samples),
            **({'backup': backup} if backup else {})}


def sound_import(project, name: str, file: str, loop: bool = False):
    _name(name, 'sound name')
    if not isinstance(loop, bool): raise SoundError('loop must be boolean')
    path = Path(file)
    conversion = {}
    if path.suffix.casefold() in ('.mp3', '.ogg'):
        path, conversion = _convert_audio(project, path)
    samples, rate = read_wav(path)
    return {**_write_audio(project, name, samples, rate, loop), **conversion}


_CONVERTER = '''import aud, json, sys
from pathlib import Path
source, target = sys.argv[sys.argv.index('--')+1:]
sound = aud.Sound.file(source)
rate, channels = sound.specs
seconds = sound.length / rate
if channels not in (1, 2) or not 0 < seconds <= 120:
    raise ValueError('audio must be mono/stereo and up to 120 seconds')
sound.write(target, 44100, channels, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM, 0, 4096)
print('JSON>' + json.dumps({'ok': True, 'seconds': seconds, 'channels': channels}))
'''


def _convert_audio(project, path):
    from . import blender_run
    if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise SoundError('input must be a local audio file up to 128 MiB')
    folder = ensure_inside(PROJECT_ROOT / '.local/scratch/G' / ('sound-import-' + str(time.time_ns())), PROJECT_ROOT)
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / 'convert.py'; script.write_text(_CONVERTER, encoding='utf-8')
    output = folder / 'decoded.wav'
    try:
        job = blender_run.run(project.s, project.jobs, script, [str(path.resolve()), str(output)],
                              title='decode sound with bundled Blender aud', timeout=45)
    except (blender_run.BlenderError, OSError) as exc:
        raise SoundError(f'MP3/OGG conversion needs the bundled Blender; use PCM WAV instead: {exc}') from exc
    if job.state != 'succeeded' or not job.result.get('ok') or not output.is_file():
        raise SoundError(f'cannot decode {path.suffix} audio; use PCM WAV; conversion log: {job.log}')
    return output, {'converted_from': path.suffix.lower(), 'conversion_log': job.log}


def sound_generate(project, name: str, preset: str, seconds: float = 2, params: dict | None = None):
    _name(name, 'sound name')
    samples, rate, loop = synthesize(preset, seconds, params)
    return {**_write_audio(project, name, samples, rate, loop), 'preset': preset}


REVERBS = ('reverb_2_crawlSpace', 'reverb_3_smallTunnels', 'reverb_4_smallCarpetRoom', 'reverb_5_smallRoom',
           'reverb_6_largeRoom', 'reverb_7_mediumHall', 'reverb_8_mediumCarpetRoom', 'reverb_9_mediumChamber',
           'reverb_10_warehouse', 'reverb_11_smallBright9', 'reverb_12_mediumBright', 'reverb_13_largeBright',
           'reverb_14_brightHallway', 'reverb_15_largeCarpetRoom', 'reverb_16_carpetCorridor',
           'reverb_17_smallConcrete', 'reverb_18_mediumConcrete', 'reverb_19_largeConcrete',
           'reverb_20_outsideAlley', 'reverb_21_outsideStreet', 'reverb_22_outsideOpen',
           'reverb_23_smallBathroom', 'reverb_24_largeBathroom')


def event_exists(project, name, own=None):
    own = _source_events(project)[2] if own is None else own
    return name in own or name in stock_index(project)['events']


def _sound_path(project, path):
    if not isinstance(path, str): raise SoundError('sound paths must be strings ending .vsnd')
    path = path.replace('\\', '/').removesuffix('_c')
    if not re.fullmatch(r'sounds/[A-Za-z0-9_./-]+\.vsnd', path) or '..' in path.split('/'):
        raise SoundError('sound must be a sounds/... .vsnd asset path')
    local = ensure_inside(project.s.game_addon / (path + '_c'), project.s.game_addon)
    if not local.is_file() and not project.game.exists(path + '_c'):
        raise SoundError(f'sound resource not found: {path}; import/generate and compile it first')
    return path


def sound_event(project, name: str, kind: str, sound: str | None = None, sounds: list[str] | None = None,
                volume: float = 1, pitch: float = 1, distance: float = 1500,
                interval: list[float] | None = None, radius: list[float] | None = None,
                children: list[str] | None = None, reverb: str | None = None, fade: float | None = None):
    _name(name, 'event name'); full_name = 'mcp.' + name
    volume = _number(volume, 'volume', 0, 1); pitch = _number(pitch, 'pitch', .1, 4)
    distance = _number(distance, 'distance', 1, 100000)
    bases = {'oneshot': 'amb.intermittent.atXYZ.base', 'loop': 'amb.looping.atXYZ.base',
             'ambient': 'amb.looping.stereo.base', 'random_around': 'amb.intermittent.randomAroundPlayer.base',
             'ui': 'amb.looping.stereo.base', 'soundscape': 'amb.soundscapeParent.base'}
    if kind not in bases: raise SoundError(f'kind must be one of {tuple(bases)}')
    if sound is not None and sounds is not None: raise SoundError('supply sound OR sounds, not both')
    body = {'base': bases[kind], 'volume': volume, 'pitch': pitch}
    if reverb is not None and reverb not in REVERBS:
        raise SoundError(f'unknown reverb; choose one of {REVERBS}')
    if fade is not None:
        fade = _number(fade, 'fade seconds', .05, 10)
    if kind == 'soundscape':
        if sound is not None or sounds is not None: raise SoundError('soundscape uses children, not sound/sounds')
        reverb = reverb or 'reverb_22_outsideOpen'
        if not isinstance(children, list) or not 1 <= len(children) <= 32 or not all(isinstance(c, str) for c in children):
            raise SoundError('soundscape children must be 1..32 event names')
        body.update(enable_child_events=True, soundevent_01=children, dsp_preset=reverb)
    else:
        paths = [sound] if sound is not None else sounds
        if not isinstance(paths, list) or not 1 <= len(paths) <= 32: raise SoundError('supply sound or 1..32 sounds')
        paths = [_sound_path(project, p) for p in paths]
        for asset in paths:
            local = project.s.game_addon / (asset + '_c')
            if local.is_file():
                try:
                    info = decode_events(local.read_bytes(), 'CTRL')['m_vSound']
                except (SoundError, KeyError):
                    continue  # stock/legacy container metadata is not guessed
                marked = info.get('m_nLoopStart', -1) >= 0
                if kind in ('loop', 'ambient') and not marked:
                    raise SoundError(f'{kind} requires loop markers: {asset}; import with loop=true or generate a loop')
                if kind in ('oneshot', 'ui', 'random_around') and marked:
                    raise SoundError(f'{kind} requires an unlooped sound: {asset}; import/generate with loop=false')
        body['vsnd_files_track_01'] = paths[0] if len(paths) == 1 else paths
        body.update(delay=0., volume_random_min=0., volume_random_max=0., pitch_random_min=0., pitch_random_max=0.)
        if kind in ('oneshot', 'loop'):
            body.update(use_world_position=False, position=[0., 0., 0.], position_relative_to_player=False,
                        enable_retrigger=False, use_time_volume_mapping_curve=False,
                        distance_volume_mapping_curve=[[0., 1., 0., 0., 0., 0.], [distance, 0., 0., 0., 0., 0.]])
        if kind in ('ui', 'ambient'):
            body.update(enable_retrigger=False, position_relative_to_player=True,
                        use_time_volume_mapping_curve=False, restrict_source_reverb=True, reverb_wet=0.)
        if kind == 'ui':
            body.update(mixgroup='UI', ducking_bypass=1., vsnd_pause_with_game=False,
                        occlusion_frequency_scale=0., reverb_source_wet=0.)
        if kind == 'random_around':
            interval = interval if interval is not None else [5, 15]; radius = radius if radius is not None else [200, 600]
            if not isinstance(interval, list) or not isinstance(radius, list) or len(interval) != 2 or len(radius) != 2:
                raise SoundError('interval/radius must be [min,max]')
            a, b = [_number(v, 'interval seconds', .1, 3600) for v in interval]
            r0, r1 = [_number(v, 'radius units', 0, 100000) for v in radius]
            if a > b or r0 > r1: raise SoundError('interval/radius minimum exceeds maximum')
            body.update(enable_retrigger=True, retrigger_interval_min=a, retrigger_interval_max=b,
                        randomize_position_min_radius=r0, randomize_position_max_radius=r1,
                        retrigger_radius=max(r1, distance), use_time_volume_mapping_curve=False)
    if reverb is not None:
        # These fields occur on csgo_mega in installed game_sounds_train/dust2.
        body.update(override_dsp_preset=True, dsp_preset=reverb,
                    restrict_source_reverb=False, reverb_wet=1., reverb_source_wet=1.)
    if fade is not None:
        body.update(use_time_volume_mapping_curve=True,
                    time_volume_mapping_curve=[[0., 0., 0., 0., 0., 0.], [fade, 1., 0., 0., 0., 0.]],
                    fadetime_volume_mapping_curve=[[0., 1., 0., 0., 0., 0.], [fade, 0., 0., 0., 0., 0.]])
    with _asset_lock():
        path, header, tree = _source_events(project)
        if bases[kind] not in tree: raise SoundError(f'Valve base is absent from addon: {bases[kind]}')
        if kind == 'soundscape':
            for child in children:
                if child == full_name or not event_exists(project, child, tree): raise SoundError(f'invalid child event: {child}')
            def cycle(event, seen):
                if event == full_name: return True
                if event in seen or event not in tree: return False
                nested = tree[event].get('soundevent_01', [])
                if isinstance(nested, str): nested = [nested]
                return any(cycle(c, seen | {event}) for c in nested)
            if any(cycle(c, set()) for c in children): raise SoundError('soundscape child cycle')
        before = dict(tree); tree[full_name] = body
        backup = _backup(project, path)
        path.write_text(kv3.dumps(tree, header), encoding='utf-8')
        try:
            result = _compile(project, path)
            compiled = decode_events(Path(result['compiled']).read_bytes())
            if set(before) - set(compiled) or full_name not in compiled:
                raise SoundError('compiled events lost existing names')
            written = kv3.loads(path.read_text(encoding='utf-8'))[1]
            if any(written[k] != v for k, v in before.items() if k != full_name):
                raise SoundError('event source changed existing definitions')
        except Exception:
            if backup:
                shutil.copy2(backup, path)
                _compile(project, path)
            raise
    return {**result, 'event': full_name, 'kind': kind, 'backup': backup, 'preserved_events': len(before),
            'reverb': reverb, 'fade': fade,
            'reload': 'rebuild the map and use game_screenshot; leave/re-enter soundscapes for live ambient changes'}


def add_sound(project, vmap, op, refs):
    event = op.get('event', '')
    if not event_exists(project, event): raise SoundError(f'unknown sound event: {event}; use sound_search/sound_event')
    start = op.get('start', True)
    if not isinstance(start, bool): raise SoundError('start must be boolean')
    name = op.get('name', '')
    if name: _name(name, 'entity name')
    _, _, own = _source_events(project)
    ui = own.get(event, {}).get('mixgroup') == 'UI'
    parent_name = ''
    if op.get('parent') is not None:
        parent = project.resolve(vmap, op['parent'], refs)
        parent_name = parent.get('entity_properties', {}).get('targetname', '')
        if not parent_name: raise SoundError('sound parent must be a named entity (name/id/@ref)')
    props = {'targetname': name, 'soundName': event, 'startOnSpawn': start, 'toLocalPlayer': ui,
             'sourceEntityName': parent_name, 'parentname': parent_name}
    ent = vmap.new_entity('point_soundevent', props, origin=op.get('origin', [0, 0, 0]),
                          defaults=project.catalog.defaults('point_soundevent'))
    vmap.attach(ent)
    if op.get('ref'): refs[op['ref']] = ent['nodeID']
    return {'op': 'add_sound', 'id': ent['nodeID'], 'event': event, 'name': name}


def add_soundscape(project, vmap, op, refs):
    event = op.get('event', '')
    if not event_exists(project, event): raise SoundError(f'unknown sound event: {event}')
    radius = _number(op.get('radius', 512), 'radius', 1, 100000)
    if op.get('name'): _name(op['name'], 'entity name')
    props = {'targetname': op.get('name', ''), 'radius': radius, 'enablesoundevent': True, 'soundevent': event}
    ent = vmap.new_entity('env_soundscape', props, origin=op.get('origin', [0, 0, 0]),
                          defaults=project.catalog.defaults('env_soundscape'))
    vmap.attach(ent)
    if op.get('ref'): refs[op['ref']] = ent['nodeID']
    return {'op': 'add_soundscape', 'id': ent['nodeID'], 'event': event, 'radius': radius}


def add_ambience(project, m, op, refs):
    """Single-player, disjoint sound zones. All changes are VMAP-only/atomic."""
    from . import lighting
    event = op.get('event', '')
    _, _, own = _source_events(project)
    body = own.get(event, {})
    if body.get('base') not in ('amb.looping.atXYZ.base', 'amb.looping.stereo.base') or body.get('mixgroup') == 'UI':
        raise SoundError('add_ambience requires an addon loop/ambient sound_event with fade')
    fade = _number(op.get('fade', 1.5), 'fade seconds', .05, 10)
    for field in ('time_volume_mapping_curve', 'fadetime_volume_mapping_curve'):
        curve = body.get(field, [])
        if not curve or abs(float(curve[-1][0]) - fade) > .0001:
            raise SoundError(f'Create sound_event({event.removeprefix("mcp.")}, fade={fade}) first; zone fade must match the event')
    lo, hi = lighting._vector(op.get('mins'), 'mins'), lighting._vector(op.get('maxs'), 'maxs')
    if any(lo[i] >= hi[i] for i in range(3)):
        raise SoundError('mins must be less than maxs')
    zones = [e for e in m.entities('trigger_multiple') if e.get('cs2map_ambience')]
    wait = fade
    for other in zones:
        info = json.loads(other['cs2map_ambience'])
        if all(lo[i] < info['maxs'][i] and hi[i] > info['mins'][i] for i in range(3)):
            raise SoundError('ambience zones must not overlap; split the rooms into disjoint boxes')
        wait = max(wait, info['fade'])
    name = lighting._name(m, op.get('name'), 'ambience')
    if name.startswith('mcp_amb_'):
        raise SoundError('mcp_amb_ is reserved for ambience helpers')
    if len(name) > 40:
        raise SoundError('ambience name must be at most 40 characters')
    names = {'sound': 'mcp_amb_sound_' + name, 'enter': 'mcp_amb_enter_' + name}
    if any(m.entities(targetname=n) for n in names.values()):
        raise SoundError('ambience helper name already exists')
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    trigger = m.new_entity('trigger_multiple', {'targetname': name, 'spawnflags': 1, 'wait': .1},
                           centre, defaults=project.catalog.defaults('trigger_multiple'))
    trigger.set('cs2map_ambience', 'string', json.dumps({'mins': lo, 'maxs': hi, 'fade': fade, 'event': event}))
    for piece in project._new_meshes(m, {'shape':'box', 'mins':lo, 'maxs':hi}, 'materials/tools/toolstrigger.vmat', None):
        m.attach(piece, trigger)
    speaker = m.new_entity('point_soundevent', {'targetname': names['sound'], 'soundName': event,
                           'startOnSpawn': False, 'toLocalPlayer': True, 'stopOnNew': True},
                           centre, defaults=project.catalog.defaults('point_soundevent'))
    relay = m.new_entity('logic_relay', {'targetname': names['enter'], 'FastRetrigger': True},
                         centre, defaults=project.catalog.defaults('logic_relay'))
    # Stop/cancel old zones before starting the next. Delay outlasts every fade tail.
    m.add_connection(trigger, 'OnStartTouch', 'mcp_amb_enter_*', 'CancelPending')
    # The wildcard also cancels this zone's relay; same-time events have no guaranteed order,
    # so trigger it a moment later (otherwise the cancel can land after it and the zone stays silent).
    m.add_connection(trigger, 'OnStartTouch', names['enter'], 'Trigger', delay=.05)
    m.add_connection(relay, 'OnTrigger', 'mcp_amb_sound_*', 'StopSound')
    m.add_connection(relay, 'OnTrigger', names['sound'], 'StartSound', delay=wait + .1)
    m.add_connection(trigger, 'OnEndTouch', names['enter'], 'CancelPending')
    m.add_connection(trigger, 'OnEndTouch', names['sound'], 'StopSound')
    # A subsequently authored longer fade must also protect earlier relays.
    for other in m.entities('logic_relay'):
        if other['entity_properties'].get('targetname', '').startswith('mcp_amb_enter_'):
            for connection in other['connectionsData']:
                if connection['inputName'] == 'StartSound':
                    connection['delay'] = max(connection['delay'], wait + .1)
    for e in (trigger, speaker, relay): m.attach(e)
    if op.get('ref'): refs[op['ref']] = trigger['nodeID']
    return {'op':'add_ambience', 'id':trigger['nodeID'], 'ids':[e['nodeID'] for e in (trigger,speaker,relay)],
            'name':name, 'event':event, 'fade':fade, 'transition_seconds':wait + .1, **names}


def check_sound(project, vmap):
    from .checks import problem
    _, _, own = _source_events(project); out = []
    for ent in vmap.entities():
        cls = vmap.classname(ent); props = ent['entity_properties']
        expects_event = cls in ('point_soundevent', 'snd_event_point')
        event = props.get('soundName', '') if cls in ('point_soundevent', 'snd_event_point') else ''
        if cls in ('env_soundscape', 'snd_soundscape'):
            try:
                if not math.isfinite(float(props.get('radius', 0))) or float(props.get('radius', 0)) <= 0:
                    raise ValueError()
            except (ValueError, TypeError):
                out.append(problem('error', 'soundscape_radius', ent['nodeID'], 'Soundscape radius must be > 0', 'Set a radius covering the room'))
            if props.get('enablesoundevent', '0') in ('1', 'true', True):
                expects_event = True; event = props.get('soundevent', '')
        if expects_event:
            if not event_exists(project, event, own):
                out.append(problem('error', 'sound_event_missing', ent['nodeID'], f'Unknown sound event: {event}', 'Use sound_search or create sound_event'))
    return out
