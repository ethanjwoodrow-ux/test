#!/bin/bash
# Set up cron jobs for automated UC Learn tasks.
# Run once: bash cron_setup.sh

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Build the crontab entries
CRON_JOBS=$(cat <<EOF
# UC Learn Automation
# Daily digest at 7:30 AM
30 7 * * * cd "$PROJECT_DIR" && $PYTHON scripts/daily_digest.py --save-text "$LOG_DIR/digest_\$(date +\%Y\%m\%d).txt" >> "$LOG_DIR/cron.log" 2>&1

# Check for new content at 9 AM and 3 PM
0 9,15 * * * cd "$PROJECT_DIR" && $PYTHON scripts/check_new_content.py >> "$LOG_DIR/cron.log" 2>&1

# Update grades spreadsheet every weekday at 6 PM
0 18 * * 1-5 cd "$PROJECT_DIR" && $PYTHON scripts/check_grades.py >> "$LOG_DIR/cron.log" 2>&1

# Rebuild calendar weekly (Sunday 8 AM)
0 8 * * 0 cd "$PROJECT_DIR" && $PYTHON scripts/build_calendar.py >> "$LOG_DIR/cron.log" 2>&1
EOF
)

# Install crontab (preserving any existing entries)
(crontab -l 2>/dev/null | grep -v "UC Learn Automation" | grep -v "daily_digest\|check_new_content\|check_grades\|build_calendar"; echo "$CRON_JOBS") | crontab -

echo "Cron jobs installed. Current crontab:"
crontab -l
