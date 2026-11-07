# wist-software-kb

wist 的**软件知识库**——产品、版本、外部标识、漏洞公告。

- **仓 = 创作源**：人写的那部分（产品归一、别名、purl/CPE 映射、区间修正）在这里评审、blame、回滚；
- **包 = 分发单元**：消费方不读本仓目录树，只读 `wist-software-kb-<version>.tar.gz`（带 sha256 与签名）；
- **DB = 检索**：计算区导入本地 Postgres，按「产品 × 版本区间」匹配（文件树做不了这件事）。

> 设计文档：[`wist-design/doc/design/center/models/software-knowledge-repo.md`](https://github.com/dayu-sec/wist-design/blob/main/doc/design/center/models/software-knowledge-repo.md)
> 与 [`software-normalization-and-vuln-enrichment.md`](https://github.com/dayu-sec/wist-design/blob/main/doc/design/center/models/software-normalization-and-vuln-enrichment.md)。
>
> 本仓是**公共知识库**：不含任何客户数据（主机、IP、安装路径、命中结果）。客户私有产品定义走
> 本地 overlay，从不回写。

## 目录

```text
version.txt                     # 版本权威（gx adm v_patch / v_feat 改它，tag 也取它）
kb.toml                         # schema_version / kb_version / 数据源与许可证
products/<eco>/<product>.toml   # curated：产品（归一后的身份，一对象一文件）
advisories/<source>/…           # curated：人工修正、供应商公告
bulk/                           # bulk：OSV / NVD / 发行版 JSON 分片（CI 生成，别手改）
schemas/                        # 文件格式（JSON Schema）
examples/                       # 格式示例（不参与校验与打包）
scripts/                        # validate.py / package.sh / gen-signing-key.sh
keys/                           # 内容签名公钥（私钥只在 CI secret）
_gal/                           # gx 工作区：gx adm v_patch | v_feat | tag_alpha | tag_beta | tag_stable
```

本地入口是 `Makefile`：`make validate` / `make dry-run` / `make pack`（详见下方 CI 一节）。

## 两级

| 层 | 谁写 | 形态 | 覆盖关系 |
| --- | --- | --- | --- |
| **curated** | 人（PR） | 一对象一文件 TOML | **覆盖** bulk |
| **bulk** | CI 定时拉 OSV / NVD / 发行版 tracker | 按源 + 年份/首字母分片 JSONL | 被 curated 覆盖 |

同一个 `osv_id` 两边都有时，curated 胜（人工修正优先，PR 里要写理由）。

## 提交一个新软件

1. 加 `products/<ecosystem>/<vendor>/<product>.toml`（照 `examples/product.toml`）；
2. `product_id` 用**确定性 id**：`sha256:` + `sha256(规范化 purl 去版本)[:32]`，校验脚本会重算比对；
3. `python3.12 scripts/validate.py`（或 `make validate`）；
4. PR。

**改 `canonical_name` / `external_ids` 会让 `product_id` 变化** —— 下游客户域的安装记录会悬空。
这种变更必须显式声明（PR 标题带 `rename:`，并在描述里写清从旧 id 到新 id 的映射）。

## 打包与消费

```sh
./scripts/package.sh --dry-run          # 预览
./scripts/package.sh                    # → dist/wist-software-kb-<ver>.tar.gz + .sha256 (+ .sig)
```

计算区侧：

```sh
wist-atlas kb import --pack /path/wist-software-kb-<ver>.tar.gz [--overlay /etc/wist-kb/local]
```

导入是幂等的：同一包重复导入结果一致；包里的 `kb_version` 会记到命中结果上，
用于回答「上周没事、这周有了，是机器变了还是知识库变了」。

## 许可证与出处

- 本仓内容：Apache-2.0（见 `LICENSE`）；
- bulk 层按源记录许可证（见 `kb.toml` 的 `[sources]`）：**只存 id / 区间 / 链接，不转存全文**；
- 引用外部数据时保留 `source` 与 `references`，这是 CI 闸门的一部分。

## CI

| 工作流 | 触发 | 做什么 |
| --- | --- | --- |
| `ci.yml` | PR / push | `scripts/validate.py`（schema、引用完整性、区间语法、id 稳定性、体量） |
| `release.yml` | tag `v*.*.*` | 校验 + 打包 + 签名 + 上传 GitHub Release |

本地没有 Python 3.11+ 时：`brew install python@3.12`，用 `python3.12 scripts/validate.py`。

## 发版

版本权威是 `version.txt`；三个通道 `alpha → beta → main`（alpha/beta 发成 prerelease，stable 才占 latest）：

```sh
make validate                      # 先过闸门
gx adm v_feat                      # 或 v_patch：改 version.txt（内容新增用 feat，修数据用 patch）
gx adm tag_alpha                   # 或 tag_beta / tag_stable → 推 tag，CI 打包发版
```

签名私钥只在 CI secret `KB_SIGNING_KEY`（PKCS#8 PEM）。没配 secret 时 CI 仍会发包，
但包**没有 `.sig`** —— 配了 `keys/kb-signing.pub.pem` 的消费方会拒收。
