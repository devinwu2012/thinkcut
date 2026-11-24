"""找素材：一句口播或关键词 → 候选素材/动画镜头，附匹配理由。

用 SQLite FTS5 trigram 召回（三字词走 MATCH，两字词走 LIKE），再按“命中词的稀有程度 × 命中位置”打分：
名称（内容/用途/文件名）最重，画面描述和标签其次，用在哪句口播再次。同义词表一组里的词互相顶替。
"""
import json
import math
import re
import sqlite3
from pathlib import Path

from .catalog import search_text

FIELDS = (('名称', 3.0), ('描述', 2.5), ('口播', 1.5))
STOP = set('的了是在和就都也很吗呢吧啊着过个这那我你他她它们有要会能把被给让一说就还么嘛呀哦')
DEFAULT_EXCLUDE = {'A roll'}
# 常见虚词片段，不参与打分
STOP_GRAMS = set('或者 者是 也不 一大 家的 然后 一定 就是 还是 这样 这个 那个 我们 你们 他们 因为 所以 如果 可以 比较 非常 其实 时候 的话 一下 一点 不是 没有 什么 怎么 人家 大家 很多 结果 时候 东西 地方 问题 情况 本身 正好'.split())


# 2026-11-23 初稿模式实测：没拍过的题材（开关插座）也返回了一串无关素材，分数还不低，对上的全是“左右”“后面的”“十公分”这类词。
# 只由数字、单位、方位和虚字组成的片段算“弱词”：一条素材只靠弱词对上不算找到。
WEAK_CHARS = STOP | set('零一二三四五六七八九十百千万两半几多少第'
                        '公分厘米毫寸尺度秒钟天年月日号块元倍层根条种样次遍'
                        '左右上下前后里外面边头中间内旁最先再又才更太好大小高低长短')


def weak(term):
    """这个片段是不是弱词（不能单独证明素材相关）。英文数字串、同义词表里的词不算弱词。"""
    return all(c in WEAK_CHARS for c in term)


def clean(term):
    """这个片段里一个弱字都没有（“烟道”“止逆阀”算，“很多人”“是装”“一做”这种夹着虚字、数字的不算）。
    2026-11-24 第二次实测：整句口播靠「很多人」「是装」、「人家」「一做」两个片段也被当成找到了。"""
    return not any(c in WEAK_CHARS for c in term)


class Results(list):
    """find 的结果；weak_only 是“只靠弱词对上、被排除”的那些词（结果为空时用来说明为什么没有合适的）。"""
    weak_only = ()


def load_synonyms(index_dir):
    path = Path(index_dir)/'同义词.txt'
    groups = []
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            words = [w for w in re.split(r'[\s,，、/]+', line.split('#', 1)[0]) if w]
            if len(words) > 1:
                groups.append(words)
    return groups


def query_terms(text, synonyms):
    """返回 {词: (权重, 来历)}：原句里的二字、三字片段，加上同义词整词。"""
    text = text.casefold()
    terms = {}
    for run in re.findall(r'[一-鿿]+|[a-z0-9]+', text):
        if re.fullmatch(r'[a-z0-9]+', run):
            if len(run) >= 2:
                terms[run] = (1.5, run)
            continue
        for n, weight in ((3, 1.5), (2, 1.0)):
            for i in range(len(run)-n+1):
                gram = run[i:i+n]
                if not all(c in STOP for c in gram) and gram not in STOP_GRAMS and gram not in terms:
                    terms[gram] = (weight, gram)
    for group in synonyms:
        hits = [w for w in group if w.casefold() in text]
        for word in group:
            word = word.casefold()
            if hits and word not in terms:
                terms[word] = (1.4 if len(word) >= 3 else 1.0, f'{word}（“{hits[0]}”的同义词）')
    return terms


def _match(db, term, field=None):
    """含这个词的条目 id；三字以上走 FTS5 trigram 索引，两字走 LIKE（trigram 索引查不了两字）。"""
    columns = [field] if field else ['名称', '描述', '口播']
    if len(term) >= 3:
        quoted = '"'+term.replace('"', '""')+'"'
        target = f'{{{field}}} : {quoted}' if field else quoted
        return {r[0] for r in db.execute('SELECT id FROM 检索 WHERE 检索 MATCH ?', (target,))}
    where = ' OR '.join(f'{c} LIKE ?' for c in columns)
    return {r[0] for r in db.execute(f'SELECT id FROM 检索 WHERE {where}', ['%'+term+'%']*len(columns))}


def find(index_dir, text, *, limit=5, categories=None, include_all=False, loose=False):
    path = Path(index_dir)/'素材库.sqlite'
    if not path.exists():
        raise RuntimeError('还没建素材库索引：先运行 python3 bin/library.py build')
    db = sqlite3.connect(f'file:{path}?mode=ro&immutable=1', uri=True)
    try:
        total = db.execute('SELECT count(*) FROM 检索').fetchone()[0] or 1
        terms = query_terms(text, load_synonyms(index_dir))
        scores, reasons = {}, {}
        for term, (weight, origin) in terms.items():
            matched = _match(db, term)
            if not matched:
                continue
            idf = math.log(1+total/len(matched))
            for field, field_weight in FIELDS:
                for ident in _match(db, term, field) & matched:
                    gain = weight*idf*field_weight
                    if gain > scores.get((ident, term), 0):
                        scores[(ident, term)] = gain
                        reasons.setdefault(ident, {})[term] = (field, origin)
        totals = {}
        for (ident, term), gain in scores.items():
            totals[ident] = totals.get(ident, 0)+gain
        rows = {r[0]: json.loads(r[1]) for r in db.execute('SELECT id, 记录 FROM 素材')}
    finally:
        db.close()
    results = Results(); dropped = []
    need = 2 if sum(1 for t in terms if not weak(t)) >= 3 else 1
    for ident, score in sorted(totals.items(), key=lambda x: -x[1]):
        record = rows[ident]
        if categories and record['category'] not in categories:
            continue
        if not categories and not include_all and record['category'] in DEFAULT_EXCLUDE:
            continue
        # 只靠弱词（数字、单位、方位、虚字）对上的不算找到；同义词表顶替出来的词是人写的，照算。
        # 一整句口播（实词片段 3 个以上）只对上孤零零一个两字词（“开关”出现在讲灯的素材描述里）也不算：至少对上两个实词片段。
        strong = [term for term, (_, origin) in reasons[ident].items() if not weak(term) or origin != term]
        if need > 1:      # 一整句口播：算数的是“干净”的实词片段（或同义词表顶替出来的词）；三个字以上的干净片段一个就够
            solid = [term for term, (_, origin) in reasons[ident].items() if clean(term) or origin != term]
            strong = solid * 2 if any(len(t) >= 3 for t in solid) else solid
        if not loose and len(strong) < need:
            dropped += [t for t in reasons[ident] if t not in dropped]
            continue
        results.append(present(record, score, reasons[ident], text))
        if len(results) >= limit:
            break
    results.weak_only = tuple(dropped[:8])
    return results


def merged_hits(text, terms, field_text=None):
    """把原句里命中的相邻片段连成整段（“只进气”“不出气”→“只进气不出气”）；连起来的整段在条目里找不到时，分开列。"""
    text = text.casefold()
    if field_text is not None:
        result = []
        haystack = field_text.casefold()
        for run in merged_hits(text, terms):
            i = 0
            while i < len(run) - 1:  # 从左往右取条目里真有的最长一段
                j = next((j for j in range(len(run), i+1, -1) if run[i:j] in haystack), None)
                if j is None:
                    i += 1
                    continue
                result.append(run[i:j])
                i = j
        return list(dict.fromkeys(result))
    covered = [False]*len(text)
    for term in terms:
        start = text.find(term)
        while start >= 0:
            for i in range(start, start+len(term)):
                covered[i] = True
            start = text.find(term, start+1)
    return [''.join(text[i] for i in run) for run in _runs(covered)]


def _runs(flags):
    run = []
    for i, flag in enumerate(flags+[False]):
        if flag:
            run.append(i)
        elif run:
            yield run
            run = []


def present(record, score, reason, text):
    by_field = {}
    texts = dict(zip([f for f, _ in FIELDS], search_text(record)))
    for field, _ in FIELDS:
        own = [t for t, (f, origin) in reason.items() if f == field and origin == t]
        words = merged_hits(text, own, texts[field])+[origin for t, (f, origin) in reason.items() if f == field and origin != t]
        if words:
            by_field[field] = words
    why = '；'.join(f'{field}含「' + '」「'.join(words) + '」' for field, words in by_field.items())
    used = [{'项目': u['project'], '镜头': u['shot'], '时间': f"{u['start']:.1f}–{u['end']:.1f} 秒", '口播': u['voice']}
            for u in record.get('used_in', [])]
    item = {'得分': round(score, 1), '类别': record['category'], '项目': record['project'], '路径': record.get('path'),
            '描述': record.get('description') or '（还没有画面描述）', '标签': record.get('tags', []),
            '横竖': record.get('orientation'), 'HDR': record.get('hdr'), '时长秒': record.get('duration_seconds'),
            '用在哪': used, '匹配理由': why}
    if record.get('usage_note'):
        item['用途说明'] = record['usage_note']
    if record.get('notes'):
        item['注意'] = record['notes']
    item['描述来源'] = record.get('description_source')
    if record['kind'] == '素材':
        item['内容'] = record.get('content')
        item['用途'] = record.get('purpose')
        if record.get('other_copies'):
            item['其他副本'] = record['other_copies']
        if record.get('unorganized'):
            item['未整理'] = True
    else:
        item['动画类型'] = '/'.join(record.get('animation_types', []))
        item['动画工程'] = [t['path'] for t in record.get('tools', [])]
        clip = record.get('clip') or {}
        item['片段'] = f"{clip.get('start', 0):.2f}–{clip.get('end', 0):.2f} 秒" if clip else None
    return item


def format_results(results):
    if not results:
        weak_only = getattr(results, 'weak_only', ())
        if weak_only:
            return ('素材库里没有合适的：没找到相关素材（只对上了「' + '」「'.join(weak_only) + '」这类数字、方位、虚词或孤零零一个词，不算）。'
                    '这个镜头不用往期素材：讲结构、尺寸、位置、原理、步骤、对错对比的做动画，要真实场景的写占位图。确实想看那些弱匹配加 --loose。')
        return '素材库里没有合适的：没找到相关素材。可以换个说法，或在 同义词.txt 里加一组同义词。'
    lines = []
    for n, item in enumerate(results, 1):
        head = f"{n}. [{item['类别']}{'·'+item['动画类型'] if item.get('动画类型') else ''}] {item['项目']}  得分 {item['得分']}"
        lines.append(head)
        lines.append(f"   路径：{item['路径']}" + (f"（{item['片段']}）" if item.get('片段') and item['路径'] and 'B肉' in item['路径'] else ''))
        lines.append(f"   描述：{item['描述']}（{item['描述来源']}）")
        if item.get('用途说明'):
            lines.append(f"   用途：{item['用途说明']}")
        if item.get('注意'):
            lines.append(f"   注意：{item['注意']}")
        extra = '，'.join(x for x in [item.get('横竖') or '', 'HDR' if item.get('HDR') else '', f"{item['时长秒']:.1f} 秒" if item.get('时长秒') else '', '未整理' if item.get('未整理') else ''] if x)
        if extra:
            lines.append(f'   画面：{extra}')
        if item.get('动画工程'):
            lines.append('   动画工程：'+'；'.join(item['动画工程']))
        for use in item['用在哪'][:3]:
            lines.append(f"   用在：{use['项目']} {use['镜头']}（{use['时间']}）口播「{use['口播'][:60]}」")
        lines.append(f"   理由：{item['匹配理由']}")
    return '\n'.join(lines)
