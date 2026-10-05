# auth-server — 云端认证 / 授权加密 / 升级 / 封号

单独部署在最小云服务器。职责：注册登录 JWT、**卡密激活 + 机器绑定**、违规封号、版本检查。

完整说明见仓库根目录 `docs/software-protection.md`。

## 运行
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8100
```

## 环境变量
| 变量 | 说明 |
| --- | --- |
| `XIGUA_AUTH_SECRET` | JWT 签名密钥（生产必须改） |
| `XIGUA_ADMIN_SECRET` | 管理员发卡密钥（默认等于 AUTH_SECRET） |
| `XIGUA_LICENSE_REQUIRED` | `1` 强制授权（默认）；`0` 仅登录 |
| `XIGUA_TRIAL_DAYS` | 注册试用天数，默认 7 |
| `XIGUA_MAX_MACHINES` | 默认可绑机器数，默认 2 |
| `XIGUA_BAN_THRESHOLD` | 红线封号阈值，默认 3 |
| `XIGUA_LATEST_VERSION` / `XIGUA_DOWNLOAD_URL` | 升级提醒 |

## 接口
- `POST /auth/register` `{username,password,machine_id?}` → `{token,user}`
- `POST /auth/login` → `{token,user}`
- `GET /auth/me`（Bearer，可选 `X-Machine-Id`）→ 含 `license_active`
- `POST /auth/heartbeat` → 周期校验
- `POST /license/activate` `{code,machine_id?}` → 激活卡密
- `POST /admin/licenses` Header `X-Admin-Secret` → 批量发卡
- `POST /admin/grant` → 直接延期
- `POST /admin/unbind-machines` → 清空机器绑定
- `POST /compliance/violation` / `GET /version`

## 发卡脚本
```bash
python ../scripts/gen_licenses.py --count 5 --plan pro --days 365
```
