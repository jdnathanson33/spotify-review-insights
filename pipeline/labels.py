"""Shared label definitions, subtopic taxonomy and record validation.

The eight topics, five intents and five severity levels are the course's
common labels (GRADING_CONTRACT.md). Subtopics are our own optional extension;
complaint/cancellation issue membership is `topic.subtopic`.
"""

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")  # precedence order
SEVERITIES = (1, 2, 3, 4, 5)

# Product areas named in the business question.
AREA_OF_TOPIC = {
    "access": "access", "usability": "usability", "playback": "playback",
    "downloads": "playback", "catalog": "usability", "billing": "billing/support",
    "support": "billing/support", "other": "none",
}

SUBTOPICS = {
    "access": {
        "login_failure": "cannot log in / logged out and cannot get back in / password not accepted",
        "account_lost_or_hacked": "account taken over, lost, merged, deleted, or data on it lost",
        "signup_verification": "cannot sign up, verification code/email/phone problems",
        "other": "other access problem",
    },
    "usability": {
        "ads_frequency_volume": "too many / too long / too loud / unskippable ads",
        "ui_redesign_navigation": "layout, home screen, navigation, design changes, hard to find things",
        "playlist_queue_management": "queue, playlist, liked songs, shuffle behaviour, unwanted songs inserted into playlists (not paywall)",
        "controls_settings": "buttons, gestures, settings, widgets/notification controls behaving badly",
        "other": "other usability problem",
    },
    "playback": {
        "crash_or_wont_open": "app crashes, freezes, will not open, stuck on loading screen",
        "playback_interrupted": "music stops, pauses, skips, won't play, cuts out",
        "connectivity_errors": "'no internet'/'offline' errors while online, wifi/data connection failures",
        "audio_quality": "sound quality, volume, equalizer, audio glitches",
        "device_integration": "bluetooth, car, Android Auto, assistant, smart speakers, Chromecast, watch",
        "performance_resource": "lag, slowness, battery drain, storage or data use",
        "other": "other playback problem",
    },
    "downloads": {
        "offline_playback_failure": "downloaded music will not play offline / needs internet",
        "downloads_disappearing": "downloads deleted or must be re-downloaded",
        "download_process": "downloading stuck, slow, fails, limits",
        "other": "other downloads problem",
    },
    "catalog": {
        "missing_content": "songs/artists/albums missing, greyed out or removed",
        "search_discovery": "search results, finding music",
        "recommendations_quality": "quality/relevance of recommendations, mixes, algorithm",
        "lyrics_availability": "lyrics missing, not loading or out of sync (not a paywall complaint)",
        "podcasts_audiobooks": "podcast/audiobook content",
        "other": "other catalog problem",
    },
    "billing": {
        "premium_only_controls": "free tier restrictions: skip limits, cannot choose/replay/rewind/seek songs, forced shuffle, lyrics or other features moved behind Premium",
        "price_too_high": "price, price increases, value for money",
        "unexpected_charges_refunds": "charged unexpectedly, after cancelling, double charged, refunds",
        "subscription_entitlement": "premium/family/student plan not activating, lost benefits, plan management",
        "payment_methods": "payment method, card, gift card problems",
        "other": "other billing problem",
    },
    "support": {
        "unresponsive_unhelpful": "support unreachable, slow or unhelpful",
        "other": "other support problem",
    },
    "other": {
        "generic_criticism": "bad/worst app, generic dislike with no specific defect",
        "update_regression_generic": "'new update is bad' with no specific feature named",
        "boycott_political": "political, religious or boycott content",
        "praise_general": "general praise with no specific feature",
        "unclear_unrelated": "meaningless, unrelated or uninterpretable text",
        "other": "anything else",
    },
}

COMPLAINT_INTENTS = ("complaint", "cancellation")


def issue_id(topic, subtopic):
    return f"{topic}.{subtopic}"


def subtopic_table():
    lines = []
    for t, subs in SUBTOPICS.items():
        for s, d in subs.items():
            lines.append(f"- {t}.{s}: {d}")
    return "\n".join(lines)


def validate_labels(lab):
    """Return an error string or None for a model label dict (internal schema)."""
    if lab.get("topic") not in TOPICS:
        return "bad_topic"
    if lab.get("subtopic") not in SUBTOPICS[lab["topic"]]:
        return "bad_subtopic"
    if lab.get("intent") not in INTENTS:
        return "bad_intent"
    sev = lab.get("severity")
    if type(sev) is not int or sev not in SEVERITIES:
        return "bad_severity"
    s = lab.get("sentiment")
    if type(s) not in (int, float) or not (-1 <= s <= 1):
        return "bad_sentiment"
    if type(lab.get("needs_review")) is not bool:
        return "bad_needs_review"
    q = lab.get("evidence_quote")
    if not isinstance(q, str) or not q.strip():
        return "bad_quote"
    return None
