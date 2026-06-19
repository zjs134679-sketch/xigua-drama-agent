# auth-server — 云端认证 / 升级 / 封号服务

单独部署在最小云服务器（几十元/月）。职责：注册 / 登录 / 发 JWT、违规累计封号（权威）、版本检查。

## 运行
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8100
```

## 环境变量
| 变量 | 说明 |
| --- | --- |
| `XIGUA_AUTH_SECRET` | JWT 签名密钥（生产必须改） |
| `XIGUA_BAN_THRESHOLD` | 红线封号阈值，默认 3 |
| `XIGUA_LATEST_VERSION` / `XIGUA_DOWNLOAD_URL` | 升级提醒用 |

## 接口
- `POST /auth/register` `{username,password}` → `{token,user}`
- `POST /auth/login` → `{token,user}`
- `GET /auth/me`（Bearer）→ 用户信息
- `POST /compliance/violation` `{username,level}` → `{violation_count,banned}`（封号权威）
- `GET /version` → `{latest,url}`

> 主程序（backend）的本地 `enforce` 是缓存版；正式以本服务的 `banned` 为准。后续把 backend 命中红线→调用本服务 `/compliance/violation`，并在登录/每次生成时校验 `banned`。
