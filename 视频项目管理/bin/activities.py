"""发布前查各平台正在办的活动和相关话题（只读查询，不挂、不发布）。2026-11-01 用户：发布时蹭平台活动和话题的扶持流量。
结果写 02_制作/平台活动候选.json，供用户挑；挑中的话题写进 项目.json 的 platform_topics，抖音挑战、视频号活动对象写进 platform_extra。"""
from datetime import date
import re

from project import command, write_json

# 和这个 IP 内容相关的词（配置档 发布.领域词，如装修、家装……）：项目话题之外再加这些，活动名、说明、标签里出现才算相关
from project import cutdesk_config
DOMAIN = tuple(cutdesk_config.load().get('发布.领域词'))
# 各平台怎么参加：快手、小红书、B 站视频表单里没有活动字段，带上活动话题投稿；抖音有 challenge 字段；视频号有 activity 字段
JOIN = {'抖音': '带上活动话题（也可挂 challenge）', '视频号': '挂 activity 字段', '快手': '带上活动话题投稿', '小红书': '带上活动话题投稿', '哔哩哔哩': '带上活动话题投稿'}


def _end(text, today):
    """'11.01' '12-01' '2026.11.15' → date；认不出返回 None（不当作过期）"""
    nums = [int(x) for x in re.findall(r'\d+', str(text or ''))]
    try:
        if len(nums) >= 3: return date(nums[0], nums[1], nums[2])
        if len(nums) == 2:
            d = date(today.year, nums[0], nums[1])
            return d if (today - d).days < 180 else date(today.year + 1, nums[0], nums[1])   # 跨年：1 月的截止日期在 12 月看
    except ValueError:
        return None
    return None


def _items(resp):
    data = resp.get('data')
    return (data.get('list') if isinstance(data, dict) else data) or []


def _text(item):
    raw = item.get('raw') or {}
    parts = [item.get('yixiaoerName'), item.get('yixiaoerDesc'), raw.get('topic_description'), raw.get('activity_description'), ' '.join(item.get('tags') or [])]
    return ' '.join(str(x) for x in parts if x)


def candidates(d, meta, today=None, run=command):
    today = today or date.today()
    domain = DOMAIN if d is None else tuple(cutdesk_config.load(project=d).get('发布.领域词'))      # 按这条片子的 IP
    words = [w for w in dict.fromkeys(list(meta.get('tags', [])) + list(domain)) if w]
    out = {'date': today.isoformat(), 'keywords': words, 'platforms': {}}
    for t in meta.get('targets', []):
        name, acc = t['platform'], t['account_id']
        if name not in JOIN: continue                                             # YouTube 等：蚁小二查不了活动和话题
        row = {'how_to_join': JOIN.get(name, ''), 'activities': [], 'topics': [], 'errors': []}
        try:
            for it in _items(run(['yxer-safe', 'query', 'activities', acc, '--type', 'video', '--json'])):
                end = _end(it.get('end_date'), today)
                if end and end < today: continue                                   # 已结束
                hit = [w for w in words if w in _text(it)]
                if not hit: continue                                               # 和本片无关
                row['activities'].append({'name': it.get('yixiaoerName'), 'desc': it.get('yixiaoerDesc') or '', 'start': it.get('start_date', ''),
                                          'end': it.get('end_date', '') or '未写', 'hit': hit, 'topic': it.get('yixiaoerName'), 'object': it})
        except RuntimeError as exc:
            row['errors'].append('活动：' + str(exc))
        seen = set()
        for w in meta.get('tags', [])[:4]:                                         # 话题按本片前几个话题搜，带播放量的排前
            try:
                for it in _items(run(['yxer-safe', 'query', 'challenges', acc, '--query', w, '--json'])):
                    n = it.get('yixiaoerName')
                    if n and n not in seen:
                        seen.add(n); row['topics'].append({'name': n, 'views': it.get('viewNum') or 0, 'query': w, 'object': it})
            except RuntimeError as exc:
                row['errors'].append(f'话题（{w}）：' + str(exc)); break
        row['topics'].sort(key=lambda x: -x['views']); row['topics'] = row['topics'][:10]
        out['platforms'][name] = row
    write_json(d / '02_制作' / '平台活动候选.json', out)
    return out


def summary(out):
    """给人看的摘要：每个平台相关活动、播放量最高的话题"""
    lines = []
    for name, row in out['platforms'].items():
        acts = '；'.join(f"{a['name']}（{a['start']}–{a['end']}）" for a in row['activities']) or '无相关活动'
        tops = '、'.join(f"{t['name']}({t['views']})" for t in row['topics'][:5]) or '查不到'
        lines.append(f"{name}｜活动：{acts}｜话题：{tops}｜参加方式：{row['how_to_join']}" + (f"｜出错：{'；'.join(row['errors'])}" if row['errors'] else ''))
    return lines
