from app.db import connect


def init_db():
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            fixture_path TEXT NOT NULL,
            depends_on_json TEXT NOT NULL,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            requires_release INTEGER NOT NULL DEFAULT 0,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS orchestrations(
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            report_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_attempts(
            id INTEGER PRIMARY KEY,
            orchestration_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            attempt_no INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS release_decisions(
            id INTEGER PRIMARY KEY,
            orchestration_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('released','rejected')),
            actor TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            decided_at TEXT NOT NULL,
            UNIQUE(orchestration_id, job_id)
        );
        CREATE TABLE IF NOT EXISTS watermarks(
            job_id INTEGER PRIMARY KEY,
            watermark_ts TEXT,
            artifact_rows INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        );
        """
    )
    # 迁移：老库 jobs 表没有 requires_release 列时补上（默认不需放行）。
    job_cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "requires_release" not in job_cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN requires_release INTEGER NOT NULL DEFAULT 0"
        )
    if conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 0:
        jobs = [
            (
                "ingest_orders",
                "ingest_orders.json",
                "[]",
                1,
                1,
                0,
                "无依赖：写出订单水位",
            ),
            (
                "dim_customer",
                "dim_customer.json",
                "[]",
                1,
                1,
                0,
                "无依赖：写出客户维表水位",
            ),
            (
                "fact_order_daily",
                "fact_order_daily.json",
                '["ingest_orders","dim_customer"]',
                1,
                1,
                1,
                "关键作业：依赖两路上游成功，且当趟需人工放行后才执行",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO jobs(name, fixture_path, depends_on_json, max_attempts, enabled, requires_release, note)
            VALUES (?,?,?,?,?,?,?)
            """,
            jobs,
        )
        for row in conn.execute("SELECT id FROM jobs").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO watermarks(job_id, watermark_ts, artifact_rows, updated_at) VALUES (?,?,?,?)",
                (row["id"], None, 0, None),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            ("default_max_attempts", "1"),
        )
    conn.commit()
    conn.close()
