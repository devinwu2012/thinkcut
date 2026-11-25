#!/usr/bin/env python3
"""B-roll：实时读取、匹配、演练/写入、局部调整与撤销。"""
import argparse
import json
import os
from pathlib import Path
import sys

if __name__=='__main__' and not sys.flags.utf8_mode:
    os.execvpe(sys.executable,[sys.executable,'-X','utf8',*sys.argv],dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1'))
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from engine.broll.live import service
from engine.broll.live.writer import undo
from engine.broll.live.review_live import identify


class ChineseParser(argparse.ArgumentParser):
    def __init__(self,*args,**kwargs):
        kwargs['add_help']=False
        super().__init__(*args,**kwargs)
        self._positionals.title='命令与位置参数';self._optionals.title='选项'
        self.add_argument('-h','--help',action='help',help='显示帮助并退出')
    def format_help(self):
        return super().format_help().replace('usage:','用法：')
    def error(self, message):
        raise ValueError('参数不正确：'+message.replace('required','必填').replace('unrecognized arguments','未识别参数').replace('not allowed with argument','不能同时使用'))


def parser():
    p=ChineseParser(description=__doc__)
    subs=p.add_subparsers(dest='command',required=True,parser_class=ChineseParser)
    for name in ('status','plan','apply','adjust','undo','at','clean'):
        cmd=subs.add_parser(name)
        cmd.add_argument('--config',default=str(Path(__file__).resolve().parents[2]/'B-roll学习库/broll.json'))
        cmd.add_argument('--timeline');cmd.add_argument('--project-dir');cmd.add_argument('--creator')
        if name=='plan':
            cmd.add_argument('--folder',action='append',default=[]);cmd.add_argument('--descriptions');cmd.add_argument('--supplements')
            cmd.add_argument('--more',action='store_true',help='加入其他项目/素材库的第二层候选')
        if name=='apply':cmd.add_argument('--matches',required=True)
        if name in ('apply','adjust','undo'):cmd.add_argument('--apply',action='store_true')
        if name=='adjust':
            cmd.add_argument('--spot',required=True,action='append')
            group=cmd.add_mutually_exclusive_group(required=True)
            group.add_argument('--use',type=int,choices=(1,2,3));group.add_argument('--segment')
            group.add_argument('--remove',action='store_true');group.add_argument('--reserve',action='store_true')
            group.add_argument('--face',choices=('on','off'));group.add_argument('--audio',choices=('on','off'))
            group.add_argument('--crop-anchor',type=float,help='重出这处衍生片段、只改裁切位置：0 取最上/最左，0.5 居中，1 取最下/最右')
        if name=='undo':cmd.add_argument('--run')
        if name=='at':
            cmd.add_argument('timecode',nargs='?');cmd.add_argument('--playhead',action='store_true')
        if name=='clean':
            cmd.add_argument('--apply',action='store_true',help='把未引用的生成片段移到 待清理/；不加只列清单')
            cmd.add_argument('--yes',action='store_true',help='用户明确同意后才用：移到 待清理/ 并删除其中仍未引用的')
    return p


def ip_name(project_dir=None):
    sys.path.append(str(Path(__file__).resolve().parents[2]/'bin')); import thinkcut_config
    return thinkcut_config.load(project=project_dir).get('名字')


def main(argv=None, preflight=service.preflight, session_type=service.Session):
    try:
        args=parser().parse_args(argv)
        if args.command=='at' and bool(args.timecode)==bool(args.playhead):raise ValueError('at请选择一个时间码或 --playhead')
        # 默认配置：工作区是程序目录；台账索引（审核线靠它找回主线台账）放在能力仓库外；作者名是这条片子所属 IP 的名字（配置档）。
        # 它所在的文件夹还叫“B-roll学习库”是历史原因：学偏好的功能 2026-11-21 下线，台账索引没有搬家。
        # 本机配置文件里的同名项覆盖默认值。
        # 位置按本仓库在哪儿推（上一级是程序目录，再上一级是 NAS 共享根），NAS 换了挂载名 也能用。
        home=Path(__file__).resolve().parents[1].parent
        cfg={'nas_mount':str(home.parent),'ledger_index':str(home/'B-roll学习库/台账索引.json'),'creator':ip_name(args.project_dir)}
        cfg.update(service.read_json(args.config,{}))
        cfg['selected_timeline']=args.timeline
        io,env=preflight(cfg,args.timeline)
        active=io.p.GetCurrentTimeline()
        tc=active.GetCurrentTimecode() if args.command=='at' and args.playhead else getattr(args,'timecode',None)
        # Review line mapping requires a registered main-line ledger. Use explicit
        # --project-dir plus --timeline main when current line is a viewing copy.
        project_dir=args.project_dir
        if args.command=='at':io,project_dir=service.main_for_review(io,cfg,project_dir)
        session=session_type(io,cfg,project_dir,args.creator,
                             readonly=args.command in ('status','at'),
                             reconcile_on_open=args.command in ('plan','apply','adjust'))
        command=args.command
        if command=='status':out={'工程':io.name,'时间线':io.main_name,'数据库':env['数据库'],'工作目录':str(session.work),'位置数':len(session.ledger['spots'])}
        elif command=='plan':out=session.plan(args.folder,args.descriptions,args.supplements,more=args.more)
        elif command=='apply':out=session.apply(service.read_json(args.matches),args.apply)
        elif command=='adjust':out=session.adjust(args.spot,use=args.use,segment=args.segment,remove=args.remove,reserve=args.reserve,face=args.face,audio=args.audio,apply=args.apply,crop_anchor=args.crop_anchor)
        elif command=='undo':
            out=undo(io,session.ledger,session.path,run=args.run,apply=args.apply,config=cfg)
            if out.get('applied'):session.ledger=out.pop('ledger');session.save()
        elif command=='at':out={'对应位置':identify(session.ledger,active.GetName(),tc,active.GetSetting('timelineFrameRate')),'说明':'不唯一时请从最多3个编号中选择；修改请回原主线'}
        else:out=session.clean(args.yes,args.apply)
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0
    except KeyboardInterrupt:
        print('已中断。写入途中被中断的会先自动回滚（上面会有“已中断：…已回滚”的提示）；没有这句提示就是还没开始写。\n下一步：需要的话重新演练再写入。',file=sys.stderr)
        return 130
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,ImportError,subprocess_timeout()) as exc:
        print('出错了：'+str(exc)+'\n下一步：按上述原因处理后重试；不要重复未核验的写入。',file=sys.stderr)
        return 2


def subprocess_timeout():
    import subprocess
    return subprocess.TimeoutExpired


if __name__=='__main__':sys.exit(main())
