#!/usr/bin/env python3
"""素材库：建跨期素材索引、按一句口播或关键词找历史素材和动画镜头。

  python3 bin/library.py build                      # 全量（增量：没变的文件不重算 sha256，描述只补缺的）
  python3 bin/library.py add-project <项目文件夹>    # 只更新这一期
  python3 bin/library.py find "地漏芯坏了" [--limit 5] [--json] [--category 动画]
  python3 bin/library.py describe <描述.json>        # 把看图写好的描述并进索引
  python3 bin/library.py notes-template <项目文件夹>  # 片子收尾：在项目里生成 02_制作/素材库说明.json 让做片子的会话填
                                                    # 列素材清单里的素材、动画镜头，以及分镜“实拍／图片／盖实拍”引用的项目内文件（动画 mp4、生成图；2026-11-22 261122 反馈单 新10）
  python3 bin/library.py notes <项目文件夹>           # 把填好的 素材库说明.json 写进素材库（优先于看图补写和素材清单说明）
"""
import argparse
import json
import os
from pathlib import Path
import sys

if __name__ == '__main__' and not sys.flags.utf8_mode:
    os.execvpe(sys.executable, [sys.executable, '-X', 'utf8', *sys.argv], dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'))
sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
from engine.library import catalog, search


class ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs['add_help'] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = '命令与位置参数'
        self._optionals.title = '选项'
        self.add_argument('-h', '--help', action='help', help='显示帮助并退出')

    def format_help(self):
        return super().format_help().replace('usage:', '用法：')

    def error(self, message):
        raise ValueError('参数不正确：'+message.replace('required', '必填').replace('unrecognized arguments', '未识别参数'))


def parser():
    p = ChineseParser(prog='library.py', description='素材库：跨期素材索引与找素材')
    p.add_argument('--index', default=str(catalog.INDEX_DIR), help='索引目录（默认 %(default)s）')
    p.add_argument('--root', default=str(catalog.VIDEOS_ROOT), help='放片子的文件夹（默认 %(default)s）')
    sub = p.add_subparsers(dest='command', required=True, parser_class=ChineseParser)
    sub.add_parser('build', help='全量建索引（没变的文件直接复用）')
    add = sub.add_parser('add-project', help='只更新一个项目')
    add.add_argument('folder', help='项目文件夹')
    find = sub.add_parser('find', help='按一句口播或关键词找素材')
    find.add_argument('text', help='一句口播或关键词')
    find.add_argument('--limit', type=int, default=5, help='最多列几条（默认 5）')
    find.add_argument('--json', action='store_true', help='输出 JSON')
    find.add_argument('--category', action='append', help='只看某类：B roll / 动画 / 未整理 / A roll，可写多次；默认除 A roll 外都找')
    find.add_argument('--loose', action='store_true', help='连只靠数字、方位、虚词对上的也列出来（默认不算找到）')
    describe = sub.add_parser('describe', help='并入看图写好的描述')
    describe.add_argument('file', help='描述 JSON：[{"key":…, "description":…, "tags":[…]}]')
    template = sub.add_parser('notes-template', help='片子收尾：生成（或补）项目里的 02_制作/素材库说明.json')
    template.add_argument('folder', help='项目文件夹')
    notes = sub.add_parser('notes', help='把项目会话填好的 素材库说明.json 写进素材库')
    notes.add_argument('folder', help='项目文件夹')
    return p


def report(result):
    print(f"素材库：{result['projects']} 期项目，素材 {result['assets']} 条（其中未整理 {result['unorganized']}），动画镜头 {result['animations']} 个；"
          f"看图补写的描述 {result['described_by_ai']} 条，待补写 {result['pending']} 条。")
    for warning in result['warnings']:
        print('注意：'+warning)
    if result['pending']:
        print('下一步：逐张查看 待描述.json 里的 sheet 图，按其中 instructions 写描述 JSON，再运行 describe <文件>。')


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        index = Path(args.index)
        if args.command == 'build':
            report(catalog.build(root=args.root, index_dir=index))
        elif args.command == 'add-project':
            report(catalog.build(root=args.root, index_dir=index, projects=[args.folder]))
        elif args.command == 'notes-template':
            result = catalog.notes_template(index, args.folder)
            print(f"已写 {result['file']}：共 {result['items']} 条（素材＋动画镜头），已填 {result['filled']} 条。按文件里的 instructions 填好后运行 notes {args.folder}")
        elif args.command == 'notes':
            count, skipped, result = catalog.merge_project_notes(index, args.folder)
            print(f'已把 {count} 条项目会话写的说明并进素材库（description 留空没写的 {skipped} 条）。')
            report(result)
        elif args.command == 'describe':
            count, result = catalog.merge_descriptions(index, catalog.media.read_json(args.file))
            print(f'已并入 {count} 条描述。')
            report(result)
        else:
            if args.limit < 1:
                raise ValueError('--limit 至少是 1')
            results = search.find(index, args.text, limit=args.limit, categories=set(args.category) if args.category else None, loose=args.loose)
            print(json.dumps(results, ensure_ascii=False, indent=2) if args.json else search.format_results(results))
            if args.json and not results:      # JSON 是空列表时把原因说在旁边（2026-11-23：没拍过的题材要明确说“没有合适的”）
                print(search.format_results(results), file=sys.stderr)
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print('出错了：'+str(error), file=sys.stderr)
        return 1
    except (KeyError, TypeError, AttributeError) as error:
        print(f'出错了：素材库的数据文件内容不完整（缺字段或格式不对：{error!r}）。下一步：重新运行 build 重建索引；还不行把这句话交给维护人。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
