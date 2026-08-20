<#
  TVLens daily warm-start: pre-stages the next queued issue so there is no cold start.
  Runs from Windows Task Scheduler at 0900 local (America/New_York).

  It does NOT draft code and does NOT run Claude. It only:
    1. reads docs/day-plan.md for the next queued issue,
    2. creates an isolated git worktree + branch for it (off master, never pushed),
    3. pulls the issue text into scripts/next-prep.md (a read-only gh view),
    4. pings the desktop.

  Then Patricio opens Claude, says "Day <n>", and drafting happens in-session in the
  ready-made worktree. Nothing here touches master or the running demo.
#>
$ErrorActionPreference = "Continue"  # git/gh write progress to stderr; check exit codes instead
$repo   = "C:\Users\Patricio Quezada\dev\tvlens"
Set-Location $repo
$logDir = Join-Path $repo "scripts\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$stamp  = Get-Date -Format "yyyy-MM-dd_HHmmss"
$log    = Join-Path $logDir "warmstart-$stamp.log"
function Log($m) { "[$(Get-Date -Format 'HH:mm:ss')] $m" | Tee-Object -FilePath $log -Append }

Log "warm-start begin"

# Next queued issue = first queued row in the plan whose prep branch does not exist yet.
$plan = Get-Content (Join-Path $repo "docs\day-plan.md")
$queued = foreach ($line in $plan) {
  if ($line -match '^\|\s*\d+\s*\|\s*#(\d+)\s*\|\s*queued\b') { [int]$Matches[1] }
}
if (-not $queued) { Log "queue empty, nothing to stage"; exit 0 }

# Stage the TOPMOST queued issue. If it is already staged, do nothing, so the job never
# runs ahead of Patricio: the next issue is only staged once he ships this one and marks
# its plan row done (dropping it out of `queued`).
$next = $queued[0]
$wt   = "C:\Users\Patricio Quezada\dev\tvlens-prep-$next"
if (Test-Path $wt) { Log "issue #$next already staged at $wt, nothing to do"; exit 0 }
Log "next issue: #$next"

# Branch is named by WHAT THE WORK IS (prep/<slug from the issue title>). "Day N"
# always means the 80-Day-Project calendar day, never the issue number.
$issueTitle = gh issue view $next --json title --jq .title 2>$null
$slug = ($issueTitle -replace "[^A-Za-z0-9]+", "-").Trim("-").ToLower()
if ($slug.Length -gt 30) { $slug = $slug.Substring(0, 30).Trim("-") }
if (-not $slug) { $slug = "issue-$next" }
git worktree add -b "prep/$slug" $wt master *>> $log
if ($LASTEXITCODE -ne 0) { Log "ERROR: worktree add failed, aborting"; exit 1 }
Log "worktree created: $wt (branch prep/day-$next)"

# Context packet (read-only gh view, no writes to GitHub).
$issue  = gh issue view $next --comments 2>&1 | Out-String
$packet = Join-Path $repo "scripts\next-prep.md"
@"
# Next build: issue #$next

Staged by the 0900 warm-start on $stamp.
Worktree ready: $wt   (branch prep/$slug)

To start: open Claude in the tvlens repo and say **"build today"** (or name the project
day, e.g. "Day 18" -- Claude resolves it to this staged issue).
Nothing is drafted yet. This is only the warm workspace plus the issue context below.

---

$issue
"@ | Set-Content -Path $packet -Encoding UTF8
Log "context packet written: $packet"

# Best-effort desktop ping (never fatal).
try {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $ni = New-Object System.Windows.Forms.NotifyIcon
  $ni.Icon = [System.Drawing.SystemIcons]::Information
  $ni.Visible = $true
  $ni.ShowBalloonTip(10000, "TVLens prep ready", "Issue #$next is staged. Say 'Day $next' in Claude.", [System.Windows.Forms.ToolTipIcon]::Info)
  Start-Sleep -Seconds 1
  $ni.Dispose()
} catch { Log "notification skipped: $_" }

Log "warm-start done"
exit 0
