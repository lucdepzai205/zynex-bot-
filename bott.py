import telebot, threading, time, requests, string, json, os, pytz, random
from telebot import types as tg_types
import hashlib
from datetime import datetime, timedelta

def get_datetime_hcm():
    tz = pytz.timezone("Asia/Ho_Chi_Minh")
    now = datetime.now(tz)
    return now

# ================== CẤU HÌNH ==================
TOKEN = os.getenv("ZYNEX_BOT_TOKEN", "").strip()
OWNER_ID = 8222877373
if not TOKEN:
    raise RuntimeError("Thiếu ZYNEX_BOT_TOKEN. Hãy đặt token bot vào biến môi trường trước khi chạy.")
bot = telebot.TeleBot(TOKEN)

# ================== TELEGRAM NETWORK SAFETY ==================
# Không để timeout mạng khi gửi tin nhắn làm chết handler/polling.
# infinity_polling vẫn tự reconnect khi getUpdates bị timeout.
_ORIG_SEND_MESSAGE = bot.send_message
_ORIG_SEND_PHOTO = bot.send_photo
_ORIG_EDIT_MESSAGE_TEXT = bot.edit_message_text
_ORIG_DELETE_MESSAGE = bot.delete_message
_ORIG_ANSWER_CALLBACK_QUERY = bot.answer_callback_query


def _tg_call_safe(fn, *args, **kwargs):
    last_error = None
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"[Telegram network] {type(e).__name__} - lần {attempt + 1}/3")
            if attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            # Không nuốt lỗi logic ngoài lỗi mạng.
            raise
    print(f"[Telegram network] Bỏ qua thao tác sau 3 lần thử: {last_error}")
    return None


def _safe_send_message(*args, **kwargs):
    return _tg_call_safe(_ORIG_SEND_MESSAGE, *args, **kwargs)


def _safe_send_photo(*args, **kwargs):
    return _tg_call_safe(_ORIG_SEND_PHOTO, *args, **kwargs)


def _safe_edit_message_text(*args, **kwargs):
    return _tg_call_safe(_ORIG_EDIT_MESSAGE_TEXT, *args, **kwargs)


def _safe_delete_message(*args, **kwargs):
    return _tg_call_safe(_ORIG_DELETE_MESSAGE, *args, **kwargs)


def _safe_answer_callback_query(*args, **kwargs):
    return _tg_call_safe(_ORIG_ANSWER_CALLBACK_QUERY, *args, **kwargs)


bot.send_message = _safe_send_message
bot.send_photo = _safe_send_photo
bot.edit_message_text = _safe_edit_message_text
bot.delete_message = _safe_delete_message
bot.answer_callback_query = _safe_answer_callback_query


# ================== KHỞI TẠO LOCK CHO ĐA LUỒNG ==================
data_lock = threading.Lock()

# ================== HÀM LƯU / ĐỌC FILE ==================
def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"Lỗi lưu file {filename}: {e}")

def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Lỗi đọc file {filename}: {e}")
            return {}
    return {}

def save_keys_file():
    with data_lock:
        tosave = {}
        for k, v in active_keys.items():
            if isinstance(v, datetime):
                tosave[k] = v.strftime("%Y-%m-%d %H:%M:%S")
            else:
                tosave[k] = str(v)
        save_json("keys.json", tosave)

def save_auth_users_file():
    with data_lock:
        tosave = {}
        for uid, v in authenticated_users.items():
            if isinstance(v, datetime):
                tosave[str(uid)] = v.strftime("%Y-%m-%d %H:%M:%S")
            else:
                tosave[str(uid)] = str(v)
        save_json("auth_users.json", tosave)

def save_kicked_file():
    with data_lock:
        save_json("kicked.json", list(kicked_users))

# ================== DỮ LIỆU BAN ĐẦU ==================
user_data = {}
_active_keys_raw = load_json("keys.json")
_authenticated_raw = load_json("auth_users.json")
_kicked_raw = load_json("kicked.json")

# Convert loaded data
active_keys = {}
for k, v in (_active_keys_raw or {}).items():
    if isinstance(v, str):
        try:
            dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                dt = datetime.fromisoformat(v)
            except:
                dt = None
        if dt and dt > datetime.now():
            active_keys[k] = dt
    else:
        pass

EXPIRY_WARNING_FILE = "expiry_warning_sent.json"


def load_expiry_warning_sent():
    raw = load_json(EXPIRY_WARNING_FILE)
    return raw if isinstance(raw, dict) else {}


def save_expiry_warning_sent(data):
    save_json(EXPIRY_WARNING_FILE, data)


expiry_warning_sent = load_expiry_warning_sent()

authenticated_users = {}
if isinstance(_authenticated_raw, dict):
    for uid_str, v in _authenticated_raw.items():
        try:
            dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                dt = datetime.fromisoformat(v)
            except:
                dt = None
        if dt and dt > datetime.now():
            try:
                authenticated_users[int(uid_str)] = dt
            except:
                pass
elif isinstance(_authenticated_raw, list):
    for uid in _authenticated_raw:
        try:
            authenticated_users[int(uid)] = datetime.now() + timedelta(days=365*10)
        except:
            pass

authenticated_users[OWNER_ID] = datetime.now() + timedelta(days=365*100)

kicked_users = set(_kicked_raw) if isinstance(_kicked_raw, list) else set()
running_users = set()

# Save cleaned initial files
save_keys_file()
save_auth_users_file()
save_kicked_file()

# ================== HÀM API SUNWIN ==================
SUNWIN_HISTORY_API = "https://kwinstore.com/sunwin/tx/history/ccc1ae5c14629528200e69be66a4c7b2b710af9b5dd772a5"


def _parse_sunwin_history_payload(js):
    """Đọc response history của KwinStore và lấy phiên mới nhất."""
    if not isinstance(js, dict) or js.get("status") != "OK":
        return None, None, None

    rows = js.get("data")
    if not isinstance(rows, list) or not rows:
        return None, None, None

    # API đang trả bản ghi mới nhất ở đầu danh sách.
    row = rows[0]
    if not isinstance(row, dict):
        return None, None, None

    phien = row.get("phiên") or row.get("phien")
    xx1 = row.get("d1")
    xx2 = row.get("d2")
    xx3 = row.get("d3")
    tong = row.get("tổng")
    kq = row.get("kết quả")

    try:
        phien = int(phien)
        xx1, xx2, xx3 = int(xx1), int(xx2), int(xx3)
    except (TypeError, ValueError):
        return None, None, None

    if tong is None:
        tong = xx1 + xx2 + xx3
    try:
        tong = int(tong)
    except (TypeError, ValueError):
        tong = xx1 + xx2 + xx3

    # Không phụ thuộc hoàn toàn vào chuỗi kết quả của API.
    if kq not in ("Tài", "Xỉu"):
        if 3 <= tong <= 10:
            kq = "Xỉu"
        elif 11 <= tong <= 18:
            kq = "Tài"
        else:
            return None, None, None

    return phien, kq, f"{xx1}-{xx2}-{xx3}"


def get_api():
    """Lấy phiên mới nhất từ API history KwinStore."""
    try:
        r = requests.get(
            SUNWIN_HISTORY_API,
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        return _parse_sunwin_history_payload(r.json())
    except requests.exceptions.RequestException as e:
        print(f"Lỗi kết nối API Sunwin: {e}")
        return None, None, None
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Lỗi JSON API Sunwin: {e}")
        return None, None, None
    except Exception as e:
        print(f"Lỗi không xác định trong get_api(): {e}")
        return None, None, None

def get_sunwin_history(limit=50):
    """Lấy nhiều phiên lịch sử để dựng bảng AI khi chọn SUN THƯỜNG."""
    try:
        r = requests.get(
            SUNWIN_HISTORY_API,
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        js = r.json()
        if not isinstance(js, dict) or js.get("status") != "OK":
            return []
        rows = js.get("data")
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            try:
                phien = int(row.get("phiên") or row.get("phien"))
                d1, d2, d3 = int(row.get("d1")), int(row.get("d2")), int(row.get("d3"))
                tong = int(row.get("tổng") if row.get("tổng") is not None else d1+d2+d3)
            except (TypeError, ValueError):
                continue
            kq = row.get("kết quả")
            if kq not in ("Tài", "Xỉu"):
                kq = "Tài" if tong >= 11 else "Xỉu"
            out.append({"phien": phien, "kq": kq, "xx": f"{d1}-{d2}-{d3}", "tong": tong})
        return out
    except Exception as e:
        print(f"Lỗi lấy lịch sử Sunwin: {e}")
        return []

def build_sunwin_ai_panel(chat_id):
    """Gửi bảng AI cho phiên mới nhất, đồng thời chấm dự đoán phiên trước."""
    rows = get_sunwin_history(50)
    if not rows:
        bot.send_message(chat_id, "☀️ SUN THƯỜNG\n\n⚠️ Chưa lấy được dữ liệu lịch sử từ API.")
        return None

    # API KwinStore trả mới -> cũ; đổi thành cũ -> mới để xử lý.
    rows = list(reversed(rows))
    data_kq = [x["kq"] for x in rows]
    latest = rows[-1]
    latest_xx = latest["xx"]

    data = user_data.setdefault(chat_id, {
        "last_phien": 0, "lich_su_kq": [], "lich_su_phan_hoi": [],
        "dem_sai": 0, "pattern_sai": set(), "so_dung": 0, "so_sai": 0,
        "lich_su_diem": [], "du_doan_truoc": None, "do_tin_cay_truoc": None,
        "phien_truoc": 0, "da_be_tai": False, "da_be_xiu": False,
        "pattern_memory": {}, "error_memory": {}, "last_scored_phien": 0
    })

    # Đồng bộ/chấm phiên vừa ra: nếu phiên mới đúng bằng phiên dự đoán trước + 1,
    # chỉ chấm đúng 1 lần cho phiên đó.
    prev_pred = data.get("du_doan_truoc")
    prev_phien = data.get("phien_truoc", 0)
    if (prev_pred in ("Tài", "Xỉu") and latest["phien"] == prev_phien + 1
            and data.get("last_scored_phien", 0) != latest["phien"]):
        thang = (prev_pred == latest["kq"])
        if thang:
            data["so_dung"] = data.get("so_dung", 0) + 1
            data["dem_sai"] = 0
        else:
            data["so_sai"] = data.get("so_sai", 0) + 1
            data["dem_sai"] = data.get("dem_sai", 0) + 1
        data.setdefault("lich_su_phan_hoi", []).append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "du_doan": prev_pred,
            "kq": latest["kq"],
            "thang": thang,
            "phien": latest["phien"]
        })
        data["last_scored_phien"] = latest["phien"]

    data["lich_su_kq"] = data_kq[-100:]
    data["last_phien"] = latest["phien"]

    du_doan_tx, confidence, reason = du_doan(
        data["lich_su_kq"], data.get("dem_sai", 0),
        data.get("pattern_sai", set()), latest_xx,
        data.setdefault("lich_su_diem", []), data
    )

    tail = data["lich_su_kq"][-10:]
    tai = tail.count("Tài")
    xiu = tail.count("Xỉu")
    trend = " → ".join("T" if x == "Tài" else "X" for x in tail)
    phien_next = latest["phien"] + 1

    dung = data.get("so_dung", 0)
    sai = data.get("so_sai", 0)
    tong_cham = dung + sai
    ty_le = round(dung * 100 / tong_cham) if tong_cham else 0

    text = (
        "🤖 AI PHÂN TÍCH SUN THƯỜNG\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎲 Phiên hiện tại: #{latest['phien']}\n"
        f"🎯 Xúc xắc: {latest_xx}  | Tổng: {latest['tong']}\n"
        f"📌 Kết quả: {latest['kq']}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 10 phiên gần nhất: Tài {tai} | Xỉu {xiu}\n"
        f"📈 Cầu: {trend}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔮 Phiên tiếp: #{phien_next}\n"
        f"🤖 Dự đoán: {du_doan_tx}\n"
        f"🎯 Độ tin cậy : {confidence}%\n"
        f"🧠 Phân tích: {reason}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 THỐNG KÊ AI: ✅ Đúng {dung}  |  ❌ Sai {sai}\n"
        f"🏆 Tỷ lệ đúng: {ty_le}% ({dung}/{tong_cham})\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ ADMIN : @luc_z2005"
    )
    bot.send_message(chat_id, text)

    # Lưu dự đoán của phiên hiện tại để phiên kế tiếp được chấm.
    data["du_doan_truoc"] = du_doan_tx
    data["do_tin_cay_truoc"] = confidence
    data["phien_truoc"] = latest["phien"]
    data["sunwin_last_panel_phien"] = latest["phien"]
    return latest["phien"]


def do_ben(data):
    if not data:
        return 0
    last = data[-1]
    count = 0
    for i in reversed(data):
        if i == last:
            count += 1
        else:
            break
    return count if count >= 3 else 0
def du_doan(data_kq, dem_sai, pattern_sai, xx, diem_lich_su, data):
    try:
        xx_list = xx.split("-")
        tong = sum(int(x) for x in xx_list)
    except:
        xx_list = ["0","0","0"]
        tong = 0

    data_kq = data_kq[-50:]  # ❗ giảm xuống để tránh nhiễu
    cuoi = data_kq[-1] if data_kq else None
    pattern = "".join("T" if x == "Tài" else "X" for x in data_kq)

    # ================== CHỐNG THUA THÔNG ==================
    # reset nếu thua quá sâu
    if dem_sai >= 5:
        return ("Tài" if tong % 2 else "Xỉu"), 75, "Reset khi thua sâu → đánh theo tổng"

    # đảo mạnh nếu thua liên tiếp
    if dem_sai >= 3:
        du_doan_tx = "Xỉu" if cuoi == "Tài" else "Tài"
        return du_doan_tx, 85, f"Đang thua {dem_sai} → đảo chiều mạnh"

    # ================== AI HỌC NHƯNG GIỚI HẠN ==================
    pattern_memory = data.get("pattern_memory", {})
    matched_pred = None
    matched_confidence = 0

    for pat, stats in pattern_memory.items():
        if pattern.endswith(pat):
            count = stats.get("count", 0)
            correct = stats.get("correct", 0)
            confidence = correct / count if count > 0 else 0

            # ❗ siết điều kiện tránh ảo
            if count >= 5 and confidence >= 0.7:
                if confidence > matched_confidence:
                    matched_confidence = confidence
                    matched_pred = stats.get("next_pred", None)

    if matched_pred:
        return matched_pred, 88, f"Học cầu ({matched_confidence:.2f})"

    # ================== CHỐNG DÍNH CẦU GIẢ ==================
    if len(data_kq) >= 4:
        last4 = data_kq[-4:]
        if last4.count("Tài") == 2 and last4.count("Xỉu") == 2:
            return ("Xỉu" if cuoi == "Tài" else "Tài"), 86, "Cầu nhiễu 2-2 → đảo"

    # ================== XỬ LÝ BỆT (GIẢM ÔM) ==================
    def do_ben(data_kq):
        if not data_kq:
            return 0
        last = data_kq[-1]
        count = 0
        for kq in reversed(data_kq):
            if kq == last:
                count += 1
            else:
                break
        return count

    ben = do_ben(data_kq)

    if ben >= 4:
        # ❗ không ôm mù nữa
        return ("Xỉu" if cuoi == "Tài" else "Tài"), 87, f"Bệt {ben} → bẻ sớm"

    if ben >= 2:
        return cuoi, 80, f"Bệt nhẹ {ben} → theo"

    # ================== XỈ NGẦU (GIỮ NHƯNG GIẢM ĐỘ TIN) ==================
    if len(set(xx_list)) == 1:
        so = xx_list[0]
        if so in ["1", "2", "4"]:
            return "Xỉu", 90, f"3 xí ngầu {so}"
        if so in ["3", "5"]:
            return "Tài", 90, f"3 xí ngầu {so}"

    # ================== LỆCH CẦU ==================
    counts = {"Tài": data_kq.count("Tài"), "Xỉu": data_kq.count("Xỉu")}
    chenh = abs(counts["Tài"] - counts["Xỉu"])

    if chenh >= 4:
        return ("Tài" if counts["Tài"] < counts["Xỉu"] else "Xỉu"), 82, "Cầu lệch → hồi"

    # ================== DEFAULT AN TOÀN ==================
    return ("Tài" if tong >= 11 else "Xỉu"), 70, "An toàn theo tổng"

# ================== XỬ LÝ PHIÊN VÀ GỬI THÔNG BÁO ==================
def xu_ly_phien(phien, kq, xx, chat_id):
    with data_lock:
        if chat_id not in user_data:
            user_data[chat_id] = {
                "last_phien": 0,
                "lich_su_kq": [],
                "lich_su_phan_hoi": [],
                "dem_sai": 0,
                "pattern_sai": set(),
                "so_dung": 0,
                "so_sai": 0,
                "lich_su_diem": [],
                "du_doan_truoc": None,
                "do_tin_cay_truoc": None,
                "phien_truoc": 0,
                "da_be_tai": False,
                "da_be_xiu": False,
                "pattern_memory": {},
                "error_memory": {}, "last_scored_phien": 0
            }

        data = user_data[chat_id]

        if not (phien and kq and xx):
            return

        if not (phien and phien > data.get("last_phien", 0)):
            return

        thong_bao = ""
        if data.get("du_doan_truoc") is not None and phien == data.get("phien_truoc", 0) + 1:
            thang = (data["du_doan_truoc"] == kq)
            thong_bao = "✓" if thang else "X"

            data.setdefault("lich_su_phan_hoi", []).append({
                "time": datetime.now().strftime("%H:%M"),
                "du_doan": data["du_doan_truoc"],
                "kq": kq,
                "thang": thang,
                "phien": phien
            })

            # Cập nhật pattern memory cho AI học
            if len(data["lich_su_kq"]) >= 3:
                pattern_key = "".join("T" if x == "Tài" else "X" for x in data["lich_su_kq"][-4:-1])
                if pattern_key not in data["pattern_memory"]:
                    data["pattern_memory"][pattern_key] = {"count": 0, "correct": 0, "next_pred": data["du_doan_truoc"]}
                
                data["pattern_memory"][pattern_key]["count"] += 1
                if thang:
                    data["pattern_memory"][pattern_key]["correct"] += 1

            # Cập nhật error memory
            if not thang and len(data["lich_su_kq"]) >= 3:
                error_key = tuple(data["lich_su_kq"][-3:])
                data["error_memory"][error_key] = data["error_memory"].get(error_key, 0) + 1

            if thang:
                data["dem_sai"] = 0
            else:
                data["dem_sai"] = data.get("dem_sai", 0) + 1
                if len(data.get("lich_su_kq", [])) >= 3:
                    pattern = tuple(data.get("lich_su_kq", [])[-3:])
                    data.setdefault("pattern_sai", set()).add(pattern)

            data["so_dung"] = data.get("so_dung", 0) + (1 if thang else 0)
            data["so_sai"] = data.get("so_sai", 0) + (0 if thang else 1)

        data["last_phien"] = phien
        data.setdefault("lich_su_kq", []).append(kq)
        if len(data["lich_su_kq"]) > 100:
            data["lich_su_kq"] = data["lich_su_kq"][-100:]

        du_doan_tx, do_tin_cay, loai_cau = du_doan(
            data["lich_su_kq"],
            data.get("dem_sai", 0),
            data.get("pattern_sai", set()),
            xx,
            data.setdefault("lich_su_diem", []),
            data
        )

        data["du_doan_truoc"] = du_doan_tx
        data["do_tin_cay_truoc"] = do_tin_cay
        data["phien_truoc"] = phien

        phien_hien_tai = phien + 1

        try:
            tong_xuc_xac = sum(map(int, xx.split("-")))
        except:
            tong_xuc_xac = None

        try:
            bot.send_message(chat_id, f"""Sun TX
Phiên: {phien} ({xx})
Kết quả: {kq} {tong_xuc_xac if tong_xuc_xac is not None else 'N/A'} {thong_bao or ''}
Phiên: {phien_hien_tai}
Dự đoán: {du_doan_tx} {do_tin_cay}%""")
        except Exception as e:
            print(f"Lỗi gửi message user {chat_id}: {e}")

# ================== VÒNG LẶP TỰ ĐỘNG CHO MỖI USER ==================
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

def auto_loop(uid, chat_id=None):
    """Theo dõi SUN THƯỜNG. Nếu có chat_id thì chạy riêng cho chat/group đó."""
    target_chat_id = chat_id if chat_id is not None else uid
    is_group_loop = chat_id is not None
    last_error_time = 0
    error_count = 0
    last_sent_phien = user_data.get(target_chat_id, {}).get("sunwin_last_panel_phien")

    while (target_chat_id in sun_running_chats if is_group_loop else uid in running_users):
        try:
            # Chat riêng: kiểm tra key.
            # Group/supergroup: bot đã được admin bật nên không kiểm tra key người bật.
            if not is_group_loop and uid != OWNER_ID and not check_key(uid):
                try:
                    bot.send_message(
                        uid,
                        "🔑 Key của bạn đã hết hạn hoặc không hợp lệ. Vui lòng nhập key mới."
                    )
                except Exception:
                    pass
                running_users.discard(uid)
                break

            rows = get_sunwin_history(50)
            if rows:
                latest_phien = rows[0]["phien"]

                # Chỉ gửi một lần cho mỗi phiên mới.
                if last_sent_phien is None or latest_phien > last_sent_phien:
                    sent_phien = build_sunwin_ai_panel(target_chat_id)
                    if sent_phien is not None:
                        last_sent_phien = sent_phien
                        user_data.setdefault(target_chat_id, {})[
                            "sunwin_last_panel_phien"
                        ] = sent_phien
                        error_count = 0
            else:
                error_count += 1

        except Exception as e:
            error_count += 1
            now = time.time()
            if now - last_error_time > 60:
                print(f"Lỗi auto Sun thường user {uid}, chat {target_chat_id}: {e}")
                last_error_time = now

        if error_count > 10:
            print(f"Dừng auto Sun thường user {uid}, chat {target_chat_id} do quá nhiều lỗi")
            if is_group_loop:
                sun_running_chats.discard(target_chat_id)
            else:
                running_users.discard(uid)
            break

        time.sleep(1)


def is_group_admin(chat_id, user_id):
    """
    Trong group/supergroup chỉ đúng OWNER_ID được phép gọi bot.
    Thành viên khác không được phản hồi.
    Chat riêng không áp dụng giới hạn này.
    """
    if chat_id >= 0:
        return True
    return user_id == OWNER_ID


def _group_admin_only(msg):
    """Giữ cơ chế cũ: trong group chỉ OWNER được dùng các chức năng bot."""
    if msg.chat.type in ("group", "supergroup") and msg.from_user.id != OWNER_ID:
        return False
    return True

# ================== THÔNG BÁO KEY VÀO GROUP ==================
NOTIFY_GROUP_FILE = "notify_group.json"

def _load_notify_group():
    try:
        with open(NOTIFY_GROUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("chat_id")) if data.get("chat_id") is not None else None
    except Exception:
        return None

def _save_notify_group(chat_id):
    with open(NOTIFY_GROUP_FILE, "w", encoding="utf-8") as f:
        json.dump({"chat_id": int(chat_id)}, f, ensure_ascii=False, indent=2)

def _is_telegram_group_admin(msg):
    """Chỉ ADMIN/CREATOR của chính group hiện tại mới được cấu hình notify group."""
    if msg.chat.type not in ("group", "supergroup"):
        return False
    try:
        member = bot.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        print(f"[NotifyGroup] Không kiểm tra được quyền admin: {e}")
        return False

@bot.message_handler(commands=['setnotifygroup'])
def handle_set_notify_group(msg):
    if not _is_telegram_group_admin(msg):
        return
    try:
        _save_notify_group(msg.chat.id)
        bot.reply_to(msg, "✅ Đã đặt nhóm này làm nhóm nhận thông báo Key.\n🆔 Chat ID: <code>%s</code>" % msg.chat.id, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(msg, f"❌ Không thể lưu nhóm thông báo: {e}")

@bot.message_handler(commands=['notifygroup'])
def handle_notify_group(msg):
    if not _is_telegram_group_admin(msg):
        return
    chat_id = _load_notify_group()
    if chat_id is None:
        bot.reply_to(msg, "ℹ️ Chưa cấu hình nhóm nhận thông báo Key.")
    else:
        bot.reply_to(msg, f"📢 Nhóm nhận thông báo hiện tại: <code>{chat_id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['unsetnotifygroup'])
def handle_unset_notify_group(msg):
    if not _is_telegram_group_admin(msg):
        return
    try:
        if os.path.exists(NOTIFY_GROUP_FILE):
            os.remove(NOTIFY_GROUP_FILE)
        bot.reply_to(msg, "✅ Đã tắt thông báo Key cho nhóm này.")
    except Exception as e:
        bot.reply_to(msg, f"❌ Không thể tắt thông báo: {e}")

def _send_key_notify_group(text):
    """Gửi thông báo Key tới group đã cấu hình; lỗi group không làm hỏng /done."""
    chat_id = _load_notify_group()
    if chat_id is None:
        return
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        print(f"[NotifyGroup] Không gửi được thông báo: {e}")

# ================== HANDLER MESSAGE ==================
# ================== HANDLER MESSAGE ==================
@bot.message_handler(commands=['start'])
def handle_start(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    bot.send_message(msg.chat.id, "💜 <b>CHÀO MỪNG ĐẾN ZYNEX AI</b> 💜\n━━━━━━━━━━━━━━━━━━\n🤖 Hệ thống AI phân tích game\n🔐 Vui lòng chọn chức năng trong menu bên dưới.\n━━━━━━━━━━━━━━━━━━\n✨ Chúc bạn sử dụng bot vui vẻ!", parse_mode="HTML", reply_markup=main_keyboard())

@bot.message_handler(commands=['stop'])
def handle_stop(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    running_users.discard(uid)
    bot.reply_to(msg, " Dừng dự đoán.")
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==== CẤU HÌNH FILE + LOCK ====
ORDERS_FILE = "orders.json"
KEYS_FILE = "keys.json"
LOCK = threading.Lock()
OWNER_ID = 8222877373  # Telegram ID admin

# ----- Load / Save orders -----
def load_orders():
    try:
        with LOCK:
            with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        return {}

def save_orders(orders):
    with LOCK:
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)

# ----- Load / Save keys -----
def load_keys():
    try:
        with LOCK:
            with open(KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        return {}

def save_keys(keys):
    with LOCK:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)

# ----- Tạo mã đơn & nội dung chuyển tiền -----
def make_order_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))

# ----- Lưu đơn mới -----
def save_new_order(user_id, key_type, order_code):
    orders = load_orders()
    orders[order_code] = {
        "user_id": user_id,
        "key_type": key_type,
        "status": "pending",
        "created_at": datetime.now().isoformat()
    }
    save_orders(orders)

# ----- Tạo key hoàn toàn mới -----
def generate_unique_key():
    keys = load_keys()
    while True:
        key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        if key not in keys:
            return key
# ===================== menu =====================
@bot.message_handler(commands=['menu'])
def handle_menu(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    running_users.discard(uid)
    bot.send_message(msg.chat.id,
        "🏠 MENU CHÍNH",
        reply_markup=main_keyboard()
    )

# ===================== /muakey =====================
@bot.message_handler(commands=['muakey'])
def handle_muakey(msg):
    if not _group_admin_only(msg):
        return
    text = (
        "BANG GIA KEY TOOL\n"
        "3 NGÀY: 50k\n"
        "1 TUẦN: 80k\n"
        "1 THÀNG: 150k\n"
        "VĨNH VIỄN: 200k\n"
        "Nhap lenh:\n"
        "/buy + thoi gian\n"
        "Vi du: /buy3day 1week 1month vip"
    )
    
    bot.reply_to(msg, text)
# ===================== /buy =====================
@bot.message_handler(func=lambda msg: msg.text.startswith('/buy'))
def handle_buy(msg):
    if not _group_admin_only(msg):
        return
    text = msg.text.lower()

    prices = {
        "3day": "50.000VND",
        "1week": "80.000VND",
        "1month": "150.000VND",
        "vip": "200.000VND"
    }

    if "/buy3day" in text:
        key_type = "3ngay"
        display_name = "3 NGÀY"
        price = prices["3day"]
    elif "/buy1week" in text:
        key_type = "1tuan"
        display_name = "1 TUẦN"
        price = prices["1week"]
    elif "/buy1month" in text:
        key_type = "1thang"
        display_name = "1 THÁNG"
        price = prices["1month"]
    elif "/buyvip" in text:
        key_type = "vinhvien"
        display_name = "VĨNH VIỄN"
        price = prices["vip"]
    else:
        bot.reply_to(msg, "Lenh khong hop le. Vi du: /buy3day")
        return

    stk = "1038854327"
    bank = "VIETCOM BANK"
    receiver = "TRAN DINH LUC"

    order_code = make_order_code()
    user_id = msg.from_user.id

    # lưu chuẩn key_type (không dấu)
    save_new_order(user_id, key_type, order_code)

    text_reply = (
        "THÔNG TIN THANH TOÁN\n"
        f"GÓI: {display_name}\n"
        f"GIÁ: {price}\n"
        f"STK: <code>{stk}</code>\n"
        f"NGÂN HÀNG: {bank}\n"
        f"CHỦ TK: {receiver}\n"
        f"NỘI DUNG: <code>{order_code}</code>\n"
        "TT XONG GỬI BILL CHO ADMIN: @luc_z2005"
    )
    bot.reply_to(msg, text_reply, parse_mode="HTML")
# ----- Tạo key hoàn toàn mới -----
def generate_unique_key():
    keys = load_keys()
    attempts = 0
    while True:
        key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        if key not in keys:
            return key
        attempts += 1
        if attempts > 1000:
            key = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            if key not in keys:
                return key



@bot.message_handler(commands=['done'])
def handle_done(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, "BẠN KHÔNG CÓ QUYỀN SỬ DỤNG LỆNH NÀY.")
        return

    try:
        order_code = msg.text.split()[1].strip()
    except IndexError:
        bot.reply_to(msg, "VUI LÒNG NHẬP: /done <MÃ ĐƠN>")
        return

    orders = load_orders()

    if order_code not in orders:
        bot.reply_to(msg, f"ĐƠN {order_code} KHÔNG TỒN TẠI.")
        return

    order_info = orders[order_code]

    if order_info.get("status") == "done":
        bot.reply_to(msg, f"ĐƠN {order_code} ĐÃ ĐƯỢC XỬ LÝ.")
        return

    user_id = order_info["user_id"]
    key_type = order_info["key_type"]
    now = datetime.now()

    # 🔥 CHUẨN HÓA LOẠI GÓI
    loai_hien_thi = {
        "3ngay": "3 NGÀY",
        "1tuan": "1 TUẦN",
        "1thang": "1 THÁNG",
        "vinhvien": "VĨNH VIỄN"
    }.get(key_type, key_type)

    # 🔥 TÍNH HẠN
    if key_type == "3ngay":
        expire_time = now + timedelta(days=3)
    elif key_type == "1tuan":
        expire_time = now + timedelta(days=7)
    elif key_type == "1thang":
        expire_time = now + timedelta(days=30)
    elif key_type == "vinhvien":
        expire_time = now + timedelta(days=100000)
    else:
        expire_time = now + timedelta(days=1)

    # 🔥 TẠO KEY
    key = generate_unique_key()

    keys = load_keys()
    keys[key] = expire_time.isoformat()
    save_keys(keys)

    # 🔥 UPDATE ORDER
    order_info["status"] = "done"
    order_info["delivered_key"] = key
    order_info["done_at"] = now.isoformat()
    order_info["expire_at"] = expire_time.isoformat()
    orders[order_code] = order_info
    save_orders(orders)

    # 🔥 AUTO KÍCH HOẠT
    authenticated_users[user_id] = expire_time
    save_auth_users_file()

    # 📩 GỬI CHO USER — sẽ gửi sau khi lấy display_name để đủ thông tin.

    # 🛠 ADMIN LOG — GỘP THÀNH 1 TIN DUY NHẤT
    try:
        user = bot.get_chat(user_id)
        display_name = user.first_name or user.username or str(user_id)
        username = user.username
    except Exception:
        display_name = str(user_id)
        username = None

    # 📩 GỬI CHO USER — đầy đủ thông tin + Key thật (chỉ chat riêng khách)
    user_activation = (
        "🎉 <b>KÍCH HOẠT KEY THÀNH CÔNG</b> 🎉\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧾 <b>Đơn:</b> <code>{order_code}</code>\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Tên:</b> {display_name}\n"
        f"📦 <b>Gói Key:</b> {loai_hien_thi}\n"
        f"🕐 <b>Kích hoạt:</b> {now.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏳ <b>Hết hạn:</b> {expire_time.strftime('%d/%m/%Y %H:%M')}\n"
        f"🔑 <b>Key:</b> <code>{key}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💜 <b>Cảm ơn bạn đã sử dụng ZYNEX AI!</b>"
    )
    bot.send_message(user_id, user_activation, parse_mode="HTML")

    # Admin thực hiện /done
    admin_username = getattr(msg.from_user, "username", None)
    admin_display = f"@{admin_username}" if admin_username else (getattr(msg.from_user, "first_name", None) or str(msg.from_user.id))

    # Tổng số user đã có quyền trong hệ thống sau khi kích hoạt
    total_users = len(authenticated_users)

    # Gộp toàn bộ thông tin duyệt đơn vào một message.
    admin_log = (
        "🎉 <b>KÍCH HOẠT THÀNH CÔNG</b> 🎉\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧾 <b>Đơn:</b> <code>{order_code}</code>\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Tên:</b> {display_name}\n"
        f"📦 <b>Gói Key:</b> {loai_hien_thi}\n"
        f"🕐 <b>Kích hoạt lúc:</b> {now.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏳ <b>Hết hạn:</b> {expire_time.strftime('%d/%m/%Y %H:%M')}\n"
        "🔐 <b>Key:</b> Đã gửi riêng cho khách\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Tổng user hệ thống:</b> {total_users}\n"
        f"✨ <b>Admin phụ trách:</b> {admin_display}"
    )

    bot.reply_to(msg, admin_log, parse_mode="HTML")

    # 📢 Gửi thêm 1 bản thông báo vào group đã được ADMIN cấu hình.
    group_log = (
        "🎉 <b>KÍCH HOẠT KEY THÀNH CÔNG</b> 🎉\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🧾 <b>Đơn:</b> <code>{order_code}</code>\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Tên:</b> {display_name}\n"
        f"📦 <b>Gói Key:</b> {loai_hien_thi}\n"
        f"🕐 <b>Kích hoạt:</b> {now.strftime('%d/%m/%Y %H:%M')}\n"
        f"⏳ <b>Hết hạn:</b> {expire_time.strftime('%d/%m/%Y %H:%M')}\n"
        "🔐 <b>Key:</b> Đã gửi riêng cho khách\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Tổng user:</b> {total_users}\n"
        f"✨ <b>Admin duyệt:</b> {admin_display}"
    )
    _send_key_notify_group(group_log)

@bot.message_handler(commands=['taokey'])
def handle_taokey(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, "Khong co quyen.")
        return

    try:
        parts = msg.text.strip().split()
        if len(parts) != 2:
            raise ValueError

        duration_str = parts[1].lower()
        unit = duration_str[-1]
        amount = int(duration_str[:-1])

        now = datetime.now()

        if unit == 'm':
            expire_time = now + timedelta(minutes=amount)
        elif unit == 'h':
            expire_time = now + timedelta(hours=amount)
        elif unit == 'd':
            expire_time = now + timedelta(days=amount)
        elif unit == 'm':  # tháng (viết M hoặc m đều ăn)
            expire_time = now + timedelta(days=30 * amount)
        else:
            bot.reply_to(msg, "Don vi khong hop le. Dung m/h/d/M")
            return

        # 🔥 load file
        keys = load_keys()

        # 🔥 tạo key không trùng
        while True:
            key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            if key not in keys:
                break

        # 🔥 lưu ISO giống /done
        keys[key] = expire_time.isoformat()
        save_keys(keys)

        bot.reply_to(
            msg,
            f"Key: <code>{key}</code>\nHet han: {expire_time.strftime('%H:%M %d-%m-%Y')}",
            parse_mode="HTML"
        )

    except:
        bot.reply_to(msg, "Sai cu phap. Dung: /taokey 30m")
import json
from datetime import datetime

ACTIVE_KEYS_FILE = "active_keys.json"
AUTH_USERS_FILE = "authenticated_users.json"

# Hàm lưu active_keys ra file
def save_keys_file():
    with open(ACTIVE_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v 
                   for k, v in active_keys.items()}, f, ensure_ascii=False, indent=2)

# Hàm load active_keys từ file
def load_keys_file():
    global active_keys
    try:
        with open(ACTIVE_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            active_keys = {k: datetime.strptime(v, "%Y-%m-%d %H:%M:%S") for k, v in data.items()}
    except:
        active_keys = {}

# Hàm lưu authenticated_users ra file
def save_auth_users_file():
    with open(AUTH_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v 
                   for k, v in authenticated_users.items()}, f, ensure_ascii=False, indent=2)

# Hàm load authenticated_users từ file
def load_auth_users_file():
    global authenticated_users
    try:
        with open(AUTH_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            authenticated_users = {int(k): datetime.strptime(v, "%Y-%m-%d %H:%M:%S") for k, v in data.items()}
    except:
        authenticated_users = {}

# Load dữ liệu khi bot khởi động
load_keys_file()
load_auth_users_file()

# ===== LỆNH /key =====
@bot.message_handler(commands=['key'])
def handle_key(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    parts = msg.text.strip().split()

    keys = load_keys()  # 🔥 luôn load từ file

    # OWNER xem danh sách key
    if uid == OWNER_ID and len(parts) == 1:
        text = "\n".join(
            f"{k} → {datetime.fromisoformat(v).strftime('%H:%M %d-%m-%Y')}"
            for k, v in keys.items()
        )
        bot.reply_to(msg, f"Danh sach key:\n{text or 'Trong'}")
        return

    # USER nhập key
    if len(parts) == 2:
        key = parts[1].strip()

        if key not in keys:
            bot.reply_to(msg, "Key sai hoac khong ton tai.")
            return

        expire = datetime.fromisoformat(keys[key])

        if expire <= datetime.now():
            bot.reply_to(msg, "Key da het han.")
            keys.pop(key, None)
            save_keys(keys)
            return

        # kích hoạt user
        authenticated_users[uid] = expire
        save_auth_users_file()

        # xóa key sau khi dùng
        keys.pop(key, None)
        save_keys(keys)

        # Thông báo kích hoạt theo mẫu ZYNEX AI
        try:
            user = bot.get_chat(uid)
            display_name = user.first_name or user.username or str(uid)
        except Exception:
            display_name = getattr(msg.from_user, "first_name", None) or str(uid)

        try:
            admin_chat = bot.get_chat(OWNER_ID)
            admin_username = getattr(admin_chat, "username", None)
            admin_display = f"@{admin_username}" if admin_username else "Admin"
        except Exception:
            admin_display = "Admin"

        total_users = len(authenticated_users)
        duration_text = "Vĩnh viễn" if expire.year >= 2090 else ""
        if not duration_text:
            delta = expire - datetime.now()
            days = max(1, round(delta.total_seconds() / 86400))
            duration_text = {1: "1 Ngày", 3: "3 Ngày", 7: "1 Tuần", 30: "1 Tháng"}.get(days, f"{days} Ngày")

        activation_text = (
            "🎉 <b>KÍCH HOẠT THÀNH CÔNG</b> 🎉\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>ID:</b> <code>{uid}</code>\n"
            f"👤 <b>Tên:</b> {display_name}\n"
            f"📦 <b>Gói Key:</b> {duration_text}\n"
            f"🕐 <b>Kích hoạt lúc:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"⏳ <b>Hết hạn:</b> {expire.strftime('%d/%m/%Y %H:%M')}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Tổng user hệ thống:</b> {total_users}\n\n"
            f"✨ <b>Admin phụ trách:</b> {admin_display}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💜 <b>Cảm ơn bạn đã sử dụng ZYNEX AI!</b>"
        )

        bot.send_message(uid, activation_text, parse_mode="HTML")
    else:
        bot.reply_to(msg, "Sai cu phap. Dung: /key <ma_key>")
@bot.message_handler(commands=['menugame'])
def handle_menugame(msg):
    if not _group_admin_only(msg):
        return
    # Chỉ đổi keyboard sang tầng 2; KHÔNG gửi danh sách lệnh game vào chat.
    try:
        show_game_keyboard(msg.chat.id)
    except NameError:
        # Khi module chưa chạy tới phần định nghĩa keyboard (chỉ xảy ra nếu gọi rất sớm).
        bot.send_message(msg.chat.id, "🎮 Menu Game đang được khởi tạo...")
@bot.message_handler(commands=['checkkey'])
def handle_checkkey(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    expire = check_key(uid)

    if not expire and uid != OWNER_ID:
        bot.reply_to(msg, " Bạn chưa kích hoạt key hoặc key đã hết hạn.")
        return

    if uid == OWNER_ID:
        bot.reply_to(msg, " Bạn là Admin, không cần key.")
        return

    now = datetime.now()
    remaining = expire - now
    days = remaining.days
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    text = "🔑 Key của bạn còn lại: "
    if days > 0:
        text += f"{days} ngày "
    if hours > 0:
        text += f"{hours} giờ "
    if minutes > 0:
        text += f"{minutes} phút"

    text += f"\n🕒 Hết hạn: {expire.strftime('%H:%M %d-%m-%Y')}"

    bot.reply_to(msg, text)

@bot.message_handler(commands=['lichsu'])
def handle_lichsu(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid not in authenticated_users and uid != OWNER_ID:
        bot.reply_to(msg, " Bạn chưa được cấp quyền.")
        return
    ls = user_data.get(uid, {}).get("lich_su_phan_hoi", [])
    if not ls:
        bot.reply_to(msg, "Chưa có lịch sử.")
        return
    text = "\n".join(
        f"Phiên {x['phien']}| Dự: {x['du_doan']}| KQ: {x['kq']}| {'✅' if x['thang'] else '❌'}"
        for x in ls[-20:][::-1]
    )
    bot.reply_to(msg, f" Lịch sử:\n{text}")

@bot.message_handler(commands=['xoakey'])
def handle_xoakey(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, " Không có quyền.")
        return
    try:
        key = msg.text.split()[1]
        if key in active_keys:
            active_keys.pop(key, None)
            save_keys_file()
            bot.reply_to(msg, f" Đã xóa key {key}.")
        else:
            bot.reply_to(msg, " Key không tồn tại.")
    except:
        bot.reply_to(msg, " Cú pháp: /xoakey <key>")

@bot.message_handler(commands=['kickid'])
def handle_kick(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, " Không có quyền.")
        return
    try:
        uid = int(msg.text.split()[1])
        authenticated_users.pop(uid, None)
        kicked_users.add(uid)
        save_auth_users_file()
        save_kicked_file()
        running_users.discard(uid)
        bot.reply_to(msg, f" Đã kick ID: {uid}")
    except:
        bot.reply_to(msg, " Cú pháp: /kickid <id>")

@bot.message_handler(commands=['unkickid'])
def handle_unkick(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, " Không có quyền.")
        return
    try:
        uid = int(msg.text.split()[1])
        kicked_users.discard(uid)
        save_kicked_file()
        bot.reply_to(msg, f" Đã mở khóa ID: {uid}")
    except:
        bot.reply_to(msg, " Cú pháp: /unkickid <id>")

@bot.message_handler(commands=['uidstart'])
def handle_uidstart(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, " Không có quyền.")
        return
    text = "👥 Users đang dùng bot:\n"
    for uid in running_users:
        if uid in authenticated_users and uid not in kicked_users:
            try:
                user = bot.get_chat(uid)
                username = f"@{user.username}" if getattr(user, "username", None) else "Không rõ"
                text += f"• {uid} ({username})\n"
            except:
                text += f"• {uid} (Không thể lấy username)\n"
    bot.reply_to(msg, text)

@bot.message_handler(commands=['reset'])
def handle_reset(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    parts = msg.text.split()
    if uid == OWNER_ID and len(parts) > 1 and parts[1].lower() == "all":
        active_keys.clear()
        authenticated_users.clear()
        authenticated_users[OWNER_ID] = datetime.now() + timedelta(days=365*100)
        kicked_users.clear()
        save_keys_file()
        save_auth_users_file()
        save_kicked_file()
        user_data.clear()
        bot.reply_to(msg, "Đã xóa toàn bộ dữ liệu persistent (keys/auth/kicked).")
        return

    user_data.pop(uid, None)
    running_users.discard(uid)
    bot.reply_to(msg, "Đã reset dữ liệu, vui lòng /start để bắt đầu lại.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== HÀM DỰ ĐOÁN =====
def predict_taixiu(lich_su):

    if len(lich_su) < 5:
        return "ĐANG PHÂN TÍCH", [], 50, None

    recent = lich_su[-20:]
    last5 = lich_su[-5:]

    tai, xiu = 0, 0
    freq = {}
    pattern = ""

    for i, t in enumerate(recent):
        weight = (i + 1) / len(recent)

        if t >= 11:
            tai += weight
            pattern += "T"
        else:
            xiu += weight
            pattern += "X"

        freq[t] = freq.get(t, 0) + weight

    last3 = pattern[-3:]
    streak = 1

    for i in range(len(pattern)-1, 0, -1):
        if pattern[i] == pattern[i-1]:
            streak += 1
        else:
            break

    last = pattern[-1]

    if streak >= 4:
        prediction = "TÀI" if last == "T" else "XỈU"
    elif streak == 3:
        prediction = "TÀI" if last == "T" else "XỈU"
    elif last3 == "TTT":
        prediction = "XỈU"
    elif last3 == "XXX":
        prediction = "TÀI"
    elif pattern[-2:] in ["TX", "XT"]:
        prediction = "TÀI" if pattern[-2] == "T" else "XỈU"
    else:
        prediction = "TÀI" if tai > xiu else "XỈU"

    if prediction == "TÀI":
        candidates = list(range(11, 18))
    else:
        candidates = list(range(4, 11))

    center_bias = {
        4:1,5:2,6:3,7:4,8:5,9:4,10:3,
        11:3,12:4,13:5,14:4,15:3,16:2,17:1
    }

    trend_boost = {}
    for t in last5:
        trend_boost[t] = trend_boost.get(t, 0) + 2

    scored = []
    for k in candidates:
        score = (
            freq.get(k, 0) * 0.6 +
            center_bias.get(k, 0) * 0.3 +
            trend_boost.get(k, 0)
        )
        scored.append((k, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    vi = [k for k, v in scored[:3]]

    # ===== XÚC XẮC =====
    combos = []

    for a in range(1,7):
        for b in range(1,7):
            for c in range(1,7):
                if a + b + c in vi:
                    combos.append((a, b, c))

    combo_str = None
    if combos:
        pick = combos[len(combos)//2]  # chọn giữa (không cần random)
        combo_str = f"{pick[0]}-{pick[1]}-{pick[2]}"

    total = tai + xiu
    tin_cay = int((max(tai, xiu) / total) * 100)

    if streak >= 3:
        tin_cay += 5

    if tin_cay > 95:
        tin_cay = 95

    return prediction, vi, tin_cay, combo_str


# ===== AUTO =====
def sicbosun_auto(uid, chat_id):
    time = __import__("time")
    urllib = __import__("urllib.request").request
    json = __import__("json")

    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:
        try:
            if uid != OWNER_ID:
                if not check_key(uid):
                    bot.send_message(uid, "Key hết hạn!")
                    running_users.discard(uid)
                    break

            try:
                res = urllib.urlopen(
                    "https://afterwards-motels-honors-vendors.trycloudflare.com/api/sunsicbo",
                    timeout=5
                )
                data = json.loads(res.read().decode())
            except:
                time.sleep(3)
                continue

            phien = data.get("phien")
            tong = data.get("tong")
            ket_qua = data.get("ket_qua")

            xx1 = data.get("xuc_xac_1")
            xx2 = data.get("xuc_xac_2")
            xx3 = data.get("xuc_xac_3")

            if not phien or tong is None:
                time.sleep(3)
                continue

            tong = int(tong)

            if uid in user_data and user_data[uid].get("last_phien") == phien:
                time.sleep(3)
                continue

            user_data.setdefault(uid, {
                "lich_su_diem": [],
                "last_phien": 0
            })

            user_data[uid]["lich_su_diem"].append(tong)

            if len(user_data[uid]["lich_su_diem"]) > 50:
                user_data[uid]["lich_su_diem"].pop(0)

            du_doan, vi, tin_cay, combo = predict_taixiu(
                user_data[uid]["lich_su_diem"]
            )

            msg_text = (
                f"🎲 SicBo Sun\n"
                f"Phiên: {phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"────────────\n"
                f"Dự đoán: {du_doan} {tin_cay}%\n"
                f"Gợi ý vị: {', '.join(map(str, vi))}"
            )

            if combo:
                msg_text += f"\nXúc xắc đẹp: {combo}"

            bot.send_message(chat_id, msg_text)

            user_data[uid]["last_phien"] = phien

        except Exception as e:
            error_count += 1
            now = time.time()

            if now - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = now

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== COMMAND =====
@bot.message_handler(commands=['sicbosun'])
def sicbosun_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key!")
            return

    if uid in running_users:
        bot.reply_to(msg, "Đang chạy rồi!")
        return

    bot.reply_to(msg, "🚀 Bắt đầu Sicbo Sun...")

    threading.Thread(
        target=sicbosun_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


@bot.message_handler(commands=['stopsicbosun'])
def stopsicbosun_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Chưa chạy!")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "⛔ Đã dừng.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== HÀM DỰ ĐOÁN =====
def predict_taixiu(lich_su):

    if len(lich_su) < 5:
        return "ĐANG PHÂN TÍCH", [], 50, None

    recent = lich_su[-20:]
    last5 = lich_su[-5:]

    tai, xiu = 0, 0
    freq = {}
    pattern = ""

    for i, t in enumerate(recent):
        weight = (i + 1) / len(recent)

        if t >= 11:
            tai += weight
            pattern += "T"
        else:
            xiu += weight
            pattern += "X"

        freq[t] = freq.get(t, 0) + weight

    last3 = pattern[-3:]
    streak = 1

    for i in range(len(pattern)-1, 0, -1):
        if pattern[i] == pattern[i-1]:
            streak += 1
        else:
            break

    last = pattern[-1]

    if streak >= 4:
        prediction = "TÀI" if last == "T" else "XỈU"
    elif streak == 3:
        prediction = "TÀI" if last == "T" else "XỈU"
    elif last3 == "TTT":
        prediction = "XỈU"
    elif last3 == "XXX":
        prediction = "TÀI"
    elif pattern[-2:] in ["TX", "XT"]:
        prediction = "TÀI" if pattern[-2] == "T" else "XỈU"
    else:
        prediction = "TÀI" if tai > xiu else "XỈU"

    if prediction == "TÀI":
        candidates = list(range(11, 18))
    else:
        candidates = list(range(4, 11))

    center_bias = {
        4:1,5:2,6:3,7:4,8:5,9:4,10:3,
        11:3,12:4,13:5,14:4,15:3,16:2,17:1
    }

    trend_boost = {}
    for t in last5:
        trend_boost[t] = trend_boost.get(t, 0) + 2

    scored = []
    for k in candidates:
        score = (
            freq.get(k, 0) * 0.6 +
            center_bias.get(k, 0) * 0.3 +
            trend_boost.get(k, 0)
        )
        scored.append((k, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    vi = [k for k, v in scored[:3]]

    # ===== XÚC XẮC =====
    combos = []

    for a in range(1,7):
        for b in range(1,7):
            for c in range(1,7):
                if a + b + c in vi:
                    combos.append((a, b, c))

    combo_str = None
    if combos:
        pick = combos[len(combos)//2]  # chọn giữa (không cần random)
        combo_str = f"{pick[0]}-{pick[1]}-{pick[2]}"

    total = tai + xiu
    tin_cay = int((max(tai, xiu) / total) * 100)

    if streak >= 3:
        tin_cay += 5

    if tin_cay > 95:
        tin_cay = 95

    return prediction, vi, tin_cay, combo_str


# ===== AUTO =====
def sicbolive_auto(uid, chat_id):
    time = __import__("time")
    urllib = __import__("urllib.request").request
    json = __import__("json")

    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:
        try:
            if uid != OWNER_ID:
                if not check_key(uid):
                    bot.send_message(uid, "Key hết hạn!")
                    running_users.discard(uid)
                    break

            try:
                res = urllib.urlopen(
                    "https://letters-carries-hip-seeking.trycloudflare.com/sun/x88",
                    timeout=5
                )
                data = json.loads(res.read().decode())
            except:
                time.sleep(3)
                continue

            phien = data.get("phien")
            tong = data.get("tong")
            ket_qua = data.get("ket_qua")

            xx1 = data.get("xuc_xac_1")
            xx2 = data.get("xuc_xac_2")
            xx3 = data.get("xuc_xac_3")

            if not phien or tong is None:
                time.sleep(3)
                continue

            tong = int(tong)

            if uid in user_data and user_data[uid].get("last_phien") == phien:
                time.sleep(3)
                continue

            user_data.setdefault(uid, {
                "lich_su_diem": [],
                "last_phien": 0
            })

            user_data[uid]["lich_su_diem"].append(tong)

            if len(user_data[uid]["lich_su_diem"]) > 50:
                user_data[uid]["lich_su_diem"].pop(0)

            du_doan, vi, tin_cay, combo = predict_taixiu(
                user_data[uid]["lich_su_diem"]
            )

            msg_text = (
                f"🎲 SicBo Live\n"
                f"Phiên: {phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"────────────\n"
                f"Dự đoán: {du_doan} {tin_cay}%\n"
                f"Gợi ý vị: {', '.join(map(str, vi))}"
            )

            if combo:
                msg_text += f"\nXúc xắc đẹp: {combo}"

            bot.send_message(chat_id, msg_text)

            user_data[uid]["last_phien"] = phien

        except Exception as e:
            error_count += 1
            now = time.time()

            if now - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = now

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== COMMAND =====
@bot.message_handler(commands=['sicbolive'])
def sicbolive_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key!")
            return

    if uid in running_users:
        bot.reply_to(msg, "Đang chạy rồi!")
        return

    bot.reply_to(msg, "🚀 Bắt đầu Sicbo Live...")

    threading.Thread(
        target=sicbolive_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


@bot.message_handler(commands=['stopsicbolive'])
def stopsicbolive_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Chưa chạy!")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "⛔ Đã dừng.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()
group_start_times = {}  # Lưu thời gian bắt đầu nhóm
group_stats_data = {}   # Lưu thống kê riêng cho nhóm  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_gb68_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO 68GB MD5 =====
def gb68md5_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID and msg.chat.id != GROUP_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(msg.chat.id, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://character-retention-accepts-bouquet.trycloudflare.com/api/68/md5", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_gb68md5") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_gb68md5"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_gb68", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_gb68"].append(state)

            user_data[uid]["history_gb68"] = user_data[uid]["history_gb68"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_gb68_ai(user_data[uid]["history_gb68"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"68GB MD5\n"
                f"Phiên:#{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả:{ket_qua} {tong}\n"
                f"=============\n"
                f"Phiên:#{phien_ht}\n"
                f"Đoán:{du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['gb68md5'])
def gb68md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID and msg.chat.id != GROUP_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "68GB MD5 đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI 68GB MD5...")

    threading.Thread(
        target=gb68md5_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stopgb68md5'])
def stopgb68md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng 68GB MD5.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AUTO SICBO HIT CHO USER RIÊNG =====
def sicbohit_auto(uid, chat_id):
    """
    Vòng lặp riêng cho từng user
    """
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, " Key của bạn đã hết hạn hoặc không hợp lệ. Vui lòng nhập key mới để tiếp tục.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://sichit.onrender.com/sicbo", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")
            if not phien or (uid in user_data and user_data[uid].get("last_phien_hit") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_hit"] = phien

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("Xuc_xac1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("Xuc_xac2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("Xuc_xac3") or data.get("d3")
            tong = data.get("Tổng") or data.get("Tong") or data.get("total") or data.get("tong")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")
            phien_ht = data.get("phien_hien_tai")
            du_doan = data.get("du_doan")
            vi = data.get("dudoan_vi", [])
            tin_cay = data.get("do_tin_cay")

            msg_text = (
                f"SicBo Hit\n"
                f"Phiên: {phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"Phiên tiếp: {phien_ht}\n"
                f"Dự đoán: {du_doan} {tin_cay}\n"
                f"Gợi ý vị: {vi}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()
            if current_time - last_error_time > 60:
                print(f"Lỗi trong auto_loop user {uid}: {e}")
                last_error_time = current_time
            if error_count > 10:
                print(f"Dừng auto_loop Sicbo Hit cho user {uid} do quá nhiều lỗi")
                running_users.discard(uid)
                break

        time.sleep(3)

# ===== LỆNH /sicbohit =====
@bot.message_handler(commands=['sicbohit'])
def sicbohit_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # Kiểm tra user bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # Kiểm tra key nếu không phải admin
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key, vui lòng kích hoạt để sử dụng lệnh.")
            return

    if uid in running_users:
        bot.reply_to(msg, "Sicbo Hit đang chạy")
        return

    bot.reply_to(msg, "Bắt đầu dự đoán Sicbo Hit")

    threading.Thread(
        target=sicbohit_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()
# ===== LỆNH /stopsicbohit =====
@bot.message_handler(commands=['stopsicbohit'])
def stopsicbohit_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy Sicbo Hit hoặc đã dừng rồi.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Dừng dự đoán Sicbo Hit.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()
group_start_times = {}  # Lưu thời gian bắt đầu nhóm
group_stats_data = {}   # Lưu thống kê riêng cho nhóm  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_hitxanh_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO B52 MD5 =====
def hitxanh_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID and msg.chat.id != GROUP_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(msg.chat.id, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://nirvana-corners-discussing-treating.trycloudflare.com/api/tx", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_hitxanh") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_hitxanh"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_hitxanh", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_hitxanh"].append(state)

            user_data[uid]["history_hitxanh"] = user_data[uid]["history_hitxanh"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_hitxanh_ai(user_data[uid]["history_hitxanh"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"Hit Xanh\n"
                f"Phiên:#{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả:{ket_qua} {tong}\n"
                f"=============\n"
                f"Phiên:#{phien_ht}\n"
                f"Đoán:{du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['hitxanh'])
def hitxanh_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID and msg.chat.id != GROUP_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "Hit Xanh đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI Hit Xanh...")

    threading.Thread(
        target=hitxanh_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stophitxanh'])
def stophitxanh_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng Hit Xanh.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_hit_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO B52 MD5 =====
def hitmd5_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://letting-tackle-newton-oak.trycloudflare.com/api/tx", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_hitmd5") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_hitmd5"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_hit", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_hit"].append(state)

            user_data[uid]["history_hit"] = user_data[uid]["history_hit"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_hit_ai(user_data[uid]["history_hit"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"HIT MD5\n"
                f"Phiên: #{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"--------------------------\n"
                f"Phiên tiếp: #{phien_ht}\n"
                f"Dự đoán: {du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['hitmd5'])
def md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "HIT MD5 đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI HIT MD5...")

    threading.Thread(
        target=hitmd5_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stophitmd5'])
def stophitmd5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng HIT MD5.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_lc79_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO LC79 =====
def lc79_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://chance-compete-chambers-feelings.trycloudflare.com/api/tx", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_lc79") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_lc79"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_lc79", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_lc79"].append(state)

            user_data[uid]["history_lc79"] = user_data[uid]["history_lc79"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_lc79_ai(user_data[uid]["history_lc79"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"LC79\n"
                f"Phiên: #{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"--------------------------\n"
                f"Phiên tiếp: #{phien_ht}\n"
                f"Dự đoán: {du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['lc79'])
def lc79_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "LC79 đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI LC79...")

    threading.Thread(
        target=lc79_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stoplc79'])
def stoplc79_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng LC79.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_lc79md5_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO LC79 MD5 =====
def lc79md5_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://chance-compete-chambers-feelings.trycloudflare.com/api/txmd5", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_lc79md5") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_lc79md5"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_lc79md5", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_lc79md5"].append(state)

            user_data[uid]["history_lc79md5"] = user_data[uid]["history_lc79md5"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_lc79md5_ai(user_data[uid]["history_lc79md5"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"LC79 MD5\n"
                f"Phiên: #{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"--------------------------\n"
                f"Phiên tiếp: #{phien_ht}\n"
                f"Dự đoán: {du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['lc79md5'])
def lc79md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "LC79 MD5 đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI LC79 MD5...")

    threading.Thread(
        target=lc79md5_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stoplc79md5'])
def stoplc79md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng LC79 MD5.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_b52_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO B52 MD5 =====
def b52md5_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://gold-ultra-fails-handles.trycloudflare.com/txmd5", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_b52md5") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_b52md5"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_b52", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_b52"].append(state)

            user_data[uid]["history_b52"] = user_data[uid]["history_b52"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_b52_ai(user_data[uid]["history_b52"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"B52 MD5\n"
                f"Phiên: #{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"--------------------------\n"
                f"Phiên tiếp: #{phien_ht}\n"
                f"Dự đoán: {du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['b52md5'])
def b52md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "B52 MD5 đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI B52 MD5...")

    threading.Thread(
        target=b52md5_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stopb52md5'])
def stopb52md5_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng B52 MD5.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AI LOGIC (HỌC TRƯỢT + TRỌNG SỐ) =====
def predict_789club_ai(history):
    if len(history) < 10:
        return f"{len(history)}/10", 0

    train = history[-10:]
    tx = [x.split("_")[0] for x in train]

    m1 = {}
    for i in range(len(tx) - 1):
        a, b = tx[i], tx[i+1]
        weight = i + 1

        if a not in m1:
            m1[a] = {"TÀI": 0, "XỈU": 0}

        m1[a][b] += weight

    m2 = {}
    for i in range(len(tx) - 2):
        key = (tx[i], tx[i+1])
        nxt = tx[i+2]
        weight = i + 1

        if key not in m2:
            m2[key] = {"TÀI": 0, "XỈU": 0}

        m2[key][nxt] += weight

    last1 = tx[-1]
    last2 = (tx[-2], tx[-1])

    if last2 in m2:
        tai = m2[last2]["TÀI"]
        xiu = m2[last2]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    if last1 in m1:
        tai = m1[last1]["TÀI"]
        xiu = m1[last1]["XỈU"]
        if tai + xiu > 0:
            if tai > xiu:
                return "TÀI", int(50 + (tai/(tai+xiu))*50)
            elif xiu > tai:
                return "XỈU", int(50 + (xiu/(tai+xiu))*50)

    return "TÀI", 50


# ===== AUTO 789CLUB =====
def club789_auto(uid, chat_id):
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:   # ✅ PHẢI NẰM TRONG HÀM
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, "Key hết hạn hoặc không hợp lệ.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://dependent-epinions-somebody-enclosed.trycloudflare.com/api/tx", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien") or data.get ("phiên")

            if not phien or (uid in user_data and user_data[uid].get("last_phien_club789") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_club789"] = phien

            # ===== FIX PHIÊN TIẾP =====
            try:
                phien_ht = int(phien) + 1
            except:
                phien_ht = "..."

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("d3")

            tong = data.get("Tong") or data.get("tong") or data.get("total")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")

            # ===== LƯU HISTORY =====
            user_data[uid].setdefault("history_club789", [])

            if ket_qua and tong:
                state = f"{ket_qua.upper()}_{tong}"
                user_data[uid]["history_club789"].append(state)

            user_data[uid]["history_club789"] = user_data[uid]["history_club789"][-50:]

            # ===== AI =====
            du_doan, tin_cay = predict_789club_ai(user_data[uid]["history_club789"])

            if tin_cay == 0:
                du_doan_text = du_doan
            else:
                du_doan_text = f"{du_doan}"

            msg_text = (
                f"CLUB789\n"
                f"Phiên: #{phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"--------------------------\n"
                f"Phiên tiếp: #{phien_ht}\n"
                f"Dự đoán: {du_doan_text}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()

            if current_time - last_error_time > 60:
                print(f"Lỗi user {uid}: {e}")
                last_error_time = current_time

            if error_count > 10:
                running_users.discard(uid)
                break

        time.sleep(3)
# ===== LỆNH START =====
@bot.message_handler(commands=['club789'])
def club789_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # check key
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key.")
            return

    # đang chạy rồi
    if uid in running_users:
        bot.reply_to(msg, "789CLUB đang chạy rồi.")
        return

    bot.reply_to(msg, "Bắt đầu AI 789CLUB...")

    threading.Thread(
        target=club789_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()


# ===== LỆNH STOP =====
@bot.message_handler(commands=['stopclub789'])
def stopclub789_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy hoặc đã dừng.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Đã dừng 789.")
# ===== QUẢN LÝ TRẠNG THÁI GAME =====
game_running = {}
game_last_phien = {}
running_users = set()  # lưu user đang chạy
user_data = {}         # lưu data user

# ===== HÀM CHECK KEY =====
def check_key(uid):
    """
    Kiểm tra key của user có hợp lệ không.
    """
    with data_lock:
        expiry = authenticated_users.get(uid)
        if not expiry:
            return None

        if isinstance(expiry, str):
            try:
                expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
                authenticated_users[uid] = expiry
                save_auth_users_file()
            except Exception as e:
                print(f"Lỗi convert expiry string: {e}")
                return None

        now = datetime.now()
        if isinstance(expiry, datetime) and expiry > now:
            return expiry
        
        return None

# ===== AUTO B52 HŨ CHO USER RIÊNG =====
def b52hu_auto(uid, chat_id):
    """
    Vòng lặp riêng cho từng user
    """
    last_error_time = 0
    error_count = 0
    running_users.add(uid)

    while uid in running_users:
        try:
            if uid != OWNER_ID:
                expiry = check_key(uid)
                if not expiry:
                    try:
                        bot.send_message(uid, " Key của bạn đã hết hạn hoặc không hợp lệ. Vui lòng nhập key mới để tiếp tục.")
                    except:
                        pass
                    running_users.discard(uid)
                    break

            r = requests.get("https://b52-cbqn.onrender.com/api/taixiu", timeout=5)
            if r.status_code != 200:
                time.sleep(3)
                continue

            data = r.json()
            phien = data.get("Phien") or data.get("phien")
            if not phien or (uid in user_data and user_data[uid].get("last_phien_b52hu") == phien):
                time.sleep(3)
                continue

            user_data.setdefault(uid, {})
            user_data[uid]["last_phien_b52hu"] = phien

            xx1 = data.get("Xuc_xac_1") or data.get("xuc_xac_1") or data.get("Xuc_xac1") or data.get("d1")
            xx2 = data.get("Xuc_xac_2") or data.get("xuc_xac_2") or data.get("Xuc_xac2") or data.get("d2")
            xx3 = data.get("Xuc_xac_3") or data.get("xuc_xac_3") or data.get("Xuc_xac3") or data.get("d3")
            tong = data.get("Tổng") or data.get("Tong") or data.get("total") or data.get("tong")
            ket_qua = data.get("Ket_qua") or data.get("ket_qua")
            phien_ht = data.get("phien_hien_tai")
            du_doan = data.get("du_doan")
            tin_cay = data.get("do_tin_cay")

            msg_text = (
                f"B52 Hũ\n"
                f"Phiên: {phien} ({xx1}-{xx2}-{xx3})\n"
                f"Kết quả: {ket_qua} {tong}\n"
                f"Phiên tiếp: {phien_ht}\n"
                f"Dự đoán: {du_doan} {tin_cay}"
            )

            bot.send_message(chat_id, msg_text)

        except Exception as e:
            error_count += 1
            current_time = time.time()
            if current_time - last_error_time > 60:
                print(f"Lỗi trong auto_loop user {uid}: {e}")
                last_error_time = current_time
            if error_count > 10:
                print(f"Dừng auto_loop B52 Hũ cho user {uid} do quá nhiều lỗi")
                running_users.discard(uid)
                break

        time.sleep(3)

# ===== LỆNH /b52hu =====
@bot.message_handler(commands=['b52hu'])
def b52hu_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # Kiểm tra user bị chặn
    if uid in kicked_users:
        bot.reply_to(msg, "Bạn đã bị chặn!")
        return

    # Kiểm tra key nếu không phải admin
    if uid != OWNER_ID:
        if not check_key(uid):
            bot.reply_to(msg, "Bạn chưa kích hoạt key, vui lòng kích hoạt để sử dụng lệnh.")
            return

    if uid in running_users:
        bot.reply_to(msg, "B52 Hũ đang chạy")
        return

    bot.reply_to(msg, "Bắt đầu dự đoán B52 Hũ")

    threading.Thread(
        target=b52hu_auto,
        args=(uid, chat_id),
        daemon=True
    ).start()
@bot.message_handler(commands=['stopb52hu'])
def stopb52hu_cmd(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id

    if uid not in running_users:
        bot.reply_to(msg, "Bạn chưa chạy B52 Hũ hoặc đã dừng rồi.")
        return

    running_users.discard(uid)
    bot.reply_to(msg, "Dừng dự đoán B52 Hũ.")

# ================== MENU NÚT TELEGRAM ==================
from telebot import types as tg_types
import requests
types = tg_types  # tương thích với menu Mua Key mới

PENDING_KEY_USERS = set()
MENU_STATE = {}  # uid -> main/game/sunwin

BCR_API_URL = "https://bcrsexy.onrender.com/api/baccarat"
BCR_RUNNING = {}
BCR_LOCK = threading.Lock()
BCR_STATS_FILE = "bcr_stats.json"
BCR_STATS_LOCK = threading.Lock()
BCR_STATS = {}

def _bcr_stats_key(chat_id, game_name, table_no):
    return f"{chat_id}|{game_name}|{table_no}"

def _bcr_load_stats():
    global BCR_STATS
    try:
        with open(BCR_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            BCR_STATS = data if isinstance(data, dict) else {}
    except Exception:
        BCR_STATS = {}

def _bcr_save_stats():
    try:
        with BCR_STATS_LOCK:
            tmp = BCR_STATS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(BCR_STATS, f, ensure_ascii=False, indent=2)
            os.replace(tmp, BCR_STATS_FILE)
    except Exception as e:
        print(f"Lỗi lưu BCR stats: {e}")

_bcr_load_stats()

def _bcr_get_stats(chat_id, game_name, table_no):
    key = _bcr_stats_key(chat_id, game_name, table_no)
    with BCR_STATS_LOCK:
        if key not in BCR_STATS:
            BCR_STATS[key] = {
                "dung": 0,
                "sai": 0,
                "history": [],
                "pending_prediction": None,
                "last_result": "",
                "last_round": None
            }
        return BCR_STATS[key]

def _bcr_predict(result):
    """Heuristic thống kê đơn giản, không đảm bảo kết quả tương lai."""
    seq = [c.upper() for c in str(result) if c.upper() in ("B", "P", "T")]
    if len(seq) < 5:
        return None, 0

    recent = seq[-12:]
    counts = {c: recent.count(c) for c in ("B", "P", "T")}
    pred = max(counts, key=counts.get)
    confidence = round(counts[pred] / len(recent) * 100)
    return pred, confidence

def _bcr_label(x):
    return {"B": "🔵 Banker", "P": "🔴 Player", "T": "🟢 Tie"}.get(x, str(x))

def _bcr_score_and_predict(chat_id, game_name, table_no, result, round_id=None):
    """Chấm dự đoán cũ bằng kết quả mới rồi tạo dự đoán cho ván kế."""
    seq = [c.upper() for c in str(result) if c.upper() in ("B", "P", "T")]
    if not seq:
        return

    st = _bcr_get_stats(chat_id, game_name, table_no)
    current_round = str(round_id) if round_id is not None else str(len(seq))

    # Khởi tạo lần đầu: không chấm ngược lịch sử cũ.
    if not st.get("last_result"):
        st["last_result"] = str(result)
        st["last_round"] = current_round
        pred, conf = _bcr_predict(result)
        st["pending_prediction"] = pred
        st["pending_confidence"] = conf
        _bcr_save_stats()
        return

    # Chỉ chấm khi chuỗi kết quả thực sự có ván mới.
    old_seq = [c.upper() for c in str(st.get("last_result", "")) if c.upper() in ("B", "P", "T")]
    if len(seq) <= len(old_seq):
        return

    # Nếu có dự đoán cho ván kế trước đó, chấm với kết quả mới nhất.
    pred = st.get("pending_prediction")
    actual = seq[-1]
    if pred in ("B", "P", "T"):
        ok = pred == actual
        if ok:
            st["dung"] = int(st.get("dung", 0)) + 1
        else:
            st["sai"] = int(st.get("sai", 0)) + 1

        hist = st.setdefault("history", [])
        hist.append({
            "round": current_round,
            "prediction": pred,
            "actual": actual,
            "ok": ok,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        st["history"] = hist[-50:]

    st["last_result"] = str(result)
    st["last_round"] = current_round

    new_pred, conf = _bcr_predict(result)
    st["pending_prediction"] = new_pred
    st["pending_confidence"] = conf
    _bcr_save_stats()

def _bcr_stats_text(chat_id, game_name, table_no):
    st = _bcr_get_stats(chat_id, game_name, table_no)
    dung = int(st.get("dung", 0))
    sai = int(st.get("sai", 0))
    tong = dung + sai
    rate = round(dung / tong * 100, 2) if tong else 0
    pred = st.get("pending_prediction")
    conf = st.get("pending_confidence", 0)

    lines = [
        f"📊 <b>THỐNG KÊ BCR — {game_name} BÀN {table_no}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"✅ Đúng: <b>{dung}</b>   ❌ Sai: <b>{sai}</b>",
        f"🏆 Tỷ lệ đúng: <b>{rate}%</b> ({dung}/{tong})",
    ]
    if pred:
        lines.append(f"🔮 Dự đoán kế: <b>{_bcr_label(pred)}</b> | {conf}% ")
    else:
        lines.append("🔮 Dự đoán kế: <b>ĐANG PHÂN TÍCH</b>")

    lines += ["━━━━━━━━━━━━━━━━━━", "📜 <b>Lịch sử 10 dự đoán gần nhất:</b>"]
    history = st.get("history", [])[-10:]
    if not history:
        lines.append("Chưa có phiên nào được đối chiếu.")
    else:
        for item in reversed(history):
            mark = "✅" if item.get("ok") else "❌"
            lines.append(
                f"#{item.get('round', '?')} | Dự: {_bcr_label(item.get('prediction'))} "
                f"→ KQ: {_bcr_label(item.get('actual'))} {mark}"
            )
    return "\n".join(lines)


def main_keyboard():
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        tg_types.KeyboardButton("🚀 Chạy Tool"),
        tg_types.KeyboardButton("🔑 Sử Dụng Key"),
    )
    kb.add(
        tg_types.KeyboardButton("💰 Mua Key"),
        tg_types.KeyboardButton("📁 Quản Lí Key"),
    )
    kb.add(
        tg_types.KeyboardButton("💳 Nạp Tiền"),
        tg_types.KeyboardButton("💵 Số Dư"),
    )
    kb.add(
        tg_types.KeyboardButton("🎁 Giftcode"),
        tg_types.KeyboardButton("🎟️ Tạo Giftcode"),
    )
    kb.add(
        tg_types.KeyboardButton("📜 Lịch Sử Nạp"),
        tg_types.KeyboardButton("✅ Duyệt Nạp"),
    )
    kb.add(
        tg_types.KeyboardButton("📝 Gửi Feedback"),
        tg_types.KeyboardButton("🆘 Hỗ Trợ"),
        tg_types.KeyboardButton("📢 Thông Báo"),
    )
    return kb


def game_keyboard():
    """Tầng 2: chỉ hiện các game, không hiện danh sách lệnh trong chat."""
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    rows = [
        ("🎮 GAME SUNWIN", "💎 GAME HIT CLUB"),
        ("🔥 GAME 789CLUB", "🎯 GAME 68GB"),
        ("⚡ GAME B52 CLUB", "🍀 GAME LC79"),
        ("🎰 GAME BCR", "🏠 Menu chính"),
    ]
    for row in rows:
        kb.row(*(tg_types.KeyboardButton(x) for x in row))
    return kb


def sunwin_keyboard():
    """Tầng 3: menu con của GAME SUNWIN."""
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    rows = [
        ("☀️ SUN THƯỜNG", "🌙 SUN NHANH"),
        ("⚡ SUN SIÊU TỐC", "🎯 SUN VIP"),
        ("↩️ Quay lại Menu Game",),
    ]
    for row in rows:
        kb.row(*(tg_types.KeyboardButton(x) for x in row))
    return kb


def simple_game_keyboard(title):
    """Menu con cho các game khác; có nút quay lại tầng 2."""
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(tg_types.KeyboardButton(f"▶️ {title} CHẠY"), tg_types.KeyboardButton(f"⏹️ {title} DỪNG"))
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Menu Game"))
    return kb


def sun_mode_keyboard(name):
    """Tầng 4: mỗi chế độ Sun có menu Bật/Tắt riêng."""
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton(f"🟢 BẬT {name}"),
        tg_types.KeyboardButton(f"🔴 TẮT {name}"),
    )
    if name == "SUN NHANH":
        kb.row(
            tg_types.KeyboardButton("📊 THỐNG KÊ SUN NHANH"),
            tg_types.KeyboardButton("📜 LỊCH SỬ SUN NHANH"),
        )
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Menu Sunwin"))
    return kb


def show_keyboard(chat_id, keyboard):
    return bot.send_message(
        chat_id,
        "🎮",
        reply_markup=keyboard,
        disable_notification=True
    )


def show_game_keyboard(chat_id):
    MENU_STATE[chat_id] = "game"
    return show_keyboard(chat_id, game_keyboard())


def show_sunwin_keyboard(chat_id):
    MENU_STATE[chat_id] = "sunwin"
    return show_keyboard(chat_id, sunwin_keyboard())


def show_main_keyboard(chat_id):
    MENU_STATE[chat_id] = "main"
    return show_keyboard(chat_id, main_keyboard())


@bot.message_handler(func=lambda m: m.text == "🎮 GAME SUNWIN")
def game_sunwin_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    if uid != OWNER_ID and not check_key(uid):
        bot.send_message(msg.chat.id, "🔑 Key chưa được kích hoạt.")
        return
    # TẦNG 3: thay trực tiếp keyboard bằng menu Sunwin.
    show_sunwin_keyboard(msg.chat.id)


def hitclub_keyboard():
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton("🔐 HIT CLUB MD5"),
        tg_types.KeyboardButton("🎲 HIT CLUB TÀI XỈU"),
    )
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Menu Game"))
    return kb


@bot.message_handler(func=lambda m: m.text == "💎 GAME HIT CLUB")
def game_hit_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    show_keyboard(msg.chat.id, hitclub_keyboard())


@bot.message_handler(func=lambda m: m.text == "🔥 GAME 789CLUB")
def game_789_button(msg):
    if not _group_admin_only(msg):
        return
    show_keyboard(msg.chat.id, simple_game_keyboard("789CLUB"))


def game68_keyboard():
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton("🔐 68GB MD5"),
        tg_types.KeyboardButton("↩️ Quay lại Menu Game"),
    )
    return kb


@bot.message_handler(func=lambda m: m.text == "🎯 GAME 68GB")
def game_68_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    show_keyboard(msg.chat.id, game68_keyboard())


@bot.message_handler(func=lambda m: m.text == "⚡ GAME B52 CLUB")
def game_b52_button(msg):
    if not _group_admin_only(msg):
        return
    show_keyboard(msg.chat.id, simple_game_keyboard("B52"))


def md5_keyboard():
    """Menu riêng cho MD5/TXMD5."""
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton("🟢 BẬT MD5"),
        tg_types.KeyboardButton("🔴 TẮT MD5"),
    )
    kb.row(
        tg_types.KeyboardButton("📊 THỐNG KÊ MD5"),
        tg_types.KeyboardButton("📜 LỊCH SỬ MD5"),
    )
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Menu Game"))
    return kb


@bot.message_handler(func=lambda m: m.text == "🍀 GAME LC79")
def game_lc79_button(msg):
    if not _group_admin_only(msg):
        return
    MENU_STATE[msg.chat.id] = "md5"
    show_keyboard(msg.chat.id, md5_keyboard())


@bot.message_handler(func=lambda m: m.text == "↩️ Quay lại Menu Game")
def back_game_menu_button(msg):
    if not _group_admin_only(msg):
        return
    show_game_keyboard(msg.chat.id)





# ================== GAME 68GB / MD5 ==================
GAME68_MD5_API = "https://gbmd5-4a69a-default-rtdb.asia-southeast1.firebasedatabase.app/taixiu_sessions.json"
GAME68_RUNNING = {}
GAME68_LAST = {}
GAME68_STATS_FILE = "game68_md5_stats.json"
GAME68_STATS = {}

def _load_game68_stats():
    global GAME68_STATS
    try:
        with open(GAME68_STATS_FILE, "r", encoding="utf-8") as f:
            GAME68_STATS = json.load(f)
    except Exception:
        GAME68_STATS = {}

def _save_game68_stats():
    try:
        with open(GAME68_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(GAME68_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Lỗi lưu GAME 68GB: {e}")

def _g68_stats(chat_id):
    key=str(chat_id)
    if key not in GAME68_STATS:
        GAME68_STATS[key]={"correct":0,"wrong":0,"history":[],"pending":None}
    return GAME68_STATS[key]

def _g68_fetch():
    """Đọc Firebase và luôn sắp xếp theo số phiên, không phụ thuộc thứ tự JSON."""
    try:
        r = requests.get(
            GAME68_MD5_API,
            timeout=(5, 12),
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        r.raise_for_status()
        data = r.json()

        # Firebase REST trả object dạng:
        # {"397563_end": {...}, "397564_end": {...}}
        rows = []
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue

                row = dict(value)
                row["_key"] = str(key)

                # Chỉ nhận bản ghi kết thúc có phiên hợp lệ.
                raw_session = row.get("phien", key)
                digits = re.sub(r"\D", "", str(raw_session))
                if not digits:
                    continue

                row["_session_num"] = int(digits)
                rows.append(row)

        elif isinstance(data, list):
            for value in data:
                if not isinstance(value, dict):
                    continue
                row = dict(value)
                raw_session = row.get("phien", row.get("id", ""))
                digits = re.sub(r"\D", "", str(raw_session))
                if not digits:
                    continue
                row["_session_num"] = int(digits)
                rows.append(row)

        # Phiên nhỏ -> lớn. Phần tử cuối luôn là phiên mới nhất.
        rows.sort(key=lambda x: x["_session_num"])

        return rows

    except requests.RequestException as e:
        print(f"Lỗi kết nối API GAME 68GB: {e}")
        return []
    except ValueError as e:
        print(f"API GAME 68GB trả JSON không hợp lệ: {e}")
        return []
    except Exception as e:
        print(f"Lỗi xử lý API GAME 68GB: {e}")
        return []

def _g68_result(row):
    v=str(row.get("ket_qua","")).strip().lower()
    if v in ("tài","tai","t"): return "Tài"
    if v in ("xỉu","xiu","x"): return "Xỉu"
    return "?"

def _g68_session(row):
    if "_session_num" in row:
        return str(row["_session_num"])
    return str(row.get("phien", row.get("_key","?"))).replace("_end","")

def _g68_md5(row):
    # MD5 hiển thị của dữ liệu phiên, không phải "mã bí mật" dùng để biết trước kết quả.
    payload={
        "phien":row.get("phien"),
        "xuc_xac_1":row.get("xuc_xac_1"),
        "xuc_xac_2":row.get("xuc_xac_2"),
        "xuc_xac_3":row.get("xuc_xac_3"),
        "tong":row.get("tong"),
        "ket_qua":row.get("ket_qua"),
        "time":row.get("time"),
    }
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def _g68_predict(history):
    # Chỉ là heuristic thống kê, không phải dự đoán chắc chắn.
    vals=[_g68_result(x) for x in history if _g68_result(x) in ("Tài","Xỉu")]
    if not vals:
        return "?"
    # Majority of last 5, tie -> opposite of latest.
    last=vals[-5:]
    t=last.count("Tài")
    x=last.count("Xỉu")
    if t>x: return "Tài"
    if x>t: return "Xỉu"
    return "Xỉu" if vals[-1]=="Tài" else "Tài"

def _g68_grade(chat_id, row, history):
    st=_g68_stats(chat_id)
    pending=st.get("pending")
    if not pending:
        return
    sid=_g68_session(row)
    if sid==str(pending.get("session")):
        return
    actual=_g68_result(row)
    pred=pending.get("prediction")
    if actual not in ("Tài","Xỉu") or pred not in ("Tài","Xỉu"):
        st["pending"]=None
        _save_game68_stats()
        return
    ok=(pred==actual)
    if ok: st["correct"]+=1
    else: st["wrong"]+=1
    st["history"].insert(0,{
        "session":pending.get("session"),
        "prediction":pred,
        "actual":actual,
        "status":"ĐÚNG" if ok else "SAI"
    })
    st["history"]=st["history"][:30]
    st["pending"]=None
    _save_game68_stats()

def _g68_stats_text(chat_id):
    st=_g68_stats(chat_id)
    c=int(st.get("correct",0)); w=int(st.get("wrong",0))
    total=c+w
    rate=(c*100/total) if total else 0
    lines=[
        "📊 <b>68GB — THỐNG KÊ MD5</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"✅ Đúng: <b>{c}</b>",
        f"❌ Sai: <b>{w}</b>",
        f"🎯 Đã đối chiếu: <b>{total}</b>",
        f"🏆 Tỷ lệ đúng: <b>{rate:.1f}%</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📜 <b>Lịch sử 30 phiên:</b>"
    ]
    hist=st.get("history",[])
    if not hist:
        lines.append("Chưa có phiên được đối chiếu.")
    else:
        for h in hist[:30]:
            icon="✅" if h.get("status")=="ĐÚNG" else "❌"
            lines.append(
                f"{icon} #{h.get('session','?')} | Dự: {h.get('prediction','?')} | KQ: {h.get('actual','?')}"
            )
    return "\n".join(lines)

def _g68_keyboard():
    kb=tg_types.ReplyKeyboardMarkup(resize_keyboard=True,row_width=2)
    kb.row(
        tg_types.KeyboardButton("🟢 BẬT 68GB MD5"),
        tg_types.KeyboardButton("🔴 TẮT 68GB MD5"),
    )
    kb.row(
        tg_types.KeyboardButton("📊 THỐNG KÊ 68GB"),
        tg_types.KeyboardButton("📜 LỊCH SỬ 68GB"),
    )
    kb.row(tg_types.KeyboardButton("↩️ GAME 68GB"))
    return kb

def _g68_panel(row, prediction, running, chat_id):
    if not row:
        return "🎯 <b>68GB MD5</b>\n📡 API: 🔴 Không lấy được dữ liệu."
    dice=f"{row.get('xuc_xac_1','?')}-{row.get('xuc_xac_2','?')}-{row.get('xuc_xac_3','?')}"
    return (
        "🎯 <b>GAME 68GB — MD5</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📡 API: {'🟢 ĐANG BẬT' if running else '🔴 ĐANG TẮT'}\n"
        f"🎲 Phiên: <b>#{_g68_session(row)}</b>\n"
        f"🎯 Xúc xắc: <b>{dice}</b> | Tổng: <b>{row.get('tong','?')}</b>\n"
        f"📌 Kết quả: <b>{_g68_result(row)}</b>\n"
        f"🤖 Dự đoán : <b>{prediction}</b>\n"
        f"🔐 MD5: <code>{_g68_md5(row)}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + _g68_stats_text(chat_id)
    )

def _g68_loop(chat_id):
    key=str(chat_id)
    last=GAME68_LAST.get(key)
    while GAME68_RUNNING.get(key):
        rows=_g68_fetch()
        if rows:
            row=rows[-1]
            sid=_g68_session(row)
            if sid!=last:
                _g68_grade(chat_id,row,rows[:-1])
                pred=_g68_predict(rows[:-1])
                GAME68_LAST[key]=sid
                st=_g68_stats(chat_id)
                st["pending"]={"session":sid,"prediction":pred}
                _save_game68_stats()
                try:
                    bot.send_message(
                        chat_id,
                        _g68_panel(row,pred,True,chat_id),
                        parse_mode="HTML",
                        reply_markup=_g68_keyboard()
                    )
                except Exception as e:
                    print(f"Lỗi gửi GAME 68GB: {e}")
        time.sleep(3)

_load_game68_stats()

@bot.message_handler(func=lambda m: m.text == "🔐 68GB MD5")
def game68_md5_menu(msg):
    if not _group_admin_only(msg):
        return
    show_keyboard(msg.chat.id,_g68_keyboard())

@bot.message_handler(func=lambda m: m.text == "↩️ GAME 68GB")
def back_game68(msg):
    if not _group_admin_only(msg):
        return
    show_keyboard(msg.chat.id,game68_keyboard())

@bot.message_handler(func=lambda m: m.text == "🟢 BẬT 68GB MD5")
def start_game68(msg):
    if not _group_admin_only(msg):
        return
    rows=_g68_fetch()
    if not rows:
        bot.send_message(msg.chat.id,"⚠️ API 68GB chưa trả dữ liệu.")
        return
    key=str(msg.chat.id)
    GAME68_RUNNING[key]=True
    if not GAME68_LAST.get(key):
        GAME68_LAST[key]=_g68_session(rows[-1])
    st=_g68_stats(msg.chat.id)
    pred=_g68_predict(rows[:-1])
    st["pending"]={"session":_g68_session(rows[-1]),"prediction":pred}
    _save_game68_stats()
    started = getattr(start_game68, "_started", None)
    if started is None:
        started = set()
        start_game68._started = started
    if key not in started:
        started.add(key)
        threading.Thread(target=_g68_loop, args=(msg.chat.id,), daemon=True).start()
    bot.send_message(
        msg.chat.id,
        "🟢 Đã kết nối GAME 68GB.\n\n"
        + _g68_panel(rows[-1], pred, True, msg.chat.id),
        parse_mode="HTML",
        reply_markup=_g68_keyboard(),
    )

@bot.message_handler(func=lambda m: m.text == "🔴 TẮT 68GB MD5")
def stop_game68(msg):
    if not _group_admin_only(msg):
        return
    GAME68_RUNNING[str(msg.chat.id)]=False
    bot.send_message(msg.chat.id,"🔴 68GB MD5: ĐÃ TẮT.",reply_markup=_g68_keyboard())

@bot.message_handler(func=lambda m: m.text == "📊 THỐNG KÊ 68GB")
def stats_game68(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id,_g68_stats_text(msg.chat.id),parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📜 LỊCH SỬ 68GB")
def history_game68(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id,_g68_stats_text(msg.chat.id),parse_mode="HTML")

# ================== HIT CLUB API ==================
HITCLUB_API_URL = "https://apihitclubmd5-x6r3.onrender.com/"
HITCLUB_RUNNING = {}
HITCLUB_LAST = {}
HITCLUB_STATS_FILE = "hitclub_stats.json"
HITCLUB_STATS = {}

def _load_hitclub_stats():
    global HITCLUB_STATS
    try:
        with open(HITCLUB_STATS_FILE, "r", encoding="utf-8") as f:
            HITCLUB_STATS = json.load(f)
    except Exception:
        HITCLUB_STATS = {}

def _save_hitclub_stats():
    try:
        with open(HITCLUB_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(HITCLUB_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Lỗi lưu thống kê HIT CLUB: {e}")

def _hc_stats(chat_id, mode):
    key = f"{chat_id}:{mode}"
    if key not in HITCLUB_STATS:
        HITCLUB_STATS[key] = {"correct": 0, "wrong": 0, "history": [], "pending": None}
    return HITCLUB_STATS[key]

def _hc_fetch():
    try:
        r = requests.get(HITCLUB_API_URL, timeout=(5, 12), headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], dict) else {}
    except Exception as e:
        print(f"Lỗi API HIT CLUB: {e}")
    return {}

def _hc_result(row):
    v = str(row.get("ket_qua", row.get("result", ""))).strip().lower()
    if v in {"t", "tai", "tài"}:
        return "Tài"
    if v in {"x", "xiu", "xỉu"}:
        return "Xỉu"
    return "?"

def _hc_prediction(row):
    v = str(row.get("du_doan", row.get("prediction", ""))).strip().lower()
    if v in {"t", "tai", "tài"}:
        return "Tài"
    if v in {"x", "xiu", "xỉu"}:
        return "Xỉu"
    return "?"

def _hc_session(row):
    return str(row.get("phien", row.get("session", row.get("id", "?"))))

def _hc_md5(row):
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def _hc_grade(chat_id, mode, row):
    st = _hc_stats(chat_id, mode)
    pending = st.get("pending")
    if not pending:
        return
    current_session = _hc_session(row)
    if current_session == str(pending.get("session")):
        return

    pred = pending.get("prediction")
    actual = _hc_result(row)
    if pred not in {"Tài", "Xỉu"} or actual not in {"Tài", "Xỉu"}:
        st["pending"] = None
        _save_hitclub_stats()
        return

    ok = pred == actual
    st["correct"] += 1 if ok else 0
    st["wrong"] += 0 if ok else 1
    st["history"].insert(0, {
        "session": pending.get("session"),
        "prediction": pred,
        "actual": actual,
        "status": "ĐÚNG" if ok else "SAI",
    })
    st["history"] = st["history"][:30]
    st["pending"] = None
    _save_hitclub_stats()

def _hc_stats_text(chat_id, mode):
    st = _hc_stats(chat_id, mode)
    c = int(st.get("correct", 0))
    w = int(st.get("wrong", 0))
    total = c + w
    rate = c * 100 / total if total else 0
    title = "MD5" if mode == "md5" else "TÀI XỈU THƯỜNG"
    lines = [
        f"📊 <b>THỐNG KÊ HIT CLUB {title}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"✅ Đúng: <b>{c}</b>",
        f"❌ Sai: <b>{w}</b>",
        f"🎯 Đã đối chiếu: <b>{total}</b>",
        f"🏆 Tỷ lệ đúng: <b>{rate:.1f}%</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📜 <b>30 phiên gần nhất:</b>",
    ]
    history = st.get("history", [])
    if not history:
        lines.append("Chưa có phiên được đối chiếu.")
    else:
        for h in history[:30]:
            icon = "✅" if h.get("status") == "ĐÚNG" else "❌"
            lines.append(
                f"{icon} #{h.get('session','?')} | "
                f"Dự: {h.get('prediction','?')} | KQ: {h.get('actual','?')}"
            )
    return "\n".join(lines)

def _hc_panel(row, mode, running, chat_id=None):
    title = "MD5" if mode == "md5" else "TÀI XỈU THƯỜNG"
    if not row:
        return (
            f"🎯 <b>HIT CLUB {title}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📡 API: 🔴 Không lấy được dữ liệu."
        )

    session = _hc_session(row)
    result = _hc_result(row)
    prediction = _hc_prediction(row)
    dice = row.get("xuc_xac", row.get("dice", []))
    md5 = _hc_md5(row)

    lines = [
        f"🎯 <b>HIT CLUB — {title}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📡 API: {'🟢 ĐANG BẬT' if running else '🔴 ĐANG TẮT'}",
        f"🎲 Phiên: <b>#{session}</b>",
        f"🎯 Xúc xắc: <b>{dice}</b>",
        f"📌 Kết quả: <b>{result}</b>",
        f"🤖 Dự đoán API: <b>{prediction}</b>",
    ]
    if mode == "md5":
        lines += [
            f"🔐 Mã MD5: <code>{md5}</code>",
        ]
    if row.get("do_tin_cay") is not None:
        lines.append(f"🎯 Độ tin cậy : <b>{row.get('do_tin_cay')}</b>")
    if row.get("pattern") is not None:
        lines.append(f"🧠 Pattern: <b>{row.get('pattern')}</b>")

    if chat_id is not None:
        lines += ["━━━━━━━━━━━━━━━━━━", _hc_stats_text(chat_id, mode)]
    return "\n".join(lines)

def _hc_mode_keyboard(mode):
    title = "MD5" if mode == "md5" else "TÀI XỈU"
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton(f"🟢 BẬT HIT CLUB {title}"),
        tg_types.KeyboardButton(f"🔴 TẮT HIT CLUB {title}"),
    )
    kb.row(
        tg_types.KeyboardButton(f"📊 THỐNG KÊ HIT CLUB {title}"),
        tg_types.KeyboardButton(f"📜 LỊCH SỬ HIT CLUB {title}"),
    )
    kb.row(tg_types.KeyboardButton("↩️ HIT CLUB"))
    return kb

def _hc_loop(chat_id, mode):
    key = (chat_id, mode)
    last = HITCLUB_LAST.get(key)
    while HITCLUB_RUNNING.get(key):
        row = _hc_fetch()
        if row:
            sid = _hc_session(row)
            if sid != last:
                _hc_grade(chat_id, mode, row)
                last = sid
                HITCLUB_LAST[key] = sid
                st = _hc_stats(chat_id, mode)
                st["pending"] = {
                    "session": sid,
                    "prediction": _hc_prediction(row),
                }
                _save_hitclub_stats()
                try:
                    bot.send_message(
                        chat_id,
                        _hc_panel(row, mode, True, chat_id),
                        parse_mode="HTML",
                        reply_markup=_hc_mode_keyboard(mode),
                    )
                except Exception as e:
                    print(f"Lỗi gửi HIT CLUB: {e}")
        time.sleep(3)

def _hc_start(chat_id, mode):
    key = (chat_id, mode)
    if HITCLUB_RUNNING.get(key):
        return
    HITCLUB_RUNNING[key] = True
    threading.Thread(target=_hc_loop, args=(chat_id, mode), daemon=True).start()

def _hc_stop(chat_id, mode):
    HITCLUB_RUNNING[(chat_id, mode)] = False

_load_hitclub_stats()

@bot.message_handler(func=lambda m: m.text == "↩️ HIT CLUB")
def back_hitclub_menu(msg):
    if not _group_admin_only(msg):
        return
    show_keyboard(msg.chat.id, hitclub_keyboard())

for _hc_label, _hc_mode in [
    ("🔐 HIT CLUB MD5", "md5"),
    ("🎲 HIT CLUB TÀI XỈU", "tx"),
]:
    def _make_hc_mode_handler(label=_hc_label, mode=_hc_mode):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            if not _group_admin_only(msg):
                return
            MENU_STATE[msg.chat.id] = f"hitclub_{mode}"
            show_keyboard(msg.chat.id, _hc_mode_keyboard(mode))
        return _handler
    _make_hc_mode_handler()

for _on_label, _mode in [
    ("🟢 BẬT HIT CLUB MD5", "md5"),
    ("🟢 BẬT HIT CLUB TÀI XỈU", "tx"),
]:
    def _make_on(label=_on_label, mode=_mode):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            if not _group_admin_only(msg):
                return
            row = _hc_fetch()
            if not row:
                bot.send_message(msg.chat.id, "⚠️ HIT CLUB API chưa trả dữ liệu.")
                return
            _hc_start(msg.chat.id, mode)
            st = _hc_stats(msg.chat.id, mode)
            st["pending"] = {
                "session": _hc_session(row),
                "prediction": _hc_prediction(row),
            }
            _save_hitclub_stats()
            bot.send_message(
                msg.chat.id,
                _hc_panel(row, mode, True, msg.chat.id),
                parse_mode="HTML",
                reply_markup=_hc_mode_keyboard(mode),
            )
        return _handler
    _make_on()

for _off_label, _mode in [
    ("🔴 TẮT HIT CLUB MD5", "md5"),
    ("🔴 TẮT HIT CLUB TÀI XỈU", "tx"),
]:
    def _make_off(label=_off_label, mode=_mode):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            if not _group_admin_only(msg):
                return
            _hc_stop(msg.chat.id, mode)
            bot.send_message(msg.chat.id, f"🔴 HIT CLUB {mode.upper()}: ĐÃ TẮT.")
        return _handler
    _make_off()

for _stat_label, _mode in [
    ("📊 THỐNG KÊ HIT CLUB MD5", "md5"),
    ("📊 THỐNG KÊ HIT CLUB TÀI XỈU", "tx"),
]:
    def _make_stat(label=_stat_label, mode=_mode):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            if not _group_admin_only(msg):
                return
            bot.send_message(msg.chat.id, _hc_stats_text(msg.chat.id, mode), parse_mode="HTML")
        return _handler
    _make_stat()

for _hist_label, _mode in [
    ("📜 LỊCH SỬ HIT CLUB MD5", "md5"),
    ("📜 LỊCH SỬ HIT CLUB TÀI XỈU", "tx"),
]:
    def _make_hist(label=_hist_label, mode=_mode):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            if not _group_admin_only(msg):
                return
            bot.send_message(msg.chat.id, _hc_stats_text(msg.chat.id, mode), parse_mode="HTML")
        return _handler
    _make_hist()

# ================== MD5 / TXMD5 API ==================
MD5_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/sessions"
MD5_RUNNING = set()
MD5_LAST_SESSION = {}
MD5_STATS_FILE = "md5_stats.json"
MD5_STATS = {}

def _load_md5_stats():
    global MD5_STATS
    try:
        with open(MD5_STATS_FILE, "r", encoding="utf-8") as f:
            MD5_STATS = json.load(f)
    except Exception:
        MD5_STATS = {}

def _save_md5_stats():
    try:
        with open(MD5_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(MD5_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Lỗi lưu thống kê MD5: {e}")

def _md5_stats(chat_id):
    key = str(chat_id)
    if key not in MD5_STATS:
        MD5_STATS[key] = {"correct": 0, "wrong": 0, "history": [], "pending": None}
    return MD5_STATS[key]

def _md5_fetch():
    try:
        r = requests.get(MD5_API_URL, timeout=(5, 12), headers={"Accept": "application/json"})
        r.raise_for_status()
        payload = r.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "sessions", "result", "items", "list", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    for sub in ("sessions", "items", "list", "records", "data"):
                        if isinstance(value.get(sub), list):
                            return value[sub]
        return []
    except Exception as e:
        print(f"Lỗi API MD5: {e}")
        return []

def _md5_get(row, *keys, default="?"):
    for k in keys:
        if isinstance(row, dict) and row.get(k) is not None:
            return row.get(k)
    return default

def _md5_result(row):
    # Schema thực tế của API: resultTruyenThong = TAI/XIU.
    value = _md5_get(
        row,
        "resultTruyenThong",
        "result",
        "resultType",
        "ket_qua",
        "ketQua",
        "outcome",
        "prediction",
        "resultText",
        default=""
    )
    s = str(value).strip().lower()
    if s in {"t", "tai", "tài", "3", "big", "lớn"}:
        return "Tài"
    if s in {"x", "xiu", "xỉu", "4", "small", "nhỏ"}:
        return "Xỉu"

    try:
        n = int(value)
        if 3 <= n <= 10:
            return "Xỉu"
        if 11 <= n <= 18:
            return "Tài"
    except Exception:
        pass
    return "?"

def _md5_session_id(row):
    # Schema thực tế: id là số phiên.
    return str(_md5_get(
        row,
        "id",
        "session",
        "sessionId",
        "session_id",
        "gameNum",
        "gameNo",
        "phien",
        default="?"
    ))

def _md5_code(row):
    # API hiện tại không trả trường "md5" 32 ký tự.
    # Nó trả "_id" dạng 24-hex (Mongo/ObjectId). Hiển thị nó
    # như mã hash của phiên; nếu muốn MD5 thực sự thì tạo MD5
    # cục bộ từ dữ liệu phiên.
    api_hash = _md5_get(row, "_id", "md5", "hash", "md5Hash", default="")
    api_hash = str(api_hash)
    if api_hash:
        return api_hash, False

    canonical = "|".join([
        _md5_session_id(row),
        str(_md5_get(row, "resultTruyenThong", "result", default="")),
        str(_md5_get(row, "point", "score", default="")),
        ",".join(map(str, _md5_get(row, "dices", "facesList", default=[]))) if isinstance(_md5_get(row, "dices", "facesList", default=[]), list) else str(_md5_get(row, "dices", "facesList", default="")),
    ])
    return hashlib.md5(canonical.encode("utf-8")).hexdigest(), True

def _md5_prediction(rows):
    vals=[_md5_result(x) for x in rows[:10]]
    tai=vals.count("Tài"); xiu=vals.count("Xỉu")
    if tai==xiu:
        return vals[0] if vals and vals[0] != "?" else "Chưa có"
    return "Tài" if tai>xiu else "Xỉu"

def _md5_record(chat_id, actual):
    st=_md5_stats(chat_id); pending=st.get("pending")
    if not pending or pending.get("session") == _md5_session_id(actual): return
    pred=pending.get("prediction"); real=_md5_result(actual)
    if pred not in {"Tài","Xỉu"} or real not in {"Tài","Xỉu"}:
        st["pending"]=None; return
    ok=pred==real
    st["correct"] += int(ok); st["wrong"] += int(not ok)
    st["history"].insert(0,{"session":pending.get("session"),"prediction":pred,"actual":real,"status":"ĐÚNG" if ok else "SAI"})
    st["history"]=st["history"][:30]; st["pending"]=None; _save_md5_stats()

def _md5_stats_text(chat_id):
    st=_md5_stats(chat_id); c=int(st.get("correct",0)); w=int(st.get("wrong",0)); total=c+w; rate=c*100/total if total else 0
    lines=["📊 <b>THỐNG KÊ MD5</b>","━━━━━━━━━━━━━━━━━━",f"✅ Đúng: <b>{c}</b>",f"❌ Sai: <b>{w}</b>",f"🎯 Đã đối chiếu: <b>{total}</b>",f"🏆 Tỷ lệ đúng: <b>{rate:.1f}%</b>","━━━━━━━━━━━━━━━━━━","📜 <b>Lịch sử 30 phiên:</b>"]
    if not st.get("history"): lines.append("Chưa có phiên được đối chiếu.")
    else:
        for h in st["history"]:
            icon="✅" if h["status"]=="ĐÚNG" else "❌"
            lines.append(f"{icon} #{h['session']} | Dự: {h['prediction']} | KQ: {h['actual']}")
    return "\n".join(lines)

def _md5_panel(rows, running, chat_id=None):
    if not rows:
        return "🔐 <b>MD5</b>\n━━━━━━━━━━━━━━━━━━\n📡 API: 🔴 Không lấy được dữ liệu."

    cur = rows[0]
    recent = rows[:10]
    pred = _md5_prediction(recent)
    hash_code, locally_generated = _md5_code(cur)
    dices = _md5_get(cur, "dices", "facesList", default=[])
    point = _md5_get(cur, "point", "score", default="?")

    lines = [
        "🔐 <b>MD5</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📡 API: {'🟢 ĐANG BẬT' if running else '🔴 ĐANG TẮT'}",
        f"🎲 Phiên: <b>{_md5_session_id(cur)}</b>",
        f"🔑 Mã hash: <code>{hash_code}</code>",
        (
            "🧮 MD5 cục bộ: <code>"
            + hashlib.md5(
                f"{_md5_session_id(cur)}|{_md5_result(cur)}|{point}|{dices}".encode("utf-8")
            ).hexdigest()
            + "</code>"
        ),
        f"🎯 Xúc xắc: <b>{dices}</b> | Tổng: <b>{point}</b>",
        f"📌 Kết quả: <b>{_md5_result(cur)}</b>",
        f"🤖 Dự đoán : <b>{pred}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📜 <b>10 phiên gần nhất:</b>",
    ]

    for x in recent:
        code, _ = _md5_code(x)
        lines.append(
            f"#{_md5_session_id(x)} | {code} | KQ: {_md5_result(x)}"
        )

    if chat_id is not None:
        lines += ["━━━━━━━━━━━━━━━━━━", _md5_stats_text(chat_id)]

    return "\n".join(lines)

def _md5_loop(chat_id):
    last=MD5_LAST_SESSION.get(chat_id)
    while chat_id in MD5_RUNNING:
        rows=_md5_fetch()
        if rows:
            sid=_md5_session_id(rows[0])
            if sid != last:
                _md5_record(chat_id, rows[0])
                last=sid; MD5_LAST_SESSION[chat_id]=sid
                st=_md5_stats(chat_id); st["pending"]={"session":sid,"prediction":_md5_prediction(rows)}; _save_md5_stats()
                try: bot.send_message(chat_id,_md5_panel(rows,True,chat_id),parse_mode="HTML")
                except Exception as e: print(f"Lỗi gửi MD5: {e}")
        time.sleep(3)

def _start_md5(chat_id):
    if chat_id in MD5_RUNNING: return False
    MD5_RUNNING.add(chat_id)
    threading.Thread(target=_md5_loop,args=(chat_id,),daemon=True).start()
    return True

_load_md5_stats()

@bot.message_handler(func=lambda m: m.text == "🟢 BẬT MD5")
def on_md5(msg):
    if not _group_admin_only(msg): return
    rows=_md5_fetch()
    if not rows:
        bot.send_message(msg.chat.id,"🔐 MD5\n\n📡 API: 🔴 Không lấy được dữ liệu.")
        return
    _start_md5(msg.chat.id)
    st=_md5_stats(msg.chat.id); sid=_md5_session_id(rows[0]); st["pending"]={"session":sid,"prediction":_md5_prediction(rows)}; _save_md5_stats()
    bot.send_message(msg.chat.id,_md5_panel(rows,True,msg.chat.id),parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "🔴 TẮT MD5")
def off_md5(msg):
    if not _group_admin_only(msg): return
    MD5_RUNNING.discard(msg.chat.id); bot.send_message(msg.chat.id,"🔴 MD5: ĐÃ TẮT API.")

@bot.message_handler(func=lambda m: m.text == "📊 THỐNG KÊ MD5")
def md5_stats_button(msg):
    if not _group_admin_only(msg): return
    bot.send_message(msg.chat.id,_md5_stats_text(msg.chat.id),parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📜 LỊCH SỬ MD5")
def md5_history_button(msg):
    if not _group_admin_only(msg):
        return

    st = _md5_stats(msg.chat.id)
    history = st.get("history", [])
    if not history:
        bot.send_message(msg.chat.id, "📜 Chưa có lịch sử đúng/sai MD5.")
        return

    lines = [
        "📜 <b>LỊCH SỬ ĐÚNG/SAI MD5</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for h in history[:30]:
        icon = "✅" if h.get("status") == "ĐÚNG" else "❌"
        lines.append(
            f"{icon} #{h.get('session','?')} | "
            f"Dự: {h.get('prediction','?')} | KQ: {h.get('actual','?')}"
        )
    bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="HTML")


# ================== SUN NHANH API ==================
SUN_NHANH_API_URL = (
    "https://api.wsktnus8.net/v2/history/getLastResult"
    "?gameId=ktrng_3979&size=100&tableId=39791215743193&curPage=1"
)
SUN_NHANH_RUNNING = set()
SUN_NHANH_LAST_GAME = {}
SUN_NHANH_STATS_FILE = "sun_nhanh_stats.json"
SUN_NHANH_STATS = {}

def _load_sun_nhanh_stats():
    global SUN_NHANH_STATS
    try:
        with open(SUN_NHANH_STATS_FILE, "r", encoding="utf-8") as f:
            SUN_NHANH_STATS = json.load(f)
    except Exception:
        SUN_NHANH_STATS = {}

def _save_sun_nhanh_stats():
    try:
        with open(SUN_NHANH_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(SUN_NHANH_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Lỗi lưu thống kê SUN NHANH: {e}")

def _sun_nhanh_stats(chat_id):
    key = str(chat_id)
    if key not in SUN_NHANH_STATS:
        SUN_NHANH_STATS[key] = {
            "correct": 0,
            "wrong": 0,
            "history": [],
            "pending": None,
        }
    return SUN_NHANH_STATS[key]

def _sun_nhanh_make_prediction(rows):
    # Dự đoán tham khảo dựa trên tần suất 10 phiên gần nhất.
    # Không coi đây là kết quả chắc chắn.
    recent = rows[:10]
    tai = sum(1 for x in recent if x.get("resultType") == 3)
    xiu = sum(1 for x in recent if x.get("resultType") == 4)
    if tai == xiu:
        # Khi hòa, dùng kết quả gần nhất làm tham khảo.
        return _sun_nhanh_label(recent[0].get("resultType")) if recent else "Chưa có"
    return "Tài" if tai > xiu else "Xỉu"

def _sun_nhanh_record_result(chat_id, actual):
    st = _sun_nhanh_stats(chat_id)
    pending = st.get("pending")
    if not pending or pending.get("game") == actual.get("gameNum"):
        return

    predicted = pending.get("prediction")
    actual_label = _sun_nhanh_label(actual.get("resultType"))
    if predicted not in {"Tài", "Xỉu"} or actual_label not in {"Tài", "Xỉu"}:
        st["pending"] = None
        return

    ok = predicted == actual_label
    if ok:
        st["correct"] += 1
    else:
        st["wrong"] += 1

    st["history"].insert(0, {
        "game": pending.get("game"),
        "prediction": predicted,
        "actual": actual_label,
        "status": "ĐÚNG" if ok else "SAI",
    })
    st["history"] = st["history"][:30]
    st["pending"] = None
    _save_sun_nhanh_stats()

def _sun_nhanh_stats_text(chat_id):
    st = _sun_nhanh_stats(chat_id)
    correct = int(st.get("correct", 0))
    wrong = int(st.get("wrong", 0))
    total = correct + wrong
    rate = (correct / total * 100) if total else 0.0
    lines = [
        "📊 <b>THỐNG KÊ SUN NHANH</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"✅ Đúng: <b>{correct}</b>",
        f"❌ Sai: <b>{wrong}</b>",
        f"🎯 Tổng đã đối chiếu: <b>{total}</b>",
        f"🏆 Tỷ lệ đúng: <b>{rate:.1f}%</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📜 <b>30 lần gần nhất:</b>",
    ]
    if not st.get("history"):
        lines.append("Chưa có phiên nào được đối chiếu.")
    else:
        for h in st["history"]:
            icon = "✅" if h["status"] == "ĐÚNG" else "❌"
            lines.append(
                f"{icon} #{h['game']} | Dự: {h['prediction']} | KQ: {h['actual']}"
            )
    return "\n".join(lines)

_load_sun_nhanh_stats()

def _sun_nhanh_fetch():
    try:
        r = requests.get(SUN_NHANH_API_URL, timeout=(5, 12))
        r.raise_for_status()
        payload = r.json()
        return payload.get("data", {}).get("resultList", []) or []
    except Exception as e:
        print(f"Lỗi API SUN NHANH: {e}")
        return []

def _sun_nhanh_label(result_type):
    # Theo dữ liệu endpoint: resultType=3 tương ứng tổng Tài,
    # resultType=4 tương ứng tổng Xỉu trong các bản ghi trả về.
    return {3: "Tài", 4: "Xỉu"}.get(result_type, f"Type {result_type}")

def _sun_nhanh_panel(rows, running):
    if not rows:
        return (
            "🌙 <b>SUN NHANH</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📡 API: 🔴 Không lấy được dữ liệu."
        )

    cur = rows[0]
    recent = rows[:10]
    tai = sum(1 for x in recent if x.get("resultType") == 3)
    xiu = sum(1 for x in recent if x.get("resultType") == 4)
    prediction = _sun_nhanh_make_prediction(rows)
    # chat_id không truyền vào panel ở các call cũ; thống kê được hiển thị
    # qua lệnh/nút riêng.

    lines = [
        "🌙 <b>SUN NHANH</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📡 API: {'🟢 ĐANG BẬT' if running else '🔴 ĐANG TẮT'}",
        f"🎲 Phiên hiện tại: <b>{cur.get('gameNum', '?')}</b>",
        f"🎯 Xúc xắc: <b>{'-'.join(map(str, cur.get('facesList', [])))}</b>",
        f"🔢 Tổng: <b>{cur.get('score', '?')}</b>",
        f"📌 Kết quả: <b>{_sun_nhanh_label(cur.get('resultType'))}</b>",
        f"🤖 Dự đoán : <b>{prediction}</b>",
        f"📊 10 phiên: Tài <b>{tai}</b> | Xỉu <b>{xiu}</b>",
        "━━━━━━━━━━━━━━━━━━",
        "📜 <b>Lịch sử gần nhất:</b>",
    ]

    for x in recent:
        faces = "-".join(map(str, x.get("facesList", [])))
        lines.append(
            f"{x.get('gameNum', '?')} | {faces} | "
            f"{x.get('score', '?')} | {_sun_nhanh_label(x.get('resultType'))}"
        )

    return "\n".join(lines)

def _sun_nhanh_loop(chat_id):
    last_game = None
    while chat_id in SUN_NHANH_RUNNING:
        rows = _sun_nhanh_fetch()
        if rows:
            game = str(rows[0].get("gameNum", ""))
            if game and game != last_game:
                # Chấm dự đoán của phiên trước bằng kết quả mới.
                _sun_nhanh_record_result(chat_id, rows[0])

                last_game = game
                SUN_NHANH_LAST_GAME[chat_id] = game

                # Dự đoán cho phiên kế tiếp được lưu để lần cập nhật sau chấm.
                prediction = _sun_nhanh_make_prediction(rows)
                st = _sun_nhanh_stats(chat_id)
                st["pending"] = {
                    "game": game,
                    "prediction": prediction,
                }
                _save_sun_nhanh_stats()

                try:
                    bot.send_message(
                        chat_id,
                        _sun_nhanh_panel(rows, True) +
                        "\n\n" + _sun_nhanh_stats_text(chat_id),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    print(f"Lỗi gửi SUN NHANH: {e}")
        time.sleep(3)

@bot.message_handler(func=lambda m: m.text == "📊 THỐNG KÊ SUN NHANH")
def sun_nhanh_stats_button(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(
        msg.chat.id,
        _sun_nhanh_stats_text(msg.chat.id),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: m.text == "📜 LỊCH SỬ SUN NHANH")
def sun_nhanh_history_button(msg):
    if not _group_admin_only(msg):
        return
    st = _sun_nhanh_stats(msg.chat.id)
    history = st.get("history", [])
    if not history:
        bot.send_message(msg.chat.id, "📜 Chưa có lịch sử đúng/sai.")
        return
    lines = ["📜 <b>LỊCH SỬ ĐÚNG/SAI SUN NHANH</b>", "━━━━━━━━━━━━━━━━━━"]
    for h in history[:30]:
        icon = "✅" if h["status"] == "ĐÚNG" else "❌"
        lines.append(f"{icon} #{h['game']} | Dự: {h['prediction']} | KQ: {h['actual']}")
    bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="HTML")


def _start_sun_nhanh(chat_id):
    if chat_id in SUN_NHANH_RUNNING:
        return False
    SUN_NHANH_RUNNING.add(chat_id)
    threading.Thread(
        target=_sun_nhanh_loop,
        args=(chat_id,),
        daemon=True
    ).start()
    return True

# ================== MENU BCR ==================
def bcr_keyboard():
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    for row in [
        ("🎰 BCR FLY88 VIP", "🎯 BCR F168"),
        ("💎 BCR CM88", "🔥 BCR XX88"),
        ("⚡ BCR SC88",),
        ("↩️ Quay lại Menu Game",),
    ]:
        kb.row(*(tg_types.KeyboardButton(x) for x in row))
    return kb


def bcr_table_keyboard(game_name, tables=None):
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    tables = tables or [str(i) for i in range(1, 11)]
    buttons = [tg_types.KeyboardButton(f"🎯 {game_name} - BÀN {t}") for t in tables]
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Menu BCR"))
    return kb


def bcr_table_control_keyboard(game_name, table_no, running=False):
    kb = tg_types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        tg_types.KeyboardButton("🟢 BẬT BCR"),
        tg_types.KeyboardButton("🔴 TẮT BCR")
    )
    kb.row(tg_types.KeyboardButton("🔄 CẬP NHẬT BÀN"))
    kb.row(tg_types.KeyboardButton("📊 THỐNG KÊ BCR"))
    kb.row(tg_types.KeyboardButton("📜 LỊCH SỬ ĐÚNG SAI"))
    kb.row(tg_types.KeyboardButton("↩️ Quay lại Danh Sách Bàn"))
    return kb


def show_bcr_keyboard(chat_id):
    MENU_STATE[chat_id] = "bcr"
    return show_keyboard(chat_id, bcr_keyboard())


def _bcr_fetch():
    try:
        r = requests.get(BCR_API_URL, timeout=(5, 12))
        r.raise_for_status()
        data = r.json()
        return data.get("data", []) if data.get("success") else []
    except Exception as e:
        print(f"Lỗi BCR API: {e}")
        return []


def _bcr_find_table(table_no):
    table_no = str(table_no)
    return next((x for x in _bcr_fetch() if str(x.get("table")) == table_no), None)


def _bcr_panel_text(game_name, table_no, row, running, chat_id=None):
    if not row:
        return (
            f"🃏 <b>BCR {game_name}</b>\n"
            f"🎯 Bàn: <b>{table_no}</b>\n\n"
            "⚠️ Bàn này hiện không có trong API."
        )

    result = str(row.get("result") or "")
    latest = result[-1:] or "?"
    last20 = result[-20:] if result else "—"

    return (
        f"🃏 <b>BCR {game_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Bàn:</b> {table_no}\n"
        f"📡 <b>Trạng thái API:</b> {'🟢 BẬT' if running else '🔴 TẮT'}\n"
        f"🎲 <b>Kết quả mới nhất:</b> {latest}\n"
        f"📊 <b>Số ván API:</b> {len(result)}\n"
        f"📈 <b>20 ván gần nhất:</b> {last20}\n"
        f"⏱ <b>Cập nhật:</b> {datetime.now().strftime('%H:%M:%S')}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + (_bcr_stats_text(chat_id, game_name, table_no) if chat_id is not None else "📊 Thống kê: chưa khởi tạo")
    )


def show_bcr_tables(chat_id, game_name):
    rows = _bcr_fetch()
    tables = [str(x.get("table")) for x in rows if x.get("table") is not None]
    if not tables:
        tables = [str(i) for i in range(1, 11)]

    MENU_STATE[chat_id] = f"bcr_{game_name}"
    return bot.send_message(
        chat_id,
        f"🃏 <b>BCR {game_name}</b>\n\n"
        f"📡 API: {'🟢 Kết nối' if rows else '🔴 Không lấy được dữ liệu'}\n"
        f"🎯 Số bàn: <b>{len(tables)}</b>\n\n"
        "👇 Chọn bàn. Chỉ sau khi chọn bàn mới có nút BẬT/TẮT.",
        parse_mode="HTML",
        reply_markup=bcr_table_keyboard(game_name, tables)
    )


def _bcr_stop(chat_id):
    with BCR_LOCK:
        return BCR_RUNNING.pop(chat_id, None) is not None


def _bcr_start(chat_id, game_name, table_no):
    with BCR_LOCK:
        old = BCR_RUNNING.get(chat_id)
        if old and old["game"] == game_name and old["table"] == str(table_no):
            return False

        BCR_RUNNING[chat_id] = {"game": game_name, "table": str(table_no)}

        threading.Thread(
            target=_bcr_loop,
            args=(chat_id, game_name, str(table_no)),
            daemon=True
        ).start()
        return True


def _bcr_loop(chat_id, game_name, table_no):
    last_result = None
    while True:
        with BCR_LOCK:
            state = BCR_RUNNING.get(chat_id)
            if not state or state["game"] != game_name or state["table"] != str(table_no):
                return

        row = _bcr_find_table(table_no)
        result = str(row.get("result") or "") if row else ""

        if result and result != last_result:
            last_result = result
            round_id = row.get("round") if row else None
            _bcr_score_and_predict(chat_id, game_name, table_no, result, round_id)
            try:
                bot.send_message(
                    chat_id,
                    _bcr_panel_text(game_name, table_no, row, True, chat_id),
                    parse_mode="HTML",
                    reply_markup=bcr_table_control_keyboard(game_name, table_no, True)
                )
            except Exception as e:
                print(f"Lỗi gửi BCR: {e}")

        time.sleep(3)


def send_bcr_table_panel(chat_id, game_name, table_no, running=False):
    row = _bcr_find_table(table_no)
    return bot.send_message(
        chat_id,
        _bcr_panel_text(game_name, table_no, row, running, chat_id),
        parse_mode="HTML",
        reply_markup=bcr_table_control_keyboard(game_name, table_no, running)
    )

@bot.message_handler(func=lambda m: m.text == "🎰 GAME BCR")
def game_bcr_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    if uid != OWNER_ID and not check_key(uid):
        bot.send_message(msg.chat.id, "🔑 Key chưa được kích hoạt.")
        return
    show_bcr_keyboard(msg.chat.id)


_BCR_GAMES = {
    "🎰 BCR FLY88 VIP": "FLY88 VIP",
    "🎯 BCR F168": "F168",
    "💎 BCR CM88": "CM88",
    "🔥 BCR XX88": "XX88",
    "⚡ BCR SC88": "SC88",
}

for _label, _game_name in _BCR_GAMES.items():
    def _make_bcr_handler(label=_label, game_name=_game_name):
        @bot.message_handler(func=lambda m, label=label: m.text == label)
        def _handler(msg):
            uid = msg.from_user.id
            if uid in kicked_users:
                bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
                return
            if uid != OWNER_ID and not check_key(uid):
                bot.send_message(msg.chat.id, "🔑 Key chưa được kích hoạt.")
                return
            show_bcr_tables(msg.chat.id, game_name)
        return _handler
    _make_bcr_handler()


@bot.message_handler(func=lambda m: bool(m.text) and m.text.startswith("↩️ Quay lại Menu BCR"))
def back_bcr_menu(msg):
    if not _group_admin_only(msg):
        return
    show_bcr_keyboard(msg.chat.id)


@bot.message_handler(
    func=lambda m: bool(m.text)
    and " - BÀN " in m.text
    and (
        m.text.startswith("🎯 BCR ")
        or m.text.startswith("🎯 ")
    )
)
def bcr_table_button(msg):
    if not _group_admin_only(msg):
        return

    try:
        left, table_no = msg.text.rsplit(" - BÀN ", 1)

        # Telegram đang gửi nút dạng:
        # "🎯 F168 - BÀN 1"
        # nhưng vẫn hỗ trợ dạng cũ:
        # "🎯 BCR F168 - BÀN 1"
        game_name = left.replace("🎯 BCR ", "", 1)
        game_name = game_name.replace("🎯 ", "", 1).strip()
        table_no = table_no.strip()

        if game_name not in {"FLY88 VIP", "F168", "CM88", "XX88", "SC88"}:
            raise ValueError("web BCR không hợp lệ")

        row = _bcr_find_table(table_no)
        if row is None:
            bot.send_message(
                msg.chat.id,
                f"⚠️ Bàn <b>{table_no}</b> không có trong API hiện tại.",
                parse_mode="HTML"
            )
            return

    except Exception as e:
        print(f"Lỗi chọn bàn BCR: {e}")
        bot.send_message(msg.chat.id, "⚠️ Không nhận diện được bàn BCR.")
        return

    # Đã chọn bàn thành công -> dừng bàn cũ, lưu bàn mới
    # và hiện riêng màn hình ON/OFF.
    _bcr_stop(msg.chat.id)
    MENU_STATE[msg.chat.id] = f"bcr_table_{game_name}_{table_no}"

    try:
        send_bcr_table_panel(
            msg.chat.id,
            game_name,
            table_no,
            False
        )
    except requests.exceptions.RequestException as e:
        print(f"Lỗi Telegram khi mở bàn BCR: {e}")
        return
    except Exception as e:
        print(f"Lỗi hiển thị bàn BCR: {e}")
        return


@bot.message_handler(func=lambda m: m.text == "🟢 BẬT BCR")
def bcr_on_button(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    if not state.startswith("bcr_table_"):
        return
    try:
        _, _, game_name, table_no = state.split("_", 3)
    except Exception:
        return

    _bcr_start(msg.chat.id, game_name, table_no)
    bot.send_message(
        msg.chat.id,
        f"🟢 <b>BCR ĐÃ BẬT</b>\n🎯 Bàn: {table_no}\n📡 Đang lấy API tự động.",
        parse_mode="HTML",
        reply_markup=bcr_table_control_keyboard(game_name, table_no, True)
    )


@bot.message_handler(func=lambda m: m.text == "🔴 TẮT BCR")
def bcr_off_button(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    if not state.startswith("bcr_table_"):
        return
    try:
        _, _, game_name, table_no = state.split("_", 3)
    except Exception:
        return

    _bcr_stop(msg.chat.id)
    send_bcr_table_panel(msg.chat.id, game_name, table_no, False)


@bot.message_handler(func=lambda m: m.text == "🔄 CẬP NHẬT BÀN")
def bcr_refresh_button(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    if not state.startswith("bcr_table_"):
        return
    try:
        _, _, game_name, table_no = state.split("_", 3)
    except Exception:
        return
    running = bool(BCR_RUNNING.get(msg.chat.id))
    send_bcr_table_panel(msg.chat.id, game_name, table_no, running)


@bot.message_handler(func=lambda m: m.text == "📊 THỐNG KÊ BCR")
def bcr_stats_button(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    if not state.startswith("bcr_table_"):
        return
    try:
        _, _, game_name, table_no = state.split("_", 3)
    except Exception:
        return
    bot.send_message(
        msg.chat.id,
        _bcr_stats_text(msg.chat.id, game_name, table_no),
        parse_mode="HTML",
        reply_markup=bcr_table_control_keyboard(
            game_name, table_no, bool(BCR_RUNNING.get(msg.chat.id))
        )
    )


@bot.message_handler(func=lambda m: m.text == "📜 LỊCH SỬ ĐÚNG SAI")
def bcr_history_button(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    if not state.startswith("bcr_table_"):
        return
    try:
        _, _, game_name, table_no = state.split("_", 3)
    except Exception:
        return
    st = _bcr_get_stats(msg.chat.id, game_name, table_no)
    history = st.get("history", [])[-30:]
    if not history:
        text = "📜 <b>LỊCH SỬ ĐÚNG SAI</b>\n\nChưa có phiên nào được đối chiếu."
    else:
        dung = int(st.get("dung", 0))
        sai = int(st.get("sai", 0))
        tong = dung + sai
        rate = round(dung / tong * 100, 2) if tong else 0
        lines = [
            f"📜 <b>LỊCH SỬ ĐÚNG SAI — {game_name} BÀN {table_no}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"🏆 Tỷ lệ đúng: <b>{rate}%</b> ({dung}/{tong})",
            ""
        ]
        for item in reversed(history):
            mark = "✅" if item.get("ok") else "❌"
            lines.append(
                f"#{item.get('round','?')} | {_bcr_label(item.get('prediction'))} "
                f"→ {_bcr_label(item.get('actual'))} {mark}"
            )
        text = "\n".join(lines)
    bot.send_message(
        msg.chat.id, text, parse_mode="HTML",
        reply_markup=bcr_table_control_keyboard(
            game_name, table_no, bool(BCR_RUNNING.get(msg.chat.id))
        )
    )


@bot.message_handler(func=lambda m: m.text == "↩️ Quay lại Danh Sách Bàn")
def bcr_back_tables(msg):
    if not _group_admin_only(msg):
        return
    state = MENU_STATE.get(msg.chat.id, "")
    try:
        _, _, game_name, _ = state.split("_", 3)
    except Exception:
        game_name = "F168"
    _bcr_stop(msg.chat.id)
    show_bcr_tables(msg.chat.id, game_name)

@bot.message_handler(func=lambda m: m.text == "☀️ SUN THƯỜNG")
def sun_thuong_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return

    # Trong group/supergroup: CHỈ admin/creator được gọi bot.
    if chat_id < 0 and not is_group_admin(chat_id, uid):
        return

    # Chat riêng: user vẫn dùng cơ chế key như trước.
    if chat_id >= 0 and uid != OWNER_ID and not check_key(uid):
        bot.send_message(chat_id, "🔑 Key chưa được kích hoạt.")
        return

    MENU_STATE[chat_id] = "sun_thuong_control"
    show_keyboard(chat_id, sun_mode_keyboard("SUN THƯỜNG"))


@bot.message_handler(func=lambda m: m.text == "🌙 SUN NHANH")
def sun_nhanh_button(msg):
    if not _group_admin_only(msg):
        return
    MENU_STATE[msg.chat.id] = "sun_nhanh_control"
    show_keyboard(msg.chat.id, sun_mode_keyboard("SUN NHANH"))


@bot.message_handler(func=lambda m: m.text == "⚡ SUN SIÊU TỐC")
def sun_sieutoc_button(msg):
    if not _group_admin_only(msg):
        return
    MENU_STATE[msg.chat.id] = "sun_sieutoc_control"
    show_keyboard(msg.chat.id, sun_mode_keyboard("SUN SIÊU TỐC"))


@bot.message_handler(func=lambda m: m.text == "🎯 SUN VIP")
def sun_vip_button(msg):
    if not _group_admin_only(msg):
        return
    MENU_STATE[msg.chat.id] = "sun_vip_control"
    show_keyboard(msg.chat.id, sun_mode_keyboard("SUN VIP"))


sun_running_chats = set()  # SUN THƯỜNG đang chạy theo từng chat/group

def _start_sun_thuong(uid, chat_id):
    """
    Khởi động SUN THƯỜNG theo từng chat.
    Quan trọng: trạng thái chạy dùng chat_id, không dùng uid,
    vì một user có thể bật bot trong nhóm và đồng thời ở chat riêng.
    """
    if chat_id in sun_running_chats:
        return

    data = user_data.setdefault(chat_id, {
        "last_phien": 0,
        "lich_su_kq": [],
        "lich_su_phan_hoi": [],
        "dem_sai": 0,
        "pattern_sai": set(),
        "so_dung": 0,
        "so_sai": 0,
        "lich_su_diem": [],
        "du_doan_truoc": None,
        "do_tin_cay_truoc": None,
        "phien_truoc": 0,
        "da_be_tai": False,
        "da_be_xiu": False,
        "pattern_memory": {},
        "error_memory": {},
        "last_scored_phien": 0,
        "sunwin_last_panel_phien": None,
    })

    # Cho phép gửi ngay phiên hiện tại vào đúng nhóm.
    try:
        rows = get_sunwin_history(50)
        if rows:
            current_phien = rows[0]["phien"]
            data["sunwin_last_panel_phien"] = current_phien - 1
    except Exception as e:
        print(f"Lỗi khởi tạo SUN THƯỜNG chat {chat_id}: {e}")

    sun_running_chats.add(chat_id)

    threading.Thread(
        target=auto_loop,
        args=(uid, chat_id),
        daemon=True
    ).start()


@bot.message_handler(func=lambda m: m.text == "🟢 BẬT SUN THƯỜNG")
def on_sun_thuong(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    # Trong group/supergroup: CHỈ admin/creator được bật bot.
    if chat_id < 0 and not is_group_admin(chat_id, uid):
        return

    # Chat riêng: user vẫn phải có key (Admin/OWNER không cần key).
    if chat_id >= 0 and uid != OWNER_ID and not check_key(uid):
        bot.send_message(chat_id, "🔑 Key chưa được kích hoạt.")
        return

    _start_sun_thuong(uid, chat_id)
    bot.send_message(chat_id, "🟢 SUN THƯỜNG: ĐÃ BẬT.")


@bot.message_handler(func=lambda m: m.text == "🔴 TẮT SUN THƯỜNG")
def off_sun_thuong(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    chat_id = msg.chat.id

    if chat_id < 0 and not is_group_admin(chat_id, uid):
        return

    sun_running_chats.discard(chat_id)
    bot.send_message(chat_id, "🔴 SUN THƯỜNG: ĐÃ TẮT.")


@bot.message_handler(func=lambda m: m.text == "🟢 BẬT SUN NHANH")
def on_sun_nhanh(msg):
    if not _group_admin_only(msg):
        return

    rows = _sun_nhanh_fetch()
    if not rows:
        bot.send_message(
            msg.chat.id,
            "🌙 SUN NHANH\n\n📡 API: 🔴 Không lấy được dữ liệu."
        )
        return

    started = _start_sun_nhanh(msg.chat.id)

    # Tạo dự đoán tham khảo cho phiên hiện tại; phiên mới tiếp theo
    # sẽ được dùng để chấm dự đoán này.
    st = _sun_nhanh_stats(msg.chat.id)
    current_game = str(rows[0].get("gameNum", ""))
    st["pending"] = {
        "game": current_game,
        "prediction": _sun_nhanh_make_prediction(rows),
    }
    _save_sun_nhanh_stats()

    bot.send_message(
        msg.chat.id,
        _sun_nhanh_panel(rows, True) +
        "\n\n" + _sun_nhanh_stats_text(msg.chat.id),
        parse_mode="HTML",
    )


@bot.message_handler(func=lambda m: m.text == "🔴 TẮT SUN NHANH")
def off_sun_nhanh(msg):
    if not _group_admin_only(msg):
        return

    SUN_NHANH_RUNNING.discard(msg.chat.id)
    bot.send_message(msg.chat.id, "🔴 SUN NHANH: ĐÃ TẮT API.")


@bot.message_handler(func=lambda m: m.text == "🟢 BẬT SUN SIÊU TỐC")
def on_sun_sieutoc(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "🟢 SUN SIÊU TỐC: ĐÃ BẬT.")


@bot.message_handler(func=lambda m: m.text == "🔴 TẮT SUN SIÊU TỐC")
def off_sun_sieutoc(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "🔴 SUN SIÊU TỐC: ĐÃ TẮT.")


@bot.message_handler(func=lambda m: m.text == "🟢 BẬT SUN VIP")
def on_sun_vip(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "🟢 SUN VIP: ĐÃ BẬT.")


@bot.message_handler(func=lambda m: m.text == "🔴 TẮT SUN VIP")
def off_sun_vip(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "🔴 SUN VIP: ĐÃ TẮT.")


@bot.message_handler(func=lambda m: m.text == "↩️ Quay lại Menu Sunwin")
def back_sunwin_menu_button(msg):
    if not _group_admin_only(msg):
        return
    show_sunwin_keyboard(msg.chat.id)


@bot.message_handler(commands=['sudungkey'])
def use_key_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return
    PENDING_KEY_USERS.add(uid)
    bot.send_message(msg.chat.id, "🔑 Nhập key của bạn để sử dụng:")


@bot.message_handler(func=lambda m: m.text == "🆘 Hỗ Trợ")
def support_button(msg):
    if not _group_admin_only(msg):
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("👤 Liên hệ Admin", url=f"tg://user?id={OWNER_ID}"))
    bot.send_message(
        msg.chat.id,
        "🆘 <b>HỖ TRỢ ZYNEX AI</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👤 Nhấn nút bên dưới để mở trang cá nhân Admin và liên hệ trực tiếp.",
        parse_mode="HTML",
        reply_markup=kb
    )


@bot.message_handler(func=lambda m: m.text == "🔑 Sử Dụng Key")
def use_key_button_text(msg):
    if not _group_admin_only(msg):
        return
    use_key_button(msg)


@bot.message_handler(func=lambda m: m.text == "🚀 Chạy Tool")
def run_tool_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    if uid in kicked_users:
        bot.reply_to(msg, "🚫 Bạn đã bị chặn!")
        return

    # Tất cả tài khoản, kể cả OWNER, đều phải có Key hợp lệ để chạy Tool.
    # OWNER chỉ có quyền quản trị bot; không được miễn kiểm tra Key.
    if check_key(uid):
        show_game_keyboard(msg.chat.id)
        return

    # Không có key hợp lệ / key đã hết hạn: không cho vào menu game.
    # Hiện thẳng menu mua key để khách có thể chọn gói và thanh toán.
    PENDING_KEY_USERS.discard(uid)
    bot.send_message(
        msg.chat.id,
        "🔐 <b>XÁC NHẬN KEY</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "❌ Key của bạn chưa được kích hoạt hoặc đã hết hạn.\n\n"
        "🛒 Vui lòng mua Key để tiếp tục sử dụng Tool.\n"
        "👇 Chọn <b>Bảng Giá Key</b> để mua:",
        parse_mode="HTML",
        reply_markup=key_main_keyboard()
    )


# ===================== MUA KEY 3 BƯỚC =====================

def key_main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.Inlinetg_types.KeyboardButton("💰 Bảng Giá Key", callback_data="key_prices"))
    kb.add(types.Inlinetg_types.KeyboardButton("↩️ Quay lại Menu chính", callback_data="key_back_main"))
    return kb


def key_prices_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.Inlinetg_types.KeyboardButton("1️⃣ Key 3 Ngày — 50k", callback_data="buy_3ngay"))
    kb.add(types.Inlinetg_types.KeyboardButton("2️⃣ Key 1 Tuần — 80k", callback_data="buy_1tuan"))
    kb.add(types.Inlinetg_types.KeyboardButton("3️⃣ Key 1 Tháng — 150k", callback_data="buy_1thang"))
    kb.add(types.Inlinetg_types.KeyboardButton("4️⃣ Key Vĩnh Viễn — 200k", callback_data="buy_vip"))
    kb.add(types.Inlinetg_types.KeyboardButton("↩️ Quay lại Menu Key", callback_data="key_menu"))
    return kb


def payment_keyboard(order_code):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.Inlinetg_types.KeyboardButton("🔄 Tôi đã chuyển khoản", callback_data=f"paid_{order_code}"))
    kb.add(types.Inlinetg_types.KeyboardButton("↩️ Quay lại chọn loại Key", callback_data="key_prices"))
    return kb


@bot.message_handler(func=lambda m: m.text == "💰 Mua Key")
def buy_key_button(msg):
    if not _group_admin_only(msg):
        return

    bot.send_message(
        msg.chat.id,
        "💰 <b>MENU KEY TOOL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔑 Tại đây bạn có thể mua và kích hoạt Key.\n"
        "📌 Vui lòng chọn chức năng bên dưới.",
        parse_mode="HTML",
        reply_markup=key_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == "key_menu")
def key_menu_callback(call):
    if not _group_admin_only(call.message):
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "💰 <b>MENU KEY TOOL</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🔑 Chọn <b>Bảng Giá Key</b> để xem các gói.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=key_main_keyboard()
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data == "key_prices")
def key_prices_callback(call):
    if not _group_admin_only(call.message):
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "🛒 <b>BẢNG GIÁ KEY TOOL</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ KEY 3 NGÀY — <b>50.000đ</b>\n"
            "2️⃣ KEY 1 TUẦN — <b>80.000đ</b>\n"
            "3️⃣ KEY 1 THÁNG — <b>150.000đ</b>\n"
            "4️⃣ KEY VĨNH VIỄN — <b>200.000đ</b>\n\n"
            "👇 Chọn gói bạn muốn mua:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=key_prices_keyboard()
        )
    except Exception:
        pass


def _send_payment_for_package(chat_id, user_id, key_type, display_name, price):
    stk = "1038854327"
    bank = "VIETCOM BANK"
    receiver = "TRAN DINH LUC"
    order_code = make_order_code()

    # Lưu đơn trước khi hiển thị thông tin thanh toán.
    save_new_order(user_id, key_type, order_code)

    # VietQR: ngân hàng + STK + số tiền + nội dung đơn.
    amount_map = {
        "3ngay": 50000,
        "1tuan": 80000,
        "1thang": 150000,
        "vinhvien": 200000,
    }
    amount = amount_map.get(key_type, 0)
    qr_url = (
        "https://img.vietqr.io/image/"
        f"VCB-{stk}-compact2.png"
        f"?amount={amount}"
        f"&addInfo={order_code}"
        f"&accountName={receiver.replace(' ', '%20')}"
    )

    caption = (
        "🧾 <b>THÔNG TIN THANH TOÁN</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 <b>Loại Key:</b> {display_name}\n"
        f"💰 <b>Giá:</b> {price}\n"
        f"⏳ <b>Thời hạn:</b> {display_name}\n"
        "👤 <b>Số lượng:</b> 1\n\n"
        "📌 Vui lòng chuyển khoản đúng số tiền và đúng nội dung.\n"
        "👇 Quét QR hoặc chuyển khoản theo STK bên dưới:\n\n"
        f"🏦 <b>Ngân hàng:</b> {bank}\n"
        f"👤 <b>Chủ TK:</b> {receiver}\n"
        f"💳 <b>STK:</b> <code>{stk}</code>\n"
        f"📝 <b>Nội dung CK:</b> <code>{order_code}</code>\n\n"
        "⏳ Sau khi Admin duyệt, Key sẽ được gửi trực tiếp vào chat riêng này."
    )

    try:
        bot.send_photo(
            chat_id,
            qr_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=payment_keyboard(order_code)
        )
    except Exception:
        bot.send_message(
            chat_id,
            caption,
            parse_mode="HTML",
            reply_markup=payment_keyboard(order_code)
        )

    return order_code


@bot.callback_query_handler(func=lambda call: call.data in {
    "buy_3ngay", "buy_1tuan", "buy_1thang", "buy_vip"
})
def buy_package_callback(call):
    if not _group_admin_only(call.message):
        bot.answer_callback_query(call.id)
        return

    packages = {
        "buy_3ngay": ("3ngay", "KEY 3 NGÀY", "50.000đ"),
        "buy_1tuan": ("1tuan", "KEY 1 TUẦN", "80.000đ"),
        "buy_1thang": ("1thang", "KEY 1 THÁNG", "150.000đ"),
        "buy_vip": ("vinhvien", "KEY VĨNH VIỄN", "200.000đ"),
    }

    key_type, display_name, price = packages[call.data]

    try:
        order_code = _send_payment_for_package(
            call.message.chat.id,
            call.from_user.id,
            key_type,
            display_name,
            price
        )
        bot.answer_callback_query(call.id, f"Đã tạo đơn {order_code}")
    except Exception as e:
        print(f"Lỗi tạo đơn mua key: {e}")
        bot.answer_callback_query(call.id, "Không tạo được đơn, thử lại sau.")


@bot.message_handler(func=lambda m: m.text == "📁 Quản Lí Key")
def manage_key_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.send_message(msg.chat.id, "🔒 Chức năng này chỉ dành cho Admin.")
        return
    keys = load_keys()
    if not keys:
        bot.send_message(msg.chat.id, "📁 Hiện chưa có key chưa sử dụng.")
        return
    text = "📁 QUẢN LÍ KEY\n\n" + "\n".join(
        f"🔑 {k} → {datetime.fromisoformat(v).strftime('%H:%M %d-%m-%Y')}"
        for k, v in keys.items()
    )
    bot.send_message(msg.chat.id, text)


@bot.message_handler(func=lambda m: m.text == "💳 Nạp Tiền")
def deposit_button(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "💳 Nạp tiền hiện dùng hệ thống đơn hàng. Bấm /muakey để tạo đơn.")


@bot.message_handler(func=lambda m: m.text == "💵 Số Dư")
def balance_button(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "💵 Chức năng số dư chưa được liên kết với ví trong file hiện tại.")


@bot.message_handler(func=lambda m: m.text == "🎁 Giftcode")
def giftcode_button(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "🎁 Chức năng Giftcode chưa được triển khai trong file hiện tại.")


@bot.message_handler(func=lambda m: m.text == "🎟️ Tạo Giftcode")
def create_giftcode_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.send_message(msg.chat.id, "🔒 Chức năng này chỉ dành cho Admin.")
        return
    bot.send_message(msg.chat.id, "🎟️ Chức năng tạo Giftcode chưa được triển khai trong file hiện tại.")


def _parse_order_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _format_remaining(expire):
    if not expire:
        return "—"
    remaining = expire - datetime.now()
    seconds = int(remaining.total_seconds())
    if seconds <= 0:
        return "ĐÃ HẾT HẠN"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} ngày")
    if hours:
        parts.append(f"{hours} giờ")
    if minutes and not days:
        parts.append(f"{minutes} phút")
    return " ".join(parts) or "< 1 phút"


def _get_order_expiry(order_code, order_info):
    # Ưu tiên hạn được lưu trong chính đơn.
    expire = _parse_order_datetime(order_info.get("expire_at"))
    if expire:
        return expire

    # Tương thích các đơn cũ chưa có expire_at.
    try:
        uid = int(order_info.get("user_id"))
        return _parse_order_datetime(authenticated_users.get(uid))
    except Exception:
        return None


def _get_user_display(user_id):
    try:
        user = bot.get_chat(int(user_id))
        name = user.first_name or user.username or str(user_id)
        username = f"@{user.username}" if getattr(user, "username", None) else ""
        return name, username
    except Exception:
        return str(user_id), ""


@bot.message_handler(func=lambda m: m.text == "📜 Lịch Sử Nạp")
def deposit_history_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.send_message(msg.chat.id, "🔒 Chức năng này chỉ dành cho Admin.")
        return

    orders = load_orders()
    if not orders:
        bot.send_message(msg.chat.id, "📜 <b>LỊCH SỬ NẠP</b>\n\nChưa có đơn nạp nào.",
                         parse_mode="HTML")
        return

    # Hiển thị đơn mới nhất trước.
    items = sorted(
        orders.items(),
        key=lambda item: _parse_order_datetime(item[1].get("created_at")) or datetime.min,
        reverse=True
    )

    lines = ["📜 <b>LỊCH SỬ NẠP</b>", "━━━━━━━━━━━━━━━━━━"]
    for index, (order_code, info) in enumerate(items[:50], 1):
        uid = info.get("user_id", "—")
        name, username = _get_user_display(uid)
        package = {
            "3ngay": "3 NGÀY",
            "1tuan": "1 TUẦN",
            "1thang": "1 THÁNG",
            "vinhvien": "VĨNH VIỄN",
        }.get(info.get("key_type"), str(info.get("key_type", "—")))

        status = info.get("status", "pending")
        status_text = "✅ ĐÃ DUYỆT" if status == "done" else "⏳ CHỜ DUYỆT"

        created = _parse_order_datetime(info.get("created_at"))
        expire = _get_order_expiry(order_code, info)

        if expire and expire.year >= 2090:
            expiry_text = "VĨNH VIỄN"
            remain_text = "∞"
        elif expire:
            expiry_text = expire.strftime("%H:%M %d/%m/%Y")
            remain_text = _format_remaining(expire)
        else:
            expiry_text = "Chưa có"
            remain_text = "—"

        lines.extend([
            f"<b>{index}. {name}</b> {username}".strip(),
            f"🆔 ID: <code>{uid}</code>",
            f"🧾 Đơn: <code>{order_code}</code>",
            f"📦 Gói: <b>{package}</b> | {status_text}",
            f"💳 Nạp lúc: {created.strftime('%H:%M %d/%m/%Y') if created else '—'}",
            f"⏳ Hết hạn: <b>{expiry_text}</b>",
            f"⌛ Còn lại: <b>{remain_text}</b>",
            "━━━━━━━━━━━━━━━━━━",
        ])

    if len(items) > 50:
        lines.append(f"ℹ️ Đang hiển thị 50/{len(items)} đơn gần nhất.")

    bot.send_message(msg.chat.id, "\n".join(lines), parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "✅ Duyệt Nạp")
def approve_deposit_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.send_message(msg.chat.id, "🔒 Chức năng này chỉ dành cho Admin.")
        return
    bot.send_message(msg.chat.id, "✅ Duyệt đơn: dùng /done <MÃ_ĐƠN>")


@bot.message_handler(func=lambda m: m.text == "📝 Gửi Feedback")
def feedback_button(msg):
    if not _group_admin_only(msg):
        return
    bot.send_message(msg.chat.id, "📝 Bạn có thể gửi feedback trực tiếp tại đây cho Admin.")


@bot.message_handler(commands=['thongbao'])
def handle_thongbao(msg):
    """Admin gửi thông báo tới toàn bộ khách đã có quyền + nhóm thông báo."""
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.reply_to(msg, "🔒 Chức năng này chỉ dành cho Admin.")
        return

    # /thongbao <nội dung>
    content = msg.text.partition(" ")[2].strip()
    if not content:
        bot.reply_to(
            msg,
            "📢 <b>Cách dùng:</b>\n<code>/thongbao Nội dung thông báo</code>",
            parse_mode="HTML"
        )
        return

    notice = (
        "📢 <b>THÔNG BÁO TỪ ZYNEX AI</b> 📢\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{content}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💜 ZYNEX AI • Nhanh chóng • Bảo mật"
    )

    # Chỉ gửi tới khách đang có quyền/key hợp lệ trong authenticated_users.
    now = datetime.now()
    recipients = []
    for uid, expire in list(authenticated_users.items()):
        try:
            if expire > now:
                recipients.append(int(uid))
        except Exception:
            continue

    sent = 0
    failed = 0
    for uid in recipients:
        try:
            bot.send_message(uid, notice, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[ThongBao] Không gửi được tới {uid}: {e}")

    # Gửi cùng nội dung vào nhóm đã cấu hình. Không để lộ dữ liệu/key của khách.
    group_id = _load_notify_group()
    if group_id is not None:
        try:
            bot.send_message(group_id, notice, parse_mode="HTML")
        except Exception as e:
            failed += 1
            print(f"[ThongBao] Không gửi được vào group {group_id}: {e}")

    bot.reply_to(
        msg,
        f"✅ <b>Đã gửi thông báo.</b>\n👥 Khách nhận: <b>{sent}</b>\n⚠️ Lỗi: <b>{failed}</b>\n📢 Nhóm: <b>{'Đã gửi' if group_id is not None else 'Chưa cấu hình'}</b>",
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: m.text == "📢 Thông Báo")
def notice_button(msg):
    if not _group_admin_only(msg):
        return
    if msg.from_user.id != OWNER_ID:
        bot.send_message(msg.chat.id, "🔒 Chức năng này chỉ dành cho Admin.")
        return
    bot.send_message(
        msg.chat.id,
        "📢 <b>THÔNG BÁO</b>\n\n"
        "Dùng lệnh:\n"
        "<code>/thongbao Nội dung thông báo</code>\n\n"
        "Tin sẽ được gửi tới các khách đang có Key hợp lệ và nhóm đã cấu hình.",
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: m.text and m.from_user.id in PENDING_KEY_USERS and not m.text.startswith('/'))
def process_button_key(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    key = msg.text.strip()
    keys = load_keys()

    if key not in keys:
        PENDING_KEY_USERS.discard(uid)
        bot.send_message(msg.chat.id, "❌ Key sai hoặc không tồn tại.")
        return

    try:
        expire = datetime.fromisoformat(keys[key])
    except Exception:
        PENDING_KEY_USERS.discard(uid)
        bot.send_message(msg.chat.id, "❌ Key có dữ liệu không hợp lệ.")
        return

    if expire <= datetime.now():
        keys.pop(key, None)
        save_keys(keys)
        PENDING_KEY_USERS.discard(uid)
        bot.send_message(msg.chat.id, "⏰ Key đã hết hạn.")
        return

    # Kích hoạt key, sau đó chờ khoảng 1 giây rồi chuyển sang menu game.
    authenticated_users[uid] = expire
    save_auth_users_file()
    keys.pop(key, None)
    save_keys(keys)
    PENDING_KEY_USERS.discard(uid)

    # Xác nhận key trước, sau ~1 giây mới chuyển keyboard sang tầng MENU GAME.
    # ReplyKeyboard chỉ tồn tại ổn định khi message mang keyboard KHÔNG bị xóa.
    checking = bot.send_message(
        msg.chat.id,
        "✅ Key hợp lệ!\n⏳ Đang xác nhận key..."
    )

    time.sleep(1)
    try:
        bot.delete_message(msg.chat.id, checking.message_id)
    except Exception:
        pass

    # TẦNG 2: thay Reply Keyboard. Không gửi MENU GAME dạng tin nhắn.
    show_game_keyboard(msg.chat.id)

@bot.message_handler(func=lambda m: m.text == "🏠 Menu chính")
def back_main_menu_button(msg):
    if not _group_admin_only(msg):
        return
    uid = msg.from_user.id
    running_users.discard(uid)
    # TẦNG 1: quay lại keyboard menu chính.
    show_main_keyboard(msg.chat.id)


# ================== Khởi động BOT ==================
print(" Xâm nhập thành công.")

def expiry_warning_loop():
    """
    Nhắc khách qua chat riêng:
    - Khi key còn <= 24 giờ: báo còn 1 ngày/đang sắp hết hạn.
    - Khi key đã hết hạn được 5 giờ: báo đã hết hạn 5 giờ và mời mua/gia hạn.
    Mỗi mốc của mỗi đơn chỉ gửi 1 lần.
    """
    while True:
        try:
            now = datetime.now()
            orders = load_orders()
            sent_snapshot = dict(expiry_warning_sent)
            changed = False

            # Dùng orders.json làm nguồn chính để sau khi restart bot
            # vẫn biết các đơn đã duyệt và thời điểm hết hạn.
            for order_code, info in orders.items():
                if info.get("status") != "done":
                    continue

                try:
                    uid = int(info.get("user_id"))
                except Exception:
                    continue

                if uid == OWNER_ID:
                    continue

                expire = _get_order_expiry(order_code, info)
                if not expire or expire.year >= 2090:
                    continue

                remaining = expire - now
                expiry_key = expire.strftime("%Y-%m-%d %H:%M:%S")

                # ===== CẢNH BÁO CÒN 1 NGÀY =====
                warning_key = f"{uid}:24h:{expiry_key}"
                if timedelta(0) < remaining <= timedelta(days=1):
                    if sent_snapshot.get(warning_key) != "sent":
                        try:
                            bot.send_message(
                                uid,
                                "⚠️ <b>KEY SẮP HẾT HẠN</b>\n"
                                "━━━━━━━━━━━━━━━━━━\n"
                                f"🔑 Đơn: <code>{order_code}</code>\n"
                                f"⏳ Key còn khoảng: <b>{_format_remaining(expire)}</b>\n"
                                f"🕒 Hết hạn: <b>{expire.strftime('%H:%M %d/%m/%Y')}</b>\n\n"
                                "📌 Hãy mua/gia hạn Key mới để không bị gián đoạn sử dụng.",
                                parse_mode="HTML"
                            )
                            expiry_warning_sent[warning_key] = "sent"
                            changed = True
                        except Exception as e:
                            print(f"Không gửi được cảnh báo 24h cho {uid}: {e}")

                # ===== CẢNH BÁO SAU 5 GIỜ HẾT HẠN =====
                expired_for = now - expire
                expired_key = f"{uid}:5h:{expiry_key}"
                if timedelta(hours=5) <= expired_for < timedelta(hours=6):
                    if sent_snapshot.get(expired_key) != "sent":
                        try:
                            bot.send_message(
                                uid,
                                "🚫 <b>KEY ĐÃ HẾT HẠN</b>\n"
                                "━━━━━━━━━━━━━━━━━━\n"
                                f"🔑 Đơn: <code>{order_code}</code>\n"
                                f"🕒 Hết hạn: <b>{expire.strftime('%H:%M %d/%m/%Y')}</b>\n"
                                "⏱️ Đã hết hạn khoảng <b>5 giờ</b>.\n\n"
                                "💰 Vui lòng mua/gia hạn Key mới để tiếp tục sử dụng ZYNEX AI.",
                                parse_mode="HTML"
                            )
                            expiry_warning_sent[expired_key] = "sent"
                            changed = True
                        except Exception as e:
                            print(f"Không gửi được cảnh báo sau hết hạn cho {uid}: {e}")

            if changed:
                save_expiry_warning_sent(expiry_warning_sent)

        except Exception as e:
            print(f"Lỗi vòng nhắc hết hạn key: {e}")

        # Kiểm tra mỗi phút để không bỏ lỡ mốc 5 giờ.
        time.sleep(60)


threading.Thread(target=expiry_warning_loop, daemon=True).start()

save_keys_file()
save_auth_users_file()
save_kicked_file()
try:
    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=20,
                long_polling_timeout=25
            )
        except Exception as e:
            print(f"[Telegram polling] Mất kết nối: {type(e).__name__}: {e}")
            time.sleep(5)

except KeyboardInterrupt:
    print("Bot đang dừng...")
    save_keys_file()
    save_auth_users_file()
    save_kicked_file()
    print("Đã lưu dữ liệu và thoát.")

except Exception as e:
    print(f"Lỗi không xác định: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "key_back_main")
def key_back_main_callback(call):
    if not _group_admin_only(call.message):
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    try:
        show_main_keyboard(call.message.chat.id)
    except Exception:
        pass
