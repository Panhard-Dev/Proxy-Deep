param([switch]$ReceberContas)
# Iniciador para Windows: usa Python existente ou baixa o pacote oficial isolado.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$scriptName = if ($ReceberContas) { 'receber_contas.py' } else { 'iniciar.py' }
Set-Location -LiteralPath $root
try {
    if (-not (Test-Path -LiteralPath (Join-Path $root 'iniciar.py'))) {
        throw 'Arquivo iniciar.py não encontrado. Extraia toda a pasta ZIP antes de executar.'
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -c 'import sys,ssl,sqlite3; assert sys.version_info >= (3,9)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $launcher.Source -3 (Join-Path $root $scriptName) @args
            exit $LASTEXITCODE
        }
    }
    $installed = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($installed -and $installed.Source -notmatch '[\\/]WindowsApps[\\/]') {
        & $installed.Source -c 'import sys,ssl,sqlite3; assert sys.version_info >= (3,9)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $installed.Source (Join-Path $root $scriptName) @args
            exit $LASTEXITCODE
        }
    }
    if (-not $env:LOCALAPPDATA) { throw 'Não foi possível localizar a pasta de dados local do Windows.' }
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($architecture.ToUpperInvariant()) {
        'AMD64' { $arch='amd64'; $sha='9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38' }
        'ARM64' { $arch='arm64'; $sha='7b58787a047b400edbfcb539aa3cdb4e2ed77bf0f22cae42b7c01cedd7dd6614' }
        'X86'   { $arch='win32'; $sha='4afd37bc88ed1391209a4c58deb902cd0741a476692412b23d340aff6e9285e7' }
        default { throw 'Arquitetura Windows não reconhecida. Instale Python 3.9 ou posterior.' }
    }
    $hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
    try { $rootHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))).Replace('-','').Substring(0,16) }
    finally { $hashAlgorithm.Dispose() }
    $runtimeDir=Join-Path $env:LOCALAPPDATA "ProxyDeep\runtime\$rootHash\python-3.13.17-$arch"
    $python=Join-Path $runtimeDir 'python.exe'
    if (Test-Path -LiteralPath $python) {
        & $python -c 'import ssl,sqlite3' 2>$null
        if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $runtimeDir -Recurse -Force }
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host 'Python não encontrado. Baixando uma versão isolada oficial...'
        [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
        $zip=Join-Path ([System.IO.Path]::GetTempPath()) ('proxydeep-python-'+[Guid]::NewGuid().ToString('N')+'.zip')
        try {
            $url="https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-$arch.zip"
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) { throw 'Falha na verificação do download do Python.' }
            New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $runtimeDir -Force
        } finally { Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue }
    }
    $pth=Join-Path $runtimeDir 'python313._pth'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Não foi possível preparar o Python isolado.' }
    [System.IO.File]::WriteAllText($pth,"python313.zip`r`n.`r`n$root`r`nimport site`r`n",[System.Text.UTF8Encoding]::new($false))
    Write-Host 'Iniciando verificações. Uma página abrirá no navegador...'
    & $python (Join-Path $root $scriptName) @args
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível iniciar o aplicativo.' }
} catch {
    Write-Host ''
    Write-Host ('Não foi possível iniciar: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Confira a conexão com a internet e se todos os arquivos foram extraídos.'
    exit 1
}
