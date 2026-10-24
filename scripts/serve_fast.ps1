# Run the API on several CPU cores (one worker process per core group), without --reload.
#
#   .\scripts\serve_fast.ps1                 # workers = logical cores / 4, at most 8
#   .\scripts\serve_fast.ps1 -Workers 12 -Port 8000
#
# The API keeps no important state in memory, so any worker can answer any request. For day-to-day
# editing keep using `uvicorn app.main:app --reload`, which is one process by design.
param(
    [int]$Workers = [Math]::Min(8, [Math]::Max(2, [int]([Environment]::ProcessorCount / 4))),
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1"
)

Set-Location (Split-Path $PSScriptRoot -Parent)
Write-Host "KalevaShorts API: $Workers workers on $([Environment]::ProcessorCount) logical cores, http://${BindHost}:$Port"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host $BindHost --port $Port --workers $Workers
