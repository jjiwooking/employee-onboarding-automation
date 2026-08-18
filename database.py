import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "onboarding.db"

DEFAULT_SETTINGS = {
    "program_title": "신입사원 온보딩 자동화 시스템",
    "menu_dashboard": "온보딩 현황",
    "menu_employees": "입사자 관리",
    "menu_templates": "온보딩 템플릿",
    "menu_notifications": "알림 및 지연업무",
    "menu_settings": "관리자 설정",
    "feature_employee_add": "신규 입사자 등록",
    "feature_progress": "온보딩 진행상태",
    "feature_overdue": "지연 업무",
    "feature_template": "온보딩 템플릿 관리",
    "feature_permissions": "권한 관리",
    "notify_before_days": "2",
    "escalate_after_days": "3",
}

DEFAULT_TEMPLATE_ITEMS = [
    ("근로계약서 작성", "인사", -7, 1, 10),
    ("인사정보 등록", "인사", -5, 1, 20),
    ("PC 및 업무장비 준비", "IT", -3, 1, 30),
    ("사내 계정 생성", "IT", -2, 1, 40),
    ("사원증 및 출입권한 신청", "총무", -2, 1, 50),
    ("부서 업무 안내", "소속부서", 0, 1, 60),
    ("법정·필수교육 안내", "인사", 3, 1, 70),
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn, table, column):
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def init_db():
    conn = connect()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            employee_no TEXT,
            department TEXT NOT NULL,
            job_title TEXT,
            employment_type TEXT DEFAULT '정규직',
            start_date TEXT NOT NULL,
            status TEXT DEFAULT '입사예정',
            exception_status TEXT DEFAULT '',
            template_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS onboarding_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            process_type TEXT DEFAULT '온보딩',
            match_department TEXT DEFAULT '',
            match_job_title TEXT DEFAULT '',
            match_employment_type TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            owner_department TEXT NOT NULL,
            due_offset INTEGER DEFAULT 0,
            required INTEGER DEFAULT 1,
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 100,
            created_at TEXT NOT NULL,
            FOREIGN KEY(template_id) REFERENCES onboarding_templates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS onboarding_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            template_item_id INTEGER,
            title TEXT NOT NULL,
            owner_department TEXT NOT NULL,
            assigned_to TEXT DEFAULT '',
            due_date TEXT NOT NULL,
            required INTEGER DEFAULT 1,
            status TEXT DEFAULT '미완료',
            completed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS department_owners (
            department TEXT PRIMARY KEY,
            owner_name TEXT DEFAULT '',
            contact TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            employee_id INTEGER NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            acknowledged INTEGER DEFAULT 0,
            generated_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(task_id, level, generated_date),
            FOREIGN KEY(task_id) REFERENCES onboarding_tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS change_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS role_permissions (
            role TEXT PRIMARY KEY,
            can_view_all INTEGER DEFAULT 0,
            can_edit_employee INTEGER DEFAULT 0,
            can_manage_templates INTEGER DEFAULT 0,
            can_manage_settings INTEGER DEFAULT 0,
            can_manage_permissions INTEGER DEFAULT 0
        );
        """
    )

    # 기존 1차 DB를 열어도 깨지지 않도록 최소 마이그레이션
    migrations = [
        ("employees", "template_id", "ALTER TABLE employees ADD COLUMN template_id INTEGER"),
        ("onboarding_tasks", "template_item_id", "ALTER TABLE onboarding_tasks ADD COLUMN template_item_id INTEGER"),
        ("onboarding_tasks", "assigned_to", "ALTER TABLE onboarding_tasks ADD COLUMN assigned_to TEXT DEFAULT ''"),
        ("role_permissions", "can_manage_templates", "ALTER TABLE role_permissions ADD COLUMN can_manage_templates INTEGER DEFAULT 0"),
        ("role_permissions", "can_manage_permissions", "ALTER TABLE role_permissions ADD COLUMN can_manage_permissions INTEGER DEFAULT 0"),
    ]
    for table, column, sql in migrations:
        if not _column_exists(conn, table, column):
            cur.execute(sql)

    now = datetime.now().isoformat(timespec="seconds")
    for key, value in DEFAULT_SETTINGS.items():
        cur.execute("INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)", (key, value, now))

    # 기본 역할 권한
    role_rows = [
        ("인사담당자", 1, 1, 1, 1, 1),
        ("부서장", 1, 0, 0, 0, 0),
        ("신입사원", 0, 0, 0, 0, 0),
    ]
    for row in role_rows:
        cur.execute(
            """INSERT INTO role_permissions(role, can_view_all, can_edit_employee, can_manage_templates, can_manage_settings, can_manage_permissions)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(role) DO NOTHING""",
            row,
        )

    # 기본 담당부서
    for dept in ["인사", "IT", "총무", "소속부서"]:
        cur.execute(
            "INSERT OR IGNORE INTO department_owners(department, owner_name, contact, updated_at) VALUES (?, '', '', ?)",
            (dept, now),
        )

    # 기본 온보딩 템플릿
    template = cur.execute("SELECT id FROM onboarding_templates WHERE name='공통 신입사원 온보딩' LIMIT 1").fetchone()
    if not template:
        cur.execute(
            "INSERT INTO onboarding_templates(name, process_type, active, created_at, updated_at) VALUES ('공통 신입사원 온보딩', '온보딩', 1, ?, ?)",
            (now, now),
        )
        template_id = cur.lastrowid
        for title, owner_department, due_offset, required, sort_order in DEFAULT_TEMPLATE_ITEMS:
            cur.execute(
                """INSERT INTO template_items(template_id, title, owner_department, due_offset, required, enabled, sort_order, created_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (template_id, title, owner_department, due_offset, required, sort_order, now),
            )

    conn.commit()
    conn.close()


def log_change(actor, action, target, detail=""):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?)", (actor, action, target, detail, now))
    conn.commit(); conn.close()


def get_settings():
    conn = connect(); rows = conn.execute("SELECT key, value FROM settings").fetchall(); conn.close()
    return {row["key"]: row["value"] for row in rows}


def update_setting(key, value, actor="관리자"):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    old = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.execute("INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now))
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES (?, '설정 변경', ?, ?, ?)", (actor, key, f"{old['value'] if old else ''} → {value}", now))
    conn.commit(); conn.close()


def list_permissions():
    conn = connect(); rows = conn.execute("SELECT * FROM role_permissions ORDER BY CASE role WHEN '인사담당자' THEN 1 WHEN '부서장' THEN 2 ELSE 3 END, role").fetchall(); conn.close(); return rows


def get_permission(role):
    conn = connect(); row = conn.execute("SELECT * FROM role_permissions WHERE role=?", (role,)).fetchone(); conn.close(); return row


def update_permission(role, view_all, edit_employee, manage_templates, manage_settings, manage_permissions):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO role_permissions(role, can_view_all, can_edit_employee, can_manage_templates, can_manage_settings, can_manage_permissions)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(role) DO UPDATE SET can_view_all=excluded.can_view_all, can_edit_employee=excluded.can_edit_employee,
           can_manage_templates=excluded.can_manage_templates, can_manage_settings=excluded.can_manage_settings, can_manage_permissions=excluded.can_manage_permissions""",
        (role, view_all, edit_employee, manage_templates, manage_settings, manage_permissions),
    )
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES ('관리자','권한 변경',?,?,?)", (role, "역할별 권한 수정", now))
    conn.commit(); conn.close()


def list_department_owners():
    conn = connect(); rows = conn.execute("SELECT * FROM department_owners ORDER BY department").fetchall(); conn.close(); return rows


def upsert_department_owner(department, owner_name, contact):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO department_owners(department, owner_name, contact, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(department) DO UPDATE SET owner_name=excluded.owner_name, contact=excluded.contact, updated_at=excluded.updated_at", (department, owner_name, contact, now))
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES ('관리자','담당자 규칙 변경',?,?,?)", (department, f"{owner_name} / {contact}", now))
    conn.commit(); conn.close()


def _owner_name(conn, owner_department, employee_department):
    lookup = employee_department if owner_department == "소속부서" else owner_department
    row = conn.execute("SELECT owner_name FROM department_owners WHERE department=?", (lookup,)).fetchone()
    return row["owner_name"] if row and row["owner_name"] else lookup


def list_templates():
    conn = connect(); rows = conn.execute("SELECT * FROM onboarding_templates ORDER BY active DESC, id").fetchall(); conn.close(); return rows


def get_template(template_id):
    conn = connect(); row = conn.execute("SELECT * FROM onboarding_templates WHERE id=?", (template_id,)).fetchone(); conn.close(); return row


def list_template_items(template_id):
    conn = connect(); rows = conn.execute("SELECT * FROM template_items WHERE template_id=? ORDER BY sort_order,id", (template_id,)).fetchall(); conn.close(); return rows


def create_template(name, match_department="", match_job_title="", match_employment_type="", process_type="온보딩"):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO onboarding_templates(name, process_type, match_department, match_job_title, match_employment_type, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)", (name, process_type, match_department, match_job_title, match_employment_type, now, now))
    tid = cur.lastrowid
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES ('관리자','템플릿 생성',?,?,?)", (str(tid), name, now))
    conn.commit(); conn.close(); return tid


def update_template(template_id, name, match_department, match_job_title, match_employment_type, active, process_type="온보딩"):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE onboarding_templates SET name=?, process_type=?, match_department=?, match_job_title=?, match_employment_type=?, active=?, updated_at=? WHERE id=?", (name, process_type, match_department, match_job_title, match_employment_type, active, now, template_id))
    conn.execute("INSERT INTO change_logs(actor, action, target, detail, created_at) VALUES ('관리자','템플릿 수정',?,?,?)", (str(template_id), name, now))
    conn.commit(); conn.close()


def add_template_item(template_id, title, owner_department, due_offset, required, sort_order):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute("INSERT INTO template_items(template_id,title,owner_department,due_offset,required,enabled,sort_order,created_at) VALUES (?,?,?,?,?,1,?,?)", (template_id,title,owner_department,due_offset,required,sort_order,now))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','템플릿 항목 추가',?,?,?)", (str(cur.lastrowid), title, now))
    conn.commit(); conn.close()


def update_template_item(item_id, title, owner_department, due_offset, required, enabled, sort_order):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE template_items SET title=?, owner_department=?, due_offset=?, required=?, enabled=?, sort_order=? WHERE id=?", (title,owner_department,due_offset,required,enabled,sort_order,item_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','템플릿 항목 수정',?,?,?)", (str(item_id), title, now))
    conn.commit(); conn.close()


def delete_template_item(item_id):
    conn=connect(); row=conn.execute("SELECT title FROM template_items WHERE id=?",(item_id,)).fetchone(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM template_items WHERE id=?",(item_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','템플릿 항목 삭제',?,?,?)",(str(item_id),row['title'] if row else '',now))
    conn.commit(); conn.close()


def choose_template(conn, department, job_title, employment_type):
    rows = conn.execute("SELECT * FROM onboarding_templates WHERE active=1 AND process_type='온보딩' ORDER BY id").fetchall()
    scored = []
    for r in rows:
        score = 0
        valid = True
        for field, value in [("match_department", department), ("match_job_title", job_title), ("match_employment_type", employment_type)]:
            rule = (r[field] or "").strip()
            if rule:
                if rule.lower() in (value or "").lower(): score += 10
                else: valid = False; break
        if valid: scored.append((score, r))
    if not scored: return None
    scored.sort(key=lambda x:(-x[0], x[1]["id"]))
    return scored[0][1]


def create_employee(name, employee_no, department, job_title, employment_type, start_date):
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    template = choose_template(conn, department, job_title, employment_type)
    template_id = template["id"] if template else None
    cur = conn.execute("INSERT INTO employees(name, employee_no, department, job_title, employment_type, start_date, status, template_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, '입사예정', ?, ?, ?)", (name, employee_no, department, job_title, employment_type, start_date, template_id, now, now))
    employee_id = cur.lastrowid
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    if template_id:
        items = conn.execute("SELECT * FROM template_items WHERE template_id=? AND enabled=1 ORDER BY sort_order,id", (template_id,)).fetchall()
        for item in items:
            due_date = (start_dt + timedelta(days=item["due_offset"])).strftime("%Y-%m-%d")
            assigned_to = _owner_name(conn, item["owner_department"], department)
            conn.execute("INSERT INTO onboarding_tasks(employee_id, template_item_id, title, owner_department, assigned_to, due_date, required, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '미완료', ?)", (employee_id,item["id"],item["title"],item["owner_department"],assigned_to,due_date,item["required"],now))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사자 등록',?,?,?)", (str(employee_id), f"{name} / {department} / {start_date} / 템플릿:{template['name'] if template else '없음'}", now))
    conn.commit(); conn.close(); return employee_id


def list_employees(search="", department="", status=""):
    conn = connect(); query="SELECT * FROM employees WHERE 1=1"; params=[]
    if search:
        query += " AND (name LIKE ? OR employee_no LIKE ? OR job_title LIKE ?)"; token=f"%{search}%"; params += [token,token,token]
    if department: query += " AND department=?"; params.append(department)
    if status: query += " AND status=?"; params.append(status)
    query += " ORDER BY start_date DESC,id DESC"
    rows=conn.execute(query,params).fetchall(); conn.close(); return rows


def get_employee(employee_id):
    conn=connect(); row=conn.execute("SELECT e.*, t.name template_name FROM employees e LEFT JOIN onboarding_templates t ON e.template_id=t.id WHERE e.id=?",(employee_id,)).fetchone(); conn.close(); return row


def list_tasks(employee_id):
    conn=connect(); rows=conn.execute("SELECT * FROM onboarding_tasks WHERE employee_id=? ORDER BY due_date,id",(employee_id,)).fetchall(); conn.close(); return rows


def toggle_task(task_id):
    conn=connect(); row=conn.execute("SELECT * FROM onboarding_tasks WHERE id=?",(task_id,)).fetchone()
    if not row: conn.close(); return
    now=datetime.now().isoformat(timespec="seconds"); new_status="완료" if row["status"]!="완료" else "미완료"; completed_at=now if new_status=="완료" else None
    conn.execute("UPDATE onboarding_tasks SET status=?, completed_at=? WHERE id=?",(new_status,completed_at,task_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('담당자','온보딩 업무 상태 변경',?,?,?)",(str(task_id),new_status,now))
    conn.commit(); conn.close()


def set_exception(employee_id, exception_status):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE employees SET exception_status=?, updated_at=? WHERE id=?",(exception_status,now,employee_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','예외 상태 변경',?,?,?)",(str(employee_id),exception_status or '해제',now))
    conn.commit(); conn.close()


def change_employee_start_date(employee_id, new_start_date, shift_incomplete_tasks=True):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT start_date,name FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not row: conn.close(); return
    old=datetime.strptime(row["start_date"],"%Y-%m-%d").date(); new=datetime.strptime(new_start_date,"%Y-%m-%d").date(); diff=(new-old).days
    conn.execute("UPDATE employees SET start_date=?,exception_status='입사일 변경',updated_at=? WHERE id=?",(new_start_date,now,employee_id))
    if shift_incomplete_tasks and diff:
        tasks=conn.execute("SELECT id,due_date FROM onboarding_tasks WHERE employee_id=? AND status!='완료'",(employee_id,)).fetchall()
        for t in tasks:
            due=datetime.strptime(t["due_date"],"%Y-%m-%d").date()+timedelta(days=diff)
            conn.execute("UPDATE onboarding_tasks SET due_date=? WHERE id=?",(due.isoformat(),t["id"]))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사일 변경',?,?,?)",(str(employee_id),f"{row['start_date']} → {new_start_date} / 미완료 기한 {'이동' if shift_incomplete_tasks else '유지'}",now))
    conn.commit(); conn.close()

def cancel_employee(employee_id):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE employees SET status='입사취소',exception_status='입사 취소',updated_at=? WHERE id=?",(now,employee_id))
    conn.execute("UPDATE onboarding_tasks SET status='취소' WHERE employee_id=? AND status!='완료'",(employee_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사 취소',?,'미완료 온보딩 업무 자동 취소',?)",(str(employee_id),now))
    conn.commit(); conn.close()

def dashboard_summary():
    conn=connect(); today=datetime.now().strftime("%Y-%m-%d")
    total=conn.execute("SELECT COUNT(*) c FROM employees").fetchone()["c"]; upcoming=conn.execute("SELECT COUNT(*) c FROM employees WHERE status='입사예정'").fetchone()["c"]
    tasks=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks").fetchone()["c"]; completed=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE status='완료'").fetchone()["c"]
    overdue=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE status!='완료' AND due_date<?",(today,)).fetchone()["c"]
    conn.close(); return {"total":total,"upcoming":upcoming,"tasks":tasks,"completed":completed,"overdue":overdue,"progress":round((completed/tasks)*100,1) if tasks else 0}


def employee_progress(employee_id):
    conn=connect(); total=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE employee_id=?",(employee_id,)).fetchone()["c"]; done=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE employee_id=? AND status='완료'",(employee_id,)).fetchone()["c"]; conn.close(); return round((done/total)*100) if total else 0


def refresh_notifications():
    conn=connect(); today=datetime.now().date(); now=datetime.now().isoformat(timespec="seconds"); today_s=today.isoformat()
    settings={r["key"]:r["value"] for r in conn.execute("SELECT key,value FROM settings").fetchall()}
    notify_before=int(settings.get("notify_before_days","2")); escalate_after=int(settings.get("escalate_after_days","3"))
    rows=conn.execute("SELECT t.*,e.name employee_name FROM onboarding_tasks t JOIN employees e ON t.employee_id=e.id WHERE t.status!='완료'").fetchall()
    for row in rows:
        due=datetime.strptime(row["due_date"],"%Y-%m-%d").date(); delta=(due-today).days
        level=None
        if delta <= -escalate_after: level="에스컬레이션"
        elif delta < 0: level="지연"
        elif delta <= notify_before: level="임박"
        if level:
            msg=f"{row['employee_name']} - {row['title']} / 담당: {row['assigned_to'] or row['owner_department']} / 기한: {row['due_date']}"
            conn.execute("INSERT OR IGNORE INTO notifications(task_id,employee_id,level,message,acknowledged,generated_date,created_at) VALUES (?,?,?,?,0,?,?)",(row['id'],row['employee_id'],level,msg,today_s,now))
    conn.commit(); conn.close()


def list_notifications(show_ack=False):
    refresh_notifications(); conn=connect(); q="SELECT n.*,e.name employee_name,t.title task_title,t.assigned_to FROM notifications n JOIN employees e ON n.employee_id=e.id JOIN onboarding_tasks t ON n.task_id=t.id"
    if not show_ack: q += " WHERE n.acknowledged=0"
    q += " ORDER BY CASE n.level WHEN '지연' THEN 1 ELSE 2 END,n.id DESC"
    rows=conn.execute(q).fetchall(); conn.close(); return rows


def acknowledge_notification(notification_id):
    conn=connect(); conn.execute("UPDATE notifications SET acknowledged=1 WHERE id=?",(notification_id,)); conn.commit(); conn.close()


def list_change_logs(limit=50):
    conn=connect(); rows=conn.execute("SELECT * FROM change_logs ORDER BY id DESC LIMIT ?",(limit,)).fetchall(); conn.close(); return rows

# ---------- 3차 확장 기능: 모듈/문서/설정 백업 ----------
def ensure_phase3_schema():
    conn = connect(); cur = conn.cursor(); now = datetime.now().isoformat(timespec="seconds")
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS modules (
            code TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            route TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 100,
            admin_only INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT '기타',
            required INTEGER DEFAULT 0,
            file_name TEXT DEFAULT '',
            stored_name TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    defaults = [
        ("dashboard","온보딩 현황","/",1,10,0),
        ("employees","입사자 관리","/employees",1,20,0),
        ("templates","온보딩 템플릿","/templates",1,30,1),
        ("notifications","알림 및 지연업무","/notifications",1,40,0),
        ("documents","문서·양식 관리","/documents",1,50,1),
        ("data","Excel 자료관리","/data",1,60,1),
        ("settings","관리자 설정","/settings",1,90,1),
    ]
    for row in defaults:
        cur.execute("INSERT OR IGNORE INTO modules(code,display_name,route,enabled,sort_order,admin_only,updated_at) VALUES (?,?,?,?,?,?,?)", (*row,now))
    conn.commit(); conn.close()


def list_modules(include_disabled=True):
    ensure_phase3_schema(); conn=connect(); q="SELECT * FROM modules"
    if not include_disabled: q += " WHERE enabled=1"
    q += " ORDER BY sort_order, code"; rows=conn.execute(q).fetchall(); conn.close(); return rows


def update_module(code, display_name, enabled, sort_order):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE modules SET display_name=?, enabled=?, sort_order=?, updated_at=? WHERE code=?",(display_name,enabled,sort_order,now,code))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','메뉴/툴 설정 변경',?,?,?)",(code,f"{display_name} / {'사용' if enabled else '미사용'} / 순서 {sort_order}",now))
    conn.commit(); conn.close()


def list_document_forms():
    ensure_phase3_schema(); conn=connect(); rows=conn.execute("SELECT * FROM document_forms ORDER BY category,name").fetchall(); conn.close(); return rows


def add_document_form(name, category, required, file_name, stored_name):
    ensure_phase3_schema(); conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    cur=conn.execute("INSERT INTO document_forms(name,category,required,file_name,stored_name,active,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",(name,category,required,file_name,stored_name,now,now))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','문서/양식 추가',?,?,?)",(str(cur.lastrowid),name,now))
    conn.commit(); conn.close(); return cur.lastrowid


def update_document_form(doc_id, name, category, required, active):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE document_forms SET name=?,category=?,required=?,active=?,updated_at=? WHERE id=?",(name,category,required,active,now,doc_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','문서/양식 수정',?,?,?)",(str(doc_id),name,now))
    conn.commit(); conn.close()


def get_document_form(doc_id):
    conn=connect(); row=conn.execute("SELECT * FROM document_forms WHERE id=?",(doc_id,)).fetchone(); conn.close(); return row


def delete_document_form(doc_id):
    conn=connect(); row=conn.execute("SELECT * FROM document_forms WHERE id=?",(doc_id,)).fetchone(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM document_forms WHERE id=?",(doc_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','문서/양식 삭제',?,?,?)",(str(doc_id),row['name'] if row else '',now))
    conn.commit(); conn.close(); return row


def export_config():
    ensure_phase3_schema(); conn=connect()
    payload = {
        "version": 1,
        "settings": [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()],
        "permissions": [dict(r) for r in conn.execute("SELECT * FROM role_permissions").fetchall()],
        "department_owners": [dict(r) for r in conn.execute("SELECT * FROM department_owners").fetchall()],
        "modules": [dict(r) for r in conn.execute("SELECT * FROM modules").fetchall()],
        "templates": [dict(r) for r in conn.execute("SELECT * FROM onboarding_templates").fetchall()],
        "template_items": [dict(r) for r in conn.execute("SELECT * FROM template_items").fetchall()],
        "document_forms": [dict(r) for r in conn.execute("SELECT * FROM document_forms").fetchall()],
    }
    conn.close(); return payload


def restore_config(payload):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    try:
        conn.execute("BEGIN")
        for r in payload.get("settings",[]):
            conn.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(r["key"],r["value"],now))
        for r in payload.get("permissions",[]):
            conn.execute("INSERT INTO role_permissions(role,can_view_all,can_edit_employee,can_manage_templates,can_manage_settings,can_manage_permissions) VALUES (?,?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET can_view_all=excluded.can_view_all,can_edit_employee=excluded.can_edit_employee,can_manage_templates=excluded.can_manage_templates,can_manage_settings=excluded.can_manage_settings,can_manage_permissions=excluded.can_manage_permissions",(r["role"],r.get("can_view_all",0),r.get("can_edit_employee",0),r.get("can_manage_templates",0),r.get("can_manage_settings",0),r.get("can_manage_permissions",0)))
        for r in payload.get("department_owners",[]):
            conn.execute("INSERT INTO department_owners(department,owner_name,contact,updated_at) VALUES (?,?,?,?) ON CONFLICT(department) DO UPDATE SET owner_name=excluded.owner_name,contact=excluded.contact,updated_at=excluded.updated_at",(r["department"],r.get("owner_name",""),r.get("contact",""),now))
        for r in payload.get("modules",[]):
            conn.execute("INSERT INTO modules(code,display_name,route,enabled,sort_order,admin_only,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET display_name=excluded.display_name,enabled=excluded.enabled,sort_order=excluded.sort_order,admin_only=excluded.admin_only,updated_at=excluded.updated_at",(r["code"],r["display_name"],r["route"],r.get("enabled",1),r.get("sort_order",100),r.get("admin_only",0),now))
        # 템플릿은 기존 입사자 task 연결 보호를 위해 ID 기준 upsert만 수행
        for r in payload.get("templates",[]):
            conn.execute("INSERT INTO onboarding_templates(id,name,process_type,match_department,match_job_title,match_employment_type,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,process_type=excluded.process_type,match_department=excluded.match_department,match_job_title=excluded.match_job_title,match_employment_type=excluded.match_employment_type,active=excluded.active,updated_at=excluded.updated_at",(r["id"],r["name"],r.get("process_type","온보딩"),r.get("match_department",""),r.get("match_job_title",""),r.get("match_employment_type",""),r.get("active",1),r.get("created_at",now),now))
        for r in payload.get("template_items",[]):
            conn.execute("INSERT INTO template_items(id,template_id,title,owner_department,due_offset,required,enabled,sort_order,created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET template_id=excluded.template_id,title=excluded.title,owner_department=excluded.owner_department,due_offset=excluded.due_offset,required=excluded.required,enabled=excluded.enabled,sort_order=excluded.sort_order",(r["id"],r["template_id"],r["title"],r["owner_department"],r.get("due_offset",0),r.get("required",1),r.get("enabled",1),r.get("sort_order",100),r.get("created_at",now)))
        conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','관리자 설정 복원','config','백업 설정 복원',?)",(now,))
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

# ---------- v1.2 운영 안정성/사용성 개선 ----------
def ensure_v12_schema():
    """v1.1 DB를 그대로 사용할 수 있도록 안전하게 확장합니다."""
    ensure_phase3_schema()
    conn = connect(); cur = conn.cursor(); now = datetime.now().isoformat(timespec="seconds")
    migrations = [
        ("onboarding_tasks", "notes", "ALTER TABLE onboarding_tasks ADD COLUMN notes TEXT DEFAULT ''"),
        ("onboarding_tasks", "updated_at", "ALTER TABLE onboarding_tasks ADD COLUMN updated_at TEXT DEFAULT ''"),
    ]
    for table, column, sql in migrations:
        if not _column_exists(conn, table, column):
            cur.execute(sql)
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS employee_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            document_form_id INTEGER NOT NULL,
            status TEXT DEFAULT '미제출',
            file_name TEXT DEFAULT '',
            stored_name TEXT DEFAULT '',
            note TEXT DEFAULT '',
            submitted_at TEXT,
            confirmed_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(employee_id, document_form_id),
            FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
            FOREIGN KEY(document_form_id) REFERENCES document_forms(id) ON DELETE CASCADE
        );
        """
    )
    # 기존 업무의 updated_at이 비어 있으면 created_at으로 보정
    cur.execute("UPDATE onboarding_tasks SET updated_at=created_at WHERE COALESCE(updated_at,'')='' ")
    conn.commit(); conn.close()
    sync_employee_document_assignments()


def refresh_employee_statuses():
    """입사일이 도래한 입사예정자를 자동으로 재직 상태로 전환합니다."""
    conn = connect(); today = datetime.now().date().isoformat(); now = datetime.now().isoformat(timespec="seconds")
    rows = conn.execute("SELECT id,name FROM employees WHERE status='입사예정' AND start_date<=?", (today,)).fetchall()
    for r in rows:
        conn.execute("UPDATE employees SET status='재직', updated_at=? WHERE id=?", (now, r["id"]))
        conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('시스템','입사상태 자동 전환',?,?,?)", (str(r["id"]), f"{r['name']} / 입사예정 → 재직", now))
    conn.commit(); conn.close()


def find_duplicate_employee(employee_no="", name="", department="", start_date=""):
    conn = connect(); row = None
    employee_no = (employee_no or "").strip()
    if employee_no:
        row = conn.execute("SELECT * FROM employees WHERE employee_no=? AND status!='입사취소' LIMIT 1", (employee_no,)).fetchone()
    if not row and name and department and start_date:
        row = conn.execute("SELECT * FROM employees WHERE name=? AND department=? AND start_date=? AND status!='입사취소' LIMIT 1", (name.strip(), department.strip(), start_date)).fetchone()
    conn.close(); return row


def create_employee(name, employee_no, department, job_title, employment_type, start_date):
    duplicate = find_duplicate_employee(employee_no, name, department, start_date)
    if duplicate:
        raise ValueError(f"중복 입사자: {duplicate['name']} ({duplicate['employee_no'] or duplicate['start_date']})")
    # 날짜 형식 선검증
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    conn = connect(); now = datetime.now().isoformat(timespec="seconds")
    template = choose_template(conn, department, job_title, employment_type)
    template_id = template["id"] if template else None
    status = "재직" if start_dt.date() <= datetime.now().date() else "입사예정"
    cur = conn.execute(
        "INSERT INTO employees(name, employee_no, department, job_title, employment_type, start_date, status, template_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), (employee_no or "").strip(), department.strip(), (job_title or "").strip(), employment_type or "정규직", start_date, status, template_id, now, now),
    )
    employee_id = cur.lastrowid
    if template_id:
        items = conn.execute("SELECT * FROM template_items WHERE template_id=? AND enabled=1 ORDER BY sort_order,id", (template_id,)).fetchall()
        for item in items:
            due_date = (start_dt + timedelta(days=item["due_offset"])).strftime("%Y-%m-%d")
            assigned_to = _owner_name(conn, item["owner_department"], department)
            conn.execute(
                "INSERT INTO onboarding_tasks(employee_id, template_item_id, title, owner_department, assigned_to, due_date, required, status, notes, completed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, '미완료', '', NULL, ?, ?)",
                (employee_id, item["id"], item["title"], item["owner_department"], assigned_to, due_date, item["required"], now, now),
            )
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사자 등록',?,?,?)", (str(employee_id), f"{name} / {department} / {start_date} / 템플릿:{template['name'] if template else '없음'}", now))
    conn.commit(); conn.close()
    sync_employee_document_assignments(employee_id)
    return employee_id


def list_employees(search="", department="", status=""):
    refresh_employee_statuses()
    conn = connect(); query="SELECT * FROM employees WHERE 1=1"; params=[]
    if search:
        query += " AND (name LIKE ? OR employee_no LIKE ? OR job_title LIKE ?)"; token=f"%{search}%"; params += [token,token,token]
    if department: query += " AND department=?"; params.append(department)
    if status: query += " AND status=?"; params.append(status)
    query += " ORDER BY CASE status WHEN '입사예정' THEN 1 WHEN '재직' THEN 2 ELSE 3 END, start_date DESC,id DESC"
    rows=conn.execute(query,params).fetchall(); conn.close(); return rows


def get_task(task_id):
    conn=connect(); row=conn.execute("SELECT * FROM onboarding_tasks WHERE id=?",(task_id,)).fetchone(); conn.close(); return row


def add_employee_task(employee_id, title, owner_department, assigned_to, due_date, required=1, notes=""):
    datetime.strptime(due_date, "%Y-%m-%d")
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    emp=conn.execute("SELECT department,name FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not emp: conn.close(); raise ValueError("입사자를 찾을 수 없습니다.")
    final_assigned=(assigned_to or "").strip() or _owner_name(conn, owner_department, emp["department"])
    cur=conn.execute("INSERT INTO onboarding_tasks(employee_id,template_item_id,title,owner_department,assigned_to,due_date,required,status,notes,completed_at,created_at,updated_at) VALUES (?,NULL,?,?,?,?,?,'미완료',?,NULL,?,?)",(employee_id,title.strip(),owner_department.strip(),final_assigned,due_date,required,notes.strip(),now,now))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','개별 온보딩 업무 추가',?,?,?)",(str(cur.lastrowid),f"{emp['name']} / {title}",now))
    conn.commit(); conn.close(); return cur.lastrowid


def update_task(task_id, title, owner_department, assigned_to, due_date, required=1, notes=""):
    datetime.strptime(due_date, "%Y-%m-%d")
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT t.*,e.department,e.name employee_name FROM onboarding_tasks t JOIN employees e ON e.id=t.employee_id WHERE t.id=?",(task_id,)).fetchone()
    if not row: conn.close(); raise ValueError("업무를 찾을 수 없습니다.")
    final_assigned=(assigned_to or "").strip() or _owner_name(conn, owner_department, row["department"])
    conn.execute("UPDATE onboarding_tasks SET title=?,owner_department=?,assigned_to=?,due_date=?,required=?,notes=?,updated_at=? WHERE id=?",(title.strip(),owner_department.strip(),final_assigned,due_date,required,notes.strip(),now,task_id))
    conn.execute("UPDATE notifications SET acknowledged=1 WHERE task_id=? AND acknowledged=0",(task_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','온보딩 업무 수정',?,?,?)",(str(task_id),f"{row['employee_name']} / {title} / 기한 {due_date} / 담당 {final_assigned}",now))
    conn.commit(); conn.close()


def set_task_cancelled(task_id, cancelled=True):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT * FROM onboarding_tasks WHERE id=?",(task_id,)).fetchone()
    if not row: conn.close(); return
    new_status="취소" if cancelled else "미완료"
    conn.execute("UPDATE onboarding_tasks SET status=?,completed_at=NULL,updated_at=? WHERE id=?",(new_status,now,task_id))
    conn.execute("UPDATE notifications SET acknowledged=1 WHERE task_id=? AND acknowledged=0",(task_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','온보딩 업무 상태 변경',?,?,?)",(str(task_id),new_status,now))
    conn.commit(); conn.close()


def toggle_task(task_id):
    conn=connect(); row=conn.execute("SELECT * FROM onboarding_tasks WHERE id=?",(task_id,)).fetchone()
    if not row or row["status"]=="취소": conn.close(); return
    now=datetime.now().isoformat(timespec="seconds"); new_status="완료" if row["status"]!="완료" else "미완료"; completed_at=now if new_status=="완료" else None
    conn.execute("UPDATE onboarding_tasks SET status=?,completed_at=?,updated_at=? WHERE id=?",(new_status,completed_at,now,task_id))
    if new_status=="완료": conn.execute("UPDATE notifications SET acknowledged=1 WHERE task_id=? AND acknowledged=0",(task_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('담당자','온보딩 업무 상태 변경',?,?,?)",(str(task_id),new_status,now))
    conn.commit(); conn.close()


def change_employee_start_date(employee_id, new_start_date, shift_incomplete_tasks=True):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT start_date,name,status FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not row: conn.close(); return
    old=datetime.strptime(row["start_date"],"%Y-%m-%d").date(); new=datetime.strptime(new_start_date,"%Y-%m-%d").date(); diff=(new-old).days
    status = "입사예정" if new > datetime.now().date() else ("입사취소" if row["status"]=="입사취소" else "재직")
    conn.execute("UPDATE employees SET start_date=?,status=?,exception_status='입사일 변경',updated_at=? WHERE id=?",(new_start_date,status,now,employee_id))
    if shift_incomplete_tasks and diff:
        tasks=conn.execute("SELECT id,due_date FROM onboarding_tasks WHERE employee_id=? AND status='미완료'",(employee_id,)).fetchall()
        for t in tasks:
            due=datetime.strptime(t["due_date"],"%Y-%m-%d").date()+timedelta(days=diff)
            conn.execute("UPDATE onboarding_tasks SET due_date=?,updated_at=? WHERE id=?",(due.isoformat(),now,t["id"]))
    conn.execute("UPDATE notifications SET acknowledged=1 WHERE employee_id=? AND acknowledged=0",(employee_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사일 변경',?,?,?)",(str(employee_id),f"{row['start_date']} → {new_start_date} / 미완료 기한 {'이동' if shift_incomplete_tasks else '유지'}",now))
    conn.commit(); conn.close()


def cancel_employee(employee_id):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE employees SET status='입사취소',exception_status='입사 취소',updated_at=? WHERE id=?",(now,employee_id))
    conn.execute("UPDATE onboarding_tasks SET status='취소',updated_at=? WHERE employee_id=? AND status!='완료'",(now,employee_id))
    conn.execute("UPDATE notifications SET acknowledged=1 WHERE employee_id=? AND acknowledged=0",(employee_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사 취소',?,'미완료 온보딩 업무 자동 취소',?)",(str(employee_id),now))
    conn.commit(); conn.close()


def dashboard_summary():
    refresh_employee_statuses()
    conn=connect(); today=datetime.now().strftime("%Y-%m-%d")
    total=conn.execute("SELECT COUNT(*) c FROM employees WHERE status!='입사취소'").fetchone()["c"]
    upcoming=conn.execute("SELECT COUNT(*) c FROM employees WHERE status='입사예정'").fetchone()["c"]
    tasks=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks t JOIN employees e ON e.id=t.employee_id WHERE t.status!='취소' AND e.status!='입사취소'").fetchone()["c"]
    completed=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks t JOIN employees e ON e.id=t.employee_id WHERE t.status='완료' AND e.status!='입사취소'").fetchone()["c"]
    overdue=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks t JOIN employees e ON e.id=t.employee_id WHERE t.status='미완료' AND e.status!='입사취소' AND t.due_date<?",(today,)).fetchone()["c"]
    unassigned=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks t JOIN employees e ON e.id=t.employee_id WHERE t.status='미완료' AND e.status!='입사취소' AND (TRIM(COALESCE(t.assigned_to,''))='' OR t.assigned_to=t.owner_department)").fetchone()["c"]
    cancelled=conn.execute("SELECT COUNT(*) c FROM employees WHERE status='입사취소'").fetchone()["c"]
    conn.close(); return {"total":total,"upcoming":upcoming,"tasks":tasks,"completed":completed,"overdue":overdue,"unassigned":unassigned,"cancelled":cancelled,"progress":round((completed/tasks)*100,1) if tasks else 0}


def employee_progress(employee_id):
    conn=connect(); total=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE employee_id=? AND status!='취소'",(employee_id,)).fetchone()["c"]; done=conn.execute("SELECT COUNT(*) c FROM onboarding_tasks WHERE employee_id=? AND status='완료'",(employee_id,)).fetchone()["c"]; conn.close(); return round((done/total)*100) if total else 0


def refresh_notifications():
    conn=connect(); today=datetime.now().date(); now=datetime.now().isoformat(timespec="seconds"); today_s=today.isoformat()
    settings={r["key"]:r["value"] for r in conn.execute("SELECT key,value FROM settings").fetchall()}
    notify_before=int(settings.get("notify_before_days","2")); escalate_after=int(settings.get("escalate_after_days","3"))
    rows=conn.execute("SELECT t.*,e.name employee_name FROM onboarding_tasks t JOIN employees e ON t.employee_id=e.id WHERE t.status='미완료' AND e.status!='입사취소'").fetchall()
    for row in rows:
        due=datetime.strptime(row["due_date"],"%Y-%m-%d").date(); delta=(due-today).days
        level=None
        if delta <= -escalate_after: level="에스컬레이션"
        elif delta < 0: level="지연"
        elif delta <= notify_before: level="임박"
        if level:
            msg=f"{row['employee_name']} - {row['title']} / 담당: {row['assigned_to'] or row['owner_department']} / 기한: {row['due_date']}"
            conn.execute("INSERT OR IGNORE INTO notifications(task_id,employee_id,level,message,acknowledged,generated_date,created_at) VALUES (?,?,?,?,0,?,?)",(row['id'],row['employee_id'],level,msg,today_s,now))
    conn.commit(); conn.close()


def list_notifications(show_ack=False):
    refresh_notifications(); conn=connect()
    q="""SELECT n.*,e.name employee_name,t.title task_title,t.assigned_to
         FROM notifications n JOIN employees e ON n.employee_id=e.id JOIN onboarding_tasks t ON n.task_id=t.id
         WHERE t.status='미완료' AND e.status!='입사취소'"""
    if not show_ack: q += " AND n.acknowledged=0"
    q += " ORDER BY CASE n.level WHEN '에스컬레이션' THEN 1 WHEN '지연' THEN 2 ELSE 3 END,n.id DESC"
    rows=conn.execute(q).fetchall(); conn.close(); return rows


def sync_employee_from_template(employee_id):
    """완료/취소 업무는 보존하면서 템플릿의 최신 활성 항목을 미완료 업무에 반영합니다."""
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    emp=conn.execute("SELECT * FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not emp: conn.close(); raise ValueError("입사자를 찾을 수 없습니다.")
    template=conn.execute("SELECT * FROM onboarding_templates WHERE id=?",(emp["template_id"],)).fetchone() if emp["template_id"] else None
    if not template:
        template=choose_template(conn,emp["department"],emp["job_title"],emp["employment_type"])
        if template: conn.execute("UPDATE employees SET template_id=?,updated_at=? WHERE id=?",(template["id"],now,employee_id))
    if not template: conn.close(); return {"added":0,"updated":0,"preserved":0,"template":"없음"}
    start_dt=datetime.strptime(emp["start_date"],"%Y-%m-%d")
    items=conn.execute("SELECT * FROM template_items WHERE template_id=? AND enabled=1 ORDER BY sort_order,id",(template["id"],)).fetchall()
    added=updated=preserved=0
    for item in items:
        task=conn.execute("SELECT * FROM onboarding_tasks WHERE employee_id=? AND template_item_id=? LIMIT 1",(employee_id,item["id"])).fetchone()
        due=(start_dt+timedelta(days=item["due_offset"])).strftime("%Y-%m-%d")
        assigned=_owner_name(conn,item["owner_department"],emp["department"])
        if not task:
            conn.execute("INSERT INTO onboarding_tasks(employee_id,template_item_id,title,owner_department,assigned_to,due_date,required,status,notes,completed_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'미완료','',NULL,?,?)",(employee_id,item["id"],item["title"],item["owner_department"],assigned,due,item["required"],now,now)); added+=1
        elif task["status"]=="미완료":
            conn.execute("UPDATE onboarding_tasks SET title=?,owner_department=?,assigned_to=?,due_date=?,required=?,updated_at=? WHERE id=?",(item["title"],item["owner_department"],assigned,due,item["required"],now,task["id"])); updated+=1
        else:
            preserved+=1
    conn.execute("UPDATE notifications SET acknowledged=1 WHERE employee_id=? AND acknowledged=0",(employee_id,))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','템플릿 최신화',?,?,?)",(str(employee_id),f"{template['name']} / 추가 {added} / 수정 {updated} / 완료·취소 보존 {preserved}",now))
    conn.commit(); conn.close(); return {"added":added,"updated":updated,"preserved":preserved,"template":template["name"]}


def update_module(code, display_name, enabled, sort_order):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE modules SET display_name=?, enabled=?, sort_order=?, updated_at=? WHERE code=?",(display_name,enabled,sort_order,now,code))
    setting_map={"dashboard":"menu_dashboard","employees":"menu_employees","templates":"menu_templates","notifications":"menu_notifications","settings":"menu_settings"}
    if code in setting_map:
        key=setting_map[code]
        conn.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(key,display_name,now))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','메뉴/툴 설정 변경',?,?,?)",(code,f"{display_name} / {'사용' if enabled else '미사용'} / 순서 {sort_order}",now))
    conn.commit(); conn.close()


def sync_employee_document_assignments(employee_id=None):
    ensure_phase3_schema(); conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    if employee_id is None:
        employees=conn.execute("SELECT id FROM employees WHERE status!='입사취소'").fetchall()
    else:
        employees=conn.execute("SELECT id FROM employees WHERE id=? AND status!='입사취소'",(employee_id,)).fetchall()
    docs=conn.execute("SELECT id FROM document_forms WHERE active=1").fetchall()
    for e in employees:
        for d in docs:
            conn.execute("INSERT OR IGNORE INTO employee_documents(employee_id,document_form_id,status,updated_at) VALUES (?,?,'미제출',?)",(e["id"],d["id"],now))
    conn.commit(); conn.close()


def list_employee_documents(employee_id):
    sync_employee_document_assignments(employee_id)
    conn=connect(); rows=conn.execute("""SELECT ed.*,d.name,d.category,d.required,d.file_name form_file_name,d.stored_name form_stored_name,d.active
        FROM employee_documents ed JOIN document_forms d ON d.id=ed.document_form_id
        WHERE ed.employee_id=? AND d.active=1 ORDER BY d.required DESC,d.category,d.name""",(employee_id,)).fetchall(); conn.close(); return rows


def get_employee_document(employee_document_id):
    conn=connect(); row=conn.execute("SELECT ed.*,d.name,d.file_name form_file_name,d.stored_name form_stored_name FROM employee_documents ed JOIN document_forms d ON d.id=ed.document_form_id WHERE ed.id=?",(employee_document_id,)).fetchone(); conn.close(); return row


def submit_employee_document(employee_document_id, file_name, stored_name, actor="신입사원"):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT ed.*,d.name FROM employee_documents ed JOIN document_forms d ON d.id=ed.document_form_id WHERE ed.id=?",(employee_document_id,)).fetchone()
    if not row: conn.close(); raise ValueError("문서 항목을 찾을 수 없습니다.")
    conn.execute("UPDATE employee_documents SET status='제출',file_name=?,stored_name=?,submitted_at=?,confirmed_at=NULL,updated_at=? WHERE id=?",(file_name,stored_name,now,now,employee_document_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES (?,'입사서류 제출',?,?,?)",(actor,str(employee_document_id),row["name"],now))
    conn.commit(); conn.close()


def review_employee_document(employee_document_id, status, note=""):
    if status not in {"미제출","제출","확인완료","보완요청"}: raise ValueError("허용되지 않은 문서 상태입니다.")
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    row=conn.execute("SELECT ed.*,d.name FROM employee_documents ed JOIN document_forms d ON d.id=ed.document_form_id WHERE ed.id=?",(employee_document_id,)).fetchone()
    if not row: conn.close(); raise ValueError("문서 항목을 찾을 수 없습니다.")
    confirmed_at=now if status=="확인완료" else None
    conn.execute("UPDATE employee_documents SET status=?,note=?,confirmed_at=?,updated_at=? WHERE id=?",(status,note.strip(),confirmed_at,now,employee_document_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사서류 확인',?,?,?)",(str(employee_document_id),f"{row['name']} / {status} / {note}",now))
    conn.commit(); conn.close()


def add_document_form(name, category, required, file_name, stored_name):
    ensure_phase3_schema(); conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    cur=conn.execute("INSERT INTO document_forms(name,category,required,file_name,stored_name,active,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",(name,category,required,file_name,stored_name,now,now))
    doc_id=cur.lastrowid
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','문서/양식 추가',?,?,?)",(str(doc_id),name,now))
    conn.commit(); conn.close(); sync_employee_document_assignments(); return doc_id


def update_document_form(doc_id, name, category, required, active):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE document_forms SET name=?,category=?,required=?,active=?,updated_at=? WHERE id=?",(name,category,required,active,now,doc_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','문서/양식 수정',?,?,?)",(str(doc_id),name,now))
    conn.commit(); conn.close()
    if active: sync_employee_document_assignments()


def create_database_backup_bytes():
    import tempfile, os
    ensure_v12_schema()
    source=connect()
    tmp=tempfile.NamedTemporaryFile(delete=False,suffix=".db"); tmp.close()
    try:
        dest=sqlite3.connect(tmp.name); source.backup(dest); dest.close(); source.close()
        with open(tmp.name,"rb") as f: return f.read()
    finally:
        try: source.close()
        except Exception: pass
        try: os.unlink(tmp.name)
        except OSError: pass


def update_employee_info(employee_id, name, employee_no, department, job_title, employment_type, reapply_template=False):
    conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    current=conn.execute("SELECT * FROM employees WHERE id=?",(employee_id,)).fetchone()
    if not current: conn.close(); raise ValueError("입사자를 찾을 수 없습니다.")
    employee_no=(employee_no or "").strip(); name=name.strip(); department=department.strip()
    if employee_no:
        dup=conn.execute("SELECT id,name FROM employees WHERE employee_no=? AND id!=? AND status!='입사취소' LIMIT 1",(employee_no,employee_id)).fetchone()
        if dup: conn.close(); raise ValueError(f"이미 사용 중인 사번입니다: {dup['name']}")
    dup2=conn.execute("SELECT id FROM employees WHERE name=? AND department=? AND start_date=? AND id!=? AND status!='입사취소' LIMIT 1",(name,department,current['start_date'],employee_id)).fetchone()
    if dup2: conn.close(); raise ValueError("동일한 성명·소속부서·입사일의 입사자가 이미 있습니다.")
    new_template_id=current["template_id"]
    if reapply_template:
        chosen=choose_template(conn,department,job_title,employment_type)
        new_template_id=chosen["id"] if chosen else None
    conn.execute("UPDATE employees SET name=?,employee_no=?,department=?,job_title=?,employment_type=?,template_id=?,updated_at=? WHERE id=?",(name,employee_no,department,(job_title or '').strip(),employment_type,new_template_id,now,employee_id))
    conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('인사담당자','입사자 기본정보 수정',?,?,?)",(str(employee_id),f"{name} / {department} / {job_title} / {employment_type} / 템플릿 {'재선택' if reapply_template else '유지'}",now))
    conn.commit(); conn.close()
    if reapply_template and new_template_id:
        return sync_employee_from_template(employee_id)
    return None


def restore_config(payload):
    """v1.2: 문서/양식 메타데이터까지 포함해 관리자 설정을 복원합니다."""
    ensure_v12_schema(); conn=connect(); now=datetime.now().isoformat(timespec="seconds")
    try:
        conn.execute("BEGIN")
        for r in payload.get("settings",[]):
            conn.execute("INSERT INTO settings(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(r["key"],r["value"],now))
        for r in payload.get("permissions",[]):
            conn.execute("INSERT INTO role_permissions(role,can_view_all,can_edit_employee,can_manage_templates,can_manage_settings,can_manage_permissions) VALUES (?,?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET can_view_all=excluded.can_view_all,can_edit_employee=excluded.can_edit_employee,can_manage_templates=excluded.can_manage_templates,can_manage_settings=excluded.can_manage_settings,can_manage_permissions=excluded.can_manage_permissions",(r["role"],r.get("can_view_all",0),r.get("can_edit_employee",0),r.get("can_manage_templates",0),r.get("can_manage_settings",0),r.get("can_manage_permissions",0)))
        for r in payload.get("department_owners",[]):
            conn.execute("INSERT INTO department_owners(department,owner_name,contact,updated_at) VALUES (?,?,?,?) ON CONFLICT(department) DO UPDATE SET owner_name=excluded.owner_name,contact=excluded.contact,updated_at=excluded.updated_at",(r["department"],r.get("owner_name",""),r.get("contact",""),now))
        for r in payload.get("modules",[]):
            conn.execute("INSERT INTO modules(code,display_name,route,enabled,sort_order,admin_only,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET display_name=excluded.display_name,enabled=excluded.enabled,sort_order=excluded.sort_order,admin_only=excluded.admin_only,updated_at=excluded.updated_at",(r["code"],r["display_name"],r["route"],r.get("enabled",1),r.get("sort_order",100),r.get("admin_only",0),now))
        for r in payload.get("templates",[]):
            conn.execute("INSERT INTO onboarding_templates(id,name,process_type,match_department,match_job_title,match_employment_type,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,process_type=excluded.process_type,match_department=excluded.match_department,match_job_title=excluded.match_job_title,match_employment_type=excluded.match_employment_type,active=excluded.active,updated_at=excluded.updated_at",(r["id"],r["name"],r.get("process_type","온보딩"),r.get("match_department",""),r.get("match_job_title",""),r.get("match_employment_type",""),r.get("active",1),r.get("created_at",now),now))
        for r in payload.get("template_items",[]):
            conn.execute("INSERT INTO template_items(id,template_id,title,owner_department,due_offset,required,enabled,sort_order,created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET template_id=excluded.template_id,title=excluded.title,owner_department=excluded.owner_department,due_offset=excluded.due_offset,required=excluded.required,enabled=excluded.enabled,sort_order=excluded.sort_order",(r["id"],r["template_id"],r["title"],r["owner_department"],r.get("due_offset",0),r.get("required",1),r.get("enabled",1),r.get("sort_order",100),r.get("created_at",now)))
        for r in payload.get("document_forms",[]):
            conn.execute("INSERT INTO document_forms(id,name,category,required,file_name,stored_name,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=excluded.category,required=excluded.required,file_name=excluded.file_name,stored_name=excluded.stored_name,active=excluded.active,updated_at=excluded.updated_at",(r["id"],r["name"],r.get("category","기타"),r.get("required",0),r.get("file_name",""),r.get("stored_name",""),r.get("active",1),r.get("created_at",now),now))
        conn.execute("INSERT INTO change_logs(actor,action,target,detail,created_at) VALUES ('관리자','관리자 설정 복원','config','백업 설정 복원',?)",(now,))
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()
    sync_employee_document_assignments()
