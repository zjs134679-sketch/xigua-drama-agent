#!/usr/bin/env python3
"""批量生成授权卡密（调用 auth-server 管理接口）。

用法：
  set XIGUA_ADMIN_SECRET=你的密钥
  python scripts/gen_licenses.py --count 10 --plan pro --days 365

默认请求 http://127.0.0.1:8100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="生成西瓜短剧Agent 授权卡密")
    parser.add_argument("--url", default=os.environ.get("XIGUA_AUTH_SERVER_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--secret", default=os.environ.get("XIGUA_ADMIN_SECRET") or os.environ.get("XIGUA_AUTH_SECRET", "dev-secret-change-me-in-prod"))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--plan", default="pro", choices=["pro", "studio", "trial"])
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--max-machines", type=int, default=2)
    parser.add_argument("--note", default="")
    parser.add_argument("--out", default="", help="写入文本文件（每行一个卡密）")
    args = parser.parse_args()

    payload = {
        "count": args.count,
        "plan": args.plan,
        "days": args.days,
        "max_machines": args.max_machines,
        "note": args.note or None,
    }
    req = urllib.request.Request(
        f"{args.url.rstrip('/')}/admin/licenses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Secret": args.secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"请求失败: {exc}", file=sys.stderr)
        return 1

    codes = data.get("codes") or []
    print(f"已生成 {len(codes)} 张卡密 | plan={data.get('plan')} days={data.get('days')}")
    for code in codes:
        print(code)
    if args.out:
        Path = __import__("pathlib").Path
        Path(args.out).write_text("\n".join(codes) + "\n", encoding="utf-8")
        print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
