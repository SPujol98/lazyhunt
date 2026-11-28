# Keeping the bot running

`uv run bot.py` runs the bot until you close the terminal. That is all you need
to try LazyHunt; to have it working all day, start it in the background or
let your operating system start it when you log in. Pick one option below.

All of them assume the repository is in a folder called `lazyhunt` in your
home directory. If yours is somewhere else, change the paths. The bot reads
`.env` and `config.local.toml` from its working directory, so it must always
start inside that folder.

The bot writes its log to the terminal (standard error). Each option below
says where that output ends up.

## Quick option: a terminal multiplexer

On Linux or macOS, `tmux` (or `screen`) keeps the bot running after you close
the terminal window. It stops when the computer restarts.

```bash
cd ~/lazyhunt
tmux new -s lazyhunt 'uv run bot.py'
```

Press `Ctrl+B`, then `D`, to leave it running in the background.
`tmux attach -t lazyhunt` brings it back (with its log); `Ctrl+C` inside it
stops the bot. With `screen`: `screen -S lazyhunt uv run bot.py`, detach with
`Ctrl+A` then `D`, reattach with `screen -r lazyhunt`.

## Linux: a systemd user service

A user service starts when you log in and restarts the bot if it crashes. No
`sudo` is needed.

1. Find the full paths of `uv` and `claude`:

   ```bash
   command -v uv claude
   ```

2. Create `~/.config/systemd/user/lazyhunt.service`:

   ```ini
   [Unit]
   Description=lazyhunt job-search bot

   [Service]
   WorkingDirectory=%h/lazyhunt
   ExecStart=%h/.local/bin/uv run bot.py
   # Services get a minimal PATH: include the folders that hold uv and claude.
   Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
   Restart=on-failure
   RestartSec=30

   [Install]
   WantedBy=default.target
   ```

   `%h` is your home directory. If step 1 printed other folders, use them in
   `ExecStart` and `PATH`. (Instead of extending `PATH`, you can set
   `claude_code_path` in `config.local.toml` to the full path of `claude`.)

3. Enable and start it:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now lazyhunt
   systemctl --user status lazyhunt
   ```

4. Optional: to keep the bot running when you are logged out (and to start it
   at boot, before you log in), enable lingering for your user once:

   ```bash
   sudo loginctl enable-linger "$USER"
   ```

| To | Run |
| --- | --- |
| See the log | `journalctl --user -u lazyhunt -f` |
| Restart | `systemctl --user restart lazyhunt` |
| Stop | `systemctl --user stop lazyhunt` |
| Remove | `systemctl --user disable --now lazyhunt`, then delete the `.service` file |

## macOS: a launchd agent

A launch agent starts the bot when you log in and restarts it if it stops.

1. Find the full paths of `uv` and `claude` with `command -v uv claude`, and
   your home directory with `echo $HOME`. launchd does not expand `~` or
   `$HOME`, so the file below needs full paths.

2. Create the log folder: `mkdir -p ~/lazyhunt/data`.

3. Create `~/Library/LaunchAgents/com.lazyhunt.bot.plist`, replacing
   `/Users/you` with your home directory:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
     <key>Label</key>
     <string>com.lazyhunt.bot</string>
     <key>ProgramArguments</key>
     <array>
       <string>/Users/you/.local/bin/uv</string>
       <string>run</string>
       <string>bot.py</string>
     </array>
     <key>WorkingDirectory</key>
     <string>/Users/you/lazyhunt</string>
     <key>EnvironmentVariables</key>
     <dict>
       <key>PATH</key>
       <string>/Users/you/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
     </dict>
     <key>RunAtLoad</key>
     <true/>
     <key>KeepAlive</key>
     <true/>
     <key>StandardOutPath</key>
     <string>/Users/you/lazyhunt/data/bot.log</string>
     <key>StandardErrorPath</key>
     <string>/Users/you/lazyhunt/data/bot.log</string>
   </dict>
   </plist>
   ```

4. Load it:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lazyhunt.bot.plist
   ```

| To | Run |
| --- | --- |
| See the log | `tail -f ~/lazyhunt/data/bot.log` |
| Restart | `launchctl kickstart -k gui/$(id -u)/com.lazyhunt.bot` |
| Stop (until next login) | `launchctl bootout gui/$(id -u)/com.lazyhunt.bot` |
| Remove | stop it, then delete the `.plist` file |

## Windows: Task Scheduler

The commands below are for **PowerShell**.

1. Save this as `start_lazyhunt.bat` inside the `lazyhunt` folder. It restarts
   the bot 30 seconds after a crash and appends everything to `data\bot.log`:

   ```bat
   @echo off
   cd /d "%~dp0"
   if not exist data mkdir data
   :loop
   echo [%DATE% %TIME%] starting bot >> data\bot.log
   uv run bot.py >> data\bot.log 2>&1
   echo [%DATE% %TIME%] bot exited with %ERRORLEVEL%, restarting in 30 s >> data\bot.log
   timeout /t 30 /nobreak >nul
   goto loop
   ```

   If `uv` is not on your `PATH` when the task runs, replace `uv` with its full
   path (`(Get-Command uv).Source` prints it).

2. Register a task that runs it when you log in, with no time limit (Task
   Scheduler otherwise stops tasks after three days):

   ```powershell
   $dir = "$HOME\lazyhunt"
   $action = New-ScheduledTaskAction -Execute "$dir\start_lazyhunt.bat" -WorkingDirectory $dir
   $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
   Register-ScheduledTask -TaskName "lazyhunt" -Action $action -Trigger $trigger -Settings $settings
   Start-ScheduledTask -TaskName "lazyhunt"
   ```

| To | Run (PowerShell) |
| --- | --- |
| See the log | `Get-Content "$HOME\lazyhunt\data\bot.log" -Tail 50 -Wait` |
| Restart | `Stop-ScheduledTask -TaskName lazyhunt; Start-ScheduledTask -TaskName lazyhunt` |
| Stop | `Stop-ScheduledTask -TaskName lazyhunt` |
| Remove | `Unregister-ScheduledTask -TaskName lazyhunt` |

If the bot still answers after you stopped the task, end the leftover
`python.exe` (or `uv.exe`) process in Task Manager.

For a quick run without a task, open PowerShell in the `lazyhunt` folder and
run `uv run bot.py`; it stops when you close the window.

## After an update

After `git pull` and `uv sync` (see [Updating](../README.md#updating)),
restart the bot with the matching command above: the running process keeps the
old code until it restarts. Database migrations run on their own when the bot
starts.

## Is it running?

Send `/help` to your bot in Telegram. If nothing comes back, read the log: the
first lines show the AI provider in use, and a warning such as
`owner_telegram_id is 0 (unset)` or `bridge bind failed on 127.0.0.1:8765`
points at the problem. More in [Troubleshooting](../README.md#troubleshooting).
