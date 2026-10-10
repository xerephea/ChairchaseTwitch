import json
import os
import sys
import time
import urllib.parse
import urllib.request

CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]
WEBHOOK = os.environ["DISCORD_WEBHOOK"]
GAME_NAME = os.environ.get("GAME_NAME", "Chair Chase")
GAME_ID = os.environ.get("GAME_ID", "")
SEEN_FILE = "seen.json"
UA = "ChairChaseNotifier/1.0"


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
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Client-Id": CLIENT_ID,
        "Authorization": "Bearer " + token,
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def send_discord(payload):
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).close()


def main():
    token = get_token()

    game_id = GAME_ID
    if not game_id:
        games = twitch_get("games", {"name": GAME_NAME}, token)["data"]
        if not games:
            print(f"Twitch'te '{GAME_NAME}' adinda kategori bulunamadi.")
            sys.exit(1)
        game_id = games[0]["id"]
        print(f"Kategori bulundu: {games[0]['name']} (id {game_id})")

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

    try:
        with open(SEEN_FILE) as f:
            seen = set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = set()

    current = set()
    for s in streams:
        current.add(s["id"])
        if s["id"] in seen:
            continue
        thumb = s["thumbnail_url"].replace("{width}", "640").replace("{height}", "360")
        send_discord({
            "content": f"🔴 **{s['user_name']}** şu an Chair Chase yayını yapıyor!",
            "embeds": [{
                "title": (s["title"] or "Yayın")[:256],
                "url": f"https://twitch.tv/{s['user_login']}",
                "color": 0x9146FF,
                "fields": [
                    {"name": "İzleyici", "value": str(s["viewer_count"]), "inline": True},
                    {"name": "Dil", "value": s.get("language") or "?", "inline": True},
                ],
                "image": {"url": thumb},
            }],
        })
        print("Bildirim gonderildi:", s["user_name"])
        time.sleep(1)

    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(current), f)

    print(f"Su an canli yayin sayisi: {len(current)}")


if __name__ == "__main__":
    main()
