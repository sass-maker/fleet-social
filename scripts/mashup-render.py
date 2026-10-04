"""Local UI adapter for Mashup's approved-edit and finished-media contracts."""

import hashlib
import json
import os
import subprocess
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from mashup.agent import run_agent
from mashup.ingest.subtitles import parse_subtitles
from mashup_quality import choose_excerpt, scene_duration, layout_scene, write_captions, write_soundtrack

root = Path(sys.argv[1]).resolve()
request = json.loads((root / 'request.json').read_text())
job_id = root.name
progress_path = root / 'progress.json'
source_captions = {}
burn_captions = {}
selected_text = []
quality = {'recipe': request.get('renderRecipe'), 'scenes': [], 'sources': []}


def progress(stage, detail):
    progress_path.write_text(json.dumps({'stage': stage, 'detail': detail}))


def command(args, timeout=180, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} could not finish this step: {result.stderr[-1000:]}')
    return result.stdout


def operation(name, inputs, validate=False):
    result = run_agent({'schema': 'fleet.video-agent-operation.v1', 'product': 'mashup', 'operationId': f'{job_id}:{name}:{"validate" if validate else "run"}', 'operation': name, 'input': inputs, 'validateOnly': validate}, progress=lambda event: progress('Rendering', event.get('message', 'Mashup is preparing the video')))
    (root / f'{name}-{"validation" if validate else "result"}.json').write_text(json.dumps(result, indent=2))
    if result['state'] == 'failed':
        raise RuntimeError(result['error']['message'])
    return result['result']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path):
    return json.loads(command(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def product_sources():
    records = []
    for index, text in enumerate(request['scenes']):
        progress('Creating scenes', f'Preparing readable scene {index + 1} of 3')
        duration = scene_duration(text)
        size, lines = layout_scene(text)
        title = root / f'scene-{index}.txt'
        title.write_text('\n'.join(lines))
        label = root / f'label-{index}.txt'
        label.write_text(['THE PROBLEM', 'THE PRODUCT', 'THE NEXT STEP'][index])
        output = root / f'source-{index}.mp4'
        font = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
        filters = (
            "drawbox=x=105:y=285:w=870:h=7:color=0xa4bc87:t=fill,"
            f"drawtext=fontfile='{font}':textfile=label-{index}.txt:expansion=none:fontcolor=0xa4bc87:fontsize=30:x=105:y=368,"
            f"drawtext=fontfile='{font}':textfile=scene-{index}.txt:expansion=none:fontcolor=0xf4f1e7:fontsize={size}:line_spacing=22:x=105:y=630:"
            "alpha='min(1,t/0.28)'"
        )
        command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                 '-f', 'lavfi', '-i', f'color=c=0x1e352c:s=1080x1920:r=30:d={duration}',
                 '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
                 '-vf', filters, '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast',
                 '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(output)], cwd=root)
        quality['scenes'].append({'durationSeconds': duration, 'lines': lines, 'fontSize': size, 'safeTextWidth': 850})
        records.append((output, text, duration, f'urn:fleet:owner-product-story:{job_id}:{index}',
                        'Creator-owned original text and procedural motion', 'urn:rights:owner-created'))
    return records


source_details = {}


def read_cues(path):
    return [{'start': cue.start, 'end': cue.end, 'text': cue.text} for cue in parse_subtitles(path)]


def public_captions(metadata, index, url):
    manual = metadata.get('subtitles') or {}
    automatic = metadata.get('automatic_captions') or {}
    language = None
    for available in [manual, automatic]:
        language = 'en' if 'en' in available else next((lang for lang in available if lang.startswith('en-')), None)
        if language:
            break
    if not language:
        return []
    try:
        command(['yt-dlp', '--ignore-config', '--no-playlist', '--skip-download', '--write-subs',
                 '--write-auto-subs', '--sub-langs', language, '--sub-format', 'vtt',
                 '--socket-timeout', '15', '--retries', '1', '--no-overwrites',
                 '--output', str(root / f'captions-{index}.%(ext)s'), '--', url], timeout=60)
        paths = list(root.glob(f'captions-{index}.*.vtt'))
        return read_cues(paths[0]) if paths else []
    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
        return []


def source_excerpt(cues, duration, index):
    try:
        return choose_excerpt(cues, request['brief'], duration, selected_text)
    except ValueError as error:
        raise ValueError(f'Source {index + 1}: {error}') from error


def linked_sources():
    records = []
    version = command(['yt-dlp', '--version']).strip()
    for index, url in enumerate(request['links']):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port or parsed.hostname not in {'www.youtube.com', 'youtube.com', 'youtu.be', 'vimeo.com', 'www.vimeo.com', 'archive.org', 'mashup.highsignal.app'} or parsed.query and parsed.hostname == 'mashup.highsignal.app':
            raise ValueError('Use a public YouTube, Vimeo, Archive.org, or Mashup proof link. Private sources are not supported.')
        known = {'https://mashup.highsignal.app/media/operators-final.mp4': ('operators', 'Ideas are not enough'), 'https://mashup.highsignal.app/media/survive-technology-final.mp4': ('survive', 'How companies survive technology')}.get(url)
        output = root / f'source-{index}.mp4'
        if known:
            name, title = known
            cache = root.parents[1]
            receipt = json.loads((cache / f'{name}.receipt.json').read_text())
            source = cache / f'{name}.mp4'
            if digest(source) != receipt['output']['video']['sha256']:
                raise ValueError('The cached source does not match its Mashup receipt.')
            cues = read_cues(cache / f'{name}.vtt')
            start, end, selected = source_excerpt(cues, receipt['output']['durationSeconds'], index)
            progress('Choosing excerpts', f'Keeping caption boundaries in prepared source {index + 1}')
            command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-ss', str(start),
                     '-i', str(source), '-t', str(end - start), '-c:v', 'libx264', '-preset', 'fast',
                     '-crf', '18', '-c:a', 'aac', str(output)])
            source_details[url] = receipt['sources']
            license_name, license_url = 'Creative Commons Attribution 3.0', 'https://creativecommons.org/licenses/by/3.0/'
        else:
            progress('Inspecting sources', f'Inspecting source {index + 1} of {len(request["links"])}')
            metadata = json.loads(command(['yt-dlp', '--ignore-config', '--no-playlist', '--simulate',
                                          '--dump-single-json', '--socket-timeout', '15', '--retries', '1', '--', url], timeout=90))
            if metadata.get('_type') in {'playlist', 'multi_video'} or metadata.get('is_live'):
                raise ValueError('Paste individual finished video links, not a playlist or livestream.')
            cues = public_captions(metadata, index, url)
            start, end, selected = source_excerpt(cues, float(metadata.get('duration') or 10), index)
            title = metadata.get('title') or f'Source {index + 1}'
            license_name = metadata.get('license') or 'Creator-authorized reuse confirmed by owner'
            license_url = 'urn:rights:owner-confirmed'
            (root / f'acquisition-{index}.json').write_text(json.dumps({
                'backend': 'yt-dlp', 'version': version, 'sourceUrl': url, 'title': title,
                'uploader': metadata.get('uploader'), 'license': metadata.get('license'), 'rights': 'owner-confirmed',
                'startSeconds': start, 'endSeconds': end,
                'selection': 'complete caption sentences and topic relevance' if selected else 'first ten seconds; captions unavailable'}, indent=2))
            progress('Acquiring sources', f'Downloading the selected excerpt from source {index + 1}')
            command(['yt-dlp', '--ignore-config', '--no-playlist', '--no-overwrites', '--max-filesize', '95M',
                     '--socket-timeout', '15', '--retries', '1', '--format',
                     'bv*[height<=1080]+ba/b[height<=1080]', '--merge-output-format', 'mp4',
                     '--download-sections', f'*{start}-{end}',
                     '--force-keyframes-at-cuts', '--output', str(root / f'source-{index}.%(ext)s'), '--', url], timeout=180)
            videos = [p for p in root.glob(f'source-{index}.*') if p.suffix in {'.mp4', '.webm', '.mkv'}]
            if len(videos) != 1 or videos[0].stat().st_size > 95_000_000:
                raise ValueError('Source exceeds the local demo media limit or could not be acquired.')
            output = videos[0]
        duration = min(end - start, float(probe(output)['format']['duration']))
        rebased = [{'start': max(0, cue['start'] - start), 'end': min(duration, cue['end'] - start), 'text': cue['text']} for cue in selected]
        source_captions[str(output)] = rebased
        burn_captions[str(output)] = [] if known else rebased
        spoken = ' '.join(cue['text'] for cue in selected)
        selected_text.append(spoken)
        quality['sources'].append({'sourceUrl': url, 'startSeconds': start, 'endSeconds': start + duration,
                                   'selection': 'complete caption sentences and topic relevance' if selected else 'first ten seconds; captions unavailable',
                                   'spokenText': spoken, 'existingBurnedCaptions': bool(known)})
        records.append((output, title, duration, url, license_name, license_url))
    return records


try:
    progress('Planning', 'Preparing an editable story and preserving source provenance')
    records = product_sources() if request['mode'] == 'product' else linked_sources()
    now = datetime.now(timezone.utc).isoformat()
    clips = []
    sources = []
    timeline_captions, timeline_burn = [], []
    cursor = 0
    for index, (path, text, duration, url, license_name, license_url) in enumerate(records):
        source_id = f'{job_id}-source-{index}'
        clips.append({'index': index, 'segment_id': source_id, 'source_id': source_id, 'source_title': request['title'] if request['mode'] == 'product' else text, 'source_path': str(path), 'start': 0, 'end': duration, 'render_start': 0, 'render_end': duration, 'text': text, 'summary': text, 'role': ['setup', 'development', 'closer'][min(index, 2)], 'energy': 0.5, 'edited': True})
        sources.append({'id': source_id, 'title': clips[-1]['source_title'], 'sourceUrl': url, 'license': license_name, 'licenseUrl': license_url, 'sha256': digest(path), 'path': str(path), 'upstreamSources': source_details.get(url, [])})
        for collection, timeline in [(source_captions, timeline_captions), (burn_captions, timeline_burn)]:
            timeline.extend({**cue, 'start': cue['start'] + cursor, 'end': cue['end'] + cursor} for cue in collection.get(str(path), []))
        cursor += duration
    soundtrack = root / 'original-soundtrack.wav'
    if request['mode'] == 'product':
        write_soundtrack(soundtrack, cursor)
        sources.append({'id': f'{job_id}-soundtrack', 'title': 'Original procedural soundtrack',
                        'sourceUrl': f'urn:fleet:owner-procedural-audio:{job_id}',
                        'license': 'Creator-owned original procedural audio', 'licenseUrl': 'urn:rights:owner-created',
                        'sha256': digest(soundtrack), 'path': str(soundtrack)})
    edl = {'version': 1, 'strategy': 'operator-story', 'prompt': request['brief'], 'target_duration': sum(c['end'] for c in clips), 'generated_at': now, 'clips': clips, 'terms': {key: 0 for key in ['relevance', 'context_completeness', 'non_repetition', 'progression', 'escalation', 'callback', 'duration_fit', 'source_diversity']}, 'rationale': ['Owner-approved source order; keyword/caption heuristics are not semantic model scores.']}
    preformatted = request['mode'] == 'links' and len(source_details) == len(records)
    source_heading = request['mode'] != 'product' and not preformatted
    watermark = not preformatted
    edit = {'schema': 'fleet.podcast-edit.v1', 'id': job_id, 'revision': 1, 'createdAt': now, 'approval': {'status': 'approved', 'approvedAt': now, 'approvedBy': request['approvedBy']}, 'sources': sources, 'presentation': {'sourceHeading': source_heading, 'watermark': watermark, 'watermarkText': 'MASHUP', 'subtitles': 'none'}, 'visualCues': [], 'editorial': edl}
    edit_path = root / 'approved-edit.json'
    edit_path.write_text(json.dumps(edit, indent=2))
    intermediate = root / 'mashup-render.mp4'
    video = root / 'final.mp4'
    inputs = {'podcastEditPath': str(edit_path), 'output': str(intermediate), 'workdir': str(root / 'work'), 'profile': 'social', 'subtitles': 'none', 'sourceLabel': source_heading, 'watermark': watermark, 'watermarkText': 'MASHUP'}
    operation('render', inputs, True)
    progress('Rendering', 'Mashup is cutting, fitting and joining the approved scenes')
    operation('render', inputs)
    if request['mode'] == 'product':
        progress('Finishing', 'Adding the original quiet soundtrack')
        command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-i', str(intermediate),
                 '-i', str(soundtrack), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
                 '-b:a', '192k', '-t', str(cursor), '-movflags', '+faststart', str(video)])
    elif timeline_burn:
        progress('Finishing', "Adding the source's timed captions")
        write_captions(root / 'burn-captions.srt', timeline_burn)
        style = "FontName=Arial,FontSize=10,PrimaryColour=&H00FFFFFF,BackColour=&HA0000000,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=48"
        command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-i', str(intermediate),
                 '-vf', f"subtitles=filename=burn-captions.srt:force_style='{style}'",
                 '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'copy',
                 '-movflags', '+faststart', str(video)], cwd=root)
    else:
        shutil.copyfile(intermediate, video)
    info = probe(video)
    stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
    receipt_inputs = {'podcastEditPath': str(edit_path), 'videoPath': str(video), 'durationSeconds': float(info['format']['duration']), 'width': stream['width'], 'height': stream['height'], 'output': str(root / 'media-receipt.json')}
    if timeline_captions:
        write_captions(root / 'captions.srt', timeline_captions)
        receipt_inputs['captionsPath'] = str(root / 'captions.srt')
    operation('media-receipt', receipt_inputs, True)
    result = operation('media-receipt', receipt_inputs)
    command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-n', '-ss', '1', '-i', str(video), '-frames:v', '1', '-vf', 'scale=360:-1', str(root / 'poster.jpg')])
    (root / 'quality.json').write_text(json.dumps(quality, indent=2))
    (root / 'complete.json').write_text(json.dumps(result))
    progress('Ready for review', 'The finished MP4 and verified Mashup receipt are ready')
except Exception as error:
    (root / 'error-details.log').write_text(str(error))
    message = str(error) if isinstance(error, ValueError) else 'The local renderer could not finish this video. Check the source links or local Mashup setup, then try again.'
    progress('Needs attention', message)
    (root / 'failed.json').write_text(json.dumps({'error': message}))
    sys.exit(1)
