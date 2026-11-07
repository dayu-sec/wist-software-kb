# advisories：人工修正的公告

**现在为空。** 这不是占位符 —— 这个目录是**创作源**，空 = 「还没有需要修正的上游数据」。

## 这里放什么

只放**人工修正**，不放上游原始公告：

```text
advisories/curated/<osv_id>.toml
```

什么时候该写一条：

| 情况 | 例子 |
| --- | --- |
| 上游区间不适用 | 发行版回移了补丁：上游说「1.18–1.24 有洞」，但 Debian 的 1.22 已经修了 |
| 产品映射缺失 | 上游只给了 `nginx-core` 的 purl，我们没有这条别名 |
| 上游撤回了，但历史命中还引用着 | 显式记录撤回，而不是删文件 |

**不该写**：把上游数据抄一遍 —— 那是 bulk 层的活（见最后一节）。

## 一条 advisory 长什么样

```toml
osv_id     = "GHSA-xxxx-xxxx-xxxx"
aliases    = ["CVE-2024-00000"]
product_id = "sha256:…"              # 必须存在于 products/
ecosystem  = "Debian:12"
source     = "curated"               # 人工修订
affected   = [{ introduced = "1.18.0", fixed = "1.24.0-1" }]
severity   = ["CVSS_V3:…"]
references = ["https://…"]           # 只存链接，不转存全文
note       = "发行版回移了补丁，上游 OSV 的区间在这里不适用"
```

`scripts/validate.py` 会查：`product_id` 存在、`source` 在词表内、`ecosystem` 非空、
`affected` 至少有一个 `introduced` / `fixed` / `last_affected`、`references` 非空、`osv_id` 不重复。

## 还没有的部分：`bulk/`

上游全量镜像是**机器生成**的（和 `dist/` 同类）。2026-11-07 从仓里删掉了 ——
生成器 `scripts/import-upstream.sh` 还不存在，留一个空目录只会跟着进包。

等导入器开工时再建，形态大概率是：`bulk/lock.toml` 记录上游快照 id + sha256，
实际分片 gitignore、打包时生成。范围按「窄而准」：只导与 `products/` 里已定义包名相关的分片。

在那之前，本目录里的 advisory 是**孤立的**（引用 `osv_id`，但包里没有对应记录）——
校验只保证形状合法，保证不了「上游真的有这条」。
