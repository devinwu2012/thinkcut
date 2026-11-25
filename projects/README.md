# 视频项目管理（`projects/`）

每条视频一个标准文件夹，文件夹名与项目.json 的 name 完全一致。标题、封面文字等资料直接写在项目.json（不使用飞书文档），Codex 和 Claude 按同一套 Skill 整理与上传；正式发布由本人在蚁小二客户端完成。

共用音效库：程序文件夹下的 `素材库/音效素材/`，包含供 AI 读取的 JSON 索引及供人查找的目录。单片存档仍包含实际用到的音效。

## 新建与填写

```bash
bash bin/setup_mac.sh
python3 bin/project.py init "项目名"
python3 bin/project.py accounts "<项目文件夹>"
python3 bin/project.py accounts "<项目文件夹>" --fill-missing   # 已有 targets、只补缺的平台
```

文件夹包含原始素材、制作、成片、达芬奇归档和上传记录。在 `项目.json` 登记真实文件相对路径，填写标题、封面文字、简介、标签、目标账号，核对视频与封面后将 materials_confirmed 设为 true。引用文档放 documents，名称与 HTTPS 链接分开。

2026-10-23 起不再使用飞书文档：doc-export、doc-create、doc-pull、archive-note 命令已移除。旧项目里的 feishu 字段和飞书快照只作历史记录保留。

空白模板在 `templates/` 下（文件夹名带历史品牌名）。没有素材、未确认或没有启用目标账号时不会上传。字幕以最终 SRT 保存在成片目录，不会重新生成或覆盖已有字幕。

最终视频文件名必须带导出日期和项目名：`YYMMDD_项目名` 加原扩展名，例如 `261018_项目名.mov`。项目名与文件夹及索引name完全一致，保留名称自带日期和空格。日期使用UTC+8的导出完成日期；重新导出使用对应完成日期，复制、封存、上传保留已有日期。历史成片先核实原始导出文件的修改时间，不拿整理副本的时间代替。项目.json 的 assets.video 填写对应相对路径；归档、封存和上传会检查文件名。

项目名决定文件夹名；发布标题读取项目.json 的 title。封面主标题与副标题独立填写，不互相猜测或自动改写。

该示范目录含线上空白模板的维护绑定，不直接复制其中的项目.json。新项目统一通过 init 建立，自动生成独立索引和供 Codex / Claude 读取的 AGENTS.md、CLAUDE.md。

## 原始素材整理

01_原始素材下固定分为：A roll、B roll、音乐。A roll放主要口播视频及独立人声；B roll放补充视频、图片和图示；音乐收纳配乐与音效。文稿和参考链接放02_制作或项目.json的documents。文件使用`内容_用途_序号`加原扩展名，例如`内容说明_口播录制_01.mov`。先核实实际内容再命名，原名保留在清单里；本项目制作生成的动画和过程产物放02_制作。

`素材目录.md`供人查找，`素材清单.json`供AI读取，记录分类、内容、用途、新旧路径和文件摘要。新项目自动创建空清单。已有素材改名前核对工程引用，避免断链；已有归档媒体和旧交付包保持原样。

```bash
python3 bin/project.py materials-check "<项目文件夹>"
```

检查原始素材是否全部登记、分类及命名是否与清单一致、文件内容是否改变。归档前也会执行检查；内容识别和用途判断仍由人或AI核实。

## 上传到内部草稿

```bash
python3 bin/project.py upload "<项目文件夹>"
python3 bin/project.py upload "<项目文件夹>" --execute
```

第一条只读取项目.json并预检；第二条上传并保存蚁小二内部草稿。工具核对当前在线账号、实时平台字段和素材摘要，保留上传与草稿回执，相同成功输入不重复提交。缺少平台必填资料时停止，不猜分类、声明、位置等字段。需要复杂平台专用字段时由 AI 按正式 yixiaoer Skill 补充，不保证空白通用模板覆盖所有平台附加要求。

视频和封面提交到蚁小二，SRT 在本地留档。上传成功、内部草稿成功、客户端可见、正式发布是不同状态。`yxer-safe` 拒绝 publish 命令；请在客户端手动检查并发布。

上传只使用已封存交付包中的文件；工程、封面或标题改变时先重新核对并 seal。已用3秒测试素材完成真实视频/封面上传、六个平台的validate和内部草稿dry-run，并按用户选择在一个真实账号上保存过一条内部草稿；客户端已核对账号、视频、标题、简介、标签和封面，未正式发布。

抖音、YouTube、B站使用tags数组；快手、小红书、视频号将已填写标签映射为description话题HTML，该格式已通过真实CLI预检。B站需在项目.json 的 bilibili_category 填写完整分类名称、至少一个标签，程序按当前账号的实时分类查询匹配，不猜ID或raw。上传有进程锁，避免两个AI同时重复创建草稿。

## 封面文字交接

```bash
python3 bin/project.py cover-brief "<项目文件夹>"
python3 bin/project.py cover-register "<项目文件夹>" --ratio 3:4 --file "/实际制作目录/成图.jpg"
```

任务单逐字读取项目.json的主标题（两行或三行）和副标题，交给既有 thinkcut-cover Skill；制作放02，登记后成品放03。登记核对制作 spec.json 的文字及图片比例，再把路径写回项目.json。此入口不会自动生成图片或改写文案。

## 凭据

蚁小二 Key 存当前 Mac 的登录钥匙串（服务 `cn.thinkcut.yixiaoer.cli`，账户 `api-key`）。Codex 和 Claude 统一运行 `yxer-safe`。运行时配置通过匿名内存管道传给原生 CLI，不写普通 config.json，不放 NAS 或 shell 配置。新机器运行 `yxer-safe key set`，输入不回显。钥匙串访问失败时停止，不回退为明文。

## 归档和整理

已有项目先 `adopt DIR`，只增设目录和索引，保留原文件；没有批量删除命令。改项目编号/名称用 `rename DIR "新名字"`，不手动改文件夹名。做完的项目用 `finish DIR` 整个移进视频文件夹下的 `finish`，要接着改先 `reopen DIR` 移回根目录。thinkcut-edit「剪口播」 工程内部结构保持原样，避免破坏脚本与达芬奇引用。

```bash
PYTHONUTF8=1 python3 bin/project.py archive "<项目文件夹>"
python3 bin/project.py seal "<项目文件夹>"
python3 bin/project.py verify "<项目文件夹>"
```

先在索引 resolve 中填工程名和最终时间线；当前达芬奇必须已打开该工程。工具在临时工程导入最终时间线，收集所用媒体并导出独立 DRP，再恢复检验工程设置、轨道、片段、媒体路径和文件摘要，最后回到原工程。保留验收记录。实际抽看通过后记录检查帧与结论，才能 seal。

交付包保留成片带时间的文件名，包含各比例封面、既有字幕、项目资料快照及摘要清单；存放在该工程版本下的“交付包/版本号”。同一内容复用，改变后新增版本。Fusion 外链、字体和插件仍须实际抽看；原始素材默认保留。不要让两个 AI 同时写同一项目。
