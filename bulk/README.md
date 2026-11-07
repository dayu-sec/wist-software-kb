# bulk：机器灌的漏洞数据

本目录由 `scripts/refresh-bulk.sh`（CI 定时任务）生成，**不要手改**——下次刷新会被覆盖。

| 子目录 | 内容 | 来源 |
| --- | --- | --- |
| `osv/<ecosystem>/<year>.jsonl` | OSV 记录（一行一条） | OSV 导出 / 各生态 advisory |
| `nvd/<year>.jsonl` | NVD 记录（OSV 化后的必要字段） | NVD JSON feeds |
| `distro/<distro>/<year>.jsonl` | 发行版安全 tracker | Alpine / Debian / Ubuntu / RHEL 系 |
| `cpe/dictionary-<part>.jsonl` | CPE 字典（**只用于归一映射，不进 advisory**） | NIST CPE dictionary |

约定：

- 每行一条 JSON，至少有 `id`；与 curated 同 id 时 **curated 覆盖**；
- 单文件 ≤ 20 MB（切分按年份 / 首字母）；
- 只保留 `id / aliases / affected / severity / references / published / modified`，
  **不存描述全文**（许可证 + 体量）。

手工验证用的最小样本可以放在 `bulk/` 下同名结构里（例如 `bulk/osv/npm/2026.jsonl`），
`scripts/validate.py` 会做行级 JSON 检查。
