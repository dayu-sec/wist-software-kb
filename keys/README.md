# 内容签名公钥

`kb-signing.pub.pem` 放**知识库内容包**的验签公钥（`scripts/gen-signing-key.sh` 生成）。

- 私钥只在 CI secret（`KB_SIGNING_KEY`）里，**永不进仓**；
- 计算区（`wist-atlas kb import`）用这里的公钥验 `*.tar.gz.sig`；
- 换钥匙 = 一次显式的密钥轮换：旧公钥保留一个版本，直到所有计算区都升级。

现在这个文件为空占位：第一次正式发布前执行

```sh
./scripts/gen-signing-key.sh
# 私钥 → 存进 GitHub Actions secret `KB_SIGNING_KEY`
# 公钥 → 提交这里
```
