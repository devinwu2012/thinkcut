# GitHub 仓库 About 设置

公开仓库 `devinwu2012/thinkcut` 右上角 About 的文案。由维护者用 `gh repo edit` 设置（介绍会话只写这一页，不直接改 GitHub）。改了这里要同步到 GitHub。

## 简介（Description）

GitHub 的简介只有一栏，中英写在一起（共约 150 字符，上限 350）：

```text
口播视频从文稿到成片：你 Mac 上的 Claude Code／Codex 干活，你在审片台拍板，达芬奇出片。AI-operated talking-head video workflow for macOS — your own Claude Code or Codex drafts, you approve.
```

只要一种语言时：

- 中文：`口播视频从文稿到成片：你 Mac 上的 Claude Code／Codex 干活，你在审片台拍板，达芬奇出片。`
- English: `AI-operated talking-head video workflow for macOS — your own Claude Code or Codex drafts the cut, you review and approve, DaVinci Resolve renders.`

## 主页链接（Website）

建议先留空：现在没有单独的产品主页，README 就是主页。以后有了再填；不要填个人主页或带真实名字的链接。

## Topics（10 个）

```text
video-editing, davinci-resolve, claude-code, codex, ai-agent, macos, talking-head, video-automation, subtitles, chinese
```

都是小写、连字符，GitHub 上已有人用的常见词。

## 其它勾选

- Releases：勾上（每个版本的说明在 `docs/releases/`）。
- Packages、Deployments、Environments：不勾（没有）。
- Social preview（仓库设置 → General → Social preview）：上传 `docs/images/hero-light.png`（2560×1280，2:1，GitHub 建议 1280×640 以上）。这一项只能在网页上传，`gh` 改不了。

## 设置命令（给维护者）

```bash
gh repo edit devinwu2012/thinkcut --description "口播视频从文稿到成片：你 Mac 上的 Claude Code／Codex 干活，你在审片台拍板，达芬奇出片。AI-operated talking-head video workflow for macOS — your own Claude Code or Codex drafts, you approve." --homepage "" --add-topic video-editing --add-topic davinci-resolve --add-topic claude-code --add-topic codex --add-topic ai-agent --add-topic macos --add-topic talking-head --add-topic video-automation --add-topic subtitles --add-topic chinese
```
