# 参与和反馈 · Contributing

谢谢关注 ThinkCut · 思刻。这是一个人维护的早期项目，目前只在作者自己的环境里验证过（见 README“现在的限制”）。

## 最有用的帮助：反馈

开 issue，写清：

- 你想做什么、实际发生了什么（截图或终端输出最好）；
- 版本（根目录 `VERSION`）、Mac 芯片和系统版本、用的是 Claude Code 还是 Codex；
- 卡在哪一步（装环境、建项目、审片台哪一栏、达芬奇……）。

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 想改代码

1. **先开 issue 说一下想改什么**，免得做了和计划冲突。合并请求（PR）的授权方式：ThinkCut 按 PolyForm Noncommercial 授权，作者保留商用授权的权利，所以合并你的代码前会先和你确认授权方式。
2. 准备：装测试要的包 `python3 -m pip install --user -r requirements/dev.txt`、ffmpeg-full 和 Node（见 README），下载字体 `bash bin/fetch_fonts.sh`。合并请求在 GitHub 上会自动跑同一套测试。
3. 改完跑全套离线测试，必须全过：

   ```bash
   python3 bin/test_all.py
   ```

   只改了一块可以先跑那一块，比如 `python3 bin/test_all.py cover`；提交前仍跑全套。
4. 代码检查（只查语法错、没定义的名字这类真错误）：`uvx ruff@0.17.6 check .`
5. 写法：
   - 程序按 macOS 自带的 Python 3.9 写，不要用 3.10 以后才有的语法；
   - 编辑器设置见 `.editorconfig`（Python、shell 4 空格缩进，网页 2 空格）；新写的代码一行尽量不超过 120 字；
   - 不要顺手重排、改名和这次改动无关的代码；
   - 说明和界面文字用中文，写成能照做的句子；
   - 改动用户能看到的地方，在 `RELEASE_NOTES.md` 最上面的“下一版”里加一句。
6. 每个文件夹自己的 `AGENTS.md` 写了那一块的规矩，改之前先读。

---

English: bug reports and questions are welcome as issues (include `VERSION`, your macOS version, and the step that failed). Please open an issue before sending a pull request; because of the noncommercial license, the author will confirm licensing terms with you before merging. Run `python3 bin/test_all.py` (all must pass) and `uvx ruff@0.17.6 check .` before submitting.
