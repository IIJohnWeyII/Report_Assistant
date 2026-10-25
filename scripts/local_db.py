"""Manage only this workspace's loopback MySQL demo, never the configured app DB."""
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request
import zipfile

import pymysql

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
MYSQL = RUNTIME / "mysql-8.4.13-winx64"
CONFIG = RUNTIME / "mysql.ini"
CREDENTIALS = RUNTIME / "demo-credentials.json"
DATABASE = "report_assistant_demo"
PORT = 13306


def admin():
    credentials = json.loads(CREDENTIALS.read_text())
    return pymysql.connect(host="127.0.0.1", port=PORT, user="root",
                           password=credentials["root_password"], charset="utf8mb4", autocommit=True)


def start():
    if not CONFIG.exists() or not CREDENTIALS.exists():
        raise RuntimeError("Run scripts/local_db.py setup first.")
    try:
        with admin() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@datadir")
                if Path(cursor.fetchone()[0]).resolve() != (RUNTIME / "mysql-data").resolve():
                    raise RuntimeError("Port belongs to another database; refusing to use it.")
        print("Local demo MySQL is running.")
        return
    except pymysql.OperationalError as error:
        if error.args[0] != 2003:
            raise
    with (RUNTIME / "mysql-process.log").open("ab") as log:
        process = subprocess.Popen([str(MYSQL / "bin/mysqld.exe"), f"--defaults-file={CONFIG}"],
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("MySQL failed to start; inspect .runtime/mysql-error.log")
        try:
            with admin():
                print(f"Local demo MySQL ready at 127.0.0.1:{PORT}")
                return
        except pymysql.OperationalError:
            time.sleep(.5)
    raise RuntimeError("MySQL startup timed out; inspect .runtime/mysql-error.log")


def setup():
    RUNTIME.mkdir(exist_ok=True)
    if not MYSQL.exists():
        archive = RUNTIME / "mysql-8.4.13-winx64.zip"
        if not archive.exists():
            urllib.request.urlretrieve("https://cdn.mysql.com/Downloads/MySQL-8.4/" + archive.name, archive)
        if hashlib.md5(archive.read_bytes()).hexdigest() != "7b94489d5524dea9b27097068bea25a8":
            raise RuntimeError("Official MySQL archive checksum mismatch")
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(RUNTIME)
    if not CREDENTIALS.exists():
        if (RUNTIME / "mysql-data").exists():
            raise RuntimeError("Existing unrecognized data directory; refusing to initialize it.")
        CONFIG.write_text(
            f"[mysqld]\nbasedir={MYSQL.as_posix()}\ndatadir={(RUNTIME / 'mysql-data').as_posix()}\n"
            f"port={PORT}\nbind-address=127.0.0.1\nmysqlx=0\nskip-log-bin\n"
            "innodb-buffer-pool-size=128M\nperformance-schema=OFF\nmax-connections=20\n"
            f"log-error={(RUNTIME / 'mysql-error.log').as_posix()}\n", encoding="utf-8")
        subprocess.run([str(MYSQL / "bin/mysqld.exe"), f"--defaults-file={CONFIG}", "--initialize-insecure"],
                       check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        CREDENTIALS.write_text(json.dumps({"root_password": "", "reader_password": secrets.token_hex(24)}))
    start()
    credentials = json.loads(CREDENTIALS.read_text())
    with admin() as connection, connection.cursor() as cursor:
        if not credentials["root_password"]:
            new_password = secrets.token_hex(24)
            cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (new_password,))
            credentials["root_password"] = new_password
            CREDENTIALS.write_text(json.dumps(credentials))
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        cursor.execute(f"USE {DATABASE}")
        # Preserve the supplied DDL; MySQL ignores TiDB's vendor-specific comments.
        import re
        ddl = (ROOT / "docs/source-ddl.sql").read_text(encoding="utf-8-sig")
        for statement in re.split(r";\s*(?:\n|$)", ddl):
            if "CREATE TABLE" in statement:
                cursor.execute(statement.replace("CREATE TABLE `", "CREATE TABLE IF NOT EXISTS `", 1))
        for shard in range(1, 4):
            cursor.execute(f"CREATE TABLE IF NOT EXISTS fi_fill_record_row{shard} LIKE fi_fill_record_row0")
        cursor.execute("CREATE USER IF NOT EXISTS 'report_reader'@'127.0.0.1' IDENTIFIED BY %s", (credentials["reader_password"],))
        cursor.execute(f"GRANT SELECT ON {DATABASE}.* TO 'report_reader'@'127.0.0.1'")
        seed(cursor)
        seed_metadata(cursor)
    env_path = ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(
            f"APP_ENV=development\nDB_HOST=127.0.0.1\nDB_PORT={PORT}\nDB_NAME={DATABASE}\n"
            f"DB_USER=report_reader\nDB_PASSWORD={credentials['reader_password']}\nDB_SSL_CA=\n", encoding="utf-8")
    print("Demo seeded: 16 completed tasks plus exclusions, historical fields and record metadata. Existing .env preserved.")


def seed(cursor):
    """Stage 1 fixtures. No fill/audit status is invented before business confirmation."""
    topics = [("OUTPUT", "项目产值月报", "华东建设 · 工程管理部"),
              ("COST", "项目成本月报", "华东建设 · 商务管理部"),
              ("MATERIAL", "钢筋混凝土材料耗用月报", "华东建设 · 物资管理部"),
              ("LABOR", "劳务用工与工资月报", "华东建设 · 人力资源部")]
    import calendar
    rows = []
    for month in range(7, 11):
        for code, name, org in topics:
            number = len(rows) + 1
            rows.append((number, f"DEMO_{code}_2026{month:02}", f"{name} · {month}月", "PROJECT", "COMPLETED",
                         f"ORG_{code}", org, "CYCLE", "MONTH", f"2026-{month:02}",
                         f"2026-{month:02}-01", f"2026-{month:02}-{calendar.monthrange(2026, month)[1]}",
                         f"TPL_{code}", "演示数据", 1, "演示数据", 1, 0))
    for number, status, deleted, name in [(17, "ACTIVE", 0, "进行中测试任务"),
                                          (18, "DRAFT", 0, "草稿测试任务"),
                                          (19, "COMPLETED", 1, "已删除测试任务")]:
        rows.append((number, f"DEMO_EXCLUDED_{number}", name, "ORG", status, "ORG_TEST", "测试组织",
                     "CYCLE", "MONTH", "2026-10", "2026-10-01", "2026-10-31", None,
                     "演示数据", 1, "演示数据", 1, deleted))
    cursor.executemany("""INSERT IGNORE INTO rt_report_task
        (id, report_task_no, task_name, task_type, task_status, publish_org_code, publish_org_name,
         data_time_type, data_cycle_type, data_time_value, time_range_start, time_range_end,
         current_template_no, create_name, create_user_id, update_name, update_user_id, delete_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)


def seed_metadata(cursor):
    common = [("project_code", "项目编码", "TEXT", "ORIGINAL", None),
              ("project_name", "项目名称", "TEXT", "ORIGINAL", None),
              ("period", "数据月份", "TEXT", "ORIGINAL", None)]
    topics = {
        "OUTPUT": [("planned_output", "计划产值", "NUMBER", "ORIGINAL", "万元"),
                   ("actual_output", "实际产值", "NUMBER", "ORIGINAL", "万元"),
                   ("completion_rate", "产值完成率", "NUMBER", "DERIVED", "%")],
        "COST": [("material_cost", "材料成本", "NUMBER", "ORIGINAL", "万元"),
                 ("labor_cost", "劳务成本", "NUMBER", "ORIGINAL", "万元"),
                 ("other_cost", "其他成本", "NUMBER", "ORIGINAL", "万元"),
                 ("total_cost", "项目总成本", "NUMBER", "DERIVED", "万元")],
        "MATERIAL": [("material_name", "材料名称", "TEXT", "ORIGINAL", None),
                     ("quantity", "材料耗用量", "NUMBER", "ORIGINAL", "吨"),
                     ("unit_price", "材料单价", "NUMBER", "ORIGINAL", "元/吨"),
                     ("amount", "材料金额", "NUMBER", "DERIVED", "元")],
        "LABOR": [("trade", "工种", "TEXT", "ORIGINAL", None),
                  ("workers", "在场人数", "NUMBER", "ORIGINAL", "人"),
                  ("workdays", "累计工日", "NUMBER", "ORIGINAL", "工日"),
                  ("payroll", "应付工资", "NUMBER", "ORIGINAL", "万元")],
    }
    fields = []

    def add_fields(template, definitions, deleted=0):
        for sort_no, (code, name, data_type, kind, unit) in enumerate(definitions):
            number = 1000 + len(fields)
            fields.append((number, f"DEMO_FIELD_{number}", template, code, name, kind,
                           data_type, unit, sort_no, "演示数据", 1, "演示数据", 1, deleted))

    for code, definitions in topics.items():
        add_fields(f"TPL_{code}", common + definitions)
    add_fields("TPL_OUTPUT_OLD", [("project_code", "工程编码", "TEXT", "ORIGINAL", None),
                                   ("legacy_project_name", "项目名称", "TEXT", "ORIGINAL", None),
                                   ("legacy_zone", "施工分区（历史）", "TEXT", "ORIGINAL", None)])
    add_fields("TPL_OUTPUT_LATEST", [("current_only", "未用于填报的新字段", "TEXT", "ORIGINAL", None)])
    add_fields("TPL_DELETED_RECORD", [("deleted_record_only", "已删记录的字段", "TEXT", "ORIGINAL", None)])
    add_fields("TPL_OUTPUT", [("deleted_field", "已删除字段", "TEXT", "ORIGINAL", None)], deleted=1)
    cursor.executemany("""INSERT IGNORE INTO tc_report_field
        (id, report_field_no, report_template_no, field_code, field_name, field_kind, data_type, unit,
         sort_no, create_name, create_user_id, update_name, update_user_id, delete_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", fields)
    records = []
    for month in range(7, 11):
        for code in topics:
            records.append((1000 + len(records), f"DEMO_REC_{code}_2026{month:02}",
                            f"DEMO_{code}_2026{month:02}", f"DEMO_PERIOD_2026{month:02}",
                            f"TPL_{code}", "PROJECT", "ORG_EAST", "华东建设", "P001", "滨江科创中心",
                            "演示数据", 1, "演示数据", 1, 0))
    for record_no, template, deleted in [("DEMO_REC_OUTPUT_OLD", "TPL_OUTPUT_OLD", 0),
                                          ("DEMO_REC_DELETED", "TPL_DELETED_RECORD", 1)]:
        records.append((1000 + len(records), record_no, "DEMO_OUTPUT_202610", "DEMO_PERIOD_202609",
                        template, "PROJECT", "ORG_EAST", "华东建设", "P001", "滨江科创中心",
                        "演示数据", 1, "演示数据", 1, deleted))
    cursor.executemany("""INSERT IGNORE INTO fi_fill_record
        (id, fill_record_no, report_task_no, report_period_no, report_template_no, task_type,
         target_org_code, target_org_name, target_project_no, target_project_name,
         create_name, create_user_id, update_name, update_user_id, delete_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", records)
    cursor.execute("""UPDATE rt_report_task SET current_template_no = 'TPL_OUTPUT_LATEST'
                      WHERE report_task_no = 'DEMO_OUTPUT_202610'""")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action == "setup":
        setup()
    elif action == "start":
        start()
    elif action == "stop":
        # Verify that this is our data directory before shutting down the instance.
        start()
        with admin() as connection, connection.cursor() as cursor:
            cursor.execute("SHUTDOWN")
        print("Local demo MySQL stopped.")
    else:
        raise SystemExit("Usage: local_db.py [setup|start|stop]")
