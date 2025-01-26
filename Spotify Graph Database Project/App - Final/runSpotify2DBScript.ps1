$containerName = "neo4j-container"
$logFilepath = "Logs/powershellScriptLogfile.txt"
$pythonFilepath = "Spotify2DBScript.py"
$envPath = "SpotifyApp\Scripts\activate.ps1"
$maxRetries = 30
$retryInterval = 20

# function stores a message into a log file. Path is defined above
Function Log {
    param([string]$message)
    Add-Content -Path $logFilepath -Value "$(Get-Date) - $message"
}

# wait for container to be in a running state
for ($i = 0; $i -lt $maxRetries; $i++){
    $status = docker inspect -f '{{.State.Status}}' $containerName

    if($status -eq "running"){
        Log "Container is running. Proceeding with Pulling Data from Spotify..."
        
        Log "opening python environment"
        & $envPath
        
        Log "running Python Script..."
        python .\$pythonFilepath
        Log "Script Run Successfully"

        exit 1
    }

    Log "Waiting for container to start... Attempt $($i+1) of $maxRetries"
    Start-Sleep -Seconds $retryInterval

    if ($i -eq $maxRetries){
        Log "Container failed to start within allocated time"
        exit 1
    }
}