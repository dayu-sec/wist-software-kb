# 更新日志

本文件记录 `wist-software-kb` 的所有重要变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-07

首版骨架。**内容很少，纪律先立住** —— 后面 bulk 层接入 OSV / NVD 时靠的就是这些约束。

### 新增

- 仓结构：`products/`（curated 产品）、`advisories/`（curated 公告修正）、`bulk/`（CI 拉的原始分片）、
  `scripts/`、`keys/`。
- 两个示范产品：`nginx`（deb）、`postgresql`（deb）。`product_id` 由 purl 确定性派生，
  不是手写编号。
- `scripts/validate.py`：schema、引用完整性、区间语法、`product_id` 重算一致性、体量（GitHub 硬限）。
- `scripts/package.sh`：白名单打包 → `dist/wist-software-kb-<ver>.tar.gz` + `.sha256`（+ `.sig`），
  包内自带 `manifest.json`（版本、内容摘要、`kb_version`、各层计数、commit）。
  **确定性构建**：同一 commit 重建的包逐字节一致（mtime 钉死、条目 `LC_ALL=C sort`、
  `gzip -n`、`created_at` 取提交时间或 `SOURCE_DATE_EPOCH`）—— 否则 `pack_sha256` 会变成
  「谁在哪天签的」，而不是「是不是同一份内容」。
- `scripts/gen-signing-key.sh`：Ed25519 密钥对（私钥进 CI secret，公钥进 `keys/`）。
- CI：PR/push 跑校验 + 打包 dry-run；tag `v*.*.*` 打包、签名、发 GitHub Release。
- `Makefile`：`validate` / `dry-run` / `pack` / `signing-key` / `clean`。
- `_gal/`：`gx adm v_patch|v_feat|tag_alpha|tag_beta|tag_stable`；版本权威仍是 `version.txt`。

### 约定（不是功能，但会被下游依赖）

- **仓是创作源，包是分发单元，DB 是检索**：消费方（`wist-atlas`）只按包导入，不读目录树。
- **KB 是公共的，客户数据是私有的**：本仓不含任何主机、IP、安装路径、命中结果；
  客户侧的自定义产品走本地 overlay，单向流入，永不回写。
- **`product_id` / `version_id` / `advisory_id` 必须可重算**，改名走 `merged_into` 而不是就地改 id。
- bulk 层**只存 id / 区间 / 链接**，不转存公告全文（合规与体量）。
- 版本比较必须用生态比较器（dpkg 的 `~` 与 epoch、rpm 段序、semver），禁止字符串比较。
