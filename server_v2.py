import odds_sports
import tennis_patch

tennis_patch.install(odds_sports)

from server_base import app  # noqa: E402

# Expose patch version without touching the production UI/engine logic.
app.version = "9.1"
