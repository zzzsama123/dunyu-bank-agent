"""种子数据：一个可讲完整故事的主用户。

- 近 3 个月 120 条消费流水（8 个分类，含 3 条异常样本）
- 今日已发生一笔 500 元转账（用于演示"再转 800 → 累计 1,300 → 红色"）
- 5 个订阅（3 个长期未用）、6 个理财产品、2 个持仓、1 张信用卡
所有金额单位：分。
"""

from __future__ import annotations

import random
from datetime import timedelta

from . import bankcore, config

TARGET_BALANCE = 1_234_567  # 12,345.67
TODAY_TRANSFER = 50_000     # 500.00

MERCHANTS = {
    "餐饮": [("星巴克", 35_00, 68_00), ("海底捞", 180_00, 420_00), ("美团外卖", 22_00, 65_00),
             ("瑞幸咖啡", 15_00, 32_00), ("肯德基", 30_00, 75_00)],
    "购物": [("京东", 89_00, 899_00), ("淘宝", 39_00, 499_00), ("优衣库", 99_00, 599_00),
             ("山姆会员店", 200_00, 780_00)],
    "交通": [("滴滴出行", 18_00, 89_00), ("深圳地铁", 3_00, 12_00), ("中国石化", 200_00, 450_00),
             ("12306", 120_00, 680_00)],
    "娱乐": [("猫眼电影", 45_00, 120_00), ("腾讯视频", 25_00, 25_00), ("网易云音乐", 15_00, 15_00)],
    "居住": [("物业费", 380_00, 380_00), ("水电燃气", 120_00, 260_00)],
    "医疗": [("叮当快药", 35_00, 120_00), ("瑞尔齿科", 300_00, 900_00)],
    "教育": [("得到", 29_00, 199_00), ("Coursera", 39_00, 399_00)],
    "其他": [("话费充值", 50_00, 100_00), ("顺丰速运", 12_00, 45_00)],
}


def _history(rand: random.Random) -> list[dict]:
    """生成近 90 天消费流水（不含今日），返回带日期/分类/商户/金额的列表。"""
    today = config.now_dt().date()
    rows: list[dict] = []
    for d in range(90, 0, -1):
        day = today - timedelta(days=d)
        n = rand.choice([1, 1, 2, 2, 3])
        for _ in range(n):
            cat = rand.choices(list(MERCHANTS), weights=[30, 18, 16, 10, 8, 6, 5, 7])[0]
            name, lo, hi = rand.choice(MERCHANTS[cat])
            amount = rand.randint(lo, hi)
            hour = rand.choices([8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 22],
                                weights=[4, 5, 6, 7, 10, 9, 8, 8, 10, 10, 9, 7, 5])[0]
            minute = rand.randint(0, 59)
            rows.append({
                "day": day.isoformat(), "time": f"{hour:02d}:{minute:02d}",
                "merchant": name, "category": cat, "amount": amount,
            })
    # 三条异常样本（对应文档 7.2 ②）
    rows.append({"day": (today - timedelta(days=12)).isoformat(), "time": "02:17",
                 "merchant": "某电商", "category": "购物", "amount": 128_000})       # 凌晨 + 大额
    rows.append({"day": (today - timedelta(days=7)).isoformat(), "time": "15:40",
                 "merchant": "携程机票", "category": "交通", "amount": 156_000})      # 异地消费
    base_day = (today - timedelta(days=3)).isoformat()
    for i in range(5):                                                              # 1 小时内 5 笔
        rows.append({"day": base_day, "time": f"21:{10 + i:02d}",
                     "merchant": "星耀直播", "category": "娱乐", "amount": 9_900})
    rows.sort(key=lambda r: (r["day"], r["time"]))
    return rows


def seed(conn, force: bool = False) -> dict:
    cur = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
    if int(cur["c"]) > 0 and not force:
        conn.execute(
            "INSERT OR IGNORE INTO loans(id,user_id,principal,remaining,monthly,next_due,status,created_at) "
            "VALUES(1,1,60000000,42000000,780000,'2026-11-15','active',?)",
            (config.now_iso(),),
        )
        return {"seeded": False, "reason": "already seeded"}

    rand = random.Random(20261029)
    history = _history(rand)

    # 1) 主用户与账户
    conn.execute(
        "INSERT OR REPLACE INTO users(id,name,phone,risk_level,risk_score,kyc_status,birthday) "
        "VALUES(1,'陈晓','138****6688','normal',10,'passed','1990-11-18')"
    )
    today_date = config.now_dt().date()
    today = today_date.isoformat()
    # 妈妈给的两笔来账（用于"查我妈给我的转账"这类查询）
    incoming = [
        {"day": (today_date - timedelta(days=45)).isoformat(), "amount": 200_000, "memo": "生活费"},
        {"day": (today_date - timedelta(days=12)).isoformat(), "amount": 110_000, "memo": "生日红包"},
    ]
    incoming_total = sum(x["amount"] for x in incoming)
    total_out = sum(h["amount"] for h in history)
    opening = TARGET_BALANCE + total_out + TODAY_TRANSFER - incoming_total
    conn.execute(
        "INSERT OR REPLACE INTO accounts(id,user_id,type,opening_balance,frozen,currency,created_at) "
        "VALUES(1,1,'current',?,0,'CNY',?)",
        (opening, config.now_iso()),
    )
    conn.execute(
        "INSERT OR REPLACE INTO accounts(id,user_id,type,opening_balance,frozen,currency,created_at) "
        "VALUES(2,1,'fixed',5000000,0,'CNY',?)", (config.now_iso(),)
    )
    conn.execute(
        "INSERT OR REPLACE INTO accounts(id,user_id,type,opening_balance,frozen,currency,created_at) "
        "VALUES(3,1,'credit',0,0,'CNY',?)", (config.now_iso(),)
    )
    bankcore._internal_account(conn, "merchant_pool")
    bankcore._internal_account(conn, "wealth_pool")

    # 2) 联系人
    contacts = [
        (1, 1, "王秀兰", "妈", "139****8821", "8821", 0),
        (2, 1, "李娜", "老婆", "137****6688", "6688", 0),
        (3, 1, "陈先生", "房东", "136****1234", "1234", 0),
        (4, 1, "张三", None, "135****0099", "0099", 1),
    ]
    for c in contacts:
        conn.execute(
            "INSERT OR REPLACE INTO contacts(id,user_id,name,alias,phone,card_tail,is_new) "
            "VALUES(?,?,?,?,?,?,?)", c
        )
        bankcore._payee_account(conn, c[0])
    conn.execute("UPDATE contacts SET phone_full='13800138000' WHERE id=4")

    # 2.5) 妈妈转来的两笔（对手方账户 D，用户活期 C）
    mom_acc = bankcore._payee_account(conn, 1)
    for j, inc in enumerate(incoming, start=1):
        txn_no = f"T{inc['day'].replace('-', '')}9{j:03d}"
        cur = conn.execute(
            "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
            "memo,category,counterparty,created_at) VALUES(?,1,'transfer_in',?,'CNY','succeeded',?,?,?,?,?)",
            (txn_no, inc["amount"], f"seed-{txn_no}", inc["memo"], "转账", "王秀兰",
             f"{inc['day']}T10:2{j}:00+08:00"),
        )
        tid = int(cur.lastrowid)
        bankcore._post_entry(conn, tid, mom_acc, "D", inc["amount"])
        bankcore._post_entry(conn, tid, 1, "C", inc["amount"])

    # 3) 历史消费（双边分录：用户活期 D，商户池 C）
    merchant_pool = bankcore._internal_account(conn, "merchant_pool")
    for i, h in enumerate(history, start=1):
        txn_no = f"T{h['day'].replace('-', '')}{i:04d}"
        created = f"{h['day']}T{h['time']}:00+08:00"
        cur = conn.execute(
            "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
            "memo,category,counterparty,created_at) VALUES(?,1,'payment',?,'CNY','succeeded',?,?,?,?,?)",
            (txn_no, h["amount"], f"seed-{txn_no}", f"{h['merchant']}·{h['category']}",
             h["category"], h["merchant"], created),
        )
        tid = int(cur.lastrowid)
        bankcore._post_entry(conn, tid, 1, "D", h["amount"])
        bankcore._post_entry(conn, tid, merchant_pool, "C", h["amount"])

    # 4) 今日一笔 500 元转账（形成"当日已用 500"的演示状态）
    r = bankcore.create_transfer(
        conn, user_id=1, from_account_id=1, contact_id=1, amount_cents=TODAY_TRANSFER,
        memo="本月生活费", idem_key=f"seed-today-{today}",
    )
    if not r.get("ok"):
        raise RuntimeError(f"seed 转账失败: {r}")

    # 5) 订阅
    subs = [
        (1, 1, "视频会员A", 25_00, 30, "2026-11-03", "active", "2026-04-20"),
        (2, 1, "云存储B", 12_00, 30, "2026-11-08", "active", "2026-05-02"),
        (3, 1, "健身C", 99_00, 30, "2026-11-08", "active", "2026-04-15"),
        (4, 1, "音乐D", 15_00, 30, "2026-11-15", "active", (config.now_dt().date() - timedelta(days=1)).isoformat()),
        (5, 1, "新闻E", 8_00, 30, "2026-11-20", "active", (config.now_dt().date() - timedelta(days=2)).isoformat()),
    ]
    for s in subs:
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions(id,user_id,merchant,amount,cycle_days,"
            "next_charge_at,status,last_used_at) VALUES(?,?,?,?,?,?,?,?)", s
        )

    # 6) 理财产品与持仓
    products = [
        (1, "稳盈 90 天", 0.028, 90, "R2", 100_000),
        (2, "稳健 180 天", 0.031, 180, "R2", 500_000),
        (3, "增利 30 天", 0.022, 30, "R1", 100_000),
        (4, "进取混合", 0.045, 365, "R3", 1_000_000),
        (5, "现金管理", 0.018, 7, "R1", 10_000),
        (6, "黄金定投", 0.035, 365, "R3", 100_000),
    ]
    for p in products:
        conn.execute(
            "INSERT OR REPLACE INTO wealth_products(id,name,rate,term_days,risk_level,min_amount) "
            "VALUES(?,?,?,?,?,?)", p
        )
    conn.execute("INSERT OR REPLACE INTO holdings(id,user_id,product_id,amount,purchased_at) "
                 "VALUES(1,1,1,500000,?)", ("2026-08-15T10:00:00+08:00",))
    conn.execute("INSERT OR REPLACE INTO holdings(id,user_id,product_id,amount,purchased_at) "
                 "VALUES(2,1,5,200000,?)", ("2026-09-01T10:00:00+08:00",))

    # 7) 信用卡
    conn.execute(
        "INSERT OR REPLACE INTO points(user_id,balance,expiring,expire_at) VALUES(1,12680,1200,'2026-12-31')"
    )

    conn.execute(
        "INSERT OR REPLACE INTO cards(id,user_id,card_tail,status,daily_limit,used,frozen,bill_cents) "
        "VALUES(1,1,'6688','active',2000000,750000,0,328050)"
    )

    conn.execute(
        "INSERT OR REPLACE INTO daily_quota(user_id,biz_date,used) VALUES(1,?,?)",
        (today, TODAY_TRANSFER),
    )

    conn.execute(
        "INSERT OR IGNORE INTO loans(id,user_id,principal,remaining,monthly,next_due,status,created_at) "
        "VALUES(1,1,60000000,42000000,780000,'2026-11-15','active',?)",
        (config.now_iso(),),
    )

    return {
        "seeded": True,
        "user": "陈晓",
        "balance_cents": bankcore.get_balance(conn, 1),
        "daily_used_cents": bankcore.get_daily_used(conn, 1),
        "history_transactions": len(history),
        "subscriptions": len(subs),
        "products": len(products),
    }
