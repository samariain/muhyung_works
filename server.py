# -*- coding: utf-8 -*-
"""
무형상품팀 KPI 대시보드 — 로컬 공유 서버 (안정화판)
- 파이썬 표준 라이브러리만 사용 (추가 설치 불필요)
- 데이터를 data.json 에 저장, 저장할 때마다 backups/ 에 자동 백업 (최근 30개)
- data.json 을 못 읽으면 '빈 데이터로 덮어쓰기'를 절대 하지 않음 (데이터 보호)
- 브라우저 연결 끊김(ConnectionReset) 은 조용히 무시
실행: 서버켜기.bat 더블클릭  (또는  python server.py)
"""
import http.server, socketserver, json, os, sys, time, shutil, threading

PORT = 8000
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data.json")
BACKUP_DIR = os.path.join(HERE, "backups")
MAX_BACKUPS = 30
ADMIN_PW = "0000"   # 관리자(입력) 비밀번호 — 바꾸려면 이 값을 수정

_EMPTY = {"daily": {}, "targets": {}, "obfee": {}, "obmeta": {}}
_lock = threading.Lock()   # 동시 저장 보호


def _load_raw(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _derive_month(d):
    """기존 데이터의 '기준 월'을 추정한다(가장 최근 일일실적 날짜의 YYYY-MM, 없으면 2026-10)."""
    daily = d.get("daily") or {}
    months = sorted({k[:7] for k in daily.keys() if isinstance(k, str) and len(k) >= 7})
    return months[-1] if months else "2026-10"


def _migrate(d):
    """단일 저장(구버전) 구조를 '월별 분리' 구조로 자동 이관한다.
    - target  -> targets{월}
    - obfee   -> {월: {...}}   (구버전은 obFeeText 키를 직접 가짐)
    - obmeta  -> {월: {...}}   (구버전은 elapsed/total 키를 직접 가짐)
    과거 데이터를 지우지 않고 '월 키 밑으로 이동'만 하므로 안전하다. 이미 이관됐으면 아무것도 안 한다(멱등)."""
    if not isinstance(d, dict):
        return d
    m = _derive_month(d)
    # target(단일) -> targets{월}
    t = d.get("target")
    if isinstance(t, dict) and "targetText" in t:
        d.setdefault("targets", {})
        d["targets"].setdefault(m, t)
        d.pop("target", None)
    # obfee: 구버전은 obFeeText 를 최상위에 가짐 -> {월: 그 값}
    of = d.get("obfee")
    if isinstance(of, dict) and "obFeeText" in of:
        d["obfee"] = {m: of}
    # obmeta: 구버전은 elapsed/total 을 최상위에 가짐 -> {월: 그 값}
    om = d.get("obmeta")
    if isinstance(om, dict) and ("elapsed" in om or "total" in om):
        d["obmeta"] = {m: om}
    # 월별 맵 키 보장
    d.setdefault("targets", {})
    d.setdefault("obfee", {})
    d.setdefault("obmeta", {})
    return d


def load_data():
    """data.json 을 읽는다. 깨져서 못 읽으면 최신 백업에서 복구를 시도한다.
    그래도 안 되면 '데이터 없음(신규)'인지 '읽기 실패'인지 구분해 반환한다.
    반환: (data_dict, ok)  ok=False 면 '기존 데이터를 신뢰할 수 없음' → 저장 측에서 덮어쓰기 금지."""
    if os.path.exists(DATA_FILE):
        try:
            return _migrate(_load_raw(DATA_FILE)), True
        except Exception as e:
            # data.json 이 깨짐 → 백업에서 복구 시도
            sys.stdout.write("  [경고] data.json 읽기 실패: %s — 백업에서 복구 시도\n" % e)
            bak = _latest_backup()
            if bak:
                try:
                    d = _load_raw(bak)
                    # 깨진 원본은 따로 보관
                    try:
                        os.replace(DATA_FILE, DATA_FILE + ".corrupt")
                    except Exception:
                        pass
                    # 복구본을 data.json 으로 되살림
                    save_data(d, backup=False)
                    sys.stdout.write("  [복구] 백업에서 data.json 복원 완료: %s\n" % os.path.basename(bak))
                    return _migrate(d), True
                except Exception as e2:
                    sys.stdout.write("  [경고] 백업 복구도 실패: %s\n" % e2)
            # 복구 불가 → 읽기 실패로 표시 (덮어쓰기 금지됨)
            return dict(_EMPTY), False
    # 파일이 없음 = 진짜 신규 (덮어써도 됨)
    return dict(_EMPTY), True


def _latest_backup():
    if not os.path.isdir(BACKUP_DIR):
        return None
    files = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
             if f.startswith("data_") and f.endswith(".json")]
    if not files:
        return None
    files.sort()  # 파일명에 시각이 들어가 있어 사전식 정렬 = 시간순
    return files[-1]


def _make_backup():
    """현재 data.json 을 backups/ 에 복사하고 오래된 건 정리."""
    if not os.path.exists(DATA_FILE):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        dst = os.path.join(BACKUP_DIR, "data_%s.json" % stamp)
        if not os.path.exists(dst):
            shutil.copy2(DATA_FILE, dst)
        # 최근 MAX_BACKUPS 개만 유지
        files = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                        if f.startswith("data_") and f.endswith(".json")])
        for old in files[:-MAX_BACKUPS]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception as e:
        sys.stdout.write("  [경고] 백업 생성 실패(무시하고 진행): %s\n" % e)


def save_data(d, backup=True):
    """원자적 저장 + 저장 직전 자동 백업."""
    with _lock:
        if backup:
            _make_backup()          # 저장 전에 '직전 상태'를 백업
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)  # 원자적 교체 (저장 중 손상 방지)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def _json(self, code, obj):
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass  # 브라우저가 연결 끊음 — 무시

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/api/data":
            data, ok = load_data()
            return self._json(200, data)
        if path == "/" or path == "":
            self.path = "/dashboard.html"
        try:
            return super().do_GET()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path != "/api/save":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            req = json.loads(raw.decode("utf-8"))
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        except Exception as e:
            return self._json(400, {"ok": False, "error": "bad request: %s" % e})

        data, ok = load_data()
        # 기존 데이터를 신뢰할 수 없으면(읽기 실패) 저장을 거부해 '빈 덮어쓰기'를 막는다.
        if not ok:
            return self._json(503, {"ok": False, "error": "기존 데이터를 읽지 못해 저장을 중단했습니다(데이터 보호). 서버 창의 경고를 확인하세요."})

        kind = req.get("kind")

        # 방송캘린더/팀스케줄/공지/소담/워크플로우는 담당자 누구나 입력 가능 — 관리자 비번 불필요
        OPEN_KINDS = ("broadcasts", "schedules", "notices", "sodam", "workflow")
        if kind not in OPEN_KINDS:
            if req.get("admin_pw") != ADMIN_PW:
                return self._json(403, {"ok": False, "error": "관리자 비밀번호가 올바르지 않습니다."})

        # 월 키 도우미: 명시된 month 우선, 없으면 asof(YYYY-MM-DD)의 앞 7자리, 그래도 없으면 거부
        def _month_of(req):
            mth = (req.get("month") or "").strip()
            if mth:
                return mth[:7]
            asof = (req.get("asof") or "").strip()
            if len(asof) >= 7:
                return asof[:7]
            return None

        if kind == "daily":
            asof = (req.get("asof") or "").strip()
            if not asof:
                return self._json(400, {"ok": False, "error": "기준일이 없습니다."})
            data.setdefault("daily", {})
            data["daily"][asof] = {"dailyText": req.get("dailyText", ""), "asof": asof}
            save_data(data)
            return self._json(200, {"ok": True, "saved": asof, "count": len(data["daily"])})
        elif kind == "target":
            mth = _month_of(req)
            if not mth:
                return self._json(400, {"ok": False, "error": "월(month)이 없습니다."})
            data.setdefault("targets", {})
            data["targets"][mth] = {"targetText": req.get("targetText", "")}
            save_data(data)
            return self._json(200, {"ok": True, "saved": "target", "month": mth})
        elif kind == "obfee":
            mth = _month_of(req)
            if not mth:
                return self._json(400, {"ok": False, "error": "월(month)이 없습니다."})
            data.setdefault("obfee", {})
            data["obfee"][mth] = {"obFeeText": req.get("obFeeText", ""), "obStart": req.get("obStart", "")}
            save_data(data)
            return self._json(200, {"ok": True, "saved": "obfee", "month": mth})
        elif kind == "obmeta":
            mth = _month_of(req)
            if not mth:
                return self._json(400, {"ok": False, "error": "월(month)이 없습니다."})
            data.setdefault("obmeta", {})
            data["obmeta"][mth] = {"elapsed": req.get("elapsed"), "total": req.get("total")}
            save_data(data)
            return self._json(200, {"ok": True, "saved": "obmeta", "month": mth})
        elif kind == "broadcasts":
            data["broadcasts"] = req.get("broadcasts", [])
            save_data(data)
            return self._json(200, {"ok": True, "saved": "broadcasts", "count": len(data["broadcasts"])})
        elif kind == "schedules":
            data["schedules"] = req.get("schedules", [])
            save_data(data)
            return self._json(200, {"ok": True, "saved": "schedules", "count": len(data["schedules"])})
        elif kind == "notices":
            data["notices"] = req.get("notices", [])
            save_data(data)
            return self._json(200, {"ok": True, "saved": "notices", "count": len(data["notices"])})
        elif kind == "sodam":
            data["sodam"] = req.get("sodam", [])
            save_data(data)
            return self._json(200, {"ok": True, "saved": "sodam", "count": len(data["sodam"])})
        elif kind == "workflow":
            data["workflow"] = req.get("workflow", [])
            save_data(data)
            return self._json(200, {"ok": True, "saved": "workflow", "count": len(data["workflow"])})
        elif kind == "delete":
            asof = (req.get("asof") or "").strip()
            if asof in data.get("daily", {}):
                del data["daily"][asof]
                save_data(data)
                return self._json(200, {"ok": True, "deleted": asof})
            return self._json(200, {"ok": True, "deleted": None})
        else:
            return self._json(400, {"ok": False, "error": "unknown kind"})

    def handle_one_request(self):
        # 연결 끊김으로 인한 긴 트레이스백 억제
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, fmt, *args):
        try:
            sys.stdout.write("  %s\n" % (fmt % args))
        except Exception:
            pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.chdir(HERE)
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError as e:
        print("서버를 시작할 수 없습니다:", e)
        print("포트 %d 가 이미 사용 중일 수 있습니다. 기존 서버 창을 닫고 다시 실행하세요." % PORT)
        input("엔터를 누르면 닫힙니다...")
        return
    print("=" * 56)
    print(" 무형상품팀 KPI 대시보드 서버 실행 중 (안정화판)")
    print("=" * 56)
    print(" 이 PC에서 보기:   http://localhost:%d" % PORT)
    print(" 같은 네트워크 팀원:  http://<이 PC의 IP>:%d" % PORT)
    print("   (IP 확인: 새 명령창에서  ipconfig  → IPv4 주소)")
    print("-" * 56)
    print(" 데이터 저장 위치: %s" % DATA_FILE)
    print(" 자동 백업 폴더:   %s (최근 %d개 유지)" % (BACKUP_DIR, MAX_BACKUPS))
    print(" 종료하려면 이 창에서 Ctrl+C  (또는 창 닫기)")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
