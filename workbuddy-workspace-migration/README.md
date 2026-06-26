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

- 公众号版（通俗）：[《我把WorkBuddy的数据目录扒了个底朝天》](https://mp.weixin.qq.com/s/w7jeTNXqEqShMEHwQafO4w?mpshare=1&srcid=0625LqAYa1qoKkd98xv5W8V6&sharer_shareinfo=994918c7c88e9cab65710dc95766e5ed&sharer_shareinfo_first=994918c7c88e9cab65710dc95766e5ed&from=timeline&scene=2&subscene=2&sessionid=1782434751&clicktime=1782435012&enterid=1782435012&ascene=45&fasttmpl_type=0&fasttmpl_fullversion=8318234-zh_CN-zip&fasttmpl_flag=0&realreporttime=1782435012733&devicetype=android-36&version=28004a39&nettype=3gnet&abtest_cookie=AAACAA==&lang=zh_CN&countrycode=CN&exportkey=n_ChQIAhIQq3lTvf7ZbSfYKWDTacgetBL2AQIE97dBBAEAAAAAAHA/CH79YWcAAAAOpnltbLcz9gKNyK89dVj0PYJL/Sz1+s1tPOqJIAIlcndyQ01dbRNin2gBJUre07kPRWog+1qqonPK5l1JvJ4Nl53Q+Cfu53miT7pJQuQpr+MqSE7eskWKEzKZ62aJU2y4llRkIZVqsFsZ3a7WFVSCsrZt1qo4wfo5BPQwt3nWYrg33wlImzf0dddDu3c45h67VeUdaHTvjYVweMkuE9UZZ4S54ExohoiwPLPtsNd9tS0yHGy/uOk9gozV3WuvA6IV7oe5VSo8r+E/BFUs8yX+yk7EtMDUljBfN8K6uE0fqw==&pass_ticket=ThF101D5pGzxf2fPw4UdhDyLHoX3uMoZd4EksxYg9LqVLxsVudtBL6LyNyDYoeTh&wx_header=3&color_scheme=light)
- 技术博客版（深度）：[《WorkBuddy 本地存储架构剖析》](https://blog.803027.xyz/archives/workbuddy-ben-di-cun-chu-jia-gou-pou-xi-si-ceng-lian-dong-xia-de-hui-hua-ke-jian-xing-kong-zhi-yu-qian-yi-shi-jian)
