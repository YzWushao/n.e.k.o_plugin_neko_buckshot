# 发布流程

按 [N.E.K.O 官方发布教程](https://project-neko.online/zh-CN/plugins/cli)整理。GitHub Release 与插件市场上架是两个步骤。

插件 ID 为 `neko_buckshot`，对应仓库：

https://github.com/YzWushao/n.e.k.o_plugin_neko_buckshot

## 准备开发环境

使用 N.E.K.O 源码环境（Python 3.11、uv、Git）。以下命令除特别说明外，都在 N.E.K.O 源码根目录执行。Steam 版可以安装成品，但打包发布使用源码版 CLI。

若尚未检出插件：

```powershell
git clone https://github.com/YzWushao/n.e.k.o_plugin_neko_buckshot.git plugin/plugins/neko_buckshot
```

插件目录自身就是 Git 仓库，不要把整个 N.E.K.O 仓库上传为插件。

## 首次投稿插件市场

1. 运行检查：

   ```powershell
   uv run neko-plugin sync neko_buckshot --clean
   uv run neko-plugin check neko_buckshot
   ```

2. 提交插件源码并推送自己的仓库，等待 GitHub Actions 中 **Verify N.E.K.O Plugin** 通过。
3. 打开 [插件市场投稿页](https://market.project-neko.cn/)，登录后填入上面的 GitHub 仓库地址。
4. 点击“读取仓库信息”，确认名称，选择分区及 1–5 个标签，填写介绍后提交首次审核。
5. 在“我的插件”查看审核结果。要求修改时，在原申请提交新 commit 的 Revision，不重复申请。

本插件面向 Windows / Steam N.E.K.O 0.9.1，介绍中请说明：普通单机、普通联机、视觉模型、分析/接管开关，以及视觉识别和近似策略的限制。

## GitHub Release

只创建 GitHub Release、不通知 Market：

```powershell
uv run neko-plugin publish github neko_buckshot
```

CLI 要求工作区干净、代码已推送、官方 `release.yml` 当前有效。它会按 `plugin.toml` 版本创建 `v<版本号>` 标签，等待官方工作流生成安装包、检查报告、发布证据。

Release 应包含：

- `neko_buckshot.neko-plugin`
- `neko_buckshot.market-release-check.txt`
- `market-evidence.json`

源码压缩包可以另外附加；不替代上述三个标准资产。

## 首次审核通过后上架

如果对应 GitHub Release 已经生成，审核通过后可在 N.E.K.O 源码根目录执行：

```powershell
uv run neko-plugin publish market https://github.com/YzWushao/n.e.k.o_plugin_neko_buckshot/releases/tag/v0.1.0
```

这一步通知 Market 读取已验证的 Release 并发布 stable 版本，不需要另传安装包。若尚未审核通过，命令不能替代首次投稿。

## 后续更新

1. 修改源码，将 `plugin.toml` 中版本号升为从未发布过的新版本；同步 `pyproject.toml` 和更新说明。
2. 如依赖有变化，运行 `sync --clean`，再运行检查。
3. 在插件目录提交并推送：

   ```powershell
   cd plugin/plugins/neko_buckshot
   git add .
   git commit -m "fix: describe the change"
   git push
   cd ../../..
   ```

4. 已完成首次审核时，执行完整发布：

   ```powershell
   uv run neko-plugin publish neko_buckshot
   ```

5. 检查 GitHub Release 的三个标准资产，再检查 Market 的对应版本和 stable 最新版本。

已发布的版本号与标签不要改指向、覆盖或重复使用。工作流失败且必须修改代码时，使用新版本号重发；单纯网络故障可重跑原工作流。

## 这个插件的依赖说明

官方发布工作流在 Linux 构建。本插件的截图只在 Windows 执行，图片处理复用 N.E.K.O 0.9.1 自带 Pillow，不把 Linux 原生二进制打包到 Windows 插件。`pyproject.toml` 中的 `N.E.K.O>=0.9.1` 是宿主依赖；独立运行测试时需要安装开发依赖中的 Pillow 与 pytest。
