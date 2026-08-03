@echo off
REM Performance Attribution Tracker - daily refresh + publish to GitHub Pages
REM This batch file exists so Windows Task Scheduler runs the script from the
REM correct folder (Task Scheduler doesn't know Python's working directory otherwise).

cd /d "%~dp0"

REM Log each run's output so you can check later whether it succeeded, without
REM needing to keep a terminal window open. Creates/appends run_log.txt in this folder.
echo. >> run_log.txt
echo ==== Run started: %date% %time% ==== >> run_log.txt

py refresh_dashboard.py >> run_log.txt 2>&1

REM Keep index.html in sync so YOURUSERNAME.github.io/your-repo/ opens the dashboard
REM directly, without needing to type the full filename on your phone.
copy /y attribution_tracker_live.html index.html >> run_log.txt 2>&1

REM Publish to GitHub Pages so the link is reachable from your phone anywhere (not just
REM on the same WiFi). Requires the one-time git setup to have been done already --
REM see one_time_git_setup.txt. If git isn't set up yet, these commands just log an
REM error below and the rest of the dashboard still works locally as before.
git add attribution_tracker_live.html index.html dashboard_data.json run_log.txt >> run_log.txt 2>&1
git commit -m "Daily refresh: %date% %time%" >> run_log.txt 2>&1
git push origin main >> run_log.txt 2>&1

echo ==== Run finished: %date% %time% ==== >> run_log.txt
