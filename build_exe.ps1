<#
    build_exe.ps1 -- gera o dist\VertexCRM.exe

    Roda de qualquer diretorio:
        powershell -ExecutionPolicy Bypass -File "C:\...\Dashboard\build_exe.ps1"

    O que ele faz:
      1. confere o .venv e instala o PyInstaller (e o Pillow, so se faltar o icone)
      2. apaga build\ e dist\
      3. roda o VertexCRM.spec
      4. informa onde o .exe ficou e quanto ele pesa
#>

$ErrorActionPreference = 'Stop'

$Raiz    = $PSScriptRoot
$Venv    = Join-Path $Raiz '.venv'
$Python  = Join-Path $Venv 'Scripts\python.exe'
$Spec    = Join-Path $Raiz 'VertexCRM.spec'
$Build   = Join-Path $Raiz 'build'
$Dist    = Join-Path $Raiz 'dist'
$Icone   = Join-Path $Raiz 'assets\icone.ico'
$GerIcon = Join-Path $Raiz 'assets\gerar_icone.py'

function Write-Passo([string]$Texto) {
    Write-Host ''
    Write-Host "==> $Texto" -ForegroundColor Cyan
}

Write-Host ''
Write-Host '================= VERTEX CRM :: BUILD DO .EXE =================' -ForegroundColor Magenta
Write-Host "Projeto: $Raiz"

# --- 1. ambiente ------------------------------------------------------------

if (-not (Test-Path $Python)) {
    Write-Host "ERRO: nao achei o Python do venv em $Python" -ForegroundColor Red
    Write-Host 'Rode "python iniciar.py" uma vez para criar o .venv e tente de novo.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Spec)) {
    Write-Host "ERRO: nao achei o $Spec" -ForegroundColor Red
    exit 1
}

Write-Passo 'Conferindo o PyInstaller'
& $Python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PyInstaller nao encontrado -- instalando no .venv ...' -ForegroundColor Yellow
    & $Python -m pip install --disable-pip-version-check --quiet 'pyinstaller>=6.16'
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'ERRO: falha ao instalar o PyInstaller.' -ForegroundColor Red
        exit 1
    }
}
$VersaoPyi = (& $Python -m PyInstaller --version) -join ''
Write-Host "PyInstaller $VersaoPyi"

if (-not (Test-Path $Icone)) {
    Write-Passo 'Icone ausente -- gerando assets\icone.ico'
    & $Python -m pip install --disable-pip-version-check --quiet pillow
    if ($LASTEXITCODE -eq 0) {
        & $Python $GerIcon $Icone
    }
    if (-not (Test-Path $Icone)) {
        Write-Host 'AVISO: sigo sem icone (o .exe fica com o icone padrao).' -ForegroundColor Yellow
    }
}

# --- 2. limpeza -------------------------------------------------------------

Write-Passo 'Limpando build\ e dist\'

# O banco de dados mora em dist\, ao lado do .exe. Apagar a pasta inteira
# destruiria TODOS os dados do usuario a cada rebuild -- entao ele e' posto a
# salvo antes e devolvido depois.
$DadosSalvos = @()
foreach ($Nome in @('vertex.db', 'vertex.db-wal', 'vertex.db-shm', '.env')) {
    $Origem = Join-Path $Dist $Nome
    if (Test-Path $Origem) {
        $Temp = Join-Path ([IO.Path]::GetTempPath()) ("vertex-guarda-" + [Guid]::NewGuid().ToString('N') + "-" + $Nome)
        Move-Item $Origem $Temp -Force
        $DadosSalvos += [PSCustomObject]@{ Nome = $Nome; Temp = $Temp }
        Write-Host "  preservado: $Nome"
    }
}

foreach ($Pasta in @($Build, $Dist)) {
    if (Test-Path $Pasta) {
        Remove-Item -Recurse -Force $Pasta
        Write-Host "  removido: $Pasta"
    }
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
foreach ($Item in $DadosSalvos) {
    Move-Item $Item.Temp (Join-Path $Dist $Item.Nome) -Force
    Write-Host "  devolvido: $($Item.Nome)"
}

# --- 3. build ---------------------------------------------------------------

Write-Passo 'Empacotando (isso leva cerca de um minuto)'
& $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Build $Spec
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'ERRO: o PyInstaller falhou. Leia a saida acima.' -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- 4. resultado -----------------------------------------------------------

$Exe = Join-Path $Dist 'VertexCRM.exe'
if (-not (Test-Path $Exe)) {
    Write-Host "ERRO: o build terminou mas nao achei o $Exe" -ForegroundColor Red
    exit 1
}

$Bytes = (Get-Item $Exe).Length
$Mb    = [math]::Round($Bytes / 1MB, 1)

Write-Host ''
Write-Host '=============================================================' -ForegroundColor Green
Write-Host '  PRONTO' -ForegroundColor Green
Write-Host "  Executavel : $Exe"
Write-Host "  Tamanho    : $Mb MB ($Bytes bytes)"
Write-Host '  Entregue a pasta dist\ inteira ou so o .exe -- ele e unico.'
Write-Host '  Os dados (vertex.db) sao criados ao lado do .exe no 1o uso.'
Write-Host '=============================================================' -ForegroundColor Green
Write-Host ''
