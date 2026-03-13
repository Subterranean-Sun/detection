import json
import os
import time
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests
from plyer import notification
import tkinter as tk
import winsound
import random


API_URL = "https://api.cc98.org/board/459/topic?from=0&size=20"
STATE_FILE = "state.json"
CHECK_INTERVAL = 28
TOKEN_URL = "https://openid.cc98.org/connect/token"
CLIENT_ID = "9a1fd200-8687-44b1-4c20-08d50a96e5cd"
CLIENT_SECRET = "8b53f727-08e2-4509-8857-e34bf92b27f2"

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
}

SPECIAL_KEYWORDS = [
    " ", " ", " ",

]


class CC98AuthError(Exception):
    pass


class CC98MonitorError(Exception):
    pass


class AuthManager:
    def __init__(self, session: requests.Session, username: str, password: str) -> None:
        self.session = session
        self.username = username
        self.password = password
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: Optional[datetime] = None

    def _save_tokens(self, token_data: Dict[str, Any]) -> None:
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)
        expires_in = int(token_data.get("expires_in", 3600))
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    def login(self) -> None:
        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "scope": "cc98-api openid offline_access",
        }
        try:
            resp = self.session.post(TOKEN_URL, data=data, timeout=20)
        except requests.RequestException as e:
            raise CC98AuthError(f"登录请求失败：{e}") from e

        if resp.status_code >= 400:
            body = resp.text[:300]
            raise CC98AuthError(
                f"密码登录失败，状态码 {resp.status_code}，返回：{body}"
            )

        token_data = resp.json()
        if "access_token" not in token_data:
            raise CC98AuthError(f"登录失败：返回中没有 access_token：{token_data}")
        self._save_tokens(token_data)
        print("登录成功，已自动获取 Authorization")

    def refresh(self) -> bool:
        if not self.refresh_token:
            return False

        data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            resp = self.session.post(TOKEN_URL, data=data, timeout=20)
            if resp.status_code >= 400:
                return False
            token_data = resp.json()
            if "access_token" not in token_data:
                return False
            self._save_tokens(token_data)
            print("Authorization 已自动刷新")
            return True
        except requests.RequestException:
            return False

    def ensure_token(self) -> None:
        if not self.access_token:
            self.login()
            return

        if self.expires_at and datetime.now(timezone.utc) > self.expires_at - timedelta(minutes=2):
            print("检测到 Authorization 即将过期，尝试自动刷新")
            if not self.refresh():
                print("刷新失败，改为重新登录")
                self.login()

    def get_headers(self) -> Dict[str, str]:
        self.ensure_token()
        if not self.access_token:
            raise CC98AuthError("没有可用的 access_token")
        headers = BASE_HEADERS.copy()
        headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def handle_401(self) -> None:
        print("检测到 401，尝试刷新/重新登录")
        if not self.refresh():
            self.login()


def build_session(config: Dict[str, Any]) -> requests.Session:
    webvpn = config.get("webvpn") or {}
    if webvpn.get("username") and webvpn.get("password"):
        try:
            import ZJUWebVPN  # type: ignore

            print("检测到 WebVPN 配置，尝试通过 WebVPN 建立会话")
            session = ZJUWebVPN.ZJUWebVPNSession(webvpn["username"], webvpn["password"])
            print("WebVPN 登录成功")
            return session
        except Exception as e:
            print(f"WebVPN 初始化失败，回退到普通 Session：{e}")
    return requests.Session()


def load_config() -> Dict[str, Any]:
    if not os.path.exists("config.json"):
        raise FileNotFoundError("未找到 config.json")
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

"""
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"latest_id": None}
"""
def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"latest_ids": []}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

"""
def get_latest_topic(auth: AuthManager, api_url: str) -> Dict[str, Any]:
    headers = auth.get_headers()
    response = auth.session.get(api_url, headers=headers, timeout=15)

    if response.status_code == 401:
        auth.handle_401()
        headers = auth.get_headers()
        response = auth.session.get(api_url, headers=headers, timeout=15)

    if response.status_code == 401:
        raise CC98MonitorError("认证失效：自动刷新后仍然 401")
    if response.status_code == 403:
        raise CC98MonitorError("没有权限访问：可能当前账号/网络环境不允许访问")
    if response.status_code != 200:
        raise CC98MonitorError(
            f"请求失败，状态码：{response.status_code}，返回内容：{response.text[:300]}"
        )

    data = response.json()
    if not isinstance(data, list) or not data:
        raise CC98MonitorError("接口返回不是帖子列表，或列表为空")

    first = data[0]
    topic_id = first["id"]
    title = first.get("title", "")
    topic_time = first.get("time", "")
    link = f"https://www.cc98.org/topic/{topic_id}"
    return {"id": topic_id, "title": title, "time": topic_time, "link": link}
"""

def get_latest_topics(auth: AuthManager, api_url: str) -> list[Dict[str, Any]]:
    headers = auth.get_headers()
    response = auth.session.get(api_url, headers=headers, timeout=15)

    if response.status_code == 401:
        auth.handle_401()
        headers = auth.get_headers()
        response = auth.session.get(api_url, headers=headers, timeout=15)

    if response.status_code == 401:
        raise CC98MonitorError("认证失效：自动刷新后仍然 401")
    if response.status_code == 403:
        raise CC98MonitorError("没有权限访问：可能当前账号/网络环境不允许访问")
    if response.status_code != 200:
        raise CC98MonitorError(
            f"请求失败，状态码：{response.status_code}，返回内容：{response.text[:300]}"
        )

    data = response.json()
    if not isinstance(data, list) or not data:
        raise CC98MonitorError("接口返回不是帖子列表，或列表为空")

    topics = []
    for item in data[:2]:
        topic_id = item["id"]
        title = item.get("title", "")
        topic_time = item.get("time", "")
        link = f"https://www.cc98.org/topic/{topic_id}"
        topics.append({
            "id": topic_id,
            "title": title,
            "time": topic_time,
            "link": link
        })

    return topics


def contains_special_keyword(title: str):
    title_lower = title.lower()
    return [kw for kw in SPECIAL_KEYWORDS if kw.lower() in title_lower]


def play_small_alert_sound() -> None:
    """轻提示音：尽量不打扰当前电脑其他工作。"""
    try:
        # Windows 系统提示音，较轻，异步播放，不阻塞
        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


def play_big_alert_sound() -> None:
    """强提示音：明显一些，但放到线程里避免阻塞界面。"""
    def _play():
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            sound_path = os.path.join(base_dir, "alert.wav")

            if os.path.exists(sound_path):
                winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC)
            for _ in range(2):
                try:
                    winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_SYNC)
                except Exception:
                    try:
                        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_SYNC)
                    except Exception:
                        break
            time.sleep(0.15)
            # 三段式提示音，比单次 Beep 更容易注意到
            winsound.Beep(1400, 180)
            time.sleep(0.08)
            winsound.Beep(1700, 220)
            time.sleep(0.08)
            winsound.Beep(2000, 420)
        except Exception:
            try:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except Exception:
                pass

    threading.Thread(target=_play, daemon=True).start()

def show_small_notification(topic: Dict[str, Any]) -> None:
    notification.notify(
        title="CC98 新帖提醒",
        message=topic["title"],
        app_name="CC98 Monitor",
        timeout=10,
    )


def show_big_popup(topic: Dict[str, Any], matched_keywords, duration=30000) -> None:
    def popup():
        root = tk.Tk()
        root.title("CC98 特殊新帖提醒")
        window_width = 920
        window_height = 380
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = int((screen_width - window_width) / 2)
        y = int((screen_height - window_height) / 2)
        root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = tk.Frame(root, padx=25, pady=20)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="CC98 459 板块命中关键词新帖", font=("微软雅黑", 22, "bold")).pack(pady=(0, 16))
        tk.Label(frame, text=f"命中关键词：{'、'.join(matched_keywords)}", font=("微软雅黑", 14, "bold")).pack(pady=(0, 14))
        tk.Label(frame, text=topic["title"], font=("微软雅黑", 18), wraplength=840, justify="center").pack(pady=(0, 18))
        tk.Label(frame, text=f"发布时间：{topic['time']}", font=("微软雅黑", 13)).pack(pady=(0, 10))

        link_label = tk.Label(
            frame,
            text=topic["link"],
            font=("微软雅黑", 12),
            wraplength=840,
            justify="center",
            fg="blue",
            cursor="hand2",
        )
        link_label.pack(pady=(0, 18))

        def copy_link(event=None):
            root.clipboard_clear()
            root.clipboard_append(topic["link"])

        def open_link():
            webbrowser.open(topic["link"])

        link_label.bind("<Button-1>", copy_link)

        btn_frame = tk.Frame(frame)
        btn_frame.pack()
        tk.Button(btn_frame, text="打开帖子", font=("微软雅黑", 12), width=12, command=open_link).pack(side="left", padx=8)
        tk.Button(btn_frame, text="关闭", font=("微软雅黑", 12), width=12, command=root.destroy).pack(side="left", padx=8)

        root.after(duration, root.destroy)
        root.mainloop()

    threading.Thread(target=popup, daemon=True).start()


def show_notification(topic: Dict[str, Any]) -> None:
    matched_keywords = contains_special_keyword(topic["title"])
    if matched_keywords:
        play_big_alert_sound()
        show_big_popup(topic, matched_keywords, duration=30000)
        print(f"命中关键词：{'、'.join(matched_keywords)} -> 触发特殊弹窗")
    else:
        play_small_alert_sound()
        show_small_notification(topic)
        print("未命中关键词 -> 触发普通小弹窗")


def main() -> None:
    config = load_config()
    cc98_conf = config.get("cc98") or {}
    if not cc98_conf.get("username") or not cc98_conf.get("password"):
        raise ValueError("config.json 中缺少 cc98.username / cc98.password")

    monitor_conf = config.get("monitor") or {}
    api_url = monitor_conf.get("board_api", API_URL)
    #interval = int(monitor_conf.get("check_interval", CHECK_INTERVAL))

    interval_range = monitor_conf.get("check_interval_range")
    fixed_interval = int(monitor_conf.get("check_interval", CHECK_INTERVAL))

    if (
        isinstance(interval_range, list)
        and len(interval_range) == 2
        and all(isinstance(x, (int, float)) for x in interval_range)
    ):
        min_interval = int(interval_range[0])
        max_interval = int(interval_range[1])
        if min_interval > max_interval:
            min_interval, max_interval = max_interval, min_interval
    else:
        min_interval = fixed_interval
        max_interval = fixed_interval

    session = build_session(config)
    auth = AuthManager(session, cc98_conf["username"], cc98_conf["password"])
    sleep_seconds = random.randint(min_interval, max_interval)
    
    print("CC98 板块新帖监控已启动")
    print(f"监控接口：{api_url}")
    print(f"检查间隔：{sleep_seconds} 秒")
    print(f"特殊关键词：{'、'.join(SPECIAL_KEYWORDS)}")
    """
    state = load_state()
    initialized = state.get("latest_id") is not None

    while True:
        try:
            latest = get_latest_topic(auth, api_url)
            print("当前最新帖：", latest["title"])
            if not initialized:
                state["latest_id"] = latest["id"]
                save_state(state)
                initialized = True
                print("初始化完成，当前最新帖已记录，不触发提醒。")
            elif latest["id"] != state.get("latest_id"):
                print("=" * 60)
                print("发现新帖！")
                print("标题：", latest["title"])
                print("时间：", latest["time"])
                print("链接：", latest["link"])
                print("=" * 60)
                show_notification(latest)
                state["latest_id"] = latest["id"]
                save_state(state)
            else:
                print(datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")+"    "+"暂无新帖")
                #print()
        except Exception as e:
            print("检查失败：", e)

        time.sleep(interval)"""

    state = load_state()
    initialized = bool(state.get("latest_ids"))

    while True:
        try:
            latest_topics = get_latest_topics(auth, api_url)
            current_ids = [topic["id"] for topic in latest_topics]

            print("当前最新两帖：")
            for topic in latest_topics:
                print(f"- {topic['title']}")

            if not initialized:
                state["latest_ids"] = current_ids
                save_state(state)
                initialized = True
                print("初始化完成，当前最新两帖已记录，不触发提醒。")
            else:
                old_ids = state.get("latest_ids", [])
                new_topics = [topic for topic in latest_topics if topic["id"] not in old_ids]

                system_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")

                if new_topics:
                    for topic in reversed(new_topics):
                        print("=" * 60)
                        print(system_time + "    "+"发现新帖！")
                        print("标题：", topic["title"])
                        print("时间：", topic["time"])
                        print("链接：", topic["link"])
                        print("=" * 60)
                        show_notification(topic)

                    state["latest_ids"] = current_ids
                    save_state(state)
                else:
                    print(system_time + "    "+"暂无新帖")

        except Exception as e:
            print("检查失败：", e)

        #time.sleep(interval)
        time.sleep(sleep_seconds)
        sleep_seconds = random.randint(min_interval, max_interval)
        print(f"检查间隔：{sleep_seconds} 秒")


if __name__ == "__main__":
    main()
