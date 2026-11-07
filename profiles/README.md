# profiles：部署画像

一个文件 = **一个产品在一种部署形态下「怎么管」**。

## 为什么和 `products/` 分开

同一个 postgresql：

| | systemd 主机 | k8s |
| --- | --- | --- |
| 升级方式 | `restart`（主版本还要 `pg_upgrade`） | **滚动替换 Pod** |
| 配置来源 | `/etc/postgresql/…` 本机文件 | ConfigMap / Secret |
| 暴露范围 | 直接对网段暴露 | 只对集群内，外部走 Ingress |
| 版本来源 | 包版本 | 镜像 tag |

这些差异**不是产品属性，是环境属性**。放进知识库主表，客户一不一样就改不动 —— 所以拆开：

```
products/    产品固有：它是谁、叫什么（判据）、什么类别、默认端口
profiles/    部署画像：认出它之后怎么管 —— 随装法/环境变
```

实测证据：把 `path` / `unit` 判据放进 profile 后，`nginx/deb` 也命中了 Alpine 容器
（因为 `/usr/sbin/nginx` 本来就不随装法变）—— 说明**判据是产品固有的**，只有治理策略是部署相关的。

---

## 文件形状

```toml
product_id = "sha256:…"      # 指向 products/，一个产品可以有多个 profile
form       = "default"       # default | deb | rpm | apk | tarball | container | macos | k8s

[version_probe]
kind     = "package"         # package | binary | image_tag | path_regex | none
command  = []                # kind=binary
stderr   = true              # nginx -v / java -version 输出在 stderr
path     = ""                # kind=path_regex
pattern  = ""                # kind=path_regex
fallback = { kind = "image_tag" }

[runtime]
bind_scope = "public"        # public | internal | loopback | none

[governance]
upgrade_mode       = "reload"     # restart | reload | rolling | none | unknown
config_paths       = []
data_paths         = []
requires_privilege = "root"

[observability]
log_faces    = ["DatabaseService"]   # 引用 wist-knowledge/catalog.toml 的 family id
metric_faces = ["HostMetrics"]
```

`(product_id, form)` 必须唯一 —— 同一个产品的同一形态只能写一次。

## 什么时候该新建一个 profile

只在**策略真的不同**时新建。判据不同不是理由（判据在产品层）。

- ✅ 加：`postgresql/k8s.toml` —— 升级从 restart 变 rolling，配置不落本机
- ❌ 不加：`nginx/deb` 和 `nginx/apk` —— 治理完全一样，只是包名不同

## 纪律

1. **`upgrade_mode` 要谨慎**：写 `restart` 意味着「这个软件可以随便重启」，这在
   有状态服务上是错的。postgres 的 `restart` 只针对小版本；主版本是 `pg_upgrade`，
   现在模型表达不了，先写在 `note` 里。
2. **`config_paths` 是「本机配置文件」**，不是「配置在哪」。k8s 里它是 ConfigMap，
   所以那个 profile 里是空数组 —— 空不是不知道，是**不适用**。
3. **`bind_scope` 是部署决定的，不是产品决定的**：同一个 postgres 在测试环境可能只听
   回环，生产对外。所以它在 profile 里，不在 products 里。
