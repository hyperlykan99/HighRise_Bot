set("init.allow_root", true)
# ChillTopia Liquidsoap radio stream
# DJ_DUDU = queue brain
# Liquidsoap = playback engine
# Icecast = public stream server
# Mount URL: http://46.250.233.150:8000/chilltopia

set("log.stdout", true)
set("log.file", false)

request_queue = playlist(mode="normal", reload=2, "/opt/highrise-bots/artifacts/highrise-bot/liquidsoap/current_request.m3u")

autodj = playlist(mode="randomize", reload=10, "/opt/highrise-bots/artifacts/highrise-bot/liquidsoap/autodj/active")

radio = fallback(track_sensitive=false, [request_queue, autodj, blank()])
radio = mksafe(radio)

output.icecast(
  %mp3(bitrate=128),
  host="127.0.0.1",
  port=8000,
  password="ChillTopia123",
  mount="/chilltopia",
  name="ChillTopia Radio",
  description="ChillTopia Liquidsoap radio stream",
  genre="HighRise",
  public=false,
  radio
)
