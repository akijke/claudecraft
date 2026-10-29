"""Pictures and videos from neural nets, viewed inside the game (/cc draw, /cc video, /cc media).

Generation goes straight to the provider's HTTP API with urllib (no extra packages):
  images: OpenAI Images (gpt-image-1) or Google Gemini (gemini-2.5-flash-image)
  video:  OpenAI Videos (Sora) or Google Veo (long-running operation, polled)
The provider is picked by the keys that exist: secrets.json (`/claude key media`), then the
environment (OPENAI_API_KEY, GEMINI_API_KEY / GOOGLE_API_KEY), then Geminicraft's own key.
Keys are never logged, echoed into events or put into URLs.

Results land in <config>/media/ (png / mp4 + a JSON sidecar with prompt, provider, time) and
in media/index.json — the gallery the mod reads directly (MediaStore.java). Files written by
any agent (Claude, Codex, Gemini) and mentioned in its answer are added to the same gallery
with a `media` event, so the chat shows them as thumbnails.

Minecraft cannot decode mp4: with ffmpeg on PATH the bridge saves a poster frame and, on
request, a short JPG frame sequence (media/frames/<id>/, frames.json last) that MediaScreen
plays silently. Without ffmpeg the video opens in the system player.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from backends.connectors import load_secrets, store_secrets
from backends.util import save_json

IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
VIDEO_EXT = ('.mp4', '.webm', '.mov')
MAX_DOWNLOAD = 300 * 1024 * 1024
INDEX_LIMIT = 500
KEY_FIELDS = {'OPENAI_API_KEY': ('openai_api_key', 'platform.openai.com/api-keys — картинки gpt-image-1, видео Sora'),
              'GEMINI_API_KEY': ('gemini_api_key', 'aistudio.google.com/apikey — картинки Gemini, видео Veo')}
# Defaults; bridge-config.json → "media": {"openai_image_model": …} overrides them.
DEFAULTS = {'openai_image_model': 'gpt-image-1', 'gemini_image_model': 'gemini-2.5-flash-image',
            'openai_video_model': 'sora-2', 'veo_model': 'veo-3.1-fast-generate-preview',
            'image_size': '1024x1024', 'video_size': '1280x720', 'video_seconds': 4}
# Rough price per result (USD), only for the warning line; providers change prices.
COST = {('image', 'openai'): '≈ $0.04', ('image', 'gemini'): '≈ $0.04',
        ('video', 'openai'): '≈ $0.10 за секунду', ('video', 'gemini'): '≈ $0.15 за секунду'}
OPENAI = 'https://api.openai.com/v1'
GOOGLE = 'https://generativelanguage.googleapis.com/v1beta'
MENTION = re.compile(r'((?:[A-Za-z]:)?[\w.\-\\/~]*[\w\-]\.(?:png|jpe?g|gif|webp|mp4|webm|mov))(?![\w.])', re.I)


class Cancelled(Exception):
    pass


def urllib_http(method, url, headers=None, body=None, timeout=120):
    """(status, headers, body bytes); HTTP errors are returned, not raised."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
            return response.status, dict(response.headers), response.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(200_000)
    except urllib.error.URLError as e:
        raise ValueError(f'Сеть недоступна: {e.reason}')


def _error(provider, status, body):
    try:
        data = json.loads(body.decode('utf-8', 'replace'))
        message = (data.get('error') or {}).get('message') if isinstance(data.get('error'), dict) else data.get('error')
    except (ValueError, AttributeError):
        message = None
    return ValueError(f'{provider}: HTTP {status} — {str(message or body[:200].decode("utf-8", "replace")).strip()[:300]}')


def _json(provider, response):
    status, _, body = response
    if status >= 400:
        raise _error(provider, status, body)
    try:
        return json.loads(body.decode('utf-8'))
    except ValueError:
        raise ValueError(f'{provider}: непонятный ответ')


def multipart(fields):
    boundary = 'claudecraft' + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n')
    return ''.join(parts + [f'--{boundary}--\r\n']).encode('utf-8'), 'multipart/form-data; boundary=' + boundary


def slug(text, limit=40):
    words = re.sub(r'[^\w\- ]+', '', text.lower()).split()
    return '-'.join(words)[:limit].strip('-') or 'media'


class Media:
    def __init__(self, bridge, http=None, sleep=None):
        self.bridge = bridge
        self.dir = bridge.directory / 'media'
        self.http = http or urllib_http
        self.sleep = sleep or time.sleep
        self.ffmpeg = shutil.which('ffmpeg')
        self.jobs = {}       # job id -> {'kind', 'prompt', 'provider', 'cancel': Event, 'chat'}
        self.pending = {}    # chat id -> paths an agent's tools mentioned (checked when the turn ends)
        self.lock = threading.Lock()

    # ---- keys ---------------------------------------------------------------
    def config(self, name):
        value = (self.bridge.config.get('media') or {}).get(name) if isinstance(self.bridge.config.get('media'), dict) else None
        return value if value not in (None, '') else DEFAULTS[name]

    def key(self, provider):
        secrets = load_secrets(self.bridge.directory)
        if provider == 'openai':
            key = secrets.get('openai_api_key') or os.environ.get('OPENAI_API_KEY')
            if not key:  # Codex logged in with an API key keeps it here
                try:
                    auth = json.loads((Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'auth.json')
                                      .read_text(encoding='utf-8'))
                    key = auth.get('OPENAI_API_KEY') if isinstance(auth, dict) else None
                except (OSError, ValueError):
                    key = None
            return key or None
        key = secrets.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if not key:  # Geminicraft next to us (config/geminicraft/secrets.json)
            key = load_secrets(self.bridge.directory.parent / ('gemini' + 'craft')).get('gemini_api_key')
        return key or None

    def key_fields(self):
        secrets = load_secrets(self.bridge.directory)
        return [{'var': var, 'hint': hint, 'required': False, 'set': bool(secrets.get(name))}
                for var, (name, hint) in KEY_FIELDS.items()]

    def set_keys(self, values):
        data = load_secrets(self.bridge.directory)
        for var, value in (values or {}).items():
            if var not in KEY_FIELDS:
                raise ValueError(f'media: неизвестный параметр {var}')
            value = str(value or '').strip()
            if len(value) > 400 or '\n' in value:
                raise ValueError(f'{var}: слишком длинное или многострочное значение')
            if value:
                data[KEY_FIELDS[var][0]] = value
        store_secrets(self.bridge.directory, data)

    def providers(self):
        return [p for p in ('openai', 'gemini') if self.key(p)]

    def pick(self, provider=None):
        if provider:
            if provider not in ('openai', 'gemini'):
                raise ValueError('Провайдер: openai или gemini')
            if not self.key(provider):
                raise ValueError(f'Нет ключа {provider}: /claude key media')
            return provider
        have = self.providers()
        if not have:
            raise ValueError('Нет ключа для генерации: /claude key media (OpenAI или Gemini)')
        return have[0]

    # ---- providers (blocking, run in a worker thread) ------------------------
    def openai_image(self, job):
        key = self.key('openai')
        body = {'model': self.config('openai_image_model'), 'prompt': job['prompt'], 'n': 1,
                'size': job.get('size') or self.config('image_size')}
        data = _json('openai', self.http('POST', OPENAI + '/images/generations',
                                         {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                                         json.dumps(body).encode('utf-8'), 300))
        row = (data.get('data') or [{}])[0]
        if row.get('b64_json'):
            return base64.b64decode(row['b64_json']), '.png', body['model']
        if row.get('url', '').startswith('https://'):
            status, _, raw = self.http('GET', row['url'], {}, None, 300)
            if status < 400:
                return raw, '.png', body['model']
        raise ValueError('openai: в ответе нет картинки')

    def gemini_image(self, job):
        model = self.config('gemini_image_model')
        body = {'contents': [{'parts': [{'text': job['prompt']}]}],
                'generationConfig': {'responseModalities': ['TEXT', 'IMAGE']}}
        data = _json('gemini', self.http('POST', f'{GOOGLE}/models/{model}:generateContent',
                                         {'x-goog-api-key': self.key('gemini'), 'Content-Type': 'application/json'},
                                         json.dumps(body).encode('utf-8'), 300))
        for candidate in data.get('candidates') or []:
            for part in (candidate.get('content') or {}).get('parts') or []:
                inline = part.get('inlineData') or part.get('inline_data')
                if inline and inline.get('data'):
                    ext = '.jpg' if 'jpeg' in inline.get('mimeType', inline.get('mime_type', '')) else '.png'
                    return base64.b64decode(inline['data']), ext, model
        reason = ((data.get('promptFeedback') or {}).get('blockReason') or
                  next((c.get('finishReason') for c in data.get('candidates') or []), None))
        raise ValueError('gemini: картинки нет в ответе' + (f' ({reason})' if reason else ''))

    def wait(self, job, check, progress, interval=5, limit=20 * 60):
        """Polls check() until it returns a value; cancel and a time limit stop it."""
        started = time.monotonic()
        while True:
            if job['cancel'].is_set():
                raise Cancelled()
            result = check()
            if result is not None:
                return result
            if time.monotonic() - started > limit:
                raise ValueError('Генерация дольше 20 минут — остановлена')
            self.sleep(interval)

    def openai_video(self, job, progress):
        key = self.key('openai')
        model = self.config('openai_video_model')
        body, ctype = multipart({'model': model, 'prompt': job['prompt'],
                                 'size': job.get('size') or self.config('video_size'),
                                 'seconds': str(job.get('seconds') or self.config('video_seconds'))})
        auth = {'Authorization': 'Bearer ' + key}
        video = _json('openai', self.http('POST', OPENAI + '/videos', {**auth, 'Content-Type': ctype}, body, 120))
        vid = str(video.get('id') or '')
        if not re.fullmatch(r'[\w\-]+', vid):
            raise ValueError('openai: нет id видео')
        job['remote'] = vid
        seen = {}

        def check():
            state = _json('openai', self.http('GET', f'{OPENAI}/videos/{vid}', auth, None, 60))
            status = state.get('status')
            if status == 'completed':
                return state
            if status in ('failed', 'cancelled'):
                raise ValueError('openai: ' + str((state.get('error') or {}).get('message') or status))
            pct = state.get('progress')
            if pct != seen.get('pct'):
                seen['pct'] = pct
                progress(f'Видео: {status or "в очереди"} {pct or 0}%', percent=pct or 0)
            return None
        self.wait(job, check, progress)
        status, _, raw = self.http('GET', f'{OPENAI}/videos/{vid}/content', auth, None, 600)
        if status >= 400:
            raise _error('openai', status, raw)
        return raw, '.mp4', model

    def veo_video(self, job, progress):
        key = self.key('gemini')
        model = self.config('veo_model')
        auth = {'x-goog-api-key': key}
        body = {'instances': [{'prompt': job['prompt']}], 'parameters': {'aspectRatio': '16:9'}}
        op = _json('gemini', self.http('POST', f'{GOOGLE}/models/{model}:predictLongRunning',
                                       {**auth, 'Content-Type': 'application/json'}, json.dumps(body).encode('utf-8'), 120))
        name = str(op.get('name') or '')
        if not re.fullmatch(r'[\w\-./]+', name) or '..' in name:
            raise ValueError('gemini: нет операции Veo')
        job['remote'] = name
        ticks = [0]

        def check():
            state = _json('gemini', self.http('GET', f'{GOOGLE}/{name}', auth, None, 60))
            if state.get('error'):
                raise ValueError('gemini: ' + str(state['error'].get('message') if isinstance(state['error'], dict) else state['error']))
            if state.get('done'):
                return state
            ticks[0] += 1
            progress(f'Видео: Veo работает… {ticks[0] * 10} с', percent=None)
            return None
        state = self.wait(job, check, progress, interval=10)
        response = state.get('response') or {}
        samples = ((response.get('generateVideoResponse') or {}).get('generatedSamples')
                   or response.get('generatedVideos') or [])
        uri = ((samples[0] if samples else {}).get('video') or {}).get('uri', '')
        if not uri.startswith('https://'):
            raise ValueError('gemini: Veo не вернул видео (возможно, запрос отклонён фильтром)')
        status, _, raw = self.http('GET', uri, auth, None, 600)
        if status >= 400:
            raise _error('gemini', status, raw)
        return raw, '.mp4', model

    def produce(self, job, progress):
        if job['kind'] == 'image':
            return (self.openai_image if job['provider'] == 'openai' else self.gemini_image)(job)
        return (self.openai_video if job['provider'] == 'openai' else self.veo_video)(job, progress)

    # ---- gallery ------------------------------------------------------------
    def index(self):
        try:
            rows = json.loads((self.dir / 'index.json').read_text(encoding='utf-8'))
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []

    def find(self, ident):
        ident = (ident or '').strip()
        rows = self.index()
        if not ident or ident == 'last':
            if rows:
                return rows[-1]
        for row in reversed(rows):
            if row.get('id') == ident or (len(ident) >= 4 and row.get('id', '').startswith(ident)):
                return row
        raise ValueError(f'Нет такого файла в галерее: {ident}')

    def find_path(self, path):
        for row in reversed(self.index()):
            if row.get('path') == str(Path(path).resolve()):
                return row
        raise ValueError(f'Нет такого файла в галерее: {path}')

    def _add(self, entry):
        with self.lock:
            rows = [r for r in self.index() if r.get('path') != entry['path']] + [entry]
            self.dir.mkdir(parents=True, exist_ok=True)
            save_json(self.dir / 'index.json', rows[-INDEX_LIMIT:])
        return entry

    def save(self, data, ext, kind, prompt, provider, model, chat=None):
        if len(data) > MAX_DOWNLOAD:
            raise ValueError('Файл больше 300 МБ — не сохранён')
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        path = self.dir / f'{stamp}-{slug(prompt)}{ext}'
        n = 1
        while path.exists():
            n += 1
            path = self.dir / f'{stamp}-{slug(prompt)}-{n}{ext}'
        path.write_bytes(data)
        entry = {'id': uuid.uuid4().hex[:10], 'kind': kind, 'path': str(path), 'prompt': prompt,
                 'provider': provider, 'model': model, 'created': time.time(), 'source': 'generated',
                 'chat': chat, 'size': len(data), 'poster': None}
        if kind == 'video':
            entry['poster'] = self.poster(path)
        save_json(path.with_name(path.name + '.json'), entry)
        return self._add(entry)

    def register(self, path, chat=None, source='agent'):
        path = Path(path).resolve()
        kind = 'video' if path.suffix.lower() in VIDEO_EXT else 'image'
        for row in self.index():
            if row.get('path') == str(path) and row.get('mtime') == path.stat().st_mtime:
                return None  # already shown
        entry = {'id': uuid.uuid4().hex[:10], 'kind': kind, 'path': str(path), 'prompt': '',
                 'provider': source, 'model': None, 'created': time.time(), 'source': source, 'chat': chat,
                 'size': path.stat().st_size, 'mtime': path.stat().st_mtime, 'poster': None}
        if kind == 'video':
            entry['poster'] = self.poster(path)
        return self._add(entry)

    def poster(self, video):
        if not self.ffmpeg:
            return None
        out = self.dir / 'posters' / (Path(video).stem + '.png')
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([self.ffmpeg, '-y', '-loglevel', 'error', '-i', str(video), '-frames:v', '1',
                            '-vf', "scale='min(960,iw)':-2", str(out)], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return str(out) if out.is_file() else None

    def frames(self, entry, fps=12, width=480, limit=300):
        """Silent preview for MediaScreen: JPG frames + frames.json (written last)."""
        if not self.ffmpeg:
            raise ValueError('Для просмотра видео в игре нужен ffmpeg в PATH — или открой его в плеере')
        out = self.dir / 'frames' / entry['id']
        if (out / 'frames.json').is_file():
            return json.loads((out / 'frames.json').read_text(encoding='utf-8'))
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        result = subprocess.run([self.ffmpeg, '-y', '-loglevel', 'error', '-i', entry['path'], '-an',
                                 '-vf', f"fps={fps},scale='min({width},iw)':-2", '-frames:v', str(limit), '-q:v', '4',
                                 str(out / '%04d.jpg')], capture_output=True, text=True, timeout=300)
        count = len(list(out.glob('*.jpg')))
        if result.returncode != 0 or not count:
            raise ValueError('ffmpeg не смог разобрать видео: ' + (result.stderr or '').strip()[:200])
        info = {'count': count, 'fps': fps}
        save_json(out / 'frames.json', info)
        return info

    def open(self, path, folder=False):
        path = Path(path)
        if not path.exists():
            raise ValueError(f'Нет такого файла: {path}')
        if sys.platform == 'win32':
            if folder:
                subprocess.Popen(['explorer', '/select,', str(path)])
            else:
                os.startfile(str(path))  # noqa: S606 - the user's own click
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path.parent if folder else path)])

    # ---- files written by agents -------------------------------------------
    def candidates(self, chat, text):
        folder = Path(self.bridge.chat_cwd(chat)) if chat.mode == 'cc' else self.bridge.workspace
        found = []
        for raw in MENTION.findall(text or '')[:10]:
            path = Path(raw).expanduser()
            found.append(path if path.is_absolute() else folder / path)
        return found

    def observe(self, chat, event):
        """Called for every event: agent answers / tool lines that name a fresh media file."""
        kind = event.get('kind')
        if kind in ('text', 'text_part', 'tool'):
            paths = self.candidates(chat, event.get('text', ''))
            if kind == 'tool':  # the Write tool runs after this line: look again when the turn ends
                self.pending.setdefault(chat.id, set()).update(paths[:5])
                return []
        elif kind in ('done', 'error'):
            paths = list(self.pending.pop(chat.id, ()))
        else:
            return []
        added = []
        for path in paths:
            try:
                if not path.is_file() or path.stat().st_size > MAX_DOWNLOAD:
                    continue
                if time.time() - path.stat().st_mtime > 3600:  # an old file someone only talks about
                    continue
                entry = self.register(path, chat.id)
            except OSError:
                continue
            if entry:
                added.append(entry)
        return added

    # ---- jobs -----------------------------------------------------------------
    def describe(self, entry):
        name = Path(entry['path']).name
        return ('Видео' if entry['kind'] == 'video' else 'Картинка') + f': {name}'

    def media_event(self, chat, entry):
        return self.bridge.emit(chat, self.describe(entry), kind='media', **{
            k: entry.get(k) for k in ('id', 'path', 'poster', 'prompt', 'provider', 'model', 'source', 'size')},
            media=entry['kind'], ffmpeg=bool(self.ffmpeg))

    def start(self, chat, kind, prompt, provider=None, **options):
        import asyncio
        prompt = (prompt or '').strip()
        if not prompt:
            raise ValueError('Опиши, что нарисовать: /cc draw <описание>' if kind == 'image'
                             else 'Опиши видео: /cc video <описание>')
        if len(prompt) > 4000:
            raise ValueError('Описание длиннее 4000 символов')
        provider = self.pick(provider)
        job_id = uuid.uuid4().hex[:8]
        job = {'id': job_id, 'kind': kind, 'prompt': prompt, 'provider': provider, 'chat': chat.id,
               'cancel': threading.Event(), **options}
        self.jobs[job_id] = job
        loop = asyncio.get_running_loop()

        last = [0.0]

        def progress(text, **extra):
            now = time.monotonic()
            if now - last[0] < 15:  # a line every 15 s at most: polling must not flood the chat
                return
            last[0] = now
            loop.call_soon_threadsafe(lambda: self.bridge.emit(chat, text, kind='media_progress', job=job_id, **extra))

        async def run():
            try:
                data, ext, model = await asyncio.to_thread(self.produce, job, progress)
                if job['cancel'].is_set():
                    raise Cancelled()
                entry = await asyncio.to_thread(self.save, data, ext, kind, prompt, provider, model, chat.id)
                self.media_event(chat, entry)
            except Cancelled:
                self.bridge.emit(chat, 'Генерация отменена (уже начатую провайдер может посчитать)', kind='status')
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.bridge.emit(chat, f'Генерация не удалась: {e}', kind='error')
            finally:
                self.jobs.pop(job_id, None)
                self.bridge.emit(chat, '', kind='media_progress', job=job_id, finished=True)
        job['task'] = asyncio.create_task(run())
        what = 'картинку' if kind == 'image' else 'видео'
        self.bridge.emit(chat, f'Генерирую {what} через {provider} ({COST[(kind, provider)]}): {prompt[:200]}',
                         kind='media_progress', job=job_id, percent=0)
        return job

    def cancel(self, job_id=''):
        jobs = [j for j in self.jobs.values() if not job_id or j['id'].startswith(job_id)]
        for job in jobs:
            job['cancel'].set()
            if job['kind'] == 'video' and job['provider'] == 'openai' and job.get('remote'):
                pass  # the API has no "stop"; polling simply ends
        return len(jobs)


def parse_gen(text):
    """'[image|video] [--provider p] [--size WxH] [--seconds N] prompt' → (kind, provider, options, prompt)."""
    words = text.strip().split(' ')
    kind, provider, options = 'image', None, {}
    if words and words[0] in ('image', 'img', 'picture', 'video'):
        kind = 'video' if words.pop(0) == 'video' else 'image'
    while len(words) >= 2 and words[0].startswith('--'):
        flag, value = words.pop(0)[2:], words.pop(0)
        if flag in ('provider', 'p'):
            provider = value
        elif flag == 'size' and re.fullmatch(r'\d{3,4}x\d{3,4}', value):
            options['size'] = value
        elif flag == 'seconds' and value.isdigit() and 1 <= int(value) <= 20:
            options['seconds'] = int(value)
        else:
            raise ValueError(f'Неизвестный параметр --{flag}')
    return kind, provider, options, ' '.join(words).strip()
