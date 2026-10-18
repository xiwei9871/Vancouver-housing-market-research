CRON_EXAMPLES = """# 每天早上 8 点跑增量
0 8 * * * /path/to/northshore-watchdog run-daily --config /path/to/config/default.yaml

# 每周六早上 9 点跑完整报告
0 9 * * 6 /path/to/northshore-watchdog run-weekly --config /path/to/config/default.yaml
"""

SYSTEMD_TIMER_EXAMPLE = """[Unit]
Description=North Shore Property Watchdog weekly report

[Timer]
OnCalendar=Sat 09:00
Persistent=true

[Install]
WantedBy=timers.target
"""
