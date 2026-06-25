# workbuddy-workspace-migration

> 解决 WorkBuddy 工作空间改名后会话消失、软删除会话占磁盘、清理后残留空目录等问题。

## 解决什么问题

| 症状 | 根因 |
|---|---|
| 工作空间改名后，所有历史会话从 UI 消失 | 四层存储（JSONL / SQLite / sessions.json / workspaces 表）路径未同步 |
| 点"删除"按钮后磁盘空间没释放 | 实际只是软删除（`deleted_at` 时间戳），文件全保留 |
| 清理完会话后磁盘上残留一堆空目录 | slug 目录和工作空间目录不随会话删除自动清理 |
| 共享机器上清理时误删了别人的数据 | 多账号共用 `~/.workbuddy/`，未按 `user_id` 过滤 |

## 包含

- `migrate.py` —— 工作空间迁移，自动修复四层存储一致性
- `purge.py` —— 物理清理（会话 / 工作空间 / 孤儿目录），带多账号安全过滤
- `SKILL.md` —— 完整存储架构文档 + 使用说明

## 快速开始

```bash
# 迁移工作空间
python scripts/migrate.py "C:\old\path" "D:\new\path" --dry-run
python scripts/migrate.py "C:\old\path" "D:\new\path"

# 清理软删除会话
python scripts/purge.py --all --dry-run
python scripts/purge.py --all

# 清理孤儿目录
python scripts/purge.py --clean-orphans
```

完整文档见 [SKILL.md](./SKILL.md)。

## 相关文章

- 公众号版（通俗）：[《我把 AI 编程助手的数据目录扒了个底朝天》](#)
- 技术博客版（深度）：[《WorkBuddy 本地存储架构剖析》](#)
