import csv
import json
from datetime import datetime, timedelta, timezone
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
WEBHOOK = os.environ["DISCORD_WEBHOOK"]
WEBHOOK_PUBLIC = os.environ.get("DISCORD_WEBHOOK_PUBLIC", "")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "")
GAME_NAME = os.environ.get("GAME_NAME", "Chair Chase")
GAME_ID = os.environ.get("GAME_ID", "")
SEEN_FILE = "seen.json"
HISTORY_FILE = "history.csv"
TR_TZ = timezone(timedelta(hours=3))
UA = "ChairChaseNotifier/1.0"
YOUTUBE_INTERVAL = 15 * 60  # YouTube kotasi icin en fazla 15 dakikada bir
CHZZK_KEYWORDS = [k.strip() for k in os.environ.get("CHZZK_KEYWORDS", GAME_NAME).split(",") if k.strip()]
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", 50 * 60))  # her calistirma bu kadar sure acik kalir
CHECK_EVERY = 3 * 60  # bu sure icinde kac saniyede bir kontrol edilecegi


def get_json(url, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def get_token():
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request("https://id.twitch.tv/oauth2/token", data=body, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["access_token"]


def twitch_get(path, params, token):
    url = "https://api.twitch.tv/helix/" + path + "?" + urllib.parse.urlencode(params)
    return get_json(url, {"Client-Id": CLIENT_ID, "Authorization": "Bearer " + token})


def send_discord(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).close()


def log_history(platform, user, link, title, viewers):
    new_file = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_tr", "platform", "streamer", "viewers_at_start", "link", "title"])
        w.writerow([datetime.now(TR_TZ).strftime("%Y-%m-%d %H:%M"), platform, user, viewers, link, title])


def notify(platform, user, link, title, viewers, extra_field, thumb, color):
    log_history(platform, user, link, title, viewers)
    embed = {
        "title": (title or "Live stream")[:256],
        "url": link,
        "color": color,
        "fields": [
            {"name": "Platform", "value": platform, "inline": True},
            {"name": "Viewers", "value": str(viewers), "inline": True},
        ],
        "image": {"url": thumb},
    }
    if extra_field:
        embed["fields"].append(extra_field)
    send_discord(WEBHOOK, {
        "content": f"🔴 **{user}** is live with Chair Chase on {platform}!",
        "embeds": [embed],
    })
    if WEBHOOK_PUBLIC:
        time.sleep(1)
        send_discord(WEBHOOK_PUBLIC, {
            "content": f"🎮 **{user}** is playing Chair Chase on {platform} right now! Stop by and say hi 👋",
            "embeds": [embed],
        })
    print(f"Bildirim gonderildi ({platform}):", user)
    time.sleep(1)


def load_state():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if isinstance(data, list):  # eski format
        data = {"twitch": data}
    return {
        "twitch": set(data.get("twitch", [])),
        "youtube": set(data.get("youtube", [])),
        "chzzk": set(data.get("chzzk", [])),
        "yt_last": data.get("yt_last", 0),
    }


def save_state(state):
    with open(SEEN_FILE, "w") as f:
        json.dump({
            "twitch": sorted(state["twitch"]),
            "youtube": sorted(state["youtube"]),
            "chzzk": sorted(state["chzzk"]),
            "yt_last": state["yt_last"],
        }, f)


def check_twitch(state):
    token = get_token()
    game_id = GAME_ID
    if not game_id:
        games = twitch_get("games", {"name": GAME_NAME}, token)["data"]
        if not games:
            print(f"Twitch'te '{GAME_NAME}' adinda kategori bulunamadi.")
            sys.exit(1)
        game_id = games[0]["id"]

    streams = []
    cursor = None
    while True:
        params = {"game_id": game_id, "first": 100}
        if cursor:
            params["after"] = cursor
        res = twitch_get("streams", params, token)
        streams += res["data"]
        cursor = res.get("pagination", {}).get("cursor")
        if not cursor or not res["data"]:
            break

    current = set()
    for s in streams:
        current.add(s["id"])
        if s["id"] in state["twitch"]:
            continue
        thumb = s["thumbnail_url"].replace("{width}", "640").replace("{height}", "360")
        notify("Twitch", s["user_name"], f"https://twitch.tv/{s['user_login']}", s["title"],
               s["viewer_count"], {"name": "Language", "value": s.get("language") or "?", "inline": True},
               thumb, 0x9146FF)
    state["twitch"] = current
    print(f"Twitch canli yayin sayisi: {len(current)}")


def check_youtube(state):
    params = urllib.parse.urlencode({
        "part": "snippet",
        "q": f'"{GAME_NAME}"',
        "type": "video",
        "eventType": "live",
        "maxResults": 50,
        "key": YOUTUBE_KEY,
    })
    items = get_json("https://www.googleapis.com/youtube/v3/search?" + params).get("items", [])
    needle = GAME_NAME.lower()
    items = [i for i in items
             if needle in (i["snippet"]["title"] + " " + i["snippet"].get("description", "")).lower()]

    viewers = {}
    if items:
        ids = ",".join(i["id"]["videoId"] for i in items)
        vparams = urllib.parse.urlencode({"part": "liveStreamingDetails", "id": ids, "key": YOUTUBE_KEY})
        for v in get_json("https://www.googleapis.com/youtube/v3/videos?" + vparams).get("items", []):
            viewers[v["id"]] = v.get("liveStreamingDetails", {}).get("concurrentViewers", "?")

    current = set()
    for i in items:
        vid = i["id"]["videoId"]
        current.add(vid)
        if vid in state["youtube"]:
            continue
        sn = i["snippet"]
        thumbs = sn.get("thumbnails", {})
        thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        notify("YouTube", sn["channelTitle"], f"https://www.youtube.com/watch?v={vid}", sn["title"],
               viewers.get(vid, "?"), None, thumb, 0xFF0000)
    state["youtube"] = current
    print(f"YouTube canli yayin sayisi: {len(current)}")


def norm(text):
    return "".join(str(text or "").lower().split())


def check_chzzk(state):
    found = {}
    for kw in CHZZK_KEYWORDS:
        url = "https://api.chzzk.naver.com/service/v1/search/lives?" + urllib.parse.urlencode(
            {"keyword": kw, "offset": 0, "size": 50})
        res = get_json(url, {"User-Agent": BROWSER_UA, "Accept": "application/json"})
        for item in (res.get("content") or {}).get("data") or []:
            live = item.get("live") or {}
            ch = item.get("channel") or {}
            cid = ch.get("channelId")
            if not cid:
                continue
            haystack = norm(live.get("liveTitle")) + " " + norm(live.get("liveCategoryValue")) + " " + \
                " ".join(norm(t) for t in (live.get("tags") or []))
            if norm(kw) not in haystack:
                continue
            key = f"{cid}:{live.get('liveId') or live.get('openDate') or ''}"
            found[key] = (ch, live)

    for key, (ch, live) in found.items():
        if key in state["chzzk"]:
            continue
        thumb = (live.get("liveImageUrl") or "").replace("{type}", "480")
        notify("CHZZK", ch.get("channelName") or "?", f"https://chzzk.naver.com/live/{ch['channelId']}",
               live.get("liveTitle"), live.get("concurrentUserCount", "?"), None, thumb, 0x00FFA3)
    state["chzzk"] = set(found)
    print(f"CHZZK canli yayin sayisi: {len(found)}")


def run_once(state):
    check_twitch(state)
    try:
        check_chzzk(state)
    except Exception as e:  # resmi olmayan API, hata verse de digerlerini durdurmasin
        print(f"CHZZK kontrol edilemedi ({e}).")
    if YOUTUBE_KEY and time.time() - state["yt_last"] >= YOUTUBE_INTERVAL:
        state["yt_last"] = int(time.time())
        try:
            check_youtube(state)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            if e.code == 403 and "quota" in body.lower():
                print("YouTube gunluk kotasi doldu, yarin devam edecek.")
            elif e.code == 429 or e.code >= 500:
                print(f"YouTube gecici hata ({e.code}).")
            else:
                return f"YouTube hatasi {e.code}: {body[:300]}"
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"YouTube baglanti hatasi ({e}).")
    return None


def main():
    state = load_state()
    end = time.time() + RUN_SECONDS
    while True:
        error = None
        try:
            error = run_once(state)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                print(f"Gecici hata ({e.code}), sonraki turda tekrar denenecek.")
            else:
                save_state(state)
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"Baglanti hatasi ({e}), sonraki turda tekrar denenecek.")
        save_state(state)
        if error:
            print(error)
            sys.exit(1)
        if time.time() + CHECK_EVERY > end:
            break
        time.sleep(CHECK_EVERY)


if __name__ == "__main__":
    main()
