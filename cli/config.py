CLI_CONFIG = {
    # Announcements panel.
    #
    # Empty by default: with no URL the CLI makes no network call at all. That
    # is the right default here — a startup banner fetched from a third party's
    # server hands every user of this project their IP address on every run and
    # renders somebody else's notices inside this CLI. Point it at your own
    # endpoint to use the feature.
    "announcements_url": "",
    "announcements_timeout": 1.0,
    "announcements_fallback": "[cyan]For more information, please visit[/cyan] [link=https://github.com/QiuMoonlit/QuantAgents]https://github.com/QiuMoonlit/QuantAgents[/link]",
}
