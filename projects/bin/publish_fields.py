"""根据实时平台结构映射已填写的发布资料。动态分类只从CLI真实查询选取。"""
import copy
import html
import re
import uuid

from project import command


def content_form(meta, target, schema, sources):
    platform = target['platform']
    properties = schema['businessFields']
    result = copy.deepcopy(schema['minimalTemplate']['publishArgs']['accountForms'][0]['contentPublishForm'])
    extra = (meta.get('platform_extra') or {}).get(platform) or {}
    # 2026-11-21：各平台简介统一走 platform_extra.<平台>.description（copy-apply 的 platform_descriptions 写进来），没有才用顶层 description；话题照旧拼在后面
    desc = extra.get('description') or meta.get('description', '')
    for key in ('title', 'description'):
        value = desc if key == 'description' else meta.get(key, '')
        if value:
            if key not in properties:
                raise RuntimeError(platform + ' 没有字段 ' + key)
            result[key] = value
    # 2026-11-01 用户：按平台加活动话题蹭流量 → 项目.json 的 platform_topics 按平台追加（去重），其余照旧
    tags = list(dict.fromkeys(list(meta.get('tags', [])) + list(meta.get('platform_topics', {}).get(platform, []))))
    def topic_html():
        body = html.escape(desc).replace('\n', '<br/>')
        topics = ''.join('<topic text="' + html.escape(t, quote=True) + '">#' + html.escape(t) + '</topic>' for t in tags)
        return ('<p>' + body + '</p>' if body else '') + '<p>' + topics + '</p>'
    if tags:
        if 'tags' in properties:
            result['tags'] = tags
            if platform == '抖音' and 'description' in properties:
                # 2026-10-24 用户：抖音只传 tags 时客户端里看不到话题 → 话题同时写进描述（和快手/小红书/视频号同一格式）
                result['description'] = topic_html()
                sources.setdefault('tag_mapping', {})[platform] = 'tags + description.topic_html（客户端只显示描述里的话题）'
        elif platform in ('快手', '小红书', '视频号') and 'description' in properties:
            # 与3.2.28三个平台真实validate返回的标准HTML一致；不伪造结构化话题ID/raw。
            body = html.escape(desc).replace('\n', '<br/>')
            topics = ''.join('<topic text="' + html.escape(t, quote=True) + '">#' + html.escape(t) + '</topic>' for t in tags)
            result['description'] = ('<p>' + body + '</p>' if body else '') + '<p>' + topics + '</p>'
            sources.setdefault('tag_mapping', {})[platform] = 'description.topic_html; CLI 3.2.28 live validate contract'
        else:
            raise RuntimeError(platform + ' 不支持统一标签字段，须按平台Skill处理；尚未上传文件。')
    if platform == '哔哩哔哩':
        selected = meta.get('bilibili_category', '').strip()
        if not selected:
            raise RuntimeError('项目.json 的 bilibili_category 未填写；先选定真实分类，尚未上传文件。')
        args = ['yxer-safe', 'query', 'categories', target['account_id'], '--type', 'video', '--paths', '--json']
        response = command(args)
        sources.setdefault('bilibili_categories', {})[target['account_id']] = {'command': args, 'response': response}
        def normal(value):
            return re.sub(r'\s*[/＞>]\s*', '/', value.strip())
        matches = [p for p in response['data']['paths'] if normal(p['path']) == normal(selected)]
        if len(matches) != 1:
            choices = '，'.join(p['path'] for p in response['data']['paths'])
            raise RuntimeError('B站分类未唯一匹配当前查询结果。请在项目.json 的 bilibili_category 填写完整分类名：' + choices)
        result['category'] = copy.deepcopy(matches[0]['category'])
    if platform == 'Youtube' and str(meta.get('youtube_category', '')).strip():
        # 2026-10-24 用户：YouTube 分类要选“DIY 和生活百科”（ID 26），不能用默认 22（人物与博客）
        cid = str(meta['youtube_category']).strip()
        allowed = properties.get('category', {}).get('enum', [])
        if allowed and cid not in allowed:
            raise RuntimeError('YouTube 分类 ID 不在当前平台结构里：' + cid + '（可选 ' + '、'.join(allowed) + '）')
        result['category'] = cid
    # 2026-10-24 用户逐项定的各平台细节（每次都写）：不标 AI 声明、不放合集、不加位置；下载/保存/同城/同框全部允许。
    # 项目.json 的 platform_extra 可按平台覆盖，例如 {"快手": {"allow_download": false}}。
    defaults = {'抖音': {'allow_save': 1}, '快手': {'nearby_show': True, 'allow_download': True, 'allow_same_frame': True},
                'Youtube': {'madeForKids': False, 'containsSyntheticMedia': False, 'license': 'youtube', 'embeddable': True, 'visible': 'public'}}
    for key, value in {**defaults.get(platform, {}), **extra}.items():
        if key in properties and key != 'description':      # 简介上面已处理（含话题），不能被这里的纯文本盖掉
            result[key] = value
    # 定时发布（upload --schedule，2026-11-22）：平台结构里有 scheduledTime 的写毫秒时间戳；没有的（YouTube）不写，记下“点发布即发”
    if meta.get('schedule'):
        if 'scheduledTime' in properties:
            result['scheduledTime'] = meta['schedule']['ms']
            sources.setdefault('schedule', {})[platform] = meta['schedule']['time'] + ' 定时'
        else:
            result.pop('scheduledTime', None)
            sources.setdefault('schedule', {})[platform] = '不支持定时，点发布即发'
    if platform == '视频号' and 'short_title' in properties and not result.get('short_title'):
        # 视频号短标题（客户端限 16 字）：用封面主标题两行连起来，超长就不填
        short = ''.join(meta.get('cover_main', '').split())
        if 0 < len(short) <= 16:
            result['short_title'] = short
    # 2026-10-24 用户：视频都是原创（IP 配置 发布.原创）。项目.json 的 original 为 true 时，按平台文档声明原创：
    # 视频号 createType 1（默认 2 非原创）、小红书 createType 1（默认 0 不申明）、B站 createType 1（自制）
    if meta.get('original') and platform in ('视频号', '小红书', '哔哩哔哩') and 'createType' in properties:
        result['createType'] = 1
    # 上传视频前先阻断明显缺失的业务必填项，避免传完大文件才发现分类或标签为空。
    for key, spec in properties.items():
        if key in ('video', 'accountForms', 'cover', 'coverKey'):
            continue
        value = result.get(key)
        if spec.get('required') is True and (value is None or isinstance(value, (str, list, dict)) and not value):
            raise RuntimeError(platform + ' 缺少必填资料 ' + key + '；尚未上传文件。')
        if isinstance(value, list) and len(value) < spec.get('minItems', 0):
            raise RuntimeError(platform + ' 资料 ' + key + ' 数量不足；尚未上传文件。')
        # 2026-11-12《水电》：预检通过、2.7GB 传完才在存草稿时被拦（抖音/B站话题最多 10 个、小红书标题最多 20 字）
        # → 按实时平台结构的上限先查，所有平台的问题攒齐由 upload 一次报出，不传文件
        if isinstance(value, (str, list)) and value:
            limit = spec.get('maxLength') if isinstance(value, str) else spec.get('maxItems')
            if limit and len(value) > limit:
                unit = '字' if isinstance(value, str) else '个'
                sources.setdefault('limit_problems', []).append(f'{platform} {key} {len(value)} {unit}，最多 {limit} {unit}')
    return result


def verify_draft_preview(preview, payload):
    """只接受已实际观察到的CLI内部草稿请求，不接受无关嵌套isDraft标记。"""
    data = preview.get('data', {})
    request = data.get('request', {})
    if preview.get('ok') is not True or preview.get('action') != 'draft.save.dry-run' or data.get('dryRun') is not True:
        return False
    if request.get('isDraft') is not True or request.get('publishType') != 'video' or request.get('platforms') != payload.get('platforms'):
        return False
    expected = payload.get('publishArgs', {}).get('accountForms', [])
    actual = request.get('publishArgs', {}).get('accountForms', [])
    if not expected or len(expected) != len(actual):
        return False
    for left, right in zip(expected, actual):
        if left.get('platformAccountId') != right.get('platformAccountId'):
            return False
        if left.get('contentPublishForm', {}).get('title') != right.get('contentPublishForm', {}).get('title'):
            return False
        if any(left.get(key, {}).get('key') != right.get(key, {}).get('key') for key in ('video', 'cover')):
            return False
    return True


def saved_draft_id(response):
    """兼容文档taskSetId及3.2.28实际返回的data.data UUID；业务失败不算保存成功。"""
    if response.get('ok') is not True or response.get('action') not in (None, 'draft.save'):
        return None
    data = response.get('data')
    if not isinstance(data, dict) or data.get('statusCode', 0) != 0:
        return None
    value = data.get('taskSetId')
    if isinstance(value, str) and value.strip():
        return value
    value = data.get('data')
    if not isinstance(value, str) or data.get('statusCode') != 0:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None
