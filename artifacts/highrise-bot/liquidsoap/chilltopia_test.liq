set("init.allow_root", true)
# ChillTopia Liquidsoap test stream
# Liquidsoap = playback engine
# Icecast = public stream server
# Mount URL: http://46.250.233.150:8000/chilltopia

set("log.stdout", true)
set("log.file", false)

autodj = playlist(mode="randomize", reload=10, "/opt/highrise-bots/artifacts/highrise-bot/liquidsoap/autodj")

radio = mksafe(autodj)

output.icecast(
  %mp3(bitrate=128),
  host="127.0.0.1",
  port=8000,
  password="ChillTopia123",
  mount="/chilltopia",
  name="ChillTopia Radio",
  description="ChillTopia Liquidsoap test stream",
  genre="HighRise",
  public=false,
  radio
)
