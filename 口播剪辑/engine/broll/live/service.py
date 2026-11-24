"""Offline-testable orchestration of T1/T2 and the SDK boundary."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import platform
import re
import shutil
import subprocess
import sys
from engine.broll import index, media
from . import ledger as books, reconcile, candidates, matching, placement
from .timeline import build_model
from .resolve_adapter import Adapter, connect, database_type, media_pool
from .workspace import infer_work_dir, work_project_dir, register
from .writer import execute, snapshot_hash
from .report import placement_report
from .arrange import segment_identity, default_profile


def read_json(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default


def preflight(config, timeline=None, connector=connect, runner=subprocess.run):
    if not sys.flags.utf8_mode or sys.getfilesystemencoding().lower()!='utf-8':
        raise ValueError('Python未开启UTF-8，请用 python3 -X utf8 运行')
    binary = shutil.which(config.get('ffmpeg','ffmpeg'))
    if not binary:
        raise ValueError('找不到ffmpeg，请完成本机接入')
    def run(args):
        result=runner(args,capture_output=True,text=True,timeout=20)
        if result.returncode:
            raise RuntimeError('环境检查命令失败：'+args[0])
        return result.stdout+result.stderr
    run([binary,'-version'])
    arches=run(['/usr/bin/file','-L',binary])
    machine=platform.machine();expected='arm64' if machine in ('arm64','aarch64') else 'x86_64'
    if expected not in arches:
        raise ValueError('ffmpeg架构与本机不符：'+expected)
    if 'zscale' not in run([binary,'-filters']):
        raise ValueError('ffmpeg缺少zscale，请安装支持zscale的版本')
    # 没配 nas_mount 时按本仓库在哪儿推：仓库上一级是程序目录，再上一级是 NAS 共享根
    nas=Path(config.get('nas_mount',Path(__file__).resolve().parents[3].parent.parent))
    if not nas.is_dir() or (str(nas).startswith('/Volumes/') and not nas.is_mount()):
        raise ValueError('NAS未挂载：'+str(nas))
    # 台账索引（审核线靠它找回主线台账）必须在 NAS 内；学习库目录不再要求（学偏好的功能 2026-11-21 下线）
    value=config.get('ledger_index')
    if not value or not Path(value).absolute().is_relative_to(nas.absolute()):
        raise ValueError('请在配置中指定NAS内的 ledger_index')
    r,p,t=connector(timeline)
    typ=database_type(r)
    if typ=='未知':raise ValueError('数据库类型无法确认，停止操作')
    return Adapter(r,p,t),{'ffmpeg':binary,'架构':expected,'NAS':str(nas),'数据库':typ}


def safe_work(path):
    path=Path(path).absolute()
    repo=Path(__file__).absolute().parents[3]
    if path.resolve().is_relative_to(repo.resolve()):
        raise ValueError('项目、台账和媒体输出不能写入共享能力仓库')
    return path


class Session:
    def __init__(self, io, config, project_dir=None, creator=None, *, readonly=False, reconcile_on_open=True):
        self.io,self.config=io,config
        self.readonly=readonly
        initialize=reconcile_on_open and not readonly
        self.snapshot=io.snapshot()
        if abs(float(self.snapshot['timeline']['fps'])-29.97)>0.001:raise ValueError('发布版只支持恒定29.97时间线')
        model=build_model(self.snapshot)
        self.work=safe_work(infer_work_dir(model,project_dir))
        self.path=self.work/'B-roll台账.json'
        self.ledger=read_json(self.path)
        self.creator=creator or (self.ledger or {}).get('creator') or config.get('creator')
        if not self.creator:
            raise ValueError('创作者未配置，请确认一次 --creator')
        if '/' in self.creator or self.creator in ('.','..'):
            raise ValueError('创作者姓名不能含路径')
        self.profile=default_profile(self.creator)
        if not books.learning_allowed(model['snapshot']['name']):
            raise ValueError('历史/审核/逐句否决线默认不核对、不写入，请指定原主线 --timeline')
        if not self.ledger:
            self.ledger=books.adopt(model,self.creator,self.run_id(),io.name,io.db)
            for row in self.ledger['spots'].values():row['guard_state']=deepcopy(row['placement'])
            if initialize:self.save()
        else:
            if self.ledger.get('project')!=io.name or self.ledger.get('database')!=io.db:
                raise ValueError('台账不属于当前工程或数据库')
            if self.ledger.get('v1_source_fingerprint')!=model['v1_source_fingerprint']:
                raise ValueError('口播源已改变，请使用新项目；不重写来源基线')
            if self.ledger['timeline']!=io.main_name:
                models=[build_model(io.snapshot())]
                # A renamed/copy line can only be selected if caller explicitly
                # targets it; never implicitly choose between modified copies.
                if not config.get('selected_timeline'):
                    raise ValueError('同谱系时间线名称改变，请用 --timeline 选择用哪条')
                books.resolve_lineage(self.ledger,models,io.main_name)
                self.ledger['timeline']=io.main_name
        self.catalog=read_json(self.work/'素材目录.json',{})
        project_root=work_project_dir(self.work)
        fx_path=project_root/'花字音效/操作记录.json'
        ai_home=self.work.parent.parent if self.work.parent.name=='B-roll_AI' else self.work.parent
        if not fx_path.exists():fx_path=ai_home/'花字音效/操作记录.json'
        self.fx_record=read_json(config.get('fx_record',fx_path),{})
        if initialize:
            if not config.get('selected_timeline'):self.check_copies()
            self.reconcile()

    def check_copies(self):
        modified=[];original=self.io.main
        try:
            for number in range(1,self.io.p.GetTimelineCount()+1):
                t=self.io.p.GetTimelineByIndex(number)
                if not books.learning_allowed(t.GetName()):continue
                self.io.main=t
                snap=self.io.snapshot()
                if not any(r['type']=='video' and r['track']==1 for r in snap['timeline']['items']):continue
                model=build_model(snap,self.ledger,fx_record=self.fx_record)
                if model['v1_source_fingerprint']!=self.ledger['v1_source_fingerprint']:continue
                delta=reconcile.diff_snapshots(self.ledger['S0'],snap,self.ledger,self.catalog,fx_record=self.fx_record)
                if delta['actions'] or delta['raw_changes'] or delta['aroll_reedit']:modified.append(t.GetName())
        finally:self.io.main=original
        if len(modified)>1:raise ValueError('同谱系两条时间线都被改过，请确认一次 --timeline 用哪条：'+'、'.join(modified))

    def run_id(self):
        return datetime.now().astimezone().strftime('%m%d-%H%M%S-%f')

    def save(self):
        if self.readonly:raise ValueError('只读会话不能写台账')
        books.save_ledger(self.path,self.ledger)
        register(self.config['ledger_index'],self.ledger,self.work)

    def reconcile(self):
        """对账：对照台账辨认用户手改，把台账基线推进到当前时间线，记下删除/替换/停用/裁短留下的保留位。只动台账，不学偏好。"""
        if self.readonly:raise ValueError('只读会话不能写台账')
        self.snapshot=self.io.snapshot()
        fx_built=bool(self.fx_record.get('effects')) and self.fx_record.get('project_id')==self.io.pid and self.fx_record.get('timeline_id')==self.io.main_id and all(e.get('item_id') in {i['id'] for i in self.snapshot['timeline']['items']} for e in self.fx_record['effects'])
        delta=reconcile.diff_snapshots(self.ledger['S0'],self.snapshot,self.ledger,self.catalog,fx_built=fx_built,fx_record=self.fx_record)
        # 追不到内部来源的复合/Fusion 片段：这几处不核对，清单照旧写在工作目录（文件名沿用以前的）
        self.untraced=delta.get('skipped',[])
        if self.untraced or (self.work/'学习跳过.json').exists():
            index.atomic_json(self.work/'学习跳过.json',{'items':self.untraced})
        if not delta['update_allowed']:
            raise ValueError(delta.get('reason','身份不确定，停止核对手改（没有改台账，也没有写入）'))
        new_approval=any(r.get('approved') and not self.ledger['spots'][r['spot']].get('approved') for r in delta['results'])
        # last_learned 是台账里的老字段名，意思是“上次对账时间”；写入器写完会把它清空。
        if delta['actions'] or delta['aroll_reedit'] or delta['raw_changes'] or new_approval:
            self.ledger=delta['ledger']
        self.ledger['last_learned']=datetime.now().astimezone().isoformat();self.save()

    def model(self):
        return build_model(self.snapshot,self.ledger,self.profile,self.fx_record)

    def plan(self, folders=(), descriptions=None, supplements=None, *, more=False):
        rows=media_pool(self.io.p)
        folder_files=[];skipped=[]
        cached=read_json(self.work/'索引/index.json',{'files':{}})['files']
        for folder in sorted(set(folders)|set(self.profile.get('new_folders',[]))):
            base=Path(folder)
            if not base.is_dir():raise ValueError('补充文件夹不存在：'+folder)
            for path in sorted(base.rglob('*')):
                if not path.is_file() or path.suffix.casefold() not in media.VIDEO|media.PHOTO:continue
                kind='video' if path.suffix.casefold() in media.VIDEO else 'photo'
                try:
                    previous=cached.get(str(path.absolute()),{});st=path.stat()
                    if kind=='photo':info={'duration':0,'fps_num':30000,'fps_den':1001}
                    elif (st.st_size,st.st_mtime_ns)==(previous.get('size'),previous.get('mtime_ns')):
                        info=previous['probe']
                    else:info=media.probe(path,kind)
                except (OSError,RuntimeError,ValueError,KeyError,TypeError,ArithmeticError,subprocess.SubprocessError) as error:
                    skipped.append({'file':str(path),'reason':str(error)});continue
                folder_files.append({'file_path':str(path.absolute()),'kind':kind,'duration':info.get('duration',0),'fps':[info.get('fps_num',30000),info.get('fps_den',1001)],'probe':info})
        valid=[]
        for row in rows:
            if row['kind']=='video' and row.get('duration',0)<=0:
                skipped.append({'file':row['file_path'],'reason':'候选素材时长无效'});continue
            valid.append(row)
        selected=candidates.collect_candidates(self.model(),valid,self.profile,folder_files=folder_files,folders=folders)
        # 不支持的 RAW(.ARW) 或离线文件先跳过；解码失败由索引逐个隔离。
        for row in selected:
            if Path(row['file_path']).suffix.casefold() not in media.VIDEO|media.PHOTO or not Path(row['file_path']).is_file():
                skipped.append({'file':row['file_path'],'reason':'不支持的素材类型或文件离线'})
        selected=[r for r in selected if r['file_path'] not in {s['file'] for s in skipped}]
        # 第一层＝本片项目里的素材。项目根优先按 V1 口播源推出（--project-dir 只决定工作目录放哪）。
        roots={work_project_dir(self.work)}
        try:roots.add(work_project_dir(infer_work_dir(self.model())))
        except ValueError:pass
        first=[r for r in selected if any(Path(r['file_path']).absolute().is_relative_to(x) for x in roots)
               or not r.get('in_media_pool')]
        second=[r for r in selected if r not in first]
        # Explicit/profile folder additions are already requested by the user.
        selected=selected if more else first
        self.work.mkdir(parents=True,exist_ok=True)
        cache=self.work/'索引'
        rules=read_json(index.RULES_FILE)
        if 'max_segments_per_file' in self.config:rules['max_segments_per_file']=self.config['max_segments_per_file']
        outcome=index.build_index_files(selected,cache,descriptions=descriptions,rules=rules)
        skipped.extend(outcome['skipped'])
        value=read_json(cache/'index.json')
        if supplements:
            value=index.merge_supplements(value,read_json(supplements),source_dir=Path('/'))
            index.atomic_json(cache/'index.json',value)
        self.catalog=candidates.build_catalog(value,selected,self.model()['fps'])
        self.catalog=dict(sorted(self.catalog.items(),key=lambda row:(row[1]['priority'],row[1]['file'],row[1]['start'])))
        index.atomic_json(self.work/'素材目录.json',self.catalog)
        draft=matching.build_match_draft(self.model(),self.catalog,self.profile,self.ledger)
        draft['candidate_layers']={'first_files':len(first),'second_files':len(second),'more':more,
                                   'message':f'第二层还有 {len(second)} 个文件可补' if not more else f'已加入第二层 {len(second)} 个文件'}
        draft['limited_materials']=outcome['limited']
        draft['skipped_materials']=skipped
        index.atomic_json(self.work/'B-roll匹配稿.json',draft)
        index.atomic_json(self.work/'匹配依据.json',{'basis_hash':snapshot_hash(self.snapshot),'profile_hash':self.profile.get('inputs_hash')})
        return {'message':'匹配稿已生成；请逐张看索引图并填写匹配，不猜画面','匹配稿':str(self.work/'B-roll匹配稿.json'),'待描述':outcome['pending'],'预计说明':'每10段画面描述约2分钟',
                '预计耗时':outcome['estimate'],'候选分层':draft['candidate_layers'],'长素材限段':outcome['limited'],'跳过的素材':len(skipped),'跳过的素材及原因':skipped}

    def prepare(self, matches, targeted=()):
        basis=read_json(self.work/'匹配依据.json')
        if not targeted and basis and basis['basis_hash']!=snapshot_hash(self.snapshot):raise ValueError('匹配稿依据已变化，请重新plan，不能沿用旧行号')
        value=matching.validate_matches(matches,self.model(),self.catalog,self.ledger,targeted)
        missing=matching.supplement_ids(value,self.catalog)
        if missing:
            index.supplement_descriptions(read_json(self.work/'索引/index.json'),missing,self.work/'补充描述',source_dir=Path('/'))
            raise ValueError('前三候选缺质量字段，已生成补充描述清单；下一步：plan --supplements 文件')
        planning=deepcopy(self.ledger)
        planning['spots']={k:r for k,r in planning['spots'].items() if r.get('status')!='deleted'}
        for row in planning['spots'].values():
            row['S0']=deepcopy(row.get('guard_state',row['S0']))
        active_targets=[k for k in targeted if k in planning['spots']]
        outcome=matching.plan_matches(value,self.model(),self.catalog,self.profile,planning,targeted=active_targets)
        if outcome.get('needs_coverage_confirmation') and not self.config.get('coverage_confirmed'):
            raise ValueError('覆盖率下降超过15个百分点，请确认后设置 coverage_confirmed=true')
        if outcome.get('flashes'):raise ValueError('计划有闪缝，不能交付')
        unavailable=[]
        raw_value=deepcopy(value)
        eligible={(r['spot'],n,c['segment']) for r in outcome['matches']['segments']
                  for n,shot in enumerate(r['shots'],1) for c in shot['candidates']}
        for row in value['segments']:
            cursor=next(l['start'] for l in self.model()['lines'] if l['line']==row['lines'][0])
            for n,shot in enumerate(row['shots'],1):
                keep=[]
                for candidate in shot['candidates']:
                    if (row['spot'],n,candidate['segment']) not in eligible:
                        keep.append(candidate);continue
                    meta=self.catalog[candidate['segment']]
                    how=placement.prepare_placement(meta,candidate.get('source_start',meta['usable_start']),shot['end']-cursor,timeline_fps=self.model()['fps'],folder_rules=self.profile.get('folder_lut_rules',self.config.get('folder_lut_rules',[])),lut_dirs=self.config.get('lut_dirs'))
                    if how['method']=='skip':unavailable.append({'spot':row['spot'],'shot':n,'segment':candidate['segment'],'reasons':[how['reason']]})
                    else:keep.append(candidate)
                shot['candidates']=keep;cursor=shot['end']
        if unavailable:
            outcome=matching.plan_matches(value,self.model(),self.catalog,self.profile,planning,targeted=active_targets)
            outcome['filtered']+=unavailable
            c=outcome['compliance'];c['considered']+=len(unavailable);c['filtered_candidates']=len(outcome['filtered'])
            c['filtered_spots']=len({r['spot'] for r in outcome['filtered']})
            c['rate']=(c['considered']-c['filtered_candidates'])/c['considered'] if c['considered'] else 1.0
            for p in outcome['placements']:
                if p['status']=='skipped' and p['reason']=='没有候选':
                    p['reason']=next((r['reasons'][0] for r in unavailable if r['spot']==p['spot'] and r['shot']==p['shot']),'没有候选')
        if outcome.get('needs_coverage_confirmation') and not self.config.get('coverage_confirmed'):raise ValueError('覆盖率下降超过15个百分点，请先确认')
        if outcome.get('flashes'):raise ValueError('回退候选后仍有闪缝')
        operations=[];run=self.run_id();crop_notes=[]
        for p in outcome['placements']:
            if p['status']!='placed':continue
            parent=p['spot'];spot=parent if p.get('shot',1)==1 else parent+'-shot'+str(p['shot']);meta=self.catalog[p['segment']]
            how=placement.prepare_placement(meta,p['source_start'],p['end']-p['start'],timeline_fps=self.model()['fps'],folder_rules=self.profile.get('folder_lut_rules',self.config.get('folder_lut_rules',[])),lut_dirs=self.config.get('lut_dirs'))
            if how['method']=='skip':
                raise ValueError('spot '+spot+' / 段 '+p['segment']+'：候选准备状态已变化：'+how['reason'])
            path=meta['file'];offset=how['source_start_frame'];generated=False
            stat=Path(path).stat()
            if any(meta.get(k) not in (None,v) for k,v in (('size',stat.st_size),('mtime_ns',stat.st_mtime_ns))):raise ValueError('spot '+spot+' / 段 '+p['segment']+'：候选源片身份已改变，请更新索引')
            if how['method']=='direct' and meta['kind']=='photo' and p.get('crop_anchor') is not None:
                # 照片点名了裁切位置：直接引用做不到，按指定位置出衍生片段（和照片长度回退同一种片段）
                lut=placement.select_lut(meta,folder_rules=self.profile.get('folder_lut_rules',self.config.get('folder_lut_rules',[])),lut_dirs=self.config.get('lut_dirs'))
                if lut['status']!='ok':raise ValueError('spot '+spot+'：'+lut['reason'])
                how=dict(how,method='render',lut_path=lut['lut_path'],reason='照片按点名的裁切位置生成衍生片段')
            if how['method']=='render':
                filename_spot=spot if re.fullmatch(r'[\w-]{1,64}',spot) else 'legacy-'+books.digest(spot)[:16]
                clip=media.render_live_clip(meta,self.work/'B-roll片段',filename_spot,p['source_start'],p['end']-p['start'],timeline_fps=self.model()['fps'],resolution=(int(self.snapshot['timeline']['width']),int(self.snapshot['timeline']['height'])),lut_path=how['lut_path'],crop_anchor=p.get('crop_anchor'))
                path,offset,generated=clip['path'],clip['startFrame'],True
                if (clip.get('crop') or {}).get('warning'):crop_notes.append(spot+'｜'+Path(meta['file']).name+'｜'+clip['crop']['warning'])
            elif p.get('crop_anchor') is not None:
                raise ValueError('spot '+spot+'：这段视频是原片直接引用，没有衍生片段，crop_anchor 不起作用；裁切位置在达芬奇里调')
            text=''.join(l['text'] for l in self.model()['lines'] if l['start']<p['end'] and p['start']<l['end'])
            row=self.ledger['spots'].get(spot)
            old=[deepcopy(row.get('guard_state',row.get('placement')))] if row and row.get('placement') and row.get('status')!='deleted' else []
            if row and row.get('audio'):old.append(deepcopy(row.get('audio_guard',row['audio'])))
            operations.append({'spot':spot,'old':old,'text':text,'candidates':next((r['shots'][p.get('shot',1)-1]['candidates'] for r in raw_value['segments'] if r['spot']==parent),[]),
                'parent_spot':parent,'new':{'type':'video','path':path,'generated':generated,'media_id':meta.get('media_id') if not generated else None,'offset':offset,'start':p['start'],'end':p['end'],'track':p['track'],'name':None,'derivative_reason':how['reason'] if generated else None,'props':p.get('props',{}),'segment':p['parent_segment'],'parent_segment':p['parent_segment'],'source_file':meta['file'],'source_kind':meta['kind'],'source_start':p['source_start']}})
            if meta['kind']=='photo' and not generated:
                operations[-1]['new']['photo_fallback']={
                    'candidate':deepcopy(meta),'out_dir':str(self.work/'B-roll片段'),
                    'lut_dirs':self.config.get('lut_dirs'),
                    'folder_rules':self.profile.get('folder_lut_rules',self.config.get('folder_lut_rules',[]))}
        if targeted:check_adjust_scope(operations,targeted)
        report=placement_report(outcome,self.model(),self.catalog,raw_value,';' in self.io.main.GetStartTimecode())
        return {'run':run,'basis_hash':snapshot_hash(self.snapshot),'operations':operations,'report':report['report'],
                'arrangement':outcome,'crop_notes':crop_notes}

    def apply(self, matches, apply=False, targeted=()):
        plan=self.prepare(matches,targeted)
        sheet=None
        if not apply and plan['operations']:
            shots=[dict(spot=op['spot'],file=op['new']['source_file'],
                        kind=op['new']['source_kind'],
                        start=op['new']['start'],end=op['new']['end'],source_start=op['new']['source_start'])
                   for op in plan['operations']]
            sheet=media.rehearsal_sheet(shots,self.work/'演练核图'/f"{plan['run']}.jpg",self.model()['fps'])
        outcome=execute(self.io,plan,self.ledger,self.path,apply=apply,config=self.config,local=bool(targeted))
        outcome.update(placement_report(plan['arrangement'],self.model(),self.catalog,matches,
                                       ';' in self.io.main.GetStartTimecode()))
        notes=list(plan.get('crop_notes',[]))
        for fallback in outcome.get('photo_fallbacks',[]):
            outcome['report']+='\n'+fallback['spot']+'｜'+fallback['derivative_reason']
            if fallback.get('crop_warning'):notes.append(fallback['spot']+'｜'+fallback['crop_warning'])
        if notes:
            # Q130：铺满裁掉超过一半画面的片段，提醒给用户看一眼
            outcome['裁切提醒']=notes
            outcome['report']+='\n'+'\n'.join('裁切提醒：'+n for n in notes)
        if sheet:outcome['演练核图']=sheet
        if outcome.get('applied'):
            self.ledger=outcome.pop('ledger')
            for op in plan['operations']:
                row=self.ledger['spots'].get(op['spot'],{})
                if row.get('placement'):row['guard_state']=deepcopy(row['placement'])
                if row.get('audio'):row['audio_guard']=deepcopy(row['audio'])
            self.save()
        return outcome

    def adjust(self, spots, *, use=None, segment=None, remove=False, reserve=False, face=None, audio=None, apply=False, crop_anchor=None):
        if len(spots)!=len(set(spots)):raise ValueError('点名编号重复')
        named=list(spots)
        spots=list(dict.fromkeys(k for name in named for k,r in self.ledger['spots'].items() if k==name or r.get('parent_spot')==name))
        if any(name not in self.ledger['spots'] for name in named):raise ValueError('找不到点名位置')
        if crop_anchor is not None:
            # Q130：同一素材、同一源入点，只改铺满时的裁切位置，重出这一段衍生片段
            segments=[]
            for parent in named:
                children=sorted((k for k in spots if k==parent or self.ledger['spots'][k].get('parent_spot')==parent),key=lambda k:self.ledger['spots'][k]['placement']['start'])
                states=[self.ledger['spots'][k]['placement'] for k in children]
                lines=[l['line'] for l in self.model()['lines'] if l['start']<states[-1]['end'] and states[0]['start']<l['end']]
                shots=[]
                for child,state in zip(children,states):
                    row=self.ledger['spots'][child]
                    if row.get('status')=='deleted':raise ValueError(child+'：该处没有可调整片段')
                    if not row.get('derivative_reason'):raise ValueError(child+'：这处是原片直接引用，没有衍生片段，crop_anchor 不起作用；裁切位置在达芬奇里调')
                    length=state['end']-state['start']
                    key=next((k for k,m in self.catalog.items() if segment_identity(k,m)==row.get('segment')
                              and m.get('usable_start',0)<=row['source_start'] and row['source_start']+length<=m.get('usable_end',0)),None)
                    if key is None:raise ValueError(child+'：素材目录里找不到这处用的素材段，先 plan 再试')
                    shots.append({'end':state['end'],'candidates':[{'segment':key,'reason':'用户点名调整裁切位置','source_start':row['source_start'],'crop_anchor':crop_anchor}]})
                segments.append({'spot':parent,'lines':[min(lines),max(lines)],'shots':shots})
            return self.apply({'version':3,'segments':segments,'face':[]},apply,targeted=spots)
        if use or segment or face=='off':
            segments=[]
            for parent in named:
                children=[k for k in spots if k==parent or self.ledger['spots'][k].get('parent_spot')==parent]
                children.sort(key=lambda k:self.ledger['spots'][k]['placement']['start'])
                states=[self.ledger['spots'][k]['placement'] for k in children]
                lines=[l['line'] for l in self.model()['lines'] if l['start']<states[-1]['end'] and states[0]['start']<l['end']]
                shots=[]
                for child,state in zip(children,states):
                    row=self.ledger['spots'][child];options=row.get('candidates',[])
                    if use and not 1<=use<=len(options):raise ValueError('该处没有所选备选')
                    chosen={'segment':segment,'reason':'用户点名替换'} if segment else options[use-1] if use else options[0] if options else None
                    if not chosen:raise ValueError('该处没有候选，请先生成局部匹配')
                    shots.append({'end':state['end'],'candidates':[chosen]})
                segments.append({'spot':parent,'lines':[min(lines),max(lines)],'shots':shots})
            # Explicit face-off allows only the named source reservation to clear.
            self.ledger['reserved']=[r for r in self.ledger['reserved'] if r.get('spot') not in spots] if face=='off' else self.ledger['reserved']
            if face=='off':self.profile['reserved']=[r for r in self.profile.get('reserved',[]) if r.get('spot') not in spots]
            return self.apply({'version':3,'segments':segments,'face':[]},apply,targeted=spots)
        operations=[]
        for spot in spots:
            row=self.ledger['spots'][spot];state=row.get('placement')
            if not state or row.get('status')=='deleted':raise ValueError('该处没有可调整片段')
            if audio:
                if audio=='off':
                    if not row.get('audio'):continue
                    operations.append({'spot':spot,'old':row.get('audio_guard',row['audio'])})
                else:
                    if row.get('audio'):continue
                    meta=self.catalog.get(row.get('segment'),{})
                    info=meta.get('probe',{})
                    if not info.get('has_audio') or meta.get('kind')=='photo':raise ValueError('这处素材无原声')
                    source=meta['file'];offset=state['broll_extra']['left_offset'];generated=False
                    if state['media_path']!=source:
                        from .audio_clip import render_audio
                        clip=render_audio(meta,self.work/'B-roll片段',spot,row['source_start'],state['duration'])
                        source,offset,generated=clip['path'],0,True
                    operations.append({'spot':spot,'new':{'type':'audio','path':source,'generated':generated,'offset':offset,'start':state['start'],'end':state['end'],'track':0,'name':state['name']+' 原声','props':{'AudioVolume':-5}}})
            else:
                old=[row.get('guard_state',state)]
                if row.get('audio'):old.append(row.get('audio_guard',row['audio']))
                operations.append({'spot':spot,'old':old,'reserve':bool(remove or reserve or face=='on'),
                    'reserve_ranges':[[l['start'],l['end']] for l in self.model()['lines'] if l['start']<state['end'] and state['start']<l['end']] if face=='on' else []})
        check_adjust_scope(operations,spots)
        outcome=execute(self.io,{'run':self.run_id(),'basis_hash':snapshot_hash(self.snapshot),'operations':operations},self.ledger,self.path,apply=apply,config=self.config,local=True)
        from .arrange import _coverage
        model=self.model()
        deleted=[state['id'] for op in operations for state in (op.get('old',[]) if isinstance(op.get('old',[]),list) else [op['old']]) if state['type']=='video']
        rows=[]
        for spot in spots:
            row=self.ledger['spots'][spot];state=row['placement']
            done=any(op['spot']==spot for op in operations)
            rows.append(dict(spot=spot,text=row.get('semantic',{}).get('text',''),start=state['start'],
                source_start=row.get('source_start'),file=row.get('source_file') or state['media_path'],
                status='adjusted' if done else 'skipped',reason=('原声已开启' if audio=='on' else '原声已关闭') if not done else
                '开原声' if audio=='on' else '关原声' if audio=='off' else '露脸' if face=='on' else '保留位（点名留空）'))
        outcome.update(placement_report({'placements':rows,'previous_coverage':_coverage(model),
                                       'coverage':_coverage(model,excluded_ids=deleted)},model,self.catalog,
                                       drop=';' in self.io.main.GetStartTimecode()))
        if outcome.get('applied'):
            self.ledger=outcome.pop('ledger')
            for spot in spots:
                row=self.ledger['spots'][spot]
                if row.get('audio'):row['audio_guard']=deepcopy(row['audio'])
            self.save()
        return outcome

    def clean(self, yes=False, apply=False):
        """不带参数只列清单，不移动任何文件；apply 才把未引用的移到 待清理/；yes（用户明确同意删）再删 待清理/ 里仍未引用的。"""
        # Query all timelines, not just this main line or the media pool.
        refs={r['file_path'] for r in media_pool(self.io.p)}
        original=self.io.main
        try:
            for i in range(1,self.io.p.GetTimelineCount()+1):
                self.io.main=self.io.p.GetTimelineByIndex(i)
                refs.update(r['media_path'] for r in self.io.snapshot()['timeline']['items'] if r.get('media_path'))
        finally:self.io.main=original
        root=self.work/'B-roll片段'
        rows=placement.clean_candidates([str(p) for p in root.glob('*') if p.is_file()],refs)
        for wav in root.glob('*.wav'):
            if str(wav) in refs:continue
            for path in (wav,wav.with_suffix('.json')):
                if path.is_file():rows.append({'path':str(path),'destination':str(root/'待清理'/path.name)})
        if not (apply or yes):
            staged=[str(p) for p in sorted((root/'待清理').glob('*')) if p.is_file()]
            return {'message':'演练：只列清单，没有移动或删除任何文件。加 --apply 才把清单里的文件移到 待清理/；用户明确同意删除后才加 --yes',
                    '清单':rows,'待清理里已有':staged,'已删除':[]}
        for row in rows:
            src,dest=Path(row['path']),Path(row['destination'])
            dest.parent.mkdir(exist_ok=True)
            if dest.exists():raise ValueError('待清理目标已存在，不覆盖：'+str(dest))
            src.rename(dest)
        removed=[]
        if yes:
            # Recheck previously staged files too; referenced files always stay.
            for path in sorted((root/'待清理').glob('*')):
                if not path.is_file() or path.is_symlink() or path.suffix.lower() not in ('.mov','.wav','.json'):continue
                original=root/path.name
                related=[path,original]
                if path.suffix.lower()=='.json':related += [path.with_suffix(ext) for ext in ('.mov','.wav')]+[original.with_suffix(ext) for ext in ('.mov','.wav')]
                if any(str(p) in refs for p in related):continue
                path.unlink();removed.append(str(path))
        return {'message':'已移到待清理；加 --yes 才删除' if not yes else '已删除本次未引用片段','清单':rows,'已删除':removed}


def check_adjust_scope(operations, named):
    touched={op['spot'] for op in operations}
    if not touched.issubset(set(named)) or len(touched)>len(set(named)):
        raise ValueError('调整范围超过点名位置数，拒绝执行')


def main_for_review(io, config, project_dir=None):
    """Locate a viewing copy's ledger via the configured NAS registry."""
    active=io.p.GetCurrentTimeline()
    if books.learning_allowed(active.GetName()):return io,project_dir
    registry=read_json(config['ledger_index'],{'entries':{}})
    found=[]
    for row in registry['entries'].values():
        if row.get('project')!=io.name or row.get('database')!=io.db:continue
        work=Path('/Volumes')/row['volume']/row['work_dir'] if row.get('volume') else Path(row['work_dir'])
        book=read_json(work/'B-roll台账.json',{})
        if active.GetName() in book.get('reviews',{}):found.append((book,work))
    if len(found)!=1:raise ValueError('审核线无法唯一对应台账，请明确主线与 --project-dir')
    from engine.fx.connect import find_timeline
    book,work=found[0]
    return Adapter(io.r,io.p,find_timeline(io.p,book['timeline'])),str(work_project_dir(work))
