import json
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
UA = "ChairChaseNotifier/1.0"
YOUTUBE_INTERVAL = 15 * 60  # YouTube kotasi icin en fazla 15 dakikada bir


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


def notify(platform, user, link, title, viewers, extra_field, thumb, color):
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
        "yt_last": data.get("yt_last", 0),
    }


def save_state(state):
    with open(SEEN_FILE, "w") as f:
        json.dump({
            "twitch": sorted(state["twitch"]),
            "youtube": sorted(state["youtube"]),
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


def main():
    state = load_state()
    youtube_failed = None
    try:
        check_twitch(state)
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
                    youtube_failed = f"YouTube hatasi {e.code}: {body[:300]}"
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"YouTube baglanti hatasi ({e}).")
    finally:
        save_state(state)
    if youtube_failed:
        print(youtube_failed)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code >= 500:
            print(f"Gecici hata ({e.code}), sonraki turda tekrar denenecek.")
            sys.exit(0)
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Baglanti hatasi ({e}), sonraki turda tekrar denenecek.")
        sys.exit(0)
