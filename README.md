# workbuddy-skills

> WorkBuddy 实用 Skill 合集 —— 围绕 [WorkBuddy](https://workbuddy.com) AI 编程助手的扩展能力、数据运维、工作流自动化等场景，分享可复用的技能包。

每个子目录是一个独立的 Skill，包含 `SKILL.md`（说明文档）和 `scripts/`（可执行脚本）。可以单独克隆使用，也可以整体下载后挑选需要的。

## 已收录

| Skill | 一句话说明 | 文档 |
|---|---|---|
| [workbuddy-workspace-migration](./workbuddy-workspace-migration) | 工作空间迁移 + 软删除会话物理清理 + 空目录残留清理 | [SKILL.md](./workbuddy-workspace-migration/SKILL.md) |

<!-- 后续新增 skill 在此处追加一行 -->

## 为什么会有这个仓库

WorkBuddy 的本地数据由四层联动存储组成（JSONL 会话内容、SQLite 元数据库、sessions.json 映射、workspaces 注册表）。日常使用中会遇到几类典型问题：

- 改了工作空间目录名，历史会话从 UI 消失
- "删除"按钮其实是软删除，磁盘空间永远不释放
- 多账号共享同一台机器，清理时容易误删别人数据
- 清理后残留空目录，常规方式删不掉

这个仓库里的 Skill 就是针对这些场景的解决方案，每个都是实战踩坑后总结出来的。

详见每个 Skill 自己的 `SKILL.md`。

## 使用方式

### 方式一：直接下载需要的 Skill

```bash
# 只下载某一个 skill（以 workbuddy-workspace-migration 为例）
git clone --depth 1 https://github.com/<your-name>/workbuddy-skills.git
cd workbuddy-skills/workbuddy-workspace-migration
```

### 方式二：作为 WorkBuddy Skill 安装

把对应子目录复制到本地 skills 目录即可：

```bash
# macOS / Linux
cp -r workbuddy-workspace-migration ~/.workbuddy/skills/

# Windows (Git Bash)
cp -r workbuddy-workspace-migration /c/Users/<you>/.workbuddy/skills/
```

之后在 WorkBuddy 中就可以通过 Skill 系统调用相关能力。

### 方式三：直接用脚本（不依赖 WorkBuddy）

每个 Skill 的 `scripts/` 目录下的 `.py` 文件都可以独立运行，纯 Python 标准库，无第三方依赖。

```bash
cd workbuddy-workspace-migration/scripts
python migrate.py --help
python purge.py --help
```

## 贡献

欢迎提 Issue 反馈使用问题，或 PR 贡献新的 Skill。

新增 Skill 时请保持目录结构一致：

```
your-skill-name/
├── SKILL.md          # 必需，文档
└── scripts/          # 可选，脚本
    ├── foo.py
    └── bar.py
```

## 相关文章

部分 Skill 配套有深度文章，详见各子目录 README 或 SKILL.md 中的链接。

## License

MIT
