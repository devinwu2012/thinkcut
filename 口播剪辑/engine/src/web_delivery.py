"""Local review-to-Resolve job and verified, versioned delivery files."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def name_stem(manifest):
    """The video name that prefixes every SRT/DRT this program writes (e.g. C3745)."""
    return manifest['timeline_name'].split('_主线_')[0].split('_紧剪_')[0]


def publish(package):
    from prepare_mvp_delivery import verify_package
    package = Path(package)
    manifest, _ = verify_package(package)
    readback = json.loads((package / 'mcp_readback.json').read_text())
    if (readback.get('passed') is not True or readback.get('resolve_render_started') is not False
            or readback.get('timeline') != manifest['timeline_name']
            or readback.get('delivery_manifest_sha256') != sha(package / 'delivery_manifest.json')):
        raise ValueError('达芬奇读回验收未通过，不能标记保存成功')
    names = [manifest['srt_name'], manifest['timeline_name'] + '_Resolve回存.drt',
             manifest['project'] + '_' + manifest['signature'] + '.drp']
    output = package.parents[2] / '交付'
    history = output / '历史'
    history.mkdir(parents=True, exist_ok=True)
    payloads = {}
    for name in names:
        data = (package / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != readback['artifact_sha256'][name]:
            raise ValueError('交付文件摘要不匹配：' + name)
        payloads[name] = data
    # 交付/ keeps only the newest SRT/DRT/DRP. Only files this program wrote are moved to 交付/历史/;
    # the user's own files (renders, notes) stay, and nothing in 历史/ is overwritten.
    ours = (manifest['project'] + '_', name_stem(manifest) + '_', 'mcp_readback_', 'delivery_manifest_')
    for old in output.iterdir():
        if old.is_file() and old.name not in names and old.name.startswith(ours) and old.suffix in ('.srt', '.drt', '.drp', '.json'):
            target = history / old.name
            if target.exists():
                target = history / (old.stem + '_' + uuid.uuid4().hex[:6] + old.suffix)
            os.replace(old, target)
    for name, data in payloads.items():
        temporary = output / ('.' + name + '.' + uuid.uuid4().hex)
        try:
            temporary.write_bytes(data)
            os.replace(temporary, output / name)
        finally:
            temporary.unlink(missing_ok=True)
    atomic_json(history / ('mcp_readback_' + manifest['signature'] + '.json'), readback)
    atomic_json(history / ('delivery_manifest_' + manifest['signature'] + '.json'), manifest)
    result = {'status': 'complete', 'running': False, 'project': manifest['project'],
              'timeline': manifest['timeline_name'], 'frames': manifest['frames'],
              'seconds': manifest['frames'] * manifest['fps'][1] / manifest['fps'][0],
              'segments': manifest['segments'], 'subtitle_cues': manifest['subtitle_cues'],
              'state_sha256': manifest['input_sha256']['review_v2_state.json'],
              'package': str(package), 'output': str(output),
              'completed_at': datetime.now(timezone.utc).isoformat()}
    atomic_json(package.parents[1] / 'latest_delivery.json', result)
    return result


# 网页“保存到达芬奇”整个过程的时限。正常约 1 分钟（CHANGELOG：C3745 含生成 B-roll 片段的交付实测 50 秒），
# 其中每次达芬奇接口调用自己 90 秒就超时；30 分钟还没完基本就是卡住了（多半是达芬奇弹了对话框）。
DELIVER_TIMEOUT_SECONDS = 30 * 60


def run_group(command, timeout, **kwargs):
    """跑交付命令并等它结束；超时就把它连同它起的子进程一起结束（只结束它自己的话，下面真正写达芬奇的子进程还会接着跑）。"""
    process = subprocess.Popen(command, start_new_session=True, **kwargs)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sign in (signal.SIGTERM, signal.SIGKILL):   # 先让整组正常退出，5 秒后还在的强制结束
            try:
                os.killpg(process.pid, sign)
            except OSError:
                break
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        process.wait()
        raise
    return process


class DeliveryJob:
    def __init__(self, run, command, lock):
        self.run, self.command, self.lock = Path(run), command, lock
        self.info = {'status': 'idle', 'running': False}

    def status(self):
        with self.lock:
            result = dict(self.info)
            latest = self.run / 'latest_delivery.json'
            if result['status'] == 'idle' and latest.exists():
                result = json.loads(latest.read_text())
            if result.get('state_sha256'):
                result['current'] = result['state_sha256'] == sha(self.run / 'review_v2_state.json')
            return result

    def start(self, expected_revision, state_revision):
        with self.lock:
            if self.info['running']:
                return dict(self.info)
            from paragraph_editor import StateConflict
            if expected_revision != state_revision:
                raise StateConflict('审核状态已更新，请核对最新文字后再保存到达芬奇。')
            if not self.command.is_file():
                raise ValueError('本页缺少共享交付入口，请用新版共享能力重开审片台（edit_project.py serve）。')
            self.info = {'status': 'running', 'running': True,
                         'message': '正在生成字幕、导入达芬奇并读回核对…',
                         'started_at': datetime.now(timezone.utc).isoformat()}
            thread = threading.Thread(target=self._work, daemon=True)
            thread.start()
            return dict(self.info)

    def _work(self):
        log = self.run / ('resolve_sync_' + uuid.uuid4().hex + '.log')
        try:
            with log.open('w') as stream:
                try:
                    process = run_group([sys.executable, str(self.command), 'deliver',
                                         '--project-dir', str(self.run.parent)], DELIVER_TIMEOUT_SECONDS,
                                        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
                                        stdout=stream, stderr=subprocess.STDOUT)
                except subprocess.TimeoutExpired:
                    raise RuntimeError(f'保存到达芬奇超过 {DELIVER_TIMEOUT_SECONDS // 60} 分钟还没完成，已停止这次保存，本次未标记保存成功。'
                                       '先看达芬奇有没有弹出对话框或卡住，处理好（必要时保存并重启达芬奇）后再保存一次；'
                                       '达芬奇里可能留下一条没做完的时间线，先别动它，再保存一次还不行就把这段话交给 AI 检查') from None
            if process.returncode:
                details = log.read_text()
                if '达芬奇正被另一个命令占用' in details:
                    raise RuntimeError('另一个命令（B-roll、花字或别的剪辑步骤）正在写达芬奇，这次没有保存。等它做完再点一次“保存到达芬奇”')
                if 'Timed out while waiting for response' in details:
                    raise RuntimeError('达芬奇接口 90 秒未响应。请保存并重启达芬奇后重试；本次未标记保存成功')
                if 'Another delivery operation is running' in details:
                    raise RuntimeError('本机有另一条视频正在同步，请等它完成后再试')
                if '出错了：' in details:
                    explained = details[details.rindex('出错了：'):].strip().splitlines()
                    raise RuntimeError(' '.join(line for line in explained if not line.startswith('完整日志'))[:400])
                lines = details.strip().splitlines()
                raise RuntimeError((lines[-1] if lines else '交付程序退出')[:300])
            result = json.loads((self.run / 'latest_delivery.json').read_text())
            if result.get('status') != 'complete' or result.get('state_sha256') != sha(self.run / 'review_v2_state.json'):
                raise RuntimeError('最新选区与交付结果不一致，请重新同步')
            with self.lock:
                self.info = result
        except Exception as error:
            with self.lock:
                self.info = {'status': 'failed', 'running': False, 'error': str(error), 'log': str(log)}
