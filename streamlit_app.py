from datetime import date, timedelta
from pathlib import Path
import sqlite3

import streamlit as st

import database as db

st.set_page_config(
    page_title="신입사원 온보딩 자동화",
    page_icon="👋",
    layout="wide",
)


def boot_database():
    db.init_db()
    db.ensure_phase3_schema()
    db.ensure_v12_schema()
    seed_demo_data()


def seed_demo_data():
    """공개 데모에서 첫 화면이 비어 있지 않도록 가상 데이터를 1회 생성합니다."""
    if db.list_employees():
        return

    # 담당자 예시
    for department, owner, contact in [
        ("인사", "김인사", "hr@example.com"),
        ("IT", "박IT", "it@example.com"),
        ("총무", "이총무", "ga@example.com"),
        ("개발팀", "최개발", "dev@example.com"),
        ("영업팀", "정영업", "sales@example.com"),
    ]:
        db.upsert_department_owner(department, owner, contact)

    today = date.today()
    demo_people = [
        ("김하늘", "D-001", "개발팀", "백엔드 개발", "정규직", today + timedelta(days=7)),
        ("이도윤", "S-002", "영업팀", "해외영업", "정규직", today + timedelta(days=2)),
        ("박서연", "H-003", "인사", "인사운영", "계약직", today - timedelta(days=3)),
    ]
    for name, no, dept, job, emp_type, start in demo_people:
        try:
            db.create_employee(name, no, dept, job, emp_type, start.isoformat())
        except ValueError:
            pass

    # 문서 양식 예시
    if not db.list_document_forms():
        db.add_document_form("근로계약서", "인사", 1, "", "")
        db.add_document_form("보안서약서", "보안", 1, "", "")
        db.add_document_form("개인정보 동의서", "인사", 1, "", "")


@st.cache_data(ttl=20)
def _help_text():
    return """
    **이 앱은 포트폴리오 공개 데모입니다.**  
    신입사원을 등록하면 템플릿을 기준으로 해야 할 일이 생성되고, 담당자와 기한을 관리할 수 있습니다.
    """


def rerun():
    st.cache_data.clear()
    st.rerun()


def reset_demo():
    try:
        if Path(db.DB_PATH).exists():
            Path(db.DB_PATH).unlink()
    except OSError as exc:
        st.error(f"데모 초기화 실패: {exc}")
        return
    rerun()


def status_badge(status):
    return {
        "완료": "✅ 완료",
        "미완료": "🕐 미완료",
        "취소": "🚫 취소",
        "입사예정": "🗓️ 입사예정",
        "재직": "👤 재직",
        "입사취소": "🚫 입사취소",
    }.get(status, status)


def page_dashboard():
    settings = db.get_settings()
    st.title(settings.get("program_title", "신입사원 온보딩 자동화"))
    st.caption("누가, 무엇을, 언제까지 준비해야 하는지 한 화면에서 확인하는 온보딩 업무관리 데모")

    summary = db.dashboard_summary()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("등록 입사자", summary["total"])
    c2.metric("입사 예정", summary["upcoming"])
    c3.metric("전체 업무", summary["tasks"])
    c4.metric("지연 업무", summary["overdue"])
    c5.metric("전체 진행률", f"{summary['progress']}%")

    st.subheader("입사자별 진행상태")
    employees = db.list_employees()
    if not employees:
        st.info("등록된 입사자가 없습니다.")
    else:
        rows = []
        for e in employees:
            rows.append({
                "이름": e["name"],
                "소속부서": e["department"],
                "직무": e["job_title"],
                "입사일": e["start_date"],
                "상태": e["status"],
                "진행률": f"{db.employee_progress(e['id'])}%",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    st.subheader("지금 확인할 업무")
    notes = db.list_notifications()
    if not notes:
        st.success("현재 미확인 알림이 없습니다.")
    else:
        for n in notes[:8]:
            icon = "🔴" if n["level"] == "에스컬레이션" else "🟠" if n["level"] == "지연" else "🟡"
            st.write(f"{icon} **{n['level']}** · {n['message']}")


def page_employees():
    st.title("입사자 관리")
    st.caption("입사자를 등록하면 조건에 맞는 온보딩 템플릿을 선택해 업무와 기한을 생성합니다.")

    with st.expander("➕ 신규 입사자 등록", expanded=False):
        with st.form("new_employee", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("이름 *")
            employee_no = c2.text_input("사번")
            c3, c4 = st.columns(2)
            department = c3.text_input("소속부서 *", placeholder="예: 개발팀")
            job_title = c4.text_input("직무/직책", placeholder="예: 백엔드 개발")
            c5, c6 = st.columns(2)
            employment_type = c5.selectbox("고용형태", ["정규직", "계약직", "인턴", "기타"])
            start_date = c6.date_input("입사일", value=date.today() + timedelta(days=7))
            submitted = st.form_submit_button("입사자 등록", type="primary")
            if submitted:
                if not name.strip() or not department.strip():
                    st.error("이름과 소속부서는 필수입니다.")
                else:
                    try:
                        db.create_employee(name, employee_no, department, job_title, employment_type, start_date.isoformat())
                        st.success("입사자를 등록했습니다.")
                        rerun()
                    except Exception as exc:
                        st.error(str(exc))

    employees = db.list_employees()
    if not employees:
        st.info("등록된 입사자가 없습니다.")
        return

    labels = {e["id"]: f"{e['name']} · {e['department']} · {e['start_date']}" for e in employees}
    employee_id = st.selectbox("상세 확인할 입사자", options=list(labels), format_func=lambda x: labels[x])
    e = db.get_employee(employee_id)
    progress = db.employee_progress(employee_id)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("상태", e["status"])
    c2.metric("진행률", f"{progress}%")
    c3.metric("입사일", e["start_date"])
    c4.metric("적용 템플릿", e["template_name"] or "없음")

    st.subheader("온보딩 업무")
    tasks = db.list_tasks(employee_id)
    if not tasks:
        st.warning("생성된 업무가 없습니다. 템플릿 조건을 확인하세요.")
    for task in tasks:
        cols = st.columns([0.7, 3.4, 1.3, 1.3, 1.1])
        done = task["status"] == "완료"
        if task["status"] == "취소":
            cols[0].write("🚫")
        else:
            checked = cols[0].checkbox("완료", value=done, key=f"task_{task['id']}", label_visibility="collapsed")
            if checked != done:
                db.toggle_task(task["id"])
                rerun()
        cols[1].write(f"**{task['title']}**")
        cols[2].write(task["assigned_to"] or task["owner_department"])
        cols[3].write(task["due_date"])
        cols[4].write(status_badge(task["status"]))

    with st.expander("➕ 개별 업무 추가"):
        with st.form(f"new_task_{employee_id}", clear_on_submit=True):
            title = st.text_input("업무명")
            c1, c2 = st.columns(2)
            owner_dept = c1.text_input("담당부서", value="소속부서")
            assigned_to = c2.text_input("담당자", placeholder="비워두면 담당부서 규칙 사용")
            c3, c4 = st.columns(2)
            due = c3.date_input("기한", value=date.today() + timedelta(days=3))
            required = c4.checkbox("필수 업무", value=True)
            notes = st.text_area("메모")
            if st.form_submit_button("업무 추가"):
                if not title.strip():
                    st.error("업무명을 입력하세요.")
                else:
                    try:
                        db.add_employee_task(employee_id, title, owner_dept, assigned_to, due.isoformat(), int(required), notes)
                        st.success("업무를 추가했습니다.")
                        rerun()
                    except Exception as exc:
                        st.error(str(exc))

    st.subheader("입사서류")
    docs = db.list_employee_documents(employee_id)
    if not docs:
        st.caption("등록된 문서 양식이 없습니다.")
    else:
        st.dataframe([
            {
                "문서": d["name"],
                "구분": d["category"],
                "필수": "필수" if d["required"] else "선택",
                "상태": d["status"],
                "검토메모": d["note"],
            }
            for d in docs
        ], use_container_width=True, hide_index=True)


def page_templates():
    st.title("온보딩 템플릿")
    st.caption("부서·직무·고용형태별로 필요한 준비업무를 코드 수정 없이 관리합니다.")

    templates = db.list_templates()
    if templates:
        rows = []
        for t in templates:
            rows.append({
                "ID": t["id"],
                "템플릿명": t["name"],
                "부서조건": t["match_department"],
                "직무조건": t["match_job_title"],
                "고용형태": t["match_employment_type"],
                "사용": "사용" if t["active"] else "미사용",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("➕ 새 템플릿 만들기"):
        with st.form("new_template", clear_on_submit=True):
            name = st.text_input("템플릿명")
            c1, c2, c3 = st.columns(3)
            dept = c1.text_input("부서 조건")
            job = c2.text_input("직무 조건")
            emp_type = c3.text_input("고용형태 조건")
            if st.form_submit_button("템플릿 생성"):
                if not name.strip():
                    st.error("템플릿명을 입력하세요.")
                else:
                    db.create_template(name, dept, job, emp_type)
                    st.success("템플릿을 생성했습니다.")
                    rerun()

    templates = db.list_templates()
    if not templates:
        return
    ids = [t["id"] for t in templates]
    names = {t["id"]: t["name"] for t in templates}
    tid = st.selectbox("항목을 관리할 템플릿", ids, format_func=lambda x: names[x])
    items = db.list_template_items(tid)
    st.dataframe([
        {
            "업무": i["title"],
            "담당부서": i["owner_department"],
            "입사일 기준": f"{i['due_offset']:+d}일",
            "구분": "필수" if i["required"] else "권장",
            "사용": "사용" if i["enabled"] else "미사용",
        }
        for i in items
    ], use_container_width=True, hide_index=True)

    with st.expander("➕ 템플릿 업무 추가"):
        with st.form(f"template_item_{tid}", clear_on_submit=True):
            title = st.text_input("업무명")
            c1, c2, c3 = st.columns(3)
            owner = c1.text_input("담당부서", value="인사")
            offset = c2.number_input("입사일 기준 일수", min_value=-60, max_value=180, value=0, step=1)
            order = c3.number_input("표시 순서", min_value=1, max_value=9999, value=100, step=10)
            required = st.checkbox("필수 업무", value=True)
            if st.form_submit_button("항목 추가"):
                if not title.strip():
                    st.error("업무명을 입력하세요.")
                else:
                    db.add_template_item(tid, title, owner, int(offset), int(required), int(order))
                    st.success("항목을 추가했습니다.")
                    rerun()


def page_notifications():
    st.title("알림 및 지연업무")
    st.caption("기한 임박·지연·에스컬레이션 업무를 한곳에서 확인합니다.")
    db.refresh_notifications()
    notes = db.list_notifications(show_ack=True)
    if not notes:
        st.success("현재 생성된 알림이 없습니다.")
        return
    for n in notes:
        cols = st.columns([1, 5, 1.2])
        cols[0].write(f"**{n['level']}**")
        cols[1].write(n["message"])
        if n["acknowledged"]:
            cols[2].caption("확인됨")
        elif cols[2].button("확인", key=f"ack_{n['id']}"):
            db.acknowledge_notification(n["id"])
            rerun()


def page_documents():
    st.title("문서·양식 관리")
    st.caption("근로계약서, 보안서약서 등 온보딩에 필요한 문서 항목을 관리합니다.")
    forms = db.list_document_forms()
    if forms:
        st.dataframe([
            {
                "문서명": f["name"],
                "구분": f["category"],
                "필수": "필수" if f["required"] else "선택",
                "상태": "사용" if f["active"] else "미사용",
            }
            for f in forms
        ], use_container_width=True, hide_index=True)

    with st.expander("➕ 문서 항목 추가"):
        with st.form("new_doc", clear_on_submit=True):
            name = st.text_input("문서명")
            c1, c2 = st.columns(2)
            category = c1.text_input("구분", value="인사")
            required = c2.checkbox("필수 문서", value=True)
            if st.form_submit_button("문서 항목 추가"):
                if not name.strip():
                    st.error("문서명을 입력하세요.")
                else:
                    db.add_document_form(name, category, int(required), "", "")
                    st.success("문서 항목을 추가했습니다.")
                    rerun()


def page_settings():
    st.title("관리자 설정")
    st.caption("프로그램 이름과 담당자 규칙 등 운영 설정을 코드 수정 없이 바꿀 수 있습니다.")
    settings = db.get_settings()

    with st.form("settings_form"):
        title = st.text_input("프로그램 이름", value=settings.get("program_title", ""))
        c1, c2 = st.columns(2)
        notify = c1.number_input("마감 임박 알림 기준(일)", min_value=0, max_value=30, value=int(settings.get("notify_before_days", "2")))
        escalate = c2.number_input("지연 에스컬레이션 기준(일)", min_value=1, max_value=60, value=int(settings.get("escalate_after_days", "3")))
        if st.form_submit_button("설정 저장", type="primary"):
            db.update_setting("program_title", title)
            db.update_setting("notify_before_days", str(int(notify)))
            db.update_setting("escalate_after_days", str(int(escalate)))
            st.success("설정을 저장했습니다.")
            rerun()

    st.subheader("담당자 규칙")
    owners = db.list_department_owners()
    if owners:
        st.dataframe([
            {"부서": o["department"], "담당자": o["owner_name"], "연락처": o["contact"]}
            for o in owners
        ], use_container_width=True, hide_index=True)
    with st.form("owner_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        department = c1.text_input("부서")
        owner = c2.text_input("담당자")
        contact = c3.text_input("연락처")
        if st.form_submit_button("담당자 규칙 저장"):
            if not department.strip():
                st.error("부서를 입력하세요.")
            else:
                db.upsert_department_owner(department, owner, contact)
                st.success("담당자 규칙을 저장했습니다.")
                rerun()

    st.subheader("최근 변경이력")
    logs = db.list_change_logs(30)
    st.dataframe([
        {
            "일시": l["created_at"],
            "작업자": l["actor"],
            "작업": l["action"],
            "대상": l["target"],
            "내용": l["detail"],
        }
        for l in logs
    ], use_container_width=True, hide_index=True)


def page_guide():
    st.title("사용 방법")
    st.markdown(
        """
### 이 프로그램은 무엇을 하나요?

신입사원 한 명이 입사하면 인사, IT, 총무, 부서장이 각각 준비해야 할 일이 생깁니다.  
이 프로그램은 **입사자를 등록했을 때 필요한 일을 자동으로 만들고 담당자와 기한을 관리**하는 데 목적이 있습니다.

### 데모 순서

1. **입사자 관리**에서 신규 입사자를 등록합니다.
2. 등록 즉시 적용된 **온보딩 업무와 기한**을 확인합니다.
3. 업무를 완료 처리하면서 **진행률**이 변하는지 확인합니다.
4. **온보딩 템플릿**에서 회사에 필요한 업무를 추가합니다.
5. **알림 및 지연업무**에서 기한이 지난 업무를 확인합니다.
6. **관리자 설정**에서 프로그램 이름과 담당자 규칙을 변경합니다.

### 공개 데모의 데이터에 대해

Streamlit Community Cloud의 로컬 저장소는 영구 보존이 보장되지 않습니다.  
따라서 이 공개 버전은 **포트폴리오 시연용 데모**이며, 실제 사내 운영 시에는 PostgreSQL/Supabase 같은 외부 데이터베이스 연결이 필요합니다.
        """
    )


boot_database()
settings = db.get_settings()

st.sidebar.title("👋 온보딩 자동화")
st.sidebar.caption("Portfolio Demo")
st.sidebar.info("공개 데모입니다. 입력한 데이터는 영구 보존되지 않을 수 있습니다.")

page = st.sidebar.radio(
    "메뉴",
    [
        "📊 온보딩 현황",
        "👥 입사자 관리",
        "📋 온보딩 템플릿",
        "🔔 알림 및 지연업무",
        "📄 문서·양식 관리",
        "⚙️ 관리자 설정",
        "❓ 사용 방법",
    ],
)

st.sidebar.divider()
if st.sidebar.button("🔄 데모 데이터 초기화"):
    reset_demo()

if page == "📊 온보딩 현황":
    page_dashboard()
elif page == "👥 입사자 관리":
    page_employees()
elif page == "📋 온보딩 템플릿":
    page_templates()
elif page == "🔔 알림 및 지연업무":
    page_notifications()
elif page == "📄 문서·양식 관리":
    page_documents()
elif page == "⚙️ 관리자 설정":
    page_settings()
else:
    page_guide()
