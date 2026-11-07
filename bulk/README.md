# bulk：机器灌的漏洞数据

**尚未实现。** 本目录现在只有本文件，0 行数据。

将来由 `scripts/import-upstream.sh`（未实现）从上游**已整理好的产物**转换而来 ——
**不做抓取**、**不手改**（下次刷新会被覆盖）。导入器开工时再补这个脚本。

## 范围：窄而准（2026-11-07 拍定）

**`bulk` 不是情报镜像。** 只导入「与已定义产品相关」的分片：

- 判据来自 `products/` 的 `[[recognize]]`（dpkg / apk / rpm 的包名）；
- 不相关的生态/年份分片直接不拉，也不入库；
- 导致的结果：公告数量从「OSV 全量 25 万」降到「我们解释得了的那一块」。

为什么不做全量：公告必须能落到 `product_id` 才能被图谱使用。全量导入的后果是
大批记录永远没有归属（模型里也没有「未归属公告」这个概念），只是把指标做大。
代价是放弃「我也存全量情报」这个卖点 —— 但那是情报库的定位，不是资产知识库的。

| 子目录 | 内容 | 来源 |
| --- | --- | --- |
| `osv/<ecosystem>/<year>.jsonl` | OSV 记录（一行一条） | OSV 导出 / 各生态 advisory |
| `distro/<distro>/<year>.jsonl` | 发行版安全 tracker | Alpine / Debian / Ubuntu / RHEL 系 |
| `cpe/dictionary-<part>.jsonl` | CPE 字典（**只用于归一映射，不进 advisory**） | NIST CPE dictionary |

约定：

- 每行一条 JSON，至少有 `id`；与 curated 同 id 时 **curated 覆盖**；
- 单文件 ≤ 20 MB（切分按年份 / 首字母）；
- 只保留 `id / aliases / affected / severity / references / published / modified`，
  **不存描述全文**（许可证 + 体量）。

手工验证用的最小样本可以放在 `bulk/` 下同名结构里（例如 `bulk/osv/npm/2026.jsonl`），
`scripts/validate.py` 会做行级 JSON 检查。
