<#
.SYNOPSIS
    A Windows PowerShell script that packages PIIDigger with Embedded Python as available from python.org
.DESCRIPTION
    This script packages PIIDigger with the Python embedded environment from https://python.org

    NOTE: This script is signed by KirkpatrickPrice using an Authenticode signature.  Use "Get-AuthenticodeSignature <script-name>" to confirm the validity of the signature.

.PARAMETER pyVersion
    Python version to download

.PARAMETER arch
    Windows architecture (win32, amd64) of Python to download.  Reference https://www.python.org/downloads/windows/ for valid values.  Defaults to amd64.

.PARAMETER venv
    Path to the Python Virtual Environment to use.  Relative to the current directory.

    Run the script from the repository root.  CONTRIBUTING.md ("Building a local dev release") has the full steps.

.EXAMPLE
    Build a 64-bit bundle from the .venv-build environment

    .\.github\scripts\build-windows-embedded.ps1 -arch amd64 -pyVersion "3.14.10" -venv ".venv-build"

.EXAMPLE
    Use win32 instead of 64-bit

    .\.github\scripts\build-windows-embedded.ps1 -arch win32 -pyVersion "3.14.10" -venv ".venv-build"

.LINK
    https://github.com/kirkpatrickprice/PIIDigger

.NOTES
    Author: Randy Bartels
    Official location:  https://github.com/kirkpatrickprice/PIIDigger
    Bug reports:        https://github.com/kirkpatrickprice/PIIDigger/issues 
#>

param(
    [Parameter(Mandatory)]
    [string]$arch = "amd64",
    [Parameter(Mandatory)]
    [string]$pyVersion,
    [Parameter(Mandatory)]
    [string]$venv
)

#Python Embedded download URL
$url = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-$arch.zip"

# Root directory (current working directory)
$base_dir=(Get-Location).Path

# Destination directories used for copying files
$build_dir = $base_dir + "\dist\piidigger-"+$arch
$bin_dir = $build_dir + "\bin"

# Source directories to copy files from 
$src_dir=$base_dir+"\src"
$venv_dir=$base_dir+"\"+$venv
$site_packages_dir=$venv_dir+"\lib\site-packages\*"

# Additional wrapper files needed to run PIIDigger using embedded Python
$cmd_path = $base_dir + "\piidigger.cmd"
$py_wrapper_path = $base_dir + "\piidigger.py"

function Write-Status {
    param ([string]$text)

    $FG_COLOR = "red"
    Write-Host $text -ForegroundColor $FG_COLOR
}

try {
    Invoke-WebRequest -Uri $url -OutFile python.zip
}
catch {
    $msg = "Failed to download embedded Python $pyVersion ($arch) from $url : $($_.Exception.Message.TrimEnd('.')).  " +
           "Check that the version and architecture exist at https://www.python.org/downloads/windows/ " +
           "(a newly released patch version may not have its embeddable package posted yet)."
    write-status $msg
    # Surface the failure as an annotation on the GitHub Actions run summary
    if ($env:GITHUB_ACTIONS -eq "true") { Write-Host "::error title=Embedded Python download failed::$msg" }
    exit 1
}

# Create the new working folder and all folders leading up to it
Write-Host "Creating new directory $bin_dir"
New-Item -ItemType Directory -Path $bin_dir -Force

# Expand the embedded Python
Write-Host "Expanding python.zip to $bin_dir"
Expand-Archive -Path python.zip -DestinationPath $bin_dir

# Copy over the necessary PiiDigger elements
Write-Host "Copying Site Packages: $site_packages_dir"
    # __editable__* files point back at the build machine's src\ directory
    Copy-Item -Path $site_packages_dir -Destination $bin_dir -Recurse -Exclude *__pycache__*, __editable__*

# Embedded Python's ._pth disables site, so pywin32.pth is never processed.  Add its paths here
# instead; piidigger.py imports pywin32_bootstrap to register the pywin32_system32 DLLs.
$pth_path = (Get-ChildItem -Path $bin_dir -Filter "python3*._pth").FullName
Write-Host "Adding pywin32 paths to $pth_path"
    Add-Content -Path $pth_path -Value "win32", "win32\lib", "pythonwin" -Encoding ascii

Write-Host "Verifying pywin32 loads under embedded Python"
    & "$bin_dir\python.exe" -c "import sys, pywin32_bootstrap, win32api, pythoncom; win32api.GetFileAttributes(sys.executable)"
    if ($LASTEXITCODE -ne 0) {
        write-status "pywin32 failed to load under embedded Python.  OneDrive/Dropbox placeholder detection would be disabled."
        exit 1
    }

# Records the commit in src\piidigger\_build_info.py so `piidigger --version` names it
Write-Host "Writing build info"
    & "$venv_dir\Scripts\python.exe" "$base_dir\.github\scripts\write_build_info.py"
    if ($LASTEXITCODE -ne 0) {
        write-status "write_build_info.py failed.  The build needs git on PATH and a git checkout."
        exit 1
    }

Write-Host "Copying Source Files: $src_dir"
    Copy-Item -Path $src_dir -Destination $build_dir -Recurse

write-host "Copying wrapper files: $cmd_path $cmd_path"
    Copy-Item -Path $py_wrapper_path, $cmd_path -Destination $build_dir

# SIG # Begin signature block
# MIIlKAYJKoZIhvcNAQcCoIIlGTCCJRUCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCDevCvJIJP5tXnJ
# y40Aa0TV2kMkwyu2Dox4b9givvJT4KCCDQowggZyMIIEWqADAgECAghkM1HTxzif
# CDANBgkqhkiG9w0BAQsFADB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMx
# EDAOBgNVBAcMB0hvdXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8G
# A1UEAwwoU1NMLmNvbSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IFJTQTAe
# Fw0xNjA2MjQyMDQ0MzBaFw0zMTA2MjQyMDQ0MzBaMHgxCzAJBgNVBAYTAlVTMQ4w
# DAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENv
# cnAxNDAyBgNVBAMMK1NTTC5jb20gQ29kZSBTaWduaW5nIEludGVybWVkaWF0ZSBD
# QSBSU0EgUjEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQCfgxNzqrDG
# bSHL24t6h3TQcdyOl3Ka5LuINLTdgAPGL0WkdJq/Hg9Q6p5tePOf+lEmqT2d0bKU
# Vz77OYkbkStW72fL5gvjDjmMxjX0jD3dJekBrBdCfVgWQNz51ShEHZVkMGE6ZPKX
# 13NMfXsjAm3zdetVPW+qLcSvvnSsXf5qtvzqXHnpD0OctVIFD+8+sbGP0EmtpuNC
# GVQ/8y8Ooct8/hP5IznaJRy4PgBKOm8yMDdkHseudQfYVdIYyQ6KvKNc8HwKp4WB
# wg6vj5lc02AlvINaaRwlE81y9eucgJvcLGfE3ckJmNVz68Qho+Uyjj4vUpjGYDdk
# jLJvSlRyGMwnh/rNdaJjIUy1PWT9K6abVa8mTGC0uVz+q0O9rdATZlAfC9KJpv/X
# gAbxwxECMzNhF/dWH44vO2jnFfF3VkopngPawismYTJboFblSSmNNqf1x1KiVgMg
# Lzh4gL32Bq5BNMuURb2bx4kYHwu6/6muakCZE93vUN8BuvIE1tAx3zQ4XldbyDge
# VtSsSKbt//m4wTvtwiS+RGCnd83VPZhZtEPqqmB9zcLlL/Hr9dQg1Zc0bl0EawUR
# 0tOSjAknRO1PNTFGfnQZBWLsiePqI3CY5NEv1IoTGEaTZeVYc9NMPSd6Ij/D+KNV
# t/nmh4LsRR7Fbjp8sU65q2j3m2PVkUG8qQIDAQABo4H7MIH4MA8GA1UdEwEB/wQF
# MAMBAf8wHwYDVR0jBBgwFoAU3QQJB6L1en1SUxKSle44gCUNplkwMAYIKwYBBQUH
# AQEEJDAiMCAGCCsGAQUFBzABhhRodHRwOi8vb2NzcHMuc3NsLmNvbTARBgNVHSAE
# CjAIMAYGBFUdIAAwEwYDVR0lBAwwCgYIKwYBBQUHAwMwOwYDVR0fBDQwMjAwoC6g
# LIYqaHR0cDovL2NybHMuc3NsLmNvbS9zc2wuY29tLXJzYS1Sb290Q0EuY3JsMB0G
# A1UdDgQWBBRUwv4QlQCTzWr158DX2bJLuI8M4zAOBgNVHQ8BAf8EBAMCAYYwDQYJ
# KoZIhvcNAQELBQADggIBAPUPJodwr5miyvXWyfCNZj05gtOII9iCv49UhCe204MH
# 154niU2EjlTRIO5gQ9tXQjzHsJX2vszqoz2OTwbGK1mGf+tzG8rlQCbgPW/M9r1x
# xs19DiBAOdYF0q+UCL9/wlG3K7V7gyHwY9rlnOFpLnUdTsthHvWlM98CnRXZ7WmT
# V7pGRS6AvGW+5xI+3kf/kJwQrfZWsqTU+tb8LryXIbN2g9KR+gZQ0bGAKID+260P
# Z+34fdzZcFt6umi1s0pmF4/n8OdX3Wn+vF7h1YyfE7uVmhX7eSuF1W0+Z0duGwdc
# +1RFDxYRLhHDsLy1bhwzV5Qe/kI0Ro4xUE7bM1eV+jjk5hLbq1guRbfZIsr0WkdJ
# LCjoT4xCPGRo6eZDrBmRqccTgl/8cQo3t51Qezxd96JSgjXktefTCm9r/o35pNfV
# HUvnfWII+NnXrJlJ27WEQRQu9i5gl1NLmv7xiHp0up516eDap8nMLDt7TAp4z5T3
# NmC2gzyKVMtODWgqlBF1JhTqIDfM63kXdlV4cW3iSTgzN9vkbFnHI2LmvM4uVEv9
# XgMqyN0eS3FE0HU+MWJliymm7STheh2ENH+kF3y0rH0/NVjLw78a3Z9UVm1F5VPz
# iIorMaPKPlDRADTsJwjDZ8Zc6Gi/zy4WZbg8Zv87spWrmo2dzJTw7XhQf+xkR6Od
# MIIGkDCCBHigAwIBAgIQMGhaa9kq6JTdsNvho6ILEDANBgkqhkiG9w0BAQsFADB4
# MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24x
# ETAPBgNVBAoMCFNTTCBDb3JwMTQwMgYDVQQDDCtTU0wuY29tIENvZGUgU2lnbmlu
# ZyBJbnRlcm1lZGlhdGUgQ0EgUlNBIFIxMB4XDTI1MTIxNTIwMzgxNVoXDTI3MTIy
# NjIwMzgxNVowdzELMAkGA1UEBhMCVVMxEjAQBgNVBAgMCVRlbm5lc3NlZTESMBAG
# A1UEBwwJTmFzaHZpbGxlMR8wHQYDVQQKDBZLaXJrcGF0cmljayBQcmljZSBJbmMu
# MR8wHQYDVQQDDBZLaXJrcGF0cmljayBQcmljZSBJbmMuMIIBojANBgkqhkiG9w0B
# AQEFAAOCAY8AMIIBigKCAYEAh+DGWDWaY5ki9IwXF8Ddmv0o+kODNKLmtekGG3Vt
# cwSvQsOJlChDJqN+hAGiWTsr0gHVFGo9gOYOdH28HmETqm3hQvS/KZaUuPmy3hj0
# zhKtX5edFcRzwpqxTgEodZi4byBnMWZiiq/qeLICaqX72Skj2JBsNxKAKCesNh2W
# +5Go63SqqGmTtiG12FVo0B8IdhIxV9z8LdozUG5kCsEFWIJROURGNXZNOT27b7mA
# /M/yRyJkFG4e1GP/bwmxVKBPgpNSeDFJTvgiTZr/1zF2DqNqAjfR2PYVHPE/9V7B
# 9imse2ovp3XQKjx+5u5wBLbhKkKguPxUqBuBSNdG72wgEFDgEEcdLvvMYwoCffoa
# t2SQpFhsPlrdGr+NG/36SRrRpZTw3XQc/2OlCTQm1CZPE5NP4dek7Mfwg0h4L26v
# euWeeLBC/I+/fEA4AyL9O//mpksIbyAL1myXbA6TEnVr8bRooMZKjGeMYdVDjTgr
# Cs0ZJYZxYonSdarrAIN95onzAgMBAAGjggGVMIIBkTAMBgNVHRMBAf8EAjAAMB8G
# A1UdIwQYMBaAFFTC/hCVAJPNavXnwNfZsku4jwzjMHoGCCsGAQUFBwEBBG4wbDBI
# BggrBgEFBQcwAoY8aHR0cDovL2NlcnQuc3NsLmNvbS9TU0xjb20tU3ViQ0EtQ29k
# ZVNpZ25pbmctUlNBLTQwOTYtUjEuY2VyMCAGCCsGAQUFBzABhhRodHRwOi8vb2Nz
# cHMuc3NsLmNvbTBRBgNVHSAESjBIMAgGBmeBDAEEATA8BgwrBgEEAYKpMAEDAwEw
# LDAqBggrBgEFBQcCARYeaHR0cHM6Ly93d3cuc3NsLmNvbS9yZXBvc2l0b3J5MBMG
# A1UdJQQMMAoGCCsGAQUFBwMDME0GA1UdHwRGMEQwQqBAoD6GPGh0dHA6Ly9jcmxz
# LnNzbC5jb20vU1NMY29tLVN1YkNBLUNvZGVTaWduaW5nLVJTQS00MDk2LVIxLmNy
# bDAdBgNVHQ4EFgQUegdXMG3gSLgnoEqIXRd18nk7lDswDgYDVR0PAQH/BAQDAgeA
# MA0GCSqGSIb3DQEBCwUAA4ICAQAah5BOjtcTWGp2HNJuksUmt5YGM4oPElaP16n6
# +FTotluUgc4lzpBuDAOGCzNN0XRn5fBLfVKsLpE73dUU+g5QzZptZgcRgqHyv657
# lu1GrJPhbePboAWEBCZVR6tcm5ARALXh9FdBEiC909Weew8O9c12JtyXyhsLVjV7
# 2O5Xu3Oh2MGeHRE+UffwnBF6yBaKQHzY4G2BQS4UVRoC/hGvUGWkNi1p/pc3mIXb
# 2wenSsYm632jT2BPsVVHst6jRhHDJ6wpS4dhNLukpWDp/gi1leNQEcpCAhBOq+sd
# 19SxzaC55Z+zcnQPzNqrnOkPzAjJKA7N3OeDsto6RK3RyJZ+ErE59UO04iwryrWO
# jNDqntMQTJB8kuZ24Ar1X/H4ylpUkV2jybTcp4DrYaq9ECh0XQ29MFi4t0iCBcX2
# eYYFmgHauDJOJFO8EpAFNSOimqeyx+sIszE/gFAmVl+Fpb1TmmauaSMSS/0jSrlu
# FqlkSsRDwpFP8BB+DhynDYvYwVNp9PUu/gG7BVYqDmmyHO7uE4u4waMVZn7pztNF
# 5MovpAYb8pcyFUncG+jS/jSAsVIw0E0FxB/W7ZVSdatlAFfXfxlSTU4XFmcmDXWh
# Bne8dBeJRQw5GTyLUoYOxzg3FOXDhgtfWTD/GGq8uP4nTghptW99llbNHZbTOCgO
# sFrJZjGCF3QwghdwAgEBMIGMMHgxCzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhh
# czEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENvcnAxNDAyBgNVBAMM
# K1NTTC5jb20gQ29kZSBTaWduaW5nIEludGVybWVkaWF0ZSBDQSBSU0EgUjECEDBo
# WmvZKuiU3bDb4aOiCxAwDQYJYIZIAWUDBAIBBQCgfDAQBgorBgEEAYI3AgEMMQIw
# ADAZBgkqhkiG9w0BCQMxDAYKKwYBBAGCNwIBBDAcBgorBgEEAYI3AgELMQ4wDAYK
# KwYBBAGCNwIBFTAvBgkqhkiG9w0BCQQxIgQgBDFwRduPJHI1Lu8PBJH1Zh2E0juB
# gFBn0MsHsYeGviwwDQYJKoZIhvcNAQEBBQAEggGAPtIerKUSgG6yDfFzHRSH+EkK
# 2PzvSzigCRru0hBHR4lQO1QzsdL3WLMReuJ5L6vbNed59xD4uq1QXXd728s8Hu/2
# lTJVYeM+NPkZLvsUTixQf5HfHiV6VLzh5Oe5lHyPNV203O1FOH0EehEBItNQbsdS
# ++dYrrv5twl4f4JRPQjWAS/FtrV+F5NGsmrF+xdRBU+nRK14o9oMDM66JNGJ7S4y
# bGtYsPBlP5BkH41FIzl9UNGsautKKaC1EFVJg2BXlixf42Bfh0Zm13NH/rp+/Kos
# rcHUitIYEqPyJfKD5Wmfc/cfJ2PCekqrSGA8WsgripCVNBU9uXJiu5DDbT9HseG/
# /uf266tXDGUxF6qr3TGeWm6HzggeDp0qYeR0zL9vN2Q7mY0898ekPJGsG3fUaUnW
# cni1GnDaj9qjveb7GaIgUY2+RbFWC35BM8W+Q5JlHy3GUCvenG2yfmZe0TPYZauf
# /2cXxu7PQvOfKa3TzPTLW6fi5LTy6+7dkrLjTz9UoYIUujCCFLYGCisGAQQBgjcD
# AwExghSmMIIUogYJKoZIhvcNAQcCoIIUkzCCFI8CAQMxDTALBglghkgBZQMEAgEw
# dwYLKoZIhvcNAQkQAQSgaARmMGQCAQEGDCsGAQQBgqkwAQMGATAxMA0GCWCGSAFl
# AwQCAQUABCC7sWXOrHC8edLbOl/N1n6klDAUforR1pzpmKpecVL8FwIIa+2r7QZX
# +QUYDzIwMjYxMTAzMjE0NDA3WjADAgEBoIIRpzCCBPMwggLboAMCAQICEFRD+De9
# Wu4YAYRyuFzajuMwDQYJKoZIhvcNAQELBQAwcjELMAkGA1UEBhMCVVMxDjAMBgNV
# BAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMREwDwYDVQQKDAhTU0wgQ29ycDEu
# MCwGA1UEAwwlU1NMLmNvbSBUaW1lc3RhbXBpbmcgSXNzdWluZyBSU0EgQ0EgMjAe
# Fw0yNjA2MTIxNzIyNDZaFw0zNjA2MDkxNzIyNDVaMG4xCzAJBgNVBAYTAlVTMQ4w
# DAYDVQQIDAVUZXhhczEQMA4GA1UEBwwHSG91c3RvbjERMA8GA1UECgwIU1NMIENv
# cnAxKjAoBgNVBAMMIVNTTC5jb20gVGltZXN0YW1waW5nIFVuaXQgMjAyNiBFMTBZ
# MBMGByqGSM49AgEGCCqGSM49AwEHA0IABOgli7hmEy5IkjjzCzbJDgbYV7CIUUgO
# On3jkML0i3A2jYer8VMwShMdSnRQURbOzh65x/ytFQOjGw0CBaVIND2jggFSMIIB
# TjAfBgNVHSMEGDAWgBRwmmXBQ4Cj/OmxZIlu8TCcCSHJfDBNBggrBgEFBQcBAQRB
# MD8wPQYIKwYBBQUHMAKGMWh0dHA6Ly9jZXJ0LnNzbC5jb20vU1NMLmNvbS10aW1l
# U3RhbXBpbmctSS1SMi5jZXIwUQYDVR0gBEowSDA8BgwrBgEEAYKpMAEDBgEwLDAq
# BggrBgEFBQcCARYeaHR0cHM6Ly93d3cuc3NsLmNvbS9yZXBvc2l0b3J5MAgGBmeB
# DAEEAjAWBgNVHSUBAf8EDDAKBggrBgEFBQcDCDBCBgNVHR8EOzA5MDegNaAzhjFo
# dHRwOi8vY3Jscy5zc2wuY29tL1NTTC5jb20tdGltZVN0YW1waW5nLUktUjIuY3Js
# MB0GA1UdDgQWBBQnrANWuihquhc7gz1Af3moJOzDPDAOBgNVHQ8BAf8EBAMCB4Aw
# DQYJKoZIhvcNAQELBQADggIBAE92bs78A57CGU1wZ/oY5rgV4Mwddn22MU/bmHum
# b4b6W2VinMg0IGnBE5D8PJOsnqp2AmMhWdOt5TDxKgIQA9pzButeGYez7R1vQ1qn
# 7IIr8Wz2AXjfre1MYJGE93saW1jHTxKgbVlchzry3CrzMYCVnyYwDcFZMFQ35Mv4
# +3H9vPoopjfDynO0v7vPy3p0UZ1dltmH53EZ5M/Qn7OZ0Q82mYLoh666dhscIJzU
# Eh+BgYxzpdz8y7J1WxqGSouGRI4F7qbOJhhjiu/hgdhwJUJ/jkveOHB6pZB9IBJb
# dlns6AjUaRC6YfamsbgZ40hKl1ln4BOshgcDtLsSjaqVlUx5gMK+krwT6H8bm1yL
# hJ/Yr0eAizrgMVEqyeNWQYP6qhtKCYxehpESm8au3MlkpBGWCYZigoNh4OX1x2oD
# sYsG5p+ST2db4Vgq25N7/3U8doL0yufnqwuR/f603JY1CO++sndq34Y35Kb8gLG7
# V7OA+5658PIvcKK+fn3bUUiQVcasy5spBX7aC9FKdkPvrciglHa4m3XJ6weBykt7
# PrdwI3XGwNOgwi/Bon0IvXy9MkQUTZQOKOgT/kq++eTGgjY1EWGQ7w8sSRwQU+3K
# o2SMLIkfLPuvOtJ6BWwQbXYHtiMOwjwv9UN9SNu4VaJUuwdMmqN7/F9jLVst5rGZ
# V03BMIIF3TCCA8WgAwIBAgIIeyyb0xaAMpkwDQYJKoZIhvcNAQELBQAwfDELMAkG
# A1UEBhMCVVMxDjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMRgwFgYD
# VQQKDA9TU0wgQ29ycG9yYXRpb24xMTAvBgNVBAMMKFNTTC5jb20gUm9vdCBDZXJ0
# aWZpY2F0aW9uIEF1dGhvcml0eSBSU0EwHhcNMTYwMjEyMTczOTM5WhcNNDEwMjEy
# MTczOTM5WjB8MQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcM
# B0hvdXN0b24xGDAWBgNVBAoMD1NTTCBDb3Jwb3JhdGlvbjExMC8GA1UEAwwoU1NM
# LmNvbSBSb290IENlcnRpZmljYXRpb24gQXV0aG9yaXR5IFJTQTCCAiIwDQYJKoZI
# hvcNAQEBBQADggIPADCCAgoCggIBAPkP3aMrfcvQKv7sZ4Wm5y4bunfh4/WvpOz6
# Sl2RxFdHaxh3a3by/ZPkPQ/CFp4LZsNWlJ4Xg4XOVu/yFv0AYvUiCVToZRdOQbng
# T0aXqhvIuG5iXmmxX9sqAn78bMrzQdjt0Oj8P2FI7bADFB0QDksZ4LtO7IZl/zbz
# XmcCC52GVWH9ejjt/uIZALdvoVBidXQ8oPrIJZK0bnoix/geoeOy3ZExqysdBP+l
# SgQ36YWkMyv94tZVNHwZpEpox7Ko07fKoZOI68GXvIz5HdkihCR0xwQ9aqkpk8zr
# uFvh/l8lqjRYyMEjVJ0bmBHDOJx+PYZspQ9AhnwC9FwCTyjLrnGfDzrIM/4RJTXq
# /LrFYD3ZfBjVsqnTdXgDciLKOsMf7yzlLqn6niy2UUb9rwPW6mBo6oUWNmuF6R7A
# s93EJNyAKoFBbZQ+yODJgUEAnl6/f8UImKIYLEJAs/lvOCdLToD0PYFH4Ih86hzO
# tXVcUS4cK38acijnALXRdMbX5J+tB5O2UzU1/Dfkw/ZdFr4hc96SCvigY2q8lpJq
# Pvi8ZVWb3vUNiSYE/CUapiVpy8JtynziWV+XrOvvLsi81xtZPCvM8hnIk2snYxnP
# /Okm+Mpxm3+T/jRnhE6Z6/yzeAkzcLpmpnbtG3PrGqUNxCITIJRWCk4sbE6x/c+c
# CbqiM+2HAgMBAAGjYzBhMB0GA1UdDgQWBBTdBAkHovV6fVJTEpKV7jiAJQ2mWTAP
# BgNVHRMBAf8EBTADAQH/MB8GA1UdIwQYMBaAFN0ECQei9Xp9UlMSkpXuOIAlDaZZ
# MA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsFAAOCAgEAIBgRlCn7Jp0cHh5w
# YfGVcpNxJK1ok1iOMq8bs3AD/CUrdIWQPXhq9LmLpZc7tRiRux6n+UBbkflVma8e
# EdBcHadm47GUBwwyOabqG7B52B2ccETjit3E+ZUfijhDPwGFpUenPUayvOUiaPd7
# nNgsPgohyC0zrL/FgZkxdMF1ccW+sfAjRfSda/wZY52jvATGGAslu1OJD7OAUN5F
# 7kR/q5R4ZJjT9ijdh9hwZXT7DrkT66cPYakylszeu+1jTBi7qUD3oFRuIIhxdRjq
# erQ0cuAjJ3dctpDqhiVAq+8zD8ufgr6iIPv2tS0a5sKFsXQP+8hlAqRSAUfdSSLB
# v9jra6x+3uxjMxW3IwiPxg+NQVrdjsW5j+VFP3jbutIbQLH+cU0/4IGiul607BXg
# k90IH37hVZkLId6Tngr75qNJvTYw/ud3sqB1l7UtgYgXZSD32pAAn8lSzDLKNXz1
# PQ/YK9f1JmzJBjSWFupwWRoyeXkLtoh/D1JIPb9s2KJELtFOt3JY04kTlf5Eq/jX
# ixtunLwsoFvVagCvXzfh1foQC5ichucmj87w7G6KVwuA406ywKBjYZC6VWg3dGq2
# ktufoYYitmUnDuy2n0Jg5GfCtdpBC8TTi2EbvPofkSvXRAdeuims2cXp71NIWuuA
# 8ShYIc2wBlX7Jz9TkHCpBB5XJ7kwggbLMIIEs6ADAgECAhBSnbvCuImH9YOA4LDr
# ogY2MA0GCSqGSIb3DQEBCwUAMHwxCzAJBgNVBAYTAlVTMQ4wDAYDVQQIDAVUZXhh
# czEQMA4GA1UEBwwHSG91c3RvbjEYMBYGA1UECgwPU1NMIENvcnBvcmF0aW9uMTEw
# LwYDVQQDDChTU0wuY29tIFJvb3QgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkgUlNB
# MB4XDTI2MDYxMjE2NDIxNVoXDTQxMDIxMjE3MzkzOVowcjELMAkGA1UEBhMCVVMx
# DjAMBgNVBAgMBVRleGFzMRAwDgYDVQQHDAdIb3VzdG9uMREwDwYDVQQKDAhTU0wg
# Q29ycDEuMCwGA1UEAwwlU1NMLmNvbSBUaW1lc3RhbXBpbmcgSXNzdWluZyBSU0Eg
# Q0EgMjCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAJ8vq+ZzTZC8MUab
# Ngd5WlBjVPcu71oiedNd8n9Il8+uW0HcGvW44/Jf0pHl6hxqn0odhaGnvvNBPJgx
# H/d6CMI7Mtgl9nF5isarRfByRmAeH+skYShA+dMJGzsrC9NiF3/vxGZ2uOI334YV
# c+DlP5OdsUPz5yIP7hYXNlpPknPwh6gK/pWFZD8+y8uUagfX9UShq/SQt+1tS4FX
# RwudHTSHbGt0Fz4ZbWGbm0d56ROAW34tI6FFuZ+TtGssHjeOUf2FS8QNGBpadTh1
# X1Z7Kl6vG4Z8NS3OJIyelzoF5TpcP2vM3ue0723bmZd3oXsE5rztAJMv56jlg0vS
# Dz8XcXRur6awW0rh5cTQDQQGx04hYJH2Zn9HWWJU/wJ2SKBcQKtByCz6JX+DoFoB
# E++kolkpZtl2ESF8FY6XqhtKZb18ESEAc0ohCY+0HkBZwrx8eIEDCDbzd/shv9a8
# YRG2oxr3tLUz95V+Dzzh1QBNECaeGczyNWGCZIVUa4lVcVUVOBbIOAT8D+iPmC5h
# 29bWeES8DhTS4f4CWA4D5QoQqMTqIBcV4MD2Waw8hEWAdGnjnsVtDimnDeEUbvXK
# hdiirvwG+QC+3mAy1RlD6xBMmGyDAKVSqqXm3NP4w0J4gvt65GrkQI4FXPJtYqfw
# kIBtQ8OGfbpVUQGPVUemWtui7E21AgMBAAGjggFRMIIBTTASBgNVHRMBAf8ECDAG
# AQH/AgEAMB8GA1UdIwQYMBaAFN0ECQei9Xp9UlMSkpXuOIAlDaZZME0GCCsGAQUF
# BwEBBEEwPzA9BggrBgEFBQcwAoYxaHR0cDovL2NlcnQuc3NsLmNvbS9TU0xjb20t
# Um9vdENBLVJTQS00MDk2LVIxLmNlcjA/BgNVHSAEODA2MDQGBFUdIAAwLDAqBggr
# BgEFBQcCARYeaHR0cHM6Ly93d3cuc3NsLmNvbS9yZXBvc2l0b3J5MBMGA1UdJQQM
# MAoGCCsGAQUFBwMIMEIGA1UdHwQ7MDkwN6A1oDOGMWh0dHA6Ly9jcmxzLnNzbC5j
# b20vU1NMY29tLVJvb3RDQS1SU0EtNDA5Ni1SMS5jcmwwHQYDVR0OBBYEFHCaZcFD
# gKP86bFkiW7xMJwJIcl8MA4GA1UdDwEB/wQEAwIBhjANBgkqhkiG9w0BAQsFAAOC
# AgEAZRRbzTHpgOogYgWKOeiCkI1oVrkGmCrqOKqX9CiTREQLdYM/h7ANki98QP1f
# hAPEkTzT/Ur19IrPuZg7KaWL6SsmBqmD9+mBVtJmxdRdGbV8bpglFDv2VeZgTSg3
# +UOD2O0G9HlgHqVHNKzSa/zL5+WYl3yT8Q29sZnIPGkQ+udv56/jq5NlAT9BON4i
# XSOCxe1bNvUd0hkS2GTsTNeQyOEHm7CkZdCg5X+DpmBginO7pk8Ym5GbTdQtSloJ
# 3/Y2nVPycJ8kmmBAYMev7/hVQukhjjc4mO03LyPTFfqmMR1D5TpugGkD9nUuG41l
# 55vOpXQm1JMbTgArpkn0/e9O+yTYlWuz2dSAZHf9jxNQAvILO1Pv2JNQTfV0TyIt
# eZP7gNcRfaHnnuSho8ohipKiE+1vEJIFE+c3gopxSpHCmn2THETklWKV7ZrUKhhk
# doOLj2KidCtYZrOoCV6x7f9+KfDPT61fKDVntGZdRKwzLV53VIN6oSS+KQN2VJaO
# 8OZTOZpfgO4o6/9xpNr+EEaA3hNkoZk13R4LaqPC/309dfZmHBE8nBNjWBkV7AZd
# zdK8w7B0a/D9066a6oUa19OIktD+3kPpQ8YEt8oRp9zs1hoQ7fRn9sdantP6nYRQ
# FTpF8czGc+FGresbAH819Pv5vBhCluTo8Px2PpC71cM0jzsxggJVMIICUQIBATCB
# hjByMQswCQYDVQQGEwJVUzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0
# b24xETAPBgNVBAoMCFNTTCBDb3JwMS4wLAYDVQQDDCVTU0wuY29tIFRpbWVzdGFt
# cGluZyBJc3N1aW5nIFJTQSBDQSAyAhBUQ/g3vVruGAGEcrhc2o7jMAsGCWCGSAFl
# AwQCAaCCAWAwGgYJKoZIhvcNAQkDMQ0GCyqGSIb3DQEJEAEEMBwGCSqGSIb3DQEJ
# BTEPFw0yNjExMDMyMTQ0MDdaMCgGCSqGSIb3DQEJNDEbMBkwCwYJYIZIAWUDBAIB
# oQoGCCqGSM49BAMCMC8GCSqGSIb3DQEJBDEiBCDUvThEN6fHyF0hdhkRvsbz8yvf
# Jq8XKJkqnJufHuHXUzCByAYLKoZIhvcNAQkQAi8xgbgwgbUwgbIwga8EIFdP6heq
# +l58aiubCMmYsXNeRQs4c8pE5qJMoJMOY/OwMIGKMHakdDByMQswCQYDVQQGEwJV
# UzEOMAwGA1UECAwFVGV4YXMxEDAOBgNVBAcMB0hvdXN0b24xETAPBgNVBAoMCFNT
# TCBDb3JwMS4wLAYDVQQDDCVTU0wuY29tIFRpbWVzdGFtcGluZyBJc3N1aW5nIFJT
# QSBDQSAyAhBUQ/g3vVruGAGEcrhc2o7jMAoGCCqGSM49BAMCBEYwRAIgBGmQT7S/
# HJJ8jyCVSvT8Mv18F5CAT2DJk0XlhPui9a4CICtC7p7l6nsmvPWmcyNiokf9/ekD
# XA7CE+txE8ft2e8u
# SIG # End signature block
