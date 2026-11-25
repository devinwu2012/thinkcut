# 安全问题怎么报 · Security

ThinkCut 只在你自己的电脑上运行：首页和审片台只开在本机地址，程序不内置模型、不经手你的 AI 账号；豆包、蚁小二的钥匙存在你自己的机器上。

如果你发现可能被利用的安全问题（比如本机网页能被别的网站调用、钥匙会被写进项目文件或日志、某个命令会删到不该删的文件）：

1. **不要公开发 issue 写细节。** 请用本仓库 GitHub 页面上的 **Security → Report a vulnerability** 私下报告。
2. 如果那里没有报告入口，就开一个只写“有安全问题想私下说明”、不带细节的 issue，作者会联系你。
3. 写清：用的版本（根目录 `VERSION`）、复现步骤、影响。

这是个人维护的早期项目，没有承诺的响应时间；确认的问题会在更新说明（`RELEASE_NOTES.md`）里注明修复版本。

---

Please report security issues privately via GitHub **Security → Report a vulnerability**, not in public issues. Include the version from `VERSION`, steps to reproduce, and impact.
