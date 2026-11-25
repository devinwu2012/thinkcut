"""Create/load only the configured new project and bind its real source ID."""
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from project_runtime import CONFIG, RUN, PROJECT
from run_mvp import resolve_mcp_runtime
from import_mvp_delivery import call, atomic_json
from source_guard import verify_source, standard_project_root

def timeline_resolution(config):
    """Displayed source size (rotation applied); older configs are probed on demand."""
    size = config.get('resolution')
    if not size:
        from probe_media import probe
        size = next(t for t in probe(Path(config['source']))['tracks'] if t['handler'] == 'vide')['display_size']
    if len(size) != 2 or any(type(x) is not int or x <= 0 for x in size):
        raise ValueError('Invalid source resolution')
    return size


def expected_library(config, run):
    """标准项目：片子自己的达芬奇项目库名（库名＝项目文件夹名，2026-10-29 用户定；规则同 projects/bin/resolve_library.py）。
    项目.json 的 resolve.database.DbName 或 project_runtime.json 的 resolve_library 可另指；不是标准项目返回 None（不检查）。"""
    if config.get('resolve_library'):
        return str(config['resolve_library'])
    root = standard_project_root(run, config.get('source'))
    if not root:
        return None
    try:
        named = json.loads((root/'项目.json').read_text(encoding='utf-8'))['resolve']['database']['DbName']
    except (OSError, ValueError, KeyError, TypeError):
        named = None
    return named if isinstance(named, str) and named else root.name


async def check_library(session, config, run):
    """deliver 动达芬奇之前：当前项目库不是这个片子的库就停下，不切库（切库会关掉用户开着的工程）。"""
    expected = expected_library(config, run)
    if not expected:
        return
    current = ((await call(session, 'project_manager', 'database_capabilities')).get('current') or {}).get('DbName')
    if current != expected:
        raise ValueError(f'达芬奇现在开着的项目库是「{current or "查不出来"}」，这个片子的项目库应是「{expected}」（库名＝项目文件夹名，工程存在项目文件夹的 02_制作/达芬奇工程）。没有改动达芬奇。'
                         '下一步（不用问用户、不用让用户退出达芬奇，2026-11-23）：运行 python3 <程序目录>/projects/bin/project.py resolve-open <项目文件夹>，'
                         '它会保存当前工程并记一笔、切到这个库（库不在列表里就借用当前开着的库）、没有工程就新建同名工程；然后重跑 deliver（deliver 平时会自动先跑它）。'
                         '老片子的库从列表里不见了：达芬奇退出后运行 project.py resolve-library <项目文件夹> 重新登记。'
                         '用户明确要用别的库时，在 审核/project_runtime.json 里写 "resolve_library": "那个库名"')


async def main():
    existing_only = CONFIG.get('existing_project_only') is True
    if not CONFIG or (not PROJECT.startswith('_mcp_') and not existing_only):
        raise ValueError('Independent managed project or explicitly bound existing project required')
    trusted_source=verify_source(CONFIG,RUN,sampled=True,audio=True)
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _,python,server=resolve_mcp_runtime()
    async with stdio_client(StdioServerParameters(command=str(python),args=[str(server)])) as (reader,writer):
        async with ClientSession(reader,writer,read_timeout_seconds=timedelta(seconds=90)) as session:
            await session.initialize()
            await check_library(session,CONFIG,RUN)
            current=await call(session,'project_manager','get_current')
            if current['name']!=PROJECT:
                empty_default=False
                if current['name']=='Untitled Project':
                    snapshot=await call(session,'project_manager','snapshot',include=['timeline','media_pool'],item_limit=1)
                    empty_default=(snapshot.get('timeline',{}).get('available') is False
                                   and snapshot.get('media_pool',{}).get('clip_count')==0)
                # A fresh empty startup project cannot SaveProject(); existing
                # work must always be saved successfully before switching away.
                if not empty_default:await call(session,'project_manager','save')
                names=(await call(session,'project_manager','list'))['projects']
                if PROJECT in names:await call(session,'project_manager','load',name=PROJECT)
                elif existing_only:raise ValueError('达芬奇当前项目库里没有工程「'+PROJECT+'」，程序不会替你新建。还没建工程：运行 python3 <程序目录>/projects/bin/project.py resolve-open <项目文件夹>（新建和项目文件夹同名的工程、帧率 29.97、缓存放项目文件夹；工程名不用问用户），再重跑 deliver；工程改过名：把 审核/project_runtime.json 的 project 改成现在的名字')
                else:await call(session,'project_manager','safe_project_create',name=PROJECT,dry_run=False)
            timelines=(await call(session,'timeline','list'))['timelines']
            if not timelines and not existing_only:
                width,height=timeline_resolution(CONFIG)
                await call(session,'project_manager','safe_set_project_settings',settings={
                    'timelineFrameRate':'29.97','timelineResolutionWidth':str(width),'timelineResolutionHeight':str(height)},restore=False,dry_run=False)
            if CONFIG.get('media_pool_item_id'):
                prop=await call(session,'media_pool_item','get_clip_property',clip_id=CONFIG['media_pool_item_id'],key='File Path')
                if prop['properties']!=CONFIG['source']:raise ValueError('Existing source binding differs')
            else:
                result=await call(session,'media_pool','safe_import_media',paths=[CONFIG['source']],dry_run=False)
                if len(result['clips'])!=1:raise ValueError('Source import did not return one clip')
                CONFIG['media_pool_item_id']=result['clips'][0]['id']
                atomic_json(RUN/'project_runtime.json',CONFIG)
            verify_source(CONFIG,RUN)
            folder=RUN/'delivery_approved_v1';folder.mkdir(exist_ok=True)
            if not (folder/'approval_manifest.json').exists():
                atomic_json(folder/'approval_manifest.json',trusted_source)
            await call(session,'project_manager','save')

if __name__=='__main__':asyncio.run(main())
