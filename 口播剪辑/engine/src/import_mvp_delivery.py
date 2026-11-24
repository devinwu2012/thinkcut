"""Import one frozen text-review package through the Resolve MCP server; never render."""
import asyncio
import json
import locale
import os
import subprocess
import sys
import re
import uuid
from datetime import timedelta
from pathlib import Path
from prepare_mvp_delivery import exclusive_lock, verify_package, digest
from run_mvp import resolve_mcp_runtime, node_runtime

from project_runtime import ROOT, RUN, NAME, CONFIG


def sha(path):
    return digest(path)


class ResolveCallError(RuntimeError):
    """达芬奇接口（MCP 调用）本身报错；和“读回来的内容对不上”分开处理。"""


async def call(session, tool, action, **params):
    result = await session.call_tool(tool, {'action': action, 'params': params})
    payload = json.loads(next(c.text for c in result.content if c.type == 'text'))
    if result.isError or payload.get('error') or payload.get('success') is False:
        raise ResolveCallError(f'{tool}.{action}: {payload.get("error", payload)}')
    return payload


PROJECT_FORMAT_KEYS = ('timelineResolutionWidth', 'timelineResolutionHeight', 'timelineFrameRate')


def same_setting(a, b):
    try:
        return abs(float(a) - float(b)) < 0.001
    except (ValueError, TypeError):
        return a == b


async def frame_rate_preflight(session, manifest, existing_timeline=None):
    """Refuse an unsafe creation before adding any clips; never change project settings."""
    if existing_timeline:
        await call(session, 'timeline', 'set_current', name=existing_timeline)
    tool = 'timeline' if existing_timeline else 'project_settings'
    value = (await call(session, tool, 'get_setting', name='timelineFrameRate'))['settings']
    expected = manifest['fps'][0] / manifest['fps'][1]
    if not same_setting(value, expected):
        location = '已有目标时间线' if existing_timeline else '项目默认'
        raise ValueError(f'{location}帧率为 {value}，原片为 {expected:.5f} fps。'
                         '本交付入口不能安全地在建线时指定独立帧率，已在建线/追加片段前停止；'
                         '请先建立与原片相同帧率的自定义准备时间线，并以交付包的 base_timeline_name 命名，再重试；不要修改项目设置。')


async def import_drt_preserving_project(session, path, report_path):
    """Every DRT import in this entry point must pass through this guard."""
    async def snapshot():
        result = {}
        for key in PROJECT_FORMAT_KEYS:
            value = (await call(session, 'project_settings', 'get_setting', name=key))['settings']
            if value is None or value == '' or float(value) <= 0:
                raise ValueError('无法读取项目格式：' + key + '；已停止 DRT 导入')
            result[key] = value
        return result
    before = await snapshot()
    report = {'before': before, 'changed': {}, 'restored': False}
    atomic_json(report_path, report)
    try:
        result = await call(session, 'timeline', 'import_timeline_checked', path=str(path), require_temp_path=False)
    finally:
        try:
            after = await snapshot()
            report['after_import'] = after
            report['changed'] = {k: {'before': before[k], 'after': after[k]}
                                 for k in before if not same_setting(before[k], after[k])}
            failures = []
            for key in report['changed']:
                try:
                    await call(session, 'project_settings', 'set_setting', name=key, value=str(before[key]))
                except Exception as error:
                    failures.append(str(error))
            restored = await snapshot()
            report['after_restore'] = restored
            if any(not same_setting(before[k], restored[k]) for k in before):
                raise ValueError('；'.join(failures) or '恢复后读回仍不一致')
            report['restored'] = True
            report['message'] = ('DRT 导入改变项目格式，已恢复并读回核对' if report['changed']
                                 else 'DRT 导入未改变项目宽高和帧率')
        except Exception as error:
            report['error'] = str(error)
            raise ValueError('DRT 导入后的项目格式恢复或核验失败：' + str(error) + '；请按项目格式恢复记录检查，勿继续交付') from error
        finally:
            atomic_json(report_path, report)
    return result, report


def resolve_script_environment():
    """Use the same scripting paths as the configured MCP, without starting it."""
    api = '/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'
    env = {'PYTHONPATH': api + '/Modules', 'RESOLVE_SCRIPT_API': api,
           'RESOLVE_SCRIPT_LIB': '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so'}
    config = Path(os.environ['THINKCUT_MCP_CONFIG']) if os.environ.get('THINKCUT_MCP_CONFIG') else None   # 2026-10-29：默认不读 Mac 上的 Codex 配置
    if config and config.is_file():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        configured = tomllib.loads(config.read_text()).get('mcp_servers', {}).get('davinci-resolve', {}).get('env', {})
        env.update({key: configured[key] for key in env if configured.get(key)})
        if not configured.get('PYTHONPATH'):
            env['PYTHONPATH'] = env['RESOLVE_SCRIPT_API'] + '/Modules'
    return env


def direct_timeline(project_name, final=None, need_timeline=True):
    """当前工程的当前时间线。need_timeline=False 只确认连得上、工程对得上：新建的空工程还没有时间线时返回 None。"""
    original_locale = locale.setlocale(locale.LC_ALL)
    try:
        env = resolve_script_environment()
        os.environ.update(env)
        for path in reversed(env['PYTHONPATH'].split(os.pathsep)):
            if path and path not in sys.path:
                sys.path.insert(0, path)
        import DaVinciResolveScript
        resolve = DaVinciResolveScript.scriptapp('Resolve')
        if not resolve:
            raise RuntimeError('达芬奇没有响应')
        project = resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline() if project else None
    except Exception as exc:
        raise RuntimeError('无法直接连接达芬奇脚本接口') from exc
    finally:
        locale.setlocale(locale.LC_ALL, original_locale)
    if not project:
        raise RuntimeError('达芬奇已连上，但现在没有打开任何工程：先打开工程「' + project_name + '」再保存')
    if project.GetName() != project_name:
        raise ValueError('达芬奇当前工程不匹配：应为「' + project_name + '」')
    if not timeline:
        if not need_timeline:
            return None
        raise RuntimeError('达芬奇已连上，但工程「' + project_name + '」里没有当前时间线（新建的空工程，或时间线都没打开）：'
                           '在达芬奇里双击打开一条时间线后再试')
    if final is not None and timeline.GetName() != final:
        raise ValueError('达芬奇当前时间线不匹配：应为「' + final + '」')
    return timeline


async def validate_timeline_inventory(session, before, allowed, final):
    names = {row['name'] for row in (await call(session, 'timeline', 'list'))['timelines']}
    extra = names - before - allowed
    if extra:
        raise ValueError('达芬奇里多出了时间线：' + '、'.join(sorted(extra)))
    clips = (await call(session, 'folder', 'get_clips', path='Master'))['clips']
    if final not in names or not any(c['name'] == final for c in clips):
        raise ValueError('交付主线不在 Master：' + final)


def validate_items(probe, segments, spec, source_id, source_path):
    if probe['start_frame'] != spec.get('origin', 108000):
        raise ValueError('Timeline origin differs from subtitle origin')
    if probe['end_frame'] - probe['start_frame'] != segments[-1]['tl_out']:
        raise ValueError('Timeline frame count differs from the approved selection')
    for kind in ('video', 'audio'):
        items = primary_items(probe, kind)
        if len(items) != len(segments):
            raise ValueError(f'{kind} clip count differs')
        for i, (item, seg) in enumerate(zip(items, segments)):
            if (item['start'], item['end'], item['duration'], item.get('media_status')) != (
                probe['start_frame'] + seg['tl_in'], probe['start_frame'] + seg['tl_out'], seg['src_out'] - seg['src_in'], 'Online'):
                raise ValueError(f'{kind} item {i} record/media mismatch')
            if item.get('media_pool_item_id') != source_id or item.get('file_path') != str(source_path):
                raise ValueError(f'{kind} item {i} uses the wrong source media')
    subtitles = primary_items(probe, 'subtitle')
    if len(subtitles) != len(spec['subtitles']):
        raise ValueError('Subtitle count differs')
    for i, (item, target) in enumerate(zip(subtitles, spec['subtitles'])):
        if (item['start'], item['duration'], item['name'].replace('\u2028', '\n')) != (
            target['startFrame'], target['durationFrames'], target['text']):
            raise ValueError(f'Subtitle {i} differs')


def primary_items(probe, kind):
    tracks = probe.get('tracks', {}).get(kind, {}).get('tracks', [])
    if not tracks or any(track['items'] for track in tracks[1:]):
        raise ValueError(f'{kind} must have exactly one populated first track')
    return tracks[0]['items']


def expected_subtitle_spec(package, manifest):
    """Read acceptance targets from the frozen SRT, never from mutable DRT output."""
    fps_n, fps_d = manifest['fps']
    def frame(tc):
        match = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', tc.strip())
        if not match:
            raise ValueError('Invalid SRT timestamp')
        h, m, s, ms = map(int, match.groups())
        if m >= 60 or s >= 60:
            raise ValueError('Invalid SRT timestamp')
        elapsed = ((h * 60 + m) * 60 + s) * 1000 + ms
        return (2 * elapsed * fps_n + fps_d * 1000) // (2 * fps_d * 1000)
    subtitles = []
    previous_end = 0
    for index, block in enumerate(re.split(r'\n\s*\n', (package / manifest['srt_name']).read_text().strip())):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index + 1) or lines[1].count('-->') != 1:
            raise ValueError('Invalid SRT cue')
        start, end = (frame(t) for t in lines[1].split('-->'))
        if start < previous_end or end <= start or end > manifest['frames']:
            raise ValueError('SRT cue overlaps or exceeds the approved timeline')
        subtitles.append({'startFrame': start + 108000, 'durationFrames': end - start, 'text': '\n'.join(lines[2:]).strip()})
        previous_end = end
    if len(subtitles) != manifest['subtitle_cues']:
        raise ValueError('SRT cue count differs from the approved package')
    return {'timeline_name': manifest['timeline_name'], 'origin': 108000, 'subtitles': subtitles}


HISTORY = '历史版本'
# Timelines this program created; user-renamed or user-made timelines never match.
AI_TIMELINE = re.compile(re.escape(NAME) + r'_(紧剪_[0-9a-f]{12}_(含字幕|无字幕)|无字幕_[0-9a-f]{12}|主线_\d{4}-\d{4}_[0-9a-f]{4}(_未完成_[0-9a-f]{6})?)')


MAIN_TIMELINE = re.compile(re.escape(NAME) + r'_(紧剪_[0-9a-f]{12}_含字幕|主线_\d{4}-\d{4}_[0-9a-f]{4})')


def resolution_problem(size, config):
    """Timeline size must equal the source's displayed size, except in a user's own bound project."""
    if not config.get('resolution') or config.get('existing_project_only') is True or list(size) == config['resolution']:
        return None
    return '达芬奇时间线分辨率 %s 与原片 %s 不一致' % ('×'.join(map(str, size)), '×'.join(map(str, config['resolution'])))


def ever_passed(package):
    """True if any saved readback of this package (current or earlier attempts) passed verification."""
    for path in package.glob('mcp_readback*.json'):
        try:
            if json.loads(path.read_text()).get('passed') is True:
                return True
        except (OSError, ValueError):
            continue
    return False


def package_for(name):
    for path in sorted((RUN / 'mvp_deliveries').glob('*/delivery_manifest.json')):
        manifest = json.loads(path.read_text())
        if manifest.get('timeline_name') == name:
            return path.parent, manifest
    return None, None


async def edited_main_lines(session, keep):
    """Earlier AI main lines in Master that no longer match their frozen package.

    A hand-finished main line must never be silently replaced and archived.
    """
    names = {row['name'] for row in (await call(session, 'timeline', 'list'))['timelines']}
    edited = []
    for clip in (await call(session, 'folder', 'get_clips', path='Master'))['clips']:
        name = clip['name']
        if name == keep or name not in names or not MAIN_TIMELINE.fullmatch(name):
            continue
        try:
            package, manifest = package_for(name)
            if manifest is None:
                raise ValueError('No frozen package for ' + name)
            mapping = json.loads((package / 'trial_keep_segments.json').read_text())
            await call(session, 'timeline', 'set_current', name=name)
            probe = await call(session, 'timeline', 'probe_timeline_structure',
                               track_types=['video', 'audio', 'subtitle'], include_markers=False)
            validate_items(probe, mapping['segments'], expected_subtitle_spec(package, manifest),
                           manifest['source']['media_pool_item_id'], Path(manifest['source']['path']))
        except ResolveCallError as error:
            # 接口抖动不等于手工修改：没查完就如实说没查完，不让用户白白去改名
            raise RuntimeError(f'检查旧主线「{name}」有没有被手工改过时，达芬奇接口出错，没有查完；这次没有改动达芬奇。'
                               '稍等几秒再保存一次；还不行就在达芬奇里保存、退出重开后再试。'
                               f'（这条主线确实手工改过的话，先给它改个名字再保存。接口原话：{error}）') from error
        except (ValueError,OSError,RuntimeError,StopIteration):
            edited.append(name)
    return edited


async def archive_old_timelines(session, keep):
    """Move earlier AI timelines from the Master bin into 历史版本; nothing is deleted.

    Bring the kept main line back from any descendant bin, including Archive.
    """
    names = {row['name'] for row in (await call(session, 'timeline', 'list'))['timelines']}
    clips = (await call(session, 'folder', 'get_clips', path='Master'))['clips']
    move = [c['id'] for c in clips if c['name'] != keep and c['name'] in names and AI_TIMELINE.fullmatch(c['name'])]
    subfolders = [s['name'] for s in (await call(session, 'folder', 'get_subfolders', path='Master'))['subfolders']]
    history = 'Master/' + HISTORY
    back = []
    async def find_main(path):
        back.extend(c['id'] for c in (await call(session, 'folder', 'get_clips', path=path))['clips'] if c['name'] == keep and c['name'] in names)
        for sub in (await call(session, 'folder', 'get_subfolders', path=path))['subfolders']:
            await find_main(path + '/' + sub['name'])
    for name in subfolders:
        await find_main('Master/' + name)
    if HISTORY not in subfolders and move:
        await call(session, 'media_pool', 'add_subfolder', parent_path='Master', name=HISTORY)
    if move:
        await call(session, 'media_pool', 'move_clips', clip_ids=move, target_path=history)
    if back:
        await call(session, 'media_pool', 'move_clips', clip_ids=back, target_path='Master')
    await call(session, 'media_pool', 'set_current_folder', path='Master')
    return len(move)


def atomic_json(path, data):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.partial')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def export_atomically(session, target, tool, action, **params):
    temporary = target.with_name(target.stem + '.partial-' + uuid.uuid4().hex + target.suffix)
    try:
        await call(session, tool, action, path=str(temporary), **params)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError('Resolve did not write a complete export: ' + target.name)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def main(package):
    package = Path(package).resolve()
    # Record failed verification only inside an existing managed package.
    # A typo or an arbitrary directory must never gain delivery-status files.
    manifest_path = package / 'delivery_manifest.json'
    if (package.parent != (RUN / 'mvp_deliveries').resolve() or
            re.fullmatch(re.escape(NAME)+r'_[0-9a-f]{12}', package.name) is None or
            not package.is_dir() or not manifest_path.is_file() or manifest_path.is_symlink()):
        raise ValueError('Expected an existing managed delivery package of this project')
    # All packages of a video target the same Resolve project; lock the project, not one package.
    with exclusive_lock(RUN / 'mvp_deliveries' / '.resolve-import.lock'):
        attempt = uuid.uuid4().hex
        readback_path = package / 'mcp_readback.json'
        if readback_path.is_symlink() or (readback_path.exists() and not readback_path.is_file()):
            raise ValueError('Delivery readback must be a regular file')
        if readback_path.exists():
            (package / ('mcp_readback.previous-' + attempt + '.json')).write_bytes(readback_path.read_bytes())
        atomic_json(readback_path, {'passed': False, 'status': 'running', 'attempt': attempt})
        try:
            manifest, mapping = verify_package(package)
            await import_package(package, manifest, mapping, attempt)
        except BaseException as exc:
            atomic_json(readback_path, {'passed': False, 'status': 'failed', 'attempt': attempt,
                                      'error': str(exc) or type(exc).__name__, 'resolve_render_started': False})
            raise


async def import_package(package, manifest, mapping, attempt):
    # 只确认连得上、当前工程是这个片子的；空工程（还没有时间线）也放行，下面会建线
    direct_timeline(manifest['project'], need_timeline=False)
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    source = Path(manifest['source']['path'])
    segments = mapping['segments']
    spec = expected_subtitle_spec(package, manifest)
    mcp_root, server_python, server = resolve_mcp_runtime()
    node, node_env = node_runtime(mcp_root)
    params = StdioServerParameters(command=str(server_python), args=[str(server)])
    base = manifest['base_timeline_name']
    final = manifest['timeline_name']
    item_id = manifest['source']['media_pool_item_id']
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=90)) as session:
            await session.initialize()
            project = await call(session, 'project_manager', 'get_current')
            if project['name'] != manifest['project']:
                raise ValueError(f'Open project {manifest["project"]} first; found {project["name"]}')
            await call(session, 'media_pool', 'set_current_folder', path='Master')
            before_names = {row['name'] for row in (await call(session, 'timeline', 'list'))['timelines']}
            allowed_names = {final, base}
            if (await call(session, 'render', 'is_rendering'))['rendering']:
                raise ValueError('Resolve is rendering; retry after it finishes')
            prop = await call(session, 'media_pool_item', 'get_clip_property', clip_id=item_id, key='File Path')
            if prop['properties'] != str(source):
                raise ValueError('Media-pool source does not match package')
            # A main line left by an attempt of this package that never passed is a half-built leftover:
            # set it aside (archived later) and rebuild. Once any attempt passed, the main line is a real
            # delivery the user may have hand-finished, so it always goes through the edit check below.
            if not ever_passed(package) and final in before_names:
                await call(session, 'timeline', 'set_current', name=final)
                incomplete = final + '_未完成_' + attempt[:6]
                await call(session, 'timeline', 'set_name', name=incomplete)
                allowed_names.add(incomplete)
            edited = await edited_main_lines(session, None)
            if final in edited:
                raise ValueError('达芬奇里的主线「' + final + '」已有手工修改，网页里没有新的改动，不需要再保存；继续在达芬奇里精修即可。')
            if edited:
                raise ValueError('达芬奇里的主线「' + '」「'.join(edited) + '」有手工修改。再次保存会生成一条不含这些修改的新主线，'
                                 '旧的会被收进“历史版本”。要保留手工修改，请先在达芬奇里把这条时间线改个名字'
                                 '（改名后程序不会再移动它），然后再保存。')
            names = {row['name'] for row in (await call(session, 'timeline', 'list'))['timelines']}
            new_final = final not in names
            await frame_rate_preflight(session, manifest, final if not new_final else base if base in names else None)
            project_format_report = None
            if new_final:
                await call(session, 'project_manager', 'save')
                before = package / '导入前项目备份.drp'
                if not before.exists():
                    await export_atomically(session, before, 'project_manager', 'export_project', name=manifest['project'])
                if base not in names:
                    first = segments[0]
                    await call(session, 'media_pool', 'create_timeline_from_clips', name=base, if_exists='fail', clip_infos=[{
                        'media_pool_item_id': item_id, 'start_frame': first['src_in'], 'end_frame': first['src_out'], 'record_frame': first['tl_in']}])
                else:
                    await call(session, 'timeline', 'set_current', name=base)
                base_probe = await call(session, 'timeline', 'probe_timeline_structure', track_types=['video', 'audio'], include_markers=False)
                video = primary_items(base_probe, 'video')
                audio = primary_items(base_probe, 'audio')
                if len(audio) != len(video):
                    raise ValueError('Existing base timeline has mismatched audio/video counts')
                count = len(video)
                if count > len(segments):
                    raise ValueError('Existing base timeline has too many clips')
                for kind, existing in (('video', video), ('audio', audio)):
                    for i, item in enumerate(existing):
                        s = segments[i]
                        if (item['start'], item['end'], item.get('media_pool_item_id'), item.get('file_path')) != (
                                base_probe['start_frame']+s['tl_in'], base_probe['start_frame']+s['tl_out'], item_id, str(source)):
                            raise ValueError(f'Existing base {kind} timeline differs at clip {i}')
                        offset = await call(session, 'timeline_item', 'get_left_offset', track_type=kind, track_index=1, item_index=i)
                        if offset['offset'] != s['src_in']:
                            raise ValueError(f'Existing base {kind} source offset differs at clip {i}')
                if count < len(segments):
                    infos = [{'media_pool_item_id': item_id, 'start_frame': s['src_in'], 'end_frame': s['src_out'],
                              'record_frame': s['tl_in'], 'track_index': 1} for s in segments[count:]]
                    added = await call(session, 'media_pool', 'append_to_timeline', clip_infos=infos)
                    if added['count'] != len(infos):
                        raise ValueError('MCP appended fewer clips than expected')
                base_probe = await call(session, 'timeline', 'probe_timeline_structure', track_types=['video', 'audio'], include_markers=False)
                for kind in ('video', 'audio'):
                    base_items = primary_items(base_probe, kind)
                    if len(base_items) != len(segments):
                        raise ValueError(f'Base {kind} clip count differs')
                    for i, (item, seg) in enumerate(zip(base_items, segments)):
                        if (item['start'], item['end'], item.get('media_pool_item_id'), item.get('file_path')) != (
                            base_probe['start_frame'] + seg['tl_in'], base_probe['start_frame'] + seg['tl_out'], item_id, str(source)):
                            raise ValueError(f'Base {kind} item {i} differs from approved source')
                await call(session, 'project_manager', 'save')
                raw_drt = package / (base + '.drt')
                await export_atomically(session, raw_drt, 'timeline', 'export_timeline_checked', format='DRT', require_temp_path=False)
                final_drt = package / (final + '.drt')
                final_temp = package / (final + '.partial-' + uuid.uuid4().hex + '.drt')
                try:
                    subprocess.run([node, str(ROOT/'src'/'author_subtitle_drt.cjs'), str(package), raw_drt.name,
                                    final_temp.name, manifest['srt_name'], final], check=True, env=node_env, capture_output=True, text=True)
                    if json.loads((package / 'subtitle_drt_spec.json').read_text()) != spec:
                        raise ValueError('Authored subtitle spec differs from the frozen SRT')
                    os.replace(final_temp, final_drt)
                finally:
                    final_temp.unlink(missing_ok=True)
                result, project_format_report = await import_drt_preserving_project(session, final_drt, package / '项目格式恢复记录.json')
                if result['name'] != final or result['media']['offline']:
                    raise ValueError('Imported timeline name or media coverage differs')
                await call(session, 'project_manager', 'save')
            else:
                await call(session, 'timeline', 'set_current', name=final)
            await call(session, 'project_manager', 'save')
            await call(session, 'project_manager', 'close')
            await call(session, 'project_manager', 'load', name=manifest['project'])
            await call(session, 'timeline', 'set_current', name=final)
            probe = await call(session, 'timeline', 'probe_timeline_structure', track_types=['video', 'audio', 'subtitle'], include_markers=False)
            validate_items(probe, segments, spec, item_id, source)
            for kind in ('video', 'audio'):
                for i, s in enumerate(segments):
                    offset = await call(session, 'timeline_item', 'get_left_offset', track_type=kind, track_index=1, item_index=i)
                    if offset['offset'] != s['src_in']:
                        raise ValueError(f'{kind} source offset differs at clip {i}')
            audio_ids = [item['timeline_item_id'] for item in probe['tracks']['audio']['tracks'][0]['items']]
            for i, audio_id in enumerate(audio_ids):
                links = await call(session, 'timeline_item', 'get_linked_items', track_type='video', track_index=1, item_index=i)
                if not any(item['id'] == audio_id for item in links['items']):
                    raise ValueError(f'Video/audio link missing at clip {i}')
            fps = await call(session, 'timeline', 'get_setting', name='timelineFrameRate')
            if abs(float(fps['settings']) - manifest['fps'][0] / manifest['fps'][1]) > 0.001:
                raise ValueError('Timeline frame rate differs from approved source')
            if CONFIG.get('resolution') and CONFIG.get('existing_project_only') is not True:
                size = [int(float((await call(session, 'timeline', 'get_setting', name=key))['settings']))
                        for key in ('timelineResolutionWidth', 'timelineResolutionHeight')]
                problem = resolution_problem(size, CONFIG)
                if problem:
                    raise ValueError(problem)
            for kind in ('video', 'audio', 'subtitle'):
                if not (await call(session, 'timeline', 'get_track_enabled', track_type=kind, index=1))['enabled']:
                    raise ValueError(kind + ' delivery track is disabled')
            readback = {'passed': True, 'status': 'complete', 'attempt': attempt, 'project_reopened': True, 'timeline': final, 'timeline_id': probe['id'],
                        'frames': manifest['frames'], 'video_clips': len(segments), 'audio_clips': len(segments),
                        'linked_pairs': len(segments), 'subtitle_cues': len(spec['subtitles']), 'source_offset_checks': len(segments)*2,
                        'resolve_render_started': False}
            if project_format_report is not None:
                readback['project_format_import'] = project_format_report
            readback['archived_timelines'] = await archive_old_timelines(session, final)
            final_export = package / (final + '_Resolve回存.drt')
            await export_atomically(session, final_export, 'timeline', 'export_timeline_checked', format='DRT', require_temp_path=False)
            await call(session, 'project_manager', 'save')
            archive = package / (manifest['project'] + '_' + manifest['signature'] + '.drp')
            await export_atomically(session, archive, 'project_manager', 'export_project', name=manifest['project'])
            await call(session, 'resolve_control', 'open_page', page='edit')
            await call(session, 'timeline_markers', 'set_current_timecode', timecode='01:00:00:00')
            if (await call(session, 'render', 'is_rendering'))['rendering']:
                raise ValueError('Unexpected Resolve render state')
            await validate_timeline_inventory(session, before_names, allowed_names, final)
            readback['resolve_drt_sha256'] = sha(final_export)
            readback['project_archive_sha256'] = sha(archive)
            verify_package(package)
            readback['delivery_manifest_sha256'] = sha(package / 'delivery_manifest.json')
            readback['artifact_sha256'] = {name: sha(package / name) for name in
                                         (manifest['srt_name'], 'subtitle_cues.json', final_export.name, archive.name)}
            atomic_json(package / 'mcp_readback.json', readback)
            print(json.dumps({'package': str(package), **readback}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
