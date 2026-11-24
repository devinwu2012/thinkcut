# 组件一览：12 个组件（文字类 7、卡片 5）各在“完全出现”的时刻截一帧，叠在真实口播 / 实拍画面上，拼成 样张/组件一览.jpg；另出每个组件的进场过程条（0、0.13、0.27、0.4 秒）。
# 另出 样张/步骤卡_并排.jpg：并排步骤卡说到第 1、2、3、4 步时各一帧（2026-11-21 起新片子默认）。
# 用法：python3 样张.py
from pathlib import Path
from PIL import Image, ImageDraw
from huazi import still, font, W, H
H_ = Path(__file__).resolve().parent; S = H_ / '样张'
A = Image.open(S / '_底图_口播.jpg').convert('RGBA').resize((W, H)); B = Image.open(S / '_底图_实拍.jpg').convert('RGBA').resize((W, H))
DEMO = [
 ('数字强调 number', A, dict(use='number', value='400', unit='元', label='中央换气扇＋管子 一套约')),
 ('参数卡 spec', A, dict(use='spec', title='吉博力水母地漏', rows=[dict(k='水封深度', v='50', u='mm'), dict(k='一次补水', v='0.8', u='L', hi=True), dict(k='价格', v='约 200', u='元')])),
 ('关键词 keywords', A, dict(use='keywords', items=[dict(t='散热孔'), dict(t='卡扣设计'), dict(t='一拽一卡')])),
 ('对比 compare', A, dict(use='compare', left=dict(t='5 类网', sub='千兆就到头'), right=dict(t='超 6 类', sub='能跑万兆'))),
 ('步骤卡 steps', A, dict(use='steps', 样式='并排', steps=[dict(t='水电定点位'), dict(t='吊顶前后试灯'), dict(t='选最好才下单')])),
 ('提示 note', A, dict(use='note', text='墙封之前一定要预埋')),
 ('引线标注 leader', B, dict(use='leader', point=(1180, 1500), to=(420, -380), text='散热孔', sub='变压器不闷着')),
 ('金句 statement', A, dict(use='statement', lines=['墙一封', '就没机会了'], keys=['没机会'])),
 ('口播大字 koubo', A, dict(use='koubo', lines=['为什么供应商老讲材料', '却很少讲【工艺】？'])),
 ('评论弹窗 comment', A, dict(use='comment', items=[dict(t='装完一年就漏了', mood='angry', xy=(430, 1500)), dict(t='师傅说没问题啊', xy=(1720, 1700))])),
 ('及格线 jigexian', A, dict(use='jigexian', bg=True, title='网线怎么选', up='预算够就升级', rows=[dict(t='5 类线', sub='达不到，别用', level='below'), dict(t='6 类线', sub='至少要用到这一档', level='on'), dict(t='超 6 类线', sub='有条件，最好选它', level='above', tag='万兆')])),
 ('网速对比 wangsu', A, dict(use='wangsu', bg=True, title='网速对比', bars=[dict(t='外网宽带', v=2000, show='2000 兆', sub='北京很多家已经是 2000 兆'), dict(t='5 类线', v=100, show='最多 100 兆', sub='接不住 2000 兆的宽带', dim=True, strike=True), dict(t='内网（超 6 类）', v=10000, show='万兆', hi=True, sub='家里设备之间互传')], race=dict(title='传一部 50GB 的 4K 电影', lanes=[dict(t='千兆', time='约 7 分钟', sec=430), dict(t='万兆', time='约 45 秒', sec=45, hi=True)], uses=['照片备份', '4K 影音', 'NAS 剪辑素材']))),
]
tiles = []
for name, bg, spec in DEMO:
    spec.update(_at=0.0, _until=3.0); im = Image.alpha_composite(bg.copy(), still(spec, 2.2)).convert('RGB').resize((540, 720))
    d = ImageDraw.Draw(im); d.rectangle((0, 0, 540, 64), fill=(10, 10, 12)); d.text((18, 12), name, font=font(34, 'medium'), fill='white'); tiles.append(im)
sheet = Image.new('RGB', (540 * 4 + 30, 730 * 3), (10, 10, 12))
for i, t in enumerate(tiles): sheet.paste(t, ((i % 4) * 550, (i // 4) * 730))
sheet.save(S / '组件一览.jpg', quality=92)
row = Image.new('RGB', (540 * 4 + 30, 720), (10, 10, 12))
spec = dict(DEMO[0][2]); spec.update(_at=0.0, _until=3.0)
for k, t in enumerate((0.0, 0.13, 0.27, 0.6)):
    im = Image.alpha_composite(A.copy(), still(spec, t)).convert('RGB').resize((540, 720)); ImageDraw.Draw(im).text((18, 12), f'{t:.2f} 秒', font=font(34, 'medium'), fill='white'); row.paste(im, (k * 550, 0))
row.save(S / '进场节奏.jpg', quality=92)
spec = dict(use='steps', 样式='并排', _at=0.0, _until=6.0, steps=[dict(t='水电定点位', _at=0.0), dict(t='吊顶前后试灯', _at=1.0), dict(t='选最好才下单', _at=2.0), dict(t='跑现场3–5次', _at=3.0)])
row = Image.new('RGB', (540 * 4 + 30, 720), (10, 10, 12))
for k, t in enumerate((0.6, 1.6, 2.6, 4.0)):
    im = Image.alpha_composite(A.copy(), still(spec, t)).convert('RGB').resize((540, 720)); ImageDraw.Draw(im).text((18, 12), f'说到第 {k + 1} 步', font=font(34, 'medium'), fill='white'); row.paste(im, (k * 550, 0))
row.save(S / '步骤卡_并排.jpg', quality=92); print('样张/组件一览.jpg、样张/进场节奏.jpg、样张/步骤卡_并排.jpg')
